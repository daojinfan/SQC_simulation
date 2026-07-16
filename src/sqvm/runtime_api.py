"""Version-dispatched Stage 6 API without changing the frozen Runtime 0.1 package."""

from __future__ import annotations

import json
from pathlib import Path
import stat

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.hamiltonian.provenance import find_repository_root
from sqvm.runtime.models import RunArtifactSet, RunVerificationReport
from sqvm.runtime.storage import inventory_tree_no_follow
from sqvm.runtime_v02.adapters import get_runtime_schema_adapter
from sqvm.runtime_v02.core import peek_request_schema, safe_directory_no_follow


def load_experiment_request_versioned(path: str | Path, repository_root: str | Path | None = None):
    version = peek_request_schema(path, repository_root)
    return get_runtime_schema_adapter(version).load_request(path, repository_root)


def run_experiment_versioned(
    request_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path | None = None,
) -> RunArtifactSet:
    version = peek_request_schema(request_path, repository_root)
    return get_runtime_schema_adapter(version).run(request_path, output_root, repository_root)


def verify_experiment_run_versioned(
    run_dir: str | Path,
    repository_root: str | Path | None = None,
) -> RunVerificationReport:
    lexical_directory = Path(run_dir).absolute()
    run_id = lexical_directory.name
    try:
        directory = _admit_run_directory_for_dispatch(lexical_directory, repository_root)
        version = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))["schema_version"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return RunVerificationReport(False, run_id, "invalid", "", "", (), (f"cannot dispatch runtime verifier: {exc}",))
    try:
        adapter = get_runtime_schema_adapter(version)
    except ValueError as exc:
        return RunVerificationReport(False, run_id, "invalid", "", "", (), (str(exc),))
    return adapter.verify_run(directory, repository_root)


def recover_interrupted_run_versioned(output_root: str | Path, run_id: str):
    try:
        version = _staging_request_version(output_root, run_id)
    except Exception as exc:
        _quarantine_unreadable_attempt(output_root, run_id, str(exc))
        raise ValueError("interrupted runtime schema cannot be determined; attempt quarantined") from exc
    if version == "0.2":
        from sqvm.runtime_v02.recovery import recover_interrupted_run_v02

        return recover_interrupted_run_v02(output_root, run_id)
    if version == "0.1":
        from sqvm.runtime.recovery import recover_interrupted_run

        return recover_interrupted_run(output_root, run_id)
    _quarantine_unreadable_attempt(output_root, run_id, f"unsupported runtime schema_version: {version}")
    raise ValueError("unsupported interrupted runtime schema_version; attempt quarantined")


def recover_terminal_resource_lock_versioned(output_root: str | Path, run_id: str):
    from sqvm.runtime.storage import validate_run_id

    validate_run_id(run_id)
    output = Path(output_root).absolute()
    run_dir = output / "runs" / run_id
    try:
        repository_root = find_repository_root(output)
        admitted = _admit_run_directory_for_dispatch(run_dir, repository_root)
        version = json.loads((admitted / "manifest.json").read_text(encoding="utf-8"))["schema_version"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("terminal runtime schema cannot be determined") from exc
    if version == "0.2":
        from sqvm.runtime_v02.recovery import recover_terminal_resource_lock_v02

        return recover_terminal_resource_lock_v02(output_root, run_id)
    if version == "0.1":
        from sqvm.runtime.recovery import recover_terminal_resource_lock

        return recover_terminal_resource_lock(output_root, run_id)
    raise ValueError("unsupported terminal runtime schema_version")


def rebuild_run_catalog_versioned(output_root: str | Path):
    from sqvm.runtime_v02.catalog import rebuild_run_catalog_versioned as rebuild

    return rebuild(output_root)


def _staging_request_version(output_root: str | Path, run_id: str) -> str:
    from sqvm.runtime.storage import initialize_layout, inventory_tree_no_follow, validate_run_id

    validate_run_id(run_id)
    staging = initialize_layout(output_root).staging / run_id
    inventory = inventory_tree_no_follow(staging)
    request_rows = [row for row in inventory if row.get("path") == "request.json"]
    if len(request_rows) != 1 or request_rows[0].get("entry_type") != "file":
        raise ValueError("interrupted request is missing or linked")
    raw = (staging / "request.json").read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError("interrupted request is not canonical")
    version = payload.get("schema_version")
    if not isinstance(version, str):
        raise ValueError("interrupted request schema_version is invalid")
    return version


def _quarantine_unreadable_attempt(output_root: str | Path, run_id: str, reason: str) -> None:
    from sqvm.runtime.recovery import _acquire_recovery_lock, _quarantine_malformed
    from sqvm.runtime.storage import initialize_layout, validate_run_id

    validate_run_id(run_id)
    layout = initialize_layout(output_root)
    recovery_lock = layout.recovery_locks / f"{run_id}.lock"
    _acquire_recovery_lock(recovery_lock, run_id)
    staging = layout.staging / run_id
    if not staging.exists() and not staging.is_symlink():
        raise ValueError("interrupted staging directory is missing")
    if (layout.quarantine / run_id).exists():
        raise ValueError("interrupted attempt is already quarantined")
    _quarantine_malformed(layout, run_id, staging, reason)


def _admit_run_directory_for_dispatch(run_dir: str | Path, repository_root: str | Path | None) -> Path:
    lexical_directory = Path(run_dir).absolute()
    info = lexical_directory.lstat()
    if lexical_directory.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise ValueError("versioned run directory is linked or reparse-backed")
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(lexical_directory)
    directory = safe_directory_no_follow(lexical_directory, root, "versioned run directory")
    inventory = inventory_tree_no_follow(directory)
    if any(row.get("entry_type") in {"link", "other"} for row in inventory):
        raise ValueError("versioned run tree contains a linked or special entry")
    return directory
