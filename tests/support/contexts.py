"""Shared deterministic execution contexts for integration tests."""

from __future__ import annotations

from pathlib import Path

from sqvm.calibration.spectroscopy import SpectroscopyAxis, SpectroscopyMode, SpectroscopyPulsePolicy, SpectroscopyRequest
from sqvm.circuits import CircuitExecutionContext, CircuitResult, DressedPopulations
from sqvm.qcis.canonical import sha256_json


def _reference(frequency: float, source: str = "bootstrap_seed") -> dict:
    value = {
        "reference_frequency_GHz": frequency,
        "frequency_source": source,
        "calibration_run_id": f"seed_{frequency}",
        "revision": 1,
    }
    value["setting_hash"] = sha256_json(value)
    return value


def spectroscopy_context(q1_name: str = "Q1", q2_name: str = "Q2") -> CircuitExecutionContext:
    authorities = {
        "instruction_profile": {"profile_id": "qcis_stage7_calibration_v3", "profile_version": "0.3"},
        "qagent_registry": {
            q1_name: {"component": "q1", "xy_channel": "q1_xy", "z_channel": "q1_z", "local_dimension": 3, "anharmonicity_GHz": -0.2, "reference_frequency_authority": _reference(5.0)},
            q2_name: {"component": "q2", "xy_channel": "q2_xy", "z_channel": "q2_z", "local_dimension": 3, "anharmonicity_GHz": -0.21, "reference_frequency_authority": _reference(5.2)},
            "C": {"component": "c", "z_channel": "c_flux", "endpoints": [q1_name, q2_name]},
        },
        "gate_configuration": {q1_name: {}, q2_name: {}, "C": {}},
        "waveform_registry": {"settings": {}},
        "clock": {"dt_ns": 0.5, "sample_rate_Hz": 2_000_000_000.0},
        "compiler": {"compiler_id": "sqvm_qcis_compiler_v3"},
        "templates": {},
    }
    authorities["expected_sha256"] = {name: sha256_json(authorities[name]) for name in ("instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")}
    return CircuitExecutionContext(authorities, {"q1": 0.1, "q2": 0.0, "c": 0.27})


def spectroscopy_result(circuit_id: str, p000: float, p100: float, p001: float, p101: float) -> CircuitResult:
    dressed = DressedPopulations(p000, p100, p001, p101)
    return CircuitResult(circuit_id=circuit_id, circuit_sha256="A" * 64, executable_qcis_sha256="B" * 64, overlay_sha256="C" * 64, dressed_populations=dressed, readout_qubit=(), readout_probabilities=(), leakage=1.0 - dressed.computational_population, norm_error=0.0, evidence_root=Path("evidence") / circuit_id, model_evidence_root=Path("model") / circuit_id, receipt_sha256="D" * 64, qualification_scope="bounded_smoke_only")


def single_spectroscopy_request(values=(4.9, 5.0, 5.1), target: str = "Q1") -> SpectroscopyRequest:
    return SpectroscopyRequest(SpectroscopyMode.SINGLE, "coarse", (target,), (SpectroscopyAxis(target, tuple(values)),), (SpectroscopyPulsePolicy(target, 5, 0.001, 2.0),))


def circuit_execution_context(*paths: str) -> CircuitExecutionContext:
    def accepted_reference(frequency: float, source: str = "accepted_simulation") -> dict:
        record = {"reference_frequency_GHz": frequency, "frequency_source": source, "calibration_run_id": f"f01_{frequency}", "revision": 1}
        record["setting_hash"] = sha256_json(record)
        return record
    def setting(setting_id: str, target: str) -> dict:
        record = {"setting_id": setting_id, "target": target, "revision": 1, "calibration_run_id": f"{setting_id}_run", "status": "accepted", "wave_index": 0, "length_samples": 2, "width_samples": 2, "amplitude_GHz": 0.1}
        record["setting_hash"] = sha256_json(record)
        return record
    authorities = {
        "instruction_profile": {"profile_id": "qcis_stage7_calibration_v3", "profile_version": "0.3"},
        "qagent_registry": {"Q1": {"component": "q1", "xy_channel": "q1_xy", "z_channel": "q1_z", "local_dimension": 3, "anharmonicity_GHz": -0.2, "reference_frequency_authority": accepted_reference(5.0)}, "Q2": {"component": "q2", "xy_channel": "q2_xy", "z_channel": "q2_z", "local_dimension": 3, "anharmonicity_GHz": -0.21, "reference_frequency_authority": accepted_reference(5.2, "bootstrap_seed")}, "C": {"component": "c", "z_channel": "c_flux", "endpoints": ["Q1", "Q2"]}},
        "gate_configuration": {"Q1": {"active_xy2_setting": "q1_xy2", "xy_pi_impl": False}, "Q2": {"active_xy2_setting": "q2_xy2", "xy_pi_impl": False}, "C": {}},
        "waveform_registry": {"settings": {"q1_xy2": setting("q1_xy2", "Q1"), "q2_xy2": setting("q2_xy2", "Q2")}},
        "clock": {"dt_ns": 0.5, "sample_rate_Hz": 2_000_000_000.0}, "compiler": {"compiler_id": "sqvm_qcis_compiler_v3"}, "templates": {},
    }
    authorities["expected_sha256"] = {name: sha256_json(authorities[name]) for name in ("instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")}
    return CircuitExecutionContext(authorities, {"q1": 0.1, "q2": 0.0, "c": 0.27}, frozenset(paths))
