"""Strict schema-0.2 configuration and design-freeze validation."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256
from sqvm.spectrum.models import DressedLabelConfig, EigshConfig, MetricsConfig
from sqvm.spectrum.solver import canonical_flux_text
from sqvm.spectrum.stage31_models import (
    CouplingConvergenceConfig,
    CouplingEvidenceConfig,
    CouplingScanConfig,
    QubitCouplingConfig,
    Stage31EigenConfig,
    Stage31RuntimeConfig,
    Stage31SolverValidationConfig,
)


class _UniqueLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_q1_q2_coupling_config(path: str | Path) -> QubitCouplingConfig:
    source = Path(path)
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"invalid Stage 3.1 config: {exc}") from exc
    root = _mapping(raw, "root")
    _exact_keys(root, {"schema_version", "spectrum"}, "root")
    _exact(root, "schema_version", "0.2")
    spectrum = _mapping(root["spectrum"], "spectrum")
    _exact_keys(
        spectrum,
        {
            "experiment_type", "name", "profile", "acceptance_eligible",
            "source_hamiltonian_config", "source_hamiltonian_artifacts",
            "source_rebaseline_manifest", "source_rebaseline_approval",
            "source_design_freeze_manifest", "eigen", "dressed_labeling", "metrics",
            "scan", "evidence", "convergence", "runtime",
        },
        "spectrum",
    )
    _exact(spectrum, "experiment_type", "q1_q2_coupling_vs_coupler")
    _exact(spectrum, "name", "demo_2q1c_q1q2_coupling")
    profile = _string(spectrum, "profile")
    if profile not in {"acceptance", "smoke", "dense_pilot", "solver_validation"}:
        raise ValueError("invalid Stage 3.1 profile")
    eligible = _bool(spectrum, "acceptance_eligible")
    if eligible != (profile == "acceptance"):
        raise ValueError("only acceptance profile is acceptance_eligible")

    eigen_raw = _section(spectrum, "eigen", {"num_states", "solver", "eigsh", "solver_validation"})
    eigsh_raw = _section(eigen_raw, "eigsh", {"which", "tolerance", "maxiter", "ncv", "v0_rule"})
    _exact(eigsh_raw, "which", "SA")
    _exact(eigsh_raw, "v0_rule", "sha256_counter_v2")
    eigsh = EigshConfig("SA", _positive(eigsh_raw, "tolerance"), _positive_int(eigsh_raw, "maxiter"), _positive_int(eigsh_raw, "ncv"), "sha256_counter_v2")
    validation_raw = _section(
        eigen_raw,
        "solver_validation",
        {
            "gap_dense_tolerance_GHz", "q1_q2_splitting_dense_tolerance_GHz",
            "near_degenerate_gap_threshold_GHz", "partition_boundary_margin_GHz",
            "projector_error_norm", "projector_dense_tolerance", "projector_repeat_tolerance",
        },
    )
    _exact(validation_raw, "projector_error_norm", "spectral_2")
    validation = Stage31SolverValidationConfig(
        *(_positive(validation_raw, key) for key in (
            "gap_dense_tolerance_GHz", "q1_q2_splitting_dense_tolerance_GHz",
            "near_degenerate_gap_threshold_GHz", "partition_boundary_margin_GHz",
        )),
        "spectral_2",
        _positive(validation_raw, "projector_dense_tolerance"),
        _positive(validation_raw, "projector_repeat_tolerance"),
    )
    eigen = Stage31EigenConfig(
        _positive_int(eigen_raw, "num_states"),
        _string(eigen_raw, "solver"),
        eigsh,
        validation,
    )

    label_raw = _section(spectrum, "dressed_labeling", {"max_excitations", "max_total_excitations", "min_overlap", "assignment"})
    modes = _mapping(label_raw["max_excitations"], "max_excitations")
    _exact_keys(modes, {"q1", "c", "q2"}, "max_excitations")
    _exact(label_raw, "assignment", "global_overlap")
    labeling = DressedLabelConfig(
        {mode: _nonnegative_int(modes, mode) for mode in ("q1", "c", "q2")},
        _positive_int(label_raw, "max_total_excitations"),
        _fraction(label_raw, "min_overlap"),
        "global_overlap",
        0.90,
    )
    metrics_raw = _section(spectrum, "metrics", {"compute_zz", "compute_anharmonicity", "compute_mode_participation", "compute_target_bare_projector"})
    metrics = MetricsConfig(*(_bool(metrics_raw, key) for key in ("compute_zz", "compute_anharmonicity", "compute_mode_participation")))

    scan_raw = _section(
        spectrum,
        "scan",
        {
            "fixed_q1_flux_phi0", "inner_target", "inner_start_phi0", "inner_stop_phi0",
            "inner_coarse_points", "refinement_points", "min_refinement_levels",
            "max_refinement_levels", "flux_key_decimal_places", "coupler_flux_points_phi0",
            "acceptance_anchor_fluxes_phi0", "reference_coupler_flux_phi0",
        },
    )
    _exact(scan_raw, "inner_target", "q2")
    _exact_int(scan_raw, "flux_key_decimal_places", 12)
    couplers = _flux_list(scan_raw, "coupler_flux_points_phi0")
    anchors = _flux_list(scan_raw, "acceptance_anchor_fluxes_phi0")
    scan = CouplingScanConfig(
        _finite(scan_raw, "fixed_q1_flux_phi0"), "q2",
        _finite(scan_raw, "inner_start_phi0"), _finite(scan_raw, "inner_stop_phi0"),
        _odd(scan_raw, "inner_coarse_points"), _odd(scan_raw, "refinement_points"),
        _positive_int(scan_raw, "min_refinement_levels"), _positive_int(scan_raw, "max_refinement_levels"),
        12, couplers, anchors, _finite(scan_raw, "reference_coupler_flux_phi0"),
    )
    if not (0.0 <= scan.inner_start_phi0 < scan.inner_stop_phi0 < 0.5):
        raise ValueError("invalid inner scan bounds")
    if scan.min_refinement_levels > scan.max_refinement_levels:
        raise ValueError("min_refinement_levels exceeds max")

    evidence_raw = _section(spectrum, "evidence", {
        "bare_detuning_evidence_tolerance_MHz", "resonance_alignment_tolerance_MHz",
        "endpoint_character_min_fraction", "character_exchange_min_delta",
        "target_bare_projector_min_weight", "target_total_excitation_min",
        "target_total_excitation_max", "coupler_fraction_max", "target_subspace_continuity_min",
    })
    evidence = CouplingEvidenceConfig(
        _positive(evidence_raw, "bare_detuning_evidence_tolerance_MHz"),
        _positive(evidence_raw, "resonance_alignment_tolerance_MHz"),
        *(_fraction(evidence_raw, key) for key in (
            "endpoint_character_min_fraction", "character_exchange_min_delta",
            "target_bare_projector_min_weight",
        )),
        _finite(evidence_raw, "target_total_excitation_min"),
        _finite(evidence_raw, "target_total_excitation_max"),
        _fraction(evidence_raw, "coupler_fraction_max"),
        _fraction(evidence_raw, "target_subspace_continuity_min"),
    )
    if evidence.target_total_excitation_min >= evidence.target_total_excitation_max:
        raise ValueError("target excitation range is invalid")

    conv_raw = _section(spectrum, "convergence", {
        "cutoff_increment", "crossing_refinement_coarse_points", "crossing_refinement_levels",
        "frequency_tolerance_MHz", "anharmonicity_tolerance_MHz", "zz_absolute_tolerance_MHz",
        "crossing_absolute_tolerance_MHz", "crossing_relative_tolerance",
        "splitting_significance_min_ratio", "modulation_significance_min_ratio",
        "splitting_level_tolerance_MHz", "flux_energy_resolution_MHz",
    })
    convergence = CouplingConvergenceConfig(
        _positive_int(conv_raw, "cutoff_increment"), _odd(conv_raw, "crossing_refinement_coarse_points"),
        _positive_int(conv_raw, "crossing_refinement_levels"),
        *(_positive(conv_raw, key) for key in (
            "frequency_tolerance_MHz", "anharmonicity_tolerance_MHz", "zz_absolute_tolerance_MHz",
            "crossing_absolute_tolerance_MHz", "crossing_relative_tolerance",
            "splitting_significance_min_ratio", "modulation_significance_min_ratio",
            "splitting_level_tolerance_MHz", "flux_energy_resolution_MHz",
        )),
    )
    runtime_raw = _section(spectrum, "runtime", {
        "acceptance_budget_seconds", "smoke_budget_seconds", "conservative_solve_ceiling",
        "over_budget_fallback", "solver_validation_artifact", "solver_validation_approval",
    })
    _exact(runtime_raw, "over_budget_fallback", "fail")
    _exact_int(runtime_raw, "conservative_solve_ceiling", 1381)
    runtime = Stage31RuntimeConfig(
        _positive(runtime_raw, "acceptance_budget_seconds"), _positive(runtime_raw, "smoke_budget_seconds"),
        1381, "fail", Path(_string(runtime_raw, "solver_validation_artifact")),
        Path(_string(runtime_raw, "solver_validation_approval")),
    )
    config = QubitCouplingConfig(
        "0.2", "q1_q2_coupling_vs_coupler", _string(spectrum, "name"), profile, eligible,
        *(Path(_string(spectrum, key)) for key in (
            "source_hamiltonian_config", "source_hamiltonian_artifacts", "source_rebaseline_manifest",
            "source_rebaseline_approval", "source_design_freeze_manifest",
        )),
        eigen, labeling, metrics, _bool(metrics_raw, "compute_target_bare_projector"), scan, evidence,
        convergence, runtime, source,
    )
    _validate_profile_literals(config)
    validate_stage31_design_freeze(config)
    return config


def validate_stage31_design_freeze(config: QubitCouplingConfig) -> dict[str, Any]:
    path = config.source_design_freeze_manifest
    payload = json.loads(path.read_text(encoding="utf-8"))
    if path.read_bytes() != canonical_json_bytes(payload):
        raise ValueError("Stage 3.1 design freeze manifest is not canonical")
    expected = {
        "schema_version": "0.1", "artifact_type": "stage_03_1_design_freeze",
        "artifact_version": "0.1", "decision": "approved",
        "reviewer_role": "independent_design_review_ai", "blocking_findings": [],
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"Stage 3.1 design freeze {key} mismatch")
    root = find_repository_root(path)
    documents = payload.get("document_sha256")
    if not isinstance(documents, dict) or len(documents) != 3:
        raise ValueError("Stage 3.1 design freeze document bindings are incomplete")
    for relative, digest in documents.items():
        if raw_file_sha256(root / relative) != digest:
            raise ValueError(f"Stage 3.1 frozen document hash mismatch: {relative}")
    review = root / payload["review_record_path"]
    if raw_file_sha256(review) != payload.get("review_record_sha256"):
        raise ValueError("Stage 3.1 design review hash mismatch")
    return {"path": path.as_posix(), "sha256": raw_file_sha256(path), "payload": payload, "ok": True}


def q1_q2_config_to_dict(config: QubitCouplingConfig) -> dict[str, Any]:
    raw = yaml.load(config.source_path.read_text(encoding="utf-8"), Loader=_UniqueLoader)
    return raw


def _validate_profile_literals(config: QubitCouplingConfig) -> None:
    acceptance = config.profile == "acceptance"
    expected = {
        "num_states": 48 if acceptance else 12,
        "solver": "validated_eigsh" if acceptance else "dense_eigh",
        "ncv": 97 if acceptance else 25,
        "couplers": (0.2, 0.27, 0.36, 0.38, 0.385, 0.39, 0.394, 0.396, 0.4) if acceptance else (0.27,),
        "anchors": (0.2, 0.27, 0.385) if acceptance else (),
        "coarse": 25 if acceptance else 9,
        "refinement": 11 if acceptance else 7,
        "min_levels": 4 if acceptance else 1,
        "max_levels": 8 if acceptance else 1,
    }
    actual = {
        "num_states": config.eigen.num_states, "solver": config.eigen.solver, "ncv": config.eigen.eigsh.ncv,
        "couplers": config.scan.coupler_flux_points_phi0, "anchors": config.scan.acceptance_anchor_fluxes_phi0,
        "coarse": config.scan.inner_coarse_points, "refinement": config.scan.refinement_points,
        "min_levels": config.scan.min_refinement_levels, "max_levels": config.scan.max_refinement_levels,
    }
    if actual != expected:
        raise ValueError(f"{config.profile} config differs from frozen literals")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    if set(value) != keys:
        raise ValueError(f"{name} keys must be exactly {sorted(keys)}")


def _section(parent, key, keys):
    value = _mapping(parent.get(key), key)
    _exact_keys(value, keys, key)
    return value


def _exact(raw, key, expected):
    if raw.get(key) != expected or isinstance(raw.get(key), bool) != isinstance(expected, bool):
        raise ValueError(f"{key} must be {expected}")


def _string(raw, key):
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _bool(raw, key):
    value = raw.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be bool")
    return value


def _finite(raw, key):
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{key} must be finite real")
    return float(value)


def _positive(raw, key):
    value = _finite(raw, key)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _positive_int(raw, key):
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be positive integer")
    return value


def _nonnegative_int(raw, key):
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be nonnegative integer")
    return value


def _exact_int(raw, key, expected):
    if raw.get(key) != expected or isinstance(raw.get(key), bool):
        raise ValueError(f"{key} must be {expected}")


def _odd(raw, key):
    value = _positive_int(raw, key)
    if value < 3 or value % 2 == 0:
        raise ValueError(f"{key} must be odd and >=3")
    return value


def _fraction(raw, key):
    value = _finite(raw, key)
    if not 0 <= value <= 1:
        raise ValueError(f"{key} must be in [0,1]")
    return value


def _flux_list(raw, key):
    value = raw.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be list")
    result = tuple(_finite({key: item}, key) for item in value)
    canonical = tuple(canonical_flux_text(item) for item in result)
    if len(set(canonical)) != len(canonical):
        raise ValueError(f"{key} contains duplicate canonical flux")
    return result
