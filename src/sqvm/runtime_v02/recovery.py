"""No-resume recovery for Runtime 0.2 staging and terminal locks."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import uuid

from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.models import RecoveryArtifactSet, ResourceLockRecoveryReceipt
from sqvm.runtime.recovery import (
    _acquire_recovery_lock,
    _find_existing_recovery,
    _load_canonical,
    _quarantine_malformed,
    _raw_sha256,
    _verify_lock_payload,
    _verify_recovery_record,
)
from sqvm.runtime.storage import (
    _rename_directory_no_replace,
    atomic_publish,
    flush_directory,
    flush_tree,
    initialize_layout,
    inventory_tree,
    remove_resource_lock,
    validate_run_id,
    write_canonical_new,
)
from sqvm.runtime_v02.verify import verify_experiment_run_v02, verify_interrupted_prefix_v02
from sqvm.runtime_v02.core import AUTHORITY_ID, _safe_regular_file


def recover_interrupted_run_v02(output_root: str | Path, run_id: str) -> RecoveryArtifactSet:
    validate_run_id(run_id)
    layout = initialize_layout(output_root)
    recovery_lock = layout.recovery_locks / f"{run_id}.lock"
    _acquire_recovery_lock(recovery_lock, run_id)
    staging = layout.staging / run_id
    run_lock = layout.locks / f"{run_id}.lock"
    recovery_staging: Path | None = None
    valid_prefix = False
    try:
        run_lock = _safe_regular_file(layout.root, f"locks/{run_id}.lock")
        run_payload = _load_canonical(run_lock)
        _verify_lock_payload(run_payload, run_id)
        resource_lock_path = layout.resource_locks / f"{run_payload['resource_key']}.lock"
        quarantine = layout.quarantine / run_id
        existing = _find_existing_recovery(layout.runs, run_id)
        if existing is not None:
            valid_prefix = True
            payload = _load_canonical(existing / "recovery.json")
            if _raw_sha256(run_lock) != payload["original_run_lock_sha256"]:
                raise ValueError("live run lock does not match the published recovery record")
            if resource_lock_path.exists() or resource_lock_path.is_symlink():
                resource_lock = _safe_regular_file(layout.root, f"resource-locks/{resource_lock_path.name}")
                if _raw_sha256(resource_lock) != payload["original_resource_lock_sha256"]:
                    raise ValueError("live resource lock does not match the published recovery record")
                remove_resource_lock(resource_lock)
            recovery_lock.unlink()
            flush_directory(layout.recovery_locks)
            return RecoveryArtifactSet(payload["recovery_id"], run_id, existing, "interrupted")
        resource_lock = _safe_regular_file(layout.root, f"resource-locks/{resource_lock_path.name}")
        if (layout.quarantine_records / f"{run_id}.json").exists():
            raise ValueError("malformed quarantine requires reviewed manual release")
        source = staging if staging.exists() or os.path.lexists(staging) else quarantine
        verify_interrupted_prefix_v02(source, run_id, run_lock, resource_lock)
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
        interrupted_request = _load_canonical(quarantine / "request.json")
        interrupted_source = _load_canonical(quarantine / "snapshots/source.json")
        payload = {
            "schema_version": "0.1",
            "recovery_id": recovery_id,
            "status": "interrupted",
            "original_run_id": run_id,
            "reason": (
                f"runtime_v02|schema=0.2|authority_id={AUTHORITY_ID}"
                f"|authority_sha256={interrupted_request['program_authority_sha256']}"
                f"|source_aggregate={interrupted_source['aggregate_sha256']}|stale_or_crashed_attempt"
            ),
            "original_run_lock_sha256": _raw_sha256(run_lock),
            "original_resource_lock_sha256": _raw_sha256(resource_lock),
            "quarantine_relative_path": f"quarantine/{run_id}",
            "quarantine_inventory": inventory_tree(quarantine),
        }
        recovery_sha = write_canonical_new(recovery_staging / "recovery.json", payload)
        manifest = {"schema_version": "0.1", "artifact_type": "stage_06_recovery_manifest", "run_id": recovery_id, "status": "interrupted", "payload_files": inventory_tree(recovery_staging)}
        manifest_sha = write_canonical_new(recovery_staging / "manifest.json", manifest)
        report = {"schema_version": "0.1", "artifact_type": "stage_06_recovery_verification_report", "run_id": recovery_id, "status": "interrupted", "ok": True, "manifest_sha256": manifest_sha, "checks": [{"name": "quarantine_and_lock_binding", "passed": True}], "blocking_reasons": []}
        report_sha = write_canonical_new(recovery_staging / "verification_report.json", report)
        receipt = {"schema_version": "0.1", "artifact_type": "stage_06_recovery_receipt", "run_id": recovery_id, "status": "interrupted", "manifest_sha256": manifest_sha, "verification_report_sha256": report_sha, "original_run_lock_sha256": payload["original_run_lock_sha256"], "original_resource_lock_sha256": payload["original_resource_lock_sha256"], "recovery_sha256": recovery_sha}
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
            from sqvm.runtime_v02.catalog import index_recovery_record_v02

            index_recovery_record_v02(target, layout.root)
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


def recover_terminal_resource_lock_v02(output_root: str | Path, run_id: str) -> ResourceLockRecoveryReceipt:
    validate_run_id(run_id)
    layout = initialize_layout(output_root)
    recovery_lock = layout.recovery_locks / f"{run_id}.lock"
    _acquire_recovery_lock(recovery_lock, run_id)
    try:
        run_dir = layout.runs / run_id
        report = verify_experiment_run_v02(run_dir)
        if not report.ok or report.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("terminal Runtime 0.2 run is not independently verified")
        if (layout.staging / run_id).exists() or os.path.lexists(layout.staging / run_id):
            raise ValueError("terminal resource recovery rejects an existing staging directory")
        run_lock = _safe_regular_file(layout.root, f"locks/{run_id}.lock")
        run_payload = _load_canonical(run_lock)
        _verify_lock_payload(run_payload, run_id)
        resource_lock_path = layout.resource_locks / f"{run_payload['resource_key']}.lock"
        resource_lock = _safe_regular_file(layout.root, f"resource-locks/{resource_lock_path.name}")
        live_run_sha = _raw_sha256(run_lock)
        live_sha = _raw_sha256(resource_lock)
        receipt = _load_canonical(run_dir / "receipt.json")
        if live_run_sha != receipt["run_lock_sha256"] or run_lock.read_bytes() != (run_dir / "snapshots/run_lock.json").read_bytes():
            raise ValueError("live run lock does not match Runtime 0.2 terminal evidence")
        if live_sha != receipt["resource_lock_sha256"] or resource_lock.read_bytes() != (run_dir / "snapshots/resource_lock.json").read_bytes():
            raise ValueError("live resource lock does not match Runtime 0.2 terminal evidence")
        authorization = {"schema_version": "0.1", "run_id": run_id, "terminal_receipt_sha256": _raw_sha256(run_dir / "receipt.json"), "resource_lock_sha256": live_sha, "reason": "verified_terminal_cleanup", "authorized_utc": utc_now_text()}
        target = layout.resource_release_records / f"{run_id}.json"
        if not target.exists():
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
