# Runbook: alibaba

## 1. Snapshot

- **captcha type:** `CaptchaType.ALIBABA` (`alibaba`) — Alibaba / Aliyun: NoCaptcha v1 по HTTP (JSONP), Captcha 2.0 — только в браузере.
- **antibot family:** нет. UMID-токен устройства с `ynuf.aliapp.org`.
- **lane:** `http`, **mode:** `both` (`solve_http` — v1, `solve_browser` — 2.0), `needs_js_runtime=True`.
- **tier:** 3 — `experimental=True`; живого тенанта нет.
- **code:** `solvers/interactive/alibaba/{solver,wire,endpoints,browser}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `appkey` | да* | алиасы `appKey`, `app_key` — для v1 |
| `scene` | да* | алиасы `sceneId`, `SceneId`, `scene_id` |
| `pageurl` / `websiteURL` / `site_url` | да** | страница: Referer и вход в браузерный лейн |
| `umid_token` / `umidToken` / `umid` | нет | готовый UMID; иначе собирается best-effort |
| `umid_collect` | нет | тело запроса UMID, если клиент его снял |
| `lang` / `language` | нет | по умолчанию `cn` |
| `user_agent` / `userAgent` | нет | UA |
| `seed` | нет | детерминизм траектории в CV |

`*` — пара обязательна для HTTP-пути. `**` — если пары нет, но есть страница, солвер эскалирует в браузер.

```json
{"type": "alibaba", "appkey": "<appkey>", "scene": "<scene>", "websiteURL": "https://target.example/"}
```

## 3. Endpoints / flow

`endpoints.py`: `GET https://cf.aliyun.com/nocaptcha/get.jsonp`, `POST https://cf.aliyun.com/nocaptcha/analyze.jsonp`, UMID — `POST https://ynuf.aliapp.org/service/um.json`.

1. Нет `appkey`/`scene` и есть страница → `ERR_NEEDS_BROWSER` (v2 подписывает `CaptchaVerifyParam` только страница). Нет ни того, ни другого → `ERR_UNSOLVABLE`.
2. UMID: `POST /service/um.json` (best-effort; провал не валит задачу — это отпечаток, а не челлендж).
3. `GET get.jsonp` (`callback=cb, ua, umidToken, scene, appkey, lang, _`) → JSONP → `wire.parse_challenge` (`csessionid`, `sig`, `token`, `bg`, `front`, вариант, промпт).
4. `variant_kind`: слайдер → CV `SLIDER` (`bg`+`piece`); поворот → `ROTATE`; иначе `CLICK` с текстом цели. Картинки — URL или `data:`.
5. `POST analyze.jsonp` form-encoded, тело из `wire.analyze_body`; `wire.find_result` → `nc_token` или `CaptchaVerifyParam`.
6. `extra["data"]` = весь найденный блок результата.

Браузерный лейн (`browser.py`): открывает страницу, pol-лит `CaptchaVerifyParam` (POLL_JS, по умолчанию 90 с).

## 4. Solution keys

`SolutionKind.DATA_NEST` → `data: {"tokens": <nc_token|CaptchaVerifyParam>}` (`domain/solution.py` для `ALIBABA`). `extra.data` проходит насквозь.

```json
{"data": {"tokens": "<nc_token>", "nc_sid": "…", "nc_token": "…"}}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_NEEDS_BROWSER` | `aliyun Captcha 2.0 signs CaptchaVerifyParam in the page` | нет `appkey`/`scene`, но есть страница | принять эскалацию или дать пару |
| `ERR_UNSOLVABLE` | `needs appkey + scene (v1), or pageurl for the browser lane` | пустой payload | требовать пару или URL |
| `ERR_UNSOLVABLE` | `slider challenge has no front image`, `click challenge has no target text`, `image body is empty`, `broken data URI` | вендор сменил форму челленджа | смотреть `keys` в контексте |
| `ERR_CHALLENGE_EXPIRED` | `alibaba get rejected the challenge` (400/401/403/404/408/410/429) | челлендж одноразовый/протух | новый раунд |
| `ERR_CONFIG` | `alibaba solver needs ctx.http` | контекст без HTTP-клиента (не штатный воркер) | собрать контекст как в `worker_http` |
| `ERR_GENERIC` | `alibaba analyze failed` / `not JSON` | 5xx/не-JSONP | `status` в контексте |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/interactive/test_alibaba.py` (JSONP, варианты, эскалация, ошибки); общий лейн-тест — `tests/solvers/interactive/test_lanes.py`.
- Живых дампов JSONP нет. Канареечной фикстуры нет.
- Кейс (не добавлен):

```json
{"name": "alibaba-live", "clientKey": "<canary-key>",
 "task": {"type": "alibaba", "appkey": "<appkey>", "scene": "<scene>", "websiteURL": "https://<host>/"},
 "expect": {"status": "ready", "solution_contains": ["data"]}}
```

## 7. FILL / live-риски

1. `alibaba/wire.py` помечает `FILL` на точных именах полей `analyze` (`answer`/`track`): тело собрано по разбору, не по живому захвату.
2. `alibaba/browser.py` (`FILL`) — контейнер с `CaptchaVerifyParam` может отличаться на разных сайтах.
3. UMID best-effort: пустой UMID не блокирует задачу, но и не помогает — сколько сайтов его требуют, не проверено.
4. v2 (Captcha 2.0) в HTTP-пути не поддерживается принципиально: подпись делает страница.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/interactive/test_alibaba.py tests/solvers/interactive/test_lanes.py
uv run ruff check src/captcha/solvers/interactive/alibaba
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [solver.py](../src/captcha/solvers/interactive/alibaba/solver.py), [wire.py](../src/captcha/solvers/interactive/alibaba/wire.py), [endpoints.py](../src/captcha/solvers/interactive/alibaba/endpoints.py), [browser.py](../src/captcha/solvers/interactive/alibaba/browser.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
- браузерный лейн: [browser.md](browser.md)
