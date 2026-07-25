from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.contract

import math
from pathlib import Path

import numpy as np
import pytest

from sqvm.control import (
    adapt_qcis_v03_compilation,
    build_parameterized_control_context,
    compile_qcis_waveform_plan,
    load_control_chain_config,
    load_control_channel_registry,
)
from sqvm.calibration.rabi_phase import (
    RabiElectronicsScheduleProof,
    RabiPhaseAuditError,
    audit_two_x2p_phase,
    rotating_to_lab_waveform,
    wrap_phase_rad,
)
from tests.test_stage7_qcis_v3 import _authorities, _refresh_expected, _refresh_setting, _sha
from sqvm.qcis import compile_qcis


def _event(start: int, *, count: int = 3, phase: float = 0.25, drive: float = 5.0, reference: float = 5.0) -> dict:
    return {
        "source_instruction_index": 1 if start == 0 else 2,
        "transition": "01",
        "phase_rule_id": "qcis_v03_absolute_detuning_phase_v1",
        "actual_start_sample": start,
        "sample_count": count,
        "phase_total_rad": phase,
        "f_drive_GHz": drive,
        "f_ref_GHz": reference,
        "detuning_GHz": drive - reference,
        "setting_evidence": {"setting_id": "q1_xy2", "setting_hash": "A" * 64},
    }


def _proof(*, applications: int = 1) -> RabiElectronicsScheduleProof:
    return RabiElectronicsScheduleProof(applications, 6, 10, -1.25, 0.5)


def _source_ops(events: list[dict]) -> dict[int, str]:
    return {event["source_instruction_index"]: "X2P" for event in events}


def test_integer_carrier_cycles_are_valid_even_when_wrapped_lab_advance_is_zero():
    events = [_event(0, count=2), _event(2, count=2)]
    audit = audit_two_x2p_phase(events, amplitude_GHz=0.1, dt_ns=0.5, logical_sample_count=6, electronics_schedule=_proof(), source_operations=_source_ops(events))
    assert audit.lab_phase_advance_unwrapped_rad == pytest.approx(10.0 * math.pi)
    assert audit.lab_phase_advance_wrapped_rad == pytest.approx(0.0)
    assert audit.absolute_start_time_ns == pytest.approx((0.25, 1.25))
    assert audit.to_dict()["electronics_schedule"]["application_count"] == 1


@pytest.mark.parametrize("detuning", (-0.2, 0.2))
def test_noninteger_carrier_cycles_and_signed_detuning_use_absolute_sample_centers(detuning: float):
    events = [_event(0, drive=5.0 + detuning), _event(3, drive=5.0 + detuning)]
    audit = audit_two_x2p_phase(events, amplitude_GHz=0.1, dt_ns=0.5, logical_sample_count=6, electronics_schedule=_proof(), source_operations=_source_ops(events))
    assert audit.rotating_first_sample_phase_rad[1] == pytest.approx(wrap_phase_rad(0.25 + 2.0 * math.pi * detuning * 1.75))
    assert audit.lab_phase_advance_unwrapped_rad == pytest.approx(2.0 * math.pi * (5.0 + detuning) * 1.5)
    assert abs(audit.lab_phase_advance_wrapped_rad) > 0.1


def test_local_phase_reset_is_rejected_even_when_interval_lengths_match():
    first, reset_second = _event(0, drive=5.2), _event(3, drive=5.2)
    reset_second["phase_total_rad"] = 0.25 - 2.0 * math.pi * 0.2 * 1.5
    with pytest.raises(RabiPhaseAuditError, match="rotating phase differ"):
        audit_two_x2p_phase([first, reset_second], amplitude_GHz=0.1, dt_ns=0.5, logical_sample_count=6, electronics_schedule=_proof(), source_operations=_source_ops([first, reset_second]))


def test_setting_or_electronics_split_processing_is_rejected():
    first, second = _event(0), _event(3)
    second["setting_evidence"] = {"setting_id": "q1_xy2", "setting_hash": "B" * 64}
    with pytest.raises(RabiPhaseAuditError, match="setting evidence differs"):
        audit_two_x2p_phase([first, second], amplitude_GHz=0.1, dt_ns=0.5, logical_sample_count=6, electronics_schedule=_proof(), source_operations=_source_ops([first, second]))
    with pytest.raises(RabiPhaseAuditError, match="exactly once"):
        events = [_event(0), _event(3)]
        audit_two_x2p_phase(events, amplitude_GHz=0.1, dt_ns=0.5, logical_sample_count=6, electronics_schedule=_proof(applications=2), source_operations=_source_ops(events))


def test_rotating_to_lab_is_derived_only_and_qutip_evolves_rotating_iq():
    qutip = pytest.importorskip("qutip")
    dt_ns = 0.01
    time = (np.arange(50, dtype=float) + 0.5) * dt_ns
    rotating_iq = np.ones(time.size, dtype=np.complex128)
    lab_preview = rotating_to_lab_waveform(rotating_iq, time_center_ns=time, f_ref_GHz=5.0)
    assert lab_preview.dtype == np.dtype(float)
    assert not np.allclose(lab_preview, rotating_iq.real)

    sigma_plus = qutip.basis(2, 1) * qutip.basis(2, 0).dag()
    hamiltonian = 2.0 * math.pi * 0.5 * (sigma_plus + sigma_plus.dag())
    result = qutip.sesolve(hamiltonian, qutip.basis(2, 0), np.linspace(0.0, 0.5, 51))
    assert abs(result.states[-1][1, 0]) ** 2 == pytest.approx(1.0, abs=1e-8)


def test_real_x2p_compilation_and_full_stage41_schedule_prove_one_global_electronics_pass(tmp_path):
    source = "X2P Q1\nX2P Q1\n"
    authorities = _authorities(source)
    setting = authorities["waveform_registry"]["settings"]["q1_xy2"]
    setting.update({"length_samples": 3, "width_samples": 3, "amplitude_GHz": 0.001})
    _refresh_setting(setting)
    _refresh_expected(authorities)
    qcis = compile_qcis(
        {
            "program_schema_version": "0.3",
            "instruction_set_id": "qcis_stage7_calibration_v3",
            "template_id": "case",
            "template_sha256": _sha(source),
            "source_format": "qcis_template",
            "source": source,
            "bindings": {},
        },
        authorities,
        idle_flux={"q1": 0.1, "q2": 0.0, "c": 0.27},
    )
    config = load_control_chain_config("configs/control/2q1c2r_control_smoke.yaml")
    context = build_parameterized_control_context(
        config,
        load_control_channel_registry("configs/control/2q1c2r_channels.yaml"),
        {name: (-0.5, 0.5) for name in ("q1", "q2", "c")},
        device_limit_authority_sha256="D" * 64,
        repository_root=Path.cwd().resolve(),
        output_root=tmp_path,
        authority_sha256={"stage4_1": "A" * 64},
        expected_plan_authority_sha256=qcis.plan.authority_sha256,
        stage4_compatibility_approved=True,
        compiler_source_snapshot={},
        environment_snapshot={},
        publication_policy={"mode": "disabled"},
    )
    plan = adapt_qcis_v03_compilation(qcis, "rabi_x2p", context)
    effective = compile_qcis_waveform_plan(plan, context)
    audit = audit_two_x2p_phase(
        qcis.plan.drive_event_inventory,
        amplitude_GHz=0.001,
        dt_ns=qcis.plan.dt_ns,
        logical_sample_count=qcis.q1_xy.size,
        electronics_schedule=RabiElectronicsScheduleProof(
            application_count=1,
            logical_sample_count=qcis.q1_xy.size,
            effective_sample_count=effective.effective_time_center_ns.size,
            effective_first_center_ns=float(effective.effective_time_center_ns[0]),
            effective_dt_ns=config.dt_ns,
        ),
        source_operations={step["index"]: step["op"] for step in qcis.plan.trace["steps"]},
    )
    assert audit.first_interval.end_sample == audit.second_interval.start_sample
    assert audit.rotating_first_sample_phase_rad == pytest.approx((0.0, 0.0))
    assert audit.lab_phase_advance_wrapped_rad == pytest.approx(math.pi)
    assert effective.metrics["logical_sample_count"] == qcis.q1_xy.size
    assert effective.metrics["effective_sample_count"] > qcis.q1_xy.size
