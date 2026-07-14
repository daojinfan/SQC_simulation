"""Immutable public models for the Stage 7 QCIS compiler."""

from __future__ import annotations

from dataclasses import dataclass
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
        return {"schema_version": "0.1", "instructions": [item.payload() for item in self.instructions]}


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
class QCISLogicalWaveformPlan:
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
