"""Strongly typed Stage 3 static-spectrum contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class EigshConfig:
    which: str
    tolerance: float
    maxiter: int
    ncv: int
    v0_rule: str


@dataclass(frozen=True, slots=True)
class SolverValidationConfig:
    gap_dense_tolerance_GHz: float
    near_degenerate_gap_threshold_GHz: float
    partition_boundary_margin_GHz: float
    projector_error_norm: str
    projector_dense_tolerance: float
    projector_repeat_tolerance: float


@dataclass(frozen=True, slots=True)
class EigenConfig:
    num_states: int
    solver: str
    eigsh: EigshConfig
    solver_validation: SolverValidationConfig


@dataclass(frozen=True, slots=True)
class DressedLabelConfig:
    max_excitations: dict[str, int]
    max_total_excitations: int
    min_overlap: float
    assignment: str
    continuity_min_overlap: float


@dataclass(frozen=True, slots=True)
class MetricsConfig:
    compute_zz: bool
    compute_anharmonicity: bool
    compute_mode_participation: bool


@dataclass(frozen=True, slots=True)
class ConvergenceConfig:
    cutoff_increment: int
    crossing_refinement_coarse_points: int
    crossing_refinement_levels: int
    frequency_tolerance_MHz: float
    anharmonicity_tolerance_MHz: float
    zz_absolute_tolerance_MHz: float
    avoided_crossing_absolute_tolerance_MHz: float
    avoided_crossing_relative_tolerance: float
    participation_fraction_tolerance: float


@dataclass(frozen=True, slots=True)
class CrossingEvidenceConfig:
    endpoint_character_min_fraction: float
    character_exchange_min_delta: float
    target_pair_min_fraction_at_crossing: float
    splitting_significance_min_ratio: float


@dataclass(frozen=True, slots=True)
class FluxScanConfig:
    enabled: bool
    target: str
    start_phi0: float
    stop_phi0: float
    coarse_points: int
    min_refinement_levels: int
    max_refinement_levels: int
    refinement_points: int
    flux_key_decimal_places: int
    splitting_level_tolerance_MHz: float
    flux_energy_resolution_MHz: float


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    acceptance_budget_seconds: float
    smoke_budget_seconds: float
    over_budget_fallback: str
    solver_validation_artifact: Path
    solver_validation_approval: Path


@dataclass(frozen=True, slots=True)
class SpectrumConfig:
    schema_version: str
    name: str
    profile: str
    acceptance_eligible: bool
    source_hamiltonian_config: Path
    source_hamiltonian_artifacts: Path
    source_rebaseline_manifest: Path
    source_rebaseline_approval: Path
    eigen: EigenConfig
    dressed_labeling: DressedLabelConfig
    metrics: MetricsConfig
    convergence: ConvergenceConfig
    crossing_evidence: CrossingEvidenceConfig
    flux_scan: FluxScanConfig
    runtime: RuntimeConfig
    source_path: Path


@dataclass(frozen=True, slots=True)
class Stage2RebaselineManifest:
    path: Path
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Stage2RebaselineApproval:
    path: Path
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Stage2GapConsistencyReport:
    ok: bool
    max_abs_difference_GHz: float | None
    compared_gap_count: int
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "max_abs_difference_GHz": self.max_abs_difference_GHz,
            "compared_gap_count": self.compared_gap_count,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class ProvenanceReport:
    ok: bool
    rebaseline_manifest_path: Path
    rebaseline_manifest_sha256: str
    rebaseline_approval_path: Path
    rebaseline_approval_sha256: str
    approval_decision: str
    hamiltonian_config_sha256: str
    device_artifacts_sha256: str
    stage2_model_source_tree_sha256: str
    stage2_artifacts_sha256: str
    stage2_artifact_version: str
    stage2_dense_gap_consistency: Stage2GapConsistencyReport | None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rebaseline_manifest_path": self.rebaseline_manifest_path.as_posix(),
            "rebaseline_manifest_sha256": self.rebaseline_manifest_sha256,
            "rebaseline_approval_path": self.rebaseline_approval_path.as_posix(),
            "rebaseline_approval_sha256": self.rebaseline_approval_sha256,
            "approval_decision": self.approval_decision,
            "hamiltonian_config_sha256": self.hamiltonian_config_sha256,
            "device_artifacts_sha256": self.device_artifacts_sha256,
            "stage2_model_source_tree_sha256": self.stage2_model_source_tree_sha256,
            "stage2_artifacts_sha256": self.stage2_artifacts_sha256,
            "stage2_artifact_version": self.stage2_artifact_version,
            "stage2_dense_gap_consistency": (
                self.stage2_dense_gap_consistency.to_dict() if self.stage2_dense_gap_consistency else None
            ),
            "all_matches": self.ok,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class ValidatedSolverSpec:
    backend: str
    num_states: int
    which: str
    tolerance: float
    maxiter: int
    ncv: int
    v0_rule: str
    stage2_artifacts_sha256: str
    validation_artifact_sha256: str | None
    acceptance_eligible: bool


@dataclass(frozen=True, slots=True)
class SolverBackendValidationArtifact:
    path: Path
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SolverBackendValidationApproval:
    path: Path
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SolverBackendReport:
    ok: bool
    validated_spec: ValidatedSolverSpec | None
    validation_artifact_sha256: str | None
    validation_approval_sha256: str | None
    stage3_solver_source_tree_sha256: str
    environment_fingerprint_sha256: str
    p95_seconds_by_signature: dict[str, float]
    validated_solver_error_GHz: float
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "validated_spec": solver_spec_to_dict(self.validated_spec) if self.validated_spec else None,
            "validation_artifact_sha256": self.validation_artifact_sha256,
            "validation_approval_sha256": self.validation_approval_sha256,
            "stage3_solver_source_tree_sha256": self.stage3_solver_source_tree_sha256,
            "environment_fingerprint_sha256": self.environment_fingerprint_sha256,
            "p95_seconds_by_signature": dict(self.p95_seconds_by_signature),
            "validated_solver_error_GHz": self.validated_solver_error_GHz,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class SpectrumBuildContext:
    config: SpectrumConfig
    provenance: ProvenanceReport
    solver_backend_report: SolverBackendReport
    solver_spec: ValidatedSolverSpec
    hamiltonian_config: Any
    device_artifacts: Any
    ec_matrix_GHz: tuple[tuple[float, ...], ...]
    mode_capacitance_matrix_fF: tuple[tuple[float, ...], ...]
    effective_junctions: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class EigenstateTable:
    eigenvalues_GHz: np.ndarray
    eigenvectors: np.ndarray
    backend: str
    solve_time_seconds: float
    cutoffs: tuple[int, int, int]
    flux_key: str

    @property
    def gaps_GHz(self) -> np.ndarray:
        return self.eigenvalues_GHz - self.eigenvalues_GHz[0]


@dataclass(frozen=True, order=True, slots=True)
class BareStateLabel:
    q1: int
    c: int
    q2: int

    @property
    def key(self) -> str:
        return f"{self.q1}{self.c}{self.q2}"

    @property
    def values(self) -> tuple[int, int, int]:
        return (self.q1, self.c, self.q2)

    @property
    def total_excitations(self) -> int:
        return self.q1 + self.c + self.q2


@dataclass(frozen=True, slots=True)
class BareStateCatalog:
    labels: tuple[BareStateLabel, ...]
    target_vectors: np.ndarray
    mode_eigenvectors: dict[str, np.ndarray]
    mode_eigenvalues_GHz: dict[str, np.ndarray]
    dimensions: dict[str, int]


@dataclass(frozen=True, slots=True)
class DressedStateAssignment:
    label: BareStateLabel
    eigen_index: int
    energy_GHz: float
    gap_from_ground_GHz: float
    overlap: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label.key,
            "target_bare_state": list(self.label.values),
            "eigen_index": self.eigen_index,
            "energy_GHz": self.energy_GHz,
            "gap_from_ground_GHz": self.gap_from_ground_GHz,
            "overlap": self.overlap,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class DressedStateTable:
    assignments: tuple[DressedStateAssignment, ...]
    overlap_matrix: np.ndarray
    warnings: tuple[str, ...]

    def by_label(self) -> dict[str, DressedStateAssignment]:
        return {row.label.key: row for row in self.assignments}


@dataclass(frozen=True, slots=True)
class ModeParticipationRow:
    label: str
    eigen_index: int
    mean_excitations: dict[str, float]
    fractions: dict[str, float] | None
    total_mean_excitation: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "eigen_index": self.eigen_index,
            "mean_excitations": dict(self.mean_excitations),
            "fractions": dict(self.fractions) if self.fractions is not None else None,
            "total_mean_excitation": self.total_mean_excitation,
        }


@dataclass(frozen=True, slots=True)
class ModeParticipationTable:
    rows: tuple[ModeParticipationRow, ...]

    def by_label(self) -> dict[str, ModeParticipationRow]:
        return {row.label: row for row in self.rows}


@dataclass(frozen=True, slots=True)
class StaticMetricTable:
    transition_frequencies_GHz: dict[str, float]
    anharmonicities_GHz: dict[str, float]
    zz_metrics_GHz: dict[str, float]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_frequencies_GHz": dict(self.transition_frequencies_GHz),
            "anharmonicities_GHz": dict(self.anharmonicities_GHz),
            "zz_metrics_GHz": dict(self.zz_metrics_GHz),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class StaticSpectrumPointResult:
    flux_key: str
    flux_bias_phi0: float
    cutoffs: dict[str, int]
    eigenstates: EigenstateTable
    bare_catalog: BareStateCatalog
    dressed_states: DressedStateTable
    participation: ModeParticipationTable
    metrics: StaticMetricTable
    bare_transition_frequencies_GHz: dict[str, float]


@dataclass(frozen=True, slots=True)
class StaticMetricConvergenceReport:
    baseline_cutoffs: dict[str, int]
    refinement_increment: int
    tolerances_MHz: dict[str, float]
    per_mode_refinement_rows: tuple[dict[str, Any], ...]
    max_frequency_drift_MHz: float
    max_anharmonicity_drift_MHz: float
    max_zz_drift_MHz: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_cutoffs": dict(self.baseline_cutoffs),
            "refinement_increment": self.refinement_increment,
            "tolerances_MHz": dict(self.tolerances_MHz),
            "per_mode_refinement_rows": list(self.per_mode_refinement_rows),
            "max_frequency_drift_MHz": self.max_frequency_drift_MHz,
            "max_anharmonicity_drift_MHz": self.max_anharmonicity_drift_MHz,
            "max_zz_drift_MHz": self.max_zz_drift_MHz,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class FluxScanPoint:
    flux_key: str
    flux_bias_phi0: float
    eigenvalue_gaps_GHz: tuple[float, ...]
    bare_transition_frequencies_GHz: dict[str, float]
    bare_detunings_MHz: dict[str, float]
    branch_energies_GHz: dict[str, float]
    branch_continuity_overlaps: dict[str, float]
    mode_participation_by_branch: dict[str, dict[str, Any]]
    assignment_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "flux_key": self.flux_key,
            "flux_bias_phi0": self.flux_bias_phi0,
            "eigenvalue_gaps_GHz": list(self.eigenvalue_gaps_GHz),
            "bare_transition_frequencies_GHz": dict(self.bare_transition_frequencies_GHz),
            "bare_detunings_MHz": dict(self.bare_detunings_MHz),
            "branch_energies_GHz": dict(self.branch_energies_GHz),
            "branch_continuity_overlaps": dict(self.branch_continuity_overlaps),
            "mode_participation_by_branch": self.mode_participation_by_branch,
            "assignment_summary": self.assignment_summary,
        }


@dataclass(frozen=True, slots=True)
class FluxScanResult:
    target: str
    evaluated_points: tuple[FluxScanPoint, ...]
    candidate_levels: dict[str, tuple[dict[str, Any], ...]]
    candidates: tuple[dict[str, Any], ...]
    solver_evaluations: int
    cache_hits: int
    wall_time_seconds: float
    acceptance_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "evaluated_flux_points_phi0": [row.flux_bias_phi0 for row in self.evaluated_points],
            "points": [row.to_dict() for row in self.evaluated_points],
            "candidate_levels": {key: list(value) for key, value in self.candidate_levels.items()},
            "candidates": list(self.candidates),
            "solver_evaluations": self.solver_evaluations,
            "cache_hits": self.cache_hits,
            "wall_time_seconds": self.wall_time_seconds,
            "acceptance_eligible": self.acceptance_eligible,
        }


@dataclass(frozen=True, slots=True)
class CrossingConvergenceReport:
    candidates: dict[str, dict[str, Any]]
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {"candidates": self.candidates, "passed": self.passed}


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    elapsed_seconds: float
    completed_jobs_by_cutoff_signature: dict[str, int]
    remaining_job_upper_bound_by_signature: dict[str, int]
    p95_seconds_by_signature: dict[str, float]
    projected_remaining_seconds: float
    projected_total_seconds: float
    budget_seconds: float
    conservative_solve_ceiling: int
    within_budget: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "completed_jobs_by_cutoff_signature": dict(self.completed_jobs_by_cutoff_signature),
            "remaining_job_upper_bound_by_signature": dict(self.remaining_job_upper_bound_by_signature),
            "p95_seconds_by_signature": dict(self.p95_seconds_by_signature),
            "projected_remaining_seconds": self.projected_remaining_seconds,
            "projected_total_seconds": self.projected_total_seconds,
            "budget_seconds": self.budget_seconds,
            "conservative_solve_ceiling": self.conservative_solve_ceiling,
            "within_budget": self.within_budget,
        }


@dataclass(frozen=True, slots=True)
class RuntimeReport:
    profile: str
    acceptance_eligible: bool
    budget_seconds: float
    projected_total_seconds: float
    wall_time_seconds: float
    solver_backend: str
    solver_validation_artifact: str
    solver_validation_approval: str
    execution_plan: ExecutionPlan
    solver_evaluations: int
    cache_hits: int
    solve_time_p50_seconds: float
    solve_time_p95_seconds: float
    budget_status: str

    def to_dict(self) -> dict[str, Any]:
        result = self.execution_plan.to_dict()
        return {
            "profile": self.profile,
            "acceptance_eligible": self.acceptance_eligible,
            "budget_seconds": self.budget_seconds,
            "projected_total_seconds": self.projected_total_seconds,
            "wall_time_seconds": self.wall_time_seconds,
            "solver_backend": self.solver_backend,
            "solver_validation_artifact": self.solver_validation_artifact,
            "solver_validation_approval": self.solver_validation_approval,
            "execution_plan": result,
            "solver_evaluations": self.solver_evaluations,
            "cache_hits": self.cache_hits,
            "solve_time_p50_seconds": self.solve_time_p50_seconds,
            "solve_time_p95_seconds": self.solve_time_p95_seconds,
            "budget_status": self.budget_status,
        }


@dataclass(frozen=True, slots=True)
class StageGateDecision:
    analysis_completed: bool
    status: str
    stage4_ready: bool
    blocking_reasons: tuple[str, ...]
    candidate_statuses: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_completed": self.analysis_completed,
            "status": self.status,
            "stage4_ready": self.stage4_ready,
            "blocking_reasons": list(self.blocking_reasons),
            "candidate_statuses": dict(self.candidate_statuses),
        }


@dataclass(frozen=True, slots=True)
class StaticSpectrumResult:
    config: SpectrumConfig
    provenance: ProvenanceReport
    solver_backend_report: SolverBackendReport
    baseline: StaticSpectrumPointResult
    metric_convergence: StaticMetricConvergenceReport
    flux_scan: FluxScanResult
    crossing_convergence: CrossingConvergenceReport
    runtime: RuntimeReport
    stage_gate: StageGateDecision
    warnings: tuple[str, ...]
    checks: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class StaticSpectrumArtifactSet:
    root: Path
    static_spectrum_artifacts: Path
    verification_notebook: Path


@dataclass(frozen=True, slots=True)
class StaticSpectrumVerificationReport:
    ok: bool
    execution_succeeded: bool
    profile: str
    acceptance_eligible: bool
    stage_gate: StageGateDecision
    checks: tuple[dict[str, Any], ...]
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "execution_succeeded": self.execution_succeeded,
            "profile": self.profile,
            "acceptance_eligible": self.acceptance_eligible,
            "stage_gate": self.stage_gate.to_dict(),
            "checks": list(self.checks),
            "artifacts": dict(self.artifacts),
        }


def solver_spec_to_dict(spec: ValidatedSolverSpec) -> dict[str, Any]:
    return {
        "backend": spec.backend,
        "num_states": spec.num_states,
        "which": spec.which,
        "tolerance": spec.tolerance,
        "maxiter": spec.maxiter,
        "ncv": spec.ncv,
        "v0_rule": spec.v0_rule,
        "stage2_artifacts_sha256": spec.stage2_artifacts_sha256,
        "validation_artifact_sha256": spec.validation_artifact_sha256,
        "acceptance_eligible": spec.acceptance_eligible,
    }
