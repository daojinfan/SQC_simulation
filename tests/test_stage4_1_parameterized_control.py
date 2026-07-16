from __future__ import annotations

import hashlib
import inspect
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sqvm.control import (
    ParameterizedControlError,
    ParameterizedControlReasonCode,
    ControlBuildContext,
    build_parameterized_control_context,
    adapt_qcis_v03_compilation,
    compile_control_schedule,
    compile_qcis_waveform_plan,
    admit_qcis_v03_plan,
    load_control_chain_config,
    load_control_channel_registry,
    load_logical_schedule,
)
from sqvm.control.stage4_1_compile import _quantize_half_even
from sqvm.qcis.canonical import sha256_json


def _sha(value: np.ndarray) -> str:
    return hashlib.sha256(value.tobytes(order="C")).hexdigest().upper()


def _hashes(prefix: str) -> dict[str, str]:
    return {"qcis": prefix * 64}


def _context(*, bounds: tuple[float, float] = (-0.5, 0.5)):
    config = load_control_chain_config("configs/control/2q1c2r_control_smoke.yaml")
    registry = load_control_channel_registry("configs/control/2q1c2r_channels.yaml")
    return build_parameterized_control_context(
        config,
        registry,
        {name: bounds for name in ("q1", "q2", "c")},
        device_limit_authority_sha256="D" * 64,
        repository_root=Path.cwd(),
        output_root=(Path.cwd() / "output").resolve(),
        authority_sha256=_hashes("A"),
        expected_plan_authority_sha256=_hashes("B"),
        stage4_compatibility_approved=True,
        compiler_source_snapshot={"sha256": "C" * 64},
        environment_snapshot={"python": "test"},
        publication_policy={"mode": "disabled"},
    )


def _plan(*, n: int = 4, q1_flux: float = 0.0, schema_version: str = "0.3") -> dict:
    arrays = {
        "logical.xy_delta_GHz.q1.i": np.zeros(n, dtype="<f8"),
        "logical.xy_delta_GHz.q1.q": np.zeros(n, dtype="<f8"),
        "logical.xy_delta_GHz.q2.i": np.zeros(n, dtype="<f8"),
        "logical.xy_delta_GHz.q2.q": np.zeros(n, dtype="<f8"),
        "logical.flux_delta_phi0.q1": np.full(n, q1_flux, dtype="<f8"),
        "logical.flux_delta_phi0.q2": np.zeros(n, dtype="<f8"),
        "logical.flux_delta_phi0.c": np.zeros(n, dtype="<f8"),
    }
    units = {name: "GHz" if ".xy_" in name else "Phi/Phi0" for name in arrays}
    inventory = {
        name: {"name": name, "dtype": "<f8", "shape": [n], "unit": units[name], "byte_length": value.nbytes, "sha256": _sha(value)}
        for name, value in arrays.items()
    }
    return {
        "schema_version": schema_version,
        "profile_id": "qcis_stage7_calibration_v3",
        "point_id": "point_001",
        "concrete_source_sha256": "1" * 64,
        "ast_sha256": "2" * 64,
        "trace_sha256": "3" * 64,
        "sample_count": n,
        "dt_ns": 0.5,
        "logical": {
            "xy_delta_GHz": {"q1": {"i": arrays["logical.xy_delta_GHz.q1.i"], "q": arrays["logical.xy_delta_GHz.q1.q"]}, "q2": {"i": arrays["logical.xy_delta_GHz.q2.i"], "q": arrays["logical.xy_delta_GHz.q2.q"]}},
            "flux_delta_phi0": {"q1": arrays["logical.flux_delta_phi0.q1"], "q2": arrays["logical.flux_delta_phi0.q2"], "c": arrays["logical.flux_delta_phi0.c"]},
        },
        "frame_reference_frequency_GHz": {"q1": 5.0, "q2": 5.1},
        "frame_reference_authority_sha256": {"q1": "4" * 64, "q2": "5" * 64},
        "array_inventory": inventory,
        "drive_event_inventory": [],
        "drive_event_inventory_sha256": sha256_json([]),
        "authority_sha256": _hashes("B"),
    }


def _admitted(plan: dict, context):
    return admit_qcis_v03_plan(plan, context)


def _refresh_inventory(plan: dict) -> None:
    xy = plan["logical"]["xy_delta_GHz"]
    flux = plan["logical"]["flux_delta_phi0"]
    arrays = {
        "logical.xy_delta_GHz.q1.i": xy["q1"]["i"],
        "logical.xy_delta_GHz.q1.q": xy["q1"]["q"],
        "logical.xy_delta_GHz.q2.i": xy["q2"]["i"],
        "logical.xy_delta_GHz.q2.q": xy["q2"]["q"],
        "logical.flux_delta_phi0.q1": flux["q1"],
        "logical.flux_delta_phi0.q2": flux["q2"],
        "logical.flux_delta_phi0.c": flux["c"],
    }
    for name, value in arrays.items():
        plan["array_inventory"][name]["sha256"] = _sha(value)


def _event() -> dict:
    return {
        "event_id": "drive_001",
        "source_instruction_index": 0,
        "target": "Q1",
        "transition": "01",
        "actual_start_sample": 0,
        "sample_count": 1,
        "phase_rule_id": "qcis_v03_absolute_detuning_phase_v1",
        "phase_total_rad": 0.0,
        "f_drive_GHz": 5.1,
        "f_ref_GHz": 5.0,
        "detuning_GHz": 0.1,
        "logical_array_contribution_sha256": "6" * 64,
        "setting_evidence": {"setting_id": "q1_xy_v3", "revision": 1, "setting_hash": "7" * 64, "calibration_run_id": "q1_xy_v3_run"},
    }


def test_v03_plan_runs_full_electronics_chain_and_adds_idle_once():
    context = _context()
    result = compile_qcis_waveform_plan(admit_qcis_v03_plan(_plan(q1_flux=0.1), context), context)
    assert result.point_id == "point_001"
    assert np.allclose(result.logical_flux_absolute_phi0["q1"], 0.2)
    for name, idle in (("q1", 0.1), ("q2", 0.0), ("c", 0.27)):
        assert np.allclose(
            result.effective_absolute_flux_phi0[name] - result.effective_flux_delta_phi0[name],
            np.full(result.effective_time_center_ns.size, idle),
        )
    assert result.dac_codes["q1_z"].dtype == np.dtype("<i8")
    assert result.effective_time_center_ns.size > result.plan.sample_count
    assert result.logical_arrays["flux_delta_phi0"]["q1"] is result.plan.flux_q1
    assert result.awg_arrays["q1_z"]["dac_codes"] is result.dac_codes["q1_z"]
    assert result.effective_arrays["absolute_flux_phi0"]["q1"] is result.effective_absolute_flux_phi0["q1"]
    assert result.source_binding["point_id"] == result.point_id
    assert result.authority_binding["control_authority_sha256"] == _hashes("A")
    with pytest.raises((TypeError, ValueError, RuntimeError)):
        result.effective_arrays["absolute_flux_phi0"]["q1"][0] = 0.0


def test_zero_delta_reconstructs_the_idle_vector_at_every_effective_sample():
    context = _context()
    result = compile_qcis_waveform_plan(_admitted(_plan(), context), context)
    for name, idle in (("q1", 0.1), ("q2", 0.0), ("c", 0.27)):
        assert np.array_equal(result.effective_absolute_flux_phi0[name], np.full(result.effective_time_center_ns.size, idle))


def test_xy_impulse_has_the_exact_aligned_center_and_full_fir_tail():
    context = _context()
    plan = _plan()
    plan["logical"]["xy_delta_GHz"]["q1"]["i"][0] = 0.01
    _refresh_inventory(plan)
    result = compile_qcis_waveform_plan(_admitted(plan, context), context)
    lmax = 26
    assert result.effective_time_center_ns[lmax] == pytest.approx(0.25)
    assert result.effective_xy_drive_GHz["q1_i"][lmax:lmax + 2] == pytest.approx([0.008, 0.002], abs=2e-6)
    assert np.max(np.abs(result.effective_xy_drive_GHz["q1_i"][lmax + 2:])) <= 1e-15
    assert np.max(np.abs(result.effective_xy_drive_GHz["q2_i"])) <= 4e-7


def test_z_crosstalk_inverse_uses_the_named_matrix_orientation():
    context = _context()
    plan = _plan(q1_flux=0.1)
    result = compile_qcis_waveform_plan(_admitted(plan, context), context)
    matrix = context.control_chain_config.static_mixing["z"]["matrix"]
    expected = np.linalg.solve(matrix, np.array([0.1, 0.0, 0.0]))
    offsets = {"q1_z": 16, "q2_z": 14, "c_z": 18}
    for lane, value in zip(("q1_z", "q2_z", "c_z"), expected, strict=True):
        assert result.requested_voltage_V[lane][offsets[lane]] == pytest.approx(value)
    assert result.effective_flux_delta_phi0["q1"][26] == pytest.approx(0.07, abs=3e-6)
    assert result.effective_flux_delta_phi0["q2"][26] == pytest.approx(0.0, abs=3e-6)
    assert result.effective_flux_delta_phi0["c"][26] == pytest.approx(0.0, abs=3e-6)


def test_half_lsb_ties_use_the_frozen_half_even_rule():
    config = _context().control_chain_config
    dac = {**config.dac, "lsb_V": Decimal("0.5")}
    codes, reconstructed = _quantize_half_even(np.array([0.25, 0.75, -0.25]), dac)
    assert codes.tolist() == [0, 2, 0]
    assert reconstructed.tolist() == pytest.approx([0.0, 1.0, 0.0])


def test_drive_event_frequency_relation_and_context_attacks_fail_closed():
    context = _context()
    plan = _plan()
    plan["drive_event_inventory"] = [_event()]
    plan["drive_event_inventory_sha256"] = sha256_json(plan["drive_event_inventory"])
    assert _admitted(plan, context).drive_event_inventory[0]["target"] == "Q1"
    plan["drive_event_inventory"][0]["detuning_GHz"] = 0.2
    with pytest.raises(ParameterizedControlError) as captured:
        _admitted(plan, context)
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID

    plan = _plan()
    event = _event()
    event["setting_evidence"] = []
    plan["drive_event_inventory"] = [event]
    plan["drive_event_inventory_sha256"] = sha256_json(plan["drive_event_inventory"])
    with pytest.raises(ParameterizedControlError) as captured:
        _admitted(plan, context)
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID

    plan = _plan()
    plan["authority_sha256"] = _hashes("C")
    with pytest.raises(ParameterizedControlError) as captured:
        _admitted(plan, context)
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_AUTHORITY_MISMATCH

    config = context.control_chain_config
    bad_z = dict(config.static_mixing["z"])
    bad_z["output_coordinates"] = tuple(reversed(bad_z["output_coordinates"]))
    with pytest.raises(ParameterizedControlError) as captured:
        build_parameterized_control_context(
            replace(config, static_mixing={**config.static_mixing, "z": bad_z}),
            context.channel_registry,
                context.device_flux_limits_phi0,
                device_limit_authority_sha256=context.device_limit_authority_sha256,
            repository_root=context.repository_root,
            output_root=context.output_root,
            authority_sha256=context.authority_sha256,
            expected_plan_authority_sha256=context.expected_plan_authority_sha256,
            stage4_compatibility_approved=True,
            compiler_source_snapshot=context.compiler_source_snapshot,
            environment_snapshot=context.environment_snapshot,
            publication_policy=context.publication_policy,
        )
    assert captured.value.code == ParameterizedControlReasonCode.NAMED_MAPPING_INVALID

    with pytest.raises(ParameterizedControlError) as captured:
        build_parameterized_control_context(
            config,
            context.channel_registry,
                context.device_flux_limits_phi0,
                device_limit_authority_sha256=context.device_limit_authority_sha256,
            repository_root=context.repository_root,
            output_root=context.output_root,
            authority_sha256={"qcis": "not-a-sha"},
            expected_plan_authority_sha256=context.expected_plan_authority_sha256,
            stage4_compatibility_approved=True,
            compiler_source_snapshot=context.compiler_source_snapshot,
            environment_snapshot=context.environment_snapshot,
            publication_policy=context.publication_policy,
        )
    assert captured.value.code == ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID

    altered_dac = {**config.dac, "lsb_V": config.dac["lsb_V"] * 2}
    with pytest.raises(ParameterizedControlError) as captured:
        build_parameterized_control_context(
            replace(config, dac=altered_dac), context.channel_registry, context.device_flux_limits_phi0,
            device_limit_authority_sha256=context.device_limit_authority_sha256,
            repository_root=context.repository_root, output_root=context.output_root,
            authority_sha256=context.authority_sha256,
            expected_plan_authority_sha256=context.expected_plan_authority_sha256,
            stage4_compatibility_approved=True,
            compiler_source_snapshot=context.compiler_source_snapshot,
            environment_snapshot=context.environment_snapshot,
            publication_policy=context.publication_policy,
        )
    assert captured.value.code == ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID


def test_qcis_compilation_adapter_rejects_structural_test_doubles():
    context = _context()
    q1 = np.asarray([1.0 + 2.0j, 3.0 + 4.0j], dtype="<c16")
    q2 = np.asarray([5.0 + 6.0j, 7.0 + 8.0j], dtype="<c16")
    source_plan = SimpleNamespace(
        schema_version="0.3",
        profile_id="qcis_stage7_calibration_v3",
        ast_sha256="2" * 64,
        trace_sha256="3" * 64,
        dt_ns=0.5,
        frame_reference_frequency_GHz={"q1": 5.0, "q2": 5.1},
        frame_reference_authority_sha256={"q1": "4" * 64, "q2": "5" * 64},
        drive_event_inventory=[],
        authority_sha256=_hashes("B"),
    )
    compilation = SimpleNamespace(
        plan=source_plan,
        concrete_source_sha256="1" * 64,
        q1_xy=q1,
        q2_xy=q2,
        q1_flux=np.zeros(2, dtype="<f8"),
        q2_flux=np.zeros(2, dtype="<f8"),
        c_flux=np.zeros(2, dtype="<f8"),
    )
    with pytest.raises(ParameterizedControlError) as captured:
        adapt_qcis_v03_compilation(compilation, "point_007", context)
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID


def test_legacy_smoke_compiler_replays_a_frozen_awg_code_oracle_without_stage41():
    root = Path.cwd()
    config = load_control_chain_config(root / "configs/control/2q1c2r_control_smoke.yaml")
    schedule = load_logical_schedule(root / "configs/control/2q1c2r_control_demo_smoke.yaml", dt_ns=config.dt_ns)
    legacy = compile_control_schedule(
        schedule,
        config,
        ControlBuildContext(
            root,
            {"stage3_1_readiness_valid": True, "stage4_0_channel_registry_ready": True, "design_and_config_provenance_valid": True, "artifact_provenance": {}},
            {},
            {"idle_convergence": {"max_frequency_drift_MHz": 0.1}},
            0.0,
        ),
    ).to_dict()
    codes = np.asarray(legacy["scenarios"][0]["awg"]["lanes"]["q1_xy_i"]["codes"], dtype="<i8")
    assert _sha(codes.view("<f8")) == "9D62052AEB3D882E2956D7AE5A0C73777444453D68A5DC91D3CF11ECA5CDF62B"


def test_v03_adapter_rejects_v02_hash_tampering_and_aggregate_device_bound_violation():
    with pytest.raises(ParameterizedControlError) as captured:
        compile_qcis_waveform_plan(_plan(), _context())
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID

    with pytest.raises(ParameterizedControlError) as captured:
        admit_qcis_v03_plan(_plan(schema_version="0.2"), _context())
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID

    plan = _plan()
    plan["array_inventory"]["logical.flux_delta_phi0.q1"]["sha256"] = "0" * 64
    with pytest.raises(ParameterizedControlError) as captured:
        admit_qcis_v03_plan(plan, _context())
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_HASH_MISMATCH

    with pytest.raises(ParameterizedControlError) as captured:
        context = _context()
        compile_qcis_waveform_plan(admit_qcis_v03_plan(_plan(q1_flux=0.45), context), context)
    assert captured.value.code == ParameterizedControlReasonCode.DEVICE_LIMIT_EXCEEDED


def test_compile_readmits_typed_plans_and_revalidates_nested_context_state():
    context = _context()
    admitted = _admitted(_plan(), context)
    forged = replace(admitted, flux_q1=np.full(admitted.sample_count, 0.1, dtype="<f8"))
    with pytest.raises(ParameterizedControlError) as captured:
        compile_qcis_waveform_plan(forged, context)
    assert captured.value.code == ParameterizedControlReasonCode.PLAN_HASH_MISMATCH

    xy = dict(context.control_chain_config.static_mixing["xy"])
    xy["matrix"] = np.asarray(xy["matrix"], dtype="<f8").copy()
    xy["matrix"][0, 0] *= 10.0
    config = replace(context.control_chain_config, static_mixing={**context.control_chain_config.static_mixing, "xy": xy})
    mutated = replace(context, control_chain_config=config)
    with pytest.raises(ParameterizedControlError) as captured:
        compile_qcis_waveform_plan(admitted, mutated)
    assert captured.value.code == ParameterizedControlReasonCode.MIXING_MATRIX_INVALID


def test_v03_dac_overflow_rejects_without_clipping():
    with pytest.raises(ParameterizedControlError) as captured:
        context = _context(bounds=(-2.0, 2.0))
        compile_qcis_waveform_plan(admit_qcis_v03_plan(_plan(q1_flux=1.0), context), context)
    assert captured.value.code == ParameterizedControlReasonCode.DAC_RANGE_EXCEEDED


def test_legacy_stage4_entry_remains_a_distinct_unchanged_api():
    assert tuple(inspect.signature(compile_control_schedule).parameters) == ("schedule", "config", "context")
    assert compile_control_schedule.__module__ == "sqvm.control.stage4_compile"
