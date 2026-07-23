from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import pytest

from sqvm.qcis import compile_qcis
from sqvm.qcis.canonical import sha256_json
from sqvm.runtime.registry import get_builtin_backend_registry
from sqvm.runtime.stage71 import (
    BOUNDED_ENVELOPE,
    CAPABILITY,
    CLAIM_ENVELOPE,
    Stage71EntranceError,
    Stage71FailureCode,
    _admit_entrance_authority,
    _admit_compilation,
    _evidence_payload,
    bounded_envelope,
    capability_descriptor,
    run_bounded_model_point,
)


ROOT = Path(__file__).resolve().parents[1]


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
    record.update(
        {
            "setting_id": setting_id,
            "target": target,
            "revision": 1,
            "calibration_run_id": f"{setting_id}_run",
            "status": "accepted",
        }
    )
    record["setting_hash"] = sha256_json(record)
    return record


def _authorities(source: str) -> dict:
    xy = {"wave_index": 0, "length_samples": 4, "width_samples": 4, "amplitude_GHz": 1.0}
    result = {
        "instruction_profile": {"profile_id": "qcis_stage7_calibration_v3", "profile_version": "0.3"},
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
            }
        },
        "clock": {"dt_ns": 0.5, "sample_rate_Hz": 2_000_000_000.0},
        "compiler": {"compiler_id": "sqvm_qcis_compiler_v3"},
        "templates": {"case": {"source": source, "bindings": {}}},
    }
    result["expected_sha256"] = {
        name: sha256_json(result[name])
        for name in ("instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")
    }
    return result


def _compile(source: str):
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
        _authorities(source),
        idle_flux={"q1": 0.1, "q2": 0.0, "c": 0.27},
    )


def _authority() -> dict:
    return {"capability": dict(CAPABILITY), "bounded_envelope": dict(BOUNDED_ENVELOPE)}


def _copy_entrance_authority(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    for relative in (
        "configs/runtime/stage71/entrance_authority_v1.json",
        "configs/runtime/stage71/entrance_approval_v1.json",
        "docs/designs/07_1_model_backend_entrance_design.md",
        "src/sqvm/runtime/stage71.py",
        "configs/control/stage41/production_approval_v1.json",
        "configs/evolution/stage51/physics_approval_v1.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return root


def test_capability_is_immutable_bounded_and_not_registered_in_stage6():
    assert capability_descriptor() is CAPABILITY
    assert bounded_envelope() is BOUNDED_ENVELOPE
    assert CAPABILITY["qualification_scope"] == "bounded_smoke_only"
    assert CAPABILITY["stage6_registered"] is False
    assert CAPABILITY["recommendation_eligible"] is False
    assert get_builtin_backend_registry().ids() == ("deterministic_fake_v1",)
    with pytest.raises(TypeError):
        CAPABILITY["stage6_registered"] = True


def test_entrance_authority_admits_exact_bytes_and_rejects_bound_drift(tmp_path: Path):
    root = _copy_entrance_authority(tmp_path)
    authority, binding = _admit_entrance_authority(root)
    assert authority["authority_id"] == binding["entrance_authority_id"]

    source = root / "src/sqvm/runtime/stage71.py"
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(Stage71EntranceError) as captured:
        _admit_entrance_authority(root)
    assert captured.value.code is Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID


@pytest.mark.parametrize(
    "relative",
    (
        "configs/runtime/stage71/entrance_authority_v1.json",
        "configs/runtime/stage71/entrance_approval_v1.json",
    ),
)
def test_entrance_authority_rejects_linked_root_documents(tmp_path: Path, relative: str):
    root = _copy_entrance_authority(tmp_path)
    path = root / relative
    backup = path.with_suffix(".original.json")
    path.replace(backup)
    try:
        path.symlink_to(backup.name)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(Stage71EntranceError) as captured:
        _admit_entrance_authority(root)
    assert captured.value.code is Stage71FailureCode.ENTRANCE_AUTHORITY_INVALID


def test_bounded_admission_accepts_one_short_compilation_and_rejects_limits():
    short = _compile("PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
    _admit_compilation(short, "point_1", 1.0, _authority())

    long = _compile("I Q1 65\n")
    with pytest.raises(Stage71EntranceError) as captured:
        _admit_compilation(long, "point_1", 1.0, _authority())
    assert captured.value.code is Stage71FailureCode.BOUNDED_ENVELOPE_EXCEEDED

    with pytest.raises(Stage71EntranceError) as captured:
        _admit_compilation(short, "point_1", 180.0001, _authority())
    assert captured.value.code is Stage71FailureCode.BOUNDED_ENVELOPE_EXCEEDED


def test_tampered_compilation_fails_before_bounded_execution():
    compilation = _compile("PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
    tampered = replace(compilation, concrete_source=compilation.concrete_source + "I Q1 1\n")
    with pytest.raises(Stage71EntranceError) as captured:
        _admit_compilation(tampered, "point_1", 1.0, _authority())
    assert captured.value.code is Stage71FailureCode.QCIS_COMPILATION_INVALID


def test_evidence_contains_only_refs_and_evidence_quality_not_physics_arrays():
    compilation = _compile("PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
    control = SimpleNamespace(control_id="control", manifest_sha256="A" * 64, receipt_sha256="B" * 64)
    coefficients = SimpleNamespace(
        coefficient_plan_id="coefficient",
        manifest_sha256="C" * 64,
        receipt_sha256="D" * 64,
        physics_authority_id="E" * 64,
    )
    evolution = SimpleNamespace(result_id="evolution", manifest_sha256="F" * 64, receipt_sha256="0" * 64, replay_fidelity=1.0)
    payload = _evidence_payload(
        compilation,
        "point_1",
        control,
        coefficients,
        evolution,
        {
            "entrance_authority_id": "1" * 64,
            "entrance_authority_sha256": "2" * 64,
            "stage4_1_approval_sha256": "3" * 64,
            "stage5_1_approval_sha256": "4" * 64,
        },
    )
    assert payload["claim_envelope"] == dict(CLAIM_ENVELOPE)
    assert payload["parent_calibration_binding"] is None
    assert payload["evolution_binding"]["replay_fidelity"] == 1.0
    assert payload["capability"]["recommendation_eligible"] is False
    serialized = repr(payload).lower()
    for forbidden in ("population", "leakage", "final_state", "projector", "solver_diagnostics"):
        assert forbidden not in serialized


def test_real_qcis_stage41_stage51_bounded_entrance_and_public_replay():
    workspace = ROOT / "output" / f".stage71-e2e.{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    try:
        compilation = _compile("PLSXY Q1 0 -1 2 0.001 5.1 0 0 2\n")
        result = run_bounded_model_point(compilation, "point_1", workspace, ROOT, timeout_s=180.0)
        assert result.qualification_scope == "bounded_smoke_only"
        assert result.replay_fidelity == pytest.approx(1.0, abs=1.0e-9)
        assert (result.artifact_root / "receipt.json").is_file()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
