from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import nbformat
import numpy as np
import pytest
import yaml

import sqvm.__main__ as cli
import sqvm.spectrum.stage31 as stage31
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.spectrum.solver import sha256_counter_seed_v2
from sqvm.spectrum.stage31 import (
    _evidence_domain,
    _fixed_refinement_completion,
    _physical_evidence,
    _resolve_refinement_termination,
    _track_complete_grid,
    build_stage31_runtime_report,
    check_q1_q2_crossing_convergence,
    classify_q1_q2_crossing,
    evaluate_coupling_modulation,
    finalize_q1_q2_crossings,
    full_flux_keys,
    preflight_stage3_1_phase,
    stage31_cache_identity,
)
from sqvm.spectrum.stage31_artifacts import (
    assemble_stage3_1_verification_report,
    execute_stage3_1_notebook,
    publish_stage3_1_transaction,
    q1_q2_coupling_result_to_payload,
    validate_stage3_1_acceptance_approval,
    write_q1_q2_coupling_artifacts,
)
from sqvm.spectrum.stage31_config import load_q1_q2_coupling_config
from sqvm.spectrum.stage31_models import (
    ArtifactWriteResult,
    CouplingModulationReport,
    FinalizedQubitCouplingResult,
    QubitCouplingScanResult,
    QubitCouplingSweepResult,
    QubitCrossingConvergenceReport,
    QubitCrossingScanEvidence,
    Stage31ComputationalGate,
    Stage31RuntimeReport,
)
from sqvm.spectrum.stage31_validation import (
    CUTOFF_SIGNATURES,
    FLUX_VECTORS,
    STAGE31_BINDING_KEYS,
    _validate_stage31_bindings,
)


ROOT = Path(__file__).resolve().parents[1]
FORMAL = ROOT / "configs/spectra/2q1c_q1q2_coupling.yaml"
SMOKE = ROOT / "configs/spectra/2q1c_q1q2_coupling_smoke.yaml"


def test_stage31_formal_and_smoke_literals_are_exact():
    formal = load_q1_q2_coupling_config(FORMAL)
    smoke = load_q1_q2_coupling_config(SMOKE)
    assert formal.acceptance_eligible and formal.eigen.num_states == 48
    assert formal.scan.coupler_flux_points_phi0 == (0.2, 0.27, 0.36, 0.38, 0.385, 0.39, 0.394, 0.396, 0.4)
    assert formal.scan.acceptance_anchor_fluxes_phi0 == (0.2, 0.27, 0.385)
    assert formal.runtime.conservative_solve_ceiling == 1381
    assert not smoke.acceptance_eligible and smoke.eigen.num_states == 12
    assert smoke.scan.coupler_flux_points_phi0 == (0.27,)
    assert smoke.scan.acceptance_anchor_fluxes_phi0 == ()


def test_stage31_config_rejects_nonexact_formal_literal(tmp_path):
    raw = yaml.safe_load(FORMAL.read_text(encoding="utf-8"))
    raw["spectrum"]["scan"]["inner_coarse_points"] = 23
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="frozen literals"):
        load_q1_q2_coupling_config(path)


def test_full_flux_keys_and_cache_identity_include_all_components():
    cutoffs = {"q1": 7, "c": 7, "q2": 7}
    left = full_flux_keys(0.1, 0.27, 0.08)
    right = full_flux_keys(0.1, 0.27, 0.12)
    assert left == {"q1": "0.100000000000", "c": "0.270000000000", "q2": "0.080000000000"}
    assert stage31_cache_identity(cutoffs, left, 48, "validated_eigsh") != stage31_cache_identity(cutoffs, right, 48, "validated_eigsh")


def test_evidence_domain_contains_every_evaluated_key_between_endpoints():
    config = load_q1_q2_coupling_config(FORMAL)
    rows = []
    for value, orientation in ((80000000000, "left"), (90000000000, "left"), (100000000000, None), (110000000000, "right"), (120000000000, "right")):
        first = {"q1": 0.9, "q2": 0.1} if orientation == "left" else {"q1": 0.1, "q2": 0.9}
        second = {"q1": 0.1, "q2": 0.9} if orientation == "left" else {"q1": 0.9, "q2": 0.1}
        if orientation is None:
            first = second = {"q1": 0.5, "q2": 0.5}
        rows.append({
            "flux_keys": {"q2": f"0.{value:012d}"},
            "participation": {"100": {"fractions": first}, "001": {"fractions": second}},
        })
    evidence, diagnostics = _evidence_domain(
        rows, ("0.090000000000", "0.110000000000"), "0.100000000000", config
    )
    assert evidence == (
        "0.090000000000", "0.100000000000", "0.110000000000",
    )
    assert diagnostics["character_endpoint_keys"] == ["0.090000000000", "0.110000000000"]


def test_sha256_counter_v2_normative_vector():
    seed, digest, block0 = sha256_counter_seed_v2(
        stage2_artifacts_sha256="DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66",
        cutoffs=(7, 7, 7), fluxes_phi0=(0.1, 0.27, 0.1), num_states=48,
    )
    assert len(seed) == 224
    assert digest == "48245D6E13E15E65DA68E7AD4E957E07F97F0C1DF6E182E1806D6B1F88027CA0"
    assert block0 == "44C000CA15E6923769C51CCC19623DAB311213490C1E50225D7E0AC79A33E322"


def test_solver_validation_matrix_is_exact_44_signature_major_cases():
    assert [row[0] for row in CUTOFF_SIGNATURES] == ["baseline", "refined_q1", "refined_c", "refined_q2"]
    assert [row[0] for row in FLUX_VECTORS] == [
        "idle", "c020_left", "c020_mid", "c020_right", "c027_left", "c027_mid",
        "c027_right", "c0385_left", "c0385_mid", "c0385_right", "c0396_mid",
    ]
    assert len(CUTOFF_SIGNATURES) * len(FLUX_VECTORS) == 44


@pytest.mark.parametrize(
    ("false_key", "expected"),
    [
        ("runtime_valid", "runtime_budget_exceeded"),
        ("minimum_interior", "boundary"),
        ("numerically_converged", "numerically_unconverged"),
        ("subspace_continuity_valid", "low_subspace_continuity"),
        ("target_bare_projector_valid", "pair_subspace_invalid"),
        ("coupler_fraction_valid", "coupler_hybridized"),
        ("resonance_aligned", "resonance_misaligned"),
        ("character_exchange_valid", "character_exchange_failed"),
        ("uncertainty_valid", "uncertainty_failed"),
    ],
)
def test_stage31_status_priority(false_key, expected):
    predicates = {
        "runtime_valid": True, "minimum_interior": True, "numerically_converged": True,
        "tracking_valid": True, "subspace_continuity_valid": True, "target_bare_projector_valid": True,
        "total_excitation_valid": True, "coupler_fraction_valid": True,
        "bare_detuning_root_valid": True, "resonance_aligned": True,
        "character_exchange_valid": True, "uncertainty_valid": True,
    }
    predicates[false_key] = False
    assert classify_q1_q2_crossing(predicates) == expected


def test_stage31_resolved_requires_every_predicate():
    predicates = {key: True for key in (
        "runtime_valid", "minimum_interior", "numerically_converged", "tracking_valid", "subspace_continuity_valid",
        "target_bare_projector_valid", "total_excitation_valid", "coupler_fraction_valid",
        "bare_detuning_root_valid", "resonance_aligned", "character_exchange_valid", "uncertainty_valid",
    )}
    assert classify_q1_q2_crossing(predicates) == "resolved"
    del predicates["uncertainty_valid"]
    assert classify_q1_q2_crossing(predicates) == "uncertainty_failed"


def test_target_subspace_continuity_uses_squared_smallest_singular_value():
    import numpy as np

    identity = np.eye(3)[:, :2]
    same = [(None, identity), (None, identity)]
    orthogonal = [(None, identity), (None, np.eye(3)[:, 1:])]
    assert stage31._subspace_continuity(same) == [None, 1.0]
    assert stage31._subspace_continuity(orthogonal)[1] == pytest.approx(0.0)


def test_deterministic_bare_detuning_root_is_inside_evidence_edge():
    rows = [
        {"flux_keys": {"q2": "0.090000000000"}, "bare_detuning_MHz": -2.0},
        {"flux_keys": {"q2": "0.110000000000"}, "bare_detuning_MHz": 6.0},
    ]
    root = stage31._detuning_root(rows)
    assert root == {"found": True, "q2_flux_key": "0.095000000000", "method": "linear_bare_detuning"}


def test_tracked_branch_crosses_character_without_fresh_bare_relabel():
    tracked = _track_complete_grid(_synthetic_avoided_crossing_points())
    assert tracked[0]["tracked_eigen_indices"] == {"100": 1, "001": 2, "010": 3}
    assert tracked[-1]["tracked_eigen_indices"] == {"100": 1, "001": 2, "010": 3}
    assert tracked[-1]["fresh_bare_assignment"]["100"]["eigen_index"] == 2
    assert tracked[-1]["fresh_bare_assignment"]["001"]["eigen_index"] == 1
    assert tracked[0]["participation"]["100"]["fractions"]["q1"] > 0.99
    assert tracked[-1]["participation"]["100"]["fractions"]["q2"] > 0.99
    assert min(row["previous_target_subspace_sigma_min_squared"] for row in tracked[1:]) > 0.99
    predicates, diagnostics, root = _physical_evidence(
        tracked, "converged", load_q1_q2_coupling_config(FORMAL)
    )
    assert root["found"]
    assert predicates["character_exchange_valid"]
    assert min(diagnostics["endpoint_character"]["exchange_deltas"]) > 0.99


def test_tracking_nonfinite_overlap_fails_closed():
    points = _synthetic_avoided_crossing_points()
    points[1][1]["eigenvectors"][0, 1] = np.nan
    with pytest.raises(ValueError, match="overlap_invalid"):
        _track_complete_grid(points)


def test_fixed_refinement_valid_L0_L4_completes_numerically():
    levels = _fixed_levels(final_drift=0.004)
    result = _fixed_refinement_completion(levels, 0.005)
    assert result["completed"] and result["reason"] is None
    assert result["final_drift_MHz"] == pytest.approx(0.004)
    termination, completion = _resolve_refinement_termination(
        "max_refinement_exhausted", True, levels, 0.005
    )
    assert termination == "fixed_refinement_completed" and completion["completed"]
    tracked = _track_complete_grid(_synthetic_avoided_crossing_points())
    predicates, _, _ = _physical_evidence(tracked, "fixed_refinement_completed", load_q1_q2_coupling_config(FORMAL))
    assert predicates["numerically_converged"]
    assert predicates["character_exchange_valid"]


@pytest.mark.parametrize(
    ("mutator", "reason"),
    [
        (lambda levels: levels.__setitem__(4, {**levels[4], "level_to_level_drift_MHz": float("nan")}), "nonfinite"),
        (lambda levels: levels.__setitem__(4, {**levels[4], "level_to_level_drift_MHz": 0.006, "minimum_splitting_MHz": 4.994}), "exceeds"),
        (lambda levels: levels.pop(2), "exactly_L0"),
        (lambda levels: levels.__setitem__(4, {**levels[4], "bracket_keys": None}), "bracket_missing"),
        (lambda levels: levels.__setitem__(3, {**levels[3], "boundary": True}), "boundary"),
    ],
)
def test_fixed_refinement_invalid_structures_do_not_complete(mutator, reason):
    levels = _fixed_levels(final_drift=0.004)
    mutator(levels)
    result = _fixed_refinement_completion(levels, 0.005)
    assert not result["completed"]
    assert reason in result["reason"]


def test_fixed_numerical_completion_survives_physical_failure():
    predicates, diagnostics, root = _physical_evidence(
        [], "fixed_refinement_completed", load_q1_q2_coupling_config(FORMAL)
    )
    assert predicates["numerically_converged"]
    assert not predicates["tracking_valid"]
    classified = {
        **predicates,
        "runtime_valid": True,
        "minimum_interior": True,
        "uncertainty_valid": False,
    }
    assert classify_q1_q2_crossing(classified) == "low_subspace_continuity"
    assert diagnostics["reason"] == "empty_evidence_domain" and not root["found"]


def test_adaptive_termination_meanings_are_unchanged():
    config = load_q1_q2_coupling_config(FORMAL)
    tracked = _track_complete_grid(_synthetic_avoided_crossing_points())
    converged, _, _ = _physical_evidence(tracked, "converged", config)
    exhausted, _, _ = _physical_evidence(tracked, "max_refinement_exhausted", config)
    assert converged["numerically_converged"]
    assert not exhausted["numerically_converged"]


def test_stage31_solver_bindings_are_exact12_and_fail_closed():
    assert len(STAGE31_BINDING_KEYS) == 12
    expected = {key: "A" * 64 for key in STAGE31_BINDING_KEYS}
    errors = []
    _validate_stage31_bindings(dict(expected), expected, errors)
    assert errors == []
    attacks = []
    missing = dict(expected); missing.pop("stage3_1_c2_2_fixed_refinement_remediation_sha256"); attacks.append(missing)
    extra = {**expected, "attacker": "A" * 64}; attacks.append(extra)
    stale = {**expected, "stage3_1_c2_2_fixed_refinement_remediation_sha256": "B" * 64}; attacks.append(stale)
    malformed = {**expected, "stage3_1_c2_2_fixed_refinement_remediation_sha256": "not-a-hash"}; attacks.append(malformed)
    tampered = {**expected, "stage3_1_source_tree_sha256": "C" * 64}; attacks.append(tampered)
    for bindings in attacks:
        errors = []
        _validate_stage31_bindings(bindings, expected, errors)
        assert errors


def test_pure_finalization_only_exposes_abs_g_for_resolved_anchor():
    config = load_q1_q2_coupling_config(FORMAL)
    predicates = {
        "minimum_interior": True, "numerically_converged": True, "tracking_valid": True, "subspace_continuity_valid": True,
        "target_bare_projector_valid": True, "total_excitation_valid": True, "coupler_fraction_valid": True,
        "bare_detuning_root_valid": True, "resonance_aligned": True, "character_exchange_valid": True,
    }
    crossing = QubitCrossingScanEvidence(
        "0.200000000000", (), (), ("0.09", "0.11"), (), {"found": True}, 8.0,
        "0.100000000000", predicates, {}, None,
    )
    scan = QubitCouplingScanResult(("0.200000000000",), (crossing,), 1, 0, 1.0)
    convergence = QubitCrossingConvergenceReport(("0.200000000000",), {"0.200000000000": {"passed": True, "U_total_MHz": 0.01}}, True)
    runtime = _runtime(True)
    final = finalize_q1_q2_crossings(config, scan, convergence, runtime).points[0]
    assert final["status"] == "resolved"
    assert final["abs_g_eff_MHz"] == 4.0


def test_staged_transaction_publishes_three_files_and_cleans_failure(tmp_path):
    result = _synthetic_result(1.0)
    gate = result.computational_gate
    target = tmp_path / "published"
    report = publish_stage3_1_transaction(
        result,
        lambda artifact, notebook: assemble_stage3_1_verification_report(gate, artifact, notebook),
        target,
    )
    assert sorted(path.name for path in target.iterdir()) == [
        "q1_q2_coupling_artifacts.json", "verification.ipynb", "verification_report.json",
    ]
    assert report.artifact_write.path == target / "q1_q2_coupling_artifacts.json"
    assert report.notebook_write.path == target / "verification.ipynb"
    assert report.artifact_write.path.exists() and report.notebook_write.path.exists()
    notebook = nbformat.read(report.notebook_write.path, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    assert "data = json.loads" in code[0].source
    for cell in code:
        cell.execution_count = None
        cell.outputs = []
    rerun = execute_stage3_1_notebook(notebook, target)
    rerun_code = [cell for cell in rerun.cells if cell.cell_type == "code"]
    assert all(cell.execution_count is not None for cell in rerun_code)
    assert not any(output.output_type == "error" for cell in rerun_code for output in cell.outputs)
    failed = tmp_path / "failed"
    with pytest.raises(RuntimeError):
        publish_stage3_1_transaction(result, lambda *_: (_ for _ in ()).throw(RuntimeError("stop")), failed)
    assert not failed.exists()


def test_cli_dispatches_schema_02_experiment_to_stage31(monkeypatch, tmp_path):
    class Report:
        ok = True

        def to_dict(self):
            return {"ok": True}

    called = []
    monkeypatch.setattr(cli, "verify_q1_q2_coupling", lambda *args: called.append(args) or Report())
    assert cli.main(["verify-spectrum", str(SMOKE), "--output", str(tmp_path)]) == 0
    assert called and called[0][0] == SMOKE


def test_modulation_zero_uncertainty_denominator_is_null_and_fails():
    config = load_q1_q2_coupling_config(FORMAL)
    points = []
    for key, splitting in (("0.200000000000", 10.0), ("0.270000000000", 12.0), ("0.385000000000", 15.0)):
        points.append({"coupler_flux_key": key, "status": "resolved", "minimum_splitting_MHz": splitting, "uncertainty": {"U_total_MHz": 0.0}})
    result = evaluate_coupling_modulation(config, FinalizedQubitCouplingResult(tuple(row["coupler_flux_key"] for row in points), tuple(points)))
    assert not result.passed
    assert all(row["modulation_significance"] is None for row in result.comparisons)


def test_runtime_ledger_totals_and_zero_remaining_are_exact():
    config = load_q1_q2_coupling_config(FORMAL)
    ledger = {
        "idle_baseline": _phase_row(1, 0, 0, 0, 1.0),
        "idle_refinements": _phase_row(0, 3, 0, 0, 2.0),
        "outer_inner_scans": _phase_row(600, 0, 100, 0, 3.0),
        "anchor_cutoff_convergence": _phase_row(0, 450, 0, 75, 4.0),
    }
    runtime = build_stage31_runtime_report(config, ledger, {"3375": 0.2, "4275": 0.3}, 12.5)
    assert runtime.evaluations_by_dimension == {"3375": 601, "4275": 453}
    assert runtime.cache_hits_by_dimension == {"3375": 100, "4275": 75}
    assert runtime.solver_evaluations == 1054
    assert runtime.remaining_by_dimension == {"3375": 0, "4275": 0}
    assert runtime.projected_total_seconds == runtime.analysis_elapsed_seconds == 12.5
    assert runtime.within_budget and runtime.within_ceiling


def test_runtime_preflight_uses_conservative_1381_bound():
    started = stage31.time.perf_counter()
    report = preflight_stage3_1_phase(
        started, {}, {"3375": 874, "4275": 507}, {"3375": 0.1, "4275": 0.2}, 1800.0, 1381,
    )
    assert report["projected_total_evaluations"] == 1381
    with pytest.raises(ValueError, match="runtime_budget_exceeded"):
        preflight_stage3_1_phase(
            started, {}, {"3375": 874, "4275": 507}, {"3375": 2.0, "4275": 2.0}, 1800.0, 1381,
        )
    with pytest.raises(ValueError, match="solve_ceiling"):
        preflight_stage3_1_phase(
            started, {}, {"3375": 875, "4275": 507}, {"3375": 0.0, "4275": 0.0}, 1800.0, 1381,
        )


def test_refined_scan_uses_character_endpoints_17_points_and_forced_bracket(monkeypatch):
    config = load_q1_q2_coupling_config(FORMAL)
    predicates = {
        "minimum_interior": True, "numerically_converged": True, "tracking_valid": True,
        "subspace_continuity_valid": True, "target_bare_projector_valid": True,
        "total_excitation_valid": True, "coupler_fraction_valid": True,
        "bare_detuning_root_valid": True, "resonance_aligned": True, "character_exchange_valid": True,
    }
    baseline = QubitCrossingScanEvidence(
        "0.200000000000", (), ({"level_to_level_drift_MHz": 0.0},),
        ("0.099000000000", "0.101000000000"), (), {"found": True}, 5.0,
        "0.100000000000", predicates,
        {"character_endpoint_keys": ["0.080000000000", "0.120000000000"]}, None,
    )
    scan = QubitCouplingScanResult(
        tuple(canonical for canonical in ("0.200000000000", "0.270000000000", "0.385000000000")),
        tuple(replace(baseline, coupler_flux_key=key) for key in ("0.200000000000", "0.270000000000", "0.385000000000")),
        0, 0, 0.0,
    )
    calls = []

    def fake_scan(context, refined_config, coupler, cache, basis, forced, bounds, fixed):
        calls.append((refined_config, tuple(forced), tuple(bounds), fixed))
        grid = stage31.decimal_flux_grid(bounds[0], bounds[1], refined_config.scan.inner_coarse_points)
        return replace(
            baseline,
            coupler_flux_key=stage31.canonical_flux_text(coupler),
            refinement_levels=({"grid_keys": list(grid), "level_to_level_drift_MHz": 0.0},),
            final_evidence_keys=("0.100000000000",),
        )

    monkeypatch.setattr(stage31, "_scan_crossing", fake_scan)
    context = SimpleNamespace(
        hamiltonian_config=SimpleNamespace(basis=SimpleNamespace(charge_cutoffs={"q1": 7, "c": 7, "q2": 7})),
        solver_backend_report=SimpleNamespace(validated_solver_error_GHz=0.0),
    )
    report = check_q1_q2_crossing_convergence(context, config, scan)
    assert len(calls) == 9
    assert all(call[0].scan.inner_coarse_points == 17 and call[0].scan.max_refinement_levels == 4 for call in calls)
    assert all(call[1] == ("0.099000000000", "0.101000000000", "0.100000000000") for call in calls)
    assert all(call[2] == (0.08, 0.12) and call[3] is True for call in calls)
    assert all(len(row["initial_grid_keys"]) == 17 for anchor in report.anchors.values() for row in anchor["rows"])


def test_verification_report_preserves_computational_checks():
    gate = Stage31ComputationalGate(
        False, "stage3_1_blocked", ({"name": "physics", "passed": False},), ("physics",),
    )
    artifact = ArtifactWriteResult(True, Path("artifact.json"), "A" * 64, True)
    from sqvm.spectrum.stage31_models import NotebookWriteResult

    notebook = NotebookWriteResult(True, Path("verification.ipynb"), "B" * 64, 2, 2, 0)
    report = assemble_stage3_1_verification_report(gate, artifact, notebook)
    payload = report.to_dict()
    assert payload["computational_gate"] == gate.to_dict()
    assert payload["checks"] == list(gate.checks)
    assert payload["post_write_checks"] != payload["checks"]


def test_writer_requires_typed_result_and_rejects_nested_inf(tmp_path):
    with pytest.raises(TypeError):
        q1_q2_coupling_result_to_payload({})
    result = _synthetic_result(float("inf"))
    with pytest.raises(ValueError, match="non-finite"):
        write_q1_q2_coupling_artifacts(result, tmp_path)
    assert not (tmp_path / "q1_q2_coupling_artifacts.json").exists()


def test_writer_is_canonical_and_development_result_never_self_approves(tmp_path):
    result = _synthetic_result(1.0)
    written = write_q1_q2_coupling_artifacts(result, tmp_path)
    raw = written.path.read_bytes()
    payload = json.loads(raw)
    assert raw == canonical_json_bytes(payload)
    assert set(payload["computational_gate"]) == {
        "computational_ready", "status", "checks", "blocking_reasons",
    }
    assert payload["computational_gate"]["computational_ready"] is False


def test_acceptance_approval_missing_fails_closed(tmp_path):
    artifact = tmp_path / "artifact.json"
    report = tmp_path / "report.json"
    notebook = tmp_path / "verification.ipynb"
    approval = tmp_path / "missing.json"
    artifact.write_bytes(canonical_json_bytes({"artifact_type": "stage_03_1_q1_q2_coupling"}))
    report.write_bytes(canonical_json_bytes({"ok": True, "acceptance_candidate_ready": True}))
    notebook.write_bytes(b"{}")
    result = validate_stage3_1_acceptance_approval(FORMAL, artifact, notebook, report, approval)
    assert not result.ok and not result.stage4_ready


def _synthetic_result(value):
    config = load_q1_q2_coupling_config(SMOKE)
    raw = {
        "coupler_flux_key": "0.270000000000", "minimum_key": "0.100000000000",
        "minimum_splitting_MHz": value, "evaluated_points": [], "refinement_levels": [],
        "final_bracket_keys": None, "final_evidence_keys": [], "resonance_root": {"found": False},
        "predicates": {}, "diagnostics": {}, "abs_g_eff_MHz": None,
    }
    point = {
        "coupler_flux_key": "0.270000000000", "minimum_q2_flux_key": "0.100000000000",
        "minimum_splitting_MHz": value, "abs_g_eff_MHz": None, "predicates": {},
        "uncertainty": None, "status": "not_run_smoke", "raw_evidence": raw,
    }
    finalized = FinalizedQubitCouplingResult(("0.270000000000",), (point,))
    checks = ({"name": "profile_not_acceptance", "passed": False},)
    gate = Stage31ComputationalGate(False, "not_run_smoke", checks, ("profile_not_acceptance",))
    runtime = _runtime(False)
    return QubitCouplingSweepResult(
        config, {"ok": True}, {"ok": True}, {}, {}, QubitCouplingScanResult(("0.270000000000",), (), 0, 0, 0.0),
        finalized, QubitCrossingConvergenceReport((), {}, False), CouplingModulationReport("0.270000000000", (), False),
        runtime, gate, checks,
    )


def _runtime(acceptance):
    config = load_q1_q2_coupling_config(FORMAL if acceptance else SMOKE)
    ledger = {
        "idle_baseline": _phase_row(1, 0, 0, 0, 0.1),
        "idle_refinements": _phase_row(0, 3 if acceptance else 0, 0, 0, 0.1),
        "outer_inner_scans": _phase_row(1, 0, 0, 0, 0.1),
        "anchor_cutoff_convergence": _phase_row(0, 1 if acceptance else 0, 0, 0, 0.1),
    }
    return build_stage31_runtime_report(config, ledger, {"3375": 0.1, "4275": 0.1}, 1.0)


def _phase_row(eval3375, eval4275, hit3375, hit4275, elapsed):
    return {
        "evaluations_by_dimension": {"3375": eval3375, "4275": eval4275},
        "cache_hits_by_dimension": {"3375": hit3375, "4275": hit4275},
        "elapsed_seconds": elapsed,
    }


def _synthetic_avoided_crossing_points():
    basis = np.eye(4)
    points = []
    angles = np.linspace(0.0, np.pi / 2.0, 5)
    for index, angle in enumerate(angles):
        branch_100 = np.cos(angle) * basis[:, 2] + np.sin(angle) * basis[:, 1]
        branch_001 = -np.sin(angle) * basis[:, 2] + np.cos(angle) * basis[:, 1]
        vectors = np.column_stack((basis[:, 0], branch_100, branch_001, basis[:, 3]))
        fresh_100, fresh_001 = ((1, 2) if index < 3 else (2, 1))
        q2 = 0.08 + index * 0.01
        row = {
            "flux_keys": full_flux_keys(0.1, 0.27, q2),
            "bare_detuning_MHz": (0.1 - q2) * 1000.0,
            "fresh_bare_assignment": {
                "100": {"eigen_index": fresh_100},
                "001": {"eigen_index": fresh_001},
                "010": {"eigen_index": 3},
            },
        }
        internal = {
            "eigenvalues_GHz": np.array([0.0, 5.0 - 0.001 * index, 5.01 + 0.001 * index, 7.0]),
            "eigenvectors": vectors,
            "fresh_indices": {"100": fresh_100, "001": fresh_001, "010": 3},
            "target_bare_vectors": np.column_stack((basis[:, 2], basis[:, 1])),
            "mode_eigenvectors": {"q1": np.eye(2), "c": np.eye(1), "q2": np.eye(2)},
            "dimensions": {"q1": 2, "c": 1, "q2": 2},
        }
        points.append([row, internal])
    return points


def _fixed_levels(final_drift):
    splittings = [5.04, 5.03, 5.02, 5.0, 5.0 - final_drift]
    return [
        {
            "level": level,
            "minimum_key": "0.100000000000",
            "minimum_splitting_MHz": splitting,
            "bracket_keys": ["0.099000000000", "0.101000000000"],
            "level_to_level_drift_MHz": None if level == 0 else abs(splitting - splittings[level - 1]),
            "boundary": False,
        }
        for level, splitting in enumerate(splittings)
    ]
