"""Single-host Stage 6 experiment orchestration and terminal publication."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import time
from types import MappingProxyType
from typing import Any, Mapping
import uuid

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.backend import admit_backend
from sqvm.runtime.catalog import index_run
from sqvm.runtime.config import load_experiment_request
from sqvm.runtime.dataset import CLAIM_ENVELOPE, dataset_manifest_sha256, write_response_dataset
from sqvm.runtime.journal import EventJournal, JournalSummary, utc_now_text
from sqvm.runtime.lifecycle import CancellationToken, CooperativeBudget, RunState, transition
from sqvm.runtime.models import CancellationReceipt, ExperimentRequest, RunArtifactSet
from sqvm.runtime.provenance import build_environment_snapshot, build_source_snapshot, validate_locked_environment
from sqvm.runtime.registry import get_builtin_backend_registry, get_builtin_experiment_registry
from sqvm.runtime.scan import expand_scan, point_table_payload
from sqvm.runtime.storage import (
    RuntimeLayout,
    atomic_publish,
    copy_new,
    create_run_locks,
    initialize_layout,
    inventory_tree,
    remove_resource_lock,
    resource_key,
    snapshot_locks,
    validate_run_id,
    write_canonical_new,
)


def run_experiment(
    request_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path | None = None,
) -> RunArtifactSet:
    """Admit, execute, and publish one immutable deterministic Stage 6 run."""

    request = load_experiment_request(request_path, repository_root)
    root = request.repository_root
    output = _resolve_output_root(output_root, root)
    points = expand_scan(request)
    request_payload = _request_payload(request)
    point_payload = point_table_payload(request)
    request_sha = _payload_sha(request_payload)
    point_sha = _payload_sha(point_payload)
    environment = build_environment_snapshot()
    validate_locked_environment(root, environment)
    source = build_source_snapshot(root)
    environment_sha = _payload_sha(environment)
    experiments = get_builtin_experiment_registry()
    backends = get_builtin_backend_registry()
    definition = experiments.resolve(request.experiment_id)
    backend = backends.resolve(request.backend_id)
    prepared = definition.prepare(
        request,
        {
            "device": _raw_sha256(request.device_snapshot),
            "calibration": _raw_sha256(request.calibration_snapshot),
            "environment": environment_sha,
            "source": _payload_sha(source),
        },
    )
    admission = admit_backend(backend, prepared, definition.required_backend_capabilities)
    capabilities_payload: Mapping[str, Any] = {"values": sorted(admission.capabilities.values)}
    capabilities_sha = _payload_sha(capabilities_payload)
    layout = initialize_layout(output)
    run_id = str(uuid.uuid4())
    resource_key_value = resource_key(request.backend_id, _raw_sha256(request.device_snapshot))
    run_lock, resource_lock = create_run_locks(
        layout,
        run_id=run_id,
        resource_key_value=resource_key_value,
        request_sha256=request_sha,
        environment_fingerprint=environment_sha,
    )
    staging = layout.staging / run_id
    try:
        staging.mkdir()
    except Exception:
        try:
            remove_resource_lock(resource_lock)
            run_lock.unlink()
            from sqvm.runtime.storage import flush_directory

            flush_directory(layout.locks)
        except Exception as cleanup_error:
            raise RuntimeError("staging creation and reservation cleanup both failed") from cleanup_error
        raise
    created_utc = utc_now_text()
    started = time.monotonic()
    state = RunState.RESERVED
    status = "failed"
    error_class = "internal"
    error_message = "run did not execute"
    responses: list[float] = []
    dataset_summary: Mapping[str, Any] | None = None
    journal_summary: JournalSummary | None = None
    journal = None
    snapshot_hashes: Mapping[str, str] = {}
    try:
        write_canonical_new(staging / "request.json", request_payload)
        write_canonical_new(staging / "point_table.json", point_payload)
        snapshots_dir = staging / "snapshots"
        run_lock_sha, resource_lock_sha = snapshot_locks(run_lock, resource_lock, snapshots_dir)
        device_sha = copy_new(request.device_snapshot, snapshots_dir / "device.yaml")
        calibration_sha = copy_new(request.calibration_snapshot, snapshots_dir / "calibration.json")
        environment_snapshot_sha = write_canonical_new(snapshots_dir / "environment.json", environment)
        source_snapshot_sha = write_canonical_new(snapshots_dir / "source.json", source)
        snapshot_hashes = {
            "device": device_sha,
            "calibration": calibration_sha,
            "environment": environment_snapshot_sha,
            "source": source_snapshot_sha,
            "run_lock": run_lock_sha,
            "resource_lock": resource_lock_sha,
        }
        journal = EventJournal(staging / "events.jsonl", run_id)
        journal.append(
            "run_reserved",
            {
                "request_sha256": request_sha,
                "point_table_sha256": point_sha,
                "run_lock_sha256": run_lock_sha,
                "resource_lock_sha256": resource_lock_sha,
            },
        )

        state = transition(state, RunState.PREPARED)
        journal.append(
            "run_prepared",
            {
                "experiment_id": request.experiment_id,
                "backend_id": request.backend_id,
                "backend_capabilities_sha256": capabilities_sha,
            },
        )
        token = CancellationToken()
        budget = CooperativeBudget(request.execution.point_budget_seconds, request.execution.run_budget_seconds)
        if _observe_cancellation(layout, run_id, token, journal):
            raise _RunCancelled
        state = transition(state, RunState.RUNNING)
        journal.append("run_started", {"point_count": len(points)})

        for point in points:
            if _observe_cancellation(layout, run_id, token, journal):
                raise _RunCancelled
            journal.append("point_started", {"point_index": point.point_index, "point_id": point.point_id})
            point_started = budget.before_point()
            try:
                command = definition.build_command(prepared, point)
                context = MappingProxyType(
                    {
                        "run_id": run_id,
                        "point_id": point.point_id,
                        "point_seed": point.seed,
                        "monotonic_deadline": point_started + request.execution.point_budget_seconds,
                        "cancellation": token,
                    }
                )
                result = backend.execute_point(command, context)
                budget.after_point(point_started)
                _validate_point_result(result.values)
            except Exception as exc:
                journal.append(
                    "point_failed",
                    {
                        "point_index": point.point_index,
                        "point_id": point.point_id,
                        "error_class": _error_class(exc),
                        "message": _message(exc),
                    },
                )
                raise
            result_payload = {"point_id": point.point_id, "values": dict(result.values)}
            responses.append(float(result.values["response"]))
            journal.append(
                "point_completed",
                {"point_index": point.point_index, "point_id": point.point_id, "result_sha256": _payload_sha(result_payload)},
            )
            if _observe_cancellation(layout, run_id, token, journal):
                raise _RunCancelled

        state = transition(state, RunState.FINALIZING)
        journal.append("run_finalizing", {"completed_point_count": len(responses)})
        dataset = write_response_dataset(staging / "data", responses, point_sha)
        dataset_summary = {
            "dataset_manifest_sha256": dataset_manifest_sha256(staging / "data"),
            "response_sha256": dataset["variables"]["response"]["raw_sha256"],
            "point_count": len(responses),
        }
        journal.append(
            "run_completed",
            {"dataset_manifest_sha256": dataset_summary["dataset_manifest_sha256"], "completed_point_count": len(responses)},
        )
        state = transition(state, RunState.COMPLETED)
        status = "completed"
        journal_summary = journal.close()
        terminal_utc = utc_now_text()
        return _publish_terminal(
            layout=layout,
            root=root,
            staging=staging,
            run_id=run_id,
            status=status,
            created_utc=created_utc,
            terminal_utc=terminal_utc,
            request=request,
            request_sha=request_sha,
            point_sha=point_sha,
            snapshot_hashes=snapshot_hashes,
            capabilities_payload=capabilities_payload,
            capabilities_sha=capabilities_sha,
            journal_summary=journal_summary,
            dataset_summary=dataset_summary,
            elapsed_seconds=time.monotonic() - started,
            resource_lock=resource_lock,
        )
    except _RunCancelled:
        status = "cancelled"
        error_class = "cancelled"
        error_message = "cancellation requested"
    except Exception as exc:
        if journal is not None and journal.closed:
            raise
        status = "failed"
        error_class = _error_class(exc)
        error_message = _message(exc)

    if journal is None:
        raise RuntimeError(error_message)
    if (staging / "data").exists():
        shutil.rmtree(staging / "data")
    if status == "cancelled":
        if state not in {RunState.PREPARED, RunState.RUNNING}:
            status = "failed"
            error_class = "internal"
            error_message = "cancellation occurred outside an allowed state"
        else:
            state = transition(state, RunState.CANCELLED)
            journal.append("run_cancelled", {"completed_point_count": len(responses)})
    if status == "failed":
        if state not in {RunState.FAILED, RunState.COMPLETED, RunState.CANCELLED}:
            state = transition(state, RunState.FAILED)
        journal.append(
            "run_failed",
            {"error_class": error_class, "message": error_message, "completed_point_count": len(responses)},
        )
    journal_summary = journal.close()
    terminal_utc = utc_now_text()
    return _publish_terminal(
        layout=layout,
        root=root,
        staging=staging,
        run_id=run_id,
        status=status,
        created_utc=created_utc,
        terminal_utc=terminal_utc,
        request=request,
        request_sha=request_sha,
        point_sha=point_sha,
        snapshot_hashes=snapshot_hashes,
        capabilities_payload=capabilities_payload,
        capabilities_sha=capabilities_sha,
        journal_summary=journal_summary,
        dataset_summary=None,
        elapsed_seconds=time.monotonic() - started,
        resource_lock=resource_lock,
    )


def request_run_cancellation(output_root: str | Path, run_id: str) -> CancellationReceipt:
    validate_run_id(run_id)
    layout = initialize_layout(output_root)
    if not (layout.locks / f"{run_id}.lock").is_file():
        raise ValueError("run_id is not reserved in this output root")
    if (layout.runs / run_id).exists() or not (layout.staging / run_id).is_dir():
        raise ValueError("cancellation is allowed only for an active staging run")
    payload = {"schema_version": "0.1", "run_id": run_id, "requested_utc": utc_now_text()}
    target = layout.cancellation / f"{run_id}.request"
    request_sha = write_canonical_new(target, payload)
    return CancellationReceipt(run_id, target, request_sha)


def _publish_terminal(
    *,
    layout: RuntimeLayout,
    root: Path,
    staging: Path,
    run_id: str,
    status: str,
    created_utc: str,
    terminal_utc: str,
    request: ExperimentRequest,
    request_sha: str,
    point_sha: str,
    snapshot_hashes: Mapping[str, str],
    capabilities_payload: Mapping[str, Any],
    capabilities_sha: str,
    journal_summary: JournalSummary,
    dataset_summary: Mapping[str, Any] | None,
    elapsed_seconds: float,
    resource_lock: Path,
) -> RunArtifactSet:
    output_relative = layout.root.relative_to(root).as_posix()
    manifest = {
        "schema_version": "0.1",
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
        "events_sha256": journal_summary.raw_sha256,
        "event_tail_sha256": journal_summary.tail_event_sha256,
        "snapshots": dict(snapshot_hashes),
        "backend_capabilities": {**capabilities_payload, "sha256": capabilities_sha},
        "payload_files": inventory_tree(staging),
        "dataset_summary": None if dataset_summary is None else dict(dataset_summary),
    }
    manifest_sha = write_canonical_new(staging / "manifest.json", manifest)
    report = {
        "schema_version": "0.1",
        "artifact_type": "stage_06_experiment_run_verification_report",
        "run_id": run_id,
        "status": status,
        "ok": True,
        "manifest_sha256": manifest_sha,
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "checks": [
            {"name": "payload_inventory_verified", "passed": True},
            {"name": "event_chain_verified", "passed": True},
            {"name": "claim_envelope_verified", "passed": True},
            {"name": "dataset_contract_applied", "passed": True, "message": "verified" if status == "completed" else "not_applicable"},
        ],
        "blocking_reasons": [],
    }
    report_sha = write_canonical_new(staging / "verification_report.json", report)
    dataset_hashes = {}
    if status == "completed":
        dataset_hashes = {
            "dataset.json": _raw_sha256(staging / "data/dataset.json"),
            "response.bin": _raw_sha256(staging / "data/response.bin"),
        }
    receipt = {
        "schema_version": "0.1",
        "artifact_type": "stage_06_experiment_run_receipt",
        "run_id": run_id,
        "status": status,
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "manifest_sha256": manifest_sha,
        "verification_report_sha256": report_sha,
        "event_tail_sha256": journal_summary.tail_event_sha256,
        "elapsed_seconds": elapsed_seconds,
        "published_relative_path": f"{output_relative}/runs/{run_id}",
        "run_lock_sha256": snapshot_hashes["run_lock"],
        "resource_lock_sha256": snapshot_hashes["resource_lock"],
        "dataset_hashes": dataset_hashes,
    }
    receipt_sha = write_canonical_new(staging / "receipt.json", receipt)
    from sqvm.runtime.verify import verify_experiment_run

    preflight = verify_experiment_run(staging, root)
    if not preflight.ok:
        raise ValueError("terminal staging verification failed: " + "; ".join(preflight.blocking_reasons))
    target = layout.runs / run_id
    atomic_publish(staging, target)
    final = verify_experiment_run(target, root)
    if not final.ok:
        raise ValueError("published run verification failed: " + "; ".join(final.blocking_reasons))
    remove_resource_lock(resource_lock)
    catalog_indexed = True
    warning = None
    try:
        index_run(target, layout.root)
    except Exception as exc:
        catalog_indexed = False
        warning = str(exc)
    return RunArtifactSet(
        run_id,
        target,
        target / "manifest.json",
        target / "verification_report.json",
        target / "receipt.json",
        manifest_sha,
        report_sha,
        receipt_sha,
        status,
        catalog_indexed,
        warning,
    )


def _request_payload(request: ExperimentRequest) -> dict[str, Any]:
    return {
        "schema_version": request.schema_version,
        "experiment_id": request.experiment_id,
        "backend_id": request.backend_id,
        "device_snapshot": request.device_snapshot.relative_to(request.repository_root).as_posix(),
        "calibration_snapshot": request.calibration_snapshot.relative_to(request.repository_root).as_posix(),
        "parameters": dict(request.parameters),
        "program": None,
        "scan": {
            "axes": [{"name": axis.name, "unit": axis.unit, "values": list(axis.values)} for axis in request.axes],
            "repetitions": request.repetitions,
        },
        "execution": {
            "seed": request.execution.seed,
            "max_points": request.execution.max_points,
            "point_budget_seconds": request.execution.point_budget_seconds,
            "run_budget_seconds": request.execution.run_budget_seconds,
            "fail_fast": request.execution.fail_fast,
        },
        "publication": {"allow_existing_target": False},
        "claim_envelope": dict(CLAIM_ENVELOPE),
    }


def _resolve_output_root(value: str | Path, root: Path) -> Path:
    text = str(value).replace("\\", "/")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if not text or posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError("output_root must be repository-relative")
    target = root.joinpath(*posix.parts).resolve()
    target.relative_to(root)
    current = root
    for part in posix.parts:
        if current.exists():
            matches = [entry.name for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
            if matches and (len(matches) != 1 or matches[0] != part):
                raise ValueError("output_root has a case-collision ambiguity")
        current /= part
    return target


def _observe_cancellation(layout: RuntimeLayout, run_id: str, token: CancellationToken, journal: EventJournal) -> bool:
    path = layout.cancellation / f"{run_id}.request"
    if not path.exists() or token.requested:
        return token.requested
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if raw != canonical_json_bytes(payload) or set(payload) != {"schema_version", "run_id", "requested_utc"} or payload.get("run_id") != run_id:
        raise ValueError("cancellation request is invalid")
    token.request()
    journal.append("cancel_requested", {"cancellation_request_sha256": hashlib.sha256(raw).hexdigest().upper()})
    return True


def _validate_point_result(values: Mapping[str, Any]) -> None:
    if set(values) != {"response"}:
        raise ValueError("backend result schema is invalid")
    response = values["response"]
    if isinstance(response, bool) or not isinstance(response, float) or not math.isfinite(response):
        raise ValueError("backend response must be finite binary64")


def _payload_sha(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


def _raw_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _error_class(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, FileExistsError):
        return "resource_busy"
    if isinstance(exc, ValueError):
        return "integrity_failure"
    return "backend_failure"


def _message(exc: Exception) -> str:
    value = str(exc).replace("\r", " ").replace("\n", " ")
    return value[:1024] or exc.__class__.__name__


class _RunCancelled(Exception):
    pass
