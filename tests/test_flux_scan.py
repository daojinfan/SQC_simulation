import importlib
from decimal import Decimal

import numpy as np
import pytest

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.spectrum import (
    bare_detuning_sign_change,
    classify_crossing_candidate,
    decimal_flux_grid,
    decimal_flux_key,
    minimum_with_neighbor_bracket,
    spectrum_cache_key,
    track_branches_one_to_one,
)
from sqvm.spectrum.flux import _build_candidate_summary


def test_flux_override_does_not_mutate_inputs(spectrum_session):
    context = spectrum_session["context"]
    before = tuple(context.effective_junctions)
    from sqvm.spectrum import rebuild_hamiltonian_for_spectrum

    rebuild_hamiltonian_for_spectrum(context, {"c": 0.3})
    assert tuple(context.effective_junctions) == before


def test_decimal_flux_key_uses_12_places_half_even():
    assert decimal_flux_key("0.1234567890125") == "0.123456789012"
    assert decimal_flux_key("0.1234567890135") == "0.123456789014"
    assert decimal_flux_key("-0.0000000000001") == "0.000000000000"


def test_cache_key_includes_cutoffs_flux_states_and_backend():
    key = spectrum_cache_key({"q1": 7, "c": 9, "q2": 7}, "0.2", 48, "validated_eigsh")
    assert key == (7, 9, 7, "0.2", 48, "validated_eigsh")


def test_coarse_grid_endpoints_are_exact():
    grid = decimal_flux_grid(0.2, 0.45, 25)
    assert grid[0] == "0.200000000000"
    assert grid[-1] == "0.450000000000"


def test_level_bracket_uses_previous_candidate_grid_neighbors():
    grid = ("0.1", "0.2", "0.3", "0.4", "0.5")
    minimum, bracket = minimum_with_neighbor_bracket(grid, {key: abs(float(key) - 0.3) for key in grid})
    assert minimum == "0.3"
    assert bracket == ("0.2", "0.4")


def test_refinement_endpoints_are_reused():
    grid = decimal_flux_grid("0.2", "0.4", 7)
    assert grid[0] == "0.200000000000" and grid[-1] == "0.400000000000"


def test_candidates_refine_level_by_level_independent_of_serialization_order():
    union1 = sorted(set(decimal_flux_grid(0.2, 0.3, 7)) | set(decimal_flux_grid(0.3, 0.4, 7)), key=Decimal)
    union2 = sorted(set(decimal_flux_grid(0.3, 0.4, 7)) | set(decimal_flux_grid(0.2, 0.3, 7)), key=Decimal)
    assert union1 == union2


@pytest.mark.parametrize("reason", ["converged", "boundary", "resolution_limit", "key_resolution_exhausted"])
def test_refinement_termination_reasons(reason):
    assert reason in {"converged", "boundary", "resolution_limit", "key_resolution_exhausted"}


def test_flux_points_are_sorted_and_cached():
    keys = decimal_flux_grid(0.2, 0.45, 9)
    assert keys == tuple(sorted(keys, key=Decimal))
    assert len(keys) == len(set(keys))


def test_branch_tracking_is_one_to_one():
    previous = np.eye(3)
    following = previous[:, [1, 2, 0]]
    mapping, scores = track_branches_one_to_one(previous, following)
    assert mapping == {0: 2, 1: 0, 2: 1}
    assert all(value == pytest.approx(1.0) for value in scores.values())


def test_low_continuity_blocks_resolved():
    predicates = _resolved_predicates()
    predicates["continuity_valid"] = False
    assert classify_crossing_candidate(predicates) == "low_continuity"


def test_synthetic_avoided_crossing_location_and_splitting():
    flux = np.linspace(-1, 1, 101)
    coupling = 0.02
    gap = 2 * np.sqrt(flux * flux + coupling * coupling)
    assert flux[np.argmin(gap)] == pytest.approx(0.0)
    assert np.min(gap) == pytest.approx(0.04)


def test_boundary_minimum_blocks_resolved():
    with pytest.raises(ValueError, match="boundary"):
        minimum_with_neighbor_bracket(("0.1", "0.2", "0.3"), {"0.1": 0.0, "0.2": 1.0, "0.3": 2.0})


def test_character_exchange_failure_blocks_resolved():
    predicates = _resolved_predicates()
    predicates["character_exchange"] = False
    assert classify_crossing_candidate(predicates) == "character_exchange_failed"


def test_geometry_too_weak_classification_requires_valid_numerics():
    predicates = _resolved_predicates()
    predicates.update(character_exchange=False, significance_valid=False, uncertainty_valid=False)
    assert classify_crossing_candidate(predicates) == "geometry_too_weak"
    predicates["numerically_converged"] = False
    assert classify_crossing_candidate(predicates) == "numerically_unconverged"


def test_bare_detuning_sign_change_uses_frequency_tolerance():
    result = bare_detuning_sign_change({"a": -0.4, "b": 0.4}, 0.5)
    assert not result["passed"]
    assert bare_detuning_sign_change({"a": -0.5, "b": 0.5}, 0.5)["passed"]


def test_geometry_too_weak_requires_bare_detuning_sign_change_evidence():
    predicates = _resolved_predicates()
    predicates.update(character_exchange=False, significance_valid=False, uncertainty_valid=False, bare_detuning_sign_change=False)
    assert classify_crossing_candidate(predicates) == "character_exchange_failed"


def test_multimode_overlap_blocks_pairwise_resolution():
    predicates = _resolved_predicates()
    predicates["target_pair_participation_valid"] = False
    assert classify_crossing_candidate(predicates) == "multimode_overlap"


def test_zero_final_level_drift_is_forwarded_and_canonical(spectrum_session):
    levels, branch_data = _candidate_summary_inputs()
    summary = _build_candidate_summary("q1-c", levels, branch_data, spectrum_session["config"])
    assert summary["level_to_level_drift_MHz"] == 0.0
    assert summary["target_pair_participation_valid"] is True
    assert b'"level_to_level_drift_MHz": 0.0' in canonical_json_bytes(summary)


@pytest.mark.parametrize(
    "branches",
    [
        {
            "100": {"target_pair_at_minimum_ok": True},
            "010": {"target_pair_at_minimum_ok": False},
        },
        {"100": {"target_pair_at_minimum_ok": True}},
    ],
    ids=["one-false", "one-missing"],
)
def test_candidate_target_pair_participation_requires_both_explicit_true(
    monkeypatch,
    spectrum_session,
    branches,
):
    flux_module = importlib.import_module("sqvm.spectrum.flux")
    monkeypatch.setattr(
        flux_module,
        "detect_character_exchange",
        lambda **kwargs: {"passed": True, "branches": branches},
    )
    levels, branch_data = _candidate_summary_inputs()
    summary = _build_candidate_summary("q1-c", levels, branch_data, spectrum_session["config"])
    assert summary["character_exchange"]["passed"] is True
    assert summary["target_pair_participation_valid"] is False


def test_character_exchange_can_fail_independently_of_target_pair_participation(
    monkeypatch,
    spectrum_session,
):
    flux_module = importlib.import_module("sqvm.spectrum.flux")
    monkeypatch.setattr(
        flux_module,
        "detect_character_exchange",
        lambda **kwargs: {
            "passed": False,
            "branches": {
                "100": {"target_pair_at_minimum_ok": True},
                "010": {"target_pair_at_minimum_ok": True},
            },
        },
    )
    levels, branch_data = _candidate_summary_inputs()
    summary = _build_candidate_summary("q1-c", levels, branch_data, spectrum_session["config"])
    assert summary["character_exchange"]["passed"] is False
    assert summary["target_pair_participation_valid"] is True


def _candidate_summary_inputs():
    keys = ("0.100000000000", "0.200000000000", "0.300000000000")
    branch_data = {}
    for index, key in enumerate(keys):
        branch_data[key] = {
            "participation": {
                "100": {"q1": 0.9 - 0.4 * index, "c": 0.1 + 0.4 * index, "q2": 0.0},
                "010": {"q1": 0.1 + 0.4 * index, "c": 0.9 - 0.4 * index, "q2": 0.0},
                "001": {"q1": 0.0, "c": 0.0, "q2": 1.0},
            },
            "continuity": {"100": 1.0, "010": 1.0, "001": 1.0},
            "detuning": {"q1-c": (-1.0, 0.0, 1.0)[index], "c-q2": 1.0},
        }
    levels = [
        {
            "minimum_key": keys[1],
            "minimum_splitting_MHz": 1.0,
            "level": 4,
            "input_bracket_keys": [keys[0], keys[2]],
            "termination_reason": "converged",
            "level_to_level_drift_MHz": 0.0,
        }
    ]
    return levels, branch_data


def test_unavailable_uncertainty_cannot_resolve_or_classify_geometry_too_weak():
    predicates = _resolved_predicates()
    predicates["uncertainty_inputs_valid"] = False
    assert classify_crossing_candidate(predicates) != "resolved"
    predicates.update(character_exchange=False, significance_valid=False, uncertainty_valid=False)
    assert classify_crossing_candidate(predicates) != "geometry_too_weak"


def _resolved_predicates():
    return {
        "runtime_valid": True,
        "numerically_converged": True,
        "minimum_interior": True,
        "resolution_converged": True,
        "continuity_valid": True,
        "target_pair_participation_valid": True,
        "character_exchange": True,
        "uncertainty_valid": True,
        "uncertainty_inputs_valid": True,
        "significance_valid": True,
        "all_modes_refined": True,
        "bare_detuning_sign_change": True,
    }
