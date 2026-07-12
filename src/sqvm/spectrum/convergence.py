"""Static metric and avoided-crossing convergence gates."""

from __future__ import annotations

import math
from decimal import Decimal
from numbers import Real
from typing import Any

from sqvm.spectrum.analysis import analyze_static_point
from sqvm.spectrum.flux import (
    CANDIDATE_LABELS,
    CANDIDATE_MODES,
    CANDIDATE_ORDER,
    _track_cached_points,
    classify_crossing_candidate,
    decimal_flux_grid,
    detect_character_exchange,
    minimum_with_neighbor_bracket,
)
from sqvm.spectrum.models import (
    CrossingConvergenceReport,
    FluxScanResult,
    SpectrumBuildContext,
    SpectrumConfig,
    StaticMetricConvergenceReport,
    StaticSpectrumPointResult,
)


def check_static_metric_convergence(
    config: SpectrumConfig,
    context: SpectrumBuildContext,
    baseline: StaticSpectrumPointResult,
) -> StaticMetricConvergenceReport:
    rows: list[dict[str, Any]] = []
    max_frequency = 0.0
    max_anharmonicity = 0.0
    max_zz = 0.0
    baseline_cutoffs = dict(baseline.cutoffs)
    for mode in ("q1", "c", "q2"):
        refined_cutoffs = dict(baseline_cutoffs)
        refined_cutoffs[mode] += config.convergence.cutoff_increment
        refined = analyze_static_point(context, config, basis_overrides={mode: refined_cutoffs[mode]})
        frequency_drift = {
            key: abs(refined.metrics.transition_frequencies_GHz[key] - value) * 1000.0
            for key, value in baseline.metrics.transition_frequencies_GHz.items()
        }
        anharmonicity_drift = {
            key: abs(refined.metrics.anharmonicities_GHz[key] - value) * 1000.0
            for key, value in baseline.metrics.anharmonicities_GHz.items()
        }
        zz_drift = {
            key: abs(refined.metrics.zz_metrics_GHz[key] - value) * 1000.0
            for key, value in baseline.metrics.zz_metrics_GHz.items()
        }
        max_frequency = max(max_frequency, *frequency_drift.values())
        max_anharmonicity = max(max_anharmonicity, *anharmonicity_drift.values())
        max_zz = max(max_zz, *zz_drift.values())
        rows.append(
            {
                "refined_mode": mode,
                "baseline_cutoffs": baseline_cutoffs,
                "refined_cutoffs": refined_cutoffs,
                "frequency_drift_MHz": frequency_drift,
                "anharmonicity_drift_MHz": anharmonicity_drift,
                "zz_drift_MHz": zz_drift,
            }
        )
    passed = (
        max_frequency <= config.convergence.frequency_tolerance_MHz
        and max_anharmonicity <= config.convergence.anharmonicity_tolerance_MHz
        and max_zz <= config.convergence.zz_absolute_tolerance_MHz
    )
    return StaticMetricConvergenceReport(
        baseline_cutoffs=baseline_cutoffs,
        refinement_increment=config.convergence.cutoff_increment,
        tolerances_MHz={
            "frequency": config.convergence.frequency_tolerance_MHz,
            "anharmonicity": config.convergence.anharmonicity_tolerance_MHz,
            "zz": config.convergence.zz_absolute_tolerance_MHz,
        },
        per_mode_refinement_rows=tuple(rows),
        max_frequency_drift_MHz=max_frequency,
        max_anharmonicity_drift_MHz=max_anharmonicity,
        max_zz_drift_MHz=max_zz,
        passed=passed,
    )


def crossing_uncertainty_summary(
    *,
    splitting_MHz: float | None,
    rows: list[dict[str, Any]],
    level_to_level_drift_MHz: float | None,
    validated_solver_error_MHz: float | None,
    absolute_tolerance_MHz: float,
    relative_tolerance: float,
    significance_min_ratio: float,
    participation_tolerance: float,
) -> dict[str, Any]:
    required_modes = ("q1", "c", "q2")
    reasons: set[str] = set()
    row_by_mode: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            reasons.add("refinement_row_not_mapping")
            continue
        mode = row.get("refined_mode")
        if mode not in required_modes:
            reasons.add("refinement_row_mode_invalid")
        elif mode in row_by_mode:
            reasons.add(f"refinement_row_duplicate_{mode}")
        else:
            row_by_mode[mode] = row
    all_modes = len(rows) == 3 and tuple(sorted(row_by_mode)) == tuple(sorted(required_modes))
    if not all_modes:
        reasons.add("refinement_rows_must_be_exactly_q1_c_q2")

    splitting = _finite_number(splitting_MHz)
    if splitting is None:
        reasons.add("splitting_MHz_missing_or_nonfinite")
    elif splitting == 0.0:
        reasons.add("splitting_MHz_zero")
    level_drift = _finite_number(level_to_level_drift_MHz)
    if level_drift is None:
        reasons.add("level_to_level_drift_MHz_missing_or_nonfinite")
    solver_error = _finite_number(validated_solver_error_MHz)
    if solver_error is None:
        reasons.add("validated_solver_error_MHz_missing_or_nonfinite")

    cutoff_values = _row_values(row_by_mode, required_modes, "absolute_drift_MHz", reasons)
    flux_values = _row_values(row_by_mode, required_modes, "flux_drift_equivalent_MHz", reasons)
    participation_values = _row_values(
        row_by_mode,
        required_modes,
        "participation_fraction_max_drift",
        reasons,
    )
    slope_inputs_valid = True
    if all_modes:
        for mode in required_modes:
            row = row_by_mode[mode]
            if row.get("flux_slope_valid") is not True:
                slope_inputs_valid = False
                reason = row.get("flux_slope_unavailable_reason")
                suffix = reason if isinstance(reason, str) and reason else "reason_missing"
                reasons.add(f"{mode}.flux_slope_invalid:{suffix}")

    u_cutoff = sum(abs(value) for value in cutoff_values) if cutoff_values is not None else None
    u_flux = sum(abs(value) for value in flux_values) if flux_values is not None else None
    participation = max(participation_values) if participation_values is not None else None
    core_inputs_valid = all(
        value is not None
        for value in (splitting, level_drift, solver_error, u_cutoff, u_flux)
    ) and splitting != 0.0 and slope_inputs_valid
    u_total = (
        max(u_cutoff, u_flux, abs(level_drift), abs(solver_error))
        if core_inputs_valid
        else None
    )
    relative = u_total / abs(splitting) if u_total is not None and splitting not in (None, 0.0) else None
    significance = abs(splitting) / max(u_total, 1e-12) if u_total is not None and splitting is not None else None
    uncertainty_inputs_valid = core_inputs_valid and participation is not None and all_modes
    character = all_modes and all(bool(row_by_mode[mode].get("character_exchange_preserved")) for mode in required_modes)
    minimum_in_bracket = all_modes and all(
        bool(row_by_mode[mode].get("minimum_within_baseline_final_bracket")) for mode in required_modes
    )
    minimum_in_evidence_domain = all_modes and all(
        bool(row_by_mode[mode].get("refined_minimum_within_evidence_domain")) for mode in required_modes
    )
    passed = (
        uncertainty_inputs_valid
        and minimum_in_bracket
        and minimum_in_evidence_domain
        and u_total is not None
        and u_total <= absolute_tolerance_MHz
        and relative is not None
        and relative <= relative_tolerance
        and significance is not None
        and significance >= significance_min_ratio
        and u_flux is not None
        and u_flux <= absolute_tolerance_MHz
        and participation is not None
        and participation <= participation_tolerance
        and character
    )
    return {
        "rows": rows,
        "U_cutoff_MHz": u_cutoff,
        "U_flux_MHz": u_flux,
        "U_total_MHz": u_total,
        "relative_uncertainty": relative,
        "significance_ratio": significance,
        "participation_fraction_max_drift": participation,
        "all_modes_refined": all_modes,
        "uncertainty_inputs_valid": uncertainty_inputs_valid,
        "unavailable_reasons": sorted(reasons),
        "character_exchange_preserved": character,
        "minimum_within_baseline_final_bracket": minimum_in_bracket,
        "refined_minimum_within_evidence_domain": minimum_in_evidence_domain,
        "passed": passed,
    }


def check_crossing_convergence(
    config: SpectrumConfig,
    context: SpectrumBuildContext,
    flux_scan: FluxScanResult,
) -> CrossingConvergenceReport:
    summaries: dict[str, dict[str, Any]] = {}
    candidate_by_name = {row["name"]: row for row in flux_scan.candidates}
    for name in CANDIDATE_ORDER:
        candidate = candidate_by_name.get(name, {"status": "not_run", "splitting_MHz": 0.0})
        rows = list(candidate.get("refinement_rows", []))
        if not rows and _candidate_has_refinement_domain(candidate):
            rows = _run_candidate_refinements(config, context, flux_scan, candidate)
        uncertainty = crossing_uncertainty_summary(
            splitting_MHz=candidate.get("splitting_MHz"),
            rows=rows,
            level_to_level_drift_MHz=candidate.get("level_to_level_drift_MHz"),
            validated_solver_error_MHz=context.solver_backend_report.validated_solver_error_GHz * 1000.0,
            absolute_tolerance_MHz=config.convergence.avoided_crossing_absolute_tolerance_MHz,
            relative_tolerance=config.convergence.avoided_crossing_relative_tolerance,
            significance_min_ratio=config.crossing_evidence.splitting_significance_min_ratio,
            participation_tolerance=config.convergence.participation_fraction_tolerance,
        )
        predicates = {
            "runtime_valid": True,
            "numerically_converged": uncertainty["all_modes_refined"],
            "cutoff_shift_outside_baseline_bracket": not uncertainty[
                "minimum_within_baseline_final_bracket"
            ],
            "refined_crossing_outside_evidence_domain": not uncertainty[
                "refined_minimum_within_evidence_domain"
            ],
            "minimum_interior": bool(candidate.get("minimum_is_interior")),
            "resolution_converged": candidate.get("termination_reason") == "converged",
            "continuity_valid": float(candidate.get("minimum_branch_overlap", 0.0))
            >= config.dressed_labeling.continuity_min_overlap,
            "target_pair_participation_valid": bool(candidate.get("target_pair_participation_valid", False)),
            "character_exchange": bool(candidate.get("character_exchange", {}).get("passed", False)),
            "uncertainty_inputs_valid": uncertainty["uncertainty_inputs_valid"],
            "uncertainty_valid": uncertainty["passed"],
            "significance_valid": uncertainty["significance_ratio"] is not None
            and uncertainty["significance_ratio"] >= config.crossing_evidence.splitting_significance_min_ratio,
            "all_modes_refined": uncertainty["all_modes_refined"],
            "bare_detuning_sign_change": bool(candidate.get("bare_detuning_sign_change", {}).get("passed", False)),
        }
        status = classify_crossing_candidate(predicates)
        summaries[name] = {**uncertainty, "predicates": predicates, "status": status}
    return CrossingConvergenceReport(
        candidates=summaries,
        passed=all(value["status"] == "resolved" for value in summaries.values()),
    )


def _candidate_has_refinement_domain(candidate: dict[str, Any]) -> bool:
    evidence = candidate.get("character_evidence_keys")
    bracket = candidate.get("final_bracket")
    return (
        isinstance(evidence, dict)
        and all(isinstance(evidence.get(key), str) for key in ("left", "minimum", "right"))
        and isinstance(bracket, (list, tuple))
        and len(bracket) == 2
    )


def _run_candidate_refinements(
    config: SpectrumConfig,
    context: SpectrumBuildContext,
    flux_scan: FluxScanResult,
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    name = candidate["name"]
    evidence = candidate["character_evidence_keys"]
    evidence_left = evidence["left"]
    evidence_minimum = evidence["minimum"]
    evidence_right = evidence["right"]
    baseline_bracket = tuple(candidate["final_bracket"])
    labels = CANDIDATE_LABELS[name]
    modes = CANDIDATE_MODES[name]
    baseline_points = {row.flux_key: row for row in flux_scan.evaluated_points}
    baseline_minimum = candidate["character_evidence_keys"]["minimum"]
    baseline_splitting = float(candidate["splitting_MHz"])
    baseline_slope, baseline_stencil_keys, baseline_stencil_gaps, baseline_slope_reason = _baseline_local_slope(
        name,
        labels,
        baseline_minimum,
        flux_scan,
    )
    rows: list[dict[str, Any]] = []
    baseline_cutoffs = dict(context.hamiltonian_config.basis.charge_cutoffs)
    for refined_mode in ("q1", "c", "q2"):
        refined_cutoffs = dict(baseline_cutoffs)
        refined_cutoffs[refined_mode] += config.convergence.cutoff_increment
        uniform = decimal_flux_grid(
            evidence_left,
            evidence_right,
            config.convergence.crossing_refinement_coarse_points,
            config.flux_scan.flux_key_decimal_places,
        )
        current_grid = tuple(
            sorted(
                set(uniform)
                | {evidence_left, evidence_right, baseline_bracket[0], evidence_minimum, baseline_bracket[1]},
                key=Decimal,
            )
        )
        cache: dict[str, Any] = {}

        def evaluate(keys: tuple[str, ...]) -> None:
            for key in keys:
                if key not in cache:
                    cache[key] = analyze_static_point(
                        context,
                        config,
                        {"c": float(Decimal(key))},
                        {refined_mode: refined_cutoffs[refined_mode]},
                    )

        evaluate(current_grid)
        final_grid = current_grid
        minimum = evidence_minimum
        for _ in range(config.convergence.crossing_refinement_levels + 1):
            _, branch_data = _track_cached_points(cache, config)
            gaps = {
                key: abs(branch_data[key]["energies"][labels[0]] - branch_data[key]["energies"][labels[1]])
                * 1000.0
                for key in final_grid
            }
            try:
                minimum, bracket = minimum_with_neighbor_bracket(final_grid, gaps)
            except ValueError:
                minimum = min(final_grid, key=lambda key: (gaps[key], Decimal(key)))
                break
            new_grid = decimal_flux_grid(
                bracket[0],
                bracket[1],
                config.flux_scan.refinement_points,
                config.flux_scan.flux_key_decimal_places,
            )
            evaluate(new_grid)
            final_grid = new_grid
        _, branch_data = _track_cached_points(cache, config)
        refined_gaps = {
            key: abs(branch_data[key]["energies"][labels[0]] - branch_data[key]["energies"][labels[1]])
            * 1000.0
            for key in final_grid
        }
        minimum = min(final_grid, key=lambda key: (refined_gaps[key], Decimal(key)))
        refined_slope, refined_stencil_keys, refined_stencil_gaps, refined_slope_reason = _local_gap_slope(
            final_grid,
            minimum,
            refined_gaps,
        )
        semantic_anchors = {
            label: max(
                modes,
                key=lambda mode: branch_data[evidence_left]["participation"][label][mode],
            )
            for label in labels
        }
        exchange = detect_character_exchange(
            left=branch_data[evidence_left]["participation"],
            minimum=branch_data[minimum]["participation"],
            right=branch_data[evidence_right]["participation"],
            branch_labels=labels,
            modes=modes,
            endpoint_min_fraction=config.crossing_evidence.endpoint_character_min_fraction,
            exchange_min_delta=config.crossing_evidence.character_exchange_min_delta,
            target_pair_min_fraction=config.crossing_evidence.target_pair_min_fraction_at_crossing,
        )
        participation_drift = _participation_drift(
            labels,
            (evidence_left, evidence_minimum, evidence_right),
            baseline_points,
            branch_data,
        )
        flux_drift = abs(float(Decimal(minimum) - Decimal(baseline_minimum)))
        slope_diagnostic = _combine_slope_diagnostic(
            baseline_slope,
            baseline_slope_reason,
            refined_slope,
            refined_slope_reason,
            flux_drift,
        )
        within_evidence = Decimal(evidence_left) < Decimal(minimum) < Decimal(evidence_right)
        within_bracket = Decimal(baseline_bracket[0]) <= Decimal(minimum) <= Decimal(baseline_bracket[1])
        rows.append(
            {
                "refined_mode": refined_mode,
                "baseline_cutoffs": baseline_cutoffs,
                "refined_cutoffs": refined_cutoffs,
                "baseline_splitting_MHz": baseline_splitting,
                "refined_splitting_MHz": refined_gaps[minimum],
                "absolute_drift_MHz": abs(refined_gaps[minimum] - baseline_splitting),
                "baseline_flux_phi0": float(Decimal(baseline_minimum)),
                "refined_flux_phi0": float(Decimal(minimum)),
                "flux_drift_phi0": flux_drift,
                **slope_diagnostic,
                "flux_slope_stencil_keys": {
                    "baseline": baseline_stencil_keys,
                    "refined": refined_stencil_keys,
                },
                "flux_slope_stencil_gaps_MHz": {
                    "baseline": baseline_stencil_gaps,
                    "refined": refined_stencil_gaps,
                },
                "character_evidence_left_key": evidence_left,
                "character_evidence_right_key": evidence_right,
                "semantic_branch_anchors": semantic_anchors,
                "participation_fraction_max_drift": participation_drift,
                "character_exchange_preserved": exchange["passed"],
                "minimum_within_baseline_final_bracket": within_bracket,
                "refined_minimum_within_evidence_domain": within_evidence,
                "runtime_seconds": sum(point.eigenstates.solve_time_seconds for point in cache.values()),
            }
        )
    return rows


def _baseline_local_slope(
    candidate_name: str,
    labels: tuple[str, str],
    minimum: str,
    flux_scan: FluxScanResult,
) -> tuple[float | None, list[str], list[float], str | None]:
    levels = flux_scan.candidate_levels[candidate_name]
    final_grid = tuple(levels[-1]["grid_keys"])
    if minimum not in final_grid:
        return None, [], [], "minimum_not_in_grid"
    index = final_grid.index(minimum)
    if index == 0 or index == len(final_grid) - 1:
        return None, [], [], "minimum_not_interior"
    point_by_key = {row.flux_key: row for row in flux_scan.evaluated_points}
    keys = [final_grid[index - 1], minimum, final_grid[index + 1]]
    try:
        gap_by_key = {
            key: abs(
                point_by_key[key].branch_energies_GHz[labels[0]]
                - point_by_key[key].branch_energies_GHz[labels[1]]
            )
            * 1000.0
            for key in keys
        }
    except (KeyError, TypeError):
        return None, keys, [], "stencil_point_missing"
    return _local_gap_slope(final_grid, minimum, gap_by_key)


def _local_gap_slope(
    grid: tuple[str, ...],
    minimum: str,
    gap_by_key: dict[str, float],
) -> tuple[float | None, list[str], list[float], str | None]:
    if minimum not in grid:
        return None, [], [], "minimum_not_in_grid"
    index = grid.index(minimum)
    if index == 0 or index == len(grid) - 1:
        return None, [], [], "minimum_not_interior"
    keys = [grid[index - 1], minimum, grid[index + 1]]
    try:
        gaps = [float(gap_by_key[key]) for key in keys]
    except (KeyError, TypeError, ValueError):
        return None, keys, [], "stencil_gap_missing"
    if not all(math.isfinite(value) for value in gaps):
        return None, keys, gaps, "nonfinite_stencil_gap"
    left_denominator = float(abs(Decimal(keys[1]) - Decimal(keys[0])))
    right_denominator = float(abs(Decimal(keys[2]) - Decimal(keys[1])))
    if left_denominator == 0.0 or right_denominator == 0.0:
        return None, keys, gaps, "zero_stencil_denominator"
    slope = max(
        abs(gaps[1] - gaps[0]) / left_denominator,
        abs(gaps[2] - gaps[1]) / right_denominator,
    )
    if not math.isfinite(slope):
        return None, keys, gaps, "nonfinite_local_slope"
    return slope, keys, gaps, None


def _combine_slope_diagnostic(
    baseline_slope: float | None,
    baseline_reason: str | None,
    refined_slope: float | None,
    refined_reason: str | None,
    flux_drift: float,
) -> dict[str, Any]:
    slope_valid = baseline_slope is not None and refined_slope is not None
    slope = max(baseline_slope, refined_slope) if slope_valid else None
    reasons = []
    if baseline_reason is not None:
        reasons.append(f"baseline_{baseline_reason}")
    if refined_reason is not None:
        reasons.append(f"refined_{refined_reason}")
    return {
        "local_gap_slope_MHz_per_phi0": slope,
        "flux_drift_equivalent_MHz": slope * flux_drift if slope is not None else None,
        "flux_slope_valid": slope_valid,
        "flux_slope_unavailable_reason": ";".join(reasons) if reasons else None,
    }


def _row_values(
    rows: dict[str, dict[str, Any]],
    required_modes: tuple[str, str, str],
    field: str,
    reasons: set[str],
) -> list[float] | None:
    values: list[float] = []
    for mode in required_modes:
        row = rows.get(mode)
        value = _finite_number(row.get(field) if row is not None else None)
        if value is None:
            reasons.add(f"{mode}.{field}_missing_or_nonfinite")
        else:
            values.append(value)
    return values if len(values) == len(required_modes) else None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _participation_drift(
    labels: tuple[str, str],
    keys: tuple[str, str, str],
    baseline_points: dict[str, Any],
    refined_branch_data: dict[str, Any],
) -> float:
    drift = 0.0
    for key in keys:
        baseline = baseline_points[key].mode_participation_by_branch
        refined = refined_branch_data[key]["participation"]
        for label in labels:
            for mode in ("q1", "c", "q2"):
                drift = max(
                    drift,
                    abs(baseline[label]["fractions"][mode] - refined[label][mode]),
                )
    return drift
