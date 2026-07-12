"""Canonical Stage 3.1 artifacts and fail-closed independent approval validation."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

import nbformat as nbf
from nbclient import NotebookClient

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256
from sqvm.spectrum.artifacts import assert_finite_json_payload
from sqvm.spectrum.solver import stage3_solver_source_tree_sha256
from sqvm.spectrum.stage31_config import load_q1_q2_coupling_config, q1_q2_config_to_dict, validate_stage31_design_freeze
from sqvm.spectrum.stage31_models import (
    ArtifactWriteResult,
    NotebookWriteResult,
    QubitCouplingSweepResult,
    Stage31ComputationalGate,
    Stage31VerificationReport,
    Stage4ReadinessReport,
    VerificationReportWriteResult,
)


def q1_q2_coupling_result_to_payload(result: QubitCouplingSweepResult) -> dict[str, Any]:
    if not isinstance(result, QubitCouplingSweepResult):
        raise TypeError("Stage 3.1 artifact writer requires QubitCouplingSweepResult")
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_1_q1_q2_coupling",
        "artifact_version": "0.1",
        "acceptance_eligible": result.config.acceptance_eligible,
        "provenance": result.provenance,
        "solver_backend": result.solver_backend,
        "idle_metrics": result.idle_metrics,
        "idle_convergence": result.idle_convergence,
        "scan_definition": q1_q2_config_to_dict(result.config)["spectrum"]["scan"],
        "coupler_points": list(result.finalized.points),
        "q1_q2_crossings": [row["raw_evidence"] for row in result.finalized.points],
        "coupling_modulation": {
            "reference_key": result.modulation.reference_key,
            "comparisons": list(result.modulation.comparisons),
            "passed": result.modulation.passed,
        },
        "runtime": _to_dict(result.runtime),
        "computational_gate": _to_dict(result.computational_gate),
        "checks": list(result.checks),
    }
    assert_finite_json_payload(payload)
    return payload


def write_q1_q2_coupling_artifacts(result: QubitCouplingSweepResult, output_dir: str | Path) -> ArtifactWriteResult:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "q1_q2_coupling_artifacts.json"
    raw = canonical_json_bytes(q1_q2_coupling_result_to_payload(result))
    _atomic_write(target, raw)
    return ArtifactWriteResult(True, target, _sha256_bytes(raw), True)


def write_q1_q2_coupling_notebook(artifact_path: str | Path, output_dir: str | Path) -> NotebookWriteResult:
    source = Path(artifact_path)
    target = Path(output_dir) / "verification.ipynb"
    raw = source.read_bytes()
    payload = json.loads(raw)
    if raw != canonical_json_bytes(payload):
        raise ValueError("Stage 3.1 notebook source artifact is not canonical")
    sections = (
        "provenance", "solver_backend", "idle_metrics", "idle_convergence",
        "scan_definition", "coupler_points", "coupling_modulation", "runtime", "computational_gate",
    )
    cells = [
        nbf.v4.new_markdown_cell(
            "# Stage 3.1 q1-q2 coupling verification\n\n"
            "Read-only view of `q1_q2_coupling_artifacts.json`; no gate is recomputed."
        ),
        nbf.v4.new_code_cell(
            "from pathlib import Path\n"
            "import json\n"
            "ARTIFACT = Path('q1_q2_coupling_artifacts.json')\n"
            "data = json.loads(ARTIFACT.read_text(encoding='utf-8'))\n"
            "data['provenance']"
        ),
    ]
    for key in sections[1:]:
        cells.append(nbf.v4.new_markdown_cell(f"## {key.replace('_', ' ').title()}"))
        cells.append(nbf.v4.new_code_cell(f"data[{key!r}]"))
    notebook = nbf.v4.new_notebook(cells=cells, metadata={"stage3_1_read_only": True})
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(name)
    try:
        executed = execute_stage3_1_notebook(notebook, target.parent)
        nbf.write(executed, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    loaded = nbf.read(target, as_version=4)
    code = [cell for cell in loaded.cells if cell.cell_type == "code"]
    errors = sum(output.output_type == "error" for cell in code for output in cell.outputs)
    executed = sum(cell.execution_count is not None for cell in code)
    return NotebookWriteResult(True, target, raw_file_sha256(target), len(code), executed, errors)


def execute_stage3_1_notebook(notebook, working_dir: str | Path):
    return NotebookClient(
        notebook,
        timeout=600,
        kernel_name="python3",
        resources={"metadata": {"path": str(Path(working_dir).resolve())}},
    ).execute()


def assemble_stage3_1_verification_report(computational_gate, artifact_write, notebook_write):
    computational_ready = computational_gate.computational_ready
    post_write_checks = (
        {"name": "artifact_write_completed", "passed": artifact_write.completed},
        {"name": "artifact_canonical_and_finite", "passed": artifact_write.canonical_and_finite},
        {"name": "notebook_write_completed", "passed": notebook_write.completed},
        {"name": "notebook_all_code_cells_executed", "passed": notebook_write.code_cell_count == notebook_write.executed_code_cell_count},
        {"name": "notebook_has_no_errors", "passed": notebook_write.error_output_count == 0},
    )
    ok = computational_ready and all(row["passed"] for row in post_write_checks)
    blockers = tuple(computational_gate.blocking_reasons) + tuple(
        row["name"] for row in post_write_checks if not row["passed"]
    )
    return Stage31VerificationReport(
        ok, True, ok, False, "pending", computational_gate, artifact_write, notebook_write,
        artifact_write.sha256, notebook_write.sha256, tuple(computational_gate.checks),
        post_write_checks, blockers,
    )


def write_stage3_1_verification_report(report: Stage31VerificationReport, output_dir: str | Path) -> VerificationReportWriteResult:
    target = Path(output_dir) / "verification_report.json"
    payload = report.to_dict()
    assert_finite_json_payload(payload)
    raw = canonical_json_bytes(payload)
    _atomic_write(target, raw)
    return VerificationReportWriteResult(True, target, _sha256_bytes(raw))


def publish_stage3_1_transaction(result: QubitCouplingSweepResult, report_builder, output_dir: str | Path):
    """Publish the three formal files atomically into a previously absent directory."""
    target = Path(output_dir)
    if target.exists():
        raise FileExistsError("formal Stage 3.1 output directory must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.staging."))
    try:
        artifact = write_q1_q2_coupling_artifacts(result, staging)
        notebook = write_q1_q2_coupling_notebook(artifact.path, staging)
        final_artifact = replace(artifact, path=target / artifact.path.name)
        final_notebook = replace(notebook, path=target / notebook.path.name)
        report = report_builder(final_artifact, final_notebook)
        write_stage3_1_verification_report(report, staging)
        os.replace(staging, target)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def validate_stage3_1_acceptance_approval(config_path, artifact_path, notebook_path, verification_report_path, approval_path):
    paths = [Path(value) for value in (artifact_path, notebook_path, verification_report_path, approval_path)]
    config = load_q1_q2_coupling_config(config_path)
    errors: list[str] = []
    approval = _load_canonical(paths[3], "acceptance approval", errors)
    report = _load_canonical(paths[2], "verification report", errors)
    artifact = _load_canonical(paths[0], "coupling artifact", errors)
    exact_keys = {
        "schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role",
        "blocking_findings", "review_record_path", "review_record_sha256", "config_sha256",
        "design_freeze_manifest_sha256", "stage3_1_source_tree_sha256", "solver_validation_sha256",
        "solver_validation_approval_sha256", "q1_q2_coupling_artifact_sha256",
        "verification_notebook_sha256", "verification_report_sha256",
    }
    if set(approval) != exact_keys:
        errors.append("acceptance approval keys are not exact")
    identity = {
        "schema_version": "0.1", "artifact_type": "stage_03_1_q1_q2_coupling_acceptance_approval",
        "artifact_version": "0.1", "decision": "approved",
        "reviewer_role": "independent_test_review_ai", "blocking_findings": [],
    }
    for key, expected in identity.items():
        if approval.get(key) != expected:
            errors.append(f"acceptance approval {key} mismatch")
    root = find_repository_root(config.source_path)
    freeze = validate_stage31_design_freeze(config)
    bindings = {
        "config_sha256": raw_file_sha256(config.source_path),
        "design_freeze_manifest_sha256": freeze["sha256"],
        "stage3_1_source_tree_sha256": stage3_solver_source_tree_sha256(root),
        "solver_validation_sha256": _safe_hash(config.runtime.solver_validation_artifact, errors),
        "solver_validation_approval_sha256": _safe_hash(config.runtime.solver_validation_approval, errors),
        "q1_q2_coupling_artifact_sha256": _safe_hash(paths[0], errors),
        "verification_notebook_sha256": _safe_hash(paths[1], errors),
        "verification_report_sha256": _safe_hash(paths[2], errors),
    }
    review_path = approval.get("review_record_path")
    if not isinstance(review_path, str) or not review_path:
        errors.append("review_record_path must be non-empty")
    else:
        bindings["review_record_sha256"] = _safe_hash(root / review_path, errors)
    for key, actual in bindings.items():
        if approval.get(key) != actual:
            errors.append(f"acceptance approval {key} does not match current bytes")
    if report.get("ok") is not True or report.get("acceptance_candidate_ready") is not True:
        errors.append("verification report is not acceptance ready")
    if artifact.get("artifact_type") != "stage_03_1_q1_q2_coupling":
        errors.append("coupling artifact identity mismatch")
    ok = not errors
    return Stage4ReadinessReport(
        ok, ok, str(approval.get("decision", "missing")), _safe_hash(paths[3], []) if paths[3].exists() else None,
        {key: value for key, value in bindings.items() if value is not None},
        tuple({"name": "acceptance_approval_valid", "passed": ok} for _ in range(1)), tuple(errors),
    )


def _load_canonical(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("root is not a mapping")
        if raw != canonical_json_bytes(payload):
            errors.append(f"{label} bytes are not canonical")
        return payload
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"cannot load {label}: {exc}")
        return {}


def _safe_hash(path: Path, errors: list[str]) -> str | None:
    try:
        return raw_file_sha256(path)
    except OSError as exc:
        errors.append(f"cannot hash {path.as_posix()}: {exc}")
        return None


def _to_dict(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return {key: _to_dict(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Mapping):
        return {key: _to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_dict(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()
