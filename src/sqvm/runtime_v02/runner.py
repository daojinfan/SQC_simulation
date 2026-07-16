"""Atomic publication for the Runtime 0.2 compiler-evidence lane."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from typing import Any, Mapping
import uuid

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.journal import EventJournal, JournalSummary, utc_now_text
from sqvm.runtime.lifecycle import CooperativeBudget
from sqvm.runtime.models import RunArtifactSet
from sqvm.runtime.provenance import build_environment_snapshot, validate_locked_environment
from sqvm.runtime.runner import _observe_cancellation
from sqvm.runtime.storage import (
    atomic_publish,
    copy_new,
    create_run_locks,
    initialize_layout,
    inventory_tree,
    remove_resource_lock,
    resource_key,
    snapshot_locks,
    write_canonical_new,
)
from sqvm.runtime_v02.core import (
    BACKEND_ID,
    CLAIM_ENVELOPE,
    CompiledPointV02,
    compile_point_v02,
    point_table_payload_v02,
    resolve_output_root_no_follow,
)
from sqvm.runtime_v02.provenance import build_source_snapshot_v02


CAPABILITIES = {"values": ["compiler_evidence_v1", "cooperative_deadline_v1"]}


def run_experiment_v02(
    request_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path | None = None,
) -> RunArtifactSet:
    from sqvm.runtime_v02.adapters import get_runtime_schema_adapter

    adapter = get_runtime_schema_adapter("0.2")
    request = adapter.load_request(request_path, repository_root)
    root = request.repository_root
    output = resolve_output_root_no_follow(output_root, root)
    request_payload = dict(adapter.canonical_request(request))
    point_payload = point_table_payload_v02(request)
    points = adapter.expand_points(request)
    request_sha = _sha_payload(request_payload)
    point_sha = _sha_payload(point_payload)
    environment = build_environment_snapshot()
    validate_locked_environment(root, environment)
    source = build_source_snapshot_v02(root)
    environment_sha = _sha_payload(environment)
    capabilities_sha = _sha_payload(CAPABILITIES)

    layout = initialize_layout(output)
    run_id = str(uuid.uuid4())
    run_lock, resource_lock_path = create_run_locks(
        layout,
        run_id=run_id,
        resource_key_value=resource_key(BACKEND_ID, _raw_sha(request.device_snapshot)),
        request_sha256=request_sha,
        environment_fingerprint=environment_sha,
    )
    staging = layout.staging / run_id
    try:
        staging.mkdir()
    except Exception:
        remove_resource_lock(resource_lock_path)
        run_lock.unlink(missing_ok=True)
        raise

    created_utc = utc_now_text()
    started = time.monotonic()
    journal: EventJournal | None = None
    compiled_results: list[Mapping[str, Any]] = []
    snapshots: dict[str, str] = {}
    try:
        write_canonical_new(staging / "request.json", request_payload)
        write_canonical_new(staging / "point_table.json", point_payload)
        snapshots_dir = staging / "snapshots"
        run_lock_sha, resource_lock_sha = snapshot_locks(run_lock, resource_lock_path, snapshots_dir)
        snapshots = {
            "device": copy_new(request.device_snapshot, snapshots_dir / "device.yaml"),
            "calibration": copy_new(request.calibration_snapshot, snapshots_dir / "calibration.json"),
            "environment": write_canonical_new(snapshots_dir / "environment.json", environment),
            "source": write_canonical_new(snapshots_dir / "source.json", source),
            "compiler_authority": write_canonical_new(snapshots_dir / "compiler_authority.json", _plain(request.authority)),
            "run_lock": run_lock_sha,
            "resource_lock": resource_lock_sha,
        }
        program_dir = staging / "program"
        program_dir.mkdir()
        _write_new(program_dir / "template.qcis", request.program["source"].encode("ascii"))
        write_canonical_new(program_dir / "program.json", _plain(request.program))

        journal = EventJournal(staging / "events.jsonl", run_id)
        journal.append("run_reserved", {
            "request_sha256": request_sha,
            "point_table_sha256": point_sha,
            "run_lock_sha256": run_lock_sha,
            "resource_lock_sha256": resource_lock_sha,
        })
        journal.append("run_prepared", {
            "experiment_id": request.experiment_id,
            "backend_id": request.backend_id,
            "backend_capabilities_sha256": capabilities_sha,
        })
        budget = CooperativeBudget(request.execution.point_budget_seconds, request.execution.run_budget_seconds)
        from sqvm.runtime.lifecycle import CancellationToken

        token = CancellationToken()
        if _observe_cancellation(layout, run_id, token, journal):
            raise _Cancelled
        journal.append("run_started", {"point_count": len(points)})
        points_dir = staging / "points"
        points_dir.mkdir()
        for point in points:
            if _observe_cancellation(layout, run_id, token, journal):
                raise _Cancelled
            journal.append("point_started", {"point_index": point.point_index, "point_id": point.point_id})
            point_started = budget.before_point()
            try:
                compiled = compile_point_v02(request, point)
                adapter.validate_point_result(point, compiled.result)
                budget.after_point(point_started)
                result_sha = _write_point(points_dir, compiled)
            except Exception as exc:
                journal.append("point_failed", {
                    "point_index": point.point_index,
                    "point_id": point.point_id,
                    "error_class": "integrity_failure" if isinstance(exc, ValueError) else "compiler_failure",
                    "message": _message(exc),
                })
                raise
            compiled_results.append(compiled.result)
            journal.append("point_completed", {
                "point_index": point.point_index,
                "point_id": point.point_id,
                "result_sha256": result_sha,
            })

        journal.append("run_finalizing", {"completed_point_count": len(compiled_results)})
        dataset = adapter.write_dataset(compiled_results, point_sha, staging / "data")
        dataset_summary = {
            "dataset_manifest_sha256": _raw_sha(staging / "data/dataset.json"),
            "point_count": len(compiled_results),
            "variable_hashes": {
                name: value["raw_sha256"] for name, value in dataset["variables"].items()
            },
        }
        journal.append("run_completed", {
            "dataset_manifest_sha256": dataset_summary["dataset_manifest_sha256"],
            "completed_point_count": len(compiled_results),
        })
        summary = journal.close()
        return _publish(
            layout, root, staging, run_id, "completed", created_utc, utc_now_text(), request,
            request_sha, point_sha, snapshots, summary, dataset_summary, capabilities_sha,
            time.monotonic() - started, resource_lock_path,
        )
    except _Cancelled:
        status, error_class, message = "cancelled", "cancelled", "cancellation requested"
    except Exception as exc:
        if journal is not None and journal.closed:
            raise
        status = "failed"
        error_class = "integrity_failure" if isinstance(exc, ValueError) else "compiler_failure"
        message = _message(exc)

    if journal is None:
        remove_resource_lock(resource_lock_path)
        raise RuntimeError(message)
    if (staging / "points").exists():
        shutil.rmtree(staging / "points")
    if (staging / "data").exists():
        shutil.rmtree(staging / "data")
    journal.append(
        "run_cancelled" if status == "cancelled" else "run_failed",
        {"completed_point_count": len(compiled_results)} if status == "cancelled" else {
            "error_class": error_class, "message": message, "completed_point_count": len(compiled_results),
        },
    )
    summary = journal.close()
    return _publish(
        layout, root, staging, run_id, status, created_utc, utc_now_text(), request,
        request_sha, point_sha, snapshots, summary, None, capabilities_sha,
        time.monotonic() - started, resource_lock_path,
    )


def _write_point(points_dir: Path, compiled: CompiledPointV02) -> str:
    target = points_dir / compiled.point.point_id
    target.mkdir()
    _write_new(target / "concrete.qcis", compiled.concrete_source)
    _write_new(target / "ast.json", compiled.ast_bytes)
    _write_new(target / "trace.json", compiled.trace_bytes)
    write_canonical_new(target / "logical_inventory.json", _plain(compiled.logical_inventory))
    return write_canonical_new(target / "result.json", _plain(compiled.result))


def _publish(
    layout, root: Path, staging: Path, run_id: str, status: str, created_utc: str,
    terminal_utc: str, request, request_sha: str, point_sha: str, snapshots: Mapping[str, str],
    journal: JournalSummary, dataset_summary: Mapping[str, Any] | None, capabilities_sha: str,
    elapsed_seconds: float, resource_lock_path: Path,
) -> RunArtifactSet:
    output_relative = layout.root.relative_to(root).as_posix()
    manifest = {
        "schema_version": "0.2",
        "artifact_type": "stage_06_experiment_run_manifest",
        "run_id": run_id,
        "status": status,
        "created_utc": created_utc,
        "terminal_utc": terminal_utc,
        "experiment_id": request.experiment_id,
        "backend_id": request.backend_id,
        "output_root": output_relative,
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "request_sha256": request_sha,
        "point_table_sha256": point_sha,
        "events_sha256": journal.raw_sha256,
        "event_tail_sha256": journal.tail_event_sha256,
        "snapshots": dict(snapshots),
        "backend_capabilities": {**CAPABILITIES, "sha256": capabilities_sha},
        "payload_files": inventory_tree(staging),
        "dataset_summary": None if dataset_summary is None else _plain(dataset_summary),
    }
    manifest_sha = write_canonical_new(staging / "manifest.json", manifest)
    report = {
        "schema_version": "0.2",
        "artifact_type": "stage_06_experiment_run_verification_report",
        "run_id": run_id,
        "status": status,
        "ok": True,
        "manifest_sha256": manifest_sha,
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "checks": [
            {"name": "payload_inventory_verified", "passed": True},
            {"name": "event_chain_verified", "passed": True},
            {"name": "qcis_replay_verified", "passed": True, "message": "verified" if status == "completed" else "not_applicable"},
            {"name": "claim_envelope_verified", "passed": True},
            {"name": "dataset_contract_applied", "passed": True, "message": "verified" if status == "completed" else "not_applicable"},
        ],
        "blocking_reasons": [],
    }
    report_sha = write_canonical_new(staging / "verification_report.json", report)
    dataset_hashes = {}
    if status == "completed":
        dataset_hashes = {
            row.name: _raw_sha(row) for row in sorted((staging / "data").iterdir(), key=lambda item: item.name)
        }
    receipt = {
        "schema_version": "0.2",
        "artifact_type": "stage_06_experiment_run_receipt",
        "run_id": run_id,
        "status": status,
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "manifest_sha256": manifest_sha,
        "verification_report_sha256": report_sha,
        "event_tail_sha256": journal.tail_event_sha256,
        "elapsed_seconds": elapsed_seconds,
        "published_relative_path": f"{output_relative}/runs/{run_id}",
        "run_lock_sha256": snapshots["run_lock"],
        "resource_lock_sha256": snapshots["resource_lock"],
        "dataset_hashes": dataset_hashes,
    }
    receipt_sha = write_canonical_new(staging / "receipt.json", receipt)
    from sqvm.runtime_v02.verify import verify_experiment_run_v02

    preflight = verify_experiment_run_v02(staging, root)
    if not preflight.ok:
        raise ValueError("Runtime 0.2 staging verification failed: " + "; ".join(preflight.blocking_reasons))
    target = layout.runs / run_id
    atomic_publish(staging, target)
    final = verify_experiment_run_v02(target, root)
    if not final.ok:
        raise ValueError("Runtime 0.2 publication verification failed: " + "; ".join(final.blocking_reasons))
    remove_resource_lock(resource_lock_path)
    catalog_indexed = True
    warning = None
    try:
        from sqvm.runtime_v02.catalog import index_run_v02

        index_run_v02(target, layout.root)
    except Exception as exc:
        catalog_indexed = False
        warning = str(exc)
    return RunArtifactSet(
        run_id, target, target / "manifest.json", target / "verification_report.json", target / "receipt.json",
        manifest_sha, report_sha, receipt_sha, status, catalog_indexed, warning,
    )


def _write_new(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _sha_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


def _raw_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _message(exc: Exception) -> str:
    return (str(exc).replace("\r", " ").replace("\n", " ")[:1024] or exc.__class__.__name__)


class _Cancelled(Exception):
    pass
