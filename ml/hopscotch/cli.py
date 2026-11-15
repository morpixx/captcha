"""Command line for the hopscotch kit.

    python -m ml.hopscotch dataset --data D:/hopscotch_v2 --out artifacts/hopscotch/split.json
    python -m ml.hopscotch train   --manifest artifacts/hopscotch/split.json --run readout-v2 --stage readout
    python -m ml.hopscotch eval    --checkpoint D:/hopscotch_v2/runs/<run>/best.pt --out artifacts/hopscotch/eval
    python -m ml.hopscotch export  --run D:/hopscotch_v2/runs/<run> --out artifacts/hopscotch-v1/v1
    python -m ml.hopscotch publish --artifact artifacts/hopscotch-v1/v1 --cache-dir models-cache

Every subcommand is offline. No live browsers, no vendor sites, no network except
the S3 endpoint used by publish.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_CONFIG = "configs/v1-spatial.yaml"
DEFAULT_NAME = "hopscotch-v1"
DEFAULT_ROLES = ("gold_val", "client_val")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hopscotch-kit", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    dataset = commands.add_parser("dataset", help="export a frozen split from the two sources")
    dataset.add_argument("--data", default="D:/hopscotch_v2", help="hopscotch_v2 checkout")
    dataset.add_argument("--out", default="artifacts/hopscotch/split.json")
    dataset.add_argument("--upstream", default=None, help="frozen hopscotch_v2 split (roles imported)")
    dataset.add_argument("--seed", type=int, default=42)
    dataset.add_argument("--no-hash-images", action="store_true")
    dataset.add_argument("--force", action="store_true")

    train = commands.add_parser("train", help="run the hopscotch_v2 trainer")
    train.add_argument("--data", default="D:/hopscotch_v2")
    train.add_argument("--manifest", default="artifacts/hopscotch/split.json")
    train.add_argument("--config", default=DEFAULT_CONFIG)
    train.add_argument("--run", default=None)
    train.add_argument("--stage", default=None)
    train.add_argument("--epochs", type=int, default=None)
    train.add_argument("--warmup-epochs", type=int, default=None)
    train.add_argument("--resume", default=None)
    train.add_argument("--initialize", default=None)
    train.add_argument("--python", default=None, help="training interpreter (default: current)")
    train.add_argument("--dry-run", action="store_true")
    train.add_argument("extra", nargs=argparse.REMAINDER)

    evaluate = commands.add_parser("eval", help="GoldVal30 + client validation")
    evaluate.add_argument("--data", default="D:/hopscotch_v2")
    evaluate.add_argument("--checkpoint", required=True)
    evaluate.add_argument("--out", default="artifacts/hopscotch/eval")
    evaluate.add_argument("--release", default=None)
    evaluate.add_argument("--config", default=DEFAULT_CONFIG)
    evaluate.add_argument("--roles", default=",".join(DEFAULT_ROLES))
    evaluate.add_argument("--python", default=None)
    evaluate.add_argument("--dry-run", action="store_true")

    export = commands.add_parser("export", help="run dir -> model.safetensors + config + release")
    export.add_argument("--run", required=True, help="runs/<name> directory")
    export.add_argument("--out", required=True)
    export.add_argument("--version", default="v1")
    export.add_argument("--checkpoint", default=None)
    export.add_argument("--release", default=None, help="calibration release.json (default: run dir)")
    export.add_argument("--metrics", default=None, help="metrics.json from eval")
    export.add_argument("--allow-mismatch", action="store_true")

    publish_cmd = commands.add_parser("publish", help="upload the artifact triple")
    publish_cmd.add_argument("--artifact", required=True)
    publish_cmd.add_argument("--name", default=DEFAULT_NAME)
    publish_cmd.add_argument("--version", default="v1")
    publish_cmd.add_argument("--bucket", default=None)
    publish_cmd.add_argument("--cache-dir", default=None)
    publish_cmd.add_argument("--allow-not-ready", action="store_true")
    publish_cmd.add_argument("--no-latest", action="store_true")
    publish_cmd.add_argument("--dry-run", action="store_true")

    verify = commands.add_parser("verify", help="validate an artifact directory")
    verify.add_argument("--artifact", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "dataset":
            return _dataset(args)
        if args.command == "train":
            return _train(args)
        if args.command == "eval":
            return _eval(args)
        if args.command == "export":
            return _export(args)
        if args.command == "publish":
            return _publish(args)
        return _verify(args)
    except _error_types() as exc:
        print(f"hopscotch-kit: {exc}", file=sys.stderr)
        return 2


def _error_types() -> tuple[type[BaseException], ...]:
    """Catch the service's ConfigError when the captcha package is importable.

    dataset/train/eval run in the training environment, where the service package
    is not installed; export/publish additionally need it for the artifact schema.
    """
    try:
        from captcha.domain.errors import CaptchaError
    except ImportError:
        return (OSError, ValueError)
    return (OSError, ValueError, CaptchaError)


def _dataset(args: argparse.Namespace) -> int:
    from .dataset import load_dataset, write_manifest

    dataset = load_dataset(
        args.data,
        upstream=args.upstream,
        seed=args.seed,
        hash_images=not args.no_hash_images,
    )
    path = write_manifest(dataset, args.out, force=args.force)
    print(json.dumps({"manifest": str(path), "counts": dataset.manifest["counts"]}, indent=2))
    return 0


def _train(args: argparse.Namespace) -> int:
    from .train import preflight, run_plan, train_plan

    audit = preflight(args.data, args.manifest)
    if audit is not None:
        print(
            f"preflight OK: {audit['protocol']} split, "
            f"{sum(audit['counts'].values())} waves",
            file=sys.stderr,
        )
    plan = train_plan(
        root=args.data,
        config=args.config,
        python=args.python,
        run=args.run,
        stage=args.stage,
        epochs=args.epochs,
        warmup_epochs=args.warmup_epochs,
        resume=args.resume,
        initialize=args.initialize,
        extra=args.extra,
    )
    return run_plan(plan, dry_run=args.dry_run)


def _eval(args: argparse.Namespace) -> int:
    roles = tuple(role.strip() for role in args.roles.split(",") if role.strip())
    from .evaluate import evaluate_command, run_matrix

    if args.dry_run:

        for role in roles:
            print(" ".join(
                evaluate_command(
                    args.data,
                    checkpoint=args.checkpoint,
                    role=role,
                    output=Path(args.out) / f"{role}.json",
                    config=args.config,
                    release=args.release,
                    python=args.python,
                )
            ))
        return 0
    summary = run_matrix(
        args.data,
        checkpoint=args.checkpoint,
        out_dir=args.out,
        config=args.config,
        release=args.release,
        roles=roles,
        python=args.python,
    )
    print(json.dumps(summary, indent=2))
    return 0


def _export(args: argparse.Namespace) -> int:
    from .export import export_run

    result = export_run(
        args.run,
        args.out,
        version=args.version,
        checkpoint=args.checkpoint,
        release=args.release,
        metrics=args.metrics,
        allow_mismatch=args.allow_mismatch,
    )
    print(
        json.dumps(
            {
                "artifact": str(result.directory),
                "status": result.release["status"],
                "threshold": result.release["threshold"],
                "checksums": result.checksums["files"],
            },
            indent=2,
        )
    )
    return 0


def _publish(args: argparse.Namespace) -> int:
    from .publish import publish

    result = publish(
        args.artifact,
        name=args.name,
        version=args.version,
        bucket=args.bucket,
        cache_dir=args.cache_dir,
        set_latest=not args.no_latest,
        allow_not_ready=args.allow_not_ready,
        dry_run=args.dry_run,
    )
    print(
        json.dumps(
            {
                "name": result.name,
                "version": result.version,
                "keys": list(result.keys),
                "files": dict(result.files),
                "layout": result.meta.layout if result.meta else "bundle",
                "cache_dir": str(result.cache_dir) if result.cache_dir else None,
                "dry_run": result.dry_run,
            },
            indent=2,
        )
    )
    return 0


def _verify(args: argparse.Namespace) -> int:
    from .export import verify_artifact

    artifact = verify_artifact(args.artifact)
    print(
        json.dumps(
            {
                "model": artifact.config.model_name,
                "version": artifact.config.version,
                "status": artifact.release.status,
                "threshold": artifact.release.threshold,
                "frames": list(artifact.config.input.frames),
                "epoch": artifact.config.checkpoint_epoch,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
