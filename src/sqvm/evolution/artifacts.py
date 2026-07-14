"""Small, atomic Stage 5 evidence publication for v0.2 profiles."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import time
import uuid
from typing import Any

import numpy as np

from sqvm.evolution.input import load_stage5_input
from sqvm.evolution.models import FormalScaleQualificationRequired, Stage5ArtifactSet, Stage5ScenarioResult
from sqvm.evolution.physics import evolve_stage5_scenario
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256


ARTIFACT_NAME = "qutip_evolution_artifacts.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "run_receipt.json"


def run_stage5_evolution(config_path: str | Path, output_dir: str | Path, repository_root: str | Path | None = None) -> Stage5ArtifactSet:
    """Publish a reconstructed-input Stage 5 run only after input admission succeeds."""

    stage5_input = load_stage5_input(config_path, repository_root)
    if stage5_input.admission.config.profile == "formal":
        raise FormalScaleQualificationRequired("formal numerical execution is excluded from v0.2 and requires separate qualification")
    target = Path(output_dir).resolve()
    try:
        target.relative_to(stage5_input.admission.repository_root)
    except ValueError as exc:
        raise ValueError("Stage 5 output directory must be inside the repository") from exc
    if target.exists():
        raise FileExistsError(f"Stage 5 output target already exists: {target}")
    started = time.perf_counter()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging.{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        results = [evolve_stage5_scenario(stage5_input, scenario_id) for scenario_id in stage5_input.admission.config.scenario_ids]
        numerical_ok = all(check["passed"] for result in results for check in result.checks)
        status = "smoke_complete" if numerical_ok else "numerical_failure"
        artifact = _artifact_payload(stage5_input, results, status)
        artifact_path = staging / ARTIFACT_NAME
        artifact_path.write_bytes(canonical_json_bytes(artifact))
        artifact_hash = raw_file_sha256(artifact_path)
        report = {
            "schema_version": "0.2", "artifact_type": "stage_05_qutip_evolution_verification_report", "artifact_version": "0.2",
            "execution_succeeded": True, "profile": stage5_input.admission.config.profile, "status": status,
            "acceptance_eligible": False, "publication_pending": False,
            "artifact_path": (target / ARTIFACT_NAME).relative_to(stage5_input.admission.repository_root).as_posix(),
            "artifact_sha256": artifact_hash, "snapshot_sha256": stage5_input.snapshot_sha256,
            "checks": [{"name": "input_snapshot_bound", "passed": True}, {"name": "scenarios_completed", "passed": True}],
            "blocking_reasons": [] if status == "smoke_complete" else ["Stage 5 numerical checks failed"],
        }
        report_path = staging / REPORT_NAME
        report_path.write_bytes(canonical_json_bytes(report))
        report_hash = raw_file_sha256(report_path)
        receipt = {
            "schema_version": "0.2", "artifact_type": "stage_05_qutip_evolution_run_receipt", "artifact_version": "0.2",
            "execution_succeeded": True, "profile": stage5_input.admission.config.profile, "status": status,
            "elapsed_seconds": time.perf_counter() - started, "snapshot_sha256": stage5_input.snapshot_sha256,
            "paths": {"artifact": (target / ARTIFACT_NAME).relative_to(stage5_input.admission.repository_root).as_posix(), "report": (target / REPORT_NAME).relative_to(stage5_input.admission.repository_root).as_posix()},
            "sha256": {"artifact": artifact_hash, "report": report_hash},
        }
        receipt_path = staging / RECEIPT_NAME
        receipt_path.write_bytes(canonical_json_bytes(receipt))
        receipt_hash = raw_file_sha256(receipt_path)
        os.replace(staging, target)
        return Stage5ArtifactSet(target, target / ARTIFACT_NAME, target / REPORT_NAME, target / RECEIPT_NAME, artifact_hash, report_hash, receipt_hash, status)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _artifact_payload(stage5_input, results: list[Stage5ScenarioResult], status: str) -> dict[str, Any]:
    return {
        "schema_version": "0.2", "artifact_type": "stage_05_qutip_evolution", "artifact_version": "0.2",
        "profile": stage5_input.admission.config.profile, "status": status, "acceptance_eligible": False,
        "acceptance_basis": "user_accepted_rebuild_snapshot_v1", "stage5_input_snapshot": stage5_input.snapshot,
        "scenario_order": list(stage5_input.admission.config.scenario_ids), "scenarios": [_scenario_payload(row) for row in results],
    }


def _scenario_payload(result: Stage5ScenarioResult) -> dict[str, Any]:
    return {
        "scenario_id": result.scenario_id, "calculation": result.calculation,
        "original_sample_count": result.original_sample_count, "window_start_index": result.window_start_index, "window_sample_count": result.window_sample_count,
        "edge_time_ns": result.edge_time_ns.tolist(), "control_digest": dict(result.control_digest),
        "initial_reference": dict(result.initial_reference),
        "states": [_complex_vector(value) for value in result.states],
        "populations": {label: values.tolist() for label, values in result.populations.items()},
        "leakage": result.leakage.tolist(), "norm_error": result.norm_error.tolist(), "checks": list(result.checks),
    }


def _complex_vector(values: np.ndarray) -> dict[str, Any]:
    return {"representation": "ket_charge_basis_v1", "dimension": int(values.size), "amplitudes": [{"re": float(value.real), "im": float(value.imag)} for value in values]}
