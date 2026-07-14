"""Strict, portable Stage 5 v0.2 configuration admission."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from sqvm.hamiltonian.provenance import find_repository_root
from sqvm.evolution.models import Stage5Config, Stage5PathAdmission


ROOT_KEYS = {"schema_version", "experiment_type", "profile", "inputs", "scenario_ids", "model", "frame", "solver", "tolerances", "publication"}
INPUT_KEYS = {"device_config", "hamiltonian_config", "stage4_control_config", "stage4_logical_schedule", "stage4_channel_registry", "user_acceptance_decision", "stage5_amendment", "phase_proxy_document"}
FORMAL_SCENARIOS = ("xy_drag", "q2_resonance_flux", "coupler_0_200", "coupler_0_270", "coupler_0_385")
SOLVER_KEYS = {"qutip_version_spec", "method", "rtol", "atol", "nsteps", "max_step_ns", "store_states", "store_final_state", "normalize_output", "progress_bar"}
TOLERANCE_KEYS = {"label_min_overlap", "lab_degeneracy_GHz", "projector_orthogonality", "norm_error", "population_bound"}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys before schema admission."""


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node, deep: bool = False):
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark, "mapping key is not hashable", key_node.start_mark) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark, f"duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def admit_stage5_config_paths(config_path: str | Path, repository_root: str | Path | None = None) -> Stage5PathAdmission:
    """Validate only config shape and contained repository-relative paths."""

    path = Path(config_path).resolve()
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(path)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load Stage 5 config: {exc}") from exc
    if not isinstance(raw, Mapping) or set(raw) != ROOT_KEYS:
        raise ValueError("Stage 5 config root keys are invalid")
    if raw.get("schema_version") != "0.2" or raw.get("experiment_type") != "qutip_time_evolution":
        raise ValueError("Stage 5 config identity is invalid")
    profile = raw.get("profile")
    if profile not in {"smoke", "formal"}:
        raise ValueError("Stage 5 profile must be smoke or formal")
    inputs = _mapping(raw.get("inputs"), INPUT_KEYS, "inputs")
    resolved = {key: _resolve_relative(value, root, f"inputs.{key}") for key, value in inputs.items()}
    scenario_ids = _scenario_ids(raw.get("scenario_ids"), profile)
    model = _mapping(raw.get("model"), {"charge_cutoffs", "convergence_charge_cutoffs", "reference_state_count", "smoke_window_start_index", "smoke_sample_count"}, "model")
    charge_cutoffs = _cutoffs(model.get("charge_cutoffs"), "model.charge_cutoffs")
    comparison = model.get("convergence_charge_cutoffs")
    convergence = None if comparison is None else _cutoffs(comparison, "model.convergence_charge_cutoffs")
    if profile == "formal" and (charge_cutoffs != (7, 7, 7) or convergence != (8, 8, 8)):
        raise ValueError("formal Stage 5 cutoffs are frozen to (7,7,7) and (8,8,8)")
    if profile == "smoke" and convergence is not None:
        raise ValueError("smoke Stage 5 config cannot request convergence cutoff")
    smoke_sample_count = model.get("smoke_sample_count")
    smoke_window_start_index = model.get("smoke_window_start_index")
    if profile == "formal" and (smoke_sample_count is not None or smoke_window_start_index is not None):
        raise ValueError("formal Stage 5 config cannot truncate its time axis")
    if profile == "smoke" and (isinstance(smoke_sample_count, bool) or not isinstance(smoke_sample_count, int) or smoke_sample_count < 1 or isinstance(smoke_window_start_index, bool) or not isinstance(smoke_window_start_index, int) or smoke_window_start_index < 0):
        raise ValueError("smoke window fields must be non-negative/positive integers")
    count = model.get("reference_state_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 16:
        raise ValueError("model.reference_state_count must be an integer of at least 16")
    frame = _mapping(raw.get("frame"), {"carrier_source", "rwa_projection"}, "frame")
    if frame != {"carrier_source": "stage4_approved_xy_metadata", "rwa_projection": "number_sector_v1"}:
        raise ValueError("Stage 5 frame contract is invalid")
    solver = _mapping(raw.get("solver"), SOLVER_KEYS, "solver")
    _validate_solver(solver)
    tolerances = _mapping(raw.get("tolerances"), TOLERANCE_KEYS, "tolerances")
    parsed_tolerances = {key: _positive_float(value, f"tolerances.{key}") for key, value in tolerances.items()}
    if parsed_tolerances["label_min_overlap"] != 0.90:
        raise ValueError("label_min_overlap is frozen to 0.90")
    publication = _mapping(raw.get("publication"), {"output_dir", "allow_existing_target"}, "publication")
    _resolve_relative(publication.get("output_dir"), root, "publication.output_dir")
    if publication.get("allow_existing_target") is not False:
        raise ValueError("publication.allow_existing_target must be false")
    config = Stage5Config(path, profile, dict(inputs), scenario_ids, charge_cutoffs, convergence, smoke_window_start_index, smoke_sample_count, count, dict(solver), parsed_tolerances, dict(publication))
    return Stage5PathAdmission(root, config, resolved)


def _mapping(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} keys are invalid")
    return dict(value)


def _resolve_relative(value: Any, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty repository-relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must stay inside the repository")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the repository") from exc
    return resolved


def _scenario_ids(value: Any, profile: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.isascii() for item in value):
        raise ValueError("scenario_ids must be a non-empty array of ASCII strings")
    result = tuple(value)
    expected = FORMAL_SCENARIOS if profile == "formal" else ("xy_drag",)
    if result != expected:
        raise ValueError(f"{profile} scenario_ids are frozen")
    return result


def _cutoffs(value: Any, label: str) -> tuple[int, int, int]:
    if not isinstance(value, list) or len(value) != 3 or any(isinstance(item, bool) or not isinstance(item, int) or item < 1 for item in value):
        raise ValueError(f"{label} must be three positive integers in q1,c,q2 order")
    return tuple(value)  # type: ignore[return-value]


def _positive_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return float(value)


def _validate_solver(value: Mapping[str, Any]) -> None:
    expected = {"qutip_version_spec": ">=5.1,<5.4", "method": "vern9", "rtol": 1e-13, "atol": 1e-15, "nsteps": 100000, "max_step_ns": 0.0025, "store_states": True, "store_final_state": True, "normalize_output": False, "progress_bar": None}
    if dict(value) != expected:
        raise ValueError("Stage 5 solver options differ from the immutable contract")
