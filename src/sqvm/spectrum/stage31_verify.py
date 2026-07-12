"""Stage 3.1 q1-q2 coupling orchestration."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time

from sqvm.spectrum.analysis import analyze_static_point
from sqvm.spectrum.context import build_spectrum_context
from sqvm.spectrum.convergence import check_static_metric_convergence
from sqvm.spectrum.models import StaticMetricConvergenceReport
from sqvm.spectrum.provenance import load_stage2_rebaseline_approval, load_stage2_rebaseline_manifest, run_stage2_dense_gap_consistency, validate_spectrum_provenance
from sqvm.spectrum.solver import build_nonacceptance_solver_report
from sqvm.spectrum.stage31 import (
    INITIAL_REMAINING_BY_DIMENSION, build_stage31_runtime_report,
    check_q1_q2_crossing_convergence, evaluate_coupling_modulation, evaluate_stage3_1_gate,
    finalize_q1_q2_crossings, preflight_stage3_1_phase, scan_q1_q2_coupling_vs_coupler,
)
from sqvm.spectrum.stage31_artifacts import (
    assemble_stage3_1_verification_report, write_q1_q2_coupling_artifacts,
    publish_stage3_1_transaction, write_q1_q2_coupling_notebook, write_stage3_1_verification_report,
)
from sqvm.spectrum.stage31_config import load_q1_q2_coupling_config
from sqvm.spectrum.stage31_models import QubitCouplingSweepResult
from sqvm.spectrum.stage31_validation import validate_stage3_1_solver_backend


def verify_q1_q2_coupling(config_path: str | Path, output_dir: str | Path):
    started = time.perf_counter()
    config = load_q1_q2_coupling_config(config_path)
    manifest = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
    approval = load_stage2_rebaseline_approval(config.source_rebaseline_approval)
    provenance = validate_spectrum_provenance(config, manifest, approval)
    dense12 = run_stage2_dense_gap_consistency(config, provenance)
    if not dense12.ok:
        raise ValueError("stage2_dense_gap_consistency_failed")
    provenance = replace(provenance, stage2_dense_gap_consistency=dense12)
    if config.acceptance_eligible:
        solver = validate_stage3_1_solver_backend(config, provenance)
        if not solver.ok or solver.validated_spec is None:
            raise ValueError("stage3_1_solver_validation_failed: " + "; ".join(solver.errors))
    else:
        solver = build_nonacceptance_solver_report(config, provenance)
    context = build_spectrum_context(config, provenance, solver)

    budget = config.runtime.acceptance_budget_seconds if config.acceptance_eligible else config.runtime.smoke_budget_seconds
    p95 = {key: float(solver.p95_seconds_by_signature.get(key, 0.0)) for key in ("3375", "4275")}
    remaining = dict(INITIAL_REMAINING_BY_DIMENSION)
    phase_ledger = {}

    preflight_stage3_1_phase(started, phase_ledger, remaining, p95, budget, config.runtime.conservative_solve_ceiling)
    phase_started = time.perf_counter()
    idle = analyze_static_point(
        context, config,
        {"q1": config.scan.fixed_q1_flux_phi0, "c": config.scan.reference_coupler_flux_phi0, "q2": 0.0},
    )
    phase_ledger["idle_baseline"] = _phase({"3375": 1, "4275": 0}, {"3375": 0, "4275": 0}, phase_started)
    remaining["3375"] -= 1

    preflight_stage3_1_phase(started, phase_ledger, remaining, p95, budget, config.runtime.conservative_solve_ceiling)
    phase_started = time.perf_counter()
    if config.acceptance_eligible:
        idle_convergence = check_static_metric_convergence(config, context, idle)
        idle_refinement_evaluations = {"3375": 0, "4275": 3}
    else:
        idle_convergence = StaticMetricConvergenceReport(
            dict(idle.cutoffs), config.convergence.cutoff_increment,
            {"frequency": config.convergence.frequency_tolerance_MHz, "anharmonicity": config.convergence.anharmonicity_tolerance_MHz, "zz": config.convergence.zz_absolute_tolerance_MHz},
            (), 0.0, 0.0, 0.0, False,
        )
        idle_refinement_evaluations = {"3375": 0, "4275": 0}
    phase_ledger["idle_refinements"] = _phase(
        idle_refinement_evaluations, {"3375": 0, "4275": 0}, phase_started
    )
    remaining["4275"] -= 3

    preflight_stage3_1_phase(started, phase_ledger, remaining, p95, budget, config.runtime.conservative_solve_ceiling)
    scan = scan_q1_q2_coupling_vs_coupler(context, config)
    phase_ledger["outer_inner_scans"] = {
        "evaluations_by_dimension": dict(scan.evaluations_by_dimension),
        "cache_hits_by_dimension": dict(scan.cache_hits_by_dimension),
        "elapsed_seconds": scan.elapsed_seconds,
    }
    remaining["3375"] -= 873

    preflight_stage3_1_phase(started, phase_ledger, remaining, p95, budget, config.runtime.conservative_solve_ceiling)
    convergence = check_q1_q2_crossing_convergence(context, config, scan)
    phase_ledger["anchor_cutoff_convergence"] = {
        "evaluations_by_dimension": dict(convergence.evaluations_by_dimension),
        "cache_hits_by_dimension": dict(convergence.cache_hits_by_dimension),
        "elapsed_seconds": convergence.elapsed_seconds,
    }
    remaining["4275"] -= 504
    if remaining != {"3375": 0, "4275": 0}:
        raise ValueError("runtime_remaining_schedule_not_zero")
    analysis_elapsed = time.perf_counter() - started
    runtime = build_stage31_runtime_report(
        config, phase_ledger, p95, analysis_elapsed,
    )
    finalized = finalize_q1_q2_crossings(config, scan, convergence, runtime)
    modulation = evaluate_coupling_modulation(config, finalized)
    gate = evaluate_stage3_1_gate(config, provenance, solver, idle_convergence, finalized, convergence, modulation, runtime)
    result = QubitCouplingSweepResult(
        config, provenance.to_dict(), solver.to_dict(), idle.metrics.to_dict(), idle_convergence.to_dict(),
        scan, finalized, convergence, modulation, runtime, gate,
        tuple(gate.checks),
    )
    output = Path(output_dir)
    if config.acceptance_eligible:
        return publish_stage3_1_transaction(
            result,
            lambda artifact, notebook: assemble_stage3_1_verification_report(gate, artifact, notebook),
            output,
        )
    artifact = write_q1_q2_coupling_artifacts(result, output)
    notebook = write_q1_q2_coupling_notebook(artifact.path, output)
    report = assemble_stage3_1_verification_report(gate, artifact, notebook)
    write_stage3_1_verification_report(report, output)
    return report


def _phase(evaluations, cache_hits, started):
    return {
        "evaluations_by_dimension": dict(evaluations),
        "cache_hits_by_dimension": dict(cache_hits),
        "elapsed_seconds": time.perf_counter() - started,
    }
