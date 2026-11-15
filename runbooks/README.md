# Runbooks

Один файл на тип капчи плюс операционные runbook-и лейнов/процессов. Шаблон —
[`_template.md`](_template.md); правило `AGENTS.md`: новый солвер без runbook-а не мержится.

## Операционные

| файл | что внутри |
|---|---|
| [deploy.md](deploy.md) | выкатка, проверки, откат, браузерная нода, canary, TLS |
| [browser.md](browser.md) | лейн `browser`: провайдер, gate, heartbeat, RAM, таймауты, утечки профилей |
| [canary.md](canary.md) | фикстуры, 6-часовой прогон, type health, алерты, пустой набор |
| [hcaptcha.md](hcaptcha.md) | тип hCaptcha: HTTP-путь, браузер, Riot/Steam/Epic, passive-ключи |

## Покрытие

Список типов сверяется с `src/captcha/domain/enums.py` и лейнами из
`src/captcha/domain/catalog.py`. Один файл может покрывать несколько типов одного
семейства — это отмечено в колонке «файл».

**Сейчас: 36 из 62 вендорских типов + `hopscotch` (кастомный, вне 62). Pending — 26.**

Статус в таблицах: **live** — файл есть; **pending** — runbook ещё не написан. «Live» здесь
значит «документ есть», а не «тип проверен на живом сайте»: `experimental=True` у солвера
важнее статуса файла.

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
| `rotate` | cv | [rotate.md](rotate.md) |
| `slider` | cv | [slider.md](slider.md) (он же делегат для capy/keycaptcha/lemin/datadome_slider) |
| `click` | cv | [click.md](click.md) |
| `coordinates` | cv | [click.md](click.md) (общий солвер) |
| `bounding_box` | cv | [bounding_box.md](bounding_box.md) |
| `classification_grid` | cv | [classification_grid.md](classification_grid.md) |
| `draw_around` | cv | [draw_around.md](draw_around.md) |
| `drag_drop` | cv | [drag_drop.md](drag_drop.md) |
| `hopscotch` | cv | [hopscotch.md](hopscotch.md) — кастомный тип, не входит в 62 |

### hCaptcha (browser)

| тип | лейн | runbook |
|---|---|---|
| `hcaptcha` | browser | [hcaptcha.md](hcaptcha.md) |
| `hcaptcha_enterprise` | browser | [hcaptcha.md](hcaptcha.md) |

**Same-origin.** hCaptcha привязывает токен к sitekey-хосту **и к странице, которая его
сминтила**: токен, полученный на одном домене, не принимается на другом. `host` обязан
совпадать с хостом `websiteURL`; при расхождении HTTP-путь отдаёт терминальный
`ERR_UNSOLVABLE` (`ERROR_CAPTCHA_UNSOLVABLE`), а не мёртвый токен
(`solvers/hcaptcha/wire.py: params_from_task`; в `getTaskResult` сейчас уходит
внутренний код — см. §6 hcaptcha.md). Для Riot/Steam это значит: минт и сабмит — на одном
origin, `host=account.riotgames.com` не переиспользуется для другого домена.

### reCAPTCHA

| тип | лейн | runbook |
|---|---|---|
| `recaptcha_v2` | cv | [recaptcha_v2.md](recaptcha_v2.md) |
| `recaptcha_v2_enterprise` | cv | [recaptcha_v2.md](recaptcha_v2.md) |
| `recaptcha_v2_invisible` | cv | [recaptcha_v2.md](recaptcha_v2.md) |
| `recaptcha_v2_callback` | cv | [recaptcha_v2.md](recaptcha_v2.md) |
| `recaptcha_v3` | http | [recaptcha_v3.md](recaptcha_v3.md) |
| `recaptcha_v3_enterprise` | http | [recaptcha_v3.md](recaptcha_v3.md) |

### GeeTest (cv)

| тип | лейн | runbook |
|---|---|---|
| `geetest_v3` | cv | [geetest_v3.md](geetest_v3.md) |
| `geetest_v4` | cv | [geetest_v4.md](geetest_v4.md) |

### DataDome (http)

| тип | лейн | runbook |
|---|---|---|
| `datadome_slider` | http | [datadome.md](datadome.md) |
| `datadome` | http | [datadome.md](datadome.md) |

### FunCaptcha / Arkose

| тип | лейн | runbook |
|---|---|---|
| `funcaptcha` | cv | [funcaptcha.md](funcaptcha.md) |
| `funcaptcha_bda` | http | [funcaptcha.md](funcaptcha.md) |
| `funcaptcha_enterprise` | cv | [funcaptcha.md](funcaptcha.md) |

### Turnstile / Cloudflare

| тип | лейн | runbook |
|---|---|---|
| `turnstile` | http | [turnstile.md](turnstile.md) |
| `turnstile_invisible` | http | [turnstile.md](turnstile.md) |
| `cf_challenge` | http | [cf_challenge.md](cf_challenge.md) |

### Антибот-движки (http)

| тип | лейн | runbook |
|---|---|---|
| `akamai_sensor` | http | [akamai_sensor.md](akamai_sensor.md) |
| `akamai_sbsd` | http | [akamai_sbsd.md](akamai_sbsd.md) |
| `kasada` | http | [kasada.md](kasada.md) |
| `perimeterx` | http | pending |
| `imperva` | http | pending |
| `castle` | http | pending |
| `tspd` | http | pending |
| `cybersiara` | http | pending |
| `twitch_integrity` | http | pending |
| `botguard` | http | pending |
| `botdeflector` | http | pending |
| `awswaf` | cv | pending |

### Interactive-вендоры

| тип | лейн | runbook |
|---|---|---|
| `shein` | cv | pending |
| `mtcaptcha` | cv | pending |
| `tencent` | cv | [tencent.md](tencent.md) |
| `yidun` | cv | [yidun.md](yidun.md) |
| `alibaba` | cv | pending |
| `prosopo` | browser | pending |
| `captchafox` | browser | pending |
| `vk` | http | pending |
| `yandex` | browser | pending |
| `temu` | http | pending |
| `binance` | cv | pending |
| `hunt` | cv | pending |
| `basilisk` | cv | pending |
| `cutcaptcha` | cv | [cutcaptcha.md](cutcaptcha.md) |
| `capy` | cv | [capy.md](capy.md) |
| `keycaptcha` | cv | [keycaptcha.md](keycaptcha.md) |
| `lemin` | cv | [lemin.md](lemin.md) |
| `atbcaptcha` | cv | [atbcaptcha.md](atbcaptcha.md) |

## Что осталось (26 pending)

`friendly_captcha`, `altcha`, `text_captcha`, `math_image`, `image_to_text`,
`audio_captcha`, `perimeterx`, `imperva`, `castle`, `tspd`, `cybersiara`,
`twitch_integrity`, `botguard`, `botdeflector`, `awswaf`, `shein`, `mtcaptcha`,
`alibaba`, `prosopo`, `captchafox`, `vk`, `yandex`, `temu`, `binance`, `hunt`,
`basilisk`.

Порядок разумной дописки: сначала `awswaf`/`shein` (у них есть grid-голова и фикстуры
`tests/solvers/classification/fixtures/golden/`), потом cookie-движки (`perimeterx`,
`imperva`, `castle`, `tspd`), потом PoW/OCR.

## Как писать

1. Формат — общий для всех типовых runbook-ов: `Snapshot` → `Payload` → `Endpoints / flow`
   → `Solution keys` → `Ошибки` → `Canary / фикстуры` → `FILL / live-риски` →
   `Проверка` → `Incident log` → `References`. `_template.md` задаёт те же десять
   смысловых блоков (сенсор/модель у типов без сенсора уходят внутрь §3 и §7).
2. Заполнять по коду, а не по памяти: §3 — реальный `solve_http`/`solve_browser`, §4 —
   `domain/solution.py` + `extra`, §5 — `domain/errors.py` и фактически наблюдавшиеся отказы.
3. Неизмеренное помечать явно («не проверено», «не измерено», FILL), а не выдумывать цифры.
4. Ссылаться только на существующие файлы; ссылки проверяются глазами перед коммитом.
5. Обновлять §9 (incident log) при каждом инциденте: дата — триггер — фикс.

## Известные расхождения

- `docs/baseline.md` говорит, что `slider` и `rotate` не грузятся без `--extra cv`.
  Замер 2026-11-15: все 60 записей `solvers/loader.py` импортируются без `torch`/`cv2`,
  `slider` решает на numpy-бэкенде (`offset 100`, `match_score≈0.81` на голден-паре), а
  `rotate` падает уже на задаче с `ERR_CONFIG` (классический путь требует OpenCV).
  `baseline.md` — снимок и не переписывается; `AGENTS.md` и `docs/architecture.md`
  приведены к факту, детали — здесь и в [rotate.md](rotate.md) / [slider.md](slider.md).
- У HTTP-солверов лейна `cv` (tencent, yidun, atbcaptcha, cutcaptcha, recaptcha_v2) есть
  риск интеграции: `services/worker_cv/worker.py` собирает `SolverContext` без `http` и не
  обрабатывает `ERR_NEEDS_BROWSER`. Подробности — в §7 соответствующих runbook-ов.
