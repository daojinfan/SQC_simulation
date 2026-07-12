"""Stage 2.1 provenance validation for Stage 3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqvm.hamiltonian import (
    raw_file_sha256,
    rebuild_stage2_low_energy_spectrum,
    require_accepted_legacy_anchor,
    stage2_model_source_tree_sha256,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root
from sqvm.spectrum.models import (
    ProvenanceReport,
    SpectrumConfig,
    Stage2GapConsistencyReport,
    Stage2RebaselineApproval,
    Stage2RebaselineManifest,
)


def load_stage2_rebaseline_manifest(path: str | Path) -> Stage2RebaselineManifest:
    source = Path(path)
    return Stage2RebaselineManifest(path=source, payload=_load_mapping(source, "Stage 2.1 manifest"))


def load_stage2_rebaseline_approval(path: str | Path) -> Stage2RebaselineApproval:
    source = Path(path)
    return Stage2RebaselineApproval(path=source, payload=_load_mapping(source, "Stage 2.1 approval"))


def validate_spectrum_provenance(
    config: SpectrumConfig,
    manifest: Stage2RebaselineManifest,
    approval: Stage2RebaselineApproval,
) -> ProvenanceReport:
    root = find_repository_root(config.source_path)
    manifest_paths = manifest.payload.get("paths")
    if not isinstance(manifest_paths, dict):
        raise ValueError("Stage 2.1 manifest paths must be a mapping")
    required = {
        "candidate_stage2_artifact",
        "device_artifacts",
        "hamiltonian_config",
        "legacy_baseline_anchor",
        "previous_stage2_artifact",
    }
    if not required.issubset(manifest_paths):
        raise ValueError("Stage 2.1 manifest paths are incomplete")
    resolved = {key: _resolve_path(value, root) for key, value in manifest_paths.items()}
    if resolved["candidate_stage2_artifact"] != config.source_hamiltonian_artifacts.resolve():
        raise ValueError("spectrum source_hamiltonian_artifacts does not match Stage 2.1 manifest")
    if resolved["hamiltonian_config"] != config.source_hamiltonian_config.resolve():
        raise ValueError("spectrum source_hamiltonian_config does not match Stage 2.1 manifest")
    if manifest.path.resolve() != config.source_rebaseline_manifest.resolve():
        raise ValueError("spectrum source_rebaseline_manifest path mismatch")
    if approval.path.resolve() != config.source_rebaseline_approval.resolve():
        raise ValueError("spectrum source_rebaseline_approval path mismatch")

    if manifest.path.read_bytes() != canonical_json_bytes(manifest.payload):
        raise ValueError("Stage 2.1 manifest bytes are not canonical")
    anchor = require_accepted_legacy_anchor(
        resolved["legacy_baseline_anchor"],
        resolved["previous_stage2_artifact"],
    )
    approval_payload = approval.payload
    expected_approval = {
        "schema_version": "0.1",
        "artifact_type": "stage_02_1_hamiltonian_rebaseline_approval",
        "artifact_version": "0.1",
        "decision": "approved",
    }
    errors: list[str] = []
    for key, expected in expected_approval.items():
        if approval_payload.get(key) != expected:
            errors.append(f"approval {key} must be {expected}")
    manifest_hash = raw_file_sha256(manifest.path)
    approval_hash = raw_file_sha256(approval.path)
    stage2_hash = raw_file_sha256(config.source_hamiltonian_artifacts)
    anchor_hash = raw_file_sha256(resolved["legacy_baseline_anchor"])
    previous_hash = raw_file_sha256(resolved["previous_stage2_artifact"])
    bindings = {
        "manifest_sha256": manifest_hash,
        "stage2_artifacts_sha256": stage2_hash,
        "legacy_baseline_anchor_sha256": anchor_hash,
        "previous_stage2_artifacts_sha256": previous_hash,
    }
    for key, actual in bindings.items():
        if approval_payload.get(key) != actual:
            errors.append(f"approval {key} does not match current bytes")
    if approval_payload.get("blocking_findings") not in ([], ()):  # JSON loader produces list.
        errors.append("approved Stage 2.1 approval contains blocking findings")

    artifact = _load_mapping(config.source_hamiltonian_artifacts, "Stage 2 artifact")
    if artifact.get("schema_version") != "0.2":
        errors.append("Stage 2 artifact schema_version must be 0.2")
    if artifact.get("artifact_type") != "stage_02_hamiltonian":
        errors.append("Stage 2 artifact type is invalid")
    if artifact.get("artifact_version") != "0.2":
        errors.append("Stage 2 artifact version must be 0.2")
    sha = manifest.payload.get("sha256", {})
    downstream_hashes = {
        "hamiltonian_config_sha256": raw_file_sha256(config.source_hamiltonian_config),
        "device_artifacts_sha256": raw_file_sha256(resolved["device_artifacts"]),
        "stage2_model_source_tree_sha256": stage2_model_source_tree_sha256(root),
        "stage2_artifacts_sha256": stage2_hash,
    }
    for key, actual in downstream_hashes.items():
        if sha.get(key) != actual:
            errors.append(f"manifest {key} does not match current bytes")
    if manifest.payload.get("previous_stage2_artifacts_sha256") != anchor["previous_stage2_artifacts_sha256"]:
        errors.append("manifest previous Stage 2 hash does not match accepted anchor")
    report = ProvenanceReport(
        ok=not errors,
        rebaseline_manifest_path=manifest.path,
        rebaseline_manifest_sha256=manifest_hash,
        rebaseline_approval_path=approval.path,
        rebaseline_approval_sha256=approval_hash,
        approval_decision=str(approval_payload.get("decision", "missing")),
        hamiltonian_config_sha256=str(sha.get("hamiltonian_config_sha256", "")),
        device_artifacts_sha256=str(sha.get("device_artifacts_sha256", "")),
        stage2_model_source_tree_sha256=str(sha.get("stage2_model_source_tree_sha256", "")),
        stage2_artifacts_sha256=stage2_hash,
        stage2_artifact_version=str(artifact.get("artifact_version", "")),
        stage2_dense_gap_consistency=None,
        errors=tuple(errors),
    )
    if not report.ok:
        raise ValueError("Stage 2.1 provenance validation failed: " + "; ".join(report.errors))
    return report


def run_stage2_dense_gap_consistency(
    config: SpectrumConfig,
    provenance: ProvenanceReport,
) -> Stage2GapConsistencyReport:
    if not provenance.ok:
        raise ValueError("Stage 2.1 provenance must pass before dense gap consistency")
    report = rebuild_stage2_low_energy_spectrum(
        config.source_hamiltonian_config,
        config.source_hamiltonian_artifacts,
    )
    return Stage2GapConsistencyReport(
        ok=report.ok,
        max_abs_difference_GHz=report.max_abs_difference_GHz,
        compared_gap_count=report.compared_gap_count,
        errors=report.errors,
    )


def _resolve_path(value: Any, root: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("manifest path values must be non-empty strings")
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _load_mapping(path: str | Path, description: str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {description}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} root must be a mapping")
    return value
