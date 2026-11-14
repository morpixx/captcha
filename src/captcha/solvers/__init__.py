"""Solver plugins: one package per captcha family.

A solver declares which CaptchaType values it answers and implements solve_http.
Registration happens at import time via @register_solver; the worker calls
discover_solvers() and keeps only the solvers that route to its lane.

Family packages are NOT imported here on purpose — classification/detection pull
torch, and the HTTP worker must not pay for that. The worker decides what to load.
"""

from __future__ import annotations

from .base import Solver, SolverContext, SolverInfo
from .harness import SolverFixture, run_fixtures
from .registry import SolverRegistry, discover_solvers, global_registry, register_solver

__all__ = [
    "Solver",
    "SolverContext",
    "SolverFixture",
    "SolverInfo",
    "SolverRegistry",
    "discover_solvers",
    "global_registry",
    "register_solver",
    "run_fixtures",
]
