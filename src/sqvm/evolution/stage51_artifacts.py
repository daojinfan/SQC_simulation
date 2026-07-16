"""Stage 5.1 evolution result publication and independent replay verification."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Any, Mapping
import uuid

import numpy as np

from sqvm.evolution.stage51_authority import (
    admit_physics_authority, canonical_sha256, fail, plain, read_json, safe_file,
)
from sqvm.evolution.stage51_coefficients import (
    ENVIRONMENT_NAME as COEFFICIENT_ENVIRONMENT_NAME,
    INVENTORY_NAME as COEFFICIENT_INVENTORY_NAME,
    PLAN_NAME as COEFFICIENT_PLAN_NAME,
    SOURCE_NAME as COEFFICIENT_SOURCE_NAME,
    verify_evolution_coefficient_artifact,
)
from sqvm.evolution.stage51_models import (
    Stage51EvolutionArtifactSet, Stage51FailureCode, Stage51NumericalResult,
    Stage51PhysicsContext, VerifiedCoefficientHandle, VerifiedEvolutionHandle,
)
from sqvm.evolution.stage51_physics import phase_invariant_overlap, run_stage51_worker_kernel
from sqvm.evolution.stage51_worker import _validate_worker_result, execute_stage51_worker
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import atomic_publish, inventory_tree_no_follow


SCHEMA_VERSION = "0.1"
RESULT_NAME = "evolution_result.json"
INVENTORY_NAME = "array_inventory.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "receipt.json"
SOURCE_NAME = "source_snapshot.json"
ENVIRONMENT_NAME = "environment_snapshot.json"
ARRAY_SPECS = {
    "initial_state": ("<c16", "amplitude", "states/initial_state.bin"),
    "final_state": ("<c16", "amplitude", "states/final_state.bin"),
    "population_000": ("<f8", "probability", "observables/population_000.bin"),
    "population_100": ("<f8", "probability", "observables/population_100.bin"),
    "population_001": ("<f8", "probability", "observables/population_001.bin"),
    "population_101": ("<f8", "probability", "observables/population_101.bin"),
    "leakage": ("<f8", "probability", "observables/leakage.bin"),
    "norm_error": ("<f8", "absolute", "observables/norm_error.bin"),
}
EVOLUTION_CHECKS = (
    "worker_identity_valid", "solver_options_exact", "result_shape_finite",
    "norm_within_tolerance", "population_bounds_valid", "projector_hashes_exact",
    "runtime_timeout_passed", "independent_replay_passed", "artifact_bindings_exact",
)


def _numerical_arrays(result: Stage51NumericalResult) -> dict[str, np.ndarray]:
    return {
        "initial_state": result.initial_state,
        "final_state": result.final_state,
        **{f"population_{label}": result.populations[label] for label in ("000", "100", "001", "101")},
        "leakage": result.leakage,
        "norm_error": result.norm_error,
    }


def _deterministic_diagnostics(result: Stage51NumericalResult) -> dict[str, Any]:
    return {key: plain(value) for key, value in result.diagnostics.items() if key != "runtime_s"}


def _bindings(coefficients: VerifiedCoefficientHandle, context: Stage51PhysicsContext) -> dict[str, Any]:
    plan = read_json(coefficients.artifact_root / COEFFICIENT_PLAN_NAME, Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED)
    return {
        "control_binding": plan["control_binding"],
        "physics_authority_binding": plan["physics_authority_binding"],
        "coefficient_plan_id": coefficients.coefficient_plan_id,
        "coefficient_manifest_sha256": coefficients.manifest_sha256,
        "coefficient_receipt_sha256": coefficients.receipt_sha256,
        "coefficient_inventory_sha256": coefficients.inventory_sha256,
        "source_snapshot_sha256": raw_file_sha256(context.source_snapshot),
        "environment_snapshot_sha256": raw_file_sha256(context.environment_snapshot),
    }


def _result_base(
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
    result: Stage51NumericalResult,
    inventory_rows: list[Mapping[str, Any]],
    replay_fidelity: float,
) -> dict[str, Any]:
    return {
        "bindings": _bindings(coefficients, context),
        "edge_time_ns": plain(result.edge_time_ns.tolist()),
        "array_inventory": plain(inventory_rows),
        "projector_sha256": plain(result.projector_sha256),
        "solver_diagnostics": _deterministic_diagnostics(result),
        "replay_fidelity": float(replay_fidelity),
    }


def _result_payload(base: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_05_1_evolution_result",
        "artifact_version": SCHEMA_VERSION,
        "status": "published",
        "result_id": canonical_sha256(base),
        **plain(base),
    }


def _write_result_staging(
    staging: Path,
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
    result: Stage51NumericalResult,
    replay_fidelity: float,
) -> str:
    rows: list[dict[str, Any]] = []
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        array = np.asarray(_numerical_arrays(result)[name], dtype=dtype, order="C")
        raw = array.tobytes(order="C")
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        rows.append({
            "name": name, "path": relative, "dtype": dtype, "shape": [int(array.size)],
            "element_count": int(array.size), "byte_count": len(raw), "unit": unit,
            "sha256": hashlib.sha256(raw).hexdigest().upper(),
        })
    rows.sort(key=lambda row: row["name"])
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_05_1_evolution_array_inventory",
        "artifact_version": SCHEMA_VERSION,
        "arrays": rows,
    }
    result_payload = _result_payload(_result_base(coefficients, context, result, rows, replay_fidelity))
    (staging / INVENTORY_NAME).write_bytes(canonical_json_bytes(inventory))
    (staging / RESULT_NAME).write_bytes(canonical_json_bytes(result_payload))
    shutil.copyfile(coefficients.artifact_root / COEFFICIENT_SOURCE_NAME, staging / SOURCE_NAME)
    shutil.copyfile(coefficients.artifact_root / COEFFICIENT_ENVIRONMENT_NAME, staging / ENVIRONMENT_NAME)
    payload_files = [
        {"path": row["path"], "byte_length": row["byte_length"], "raw_sha256": row["raw_sha256"]}
        for row in inventory_tree_no_follow(staging) if row.get("entry_type") == "file"
    ]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_05_1_evolution_manifest",
        "artifact_version": SCHEMA_VERSION,
        "result_id": result_payload["result_id"],
        "payload_files": payload_files,
        "result_sha256": raw_file_sha256(staging / RESULT_NAME),
        "inventory_sha256": raw_file_sha256(staging / INVENTORY_NAME),
    }
    (staging / MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest))
    manifest_sha = raw_file_sha256(staging / MANIFEST_NAME)
    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_05_1_evolution_verification_report",
        "artifact_version": SCHEMA_VERSION,
        "result_id": result_payload["result_id"],
        "ok": True,
        "checks": [{"name": name, "passed": True} for name in EVOLUTION_CHECKS],
        "manifest_sha256": manifest_sha,
    }
    (staging / REPORT_NAME).write_bytes(canonical_json_bytes(report))
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_05_1_evolution_receipt",
        "artifact_version": SCHEMA_VERSION,
        "status": "published",
        "result_id": result_payload["result_id"],
        "control_id": coefficients.source_control_handle.control_id,
        "coefficient_plan_id": coefficients.coefficient_plan_id,
        "physics_authority_id": coefficients.physics_authority_id,
        "manifest_sha256": manifest_sha,
        "verification_report_sha256": raw_file_sha256(staging / REPORT_NAME),
    }
    (staging / RECEIPT_NAME).write_bytes(canonical_json_bytes(receipt))
    return result_payload["result_id"]


def _read_result_arrays(root: Path, inventory: Mapping[str, Any]) -> Mapping[str, np.ndarray]:
    rows = inventory.get("arrays")
    if set(inventory) != {"schema_version", "artifact_type", "artifact_version", "arrays"} or inventory.get("schema_version") != SCHEMA_VERSION or inventory.get("artifact_type") != "stage_05_1_evolution_array_inventory" or inventory.get("artifact_version") != SCHEMA_VERSION or not isinstance(rows, list) or len(rows) != len(ARRAY_SPECS):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result inventory")
    arrays: dict[str, np.ndarray] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"name", "path", "dtype", "shape", "element_count", "byte_count", "unit", "sha256"}:
            fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result inventory row")
        name = row.get("name")
        if name not in ARRAY_SPECS or name in seen:
            fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result array name")
        seen.add(name)
        dtype, unit, relative = ARRAY_SPECS[name]
        count = row.get("element_count")
        if row.get("path") != relative or row.get("dtype") != dtype or row.get("unit") != unit or type(count) is not int or count <= 0 or row.get("shape") != [count] or row.get("byte_count") != count * np.dtype(dtype).itemsize:
            fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, str(name))
        raw = safe_file(root, root / relative, Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED).read_bytes()
        if len(raw) != row["byte_count"] or hashlib.sha256(raw).hexdigest().upper() != row.get("sha256"):
            fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, str(name))
        array = np.frombuffer(raw, dtype=dtype).copy(order="C")
        if not np.all(np.isfinite(array)):
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, str(name))
        array.setflags(write=False)
        arrays[name] = array
    return MappingProxyType(arrays)


def _numerical_from_artifact(payload: Mapping[str, Any], arrays: Mapping[str, np.ndarray]) -> Stage51NumericalResult:
    edges = np.asarray(payload.get("edge_time_ns"), dtype="<f8")
    edges.setflags(write=False)
    diagnostics = payload.get("solver_diagnostics")
    hashes = payload.get("projector_sha256")
    if not isinstance(diagnostics, Mapping) or not isinstance(hashes, Mapping):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result diagnostics")
    return Stage51NumericalResult(
        edges, arrays["initial_state"], arrays["final_state"],
        MappingProxyType({label: arrays[f"population_{label}"] for label in ("000", "100", "001", "101")}),
        arrays["leakage"], arrays["norm_error"], MappingProxyType(dict(hashes)),
        MappingProxyType(dict(diagnostics)),
    )


def verify_stage51_evolution_artifact(
    artifact_root: Path,
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
    *,
    _independent_result: Stage51NumericalResult | None = None,
) -> VerifiedEvolutionHandle:
    verified_coefficients = verify_evolution_coefficient_artifact(
        coefficients.artifact_root, context, coefficients.source_control_handle,
    )
    root = Path(artifact_root).resolve()
    try:
        root.relative_to(context.output_root.resolve())
    except ValueError:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result outside output root")
    expected_files = {RESULT_NAME, INVENTORY_NAME, MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME, SOURCE_NAME, ENVIRONMENT_NAME, *{spec[2] for spec in ARRAY_SPECS.values()}}
    tree = inventory_tree_no_follow(root)
    actual_files = {row["path"] for row in tree if row.get("entry_type") == "file"}
    if actual_files != expected_files or any(row.get("entry_type") in {"link", "other"} for row in tree):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result file set")
    if raw_file_sha256(root / SOURCE_NAME) != raw_file_sha256(context.source_snapshot) or raw_file_sha256(root / ENVIRONMENT_NAME) != raw_file_sha256(context.environment_snapshot):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result snapshots")
    payload, inventory, manifest, report, receipt = (
        read_json(root / name, Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED)
        for name in (RESULT_NAME, INVENTORY_NAME, MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME)
    )
    payload_keys = {"schema_version", "artifact_type", "artifact_version", "status", "result_id", "bindings", "edge_time_ns", "array_inventory", "projector_sha256", "solver_diagnostics", "replay_fidelity"}
    if set(payload) != payload_keys or payload.get("schema_version") != SCHEMA_VERSION or payload.get("artifact_type") != "stage_05_1_evolution_result" or payload.get("artifact_version") != SCHEMA_VERSION or payload.get("status") != "published":
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result schema")
    arrays = _read_result_arrays(root, inventory)
    if payload.get("array_inventory") != inventory["arrays"]:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result inventory binding")
    base = {key: payload[key] for key in ("bindings", "edge_time_ns", "array_inventory", "projector_sha256", "solver_diagnostics", "replay_fidelity")}
    if payload.get("result_id") != canonical_sha256(base) or payload.get("bindings") != _bindings(verified_coefficients, context):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result bindings")
    numerical = _numerical_from_artifact(payload, arrays)
    authority, _ = admit_physics_authority(context)
    _validate_worker_result(numerical, authority["tolerances"])
    independent = _independent_result or run_stage51_worker_kernel(verified_coefficients.artifact_root, context)
    if not isinstance(independent, Stage51NumericalResult):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "independent replay result")
    fidelity = phase_invariant_overlap(numerical.final_state, independent.final_state)
    threshold = float(authority["tolerances"]["norm_error"])
    if not np.isfinite(fidelity) or 1.0 - fidelity > threshold or payload.get("replay_fidelity") != fidelity or numerical.projector_sha256 != independent.projector_sha256 or not np.array_equal(numerical.edge_time_ns, independent.edge_time_ns) or not np.array_equal(numerical.initial_state, independent.initial_state):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "independent replay")
    payload_files = [
        {"path": row["path"], "byte_length": row["byte_length"], "raw_sha256": row["raw_sha256"]}
        for row in tree if row.get("entry_type") == "file" and row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}
    ]
    if set(manifest) != {"schema_version", "artifact_type", "artifact_version", "result_id", "payload_files", "result_sha256", "inventory_sha256"} or manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("artifact_type") != "stage_05_1_evolution_manifest" or manifest.get("artifact_version") != SCHEMA_VERSION or manifest.get("result_id") != payload["result_id"] or manifest.get("payload_files") != payload_files or manifest.get("result_sha256") != raw_file_sha256(root / RESULT_NAME) or manifest.get("inventory_sha256") != raw_file_sha256(root / INVENTORY_NAME):
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result manifest")
    manifest_sha = raw_file_sha256(root / MANIFEST_NAME)
    expected_checks = [{"name": name, "passed": True} for name in EVOLUTION_CHECKS]
    if set(report) != {"schema_version", "artifact_type", "artifact_version", "result_id", "ok", "checks", "manifest_sha256"} or report.get("schema_version") != SCHEMA_VERSION or report.get("artifact_type") != "stage_05_1_evolution_verification_report" or report.get("artifact_version") != SCHEMA_VERSION or report.get("result_id") != payload["result_id"] or report.get("ok") is not True or report.get("checks") != expected_checks or report.get("manifest_sha256") != manifest_sha:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result report")
    expected_receipt = {
        "schema_version": SCHEMA_VERSION, "artifact_type": "stage_05_1_evolution_receipt",
        "artifact_version": SCHEMA_VERSION, "status": "published", "result_id": payload["result_id"],
        "control_id": verified_coefficients.source_control_handle.control_id,
        "coefficient_plan_id": verified_coefficients.coefficient_plan_id,
        "physics_authority_id": verified_coefficients.physics_authority_id,
        "manifest_sha256": manifest_sha,
        "verification_report_sha256": raw_file_sha256(root / REPORT_NAME),
    }
    if receipt != expected_receipt:
        fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "result receipt")
    return VerifiedEvolutionHandle(payload["result_id"], root, manifest_sha, raw_file_sha256(root / RECEIPT_NAME), fidelity)


def run_verified_control_evolution(
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
    output_dir: Path,
    *,
    timeout_s: float,
) -> Stage51EvolutionArtifactSet:
    verified = verify_evolution_coefficient_artifact(
        coefficients.artifact_root, context, coefficients.source_control_handle,
    )
    target = Path(output_dir).resolve()
    try:
        target.relative_to(context.output_root.resolve())
    except ValueError:
        fail(Stage51FailureCode.PUBLICATION_CONFLICT, "result outside output root")
    if target.exists():
        fail(Stage51FailureCode.PUBLICATION_CONFLICT, "result target exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    worker_result = execute_stage51_worker(verified, context, timeout_s=timeout_s)
    independent = run_stage51_worker_kernel(verified.artifact_root, context)
    fidelity = phase_invariant_overlap(worker_result.final_state, independent.final_state)
    authority, _ = admit_physics_authority(context)
    if 1.0 - fidelity > float(authority["tolerances"]["norm_error"]):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker replay fidelity")
    staging = target.parent / f".{target.name}.staging.{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        result_id = _write_result_staging(staging, verified, context, worker_result, fidelity)
        verified_result = verify_stage51_evolution_artifact(
            staging, verified, context, _independent_result=independent,
        )
        if verified_result.result_id != result_id:
            fail(Stage51FailureCode.ARTIFACT_VERIFICATION_FAILED, "staging result id")
        atomic_publish(staging, target)
        return Stage51EvolutionArtifactSet(target, result_id, raw_file_sha256(target / MANIFEST_NAME), raw_file_sha256(target / RECEIPT_NAME), "published")
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
