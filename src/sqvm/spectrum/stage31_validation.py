"""Stage 3.1 non-acceptance pilot and 44-case solver candidate generation."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256
from sqvm.spectrum.analysis import assign_dressed_states, build_bare_state_catalog, compute_mode_participation, compute_static_metrics
from sqvm.spectrum.context import build_spectrum_context, rebuild_hamiltonian_for_spectrum
from sqvm.spectrum.models import EigenstateTable, SolverBackendReport, ValidatedSolverSpec
from sqvm.spectrum.provenance import load_stage2_rebaseline_approval, load_stage2_rebaseline_manifest, run_stage2_dense_gap_consistency, validate_spectrum_provenance
from sqvm.spectrum.solver import (
    build_nonacceptance_solver_report, dense_near_degenerate_blocks, dense_reference_eigensystem,
    environment_fingerprint, projector_spectral_error, sha256_counter_seed_v2,
    solve_static_eigensystem, stage3_solver_source_tree_sha256,
)
from sqvm.spectrum.stage31 import full_flux_keys
from sqvm.spectrum.stage31_config import load_q1_q2_coupling_config, validate_stage31_design_freeze


CUTOFF_SIGNATURES = (
    ("baseline", (7, 7, 7), 3375),
    ("refined_q1", (9, 7, 7), 4275),
    ("refined_c", (7, 9, 7), 4275),
    ("refined_q2", (7, 7, 9), 4275),
)
FLUX_VECTORS = (
    ("idle", 0.10, 0.270, 0.00),
    ("c020_left", 0.10, 0.200, 0.08),
    ("c020_mid", 0.10, 0.200, 0.10),
    ("c020_right", 0.10, 0.200, 0.12),
    ("c027_left", 0.10, 0.270, 0.08),
    ("c027_mid", 0.10, 0.270, 0.10),
    ("c027_right", 0.10, 0.270, 0.12),
    ("c0385_left", 0.10, 0.385, 0.08),
    ("c0385_mid", 0.10, 0.385, 0.10),
    ("c0385_right", 0.10, 0.385, 0.12),
    ("c0396_mid", 0.10, 0.396, 0.10),
)
REMEDIATION_PATH = Path("docs/decisions/2026-07-11-stage3-1-c2-remediation.md")
FIXED_REFINEMENT_REMEDIATION_PATH = Path(
    "docs/decisions/2026-07-11-stage3-1-c2-2-fixed-refinement-remediation.md"
)
DENSE_PILOT_PATH = Path("output/stage_03_1_solver_validation/dense_pilot.json")
STAGE31_BINDING_KEYS = (
    "config_sha256",
    "dense_pilot_sha256",
    "design_freeze_manifest_sha256",
    "environment_fingerprint_sha256",
    "hamiltonian_config_sha256",
    "stage2_artifacts_sha256",
    "stage2_model_source_tree_sha256",
    "stage2_rebaseline_approval_sha256",
    "stage2_rebaseline_manifest_sha256",
    "stage3_1_c2_remediation_sha256",
    "stage3_1_c2_2_fixed_refinement_remediation_sha256",
    "stage3_1_source_tree_sha256",
)


def run_stage3_1_dense_pilot(config_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    config, provenance, context = _nonacceptance_context(config_path, num_states=12)
    rows = []
    started = time.perf_counter()
    for vector_id, q1, c, q2 in FLUX_VECTORS[1:]:
        model = rebuild_hamiltonian_for_spectrum(context, {"q1": q1, "c": c, "q2": q2})
        eigen = solve_static_eigensystem(model, context.solver_spec)
        catalog = build_bare_state_catalog(model, config.dressed_labeling)
        dressed = assign_dressed_states(eigen, catalog, config.dressed_labeling)
        assigned = dressed.by_label()
        splitting = abs(assigned["100"].gap_from_ground_GHz - assigned["001"].gap_from_ground_GHz) * 1000.0
        bare = {mode: float(values[1] - values[0]) for mode, values in catalog.mode_eigenvalues_GHz.items()}
        rows.append({
            "flux_vector_id": vector_id, "flux_keys": full_flux_keys(q1, c, q2),
            "q1_q2_splitting_MHz": splitting, "bare_q1_q2_detuning_MHz": (bare["q1"] - bare["q2"]) * 1000.0,
            "assignment_overlaps": {label: assigned[label].overlap for label in ("100", "010", "001")},
            "solve_time_seconds": eigen.solve_time_seconds,
        })
    freeze = validate_stage31_design_freeze(config)
    payload = {
        "schema_version": "0.1", "artifact_type": "stage_03_1_dense_pilot", "artifact_version": "0.1",
        "profile": "dense_pilot", "acceptance_eligible": False,
        "config_sha256": raw_file_sha256(config.source_path), "design_freeze_manifest_sha256": freeze["sha256"],
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "num_states": 12, "solver_backend": "dense_eigh", "points": rows,
        "wall_time_seconds": time.perf_counter() - started,
    }
    _write_candidate(output_path, payload)
    return payload


def generate_stage3_1_solver_validation(config_path: str | Path, dense_pilot_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    config, provenance, context = _nonacceptance_context(config_path, num_states=48)
    pilot = _load_canonical(dense_pilot_path)
    if pilot.get("artifact_type") != "stage_03_1_dense_pilot" or pilot.get("acceptance_eligible") is not False:
        raise ValueError("Stage 3.1 validation requires the non-acceptance dense pilot")
    root = find_repository_root(config.source_path)
    if Path(dense_pilot_path).resolve() != (root / DENSE_PILOT_PATH).resolve():
        raise ValueError("Stage 3.1 dense pilot path does not match fixed contract")
    freeze = validate_stage31_design_freeze(config)
    environment, environment_hash = environment_fingerprint()
    timings: dict[str, list[float]] = {"3375": [], "4275": []}
    cases = []
    for signature, cutoffs, expected_dimension in CUTOFF_SIGNATURES:
        overrides = {mode: value for mode, value in zip(("q1", "c", "q2"), cutoffs) if value != 7}
        for vector_id, q1, c, q2 in FLUX_VECTORS:
            model = rebuild_hamiltonian_for_spectrum(context, {"q1": q1, "c": c, "q2": q2}, overrides)
            if model.matrix.shape[0] != expected_dimension:
                raise ValueError(f"{signature} dimension does not match frozen contract")
            dense_values, dense_vectors, dense_seconds = dense_reference_eigensystem(model, 48)
            spec = ValidatedSolverSpec(
                "validated_eigsh", 48, config.eigen.eigsh.which, config.eigen.eigsh.tolerance,
                config.eigen.eigsh.maxiter, config.eigen.eigsh.ncv, "sha256_counter_v2",
                provenance.stage2_artifacts_sha256, None, False,
            )
            run1 = solve_static_eigensystem(model, spec)
            run2 = solve_static_eigensystem(model, spec)
            timings[str(expected_dimension)].extend((run1.solve_time_seconds, run2.solve_time_seconds))
            blocks, _ = dense_near_degenerate_blocks(
                dense_values, num_states=48,
                threshold_GHz=config.eigen.solver_validation.near_degenerate_gap_threshold_GHz,
                boundary_margin_GHz=config.eigen.solver_validation.partition_boundary_margin_GHz,
            )
            dense_gaps = dense_values[:48] - dense_values[0]
            max_gap = float(np.max(np.abs(dense_gaps - run1.gaps_GHz)))
            repeat_gap = float(np.max(np.abs(run1.gaps_GHz - run2.gaps_GHz)))
            projector_dense = [projector_spectral_error(dense_vectors[:, left:right + 1], run1.eigenvectors[:, left:right + 1]) for left, right in blocks]
            projector_repeat = [projector_spectral_error(run1.eigenvectors[:, left:right + 1], run2.eigenvectors[:, left:right + 1]) for left, right in blocks]
            dense_table = EigenstateTable(
                dense_values[:48], dense_vectors[:, :48], "dense_eigh", dense_seconds, cutoffs,
                full_flux_keys(q1, c, q2)["c"],
            )
            physics = _physics_comparison(dense_table, run1, run2, model, config)
            splitting_error = abs(physics["dense_q1_q2_splitting_GHz"] - physics["eigsh_q1_q2_splitting_GHz"])
            passed = (
                max_gap <= config.eigen.solver_validation.gap_dense_tolerance_GHz
                and repeat_gap <= 1e-10
                and max(projector_dense, default=0.0) <= config.eigen.solver_validation.projector_dense_tolerance
                and max(projector_repeat, default=0.0) <= config.eigen.solver_validation.projector_repeat_tolerance
                and splitting_error <= config.eigen.solver_validation.q1_q2_splitting_dense_tolerance_GHz
                and physics["physics_checks"]["passed"]
            )
            cases.append({
                "case_id": f"{signature}__{vector_id}", "cutoff_signature": signature,
                "cutoffs": list(cutoffs), "dimension": expected_dimension,
                "flux_keys": full_flux_keys(q1, c, q2), "passed": bool(passed),
                "dense_gaps_GHz": dense_gaps.tolist(), "eigsh_gaps_GHz": run1.gaps_GHz.tolist(),
                "max_gap_error_GHz": max_gap, "repeat_gap_error_GHz": repeat_gap,
                "near_degenerate_blocks": [list(block) for block in blocks],
                "projector_dense_errors": projector_dense, "projector_repeat_errors": projector_repeat,
                "dense_q1_q2_splitting_GHz": physics["dense_q1_q2_splitting_GHz"],
                "eigsh_q1_q2_splitting_GHz": physics["eigsh_q1_q2_splitting_GHz"],
                "q1_q2_splitting_error_GHz": splitting_error,
                "assignments_match": physics["assignments_match"],
                "max_participation_dense_error": physics["max_participation_dense_error"],
                "max_participation_repeat_error": physics["max_participation_repeat_error"],
                "max_metric_dense_error_GHz": physics["max_metric_dense_error_GHz"],
                "physics_checks": physics["physics_checks"],
            })
    failed = [row["case_id"] for row in cases if row["passed"] is not True]
    aggregates = _aggregates(cases)
    seed, seed_hash, block0 = sha256_counter_seed_v2(
        stage2_artifacts_sha256=provenance.stage2_artifacts_sha256, cutoffs=(7, 7, 7),
        fluxes_phi0=(0.1, 0.27, 0.1), num_states=48,
    )
    normative = {
        "encoding_version": "sha256_counter_v2", "seed_length": len(seed),
        "seed_sha256": seed_hash, "block0_sha256": block0,
        "passed": len(seed) == 224
        and seed_hash == "48245D6E13E15E65DA68E7AD4E957E07F97F0C1DF6E182E1806D6B1F88027CA0"
        and block0 == "44C000CA15E6923769C51CCC19623DAB311213490C1E50225D7E0AC79A33E322",
    }
    p50 = {key: float(np.percentile(values, 50)) for key, values in timings.items()}
    p95 = {key: float(np.percentile(values, 95)) for key, values in timings.items()}
    payload = {
        "schema_version": "0.1", "artifact_type": "stage_03_1_solver_backend_validation",
        "artifact_version": "0.1", "profile": "solver_validation", "acceptance_eligible": False,
        "validation_passed": not failed and normative["passed"], "coverage_complete": len(cases) == 44,
        "failed_cases": failed,
        "bindings": _expected_stage31_bindings(
            config, provenance, root, stage3_solver_source_tree_sha256(root), environment_hash
        ),
        "eigsh_spec": {"which": config.eigen.eigsh.which, "tolerance": config.eigen.eigsh.tolerance, "maxiter": config.eigen.eigsh.maxiter, "ncv": config.eigen.eigsh.ncv, "v0_rule": "sha256_counter_v2", "num_states": 48},
        "dense_spec": {"solver": "scipy.linalg.eigh", "num_states_plus_one": 49},
        "thresholds": {
            "gap_dense_tolerance_GHz": config.eigen.solver_validation.gap_dense_tolerance_GHz,
            "q1_q2_splitting_dense_tolerance_GHz": config.eigen.solver_validation.q1_q2_splitting_dense_tolerance_GHz,
            "near_degenerate_gap_threshold_GHz": config.eigen.solver_validation.near_degenerate_gap_threshold_GHz,
            "partition_boundary_margin_GHz": config.eigen.solver_validation.partition_boundary_margin_GHz,
            "projector_error_norm": config.eigen.solver_validation.projector_error_norm,
            "projector_dense_tolerance": config.eigen.solver_validation.projector_dense_tolerance,
            "projector_repeat_tolerance": config.eigen.solver_validation.projector_repeat_tolerance,
        },
        "normative_vector": normative,
        "cutoff_signatures": [{"id": name, "cutoffs": list(cutoffs), "dimension": dimension} for name, cutoffs, dimension in CUTOFF_SIGNATURES],
        "flux_vectors": [{"id": name, "flux_keys": full_flux_keys(q1, c, q2)} for name, q1, c, q2 in FLUX_VECTORS],
        "validation_cases": cases, "aggregates": aggregates,
        "p50_seconds_by_dimension": p50, "p95_seconds_by_dimension": p95,
    }
    _write_candidate(output_path, payload)
    return payload


def validate_stage3_1_solver_backend(config, provenance, validation_path=None, approval_path=None) -> SolverBackendReport:
    """Revalidate the complete candidate and its independent approval from current bytes."""
    root = find_repository_root(config.source_path)
    source_hash = stage3_solver_source_tree_sha256(root)
    _, environment_hash = environment_fingerprint()
    validation_source = Path(validation_path or config.runtime.solver_validation_artifact)
    approval_source = Path(approval_path or config.runtime.solver_validation_approval)
    errors: list[str] = []
    candidate = _safe_load_canonical(validation_source, "solver validation", errors)
    approval = _safe_load_canonical(approval_source, "solver validation approval", errors)
    if validation_source.resolve() != Path(config.runtime.solver_validation_artifact).resolve():
        errors.append("solver validation path does not match config")
    if approval_source.resolve() != Path(config.runtime.solver_validation_approval).resolve():
        errors.append("solver validation approval path does not match config")
    approval_keys = {
        "schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role",
        "blocking_findings", "validation_artifact_sha256", "review_record_path", "review_record_sha256",
    }
    if set(approval) != approval_keys:
        errors.append("solver approval keys are not exact")
    for key, value in {
        "schema_version": "0.1", "artifact_type": "stage_03_1_solver_backend_validation_approval",
        "artifact_version": "0.1", "decision": "approved", "reviewer_role": "independent_test_review_ai",
        "blocking_findings": [],
    }.items():
        if approval.get(key) != value:
            errors.append(f"solver approval {key} mismatch")
    validation_hash = _hash_or_none(validation_source, errors)
    approval_hash = _hash_or_none(approval_source, errors)
    if approval.get("validation_artifact_sha256") != validation_hash:
        errors.append("solver approval candidate hash mismatch")
    review_path = approval.get("review_record_path")
    if not isinstance(review_path, str) or not review_path:
        errors.append("solver approval review_record_path is invalid")
    elif approval.get("review_record_sha256") != _hash_or_none(root / review_path, errors):
        errors.append("solver approval review record hash mismatch")

    candidate_keys = {
        "schema_version", "artifact_type", "artifact_version", "profile", "acceptance_eligible",
        "validation_passed", "coverage_complete", "failed_cases", "bindings", "eigsh_spec",
        "dense_spec", "thresholds", "normative_vector", "cutoff_signatures", "flux_vectors",
        "validation_cases", "aggregates", "p50_seconds_by_dimension", "p95_seconds_by_dimension",
    }
    if set(candidate) != candidate_keys:
        errors.append("solver validation top-level keys are not exact")
    for key, value in {
        "schema_version": "0.1", "artifact_type": "stage_03_1_solver_backend_validation",
        "artifact_version": "0.1", "profile": "solver_validation", "acceptance_eligible": False,
        "validation_passed": True, "coverage_complete": True, "failed_cases": [],
    }.items():
        if candidate.get(key) != value:
            errors.append(f"solver validation {key} mismatch")
    freeze = validate_stage31_design_freeze(config)
    expected_bindings = _expected_stage31_bindings(
        config, provenance, root, source_hash, environment_hash
    )
    bindings = candidate.get("bindings")
    if not isinstance(bindings, dict):
        errors.append("solver validation bindings must be a mapping")
        bindings = {}
    _validate_stage31_bindings(bindings, expected_bindings, errors)
    expected_eigsh = {
        "which": config.eigen.eigsh.which, "tolerance": config.eigen.eigsh.tolerance,
        "maxiter": config.eigen.eigsh.maxiter, "ncv": config.eigen.eigsh.ncv,
        "v0_rule": "sha256_counter_v2", "num_states": 48,
    }
    if candidate.get("eigsh_spec") != expected_eigsh:
        errors.append("solver validation eigsh spec mismatch")
    expected_thresholds = {
        "gap_dense_tolerance_GHz": config.eigen.solver_validation.gap_dense_tolerance_GHz,
        "q1_q2_splitting_dense_tolerance_GHz": config.eigen.solver_validation.q1_q2_splitting_dense_tolerance_GHz,
        "near_degenerate_gap_threshold_GHz": config.eigen.solver_validation.near_degenerate_gap_threshold_GHz,
        "partition_boundary_margin_GHz": config.eigen.solver_validation.partition_boundary_margin_GHz,
        "projector_error_norm": config.eigen.solver_validation.projector_error_norm,
        "projector_dense_tolerance": config.eigen.solver_validation.projector_dense_tolerance,
        "projector_repeat_tolerance": config.eigen.solver_validation.projector_repeat_tolerance,
    }
    if candidate.get("thresholds") != expected_thresholds:
        errors.append("solver validation thresholds mismatch")
    expected_signatures = [{"id": name, "cutoffs": list(cutoffs), "dimension": dimension} for name, cutoffs, dimension in CUTOFF_SIGNATURES]
    expected_vectors = [{"id": name, "flux_keys": full_flux_keys(q1, c, q2)} for name, q1, c, q2 in FLUX_VECTORS]
    if candidate.get("cutoff_signatures") != expected_signatures:
        errors.append("solver validation cutoff signatures mismatch")
    if candidate.get("flux_vectors") != expected_vectors:
        errors.append("solver validation flux vectors mismatch")
    normative = candidate.get("normative_vector")
    if normative != {
        "encoding_version": "sha256_counter_v2", "seed_length": 224,
        "seed_sha256": "48245D6E13E15E65DA68E7AD4E957E07F97F0C1DF6E182E1806D6B1F88027CA0",
        "block0_sha256": "44C000CA15E6923769C51CCC19623DAB311213490C1E50225D7E0AC79A33E322",
        "passed": True,
    }:
        errors.append("solver validation normative vector mismatch")
    cases = candidate.get("validation_cases")
    expected_ids = [f"{signature}__{vector_id}" for signature, _, _ in CUTOFF_SIGNATURES for vector_id, *_ in FLUX_VECTORS]
    if not isinstance(cases, list) or [row.get("case_id") if isinstance(row, dict) else None for row in cases] != expected_ids:
        errors.append("solver validation case coverage/order mismatch")
        cases = []
    recomputed_failed = []
    for index, row in enumerate(cases):
        if not _validate_case(row, CUTOFF_SIGNATURES[index // 11], FLUX_VECTORS[index % 11], config, errors):
            recomputed_failed.append(row.get("case_id", f"case-{index}"))
    if candidate.get("failed_cases") != recomputed_failed:
        errors.append("solver validation failed_cases mismatch")
    if cases:
        expected_aggregates = _aggregates(cases)
        if candidate.get("aggregates") != expected_aggregates:
            errors.append("solver validation aggregates mismatch")
    else:
        expected_aggregates = {}
    for name in ("p50_seconds_by_dimension", "p95_seconds_by_dimension"):
        values = candidate.get(name)
        if not isinstance(values, dict) or set(values) != {"3375", "4275"} or not all(_finite_positive(value) for value in values.values()):
            errors.append(f"solver validation {name} invalid")
    p50, p95 = candidate.get("p50_seconds_by_dimension", {}), candidate.get("p95_seconds_by_dimension", {})
    if all(key in p50 and key in p95 for key in ("3375", "4275")) and any(p95[key] < p50[key] for key in p50):
        errors.append("solver validation p95 is below p50")
    solver_error = expected_aggregates.get("max_q1_q2_splitting_error_GHz")
    spec = None
    if not errors and _finite_nonnegative(solver_error):
        spec = ValidatedSolverSpec(
            "validated_eigsh", 48, config.eigen.eigsh.which, config.eigen.eigsh.tolerance,
            config.eigen.eigsh.maxiter, config.eigen.eigsh.ncv, "sha256_counter_v2",
            provenance.stage2_artifacts_sha256, validation_hash, True,
        )
    return SolverBackendReport(
        not errors, spec, validation_hash, approval_hash, source_hash, environment_hash,
        {key: float(value) for key, value in p95.items()} if isinstance(p95, dict) and all(_finite_positive(value) for value in p95.values()) else {},
        float(solver_error) if _finite_nonnegative(solver_error) else 0.0, tuple(errors),
    )


def _nonacceptance_context(config_path, num_states):
    config = load_q1_q2_coupling_config(config_path)
    manifest = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
    approval = load_stage2_rebaseline_approval(config.source_rebaseline_approval)
    provenance = validate_spectrum_provenance(config, manifest, approval)
    gap = run_stage2_dense_gap_consistency(config, provenance)
    if not gap.ok:
        raise ValueError("Stage 2 dense gap consistency failed")
    provenance = replace(provenance, stage2_dense_gap_consistency=gap)
    eigsh = replace(config.eigen.eigsh, ncv=25 if num_states == 12 else config.eigen.eigsh.ncv)
    run_config = replace(config, profile="smoke", acceptance_eligible=False, eigen=replace(config.eigen, num_states=num_states, solver="dense_eigh", eigsh=eigsh))
    solver = build_nonacceptance_solver_report(run_config, provenance)
    return config, provenance, build_spectrum_context(run_config, provenance, solver)


def _physics_comparison(dense, run1, run2, model, config):
    catalog = build_bare_state_catalog(model, config.dressed_labeling)
    dressed = [assign_dressed_states(item, catalog, config.dressed_labeling) for item in (dense, run1, run2)]
    participation = [compute_mode_participation(item, catalog, labels) for item, labels in zip((dense, run1, run2), dressed)]
    metrics = [compute_static_metrics(labels, item, part) for item, labels, part in zip((dense, run1), dressed[:2], participation[:2])]
    assignments = [{row.label.key: row.eigen_index for row in labels.assignments} for labels in dressed]
    part = [table.by_label() for table in participation]
    dense_error = max(abs(part[1][label].mean_excitations[mode] - part[0][label].mean_excitations[mode]) for label in part[0] for mode in ("q1", "c", "q2"))
    repeat_error = max(abs(part[1][label].mean_excitations[mode] - part[2][label].mean_excitations[mode]) for label in part[0] for mode in ("q1", "c", "q2"))
    metric_error = max(
        abs(right[key] - left[key]) for left, right in (
            (metrics[0].transition_frequencies_GHz, metrics[1].transition_frequencies_GHz),
            (metrics[0].anharmonicities_GHz, metrics[1].anharmonicities_GHz),
            (metrics[0].zz_metrics_GHz, metrics[1].zz_metrics_GHz),
        ) for key in left
    )
    def splitting(labels):
        assigned = labels.by_label()
        return abs(assigned["100"].gap_from_ground_GHz - assigned["001"].gap_from_ground_GHz)
    assignment_match = assignments[0] == assignments[1] == assignments[2]
    checks = {
        "assignments_match": assignment_match, "participation_dense_passed": dense_error <= 1e-6,
        "participation_repeat_passed": repeat_error <= 1e-8,
        "metric_dense_passed": metric_error <= config.eigen.solver_validation.gap_dense_tolerance_GHz,
    }
    checks["passed"] = all(checks.values())
    return {
        "dense_q1_q2_splitting_GHz": splitting(dressed[0]), "eigsh_q1_q2_splitting_GHz": splitting(dressed[1]),
        "assignments_match": assignment_match, "max_participation_dense_error": dense_error,
        "max_participation_repeat_error": repeat_error, "max_metric_dense_error_GHz": metric_error,
        "physics_checks": checks,
    }


def _aggregates(cases):
    mapping = {
        "max_gap_error_GHz": "max_gap_error_GHz", "max_repeat_gap_error_GHz": "repeat_gap_error_GHz",
        "max_projector_dense_error": "projector_dense_errors", "max_projector_repeat_error": "projector_repeat_errors",
        "max_participation_dense_error": "max_participation_dense_error",
        "max_participation_repeat_error": "max_participation_repeat_error",
        "max_metric_dense_error_GHz": "max_metric_dense_error_GHz",
        "max_q1_q2_splitting_error_GHz": "q1_q2_splitting_error_GHz",
    }
    result = {}
    for output, source in mapping.items():
        values = [max(row[source], default=0.0) if isinstance(row[source], list) else row[source] for row in cases]
        result[output] = max(values)
    if not all(np.isfinite(value) for value in result.values()):
        raise ValueError("Stage 3.1 validation aggregates must be finite")
    return result


def _write_candidate(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical_json_bytes(payload))


def _expected_stage31_bindings(config, provenance, root, source_hash, environment_hash):
    result = {
        "config_sha256": raw_file_sha256(config.source_path),
        "dense_pilot_sha256": raw_file_sha256(root / DENSE_PILOT_PATH),
        "design_freeze_manifest_sha256": raw_file_sha256(config.source_design_freeze_manifest),
        "environment_fingerprint_sha256": environment_hash,
        "hamiltonian_config_sha256": provenance.hamiltonian_config_sha256,
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "stage2_model_source_tree_sha256": provenance.stage2_model_source_tree_sha256,
        "stage2_rebaseline_approval_sha256": provenance.rebaseline_approval_sha256,
        "stage2_rebaseline_manifest_sha256": provenance.rebaseline_manifest_sha256,
        "stage3_1_c2_remediation_sha256": raw_file_sha256(root / REMEDIATION_PATH),
        "stage3_1_c2_2_fixed_refinement_remediation_sha256": raw_file_sha256(
            root / FIXED_REFINEMENT_REMEDIATION_PATH
        ),
        "stage3_1_source_tree_sha256": source_hash,
    }
    if tuple(sorted(result)) != tuple(sorted(STAGE31_BINDING_KEYS)):
        raise ValueError("Stage 3.1 binding implementation keys mismatch")
    return result


def _validate_stage31_bindings(bindings, expected, errors):
    if set(bindings) != set(expected):
        missing = sorted(set(expected) - set(bindings))
        extra = sorted(set(bindings) - set(expected))
        if missing:
            errors.append(f"solver validation bindings missing keys: {missing}")
        if extra:
            errors.append(f"solver validation bindings extra keys: {extra}")
    for key in sorted(expected):
        actual = bindings.get(key)
        if not _is_sha(actual):
            errors.append(f"solver validation binding {key} malformed")
        elif actual != expected[key]:
            errors.append(f"solver validation binding {key} mismatch")


def _load_canonical(path):
    source = Path(path)
    raw = source.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError("pilot must be a canonical JSON mapping")
    return payload


def _safe_load_canonical(path: Path, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("root must be a mapping")
        if raw != canonical_json_bytes(payload):
            errors.append(f"{label} bytes are not canonical")
        return payload
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"cannot load {label}: {exc}")
        return {}


def _hash_or_none(path: Path, errors: list[str]) -> str | None:
    try:
        return raw_file_sha256(path)
    except OSError as exc:
        errors.append(f"cannot hash {path.as_posix()}: {exc}")
        return None


def _validate_case(row, signature, vector, config, errors):
    signature_id, cutoffs, dimension = signature
    vector_id, q1, c, q2 = vector
    case_id = f"{signature_id}__{vector_id}"
    required = {
        "case_id", "cutoff_signature", "cutoffs", "dimension", "flux_keys", "passed",
        "dense_gaps_GHz", "eigsh_gaps_GHz", "max_gap_error_GHz", "repeat_gap_error_GHz",
        "near_degenerate_blocks", "projector_dense_errors", "projector_repeat_errors",
        "dense_q1_q2_splitting_GHz", "eigsh_q1_q2_splitting_GHz", "q1_q2_splitting_error_GHz",
        "assignments_match", "max_participation_dense_error", "max_participation_repeat_error",
        "max_metric_dense_error_GHz", "physics_checks",
    }
    ok = True
    if set(row) != required:
        errors.append(f"{case_id} keys are not exact")
        ok = False
    if row.get("case_id") != case_id or row.get("cutoff_signature") != signature_id:
        errors.append(f"{case_id} identity mismatch")
        ok = False
    if row.get("cutoffs") != list(cutoffs) or row.get("dimension") != dimension or row.get("flux_keys") != full_flux_keys(q1, c, q2):
        errors.append(f"{case_id} cutoff/dimension/flux mismatch")
        ok = False
    dense, eigsh = row.get("dense_gaps_GHz"), row.get("eigsh_gaps_GHz")
    if not _finite_list(dense, 48) or not _finite_list(eigsh, 48):
        errors.append(f"{case_id} gap vectors invalid")
        return False
    gap_error = max(abs(left - right) for left, right in zip(dense, eigsh))
    if not _close(row.get("max_gap_error_GHz"), gap_error) or gap_error > config.eigen.solver_validation.gap_dense_tolerance_GHz:
        errors.append(f"{case_id} dense gap gate failed")
        ok = False
    if not _finite_nonnegative(row.get("repeat_gap_error_GHz")) or row["repeat_gap_error_GHz"] > 1e-10:
        errors.append(f"{case_id} repeat gap gate failed")
        ok = False
    blocks = row.get("near_degenerate_blocks")
    flattened = [index for block in blocks for index in range(block[0], block[1] + 1)] if isinstance(blocks, list) and all(isinstance(block, list) and len(block) == 2 for block in blocks) else []
    if flattened != list(range(48)):
        errors.append(f"{case_id} block partition invalid")
        ok = False
    dense_projector, repeat_projector = row.get("projector_dense_errors"), row.get("projector_repeat_errors")
    if not _finite_list(dense_projector, len(blocks) if isinstance(blocks, list) else -1) or max(dense_projector, default=0.0) > config.eigen.solver_validation.projector_dense_tolerance:
        errors.append(f"{case_id} dense projector gate failed")
        ok = False
    if not _finite_list(repeat_projector, len(blocks) if isinstance(blocks, list) else -1) or max(repeat_projector, default=0.0) > config.eigen.solver_validation.projector_repeat_tolerance:
        errors.append(f"{case_id} repeat projector gate failed")
        ok = False
    dense_split, eigsh_split = row.get("dense_q1_q2_splitting_GHz"), row.get("eigsh_q1_q2_splitting_GHz")
    split_error = abs(dense_split - eigsh_split) if _finite_nonnegative(dense_split) and _finite_nonnegative(eigsh_split) else None
    if split_error is None or not _close(row.get("q1_q2_splitting_error_GHz"), split_error) or split_error > config.eigen.solver_validation.q1_q2_splitting_dense_tolerance_GHz:
        errors.append(f"{case_id} q1-q2 splitting gate failed")
        ok = False
    physics = row.get("physics_checks")
    if row.get("assignments_match") is not True or not isinstance(physics, dict) or physics.get("passed") is not True:
        errors.append(f"{case_id} physics identity gate failed")
        ok = False
    for key, tolerance in (
        ("max_participation_dense_error", 1e-6),
        ("max_participation_repeat_error", 1e-8),
        ("max_metric_dense_error_GHz", config.eigen.solver_validation.gap_dense_tolerance_GHz),
    ):
        if not _finite_nonnegative(row.get(key)) or row[key] > tolerance:
            errors.append(f"{case_id} {key} gate failed")
            ok = False
    if row.get("passed") is not True:
        errors.append(f"{case_id} passed must be true")
        ok = False
    return ok


def _finite_list(value, length):
    return isinstance(value, list) and len(value) == length and all(_finite_nonnegative(item) or (isinstance(item, (int, float)) and not isinstance(item, bool) and np.isfinite(item)) for item in value)


def _finite_nonnegative(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and np.isfinite(value) and value >= 0


def _finite_positive(value):
    return _finite_nonnegative(value) and value > 0


def _close(left, right):
    return _finite_nonnegative(left) and abs(left - right) <= max(1e-15, abs(right) * 1e-12)


def _is_sha(value):
    if not isinstance(value, str) or len(value) != 64 or value != value.upper():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True
