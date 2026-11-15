"""KeyCAPTCHA endpoints.

The host page loads `<base>/swfs/cap.js` plus the four `s_s_c_*` variables; after
the puzzle is solved the widget posts its answer back to the same host and gets
the `cap_key` the form submits.
"""

from __future__ import annotations

# FILL: backend origin. Devtools -> Network, filter `keycaptcha`: copy the origin
# of the `cap.js` load and of the POST that follows a manual solve. The CDN host
# and the check host can differ per tenant.
BASE = "https://backs.keycaptcha.com"

# FILL: check path. Network -> the POST made right after the drag/click: copy
# the full URL (path included) and its form field names. `SWF` is only here as
# the pointer to the widget bundle the constants above were read from.
CHECK = f"{BASE}/captcha/check"
SWF = f"{BASE}/swfs/cap.js"
