# hcaptcha_js

Node worker that runs hCaptcha's obfuscated **hsw/hsj** bundle and exposes the
three operations the HTTP solver needs:

| operation | bundle call | used for |
|---|---|---|
| solve | `hsw(reqJwt, {href, vm_data, ...})` | n-token after `checksiteconfig` |
| encrypt | `hsw(1, msgpackPayload)` | `enc_get_req` request body |
| decrypt | `hsw(0, responseBody)` | encrypted `getcaptcha` response |

## Layout

- `server.js` - stdio server: one JSON request line, one JSON response line.
  Protocol is documented in the file header. Requests are serialized.
- `bundle.js` - JWT claims -> bundle URL (`n` = bundle name, `l` = asset path),
  allowlisted to `*.hcaptcha.com` over https, 12h disk cache in `.cache/`,
  one jsdom realm per build+fingerprint (max 4, oldest closed first).
- `realm.js` - the browser surface the bundle probes: `navigator`, `screen`,
  2d canvas (via `node-canvas`, wired by jsdom), a WebGL stub, WebCrypto,
  `atob`/`btoa`, `fetch`.
- `runner.py` - Python side (`JsBundleRunner`): async subprocess, request lock,
  stderr drain, base64 framing, domain errors.

## Install

`node_modules` is not vendored. Once per image:

    cd src/captcha/adapters/hcaptcha_js && npm ci

`canvas` ships prebuilt binaries for the usual platforms; if the install falls
back to a source build, the image needs a C++ toolchain.

## Limits (read before trusting a pass rate)

The realm is not Chrome. It has no real WebGL renderer, no Chrome font stack,
no window-property facade, and no anti-detect `vm_data` collector. On hardened
sitekeys (Riot and friends) the n-token is protocol-correct and still refused -
that is the browser lane's job, and the solver escalates on a challenge rather
than retrying. `vm_data` can be passed through from the caller when a collector
is available; without it the bundle sees `null`.

Set `HCAPTCHA_JS_DEBUG=1` in the worker environment to get full stacks on
stderr instead of the one-line error that crosses the pipe.
