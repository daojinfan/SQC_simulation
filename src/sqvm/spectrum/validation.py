"""Non-acceptance dense pilot and eigsh validation artifact generation."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256
from sqvm.spectrum.analysis import (
    assign_dressed_states,
    build_bare_state_catalog,
    compute_mode_participation,
    compute_static_metrics,
)
from sqvm.spectrum.config import load_spectrum_config
from sqvm.spectrum.context import build_spectrum_context, rebuild_hamiltonian_for_spectrum
from sqvm.spectrum.flux import CANDIDATE_LABELS, decimal_flux_grid
from sqvm.spectrum.models import EigenstateTable, ValidatedSolverSpec
from sqvm.spectrum.provenance import (
    load_stage2_rebaseline_approval,
    load_stage2_rebaseline_manifest,
    run_stage2_dense_gap_consistency,
    validate_spectrum_provenance,
)
from sqvm.spectrum.solver import (
    build_nonacceptance_solver_report,
    canonical_flux_text,
    dense_near_degenerate_blocks,
    dense_reference_eigensystem,
    environment_fingerprint,
    projector_spectral_error,
    sha256_counter_seed,
    solve_static_eigensystem,
    stage3_solver_source_tree_sha256,
)


def run_dense_pilot(config_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    source_config = load_spectrum_config(config_path)
    pilot_eigsh = replace(source_config.eigen.eigsh, ncv=25)
    pilot_config = replace(
        source_config,
        profile="dense_pilot",
        acceptance_eligible=False,
        eigen=replace(source_config.eigen, num_states=12, solver="dense_eigh", eigsh=pilot_eigsh),
    )
    manifest = load_stage2_rebaseline_manifest(pilot_config.source_rebaseline_manifest)
    approval = load_stage2_rebaseline_approval(pilot_config.source_rebaseline_approval)
    provenance = validate_spectrum_provenance(pilot_config, manifest, approval)
    gap_report = run_stage2_dense_gap_consistency(pilot_config, provenance)
    if not gap_report.ok:
        raise ValueError("dense pilot blocked by Stage 2 gap inconsistency")
    solver_report = build_nonacceptance_solver_report(pilot_config, provenance)
    context = build_spectrum_context(pilot_config, provenance, solver_report)
    keys = decimal_flux_grid(
        pilot_config.flux_scan.start_phi0,
        pilot_config.flux_scan.stop_phi0,
        pilot_config.flux_scan.coarse_points,
        pilot_config.flux_scan.flux_key_decimal_places,
    )
    started = time.perf_counter()
    points: list[dict[str, Any]] = []
    for key in keys:
        model = rebuild_hamiltonian_for_spectrum(context, {"c": float(Decimal(key))})
        eigen = solve_static_eigensystem(model, context.solver_spec)
        catalog = build_bare_state_catalog(model, pilot_config.dressed_labeling)
        dressed = assign_dressed_states(eigen, catalog, pilot_config.dressed_labeling)
        participation = compute_mode_participation(eigen, catalog, dressed)
        assigned = dressed.by_label()
        bare = {mode: float(values[1] - values[0]) for mode, values in catalog.mode_eigenvalues_GHz.items()}
        points.append(
            {
                "flux_key": key,
                "flux_bias_phi0": float(Decimal(key)),
                "gaps_GHz": [float(value) for value in eigen.gaps_GHz],
                "assigned_energies_GHz": {
                    label: assigned[label].energy_GHz for label in ("100", "010", "001")
                },
                "assignment_overlaps": {label: assigned[label].overlap for label in ("100", "010", "001")},
                "participation": {
                    label: (participation.by_label()[label].fractions or {"q1": 0.0, "c": 0.0, "q2": 0.0})
                    for label in ("100", "010", "001")
                },
                "bare_transition_frequencies_GHz": bare,
                "bare_detunings_MHz": {
                    "q1-c": (bare["c"] - bare["q1"]) * 1000.0,
                    "c-q2": (bare["c"] - bare["q2"]) * 1000.0,
                },
                "solve_time_seconds": eigen.solve_time_seconds,
            }
        )
    candidates: list[dict[str, Any]] = []
    for name in ("q1-c", "c-q2"):
        labels = CANDIDATE_LABELS[name]
        gap_by_index = [
            abs(row["assigned_energies_GHz"][labels[0]] - row["assigned_energies_GHz"][labels[1]]) * 1000.0
            for row in points
        ]
        minimum_index = int(np.argmin(gap_by_index))
        if minimum_index == 0 or minimum_index == len(points) - 1:
            raise ValueError(f"dense pilot {name} minimum is at scan boundary")
        tolerance = pilot_config.convergence.frequency_tolerance_MHz
        left_indices = [
            index
            for index in range(minimum_index - 1, -1, -1)
            if points[index]["bare_detunings_MHz"][name] <= -tolerance
            or points[index]["bare_detunings_MHz"][name] >= tolerance
        ]
        right_indices = [
            index
            for index in range(minimum_index + 1, len(points))
            if points[index]["bare_detunings_MHz"][name] <= -tolerance
            or points[index]["bare_detunings_MHz"][name] >= tolerance
        ]
        left_index = left_indices[0] if left_indices else minimum_index - 1
        right_index = right_indices[0] if right_indices else minimum_index + 1
        candidates.append(
            {
                "name": name,
                "evidence_left_key": points[left_index]["flux_key"],
                "minimum_key": points[minimum_index]["flux_key"],
                "evidence_right_key": points[right_index]["flux_key"],
                "minimum_splitting_MHz": gap_by_index[minimum_index],
                "minimum_is_interior": True,
                "approximate_bracket_keys": [
                    points[minimum_index - 1]["flux_key"],
                    points[minimum_index + 1]["flux_key"],
                ],
            }
        )
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_dense_pilot",
        "artifact_version": "0.1",
        "profile": "dense_pilot",
        "acceptance_eligible": False,
        "num_states": 12,
        "solver_backend": "dense_eigh",
        "spectrum_config_path": Path(config_path).as_posix(),
        "spectrum_config_sha256": raw_file_sha256(config_path),
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "stage2_dense_gap_consistency": gap_report.to_dict(),
        "idle_flux_key": canonical_flux_text(
            next(row.flux_bias_phi0 for row in context.effective_junctions if row.mode == "c")
        ),
        "start_key": keys[0],
        "stop_key": keys[-1],
        "points": points,
        "candidates": candidates,
        "wall_time_seconds": time.perf_counter() - started,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(payload))
    return payload


def generate_solver_validation(
    config_path: str | Path,
    dense_pilot_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    config = load_spectrum_config(config_path)
    pilot = _load_mapping(dense_pilot_path)
    if pilot.get("acceptance_eligible") is not False or pilot.get("artifact_type") != "stage_03_dense_pilot":
        raise ValueError("solver validation requires a non-acceptance dense pilot")
    manifest = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
    approval = load_stage2_rebaseline_approval(config.source_rebaseline_approval)
    provenance = validate_spectrum_provenance(config, manifest, approval)
    solver_report = build_nonacceptance_solver_report(replace(config, acceptance_eligible=False), provenance)
    context = build_spectrum_context(replace(config, acceptance_eligible=False), provenance, solver_report)
    pilot_candidates = {row["name"]: row for row in pilot["candidates"]}
    point_keys = {
        pilot["idle_flux_key"],
        pilot["start_key"],
        pilot["stop_key"],
    }
    for name in ("q1-c", "c-q2"):
        candidate = pilot_candidates[name]
        point_keys.update(
            {
                candidate["evidence_left_key"],
                candidate["minimum_key"],
                candidate["evidence_right_key"],
            }
        )
    ordered_keys = sorted(point_keys, key=Decimal)
    base_cutoffs = dict(context.hamiltonian_config.basis.charge_cutoffs)
    cutoff_cases = [("baseline", base_cutoffs)]
    for mode in ("q1", "c", "q2"):
        refined = dict(base_cutoffs)
        refined[mode] += config.convergence.cutoff_increment
        cutoff_cases.append((f"refined_{mode}", refined))

    cases: list[dict[str, Any]] = []
    timings: dict[str, list[float]] = {"3375": [], "4275": []}
    for cutoff_name, cutoffs in cutoff_cases:
        for key in ordered_keys:
            basis_overrides = {
                mode: value for mode, value in cutoffs.items() if value != base_cutoffs[mode]
            }
            model = rebuild_hamiltonian_for_spectrum(
                context,
                {"c": float(Decimal(key))},
                basis_overrides=basis_overrides,
            )
            dense_values, dense_vectors, dense_seconds = dense_reference_eigensystem(model, config.eigen.num_states)
            spec = ValidatedSolverSpec(
                backend="validated_eigsh",
                num_states=config.eigen.num_states,
                which=config.eigen.eigsh.which,
                tolerance=config.eigen.eigsh.tolerance,
                maxiter=config.eigen.eigsh.maxiter,
                ncv=config.eigen.eigsh.ncv,
                v0_rule=config.eigen.eigsh.v0_rule,
                stage2_artifacts_sha256=provenance.stage2_artifacts_sha256,
                validation_artifact_sha256=None,
                acceptance_eligible=False,
            )
            run1 = solve_static_eigensystem(model, spec)
            run2 = solve_static_eigensystem(model, spec)
            dimension_key = str(model.matrix.shape[0])
            timings.setdefault(dimension_key, []).extend([run1.solve_time_seconds, run2.solve_time_seconds])
            blocks, boundary_gaps = dense_near_degenerate_blocks(
                dense_values,
                num_states=config.eigen.num_states,
                threshold_GHz=config.eigen.solver_validation.near_degenerate_gap_threshold_GHz,
                boundary_margin_GHz=config.eigen.solver_validation.partition_boundary_margin_GHz,
            )
            dense_gaps = dense_values[: config.eigen.num_states] - dense_values[0]
            gap_error = float(np.max(np.abs(run1.gaps_GHz - dense_gaps)))
            repeat_gap_error = float(np.max(np.abs(run1.gaps_GHz - run2.gaps_GHz)))
            dense_projectors: list[float] = []
            repeat_projectors: list[float] = []
            for left, right in blocks:
                dense_basis = dense_vectors[:, left : right + 1]
                dense_projectors.append(projector_spectral_error(dense_basis, run1.eigenvectors[:, left : right + 1]))
                repeat_projectors.append(
                    projector_spectral_error(
                        run1.eigenvectors[:, left : right + 1],
                        run2.eigenvectors[:, left : right + 1],
                    )
                )
            catalog = build_bare_state_catalog(model, config.dressed_labeling)
            dense_table = EigenstateTable(
                eigenvalues_GHz=dense_values[: config.eigen.num_states],
                eigenvectors=dense_vectors[:, : config.eigen.num_states],
                backend="dense_eigh",
                solve_time_seconds=dense_seconds,
                cutoffs=tuple(cutoffs[mode] for mode in ("q1", "c", "q2")),
                flux_key=key,
            )
            comparisons = _compare_physics(dense_table, run1, run2, catalog, config)
            case_passed = (
                gap_error <= config.eigen.solver_validation.gap_dense_tolerance_GHz
                and repeat_gap_error <= 1e-10
                and max(dense_projectors, default=0.0)
                <= config.eigen.solver_validation.projector_dense_tolerance
                and max(repeat_projectors, default=0.0)
                <= config.eigen.solver_validation.projector_repeat_tolerance
                and comparisons["passed"]
            )
            cases.append(
                {
                    "case_id": f"{cutoff_name}@{key}",
                    "cutoff_signature": [cutoffs[mode] for mode in ("q1", "c", "q2")],
                    "dimension": model.matrix.shape[0],
                    "flux_key": key,
                    "dense_seconds": dense_seconds,
                    "eigsh_run_seconds": [run1.solve_time_seconds, run2.solve_time_seconds],
                    "dense_gaps_GHz": dense_gaps.tolist(),
                    "eigsh_gaps_GHz": run1.gaps_GHz.tolist(),
                    "gap_dense_max_error_GHz": gap_error,
                    "gap_repeat_max_error_GHz": repeat_gap_error,
                    "dense_block_index_ranges": [list(block) for block in blocks],
                    "boundary_gaps_GHz": list(boundary_gaps),
                    "truncation_status": "not_truncated",
                    "projector_dense_errors_spectral_2": dense_projectors,
                    "projector_repeat_errors_spectral_2": repeat_projectors,
                    "physics_comparison": comparisons,
                    "passed": case_passed,
                }
            )
    seed, seed_hash, block0_hash = sha256_counter_seed(
        stage2_artifacts_sha256="222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C",
        cutoffs=(7, 7, 7),
        flux_phi0=0.2,
        num_states=48,
    )
    normative_passed = (
        len(seed) == 166
        and seed_hash == "ED780C6FA5C04949197BEE6D43156B10391B1E6E3596F9CFA41D80D455FBF1A7"
        and block0_hash == "2601A3794F3CD9A73B4B6B2D4DDDB15E3FC890D8593B2752672D366E83EA2950"
    )
    root = find_repository_root(config.source_path)
    environment, environment_hash = environment_fingerprint()
    p50 = {key: float(np.percentile(values, 50)) for key, values in timings.items() if values}
    p95 = {key: float(np.percentile(values, 95)) for key, values in timings.items() if values}
    expected_case_ids = {
        f"{cutoff_name}@{key}"
        for cutoff_name, _ in cutoff_cases
        for key in ordered_keys
    }
    actual_case_ids = [case["case_id"] for case in cases]
    coverage_complete = (
        len(actual_case_ids) == len(expected_case_ids)
        and len(set(actual_case_ids)) == len(actual_case_ids)
        and set(actual_case_ids) == expected_case_ids
    )
    failed_cases = [case["case_id"] for case in cases if case.get("passed") is not True]
    aggregates = {
        "max_gap_error_GHz": max(case["gap_dense_max_error_GHz"] for case in cases),
        "max_repeat_gap_error_GHz": max(case["gap_repeat_max_error_GHz"] for case in cases),
        "max_projector_dense_error": max(
            max(case["projector_dense_errors_spectral_2"], default=0.0) for case in cases
        ),
        "max_projector_repeat_error": max(
            max(case["projector_repeat_errors_spectral_2"], default=0.0) for case in cases
        ),
        "max_participation_dense_error": max(
            case["physics_comparison"]["participation_dense_max_error"] for case in cases
        ),
        "max_participation_repeat_error": max(
            case["physics_comparison"]["participation_repeat_max_error"] for case in cases
        ),
        "max_metric_dense_error_GHz": max(
            case["physics_comparison"]["metric_dense_max_error_GHz"] for case in cases
        ),
    }
    if not all(np.isfinite(value) for value in aggregates.values()):
        raise ValueError("solver validation aggregates must be finite")
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation",
        "artifact_version": "0.1",
        "profile": "solver_validation",
        "acceptance_eligible": False,
        "spectrum_config_path": Path(config_path).as_posix(),
        "spectrum_config_sha256": raw_file_sha256(config_path),
        "stage2_rebaseline_manifest_sha256": provenance.rebaseline_manifest_sha256,
        "stage2_rebaseline_approval_sha256": provenance.rebaseline_approval_sha256,
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "hamiltonian_config_sha256": provenance.hamiltonian_config_sha256,
        "stage2_model_source_tree_sha256": provenance.stage2_model_source_tree_sha256,
        "stage3_solver_source_tree_sha256": stage3_solver_source_tree_sha256(root),
        "environment_fingerprint": environment,
        "environment_fingerprint_sha256": environment_hash,
        "eigsh_spec": {
            "which": config.eigen.eigsh.which,
            "tolerance": config.eigen.eigsh.tolerance,
            "maxiter": config.eigen.eigsh.maxiter,
            "ncv": config.eigen.eigsh.ncv,
            "v0_rule": config.eigen.eigsh.v0_rule,
            "num_states": config.eigen.num_states,
        },
        "dense_config": {"solver": "scipy.linalg.eigh", "num_states_plus_one": config.eigen.num_states + 1},
        "v0_canonical_encoding_version": "sha256_counter_v1",
        "normative_test_vector": {
            "seed_length": len(seed),
            "seed_sha256": seed_hash,
            "block0_sha256": block0_hash,
        },
        "normative_test_vector_passed": normative_passed,
        "near_degenerate_gap_threshold_GHz": config.eigen.solver_validation.near_degenerate_gap_threshold_GHz,
        "partition_boundary_margin_GHz": config.eigen.solver_validation.partition_boundary_margin_GHz,
        "projector_error_norm": "spectral_2",
        "projector_dense_tolerance": config.eigen.solver_validation.projector_dense_tolerance,
        "projector_repeat_tolerance": config.eigen.solver_validation.projector_repeat_tolerance,
        "dense_pilot_path": Path(dense_pilot_path).as_posix(),
        "dense_pilot_sha256": raw_file_sha256(dense_pilot_path),
        "validation_cases": cases,
        "coverage_complete": coverage_complete,
        "failed_cases": failed_cases,
        "p50_seconds_by_signature": p50,
        "p95_seconds_by_signature": p95,
        **aggregates,
        "validation_passed": normative_passed and coverage_complete and not failed_cases,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(payload))
    return payload


def _compare_physics(dense, eigsh1, eigsh2, catalog, config) -> dict[str, Any]:
    dense_dressed = assign_dressed_states(dense, catalog, config.dressed_labeling)
    run1_dressed = assign_dressed_states(eigsh1, catalog, config.dressed_labeling)
    run2_dressed = assign_dressed_states(eigsh2, catalog, config.dressed_labeling)
    dense_participation = compute_mode_participation(dense, catalog, dense_dressed)
    run1_participation = compute_mode_participation(eigsh1, catalog, run1_dressed)
    run2_participation = compute_mode_participation(eigsh2, catalog, run2_dressed)
    dense_metrics = compute_static_metrics(dense_dressed, dense, dense_participation)
    run1_metrics = compute_static_metrics(run1_dressed, eigsh1, run1_participation)
    assignments_dense = {row.label.key: row.eigen_index for row in dense_dressed.assignments}
    assignments_run1 = {row.label.key: row.eigen_index for row in run1_dressed.assignments}
    assignments_run2 = {row.label.key: row.eigen_index for row in run2_dressed.assignments}
    part1 = run1_participation.by_label()
    part2 = run2_participation.by_label()
    dense_part = dense_participation.by_label()
    participation_dense_error = max(
        abs(part1[label].mean_excitations[mode] - dense_part[label].mean_excitations[mode])
        for label in part1
        for mode in ("q1", "c", "q2")
    )
    participation_repeat_error = max(
        abs(part1[label].mean_excitations[mode] - part2[label].mean_excitations[mode])
        for label in part1
        for mode in ("q1", "c", "q2")
    )
    metric_dense_error = max(
        abs(run1_metrics.transition_frequencies_GHz[key] - dense_metrics.transition_frequencies_GHz[key])
        for key in dense_metrics.transition_frequencies_GHz
    )
    passed = (
        assignments_dense == assignments_run1 == assignments_run2
        and participation_dense_error <= 1e-6
        and participation_repeat_error <= 1e-8
        and metric_dense_error <= config.eigen.solver_validation.gap_dense_tolerance_GHz
    )
    return {
        "assignments_dense": assignments_dense,
        "assignments_eigsh_run1": assignments_run1,
        "assignments_eigsh_run2": assignments_run2,
        "participation_dense_max_error": participation_dense_error,
        "participation_repeat_max_error": participation_repeat_error,
        "metric_dense_max_error_GHz": metric_dense_error,
        "passed": passed,
    }


def _load_mapping(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("artifact root must be a mapping")
    return value
