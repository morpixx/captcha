"""Akamai _abck cookie: the only readable verdict in the flow.

The value is mostly opaque signed material. What is readable is the tail of
tilde-delimited numeric fields, and that tail is what the solver grades on
(docs/reverse-2026.md has no Akamai section; source is the 2026-06 write-up that
traces the handshake):

    ~-1~            challenge outstanding, no sensor data accepted yet
    ~0~-1~          sensor data received, not judged sufficient
    ~0~             satisfied -- the client may stop posting sensors

These markers are read from observed traffic, not from vendor documentation, so
they are a strong hint rather than a contract. Consequences for the solver:

- only SATISFIED counts as a solve; a 200 with an intermediate cookie is a
  failed task, retried/DLQed as ChallengeExpiredError, never billed as success;
- UNKNOWN is not satisfied either -- a tenant that never exposes the markers
  needs the second layer (sec-cpt) or a different flow, and the solver says so
  instead of papering over it.

sec_cpt has its own marker: the challenge counts as cleared once the cookie
value contains the literal ~3~.
"""

from __future__ import annotations

from dataclasses import dataclass

UNSATISFIED = "unsatisfied"
INTERMEDIATE = "intermediate"
SATISFIED = "satisfied"
UNKNOWN = "unknown"

#: Marker the sec-cpt challenge leaves in its own cookie once it clears.
SEC_CPT_CLEARED = "~3~"

#: Literal tails, most specific first. See the module docstring.
_INTERMEDIATE = "~0~-1~"
_UNSATISFIED = "~-1~"
_SATISFIED = "~0~"


@dataclass(frozen=True, slots=True)
class AbckState:
    """Parsed view of one _abck value. Never a token by itself."""

    value: str
    state: str
    markers: tuple[str, ...] = ()

    @property
    def satisfied(self) -> bool:
        return self.state == SATISFIED

    def summary(self) -> dict[str, object]:
        """Error-context safe: the value itself is a token and stays out."""
        return {"state": self.state, "markers": list(self.markers), "length": len(self.value)}


def read(value: object) -> AbckState:
    """Classify an _abck value by the markers its tail carries.

    The checks are ordered the way the observed states nest: an intermediate
    cookie also contains ~-1~, and a satisfied one contains ~0~. Missing or
    marker-less values are UNKNOWN -- callers must treat that as "not solved".
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return AbckState(value="", state=UNKNOWN)
    if _INTERMEDIATE in text:
        return AbckState(value=text, state=INTERMEDIATE, markers=(_INTERMEDIATE,))
    if _UNSATISFIED in text:
        return AbckState(value=text, state=UNSATISFIED, markers=(_UNSATISFIED,))
    if _SATISFIED in text:
        return AbckState(value=text, state=SATISFIED, markers=(_SATISFIED,))
    return AbckState(value=text, state=UNKNOWN)


def sec_cpt_cleared(value: object) -> bool:
    """True when the sec-cpt cookie reports the challenge as cleared (~3~)."""
    return isinstance(value, str) and SEC_CPT_CLEARED in value
