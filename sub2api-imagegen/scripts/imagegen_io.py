"""Output planning, response decoding, and optional image resizing."""

from __future__ import annotations

import base64
import binascii
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from imagegen_support import COMPATIBLE_USER_AGENT


@dataclass(frozen=True)
class OutputPlan:
    originals: list[Path]
    downscaled: list[Path]


def format_extension(output_format: str) -> str:
    return "jpeg" if output_format == "jpeg" else output_format


def numbered_paths(base: Path, n: int) -> list[Path]:
    if n == 1:
        return [base]
    return [base.with_name(f"{base.stem}-{index}{base.suffix}") for index in range(1, n + 1)]


def prompt_slug(prompt: str, limit: int = 42) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", prompt).strip("-").lower()
    return (slug[:limit].rstrip("-") or "image")


def _batch_base(out_dir: Path, out: str | None, index: int, prompt: str, extension: str) -> Path:
    if out:
        relative = Path(out)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("batch job out must be a relative path inside --out-dir")
        filename = Path(relative.name)
        if not filename.suffix:
            filename = filename.with_suffix(f".{extension}")
        return out_dir / filename
    return out_dir / f"{index:03d}-{prompt_slug(prompt)}.{extension}"


def plan_outputs(
    values: Mapping[str, Any],
    prompt: str,
    *,
    batch_index: int | None = None,
    batch_out_dir: Path | None = None,
) -> OutputPlan:
    n = int(values["n"])
    extension = format_extension(str(values["output_format"]))
    if batch_index is not None:
        if batch_out_dir is None:
            raise ValueError("batch output directory is required")
        base = _batch_base(batch_out_dir, values.get("out"), batch_index, prompt, extension)
        originals = numbered_paths(base, n)
    elif values.get("out"):
        originals = numbered_paths(Path(str(values["out"])).expanduser(), n)
    elif values.get("out_dir"):
        directory = Path(str(values["out_dir"])).expanduser()
        originals = [directory / f"image_{index}.{extension}" for index in range(1, n + 1)]
    else:
        originals = numbered_paths(Path("output/imagegen/output.png"), n)
    max_dim = values.get("downscale_max_dim")
    suffix = str(values.get("downscale_suffix") or "-web")
    downscaled = [path.with_name(f"{path.stem}{suffix}{path.suffix}") for path in originals] if max_dim else []
    return OutputPlan(originals, downscaled)


def validate_output_plans(plans: Sequence[OutputPlan], force: bool) -> None:
    all_paths = [path for plan in plans for path in (*plan.originals, *plan.downscaled)]
    canonical = [str(path.expanduser().resolve()).casefold() for path in all_paths]
    if len(canonical) != len(set(canonical)):
        raise ValueError("two outputs resolve to the same path")
    directories = [path for path in all_paths if path.exists() and path.is_dir()]
    if directories:
        raise IsADirectoryError(f"output path is a directory: {directories[0]}")
    if not force:
        existing = [str(path) for path in all_paths if path.exists()]
        if existing:
            raise FileExistsError("refusing to overwrite existing output: " + ", ".join(existing))


def _item_value(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _download(url: str) -> bytes:
    with httpx.Client(
        headers={"User-Agent": COMPATIBLE_USER_AGENT},
        follow_redirects=True,
        timeout=60.0,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def response_bytes(item: Any) -> bytes:
    encoded = _item_value(item, "b64_json")
    if encoded:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image response contains invalid base64 data") from exc
    else:
        url = _item_value(item, "url")
        if not url:
            raise ValueError("image response contains neither b64_json nor url")
        data = _download(str(url))
    if not data:
        raise ValueError("image response is empty")
    return data


def decode_response(response: Any, expected: int) -> list[bytes]:
    items = response.get("data") if isinstance(response, dict) else getattr(response, "data", None)
    if not items:
        raise ValueError("image API returned no image data")
    if len(items) != expected:
        raise ValueError(f"image API returned {len(items)} image(s); expected {expected}")
    return [response_bytes(item) for item in items]


def _write(path: Path, data: bytes, force: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb" if force else "xb") as stream:
        stream.write(data)


def _downscaled_bytes(data: bytes, max_dim: int, output_format: str) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        image.load()
        image.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
        api_format = "JPEG" if output_format in {"jpg", "jpeg"} else output_format.upper()
        if api_format == "JPEG" and image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        output = io.BytesIO()
        image.save(output, format=api_format)
        return output.getvalue()


def save_images(
    payloads: Sequence[bytes],
    plan: OutputPlan,
    *,
    force: bool,
    downscale_max_dim: int | None,
    output_format: str,
) -> list[Path]:
    if len(payloads) != len(plan.originals):
        raise ValueError("decoded image count does not match output plan")
    downscaled_payloads = (
        [_downscaled_bytes(data, downscale_max_dim, output_format) for data in payloads]
        if downscale_max_dim
        else []
    )
    for path, data in zip(plan.originals, payloads):
        _write(path, data, force)
    for path, data in zip(plan.downscaled, downscaled_payloads):
        _write(path, data, force)
    return [*plan.originals, *plan.downscaled]
