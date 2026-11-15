"""2captcha-style error envelope + mapping from internal CaptchaError codes.

Public API never returns HTTP 4xx/5xx for business failures — 2captcha clients
expect HTTP 200 with `errorId != 0`. We keep the FastAPI validation exception
handler returning JSON in the same shape.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse

# --- 2captcha public error codes ---------------------------------------------

# (errorId, errorCode, errorDescription)
ERR_NO_SLOT: tuple[int, str, str] = (
    2,
    "ERROR_NO_SLOT_AVAILABLE",
    "No empty slots available on the server. Try again later.",
)
ERR_ZERO_BALANCE: tuple[int, str, str] = (
    10,
    "ERROR_ZERO_BALANCE",
    "Account has zero or negative balance",
)
ERR_KEY_MISSING: tuple[int, str, str] = (
    1,
    "ERROR_KEY_DOES_NOT_EXIST",
    "Account authorization key not found",
)
ERR_WRONG_USER_KEY: tuple[int, str, str] = (
    1,
    "ERROR_WRONG_USER_KEY",
    "Account authorization key is invalid",
)
ERR_TASK_NOT_FOUND: tuple[int, str, str] = (
    16,
    "ERROR_TASK_NOT_FOUND",
    "Task with the given id was not found",
)
ERR_UNSUPPORTED_TYPE: tuple[int, str, str] = (
    12,
    "ERROR_TASK_NOT_SUPPORTED",
    "Captcha type is not supported",
)
ERR_BAD_REQUEST: tuple[int, str, str] = (
    21,
    "ERROR_BAD_REQUEST",
    "Request malformed or missing required fields",
)
ERR_INTERNAL: tuple[int, str, str] = (
    999,
    "ERROR_INTERNAL",
    "Internal service error",
)


# Map CaptchaError.code → public envelope.
_CODE_MAP: dict[str, tuple[int, str, str]] = {
    "ERR_UNSOLVABLE": (12, "ERROR_CAPTCHA_UNSOLVABLE", "Captcha was not solved"),
    "ERR_TIMEOUT": (14, "ERROR_RECAPTCHA_TIMEOUT", "Timeout waiting for the captcha to be solved"),
    "ERR_PROXY": (23, "ERROR_PROXY_CONNECT_REFUSED", "Proxy connection failed"),
    "ERR_SENSOR": (24, "ERROR_SENSOR_INVALID", "Antibot sensor payload rejected"),
    "ERR_JS_RUNTIME": (25, "ERROR_JS_RUNTIME", "JS runtime failure"),
    "ERR_CHALLENGE_EXPIRED": (26, "ERROR_CHALLENGE_EXPIRED", "Challenge data is stale"),
    "ERR_MODEL_INFERENCE": (27, "ERROR_MODEL_INFERENCE", "Model inference failed"),
    "ERR_CONFIG": (999, "ERROR_INTERNAL", "Internal service error"),
    "ERR_BALANCE": ERR_ZERO_BALANCE,
}


def envelope(err: tuple[int, str, str]) -> dict[str, Any]:
    eid, code, desc = err
    return {"errorId": eid, "errorCode": code, "errorDescription": desc}


def classify(code: str, message: str | None = None) -> dict[str, Any]:
    eid, ecode, desc = _CODE_MAP.get(code, (999, "ERROR_INTERNAL", message or "Internal service error"))
    if message:
        desc = message
    return {"errorId": eid, "errorCode": ecode, "errorDescription": desc}


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> ORJSONResponse:
    """Any pydantic validation failure returns a 2captcha ERROR_BAD_REQUEST envelope.
    The validator's own detail list rides along in `errorDescription` so callers
    can see what field they got wrong — 2captcha itself just says the thing,
    but a line of context costs nothing and saves an hour of head-scratching.
    """
    first = ""
    try:
        errs = exc.errors()
        if errs:
            loc = ".".join(str(p) for p in errs[0].get("loc", ()) if p != "body")
            first = f"{loc}: {errs[0].get('msg', '')}".strip(": ")
    except Exception:
        first = ""
    _eid, code, default = ERR_BAD_REQUEST
    body = {
        "errorId": _eid,
        "errorCode": code,
        "errorDescription": first or default,
    }
    return ORJSONResponse(
        content=body,
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )

