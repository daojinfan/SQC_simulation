from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

import sqvm.circuits as circuits_module
from sqvm.circuits import (
    CircuitExecutionContext,
    CircuitExecutionError,
    CircuitExecutionProfile,
    CircuitReasonCode,
    QCISCircuit,
    compile_circuit,
    run_circuits,
)
from sqvm.qcis.canonical import canonical_json_bytes, sha256_json


ROOT = Path(__file__).resolve().parents[1]


def _reference(frequency: float, source: str = "accepted_simulation") -> dict:
    record = {
        "reference_frequency_GHz": frequency,
        "frequency_source": source,
        "calibration_run_id": f"f01_{frequency}",
        "revision": 1,
    }
    record["setting_hash"] = sha256_json(record)
    return record


def _setting(setting_id: str, target: str, waveform: dict) -> dict:
    record = {
        **waveform,
        "setting_id": setting_id,
        "target": target,
        "revision": 1,
        "calibration_run_id": f"{setting_id}_run",
        "status": "accepted",
    }
    record["setting_hash"] = sha256_json(record)
    return record


def _authorities() -> dict:
    authorities = {
        "instruction_profile": {
            "profile_id": "qcis_stage7_calibration_v3",
            "profile_version": "0.3",
        },
        "qagent_registry": {
            "Q1": {
                "component": "q1",
                "xy_channel": "q1_xy",
                "z_channel": "q1_z",
                "local_dimension": 3,
                "anharmonicity_GHz": -0.2,
                "reference_frequency_authority": _reference(5.0),
            },
            "Q2": {
                "component": "q2",
                "xy_channel": "q2_xy",
                "z_channel": "q2_z",
                "local_dimension": 3,
                "anharmonicity_GHz": -0.21,
                "reference_frequency_authority": _reference(5.2, "bootstrap_seed"),
            },
            "C": {"component": "c", "z_channel": "c_flux", "endpoints": ["Q1", "Q2"]},
        },
        "gate_configuration": {
            "Q1": {"active_xy2_setting": "q1_xy2", "xy_pi_impl": False},
            "Q2": {"active_xy2_setting": "q2_xy2", "xy_pi_impl": False},
            "C": {},
        },
        "waveform_registry": {
            "settings": {
                "q1_xy2": _setting(
                    "q1_xy2",
                    "Q1",
                    {
                        "wave_index": 0,
                        "length_samples": 2,
                        "width_samples": 2,
                        "amplitude_GHz": 0.1,
                    },
                ),
                "q2_xy2": _setting(
                    "q2_xy2",
                    "Q2",
                    {
                        "wave_index": 0,
                        "length_samples": 2,
                        "width_samples": 2,
                        "amplitude_GHz": 0.1,
                    },
                ),
            }
        },
        "clock": {"dt_ns": 0.5, "sample_rate_Hz": 2_000_000_000.0},
        "compiler": {"compiler_id": "sqvm_qcis_compiler_v3"},
        "templates": {},
    }
    authorities["expected_sha256"] = {
        name: sha256_json(authorities[name])
        for name in (
            "instruction_profile",
            "qagent_registry",
            "gate_configuration",
            "waveform_registry",
            "clock",
            "compiler",
        )
    }
    return authorities


def _context(*paths: str) -> CircuitExecutionContext:
    return CircuitExecutionContext(
        _authorities(),
        {"q1": 0.1, "q2": 0.0, "c": 0.27},
        frozenset(paths),
    )


@pytest.mark.integration
def test_set_preamble_creates_hashed_overlay_without_mutating_base_setting():
    context = _context("Q1.setting.active_xy2_setting.amplitude_GHz")
    original = copy.deepcopy(context.authorities)
    compiled = compile_circuit(
        QCISCircuit(
            "set_amplitude",
            "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.25\nX2P Q1\n",
        ),
        context,
    )

    assert compiled.executable_source == "X2P Q1\n"
    assert compiled.overlay_sha256
    assert len(compiled.overlays) == 1
    overlay = compiled.overlays[0]
    assert overlay["value"] == 0.25
    assert overlay["base_setting_hash"] != overlay["effective_setting_hash"]
    assert compiled.compilation.trace["steps"][0]["setting"]["setting_hash"] == overlay["effective_setting_hash"]
    assert context.authorities == original
    assert max(abs(value) for value in compiled.compilation.q1_xy) == pytest.approx(0.25)


@pytest.mark.integration
def test_multiple_set_fields_on_one_setting_share_one_base_and_effective_hash():
    context = _context(
        "Q1.setting.active_xy2_setting.amplitude_GHz",
        "Q1.setting.active_xy2_setting.length_samples",
    )
    compiled = compile_circuit(
        QCISCircuit(
            "set_two_fields",
            "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.25\n"
            "SET Q1 setting.active_xy2_setting.length_samples 4\n"
            "X2P Q1\n",
        ),
        context,
    )
    first, second = compiled.overlays
    assert first["base_setting_hash"] == second["base_setting_hash"]
    assert first["effective_setting_hash"] == second["effective_setting_hash"]
    assert compiled.compilation.q1_xy.size == 4


@pytest.mark.parametrize(
    ("source", "code"),
    (
        (
            "X2P Q1\nSET Q1 setting.active_xy2_setting.amplitude_GHz 0.2\n",
            CircuitReasonCode.SET_POSITION_INVALID,
        ),
        (
            "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.2\nSET Q1 setting.active_xy2_setting.amplitude_GHz 0.3\nX2P Q1\n",
            CircuitReasonCode.SET_PATH_INVALID,
        ),
        (
            "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.2\nX2P Q1\n",
            CircuitReasonCode.SET_PATH_NOT_ALLOWED,
        ),
        (
            "SET Q1 setting.active_xy2_setting.frequency_GHz 5.1\nX2P Q1\n",
            CircuitReasonCode.SET_PATH_INVALID,
        ),
        (
            "SET Q1 setting.active_xy2_setting.setting_hash 1\nX2P Q1\n",
            CircuitReasonCode.SET_PATH_INVALID,
        ),
    ),
)
@pytest.mark.integration
def test_set_rejects_non_preamble_duplicate_unapproved_or_nonconfig_paths(source: str, code: CircuitReasonCode):
    paths = frozenset(
        {
            "Q1.setting.active_xy2_setting.amplitude_GHz",
            "Q1.setting.active_xy2_setting.frequency_GHz",
            "Q1.setting.active_xy2_setting.setting_hash",
        }
    )
    context = CircuitExecutionContext(
        _authorities(),
        {"q1": 0.1, "q2": 0.0, "c": 0.27},
        frozenset() if code == CircuitReasonCode.SET_PATH_NOT_ALLOWED else paths,
    )
    with pytest.raises(CircuitExecutionError) as captured:
        compile_circuit(QCISCircuit("invalid_set", source), context)
    assert captured.value.code == code


@pytest.mark.integration
def test_direct_waveform_parameters_remain_qcis_operands_without_set():
    compiled = compile_circuit(
        QCISCircuit("direct_pulse", "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n"),
        _context(),
    )
    assert compiled.overlays == ()
    assert compiled.compilation.trace["steps"][0]["op"] == "PLSXY"
    assert compiled.compilation.plan.drive_event_inventory[0]["f_drive_GHz"] == 5.1


@pytest.mark.integration
def test_batch_is_fully_compiled_before_first_model_point(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(circuits_module, "run_bounded_model_point", lambda *args, **kwargs: calls.append(args))
    circuits = (
        QCISCircuit("valid_first", "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n"),
        QCISCircuit("invalid_second", "X2P Q1\nSET Q1 setting.active_xy2_setting.amplitude_GHz 0.2\n"),
    )
    with pytest.raises(CircuitExecutionError) as captured:
        run_circuits(circuits, _context("Q1.setting.active_xy2_setting.amplitude_GHz"), tmp_path, ROOT)
    assert captured.value.code == CircuitReasonCode.SET_POSITION_INVALID
    assert calls == []


@pytest.mark.integration
def test_batch_limit_can_only_be_tightened(tmp_path: Path):
    circuits = tuple(
        QCISCircuit(f"circuit_{index}", "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
        for index in range(2)
    )
    with pytest.raises(CircuitExecutionError) as captured:
        run_circuits(circuits, _context(), tmp_path, ROOT, max_circuits=1)
    assert captured.value.code == CircuitReasonCode.BATCH_LIMIT_EXCEEDED


@pytest.mark.integration
def test_smoke_context_rejects_unregistered_initial_state_or_observable():
    circuit = QCISCircuit("unsupported_contract", "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
    with pytest.raises(CircuitExecutionError) as captured:
        compile_circuit(
            circuit,
            CircuitExecutionContext(_authorities(), {"q1": 0.1, "q2": 0.0, "c": 0.27}, initial_state_id="q1_excited"),
        )
    assert captured.value.code == CircuitReasonCode.CONFIG_AUTHORITY_INVALID
    with pytest.raises(CircuitExecutionError) as captured:
        compile_circuit(
            circuit,
            CircuitExecutionContext(
                _authorities(),
                {"q1": 0.1, "q2": 0.0, "c": 0.27},
                observable_set_id="arbitrary_observable",
            ),
        )
    assert captured.value.code == CircuitReasonCode.CONFIG_AUTHORITY_INVALID


@pytest.mark.parametrize(
    "readout_qubit",
    (
        [],
        ["Q1"],
        [[], ["Q1"]],
        [["Q1", "Q1"]],
        [["Q3"]],
        [["C"]],
        [["Q1"], ["Q1"]],
        [["Q1", "Q2", "Q1"]],
    ),
)
@pytest.mark.integration
def test_readout_qubit_rejects_invalid_nested_groups_before_execution(
    readout_qubit, monkeypatch, tmp_path: Path
):
    calls = []
    monkeypatch.setattr(circuits_module, "run_bounded_model_point", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(CircuitExecutionError) as captured:
        run_circuits(
            (QCISCircuit("invalid_readout", "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n"),),
            _context(),
            tmp_path,
            ROOT,
            readout_qubit=readout_qubit,
        )
    assert captured.value.code == CircuitReasonCode.READOUT_QUBIT_INVALID
    assert calls == []


@pytest.mark.integration
def test_run_circuits_publishes_outer_execution_evidence(monkeypatch, tmp_path: Path):
    handles = {}

    def fake_run(_compilation, point_id, output_root, _repository_root, *, timeout_s):
        assert timeout_s == 10.0
        root = Path(output_root) / point_id
        root.mkdir()
        manifest = canonical_json_bytes({"kind": "model_manifest"})
        receipt = canonical_json_bytes({"kind": "model_receipt"})
        (root / "manifest.json").write_bytes(manifest)
        (root / "receipt.json").write_bytes(receipt)
        handle = SimpleNamespace(
            artifact_root=root,
            manifest_sha256=hashlib.sha256(manifest).hexdigest().upper(),
            receipt_sha256=hashlib.sha256(receipt).hexdigest().upper(),
            qualification_scope="bounded_smoke_only",
        )
        handles[point_id] = handle
        return handle

    arrays = {
        "population_000": np.array([0.7]),
        "population_100": np.array([0.12]),
        "population_001": np.array([0.08]),
        "population_101": np.array([0.05]),
        "leakage": np.array([0.05]),
        "norm_error": np.array([1.0e-12]),
    }
    monkeypatch.setattr(circuits_module, "run_bounded_model_point", fake_run)
    observable_binding = {
        "evolution_manifest_sha256": "A" * 64,
        "evolution_receipt_sha256": "B" * 64,
        "array_inventory_sha256": "C" * 64,
        "arrays": {},
    }
    monkeypatch.setattr(
        circuits_module,
        "_load_verified_final_observables",
        lambda _root: (arrays, observable_binding),
    )
    source = "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.25\nX2P Q1\n"
    result = run_circuits(
        (QCISCircuit("evidence_case", source),),
        _context("Q1.setting.active_xy2_setting.amplitude_GHz"),
        tmp_path,
        ROOT,
        timeout_s=10.0,
    )[0]

    assert result.dressed_populations.population_100 == 0.12
    assert result.readout_qubit == (("Q1",), ("Q2",))
    assert result.probabilities["Q1"].p1 == pytest.approx(0.17)
    assert result.probabilities["Q2"].p1 == pytest.approx(0.13)
    assert result.evidence_root == tmp_path / "circuit_execution" / "evidence_case"
    evidence = json.loads((result.evidence_root / "evidence.json").read_text("utf-8"))
    assert evidence["schema_version"] == "0.2"
    assert evidence["qcis"]["source"] == source
    assert evidence["set_overlay"]["entries"][0]["base_setting_hash"]
    assert evidence["set_overlay"]["entries"][0]["effective_setting_hash"]
    assert evidence["set_overlay"]["persistent_config_mutated"] is False
    assert evidence["final_observables"]["dressed_populations"]["population_101"] == 0.05
    assert evidence["claim"]["measurement"] is False
    assert evidence["execution_contract"]["readout_qubit_requested"] == [[]]
    assert evidence["execution_contract"]["readout_qubit_effective"] == [["Q1"], ["Q2"]]
    assert (result.evidence_root / "receipt.json").is_file()
    monkeypatch.setattr(
        circuits_module,
        "verify_bounded_model_point",
        lambda artifact_root, _compilation, _repository_root: handles[Path(artifact_root).name],
    )
    verified = circuits_module.verify_circuit_result(result.evidence_root, _context(
        "Q1.setting.active_xy2_setting.amplitude_GHz"
    ), ROOT)
    assert verified.to_dict() == result.to_dict()

    joint = run_circuits(
        (QCISCircuit("joint_case", "X2P Q1\n"),),
        _context(),
        tmp_path,
        ROOT,
        readout_qubit=[["Q1", "Q2"]],
        timeout_s=10.0,
    )[0]
    assert joint.probabilities == {}
    assert joint.readout_qubit == (("Q1", "Q2"),)
    assert dict(joint.readout_probabilities[0].probabilities) == {
        "P00": 0.7,
        "P01": 0.08,
        "P10": 0.12,
        "P11": 0.05,
    }
    joint_evidence = json.loads((joint.evidence_root / "evidence.json").read_text("utf-8"))
    assert joint_evidence["execution_contract"]["readout_qubit_requested"] == [["Q1", "Q2"]]
    assert circuits_module.verify_circuit_result(joint.evidence_root, _context(), ROOT).to_dict() == joint.to_dict()

    combined = run_circuits(
        (QCISCircuit("combined_case", "X2P Q1\n"),),
        _context(),
        tmp_path,
        ROOT,
        readout_qubit=[["Q1"], ["Q2"], ["Q1", "Q2"]],
        timeout_s=10.0,
    )[0]
    assert set(combined.probabilities) == {"Q1", "Q2"}
    assert combined.readout_qubit == (("Q1",), ("Q2",), ("Q1", "Q2"))
    assert dict(combined.readout_probabilities[2].probabilities) == {
        "P00": 0.7,
        "P01": 0.08,
        "P10": 0.12,
        "P11": 0.05,
    }

    reversed_joint = run_circuits(
        (QCISCircuit("reversed_joint", "X2P Q1\n"),),
        _context(),
        tmp_path,
        ROOT,
        readout_qubit=[["Q2", "Q1"]],
        timeout_s=10.0,
    )[0]
    assert dict(reversed_joint.readout_probabilities[0].probabilities) == {
        "P00": 0.7,
        "P01": 0.12,
        "P10": 0.08,
        "P11": 0.05,
    }


@pytest.mark.integration
def test_calibration_scan_profile_uses_scan_executor_and_structural_verifier(
    monkeypatch,
    tmp_path: Path,
):
    handles = {}
    progress = []

    def fake_scan(
        _compilation,
        point_id,
        output_root,
        _repository_root,
        *,
        timeout_s,
        model_configuration,
        idle_flux_phi0,
    ):
        assert model_configuration is None
        assert idle_flux_phi0 == {"q1": 0.1, "q2": 0.0, "c": 0.27}
        assert timeout_s == 600.0
        root = Path(output_root) / point_id
        root.mkdir()
        manifest = canonical_json_bytes({"kind": "scan"})
        receipt = canonical_json_bytes({"kind": "scan"})
        (root / "manifest.json").write_bytes(manifest)
        (root / "receipt.json").write_bytes(receipt)
        handle = SimpleNamespace(
            artifact_root=root,
            manifest_sha256=hashlib.sha256(manifest).hexdigest().upper(),
            receipt_sha256=hashlib.sha256(receipt).hexdigest().upper(),
            qualification_scope="local_calibration_scan_v1",
        )
        handles[point_id] = handle
        return handle

    monkeypatch.setattr(circuits_module, "run_calibration_scan_point", fake_scan)
    monkeypatch.setattr(
        circuits_module,
        "run_bounded_model_point",
        lambda *_args, **_kwargs: pytest.fail("bounded executor must not run"),
    )
    arrays = {
        "population_000": np.array([0.8]),
        "population_100": np.array([0.1]),
        "population_001": np.array([0.05]),
        "population_101": np.array([0.04]),
        "leakage": np.array([0.01]),
        "norm_error": np.array([1.0e-12]),
    }
    monkeypatch.setattr(
        circuits_module,
        "_load_verified_final_observables",
        lambda _root: (arrays, {
            "evolution_manifest_sha256": "C" * 64,
            "evolution_receipt_sha256": "D" * 64,
            "array_inventory_sha256": "E" * 64,
            "arrays": {},
        }),
    )
    result = run_circuits(
        (QCISCircuit("scan_case", "X2P Q1\n"),),
        _context(),
        tmp_path,
        ROOT,
        timeout_s=600.0,
        execution_profile=CircuitExecutionProfile.CALIBRATION_SCAN,
        progress_callback=lambda event: progress.append(dict(event)),
    )[0]
    assert result.qualification_scope == "local_calibration_scan_v1"
    assert [event["event"] for event in progress] == [
        "circuit_started",
        "circuit_completed",
    ]
    assert progress[-1]["completed"] == progress[-1]["total"] == 1

    monkeypatch.setattr(
        circuits_module,
        "verify_calibration_scan_point",
        lambda artifact_root, _compilation, _repository_root, **_kwargs: handles[Path(artifact_root).name],
    )
    monkeypatch.setattr(
        circuits_module,
        "verify_bounded_model_point",
        lambda *_args, **_kwargs: pytest.fail("bounded verifier must not run"),
    )
    verified = circuits_module.verify_circuit_result(result.evidence_root, _context(), ROOT)
    assert verified.to_dict() == result.to_dict()


@pytest.mark.physics_slow
def test_run_circuits_returns_final_q1_q2_probabilities_from_verified_evolution():
    workspace = ROOT / "artifacts" / f".run-circuits-e2e.{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    try:
        results = run_circuits(
            (QCISCircuit("circuit_1", "PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n"),),
            _context(),
            workspace,
            ROOT,
            timeout_s=900.0,
        )
        assert len(results) == 1
        result = results[0]
        assert result.qualification_scope == "bounded_smoke_only"
        assert set(result.probabilities) == {"Q1", "Q2"}
        for probability in result.probabilities.values():
            assert probability.p0 + probability.p1 + result.leakage == pytest.approx(1.0, abs=1.0e-8)
        assert result.norm_error >= 0.0
        assert (result.evidence_root / "receipt.json").is_file()
        assert (result.model_evidence_root / "receipt.json").is_file()
        assert result.to_dict()["probabilities"]["Q1"] == {
            "P0": result.probabilities["Q1"].p0,
            "P1": result.probabilities["Q1"].p1,
            "computational_population": result.probabilities["Q1"].p0 + result.probabilities["Q1"].p1,
            "normalization": "computational_subspace_nonconditional",
        }
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
