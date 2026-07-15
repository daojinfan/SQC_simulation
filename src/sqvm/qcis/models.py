"""Immutable public models for the Stage 7 QCIS compiler."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np


def frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return frozen_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


def frozen_array(value: np.ndarray, dtype: str) -> np.ndarray:
    result = np.asarray(value, dtype=dtype).copy(order="C")
    result.setflags(write=False)
    return result


@dataclass(frozen=True, slots=True)
class BindingPosition:
    line: int
    op: str
    operand: str


@dataclass(frozen=True, slots=True)
class BindingSpec:
    binding_id: str
    unit: str
    occurrences: int
    positions: tuple[BindingPosition, ...]


@dataclass(frozen=True, slots=True)
class QCISBinding:
    """A resolved-later literal or scan reference in a program envelope."""

    unit: str
    literal: float | None = None
    scan_ref: str | None = None


@dataclass(frozen=True, slots=True)
class QCISScanValue:
    """The immutable numeric value supplied for one runtime scan axis."""

    value: float
    unit: str


@dataclass(frozen=True, slots=True)
class QCISTemplate:
    template_id: str
    source: str
    binding_specs: Mapping[str, BindingSpec]


@dataclass(frozen=True, slots=True)
class ProgramEnvelope:
    program_schema_version: str
    instruction_set_id: str
    template_id: str
    template_sha256: str
    source_format: str
    source: str
    bindings: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class QCISInstruction:
    index: int
    op: str
    fields: Mapping[str, Any]

    def payload(self) -> dict[str, Any]:
        return {"index": self.index, "op": self.op, **dict(self.fields)}


@dataclass(frozen=True, slots=True)
class QCISProgram:
    instructions: tuple[QCISInstruction, ...]

    def payload(self) -> dict[str, Any]:
        return {"schema_version": "0.2", "instructions": [item.payload() for item in self.instructions]}


@dataclass(frozen=True, slots=True)
class QCISAuthorities:
    instruction_profile: Mapping[str, Any]
    qagent_registry: Mapping[str, Any]
    gate_configuration: Mapping[str, Any]
    waveform_registry: Mapping[str, Any]
    clock_profile: Mapping[str, Any]
    compiler_snapshot: Mapping[str, Any]
    calibration: Mapping[str, Any] | None = None
    expected_sha256: Mapping[str, str] | None = None
    templates: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class QCISCharacterizationMetric:
    """One typed experimental quality result; QPT and XEB remain distinct."""

    metric_type: str
    value: float
    uncertainty: float | None = None

    def __post_init__(self) -> None:
        if self.metric_type not in {"qpt_process_fidelity", "xeb_cycle_fidelity"}:
            raise ValueError("metric_type must be qpt_process_fidelity or xeb_cycle_fidelity")
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(float(self.value)) or not 0.0 <= float(self.value) <= 1.0:
            raise ValueError("fidelity must be a finite value in [0, 1]")
        if self.uncertainty is not None and (
            isinstance(self.uncertainty, bool)
            or not isinstance(self.uncertainty, (int, float))
            or not math.isfinite(float(self.uncertainty))
            or float(self.uncertainty) < 0.0
        ):
            raise ValueError("uncertainty must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class PhasedFSimCharacterization:
    """Accepted five-parameter PhasedFSim result, never a waveform input."""

    characterization_run_id: str
    source: str
    theta_rad: float
    zeta_rad: float
    chi_rad: float
    gamma_rad: float
    phi_rad: float
    metrics: tuple[QCISCharacterizationMetric, ...]
    leakage: float | None = None
    method: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.characterization_run_id, str) or not self.characterization_run_id:
            raise ValueError("characterization_run_id is required")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("source is required")
        method = self.source if not self.method else self.method
        if not isinstance(method, str) or not method:
            raise ValueError("method is required")
        object.__setattr__(self, "method", method)
        for name in ("theta_rad", "zeta_rad", "chi_rad", "gamma_rad", "phi_rad"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.metrics, tuple) or not self.metrics:
            raise ValueError("at least one typed QPT/XEB fidelity metric is required")
        if any(not isinstance(metric, QCISCharacterizationMetric) for metric in self.metrics):
            raise ValueError("metrics must be QCISCharacterizationMetric instances")
        if self.leakage is not None and (
            isinstance(self.leakage, bool)
            or not isinstance(self.leakage, (int, float))
            or not math.isfinite(float(self.leakage))
            or not 0.0 <= float(self.leakage) <= 1.0
        ):
            raise ValueError("leakage must be a finite value in [0, 1]")


@dataclass(frozen=True, slots=True)
class QCISLogicalWaveformPlan:
    """Idle-relative XY and flux increments emitted by the v0.2 compiler."""

    source: str
    program: QCISProgram
    ast_sha256: str
    trace: Mapping[str, Any]
    trace_sha256: str
    q1_xy: np.ndarray
    q2_xy: np.ndarray
    q1_flux: np.ndarray
    q2_flux: np.ndarray
    c_flux: np.ndarray
    carrier_metadata: Mapping[str, float]
    array_sha256: Mapping[str, str]
    authority_sha256: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class QCISCompilation:
    envelope: ProgramEnvelope
    concrete_source: str
    concrete_source_sha256: str
    plan: QCISLogicalWaveformPlan
    ast_bytes: bytes
    trace_bytes: bytes
    q1_xy: np.ndarray
    q2_xy: np.ndarray
    q1_flux: np.ndarray
    q2_flux: np.ndarray
    c_flux: np.ndarray
    carrier_metadata_bytes: bytes
    effective_q1_xy: np.ndarray
    effective_q2_xy: np.ndarray
    effective_q1_flux: np.ndarray
    effective_q2_flux: np.ndarray
    effective_c_flux: np.ndarray
    coefficient_inventory_bytes: bytes

    @property
    def materialized_source(self) -> str:
        return self.concrete_source

    @property
    def trace(self) -> Mapping[str, Any]:
        return self.plan.trace
