"""Run a createTask/getTaskResult load test: `python -m tools.load.run --key ...`.

Two things are needed for a meaningful run: a funded account key and a stack
that answers. `python -m tools.load.devstack` brings up the second on the local
box (redis + postgres + gateway + fake-solver workers); against a deployed
compose the same driver points at `--url`.

PYTHONPATH must include this worktree's `src`, because the tool reads
`captcha.domain.catalog` to know which lane to sample for the type.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .config import build_parser, config_from_args
from .report import format_buckets, format_text, verdict_lines
from .scenario import run_load


def _progress(snapshot: dict[str, object]) -> None:
    print(
        "  +{elapsed:>7}s created={created} ready={ready} errors={errors} "
        "in_flight={in_flight} dropped={dropped}".format(**snapshot),
        file=sys.stderr,
        flush=True,
    )


def _exit_code(result, args: argparse.Namespace) -> int:
    if not args.strict:
        return 0
    if result.errors or result.target_ratio < 0.95:
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = config_from_args(args, parser)

    result = asyncio.run(run_load(cfg, on_progress=_progress if cfg.progress_sec > 0 else None))
    print(format_text(result))

    buckets = format_buckets(result)
    if buckets:
        print("\nper-bucket (drift over the run):")
        print(buckets)

    for line in verdict_lines(result):
        print(f"verdict: {line}")

    if cfg.report_json:
        path = Path(cfg.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\njson: {path}")
    return _exit_code(result, args)


if __name__ == "__main__":
    raise SystemExit(main())
