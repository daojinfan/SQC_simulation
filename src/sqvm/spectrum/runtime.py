"""Adaptive remaining-work and Stage 3 gate evaluation."""

from __future__ import annotations

from sqvm.spectrum.models import (
    CrossingConvergenceReport,
    ExecutionPlan,
    FluxScanResult,
    ProvenanceReport,
    RuntimeReport,
    SolverBackendReport,
    SpectrumConfig,
    StageGateDecision,
    StaticMetricConvergenceReport,
)


CONSERVATIVE_ACCEPTANCE_SOLVE_CEILING = 509


def build_execution_plan(
    config: SpectrumConfig,
    flux_scan: FluxScanResult,
    solver_backend_report: SolverBackendReport,
    elapsed: float,
) -> ExecutionPlan:
    baseline_completed = flux_scan.solver_evaluations
    completed = {"3375": baseline_completed, "4275": 0}
    if config.acceptance_eligible:
        remaining = {
            "3375": max(0, 173 - baseline_completed),
            "4275": 336,
        }
        budget = config.runtime.acceptance_budget_seconds
    else:
        remaining = {"3375": 0, "4275": 0}
        budget = config.runtime.smoke_budget_seconds
    p95 = {
        "3375": float(solver_backend_report.p95_seconds_by_signature.get("3375", 0.0)),
        "4275": float(solver_backend_report.p95_seconds_by_signature.get("4275", 0.0)),
    }
    projected_remaining = sum(remaining[key] * p95[key] for key in remaining)
    projected_total = elapsed + projected_remaining
    return ExecutionPlan(
        elapsed_seconds=elapsed,
        completed_jobs_by_cutoff_signature=completed,
        remaining_job_upper_bound_by_signature=remaining,
        p95_seconds_by_signature=p95,
        projected_remaining_seconds=projected_remaining,
        projected_total_seconds=projected_total,
        budget_seconds=budget,
        conservative_solve_ceiling=CONSERVATIVE_ACCEPTANCE_SOLVE_CEILING,
        within_budget=projected_total <= budget,
    )


def evaluate_stage3_gate(
    config: SpectrumConfig,
    provenance: ProvenanceReport,
    metric_convergence: StaticMetricConvergenceReport,
    flux_scan: FluxScanResult,
    crossing_convergence: CrossingConvergenceReport,
    runtime: RuntimeReport,
) -> StageGateDecision:
    blockers: list[str] = []
    if not provenance.ok:
        blockers.append("provenance_failure")
    if provenance.stage2_dense_gap_consistency is None or not provenance.stage2_dense_gap_consistency.ok:
        blockers.append("stage2_dense_gap_consistency_failed")
    if not metric_convergence.passed:
        blockers.append("static_metrics_unconverged")
    statuses = {
        name: value.get("status", "unresolved")
        for name, value in crossing_convergence.candidates.items()
    }
    for name in ("q1-c", "c-q2"):
        if statuses.get(name) != "resolved":
            blockers.append(f"{name}:{statuses.get(name, 'missing')}")
    if runtime.budget_status != "within_budget":
        blockers.append("runtime_budget_exceeded")
    if not config.acceptance_eligible or not flux_scan.acceptance_eligible:
        blockers.append("profile_not_acceptance_eligible")
    if not blockers:
        status = "ready_for_stage4"
    elif any("runtime" in item for item in blockers):
        status = "runtime_budget_exceeded"
    elif any("provenance" in item or "dense_gap" in item for item in blockers):
        status = "provenance_failure"
    elif any("geometry_too_weak" in item for item in blockers):
        status = "geometry_too_weak"
    elif not metric_convergence.passed:
        status = "numerical_failure"
    else:
        status = "unresolved_crossing"
    return StageGateDecision(
        analysis_completed=True,
        status=status,
        stage4_ready=status == "ready_for_stage4",
        blocking_reasons=tuple(blockers),
        candidate_statuses=statuses,
    )
