"""Command-line parsing and top-level orchestration."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from imagegen_batch import (
    MAX_ATTEMPTS,
    MAX_CONCURRENCY,
    dry_run_batch,
    prepare_batch,
    run_batch,
)
from imagegen_io import validate_output_plans
from imagegen_runner import prepare_job, print_dry_run, run_live
from imagegen_support import (
    DEFAULT_FORMAT,
    DEFAULT_MODEL,
    DEFAULT_QUALITY,
    DEFAULT_SIZE,
    resolve_base_url,
)


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--n", type=int, default=1)
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--quality", default=DEFAULT_QUALITY)
    parser.add_argument("--background")
    parser.add_argument("--output-format", default=DEFAULT_FORMAT)
    parser.add_argument("--output-compression", type=int)
    parser.add_argument("--moderation")
    parser.add_argument("--out")
    parser.add_argument("--out-dir")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--augment", dest="augment", action="store_true")
    parser.add_argument("--no-augment", dest="augment", action="store_false")
    parser.set_defaults(augment=True)
    parser.add_argument("--use-case")
    parser.add_argument("--scene")
    parser.add_argument("--subject")
    parser.add_argument("--style")
    parser.add_argument("--composition")
    parser.add_argument("--lighting")
    parser.add_argument("--palette")
    parser.add_argument("--materials")
    parser.add_argument("--text")
    parser.add_argument("--constraints")
    parser.add_argument("--negative")
    parser.add_argument("--downscale-max-dim", type=int)
    parser.add_argument("--downscale-suffix", default="-web")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="create images from one prompt")
    add_common_arguments(generate)

    edit = commands.add_parser("edit", help="edit one or more input images")
    add_common_arguments(edit)
    edit.add_argument("--image", action="append", required=True)
    edit.add_argument("--mask")
    edit.add_argument("--input-fidelity")

    batch = commands.add_parser("generate-batch", help="run generation jobs from JSONL")
    add_common_arguments(batch)
    batch.add_argument("--input", required=True)
    batch.add_argument("--concurrency", type=int, default=5)
    batch.add_argument("--max-attempts", type=int, default=3)
    batch.add_argument("--fail-fast", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "generate-batch" and args.out and args.out_dir:
        parser.error("--out and --out-dir are mutually exclusive")
    if args.command == "generate-batch":
        if not args.out_dir:
            parser.error("generate-batch requires --out-dir")
        if args.out:
            parser.error("generate-batch does not accept global --out; set out per JSONL job")
        if not 1 <= args.concurrency <= MAX_CONCURRENCY:
            parser.error(f"--concurrency must be between 1 and {MAX_CONCURRENCY}")
        if not 1 <= args.max_attempts <= MAX_ATTEMPTS:
            parser.error(f"--max-attempts must be between 1 and {MAX_ATTEMPTS}")
    return args


def _run_single(args: argparse.Namespace, base_url: str) -> int:
    values = vars(args)
    job = prepare_job(args.command, values)
    validate_output_plans([job.plan], bool(args.force))
    if args.dry_run:
        print_dry_run(job)
        return 0
    for path in run_live(job, base_url):
        print(path)
    return 0


def _run_batch(args: argparse.Namespace, base_url: str) -> int:
    jobs = prepare_batch(vars(args))
    if args.dry_run:
        dry_run_batch(jobs)
        return 0
    ok = run_batch(
        jobs,
        base_url,
        concurrency=args.concurrency,
        max_attempts=args.max_attempts,
        fail_fast=args.fail_fast,
    )
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        base_url = resolve_base_url()
        if args.command == "generate-batch":
            return _run_batch(args, base_url)
        return _run_single(args, base_url)
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - convert all user-facing failures to a clean exit.
        print(f"error: {exc}", file=sys.stderr)
        return 1
