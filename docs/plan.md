# План проекта captcha-solver

Готовый сервис со своими решениями. Full scope — все 62 типа из `domain/enums.py`.
Разработка и долгосрочная поддержка — командой.

**Архитектурный принцип: HTTP-first. Браузер — второй контур, а не первый.**

> **Статус документа.** Это план и **гейт приёмки**, а не описание текущего кода. Что
> реально работает — `docs/architecture.md` и `docs/baseline.md`. Числа в §2 — пороги
> приёмки; замеров на живом трафике по большинству типов пока нет, и это отмечено там, где
> это важно.

Каждый солвер сначала пытается HTTP-решение (`curl_cffi` + JA3/JA4 + sensor-эмуляция +
JS-runtime при необходимости). Браузерный лейн включается в двух случаях: HTTP-солвер сам
бросил `NeedsBrowserError` (отпечаток не собирается), либо тип по каталогу живёт в браузере
(hCaptcha/Riot). Клиент не выбирает лейн.

---

## 1. Финальный гейт: 62/62

Финальная приёмка — **все 62 типа из `domain/enums.py` на уровне L3** (§2), плюс
`hopscotch` 95%/80% (или beta с abstention — решение lead). Ни один тип не выкидывается:
«не получилось» по типу — это не «уменьшим скоуп», а незакрытый гейт.

| что | значение | источник |
|---|---|---|
| типов в каталоге | 62 | `domain/enums.py`, `domain/catalog.py` |
| лейнов | 4: `http` (25), `cv` (27 + `hopscotch`), `protocol` (2), `browser` (8) | `catalog.lane_for` |
| REST-поверхность | `/createTask`, `/getTaskResult`, `/getBalance`, `/in.php`, `/res.php`, `/account/*`, `/admin/*` | `docs/protocol.md` |
| custom-тип hopscotch | вне 62, гейт 95% index / 80% координат | контракт §1 |
| экспериментальные типы | не продаются до L3 (`ALLOW_EXPERIMENTAL_TASKS=0`) | контракт §7, §9 |

Требование выше — контрактное, **в beta гейт ещё не включён**: `ALLOW_EXPERIMENTAL_TASKS`
реализован, но по умолчанию `1`, поэтому gateway продаёт всё, что есть в реестре воркера
(`worker_http/worker.py` выбирает солвер с `allow_experimental=True`). Это открытый пункт
§12 (вопрос 7), а не текущее поведение гейта.

Что считается «тип закрыт»:

1. солвер загружается без `--extra cv` (или честно требует экстру, и это записано в runbook);
2. есть runbook `runbooks/<type>.md` (или тип явно покрыт семейным runbook-ом из индекса);
3. есть golden/канареечные кейсы, а не только unit-тесты;
4. тип прошёл L3 по §2 и получил `experimental=False`;
5. `docs/protocol.md` описывает его solution-ключи, и живой клиент их читает.

---

## 2. Уровни зрелости L0–L3 и пороги L3

Уровень — свойство типа, не солвера. Переход между уровнями фиксирует verifier (не автор
солвера) по артефактам, а не по словам.

### L0 — код

- солвер зарегистрирован в `solvers/loader.py`, импортируется без тяжёлых экстр;
- unit-тесты и replay-фикстуры зелёные (`tests/solvers/<family>/`);
- `experimental=True` в `SolverInfo`;
- runbook написан (или тип в индексе `runbooks/README.md` со статусом pending);
- **не продаётся**: тип не должен попадать в capabilities до L1+.

### L1 — offline-качество

- golden-набор: ≥300 кейсов на тип для grid/click, 400 blind для hopscotch, для остальных —
  ≥100 размеченных кейсов или эквивалент по семейству;
- прогнаны метрики из [ADR-001](adr-vision-architecture.md) §8, отчёт с Wilson CI;
- пороги семейства (§2.4) выполнены;
- latency измерена на целевом железе (GPU/CPU), а не оценена по roofline;
- **не продаётся**: offline-метрика не заменяет живой canary.

### L2 — staging

- канарейка 6 ч по фикстурам типа против staging-шлюза, ≥3 прогона подряд;
- solution-ключи проверены настоящим клиентом (2captcha SDK / capmonster typings), не только
  нашим тестом;
- p95 и доля отказов измерены, а не предположены;
- known failure modes заполнены по факту падений, а не по шаблону;
- **не продаётся**: staging-цифры — не SLA.

### L3 — production

- **7 дней** канарейки в проде (контракт §10) без падения ниже порога;
- pass rate, coverage, p95 — по таблице §2.4, per type;
- `captcha_type_health{type} = 1` все 7 дней, ни одного `degraded`;
- инцидентов, где тип был причиной, нет;
- после этого verifier ставит `experimental=False`; только тогда тип **продаётся**.

**Продаём только L3.** Всё, что ниже, — внутренние прогоны и beta-доступ по решению lead;
платный трафик на L0–L2 не идёт. Биллинг и gate типов должны это обеспечивать
(`ALLOW_EXPERIMENTAL_TASKS=0`).

### 2.4 Пороги L3 по семействам

Пороги ниже — предмет приёмки. Где стоит «ADR §8» — цифра взята из предложенных offline-гейтов
ADR-001; где «предложение» — цифра вводится этим документом и требует подтверждения lead.
Ни одна из них пока не подтверждена замером на живом трафике.

| семейство | primary | secondary | latency p95 | coverage | источник |
|---|---|---|---|---|---|
| grid (hCaptcha, reCAPTCHA grid, FunCaptcha grid, AWS WAF, Shein) | set-exact ≥ 0.85 | tile AP ≥ 0.90 | ≤ 150 мс (GPU) | ≥ 0.95 при precision ≥ 0.95 | ADR §8 |
| click / bbox / draw / drag | hit@2% диагонали ≥ 0.90 | mAP50 ≥ 0.85 | ≤ 100 мс | ≥ 0.95 | ADR §8 |
| hopscotch | index top-1 ≥ 0.95 | coord hit@2% ≥ 0.80 | ≤ 300 мс | ≥ 0.90 | контракт §1, ADR §8 |
| slider / gap | offset в допуске ±3 px ≥ 0.95 | — | ≤ 100 мс | ≥ 0.95 | предложение |
| rotate | угол в допуске ±5° ≥ 0.95 | — | ≤ 100 мс | ≥ 0.95 | предложение |
| OCR / audio / text | exact match ≥ 0.95 | CER ≤ 0.02 | ≤ 2 с | ≥ 0.95 | предложение |
| token / protocol (PoW, Turnstile, v3, geo-cookie) | canary pass ≥ 0.95 | — | ≤ 60 с | ≥ 0.95 | предложение |
| antibot cookie-engines | canary pass ≥ 0.95 | cookie принимается целевым сайтом | ≤ 120 с | ≥ 0.90 | предложение |
| hCaptcha (browser) | canary pass ≥ 0.95 | `gRecaptchaResponse` + `respKey` + `userAgent` приняты | ≤ 240 с | ≥ 0.95 | предложение |
| VLM/детекторный fallback | fallback rate ≤ 10% | precision на отвеченных ≥ 0.95 | ≤ 3 с | ≥ 0.90 | ADR §8 |

Что здесь **не** порог:

- `CANARY_PASS_THRESHOLD=0.9` в коде — порог **алерта** для дежурного, не приёмки. Тип с
  pass rate 0.92 не падает в алерт, но L3 не проходит;
- pass rate без coverage бессмысленен: «95% на 40% задач» — это abstention, а не качество.
  Поэтому coverage стоит отдельной колонкой;
- latency p95 меряется от `createTask` до терминального статуса, а не внутри солвера.

### 2.5 Где сейчас типы

Замер 2026-11-15: 63 типа в каталоге (62 вендорских + `hopscotch`), 60 солверов в таблице,
все 60 загружаются без `--extra cv`, покрытие — 63/63. Ни один тип не имеет 7-дневной
canary: **L3 на сегодня — 0 типов**. Это стартовое состояние гейта, а не результат.

---

## 3. Группировка по технике

62 типа — это ~10 семейств. Планируем по семействам, параллельными треками.

| Семейство | Техника (HTTP-first) | Из списка |
|---|---|---|
| OCR-текст | PaddleOCR PP-OCRv5 / TrOCR + math-вычислитель. Pure HTTP: картинка → текст. | Image-to-Text, Math Image, Text Captcha |
| Audio | Whisper small/medium. Pure HTTP: аудио → текст. | Audio Captcha, reCAPTCHA v2 audio-bypass |
| Классификация тайлов | один ViT-бэкбон (DINOv3-B/16) + классовые головы. Pure HTTP: тайлы → индексы. | reCAPTCHA Grid, hCaptcha, FunCaptcha Grid, AWS WAF, Shein |
| Slider / puzzle | edge/template matching + humanized траектория. HTTP + CV-шаг. | GeeTest v3/v4, DataDome Slider, Capy, KeyCAPTCHA, Lemin, Cutcaptcha, atbCAPTCHA, Tencent, Yidun, Alibaba, VK, Yandex, Temu, Binance, Hunt, Basilisk |
| Rotate | angle regressor (ResNet50 / classical). | Rotate, FunCaptcha rotate-вариант |
| Click / Draw / Drag / BBox | детектор RT-DETR-R18/R34 (ONNX) + планировщик действий. | Click, Draw Around, Drag & Drop, Bounding Box, Shein |
| Score-based | HTTP-эмуляция с JA3/JA4 + sensor payload, без браузера. | reCAPTCHA v3, v3 Enterprise, Turnstile invisible |
| Full antibot | HTTP-reverse: sensor-эмулятор на движок + JS-runtime для obfuscated challenge. | CF Challenge, Akamai, Akamai SBSD, DataDome, Kasada, PerimeterX, Imperva, Castle, TSPD, CyberSiARA, Twitch Integrity, BotGuard, BotDeflector |
| PoW | вычислить nonce по схеме. Pure HTTP. | Friendly Captcha, ALTCHA |
| BDA / fingerprint payload | RE схемы шифрования + эмуляция сборщика через JS-runtime. | FunCaptcha BDA, enterprise-варианты |
| Interactive (misc) | свой HTTP-модуль на каждый: payload emulation + CV-шаг. | Cutcaptcha, atbCAPTCHA, Tencent TenDI, Yidun, Alibaba, Prosopo, CaptchaFox, VK, Yandex, MTCaptcha |
| hCaptcha browser | Camoufox + реальный виджет: HTTP-путь отказывает по отпечатку. | hCaptcha, hCaptcha Enterprise (Riot) |

Детектор: **YOLO11 не идёт в прод** (AGPL-3.0), целевой — RT-DETR (Apache-2.0), текущий
`detection/_yolo.py` остаётся как эксперимент до замены. Это решение ADR §5, не вкусовщина.

Один бэкбон закрывает классификацию и часть detection через головы: экономит GPU, ускоряет
файнтюн. Точные метрики — `docs/ml.md`, `docs/ml-tz.md`, ADR-001.

---

## 4. Архитектура

Четыре лейна, один stream на лейн, один воркер на лейн (подробно — `docs/architecture.md`):

```
[client] → [API gateway] → [redis stream <lane>] → [worker-http | worker-cv | worker-protocol | worker-browser]
                                                          │
                                              [solver registry] → [solver]
                                                          │
                                    [HTTP stealth] [JS runtime] [sensor builders] [Camoufox]
                                                          │
                                              [proxy pool] [fingerprint store]
                                                          │
                                              [canary] → type health → алерт
```

**HTTP-first router (фактическая механика).** Не «три фейла — переключаем тип», а сигнал от
солвера: `NeedsBrowserError` → один прыжок в `browser` (контракт §2). Метрика
`captcha_escalations_total{type}` — это и есть «сколько тип не решается по HTTP».
Автоматический перевод типа по проценту успеха за час (`HTTP_FALLBACK_THRESHOLD=0.7`,
`HTTP_FALLBACK_WINDOW_SEC=3600` в `config.py`) остаётся настройкой планировщика/дежурного,
а не скрытым рантайм-переключением: тип, который перестал решаться, должен быть виден в
канарейке, а не молча переехать.

**Контракт с клиентом: payload mode.** Клиент сам собирает challenge data со страницы
(sitekey, pageurl, свежие cookies, raw sensor data если нужно) и шлёт в createTask. Мы
возвращаем токен/ответ. Так работают capsolver/2captcha в большинстве эндпоинтов. URL-режима
в API нет; для hCaptcha браузерный солвер сам открывает `websiteURL`.

Компоненты: gateway, очередь (Redis Streams + DLQ), worker-http (1000+ concurrent на ноде),
worker-cv (GPU), worker-protocol (CPU), worker-browser (12 сессий на 16 ГБ), solver registry,
JS runtime, sensor builders, прокси-пул, fingerprint store, canary, model registry,
dataset pipeline. Admin dashboard — HTML-дашборда нет и не планируется: REST `/admin/*`.

---

## 5. Riot (browser) и Steam/Epic (HTTP probe): что показал живой замер

Снимок 2026-11-15, [hcaptcha-sites.md](hcaptcha-sites.md), инструмент
`tools/hcaptcha_probe.py`: 50 прогонов (5 хостов × 10), один egress IP, без прокси,
jsdom-realm, UA Chrome 131. Это первый **живой** ответ на вопрос «какой лейн вообще имеет
шанс» — до него все оценки были предположениями.

| сайт | host | sitekey | HTTP pass | вердикт | в `sites.py` |
|---|---|---|---|---|---|
| Steam join | `store.steampowered.com` | `e18a349a-46c2-46a0-87a8-74be79345c92` | 0/10 (0%), все — challenge | browser | `None` + `FILL` (ключ уже снят, профиль не обновлён) |
| Steam community | `steamcommunity.com` | тот же ключ | 0/10 (0%) | browser | `None` + `FILL` |
| Riot authenticate | `authenticate.riotgames.com` | `a5f74b19-9e45-40e0-b45d-47ff91b7a6c2` | 0/10 (0%), feature `andint` | browser | `BROWSER` |
| Riot account | `account.riotgames.com` | `b3c619fc-4b72-4838-9bf2-398888588d62` | 0/10 (0%) | browser | `BROWSER` |
| контроль hCaptcha | `example.com` | `10000000-ffff-ffff-ffff-000000000001` (passive) | **10/10 (100%)** | http | — |
| Epic portal | `www.epicgames.com/id/login`, `accounts.epicgames.com` | нет | — | Talon, hCaptcha не найдена | `None` + `FILL` |

Что это значит:

- **HTTP probe Steam/Epic дал 0% на тестовом IP**, а контрольный ключ — 100%. Значит
  транспорт и протокол (checksiteconfig → n-token → getcaptcha) собраны правильно, а 0% —
  свойство сайта: сервер принимает запрос и возвращает новый `c` вместо `P1_`-токена.
  На Riot это подтверждается feature `andint` — anti-detect VM, которую jsdom-отпечаток
  заведомо не проходит; на Steam ключ ведёт с `enc_get_req` и `custom_theme`.
- **Riot — browser by design**, как и планировалось. Steam сейчас тоже browser: HTTP-путь
  не закрыт, и «0%» — измеренный факт, а не «ещё не пробовали».
- **Epic не входит в HTTP probe**: hCaptcha на публичном входе нет, enforcement — Talon,
  sitekey отсутствует в HTML и чанках. Гадать ключ нельзя; тип `hcaptcha` для этого хоста
  не считается закрытым.
- Размер выборки — 10 прогонов на хост, один IP, без `vm_data`. Числа — свойство точки
  замера, а не «pass rate солвера вообще» (ограничения перечислены в
  [hcaptcha-sites.md](hcaptcha-sites.md)). Для L3 нужен замер через browser-лейн с реальной
  сессией и прокси, а не повторный jsdom-прогон.
- Passive-ключи (`checksiteconfig` → `{"pass": true}`, без `c`) решаются вообще без JS,
  пустым `n`: контроль 10/10. `wire.checksite_config` сейчас бросает на них
  `UnsolvableError` — дешёвый +1 к HTTP-покрытию, отдельная задача hcaptcha-http.

Итог для гейта: **hCaptcha L3 считается по браузерному лейну**, а не по HTTP. Steam/Epic
остаются в `auto`, но продаются как браузерные до появления HTTP-агента с реальным
отпечатком; «0% HTTP» — не повод убирать тип из 62.

## 6. Этапы (параллельные треки при команде)

При команде из 6–7 инженеров треки идут параллельно.

**Трек A — Core (Backend + DevOps)**
- Нед 1–4: M0 — gateway, очередь, воркеры, solver-интерфейс, docker-compose, REST `/admin/*`.
- Нед 5–8: прокси-пул, fingerprint-store, model registry, HTTP-first escalation.
- Нед 9–12: canary, billing, prod-инфра (k8s), TLS-ингресс.
- Нед 13+: hardening, autoscaling, observability.

**Трек B — ML / CV**
- Нед 1–3: M1 — OCR, audio, PoW.
- Нед 4–7: M2 — DINOv3-B/16 + головы классификации (hCaptcha, reCAPTCHA Grid, FunCaptcha Grid,
  AWS WAF, Shein).
- Нед 6–9: M3 — slider gap-detect + humanized траектория.
- Нед 8–11: rotate, click/draw/drag/bbox на RT-DETR (ONNX).
- Нед 10–14: dataset pipeline, retrain, drift, auto-labeling, калибровка/abstention.

**Трек C — HTTP Reverse (критичный путь)**
- Нед 1–4: HTTP-стелс (curl_cffi, JA3/JA4, HTTP/2), JS-runtime.
- Нед 5–8: reCAPTCHA v2/v3, Turnstile, базовый DataDome; hCaptcha HTTP (checksiteconfig →
  n-token → getcaptcha) + Steam/Epic probe.
- Нед 9–14: CF Challenge, Akamai sensor, DataDome full.
- Нед 15–20: Kasada, PerimeterX, Imperva.
- Нед 21–26: Castle, TSPD, CyberSiARA, Twitch Integrity, BotGuard, BotDeflector.
- Нед 27–30: enterprise-варианты, FunCaptcha BDA.

**Трек D — Browser**
- Нед 1–4: Camoufox-провайдер, профиль-менеджер, GeoIP, kill дерева процессов.
- Нед 5–8: worker-browser (gate, heartbeat, метрики), Riot.
- Нед 9+: поддержка версий, Steam/Epic fallback, тюнинг RAM.

**Трек E — Interactive misc (нед 12+)**
MTCaptcha, Tencent, Yidun, Alibaba, Prosopo, CaptchaFox, VK, Yandex, Temu, Binance, Hunt,
Basilisk — на готовых CV-примитивах и HTTP-sensor инфраструктуре.

**Интеграционные вехи:**
- **Нед 8:** MVP demo — 15+ простых типов end-to-end через HTTP.
- **Нед 14:** все CV-семейства + CF/DataDome/Turnstile закрыты, canary работает.
- **Нед 20:** основной антибот-стек по HTTP (CF, Akamai, DataDome, Kasada, PerimeterX).
- **Нед 26:** остаток антибот-семейства, enterprise.
- **Нед 28–30:** interactive misc, prod-release, canary зелёный по всем типам.

**Срок полного релиза:** 6–7 месяцев при команде 6–7 человек. HTTP-reverse занимает больше
времени, чем browser-based, но даёт 10–50x преимущество в latency и cost в проде.

---

## 7. Команда и роли

**Разработка:**
- **1 Core / backend** — gateway, очередь, воркеры, REST-админка, billing, регистры, инфра.
- **2 ML / CV** — OCR, audio, классификация, детекция, регрессор поворота, slider gap-detect,
  retrain pipeline, dataset tooling.
- **3 Reverse engineer** — RE #1: Cloudflare (Challenge, Turnstile), Akamai (+SBSD), DataDome.
  RE #2: Kasada, PerimeterX, Imperva, Castle, TSPD, CyberSiARA. RE #3: FunCaptcha BDA,
  enterprise-профили, Twitch Integrity, BotGuard, BotDeflector, JS-runtime.
- **0.5 Browser-engineer (part-time)** — Camoufox-нода, ротация профилей, RAM-тюнинг.
- **1 DevOps / Infra** — k8s, прокси-пул, мониторинг, CI/CD, GPU-ноды.
- **1 Data labeler / QA (part-time)** — разметка, проверка канареек, регрессы.

Итого: **7–8 человек на разработку**, из них 6–7 в фулл-тайме. Reverse-команда критично
важнее — она определяет срок антибот-блока.

**Поддержка после релиза:** 1 Core on-call, 1 ML on-call, 2 Antibot on-call. Ротация
недельная, three-tier escalation, минимум 4 человека.

---

## 8. Инфра и железо

**Dev / файнтюн:** 2× RTX 4090 (24 ГБ) или 1× A100 (40 ГБ); выделенная машина под
JS-runtime и sensor-дебаг.

**Prod inference:** 2–4 GPU-ноды (RTX 4090 / A6000) с autoscale. Отдельный пул под audio.

**HTTP worker nodes:** 4–8 CPU-нод (16–32 vCPU), 1000+ concurrent HTTP-сессий на ноду.

**Browser nodes:**
- 16 ГБ RAM на ноду → **12 сессий** (`WORKER_BROWSER_SESSIONS=12`), guard
  `WORKER_BROWSER_MIN_FREE_MB=2048`;
- ~700 МБ на активную страницу (замер пользователя; 0.5–1.1 ГБ по коду, UBO выключен);
- тюнинг 8–20 сессий на ноду, ramp с 2;
- 1–3 ноды в проде; CPU 8+ ядер, GPU не нужен.

**Residential proxy:** 300–1500 IP с геопокрытием (US/EU/APAC), ~$3–10k/мес. Два провайдера
для fail-over.

**Хранилище:** S3-совместимое (minio/S3) для датасетов и весов; Postgres (master + replica)
для задач, биллинга, логов; Redis для очередей, состояния, квот, capabilities, type health.

**Мониторинг:** Prometheus + Grafana, алерты в tg/Slack/PagerDuty; Loki/ELK для логов;
Sentry для исключений. Обязательные серии — контракт §10 (`captcha_tasks_total`,
`captcha_task_latency_seconds`, `captcha_queue_depth`, `captcha_browser_*`,
`captcha_escalations_total`, `captcha_canary_pass_rate`, `captcha_type_health`,
`captcha_dlq_total`). Полный список и алерты — `docs/operations.md`.

---

## 9. Что требует постоянного внимания после релиза

- **CF Challenge / Turnstile** — payload-JS обновляется каждые 2–6 недель.
- **Akamai sensor_data** — схема мутирует, два набора полей (BMP / AKAM-BM-S).
- **DataDome device check** — payload rotates, ddtk endpoint меняется.
- **Kasada x-kpsdk** — обфускация меняется каждый релиз JS.
- **PerimeterX px payload** — то же.
- **Imperva ___utmvc** — стабильнее, мутирует раз в 2–3 месяца.
- **FunCaptcha BDA** — шифрование меняется ~раз в 2–3 месяца, RE-сессия с нуля.
- **reCAPTCHA v3 / Enterprise** — зависит от fingerprint-стойки; любой апдейт детектора ломает
  успех.
- **hCaptcha** — bundle (hsw/hsj) и sitekey-профили; Riot отдельно, HTTP-путь нестабилен by design.
- **CV-модели** — дрейф данных, ретрейн раз в 1–2 месяца на свежем трафике.
- **Camoufox/Firefox** — версия браузера и опции профиля (RAM, addons); апгрейд раз в 1–2 месяца.
- **TLS-профили (curl_cffi)** — новые версии curl-impersonate раз в 2–4 месяца.
- **Прокси-пул** — ban-rate меняется, нужен мониторинг и смена подов.

Клиенту это продаётся как **ежемесячный retainer**, не «fixed-price и забыли». SLA — success
rate и время реакции на инциденты.

---

## 10. Стек

- **Backend:** Python 3.14 (пин в `.python-version`), FastAPI, asyncio, Redis Streams,
  Postgres, Alembic.
- **HTTP stealth:** curl_cffi (curl-impersonate). Альтернативы: tls-client, noble_tls, rnet, primp.
- **JS runtime:** mini-racer (пакет `mini-racer`, модуль `py_mini_racer`) или Node-сайдкар.
- **CV:** PyTorch 2.x, torchvision, opencv, DINOv3/SigLIP2, RT-DETR (ONNX), PaddleOCR/TrOCR,
  openai-whisper. YOLO11 — только эксперименты (AGPL).
- **Browser:** Camoufox (Firefox), psutil для дерева процессов. Playwright/Patchright — совместимый
  транспорт провайдера, но контракт — `BrowserProvider`, а не сырой Playwright.
- **Infra:** Docker, docker-compose (dev), k8s (prod), Helm, Terraform.
- **Model registry:** S3 + метаданные (`adapters/weights`), `make publish-model`.
- **Observability:** Prometheus, Grafana, Loki, Sentry, OTel.

---

## 11. Бюджетная оценка (orientation only)

- **Зарплаты:** 7–8 человек × $3–8k/мес = $24–65k/мес.
- **Инфра:** $1–3k/мес.
- **Residential proxy:** $3–10k/мес.
- **Мониторинг/tooling:** $300–800/мес.
- **Итого OPEX:** $28–80k/мес; CAPEX на dev-железо $8–15k разово.

На разработку 6–7 месяцев — $170–450k в зависимости от региона команды.

---

## 12. Открытые вопросы к lead

1. L3-таблица §2.4 введена этим документом; контракт §10 ссылался на `plan.md` §2, где её не
   было. Нужно подтверждение цифр «предложение» (slider/rotate/OCR/token/browser) или замена.
2. hopscotch «95%/80%» — это 95% index и 80% координат в допуске (трактовка ADR §12), или
   95% joint? Ответ меняет гейт.
3. Есть ли бюджет на 1×24 ГБ GPU под VLM-fallback, или fallback только через abstention?
4. Приемлемо ли, что DINOv3 требует HF-токен и принятия лицензии в CI/офлайн-сборке?
5. Кто владеет ONNX-загрузчиком RT-DETR и когда он появляется (ml-platform/cv-grid)?
6. Юридическая позиция по harvest изображений: ToS, consent, retention.
7. Когда закрывается `ALLOW_EXPERIMENTAL_TASKS=0`? Сейчас gateway продаёт типы, которых нет
   в L3, — это расходится с «продаём только L3» и должно быть закрыто до финального гейта.