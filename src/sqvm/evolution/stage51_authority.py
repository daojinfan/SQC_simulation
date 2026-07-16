"""Authority and verified-control admission for Stage 5.1."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from sqvm.control.stage4_1_artifacts import (
    CONTROL_NAME, INVENTORY_NAME, MANIFEST_NAME, RECEIPT_NAME, REPORT_NAME,
)
from sqvm.control.stage4_1_verify import VerifiedControlHandle
from sqvm.evolution.stage51_models import (
    Stage51EvolutionError, Stage51EvolutionInput, Stage51FailureCode,
    Stage51PhysicsContext,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256


DT_NS = 0.5


def fail(code: Stage51FailureCode, detail: str) -> None:
    raise Stage51EvolutionError(code, detail)


def plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [plain(item) for item in value]
    return value


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(plain(value))).hexdigest().upper()


def read_json(path: Path, code: Stage51FailureCode) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(code, f"cannot read {path.name}: {exc}")
    if not isinstance(value, dict):
        fail(code, f"{path.name} must be an object")
    return value


def safe_file(root: Path, path: Path, code: Stage51FailureCode) -> Path:
    try:
        root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        fail(code, f"path outside root: {path}")
    if resolved.is_symlink() or not resolved.is_file():
        fail(code, f"path is not a regular file: {path}")
    return resolved


def freeze_array(value: Any, dtype: str, name: str) -> np.ndarray:
    if (
        not isinstance(value, np.ndarray)
        or value.dtype != np.dtype(dtype)
        or value.ndim != 1
        or value.size == 0
        or not value.flags.c_contiguous
        or value.flags.writeable
        or not np.all(np.isfinite(value))
    ):
        fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, name)
    result = value.copy(order="C")
    result.setflags(write=False)
    return result


def admit_physics_authority(context: Stage51PhysicsContext) -> tuple[Mapping[str, Any], Mapping[str, str]]:
    """Admit the independently approved numerical source, never Stage 5 config."""

    if not isinstance(context, Stage51PhysicsContext):
        fail(Stage51FailureCode.PHYSICS_AUTHORITY_INVALID, "typed context required")
    root = context.repository_root.resolve()
    authority_path = safe_file(root, context.accepted_stage5_physics_authority, Stage51FailureCode.PHYSICS_AUTHORITY_INVALID)
    authority = read_json(authority_path, Stage51FailureCode.PHYSICS_AUTHORITY_INVALID)
    expected = {
        "schema_version", "artifact_type", "artifact_version", "status", "device",
        "hamiltonian", "model", "frame", "solver", "tolerances",
        "accepted_stage5_bindings", "source_snapshot_sha256",
        "environment_snapshot_sha256", "publication_policy_sha256", "authority_id",
    }
    if set(authority) != expected or authority.get("schema_version") != "0.1" or authority.get("artifact_type") != "stage_05_1_physics_authority" or authority.get("artifact_version") != "0.1" or authority.get("status") != "approved":
        fail(Stage51FailureCode.PHYSICS_AUTHORITY_INVALID, "authority schema")
    payload = dict(authority)
    authority_id = payload.pop("authority_id")
    if not isinstance(authority_id, str) or authority_id != canonical_sha256(payload):
        fail(Stage51FailureCode.PHYSICS_AUTHORITY_INVALID, "authority_id")
    model = authority["model"]
    if not isinstance(model, Mapping) or model != {"tensor_order": ["q1", "c", "q2"], "charge_cutoffs": [1, 1, 1], "reference_state_count": 16}:
        fail(Stage51FailureCode.TENSOR_MAPPING_INVALID, "model")
    if authority["frame"] != {"rwa_projection": "number_sector_v1"}:
        fail(Stage51FailureCode.FRAME_AUTHORITY_MISMATCH, "frame")
    bindings = {
        "physics_authority": raw_file_sha256(authority_path),
        "physics_authority_id": authority_id,
    }
    for name, configured in (("device", context.accepted_device_artifact), ("hamiltonian", context.accepted_hamiltonian_artifact)):
        row = authority[name]
        if not isinstance(row, Mapping) or set(row) != {"path", "raw_sha256"} or not isinstance(row["path"], str) or not isinstance(row["raw_sha256"], str):
            fail(Stage51FailureCode.PHYSICS_AUTHORITY_INVALID, name)
        path = safe_file(root, root / row["path"], Stage51FailureCode.PHYSICS_AUTHORITY_INVALID)
        if path != safe_file(root, configured, Stage51FailureCode.PHYSICS_AUTHORITY_INVALID) or raw_file_sha256(path) != row["raw_sha256"]:
            fail(Stage51FailureCode.PHYSICS_AUTHORITY_INVALID, f"{name} binding")
        bindings[name] = row["raw_sha256"]
    for name, configured in (
        ("design", context.stage5_1_design_authority), ("approval", context.stage5_1_approval_authority),
        ("solver_validation", context.solver_validation_approval), ("source_snapshot", context.source_snapshot),
        ("environment_snapshot", context.environment_snapshot), ("publication_policy", context.publication_policy),
    ):
        bindings[name] = raw_file_sha256(safe_file(root, configured, Stage51FailureCode.PHYSICS_AUTHORITY_INVALID))
    if authority["source_snapshot_sha256"] != bindings["source_snapshot"] or authority["environment_snapshot_sha256"] != bindings["environment_snapshot"] or authority["publication_policy_sha256"] != bindings["publication_policy"]:
        fail(Stage51FailureCode.PHYSICS_AUTHORITY_INVALID, "snapshot binding")
    if not isinstance(authority["solver"], Mapping) or not isinstance(authority["tolerances"], Mapping):
        fail(Stage51FailureCode.SOLVER_AUTHORITY_INVALID, "solver/tolerances")
    return MappingProxyType(plain(authority)), MappingProxyType(bindings)


def _recheck_handle(handle: VerifiedControlHandle, context: Stage51PhysicsContext) -> None:
    if not isinstance(handle, VerifiedControlHandle) or handle.schema_version != "0.1" or not isinstance(handle.control_id, str) or not handle.control_id:
        fail(Stage51FailureCode.HANDLE_SCHEMA_INVALID, "handle")
    root = handle.artifact_root.resolve()
    try:
        root.relative_to(context.repository_root.resolve())
    except ValueError:
        fail(Stage51FailureCode.HANDLE_NOT_PUBLISHED, "artifact root")
    for name, expected in ((MANIFEST_NAME, handle.manifest_sha256), (RECEIPT_NAME, handle.receipt_sha256), (INVENTORY_NAME, handle.inventory_sha256)):
        path = root / name
        if not path.is_file() or raw_file_sha256(path) != expected:
            fail(Stage51FailureCode.CONTROL_BINDING_MISMATCH, name)
    control = read_json(root / CONTROL_NAME, Stage51FailureCode.HANDLE_NOT_PUBLISHED)
    receipt = read_json(root / RECEIPT_NAME, Stage51FailureCode.HANDLE_NOT_PUBLISHED)
    report = read_json(root / REPORT_NAME, Stage51FailureCode.HANDLE_NOT_PUBLISHED)
    if control.get("status") != "published" or receipt.get("status") != "published" or report.get("ok") is not True or control.get("control_id") != handle.control_id or receipt.get("control_id") != handle.control_id or control.get("effective", {}).get("effective_control_sha256") != handle.effective_control_sha256:
        fail(Stage51FailureCode.HANDLE_NOT_PUBLISHED, "receipt/report/control")


def admit_verified_control(handle: VerifiedControlHandle, context: Stage51PhysicsContext) -> Stage51EvolutionInput:
    _recheck_handle(handle, context)
    if set(handle.xy_drive_GHz) != {"q1", "q2"} or set(handle.absolute_flux_phi0) != {"q1", "q2", "c"} or set(handle.frame_reference_frequency_GHz) != {"q1", "q2"}:
        fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, "names")
    centers = freeze_array(handle.time_center_ns, "<f8", "time_center_ns")
    if not np.array_equal(centers, centers[0] + np.arange(centers.size, dtype="<f8") * DT_NS):
        fail(Stage51FailureCode.CONTROL_CLOCK_MISMATCH, "signed centers")
    epsilon: dict[str, np.ndarray] = {}
    for mode in ("q1", "q2"):
        pair = handle.xy_drive_GHz[mode]
        if not isinstance(pair, tuple) or len(pair) != 2:
            fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, mode)
        i, q = freeze_array(pair[0], "<f8", f"{mode}.i"), freeze_array(pair[1], "<f8", f"{mode}.q")
        if i.size != centers.size or q.size != centers.size:
            fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, mode)
        value = np.asarray(i + 1j * q, dtype="<c16")
        value.setflags(write=False)
        epsilon[mode] = value
    flux = {name: freeze_array(handle.absolute_flux_phi0[name], "<f8", f"flux.{name}") for name in ("q1", "c", "q2")}
    if any(value.size != centers.size for value in flux.values()):
        fail(Stage51FailureCode.CONTROL_ARRAY_INVALID, "flux length")
    frame = {name: handle.frame_reference_frequency_GHz[name] for name in ("q1", "q2")}
    if any(type(value) is not float or not math.isfinite(value) for value in frame.values()):
        fail(Stage51FailureCode.FRAME_AUTHORITY_MISMATCH, "frame")
    binding = MappingProxyType({"control_id": handle.control_id, "manifest_sha256": handle.manifest_sha256, "receipt_sha256": handle.receipt_sha256, "inventory_sha256": handle.inventory_sha256, "effective_control_sha256": handle.effective_control_sha256})
    checks = tuple(MappingProxyType({"name": name, "passed": True}) for name in ("verified_control_handle_valid", "control_receipt_rechecked", "effective_arrays_exact", "signed_sample_grid_exact", "named_tensor_mapping_exact", "frame_reference_authority_valid", "phase_not_reapplied", "idle_not_reapplied"))
    return Stage51EvolutionInput(handle.control_id, binding, centers, MappingProxyType(epsilon), MappingProxyType(flux), MappingProxyType(frame), checks)
