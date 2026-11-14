# captcha-solver-interactive

Interactive misc captcha plugins. One workspace package, 14 entry points, each loaded via `captcha.solvers` entry group.

These are **protocol wrappers**: the HTTP envelope, token lifecycle, session wiring, and response parsing live here. Computer-vision is **delegated** through `SolverContext` to the corresponding primitive solver (`slider`, `rotate`, `click`, `bounding_box`). Where the vendor protocol is undocumented or fully bespoke, the solver exposes the real reversible shape as module-level constants and raises `CaptchaError("not yet implemented — see runbooks/<type>.md")` from `solve_http` until live RE fills in the blanks.

## Status matrix

| Vendor / name          | `CaptchaType`   | Vendor API host (where known)                     | CV delegate      | Status             |
|------------------------|-----------------|---------------------------------------------------|------------------|--------------------|
| MTCaptcha              | `mtcaptcha`     | `service.mtcaptcha.com`, `service2.mtcaptcha.com` | slider / click   | protocol sketch    |
| Tencent TCaptcha       | `tencent`       | `t.captcha.qq.com`, `turing.captcha.qcloud.com`   | slider / click   | protocol sketch    |
| NetEase Yidun          | `yidun`         | `cstaticdun.126.net`, `c.dun.163.com`             | slider / click   | protocol sketch    |
| Alibaba / AliyunCaptcha| `alibaba`       | `ynuf.aliapp.org`, `captcha.nocaptcha.io`         | slider / click   | protocol sketch    |
| Prosopo Procaptcha     | `prosopo`       | `api.prosopo.io`                                  | click (3x3 grid) | protocol sketch    |
| CaptchaFox             | `captchafox`    | `api.captchafox.com`                              | click / rotate   | protocol sketch    |
| VK                     | `vk`            | `id.vk.com`, `api.vk.com`                         | slider / text    | protocol sketch    |
| Yandex SmartCaptcha    | `yandex`        | `captcha-api.yandex.ru`, `smartcaptcha.yandexcloud.net` | click (grid)  | protocol sketch    |
| Temu riskcontrol       | `temu`          | `www.temu.com/api/bg/aegis/*`                     | slider / click   | protocol sketch    |
| Binance                | `binance`       | `www.binance.com/bapi/composite/v1/public/antibot/*` | slider        | protocol sketch    |
| Hunt (CyberSiara-style)| `hunt`          | vendor-dependent                                  | click            | protocol sketch    |
| Basilisk               | `basilisk`      | `api.basiliskcaptcha.com`                         | click            | protocol sketch    |
| CutCaptcha             | `cutcaptcha`    | `api.cutcaptcha.com`                              | slider           | protocol sketch    |
| atbCaptcha             | `atbcaptcha`    | `cap.aisecurius.com`                              | slider / click   | protocol sketch    |

"Protocol sketch" means: discovery URL + payload constants in `endpoints.py`, HTTP envelope stub in `solver.py`, CV delegation wired, token flow marked with `# FILL:` where live traffic needs to be captured. All import cleanly; `solve_http` raises `CaptchaError("not yet implemented — see runbooks/<type>.md")` if a required RE step has not been filled.

## CV delegation

Every interactive solver does the same shape:

1. POST to vendor's `init` endpoint, parse challenge metadata (cid, prehandle, image URLs).
2. Fetch bg + puzzle / grid images.
3. Build a synthetic `Task` for the right CV primitive (`CaptchaType.SLIDER`, `CaptchaType.ROTATE`, `CaptchaType.CLICK`, `CaptchaType.BOUNDING_BOX`).
4. Look up the primitive solver in the global registry and run its `solve_http`.
5. Package the CV answer (offset / angle / coords) into the vendor's track/sig envelope.
6. POST to `verify`, read back the vendor token.

The lookup/dispatch is on `InteractiveBase` in `_base.py`.
