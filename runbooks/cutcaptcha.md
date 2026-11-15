# Runbook: cutcaptcha

## 1. Snapshot

- **captcha type:** `CaptchaType.CUTCAPTCHA` — слайдер-пазл с треком указателя, ответ = `token`.
- **antibot family:** CutCaptcha (собственный вендор); `AntibotEngine` нет.
- **lane:** `cv` (`worker_cv` выдаёт per-task `ctx.http`), **mode:** `http` только.
- **tier:** 3 — `experimental=True`: эндпоинты взяты из публичных интеграций, с живого
  тенанта не снимались (об этом прямо говорит `endpoints.py`).
- **code:** `solvers/interactive/cutcaptcha/{solver,wire,endpoints}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | хост встраивания; уходит в `host` запроса challenge |
| `apikey` | да | публичный ключ площадки; без него `ERR_UNSOLVABLE` |
| `host` | нет | переопределяет `websiteURL`/`pageurl` в запросе challenge |

```json
{"type": "cutcaptcha", "websiteURL": "https://target.example/", "apikey": "<apikey>"}
```

## 3. Endpoints / flow

`solvers/interactive/cutcaptcha/endpoints.py`: `api.cutcaptcha.com`.

1. `GET /api/v1/challenge?apikey=&host=` → `{cid, bgUrl, pieceUrl}`.
2. `GET <bg>`, `GET <piece>` — обе картинки обязательны.
3. CV slider in-process → горизонтальный offset.
4. Трек: `wire.drag_track(offset)` — безье из `captcha.behavior.bezier_path` +
   `humanize_timing`, 24 точки (минимум `MIN_TRACK_POINTS = 12`), точки `{x, y, t}`,
   вертикальный дрейф `TRACK_DRIFT_PX = 3.0`.
5. `POST /api/v1/verify` (JSON) — `{cid, offset, track, apikey}` → `{token}`.

Вендор проигрывает трек: прямая линия от 0 до offset — это ровно то, что ловит проверка.

## 4. Solution keys

`SolutionKind.TOKEN`: `token` + `userAgent` (если есть). `extra` проходит насквозь:
в ответе видны `offset` и `cid`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `apikey`; нет cid/bg/piece; offset <= 0 после коррекции | битый payload / детект не сошёлся | проверить картинки, `match_score` |
| `ERR_CHALLENGE_EXPIRED` | `code` expired/challenge_expired, текст expire/timeout/not found | раунд протух | ускорить шаги 1–5 |
| `ERR_CONFIG` | requires ctx.http | контекст без HTTP-клиента (не штатный воркер) | собрать контекст как в `worker_cv` |
| `ERR_INTERNAL` (`CaptchaError`) | token при `error`/fail-флаге; или token нет | vendor-ответ не по схеме | логи `success`/`error` |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_cutcaptcha.py`.
- Фикстур типа нет; трек детерминирован при фиксированном `rng` — тест на форму кривой
  пишется без сети.
- Кейс (не добавлен):

```json
{"name": "cutcaptcha-live", "clientKey": "<canary-key>",
 "task": {"type": "cutcaptcha", "websiteURL": "https://<host>/", "apikey": "<apikey>"},
 "expect": {"status": "ready", "solution_contains": ["token"]}}
```

## 7. FILL / live-risks

1. Эндпоинты и тела **не сняты с живого тенанта** — это и есть причина `experimental`.
2. Транспорт есть: `services/worker_cv/worker.py` выдаёт per-task `ctx.http` (`_run_one` →
   `context.task_context`), так что шаг 1 идёт через клиент задачи; `ERR_CONFIG` из §5
   возможен только на контексте без клиента. По коду; live не проверено.
3. Не проверено: минимальная длина трека (12 — допущение), требование к `t`-монотонности,
   реакция на неверный `host`.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_cutcaptcha.py
uv run ruff check src/captcha/solvers/interactive/cutcaptcha
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/cutcaptcha/solver.py),
  [wire.py](../src/captcha/solvers/interactive/cutcaptcha/wire.py),
  [endpoints.py](../src/captcha/solvers/interactive/cutcaptcha/endpoints.py)
- поведение: `src/captcha/behavior/` (безье и тайминги)
- протокол: [docs/protocol.md](../docs/protocol.md)
