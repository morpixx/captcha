from __future__ import annotations

import importlib
import importlib.metadata
import logging
from collections.abc import Iterable

from captcha.domain.enums import CaptchaType
from captcha.solvers.base import Solver, SolverInfo

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "captcha.solvers"


class SolverRegistry:
    """Holds all loaded solver classes keyed by CaptchaType.

    Multiple solvers may claim the same type (e.g. HTTP primary + browser fallback).
    Resolution order: explicit mode > experimental=False > first registered.
    """

    def __init__(self) -> None:
        self._by_type: dict[CaptchaType, list[type[Solver]]] = {}
        self._by_name: dict[str, type[Solver]] = {}

    def register(self, solver_cls: type[Solver]) -> None:
        info = getattr(solver_cls, "info", None)
        if not isinstance(info, SolverInfo):
            raise TypeError(f"{solver_cls.__name__} missing SolverInfo")
        if info.name in self._by_name:
            log.warning("solver name collision: %s", info.name)
        self._by_name[info.name] = solver_cls
        for ct in info.types:
            self._by_type.setdefault(ct, []).append(solver_cls)
        log.info("registered solver %s for %s", info.name, [t.value for t in info.types])

    def for_type(self, ct: CaptchaType) -> list[type[Solver]]:
        return list(self._by_type.get(ct, ()))

    def pick(self, ct: CaptchaType, *, allow_experimental: bool = False) -> type[Solver] | None:
        cands = self._by_type.get(ct, [])
        if not cands:
            return None
        non_exp = [c for c in cands if not c.info.experimental]
        if non_exp:
            return non_exp[0]
        return cands[0] if allow_experimental else None

    def all_types(self) -> set[CaptchaType]:
        return set(self._by_type.keys())

    def all_solvers(self) -> Iterable[type[Solver]]:
        return self._by_name.values()


_global_registry = SolverRegistry()


def register_solver(solver_cls: type[Solver]) -> type[Solver]:
    """Decorator form — most solver modules call this at import time."""
    _global_registry.register(solver_cls)
    return solver_cls


def discover_solvers(
    *,
    extra_modules: Iterable[str] = (),
    registry: SolverRegistry | None = None,
) -> SolverRegistry:
    """Load all solvers via entry_points, plus any extra dotted modules.

    Returns a fresh registry when one isn't passed; the global registry is populated
    as a side effect of importing solver modules that call `register_solver`.
    """
    reg = registry or _global_registry
    eps = importlib.metadata.entry_points().select(group=ENTRY_POINT_GROUP)
    loaded: set[str] = set()
    for ep in eps:
        try:
            cls = ep.load()
            if not isinstance(cls, type) or not issubclass(cls, Solver):
                log.error("entry_point %s is not a Solver subclass", ep.name)
                continue
            if cls.info.name in loaded:
                continue
            reg.register(cls)
            loaded.add(cls.info.name)
        except Exception as exc:
            log.exception("failed to load solver entry_point %s: %s", ep.name, exc)
    for modname in extra_modules:
        try:
            importlib.import_module(modname)
        except Exception as exc:
            log.exception("failed to import extra solver module %s: %s", modname, exc)
    return reg


def global_registry() -> SolverRegistry:
    return _global_registry
