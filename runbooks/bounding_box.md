# Runbook: bounding_box

## 1. Snapshot

- **captcha type:** `CaptchaType.BOUNDING_BOX` — «обведи объект», ответ = рамка `(x, y, w, h)`.
- **antibot family:** нет (абстрактная геометрия).
- **lane:** `cv`, **mode:** `http` (`needs_gpu=True`).
- **tier:** 2 — `experimental=False` (`version 0.2.0`); весов в бакете нет.
- **code:** `solvers/detection/{bbox,detector,detector_loader}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image` (или `image_b64`, `img`, `body`) | да | картинка, base64 |
| `target` (или `prompt`) | да | класс объекта |
| `conf` | нет | порог, дефолт 0.25 |
| `model`, `model_version`, `detector`, `device` | нет | override артефакта (`bbox-v1`) |

```json
{"type": "bounding_box", "image": "<b64>", "target": "cat"}
```

## 3. Flow

1. Декод картинки; мусор — `ERR_UNSOLVABLE`.
2. Детектор `bbox-v1` через `ctx.models` (ONNX RT-DETR).
3. Лучший бокс целевого класса: `(x1, y1, x2, y2)`.
4. В решение уходит `(x1, y1, x2-x1, y2-y1)`; исходный `xyxy` остаётся в `extra`.

## 4. Solution keys

`SolutionKind.BBOX`: `bbox: [x, y, w, h]`. `extra`: `confidence`, `xyxy`, `model`,
`n_detections`. Отдельной legacy-формы у bbox нет (в `docs/protocol.md` таблице `/res.php`
он не описан) — не проверено, принимает ли его legacy-клиент.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `image`/`target`; битая картинка | payload | проверить поля |
| `ERR_CONFIG` | нет `ctx.models` / битая meta | деплой или артефакт | проверить бакет |
| `ERR_MODEL_INFERENCE` | «no detections for target» | класс/порог | сверить `class_names`/`conf` |

## 6. Canary / фикстуры

- `tests/solvers/detection/fixtures/golden/images/bbox-scene-0002.png` + `detection-v1.json`.
- Тесты: `tests/solvers/detection/test_detection_solvers.py`, `test_golden_detection.py`.
- Кейс (не добавлен):

```json
{"name": "bbox-object", "clientKey": "<canary-key>",
 "task": {"type": "bounding_box", "image": "<b64>", "target": "cat"},
 "expect": {"status": "ready", "solution_contains": ["bbox"]}}
```

## 7. FILL / live-risks

1. Порядок ключей `(x, y, w, h)` — наш контракт: 2captcha-стандарта для bbox нет
   (`domain/solution.py`, docstring). Клиенту нельзя присылать `xyxy` в `bbox`.
2. Без весов тип не отвечает (`ERR_CONFIG`).
3. Один объект. Несколько рамок не поддерживаются — это `draw_around`/grid.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/detection
uv run ruff check src/captcha/solvers/detection
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [bbox.py](../src/captcha/solvers/detection/bbox.py),
  [detector.py](../src/captcha/solvers/detection/detector.py)
- протокол: [docs/protocol.md](../docs/protocol.md)