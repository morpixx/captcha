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

**Сейчас: 63 из 63 (62 вендорских типа + `hopscotch`). Pending — 0.**

Статус «live» здесь значит «документ есть», а не «тип проверен на живом сайте»:
`experimental=True` у солвера важнее статуса файла. На 2026-11-15 — 42 экспериментальных
солвера, 38 из 62 вендорских типов остаются experimental-only, живой evidence есть у 0/62
(финальный гейт L3 не закрыт ни по одному типу), канареечные фикстуры — у 10 из 62.

### PoW / protocol

| тип | лейн | runbook |
|---|---|---|
| `friendly_captcha` | protocol | [friendly_captcha.md](friendly_captcha.md) |
| `altcha` | protocol | [altcha.md](altcha.md) |

### OCR / audio (cv)

| тип | лейн | runbook |
|---|---|---|
| `text_captcha` | cv | [text_captcha.md](text_captcha.md) |
| `math_image` | cv | [math_image.md](math_image.md) |
| `image_to_text` | cv | [image_to_text.md](image_to_text.md) |
| `audio_captcha` | cv | [audio_captcha.md](audio_captcha.md) |

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
| `funcaptcha` | http | [funcaptcha.md](funcaptcha.md) |
| `funcaptcha_bda` | http | [funcaptcha.md](funcaptcha.md) |
| `funcaptcha_enterprise` | http | [funcaptcha.md](funcaptcha.md) |

### Turnstile / Cloudflare

| тип | лейн | runbook |
|---|---|---|
| `turnstile` | http | [turnstile.md](turnstile.md) |
| `turnstile_invisible` | http | [turnstile.md](turnstile.md) |
| `cf_challenge` | http | [cf_challenge.md](cf_challenge.md) |

### Антибот-движки

| тип | лейн | runbook |
|---|---|---|
| `akamai_sensor` | http | [akamai_sensor.md](akamai_sensor.md) |
| `akamai_sbsd` | http | [akamai_sbsd.md](akamai_sbsd.md) |
| `kasada` | http | [kasada.md](kasada.md) |
| `perimeterx` | http | [perimeterx.md](perimeterx.md) |
| `imperva` | http | [imperva.md](imperva.md) |
| `castle` | http | [castle.md](castle.md) |
| `tspd` | http | [tspd.md](tspd.md) |
| `cybersiara` | http | [cybersiara.md](cybersiara.md) |
| `twitch_integrity` | http | [twitch_integrity.md](twitch_integrity.md) |
| `botguard` | http | [botguard.md](botguard.md) |
| `botdeflector` | http | [botdeflector.md](botdeflector.md) |
| `awswaf` | cv | [awswaf.md](awswaf.md) (grid-голова, не движок `engines/`) |

### Interactive-вендоры

| тип | лейн | runbook |
|---|---|---|
| `shein` | cv | [shein.md](shein.md) |
| `mtcaptcha` | cv | [mtcaptcha.md](mtcaptcha.md) |
| `tencent` | browser | [tencent.md](tencent.md) |
| `yidun` | browser | [yidun.md](yidun.md) |
| `alibaba` | http | [alibaba.md](alibaba.md) |
| `prosopo` | browser | [prosopo.md](prosopo.md) |
| `captchafox` | browser | [captchafox.md](captchafox.md) |
| `vk` | http | [vk.md](vk.md) |
| `yandex` | browser | [yandex.md](yandex.md) |
| `temu` | http | [temu.md](temu.md) |
| `binance` | cv | [binance.md](binance.md) |
| `hunt` | http | [hunt.md](hunt.md) |
| `basilisk` | cv | [basilisk.md](basilisk.md) |
| `cutcaptcha` | cv | [cutcaptcha.md](cutcaptcha.md) |
| `capy` | cv | [capy.md](capy.md) |
| `keycaptcha` | cv | [keycaptcha.md](keycaptcha.md) |
| `lemin` | cv | [lemin.md](lemin.md) |
| `atbcaptcha` | browser | [atbcaptcha.md](atbcaptcha.md) |

## Что осталось

**Типовых runbook-ов не осталось: 63/63.** Всё, что осталось по покрытию, — не документы:

1. **Живой evidence.** 0 из 62 типов имеют L3-запись (7-дневный canary, pass/p95, покрытие);
   38 из 62 вендорских типов — experimental-only. Порядок L0–L3 и пороги — `docs/plan.md` §2.
2. **Канареечные фикстуры.** 10 из 62 типов: `altcha`, `datadome`, `friendly_captcha`,
   `funcaptcha`, `geetest_v3`, `geetest_v4`, `hcaptcha`, `recaptcha_v2`, `recaptcha_v3`,
   `turnstile`. Остальные runbook-и §6 содержат кейс «не добавлен» с готовым JSON.
3. **Веса.** Головы `awswaf-v1`/`shein-v1` и OCR/audio-модели без весов в бакете отвечают
   `ERR_CONFIG` — см. §7 соответствующих файлов и `docs/ml.md`.
4. **live-`FILL`.** В §7 типовых файлов перечислены конкретные `# FILL:` из кода — схемы
   сенсоров, MAC-секреты, пути `/flow-token/`, селекторы виджетов; каждый закрывается
   только захватом на живом сайте.

## Как писать

1. Формат — общий для всех типовых runbook-ов: `Snapshot` → `Payload` → `Endpoints / flow`
   → `Solution keys` → `Ошибки` → `Canary / фикстуры` → `FILL / live-риски` →
   `Проверка` → `Incident log` → `References`. `_template.md` задаёт те же десять
   смысловых блоков (сенсор/модель у типов без сенсора уходят внутрь §3 и §7).
2. Заполнять по коду, а не по памяти: §3 — реальный `solve_http`/`solve_browser`, §4 —
   `domain/solution.py` + `extra`, §5 — `domain/errors.py` и фактически наблюдавшиеся отказы.
3. Неизмеренное помечать явно («не проверено», «не измерено», FILL), а не выдумывать цифры.
4. Ссылаться только на существующие файлы; ссылки проверяются перед коммитом.
5. Обновлять §9 (incident log) при каждом инциденте: дата — триггер — фикс.

## Известные расхождения

- `docs/baseline.md` говорит, что `slider` и `rotate` не грузятся без `--extra cv`.
  Замер 2026-11-15: все 60 записей `solvers/loader.py` импортируются без `torch`/`cv2`,
  `slider` решает на numpy-бэкенде (`offset 100`, `match_score≈0.81` на голден-паре), а
  `rotate` падает уже на задаче с `ERR_CONFIG` (классический путь требует OpenCV).
  `baseline.md` — снимок и не переписывается; `AGENTS.md` и `docs/architecture.md`
  приведены к факту, детали — здесь и в [rotate.md](rotate.md) / [slider.md](slider.md).
- **cv-воркер даёт per-task `http`.** `_run_one` вызывает
  `context.task_context(self._ctx, self._http_factory, task)`, который делает
  `replace(template, http=Client(...))`; клиент несёт proxy/UA/cookies задачи и
  закрывается в `finally`. Поэтому `mtcaptcha`, `binance`, `basilisk`,
  `cutcaptcha` получают `ctx.http`. `tencent`/`yidun`/`atbcaptcha` вообще не в cv,
  а в browser-лейне; `alibaba`/`hunt` — в http. Ложные §7-записи в старых
  runbook'ах исправляются отдельным docs-проходом.
- **Форма ответа `awswaf`/`shein`.** Каталог объявляет `AWSWAF` (`captcha_voucher`/
  `existing_token`) и `COORDS` (`coordinates`), а grid-солверы возвращают `indices`;
  `domain/solution.py` уходит в generic-фолбэк и отдаёт `{"indices": [...]}`. Пока обмен
  voucher и конвертация индекса в точку не реализованы — см. [awswaf.md](awswaf.md) §4,
  [shein.md](shein.md) §4.
- **`experimental` важнее статуса файла.** Runbook есть у всех 63 типов, но это не значит
  «продаётся»: с `ALLOW_EXPERIMENTAL_TASKS=0` продаются 24 из 62 вендорских типов.
