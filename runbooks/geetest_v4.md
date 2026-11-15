# Runbook: geetest_v4

## 1. Snapshot

- **captcha type:** `CaptchaType.GEETEST_V4` — тот же слайдер, другой набор полей и собственный proof-of-work
- **antibot family:** GeeTest — слайдер четвёртого поколения
- **mode:** `http` (CV-примитив вызывается in-process через `InteractiveBase._solve_cv`)
- **tier:** 3 (skeleton) — протокол собран, wire-константы не сняты с живой сессии
- **lane:** `cv`

## 2. Payload shape

```json
{
  "type": "geetest_v4",
  "websiteURL": "https://target.example/login",
  "captchaId": "<captcha_id>"
}
```

Обязательные: `websiteURL`, `captcha_id`.

## 3. Flow

1. `GET /v4/get` (`captcha_id`, `client_type`, `lang`) → `lot_number`, картинки, параметры PoW.
2. PoW решается локально (`payload.solve_pow`).
3. Картинки → `_solve_cv(SLIDER, …)` → offset.
4. `payload.build_w_v4`: тело → AES-128-CBC → hex, вторая половина — RSA-ключ.
5. `POST /v4/verify` с `captcha_id`, `lot_number`, `w` → токен.

## 4. FILL: что снять с живой сессии

| # | где | что нужно |
|---|---|---|
| 1 | payload.py `V4_RSA_MODULUS_HEX` | конструктор в `gcaptcha4.js`, тот же `setPublic` |
| 2 | payload.py `V4_BZJ5` и группы среза lot | перевывести: каждый релиз SDK переписывает выражение среза |
| 3 | payload.py v4-константы тела | `biht`, `ep`, `geetest`, `em`, `gee_guard` — сверить по одному расшифрованному `w` |
| 4 | v4.py `/verify` | GET с query или form POST — снять из Network |
| 5 | v4.py ключи ответа | новые сборки отдают только `seccode`, старые — `captcha_output`/`pass_token`/`gen_time` |

## 5. Solution keys

См. `domain/solution.py` — проекция по `SolutionKind`. Всё, чего нет в каноническом
наборе, солвер кладёт в `SolverResult.extra` и уходит клиенту насквозь.

## 6. Проверка

```bash
uv run pytest -q tests/solvers/interactive -k geetestv4
uv run python -m captcha.services.cli expected-models
```

## 7. Известные риски

- Набор полей `w` меняется чаще, чем в v3: при поломке первым делом сверять тело.

