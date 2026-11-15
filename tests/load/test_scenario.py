"""The driver against a mocked gateway: pacing, error taxonomy, overload."""

from __future__ import annotations

import asyncio

import httpx
import respx
from tools.load.config import LoadConfig
from tools.load.scenario import run_load

BASE = "http://gateway.test"


def _cfg(**overrides) -> LoadConfig:
    defaults = {
        "url": BASE,
        "client_key": "load-test-key",
        "rate": 40.0,
        "duration": 0.3,
        "timeout": 2.0,
        "poll_interval": 0.0,
        "poll_max_interval": 0.0,
        "solve_timeout": 5.0,
        "progress_sec": 0.0,
    }
    defaults.update(overrides)
    return LoadConfig(**defaults)


@respx.mock
async def test_ready_tasks_are_counted_end_to_end() -> None:
    respx.post(f"{BASE}/createTask").mock(
        return_value=httpx.Response(200, json={"errorId": 0, "taskId": "11111111-1111-1111-1111-111111111111"})
    )
    respx.post(f"{BASE}/getTaskResult").mock(
        return_value=httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"text": "x"}})
    )

    result = await run_load(_cfg())

    assert result.created > 0
    assert result.ready == result.created
    assert result.failed == 0
    assert result.errors == {}
    assert result.polls >= result.ready
    assert result.e2e.count == result.ready
    assert result.target_ratio > 0.0
    assert result.create.p50 >= 0.0


@respx.mock
async def test_create_task_error_id_is_tallied_not_raised() -> None:
    respx.post(f"{BASE}/createTask").mock(
        return_value=httpx.Response(200, json={"errorId": 12, "errorCode": "ERROR_CAPTCHA_UNSOLVABLE"})
    )

    result = await run_load(_cfg(rate=20.0, duration=0.2))

    assert result.created > 0
    assert result.ready == 0
    assert set(result.errors) == {"createTask:ERROR_CAPTCHA_UNSOLVABLE"}
    assert sum(result.errors.values()) == result.created


@respx.mock
async def test_http_500_and_failed_status_are_separate_tallies() -> None:
    responses = {
        "count": 0,
    }

    def create_side_effect(request: httpx.Request) -> httpx.Response:
        responses["count"] += 1
        if responses["count"] % 2 == 0:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})

    respx.post(f"{BASE}/createTask").mock(side_effect=create_side_effect)
    respx.post(f"{BASE}/getTaskResult").mock(
        return_value=httpx.Response(200, json={"errorId": 0, "status": "failed", "errorCode": "ERR_PROXY"})
    )

    result = await run_load(_cfg(rate=20.0, duration=0.4))

    assert "createTask:http_500" in result.errors
    assert "getTaskResult:ERR_PROXY" in result.errors
    assert result.failed >= 1


@respx.mock
async def test_connection_errors_are_tallied_by_exception_name() -> None:
    respx.post(f"{BASE}/createTask").mock(side_effect=httpx.ConnectError("refused"))

    result = await run_load(_cfg(rate=20.0, duration=0.2))

    assert set(result.errors) == {"createTask:ConnectError"}
    assert result.created == 0
    assert result.ready == 0


@respx.mock
async def test_in_flight_cap_drops_dispatches_and_says_so() -> None:
    async def slow_create(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"errorId": 0, "taskId": "task-1"})

    respx.post(f"{BASE}/createTask").mock(side_effect=slow_create)
    respx.post(f"{BASE}/getTaskResult").mock(
        return_value=httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"text": "x"}})
    )

    result = await run_load(_cfg(rate=400.0, duration=0.3, in_flight_max=2, solve_timeout=5.0))

    assert result.dropped > 0
    assert result.target_ratio < 0.5


@respx.mock
async def test_stuck_processing_task_hits_the_solve_timeout() -> None:
    respx.post(f"{BASE}/createTask").mock(return_value=httpx.Response(200, json={"errorId": 0, "taskId": "task-1"}))
    respx.post(f"{BASE}/getTaskResult").mock(
        return_value=httpx.Response(200, json={"errorId": 0, "status": "processing"})
    )

    result = await run_load(_cfg(rate=5.0, duration=0.1, poll_interval=0.01, solve_timeout=0.2))

    assert result.created >= 1
    assert result.solve_timeout >= 1
    assert "getTaskResult:solve_timeout" in result.errors
    assert result.ready == 0


@respx.mock
async def test_progress_callback_sees_running_counters() -> None:
    respx.post(f"{BASE}/createTask").mock(return_value=httpx.Response(200, json={"errorId": 0, "taskId": "task-1"}))
    respx.post(f"{BASE}/getTaskResult").mock(
        return_value=httpx.Response(200, json={"errorId": 0, "status": "ready", "solution": {"text": "x"}})
    )
    seen: list[dict] = []

    await run_load(_cfg(rate=10.0, duration=0.3, progress_sec=0.05), on_progress=seen.append)

    assert seen
    assert "created" in seen[0]
    assert seen[-1]["elapsed"] > 0
