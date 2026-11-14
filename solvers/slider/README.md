# captcha-solver-slider

Classical CV slider-captcha solver. One class (`SliderSolver`) is registered under
six entry points: `slider`, `capy`, `keycaptcha`, `lemin`, `cutcaptcha_slider`,
`datadome_slider`. The underlying gap-detection is identical; per-vendor payload
quirks (image keys, coordinate frame) are handled inside `solve_http`.

## Payload shape

```json
{
  "bg":    "<base64 PNG/JPG, full challenge background>",
  "piece": "<base64 PNG, the sliding puzzle piece>",
  "piece_y": 40,                       // optional, vertical offset of the piece
  "bg_offset_x": 0,                    // optional, pixel offset to add to result
  "piece_width": null                  // optional, hint for template crop
}
```

Either field may be a `data:` URL; the prefix is stripped.

## Result

`SolverResult(offset=<int>, extra={"trajectory": [[x,y,t], ...]})`

`trajectory` is a humanized cubic-bezier path produced by `captcha_behavior.bezier`
with `captcha_behavior.timing` cumulative milliseconds. The caller feeds it into
the vendor's drag-events submit.

## When this solver works

The classical pipeline solves ~70% of real slider challenges out of the box:
Capy, KeyCAPTCHA, Lemin, Cutcaptcha-simple, and the easier DataDome slider
variants. It fails on puzzles whose piece mask is non-rectangular with fuzzy
edges, or where the background has been dithered to spike the Canny response
everywhere — those need the ML-regressor path, out of scope here.
