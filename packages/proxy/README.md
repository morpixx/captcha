# captcha-proxy

Residential proxy pool manager.

- Load a pool from a JSON/TXT file
- Pick by country / with a sticky session id
- Mark bad proxies and rotate sessions
- Plug a ban detector onto responses to auto-mark burnt proxies

## Supported providers

URL-builder helpers for Bright Data, Oxylabs, Smartproxy, IPRoyal. These
assemble the right `user-session-...` auth string; they do **not** talk to
any vendor API — add that if your workflow needs on-the-fly pool creation.

## Quick use

```python
from captcha_proxy import Pool

pool = Pool.load_from_file("pool.json")
p = pool.pick(country="DE", sticky=True, session_id="task-abc")
# httpx.AsyncClient(proxy=p.url) ...
if is_banned_response(resp):
    pool.mark_bad(p, reason="403 CF challenge")
```

## FILL list

- Vendor API adapters (pool resize, session refresh)
- Health-check background task
- Pluggable metrics sink (prom, otel)
