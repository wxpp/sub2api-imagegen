from __future__ import annotations

import base64
import io
import os
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "sub2api-imagegen" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import imagegen_support
from imagegen_batch import (
    MAX_BATCH_JOBS,
    _attempt_job,
    _read_jobs,
    is_retryable_error,
    retry_after_seconds,
)
from imagegen_cli import main, parse_args
from imagegen_io import (
    OutputPlan,
    decode_response,
    plan_outputs,
    response_bytes,
    save_images,
)
from imagegen_runner import MAX_INPUT_BYTES, _input_path, prepare_job
from imagegen_support import (
    clean_sdk_headers,
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

    def test_base_url_environment_precedes_local_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.local.json"
            config.write_text('{"base_url":"https://file.invalid/v1"}', encoding="utf-8")
            with (
                patch.object(imagegen_support, "LOCAL_CONFIG_PATH", config),
                patch.dict(
                    os.environ,
                    {"OPENAI_BASE_URL": "https://environment.invalid/v1"},
                    clear=True,
                ),
            ):
                self.assertEqual(resolve_base_url(), "https://environment.invalid/v1")
            with (
                patch.object(imagegen_support, "LOCAL_CONFIG_PATH", config),
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

    def test_large_input_and_non_png_mask_warn_without_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            large = root / "large.png"
            with large.open("wb") as stream:
                stream.truncate(MAX_INPUT_BYTES)
            warnings = io.StringIO()
            with redirect_stderr(warnings):
                self.assertEqual(_input_path(str(large)), large)
            self.assertIn("reaches or exceeds 50MB", warnings.getvalue())

            source = root / "source.png"
            source.write_bytes(b"source")
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

    def test_concurrency_and_attempt_boundaries_apply_to_dry_run(self) -> None:
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

    def test_retry_after_header_and_retry_classification(self) -> None:
        error = RuntimeError("service unavailable")
        error.response = httpx.Response(503, headers={"Retry-After": "0.25"})
        self.assertEqual(retry_after_seconds(error), 0.25)
        self.assertTrue(is_retryable_error(error))
        self.assertFalse(is_retryable_error(ValueError("invalid response data")))
        self.assertFalse(is_retryable_error(ValueError("validation timeout value is invalid")))

    def test_only_request_failures_are_retried(self) -> None:
        class RateLimitError(Exception):
            retry_after = 0.1

        response = object()
        job = SimpleNamespace(index=1)
        with (
            patch("imagegen_batch.request_live", side_effect=[RateLimitError(), response]) as request,
            patch("imagegen_batch.finish_response", return_value=[Path("done.png")]),
            patch("imagegen_batch.time.sleep") as sleep,
        ):
            result = _attempt_job(job, "https://example.invalid/v1", 3, threading.Event())
        self.assertEqual(result, [Path("done.png")])
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.1)

        with (
            patch("imagegen_batch.request_live", return_value=response) as request,
            patch("imagegen_batch.finish_response", side_effect=ValueError("bad base64")),
            patch("imagegen_batch.time.sleep") as sleep,
            self.assertRaisesRegex(ValueError, "bad base64"),
        ):
            _attempt_job(job, "https://example.invalid/v1", 3, threading.Event())
        request.assert_called_once()
        sleep.assert_not_called()

        with (
            patch("imagegen_batch.request_live", side_effect=ValueError("bad request")) as request,
            patch("imagegen_batch.time.sleep") as sleep,
            self.assertRaisesRegex(ValueError, "bad request"),
        ):
            _attempt_job(job, "https://example.invalid/v1", 3, threading.Event())
        request.assert_called_once()
        sleep.assert_not_called()


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
