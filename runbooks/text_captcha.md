# Runbook: text_captcha

## 1. Snapshot

- **captcha type:** `CaptchaType.TEXT` (`text_captcha`) — текстовый вопрос («сколько будет…», «какой город…»), ответ — одна строка от LLM.
- **antibot family:** нет. Внешний OpenAI-совместимый `/v1/chat/completions` (Ollama, vLLM, llama.cpp server, OpenAI).
- **lane:** `cv` (по каталогу), **mode:** `http`.
- **tier:** 2 — `experimental=False`; качество модели не измерено.
- **code:** `solvers/ocr/text_captcha.py`, политика эндпоинта — `solvers/ocr/_llm.py`.

## 2. Payload

| поле | обяз. | смысл |
|---|---|---|
| `comment` | да* | текст вопроса; алиасы `question`, `text` |
| `endpoint` | нет | переопределение LLM-эндпоинта; требует настроенного `LLM_ENDPOINT` и того же origin |
| `model` | нет | имя модели; иначе `LLM_MODEL` |
| `api_key` | нет | **игнорируется** (пишется warning); ключ берётся только из `LLM_API_KEY` |

`*` — один из трёх ключей обязателен; вопрос длиннее 8000 символов отвергается.

```json
{"type": "text_captcha", "comment": "Which planet is known as the Red Planet?"}
```

## 3. Endpoints / flow

Эндпоинт собирается из `LLM_ENDPOINT` (`normalize_endpoint` дописывает `/v1/chat/completions`).

1. `resolve_endpoint`: проверка абсолютного http(s), запрет credentials в URL, metadata-хостов (`169.254.169.254`, `metadata.google.internal`, `100.100.100.200`), link-local/multicast; per-task `endpoint` разрешён только при совпадении origin с `LLM_ENDPOINT` (иначе SSRF-сток и утечка ключа).
2. `resolve_model`: `payload["model"]` либо `LLM_MODEL`, длина ≤ 128.
3. Тело: `{model, messages:[{system: SYS_PROMPT}, {user: question}], temperature: 0, max_tokens: 32, stream: false}`; `authorization: Bearer <LLM_API_KEY>`, если ключ задан.
4. Клиент: `ctx.http`, если у него есть `post`; иначе короткоживущий `httpx.AsyncClient` — `cv`-воркер не даёт общего HTTP-клиента.
5. `chat()`: до 2 попыток на 5xx/транспорт, пока жив бюджет; таймаут 30 с или остаток дедлайна.
6. `strip_answer`: первая строка, срезается префикс «answer is», кавычки и хвостовая пунктуация.

## 4. Solution keys

`SolutionKind.TEXT` → `text` (`domain/solution.py`). `extra.model` показывает, какая модель отвечала.

```json
{"text": "mars", "model": "llama3.1:8b"}
```

## 5. Ошибки

| код | симптом | причина | что делать |
|---|---|---|---|
| `ERR_UNSOLVABLE` | `payload missing 'comment'`, `question is too long` | клиент не прислал вопрос | требовать `comment`/носить текст вопроса |
| `ERR_CONFIG` | `LLM endpoint not configured (LLM_ENDPOINT)`, `override must match LLM_ENDPOINT` | не настроен`LLM_ENDPOINT`/чужая модель | выставить env, не переопределять origin |
| `ERR_CONFIG` | `rejected credentials (HTTP 401/403/407)`, `not found (HTTP 404)` | ключ/путь | `LLM_API_KEY`, `LLM_ENDPOINT` |
| `ERR_MODEL_INFERENCE` | `response is not JSON`, `no choices[0]`, `no message content` | не тот сервер/прокси | сверить, что эндпоинт OpenAI-совместим |
| `ERR_MODEL_INFERENCE` | `rate-limited (HTTP 429)`, `HTTP <code>` | лимит провайдера | бэкофф на стороне клиента повтора |
| `ERR_MODEL_INFERENCE` | `LLM returned an empty answer` | модель вернула пусто | сменить модель/промпт |
| `ERR_TIMEOUT` | `endpoint timed out`, `exceeded the task deadline` | модель медленнее бюджета | поднять дедлайн задачи |

## 6. Canary / фикстуры

- Юнит: `tests/solvers/ocr/test_text_captcha.py` (эндпоинт, ответ, ошибки) и `tests/solvers/ocr/test_llm_endpoint_policy.py` (SSRF-политика эндпоинта).
- Канареечной фикстуры у типа нет.
- Кейс (не добавлен):

```json
{"name": "text_captcha-live", "clientKey": "<canary-key>",
 "task": {"type": "text_captcha", "comment": "<вопрос со страницы>"},
 "expect": {"status": "ready", "solution_contains": ["text"]}}
```

## 7. FILL / live-риски

1. Точность модели на живых вопросах не измерена: ни голден-набора, ни pass rate нет.
2. Один вопрос — один запрос, без self-consistency и без проверки формата ответа по типу вопроса.
3. `api_key` в payload не поддерживается (warning в лог): клиент с собственным ключом получит отказ по `LLM_API_KEY` воркера.
4. Точка с запятой и хвостовая пунктуация срезаются, но ответ из двух слов («New York») вернётся как есть — валидации «одно слово» нет.
5. `get_settings()` вызывается здесь при отсутствии `ctx.config` — это одно из двух документированных исключений из правила «конфиг только в services».

## 8. Проверка

```bash
uv run pytest -q tests/solvers/ocr/test_text_captcha.py tests/solvers/ocr/test_llm_endpoint_policy.py
uv run ruff check src/captcha/solvers/ocr
```

## 9. Incident log

- `2026-11-15 — runbook создан — live-инцидентов нет.`

## 10. References

- код: [text_captcha.py](../src/captcha/solvers/ocr/text_captcha.py), [_llm.py](../src/captcha/solvers/ocr/_llm.py)
- конфиг: [config.py](../src/captcha/config.py) (`LLM_ENDPOINT`, `LLM_MODEL`, `LLM_API_KEY`)
- модель/веса: [docs/ml.md](../docs/ml.md)
- протокол: [docs/protocol.md](../docs/protocol.md)
