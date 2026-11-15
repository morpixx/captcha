# reCAPTCHA fixtures

Synthetic, hand-built captures of the response shapes the solvers parse. They
are intentionally tiny and contain fake tokens. No live Google request, Node
runtime, or network is used by the tests.

- `anchor.html` / `anchor_no_token.html` — anchor page with/without the hidden
  `recaptcha-token` input.
- `api.js` — bootstrap stub carrying a fake `recaptcha/releases/TESTVERSION`.
- `reload_swap.json`, `reload_audio*.json` — XSSI-guarded `rresp` reload
  bodies; the audio ones carry a payload URL, the `_no_url` variant does not.
- `userverify.json`, `userverify_rejected.json`, `reload_no_token.json` —
  accepted and rejected final steps.
- `v3_reload*.json`, `v3_reload_malformed.txt` — v3 token shapes: plain,
  nested, empty and malformed-but-greppable.
- `audio.bin` — fake MP3 bytes; a real MP3 only needs to reach the injected
  transcriber, whose contract is covered by tests.
