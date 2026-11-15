# Runbook: slider

## 1. Snapshot

- **captcha type:** `CaptchaType.SLIDER` — фон + пазл, ответ = горизонтальный `offset` и трек.
- **antibot family:** нет (абстрактная геометрия); этот же солвер обслуживает `CAPY`,
  `KEYCAPTCHA`, `LEMIN`, `DATADOME_SLIDER` как делегат.
- **lane:** `cv`, **mode:** `http`.
- **tier:** 2 — `experimental=False` (`version 0.2.0`), но live pass rate не измерен.
- **code:** `solvers/slider/{slider,gap_detect,trajectory}.py`, поведение — `crypto/behavior`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `bg` (или `background`, `bg_image`, `imgUrl`, `image_b64`, `capy_bg`, `fullbg`) | да | фон, base64 |
| `piece` (или `slice`, `puzzle`, `pieceImg`, `capy_piece`, `pz`) | да | пазл, base64 |
| `piece_width` | нет | ширина пазла в пикселях (1..10000) |
| `piece_y`, `start_x` | нет | стартовая позиция пазла (дефолт 0.0) |
| `bg_offset_x` | нет | коррекция: offset относительно трека, а не картинки |
| `min_score` | нет | порог NCC, 0..1 (дефолт 0.15) |
| `backend` | нет | `auto` / `cv2` / `numpy` (`gap_detect.BACKENDS`) |
| `trajectory_points` | нет | 2..512, дефолт 42 |
| `seed` | нет | детерминированный трек для теста |

```json
{"type": "slider", "bg": "<b64>", "piece": "<b64>", "bg_offset_x": 0}
```

## 3. Flow

1. `_extract_images` нормализует алиасы вендоров; нет bg или piece — `ERR_UNSOLVABLE`.
2. `gap_detect.detect_gap`: маска пазла (alpha или порог по углам), edge-map (Canny при cv2,
   Sobel иначе), NCC-поиск по столбцам, игнор области под самим пазлом. `backend=cv2` без
   OpenCV — `ERR_CONFIG`.
3. `final_x = offset - bg_offset_x`; `<= 0` — `ERR_UNSOLVABLE` (это не «слайдер в нуле»).
4. `build_trajectory(final_x, ...)` — безье + humanized-тайминги в `extra.trajectory`.

## 4. Solution keys

`SolutionKind.SLIDER`: `offset` (float). `extra` проходит насквозь: `trajectory`
(`[[x, y, t]…]`), `gap_x`, `match_score`, `piece_bbox`, `bg_shape`, `backend`.

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | нет bg/piece; offset ≤ 0; `min_score`/`trajectory_points`/`seed` вне диапазона | payload или промах детекта | проверить `match_score`, `gap_x` |
| `ERR_CONFIG` | «opencv required for the slider cv2 backend» | `backend=cv2` без `--extra cv` | `backend=auto`/numpy |
| `ERR_UNSOLVABLE` | «no CV solver registered for slider» | cv-воркер без солвера | проверить `/admin/solvers` |

## 6. Canary / фикстуры

- `tests/solvers/slider/fixtures/golden/`: `slider-v1.json`, `images/slider-bg-000{1,2,3}.png`,
  `pieces/piece-0000.png` (+ `README.md`).
- Тесты: `test_gap_detect.py`, `test_golden_slider.py`, `test_slider_solver.py`.
- Замер (2026-11-15, venv без cv2): `slider-bg-0001.png` + `piece-0000.png` → `offset 100`,
  `backend=numpy`, `match_score≈0.81`.
- Кейс (не добавлен; нужны живые bg/piece):

```json
{"name": "slider-live", "clientKey": "<canary-key>",
 "task": {"type": "slider", "bg": "<b64>", "piece": "<b64>"},
 "expect": {"status": "ready", "solution_contains": ["offset"]}}
```

## 7. FILL / live-risks

1. `docs/architecture.md` (и `AGENTS.md`) говорят, что `slider` не грузится без `cv2`. Это
   устарело: `gap_detect` ушёл на numpy-fallback, все 60 записей `solvers/loader.py` импортируются
   без `--extra cv`, и прогон выше это подтверждает. `cv2` остался ускорителем.
2. Точность детекта на живых вендорах не измерена; `min_score=0.15` — эмпирический пол.
3. Трек уходит как `extra`, а не часть канонического ответа: клиент, которому нужен трек для
   сабмита (Capy, CutCaptcha, DataDome), берёт его оттуда.
4. `bg_offset_x` надо знать из вендора: неверное значение сдвигает ответ на константу.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/slider
uv run ruff check src/captcha/solvers/slider
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет; зафиксирован numpy-путь без cv2.`

## 10. References

- код: [slider.py](../src/captcha/solvers/slider/slider.py),
  [gap_detect.py](../src/captcha/solvers/slider/gap_detect.py),
  [trajectory.py](../src/captcha/solvers/slider/trajectory.py)
- вендорские делегаты: [capy.md](capy.md), [keycaptcha.md](keycaptcha.md), [lemin.md](lemin.md),
  [datadome.md](datadome.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
