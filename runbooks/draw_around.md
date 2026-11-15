# Runbook: draw_around

## 1. Snapshot

- **captcha type:** `CaptchaType.DRAW_AROUND` — обвести объект полигоном, ответ = список вершин.
- **antibot family:** нет (абстрактная геометрия).
- **lane:** `cv`, **mode:** `http` (`needs_gpu=True`).
- **tier:** 2 — `experimental=False` (`version 0.2.0`); весов в бакете нет.
- **code:** `solvers/detection/draw.py` (+ общий `detector.py`).

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image` (или `image_b64`, `img`, `body`) | да | картинка, base64 |
| `target` (или `prompt`) | да | класс объекта |
| `conf` | нет | порог, дефолт 0.25 |
| `model`, `model_version`, `detector`, `device` | нет | override артефакта (`draw-v1`) |

```json
{"type": "draw_around", "image": "<b64>", "target": "dog"}
```

## 3. Flow

1. Декод, детектор `draw-v1` (`ctx.models`, ONNX RT-DETR).
2. Лучший бокс целевого класса → полигон из четырёх углов `(x1,y1) (x2,y1) (x2,y2) (x1,y2)`.
3. В `extra.source = "bbox_rect"` честно сказано, что это прямоугольник, а не маска:
   сегментационной головы в контракте детектора нет.

## 4. Solution keys

`SolutionKind.COORDS`: `coordinates` = 4 вершины в пикселях. `extra`: `confidence`, `model`,
`n_vertices`, `source`. Legacy-форма — `coordinate:x=..,y=..;...`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `image`/`target`; битая картинка | payload | проверить поля |
| `ERR_CONFIG` | нет `ctx.models`; битая meta | деплой | проверить бакет |
| `ERR_MODEL_INFERENCE` | «no detections for target» | класс/порог | сверить `class_names` |

## 6. Canary / фикстуры

- `tests/solvers/detection/fixtures/golden/images/draw-scene-0003.png` + `detection-v1.json`.
- Тесты: `tests/solvers/detection/test_detection_solvers.py`.
- Кейс (не добавлен):

```json
{"name": "draw-object", "clientKey": "<canary-key>",
 "task": {"type": "draw_around", "image": "<b64>", "target": "dog"},
 "expect": {"status": "ready", "solution_contains": ["coordinates"]}}
```

## 7. FILL / live-risks

1. Полигон — прямоугольник по боксу. Вендор, который сверяет площадь/контур, будет считать это
   грубым попаданием; метрика IoU по живым сайтам не измерялась.
2. Один объект; порядок вершин — по часовой от левого верхнего угла. Стандарта нет, это наш
   контракт.
3. Без весов тип не отвечает (`ERR_CONFIG`).

## 8. Проверка

```bash
uv run pytest -q tests/solvers/detection
uv run ruff check src/captcha/solvers/detection
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [draw.py](../src/captcha/solvers/detection/draw.py),
  [detector.py](../src/captcha/solvers/detection/detector.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
