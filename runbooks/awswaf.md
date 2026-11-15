# Runbook: awswaf

## 1. Snapshot

- **captcha type:** `CaptchaType.AWSWAF` (`awswaf`) — AWS WAF: image-челлендж «выберите все плитки с …» на сетке 3×3 плюс обмен voucher/token.
- **antibot family:** нет отдельного `AntibotEngine`. Это grid-голова `awswaf-v1` в `solvers/classification/`.
- **lane:** `cv`, **mode:** `http`, `needs_gpu=True`.
- **tier:** 3 — `experimental=True`; обмен voucher/token не реализован, весов нет.
- **code:** `solvers/classification/awswaf.py` (тонкий подкласс), общий путь — `classification/base_grid.py`, контракт головы — `classification/grid.py`.

## 2. Payload

Контракт `base_grid.py` — общий для всех grid-солверов:

| поле | обяз. | смысл |
|---|---|---|
| `image_b64` | да | base64 всей сетки; алиасы `image`, `body` |
| `prompt` | да | что искать; алиасы `question`, `target`, `textinstructions` |
| `rows` / `cols` | нет | форма сетки; только вместе; для `awswaf-v1` допустима лишь 3×3 |
| `head` | нет | подмена головы; должна быть в `grid.HEADS` |
| `threshold` | нет | порог сигмоиды 0..1 на вызов |

```json
{"type": "awswaf", "image_b64": "<base64 png 3x3>", "prompt": "traffic lights"}
```

## 3. Endpoints / flow

Сети нет: солвер только оценивает плитки, когда сетка и промпт уже есть.

1. `resolve_grid`: `rows`/`cols` (только парой) либо дефолт головы 3×3; 5×5 и прочее — `ERR_UNSOLVABLE`.
2. `LoRAHead.load(ctx.models, name="awswaf-v1", backbone=ViT-B/16)`; нет `ctx.models` → `ERR_CONFIG`.
3. `decode_base64_image` → `slice_grid` → `tiles_to_batch` → `ViTBackbone.encode`.
4. `head.pick_tiles_for_prompt(features, prompt, threshold, shape)`: метка промпта ищется по `labels`/`aliases` головы; неизвестный промпт — `ERR_MODEL_INFERENCE`.
5. Ответ — индексы плиток row-major; пустой список валиден («ни одной»), argmax-фолбэка нет.

**Обмен voucher/token (не реализован):** собственно AWS-ответ — `captcha_voucher` + `existing_token` либо cookie `aws-waf-token`; этот шаг в солвере отсутствует.

## 4. Solution keys

Каталог объявляет `SolutionKind.AWSWAF`: `captcha_voucher`+`existing_token` либо `cookies: {"aws-waf-token": …}` (`domain/solution.py`). Фактически grid-солвер возвращает только `indices`, поэтому проекция уходит в generic-фолбэк и отдаёт `indices`:

```json
{"indices": [0, 4, 8]}
```

Это расхождение формы: клиент, который ждёт voucher, его не получит, пока обмен не реализован.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CONFIG` | `requires ctx.models`, `unknown classification head` | воркер без реестра весов / опечатка в `head` | cv-воркер с бакетом, поправить `head` |
| `ERR_MODEL_INFERENCE` | `unknown prompt`, `tile count does not match the grid` | промпт вне словаря головы / сетка не та | `head` с нужным словарём, верная сетка |
| `ERR_UNSOLVABLE` | `payload missing image_b64`/`prompt`, `grid shape is not supported`, `rows/cols must be sent together` | битый payload | требовать картинку, промпт и обе оси |
| `ERR_MODEL_INFERENCE` | `unreadable weights`, `dimension mismatch` | битые/чужие веса в бакете | `make check-model` |

## 6. Canary / фикстуры

- Голден-картинка: `tests/solvers/classification/fixtures/golden/images/awswaf-3x3-0005.png` (`grid-v1.json` — манифест).
- Тесты: `tests/solvers/classification/test_golden_grid.py` (сверка индексов, без пиксельного эпсилона), `test_grid_solver.py` (индексы, пустой ответ, ошибки), `test_grid_contract.py` (форма головы).
- Точность на голден-наборе **не измерена**: весов в бакете нет (`tests/solvers/classification/test_backbone.py` скипается без torch).
- Канареечной фикстуры типа нет.

## 7. FILL / live-риски

1. Обмен voucher/token отсутствует: тип отдаёт `indices`, а не AWS-ответ. Продавать как «AWS WAF решён» нельзя.
2. Весов `awswaf-v1` в бакете нет — на чистом воркере `ERR_CONFIG`, не результат.
3. `prompt` должен попадать в словарь головы; AWS формулирует инструкции свободным текстом — покрытие промптов не измерено.
4. Только 3×3: 4×4/2×3 у этой головы запрещены контрактом.
5. Pass rate и latency на живом WAF не измерены.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/classification/test_grid_solver.py tests/solvers/classification/test_grid_contract.py tests/solvers/classification/test_golden_grid.py
uv run ruff check src/captcha/solvers/classification
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [awswaf.py](../src/captcha/solvers/classification/awswaf.py), [base_grid.py](../src/captcha/solvers/classification/base_grid.py), [grid.py](../src/captcha/solvers/classification/grid.py), [head.py](../src/captcha/solvers/classification/head.py)
- веса и мета: [docs/ml.md](../docs/ml.md)
- прогрев/загрузка голов: [classification_grid.md](classification_grid.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
