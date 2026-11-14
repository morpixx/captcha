"""atbCaptcha (aisecurius / 顶象 / aliyun-origin split) endpoints.

Sold as "AiSecurius" / "atb" slider + 3-point click; SDK is hosted at
`cap.aisecurius.com` with a tenant-id prefix path. JS envelope is a BDA-like
AES-GCM-of-mouse-trajectory blob.
"""

from __future__ import annotations

API_HOST = "cap.aisecurius.com"
GET_CONF = "https://cap.aisecurius.com/cap-api/v1/get"
VERIFY = "https://cap.aisecurius.com/cap-api/v1/check"
