# ADR-001: Visual core, task heads and router

**Статус:** proposed (решение принято, ждёт review lead)
**Дата:** 2026-11-15
**Контекст:** `docs/production-contract.md` §9, `docs/ml-tz.md`, `docs/baseline.md`,
`docs/ml-research.md` (обзор моделей, лицензий и оценок latency/VRAM).
**Решает:** какая визуальная архитектура идёт в прод для 62 типов + hopscotch.
**Не решает:** конкретные веса, гиперпараметры, схему разметки (это планы обучения).

## 1. Контекст

Контракт требует: 62/62 L3, hopscotch 95%/80%, 10 артефактов из `ml-tz` плюс
`hopscotch-v1`, «unified visual core: DINOv3/SigLIP2 + heads (OCR, grid, detection,
geometry, matching). ADR decides; per-type fallback allowed» (§9).

Текущее состояние:

- `classification/head.py` читает `classifier.weight [C,768]` и замороженный
  `google/vit-base-patch16-224-in21k`; голова линейная.
- `detection/_yolo.py` грузит Ultralytics `.pt`; `rotate/regressor.py` — ResNet50.
- `hopscotch`: DINOv3 ViT-B/16 + 9 query-голов, 282/400 (70.5%), 27/30 GoldVal.
- Весов в бакете нет, golden-наборов нет, latency на целевом железе не измерена.
- Целевое железо: RTX 5060 Laptop **8 ГБ**, Python 3.14, torch 2.13 cu130 (из baseline).
- В `ml-tz` бюджет «голова на 9 тайлов ≤ 30 мс на CPU»; по roofline это 1.5–3 с
  (§8 `ml-research`). Бюджет и железо противоречат друг другу.

## 2. Проблема

Одна архитектура должна закрыть пять разных задач:

1. **OCR** — текст/формула, выход — строка.
2. **Grid** — какие тайлы содержат объект X, выход — индексы.
3. **Detection** — где объекты, выход — боксы/точки.
4. **Geometry** — угол, смещение паза, контур, выход — число/полигон.
5. **Matching/selection** — какой элемент из набора, выход — индекс + координата.

Плюс 20 http- и 2 protocol-типа, где vision не нужен вообще. Вопрос не «какая модель
лучше вообще», а «какая архитектура даёт 62/62 при 8–24 ГБ, без AGPL/GPL в проде и с
калиброванным отказом».

## 3. Рассмотренные варианты

### A. VLM-only

Один Qwen2.5-VL-7B (Apache-2.0), промпт на тип, ответ текстом/JSON. Все головы
эмулируются инструкцией.

Плюсы: один артефакт, zero-shot на новые промпты, быстро стартует.
Минусы: 16.6 ГБ bf16 (не влезает в 8 ГБ), 2–3 с на задачу, координаты теряются
(OpenCaptchaWorld: 40.0% у лучшего агента), нет калиброванной уверенности, 3B-вариант
нельзя в прод из-за `qwen-research`.

### B. Core + heads

Замороженный DINOv3 ViT-B/16 (или SigLIP2-B/16) + пять маленьких голов; VLM нет.

Плюсы: 10–150 мс, 0.2–2 ГБ, обучается на тысячах примеров, детерминирован, легко
калибруется, лицензии чистые.
Минусы: фиксированный словарь; новый промпт = переобучение головы; не покрывает
open-vocabulary детекцию и «нестандартные» типы.

### C. Hybrid (core + heads + router + VLM fallback)

B на hot path, A — только как fallback по калиброванному отказу (и GroundingDINO для
open-vocab детекции).

Плюсы: дешёвый и точный hot path; open-vocab и новые типы закрываются fallback-ом;
отказ виден и измерим; можно запускать на 8 ГБ без VLM, а fallback — на отдельной
24 ГБ ноде.
Минусы: два контура эксплуатации; fallback надо калибровать; 24 ГБ нода.

## 4. Evidence

- **Локализация — слабое место VLM.** OpenCaptchaWorld (NeurIPS 2025): 20 типов,
  225 пазлов, лучший агент 40.0%, человек 93.3% ([arXiv:2505.24878](https://arxiv.org/abs/2505.24878)).
- **Даже специализированный VLM не добивает порог.** CaptchaMind: Qwen2.5-VL-7B +
  SFT + RL, 16k сэмплов → 82.9% на синтетическом CaptchaBench и **71.0%** на real-world
  ([arXiv:2605.19538](https://arxiv.org/abs/2605.19538)). Для гейта 95% этого мало.
- **Fine-grained localization/space — тяжёлое для MLLM.** COGNITION: recognition и
  low-interaction близки к человеку, fine localization и multi-step spatial — нет
  ([arXiv:2512.02318](https://arxiv.org/abs/2512.02318)).
- **Core+head уже работает.** hopscotch: DINOv3 ViT-B/16 + 9 queries, 70.5% на 400 blind.
  Это не VLM и не zero-shot; это ровно вариант B.
- **Дешёвая grid-голова возможна.** Замороженный ViT-B/16 + линейная голова — это
  текущий контракт `[C,768]`; DINOv3-B/16 и SigLIP2-B/16 — B-класс, 768-d (сверить
  `config.json` при публикации).
- **Лицензии.** YOLO11 — AGPL-3.0 (сетевой copyleft), hcaptcha-challenger — GPL-3.0,
  CaptchaBench/CaptchaArena — CC-BY-NC-4.0, Qwen2.5-VL-3B — non-commercial. В прод
  это не идёт.

## 5. Решение

**Принимается вариант C: hybrid.**

1. **Visual core** — DINOv3 ViT-B/16 (85.7M, DINOv3 License, gated) как основной
   энкодер. SigLIP2-B/16 (Apache-2.0) — второй энкодер для text-prompt matching и
   лицензионно-чистый резерв. Оба B-класса, 768-d (подтвердить по `config.json`),
   совместимы с текущим форматом головы. CLIP-L/14 — только baseline, не прод.
2. **Пять голов** (контракт §9):
   - OCR: PaddleOCR PP-OCRv5 (Apache-2.0) дефолт, TrOCR-base (MIT) — fallback на
     трудный текст. Голова не на core-фичах: OCR-движки сильнее и дешевле.
   - Grid: линейная/MLP-голова на замороженных 768-d фичах, per-family; для промптов,
     которых не было в обучении — SigLIP2 text embedding (zero-shot) с порогом.
   - Detection: RT-DETR-R18/R34 (Apache-2.0), ONNX. YOLO11 в прод **не идёт** без
     коммерческой лицензии Ultralytics; остаётся baseline для экспериментов.
   - Geometry: классика (edge/template) для slider, ResNet50-регрессор для rotate,
     гомография/контур для draw/bbox. Core-фичи — только если классика не хватает.
   - Matching: DINOv3 patch features + query-голова (текущая hopscotch-схема), плюс
     cosine-matching для «выбери элемент».
3. **Router** — на уровне выбора головы в солвере, не на уровне очереди:
   - тип из `catalog.py` → голова;
   - голова отдаёт score; порог калиброван на golden под precision ≥ 0.95;
   - score ниже порога → fallback: GroundingDINO-T (если задача про open-vocab
     детекцию) или Qwen2.5-VL-7B (если про reasoning/сложный выбор);
   - fallback ниже своего порога → `UnsolvableError` (abstention), без угадывания.
   - Fallback-тир живёт в cv-воркере на ноде ≥24 ГБ, включается конфигом
     `VLM_FALLBACK=1`; на 8 ГБ ноде его нет — тогда только abstention. Новый lane
     не вводится, контракт §2 не меняется.
4. **Калибровка и отказ** — обязательны для каждой головы: temperature scaling или
   isotonic на held-out, отчёт ECE и coverage-precision. Для hopscotch использовать
   `release.json` (calibration/rejector), который уже предусмотрен контрактом §9.
5. **Лицензионная политика:** в прод-путь не попадают AGPL/GPL/NC-модели и датасеты.
   DINOv3 — с копией лицензии и attribution; Gemma Terms — только после legal review.

Решение не меняет solver ABI, `SolverResult`, lane-раскладку и формат реестра весов.
Меняются тренировочные артефакты и загрузчики (добавляется ONNX-путь детектора).

## 6. Матрица решения

Шкала 1–5 (5 — лучше). Веса — приоритеты проекта: точность и latency важнее всего,
лицензия и ops — гигиена.

| критерий | вес | A: VLM-only | B: core+heads | C: hybrid |
|---|---:|---:|---:|---:|
| точность на известных типах | 0.25 | 3.0 | 4.0 | 4.5 |
| latency / throughput | 0.20 | 1.0 | 5.0 | 4.0 |
| VRAM / стоимость | 0.15 | 1.0 | 4.5 | 3.5 |
| стоимость данных и разметки | 0.10 | 4.0 | 2.0 | 3.0 |
| open-vocab / новые типы | 0.10 | 5.0 | 1.5 | 4.5 |
| калибровка / abstention | 0.10 | 2.0 | 4.5 | 4.5 |
| ops-сложность | 0.05 | 3.0 | 3.5 | 2.5 |
| лицензии | 0.05 | 3.0 | 4.0 | 4.0 |
| **взвешенный итог** | **1.00** | **2.500** | **3.850** | **3.975** |

Расшифровка оценок:

- **Точность.** A теряет на координатах и порядке (OpenCaptchaWorld/CaptchaMind);
  B силён на своих классах, но новый промпт без retrain — ноль; C закрывает оба случая.
- **Latency.** A — 0.8–3 с и последовательный decode; B — 10–150 мс; C — B на 90–95%
  трафика, A только на отказе.
- **VRAM.** A 7B bf16 = 16.6 ГБ, 4-bit = 4.4 ГБ с потерей точности; B ≤ 2 ГБ; C требует
  отдельную 24 ГБ ноду под fallback.
- **Данные.** A почти не требует разметки (промпт), B требует 50–100k тайлов на голову,
  C — то же плюс небольшой набор для калибровки fallback.
- **Open-vocab.** B фиксирован; A видит новые промпты; C использует A только там, где B
  уверенно отказался.
- **Калибровка.** У A нет честного score (текстовая генерация); у B логистическая
  голова и температура; C наследует B и добавляет порог на fallback.
- **Ops.** A — один процесс; B — реестр + несколько голов; C — плюс VLM-нода.
- **Лицензии.** A: 7B Apache, но 3B нет; B: DINOv3 custom + Apache-резерв;
  C: то же плюс Gemma/NC-риски, если не соблюдать политику.

**Чувствительность.** Если поднять latency (0.20 → 0.30), убрать open-vocab
(0.10 → 0.05) и забрать разницу из data (0.10 → 0.05), B выигрывает ~0.18 балла.
Hybrid выбран потому, что дрейф промптов — ожидаемое свойство hCaptcha/reCAPTCHA
(категории меняются), а fallback срабатывает редко. Если lead считает дрейф незначимым, решение можно упростить до B без потери
hot path.

## 7. План данных

| голова | данные | объём | разметка | источник |
|---|---|---|---|---|
| grid | тайлы 224² + промпт | 50–100k тайлов, 5–10k челленджей | JSONL `{tile, prompt, label}` | свой harvest + ручная |
| detection | изображение + боксы | 25k изображений, ~150k инстансов | COCO JSON | harvest + синтетика для баланса |
| OCR | кропы строк | 200k синтетики + 20k реальных | строка/ charset | синтетика + harvest |
| geometry | пары/углы | 100k поворотов, 20k слайдеров | угол/offset | синтетика + реальные |
| matching | изображение + индекс/точка | 4k+ (есть 110 gold / 3953 silver / 400 blind) | JSONL | hopscotch_v2 |

Правила:

- **Golden:** ≥300 кейсов на тип (grid/click), 400 hopscotch blind; заморожены хешем,
  не участвуют в обучении, стратифицированы по сайту/теме.
- **Split по источнику и теме**, не по кадрам: near-duplicate leakage убивает метрику
  быстрее, чем слабая модель. Проверка perceptual hash.
- **Публичные данные** — только лицензионно чистые (Apache/MIT) и только для
  pretrain/smoke. CC-BY-NC и scraped-наборы в прод-обучение не идут (см. `ml-research` §6).
- **Разметка:** CVAT/Label Studio, двойная на 10% выборки, adjudication; для grid —
  проверка «есть/нет» по каждому промпту, а не только выбранные тайлы.
- **Синтетика** — для OCR, поворотов и баланса классов детектора; для grid не
  использовать как основной источник (доменный разрыв).

## 8. План бенчмарков и приёмки

**Harness.** `tools/ml_probe/metrics.py` — чистый stdlib, читает golden/predictions
JSONL, считает метрики, latency-перцентили и Wilson CI. Никакого torch, работает на
любой машине и в CI.

**Протокол:**

1. Один и тот же golden для всех вариантов; predictions фиксируются в JSONL с id.
2. Baselines: zero-shot CLIP/SigLIP2; zero-shot Qwen2.5-VL-3B (только research);
   обученная голова; hybrid с fallback.
3. Latency: warmup 20, 200 прогонов, batch 1, concurrency 1 и 8; p50/p95/p99;
   отдельно preprocessing; VRAM peak через `nvidia-smi`.
4. Статистика: Wilson 95% CI; сравнение моделей — paired bootstrap 10k; n≥300 даёт
   ±~5 п.п., поэтому «+3 п.п.» без CI не считается улучшением.
5. Отчёт: accuracy, coverage (доля ответов), precision на отвеченных, ECE, latency,
   VRAM, GPU-секунды на задачу.

**Предлагаемые offline-гейты** (L3-таблица в `plan.md` §2 отсутствует; цифры —
предложение lead, не замена контракту):

| тип | primary | secondary | latency p95 | coverage |
|---|---|---|---|---|
| grid | set-exact ≥ 0.85 | tile AP ≥ 0.90 | ≤ 150 мс (GPU) | ≥ 0.95 при precision ≥ 0.95 |
| click | hit@2% diag ≥ 0.90 | mAP50 ≥ 0.85 | ≤ 100 мс | ≥ 0.95 |
| hopscotch | index ≥ 0.95 | coord hit@2% ≥ 0.80 | ≤ 300 мс | ≥ 0.90 |
| fallback | — | доля ≤ 10% | ≤ 3 с | — |

Финальный гейт — 7-дневная canary по контракту §10; offline-гейты только отсекают
заведомо слабые модели.

## 9. Прототип: grid, click, hopscotch

Прод-код не пишется. Всё — в `ml/` (владелец hopscotch-ml) и `tools/ml_probe`
(владелец ml-research); прод-солверы не трогаются.

### 9.1 Grid (hCaptcha / reCAPTCHA / classification_grid)

- **Гипотеза:** замороженный DINOv3-B/16 + линейная голова даёт +5 п.п. set-exact
  против текущего ImageNet ViT-B/16 и +20 п.п. против zero-shot Qwen2.5-VL-3B.
- **Данные:** 300 golden челленджей (2.7k тайлов), 20k train-тайлов из 2.5k челленджей;
  3 промпта полностью hold out.
- **Модель:** фичи 768-d кэшируются; per-prompt logistic regression / 2-layer MLP;
  SigLIP2 text embedding для hold-out промптов.
- **Метрики:** set-exact, tile AP, set-F1, coverage, p95.
- **Exit:** set-exact ≥ 0.85, tile AP ≥ 0.90, p95 ≤ 150 мс, hold-out промпты ≥ 0.70.
- **Срок:** 2 недели (неделя 1 — данные/фичи, неделя 2 — голова/замер).

### 9.2 Click (click / coordinates / hunt / basilisk / atb / mtcaptcha / yidun / tencent)

- **Гипотеза:** RT-DETR-R18 после fine-tune бьёт zero-shot GroundingDINO-T и YOLO11s
  baseline по hit@2% при p95 ≤ 100 мс.
- **Данные:** 300 golden изображений, 5k train, 30 классов, ~30k инстансов, hard
  negatives (похожие объекты без клика).
- **Модель:** RT-DETR-R18 (Apache-2.0), экспорт ONNX; сравнение с YOLO11s только как
  baseline (AGPL — не в прод).
- **Метрики:** hit@2% диагонали, mAP50, ошибка количества, p95.
- **Exit:** hit@2% ≥ 0.90, mAP50 ≥ 0.85, p95 ≤ 100 мс, худший класс recall ≥ 0.75.
- **Срок:** 2 недели.

### 9.3 Hopscotch

- **Гипотеза:** текущая DINOv3 9-query модель + калибровка/rejector даёт 95% index и
  80% coordinate hit; если нет — heatmap-голова координат добавляет ≥5 п.п.
- **Данные:** 110 gold train, 3953 silver, 30 GoldVal, 400 blind. Blind не трогать.
- **Модель:** без смены backbone; добавить temperature scaling и rejector, измерить
  coverage-precision.
- **Метрики:** index top-1, coord hit@2% диагонали, joint, ECE, coverage.
- **Exit:** 95/80 на blind (или зафиксированный gap + beta-abstention по контракту),
  p95 ≤ 300 мс, ECE ≤ 0.05.
- **Срок:** 1–2 недели (модель уже есть, работа — калибровка и замер).

### 9.4 Роутер и fallback

- **Гипотеза:** ≤10% трафика уходит в fallback, и это ровно те кейсы, где head
  ошибается; precision на отвеченных ≥ 0.95.
- **Данные:** объединённые golden трёх типов + 100 «странных» кейсов.
- **Модель:** порог по score, VLM 4-bit на 24 ГБ ноде (если есть), иначе abstention.
- **Exit:** fallback rate ≤ 10%, coverage ≥ 0.90, precision ≥ 0.95, fallback p95 ≤ 3 с.

## 10. Последствия

**Положительные**

- Hot path дешёвый: 10–150 мс и ≤ 2 ГБ на голову; 8 ГБ хватает для core+heads.
- Калиброванный отказ: меньше «уверенных» неверных ответов, проще canary.
- Лицензии чистые: DINOv3/SigLIP2/RT-DETR/PaddleOCR/TrOCR — коммерчески допустимы.
- Формат артефактов не меняется: `[C,768]`, safetensors, `.pt`/ONNX.

**Отрицательные**

- Два энкодера и пять голов — больше кода обучения и версионирования.
- Нужна 24 ГБ нода под VLM-fallback, иначе coverage падает на сложных типах.
- RT-DETR требует нового загрузчика/ONNX-пути; это работа ml-platform/cv-grid.
- Разметка 50–100k тайлов и 25k изображений — время и деньги.

**Нейтральные**

- Lane-раскладка и solver ABI не меняются.
- `experimental`-флаги остаются как есть; тип получает `experimental=False` только
  после L3 canary (контракт §9).

## 11. Риски

| риск | вероятность | влияние | mitigation |
|---|---|---|---|
| DINOv3 gated / лицензия не принята | средняя | блокер core | принять условия, хранить копию LICENSE, SigLIP2-B/16 как резерв |
| YOLO11 AGPL в проде | высокая | юридический | RT-DETR; YOLO только внутренние эксперименты |
| 8 ГБ не тянут VLM | высокая | coverage | отдельная 24 ГБ нода; abstention без неё |
| Новый промпт вне словаря | высокая | падение grid | SigLIP2 text zero-shot + GroundingDINO fallback |
| Шум разметки / утечка | средняя | ложная точность | двойная разметка, pHash-дедуп, split по сайту |
| Hopscotch 95% недостижим | средняя | гейт | beta-abstention по контракту, честный отчёт gap |
| CC-BY-NC/scraped данные | высокая | юридический | только свой harvest в прод-обучение |
| Бюджет 30 мс CPU из ml-tz | высокая | срыв приёмки | переписать бюджет под GPU или уменьшить модель |
| PII в картинках | низкая | приватность | retention, redaction логов, не отправлять в внешние API |

## 12. Открытые вопросы к lead

1. Где L3-таблица? `production-contract.md` §10 ссылается на `plan.md` §2, но там её нет.
   Нужны пороги по типам, иначе «L3 пройден» — договорённость на словах.
2. Что именно значит hopscotch «95%/80%»: 95% index и 80% координат в допуске, или
   95% joint? В ADR принята первая трактовка; нужен ответ.
3. Есть ли бюджет на 1×24 ГБ GPU под VLM-fallback, или fallback только через
   abstention?
4. Приемлемо ли, что DINOv3 требует HF-токен и принятия лицензии в CI/офлайн-сборке?
5. Кто владеет ONNX-загрузчиком RT-DETR и когда он появляется (ml-platform/cv-grid)?
6. Юридическая позиция по harvest изображений с сайтов: ToS, consent, retention.

## 13. Ссылки

- [OpenCaptchaWorld](https://arxiv.org/abs/2505.24878) · [данные](https://huggingface.co/datasets/OpenCaptchaWorld/Open_CaptchaWorld)
- [CaptchaMind](https://arxiv.org/abs/2605.19538) · [CaptchaBench](https://huggingface.co/datasets/AIDC-AI/Captcha)
- [MCA-Bench](https://arxiv.org/abs/2506.05982) · [COGNITION](https://arxiv.org/abs/2512.02318)
- [DINOv3](https://github.com/facebookresearch/dinov3) · [DINOv2](https://github.com/facebookresearch/dinov2) · [SigLIP2](https://arxiv.org/abs/2502.14786) · [CLIP](https://github.com/openai/CLIP)
- [Qwen2.5-VL-7B](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct) · [Florence-2](https://huggingface.co/microsoft/Florence-2-large) · [PaliGemma 2](https://huggingface.co/google/paligemma2-3b-pt-448)
- [RT-DETR](https://github.com/lyuwenyu/RT-DETR) · [GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) · [YOLO11](https://docs.ultralytics.com/models/yolo11/)
- [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) · [TrOCR](https://arxiv.org/abs/2109.10282)
- `docs/ml-research.md` — лицензии, VRAM/latency, датасеты, evidence.
