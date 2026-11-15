# Runbook: geetest_v3

## 1. Snapshot

- **captcha type:** `CaptchaType.GEETEST_V3` — слайдер с разрезом картинки и AES-обёрнутым payload
- **antibot family:** GeeTest — slider-капча третьего поколения
- **mode:** `http` (CV-примитив вызывается in-process через `InteractiveBase._solve_cv`)
- **tier:** 3 (skeleton) — протокол собран, wire-константы не сняты с живой сессии
- **lane:** `cv`

## 2. Payload shape

```json
{
  "type": "geetest_v3",
  "websiteURL": "https://target.example/login",
  "gt": "<gt>",
  "challenge": "<challenge>",
  "api_server": "<optional host>"
}
```

Обязательные: `websiteURL`, `gt`, `challenge`. `api_server` — переопределение хоста
(легаси-клиенты шлют его как `api_server`).

## 3. Flow

1. `GET /gettype.php?gt=…&callback=…` → путь к JS-бандлу (нужен только для версии `ep`).
2. `GET /get.php?gt=…&challenge=…&lang=…&client_type=web` → JSON с `bg`/`slice` и флагами.
3. Скачать обе картинки, отдать в `_solve_cv(SLIDER, …)` — вернётся `offset`.
4. `payload.build_w_v3`: JSON-тело → AES-128-CBC (ключ = сгенерированный `guid`, IV = 16 ASCII `'0'`) → hex.
5. Вторая половина `w` — RSA-PKCS1v15 того же ключа; публичный модуль берётся из бандла (FILL #2).
6. `POST /ajax.php` (или второй `/get.php` — FILL #12) с `w`, `challenge`, `gt` → `validate`/`seccode`.

## 4. FILL: что снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | payload.py `AES_IV` | подтвердить, что IV всё ещё 16 ASCII `'0'` — открыть бандл, найти `iv` |
| 2 | payload.py `V3_RSA_MODULUS_HEX` | `setPublic(<N>,<e>)` в `slide.<ver>.js`; без него `w` не собрать |
| 3 | payload.py `V3_RSA_EXPONENT` | там же, второй аргумент `setPublic` |
| 4 | payload.py `V3_SDK_VERSION` | `ep.v` из одного расшифрованного тела verify |
| 5 | payload.py `userresponse` | реальное значение — `H(distance, challenge)`; снять тройку и портировать H |
| 6 | payload.py `imgload` | реальный диапазон мс между запросом картинок и ответом |
| 7 | payload.py `aa` | `getAA(track, c, s)`; нужны `c`/`s` из картинки и формат трека |
| 8 | payload.py `ep` | форма объекта, включая `tm` |
| 9 | v3.py выбор эндпоинта | какой вызов реально отдаёт `bg`/`slice`: `ajax.php` или второй `get.php` |
| 10 | v3.py распаковка bg | порядок обратной перестановки 52 тайлов перед `_solve_cv` |

## 5. Solution keys

См. `domain/solution.py` — проекция по `SolutionKind`. Всё, чего нет в каноническом
наборе, солвер кладёт в `SolverResult.extra` и уходит клиенту насквозь.

## 6. Проверка

```bash
uv run pytest -q tests/solvers/interactive -k geetestv3
uv run python -m captcha.services.cli expected-models
```

## 7. Известные риски

- Модуль RSA меняется с релизом бандла — это самый частый источник «перестало работать».
- `userresponse` не равен сырому offset: сервер ждёт хеш, поэтому неверный H даёт `fail`
  при визуально правильном ответе.

