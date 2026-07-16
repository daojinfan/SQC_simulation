"""Pure Stage 5.1 closed-system numerical kernel (no worker or artifacts)."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from itertools import combinations
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from sqvm.device.capacitance import build_capacitance_matrix
from sqvm.device.junction import resolve_junction_parameters
from sqvm.device.spec import load_device
from sqvm.evolution.models import EffectiveScenario, RebuiltModel, Stage5Config, Stage5Input, Stage5PathAdmission
from sqvm.evolution.physics import (
    _build_frame, _lab_reference, _qutip, _static_hamiltonian,
    angular_rad_per_ns, evolve_stage5_scenario, zoh_edges,
)
from sqvm.evolution.stage51_authority import admit_physics_authority, fail, read_json
from sqvm.evolution.stage51_coefficients import (
    _verify_coefficient_payload, verify_evolution_coefficient_artifact,
)
from sqvm.evolution.stage51_models import (
    Stage51EvolutionInput, Stage51FailureCode, Stage51NumericalResult,
    Stage51PhysicsContext, VerifiedCoefficientHandle,
)
from sqvm.hamiltonian import BasisConfig, DeviceArtifacts, build_ec_matrix, build_mode_capacitance_matrix, build_mode_transform, load_hamiltonian_config


def _phase_fixed(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype="<c16").reshape(-1).copy()
    if value.size == 0 or not np.all(np.isfinite(value)):
        fail(Stage51FailureCode.INITIAL_STATE_INVALID, "state vector")
    index = int(np.flatnonzero(np.abs(value) == np.abs(value).max())[0])
    if value[index] == 0:
        fail(Stage51FailureCode.INITIAL_STATE_INVALID, "zero state")
    pivot = value[index]
    value *= np.conj(pivot) / abs(pivot)
    real = np.round(value.real, decimals=14)
    imag = np.round(value.imag, decimals=14)
    real[np.abs(real) < 5e-15] = 0.0
    imag[np.abs(imag) < 5e-15] = 0.0
    value = np.asarray(real + 1j * imag, dtype="<c16")
    value[index] = complex(round(abs(pivot), 14), 0.0)
    value.setflags(write=False)
    return value


def phase_invariant_overlap(left: np.ndarray, right: np.ndarray) -> float:
    left_value = np.asarray(left, dtype=np.complex128)
    right_value = np.asarray(right, dtype=np.complex128)
    if left_value.ndim != 1 or right_value.ndim != 1 or left_value.shape != right_value.shape or not np.all(np.isfinite(left_value)) or not np.all(np.isfinite(right_value)):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "overlap vectors")
    left_norm = float(np.vdot(left_value, left_value).real)
    right_norm = float(np.vdot(right_value, right_value).real)
    if left_norm <= 0.0 or right_norm <= 0.0:
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "zero overlap vector")
    result = float(abs(np.vdot(left_value, right_value)) ** 2 / (left_norm * right_norm))
    if not np.isfinite(result) or result < -1e-12 or result > 1.0 + 1e-12:
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "overlap outside bounds")
    return min(1.0, max(0.0, result))


def _arrays(handle: VerifiedCoefficientHandle, context: Stage51PhysicsContext) -> dict[str, np.ndarray]:
    verified = verify_evolution_coefficient_artifact(handle.artifact_root, context, handle.source_control_handle)
    fields = ("coefficient_plan_id", "artifact_root", "manifest_sha256", "receipt_sha256", "inventory_sha256", "physics_authority_id")
    if any(getattr(verified, name) != getattr(handle, name) for name in fields) or verified.source_control_handle is not handle.source_control_handle:
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "stale coefficient handle")
    inventory = read_json(handle.artifact_root / "array_inventory.json", Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED)
    result = {row["name"]: np.frombuffer((handle.artifact_root / row["path"]).read_bytes(), dtype=row["dtype"]).copy() for row in inventory["arrays"]}
    for value in result.values(): value.setflags(write=False)
    return result


def _build_stage5_input_from_controls(
    time_center_ns: np.ndarray,
    epsilon_q1: np.ndarray,
    epsilon_q2: np.ndarray,
    absolute_flux_phi0: Mapping[str, np.ndarray],
    frame: Mapping[str, float],
    authority: Mapping[str, Any],
    context: Stage51PhysicsContext,
) -> Stage5Input:
    if not isinstance(absolute_flux_phi0, Mapping) or set(absolute_flux_phi0) != {"q1", "c", "q2"}:
        fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, "absolute flux names")
    arrays = {
        "time_center_ns": (time_center_ns, "<f8"),
        "epsilon_q1": (epsilon_q1, "<c16"),
        "epsilon_q2": (epsilon_q2, "<c16"),
        **{f"absolute_flux_{name}": (absolute_flux_phi0.get(name), "<f8") for name in ("q1", "c", "q2")},
    }
    sample_count: int | None = None
    for name, (value, dtype) in arrays.items():
        if not isinstance(value, np.ndarray) or value.dtype != np.dtype(dtype) or value.ndim != 1 or value.size == 0 or not value.flags.c_contiguous or not np.all(np.isfinite(value)):
            fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, name)
        if sample_count is None:
            sample_count = int(value.size)
        elif value.size != sample_count:
            fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, "control lengths")
    expected_centers = time_center_ns[0] + np.arange(time_center_ns.size, dtype="<f8") * 0.5
    if not np.array_equal(time_center_ns, expected_centers):
        fail(Stage51FailureCode.CONTROL_CLOCK_MISMATCH, "signed centers")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"} or any(type(value) is not float or not np.isfinite(value) for value in frame.values()):
        fail(Stage51FailureCode.FRAME_AUTHORITY_MISMATCH, "coefficient frame")
    model = authority["model"]
    if model.get("tensor_order") != ["q1", "c", "q2"] or model.get("charge_cutoffs") != [1, 1, 1] or model.get("reference_state_count") != 16:
        fail(Stage51FailureCode.TENSOR_MAPPING_INVALID, "authority model")
    device = load_device(context.accepted_device_artifact); cap = build_capacitance_matrix(device)
    artifacts = DeviceArtifacts(context.accepted_device_artifact, {"capacitance_matrix": {"nodes": list(cap.nodes), "matrix_fF": [list(row) for row in cap.matrix_fF]}, "junction_parameters": [{"component": row.component, "junction": row.junction, "rn_ohm": row.rn_ohm, "ej_GHz": row.ej_GHz, "source": row.source} for row in resolve_junction_parameters(device).rows], "components": {name: {"squid": {"flux_bias_phi0": item.squid.flux_bias_phi0}} for name, item in device.components.items() if item.squid is not None}})
    ec = build_ec_matrix(build_mode_capacitance_matrix(artifacts, build_mode_transform(artifacts))).matrix_GHz
    config = Stage5Config(Path("stage51-authority"), "stage51", {}, ("stage51",), tuple(model["charge_cutoffs"]), None, None, None, model["reference_state_count"], authority["solver"], authority["tolerances"], {})
    scenario = EffectiveScenario("stage51", time_center_ns, {"q1": (epsilon_q1.real.astype("<f8"), epsilon_q1.imag.astype("<f8")), "q2": (epsilon_q2.real.astype("<f8"), epsilon_q2.imag.astype("<f8"))}, {name: absolute_flux_phi0[name] for name in ("q1", "c", "q2")}, dict(frame), {"q1": 0.0, "q2": 0.0})
    return Stage5Input(Stage5PathAdmission(context.repository_root, config, {}), {}, "stage51", {"stage51": scenario}, RebuiltModel(artifacts, replace(load_hamiltonian_config(context.accepted_hamiltonian_artifact), basis=BasisConfig(dict(zip(("q1", "c", "q2"), model["charge_cutoffs"], strict=True)))), ec))


def _build_stage51_input(coefficients: VerifiedCoefficientHandle, context: Stage51PhysicsContext) -> Stage5Input:
    if not isinstance(coefficients, VerifiedCoefficientHandle): fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "coefficient handle")
    authority, _ = admit_physics_authority(context)
    arrays = _arrays(coefficients, context)
    plan = read_json(coefficients.artifact_root / "coefficient_plan.json", Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED)
    return _build_stage5_input_from_controls(
        arrays["time_center_ns"], arrays["epsilon_q1"], arrays["epsilon_q2"],
        {"q1": arrays["absolute_flux_q1"], "c": arrays["absolute_flux_c"], "q2": arrays["absolute_flux_q2"]},
        plan.get("frame_reference_frequency_GHz"), authority, context,
    )


def _build_stage51_payload_input(artifact_root: Path, context: Stage51PhysicsContext) -> Stage5Input:
    plan, arrays, _ = _verify_coefficient_payload(artifact_root, context)
    authority, _ = admit_physics_authority(context)
    return _build_stage5_input_from_controls(
        arrays["time_center_ns"], arrays["epsilon_q1"], arrays["epsilon_q2"],
        {"q1": arrays["absolute_flux_q1"], "c": arrays["absolute_flux_c"], "q2": arrays["absolute_flux_q2"]},
        plan.get("frame_reference_frequency_GHz"), authority, context,
    )


def _projector_evidence(projectors: Mapping[str, Any], tolerance: float) -> tuple[Mapping[str, str], tuple[Mapping[str, Any], ...]]:
    labels = ("000", "100", "001", "101")
    if set(projectors) != set(labels) or not np.isfinite(tolerance) or tolerance <= 0.0:
        fail(Stage51FailureCode.OBSERVABLE_SPEC_INVALID, "projector labels or tolerance")
    matrices: dict[str, np.ndarray] = {}
    checks: list[Mapping[str, Any]] = []
    hashes: dict[str, str] = {}
    dimension: int | None = None
    for label in labels:
        source = projectors[label]
        matrix = np.asarray(source.full() if hasattr(source, "full") else source, dtype="<c16", order="C")
        if matrix.ndim != 2 or matrix.shape[0] != matrix.shape[1] or not np.all(np.isfinite(matrix)):
            fail(Stage51FailureCode.OBSERVABLE_SPEC_INVALID, f"projector {label}")
        if dimension is None:
            dimension = matrix.shape[0]
        if matrix.shape != (dimension, dimension):
            fail(Stage51FailureCode.OBSERVABLE_SPEC_INVALID, "projector dimensions")
        hermitian_error = float(np.linalg.norm(matrix - matrix.conj().T))
        idempotence_error = float(np.linalg.norm(matrix @ matrix - matrix))
        if hermitian_error > tolerance or idempotence_error > tolerance:
            fail(Stage51FailureCode.OBSERVABLE_SPEC_INVALID, f"projector {label} algebra")
        matrices[label] = matrix
        hashes[label] = hashlib.sha256(matrix.tobytes(order="C")).hexdigest().upper()
        checks.append(MappingProxyType({"name": f"projector_{label}_valid", "passed": True, "hermitian_error": hermitian_error, "idempotence_error": idempotence_error}))
    for left, right in combinations(labels, 2):
        error = float(np.linalg.norm(matrices[left] @ matrices[right]))
        if error > tolerance:
            fail(Stage51FailureCode.OBSERVABLE_SPEC_INVALID, f"projectors {left}/{right} overlap")
        checks.append(MappingProxyType({"name": f"projector_{left}_{right}_orthogonal", "passed": True, "error": error}))
    return MappingProxyType(hashes), tuple(checks)


def _physics_preflight(stage5: Stage5Input) -> tuple[Mapping[str, str], tuple[Mapping[str, Any], ...]]:
    scenario = stage5.scenarios["stage51"]
    qt = _qutip()
    frame = _build_frame(stage5, scenario, qt)
    dimension = frame["dimension"]
    operator_tolerance = 1.0e-10
    frame_generator = frame["f_generator"]
    for name, operator in {"frame_generator": frame_generator, **{f"drive_{key}_plus": value for key, value in frame["d_plus"].items()}}.items():
        matrix = np.asarray(operator.full(), dtype=np.complex128)
        if matrix.shape != (dimension, dimension) or not np.all(np.isfinite(matrix)):
            fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, name)
    frame_matrix = np.asarray(frame_generator.full(), dtype=np.complex128)
    if float(np.linalg.norm(frame_matrix - frame_matrix.conj().T)) > operator_tolerance:
        fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, "frame generator Hermiticity")
    for mode, drive_plus in frame["d_plus"].items():
        drive_minus = drive_plus.dag()
        plus = np.asarray(drive_plus.full(), dtype=np.complex128)
        minus = np.asarray(drive_minus.full(), dtype=np.complex128)
        if minus.shape != plus.shape or not np.all(np.isfinite(minus)) or not np.array_equal(minus, plus.conj().T):
            fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, f"drive {mode} adjoint")
    static_by_index: dict[int, Any] = {}
    seen_flux: dict[tuple[float, float, float], Any] = {}
    for index in range(scenario.time_center_ns.size):
        key = tuple(float(scenario.absolute_flux_phi0[name][index]) for name in ("q1", "c", "q2"))
        static = seen_flux.get(key)
        if static is None:
            static = _static_hamiltonian(stage5, scenario, index, qt)
            seen_flux[key] = static
        matrix = np.asarray(static.full(), dtype=np.complex128)
        if matrix.shape != (dimension, dimension) or not np.all(np.isfinite(matrix)) or float(np.linalg.norm(matrix - matrix.conj().T)) > operator_tolerance:
            fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, "static Hamiltonian")
        static_by_index[index] = static
    edges = zoh_edges(scenario.time_center_ns)
    drive_magnitude = np.asarray([
        abs(scenario.xy_iq_GHz["q1"][0][index] + 1j * scenario.xy_iq_GHz["q1"][1][index])
        + abs(scenario.xy_iq_GHz["q2"][0][index] + 1j * scenario.xy_iq_GHz["q2"][1][index])
        for index in range(scenario.time_center_ns.size)
    ])
    representative = {0, scenario.time_center_ns.size - 1, int(np.argmax(drive_magnitude))}
    identity = np.eye(dimension, dtype=np.complex128)

    def checked_unitary(time_ns: float):
        unitary = frame["u"](time_ns)
        matrix = np.asarray(unitary.full(), dtype=np.complex128)
        if not np.all(np.isfinite(matrix)) or float(np.linalg.norm(matrix.conj().T @ matrix - identity)) > operator_tolerance:
            fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, "frame unitary")
        return unitary

    checked_unitary(float(edges[0]))
    checked_unitary(float(edges[-1]))
    for index in sorted(representative):
        time_ns = float(edges[index])
        unitary = checked_unitary(time_ns)
        drive = sum((
            0.5 * (
                (scenario.xy_iq_GHz[mode][0][index] + 1j * scenario.xy_iq_GHz[mode][1][index]) * frame["d_plus"][mode]
                + (scenario.xy_iq_GHz[mode][0][index] - 1j * scenario.xy_iq_GHz[mode][1][index]) * frame["d_plus"][mode].dag()
            )
            for mode in ("q1", "q2")
        ), qt.Qobj(np.zeros((dimension, dimension), dtype=complex), dims=frame["dims"]))
        h_ip = unitary.dag() * static_by_index[index] * unitary - frame_generator + drive
        h_ip_matrix = np.asarray(h_ip.full(), dtype=np.complex128)
        if not np.all(np.isfinite(h_ip_matrix)) or float(np.linalg.norm(h_ip_matrix - h_ip_matrix.conj().T)) > operator_tolerance:
            fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, "interaction-picture Hamiltonian")
        angular = np.asarray(angular_rad_per_ns(h_ip).full(), dtype=np.complex128)
        if not np.array_equal(angular, angular_rad_per_ns(1.0) * h_ip_matrix):
            fail(Stage51FailureCode.ANGULAR_CONVERSION_VIOLATION, "interaction-picture Hamiltonian")
    reference = _lab_reference(stage5, scenario, frame, qt)
    lab_state = np.asarray(reference["psi_lab"].full(), dtype=np.complex128).reshape(-1)
    lab_pivot = int(np.flatnonzero(np.abs(lab_state) == np.abs(lab_state).max())[0])
    if lab_state[lab_pivot].real < 0.0 or abs(float(lab_state[lab_pivot].imag)) > 1.0e-14:
        fail(Stage51FailureCode.INITIAL_STATE_INVALID, "lab-ground phase")
    state = np.asarray(reference["psi_ip"].full(), dtype=np.complex128).reshape(-1)
    if not np.all(np.isfinite(lab_state)) or not np.all(np.isfinite(state)) or abs(float(np.vdot(lab_state, lab_state).real) - 1.0) > stage5.admission.config.tolerances["norm_error"] or abs(float(np.vdot(state, state).real) - 1.0) > stage5.admission.config.tolerances["norm_error"]:
        fail(Stage51FailureCode.INITIAL_STATE_INVALID, "interaction-picture initial state")
    projector_hashes, projector_checks = _projector_evidence(reference["projectors"], stage5.admission.config.tolerances["projector_orthogonality"])
    return projector_hashes, projector_checks


def run_stage51_physics_preflight(admitted: Stage51EvolutionInput, context: Stage51PhysicsContext) -> Mapping[str, Any]:
    if not isinstance(admitted, Stage51EvolutionInput):
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "admitted input")
    authority, _ = admit_physics_authority(context)
    stage5 = _build_stage5_input_from_controls(admitted.time_center_ns, admitted.epsilon_q1, admitted.epsilon_q2, admitted.absolute_flux_phi0, admitted.frame_reference_frequency_GHz, authority, context)
    hashes, checks = _physics_preflight(stage5)
    return MappingProxyType({"projector_sha256": hashes, "projector_checks": checks})


def _evolve_stage51_input(stage5: Stage5Input) -> Stage51NumericalResult:
    projector_hashes, projector_checks = _physics_preflight(stage5)
    result = evolve_stage5_scenario(stage5, "stage51")
    initial, final = _phase_fixed(result.states[0]), _phase_fixed(result.states[-1])
    populations = {name: np.asarray(values, dtype="<f8") for name, values in result.populations.items()}
    for value in (*populations.values(),): value.setflags(write=False)
    leakage, norm = np.asarray(result.leakage, dtype="<f8"), np.asarray(result.norm_error, dtype="<f8"); leakage.setflags(write=False); norm.setflags(write=False)
    edges = np.asarray(result.edge_time_ns, dtype="<f8"); edges.setflags(write=False)
    return Stage51NumericalResult(edges, initial, final, MappingProxyType(populations), leakage, norm, projector_hashes, MappingProxyType({"angular_conversion": angular_rad_per_ns.__name__, "checks": result.checks, "projector_checks": projector_checks, "projector_validation_pending": False}))


def run_stage51_worker_kernel(artifact_root: Path, context: Stage51PhysicsContext) -> Stage51NumericalResult:
    """Run from a verified coefficient payload without minting an upstream capability."""

    return _evolve_stage51_input(_build_stage51_payload_input(artifact_root, context))


def run_stage51_numerical_kernel(coefficients: VerifiedCoefficientHandle, context: Stage51PhysicsContext) -> Stage51NumericalResult:
    """Run the parent-process kernel after full control-capability verification."""

    if not isinstance(coefficients, VerifiedCoefficientHandle):
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "coefficient handle")
    verified = verify_evolution_coefficient_artifact(
        coefficients.artifact_root, context, coefficients.source_control_handle,
    )
    if verified.coefficient_plan_id != coefficients.coefficient_plan_id:
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "coefficient handle binding")
    return run_stage51_worker_kernel(verified.artifact_root, context)
