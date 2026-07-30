"""Runtime 0.3 fault coverage through the public Rabi adapter."""

from __future__ import annotations

from pathlib import Path
import shutil
import time
import uuid

import pytest

import sqvm.calibration.rabi as rabi
from sqvm.circuits import CircuitResult, DressedPopulations, ReadoutProbabilities
from sqvm.qcis.canonical import sha256_bytes, sha256_json
from sqvm.runtime.batch import CircuitBatchError, run_circuit_batch as runtime_batch
from sqvm.runtime.batch import CircuitBatchCancelledError
from sqvm.runtime.lifecycle import CancellationToken
from tests.support.contexts import circuit_execution_context


pytestmark = pytest.mark.integration


def _request(step: float = 0.05):
    return rabi.RabiRequest("Q1", rabi.amplitude_axis((0.0, 0.1), step, "Q1"))


def _runner(calls: list[str], *, fail_once: set[str] | None = None):
    def run(circuits, _context, output_root, _repository_root, **_kwargs):
        circuit = circuits[0]
        calls.append(circuit.circuit_id)
        if fail_once is not None and circuit.circuit_id in fail_once:
            fail_once.remove(circuit.circuit_id)
            raise RuntimeError("injected point failure")
        root = Path(output_root)
        evidence, model = root / "circuit_execution" / circuit.circuit_id, root / circuit.circuit_id
        evidence.mkdir(parents=True); model.mkdir(parents=True)
        (evidence / "result.bin").write_bytes(circuit.circuit_id.encode())
        (model / "model.bin").write_bytes(circuit.source.encode())
        index = int(circuit.circuit_id.rsplit("_", 1)[1])
        p1 = (0.0, 1.0, 0.0)[index]
        return (CircuitResult(
            circuit.circuit_id, sha256_bytes(circuit.source.encode()), "B" * 64, "C" * 64,
            DressedPopulations(1.0 - p1, p1, 0.0, 0.0), (("Q1",),),
            (ReadoutProbabilities(("Q1",), {"P0": 1.0 - p1, "P1": p1}),),
            0.0, 0.0, evidence, model, "D" * 64, "calibration_scan_model_only",
        ),)
    return run


def _adapter(monkeypatch, calls, *, fail_once=None, runner_factory=None):
    def run(*args, **kwargs):
        kwargs["circuit_runner"] = (runner_factory or _runner)(calls, fail_once=fail_once)
        kwargs["result_loader"] = lambda *_args: (_ for _ in ()).throw(AssertionError("unexpected loader"))
        return runtime_batch(*args, **kwargs)
    monkeypatch.setattr(rabi, "run_circuit_batch", run)


def _run(monkeypatch, tmp_path, calls, *, request=None, operation_id=None, fail_once=None, context=None, deadline=3600.0, runner_factory=None):
    _adapter(monkeypatch, calls, fail_once=fail_once, runner_factory=runner_factory)
    policy = tmp_path / rabi.RABI_POLICY_PATH
    policy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / rabi.RABI_POLICY_PATH, policy)
    parent = tmp_path / "parent.json"; parent.write_text("{}", encoding="utf-8")
    return rabi.run_qubit_rabi_scan(
        request or _request(), context or circuit_execution_context("Q1.setting.active_xy2_setting.amplitude_GHz"),
        parent, tmp_path / "published", tmp_path,
        operation_id=operation_id or str(uuid.uuid4()), batch_deadline_s=deadline,
    )


def test_rabi_runtime_failure_resumes_only_uncommitted_suffix(monkeypatch, tmp_path):
    operation = str(uuid.uuid4())
    calls: list[str] = []
    with pytest.raises(RuntimeError, match="injected point failure"):
        _run(monkeypatch, tmp_path, calls, operation_id=operation, fail_once={"rabi_q1_0001"})
    _run(monkeypatch, tmp_path, calls, operation_id=operation)
    assert calls == ["rabi_q1_0000", "rabi_q1_0001", "rabi_q1_0001", "rabi_q1_0002"]


def test_rabi_runtime_rejects_changed_axis_for_same_operation(monkeypatch, tmp_path):
    operation = str(uuid.uuid4())
    calls: list[str] = []
    _run(monkeypatch, tmp_path, calls, operation_id=operation)
    with pytest.raises(rabi.RabiError, match="idempotency conflict"):
        _run(monkeypatch, tmp_path, calls, operation_id=operation, request=_request(0.025))


def test_rabi_precompile_failure_starts_no_runtime_point(monkeypatch, tmp_path):
    calls: list[str] = []
    _adapter(monkeypatch, calls)
    monkeypatch.setattr(rabi, "_static_phase_audits", lambda *_args: (_ for _ in ()).throw(rabi.RabiError("rabi_phase_audit_failed")))
    parent = tmp_path / "parent.json"; parent.write_text("{}", encoding="utf-8")
    policy = tmp_path / rabi.RABI_POLICY_PATH; policy.parent.mkdir(parents=True); shutil.copyfile(Path(__file__).resolve().parents[1] / rabi.RABI_POLICY_PATH, policy)
    with pytest.raises(rabi.RabiError, match="rabi_phase_audit_failed"):
        rabi.run_qubit_rabi_scan(_request(), circuit_execution_context("Q1.setting.active_xy2_setting.amplitude_GHz"), parent, tmp_path / "published", tmp_path, operation_id=str(uuid.uuid4()))
    assert calls == []


def test_rabi_control_preflight_failure_is_public_and_leaves_no_staging(
    monkeypatch,
    tmp_path,
):
    operation = str(uuid.uuid4())

    def reject(*_args, **_kwargs):
        raise CircuitBatchError(
            "circuit_control_preflight_failed",
            422,
            "rabi_q1_0007: DAC range exceeded",
            batch_id=operation,
        )

    monkeypatch.setattr(rabi, "run_circuit_batch", reject)
    parent = tmp_path / "parent.json"
    parent.write_text("{}", encoding="utf-8")
    policy = tmp_path / rabi.RABI_POLICY_PATH
    policy.parent.mkdir(parents=True)
    shutil.copyfile(Path(__file__).resolve().parents[1] / rabi.RABI_POLICY_PATH, policy)

    with pytest.raises(rabi.RabiError) as captured:
        rabi.run_qubit_rabi_scan(
            _request(),
            circuit_execution_context(
                "Q1.setting.active_xy2_setting.amplitude_GHz"
            ),
            parent,
            tmp_path / "published",
            tmp_path,
            operation_id=operation,
        )

    assert captured.value.code == "rabi_control_preflight_failed"
    assert "rabi_q1_0007" in str(captured.value)
    assert not (tmp_path / f".rabi_{operation.replace('-', '')}").exists()


def test_rabi_cancelled_before_first_point_starts_no_runtime_point(monkeypatch, tmp_path):
    token = CancellationToken(); token.request()
    calls: list[str] = []
    _adapter(monkeypatch, calls)
    parent = tmp_path / "parent.json"; parent.write_text("{}", encoding="utf-8")
    policy = tmp_path / rabi.RABI_POLICY_PATH; policy.parent.mkdir(parents=True); shutil.copyfile(Path(__file__).resolve().parents[1] / rabi.RABI_POLICY_PATH, policy)
    with pytest.raises(CircuitBatchCancelledError):
        rabi.run_qubit_rabi_scan(_request(), circuit_execution_context("Q1.setting.active_xy2_setting.amplitude_GHz"), parent, tmp_path / "published", tmp_path, operation_id=str(uuid.uuid4()), cancellation_token=token)
    assert calls == []


def test_rabi_deadline_resumes_only_uncommitted_suffix(monkeypatch, tmp_path):
    def slow(calls, **kwargs):
        base = _runner(calls, **kwargs)
        def run(*args, **inner):
            result = base(*args, **inner); time.sleep(0.3); return result
        return run
    operation, calls = str(uuid.uuid4()), []
    with pytest.raises(CircuitBatchError, match="batch_deadline_exceeded"):
        _run(monkeypatch, tmp_path, calls, operation_id=operation, deadline=0.2, runner_factory=slow)
    _run(monkeypatch, tmp_path, calls, operation_id=operation, deadline=0.2)
    assert calls == ["rabi_q1_0000", "rabi_q1_0001", "rabi_q1_0002"]


def test_rabi_published_reopen_executes_no_runtime_point(monkeypatch, tmp_path):
    operation, calls = str(uuid.uuid4()), []
    first = _run(monkeypatch, tmp_path, calls, operation_id=operation)
    replay = _run(monkeypatch, tmp_path, calls, operation_id=operation)
    assert replay.run_id == first.run_id
    assert calls == ["rabi_q1_0000", "rabi_q1_0001", "rabi_q1_0002"]


def test_rabi_runtime_rejects_changed_context_and_policy(monkeypatch, tmp_path):
    operation, calls = str(uuid.uuid4()), []
    _run(monkeypatch, tmp_path, calls, operation_id=operation)
    context = circuit_execution_context("Q1.setting.active_xy2_setting.amplitude_GHz")
    authorities = dict(context.authorities)
    registry = dict(authorities["waveform_registry"])
    settings = dict(registry["settings"]); changed = dict(settings["q1_xy2"])
    changed["amplitude_GHz"] = 0.11; changed["setting_hash"] = sha256_json(changed)
    settings["q1_xy2"] = changed; registry["settings"] = settings; authorities["waveform_registry"] = registry
    expected = dict(authorities["expected_sha256"]); expected["waveform_registry"] = sha256_json(registry); authorities["expected_sha256"] = expected
    changed_context = type(context)(authorities, context.idle_flux_phi0, context.settable_paths)
    with pytest.raises(rabi.RabiError, match="idempotency conflict"):
        _run(monkeypatch, tmp_path, calls, operation_id=operation, context=changed_context)
    policy = {"schema_version": "0.1", "policy_id": "alternate_pilot", "approved": False, "minimum_point_count": None, "minimum_points_before_peak": None, "minimum_points_after_peak": None, "minimum_contrast": None, "minimum_r_squared": None, "maximum_normalized_rmse": None, "maximum_candidate_leakage": None, "maximum_norm_error": None, "minimum_edge_guard_steps": None, "fit_parameter_bounds": None}
    with pytest.raises(rabi.RabiError, match="idempotency conflict"):
        _run(monkeypatch, tmp_path, calls, operation_id=operation, request=rabi.RabiRequest("Q1", _request().axis, analysis_policy=policy))
