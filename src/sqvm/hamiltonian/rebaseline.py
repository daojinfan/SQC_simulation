"""Non-mutating Stage 2.1 rebuild and fail-closed rebaseline payloads."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy import linalg

from sqvm.hamiltonian.artifacts import load_device_artifacts
from sqvm.hamiltonian.builder import build_hamiltonian
from sqvm.hamiltonian.capacitance import build_ec_matrix, build_mode_capacitance_matrix, build_mode_transform
from sqvm.hamiltonian.config import load_hamiltonian_config
from sqvm.hamiltonian.junction import resolve_effective_junctions
from sqvm.hamiltonian.provenance import (
    build_stage2_artifact_provenance,
    canonical_json_bytes,
    find_repository_root,
    raw_file_sha256,
)


LOW_ENERGY_GAP_COUNT = 12
STAGE2_REBUILD_TOLERANCE_GHZ = 1e-9
_SHA256_PATTERN = re.compile(r"^[0-9A-F]{64}$")
_MANIFEST_PATH_KEYS = frozenset(
    {
        "candidate_stage2_artifact",
        "device_artifacts",
        "device_config",
        "hamiltonian_config",
        "legacy_baseline_anchor",
        "previous_stage2_artifact",
        "stage2_cli",
    }
)
_MANIFEST_SHA256_KEYS = frozenset(
    {
        "device_config_sha256",
        "device_artifacts_sha256",
        "hamiltonian_config_sha256",
        "stage2_model_source_tree_sha256",
        "legacy_baseline_anchor_sha256",
        "stage2_artifacts_sha256",
        "stage2_cli_sha256_at_rebaseline",
    }
)
_MANIFEST_MAPPING_FIELDS = (
    "old_to_new_numeric_deltas",
    "test_summary",
    "verify_device_summary",
    "verify_hamiltonian_summary",
    "determinism_checks",
)


class RebaselineGateError(ValueError):
    """Raised when a formal Stage 2.1 path is not authorized by its anchor."""


@dataclass(frozen=True, slots=True)
class Stage2RebuildConsistencyReport:
    ok: bool
    compared_gap_count: int
    tolerance_GHz: float
    rebuilt_gaps_GHz: tuple[float, ...]
    artifact_gaps_GHz: tuple[float, ...]
    absolute_differences_GHz: tuple[float, ...]
    max_abs_difference_GHz: float | None
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "compared_gap_count": self.compared_gap_count,
            "tolerance_GHz": self.tolerance_GHz,
            "rebuilt_gaps_GHz": list(self.rebuilt_gaps_GHz),
            "artifact_gaps_GHz": list(self.artifact_gaps_GHz),
            "absolute_differences_GHz": list(self.absolute_differences_GHz),
            "max_abs_difference_GHz": self.max_abs_difference_GHz,
            "errors": list(self.errors),
        }


def rebuild_stage2_low_energy_spectrum(
    config_path: str | Path,
    artifact_path: str | Path,
) -> Stage2RebuildConsistencyReport:
    """Rebuild the lowest 12 Stage 2 gaps with dense eigh and compare them."""

    config = load_hamiltonian_config(config_path)
    device_path = _resolve_project_path(config.source_device_artifacts)
    device = load_device_artifacts(device_path)
    transform = build_mode_transform(device)
    mode_capacitance = build_mode_capacitance_matrix(device, transform)
    ec_matrix = build_ec_matrix(mode_capacitance)
    junctions = resolve_effective_junctions(device)
    model = build_hamiltonian(config, ec_matrix.matrix_GHz, junctions)

    count = min(LOW_ENERGY_GAP_COUNT, model.matrix.shape[0])
    values = linalg.eigh(
        model.matrix.toarray(),
        subset_by_index=[0, count - 1],
        eigvals_only=True,
    )
    values = np.sort(values)
    rebuilt = tuple(float(value - values[0]) for value in values)

    artifact = _load_json_mapping(artifact_path, "Stage 2 artifact")
    raw_artifact_gaps = artifact.get("eigenvalue_gaps_GHz")
    errors: list[str] = []
    artifact_gaps: tuple[float, ...] = ()
    if not isinstance(raw_artifact_gaps, list) or len(raw_artifact_gaps) < count:
        errors.append(f"artifact must contain at least {count} eigenvalue_gaps_GHz values")
    else:
        try:
            artifact_gaps = tuple(float(value) for value in raw_artifact_gaps[:count])
        except (TypeError, ValueError):
            errors.append("artifact eigenvalue_gaps_GHz must contain numeric values")

    differences: tuple[float, ...] = ()
    maximum: float | None = None
    if artifact_gaps:
        differences = tuple(abs(left - right) for left, right in zip(rebuilt, artifact_gaps, strict=True))
        maximum = max(differences, default=0.0)
        if not np.isfinite(maximum):
            errors.append("artifact gap comparison is not finite")
        elif maximum > STAGE2_REBUILD_TOLERANCE_GHZ:
            errors.append(
                "lowest 12 gap rebuild exceeds "
                f"{STAGE2_REBUILD_TOLERANCE_GHZ:.1e} GHz: {maximum:.17g} GHz"
            )

    return Stage2RebuildConsistencyReport(
        ok=not errors,
        compared_gap_count=count,
        tolerance_GHz=STAGE2_REBUILD_TOLERANCE_GHZ,
        rebuilt_gaps_GHz=rebuilt,
        artifact_gaps_GHz=artifact_gaps,
        absolute_differences_GHz=differences,
        max_abs_difference_GHz=maximum,
        errors=tuple(errors),
    )


def require_accepted_legacy_anchor(
    legacy_anchor_path: str | Path,
    previous_stage2_artifact_path: str | Path | None = None,
) -> dict[str, Any]:
    """Load and validate an accepted legacy anchor, failing closed otherwise."""

    path = Path(legacy_anchor_path)
    if not path.is_file():
        raise RebaselineGateError("legacy baseline anchor is missing")
    anchor = _load_json_mapping(path, "legacy baseline anchor")
    if anchor.get("schema_version") != "0.1":
        raise RebaselineGateError("legacy baseline anchor schema_version must be 0.1")
    if anchor.get("artifact_type") != "stage_02_legacy_baseline_anchor":
        raise RebaselineGateError("legacy baseline anchor artifact_type is invalid")
    if anchor.get("artifact_version") != "0.1":
        raise RebaselineGateError("legacy baseline anchor artifact_version must be 0.1")

    decision = anchor.get("decision")
    if decision != "accepted":
        raise RebaselineGateError(f"legacy baseline anchor decision is {decision or 'missing'}, not accepted")

    previous_hash = _required_sha256(anchor, "previous_stage2_artifacts_sha256")
    expected_hash = _required_sha256(anchor, "expected_sha256")
    if previous_hash != expected_hash:
        raise RebaselineGateError("legacy baseline anchor previous and expected SHA-256 differ")
    if anchor.get("approved_by") != "user":
        raise RebaselineGateError("legacy baseline anchor must be approved_by user")
    if previous_stage2_artifact_path is not None:
        actual_hash = raw_file_sha256(previous_stage2_artifact_path)
        if actual_hash != previous_hash:
            raise RebaselineGateError("legacy Stage 2 artifact bytes do not match the accepted anchor")
    return dict(anchor)


def build_rebaseline_manifest_payload(
    *,
    legacy_anchor_path: str | Path,
    previous_stage2_artifact_path: str | Path,
    candidate_stage2_artifact_path: str | Path,
    device_config_path: str | Path,
    device_artifacts_path: str | Path,
    hamiltonian_config_path: str | Path,
    stage2_cli_path: str | Path,
    old_to_new_numeric_deltas: Mapping[str, Any],
    test_summary: Mapping[str, Any],
    verify_device_summary: Mapping[str, Any],
    verify_hamiltonian_summary: Mapping[str, Any],
    determinism_checks: Mapping[str, Any],
    repository_root: str | Path | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> dict[str, Any]:
    """Build a deterministic formal manifest payload after the anchor gate."""

    anchor = require_accepted_legacy_anchor(legacy_anchor_path, previous_stage2_artifact_path)
    candidate = _load_json_mapping(candidate_stage2_artifact_path, "candidate Stage 2 artifact")
    previous = _load_json_mapping(previous_stage2_artifact_path, "previous Stage 2 artifact")
    if candidate.get("artifact_type") != "stage_02_hamiltonian":
        raise RebaselineGateError("candidate Stage 2 artifact_type is invalid")
    if candidate.get("schema_version") != "0.2" or candidate.get("artifact_version") != "0.2":
        raise RebaselineGateError("candidate Stage 2 artifact schema_version and artifact_version must be 0.2")
    if previous.get("artifact_version") != "0.1":
        raise RebaselineGateError("previous Stage 2 artifact artifact_version must be 0.1")
    summaries = {
        "old_to_new_numeric_deltas": _validated_json_mapping(
            old_to_new_numeric_deltas, "old_to_new_numeric_deltas"
        ),
        "test_summary": _validated_json_mapping(test_summary, "test_summary"),
        "verify_device_summary": _validated_json_mapping(verify_device_summary, "verify_device_summary"),
        "verify_hamiltonian_summary": _validated_json_mapping(
            verify_hamiltonian_summary, "verify_hamiltonian_summary"
        ),
        "determinism_checks": _validated_json_mapping(determinism_checks, "determinism_checks"),
    }
    if not isinstance(git_commit, str) or not git_commit.strip():
        raise RebaselineGateError("git_commit must be a non-empty string")
    if not isinstance(git_dirty, bool):
        raise RebaselineGateError("git_dirty must be a boolean")

    root = find_repository_root(hamiltonian_config_path) if repository_root is None else Path(repository_root)
    expected_provenance = build_stage2_artifact_provenance(
        device_artifacts_path=device_artifacts_path,
        hamiltonian_config_path=hamiltonian_config_path,
        repository_root=root,
    )
    if candidate.get("provenance") != expected_provenance:
        raise RebaselineGateError("candidate Stage 2 artifact provenance does not match current bytes")

    paths = {
        "candidate_stage2_artifact": _display_path(candidate_stage2_artifact_path, root),
        "device_artifacts": _display_path(device_artifacts_path, root),
        "device_config": _display_path(device_config_path, root),
        "hamiltonian_config": _display_path(hamiltonian_config_path, root),
        "legacy_baseline_anchor": _display_path(legacy_anchor_path, root),
        "previous_stage2_artifact": _display_path(previous_stage2_artifact_path, root),
        "stage2_cli": _display_path(stage2_cli_path, root),
    }
    sha256 = {
        "device_config_sha256": raw_file_sha256(device_config_path),
        **expected_provenance,
        "legacy_baseline_anchor_sha256": raw_file_sha256(legacy_anchor_path),
        "stage2_artifacts_sha256": raw_file_sha256(candidate_stage2_artifact_path),
        "stage2_cli_sha256_at_rebaseline": raw_file_sha256(stage2_cli_path),
    }
    return {
        "schema_version": "0.1",
        "artifact_type": "stage_02_1_hamiltonian_rebaseline",
        "artifact_version": "0.1",
        "created_from_stage2_artifact_version": previous.get("artifact_version"),
        "candidate_stage2_artifact_version": "0.2",
        "previous_stage2_artifacts_sha256": anchor["previous_stage2_artifacts_sha256"],
        "legacy_baseline_anchor_path": paths["legacy_baseline_anchor"],
        "legacy_baseline_anchor_sha256": sha256["legacy_baseline_anchor_sha256"],
        "paths": paths,
        "sha256": sha256,
        **summaries,
        "git_commit": git_commit.strip(),
        "git_dirty": git_dirty,
    }


def validate_rebaseline_manifest(
    *,
    manifest_path: str | Path,
    candidate_stage2_artifact_path: str | Path,
    legacy_anchor_path: str | Path,
    previous_stage2_artifact_path: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a canonical manifest by rebuilding it from its bound inputs."""

    manifest_file = Path(manifest_path)
    manifest = _load_json_mapping(manifest_file, "rebaseline manifest")
    _require_manifest_value(manifest, "schema_version", "0.1")
    _require_manifest_value(manifest, "artifact_type", "stage_02_1_hamiltonian_rebaseline")
    _require_manifest_value(manifest, "artifact_version", "0.1")
    _require_manifest_value(manifest, "candidate_stage2_artifact_version", "0.2")

    root = find_repository_root(manifest_file) if repository_root is None else Path(repository_root).resolve()
    paths = _required_exact_mapping(manifest, "paths", _MANIFEST_PATH_KEYS)
    resolved_paths = {
        key: _resolve_manifest_path(value, root, key)
        for key, value in paths.items()
    }
    explicit_paths = {
        "candidate_stage2_artifact": Path(candidate_stage2_artifact_path).resolve(),
        "legacy_baseline_anchor": Path(legacy_anchor_path).resolve(),
        "previous_stage2_artifact": Path(previous_stage2_artifact_path).resolve(),
    }
    for key, explicit in explicit_paths.items():
        if resolved_paths[key] != explicit:
            raise RebaselineGateError(f"manifest paths.{key} does not match the explicit approval path")

    sha256 = _required_exact_mapping(manifest, "sha256", _MANIFEST_SHA256_KEYS)
    for key, value in sha256.items():
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise RebaselineGateError(f"manifest sha256.{key} must be an uppercase SHA-256")

    candidate = _load_json_mapping(resolved_paths["candidate_stage2_artifact"], "candidate Stage 2 artifact")
    _require_manifest_value(candidate, "schema_version", "0.2", description="candidate Stage 2 artifact")
    _require_manifest_value(
        candidate,
        "artifact_type",
        "stage_02_hamiltonian",
        description="candidate Stage 2 artifact",
    )
    _require_manifest_value(candidate, "artifact_version", "0.2", description="candidate Stage 2 artifact")

    anchor = require_accepted_legacy_anchor(
        resolved_paths["legacy_baseline_anchor"],
        resolved_paths["previous_stage2_artifact"],
    )
    previous = _load_json_mapping(resolved_paths["previous_stage2_artifact"], "previous Stage 2 artifact")
    if previous.get("artifact_version") != "0.1":
        raise RebaselineGateError("previous Stage 2 artifact artifact_version must be 0.1")
    if manifest.get("created_from_stage2_artifact_version") != previous["artifact_version"]:
        raise RebaselineGateError("manifest created_from_stage2_artifact_version does not match previous artifact")
    if manifest.get("previous_stage2_artifacts_sha256") != anchor["previous_stage2_artifacts_sha256"]:
        raise RebaselineGateError("manifest previous Stage 2 SHA-256 does not match accepted anchor")
    actual_anchor_hash = raw_file_sha256(resolved_paths["legacy_baseline_anchor"])
    if manifest.get("legacy_baseline_anchor_sha256") != actual_anchor_hash:
        raise RebaselineGateError("manifest legacy anchor SHA-256 does not match anchor bytes")
    if manifest.get("legacy_baseline_anchor_path") != paths["legacy_baseline_anchor"]:
        raise RebaselineGateError("manifest legacy_baseline_anchor_path does not match paths mapping")

    summaries = {
        field: _required_mapping(manifest, field)
        for field in _MANIFEST_MAPPING_FIELDS
    }
    git_commit = manifest.get("git_commit")
    if not isinstance(git_commit, str) or not git_commit.strip():
        raise RebaselineGateError("manifest git_commit must be a non-empty string")
    git_dirty = manifest.get("git_dirty")
    if not isinstance(git_dirty, bool):
        raise RebaselineGateError("manifest git_dirty must be a boolean")

    expected = build_rebaseline_manifest_payload(
        legacy_anchor_path=resolved_paths["legacy_baseline_anchor"],
        previous_stage2_artifact_path=resolved_paths["previous_stage2_artifact"],
        candidate_stage2_artifact_path=resolved_paths["candidate_stage2_artifact"],
        device_config_path=resolved_paths["device_config"],
        device_artifacts_path=resolved_paths["device_artifacts"],
        hamiltonian_config_path=resolved_paths["hamiltonian_config"],
        stage2_cli_path=resolved_paths["stage2_cli"],
        repository_root=root,
        git_commit=git_commit,
        git_dirty=git_dirty,
        **summaries,
    )
    if manifest != expected:
        raise RebaselineGateError("rebaseline manifest does not equal the payload rebuilt from current inputs")
    if manifest_file.read_bytes() != canonical_json_bytes(expected):
        raise RebaselineGateError("rebaseline manifest bytes are not canonical")
    return expected


def build_rebaseline_approval_payload(
    *,
    decision: str,
    manifest_path: str | Path,
    candidate_stage2_artifact_path: str | Path,
    legacy_anchor_path: str | Path,
    previous_stage2_artifact_path: str | Path,
    review_record_path: str | Path,
    blocking_findings: Sequence[str],
) -> dict[str, Any]:
    """Build an approval payload only when all external hash bindings are valid."""

    manifest = validate_rebaseline_manifest(
        manifest_path=manifest_path,
        candidate_stage2_artifact_path=candidate_stage2_artifact_path,
        legacy_anchor_path=legacy_anchor_path,
        previous_stage2_artifact_path=previous_stage2_artifact_path,
    )
    if decision not in {"approved", "rejected"}:
        raise RebaselineGateError("approval decision must be approved or rejected")
    findings = tuple(str(item) for item in blocking_findings)
    if decision == "approved" and findings:
        raise RebaselineGateError("an approved rebaseline cannot contain blocking findings")

    anchor = require_accepted_legacy_anchor(legacy_anchor_path, previous_stage2_artifact_path)
    candidate_hash = raw_file_sha256(candidate_stage2_artifact_path)
    anchor_hash = raw_file_sha256(legacy_anchor_path)

    return {
        "schema_version": "0.1",
        "artifact_type": "stage_02_1_hamiltonian_rebaseline_approval",
        "artifact_version": "0.1",
        "decision": decision,
        "manifest_sha256": raw_file_sha256(manifest_path),
        "stage2_artifacts_sha256": candidate_hash,
        "legacy_baseline_anchor_sha256": anchor_hash,
        "previous_stage2_artifacts_sha256": anchor["previous_stage2_artifacts_sha256"],
        "review_record_path": Path(review_record_path).as_posix(),
        "reviewer_role": "independent_test_review_ai",
        "blocking_findings": list(findings),
    }


def serialize_rebaseline_payload(payload: Mapping[str, Any]) -> bytes:
    """Return deterministic bytes for a manifest, anchor fixture, or approval."""

    return canonical_json_bytes(payload)


def _load_json_mapping(path: str | Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise RebaselineGateError(f"cannot read {description}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RebaselineGateError(f"invalid {description} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RebaselineGateError(f"{description} root must be a mapping")
    return value


def _required_sha256(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value.upper()):
        raise RebaselineGateError(f"legacy baseline anchor {field} must be a SHA-256")
    return value.upper()


def _validated_json_mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RebaselineGateError(f"{field} must be a mapping")
    copied = json.loads(canonical_json_bytes(value))
    if not isinstance(copied, dict):
        raise RebaselineGateError(f"{field} must serialize as a JSON mapping")
    return copied


def _required_mapping(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise RebaselineGateError(f"manifest {field} must be a mapping")
    return value


def _required_exact_mapping(
    payload: Mapping[str, Any],
    field: str,
    expected_keys: frozenset[str],
) -> dict[str, Any]:
    value = _required_mapping(payload, field)
    keys = set(value)
    if keys != expected_keys:
        missing = sorted(expected_keys - keys)
        extra = sorted(keys - expected_keys)
        raise RebaselineGateError(f"manifest {field} keys are invalid; missing={missing}, extra={extra}")
    return value


def _require_manifest_value(
    payload: Mapping[str, Any],
    field: str,
    expected: str,
    *,
    description: str = "rebaseline manifest",
) -> None:
    if payload.get(field) != expected:
        raise RebaselineGateError(f"{description} {field} must be {expected}")


def _resolve_manifest_path(value: Any, root: Path, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise RebaselineGateError(f"manifest paths.{field} must be a non-empty string")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _display_path(path: str | Path, root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path
