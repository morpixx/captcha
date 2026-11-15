"""BotDeflector endpoint names and the cookie reality.

The challenge API lives on a per-tenant host: botdeflector.eu for standalone
deployments, ipv4.challenge.queue-it.net or dual.challenge.queue-it.net for a
Queue-it softblock. The page names its host in botDeflectorSource, so the
payload's domain field (or a Queue-it site_url) picks it; paths are fixed:

    /pow/get  /pow/verify  /verify-flow        (and /icon/get /icon/verify)

BotDeflector sets no cookie of its own (2026-10 capture): the flow runs through
headers and query parameters only. Queue-it adds Queue-it-visitorsession on the
softblock page, but that is the host's cookie, not the engine's, and it is the
caller's to carry.

FILL: the /flow-token/ path a standalone widget uses to mint its JWT. The token
is public in the page once minted, so the solver takes it from the task instead.
"""

from __future__ import annotations

#: Key the Queue-it softblock page prints the flow JWT under.
FLOW_TOKEN_FIELD: str = "botDeflectorJwtToken"

#: Key the standalone widget config carries the challenge host under.
SOURCE_FIELD: str = "botDeflectorSource"
