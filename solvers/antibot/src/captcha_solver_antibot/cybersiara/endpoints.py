"""CyberSiARA endpoints.

CyberSiARA fronts its challenge at ``mfpapi.cybersiara.com``; the sitekey
drives which script variant is served.
"""

from __future__ import annotations

CYBERSIARA_API: str = "https://mfpapi.cybersiara.com"
CYBERSIARA_VERIFY_FMT: str = "https://mfpapi.cybersiara.com/api/v1/verify/{sitekey}"
# FILL: challenge script URL (varies per tenant).
