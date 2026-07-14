"""Typed, immutable contracts for the Stage 6 platform-only runtime core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping


def frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy a mapping behind a read-only view for public runtime contracts."""

    return MappingProxyType({key: _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    name: str
    unit: str
    scannable: bool
    value_kind: str = "finite_scalar"


@dataclass(frozen=True, slots=True)
class ScanAxis:
    name: str
    unit: str
    values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ExecutionSettings:
    seed: int
    max_points: int
    point_budget_seconds: float
    run_budget_seconds: float
    fail_fast: bool


@dataclass(frozen=True, slots=True)
class ExperimentRequest:
    source_path: Path
    repository_root: Path
    schema_version: str
    experiment_id: str
    backend_id: str
    device_snapshot: Path
    calibration_snapshot: Path
    parameters: Mapping[str, Any]
    program: None
    axes: tuple[ScanAxis, ...]
    repetitions: int
    execution: ExecutionSettings
    allow_existing_target: bool


@dataclass(frozen=True, slots=True)
class ScanPoint:
    schema_version: str
    experiment_id: str
    point_index: int
    repetition: int
    coordinates: tuple[tuple[str, float, str], ...]
    seed: int
    point_id: str

    def payload(self, *, include_point_id: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "point_index": self.point_index,
            "repetition": self.repetition,
            "coordinates": [
                {"axis": axis, "value": value, "unit": unit}
                for axis, value, unit in self.coordinates
            ],
            "seed": self.seed,
        }
        if include_point_id:
            result["point_id"] = self.point_id
        return result

    def coordinate_map(self) -> Mapping[str, float]:
        return frozen_mapping({name: value for name, value, _unit in self.coordinates})


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    values: frozenset[str]


@dataclass(frozen=True, slots=True)
class PreparedExperiment:
    definition_id: str
    request: ExperimentRequest
    snapshots: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BackendCommand:
    experiment_id: str
    point_id: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BackendAdmission:
    backend_id: str
    capabilities: BackendCapabilities


@dataclass(frozen=True, slots=True)
class PointResult:
    values: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class RunArtifactSet:
    run_id: str
    run_dir: Path
    manifest_path: Path
    report_path: Path
    receipt_path: Path
    manifest_sha256: str
    report_sha256: str
    receipt_sha256: str
    status: str
    catalog_indexed: bool
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": self.run_dir.as_posix(),
            "manifest_path": self.manifest_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "receipt_path": self.receipt_path.as_posix(),
            "manifest_sha256": self.manifest_sha256,
            "report_sha256": self.report_sha256,
            "receipt_sha256": self.receipt_sha256,
            "status": self.status,
            "catalog_indexed": self.catalog_indexed,
            "warning": self.warning,
        }


@dataclass(frozen=True, slots=True)
class RunVerificationReport:
    ok: bool
    run_id: str
    status: str
    manifest_sha256: str
    receipt_sha256: str
    checks: tuple[Mapping[str, Any], ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "run_id": self.run_id,
            "status": self.status,
            "manifest_sha256": self.manifest_sha256,
            "receipt_sha256": self.receipt_sha256,
            "checks": [dict(row) for row in self.checks],
            "blocking_reasons": list(self.blocking_reasons),
        }


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    run_dir: Path
    manifest: Mapping[str, Any]
    report: Mapping[str, Any]
    receipt: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class CancellationReceipt:
    run_id: str
    request_path: Path
    request_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "request_path": self.request_path.as_posix(), "request_sha256": self.request_sha256}


@dataclass(frozen=True, slots=True)
class RecoveryArtifactSet:
    recovery_id: str
    original_run_id: str
    run_dir: Path
    status: str


@dataclass(frozen=True, slots=True)
class ResourceLockRecoveryReceipt:
    run_id: str
    authorization_path: Path
    resource_lock_sha256: str


@dataclass(frozen=True, slots=True)
class CatalogRebuildReport:
    ok: bool
    indexed_runs: int
    catalog_path: Path
    blocking_reasons: tuple[str, ...]


PrepareExperiment = Callable[[ExperimentRequest, Mapping[str, Any]], PreparedExperiment]
BuildCommand = Callable[[PreparedExperiment, ScanPoint], BackendCommand]


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    experiment_id: str
    parameter_specs: tuple[ParameterSpec, ...]
    program_schema: str
    required_backend_capabilities: frozenset[str]
    result_schema: Mapping[str, Any]
    dataset_schema: Mapping[str, Any]
    prepare: PrepareExperiment
    build_command: BuildCommand

    @property
    def request_schema(self) -> tuple[ParameterSpec, ...]:
        return self.parameter_specs

    def parameter_map(self) -> Mapping[str, ParameterSpec]:
        return frozen_mapping({spec.name: spec for spec in self.parameter_specs})
