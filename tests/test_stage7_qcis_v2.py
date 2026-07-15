from __future__ import annotations

import hashlib

import numpy as np
import pytest

from sqvm.qcis import QCISCompilationError, QCISReasonCode, compile_qcis, parse_qcis
from sqvm.qcis.waveforms import acz, flattop


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest().upper()


def _authorities(source: str) -> dict:
    rectangle_xy2 = {"wave_index": 0, "length_samples": 2, "width_samples": 2, "amplitude_GHz": 1.0, "frequency_GHz": 5.0}
    rectangle_xy = {"wave_index": 0, "length_samples": 4, "width_samples": 4, "amplitude_GHz": 1.0, "frequency_GHz": 5.0}
    detune = {"control_role": "z", "input_unit": "phi_over_phi0", "envelope_class": "rect", "status": "accepted"}
    composite = {
        "duration_samples": 4,
        "use_f012zbias_mapper": False,
        "use_g2zbias_mapper": False,
        "q0": {"wave_index": 0, "width_samples": 4, "flux_offset_phi0": 0.1},
        "q1": {"wave_index": 0, "width_samples": 4, "flux_offset_phi0": -0.2},
        "coupler": {"wave_index": 0, "width_samples": 4, "flux_offset_phi0": 0.3},
        "q0_calibrated_dynamic_phase_rad": 0.4,
        "q1_calibrated_dynamic_phase_rad": -0.2,
    }
    return {
        "instruction_profile": {"profile_id": "qcis_stage7_calibration_v1", "profile_version": "0.2"},
        "qagent_registry": {
            "Q1": {"component": "q1", "xy_channel": "q1_xy", "z_channel": "q1_z", "local_dimension": 3, "f01_GHz": 5.0, "anharmonicity_GHz": -0.2, "flux_min_phi0": -1.0, "flux_max_phi0": 1.0},
            "Q2": {"component": "q2", "xy_channel": "q2_xy", "z_channel": "q2_z", "local_dimension": 3, "flux_min_phi0": -1.0, "flux_max_phi0": 1.0},
            "C": {"component": "c", "z_channel": "c_flux", "endpoints": ["Q1", "Q2"]},
        },
        "gate_configuration": {
            "Q1": {"active_xy2_setting": "q1_xy2", "active_xy_setting": "q1_xy", "active_xy12_setting": "q1_xy12", "active_detune_setting": "q1_dtn", "active_f012zbias_mapper": "q1_fmap", "xy_pi_impl": False},
            "Q2": {"active_xy2_setting": "q2_xy2", "active_detune_setting": "q2_dtn", "active_f012zbias_mapper": "q2_fmap"},
            "C": {"active_cz_setting": "cz", "active_fsim_setting": "fsim", "active_g2zbias_mapper": "c_gmap"},
        },
        "waveform_registry": {
            "settings": {
                "q1_xy2": rectangle_xy2,
                "q2_xy2": rectangle_xy2,
                "q1_xy": rectangle_xy,
                "q1_xy12": {"wave_index": 0, "length_samples": 3, "width_samples": 3, "amplitude_GHz": 0.4, "frequency_detune_GHz": 0.01},
                "q1_dtn": detune,
                "q2_dtn": detune,
                "cz": composite,
                "fsim": composite,
            },
            "mappers": {
                "q1_fmap": {"f01max_GHz": 5.5, "k_rad_per_phi0": 2.0, "idle_flux_offset_phi0": 0.0},
                "q2_fmap": {"f01max_GHz": 5.5, "k_rad_per_phi0": 2.0, "idle_flux_offset_phi0": 0.0},
                "c_gmap": {"coupling_detune_GHz": [-0.1, 0.0, 0.1], "zbias_offset_phi0": [-0.2, 0.0, 0.2]},
            },
        },
        "clock": {"dt_ns": 0.5},
        "compiler": {"compiler_id": "sqvm_qcis_compiler_v2"},
        "templates": {"case": {"source": source, "bindings": {}}},
    }


def _compile(source: str, authorities: dict | None = None):
    authority = _authorities(source) if authorities is None else authorities
    return compile_qcis(
        {
            "program_schema_version": "0.1",
            "instruction_set_id": "qcis_stage7_calibration_v1",
            "template_id": "case",
            "template_sha256": _sha(source),
            "source_format": "qcis_template",
            "source": source,
            "bindings": {},
        },
        authority,
        idle_flux={"q1": 0.1, "q2": 0.0, "c": 0.27},
    )


def test_parser_accepts_v02_waveforms_and_parse_only_operations():
    source = "PLS C 2 -1 8 0.1 0 0 0 2\nPLSXY Q1 -1 0 1 2 3 4\nRXY Q1 0.2 -1.5\nM Q1\n"
    program = parse_qcis(source)
    assert [instruction.op for instruction in program.instructions] == ["PLS", "PLSXY", "RXY", "M"]
    assert program.instructions[1].fields["length"] == 2


def test_flattop_and_acz_are_deterministic_and_normalized():
    flat, derivative = flattop(9, 2)
    assert flat[0] == flat[-1] == 0.0
    assert flat[4] == 1.0
    assert np.allclose(flat, flat[::-1])
    assert np.allclose(derivative, -derivative[::-1])
    shaped, _ = acz(9, 1.2, 0.2, 0.1, 0.2)
    assert shaped[0] == shaped[-1] == 0.0
    assert np.all(np.isfinite(shaped))


def test_direct_xy_ignores_rz_and_absolute_overlap_adds():
    source = "RZ Q1 0.5\nPLSXY Q1 0 0 4 1 5 0 0 4\nI Q1 3\nB Q1 Q2\nPLSXY Q1 -1 1 1 1 0 0\n"
    result = _compile(source)
    assert np.array_equal(result.q1_xy.real, np.array([1.0, 2.0, 2.0, 1.0, 0.0, 0.0, 0.0]))
    assert np.all(result.q1_xy.imag == 0.0)
    assert result.trace["final_frames"]["Q1"] == 0.5
    assert result.trace["final_cursors"]["Q1"] == {"xy": 7, "z": 7}


def test_flux_is_idle_relative_and_overlaps_add():
    source = "PLS Q1 0 0 4 0.1 0 0 0 4\nPLS Q1 0 2 4 0.2 0 0 0 4\n"
    result = _compile(source)
    assert np.allclose(result.q1_flux, [0.1, 0.1, 0.3, 0.3, 0.2, 0.2])


def test_i_advances_xy_and_z_without_aligning_first():
    source = "PLSXY Q1 0 10 1 1 5 0 0 1\nPLS Q1 0 2 1 0.1 0 0 0 1\nI Q1 2\n"
    result = _compile(source)
    assert result.trace["final_cursors"]["Q1"] == {"xy": 13, "z": 5}


def test_named_and_arbitrary_rotations_keep_fixed_duration():
    source = "X Q1\nRXY Q1 0 0\nX2M Q1\nY2M Q1\n"
    result = _compile(source)
    assert result.q1_xy.shape == (12,)
    assert np.allclose(result.q1_xy[:4], 1.0)
    assert np.all(result.q1_xy[4:8] == 0.0)
    assert np.allclose(result.q1_xy[8:10], -1.0)
    assert np.allclose(result.q1_xy[10:12], 1j)


def test_virtual_z_changes_high_level_gate_phase_but_not_duration():
    source = "S Q1\nX2P Q1\n"
    result = _compile(source)
    assert result.q1_xy.shape == (2,)
    assert np.allclose(result.q1_xy, 1j)


def test_dtn_and_cz_emit_three_synchronous_delta_waveforms_and_phase_corrections():
    source = "DTN Q1 2 0.05\nCZ C\n"
    result = _compile(source)
    assert np.allclose(result.q1_flux, [0.05, 0.05, 0.1, 0.1, 0.1, 0.1])
    assert np.allclose(result.q2_flux, [0.0, 0.0, -0.2, -0.2, -0.2, -0.2])
    assert np.allclose(result.c_flux, [0.0, 0.0, 0.3, 0.3, 0.3, 0.3])
    assert result.trace["final_frames"]["Q1"] == -0.4
    assert result.trace["final_frames"]["Q2"] == 0.2


def test_composite_mapper_switches_resolve_external_mapper_records():
    source = "CZ C\n"
    authorities = _authorities(source)
    setting = authorities["waveform_registry"]["settings"]["cz"]
    setting["use_f012zbias_mapper"] = True
    setting["use_g2zbias_mapper"] = True
    setting["q0"] = {"wave_index": 0, "width_samples": 4, "frequency_detune_GHz": -0.05}
    setting["q1"] = {"wave_index": 0, "width_samples": 4, "frequency_detune_GHz": -0.05}
    setting["coupler"] = {"wave_index": 0, "width_samples": 4, "coupling_detune_GHz": 0.05}
    result = _compile(source, authorities)
    assert np.any(result.q1_flux != 0.0)
    assert np.any(result.q2_flux != 0.0)
    assert np.allclose(result.c_flux, 0.1)
    assert set(result.trace["steps"][0]["mappers"]) == {"Q1", "Q2", "C"}


def test_fsim_uses_its_own_active_setting_and_missing_mapper_fails_closed():
    source = "FSIM C\n"
    result = _compile(source)
    assert np.allclose(result.c_flux, 0.3)
    authorities = _authorities(source)
    setting = authorities["waveform_registry"]["settings"]["fsim"]
    setting["use_g2zbias_mapper"] = True
    setting["coupler"] = {"wave_index": 0, "width_samples": 4, "coupling_detune_GHz": 0.05}
    authorities["gate_configuration"]["C"].pop("active_g2zbias_mapper")
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source, authorities)
    assert captured.value.code == QCISReasonCode.MAPPER_NOT_FOUND


def test_x12_uses_f12_carrier_and_requires_three_levels():
    source = "X12 Q1\n"
    result = _compile(source)
    assert result.plan.carrier_metadata["Q1"] == pytest.approx(4.81)
    authorities = _authorities(source)
    authorities["qagent_registry"]["Q1"]["local_dimension"] = 2
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source, authorities)
    assert captured.value.code == QCISReasonCode.SETTING_INVALID


def test_parse_only_operation_fails_at_lowering_not_parsing():
    source = "M Q1\n"
    assert parse_qcis(source).instructions[0].op == "M"
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source)
    assert captured.value.code == QCISReasonCode.PARSE_ONLY_OPERATION
