# Runbook: classification_grid

## 1. Snapshot

- **captcha type:** `CaptchaType.CLASSIFICATION_GRID` — «выбери все тайлы с <объект>», ответ = индексы тайлов.
- **antibot family:** нет; пять голов на одном контракте (`hcaptcha-v1`, `recaptcha-v1`,
  `funcap-v1`, `awswaf-v1`, `shein-v1`).
- **lane:** `cv`, **mode:** `http` (`needs_gpu=True`).
- **tier:** 2 — `experimental=False` для `hcaptcha`/`recaptcha_grid` солверов,
  `experimental=True` для `funcaptcha_grid`/`shein`/`awswaf`.
- **code:** `solvers/classification/{grid,base_grid,head,head_meta,backbone,prompt,_tiles}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image_b64` (или `image`, `body`) | да | вся сетка одной картинкой |
| `prompt` (или `question`, `target`, `textinstructions`) | да | объект или полная инструкция |
| `rows`, `cols` | нет | только вместе; допустимы 2×3, 3×3, 4×4 |
| `head` | нет | override головы (по умолчанию — голова солвера) |
| `threshold` | нет | 0..1, порог сигмоиды на тайл |

```json
{"type": "classification_grid", "image_b64": "<b64>", "prompt": "traffic lights",
 "rows": 3, "cols": 3}
```

## 3. Flow

1. Контракт головы (`grid.HEADS`) + проверка формы: 5×5 и прочее — `ERR_UNSOLVABLE`, а не повод
   «дообучить».
2. `LoRAHead.load(ctx.models, ...)` поверх `ViTBackbone` (`google/vit-base-patch16-224-in21k`,
   лениво, torch).
3. Декод картинки, нарезка на тайлы, энкод, `head.pick_tiles_for_prompt(...)`.
4. Пустой ответ («ни одного тайла») — **валиден**: argmax-фолбэка нет намеренно.
5. Тайл-индексы row-major, 0-based.

## 4. Solution keys

`SolutionKind.GRID`: `objects` = список индексов. `extra`: `head`, `head_version`, `rows`,
`cols`, `threshold`, `backbone`, `device`. Legacy-форма — `click:3/8/9` (с единицы,
`docs/protocol.md`).

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `image_b64`/`prompt`; неизвестная форма; `rows` без `cols`; голова не обслуживает форму | payload | проверить поля и `head` |
| `ERR_CONFIG` | «requires ctx.models»; «unknown classification head»; «head has no labels» | деплой/мета | проверить `captcha-models` и `meta.json` |
| `ERR_MODEL_INFERENCE` | «tile count does not match the grid»; «unreadable weights»; неизвестный prompt | веса/промпт | сверить `head_version`, `labels` |

## 6. Canary / фикстуры

- `tests/solvers/classification/fixtures/golden/grid-v1.json` +
  `images/{hcaptcha-3x3-0001,recaptcha-3x3-0002,recaptcha-4x4-0003,funcap-2x3-0004,awswaf-3x3-0005,shein-3x3-0006}.png`.
- Тесты: `test_grid_contract.py`, `test_grid_solver.py`, `test_head_loader.py`,
  `test_golden_grid.py`, `test_backbone.py` (часть требует torch).
- Кейс (не добавлен; нужна картинка сетки + промпт):

```json
{"name": "grid-3x3", "clientKey": "<canary-key>",
 "task": {"type": "classification_grid", "image_b64": "<b64>", "prompt": "cars", "rows": 3, "cols": 3},
 "expect": {"status": "ready", "solution_contains": ["objects"]}}
```

## 7. FILL / live-risks

1. Весов в бакете нет (`docs/baseline.md`): без `captcha-models` голова не загрузится, тип
   отвечает `ERR_CONFIG`. Голден-файлы — синтетика, а не живые тайлы.
2. Голова `hcaptcha` — **только CV-половина**: типы `hcaptcha`/`hcaptcha_enterprise` обслуживаются
   `solvers/hcaptcha/*`, а grid-классификатор на них претендовать не может
   (`GridClassifier.__init_subclass__` бросает `TypeError`).
3. `SheinSolver` и `AWSWAFSolver` зарегистрированы под своими типами (`shein`, `awswaf`), а
   `FunCaptchaGridSolver` — под `classification_grid` с головой `funcap-v1`. На
   `classification_grid` претендуют три солвера (`hcaptcha`, `recaptcha_grid`,
   `funcaptcha_grid`), поэтому в логах смотреть `info.name`, а не тип.
4. Точность на живых тайлах не измерена; `threshold` по умолчанию 0.5 из контракта головы.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/classification
uv run ruff check src/captcha/solvers/classification
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [grid.py](../src/captcha/solvers/classification/grid.py),
  [base_grid.py](../src/captcha/solvers/classification/base_grid.py),
  [head.py](../src/captcha/solvers/classification/head.py)
- протокол: [docs/protocol.md](../docs/protocol.md) (`objects`, legacy `click:3/8/9`)
- ML: [docs/ml.md](../docs/ml.md)