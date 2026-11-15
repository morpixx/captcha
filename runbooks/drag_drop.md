# Runbook: drag_drop

## 1. Snapshot

- **captcha type:** `CaptchaType.DRAG_DROP` — перетащить объект в цель, ответ = две точки и траектория.
- **antibot family:** нет (абстрактная геометрия).
- **lane:** `cv`, **mode:** `http` (`needs_gpu=True`).
- **tier:** 2 — `experimental=False` (`version 0.2.0`); весов в бакете нет.
- **code:** `solvers/detection/drag.py` + `captcha.behavior` (безье/тайминги).

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image` (или `image_b64`, `img`, `body`) | да | картинка, base64 |
| `source_target` (или `source`) | да | класс источника |
| `dest_target` (или `dest`) | да | класс цели |
| `conf` | нет | порог, дефолт 0.25 |
| `trajectory_points` | нет | 2..512, дефолт 48 |
| `seed` | нет | детерминированная траектория |
| `model`, `model_version`, `detector`, `device` | нет | override артефакта (`drag-v1`) |

```json
{"type": "drag_drop", "image": "<b64>", "source_target": "apple", "dest_target": "basket"}
```

## 3. Flow

1. Декод; детектор `drag-v1` одним вызовом на объединении классов источника и цели.
2. Лучший бокс на каждый набор; совпавшие центры (расстояние < 1 px) — `ERR_MODEL_INFERENCE`:
   перетаскивать нечего.
3. Траектория: `bezier_path(jitter=0.5, curvature=0.18)` + `humanize_timing(mean 16 ms, std 5 ms)`,
   точки `[x, y, t]`.

## 4. Solution keys

`SolutionKind.COORDS`: `coordinates` = `[[src_x, src_y], [dst_x, dst_y]]`. `extra`:
`trajectory` (`[[x, y, t]…]`), `src_conf`, `dst_conf`, `src_bbox`, `dst_bbox`, `model`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `image`; нет `source_target`/`dest_target`; `trajectory_points`/ `seed` вне диапазона | payload | проверить поля |
| `ERR_CONFIG` | нет `ctx.models` | деплой | проверить бакет |
| `ERR_MODEL_INFERENCE` | «no source detection» / «no destination detection» | класс не найден | сверить `class_names`, снизить `conf` |
| `ERR_MODEL_INFERENCE` | «source and destination detections coincide» | детект схлопнулся | проверить картинку |

## 6. Canary / фикстуры

- `tests/solvers/detection/fixtures/golden/images/drag-scene-0004.png` + `detection-v1.json`.
- Тесты: `tests/solvers/detection/test_detection_solvers.py`.
- Кейс (не добавлен):

```json
{"name": "drag-object", "clientKey": "<canary-key>",
 "task": {"type": "drag_drop", "image": "<b64>", "source_target": "apple", "dest_target": "basket"},
 "expect": {"status": "ready", "solution_contains": ["coordinates"]}}
```

## 7. FILL / live-risks

1. Траектория — наша, а не вендорская: вендор может ждать свои поля (`t`, дельты, паузы).
   Формат не сверялся ни с одним живым движком.
2. Один источник и одна цель; мульти-перетаскивание не поддерживается.
3. Без весов тип не отвечает (`ERR_CONFIG`).

## 8. Проверка

```bash
uv run pytest -q tests/solvers/detection
uv run ruff check src/captcha/solvers/detection
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [drag.py](../src/captcha/solvers/detection/drag.py),
  [detector.py](../src/captcha/solvers/detection/detector.py)
- поведение: `src/captcha/behavior/`
- протокол: [docs/protocol.md](../docs/protocol.md)
