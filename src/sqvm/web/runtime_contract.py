"""Exhaustive ownership contract for every Active editable configuration section."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


RUNTIME_CONFIGURATION_CONSUMERS = MappingProxyType(
    {
        "control_values.clock": (
            "qcis_compiler",
            "stage4_1_electronics",
        ),
        "control_values.dac": ("stage4_1_electronics",),
        "control_values.lane_order": ("stage4_1_electronics",),
        "control_values.lanes": ("stage4_1_electronics",),
        "control_values.static_mixing": ("stage4_1_electronics",),
        "control_values.idle_flux_phi0": (
            "qcis_compiler",
            "stage4_1_electronics",
            "calibration_model",
        ),
        "control_values.acceptance": (
            "qcis_compiler",
            "stage4_1_electronics",
        ),
        "control_values.simulation": ("calibration_model",),
        "calibration_values.qagents": ("qcis_compiler",),
        "calibration_values.gate_configuration": ("qcis_compiler",),
        "calibration_values.waveform_registry": ("qcis_compiler",),
        "calibration_values.fsim_characterizations": (
            "configuration_evidence",
        ),
    }
)


def assert_runtime_configuration_covered(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "control_values",
        "calibration_values",
    }:
        raise ValueError("Active editable configuration partitions are not exact")
    actual: set[str] = set()
    for partition in ("control_values", "calibration_values"):
        section = value.get(partition)
        if not isinstance(section, Mapping):
            raise ValueError(f"Active {partition} must be a mapping")
        actual.update(f"{partition}.{name}" for name in section)
    expected = set(RUNTIME_CONFIGURATION_CONSUMERS)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ValueError(
            f"runtime configuration consumer coverage mismatch; missing={missing}, unknown={unknown}"
        )


__all__ = [
    "RUNTIME_CONFIGURATION_CONSUMERS",
    "assert_runtime_configuration_covered",
]
