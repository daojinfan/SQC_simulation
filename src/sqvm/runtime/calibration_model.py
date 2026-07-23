"""Isolated QuTiP evolution for the local calibration-scan profile."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from types import MappingProxyType
from typing import Any, Mapping
import uuid
import warnings

import numpy as np
from scipy import linalg
from scipy.optimize import linear_sum_assignment

from sqvm.device import build_capacitance_matrix, load_device, resolve_junction_parameters
from sqvm.evolution.stage51_models import Stage51NumericalResult, VerifiedCoefficientHandle
from sqvm.evolution.stage51_worker import ARRAY_SPECS, _validate_worker_result
from sqvm.hamiltonian import (
    DeviceArtifacts,
    build_ec_matrix,
    build_mode_capacitance_matrix,
    build_mode_transform,
    load_hamiltonian_config,
    resolve_effective_junctions,
)
from sqvm.hamiltonian.basis import cos_phi_operator
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import inventory_tree_no_follow


PROTOCOL_VERSION = "0.1"
MODEL_AUTHORITY_PATH = "configs/runtime/calibration_scan/model_authority_v1.json"
REQUEST_NAME = "request.json"
METADATA_NAME = "worker_result.json"
INVENTORY_NAME = "array_inventory.json"
_COEFFICIENT_ARRAYS = {
    "time_edge_ns": ("<f8", "arrays/time_edge_ns.bin"),
    "epsilon_q1": ("<c16", "arrays/epsilon_q1.bin"),
    "epsilon_q2": ("<c16", "arrays/epsilon_q2.bin"),
    "absolute_flux_q1": ("<f8", "arrays/absolute_flux_q1.bin"),
    "absolute_flux_c": ("<f8", "arrays/absolute_flux_c.bin"),
    "absolute_flux_q2": ("<f8", "arrays/absolute_flux_q2.bin"),
}


class CalibrationModelError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _ProjectedChargeModel:
    charge_cutoffs: tuple[int, int, int]
    dimensions: tuple[int, int, int]
    energies_GHz: np.ndarray
    drive_operators: Mapping[str, np.ndarray]
    label_indexes: Mapping[str, int]
    label_overlaps: Mapping[str, float]
    transition_frequencies_GHz: Mapping[str, float]
    drive_matrix_elements: Mapping[str, float]


def load_calibration_model_authority(
    repository_root: str | Path,
) -> Mapping[str, Any]:
    root = Path(repository_root).resolve(strict=True)
    path = root / MODEL_AUTHORITY_PATH
    value = _canonical(path)
    expected = {
        "schema_version",
        "artifact_type",
        "artifact_version",
        "status",
        "model_authority_id",
        "model",
        "clock",
        "solver",
        "tolerances",
        "claim",
        "sources",
    }
    if (
        set(value) != expected
        or value.get("schema_version") != PROTOCOL_VERSION
        or value.get("artifact_type") != "stage_07_calibration_model_authority"
        or value.get("artifact_version") != PROTOCOL_VERSION
        or value.get("status") != "local_simulation_approved"
    ):
        raise CalibrationModelError("calibration model authority schema is invalid")
    identity = {key: item for key, item in value.items() if key != "model_authority_id"}
    if value.get("model_authority_id") != _sha(identity):
        raise CalibrationModelError("calibration model authority identity is invalid")
    _validate_model(value)
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CalibrationModelError("calibration model sources are invalid")
    for row in sources:
        if not isinstance(row, Mapping) or set(row) != {"path", "raw_sha256"}:
            raise CalibrationModelError("calibration model source binding is invalid")
        source = (root / str(row["path"])).resolve(strict=True)
        _inside(source, root, "model source")
        if raw_file_sha256(source) != row["raw_sha256"]:
            raise CalibrationModelError(f"calibration model source changed: {row['path']}")
    return MappingProxyType(value)


def model_configuration_from_authority(
    authority: Mapping[str, Any],
) -> Mapping[str, Any]:
    model = authority["model"]
    order = ("q1", "c", "q2")
    return MappingProxyType(
        {
            field: MappingProxyType(dict(zip(order, model[field], strict=True)))
            for field in (
                "charge_cutoffs",
                "retained_energy_levels",
                "convergence_charge_cutoffs",
                "convergence_retained_energy_levels",
            )
        }
    )


def resolve_calibration_model_authority(
    repository_root: str | Path,
    model_configuration: Mapping[str, Any] | None = None,
    *,
    idle_flux_phi0: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    base = load_calibration_model_authority(repository_root)
    normalized = _normalize_model_configuration(
        model_configuration
        if model_configuration is not None
        else model_configuration_from_authority(base)
    )
    value = copy.deepcopy(dict(base))
    for field, values in normalized.items():
        value["model"][field] = [values[mode] for mode in ("q1", "c", "q2")]
    if idle_flux_phi0 is not None:
        value["model"]["idle_flux_phi0"] = _normalize_idle_flux(idle_flux_phi0)
    identity = {key: item for key, item in value.items() if key != "model_authority_id"}
    value["model_authority_id"] = _sha(identity)
    _validate_model(value)
    return MappingProxyType(value)


def _normalize_idle_flux(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"q1", "c", "q2"}:
        raise CalibrationModelError("calibration idle flux configuration is invalid")
    normalized: dict[str, float] = {}
    for mode in ("q1", "c", "q2"):
        item = value[mode]
        if (
            isinstance(item, bool)
            or not isinstance(item, int | float)
            or not math.isfinite(float(item))
        ):
            raise CalibrationModelError(
                f"calibration idle flux configuration is invalid: {mode}"
            )
        normalized[mode] = float(item)
    return normalized


def calibration_model_configuration_sha256(value: Mapping[str, Any]) -> str:
    return _sha(_normalize_model_configuration(value))


def _normalize_model_configuration(value: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    fields = {
        "charge_cutoffs",
        "retained_energy_levels",
        "convergence_charge_cutoffs",
        "convergence_retained_energy_levels",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CalibrationModelError("calibration model configuration fields are invalid")
    normalized: dict[str, dict[str, int]] = {}
    for field in sorted(fields):
        row = value[field]
        if not isinstance(row, Mapping) or set(row) != {"q1", "c", "q2"}:
            raise CalibrationModelError(f"calibration model configuration is invalid: {field}")
        normalized[field] = {}
        for mode in ("q1", "c", "q2"):
            item = row[mode]
            if type(item) is not int:
                raise CalibrationModelError(f"calibration model configuration must be integer: {field}.{mode}")
            normalized[field][mode] = item
    return normalized


def execute_calibration_model_worker(
    coefficients: VerifiedCoefficientHandle,
    repository_root: str | Path,
    *,
    timeout_s: float,
    model_configuration: Mapping[str, Any] | None = None,
    idle_flux_phi0: Mapping[str, Any] | None = None,
) -> Stage51NumericalResult:
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int | float)
        or not math.isfinite(float(timeout_s))
        or float(timeout_s) <= 0.0
    ):
        raise CalibrationModelError("worker timeout is invalid")
    if not isinstance(coefficients, VerifiedCoefficientHandle):
        raise CalibrationModelError("verified coefficient handle is required")
    root = Path(repository_root).resolve(strict=True)
    authority = resolve_calibration_model_authority(
        root,
        model_configuration,
        idle_flux_phi0=idle_flux_phi0,
    )
    resolved_configuration = model_configuration_from_authority(authority)
    output_root = coefficients.artifact_root.parent.resolve()
    session = output_root / f".calibration-worker.{uuid.uuid4().hex}"
    request_path = session / REQUEST_NAME
    worker_output = session / "result"
    session.mkdir()
    request = {
        "schema_version": PROTOCOL_VERSION,
        "repository_root": str(root),
        "coefficient_artifact": str(coefficients.artifact_root.resolve(strict=True)),
        "model_authority": str((root / MODEL_AUTHORITY_PATH).resolve(strict=True)),
        "model_configuration": _plain(resolved_configuration),
        "idle_flux_phi0": _plain(authority["model"]["idle_flux_phi0"]),
        "output_dir": str(worker_output.resolve()),
    }
    request_path.write_bytes(canonical_json_bytes(request))
    command = [sys.executable, "-m", "sqvm.runtime.calibration_model", str(request_path)]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str((root / "src").resolve())
    try:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=float(timeout_s),
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise CalibrationModelError(f"calibration worker timeout: {timeout_s}s") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "worker failed").strip()[-1000:]
            raise CalibrationModelError(f"calibration worker failed: {detail}")
        return _read_worker_result(worker_output, authority)
    finally:
        if session.exists():
            shutil.rmtree(session, ignore_errors=True)


def run_projected_charge_model(
    coefficient_artifact: Path,
    authority: Mapping[str, Any],
    repository_root: Path,
) -> Stage51NumericalResult:
    arrays, plan = _read_coefficients(coefficient_artifact)
    edges = arrays["time_edge_ns"]
    epsilon_q1 = arrays["epsilon_q1"]
    epsilon_q2 = arrays["epsilon_q2"]
    if edges.size != epsilon_q1.size + 1 or epsilon_q1.shape != epsilon_q2.shape:
        raise CalibrationModelError("coefficient clock is inconsistent")
    model = authority["model"]
    idle = model["idle_flux_phi0"]
    for name in ("q1", "c", "q2"):
        values = arrays[f"absolute_flux_{name}"]
        if values.shape != epsilon_q1.shape or not np.all(values == float(idle[name])):
            raise CalibrationModelError(
                "projected calibration model currently admits idle-flux XY experiments only"
            )
    frame = plan.get("frame_reference_frequency_GHz")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"}:
        raise CalibrationModelError("coefficient frame authority is invalid")

    baseline = _build_projected_charge_model(
        repository_root,
        model,
        tuple(model["charge_cutoffs"]),
        tuple(model["retained_energy_levels"]),
    )
    convergence = _build_projected_charge_model(
        repository_root,
        model,
        tuple(model["convergence_charge_cutoffs"]),
        tuple(model["convergence_retained_energy_levels"]),
    )
    convergence_evidence = _validate_projection_convergence(
        baseline,
        convergence,
        authority["tolerances"],
    )

    import qutip as qt

    if not str(qt.__version__).startswith(("5.1", "5.2", "5.3")):
        raise CalibrationModelError(f"unsupported QuTiP version: {qt.__version__}")
    dimension = int(np.prod(baseline.dimensions))
    occupations = np.asarray(
        list(np.ndindex(baseline.dimensions)),
        dtype=float,
    )
    rotating_energy = (
        float(frame["q1"]) * occupations[:, 0]
        + float(frame["q2"]) * occupations[:, 2]
    )
    h0 = qt.Qobj(
        np.diag(baseline.energies_GHz - baseline.energies_GHz[0] - rotating_energy),
    )
    d_plus = {
        mode: qt.Qobj(np.asarray(baseline.drive_operators[mode], dtype=complex))
        for mode in ("q1", "q2")
    }
    starts = edges[:-1]
    if not np.array_equal(
        np.diff(edges),
        np.full(edges.size - 1, float(authority["clock"]["dt_ns"]), dtype="<f8"),
    ):
        raise CalibrationModelError("coefficient clock differs from model authority")

    def hamiltonian(time_ns: float, _args=None):
        index = min(max(int(np.searchsorted(starts, float(time_ns), side="right") - 1), 0), epsilon_q1.size - 1)
        drive = 0.5 * (
            epsilon_q1[index] * d_plus["q1"]
            + np.conj(epsilon_q1[index]) * d_plus["q1"].dag()
            + epsilon_q2[index] * d_plus["q2"]
            + np.conj(epsilon_q2[index]) * d_plus["q2"].dag()
        )
        return 2.0 * np.pi * (h0 + drive)

    initial = qt.basis(dimension, baseline.label_indexes["000"])
    projectors = {
        label: qt.basis(dimension, index).proj()
        for label, index in baseline.label_indexes.items()
    }
    solver = authority["solver"]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = qt.sesolve(
            qt.QobjEvo(hamiltonian),
            initial,
            edges,
            e_ops=list(projectors.values()),
            options={
                "method": solver["method"],
                "rtol": solver["rtol"],
                "atol": solver["atol"],
                "nsteps": solver["nsteps"],
                "max_step": solver["max_step_ns"],
                "store_states": True,
                "store_final_state": True,
                "normalize_output": solver["normalize_output"],
                "progress_bar": None,
            },
        )
    if caught:
        raise CalibrationModelError(f"QuTiP solver warning: {caught[0].message}")
    states = tuple(np.asarray(state.full(), dtype="<c16").reshape(-1) for state in result.states)
    populations = {
        label: np.asarray(result.expect[index], dtype="<f8")
        for index, label in enumerate(projectors)
    }
    norm = np.asarray([float(np.vdot(state, state).real) for state in states], dtype="<f8")
    computational = sum(populations.values())
    projector_sha256 = {
        label: hashlib.sha256(np.asarray(value.full(), dtype="<c16").tobytes()).hexdigest().upper()
        for label, value in projectors.items()
    }
    numerical = Stage51NumericalResult(
        np.asarray(edges, dtype="<f8"),
        states[0],
        states[-1],
        MappingProxyType(populations),
        np.asarray(1.0 - computational, dtype="<f8"),
        np.asarray(np.abs(norm - 1.0), dtype="<f8"),
        MappingProxyType(projector_sha256),
        MappingProxyType(
            {
                "engine_id": model["model_id"],
                "model_authority_id": authority["model_authority_id"],
                "model_configuration_sha256": calibration_model_configuration_sha256(
                    model_configuration_from_authority(authority)
                ),
                "charge_cutoffs": list(model["charge_cutoffs"]),
                "retained_energy_levels": list(model["retained_energy_levels"]),
                "hilbert_dimension": dimension,
                "transition_frequencies_GHz": dict(baseline.transition_frequencies_GHz),
                "label_overlaps": dict(baseline.label_overlaps),
                "projection_convergence": convergence_evidence,
                "qutip_version": str(qt.__version__),
                "solver_spec": dict(solver),
            }
        ),
    )
    validate_calibration_model_result(numerical, authority)
    return numerical


def _build_projected_charge_model(
    repository_root: Path,
    model: Mapping[str, Any],
    charge_cutoffs: tuple[int, int, int],
    retained_levels: tuple[int, int, int],
) -> _ProjectedChargeModel:
    root = Path(repository_root).resolve(strict=True)
    device_path = root / "configs/devices/2q1c2r.yaml"
    hamiltonian_path = root / "configs/hamiltonians/2q1c_charge_basis.yaml"
    device = load_device(device_path)
    capacitance = build_capacitance_matrix(device)
    junction_table = resolve_junction_parameters(device)
    device_artifacts = DeviceArtifacts(
        device_path,
        {
            "capacitance_matrix": {
                "nodes": list(capacitance.nodes),
                "matrix_fF": [list(row) for row in capacitance.matrix_fF],
            },
            "junction_parameters": [
                {
                    "component": row.component,
                    "junction": row.junction,
                    "rn_ohm": row.rn_ohm,
                    "ej_GHz": row.ej_GHz,
                    "source": row.source,
                }
                for row in junction_table.rows
            ],
            "components": {
                name: {"squid": {"flux_bias_phi0": component.squid.flux_bias_phi0}}
                for name, component in device.components.items()
                if component.squid is not None
            },
        },
    )
    configured = load_hamiltonian_config(hamiltonian_path)
    configured_cutoffs = tuple(
        configured.basis.charge_cutoffs[mode] for mode in ("q1", "c", "q2")
    )
    if charge_cutoffs == tuple(model["charge_cutoffs"]) and configured_cutoffs != charge_cutoffs:
        raise CalibrationModelError("calibration charge cutoffs differ from Hamiltonian config")
    ec = np.asarray(
        build_ec_matrix(
            build_mode_capacitance_matrix(
                device_artifacts,
                build_mode_transform(device_artifacts),
            )
        ).matrix_GHz,
        dtype=float,
    )
    junctions = {
        row.mode: row.ej_effective_GHz
        for row in resolve_effective_junctions(
            device_artifacts,
            model["idle_flux_phi0"],
        )
    }
    local_energies: list[np.ndarray] = []
    local_charge: list[np.ndarray] = []
    for index, mode in enumerate(("q1", "c", "q2")):
        cutoff = charge_cutoffs[index]
        level_count = retained_levels[index]
        charges = np.arange(-cutoff, cutoff + 1, dtype=float)
        local_hamiltonian = (
            np.diag(4.0 * ec[index, index] * charges**2)
            - junctions[mode] * cos_phi_operator(charges.size, sparse_output=False)
        )
        values, vectors = linalg.eigh(local_hamiltonian)
        selected = vectors[:, :level_count]
        local_energies.append(values[:level_count])
        local_charge.append(selected.conj().T @ np.diag(charges) @ selected)

    dimensions = tuple(int(value) for value in retained_levels)

    def embed(operator: np.ndarray, target: int) -> np.ndarray:
        result = np.asarray([[1.0]], dtype=complex)
        for index, size in enumerate(dimensions):
            local = operator if index == target else np.eye(size, dtype=complex)
            result = np.kron(result, local)
        return result

    dimension = int(np.prod(dimensions))
    hamiltonian = np.zeros((dimension, dimension), dtype=complex)
    for index in range(3):
        hamiltonian += embed(np.diag(local_energies[index]), index)
    embedded_charge = [embed(local_charge[index], index) for index in range(3)]
    for left in range(3):
        for right in range(left + 1, 3):
            hamiltonian += (
                8.0
                * ec[left, right]
                * (embedded_charge[left] @ embedded_charge[right])
            )
    values, vectors = linalg.eigh(hamiltonian)
    bare_rows, eigen_columns = linear_sum_assignment(-np.abs(vectors) ** 2)
    bare_to_eigen = np.empty(dimension, dtype=int)
    bare_to_eigen[bare_rows] = eigen_columns
    ordered_vectors = vectors[:, bare_to_eigen]
    ordered_energies = np.asarray(values[bare_to_eigen], dtype=float)
    overlaps = np.abs(ordered_vectors[np.arange(dimension), np.arange(dimension)]) ** 2

    drive_operators: dict[str, np.ndarray] = {}
    for mode, target in (("q1", 0), ("q2", 2)):
        local = np.zeros_like(local_charge[target], dtype=complex)
        for level in range(dimensions[target] - 1):
            local[level + 1, level] = local_charge[target][level + 1, level]
        dressed = ordered_vectors.conj().T @ embed(local, target) @ ordered_vectors
        mask = np.zeros_like(dressed, dtype=bool)
        for source_tuple in np.ndindex(dimensions):
            if source_tuple[target] + 1 >= dimensions[target]:
                continue
            destination = list(source_tuple)
            destination[target] += 1
            source_index = np.ravel_multi_index(source_tuple, dimensions)
            destination_index = np.ravel_multi_index(tuple(destination), dimensions)
            mask[destination_index, source_index] = True
        drive_operators[mode] = np.where(mask, dressed, 0.0)

    indexes = {
        "000": int(np.ravel_multi_index((0, 0, 0), dimensions)),
        "100": int(np.ravel_multi_index((1, 0, 0), dimensions)),
        "001": int(np.ravel_multi_index((0, 0, 1), dimensions)),
        "101": int(np.ravel_multi_index((1, 0, 1), dimensions)),
    }
    ground = ordered_energies[indexes["000"]]
    frequencies = {
        "q1": float(ordered_energies[indexes["100"]] - ground),
        "q2": float(ordered_energies[indexes["001"]] - ground),
    }
    matrix_elements = {
        "q1": float(abs(drive_operators["q1"][indexes["100"], indexes["000"]])),
        "q2": float(abs(drive_operators["q2"][indexes["001"], indexes["000"]])),
    }
    return _ProjectedChargeModel(
        charge_cutoffs,
        dimensions,
        ordered_energies,
        MappingProxyType(drive_operators),
        MappingProxyType(indexes),
        MappingProxyType({label: float(overlaps[index]) for label, index in indexes.items()}),
        MappingProxyType(frequencies),
        MappingProxyType(matrix_elements),
    )


def _validate_projection_convergence(
    baseline: _ProjectedChargeModel,
    comparison: _ProjectedChargeModel,
    tolerances: Mapping[str, float],
) -> dict[str, Any]:
    frequency_drift = {
        mode: abs(
            baseline.transition_frequencies_GHz[mode]
            - comparison.transition_frequencies_GHz[mode]
        )
        for mode in ("q1", "q2")
    }
    drive_drift = {
        mode: abs(
            baseline.drive_matrix_elements[mode]
            - comparison.drive_matrix_elements[mode]
        )
        for mode in ("q1", "q2")
    }
    if max(frequency_drift.values()) > float(tolerances["projection_f01_drift_GHz"]):
        raise CalibrationModelError("projected-model f01 convergence failed")
    if max(drive_drift.values()) > float(tolerances["projection_drive_drift"]):
        raise CalibrationModelError("projected-model drive convergence failed")
    if min(baseline.label_overlaps.values()) < float(tolerances["label_min_overlap"]):
        raise CalibrationModelError("projected-model computational label overlap failed")
    return {
        "baseline_charge_cutoffs": list(baseline.charge_cutoffs),
        "baseline_retained_energy_levels": list(baseline.dimensions),
        "comparison_charge_cutoffs": list(comparison.charge_cutoffs),
        "comparison_retained_energy_levels": list(comparison.dimensions),
        "frequency_drift_GHz": frequency_drift,
        "drive_matrix_element_drift": drive_drift,
        "passed": True,
    }


def validate_calibration_model_result(
    result: Stage51NumericalResult,
    authority: Mapping[str, Any],
) -> None:
    _validate_worker_result(result, authority["tolerances"])
    model = authority["model"]
    diagnostics = result.diagnostics
    expected_dimension = int(np.prod(model["retained_energy_levels"]))
    if (
        result.initial_state.size != expected_dimension
        or result.final_state.size != expected_dimension
        or diagnostics.get("engine_id") != model["model_id"]
        or diagnostics.get("model_authority_id") != authority["model_authority_id"]
        or diagnostics.get("model_configuration_sha256")
        != calibration_model_configuration_sha256(model_configuration_from_authority(authority))
        or diagnostics.get("charge_cutoffs") != model["charge_cutoffs"]
        or diagnostics.get("retained_energy_levels")
        != model["retained_energy_levels"]
        or diagnostics.get("hilbert_dimension") != expected_dimension
        or diagnostics.get("solver_spec") != dict(authority["solver"])
    ):
        raise CalibrationModelError("calibration result model binding is invalid")
    frequencies = diagnostics.get("transition_frequencies_GHz")
    overlaps = diagnostics.get("label_overlaps")
    convergence = diagnostics.get("projection_convergence")
    if (
        not isinstance(frequencies, Mapping)
        or set(frequencies) != {"q1", "q2"}
        or any(not _finite_positive(value) for value in frequencies.values())
        or not isinstance(overlaps, Mapping)
        or set(overlaps) != {"000", "100", "001", "101"}
        or any(
            not _finite_number(value)
            or not float(authority["tolerances"]["label_min_overlap"])
            <= float(value)
            <= 1.0
            for value in overlaps.values()
        )
        or not isinstance(convergence, Mapping)
        or convergence.get("baseline_charge_cutoffs") != model["charge_cutoffs"]
        or convergence.get("baseline_retained_energy_levels")
        != model["retained_energy_levels"]
        or convergence.get("comparison_charge_cutoffs")
        != model["convergence_charge_cutoffs"]
        or convergence.get("comparison_retained_energy_levels")
        != model["convergence_retained_energy_levels"]
        or convergence.get("passed") is not True
    ):
        raise CalibrationModelError("calibration result projection evidence is invalid")
    frequency_drift = convergence.get("frequency_drift_GHz")
    drive_drift = convergence.get("drive_matrix_element_drift")
    if (
        not isinstance(frequency_drift, Mapping)
        or set(frequency_drift) != {"q1", "q2"}
        or any(
            not _finite_nonnegative(value)
            or float(value)
            > float(authority["tolerances"]["projection_f01_drift_GHz"])
            for value in frequency_drift.values()
        )
        or not isinstance(drive_drift, Mapping)
        or set(drive_drift) != {"q1", "q2"}
        or any(
            not _finite_nonnegative(value)
            or float(value)
            > float(authority["tolerances"]["projection_drive_drift"])
            for value in drive_drift.values()
        )
    ):
        raise CalibrationModelError("calibration result convergence metrics are invalid")


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(float(value))
    )


def _finite_positive(value: Any) -> bool:
    return _finite_number(value) and float(value) > 0.0


def _finite_nonnegative(value: Any) -> bool:
    return _finite_number(value) and float(value) >= 0.0


def _write_worker_result(
    output: Path,
    result: Stage51NumericalResult,
    authority: Mapping[str, Any],
    elapsed_s: float,
) -> None:
    output.mkdir()
    arrays = {
        "edge_time_ns": result.edge_time_ns,
        "initial_state": result.initial_state,
        "final_state": result.final_state,
        **{f"population_{label}": result.populations[label] for label in ("000", "100", "001", "101")},
        "leakage": result.leakage,
        "norm_error": result.norm_error,
    }
    rows = []
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        array = np.asarray(arrays[name], dtype=dtype, order="C")
        raw = array.tobytes(order="C")
        path = output / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        rows.append(
            {
                "name": name,
                "path": relative,
                "dtype": dtype,
                "shape": [int(array.size)],
                "element_count": int(array.size),
                "byte_count": len(raw),
                "unit": unit,
                "sha256": hashlib.sha256(raw).hexdigest().upper(),
            }
        )
    inventory = {
        "schema_version": PROTOCOL_VERSION,
        "artifact_type": "stage_07_calibration_worker_array_inventory",
        "arrays": sorted(rows, key=lambda row: row["name"]),
    }
    metadata = {
        "schema_version": PROTOCOL_VERSION,
        "artifact_type": "stage_07_calibration_worker_result",
        "status": "complete",
        "worker_identity": {
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "qutip_version": result.diagnostics["qutip_version"],
        },
        "model_authority_id": authority["model_authority_id"],
        "runtime_s": float(elapsed_s),
        "projector_sha256": dict(result.projector_sha256),
        "diagnostics": dict(result.diagnostics),
    }
    (output / INVENTORY_NAME).write_bytes(canonical_json_bytes(inventory))
    (output / METADATA_NAME).write_bytes(canonical_json_bytes(metadata))


def _read_worker_result(
    output: Path,
    authority: Mapping[str, Any],
) -> Stage51NumericalResult:
    expected = {METADATA_NAME, INVENTORY_NAME, *{spec[2] for spec in ARRAY_SPECS.values()}}
    rows = inventory_tree_no_follow(output)
    if {row["path"] for row in rows if row.get("entry_type") == "file"} != expected:
        raise CalibrationModelError("calibration worker file set is invalid")
    metadata, inventory = _canonical(output / METADATA_NAME), _canonical(output / INVENTORY_NAME)
    if (
        set(metadata)
        != {
            "schema_version",
            "artifact_type",
            "status",
            "worker_identity",
            "model_authority_id",
            "runtime_s",
            "projector_sha256",
            "diagnostics",
        }
        or metadata.get("artifact_type") != "stage_07_calibration_worker_result"
        or metadata.get("status") != "complete"
        or metadata.get("model_authority_id") != authority["model_authority_id"]
    ):
        raise CalibrationModelError("calibration worker metadata is invalid")
    rows = inventory.get("arrays")
    if (
        set(inventory) != {"schema_version", "artifact_type", "arrays"}
        or inventory.get("artifact_type") != "stage_07_calibration_worker_array_inventory"
        or not isinstance(rows, list)
        or len(rows) != len(ARRAY_SPECS)
    ):
        raise CalibrationModelError("calibration worker inventory is invalid")
    by_name = {row.get("name"): row for row in rows if isinstance(row, Mapping)}
    arrays: dict[str, np.ndarray] = {}
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        row = by_name.get(name)
        raw = (output / relative).read_bytes()
        if (
            not isinstance(row, Mapping)
            or row.get("path") != relative
            or row.get("dtype") != dtype
            or row.get("unit") != unit
            or row.get("byte_count") != len(raw)
            or row.get("sha256") != hashlib.sha256(raw).hexdigest().upper()
        ):
            raise CalibrationModelError(f"calibration worker array is invalid: {name}")
        array = np.frombuffer(raw, dtype=dtype).copy(order="C")
        array.setflags(write=False)
        arrays[name] = array
    diagnostics = {
        **metadata["diagnostics"],
        "worker_identity": metadata["worker_identity"],
        "runtime_s": metadata["runtime_s"],
    }
    result = Stage51NumericalResult(
        arrays["edge_time_ns"],
        arrays["initial_state"],
        arrays["final_state"],
        MappingProxyType({label: arrays[f"population_{label}"] for label in ("000", "100", "001", "101")}),
        arrays["leakage"],
        arrays["norm_error"],
        MappingProxyType(metadata["projector_sha256"]),
        MappingProxyType(diagnostics),
    )
    validate_calibration_model_result(result, authority)
    return result


def _read_coefficients(artifact: Path) -> tuple[dict[str, np.ndarray], Mapping[str, Any]]:
    inventory = _canonical(artifact / INVENTORY_NAME)
    plan = _canonical(artifact / "coefficient_plan.json")
    rows = inventory.get("arrays")
    if not isinstance(rows, list):
        raise CalibrationModelError("coefficient inventory is invalid")
    by_name = {row.get("name"): row for row in rows if isinstance(row, Mapping)}
    arrays = {}
    for name, (dtype, relative) in _COEFFICIENT_ARRAYS.items():
        row = by_name.get(name)
        raw = (artifact / relative).read_bytes()
        if (
            not isinstance(row, Mapping)
            or row.get("path") != relative
            or row.get("dtype") != dtype
            or row.get("sha256") != hashlib.sha256(raw).hexdigest().upper()
        ):
            raise CalibrationModelError(f"coefficient array is invalid: {name}")
        arrays[name] = np.frombuffer(raw, dtype=dtype).copy(order="C")
    return arrays, plan


def _validate_model(authority: Mapping[str, Any]) -> None:
    model = authority.get("model")
    solver = authority.get("solver")
    tolerances = authority.get("tolerances")
    claim = authority.get("claim")
    expected_model_keys = {
        "model_id",
        "tensor_order",
        "charge_cutoffs",
        "convergence_charge_cutoffs",
        "retained_energy_levels",
        "convergence_retained_energy_levels",
        "drive_operator",
        "idle_flux_phi0",
    }
    expected_solver_keys = {
        "qutip_version_spec",
        "method",
        "rtol",
        "atol",
        "nsteps",
        "max_step_ns",
        "normalize_output",
    }
    if (
        not isinstance(model, Mapping)
        or set(model) != expected_model_keys
        or model.get("model_id") != "projected_charge_basis_2q1c_v1"
        or model.get("tensor_order") != ["q1", "c", "q2"]
        or model.get("drive_operator") != "nearest_neighbor_charge_rwa_v1"
        or not isinstance(solver, Mapping)
        or set(solver) != expected_solver_keys
        or not isinstance(tolerances, Mapping)
        or set(tolerances)
        != {
            "norm_error",
            "population_bound",
            "label_min_overlap",
            "projection_f01_drift_GHz",
            "projection_drive_drift",
        }
        or authority.get("clock") != {"dt_ns": 0.5}
        or solver.get("qutip_version_spec") != ">=5.1,<5.4"
        or solver.get("method") != "vern9"
        or solver.get("normalize_output") is not False
        or type(solver.get("nsteps")) is not int
        or solver["nsteps"] <= 0
        or claim
        != {
            "formal_scale_qualified": False,
            "hardware_measurement": False,
            "scope": "local_simulator_calibration_only",
        }
    ):
        raise CalibrationModelError("calibration model contract is invalid")
    for field in (
        "charge_cutoffs",
        "retained_energy_levels",
        "convergence_charge_cutoffs",
        "convergence_retained_energy_levels",
    ):
        values = model.get(field)
        if (
            not isinstance(values, list)
            or len(values) != 3
            or any(type(item) is not int or not 1 <= item <= 16 for item in values)
        ):
            raise CalibrationModelError(f"calibration model truncation is invalid: {field}")
    cutoffs = model["charge_cutoffs"]
    levels = model["retained_energy_levels"]
    comparison_cutoffs = model["convergence_charge_cutoffs"]
    comparison_levels = model["convergence_retained_energy_levels"]
    if (
        any(levels[index] > 2 * cutoffs[index] + 1 for index in range(3))
        or any(comparison_cutoffs[index] < cutoffs[index] for index in range(3))
        or any(
            comparison_levels[index] < levels[index]
            or comparison_levels[index] > 2 * comparison_cutoffs[index] + 1
            for index in range(3)
        )
        or math.prod(levels) > 512
        or math.prod(comparison_levels) > 512
        or (cutoffs == comparison_cutoffs and levels == comparison_levels)
    ):
        raise CalibrationModelError("calibration model truncation relationship is invalid")
    numeric = [
        authority.get("clock", {}).get("dt_ns"),
        solver.get("rtol"),
        solver.get("atol"),
        solver.get("max_step_ns"),
        tolerances.get("norm_error"),
        tolerances.get("population_bound"),
        tolerances.get("label_min_overlap"),
        tolerances.get("projection_f01_drift_GHz"),
        tolerances.get("projection_drive_drift"),
    ]
    idle = model.get("idle_flux_phi0")
    if not isinstance(idle, Mapping) or set(idle) != {"q1", "c", "q2"}:
        raise CalibrationModelError("calibration idle flux is invalid")
    numeric.extend(idle.values())
    if any(isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)) for value in numeric):
        raise CalibrationModelError("calibration model numbers are invalid")
    if (
        float(solver["rtol"]) <= 0.0
        or float(solver["atol"]) <= 0.0
        or float(solver["max_step_ns"]) <= 0.0
        or float(tolerances["norm_error"]) <= 0.0
        or float(tolerances["population_bound"]) < 0.0
        or not 0.0 < float(tolerances["label_min_overlap"]) <= 1.0
        or float(tolerances["projection_f01_drift_GHz"]) <= 0.0
        or float(tolerances["projection_drive_drift"]) <= 0.0
    ):
        raise CalibrationModelError("calibration solver numbers are invalid")


def _worker_main(request_path: Path) -> int:
    request = _canonical(request_path)
    if (
        set(request)
        != {
            "schema_version",
            "repository_root",
            "coefficient_artifact",
            "model_authority",
            "model_configuration",
            "idle_flux_phi0",
            "output_dir",
        }
        or request.get("schema_version") != PROTOCOL_VERSION
    ):
        raise CalibrationModelError("calibration worker request is invalid")
    root = Path(request["repository_root"]).resolve(strict=True)
    coefficient = Path(request["coefficient_artifact"]).resolve(strict=True)
    authority_path = Path(request["model_authority"]).resolve(strict=True)
    output = Path(request["output_dir"]).resolve()
    _inside(coefficient, root, "coefficient artifact")
    _inside(authority_path, root, "model authority")
    _inside(output, root, "worker output")
    if authority_path != root / MODEL_AUTHORITY_PATH or output.exists():
        raise CalibrationModelError("calibration worker path binding is invalid")
    authority = resolve_calibration_model_authority(
        root,
        request["model_configuration"],
        idle_flux_phi0=request["idle_flux_phi0"],
    )
    started = time.perf_counter()
    result = run_projected_charge_model(coefficient, authority, root)
    _write_worker_result(output, result, authority, time.perf_counter() - started)
    return 0


def _canonical(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationModelError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise CalibrationModelError(f"noncanonical JSON: {path.name}")
    return value


def _inside(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CalibrationModelError(f"{label} is outside repository") from exc


def _sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    try:
        return _worker_main(args.request)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CalibrationModelError",
    "MODEL_AUTHORITY_PATH",
    "execute_calibration_model_worker",
    "calibration_model_configuration_sha256",
    "load_calibration_model_authority",
    "model_configuration_from_authority",
    "resolve_calibration_model_authority",
    "run_projected_charge_model",
    "validate_calibration_model_result",
]
