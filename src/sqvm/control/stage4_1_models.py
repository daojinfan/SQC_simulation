"""Typed, in-memory contracts for the Stage 4.1 parameterized control core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from sqvm.control.models import ControlChannelRegistry
from sqvm.control.stage4_models import ControlChainConfig


class ParameterizedControlReasonCode(StrEnum):
    PLAN_SCHEMA_INVALID = "PLAN_SCHEMA_INVALID"
    PLAN_AUTHORITY_MISMATCH = "PLAN_AUTHORITY_MISMATCH"
    PLAN_HASH_MISMATCH = "PLAN_HASH_MISMATCH"
    ARRAY_CONTRACT_INVALID = "ARRAY_CONTRACT_INVALID"
    CLOCK_MISMATCH = "CLOCK_MISMATCH"
    NAMED_MAPPING_INVALID = "NAMED_MAPPING_INVALID"
    CONTROL_AUTHORITY_INVALID = "CONTROL_AUTHORITY_INVALID"
    MIXING_MATRIX_INVALID = "MIXING_MATRIX_INVALID"
    LATENCY_CONTRACT_INVALID = "LATENCY_CONTRACT_INVALID"
    DAC_RANGE_EXCEEDED = "DAC_RANGE_EXCEEDED"
    QUANTIZATION_BOUND_EXCEEDED = "QUANTIZATION_BOUND_EXCEEDED"
    DEVICE_LIMIT_EXCEEDED = "DEVICE_LIMIT_EXCEEDED"


class ParameterizedControlError(ValueError):
    """Stable failure for the non-publishing Stage 4.1 core."""

    def __init__(self, code: ParameterizedControlReasonCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else str(code))


def freeze_array(value: np.ndarray, dtype: str) -> np.ndarray:
    result = np.asarray(value, dtype=dtype).copy(order="C")
    result.setflags(write=False)
    return result


def freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return freeze_mapping(value)
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class LogicalArrayInventoryRow:
    name: str
    dtype: str
    shape: tuple[int, ...]
    unit: str
    byte_length: int
    sha256: str


@dataclass(frozen=True, slots=True)
class QCISV03LogicalWaveformPlan:
    """The only QCIS plan form admitted by Stage 4.1 electronics."""

    schema_version: str
    profile_id: str
    point_id: str
    concrete_source_sha256: str
    ast_sha256: str
    trace_sha256: str
    sample_count: int
    dt_ns: float
    xy_q1_i: np.ndarray
    xy_q1_q: np.ndarray
    xy_q2_i: np.ndarray
    xy_q2_q: np.ndarray
    flux_q1: np.ndarray
    flux_q2: np.ndarray
    flux_c: np.ndarray
    frame_reference_frequency_GHz: Mapping[str, float]
    frame_reference_authority_sha256: Mapping[str, str]
    array_inventory: Mapping[str, LogicalArrayInventoryRow]
    drive_event_inventory: tuple[Mapping[str, Any], ...]
    authority_sha256: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ParameterizedControlContext:
    """Frozen electronics and device constraints for one parameterized point."""

    repository_root: Path
    output_root: Path
    control_chain_config: ControlChainConfig
    channel_registry: ControlChannelRegistry
    device_flux_limits_phi0: Mapping[str, tuple[float, float]]
    authority_sha256: Mapping[str, str]
    expected_plan_authority_sha256: Mapping[str, str]
    stage4_compatibility_approved: bool
    compiler_source_snapshot: Mapping[str, Any]
    environment_snapshot: Mapping[str, Any]
    publication_policy: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ParameterizedControlCompilation:
    """Immutable pre-publication result with every electronics intermediate."""

    schema_version: str
    artifact_type: str
    status: str
    plan: QCISV03LogicalWaveformPlan
    logical_flux_absolute_phi0: Mapping[str, np.ndarray]
    requested_voltage_V: Mapping[str, np.ndarray]
    dac_codes: Mapping[str, np.ndarray]
    reconstructed_voltage_V: Mapping[str, np.ndarray]
    delivered_voltage_V: Mapping[str, np.ndarray]
    effective_xy_drive_GHz: Mapping[str, np.ndarray]
    effective_flux_delta_phi0: Mapping[str, np.ndarray]
    effective_absolute_flux_phi0: Mapping[str, np.ndarray]
    logical_time_center_ns: np.ndarray
    awg_time_center_ns: np.ndarray
    effective_time_center_ns: np.ndarray
    checks: tuple[Mapping[str, Any], ...]
    metrics: Mapping[str, Any]
    context_authority_sha256: Mapping[str, str]

    @property
    def point_id(self) -> str:
        return self.plan.point_id

    @property
    def logical_arrays(self) -> Mapping[str, np.ndarray]:
        return freeze_mapping({
            "q1_i": self.plan.xy_q1_i,
            "q1_q": self.plan.xy_q1_q,
            "q2_i": self.plan.xy_q2_i,
            "q2_q": self.plan.xy_q2_q,
            "q1_flux_delta": self.plan.flux_q1,
            "q2_flux_delta": self.plan.flux_q2,
            "c_flux_delta": self.plan.flux_c,
        })

    @property
    def awg_arrays(self) -> Mapping[str, Mapping[str, np.ndarray]]:
        return freeze_mapping({
            lane: freeze_mapping({
                "requested_voltage": self.requested_voltage_V[lane],
                "dac_codes": self.dac_codes[lane],
                "reconstructed_voltage": self.reconstructed_voltage_V[lane],
                "delivered_voltage": self.delivered_voltage_V[lane],
            })
            for lane in self.requested_voltage_V
        })

    @property
    def effective_arrays(self) -> Mapping[str, np.ndarray]:
        return freeze_mapping({
            "time_center_ns": self.effective_time_center_ns,
            **self.effective_xy_drive_GHz,
            **{f"{name}_flux_delta": value for name, value in self.effective_flux_delta_phi0.items()},
            **{f"{name}_flux_absolute": value for name, value in self.effective_absolute_flux_phi0.items()},
        })

    @property
    def source_binding(self) -> Mapping[str, Any]:
        return freeze_mapping({
            "point_id": self.plan.point_id,
            "concrete_source_sha256": self.plan.concrete_source_sha256,
            "ast_sha256": self.plan.ast_sha256,
            "trace_sha256": self.plan.trace_sha256,
            "logical_array_inventory": self.plan.array_inventory,
        })

    @property
    def authority_binding(self) -> Mapping[str, Any]:
        return freeze_mapping({
            "plan_authority_sha256": self.plan.authority_sha256,
            "frame_reference_authority_sha256": self.plan.frame_reference_authority_sha256,
            "control_authority_sha256": self.context_authority_sha256,
        })
