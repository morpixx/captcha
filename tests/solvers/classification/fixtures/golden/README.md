# Grid golden manifest

Format contract for the five prompt-conditioned grid heads. It is consumed by
the ml-platform harness (captcha.services.pipeline.golden) and by
tests/solvers/classification/test_golden_grid.py; there are no weights here.

    {
      "artifact": "grid-heads",
      "version": "v1",
      "tolerance_px": 0,
      "min_accuracy": 0.95,
      "min_coverage": 0.80,
      "cases": [
        {
          "id": "hcaptcha-3x3-0001",
          "type": "classification_grid",
          "image": "images/hcaptcha-3x3-0001.png",
          "payload": {
            "head": "hcaptcha-v1",
            "prompt": "select all images with a traffic light",
            "rows": 3,
            "cols": 3
          },
          "expected": {"objects": [2, 5]},
          "tags": ["hcaptcha-v1", "3x3"]
        }
      ]
    }

Rules:

- type is the task type. Grid head work is always classification_grid; the
  head is selected by payload.head (one of hcaptcha-v1, recaptcha-v1,
  funcap-v1, awswaf-v1, shein-v1).
- payload.rows / payload.cols must be a shape the head contract allows: 3x3
  for hcaptcha/awswaf/shein, 3x3 or 4x4 for recaptcha, 2x3 for funcap. Omit
  them to exercise the head's published default.
- expected.objects is the 0-based row-major tile index list, exactly what
  domain/solution.py projects for classification_grid. Indices are exact, so
  tolerance_px must be 0 on every case: the harness compares number lists with
  pixel tolerance, and a tile index is not a pixel.
- image keys resolve through the harness loader. The committed images are
  synthetic 112 px tiles (cols*112 x rows*112), one highlighted tile per
  expected index; they validate the slicing path, not model accuracy. Replace
  them with a frozen real set when one exists — never with customer payloads.
- An empty objects list is a valid answer ("none of these tiles"). The solver
  has no argmax fallback; a case with no detection must stay empty.

Run it:

    python -m captcha.services.pipeline.cli --once golden \
      --manifest tests/solvers/classification/fixtures/golden/grid-v1.json \
      --predict <module>:predict \
      --loader <module>:load

The predictor receives the case payload with image_b64 attached and returns
{"objects": [...]}. Without published weights the solver raises ERR_CONFIG /
ERR_MODEL_INFERENCE; the harness counts that as no coverage, not as a guessed
correct answer.
