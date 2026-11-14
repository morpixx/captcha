# captcha-solver-audio

Tier-1 audio-captcha solver. Transcribes with Whisper.

| name           | type   | engine                                       |
|----------------|--------|----------------------------------------------|
| audio_captcha  | AUDIO  | faster-whisper (preferred) / openai-whisper  |

## Install

```
# pick one engine — faster-whisper is 4x faster at same accuracy
uv pip install -e "solvers/audio[faster-whisper]"
uv pip install -e "solvers/audio[whisper]"
```

## Payload

```json
{
  "body":  "<base64-audio>",
  "format": "mp3",          // optional; used only as temp-file suffix hint
  "model":  "small",        // optional; overrides config
  "device": "cpu"           // optional; overrides config
}
```

`task.language` is passed to Whisper as the `language=` hint; omit to auto-detect.

## Config

Reads `captcha_common.get_settings()`:
- `WORKER_CV_DEVICE` — `cpu` or `cuda`.

Model name is read from `payload.model` (default `small`). Use `tiny`, `base`, `small`,
`medium`, or `large-v3`. For captcha audio (short, clean speech) `small` or `tiny` is plenty.
