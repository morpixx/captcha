# slider-v1 golden fixtures

Edge/template matching needs no model: each case is a synthetic background with
a patterned patch pasted at the expected gap x, plus the shared RGBA piece.
The expected answer is the gap x in background pixels, tolerance 1 px.

The predictor reads the piece from payload.piece_image and passes backend
"numpy", so the golden run exercises the cv2-free matcher available in the base
worker. Images and pieces are generated fixtures, not customer data.
