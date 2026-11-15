# Runbook: temu

## 1. Snapshot

- **captcha type:** `CaptchaType.TEMU` (`temu`) — Temu aegis-слайдер на `api/bg/aegis/*` с фингерпринтом `anti_content` от PDD.
- **antibot family:** нет. Собственный риск-контроль страницы, без iframe и без sitekey.
- **lane:** `http`, **mode:** `http`, `needs_js_runtime=True` (подпись `anti_content` делает страница).
- **tier:** 3 — `experimental=True`; живого захвата ответа `verify` нет.
- **code:** `solvers/interactive/temu/{solver,wire,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `anti_content` | да | фингерпринт, снятый клиентом из заголовка запроса; из sitekey не выводится |
| `scene` / `captcha_scene` | нет | сцена в теле `init` |
| `captcha_id` / `captchaId` | нет | id челленджа, если клиент его уже знает |
| `biz_type` / `bizType` | нет | бизнес-тип |
| `pageurl` / `websiteURL` / `site_url` | нет | Referer/Origin; по умолчанию `www.temu.com` |
| `user_agent` / `userAgent` | нет | UA |
| `lang` / `language` | нет | Accept-Language |
| `seed` | нет | детерминизм траектории |

```json
{"type": "temu", "anti_content": "<header>", "scene": "<scene>", "pageurl": "https://www.temu.com/"}
```

## 3. Endpoints / flow

`endpoints.py`: `POST https://www.temu.com/api/bg/aegis/captcha/init`, `POST .../captcha/verify` (недокументированные, per-build).

1. Нет `anti_content` → `ERR_UNSOLVABLE` с подсказкой, где его взять (пустой заголовок и 403 в ответ — не «успех»).
2. `POST init` (`{scene, captchaId, bizType}`) с заголовками `anti-content`, `x-requested-with`, Referer/Origin, Accept-Language.
3. `wire.parse_challenge`: вариант, `bg`, `piece`, промпт.
4. `variant_kind`: слайдер → CV `SLIDER`; иначе `CLICK` с текстом цели. **Grid-вариант отвергается** `ERR_UNSOLVABLE` — головы под этот layout нет.
5. `POST verify` (`wire.answer_body`: геометрия + трек + seed).
6. `wire.extract_cookies` (Set-Cookie) и `wire.find_token`; ни того, ни другого → `ERR_CHALLENGE_EXPIRED`.

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE` → `cookies`, `domains: {host: {cookies}}`, `userAgent`. `extra`: `cookies`, `domain`, `challengeType`, `captchaId`, `userAgent`.

```json
{"cookies": {"…": "…"}, "domains": {"www.temu.com": {"cookies": {"…": "…"}}},
 "userAgent": "<ua>", "captchaId": "…", "challengeType": "slide"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `needs the client-captured anti_content header` | клиент не снял заголовок | требовать `anti_content` |
| `ERR_UNSOLVABLE` | `grid variant is not wired` | Temu отдал сетку | не поддерживается этим солвером |
| `ERR_UNSOLVABLE` | `slider challenge has no piece image`, `click challenge has no target text`, `image body is empty`, `broken data URI` | смена формы челленджа | `keys`/`variant` в контексте |
| `ERR_CHALLENGE_EXPIRED` | `verify returned no session cookie or token` | челлендж потреблён/отвергнут | новый раунд |
| `ERR_CHALLENGE_EXPIRED` | 401/403/409/410/428/429 в `wire` | сессия/подпись не сошлись | свежий `anti_content` |
| `ERR_CONFIG` | `temu solver needs ctx.http` | воркер без HTTP | см. §7 |
| `ERR_GENERIC` | `temu init failed` / `not JSON` | 5xx/не-JSON | `status` в контексте |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_temu.py` (init/verify, варианты, отсутствие `anti_content`, grid-отказ).
- Живых дампов `init`/`verify` нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "temu-live", "clientKey": "<canary-key>",
 "task": {"type": "temu", "anti_content": "<header>", "pageurl": "https://www.temu.com/"},
 "expect": {"status": "ready", "solution_contains": ["cookies"]}}
```

## 7. FILL / live-риски

1. `anti_content` не генерируется: без клиентского захвата тип нерешаем. Это архитектурное ограничение, не баг.
2. Пути `api/bg/aegis/*` недокументированы и меняются per-build; парсер терпим к алиасам, но на дрейфе упадёт громко.
3. Трек/подпись в `verify` собраны по разбору: сколько живых вызовов проходит — не проверено.
4. Grid-вариант не поддержан (нет головы под layout) — часть трафика Temu будет `ERR_UNSOLVABLE`.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_temu.py
uv run ruff check src/captcha/solvers/interactive/temu
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/temu/solver.py), [wire.py](../src/captcha/solvers/interactive/temu/wire.py), [endpoints.py](../src/captcha/solvers/interactive/temu/endpoints.py)
- геометрия-делегаты: [slider.md](slider.md), [click.md](click.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
