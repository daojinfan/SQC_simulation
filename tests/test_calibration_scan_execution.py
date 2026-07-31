from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

from types import SimpleNamespace
from pathlib import Path
from types import MappingProxyType
import shutil
import uuid

import numpy as np
import pytest

import sqvm.runtime.calibration_scan as scan_module
from sqvm.circuits import QCISCircuit, compile_circuit
from sqvm.evolution.stage51_models import Stage51NumericalResult
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.calibration_model import (
    _build_projected_charge_model,
    _validate_projection_convergence,
    calibration_model_configuration_sha256,
    load_calibration_model_authority,
    model_configuration_from_authority,
    resolve_calibration_model_authority,
)
from sqvm.runtime.calibration_scan import (
    CalibrationScanExecutionError,
    calibration_scan_policy,
    run_calibration_scan_point,
    verify_calibration_scan_point,
)
from tests.support.contexts import circuit_execution_context as _context


ROOT = Path(__file__).resolve().parents[1]


def test_calibration_scan_policy_is_local_and_has_operational_worker_budget():
    policy = calibration_scan_policy()
    assert policy["profile_id"] == "local_calibration_scan_v1"
    assert policy["max_worker_wall_seconds"] == 600.0
    assert policy["numerical_replay_policy"] == "deferred_batch_review"
    assert policy["formal_scale_qualified"] is False
    assert policy["hardware_measurement"] is False


def test_calibration_model_authority_binds_formal_cutoffs():
    authority = load_calibration_model_authority(ROOT)

    assert authority["model"]["model_id"] == "projected_charge_basis_2q1c_v1"
    assert authority["model"]["charge_cutoffs"] == [7, 7, 7]
    assert authority["model"]["convergence_charge_cutoffs"] == [8, 8, 8]
    assert authority["model"]["retained_energy_levels"] == [5, 3, 5]
    assert authority["model"]["convergence_retained_energy_levels"] == [6, 4, 6]
    assert authority["claim"] == {
        "formal_scale_qualified": False,
        "hardware_measurement": False,
        "scope": "local_simulator_calibration_only",
    }


def test_projected_charge_model_reproduces_converged_idle_spectrum():
    authority = load_calibration_model_authority(ROOT)
    model = authority["model"]
    baseline = _build_projected_charge_model(
        ROOT,
        model,
        tuple(model["charge_cutoffs"]),
        tuple(model["retained_energy_levels"]),
    )
    comparison = _build_projected_charge_model(
        ROOT,
        model,
        tuple(model["convergence_charge_cutoffs"]),
        tuple(model["convergence_retained_energy_levels"]),
    )
    evidence = _validate_projection_convergence(
        baseline,
        comparison,
        authority["tolerances"],
    )

    assert baseline.dimensions == (5, 3, 5)
    assert baseline.transition_frequencies_GHz["q1"] == pytest.approx(
        5.193479910702088,
        abs=1.0e-12,
    )
    assert baseline.transition_frequencies_GHz["q2"] == pytest.approx(
        5.3316330518486765,
        abs=1.0e-12,
    )
    assert min(baseline.label_overlaps.values()) > 0.999
    assert max(evidence["frequency_drift_GHz"].values()) < 1.1e-6
    assert max(evidence["drive_matrix_element_drift"].values()) < 2.0e-7


def test_calibration_model_authority_uses_effective_idle_flux():
    baseline = resolve_calibration_model_authority(ROOT)
    effective = resolve_calibration_model_authority(
        ROOT,
        idle_flux_phi0={"q1": 0.2, "c": 0.27, "q2": 0.0},
    )

    assert effective["model"]["idle_flux_phi0"] == {
        "q1": 0.2,
        "c": 0.27,
        "q2": 0.0,
    }
    assert effective["model_authority_id"] != baseline["model_authority_id"]
    model = effective["model"]
    projected = _build_projected_charge_model(
        ROOT,
        model,
        tuple(model["charge_cutoffs"]),
        tuple(model["retained_energy_levels"]),
    )
    assert projected.transition_frequencies_GHz["q1"] == pytest.approx(
        4.768138448604837,
        abs=1.0e-12,
    )


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


def test_calibration_scan_v2_admits_long_rabi_and_keeps_v1_verifiable(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(scan_module, "verify_compilation", lambda _value: None)
    monkeypatch.setattr(scan_module, "verify_drive_event_inventory", lambda _value: None)
    monkeypatch.setattr(scan_module, "verify_coefficient_inventory", lambda _value: None)
    compilation = SimpleNamespace(
        q1_xy=np.zeros(80),
        plan=SimpleNamespace(dt_ns=0.5),
    )
    policy = calibration_scan_policy()

    assert policy["artifact_version"] == "0.2"
    scan_module._admit(compilation, "rabi_long", 600.0, policy)

    legacy_sha256 = raw_file_sha256(
        ROOT / "configs/runtime/calibration_scan/execution_policy_v1.json"
    )
    (tmp_path / scan_module.EVIDENCE_NAME).write_bytes(
        canonical_json_bytes({"policy_sha256": legacy_sha256})
    )
    legacy = scan_module._load_bound_policy(tmp_path, ROOT)
    assert legacy["artifact_version"] == "0.1"
    assert legacy["max_logical_sample_count"] == 64
    assert scan_module._canonical_sha(legacy) == legacy_sha256


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

    def fail_after_staging(
        _compilation,
        _point_id,
        staging,
        _root,
        _idle_flux,
        _control_values,
    ):
        observed["name"] = staging.name
        raise RuntimeError("stop")

    monkeypatch.setattr(scan_module, "_publish_control", fail_after_staging)
    with pytest.raises(RuntimeError, match="stop"):
        scan_module.run_calibration_scan_point(
            compilation,
            "point_" + "a" * 64,
            tmp_path,
            ROOT,
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

    dynamic_idle = {"q1": 0.12, "c": 0.27, "q2": 0.0}

    def fake_worker(
        coefficients,
        repository_root,
        *,
        timeout_s,
        model_configuration,
        idle_flux_phi0,
    ):
        assert timeout_s == 600.0
        assert Path(repository_root).resolve() == ROOT
        assert idle_flux_phi0 == dynamic_idle
        absolute_q1 = np.frombuffer(
            (coefficients.artifact_root / "arrays" / "absolute_flux_q1.bin").read_bytes(),
            dtype="<f8",
        )
        assert np.all(absolute_q1 == dynamic_idle["q1"])
        edges = np.frombuffer(
            (coefficients.artifact_root / "arrays" / "time_edge_ns.bin").read_bytes(),
            dtype="<f8",
        ).copy()
        state = np.zeros(75, dtype="<c16")
        state[0] = 1.0
        zeros = np.zeros(edges.size, dtype="<f8")
        populations = {
            "000": np.ones(edges.size, dtype="<f8"),
            "100": zeros.copy(),
            "001": zeros.copy(),
            "101": zeros.copy(),
        }
        authority = resolve_calibration_model_authority(
            ROOT,
            model_configuration,
            idle_flux_phi0=idle_flux_phi0,
        )
        return Stage51NumericalResult(
            edges,
            state,
            state.copy(),
            MappingProxyType(populations),
            zeros.copy(),
            zeros.copy(),
            MappingProxyType({label: "A" * 64 for label in populations}),
            MappingProxyType({
                "engine_id": authority["model"]["model_id"],
                "model_authority_id": authority["model_authority_id"],
                "model_configuration_sha256": calibration_model_configuration_sha256(
                    model_configuration_from_authority(authority)
                ),
                "charge_cutoffs": authority["model"]["charge_cutoffs"],
                "retained_energy_levels": authority["model"]["retained_energy_levels"],
                "hilbert_dimension": 75,
                "transition_frequencies_GHz": {"q1": 5.19, "q2": 5.33},
                "label_overlaps": {
                    "000": 1.0,
                    "100": 1.0,
                    "001": 1.0,
                    "101": 1.0,
                },
                "projection_convergence": {
                    "baseline_charge_cutoffs": [7, 7, 7],
                    "baseline_retained_energy_levels": [5, 3, 5],
                    "comparison_charge_cutoffs": [8, 8, 8],
                    "comparison_retained_energy_levels": [6, 4, 6],
                    "frequency_drift_GHz": {"q1": 1.0e-7, "q2": 1.0e-7},
                    "drive_matrix_element_drift": {"q1": 1.0e-7, "q2": 1.0e-7},
                    "passed": True,
                },
                "solver_spec": dict(authority["solver"]),
                "runtime_s": 0.0,
            }),
        )

    monkeypatch.setattr(scan_module, "execute_calibration_model_worker", fake_worker)
    handle = run_calibration_scan_point(
        compiled.compilation,
        compiled.circuit.circuit_id,
        output,
        ROOT,
        timeout_s=600.0,
        idle_flux_phi0=dynamic_idle,
    )
    reopened = verify_calibration_scan_point(
        handle.artifact_root,
        compiled.compilation,
        ROOT,
        idle_flux_phi0=dynamic_idle,
    )
    assert reopened == handle

    population = handle.artifact_root / "stage51/evolution/observables/population_000.bin"
    population.write_bytes(population.read_bytes() + b"tamper")
    with pytest.raises(CalibrationScanExecutionError, match="mismatch|array hash"):
        verify_calibration_scan_point(
            handle.artifact_root,
            compiled.compilation,
            ROOT,
            idle_flux_phi0=dynamic_idle,
        )
