import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

import importlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import nbformat
import numpy as np
import pytest

from sqvm.hamiltonian import load_hamiltonian_config
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.spectrum import (
    CrossingConvergenceReport,
    FluxScanResult,
    SolverBackendValidationApproval,
    SolverBackendValidationArtifact,
    StageGateDecision,
    StaticMetricConvergenceReport,
    StaticSpectrumResult,
    ValidatedSolverSpec,
    build_execution_plan,
    dense_near_degenerate_blocks,
    dense_reference_eigensystem,
    environment_fingerprint,
    evaluate_stage3_gate,
    load_spectrum_config,
    projector_spectral_error,
    rebuild_hamiltonian_for_spectrum,
    sha256_counter_seed,
    sha256_counter_v1,
    solve_static_eigensystem,
    stage3_solver_source_tree_sha256,
    validate_solver_backend,
    verify_static_spectrum,
    write_static_spectrum_artifacts,
)
from sqvm.spectrum.models import RuntimeReport
from tests.support.physics_fixture import physics_acceptance_config, physics_smoke_config


SMOKE = physics_smoke_config()
ACCEPTANCE = physics_acceptance_config()


def test_verify_static_spectrum_writes_artifacts(tmp_path):
    report = verify_static_spectrum(SMOKE, tmp_path)
    assert report.execution_succeeded
    assert not report.ok
    assert (tmp_path / "static_spectrum_artifacts.json").exists()
    assert (tmp_path / "verification.ipynb").exists()


def test_verification_notebook_reads_static_spectrum_artifacts(tmp_path):
    verify_static_spectrum(SMOKE, tmp_path)
    notebook = nbformat.read(tmp_path / "verification.ipynb", as_version=4)
    source = "\n".join(cell.source for cell in notebook.cells)
    assert "static_spectrum_artifacts.json" in source
    assert all(cell.execution_count == 1 for cell in notebook.cells if cell.cell_type == "code")


def test_vscode_runner_smoke(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_stage_03_static_spectrum_smoke.py",
            "--config",
            str(SMOKE),
            "--output",
            str(tmp_path / "runner"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["execution_succeeded"]
    assert not payload["acceptance_eligible"]
    assert not payload["stage_gate"]["stage4_ready"]


def test_vscode_acceptance_runner_uses_formal_paths():
    source = Path("scripts/run_stage_03_static_spectrum.py").read_text(encoding="utf-8")
    assert 'config_path="configs/spectra/2q1c_static.yaml"' in source
    assert 'output_dir="output/stage_03_static_spectrum"' in source
    assert "2q1c_static_smoke.yaml" not in source


def test_cli_verify_spectrum_success(monkeypatch, tmp_path):
    import sqvm.__main__ as cli

    class Report:
        ok = True

        @staticmethod
        def to_dict():
            return {"ok": True}

    monkeypatch.setattr(cli, "verify_static_spectrum", lambda *args, **kwargs: Report())
    assert cli.main(["verify-spectrum", str(SMOKE), "--output", str(tmp_path)]) == 0


def test_cli_verify_spectrum_failure_exit_code(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "sqvm", "verify-spectrum", str(SMOKE), "--output", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False


def test_geometry_too_weak_returns_ok_false_and_exit_1(spectrum_session):
    gate = _gate(spectrum_session, {"q1-c": "geometry_too_weak", "c-q2": "resolved"})
    assert gate.status == "geometry_too_weak"
    assert not gate.stage4_ready


def test_stage4_ready_requires_both_crossings_resolved(spectrum_session):
    assert not _gate(spectrum_session, {"q1-c": "resolved", "c-q2": "unresolved"}).stage4_ready
    assert _gate(spectrum_session, {"q1-c": "resolved", "c-q2": "resolved"}, acceptance=True).stage4_ready


def test_smoke_profile_is_not_acceptance_eligible():
    config = load_spectrum_config(SMOKE)
    assert not config.acceptance_eligible


def test_acceptance_rejects_missing_or_stale_solver_validation(monkeypatch, tmp_path):
    verify_module = importlib.import_module("sqvm.spectrum.verify")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    validation = inputs / "validation.json"
    validation.write_bytes(canonical_json_bytes({}))
    config = load_spectrum_config(ACCEPTANCE)
    config = replace(
        config,
        runtime=replace(
            config.runtime,
            solver_validation_artifact=validation,
            solver_validation_approval=inputs / "missing-approval.json",
        ),
    )

    def missing_approval(_):
        raise ValueError("solver validation approval is missing")

    def unexpected_analysis(*args, **kwargs):
        pytest.fail("missing solver approval must fail before acceptance analysis")

    monkeypatch.setattr(verify_module, "load_solver_backend_validation_approval", missing_approval)
    monkeypatch.setattr(verify_module, "analyze_static_point", unexpected_analysis)
    monkeypatch.setattr(verify_module, "load_spectrum_config", lambda _: config)
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="solver validation approval is missing"):
        verify_static_spectrum(ACCEPTANCE, output)
    assert not output.exists()


def test_solver_validation_approval_binds_artifact_hash(tmp_path, spectrum_session):
    validation, approval, config = _solver_validation_fixture(tmp_path, spectrum_session)
    approval.payload["validation_artifact_sha256"] = "0" * 64
    report = validate_solver_backend(validation, approval, spectrum_session["provenance"], config)
    assert not report.ok
    assert report.validated_spec is None


def test_solver_validation_binds_stage3_solver_source_tree_hash(tmp_path, spectrum_session):
    validation, approval, config = _solver_validation_fixture(tmp_path, spectrum_session)
    validation.payload["stage3_solver_source_tree_sha256"] = "0" * 64
    report = validate_solver_backend(validation, approval, spectrum_session["provenance"], config)
    assert not report.ok
    assert any("source hash" in error for error in report.errors)


def test_solver_validation_binds_environment_fingerprint(tmp_path, spectrum_session):
    validation, approval, config = _solver_validation_fixture(tmp_path, spectrum_session)
    validation.payload["environment_fingerprint_sha256"] = "0" * 64
    report = validate_solver_backend(validation, approval, spectrum_session["provenance"], config)
    assert not report.ok
    assert any("environment" in error for error in report.errors)


def test_validated_solver_spec_exactly_matches_spectrum_config(tmp_path, spectrum_session):
    validation, approval, config = _solver_validation_fixture(tmp_path, spectrum_session)
    report = validate_solver_backend(
        validation, approval, spectrum_session["provenance"], config
    )
    assert report.ok
    assert report.validated_spec is not None
    assert np.isfinite(report.validated_solver_error_GHz)
    assert report.validated_spec.ncv == 97
    assert report.validated_spec.num_states == 48
    assert report.validated_spec.acceptance_eligible


def test_sha256_counter_v1_v0_is_deterministic():
    kwargs = {
        "stage2_artifacts_sha256": "D" * 64,
        "cutoffs": (7, 7, 7),
        "flux_phi0": 0.2,
        "num_states": 48,
    }
    assert np.array_equal(sha256_counter_v1(100, **kwargs), sha256_counter_v1(100, **kwargs))


def test_sha256_counter_v1_normative_seed_and_block0_vector():
    seed, digest, block0 = sha256_counter_seed(
        stage2_artifacts_sha256="222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C",
        cutoffs=(7, 7, 7),
        flux_phi0=0.2,
        num_states=48,
    )
    assert len(seed) == 166
    assert digest == "ED780C6FA5C04949197BEE6D43156B10391B1E6E3596F9CFA41D80D455FBF1A7"
    assert block0 == "2601A3794F3CD9A73B4B6B2D4DDDB15E3FC890D8593B2752672D366E83EA2950"


def test_near_degenerate_blocks_use_dense_maximal_contiguous_partition():
    values = np.array([0.0, 1e-6, 2e-6, 1.0, 2.0])
    blocks, _ = dense_near_degenerate_blocks(values, num_states=4, threshold_GHz=1e-5, boundary_margin_GHz=1e-8)
    assert blocks == ((0, 2), (3, 3))


def test_near_degenerate_partition_boundary_margin_is_rejected():
    values = np.array([0.0, 1e-5, 1.0])
    with pytest.raises(ValueError, match="ambiguous"):
        dense_near_degenerate_blocks(values, num_states=2, threshold_GHz=1e-5, boundary_margin_GHz=2e-6)


def test_trailing_near_degenerate_block_is_rejected():
    values = np.array([0.0, 1.0, 1.0 + 1e-6])
    with pytest.raises(ValueError, match="truncated"):
        dense_near_degenerate_blocks(values, num_states=2, threshold_GHz=1e-5, boundary_margin_GHz=2e-6)


def test_projector_error_uses_dense_index_blocks_and_spectral_norm():
    left = np.eye(3)[:, :1]
    right = np.array([[np.cos(0.1)], [np.sin(0.1)], [0.0]])
    assert projector_spectral_error(left, right) == pytest.approx(np.sin(0.1))


def test_eigsh_repeatability_for_gaps_subspaces_and_participation(spectrum_session):
    model = rebuild_hamiltonian_for_spectrum(spectrum_session["context"])
    spec = replace(spectrum_session["context"].solver_spec, backend="validated_eigsh", acceptance_eligible=False)
    first = solve_static_eigensystem(model, spec)
    second = solve_static_eigensystem(model, spec)
    assert np.max(np.abs(first.gaps_GHz - second.gaps_GHz)) <= 1e-10
    assert projector_spectral_error(first.eigenvectors, second.eigenvectors) <= 1e-8


def test_validated_eigsh_matches_dense_for_baseline_and_refined_crossing_cases(spectrum_session):
    for overrides in ({}, {"c": 9}):
        model = rebuild_hamiltonian_for_spectrum(spectrum_session["context"], {"c": 0.35}, overrides)
        dense_values, dense_vectors, _ = dense_reference_eigensystem(model, 12)
        spec = replace(spectrum_session["context"].solver_spec, backend="validated_eigsh", acceptance_eligible=False)
        eigsh = solve_static_eigensystem(model, spec)
        assert np.max(np.abs(eigsh.gaps_GHz - (dense_values[:12] - dense_values[0]))) <= 1e-6
        assert projector_spectral_error(dense_vectors[:, :12], eigsh.eigenvectors) <= 1e-6


def test_execution_plan_uses_dimension_specific_p95(spectrum_session):
    solver = replace(spectrum_session["solver"], p95_seconds_by_signature={"3375": 1.0, "4275": 2.0})
    plan = build_execution_plan(load_spectrum_config(ACCEPTANCE), _empty_flux(True), solver, 10.0)
    assert plan.p95_seconds_by_signature == {"3375": 1.0, "4275": 2.0}
    assert plan.projected_remaining_seconds == 173.0 + 672.0


def test_runtime_budget_exceeded_blocks_acceptance(spectrum_session):
    solver = replace(spectrum_session["solver"], p95_seconds_by_signature={"3375": 10.0, "4275": 10.0})
    plan = build_execution_plan(load_spectrum_config(ACCEPTANCE), _empty_flux(True), solver, 10.0)
    assert not plan.within_budget


def test_static_spectrum_result_is_only_artifact_writer_input(tmp_path):
    with pytest.raises(TypeError, match="StaticSpectrumResult"):
        write_static_spectrum_artifacts({}, tmp_path)


def test_boundary_result_writes_canonical_diagnostic_artifact_and_notebook(tmp_path, spectrum_session):
    result = _synthetic_boundary_result(spectrum_session)
    artifacts = write_static_spectrum_artifacts(result, tmp_path)
    payload = json.loads(artifacts.static_spectrum_artifacts.read_text(encoding="utf-8"))
    assert artifacts.static_spectrum_artifacts.read_bytes() == canonical_json_bytes(payload)
    assert payload["stage_gate"]["stage4_ready"] is False
    assert payload["crossing_convergence"]["candidates"]["q1-c"]["U_total_MHz"] is None
    notebook = nbformat.read(artifacts.verification_notebook, as_version=4)
    assert all(cell.execution_count == 1 for cell in notebook.cells if cell.cell_type == "code")
    assert not any(output.output_type == "error" for cell in notebook.cells for output in cell.get("outputs", []))


def test_nested_nonfinite_is_rejected_with_json_path_and_no_partial_artifact(tmp_path, spectrum_session):
    result = _synthetic_boundary_result(spectrum_session)
    bad_crossing = CrossingConvergenceReport(
        candidates={"q1-c": {"status": "boundary", "nested": {"value": float("inf")}}},
        passed=False,
    )
    result = replace(result, crossing_convergence=bad_crossing)
    output = tmp_path / "bad"
    with pytest.raises(ValueError, match=r"\$\.crossing_convergence\.candidates\.q1-c\.nested\.value"):
        write_static_spectrum_artifacts(result, output)
    assert not output.exists()


def _solver_validation_fixture(tmp_path, spectrum_session):
    config = load_spectrum_config(ACCEPTANCE)
    pilot_path = tmp_path / "dense_pilot.json"
    flux_keys = (
        "0.200000000000",
        "0.240000000000",
        "0.260000000000",
        "0.270000000000",
        "0.280000000000",
        "0.360000000000",
        "0.450000000000",
    )
    provenance = spectrum_session["provenance"]
    pilot_payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_dense_pilot",
        "artifact_version": "0.1",
        "profile": "dense_pilot",
        "acceptance_eligible": False,
        "num_states": 12,
        "solver_backend": "dense_eigh",
        "spectrum_config_path": config.source_path.as_posix(),
        "spectrum_config_sha256": raw_file_sha256(config.source_path),
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "stage2_dense_gap_consistency": {
            "ok": True,
            "compared_gap_count": 12,
            "max_abs_difference_GHz": 0.0,
        },
        "start_key": flux_keys[0],
        "stop_key": flux_keys[-1],
        "idle_flux_key": flux_keys[3],
        "points": [{"flux_key": key} for key in flux_keys],
        "candidates": [
            {
                "name": "q1-c",
                "evidence_left_key": flux_keys[0],
                "minimum_key": flux_keys[1],
                "evidence_right_key": flux_keys[2],
            },
            {
                "name": "c-q2",
                "evidence_left_key": flux_keys[4],
                "minimum_key": flux_keys[5],
                "evidence_right_key": flux_keys[6],
            },
        ],
        "fixture_authority": "test_only_non_production",
    }
    pilot_path.write_bytes(canonical_json_bytes(pilot_payload))

    validation_path = tmp_path / "validation.json"
    hamiltonian = load_hamiltonian_config(config.source_hamiltonian_config)
    base = tuple(hamiltonian.basis.charge_cutoffs[mode] for mode in ("q1", "c", "q2"))
    signatures = []
    for name, index in (("baseline", None), ("refined_q1", 0), ("refined_c", 1), ("refined_q2", 2)):
        cutoffs = list(base)
        if index is not None:
            cutoffs[index] += config.convergence.cutoff_increment
        dimension = int(np.prod([2 * value + 1 for value in cutoffs]))
        signatures.append((name, cutoffs, dimension))
    gaps = [float(index) for index in range(config.eigen.num_states)]
    blocks = [[index, index] for index in range(config.eigen.num_states)]
    zeros = [0.0] * config.eigen.num_states
    cases = []
    for name, cutoffs, dimension in signatures:
        for flux_key in flux_keys:
            assignments = {"000": 0}
            cases.append(
                {
                    "case_id": f"{name}@{flux_key}",
                    "cutoff_signature": cutoffs,
                    "dimension": dimension,
                    "flux_key": flux_key,
                    "dense_gaps_GHz": gaps,
                    "eigsh_gaps_GHz": gaps,
                    "gap_dense_max_error_GHz": 0.0,
                    "gap_repeat_max_error_GHz": 0.0,
                    "dense_block_index_ranges": blocks,
                    "boundary_gaps_GHz": [1.0] * config.eigen.num_states,
                    "projector_dense_errors_spectral_2": zeros,
                    "projector_repeat_errors_spectral_2": zeros,
                    "truncation_status": "not_truncated",
                    "physics_comparison": {
                        "assignments_dense": assignments,
                        "assignments_eigsh_run1": assignments,
                        "assignments_eigsh_run2": assignments,
                        "participation_dense_max_error": 0.0,
                        "participation_repeat_max_error": 0.0,
                        "metric_dense_max_error_GHz": 0.0,
                        "passed": True,
                    },
                    "passed": True,
                }
            )
    environment, environment_hash = environment_fingerprint()
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation",
        "artifact_version": "0.1",
        "profile": "solver_validation",
        "validation_passed": True,
        "acceptance_eligible": False,
        "spectrum_config_path": config.source_path.as_posix(),
        "spectrum_config_sha256": raw_file_sha256(config.source_path),
        "stage2_rebaseline_manifest_sha256": provenance.rebaseline_manifest_sha256,
        "stage2_rebaseline_approval_sha256": provenance.rebaseline_approval_sha256,
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "hamiltonian_config_sha256": provenance.hamiltonian_config_sha256,
        "stage2_model_source_tree_sha256": provenance.stage2_model_source_tree_sha256,
        "stage3_solver_source_tree_sha256": stage3_solver_source_tree_sha256("."),
        "environment_fingerprint_sha256": environment_hash,
        "environment_fingerprint": environment,
        "eigsh_spec": {
            "which": config.eigen.eigsh.which,
            "tolerance": config.eigen.eigsh.tolerance,
            "maxiter": config.eigen.eigsh.maxiter,
            "ncv": config.eigen.eigsh.ncv,
            "v0_rule": config.eigen.eigsh.v0_rule,
            "num_states": config.eigen.num_states,
        },
        "dense_config": {"solver": "scipy.linalg.eigh", "num_states_plus_one": 49},
        "near_degenerate_gap_threshold_GHz": config.eigen.solver_validation.near_degenerate_gap_threshold_GHz,
        "partition_boundary_margin_GHz": config.eigen.solver_validation.partition_boundary_margin_GHz,
        "projector_error_norm": config.eigen.solver_validation.projector_error_norm,
        "projector_dense_tolerance": config.eigen.solver_validation.projector_dense_tolerance,
        "projector_repeat_tolerance": config.eigen.solver_validation.projector_repeat_tolerance,
        "v0_canonical_encoding_version": "sha256_counter_v1",
        "normative_test_vector": {
            "seed_length": 166,
            "seed_sha256": "ED780C6FA5C04949197BEE6D43156B10391B1E6E3596F9CFA41D80D455FBF1A7",
            "block0_sha256": "2601A3794F3CD9A73B4B6B2D4DDDB15E3FC890D8593B2752672D366E83EA2950",
        },
        "normative_test_vector_passed": True,
        "dense_pilot_path": pilot_path.as_posix(),
        "dense_pilot_sha256": raw_file_sha256(pilot_path),
        "validation_cases": cases,
        "failed_cases": [],
        "coverage_complete": True,
        "max_gap_error_GHz": 0.0,
        "max_repeat_gap_error_GHz": 0.0,
        "max_projector_dense_error": 0.0,
        "max_projector_repeat_error": 0.0,
        "max_participation_dense_error": 0.0,
        "max_participation_repeat_error": 0.0,
        "max_metric_dense_error_GHz": 0.0,
        "p50_seconds_by_signature": {"3375": 1.0, "4275": 2.0},
        "p95_seconds_by_signature": {"3375": 1.0, "4275": 2.0},
        "fixture_authority": "test_only_non_production",
    }
    validation_path.write_bytes(canonical_json_bytes(payload))
    approval_path = tmp_path / "approval.json"
    approval_payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation_approval",
        "artifact_version": "0.1",
        "decision": "approved",
        "reviewer_role": "independent_test_review_ai",
        "validation_artifact_sha256": raw_file_sha256(validation_path),
        "blocking_findings": [],
    }
    approval_path.write_bytes(canonical_json_bytes(approval_payload))
    config = replace(
        config,
        runtime=replace(
            config.runtime,
            solver_validation_artifact=validation_path,
            solver_validation_approval=approval_path,
        ),
    )
    return (
        SolverBackendValidationArtifact(validation_path, payload),
        SolverBackendValidationApproval(approval_path, approval_payload),
        config,
    )


def _empty_flux(eligible):
    return FluxScanResult("c", (), {"q1-c": (), "c-q2": ()}, (), 0, 0, 0.0, eligible)


def _synthetic_boundary_result(session):
    config = session["config"]
    flux = FluxScanResult(
        "c",
        (),
        {"q1-c": (), "c-q2": ()},
        (
            {"name": "q1-c", "status": "boundary", "level_to_level_drift_MHz": None},
            {"name": "c-q2", "status": "unresolved", "level_to_level_drift_MHz": None},
        ),
        0,
        0,
        0.0,
        False,
    )
    metric = StaticMetricConvergenceReport(
        dict(session["baseline"].cutoffs),
        2,
        {"frequency": 0.50, "anharmonicity": 1.0, "zz": 0.01},
        (),
        0.0,
        0.0,
        0.0,
        False,
    )
    crossing = CrossingConvergenceReport(
        {
            "q1-c": {
                "status": "boundary",
                "uncertainty_inputs_valid": False,
                "unavailable_reasons": ["level_to_level_drift_MHz_missing_or_nonfinite"],
                "U_cutoff_MHz": None,
                "U_flux_MHz": None,
                "U_total_MHz": None,
                "relative_uncertainty": None,
                "significance_ratio": None,
                "passed": False,
            },
            "c-q2": {
                "status": "unresolved",
                "uncertainty_inputs_valid": False,
                "unavailable_reasons": ["level_to_level_drift_MHz_missing_or_nonfinite"],
                "U_cutoff_MHz": None,
                "U_flux_MHz": None,
                "U_total_MHz": None,
                "relative_uncertainty": None,
                "significance_ratio": None,
                "passed": False,
            },
        },
        False,
    )
    plan = build_execution_plan(config, flux, session["solver"], 0.0)
    runtime = RuntimeReport(
        config.profile,
        False,
        plan.budget_seconds,
        plan.projected_total_seconds,
        0.0,
        "dense_eigh",
        "",
        "",
        plan,
        1,
        0,
        0.0,
        0.0,
        "within_budget",
    )
    gate = StageGateDecision(
        True,
        "numerical_failure",
        False,
        ("crossing_boundary",),
        {"q1-c": "boundary", "c-q2": "unresolved"},
    )
    return StaticSpectrumResult(
        config,
        session["provenance"],
        session["solver"],
        session["baseline"],
        metric,
        flux,
        crossing,
        runtime,
        gate,
        (),
        (),
    )


def _gate(session, statuses, acceptance=False):
    config = load_spectrum_config(ACCEPTANCE if acceptance else SMOKE)
    metric = StaticMetricConvergenceReport({}, 2, {}, (), 0.0, 0.0, 0.0, True)
    crossing = CrossingConvergenceReport({key: {"status": value} for key, value in statuses.items()}, all(value == "resolved" for value in statuses.values()))
    plan = build_execution_plan(config, _empty_flux(acceptance), session["solver"], 0.0)
    runtime = RuntimeReport(config.profile, acceptance, plan.budget_seconds, plan.projected_total_seconds, 0.0, "validated_eigsh", "", "", plan, 0, 0, 0.0, 0.0, "within_budget")
    return evaluate_stage3_gate(config, session["provenance"], metric, _empty_flux(acceptance), crossing, runtime)
