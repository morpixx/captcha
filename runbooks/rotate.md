# Runbook: rotate

## 1. Snapshot

- **captcha type:** `CaptchaType.ROTATE` — «выровняй картинку», ответ = угол в градусах.
- **antibot family:** нет (абстрактная геометрия).
- **lane:** `cv`, **mode:** `http`.
- **tier:** 2 — `experimental=False` (`version 0.2.0`); классический путь требует OpenCV,
  ML-путь требует весов; live pass rate не измерен.
- **code:** `solvers/rotate/{rotate,classical,regressor,regressor_spec}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image` (или `image_b64`, `img`, `body`) | да | картинка, base64; data-URI срезается |
| `prefer` | нет | `auto` (дефолт) / `classical` / `ml` |

```json
{"type": "rotate", "image": "<b64>", "prefer": "auto"}
```

## 3. Flow

1. Декод в RGB через PIL; битая картинка — `ERR_UNSOLVABLE`.
2. `prefer=classical` — `classical.estimate_angle(full_rotation=True)` (OpenCV; без него
   `ERR_CONFIG`).
3. `prefer=ml` — строго регрессор `rotate-v1` (`AngleRegressor.from_registry(ctx.models)`): нет
   весов — `ERR_CONFIG`, ошибка инференса — `ERR_MODEL_INFERENCE`.
4. `prefer=auto` — регрессор, при `ModelInferenceError` откат на классику (`path=classical_fallback`);
   без регрессора — сразу классика.
5. Угол нормализуется в `[0, 360)`, уверенность зажимается в `[0, 1]`.

## 4. Solution keys

`SolutionKind.ROTATE`: `angle` (float, градусы). `extra`: `path` (`ml` / `classical` /
`classical_fallback`), `confidence`, `model` (только для `ml`), `fallback_reason`.
В legacy-fорме ответ — просто число градусов (`docs/protocol.md`, `/res.php`).

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет `image`; битый base64/картинка; `prefer` не из набора | payload | проверить поле и значение |
| `ERR_CONFIG` | «opencv required for the classical rotate path» | нет `--extra cv` | ставить extra или `prefer=ml` |
| `ERR_CONFIG` | «rotate ML path requested but no regressor is available» | нет `ctx.models`/весов | `make publish-model` |
| `ERR_MODEL_INFERENCE` | регрессор упал; нефинитный угол | битые веса/вход | проверить `rotate-v1` |

## 6. Canary / фикстуры

- `tests/solvers/rotate/fixtures/golden/`: `rotate-v1.json`, `images/rotate-000{1,2}.png`,
  `README.md`.
- Тесты: `test_rotate_solver.py`, `test_golden_rotate.py`, `test_regressor_onnx.py`,
  `test_regressor_state_dict.py`.
- Замер (2026-11-15, venv без cv2): `solve_http(prefer=classical)` → `ERR_CONFIG`, текст
  «opencv required for the classical rotate path». То есть тип без `--extra cv` не отвечает,
  хотя модуль импортируется.

## 7. FILL / live-риски

1. `prefer=auto` без весов и без cv2 падает `ERR_CONFIG` — это не ошибка клиента, а состояние
   деплоя; «rotate работает из коробки» утверждать нельзя.
2. Порогов/точной метрики классического пути в репозитории нет; ошибка ±N° не измерена.
3. Полные 360° (`full_rotation=True`) означают неоднозначность «угол или угол+180»; как это
   решает конкретный вендор — не проверено.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/rotate
uv run ruff check src/captcha/solvers/rotate
```

## 9. Incident log

- `2026-11-15 — runbook создан — зафиксировано: классический путь без cv2 даёт ERR_CONFIG.`

## 10. References

- код: [rotate.py](../src/captcha/solvers/rotate/rotate.py),
  [classical.py](../src/captcha/solvers/rotate/classical.py),
  [regressor.py](../src/captcha/solvers/rotate/regressor.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
