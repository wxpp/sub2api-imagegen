# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx>=0.27,<1",
#   "openai>=2,<3",
# ]
# ///

"""Generate or edit images through a configured gateway with the OpenAI SDK."""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

import httpx
from openai import DefaultHttpxClient, OpenAI


DEFAULT_MODEL = "gpt-image-2"
COMPATIBLE_USER_AGENT = "python-requests/2.32.5"
SKILL_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG_PATH = SKILL_ROOT / "config.local.json"


def clean_sdk_headers(request: httpx.Request) -> None:
    """Remove SDK fingerprint headers that the gateway's Cloudflare rule blocks."""
    for name in list(request.headers.keys()):
        if name.lower().startswith("x-stainless-"):
            del request.headers[name]
    request.headers["User-Agent"] = COMPATIBLE_USER_AGENT


def resolve_base_url() -> str:
    """Resolve an explicitly configured gateway URL without using a default."""
    environment_value = os.environ.get("OPENAI_BASE_URL")
    if environment_value is not None:
        base_url = environment_value.strip()
        if not base_url:
            raise RuntimeError("OPENAI_BASE_URL is set but empty")
        return base_url

    if LOCAL_CONFIG_PATH.is_file():
        try:
            config = json.loads(LOCAL_CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read valid JSON from {LOCAL_CONFIG_PATH}: {exc}") from exc
        base_url = config.get("base_url") if isinstance(config, dict) else None
        if not isinstance(base_url, str) or not base_url.strip():
            raise RuntimeError(
                f'{LOCAL_CONFIG_PATH} must contain a non-empty string field named "base_url"'
            )
        return base_url.strip()

    raise RuntimeError(
        "OPENAI_BASE_URL is required; set it in the environment or copy "
        f"config.example.json to {LOCAL_CONFIG_PATH.name} and set base_url"
    )


def make_client(base_url: str) -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for a live image request")
    http_client = DefaultHttpxClient(event_hooks={"request": [clean_sdk_headers]})
    return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def item_value(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def extension_for(output_format: str | None) -> str:
    if not output_format:
        return "png"
    return "jpg" if output_format.lower() in {"jpg", "jpeg"} else output_format.lower()


def output_paths(args: argparse.Namespace) -> list[Path]:
    if args.out:
        if args.n != 1:
            raise ValueError("--out can only be used with --n 1; use --out-dir for multiple images")
        return [Path(args.out).expanduser().resolve()]

    out_dir = Path(args.out_dir or ".").expanduser().resolve()
    suffix = extension_for(args.output_format)
    stem = "sub2api-edit" if args.command == "edit" else "sub2api-image"
    return [out_dir / f"{stem}-{index}.{suffix}" for index in range(1, args.n + 1)]


def validate_outputs(paths: Sequence[Path], force: bool) -> None:
    if not force:
        existing = [str(path) for path in paths if path.exists()]
        if existing:
            raise FileExistsError(
                "refusing to overwrite existing output; choose another path or pass --force: "
                + ", ".join(existing)
            )
    for path in paths:
        if path.exists() and path.is_dir():
            raise IsADirectoryError(f"output path is a directory: {path}")


def request_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    values: dict[str, Any] = {
        "model": args.model,
        "prompt": args.prompt,
        "n": args.n,
        "size": args.size,
    }
    if args.quality is not None:
        values["quality"] = args.quality
    if args.output_format is not None:
        values["output_format"] = "jpeg" if args.output_format == "jpg" else args.output_format
    return values


def print_dry_run(args: argparse.Namespace, paths: Sequence[Path], base_url: str) -> None:
    payload = request_kwargs(args)
    if args.command == "edit":
        payload["image"] = [str(Path(value).expanduser().resolve()) for value in args.image]
    summary = {
        "command": args.command,
        "base_url": base_url,
        "payload": payload,
        "outputs": [str(path) for path in paths],
        "header_policy": {
            "remove": "x-stainless-*",
            "user-agent": COMPATIBLE_USER_AGENT,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def download_url(url: str) -> bytes:
    with httpx.Client(
        headers={"User-Agent": COMPATIBLE_USER_AGENT}, follow_redirects=True, timeout=60.0
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def image_bytes(item: Any) -> bytes:
    encoded = item_value(item, "b64_json")
    if encoded:
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image response contained invalid b64_json") from exc
        if not data:
            raise ValueError("image response contained empty b64_json")
        return data

    url = item_value(item, "url")
    if url:
        data = download_url(url)
        if not data:
            raise ValueError("image URL returned an empty response")
        return data
    raise ValueError("image response contained neither b64_json nor url")


def save_response(response: Any, paths: Sequence[Path], force: bool) -> None:
    items = getattr(response, "data", None)
    if items is None and isinstance(response, dict):
        items = response.get("data")
    if not items:
        raise ValueError("image API returned no image data")
    if len(items) != len(paths):
        raise ValueError(f"image API returned {len(items)} item(s), expected {len(paths)}")

    decoded = [image_bytes(item) for item in items]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path, data in zip(paths, decoded):
        with path.open("wb" if force else "xb") as output:
            output.write(data)
        print(path)


def run_live(args: argparse.Namespace, paths: Sequence[Path], base_url: str) -> None:
    client = make_client(base_url)
    try:
        kwargs = request_kwargs(args)
        if args.command == "generate":
            response = client.images.generate(**kwargs)
        else:
            with ExitStack() as stack:
                handles = [stack.enter_context(Path(value).expanduser().open("rb")) for value in args.image]
                kwargs["image"] = handles[0] if len(handles) == 1 else handles
                response = client.images.edit(**kwargs)
        save_response(response, paths, args.force)
    finally:
        client.close()


def add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--out")
    parser.add_argument("--out-dir")
    parser.add_argument("--quality")
    parser.add_argument("--output-format", choices=["png", "jpeg", "jpg", "webp"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="generate images from a prompt")
    add_shared_arguments(generate)
    edit = subparsers.add_parser("edit", help="edit one or more source images")
    add_shared_arguments(edit)
    edit.add_argument("--image", action="append", required=True)
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be at least 1")
    if args.out and args.out_dir:
        parser.error("--out and --out-dir are mutually exclusive")
    if args.command == "edit":
        missing = [value for value in args.image if not Path(value).expanduser().is_file()]
        if missing:
            parser.error("input image does not exist or is not a file: " + ", ".join(missing))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        base_url = resolve_base_url()
        paths = output_paths(args)
        validate_outputs(paths, args.force)
        if args.dry_run:
            print_dry_run(args, paths, base_url)
        else:
            run_live(args, paths, base_url)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
