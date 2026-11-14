# captcha-fingerprint

Browser + TLS fingerprint profiles. Each profile is a pinned combination of:

- **TLS layer**: JA3, JA4, HTTP/2 SETTINGS, ALPN, extension order
- **Browser layer**: canvas / WebGL / audio hashes, font list, timezone, WebRTC mode, user-agent, Accept-Language

Profiles are stored as JSON on disk (or S3 — see `Store`) and selected per task
based on target geo / language / engine requirements.

## Quick use

```python
from captcha_fingerprint import Store, pick_profile

store = Store("./fingerprints")
await store.load_all()

profile = store.pick({"browser": "chrome", "os": "windows"})
# profile.ja3, profile.user_agent, profile.canvas_hash, ...
```

## Bundles

`bundles.DEFAULT_BUNDLES` ships six handcrafted, structurally-correct profiles
(Chrome/Firefox/Safari on Windows/macOS). They are **scaffold data** —
replace with real captured fingerprints before running against protected
targets.

## FILL list

- S3 backend for `Store` (currently file-only)
- Rotation policy per client / target
- Fingerprint capture pipeline (upstream task)
