# captcha-solver-classification

ViT-B/16 backbone + per-target LoRA classification heads. Picks tiles in a grid
challenge matching a natural-language prompt ("bus", "crosswalk", "fire hydrant").

## Architecture

```
image (base64)  ──►  slice_grid(rows,cols)  ──►  [tile tensor] * N
                                                       │
                                                       ▼
                                   ViTBackbone.encode (shared, cached per device)
                                                       │
                                                       ▼
                                          features: [N, D_feat]
                                                       │
                              ┌────────────────────────┼─────────────────────────┐
                              ▼                        ▼                         ▼
                   LoRAHead("hcaptcha-v1")  LoRAHead("recaptcha-v1")   LoRAHead("funcap-v1")
                              │                        │                         │
                              ▼                        ▼                         ▼
                         indices                   indices                   indices
```

Head weights live in `captcha-models` (S3/MinIO registry) under e.g.
`hcaptcha-v1/<version>/weights.safetensors`. Each head is a LoRA adapter on the
ViT backbone plus a tiny classifier layer; swap the registry pointer to ship an
updated head with no code deploy.

When a prompt arrives with no registered head (new object class on the live
site), the solver raises `ModelInferenceError` with a pointer at
`runbooks/<type>.md` on training the new head — don't fall back to a guess, it
burns the client's rate.

A CLIP zero-shot fallback is wired in `_tiles.select_targets_by_prompt` but
disabled by default: accuracy on small tiles is too low to ship, only use it in
dev to bootstrap labels for the next head.

## Payload (per-type)

```json
{
  "image_b64": "...",          // base64 PNG/JPEG of the full grid
  "prompt": "bus",             // object/scene name, lowercased
  "rows": 3,                   // tile rows
  "cols": 3,                   // tile cols
  "head": "hcaptcha-v1"        // optional override; default = solver's default
}
```

Returns `SolverResult(indices=[0, 2, 5])` — 0-indexed, row-major.

## Install

```
uv pip install -e solvers/classification[torch,hf,peft]
```

Optional extras:
- `torch` — pytorch + torchvision for preprocessing / inference
- `hf`    — transformers (ViT loader, CLIP for zero-shot)
- `peft`  — LoRA adapter loader

## Entry points

Registered under `captcha.solvers`:
- `hcaptcha`         — HCaptchaSolver
- `recaptcha_grid`   — RecaptchaGridSolver
- `funcaptcha_grid`  — FunCaptchaGridSolver
- `awswaf`           — AWSWAFSolver
- `shein`            — SheinSolver

## Training a new head

See `runbooks/<captcha_type>.md` in the repo root. Short version: harvest tiles
+ labels into a dataset, run `scripts/train_lora_head.py` with the target name,
push weights to the model registry, bump the `latest` pointer.
