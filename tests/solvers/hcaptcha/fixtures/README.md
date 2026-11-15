# hCaptcha HTTP fixtures

Shapes are the current hCaptcha wire format (see `solvers/hcaptcha/wire.py` and the
reference client in D:/riot-research). Values are synthetic: sitekeys, JWT
signatures and challenge keys are placeholders, and no live request was made
while producing them. They exist to pin request construction and the response
branches, not to prove a pass rate.

- `checksiteconfig_hsw.json` - encrypted mode: `features.enc_get_req` is set.
- `checksiteconfig_plain.json` - legacy form mode.
- `getcaptcha_pass.json` - JSON pass answer with `generated_pass_UUID` (P1_).
- `getcaptcha_challenge.json` - image challenge answer; HTTP cannot finish it.
- `getcaptcha_error.json` - n-token rejected, `error-codes` populated.
- `getcaptcha_pass.enc` - opaque sealed body, decoded through the (mocked) bundle.
