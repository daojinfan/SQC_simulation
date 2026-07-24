from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.contract

import hashlib
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from sqvm.qcis import QCISCompilationError, QCISReasonCode, admit_program, compile_qcis, verify_compilation, verify_drive_event_inventory
from sqvm.qcis.canonical import sha256_bytes, sha256_json
from sqvm.control import (
    ParameterizedControlError,
    ParameterizedControlReasonCode,
    adapt_qcis_v03_compilation,
    build_parameterized_control_context,
    compile_qcis_waveform_plan,
    load_control_chain_config,
    load_control_channel_registry,
    verify_parameterized_control_artifact,
    write_parameterized_control_artifact,
)


def _sha(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest().upper()


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
    record = dict(waveform)
    record.update({
        "setting_id": setting_id,
        "target": target,
        "revision": 1,
        "calibration_run_id": f"{setting_id}_run",
        "status": "accepted",
    })
    record["setting_hash"] = sha256_json(record)
    return record


def _refresh_expected(authorities: dict) -> None:
    authorities["expected_sha256"] = {
        name: sha256_json(authorities[name])
        for name in ("instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")
    }


def _refresh_setting(record: dict) -> None:
    record["setting_hash"] = sha256_json({name: value for name, value in record.items() if name != "setting_hash"})


def _authorities(source: str) -> dict:
    xy = {"wave_index": 0, "length_samples": 4, "width_samples": 4, "amplitude_GHz": 1.0}
    result = {
        "instruction_profile": {"profile_id": "qcis_stage7_calibration_v3", "profile_version": "0.3"},
        "qagent_registry": {
            "Q1": {
                "component": "q1", "xy_channel": "q1_xy", "z_channel": "q1_z", "local_dimension": 3,
                "anharmonicity_GHz": -0.2, "reference_frequency_authority": _reference(5.0),
            },
            "Q2": {
                "component": "q2", "xy_channel": "q2_xy", "z_channel": "q2_z", "local_dimension": 3,
                "anharmonicity_GHz": -0.21, "reference_frequency_authority": _reference(5.2, "bootstrap_seed"),
            },
            "C": {"component": "c", "z_channel": "c_flux", "endpoints": ["Q1", "Q2"]},
        },
        "gate_configuration": {
            "Q1": {"active_xy_setting": "q1_xy", "active_xy2_setting": "q1_xy2", "active_xy12_setting": "q1_xy12", "xy_pi_impl": True},
            "Q2": {"active_xy_setting": "q2_xy", "active_xy2_setting": "q2_xy2", "active_xy12_setting": "q2_xy12", "xy_pi_impl": True},
            "C": {},
        },
        "waveform_registry": {
            "settings": {
                "q1_xy": _setting("q1_xy", "Q1", xy),
                "q1_xy2": _setting("q1_xy2", "Q1", {"wave_index": 0, "length_samples": 2, "width_samples": 2, "amplitude_GHz": 1.0}),
                "q1_xy12": _setting("q1_xy12", "Q1", {"wave_index": 0, "length_samples": 4, "width_samples": 4, "amplitude_GHz": 0.5, "transition": "12"}),
                "q2_xy": _setting("q2_xy", "Q2", xy),
                "q2_xy2": _setting("q2_xy2", "Q2", {"wave_index": 0, "length_samples": 2, "width_samples": 2, "amplitude_GHz": 1.0}),
                "q2_xy12": _setting("q2_xy12", "Q2", {"wave_index": 0, "length_samples": 4, "width_samples": 4, "amplitude_GHz": 0.5, "transition": "12"}),
            },
        },
        "clock": {"dt_ns": 0.5, "sample_rate_Hz": 2_000_000_000.0},
        "compiler": {"compiler_id": "sqvm_qcis_compiler_v3"},
        "templates": {"case": {"source": source, "bindings": {}}},
    }
    _refresh_expected(result)
    return result


def _compile(source: str, authorities: dict | None = None):
    return compile_qcis(
        {
            "program_schema_version": "0.3",
            "instruction_set_id": "qcis_stage7_calibration_v3",
            "template_id": "case",
            "template_sha256": _sha(source),
            "source_format": "qcis_template",
            "source": source,
            "bindings": {},
        },
        _authorities(source) if authorities is None else authorities,
        idle_flux={"q1": 0.1, "q2": 0.0, "c": 0.27},
    )


def test_v03_public_template_admission_accepts_the_new_profile_schema():
    source = "X Q1\n"
    admitted = admit_program(
        {
            "program_schema_version": "0.3",
            "instruction_set_id": "qcis_stage7_calibration_v3",
            "template_id": "case",
            "template_sha256": _sha(source),
            "source_format": "qcis_template",
            "source": source,
            "bindings": {},
        },
        {"template_id": "case", "source": source, "bindings": {}},
    )
    assert admitted.instruction_set_id == "qcis_stage7_calibration_v3"


def test_v03_x_uses_reference_frame_and_x12_has_absolute_time_detuning_ramp():
    result = _compile("X Q1\nX12 Q1\n")
    assert result.plan.carrier_metadata == {}
    assert result.plan.frame_reference_frequency_GHz == {"q1": 5.0, "q2": 5.2}
    assert np.allclose(result.q1_xy[:4], 1.0)
    event = result.plan.drive_event_inventory[1]
    assert event["transition"] == "12"
    assert event["f_drive_GHz"] == pytest.approx(4.8)
    assert event["detuning_GHz"] == pytest.approx(-0.2)
    assert not np.allclose(result.q1_xy[4:], result.q1_xy[4])


def test_v03_x_and_x12_coexist_without_scalar_carrier_conflict_and_are_deterministic():
    source = "X Q1\nX12 Q1\n"
    first = _compile(source)
    second = _compile(source)
    assert len(first.plan.drive_event_inventory) == 2
    assert np.array_equal(first.q1_xy, second.q1_xy)
    assert first.trace_bytes == second.trace_bytes
    verify_drive_event_inventory(first)


def test_v03_direct_plsxy_has_absolute_time_phase_and_ignores_rz_frame():
    source = "RZ Q1 0.7\nI Q1 4\nPLSXY Q1 0 0 2 1 5.2 0 0 2\n"
    result = _compile(source)
    theta = math.remainder(2.0 * math.pi * 0.2 * 0.25, 2.0 * math.pi)
    assert result.q1_xy[0] == pytest.approx(complex(math.cos(-theta), math.sin(-theta)))
    event = result.plan.drive_event_inventory[0]
    assert event["actual_start_sample"] == 0
    assert event["phase_total_rad"] == 0.0


def test_v03_nonzero_detuning_raw_c16_bytes_are_frozen():
    result = _compile("PLSXY Q1 0 3 3 0.125 5.25 0.3 0 3\n")
    assert result.q1_xy.dtype == np.dtype("<c16")
    assert result.q1_xy.tobytes().hex() == (
        "00000000000000000000000000000000"
        "00000000000000000000000000000000"
        "00000000000000000000000000000000"
        "4eeb51e9d3dcbfbf"
        "37a11b176eb287bf1a71c330f39fb8bf822cf4e78b6fb43f49a11b176eb287bf"
        "4eeb51e9d3dcbf3f"
    )
    assert sha256_bytes(result.q1_xy.tobytes()) == "67121800439CF4F3CE76203FA653C39681D0EA891B5A2F559264656C5A9EA7D3"
    assert result.plan.drive_event_inventory[0]["logical_array_contribution_sha256"] == "56E53C9DACE683D05CA318A468D4671237B3BA0FBB4928E69A574149AE6481C7"


def test_v03_frequency_scan_binding_changes_bytes_and_event_evidence():
    template = "PLSXY Q1 0 -1 2 1 $frequency 0 0 2\n"
    authorities = _authorities(template)
    authorities["templates"]["case"]["bindings"] = {
        "frequency": {"unit": "GHz", "occurrences": 1, "position": [0, "frequency"]},
    }

    def compile_point(value: float):
        return compile_qcis(
            {
                "program_schema_version": "0.3",
                "instruction_set_id": "qcis_stage7_calibration_v3",
                "template_id": "case",
                "template_sha256": _sha(template),
                "source_format": "qcis_template",
                "source": template,
                "bindings": {"frequency": {"scan_ref": "drive_frequency", "unit": "GHz"}},
            },
            authorities,
            idle_flux={"q1": 0.1, "q2": 0.0, "c": 0.27},
            scan_values={"drive_frequency": {"value": value, "unit": "GHz"}},
        )

    first = compile_point(5.25)
    second = compile_point(5.3)
    assert first.concrete_source == "PLSXY Q1 0 -1 2 1 5.25 0 0 2\n"
    assert second.concrete_source == "PLSXY Q1 0 -1 2 1 5.3 0 0 2\n"
    assert sha256_bytes(first.q1_xy.tobytes()) != sha256_bytes(second.q1_xy.tobytes())
    assert first.trace_bytes != second.trace_bytes
    assert first.plan.drive_event_inventory[0]["f_drive_GHz"] == 5.25
    assert second.plan.drive_event_inventory[0]["detuning_GHz"] == pytest.approx(0.3)


def test_v03_idle_changes_append_start_but_never_resets_detuning_phase():
    immediate = _compile("X12 Q1\n")
    delayed = _compile("I Q1 2\nX12 Q1\n")
    assert delayed.plan.drive_event_inventory[0]["actual_start_sample"] == 2
    assert not np.allclose(immediate.q1_xy, delayed.q1_xy[2:])


@pytest.mark.parametrize("frequency", [6.0, 6.1])
def test_v03_rejects_nyquist_and_out_of_band_direct_drive(frequency: float):
    source = f"PLSXY Q1 0 -1 1 1 {frequency} 0 0 1\n"
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source)
    assert captured.value.code == QCISReasonCode.DRIVE_DETUNING_OUT_OF_BAND


@pytest.mark.parametrize(("frequency", "detuning"), [(4.75, -0.25), (5.999999, 0.999999)])
def test_v03_accepts_negative_and_strictly_sub_nyquist_detuning(frequency: float, detuning: float):
    result = _compile(f"PLSXY Q1 0 -1 1 1 {frequency} 0 0 1\n")
    assert result.plan.drive_event_inventory[0]["detuning_GHz"] == pytest.approx(detuning)


def test_v03_reference_and_event_tampering_fail_closed():
    result = _compile("X12 Q1\n")
    candidate = {
        "dt_ns": result.plan.dt_ns,
        "sample_rate_Hz": result.plan.sample_rate_Hz,
        "frame_reference_frequency_GHz": dict(result.plan.frame_reference_frequency_GHz),
        "frame_reference_authority_sha256": dict(result.plan.frame_reference_authority_sha256),
        "drive_event_inventory": [dict(event) for event in result.plan.drive_event_inventory],
    }
    candidate["drive_event_inventory"][0]["detuning_GHz"] = 0.0
    with pytest.raises(QCISCompilationError) as captured:
        verify_drive_event_inventory(result, candidate)
    assert captured.value.code == QCISReasonCode.DRIVE_EVENT_EVIDENCE_MISMATCH

    arrays = {name: np.asarray(getattr(result, name)).copy() for name in ("q1_xy", "q2_xy", "q1_flux", "q2_flux", "c_flux")}
    arrays["q1_xy"][0] *= -1.0
    with pytest.raises(QCISCompilationError) as captured:
        verify_compilation(result, arrays)
    assert captured.value.code == QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH


@pytest.mark.parametrize(
    "mutate",
    [
        lambda result: replace(result, concrete_source=result.concrete_source + "I Q1 1\n"),
        lambda result: replace(result, ast_bytes=result.ast_bytes + b" "),
        lambda result: replace(result, trace_bytes=result.trace_bytes + b" "),
    ],
)
def test_v03_handoff_verifies_source_ast_and_trace_bytes(mutate):
    result = _compile("X Q1\n")
    with pytest.raises(QCISCompilationError) as captured:
        verify_compilation(mutate(result))
    assert captured.value.code == QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH


def test_v03_reference_authority_and_x12_transition_are_strict():
    source = "X12 Q1\n"
    authorities = _authorities(source)
    authorities["qagent_registry"]["Q1"]["reference_frequency_authority"]["setting_hash"] = "bad"
    _refresh_expected(authorities)
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source, authorities)
    assert captured.value.code == QCISReasonCode.REFERENCE_FREQUENCY_INVALID

    authorities = _authorities(source)
    authorities["waveform_registry"]["settings"]["q1_xy12"]["transition"] = "01"
    _refresh_expected(authorities)
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source, authorities)
    assert captured.value.code == QCISReasonCode.SETTING_INVALID

    authorities = _authorities("X Q1\n")
    authorities["waveform_registry"]["settings"]["q1_xy"].pop("status")
    _refresh_expected(authorities)
    with pytest.raises(QCISCompilationError) as captured:
        _compile("X Q1\n", authorities)
    assert captured.value.code == QCISReasonCode.SETTING_INVALID


def test_v03_qagent_frequency_or_anharmonicity_tamper_breaks_bound_authority_hash():
    source = "X12 Q1\n"
    authorities = _authorities(source)
    authorities["qagent_registry"]["Q1"]["anharmonicity_GHz"] = -0.19
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source, authorities)
    assert captured.value.code == QCISReasonCode.QAGENT_AUTHORITY_HASH_MISMATCH


def test_v03_rejects_macro_settings_that_duplicate_absolute_drive_frequency():
    source = "X12 Q1\n"
    authorities = _authorities(source)
    setting = authorities["waveform_registry"]["settings"]["q1_xy12"]
    setting["frequency_GHz"] = 4.81
    _refresh_setting(setting)
    _refresh_expected(authorities)
    with pytest.raises(QCISCompilationError) as captured:
        _compile(source, authorities)
    assert captured.value.code == QCISReasonCode.SETTING_INVALID


def test_v03_compilation_reaches_a_verified_stage41_control_handle(tmp_path: Path):
    qcis = _compile("PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
    config = load_control_chain_config("configs/control/2q1c2r_control_smoke.yaml")
    registry = load_control_channel_registry("configs/control/2q1c2r_channels.yaml")
    context = build_parameterized_control_context(
        config,
        registry,
        {name: (-0.5, 0.5) for name in ("q1", "q2", "c")},
        device_limit_authority_sha256="D" * 64,
        repository_root=Path.cwd().resolve(),
        output_root=tmp_path.resolve(),
        authority_sha256={"stage4_1": "A" * 64},
        expected_plan_authority_sha256=qcis.plan.authority_sha256,
        stage4_compatibility_approved=True,
        compiler_source_snapshot={"source_sha256": "B" * 64},
        environment_snapshot={"environment_sha256": "C" * 64},
        publication_policy={"mode": "atomic_no_replace"},
    )
    admitted = adapt_qcis_v03_compilation(qcis, "point_integration", context)
    compilation = compile_qcis_waveform_plan(admitted, context)
    publication = write_parameterized_control_artifact(compilation, context, tmp_path / "point_integration")
    handle = verify_parameterized_control_artifact(publication["artifact_root"], context, admitted)

    assert handle.schema_version == "0.1"
    assert handle.control_id == publication["control_id"]
    assert np.max(np.abs(handle.xy_drive_GHz["q1"][0])) > 0.0
    assert np.array_equal(
        handle.absolute_flux_phi0["c"],
        np.full(handle.time_center_ns.size, 0.27, dtype="<f8"),
    )


def test_stage41_adapter_rejects_a_qcis_clock_mismatch(tmp_path: Path):
    source = "X Q1\n"
    authorities = _authorities(source)
    authorities["clock"] = {"dt_ns": 1.0, "sample_rate_Hz": 1_000_000_000.0}
    _refresh_expected(authorities)
    qcis = _compile(source, authorities)
    context = build_parameterized_control_context(
        load_control_chain_config("configs/control/2q1c2r_control_smoke.yaml"),
        load_control_channel_registry("configs/control/2q1c2r_channels.yaml"),
        {name: (-0.5, 0.5) for name in ("q1", "q2", "c")},
        device_limit_authority_sha256="D" * 64,
        repository_root=Path.cwd().resolve(), output_root=tmp_path.resolve(),
        authority_sha256={"stage4_1": "A" * 64}, expected_plan_authority_sha256=qcis.plan.authority_sha256,
        stage4_compatibility_approved=True, compiler_source_snapshot={}, environment_snapshot={}, publication_policy={},
    )
    with pytest.raises(ParameterizedControlError) as captured:
        adapt_qcis_v03_compilation(qcis, "point_clock", context)
    assert captured.value.code == ParameterizedControlReasonCode.CLOCK_MISMATCH
