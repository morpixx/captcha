# Runbook: audio_captcha

## 1. Snapshot

- **captcha type:** `CaptchaType.AUDIO` (`audio_captcha`) — аудио-челлендж (обычно «озвученные цифры» reCAPTCHA-подобных форм), ответ — транскрипт.
- **antibot family:** нет. ASR-бэкенды: faster-whisper (приоритет) и openai-whisper.
- **lane:** `cv`, **mode:** `http`.
- **tier:** 2 — `experimental=False`; весов нет, точность не измерена.
- **code:** `solvers/audio/audio_captcha.py`, бэкенды — `solvers/audio/_engines.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `body` | да* | base64 аудио; алиасы `audio`, `base64`; `data:`-URI допускается |
| `format` | нет | расширение файла, по умолчанию `mp3`; только `^[a-z0-9]{1,8}$`, иначе `mp3` |
| `model` | нет | размер Whisper, по умолчанию `small` |
| `device` | нет | `cpu`/`cuda`; иначе `WORKER_CV_DEVICE` |
| `language` | нет | ISO-код; иначе `task.language` |

`*` — один из трёх алиасов обязателен. Лимиты: ≤ 24 000 000 символов base64, ≤ 16 MiB после декода.

```json
{"type": "audio_captcha", "body": "<base64 mp3>", "format": "mp3", "model": "small", "language": "en"}
```

## 3. Endpoints / flow

Сети нет.

1. `_decode_audio`: base64 → байты, проверка размера, санитизация `format`.
2. `_transcribe_file`: байты пишутся в `tempfile.mkstemp(suffix=".<fmt>", prefix="captcha_audio_")`, файл удаляется в `finally` (в том числе при ошибке).
3. `transcribe(path, model_name=…, device=…, language=…)` в `asyncio.to_thread` → `faster_whisper` или `openai-whisper` (`_engines.pick_backend`).
4. Пустой транскрипт → `ERR_MODEL_INFERENCE`; иначе `text.strip()`.

## 4. Solution keys

`SolutionKind.TEXT` → `text`. `extra`: `model`, `device`, `language`.

```json
{"text": "4 7 1 2 9", "model": "small", "device": "cpu"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `payload missing 'body'`, `bad base64`, `audio body too large` | битый/огромный payload | переслать файл как base64 ≤ 16 MiB |
| `ERR_CONFIG` | `no ASR backend`, `weights missing` | нет faster-whisper/openai-whisper или локальных весов | cv-образ с whisper и весами |
| `ERR_MODEL_INFERENCE` | `Whisper returned an empty transcription` | тишина/шум/не тот формат | проверить `format` и сам файл |
| `ERR_MODEL_INFERENCE` | `transcription crashed: …` | падение бэкенда | `error_type` в контексте |
| `ERR_MODEL_INFERENCE` | `cannot stage the audio file` | нет места/прав на temp | почистить диск воркера |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/audio/test_audio_captcha.py` (payload, лимиты, пустой транскрипт, очистка temp).
- Канареечной фикстуры нет: аудио в репозиторий не кладём (лицензии на голос).
- Pass rate не измерен.

## 7. FILL / live-риски

1. Весов Whisper в бакете нет: на чистом воркере тип отвечает `ERR_CONFIG`.
2. Точность на реальных челленджах не измерена; `small` выбран как дефолт без замеров latency/качества.
3. Только base64 в payload. Клиент, который присылает `audio_url`, не обслужен (сервер ничего не скачивает намеренно).
4. Цифровой транскрипт отдаётся как есть (`"4 7 1 2 9"`); нормализации «только цифры без пробелов» нет — клиент должен сам решить, что делать с разделителями.
5. `get_settings()` вызывается при отсутствии `ctx.config` — второе документированное исключение из правила «конфиг только в services».

## 8. Проверка

```bash
uv run pytest -q tests/solvers/audio/test_audio_captcha.py
uv run ruff check src/captcha/solvers/audio
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [audio_captcha.py](../src/captcha/solvers/audio/audio_captcha.py), [_engines.py](../src/captcha/solvers/audio/_engines.py)
- ML: [docs/ml.md](../docs/ml.md)
- конфиг: [config.py](../src/captcha/config.py) (`WORKER_CV_DEVICE`)
- протокол: [docs/protocol.md](../docs/protocol.md)
