# captcha-solver-recaptcha

reCAPTCHA v2 audio-bypass. Covers the four v2 shapes:

| Entry point | `CaptchaType` |
| --- | --- |
| `recaptcha_v2` | `RECAPTCHA_V2` |
| `recaptcha_v2_invisible` | `RECAPTCHA_V2_INVISIBLE` |
| `recaptcha_v2_callback` | `RECAPTCHA_V2_CALLBACK` |
| `recaptcha_v2_enterprise` | `RECAPTCHA_V2_ENTERPRISE` |

## How it solves

v2 ships an audio alternative for accessibility. The flow:

1. `GET /recaptcha/api2/anchor` — scrape the hidden `recaptcha-token`.
2. `POST /recaptcha/api2/reload?k=<sitekey>` with `reason=q&c=<token>` — swap
   image challenge for audio.
3. `POST /recaptcha/api2/reload?k=<sitekey>` with `reason=a` — confirm audio.
4. `GET /recaptcha/api2/payload?c=<token>&k=<sitekey>&id=2` — download the
   MP3.
5. Transcribe via an injected audio solver (resolved through the registry
   handed in `ctx.extra['solver_registry']`).
6. `POST /recaptcha/api2/userverify?k=<sitekey>` with the transcript → final
   token.

The audio solver lives in `captcha-solver-audio`; this package references it
through the registry rather than importing it, so a worker that bundles a
different speech backend can swap it in.

## Enterprise

`RecaptchaV2EnterpriseSolver` just repoints the base path to
`/recaptcha/enterprise/*`. The audio alternative still works; Google kept the
accessibility path on both pipelines.

## Why no sensor

The audio-bypass path grades text, not behavior. A sensor is irrelevant —
only the proxy reputation and the transcription quality move the number. The
upstream speech solver choice is the single tuning knob.

## When this breaks

Google rotates the `v` build hash and the response envelope every few months.
Symptoms: anchor returns a page with a different hidden input id, or reload
returns a shape the parser doesn't match. `runbooks/recaptcha_v2.md` holds
the capture procedure (DevTools, search for `/userverify`).
