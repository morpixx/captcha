# funcaptcha fixtures

Trimmed, hand-written stand-ins for the three Arkose endpoints, plus one loader
bundle carrying the constants the solver scrapes (capi 4.4.5, enforcement build
id, enforcement hash, an SPKI public key). The SPKI is a throwaway test key —
public half only, never used against a live deployment.

| file | stands for |
|---|---|
| `api_js.js` | GET /v2/{pk}/api.js, current build (bda field is `c`) |
| `api_js_old.js` | pre-build loader: no build id, no plaintext RSA key |
| `gt2_suppressed.json` | token endpoint, sup=1 (no game) |
| `gt2_session.json` | token endpoint, game session |
| `gfct_grid.json` | challenge, 2x3 tile round |
| `gfct_rotate.json` | challenge, rotate round |
| `ca_answered.json` | answer accepted |
| `ca_round2.json` | answer accepted, next round offered |
| `ca_rejected.json` | answer rejected, no follow-up |
