"""Prepare and execute individual image API jobs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from imagegen_io import OutputPlan, decode_response, plan_outputs, save_images
from imagegen_support import (
    make_client,
    read_prompt,
    request_kwargs,
    structured_prompt,
    validate_options,
)

MAX_INPUT_BYTES = 50 * 1024 * 1024


@dataclass
class PreparedJob:
    command: str
    values: dict[str, Any]
    prompt: str
    plan: OutputPlan
    image_paths: list[Path]
    mask_path: Path | None
    index: int | None = None


def _input_path(value: str, base_dir: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        raise ValueError(f"input file does not exist: {path}")
    if path.stat().st_size >= MAX_INPUT_BYTES:
        raise ValueError(f"input file must be smaller than 50MB: {path}")
    return path


def prepare_job(
    command: str,
    values: Mapping[str, Any],
    *,
    base_dir: Path | None = None,
    batch_index: int | None = None,
    batch_out_dir: Path | None = None,
) -> PreparedJob:
    copied = dict(values)
    validate_options(copied, command)
    raw_prompt = read_prompt(copied.get("prompt"), copied.get("prompt_file"), base_dir)
    prompt = structured_prompt(raw_prompt, copied)
    images: list[Path] = []
    mask: Path | None = None
    if command == "edit":
        raw_images = copied.get("image") or []
        if not 1 <= len(raw_images) <= 16:
            raise ValueError("edit requires between 1 and 16 --image values")
        images = [_input_path(value, base_dir) for value in raw_images]
        if copied.get("mask"):
            mask = _input_path(str(copied["mask"]), base_dir)
    plan = plan_outputs(
        copied,
        raw_prompt,
        batch_index=batch_index,
        batch_out_dir=batch_out_dir,
    )
    return PreparedJob(command, copied, prompt, plan, images, mask, batch_index)


def dry_run_data(job: PreparedJob) -> dict[str, Any]:
    payload = request_kwargs(job.values, job.prompt)
    if job.command == "edit":
        payload["image"] = [str(path) for path in job.image_paths]
        if job.mask_path:
            payload["mask"] = str(job.mask_path)
    summary: dict[str, Any] = {
        "endpoint": "/v1/images/edits" if job.command == "edit" else "/v1/images/generations",
        "model": payload["model"],
        "n": payload["n"],
        "output_format": payload["output_format"],
        "outputs": [str(path) for path in job.plan.originals],
        "outputs_downscaled": [str(path) for path in job.plan.downscaled] or None,
        "prompt": payload["prompt"],
        "quality": payload["quality"],
        "size": payload["size"],
    }
    if job.index is not None:
        summary["job"] = job.index
    for key in ("background", "output_compression", "moderation", "input_fidelity"):
        if key in payload:
            summary[key] = payload[key]
    if job.command == "edit":
        summary["images"] = payload["image"]
        if "mask" in payload:
            summary["mask"] = payload["mask"]
    return summary


def print_dry_run(job: PreparedJob) -> None:
    print(json.dumps(dry_run_data(job), ensure_ascii=False, indent=2))


def request_live(job: PreparedJob, base_url: str) -> Any:
    client = make_client(base_url)
    try:
        kwargs = request_kwargs(job.values, job.prompt)
        if job.command == "generate":
            response = client.images.generate(**kwargs)
        else:
            with ExitStack() as stack:
                handles = [stack.enter_context(path.open("rb")) for path in job.image_paths]
                kwargs["image"] = handles[0] if len(handles) == 1 else handles
                if job.mask_path:
                    kwargs["mask"] = stack.enter_context(job.mask_path.open("rb"))
                response = client.images.edit(**kwargs)
        return response
    finally:
        client.close()


def finish_response(job: PreparedJob, response: Any) -> list[Path]:
    payloads = decode_response(response, int(job.values["n"]))
    return save_images(
        payloads,
        job.plan,
        force=bool(job.values["force"]),
        downscale_max_dim=job.values.get("downscale_max_dim"),
        output_format=str(job.values["output_format"]),
    )


def run_live(job: PreparedJob, base_url: str) -> list[Path]:
    return finish_response(job, request_live(job, base_url))
