import copy
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.spectrum import (
    SolverBackendValidationApproval,
    SolverBackendValidationArtifact,
    load_spectrum_config,
    stage3_solver_source_tree_sha256,
    validate_solver_backend,
)


ACCEPTANCE = Path("configs/spectra/2q1c_static.yaml")
CANDIDATE = Path("output/stage_03_solver_validation/eigsh_validation.json")
PILOT = Path("output/stage_03_solver_validation/dense_pilot.json")


def test_minimal_noncanonical_fake_validation_and_attacker_approval_fail_closed(tmp_path, spectrum_session):
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation",
        "artifact_version": "0.1",
        "profile": "solver_validation",
        "acceptance_eligible": False,
        "validation_passed": True,
        "p95_seconds_by_signature": {"3375": -1.0, "4275": -1.0},
    }
    validation_path = tmp_path / "validation.json"
    validation_path.write_text(json.dumps(payload), encoding="utf-8")
    approval_payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation_approval",
        "artifact_version": "0.1",
        "decision": "approved",
        "reviewer_role": "attacker",
        "blocking_findings": [],
        "validation_artifact_sha256": raw_file_sha256(validation_path),
    }
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(approval_payload), encoding="utf-8")
    config = _config_for_paths(validation_path, approval_path)
    report = validate_solver_backend(
        SolverBackendValidationArtifact(validation_path, payload),
        SolverBackendValidationApproval(approval_path, approval_payload),
        spectrum_session["provenance"],
        config,
    )
    assert not report.ok
    assert report.validated_spec is None
    assert np.isfinite(report.validated_solver_error_GHz)


def test_formal_candidate_fixture_returns_finite_validated_spec(tmp_path, spectrum_session):
    report = _validate_bundle(tmp_path, spectrum_session)
    assert report.ok
    assert report.validated_spec is not None
    assert report.validated_spec.acceptance_eligible
    assert np.isfinite(report.validated_solver_error_GHz)


@pytest.mark.parametrize("role", [None, "attacker", "", False])
def test_approval_reviewer_role_is_strict(tmp_path, spectrum_session, role):
    def mutate(payload):
        if role is None:
            payload.pop("reviewer_role")
        else:
            payload["reviewer_role"] = role

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_approval=mutate).ok


@pytest.mark.parametrize("target", ["validation", "approval"])
def test_noncanonical_validation_or_approval_is_rejected(tmp_path, spectrum_session, target):
    report = _validate_bundle(
        tmp_path,
        spectrum_session,
        noncanonical_validation=target == "validation",
        noncanonical_approval=target == "approval",
    )
    assert not report.ok
    assert report.validated_spec is None


@pytest.mark.parametrize("target", ["validation", "approval"])
def test_runtime_path_mismatch_is_rejected(tmp_path, spectrum_session, target):
    validation, approval, config = _bundle(tmp_path)
    replacement = {f"solver_validation_{'artifact' if target == 'validation' else 'approval'}": tmp_path / "other.json"}
    config = replace(config, runtime=replace(config.runtime, **replacement))
    report = validate_solver_backend(validation, approval, spectrum_session["provenance"], config)
    assert not report.ok
    assert any("path" in error for error in report.errors)


def test_approval_extra_identity_field_is_rejected(tmp_path, spectrum_session):
    report = _validate_bundle(
        tmp_path,
        spectrum_session,
        mutate_approval=lambda payload: payload.__setitem__("approved_by", "attacker"),
    )
    assert not report.ok


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("failed_cases"),
        lambda payload: payload.__setitem__("failed_cases", ["forged"]),
        lambda payload: payload.__setitem__("validation_passed", False),
        lambda payload: payload.__setitem__("coverage_complete", False),
    ],
    ids=["failed-missing", "failed-nonempty", "self-declared-failure", "coverage-self-declared"],
)
def test_failed_cases_and_validation_passed_are_recomputed(tmp_path, spectrum_session, mutation):
    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutation).ok


def test_failed_cases_must_track_recomputed_case_failure(tmp_path, spectrum_session):
    def mutate(payload):
        payload["validation_cases"][0]["passed"] = False
        payload["failed_cases"] = []

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize("variant", ["missing", "extra", "duplicate", "wrong_key", "wrong_signature", "wrong_dimension", "wrong_case_id"])
def test_validation_case_coverage_is_exact(tmp_path, spectrum_session, variant):
    def mutate(payload):
        cases = payload["validation_cases"]
        if variant == "missing":
            cases.pop()
        elif variant == "extra":
            row = copy.deepcopy(cases[0])
            row["case_id"] = "baseline@0.123456789012"
            row["flux_key"] = "0.123456789012"
            cases.append(row)
        elif variant == "duplicate":
            cases[-1] = copy.deepcopy(cases[0])
        elif variant == "wrong_key":
            cases[0]["flux_key"] = "0.450000000000"
        elif variant == "wrong_signature":
            cases[0]["cutoff_signature"] = [9, 7, 7]
        elif variant == "wrong_dimension":
            cases[0]["dimension"] = 1
        else:
            cases[0]["case_id"] = "baseline@0.123456789012"

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize(
    "variant",
    [
        "gaps",
        "reported_gap",
        "repeat_gap",
        "blocks",
        "boundary_ambiguous",
        "dense_projector",
        "repeat_projector",
        "truncation",
        "assignments",
        "participation_dense",
        "participation_repeat",
        "metric",
        "physics_passed",
        "case_passed",
        "nan_gap",
        "inf_projector",
    ],
)
def test_each_case_gate_rejects_tampering(tmp_path, spectrum_session, variant):
    def mutate(payload):
        case = payload["validation_cases"][0]
        physics = case["physics_comparison"]
        if variant == "gaps":
            case["eigsh_gaps_GHz"][1] += 1e-3
        elif variant == "reported_gap":
            case["gap_dense_max_error_GHz"] = 0.0
        elif variant == "repeat_gap":
            case["gap_repeat_max_error_GHz"] = 1e-3
        elif variant == "blocks":
            case["dense_block_index_ranges"] = [[0, 46], [48, 47]]
        elif variant == "boundary_ambiguous":
            case["boundary_gaps_GHz"][0] = 1e-5
        elif variant == "dense_projector":
            case["projector_dense_errors_spectral_2"][0] = 1e-3
        elif variant == "repeat_projector":
            case["projector_repeat_errors_spectral_2"][0] = 1e-3
        elif variant == "truncation":
            case["truncation_status"] = "truncated"
        elif variant == "assignments":
            physics["assignments_eigsh_run1"]["000"] = 47
        elif variant == "participation_dense":
            physics["participation_dense_max_error"] = 1e-3
        elif variant == "participation_repeat":
            physics["participation_repeat_max_error"] = 1e-3
        elif variant == "metric":
            physics["metric_dense_max_error_GHz"] = 1e-3
        elif variant == "physics_passed":
            physics["passed"] = False
        elif variant == "case_passed":
            case["passed"] = False
        elif variant == "nan_gap":
            case["dense_gaps_GHz"][0] = float("nan")
        else:
            case["projector_dense_errors_spectral_2"][0] = float("inf")

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize(
    "field",
    [
        "spectrum_config_sha256",
        "stage2_rebaseline_manifest_sha256",
        "stage2_rebaseline_approval_sha256",
        "stage2_artifacts_sha256",
        "hamiltonian_config_sha256",
        "stage2_model_source_tree_sha256",
        "stage3_solver_source_tree_sha256",
        "environment_fingerprint_sha256",
        "dense_pilot_sha256",
    ],
)
def test_config_source_environment_stage2_and_pilot_hashes_are_bound(tmp_path, spectrum_session, field):
    assert not _validate_bundle(
        tmp_path,
        spectrum_session,
        mutate_validation=lambda payload: payload.__setitem__(field, "0" * 64),
    ).ok


@pytest.mark.parametrize("field", ["spectrum_config_path", "dense_pilot_path"])
def test_config_and_pilot_paths_are_bound(tmp_path, spectrum_session, field):
    assert not _validate_bundle(
        tmp_path,
        spectrum_session,
        mutate_validation=lambda payload: payload.__setitem__(field, "missing.json"),
    ).ok


def test_environment_payload_is_bound(tmp_path, spectrum_session):
    def mutate(payload):
        payload["environment_fingerprint"]["python_version"] = "0.0"

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize(
    "field",
    [
        "v0_canonical_encoding_version",
        "normative_test_vector",
        "normative_test_vector_passed",
        "eigsh_spec",
        "dense_config",
        "near_degenerate_gap_threshold_GHz",
        "partition_boundary_margin_GHz",
        "projector_error_norm",
        "projector_dense_tolerance",
        "projector_repeat_tolerance",
    ],
)
def test_normative_solver_spec_and_thresholds_are_exact(tmp_path, spectrum_session, field):
    def mutate(payload):
        if field == "normative_test_vector_passed":
            payload[field] = False
        elif isinstance(payload[field], dict):
            payload[field] = {"forged": True}
        elif isinstance(payload[field], str):
            payload[field] = "forged"
        else:
            payload[field] = float(payload[field]) * 2.0

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize("target", ["pilot_provenance", "pilot_noncanonical", "pilot_reduced_coverage"])
def test_dense_pilot_is_revalidated_from_current_bytes(tmp_path, spectrum_session, target):
    pilot_payload = json.loads(PILOT.read_text(encoding="utf-8"))
    pilot_path = tmp_path / "pilot.json"
    if target == "pilot_provenance":
        pilot_payload["stage2_artifacts_sha256"] = "0" * 64
        pilot_path.write_bytes(canonical_json_bytes(pilot_payload))
    elif target == "pilot_noncanonical":
        pilot_path.write_text(json.dumps(pilot_payload), encoding="utf-8")
    else:
        pilot_payload["candidates"][1]["evidence_left_key"] = pilot_payload["candidates"][0]["evidence_left_key"]
        pilot_payload["candidates"][1]["minimum_key"] = pilot_payload["candidates"][0]["minimum_key"]
        pilot_payload["candidates"][1]["evidence_right_key"] = pilot_payload["candidates"][0]["evidence_right_key"]
        pilot_path.write_bytes(canonical_json_bytes(pilot_payload))

    def mutate(payload):
        payload["dense_pilot_path"] = str(pilot_path)
        payload["dense_pilot_sha256"] = raw_file_sha256(pilot_path)

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize(
    "map_name,key,value",
    [
        ("p50_seconds_by_signature", "3375", None),
        ("p95_seconds_by_signature", "4275", None),
        ("p50_seconds_by_signature", "3375", -1.0),
        ("p95_seconds_by_signature", "4275", 0.0),
        ("p95_seconds_by_signature", "3375", float("inf")),
        ("p95_seconds_by_signature", "3375", 1e-9),
    ],
)
def test_p50_p95_are_complete_finite_positive_and_ordered(tmp_path, spectrum_session, map_name, key, value):
    def mutate(payload):
        if value is None:
            payload[map_name].pop(key)
        else:
            payload[map_name][key] = value

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


@pytest.mark.parametrize(
    "field",
    [
        "max_gap_error_GHz",
        "max_repeat_gap_error_GHz",
        "max_projector_dense_error",
        "max_projector_repeat_error",
        "max_participation_dense_error",
        "max_participation_repeat_error",
        "max_metric_dense_error_GHz",
    ],
)
@pytest.mark.parametrize("variant", ["missing", "tampered", "infinite"])
def test_all_top_level_maxima_are_required_finite_and_recomputed(tmp_path, spectrum_session, field, variant):
    def mutate(payload):
        if variant == "missing":
            payload.pop(field)
        elif variant == "tampered":
            payload[field] = float(payload[field]) + 1.0
        else:
            payload[field] = float("inf")

    assert not _validate_bundle(tmp_path, spectrum_session, mutate_validation=mutate).ok


def _validate_bundle(
    tmp_path,
    spectrum_session,
    *,
    mutate_validation=None,
    mutate_approval=None,
    noncanonical_validation=False,
    noncanonical_approval=False,
):
    validation, approval, config = _bundle(tmp_path, write=False)
    if mutate_validation:
        mutate_validation(validation.payload)
    _write_json(validation.path, validation.payload, noncanonical_validation)
    approval.payload["validation_artifact_sha256"] = raw_file_sha256(validation.path)
    if mutate_approval:
        mutate_approval(approval.payload)
    _write_json(approval.path, approval.payload, noncanonical_approval)
    return validate_solver_backend(validation, approval, spectrum_session["provenance"], config)


def _bundle(tmp_path, *, write=True):
    validation_path = tmp_path / "validation.json"
    approval_path = tmp_path / "approval.json"
    validation_payload = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    validation_payload["stage3_solver_source_tree_sha256"] = stage3_solver_source_tree_sha256(".")
    approval_payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation_approval",
        "artifact_version": "0.1",
        "decision": "approved",
        "reviewer_role": "independent_test_review_ai",
        "blocking_findings": [],
        "validation_artifact_sha256": "pending",
    }
    validation = SolverBackendValidationArtifact(validation_path, validation_payload)
    approval = SolverBackendValidationApproval(approval_path, approval_payload)
    if write:
        _write_json(validation_path, validation_payload)
        approval_payload["validation_artifact_sha256"] = raw_file_sha256(validation_path)
        _write_json(approval_path, approval_payload)
    return validation, approval, _config_for_paths(validation_path, approval_path)


def _config_for_paths(validation_path, approval_path):
    config = load_spectrum_config(ACCEPTANCE)
    return replace(
        config,
        runtime=replace(
            config.runtime,
            solver_validation_artifact=validation_path,
            solver_validation_approval=approval_path,
        ),
    )


def _write_json(path, payload, noncanonical=False):
    if noncanonical:
        path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
        return
    try:
        path.write_bytes(canonical_json_bytes(payload))
    except ValueError:
        path.write_text(json.dumps(payload, sort_keys=True, indent=2, allow_nan=True) + "\n", encoding="utf-8")
