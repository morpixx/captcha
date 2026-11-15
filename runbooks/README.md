# Runbooks

Один файл на тип капчи плюс операционные runbook-и лейнов/процессов. Шаблон —
[`_template.md`](_template.md); структура десяти заголовков обязательна. Правило
`AGENTS.md`: новый солвер без runbook-а не мержится.

## Операционные

| файл | что внутри |
|---|---|
| [deploy.md](deploy.md) | выкатка, проверки, откат, браузерная нода, canary, TLS |
| [browser.md](browser.md) | лейн `browser`: провайдер, gate, heartbeat, RAM, таймауты, утечки профилей |
| [canary.md](canary.md) | фикстуры, 6-часовой прогон, type health, алерты, пустой набор |
| [hcaptcha.md](hcaptcha.md) | тип hCaptcha: HTTP-путь, браузер, Riot/Steam/Epic, passive-ключи |

## Типовые: 62 типа каталога

Статус: **live** — файл есть; **pending** — runbook ещё не написан (тип в L0–L2, продаже не
подлежит). Список типов и лейнов сверяется с `domain/catalog.py`; порядок — по семействам.

### PoW / protocol

| тип | лейн | runbook |
|---|---|---|
| `friendly_captcha` | protocol | pending |
| `altcha` | protocol | pending |

### OCR / audio (cv)

| тип | лейн | runbook |
|---|---|---|
| `text_captcha` | cv | pending |
| `math_image` | cv | pending |
| `image_to_text` | cv | pending |
| `audio_captcha` | cv | pending |

### Геометрия и сетка (cv)

| тип | лейн | runbook |
|---|---|---|
| `rotate` | cv | pending |
| `slider` | cv | pending |
| `click` | cv | pending |
| `coordinates` | cv | pending |
| `bounding_box` | cv | pending |
| `classification_grid` | cv | pending |
| `draw_around` | cv | pending |
| `drag_drop` | cv | pending |

### hCaptcha (browser)

| тип | лейн | runbook |
|---|---|---|
| `hcaptcha` | browser | [hcaptcha.md](hcaptcha.md) |
| `hcaptcha_enterprise` | browser | [hcaptcha.md](hcaptcha.md) |

### reCAPTCHA (cv/http)

| тип | лейн | runbook |
|---|---|---|
| `recaptcha_v2` | cv | pending |
| `recaptcha_v2_enterprise` | cv | pending |
| `recaptcha_v2_invisible` | cv | pending |
| `recaptcha_v2_callback` | cv | pending |
| `recaptcha_v3` | http | pending |
| `recaptcha_v3_enterprise` | http | pending |

### GeeTest (cv)

| тип | лейн | runbook |
|---|---|---|
| `geetest_v3` | cv | [geetest_v3.md](geetest_v3.md) |
| `geetest_v4` | cv | [geetest_v4.md](geetest_v4.md) |

### DataDome (http)

| тип | лейн | runbook |
|---|---|---|
| `datadome_slider` | http | pending |
| `datadome` | http | pending |

### FunCaptcha / Arkose (cv/http)

| тип | лейн | runbook |
|---|---|---|
| `funcaptcha` | cv | pending |
| `funcaptcha_bda` | http | pending |
| `funcaptcha_enterprise` | cv | pending |

### Turnstile / Cloudflare (http)

| тип | лейн | runbook |
|---|---|---|
| `turnstile` | http | pending |
| `turnstile_invisible` | http | pending |
| `cf_challenge` | http | pending |

### Антибот-движки (http)

| тип | лейн | runbook |
|---|---|---|
| `akamai_sensor` | http | pending |
| `akamai_sbsd` | http | pending |
| `kasada` | http | pending |
| `perimeterx` | http | pending |
| `imperva` | http | pending |
| `castle` | http | pending |
| `tspd` | http | pending |
| `cybersiara` | http | pending |
| `twitch_integrity` | http | pending |
| `botguard` | http | pending |
| `botdeflector` | http | pending |
| `awswaf` | cv | pending |

### Interactive-вендоры (cv/http)

| тип | лейн | runbook |
|---|---|---|
| `shein` | cv | pending |
| `mtcaptcha` | cv | pending |
| `tencent` | cv | pending |
| `yidun` | cv | pending |
| `alibaba` | cv | pending |
| `prosopo` | cv | pending |
| `captchafox` | cv | pending |
| `vk` | cv | pending |
| `yandex` | cv | pending |
| `temu` | http | pending |
| `binance` | cv | pending |
| `hunt` | cv | pending |
| `basilisk` | cv | pending |
| `cutcaptcha` | cv | pending |
| `capy` | cv | [capy.md](capy.md) |
| `keycaptcha` | cv | [keycaptcha.md](keycaptcha.md) |
| `lemin` | cv | [lemin.md](lemin.md) |
| `atbcaptcha` | cv | pending |

Итого: 7 типов покрыты файлом, 55 pending. Операционные runbook-и покрывают лейны, но не
заменяют типовой: у типа свои payload, failure modes и модель.

## Как писать

1. Скопировать `_template.md` в `runbooks/<captcha_type>.md`, сохранить десять заголовков
   и их порядок.
2. Заполнять по коду, а не по памяти: §2 — `docs/protocol.md` + `Task.payload`, §3 — реальный
   `solve_http`/`solve_browser`, §6 — коды `domain/errors.py` и фактически наблюдавшиеся
   отказы.
3. Неизмеренное помечать явно («не измерено», «FILL»), а не выдумывать цифры.
4. Ссылаться на существующие файлы; если ссылка ведёт на чужой документ (например
   `docs/hcaptcha-sites.md`), он должен быть в дереве на момент мержа.
5. Обновлять §9 (incident log) при каждом инциденте: дата — триггер — фикс.
