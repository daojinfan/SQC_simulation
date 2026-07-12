"""Stage 3 static-spectrum orchestration with acceptance fail-closed."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from sqvm.spectrum.analysis import analyze_static_point
from sqvm.spectrum.artifacts import write_static_spectrum_artifacts
from sqvm.spectrum.config import load_spectrum_config
from sqvm.spectrum.context import build_spectrum_context
from sqvm.spectrum.convergence import check_crossing_convergence
from sqvm.spectrum.flux import scan_coupler_flux
from sqvm.spectrum.models import (
    CrossingConvergenceReport,
    RuntimeReport,
    StaticMetricConvergenceReport,
    StaticSpectrumResult,
    StaticSpectrumVerificationReport,
)
from sqvm.spectrum.provenance import (
    load_stage2_rebaseline_approval,
    load_stage2_rebaseline_manifest,
    run_stage2_dense_gap_consistency,
    validate_spectrum_provenance,
)
from sqvm.spectrum.runtime import build_execution_plan, evaluate_stage3_gate
from sqvm.spectrum.solver import (
    build_nonacceptance_solver_report,
    load_solver_backend_validation,
    load_solver_backend_validation_approval,
    validate_solver_backend,
)


def assemble_static_spectrum_result(
    config,
    provenance,
    baseline,
    metric_convergence,
    flux_scan,
    crossing_convergence,
    runtime,
    gate,
    solver_backend_report,
    *,
    warnings=(),
    checks=(),
) -> StaticSpectrumResult:
    return StaticSpectrumResult(
        config=config,
        provenance=provenance,
        solver_backend_report=solver_backend_report,
        baseline=baseline,
        metric_convergence=metric_convergence,
        flux_scan=flux_scan,
        crossing_convergence=crossing_convergence,
        runtime=runtime,
        stage_gate=gate,
        warnings=tuple(warnings),
        checks=tuple(checks),
    )


def verify_static_spectrum(config_path: str | Path, output_dir: str | Path) -> StaticSpectrumVerificationReport:
    started = time.perf_counter()
    config = load_spectrum_config(config_path)
    manifest = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
    approval = load_stage2_rebaseline_approval(config.source_rebaseline_approval)
    provenance = validate_spectrum_provenance(config, manifest, approval)
    gap_report = run_stage2_dense_gap_consistency(config, provenance)
    provenance = replace(provenance, stage2_dense_gap_consistency=gap_report, ok=provenance.ok and gap_report.ok)
    if not gap_report.ok:
        raise ValueError("stage2_gap_consistency_error")

    if config.acceptance_eligible:
        validation = load_solver_backend_validation(config.runtime.solver_validation_artifact)
        validation_approval = load_solver_backend_validation_approval(config.runtime.solver_validation_approval)
        solver_report = validate_solver_backend(validation, validation_approval, provenance, config)
    else:
        solver_report = build_nonacceptance_solver_report(config, provenance)
    context = build_spectrum_context(config, provenance, solver_report)
    baseline = analyze_static_point(context, config)

    if config.acceptance_eligible:
        from sqvm.spectrum.convergence import check_static_metric_convergence

        metric_convergence = check_static_metric_convergence(config, context, baseline)
        flux_scan = scan_coupler_flux(config, context)
        crossing = check_crossing_convergence(config, context, flux_scan)
    else:
        metric_convergence = StaticMetricConvergenceReport(
            baseline_cutoffs=dict(baseline.cutoffs),
            refinement_increment=config.convergence.cutoff_increment,
            tolerances_MHz={
                "frequency": config.convergence.frequency_tolerance_MHz,
                "anharmonicity": config.convergence.anharmonicity_tolerance_MHz,
                "zz": config.convergence.zz_absolute_tolerance_MHz,
            },
            per_mode_refinement_rows=(),
            max_frequency_drift_MHz=0.0,
            max_anharmonicity_drift_MHz=0.0,
            max_zz_drift_MHz=0.0,
            passed=False,
        )
        flux_scan = scan_coupler_flux(config, context)
        crossing = CrossingConvergenceReport(
            candidates={
                "q1-c": {"status": "not_run_smoke"},
                "c-q2": {"status": "not_run_smoke"},
            },
            passed=False,
        )
    elapsed = time.perf_counter() - started
    execution_plan = build_execution_plan(config, flux_scan, solver_report, elapsed)
    solve_times = [baseline.eigenstates.solve_time_seconds]
    runtime = RuntimeReport(
        profile=config.profile,
        acceptance_eligible=config.acceptance_eligible,
        budget_seconds=execution_plan.budget_seconds,
        projected_total_seconds=execution_plan.projected_total_seconds,
        wall_time_seconds=elapsed,
        solver_backend=context.solver_spec.backend,
        solver_validation_artifact=config.runtime.solver_validation_artifact.as_posix(),
        solver_validation_approval=config.runtime.solver_validation_approval.as_posix(),
        execution_plan=execution_plan,
        solver_evaluations=1 + flux_scan.solver_evaluations,
        cache_hits=flux_scan.cache_hits,
        solve_time_p50_seconds=float(np.percentile(solve_times, 50)),
        solve_time_p95_seconds=float(np.percentile(solve_times, 95)),
        budget_status="within_budget" if execution_plan.within_budget else "runtime_budget_exceeded",
    )
    gate = evaluate_stage3_gate(config, provenance, metric_convergence, flux_scan, crossing, runtime)
    checks = _build_checks(config, provenance, baseline, metric_convergence, flux_scan, crossing, runtime, gate)
    warnings = tuple(baseline.dressed_states.warnings) + tuple(baseline.metrics.warnings)
    result = assemble_static_spectrum_result(
        config,
        provenance,
        baseline,
        metric_convergence,
        flux_scan,
        crossing,
        runtime,
        gate,
        solver_report,
        warnings=warnings,
        checks=checks,
    )
    artifact_set = write_static_spectrum_artifacts(result, output_dir)
    return StaticSpectrumVerificationReport(
        ok=gate.stage4_ready,
        execution_succeeded=True,
        profile=config.profile,
        acceptance_eligible=config.acceptance_eligible,
        stage_gate=gate,
        checks=tuple(checks),
        artifacts={
            "static_spectrum_artifacts": str(artifact_set.static_spectrum_artifacts),
            "verification_notebook": str(artifact_set.verification_notebook),
        },
    )


def _build_checks(config, provenance, baseline, convergence, flux_scan, crossing, runtime, gate):
    eigenvalues = baseline.eigenstates.eigenvalues_GHz
    vectors = baseline.eigenstates.eigenvectors
    checks = [
        _check("stage2_rebaseline_approval_valid", provenance.ok),
        _check(
            "stage2_gap_consistency_error",
            provenance.stage2_dense_gap_consistency is not None and provenance.stage2_dense_gap_consistency.ok,
        ),
        _check("eigenvalues_finite", bool(np.all(np.isfinite(eigenvalues)))),
        _check("eigenvalues_sorted", bool(np.all(np.diff(eigenvalues) >= 0.0))),
        _check("eigenvectors_normalized", bool(np.allclose(np.linalg.norm(vectors, axis=0), 1.0, atol=1e-10))),
        _check("eigenvectors_orthogonal", bool(np.allclose(vectors.T @ vectors, np.eye(vectors.shape[1]), atol=1e-10))),
        _check("dressed_assignment_unique", len({row.eigen_index for row in baseline.dressed_states.assignments}) == len(baseline.dressed_states.assignments)),
        _check("dressed_ground_assigned", "000" in baseline.dressed_states.by_label()),
        _check("mode_participation_finite_nonnegative", all(value >= 0.0 and np.isfinite(value) for row in baseline.participation.rows for value in row.mean_excitations.values())),
        _check("transition_frequency_converged", convergence.passed),
        _check("avoided_crossing_payload_complete", bool(flux_scan.candidates) or not config.flux_scan.enabled),
        _check("runtime_profile_valid", runtime.budget_status == "within_budget"),
        _check("stage4_ready", gate.stage4_ready),
    ]
    return checks


def _check(name: str, passed: bool, severity: str = "error") -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "severity": severity}
