# ML research: open-source visual stack

**Статус:** research input к [ADR-001](adr-vision-architecture.md). Не контракт.
**Дата:** 2026-11-15. **Скоуп:** только визуальные модели; audio/LLM-текст — вне.
**Метод:** карточки моделей и лицензии проверены по первоисточникам (HF API, LICENSE,
README) 2026-11-15; параметры — из HF API; latency/VRAM — **оценки** по roofline-модели
(§8), а не замеры на целевом железе. В базовом venv нет torch, поэтому замеры не делались.
Всё, что помечено **est**, обязано быть заменено замером `tools/ml_probe` до приёмки.

## 0. Короткий вывод

| роль | основной кандидат | резерв | почему |
|---|---|---|---|
| visual core | DINOv3 ViT-B/16, 85.7M | SigLIP2-B/16 (Apache), CLIP-L/14 (MIT) | плотные фичи, 16px патч, 768-d пул — совпадает с текущим `head.py` |
| grid-голова | линейная/MLP на замороженных фичах | SigLIP2 text tower для новых промптов zero-shot | 50–100k тайлов, обучение за минуты на CPU |
| детекция | RT-DETR-R18/R34 (Apache) | GroundingDINO-T (open-vocab) | YOLO11 — AGPL-3.0, в закрытый сервис нельзя без коммерческой лицензии |
| OCR | PaddleOCR PP-OCRv5 (Apache) | TrOCR-base (MIT) | первый дефолт по цене/качеству, второй — на трудный текст |
| геометрия | классика (edge/template) + малый регрессор | DINOv3-фичи + MLP | поворот/слайдер не требуют большой модели |
| matching | DINOv3 patch features + query-голова | — | уже работает в hopscotch: 70.5% на 400 blind |
| VLM fallback | Qwen2.5-VL-7B (Apache), 4-bit | Florence-2-large (MIT) | только вне hot path, на ноде ≥24 ГБ |

Главный вывод: **VLM-only не проходит** ни по локализации (OpenCaptchaWorld: лучший
агент 40.0% против 93.3% у человека), ни по железу (7B bf16 — 16.6 ГБ, у нас 8 ГБ),
ни по цене за задачу. **Core + heads** не покрывает open-vocabulary и новые промпты.
Решение — hybrid: heads на hot path, VLM — fallback по калиброванному отказу.

## 1. Что именно нужно решать

В `domain/catalog.py` 62 типа: 40 в lane `cv`, 20 в `http`, 2 в `protocol`.
Но «визуальная задача» есть не у всех 40: часть cv-типов — это протокол с картинкой
внутри (слайдер/клик-виджет), где сеть нужна только для одного шага.

| семейство | типов | что решает модель | текущий артефакт |
|---|---|---|---|
| OCR текста | 3 | строка/формула по картинке | нет весов, PaddleOCR/TrOCR |
| grid-классификация | 11 | какие тайлы содержат объект X | `hcaptcha-v1`, `recaptcha-v1`, `funcap-v1`, `awswaf-v1`, `shein-v1` |
| click/bbox/draw/drag | 5 | точка/бокс/контур объекта | `click-v1`, `bbox-v1`, `drag-v1`, `draw-v1` |
| rotate | 1–2 | угол | `rotate-v1` |
| slider/gap | ~15 | смещение паза | классика OpenCV, весов нет |
| matching/selection | 1+ | какой элемент выбран | `hopscotch-v1` |
| audio | 2 | речь → текст | Whisper |
| protocol/PoW/antibot | ~20 | не vision | сенсоры, JS, PoW |

Отсюда приоритет: grid (11 типов на одну голову), click (5 типов на один детектор),
hopscotch (кастомный гейт 95/80). Всё остальное — либо классика, либо уже закрыто
протоколом.

## 2. Энкодеры

Параметры — HF API; VRAM — только веса в fp16; latency — **est** для batch 1, 224×224,
GPU = RTX 5060 Laptop 8 ГБ (30% MFU), CPU = 16 ядер.

| модель | params | лицензия | VRAM весов | GPU, мс | CPU, мс | роль |
|---|---|---|---|---|---|---|
| DINOv3 ViT-S/16 | 21.6M | DINOv3 License (custom) | 0.04 ГБ | 5–10 | 50–100 | быстрый край |
| **DINOv3 ViT-B/16** | **85.7M** | DINOv3 License | **0.17 ГБ** | **10–20** | **150–300** | **core** |
| DINOv3 ViT-L/16 | 303M | DINOv3 License | 0.61 ГБ | 25–50 | 500–900 | если нужен запас |
| DINOv3 ViT-H+/16 | 841M | DINOv3 License | 1.7 ГБ | 60–120 | 1.5–3 с | не для 8 ГБ |
| DINOv3 ViT-7B/16 | 6.72B | DINOv3 License | 13.4 ГБ | — | — | не влезает |
| DINOv2 ViT-B/14 | 86.6M | Apache-2.0 | 0.17 ГБ | 10–20 | 150–300 | безопасный fallback |
| DINOv2 ViT-L/14 | 304M | Apache-2.0 | 0.61 ГБ | 25–50 | 500–900 | — |
| SigLIP2-B/16 | 375M (vision+text) | Apache-2.0 | 0.75 ГБ | 10–25 | 200–400 | второй энкодер, text tower |
| SigLIP2 So400m/14 | 1.14B | Apache-2.0 | 2.3 ГБ | 40–80 | 1–2 с | тяжело для 8 ГБ |
| CLIP ViT-L/14 | 428M | MIT | 0.86 ГБ | 25–50 | 500–900 | baseline |

Детали, которые влияют на решение:

- **DINOv3.** Лицензия не OSI, но запрета на коммерческое использование в ней нет:
  грант «non-exclusive, worldwide, non-transferable and royalty-free», распространение —
  только вместе с копией соглашения, публикации — с attribution, п. 1(b)(iv) запрещает
  reverse engineering «underlying components». Вес на HF **gated** (manual approval):
  CI и разработчикам нужен токен и принятие условий. Код и веса — под одним DINOv3
  License; отдельного Apache для кода в репозитории нет.
- **DINOv2.** В текущем репозитории стандартные веса уже **Apache-2.0** (README и
  LICENSE), старые упоминания CC-BY-NC относятся к X-ray/cell-вариантам. Перед
  публикацией артефакта фиксировать commit/LICENSE, а не верить карточке зеркала.
- **SigLIP2.** Apache-2.0, мультиязычный text tower. Это единственный дешёвый способ
  классифицировать **новый промпт без переобучения головы**: эмбеддинг текста против
  эмбеддинга тайла. Минус — 375M суммарно и 1.14B у So400m.
- **CLIP.** MIT, зрелый, но плотные фичи слабее DINOv3; держим как baseline, не как core.
- **768-d.** DINOv3 ViT-B/16 и SigLIP2-B/16 — B-класс, 768-d, как у текущего
  `google/vit-base-patch16-224-in21k`. Контракт `classifier.weight [C,768]` не ломается;
  `meta.base_backbone` должен указывать реальный энкодер, иначе загрузчик соберёт не ту модель.

## 3. VLM

| модель | params | лицензия | VRAM bf16 | 4-bit | latency **est** | вывод |
|---|---|---|---|---|---|---|
| Qwen2.5-VL-3B | 3.75B | **qwen-research (non-commercial)** | 7.5 ГБ | ~2.2 ГБ | 0.9–1.5 с | в прод нельзя; 8 ГБ впритык даже в bf16 |
| **Qwen2.5-VL-7B** | 8.29B | **Apache-2.0** | 16.6 ГБ | ~4.4 ГБ | 0.8–1.5 с (4-bit) / 2–3 с (bf16, 24 ГБ) | единственный допустимый VLM-fallback |
| Qwen2.5-VL-72B | 73.4B | Qwen license (MAU-условия) | 147 ГБ | — | — | не рассматриваем |
| Florence-2-base | 232M | MIT | 0.46 ГБ | — | 30–60 мс | caption/OCR/grounding, слабый instruction-following |
| Florence-2-large | 770M | MIT | 1.5 ГБ | — | 60–120 мс | то же, точнее |
| PaliGemma 2 3B | 3.03B | **Gemma Terms of Use** (не OSI) | 6.1 ГБ | ~2 ГБ | 0.8–1.5 с | лицензия требует legal review; gated |
| PaliGemma 2 10B | 9.66B | Gemma Terms | 19.3 ГБ | — | 2–4 с | не для 8 ГБ |

Практические ограничения:

- 8 ГБ не держат 7B в bf16. Либо 4-bit на 8 ГБ (качество падает на мелкой локализации),
  либо отдельная нода 24 ГБ. Второе — рекомендация ADR: fallback не должен делить GPU
  с hot path.
- 3B не годится не только по памяти: лицензия `qwen-research` запрещает коммерческое
  использование. Это отсекает самый удобный по размеру вариант.
- Florence-2 дёшев и MIT, но это seq2seq-модель под конкретные задачи (`<OCR>`,
  `<CAPTION>`, `<GROUNDING>`), а не универсальный агент; для «выбери все тайлы с X»
  нужен свой парсер и калибровка.
- PaliGemma 2 под Gemma Terms: коммерческое использование разрешено, но с ограничениями
  и правом Google ограничить распространение; для закрытого сервиса нужен формальный
  review, поэтому не дефолт.

## 4. Детекторы

| модель | params | FLOPs @640 | лицензия | CPU, мс | T4/GPU, мс | роль |
|---|---|---|---|---|---|---|
| YOLO11n | 2.6M | 6.5B | AGPL-3.0 | 56 | 1.5 | только эксперимент |
| YOLO11s | 9.4M | 21.6B | AGPL-3.0 | 90 | 2.5 | baseline, не прод |
| YOLO11m | 20.1M | 68.1B | AGPL-3.0 | 183 | 4.7 | — |
| RT-DETR-R18 | 20M | 60B | Apache-2.0 | ~500–900 **est** | ~5–10 **est** | прод-кандидат |
| RT-DETR-R34 | 31M | 92B | Apache-2.0 | ~700–1200 **est** | ~7–14 **est** | если R18 мало |
| RT-DETR-R50 | 42M | 136B | Apache-2.0 | — | 53.1 AP, 108 FPS T4 | потолок качества |
| GroundingDINO-T | 172M | ~150B **est** | Apache-2.0 | 2–4 с **est** | 30–70 **est** | open-vocab fallback |
| GroundingDINO-B | 233M | ~200B **est** | Apache-2.0 | — | 50–100 **est** | то же, точнее |

- **AGPL — блокер.** Ultralytics YOLO11 под AGPL-3.0: сетевой сервис, который её
  использует, обязан открыть исходники. Для закрытого продукта нужна коммерческая
  лицензия Ultralytics. Текущий `detection/_yolo.py` грузит `.pt` Ultralytics — это
  юридический риск, а не только технический.
- **RT-DETR** (официальный репозиторий, Apache-2.0) — прямая замена по качеству/скорости.
  Минус: другой формат чекпоинта; текущий контракт «обычный `.pt` Ultralytics» под него
  не подходит, нужен ONNX-путь или отдельный загрузчик (это работа ml-platform/cv-grid,
  не ml-research).
- **GroundingDINO** решает главную слабость фиксированного словаря: промпт «crosswalk»
  можно подать текстом, без переобучения. Стоимость — в 3–10 раз медленнее YOLO/RT-DETR,
  поэтому только fallback.

## 5. OCR

| модель | params | лицензия | CPU, мс | GPU, мс | примечание |
|---|---|---|---|---|---|
| PaddleOCR PP-OCRv5 mobile (det+rec) | ~5–10M | Apache-2.0 | 30–100 | 5–15 | лучший дефолт, мультиязычный |
| PaddleOCR PP-OCRv5 server | ~80–100M **est** | Apache-2.0 | 100–300 | 10–30 | если mobile не хватает |
| TrOCR-small | 61M | MIT | 100–200 | 10–20 | только строка, нужен кроп |
| TrOCR-base | 333M | MIT | 300–600 | 20–50 | печать/рукопись, хуже на искажениях |
| deep-text-recognition-benchmark (CRNN) | — | Apache-2.0 | — | — | код обучения под капчу-шрифты |

Важно: `--extra cv` в `pyproject.toml` содержит только torch/torchvision/opencv/soundfile.
Ни `paddleocr`, ни `transformers` туда не входят, а `solvers/ocr/_engines.py` подсказывает
установку из несуществующего `solvers/ocr[paddle]`. То есть «PaddleOCR/TrOCR из коробки»
из `docs/ml.md` сейчас неверно: без отдельной установки `image_to_text`/`math_image`
падают в `ModelInferenceError`. Это не моя зона правки, фиксирую как риск для ml-platform.

## 6. Публичные данные и репозитории

| источник | что внутри | лицензия | годится |
|---|---|---|---|
| [OpenCaptchaWorld](https://huggingface.co/datasets/OpenCaptchaWorld/Open_CaptchaWorld) | 20 типов, 225 интерактивных пазлов + web-платформа | Apache-2.0 | benchmark, не train |
| [CaptchaBench / AIDC-AI](https://huggingface.co/datasets/AIDC-AI/Captcha) | 16k сгенерированных, 8 типов, process-аннотации | **CC-BY-NC-4.0** | только research |
| [CaptchaArena](https://huggingface.co/datasets/ZHEN-04/CaptchaArena) | grid/click, gated | **CC-BY-NC-4.0** | нельзя в прод |
| [orlov-ai/hcaptcha-dataset](https://github.com/orlov-ai/hcaptcha-dataset) | ~4.1k hCaptcha-картинок, 8 классов, ручная разметка | MIT-репо, но README: «educational / fair use» | research; права на картинки неясны |
| [xtekky/hcaptcha-dataset](https://github.com/xtekky/hcaptcha-dataset) | скрейп hCaptcha | MIT-репо, provenance hCaptcha ToS | research |
| [tcsenpai/aggregated_captcha_images_and_text](https://huggingface.co/datasets/tcsenpai/aggregated_captcha_images_and_text) | сотни тысяч картинок + текст | **CC-BY-NC-4.0** | нельзя в прод |
| [ssivakorn/recaptcha-challenge](https://www.kaggle.com/datasets/ssivakorn/recaptcha-challenge) | размеченные reCAPTCHA-челленджи | не указана | нельзя без clarification |
| [CaptchaSolve30k](https://huggingface.co/datasets/Capycap-AI/CaptchaSolve30k) | 30k человеческих mouse-траекторий | Apache-2.0 | behavior, не vision |
| [szili2011/captcha-ocr-dataset](https://huggingface.co/datasets/szili2011/captcha-ocr-dataset) | 1.8M синтетических OCR-капч | Apache-2.0 | pretrain OCR |
| [hcaptcha-challenger](https://github.com/QIN2DIM/hcaptcha-challenger) | ONNX MoE-решатель hCaptcha | **GPL-3.0** | только идеи, код/веса нельзя |
| [uncaptcha2](https://github.com/ecthros/uncaptcha2) | audio reCAPTCHA | лицензии нет | нельзя |
| [deep-text-recognition-benchmark](https://github.com/clovaai/deep-text-recognition-benchmark) | OCR-пайплайн | Apache-2.0 | train OCR |

Вывод по данным: **публичного grid-датасета с чистой лицензией и честной разметкой нет.**
Всё, что есть, — либо scraped (права на изображения у владельца капчи), либо
non-commercial, либо синтетика. Для продакшена — только собственный harvest через свой
gateway (с логированием согласия/ToS) + ручная разметка. Публичные наборы — smoke и
сравнение архитектур, не обучение прод-модели.

## 7. Что говорят бенчмарки

- **OpenCaptchaWorld** (NeurIPS 2025, [arXiv:2505.24878](https://arxiv.org/abs/2505.24878)):
  20 типов, 225 пазлов, метрика reasoning depth. Лучший агент — Browser-Use OpenAI-o3,
  **40.0%**; человек — **93.3%**. Разрыв именно в интерактивных, многошаговых задачах.
- **CaptchaMind** (EMNLP 2026, [arXiv:2605.19538](https://arxiv.org/abs/2605.19538)):
  Qwen2.5-VL-7B + SFT + GRPO с супервизией grounding-регионов даёт **82.9%** на
  CaptchaBench и **71.0%** на real-world. То есть даже специализированная 7B-модель
  с 16k обучающих сэмплов не добивает до продакшн-порога 95% на реальных капчах.
- **MCA-Bench** ([arXiv:2506.05982](https://arxiv.org/abs/2506.05982)): LoRA-агенты на
  Qwen2.5-VL-7B сильны на визуальных/мелкосемантических типах и проваливаются на
  физическом/многошаговом reasoning; сложность и глубина взаимодействия коррелируют
  с solvability.
- **COGNITION** ([arXiv:2512.02318](https://arxiv.org/abs/2512.02318)): 7 MLLM, 18 типов;
  recognition и low-interaction задачи решаются на уровне человека по цене/латентности,
  fine-grained localization, multi-step spatial reasoning и cross-frame consistency —
  остаются тяжёлыми.
- **Внутренний hopscotch:** DINOv3 ViT-B/16 + 9 query-голов — **282/400 (70.5%)** на
  клиентской валидации и **27/30** на GoldVal. Это уже core+head, а не VLM, и это
  лучший имеющийся signal, что выбранное направление работает.

Итог: распознавание «что на картинке» VLM делает хорошо; «где именно и в каком порядке»
— плохо. Ровно там, где нужны координаты и сетка, task head на плотных фичах дешевле и
точнее.

## 8. VRAM и latency: модель оценки

Оценка, не замер. Формулы:

```
VRAM ≈ P·b + A + KV            # P параметры, b байт/параметр, A активации, KV кэш
t_prefill ≈ 2·P·T / F_eff      # T токенов входа
t_decode  ≈ P·b / BW           # memory-bound, на 1 токен
```

Допущения: bf16 = 2 байта; 4-bit ≈ 0.55 байта с масштабами; активации ViT 224 — 0.5–1 ГБ,
1024² — 2–4 ГБ без flash-attention; RTX 5060 Laptop: 8 ГБ, ~384 ГБ/с, F_eff ≈ 20 TFLOPS;
RTX 4090: 24 ГБ, ~1008 ГБ/с, F_eff ≈ 60 TFLOPS; CPU 16 ядер: 150–250 GFLOPS fp32.

| нагрузка | FLOPs/токены | GPU 5060 **est** | CPU **est** |
|---|---|---|---|
| ViT-B/16 @224, 1 тайл | 17.6B | 10–20 мс | 150–300 мс |
| ViT-B/16 @224, 9 тайлов | 158B | 30–60 мс | 1.5–3 с |
| ViT-B/16 @1024 (4096 токенов) | 368B | 25–50 мс | 3–6 с |
| hopscotch 1000×1600 (6300 токенов) | 565B | 40–90 мс | 5–10 с |
| YOLO11s @640 | 21.6B | 2–5 мс | 90–150 мс |
| RT-DETR-R18 @640 | 60B | 5–10 мс | 500–900 мс |
| GroundingDINO-T @800×1333 | ~150B | 30–70 мс | 2–4 с |
| TrOCR-base, строка | ~40B | 20–40 мс | 300–600 мс |
| PaddleOCR mobile, страница | ~5B | 5–15 мс | 30–100 мс |
| Qwen2.5-VL-3B, 1024 prefill + 30 decode | 7.7T + 0.2T | 0.9–1.5 с | — |
| Qwen2.5-VL-7B, то же, bf16 | 17T + 0.5T | 2–3 с (нужно 24 ГБ) | — |
| Qwen2.5-VL-7B, 4-bit | — | 0.8–1.5 с | — |

Расхождение с `docs/ml-tz.md`: там бюджет «голова на 9 тайлов ≤ 30 мс на CPU». По
roofline это 1.5–3 с для ViT-B/16; 30 мс на CPU достижимы только для модели класса
MobileNet/CLIP-B/32 на 9 кропах, и то с натяжкой. Либо бюджет переписывается под GPU,
либо grid-голова должна быть сильно меньше. Это вопрос к lead: контрактный бюджет и
фактическое железо сейчас противоречат друг другу.

## 9. Что это значит для текущих артефактов

- `classifier.weight [C,768]` + `meta.base_backbone`: DINOv3-B/16 и SigLIP2-B/16 —
  B-класс с 768-d пулом (сверить `config.json` при публикации артефакта), формат
  `safetensors` не меняется. Меняется только имя backbone в meta и препроцесс
  (нормализация/размер входа). Это дешёвая миграция.
- Детекторы: текущий контракт — Ultralytics `.pt`. Переход на RT-DETR требует ONNX или
  нового загрузчика; это изменение адаптера, а не солвера. Пока решение не принято,
  YOLO11 остаётся, но с юридическим флагом.
- `rotate-v1` (ResNet50): можно оставить как есть; DINOv3+MLP даст лучше, но выигрыш
  не оправдывает ещё один формат артефакта.
- `hopscotch-v1` уже на DINOv3 ViT-B/16 — совпадает с core, ничего переписывать не надо.
- OCR: артефактов нет, нужен выбор engine + установка зависимостей (сейчас их нет в extras).

## 10. Дыры, которые закрывает прототип

1. Нет ни одного замера latency/VRAM на 5060 — таблицы §8 аналитические.
2. Нет golden-наборов; hopscotch 400 blind — единственный замороженный набор.
3. Неизвестно, сколько стоит grid-голова в качестве на реальных hCaptcha/reCAPTCHA
   после смены backbone (ViT-B/16 ImageNet → DINOv3).
4. Не проверено, тянет ли 4-bit Qwen2.5-VL-7B координаты на grid/click (литература
   говорит «плохо», но нужен замер на наших данных).
5. Лицензионный аудит датасетов не завершён: у MCA-Bench и Kaggle-наборов лицензия
   не подтверждена.
6. `docs/ml.md` обещает OCR «из коробки», которого нет в extras; `solvers/ocr/_engines.py`
   ссылается на несуществующий `solvers/ocr[paddle]`.

## 11. Ссылки

- DINOv3: [repo](https://github.com/facebookresearch/dinov3), [LICENSE](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md), [arXiv:2508.10104](https://arxiv.org/abs/2508.10104)
- DINOv2: [repo](https://github.com/facebookresearch/dinov2), [arXiv:2304.07193](https://arxiv.org/abs/2304.07193)
- SigLIP2: [HF](https://huggingface.co/google/siglip2-base-patch16-224), [arXiv:2502.14786](https://arxiv.org/abs/2502.14786)
- CLIP: [repo](https://github.com/openai/CLIP), [arXiv:2103.00020](https://arxiv.org/abs/2103.00020)
- Qwen2.5-VL: [7B](https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct), [3B](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct), [arXiv:2502.13923](https://arxiv.org/abs/2502.13923)
- Florence-2: [HF](https://huggingface.co/microsoft/Florence-2-large), [arXiv:2311.06242](https://arxiv.org/abs/2311.06242)
- PaliGemma 2: [HF](https://huggingface.co/google/paligemma2-3b-pt-448), [Terms](https://ai.google.dev/gemma/terms), [arXiv:2412.03555](https://arxiv.org/abs/2412.03555)
- YOLO11: [docs](https://docs.ultralytics.com/models/yolo11/), [AGPL-3.0](https://github.com/ultralytics/ultralytics/blob/main/LICENSE)
- RT-DETR: [repo](https://github.com/lyuwenyu/RT-DETR), [arXiv:2304.08069](https://arxiv.org/abs/2304.08069)
- GroundingDINO: [repo](https://github.com/IDEA-Research/GroundingDINO), [arXiv:2303.05499](https://arxiv.org/abs/2303.05499)
- TrOCR: [HF](https://huggingface.co/microsoft/trocr-base-printed), [arXiv:2109.10282](https://arxiv.org/abs/2109.10282)
- PaddleOCR: [repo](https://github.com/PaddlePaddle/PaddleOCR)
