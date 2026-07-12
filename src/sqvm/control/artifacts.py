"""Canonical Stage 4.0 candidate publication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

import nbformat as nbf

from sqvm.control.compatibility import (
    _assert_finite,
    _execute_notebook,
    _new_notebook_from_specification,
    _preflight_notebook_runtime,
    build_control_channel_manifest,
    validate_verification_candidate,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256


MANIFEST_NAME = "control_channel_manifest.json"
NOTEBOOK_NAME = "verification.ipynb"
REPORT_NAME = "verification_report.json"


def publish_control_channel_candidate(
    output_dir: str | Path,
    registry_path: str | Path = "configs/control/2q1c2r_channels.yaml",
    *,
    repository_root: str | Path | None = None,
    paths: Mapping[str, str | Path] | None = None,
) -> dict[str, Any]:
    """Publish the exact-three pending candidate by one sibling directory rename."""

    target = Path(output_dir)
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(target)
    if target.exists():
        raise FileExistsError("formal Stage 4.0 output directory must not already exist")
    manifest = build_control_channel_manifest(registry_path, repository_root=root, paths=paths)
    if manifest["compatibility_candidate_ready"] is not True or manifest["blocking_reasons"]:
        raise ValueError("control channel compatibility candidate is not ready")
    manifest_raw = canonical_json_bytes(manifest)
    _assert_finite(manifest)
    notebook_client = _preflight_notebook_runtime()

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}.staging."))
    try:
        manifest_path = staging / MANIFEST_NAME
        _atomic_write(manifest_path, manifest_raw)
        notebook_path = staging / NOTEBOOK_NAME
        _write_executed_notebook(notebook_path, staging, notebook_client)
        report = _build_report(
            manifest,
            target / MANIFEST_NAME,
            manifest_path,
            target / NOTEBOOK_NAME,
            notebook_path,
            root,
        )
        report_raw = canonical_json_bytes(report)
        _assert_finite(report)
        _atomic_write(staging / REPORT_NAME, report_raw)
        if {entry.name for entry in staging.iterdir()} != {MANIFEST_NAME, NOTEBOOK_NAME, REPORT_NAME}:
            raise ValueError("Stage 4.0 staging directory is not exact-three")
        os.replace(staging, target)
        validate_verification_candidate(target, repository_root=root)
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise


def _build_report(
    manifest: Mapping[str, Any],
    final_manifest: Path,
    staged_manifest: Path,
    final_notebook: Path,
    staged_notebook: Path,
    root: Path,
) -> dict[str, Any]:
    report = {
        "schema_version": "0.1",
        "artifact_type": "stage_04_0_control_channel_verification_report",
        "artifact_version": "0.1",
        "execution_succeeded": True,
        "compatibility_candidate_ready": manifest["compatibility_candidate_ready"],
        "control_channel_ready": False,
        "approval_status": "pending",
        "manifest_path": final_manifest.resolve().relative_to(root).as_posix(),
        "manifest_sha256": raw_file_sha256(staged_manifest),
        "notebook_path": final_notebook.resolve().relative_to(root).as_posix(),
        "notebook_sha256": raw_file_sha256(staged_notebook),
        "checks": manifest["checks"],
        "blocking_reasons": manifest["blocking_reasons"],
    }
    _assert_finite(report)
    return report


def _write_executed_notebook(target: Path, working_dir: Path, notebook_client) -> None:
    executed = _execute_notebook(_new_notebook_from_specification(), working_dir, notebook_client)
    descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        nbf.write(executed, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(path: Path, raw: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()
