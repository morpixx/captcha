# Runbook: kasada

## 1. Snapshot

- **captcha type:** `CaptchaType.KASADA` — Kasada KPSDK: `/tl`-хендшейк, ответ = cookie-jar + `x-kpsdk-*` заголовки.
- **antibot family:** `AntibotEngine.KASADA` (builder — `engines/kasada/builder.py`, PoW — `engines/kasada/pow.py`).
- **lane:** `http`, **mode:** `http` (`needs_js_runtime=True`).
- **tier:** 3 — `experimental=True`; разборы 2026-05 и 2026-10, живого тенанта нет.
- **code:** `solvers/antibot/kasada/{solver,wire,endpoints,assembly}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница; из неё base-origin и referer |
| `cookies` | нет | jar прошлого раунда (dict / строка / список) |
| `userAgent` | нет* | UA сессии; без него `ERR_CONFIG` |
| `script` / `ips_js` / `p_js` | нет | исходник SDK-скрипта |
| `script_url` / `ips_url` / `p_js_url` | нет | URL скрипта |
| `kpsdk_v` / `version` | нет | `j-X.Y.Z`; иначе вычитывается из скрипта (`VERSION_RE`) |
| `tl_url` | нет | переопределение `/tl` |
| `mfc_url` | нет | переопределение `/mfc` |
| `kpsdk_st`, `kpsdk_answers`, `kpsdk_work_time`, `kpsdk_id`, `kpsdk_duration`, `kpsdk_rst` | нет | входы для `x-kpsdk-cd`; можно вложить в `extra` |

```json
{"type": "kasada", "websiteURL": "https://target.example/", "userAgent": "<ua>",
 "script_url": "https://target.example/149e9513-…/2d206a39-…/p.js",
 "cookies": {"KP_UIDz": "…"}}
```

## 3. Endpoints / flow

Префикс один на все деплои (`endpoints.py`): `/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3`.

1. `GET <websiteURL>` → 429 с HTML, в котором назван `ips.js` (или отдаётся страница).
2. `GET <prefix>/ips.js` / `p.js` — SDK-скрипт (payload-источник приоритетнее сети).
3. `engines/kasada/builder.py` прогоняет скрипт в V8 воркера за stub-браузером
   (`/tl`-маркер, `JS_TIMEOUT_MS = 12000`), возвращает байты тела и заголовки
   `x-kpsdk-ct/dt/im`.
4. `POST <prefix>/tl` — тело `application/octet-stream`, ответ `200 {"reload": true}` +
   `x-kpsdk-ct`, `x-kpsdk-st`. 429/не-200/без `reload:true`/без `ct` = `ERR_CHALLENGE_EXPIRED`.
5. `GET <prefix>/mfc` → `x-kpsdk-fc`, `x-kpsdk-h`.
6. `x-kpsdk-cd` (PoW, одноразовый) собирается только из VM-ответов в payload
   (`assembly.cd_header` → `pow.*`); не выдумывается.

## 4. Solution keys

`extra` (он побеждает канонические ключи): `cookies` (jar после `/tl`), `userAgent`,
`x-kpsdk-ct`, `x-kpsdk-st`, `x-kpsdk-h`, `x-kpsdk-v`, `x-kpsdk-fc`, `x-kpsdk-cd` (если был),
`headers` (только `x-kpsdk-*`), `mfc` (заметка, если `/mfc` не ответил). `domain/solution.py`
добавляет `domains: {host: {cookies}}`. `token` = `x-kpsdk-ct`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CHALLENGE_EXPIRED` | `/tl` ответил 429 | payload/сессия отвергнуты | свежий профиль, проверить UA/прокси |
| `ERR_CHALLENGE_EXPIRED` | не-200, нет `reload:true`, нет `x-kpsdk-ct` | неверное тело или деплой сменил контракт | регенерировать скрипт/версию |
| `ERR_UNSOLVABLE` | «the SDK script could not be read» | скрипт не отдаётся и не прислан | прислать `script`/`script_url` |
| `ERR_SENSOR` | билдер вернул не base64 / пусто | VM не собрала тело | `ctx.js_runtime`, размер скрипта |
| `ERR_CONFIG` | нет UA | сессия без UA невоспроизводима | прислать `userAgent` |
| `ERR_PROXY` | проброс транспорта | прокси | менять прокси |

## 6. Canary / фикстуры

- Билдер: `tests/engines/test_kasada_builder.py`; флоу: `tests/solvers/antibot/test_kasada_flow.py`.
- Живых дампов `/tl` в репозитории нет (тело — байткод VM, его нельзя «примерно» записать).
- Кейс (не добавлен):

```json
{"name": "kasada-live", "clientKey": "<canary-key>",
 "task": {"type": "kasada", "websiteURL": "https://<host>/", "userAgent": "<ua>"},
 "expect": {"status": "ready", "solution_contains": ["cookies", "x-kpsdk-ct", "userAgent"]}}
```

## 7. FILL / live-риски

1. `x-kpsdk-cd` одноразовый и здесь **не минтится**: он либо приходит из VM-ответов payload,
   либо отсутствует. Клиент, которому нужен `cd` на каждый запрос, получит только `ct/st/h/v/fc`.
2. Версия (`x-kpsdk-v`) вычитывается регуляркой из скрипта; при смене формата — `version_hint`
   вернёт пусто, и заголовок уйдёт без версии. Не проверено на живом деплое.
3. Имена cookie per-tenant (`KP_UIDz` — только пример); ничего не ключуется на конкретное имя.
4. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/engines/test_kasada_builder.py tests/solvers/antibot/test_kasada_flow.py
uv run ruff check src/captcha/solvers/antibot/kasada src/captcha/engines/kasada
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/kasada/solver.py),
  [assembly.py](../src/captcha/solvers/antibot/kasada/assembly.py),
  [endpoints.py](../src/captcha/solvers/antibot/kasada/endpoints.py)
- builder: [builder.py](../src/captcha/engines/kasada/builder.py), PoW: `engines/kasada/pow.py`
- разбор: `docs/reverse-2026.md` §6 (на него ссылается docstring солвера)
- протокол: [docs/protocol.md](../docs/protocol.md)
