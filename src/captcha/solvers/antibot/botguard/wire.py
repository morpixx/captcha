"""BotGuard wire shapes: the program blob and the VM's response token.

Two products wrap the same VM differently. YouTube/WAA hands the client a
JSON challenge with program/globalName/interpreterUrl; reCAPTCHA inlines a
bare program string. The parser accepts both instead of making the solver
branch on the product.

The token itself is opaque: printable, long, and only meaningful to Google.
Refusing an empty or truncated string is the only validation that does not
invent a format.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from captcha.domain.errors import ChallengeExpiredError, SensorError

#: Shortest plausible VM response. Real tokens are ~1 KB; anything under this
#: is a truncated write or a "false" placeholder, not a token.
MIN_TOKEN_LEN = 32


@dataclass(frozen=True, slots=True)
class Program:
    """The bytecode program plus the handles needed to run it."""

    program: str
    global_name: str = ""
    interpreter_url: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.program.strip()


def parse_program(raw: str, *, global_name: str = "", interpreter_url: str = "") -> Program:
    """Accept a bare program or a JSON challenge wrapper.

    A JSON blob without a usable "program" is treated as a malformed challenge,
    not as bytecode: sending JSON to the VM only wastes a runtime.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ChallengeExpiredError(
            "botguard program is empty; the challenge rotated",
            context={"type": type(raw).__name__},
        )
    blob = raw.strip()
    if blob.startswith("{"):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            program = data.get("program") or data.get("ftx")
            if not isinstance(program, str) or not program.strip():
                raise ChallengeExpiredError(
                    "botguard JSON challenge carries no program",
                    context={"keys": sorted(data)[:12]},
                )
            return Program(
                program=program.strip(),
                global_name=str(data.get("globalName") or data.get("global_name") or global_name or "").strip(),
                interpreter_url=str(
                    data.get("interpreterUrl") or data.get("interpreter_url") or interpreter_url or ""
                ).strip(),
            )
    return Program(program=blob, global_name=global_name.strip(), interpreter_url=interpreter_url.strip())


def token_from_runtime(raw: Any) -> str:
    """Pull the VM response out of the driver's JSON (or a bare string).

    A driver-reported error is a sensor failure: the interpreter or program
    drifted, or the shim no longer satisfies the VM. An empty answer is an
    expired challenge, which is retryable with fresh f.txt.
    """
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _checked(raw, warnings=[])
    if isinstance(data, str):
        return _checked(data, warnings=[])
    if not isinstance(data, dict):
        raise SensorError(
            "botguard driver returned an unexpected type",
            context={"got": type(data).__name__},
        )
    warnings = data.get("warnings") if isinstance(data.get("warnings"), list) else []
    token = data.get("token") or data.get("bgresponse") or data.get("response")
    if isinstance(token, str) and token.strip():
        return _checked(token, warnings=warnings)
    error = data.get("error")
    if error:
        raise SensorError(
            "botguard VM did not produce a token",
            context={
                "error": str(error)[:300],
                "stage": data.get("stage"),
                "warnings": [str(item)[:120] for item in warnings][:4],
            },
        )
    raise ChallengeExpiredError(
        "botguard VM returned no token; the program may have rotated",
        context={"stage": data.get("stage")},
    )


def _checked(token: str, *, warnings: list[Any]) -> str:
    value = token.strip()
    if len(value) < MIN_TOKEN_LEN:
        raise SensorError(
            "botguard token is too short to be a VM response",
            context={"length": len(value), "warnings": [str(w)[:120] for w in warnings][:4]},
        )
    if any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise SensorError(
            "botguard token contains non-printable characters",
            context={"length": len(value)},
        )
    return value
