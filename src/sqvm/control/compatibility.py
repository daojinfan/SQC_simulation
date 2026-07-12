"""Stage 4.0 compatibility, manifest, and independent approval gates."""

from __future__ import annotations

import json
import importlib.metadata
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime
from typing import Any, Mapping

import nbformat as nbf

from sqvm.control.models import (
    ControlChannelCompatibilityReport,
    ControlChannelReadinessReport,
    ControlChannelRegistry,
)
from sqvm.control.registry import ADDED_CHANNEL_ORDER, BASE_CHANNEL_ORDER, CHANNEL_ORDER, load_control_channel_registry
from sqvm.control.upstream import validate_frozen_stage4_upstream_receipt
from sqvm.device import load_device
from sqvm.hamiltonian import raw_file_sha256
from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root


PATH_KEYS = (
    "stage4_design_freeze_manifest", "base_device_config", "base_device_artifact",
    "stage2_artifact", "stage2_1_manifest", "stage2_1_approval", "stage3_1_artifact",
    "stage3_1_report", "stage3_1_approval", "channel_registry",
)
DEFAULT_PATHS = {
    "stage4_design_freeze_manifest": "docs/decisions/2026-07-11-stage4-design-freeze.json",
    "base_device_config": "configs/devices/2q1c2r.yaml",
    "base_device_artifact": "output/stage_01_device_model/device_artifacts.json",
    "stage2_artifact": "output/stage_02_hamiltonian/hamiltonian_artifacts.json",
    "stage2_1_manifest": "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json",
    "stage2_1_approval": "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json",
    "stage3_1_artifact": "output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json",
    "stage3_1_report": "output/stage_03_1_q1_q2_coupling/verification_report.json",
    "stage3_1_approval": "output/stage_03_1_q1_q2_coupling/acceptance_approval.json",
    "channel_registry": "configs/control/2q1c2r_channels.yaml",
}
MANIFEST_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "paths", "sha256",
    "base_channels", "added_channels", "merged_channels", "checks", "blocking_reasons",
    "compatibility_candidate_ready",
}
REPORT_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "execution_succeeded",
    "compatibility_candidate_ready", "control_channel_ready", "approval_status", "manifest_path",
    "manifest_sha256", "notebook_path", "notebook_sha256", "checks", "blocking_reasons",
}
APPROVAL_HASH_KEYS = (
    "stage4_design_freeze_manifest_sha256", "channel_registry_sha256",
    "control_channel_manifest_sha256", "verification_notebook_sha256",
    "verification_report_sha256", "review_record_sha256",
)
APPROVAL_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role",
    "blocking_findings", *APPROVAL_HASH_KEYS, "review_record_path",
}
CHECK_NAMES = (
    "stage4_design_freeze_valid", "base_device_config_matches_stage1_artifact",
    "stage2_1_approval_valid", "stage3_1_readiness_valid", "base_channel_subset_exact",
    "only_q1_z_q2_z_added", "targets_and_kinds_valid", "ports_unique", "awg_lanes_unique",
    "merged_registry_canonical",
)
READINESS_CHECK_NAMES = (
    "design_freeze_hash_matches", "registry_hash_matches", "manifest_hash_matches",
    "notebook_hash_matches", "report_hash_matches", "review_hash_matches",
    "approval_contract_valid",
)
FREEZE_DOC_HASHES = {
    "docs/decisions/2026-07-11-stage4-control-signal-scope.md": "602D3E6DA61219284AE6BE58E74BF9681977D563C7F524A768DE802FA8941765",
    "docs/designs/04_control_signal_design.md": "56F5ED6CA1A10634F4D2D9D87507C056E5FC26CF9F875D30D1BC879C52FFA2DB",
    "docs/stages/04_0_control_channel_rebaseline_plan.md": "FD8A18D3FA28DA98C2FC2CAA271FC6C38F83B80521CBAC54E568FA35D4D3D1B6",
    "docs/stages/04_control_signal_plan.md": "166DB399B0F875C435B9E635CDC894A83447487D422371C36B7D942280640659",
}
FREEZE_REVIEW_PATH = "docs/decisions/2026-07-11-stage4-design-freeze-review.md"
FREEZE_REVIEW_SHA256 = "FBE6C28CAEEA8179389F8E67C6626B66A7CA2EC95DA66059C5AF23D8A6469BB9"
SHA256 = re.compile(r"^[0-9A-F]{64}$")
APPROVED_INTERPRETER = Path(r"C:\Users\fandaojin\anaconda3\python.exe")
NBCLIENT_VERSION = "0.10.2"
NOTEBOOK_CELL_IDS = (
    "f23b4a12", "a4a80a8c", "0d7a14c9", "1363ecab", "9627b402",
    "bf587910", "6f9510ba", "73c1ca22", "921d67a9", "db82b0b1",
)
EXECUTION_METADATA_KEYS = {
    "iopub.execute_input", "iopub.status.busy", "iopub.status.idle", "shell.execute_reply",
}


def validate_control_channel_compatibility(
    registry: ControlChannelRegistry,
    *,
    repository_root: str | Path | None = None,
    paths: Mapping[str, str | Path] | None = None,
) -> ControlChannelCompatibilityReport:
    """Validate the exact registry merge and every accepted upstream readiness gate."""

    if not isinstance(registry, ControlChannelRegistry):
        raise TypeError("registry must be a ControlChannelRegistry")
    root = _root(registry.source_path, repository_root)
    resolved = _resolved_paths(root, paths, registry.source_path)
    results: dict[str, tuple[bool, str]] = {}
    device = None
    artifact: dict[str, Any] = {}

    try:
        _validate_freeze(resolved["stage4_design_freeze_manifest"], root)
        results[CHECK_NAMES[0]] = (True, "Stage 4 design freeze manifest and bindings are valid")
    except ValueError as exc:
        results[CHECK_NAMES[0]] = (False, str(exc))

    try:
        device = load_device(resolved["base_device_config"])
        artifact = _load_json(resolved["base_device_artifact"], "Stage 1 device artifact")
        if artifact.get("artifact_type") != "stage_01_device_model" or artifact.get("artifact_version") != "0.1":
            raise ValueError("Stage 1 device artifact identity is invalid")
        expected = {name: _base_row(channel) for name, channel in device.channels.items()}
        if artifact.get("channels") != expected:
            raise ValueError("Stage 1 artifact channels do not equal the current device config")
        if artifact.get("device_summary", {}).get("name") != device.name:
            raise ValueError("Stage 1 artifact device name does not match the current config")
        if artifact.get("validation", {}).get("ok") is not True:
            raise ValueError("Stage 1 device artifact validation is not ready")
        results[CHECK_NAMES[1]] = (True, "Stage 1 config and accepted artifact agree")
    except (ValueError, OSError) as exc:
        results[CHECK_NAMES[1]] = (False, str(exc))

    receipt = validate_frozen_stage4_upstream_receipt(
        repository_root=root,
        stage2_artifact_path=resolved["stage2_artifact"],
        stage2_1_manifest_path=resolved["stage2_1_manifest"],
        stage2_1_approval_path=resolved["stage2_1_approval"],
        stage3_1_artifact_path=resolved["stage3_1_artifact"],
        stage3_1_report_path=resolved["stage3_1_report"],
        stage3_1_approval_path=resolved["stage3_1_approval"],
    )
    if receipt.ok and receipt.stage2_1_ready:
        results[CHECK_NAMES[2]] = (True, "Stage 2.1 manifest and approval validate from current bytes")
    else:
        results[CHECK_NAMES[2]] = (False, receipt.errors[0] if receipt.errors else "Stage 2.1 frozen receipt validation failed")
    if receipt.ok and receipt.stage3_1_ready:
        results[CHECK_NAMES[3]] = (True, "Stage 3.1 readiness approval validates from current bytes")
    else:
        results[CHECK_NAMES[3]] = (False, receipt.errors[0] if receipt.errors else "Stage 3.1 frozen receipt validation failed")

    channel_map = registry.channel_map()
    base = {name: _base_row(channel_map[name]) for name in BASE_CHANNEL_ORDER}
    added = {name: channel_map[name].to_dict() for name in ADDED_CHANNEL_ORDER}
    merged = {
        name: {**channel_map[name].to_dict(), "origin": "stage1_base" if name in BASE_CHANNEL_ORDER else "stage4_extension"}
        for name in CHANNEL_ORDER
    }
    actual_base = artifact.get("channels", {}) if artifact else {}
    base_ok = set(actual_base) == set(BASE_CHANNEL_ORDER) and all(actual_base.get(name) == base[name] for name in BASE_CHANNEL_ORDER)
    results[CHECK_NAMES[4]] = (base_ok, "All five Stage 1 base channels are preserved exactly" if base_ok else "base channel subset differs from Stage 1")

    additions_ok = set(channel_map) - set(actual_base) == set(ADDED_CHANNEL_ORDER)
    results[CHECK_NAMES[5]] = (additions_ok, "Only q1_z and q2_z are added" if additions_ok else "registry additions are not exactly q1_z and q2_z")

    targets_ok = device is not None and _targets_valid(channel_map, device.components)
    results[CHECK_NAMES[6]] = (targets_ok, "All targets and channel kinds are compatible" if targets_ok else "one or more targets or kinds are invalid")
    ports = [channel.port for channel in registry.channels]
    ports_ok = len(ports) == len(set(ports))
    results[CHECK_NAMES[7]] = (ports_ok, "Channel ports are globally unique" if ports_ok else "channel ports are not unique")
    lanes = [lane for channel in registry.channels for lane in channel.awg_lanes]
    lanes_ok = len(lanes) == len(set(lanes))
    results[CHECK_NAMES[8]] = (lanes_ok, "AWG lanes are globally unique" if lanes_ok else "AWG lanes are not unique")
    canonical_ok = tuple(channel.name for channel in registry.channels) == CHANNEL_ORDER
    results[CHECK_NAMES[9]] = (canonical_ok, "Merged registry uses the frozen canonical order" if canonical_ok else "merged registry order is not canonical")

    checks = tuple(_check(name, *results[name]) for name in CHECK_NAMES)
    blockers = tuple(row["message"] for row in checks if not row["passed"])
    ready = not blockers
    return ControlChannelCompatibilityReport(ready, ready, base, added, merged, checks, blockers)


def build_control_channel_manifest(
    registry_path: str | Path = DEFAULT_PATHS["channel_registry"],
    *,
    repository_root: str | Path | None = None,
    paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    registry = load_control_channel_registry(registry_path)
    root = _root(registry.source_path, repository_root)
    resolved = _resolved_paths(root, paths, registry.source_path)
    report = validate_control_channel_compatibility(registry, repository_root=root, paths=resolved)
    path_rows = {key: _repo_relative(resolved[key], root) for key in PATH_KEYS}
    sha = {f"{key}_sha256": raw_file_sha256(resolved[key]) for key in PATH_KEYS}
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_04_0_control_channel_compatibility",
        "artifact_version": "0.1",
        "paths": path_rows,
        "sha256": sha,
        "base_channels": report.base_channels,
        "added_channels": report.added_channels,
        "merged_channels": report.merged_channels,
        "checks": list(report.checks),
        "blocking_reasons": list(report.blocking_reasons),
        "compatibility_candidate_ready": report.compatibility_candidate_ready,
    }
    _assert_finite(payload)
    return payload


def validate_control_channel_manifest(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
    paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    source = Path(path)
    root = _root(source, repository_root)
    payload = _load_canonical(source, "control channel manifest")
    if set(payload) != MANIFEST_KEYS:
        raise ValueError("control channel manifest fields are not exact")
    _identity(payload, "stage_04_0_control_channel_compatibility")
    raw_paths = _exact_mapping(payload.get("paths"), set(PATH_KEYS), "manifest paths")
    raw_sha = _exact_mapping(payload.get("sha256"), {f"{key}_sha256" for key in PATH_KEYS}, "manifest sha256")
    resolved = {key: _resolve_repo_path(raw_paths[key], root, f"paths.{key}") for key in PATH_KEYS}
    if paths is not None:
        explicit = _resolved_paths(root, paths, resolved["channel_registry"])
        for key in PATH_KEYS:
            if resolved[key] != explicit[key]:
                raise ValueError(f"manifest paths.{key} does not match the explicit path")
    for key in PATH_KEYS:
        value = raw_sha[f"{key}_sha256"]
        if not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ValueError(f"manifest sha256.{key}_sha256 is invalid")
        if value != raw_file_sha256(resolved[key]):
            raise ValueError(f"manifest sha256.{key}_sha256 does not match current bytes")
    expected = build_control_channel_manifest(resolved["channel_registry"], repository_root=root, paths=resolved)
    if payload != expected:
        raise ValueError("control channel manifest does not equal the payload rebuilt from current bytes")
    return expected


def build_control_channel_approval(
    output_dir: str | Path,
    review_record_path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    directory = Path(output_dir)
    root = _root(directory, repository_root)
    _require_file_set(directory, {"control_channel_manifest.json", "verification.ipynb", "verification_report.json"})
    manifest_path = directory / "control_channel_manifest.json"
    notebook_path = directory / "verification.ipynb"
    report_path = directory / "verification_report.json"
    manifest = validate_control_channel_manifest(manifest_path, repository_root=root)
    _validate_notebook(notebook_path)
    _validate_report(report_path, manifest_path, notebook_path, manifest, root)
    review = Path(review_record_path)
    if not review.is_absolute():
        review = root / review
    review = review.resolve()
    if directory.resolve() in review.parents:
        raise ValueError("review record must be outside the Stage 4.0 output directory")
    payload = {
        "schema_version": "0.1",
        "artifact_type": "stage_04_0_control_channel_approval",
        "artifact_version": "0.1",
        "decision": "approved",
        "reviewer_role": "independent_test_review_ai",
        "blocking_findings": [],
        "stage4_design_freeze_manifest_sha256": manifest["sha256"]["stage4_design_freeze_manifest_sha256"],
        "channel_registry_sha256": manifest["sha256"]["channel_registry_sha256"],
        "control_channel_manifest_sha256": raw_file_sha256(manifest_path),
        "verification_notebook_sha256": raw_file_sha256(notebook_path),
        "verification_report_sha256": raw_file_sha256(report_path),
        "review_record_path": _repo_relative(review, root),
        "review_record_sha256": raw_file_sha256(review),
    }
    _assert_finite(payload)
    return payload


def validate_control_channel_approval(
    path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> ControlChannelReadinessReport:
    approval_path = Path(path)
    directory = approval_path.parent
    root = _root(approval_path, repository_root)
    errors: list[str] = []
    approval: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    contract_valid = True
    try:
        _require_file_set(directory, {"control_channel_manifest.json", "verification.ipynb", "verification_report.json", "control_channel_approval.json"})
        if approval_path.resolve() != (directory / "control_channel_approval.json").resolve():
            raise ValueError("approval path must be the exact lifecycle approval file")
        approval = _load_canonical(approval_path, "control channel approval")
        if set(approval) != APPROVAL_KEYS:
            raise ValueError("control channel approval fields are not exact")
        _identity(approval, "stage_04_0_control_channel_approval")
        if approval.get("decision") != "approved" or approval.get("reviewer_role") != "independent_test_review_ai" or approval.get("blocking_findings") != []:
            raise ValueError("control channel approval identity is invalid")
        manifest = validate_control_channel_manifest(directory / "control_channel_manifest.json", repository_root=root)
        _validate_notebook(directory / "verification.ipynb")
        _validate_report(directory / "verification_report.json", directory / "control_channel_manifest.json", directory / "verification.ipynb", manifest, root)
    except (ValueError, OSError) as exc:
        errors.append(str(exc))
        contract_valid = False

    actual: dict[str, str] = {key: "" for key in APPROVAL_HASH_KEYS}
    sources = {
        "stage4_design_freeze_manifest_sha256": root / DEFAULT_PATHS["stage4_design_freeze_manifest"],
        "channel_registry_sha256": root / DEFAULT_PATHS["channel_registry"],
        "control_channel_manifest_sha256": directory / "control_channel_manifest.json",
        "verification_notebook_sha256": directory / "verification.ipynb",
        "verification_report_sha256": directory / "verification_report.json",
    }
    review_value = approval.get("review_record_path")
    review = None
    if isinstance(review_value, str) and review_value:
        try:
            review = _resolve_repo_path(review_value, root, "review_record_path")
            sources["review_record_sha256"] = review
        except ValueError as exc:
            errors.append(str(exc)); contract_valid = False
    else:
        errors.append("review_record_path must be non-empty"); contract_valid = False

    passed_by_name: dict[str, bool] = {}
    for name, key in zip(READINESS_CHECK_NAMES[:6], APPROVAL_HASH_KEYS, strict=True):
        source = sources.get(key)
        passed = False
        if source is not None:
            try:
                actual[key] = raw_file_sha256(source)
                passed = approval.get(key) == actual[key]
            except OSError:
                passed = False
        passed_by_name[name] = passed
    passed_by_name[READINESS_CHECK_NAMES[6]] = contract_valid
    checks = tuple(_check(name, passed_by_name[name], f"{name} {'passed' if passed_by_name[name] else 'failed'}") for name in READINESS_CHECK_NAMES)
    blockers = tuple(dict.fromkeys([*errors, *(row["message"] for row in checks if not row["passed"])]))
    candidate_ready = bool(manifest.get("compatibility_candidate_ready")) and not manifest.get("blocking_reasons") if manifest else False
    approval_valid = contract_valid and all(row["passed"] for row in checks)
    ok = candidate_ready and approval_valid and not blockers
    return ControlChannelReadinessReport(ok, ok, str(approval.get("decision", "missing")), approval_valid, actual, checks, blockers)


def validate_verification_candidate(output_dir: str | Path, *, repository_root: str | Path | None = None) -> dict[str, Any]:
    directory = Path(output_dir)
    root = _root(directory, repository_root)
    _require_file_set(directory, {"control_channel_manifest.json", "verification.ipynb", "verification_report.json"})
    manifest = validate_control_channel_manifest(directory / "control_channel_manifest.json", repository_root=root)
    _validate_notebook(directory / "verification.ipynb")
    return _validate_report(directory / "verification_report.json", directory / "control_channel_manifest.json", directory / "verification.ipynb", manifest, root)


def _validate_freeze(path: Path, root: Path) -> None:
    payload = _load_canonical(path, "Stage 4 design freeze manifest")
    expected_keys = {"schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role", "blocking_findings", "document_sha256", "review_record_path", "review_record_sha256"}
    if set(payload) != expected_keys:
        raise ValueError("Stage 4 design freeze fields are not exact")
    _identity(payload, "stage_04_control_signal_design_freeze")
    if payload.get("decision") != "approved" or payload.get("reviewer_role") != "independent_design_review_ai" or payload.get("blocking_findings") != []:
        raise ValueError("Stage 4 design freeze is not independently approved")
    if payload.get("document_sha256") != FREEZE_DOC_HASHES:
        raise ValueError("Stage 4 design freeze document bindings are not exact")
    if payload.get("review_record_path") != FREEZE_REVIEW_PATH or payload.get("review_record_sha256") != FREEZE_REVIEW_SHA256:
        raise ValueError("Stage 4 design freeze review binding is invalid")
    for relative, expected in {**FREEZE_DOC_HASHES, FREEZE_REVIEW_PATH: FREEZE_REVIEW_SHA256}.items():
        if raw_file_sha256(root / relative) != expected:
            raise ValueError(f"Stage 4 frozen file hash mismatch: {relative}")


def _targets_valid(channels, components) -> bool:
    for channel in channels.values():
        component = components.get(channel.target)
        if component is None:
            return False
        if channel.kind in {"xy", "z"} and component.squid is None:
            return False
        if channel.kind == "readout" and component.role != "readout":
            return False
        if channel.kind == "xy" and component.role != "qubit":
            return False
    return True


def _resolved_paths(root: Path, overrides: Mapping[str, str | Path] | None, registry_path: Path) -> dict[str, Path]:
    values: dict[str, str | Path] = dict(DEFAULT_PATHS)
    if overrides is not None:
        unknown = set(overrides) - set(PATH_KEYS)
        if unknown:
            raise ValueError("path override fields are not exact")
        values.update(overrides)
    values["channel_registry"] = registry_path
    return {key: (Path(value) if Path(value).is_absolute() else root / Path(value)).resolve() for key, value in values.items()}


def _root(source: Path, repository_root: str | Path | None) -> Path:
    return Path(repository_root).resolve() if repository_root is not None else find_repository_root(source)


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"path must be inside repository root: {path}") from exc


def _resolve_repo_path(value: Any, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or Path(value).is_absolute():
        raise ValueError(f"{label} must be repository-relative POSIX text")
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the repository root") from exc
    return resolved


def _base_row(channel) -> dict[str, str]:
    return {"kind": channel.kind, "target": channel.target, "port": channel.port}


def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "message": message or name}


def _identity(payload: Mapping[str, Any], artifact_type: str) -> None:
    expected = {"schema_version": "0.1", "artifact_type": artifact_type, "artifact_version": "0.1"}
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"{artifact_type} {key} mismatch")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a mapping")
    _assert_finite(payload)
    return payload


def _load_canonical(path: Path, label: str) -> dict[str, Any]:
    payload = _load_json(path, label)
    if path.read_bytes() != canonical_json_bytes(payload):
        raise ValueError(f"{label} bytes are not canonical")
    return payload


def _exact_mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} fields are not exact")
    return value


def _assert_finite(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON value at {path}")
        return
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            _assert_finite(value[key], f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_finite(item, f"{path}[{index}]")
        return
    raise ValueError(f"unsupported JSON value at {path}: {type(value).__name__}")


def _require_file_set(directory: Path, expected: set[str]) -> None:
    if not directory.is_dir():
        raise ValueError("control channel lifecycle directory is missing")
    entries = list(directory.iterdir())
    if any(not entry.is_file() for entry in entries) or {entry.name for entry in entries} != expected:
        raise ValueError(f"control channel lifecycle file set must be exactly {sorted(expected)}")


def _validate_notebook(path: Path) -> None:
    notebook_client = _preflight_notebook_runtime()
    try:
        notebook = nbf.read(path, as_version=4)
    except Exception as exc:
        raise ValueError(f"cannot load verification notebook: {exc}") from exc
    _validate_executed_notebook(notebook, "candidate notebook")
    manifest_path = path.with_name("control_channel_manifest.json")
    manifest_raw = manifest_path.read_bytes()
    manifest = _load_canonical(manifest_path, "control channel manifest for notebook replay")
    if manifest_raw != canonical_json_bytes(manifest):
        raise ValueError("notebook replay manifest bytes are not canonical")
    root = find_repository_root(path)
    try:
        with tempfile.TemporaryDirectory(prefix="sqvm_stage40_notebook_replay_") as name:
            replay_dir = Path(name).resolve()
            try:
                replay_dir.relative_to(root.resolve())
            except ValueError:
                pass
            else:
                raise ValueError("notebook replay directory must be outside the repository")
            replay_manifest = replay_dir / "control_channel_manifest.json"
            replay_manifest.write_bytes(manifest_raw)
            if {entry.name for entry in replay_dir.iterdir()} != {"control_channel_manifest.json"}:
                raise ValueError("notebook replay manifest must be the sole input file")
            replay = _execute_notebook(_new_notebook_from_specification(), replay_dir, notebook_client)
            _validate_executed_notebook(replay, "replay notebook")
            if notebook.metadata["language_info"] != replay.metadata["language_info"]:
                raise ValueError("candidate notebook language_info does not match normative replay")
            if _normalized_notebook(notebook) != _normalized_notebook(replay):
                raise ValueError("candidate notebook does not equal the normative replay")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"notebook normative replay failed: {exc}") from exc


def _validate_executed_notebook(notebook, label: str) -> None:
    specification = notebook_cell_specification()
    if len(notebook.cells) != len(specification):
        raise ValueError(f"{label} cell count is not exact")
    for index, (cell, expected) in enumerate(zip(notebook.cells, specification, strict=True)):
        expected_id, expected_type, expected_source = expected
        if cell.id != expected_id or cell.cell_type != expected_type or cell.source != expected_source:
            raise ValueError(f"{label} cell {index} does not match the canonical read-only template")
        if expected_type == "markdown" and dict(cell.metadata) != {}:
            raise ValueError(f"{label} markdown cell {index} metadata is not exact")
        if expected_type == "code" and set(cell.metadata) != {"execution"}:
            raise ValueError(f"{label} code cell {index} execution metadata is not exact")
    if set(notebook.metadata) != {"language_info", "stage4_0_read_only"} or notebook.metadata.get("stage4_0_read_only") is not True:
        raise ValueError(f"{label} metadata is not exact")
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    counts = [cell.execution_count for cell in code]
    if counts != list(range(1, len(code) + 1)):
        raise ValueError(f"{label} code cells are not sequentially executed")
    previous_busy = None
    for count, cell in enumerate(code, start=1):
        execution = cell.metadata["execution"]
        if not isinstance(execution, Mapping) or set(execution) != EXECUTION_METADATA_KEYS:
            raise ValueError(f"{label} execution metadata schema is not exact")
        timestamps = {key: _utc_timestamp(execution[key], f"{label} {key}") for key in EXECUTION_METADATA_KEYS}
        if not (
            timestamps["iopub.status.busy"] <= timestamps["iopub.execute_input"]
            <= timestamps["shell.execute_reply"] <= timestamps["iopub.status.idle"]
        ):
            raise ValueError(f"{label} execution timestamp order is invalid")
        if previous_busy is not None and timestamps["iopub.status.busy"] < previous_busy:
            raise ValueError(f"{label} busy timestamps are not nondecreasing")
        previous_busy = timestamps["iopub.status.busy"]
        outputs = cell.get("outputs", [])
        if len(outputs) != 1:
            raise ValueError(f"{label} code cell must have exactly one output")
        output = outputs[0]
        if set(output) != {"output_type", "metadata", "data", "execution_count"}:
            raise ValueError(f"{label} execute_result fields are not exact")
        if output.get("output_type") != "execute_result" or output.get("execution_count") != count:
            raise ValueError(f"{label} execute_result identity is invalid")
        if dict(output.get("metadata", {})) != {}:
            raise ValueError(f"{label} execute_result metadata must be empty")
        data = output.get("data")
        if not isinstance(data, Mapping) or set(data) != {"text/plain"} or not isinstance(data["text/plain"], str):
            raise ValueError(f"{label} execute_result MIME data is not exact")


def _utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"{label} must be UTC")
    return parsed


def _normalized_notebook(notebook) -> dict[str, Any]:
    payload = json.loads(nbf.writes(notebook, version=4))
    for cell in payload["cells"]:
        if cell["cell_type"] == "code":
            del cell["metadata"]["execution"]
    return payload


def _preflight_notebook_runtime():
    if not _windows_path_equal(Path(sys.executable), APPROVED_INTERPRETER):
        raise ValueError("Stage 4 notebook runtime prerequisite failed: host interpreter is not approved")
    try:
        version = importlib.metadata.version("nbclient")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("Stage 4 notebook runtime prerequisite failed: nbclient metadata is missing") from exc
    except Exception as exc:
        raise ValueError("Stage 4 notebook runtime prerequisite failed: nbclient metadata lookup failed") from exc
    if version != NBCLIENT_VERSION:
        raise ValueError(f"Stage 4 notebook runtime prerequisite failed: nbclient version must be {NBCLIENT_VERSION}")
    try:
        from nbclient import NotebookClient
    except Exception as exc:
        raise ValueError("Stage 4 notebook runtime prerequisite failed: nbclient import failed") from exc
    try:
        from jupyter_client.kernelspec import KernelSpecManager
        argv = KernelSpecManager().get_kernel_spec("python3").argv
    except Exception as exc:
        raise ValueError("Stage 4 notebook runtime prerequisite failed: python3 kernelspec is unavailable") from exc
    if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv) or len(argv) != 6:
        raise ValueError("Stage 4 notebook runtime prerequisite failed: python3 kernelspec argv is invalid")
    expected_tail = ["-Xfrozen_modules=off", "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    if not _windows_path_equal(Path(argv[0]), APPROVED_INTERPRETER) or argv[1:] != expected_tail:
        raise ValueError("Stage 4 notebook runtime prerequisite failed: python3 kernelspec argv is not approved")
    return NotebookClient


def _windows_path_equal(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())).casefold() == os.path.normcase(str(right.resolve())).casefold()


def _execute_notebook(notebook, working_dir: Path, notebook_client):
    return notebook_client(
        notebook,
        timeout=600,
        kernel_name="python3",
        resources={"metadata": {"path": str(working_dir.resolve())}},
    ).execute()


def _new_notebook_from_specification():
    cells = []
    for cell_id, cell_type, source in notebook_cell_specification():
        if cell_type == "markdown":
            cells.append(nbf.v4.new_markdown_cell(source, id=cell_id))
        else:
            cells.append(nbf.v4.new_code_cell(source, id=cell_id))
    return nbf.v4.new_notebook(cells=cells, metadata={"stage4_0_read_only": True})


def _validate_report(report_path: Path, manifest_path: Path, notebook_path: Path, manifest: Mapping[str, Any], root: Path) -> dict[str, Any]:
    report = _load_canonical(report_path, "control channel verification report")
    if set(report) != REPORT_KEYS:
        raise ValueError("verification report fields are not exact")
    _identity(report, "stage_04_0_control_channel_verification_report")
    expected = {
        "execution_succeeded": True,
        "compatibility_candidate_ready": manifest["compatibility_candidate_ready"],
        "control_channel_ready": False,
        "approval_status": "pending",
        "manifest_path": _repo_relative(manifest_path, root),
        "manifest_sha256": raw_file_sha256(manifest_path),
        "notebook_path": _repo_relative(notebook_path, root),
        "notebook_sha256": raw_file_sha256(notebook_path),
        "checks": manifest["checks"],
        "blocking_reasons": manifest["blocking_reasons"],
    }
    for key, value in expected.items():
        if report.get(key) != value:
            raise ValueError(f"verification report {key} does not match current evidence")
    return report


def notebook_cell_specification() -> tuple[tuple[str, str, str], ...]:
    """Return the only allowed Stage 4.0 verification notebook cell template."""

    sources = (
        (
            "markdown",
            "# Stage 4.0 control-channel compatibility\n\n"
            "Read-only verification of the sibling canonical manifest.",
        ),
        (
            "code",
            "from pathlib import Path\n"
            "import json\n"
            "MANIFEST = Path('control_channel_manifest.json')\n"
            "data = json.loads(MANIFEST.read_text(encoding='utf-8'))\n"
            "data",
        ),
        ("markdown", "## Merged channels"),
        ("code", "data['merged_channels']"),
        ("markdown", "## Upstream hashes"),
        ("code", "data['sha256']"),
        ("markdown", "## Compatibility checks"),
        ("code", "data['checks']"),
        ("markdown", "## Candidate state"),
        ("code", "{'ready': data['compatibility_candidate_ready'], 'blocking_reasons': data['blocking_reasons']}"),
    )
    return tuple((cell_id, cell_type, source) for cell_id, (cell_type, source) in zip(NOTEBOOK_CELL_IDS, sources, strict=True))
