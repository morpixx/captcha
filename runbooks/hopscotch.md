# Runbook: hopscotch

## 1. Snapshot

- **captcha type:** `CaptchaType.HOPSCOTCH` — **кастомный тип, вне 62 вендорских** (контракт §1): одна волна карточек 200×200 + панель-подсказка, ответ = номер карточки (1-based) и точка «стопы» фигуры.
- **antibot family:** нет; это ML-тип с собственным артефактом (`hopscotch-v1`).
- **lane:** `cv`, **mode:** `http` (`needs_gpu=True`).
- **tier:** 3 — `experimental=True` до L3 canary (цель 95% при покрытии 80%, контракт §1).
- **code:** `solvers/hopscotch/{solver,payload,artifact,calibration,backend,v2runtime}.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `image_b64` (или `image`) | да | вся полоса: 1000×400 (5 карточек) или 1600×400 (8); data-URI допускается; ≤ 32 MiB после декода |
| `frames` | нет | 5 или 8; должен совпадать с шириной/геометрией, иначе `ERR_UNSOLVABLE` |
| `hint` | нет | текст подсказки, ≤64 символа; **адвизорный** — панель уже в картинке |

```json
{"type": "hopscotch", "image_b64": "<png-base64>", "frames": 5, "hint": "зелёный"}
```

## 3. Flow

1. `prepare()` читает артефакт (`load_artifact`): `model.safetensors` + `config.json` +
   `release.json`; каталог — `ctx.extra["hopscotch_artifact_dir"]` → `config.hopscotch_artifact_dir`
   → env `HOPSCOTCH_ARTIFACT_DIR`.
2. `parse_payload`: высота = `geometry.image_height`, ширина кратна `card_width`, число карточек
   из набора `geometry.frames`.
3. Backend: `ctx.extra["hopscotch_backend"]` → `V2Backend` (импорт чекаута `D:/hopscotch_v2`,
   env `HOPSCOTCH_V2_ROOT`, torch/safetensors).
4. `calibration.decide` поверх 8 фиксированных фич (`artifact.FEATURES`) и `release.json`:
   нет готового релиза, низкий скор или проваленные guard'ы = **abstention** → `ERR_UNSOLVABLE`
   (терминально, не ретрай).
5. `foot_to_pixels`: нормированная «стопа» внутри карточки → глобальные пиксели; нет стопы —
   центр карточки.

## 4. Solution keys

`SolutionKind.HOPSCOTCH`: `index` (1-based, `config.index_base`) и `coordinates: [x, y]` в
пикселях входной картинки. `extra` проходит насквозь: `card_index` (0-based), `frames`,
`confidence`, `model`, `version`, `release`, `hint`. Два основания индекса — намеренно: клиент,
взявший 0-based, теряет раунд.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_CONFIG` | «artifact directory is not configured» | нет env/ctx-пути | задать `HOPSCOTCH_ARTIFACT_DIR` |
| `ERR_CONFIG` | «inference runtime is not configured» / «checkout not found» | нет `HOPSCOTCH_V2_ROOT` | смонтировать чекаут |
| `ERR_CONFIG` | «serving needs torch + safetensors» | образ без `--extra cv` | ставить extra на cv-воркер |
| `ERR_UNSOLVABLE` | «payload missing image_b64»; «unsupported card count»; «frames disagrees with the image» | битый payload | проверить картинку и `frames` |
| `ERR_UNSOLVABLE` | «hopscotch abstained» | скор ниже порога / нет release | это политика, не баг; см. §7 |
| `ERR_MODEL_INFERENCE` | «failed to read model.safetensors» | битые веса | перепубликовать артефакт |

## 6. Canary / фикстуры

- `tests/solvers/hopscotch/`: `conftest.py`, `test_solver.py`, `test_artifact.py`, `test_kit.py`.
- Голден-набора в репозитории нет; артефакт публикуется отдельно (`make publish-model`,
  `docs/ml-hopscotch.md`).
- Кейс (не добавлен; нужен `hopscotch`-артефакт в бакете и картинка-волна):

```json
{"name": "hopscotch-wave", "clientKey": "<canary-key>",
 "task": {"type": "hopscotch", "image_b64": "<png-base64>", "frames": 8},
 "expect": {"status": "ready", "solution_contains": ["index", "coordinates"]}}
```

## 7. FILL / live-риски

1. `docs/baseline.md`: последний прогон — 282/400 (70.5%) клиентской валидации, 27/30 GoldVal;
   цель 95%/80%. На таких числах abstention будет резать покрытие заметно — это ожидаемо.
2. Абстенция маппится в `ERR_UNSOLVABLE` (терминально). Если бизнес захочет `ERR_QUOTA`,
   это решение lead, а не солвера.
3. Веса в бакете отсутствуют (baseline: «ML inference code exists, no weights»): без
   `HOPSCOTCH_ARTIFACT_DIR` тип в проде не отвечает.
4. Порог и веса живут в `release.json`; реордер `FEATURES` ломает калибровку молча — порядок
   заморожен и это записано в `artifact.py`.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/hopscotch
uv run ruff check src/captcha/solvers/hopscotch
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет, тип не продаётся.`

## 10. References

- код: [solver.py](../src/captcha/solvers/hopscotch/solver.py),
  [payload.py](../src/captcha/solvers/hopscotch/payload.py),
  [artifact.py](../src/captcha/solvers/hopscotch/artifact.py),
  [calibration.py](../src/captcha/solvers/hopscotch/calibration.py)
- ML-док: [docs/ml-hopscotch.md](../docs/ml-hopscotch.md)
- контракт: [docs/production-contract.md](../docs/production-contract.md) §1, §9
