"""Route aggregator. `include_routers(app)` wires every router onto the app."""

from __future__ import annotations

from fastapi import FastAPI

from captcha.services.gateway.routes import admin, balance, health, tasks


def include_routers(app: FastAPI) -> None:
    app.include_router(tasks.router)
    app.include_router(balance.router)
    app.include_router(health.router)
    app.include_router(admin.router)
