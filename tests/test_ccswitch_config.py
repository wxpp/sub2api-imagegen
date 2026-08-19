from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "sub2api-imagegen" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import ccswitch_config
import imagegen_support
from ccswitch_config import resolve_ccswitch_api_key, resolve_ccswitch_base_url
from imagegen_cli import main
from imagegen_support import resolve_api_key, resolve_base_url


def provider_settings(name: str, base_url: str, api_key: str) -> str:
    config = (
        f'model_provider = "{name}"\n'
        f'[model_providers.{name}]\n'
        f'base_url = "{base_url}"\n'
    )
    return json.dumps({"auth": {"OPENAI_API_KEY": api_key}, "config": config})


class CCSwitchFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.database = root / "cc-switch.db"
        self.settings = root / "settings.json"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "CREATE TABLE providers (id TEXT, app_type TEXT, is_current INTEGER, "
                "settings_config TEXT)"
            )
            connection.commit()

    def add(
        self,
        provider_id: str,
        *,
        current: int = 1,
        app_type: str = "codex",
        settings_config: str | None = None,
    ) -> None:
        settings_config = settings_config or provider_settings(
            provider_id, f"https://{provider_id}.invalid/v1", f"key-{provider_id}"
        )
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                "INSERT INTO providers VALUES (?, ?, ?, ?)",
                (provider_id, app_type, current, settings_config),
            )
            connection.commit()

    def select(self, provider_id: str) -> None:
        self.settings.write_text(
            json.dumps({"currentProviderCodex": provider_id}), encoding="utf-8"
        )


class CCSwitchTests(unittest.TestCase):
    def test_missing_or_structurally_invalid_database_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIsNone(resolve_ccswitch_base_url(root=root))
            with closing(sqlite3.connect(root / "cc-switch.db")) as connection:
                connection.execute("CREATE TABLE unrelated (value TEXT)")
                connection.commit()
            self.assertIsNone(resolve_ccswitch_api_key(root=root))

    def test_database_connection_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            with (
                closing(ccswitch_config._connect(fixture.database)) as connection,
                self.assertRaises(sqlite3.OperationalError),
            ):
                connection.execute(
                    "INSERT INTO providers VALUES ('x', 'codex', 1, '{}')"
                )

    def test_settings_selects_current_codex_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            fixture.add("chosen")
            fixture.add("other", current=0)
            fixture.select("chosen")
            self.assertEqual(
                resolve_ccswitch_base_url(root=fixture.root),
                "https://chosen.invalid/v1",
            )
            self.assertEqual(resolve_ccswitch_api_key(root=fixture.root), "key-chosen")

    def test_inconsistent_settings_uses_only_unique_current_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            fixture.add("unique")
            fixture.add("stale", current=0)
            fixture.select("stale")
            self.assertEqual(
                resolve_ccswitch_base_url(root=fixture.root),
                "https://unique.invalid/v1",
            )

    def test_missing_or_bad_settings_uses_unique_current_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            fixture.add("unique")
            self.assertEqual(
                resolve_ccswitch_base_url(root=fixture.root),
                "https://unique.invalid/v1",
            )
            fixture.settings.write_text("{broken", encoding="utf-8")
            self.assertEqual(resolve_ccswitch_api_key(root=fixture.root), "key-unique")

    def test_multiple_current_providers_are_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            fixture.add("one")
            fixture.add("two")
            fixture.select("missing")
            self.assertIsNone(resolve_ccswitch_base_url(root=fixture.root))
            self.assertIsNone(resolve_ccswitch_api_key(root=fixture.root))

    def test_bad_settings_json_and_toml_are_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            fixture.add("bad-json", settings_config="{not-json")
            fixture.select("bad-json")
            self.assertIsNone(resolve_ccswitch_base_url(root=fixture.root))
            self.assertIsNone(resolve_ccswitch_api_key(root=fixture.root))

        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            settings = json.dumps(
                {"auth": {"OPENAI_API_KEY": "secret"}, "config": "not = [valid"}
            )
            fixture.add("bad-toml", settings_config=settings)
            fixture.select("bad-toml")
            self.assertIsNone(resolve_ccswitch_base_url(root=fixture.root))

    def test_base_url_priority_is_ccswitch_local_then_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "config.local.json"
            local.write_text('{"base_url":"https://local.invalid/v1"}', encoding="utf-8")
            with (
                patch.object(imagegen_support, "LOCAL_CONFIG_PATH", local),
                patch.object(
                    imagegen_support,
                    "resolve_ccswitch_base_url",
                    return_value="https://cc.invalid/v1",
                ),
                patch.dict(
                    os.environ,
                    {"OPENAI_BASE_URL": "https://env.invalid/v1"},
                    clear=True,
                ),
            ):
                self.assertEqual(resolve_base_url(), "https://cc.invalid/v1")
            with patch.object(
                imagegen_support, "resolve_ccswitch_base_url", return_value=None
            ), patch.object(imagegen_support, "LOCAL_CONFIG_PATH", local), patch.dict(
                os.environ,
                {"OPENAI_BASE_URL": "https://env.invalid/v1"},
                clear=True,
            ):
                self.assertEqual(resolve_base_url(), "https://local.invalid/v1")
            local.unlink()
            with patch.object(
                imagegen_support, "resolve_ccswitch_base_url", return_value=None
            ), patch.object(imagegen_support, "LOCAL_CONFIG_PATH", local), patch.dict(
                os.environ,
                {"OPENAI_BASE_URL": "https://env.invalid/v1"},
                clear=True,
            ):
                self.assertEqual(resolve_base_url(), "https://env.invalid/v1")

    def test_api_key_priority_is_ccswitch_then_environment(self) -> None:
        with patch.object(
            imagegen_support, "resolve_ccswitch_api_key", return_value="cc-secret"
        ), patch.dict(os.environ, {"OPENAI_API_KEY": "env-secret"}, clear=True):
            self.assertEqual(resolve_api_key(), "cc-secret")
        with patch.object(
            imagegen_support, "resolve_ccswitch_api_key", return_value=None
        ), patch.dict(os.environ, {"OPENAI_API_KEY": "env-secret"}, clear=True):
            self.assertEqual(resolve_api_key(), "env-secret")

    def test_dry_run_never_queries_ccswitch_api_key(self) -> None:
        output = io.StringIO()
        with (
            patch.object(
                imagegen_support,
                "resolve_ccswitch_base_url",
                return_value="https://cc.invalid/v1",
            ),
            patch.object(
                imagegen_support,
                "resolve_ccswitch_api_key",
                side_effect=AssertionError("key query must not run"),
            ),
            redirect_stderr(output),
        ):
            self.assertEqual(main(["generate", "--prompt", "robot", "--dry-run"]), 0)
        self.assertEqual(output.getvalue(), "")

    def test_ccswitch_failure_output_does_not_leak_values(self) -> None:
        secret = "do-not-leak-key"
        private_url = "https://do-not-leak.invalid/v1"
        with tempfile.TemporaryDirectory() as directory:
            fixture = CCSwitchFixture(Path(directory))
            broken = json.dumps(
                {
                    "auth": {"OPENAI_API_KEY": secret},
                    "config": f'model_provider = "x"\nbase_url = "{private_url}"\n',
                }
            )
            fixture.add("broken", settings_config=broken)
            fixture.select("broken")
            error_output = io.StringIO()
            missing_local = fixture.root / "missing.json"
            with (
                patch.object(ccswitch_config, "CCSWITCH_ROOT", fixture.root),
                patch.object(imagegen_support, "LOCAL_CONFIG_PATH", missing_local),
                patch.dict(os.environ, {}, clear=True),
                redirect_stderr(error_output),
            ):
                self.assertEqual(main(["generate", "--prompt", "robot", "--dry-run"]), 1)
            rendered = error_output.getvalue()
            self.assertNotIn(secret, rendered)
            self.assertNotIn(private_url, rendered)


if __name__ == "__main__":
    unittest.main()
