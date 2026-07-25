"""Deterministic synthetic runners shared by workflow integration tests."""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

import sqvm.calibration.spectroscopy as spectroscopy_module
from sqvm.qcis.canonical import sha256_bytes

from tests.support.contexts import single_spectroscopy_request, spectroscopy_result


def install_synthetic_spectroscopy_runner(monkeypatch, calls, *, cross_excitation: float = 0.005) -> None:
    centers = {"Q1": 5.0, "Q2": 5.2}
    def fake_run(circuits, _context_value, output_root, _repository_root, **kwargs):
        evidence_root = Path(output_root); evidence_root.mkdir(parents=True, exist_ok=True)
        calls.append({"circuits": circuits, "readout_qubit": kwargs["readout_qubit"], "execution_profile": kwargs["execution_profile"].value})
        results = []
        for circuit in circuits:
            frequencies = {line.split(" ")[1]: float(line.split(" ")[6]) for line in circuit.source.strip().splitlines()}
            driven = set(frequencies)
            q1 = 0.02 + 0.33 * math.exp(-((frequencies["Q1"] - centers["Q1"]) / 0.055) ** 2) if "Q1" in driven else cross_excitation
            q2 = 0.02 + 0.33 * math.exp(-((frequencies["Q2"] - centers["Q2"]) / 0.055) ** 2) if "Q2" in driven else cross_excitation
            result = spectroscopy_result(circuit.circuit_id, 1.0 - 0.001 - q1 - q2, q1, q2, 0.0)
            circuit_evidence = evidence_root / "circuit-execution-evidence" / circuit.circuit_id; model_evidence = evidence_root / circuit.circuit_id
            circuit_evidence.mkdir(parents=True, exist_ok=True); model_evidence.mkdir(parents=True, exist_ok=True)
            (circuit_evidence / "result.bin").write_bytes(circuit.circuit_id.encode("ascii")); (model_evidence / "model.bin").write_bytes(b"synthetic")
            results.append(
                replace(
                    result,
                    circuit_sha256=sha256_bytes(circuit.source.encode("utf-8")),
                    readout_qubit=tuple(tuple(group) for group in kwargs["readout_qubit"]),
                    evidence_root=circuit_evidence,
                    model_evidence_root=model_evidence,
                )
            )
        return tuple(results)
    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


def install_evidence_runner(monkeypatch) -> None:
    def fake_run(circuits, _context_value, output_root, _repository_root, **kwargs):
        execution_root = Path(output_root); results = []
        for circuit in circuits:
            evidence_root = execution_root / "circuits" / circuit.circuit_id
            evidence_root.mkdir(parents=True); (evidence_root / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
            results.append(
                replace(
                    spectroscopy_result(circuit.circuit_id, 0.8, 0.19, 0.0, 0.0),
                    circuit_sha256=sha256_bytes(circuit.source.encode("utf-8")),
                    readout_qubit=tuple(tuple(group) for group in kwargs["readout_qubit"]),
                    evidence_root=evidence_root,
                    model_evidence_root=evidence_root,
                )
            )
        return tuple(results)
    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


def scan_spectroscopy_request():
    return replace(single_spectroscopy_request(), run_phase="scan")
