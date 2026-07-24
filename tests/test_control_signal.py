from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import time
import uuid

import nbformat as nbf
import numpy as np
import pytest
import yaml

from sqvm.control import (
    ControlBuildContext,
    ControlCompilationResult,
    build_stage4_acceptance_approval,
    compile_control_schedule,
    load_control_chain_config,
    load_control_channel_registry,
    load_logical_schedule,
    validate_logical_schedule,
    validate_stage4_acceptance_approval,
    verify_control_signal,
    write_control_signal_artifacts,
)
from sqvm.control.stage4_compile import FORMAL_CHECKS, FORMAL_SCENARIOS, SMOKE_CHECKS, _quantize, _shape
from sqvm.control.stage4_models import LogicalPulse
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256


ROOT = Path(__file__).resolve().parents[1]
FORMAL_CONFIG = ROOT / "configs/control/2q1c2r_control.yaml"
SMOKE_CONFIG = ROOT / "configs/control/2q1c2r_control_smoke.yaml"
FORMAL_SCHEDULE = ROOT / "configs/control/2q1c2r_control_demo.yaml"
SMOKE_SCHEDULE = ROOT / "configs/control/2q1c2r_control_demo_smoke.yaml"
REVIEW = ROOT / "docs/decisions/2026-07-12-stage4-d1-acceptance-remediation.md"


@pytest.fixture
def stage4_tmp():
    path = ROOT / "tmp" / f"stage4_control_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def formal_candidate():
    path = ROOT / "tmp" / f"stage4_formal_candidate_{uuid.uuid4().hex}"
    verify_control_signal(FORMAL_CONFIG, FORMAL_SCHEDULE, path)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _builder(directory, review=REVIEW):
    return build_stage4_acceptance_approval(
        artifact_path=directory / "control_signal_artifacts.json",
        notebook_path=directory / "verification.ipynb",
        report_path=directory / "verification_report.json",
        run_receipt_path=directory / "run_receipt.json",
        review_record_path=review,
        repository_root=ROOT,
    )


def _rewrite_candidate(directory, artifact):
    artifact_path = directory / "control_signal_artifacts.json"
    report_path = directory / "verification_report.json"
    receipt_path = directory / "run_receipt.json"
    artifact_path.write_bytes(canonical_json_bytes(artifact))
    report = json.loads(report_path.read_bytes())
    report["artifact_sha256"] = raw_file_sha256(artifact_path)
    report["computational_checks"] = artifact["computational_gate"]["checks"]
    report["computational_ready"] = artifact["computational_gate"]["computational_ready"]
    report["blocking_reasons"] = artifact["computational_gate"]["blocking_reasons"]
    report_path.write_bytes(canonical_json_bytes(report))
    receipt = json.loads(receipt_path.read_bytes())
    receipt["sha256"]["artifact_sha256"] = raw_file_sha256(artifact_path)
    receipt["sha256"]["verification_report_sha256"] = raw_file_sha256(report_path)
    receipt_path.write_bytes(canonical_json_bytes(receipt))


def _copy_candidate(source, target):
    shutil.copytree(source, target)
    report_path = target / "verification_report.json"
    receipt_path = target / "run_receipt.json"
    report = json.loads(report_path.read_bytes())
    report["artifact_path"] = (target / "control_signal_artifacts.json").relative_to(ROOT).as_posix()
    report["notebook_path"] = (target / "verification.ipynb").relative_to(ROOT).as_posix()
    report_path.write_bytes(canonical_json_bytes(report))
    receipt = json.loads(receipt_path.read_bytes())
    receipt["paths"] = {
        "artifact": (target / "control_signal_artifacts.json").relative_to(ROOT).as_posix(),
        "notebook": (target / "verification.ipynb").relative_to(ROOT).as_posix(),
        "verification_report": report_path.relative_to(ROOT).as_posix(),
    }
    receipt["sha256"]["verification_report_sha256"] = raw_file_sha256(report_path)
    receipt_path.write_bytes(canonical_json_bytes(receipt))


def _write_approval_for_current_bytes(directory, payload):
    payload = deepcopy(payload)
    payload["control_signal_artifact_sha256"] = raw_file_sha256(directory / "control_signal_artifacts.json")
    payload["verification_notebook_sha256"] = raw_file_sha256(directory / "verification.ipynb")
    payload["verification_report_sha256"] = raw_file_sha256(directory / "verification_report.json")
    payload["run_receipt_sha256"] = raw_file_sha256(directory / "run_receipt.json")
    (directory / "acceptance_approval.json").write_bytes(canonical_json_bytes(payload))


def _context(config, schedule):
    registry = load_control_channel_registry(ROOT / "configs/control/2q1c2r_channels.yaml")
    stage3 = json.loads((ROOT / "output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json").read_bytes())
    registry_payload = {
        "channel_order": [row.name for row in registry.channels],
        "channels": {row.name: {**row.to_dict(), "origin": "stage4_extension" if row.name in {"q1_z", "q2_z"} else "stage1_base"} for row in registry.channels},
    }
    provenance = {
        "paths": {
            "stage4_design_freeze_manifest": "docs/decisions/2026-07-11-stage4-design-freeze.json",
            "channel_registry": "configs/control/2q1c2r_channels.yaml",
            "channel_registry_approval": "output/stage_04_0_control_channel_rebaseline/control_channel_approval.json",
            "control_config": config.source_path.relative_to(ROOT).as_posix(),
            "logical_schedule": schedule.source_path.relative_to(ROOT).as_posix(),
            "stage3_1_artifact": "output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json",
            "stage3_1_approval": "output/stage_03_1_q1_q2_coupling/acceptance_approval.json",
        },
        "sha256": {},
    }
    return ControlBuildContext(ROOT, {"stage3_1_readiness_valid": True, "stage4_0_channel_registry_ready": True, "design_and_config_provenance_valid": True, "artifact_provenance": provenance}, registry_payload, stage3, time.perf_counter())


def _compile(profile="formal"):
    config_path, schedule_path = (FORMAL_CONFIG, FORMAL_SCHEDULE) if profile == "formal" else (SMOKE_CONFIG, SMOKE_SCHEDULE)
    config = load_control_chain_config(config_path)
    schedule = load_logical_schedule(schedule_path, dt_ns=config.dt_ns)
    return config, schedule, compile_control_schedule(schedule, config, _context(config, schedule))


def test_exact_control_profiles_and_frozen_electronics():
    formal = load_control_chain_config(FORMAL_CONFIG)
    smoke = load_control_chain_config(SMOKE_CONFIG)
    assert formal.profile == "formal" and smoke.profile == "smoke"
    assert formal.sample_rate_Hz == 2_000_000_000 and formal.dt_ns == Decimal("0.5")
    assert formal.dac["lsb_V"] == Decimal("0.66") / Decimal(65536)
    assert formal.lane_order == smoke.lane_order and len(formal.lane_order) == 11
    assert formal.static_mixing["xy"]["condition_number_2"] == pytest.approx(1.0495417347511131)
    assert formal.static_mixing["z"]["condition_number_2"] == pytest.approx(1.0528243806381707)
    assert formal.static_mixing["readout"]["condition_number_2"] == pytest.approx(1.0199980198039404)


@pytest.mark.parametrize("attack", ["extra", "bool_rate", "bad_fir", "singular"])
def test_control_config_rejects_schema_numeric_and_electronics_attacks(stage4_tmp, attack):
    payload = yaml.safe_load(FORMAL_CONFIG.read_text(encoding="utf-8"))
    if attack == "extra": payload["extra"] = True
    elif attack == "bool_rate": payload["clock"]["sample_rate_Hz"] = True
    elif attack == "bad_fir": payload["lanes"]["q1_z"]["fir"] = [0.5, 0.4]
    else: payload["static_mixing"]["z"]["matrix"] = [[1, 0, 0], [1, 0, 0], [0, 0, 1]]
    path = stage4_tmp / "config.yaml"; path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError): load_control_chain_config(path)


def test_formal_and_smoke_schedule_exact_subset_and_validation():
    formal = load_control_chain_config(FORMAL_CONFIG)
    schedule = load_logical_schedule(FORMAL_SCHEDULE, dt_ns=formal.dt_ns)
    assert tuple(row.scenario_id for row in schedule.scenarios) == FORMAL_SCENARIOS
    assert validate_logical_schedule(schedule, load_control_channel_registry(ROOT / "configs/control/2q1c2r_channels.yaml"), formal).ok
    smoke = load_control_chain_config(SMOKE_CONFIG)
    smoke_schedule = load_logical_schedule(SMOKE_SCHEDULE, dt_ns=smoke.dt_ns)
    assert tuple(row.scenario_id for row in smoke_schedule.scenarios) == FORMAL_SCENARIOS[:2]


def test_schedule_conflict_and_off_grid_fail_closed(stage4_tmp):
    payload = yaml.safe_load(FORMAL_SCHEDULE.read_text(encoding="utf-8"))
    payload["scenarios"][0]["pulses"][1]["channel"] = "q1_xy"
    path = stage4_tmp / "conflict.yaml"; path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    config = load_control_chain_config(FORMAL_CONFIG)
    schedule = load_logical_schedule(path, dt_ns=config.dt_ns)
    report = validate_logical_schedule(schedule, load_control_channel_registry(ROOT / "configs/control/2q1c2r_channels.yaml"), config)
    assert not report.ok and report.conflicts
    payload["scenarios"][0]["pulses"][0]["start_ns"] = 20.1
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="sample grid"): load_logical_schedule(path, dt_ns=config.dt_ns)


def test_corrected_gaussian_drag_and_flattop_vectors():
    pulse = LogicalPulse("p", "xy", "q1_xy", Decimal(0), Decimal(32), {"shape": "drag_gaussian", "sigma_ns": 8.0, "drag_beta_ns": -0.5})
    envelope, derivative = _shape(pulse, 64, 0.5)
    assert envelope[0] == pytest.approx(0.0) and envelope[-1] == pytest.approx(0.0)
    assert np.allclose(envelope, envelope[::-1]) and np.allclose(derivative, -derivative[::-1])
    assert abs(np.sum(derivative) * 0.5) <= 1e-12
    z = LogicalPulse("z", "z", "q1_z", Decimal(0), Decimal(20), {"shape": "flattop_cos", "rise_ns": 4.0})
    flat, _ = _shape(z, 40, 0.5)
    assert flat[0] > 0 and flat[8] == 1 and flat[-1] > 0


def test_decimal_from_float_quantization_and_range():
    config = load_control_chain_config(FORMAL_CONFIG)
    values = np.array([0.0, float(config.dac["lsb_V"]), -float(config.dac["lsb_V"]), 0.32998])
    codes, reconstructed = _quantize(values, config.dac)
    assert codes.tolist()[:3] == [0, 1, -1]
    assert np.max(np.abs(values - reconstructed)) <= 0.5 * float(config.dac["lsb_V"]) + 1e-15
    with pytest.raises(ValueError, match="out of range"): _quantize(np.array([0.34]), config.dac)


@pytest.mark.parametrize("profile,expected_checks,expected_scenarios", [("formal", FORMAL_CHECKS, 6), ("smoke", SMOKE_CHECKS, 2)])
def test_compilation_pipeline_metrics_and_profile_gate(profile, expected_checks, expected_scenarios):
    config, schedule, result = _compile(profile)
    payload = result.to_dict()
    assert payload["computational_gate"]["computational_ready"]
    assert [row["name"] for row in payload["computational_gate"]["checks"]] == list(expected_checks)
    assert len(payload["scenarios"]) == expected_scenarios
    assert all(row["effective_sample_count"] <= row["desired_sample_count"] + 191 for row in payload["scenarios"])
    assert payload["global_metrics"]["xy_area_error_within_budget"]
    assert payload["global_metrics"]["z_target_error_within_budget"]
    assert payload["phase_proxy"]["phase_proxy_rad"] == pytest.approx(0.01957651736909815)
    if profile == "formal":
        assert len(payload["global_metrics"]["area_rows"]) == 4 and payload["global_metrics"]["readout_area_error_within_budget"]
    else:
        assert len(payload["global_metrics"]["area_rows"]) == 2 and not payload["global_metrics"]["readout_area_error_within_budget"] and not result.acceptance_eligible


def test_writer_requires_result_and_nonfinite_leaves_no_partial(stage4_tmp):
    with pytest.raises(TypeError): write_control_signal_artifacts({}, stage4_tmp / "bad")
    _, _, result = _compile("smoke")
    payload = deepcopy(result.to_dict()); payload["phase_proxy"]["phase_proxy_rad"] = float("inf")
    attacked = ControlCompilationResult("smoke", False, "smoke_complete", payload)
    target = stage4_tmp / "nonfinite"
    with pytest.raises(ValueError, match="non-finite"): write_control_signal_artifacts(attacked, target)
    assert not target.exists() and not list(stage4_tmp.glob(".nonfinite.staging.*"))


def test_smoke_transaction_exact_four_and_real_notebook(stage4_tmp):
    target = stage4_tmp / "smoke"
    receipt = verify_control_signal(SMOKE_CONFIG, SMOKE_SCHEDULE, target)
    assert receipt.execution_succeeded and not receipt.acceptance_candidate_ready
    assert {row.name for row in target.iterdir()} == {"control_signal_artifacts.json", "verification.ipynb", "verification_report.json", "run_receipt.json"}
    notebook = nbf.read(target / "verification.ipynb", as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    assert [cell.execution_count for cell in code] == list(range(1, len(code) + 1))
    assert not [output for cell in code for output in cell.outputs if output.output_type == "error"]


def test_formal_temp_candidate_positive_approval_and_smoke_attack(stage4_tmp, formal_candidate):
    target = stage4_tmp / "formal"
    _copy_candidate(formal_candidate, target)
    approval = _builder(target)
    assert set(approval) == {
        "schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role", "blocking_findings",
        "stage4_design_freeze_manifest_sha256", "control_channel_approval_sha256", "control_config_sha256",
        "logical_schedule_sha256", "stage4_source_tree_sha256", "stage3_1_acceptance_approval_sha256",
        "control_signal_artifact_sha256", "verification_notebook_sha256", "verification_report_sha256",
        "run_receipt_sha256", "review_record_sha256", "review_record_path",
    }
    (target / "acceptance_approval.json").write_bytes(canonical_json_bytes(approval))
    ready = validate_stage4_acceptance_approval(target / "acceptance_approval.json", target / "control_signal_artifacts.json", target / "verification.ipynb", target / "verification_report.json", target / "run_receipt.json", repository_root=ROOT)
    assert ready.ok and ready.stage5_ready and ready.acceptance_approval_valid

    smoke = stage4_tmp / "smoke_attack"
    verify_control_signal(SMOKE_CONFIG, SMOKE_SCHEDULE, smoke)
    with pytest.raises(ValueError, match="formal acceptance candidate"):
        _builder(smoke)
    shutil.copyfile(target / "acceptance_approval.json", smoke / "acceptance_approval.json")
    rejected = validate_stage4_acceptance_approval(smoke / "acceptance_approval.json", smoke / "control_signal_artifacts.json", smoke / "verification.ipynb", smoke / "verification_report.json", smoke / "run_receipt.json", repository_root=ROOT)
    assert not rejected.ok and not rejected.stage5_ready


@pytest.mark.parametrize("attack", ["missing", "extra", "existing_approval", "stale_source", "stale_config", "stale_schedule", "nonready_receipt"])
def test_builder_rejects_nonexact_stale_and_nonready_candidates(stage4_tmp, formal_candidate, attack):
    target = stage4_tmp / attack
    _copy_candidate(formal_candidate, target)
    if attack == "missing":
        (target / "run_receipt.json").unlink()
    elif attack == "extra":
        (target / "extra.txt").write_text("x", encoding="ascii")
    elif attack == "existing_approval":
        (target / "acceptance_approval.json").write_text("{}", encoding="ascii")
    elif attack in {"stale_source", "stale_config", "stale_schedule"}:
        artifact = json.loads((target / "control_signal_artifacts.json").read_bytes())
        key = {"stale_source": "stage4_source_tree_sha256", "stale_config": "control_config_sha256", "stale_schedule": "logical_schedule_sha256"}[attack]
        artifact["provenance"]["sha256"][key] = "0" * 64
        _rewrite_candidate(target, artifact)
    else:
        receipt = json.loads((target / "run_receipt.json").read_bytes())
        receipt["acceptance_candidate_ready"] = False
        receipt["blocking_reasons"] = ["not ready"]
        (target / "run_receipt.json").write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ValueError):
        _builder(target)


def test_builder_rejects_missing_and_outside_review(stage4_tmp, formal_candidate):
    target = stage4_tmp / "review"
    _copy_candidate(formal_candidate, target)
    with pytest.raises(ValueError, match="non-empty"):
        _builder(target, stage4_tmp / "missing.md")
    outside = Path(os.environ["TEMP"]) / f"stage4-review-{uuid.uuid4().hex}.md"
    outside.write_text("review", encoding="ascii")
    try:
        with pytest.raises(ValueError, match="inside repository"):
            _builder(target, outside)
    finally:
        outside.unlink(missing_ok=True)


def test_dac_code_tamper_rejected_by_builder_and_validator(stage4_tmp, formal_candidate):
    target = stage4_tmp / "dac"
    _copy_candidate(formal_candidate, target)
    approval = _builder(target)
    artifact = json.loads((target / "control_signal_artifacts.json").read_bytes())
    codes = artifact["scenarios"][0]["awg"]["lanes"]["q1_xy_i"]["codes"]
    index = next(index for index, code in enumerate(codes) if -32768 < code < 32767)
    codes[index] += 1
    _rewrite_candidate(target, artifact)
    with pytest.raises(ValueError, match="DAC reference"):
        _builder(target)
    _write_approval_for_current_bytes(target, approval)
    report = validate_stage4_acceptance_approval(target / "acceptance_approval.json", target / "control_signal_artifacts.json", target / "verification.ipynb", target / "verification_report.json", target / "run_receipt.json", repository_root=ROOT)
    assert not report.ok and any("DAC reference" in reason for reason in report.blocking_reasons)


@pytest.mark.parametrize("group,lane,scenario_index", [("xy", "q1_xy_i", 0), ("z", "c_z", 2)])
def test_synchronized_delivered_and_effective_attack_rejected(stage4_tmp, formal_candidate, group, lane, scenario_index):
    target = stage4_tmp / f"delivered_{group}"
    _copy_candidate(formal_candidate, target)
    approval = _builder(target)
    artifact = json.loads((target / "control_signal_artifacts.json").read_bytes())
    scenario = artifact["scenarios"][scenario_index]
    sample = 100
    scenario["awg"]["lanes"][lane]["delivered_after_fir_latency_V"][sample] += 1e-9
    spec = artifact["control_config"]["static_mixing"][group]
    lane_index = spec["input_lanes"].index(lane)
    delta = np.asarray(spec["matrix"], dtype=float)[:, lane_index] * 1e-9
    if group == "xy":
        coordinates = (("q1", "i"), ("q1", "q"), ("q2", "i"), ("q2", "q"))
        for value, (mode, quadrature) in zip(delta, coordinates, strict=True):
            scenario["effective"]["xy_drive_GHz"][mode][quadrature][sample] += float(value)
    else:
        for value, mode in zip(delta, ("q1", "q2", "c"), strict=True):
            scenario["effective"]["absolute_flux_phi0"][mode][sample] += float(value)
    _rewrite_candidate(target, artifact)
    with pytest.raises(ValueError, match="delivered signal mismatch"):
        _builder(target)
    _write_approval_for_current_bytes(target, approval)
    report = validate_stage4_acceptance_approval(target / "acceptance_approval.json", target / "control_signal_artifacts.json", target / "verification.ipynb", target / "verification_report.json", target / "run_receipt.json", repository_root=ROOT)
    assert not report.ok and any("delivered signal mismatch" in reason for reason in report.blocking_reasons)


def test_nonzero_forward_error_is_recomputed_and_check_message_is_exact(stage4_tmp, formal_candidate):
    target = stage4_tmp / "forward"
    _copy_candidate(formal_candidate, target)
    artifact = json.loads((target / "control_signal_artifacts.json").read_bytes())
    scenario = artifact["scenarios"][2]
    scenario["effective"]["absolute_flux_phi0"]["q1"][0] += 5e-13
    row = next(item for item in scenario["metrics"]["forward_reference_rows"] if item["coordinate"] == "q1_delta_flux_phi0")
    row["max_abs_error"] = abs(scenario["effective"]["absolute_flux_phi0"]["q1"][0] - artifact["control_config"]["idle_flux_phi0"]["q1"])
    row["passed"] = row["max_abs_error"] <= row["threshold"]
    _rewrite_candidate(target, artifact)
    approval = _builder(target)
    assert approval["control_signal_artifact_sha256"] == raw_file_sha256(target / "control_signal_artifacts.json")
    artifact["scenarios"][2]["checks"][2]["message"] = "attacker"
    _rewrite_candidate(target, artifact)
    with pytest.raises(ValueError, match="scenario checks"):
        _builder(target)


@pytest.mark.parametrize("attack", ["delivered_short", "delivered_reordered", "effective_short", "nonfinite"])
def test_signal_shape_order_and_finite_attacks_fail_closed(stage4_tmp, formal_candidate, attack):
    target = stage4_tmp / attack
    _copy_candidate(formal_candidate, target)
    artifact = json.loads((target / "control_signal_artifacts.json").read_bytes())
    delivered = artifact["scenarios"][0]["awg"]["lanes"]["q1_xy_i"]["delivered_after_fir_latency_V"]
    if attack == "delivered_short":
        delivered.pop()
        _rewrite_candidate(target, artifact)
    elif attack == "delivered_reordered":
        delivered[100], delivered[101] = delivered[101], delivered[100]
        _rewrite_candidate(target, artifact)
    elif attack == "effective_short":
        artifact["scenarios"][0]["effective"]["xy_drive_GHz"]["q1"]["i"].pop()
        _rewrite_candidate(target, artifact)
    else:
        delivered[100] = float("nan")
        (target / "control_signal_artifacts.json").write_text(json.dumps(artifact, allow_nan=True), encoding="utf-8")
    with pytest.raises(ValueError):
        _builder(target)


def test_formal_runner_and_cli_dispatch_contracts():
    formal = (ROOT / "scripts/run_stage_04_control_signal.py").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/run_stage_04_control_signal_smoke.py").read_text(encoding="utf-8")
    cli = (ROOT / "src/sqvm/__main__.py").read_text(encoding="utf-8")
    assert "2q1c2r_control.yaml" in formal and "output/stage_04_control_signal" in formal
    assert "2q1c2r_control_smoke.yaml" in smoke and "output/stage_04_control_signal_smoke" in smoke
    assert "verify-control" in cli


def test_stage4_control_source_keeps_lazy_spectrum_boundary():
    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src/sqvm/control").glob("*.py"))
    assert "sqvm.spectrum" not in source
