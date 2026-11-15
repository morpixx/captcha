# Runbook: keycaptcha

## 1. Snapshot

- **captcha type:** `CaptchaType.KEYCAPTCHA` — блоб с сессией и набором картинок, ответ — собранный токен
- **antibot family:** KeyCAPTCHA — клик или перетаскивание по картинке
- **mode:** `http` (CV-примитив вызывается in-process через `InteractiveBase._solve_cv`)
- **tier:** 3 (skeleton) — протокол собран, wire-константы не сняты с живой сессии
- **lane:** `cv`

## 2. Payload shape

```json
{
  "type": "keycaptcha",
  "websiteURL": "https://target.example/",
  "data": "<blob or JSON>"
}
```

Принимается также `challenge`, `challenge_json`, `blob` или плоские поля `s_s_c_*`.

## 3. Flow

1. Разобрать блоб: id сессии + URLs картинок.
2. Скачать картинки; клик-задача → `_solve_cv(CLICK, …)`, иначе `_solve_cv(SLIDER, …)`.
3. Собрать токен в формате вендора.
4. `POST` на check-эндпоинт → финальный токен.

## 4. FILL: что снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | endpoints.py origin | Network, фильтр `keycaptcha`: скопировать origin |
| 2 | endpoints.py путь check | POST сразу после ручного перетаскивания |
| 3 | solver.py тело check | Form Data этого POST |
| 4 | solver.py клик vs драг | снять клик-блоб и драг-блоб, сравнить структуру |
| 5 | solver.py разделитель | как склеиваются части токена |
| 6 | solver.py формат токена | 2captcha публикует `<40 hex>|<int>` — сверить |

## 5. Solution keys

См. `domain/solution.py` — проекция по `SolutionKind`. Всё, чего нет в каноническом
наборе, солвер кладёт в `SolverResult.extra` и уходит клиенту насквозь.

## 6. Проверка

```bash
uv run pytest -q tests/solvers/interactive -k keycaptcha
uv run python -m captcha.services.cli expected-models
```

## 7. Известные риски

- Токен собирается на клиенте из нескольких частей; порядок критичен.

