"""Crash-resumable Runtime 0.3 batches for ordered QCIS circuits."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading
import time
from types import MappingProxyType
from typing import Any, Iterator
import uuid

from sqvm.circuits import (
    CircuitExecutionContext,
    CircuitExecutionProfile,
    CircuitResult,
    DressedPopulations,
    QCISCircuit,
    ReadoutProbabilities,
    compile_circuit,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_bytes, sha256_json
from sqvm.control.stage4_1_models import ParameterizedControlError
from sqvm.runtime.calibration_scan import preflight_calibration_scan_control
from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.lifecycle import CancellationToken
from sqvm.runtime.storage import flush_directory, write_canonical_new


SCHEMA_VERSION = "0.3"
_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_CIRCUITS = 64
_SHA256 = __import__("re").compile(r"[A-F0-9]{64}$")
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_WINDOWS_REPLACE_ERRORS = frozenset({5, 32, 33})


class CircuitBatchError(ValueError):
    """Stable Runtime 0.3 batch-boundary failure."""

    def __init__(
        self,
        code: str,
        status: int,
        detail: str,
        *,
        batch_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.detail = detail
        self.batch_id = batch_id
        self.retry_after = retry_after
        super().__init__(f"{code}: {detail}")


class CircuitBatchCancelledError(CircuitBatchError):
    pass


@dataclass(frozen=True, slots=True)
class CircuitBatchHandle:
    batch_id: str
    root: Path
    request_sha256: str
    head_sha256: str
    status: str
    attempt_count: int
    reused_point_count: int
    results: tuple[CircuitResult, ...]
    metadata: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "root": self.root.as_posix(),
            "request_sha256": self.request_sha256,
            "head_sha256": self.head_sha256,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "reused_point_count": self.reused_point_count,
            "point_count": len(self.results),
            "metadata": copy.deepcopy(dict(self.metadata)),
        }


CircuitRunner = Callable[..., tuple[CircuitResult, ...]]
CircuitResultLoader = Callable[
    [str | Path, CircuitExecutionContext, str | Path | None], CircuitResult
]


def run_circuit_batch(
    circuits: Sequence[QCISCircuit],
    context: CircuitExecutionContext,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    batch_id: str,
    experiment_request: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    coordinator_root: str | Path | None = None,
    readout_qubit: Sequence[Sequence[str]] = ((),),
    point_timeout_s: float = 600.0,
    batch_deadline_s: float = 3600.0,
    max_circuits: int = _MAX_CIRCUITS,
    execution_profile: CircuitExecutionProfile = CircuitExecutionProfile.CALIBRATION_SCAN,
    cancellation_token: CancellationToken | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    circuit_runner: CircuitRunner | None = None,
    result_loader: CircuitResultLoader | None = None,
) -> CircuitBatchHandle:
    """Execute or replay one ordered, crash-resumable circuit batch."""

    identifier = _batch_id(batch_id)
    root = _repository_root(repository_root)
    output = _inside_repository(output_root, root, "batch output")
    coordinator = _inside_repository(
        coordinator_root if coordinator_root is not None else output.parent / ".runtime-v03",
        root,
        "batch coordinator",
    )
    normalized_circuits = _circuits(circuits, max_circuits)
    normalized_readout = _readout(readout_qubit)
    point_timeout = _positive_seconds(point_timeout_s, "point_timeout_s")
    batch_deadline = _positive_seconds(batch_deadline_s, "batch_deadline_s")
    if not isinstance(execution_profile, CircuitExecutionProfile):
        raise CircuitBatchError(
            "invalid_batch_request", 422, "execution_profile is invalid", batch_id=identifier
        )
    if cancellation_token is not None and not isinstance(cancellation_token, CancellationToken):
        raise CircuitBatchError(
            "invalid_batch_request", 422, "cancellation_token is invalid", batch_id=identifier
        )
    if progress_callback is not None and not callable(progress_callback):
        raise CircuitBatchError(
            "invalid_batch_request", 422, "progress_callback is invalid", batch_id=identifier
        )

    # Precompile the complete batch before reserving filesystem state.
    compiled = tuple(compile_circuit(circuit, context) for circuit in normalized_circuits)
    if (
        execution_profile is CircuitExecutionProfile.CALIBRATION_SCAN
        and circuit_runner is None
        and context.platform_configuration is not None
    ):
        control_values = context.platform_configuration.get("control_values")
        if not isinstance(control_values, Mapping):
            raise CircuitBatchError(
                "invalid_batch_request",
                422,
                "Active platform control_values are unavailable",
                batch_id=identifier,
            )
        for value in compiled:
            try:
                preflight_calibration_scan_control(
                    value.compilation,
                    value.circuit.circuit_id,
                    root,
                    idle_flux_phi0=context.idle_flux_phi0,
                    control_values=control_values,
                )
            except ParameterizedControlError as exc:
                raise CircuitBatchError(
                    "circuit_control_preflight_failed",
                    422,
                    f"{value.circuit.circuit_id}: {exc}",
                    batch_id=identifier,
                ) from exc
    context_payload = _context_payload(context)
    resource_key = sha256_json(
        {
            "context": context_payload,
            "execution_profile": execution_profile.value,
        }
    )
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "runtime_v03_circuit_batch_request",
        "artifact_version": SCHEMA_VERSION,
        "batch_id": identifier,
        "experiment_request": _plain_mapping(experiment_request, "experiment_request"),
        "circuits": [
            {
                "point_index": index,
                "circuit_id": value.circuit.circuit_id,
                "circuit_sha256": value.circuit_sha256,
                "qcis_source": value.circuit.source,
            }
            for index, value in enumerate(compiled)
        ],
        "context": context_payload,
        "execution": {
            "readout_qubit": [list(group) for group in normalized_readout],
            "point_timeout_s": point_timeout,
            "batch_deadline_s": batch_deadline,
            "max_circuits": max_circuits,
            "execution_profile": execution_profile.value,
        },
        "resource_key": resource_key,
    }
    request_sha256 = sha256_json(semantic)
    request_payload = {
        **semantic,
        "metadata": _plain_mapping(metadata or {}, "metadata"),
        "request_sha256": request_sha256,
    }
    runner, loader = _execution_functions(circuit_runner, result_loader)
    _ensure_directory(output, _storage_boundary(output, root), "batch output")
    _ensure_coordinator(coordinator, _storage_boundary(coordinator, root))
    batch_lock = coordinator / "locks" / "batches" / f"{identifier}.lock"
    resource_lock = coordinator / "locks" / "resources" / f"{resource_key}.lock"

    with _file_lock(batch_lock, identifier), _file_lock(resource_lock, identifier):
        return _run_locked(
            compiled,
            context,
            output,
            root,
            coordinator,
            request_payload,
            normalized_readout,
            point_timeout,
            batch_deadline,
            max_circuits,
            execution_profile,
            cancellation_token,
            progress_callback,
            runner,
            loader,
        )


def verify_circuit_batch(
    output_root: str | Path,
    context: CircuitExecutionContext | None = None,
    repository_root: str | Path | None = None,
    *,
    expected_batch_id: str | None = None,
) -> CircuitBatchHandle:
    """Reopen one completed batch without executing a circuit."""

    root = _repository_root(repository_root)
    output = _inside_repository(output_root, root, "batch output")
    batch = output / "batch"
    _safe_directory(batch, "batch metadata")
    _exact_children(batch, {"request.json", "head.json", "points"}, {"points"})
    request = _load_canonical(batch / "request.json", "batch request")
    identifier = _stored_batch_id(request.get("batch_id"))
    if expected_batch_id is not None and identifier != _batch_id(expected_batch_id):
        raise _recovery(identifier, "batch identity differs from the expected batch")
    _validate_request(request, identifier)
    if context is not None and request["context"] != _context_payload(context):
        raise _recovery(identifier, "batch execution context differs")
    head = _load_head(batch / "head.json", identifier, request["request_sha256"])
    if head["status"] != "completed":
        raise _recovery(identifier, "batch is not completed")
    circuits = request["circuits"]
    completed = head["completed_points"]
    if len(completed) != len(circuits):
        raise _recovery(identifier, "completed batch point count differs")
    _exact_point_receipts(batch / "points", circuits, identifier)
    results = tuple(
        _load_point_receipt(output, batch, request, row, expected_index=index)
        for index, row in enumerate(completed)
    )
    _assert_result_order(request, results, identifier)
    return CircuitBatchHandle(
        identifier,
        output,
        request["request_sha256"],
        _raw_sha(batch / "head.json"),
        "completed",
        head["attempt_count"],
        len(results),
        results,
        MappingProxyType(copy.deepcopy(dict(request["metadata"]))),
    )


def request_circuit_batch_cancellation(
    coordinator_root: str | Path,
    batch_id: str,
    repository_root: str | Path | None = None,
) -> Path:
    """Persist one idempotent cooperative cancellation request."""

    identifier = _batch_id(batch_id)
    root = _repository_root(repository_root)
    coordinator = _inside_repository(coordinator_root, root, "batch coordinator")
    _ensure_coordinator(coordinator, _storage_boundary(coordinator, root))
    target = coordinator / "cancellation" / f"{identifier}.json"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "runtime_v03_batch_cancellation_request",
        "artifact_version": SCHEMA_VERSION,
        "batch_id": identifier,
        "requested_utc": utc_now_text(),
    }
    if target.exists():
        existing = _load_canonical(target, "batch cancellation request")
        if (
            set(existing) != set(payload)
            or existing.get("schema_version") != SCHEMA_VERSION
            or existing.get("artifact_type") != payload["artifact_type"]
            or existing.get("artifact_version") != SCHEMA_VERSION
            or existing.get("batch_id") != identifier
            or not isinstance(existing.get("requested_utc"), str)
        ):
            raise _recovery(identifier, "batch cancellation request is invalid")
        return target
    write_canonical_new(target, payload)
    return target


def _run_locked(
    compiled: Sequence[Any],
    context: CircuitExecutionContext,
    output: Path,
    repository_root: Path,
    coordinator: Path,
    request_payload: Mapping[str, Any],
    readout: tuple[tuple[str, ...], ...],
    point_timeout_s: float,
    batch_deadline_s: float,
    max_circuits: int,
    execution_profile: CircuitExecutionProfile,
    cancellation_token: CancellationToken | None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    runner: CircuitRunner,
    loader: CircuitResultLoader,
) -> CircuitBatchHandle:
    identifier = str(request_payload["batch_id"])
    batch = output / "batch"
    request_path = batch / "request.json"
    head_path = batch / "head.json"
    if batch.exists():
        _safe_directory(batch, "batch metadata")
        existing = _load_canonical(request_path, "batch request")
        _validate_request(existing, identifier)
        if existing["request_sha256"] != request_payload["request_sha256"]:
            raise CircuitBatchError(
                "batch_idempotency_conflict",
                409,
                "batch ID is already bound to a different request",
                batch_id=identifier,
            )
        request = existing
        head = _load_head(head_path, identifier, request["request_sha256"])
    else:
        if any(output.iterdir()):
            raise _recovery(identifier, "batch output contains unbound entries")
        batch.mkdir()
        (batch / "points").mkdir()
        write_canonical_new(request_path, request_payload)
        request = copy.deepcopy(dict(request_payload))
        head = _initial_head(identifier, request["request_sha256"])
        _write_head(head_path, head)

    if len(head["completed_points"]) > len(request["circuits"]):
        raise _recovery(identifier, "batch head contains too many completed points")

    if head["status"] == "completed":
        return verify_circuit_batch(
            output, context, repository_root, expected_batch_id=identifier
        )
    if head["status"] == "cancelled":
        raise CircuitBatchCancelledError(
            "batch_cancelled", 409, "batch was cancelled", batch_id=identifier
        )
    results = [
        _load_point_receipt(output, batch, request, row, expected_index=index)
        for index, row in enumerate(head["completed_points"])
    ]
    _assert_result_order(request, results, identifier)
    reused = len(results)
    if _cancel_requested(coordinator, identifier, cancellation_token):
        head = _terminal_head(head, "cancelled", "batch_cancelled", "cancellation requested")
        _write_head(head_path, head)
        raise CircuitBatchCancelledError(
            "batch_cancelled", 409, "batch cancellation requested", batch_id=identifier
        )
    head = {
        **head,
        "status": "running",
        "attempt_count": head["attempt_count"] + 1,
        "failure": None,
        "updated_utc": utc_now_text(),
    }
    _write_head(head_path, head)
    started = time.monotonic()
    try:
        for index in range(len(results), len(compiled)):
            if _cancel_requested(coordinator, identifier, cancellation_token):
                raise CircuitBatchCancelledError(
                    "batch_cancelled", 409, "batch cancellation requested", batch_id=identifier
                )
            remaining = batch_deadline_s - (time.monotonic() - started)
            if remaining <= 0.0:
                raise CircuitBatchError(
                    "batch_deadline_exceeded", 408, "batch deadline exceeded", batch_id=identifier
                )
            value = compiled[index]
            _progress(
                progress_callback,
                "circuit_started",
                identifier,
                index,
                len(compiled),
                value.circuit.circuit_id,
                reused=False,
            )
            result = _adopt_or_execute(
                output,
                repository_root,
                value,
                context,
                readout,
                min(point_timeout_s, remaining),
                execution_profile,
                runner,
                loader,
            )
            receipt_path = batch / "points" / f"{value.circuit.circuit_id}.json"
            receipt = _point_receipt(output, identifier, index, value.circuit, result)
            if receipt_path.exists():
                raise _recovery(identifier, "point receipt already exists outside the batch head")
            receipt_sha = write_canonical_new(receipt_path, receipt)
            completed = [
                *head["completed_points"],
                {
                    "point_index": index,
                    "circuit_id": value.circuit.circuit_id,
                    "receipt_path": f"points/{value.circuit.circuit_id}.json",
                    "receipt_raw_sha256": receipt_sha,
                },
            ]
            head = {
                **head,
                "generation": head["generation"] + 1,
                "completed_points": completed,
                "updated_utc": utc_now_text(),
            }
            _write_head(head_path, head)
            results.append(result)
            _progress(
                progress_callback,
                "circuit_completed",
                identifier,
                index + 1,
                len(compiled),
                value.circuit.circuit_id,
                reused=False,
            )
            if time.monotonic() - started > batch_deadline_s:
                raise CircuitBatchError(
                    "batch_deadline_exceeded", 408, "batch deadline exceeded", batch_id=identifier
                )
        head = {
            **head,
            "status": "completed",
            "failure": None,
            "updated_utc": utc_now_text(),
        }
        _write_head(head_path, head)
    except CircuitBatchCancelledError as exc:
        _write_head(
            head_path,
            _terminal_head(head, "cancelled", exc.code, exc.detail),
        )
        raise
    except CircuitBatchError as exc:
        status = "deadline_exceeded" if exc.code == "batch_deadline_exceeded" else "interrupted"
        _write_head(head_path, _terminal_head(head, status, exc.code, exc.detail))
        raise
    except BaseException as exc:
        _write_head(
            head_path,
            _terminal_head(head, "interrupted", "point_execution_failed", _message(exc)),
        )
        raise

    verified = verify_circuit_batch(
        output, context, repository_root, expected_batch_id=identifier
    )
    return CircuitBatchHandle(
        verified.batch_id,
        verified.root,
        verified.request_sha256,
        verified.head_sha256,
        verified.status,
        verified.attempt_count,
        reused,
        verified.results,
        verified.metadata,
    )


def _adopt_or_execute(
    output: Path,
    repository_root: Path,
    compiled: Any,
    context: CircuitExecutionContext,
    readout: tuple[tuple[str, ...], ...],
    timeout_s: float,
    execution_profile: CircuitExecutionProfile,
    runner: CircuitRunner,
    loader: CircuitResultLoader,
) -> CircuitResult:
    evidence = output / "circuit_execution" / compiled.circuit.circuit_id
    model = output / compiled.circuit.circuit_id
    if os.path.lexists(evidence) or os.path.lexists(model):
        if not evidence.is_dir() or not model.is_dir():
            raise _recovery(compiled.circuit.circuit_id, "orphan point evidence is incomplete")
        try:
            result = loader(evidence, context, repository_root)
        except Exception as exc:
            raise _recovery(
                compiled.circuit.circuit_id,
                "orphan point evidence cannot be verified",
            ) from exc
    else:
        values = runner(
            (compiled.circuit,),
            context,
            output,
            repository_root,
            readout_qubit=readout,
            timeout_s=timeout_s,
            max_circuits=1,
            execution_profile=execution_profile,
            progress_callback=None,
        )
        if not isinstance(values, tuple) or len(values) != 1:
            raise _recovery(compiled.circuit.circuit_id, "circuit runner result count is invalid")
        result = values[0]
    if (
        not isinstance(result, CircuitResult)
        or result.circuit_id != compiled.circuit.circuit_id
        or result.circuit_sha256 != compiled.circuit_sha256
    ):
        raise _recovery(compiled.circuit.circuit_id, "circuit runner result identity is invalid")
    return result


def _point_receipt(
    output: Path,
    batch_id: str,
    point_index: int,
    circuit: QCISCircuit,
    result: CircuitResult,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "runtime_v03_circuit_point_receipt",
        "artifact_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "point_index": point_index,
        "circuit_id": circuit.circuit_id,
        "circuit_sha256": sha256_bytes(circuit.source.encode("utf-8")),
        "result": _result_payload(output, result),
        "evidence": [
            _evidence_binding(output, "circuit", result.evidence_root),
            _evidence_binding(output, "model", result.model_evidence_root),
        ],
    }


def _result_payload(output: Path, result: CircuitResult) -> dict[str, Any]:
    return {
        "circuit_id": result.circuit_id,
        "circuit_sha256": result.circuit_sha256,
        "executable_qcis_sha256": result.executable_qcis_sha256,
        "overlay_sha256": result.overlay_sha256,
        "dressed_populations": {
            "population_000": result.dressed_populations.population_000,
            "population_100": result.dressed_populations.population_100,
            "population_001": result.dressed_populations.population_001,
            "population_101": result.dressed_populations.population_101,
        },
        "readout_qubit": [list(group) for group in result.readout_qubit],
        "readout_probabilities": [
            {
                "qagents": list(value.qagents),
                "probabilities": dict(value.probabilities),
            }
            for value in result.readout_probabilities
        ],
        "leakage": result.leakage,
        "norm_error": result.norm_error,
        "evidence_root": _relative_directory(output, result.evidence_root, "circuit evidence"),
        "model_evidence_root": _relative_directory(
            output, result.model_evidence_root, "model evidence"
        ),
        "receipt_sha256": result.receipt_sha256,
        "qualification_scope": result.qualification_scope,
    }


def _load_point_receipt(
    output: Path,
    batch: Path,
    request: Mapping[str, Any],
    row: Mapping[str, Any],
    *,
    expected_index: int,
) -> CircuitResult:
    identifier = str(request["batch_id"])
    _exact(
        row,
        {"point_index", "circuit_id", "receipt_path", "receipt_raw_sha256"},
        identifier,
        "completed point",
    )
    circuit = request["circuits"][expected_index]
    expected_path = f"points/{circuit['circuit_id']}.json"
    if (
        row.get("point_index") != expected_index
        or row.get("circuit_id") != circuit["circuit_id"]
        or row.get("receipt_path") != expected_path
        or not _is_hash(row.get("receipt_raw_sha256"))
    ):
        raise _recovery(identifier, "completed point identity is invalid")
    receipt_path = _inside(batch, expected_path, identifier, "point receipt")
    if _raw_sha(receipt_path) != row["receipt_raw_sha256"]:
        raise _recovery(identifier, "point receipt hash differs")
    receipt = _load_canonical(receipt_path, "point receipt")
    _exact(
        receipt,
        {
            "schema_version",
            "artifact_type",
            "artifact_version",
            "batch_id",
            "point_index",
            "circuit_id",
            "circuit_sha256",
            "result",
            "evidence",
        },
        identifier,
        "point receipt",
    )
    if (
        receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("artifact_type") != "runtime_v03_circuit_point_receipt"
        or receipt.get("artifact_version") != SCHEMA_VERSION
        or receipt.get("batch_id") != identifier
        or receipt.get("point_index") != expected_index
        or receipt.get("circuit_id") != circuit["circuit_id"]
        or receipt.get("circuit_sha256") != circuit["circuit_sha256"]
    ):
        raise _recovery(identifier, "point receipt identity is invalid")
    evidence = receipt.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != 2:
        raise _recovery(identifier, "point evidence bindings are invalid")
    for expected_kind, binding in zip(("circuit", "model"), evidence, strict=True):
        _validate_evidence_binding(output, binding, expected_kind, identifier)
    return _result_from_payload(output, receipt.get("result"), identifier)


def _result_from_payload(output: Path, value: Any, batch_id: str) -> CircuitResult:
    if not isinstance(value, Mapping):
        raise _recovery(batch_id, "point result is invalid")
    expected = {
        "circuit_id",
        "circuit_sha256",
        "executable_qcis_sha256",
        "overlay_sha256",
        "dressed_populations",
        "readout_qubit",
        "readout_probabilities",
        "leakage",
        "norm_error",
        "evidence_root",
        "model_evidence_root",
        "receipt_sha256",
        "qualification_scope",
    }
    _exact(value, expected, batch_id, "point result")
    dressed = value.get("dressed_populations")
    if not isinstance(dressed, Mapping) or set(dressed) != {
        "population_000",
        "population_100",
        "population_001",
        "population_101",
    }:
        raise _recovery(batch_id, "dressed populations are invalid")
    numbers = [*dressed.values(), value.get("leakage"), value.get("norm_error")]
    if any(not _finite_number(item) for item in numbers):
        raise _recovery(batch_id, "point result contains a non-finite number")
    readout = value.get("readout_qubit")
    probabilities = value.get("readout_probabilities")
    if not isinstance(readout, list) or not isinstance(probabilities, list):
        raise _recovery(batch_id, "point readout result is invalid")
    readout_groups = _readout(readout)
    restored_probabilities = []
    for row in probabilities:
        if not isinstance(row, Mapping) or set(row) != {"qagents", "probabilities"}:
            raise _recovery(batch_id, "point readout probabilities are invalid")
        qagents = row["qagents"]
        values = row["probabilities"]
        if (
            not isinstance(qagents, list)
            or not isinstance(values, Mapping)
            or any(not isinstance(key, str) or not _finite_number(item) for key, item in values.items())
        ):
            raise _recovery(batch_id, "point readout probabilities are invalid")
        restored_probabilities.append(
            ReadoutProbabilities(tuple(qagents), MappingProxyType(dict(values)))
        )
    for name in ("circuit_sha256", "executable_qcis_sha256", "overlay_sha256", "receipt_sha256"):
        if not _is_hash(value.get(name)):
            raise _recovery(batch_id, f"point result {name} is invalid")
    if not isinstance(value.get("circuit_id"), str) or not isinstance(
        value.get("qualification_scope"), str
    ):
        raise _recovery(batch_id, "point result strings are invalid")
    return CircuitResult(
        value["circuit_id"],
        value["circuit_sha256"],
        value["executable_qcis_sha256"],
        value["overlay_sha256"],
        DressedPopulations(
            float(dressed["population_000"]),
            float(dressed["population_100"]),
            float(dressed["population_001"]),
            float(dressed["population_101"]),
        ),
        readout_groups,
        tuple(restored_probabilities),
        float(value["leakage"]),
        float(value["norm_error"]),
        _inside(output, value["evidence_root"], batch_id, "circuit evidence"),
        _inside(output, value["model_evidence_root"], batch_id, "model evidence"),
        value["receipt_sha256"],
        value["qualification_scope"],
    )


def _evidence_binding(output: Path, kind: str, evidence_root: Path) -> dict[str, Any]:
    relative = _relative_directory(output, evidence_root, f"{kind} evidence")
    return {
        "kind": kind,
        "path": relative,
        "files": _safe_inventory(_inside(output, relative, kind, f"{kind} evidence")),
    }


def _validate_evidence_binding(
    output: Path, value: Any, kind: str, batch_id: str
) -> None:
    if not isinstance(value, Mapping) or set(value) != {"kind", "path", "files"}:
        raise _recovery(batch_id, "point evidence binding fields are invalid")
    if value.get("kind") != kind or not isinstance(value.get("path"), str):
        raise _recovery(batch_id, "point evidence binding identity is invalid")
    path = _inside(output, value["path"], batch_id, f"{kind} evidence")
    if value.get("files") != _safe_inventory(path):
        raise _recovery(batch_id, f"{kind} evidence inventory differs")


def _safe_inventory(root: Path) -> list[dict[str, Any]]:
    _safe_directory(root, "point evidence")
    rows: list[dict[str, Any]] = []
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in sorted(directories):
            _safe_directory(parent / name, "point evidence directory")
        for name in sorted(files):
            path = parent / name
            info = os.lstat(path)
            if (
                not stat.S_ISREG(info.st_mode)
                or path.is_symlink()
                or info.st_nlink != 1
            ):
                raise CircuitBatchError(
                    "batch_recovery_required", 503, "point evidence file is unsafe"
                )
            raw = path.read_bytes()
            rows.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "byte_length": len(raw),
                    "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
                }
            )
    rows.sort(key=lambda row: row["path"])
    return rows


def _initial_head(batch_id: str, request_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "runtime_v03_circuit_batch_head",
        "artifact_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "request_sha256": request_sha256,
        "generation": 0,
        "attempt_count": 0,
        "status": "prepared",
        "completed_points": [],
        "failure": None,
        "updated_utc": utc_now_text(),
    }


def _terminal_head(
    head: Mapping[str, Any], status: str, code: str, detail: str
) -> dict[str, Any]:
    return {
        **head,
        "status": status,
        "failure": {"code": code, "detail": detail[:1024]},
        "updated_utc": utc_now_text(),
    }


def _load_head(path: Path, batch_id: str, request_sha256: str) -> dict[str, Any]:
    value = dict(_load_canonical(path, "batch head"))
    _exact(
        value,
        {
            "schema_version",
            "artifact_type",
            "artifact_version",
            "batch_id",
            "request_sha256",
            "generation",
            "attempt_count",
            "status",
            "completed_points",
            "failure",
            "updated_utc",
        },
        batch_id,
        "batch head",
    )
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != "runtime_v03_circuit_batch_head"
        or value.get("artifact_version") != SCHEMA_VERSION
        or value.get("batch_id") != batch_id
        or value.get("request_sha256") != request_sha256
        or type(value.get("generation")) is not int
        or type(value.get("attempt_count")) is not int
        or value["generation"] < 0
        or value["attempt_count"] < 0
        or value.get("status")
        not in {"prepared", "running", "completed", "interrupted", "deadline_exceeded", "cancelled"}
        or not isinstance(value.get("completed_points"), list)
        or value["generation"] != len(value["completed_points"])
        or not isinstance(value.get("updated_utc"), str)
    ):
        raise _recovery(batch_id, "batch head identity is invalid")
    failure = value.get("failure")
    if failure is not None and (
        not isinstance(failure, Mapping)
        or set(failure) != {"code", "detail"}
        or not all(isinstance(failure.get(name), str) for name in ("code", "detail"))
    ):
        raise _recovery(batch_id, "batch head failure is invalid")
    terminal_failure = value["status"] in {
        "interrupted",
        "deadline_exceeded",
        "cancelled",
    }
    if terminal_failure != (failure is not None):
        raise _recovery(batch_id, "batch head failure does not match its status")
    return value


def _validate_request(value: Mapping[str, Any], batch_id: str) -> None:
    expected = {
        "schema_version",
        "artifact_type",
        "artifact_version",
        "batch_id",
        "experiment_request",
        "circuits",
        "context",
        "execution",
        "resource_key",
        "metadata",
        "request_sha256",
    }
    _exact(value, expected, batch_id, "batch request")
    semantic = {key: copy.deepcopy(item) for key, item in value.items() if key not in {"metadata", "request_sha256"}}
    if (
        value.get("schema_version") != SCHEMA_VERSION
        or value.get("artifact_type") != "runtime_v03_circuit_batch_request"
        or value.get("artifact_version") != SCHEMA_VERSION
        or value.get("batch_id") != batch_id
        or not isinstance(value.get("experiment_request"), Mapping)
        or not isinstance(value.get("circuits"), list)
        or not value["circuits"]
        or not isinstance(value.get("context"), Mapping)
        or not isinstance(value.get("execution"), Mapping)
        or not isinstance(value.get("metadata"), Mapping)
        or not _is_hash(value.get("resource_key"))
        or value.get("request_sha256") != sha256_json(semantic)
    ):
        raise _recovery(batch_id, "batch request identity is invalid")
    for index, circuit in enumerate(value["circuits"]):
        if (
            not isinstance(circuit, Mapping)
            or set(circuit) != {"point_index", "circuit_id", "circuit_sha256", "qcis_source"}
            or circuit.get("point_index") != index
            or not isinstance(circuit.get("circuit_id"), str)
            or not isinstance(circuit.get("qcis_source"), str)
            or circuit.get("circuit_sha256")
            != sha256_bytes(circuit["qcis_source"].encode("utf-8"))
        ):
            raise _recovery(batch_id, "batch circuit table is invalid")


def _context_payload(context: CircuitExecutionContext) -> dict[str, Any]:
    return {
        "platform_snapshot_id": context.platform_snapshot_id,
        "platform_snapshot_content_sha256": context.platform_snapshot_content_sha256,
        "authority_context_sha256": context.authority_context_sha256,
        "authorities_sha256": sha256_json(_plain(context.authorities)),
        "idle_flux_phi0": {
            key: float(value) for key, value in sorted(context.idle_flux_phi0.items())
        },
        "settable_paths_sha256": sha256_json(sorted(context.settable_paths)),
        "initial_state_id": context.initial_state_id,
        "observable_set_id": context.observable_set_id,
        "calibration_model_configuration_sha256": (
            sha256_json(_plain(context.calibration_model_configuration))
            if context.calibration_model_configuration is not None
            else None
        ),
        "platform_configuration_sha256": (
            sha256_json(_plain(context.platform_configuration))
            if context.platform_configuration is not None
            else None
        ),
    }


def _assert_result_order(
    request: Mapping[str, Any], results: Sequence[CircuitResult], batch_id: str
) -> None:
    if len(results) > len(request["circuits"]):
        raise _recovery(batch_id, "batch result count exceeds the request")
    for circuit, result in zip(request["circuits"], results):
        if (
            result.circuit_id != circuit["circuit_id"]
            or result.circuit_sha256 != circuit["circuit_sha256"]
        ):
            raise _recovery(batch_id, "batch result order or identity differs")


def _exact_point_receipts(
    points: Path, circuits: Sequence[Mapping[str, Any]], batch_id: str
) -> None:
    _safe_directory(points, "batch point receipts")
    expected = {f"{value['circuit_id']}.json" for value in circuits}
    actual: set[str] = set()
    for entry in os.scandir(points):
        if not entry.is_file(follow_symlinks=False):
            raise _recovery(batch_id, f"unknown point receipt entry: {entry.name}")
        actual.add(entry.name)
    if actual != expected:
        raise _recovery(batch_id, "batch point receipt set differs")


def _execution_functions(
    runner: CircuitRunner | None, loader: CircuitResultLoader | None
) -> tuple[CircuitRunner, CircuitResultLoader]:
    if runner is None or loader is None:
        from sqvm.circuits import run_circuits, verify_circuit_result

        runner = runner or run_circuits
        loader = loader or verify_circuit_result
    if not callable(runner) or not callable(loader):
        raise CircuitBatchError(
            "invalid_batch_request", 422, "batch execution callbacks are invalid"
        )
    return runner, loader


def _circuits(values: Sequence[QCISCircuit], maximum: int) -> tuple[QCISCircuit, ...]:
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, Sequence)
        or not values
        or any(not isinstance(value, QCISCircuit) for value in values)
    ):
        raise CircuitBatchError(
            "invalid_batch_request", 422, "circuits must be a nonempty QCISCircuit sequence"
        )
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= _MAX_CIRCUITS:
        raise CircuitBatchError(
            "invalid_batch_request", 422, "max_circuits must be in [1, 64]"
        )
    if len(values) > maximum:
        raise CircuitBatchError(
            "invalid_batch_request", 422, "circuit count exceeds max_circuits"
        )
    ids = [value.circuit_id for value in values]
    if len(ids) != len(set(ids)):
        raise CircuitBatchError(
            "invalid_batch_request", 422, "circuit IDs must be unique"
        )
    return tuple(values)


def _readout(value: Sequence[Sequence[str]]) -> tuple[tuple[str, ...], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise CircuitBatchError(
            "invalid_batch_request", 422, "readout_qubit must be a nonempty sequence"
        )
    rows = []
    for group in value:
        if isinstance(group, (str, bytes)) or not isinstance(group, Sequence):
            raise CircuitBatchError(
                "invalid_batch_request", 422, "readout_qubit group is invalid"
            )
        row = tuple(group)
        if any(not isinstance(item, str) or not item for item in row):
            raise CircuitBatchError(
                "invalid_batch_request", 422, "readout_qubit target is invalid"
            )
        rows.append(row)
    return tuple(rows)


def _plain_mapping(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CircuitBatchError("invalid_batch_request", 422, f"{label} must be an object")
    result = _plain(value)
    try:
        canonical_json_bytes(result)
    except (TypeError, ValueError) as exc:
        raise CircuitBatchError(
            "invalid_batch_request", 422, f"{label} is not canonical JSON data"
        ) from exc
    return result


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, bool, int)) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite value")
        return value
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _positive_seconds(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CircuitBatchError("invalid_batch_request", 422, f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise CircuitBatchError("invalid_batch_request", 422, f"{label} must be positive")
    return result


def _cancel_requested(
    coordinator: Path,
    batch_id: str,
    token: CancellationToken | None,
) -> bool:
    if token is not None and token.requested:
        return True
    path = coordinator / "cancellation" / f"{batch_id}.json"
    if not path.exists():
        return False
    payload = _load_canonical(path, "batch cancellation request")
    if (
        set(payload)
        != {"schema_version", "artifact_type", "artifact_version", "batch_id", "requested_utc"}
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("artifact_type") != "runtime_v03_batch_cancellation_request"
        or payload.get("artifact_version") != SCHEMA_VERSION
        or payload.get("batch_id") != batch_id
        or not isinstance(payload.get("requested_utc"), str)
    ):
        raise _recovery(batch_id, "batch cancellation request is invalid")
    return True


def _progress(
    callback: Callable[[Mapping[str, Any]], None] | None,
    event: str,
    batch_id: str,
    completed: int,
    total: int,
    circuit_id: str,
    *,
    reused: bool,
) -> None:
    if callback is not None:
        callback(
            MappingProxyType(
                {
                    "event": event,
                    "batch_id": batch_id,
                    "completed": completed,
                    "total": total,
                    "circuit_id": circuit_id,
                    "reused": reused,
                }
            )
        )


def _write_head(path: Path, payload: Mapping[str, Any]) -> None:
    raw = canonical_json_bytes(payload)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        delay = 0.01
        for attempt in range(25):
            try:
                os.replace(temporary, path)
                break
            except OSError as exc:
                code = getattr(exc, "winerror", None) or exc.errno
                if os.name != "nt" or code not in _WINDOWS_REPLACE_ERRORS or attempt == 24:
                    raise
                time.sleep(delay)
                delay = min(delay * 2.0, 0.1)
        flush_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _file_lock(path: Path, batch_id: str) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe_directory(path.parent, "batch lock directory")
    key = str(path.resolve()).casefold()
    with _PROCESS_LOCKS_GUARD:
        process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
    if not process_lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS):
        raise CircuitBatchError(
            "batch_busy", 503, "batch resource is busy", batch_id=batch_id, retry_after=1
        )
    stream = None
    try:
        stream = path.open("a+b")
        _acquire_os_lock(stream, batch_id)
        yield
    finally:
        if stream is not None:
            try:
                _release_os_lock(stream)
            finally:
                stream.close()
        process_lock.release()


def _acquire_os_lock(stream: Any, batch_id: str) -> None:
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    if os.name == "nt":
        import msvcrt

        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise CircuitBatchError(
                        "batch_busy", 503, "batch resource is busy", batch_id=batch_id, retry_after=1
                    )
                time.sleep(0.02)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CircuitBatchError(
                        "batch_busy", 503, "batch resource is busy", batch_id=batch_id, retry_after=1
                    )
                time.sleep(0.02)


def _release_os_lock(stream: Any) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _ensure_coordinator(path: Path, root: Path) -> None:
    _ensure_directory(path, root, "batch coordinator")
    for relative in ("locks", "locks/batches", "locks/resources", "cancellation"):
        _ensure_directory(path / relative, root, "batch coordinator directory")


def _ensure_directory(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CircuitBatchError("invalid_batch_request", 422, f"{label} escapes repository") from exc
    path.mkdir(parents=True, exist_ok=True)
    cursor = path
    while True:
        if cursor.exists():
            _safe_directory(cursor, label)
        if cursor == root:
            return
        cursor = cursor.parent


def _safe_directory(path: Path, label: str) -> None:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise CircuitBatchError("batch_recovery_required", 503, f"cannot inspect {label}") from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.is_symlink()
        or bool(getattr(path, "is_junction", lambda: False)())
    ):
        raise CircuitBatchError("batch_recovery_required", 503, f"{label} is unsafe")


def _exact_children(root: Path, allowed: set[str], directories: set[str]) -> None:
    for entry in os.scandir(root):
        if entry.name not in allowed or entry.is_dir(follow_symlinks=False) != (entry.name in directories):
            raise CircuitBatchError(
                "batch_recovery_required", 503, f"unknown batch entry: {entry.name}"
            )


def _load_canonical(path: Path, label: str) -> Mapping[str, Any]:
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_nlink != 1:
            raise ValueError("unsafe file")
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CircuitBatchError("batch_recovery_required", 503, f"cannot load {label}") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise CircuitBatchError("batch_recovery_required", 503, f"{label} is not canonical JSON")
    return value


def _inside(base: Path, relative: Any, batch_id: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise _recovery(batch_id, f"{label} path is invalid")
    candidate = Path(relative)
    if candidate.is_absolute() or candidate.as_posix() != relative or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise _recovery(batch_id, f"{label} path is unsafe")
    path = (base / candidate).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise _recovery(batch_id, f"{label} path escapes batch root") from exc
    return path


def _relative_directory(base: Path, path: Path, label: str) -> str:
    resolved = Path(path).resolve()
    try:
        relative = resolved.relative_to(base.resolve()).as_posix()
    except ValueError as exc:
        raise CircuitBatchError("batch_recovery_required", 503, f"{label} escapes batch output") from exc
    _safe_directory(resolved, label)
    return relative


def _inside_repository(value: str | Path, root: Path, label: str) -> Path:
    path = Path(value)
    try:
        resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    except (OSError, RuntimeError) as exc:
        raise CircuitBatchError("invalid_batch_request", 422, f"{label} is invalid") from exc
    return resolved


def _storage_boundary(path: Path, repository_root: Path) -> Path:
    try:
        path.relative_to(repository_root)
    except ValueError:
        return Path(path.anchor)
    return repository_root


def _repository_root(value: str | Path | None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]


def _batch_id(value: Any) -> str:
    if not isinstance(value, str):
        raise CircuitBatchError("invalid_batch_id", 422, "batch ID must be a canonical UUID4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise CircuitBatchError("invalid_batch_id", 422, "batch ID must be a canonical UUID4") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise CircuitBatchError("invalid_batch_id", 422, "batch ID must be a canonical UUID4")
    return value


def _stored_batch_id(value: Any) -> str:
    try:
        return _batch_id(value)
    except CircuitBatchError as exc:
        raise _recovery(None, "stored batch ID is invalid") from exc


def _exact(
    value: Mapping[str, Any], keys: set[str], batch_id: str | None, label: str
) -> None:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise _recovery(batch_id, f"{label} fields are invalid")


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _message(exc: BaseException) -> str:
    return str(exc).replace("\r", " ").replace("\n", " ")[:1024] or exc.__class__.__name__


def _recovery(batch_id: str | None, detail: str) -> CircuitBatchError:
    return CircuitBatchError("batch_recovery_required", 503, detail, batch_id=batch_id)


__all__ = [
    "CircuitBatchCancelledError",
    "CircuitBatchError",
    "CircuitBatchHandle",
    "request_circuit_batch_cancellation",
    "run_circuit_batch",
    "verify_circuit_batch",
]
