# Runbook: turnstile (+ turnstile_invisible)

## 1. Snapshot

- **captcha types:** `TURNSTILE`, `TURNSTILE_INVISIBLE` — виджет Cloudflare Turnstile, ответ = `cf-turnstile-response` токен.
- **antibot family:** `AntibotEngine.CLOUDFLARE` (builder — `engines/cloudflare/builder.py`).
- **lane:** `http`, **mode:** `both`, но токен минтит **только браузерный лейн**: HTTP-путь
  делает bootstrap и эскалирует.
- **tier:** 3 — `experimental=True` (`version 0.1.0`).
- **code:** `solvers/antibot/cloudflare/{turnstile,turnstile_wire,turnstile_page,_cookies,_common}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `websiteURL` | да | страница, где стоит виджет; токен привязан к hostname |
| `websiteKey` / `sitekey` | да | sitekey; печатный, без пробелов, ≤256 символов |
| `action` | нет | `[A-Za-z0-9_-]{1,32}`; иначе `ERR_UNSOLVABLE` |
| `cdata` / `cData` / `chlPageData` | нет | ≤255 символов |
| `invisible` / `isInvisible` | нет | `1/true/yes/on`; тип `turnstile_invisible` включает сам |
| `enterprise` | нет | флаг, а не отдельный тип |
| `userAgent` | нет | override UA для bootstrap-пробы |
| `cookies` | нет | начальный jar для браузерной сессии |

```json
{"type": "turnstile", "websiteURL": "https://target.example/", "websiteKey": "0x4AAA…",
 "action": "login", "cdata": "..."}
```

## 3. Endpoints / flow

Konstants — `endpoints.py`: `challenges.cloudflare.com`, `/turnstile/v0/api.js`,
бандл `/turnstile/v0/b/<hash>/api.js`, виджет
`/cdn-cgi/challenge-platform/h/<variant>/turnstile/if/ov2/av0/<sitekey>`,
минт `…/turnstile/f/av0/rch/<segment>/<sitekey>` (схема тела закрыта).

HTTP-половина (`turnstile_wire.bootstrap`):
1. `GET /turnstile/v0/api.js?render=explicit&onload=…` → бандл (redirect/Location/тело; только
   `https` и только `challenges.cloudflare.com`).
2. `GET <bundle>` → вариант challenge-platform (`/h/([a-z])/turnstile/`, дефолт `b`).
3. `GET <widget>` — проба документа виджета; `4xx` — заметка, не провал.
4. Схемы минт-POST нет → `NeedsBrowserError` с тем, что узнали (`bundle/variant/widget_status`).

Браузерная половина:
1. Одна свежая сессия (`ctx.browser`), навигация на `websiteURL`.
2. `drive_widget` вставляет виджет (или берёт существующий) и ждёт `cf-turnstile-response`;
   окно `GRACE_S = 12`, затем клик по чекбоксу (если не invisible) + `Tab`/`Space` и второй проход
   на остаток бюджета.
3. Валидация токена: `^\d\.[A-Za-z0-9._~+/=-]{10,}$` и `>= 20` символов; эхо sitekey токеном
   не считается. Origin сверяется с hostname из `websiteURL`.

## 4. Solution keys

`SolutionKind.TURNSTILE`: `token`, `userAgent` — UA страницы, которая минтила токен (клиент
обязан подставить его при сабмите). Ничего больше в ответ не добавляется.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | «sitekey is required» / «malformed sitekey» / «websiteURL is required» | payload | поправить поля |
| `ERR_UNSOLVABLE` | «the page left the expected origin» | редирект/подмена хоста | проверить `websiteURL`, прокси |
| `ERR_UNSOLVABLE` | «the widget rejected the task (error N)» | терминальные коды: 110200, 110420, 110430, 110440, 110520, 110600, 110610, 110620, 110630, 400020, 400070 | повтор с тем же payload бессмыслен |
| `ERR_CHALLENGE_EXPIRED` | «turnstile issued no token (…)» | виджет не успел/не показался | бюджет, прокси, новый профиль |
| `ERR_NEEDS_BROWSER` | HTTP-лейн эскалирует | нет провайдера браузера в этом воркере | нормальный путь, см. §7 |
| `ERR_CONFIG` | `ctx.browser` is None / «provider exposes neither session() nor acquire()» | лейн/деплой | проверить `WORKER_BROWSER_*` |

## 6. Canary / фикстуры

- `tests/solvers/antibot/fixtures/turnstile/`: `api_js.js`, `bundle_api.js`, `widget.html`.
- Тесты: `tests/solvers/antibot/test_cloudflare_wire.py`,
  `tests/solvers/antibot/test_cloudflare_turnstile.py`.
- Кейс (не добавлен):

```json
{"name": "turnstile-live", "clientKey": "<canary-key>",
 "task": {"type": "turnstile", "websiteURL": "https://<host>/", "websiteKey": "0x4AAA…"},
 "expect": {"status": "ready", "solution_contains": ["token", "userAgent"]}}
```

## 7. FILL / live-риски

1. `api.js` → бандл → вариант — публичная часть; **байтовая схема минт-POST не воспроизведена** ни
   одним публичным разбором. HTTP-лейн поэтому не может вернуть токен by design, а не «пока не
   доделали».
2. `variant` (сейчас `b`) и hash бандла ротируются с релизами Cloudflare — проба только
   записывает, что видела.
3. Браузерный бюджет по умолчанию 90 с (`DEFAULT_TIMEOUT_S`), минимум 10 с; per-task
   hard timeout воркера — 180–300 с (`docs/protocol.md`).
4. Pass rate не измерен. `engines/cloudflare/builder.py` всё ещё содержит `# FILL` по shim-слою —
   HTTP-путь его не использует.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/antibot/test_cloudflare_wire.py tests/solvers/antibot/test_cloudflare_turnstile.py
uv run ruff check src/captcha/solvers/antibot/cloudflare
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [turnstile.py](../src/captcha/solvers/antibot/cloudflare/turnstile.py),
  [turnstile_wire.py](../src/captcha/solvers/antibot/cloudflare/turnstile_wire.py),
  [turnstile_page.py](../src/captcha/solvers/antibot/cloudflare/turnstile_page.py),
  [endpoints.py](../src/captcha/solvers/antibot/cloudflare/endpoints.py)
- разбор: `docs/reverse-2026.md` §3
- протокол: [docs/protocol.md](../docs/protocol.md)
- лейн браузера: [runbooks/browser.md](browser.md)
