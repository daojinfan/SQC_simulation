"""Stage 1 device artifact writing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqvm.device.capacitance import CapacitanceMatrix, build_capacitance_matrix
from sqvm.device.junction import JunctionParameterTable, resolve_junction_parameters
from sqvm.device.spec import ComponentSpec, DeviceSpec
from sqvm.device.validation import ValidationReport, validate_device


@dataclass(frozen=True, slots=True)
class DeviceArtifactSet:
    root: Path
    device_artifacts: Path


def write_device_artifacts(device: DeviceSpec, output_dir: str | Path) -> DeviceArtifactSet:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    validation = validate_device(device)
    capacitance_matrix = build_capacitance_matrix(device)
    junction_parameters = resolve_junction_parameters(device)
    checks = build_checks(device, validation, capacitance_matrix, junction_parameters)
    payload = device_artifacts_payload(device, validation, capacitance_matrix, junction_parameters, checks)
    path = root / "device_artifacts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return DeviceArtifactSet(root=root, device_artifacts=path)


def device_artifacts_payload(
    device: DeviceSpec,
    validation: ValidationReport,
    capacitance_matrix: CapacitanceMatrix,
    junction_parameters: JunctionParameterTable,
    checks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": device.schema_version,
        "artifact_type": "stage_01_device_model",
        "artifact_version": "0.1",
        "source_config": str(device.source_path) if device.source_path else None,
        "device_summary": {"name": device.name, "topology": device.topology},
        "components": {name: _component_dict(component) for name, component in device.components.items()},
        "channels": {
            name: {"kind": channel.kind, "target": channel.target, "port": channel.port}
            for name, channel in device.channels.items()
        },
        "priors": device.priors,
        "capacitance_matrix": {
            "nodes": list(capacitance_matrix.nodes),
            "matrix_fF": [list(row) for row in capacitance_matrix.matrix_fF],
        },
        "junction_parameters": [
            {
                "component": row.component,
                "junction": row.junction,
                "rn_ohm": row.rn_ohm,
                "ej_GHz": row.ej_GHz,
                "source": row.source,
            }
            for row in junction_parameters.rows
        ],
        "validation": validation.to_dict(),
        "checks": checks,
    }


def build_checks(
    device: DeviceSpec,
    validation: ValidationReport,
    capacitance_matrix: CapacitanceMatrix,
    junction_parameters: JunctionParameterTable,
) -> list[dict[str, Any]]:
    checks = [
        _check("validation_has_no_errors", validation.ok, "validation has no errors"),
        _check(
            "capacitance_matrix_shape",
            capacitance_matrix.shape == (len(capacitance_matrix.nodes), len(capacitance_matrix.nodes)),
            f"shape is {capacitance_matrix.shape}",
        ),
        _check(
            "capacitance_matrix_symmetric",
            _is_symmetric(capacitance_matrix.matrix_fF),
            "matrix is symmetric",
        ),
        _check(
            "capacitance_matrix_nonnegative_diagonal",
            all(row[i] >= 0 for i, row in enumerate(capacitance_matrix.matrix_fF)),
            "diagonal entries are nonnegative",
        ),
        _check(
            "junction_ej_positive",
            all(row.ej_GHz > 0 for row in junction_parameters.rows),
            "resolved EJ values are positive",
        ),
        _check(
            "required_tunable_components_have_junctions",
            {row.component for row in junction_parameters.rows} == {"q1", "q2", "c"},
            "q1, q2, and c have resolved junction parameters",
        ),
    ]
    if not device.capacitors:
        checks.append(_check("explicit_capacitors_present", False, "no explicit capacitors configured"))
    else:
        checks.append(_check("explicit_capacitors_present", True, "explicit capacitors configured"))
    return checks


def _component_dict(component: ComponentSpec) -> dict[str, Any]:
    data: dict[str, Any] = {
        "kind": component.kind,
        "role": component.role,
        "floating": component.floating,
        "nodes": list(component.nodes),
    }
    for key in (
        "capacitance_fF",
        "coupled_to",
        "coupling_node",
        "frequency_GHz",
        "coupling_capacitance_fF",
        "kappa_MHz",
    ):
        value = getattr(component, key)
        if value is not None:
            data[key] = value
    if component.squid is not None:
        data["squid"] = {
            "rn1_ohm": component.squid.rn1_ohm,
            "rn2_ohm": component.squid.rn2_ohm,
            "flux_bias_phi0": component.squid.flux_bias_phi0,
            "asymmetry": component.squid.asymmetry,
        }
    return data


def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "message": message}


def _is_symmetric(matrix: tuple[tuple[float, ...], ...], *, tolerance: float = 1e-12) -> bool:
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            if abs(value - matrix[j][i]) > tolerance:
                return False
    return True
