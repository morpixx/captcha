# captcha-solver-rotate

Returns a rotation angle (degrees, [0, 360)) to align a rotatable image back to
upright. Used for Tencent / Yandex / Alibaba / Temu style rotate challenges.

## Paths

- **Classical** (default): Hough-line orientation + PCA on edge pixels.
  Works for challenges that carry obvious horizontal/vertical structure
  (buildings, text banners, straight-cropped photos). Zero model deps.
- **ML regressor**: `AngleRegressor` wraps a pretrained ResNet50 whose final
  layer is a 1-logit head mapped onto [0, 360). Loaded lazily from
  `ctx.models` under the name `rotate-v1`. When the model is absent the solver
  falls back to classical automatically; when it is present but inference
  fails, the solver raises `ModelInferenceError`.

## Payload

```json
{
  "image": "<base64 PNG/JPG>",
  "prefer": "classical" | "ml" | null
}
```

`prefer` is optional — default picks `ml` when the regressor is loaded,
classical otherwise.

## Result

`SolverResult(angle=<float 0..360>, extra={"path": "classical" | "ml", "score": ...})`
