"""Read the current Codex provider from CC Switch without exposing credentials."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, supplied by the script's inline dependencies.
    import tomli as tomllib

CCSWITCH_ROOT = Path.home() / ".cc-switch"


def _current_provider_id(settings_path: Path) -> str | int | None:
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(settings, dict):
        return None
    provider_id = settings.get("currentProviderCodex")
    if isinstance(provider_id, bool) or not isinstance(provider_id, (str, int)):
        return None
    if isinstance(provider_id, str) and not provider_id.strip():
        return None
    return provider_id


def _database_uri(database_path: Path) -> str:
    return f"{database_path.resolve().as_uri()}?mode=ro"


def _connect(database_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(_database_uri(database_path), uri=True)


def _selected_provider_id(database_path: Path, settings_path: Path) -> Any | None:
    preferred = _current_provider_id(settings_path)
    with closing(_connect(database_path)) as connection:
        if preferred is not None:
            rows = connection.execute(
                "SELECT id, app_type, is_current FROM providers WHERE id = ?",
                (preferred,),
            ).fetchall()
            if len(rows) == 1 and rows[0][1] == "codex" and rows[0][2] == 1:
                return rows[0][0]
        rows = connection.execute(
            "SELECT id FROM providers WHERE app_type = 'codex' AND is_current = 1 LIMIT 2"
        ).fetchall()
    return rows[0][0] if len(rows) == 1 else None


def _provider_setting(
    field: Literal["config", "api_key"],
    *,
    root: Path | None = None,
) -> str | None:
    cc_root = root or CCSWITCH_ROOT
    database_path = cc_root / "cc-switch.db"
    settings_path = cc_root / "settings.json"
    try:
        provider_id = _selected_provider_id(database_path, settings_path)
        if provider_id is None:
            return None
        json_path = "$.config" if field == "config" else "$.auth.OPENAI_API_KEY"
        with closing(_connect(database_path)) as connection:
            row = connection.execute(
                "SELECT json_extract(settings_config, ?) FROM providers WHERE id = ? "
                "AND app_type = 'codex' AND is_current = 1",
                (json_path, provider_id),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if row is None or not isinstance(row[0], str) or not row[0].strip():
        return None
    return row[0].strip()


def resolve_ccswitch_base_url(*, root: Path | None = None) -> str | None:
    """Return the current Codex provider Base URL, or None when unavailable."""
    config_text = _provider_setting("config", root=root)
    if config_text is None:
        return None
    try:
        config = tomllib.loads(config_text)
    except (tomllib.TOMLDecodeError, TypeError):
        return None
    provider_name = config.get("model_provider")
    providers = config.get("model_providers")
    if not isinstance(provider_name, str) or not isinstance(providers, dict):
        return None
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        return None
    base_url = provider.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return None
    return base_url.strip()


def resolve_ccswitch_api_key(*, root: Path | None = None) -> str | None:
    """Return the current Codex provider API key, or None when unavailable."""
    return _provider_setting("api_key", root=root)
