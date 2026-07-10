"""Validation rules for stage 1 device configs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqvm.device.capacitance import DEFAULT_2Q1C_NODE_ORDER
from sqvm.device.spec import SUPPORTED_SCHEMA_VERSION, SUPPORTED_TOPOLOGY, ComponentSpec, DeviceSpec


ALLOWED_PRIOR_FIELDS = {
    "estimated_f01_GHz",
    "estimated_anharmonicity_GHz",
    "estimated_idle_frequency_GHz",
    "estimated_flux_sweet_spot_phi0",
    "r1_frequency_GHz",
    "r2_frequency_GHz",
    "note",
}


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    level: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "path": self.path, "message": self.message}


@dataclass(frozen=True, slots=True)
class ValidationReport:
    errors: tuple[ValidationIssue, ...] = ()
    warnings: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


def validate_device(device: DeviceSpec) -> ValidationReport:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    _require(device.schema_version == SUPPORTED_SCHEMA_VERSION, errors, "schema_version", "unsupported schema version")
    _require(device.topology == SUPPORTED_TOPOLOGY, errors, "device.topology", "must be 2q1c2r")
    _validate_required_components(device, errors)
    _validate_components(device, errors, warnings)
    _validate_capacitors(device, errors)
    _validate_channels(device, errors, warnings)
    _validate_priors(device, errors)

    return ValidationReport(errors=tuple(errors), warnings=tuple(warnings))


def _validate_required_components(device: DeviceSpec, errors: list[ValidationIssue]) -> None:
    for name in ("q1", "q2", "c", "r1", "r2"):
        _require(name in device.components, errors, f"device.components.{name}", "component is required")


def _validate_components(
    device: DeviceSpec, errors: list[ValidationIssue], warnings: list[ValidationIssue]
) -> None:
    expected = {
        "q1": ("tunable_transmon", "qubit", True, ("q1_p", "q1_m")),
        "q2": ("tunable_transmon", "qubit", True, ("q2_p", "q2_m")),
        "c": ("tunable_coupler", "coupler", False, ("c",)),
    }
    for name, (kind, role, floating, nodes) in expected.items():
        component = device.components.get(name)
        if component is None:
            continue
        base = f"device.components.{name}"
        _require(component.kind == kind, errors, f"{base}.kind", f"must be {kind}")
        _require(component.role == role, errors, f"{base}.role", f"must be {role}")
        _require(component.floating is floating, errors, f"{base}.floating", f"must be {floating}")
        _require(component.nodes == nodes, errors, f"{base}.nodes", f"must be {list(nodes)}")
        _require_positive(component.capacitance_fF, errors, f"{base}.capacitance_fF")
        _require(component.squid is not None, errors, f"{base}.squid", "SQUID is required")
        if component.squid is not None:
            _require_positive(component.squid.rn1_ohm, errors, f"{base}.squid.rn1_ohm")
            _require_positive(component.squid.rn2_ohm, errors, f"{base}.squid.rn2_ohm")
            _warn_range(component.squid.rn1_ohm, warnings, f"{base}.squid.rn1_ohm", 1000.0, 100000.0, "ohm")
            _warn_range(component.squid.rn2_ohm, warnings, f"{base}.squid.rn2_ohm", 1000.0, 100000.0, "ohm")
            _warn_range(
                component.squid.flux_bias_phi0, warnings, f"{base}.squid.flux_bias_phi0", -1.0, 1.0, "Phi0"
            )
        _warn_range(component.capacitance_fF, warnings, f"{base}.capacitance_fF", 20.0, 200.0, "fF")

    for name in ("r1", "r2"):
        component = device.components.get(name)
        if component is None:
            continue
        _validate_readout_component(component, errors, warnings)


def _validate_readout_component(
    component: ComponentSpec, errors: list[ValidationIssue], warnings: list[ValidationIssue]
) -> None:
    base = f"device.components.{component.name}"
    _require(component.kind == "readout_resonator", errors, f"{base}.kind", "must be readout_resonator")
    _require(component.role == "readout", errors, f"{base}.role", "must be readout")
    _require(component.coupled_to in {"q1", "q2"}, errors, f"{base}.coupled_to", "must be q1 or q2")
    _require(
        component.coupling_node in DEFAULT_2Q1C_NODE_ORDER,
        errors,
        f"{base}.coupling_node",
        "must reference a 2q1c node",
    )
    _require_positive(component.frequency_GHz, errors, f"{base}.frequency_GHz")
    _require_positive(component.coupling_capacitance_fF, errors, f"{base}.coupling_capacitance_fF")
    _require_positive(component.kappa_MHz, errors, f"{base}.kappa_MHz")
    _warn_range(component.frequency_GHz, warnings, f"{base}.frequency_GHz", 4.0, 10.0, "GHz")
    _warn_range(component.kappa_MHz, warnings, f"{base}.kappa_MHz", 0.01, 50.0, "MHz")


def _validate_capacitors(device: DeviceSpec, errors: list[ValidationIssue]) -> None:
    known_nodes = set(DEFAULT_2Q1C_NODE_ORDER) | {"ground"}
    for index, capacitor in enumerate(device.capacitors):
        base = f"device.capacitors[{index}]"
        _require_positive(capacitor.capacitance_fF, errors, f"{base}.capacitance_fF")
        if capacitor.node_a == capacitor.node_b:
            _error(errors, f"{base}.between", "must connect two different nodes")
        for node in (capacitor.node_a, capacitor.node_b):
            if node not in known_nodes:
                _error(errors, f"{base}.between", f"unknown node {node!r}")


def _validate_channels(
    device: DeviceSpec, errors: list[ValidationIssue], warnings: list[ValidationIssue]
) -> None:
    for name, channel in device.channels.items():
        base = f"device.channels.{name}"
        if channel.kind not in {"xy", "z", "readout"}:
            _error(errors, f"{base}.kind", "must be xy, z, or readout")
        if channel.target not in device.components:
            _error(errors, f"{base}.target", "must reference an existing component")

    xy_targets = {channel.target for channel in device.channels.values() if channel.kind == "xy"}
    readout_targets = {channel.target for channel in device.channels.values() if channel.kind == "readout"}
    for target in ("q1", "q2"):
        if target not in xy_targets:
            _warn(warnings, f"device.channels.{target}_xy", "missing XY channel")
    for target in ("r1", "r2"):
        if target not in readout_targets:
            _warn(warnings, f"device.channels.{target}_ro", "missing readout channel")


def _validate_priors(device: DeviceSpec, errors: list[ValidationIssue]) -> None:
    def visit(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            if isinstance(item, dict):
                visit(item, next_path)
            elif key not in ALLOWED_PRIOR_FIELDS:
                _error(errors, f"device.priors.{next_path}", "prior field is not allowed in stage 1")

    visit(device.priors, "")


def _require(condition: bool, errors: list[ValidationIssue], path: str, message: str) -> None:
    if not condition:
        _error(errors, path, message)


def _require_positive(value: float | None, errors: list[ValidationIssue], path: str) -> None:
    if value is None:
        _error(errors, path, "is required")
    elif value <= 0:
        _error(errors, path, "must be positive")


def _warn_range(
    value: float | None,
    warnings: list[ValidationIssue],
    path: str,
    low: float,
    high: float,
    unit: str,
) -> None:
    if value is None:
        return
    if value < low or value > high:
        _warn(warnings, path, f"{value:g} {unit} is outside loose sanity range [{low:g}, {high:g}]")


def _error(errors: list[ValidationIssue], path: str, message: str) -> None:
    errors.append(ValidationIssue(level="error", path=path, message=message))


def _warn(warnings: list[ValidationIssue], path: str, message: str) -> None:
    warnings.append(ValidationIssue(level="warning", path=path, message=message))
