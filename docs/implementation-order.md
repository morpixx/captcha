# Порядок реализации солверов

Ранжирование от простого к сложному. Payload mode (клиент шлёт challenge data → мы отдаём ответ). Все оценки — на baseline-рабочую версию, ретрейн и hardening идут параллельно.

---

## Tier 1 — тривиально (дни, не недели)

Чистый код, нет reverse, нет собственных датасетов. Можно выдать demo заказчику за 2 недели.

| # | Тип | Что делать | Срок |
|---|---|---|---|
| 1 | Friendly Captcha | PoW: вычисление nonce по sha256 с N ведущими нулями. Чистый код. | 1-2 дня |
| 2 | ALTCHA | То же: PoW по их опубликованной схеме. | 1-2 дня |
| 3 | Text Captcha | Запрос к LLM (хостим Mistral-7B/Llama-3 на своей инфре) с жёстким промптом. | 1-2 дня |
| 4 | Math Image | OCR → eval выражения через simpleeval. Нужен Image-to-Text готовым. | 1-2 дня после #5 |
| 5 | Image-to-Text / Normal OCR | PaddleOCR / TrOCR из коробки. Fine-tune на собранном датасете под сложные сайты. Публичные датасеты: CaptchaDataset, SimpleCaptcha. | 3-5 дней baseline, +1 нед fine-tune |
| 6 | Audio Captcha | Whisper из коробки для английских. Fine-tune под искажённое аудио и другие языки. | 3-5 дней baseline |

**Итого Tier 1: 6 типов за 1.5-2 недели.**

---

## Tier 2 — несложно, но нужен датасет (1-3 нед каждый)

CV с ML, но архитектуры стандартные, датасеты собираются или синтезируются.

| # | Тип | Что делать | Срок |
|---|---|---|---|
| 7 | Rotate | Angle regressor (ResNet50 или ViT-B, regression head). Датасет синтезируем: берём ImageNet, крутим на случайные углы. | 1-1.5 нед |
| 8 | Slider простые (Capy, KeyCAPTCHA, Lemin, Cutcaptcha) | Classical CV: edge-diff, template matching. Без ML в 80%+ случаев. Один движок на 4+ типов. | 1-2 нед, закрывает 4-5 типов |
| 9 | Click / Coordinates | Классификатор на ViT + координатная голова или YOLO. Датасет для типовых сцен. | 2 нед |
| 10 | Bounding Box | YOLOv8 fine-tune на целевые классы. | 2 нед |
| 11 | Classification Grid (hCaptcha, reCAPTCHA Grid, FunCaptcha Grid, AWS WAF Classif., Shein) | ViT-B/16 backbone + LoRA-головы на классы. Датасет через live-traffic sampling + human-in-loop. | 2-3 нед baseline, потом continuous retrain |
| 12 | Draw Around | Детекция объекта (на #10 уже YOLO) + генерация контурной траектории. | 2 нед |
| 13 | Drag & Drop | Детекция обоих объектов + планировщик движения. | 2 нед |

**Итого Tier 2: +10 типов за 4-6 нед.**

---

## Tier 3 — средняя сложность (2-4 нед каждый)

HTTP-flow + уже существующие CV-примитивы. Reverse лёгкий, OSS есть.

| # | Тип | Что делать | Срок |
|---|---|---|---|
| 14 | reCAPTCHA v2 (checkbox / invisible / callback) | audio-bypass: HTTP-flow к recaptcha endpoints + Whisper. OSS готовый (`recaptcha-solver-python` и подобные), адаптировать под свой стек. | 2-3 нед |
| 15 | GeeTest v3 | Gap detection (из #8) + payload emulation (w, challenge, gt). Коммьюнити reverse документирован. | 2-3 нед |
| 16 | GeeTest v4 | То же что v3, но обновлённый payload-формат. | 2 нед после v3 |
| 17 | DataDome Slider mode | Gap detection + свой payload. | 2-3 нед |
| 18 | FunCaptcha rotate / image-matching | Несколько CV-задач в одной капче, логика + CV уже есть в Tier 2. | 2-3 нед |

**Итого Tier 3: +5 типов за 8-10 нед.**

---

## Tier 4 — сложно, HTTP-reverse каждого (3-6 нед)

Основной антибот-блок. Reverse-команда работает здесь.

| # | Тип | Что делать | Срок |
|---|---|---|---|
| 19 | Turnstile (interactive + invisible) | Payload emulation, challenge-JS runtime. | 3-4 нед |
| 20 | reCAPTCHA v3 / Enterprise | Sensor payload + fingerprint-sensitive HTTP. | 3-4 нед |
| 21 | CF Challenge (cf_clearance) | Full challenge-JS runtime, cookie flow, WASM parts. | 4-6 нед |
| 22 | FunCaptcha BDA | RE шифрования payload, JS runtime. | 4-6 нед |
| 23 | Akamai sensor_data (BMP + AKAM-BM-S) | Sensor emulation. Мутирует регулярно. | 3-5 нед |
| 24 | Akamai SBSD | Отдельный sensor под мобильные кейсы. | 2-3 нед после Akamai |
| 25 | DataDome full (device check + ddtk) | Payload emulation. | 3-4 нед |

**Итого Tier 4: +7 типов за 16-20 нед (в параллель между 3 RE-инженерами ~10-12 нед).**

---

## Tier 5 — тяжело + maintenance-heavy

Reverse каждый + постоянный догон мутаций.

| # | Тип | Что делать | Срок |
|---|---|---|---|
| 26 | Kasada x-kpsdk | Payload RE + JS obfuscation. Релиз каждый месяц. | 3-4 нед |
| 27 | PerimeterX (_px / _pxhd / sensor) | Sensor emulation. | 2-3 нед |
| 28 | Imperva ___utmvc | Payload RE. | 2-3 нед |
| 29 | Castle | Fingerprint-based HTTP, относительно простой. | 1-2 нед |
| 30 | TSPD | Payload RE. | 1-2 нед |
| 31 | CyberSiARA | Payload RE. | 1-2 нед |
| 32 | Twitch Integrity | Twitch-специфичный protocol. | 1-2 нед |
| 33 | BotGuard | Payload RE. | 1-2 нед |
| 34 | BotDeflector | Payload RE. | 1-2 нед |

**Итого Tier 5: +9 типов за 15-20 нед (в параллель ~8-10 нед).**

---

## Interactive misc — ответвление, после Tier 2+3

Базовые примитивы готовы, остаётся только протокол каждого.

| # | Тип | Что делать | Срок |
|---|---|---|---|
| 35 | MTCaptcha | Protocol wrapper + CV из Tier 2. | 1-2 нед |
| 36 | Tencent TenDI | Protocol wrapper + slider CV. | 1-2 нед |
| 37 | Yidun (NECaptcha) | Protocol wrapper. | 1-2 нед |
| 38 | Alibaba | Protocol wrapper. | 1-2 нед |
| 39 | Prosopo Procaptcha | Protocol wrapper. | 1-2 нед |
| 40 | CaptchaFox | Protocol wrapper. | 1-2 нед |
| 41 | VK Captcha | Protocol wrapper. | 1 нед |
| 42 | Yandex SmartCaptcha | Protocol wrapper. | 1-2 нед |
| 43 | Temu | Protocol wrapper. | 1 нед |
| 44 | Binance | Protocol wrapper. | 1 нед |
| 45 | Hunt | Protocol wrapper. | 1 нед |
| 46 | Basilisk | Protocol wrapper. | 1 нед |
| 47 | atbCAPTCHA | Protocol wrapper. | 1 нед |
| 48 | Cutcaptcha | Уже частично в Tier 2 (слайдер), добить протокол. | 1 нед |

**Итого Interactive: +14 типов за 14-20 нед (в параллель между RE #2 и RE #3 после основного антибот-стека → ~6-8 нед).**

---

## Рекомендуемый порядок старта

**Недели 1-2:**
- #1-#6 (Tier 1) — быстрая победа, можно показать работающий API заказчику с 6 типами
- Параллельно: ML-инженер #1 собирает датасет для Grid classification (#11)
- Параллельно: RE #1 поднимает `packages/http` (curl_cffi, JA3/JA4 профили, прокси-интеграция)

**Недели 3-6:**
- #7-#13 (Tier 2) — ML-инженеры в параллели
- RE #1 начинает Turnstile (#19) — быстрее CF и даёт ранний выигрыш
- RE #2 начинает DataDome Slider mode (#17) + GeeTest v3 (#15)

**Недели 7-14:**
- #14-#18 (Tier 3) добирается ML + RE
- RE начинают тяжёлый блок: CF Challenge (#21), Akamai (#23), reCAPTCHA v3 (#20)

**Недели 15-24:**
- Tier 4 добивается, Tier 5 стартует
- Interactive misc идёт параллельным треком на базе готовых примитивов

**Недели 25-30:**
- Tier 5 завершение, Interactive misc добивается, prod hardening
