"""Typed contracts for the Stage 3.1 q1-q2 coupling experiment."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqvm.spectrum.models import DressedLabelConfig, EigshConfig, MetricsConfig


@dataclass(frozen=True, slots=True)
class Stage31SolverValidationConfig:
    gap_dense_tolerance_GHz: float
    q1_q2_splitting_dense_tolerance_GHz: float
    near_degenerate_gap_threshold_GHz: float
    partition_boundary_margin_GHz: float
    projector_error_norm: str
    projector_dense_tolerance: float
    projector_repeat_tolerance: float


@dataclass(frozen=True, slots=True)
class Stage31EigenConfig:
    num_states: int
    solver: str
    eigsh: EigshConfig
    solver_validation: Stage31SolverValidationConfig


@dataclass(frozen=True, slots=True)
class CouplingScanConfig:
    fixed_q1_flux_phi0: float
    inner_target: str
    inner_start_phi0: float
    inner_stop_phi0: float
    inner_coarse_points: int
    refinement_points: int
    min_refinement_levels: int
    max_refinement_levels: int
    flux_key_decimal_places: int
    coupler_flux_points_phi0: tuple[float, ...]
    acceptance_anchor_fluxes_phi0: tuple[float, ...]
    reference_coupler_flux_phi0: float


@dataclass(frozen=True, slots=True)
class CouplingEvidenceConfig:
    bare_detuning_evidence_tolerance_MHz: float
    resonance_alignment_tolerance_MHz: float
    endpoint_character_min_fraction: float
    character_exchange_min_delta: float
    target_bare_projector_min_weight: float
    target_total_excitation_min: float
    target_total_excitation_max: float
    coupler_fraction_max: float
    target_subspace_continuity_min: float


@dataclass(frozen=True, slots=True)
class CouplingConvergenceConfig:
    cutoff_increment: int
    crossing_refinement_coarse_points: int
    crossing_refinement_levels: int
    frequency_tolerance_MHz: float
    anharmonicity_tolerance_MHz: float
    zz_absolute_tolerance_MHz: float
    crossing_absolute_tolerance_MHz: float
    crossing_relative_tolerance: float
    splitting_significance_min_ratio: float
    modulation_significance_min_ratio: float
    splitting_level_tolerance_MHz: float
    flux_energy_resolution_MHz: float


@dataclass(frozen=True, slots=True)
class Stage31RuntimeConfig:
    acceptance_budget_seconds: float
    smoke_budget_seconds: float
    conservative_solve_ceiling: int
    over_budget_fallback: str
    solver_validation_artifact: Path
    solver_validation_approval: Path


@dataclass(frozen=True, slots=True)
class QubitCouplingConfig:
    schema_version: str
    experiment_type: str
    name: str
    profile: str
    acceptance_eligible: bool
    source_hamiltonian_config: Path
    source_hamiltonian_artifacts: Path
    source_rebaseline_manifest: Path
    source_rebaseline_approval: Path
    source_design_freeze_manifest: Path
    eigen: Stage31EigenConfig
    dressed_labeling: DressedLabelConfig
    metrics: MetricsConfig
    compute_target_bare_projector: bool
    scan: CouplingScanConfig
    evidence: CouplingEvidenceConfig
    convergence: CouplingConvergenceConfig
    runtime: Stage31RuntimeConfig
    source_path: Path


@dataclass(frozen=True, slots=True)
class QubitCrossingScanEvidence:
    coupler_flux_key: str
    evaluated_points: tuple[dict[str, Any], ...]
    refinement_levels: tuple[dict[str, Any], ...]
    final_bracket_keys: tuple[str, str] | None
    final_evidence_keys: tuple[str, ...]
    resonance_root: dict[str, Any]
    minimum_splitting_MHz: float
    minimum_key: str
    predicates: dict[str, bool]
    diagnostics: dict[str, Any]
    abs_g_eff_MHz: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "coupler_flux_key": self.coupler_flux_key,
            "evaluated_points": list(self.evaluated_points),
            "refinement_levels": list(self.refinement_levels),
            "final_bracket_keys": list(self.final_bracket_keys) if self.final_bracket_keys else None,
            "final_evidence_keys": list(self.final_evidence_keys),
            "resonance_root": self.resonance_root,
            "minimum_splitting_MHz": self.minimum_splitting_MHz,
            "minimum_key": self.minimum_key,
            "predicates": self.predicates,
            "diagnostics": self.diagnostics,
            "abs_g_eff_MHz": None,
        }


@dataclass(frozen=True, slots=True)
class QubitCouplingScanResult:
    coupler_flux_keys: tuple[str, ...]
    crossings: tuple[QubitCrossingScanEvidence, ...]
    solver_evaluations: int
    cache_hits: int
    elapsed_seconds: float
    evaluations_by_dimension: dict[str, int] = field(default_factory=lambda: {"3375": 0, "4275": 0})
    cache_hits_by_dimension: dict[str, int] = field(default_factory=lambda: {"3375": 0, "4275": 0})

    def by_key(self) -> dict[str, QubitCrossingScanEvidence]:
        return {row.coupler_flux_key: row for row in self.crossings}


@dataclass(frozen=True, slots=True)
class QubitCrossingConvergenceReport:
    anchor_keys: tuple[str, ...]
    anchors: dict[str, dict[str, Any]]
    passed: bool
    evaluations_by_dimension: dict[str, int] = field(default_factory=lambda: {"3375": 0, "4275": 0})
    cache_hits_by_dimension: dict[str, int] = field(default_factory=lambda: {"3375": 0, "4275": 0})
    elapsed_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class FinalizedQubitCouplingResult:
    coupler_flux_keys: tuple[str, ...]
    points: tuple[dict[str, Any], ...]

    def by_key(self) -> dict[str, dict[str, Any]]:
        return {row["coupler_flux_key"]: row for row in self.points}


@dataclass(frozen=True, slots=True)
class CouplingModulationReport:
    reference_key: str
    comparisons: tuple[dict[str, Any], ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class Stage31RuntimeReport:
    acceptance_eligible: bool
    budget_seconds: float
    phase_ledger: dict[str, dict[str, Any]]
    p95_seconds_by_dimension: dict[str, float]
    evaluations_by_dimension: dict[str, int]
    cache_hits_by_dimension: dict[str, int]
    remaining_by_dimension: dict[str, int]
    solver_evaluations: int
    cache_hits: int
    analysis_elapsed_seconds: float
    projected_total_seconds: float
    projected_total_evaluations: int
    conservative_solve_ceiling: int
    within_budget: bool
    within_ceiling: bool

    @property
    def elapsed_seconds(self) -> float:
        return self.analysis_elapsed_seconds


@dataclass(frozen=True, slots=True)
class Stage31ComputationalGate:
    computational_ready: bool
    status: str
    checks: tuple[dict[str, Any], ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "computational_ready": self.computational_ready,
            "status": self.status,
            "checks": list(self.checks),
            "blocking_reasons": list(self.blocking_reasons),
        }


@dataclass(frozen=True, slots=True)
class QubitCouplingSweepResult:
    config: QubitCouplingConfig
    provenance: dict[str, Any]
    solver_backend: dict[str, Any]
    idle_metrics: dict[str, Any]
    idle_convergence: dict[str, Any]
    scan: QubitCouplingScanResult
    finalized: FinalizedQubitCouplingResult
    crossing_convergence: QubitCrossingConvergenceReport
    modulation: CouplingModulationReport
    runtime: Stage31RuntimeReport
    computational_gate: Stage31ComputationalGate
    checks: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ArtifactWriteResult:
    completed: bool
    path: Path
    sha256: str
    canonical_and_finite: bool


@dataclass(frozen=True, slots=True)
class NotebookWriteResult:
    completed: bool
    path: Path
    sha256: str
    code_cell_count: int
    executed_code_cell_count: int
    error_output_count: int


@dataclass(frozen=True, slots=True)
class VerificationReportWriteResult:
    completed: bool
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class Stage31VerificationReport:
    ok: bool
    execution_succeeded: bool
    acceptance_candidate_ready: bool
    stage4_ready: bool
    approval_status: str
    computational_gate: Stage31ComputationalGate
    artifact_write: ArtifactWriteResult | None
    notebook_write: NotebookWriteResult | None
    artifact_sha256: str | None
    notebook_sha256: str | None
    checks: tuple[dict[str, Any], ...]
    post_write_checks: tuple[dict[str, Any], ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1",
            "artifact_type": "stage_03_1_q1_q2_coupling_verification_report",
            "artifact_version": "0.1",
            "ok": self.ok,
            "execution_succeeded": self.execution_succeeded,
            "acceptance_candidate_ready": self.acceptance_candidate_ready,
            "stage4_ready": False,
            "approval_status": "pending",
            "computational_gate": self.computational_gate.to_dict(),
            "artifact_write": _write_result(self.artifact_write),
            "notebook_write": _write_result(self.notebook_write),
            "artifact_sha256": self.artifact_sha256,
            "notebook_sha256": self.notebook_sha256,
            "checks": list(self.checks),
            "post_write_checks": list(self.post_write_checks),
            "blocking_reasons": list(self.blocking_reasons),
        }


@dataclass(frozen=True, slots=True)
class Stage4ReadinessReport:
    ok: bool
    stage4_ready: bool
    approval_decision: str
    approval_sha256: str | None
    bound_hashes: dict[str, str]
    checks: tuple[dict[str, Any], ...]
    blocking_reasons: tuple[str, ...]


def _write_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {name: (item.as_posix() if isinstance(item, Path) else item) for name, item in ((field, getattr(value, field)) for field in value.__dataclass_fields__)}
