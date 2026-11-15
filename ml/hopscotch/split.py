"""Split allocation for the hopscotch kit.

Roles come from the upstream frozen manifest when one is available: that split
produced the 70.5% baseline, so re-rolling it would silently invalidate every
comparison. Without one, the roles are derived deterministically from
sha256(seed:wave) with whole-session exclusion of manual holdouts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .types import HOLDOUT, MIN_CALIBRATION, QUOTA, ROLES, Record, sha256_file


def digest_key(seed: int, wave: str) -> str:
    return hashlib.sha256(f"{seed}:{wave}".encode()).hexdigest()


def import_upstream_roles(path: Path) -> dict[str, str]:
    """Read role membership from a frozen hopscotch_v2 manifest."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("status") != "frozen":
        raise ValueError(f"{path.name}: upstream split is not frozen")
    roles: dict[str, str] = {}
    for role in ("gold_train", "gold_val", "honeypot_probe", "calibration", "excluded"):
        for wave in raw.get(role, []):
            roles[str(wave)] = role
    client_val = raw.get("client_val") or raw.get("blind_test") or []
    for wave in client_val:
        roles[str(wave)] = "client_val"
    return roles


def derive_roles(records: Sequence[Record], *, seed: int) -> dict[str, str]:
    """Deterministic session-safe split used when no upstream manifest exists."""
    manual = [r for r in records if r.label_source == "manual"]
    if len(manual) < sum(QUOTA[r] for r in ("gold_train", "gold_val", "honeypot_probe")):
        raise ValueError("not enough manual waves for the 110/30/10 protocol")
    ordered = sorted(manual, key=lambda record: digest_key(seed, record.wave))
    roles: dict[str, str] = {}
    cursor = 0
    for role in ("gold_train", "gold_val", "honeypot_probe"):
        for record in ordered[cursor : cursor + QUOTA[role]]:
            roles[record.wave] = role
        cursor += QUOTA[role]

    held_sessions = {r.session for r in manual if roles.get(r.wave) in ("gold_val", "honeypot_probe")}
    pool = [
        r
        for r in records
        if r.label_source == "client" and r.session not in held_sessions
    ]
    excluded = [r.wave for r in records if r.label_source == "client" and r.session in held_sessions]
    if len(pool) < QUOTA["client_val"] + MIN_CALIBRATION:
        raise ValueError("not enough client waves for calibration + client validation")
    ordered = sorted(pool, key=lambda record: digest_key(seed, record.wave))
    for record in ordered[:MIN_CALIBRATION + 200]:
        roles[record.wave] = "calibration"
    for record in ordered[MIN_CALIBRATION + 200 : MIN_CALIBRATION + 200 + QUOTA["client_val"]]:
        roles[record.wave] = "client_val"
    for wave in excluded:
        roles.setdefault(wave, "excluded")
    return roles


def apply_roles(records: Sequence[Record], roles: Mapping[str, str]) -> list[Record]:
    out: list[Record] = []
    for record in records:
        role = roles.get(record.wave, "silver_train")
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r} for {record.wave}")
        out.append(
            Record(
                wave=record.wave,
                session=record.session,
                wave_number=record.wave_number,
                frames=record.frames,
                answer=record.answer,
                image=record.image,
                role=role,
                label_source=record.label_source,
            )
        )
    return out


def resolve_roles(
    records: Sequence[Record], *, seed: int, upstream: Path | None
) -> tuple[dict[str, str], dict[str, Any]]:
    if upstream is None:
        return derive_roles(records, seed=seed), {"roles_imported": False}
    roles = import_upstream_roles(upstream)
    known = {record.wave for record in records}
    missing = sorted(set(roles) - known)
    if missing:
        raise ValueError(f"upstream split names unknown waves, e.g. {missing[:3]}")
    payload = {
        "path": str(upstream),
        "sha256": sha256_file(upstream),
        "roles_imported": True,
    }
    return roles, payload


def counts(records: Iterable[Record]) -> dict[str, int]:
    result = dict.fromkeys(ROLES, 0)
    for record in records:
        result[record.role] += 1
    return result


def check_counts(role_counts: Mapping[str, int]) -> None:
    for role, expected in QUOTA.items():
        if role == "client_val" and role_counts["gold_val"] == 0:
            continue
        if role_counts.get(role, 0) != expected:
            raise ValueError(f"split has {role_counts.get(role, 0)} {role}, protocol wants {expected}")
    if role_counts.get("calibration", 0) < MIN_CALIBRATION:
        raise ValueError("split has too few calibration waves")


__all__ = [
    "HOLDOUT",
    "MIN_CALIBRATION",
    "QUOTA",
    "ROLES",
    "apply_roles",
    "check_counts",
    "counts",
    "derive_roles",
    "digest_key",
    "import_upstream_roles",
    "resolve_roles",
]
