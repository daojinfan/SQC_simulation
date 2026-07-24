"""Bounded Stage 7.1 entrance to production Stage 4.1 and Stage 5.1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
from types import MappingProxyType
from typing import Any, Mapping
import uuid

from sqvm.control.stage4_1_artifacts import write_parameterized_control_artifact
from sqvm.control.stage4_1_compile import adapt_qcis_v03_compilation, compile_qcis_waveform_plan
from sqvm.control.stage4_1_verify import verify_parameterized_control_artifact
from sqvm.evolution.stage51_artifacts import run_verified_control_evolution, verify_stage51_evolution_artifact
from sqvm.evolution.stage51_authority import admit_verified_control
from sqvm.evolution.stage51_coefficients import (
    build_evolution_coefficient_plan,
    publish_evolution_coefficient_artifact,
    verify_evolution_coefficient_artifact,
)
from sqvm.evolution.stage51_context import production_stage51_physics_context
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.qcis import QCISCompilation, verify_coefficient_inventory, verify_compilation, verify_drive_event_inventory
from sqvm.qcis.canonical import sha256_json
from sqvm.runtime.models import frozen_mapping
from sqvm.runtime.storage import atomic_publish, inventory_tree, inventory_tree_no_follow, write_canonical_new


SCHEMA_VERSION = "0.1"
EVIDENCE_NAME = "entrance_evidence.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "receipt.json"
_POINT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9A-F]{64}$")

CAPABILITY = frozen_mapping(
    {
        "backend_id": "stage51_qutip_closed_system_v1",
        "backend_kind": "model_evolution",
        "program_profile": "qcis_stage7_calibration_v3",
        "control_profile": "stage_04_1_parameterized_control_v1",
        "evolution_profile": "stage_05_1_verified_control_v1",
        "qualification_scope": "bounded_smoke_only",
        "response_kind": "model_evolution_evidence_v1",
        "measurement": False,
        "readout": False,
        "formal_scale_qualified": False,
        "calibration_eligible": False,
        "recommendation_eligible": False,
        "stage6_registered": False,
    }
)

CLAIM_ENVELOPE = frozen_mapping(
    {
        "response_kind": "model_evolution_evidence_v1",
        "claim_class": "bounded_closed_system_smoke_simulation",
        "measurement": False,
        "readout": False,
        "formal_scale_qualified": False,
        "calibration_eligible": False,
        "recommendation_eligible": False,
    }
)

BOUNDED_ENVELOPE = frozen_mapping(
    {
        "max_point_count": 1,
        "max_logical_sample_count": 64,
        "max_logical_duration_ns": 32.0,
        "max_effective_sample_count": 96,
        "max_effective_duration_ns": 48.0,
        "dt_ns": 0.5,
        "max_worker_wall_seconds": 900.0,
        "allowed_pilot_kind": "compiled_qcis_single_point",
        "allowed_cutoffs": {"q1": 1, "c": 1, "q2": 1},
        "solver_profile": "stage_05_1_smoke",
    }
)


class Stage71FailureCode(StrEnum):
    ENTRANCE_AUTHORITY_INVALID = "ENTRANCE_AUTHORITY_INVALID"
    CAPABILITY_NOT_APPROVED = "CAPABILITY_NOT_APPROVED"
    BOUNDED_ENVELOPE_EXCEEDED = "BOUNDED_ENVELOPE_EXCEEDED"
    QCIS_COMPILATION_INVALID = "QCIS_COMPILATION_INVALID"
    UPSTREAM_CONTROL_FAILED = "UPSTREAM_CONTROL_FAILED"
    UPSTREAM_EVOLUTION_FAILED = "UPSTREAM_EVOLUTION_FAILED"
    EVIDENCE_PUBLICATION_CONFLICT = "EVIDENCE_PUBLICATION_CONFLICT"
    EVIDENCE_VERIFICATION_FAILED = "EVIDENCE_VERIFICATION_FAILED"


class Stage71EntranceError(ValueError):
    def __init__(self, code: Stage71FailureCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else str(code))


@dataclass(frozen=True, slots=True)
class Stage71EvidenceHandle:
    evidence_id: str
    point_id: str
    artifact_root: Path
    manifest_sha256: str
    receipt_sha256: str
    replay_fidelity: float
    qualification_scope: str = "bounded_smoke_only"


def capability_descriptor() -> Mapping[str, Any]:
    """Return the immutable, deliberately unregistered capability descriptor."""

    return CAPABILITY


def bounded_envelope() -> Mapping[str, Any]:
    """Return the immutable single-point resource envelope."""

    return BOUNDED_ENVELOPE


def run_bounded_model_point(
    compilation: QCISCompilation,
    point_id: str,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    timeout_s: float = 900.0,
) -> Stage71EvidenceHandle:
    """Run and atomically publish one bounded, non-calibration model point."""

    root = _repository_root(repository_root)
    authority, authority_binding = _admit_entrance_authority(root)
    _admit_compilation(compilation, point_id, timeout_s, authority)
    output = _safe_output_root(output_root, root)
    try:
        _production_stage41_context(
            compilation.plan.authority_sha256,
            root,
            output / ".stage41-admission",
        )
        production_stage51_physics_context(root, output_root=output / ".stage51-admission")
    except Exception as exc:
        raise Stage71EntranceError(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, str(exc)) from exc
    target = output / point_id
    if target.exists():
        _fail(Stage71FailureCode.EVIDENCE_PUBLICATION_CONFLICT, "point target exists")
    output.mkdir(parents=True, exist_ok=True)
    staging = output / f".{point_id}.staging.{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        _expected_plan, control = _publish_control(compilation, point_id, staging, root)
        coefficients, evolution = _publish_evolution(control, staging, root, timeout_s)
        payload = _evidence_payload(
            compilation,
            point_id,
            control,
            coefficients,
            evolution,
            authority_binding,
        )
        write_canonical_new(staging / EVIDENCE_NAME, payload)
        _write_terminal_documents(staging, payload)
        _verify_evidence_graph(staging, payload, authority_binding)
        atomic_publish(staging, target)
        return verify_bounded_model_point(target, compilation, root)
    except Stage71EntranceError:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    except FileExistsError as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise Stage71EntranceError(Stage71FailureCode.EVIDENCE_PUBLICATION_CONFLICT, str(exc)) from exc
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise Stage71EntranceError(Stage71FailureCode.UPSTREAM_EVOLUTION_FAILED, str(exc)) from exc


def verify_bounded_model_point(
    artifact_root: str | Path,
    compilation: QCISCompilation,
    repository_root: str | Path | None = None,
) -> Stage71EvidenceHandle:
    """Independently reopen every upstream artifact and replay Stage 5.1."""

    root = _repository_root(repository_root)
    _authority, authority_binding = _admit_entrance_authority(root)
    point_root = Path(artifact_root).resolve()
    _inside(point_root, root, Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "artifact root")
    point_id = point_root.name
    _admit_compilation(compilation, point_id, BOUNDED_ENVELOPE["max_worker_wall_seconds"], _authority)
    payload = _canonical(point_root / EVIDENCE_NAME, Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED)
    _verify_evidence_graph(point_root, payload, authority_binding)
    try:
        stage41_context = _production_stage41_context(
            compilation.plan.authority_sha256,
            root,
            point_root / "stage41",
        )
        expected_plan = adapt_qcis_v03_compilation(compilation, point_id, stage41_context)
        control = verify_parameterized_control_artifact(
            point_root / "stage41" / "control",
            stage41_context,
            expected_plan,
        )
        stage51_context = production_stage51_physics_context(root, output_root=point_root / "stage51")
        coefficients = verify_evolution_coefficient_artifact(
            point_root / "stage51" / "coefficient",
            stage51_context,
            control,
        )
        evolution = verify_stage51_evolution_artifact(
            point_root / "stage51" / "evolution",
            coefficients,
            stage51_context,
        )
    except Exception as exc:
        raise Stage71EntranceError(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, str(exc)) from exc
    expected = _evidence_payload(compilation, point_id, control, coefficients, evolution, authority_binding)
    if payload != expected:
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "upstream binding mismatch")
    return Stage71EvidenceHandle(
        payload["evidence_id"],
        point_id,
        point_root,
        raw_file_sha256(point_root / MANIFEST_NAME),
        raw_file_sha256(point_root / RECEIPT_NAME),
        float(payload["evolution_binding"]["replay_fidelity"]),
    )


def _publish_control(compilation: QCISCompilation, point_id: str, staging: Path, root: Path):
    try:
        stage41_root = staging / "stage41"
        stage41_root.mkdir()
        context = _production_stage41_context(compilation.plan.authority_sha256, root, stage41_root)
        expected_plan = adapt_qcis_v03_compilation(compilation, point_id, context)
        compiled = compile_qcis_waveform_plan(expected_plan, context)
        published = write_parameterized_control_artifact(compiled, context, stage41_root / "control")
        handle = verify_parameterized_control_artifact(published["artifact_root"], context, expected_plan)
        effective_count = int(handle.time_center_ns.size)
        if (
            effective_count > BOUNDED_ENVELOPE["max_effective_sample_count"]
            or effective_count * BOUNDED_ENVELOPE["dt_ns"] > BOUNDED_ENVELOPE["max_effective_duration_ns"]
        ):
            _fail(Stage71FailureCode.BOUNDED_ENVELOPE_EXCEEDED, "effective control envelope")
        return expected_plan, handle
    except Stage71EntranceError:
        raise
    except Exception as exc:
        raise Stage71EntranceError(Stage71FailureCode.UPSTREAM_CONTROL_FAILED, str(exc)) from exc


def _publish_evolution(control, staging: Path, root: Path, timeout_s: float):
    try:
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
        published = run_verified_control_evolution(
            coefficients,
            context,
            stage51_root / "evolution",
            timeout_s=timeout_s,
        )
        return coefficients, verify_stage51_evolution_artifact(published.artifact_root, coefficients, context)
    except Exception as exc:
        raise Stage71EntranceError(Stage71FailureCode.UPSTREAM_EVOLUTION_FAILED, str(exc)) from exc


def _admit_compilation(
    compilation: QCISCompilation,
    point_id: str,
    timeout_s: float,
    authority: Mapping[str, Any],
) -> None:
    if not isinstance(point_id, str) or not point_id.isascii() or _POINT_ID.fullmatch(point_id) is None:
        _fail(Stage71FailureCode.BOUNDED_ENVELOPE_EXCEEDED, "point_id")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int | float) or not math.isfinite(float(timeout_s)) or not 0.0 < float(timeout_s) <= float(BOUNDED_ENVELOPE["max_worker_wall_seconds"]):
        _fail(Stage71FailureCode.BOUNDED_ENVELOPE_EXCEEDED, "timeout_s")
    try:
        verify_compilation(compilation)
        verify_drive_event_inventory(compilation)
        verify_coefficient_inventory(compilation)
    except Exception as exc:
        raise Stage71EntranceError(Stage71FailureCode.QCIS_COMPILATION_INVALID, str(exc)) from exc
    plan = compilation.plan
    count = int(compilation.q1_xy.size)
    if (
        plan.dt_ns != BOUNDED_ENVELOPE["dt_ns"]
        or plan.sample_rate_Hz != 2_000_000_000.0
        or compilation.envelope.instruction_set_id != CAPABILITY["program_profile"]
        or count < 1
        or count > BOUNDED_ENVELOPE["max_logical_sample_count"]
        or count * float(plan.dt_ns) > BOUNDED_ENVELOPE["max_logical_duration_ns"]
        or any(array.size != count for array in (compilation.q2_xy, compilation.q1_flux, compilation.q2_flux, compilation.c_flux))
    ):
        _fail(Stage71FailureCode.BOUNDED_ENVELOPE_EXCEEDED, "QCIS plan envelope")
    if authority.get("capability") != _plain(CAPABILITY) or authority.get("bounded_envelope") != _plain(BOUNDED_ENVELOPE):
        _fail(Stage71FailureCode.CAPABILITY_NOT_APPROVED, "authority descriptor")


def _evidence_payload(compilation, point_id, control, coefficients, evolution, authority_binding):
    base = {
        "point_id": point_id,
        "capability": _plain(CAPABILITY),
        "claim_envelope": _plain(CLAIM_ENVELOPE),
        "parent_calibration_binding": None,
        "qcis_binding": {
            "concrete_qcis_sha256": compilation.concrete_source_sha256,
            "ast_sha256": compilation.plan.ast_sha256,
            "trace_sha256": compilation.plan.trace_sha256,
            "plan_authority_sha256": sha256_json(_plain(compilation.plan.authority_sha256)),
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
        },
        "evolution_binding": {
            "evolution_result_id": evolution.result_id,
            "manifest_sha256": evolution.manifest_sha256,
            "receipt_sha256": evolution.receipt_sha256,
            "physics_authority_id": coefficients.physics_authority_id,
            "replay_fidelity": float(evolution.replay_fidelity),
        },
        "authority_binding": dict(authority_binding),
    }
    evidence_id = _canonical_sha256(base)
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_1_model_entrance_evidence",
        "artifact_version": SCHEMA_VERSION,
        "status": "published",
        "evidence_id": evidence_id,
        **base,
    }


def _write_terminal_documents(staging: Path, payload: Mapping[str, Any]) -> None:
    terminal = {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}
    payload_files = [row for row in inventory_tree(staging) if row["path"] not in terminal]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_1_model_entrance_manifest",
        "artifact_version": SCHEMA_VERSION,
        "evidence_id": payload["evidence_id"],
        "evidence_sha256": raw_file_sha256(staging / EVIDENCE_NAME),
        "payload_files": payload_files,
    }
    manifest_sha = write_canonical_new(staging / MANIFEST_NAME, manifest)
    report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_1_model_entrance_verification_report",
        "artifact_version": SCHEMA_VERSION,
        "evidence_id": payload["evidence_id"],
        "ok": True,
        "checks": [
            {"name": "authority_verified", "passed": True},
            {"name": "bounded_envelope_verified", "passed": True},
            {"name": "upstream_artifacts_verified", "passed": True},
            {"name": "claim_boundary_verified", "passed": True},
            {"name": "payload_inventory_verified", "passed": True},
        ],
        "blocking_reasons": [],
        "manifest_sha256": manifest_sha,
    }
    report_sha = write_canonical_new(staging / REPORT_NAME, report)
    write_canonical_new(
        staging / RECEIPT_NAME,
        {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "stage_07_1_model_entrance_receipt",
            "artifact_version": SCHEMA_VERSION,
            "status": "published",
            "evidence_id": payload["evidence_id"],
            "manifest_sha256": manifest_sha,
            "verification_report_sha256": report_sha,
            "qualification_scope": "bounded_smoke_only",
            "recommendation_eligible": False,
        },
    )


def _verify_evidence_graph(root: Path, payload: Mapping[str, Any], authority_binding: Mapping[str, str]) -> None:
    rows = inventory_tree_no_follow(root)
    if any(row.get("entry_type") in {"link", "other"} for row in rows):
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "unsafe publication tree")
    expected_top = {EVIDENCE_NAME, MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME, "stage41", "stage51"}
    if {path.name for path in root.iterdir()} != expected_top:
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "top-level file set")
    expected_payload_keys = {
        "schema_version", "artifact_type", "artifact_version", "status", "evidence_id", "point_id",
        "capability", "claim_envelope", "parent_calibration_binding", "qcis_binding", "control_binding",
        "coefficient_binding", "evolution_binding", "authority_binding",
    }
    if (
        set(payload) != expected_payload_keys
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("artifact_type") != "stage_07_1_model_entrance_evidence"
        or payload.get("artifact_version") != SCHEMA_VERSION
        or payload.get("status") != "published"
        or payload.get("capability") != _plain(CAPABILITY)
        or payload.get("claim_envelope") != _plain(CLAIM_ENVELOPE)
        or payload.get("parent_calibration_binding") is not None
        or payload.get("authority_binding") != dict(authority_binding)
    ):
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "evidence schema or claim")
    body = {key: payload[key] for key in payload if key not in {"schema_version", "artifact_type", "artifact_version", "status", "evidence_id"}}
    if payload.get("evidence_id") != _canonical_sha256(body):
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "evidence_id")
    manifest = _canonical(root / MANIFEST_NAME, Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED)
    report = _canonical(root / REPORT_NAME, Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED)
    receipt = _canonical(root / RECEIPT_NAME, Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED)
    payload_files = [row for row in inventory_tree(root) if row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}]
    expected_manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_1_model_entrance_manifest",
        "artifact_version": SCHEMA_VERSION,
        "evidence_id": payload["evidence_id"],
        "evidence_sha256": raw_file_sha256(root / EVIDENCE_NAME),
        "payload_files": payload_files,
    }
    if manifest != expected_manifest:
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "manifest")
    expected_report = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_1_model_entrance_verification_report",
        "artifact_version": SCHEMA_VERSION,
        "evidence_id": payload["evidence_id"],
        "ok": True,
        "checks": [
            {"name": "authority_verified", "passed": True},
            {"name": "bounded_envelope_verified", "passed": True},
            {"name": "upstream_artifacts_verified", "passed": True},
            {"name": "claim_boundary_verified", "passed": True},
            {"name": "payload_inventory_verified", "passed": True},
        ],
        "blocking_reasons": [],
        "manifest_sha256": raw_file_sha256(root / MANIFEST_NAME),
    }
    if report != expected_report:
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "verification report")
    expected_receipt = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "stage_07_1_model_entrance_receipt",
        "artifact_version": SCHEMA_VERSION,
        "status": "published",
        "evidence_id": payload["evidence_id"],
        "manifest_sha256": raw_file_sha256(root / MANIFEST_NAME),
        "verification_report_sha256": raw_file_sha256(root / REPORT_NAME),
        "qualification_scope": "bounded_smoke_only",
        "recommendation_eligible": False,
    }
    if receipt != expected_receipt:
        _fail(Stage71FailureCode.EVIDENCE_VERIFICATION_FAILED, "receipt")


def _admit_entrance_authority(root: Path) -> tuple[Mapping[str, Any], Mapping[str, str]]:
    authority_path = _safe_authority_file(root, "configs/runtime/stage71/entrance_authority_v1.json")
    approval_path = _safe_authority_file(root, "configs/runtime/stage71/entrance_approval_v1.json")
    authority = _canonical(authority_path, Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID)
    approval = _canonical(approval_path, Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID)
    expected_authority_keys = {
        "schema_version", "artifact_type", "artifact_version", "status", "design", "stage4_1_approval",
        "stage5_1_approval", "source", "capability", "bounded_envelope", "authority_id",
    }
    if (
        set(authority) != expected_authority_keys
        or authority.get("schema_version") != SCHEMA_VERSION
        or authority.get("artifact_type") != "stage_07_1_model_entrance_authority"
        or authority.get("artifact_version") != SCHEMA_VERSION
        or authority.get("status") != "approved_bounded_only"
        or authority.get("capability") != _plain(CAPABILITY)
        or authority.get("bounded_envelope") != _plain(BOUNDED_ENVELOPE)
    ):
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority schema")
    authority_body = dict(authority)
    authority_id = authority_body.pop("authority_id")
    if authority_id != _canonical_sha256(authority_body):
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority_id")
    for name in ("design", "stage4_1_approval", "stage5_1_approval", "source"):
        row = authority[name]
        if not isinstance(row, Mapping) or set(row) != {"path", "raw_sha256"}:
            _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, name)
        path = _safe_authority_file(root, row["path"])
        if raw_file_sha256(path) != row["raw_sha256"]:
            _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, f"{name} hash")
    expected_approval_keys = {
        "schema_version", "artifact_type", "artifact_version", "status", "authority_path",
        "authority_raw_sha256", "authority_id", "design_path", "design_raw_sha256", "reviewer_role", "decision",
    }
    if (
        set(approval) != expected_approval_keys
        or approval.get("schema_version") != SCHEMA_VERSION
        or approval.get("artifact_type") != "stage_07_1_model_entrance_approval"
        or approval.get("artifact_version") != SCHEMA_VERSION
        or approval.get("status") != "approved_bounded_only"
        or approval.get("decision") != "bounded_pilot_only_stage6_registration_closed"
        or approval.get("reviewer_role") != "independent_test_reviewer"
        or approval.get("authority_path") != authority_path.relative_to(root).as_posix()
        or approval.get("authority_raw_sha256") != raw_file_sha256(authority_path)
        or approval.get("authority_id") != authority_id
        or approval.get("design_path") != authority["design"]["path"]
        or approval.get("design_raw_sha256") != authority["design"]["raw_sha256"]
    ):
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "approval")
    return MappingProxyType(authority), MappingProxyType(
        {
            "entrance_authority_id": authority_id,
            "entrance_authority_sha256": raw_file_sha256(authority_path),
            "stage4_1_approval_sha256": authority["stage4_1_approval"]["raw_sha256"],
            "stage5_1_approval_sha256": authority["stage5_1_approval"]["raw_sha256"],
        }
    )


def _production_stage41_context(expected, root: Path, output_root: Path):
    from sqvm.control.stage4_1_context import production_parameterized_control_context

    return production_parameterized_control_context(
        expected,
        repository_root=root,
        output_root=output_root,
    )


def _canonical(path: Path, code: Stage71FailureCode) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage71EntranceError(code, str(exc)) from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        _fail(code, f"noncanonical JSON: {path.name}")
    return value


def _safe_authority_file(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or not relative.isascii() or "\\" in relative:
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority path")
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority path")
    candidate = root.joinpath(*posix.parts)
    current = root
    try:
        for part in posix.parts:
            current /= part
            attributes = getattr(current.stat(), "st_file_attributes", 0)
            if current.is_symlink() or attributes & 0x400:
                _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority path is a link or reparse point")
        path = candidate.resolve(strict=True)
    except OSError as exc:
        raise Stage71EntranceError(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, str(exc)) from exc
    _inside(path, root, Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority path")
    if not path.is_file():
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "authority file")
    return path


def _safe_output_root(value: str | Path, root: Path) -> Path:
    path = Path(value)
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    _inside(path, root, Stage71FailureCode.EVIDENCE_PUBLICATION_CONFLICT, "output root")
    if path.exists() and (not path.is_dir() or path.is_symlink()):
        _fail(Stage71FailureCode.EVIDENCE_PUBLICATION_CONFLICT, "unsafe output root")
    return path


def _repository_root(value: str | Path | None) -> Path:
    root = Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]
    if not root.is_dir():
        _fail(Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID, "repository root")
    return root


def _inside(path: Path, root: Path, code: Stage71FailureCode, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise Stage71EntranceError(code, f"{label} is outside repository") from exc


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _fail(code: Stage71FailureCode, detail: str) -> None:
    raise Stage71EntranceError(code, detail)
