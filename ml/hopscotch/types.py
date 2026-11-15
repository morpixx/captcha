"""Shared kit types: one wave record, the frozen dataset, quota constants."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLES: tuple[str, ...] = (
    "gold_train",
    "gold_val",
    "honeypot_probe",
    "calibration",
    "client_val",
    "silver_train",
    "excluded",
)
HOLDOUT = frozenset({"gold_val", "honeypot_probe", "calibration", "client_val", "excluded"})

#: The protocol numbers every downstream step assumes. A split that cannot meet
#: them is rejected at export time, not discovered after a 90-epoch run.
QUOTA = {"gold_train": 110, "gold_val": 30, "honeypot_probe": 10, "client_val": 400}
MIN_CALIBRATION = 20


@dataclass(frozen=True)
class Record:
    wave: str
    session: str
    wave_number: int
    frames: int
    answer: int  # 0-based, client-facing numbers are 1-based
    image: str
    role: str
    label_source: str


@dataclass(frozen=True)
class Dataset:
    records: tuple[Record, ...]
    manifest: Mapping[str, Any]
    raw_sha256: str
    annotations_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = [
    "HOLDOUT",
    "MIN_CALIBRATION",
    "QUOTA",
    "ROLES",
    "Dataset",
    "Record",
    "sha256_file",
]
