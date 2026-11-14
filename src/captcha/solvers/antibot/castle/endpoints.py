"""Castle.io endpoints.

Castle's client SDK POSTs a device-signals blob to `d.castle.io/v1/c/mon`;
the public-key-scoped `_cid` token comes back in the response.
"""

from __future__ import annotations

CASTLE_COLLECT_URL: str = "https://d.castle.io/v1/c/mon"
CASTLE_PK_HEADER: str = "x-castle-client-id"
# FILL: additional endpoints when using the risk-scoring API directly.
