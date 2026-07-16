from __future__ import annotations

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy as np
import pytest

from sqvm.control.stage4_1_artifacts import Stage41ArtifactError, write_parameterized_control_artifact
from sqvm.control.stage4_1_verify import independent_electronics_replay, verify_parameterized_control_artifact
from sqvm.hamiltonian.provenance import canonical_json_bytes


def _context(tmp_path: Path):
    lanes = {name: {"latency_samples": 0, "fir": [1.0]} for name in ("q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q", "q1_z", "q2_z", "c_z")}
    config = SimpleNamespace(
        dt_ns=0.5,
        dac={"lsb_V": 0.001, "code_min": -32768, "code_max": 32767},
        lanes=lanes,
        static_mixing={
            "xy": {"input_lanes": ["q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q"], "output_coordinates": ["q1_i", "q1_q", "q2_i", "q2_q"], "matrix": np.eye(4)},
            "z": {"input_lanes": ["q1_z", "q2_z", "c_z"], "output_coordinates": ["q1_flux_delta", "q2_flux_delta", "c_flux_delta"], "matrix": np.eye(3)},
        },
        idle_flux_phi0={"q1": 0.1, "q2": 0.0, "c": 0.27},
    )
    return SimpleNamespace(output_root=tmp_path, control_chain_config=config, compiler_source_snapshot={}, environment_snapshot={}, device_flux_limits_phi0={"q1": [-0.5, 0.5], "q2": [-0.5, 0.5], "c": [-0.5, 0.5]})


def _compilation(context, *, corrupt: str | None = None):
    time = np.asarray([0.25, 0.75], dtype="<f8")
    logical = {
        "time_center_ns": time, "q1_i": np.asarray([0.01, 0.0], dtype="<f8"), "q1_q": np.zeros(2, dtype="<f8"),
        "q2_i": np.zeros(2, dtype="<f8"), "q2_q": np.zeros(2, dtype="<f8"),
        "q1_flux_absolute": np.asarray([0.1, 0.11], dtype="<f8"), "q2_flux_absolute": np.zeros(2, dtype="<f8"), "c_flux_absolute": np.full(2, 0.27, dtype="<f8"),
    }
    awg, effective = independent_electronics_replay(logical, context.control_chain_config)
    if corrupt == "idle":
        effective["q1_flux_absolute"] = effective["q1_flux_absolute"] + 0.1
    if corrupt == "awg":
        awg["q1_xy_i"]["dac_codes"] = awg["q1_xy_i"]["dac_codes"].copy()
        awg["q1_xy_i"]["dac_codes"][0] += 1
    return SimpleNamespace(
        point_id="point_1",
        logical_arrays={"time_center_ns": time, "xy_delta_GHz": {"q1": {"i": logical["q1_i"], "q": logical["q1_q"]}, "q2": {"i": logical["q2_i"], "q": logical["q2_q"]}}, "flux_absolute_phi0": {mode: logical[f"{mode}_flux_absolute"] for mode in ("q1", "q2", "c")}, "frame_reference_frequency_GHz": {"q1": 5.0, "q2": 5.1}},
        awg_arrays=awg,
        effective_arrays={"time_center_ns": effective["time_center_ns"], "xy_drive_GHz": {"q1": {"i": effective["q1_i"], "q": effective["q1_q"]}, "q2": {"i": effective["q2_i"], "q": effective["q2_q"]}}, "flux_delta_phi0": {mode: effective[f"{mode}_flux_delta"] for mode in ("q1", "q2", "c")}, "absolute_flux_phi0": {mode: effective[f"{mode}_flux_absolute"] for mode in ("q1", "q2", "c")}},
        source_binding={"plan_sha256": "A" * 64}, authority_binding={"control_config_sha256": "B" * 64},
        checks=({"name": "logical_plan_schema_valid", "passed": True, "reason_code": "OK", "evidence_ref": "fixture"},), metrics={},
    )


def test_publish_replay_and_readonly_handle(tmp_path):
    context = _context(tmp_path)
    result = write_parameterized_control_artifact(_compilation(context), context, tmp_path / "point")
    root = result["artifact_root"]
    assert {path.name for path in root.iterdir()} == {"arrays", "control.json", "array_inventory.json", "source_snapshot.json", "environment_snapshot.json", "manifest.json", "verification_report.json", "receipt.json"}
    report = json.loads((root / "verification_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True and report["blocking_reasons"] == []
    handle = verify_parameterized_control_artifact(root, context)
    assert not handle.time_center_ns.flags.writeable
    assert handle.absolute_flux_phi0["q1"].tolist() == [0.1, 0.11]


@pytest.mark.parametrize("kind", ["idle", "awg"])
def test_staging_replay_failure_publishes_nothing(tmp_path, kind):
    context = _context(tmp_path)
    target = tmp_path / "point"
    with pytest.raises(Stage41ArtifactError):
        write_parameterized_control_artifact(_compilation(context, corrupt=kind), context, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".point.staging.*"))


def test_post_publication_array_tamper_is_detected(tmp_path):
    context = _context(tmp_path)
    root = write_parameterized_control_artifact(_compilation(context), context, tmp_path / "point")["artifact_root"]
    path = root / "arrays" / "effective" / "q1_i.bin"
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(Stage41ArtifactError) as captured:
        verify_parameterized_control_artifact(root, context)
    assert captured.value.code in {"PLAN_HASH_MISMATCH", "FORWARD_RECONSTRUCTION_MISMATCH", "ARTIFACT_VERIFICATION_FAILED"}


def test_inventory_path_escape_is_detected(tmp_path):
    context = _context(tmp_path)
    root = write_parameterized_control_artifact(_compilation(context), context, tmp_path / "point")["artifact_root"]
    inventory_path = root / "array_inventory.json"
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    payload["arrays"][0]["path"] = "../outside.bin"
    inventory_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(Stage41ArtifactError):
        verify_parameterized_control_artifact(root, context)


def test_publication_is_no_replace(tmp_path):
    context = _context(tmp_path)
    target = tmp_path / "point"
    write_parameterized_control_artifact(_compilation(context), context, target)
    with pytest.raises(Stage41ArtifactError) as captured:
        write_parameterized_control_artifact(_compilation(context), context, target)
    assert captured.value.code == "PUBLICATION_CONFLICT"


def test_unpublished_staging_copy_cannot_construct_handle(tmp_path):
    context = _context(tmp_path)
    root = write_parameterized_control_artifact(_compilation(context), context, tmp_path / "point")["artifact_root"]
    staging = tmp_path / ".point.staging.attacker"
    shutil.copytree(root, staging)
    with pytest.raises(Stage41ArtifactError) as captured:
        verify_parameterized_control_artifact(staging, context)
    assert captured.value.code == "ARTIFACT_VERIFICATION_FAILED"
