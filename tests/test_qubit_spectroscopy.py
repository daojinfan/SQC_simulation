from __future__ import annotations

from dataclasses import replace
import pytest as _pytest

pytestmark = _pytest.mark.integration

from pathlib import Path
from types import MappingProxyType

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
from sqvm.circuits import CircuitExecutionContext, CircuitResult, DressedPopulations
from sqvm.calibration.spectroscopy import (
    SpectroscopyAxis,
    SpectroscopyError,
    SpectroscopyMode,
    SpectroscopyPulsePolicy,
    SpectroscopyReasonCode,
    SpectroscopyRequest,
    analyze_qubit_spectroscopy,
    build_qubit_capability_adapter,
    expand_qubit_spectroscopy_points,
    run_qubit_spectroscopy,
)
from sqvm.qcis.canonical import sha256_bytes, sha256_json


ROOT = Path(__file__).resolve().parents[1]


def _reference(frequency: float) -> dict:
    value = {
        "reference_frequency_GHz": frequency,
        "frequency_source": "bootstrap_seed",
        "calibration_run_id": f"seed_{frequency}",
        "revision": 1,
    }
    value["setting_hash"] = sha256_json(value)
    return value


def _context(q1_name: str = "Q1", q2_name: str = "Q2") -> CircuitExecutionContext:
    authorities = {
        "instruction_profile": {
            "profile_id": "qcis_stage7_calibration_v3",
            "profile_version": "0.3",
        },
        "qagent_registry": {
            q1_name: {
                "component": "q1",
                "xy_channel": "q1_xy",
                "z_channel": "q1_z",
                "local_dimension": 3,
                "anharmonicity_GHz": -0.2,
                "reference_frequency_authority": _reference(5.0),
            },
            q2_name: {
                "component": "q2",
                "xy_channel": "q2_xy",
                "z_channel": "q2_z",
                "local_dimension": 3,
                "anharmonicity_GHz": -0.21,
                "reference_frequency_authority": _reference(5.2),
            },
            "C": {"component": "c", "z_channel": "c_flux", "endpoints": [q1_name, q2_name]},
        },
        "gate_configuration": {q1_name: {}, q2_name: {}, "C": {}},
        "waveform_registry": {"settings": {}},
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
    return CircuitExecutionContext(
        authorities,
        {"q1": 0.1, "q2": 0.0, "c": 0.27},
    )


def _policy(target: str, *, length: int = 5) -> SpectroscopyPulsePolicy:
    return SpectroscopyPulsePolicy(target, length, 0.001, 2.0)


def _single_request(values=(4.9, 5.0, 5.1), target: str = "Q1") -> SpectroscopyRequest:
    return SpectroscopyRequest(
        SpectroscopyMode.SINGLE,
        "coarse",
        (target,),
        (SpectroscopyAxis(target, tuple(values)),),
        (_policy(target),),
    )


def _parallel_request(
    targets=("Q1", "Q2"),
    q1_values=(4.9, 5.1),
    q2_values=(5.1, 5.3),
) -> SpectroscopyRequest:
    return SpectroscopyRequest(
        SpectroscopyMode.PARALLEL_LOCKSTEP,
        "coarse",
        targets,
        (
            SpectroscopyAxis(targets[0], tuple(q1_values)),
            SpectroscopyAxis(targets[1], tuple(q2_values)),
        ),
        (_policy(targets[0]), _policy(targets[1])),
    )


def _result(circuit_id: str, p000: float, p100: float, p001: float, p101: float) -> CircuitResult:
    dressed = DressedPopulations(p000, p100, p001, p101)
    return CircuitResult(
        circuit_id=circuit_id,
        circuit_sha256="A" * 64,
        executable_qcis_sha256="B" * 64,
        overlay_sha256="C" * 64,
        dressed_populations=dressed,
        readout_qubit=(),
        readout_probabilities=(),
        leakage=1.0 - dressed.computational_population,
        norm_error=0.0,
        evidence_root=Path("evidence") / circuit_id,
        model_evidence_root=Path("model") / circuit_id,
        receipt_sha256="D" * 64,
        qualification_scope="bounded_smoke_only",
    )


def _materialized_result(
    circuit,
    output_root,
    p000: float,
    p100: float,
    p001: float,
    p101: float,
) -> CircuitResult:
    evidence = Path(output_root) / "fake-circuit-evidence" / circuit.circuit_id
    model = Path(output_root) / "fake-model-evidence" / circuit.circuit_id
    evidence.mkdir(parents=True)
    model.mkdir(parents=True)
    (evidence / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
    (model / "model.bin").write_bytes(circuit.source.encode("ascii"))
    return replace(
        _result(circuit.circuit_id, p000, p100, p001, p101),
        circuit_sha256=sha256_bytes(circuit.source.encode("utf-8")),
        readout_qubit=(("Q1",),),
        evidence_root=evidence,
        model_evidence_root=model,
    )


def test_capability_adapter_supports_arbitrary_registered_names():
    adapter = build_qubit_capability_adapter(_context("QA", "QB"))

    assert set(adapter.capabilities) == {"QA", "QB"}
    assert adapter.resolve("QA").backend_component_slot == "q1"
    assert adapter.resolve("QB").projector_bit_index == 1
    assert len(adapter.authority_sha256) == 64


def test_capability_adapter_rejects_tampered_frequency_authority():
    context = _context()
    context.authorities["qagent_registry"]["Q1"]["reference_frequency_authority"][
        "reference_frequency_GHz"
    ] = 5.1

    with pytest.raises(SpectroscopyError) as captured:
        build_qubit_capability_adapter(context)
    assert captured.value.code == SpectroscopyReasonCode.CAPABILITY_INVALID


def test_parallel_lockstep_expands_two_points_not_cartesian_product():
    points = expand_qubit_spectroscopy_points(_parallel_request(), _context())

    assert len(points) == 2
    assert dict(points[0].coordinates_GHz) == {"Q1": 4.9, "Q2": 5.1}
    assert dict(points[1].coordinates_GHz) == {"Q1": 5.1, "Q2": 5.3}
    assert points[0].circuit.source == (
        "PLSXY Q1 1 0 5 0.001 4.9 0 0 2\n"
        "PLSXY Q2 1 0 5 0.001 5.1 0 0 2\n"
    )
    assert points[0].circuit.circuit_id == "sp_" + points[0].point_input_sha256.lower()[:32]
    assert len({point.circuit.circuit_id for point in points}) == 2


@pytest.mark.parametrize(
    ("spectroscopy_request", "code"),
    (
        (_parallel_request(targets=("Q2", "Q1")), SpectroscopyReasonCode.TARGET_ORDER_INVALID),
        (
            _parallel_request(q1_values=(4.9, 5.0), q2_values=(5.1, 5.2, 5.3)),
            SpectroscopyReasonCode.AXIS_INVALID,
        ),
        (
            SpectroscopyRequest(
                SpectroscopyMode.PARALLEL_LOCKSTEP,
                "coarse",
                ("Q1", "Q2"),
                (SpectroscopyAxis("Q1", (4.9,)), SpectroscopyAxis("Q2", (5.1,))),
                (_policy("Q1", length=5), _policy("Q2", length=7)),
            ),
            SpectroscopyReasonCode.PULSE_POLICY_INVALID,
        ),
        (_single_request((4.9, 4.9, 5.1)), SpectroscopyReasonCode.AXIS_INVALID),
    ),
)
def test_planner_rejects_ambiguous_or_invalid_requests(spectroscopy_request, code):
    with pytest.raises(SpectroscopyError) as captured:
        expand_qubit_spectroscopy_points(spectroscopy_request, _context())
    assert captured.value.code == code


def test_run_parallel_delegates_one_batch_and_uses_excited_marginals(monkeypatch, tmp_path: Path):
    captured = []

    def fake_run(circuits, context, output_root, repository_root, **kwargs):
        captured.append(
            {
                "circuits": circuits,
                "context": context,
                "output_root": output_root,
                "repository_root": repository_root,
                "kwargs": kwargs,
            }
        )
        return tuple(
            _materialized_result(circuit, output_root, 0.45, 0.15, 0.20, 0.10)
            for circuit in circuits
        )

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)
    request = _parallel_request()
    context = _context()
    dataset = run_qubit_spectroscopy(request, context, str(tmp_path), str(ROOT), timeout_s=12.0)

    assert [len(call["circuits"]) for call in captured] == [1, 1]
    assert captured[0]["kwargs"]["readout_qubit"] == (("Q1",), ("Q2",), ("Q1", "Q2"))
    assert captured[0]["kwargs"]["timeout_s"] == 12.0
    assert dataset.points[0].target_excited_population["Q1"] == pytest.approx(0.25)
    assert dataset.points[0].target_excited_population["Q2"] == pytest.approx(0.30)
    assert dataset.points[0].primary_observable_id["Q1"] == "Q1.target_excited_marginal"
    assert dataset.dataset_sha256 == sha256_json(dataset.to_dict())


def test_single_uses_spectator_ground_projector_and_peak_analysis(monkeypatch, tmp_path: Path):
    populations = (0.1, 0.7, 0.2)
    next_population = iter(populations)

    def fake_run(circuits, _context, output_root, _repository_root, **_kwargs):
        return tuple(
            _materialized_result(
                circuit,
                output_root,
                0.9 - (p100 := next(next_population)),
                p100,
                0.0,
                0.0,
            )
            for circuit in circuits
        )

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)
    dataset = run_qubit_spectroscopy(_single_request(), _context(), str(tmp_path), str(ROOT))
    analysis = analyze_qubit_spectroscopy(dataset, min_contrast=0.1)

    assert [point.target_excited_population["Q1"] for point in dataset.points] == list(populations)
    assert dataset.points[0].primary_observable_id["Q1"] == "Q1.target_excited_spectators_ground"
    assert analysis.peak_quality_eligible is True
    assert analysis.recommendation_eligible is False
    assert analysis.peaks["Q1"].discrete_frequency_GHz == 5.0
    assert analysis.peaks["Q1"].estimated_frequency_GHz == pytest.approx(5.0045454545)


def test_boundary_or_nonunique_peak_is_not_recommendation_eligible(monkeypatch, tmp_path: Path):
    for label, populations, reason in (
        ("boundary", (0.7, 0.2, 0.1), "peak_at_boundary"),
        ("nonunique", (0.2, 0.7, 0.7), "peak_nonunique"),
    ):
        next_population = iter(populations)

        def fake_run(circuits, _context, output_root, _repository_root, **_kwargs):
            return tuple(
                _materialized_result(
                    circuit,
                    output_root,
                    0.9 - (p100 := next(next_population)),
                    p100,
                    0.0,
                    0.0,
                )
                for circuit in circuits
            )

        monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)
        dataset = run_qubit_spectroscopy(
            _single_request(), _context(), str(tmp_path / label), str(ROOT)
        )
        analysis = analyze_qubit_spectroscopy(dataset)
        assert analysis.peak_quality_eligible is False
        assert analysis.recommendation_eligible is False
        assert analysis.peaks["Q1"].reason == reason
