"""2captcha-compat API schemas. See docs/protocol.md for the full spec."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from captcha_common.enums import CaptchaType, SolveMode, TaskStatus


class TaskPayload(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    type: CaptchaType
    sitekey: str | None = Field(default=None, alias="websiteKey")
    site_url: str | None = Field(default=None, alias="websiteURL")
    user_agent: str | None = Field(default=None, alias="userAgent")
    proxy: str | None = Field(default=None, alias="proxyAddress")
    language: str | None = Field(default=None, alias="languagePool")
    preferred_mode: SolveMode = SolveMode.AUTO


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


def status_to_api(status: TaskStatus) -> Literal["ready", "processing", "failed"]:
    if status == TaskStatus.READY:
        return "ready"
    if status == TaskStatus.FAILED:
        return "failed"
    return "processing"
