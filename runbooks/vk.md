# Runbook: vk

## 1. Snapshot

- **captcha type:** `CaptchaType.VK` (`vk`) — VK: legacy image-капча (`captcha_sid`) по OCR и VK ID «not a robot» (`redirectUri`) в браузере.
- **antibot family:** нет. Три режима под одним типом: `image`, `token`, `steps`.
- **lane:** `http` (по каталогу), **mode:** `both`, `browser=True`.
- **tier:** 3 — `experimental=True`; живой прогон не записан.
- **code:** `solvers/interactive/vk/{solver,payload,wire,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `mode` | нет | `image` / `token` / `steps`; иначе выводится из ключей (по умолчанию `token`) |
| `sid` / `captcha_sid` / `captchaSid` | да* | id image-капчи (API error 14) |
| `captcha_img` / `captchaImg` / `image_url` | нет | прямой URL картинки; приоритетнее `captcha.php` |
| `redirectUri` / `redirect_uri` / `websiteURL` | да** | страница VK ID для token-режима |
| `image` + `steps` | нет | наличие обоих переключает на режим `steps` |

`*` — для `image`-режима. `**` — для `token`-режима.

```json
{"type": "vk", "mode": "image", "captcha_sid": "1234567890"}
```

## 3. Endpoints / flow

`endpoints.py`: `https://api.vk.com/captcha.php?sid=<sid>`, `https://api.vk.com/method/captchaNotRobot.getContent`, хост страницы `https://id.vk.com`.

**image:** `GET captcha.php` (или `captcha_img`) → `wire.parse_image` → CV `IMAGE_TO_TEXT` (`lang=en`, charset `latin`) → `token`=`text`=распознанное.

**token (`redirectUri`):** `solve_http` отдаёт `ERR_NEEDS_BROWSER` (или решает тут же, если у контекста есть браузер). Браузерный лейн ставит init-script `TOKEN_HOOK_JS` **до** навигации, потом ждёт `wire.TOKEN_JS`, при пустом ответе кликает по `#not-robot-captcha-checkbox`/чекбоксу и ждёт ещё раз. Хост после навигации обязан остаться в семействе `vk.com`/`vk.ru`.

**steps:** `captchaNotRobot.getContent` image+steps — калейдоскоп, не OCR-картинка; `ERR_UNSOLVABLE` намеренно, а не «наполовину реализовано».

## 4. Solution keys

`SolutionKind.TOKEN` → `token`, `userAgent`. Для image-режима `extra` пуст, но `text` тоже заполнен (удобно клиентам, читающим `text`).

```json
{"token": "k7f3q", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | `success_token is minted by the VK ID widget and bound to the browser session` | token-режим на HTTP-воркере | эскалация в браузерный лейн |
| `ERR_UNSOLVABLE` | `image+steps is a kaleidoscope path, not an OCR image` | режим `steps` | не поддерживается |
| `ERR_UNSOLVABLE` | `payload missing sid/captcha_sid`, `payload missing redirectUri/websiteURL`, `the page left the redirectUri host` | пустой payload/чужой хост | требовать поля, сменить прокси |
| `ERR_CHALLENGE_EXPIRED` | `vk issued no success_token` | виджет не отдал токен | новый раунд |
| `ERR_CONFIG` | `vk image solver requires ctx.http` | контекст без HTTP-клиента (не штатный воркер) | собрать контекст как в `worker_http` |
| `ERR_GENERIC` | `vk: OCR returned no text` | пустой OCR | новый раунд |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_vk.py` (режимы, OCR-делегат, эскалация, хост-проверка).
- Канареечной фикстуры нет; живой VK не прогонялся.
- Кейс (не добавлен):

```json
{"name": "vk-image-live", "clientKey": "<canary-key>",
 "task": {"type": "vk", "captcha_sid": "<sid>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. `captcha.php` без авторизации отдаёт картинку не всегда (нужен `sid` из живой сессии) — на живом VK не проверено.
2. `success_token` привязан к сессии/IP/фингерпринту: браузерный лейн обязан минтить и отдавать `userAgent`; клиент, подставляющий другой UA, токен потеряет.
3. `wire.TOKEN_HOOK_JS` ставится до навигации и хранит только token-образный JSON; ложный успех по одному 200 не считается — но и живого подтверждения нет.
4. Режим `steps` не реализован и не будет: это отдельный калейдоскоп-протокол.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_vk.py
uv run ruff check src/captcha/solvers/interactive/vk
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/vk/solver.py), [payload.py](../src/captcha/solvers/interactive/vk/payload.py), [wire.py](../src/captcha/solvers/interactive/vk/wire.py), [endpoints.py](../src/captcha/solvers/interactive/vk/endpoints.py)
- OCR-делегат: [image_to_text.md](image_to_text.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
