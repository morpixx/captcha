from __future__ import annotations

import json

import pytest
from tools.ml_probe.metrics import _wilson, evaluate, load_jsonl, main, percentile


def _write(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_grid_exact_and_partial():
    golden = [
        {"id": "a", "task": "grid", "expected": [0, 2]},
        {"id": "b", "task": "grid", "expected": [4]},
    ]
    preds = [{"id": "a", "predicted": [2, 0]}, {"id": "b", "predicted": [5]}]
    out = evaluate(golden, preds)
    grid = out["tasks"]["grid"]
    assert grid["exact_match"] == 0.5
    # micro over two records: tp=2, fp=1, fn=1
    assert grid["precision"] == pytest.approx(2 / 3)
    assert grid["recall"] == pytest.approx(2 / 3)
    assert grid["exact_match_ci"][0] < 0.5 < grid["exact_match_ci"][1]


def test_click_tolerance_and_false_positive():
    golden = [{"id": "a", "task": "click", "image_size": [1000, 600], "expected": [[100, 200]]}]
    near = evaluate(golden, [{"id": "a", "predicted": [[105, 204]]}])["tasks"]["click"]
    far = evaluate(golden, [{"id": "a", "predicted": [[400, 500]]}])["tasks"]["click"]
    assert near["hit_rate"] == 1.0
    assert far["hit_rate"] == 0.0
    assert far["recall"] == 0.0


def test_hopscotch_index_and_coordinate():
    golden = [
        {"id": "a", "task": "hopscotch", "image_size": [1600, 1000], "expected": {"index": 3, "point": [800, 400]}}
    ]
    good = evaluate(golden, [{"id": "a", "predicted": {"index": 3, "point": [810, 395]}}])
    wrong_index = evaluate(golden, [{"id": "a", "predicted": {"index": 4, "point": [810, 395]}}])
    assert good["tasks"]["hopscotch"]["joint_rate"] == 1.0
    assert wrong_index["tasks"]["hopscotch"]["joint_rate"] == 0.0
    assert wrong_index["tasks"]["hopscotch"]["coord_accuracy"] == 1.0


def test_abstain_counts_as_coverage_not_accuracy():
    golden = [{"id": "a", "task": "grid", "expected": [1]}]
    out = evaluate(golden, [{"id": "a", "abstain": True}])
    assert out["tasks"]["_all"]["abstained"] == 1
    assert out["tasks"]["_all"]["coverage"] == 0.0


def test_id_mismatch_is_an_error():
    with pytest.raises(ValueError, match="id mismatch"):
        evaluate([{"id": "a", "task": "grid", "expected": [1]}], [])


def test_percentile_and_wilson():
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    assert percentile([], 0.95) is None
    lo, hi = _wilson(0, 10)
    assert lo == 0.0 and 0.0 < hi < 0.4


def test_cli_fail_under(tmp_path):
    golden = _write(tmp_path, "g.jsonl", [{"id": "a", "task": "grid", "expected": [0]}])
    preds = _write(tmp_path, "p.jsonl", [{"id": "a", "predicted": [1]}])
    assert main([str(golden), str(preds), "--fail-under", "grid.exact_match=0.5"]) == 1
    assert main([str(golden), str(preds), "--json"]) == 0


def test_duplicate_ids_are_an_error(tmp_path):
    path = _write(
        tmp_path,
        "g.jsonl",
        [{"id": "a", "task": "grid", "expected": [0]}, {"id": "a", "task": "grid", "expected": [1]}],
    )
    with pytest.raises(ValueError, match="duplicate id"):
        evaluate(load_jsonl(path), load_jsonl(path))
