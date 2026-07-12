"""Stage 4 control artifact transaction and acceptance validation."""

from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping
import uuid

import nbformat as nbf
import numpy as np

from sqvm.control.compatibility import _preflight_notebook_runtime
from sqvm.control.registry import ADDED_CHANNEL_ORDER, load_control_channel_registry
from sqvm.control.stage4_compile import FORMAL_CHECKS, FORMAL_SCENARIOS, SMOKE_CHECKS, _normalized_config, _quantize
from sqvm.control.stage4_config import load_control_chain_config
from sqvm.control.stage4_models import ControlArtifactSet, ControlCompilationResult, Stage5ReadinessReport
from sqvm.control.stage4_provenance import stage4_source_tree_sha256
from sqvm.control.stage4_provenance import stage4_environment_fingerprint
from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256


ARTIFACT_NAME = "control_signal_artifacts.json"
NOTEBOOK_NAME = "verification.ipynb"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "run_receipt.json"
DEVELOPMENT_FILES = {ARTIFACT_NAME, NOTEBOOK_NAME, REPORT_NAME, RECEIPT_NAME}
APPROVED_FILES = DEVELOPMENT_FILES | {"acceptance_approval.json"}
STAGING_CHECKS = (
    "staged_artifact_exists_and_hash_matches", "artifact_canonical_and_finite",
    "staged_notebook_exists_and_hash_matches", "notebook_executed_without_errors",
    "reported_paths_equal_expected_final_paths",
)
PUBLICATION_CHECKS = (
    "final_directory_exact_three_before_receipt", "final_artifact_exists_and_hash_matches",
    "final_notebook_exists_and_hash_matches", "final_report_exists_and_hash_matches",
    "no_staging_or_partial_files",
)
ARTIFACT_KEYS = {"schema_version", "artifact_type", "artifact_version", "profile", "acceptance_eligible", "status", "provenance", "registry", "control_config", "scenario_order", "scenarios", "global_metrics", "phase_proxy", "runtime", "computational_gate"}
REPORT_KEYS = {"schema_version", "artifact_type", "artifact_version", "execution_succeeded", "profile", "computational_ready", "acceptance_eligible", "status", "publication_pending", "stage5_ready", "approval_status", "artifact_path", "artifact_sha256", "notebook_path", "notebook_sha256", "computational_checks", "staging_checks", "blocking_reasons"}
RECEIPT_KEYS = {"schema_version", "artifact_type", "artifact_version", "execution_succeeded", "profile", "acceptance_eligible", "acceptance_candidate_ready", "paths", "sha256", "analysis_elapsed_seconds", "total_elapsed_seconds_to_receipt_assembly", "total_runtime_budget_seconds", "total_runtime_within_budget", "publication_checks", "blocking_reasons"}
APPROVAL_HASH_FIELDS = (
    "stage4_design_freeze_manifest_sha256", "control_channel_approval_sha256", "control_config_sha256",
    "logical_schedule_sha256", "stage4_source_tree_sha256", "stage3_1_acceptance_approval_sha256",
    "control_signal_artifact_sha256", "verification_notebook_sha256", "verification_report_sha256",
    "run_receipt_sha256", "review_record_sha256",
)
APPROVAL_KEYS = {"schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role", "blocking_findings", *APPROVAL_HASH_FIELDS, "review_record_path"}


def build_stage4_acceptance_approval(
    *,
    artifact_path: str | Path,
    notebook_path: str | Path,
    report_path: str | Path,
    run_receipt_path: str | Path,
    review_record_path: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build, but never write, an approval bound to current candidate bytes."""

    candidate_paths = tuple(Path(value).resolve() for value in (artifact_path, notebook_path, report_path, run_receipt_path))
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(candidate_paths[0])
    artifact, _, _ = _validated_candidate_evidence(candidate_paths, root, DEVELOPMENT_FILES)
    review = _resolve_inside(review_record_path, root)
    if not review.is_file() or review.stat().st_size <= 0:
        raise ValueError("Stage 4 review record must be a non-empty regular file")
    actual = _approval_current_hashes(candidate_paths, review, root)
    provenance_sha = artifact["provenance"]["sha256"]
    expected = {
        "stage4_design_freeze_manifest_sha256": provenance_sha["stage4_design_freeze_manifest_sha256"],
        "control_channel_approval_sha256": provenance_sha["channel_registry_approval_sha256"],
        "control_config_sha256": provenance_sha["control_config_sha256"],
        "logical_schedule_sha256": provenance_sha["logical_schedule_sha256"],
        "stage4_source_tree_sha256": provenance_sha["stage4_source_tree_sha256"],
        "stage3_1_acceptance_approval_sha256": provenance_sha["stage3_1_approval_sha256"],
    }
    if any(actual[key] != value for key, value in expected.items()):
        raise ValueError("Stage 4 approval evidence does not match candidate provenance")
    return {
        "schema_version": "0.1", "artifact_type": "stage_04_control_signal_acceptance_approval",
        "artifact_version": "0.1", "decision": "approved",
        "reviewer_role": "independent_test_review_ai", "blocking_findings": [],
        **actual, "review_record_path": review.relative_to(root).as_posix(),
    }


def write_control_signal_artifacts(result: ControlCompilationResult, output_dir: str | Path) -> ControlArtifactSet:
    if not isinstance(result, ControlCompilationResult):
        raise TypeError("artifact writer input must be ControlCompilationResult")
    payload = deepcopy(result.to_dict())
    _refresh_forward_reference_rows(payload)
    _finite(payload)
    _validate_artifact_payload(payload)
    artifact_bytes = canonical_json_bytes(payload)
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"control signal output target already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging.{uuid.uuid4().hex}"
    root = find_repository_root(target)
    artifact_path = staging / ARTIFACT_NAME
    notebook_path = staging / NOTEBOOK_NAME
    report_path = staging / REPORT_NAME
    try:
        staging.mkdir()
        artifact_path.write_bytes(artifact_bytes)
        artifact_hash = raw_file_sha256(artifact_path)
        notebook = _execute_notebook(_new_notebook(), staging)
        nbf.write(notebook, notebook_path)
        _validate_notebook(notebook_path, replay=False)
        notebook_hash = raw_file_sha256(notebook_path)
        final_artifact = target / ARTIFACT_NAME
        final_notebook = target / NOTEBOOK_NAME
        checks = [{"name": name, "passed": True, "message": "passed"} for name in STAGING_CHECKS]
        report = {
            "schema_version": "0.1", "artifact_type": "stage_04_control_signal_verification_report", "artifact_version": "0.1",
            "execution_succeeded": True, "profile": payload["profile"],
            "computational_ready": payload["computational_gate"]["computational_ready"],
            "acceptance_eligible": payload["acceptance_eligible"], "status": payload["status"],
            "publication_pending": True, "stage5_ready": False, "approval_status": "pending",
            "artifact_path": _relative(final_artifact, root), "artifact_sha256": artifact_hash,
            "notebook_path": _relative(final_notebook, root), "notebook_sha256": notebook_hash,
            "computational_checks": payload["computational_gate"]["checks"], "staging_checks": checks,
            "blocking_reasons": payload["computational_gate"]["blocking_reasons"],
        }
        report_path.write_bytes(canonical_json_bytes(report))
        report_hash = raw_file_sha256(report_path)
        _validate_report_payload(report, payload, artifact_hash, notebook_hash)
        os.replace(staging, target)
        return ControlArtifactSet(target / ARTIFACT_NAME, target / NOTEBOOK_NAME, target / REPORT_NAME, artifact_hash, notebook_hash, report_hash)
    except Exception:
        if staging.exists(): shutil.rmtree(staging)
        raise


def validate_stage4_acceptance_approval(
    approval_path: str | Path,
    artifact_path: str | Path,
    notebook_path: str | Path,
    report_path: str | Path,
    run_receipt_path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Stage5ReadinessReport:
    paths = [Path(value).resolve() for value in (approval_path, artifact_path, notebook_path, report_path, run_receipt_path)]
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(paths[0])
    errors: list[str] = []
    actual = {key: "" for key in APPROVAL_HASH_FIELDS}
    approval: dict[str, Any] = {}
    artifact: dict[str, Any] = {}
    report: dict[str, Any] = {}
    receipt: dict[str, Any] = {}
    try:
        approval = _load_canonical(paths[0], "Stage 4 approval")
        artifact, report, receipt = _validated_candidate_evidence(tuple(paths[1:]), root, APPROVED_FILES, approval_path=paths[0])
    except (OSError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    review_value = approval.get("review_record_path")
    review = None
    try:
        if isinstance(review_value, str) and review_value:
            review = _resolve_inside(review_value, root)
            actual.update(_approval_current_hashes(tuple(paths[1:]), review, root))
    except (OSError, TypeError, ValueError):
        pass
    checks = []
    check_names = (
        "design_freeze_hash_matches", "channel_approval_hash_matches", "config_hash_matches", "schedule_hash_matches",
        "source_hash_matches", "stage3_1_approval_hash_matches", "artifact_hash_matches", "notebook_hash_matches",
        "report_hash_matches", "run_receipt_hash_matches", "review_hash_matches",
    )
    for name, key in zip(check_names, APPROVAL_HASH_FIELDS, strict=True):
        passed = approval.get(key) == actual[key] and bool(actual[key])
        checks.append(_check(name, passed))
    candidate_ready = _formal_candidate_ready(artifact, report, receipt)
    checks.append(_check("formal_acceptance_candidate_ready", candidate_ready))
    contract = set(approval) == APPROVAL_KEYS and _identity(approval, "stage_04_control_signal_acceptance_approval") and approval.get("decision") == "approved" and approval.get("reviewer_role") == "independent_test_review_ai" and approval.get("blocking_findings") == []
    checks.append(_check("approval_contract_valid", contract))
    directory = paths[0].parent
    exact_files = directory.is_dir() and {row.name for row in directory.iterdir() if row.is_file()} == APPROVED_FILES and all(row.is_file() for row in directory.iterdir())
    checks.append(_check("exact_five_file_set", exact_files))
    blockers = tuple([*errors, *(row["message"] for row in checks if not row["passed"])])
    valid = contract and all(row["passed"] for row in checks)
    ok = valid and not blockers
    return Stage5ReadinessReport(ok, ok, str(approval.get("decision", "missing")), valid, actual, tuple(checks), blockers)


def validate_control_development_set(directory: str | Path, *, repository_root: str | Path | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    target = Path(directory).resolve()
    if not target.is_dir() or {row.name for row in target.iterdir() if row.is_file()} != DEVELOPMENT_FILES or any(not row.is_file() for row in target.iterdir()):
        raise ValueError("Stage 4 development directory must be exact-four")
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(target)
    artifact = _load_canonical(target / ARTIFACT_NAME, "Stage 4 artifact")
    report = _load_canonical(target / REPORT_NAME, "Stage 4 report")
    receipt = _load_canonical(target / RECEIPT_NAME, "Stage 4 run receipt")
    _validate_artifact_payload(artifact)
    _validate_current_provenance(artifact, root)
    _validate_notebook(target / NOTEBOOK_NAME)
    _validate_report_payload(report, artifact, raw_file_sha256(target / ARTIFACT_NAME), raw_file_sha256(target / NOTEBOOK_NAME))
    _validate_receipt_payload(receipt, artifact, report, target / ARTIFACT_NAME, target / NOTEBOOK_NAME, target / REPORT_NAME, root)
    return artifact, report, receipt


def _validated_candidate_evidence(
    paths: tuple[Path, Path, Path, Path],
    root: Path,
    expected_files: set[str],
    *,
    approval_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    expected_names = (ARTIFACT_NAME, NOTEBOOK_NAME, REPORT_NAME, RECEIPT_NAME)
    if tuple(path.name for path in paths) != expected_names or len({path.parent for path in paths}) != 1:
        raise ValueError("Stage 4 candidate paths must be exact names in one directory")
    directory = paths[0].parent
    if approval_path is not None and (approval_path.name != "acceptance_approval.json" or approval_path.parent != directory):
        raise ValueError("Stage 4 approval path must be the sibling acceptance_approval.json")
    if not directory.is_dir() or {row.name for row in directory.iterdir()} != expected_files or any(not row.is_file() for row in directory.iterdir()):
        raise ValueError(f"Stage 4 candidate directory must be exact-{len(expected_files)}")
    artifact = _load_canonical(paths[0], "Stage 4 artifact")
    report = _load_canonical(paths[2], "Stage 4 report")
    receipt = _load_canonical(paths[3], "Stage 4 run receipt")
    _validate_artifact_payload(artifact)
    _validate_current_provenance(artifact, root)
    _validate_notebook(paths[1])
    _validate_report_payload(report, artifact, raw_file_sha256(paths[0]), raw_file_sha256(paths[1]))
    _validate_receipt_payload(receipt, artifact, report, paths[0], paths[1], paths[2], root)
    if not _formal_candidate_ready(artifact, report, receipt):
        raise ValueError("Stage 4 formal acceptance candidate is not ready")
    return artifact, report, receipt


def _resolve_inside(value: str | Path, root: Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError("Stage 4 path must be str or Path")
    path = Path(value)
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("Stage 4 review record must resolve inside repository") from exc
    return resolved


def _approval_current_hashes(paths: tuple[Path, Path, Path, Path], review: Path, root: Path) -> dict[str, str]:
    if not review.is_file() or review.stat().st_size <= 0:
        raise ValueError("Stage 4 review record must be a non-empty regular file")
    sources = {
        "stage4_design_freeze_manifest_sha256": root / "docs/decisions/2026-07-11-stage4-design-freeze.json",
        "control_channel_approval_sha256": root / "output/stage_04_0_control_channel_rebaseline/control_channel_approval.json",
        "control_config_sha256": root / "configs/control/2q1c2r_control.yaml",
        "logical_schedule_sha256": root / "configs/control/2q1c2r_control_demo.yaml",
        "stage3_1_acceptance_approval_sha256": root / "output/stage_03_1_q1_q2_coupling/acceptance_approval.json",
        "control_signal_artifact_sha256": paths[0], "verification_notebook_sha256": paths[1],
        "verification_report_sha256": paths[2], "run_receipt_sha256": paths[3],
        "review_record_sha256": review,
    }
    result = {key: raw_file_sha256(path) for key, path in sources.items()}
    result["stage4_source_tree_sha256"] = stage4_source_tree_sha256(root)
    if set(result) != set(APPROVAL_HASH_FIELDS):
        raise ValueError("Stage 4 approval hash fields are not exact")
    return result


def _validate_artifact_payload(payload: Mapping[str, Any]) -> None:
    if set(payload) != ARTIFACT_KEYS or not _identity(payload, "stage_04_control_signal"):
        raise ValueError("Stage 4 artifact root fields or identity are invalid")
    profile = payload.get("profile")
    if profile not in {"formal", "smoke"} or payload.get("acceptance_eligible") is not (profile == "formal"):
        raise ValueError("Stage 4 artifact profile contract mismatch")
    expected = FORMAL_SCENARIOS if profile == "formal" else FORMAL_SCENARIOS[:2]
    if payload.get("scenario_order") != list(expected): raise ValueError("Stage 4 scenario order mismatch")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list) or [row.get("scenario_id") if isinstance(row, Mapping) else None for row in scenarios] != list(expected): raise ValueError("Stage 4 scenarios mismatch")
    config = payload.get("control_config")
    if not isinstance(config, Mapping) or not isinstance(config.get("dac"), Mapping): raise ValueError("Stage 4 persisted config is invalid")
    dac = config["dac"].copy(); dac["lsb_V"] = Decimal(str(dac["lsb_V"]))
    for scenario in scenarios:
        if set(scenario) != {"scenario_id", "duration_ns", "desired_sample_count", "awg_sample_count", "effective_sample_count", "logical_pulses", "logical_targets", "awg", "effective", "metrics", "checks"}: raise ValueError("Stage 4 scenario fields are not exact")
        n, n_awg, p_count = scenario["desired_sample_count"], scenario["awg_sample_count"], scenario["effective_sample_count"]
        if isinstance(n, bool) or not isinstance(n, int) or n <= 0 or n_awg != n + 26 or p_count != n_awg + 2 + 26: raise ValueError("Stage 4 scenario sample formulas mismatch")
        awg = scenario.get("awg")
        if not isinstance(awg, Mapping) or awg.get("lane_order") != list(config["lane_order"]): raise ValueError("Stage 4 AWG contract mismatch")
        lanes = awg.get("lanes")
        if not isinstance(lanes, Mapping) or set(lanes) != set(config["lane_order"]): raise ValueError("Stage 4 AWG lane mapping mismatch")
        for lane in config["lane_order"]:
            row = lanes[lane]
            if not isinstance(row, Mapping) or set(row) != {"requested_V", "codes", "reconstructed_V", "delivered_after_fir_latency_V"}: raise ValueError("Stage 4 AWG lane fields are not exact")
            if len(row["requested_V"]) != n_awg or len(row["codes"]) != n_awg or len(row["reconstructed_V"]) != n_awg or len(row["delivered_after_fir_latency_V"]) != p_count: raise ValueError("Stage 4 AWG lane lengths mismatch")
            expected_codes, expected_reconstructed = _quantize(np.asarray(row["requested_V"], dtype=float), dac)
            if row["codes"] != [int(value) for value in expected_codes] or not np.array_equal(np.asarray(row["reconstructed_V"]), expected_reconstructed): raise ValueError(f"Stage 4 DAC reference mismatch:{lane}")
            if any(isinstance(code, bool) or not isinstance(code, int) or code < dac["code_min"] or code > dac["code_max"] for code in row["codes"]): raise ValueError(f"Stage 4 DAC code type/range mismatch:{lane}")
        _validate_scenario_evidence(scenario, config, dac)
    _validate_global_evidence(payload, config)
    _validate_phase_proxy(payload.get("phase_proxy"))
    gate = payload.get("computational_gate")
    names = FORMAL_CHECKS if profile == "formal" else SMOKE_CHECKS
    values = {
        "stage3_1_readiness_valid": True, "stage4_0_channel_registry_ready": True,
        "design_and_config_provenance_valid": True, "profile_acceptance_contract_valid": True,
        "schedule_schema_valid": True, "schedule_conflict_free": True,
        "sample_grid_exact": all(_scenario_check(row, "sample_lengths_valid") for row in scenarios),
        "static_matrices_valid": all(row["condition_number_2"] <= config["acceptance"]["max_condition_number"] for row in config["static_mixing"].values()),
        "no_dac_clipping": all(_scenario_check(row, "dac_codes_in_range") for row in scenarios),
        "quantization_error_within_bound": payload["global_metrics"]["quantization_error_within_bound"],
        "latency_alignment_exact": payload["global_metrics"]["latency_alignment_exact"],
        "forward_reconstruction_matches_reference": payload["global_metrics"]["forward_reconstruction_matches_reference"],
        "xy_area_error_within_budget": payload["global_metrics"]["xy_area_error_within_budget"],
        "z_target_error_within_budget": payload["global_metrics"]["z_target_error_within_budget"],
        "readout_area_error_within_budget": payload["global_metrics"]["readout_area_error_within_budget"],
        "stage3_phase_proxy_within_budget": payload["phase_proxy"]["passed"],
        "runtime_within_budget": payload.get("runtime", {}).get("analysis_runtime_within_budget") is True,
    }
    expected_checks = [{"name": name, "passed": bool(values[name]), "message": "passed" if values[name] else f"{name} failed"} for name in names]
    expected_status = "ready_for_stage5_review" if profile == "formal" else "smoke_complete"
    if not isinstance(gate, Mapping) or set(gate) != {"computational_ready", "status", "checks", "blocking_reasons"} or gate != {"computational_ready": True, "status": expected_status, "checks": expected_checks, "blocking_reasons": []} or payload.get("status") != expected_status:
        raise ValueError("Stage 4 computational gate is not ready or exact")
    runtime = payload.get("runtime")
    if not isinstance(runtime, Mapping) or set(runtime) != {"analysis_elapsed_seconds", "analysis_runtime_budget_seconds", "analysis_runtime_within_budget"} or isinstance(runtime["analysis_elapsed_seconds"], bool) or not isinstance(runtime["analysis_elapsed_seconds"], (int, float)) or runtime["analysis_elapsed_seconds"] < 0 or runtime["analysis_runtime_budget_seconds"] != config["acceptance"]["analysis_runtime_budget_seconds"] or runtime["analysis_runtime_within_budget"] is not (runtime["analysis_elapsed_seconds"] <= runtime["analysis_runtime_budget_seconds"]):
        raise ValueError("Stage 4 runtime evidence is invalid")
    _finite(payload)


def _validate_current_provenance(artifact: Mapping[str, Any], root: Path) -> None:
    provenance = artifact.get("provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != {"paths", "sha256"}:
        raise ValueError("Stage 4 provenance fields are not exact")
    paths = provenance.get("paths")
    hashes = provenance.get("sha256")
    path_keys = {"stage4_design_freeze_manifest", "channel_registry", "channel_registry_approval", "control_config", "logical_schedule", "stage3_1_artifact", "stage3_1_approval"}
    sha_keys = {f"{key}_sha256" for key in path_keys} | {"stage4_source_tree_sha256", "environment_fingerprint_sha256"}
    if not isinstance(paths, Mapping) or set(paths) != path_keys or not isinstance(hashes, Mapping) or set(hashes) != sha_keys:
        raise ValueError("Stage 4 provenance path/hash fields are not exact")
    profile = artifact.get("profile")
    expected_paths = {
        "stage4_design_freeze_manifest": "docs/decisions/2026-07-11-stage4-design-freeze.json",
        "channel_registry": "configs/control/2q1c2r_channels.yaml",
        "channel_registry_approval": "output/stage_04_0_control_channel_rebaseline/control_channel_approval.json",
        "control_config": f"configs/control/2q1c2r_control{'_smoke' if profile == 'smoke' else ''}.yaml",
        "logical_schedule": f"configs/control/2q1c2r_control_demo{'_smoke' if profile == 'smoke' else ''}.yaml",
        "stage3_1_artifact": "output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json",
        "stage3_1_approval": "output/stage_03_1_q1_q2_coupling/acceptance_approval.json",
    }
    if paths != expected_paths:
        raise ValueError("Stage 4 provenance paths mismatch")
    for key, relative in expected_paths.items():
        if hashes.get(f"{key}_sha256") != raw_file_sha256(root / relative):
            raise ValueError(f"Stage 4 provenance hash mismatch:{key}")
    if hashes.get("stage4_source_tree_sha256") != stage4_source_tree_sha256(root):
        raise ValueError("Stage 4 provenance source hash mismatch")
    _, environment_sha = stage4_environment_fingerprint()
    if hashes.get("environment_fingerprint_sha256") != environment_sha:
        raise ValueError("Stage 4 provenance environment hash mismatch")
    config = load_control_chain_config(root / expected_paths["control_config"])
    if artifact.get("control_config") != _normalized_config(config):
        raise ValueError("Stage 4 persisted config does not match current config")
    registry = load_control_channel_registry(root / expected_paths["channel_registry"])
    expected_registry = {
        "channel_order": [row.name for row in registry.channels],
        "channels": {row.name: {**row.to_dict(), "origin": "stage4_extension" if row.name in ADDED_CHANNEL_ORDER else "stage1_base"} for row in registry.channels},
    }
    if artifact.get("registry") != expected_registry:
        raise ValueError("Stage 4 persisted registry does not match current registry")
    stage3 = _load_canonical(root / expected_paths["stage3_1_artifact"], "Stage 3.1 artifact")
    idle = stage3.get("idle_convergence")
    if not isinstance(idle, Mapping) or artifact.get("phase_proxy", {}).get("source_value_MHz") != idle.get("max_frequency_drift_MHz"):
        raise ValueError("Stage 4 phase proxy is not bound to current Stage 3.1 evidence")


def _validate_scenario_evidence(scenario: Mapping[str, Any], config: Mapping[str, Any], dac: Mapping[str, Any]) -> None:
    n = scenario["desired_sample_count"]
    n_awg = scenario["awg_sample_count"]
    p_count = scenario["effective_sample_count"]
    dt = config["clock"]["dt_ns"]
    lane_order = config["lane_order"]
    lanes = scenario["awg"]["lanes"]
    expected_awg_time = [(index - 26 + 0.5) * dt for index in range(n_awg)]
    expected_effective_time = [(index - 26 + 0.5) * dt for index in range(p_count)]
    if scenario["awg"].get("time_center_ns") != expected_awg_time:
        raise ValueError("Stage 4 AWG time grid mismatch")
    _validate_signal_lengths(scenario.get("logical_targets"), p_count, expected_effective_time, "logical targets")
    _validate_signal_lengths(scenario.get("effective"), p_count, expected_effective_time, "effective signals")
    effective = scenario["effective"]
    expected_quant, expected_forward = _recomputed_coordinate_rows(scenario, config, dac)
    metrics = scenario.get("metrics")
    if not isinstance(metrics, Mapping) or set(metrics) != {"quantization_rows", "forward_reference_rows", "z_target_rows", "latency_alignment_error_samples"}:
        raise ValueError("Stage 4 scenario metrics fields are not exact")
    if metrics["quantization_rows"] != expected_quant or metrics["forward_reference_rows"] != expected_forward:
        raise ValueError("Stage 4 scenario coordinate rows mismatch")
    if metrics["latency_alignment_error_samples"] != 0:
        raise ValueError("Stage 4 latency alignment mismatch")
    z_rows = metrics["z_target_rows"]
    if not isinstance(z_rows, list) or any(not isinstance(row, Mapping) or set(row) != {"scenario_id", "pulse_id", "channel", "target_flux_phi0", "plateau_start_index", "plateau_stop_index", "max_abs_error_phi0", "threshold_phi0", "passed", "unavailable_reason"} for row in z_rows):
        raise ValueError("Stage 4 Z target rows are invalid")
    expected_z_rows = []
    for pulse in sorted((row for row in scenario["logical_pulses"] if row.get("kind") == "z"), key=lambda row: row["pulse_id"]):
        rise = int(Decimal(str(pulse["rise_ns"])) / Decimal(str(dt)))
        start = int(Decimal(str(pulse["start_ns"])) / Decimal(str(dt)))
        duration = int(Decimal(str(pulse["duration_ns"])) / Decimal(str(dt)))
        plateau_start = 26 + start + rise + 2
        plateau_stop = 26 + start + duration - rise - 2
        mode = {"q1_z": "q1", "q2_z": "q2", "c_z": "c"}[pulse["channel"]]
        error = float(np.max(np.abs(np.asarray(effective["absolute_flux_phi0"][mode])[plateau_start:plateau_stop] - pulse["target_flux_phi0"])))
        threshold = config["acceptance"]["max_z_flat_top_error_phi0"]
        expected_z_rows.append({"scenario_id": scenario["scenario_id"], "pulse_id": pulse["pulse_id"], "channel": pulse["channel"], "target_flux_phi0": pulse["target_flux_phi0"], "plateau_start_index": plateau_start, "plateau_stop_index": plateau_stop, "max_abs_error_phi0": error, "threshold_phi0": threshold, "passed": bool(error <= threshold), "unavailable_reason": None})
    if z_rows != expected_z_rows:
        raise ValueError("Stage 4 Z target row recomputation mismatch")
    checks = scenario.get("checks")
    names = ("sample_lengths_valid", "dac_codes_in_range", "forward_reference_matches", "scenario_error_budgets_passed")
    values = (True, True, all(row["passed"] for row in expected_forward), all(row["passed"] for row in expected_quant) and all(row["passed"] for row in z_rows))
    expected_checks = [{"name": name, "passed": bool(value), "message": "passed" if value else f"{name} failed"} for name, value in zip(names, values, strict=True)]
    if checks != expected_checks:
        raise ValueError("Stage 4 scenario checks mismatch")


def _refresh_forward_reference_rows(payload: Mapping[str, Any]) -> None:
    config = payload.get("control_config")
    scenarios = payload.get("scenarios")
    if not isinstance(config, Mapping) or not isinstance(config.get("dac"), Mapping) or not isinstance(scenarios, list):
        raise ValueError("Stage 4 artifact cannot refresh forward evidence")
    dac = config["dac"].copy()
    dac["lsb_V"] = Decimal(str(dac["lsb_V"]))
    for scenario in scenarios:
        if not isinstance(scenario, Mapping) or not isinstance(scenario.get("metrics"), Mapping):
            raise ValueError("Stage 4 scenario cannot refresh forward evidence")
        _, forward_rows = _recomputed_coordinate_rows(scenario, config, dac)
        scenario["metrics"]["forward_reference_rows"] = forward_rows


def _recomputed_coordinate_rows(scenario: Mapping[str, Any], config: Mapping[str, Any], dac: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    p_count = scenario["effective_sample_count"]
    lane_order = config["lane_order"]
    lanes = scenario["awg"]["lanes"]
    expected_delivered: dict[str, np.ndarray] = {}
    analog_delivered: dict[str, np.ndarray] = {}
    for lane in lane_order:
        spec = config["lanes"][lane]
        requested = np.asarray(lanes[lane]["requested_V"], dtype=float)
        reconstructed = np.asarray(lanes[lane]["reconstructed_V"], dtype=float)
        persisted = np.asarray(lanes[lane]["delivered_after_fir_latency_V"], dtype=float)
        if requested.ndim != 1 or reconstructed.ndim != 1 or persisted.ndim != 1 or not np.all(np.isfinite(requested)) or not np.all(np.isfinite(reconstructed)) or not np.all(np.isfinite(persisted)):
            raise ValueError(f"Stage 4 lane signal shape/finite contract mismatch:{lane}")
        native = np.concatenate((np.zeros(spec["latency_samples"]), np.convolve(reconstructed, np.asarray(spec["fir"], dtype=float), mode="full")))
        expected = np.zeros(p_count, dtype=float)
        expected[:native.size] = native
        if float(np.max(np.abs(expected - persisted))) > 1e-15:
            raise ValueError(f"Stage 4 delivered signal mismatch:{lane}")
        analog_native = np.concatenate((np.zeros(spec["latency_samples"]), np.convolve(requested, np.asarray(spec["fir"], dtype=float), mode="full")))
        analog = np.zeros(p_count, dtype=float)
        analog[:analog_native.size] = analog_native
        expected_delivered[lane] = expected
        analog_delivered[lane] = analog
    expected_quant: list[dict[str, Any]] = []
    expected_forward: list[dict[str, Any]] = []
    for group in ("xy", "z", "readout"):
        spec = config["static_mixing"][group]
        matrix = np.asarray(spec["matrix"], dtype=float)
        expected_delta = np.stack([matrix @ np.array([expected_delivered[lane][index] for lane in spec["input_lanes"]]) for index in range(p_count)])
        analog_delta = np.stack([matrix @ np.array([analog_delivered[lane][index] for lane in spec["input_lanes"]]) for index in range(p_count)])
        expected_effective = expected_delta
        if group == "z":
            idle = np.array([config["idle_flux_phi0"][key] for key in ("q1", "q2", "c")])
            expected_effective = expected_delta + idle
        persisted = _effective_matrix(scenario["effective"], group)
        if persisted.shape != expected_effective.shape or not np.all(np.isfinite(persisted)):
            raise ValueError(f"Stage 4 effective signal shape/finite contract mismatch:{group}")
        for index, coordinate in enumerate(spec["output_coordinates"]):
            bound = 0.5 * float(dac["lsb_V"]) * sum(abs(matrix[index, j]) * sum(abs(value) for value in config["lanes"][lane]["fir"]) for j, lane in enumerate(spec["input_lanes"])) + 1e-12
            quant_error = float(np.max(np.abs(analog_delta[:, index] - expected_delta[:, index])))
            forward_error = float(np.max(np.abs(persisted[:, index] - expected_effective[:, index])))
            unit = {"xy": "GHz", "z": "Phi0", "readout": "V"}[group]
            expected_quant.append({"coordinate": coordinate, "output_unit": unit, "bound": float(bound), "max_abs_error": quant_error, "passed": bool(quant_error <= bound)})
            expected_forward.append({"coordinate": coordinate, "output_unit": unit, "threshold": 1e-12, "max_abs_error": forward_error, "passed": bool(forward_error <= 1e-12)})
    return expected_quant, expected_forward


def _validate_signal_lengths(value: Any, count: int, time_axis: list[float], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"time_center_ns", "xy_drive_GHz", "absolute_flux_phi0", "readout_device_V"} or value["time_center_ns"] != time_axis:
        raise ValueError(f"Stage 4 {label} schema/time mismatch")
    expected = (("xy_drive_GHz", ("q1", "q2"), ("i", "q")), ("absolute_flux_phi0", ("q1", "q2", "c"), None), ("readout_device_V", ("r1", "r2"), ("i", "q")))
    for section, keys, components in expected:
        rows = value.get(section)
        if not isinstance(rows, Mapping) or set(rows) != set(keys):
            raise ValueError(f"Stage 4 {label} coordinate keys mismatch")
        for key in keys:
            if components is None:
                arrays = [rows[key]]
            else:
                if not isinstance(rows[key], Mapping) or set(rows[key]) != set(components): raise ValueError(f"Stage 4 {label} IQ keys mismatch")
                arrays = [rows[key][component] for component in components]
            if any(not isinstance(array, list) or len(array) != count for array in arrays): raise ValueError(f"Stage 4 {label} array length mismatch")


def _effective_matrix(effective: Mapping[str, Any], group: str) -> np.ndarray:
    if group == "xy":
        return np.stack([effective["xy_drive_GHz"][mode][quadrature] for mode in ("q1", "q2") for quadrature in ("i", "q")], axis=1)
    if group == "z":
        return np.stack([effective["absolute_flux_phi0"][mode] for mode in ("q1", "q2", "c")], axis=1)
    return np.stack([effective["readout_device_V"][mode][quadrature] for mode in ("r1", "r2") for quadrature in ("i", "q")], axis=1)


def _validate_global_evidence(payload: Mapping[str, Any], config: Mapping[str, Any]) -> None:
    metrics = payload.get("global_metrics")
    keys = {"area_rows", "xy_area_error_within_budget", "readout_area_error_within_budget", "z_target_error_within_budget", "quantization_error_within_bound", "latency_alignment_exact", "forward_reconstruction_matches_reference"}
    if not isinstance(metrics, Mapping) or set(metrics) != keys: raise ValueError("Stage 4 global metrics fields are not exact")
    required = [("xy_drag", "q1_drag", "xy"), ("xy_drag", "q2_drag", "xy")]
    if payload["profile"] == "formal": required += [("readout_envelopes", "r1_readout", "readout"), ("readout_envelopes", "r2_readout", "readout")]
    rows = metrics["area_rows"]
    if not isinstance(rows, list) or [(row.get("scenario_id"), row.get("pulse_id"), row.get("kind")) for row in rows if isinstance(row, Mapping)] != required: raise ValueError("Stage 4 area row identity/order mismatch")
    scenario_map = {row["scenario_id"]: row for row in payload["scenarios"]}
    for row, (scenario_id, pulse_id, kind) in zip(rows, required, strict=True):
        if set(row) != {"scenario_id", "pulse_id", "kind", "phase_rad", "desired_in_phase_area", "effective_in_phase_area", "denominator", "relative_error", "threshold", "passed", "unavailable_reason"}: raise ValueError("Stage 4 area row fields are not exact")
        scenario = scenario_map[scenario_id]; pulse = next(item for item in scenario["logical_pulses"] if item["pulse_id"] == pulse_id)
        target = pulse["channel"].split("_")[0]; section = "xy_drive_GHz" if kind == "xy" else "readout_device_V"
        desired = np.asarray(scenario["logical_targets"][section][target]["i"]) + 1j * np.asarray(scenario["logical_targets"][section][target]["q"])
        effective = np.asarray(scenario["effective"][section][target]["i"]) + 1j * np.asarray(scenario["effective"][section][target]["q"])
        phase = pulse["phase_rad"]; desired_area = config["clock"]["dt_ns"] * float(np.sum(np.real(desired * np.exp(-1j * phase)))); effective_area = config["clock"]["dt_ns"] * float(np.sum(np.real(effective * np.exp(-1j * phase)))); denominator = abs(desired_area); relative = abs(effective_area - desired_area) / denominator
        expected = {"scenario_id": scenario_id, "pulse_id": pulse_id, "kind": kind, "phase_rad": phase, "desired_in_phase_area": desired_area, "effective_in_phase_area": effective_area, "denominator": denominator, "relative_error": relative, "threshold": 0.005, "passed": bool(relative <= 0.005), "unavailable_reason": None}
        if row != expected: raise ValueError("Stage 4 area row recomputation mismatch")
    xy_ok = all(row["passed"] for row in rows[:2]); readout_ok = payload["profile"] == "formal" and all(row["passed"] for row in rows[2:])
    z_rows = [row for scenario in payload["scenarios"] for row in scenario["metrics"]["z_target_rows"]]
    expected_values = {"xy_area_error_within_budget": xy_ok, "readout_area_error_within_budget": readout_ok, "z_target_error_within_budget": all(row["passed"] for row in z_rows), "quantization_error_within_bound": all(row["passed"] for scenario in payload["scenarios"] for row in scenario["metrics"]["quantization_rows"]), "latency_alignment_exact": True, "forward_reconstruction_matches_reference": True}
    if any(metrics[key] is not bool(value) for key, value in expected_values.items()): raise ValueError("Stage 4 global metric predicate mismatch")


def _validate_phase_proxy(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != {"source_json_path", "source_value_MHz", "window_ns", "phase_proxy_rad", "threshold_rad", "passed"}: raise ValueError("Stage 4 phase proxy fields are not exact")
    expected = 2 * math.pi * value["source_value_MHz"] * 1e6 * value["window_ns"] * 1e-9
    if value["source_json_path"] != "$.idle_convergence.max_frequency_drift_MHz" or value["window_ns"] != 32.0 or value["threshold_rad"] != 0.10 or value["phase_proxy_rad"] != expected or value["passed"] is not (expected <= 0.10): raise ValueError("Stage 4 phase proxy mismatch")


def _scenario_check(row: Mapping[str, Any], name: str) -> bool:
    checks = row.get("checks")
    if not isinstance(checks, list):
        return False
    matches = [check for check in checks if isinstance(check, Mapping) and check.get("name") == name]
    return len(matches) == 1 and matches[0].get("passed") is True


def _validate_report_payload(report, artifact, artifact_hash, notebook_hash):
    if set(report) != REPORT_KEYS or not _identity(report, "stage_04_control_signal_verification_report"): raise ValueError("Stage 4 report fields or identity are invalid")
    expected = {"execution_succeeded": True, "profile": artifact["profile"], "computational_ready": artifact["computational_gate"]["computational_ready"], "acceptance_eligible": artifact["acceptance_eligible"], "status": artifact["status"], "publication_pending": True, "stage5_ready": False, "approval_status": "pending", "artifact_sha256": artifact_hash, "notebook_sha256": notebook_hash, "computational_checks": artifact["computational_gate"]["checks"], "blocking_reasons": artifact["computational_gate"]["blocking_reasons"]}
    if any(report.get(key) != value for key, value in expected.items()): raise ValueError("Stage 4 report does not match artifact/current hashes")
    if [row.get("name") for row in report.get("staging_checks", [])] != list(STAGING_CHECKS) or any(row.get("passed") is not True for row in report["staging_checks"]): raise ValueError("Stage 4 report staging checks are invalid")


def _validate_receipt_payload(receipt, artifact, report, artifact_path, notebook_path, report_path, root):
    if set(receipt) != RECEIPT_KEYS or not _identity(receipt, "stage_04_control_signal_run_receipt"): raise ValueError("Stage 4 receipt fields or identity are invalid")
    expected_paths = {"artifact": _relative(artifact_path, root), "notebook": _relative(notebook_path, root), "verification_report": _relative(report_path, root)}
    expected_sha = {"artifact_sha256": raw_file_sha256(artifact_path), "notebook_sha256": raw_file_sha256(notebook_path), "verification_report_sha256": raw_file_sha256(report_path)}
    if receipt.get("paths") != expected_paths or receipt.get("sha256") != expected_sha: raise ValueError("Stage 4 receipt bindings mismatch")
    if receipt.get("profile") != artifact["profile"] or receipt.get("acceptance_eligible") != artifact["acceptance_eligible"] or receipt.get("analysis_elapsed_seconds") != artifact["runtime"]["analysis_elapsed_seconds"]: raise ValueError("Stage 4 receipt artifact consistency mismatch")
    if [row.get("name") for row in receipt.get("publication_checks", [])] != list(PUBLICATION_CHECKS) or any(row.get("passed") is not True for row in receipt["publication_checks"]): raise ValueError("Stage 4 receipt publication checks are invalid")


def _new_notebook():
    cells = [
        nbf.v4.new_markdown_cell("# Stage 4 control-signal verification", id="stage4-title"),
        nbf.v4.new_code_cell("from pathlib import Path\nimport json\nARTIFACT = Path('control_signal_artifacts.json')\ndata = json.loads(ARTIFACT.read_text(encoding='utf-8'))\n{'artifact_type': data['artifact_type'], 'profile': data['profile']}", id="stage4-load"),
        nbf.v4.new_markdown_cell("## Provenance and configuration", id="stage4-prov-title"),
        nbf.v4.new_code_cell("{'provenance': data['provenance'], 'control_config': data['control_config'], 'registry': data['registry']}", id="stage4-prov"),
        nbf.v4.new_markdown_cell("## Signal layers", id="stage4-signal-title"),
        nbf.v4.new_code_cell("import matplotlib.pyplot as plt\nrow = data['scenarios'][0]\nfig, axes = plt.subplots(3, 1, figsize=(9, 7))\naxes[0].plot(row['logical_targets']['time_center_ns'], row['logical_targets']['xy_drive_GHz']['q1']['i']); axes[0].set_ylabel('logical GHz')\naxes[1].plot(row['awg']['time_center_ns'], row['awg']['lanes']['q1_xy_i']['reconstructed_V']); axes[1].set_ylabel('AWG V')\naxes[2].plot(row['effective']['time_center_ns'], row['effective']['xy_drive_GHz']['q1']['i']); axes[2].set_ylabel('effective GHz'); axes[2].set_xlabel('ns')\nfig.tight_layout(); fig", id="stage4-signals"),
        nbf.v4.new_markdown_cell("## DAC, latency, FIR, and matrices", id="stage4-electronics-title"),
        nbf.v4.new_code_cell("{'dac': data['control_config']['dac'], 'lanes': data['control_config']['lanes'], 'matrices': data['control_config']['static_mixing']}", id="stage4-electronics"),
        nbf.v4.new_markdown_cell("## Metrics and Stage 3.1 context", id="stage4-metrics-title"),
        nbf.v4.new_code_cell("{'metrics': data['global_metrics'], 'phase_proxy': data['phase_proxy'], 'checks': data['computational_gate']['checks']}", id="stage4-metrics"),
    ]
    return nbf.v4.new_notebook(cells=cells, metadata={"stage4_read_only": True})


def _execute_notebook(notebook, directory):
    client = _preflight_notebook_runtime()
    return client(notebook, timeout=600, kernel_name="python3", resources={"metadata": {"path": str(directory)}}).execute()


def _validate_notebook(path, *, replay=True):
    notebook = nbf.read(path, as_version=4)
    template = _new_notebook()
    if len(notebook.cells) != len(template.cells):
        raise ValueError("Stage 4 notebook cell count mismatch")
    for index, (cell, expected) in enumerate(zip(notebook.cells, template.cells, strict=True)):
        if cell.id != expected.id or cell.cell_type != expected.cell_type or cell.source != expected.source:
            raise ValueError(f"Stage 4 notebook cell {index} does not match exact template")
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    if not code or code[0].source != _new_notebook().cells[1].source or [cell.execution_count for cell in code] != list(range(1, len(code) + 1)) or any(output.get("output_type") == "error" for cell in code for output in cell.outputs): raise ValueError("Stage 4 notebook execution/read-only contract mismatch")
    if notebook.metadata.get("stage4_read_only") is not True or set(notebook.metadata) != {"language_info", "stage4_read_only"}:
        raise ValueError("Stage 4 notebook metadata mismatch")
    if replay:
        artifact_path = Path(path).with_name(ARTIFACT_NAME)
        with tempfile.TemporaryDirectory(prefix="sqvm_stage4_notebook_replay_") as name:
            directory = Path(name)
            shutil.copyfile(artifact_path, directory / ARTIFACT_NAME)
            replayed = _execute_notebook(_new_notebook(), directory)
            if _normalized_notebook(notebook) != _normalized_notebook(replayed):
                raise ValueError("Stage 4 notebook does not equal normative replay")


def _normalized_notebook(notebook):
    payload = json.loads(nbf.writes(notebook, version=4))
    for cell in payload["cells"]:
        if cell["cell_type"] == "code":
            cell.get("metadata", {}).pop("execution", None)
    return payload


def _formal_candidate_ready(artifact, report, receipt):
    return bool(artifact and report and receipt and artifact.get("profile") == "formal" and artifact.get("acceptance_eligible") is True and artifact.get("status") == "ready_for_stage5_review" and report.get("execution_succeeded") is True and report.get("computational_ready") is True and report.get("blocking_reasons") == [] and receipt.get("execution_succeeded") is True and receipt.get("acceptance_candidate_ready") is True and receipt.get("total_runtime_within_budget") is True and receipt.get("blocking_reasons") == [])


def _load_canonical(path, label):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result: raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def constant(value): raise ValueError(f"non-finite JSON value: {value}")
    try: payload = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise ValueError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict) or Path(path).read_bytes() != canonical_json_bytes(payload): raise ValueError(f"{label} is not canonical mapping JSON")
    _finite(payload)
    return payload


def _identity(payload, kind):
    return payload.get("schema_version") == "0.1" and payload.get("artifact_type") == kind and payload.get("artifact_version") == "0.1"


def _check(name, passed): return {"name": name, "passed": bool(passed), "message": "passed" if passed else f"{name} failed"}


def _relative(path, root): return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def _finite(value, path="$"):
    if value is None or isinstance(value, (str, bool, int)): return
    if isinstance(value, float):
        if not math.isfinite(value): raise ValueError(f"non-finite value at {path}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items(): _finite(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value): _finite(item, f"{path}[{index}]")
        return
    raise ValueError(f"unsupported JSON value at {path}")
