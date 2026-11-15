# Runbook: recaptcha_v3 (+ enterprise)

## 1. Snapshot

- **captcha types:** `RECAPTCHA_V3`, `RECAPTCHA_V3_ENTERPRISE` — anchor → protobuf reload → `rresp`.
- **antibot family:** Google reCAPTCHA v3; `AntibotEngine` нет.
- **lane:** `http`, **mode:** `http` (`needs_js_runtime=False`).
- **tier:** 3 — `experimental=True`: схема protobuf-полей снята с публичного бандла, live не сверялась.
- **code:** `solvers/score_based/recaptcha_v3.py` + `solvers/recaptcha/{_common,_proto}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница; из неё `co` и referer |
| `websiteKey` | да | sitekey |
| `action` | нет | action-тег, дефолт `verify`; пустая строка — `ERR_UNSOLVABLE` |
| `min_score` | нет | 0..1, дефолт 0.3; **только валидируется и эхом возвращается** |
| `bg` | нет | телеметрия (поле 4); иначе `ctx.extra["recaptcha_bg"]` |
| `fingerprint` | нет | dict полей 5/7/16/20/22/25/28/29; иначе `ctx.extra["recaptcha_fingerprint"]` |
| `v` / `version` | нет | release-slug |

```json
{"type": "recaptcha_v3", "websiteURL": "https://target.example/login",
 "websiteKey": "6Lc…", "action": "login", "min_score": 0.5}
```

## 3. Endpoints / flow

1. `GET <anchor>` c `size=invisible`, `ar=1`, `k`, `co`, `hl`, `v`, `cb` → `recaptcha-token`
   (при промахе — один ре-резолв slug из `api.js`/`enterprise.js`).
2. `POST <reload>?k=` с `content-type: application/x-protobuffer`, тело — `encode_reload_body`
   (`_proto.py`): поле 1 `v`, 2 `c`, 4 `bg`, 6 `reason`, 8 `action`, 14 `k` + fingerprint-поля.
3. Ответ — XSSI-обёрнутый `["rresp","<token>",…]`; парсер терпим к вложенности, битому JSON и
   «protobuf-подобному» телу.

## 4. Solution keys

`SolutionKind.RECAPTCHA`: `gRecaptchaResponse` + `userAgent` (если есть). `extra` проходит
насквозь: в ответе также `action` и `min_score`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CONFIG` | «requires ctx.http» | контекст без HTTP-клиента (не штатный воркер) | собрать контекст как в `worker_http` |
| `ERR_UNSOLVABLE` | нет sitekey/site_url; пустой `action`; `min_score` вне [0,1]; reload без `rresp` | payload или смена схемы ответа | сверить `status`/`body_head` в контексте |
| `ERR_CHALLENGE_EXPIRED` | anchor без токена | протух release-slug или origin | прислать `v` из живой сессии |

## 6. Canary / фикстуры

- `tests/solvers/recaptcha/fixtures/`: `v3_reload.json`, `v3_reload_empty.json`,
  `v3_reload_nested.json`, `v3_reload_malformed.txt`.
- Тест: `tests/solvers/score_based/test_recaptcha_v3.py`.
- Кейс (не добавлен):

```json
{"name": "recaptcha-v3", "clientKey": "<canary-key>",
 "task": {"type": "recaptcha_v3", "websiteURL": "https://<host>/", "websiteKey": "6Lc…", "action": "login"},
 "expect": {"status": "ready", "solution_contains": ["gRecaptchaResponse"]}}
```

## 7. FILL / live-риски

1. `min_score` **не влияет на результат**: скор виден только на `/siteverify` у Google. Солвер
   возвращает токен и эхо-значение; обещать «скор ≥ 0.5» нельзя.
2. Номера protobuf-полей и их типы — из публичного `releases/<slug>/recaptcha__*.js`; при
   смене бандла запрос станет пустым и упадёт в `ERR_UNSOLVABLE`. Проверки на живой смене нет.
3. Fingerprint-телеметрия почти всегда пустая (payload-режим): качество токена на строгих
   сайтах не измерено.
4. Pass rate не измерен; тип продаётся как experimental.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/score_based
uv run ruff check src/captcha/solvers/score_based src/captcha/solvers/recaptcha
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [recaptcha_v3.py](../src/captcha/solvers/score_based/recaptcha_v3.py),
  [_proto.py](../src/captcha/solvers/recaptcha/_proto.py),
  [_common.py](../src/captcha/solvers/recaptcha/_common.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
