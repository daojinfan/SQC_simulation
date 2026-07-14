"""Immutable built-in experiment and backend registries."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from sqvm.runtime.models import ExperimentDefinition


@dataclass(frozen=True, slots=True)
class ExperimentRegistry:
    _definitions: Mapping[str, ExperimentDefinition]

    def __init__(self, definitions: tuple[ExperimentDefinition, ...]):
        by_id = {definition.experiment_id: definition for definition in definitions}
        if len(by_id) != len(definitions):
            raise ValueError("duplicate experiment identifier in registry")
        object.__setattr__(self, "_definitions", MappingProxyType(by_id))

    def resolve(self, experiment_id: str) -> ExperimentDefinition:
        try:
            return self._definitions[experiment_id]
        except KeyError as exc:
            raise ValueError(f"unregistered experiment_id: {experiment_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._definitions)


@dataclass(frozen=True, slots=True)
class BackendRegistry:
    _backends: Mapping[str, object]

    def __init__(self, backends: tuple[object, ...]):
        by_id: dict[str, object] = {}
        for backend in backends:
            backend_id = getattr(backend, "backend_id", None)
            if not isinstance(backend_id, str):
                raise ValueError("backend must define a string backend_id")
            if backend_id in by_id:
                raise ValueError("duplicate backend identifier in registry")
            by_id[backend_id] = backend
        object.__setattr__(self, "_backends", MappingProxyType(by_id))

    def resolve(self, backend_id: str) -> object:
        try:
            return self._backends[backend_id]
        except KeyError as exc:
            raise ValueError(f"unregistered backend_id: {backend_id}") from exc

    def ids(self) -> tuple[str, ...]:
        return tuple(self._backends)


def get_builtin_experiment_registry() -> ExperimentRegistry:
    from sqvm.runtime.fake import deterministic_fake_definition

    return ExperimentRegistry((deterministic_fake_definition(),))


def get_builtin_backend_registry() -> BackendRegistry:
    from sqvm.runtime.fake import DeterministicFakeBackend

    return BackendRegistry((DeterministicFakeBackend(),))
