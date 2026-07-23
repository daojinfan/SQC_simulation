"""Closed, no-follow evidence envelopes for published spectroscopy scans."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.storage import flush_directory


SCAN_ARTIFACT_VERSION = "0.3"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "receipt.json"
RECOVERY_NAME = "recovery.json"
UNCERTAIN_PUBLICATION_PREFIX = ".spectroscopy-publication-"
RECOVERY_MARKER_PREFIX = ".spectroscopy-recovery-"
_TERMINAL_NAMES = frozenset({MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME})


class SpectroscopyEvidenceError(ValueError):
    """The published evidence envelope is incomplete or unsafe."""


def write_completed_evidence(
    directory: Path,
    *,
    run_id: str,
    recommendation_id: str,
    workflow_id: str,
    workflow_sha256: str,
    dataset_sha256: str,
    parent_configuration_sha256: str,
    recommendation_eligible: bool,
) -> tuple[str, str]:
    """Write the acyclic terminal documents after all scan evidence exists."""

    payload_files = _payload_inventory(directory)
    execution_files = _execution_inventory(directory)
    if not execution_files:
        raise SpectroscopyEvidenceError("execution evidence inventory is empty")
    manifest = {
        "schema_version": "0.1",
        "artifact_type": "stage_07_qubit_spectroscopy_scan_manifest",
        "artifact_version": SCAN_ARTIFACT_VERSION,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "status": "completed",
        "payload_files": payload_files,
        "execution_files": execution_files,
    }
    manifest_sha256 = _write_new(directory / MANIFEST_NAME, manifest)
    report = {
        "schema_version": "0.1",
        "artifact_type": "stage_07_qubit_spectroscopy_scan_verification_report",
        "artifact_version": SCAN_ARTIFACT_VERSION,
        "run_id": run_id,
        "status": "completed",
        "ok": True,
        "manifest_sha256": manifest_sha256,
        "checks": [
            {"name": "exact_root_file_set", "passed": True},
            {"name": "no_follow_execution_inventory", "passed": True},
            {"name": "payload_hashes_bound", "passed": True},
        ],
        "blocking_reasons": [],
    }
    report_sha256 = _write_new(directory / REPORT_NAME, report)
    receipt = {
        "schema_version": "0.1",
        "artifact_type": "qubit_spectroscopy_scan_receipt",
        "artifact_version": SCAN_ARTIFACT_VERSION,
        "run_id": run_id,
        "recommendation_id": recommendation_id,
        "status": "completed",
        "workflow_sha256": workflow_sha256,
        "dataset_sha256": dataset_sha256,
        "manifest_sha256": manifest_sha256,
        "verification_report_sha256": report_sha256,
        "recommendation_eligible": recommendation_eligible,
        "parent_configuration_sha256": parent_configuration_sha256,
        "archive_eligible": True,
    }
    receipt_sha256 = _write_new(directory / RECEIPT_NAME, receipt)
    return manifest_sha256, receipt_sha256


def verify_completed_evidence(
    directory: Path,
    *,
    workflow: Mapping[str, Any],
    receipt: Mapping[str, Any],
    workflow_sha256: str,
    dataset_sha256: str,
) -> None:
    """Verify the exact v0.3 evidence closure without following links."""

    _require_exact_root(directory)
    manifest = _load_canonical(directory / MANIFEST_NAME, "manifest")
    report = _load_canonical(directory / REPORT_NAME, "verification report")
    expected_payload = _payload_inventory(directory)
    expected_execution = _execution_inventory(directory)
    if not expected_execution:
        raise SpectroscopyEvidenceError("execution evidence inventory is empty")
    expected_manifest = {
        "schema_version": "0.1",
        "artifact_type": "stage_07_qubit_spectroscopy_scan_manifest",
        "artifact_version": SCAN_ARTIFACT_VERSION,
        "run_id": workflow["run_id"],
        "workflow_id": workflow["workflow_id"],
        "status": "completed",
        "payload_files": expected_payload,
        "execution_files": expected_execution,
    }
    if manifest != expected_manifest:
        raise SpectroscopyEvidenceError("spectroscopy evidence manifest inventory is invalid")
    manifest_sha256 = _raw_sha256(directory / MANIFEST_NAME)
    expected_report = {
        "schema_version": "0.1",
        "artifact_type": "stage_07_qubit_spectroscopy_scan_verification_report",
        "artifact_version": SCAN_ARTIFACT_VERSION,
        "run_id": workflow["run_id"],
        "status": "completed",
        "ok": True,
        "manifest_sha256": manifest_sha256,
        "checks": [
            {"name": "exact_root_file_set", "passed": True},
            {"name": "no_follow_execution_inventory", "passed": True},
            {"name": "payload_hashes_bound", "passed": True},
        ],
        "blocking_reasons": [],
    }
    if report != expected_report:
        raise SpectroscopyEvidenceError("spectroscopy evidence verification report is invalid")
    expected_receipt = {
        "schema_version": "0.1",
        "artifact_type": "qubit_spectroscopy_scan_receipt",
        "artifact_version": SCAN_ARTIFACT_VERSION,
        "run_id": workflow["run_id"],
        "recommendation_id": workflow["recommendation_id"],
        "status": "completed",
        "workflow_sha256": workflow_sha256,
        "dataset_sha256": dataset_sha256,
        "manifest_sha256": manifest_sha256,
        "verification_report_sha256": _raw_sha256(directory / REPORT_NAME),
        "recommendation_eligible": workflow["recommendation_eligible"],
        "parent_configuration_sha256": workflow["parent_configuration"]["sha256"],
        "archive_eligible": True,
    }
    if dict(receipt) != expected_receipt:
        raise SpectroscopyEvidenceError("spectroscopy evidence receipt is invalid")


def write_recovery_required(
    directory: Path,
    *,
    run_id: str,
    recommendation_id: str,
    reason: str,
    failure: BaseException,
    created_utc: str,
) -> None:
    """Persist a nonterminal marker; it intentionally never claims completion."""

    if reason not in {"failed", "cancelled", "interrupted", "publication_failure"}:
        raise ValueError("unsupported spectroscopy recovery reason")
    target = directory / RECOVERY_NAME
    if target.exists():
        return
    message = str(failure).replace("\r", " ").replace("\n", " ")[:1024]
    payload = {
        "schema_version": "0.1",
        "artifact_type": "qubit_spectroscopy_scan_recovery",
        "artifact_version": "0.1",
        "run_id": run_id,
        "recommendation_id": recommendation_id,
        "status": "recovery_required",
        "reason": reason,
        "created_utc": created_utc,
        "failure_class": failure.__class__.__name__,
        "failure_message": message or failure.__class__.__name__,
    }
    _write_new(target, payload)


def write_uncertain_publication(
    directory: Path,
    *,
    run_id: str,
    recommendation_id: str,
    target_directory_name: str,
    target_relative_path: str,
    failure: BaseException,
    created_utc: str,
) -> Path:
    """Record a post-rename publication uncertainty without touching the target tree."""

    target = directory / f"{UNCERTAIN_PUBLICATION_PREFIX}{run_id}.json"
    if target.exists():
        return target
    payload = {
        "schema_version": "0.1",
        "artifact_type": "qubit_spectroscopy_scan_publication_uncertain",
        "artifact_version": "0.1",
        "run_id": run_id,
        "recommendation_id": recommendation_id,
        "status": "publication_uncertain",
        "created_utc": created_utc,
        "target_directory_name": target_directory_name,
        "target_relative_path": target_relative_path,
        "failure_class": failure.__class__.__name__,
        "failure_message": (
            str(failure).replace("\r", " ").replace("\n", " ")[:1024]
            or failure.__class__.__name__
        ),
    }
    _write_new(target, payload)
    return target


def write_recovery_fallback_marker(
    staging: Path,
    *,
    run_id: str,
    recommendation_id: str,
    reason: str,
    failure: BaseException,
    primary_failure: Exception,
    created_utc: str,
) -> Path:
    """Write an independent create-only marker when ``recovery.json`` cannot be written."""

    target = staging.parent / f"{RECOVERY_MARKER_PREFIX}{run_id}.marker"
    if os.path.lexists(target):
        return target
    payload = {
        "schema_version": "0.1",
        "artifact_type": "qubit_spectroscopy_scan_recovery_marker",
        "artifact_version": "0.1",
        "run_id": run_id,
        "recommendation_id": recommendation_id,
        "status": "recovery_required",
        "reason": reason,
        "created_utc": created_utc,
        "staging_directory_name": staging.name,
        "failure_class": failure.__class__.__name__,
        "failure_message": _message(failure),
        "primary_failure_class": primary_failure.__class__.__name__,
        "primary_failure_message": _message(primary_failure),
    }
    _write_fallback_marker_new(target, payload)
    return target


def _require_exact_root(directory: Path) -> None:
    _require_safe_ancestry(directory)
    if _is_link_or_junction(directory) or not directory.is_dir():
        raise SpectroscopyEvidenceError("spectroscopy evidence root is unsafe")
    names = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            path = Path(entry.path)
            if _is_link_or_junction(path):
                raise SpectroscopyEvidenceError("spectroscopy evidence root contains a link")
            names.add(entry.name)
    expected = {
        "workflow.json", "dataset.json", "execution", MANIFEST_NAME, REPORT_NAME,
        RECEIPT_NAME,
    }
    if names != expected:
        raise SpectroscopyEvidenceError("spectroscopy evidence root file set is invalid")
    execution = directory / "execution"
    if not execution.is_dir() or _is_link_or_junction(execution):
        raise SpectroscopyEvidenceError("spectroscopy execution root is unsafe")


def _payload_inventory(directory: Path) -> list[dict[str, Any]]:
    return [
        entry for entry in _inventory_no_follow(directory)
        if entry["path"] not in _TERMINAL_NAMES
    ]


def _execution_inventory(directory: Path) -> list[dict[str, Any]]:
    return [
        {**entry, "path": f"execution/{entry['path']}"}
        for entry in _inventory_no_follow(directory / "execution")
        if entry["path"]
    ]


def _inventory_no_follow(root: Path) -> list[dict[str, Any]]:
    if _is_link_or_junction(root) or not root.is_dir():
        raise SpectroscopyEvidenceError("evidence inventory root is unsafe")
    rows: list[dict[str, Any]] = []

    def visit(directory: Path) -> None:
        names: set[str] = set()
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda row: row.name):
                path = Path(entry.path)
                folded = entry.name.casefold()
                if folded in names:
                    raise SpectroscopyEvidenceError("evidence inventory has a case collision")
                names.add(folded)
                if entry.is_symlink() or _is_link_or_junction(path):
                    raise SpectroscopyEvidenceError("evidence inventory contains a link")
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False):
                    rows.append(_file_entry(path, root))
                else:
                    raise SpectroscopyEvidenceError("evidence inventory contains a special file")

    visit(root)
    return sorted(rows, key=lambda row: row["path"])


def _file_entry(path: Path, root: Path) -> dict[str, Any]:
    if path.stat(follow_symlinks=False).st_nlink != 1:
        raise SpectroscopyEvidenceError("evidence inventory contains a hard link")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return {
        "path": path.relative_to(root).as_posix(),
        "byte_length": size,
        "raw_sha256": digest.hexdigest().upper(),
    }


def _load_canonical(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpectroscopyEvidenceError(f"cannot read spectroscopy {label}: {exc}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise SpectroscopyEvidenceError(f"spectroscopy {label} is not canonical JSON")
    return value


def _write_new(path: Path, payload: Mapping[str, Any]) -> str:
    raw = canonical_json_bytes(payload)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    flush_directory(path.parent)
    return hashlib.sha256(raw).hexdigest().upper()


def _write_fallback_marker_new(path: Path, payload: Mapping[str, Any]) -> None:
    """Keep the fallback write independent from the primary recovery writer."""

    raw = canonical_json_bytes(payload)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    flush_directory(path.parent)


def _message(exc: BaseException) -> str:
    return str(exc).replace("\r", " ").replace("\n", " ")[:1024] or exc.__class__.__name__


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _require_safe_ancestry(path: Path) -> None:
    current = path
    while True:
        if _is_link_or_junction(current):
            raise SpectroscopyEvidenceError("spectroscopy evidence path contains an ancestor link")
        parent = current.parent
        if parent == current:
            return
        current = parent
