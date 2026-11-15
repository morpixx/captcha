"""Training kit, step 1: turn the two read-only sources into a frozen split.

Inputs (never written):
    D:/hopscotch_v2/dataset/raw/index.jsonl         client answers, 1-based cards
    D:/hopscotch_v2/dataset/annotations.jsonl       manual V2 labels, 0-based cards
Optional input, imported verbatim so we do not re-roll a split that produced the
70.5% baseline:
    D:/hopscotch_v2/dataset/splits/development-v4.json

Output: one manifest with source hashes, image hashes, per-wave role and answer.
The manifest is frozen: train.py refuses to run when either source hash moved,
because a model trained on a different split cannot be compared with the baseline.

The V2 annotation validator below is a port of hopscotch_v2 src/data/labels.py
(validate_full). It is duplicated on purpose: the kit must be able to reject a
corrupt label without importing torch, cv2 or the training checkout.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .split import apply_roles, check_counts, counts, resolve_roles
from .types import ROLES, Dataset, Record, sha256_file

FORMAT = "captcha-hopscotch-dataset-v1"
PROTOCOL = "hopscotch-gold110-30-10-client-development-v1"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} line {number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} line {number}: expected an object")
        rows.append(row)
    return rows


def validate_annotation(annotation: Mapping[str, Any]) -> None:
    """Structural V2 check: one hint identity, one answer, consistent nodes."""
    wave = annotation.get("wave")
    if annotation.get("v") != 2 or not isinstance(wave, str):
        raise ValueError(f"invalid V2 identity: {wave!r}")
    if annotation.get("flags"):
        raise ValueError(f"flagged annotation cannot be supervised: {wave}")
    items = annotation.get("frames")
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError(f"invalid frame count: {wave}")
    keys = set(items[0].get("nodes", {}))
    if not 1 <= len(keys) <= 8:
        raise ValueError(f"unsupported identity capacity: {wave}")
    for index, frame in enumerate(items):
        nodes = frame.get("nodes")
        figure = frame.get("fig")
        if not isinstance(nodes, dict) or set(nodes) != keys or not isinstance(figure, dict):
            raise ValueError(f"inconsistent frame identities: {wave}")
        if figure.get("on") not in keys:
            raise ValueError(f"figure identity is not a node: {wave}")
        if figure.get("foot") is not None:
            _point(figure["foot"], index, wave)
        for node in nodes.values():
            if node.get("vis") not in (0, 1, 2):
                raise ValueError(f"invalid visibility: {wave}")
            if node.get("pt") is not None:
                _point(node["pt"], index, wave)
            radius = node.get("r")
            if radius is not None and (type(radius) is not int or not 1 <= radius <= 100):
                raise ValueError(f"invalid radius: {wave}")
    answer = annotation.get("answer")
    if type(answer) is not int or not 0 <= answer < len(items):
        raise ValueError(f"answer out of range: {wave}")
    hint = annotation.get("hint")
    if not isinstance(hint, Mapping) or hint.get("key") not in keys:
        raise ValueError(f"hint identity is not a node: {wave}")
    matches = [i for i, frame in enumerate(items) if frame["fig"]["on"] == hint["key"]]
    if matches != [answer]:
        raise ValueError(f"answer inconsistent with manual structure: {wave}")


def _point(value: Any, frame: int, wave: str) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(type(v) is not int for v in value)
        or not 200 * frame <= value[0] < 200 * (frame + 1)
        or not 0 <= value[1] < 200
    ):
        raise ValueError(f"invalid global coordinate in {wave}")


def merge_sources(
    client_rows: Iterable[Mapping[str, Any]],
    annotations: Iterable[Mapping[str, Any]],
) -> list[Record]:
    """One record per wave; manual labels win, client answers are 1-based."""
    merged: dict[str, Record] = {}
    for row in client_rows:
        session, number, frames = row.get("session_id"), row.get("wave"), row.get("difficulty")
        answer = row.get("submitted_index")
        if not isinstance(session, str) or type(number) is not int or number < 0:
            raise ValueError("invalid client wave identity")
        if type(frames) is not int or not 3 <= frames <= 8:
            raise ValueError(f"invalid card count for {session}:wave_{number}")
        if type(answer) is not int or not 1 <= answer < frames + 1:
            raise ValueError(f"client answer must be 1-based: {session}:wave_{number}")
        wave = f"{session}:wave_{number}"
        merged[wave] = Record(
            wave=wave,
            session=session,
            wave_number=number,
            frames=frames,
            answer=answer - 1,
            image=str(row.get("image") or f"sessions/{session}/wave_{number}.jpg"),
            role="silver_train",
            label_source="client",
        )

    for annotation in annotations:
        validate_annotation(annotation)
        wave = str(annotation["wave"])
        session, _, number = wave.partition(":wave_")
        if not session or not number.isdigit():
            raise ValueError(f"cannot derive image path for manual wave: {wave}")
        frames = len(annotation["frames"])
        existing = merged.get(wave)
        if existing is not None and existing.frames != frames:
            raise ValueError(f"manual/client card counts differ: {wave}")
        merged[wave] = Record(
            wave=wave,
            session=session,
            wave_number=int(number),
            frames=frames,
            answer=int(annotation["answer"]),
            image=existing.image if existing else f"sessions/{session}/wave_{number}.jpg",
            role="silver_train",
            label_source="manual",
        )
    return sorted(merged.values(), key=lambda record: record.wave)



# ---- manifest ---------------------------------------------------------------


def load_dataset(
    root: str | Path,
    *,
    upstream: str | Path | None = None,
    seed: int = 42,
    hash_images: bool = True,
) -> Dataset:
    """Read the sources, allocate roles, return the dataset and its manifest."""
    base = Path(root)
    raw_index = base / "dataset" / "raw" / "index.jsonl"
    annotations_path = base / "dataset" / "annotations.jsonl"
    for path in (raw_index, annotations_path):
        if not path.is_file():
            raise FileNotFoundError(f"missing dataset source: {path}")

    upstream_path = Path(upstream) if upstream is not None else _default_upstream(base)
    raw_rows = read_jsonl(raw_index)
    annotations = read_jsonl(annotations_path)
    merged = merge_sources(raw_rows, annotations)
    roles, upstream_meta = resolve_roles(
        merged,
        seed=seed,
        upstream=upstream_path if upstream_path and upstream_path.is_file() else None,
    )
    records = apply_roles(merged, roles)
    role_counts = counts(records)
    check_counts(role_counts)

    image_hashes: dict[str, str] = {}
    if hash_images:
        for record in records:
            image_path = base / "dataset" / "raw" / record.image
            if not image_path.is_file():
                raise FileNotFoundError(f"missing image for {record.wave}: {image_path}")
            image_hashes[record.wave] = sha256_file(image_path)

    raw_sha = sha256_file(raw_index)
    annotations_sha = sha256_file(annotations_path)
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "protocol": PROTOCOL,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": seed,
        "client_index_base": 1,
        "manual_index_base": 0,
        "source": {
            "raw_index": {"path": str(raw_index), "sha256": raw_sha, "rows": len(raw_rows)},
            "annotations": {
                "path": str(annotations_path),
                "sha256": annotations_sha,
                "rows": len(annotations),
            },
        },
        "upstream": upstream_meta,
        "counts": role_counts,
        "roles": {role: [r.wave for r in records if r.role == role] for role in ROLES},
        "image_hashes": image_hashes,
        "records": [_record_json(record) for record in records],
    }
    return Dataset(
        records=tuple(records),
        manifest=manifest,
        raw_sha256=raw_sha,
        annotations_sha256=annotations_sha,
    )


def write_manifest(dataset: Dataset, path: str | Path, *, force: bool = False) -> Path:
    target = Path(path)
    if target.exists() and not force:
        raise FileExistsError(f"frozen split already exists: {target} (use --force to replace)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def verify_sources(manifest_path: str | Path, root: str | Path) -> dict[str, Any]:
    """Fail when either source moved since the split was frozen."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    base = Path(root)
    expected = {
        "raw_index": base / "dataset" / "raw" / "index.jsonl",
        "annotations": base / "dataset" / "annotations.jsonl",
    }
    for key, path in expected.items():
        actual = sha256_file(path)
        wanted = manifest["source"][key]["sha256"]
        if actual != wanted:
            raise ValueError(
                f"{path.name} changed since the split was frozen "
                f"(expected {wanted[:12]}, got {actual[:12]}); re-export the split"
            )
    return manifest


def _record_json(record: Record) -> dict[str, Any]:
    return {
        "wave": record.wave,
        "session": record.session,
        "wave_number": record.wave_number,
        "frames": record.frames,
        "answer": record.answer,
        "image": record.image,
        "role": record.role,
        "label_source": record.label_source,
    }


def _default_upstream(base: Path) -> Path | None:
    splits = base / "dataset" / "splits"
    for name in ("development-v4.json", "manifest.json"):
        candidate = splits / name
        if candidate.is_file():
            return candidate
    return None


__all__ = [
    "FORMAT",
    "PROTOCOL",
    "Dataset",
    "Record",
    "load_dataset",
    "merge_sources",
    "read_jsonl",
    "sha256_file",
    "validate_annotation",
    "verify_sources",
    "write_manifest",
]
