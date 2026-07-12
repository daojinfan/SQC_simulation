"""Deterministic coupler-flux grids, branch tracking, and crossing evidence."""

from __future__ import annotations

import time
from collections.abc import Mapping
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Callable

import numpy as np
from scipy.optimize import linear_sum_assignment

from sqvm.spectrum.analysis import analyze_static_point
from sqvm.spectrum.models import FluxScanPoint, FluxScanResult, SpectrumBuildContext, SpectrumConfig
from sqvm.spectrum.solver import canonical_flux_text


CANDIDATE_ORDER = ("q1-c", "c-q2")
CANDIDATE_LABELS = {"q1-c": ("100", "010"), "c-q2": ("010", "001")}
CANDIDATE_MODES = {"q1-c": ("q1", "c"), "c-q2": ("c", "q2")}


def decimal_flux_key(value: float | str | Decimal, decimal_places: int = 12) -> str:
    return canonical_flux_text(value, decimal_places)


def decimal_flux_grid(
    start: float | str | Decimal,
    stop: float | str | Decimal,
    count: int,
    decimal_places: int = 12,
) -> tuple[str, ...]:
    if count < 2:
        raise ValueError("flux grid count must be at least two")
    left = Decimal(str(start))
    right = Decimal(str(stop))
    step = (right - left) / Decimal(count - 1)
    keys = [decimal_flux_key(left + step * index, decimal_places) for index in range(count)]
    keys[0] = decimal_flux_key(left, decimal_places)
    keys[-1] = decimal_flux_key(right, decimal_places)
    if len(set(keys)) != count:
        raise ValueError("key_resolution_exhausted")
    return tuple(keys)


def spectrum_cache_key(
    cutoffs: dict[str, int],
    flux_key: str,
    num_states: int,
    solver_backend: str,
) -> tuple[int, int, int, str, int, str]:
    return (
        cutoffs["q1"],
        cutoffs["c"],
        cutoffs["q2"],
        flux_key,
        num_states,
        solver_backend,
    )


def track_branches_one_to_one(previous_vectors: np.ndarray, next_vectors: np.ndarray) -> tuple[dict[int, int], dict[int, float]]:
    if previous_vectors.shape[0] != next_vectors.shape[0]:
        raise ValueError("branch tracking requires equal Hilbert dimensions")
    overlap = np.abs(previous_vectors.conj().T @ next_vectors) ** 2
    count = min(overlap.shape)
    tie = np.arange(overlap.size, dtype=float).reshape(overlap.shape) * np.finfo(float).eps
    previous, following = linear_sum_assignment(-overlap + tie)
    pairs = [(int(left), int(right)) for left, right in zip(previous, following, strict=True) if left < count]
    mapping = dict(pairs)
    scores = {left: float(overlap[left, right]) for left, right in pairs}
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("branch assignment is not one-to-one")
    return mapping, scores


def minimum_with_neighbor_bracket(grid_keys: tuple[str, ...], gap_by_key: dict[str, float]) -> tuple[str, tuple[str, str]]:
    if len(grid_keys) < 3:
        raise ValueError("candidate grid must contain at least three keys")
    minimum = min(grid_keys, key=lambda key: (gap_by_key[key], Decimal(key)))
    index = grid_keys.index(minimum)
    if index == 0 or index == len(grid_keys) - 1:
        raise ValueError("boundary")
    return minimum, (grid_keys[index - 1], grid_keys[index + 1])


def detect_character_exchange(
    *,
    left: dict[str, dict[str, float]],
    minimum: dict[str, dict[str, float]],
    right: dict[str, dict[str, float]],
    branch_labels: tuple[str, str],
    modes: tuple[str, str],
    endpoint_min_fraction: float,
    exchange_min_delta: float,
    target_pair_min_fraction: float,
) -> dict[str, Any]:
    branch_results: dict[str, Any] = {}
    passed = True
    for branch in branch_labels:
        left_difference = left[branch][modes[0]] - left[branch][modes[1]]
        right_difference = right[branch][modes[0]] - right[branch][modes[1]]
        endpoint = max(left[branch][modes[0]], left[branch][modes[1]]) >= endpoint_min_fraction
        exchanged = left_difference * right_difference < 0.0 and abs(right_difference - left_difference) >= exchange_min_delta
        pair_at_minimum = minimum[branch][modes[0]] + minimum[branch][modes[1]] >= target_pair_min_fraction
        branch_passed = endpoint and exchanged and pair_at_minimum
        passed = passed and branch_passed
        branch_results[branch] = {
            "left_difference": left_difference,
            "right_difference": right_difference,
            "endpoint_character_ok": endpoint,
            "exchanged": exchanged,
            "target_pair_at_minimum_ok": pair_at_minimum,
            "passed": branch_passed,
        }
    return {"passed": passed, "branches": branch_results}


def bare_detuning_sign_change(
    detuning_by_key_MHz: dict[str, float],
    tolerance_MHz: float,
) -> dict[str, Any]:
    negative = [key for key, value in detuning_by_key_MHz.items() if value <= -tolerance_MHz]
    positive = [key for key, value in detuning_by_key_MHz.items() if value >= tolerance_MHz]
    return {
        "passed": bool(negative and positive),
        "negative_evidence_keys": sorted(negative, key=_stable_key),
        "positive_evidence_keys": sorted(positive, key=_stable_key),
        "minimum_MHz": min(detuning_by_key_MHz.values(), default=None),
        "maximum_MHz": max(detuning_by_key_MHz.values(), default=None),
    }


def _stable_key(value: str) -> tuple[int, Decimal | str]:
    try:
        return (0, Decimal(value))
    except Exception:
        return (1, value)


def classify_crossing_candidate(predicates: dict[str, bool]) -> str:
    if not predicates.get("runtime_valid", False):
        return "runtime_budget_exceeded"
    if not predicates.get("numerically_converged", False):
        return "numerically_unconverged"
    if predicates.get("cutoff_shift_outside_baseline_bracket", False):
        return "cutoff_shift_outside_baseline_bracket"
    if predicates.get("refined_crossing_outside_evidence_domain", False):
        return "refined_crossing_outside_evidence_domain"
    if not predicates.get("minimum_interior", False):
        return "boundary"
    if not predicates.get("resolution_converged", False):
        return "resolution_limit"
    if not predicates.get("continuity_valid", False):
        return "low_continuity"
    if not predicates.get("target_pair_participation_valid", False):
        return "multimode_overlap"
    if all(
        predicates.get(key, False)
        for key in (
            "character_exchange",
            "uncertainty_inputs_valid",
            "uncertainty_valid",
            "significance_valid",
            "all_modes_refined",
        )
    ):
        return "resolved"
    geometry_too_weak = all(
        predicates.get(key, False)
        for key in (
            "bare_detuning_sign_change",
            "continuity_valid",
            "target_pair_participation_valid",
            "all_modes_refined",
            "uncertainty_inputs_valid",
        )
    ) and not predicates.get("significance_valid", False) and not predicates.get("character_exchange", False)
    if geometry_too_weak:
        return "geometry_too_weak"
    return "character_exchange_failed"


def scan_coupler_flux(config: SpectrumConfig, context: SpectrumBuildContext) -> FluxScanResult:
    started = time.perf_counter()
    if not config.flux_scan.enabled:
        return FluxScanResult(
            target="c",
            evaluated_points=(),
            candidate_levels={key: () for key in CANDIDATE_ORDER},
            candidates=tuple(
                {"name": key, "status": "not_run", "acceptance_eligible": False} for key in CANDIDATE_ORDER
            ),
            solver_evaluations=0,
            cache_hits=0,
            wall_time_seconds=time.perf_counter() - started,
            acceptance_eligible=False,
        )
    keys = decimal_flux_grid(
        config.flux_scan.start_phi0,
        config.flux_scan.stop_phi0,
        config.flux_scan.coarse_points,
        config.flux_scan.flux_key_decimal_places,
    )
    cache: dict[str, Any] = {}
    candidate_grids = {name: keys for name in CANDIDATE_ORDER}
    level_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in CANDIDATE_ORDER}
    previous_minimum: dict[str, float | None] = {name: None for name in CANDIDATE_ORDER}
    active = set(CANDIDATE_ORDER)
    cache_hits = 0

    def evaluate(missing: set[str]) -> None:
        nonlocal cache_hits
        for key in sorted(missing, key=Decimal):
            if key in cache:
                cache_hits += 1
                continue
            cache[key] = analyze_static_point(context, config, {"c": float(Decimal(key))})

    evaluate(set(keys))
    max_levels = config.flux_scan.max_refinement_levels
    for level in range(max_levels + 1):
        tracked_points, branch_data = _track_cached_points(cache, config)
        next_grids: dict[str, tuple[str, ...]] = {}
        for candidate in CANDIDATE_ORDER:
            if candidate not in active:
                continue
            grid = candidate_grids[candidate]
            gap = {
                key: abs(
                    branch_data[key]["energies"][CANDIDATE_LABELS[candidate][0]]
                    - branch_data[key]["energies"][CANDIDATE_LABELS[candidate][1]]
                )
                * 1000.0
                for key in grid
            }
            try:
                minimum, bracket = minimum_with_neighbor_bracket(grid, gap)
                minimum_index = grid.index(minimum)
                left_gap = gap[grid[minimum_index - 1]]
                right_gap = gap[grid[minimum_index + 1]]
                half_width = max(
                    abs(Decimal(minimum) - Decimal(bracket[0])),
                    abs(Decimal(bracket[1]) - Decimal(minimum)),
                )
                slope = max(
                    abs(gap[minimum] - left_gap) / float(abs(Decimal(minimum) - Decimal(bracket[0]))),
                    abs(right_gap - gap[minimum]) / float(abs(Decimal(bracket[1]) - Decimal(minimum))),
                )
                resolution = float(half_width) * slope
                drift = None if previous_minimum[candidate] is None else abs(gap[minimum] - previous_minimum[candidate])
                converged = (
                    level >= config.flux_scan.min_refinement_levels
                    and drift is not None
                    and drift <= config.flux_scan.splitting_level_tolerance_MHz
                    and resolution <= config.flux_scan.flux_energy_resolution_MHz
                )
                termination = "converged" if converged else "continue"
                level_rows[candidate].append(
                    {
                        "level": level,
                        "input_bracket_keys": list(bracket) if level else None,
                        "grid_keys": list(grid),
                        "minimum_key": minimum,
                        "minimum_splitting_MHz": gap[minimum],
                        "level_to_level_drift_MHz": drift,
                        "local_energy_resolution_MHz": resolution,
                        "termination_reason": termination,
                    }
                )
                previous_minimum[candidate] = gap[minimum]
                if converged:
                    active.remove(candidate)
                elif level == max_levels:
                    level_rows[candidate][-1]["termination_reason"] = "resolution_limit"
                    active.remove(candidate)
                else:
                    next_grids[candidate] = decimal_flux_grid(
                        bracket[0],
                        bracket[1],
                        config.flux_scan.refinement_points,
                        config.flux_scan.flux_key_decimal_places,
                    )
            except ValueError as exc:
                level_rows[candidate].append(
                    {
                        "level": level,
                        "input_bracket_keys": None,
                        "grid_keys": list(grid),
                        "minimum_key": min(grid, key=lambda key: (gap[key], Decimal(key))),
                        "minimum_splitting_MHz": min(gap.values()),
                        "level_to_level_drift_MHz": None,
                        "local_energy_resolution_MHz": None,
                        "termination_reason": str(exc),
                    }
                )
                active.remove(candidate)
        if not active:
            break
        union = set().union(*(next_grids[name] for name in CANDIDATE_ORDER if name in next_grids))
        evaluate(union)
        candidate_grids.update(next_grids)

    tracked_points, branch_data = _track_cached_points(cache, config)
    candidates = tuple(
        _build_candidate_summary(name, level_rows[name], branch_data, config)
        for name in CANDIDATE_ORDER
    )
    return FluxScanResult(
        target="c",
        evaluated_points=tracked_points,
        candidate_levels={name: tuple(rows) for name, rows in level_rows.items()},
        candidates=candidates,
        solver_evaluations=len(cache),
        cache_hits=cache_hits,
        wall_time_seconds=time.perf_counter() - started,
        acceptance_eligible=config.acceptance_eligible,
    )


def _track_cached_points(cache: dict[str, Any], config: SpectrumConfig) -> tuple[tuple[FluxScanPoint, ...], dict[str, Any]]:
    ordered = sorted(cache, key=Decimal)
    first = cache[ordered[0]]
    first_assignments = first.dressed_states.by_label()
    branch_indices = {label: first_assignments[label].eigen_index for label in ("100", "010", "001")}
    previous_vectors = first.eigenstates.eigenvectors
    branch_data: dict[str, Any] = {}
    result: list[FluxScanPoint] = []
    for position, key in enumerate(ordered):
        point = cache[key]
        continuity = {label: 1.0 for label in branch_indices}
        if position:
            mapping, scores = track_branches_one_to_one(previous_vectors, point.eigenstates.eigenvectors)
            continuity = {label: scores[index] for label, index in branch_indices.items()}
            branch_indices = {label: mapping[index] for label, index in branch_indices.items()}
        participation_by_index = {row.eigen_index: row for row in point.participation.rows}
        participation_by_branch: dict[str, dict[str, Any]] = {}
        energies: dict[str, float] = {}
        for label, index in branch_indices.items():
            row = participation_by_index.get(index)
            if row is None:
                raise ValueError(f"tracked branch {label} lacks participation at flux {key}")
            fractions = row.fractions or {"q1": 0.0, "c": 0.0, "q2": 0.0}
            participation_by_branch[label] = {
                "mean_excitations": dict(row.mean_excitations),
                "fractions": dict(fractions),
            }
            energies[label] = float(point.eigenstates.eigenvalues_GHz[index])
        bare = point.bare_transition_frequencies_GHz
        detuning = {
            "q1-c": (bare["c"] - bare["q1"]) * 1000.0,
            "c-q2": (bare["c"] - bare["q2"]) * 1000.0,
        }
        branch_data[key] = {
            "energies": energies,
            "participation": {label: value["fractions"] for label, value in participation_by_branch.items()},
            "continuity": continuity,
            "detuning": detuning,
        }
        result.append(
            FluxScanPoint(
                flux_key=key,
                flux_bias_phi0=float(Decimal(key)),
                eigenvalue_gaps_GHz=tuple(float(value) for value in point.eigenstates.gaps_GHz),
                bare_transition_frequencies_GHz=dict(bare),
                bare_detunings_MHz=detuning,
                branch_energies_GHz=energies,
                branch_continuity_overlaps=continuity,
                mode_participation_by_branch=participation_by_branch,
                assignment_summary={row.label.key: row.eigen_index for row in point.dressed_states.assignments},
            )
        )
        previous_vectors = point.eigenstates.eigenvectors
    return tuple(result), branch_data


def _build_candidate_summary(
    name: str,
    levels: list[dict[str, Any]],
    branch_data: dict[str, Any],
    config: SpectrumConfig,
) -> dict[str, Any]:
    final = levels[-1]
    minimum = final["minimum_key"]
    ordered = sorted(branch_data, key=Decimal)
    minimum_index = ordered.index(minimum)
    left_candidates = ordered[:minimum_index]
    right_candidates = ordered[minimum_index + 1 :]
    labels = CANDIDATE_LABELS[name]
    modes = CANDIDATE_MODES[name]

    def endpoint_ok(key: str) -> bool:
        participation = branch_data[key]["participation"]
        return all(max(participation[label][modes[0]], participation[label][modes[1]]) >= config.crossing_evidence.endpoint_character_min_fraction for label in labels)

    left_key = next((key for key in reversed(left_candidates) if endpoint_ok(key)), None)
    right_key = next((key for key in right_candidates if endpoint_ok(key)), None)
    exchange = {"passed": False, "reason": "character_evidence_missing"}
    if left_key and right_key:
        exchange = detect_character_exchange(
            left=branch_data[left_key]["participation"],
            minimum=branch_data[minimum]["participation"],
            right=branch_data[right_key]["participation"],
            branch_labels=labels,
            modes=modes,
            endpoint_min_fraction=config.crossing_evidence.endpoint_character_min_fraction,
            exchange_min_delta=config.crossing_evidence.character_exchange_min_delta,
            target_pair_min_fraction=config.crossing_evidence.target_pair_min_fraction_at_crossing,
        )
    exchange_branches = exchange.get("branches") if isinstance(exchange, Mapping) else None
    target_pair_participation_valid = isinstance(exchange_branches, Mapping) and all(
        isinstance(exchange_branches.get(label), Mapping)
        and exchange_branches[label].get("target_pair_at_minimum_ok") is True
        for label in labels
    )
    detuning = {key: branch_data[key]["detuning"][name] for key in ordered}
    detuning_evidence = bare_detuning_sign_change(detuning, config.convergence.frequency_tolerance_MHz)
    continuity = min(
        (branch_data[key]["continuity"][label] for key in ordered for label in labels),
        default=0.0,
    )
    bracket = final.get("input_bracket_keys")
    status = "unresolved"
    return {
        "name": name,
        "branch_a": labels[0],
        "branch_b": labels[1],
        "flux_bias_phi0_at_min": float(Decimal(minimum)),
        "splitting_MHz": final["minimum_splitting_MHz"],
        "splitting_GHz": final["minimum_splitting_MHz"] / 1000.0,
        "refinement_levels_completed": final["level"],
        "minimum_is_interior": final["termination_reason"] != "boundary",
        "minimum_branch_overlap": continuity,
        "final_bracket": bracket,
        "character_evidence_keys": {"left": left_key, "minimum": minimum, "right": right_key},
        "character_exchange": exchange,
        "target_pair_participation_valid": target_pair_participation_valid,
        "bare_detuning_sign_change": detuning_evidence,
        "level_to_level_drift_MHz": final.get("level_to_level_drift_MHz"),
        "termination_reason": final["termination_reason"],
        "status": status,
        "acceptance_eligible": config.acceptance_eligible,
    }
