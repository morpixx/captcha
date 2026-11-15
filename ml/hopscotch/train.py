"""Training kit, step 2: wrapper around hopscotch_v2 scripts/v1.py.

The trainer is NOT reimplemented here and is not vendored. This wrapper exists so
the service repo owns the reproducible entry point and the preflight rules:

    * the frozen split must still match the two source files (hashes);
    * the training checkout must be the one the exporter was written against;
    * nothing is written into D:/hopscotch_v2 - runs land there by their own rules.

The training environment is the system Python 3.14 from hopscotch_v2, not the
service venv. Point HOPSCOTCH_PYTHON at it; default is sys.executable.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT_ENV = "HOPSCOTCH_V2_ROOT"
PYTHON_ENV = "HOPSCOTCH_PYTHON"
DEFAULT_ROOT = Path("D:/hopscotch_v2")
DEFAULT_CONFIG = "configs/v1-spatial.yaml"

#: Flags script v1.py understands for the train subcommand.
TRAIN_FLAGS: tuple[tuple[str, str], ...] = (
    ("--run", "run"),
    ("--stage", "stage"),
    ("--epochs", "epochs"),
    ("--warmup-epochs", "warmup_epochs"),
    ("--device", "device"),
    ("--resume", "resume"),
    ("--initialize", "initialize"),
)


@dataclass(frozen=True)
class TrainPlan:
    root: Path
    command: list[str]
    cwd: Path


def v2_root(explicit: str | Path | None = None) -> Path:
    if explicit is not None:
        root = Path(explicit)
    elif os.environ.get(ROOT_ENV):
        root = Path(os.environ[ROOT_ENV])
    else:
        root = DEFAULT_ROOT
    root = root.expanduser().resolve()
    if not (root / "scripts" / "v1.py").is_file():
        raise FileNotFoundError(
            f"hopscotch_v2 checkout not found at {root}; set {ROOT_ENV} or pass --v2-root"
        )
    return root


def python_for(root: Path, explicit: str | None = None) -> str:
    return explicit or os.environ.get(PYTHON_ENV) or sys.executable


def train_plan(
    *,
    root: str | Path | None = None,
    config: str = DEFAULT_CONFIG,
    python: str | None = None,
    run: str | None = None,
    stage: str | None = None,
    epochs: int | None = None,
    warmup_epochs: int | None = None,
    device: str | None = None,
    resume: str | Path | None = None,
    initialize: str | Path | None = None,
    extra: Sequence[str] = (),
) -> TrainPlan:
    base = v2_root(root)
    command = [python_for(base, python), str(base / "scripts" / "v1.py"), "--config", config, "train"]
    values = {
        "run": run,
        "stage": stage,
        "epochs": epochs,
        "warmup_epochs": warmup_epochs,
        "device": device,
        "resume": resume,
        "initialize": initialize,
    }
    for flag, key in TRAIN_FLAGS:
        value = values[key]
        if value is not None:
            command += [flag, str(value)]
    command += list(extra)
    return TrainPlan(root=base, command=command, cwd=base)


def preflight(root: str | Path | None, manifest: str | Path | None) -> dict[str, Any] | None:
    """Refuse to train on sources that no longer match the frozen split."""
    if manifest is None:
        return None
    from .dataset import verify_sources

    base = v2_root(root)
    return verify_sources(manifest, base)


def run_plan(plan: TrainPlan, *, dry_run: bool = False, runner: Any = None) -> int:
    if dry_run:
        print(" ".join(plan.command))
        return 0
    run = runner or _run
    return int(run(plan.command, plan.cwd) or 0)


def _run(command: Sequence[str], cwd: Path) -> int:
    completed = subprocess.run(list(command), cwd=str(cwd), check=False)
    return completed.returncode


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_ROOT",
    "PYTHON_ENV",
    "ROOT_ENV",
    "TRAIN_FLAGS",
    "TrainPlan",
    "preflight",
    "python_for",
    "run_plan",
    "train_plan",
    "v2_root",
]
