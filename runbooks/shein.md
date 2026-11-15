# Runbook: shein

## 1. Snapshot

- **captcha type:** `CaptchaType.SHEIN` (`shein`) — bespoke image-selection капча Shein: простая сетка, узкий словарь (обувь/сумки/одежда), хороший свет.
- **antibot family:** нет отдельного `AntibotEngine`. Grid-голова `shein-v1` в `solvers/classification/`.
- **lane:** `cv`, **mode:** `http`, `needs_gpu=True`.
- **tier:** 3 — `experimental=True`; весов нет.
- **code:** `solvers/classification/shein.py`, общий путь — `classification/base_grid.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image_b64` | да | base64 сетки; алиасы `image`, `body` |
| `prompt` | да | класс товара; алиасы `question`, `target`, `textinstructions` |
| `rows` / `cols` | нет | только вместе; `shein-v1` — 3×3 |
| `head` | нет | другая голова из `grid.HEADS` |
| `threshold` | нет | порог 0..1 |

```json
{"type": "shein", "image_b64": "<base64 png 3x3>", "prompt": "shoes"}
```

## 3. Endpoints / flow

Сети нет; путь целиком в `base_grid.GridClassifier.solve_http`.

1. `resolve_grid` (3×3 по умолчанию; иная сетка → `ERR_UNSOLVABLE`).
2. `LoRAHead.load(..., name="shein-v1", backbone="google/vit-base-patch16-224-in21k")`; `ctx.models is None` → `ERR_CONFIG`.
3. Декод base64, `slice_grid` на 9 плиток, батч, `ViTBackbone.encode`.
4. `head.pick_tiles_for_prompt`: метка из `prompt` через `prompt.resolve_label` (алиасы головы), порог из головы либо payload.
5. Индексы плиток row-major; пустой список — валидный ответ.

## 4. Solution keys

Каталог объявляет `SolutionKind.COORDS`; `SheinSolver.solve_http` превращает row-major индексы в центры выбранных плиток по размерам исходной картинки:

```json
{"coordinates": [[100, 100], [250, 250]]}
```

Координаты — пиксели исходного изображения. Проекция покрыта unit-тестом `tests/solvers/classification/test_shein_geometry.py`; на живом Shein не проверялась.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CONFIG` | `requires ctx.models (captcha.adapters.weights.Registry)` | cv-воркер без реестра | поднять с бакетом весов |
| `ERR_CONFIG` | `unknown classification head`, `head … was published for 3x3` | чужой `head`/сетка | убрать `head` из payload |
| `ERR_UNSOLVABLE` | `payload missing image_b64`/`prompt`, `grid shape is not supported` | битый payload | требовать картинку и промпт |
| `ERR_MODEL_INFERENCE` | `unknown prompt`, `tile count does not match the grid` | промпт вне словаря | нормализовать промпт или учить голову |

## 6. Canary / фикстуры

- Голден-картинка: `tests/solvers/classification/fixtures/golden/images/shein-3x3-0006.png` (`grid-v1.json`).
- Тесты: `tests/solvers/classification/test_golden_grid.py`, `test_grid_solver.py`, `test_grid_contract.py`.
- Точность на голдене не измерена (весов нет). Канареечной фикстуры типа нет.

## 7. FILL / live-риски

1. Весов `shein-v1` в бакете нет: на чистом воркере тип отвечает `ERR_CONFIG`.
2. Индексы теперь конвертируются в центры плиток (`tile_centres`), но семантика координат на живом Shein не подтверждена — проверять на L3.
3. Словарь головы узкий (товары); промпт вида «select all bags and shoes» требует совпадения метки, иначе `ERR_MODEL_INFERENCE`.
4. Живых картинок Shein в репозитории нет — только синтетический голден.
5. Pass rate не измерен.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/classification/test_grid_solver.py tests/solvers/classification/test_grid_contract.py tests/solvers/classification/test_golden_grid.py
uv run ruff check src/captcha/solvers/classification
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [shein.py](../src/captcha/solvers/classification/shein.py), [base_grid.py](../src/captcha/solvers/classification/base_grid.py), [grid.py](../src/captcha/solvers/classification/grid.py)
- ML: [docs/ml.md](../docs/ml.md), [docs/ml-tz.md](../docs/ml-tz.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
