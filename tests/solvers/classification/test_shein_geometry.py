from __future__ import annotations

from captcha.solvers.classification.shein import tile_centres


def test_tile_centres_3x3() -> None:
    coords = tile_centres(300, 300, rows=3, cols=3, indices=[0, 4, 8])

    assert coords == [(50, 50), (150, 150), (250, 250)]


def test_tile_centres_skips_out_of_range() -> None:
    assert tile_centres(300, 300, rows=3, cols=3, indices=[-1, 9]) == []


def test_tile_centres_rectangular_grid() -> None:
    assert tile_centres(400, 200, rows=2, cols=4, indices=[0, 3, 7]) == [
        (50, 50),
        (350, 50),
        (350, 150),
    ]
