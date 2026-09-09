"""JSONL batch loading and concurrent execution."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
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
from imagegen_support import MAX_ATTEMPTS

MAX_BATCH_JOBS = 500
MAX_CONCURRENCY = 25


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


def _attempt_job(
    job: PreparedJob,
    base_url: str,
    max_attempts: int,
    stop: threading.Event,
) -> list[Path]:
    if stop.is_set():
        raise RuntimeError("cancelled after another batch job failed")
    response = request_live(job, base_url, max_attempts)
    return finish_response(job, response)


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
