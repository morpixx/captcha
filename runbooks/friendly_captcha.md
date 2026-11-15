# Runbook: friendly_captcha

## 1. Snapshot

- **captcha type:** `CaptchaType.FRIENDLY` (`friendly_captcha`) — Friendly Captcha v1 proof-of-work: сервер отдаёт подписанный puzzle-буфер, клиент возвращает nonce-ы, на которых blake2b проходит порог сложности.
- **antibot family:** нет. Это не `AntibotEngine`, а собственный PoW (friendly-pow v1); сенсор-билдера в `engines/` у типа нет.
- **lane:** `protocol`, **mode:** `http` — чистый CPU, без сети и моделей.
- **tier:** 2 — `experimental=False`, есть canary-фикстура; живой сайт не прогонялся.
- **code:** `solvers/pow/friendly.py`, `solvers/pow/_hash.py` (порог), `solvers/pow/_generator.py` (генератор кейсов, не регистрируется).

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `puzzle` | да* | вся строка `<signature>.<base64 buffer>` |
| `signature` | да* | подпись отдельно, когда `puzzle` — только буфер |
| `puzzle_buffer` / `puzzleBuffer` | да* | base64-буфер при раздельной подаче |

`websiteURL` не нужен: солвер не делает ни одного запроса. `*` — либо `puzzle` в форме `<sig>.<b64>`, либо `signature` + `puzzle_buffer`.

```json
{"type": "friendly_captcha", "puzzle": "<sig>.avkHfwAAAAAAAAAAAQAFLAAAAAAAAAAAquifwPA+KVk="}
```

## 3. Endpoints / flow

Сети нет: вход и выход целиком в payload (`friendly.py: parse_puzzle`).

1. `puzzle` разбирается на `(signature, buffer)`; base64 обычный, при отказе — urlsafe.
2. Валидация буфера: 32…64 байта, `buf[12] == 1` (версия), `buf[14] != 0` (число решений), срок `buf[13] * 300` секунд от `u32be timestamp` в `buf[0:4]`.
3. Майнинг в отдельном потоке (`asyncio.to_thread(_mine)`): буфер дополняется нулями до 128 байт, кандидат пишется в последние 8 байт (big-endian), `blake2b-256(buffer)[:4]` как `u32le` сравнивается с порогом `_hash.difficulty_threshold(difficulty)`.
4. Токен: `<signature>.<b64 buffer>.<b64 solutions>.<b64 diagnostics>`, diagnostics = `(solver_id=0, took_sec >> 8, took_sec & 0xFF)`.

Бюджет: `task.deadline_ts`, иначе 30 с (`_DEFAULT_BUDGET_SEC`) — враждебный байт difficulty не должен крутить воркер бесконечно.

## 4. Solution keys

`SolutionKind.FRIENDLY` → `data: {"token": <token>}` (`domain/solution.py`). `extra` проходит насквозь: `difficulty`, `solutions`, `took_ms`.

```json
{"data": {"token": "sig.b64buffer.b64solutions.b64diagnostics"}}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `payload missing 'puzzle'`, `puzzle must be '<signature>.<base64 buffer>'` | клиент не прислал puzzle или склеил поля | прислать `puzzle` либо `signature`+`puzzle_buffer` |
| `ERR_UNSOLVABLE` | `puzzle buffer must be 32..64 bytes`, `unsupported puzzle version`, `asks for zero solutions` | другой wire или дрейф версии | сверить длину и байт версии с friendly-pow v1 |
| `ERR_CHALLENGE_EXPIRED` | `puzzle expired` | `timestamp + expiry*300 < now` | брать свежий puzzle у страницы |
| `ERR_TIMEOUT` | `deadline exceeded while mining` | difficulty выше бюджета | поднять дедлайн задачи, посмотреть `difficulty` в контексте |

В `context` у ошибок лежат `length`/`version`/`found`/`needed`/`difficulty` — по ним видно, какой байт не сошёлся.

## 6. Canary / фикстуры

- Канареечный кейс: [friendly_captcha.json](../tests/canary/fixtures/friendly_captcha.json) — 32-байтный буфер, 5 решений, difficulty 44, без срока; ожидание `data`.
- Юнит-фикстуры: `tests/solvers/pow/fixtures/friendly_v1_canary.json`, `friendly_v1_64byte_canary.json`; прогон через `captcha.solvers.harness` — `tests/solvers/pow/test_canary_fixtures.py`.
- Генератор кейсов: `make_friendly_puzzle()` в `_generator.py` (детерминированный, только тесты и канарейка).
- Pass rate на живом сайте не измерен.

## 7. FILL / live-риски

1. Живого сайта нет: всё, что известно о wire, — friendly-pow v1 из docstring и фикстур. Лайв-прогон не снимался.
2. `expiry` читается как `buf[13] * 300` секунд (спецификация, не замер). Сайт с другим юнитом даст ложный `ERR_CHALLENGE_EXPIRED` либо пропустит протухший puzzle.
3. `solver_id` в diagnostics всегда `0` (unspecified); `1` (JS) и `2` (WASM32) не выставляются — принимает ли их сервер, не проверено.
4. Время майнинга на реальном difficulty не измерено; в коде только бюджет 30 с и `took_ms` в `extra`.
5. v2 (session signals, pointer/keyboard telemetry) не реализован: это браузерный протокол, см. `docs/reverse-2026.md` §12.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/pow/test_friendly.py tests/solvers/pow/test_canary_fixtures.py
uv run ruff check src/captcha/solvers/pow
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [friendly.py](../src/captcha/solvers/pow/friendly.py), [_hash.py](../src/captcha/solvers/pow/_hash.py), [_generator.py](../src/captcha/solvers/pow/_generator.py)
- канарейка: [canary.md](canary.md), кейс [friendly_captcha.json](../tests/canary/fixtures/friendly_captcha.json)
- протокол: [docs/protocol.md](../docs/protocol.md)
- разбор: [docs/reverse-2026.md](../docs/reverse-2026.md) §12
