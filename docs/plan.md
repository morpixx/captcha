# План проекта captcha-solver

Готовый сервис со своими решениями. Full scope — все 60+ типов из списка клиента. Разработка и долгосрочная поддержка — командой.

**Архитектурный принцип: HTTP-first. Браузер — только fallback.**

> **Статус документа:** это стратегический план на команду из 7-8 человек, а не описание
> текущего кода. Браузерного лейна в репозитории нет: JS исполняется в песочнице
> (`adapters/jssandbox`), отдельного worker-browser и playwright-зависимости не существует.
> Там, где ниже упомянуты `worker-browser`, `canary`, `dataset pipeline` и `admin dashboard`,
> читать как «планировалось», а не «есть». Актуальное состояние — `docs/architecture.md`.

Каждый солвер сначала пытается HTTP-решение (curl_cffi + JA3/JA4 + sensor-эмуляция + JS-runtime при необходимости). Браузерный fallback включается только если HTTP-реверс не поспевает за мутацией энджина, либо клиент передал URL без raw-payload.

---

## 1. Группировка по технике

60 строк в списке — это ~10 семейств. Планируем по семействам, параллельными треками.

| Семейство | Техника (HTTP-first) | Из списка клиента |
|---|---|---|
| OCR-текст | TrOCR / Donut / PaddleOCR + math-вычислитель. Pure HTTP: картинка на вход, текст на выход. | Image-to-Text, Math Image, Text Captcha |
| Audio | Whisper small/medium, STT. Pure HTTP: аудио на вход, текст на выход. | Audio Captcha, reCAPTCHA v2 audio-bypass |
| Классификация тайлов | один ViT/CLIP backbone + классовые головы. Pure HTTP: тайлы на вход, индексы на выход. | reCAPTCHA Grid, hCaptcha, FunCaptcha Grid, AWS WAF Classification, Shein |
| Slider / puzzle (gap-detect) | edge/template matching + humanized траектория. Pure HTTP: две картинки на вход, offset + payload на выход. | GeeTest v3/v4, DataDome Slider, Capy, KeyCAPTCHA, Lemin, Cutcaptcha, atbCAPTCHA, Tencent, Yidun, Alibaba, VK, Yandex, Temu, Binance, Hunt, Basilisk, Slider VisionEngine |
| Rotate | angle regressor (ResNet/ViT backbone). Pure HTTP. | Rotate, FunCaptcha rotate-вариант |
| Click / Draw / Drag / BBox | детектор (YOLOv8 fine-tuned) + планировщик действий. Pure HTTP. | Click, Draw Around, Drag & Drop, Bounding Box |
| Score-based | HTTP-эмуляция с JA3/JA4 + sensor payload, без браузера | reCAPTCHA v3, v3 Enterprise, Turnstile invisible |
| Full antibot | HTTP-reverse: sensor-эмулятор на каждый энджин + JS-runtime для obfuscated challenge | CF Challenge, Akamai, Akamai SBSD, DataDome, Kasada, PerimeterX, Imperva, Castle, TSPD, CyberSiARA, Twitch Integrity, BotGuard, BotDeflector |
| PoW | вычислить nonce по схеме. Pure HTTP. | Friendly Captcha, ALTCHA |
| BDA / fingerprint payload | RE схемы шифрования + эмуляция сборщика через JS-runtime | FunCaptcha BDA, часть enterprise вариантов |
| Interactive (misc) | свой HTTP-модуль на каждый — payload emulation + slider/click/rotate CV | Cutcaptcha, atbCAPTCHA, Tencent TenDI, Yidun, Alibaba, Prosopo, CaptchaFox, VK, Yandex, MTCaptcha |

Один бэкбон (ViT-B/16 или CLIP ViT-L) закрывает всю классификацию и часть detection через LoRA-головы. Экономит GPU, ускоряет файнтюн.

---

## 2. Архитектура

```
[client] → [API gateway] → [queue] → [worker pool]
                              │           │
                              │           ├──→ [worker-http]     ← main, 1000+ concurrent/node
                              │           ├──→ [worker-cv]       ← ML inference (classification/OCR/audio)
                              │           ├──→ [worker-protocol] ← PoW, BDA, sensor builders
                              │           └──→ [worker-browser]  ← fallback only, 15-25 concurrent/node
                              ▼
                       [solver registry]
                              │
             ┌────────────────┼──────────────────┬──────────────────┐
             ▼                ▼                  ▼                  ▼
       [HTTP stealth]   [JS runtime]      [sensor builders]   [browser fallback]
       curl_cffi+JA3    v8/pyminiracer    per-engine sensors  camoufox/nodriver
             │                │                  │                  │
             └────────────────┼──────────────────┼──────────────────┘
                              ▼
                       [proxy pool]
                       [fingerprint store]
                              │
                       [canary / regression bot] → алерты в тг
```

**HTTP-first router:**
Gateway кладёт задачу в очередь с меткой `prefer=http`. Воркер пробует HTTP-солвер. При трёх подряд fail-ах (configurable threshold, например success rate < 70% за последний час на этом типе) — автоматический перевод задач этого типа на `worker-browser` на время, пока reverse-команда не починит HTTP-путь. Прозрачно для клиента, логируется.

**Контракт с клиентом: Payload mode (зафиксировано).**
Клиент сам собирает challenge data со страницы (sitekey, pageurl, свежие cookies, raw sensor data если нужно) и шлёт нам в createTask. Мы возвращаем токен/ответ. Это как работает capsolver/2captcha в большинстве эндпоинтов — проверенная модель, минимизирует наш browser-fallback и упрощает интеграцию.

**URL mode (на будущее, не в первом релизе):** если заказчик захочет "URL → cookie" тариф, добавляем позже как отдельный fallback-tier. В первом релизе клиент всегда шлёт собранный payload.

Компоненты:
- **API gateway** — HTTP, протокол-совместимый с 2captcha/capsolver (createTask / getTaskResult / balance). Drop-in замена, клиент не переписывает интеграции.
- **Очередь** — Redis streams, приоритеты по типу (быстрые OCR в отдельной очереди, antibot в своей, browser-fallback в своей).
- **Worker-http** — главная рабочая лошадка. Python asyncio + curl_cffi. 1000+ concurrent на одной ноде (I/O-bound). Используется для 95% трафика.
- **Worker-cv** — ML inference (ViT/CLIP классификация, Whisper audio, YOLO detection, TrOCR). На GPU-нодах.
- **Worker-protocol** — PoW вычисления, BDA payload-builders, sensor-генерация для антиботов. Pure CPU, легко скейлится.
- **Worker-browser** — ТОЛЬКО fallback. 1-3 ноды в проде, 10-20 браузерных сессий на ноду. Включается когда HTTP не справляется или клиент в URL mode.
- **Solver registry** — один интерфейс `solve(task) -> result`, каждое семейство — плагин. Hot-reload моделей и sensor-модулей.
- **JS runtime** — песочница для исполнения obfuscated challenge JS (CF, FunCaptcha BDA, часть DataDome). Варианты: py-mini-racer, PyExecJS с v8, или отдельный Node-сайдкар.
- **Sensor builders** — per-engine эмуляторы sensor-данных: akamai_sensor, px_sensor, datadome_sensor, kasada_payload, imperva_utmvc и т.д. Это ядро HTTP-реверса.
- **Прокси-пул** — residential, geo-aware, session-sticky, автоматический ban-detection, авто-ротация.
- **Fingerprint store** — профили (TLS JA3/JA4, HTTP/2 settings, ALPN, extensions, canvas/webgl/audio/font/timezone/webrtc) с ротацией и привязкой к сессии.
- **Behavior sim** — для интерактивных типов с browser-fallback: безье-траектории мыши, человеческий таймминг. В HTTP-режиме используется для генерации правдоподобных mouse-events payload-ов (coordinates arrays).
- **Model registry** — MLflow или свой простой; версии, canary rollout, auto-rollback по success rate.
- **Dataset pipeline** — live traffic → sampling → human-in-loop разметка → retrain cron.
- **Canary / regression bot** — каждые N часов прогоняет живые кейсы по каждому типу на эталонных сайтах. Падение success rate ниже порога → алерт on-call инженеру в тг. Узнаёшь о поломке раньше клиента.
- **Admin dashboard** — success/latency/cost на тип, HTTP/browser ratio, model switch, состояние прокси и браузерной фермы, статус канарейки.
- **Knowledge base** — на каждый тип captcha runbook: как регенерить модель, где датасет, последняя схема payload, актуальный JS-challenge offset, история поломок и фиксов. Критично для команды — без этого знания в одной голове.

---

## 3. Команда и роли

Разработка full scope + долгосрочный maintenance требует специализации. HTTP-first архитектура сдвигает центр тяжести на reverse-инженеров.

**Разработка:**
- **1 Core / backend** — gateway, очередь, воркеры, админка, biling, регистры, инфраструктурные решения.
- **2 ML / CV инженера** — OCR, audio, классификация, детекция, регрессор поворота, slider gap-detect, retrain pipeline, dataset tooling. Один сфокусирован на классификации, второй на detection/OCR/audio.
- **3 Reverse engineer** — сдвинутый центр тяжести из-за HTTP-first:
  - RE #1: Cloudflare (Challenge, Turnstile), Akamai (+SBSD), DataDome — "большая тройка" по трафику.
  - RE #2: Kasada, PerimeterX, Imperva, Castle, TSPD, CyberSiARA.
  - RE #3: FunCaptcha BDA, Enterprise fingerprint-профили, Twitch Integrity, BotGuard, BotDeflector, JS-runtime инфраструктура.
- **0.5 Browser-engineer (part-time)** — camoufox/nodriver fallback-нода, ротация профилей. Основная работа в проде, не в разработке.
- **1 DevOps / Infra** — k8s, прокси-пул, мониторинг, CI/CD, GPU-ноды.
- **1 Data labeler / QA (part-time)** — разметка новых датасетов, проверка канареек, прогоны регрессов.

Итого: **7-8 человек на разработку**, из них 6-7 в фулл-тайме. Reverse-команда критично важнее — она определяет срок антибот-блока.

**Поддержка после релиза (постоянная):**
- 1 Core on-call (ротация из backend + devops)
- 1 ML on-call (ретрейн, дрейф моделей, обновление датасетов)
- 2 Antibot on-call (ротация из RE-команды — HTTP-реверс мутирует быстрее, нужно больше рук)

Ротация недельная, three-tier escalation. Минимум 4 человека в поддержке всегда.

---

## 4. Этапы (параллельные треки при команде)

При команде из 6-7 инженеров треки идут параллельно:

**Трек A — Core (Backend + DevOps)**
- Нед 1-4: M0 — API gateway, очередь, воркеры, solver-интерфейс, docker-compose, базовая админка.
- Нед 5-8: прокси-пул, fingerprint-store (TLS + browser-level), model registry, HTTP-first router с fallback-логикой.
- Нед 9-12: canary-бот, dashboard, биллинг (если публичный сервис), prod-инфра на k8s.
- Нед 13+: hardening, auto-scaling, observability.

**Трек B — ML / CV**
- Нед 1-3: M1 — OCR (TrOCR + math), Audio (Whisper), PoW (Friendly, ALTCHA).
- Нед 4-7: M2 — ViT backbone + LoRA-головы под классификацию (hCaptcha, reCAPTCHA Grid, FunCaptcha Grid, AWS WAF, Shein).
- Нед 6-9: M3 — slider gap-detect + humanized траектория → 15+ slider-типов (CV-часть; HTTP payload-эмуляция на Треке C).
- Нед 8-11: Rotate (angle regressor), Click/Draw/Drag/BBox (YOLOv8 fine-tuned).
- Нед 10-14: dataset pipeline, continuous retrain, дрейф-мониторинг, auto-labeling.

**Трек C — HTTP Reverse (критичный путь, сдвинут вверх)**
- Нед 1-4: базовый HTTP-стелс (curl_cffi wrapper, JA3/JA4 profiles, HTTP/2 settings, residential-ротация), JS-runtime (py-mini-racer sandbox).
- Нед 5-8: **reCAPTCHA v2/v3** (audio-bypass для v2, HTTP-эмуляция для v3), **Turnstile** (Challenge + invisible), **базовый DataDome** (slider payload + device check).
- Нед 9-14: **CF Challenge** (challenge-JS runtime, cookie flow), **Akamai** (sensor_data generator), **DataDome full**.
- Нед 15-20: **Kasada** (x-kpsdk payload RE), **PerimeterX** (px sensor emulation), **Imperva** (___utmvc).
- Нед 21-26: **Castle, TSPD, CyberSiARA, Twitch Integrity, BotGuard, BotDeflector** — каждый 1-2 недели.
- Нед 27-30: **Enterprise варианты** (reCAPTCHA Enterprise, v3 Enterprise, FunCaptcha Enterprise), **FunCaptcha BDA** — RE схемы шифрования.

**Трек D — Browser fallback (минимальный)**
- Нед 1-4: camoufox + patchright базовая обвязка, профиль-менеджер, URL-mode handler.
- Нед 5-8: интеграция fallback-роутера с Треком A.
- Нед 9+: поддержка и обновление версий (периодическое, part-time).

**Трек E — Interactive misc (ответвление от B и C, нед 12+)**
MTCaptcha, Tencent, Yidun, Alibaba, Prosopo, CaptchaFox, VK, Yandex, Temu, Binance, Hunt, Basilisk — на базе уже готовых CV-примитивов и HTTP-sensor инфраструктуры.

**Интеграционные вехи:**
- **Нед 8:** MVP demo — 15+ простых типов работают end-to-end через HTTP, API-совместимость доказана.
- **Нед 14:** все CV-семейства + базовые антибот-энджины (CF, DataDome, Turnstile) закрыты.
- **Нед 20:** основной антибот-стек закрыт по HTTP (CF, Akamai, DataDome, Kasada, PerimeterX).
- **Нед 26:** остаток антибот-семейства закрыт, enterprise работают.
- **Нед 28-30:** interactive misc добит, prod-release, canary зелёный по всем типам.

**Срок полного релиза:** 6-7 месяцев при команде 6-7 человек. HTTP-reverse занимает больше времени чем browser-based, но даёт 10-50x преимущество в latency и cost в проде.

---

## 5. Что требует постоянного внимания после релиза

HTTP-first архитектура смещает тип поддержки — не апгрейды браузеров, а догонять мутации sensor/payload схем.

- **CF Challenge / Turnstile** — payload-JS обновляется каждые 2-6 недель. HTTP-реверс догоняет diff в challenge-runtime.
- **Akamai sensor_data** — схема мутирует регулярно, два набора полей (BMP / AKAM-BM-S). Live RE.
- **DataDome device check** — payload rotates, ddtk endpoint меняется.
- **Kasada x-kpsdk** — payload obfuscation меняется каждый релиз JS.
- **PerimeterX px payload** — то же.
- **Imperva ___utmvc** — относительно стабилен, но мутирует раз в 2-3 месяца.
- **FunCaptcha BDA** — payload-шифрование меняется ~раз в 2-3 месяца. RE-сессия с нуля при каждом обновлении схемы.
- **reCAPTCHA v3 / Enterprise score-based** — зависит от fingerprint-стойки. Любой апдейт детектора ломает успех. Fingerprint-профили — живой артефакт.
- **CV-модели классификации** — дрейф датасета, ретрейн раз в 1-2 месяца на свежем трафике.
- **TLS-профили (curl_cffi)** — отслеживать новые версии curl-impersonate, апгрейд раз в 2-4 месяца под новые версии Chrome/Firefox.
- **Прокси-пул** — ban-rate меняется, провайдеры банят. Нужен мониторинг и смена подов пула.

Клиенту это **продаём как ежемесячный retainer**, не как "fixed-price и забыли". Контракт описывает SLA по success rate и response time на инциденты.

---

## 6. Железо и инфра

HTTP-first существенно сокращает требования к железу.

**Dev / файнтюн:**
- 2x RTX 4090 (24GB) или 1x A100 (40GB) — для файнтюна CV-моделей и экспериментов.
- Выделенная dev-машина под JS-runtime и sensor-дебаг.

**Prod inference:**
- 2-4 GPU-нод (RTX 4090 / A6000 класса), autoscale по нагрузке. Нужно только для ML-инференса.
- Отдельный GPU-пул для audio (Whisper CPU работает, но GPU в 10x быстрее).

**HTTP worker nodes:**
- 4-8 CPU-нод (16-32 vCPU каждая), каждая держит 1000+ concurrent HTTP-сессий.
- Основная рабочая лошадка, скейлится горизонтально под нагрузку.

**Browser fallback:**
- 1-3 ноды без GPU, по 15-25 браузерных сессий на ноду.
- Включаются только для URL-mode задач и когда HTTP-реверс лагает.

**Residential proxy:**
- 300-1500 IP с геопокрытием (US/EU/APAC обязательно), ~$3-10k/месяц (ниже чем для browser-heavy сетапа, т.к. меньше сессий на IP с HTTP).
- Два провайдера для fail-over.

**Хранилище:**
- S3-совместимое (minio self-hosted или AWS S3) для датасетов и артефактов моделей.
- Postgres cluster (master + replica) для задач, биллинга, логов.
- Redis cluster для очередей, кеша fingerprint, сессионных cookies.

**Мониторинг:**
- Prometheus + Grafana, алерты в тг / Slack / PagerDuty.
- Loki / ELK для логов.
- Sentry для exceptions.

---

## 7. Стек

- **Backend:** Python 3.14 (пин в `.python-version`, код совместим с 3.12+), FastAPI, asyncio, Redis streams, Postgres, Alembic.
- **HTTP stealth:** **curl_cffi** (curl-impersonate binding) — главный клиент, TLS/HTTP/2 fingerprint-имитация. Альтернативы: tls-client, noble_tls, rnet, primp.
- **JS runtime:** mini-racer (пакет переименован из py-mini-racer; модуль внутри всё ещё `py_mini_racer`) или Node-сайдкар через IPC для тяжёлых obfuscated скриптов.
- **ML:** PyTorch 2.x, transformers, openai-whisper, ultralytics (YOLOv8), timm (бэкбоны), peft (LoRA).
- **Browser (fallback):** Playwright + patchright, camoufox, nodriver, rebrowser-patches.
- **Infra:** Docker, docker-compose (dev), k8s (prod), Helm charts, Terraform под cloud-ресурсы.
- **Model registry:** MLflow (или свой на S3 + метадата в Postgres).
- **Observability:** Prometheus, Grafana, Loki, Sentry.
- **CI/CD:** GitHub Actions, self-hosted runners на GPU-нодах для ML-пайплайнов.

---

## 8. Процессы (критично при команде)

- **Одна дистрибуция** с доменной структурой: `src/captcha/{domain,application,adapters,engines,solvers,services}` (см. `architecture.md`).
- **Общий solver-интерфейс** — любой тип капчи реализуется как плагин с одним методом. Новый тип добавляется без трогания core.
- **Runbook на каждый тип** — обязательный артефакт, PR не мержится без обновлённого runbook.
- **Регрессионные тесты на живых кейсах** — автоматический прогон перед релизом + каждые 6 часов в проде.
- **Two-tier review:** ML изменения ревьювит ML-инженер, HTTP-реверс изменения — reverse-инженер. Крипто-related изменения (JA3, payload RE, sensor-схемы) — two-man rule.
- **Weekly sync** по канарейке: что падало, что чинили, что ждёт апдейта.
- **Postmortem-культура** на каждый инцидент в проде — что сломалось, как починили, как предотвратить.

---

## 9. Бюджетная оценка (orientation only)

HTTP-first сокращает OPEX примерно на 30-40% в сравнении с browser-heavy сетапом.

- **Зарплаты команды:** 7-8 человек × $3-8k/мес (регион-зависимо) = $24-65k/мес.
- **Инфра cloud:** $1-3k/мес (меньше GPU-нод и браузерных нод чем в browser-heavy варианте).
- **Residential proxy:** $3-10k/мес (меньше сессий на IP с HTTP = ниже стоимость).
- **Мониторинг / tooling:** $300-800/мес.
- **Итого OPEX:** $28-80k/мес.
- **CAPEX на dev-железо** (если не cloud): $8-15k разовый.

На разработку 6-7 месяцев это $170-450k в зависимости от региона команды.

---

## 10. Вопросы к заказчику (блокеры)

1. Сервис публичный (как capsolver, принимает чужие задачи) или closed для одного клиента?
2. SLA: success rate target по типам (90? 95? 99?) и latency (p50/p95/p99). С HTTP-first + payload mode достижимы p50 100-300ms.
3. Объём трафика: RPS на пике, по регионам, по типам капч.
4. Приоритет типов — что показать на demo-вехе (нед 2 с Tier 1, нед 8 с MVP), что можно к финалу.
5. Есть ли живой трафик для сбора датасетов, или всё собираем с нуля (повлияет на срок M2-M3).
6. Model и формат retainer-контракта: ежемесячная сумма / per-incident / mixed.
7. Юридические требования и GDPR/DPA — где хранить данные, какие регионы прокси запрещены.
8. Кто поставляет команду — твоя сторона набирает или заказчик даёт готовых людей?
