# Runbook: click (+ coordinates)

## 1. Snapshot

- **captcha types:** `CaptchaType.CLICK`, `CaptchaType.COORDINATES` — один детектор, разные подписи задачи: «клик по объекту» и «координаты объекта».
- **antibot family:** нет (абстрактная геометрия).
- **lane:** `cv`, **mode:** `http` (`needs_gpu=True`).
- **tier:** 2 — `experimental=False` (`version 0.2.0`); весов в бакете нет, live не измерен.
- **code:** `solvers/detection/{click,detector,detector_loader,detector_spec,onnx_detector}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image` (или `image_b64`, `img`, `body`) | да | картинка, base64 |
| `target` (или `prompt`) | да | класс объекта; имя из `class_names` детектора |
| `conf` | нет | порог, дефолт 0.25 (`coerce_confidence`) |
| `model`, `model_version`, `detector`, `device` | нет | override артефакта: `click-v1`, `latest`, `auto`, `cpu` |

```json
{"type": "click", "image": "<b64>", "target": "car"}
```

## 3. Flow

1. Декод изображения; мусор — `ERR_UNSOLVABLE`.
2. `detector_for_task(ctx, payload, "click-v1")` → `load_detector` (`ctx.models` обязателен;
   production-путь только ONNX RT-DETR, Ultralytics — лишь с `allow_dev`).
3. `resolve_class_ids(class_names, target)` — неизвестный класс даёт пустой набор, а не «угадай».
4. `predict(rgb, conf, classes)` → лучший детект по целевому классу; ничего не найдено —
   `ERR_MODEL_INFERENCE` (не пустой ответ).
5. Ответ — центр лучшего бокса.

## 4. Solution keys

`SolutionKind.COORDS`: `coordinates: [[x, y]]` — пиксели входной картинки, не CSS.
`extra`: `confidence`, `bbox` (`[x1,y1,x2,y2]`), `model`, `n_detections`. Legacy-форма —
`click:83,22` / `coordinate:x=39,y=59` (`docs/protocol.md`).

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `image`/`target`; нечитаемая картинка | payload | проверить поля |
| `ERR_CONFIG` | «detection solver requires ctx.models» | деплой без реестра моделей | проверить `S3_*`, `--extra cv` |
| `ERR_MODEL_INFERENCE` | «no detections for target» | класс не в модели / низкий `conf` | сверить `class_names`, снизить `conf` |
| `ERR_CONFIG` | «models registry returned unusable detector meta» | битый `meta.json` артефакта | перепубликовать |

## 6. Canary / фикстуры

- `tests/solvers/detection/fixtures/golden/detection-v1.json` +
  `images/click-scene-0001.png` (+ `README.md`).
- Тесты: `test_detection_solvers.py`, `test_detector_contract.py`, `test_golden_detection.py`,
  `test_onnx_detector.py`.
- Кейс (не добавлен; нужна картинка + имя класса из модели):

```json
{"name": "click-object", "clientKey": "<canary-key>",
 "task": {"type": "click", "image": "<b64>", "target": "car"},
 "expect": {"status": "ready", "solution_contains": ["coordinates"]}}
```

## 7. FILL / live-risks

1. Без весов `click-v1` тип не отвечает (`ERR_CONFIG`); в baseline весов нет.
2. Один клик. Мульти-клик (hCaptcha-подобные «выбери все») сюда не входит — это
   `classification_grid`.
3. Координаты не масштабируются под CSS: клиент должен знать исходный размер картинки.
4. Точность на живых сценах не измерена.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/detection
uv run ruff check src/captcha/solvers/detection
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [click.py](../src/captcha/solvers/detection/click.py),
  [detector.py](../src/captcha/solvers/detection/detector.py),
  [detector_loader.py](../src/captcha/solvers/detection/detector_loader.py)
- родственные: [bounding_box.md](bounding_box.md), [draw_around.md](draw_around.md),
  [drag_drop.md](drag_drop.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
