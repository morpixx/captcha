# captcha-browser

Browser fallback pool. Only touches a real Chromium when:

- the task was dispatched with `preferred_mode=BROWSER` (URL-mode solve), or
- the HTTP solver failed and the router promoted the task to the browser worker.

Not the default path for anything. HTTP-first is always preferred because a browser
session is ~200x the cost per solve and ~50x the latency.

## Backends

Picked by `profiles.pick_launcher()` at `Pool.launch()` time:

1. **camoufox** — Firefox-based, custom anti-fingerprint build. Best stealth against
   Cloudflare Turnstile interactive, DataDome interactive, AWS WAF. Heaviest.
2. **patchright** — patched playwright with the easy fingerprint tells closed. Default
   when camoufox isn't installed.
3. **playwright** — vanilla. Fingerprints as bot on anything serious; useful for local
   dev and regression testing.

Install either or both as extras:

```
uv pip install "captcha-browser[patchright]"
uv pip install "captcha-browser[camoufox]"
```

## Usage

```python
from captcha_browser import Pool, SessionConfig

pool = Pool(size=10, headless=True)
await pool.launch()
try:
    async with pool.acquire(SessionConfig(proxy="http://user:pass@host:1080")) as sess:
        await sess.apply_fingerprint(profile)
        await sess.goto_with_stealth("https://target.example/challenge")
        await sess.humanize_click("button[data-captcha-submit]")
finally:
    await pool.close_all()
```

A context is retired after `max_uses` solves or on any unrecoverable error.
