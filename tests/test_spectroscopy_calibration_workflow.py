from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import uuid

import pytest

import sqvm.experiments.spectroscopy as spectroscopy_module
from sqvm.experiments import (
    SpectroscopyAxis,
    SpectroscopyCalibrationError,
    SpectroscopyCalibrationPolicy,
    SpectroscopyCalibrationRequest,
    SpectroscopyMode,
    SpectroscopyPulsePolicy,
    SpectroscopyRequest,
    build_qubit_capability_adapter,
    context_with_spectroscopy_calibration,
    decide_qubit_spectroscopy_calibration,
    expand_qubit_spectroscopy_points,
    run_qubit_spectroscopy_calibration,
    verify_qubit_spectroscopy_calibration,
    verify_qubit_spectroscopy_calibration_decision,
)
from sqvm.qcis.canonical import canonical_json_bytes
from test_qubit_spectroscopy import _context, _result


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "configs/calibration/platform_uncalibrated_v1.json"


def _request(mode: SpectroscopyMode = SpectroscopyMode.PARALLEL_LOCKSTEP):
    if mode == SpectroscopyMode.SINGLE:
        targets = ("Q1",)
        axes = (SpectroscopyAxis("Q1", (4.8, 5.0, 5.2)),)
    else:
        targets = ("Q1", "Q2")
        axes = (
            SpectroscopyAxis("Q1", (4.8, 5.0, 5.2)),
            SpectroscopyAxis("Q2", (5.0, 5.2, 5.4)),
        )
    policies = tuple(
        SpectroscopyPulsePolicy(target, 5, 0.001, 2.0) for target in targets
    )
    coarse = SpectroscopyRequest(mode, "coarse", targets, axes, policies)
    return SpectroscopyCalibrationRequest(
        coarse,
        SpectroscopyCalibrationPolicy(
            fine_span_GHz=0.1,
            fine_points=5,
            confirmation_span_GHz=0.08,
            confirmation_points=5,
            min_contrast=0.1,
            max_leakage=0.01,
            max_norm_error=1.0e-8,
            max_coarse_refined_shift_GHz=0.05,
            max_parallel_peak_shift_GHz=0.01,
            max_cross_excitation=0.02,
            max_parallel_leakage_delta=0.005,
        ),
    )


def _install_synthetic_runner(monkeypatch, calls, *, cross_excitation=0.005):
    centers = {"Q1": 5.0, "Q2": 5.2}

    def fake_run(circuits, _context_value, _output_root, _repository_root, **kwargs):
        calls.append({"circuits": circuits, "readout_qubit": kwargs["readout_qubit"]})
        results = []
        for circuit in circuits:
            frequencies = {}
            for line in circuit.source.strip().splitlines():
                tokens = line.split(" ")
                frequencies[tokens[1]] = float(tokens[6])
            driven = set(frequencies)
            q1 = 0.02 + 0.33 * _gaussian(frequencies["Q1"], centers["Q1"]) if "Q1" in driven else cross_excitation
            q2 = 0.02 + 0.33 * _gaussian(frequencies["Q2"], centers["Q2"]) if "Q2" in driven else cross_excitation
            leakage = 0.001
            p101 = 0.0
            p000 = 1.0 - leakage - q1 - q2 - p101
            results.append(_result(circuit.circuit_id, p000, q1, q2, p101))
        return tuple(results)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


def _gaussian(frequency: float, center: float) -> float:
    return math.exp(-((frequency - center) / 0.055) ** 2)


def _target(name: str) -> Path:
    return ROOT / "tmp" / f"{name}_{uuid.uuid4().hex}"


def test_parallel_workflow_reuses_one_api_and_publishes_eligible_candidates(monkeypatch):
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    target = _target("spectroscopy_workflow")
    try:
        run = run_qubit_spectroscopy_calibration(
            _request(),
            _context(),
            PARENT,
            target,
            ROOT,
            timeout_s=10.0,
        )

        assert run.recommendation_eligible is True
        assert set(run.candidates) == {"Q1", "Q2"}
        assert run.candidates["Q1"]["proposed_frequency_GHz"] == pytest.approx(5.0)
        assert run.candidates["Q2"]["proposed_frequency_GHz"] == pytest.approx(5.2)
        assert len(calls) == 4
        assert [len(call["circuits"]) for call in calls] == [3, 5, 5, 5]
        assert calls[0]["readout_qubit"] == [["Q1"], ["Q2"], ["Q1", "Q2"]]
        assert calls[1]["readout_qubit"] == [["Q1"], ["Q2"], ["Q1", "Q2"]]
        assert calls[2]["readout_qubit"] == [["Q1"]]
        assert calls[3]["readout_qubit"] == [["Q2"]]
        assert verify_qubit_spectroscopy_calibration(target)
        assert (target / "spectroscopy.png").is_file()
        workflow = json.loads((target / "workflow.json").read_text("utf-8"))
        assert workflow["analyses"]["coarse"]["recommendation_eligible"] is False
        assert workflow["analyses"]["refined"]["recommendation_eligible"] is False
        assert workflow["recommendation_eligible"] is True
        assert all(row["passed"] for row in workflow["gates"])
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_single_workflow_skips_confirmation_and_accept_updates_context(monkeypatch):
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    run_target = _target("spectroscopy_single")
    decision_target = _target("spectroscopy_decision")
    try:
        context = _context()
        run = run_qubit_spectroscopy_calibration(
            _request(SpectroscopyMode.SINGLE),
            context,
            PARENT,
            run_target,
            ROOT,
        )
        assert len(calls) == 2
        decision = decide_qubit_spectroscopy_calibration(
            run.root,
            PARENT,
            decision_target,
            context,
            decision="accept",
            actor_id="project.manager",
            reason="accept verified model spectroscopy frequency",
            confirmation_phrase=f"ACCEPT SIMULATION CALIBRATION {run.recommendation_id}",
            accepted_targets=("Q1",),
            repository_root=ROOT,
        )

        assert decision.calibration_path == decision_target / "calibration.json"
        assert verify_qubit_spectroscopy_calibration_decision(decision_target)
        snapshot = json.loads(decision.calibration_path.read_text("utf-8"))
        assert snapshot["status"] == "accepted_simulation"
        assert snapshot["accepted_targets"] == ["Q1"]
        assert snapshot["values"]["qagents"]["Q1"]["reference_frequency_authority"][
            "frequency_source"
        ] == "accepted_simulation"
        assert snapshot["values"]["qagents"]["Q2"]["reference_frequency_authority"][
            "frequency_source"
        ] == "bootstrap_seed"
        updated = context_with_spectroscopy_calibration(context, decision.calibration_path)
        adapter = build_qubit_capability_adapter(updated)
        assert adapter.resolve("Q1").reference_frequency_GHz == pytest.approx(5.0)
        assert "calibration" in updated.authorities["expected_sha256"]
        assert len(expand_qubit_spectroscopy_points(_request(SpectroscopyMode.SINGLE).coarse_request, updated)) == 3
    finally:
        shutil.rmtree(run_target, ignore_errors=True)
        shutil.rmtree(decision_target, ignore_errors=True)


def test_reject_publishes_no_calibration(monkeypatch):
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    run_target = _target("spectroscopy_reject_run")
    decision_target = _target("spectroscopy_reject")
    try:
        run = run_qubit_spectroscopy_calibration(
            _request(SpectroscopyMode.SINGLE), _context(), PARENT, run_target, ROOT
        )
        decision = decide_qubit_spectroscopy_calibration(
            run.root,
            PARENT,
            decision_target,
            _context(),
            decision="reject",
            actor_id="project.manager",
            reason="reject candidate",
            confirmation_phrase=f"REJECT SIMULATION CALIBRATION {run.recommendation_id}",
            repository_root=ROOT,
        )
        assert decision.calibration_path is None
        assert not (decision_target / "calibration.json").exists()
        assert (decision_target / "decision.json").is_file()
        assert verify_qubit_spectroscopy_calibration_decision(decision_target)
    finally:
        shutil.rmtree(run_target, ignore_errors=True)
        shutil.rmtree(decision_target, ignore_errors=True)


def test_tampered_dataset_blocks_verification_and_decision(monkeypatch):
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    run_target = _target("spectroscopy_tamper")
    decision_target = _target("spectroscopy_tamper_decision")
    try:
        run = run_qubit_spectroscopy_calibration(
            _request(SpectroscopyMode.SINGLE), _context(), PARENT, run_target, ROOT
        )
        path = run_target / "datasets" / "refined.json"
        payload = json.loads(path.read_text("utf-8"))
        payload["points"][0]["target_excited_population"]["Q1"] = 0.99
        path.write_bytes(canonical_json_bytes(payload))

        with pytest.raises(SpectroscopyCalibrationError, match="refined dataset hash mismatch"):
            verify_qubit_spectroscopy_calibration(run_target)
        with pytest.raises(SpectroscopyCalibrationError, match="refined dataset hash mismatch"):
            decide_qubit_spectroscopy_calibration(
                run.root,
                PARENT,
                decision_target,
                _context(),
                decision="accept",
                actor_id="project.manager",
                reason="must fail",
                confirmation_phrase=f"ACCEPT SIMULATION CALIBRATION {run.recommendation_id}",
                accepted_targets=("Q1",),
                repository_root=ROOT,
            )
    finally:
        shutil.rmtree(run_target, ignore_errors=True)
        shutil.rmtree(decision_target, ignore_errors=True)


def test_failed_parallel_gate_publishes_diagnostics_but_blocks_accept(monkeypatch):
    calls = []
    _install_synthetic_runner(monkeypatch, calls, cross_excitation=0.08)
    run_target = _target("spectroscopy_gate_failure")
    decision_target = _target("spectroscopy_gate_failure_decision")
    try:
        run = run_qubit_spectroscopy_calibration(
            _request(), _context(), PARENT, run_target, ROOT
        )
        assert run.recommendation_eligible is False
        assert all(candidate["recommendation_eligible"] is False for candidate in run.candidates.values())
        workflow = json.loads((run_target / "workflow.json").read_text("utf-8"))
        failed = {row["name"] for row in workflow["gates"] if not row["passed"]}
        assert failed == {"Q1.cross_excitation", "Q2.cross_excitation"}
        with pytest.raises(SpectroscopyCalibrationError, match="not recommendation eligible"):
            decide_qubit_spectroscopy_calibration(
                run.root,
                PARENT,
                decision_target,
                _context(),
                decision="accept",
                actor_id="project.manager",
                reason="must fail",
                confirmation_phrase=f"ACCEPT SIMULATION CALIBRATION {run.recommendation_id}",
                accepted_targets=("Q1",),
                repository_root=ROOT,
            )
    finally:
        shutil.rmtree(run_target, ignore_errors=True)
        shutil.rmtree(decision_target, ignore_errors=True)
