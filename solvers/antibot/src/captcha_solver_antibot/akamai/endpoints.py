"""Akamai Bot Manager endpoints.

The sensor_data POST path is rewritten per-tenant: Akamai serves a per-
customer script that posts to a randomized path on the target origin
(e.g. ``/_sec/akam/11/abc123``). The real URL must be scraped from the
challenge script and passed through on the task payload.

Two variants:
    - classic `sensor_data` form POST (`AKAMAI_SENSOR`)
    - SBSD / Bot Manager Premier JSON endpoint (`AKAMAI_SBSD`)
"""

from __future__ import annotations

# Payload keys the classic form expects.
SENSOR_FORM_KEY: str = "sensor_data"

# SBSD endpoint is normally relative to the target: /sec/cp_challenge/sbsd/...
# FILL: dump the exact SBSD path per site; it rotates with Akamai releases.
SBSD_PATH_HINT: str = "/sec/cp_challenge/sbsd"

# Cookies worth parsing out of the final response.
AKAMAI_COOKIES_OF_INTEREST: tuple[str, ...] = ("_abck", "bm_sz", "bm_sv", "ak_bmsc")
