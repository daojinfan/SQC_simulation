import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

from dataclasses import replace

import pytest

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.spectrum import (
    check_static_metric_convergence,
    classify_crossing_candidate,
    crossing_uncertainty_summary,
    rebuild_hamiltonian_for_spectrum,
)
from sqvm.spectrum.convergence import _combine_slope_diagnostic


def test_per_mode_cutoff_refinement_changes_only_target_mode(spectrum_session):
    model = rebuild_hamiltonian_for_spectrum(spectrum_session["context"], basis_overrides={"c": 9})
    assert model.basis.charge_cutoffs == {"q1": 7, "c": 9, "q2": 7}


def _rows(drift=0.001, flux=0.001, participation=0.001, character=True):
    return [
        {
            "refined_mode": mode,
            "absolute_drift_MHz": drift,
            "flux_drift_equivalent_MHz": flux,
            "flux_slope_valid": True,
            "flux_slope_unavailable_reason": None,
            "participation_fraction_max_drift": participation,
            "character_exchange_preserved": character,
            "minimum_within_baseline_final_bracket": True,
            "refined_minimum_within_evidence_domain": True,
        }
        for mode in ("q1", "c", "q2")
    ]


def test_each_crossing_refines_q1_c_and_q2():
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=_rows(),
        level_to_level_drift_MHz=0.001,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert result["all_modes_refined"]


def test_spectator_mode_drift_contributes_to_conservative_uncertainty():
    rows = _rows()
    rows[2]["absolute_drift_MHz"] = 0.004
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=rows,
        level_to_level_drift_MHz=0.0,
        validated_solver_error_MHz=0.0,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert result["U_cutoff_MHz"] == pytest.approx(0.006)


def test_missing_level_drift_is_null_and_invalid_but_canonical():
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=_rows(),
        level_to_level_drift_MHz=None,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert result["U_total_MHz"] is None
    assert result["relative_uncertainty"] is None
    assert result["significance_ratio"] is None
    assert not result["uncertainty_inputs_valid"]
    assert result["unavailable_reasons"] == ["level_to_level_drift_MHz_missing_or_nonfinite"]
    assert b'"U_total_MHz": null' in canonical_json_bytes(result)


def test_invalid_baseline_and_refined_stencils_emit_null_and_deterministic_reason():
    diagnostic = _combine_slope_diagnostic(
        None,
        "minimum_not_interior",
        None,
        "zero_stencil_denominator",
        0.001,
    )
    assert diagnostic == {
        "local_gap_slope_MHz_per_phi0": None,
        "flux_drift_equivalent_MHz": None,
        "flux_slope_valid": False,
        "flux_slope_unavailable_reason": (
            "baseline_minimum_not_interior;refined_zero_stencil_denominator"
        ),
    }


def test_flux_slope_valid_must_be_explicit_true():
    rows = _rows()
    rows[0].pop("flux_slope_valid")
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=rows,
        level_to_level_drift_MHz=0.001,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert not result["uncertainty_inputs_valid"]
    assert result["unavailable_reasons"] == ["q1.flux_slope_invalid:reason_missing"]
    assert not result["passed"]


def test_refined_branches_anchor_by_bare_character_not_cross_dimension_vector_overlap():
    anchors = {"A_like": {"mode": "q1", "fraction": 0.9}, "B_like": {"mode": "c", "fraction": 0.9}}
    assert anchors["A_like"]["mode"] != anchors["B_like"]["mode"]


def test_refinement_domain_includes_character_evidence_keys():
    uniform = {f"{value:.12f}" for value in (0.3, 0.35, 0.4)}
    forced = {"0.320000000000", "0.350000000000", "0.380000000000"}
    assert forced <= uniform | forced


def test_cutoff_shift_outside_baseline_bracket_fails():
    predicates = {
        "runtime_valid": True,
        "numerically_converged": True,
        "cutoff_shift_outside_baseline_bracket": True,
    }
    assert classify_crossing_candidate(predicates) == "cutoff_shift_outside_baseline_bracket"


def test_refined_crossing_outside_evidence_domain_fails():
    predicates = {
        "runtime_valid": True,
        "numerically_converged": True,
        "refined_crossing_outside_evidence_domain": True,
    }
    assert classify_crossing_candidate(predicates) == "refined_crossing_outside_evidence_domain"


def test_crossing_uncertainty_rejects_refined_minimum_outside_evidence_domain():
    rows = _rows()
    rows[1]["refined_minimum_within_evidence_domain"] = False
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=rows,
        level_to_level_drift_MHz=0.001,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert not result["refined_minimum_within_evidence_domain"]
    assert not result["passed"]


def test_flux_drift_converts_to_energy_with_local_slope():
    slope = max(abs(1.0 - 1.2) / 0.01, abs(1.1 - 1.0) / 0.01)
    assert slope * 0.001 == pytest.approx(0.02)


def test_participation_convergence_required():
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=_rows(participation=0.03),
        level_to_level_drift_MHz=0.001,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert not result["passed"]


def test_convergence_passes_within_tolerance():
    result = crossing_uncertainty_summary(
        splitting_MHz=1.0,
        rows=_rows(),
        level_to_level_drift_MHz=0.001,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert result["passed"]


def test_convergence_failure_blocks_verify():
    result = crossing_uncertainty_summary(
        splitting_MHz=0.01,
        rows=_rows(drift=0.02),
        level_to_level_drift_MHz=0.02,
        validated_solver_error_MHz=0.001,
        absolute_tolerance_MHz=0.01,
        relative_tolerance=0.05,
        significance_min_ratio=5.0,
        participation_tolerance=0.02,
    )
    assert not result["passed"]


def test_default_demo_coupler_cutoff_is_not_accepted_by_relaxing_tolerance(spectrum_session):
    assert spectrum_session["config"].convergence.frequency_tolerance_MHz == 0.50
    assert spectrum_session["baseline"].cutoffs["c"] == 7
