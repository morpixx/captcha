# captcha-models

Model registry client for captcha solvers. Pulls weights from S3/MinIO by name+version, caches on disk, exposes a hot-reload watcher so the OCR/audio pipelines can swap weights without a restart.

## Layout in the bucket

```
<bucket>/
  <model-name>/
    <version>/
      meta.json         ModelMeta (JSON, matches msgspec struct)
      weights.<ext>     weights blob (pt, onnx, safetensors, bin)
    latest -> <version>  pointer file (object body = version string)
```

`version='latest'` resolves through the pointer file, then fetches `meta.json`.

## Usage

```python
from captcha_models import Registry, load_torch

reg = Registry(s3_endpoint="http://minio:9000", bucket="captcha-models",
               access_key="...", secret_key="...", cache_dir="./models-cache")

meta = reg.get_meta("hcaptcha-tile-v1")                 # resolves 'latest'
local = reg.download("hcaptcha-tile-v1", meta.version)  # cached by sha256
model = load_torch(local, device="cuda:0")

reg.watch(interval=60.0)   # background thread; re-fetches meta.json
```

Canary rollouts: `ModelMeta.canary_rollout` is 0.0–1.0; `Registry.pick_version(name, bucket=traffic_id)` hashes the traffic id and returns the canary version for that slice.

## Extras

- `captcha-models[torch]` — torch loader
- `captcha-models[onnx]` — onnxruntime loader
- `captcha-models[hf]` — transformers + safetensors
