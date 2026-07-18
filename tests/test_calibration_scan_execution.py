from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from types import MappingProxyType
import shutil
import uuid

import numpy as np
import pytest

import sqvm.runtime.calibration_scan as scan_module
from sqvm.circuits import QCISCircuit, compile_circuit
from sqvm.evolution.stage51_authority import admit_physics_authority
from sqvm.evolution.stage51_models import Stage51NumericalResult
from sqvm.runtime.calibration_scan import (
    CalibrationScanExecutionError,
    calibration_scan_policy,
    run_calibration_scan_point,
    verify_calibration_scan_point,
)
from test_run_circuits import _context


ROOT = Path(__file__).resolve().parents[1]


def test_calibration_scan_policy_is_local_and_has_operational_worker_budget():
    policy = calibration_scan_policy()
    assert policy["profile_id"] == "local_calibration_scan_v1"
    assert policy["max_worker_wall_seconds"] == 600.0
    assert policy["numerical_replay_policy"] == "deferred_batch_review"
    assert policy["formal_scale_qualified"] is False
    assert policy["hardware_measurement"] is False


def test_calibration_scan_admission_enforces_policy_timeout(monkeypatch):
    monkeypatch.setattr(scan_module, "verify_compilation", lambda _value: None)
    monkeypatch.setattr(scan_module, "verify_drive_event_inventory", lambda _value: None)
    monkeypatch.setattr(scan_module, "verify_coefficient_inventory", lambda _value: None)
    compilation = SimpleNamespace(
        q1_xy=np.zeros(8),
        plan=SimpleNamespace(dt_ns=0.5),
    )
    policy = calibration_scan_policy()

    scan_module._admit(compilation, "point_1", 600.0, policy)
    with pytest.raises(CalibrationScanExecutionError, match="timeout_s"):
        scan_module._admit(compilation, "point_1", 600.1, policy)


def test_calibration_scan_staging_name_preserves_windows_path_budget(monkeypatch, tmp_path):
    compilation = SimpleNamespace(
        q1_xy=np.zeros(8),
        plan=SimpleNamespace(dt_ns=0.5),
    )
    monkeypatch.setattr(scan_module, "_load_policy", lambda _root: {
        "max_worker_wall_seconds": 600.0,
        "max_logical_sample_count": 64,
        "max_logical_duration_ns": 32.0,
    })
    monkeypatch.setattr(scan_module, "_admit", lambda *_args: None)
    monkeypatch.setattr(scan_module, "_safe_output_root", lambda *_args: Path(tmp_path))

    observed = {}

    def fail_after_staging(_compilation, _point_id, staging, _root):
        observed["name"] = staging.name
        raise RuntimeError("stop")

    monkeypatch.setattr(scan_module, "_publish_control", fail_after_staging)
    with pytest.raises(RuntimeError, match="stop"):
        scan_module.run_calibration_scan_point(
            compilation,
            "point_" + "a" * 64,
            tmp_path,
            tmp_path,
            timeout_s=600.0,
        )
    assert observed["name"].startswith(".cs_")
    assert len(observed["name"]) == 12


def test_calibration_scan_artifact_round_trip_without_numerical_replay(
    monkeypatch,
    request,
):
    output = ROOT / "tmp" / f"calibration_scan_test_{uuid.uuid4().hex}"
    request.addfinalizer(lambda: shutil.rmtree(output, ignore_errors=True))
    compiled = compile_circuit(
        QCISCircuit(
            "scan_round_trip",
            "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n",
        ),
        _context(),
    )

    def fake_worker(coefficients, context, *, timeout_s):
        assert timeout_s == 600.0
        edges = np.frombuffer(
            (coefficients.artifact_root / "arrays" / "time_edge_ns.bin").read_bytes(),
            dtype="<f8",
        ).copy()
        state = np.zeros(27, dtype="<c16")
        state[0] = 1.0
        zeros = np.zeros(edges.size, dtype="<f8")
        populations = {
            "000": np.ones(edges.size, dtype="<f8"),
            "100": zeros.copy(),
            "001": zeros.copy(),
            "101": zeros.copy(),
        }
        authority, _binding = admit_physics_authority(context)
        return Stage51NumericalResult(
            edges,
            state,
            state.copy(),
            MappingProxyType(populations),
            zeros.copy(),
            zeros.copy(),
            MappingProxyType({label: "A" * 64 for label in populations}),
            MappingProxyType({
                "solver_spec": dict(authority["solver"]),
                "runtime_s": 0.0,
            }),
        )

    monkeypatch.setattr(scan_module, "execute_stage51_worker", fake_worker)
    handle = run_calibration_scan_point(
        compiled.compilation,
        compiled.circuit.circuit_id,
        output,
        ROOT,
        timeout_s=600.0,
    )
    reopened = verify_calibration_scan_point(
        handle.artifact_root,
        compiled.compilation,
        ROOT,
    )
    assert reopened == handle

    population = handle.artifact_root / "stage51/evolution/observables/population_000.bin"
    population.write_bytes(population.read_bytes() + b"tamper")
    with pytest.raises(CalibrationScanExecutionError, match="mismatch|array hash"):
        verify_calibration_scan_point(handle.artifact_root, compiled.compilation, ROOT)
