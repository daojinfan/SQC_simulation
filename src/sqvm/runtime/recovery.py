"""Explicit, no-resume recovery operations for Stage 6 runtime evidence."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping
import uuid

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root
from sqvm.runtime.dataset import EXPECTED_RESPONSE_SHA256, validate_response_dataset
from sqvm.runtime.journal import utc_now_text, verify_event_journal
from sqvm.runtime.models import RecoveryArtifactSet, ResourceLockRecoveryReceipt
from sqvm.runtime.provenance import (
    build_environment_snapshot,
    validate_locked_environment,
    verify_source_snapshot,
)
from sqvm.runtime.storage import (
    _rename_directory_no_replace,
    atomic_publish,
    flush_directory,
    flush_tree,
    initialize_layout,
    inventory_tree,
    inventory_tree_no_follow,
    remove_resource_lock,
    resource_key,
    validate_run_id,
    write_canonical_new,
)
from sqvm.runtime.verify import verify_experiment_run


_SHA256 = re.compile(r"[0-9A-F]{64}")


def recover_interrupted_run(output_root: str | Path, run_id: str) -> RecoveryArtifactSet:
    validate_run_id(run_id)
    layout = initialize_layout(output_root)
    recovery_lock = layout.recovery_locks / f"{run_id}.lock"
    _acquire_recovery_lock(recovery_lock, run_id)
    staging = layout.staging / run_id
    run_lock = layout.locks / f"{run_id}.lock"
    recovery_staging: Path | None = None
    valid_prefix = False
    try:
        run_payload = _load_canonical(run_lock)
        _verify_lock_payload(run_payload, run_id)
        resource_lock = layout.resource_locks / f"{run_payload['resource_key']}.lock"
        quarantine = layout.quarantine / run_id
        existing = _find_existing_recovery(layout.runs, run_id)
        if existing is not None:
            valid_prefix = True
            payload = _load_canonical(existing / "recovery.json")
            if _raw_sha256(run_lock) != payload["original_run_lock_sha256"]:
                raise ValueError("live run lock does not match the published recovery record")
            if resource_lock.exists():
                if _raw_sha256(resource_lock) != payload["original_resource_lock_sha256"]:
                    raise ValueError("live resource lock does not match the published recovery record")
                remove_resource_lock(resource_lock)
            recovery_lock.unlink()
            flush_directory(layout.recovery_locks)
            return RecoveryArtifactSet(payload["recovery_id"], run_id, existing, "interrupted")
        if (layout.quarantine_records / f"{run_id}.json").exists():
            raise ValueError("malformed quarantine requires reviewed manual release")
        source = staging if staging.exists() or os.path.lexists(staging) else quarantine
        _verify_interrupted_prefix(source, run_id, run_lock, resource_lock)
        valid_prefix = True
        if source == staging:
            if quarantine.exists() or os.path.lexists(quarantine):
                raise FileExistsError("quarantine target already exists")
            flush_tree(staging)
            _rename_directory_no_replace(staging, quarantine)
            flush_tree(quarantine)
            flush_directory(layout.quarantine)
            flush_directory(layout.root)
        recovery_id = str(uuid.uuid4())
        recovery_staging = layout.staging / recovery_id
        recovery_staging.mkdir()
        payload = {
            "schema_version": "0.1",
            "recovery_id": recovery_id,
            "status": "interrupted",
            "original_run_id": run_id,
            "reason": "stale_or_crashed_attempt",
            "original_run_lock_sha256": _raw_sha256(run_lock),
            "original_resource_lock_sha256": _raw_sha256(resource_lock),
            "quarantine_relative_path": f"quarantine/{run_id}",
            "quarantine_inventory": inventory_tree(quarantine),
        }
        recovery_sha = write_canonical_new(recovery_staging / "recovery.json", payload)
        manifest = {
            "schema_version": "0.1",
            "artifact_type": "stage_06_recovery_manifest",
            "run_id": recovery_id,
            "status": "interrupted",
            "payload_files": inventory_tree(recovery_staging),
        }
        manifest_sha = write_canonical_new(recovery_staging / "manifest.json", manifest)
        report = {
            "schema_version": "0.1",
            "artifact_type": "stage_06_recovery_verification_report",
            "run_id": recovery_id,
            "status": "interrupted",
            "ok": True,
            "manifest_sha256": manifest_sha,
            "checks": [{"name": "quarantine_and_lock_binding", "passed": True}],
            "blocking_reasons": [],
        }
        report_sha = write_canonical_new(recovery_staging / "verification_report.json", report)
        receipt = {
            "schema_version": "0.1",
            "artifact_type": "stage_06_recovery_receipt",
            "run_id": recovery_id,
            "status": "interrupted",
            "manifest_sha256": manifest_sha,
            "verification_report_sha256": report_sha,
            "original_run_lock_sha256": payload["original_run_lock_sha256"],
            "original_resource_lock_sha256": payload["original_resource_lock_sha256"],
            "recovery_sha256": recovery_sha,
        }
        write_canonical_new(recovery_staging / "receipt.json", receipt)
        _verify_recovery_record(recovery_staging)
        target = layout.runs / recovery_id
        atomic_publish(recovery_staging, target)
        recovery_staging = None
        _verify_recovery_record(target)
        remove_resource_lock(resource_lock)
        recovery_lock.unlink()
        flush_directory(layout.recovery_locks)
        try:
            from sqvm.runtime.catalog import index_recovery_record

            index_recovery_record(target, layout.root)
        except Exception:
            pass
        return RecoveryArtifactSet(recovery_id, run_id, target, "interrupted")
    except Exception as exc:
        if recovery_staging is not None and recovery_staging.exists():
            shutil.rmtree(recovery_staging)
            flush_directory(layout.staging)
        if not valid_prefix and (staging.exists() or os.path.lexists(staging)) and not (layout.quarantine / run_id).exists():
            _quarantine_malformed(layout, run_id, staging, str(exc))
        elif valid_prefix and recovery_lock.exists():
            recovery_lock.unlink()
            flush_directory(layout.recovery_locks)
        raise


def recover_terminal_resource_lock(output_root: str | Path, run_id: str) -> ResourceLockRecoveryReceipt:
    validate_run_id(run_id)
    layout = initialize_layout(output_root)
    recovery_lock = layout.recovery_locks / f"{run_id}.lock"
    _acquire_recovery_lock(recovery_lock, run_id)
    try:
        run_dir = layout.runs / run_id
        report = verify_experiment_run(run_dir)
        if not report.ok or report.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("terminal run is not independently verified")
        if (layout.staging / run_id).exists() or os.path.lexists(layout.staging / run_id):
            raise ValueError("terminal resource recovery rejects an existing staging directory")
        run_lock_payload = _load_canonical(layout.locks / f"{run_id}.lock")
        _verify_lock_payload(run_lock_payload, run_id)
        run_lock = layout.locks / f"{run_id}.lock"
        resource_lock = layout.resource_locks / f"{run_lock_payload['resource_key']}.lock"
        live_run_sha = _raw_sha256(run_lock)
        live_sha = _raw_sha256(resource_lock)
        receipt = _load_canonical(run_dir / "receipt.json")
        if live_run_sha != receipt["run_lock_sha256"] or run_lock.read_bytes() != (run_dir / "snapshots/run_lock.json").read_bytes():
            raise ValueError("live run lock does not match terminal evidence")
        if live_sha != receipt["resource_lock_sha256"] or live_sha != _raw_sha256(run_dir / "snapshots/resource_lock.json"):
            raise ValueError("live resource lock does not match terminal evidence")
        if resource_lock.read_bytes() != (run_dir / "snapshots/resource_lock.json").read_bytes():
            raise ValueError("live resource lock bytes do not match terminal evidence")
        flush_tree(run_dir)
        flush_directory(layout.runs)
        flush_directory(layout.root)
        authorization = {
            "schema_version": "0.1",
            "run_id": run_id,
            "terminal_receipt_sha256": _raw_sha256(run_dir / "receipt.json"),
            "resource_lock_sha256": live_sha,
            "reason": "verified_terminal_cleanup",
            "authorized_utc": utc_now_text(),
        }
        target = layout.resource_release_records / f"{run_id}.json"
        if target.exists():
            existing = _load_canonical(target)
            if {key: existing[key] for key in authorization if key != "authorized_utc"} != {key: authorization[key] for key in authorization if key != "authorized_utc"}:
                raise ValueError("existing resource release authorization is inconsistent")
        else:
            write_canonical_new(target, authorization)
        remove_resource_lock(resource_lock)
        recovery_lock.unlink()
        flush_directory(layout.recovery_locks)
        return ResourceLockRecoveryReceipt(run_id, target, live_sha)
    except Exception:
        if recovery_lock.exists():
            recovery_lock.unlink()
            flush_directory(layout.recovery_locks)
        raise


def _verify_interrupted_prefix(staging: Path, run_id: str, run_lock: Path, resource_lock: Path) -> None:
    if not staging.is_dir() or staging.is_symlink():
        raise ValueError("interrupted staging directory is missing or unsafe")
    required = {
        "request.json",
        "point_table.json",
        "events.jsonl",
        "snapshots/device.yaml",
        "snapshots/calibration.json",
        "snapshots/environment.json",
        "snapshots/source.json",
        "snapshots/run_lock.json",
        "snapshots/resource_lock.json",
    }
    paths = {row["path"] for row in inventory_tree(staging)}
    if not required.issubset(paths):
        raise ValueError("interrupted staging prefix is incomplete")
    allowed = required | {
        "data/response.bin",
        "data/dataset.json",
        "manifest.json",
        "verification_report.json",
        "receipt.json",
    }
    if not paths.issubset(allowed):
        raise ValueError("interrupted staging contains an unknown file")
    request = _load_canonical(staging / "request.json")
    point_table = _load_canonical(staging / "point_table.json")
    verify_event_journal(staging / "events.jsonl", run_id)
    if (staging / "snapshots/run_lock.json").read_bytes() != run_lock.read_bytes():
        raise ValueError("interrupted run lock snapshot mismatch")
    if (staging / "snapshots/resource_lock.json").read_bytes() != resource_lock.read_bytes():
        raise ValueError("interrupted resource lock snapshot mismatch")
    root = find_repository_root(staging)
    from sqvm.runtime.config import load_experiment_request
    from sqvm.runtime.runner import _request_payload
    from sqvm.runtime.scan import point_table_payload

    frozen = load_experiment_request(root / "configs/experiments/platform_deterministic_smoke_v1.yaml", root)
    if request != _request_payload(frozen) or point_table != point_table_payload(frozen):
        raise ValueError("interrupted request or point table does not match the frozen configuration")
    if (staging / "snapshots/device.yaml").read_bytes() != frozen.device_snapshot.read_bytes():
        raise ValueError("interrupted device snapshot mismatch")
    if (staging / "snapshots/calibration.json").read_bytes() != frozen.calibration_snapshot.read_bytes():
        raise ValueError("interrupted calibration snapshot mismatch")
    environment = _load_canonical(staging / "snapshots/environment.json")
    current_environment = build_environment_snapshot()
    validate_locked_environment(root, current_environment)
    if environment != current_environment:
        raise ValueError("interrupted environment snapshot mismatch")
    verify_source_snapshot(_load_canonical(staging / "snapshots/source.json"), root)
    run_payload = _load_canonical(run_lock)
    resource_payload = _load_canonical(resource_lock)
    _verify_lock_payload(run_payload, run_id)
    _verify_lock_payload(resource_payload, run_id)
    if run_payload != resource_payload or run_payload.get("run_id") != run_id:
        raise ValueError("interrupted live lock identity mismatch")
    expected_resource_key = resource_key(request["backend_id"], _raw_sha256(staging / "snapshots/device.yaml"))
    if run_payload.get("resource_key") != expected_resource_key:
        raise ValueError("interrupted resource key mismatch")
    if run_payload.get("request_sha256") != _raw_sha256(staging / "request.json"):
        raise ValueError("interrupted request lock binding mismatch")
    if run_payload.get("environment_fingerprint") != _raw_sha256(staging / "snapshots/environment.json"):
        raise ValueError("interrupted environment lock binding mismatch")
    data_paths = paths & {"data/response.bin", "data/dataset.json"}
    if data_paths == {"data/dataset.json"}:
        raise ValueError("interrupted dataset manifest cannot precede its binary")
    if data_paths == {"data/response.bin"}:
        raw = (staging / "data/response.bin").read_bytes()
        if len(raw) != 48 or hashlib.sha256(raw).hexdigest().upper() != EXPECTED_RESPONSE_SHA256:
            raise ValueError("interrupted response binary is invalid")
    if data_paths == {"data/response.bin", "data/dataset.json"}:
        validate_response_dataset(
            staging / "data",
            expected_point_count=6,
            point_table_sha256=_raw_sha256(staging / "point_table.json"),
            require_frozen_fixture=True,
        )
    terminal_paths = paths & {"manifest.json", "verification_report.json", "receipt.json"}
    if terminal_paths and terminal_paths != {"manifest.json", "verification_report.json", "receipt.json"}:
        raise ValueError("interrupted terminal evidence is incomplete")
    if terminal_paths:
        report = verify_experiment_run(staging, root)
        if not report.ok:
            raise ValueError("interrupted terminal evidence is invalid: " + "; ".join(report.blocking_reasons))


def _verify_recovery_record(directory: Path) -> None:
    if {row.name for row in directory.iterdir()} != {"recovery.json", "manifest.json", "verification_report.json", "receipt.json"}:
        raise ValueError("recovery record file set is invalid")
    recovery = _load_canonical(directory / "recovery.json")
    manifest = _load_canonical(directory / "manifest.json")
    report = _load_canonical(directory / "verification_report.json")
    receipt = _load_canonical(directory / "receipt.json")
    recovery_keys = {
        "schema_version", "recovery_id", "status", "original_run_id", "reason",
        "original_run_lock_sha256", "original_resource_lock_sha256", "quarantine_relative_path",
        "quarantine_inventory",
    }
    manifest_keys = {"schema_version", "artifact_type", "run_id", "status", "payload_files"}
    report_keys = {"schema_version", "artifact_type", "run_id", "status", "ok", "manifest_sha256", "checks", "blocking_reasons"}
    receipt_keys = {
        "schema_version", "artifact_type", "run_id", "status", "manifest_sha256",
        "verification_report_sha256", "original_run_lock_sha256", "original_resource_lock_sha256",
        "recovery_sha256",
    }
    if set(recovery) != recovery_keys or set(manifest) != manifest_keys or set(report) != report_keys or set(receipt) != receipt_keys:
        raise ValueError("recovery record schema is invalid")
    if any(row.get("schema_version") != "0.1" for row in (recovery, manifest, report, receipt)):
        raise ValueError("recovery record version is invalid")
    if manifest["artifact_type"] != "stage_06_recovery_manifest" or report["artifact_type"] != "stage_06_recovery_verification_report" or receipt["artifact_type"] != "stage_06_recovery_receipt":
        raise ValueError("recovery artifact identity is invalid")
    if recovery["recovery_id"] != directory.name or manifest["run_id"] != directory.name or report["run_id"] != directory.name or receipt["run_id"] != directory.name:
        raise ValueError("recovery identity binding is invalid")
    if any(row.get("status") != "interrupted" for row in (recovery, manifest, report, receipt)) or report.get("ok") is not True or report.get("blocking_reasons") != []:
        raise ValueError("recovery terminal status is invalid")
    if manifest["payload_files"] != [
        {"path": "recovery.json", "byte_length": (directory / "recovery.json").stat().st_size, "raw_sha256": _raw_sha256(directory / "recovery.json")}
    ]:
        raise ValueError("recovery manifest inventory is invalid")
    manifest_sha = _raw_sha256(directory / "manifest.json")
    report_sha = _raw_sha256(directory / "verification_report.json")
    if report["manifest_sha256"] != manifest_sha or receipt["manifest_sha256"] != manifest_sha or receipt["verification_report_sha256"] != report_sha:
        raise ValueError("recovery hash graph is invalid")
    if receipt["recovery_sha256"] != _raw_sha256(directory / "recovery.json") or recovery["status"] != "interrupted":
        raise ValueError("recovery payload binding is invalid")
    if (
        receipt["original_run_lock_sha256"] != recovery["original_run_lock_sha256"]
        or receipt["original_resource_lock_sha256"] != recovery["original_resource_lock_sha256"]
    ):
        raise ValueError("recovery receipt lock binding is invalid")


def _quarantine_malformed(layout, run_id: str, staging: Path, reason: str) -> None:
    target = layout.quarantine / run_id
    if target.exists() or os.path.lexists(target):
        return
    record = {
        "schema_version": "0.1",
        "original_run_id": run_id,
        "reason": reason[:1024],
        "inventory": inventory_tree_no_follow(staging),
        "recorded_utc": utc_now_text(),
    }
    write_canonical_new(layout.quarantine_records / f"{run_id}.json", record)
    _rename_directory_no_replace(staging, target)
    flush_directory(layout.quarantine)
    flush_directory(layout.root)


def _find_existing_recovery(runs: Path, original_run_id: str) -> Path | None:
    matches = []
    for directory in runs.iterdir():
        recovery_path = directory / "recovery.json"
        if not directory.is_dir() or directory.is_symlink() or not recovery_path.is_file():
            continue
        try:
            payload = _load_canonical(recovery_path)
            if payload.get("original_run_id") == original_run_id:
                _verify_recovery_record(directory)
                matches.append(directory)
        except Exception:
            continue
    if len(matches) > 1:
        raise ValueError("multiple recovery records bind the same original run")
    return matches[0] if matches else None


def _acquire_recovery_lock(path: Path, run_id: str) -> None:
    payload = {"schema_version": "0.1", "original_run_id": run_id, "created_utc": utc_now_text(), "process_id": os.getpid()}
    write_canonical_new(path, payload)


def _verify_lock_payload(payload: Mapping[str, Any], run_id: str) -> None:
    expected = {
        "schema_version",
        "run_id",
        "resource_key",
        "created_utc",
        "host",
        "process_id",
        "request_sha256",
        "environment_fingerprint",
    }
    if set(payload) != expected or payload.get("schema_version") != "0.1" or payload.get("run_id") != run_id:
        raise ValueError("runtime lock schema or run identity is invalid")
    if not all(isinstance(payload.get(key), str) and payload[key] for key in ("created_utc", "host")):
        raise ValueError("runtime lock metadata is invalid")
    if isinstance(payload.get("process_id"), bool) or not isinstance(payload.get("process_id"), int) or payload["process_id"] <= 0:
        raise ValueError("runtime lock process identity is invalid")
    for key in ("resource_key", "request_sha256", "environment_fingerprint"):
        if not isinstance(payload.get(key), str) or not _SHA256.fullmatch(payload[key]):
            raise ValueError(f"runtime lock {key} is invalid")


def _load_canonical(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError(f"recovery input is not canonical: {path.name}")
    return payload


def _raw_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()
