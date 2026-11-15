# Runbook: akamai_sbsd

## 1. Snapshot

- **captcha type:** `CaptchaType.AKAMAI_SBSD` — Akamai Bot Manager Premier (SBSD): JSON-челлендж, ответ = cookie-jar с `_abck` (+ `sbsd`).
- **antibot family:** `AntibotEngine.AKAMAI`; тот же backend, другое тело.
- **lane:** `http`, **mode:** `http` (`needs_js_runtime=True`).
- **tier:** 3 — `experimental=True`.
- **code:** `solvers/antibot/akamai/{sbsd,solver,wire,abck,endpoints}.py`.

## 2. Payload

Всё из [akamai_sensor.md](akamai_sensor.md) плюс SBSD-специфика:

| поле | обяз. | смысл |
|---|---|---|
| `sbsd_body` | нет | готовое тело, снятое клиентом (payload-режим, приоритет) |
| `sbsd_version` | нет | версия схемы для билдера |
| `sbsd_url` / `endpoint` | нет | переопределение endpoint; иначе ищется в HTML, иначе `origin + /sec/cp_challenge/sbsd` |

```json
{"type": "akamai_sbsd", "websiteURL": "https://target.example/", "userAgent": "<ua>",
 "cookies": {"bm_sz": "…"}, "sbsd_body": { }}
```

## 3. Endpoints / flow

1. `GET <websiteURL>` — seed-cookie и HTML (как в классическом флоу).
2. Если `_abck` уже satisfied — сразу ответ, без POST.
3. Endpoint: `sbsd_url`/`endpoint` → ссылка `/sec/cp_challenge/*sbsd*` из HTML (только свой
   host) → `origin + SBSD_PATH_HINT`.
4. Тело: `builder.build_sbsd(ctx)`. Приоритет источника — `payload["sbsd_body"]`, иначе
   секционная модель билдера. Билдер без `build_sbsd` → `ERR_CONFIG`.
5. Один `POST` с `Content-Type: application/json`; заголовки `User-Agent`, `Referer`,
   `Origin`.
6. Вердикт тот же: `_abck ~0~` = решено; `428` = sec-cpt (`ERR_CHALLENGE_EXPIRED`).

## 4. Solution keys

`SolutionKind.COOKIE_ENGINE`: `cookies`, `domains: {host: {cookies}}`, `userAgent`. Cookie
`sbsd`, если вендор её выставил, попадает в тот же jar.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CONFIG` | «the registered akamai builder has no build_sbsd()» | билдер не тот | проверить `engines/akamai/builder.py`, реестр движков |
| `ERR_UNSOLVABLE` | «the challenge page was not readable and no sbsd_url was sent» | bootstrap закрыт 403, endpoint неизвестен | прислать `sbsd_url`/`sbsd_body` |
| `ERR_CHALLENGE_EXPIRED` | «challenge answered but _abck never settled» | тело не принято / sec-cpt | см. §7 |
| `ERR_CHALLENGE_EXPIRED` | HTTP 428 | sec-cpt слой | не ретраить |
| `ERR_SENSOR` | ошибка билдера | скрипт/секции не собрались | `ctx.js_runtime`, `sbsd_version` |

## 6. Canary / фикстуры

- `tests/solvers/antibot/test_akamai_sbsd.py` — единственное покрытие; фикстур-дампов нет.
- Кейс (не добавлен): как у `akamai_sensor`, `type: "akamai_sbsd"`, `solution_contains:
  ["cookies", "domains"]`.

## 7. FILL / live-risks

1. Генерация SBSD-данных **публично не документирована как байты**: солвер принимает только
   клиентское тело или секционную модель билдера, и ни то, ни другое не проверено на живом
   деплое.
2. `SBSD_PATH_HINT` — семейство путей, а не конкретный deploy; реальный путь всегда
   обнаруживается или приходит в payload.
3. 200 сам по себе не считается решением — это главный тест корректности: если кто-то «починит»
   солвер, вернув 200-успех, тип начнёт продавать мусор.
4. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_akamai_sbsd.py tests/engines/test_akamai_builder.py
uv run ruff check src/captcha/solvers/antibot/akamai
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [sbsd.py](../src/captcha/solvers/antibot/akamai/sbsd.py),
  [solver.py](../src/captcha/solvers/antibot/akamai/solver.py),
  [endpoints.py](../src/captcha/solvers/antibot/akamai/endpoints.py)
- builder: [builder.py](../src/captcha/engines/akamai/builder.py)
- родственный runbook: [akamai_sensor.md](akamai_sensor.md)
