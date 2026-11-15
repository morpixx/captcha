# Runbook: altcha

## 1. Snapshot

- **captcha type:** `CaptchaType.ALTCHA` (`altcha`) — ALTCHA altcha-lib v1 proof-of-work: `challenge = hex(hash(salt + number))`, ответ — base64(JSON) с найденным `number`.
- **antibot family:** нет. Собственный PoW внутри виджета; `AntibotEngine` не задействован.
- **lane:** `protocol`, **mode:** `http` — линейный перебор в памяти, без сети.
- **tier:** 2 — `experimental=False`, есть canary-фикстура; живой сайт не прогонялся.
- **code:** `solvers/pow/altcha.py`, `solvers/pow/_generator.py` (генератор кейсов).

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `algorithm` | нет | `SHA-1` / `SHA-256` (по умолчанию) / `SHA-512` |
| `challenge` | да | hex-дайджест, который надо попасть; длина обязана совпасть с алгоритмом |
| `maxnumber` / `max` | нет | верхняя граница перебора, по умолчанию `1_000_000` |
| `salt` | да | префикс хеша; может нести query (`?expires=...&`) |
| `signature` | нет | подпись сайта, кладётся в токен как есть |

`parameters` или `challenge`-объект в payload = v2/v3 (KDF) и отвергается: против v1-preimage такой кейс проверить нельзя.

```json
{"type": "altcha", "algorithm": "SHA-256",
 "challenge": "aba3677b4a3e9233656758614dc3476468eea94ab016dd433f4a3ccee77eba25",
 "maxnumber": 5000, "salt": "canary-salt&", "signature": "dbdcce0e..."}
```

## 3. Endpoints / flow

Сети нет — вся работа в `altcha.py: solve_http`.

1. `_parse_challenge`: отказ на v2/v3 (`parameters` / объект вместо строки), проверка алгоритма, длины hex (digest_size*2), hex-алфавита, типа `maxnumber`.
2. `max_n` вне `[0, 10**9]` — `ERR_UNSOLVABLE`; враждебный `max` не должен становиться бесконечным циклом.
3. `_check_expiry`: `expires`/`expire` из query соли; протухший — `ERR_CHALLENGE_EXPIRED` (altcha-lib сам отвергнет такой ответ).
4. `_scan` в потоке: `for n in range(max_n+1)`, `hasher(salt_bytes + str(n)) == challenge`; проверка дедлайна каждые 65536 итераций.
5. Токен: `base64(json{algorithm, challenge, number, salt, signature})` c `separators=(",", ":")`.

Бюджет: `task.deadline_ts`, иначе 30 с.

## 4. Solution keys

`SolutionKind.ALTCHA` → `data` (`domain/solution.py`): `{"token": <base64 json>, "number": <n>}`. `extra` добавляет `algorithm`, `number`, `took_ms`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `v2/v3 payload (KDF parameters) is not supported` | сайт перешёл на PBKDF2/Scrypt/Argon2 | нужен v2-путь, сейчас его нет |
| `ERR_UNSOLVABLE` | `unsupported algorithm`, `challenge length does not match`, `not hex` | смена алгоритма/битый payload | сверить `algorithm` и длину с виджетом |
| `ERR_UNSOLVABLE` | `no number in [0..max] matches` | неверная соль/граница | запросить свежий challenge |
| `ERR_CHALLENGE_EXPIRED` | `challenge expired` / `malformed expiry` | `expires` в соли в прошлом | брать свежий challenge у страницы |
| `ERR_TIMEOUT` | `deadline exceeded` | `maxnumber` слишком велик | поднять дедлайн или ограничить `max` |

## 6. Canary / фикстуры

- Канареечный кейс: [altcha.json](../tests/canary/fixtures/altcha.json) — SHA-256, ответ `1337`, `maxnumber` 5000.
- Юнит-фикстуры: `tests/solvers/pow/fixtures/altcha_v1_canary.json`, `altcha_v1_sha512_canary.json`; прогон через harness — `tests/solvers/pow/test_canary_fixtures.py`.
- Pass rate на живом сайте не измерен.

## 7. FILL / live-риски

1. `signature` не проверяется и не подделывается: секрет сайта не покидает сайт. Ответ отдаётся с подписью из payload; если клиент ждёт пересчитанную — её не будет.
2. v2/v3 (PBKDF2/Scrypt/Argon2 против `parameters.keyPrefix`) не реализованы осознанно: v1-preimage там не сходится никогда.
3. Скорость перебора и типичные `maxnumber` на живых сайтах не измерены.
4. `expires` проверяется только когда он есть в query соли; сайт, кладущий срок в поле верхнего уровня, не отсекается.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/pow/test_altcha.py tests/solvers/pow/test_canary_fixtures.py
uv run ruff check src/captcha/solvers/pow
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [altcha.py](../src/captcha/solvers/pow/altcha.py), [_generator.py](../src/captcha/solvers/pow/_generator.py)
- канарейка: [canary.md](canary.md), кейс [altcha.json](../tests/canary/fixtures/altcha.json)
- протокол: [docs/protocol.md](../docs/protocol.md) (раздел «Форма решения»)
