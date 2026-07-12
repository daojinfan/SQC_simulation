"""Strict Stage 3 spectrum configuration loading."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from sqvm.spectrum.models import (
    ConvergenceConfig,
    CrossingEvidenceConfig,
    DressedLabelConfig,
    EigenConfig,
    EigshConfig,
    FluxScanConfig,
    MetricsConfig,
    RuntimeConfig,
    SolverValidationConfig,
    SpectrumConfig,
)


SUPPORTED_MODES = ("q1", "c", "q2")
SUPPORTED_PROFILES = {"acceptance", "smoke", "dense_pilot", "solver_validation"}


def load_spectrum_config(path: str | Path) -> SpectrumConfig:
    source_path = Path(path)
    try:
        raw = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"{source_path}: cannot read spectrum config: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"{source_path}: invalid YAML: {exc}") from exc
    root = _mapping(raw, "root")
    if root.get("schema_version") != "0.1":
        raise ValueError("schema_version must be 0.1")
    spectrum = _required_mapping(root, "spectrum")

    profile = _string(spectrum, "profile", default="acceptance")
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"spectrum.profile must be one of {sorted(SUPPORTED_PROFILES)}")
    acceptance_eligible = _bool(spectrum, "acceptance_eligible", default=profile == "acceptance")
    if acceptance_eligible != (profile == "acceptance"):
        raise ValueError("only the acceptance profile may set acceptance_eligible=true")

    eigen_raw = _required_mapping(spectrum, "eigen")
    num_states = _positive_int(eigen_raw, "num_states")
    solver = _string(eigen_raw, "solver")
    if solver not in {"validated_eigsh", "dense_eigh"}:
        raise ValueError("spectrum.eigen.solver must be validated_eigsh or dense_eigh")
    if acceptance_eligible and solver != "validated_eigsh":
        raise ValueError("acceptance profile requires validated_eigsh")
    eigsh_raw = _required_mapping(eigen_raw, "eigsh")
    eigsh = EigshConfig(
        which=_exact_string(eigsh_raw, "which", "SA"),
        tolerance=_positive_float(eigsh_raw, "tolerance"),
        maxiter=_positive_int(eigsh_raw, "maxiter"),
        ncv=_positive_int(eigsh_raw, "ncv"),
        v0_rule=_exact_string(eigsh_raw, "v0_rule", "sha256_counter_v1"),
    )
    if eigsh.ncv <= 2 * num_states:
        raise ValueError("spectrum.eigen.eigsh.ncv must be greater than 2*num_states")
    validation_raw = _required_mapping(eigen_raw, "solver_validation")
    validation = SolverValidationConfig(
        gap_dense_tolerance_GHz=_positive_float(validation_raw, "gap_dense_tolerance_GHz"),
        near_degenerate_gap_threshold_GHz=_positive_float(
            validation_raw, "near_degenerate_gap_threshold_GHz"
        ),
        partition_boundary_margin_GHz=_positive_float(validation_raw, "partition_boundary_margin_GHz"),
        projector_error_norm=_exact_string(validation_raw, "projector_error_norm", "spectral_2"),
        projector_dense_tolerance=_positive_float(validation_raw, "projector_dense_tolerance"),
        projector_repeat_tolerance=_positive_float(validation_raw, "projector_repeat_tolerance"),
    )

    label_raw = _required_mapping(spectrum, "dressed_labeling")
    max_excitations_raw = _required_mapping(label_raw, "max_excitations")
    if set(max_excitations_raw) != set(SUPPORTED_MODES):
        raise ValueError("dressed_labeling.max_excitations must contain exactly q1, c, q2")
    max_excitations = {mode: _nonnegative_int(max_excitations_raw, mode) for mode in SUPPORTED_MODES}
    label = DressedLabelConfig(
        max_excitations=max_excitations,
        max_total_excitations=_positive_int(label_raw, "max_total_excitations"),
        min_overlap=_unit_interval(label_raw, "min_overlap"),
        assignment=_exact_string(label_raw, "assignment", "global_overlap"),
        continuity_min_overlap=_unit_interval(label_raw, "continuity_min_overlap"),
    )
    required_label_count = _target_label_count(label)
    if num_states < required_label_count:
        raise ValueError(f"num_states must be at least {required_label_count} for the target bare catalog")

    metrics_raw = _required_mapping(spectrum, "metrics")
    metrics = MetricsConfig(
        compute_zz=_bool(metrics_raw, "compute_zz"),
        compute_anharmonicity=_bool(metrics_raw, "compute_anharmonicity"),
        compute_mode_participation=_bool(metrics_raw, "compute_mode_participation"),
    )
    convergence_raw = _required_mapping(spectrum, "convergence")
    convergence = ConvergenceConfig(
        cutoff_increment=_positive_int(convergence_raw, "cutoff_increment"),
        crossing_refinement_coarse_points=_odd_int(convergence_raw, "crossing_refinement_coarse_points", 17),
        crossing_refinement_levels=_positive_int(convergence_raw, "crossing_refinement_levels"),
        frequency_tolerance_MHz=_positive_float(convergence_raw, "frequency_tolerance_MHz"),
        anharmonicity_tolerance_MHz=_positive_float(convergence_raw, "anharmonicity_tolerance_MHz"),
        zz_absolute_tolerance_MHz=_positive_float(convergence_raw, "zz_absolute_tolerance_MHz"),
        avoided_crossing_absolute_tolerance_MHz=_positive_float(
            convergence_raw, "avoided_crossing_absolute_tolerance_MHz"
        ),
        avoided_crossing_relative_tolerance=_positive_float(
            convergence_raw, "avoided_crossing_relative_tolerance"
        ),
        participation_fraction_tolerance=_positive_float(
            convergence_raw, "participation_fraction_tolerance"
        ),
    )
    evidence_raw = _required_mapping(spectrum, "crossing_evidence")
    evidence = CrossingEvidenceConfig(
        endpoint_character_min_fraction=_unit_interval(evidence_raw, "endpoint_character_min_fraction"),
        character_exchange_min_delta=_unit_interval(evidence_raw, "character_exchange_min_delta"),
        target_pair_min_fraction_at_crossing=_unit_interval(
            evidence_raw, "target_pair_min_fraction_at_crossing"
        ),
        splitting_significance_min_ratio=_positive_float(evidence_raw, "splitting_significance_min_ratio"),
    )
    flux_raw = _required_mapping(spectrum, "flux_scan")
    enabled = _bool(flux_raw, "enabled")
    start = _finite_float(flux_raw, "start_phi0")
    stop = _finite_float(flux_raw, "stop_phi0")
    if start >= stop:
        raise ValueError("spectrum.flux_scan.start_phi0 must be less than stop_phi0")
    min_levels = _positive_int(flux_raw, "min_refinement_levels")
    max_levels = _positive_int(flux_raw, "max_refinement_levels")
    if max_levels < min_levels:
        raise ValueError("max_refinement_levels must be >= min_refinement_levels")
    flux = FluxScanConfig(
        enabled=enabled,
        target=_exact_string(flux_raw, "target", "c"),
        start_phi0=start,
        stop_phi0=stop,
        coarse_points=_odd_int(flux_raw, "coarse_points", 9),
        min_refinement_levels=min_levels,
        max_refinement_levels=max_levels,
        refinement_points=_odd_int(flux_raw, "refinement_points", 7),
        flux_key_decimal_places=_exact_int(flux_raw, "flux_key_decimal_places", 12),
        splitting_level_tolerance_MHz=_positive_float(flux_raw, "splitting_level_tolerance_MHz"),
        flux_energy_resolution_MHz=_positive_float(flux_raw, "flux_energy_resolution_MHz"),
    )
    runtime_raw = _required_mapping(spectrum, "runtime")
    runtime = RuntimeConfig(
        acceptance_budget_seconds=_positive_float(runtime_raw, "acceptance_budget_seconds"),
        smoke_budget_seconds=_positive_float(runtime_raw, "smoke_budget_seconds"),
        over_budget_fallback=_exact_string(runtime_raw, "over_budget_fallback", "fail"),
        solver_validation_artifact=Path(_string(runtime_raw, "solver_validation_artifact")),
        solver_validation_approval=Path(_string(runtime_raw, "solver_validation_approval")),
    )
    return SpectrumConfig(
        schema_version="0.1",
        name=_string(spectrum, "name"),
        profile=profile,
        acceptance_eligible=acceptance_eligible,
        source_hamiltonian_config=Path(_string(spectrum, "source_hamiltonian_config")),
        source_hamiltonian_artifacts=Path(_string(spectrum, "source_hamiltonian_artifacts")),
        source_rebaseline_manifest=Path(_string(spectrum, "source_rebaseline_manifest")),
        source_rebaseline_approval=Path(_string(spectrum, "source_rebaseline_approval")),
        eigen=EigenConfig(num_states=num_states, solver=solver, eigsh=eigsh, solver_validation=validation),
        dressed_labeling=label,
        metrics=metrics,
        convergence=convergence,
        crossing_evidence=evidence,
        flux_scan=flux,
        runtime=runtime,
        source_path=source_path,
    )


def spectrum_config_to_dict(config: SpectrumConfig) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "name": config.name,
        "profile": config.profile,
        "acceptance_eligible": config.acceptance_eligible,
        "source_hamiltonian_config": config.source_hamiltonian_config.as_posix(),
        "source_hamiltonian_artifacts": config.source_hamiltonian_artifacts.as_posix(),
        "source_rebaseline_manifest": config.source_rebaseline_manifest.as_posix(),
        "source_rebaseline_approval": config.source_rebaseline_approval.as_posix(),
        "eigen": {
            "num_states": config.eigen.num_states,
            "solver": config.eigen.solver,
            "eigsh": {
                "which": config.eigen.eigsh.which,
                "tolerance": config.eigen.eigsh.tolerance,
                "maxiter": config.eigen.eigsh.maxiter,
                "ncv": config.eigen.eigsh.ncv,
                "v0_rule": config.eigen.eigsh.v0_rule,
            },
            "solver_validation": {
                "gap_dense_tolerance_GHz": config.eigen.solver_validation.gap_dense_tolerance_GHz,
                "near_degenerate_gap_threshold_GHz": (
                    config.eigen.solver_validation.near_degenerate_gap_threshold_GHz
                ),
                "partition_boundary_margin_GHz": config.eigen.solver_validation.partition_boundary_margin_GHz,
                "projector_error_norm": config.eigen.solver_validation.projector_error_norm,
                "projector_dense_tolerance": config.eigen.solver_validation.projector_dense_tolerance,
                "projector_repeat_tolerance": config.eigen.solver_validation.projector_repeat_tolerance,
            },
        },
        "dressed_labeling": {
            "max_excitations": dict(config.dressed_labeling.max_excitations),
            "max_total_excitations": config.dressed_labeling.max_total_excitations,
            "min_overlap": config.dressed_labeling.min_overlap,
            "assignment": config.dressed_labeling.assignment,
            "continuity_min_overlap": config.dressed_labeling.continuity_min_overlap,
        },
        "metrics": {
            "compute_zz": config.metrics.compute_zz,
            "compute_anharmonicity": config.metrics.compute_anharmonicity,
            "compute_mode_participation": config.metrics.compute_mode_participation,
        },
        "convergence": config.convergence.__dict__ if hasattr(config.convergence, "__dict__") else {
            field: getattr(config.convergence, field) for field in config.convergence.__dataclass_fields__
        },
        "crossing_evidence": {
            field: getattr(config.crossing_evidence, field)
            for field in config.crossing_evidence.__dataclass_fields__
        },
        "flux_scan": {field: getattr(config.flux_scan, field) for field in config.flux_scan.__dataclass_fields__},
        "runtime": {
            "acceptance_budget_seconds": config.runtime.acceptance_budget_seconds,
            "smoke_budget_seconds": config.runtime.smoke_budget_seconds,
            "over_budget_fallback": config.runtime.over_budget_fallback,
            "solver_validation_artifact": config.runtime.solver_validation_artifact.as_posix(),
            "solver_validation_approval": config.runtime.solver_validation_approval.as_posix(),
        },
    }


def _target_label_count(config: DressedLabelConfig) -> int:
    count = 0
    for q1 in range(config.max_excitations["q1"] + 1):
        for c in range(config.max_excitations["c"] + 1):
            for q2 in range(config.max_excitations["q2"] + 1):
                if q1 + c + q2 <= config.max_total_excitations:
                    count += 1
    return count


def _mapping(value: Any, display: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{display} must be a mapping")
    return value


def _required_mapping(raw: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in raw:
        raise ValueError(f"{key} is required")
    return _mapping(raw[key], key)


def _string(raw: Mapping[str, Any], key: str, *, default: str | None = None) -> str:
    value = raw.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def _exact_string(raw: Mapping[str, Any], key: str, expected: str) -> str:
    value = _string(raw, key)
    if value != expected:
        raise ValueError(f"{key} must be {expected}")
    return value


def _bool(raw: Mapping[str, Any], key: str, *, default: bool | None = None) -> bool:
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _positive_int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _nonnegative_int(raw: Mapping[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a nonnegative integer")
    return value


def _odd_int(raw: Mapping[str, Any], key: str, minimum: int) -> int:
    value = _positive_int(raw, key)
    if value < minimum or value % 2 == 0:
        raise ValueError(f"{key} must be odd and at least {minimum}")
    return value


def _exact_int(raw: Mapping[str, Any], key: str, expected: int) -> int:
    value = _positive_int(raw, key)
    if value != expected:
        raise ValueError(f"{key} must be {expected}")
    return value


def _finite_float(raw: Mapping[str, Any], key: str) -> float:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a finite number")
    converted = float(value)
    if not (-float("inf") < converted < float("inf")):
        raise ValueError(f"{key} must be a finite number")
    return converted


def _positive_float(raw: Mapping[str, Any], key: str) -> float:
    value = _finite_float(raw, key)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _unit_interval(raw: Mapping[str, Any], key: str) -> float:
    value = _finite_float(raw, key)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{key} must be in [0, 1]")
    return value
