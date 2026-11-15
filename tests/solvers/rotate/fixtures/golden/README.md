# rotate-v1 golden fixtures

No weights are committed. The test predictor installs a fake sin/cos regressor
that returns the red channel of the fixture image as the angle, so the manifest
verifies image decoding, regressor path selection and the angle projection.
The expected angles are 30 and 200 degrees, matched exactly (tolerance_px is 0).

Real acceptance requires rotate-v1 in the model registry and the golden set from
docs/ml-tz.md; this manifest only guards the plumbing and the solution shape.
