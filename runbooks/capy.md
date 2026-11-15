# Runbook: capy

## 1. Snapshot

- **captcha type:** `CaptchaType.CAPY` — две картинки, offset и подписанный трек
- **antibot family:** Capy.ai — слайдер-пазл
- **mode:** `http` (CV-примитив вызывается in-process через `InteractiveBase._solve_cv`)
- **tier:** 3 (skeleton) — протокол собран, wire-константы не сняты с живой сессии
- **lane:** `cv`

## 2. Payload shape

```json
{
  "type": "capy",
  "websiteURL": "https://target.example/",
  "websiteKey": "<sitekey>"
}
```

Обязательные: `websiteURL`, `websiteKey`.

## 3. Flow

1. `GET {origin}/puzzle/get/?sitekey=…&pageurl=…` → JSON пазла (bg, piece, ключи ответа).
2. Скачать обе картинки → `_solve_cv(SLIDER, …)`.
3. Собрать трек перетаскивания (`captcha-behavior`).
4. `POST {origin}/puzzle/answer/` с offset + трек → токен.

## 4. FILL: что снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | endpoints.py origin | Network → запрос `get_js`: скопировать origin |
| 2 | endpoints.py пути | `puzzle/get/`, `puzzle/answer/` — точные пути из Network |
| 3 | solver.py `get` query | полный набор параметров запроса пазла |
| 4 | solver.py ключи ответа | как называются bg/piece/ключ в JSON |
| 5 | solver.py тело answer | Form Data запроса ответа: имена полей offset/трек |
| 6 | solver.py формат трека | опубликованный ответ capy: массив точек или строка |
| 7 | solver.py финальный токен | 2captcha отдаёт `answer` + `challenge_key`; сверить с проекцией |

## 5. Solution keys

См. `domain/solution.py` — проекция по `SolutionKind`. Всё, чего нет в каноническом
наборе, солвер кладёт в `SolverResult.extra` и уходит клиенту насквозь.

## 6. Проверка

```bash
uv run pytest -q tests/solvers/interactive -k capy
uv run python -m captcha.services.cli expected-models
```

## 7. Известные риски

- Вендор валидирует тайминг трека, а не только offset.

