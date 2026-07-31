from __future__ import annotations

import pytest

pytestmark = pytest.mark.contract

from sqvm.calibration.rabi import (
    RabiError,
    RabiRequest,
    _candidates,
    amplitude_axis,
    analyze_rabi,
    build_rabi_circuits,
    load_rabi_analysis_policy,
)
from sqvm.candidate_protocol import normalize_calibration_candidate
from sqvm.runtime.batch import CircuitBatchHandle


def test_rabi_circuits_are_exact_set_then_two_x2p_without_plsxy():
    axis = amplitude_axis((0, 0.1), 0.05, "Q1")
    circuits = build_rabi_circuits(RabiRequest("Q1", axis))

    assert circuits[1].circuit_id == "rabi_q1_0001"
    assert circuits[1].source == (
        "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.05\nX2P Q1\nX2P Q1\n"
    )
    assert "PLSXY" not in circuits[1].source


def test_default_rabi_analysis_policy_is_versioned_and_disables_pilot_candidates():
    policy = load_rabi_analysis_policy()
    assert policy["policy_id"] == "rabi_x2p_pilot_v1"
    assert policy["approved"] is False
    assert policy["minimum_contrast"] is None


def test_approved_policy_requires_complete_integer_thresholds_and_bounds():
    from sqvm.calibration import rabi
    malformed = {
        "schema_version": "0.1", "policy_id": "approved", "approved": True,
        "minimum_point_count": 3.0, "minimum_points_before_peak": 1,
        "minimum_points_after_peak": 1, "minimum_contrast": 0.1,
        "minimum_r_squared": 0.9, "maximum_normalized_rmse": 0.1,
        "maximum_candidate_leakage": 0.1, "maximum_norm_error": 0.1,
        "minimum_edge_guard_steps": 1,
        "fit_parameter_bounds": {"offset": [0.0, 1.0], "contrast": [0.0, 1.0], "x2p_amplitude_GHz": [0.0, 1.0]},
    }
    with pytest.raises(RabiError, match="integer"):
        rabi._validate_policy_mapping(malformed)
    malformed["minimum_point_count"] = 3
    malformed["fit_parameter_bounds"]["contrast"] = [0.5, 0.5]
    with pytest.raises(RabiError, match="fit bounds"):
        rabi._validate_policy_mapping(malformed)


@pytest.mark.parametrize(
    ("bounds", "step", "message"),
    [((0.01, 0.1), 0.01, "start"), ((0, 0.1), 0.03, "divisible"), ((0, 3.2), 0.05, "between 3 and 64")],
)
def test_rabi_axis_rejects_non_first_lobe_or_invalid_grid(bounds, step, message):
    with pytest.raises(RabiError, match=message) as caught:
        amplitude_axis(bounds, step, "Q1")
    assert caught.value.code == "rabi_axis_invalid"


def test_rabi_error_exposes_a_stable_machine_code_without_losing_detail():
    error = RabiError("rabi_candidate_ineligible", "analysis policy is not approved")

    assert error.code == "rabi_candidate_ineligible"
    assert error.detail == "analysis policy is not approved"
    assert str(error) == "rabi_candidate_ineligible: analysis policy is not approved"


def test_rabi_analysis_fits_first_peak_and_builds_common_candidate():
    amplitudes = tuple(index * 0.01 for index in range(11))
    p1 = tuple(0.03 + 0.8 * __import__("math").sin(__import__("math").pi * value / 0.1) ** 2 for value in amplitudes)
    batch = CircuitBatchHandle("00000000-0000-4000-8000-000000000000", __import__("pathlib").Path("."), "A" * 64, "B" * 64, "completed", 1, 0, (), {})
    from sqvm.calibration.rabi import RabiDataset
    dataset = RabiDataset("Q1", amplitudes, tuple(1 - value for value in p1), p1, (0.0,) * 11, (0.0,) * 11, (), "C" * 64, batch)

    analysis = analyze_rabi(dataset)
    assert analysis.fit_converged
    assert analysis.x2p_amplitude_GHz == pytest.approx(0.05, abs=1e-8)
    assert len(analysis.dense_fit_curve["amplitude_GHz"]) == 201
    assert len(analysis.dense_fit_curve["P1"]) == 201
    candidate = _candidates(RabiRequest("Q1", amplitude_axis((0, 0.1), 0.01, "Q1")), analysis, "D" * 64, "q1_xy2", {"amplitude_GHz": 0.02}, True)[0]
    normalized = normalize_calibration_candidate(candidate)
    assert normalized["candidate_type"] == "xy2_amplitude"
    assert normalized["changes"][0]["parameter_path"] == "calibration_values.waveform_registry.settings.q1_xy2.amplitude_GHz"


def test_approved_policy_bounds_drive_fit_and_analysis_payload_round_trips():
    from dataclasses import replace
    from sqvm.calibration import rabi
    amplitudes = tuple(index * 0.01 for index in range(11))
    p1 = tuple(0.03 + 0.8 * __import__("math").sin(__import__("math").pi * value / 0.1) ** 2 for value in amplitudes)
    batch = CircuitBatchHandle("00000000-0000-4000-8000-000000000000", __import__("pathlib").Path("."), "A" * 64, "B" * 64, "completed", 1, 0, (), {})
    from sqvm.calibration.rabi import RabiDataset
    dataset = RabiDataset("Q1", amplitudes, tuple(1 - value for value in p1), p1, (0.01,) * 11, (0.0,) * 11, (), "C" * 64, batch)
    policy = {"approved": True, "fit_parameter_bounds": {"offset": [0.0, 0.1], "contrast": [0.7, 0.9], "x2p_amplitude_GHz": [0.045, 0.055]}}
    analysis = analyze_rabi(dataset, policy=policy)
    assert analysis.fit_converged
    assert 0.045 <= analysis.x2p_amplitude_GHz <= 0.055
    assert analysis.input_dataset_sha256 == "C" * 64
    assert analysis.optimizer_nfev is not None
    assert analysis.residual_sum_squares is not None
    bound = replace(analysis, analysis_policy_sha256="E" * 64)
    restored = rabi._analysis_from_payload(bound.to_dict())
    assert restored.to_dict() == bound.to_dict()
    impossible = {"approved": True, "fit_parameter_bounds": {**policy["fit_parameter_bounds"], "x2p_amplitude_GHz": [0.07, 0.08]}}
    assert analyze_rabi(dataset, policy=impossible).reason == "fit_bounds_do_not_intersect_peak_bracket"


def test_rabi_publishes_and_replays_same_operation_without_second_batch(monkeypatch, tmp_path):
    import hashlib
    import json
    from pathlib import Path
    from sqvm.calibration import rabi
    from sqvm.circuits import CircuitExecutionContext, CircuitResult, DressedPopulations
    from sqvm.qcis.canonical import sha256_json

    authority = {"reference_frequency_GHz": 5.0, "frequency_source": "accepted_simulation"}
    setting = {"setting_id": "q1_xy2", "target": "Q1", "status": "accepted", "gate_type": "XY2", "transition": "01", "length_samples": 2, "amplitude_GHz": 0.02}
    context = CircuitExecutionContext({"qagent_registry": {"Q1": {"component": "q1", "reference_frequency_authority": authority}}, "gate_configuration": {"Q1": {"active_xy2_setting": "q1_xy2"}}, "waveform_registry": {"settings": {"q1_xy2": setting}}}, {}, frozenset({"Q1.setting.active_xy2_setting.amplitude_GHz"}))
    parent = tmp_path / "parent.json"; parent.write_text("{}", encoding="utf-8")
    calls = []
    def fake_batch(circuits, *_args, **kwargs):
        execution_root = Path(_args[1])
        calls.append(tuple(circuit.circuit_id for circuit in circuits))
        results = tuple(CircuitResult(circuit.circuit_id, sha256_json({"id": circuit.circuit_id}), "B" * 64, "C" * 64, DressedPopulations(1.0 - index / 10, index / 10, 0.0, 0.0), (("Q1",),), (), 0.0, 0.0, execution_root / "circuit_execution" / circuit.circuit_id, execution_root / circuit.circuit_id, "D" * 64, "calibration_scan_model_only") for index, circuit in enumerate(circuits))
        return CircuitBatchHandle(kwargs["batch_id"], execution_root, "A" * 64, "B" * 64, "completed", 1, 0, results, kwargs["metadata"])
    monkeypatch.setattr(rabi, "run_circuit_batch", fake_batch)
    # This is an idempotency seam: its fake runner intentionally does not
    # materialise Runtime evidence.  Full evidence publication is covered by
    # reader/integration fixtures below.
    monkeypatch.setattr(rabi, "_verify_staging_for_publish", lambda *_args: None)
    monkeypatch.setattr(rabi, "verify_rabi_scan", lambda *_args: True)
    monkeypatch.setattr(
        rabi,
        "_static_phase_audits",
        lambda circuits, _context, _amplitudes: tuple({"event_count": 2, "first_start_sample": 0, "second_start_sample": 2, "length_samples": 2} for _ in circuits),
    )
    operation_id = "00000000-0000-4000-8000-000000000000"
    request = RabiRequest(
        "Q1", amplitude_axis((0, 0.1), 0.01, "Q1"),
        analysis_policy={
            "schema_version": "0.1", "policy_id": "test_pilot", "approved": False,
            "minimum_point_count": None, "minimum_points_before_peak": None,
            "minimum_points_after_peak": None, "minimum_contrast": None,
            "minimum_r_squared": None, "maximum_normalized_rmse": None,
            "maximum_candidate_leakage": None, "maximum_norm_error": None,
            "minimum_edge_guard_steps": None, "fit_parameter_bounds": None,
        },
    )
    first = rabi.run_qubit_rabi_scan(request, context, parent, tmp_path / "run", tmp_path, operation_id=operation_id)
    replay = rabi.run_qubit_rabi_scan(request, context, parent, tmp_path / "run", tmp_path, operation_id=operation_id)
    assert first.run_id == replay.run_id == operation_id
    assert len(calls) == 1
    assert first.candidates == {}
    workflow = json.loads((first.root / "workflow.json").read_text("utf-8"))
    assert workflow["created_utc"] == first.dataset.runtime_batch.metadata["created_utc"]
    assert workflow["candidates"] == []
    assert first.dataset.runtime_batch.root == first.root / "execution"
    monkeypatch.undo()
    for name, key, replacement in (
        ("receipt.json", "status", "tampered"),
        ("verification_report.json", "ok", False),
        ("workflow.json", "status", "tampered"),
    ):
        path = first.root / name
        original = path.read_text("utf-8")
        payload = json.loads(original); payload[key] = replacement
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(rabi.RabiError):
            rabi.verify_rabi_scan(first.root)
        path.write_text(original, encoding="utf-8")
    dataset_path = first.root / "dataset.json"
    workflow_path = first.root / "workflow.json"
    dataset_payload = json.loads(dataset_path.read_text("utf-8"))
    dataset_payload["series"]["Q1"]["P1"][0] = -0.1
    dataset_path.write_text(json.dumps(dataset_payload), encoding="utf-8")
    workflow_payload = json.loads(workflow_path.read_text("utf-8"))
    workflow_payload["dataset"]["sha256"] = hashlib.sha256(dataset_path.read_bytes()).hexdigest().upper()
    workflow_path.write_text(json.dumps(workflow_payload), encoding="utf-8")
    workflow_sha = hashlib.sha256(workflow_path.read_bytes()).hexdigest().upper()
    for name in ("receipt.json", "verification_report.json"):
        path = first.root / name
        payload = json.loads(path.read_text("utf-8"))
        payload["workflow_sha256"] = workflow_sha
        payload["dataset_sha256"] = workflow_payload["dataset"]["sha256"]
        path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(rabi.RabiError):
        rabi.verify_rabi_scan(first.root)
