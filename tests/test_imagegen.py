from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from openai import PermissionDeniedError
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "sub2api-imagegen" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import imagegen_support
from imagegen_batch import MAX_BATCH_JOBS, _attempt_job, _read_jobs
from imagegen_cli import main, parse_args
from imagegen_io import (
    OutputPlan,
    decode_response,
    plan_outputs,
    response_bytes,
    save_images,
)
from imagegen_runner import (
    MAX_INPUT_BYTES,
    _input_path,
    prepare_job,
    request_live,
    run_live,
)
from imagegen_support import (
    clean_sdk_headers,
    make_client,
    request_kwargs,
    resolve_base_url,
    structured_prompt,
    validate_options,
    validate_size,
)

DEFAULTS = {
    "model": "gpt-image-2",
    "size": "auto",
    "n": 1,
    "quality": "medium",
    "output_format": "png",
    "background": None,
    "output_compression": None,
    "moderation": None,
    "input_fidelity": None,
    "downscale_max_dim": None,
    "downscale_suffix": "-web",
    "out": None,
    "out_dir": None,
    "force": False,
    "augment": True,
}


class SupportTests(unittest.TestCase):
    def test_header_hook_only_changes_compatibility_headers(self) -> None:
        request = httpx.Request(
            "POST",
            "https://example.invalid/images/generations",
            headers={
                "Authorization": "Bearer placeholder",
                "Content-Type": "application/json",
                "X-Stainless-OS": "Windows",
                "x-stainless-lang": "python",
                "User-Agent": "OpenAI/Python",
            },
        )
        clean_sdk_headers(request)
        self.assertEqual(request.headers["Authorization"], "Bearer placeholder")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(request.headers["User-Agent"], "python-requests/2.32.5")
        self.assertFalse(any(name.lower().startswith("x-stainless-") for name in request.headers))

    def test_defaults_are_sent_to_images_api(self) -> None:
        payload = request_kwargs(DEFAULTS, "Primary request: test")
        self.assertEqual(payload["model"], "gpt-image-2")
        self.assertEqual(payload["size"], "auto")
        self.assertEqual(payload["quality"], "medium")
        self.assertEqual(payload["output_format"], "png")

    def test_client_uses_sdk_retry_count_for_total_attempts(self) -> None:
        transport = object()
        client = object()
        with (
            patch.object(imagegen_support, "resolve_api_key", return_value="placeholder"),
            patch.object(imagegen_support, "DefaultHttpxClient", return_value=transport),
            patch.object(imagegen_support, "OpenAI", return_value=client) as openai,
        ):
            self.assertIs(make_client("https://example.invalid/v1", 3), client)
        openai.assert_called_once_with(
            api_key="placeholder",
            base_url="https://example.invalid/v1",
            http_client=transport,
            max_retries=2,
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            make_client("https://example.invalid/v1", 0)

    def test_sdk_retries_503_and_keeps_clean_headers(self) -> None:
        attempts = 0
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        observed_headers: list[httpx.Headers] = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            observed_headers.append(request.headers)
            if attempts < 3:
                return httpx.Response(
                    503,
                    headers={"Retry-After": "0"},
                    json={"error": {"message": "busy", "type": "server_error"}},
                )
            return httpx.Response(200, json={"created": 0, "data": [{"b64_json": encoded}]})

        transport = httpx.MockTransport(handler)

        def build_http_client(**kwargs: object) -> httpx.Client:
            return httpx.Client(transport=transport, **kwargs)

        with (
            patch.object(imagegen_support, "resolve_api_key", return_value="placeholder"),
            patch.object(
                imagegen_support,
                "DefaultHttpxClient",
                side_effect=build_http_client,
            ),
        ):
            client = make_client("https://example.invalid/v1", 3)
            try:
                response = client.images.generate(
                    model="gpt-image-2",
                    prompt="robot",
                    n=1,
                    size="1024x1024",
                )
            finally:
                client.close()

        self.assertEqual(attempts, 3)
        self.assertEqual(response.data[0].b64_json, encoded)
        for headers in observed_headers:
            self.assertEqual(headers["User-Agent"], "python-requests/2.32.5")
            self.assertFalse(
                any(name.lower().startswith("x-stainless-") for name in headers)
            )
            self.assertIn("Authorization", headers)

    def test_sdk_does_not_retry_403(self) -> None:
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                403,
                request=request,
                json={"error": {"message": "forbidden", "type": "permission_error"}},
            )

        transport = httpx.MockTransport(handler)

        def build_http_client(**kwargs: object) -> httpx.Client:
            return httpx.Client(transport=transport, **kwargs)

        with (
            patch.object(imagegen_support, "resolve_api_key", return_value="placeholder"),
            patch.object(
                imagegen_support,
                "DefaultHttpxClient",
                side_effect=build_http_client,
            ),
        ):
            client = make_client("https://example.invalid/v1", 3)
            try:
                with self.assertRaises(PermissionDeniedError):
                    client.images.generate(
                        model="gpt-image-2",
                        prompt="robot",
                        n=1,
                        size="1024x1024",
                    )
            finally:
                client.close()

        self.assertEqual(attempts, 1)

    def test_prompt_augmentation_can_be_disabled(self) -> None:
        values = {**DEFAULTS, "style": "watercolor", "negative": "letters"}
        self.assertIn("Style/medium: watercolor", structured_prompt("bird", values))
        self.assertEqual(structured_prompt("bird", {**values, "augment": False}), "bird")

    def test_size_and_transparency_validation(self) -> None:
        validate_size("gpt-image-2", "3840x2160")
        with self.assertRaises(ValueError):
            validate_size("gpt-image-2", "1000x1000")
        with self.assertRaises(ValueError):
            validate_options({**DEFAULTS, "background": "transparent"}, "generate")
        validate_options(
            {**DEFAULTS, "model": "gpt-image-1.5", "background": "transparent"},
            "generate",
        )

    def test_output_planning_matches_one_off_defaults(self) -> None:
        plan = plan_outputs(DEFAULTS, "test")
        self.assertEqual(plan.originals, [Path("output/imagegen/output.png")])
        multi = plan_outputs({**DEFAULTS, "n": 2, "out": "hero.webp"}, "test")
        self.assertEqual(multi.originals, [Path("hero-1.webp"), Path("hero-2.webp")])

    def test_base64_response_compatibility(self) -> None:
        encoded = base64.b64encode(b"image-bytes").decode("ascii")
        self.assertEqual(decode_response({"data": [{"b64_json": encoded}]}, 1), [b"image-bytes"])

    def test_url_response_compatibility(self) -> None:
        with patch("imagegen_io._download", return_value=b"downloaded-image"):
            self.assertEqual(response_bytes({"url": "https://example.invalid/image.png"}), b"downloaded-image")

    def test_base_url_local_config_precedes_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.local.json"
            config.write_text('{"base_url":"https://file.invalid/v1"}', encoding="utf-8")
            with (
                patch.object(imagegen_support, "LOCAL_CONFIG_PATH", config),
                patch.object(imagegen_support, "resolve_ccswitch_base_url", return_value=None),
                patch.dict(
                    os.environ,
                    {"OPENAI_BASE_URL": "https://environment.invalid/v1"},
                    clear=True,
                ),
            ):
                self.assertEqual(resolve_base_url(), "https://file.invalid/v1")
            with (
                patch.object(imagegen_support, "LOCAL_CONFIG_PATH", config),
                patch.object(imagegen_support, "resolve_ccswitch_base_url", return_value=None),
                patch.dict(os.environ, {}, clear=True),
            ):
                self.assertEqual(resolve_base_url(), "https://file.invalid/v1")

    def test_downscale_writes_original_and_bounded_copy(self) -> None:
        source = io.BytesIO()
        Image.new("RGB", (12, 6), "red").save(source, format="PNG")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = OutputPlan([root / "full.png"], [root / "full-web.png"])
            save_images(
                [source.getvalue()],
                plan,
                force=False,
                downscale_max_dim=4,
                output_format="png",
            )
            with Image.open(plan.downscaled[0]) as resized:
                self.assertEqual(resized.size, (4, 2))


class BatchTests(unittest.TestCase):
    def test_plain_lines_comments_and_nested_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "jobs.jsonl"
            source.write_text(
                "# comment\n\nplain prompt\n"
                '{"prompt":"nested","fields":{"style":"ink","future_field":"ignored"},'
                '"scene":"studio","future_option":true}\n',
                encoding="utf-8",
            )
            jobs = _read_jobs(source)
            self.assertEqual(jobs[0], {"prompt": "plain prompt"})
            self.assertEqual(jobs[1]["fields"]["style"], "ink")
            self.assertEqual(jobs[1]["fields"]["future_field"], "ignored")
            self.assertEqual(jobs[1]["scene"], "studio")
            self.assertTrue(jobs[1]["future_option"])

    def test_edit_inputs_must_be_smaller_than_50mb(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            below_limit = root / "below.png"
            with below_limit.open("wb") as stream:
                stream.truncate(MAX_INPUT_BYTES - 1)
            self.assertEqual(_input_path(str(below_limit)), below_limit)

            at_limit = root / "at-limit.png"
            with at_limit.open("wb") as stream:
                stream.truncate(MAX_INPUT_BYTES)
            with self.assertRaisesRegex(ValueError, "must be smaller than 50MB"):
                _input_path(str(at_limit))

            source = root / "source.png"
            source.write_bytes(b"source")
            mask = root / "mask.png"
            with mask.open("wb") as stream:
                stream.truncate(MAX_INPUT_BYTES)
            with self.assertRaisesRegex(ValueError, "mask must be smaller than 50MB"):
                prepare_job(
                    "edit",
                    {
                        **DEFAULTS,
                        "prompt": "change background",
                        "prompt_file": None,
                        "image": [str(source)],
                        "mask": str(mask),
                    },
                )

            mask = root / "mask.jpg"
            mask.write_bytes(b"mask")
            warnings = io.StringIO()
            with redirect_stderr(warnings):
                prepare_job(
                    "edit",
                    {
                        **DEFAULTS,
                        "prompt": "change background",
                        "prompt_file": None,
                        "image": [str(source)],
                        "mask": str(mask),
                    },
                )
            self.assertIn("mask should be a PNG with an alpha channel", warnings.getvalue())

    def test_batch_job_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "too-many.txt"
            source.write_text("\n".join(f"prompt {i}" for i in range(MAX_BATCH_JOBS + 1)))
            with self.assertRaisesRegex(ValueError, "maximum is 500"):
                _read_jobs(source)

    def test_attempt_boundaries_apply_to_every_command(self) -> None:
        error_output = io.StringIO()
        with redirect_stderr(error_output), self.assertRaises(SystemExit):
            parse_args(
                [
                    "generate-batch",
                    "--input",
                    "jobs.txt",
                    "--out-dir",
                    "out",
                    "--concurrency",
                    "26",
                    "--dry-run",
                ]
            )
        with redirect_stderr(error_output), self.assertRaises(SystemExit):
            parse_args(
                [
                    "generate-batch",
                    "--input",
                    "jobs.txt",
                    "--out-dir",
                    "out",
                    "--max-attempts",
                    "11",
                    "--dry-run",
                ]
            )
        with redirect_stderr(error_output), self.assertRaises(SystemExit):
            parse_args(
                [
                    "generate",
                    "--prompt",
                    "robot",
                    "--max-attempts",
                    "0",
                    "--dry-run",
                ]
            )
        self.assertEqual(
            parse_args(["edit", "--image", "source.png", "--prompt", "change"]).max_attempts,
            3,
        )

    def test_batch_delegates_retry_to_one_sdk_request(self) -> None:
        response = object()
        job = SimpleNamespace(index=1)
        with (
            patch("imagegen_batch.request_live", return_value=response) as request,
            patch("imagegen_batch.finish_response", return_value=[Path("done.png")]),
        ):
            result = _attempt_job(
                job,
                "https://example.invalid/v1",
                3,
                SimpleNamespace(is_set=lambda: False),
            )
        self.assertEqual(result, [Path("done.png")])
        request.assert_called_once_with(job, "https://example.invalid/v1", 3)

        with (
            patch("imagegen_batch.request_live", return_value=response) as request,
            patch("imagegen_batch.finish_response", side_effect=ValueError("bad base64")),
            self.assertRaisesRegex(ValueError, "bad base64"),
        ):
            _attempt_job(
                job,
                "https://example.invalid/v1",
                3,
                SimpleNamespace(is_set=lambda: False),
            )
        request.assert_called_once()

        with (
            patch("imagegen_batch.request_live", side_effect=RuntimeError("503")) as request,
            self.assertRaisesRegex(RuntimeError, "503"),
        ):
            _attempt_job(
                job,
                "https://example.invalid/v1",
                3,
                SimpleNamespace(is_set=lambda: False),
            )
        request.assert_called_once()

    def test_generate_and_edit_pass_attempts_to_client_once(self) -> None:
        response = object()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            source.write_bytes(b"source")
            jobs = [
                SimpleNamespace(
                    command="generate",
                    values=DEFAULTS,
                    prompt="robot",
                    image_paths=[],
                    mask_path=None,
                ),
                SimpleNamespace(
                    command="edit",
                    values=DEFAULTS,
                    prompt="change sky",
                    image_paths=[source],
                    mask_path=None,
                ),
            ]
            for job in jobs:
                with self.subTest(command=job.command):
                    client = SimpleNamespace(
                        images=SimpleNamespace(
                            generate=Mock(return_value=response),
                            edit=Mock(return_value=response),
                        ),
                        close=Mock(),
                    )
                    with patch("imagegen_runner.make_client", return_value=client) as make:
                        self.assertIs(
                            request_live(job, "https://example.invalid/v1", 4),
                            response,
                        )
                    make.assert_called_once_with("https://example.invalid/v1", 4)
                    getattr(client.images, job.command).assert_called_once()
                    client.close.assert_called_once()

    def test_single_response_processing_failure_is_not_retried(self) -> None:
        job = SimpleNamespace(command="generate")
        response = object()
        with (
            patch("imagegen_runner.request_live", return_value=response) as request,
            patch("imagegen_runner.finish_response", side_effect=ValueError("bad base64")),
            self.assertRaisesRegex(ValueError, "bad base64"),
        ):
            run_live(job, "https://example.invalid/v1", max_attempts=3)
        request.assert_called_once_with(job, "https://example.invalid/v1", 3)


class DryRunTests(unittest.TestCase):
    def run_cli(self, arguments: list[str], cwd: Path) -> tuple[int, str]:
        output = io.StringIO()
        old_cwd = Path.cwd()
        try:
            os.chdir(cwd)
            with patch.dict(
                os.environ,
                {"OPENAI_BASE_URL": "https://example.invalid/v1"},
                clear=True,
            ), patch.object(
                imagegen_support, "resolve_ccswitch_base_url", return_value=None
            ), redirect_stdout(output):
                code = main(arguments)
        finally:
            os.chdir(old_cwd)
        return code, output.getvalue()

    def test_generate_edit_and_batch_dry_runs_without_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "source.png"
            source.write_bytes(b"dry-run fixture")
            jobs = temp / "jobs.jsonl"
            jobs.write_text(
                "# ignored\n\nfirst plain prompt\n"
                '{"prompt":"second","fields":{"style":"ink","future":"ignored"},'
                '"n":2,"output_format":"webp","future_option":true}\n',
                encoding="utf-8",
            )
            generate = self.run_cli(["generate", "--prompt", "robot", "--dry-run"], temp)
            edit = self.run_cli(
                ["edit", "--image", str(source), "--prompt", "change sky", "--dry-run"],
                temp,
            )
            batch = self.run_cli(
                [
                    "generate-batch",
                    "--input",
                    str(jobs),
                    "--out-dir",
                    str(temp / "batch"),
                    "--dry-run",
                ],
                temp,
            )
            self.assertEqual([generate[0], edit[0], batch[0]], [0, 0, 0])
            self.assertIn('"size": "auto"', generate[1])
            self.assertIn('"endpoint": "/v1/images/edits"', edit[1])
            self.assertIn('"job": 2', batch[1])
            self.assertIn("Style/medium: ink", batch[1])


if __name__ == "__main__":
    unittest.main()
