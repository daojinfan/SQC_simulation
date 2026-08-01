from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from types import MappingProxyType
import uuid

import pytest

from sqvm.circuits import CircuitResult, DressedPopulations, QCISCircuit
from sqvm.control.stage4_1_models import (
    ParameterizedControlError,
    ParameterizedControlReasonCode,
)
from sqvm.qcis.canonical import sha256_bytes
from sqvm.runtime.batch import (
    CircuitBatchCancelledError,
    CircuitBatchError,
    request_circuit_batch_cancellation,
    run_circuit_batch,
    verify_circuit_batch,
)
from sqvm.runtime.lifecycle import CancellationToken
import sqvm.runtime.batch as batch_module
from tests.support.contexts import circuit_execution_context


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.integration


def _circuits(count: int = 3) -> tuple[QCISCircuit, ...]:
    return tuple(
        QCISCircuit(
            f"runtime_point_{index}",
            f"PLSXY Q1 0 -1 2 0.001 {5.0 + index * 0.01} 0 0 2\n",
        )
        for index in range(count)
    )


def _result(circuit: QCISCircuit, output_root: Path) -> CircuitResult:
    circuit_evidence = output_root / "circuit_execution" / circuit.circuit_id
    model_evidence = output_root / circuit.circuit_id
    circuit_evidence.mkdir(parents=True)
    model_evidence.mkdir(parents=True)
    (circuit_evidence / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
    (model_evidence / "model.bin").write_bytes(circuit.source.encode("ascii"))
    return CircuitResult(
        circuit_id=circuit.circuit_id,
        circuit_sha256=sha256_bytes(circuit.source.encode("utf-8")),
        executable_qcis_sha256="B" * 64,
        overlay_sha256="C" * 64,
        dressed_populations=DressedPopulations(0.8, 0.19, 0.0, 0.0),
        readout_qubit=(("Q1",),),
        readout_probabilities=(),
        leakage=0.01,
        norm_error=0.0,
        evidence_root=circuit_evidence,
        model_evidence_root=model_evidence,
        receipt_sha256="D" * 64,
        qualification_scope="calibration_scan_model_only",
    )


def _runner(calls: list[str], *, fail_on: str | None = None):
    def run(circuits, _context, output_root, _repository_root, **_kwargs):
        assert len(circuits) == 1
        circuit = circuits[0]
        calls.append(circuit.circuit_id)
        if circuit.circuit_id == fail_on:
            raise RuntimeError("injected point failure")
        return (_result(circuit, Path(output_root)),)

    return run


def _run(tmp_path: Path, calls: list[str], **overrides):
    values = {
        "circuits": _circuits(),
        "context": circuit_execution_context(),
        "output_root": tmp_path / "execution",
        "repository_root": tmp_path,
        "batch_id": str(uuid.uuid4()),
        "experiment_request": {"experiment_id": "contract_fixture", "axis": [1, 2, 3]},
        "circuit_runner": _runner(calls),
        "result_loader": lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected loader")),
    }
    values.update(overrides)
    return run_circuit_batch(**values), values


def test_contract_vector_matches_runtime_artifact_fields():
    vectors = json.loads(
        (ROOT / "tests" / "fixtures" / "runtime_v03_batch_v1" / "vectors.json").read_text(
            encoding="utf-8"
        )
    )
    assert vectors["schema_version"] == "0.3"
    assert vectors["terminal_statuses"] == ["completed", "cancelled"]
    assert vectors["stable_errors"]["batch_idempotency_conflict"] == 409


def test_calibration_batch_preflights_every_control_point_before_reserving_output(
    monkeypatch,
    tmp_path: Path,
):
    context = replace(
        circuit_execution_context(),
        platform_configuration=MappingProxyType(
            {
                "control_values": MappingProxyType({"sentinel": True}),
                "calibration_values": MappingProxyType({}),
            }
        ),
    )
    seen: list[str] = []

    def reject(_compilation, point_id, *_args, **_kwargs):
        seen.append(point_id)
        if point_id == "runtime_point_1":
            raise ParameterizedControlError(
                ParameterizedControlReasonCode.DAC_RANGE_EXCEEDED,
                "q1_xy_i: requested 3.5 V outside [-3, 3) V at sample 2",
            )

    monkeypatch.setattr(batch_module, "preflight_calibration_scan_control", reject)
    output = tmp_path / "execution"

    with pytest.raises(CircuitBatchError) as captured:
        run_circuit_batch(
            _circuits(),
            context,
            output,
            tmp_path,
            batch_id=str(uuid.uuid4()),
            experiment_request={"experiment_id": "preflight_fixture"},
        )

    assert captured.value.code == "circuit_control_preflight_failed"
    assert "runtime_point_1" in captured.value.detail
    assert "requested 3.5 V outside [-3, 3) V" in captured.value.detail
    assert seen == ["runtime_point_0", "runtime_point_1"]
    assert not output.exists()


def test_completed_batch_replays_without_executing_any_point(tmp_path: Path):
    calls: list[str] = []
    first, values = _run(tmp_path, calls)
    replay = run_circuit_batch(**values)

    assert calls == [circuit.circuit_id for circuit in values["circuits"]]
    assert first.status == replay.status == "completed"
    assert replay.reused_point_count == 3
    assert replay.head_sha256 == first.head_sha256
    assert verify_circuit_batch(values["output_root"], repository_root=tmp_path).head_sha256 == first.head_sha256
    vectors = json.loads(
        (ROOT / "tests" / "fixtures" / "runtime_v03_batch_v1" / "vectors.json").read_text(
            encoding="utf-8"
        )
    )
    request = json.loads((first.root / "batch" / "request.json").read_text("utf-8"))
    head = json.loads((first.root / "batch" / "head.json").read_text("utf-8"))
    receipt = json.loads(
        (first.root / "batch" / head["completed_points"][0]["receipt_path"]).read_text("utf-8")
    )
    assert sorted(request) == vectors["request_fields"]
    assert sorted(head) == vectors["head_fields"]
    assert sorted(receipt) == vectors["point_receipt_fields"]
    assert sorted(head["completed_points"][0]) == vectors["completed_point_fields"]


def test_interrupted_batch_reuses_committed_prefix_and_only_runs_tail(tmp_path: Path):
    calls: list[str] = []
    circuits = _circuits()
    identifier = str(uuid.uuid4())
    common = {
        "circuits": circuits,
        "context": circuit_execution_context(),
        "output_root": tmp_path / "execution",
        "repository_root": tmp_path,
        "batch_id": identifier,
        "experiment_request": {"experiment_id": "resume"},
        "result_loader": lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected loader")),
    }
    with pytest.raises(RuntimeError, match="injected point failure"):
        run_circuit_batch(
            **common,
            circuit_runner=_runner(calls, fail_on=circuits[1].circuit_id),
        )

    resumed = run_circuit_batch(**common, circuit_runner=_runner(calls))

    assert calls == [
        circuits[0].circuit_id,
        circuits[1].circuit_id,
        circuits[1].circuit_id,
        circuits[2].circuit_id,
    ]
    assert resumed.attempt_count == 2
    assert resumed.reused_point_count == 1


def test_same_batch_id_with_different_request_is_a_conflict(tmp_path: Path):
    calls: list[str] = []
    _first, values = _run(tmp_path, calls)
    values["experiment_request"] = {"experiment_id": "different"}

    with pytest.raises(CircuitBatchError) as captured:
        run_circuit_batch(**values)

    assert captured.value.code == "batch_idempotency_conflict"
    assert captured.value.status == 409


def test_cancellation_is_terminal_before_first_point(tmp_path: Path):
    calls: list[str] = []
    token = CancellationToken()
    token.request()

    with pytest.raises(CircuitBatchCancelledError) as captured:
        _run(tmp_path, calls, cancellation_token=token)

    assert captured.value.code == "batch_cancelled"
    assert calls == []


def test_external_cancellation_request_is_consumed_by_batch(tmp_path: Path):
    calls: list[str] = []
    identifier = str(uuid.uuid4())
    coordinator = tmp_path / ".runtime-v03"
    request_circuit_batch_cancellation(coordinator, identifier, tmp_path)

    with pytest.raises(CircuitBatchCancelledError):
        _run(
            tmp_path,
            calls,
            batch_id=identifier,
            coordinator_root=coordinator,
        )

    assert calls == []


def test_verified_replay_rejects_tampered_evidence(tmp_path: Path):
    calls: list[str] = []
    handle, values = _run(tmp_path, calls)
    result_file = handle.results[0].evidence_root / "result.bin"
    result_file.write_bytes(b"tampered")

    with pytest.raises(CircuitBatchError) as captured:
        verify_circuit_batch(values["output_root"], repository_root=tmp_path)

    assert captured.value.code == "batch_recovery_required"


def test_replay_rejects_a_different_execution_context(tmp_path: Path):
    calls: list[str] = []
    handle, values = _run(tmp_path, calls)
    changed = replace(values["context"], idle_flux_phi0={"q1": 0.12, "q2": 0.0, "c": 0.27})

    with pytest.raises(CircuitBatchError) as captured:
        verify_circuit_batch(handle.root, changed, tmp_path)

    assert captured.value.code == "batch_recovery_required"


def test_deadline_commits_a_finished_point_before_stopping_and_then_resumes(tmp_path: Path):
    calls: list[str] = []
    circuit = _circuits(1)

    def slow_runner(circuits, _context, output_root, _repository_root, **_kwargs):
        calls.append(circuits[0].circuit_id)
        time.sleep(0.08)
        return (_result(circuits[0], Path(output_root)),)

    common = {
        "circuits": circuit,
        "context": circuit_execution_context(),
        "output_root": tmp_path / "execution",
        "repository_root": tmp_path,
        "batch_id": str(uuid.uuid4()),
        "experiment_request": {"experiment_id": "deadline"},
        "batch_deadline_s": 0.05,
        "circuit_runner": slow_runner,
        "result_loader": lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected loader")),
    }
    with pytest.raises(CircuitBatchError) as captured:
        run_circuit_batch(**common)
    assert captured.value.code == "batch_deadline_exceeded"

    resumed = run_circuit_batch(**common)
    assert calls == [circuit[0].circuit_id]
    assert resumed.status == "completed"
    assert resumed.reused_point_count == 1


def test_orphan_evidence_is_verified_and_adopted_after_receipt_write_failure(
    monkeypatch,
    tmp_path: Path,
):
    calls: list[str] = []
    loaded: list[str] = []
    result_by_id: dict[str, CircuitResult] = {}
    original_writer = batch_module.write_canonical_new
    fail_once = True

    def runner(circuits, _context, output_root, _repository_root, **_kwargs):
        calls.append(circuits[0].circuit_id)
        result = _result(circuits[0], Path(output_root))
        result_by_id[circuits[0].circuit_id] = result
        return (result,)

    def loader(evidence_root, _context, _repository_root):
        identifier = Path(evidence_root).name
        loaded.append(identifier)
        return result_by_id[identifier]

    def failing_writer(path, payload):
        nonlocal fail_once
        if fail_once and Path(path).parent.name == "points":
            fail_once = False
            raise OSError("injected receipt failure")
        return original_writer(path, payload)

    values = {
        "circuits": _circuits(1),
        "context": circuit_execution_context(),
        "output_root": tmp_path / "execution",
        "repository_root": tmp_path,
        "batch_id": str(uuid.uuid4()),
        "experiment_request": {"experiment_id": "orphan-adoption"},
        "circuit_runner": runner,
        "result_loader": loader,
    }
    monkeypatch.setattr(batch_module, "write_canonical_new", failing_writer)
    with pytest.raises(OSError, match="receipt failure"):
        run_circuit_batch(**values)

    resumed = run_circuit_batch(**values)
    identifier = values["circuits"][0].circuit_id
    assert calls == [identifier]
    assert loaded == [identifier]
    assert resumed.status == "completed"


def test_resource_lock_rejects_a_concurrent_batch_for_the_same_context(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr(batch_module, "_LOCK_TIMEOUT_SECONDS", 0.05)
    entered = threading.Event()
    release = threading.Event()
    context = circuit_execution_context()
    coordinator = tmp_path / ".runtime-v03"

    def blocking_runner(circuits, _context, output_root, _repository_root, **_kwargs):
        entered.set()
        assert release.wait(timeout=2.0)
        return (_result(circuits[0], Path(output_root)),)

    first = {
        "circuits": _circuits(1),
        "context": context,
        "output_root": tmp_path / "first",
        "repository_root": tmp_path,
        "coordinator_root": coordinator,
        "batch_id": str(uuid.uuid4()),
        "experiment_request": {"experiment_id": "concurrency", "owner": "first"},
        "circuit_runner": blocking_runner,
        "result_loader": lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected loader")),
    }
    second = {
        **first,
        "output_root": tmp_path / "second",
        "batch_id": str(uuid.uuid4()),
        "experiment_request": {"experiment_id": "concurrency", "owner": "second"},
        "circuit_runner": _runner([]),
    }
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(run_circuit_batch, **first)
        assert entered.wait(timeout=2.0)
        with pytest.raises(CircuitBatchError) as captured:
            run_circuit_batch(**second)
        release.set()
        assert future.result(timeout=2.0).status == "completed"

    assert captured.value.code == "batch_busy"
