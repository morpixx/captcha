"""Slider solver: vendor payload aliases, offset correction, stable errors."""

from __future__ import annotations

import msgspec
import pytest
from _slider_support import bg_image, piece_image, png_b64, slider_context, task

from captcha.domain import CaptchaType
from captcha.domain.errors import UnsolvableError
from captcha.domain.solution import build_solution
from captcha.solvers.slider.slider import SliderSolver

BG = png_b64(bg_image())
PIECE = png_b64(piece_image())


async def test_solver_returns_offset_and_trajectory() -> None:
    result = await SliderSolver().solve_http(
        task(bg=BG, piece=PIECE, backend="numpy", seed=7),
        slider_context(),
    )

    assert result.offset == 100.0
    assert result.extra is not None
    assert len(result.extra["trajectory"]) == 42
    assert result.extra["backend"] == "numpy"
    assert result.extra["gap_x"] == 100

    solution = build_solution(CaptchaType.SLIDER, msgspec.structs.asdict(result))
    assert solution is not None
    assert solution["offset"] == 100.0


async def test_vendor_payload_aliases_are_accepted() -> None:
    result = await SliderSolver().solve_http(
        task(bg_image=BG, pieceImg=PIECE, backend="numpy"),
        slider_context(),
    )
    assert result.offset == 100.0


async def test_bg_offset_correction_and_negative_track_origin() -> None:
    shifted = await SliderSolver().solve_http(
        task(bg=BG, piece=PIECE, backend="numpy", bg_offset_x=-20),
        slider_context(),
    )
    assert shifted.offset == 120.0

    with pytest.raises(UnsolvableError, match="non-positive"):
        await SliderSolver().solve_http(
            task(bg=BG, piece=PIECE, backend="numpy", bg_offset_x=150),
            slider_context(),
        )


async def test_missing_images_and_bad_options_are_unsolvable() -> None:
    with pytest.raises(UnsolvableError, match="missing bg or piece"):
        await SliderSolver().solve_http(task(bg=BG), slider_context())

    with pytest.raises(UnsolvableError, match="piece_width"):
        await SliderSolver().solve_http(task(bg=BG, piece=PIECE, piece_width="wide"), slider_context())

    with pytest.raises(UnsolvableError, match="piece_y"):
        await SliderSolver().solve_http(task(bg=BG, piece=PIECE, piece_y="up"), slider_context())

    with pytest.raises(UnsolvableError, match="bg_offset_x"):
        await SliderSolver().solve_http(task(bg=BG, piece=PIECE, bg_offset_x="left"), slider_context())

    with pytest.raises(UnsolvableError, match="min_score"):
        await SliderSolver().solve_http(task(bg=BG, piece=PIECE, min_score=2), slider_context())

    with pytest.raises(UnsolvableError, match="trajectory_points"):
        await SliderSolver().solve_http(task(bg=BG, piece=PIECE, trajectory_points=1), slider_context())

    with pytest.raises(UnsolvableError, match="seed"):
        await SliderSolver().solve_http(task(bg=BG, piece=PIECE, seed="abc"), slider_context())


async def test_piece_width_hint_can_hide_the_gap() -> None:
    with pytest.raises(UnsolvableError, match="confidence too low"):
        await SliderSolver().solve_http(
            task(bg=BG, piece=PIECE, backend="numpy", piece_width=200),
            slider_context(),
        )


def test_slider_solver_covers_all_slider_types() -> None:
    assert set(SliderSolver.info.types) == {
        CaptchaType.SLIDER,
        CaptchaType.CAPY,
        CaptchaType.KEYCAPTCHA,
        CaptchaType.LEMIN,
        CaptchaType.DATADOME_SLIDER,
    }
