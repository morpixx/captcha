"""Payload parsing for the custom hopscotch type.

Contract (docs/production-contract.md section 6):

    {"image_b64": str, "frames"?: int, "hint"?: str} -> {"index", "coordinates"}

image_b64 is the whole wave strip (1000x400 for 5 cards, 1600x400 for 8) and is
capped at 32 MiB decoded. frames is optional: the width already implies it, so a
mismatch is a client bug, not something to paper over. hint is advisory only -
the hint panel is part of the image, so a missing or stale hint string cannot
change the prediction and must not fail the task.
"""

from __future__ import annotations

import base64
import binascii
import io
from dataclasses import dataclass
from typing import Any

from PIL import Image

from captcha.domain.errors import UnsolvableError

from .artifact import InputGeometry

#: Contract cap on the decoded image.
MAX_IMAGE_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Wave:
    image: Image.Image
    data: bytes
    frames: int
    hint: str | None = None

    @property
    def width(self) -> int:
        return self.image.size[0]

    @property
    def height(self) -> int:
        return self.image.size[1]


def parse_payload(payload: dict[str, Any] | None, geometry: InputGeometry) -> Wave:
    """Decode and validate one task payload. Raises UnsolvableError on bad input."""
    raw = payload or {}
    encoded = raw.get("image_b64") or raw.get("image")
    if not isinstance(encoded, str) or not encoded:
        raise UnsolvableError("payload missing image_b64", context={"type": "hopscotch"})

    data = decode_image_bytes(encoded)
    try:
        image = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # Pillow raises a zoo of exception types on bad data
        raise UnsolvableError("image decode failed", context={"error": str(exc)}) from exc

    frames = _frames(raw.get("frames"), geometry)
    width, height = image.size
    if height != geometry.image_height:
        raise UnsolvableError(
            "image height does not match the artifact",
            context={"expected": geometry.image_height, "got": height},
        )
    if width % geometry.card_width:
        raise UnsolvableError(
            "image width is not a whole number of cards",
            context={"width": width, "card_width": geometry.card_width},
        )
    implied = width // geometry.card_width
    if implied not in geometry.frames:
        raise UnsolvableError(
            "unsupported card count",
            context={"frames": implied, "supported": list(geometry.frames)},
        )
    if frames is not None and frames != implied:
        raise UnsolvableError(
            "payload frames disagrees with the image",
            context={"frames": frames, "implied": implied},
        )
    return Wave(image=image, data=data, frames=implied, hint=_hint(raw.get("hint")))


def decode_image_bytes(encoded: str) -> bytes:
    """Base64 (data URI tolerated) -> bytes, capped at 32 MiB."""
    if encoded.startswith("data:"):
        _, _, encoded = encoded.partition(",")
    try:
        data = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise UnsolvableError("image is not valid base64", context={"error": str(exc)}) from exc
    if not data:
        raise UnsolvableError("image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise UnsolvableError(
            "image exceeds the 32 MiB payload cap",
            context={"bytes": len(data), "limit": MAX_IMAGE_BYTES},
        )
    return data


def foot_to_pixels(
    foot: tuple[float, float] | None,
    index: int,
    geometry: InputGeometry,
    *,
    width: int,
    height: int,
) -> tuple[float, float]:
    """Card-local normalized foot -> global pixel point.

    The network predicts feet normalized inside their own card (see hopscotch_v2
    data/collate.py), so the card offset has to be added back before a client can
    click anything. Missing feet fall back to the card center.
    """
    if foot is None:
        fx, fy = 0.5, 0.5
    else:
        fx, fy = float(foot[0]), float(foot[1])
    x = (index + fx) * geometry.card_width
    y = fy * geometry.card_height
    return (min(max(x, 0.0), float(width - 1)), min(max(y, 0.0), float(height - 1)))


def _frames(raw: Any, geometry: InputGeometry) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        raise UnsolvableError("payload frames must be an integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise UnsolvableError("payload frames must be an integer", context={"frames": raw}) from exc
    if value not in geometry.frames:
        raise UnsolvableError(
            "unsupported card count",
            context={"frames": value, "supported": list(geometry.frames)},
        )
    return value


def _hint(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text[:64] or None


__all__ = ["MAX_IMAGE_BYTES", "Wave", "decode_image_bytes", "foot_to_pixels", "parse_payload"]
