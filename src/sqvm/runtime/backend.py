"""Backend protocol and capability admission for Stage 6 core."""

from __future__ import annotations

from typing import Protocol

from sqvm.runtime.models import BackendAdmission, BackendCapabilities, BackendCommand, PointResult, PreparedExperiment


class ExperimentBackend(Protocol):
    backend_id: str

    def capabilities(self) -> BackendCapabilities: ...

    def admit(self, prepared_experiment: PreparedExperiment) -> BackendAdmission: ...

    def execute_point(self, command: BackendCommand, context: object) -> PointResult: ...


def admit_backend(backend: ExperimentBackend, prepared_experiment: PreparedExperiment, required: frozenset[str]) -> BackendAdmission:
    """Fail closed when a backend lacks any definition-required capability."""

    admission = backend.admit(prepared_experiment)
    missing = required.difference(admission.capabilities.values)
    if missing:
        raise ValueError(f"backend lacks required capabilities: {','.join(sorted(missing))}")
    if "cooperative_deadline_v1" not in admission.capabilities.values:
        raise ValueError("backend lacks cooperative_deadline_v1")
    return admission
