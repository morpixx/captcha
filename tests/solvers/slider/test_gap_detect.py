"""Classical slider matching: numpy fallback, cv2 opt-in, stable errors."""

from __future__ import annotations

from typing import Any

import pytest
from _slider_support import bg_image, piece_image, png_b64, solid_piece_b64

from captcha.domain.errors import ConfigError, UnsolvableError
from captcha.solvers.slider import gap_detect
from captcha.solvers.slider.gap_detect import decode_image, detect_gap


@pytest.mark.parametrize(
    ("width", "height", "gap_x", "gap_y"),
    ((240, 120, 100, 40), (260, 120, 160, 50), (200, 100, 80, 30)),
)
def test_numpy_matcher_finds_the_gap(width: int, height: int, gap_x: int, gap_y: int) -> None:
    debug: dict[str, Any] = {}
    offset = detect_gap(
        png_b64(bg_image(width, height, gap_x, gap_y)),
        png_b64(piece_image()),
        backend="numpy",
        debug=debug,
    )

    assert offset == gap_x
    assert debug["backend"] == "numpy"
    assert debug["match_xy"] == (gap_x, gap_y)
    assert debug["score"] > 0.5


def test_auto_backend_works_without_extras() -> None:
    offset = detect_gap(
        png_b64(bg_image(gap_x=120, gap_y=35)),
        png_b64(piece_image()),
        backend="auto",
    )
    assert offset == 120


def test_explicit_cv2_backend_without_opencv_is_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*, required: bool = False) -> Any:
        if required:
            raise ConfigError("opencv required for the slider cv2 backend", context={})
        return None

    monkeypatch.setattr(gap_detect, "_import_cv2", missing)
    with pytest.raises(ConfigError, match="opencv"):
        detect_gap(
            png_b64(bg_image()),
            png_b64(piece_image()),
            backend="cv2",
        )


def test_unknown_backend_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="backend"):
        detect_gap(png_b64(bg_image()), png_b64(piece_image()), backend="magic")


def test_min_score_must_be_a_ratio() -> None:
    with pytest.raises(UnsolvableError, match="min_score"):
        detect_gap(png_b64(bg_image()), png_b64(piece_image()), backend="numpy", min_score=2.0)


def test_low_match_score_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="confidence too low"):
        detect_gap(
            png_b64(bg_image()),
            png_b64(piece_image()),
            backend="numpy",
            min_score=0.99,
        )


def test_search_window_can_exclude_the_real_gap() -> None:
    with pytest.raises(UnsolvableError, match="confidence too low"):
        detect_gap(
            png_b64(bg_image(gap_x=100)),
            png_b64(piece_image()),
            backend="numpy",
            search_x_min=150,
            min_score=0.5,
        )


def test_piece_larger_than_background_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="piece larger"):
        detect_gap(
            png_b64(bg_image(width=40, height=40, gap_x=0, gap_y=0)),
            png_b64(piece_image()),
            backend="numpy",
        )


def test_empty_piece_mask_is_unsolvable() -> None:
    from PIL import Image

    transparent = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    with pytest.raises(UnsolvableError, match="mask is empty"):
        detect_gap(png_b64(bg_image()), png_b64(transparent), backend="numpy")


def test_flat_piece_template_is_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="flat"):
        detect_gap(png_b64(bg_image()), solid_piece_b64(), backend="numpy")


@pytest.mark.parametrize("bad", ("", "!!!not-base64!!!", "aGVsbG8="))
def test_decode_failures_are_unsolvable(bad: str) -> None:
    with pytest.raises(UnsolvableError):
        decode_image(bad)


def test_decode_image_keeps_alpha_only_when_asked() -> None:
    rgb = decode_image(png_b64(bg_image()))
    rgba = decode_image(png_b64(piece_image()), with_alpha=True)
    assert rgb.shape == (120, 240, 3)
    assert rgba.shape == (50, 50, 4)
