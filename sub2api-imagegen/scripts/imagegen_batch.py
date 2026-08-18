"""JSONL batch loading and concurrent execution."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from imagegen_io import validate_output_plans
from imagegen_runner import (
    PreparedJob,
    finish_response,
    prepare_job,
    print_dry_run,
    request_live,
)

MAX_BATCH_JOBS = 500
MAX_CONCURRENCY = 25
MAX_ATTEMPTS = 10


def _read_jobs(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"batch input does not exist: {path}")
    jobs: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise TypeError(f"batch line {line_number} must be a JSON object")
        else:
            value = {"prompt": line}
        if not str(value.get("prompt") or "").strip():
            raise ValueError(f"batch line {line_number} requires a non-empty prompt")
        fields = value.get("fields")
        if fields is not None and not isinstance(fields, dict):
            raise TypeError(f"batch line {line_number} fields must be a JSON object")
        jobs.append(value)
    if not jobs:
        raise ValueError("batch input contains no jobs")
    if len(jobs) > MAX_BATCH_JOBS:
        raise ValueError(f"batch input has {len(jobs)} jobs; maximum is {MAX_BATCH_JOBS}")
    return jobs


def prepare_batch(values: Mapping[str, Any]) -> list[PreparedJob]:
    input_path = Path(str(values["input"])).expanduser()
    out_dir = Path(str(values["out_dir"])).expanduser()
    raw_jobs = _read_jobs(input_path)
    jobs: list[PreparedJob] = []
    for index, override in enumerate(raw_jobs, 1):
        merged = dict(values)
        nested_fields = override.get("fields") or {}
        merged.update({key: value for key, value in nested_fields.items() if value is not None})
        merged.update(
            {
                key: value
                for key, value in override.items()
                if key != "fields" and value is not None
            }
        )
        merged["force"] = values["force"]
        merged["out_dir"] = str(out_dir)
        jobs.append(
            prepare_job(
                "generate",
                merged,
                base_dir=input_path.parent,
                batch_index=index,
                batch_out_dir=out_dir,
            )
        )
    validate_output_plans([job.plan for job in jobs], bool(values["force"]))
    return jobs


def dry_run_batch(jobs: list[PreparedJob]) -> None:
    for job in jobs:
        print_dry_run(job)


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def retry_after_seconds(exc: Exception) -> float | None:
    for name in ("retry_after", "retry_after_seconds"):
        value = getattr(exc, name, None)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    header_value: str | None = None
    for source in (exc, getattr(exc, "response", None)):
        headers = getattr(source, "headers", None)
        if headers is not None:
            value = headers.get("retry-after") or headers.get("Retry-After")
            if value is not None:
                header_value = str(value).strip()
                break
    if header_value:
        try:
            return max(0.0, float(header_value))
        except ValueError:
            try:
                target = parsedate_to_datetime(header_value)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    match = re.search(r"retry[- ]after[:= ]+([0-9]+(?:\.[0-9]+)?)", str(exc), re.IGNORECASE)
    return float(match.group(1)) if match else None


def is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, (ValueError, TypeError)):
        return False
    status = _status_code(exc)
    if status in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "ratelimit" in name or "rate_limit" in name:
        return True
    if "429" in message or "rate limit" in message or "too many requests" in message:
        return True
    transient_names = ("timeout", "timedout", "temporary", "apiconnection", "networkerror")
    if any(marker in name for marker in transient_names):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return any(
        marker in message
        for marker in ("timed out", "timeout", "connection reset", "temporarily unavailable")
    )


def _attempt_job(
    job: PreparedJob,
    base_url: str,
    max_attempts: int,
    stop: threading.Event,
) -> list[Path]:
    for attempt in range(1, max_attempts + 1):
        if stop.is_set():
            raise RuntimeError("cancelled after another batch job failed")
        try:
            response = request_live(job, base_url)
        except Exception as exc:
            if not is_retryable_error(exc) or attempt == max_attempts:
                raise
            delay = retry_after_seconds(exc)
            sleep_seconds = delay if delay is not None else min(60.0, 2.0**attempt)
            print(
                f"batch job {job.index} attempt {attempt}/{max_attempts} failed "
                f"({type(exc).__name__}); retrying in {sleep_seconds:.1f}s",
                file=sys.stderr,
            )
            time.sleep(sleep_seconds)
        else:
            return finish_response(job, response)
    raise RuntimeError("retry loop ended unexpectedly")


def run_batch(
    jobs: list[PreparedJob],
    base_url: str,
    *,
    concurrency: int,
    max_attempts: int,
    fail_fast: bool,
) -> bool:
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
    if not 1 <= max_attempts <= MAX_ATTEMPTS:
        raise ValueError(f"--max-attempts must be between 1 and {MAX_ATTEMPTS}")
    stop = threading.Event()
    succeeded = True
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures: dict[Future[list[Path]], PreparedJob] = {
            pool.submit(_attempt_job, job, base_url, max_attempts, stop): job for job in jobs
        }
        for future in as_completed(futures):
            job = futures[future]
            try:
                paths = future.result()
            except Exception as exc:  # noqa: BLE001 - report every worker failure uniformly.
                succeeded = False
                print(f"error: batch job {job.index} failed: {exc}", file=sys.stderr)
                if fail_fast:
                    stop.set()
                    for pending in futures:
                        pending.cancel()
            else:
                for path in paths:
                    print(path)
    return succeeded
