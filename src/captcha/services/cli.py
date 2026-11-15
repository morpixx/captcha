"""Operator CLI: migrations and accounts.

    python -m captcha.services.cli init-db
    python -m captcha.services.cli create-account --name demo --balance 10
    python -m captcha.services.cli topup --key <api_key> --amount 5
    python -m captcha.services.cli list-accounts
    python -m captcha.services.cli balance --key <api_key>

Everything here talks to Postgres only — no redis, no gateway. It is the
bootstrap path: without an account row the API answers ERROR_KEY_DOES_NOT_EXIST
for every request, so this runs before the first createTask.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from captcha.adapters.storage import AccountRepo, init_engine, shutdown_engine
from captcha.adapters.storage.engine import async_session
from captcha.config import get_settings

if TYPE_CHECKING:
    from captcha.adapters.weights import ArtifactCheck

_ALEMBIC_INI = Path(__file__).resolve().parent.parent / "adapters" / "storage" / "alembic.ini"


def _alembic_config():
    from alembic.config import Config

    if not _ALEMBIC_INI.exists():  # pragma: no cover - packaging mistake
        raise SystemExit(f"alembic.ini not found at {_ALEMBIC_INI}")
    return Config(str(_ALEMBIC_INI))


async def _with_repo(fn):
    settings = get_settings()
    init_engine(settings.postgres_url, pool_size=2)
    try:
        async with async_session() as session:
            return await fn(AccountRepo(session))
    finally:
        await shutdown_engine()


def cmd_init_db(_args: argparse.Namespace) -> int:
    from alembic import command

    cfg = _alembic_config()
    command.upgrade(cfg, "head")
    print("schema at head")
    return 0


def cmd_create_account(args: argparse.Namespace) -> int:
    api_key = args.key or secrets.token_hex(16)
    balance = Decimal(str(args.balance))

    async def run(repo: AccountRepo):
        return await repo.create(api_key=api_key, name=args.name, balance=balance)

    account = asyncio.run(_with_repo(run))
    print(f"account  {account.id}")
    # The repo stores only the hash, so the key is printed from the value this
    # process generated. It cannot be recovered later.
    print(f"key      {api_key}")
    print(f"balance  {account.balance}")
    return 0


def cmd_topup(args: argparse.Namespace) -> int:
    amount = Decimal(str(args.amount))
    if amount <= 0:
        print("amount must be positive", file=sys.stderr)
        return 2

    async def run(repo: AccountRepo):
        account = await repo.get_by_key(args.key)
        if account is None:
            return None
        return await repo.refund(account.id, amount)

    new_balance = asyncio.run(_with_repo(run))
    if new_balance is None:
        print("no such key", file=sys.stderr)
        return 1
    print(f"balance  {new_balance}")
    return 0


def cmd_list_accounts(_args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from captcha.adapters.storage.models import Account

    async def run(_repo: AccountRepo):
        async with async_session() as session:
            rows = (await session.execute(select(Account).order_by(Account.created_at))).scalars().all()
            return [(a.id, a.key_tail, a.name, a.balance, a.disabled) for a in rows]

    rows = asyncio.run(_with_repo(run))
    if not rows:
        print("no accounts — run: create-account --name demo --balance 10")
        return 0
    for acc_id, tail, name, balance, disabled in rows:
        flag = " (disabled)" if disabled else ""
        print(f"{acc_id}  …{tail}  {name or '-'}  {balance}{flag}")
    return 0


def cmd_balance(args: argparse.Namespace) -> int:
    async def run(repo: AccountRepo):
        return await repo.get_by_key(args.key)

    account = asyncio.run(_with_repo(run))
    if account is None:
        print("no such key", file=sys.stderr)
        return 1
    print(f"{account.balance}")
    return 0


@dataclass(frozen=True, slots=True)
class ModelExpectation:
    """What a training run has to hand over for one artifact name.

    layout="bundle" means the bucket entry is a directory
    (<name>/<version>/{...}), not a single weights blob; files is the exact set
    publish-model has to upload and check-model has to find.
    """

    contract: str
    serves: str
    layout: Literal["single", "bundle"] = "single"
    files: tuple[str, ...] = ()


HOPSCOTCH_FILES = ("model.safetensors", "config.json", "release.json")

#: Every artifact name hard-coded in a solver. Kept here so the training side
#: has one checklist of what "plug the model in" actually means.
EXPECTED_MODELS: dict[str, ModelExpectation] = {
    "rotate-v1": ModelExpectation("ResNet50 state_dict, fc -> 2 (sin, cos)", "rotate, captchafox"),
    "click-v1": ModelExpectation(
        "YOLO detector, classes = object names", "click, coordinates, hunt, basilisk, atb, mtcaptcha, yidun, tencent"
    ),
    "bbox-v1": ModelExpectation("YOLO detector", "bounding_box"),
    "drag-v1": ModelExpectation("YOLO detector, source + destination classes", "drag_drop"),
    "draw-v1": ModelExpectation("YOLO detector", "draw_around"),
    "hcaptcha-v1": ModelExpectation(
        "safetensors: classifier.weight/bias + meta.json labels", "hcaptcha, hcaptcha_enterprise, classification_grid"
    ),
    "recaptcha-v1": ModelExpectation("safetensors head", "recaptcha_v2 grid, classification_grid"),
    "funcap-v1": ModelExpectation("safetensors head, 2x3 grid", "funcaptcha, funcaptcha_enterprise"),
    "awswaf-v1": ModelExpectation("safetensors head", "awswaf"),
    "shein-v1": ModelExpectation("safetensors head", "shein"),
    "hopscotch-v1": ModelExpectation(
        "bundle: DINOv3 ViT-B/16 detector + query head; release.json carries calibration/rejector",
        "hopscotch",
        layout="bundle",
        files=HOPSCOTCH_FILES,
    ),
}


def _weights_registry():
    from captcha.adapters.weights import Registry

    settings = get_settings()
    return Registry(
        s3_endpoint=settings.s3_endpoint,
        bucket=settings.s3_bucket,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


def cmd_publish_model(args: argparse.Namespace) -> int:
    import json

    labels: dict[str, str] = {}
    if args.labels:
        try:
            labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"cannot read labels file: {exc}", file=sys.stderr)
            return 2
        if not isinstance(labels, dict):
            print("labels file must contain a JSON object", file=sys.stderr)
            return 2

    registry = _weights_registry()
    try:
        meta = registry.publish(
            args.name,
            args.version,
            args.path,
            framework=args.framework,
            labels=labels,
            set_latest=not args.no_latest,
        )
    except Exception as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return 1
    print(f"{meta.name}@{meta.version}  {meta.size_bytes} bytes  sha256={meta.sha256}")
    print(f"s3_key   {meta.s3_key}")
    if not args.no_latest:
        print("latest   ->", meta.version)
    return 0


def cmd_list_models(_args: argparse.Namespace) -> int:
    registry = _weights_registry()
    try:
        names = registry.list_models()
    except Exception as exc:
        print(f"cannot list models: {exc}", file=sys.stderr)
        return 1
    if not names:
        print("no models in the bucket — train one and run publish-model")
        return 0
    for name in names:
        try:
            versions = registry.list_versions(name)
        except Exception:
            versions = []
        print(f"{name}: {', '.join(versions) or '(no versions)'}")
    return 0


def _print_bundle_check(check: ArtifactCheck) -> None:
    for item in check.files:
        mark = "ok  " if item.ok else "FAIL"
        detail = item.error or item.note
        digest = item.actual_sha256[:12] if item.actual_sha256 else "-"
        line = f"  {mark} {item.name:<18} {item.size_bytes:>10} B  {digest:<12} {detail}"
        print(line.rstrip())
    if check.extra_files:
        print(f"  extra  {', '.join(check.extra_files)} (present, not part of the contract)")
    for warning in check.warnings:
        print(f"  note  {warning}")


def cmd_check_model(args: argparse.Namespace) -> int:
    from captcha.adapters.weights import is_bundle

    registry = _weights_registry()
    try:
        meta = registry.get_meta(args.name, args.version)
    except Exception as exc:
        print(f"meta not found: {exc}", file=sys.stderr)
        return 1
    print(f"{meta.name}@{meta.version}  framework={meta.framework}  {meta.size_bytes} bytes")
    print(f"sha256   {meta.sha256}")
    print(f"s3_key   {meta.s3_key}")
    if meta.labels:
        print(f"labels   {meta.labels}")

    if is_bundle(meta):
        # A bundle is only "there" if every file is: verify the whole directory,
        # not just the weights blob download() would return.
        try:
            check = registry.check_artifact(args.name, meta.version)
        except Exception as exc:
            print(f"bundle check failed: {exc}", file=sys.stderr)
            return 1
        artifact_id = (check.meta.artifact if check.meta else "") or args.name
        print(f"layout   bundle  artifact={artifact_id}")
        _print_bundle_check(check)
        if not check.ok:
            for failure in check.failures:
                print(f"check failed: {failure}", file=sys.stderr)
            return 1
        verified = sum(1 for item in check.files if item.ok)
        print(f"bundle   ok  files={verified}/{len(check.files)}")
    else:
        try:
            path = registry.download(args.name, args.version, force=True)
        except Exception as exc:
            print(f"download/verify failed: {exc}", file=sys.stderr)
            return 1
        print(f"verified {path}")

    if not args.load:
        return 0
    try:
        # load exactly the version that was just verified, not the 'latest' alias
        if meta.framework == "torch":
            registry.load_pytorch(args.name, meta.version)
        elif meta.framework == "safetensors":
            registry.load_safetensors(args.name, meta.version)
        elif meta.framework == "onnx":
            registry.load_onnx(args.name, meta.version)
        elif meta.framework == "hf":
            registry.load_hf(args.name, meta.version)
        else:
            print("framework 'raw': nothing to load")
            return 0
    except Exception as exc:
        print(f"load failed: {exc}", file=sys.stderr)
        return 1
    print("load     ok")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """End-to-end check against a live gateway: create one task, poll, print."""
    import json
    import time

    import httpx

    base = args.url.rstrip("/")
    payload = {**json.loads(args.payload), "type": args.type}
    deadline = time.monotonic() + args.timeout

    with httpx.Client(base_url=base, timeout=15.0) as client:
        if args.legacy:
            form = {"key": args.key, "method": _legacy_method(args.type), **json.loads(args.payload)}
            created = client.post("/in.php", data=form)
            if not created.text.startswith("OK|"):
                print(f"createTask failed: {created.text}", file=sys.stderr)
                return 1
            task_id = created.text.removeprefix("OK|")
            print(f"task {task_id}")
            while time.monotonic() < deadline:
                polled = client.post("/res.php", data={"key": args.key, "action": "get", "id": task_id})
                if polled.text == "CAPCHA_NOT_READY":
                    time.sleep(2.0)
                    continue
                print(f"legacy answer: {polled.text}")
                return 0 if polled.text.startswith("OK|") else 1
            print("timed out", file=sys.stderr)
            return 1

        created = client.post("/createTask", json={"clientKey": args.key, "task": payload})
        body = created.json()
        if body.get("errorId"):
            print(f"createTask refused: {body}", file=sys.stderr)
            return 1
        task_id = body["taskId"]
        print(f"task {task_id}")

        while time.monotonic() < deadline:
            polled = client.get("/getTaskResult", params={"clientKey": args.key, "taskId": task_id}).json()
            status = polled.get("status")
            if status == "processing":
                time.sleep(2.0)
                continue
            print(json.dumps(polled, indent=2, ensure_ascii=False))
            return 0 if status == "ready" else 1
    print("timed out", file=sys.stderr)
    return 1


_LEGACY_METHODS = {
    "text_captcha": "textcaptcha",
    "image_to_text": "base64",
    "friendly_captcha": "friendly",
    "altcha": "altcha",
    "hcaptcha": "hcaptcha",
    "turnstile": "turnstile",
    "recaptcha_v2": "userrecaptcha",
    "geetest_v3": "geetest",
}


def _legacy_method(captcha_type: str) -> str:
    return _LEGACY_METHODS.get(captcha_type, captcha_type)


def cmd_expected_models(_args: argparse.Namespace) -> int:
    for name, expectation in EXPECTED_MODELS.items():
        print(f"{name}")
        print(f"  format  {expectation.contract}")
        print(f"  serves  {expectation.serves}")
        print(f"  layout  {expectation.layout}")
        if expectation.files:
            print(f"  files   {', '.join(expectation.files)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="captcha-cli", description="captcha service operator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db", help="apply alembic migrations up to head").set_defaults(fn=cmd_init_db)

    ca = sub.add_parser("create-account", help="create an API account and print its key")
    ca.add_argument("--name", default="", help="human label for the account")
    ca.add_argument("--balance", default="10", help="starting balance (default 10)")
    ca.add_argument("--key", default="", help="explicit api key instead of a generated one")
    ca.set_defaults(fn=cmd_create_account)

    tp = sub.add_parser("topup", help="add balance to an existing account")
    tp.add_argument("--key", required=True)
    tp.add_argument("--amount", required=True)
    tp.set_defaults(fn=cmd_topup)

    sub.add_parser("list-accounts", help="print every account").set_defaults(fn=cmd_list_accounts)

    bal = sub.add_parser("balance", help="print one account balance")
    bal.add_argument("--key", required=True)
    bal.set_defaults(fn=cmd_balance)

    pm = sub.add_parser("publish-model", help="upload weights + meta.json and point 'latest' at them")
    pm.add_argument("name", help="registry name the solver asks for, e.g. rotate-v1")
    pm.add_argument("path", help="local weights file (.pt/.safetensors/.onnx/.bin)")
    pm.add_argument("--version", required=True, help="version string, e.g. v1 or 2026-11-15")
    pm.add_argument("--framework", default="torch", choices=("torch", "onnx", "hf", "safetensors", "raw"))
    pm.add_argument("--labels", default="", help="JSON file: {label: value} stored in meta.json")
    pm.add_argument("--no-latest", action="store_true", help="upload without moving the 'latest' pointer")
    pm.set_defaults(fn=cmd_publish_model)

    sub.add_parser("list-models", help="print every model and its versions").set_defaults(fn=cmd_list_models)

    cm = sub.add_parser("check-model", help="download a model, verify its sha256 and print its contract")
    cm.add_argument("name")
    cm.add_argument("--version", default="latest")
    cm.add_argument("--load", action="store_true", help="also load the weights with the matching framework loader")
    cm.set_defaults(fn=cmd_check_model)

    sub.add_parser("expected-models", help="artifact names the solvers look for").set_defaults(fn=cmd_expected_models)

    sm = sub.add_parser("smoke", help="create one task against a running gateway and wait for the answer")
    sm.add_argument("--url", default="http://localhost:8080", help="gateway base URL")
    sm.add_argument("--key", required=True, help="clientKey (see: create-account)")
    sm.add_argument("--type", default="friendly_captcha", help="captcha type, e.g. text_captcha")
    sm.add_argument("--payload", default="{}", help="extra task payload as JSON")
    sm.add_argument("--legacy", action="store_true", help="talk /in.php + /res.php instead of the JSON API")
    sm.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for a result")
    sm.set_defaults(fn=cmd_smoke)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
