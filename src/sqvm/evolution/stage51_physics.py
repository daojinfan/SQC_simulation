"""Pure Stage 5.1 closed-system numerical kernel (no worker or artifacts)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from sqvm.device.capacitance import build_capacitance_matrix
from sqvm.device.junction import resolve_junction_parameters
from sqvm.device.spec import load_device
from sqvm.evolution.models import EffectiveScenario, RebuiltModel, Stage5Config, Stage5Input, Stage5PathAdmission
from sqvm.evolution.physics import angular_rad_per_ns, evolve_stage5_scenario
from sqvm.evolution.stage51_authority import admit_physics_authority, fail, read_json
from sqvm.evolution.stage51_coefficients import verify_evolution_coefficient_artifact
from sqvm.evolution.stage51_models import (
    Stage51FailureCode, Stage51NumericalResult, Stage51PhysicsContext,
    VerifiedCoefficientHandle,
)
from sqvm.hamiltonian import BasisConfig, DeviceArtifacts, build_ec_matrix, build_mode_capacitance_matrix, build_mode_transform, load_hamiltonian_config


def _phase_fixed(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype="<c16").reshape(-1).copy()
    if value.size == 0 or not np.all(np.isfinite(value)):
        fail(Stage51FailureCode.INITIAL_STATE_INVALID, "state vector")
    index = int(np.flatnonzero(np.abs(value) == np.abs(value).max())[0])
    if value[index] == 0:
        fail(Stage51FailureCode.INITIAL_STATE_INVALID, "zero state")
    value *= np.exp(-1j * np.angle(value[index]))
    if value[index].real < 0: value *= -1
    value = np.asarray(value, dtype="<c16"); value.setflags(write=False)
    return value


def phase_invariant_overlap(left: np.ndarray, right: np.ndarray) -> float:
    return float(abs(np.vdot(left, right)) ** 2)


def _arrays(handle: VerifiedCoefficientHandle, context: Stage51PhysicsContext) -> dict[str, np.ndarray]:
    verified = verify_evolution_coefficient_artifact(handle.artifact_root, context, handle.source_control_handle)
    fields = ("coefficient_plan_id", "artifact_root", "manifest_sha256", "receipt_sha256", "inventory_sha256", "physics_authority_id")
    if any(getattr(verified, name) != getattr(handle, name) for name in fields) or verified.source_control_handle is not handle.source_control_handle:
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "stale coefficient handle")
    inventory = read_json(handle.artifact_root / "array_inventory.json", Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED)
    result = {row["name"]: np.frombuffer((handle.artifact_root / row["path"]).read_bytes(), dtype=row["dtype"]).copy() for row in inventory["arrays"]}
    for value in result.values(): value.setflags(write=False)
    return result


def _run_stage51_smoke_base(coefficients: VerifiedCoefficientHandle, context: Stage51PhysicsContext) -> Stage51NumericalResult:
    """Run accepted bounded smoke physics from verified coefficient bytes only."""
    if not isinstance(coefficients, VerifiedCoefficientHandle): fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "coefficient handle")
    authority, _ = admit_physics_authority(context); arrays = _arrays(coefficients, context)
    plan = read_json(coefficients.artifact_root / "coefficient_plan.json", Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED)
    frame = plan.get("frame_reference_frequency_GHz")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"} or any(type(value) is not float or not np.isfinite(value) for value in frame.values()):
        fail(Stage51FailureCode.FRAME_AUTHORITY_MISMATCH, "coefficient frame")
    model = authority["model"]
    if model.get("tensor_order") != ["q1", "c", "q2"] or model.get("charge_cutoffs") != [1, 1, 1] or model.get("reference_state_count") != 16:
        fail(Stage51FailureCode.TENSOR_MAPPING_INVALID, "authority model")
    device = load_device(context.accepted_device_artifact); cap = build_capacitance_matrix(device)
    artifacts = DeviceArtifacts(context.accepted_device_artifact, {"capacitance_matrix": {"nodes": list(cap.nodes), "matrix_fF": [list(row) for row in cap.matrix_fF]}, "junction_parameters": [{"component": row.component, "junction": row.junction, "rn_ohm": row.rn_ohm, "ej_GHz": row.ej_GHz, "source": row.source} for row in resolve_junction_parameters(device).rows], "components": {name: {"squid": {"flux_bias_phi0": item.squid.flux_bias_phi0}} for name, item in device.components.items() if item.squid is not None}})
    ec = build_ec_matrix(build_mode_capacitance_matrix(artifacts, build_mode_transform(artifacts))).matrix_GHz
    config = Stage5Config(Path("stage51-authority"), "stage51", {}, ("stage51",), tuple(model["charge_cutoffs"]), None, None, None, model["reference_state_count"], authority["solver"], authority["tolerances"], {})
    scenario = EffectiveScenario("stage51", arrays["time_center_ns"], {"q1": (arrays["epsilon_q1"].real.astype("<f8"), arrays["epsilon_q1"].imag.astype("<f8")), "q2": (arrays["epsilon_q2"].real.astype("<f8"), arrays["epsilon_q2"].imag.astype("<f8"))}, {"q1": arrays["absolute_flux_q1"], "c": arrays["absolute_flux_c"], "q2": arrays["absolute_flux_q2"]}, dict(frame), {"q1": 0.0, "q2": 0.0})
    stage5 = Stage5Input(Stage5PathAdmission(context.repository_root, config, {}), {}, "stage51", {"stage51": scenario}, RebuiltModel(artifacts, replace(load_hamiltonian_config(context.accepted_hamiltonian_artifact), basis=BasisConfig({"q1":1,"c":1,"q2":1})), ec))
    result = evolve_stage5_scenario(stage5, "stage51")
    initial, final = _phase_fixed(result.states[0]), _phase_fixed(result.states[-1])
    populations = {name: np.asarray(values, dtype="<f8") for name, values in result.populations.items()}
    for value in (*populations.values(),): value.setflags(write=False)
    leakage, norm = np.asarray(result.leakage, dtype="<f8"), np.asarray(result.norm_error, dtype="<f8"); leakage.setflags(write=False); norm.setflags(write=False)
    edges = np.asarray(result.edge_time_ns, dtype="<f8"); edges.setflags(write=False)
    return Stage51NumericalResult(edges, initial, final, MappingProxyType(populations), leakage, norm, MappingProxyType({}), MappingProxyType({"angular_conversion": angular_rad_per_ns.__name__, "checks": result.checks, "projector_validation_pending": True}))
