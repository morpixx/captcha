# captcha-solver-detection

Four solvers over a shared YOLOv8 backend:

| Entry point       | Class             | Result        | Use for                         |
|-------------------|-------------------|---------------|---------------------------------|
| `click`           | `ClickSolver`     | `coords=[xy]` | "click the owl" / 2captcha v4   |
| `coordinates`     | `ClickSolver`     | `coords=[xy]` | alias of click                  |
| `bounding_box`    | `BBoxSolver`      | `bbox=(x,y,w,h)` | "put a box around the dog"   |
| `draw_around`     | `DrawAroundSolver`| `coords=[...]`| polygon approx around object    |
| `drag_drop`       | `DragDropSolver`  | `coords=[src,dst]` + trajectory | drag piece onto slot |

## Model loading

`ctx.models` must expose the model under the name requested in the payload
(`model`, defaulting to `click-v1` / `bbox-v1` / etc). The YOLO file is
downloaded via `ctx.models.download(name)` and loaded with ultralytics.

If `ultralytics` is not installed or the weights are missing,
`ModelInferenceError` is raised with a FILL note; the worker surfaces this as
`ERR_MODEL_INFERENCE`.

## Payload

```json
{
  "image": "<base64 PNG/JPG>",
  "target": "owl",           // free-text or class id, matched against model classes
  "model": "click-v1",       // optional; default per solver
  "conf": 0.25               // optional, YOLO confidence threshold
}
```

For `drag_drop` the payload carries `source_target` and `dest_target` instead
of a single `target`.
