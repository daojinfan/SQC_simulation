from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import pytest as _pytest

pytestmark = _pytest.mark.contract


ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "tests" / "fixtures" / "rabi_x2p_acceptance_v1" / "vectors.json"


def _wrap(value: float) -> float:
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def _vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_frozen_axis_and_exact_qcis_program_are_unambiguous() -> None:
    vector = _vectors()
    axis = vector["axis_decimal"]
    start = Decimal(axis["start"])
    stop = Decimal(axis["stop"])
    step = Decimal(axis["step"])
    intervals = (stop - start) / step
    assert intervals == intervals.to_integral_value()
    expected_axis = [float(start + step * index) for index in range(int(intervals) + 1)]
    assert vector["axis_decimal"]["canonical_values"] == expected_axis
    assert expected_axis[0] == 0.0
    assert expected_axis == sorted(set(expected_axis))

    exact = vector["exact_qcis"]
    lines = exact["source"].splitlines()
    assert lines == [
        "SET Q1 setting.active_xy2_setting.amplitude_GHz 0.15",
        "X2P Q1",
        "X2P Q1",
    ]
    assert all("PLSXY" not in line for line in lines)
    assert exact["circuit_id"] == "rabi_q1_0003"


def test_frozen_phase_vectors_encode_absolute_time_not_local_reset() -> None:
    for vector in _vectors()["phase_vectors"]:
        first = vector["first_start_sample"]
        second = vector["second_start_sample"]
        length = vector["length_samples"]
        assert second == first + length

        dt_ns = vector["dt_ns"]
        phi = vector["phi_total_rad"]
        drive = vector["f_drive_GHz"]
        reference = vector["f_ref_GHz"]
        first_time = (first + 0.5) * dt_ns
        second_time = (second + 0.5) * dt_ns
        rotating_first = _wrap(phi + 2.0 * math.pi * (drive - reference) * first_time)
        rotating_second = _wrap(phi + 2.0 * math.pi * (drive - reference) * second_time)
        lab_first = _wrap(phi + 2.0 * math.pi * drive * first_time)
        lab_second = _wrap(phi + 2.0 * math.pi * drive * second_time)
        lab_advance = 2.0 * math.pi * drive * (second - first) * dt_ns

        assert math.isfinite(rotating_first)
        assert math.isfinite(rotating_second)
        assert _wrap(lab_second - lab_first) == _pytest.approx(_wrap(lab_advance))
        assert (lab_first == _pytest.approx(lab_second)) is vector[
            "expect_wrapped_lab_phases_equal"
        ]
        if vector["id"] == "phase_detuned":
            assert rotating_first != _pytest.approx(rotating_second)


def test_frozen_fake_fit_vector_has_one_bracketed_first_lobe() -> None:
    vector = _vectors()["fake_fit_first_lobe"]
    amplitudes = vector["amplitudes_GHz"]
    candidate = vector["x2p_amplitude_GHz"]
    values = [
        vector["offset"]
        + vector["contrast"] * math.sin(math.pi * value / (2.0 * candidate)) ** 2
        for value in amplitudes
    ]
    peak = vector["first_peak_index"]
    assert values[peak - 1] < values[peak] > values[peak + 1]
    assert tuple(amplitudes[peak - 1 : peak + 2]) == tuple(vector["peak_bracket_GHz"])
    assert vector["peak_bracket_GHz"][0] < candidate < vector["peak_bracket_GHz"][2]
