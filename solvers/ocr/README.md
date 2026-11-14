# captcha-solver-ocr

Tier-1 OCR solvers.

| name            | type            | engine                                    |
|-----------------|-----------------|-------------------------------------------|
| image_to_text   | IMAGE_TO_TEXT   | PaddleOCR (default) or TrOCR              |
| math_image      | MATH_IMAGE      | image_to_text + simpleeval                |
| text_captcha    | TEXT            | OpenAI-compatible chat endpoint (Ollama)  |

## Install

```
# baseline
uv pip install -e solvers/ocr

# engines (pick one or both)
uv pip install -e "solvers/ocr[paddle]"
uv pip install -e "solvers/ocr[trocr]"
```

## Payloads

### image_to_text
```json
{ "body": "<base64-image>", "engine": "paddle" | "trocr", "case": true, "lang": "en" }
```

### math_image
```json
{ "body": "<base64-image>", "engine": "paddle" }
```

### text_captcha
```json
{ "comment": "If tomorrow is Saturday, what day is today?" }
```
Routes through `Settings.llm_endpoint` with model `Settings.llm_model`. Works against any
OpenAI-compatible `/v1/chat/completions` server (Ollama, vLLM, llama.cpp server, OpenAI).

## Config

Reads `captcha_common.get_settings()`:
- `LLM_ENDPOINT` — base URL, e.g. `http://localhost:11434/v1` or `http://localhost:11434` (we add `/v1`)
- `LLM_MODEL` — `llama3.1:8b`, `gpt-4o-mini`, etc.
- `LLM_API_KEY` — optional bearer.
