"""Configuration, request construction, and validation helpers."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from openai import DefaultHttpxClient, OpenAI

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "auto"
DEFAULT_QUALITY = "medium"
DEFAULT_FORMAT = "png"
COMPATIBLE_USER_AGENT = "python-requests/2.32.5"
SKILL_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG_PATH = SKILL_ROOT / "config.local.json"
OLDER_MODEL_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}
PROMPT_FIELDS = (
    ("use_case", "Use case"),
    ("scene", "Scene/background"),
    ("subject", "Subject"),
    ("style", "Style/medium"),
    ("composition", "Composition/framing"),
    ("lighting", "Lighting/mood"),
    ("palette", "Color palette"),
    ("materials", "Materials/textures"),
    ("text", "Text (verbatim)"),
    ("constraints", "Constraints"),
    ("negative", "Avoid"),
)


def clean_sdk_headers(request: httpx.Request) -> None:
    """Apply the two compatibility changes required by the target gateway."""
    for name in list(request.headers):
        if name.lower().startswith("x-stainless-"):
            del request.headers[name]
    request.headers["User-Agent"] = COMPATIBLE_USER_AGENT


def resolve_base_url() -> str:
    value = os.environ.get("OPENAI_BASE_URL")
    if value is not None:
        if value.strip():
            return value.strip()
        raise RuntimeError("OPENAI_BASE_URL is set but empty")
    if LOCAL_CONFIG_PATH.is_file():
        try:
            config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read valid JSON from {LOCAL_CONFIG_PATH}: {exc}") from exc
        value = config.get("base_url") if isinstance(config, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        raise RuntimeError(f'{LOCAL_CONFIG_PATH.name} requires a non-empty "base_url" string')
    raise RuntimeError(
        "OPENAI_BASE_URL is required; set it or create config.local.json from config.example.json"
    )


def make_client(base_url: str) -> OpenAI:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is required for a live image request")
    transport = DefaultHttpxClient(event_hooks={"request": [clean_sdk_headers]})
    return OpenAI(api_key=key, base_url=base_url, http_client=transport)


def read_prompt(prompt: str | None, prompt_file: str | None, base_dir: Path | None = None) -> str:
    if bool(prompt) == bool(prompt_file):
        raise ValueError("provide exactly one of --prompt or --prompt-file")
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if base_dir is not None and not path.is_absolute():
            path = base_dir / path
        try:
            prompt = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read prompt file {path}: {exc}") from exc
    assert prompt is not None
    prompt = prompt.strip()
    if not prompt:
        raise ValueError("prompt must not be empty")
    return prompt


def structured_prompt(prompt: str, values: Mapping[str, Any]) -> str:
    if values.get("augment") is False:
        return prompt
    lines: list[str] = []
    use_case = values.get("use_case")
    if use_case:
        lines.append(f"Use case: {use_case}")
    lines.append(f"Primary request: {prompt}")
    for key, label in PROMPT_FIELDS[1:]:
        value = values.get(key)
        if value:
            rendered = f'"{value}"' if key == "text" else str(value)
            lines.append(f"{label}: {rendered}")
    return "\n".join(lines)


def validate_size(model: str, size: str) -> None:
    if not model.startswith("gpt-image-"):
        raise ValueError("model must be in the gpt-image family")
    if model != "gpt-image-2":
        if size not in OLDER_MODEL_SIZES:
            raise ValueError("this model supports auto, 1024x1024, 1536x1024, or 1024x1536")
        return
    if size == "auto":
        return
    match = re.fullmatch(r"(\d+)x(\d+)", size)
    if not match:
        raise ValueError("gpt-image-2 size must be auto or WIDTHxHEIGHT")
    width, height = map(int, match.groups())
    if max(width, height) > 3840:
        raise ValueError("gpt-image-2 size cannot exceed 3840px on either edge")
    if width % 16 or height % 16:
        raise ValueError("gpt-image-2 width and height must be multiples of 16")
    if max(width, height) > 3 * min(width, height):
        raise ValueError("gpt-image-2 aspect ratio cannot exceed 3:1")
    pixels = width * height
    if not 655_360 <= pixels <= 8_294_400:
        raise ValueError("gpt-image-2 total pixels must be between 655360 and 8294400")


def validate_options(values: Mapping[str, Any], command: str) -> None:
    model = str(values["model"])
    validate_size(model, str(values["size"]))
    n = int(values["n"])
    if not 1 <= n <= 10:
        raise ValueError("--n must be between 1 and 10")
    if values["quality"] not in {"low", "medium", "high", "auto"}:
        raise ValueError("quality must be low, medium, high, or auto")
    output_format = values["output_format"]
    if output_format not in {"png", "jpeg", "jpg", "webp"}:
        raise ValueError("output format must be png, jpeg, jpg, or webp")
    background = values.get("background")
    if background not in {None, "auto", "opaque", "transparent"}:
        raise ValueError("background must be auto, opaque, or transparent")
    if background == "transparent":
        if model == "gpt-image-2":
            raise ValueError("gpt-image-2 does not support transparent background output")
        if output_format not in {"png", "webp"}:
            raise ValueError("transparent backgrounds require png or webp output")
    compression = values.get("output_compression")
    if compression is not None and not 0 <= int(compression) <= 100:
        raise ValueError("--output-compression must be between 0 and 100")
    if values.get("moderation") not in {None, "auto", "low"}:
        raise ValueError("moderation must be auto or low")
    fidelity = values.get("input_fidelity")
    if command != "edit" and fidelity is not None:
        raise ValueError("--input-fidelity is edit-only")
    if fidelity not in {None, "low", "high"}:
        raise ValueError("input fidelity must be low or high")
    if model == "gpt-image-2" and fidelity is not None:
        raise ValueError("gpt-image-2 always uses high input fidelity; omit --input-fidelity")
    downscale = values.get("downscale_max_dim")
    if downscale is not None and int(downscale) < 1:
        raise ValueError("--downscale-max-dim must be positive")


def request_kwargs(values: Mapping[str, Any], prompt: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "model": values["model"],
        "prompt": prompt,
        "n": int(values["n"]),
        "size": values["size"],
        "quality": values["quality"],
        "output_format": "jpeg" if values["output_format"] == "jpg" else values["output_format"],
    }
    for key in ("background", "output_compression", "moderation"):
        if values.get(key) is not None:
            result[key] = values[key]
    if values.get("input_fidelity") is not None:
        result["input_fidelity"] = values["input_fidelity"]
    return result
