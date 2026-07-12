"""Stage 3.1 q1-q2 crossing scan, convergence, and computational gate."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import math
import time
from typing import Any, Mapping

import numpy as np
from scipy.optimize import linear_sum_assignment

from sqvm.spectrum.analysis import analyze_static_point
from sqvm.spectrum.flux import decimal_flux_grid, minimum_with_neighbor_bracket
from sqvm.spectrum.solver import canonical_flux_text
from sqvm.spectrum.stage31_models import (
    CouplingModulationReport,
    FinalizedQubitCouplingResult,
    QubitCouplingConfig,
    QubitCouplingScanResult,
    QubitCouplingSweepResult,
    QubitCrossingConvergenceReport,
    QubitCrossingScanEvidence,
    Stage31ComputationalGate,
    Stage31RuntimeReport,
)


StageGateDecision = Stage31ComputationalGate


TARGET_LABELS = ("100", "001")
SPECTATOR_LABEL = "010"
STATUS_PRIORITY = (
    "runtime_budget_exceeded",
    "boundary",
    "numerically_unconverged",
    "low_subspace_continuity",
    "pair_subspace_invalid",
    "coupler_hybridized",
    "resonance_misaligned",
    "character_exchange_failed",
    "uncertainty_failed",
    "resolved",
)
PHASE_ORDER = (
    "idle_baseline",
    "idle_refinements",
    "outer_inner_scans",
    "anchor_cutoff_convergence",
)
INITIAL_REMAINING_BY_DIMENSION = {"3375": 874, "4275": 507}


def full_flux_keys(q1: float, c: float, q2: float) -> dict[str, str]:
    return {mode: canonical_flux_text(value) for mode, value in (("q1", q1), ("c", c), ("q2", q2))}


def stage31_cache_identity(
    cutoffs: Mapping[str, int],
    flux_keys: Mapping[str, str],
    num_states: int,
    backend: str,
) -> tuple[Any, ...]:
    return (
        cutoffs["q1"], cutoffs["c"], cutoffs["q2"],
        flux_keys["q1"], flux_keys["c"], flux_keys["q2"], num_states, backend,
    )


class _PointCache:
    def __init__(self) -> None:
        self.values: dict[tuple[Any, ...], tuple[dict[str, Any], dict[str, Any]]] = {}
        self.evaluations = 0
        self.hits = 0
        self.evaluations_by_dimension = {"3375": 0, "4275": 0}
        self.cache_hits_by_dimension = {"3375": 0, "4275": 0}


def scan_q1_q2_crossing(context, config: QubitCouplingConfig, coupler_flux_phi0: float) -> QubitCrossingScanEvidence:
    cache = _PointCache()
    return _scan_crossing(context, config, coupler_flux_phi0, cache, None, (), None, False)


def scan_q1_q2_coupling_vs_coupler(context, config: QubitCouplingConfig) -> QubitCouplingScanResult:
    started = time.perf_counter()
    cache = _PointCache()
    crossings = tuple(
        _scan_crossing(context, config, value, cache, None, (), None, False)
        for value in config.scan.coupler_flux_points_phi0
    )
    return QubitCouplingScanResult(
        coupler_flux_keys=tuple(canonical_flux_text(value) for value in config.scan.coupler_flux_points_phi0),
        crossings=crossings,
        solver_evaluations=cache.evaluations,
        cache_hits=cache.hits,
        elapsed_seconds=time.perf_counter() - started,
        evaluations_by_dimension=dict(cache.evaluations_by_dimension),
        cache_hits_by_dimension=dict(cache.cache_hits_by_dimension),
    )


def _scan_crossing(
    context,
    config,
    coupler_flux,
    cache,
    basis_overrides,
    forced_keys,
    initial_bounds,
    fixed_levels,
):
    start, stop = initial_bounds or (config.scan.inner_start_phi0, config.scan.inner_stop_phi0)
    coarse = decimal_flux_grid(
        start,
        stop,
        config.scan.inner_coarse_points,
    )
    evaluated: set[str] = set(forced_keys)
    levels: list[dict[str, Any]] = []
    current = coarse
    previous_bracket: tuple[str, str] | None = None
    consecutive = 0
    termination = "max_refinement_exhausted"
    final_bracket: tuple[str, str] | None = None
    minimum_key = current[0]
    previous_splitting: float | None = None

    for level in range(config.scan.max_refinement_levels + 1):
        for key in forced_keys:
            _point(context, config, coupler_flux, float(Decimal(key)), cache, basis_overrides)
        for key in current:
            _point(context, config, coupler_flux, float(Decimal(key)), cache, basis_overrides)
            evaluated.add(key)
        ordered = tuple(sorted(evaluated, key=Decimal))
        tracked = _track_complete_grid(
            [_point(context, config, coupler_flux, float(Decimal(key)), cache, basis_overrides) for key in ordered]
        )
        gaps = {row["flux_keys"]["q2"]: row["splitting_MHz"] for row in tracked}
        if fixed_levels and any(not math.isfinite(float(value)) for value in gaps.values()):
            termination = "fixed_refinement_invalid"
            break
        minimum_key, bracket = minimum_with_neighbor_bracket(ordered, gaps)
        minimum_index = ordered.index(minimum_key)
        boundary = minimum_index in (0, len(ordered) - 1)
        splitting = gaps[minimum_key]
        drift = None if previous_splitting is None else abs(splitting - previous_splitting)
        width = float(Decimal(bracket[1]) - Decimal(bracket[0])) if not boundary else None
        strict_shrink = previous_bracket is not None and width is not None and (
            Decimal(bracket[1]) - Decimal(bracket[0])
            < Decimal(previous_bracket[1]) - Decimal(previous_bracket[0])
        )
        transition_passed = (
            level >= config.scan.min_refinement_levels
            and drift is not None
            and drift <= config.convergence.splitting_level_tolerance_MHz
            and width is not None
            and _local_resolution_MHz(ordered, gaps, minimum_index) <= config.convergence.flux_energy_resolution_MHz
            and strict_shrink
        )
        consecutive = consecutive + 1 if transition_passed else 0
        levels.append({
            "level": level,
            "grid_keys": list(current),
            "minimum_key": minimum_key,
            "minimum_splitting_MHz": splitting,
            "bracket_keys": None if boundary else list(bracket),
            "level_to_level_drift_MHz": drift,
            "strict_bracket_shrink": strict_shrink,
            "transition_passed": transition_passed,
            "boundary": boundary,
        })
        if boundary:
            termination = "boundary"
            final_bracket = None
            break
        final_bracket = bracket
        if consecutive >= 2 and not fixed_levels:
            termination = "converged"
            break
        if level == config.scan.max_refinement_levels:
            break
        previous_bracket = bracket
        previous_splitting = splitting
        current = decimal_flux_grid(float(Decimal(bracket[0])), float(Decimal(bracket[1])), config.scan.refinement_points)

    fixed_completion = None
    termination, fixed_completion = _resolve_refinement_termination(
        termination,
        fixed_levels,
        levels,
        config.convergence.splitting_level_tolerance_MHz,
    )

    ordered = tuple(sorted(evaluated, key=Decimal))
    point_rows = [_point(context, config, coupler_flux, float(Decimal(key)), cache, basis_overrides) for key in ordered]
    serialized = _track_complete_grid(point_rows)
    evidence_keys, endpoint_diagnostics = _evidence_domain(serialized, final_bracket, minimum_key, config)
    domain = [row for row in serialized if row["flux_keys"]["q2"] in evidence_keys]
    predicates, diagnostics, root = _physical_evidence(domain, termination, config)
    minimum_row = min(serialized, key=lambda row: (row["splitting_MHz"], Decimal(row["flux_keys"]["q2"])))
    return QubitCrossingScanEvidence(
        coupler_flux_key=canonical_flux_text(coupler_flux),
        evaluated_points=tuple(serialized),
        refinement_levels=tuple(levels),
        final_bracket_keys=final_bracket,
        final_evidence_keys=tuple(evidence_keys),
        resonance_root=root,
        minimum_splitting_MHz=float(minimum_row["splitting_MHz"]),
        minimum_key=minimum_row["flux_keys"]["q2"],
        predicates=predicates,
        diagnostics={
            **diagnostics,
            **endpoint_diagnostics,
            "termination_reason": termination,
            "fixed_completion": fixed_completion,
        },
        abs_g_eff_MHz=None,
    )


def _point(context, config, coupler_flux, q2_flux, cache, basis_overrides):
    cutoffs = dict(context.hamiltonian_config.basis.charge_cutoffs)
    if basis_overrides:
        cutoffs.update(basis_overrides)
    keys = full_flux_keys(config.scan.fixed_q1_flux_phi0, coupler_flux, q2_flux)
    identity = stage31_cache_identity(cutoffs, keys, context.solver_spec.num_states, context.solver_spec.backend)
    dimension = str(math.prod(2 * cutoffs[mode] + 1 for mode in ("q1", "c", "q2")))
    if identity in cache.values:
        cache.hits += 1
        cache.cache_hits_by_dimension[dimension] += 1
        return cache.values[identity]
    result = analyze_static_point(
        context, config,
        {"q1": config.scan.fixed_q1_flux_phi0, "c": coupler_flux, "q2": q2_flux},
        basis_overrides,
    )
    assignments = result.dressed_states.by_label()
    label_index = {label.key: index for index, label in enumerate(result.bare_catalog.labels)}
    target_bare_indices = [label_index[label] for label in TARGET_LABELS]
    row = {
        "flux_keys": keys,
        "flux_biases_phi0": {key: float(value) for key, value in keys.items()},
        "cache_identity": list(identity),
        "cutoffs": cutoffs,
        "eigenvalue_gaps_GHz": [float(value) for value in result.eigenstates.gaps_GHz],
        "bare_transition_frequencies_GHz": dict(result.bare_transition_frequencies_GHz),
        "bare_detuning_MHz": (
            result.bare_transition_frequencies_GHz["q1"] - result.bare_transition_frequencies_GHz["q2"]
        ) * 1000.0,
        "fresh_bare_assignment": {
            label: assignments[label].to_dict() for label in (*TARGET_LABELS, SPECTATOR_LABEL)
        },
        "solve_time_seconds": result.eigenstates.solve_time_seconds,
    }
    internal = {
        "eigenvalues_GHz": result.eigenstates.eigenvalues_GHz,
        "eigenvectors": result.eigenstates.eigenvectors,
        "fresh_indices": {
            label: assignments[label].eigen_index for label in (*TARGET_LABELS, SPECTATOR_LABEL)
        },
        "target_bare_vectors": result.bare_catalog.target_vectors[:, target_bare_indices],
        "mode_eigenvectors": result.bare_catalog.mode_eigenvectors,
        "dimensions": result.bare_catalog.dimensions,
    }
    cache.values[identity] = (row, internal)
    cache.evaluations += 1
    cache.evaluations_by_dimension[dimension] += 1
    return cache.values[identity]


def _track_complete_grid(points):
    if not points:
        return []
    labels = (*TARGET_LABELS, SPECTATOR_LABEL)
    first_row, first = points[0]
    tracked_indices = dict(first["fresh_indices"])
    previous_vectors = first["eigenvectors"][:, [tracked_indices[label] for label in labels]]
    result = [_tracked_row(first_row, first, tracked_indices, None, None, True)]
    for row, internal in points[1:]:
        vectors = internal["eigenvectors"]
        overlap = np.abs(previous_vectors.conj().T @ vectors) ** 2
        if overlap.shape[0] != len(labels) or not np.all(np.isfinite(overlap)):
            raise ValueError("tracked_branch_overlap_invalid")
        tie = np.arange(overlap.size, dtype=float).reshape(overlap.shape) * np.finfo(float).eps
        left, right = linear_sum_assignment(-overlap + tie)
        if tuple(left) != tuple(range(len(labels))) or len(set(right.tolist())) != len(labels):
            raise ValueError("tracked_branch_assignment_ambiguous")
        chosen = overlap[left, right]
        if not np.all(np.isfinite(chosen)) or np.any(chosen <= 0.0):
            raise ValueError("tracked_branch_assignment_nonfinite_or_zero")
        tracked_indices = {label: int(right[index]) for index, label in enumerate(labels)}
        current_vectors = vectors[:, right]
        target_overlap = previous_vectors[:, :2].conj().T @ current_vectors[:, :2]
        singular = np.linalg.svd(target_overlap, compute_uv=False)
        sigma2 = float(np.min(singular) ** 2)
        if not math.isfinite(sigma2):
            raise ValueError("tracked_target_subspace_continuity_nonfinite")
        adjacent = {label: float(chosen[index]) for index, label in enumerate(labels)}
        result.append(_tracked_row(row, internal, tracked_indices, adjacent, sigma2, True))
        previous_vectors = current_vectors
    return result


def _tracked_row(row, internal, tracked_indices, adjacent, sigma2, tracking_valid):
    values = internal["eigenvalues_GHz"]
    ground = float(values[0])
    branch_energies = {
        label: float(values[index] - ground) for label, index in tracked_indices.items()
    }
    target_bare = internal["target_bare_vectors"]
    projector = {}
    participation = {}
    for label, index in tracked_indices.items():
        vector = internal["eigenvectors"][:, index]
        if label in TARGET_LABELS:
            projector[label] = float(np.sum(np.abs(target_bare.conj().T @ vector) ** 2))
        participation[label] = _participation_for_index(label, index, vector, internal)
    return {
        **row,
        "tracked_eigen_indices": dict(tracked_indices),
        "branch_energies_GHz": branch_energies,
        "splitting_MHz": abs(branch_energies["100"] - branch_energies["001"]) * 1000.0,
        "target_bare_projector_weights": projector,
        "participation": participation,
        "adjacent_branch_overlaps": adjacent,
        "previous_target_subspace_sigma_min_squared": sigma2,
        "tracking_valid": bool(tracking_valid),
    }


def _participation_for_index(label, eigen_index, vector, internal):
    dimensions = tuple(internal["dimensions"][mode] for mode in ("q1", "c", "q2"))
    uq1 = internal["mode_eigenvectors"]["q1"].conj()
    uc = internal["mode_eigenvectors"]["c"].conj()
    uq2 = internal["mode_eigenvectors"]["q2"].conj()
    charge_tensor = vector.reshape(dimensions)
    beta = np.einsum("ia,jb,kc,ijk->abc", uq1, uc, uq2, charge_tensor, optimize=True)
    probability = np.abs(beta) ** 2
    grids = np.meshgrid(*(np.arange(size, dtype=float) for size in dimensions), indexing="ij")
    mean = {
        mode: float(np.sum(grid * probability))
        for mode, grid in zip(("q1", "c", "q2"), grids, strict=True)
    }
    total = float(sum(mean.values()))
    fractions = None if total < 1e-12 else {mode: value / total for mode, value in mean.items()}
    return {
        "label": label,
        "eigen_index": eigen_index,
        "mean_excitations": mean,
        "fractions": fractions,
        "total_mean_excitation": total,
    }


def _subspace_continuity(points):
    values: list[float | None] = [None]
    for (_, left), (_, right) in zip(points, points[1:]):
        singular = np.linalg.svd(left.conj().T @ right, compute_uv=False)
        values.append(float(np.min(singular) ** 2))
    return values


def _local_resolution_MHz(keys, gaps, index):
    if index == 0 or index == len(keys) - 1:
        return math.inf
    return max(abs(gaps[keys[index]] - gaps[keys[index - 1]]), abs(gaps[keys[index + 1]] - gaps[keys[index]]))


def _fixed_refinement_completion(levels, tolerance_MHz):
    if not isinstance(levels, list) or len(levels) != 5:
        return {"completed": False, "reason": "fixed_levels_must_be_exactly_L0_through_L4"}
    if [row.get("level") for row in levels if isinstance(row, dict)] != [0, 1, 2, 3, 4]:
        return {"completed": False, "reason": "fixed_level_sequence_invalid"}
    if any(row.get("boundary") is not False for row in levels):
        return {"completed": False, "reason": "fixed_level_boundary"}
    final = levels[4]
    previous = levels[3]
    bracket = final.get("bracket_keys")
    minimum = final.get("minimum_key")
    if not isinstance(bracket, list) or len(bracket) != 2 or not isinstance(minimum, str):
        return {"completed": False, "reason": "fixed_final_bracket_missing"}
    try:
        left, middle, right = Decimal(bracket[0]), Decimal(minimum), Decimal(bracket[1])
    except Exception:
        return {"completed": False, "reason": "fixed_final_bracket_invalid"}
    if not left.is_finite() or not middle.is_finite() or not right.is_finite() or not left < middle < right:
        return {"completed": False, "reason": "fixed_final_bracket_not_distinct_interior"}
    previous_splitting = _finite_or_none(previous.get("minimum_splitting_MHz"))
    final_splitting = _finite_or_none(final.get("minimum_splitting_MHz"))
    final_drift = _finite_or_none(final.get("level_to_level_drift_MHz"))
    tolerance = _finite_or_none(tolerance_MHz)
    if None in (previous_splitting, final_splitting, final_drift, tolerance):
        return {"completed": False, "reason": "fixed_final_drift_nonfinite"}
    expected_drift = abs(final_splitting - previous_splitting)
    if not math.isclose(final_drift, expected_drift, rel_tol=1e-12, abs_tol=1e-15):
        return {"completed": False, "reason": "fixed_final_drift_mismatch"}
    if final_drift > tolerance:
        return {"completed": False, "reason": "fixed_final_drift_exceeds_tolerance"}
    return {
        "completed": True,
        "reason": None,
        "final_drift_MHz": final_drift,
    }


def _resolve_refinement_termination(termination, fixed_levels, levels, tolerance_MHz):
    if not fixed_levels or termination == "boundary":
        return termination, None
    completion = _fixed_refinement_completion(levels, tolerance_MHz)
    return (
        "fixed_refinement_completed" if completion["completed"] else "fixed_refinement_invalid",
        completion,
    )


def _evidence_domain(rows, bracket, minimum_key, config):
    if not rows or minimum_key not in {row["flux_keys"]["q2"] for row in rows}:
        return (), {
            "character_endpoints_found": False,
            "character_endpoint_keys": None,
            "character_endpoint_reason": "minimum_missing_from_tracked_grid",
        }
    minimum_index = next(index for index, row in enumerate(rows) if row["flux_keys"]["q2"] == minimum_key)
    left = next(
        (index for index in range(minimum_index - 1, -1, -1) if _character_orientation(rows[index], config) == "left"),
        None,
    )
    right = next(
        (index for index in range(minimum_index + 1, len(rows)) if _character_orientation(rows[index], config) == "right"),
        None,
    )
    if left is None or right is None:
        missing = "left" if left is None else "right"
        if left is None and right is None:
            missing = "left_and_right"
        return (), {
            "character_endpoints_found": False,
            "character_endpoint_keys": None,
            "character_endpoint_reason": f"missing_{missing}_character_endpoint",
        }
    ordered = tuple(row["flux_keys"]["q2"] for row in rows)
    selected = set(ordered[left : right + 1])
    selected.add(minimum_key)
    if bracket:
        selected.update(bracket)
    evidence = tuple(key for key in ordered if key in selected)
    return evidence, {
        "character_endpoints_found": True,
        "character_endpoint_keys": [ordered[left], ordered[right]],
        "character_endpoint_reason": None,
    }


def _character_orientation(row, config):
    try:
        first = row["participation"]["100"]["fractions"]
        second = row["participation"]["001"]["fractions"]
        if not isinstance(first, dict) or not isinstance(second, dict):
            return None
        values = [first.get("q1"), first.get("q2"), second.get("q1"), second.get("q2")]
        if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in values):
            return None
        threshold = config.evidence.endpoint_character_min_fraction
        if first["q1"] >= threshold and second["q2"] >= threshold and first["q1"] > first["q2"] and second["q2"] > second["q1"]:
            return "left"
        if first["q2"] >= threshold and second["q1"] >= threshold and first["q2"] > first["q1"] and second["q1"] > second["q2"]:
            return "right"
    except (KeyError, TypeError):
        return None
    return None


def _physical_evidence(rows, termination, config):
    if not rows:
        return {
            "minimum_interior": termination != "boundary",
            "numerically_converged": termination in {"converged", "fixed_refinement_completed"},
            "subspace_continuity_valid": False,
            "target_bare_projector_valid": False,
            "total_excitation_valid": False,
            "coupler_fraction_valid": False,
            "bare_detuning_root_valid": False,
            "resonance_aligned": False,
            "character_exchange_valid": False,
            "tracking_valid": False,
        }, {"reason": "empty_evidence_domain"}, {"found": False, "q2_flux_key": None, "method": "none"}
    projector_ok = all(
        row["target_bare_projector_weights"][label] >= config.evidence.target_bare_projector_min_weight
        for row in rows for label in TARGET_LABELS
    )
    excitation_ok = all(
        config.evidence.target_total_excitation_min
        <= row["participation"][label]["total_mean_excitation"]
        <= config.evidence.target_total_excitation_max
        for row in rows for label in TARGET_LABELS
    )
    coupler_ok = all(
        (row["participation"][label]["fractions"] or {}).get("c", math.inf)
        <= config.evidence.coupler_fraction_max
        for row in rows for label in TARGET_LABELS
    )
    continuity_values = [row["previous_target_subspace_sigma_min_squared"] for row in rows[1:]]
    continuity_ok = bool(continuity_values) and all(
        isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in continuity_values
    ) and min(continuity_values) >= config.evidence.target_subspace_continuity_min
    tracking_ok = all(row.get("tracking_valid") is True for row in rows)
    root = _detuning_root(rows)
    minimum = min(rows, key=lambda row: (row["splitting_MHz"], Decimal(row["flux_keys"]["q2"])))
    aligned = root["found"] and abs(minimum["bare_detuning_MHz"]) <= config.evidence.resonance_alignment_tolerance_MHz
    endpoints = (rows[0], rows[-1])
    left_100 = endpoints[0]["participation"]["100"]["fractions"] or {}
    left_001 = endpoints[0]["participation"]["001"]["fractions"] or {}
    right_100 = endpoints[1]["participation"]["100"]["fractions"] or {}
    right_001 = endpoints[1]["participation"]["001"]["fractions"] or {}
    high_character = (
        left_100.get("q1", 0.0), left_001.get("q2", 0.0),
        right_100.get("q2", 0.0), right_001.get("q1", 0.0),
    )
    exchange_deltas = (
        left_100.get("q1", 0.0) - right_100.get("q1", 0.0),
        right_100.get("q2", 0.0) - left_100.get("q2", 0.0),
        right_001.get("q1", 0.0) - left_001.get("q1", 0.0),
        left_001.get("q2", 0.0) - right_001.get("q2", 0.0),
    )
    character = (
        min(high_character) >= config.evidence.endpoint_character_min_fraction
        and min(exchange_deltas) >= config.evidence.character_exchange_min_delta
    )
    predicates = {
        "minimum_interior": termination != "boundary",
        "numerically_converged": termination in {"converged", "fixed_refinement_completed"},
        "subspace_continuity_valid": continuity_ok,
        "target_bare_projector_valid": projector_ok,
        "total_excitation_valid": excitation_ok,
        "coupler_fraction_valid": coupler_ok,
        "bare_detuning_root_valid": root["found"],
        "resonance_aligned": bool(aligned),
        "character_exchange_valid": bool(character),
        "tracking_valid": tracking_ok,
    }
    return predicates, {
        "minimum_bare_detuning_MHz": minimum["bare_detuning_MHz"],
        "minimum_target_subspace_sigma_min_squared": min(continuity_values) if continuity_values else None,
        "endpoint_character": {
            "left_100": dict(left_100), "left_001": dict(left_001),
            "right_100": dict(right_100), "right_001": dict(right_001),
            "exchange_deltas": list(exchange_deltas),
        },
    }, root


def _detuning_root(rows):
    ordered = sorted(rows, key=lambda row: Decimal(row["flux_keys"]["q2"]))
    for row in ordered:
        if row["bare_detuning_MHz"] == 0.0:
            return {"found": True, "q2_flux_key": row["flux_keys"]["q2"], "method": "exact"}
    for left, right in zip(ordered, ordered[1:]):
        a, b = left["bare_detuning_MHz"], right["bare_detuning_MHz"]
        if a * b < 0:
            x0, x1 = Decimal(left["flux_keys"]["q2"]), Decimal(right["flux_keys"]["q2"])
            root = x0 + (x1 - x0) * Decimal(str(abs(a) / (abs(a) + abs(b))))
            return {"found": True, "q2_flux_key": canonical_flux_text(root), "method": "linear_bare_detuning"}
    return {"found": False, "q2_flux_key": None, "method": "none"}


def check_q1_q2_crossing_convergence(context, config: QubitCouplingConfig, scan: QubitCouplingScanResult) -> QubitCrossingConvergenceReport:
    started = time.perf_counter()
    if not config.acceptance_eligible:
        return QubitCrossingConvergenceReport(anchor_keys=(), anchors={}, passed=False)
    by_key = scan.by_key()
    expected = tuple(canonical_flux_text(value) for value in config.scan.acceptance_anchor_fluxes_phi0)
    if any(key not in by_key for key in expected):
        return QubitCrossingConvergenceReport(expected, {}, False)
    anchors = {}
    evaluations = {"3375": 0, "4275": 0}
    cache_hits = {"3375": 0, "4275": 0}
    base_cutoffs = dict(context.hamiltonian_config.basis.charge_cutoffs)
    for key in expected:
        baseline = by_key[key]
        rows = []
        endpoint_keys = baseline.diagnostics.get("character_endpoint_keys")
        for mode in ("q1", "c", "q2"):
            if not isinstance(endpoint_keys, list) or len(endpoint_keys) != 2:
                rows.append({
                    "refined_mode": mode,
                    "baseline_splitting_MHz": baseline.minimum_splitting_MHz,
                    "refined_splitting_MHz": None,
                    "absolute_drift_MHz": None,
                    "minimum_within_baseline_final_bracket": False,
                    "refined_minimum_within_evidence_domain": False,
                    "physical_predicates_preserved": False,
                    "failure_reason": "baseline_character_endpoints_missing",
                })
                continue
            refined_scan = replace(
                config.scan,
                inner_coarse_points=config.convergence.crossing_refinement_coarse_points,
                min_refinement_levels=config.convergence.crossing_refinement_levels,
                max_refinement_levels=config.convergence.crossing_refinement_levels,
            )
            refined_config = replace(config, scan=refined_scan)
            refined_cache = _PointCache()
            refined = _scan_crossing(
                context, refined_config, float(Decimal(key)), refined_cache,
                {mode: base_cutoffs[mode] + config.convergence.cutoff_increment},
                tuple(dict.fromkeys((*(baseline.final_bracket_keys or ()), baseline.minimum_key))),
                (float(Decimal(endpoint_keys[0])), float(Decimal(endpoint_keys[1]))),
                True,
            )
            dimension = str((2 * (base_cutoffs[mode] + config.convergence.cutoff_increment) + 1)
                            * math.prod(2 * base_cutoffs[other] + 1 for other in ("q1", "c", "q2") if other != mode))
            evaluations[dimension] += refined_cache.evaluations
            cache_hits[dimension] += refined_cache.hits
            drift = abs(refined.minimum_splitting_MHz - baseline.minimum_splitting_MHz)
            rows.append({
                "refined_mode": mode,
                "baseline_splitting_MHz": baseline.minimum_splitting_MHz,
                "refined_splitting_MHz": refined.minimum_splitting_MHz,
                "absolute_drift_MHz": drift,
                "minimum_within_baseline_final_bracket": _within(refined.minimum_key, baseline.final_bracket_keys),
                "refined_minimum_within_evidence_domain": refined.minimum_key in refined.final_evidence_keys,
                "physical_predicates_preserved": all(refined.predicates.values()),
                "character_endpoint_keys": refined.diagnostics.get("character_endpoint_keys"),
                "initial_grid_keys": list(refined.refinement_levels[0]["grid_keys"]),
                "forced_baseline_bracket_keys": list(dict.fromkeys((*(baseline.final_bracket_keys or ()), baseline.minimum_key))),
            })
        final_level_drift = baseline.refinement_levels[-1].get("level_to_level_drift_MHz")
        drift_values = [_finite_or_none(row["absolute_drift_MHz"]) for row in rows]
        u_cutoff = sum(drift_values) if all(value is not None for value in drift_values) else None
        u_inner = _finite_or_none(final_level_drift)
        u_level = _finite_or_none(final_level_drift)
        u_solver = context.solver_backend_report.validated_solver_error_GHz * 1000.0
        inputs = all(value is not None and math.isfinite(value) for value in (u_cutoff, u_inner, u_level, u_solver))
        u_total = max(u_cutoff, u_inner, u_level, u_solver) if inputs else None
        splitting = baseline.minimum_splitting_MHz
        relative = u_total / splitting if u_total is not None and splitting > 0 else None
        significance = splitting / u_total if u_total not in (None, 0.0) else None
        passed = (
            inputs and all(row["absolute_drift_MHz"] is not None and row["absolute_drift_MHz"] <= config.convergence.crossing_absolute_tolerance_MHz for row in rows)
            and all(row["minimum_within_baseline_final_bracket"] and row["refined_minimum_within_evidence_domain"] and row["physical_predicates_preserved"] for row in rows)
            and u_inner <= config.convergence.flux_energy_resolution_MHz
            and u_level <= config.convergence.splitting_level_tolerance_MHz
            and u_total <= config.convergence.crossing_absolute_tolerance_MHz
            and relative is not None
            and relative <= config.convergence.crossing_relative_tolerance
            and significance is not None
            and significance >= config.convergence.splitting_significance_min_ratio
        )
        anchors[key] = {
            "rows": rows, "U_cutoff_MHz": u_cutoff, "U_inner_flux_MHz": u_inner,
            "U_level_MHz": u_level, "U_solver_MHz": u_solver, "U_total_MHz": u_total,
            "relative_uncertainty": relative, "significance_ratio": significance,
            "uncertainty_inputs_valid": inputs, "passed": bool(passed),
        }
    return QubitCrossingConvergenceReport(
        expected,
        anchors,
        all(row["passed"] for row in anchors.values()),
        evaluations,
        cache_hits,
        time.perf_counter() - started,
    )


def classify_q1_q2_crossing(predicates: Mapping[str, Any]) -> str:
    if predicates.get("runtime_valid") is not True:
        return "runtime_budget_exceeded"
    if predicates.get("minimum_interior") is not True:
        return "boundary"
    if predicates.get("numerically_converged") is not True:
        return "numerically_unconverged"
    if predicates.get("tracking_valid") is not True or predicates.get("subspace_continuity_valid") is not True:
        return "low_subspace_continuity"
    if predicates.get("target_bare_projector_valid") is not True or predicates.get("total_excitation_valid") is not True:
        return "pair_subspace_invalid"
    if predicates.get("coupler_fraction_valid") is not True:
        return "coupler_hybridized"
    if predicates.get("bare_detuning_root_valid") is not True or predicates.get("resonance_aligned") is not True:
        return "resonance_misaligned"
    if predicates.get("character_exchange_valid") is not True:
        return "character_exchange_failed"
    if predicates.get("uncertainty_valid") is not True:
        return "uncertainty_failed"
    return "resolved"


def finalize_q1_q2_crossings(config, scan, crossing_convergence, runtime):
    uncertainty = crossing_convergence.anchors
    points = []
    for crossing in scan.crossings:
        convergence = uncertainty.get(crossing.coupler_flux_key)
        predicates = dict(crossing.predicates)
        predicates["runtime_valid"] = bool(getattr(runtime, "within_budget", getattr(runtime, "budget_status", "") == "within_budget"))
        predicates["uncertainty_valid"] = bool(convergence and convergence.get("passed"))
        status = "not_run_smoke" if not config.acceptance_eligible else classify_q1_q2_crossing(predicates)
        points.append({
            "coupler_flux_key": crossing.coupler_flux_key,
            "minimum_q2_flux_key": crossing.minimum_key,
            "minimum_splitting_MHz": crossing.minimum_splitting_MHz,
            "abs_g_eff_MHz": crossing.minimum_splitting_MHz / 2.0 if status == "resolved" else None,
            "predicates": predicates, "uncertainty": convergence, "status": status,
            "raw_evidence": crossing.to_dict(),
        })
    return FinalizedQubitCouplingResult(scan.coupler_flux_keys, tuple(points))


def evaluate_coupling_modulation(config, finalized):
    reference = canonical_flux_text(config.scan.reference_coupler_flux_phi0)
    by_key = finalized.by_key()
    comparisons = []
    for value in config.scan.acceptance_anchor_fluxes_phi0:
        key = canonical_flux_text(value)
        if key == reference:
            continue
        left, right = by_key.get(key), by_key.get(reference)
        valid = bool(left and right and left["status"] == right["status"] == "resolved")
        delta = abs(left["minimum_splitting_MHz"] - right["minimum_splitting_MHz"]) if valid else None
        u_left = left.get("uncertainty", {}).get("U_total_MHz") if valid else None
        u_right = right.get("uncertainty", {}).get("U_total_MHz") if valid else None
        u_delta = u_left + u_right if _all_finite(u_left, u_right) else None
        significance = delta / u_delta if delta is not None and u_delta is not None and u_delta > 0 else None
        comparisons.append({
            "anchor_key": key, "reference_key": reference, "delta_splitting_MHz": delta,
            "U_delta_MHz": u_delta, "modulation_significance": significance,
            "passed": significance is not None and significance >= config.convergence.modulation_significance_min_ratio,
        })
    return CouplingModulationReport(reference, tuple(comparisons), bool(comparisons) and any(row["passed"] for row in comparisons))


def preflight_stage3_1_phase(started, phase_ledger, remaining_by_dimension, p95_by_dimension, budget, ceiling):
    _validate_dimension_mapping(remaining_by_dimension, int, "remaining_by_dimension")
    _validate_dimension_mapping(p95_by_dimension, (int, float), "p95_by_dimension", finite=True)
    if set(phase_ledger) - set(PHASE_ORDER):
        raise ValueError("runtime phase ledger contains an unknown phase")
    completed = sum(
        phase["evaluations_by_dimension"][dimension]
        for phase in phase_ledger.values()
        for dimension in ("3375", "4275")
    )
    elapsed = time.perf_counter() - started
    projected_seconds = elapsed + sum(
        remaining_by_dimension[key] * float(p95_by_dimension[key]) for key in ("3375", "4275")
    )
    projected_evaluations = completed + sum(remaining_by_dimension.values())
    if projected_seconds > budget:
        raise ValueError("runtime_budget_exceeded_before_phase")
    if projected_evaluations > ceiling:
        raise ValueError("solve_ceiling_exceeded_before_phase")
    return {
        "elapsed_seconds": elapsed,
        "projected_total_seconds": projected_seconds,
        "projected_total_evaluations": projected_evaluations,
    }


def build_stage31_runtime_report(config, phase_ledger, p95_by_dimension, analysis_elapsed_seconds):
    if tuple(phase_ledger) != PHASE_ORDER:
        raise ValueError("runtime phase ledger keys/order mismatch")
    for name, phase in phase_ledger.items():
        if set(phase) != {"evaluations_by_dimension", "cache_hits_by_dimension", "elapsed_seconds"}:
            raise ValueError(f"runtime phase {name} fields mismatch")
        _validate_dimension_mapping(phase["evaluations_by_dimension"], int, f"{name}.evaluations")
        _validate_dimension_mapping(phase["cache_hits_by_dimension"], int, f"{name}.cache_hits")
        if not isinstance(phase["elapsed_seconds"], (int, float)) or not math.isfinite(float(phase["elapsed_seconds"])) or phase["elapsed_seconds"] < 0:
            raise ValueError(f"runtime phase {name} elapsed invalid")
    _validate_dimension_mapping(p95_by_dimension, (int, float), "p95_by_dimension", finite=True)
    evaluations = {
        dimension: sum(phase["evaluations_by_dimension"][dimension] for phase in phase_ledger.values())
        for dimension in ("3375", "4275")
    }
    hits = {
        dimension: sum(phase["cache_hits_by_dimension"][dimension] for phase in phase_ledger.values())
        for dimension in ("3375", "4275")
    }
    remaining = {"3375": 0, "4275": 0}
    total = sum(evaluations.values())
    projected = float(analysis_elapsed_seconds)
    budget = config.runtime.acceptance_budget_seconds if config.acceptance_eligible else config.runtime.smoke_budget_seconds
    return Stage31RuntimeReport(
        config.acceptance_eligible,
        budget,
        {name: dict(value) for name, value in phase_ledger.items()},
        {key: float(value) for key, value in p95_by_dimension.items()},
        evaluations,
        hits,
        remaining,
        total,
        sum(hits.values()),
        projected,
        projected,
        total,
        config.runtime.conservative_solve_ceiling,
        projected <= budget,
        total <= config.runtime.conservative_solve_ceiling,
    )


def _validate_dimension_mapping(value, expected_type, name, finite=False):
    if not isinstance(value, dict) or set(value) != {"3375", "4275"}:
        raise ValueError(f"{name} dimension keys mismatch")
    for item in value.values():
        if isinstance(item, bool) or not isinstance(item, expected_type) or item < 0:
            raise ValueError(f"{name} contains invalid value")
        if finite and not math.isfinite(float(item)):
            raise ValueError(f"{name} contains nonfinite value")


def evaluate_stage3_1_gate(config, provenance, solver_backend, idle_convergence, finalized, crossing_convergence, modulation, runtime) -> StageGateDecision:
    by_key = finalized.by_key()
    anchors = tuple(canonical_flux_text(value) for value in config.scan.acceptance_anchor_fluxes_phi0)
    runtime_ok = bool(getattr(runtime, "within_budget", False) and getattr(runtime, "within_ceiling", True))
    checks = [
        {"name": "provenance_ok", "passed": provenance.ok},
        {"name": "solver_validation_ok", "passed": solver_backend.ok and solver_backend.validated_spec is not None},
        {"name": "idle_metric_convergence_ok", "passed": idle_convergence.passed},
    ]
    checks.extend({"name": f"anchor_{key}_resolved", "passed": key in by_key and by_key[key]["status"] == "resolved"} for key in anchors)
    checks.extend((
        {"name": "resolved_coupler_point_count", "passed": sum(row["status"] == "resolved" for row in finalized.points) >= 3},
        {"name": "coupling_modulation", "passed": modulation.passed},
        {"name": "runtime_within_budget", "passed": runtime_ok},
    ))
    ready = config.acceptance_eligible and all(row["passed"] for row in checks)
    blockers = tuple(row["name"] for row in checks if not row["passed"])
    status = "ready_for_stage4" if ready else ("not_run_smoke" if not config.acceptance_eligible else "stage3_1_blocked")
    return StageGateDecision(ready, status, tuple(checks), blockers)


def assemble_q1_q2_coupling_result(config, provenance, solver_backend, idle_metrics, idle_convergence, finalized, crossing_convergence, modulation, runtime, computational_gate):
    empty_scan = QubitCouplingScanResult(finalized.coupler_flux_keys, tuple(), 0, 0, 0.0)
    checks = tuple(computational_gate.checks)
    return QubitCouplingSweepResult(
        config, provenance.to_dict(), solver_backend.to_dict(), idle_metrics, idle_convergence.to_dict(),
        empty_scan, finalized, crossing_convergence, modulation, runtime, computational_gate, checks,
    )


def _within(key, bracket):
    return bool(bracket and Decimal(bracket[0]) <= Decimal(key) <= Decimal(bracket[1]))


def _finite_or_none(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _all_finite(*values):
    return all(_finite_or_none(value) is not None for value in values)
