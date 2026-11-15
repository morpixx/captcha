# Runbook: botdeflector

## 1. Snapshot

- **captcha type:** `CaptchaType.BOTDEFLECTOR` (`botdeflector`) — BotDeflector (botdeflector.eu / Queue-it softblock): flow-token JWT задаёт цепочку шагов, invisible-шаги — Cuckoo Cycle PoW.
- **antibot family:** `AntibotEngine.BOTDEFLECTOR` (builder — `engines/botdeflector/builder.py`, PoW — `engines/botdeflector/pow.py`).
- **lane:** `http`, **mode:** `http` — JS-рантайм не нужен, PoW считается в Python.
- **tier:** 3 — `experimental=True`; классический режим (`siteKey`+`accountId`) не реализован.
- **code:** `solvers/antibot/botdeflector/{solver,payload,wire,transport,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `flowToken` / `flow_token` / `botDeflectorJwtToken` / `botReflectorJwtToken` / `botdeflector_jwt_token` | да | JWT со списком `challengeFlow`; срок 120 с (600 с на botdeflector.eu) |
| `domain` / `challenge_domain` / `api_domain` | нет | API-хост; по умолчанию `https://botdeflector.eu`, для `queue-it` в URL — `https://ipv4.challenge.queue-it.net` |
| `websiteURL` / `pageurl` / `url` | нет | страница: `Origin`/`Referer` для запросов |
| `fingerprint` | нет | словарь захвата клиента; выигрывает у профиля |
| `headers`, `cookies` | нет | доп. заголовки и jar |
| `userAgent` / `user_agent` / `ua` | нет | UA; без него и без профиля — `ERR_SENSOR` |
| `tt_solutions` / `challenge_flow_result` / `challengeFlowResult` / `solution_token` | нет | уже полученный `ChallengeFlowResult`: короткий путь |
| `siteKey` / `site_key`, `accountId` / `account_id` | нет | классический режим; без flow-token даёт `ERR_SENSOR` |

```json
{"type": "botdeflector", "flowToken": "<JWT>", "domain": "https://botdeflector.eu",
 "websiteURL": "https://target.example/", "userAgent": "<ua>"}
```

## 3. Endpoints / flow

Пути из `engines/botdeflector/builder.py`: `/pow/get`, `/pow/verify`, `/verify-flow`; заголовки `x-tt-flow-token`, `x-tt-ch-flow-token`, `x-tt-solution-tokens`, `x-tt-client-fingerprint`.

1. Есть `ChallengeFlowResult` → `wire.flow_result_token` → ответ.
2. `wire.decode_flow_token`: шаги `inv`/`icon`; `icon` (визуальный шаг) не решается — `ERR_UNSOLVABLE` с перечнем `unsupported`.
3. Билдер считает visitor id: из `extra.visitor_id`/`visitorId`/`solution_fingerprint` или как `sha256` отпечатка, первые 32 hex; не 32 hex → `ERR_SENSOR`. Нет UA и нет профиля → `ERR_SENSOR`.
4. На каждый шаг: `GET {domain}/pow/get` (заголовок flow-token) → `wire.pow_challenge`; граф Cuckoo Cycle строится один раз на сессию (`asyncio.to_thread`), решения — тоже в потоке.
5. `POST {domain}/pow/verify` c `solution`+`params`+`time` (уходит фактически потраченное время) → step-token.
6. `GET {domain}/verify-flow` c `x-tt-solution-tokens` → `ChallengeFlowResult`; токен списывается на сайте как `?tt_solutions=<token>`.

Cookies BotDeflector сам не ставит (захват 2026-10): `Queue-it-visitorsession` принадлежит хосту, поэтому jar возвращается только если хост его реально выставил.

## 4. Solution keys

`SolutionKind.TOKEN` → `token` (это `ChallengeFlowResult`), плюс `userAgent` и `cookies`, если они есть. `extra` = `{userAgent?, cookies?}`.

```json
{"token": "<ChallengeFlowResult>", "userAgent": "<ua>", "cookies": {"Queue-it-visitorsession": "…"}}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `requires the scraped botDeflectorJwtToken (flowToken)` | нет flow-token | требовать токен со страницы |
| `ERR_UNSOLVABLE` | `needs a visual step this HTTP lane does not solve` | в цепочке `icon`-шаг | визуального решателя нет — эскалировать вручную |
| `ERR_UNSOLVABLE` | `flow token carries no challengeFlow` | токен без шагов | новый токен |
| `ERR_SENSOR` | `classic mode needs its flow token; the /flow-token/ request shape is not public` | клиент дал только `siteKey`/`accountId` | реализовать `/flow-token/` (см. §7) |
| `ERR_SENSOR` | `builder returned no visitor id`, `visitor id must be 32 hex characters` | нет отпечатка/UA, кривой visitor id | прислать `fingerprint`/UA |
| `ERR_CHALLENGE_EXPIRED` | `check_status` на `pow/*` | шаг отвергнут, токен протух | новый flow-token |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_botdeflector_flow.py` (полный invisible-флоу на фейковом транспорте), `tests/solvers/antibot/test_botdeflector_wire.py` (JWT, PoW-тело, шаги), `tests/engines/test_botdeflector.py` (билдер/PoW).
- Живых JWT нет (120 с). Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "botdeflector-live", "clientKey": "<canary-key>",
 "task": {"type": "botdeflector", "flowToken": "<JWT>", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. `solver.py` и `endpoints.py` помечают `FILL: the /flow-token/ path a standalone widget uses to mint its JWT`. Пока он не снят, классический режим отвечает `ERR_SENSOR`, а не догадкой.
2. `FILL: icon steps need a visual solver` — цепочка с `icon` не решается вообще (это `ERR_UNSOLVABLE`, не ретрай).
3. `FILL: whether /pow/verify enforces a minimum reported solve time` — отправляется фактически потраченное время; сервер может ждать «человеческие» 150–420 мс.
4. `transport.py`: `FILL` про `sec-fetch-*`/`Accept-Encoding` — скорит ли их хост, не проверено.
5. Pass rate на живом Queue-it не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_botdeflector_flow.py tests/solvers/antibot/test_botdeflector_wire.py tests/engines/test_botdeflector.py
uv run ruff check src/captcha/solvers/antibot/botdeflector src/captcha/engines/botdeflector
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/botdeflector/solver.py), [wire.py](../src/captcha/solvers/antibot/botdeflector/wire.py), [transport.py](../src/captcha/solvers/antibot/botdeflector/transport.py), [payload.py](../src/captcha/solvers/antibot/botdeflector/payload.py)
- builder: [builder.py](../src/captcha/engines/botdeflector/builder.py), PoW: [pow.py](../src/captcha/engines/botdeflector/pow.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
