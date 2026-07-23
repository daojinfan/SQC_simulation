"""Local projected-charge calibration scans without synchronous replay."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from types import MappingProxyType
from typing import Any, Mapping
import uuid

import numpy as np

from sqvm.control.stage4_1_artifacts import write_parameterized_control_artifact
from sqvm.control.stage4_1_compile import (
    adapt_qcis_v03_compilation,
    compile_qcis_waveform_plan,
)
from sqvm.control.stage4_1_context import production_parameterized_control_context
from sqvm.control.stage4_1_verify import verify_parameterized_control_artifact
from sqvm.evolution.stage51_artifacts import ARRAY_SPECS
from sqvm.evolution.stage51_authority import admit_verified_control
from sqvm.evolution.stage51_coefficients import (
    build_evolution_coefficient_plan,
    publish_evolution_coefficient_artifact,
    verify_evolution_coefficient_artifact,
)
from sqvm.evolution.stage51_context import production_stage51_physics_context
from sqvm.evolution.stage51_models import Stage51NumericalResult
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.qcis import (
    QCISCompilation,
    verify_coefficient_inventory,
    verify_compilation,
    verify_drive_event_inventory,
)
from sqvm.runtime.calibration_model import (
    MODEL_AUTHORITY_PATH,
    calibration_model_configuration_sha256,
    execute_calibration_model_worker,
    model_configuration_from_authority,
    resolve_calibration_model_authority,
    validate_calibration_model_result,
)
from sqvm.runtime.publication import publish_calibration_directory
from sqvm.runtime.storage import (
    inventory_tree_no_follow,
    write_canonical_new,
)


SCHEMA_VERSION = "0.1"
POLICY_PATH = "configs/runtime/calibration_scan/execution_policy_v1.json"
QUALIFICATION_SCOPE = "local_calibration_scan_v1"
RESULT_NAME = "scan_result.json"
INVENTORY_NAME = "array_inventory.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "receipt.json"
EVIDENCE_NAME = "execution_evidence.json"
_POINT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_RESULT_CHECKS = (
    "worker_result_valid",
    "solver_authority_exact",
    "artifact_bindings_exact",
    "numerical_replay_deferred_declared",
)
_POINT_CHECKS = (
    "qcis_verified",
    "control_and_model_authorities_verified",
    "worker_result_structurally_verified",
    "claim_boundary_explicit",
)


class CalibrationScanExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CalibrationScanHandle:
    artifact_root: Path
    manifest_sha256: str
    receipt_sha256: str
    qualification_scope: str = QUALIFICATION_SCOPE


def calibration_scan_policy(repository_root: str | Path | None = None) -> Mapping[str, Any]:
    root = _repository_root(repository_root)
    return MappingProxyType(_load_policy(root))


def run_calibration_scan_point(
    compilation: QCISCompilation,
    point_id: str,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    timeout_s: float = 600.0,
    model_configuration: Mapping[str, Any] | None = None,
    idle_flux_phi0: Mapping[str, Any] | None = None,
) -> CalibrationScanHandle:
    """Run one point through Stage 4.1 and an isolated projected-charge worker."""

    root = _repository_root(repository_root)
    model_authority = resolve_calibration_model_authority(
        root,
        model_configuration,
        idle_flux_phi0=idle_flux_phi0,
    )
    resolved_configuration = model_configuration_from_authority(model_authority)
    resolved_idle_flux = model_authority["model"]["idle_flux_phi0"]
    policy = _load_policy(root)
    _admit(compilation, point_id, timeout_s, policy)
    output = _safe_output_root(output_root, root)
    target = output / point_id
    if target.exists():
        raise CalibrationScanExecutionError(f"point target already exists: {point_id}")
    output.mkdir(parents=True, exist_ok=True)
    staging = output / f".cs_{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir()
        control = _publish_control(
            compilation,
            point_id,
            staging,
            root,
            resolved_idle_flux,
        )
        coefficients, numerical = _run_worker(
            control,
            staging,
            root,
            timeout_s,
            resolved_configuration,
            resolved_idle_flux,
        )
        result_binding = _write_scan_result(
            staging / "stage51" / "evolution",
            compilation,
            coefficients,
            numerical,
            policy,
            root,
            model_authority,
        )
        evidence = _execution_evidence(
            compilation,
            point_id,
            control,
            coefficients,
            result_binding,
            policy,
            root,
            model_authority,
        )
        write_canonical_new(staging / EVIDENCE_NAME, evidence)
        _write_terminal_documents(staging, evidence)
        _verify_scan_tree(
            staging, compilation, policy, root, point_id, model_authority
        )
        publish_calibration_directory(staging, target)
        return verify_calibration_scan_point(
            target,
            compilation,
            root,
            model_configuration=resolved_configuration,
            idle_flux_phi0=resolved_idle_flux,
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_calibration_scan_point(
    artifact_root: str | Path,
    compilation: QCISCompilation,
    repository_root: str | Path | None = None,
    *,
    model_configuration: Mapping[str, Any] | None = None,
    idle_flux_phi0: Mapping[str, Any] | None = None,
) -> CalibrationScanHandle:
    """Structurally verify a scan point without rerunning numerical evolution."""

    root = _repository_root(repository_root)
    model_authority = resolve_calibration_model_authority(
        root,
        model_configuration,
        idle_flux_phi0=idle_flux_phi0,
    )
    point_root = Path(artifact_root).resolve()
    _inside(point_root, root, "artifact root")
    policy = _load_policy(root)
    _admit(compilation, point_root.name, policy["max_worker_wall_seconds"], policy)
    _verify_scan_tree(
        point_root, compilation, policy, root, point_root.name, model_authority
    )
    return CalibrationScanHandle(
        point_root,
        raw_file_sha256(point_root / MANIFEST_NAME),
        raw_file_sha256(point_root / RECEIPT_NAME),
    )


def _publish_control(
    compilation: QCISCompilation,
    point_id: str,
    staging: Path,
    root: Path,
    idle_flux_phi0: Mapping[str, Any],
):
    stage41_root = staging / "stage41"
    stage41_root.mkdir()
    context = production_parameterized_control_context(
        compilation.plan.authority_sha256,
        root,
        stage41_root,
        idle_flux_phi0=idle_flux_phi0,
    )
    expected = adapt_qcis_v03_compilation(compilation, point_id, context)
    built = compile_qcis_waveform_plan(expected, context)
    published = write_parameterized_control_artifact(
        built,
        context,
        stage41_root / "control",
    )
    return verify_parameterized_control_artifact(
        published["artifact_root"],
        context,
        expected,
    )


def _run_worker(
    control,
    staging: Path,
    root: Path,
    timeout_s: float,
    model_configuration: Mapping[str, Any],
    idle_flux_phi0: Mapping[str, Any],
):
    stage51_root = staging / "stage51"
    context = production_stage51_physics_context(root, output_root=stage51_root)
    admitted = admit_verified_control(control, context)
    plan = build_evolution_coefficient_plan(admitted, context)
    coefficients = publish_evolution_coefficient_artifact(
        plan,
        context,
        stage51_root / "coefficient",
        control,
    )
    return coefficients, execute_calibration_model_worker(
        coefficients,
        root,
        timeout_s=timeout_s,
        model_configuration=model_configuration,
        idle_flux_phi0=idle_flux_phi0,
    )


def _write_scan_result(
    target: Path,
    compilation: QCISCompilation,
    coefficients,
    numerical: Stage51NumericalResult,
    policy: Mapping[str, Any],
    root: Path,
    model_authority: Mapping[str, Any],
) -> Mapping[str, str]:
    target.mkdir()
    arrays = {
        "initial_state": numerical.initial_state,
        "final_state": numerical.final_state,
        **{
            f"population_{label}": numerical.populations[label]
            for label in ("000", "100", "001", "101")
        },
        "leakage": numerical.leakage,
        "norm_error": numerical.norm_error,
    }
    rows: list[dict[str, Any]] = []
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        array = np.asarray(arrays[name], dtype=dtype, order="C")
        raw = array.tobytes(order="C")
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
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
    rows.sort(key=lambda row: row["name"])
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_array_inventory",
        "arrays": rows,
    }
    write_canonical_new(target / INVENTORY_NAME, inventory)
    diagnostics = _plain(numerical.diagnostics)
    result_base = {
        "profile_id": policy["profile_id"],
        "qualification_scope": QUALIFICATION_SCOPE,
        "qcis_sha256": compilation.concrete_source_sha256,
        "coefficient_plan_id": coefficients.coefficient_plan_id,
        "coefficient_manifest_sha256": coefficients.manifest_sha256,
        "coefficient_receipt_sha256": coefficients.receipt_sha256,
        "control_adapter_physics_authority_id": coefficients.physics_authority_id,
        "model_authority_id": model_authority["model_authority_id"],
        "model_authority_sha256": raw_file_sha256(root / MODEL_AUTHORITY_PATH),
        "model_configuration_sha256": calibration_model_configuration_sha256(
            model_configuration_from_authority(model_authority)
        ),
        "policy_sha256": raw_file_sha256(root / POLICY_PATH),
        "edge_time_ns": _plain(numerical.edge_time_ns.tolist()),
        "projector_sha256": _plain(numerical.projector_sha256),
        "solver_diagnostics": diagnostics,
        "array_inventory_sha256": raw_file_sha256(target / INVENTORY_NAME),
        "numerical_replay": {
            "performed": False,
            "policy": policy["numerical_replay_policy"],
        },
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result",
        "status": "completed",
        "result_id": _canonical_sha(result_base),
        **result_base,
    }
    write_canonical_new(target / RESULT_NAME, result)
    payload_files = _inventory_payload(target)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result_manifest",
        "result_id": result["result_id"],
        "payload_files": payload_files,
    }
    write_canonical_new(target / MANIFEST_NAME, manifest)
    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result_report",
        "result_id": result["result_id"],
        "ok": True,
        "checks": [{"name": name, "passed": True} for name in _RESULT_CHECKS],
        "manifest_sha256": raw_file_sha256(target / MANIFEST_NAME),
    }
    write_canonical_new(target / REPORT_NAME, report)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result_receipt",
        "status": "completed",
        "result_id": result["result_id"],
        "manifest_sha256": raw_file_sha256(target / MANIFEST_NAME),
        "verification_report_sha256": raw_file_sha256(target / REPORT_NAME),
        "qualification_scope": QUALIFICATION_SCOPE,
        "formal_scale_qualified": False,
        "recommendation_eligible": False,
    }
    write_canonical_new(target / RECEIPT_NAME, receipt)
    return MappingProxyType(
        {
            "result_id": result["result_id"],
            "manifest_sha256": raw_file_sha256(target / MANIFEST_NAME),
            "receipt_sha256": raw_file_sha256(target / RECEIPT_NAME),
        }
    )


def _execution_evidence(
    compilation,
    point_id: str,
    control,
    coefficients,
    result_binding: Mapping[str, str],
    policy: Mapping[str, Any],
    root: Path,
    model_authority: Mapping[str, Any],
) -> dict[str, Any]:
    model_id = model_authority["model"]["model_id"]
    base = {
        "point_id": point_id,
        "profile_id": policy["profile_id"],
        "qualification_scope": QUALIFICATION_SCOPE,
        "policy_sha256": raw_file_sha256(root / POLICY_PATH),
        "qcis_binding": {
            "concrete_qcis_sha256": compilation.concrete_source_sha256,
            "ast_sha256": compilation.plan.ast_sha256,
            "trace_sha256": compilation.plan.trace_sha256,
        },
        "control_binding": {
            "control_id": control.control_id,
            "manifest_sha256": control.manifest_sha256,
            "receipt_sha256": control.receipt_sha256,
        },
        "coefficient_binding": {
            "coefficient_plan_id": coefficients.coefficient_plan_id,
            "manifest_sha256": coefficients.manifest_sha256,
            "receipt_sha256": coefficients.receipt_sha256,
            "role": "verified_control_array_adapter_only",
        },
        "result_binding": dict(result_binding),
        "claim": {
            "model_evolution": True,
            "hardware_measurement": False,
            "formal_scale_qualified": False,
            "independent_numerical_replay": False,
            "evolution_model": model_id,
            "calibration_update_scope": "simulator_configuration_only",
        },
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_execution_evidence",
        "status": "completed",
        "evidence_id": _canonical_sha(base),
        **base,
    }


def _write_terminal_documents(staging: Path, evidence: Mapping[str, Any]) -> None:
    terminal = {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_manifest",
        "evidence_id": evidence["evidence_id"],
        "payload_files": [
            row for row in _inventory_payload(staging) if row["path"] not in terminal
        ],
    }
    write_canonical_new(staging / MANIFEST_NAME, manifest)
    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_report",
        "evidence_id": evidence["evidence_id"],
        "ok": True,
        "checks": [{"name": name, "passed": True} for name in _POINT_CHECKS],
        "manifest_sha256": raw_file_sha256(staging / MANIFEST_NAME),
    }
    write_canonical_new(staging / REPORT_NAME, report)
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_receipt",
        "status": "completed",
        "evidence_id": evidence["evidence_id"],
        "manifest_sha256": raw_file_sha256(staging / MANIFEST_NAME),
        "verification_report_sha256": raw_file_sha256(staging / REPORT_NAME),
        "qualification_scope": QUALIFICATION_SCOPE,
        "recommendation_eligible": False,
    }
    write_canonical_new(staging / RECEIPT_NAME, receipt)


def _verify_scan_tree(
    point_root: Path,
    compilation: QCISCompilation,
    policy: Mapping[str, Any],
    root: Path,
    expected_point_id: str,
    model_authority: Mapping[str, Any],
) -> None:
    model_id = model_authority["model"]["model_id"]
    expected_top = {
        "stage41",
        "stage51",
        EVIDENCE_NAME,
        MANIFEST_NAME,
        REPORT_NAME,
        RECEIPT_NAME,
    }
    if {path.name for path in point_root.iterdir()} != expected_top:
        raise CalibrationScanExecutionError("scan point file set is invalid")
    evidence = _canonical(point_root / EVIDENCE_NAME)
    manifest = _canonical(point_root / MANIFEST_NAME)
    report = _canonical(point_root / REPORT_NAME)
    receipt = _canonical(point_root / RECEIPT_NAME)
    expected_evidence_keys = {
        "schema_version",
        "artifact_type",
        "status",
        "evidence_id",
        "point_id",
        "profile_id",
        "qualification_scope",
        "policy_sha256",
        "qcis_binding",
        "control_binding",
        "coefficient_binding",
        "result_binding",
        "claim",
    }
    expected_claim = {
        "model_evolution": True,
        "hardware_measurement": False,
        "formal_scale_qualified": False,
        "independent_numerical_replay": False,
        "evolution_model": model_id,
        "calibration_update_scope": "simulator_configuration_only",
    }
    if (
        set(evidence) != expected_evidence_keys
        or evidence.get("schema_version") != SCHEMA_VERSION
        or evidence.get("artifact_type")
        != "stage_07_calibration_scan_execution_evidence"
        or evidence.get("status") != "completed"
        or evidence.get("point_id") != expected_point_id
        or evidence.get("profile_id") != policy["profile_id"]
        or evidence.get("qualification_scope") != QUALIFICATION_SCOPE
        or evidence.get("policy_sha256") != raw_file_sha256(root / POLICY_PATH)
        or evidence.get("qcis_binding", {}).get("concrete_qcis_sha256")
        != compilation.concrete_source_sha256
        or evidence.get("claim") != expected_claim
    ):
        raise CalibrationScanExecutionError("scan evidence binding is invalid")
    base = {
        key: value
        for key, value in evidence.items()
        if key not in {"schema_version", "artifact_type", "status", "evidence_id"}
    }
    if evidence.get("evidence_id") != _canonical_sha(base):
        raise CalibrationScanExecutionError("scan evidence identity mismatch")
    expected_manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_manifest",
        "evidence_id": evidence["evidence_id"],
        "payload_files": [
            row
            for row in _inventory_payload(point_root)
            if row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}
        ],
    }
    if manifest != expected_manifest:
        raise CalibrationScanExecutionError("scan manifest mismatch")
    expected_report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_report",
        "evidence_id": evidence["evidence_id"],
        "ok": True,
        "checks": [{"name": name, "passed": True} for name in _POINT_CHECKS],
        "manifest_sha256": raw_file_sha256(point_root / MANIFEST_NAME),
    }
    expected_receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_receipt",
        "status": "completed",
        "evidence_id": evidence["evidence_id"],
        "manifest_sha256": raw_file_sha256(point_root / MANIFEST_NAME),
        "verification_report_sha256": raw_file_sha256(point_root / REPORT_NAME),
        "qualification_scope": QUALIFICATION_SCOPE,
        "recommendation_eligible": False,
    }
    if report != expected_report or receipt != expected_receipt:
        raise CalibrationScanExecutionError("scan terminal documents are invalid")

    stage41_context = production_parameterized_control_context(
        compilation.plan.authority_sha256,
        root,
        point_root / "stage41",
        idle_flux_phi0=model_authority["model"]["idle_flux_phi0"],
    )
    expected_plan = adapt_qcis_v03_compilation(
        compilation,
        expected_point_id,
        stage41_context,
    )
    control = verify_parameterized_control_artifact(
        point_root / "stage41" / "control",
        stage41_context,
        expected_plan,
    )
    stage51_context = production_stage51_physics_context(
        root,
        output_root=point_root / "stage51",
    )
    coefficients = verify_evolution_coefficient_artifact(
        point_root / "stage51" / "coefficient",
        stage51_context,
        control,
    )
    result_id = _verify_scan_result(
        point_root / "stage51" / "evolution",
        coefficients,
        stage51_context,
        compilation,
        policy,
        root,
        model_authority,
    )
    if evidence.get("result_binding") != {
        "result_id": result_id,
        "manifest_sha256": raw_file_sha256(
            point_root / "stage51" / "evolution" / MANIFEST_NAME
        ),
        "receipt_sha256": raw_file_sha256(
            point_root / "stage51" / "evolution" / RECEIPT_NAME
        ),
    }:
        raise CalibrationScanExecutionError("scan result binding mismatch")


def _verify_scan_result(
    target: Path,
    coefficients,
    context,
    compilation,
    policy: Mapping[str, Any],
    root: Path,
    model_authority: Mapping[str, Any],
) -> str:
    expected_files = {
        RESULT_NAME,
        INVENTORY_NAME,
        MANIFEST_NAME,
        REPORT_NAME,
        RECEIPT_NAME,
        *{spec[2] for spec in ARRAY_SPECS.values()},
    }
    rows = inventory_tree_no_follow(target)
    actual = {row["path"] for row in rows if row.get("entry_type") == "file"}
    if actual != expected_files or any(
        row.get("entry_type") in {"link", "other"} for row in rows
    ):
        raise CalibrationScanExecutionError("scan result file set is invalid")
    inventory = _canonical(target / INVENTORY_NAME)
    result = _canonical(target / RESULT_NAME)
    manifest = _canonical(target / MANIFEST_NAME)
    report = _canonical(target / REPORT_NAME)
    receipt = _canonical(target / RECEIPT_NAME)
    inventory_rows = inventory.get("arrays")
    if (
        set(inventory) != {"schema_version", "artifact_type", "arrays"}
        or inventory.get("schema_version") != SCHEMA_VERSION
        or inventory.get("artifact_type")
        != "stage_07_calibration_scan_array_inventory"
        or not isinstance(inventory_rows, list)
        or len(inventory_rows) != len(ARRAY_SPECS)
    ):
        raise CalibrationScanExecutionError("scan result inventory is invalid")
    by_name = {
        row.get("name"): row
        for row in inventory_rows
        if isinstance(row, Mapping)
    }
    arrays: dict[str, np.ndarray] = {}
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        row = by_name.get(name)
        if (
            not isinstance(row, Mapping)
            or set(row)
            != {
                "name",
                "path",
                "dtype",
                "shape",
                "element_count",
                "byte_count",
                "unit",
                "sha256",
            }
            or row.get("path") != relative
            or row.get("dtype") != dtype
            or row.get("unit") != unit
        ):
            raise CalibrationScanExecutionError(f"scan result array invalid: {name}")
        raw = (target / relative).read_bytes()
        if (
            len(raw) != row.get("byte_count")
            or row.get("element_count") != len(raw) // np.dtype(dtype).itemsize
            or row.get("shape") != [row.get("element_count")]
            or hashlib.sha256(raw).hexdigest().upper() != row.get("sha256")
        ):
            raise CalibrationScanExecutionError(f"scan result array hash: {name}")
        values = np.frombuffer(raw, dtype=dtype).copy(order="C")
        values.setflags(write=False)
        arrays[name] = values
    result_base = {
        key: value
        for key, value in result.items()
        if key not in {"schema_version", "artifact_type", "status", "result_id"}
    }
    expected_result_keys = {
        "schema_version",
        "artifact_type",
        "status",
        "result_id",
        "profile_id",
        "qualification_scope",
        "qcis_sha256",
        "coefficient_plan_id",
        "coefficient_manifest_sha256",
        "coefficient_receipt_sha256",
        "control_adapter_physics_authority_id",
        "model_authority_id",
        "model_authority_sha256",
        "model_configuration_sha256",
        "policy_sha256",
        "edge_time_ns",
        "projector_sha256",
        "solver_diagnostics",
        "array_inventory_sha256",
        "numerical_replay",
    }
    if (
        set(result) != expected_result_keys
        or result.get("schema_version") != SCHEMA_VERSION
        or result.get("artifact_type") != "stage_07_calibration_scan_result"
        or result.get("status") != "completed"
        or result.get("result_id") != _canonical_sha(result_base)
        or result.get("profile_id") != policy["profile_id"]
        or result.get("qualification_scope") != QUALIFICATION_SCOPE
        or result.get("qcis_sha256") != compilation.concrete_source_sha256
        or result.get("coefficient_plan_id") != coefficients.coefficient_plan_id
        or result.get("coefficient_manifest_sha256") != coefficients.manifest_sha256
        or result.get("coefficient_receipt_sha256") != coefficients.receipt_sha256
        or result.get("control_adapter_physics_authority_id")
        != coefficients.physics_authority_id
        or result.get("model_authority_id")
        != model_authority["model_authority_id"]
        or result.get("model_authority_sha256")
        != raw_file_sha256(root / MODEL_AUTHORITY_PATH)
        or result.get("model_configuration_sha256")
        != calibration_model_configuration_sha256(
            model_configuration_from_authority(model_authority)
        )
        or result.get("policy_sha256") != raw_file_sha256(root / POLICY_PATH)
        or result.get("array_inventory_sha256")
        != raw_file_sha256(target / INVENTORY_NAME)
        or result.get("numerical_replay")
        != {"performed": False, "policy": policy["numerical_replay_policy"]}
    ):
        raise CalibrationScanExecutionError("scan result identity is invalid")
    diagnostics = result.get("solver_diagnostics")
    projector = result.get("projector_sha256")
    if not isinstance(diagnostics, Mapping) or not isinstance(projector, Mapping):
        raise CalibrationScanExecutionError("scan result diagnostics are invalid")
    numerical = Stage51NumericalResult(
        np.asarray(result["edge_time_ns"], dtype="<f8"),
        arrays["initial_state"],
        arrays["final_state"],
        MappingProxyType(
            {
                label: arrays[f"population_{label}"]
                for label in ("000", "100", "001", "101")
            }
        ),
        arrays["leakage"],
        arrays["norm_error"],
        MappingProxyType(dict(projector)),
        MappingProxyType(dict(diagnostics)),
    )
    expected_edges = np.frombuffer(
        (coefficients.artifact_root / "arrays" / "time_edge_ns.bin").read_bytes(),
        dtype="<f8",
    )
    if not np.array_equal(numerical.edge_time_ns, expected_edges):
        raise CalibrationScanExecutionError("scan result edge time mismatch")
    if (
        diagnostics.get("solver_spec") != _plain(model_authority["solver"])
        or diagnostics.get("model_authority_id")
        != model_authority["model_authority_id"]
        or diagnostics.get("engine_id")
        != model_authority["model"]["model_id"]
    ):
        raise CalibrationScanExecutionError("scan solver authority mismatch")
    try:
        validate_calibration_model_result(numerical, model_authority)
    except ValueError as exc:
        raise CalibrationScanExecutionError(
            f"scan model result is invalid: {exc}"
        ) from exc
    expected_manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result_manifest",
        "result_id": result["result_id"],
        "payload_files": [
            row
            for row in _inventory_payload(target)
            if row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}
        ],
    }
    if manifest != expected_manifest:
        raise CalibrationScanExecutionError("scan result manifest mismatch")
    expected_report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result_report",
        "result_id": result["result_id"],
        "ok": True,
        "checks": [{"name": name, "passed": True} for name in _RESULT_CHECKS],
        "manifest_sha256": raw_file_sha256(target / MANIFEST_NAME),
    }
    expected_receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_calibration_scan_result_receipt",
        "status": "completed",
        "result_id": result["result_id"],
        "manifest_sha256": raw_file_sha256(target / MANIFEST_NAME),
        "verification_report_sha256": raw_file_sha256(target / REPORT_NAME),
        "qualification_scope": QUALIFICATION_SCOPE,
        "formal_scale_qualified": False,
        "recommendation_eligible": False,
    }
    if report != expected_report or receipt != expected_receipt:
        raise CalibrationScanExecutionError("scan result terminal documents are invalid")
    return str(result["result_id"])


def _admit(
    compilation: QCISCompilation,
    point_id: str,
    timeout_s: float,
    policy: Mapping[str, Any],
) -> None:
    if not isinstance(point_id, str) or _POINT_ID.fullmatch(point_id) is None:
        raise CalibrationScanExecutionError("point_id is invalid")
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int | float)
        or not math.isfinite(float(timeout_s))
        or not 0.0 < float(timeout_s) <= float(policy["max_worker_wall_seconds"])
    ):
        raise CalibrationScanExecutionError(
            f"timeout_s must be in (0, {policy['max_worker_wall_seconds']}]"
        )
    verify_compilation(compilation)
    verify_drive_event_inventory(compilation)
    verify_coefficient_inventory(compilation)
    sample_count = int(compilation.q1_xy.size)
    if (
        sample_count < 1
        or sample_count > int(policy["max_logical_sample_count"])
        or sample_count * float(compilation.plan.dt_ns)
        > float(policy["max_logical_duration_ns"])
    ):
        raise CalibrationScanExecutionError("QCIS plan exceeds calibration scan policy")


def _load_policy(root: Path) -> dict[str, Any]:
    path = root / POLICY_PATH
    policy = _canonical(path)
    expected = {
        "schema_version",
        "artifact_type",
        "artifact_version",
        "status",
        "profile_id",
        "max_worker_wall_seconds",
        "max_logical_sample_count",
        "max_logical_duration_ns",
        "numerical_replay_policy",
        "formal_scale_qualified",
        "hardware_measurement",
    }
    if (
        set(policy) != expected
        or policy.get("schema_version") != SCHEMA_VERSION
        or policy.get("artifact_type")
        != "stage_07_calibration_scan_execution_policy"
        or policy.get("status") != "local_simulation_enabled"
        or policy.get("profile_id") != QUALIFICATION_SCOPE
        or policy.get("numerical_replay_policy") != "deferred_batch_review"
        or policy.get("formal_scale_qualified") is not False
        or policy.get("hardware_measurement") is not False
    ):
        raise CalibrationScanExecutionError("calibration scan policy is invalid")
    return policy


def _inventory_payload(root: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": row["path"],
            "byte_length": row["byte_length"],
            "raw_sha256": row["raw_sha256"],
        }
        for row in inventory_tree_no_follow(root)
        if row.get("entry_type") == "file"
    ]


def _canonical(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationScanExecutionError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise CalibrationScanExecutionError(f"noncanonical JSON: {path.name}")
    return value


def _canonical_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _repository_root(value: str | Path | None) -> Path:
    root = Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]
    if not root.is_dir():
        raise CalibrationScanExecutionError("repository root is invalid")
    return root


def _safe_output_root(value: str | Path, root: Path) -> Path:
    path = Path(value)
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    _inside(path, root, "output root")
    if path.exists() and (not path.is_dir() or path.is_symlink()):
        raise CalibrationScanExecutionError("output root is unsafe")
    return path


def _inside(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CalibrationScanExecutionError(f"{label} is outside repository") from exc


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    return value


__all__ = [
    "CalibrationScanExecutionError",
    "CalibrationScanHandle",
    "calibration_scan_policy",
    "run_calibration_scan_point",
    "verify_calibration_scan_point",
]
