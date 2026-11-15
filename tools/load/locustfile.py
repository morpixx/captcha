"""Locust alternative to the k6 scenario.

k6 is the throughput tool (constant-arrival-rate is what makes "1k/s" honest).
Locust is here for a long soak with request-level breakdowns, or for machines
without k6:

    locust -f tools/load/locustfile.py --headless \\
        -u 1000 -r 200 --run-time 300s --host http://127.0.0.1:8080

Set CLIENT_KEY in the environment; the account must be funded and the gateway
must run with the quota limits disabled (see tools/load/README.md). Tasks are
completed by tools/load/fake_solver.py.
"""

from __future__ import annotations

import logging
import os
import random

from locust import HttpUser, constant, task

log = logging.getLogger(__name__)

CLIENT_KEY = os.getenv("CLIENT_KEY", "")
TYPES = [value.strip() for value in os.getenv("TYPE", "text_captcha").split(",") if value.strip()]


class CreateTaskUser(HttpUser):
    """Saturating createTask client: no think time between requests."""

    wait_time = constant(0)

    def on_start(self) -> None:
        if not CLIENT_KEY:
            raise RuntimeError("CLIENT_KEY is required; export it before starting locust")
        response = self.client.get(
            "/getBalance",
            params={"clientKey": CLIENT_KEY},
            name="getBalance",
        )
        body = response.json()
        if response.status_code != 200 or body.get("errorId") != 0:
            raise RuntimeError(f"getBalance failed: HTTP {response.status_code} {response.text}")
        log.info("load client balance=%s", body.get("balance"))

    @task
    def create_task(self) -> None:
        response = self.client.post(
            "/createTask",
            json={
                "clientKey": CLIENT_KEY,
                "task": {
                    "type": random.choice(TYPES),
                    "websiteURL": "https://loadtest.invalid/login",
                    "websiteKey": "loadtest-site-key",
                },
            },
            name="createTask",
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code != 200 or body.get("errorId") != 0:
            # Locust counts non-2xx as failures only when the request is marked
            # as such; do it explicitly so business errors are visible too.
            response.failure(f"errorId={body.get('errorId')} code={body.get('errorCode')}")
