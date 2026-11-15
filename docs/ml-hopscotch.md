# Hopscotch: custom-тип, обучение и подключение

Hopscotch — не один из 62 вендорских типов. Это custom-тип `CaptchaType.HOPSCOTCH`
(вне таксономии, lane `cv`): по изображению волны и подсказке выбрать карту, которая
совпадает с подсказкой. Контракт: [docs/production-contract.md](production-contract.md)
§1, §6, §9. Код-контракт сервиса: `src/captcha/solvers/hopscotch/`; тренировочный
kit — `ml/hopscotch/`. Модель обучается в отдельном репозитории
`D:/hopscotch_v2` (только чтение), здесь — экспорт, инференс-контракт, оценка и
публикация.

Текущее качество: **282/400 (70.5%)** на client validation и **27/30** на GoldVal30.
Цель приёмки: **95% при охвате 80%** (selective accuracy/coverage), поэтому тип
зарегистрирован как `experimental=True` и продаётся только при
`ALLOW_EXPERIMENTAL_TASKS=1`. Калиброванный релиз сейчас `not_ready`: модель
отказывается отвечать, а не угадывает.

## 1. Вход и выход

Payload (2captcha-совместимый createTask):

~~~json
{
  "type": "hopscotch",
  "image_b64": "<png/jpeg, 1000x400 для 5 карт или 1600x400 для 8>",
  "frames": 8,
  "hint": "k1"
}
~~~

- `image_b64` обязателен, data URI допускается, декодированный размер ≤ 32 МБ.
- `frames` опционален: ширина уже задаёт число карт (200 px на карту). Расхождение
  с картинкой или число вне `[5, 8]` — `ERROR_CAPTCHA_UNSOLVABLE`, не догадка.
- `hint` опционален и справочный: панель подсказки — часть изображения
  (низ-лево, 135×200), поэтому строка подсказки не влияет на ответ.
- Высота ровно 400 px; карты — верхние 200 строк, по 200 px ширины.

Ответ:

~~~json
{"index": 3, "coordinates": [650.0, 100.0]}
~~~

- `index` — номер карты **от 1**, как в клиентском индексе и в редакторе.
- `coordinates` — точка стопы фигуры выбранной карты в пикселях исходного
  изображения (x = (card_index + fx) * 200, y = fy * 200). Внутри модели ответ
  0-based; конверсия делается ровно один раз, в солвере.
- Отказ модели (низкий калиброванный score, невалидная структура, нет
  калибровки) → `ERROR_CAPTCHA_UNSOLVABLE` без списания. Это beta-политика до
  решения lead по abstention.

## 2. Данные

Два read-only источника в `D:/hopscotch_v2/dataset`:

| файл | что внутри |
|---|---|
| `raw/index.jsonl` | 4569 клиентских волн: `session_id`, `wave`, `difficulty` (5/8), `submitted_index` (**от 1**), `image` |
| `annotations.jsonl` | 150 ручных волн V2, `answer` — индекс массива (**от 0**) |
| `raw/sessions/` | 4719 исходных изображений (только чтение) |
| `splits/development-v4.json` | текущий frozen split (110/30/10 + client400) |

Ручная волна V2:

~~~json
{
  "v": 2,
  "wave": "session_10:wave_0",
  "hint": {"pt": null, "r": null, "key": "k1"},
  "frames": [
    {
      "fig": {"foot": [81, 93], "on": "k2"},
      "nodes": {"k1": {"pt": [153, 137], "r": 24, "vis": 2}, "...": {}}
    }
  ],
  "answer": 2,
  "flags": []
}
~~~

Инварианты (проверяются и в hopscotch_v2, и в `ml/hopscotch/dataset.py`):

- `v == 2`, `flags` пуст, `wave` = `<session>:wave_<n>`;
- одинаковый набор идентичностей `k1..k8` во всех кадрах, `fig.on` ∈ этот набор;
- `vis` ∈ {0, 1, 2}, `r` ∈ 1..100, координаты глобальные: x в
  `[200*frame, 200*(frame+1))`, y в `[0, 200)`;
- ровно один кадр, где `fig.on == hint.key`, и это `answer` (иначе разметка
  противоречива и в обучение не идёт).

Слияние источников: ручная разметка приоритетна на своём `wave`; клиентский
номер переводится в 0-based один раз. Одна волна — одна запись, дубликаты
`(session, wave)` — ошибка экспорта.

## 3. Frozen split

Протокол: 110 manual train / 30 manual validation / 10 honeypot, 201 client
calibration, 400 client validation, остальное — silver train (3953), 15 клиентских
волн-родственников ручных holdout исключены. Группы (сессии) не пересекаются.

Кит не пере-генерирует сплит, который дал baseline: при наличии
`dataset/splits/development-v4.json` роли импортируются оттуда verbatim, а хеши
двух источников фиксируются в нашем манифесте. Без upstream — детерминированная
нарезка `sha256(seed:wave)` с посессионным исключением.

~~~powershell
python -m ml.hopscotch dataset --data D:/hopscotch_v2 --out artifacts/hopscotch/split.json
~~~

Манифест: `format`, `protocol`, `source.*.sha256`, `upstream`, `counts`, `roles`,
`image_hashes`, `records`. Он иммутабелен (`--force` обязателен для замены), а
обёртка обучения отказывается стартовать, если хеш `index.jsonl` или
`annotations.jsonl` изменился: чекпоинт, обученный на другом сплите, нельзя
сравнивать с 70.5%.

## 4. Разметка редактором hopscotch_v2

Источник истины ручной разметки — `D:/hopscotch_v2/annotation/data/editor.sqlite3`
(SQLite + WAL). Редактор: `annotation/server.py` + `annotation/templates/editor.js`.

- Разметчик открывает волну, отмечает иконки как идентичности `k1..k8` (точка +
  радиус + видимость), ставит фигуру на карту (`fig.on`) и её стопу (`fig.foot`),
  выбирает карту-подсказку (`hint.key`) и ответ.
- В редакторе карты показываются **от 1**; экспорт V2 хранит `answer` **от 0**.
  Перевод делается при чтении, файлы не переписываются.
- Подтверждённые волны экспортируются в `dataset/annotations.jsonl`
  (`annotation/export.py`). Экспорт и SQLite не правятся нашим китом.
- Валидация V2 повторена в `ml/hopscotch/dataset.py` (порт `validate_full`), чтобы
  битая разметка отсекалась без импорта обучения.
- Флаги (`flags`) означают «не учить»: такие волны не попадают в манифест.

## 5. Обучение (обёртка, не тренер)

Тренер и модель живут в `D:/hopscotch_v2`; здесь только команда и preflight.

~~~powershell
python -m ml.hopscotch train --manifest artifacts/hopscotch/split.json ^
    --run readout-v2 --stage readout --config configs/v1-spatial.yaml
~~~

- `HOPSCOTCH_V2_ROOT` (default `D:/hopscotch_v2`), `HOPSCOTCH_PYTHON` — интерпретатор
  тренировочной среды (Python 3.14 + torch cu130), не venv сервиса.
- Вызов: `<python> <root>/scripts/v1.py --config <cfg> train ...` с cwd = root;
  никакие флаги не переизобретаются, `--dry-run` печатает команду.
- Preflight: хеши обоих источников против frozen split. Не сошлось — стоп.
- Кит не пишет в `D:/hopscotch_v2`: прогоны и артефакты создаёт сам репозиторий
  обучения по своим правилам.

Текущий baseline: 282/400 client validation (70.5%), 27/30 GoldVal30, чекпоинт
эпохи 39 (rotation-ветка), DINOv3 ViT-B/16 заморожен. 95% на текущей разметке не
достигнуто; эксперименты с identity bridge и поворотами прироста не дали.

## 6. Оценка

~~~powershell
python -m ml.hopscotch eval --checkpoint D:/hopscotch_v2/runs/<run>/best.pt ^
    --out artifacts/hopscotch/eval --release D:/hopscotch_v2/runs/<run>/release.json
~~~

Оцениваются `gold_val` (30) и `client_val` (400) через
`v1.py evaluate --role ...`. В `metrics.json` попадают `answer_accuracy`,
`coverage`, `selective_accuracy`, `nll`, `total`, `correct` — их export кладёт в
release. Цели: accuracy ≥ 0.95 при coverage ≥ 0.80; `score_predictions` умеет
пересчитать согласие по сохранённым предсказаниям офлайн.

## 7. Артефакт и публикация

Экспорт одного прогона:

~~~powershell
python -m ml.hopscotch export --run D:/hopscotch_v2/runs/<run> ^
    --out artifacts/hopscotch-v1/v1 --version v1 --metrics artifacts/hopscotch/eval/metrics.json
~~~

Получается тройка + контрольные суммы:

| файл | назначение |
|---|---|
| `model.safetensors` | полный serving state_dict (DINO + обучаемые блоки) |
| `config.json` | архитектура, `encoder_chunk`, `backbone_config`, геометрия входа, эпоха |
| `release.json` | статус, threshold, mean/scale/weights/bias, features, guards, metrics, хеш сплита |
| `checksums.json` | sha256 всех трёх файлов |

`config.json.input` фиксирует 200/400 px, 5 и 8 карт, hint-бокс `[0,200,135,400]`,
`index_base: 1`. Release без калибровки экспортируется как `not_ready`: веса
грузятся, но солвер отказывается отвечать (`decide()` → `uncalibrated`).
Release от другого чекпоинта (по `model_id`) отклоняется до записи файлов.

Окружение экспорта: `export`/`publish` импортируют схему артефакта из пакета
`captcha`, поэтому запускаются из проекта (`uv sync`; `--extra cv` даёт torch) или
с `PYTHONPATH=src`. `dataset`/`train`/`eval` зависят только от stdlib и работают в
тренировочном интерпретаторе.

Публикация:

~~~powershell
python -m ml.hopscotch publish --artifact artifacts/hopscotch-v1/v1 ^
    --version v1 --cache-dir models-cache
~~~

Раскладка бакета: `hopscotch-v1/<version>/{model.safetensors,config.json,release.json,checksums.json,meta.json}`
и `hopscotch-v1/latest` (тело = версия). `meta.json` совместим с
`captcha.adapters.weights.ModelMeta`, `s3_key` указывает на `model.safetensors`,
поэтому `make check-model NAME=hopscotch-v1` проверяет sha256 без изменений.
`--allow-not-ready` нужен для beta-артефакта; без него публикация `not_ready`
запрещена. Откат — `set_latest` на предыдущую версию.

## 8. Как солвер находит артефакт

Солвер не скачивает веса сам. Он берёт **локальный каталог артефакта**:

1. `SolverContext.extra["hopscotch_artifact_dir"]`;
2. `Settings.hopscotch_artifact_dir` (если поле появится в config);
3. env `HOPSCOTCH_ARTIFACT_DIR`.

Инференс: `SolverContext.extra["hopscotch_backend"]` (тесты/батч-сервер) либо
`V2Backend`, который по `HOPSCOTCH_V2_ROOT` собирает модель из
`config.json` + `model.safetensors` и вызывает
`src.data.v1_dataset.image_inputs` / `src.inference.rejector.evidence`. Требуются
torch и checkout обучения; без них солвер падает `ERR_CONFIG`, а не молча.

Важно: `D:/hopscotch_v2/src/inference/predict.py` сейчас **не парсится**
(`except ValueError, OSError:` — синтаксическая ошибка), поэтому солвер не
импортирует `Predictor`, а строит модель сам через `src.model.*` и
`src.inference.rejector`. Это отмечено в отчёте для владельца репозитория обучения.

## 9. Rejector и abstention

8 фиксированных признаков в порядке
`FEATURES`: answer_probability, answer_margin, hint_margin, support_margin,
hint_cosine, prototype_mass, appearance_consistency, eight_cards.
Score = sigmoid(bias + Σ w_i * (x_i - mean_i) / scale_i). Порядок признаков
заморожен: release с другим порядком не загрузится (ConfigError), чтобы
калибровка не ломалась молча.

Правило: нет ready-release → отказ; структурный guard не прошёл → отказ с
причиной; score < threshold → `low_correctness_score`; иначе ответ. Отказ
терминален для задачи (beta), не ретраится.

## 10. Active learning

Цель — 95/80. Данные дорогие (ручная разметка одной волны ≈ минуты), поэтому
добирать надо не «ещё 1000 волн», а те, где модель ошибается или сомневается.

Цикл:

1. **Harvest.** Прод-трафик типа (payload-изображения) складывать в S3 с
   `task_id`/датой; источник — только реальные волны, не скриншоты.
2. **Отбор.** Приоритет: score ниже threshold; расхождение answer vs hint
   (support ≠ hint); 8-карточные волны (63.7% против 80.8% на 5 картах);
   волны с поворотом/наложениями; ошибки, подтверждённые клиентом (report).
3. **Разметка.** Редактор `hopscotch_v2` → SQLite → `annotations.jsonl`.
   Никаких правок старых 150 волн: они входят в frozen split и в сравнение с
   baseline.
4. **Новый сплит.** `dataset` с новым seed/upstream; старый чекпоинт не
   продолжать (`train --resume` запрещён при смене сплита).
5. **Обучение и оценка.** `train` → `eval` (gold_val + client_val). Сравнение
   только с фиксированным baseline; honeypot — отдельной командой, в обучение
   не попадает.
6. **Экспорт/публикация.** `export` → `publish` с релизом `ready` только после
   прохождения цели; иначе beta с abstention.

Правила против переобучения: validation никогда не в train; калибровка — на
отдельных 201 волнах; тест-наборы не смотреть при выборе эпохи; метрики писать в
release.json, а не только в консоль.

## 11. Что не сделано

- 95/80 не достигнуто (70.5%); тип experimental, L3 canary не пройдена.
- Release в реестре пока не опубликован: нет готового калиброванного артефакта,
  а `not_ready` публикуется только с `--allow-not-ready`.
- `services/cli.py EXPECTED_MODELS` не содержит `hopscotch-v1` — это файл другого
  владельца (ml-platform), нужен отдельный коммит: строка артефакта и его схема.
- Инференс требует путь к checkout обучения; упаковка serving-бандла в образ
  (torch + `src/model`) — отдельная задача инфраструктуры.
- Runbook по шаблону `runbooks/_template.md` не заведён отдельно: этот файл
  выполняет его роль, пока тип не выйдет из beta.
