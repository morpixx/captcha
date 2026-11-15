# detection-v1 golden fixtures

The manifest fixes the answer shape for the four detection solvers:

| case | solver | projected key |
|---|---|---|
| click-0001 | click / coordinates | coordinates (a single point is written flat: [x, y]) |
| bbox-0002 | bounding_box | bbox [x, y, w, h] |
| draw-0003 | draw_around | coordinates: four box corners |
| drag-0004 | drag_drop | coordinates: [source, destination] |

Images are synthetic 320x240 PNGs. No weights are committed; the payload
carries fake detector rows under _boxes / _scores / _classes, which the test
predictor strips before building the task. The single-point click expectation
is flat because the golden matcher only applies pixel tolerance to numeric
lists of length >= 2; a one-element nested list would be compared exactly.
