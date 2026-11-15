"""2captcha-compat API schemas. See docs/protocol.md for the full spec."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from captcha.adapters.netguard import Resolver, ensure_public_url_async
from captcha.domain.enums import CaptchaType, TaskStatus
from captcha.services.gateway.type_map import normalize_task_payload


class TaskPayload(BaseModel):
    """One 2captcha task object.

    The wire spelling is not ours: clients send official 2captcha/capsolver
    names, flat proxy fields and hCaptcha extras. The before-validator folds
    all of that into the same internal shape (see
    `captcha.services.gateway.type_map`) before the enum and the field types
    run, so a bad type or a bad proxy is still a clean ERROR_BAD_REQUEST.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: CaptchaType
    sitekey: str | None = Field(default=None, alias="websiteKey")
    site_url: str | None = Field(default=None, alias="websiteURL")
    user_agent: str | None = Field(default=None, alias="userAgent")
    proxy: str | None = None
    language: str | None = Field(default=None, alias="languagePool")

    @model_validator(mode="before")
    @classmethod
    def _normalize_wire_fields(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            return normalize_task_payload(data)
        return data


#: Task fields whose value a solver or the browser lane will eventually fetch.
#: Production contract §8: all of them go through `adapters.netguard` before
#: the task is priced or queued.
CLIENT_URL_FIELDS: tuple[str, ...] = (
    "site_url",
    "websiteURL",
    "websiteUrl",
    "pageurl",
    "challenge_url",
    "surl",
    "api_server",
)

#: Legacy vendors send these as a bare host (FunCaptcha `surl`, GeeTest
#: `api_server`); the solvers prepend https:// themselves, so validate the
#: shape they will actually fetch instead of rejecting a working client.
_BARE_HOST_URL_FIELDS = frozenset({"surl", "api_server"})


def _url_candidate(field: str, value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if field in _BARE_HOST_URL_FIELDS and "://" not in text:
        return "https://" + text.lstrip("/")
    return text


async def validate_task_urls(
    payload: Mapping[str, Any],
    *,
    resolver: Resolver | None = None,
) -> None:
    """Reject private/loopback/metadata targets before the task is queued.

    Runs before pricing, so a rejected URL never costs the client anything.
    DNS happens on a worker thread (`ensure_public_url_async`): the gateway
    serves every account on one loop and a blocking getaddrinfo would stall it.
    """
    validated: set[str] = set()
    for field in CLIENT_URL_FIELDS:
        candidate = _url_candidate(field, payload.get(field))
        if candidate is None or candidate in validated:
            continue
        await ensure_public_url_async(candidate, resolver=resolver, field=field)
        validated.add(candidate)


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    client_key: str = Field(alias="clientKey")
    task: TaskPayload
    callback_url: str | None = Field(default=None, alias="callbackUrl")
    soft_id: int | None = Field(default=None, alias="softId")


class CreateTaskResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    task_id: str | None = Field(default=None, alias="taskId")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_description: str | None = Field(default=None, alias="errorDescription")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class GetTaskResultRequest(BaseModel):
    client_key: str = Field(alias="clientKey")
    task_id: str = Field(alias="taskId")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class GetTaskResultResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    status: Literal["ready", "processing", "failed"] = "processing"
    solution: dict[str, Any] | None = None
    cost: str | None = None
    ip: str | None = None
    create_time: int | None = Field(default=None, alias="createTime")
    end_time: int | None = Field(default=None, alias="endTime")
    solve_count: int | None = Field(default=None, alias="solveCount")
    error_code: str | None = Field(default=None, alias="errorCode")
    error_description: str | None = Field(default=None, alias="errorDescription")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class GetBalanceRequest(BaseModel):
    client_key: str = Field(alias="clientKey")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class GetBalanceResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    balance: float = 0.0
    error_code: str | None = Field(default=None, alias="errorCode")
    error_description: str | None = Field(default=None, alias="errorDescription")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class ReportRequest(BaseModel):
    client_key: str = Field(alias="clientKey")
    task_id: str = Field(alias="taskId")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ReportResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    status: Literal["OK"] = "OK"

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


# ---- cabinet ----------------------------------------------------------------


class CabinetAccountResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    account_id: str = Field(alias="accountId")
    balance: float = 0.0
    disabled: bool = False
    created_at: str | None = Field(default=None, alias="createdAt")
    key_tail: str = Field(default="", alias="keyTail")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TransactionOut(BaseModel):
    id: int
    kind: str
    amount: str
    balance_after: str = Field(alias="balanceAfter")
    task_id: str | None = Field(default=None, alias="taskId")
    external_id: str | None = Field(default=None, alias="externalId")
    created_at: str = Field(alias="createdAt")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TransactionListResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    items: list[TransactionOut] = Field(default_factory=list)
    limit: int = 50
    offset: int = 0

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TopupRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    amount: Decimal = Field(gt=0, le=100_000)
    provider: str = "manual"
    return_url: str | None = Field(default=None, alias="returnUrl")


class TopupResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    intent_id: str = Field(alias="intentId")
    provider: str
    amount: str
    status: str
    payment_url: str | None = Field(default=None, alias="paymentUrl")
    instructions: str = ""

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class TopupConfirmResponse(BaseModel):
    error_id: int = Field(default=0, alias="errorId")
    intent_id: str = Field(alias="intentId")
    credited: bool = False
    balance: float = 0.0
    status: str = "pending"

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


def status_to_api(status: TaskStatus) -> Literal["ready", "processing", "failed"]:
    if status == TaskStatus.READY:
        return "ready"
    if status == TaskStatus.FAILED:
        return "failed"
    return "processing"
