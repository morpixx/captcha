# Runbook: image_to_text

## 1. Snapshot

- **captcha type:** `CaptchaType.IMAGE_TO_TEXT` (`image_to_text`) — OCR картинки в строку; базовый тип для `text_captcha`-подобных и делегат для `math_image`/`vk`/`mtcaptcha`.
- **antibot family:** нет. Движки OCR: PaddleOCR (по умолчанию) и TrOCR, оба ленивые.
- **lane:** `cv`, **mode:** `http`.
- **tier:** 2 — `experimental=False`; весов в бакете нет, точность не измерена.
- **code:** `solvers/ocr/image_to_text.py`, движки — `solvers/ocr/_engines.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `body` | да* | base64 картинки; алиасы `image`, `base64`; `data:`-URI допускается; ≤ 40 000 000 символов base64 |
| `engine` | нет | `auto` / `paddle` / `trocr` |
| `lang` | нет | язык PaddleOCR; иначе `task.language`, иначе `en` |
| `case` | нет | `true` — не приводить к нижнему регистру (по умолчанию приводится) |
| `det_model_dir`/`rec_model_dir`/`cls_model_dir` | нет | локальные каталоги PaddleOCR |
| `model_path`/`model_dir` | нет | локальные веса TrOCR |

`*` — один из трёх алиасов обязателен.

```json
{"type": "image_to_text", "body": "<base64 png>", "engine": "paddle", "lang": "en", "case": false}
```

Официальные имена задачи в `type_map`: `ImageToTextTask`, `ImageToTextTaskMugshot` — оба ведут на этот тип.

## 3. Endpoints / flow

Сети нет — картинка приходит в payload.

1. `_decode_image`: base64 (обычный, при отказе urlsafe), `data:`-префикс срезается, пробелы выкидываются; `PIL.Image.open`.
2. Лимиты: `width*height ≤ 20 000 000`, сторона ≤ 12 000 px; `img.load()` под try — битый файл это `ERR_UNSOLVABLE`.
3. `_requested_engine`: `auto` берёт первый доступный из `available_engines()`; список пуст → `ERR_CONFIG`.
4. `paddle_read(image, lang, dirs=…)` или `trocr_read(image, device, model_path=…)` — в `asyncio.to_thread` (модели не thread-safe, в `_engines` свои локи).
5. Пустой текст → `ERR_MODEL_INFERENCE`; иначе `text.strip()` и, если `case` не задан, `.lower()`.

## 4. Solution keys

`SolutionKind.TEXT` → `text` (`domain/solution.py`). `extra`: `engine`, `lang`.

```json
{"text": "k7f3q", "engine": "paddle", "lang": "en"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `payload missing 'body'`, `bad base64`, `unreadable image` | битый payload | переслать картинку как base64 |
| `ERR_UNSOLVABLE` | `image dimensions out of range`, `image too large` | скрин 4K вместо кропа | обрезать до капчи |
| `ERR_CONFIG` | `unknown engine`, `no OCR engine installed` | нет paddleocr/torch | cv-образ с движком |
| `ERR_MODEL_INFERENCE` | `OCR returned no text` | пустая/шумная картинка | проверить кроп; ретрай не поможет |
| `ERR_MODEL_INFERENCE` | `<engine> crashed: …` | баг движка/весов | `error_type` в контексте |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/ocr/test_image_to_text.py` (payload, кейс, лимиты), `tests/solvers/ocr/test_ocr_engines.py` (движки).
- Голден-набора картинок для OCR в репозитории нет.
- `type_map`-алиасы пинятся в `tests/gateway/test_type_map.py`.

## 7. FILL / live-риски

1. Весов в бакете нет (`docs/ml.md`): на чистом воркере солвер отвечает `ERR_CONFIG`, а не результатом.
2. Точность на живых капчах не измерена — ни по семействам шрифтов, ни по искажениям.
3. `case=false` по умолчанию приводит к нижнему регистру; капча, чувствительная к регистру, без `"case": true` вернёт неверный ответ.
4. TrOCR по умолчанию `microsoft/trocr-base-printed` — печатный текст; рукописный/искажённый не покрыт.
5. `Mugshot`-алиас ведёт на тот же солвер: специализированной головы для mugshot нет.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/ocr/test_image_to_text.py tests/solvers/ocr/test_ocr_engines.py
uv run ruff check src/captcha/solvers/ocr
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [image_to_text.py](../src/captcha/solvers/ocr/image_to_text.py), [_engines.py](../src/captcha/solvers/ocr/_engines.py)
- ML: [docs/ml.md](../docs/ml.md)
- имена задач: [type_map.py](../src/captcha/services/gateway/type_map.py)
- протокол: [docs/protocol.md](../docs/protocol.md)
