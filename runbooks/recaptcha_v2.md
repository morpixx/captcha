# Runbook: recaptcha_v2 (+ enterprise / invisible / callback)

## 1. Snapshot

- **captcha types:** `RECAPTCHA_V2`, `RECAPTCHA_V2_INVISIBLE`, `RECAPTCHA_V2_CALLBACK` — один солвер; `RECAPTCHA_V2_ENTERPRISE` — второй, те же шаги на `/recaptcha/enterprise/`.
- **antibot family:** Google reCAPTCHA v2; `AntibotEngine` нет.
- **lane:** `cv`, **mode:** `http` только (`needs_js_runtime=False`).
- **tier:** 3 — `experimental=False` в реестре (`version 0.2.0`), но live pass rate не измерен.
- **code:** `solvers/recaptcha/{v2_audio,_audio,_common,_payload,_proto,v2_grid}.py`,
  grid-делегат — `solvers/classification/recaptcha_grid.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница; из неё `co` (base64 origin) и referer |
| `websiteKey` | да | sitekey (`task.sitekey`) |
| `size` | нет | `normal` / `invisible` / `compact`; у invisible/callback типов дефолт `invisible` |
| `v` / `version` | нет | release-slug; иначе `DEFAULT_VERSION`, при отказе — из `api.js` |
| `image_b64` + `prompt` | нет | если заданы — задача уходит в grid-путь, а не в audio |
| `language` | нет | `hl`, дефолт `en` |

```json
{"type": "recaptcha_v2", "websiteURL": "https://target.example/login",
 "websiteKey": "6Lc…", "size": "normal"}
```

## 3. Endpoints / flow

Endpoints — `solvers/recaptcha/_common.py`: `API2` (`/recaptcha/api2/…`) и `ENTERPRISE`
(`/recaptcha/enterprise/…`), хост `www.google.com`, bootstrap `api.js` / `enterprise.js`.

1. `GET <anchor>?ar=1&k=<sitekey>&co=<base64 origin>&hl=&v=&size=&cb=<12 random>` → HTML с
   `recaptcha-token` (иначе пробуем свежий release-slug из `api.js`).
2. `POST <reload>?k=<sitekey>` (form `v, reason=q, c=<anchor token>, k, sa, ds=[], chr=[0,0,0]`) →
   challenge token.
3. `POST <reload>` `reason=a` → audio token + (обычно) URL mp3.
4. `GET <payload url>` — mp3; расшифровка через `ctx.extra["audio_transcriber"]` или
   `ctx.extra["solver_registry"]` (Whisper здесь не импортируется).
5. `POST <userverify>?k=` (form `v, c=<audio token>, response=<text>, t=0, ct=0, bg=`) → финальный
   `rresp` = `g-recaptcha-response`.

Grid-путь (`v2_grid.solve_grid`): задача с `image_b64` + `prompt` превращается в
`CLASSIFICATION_GRID` и решается `RecaptchaGridSolver` (head `recaptcha-v1`, 3x3/4x4). Возвращает
`indices`, а не токен: сабмит сетки — отдельный шаг клиента.

## 4. Solution keys

`SolutionKind.RECAPTCHA`: `gRecaptchaResponse` (= токен), `userAgent` (если известен).
`extra` проходит насквозь, поэтому в audio-пути клиент увидит ещё `audio_bytes`. У grid-пути
токена нет: в решении остаются только `indices` (из `extra`) — ключа `objects` не будет, потому
что тип остаётся `RECAPTCHA_V2`, а не `CLASSIFICATION_GRID`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CONFIG` | «requires ctx.http»; «audio path needs ctx.extra['audio_transcriber']» | лейн без транспорта или без ASR | см. §7 |
| `ERR_CHALLENGE_EXPIRED` | anchor без `recaptcha-token`; reload отказал (`q` или `a`) | протух release-slug / origin, «сожжённая» сессия | пришлось бы прислать `v`; повторить на новом профиле |
| `ERR_UNSOLVABLE` | нет sitekey/site_url; пустая расшифровка; userverify отверг текст | payload или качество ASR | проверить `image_b64`-ветку / аудио |
| `ERR_UNSOLVABLE` | grid-путь без `image_b64`/`prompt` | смешанный payload | не смешивать токен-задачу и сетку |

## 6. Canary / фикстуры

- `tests/solvers/recaptcha/fixtures/`: `anchor.html`, `anchor_no_token.html`, `api.js`,
  `reload_swap.json`, `reload_audio*.json`, `userverify*.json`, `audio.bin` (+ `README.md`).
- Тесты: `tests/solvers/recaptcha/test_v2_audio.py`, `test_common.py`, `test_v2_grid.py`.
- Кейс (не добавлен):

```json
{"name": "recaptcha-v2-audio", "clientKey": "<canary-key>",
 "task": {"type": "recaptcha_v2", "websiteURL": "https://<host>/login", "websiteKey": "6Lc…"},
 "expect": {"status": "ready", "solution_contains": ["gRecaptchaResponse"]}}
```

## 7. FILL / live-риски

1. **ASR не подключён в воркере.** `services/worker_http/context.py` кладёт в `ctx.extra` только
   `redis/pg/http_factory/hcaptcha_js`; ни `audio_transcriber`, ни `solver_registry` там нет.
   Значит audio-путь в проде падает `ERR_CONFIG` — это дыра интеграции, не солвера.
2. **Лейн `cv` без транспорта.** `services/worker_cv/worker.py` (строка 140) создаёт
   `SolverContext` без `http`, а тип живёт в `cv`: по коду `ERR_CONFIG` на первом же запросе.
   На живом воркере не проверено.
3. Release-slug Google меняется; при отказе солвер один раз перечитывает `api.js`. Частоту
   смены не измеряли.
4. Pass rate не измерен; `experimental=False` стоит с baseline, не по канарейке.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/recaptcha
uv run ruff check src/captcha/solvers/recaptcha
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [v2_audio.py](../src/captcha/solvers/recaptcha/v2_audio.py),
  [_audio.py](../src/captcha/solvers/recaptcha/_audio.py),
  [_common.py](../src/captcha/solvers/recaptcha/_common.py),
  [v2_grid.py](../src/captcha/solvers/recaptcha/v2_grid.py)
- grid-голова: [recaptcha_grid.py](../src/captcha/solvers/classification/recaptcha_grid.py)
- протокол: [docs/protocol.md](../docs/protocol.md) (ключи решения, errorId)
- канарейка: [runbooks/canary.md](canary.md)