# ml_probe

Offline metrics for visual captcha prototypes. Two JSONL files in, one report out.
Stdlib only — no torch, no network, safe for CI and for machines that never install
the cv extra.

## Why

Every accuracy claim in `docs/ml-research.md` and `docs/adr-vision-architecture.md`
has to be reproducible. This is the smallest harness that makes it so: the model
writes predictions, this script computes the numbers, the golden set stays frozen.

## Schema

One JSON object per line. Golden and predictions are joined by `id`; missing or
extra ids are an error (never silently score a subset).

### Golden

```json
{"id": "g1", "task": "grid", "image_size": [300, 300], "expected": [0, 2, 5]}
{"id": "c1", "task": "click", "image_size": [1000, 600], "expected": [[100, 200], [500, 300]]}
{"id": "h1", "task": "hopscotch", "image_size": [1600, 1000], "expected": {"index": 3, "point": [800, 400]}}
```

### Predictions

```json
{"id": "g1", "predicted": [0, 2], "latency_ms": 42}
{"id": "c1", "predicted": [[102, 198]], "latency_ms": 55}
{"id": "h1", "predicted": {"index": 3, "point": [810, 395]}, "latency_ms": 210}
{"id": "g2", "abstain": true, "latency_ms": 12}
```

- `image_size` is `[width, height]`; required for `click` and `hopscotch`.
  It may live in either file, golden wins.
- `abstain: true` excludes the record from accuracy and counts it in coverage.
- `latency_ms` is optional and measured outside this script. Report inference-only
  latency (preprocessing separately) or the numbers stop being comparable.

## Metrics

| task | primary | secondary |
|---|---|---|
| grid | `exact_match` — predicted set equals expected set | tile precision/recall/F1, mean per-record F1 |
| click | `hit_rate` — every expected point matched, no false positives | point precision/recall/F1, mean error of matched points |
| hopscotch | `joint_rate` — index and coordinate both correct | `index_accuracy`, `coord_accuracy`, mean error |

Point matching is greedy nearest-neighbour, one-to-one, with `tol` as a fraction of
the image diagonal (default 0.02 = 2%). A prediction is a hit if it is within
`tol × diagonal` pixels. Greedy is deliberate: it is deterministic and cheap; for
crowded scenes use Hungarian matching in the training code and keep this script as
the public, reproducible number.

Every proportion is reported with a 95% Wilson interval. `n=300` is roughly ±5 p.p.;
a +3 p.p. win without a CI is noise.

Latency is reported as p50/p95/p99/mean over answered records, per task.

## Usage

```bash
D:/captcha/.venv/Scripts/python.exe tools/ml_probe/metrics.py \
    tools/ml_probe/sample/golden.jsonl \
    tools/ml_probe/sample/predictions.jsonl

# machine-readable
... --json

# CI gate: fail if grid exact match is below 0.85
... --fail-under grid.exact_match=0.85 --fail-under hopscotch.joint_rate=0.80
```

Or as a module, from the repo root:

```bash
python -m tools.ml_probe.metrics --help
```

## What it does not do

- No image loading, no model inference, no dataset download.
- No significance testing beyond the Wilson interval; paired bootstrap belongs in
  the experiment notebook.
- No per-class breakdown. For grid per-prompt AP and detector per-class recall,
  export a separate `prompt`/`class` field and slice the JSONL — deliberately not
  in this file to keep it under the size cap.
