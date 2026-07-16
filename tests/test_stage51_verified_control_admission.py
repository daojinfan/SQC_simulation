from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import MappingProxyType
from typing import Any, Callable
import uuid

import numpy as np
import pytest

from sqvm.control import (
    admit_qcis_v03_plan,
    build_parameterized_control_context,
    compile_qcis_waveform_plan,
    load_control_chain_config,
    load_control_channel_registry,
    verify_parameterized_control_artifact,
    write_parameterized_control_artifact,
)
from sqvm.evolution import (
    Stage51EvolutionError, Stage51FailureCode, Stage51PhysicsContext,
    admit_verified_control, build_evolution_coefficient_plan,
    production_stage51_physics_context, publish_evolution_coefficient_artifact,
    run_verified_control_evolution, verify_stage51_evolution_artifact,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json


ROOT = Path(__file__).resolve().parents[1]


def _sha(values: np.ndarray) -> str:
    return hashlib.sha256(values.tobytes(order="C")).hexdigest().upper()


def _stage41_context(output_root: Path):
    return build_parameterized_control_context(
        load_control_chain_config(ROOT / "configs/control/2q1c2r_control_smoke.yaml"),
        load_control_channel_registry(ROOT / "configs/control/2q1c2r_channels.yaml"),
        {name: (-0.5, 0.5) for name in ("q1", "q2", "c")},
        device_limit_authority_sha256="D" * 64,
        repository_root=ROOT,
        output_root=output_root,
        authority_sha256={"stage4_1": "B" * 64},
        expected_plan_authority_sha256={"qcis": "A" * 64},
        stage4_compatibility_approved=True,
        compiler_source_snapshot={"source_sha256": "C" * 64},
        environment_snapshot={"environment_sha256": "D" * 64},
        publication_policy={"mode": "atomic_no_replace"},
    )


def _raw_plan() -> dict[str, Any]:
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
        name: {
            "name": name,
            "dtype": "<f8",
            "shape": [n],
            "unit": "GHz" if ".xy_delta_" in name else "Phi/Phi0",
            "byte_length": value.nbytes,
            "sha256": _sha(value),
        }
        for name, value in arrays.items()
    }
    return {
        "schema_version": "0.3",
        "profile_id": "qcis_stage7_calibration_v3",
        "point_id": "stage51_admission_point",
        "concrete_source_sha256": "1" * 64,
        "ast_sha256": "2" * 64,
        "trace_sha256": "3" * 64,
        "sample_count": n,
        "dt_ns": 0.5,
        "logical": {
            "xy_delta_GHz": {
                "q1": {"i": arrays["logical.xy_delta_GHz.q1.i"], "q": arrays["logical.xy_delta_GHz.q1.q"]},
                "q2": {"i": arrays["logical.xy_delta_GHz.q2.i"], "q": arrays["logical.xy_delta_GHz.q2.q"]},
            },
            "flux_delta_phi0": {
                "q1": arrays["logical.flux_delta_phi0.q1"],
                "q2": arrays["logical.flux_delta_phi0.q2"],
                "c": arrays["logical.flux_delta_phi0.c"],
            },
        },
        "frame_reference_frequency_GHz": {"q1": 5.0, "q2": 5.1},
        "frame_reference_authority_sha256": {"q1": "3" * 64, "q2": "4" * 64},
        "array_inventory": inventory,
        "drive_event_inventory": [],
        "drive_event_inventory_sha256": sha256_json([]),
        "authority_sha256": {"qcis": "A" * 64},
    }


def _stage51_context(root: Path) -> Stage51PhysicsContext:
    paths = {}
    for name in (
        "design.md", "approval.json", "physics-authority.json", "device.yaml", "hamiltonian.yaml",
        "solver.json", "source.json", "environment.json", "publication-policy.json",
    ):
        path = root / "authorities" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}\n")
        paths[name] = path
    return Stage51PhysicsContext(
        repository_root=root,
        output_root=root / "stage51-output",
        stage5_1_design_authority=paths["design.md"],
        stage5_1_approval_authority=paths["approval.json"],
        accepted_stage5_physics_authority=paths["physics-authority.json"],
        accepted_device_artifact=paths["device.yaml"],
        accepted_hamiltonian_artifact=paths["hamiltonian.yaml"],
        solver_validation_approval=paths["solver.json"],
        source_snapshot=paths["source.json"],
        environment_snapshot=paths["environment.json"],
        publication_policy=paths["publication-policy.json"],
    )


def _published_handle(tmp_path: Path):
    stage41 = _stage41_context(tmp_path)
    expected_plan = admit_qcis_v03_plan(_raw_plan(), stage41)
    compilation = compile_qcis_waveform_plan(expected_plan, stage41)
    publication = write_parameterized_control_artifact(compilation, stage41, tmp_path / "point")
    handle = verify_parameterized_control_artifact(publication["artifact_root"], stage41, expected_plan)
    return handle, _stage51_context(tmp_path)


def _admit(handle, context):
    return admit_verified_control(handle, context)


def _assert_stage51_reject(handle, context, expected: Stage51FailureCode) -> None:
    with pytest.raises(Stage51EvolutionError) as captured:
        _admit(handle, context)
    assert captured.value.code is expected


def test_real_stage41_publication_yields_eight_exact_effective_arrays_and_admitted_controls(tmp_path: Path):
    handle, context = _published_handle(tmp_path)
    admitted = _admit(handle, context)
    effective = {
        "time_center_ns": handle.time_center_ns,
        "q1_i": handle.xy_drive_GHz["q1"][0],
        "q1_q": handle.xy_drive_GHz["q1"][1],
        "q2_i": handle.xy_drive_GHz["q2"][0],
        "q2_q": handle.xy_drive_GHz["q2"][1],
        "q1_flux_absolute": handle.absolute_flux_phi0["q1"],
        "q2_flux_absolute": handle.absolute_flux_phi0["q2"],
        "c_flux_absolute": handle.absolute_flux_phi0["c"],
    }
    assert len(effective) == 8
    assert np.array_equal(admitted.time_center_ns, effective["time_center_ns"])
    assert np.array_equal(admitted.epsilon_q1, effective["q1_i"] + 1j * effective["q1_q"])
    assert np.array_equal(admitted.epsilon_q2, effective["q2_i"] + 1j * effective["q2_q"])
    assert np.array_equal(admitted.absolute_flux_phi0["q1"], effective["q1_flux_absolute"])
    assert np.array_equal(admitted.absolute_flux_phi0["q2"], effective["q2_flux_absolute"])
    assert np.array_equal(admitted.absolute_flux_phi0["c"], effective["c_flux_absolute"])
    assert admitted.control_binding["control_id"] == handle.control_id


def test_real_stage41_handle_runs_through_production_qutip_worker_and_replay():
    workspace = ROOT / "output" / f".stage51-e2e.{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    try:
        source_handle, _ = _published_handle(workspace)
        context = production_stage51_physics_context(ROOT, output_root=workspace / "stage51")
        admitted = admit_verified_control(source_handle, context)
        plan = build_evolution_coefficient_plan(admitted, context)
        coefficients = publish_evolution_coefficient_artifact(
            plan, context, context.output_root / "coefficient", source_handle,
        )

        published = run_verified_control_evolution(
            coefficients, context, context.output_root / "evolution", timeout_s=180.0,
        )
        verified = verify_stage51_evolution_artifact(
            published.artifact_root, coefficients, context,
        )

        assert published.status == "published"
        assert verified.result_id == published.result_id
        assert verified.replay_fidelity == pytest.approx(1.0, abs=1.0e-9)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.mark.parametrize("kind", ("q1_i", "q1_flux_absolute"))
def test_copied_handle_with_in_memory_effective_value_tamper_rejects(kind: str, tmp_path: Path):
    handle, context = _published_handle(tmp_path)
    if kind == "q1_i":
        q1_i = handle.xy_drive_GHz["q1"][0].copy()
        q1_i.setflags(write=True)
        q1_i[0] = np.nextafter(q1_i[0], np.inf)
        q1_i.setflags(write=False)
        tampered = replace(handle, xy_drive_GHz=MappingProxyType({"q1": (q1_i, handle.xy_drive_GHz["q1"][1]), "q2": handle.xy_drive_GHz["q2"]}))
    else:
        q1_flux = handle.absolute_flux_phi0["q1"].copy()
        q1_flux.setflags(write=True)
        q1_flux[0] = np.nextafter(q1_flux[0], np.inf)
        q1_flux.setflags(write=False)
        tampered = replace(handle, absolute_flux_phi0=MappingProxyType({"q1": q1_flux, "q2": handle.absolute_flux_phi0["q2"], "c": handle.absolute_flux_phi0["c"]}))
    _assert_stage51_reject(tampered, context, Stage51FailureCode.CONTROL_BINDING_MISMATCH)


def _flip_first_byte(path: Path) -> None:
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])


def test_post_publication_effective_raw_byte_tamper_rejects_as_control_binding_mismatch(tmp_path: Path):
    handle, context = _published_handle(tmp_path)
    _flip_first_byte(handle.artifact_root / "arrays/effective/q1_i.bin")
    _assert_stage51_reject(handle, context, Stage51FailureCode.CONTROL_BINDING_MISMATCH)


def _mutate_json(path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_bytes(canonical_json_bytes(payload))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["arrays"][0].update({"dtype": ">f8"}),
        lambda payload: payload["arrays"][0].update({"shape": [3]}),
        lambda payload: payload["arrays"][0].update({"byte_length": 1}),
        lambda payload: payload["arrays"][0].update({"sha256": "0" * 64}),
        lambda payload: payload["arrays"].append(dict(payload["arrays"][0])),
        lambda payload: payload["arrays"][0].update({"path": "../outside.bin"}),
    ],
    ids=("dtype", "shape", "nbytes", "hash", "duplicate_effective_name", "path_traversal"),
)
def test_inventory_tampering_rejects_before_stage51_admission(mutate, tmp_path: Path):
    handle, context = _published_handle(tmp_path)
    _mutate_json(handle.artifact_root / "array_inventory.json", mutate)
    _assert_stage51_reject(handle, context, Stage51FailureCode.CONTROL_BINDING_MISMATCH)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("manifest.json", Stage51FailureCode.CONTROL_BINDING_MISMATCH),
        ("verification_report.json", Stage51FailureCode.HANDLE_NOT_PUBLISHED),
        ("receipt.json", Stage51FailureCode.CONTROL_BINDING_MISMATCH),
    ],
)
def test_publication_topology_file_tampering_rejects_before_stage51_admission(
    name: str, expected: Stage51FailureCode, tmp_path: Path
):
    handle, context = _published_handle(tmp_path)
    _flip_first_byte(handle.artifact_root / name)
    _assert_stage51_reject(handle, context, expected)


def test_effective_symlink_rejects_fail_closed(tmp_path: Path):
    handle, context = _published_handle(tmp_path)
    path = handle.artifact_root / "arrays/effective/q1_i.bin"
    target = handle.artifact_root / "arrays/effective/q1_i.original.bin"
    path.replace(target)
    try:
        path.symlink_to(target.name)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    _assert_stage51_reject(handle, context, Stage51FailureCode.CONTROL_BINDING_MISMATCH)


@pytest.mark.skipif(os.name != "nt", reason="junction is a Windows reparse-point condition")
def test_effective_junction_rejects_fail_closed(tmp_path: Path):
    handle, context = _published_handle(tmp_path)
    effective = handle.artifact_root / "arrays/effective"
    backing = handle.artifact_root / "arrays/effective-backing"
    shutil.copytree(effective, backing)
    shutil.rmtree(effective)
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(effective), str(backing)], capture_output=True, text=True)
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr or created.stdout}")
    _assert_stage51_reject(handle, context, Stage51FailureCode.CONTROL_BINDING_MISMATCH)
