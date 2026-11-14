"""Sensor builder registry. Builders self-register at import time."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from captcha_common.enums import AntibotEngine
from captcha_common.errors import ConfigError

from .base import SensorBuilder

B = TypeVar("B", bound=SensorBuilder)


class SensorRegistry:
    """Singleton mapping `AntibotEngine -> SensorBuilder instance`."""

    __slots__ = ("_builders",)

    def __init__(self) -> None:
        self._builders: dict[AntibotEngine, SensorBuilder] = {}

    def register(self, engine: AntibotEngine, builder: SensorBuilder) -> None:
        if engine in self._builders:
            raise ConfigError(
                f"sensor builder already registered for {engine.value}",
                context={"engine": engine.value},
            )
        self._builders[engine] = builder

    def get(self, engine: AntibotEngine) -> SensorBuilder:
        try:
            return self._builders[engine]
        except KeyError as e:
            raise ConfigError(
                f"no sensor builder registered for {engine.value}",
                context={"engine": engine.value},
            ) from e

    def has(self, engine: AntibotEngine) -> bool:
        return engine in self._builders

    def all(self) -> dict[AntibotEngine, SensorBuilder]:
        return dict(self._builders)


_REGISTRY = SensorRegistry()


def global_registry() -> SensorRegistry:
    return _REGISTRY


def get_builder(engine: AntibotEngine) -> SensorBuilder:
    return _REGISTRY.get(engine)


def register_builder(engine: AntibotEngine) -> Callable[[type[B]], type[B]]:
    """Class decorator — instantiates and registers the builder.

    @register_builder(AntibotEngine.AKAMAI)
    class AkamaiSensorBuilder(SensorBuilder):
        ...
    """

    def decorator(cls: type[B]) -> type[B]:
        cls.engine = engine
        _REGISTRY.register(engine, cls())
        return cls

    return decorator
