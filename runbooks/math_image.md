# Runbook: math_image

## 1. Snapshot

- **captcha type:** `CaptchaType.MATH_IMAGE` (`math_image`) — арифметика на картинке: OCR читает выражение, солвер его считает.
- **antibot family:** нет. Две половины: OCR-делегат `image_to_text` и `simpleeval`.
- **lane:** `cv`, **mode:** `http` (OCR — CPU/GPU-инференс, сети нет).
- **tier:** 2 — `experimental=False`; точность OCR на живых картинках не измерена.
- **code:** `solvers/ocr/math_image.py` (нормализация + вычисление), `solvers/ocr/image_to_text.py` (OCR).

## 2. Payload

Payload тот же, что у `image_to_text`: своего поля у типа нет.

| поле | обяз. | смысл |
|---|---|---|
| `body` | да* | base64 картинки; алиасы `image`, `base64`; `data:`-URI допускается |
| `engine` | нет | `auto` (по умолчанию) / `paddle` / `trocr` |
| `lang` | нет | язык OCR; иначе `task.language`, иначе `en` |
| `det_model_dir`/`rec_model_dir`/`cls_model_dir` | нет | локальные каталоги PaddleOCR |
| `model_path`/`model_dir` | нет | локальные веса TrOCR |

`*` — один из трёх алиасов обязателен.

```json
{"type": "math_image", "body": "<base64 png>", "engine": "auto", "lang": "en"}
```

## 3. Endpoints / flow

Сети нет: `MathImageSolver.solve_http` → `ImageToTextSolver.solve_http` → нормализация → `simpleeval`.

1. OCR-делегат возвращает строку (его ошибки уходят как есть, см. [image_to_text.md](image_to_text.md)).
2. `_sanitize`: подстановки типичных ошибок OCR (`×`→`*`, `O`→`0`, `l`→`1`, `S`→`5`, `B`→`8`, `Z`→`2`, `=`/`?`/пробелы — выбросить), затем строгий фильтр `^[\d+\-*/().]+$`, длина ≤ 64, `**` запрещён.
3. `SimpleEval(functions={})` с пустым `names`: только арифметика, никаких вызовов и переменных.
4. Результат: целое печатается без `.0`; `bool`, не-число и `inf`/`nan` — ошибка, а не ответ.

Строгость намеренная: «число из остатков цифр» — это штраф за неверный ответ, а не решение.

## 4. Solution keys

`SolutionKind.TEXT` → `text` (`domain/solution.py`). `extra` несёт `raw_ocr` (что прочитала модель) и `expr` (что посчитали) — по ним разбирается инцидент.

```json
{"text": "42", "raw_ocr": "6*7=", "expr": "6*7"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_MODEL_INFERENCE` | `OCR returned empty string` | модель ничего не прочитала | см. [image_to_text.md](image_to_text.md) §5 |
| `ERR_MODEL_INFERENCE` | `cannot sanitize OCR output to a math expression` | OCR прочитал буквы/мусор | апгрейд модели; в контексте `raw` |
| `ERR_MODEL_INFERENCE` | `eval failed on ...`, `did not evaluate to a number`, `non-finite result` | деление на ноль, скобки, переполнение | битый кроп — задача нерешаема этим путём |
| `ERR_UNSOLVABLE` | `payload missing 'body'`, base64/размеры | битый payload | см. [image_to_text.md](image_to_text.md) §5 |
| `ERR_CONFIG` | `no OCR engine installed` | нет paddleocr / torch+transformers | cv-образ с движком |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/ocr/test_math.py` (нормализация, `**`, не-число, `inf`); OCR-часть — `tests/solvers/ocr/test_ocr_engines.py`.
- Канареечной фикстуры у типа нет; кейс (не добавлен) повторяет `image_to_text` с ожиданием `text`.
- Pass rate не измерен.

## 7. FILL / live-риски

1. Точность связки «OCR → eval» не измерена: голден-набора математических картинок нет.
2. `_VALID_RE` не пропускает `%`, `^`, `!` — выражение с ними станет `ERR_MODEL_INFERENCE`, а не ответом.
3. `x`/`X` конвертируются в `*`, поэтому выражение с переменной `x` посчитается как умножение: ложный ответ, а не отказ.
4. Унарный минус и вложенные скобки поддержаны `simpleeval`; проверено юнитами, не живыми картинками.

## 8. Проверка

```bash
uv run pytest -q tests/solvers/ocr/test_math.py tests/solvers/ocr/test_ocr_engines.py
uv run ruff check src/captcha/solvers/ocr
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [math_image.py](../src/captcha/solvers/ocr/math_image.py), [image_to_text.py](../src/captcha/solvers/ocr/image_to_text.py), [_engines.py](../src/captcha/solvers/ocr/_engines.py)
- ML: [docs/ml.md](../docs/ml.md), [docs/ml-tz.md](../docs/ml-tz.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
