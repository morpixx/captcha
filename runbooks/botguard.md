# Runbook: botguard

## 1. Snapshot

- **captcha type:** `CaptchaType.BOTGUARD` (`botguard`) — Google BotGuard: байткод-VM за reCAPTCHA / WAA (YouTube) / account-сигналами; ответ — непрозрачный response-token.
- **antibot family:** `AntibotEngine.BOTGUARD` (builder — `engines/botguard/builder.py`, разбор программы — `solvers/antibot/botguard/wire.py`).
- **lane:** `http`, **mode:** `http` (`needs_js_runtime=True`, браузерного пути нет).
- **tier:** 3 — `experimental=True`; verify-эндпоинт за продуктом не запинен.
- **code:** `solvers/antibot/botguard/{solver,payload,wire,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `ftx` / `program` / `bg_program` / `challenge_program` / `bg_challenge` | да | тело программы (`f.txt`) — как есть или JSON-обёрткой |
| `interpreter_js` / `interpreterJavascript` / `interpreter` / `challenge_js` / `challenge_script` | да* | интерпретатор VM |
| `interpreter_url` / `interpreterUrl` / `challenge_js_url` / `program_url` | нет* | откуда скачать интерпретатор; протокол-относительный URL достраивается |
| `global_name` / `globalName` / `vm_global` | нет | имя глобала VM; обязано быть JS-идентификатором |
| `product` / `surface` / `botguard_product` | нет | `recaptcha` (по умолчанию) / `waa` / иное |
| `cookies` | нет | карта или строка `a=b; c=d` — уходит в `document.cookie` шима |
| `headers` | нет | дополнительные заголовки (lowercase) |
| `userAgent` / `user_agent` / `ua` | нет | UA в шиме и в ответе |
| `timestamp_ms`, `js_timeout_ms` | нет | входы билдера; JS-бюджет по умолчанию 30000 |

`*` — нужен либо инлайн-интерпретатор, либо его URL.

```json
{"type": "botguard", "ftx": "<программа>", "interpreter_js": "<интерпретатор>", "product": "recaptcha"}
```

## 3. Endpoints / flow

Своего verify-вызова нет: солвер **отдаёт токен**, а какой эндпоинт его примет — решает продукт (`endpoints.py`: для reCAPTCHA `/recaptcha/api2/bframe` и `/recaptcha/api2/userverify`; для account/WAA — `FILL`).

1. `require_ctx(ctx, "http", "sensors", "js_runtime")` — нет рантайма ⇒ `ERR_CONFIG`.
2. `wire.parse_program` разбирает программу и `global_name`; нормализация `interpreter_url` (`//host/…` → `https:`, `/path` → `https://www.google.com/path`).
3. Если интерпретатора нет — `GET <interpreter_url>`; не-2xx ⇒ `ERR_SENSOR`.
4. Билдер отдаёт `{script, globals}`: шим (window/document/navigator/crypto/timers — детерминированные, чтобы токен не менялся от ретрая к ретраю) + интерпретатор + вызов `vm.a(program, …)` либо `new botguard.bg(program)`.
5. `ctx.js_runtime.exec_script` → `wire.token_from_runtime` (минимум 32 символа, иначе `ERR_CHALLENGE_EXPIRED`).

## 4. Solution keys

`SolutionKind.TOKEN` → `token` (+ `userAgent`, если он был). `extra`: `product`, `userAgent`.

```json
{"token": "<botguard response>", "product": "recaptcha", "userAgent": "<ua>"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `requires the scraped f.txt program` / `requires the interpreter script or its interpreter_url` | клиент не прислал программу/интерпретатор | требовать `ftx` + один из interpreter-полей |
| `ERR_CHALLENGE_EXPIRED` | `needs the f.txt program; it rotates with the challenge` | программа протухла между разбором и сборкой | свежий `f.txt` |
| `ERR_CHALLENGE_EXPIRED` | VM вернула пусто / токен короче 32 | VM не инициализировалась | ретрай с новой программой |
| `ERR_SENSOR` | `interpreter fetch failed`, `global_name must be a plain JS identifier` | не скачался интерпретатор / плохое имя глобала | поправить payload, проверить URL |
| `ERR_CONFIG` | `SolverContext missing required fields: http, sensors, js_runtime` | воркер без рантайма | http-воркер с JS-песочницей |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/antibot/test_botguard.py` (разбор программы, отсутствие интерпретатора, `global_name`, пустая VM, `token_from_runtime`), драйвер — `tests/engines/test_botguard_driver.py`.
- Живых `f.txt` в репозитории нет (ротируются). Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "botguard-live", "clientKey": "<canary-key>",
 "task": {"type": "botguard", "ftx": "<f.txt>", "interpreter_url": "<url>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-риски

1. `endpoints.py` прямо помечает `FILL: capture live f.txt + verify URL for the specific product target` — какой эндпоинт ест токен у account/WAA, не снято.
2. Токен отдаётся «как есть», без verify: успешность на живом продукте не измерена.
3. Шим детерминированный (фиксированные navigator/screen/crypto): VM, которая проверяет энтропию окружения, может вернуть `no-token` — это будет `ERR_CHALLENGE_EXPIRED`, а не «неверный ответ».
4. `global_name` валидируется как JS-идентификатор; VM, ожидающая вложенное имя (`a.b`), отвергается `ERR_SENSOR`.
5. API VM: поддержаны три формы (`vm.a`/`botguard.bg`/`factory`); четвёртая даст `vm-shape-unknown` в предупреждениях результата.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_botguard.py tests/engines/test_botguard_driver.py
uv run ruff check src/captcha/solvers/antibot/botguard src/captcha/engines/botguard
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/antibot/botguard/solver.py), [payload.py](../src/captcha/solvers/antibot/botguard/payload.py), [wire.py](../src/captcha/solvers/antibot/botguard/wire.py), [endpoints.py](../src/captcha/solvers/antibot/botguard/endpoints.py)
- builder: [builder.py](../src/captcha/engines/botguard/builder.py)
- песочница: [adapters/jssandbox](../src/captcha/adapters/jssandbox)
- протокол: [docs/protocol.md](../docs/protocol.md)
