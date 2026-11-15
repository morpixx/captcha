# Runbook: mtcaptcha

## 1. Snapshot

- **captcha type:** `CaptchaType.MTCAPTCHA` (`mtcaptcha`) — текстовый image-челлендж MTCaptcha: OCR картинки, токен минтит верификатор.
- **antibot family:** нет. Плоский JSON-API `mtcv1` + риск-cookie `mtv1Pulse`.
- **lane:** `cv` (по каталогу, нужен OCR), **mode:** `http`.
- **tier:** 3 — `experimental=True`; wire снят с виджета 2026-10-23, живой прогон не записан.
- **code:** `solvers/interactive/mtcaptcha/{solver,wire,endpoints,pulse,fold}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `sitekey` | да | алиасы `websiteKey`, `key` |
| `websiteURL` | да | абсолютный URL страницы (иначе `ERR_UNSOLVABLE`); из него домен для верификатора |
| `action` | нет | action-тег виджета |
| `charset` | нет | подсказка OCR-делегату |
| `lang` / `language` | нет | язык; иначе `task.language`, иначе `en` |
| `user_agent` / `userAgent` | нет | UA для `mtv1Pulse`; иначе `DEFAULT_UA` |

```json
{"type": "mtcaptcha", "sitekey": "<sitekey>", "websiteURL": "https://target.example/login"}
```

## 3. Endpoints / flow

`endpoints.py`: `https://service.mtcaptcha.com/mtcv1/api/{getchallenge,getimage,solvechallenge}.json`; `JS_VERSION = "2026-10-23.19.20.13"`, `CONF_SUM`, `TRANSACTION_SALT`.

1. `wire.session_id()`, `wire.headers_for(sitekey, domain, language)`.
2. `GET getchallenge.json` (`sitekey, domain, session, language, action`) → `wire.parse_challenge` (`ct`, `fold`, длина текста).
3. `pulse_mod.pulse(site_url, ua, language)` → cookie `mtv1Pulse`; `GET getimage.json` (`sitekey, ct, session, fold`) с этой cookie.
4. CV-делегат `CaptchaType.IMAGE_TO_TEXT` с `{image, lang, charset}`.
5. `GET solvechallenge.json` (`sitekey, domain, ct, session, answer, fold, language`) → `verifyResult.verifiedToken.vt`.

Токен `v1(h1,h2,sitekey,hash)` **не собирается** в солвере: оба chain-хэша принадлежат верификатору.

## 4. Solution keys

`SolutionKind.TOKEN` → `token` (= `vt`) и `userAgent`, если у задачи был UA.

```json
{"token": "v1(h1,h2,sitekey,hash)", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `payload missing sitekey/websiteKey`, `websiteURL must be an absolute URL` | нет ключа/относительный URL | требовать sitekey + абсолютный URL |
| `ERR_CONFIG` | `requires ctx.http` | cv-воркер без HTTP-клиента | см. §7 |
| `ERR_CHALLENGE_EXPIRED` | `getchallenge/getimage rejected the challenge` (400/401/403/404/408/410/429) | челлендж одноразовый/протух | новый раунд |
| `ERR_GENERIC` (`CaptchaError`) | `mtcaptcha: OCR returned no text` | пустой OCR | ретрай с новым `ct` |
| `ERR_GENERIC` (`CaptchaError`) | `getchallenge failed` / `not JSON` | 5xx или не-JSON | смотреть `status` в контексте |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_mtcaptcha.py` (канонические ответы вендора, OCR-делегат замокан, ошибки).
- Живых дампов виджета в репозитории нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "mtcaptcha-live", "clientKey": "<canary-key>",
 "task": {"type": "mtcaptcha", "sitekey": "<sitekey>", "websiteURL": "https://<host>/login"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. **Лейн против контекста.** Каталог ставит тип в `cv`, а `services/worker_cv/worker.py` собирает `SolverContext` без `http` — на этом воркере задача падает в `ERR_CONFIG` до первого запроса. Нужен `ctx.http` на cv-воркере или перенос в http-лейн; по коду, на живом стенде не проверено.
2. `DEFAULT_UA` — заглушка, помеченная `# FILL` в солвере: реальный UA транспорта не читается (curl_cffi его не отдаёт).
3. `JS_VERSION`, `X-...`-заголовки и `CONF_SUM` пиннятся на сборку 2026-10-23; новый релиз виджета = `ERR_CHALLENGE_EXPIRED`.
4. `mtv1Pulse` строится из site_url+UA+языка; насколько он совпадает с настоящим риском-куки, не проверено.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_mtcaptcha.py
uv run ruff check src/captcha/solvers/interactive/mtcaptcha
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/mtcaptcha/solver.py), [wire.py](../src/captcha/solvers/interactive/mtcaptcha/wire.py), [endpoints.py](../src/captcha/solvers/interactive/mtcaptcha/endpoints.py), [pulse.py](../src/captcha/solvers/interactive/mtcaptcha/pulse.py)
- OCR-делегат: [image_to_text.md](image_to_text.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
