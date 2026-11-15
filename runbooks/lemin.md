# Runbook: lemin

## 1. Snapshot

- **captcha type:** `CaptchaType.LEMIN` — bootstrap-запрос, картинка, pre-validate, финальный ответ формы
- **antibot family:** Lemin (leminnow) — слайдер/пазл
- **mode:** `http` (CV-примитив вызывается in-process через `InteractiveBase._solve_cv`)
- **tier:** 3 (skeleton) — протокол собран, wire-константы не сняты с живой сессии
- **lane:** `cv`

## 2. Payload shape

```json
{
  "type": "lemin",
  "websiteURL": "https://target.example/",
  "websiteKey": "<sitekey>"
}
```

Обязательные: `websiteURL`, `websiteKey`.

## 3. Flow

1. Bootstrap-запрос к API лемина → challenge с картинками и id.
2. Скачать картинку → `_solve_cv(SLIDER, …)` (или CLICK, если задача кликовая).
3. `POST /pre-validate` → промежуточный токен.
4. Собрать `lemin_answer` + `lemin_challenge_id` → финальный ответ.

## 4. FILL: что снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | endpoints.py bootstrap | первый запрос скрипта лемина в Network |
| 2 | endpoints.py pre-validate | POST после ручного решения |
| 3 | endpoints.py имя формы | `lemin_answer` в форме страницы |
| 4 | solver.py тело bootstrap | Request Payload первого запроса |
| 5 | solver.py ключи ответа | как называются картинка/поля в ответе |
| 6 | solver.py envelope pre-validate | структура ответа |
| 7 | solver.py финальная сборка | какие поля ждёт страница |
| 8 | solver.py формат ответа | строка или объект |
| 9 | solver.py тайминг | есть ли проверка времени прохождения |

## 5. Solution keys

См. `domain/solution.py` — проекция по `SolutionKind`. Всё, чего нет в каноническом
наборе, солвер кладёт в `SolverResult.extra` и уходит клиенту насквозь.

## 6. Проверка

```bash
uv run pytest -q tests/solvers/interactive -k lemin
uv run python -m captcha.services.cli expected-models
```

## 7. Известные риски

- Двухшаговая валидация: промежуточный токен живёт ограниченное время.

