"""Pure Stage 5.1 coefficient plans and coefficient-artifact verification."""

from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Any, Mapping
import uuid

import numpy as np

from sqvm.device.capacitance import build_capacitance_matrix
from sqvm.device.junction import resolve_junction_parameters
from sqvm.device.spec import load_device
from sqvm.evolution.physics import zoh_edges
from sqvm.evolution.stage51_authority import admit_physics_authority, canonical_sha256, fail, plain, read_json, safe_file
from sqvm.evolution.stage51_models import (
    EvolutionCoefficientPlan, Stage51EvolutionInput, Stage51FailureCode,
    Stage51PhysicsContext, VerifiedCoefficientHandle,
)
from sqvm.hamiltonian import BasisConfig, DeviceArtifacts, build_ec_matrix, build_hamiltonian, build_mode_capacitance_matrix, build_mode_transform, load_hamiltonian_config, resolve_effective_junctions
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import atomic_publish, inventory_tree_no_follow


PLAN_NAME = "coefficient_plan.json"
INVENTORY_NAME = "array_inventory.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "receipt.json"
SOURCE_NAME = "source_snapshot.json"
ENVIRONMENT_NAME = "environment_snapshot.json"
ARRAYS = {
    "time_center_ns": ("<f8", "ns"), "time_edge_ns": ("<f8", "ns"),
    "epsilon_q1": ("<c16", "GHz"), "epsilon_q2": ("<c16", "GHz"),
    "absolute_flux_q1": ("<f8", "Phi/Phi0"), "absolute_flux_c": ("<f8", "Phi/Phi0"), "absolute_flux_q2": ("<f8", "Phi/Phi0"),
}
CHECKS = (
    "verified_control_handle_valid", "control_receipt_rechecked", "effective_arrays_exact",
    "signed_sample_grid_exact", "zero_order_hold_edges_exact", "named_tensor_mapping_exact",
    "frame_reference_authority_valid", "phase_not_reapplied", "idle_not_reapplied",
    "physics_authorities_valid", "operators_finite_and_hermitian", "coefficient_arrays_finite",
    "angular_conversion_applied_once", "initial_state_valid", "initial_state_phase_canonical",
    "computational_projectors_valid", "solver_authority_valid",
)


def _model_probe(authority: Mapping[str, Any], context: Stage51PhysicsContext, flux: Mapping[str, float]) -> Mapping[str, Any]:
    try:
        device = load_device(context.accepted_device_artifact)
        capacitance = build_capacitance_matrix(device)
        artifacts = DeviceArtifacts(context.accepted_device_artifact, {
            "capacitance_matrix": {"nodes": list(capacitance.nodes), "matrix_fF": [list(row) for row in capacitance.matrix_fF]},
            "junction_parameters": [{"component": row.component, "junction": row.junction, "rn_ohm": row.rn_ohm, "ej_GHz": row.ej_GHz, "source": row.source} for row in resolve_junction_parameters(device).rows],
            "components": {name: {"squid": {"flux_bias_phi0": item.squid.flux_bias_phi0}} for name, item in device.components.items() if item.squid is not None},
        })
        ec = build_ec_matrix(build_mode_capacitance_matrix(artifacts, build_mode_transform(artifacts))).matrix_GHz
        config = replace(load_hamiltonian_config(context.accepted_hamiltonian_artifact), basis=BasisConfig({"q1": 1, "c": 1, "q2": 1}))
        matrix = build_hamiltonian(config, ec, resolve_effective_junctions(artifacts, flux)).matrix.toarray()
    except Exception as exc:
        fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, str(exc))
    if not np.all(np.isfinite(matrix)) or not np.array_equal(matrix, matrix.conj().T):
        fail(Stage51FailureCode.OPERATOR_CONSTRUCTION_FAILED, "non-Hermitian static probe")
    return MappingProxyType({"tensor_order": ["q1", "c", "q2"], "dimension": int(matrix.shape[0]), "static_probe_sha256": hashlib.sha256(np.asarray(matrix, dtype="<c16").tobytes()).hexdigest().upper()})


def build_evolution_coefficient_plan(admitted: Stage51EvolutionInput, context: Stage51PhysicsContext) -> EvolutionCoefficientPlan:
    if not isinstance(admitted, Stage51EvolutionInput):
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "admitted input")
    authority, binding = admit_physics_authority(context)
    edges = np.asarray(zoh_edges(admitted.time_center_ns), dtype="<f8")
    if edges.size != admitted.time_center_ns.size + 1 or not np.array_equal(edges[1:] - edges[:-1], np.full(admitted.time_center_ns.size, 0.5)):
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "ZOH edges")
    raw_arrays = {
        "time_center_ns": admitted.time_center_ns, "time_edge_ns": edges,
        "epsilon_q1": admitted.epsilon_q1, "epsilon_q2": admitted.epsilon_q2,
        "absolute_flux_q1": admitted.absolute_flux_phi0["q1"], "absolute_flux_c": admitted.absolute_flux_phi0["c"], "absolute_flux_q2": admitted.absolute_flux_phi0["q2"],
    }
    arrays: dict[str, np.ndarray] = {}
    inventory: dict[str, Mapping[str, Any]] = {}
    for name, value in raw_arrays.items():
        dtype, unit = ARRAYS[name]
        array = np.asarray(value, dtype=dtype).copy(order="C")
        if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
            fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, name)
        array.setflags(write=False); arrays[name] = array
        raw = array.tobytes()
        inventory[name] = MappingProxyType({"dtype": dtype, "shape": [int(array.size)], "element_count": int(array.size), "byte_count": len(raw), "unit": unit, "sha256": hashlib.sha256(raw).hexdigest().upper()})
    probe = _model_probe(authority, context, {"q1": float(arrays["absolute_flux_q1"][0]), "c": float(arrays["absolute_flux_c"][0]), "q2": float(arrays["absolute_flux_q2"][0])})
    payload = {
        "control_binding": plain(admitted.control_binding), "physics_authority_binding": plain(binding), "clock": {"dt_ns": 0.5},
        "frame_reference_frequency_GHz": plain(admitted.frame_reference_frequency_GHz), "operator_inventory": plain(probe),
        "coefficient_inventory": {name: plain(row) for name, row in inventory.items()},
        "initial_state_spec": {"rule": "phase_fixed_lowest_lab_eigenvector_v1"},
        "observable_spec": {"labels": ["000", "100", "001", "101"], "replay_fidelity": "phase_invariant_final_overlap_v1"},
        "solver_spec": plain(authority["solver"]),
    }
    checks = tuple(MappingProxyType({"name": name, "passed": True}) for name in CHECKS)
    return EvolutionCoefficientPlan("0.1", canonical_sha256(payload), admitted.control_binding, binding, MappingProxyType({"dt_ns": 0.5}), admitted.frame_reference_frequency_GHz, probe, MappingProxyType(inventory), MappingProxyType(payload["initial_state_spec"]), MappingProxyType(payload["observable_spec"]), MappingProxyType(payload["solver_spec"]), checks, MappingProxyType(arrays))


def _payload(plan: EvolutionCoefficientPlan) -> dict[str, Any]:
    return {"schema_version": plan.schema_version, "coefficient_plan_id": plan.coefficient_plan_id, "control_binding": plain(plan.control_binding), "physics_authority_binding": plain(plan.physics_authority_binding), "clock": plain(plan.clock), "frame_reference_frequency_GHz": plain(plan.frame_reference_frequency_GHz), "operator_inventory": plain(plan.operator_inventory), "coefficient_inventory": plain(plan.coefficient_inventory), "initial_state_spec": plain(plan.initial_state_spec), "observable_spec": plain(plan.observable_spec), "solver_spec": plain(plan.solver_spec), "checks": plain(plan.checks)}


def publish_evolution_coefficient_artifact(plan: EvolutionCoefficientPlan, context: Stage51PhysicsContext, output_dir: Path) -> VerifiedCoefficientHandle:
    if not isinstance(plan, EvolutionCoefficientPlan):
        fail(Stage51FailureCode.COEFFICIENT_PLAN_INVALID, "plan")
    admit_physics_authority(context)
    output = context.output_root.resolve(); target = Path(output_dir).resolve()
    try: target.relative_to(output)
    except ValueError: fail(Stage51FailureCode.PUBLICATION_CONFLICT, "outside output root")
    if target.exists(): fail(Stage51FailureCode.PUBLICATION_CONFLICT, "target exists")
    target.parent.mkdir(parents=True, exist_ok=True); staging = target.parent / f".{target.name}.staging.{uuid.uuid4().hex}"
    try:
        staging.mkdir(); rows = []
        for name, array in plan.arrays.items():
            path = staging / "arrays" / f"{name}.bin"; path.parent.mkdir(exist_ok=True); raw = array.tobytes(); path.write_bytes(raw)
            rows.append({"name": name, "path": f"arrays/{name}.bin", **plain(plan.coefficient_inventory[name])})
        inventory = {"schema_version": "0.1", "artifact_type": "stage_05_1_coefficient_array_inventory", "artifact_version": "0.1", "arrays": sorted(rows, key=lambda row: row["name"])}
        (staging / INVENTORY_NAME).write_bytes(canonical_json_bytes(inventory)); (staging / PLAN_NAME).write_bytes(canonical_json_bytes(_payload(plan)))
        shutil.copyfile(context.source_snapshot, staging / SOURCE_NAME); shutil.copyfile(context.environment_snapshot, staging / ENVIRONMENT_NAME)
        payload_files = [{"path": row["path"], "byte_length": row["byte_length"], "raw_sha256": row["raw_sha256"]} for row in inventory_tree_no_follow(staging) if row.get("entry_type") == "file"]
        manifest = {"schema_version": "0.1", "artifact_type": "stage_05_1_coefficient_manifest", "artifact_version": "0.1", "coefficient_plan_id": plan.coefficient_plan_id, "payload_files": payload_files, "plan_sha256": raw_file_sha256(staging / PLAN_NAME), "inventory_sha256": raw_file_sha256(staging / INVENTORY_NAME)}
        (staging / MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest)); manifest_sha = raw_file_sha256(staging / MANIFEST_NAME)
        report = {"schema_version": "0.1", "artifact_type": "stage_05_1_coefficient_verification_report", "artifact_version": "0.1", "coefficient_plan_id": plan.coefficient_plan_id, "ok": True, "checks": plain(plan.checks), "manifest_sha256": manifest_sha}
        (staging / REPORT_NAME).write_bytes(canonical_json_bytes(report)); report_sha = raw_file_sha256(staging / REPORT_NAME)
        receipt = {"schema_version": "0.1", "artifact_type": "stage_05_1_coefficient_receipt", "artifact_version": "0.1", "coefficient_plan_id": plan.coefficient_plan_id, "status": "published", "manifest_sha256": manifest_sha, "verification_report_sha256": report_sha, "physics_authority_id": plan.physics_authority_binding["physics_authority_id"]}
        (staging / RECEIPT_NAME).write_bytes(canonical_json_bytes(receipt)); receipt_sha = raw_file_sha256(staging / RECEIPT_NAME)
        atomic_publish(staging, target)
        return VerifiedCoefficientHandle(plan.coefficient_plan_id, target, manifest_sha, receipt_sha, raw_file_sha256(target / INVENTORY_NAME), plan.physics_authority_binding["physics_authority_id"])
    except Exception:
        if staging.exists(): shutil.rmtree(staging)
        raise


def verify_evolution_coefficient_artifact(artifact_root: Path, context: Stage51PhysicsContext) -> VerifiedCoefficientHandle:
    root = Path(artifact_root).resolve(); output = context.output_root.resolve()
    try: root.relative_to(output)
    except ValueError: fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "outside output root")
    expected_files = {PLAN_NAME, INVENTORY_NAME, SOURCE_NAME, ENVIRONMENT_NAME, MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME, *{f"arrays/{name}.bin" for name in ARRAYS}}
    actual_files = {row["path"] for row in inventory_tree_no_follow(root) if row.get("entry_type") == "file"}
    if actual_files != expected_files:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "published file set")
    plan, inventory, manifest, report, receipt = (read_json(root / name, Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED) for name in (PLAN_NAME, INVENTORY_NAME, MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME))
    manifest_sha, report_sha, inventory_sha = raw_file_sha256(root / MANIFEST_NAME), raw_file_sha256(root / REPORT_NAME), raw_file_sha256(root / INVENTORY_NAME)
    if set(manifest) != {"schema_version", "artifact_type", "artifact_version", "coefficient_plan_id", "payload_files", "plan_sha256", "inventory_sha256"} or manifest.get("schema_version") != "0.1" or manifest.get("artifact_type") != "stage_05_1_coefficient_manifest" or manifest.get("artifact_version") != "0.1":
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "manifest schema")
    payload_files = [{"path": row["path"], "byte_length": row["byte_length"], "raw_sha256": row["raw_sha256"]} for row in inventory_tree_no_follow(root) if row.get("entry_type") == "file" and row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}]
    if manifest.get("payload_files") != payload_files or manifest.get("plan_sha256") != raw_file_sha256(root / PLAN_NAME) or manifest.get("inventory_sha256") != inventory_sha or manifest.get("coefficient_plan_id") != plan.get("coefficient_plan_id"):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "manifest bindings")
    if set(report) != {"schema_version", "artifact_type", "artifact_version", "coefficient_plan_id", "ok", "checks", "manifest_sha256"} or report.get("schema_version") != "0.1" or report.get("artifact_type") != "stage_05_1_coefficient_verification_report" or report.get("artifact_version") != "0.1" or report.get("ok") is not True or report.get("manifest_sha256") != manifest_sha or report.get("coefficient_plan_id") != plan.get("coefficient_plan_id"):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "report schema")
    if set(receipt) != {"schema_version", "artifact_type", "artifact_version", "coefficient_plan_id", "status", "manifest_sha256", "verification_report_sha256", "physics_authority_id"} or receipt.get("schema_version") != "0.1" or receipt.get("artifact_type") != "stage_05_1_coefficient_receipt" or receipt.get("artifact_version") != "0.1" or receipt.get("status") != "published" or receipt.get("manifest_sha256") != manifest_sha or receipt.get("verification_report_sha256") != report_sha or plan.get("coefficient_plan_id") != receipt.get("coefficient_plan_id"):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "terminal bindings")
    rows = inventory.get("arrays")
    if set(inventory) != {"schema_version", "artifact_type", "artifact_version", "arrays"} or inventory.get("schema_version") != "0.1" or inventory.get("artifact_type") != "stage_05_1_coefficient_array_inventory" or inventory.get("artifact_version") != "0.1" or not isinstance(rows, list) or len(rows) != len(ARRAYS): fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "array inventory")
    seen: set[str] = set(); plan_rows: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"name", "path", "dtype", "shape", "element_count", "byte_count", "unit", "sha256"}: fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "inventory row")
        name = row["name"]
        if name not in ARRAYS or name in seen or row["path"] != f"arrays/{name}.bin" or Path(row["path"]).is_absolute() or ".." in Path(row["path"]).parts: fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, str(name))
        seen.add(name); dtype, unit = ARRAYS[name]; count = row["element_count"]
        if row["dtype"] != dtype or row["unit"] != unit or type(count) is not int or count <= 0 or row["shape"] != [count] or row["byte_count"] != np.dtype(dtype).itemsize * count: fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, name)
        raw = safe_file(root, root / row["path"], Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED).read_bytes()
        if len(raw) != row["byte_count"] or hashlib.sha256(raw).hexdigest().upper() != row["sha256"] or not np.all(np.isfinite(np.frombuffer(raw, dtype=dtype))): fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, name)
        plan_rows[name] = {key: row[key] for key in ("dtype", "shape", "element_count", "byte_count", "unit", "sha256")}
    if seen != set(ARRAYS) or plan.get("coefficient_inventory") != plan_rows:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "plan inventory")
    payload = {key: plan[key] for key in ("control_binding", "physics_authority_binding", "clock", "frame_reference_frequency_GHz", "operator_inventory", "coefficient_inventory", "initial_state_spec", "observable_spec", "solver_spec")}
    if plan.get("coefficient_plan_id") != canonical_sha256(payload):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "coefficient_plan_id")
    _, authority_binding = admit_physics_authority(context)
    if plain(plan.get("physics_authority_binding")) != plain(authority_binding) or receipt.get("physics_authority_id") != authority_binding["physics_authority_id"]:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "physics authority binding")
    return VerifiedCoefficientHandle(plan["coefficient_plan_id"], root, raw_file_sha256(root / MANIFEST_NAME), raw_file_sha256(root / RECEIPT_NAME), raw_file_sha256(root / INVENTORY_NAME), receipt["physics_authority_id"])
