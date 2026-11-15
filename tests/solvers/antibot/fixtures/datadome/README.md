# DataDome fixtures

Shapes are taken from the wire description in `docs/reverse-2026.md` §5
(tags POST answer, interstitial script output). The values are synthetic — no
live capture is committed, and the tests must run with network disabled.

| file | what it replays |
|---|---|
| `tags_ok.json` | `POST api-js.datadome.co/js/` -> 200 + `Set-Cookie: datadome=...` |
| `interstitial_ok.json` | the challenge script's `{cookies, headers, userAgent}` result |
