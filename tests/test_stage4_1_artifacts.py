from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import json
import hashlib
from dataclasses import replace
from pathlib import Path
import shutil

import numpy as np
import pytest

from sqvm.control import (
    Stage41ArtifactError,
    admit_qcis_v03_plan,
    build_parameterized_control_context,
    compile_qcis_waveform_plan,
    load_control_chain_config,
    load_control_channel_registry,
    verify_parameterized_control_artifact,
    write_parameterized_control_artifact,
)
from sqvm.control.stage4_1_models import LogicalArrayInventoryRow, freeze_mapping
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json


def _context(tmp_path: Path):
    return build_parameterized_control_context(
        load_control_chain_config("configs/control/2q1c2r_control_smoke.yaml"),
        load_control_channel_registry("configs/control/2q1c2r_channels.yaml"),
        {name: (-0.5, 0.5) for name in ("q1", "q2", "c")},
        device_limit_authority_sha256="D" * 64,
        repository_root=Path.cwd().resolve(),
        output_root=tmp_path.resolve(),
        authority_sha256={"stage4_1": "B" * 64},
        expected_plan_authority_sha256={"qcis": "A" * 64},
        stage4_compatibility_approved=True,
        compiler_source_snapshot={},
        environment_snapshot={},
        publication_policy={"mode": "atomic_no_replace"},
    )


def _compilation(context, *, corrupt: str | None = None):
    n = 2
    arrays = {
        "logical.xy_delta_GHz.q1.i": np.asarray([0.001, 0.0], dtype="<f8"),
        "logical.xy_delta_GHz.q1.q": np.zeros(n, dtype="<f8"),
        "logical.xy_delta_GHz.q2.i": np.zeros(n, dtype="<f8"),
        "logical.xy_delta_GHz.q2.q": np.zeros(n, dtype="<f8"),
        "logical.flux_delta_phi0.q1": np.asarray([0.0, 0.01], dtype="<f8"),
        "logical.flux_delta_phi0.q2": np.zeros(n, dtype="<f8"),
        "logical.flux_delta_phi0.c": np.zeros(n, dtype="<f8"),
    }
    inventory = {
        name: {"name": name, "dtype": "<f8", "shape": [n], "unit": "GHz" if ".xy_delta_" in name else "Phi/Phi0", "byte_length": value.nbytes, "sha256": hashlib.sha256(value.tobytes()).hexdigest().upper()}
        for name, value in arrays.items()
    }
    raw_plan = {
        "schema_version": "0.3", "profile_id": "qcis_stage7_calibration_v3", "point_id": "point_1",
        "concrete_source_sha256": "1" * 64, "ast_sha256": "2" * 64, "trace_sha256": "3" * 64,
        "sample_count": n, "dt_ns": 0.5,
        "logical": {"xy_delta_GHz": {"q1": {"i": arrays["logical.xy_delta_GHz.q1.i"], "q": arrays["logical.xy_delta_GHz.q1.q"]}, "q2": {"i": arrays["logical.xy_delta_GHz.q2.i"], "q": arrays["logical.xy_delta_GHz.q2.q"]}}, "flux_delta_phi0": {"q1": arrays["logical.flux_delta_phi0.q1"], "q2": arrays["logical.flux_delta_phi0.q2"], "c": arrays["logical.flux_delta_phi0.c"]}},
        "frame_reference_frequency_GHz": {"q1": 5.0, "q2": 5.1},
        "frame_reference_authority_sha256": {"q1": "C" * 64, "q2": "D" * 64},
        "array_inventory": inventory, "drive_event_inventory": [], "drive_event_inventory_sha256": sha256_json([]),
        "authority_sha256": context.expected_plan_authority_sha256,
    }
    result = compile_qcis_waveform_plan(admit_qcis_v03_plan(raw_plan, context), context)
    if corrupt == "idle":
        absolute = dict(result.effective_absolute_flux_phi0)
        absolute["q1"] = np.asarray(absolute["q1"] + 0.1, dtype="<f8")
        result = replace(result, effective_absolute_flux_phi0=freeze_mapping(absolute))
    if corrupt == "awg":
        codes = dict(result.dac_codes)
        codes["q1_xy_i"] = codes["q1_xy_i"].copy()
        codes["q1_xy_i"].setflags(write=True)
        codes["q1_xy_i"][0] += 1
        result = replace(result, dac_codes=freeze_mapping(codes))
    if corrupt == "source":
        rows = dict(result.plan.array_inventory)
        row = rows["logical.xy_delta_GHz.q1.i"]
        rows[row.name] = LogicalArrayInventoryRow(row.name, row.dtype, row.shape, row.unit, row.byte_length, "0" * 64)
        result = replace(result, plan=replace(result.plan, array_inventory=freeze_mapping(rows)))
    if corrupt == "time":
        result = replace(result, logical_time_center_ns=np.asarray(result.logical_time_center_ns + 1.0, dtype="<f8"))
    return result


def test_publish_replay_and_readonly_handle(tmp_path):
    context = _context(tmp_path)
    compilation = _compilation(context)
    result = write_parameterized_control_artifact(compilation, context, tmp_path / "point")
    root = result["artifact_root"]
    assert {path.name for path in root.iterdir()} == {"arrays", "control.json", "array_inventory.json", "source_snapshot.json", "environment_snapshot.json", "manifest.json", "verification_report.json", "receipt.json"}
    report = json.loads((root / "verification_report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True and report["blocking_reasons"] == []
    handle = verify_parameterized_control_artifact(root, context, compilation.plan)
    assert not handle.time_center_ns.flags.writeable
    assert np.allclose(
        handle.absolute_flux_phi0["c"],
        np.full(handle.time_center_ns.size, 0.27, dtype="<f8"),
        atol=1e-6,
    )


@pytest.mark.parametrize("kind", ["idle", "awg", "source", "time"])
def test_staging_replay_failure_publishes_nothing(tmp_path, kind):
    context = _context(tmp_path)
    target = tmp_path / "point"
    with pytest.raises(Stage41ArtifactError):
        write_parameterized_control_artifact(_compilation(context, corrupt=kind), context, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".point.staging.*"))


def test_post_publication_array_tamper_is_detected(tmp_path):
    context = _context(tmp_path)
    compilation = _compilation(context)
    root = write_parameterized_control_artifact(compilation, context, tmp_path / "point")["artifact_root"]
    path = root / "arrays" / "effective" / "q1_i.bin"
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(Stage41ArtifactError) as captured:
        verify_parameterized_control_artifact(root, context, compilation.plan)
    assert captured.value.code in {"PLAN_HASH_MISMATCH", "FORWARD_RECONSTRUCTION_MISMATCH", "ARTIFACT_VERIFICATION_FAILED"}


def test_inventory_path_escape_is_detected(tmp_path):
    context = _context(tmp_path)
    compilation = _compilation(context)
    root = write_parameterized_control_artifact(compilation, context, tmp_path / "point")["artifact_root"]
    inventory_path = root / "array_inventory.json"
    payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    payload["arrays"][0]["path"] = "../outside.bin"
    inventory_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(Stage41ArtifactError):
        verify_parameterized_control_artifact(root, context, compilation.plan)


def test_publication_is_no_replace(tmp_path):
    context = _context(tmp_path)
    target = tmp_path / "point"
    write_parameterized_control_artifact(_compilation(context), context, target)
    with pytest.raises(Stage41ArtifactError) as captured:
        write_parameterized_control_artifact(_compilation(context), context, target)
    assert captured.value.code == "PUBLICATION_CONFLICT"


def test_unpublished_staging_copy_cannot_construct_handle(tmp_path):
    context = _context(tmp_path)
    compilation = _compilation(context)
    root = write_parameterized_control_artifact(compilation, context, tmp_path / "point")["artifact_root"]
    staging = tmp_path / ".point.staging.attacker"
    shutil.copytree(root, staging)
    with pytest.raises(Stage41ArtifactError) as captured:
        verify_parameterized_control_artifact(staging, context, compilation.plan)
    assert captured.value.code == "ARTIFACT_VERIFICATION_FAILED"


def test_artifact_cannot_be_rebound_to_a_different_control_authority(tmp_path):
    context = _context(tmp_path)
    compilation = _compilation(context)
    root = write_parameterized_control_artifact(compilation, context, tmp_path / "point")["artifact_root"]
    altered = replace(context, authority_sha256=freeze_mapping({"stage4_1": "E" * 64}))
    with pytest.raises(Stage41ArtifactError) as captured:
        verify_parameterized_control_artifact(root, altered, compilation.plan)
    assert captured.value.code == "PLAN_AUTHORITY_MISMATCH"


def test_control_id_binds_the_environment_snapshot(tmp_path):
    first_context = _context(tmp_path)
    second_context = replace(first_context, environment_snapshot=freeze_mapping({"numpy": "different"}))
    first = write_parameterized_control_artifact(_compilation(first_context), first_context, tmp_path / "first")
    second = write_parameterized_control_artifact(_compilation(second_context), second_context, tmp_path / "second")
    assert first["control_id"] != second["control_id"]


def test_handle_requires_the_independently_admitted_frame_plan(tmp_path):
    context = _context(tmp_path)
    compilation = _compilation(context)
    root = write_parameterized_control_artifact(compilation, context, tmp_path / "point")["artifact_root"]
    altered_plan = replace(compilation.plan, frame_reference_frequency_GHz=freeze_mapping({"q1": 4.9, "q2": 5.1}))
    with pytest.raises(Stage41ArtifactError) as captured:
        verify_parameterized_control_artifact(root, context, altered_plan)
    assert captured.value.code == "PLAN_AUTHORITY_MISMATCH"
