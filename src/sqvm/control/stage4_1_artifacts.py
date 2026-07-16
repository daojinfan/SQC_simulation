"""Atomic raw-binary publication for Stage 4.1 control points.

The public writer admits only the typed pre-publication result and context. The
artifact itself remains language-neutral canonical JSON plus raw binary arrays.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, is_dataclass
from decimal import Decimal
from pathlib import Path
import shutil
from typing import Any, Mapping
import uuid

import numpy as np

from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import atomic_publish, inventory_tree
from sqvm.control.stage4_1_models import ParameterizedControlCompilation, ParameterizedControlContext, ParameterizedControlError
from sqvm.control.stage4_1_config import validate_parameterized_control_context


CONTROL_NAME = "control.json"
INVENTORY_NAME = "array_inventory.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "verification_report.json"
RECEIPT_NAME = "receipt.json"
SOURCE_SNAPSHOT_NAME = "source_snapshot.json"
ENVIRONMENT_SNAPSHOT_NAME = "environment_snapshot.json"
_TERMINAL = {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}
_REQUIRED_CHECKS = {
    "logical_plan_schema_valid", "logical_plan_hashes_valid", "logical_arrays_valid",
    "authority_bindings_valid", "sample_grid_exact", "named_mapping_exact",
    "static_matrices_valid", "latency_alignment_exact", "no_dac_clipping",
    "quantization_error_within_bound", "forward_reconstruction_matches_reference",
    "idle_added_exactly_once", "effective_arrays_finite", "device_limits_satisfied",
    "stage4_compatibility_approval_valid",
}


class Stage41ArtifactError(ValueError):
    """Stable Stage 4.1 artifact/verification error category."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def write_parameterized_control_artifact(compilation: Any, context: Any, output_dir: str | Path) -> Mapping[str, Any]:
    """Publish one accepted point through sibling staging and no-replace rename."""

    if not isinstance(compilation, ParameterizedControlCompilation) or not isinstance(context, ParameterizedControlContext):
        _fail("PLAN_SCHEMA_INVALID", "typed Stage 4.1 compilation and context are required")
    try:
        validate_parameterized_control_context(context)
    except ParameterizedControlError as exc:
        _fail(exc.code.value, exc.detail)
    normalized = _normalize_compilation(compilation, context)
    output_root = Path(_field(context, "output_root")).resolve()
    target = _inside(Path(output_dir), output_root)
    if target.exists():
        _fail("PUBLICATION_CONFLICT", "target already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging.{uuid.uuid4().hex}"
    try:
        staging.mkdir()
        inventory = _write_arrays(staging, normalized["arrays"])
        (staging / INVENTORY_NAME).write_bytes(canonical_json_bytes(inventory))
        inventory_sha = raw_file_sha256(staging / INVENTORY_NAME)
        effective_sha = _effective_sha(inventory)
        control_id = _control_id(normalized, inventory, effective_sha)
        control = {
            "schema_version": "0.1", "artifact_type": "stage_04_1_parameterized_control", "artifact_version": "0.1",
            "control_id": control_id, "point_id": normalized["point_id"], "status": "published",
            "clock": normalized["clock"], "source_binding": normalized["source_binding"],
            "authority_binding": normalized["authority_binding"], "logical_inventory": _rows(inventory, "logical/"),
            "awg": _rows(inventory, "awg/"),
            "effective": {"arrays": _rows(inventory, "effective/"), "effective_control_sha256": effective_sha,
                          "frame_reference_frequency_GHz": normalized["frame_reference_frequency_GHz"]},
            "metrics": normalized["metrics"], "checks": normalized["checks"],
        }
        (staging / CONTROL_NAME).write_bytes(canonical_json_bytes(control))
        (staging / SOURCE_SNAPSHOT_NAME).write_bytes(canonical_json_bytes(normalized["source_snapshot"]))
        (staging / ENVIRONMENT_SNAPSHOT_NAME).write_bytes(canonical_json_bytes(normalized["environment_snapshot"]))
        # This is intentionally an independent implementation in stage4_1_verify.
        from sqvm.control.stage4_1_verify import verify_parameterized_control_staging

        replay = verify_parameterized_control_staging(staging, context)
        manifest = {
            "schema_version": "0.1", "artifact_type": "stage_04_1_parameterized_control_manifest", "artifact_version": "0.1",
            "control_id": control_id, "point_id": normalized["point_id"], "status": "published",
            "payload_files": inventory_tree(staging), "control_sha256": raw_file_sha256(staging / CONTROL_NAME),
            "array_inventory_sha256": inventory_sha, "effective_control_sha256": effective_sha,
        }
        (staging / MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest))
        manifest_sha = raw_file_sha256(staging / MANIFEST_NAME)
        report = {
            "schema_version": "0.1", "artifact_type": "stage_04_1_parameterized_control_verification_report", "artifact_version": "0.1",
            "control_id": control_id, "point_id": normalized["point_id"], "status": "published",
            "ok": True, "checks": replay["checks"], "blocking_reasons": [], "manifest_sha256": manifest_sha,
            "inventory_sha256": inventory_sha, "effective_control_sha256": effective_sha,
        }
        (staging / REPORT_NAME).write_bytes(canonical_json_bytes(report))
        report_sha = raw_file_sha256(staging / REPORT_NAME)
        receipt = {
            "schema_version": "0.1", "artifact_type": "stage_04_1_parameterized_control_receipt", "artifact_version": "0.1",
            "control_id": control_id, "point_id": normalized["point_id"], "status": "published",
            "manifest_sha256": manifest_sha, "verification_report_sha256": report_sha,
            "inventory_sha256": inventory_sha, "effective_control_sha256": effective_sha,
            "source_binding": normalized["source_binding"], "authority_binding": normalized["authority_binding"],
        }
        (staging / RECEIPT_NAME).write_bytes(canonical_json_bytes(receipt))
        receipt_sha = raw_file_sha256(staging / RECEIPT_NAME)
        atomic_publish(staging, target)
        return {"artifact_root": target, "control_id": control_id, "manifest_sha256": manifest_sha,
                "receipt_sha256": receipt_sha, "inventory_sha256": inventory_sha,
                "effective_control_sha256": effective_sha}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _normalize_compilation(compilation: Any, context: Any) -> dict[str, Any]:
    point_id = _field(compilation, "point_id")
    if not isinstance(point_id, str) or not point_id.isascii() or not point_id:
        _fail("PLAN_SCHEMA_INVALID", "point_id")
    if _field(compilation, "status", None) != "compiled_prepublication":
        _fail("PLAN_SCHEMA_INVALID", "only compiled prepublication results publish")
    config = _field(context, "control_chain_config")
    dt = _field(config, "dt_ns")
    if not _finite(dt):
        _fail("CLOCK_MISMATCH", "dt_ns")
    logical = _mapping(_field(compilation, "logical_arrays"), "logical_arrays")
    awg = _mapping(_field(compilation, "awg_arrays"), "awg_arrays")
    effective = _mapping(_field(compilation, "effective_arrays"), "effective_arrays")
    logical_xy = _xy_arrays(logical.get("xy_delta_GHz", logical.get("xy_drive_GHz")), "logical XY")
    has_delta, has_absolute = "flux_delta_phi0" in logical, "flux_absolute_phi0" in logical
    if not has_delta or has_absolute:
        _fail("ARRAY_CONTRACT_INVALID", "logical flux semantics")
    logical_flux = _named_arrays(logical["flux_delta_phi0"], ("q1", "q2", "c"), "logical flux")
    requested, codes, reconstructed, delivered = _awg_arrays(awg)
    effective_xy = _xy_arrays(effective.get("xy_drive_GHz"), "effective XY")
    effective_delta = _named_arrays(effective.get("flux_delta_phi0"), ("q1", "q2", "c"), "effective delta")
    effective_absolute = _named_arrays(effective.get("absolute_flux_phi0"), ("q1", "q2", "c"), "effective absolute")
    n = next(iter(logical_xy.values())).size
    logical_time = _array(logical.get("time_center_ns", (np.arange(n, dtype="<f8") + 0.5) * float(dt)), "logical time", "<f8")
    awg_length = next(iter(requested.values())).size
    awg_time = _array(awg.get("time_center_ns", (np.arange(awg_length, dtype="<f8") + 0.5) * float(dt)), "AWG time", "<f8")
    p = next(iter(effective_xy.values())).size
    effective_time = _array(effective.get("time_center_ns"), "effective time", "<f8")
    n, p = logical_time.size, effective_time.size
    if n == 0 or any(value.size != n for value in (*logical_xy.values(), *logical_flux.values())) or any(value.size != p for value in (*effective_xy.values(), *effective_delta.values(), *effective_absolute.values())):
        _fail("ARRAY_CONTRACT_INVALID", "logical/effective lengths")
    if np.any(np.diff(logical_time) != float(dt)) or np.any(np.diff(awg_time) != float(dt)) or np.any(np.diff(effective_time) != float(dt)):
        _fail("CLOCK_MISMATCH", "sample centers")
    if set(requested) != set(codes) or set(requested) != set(reconstructed) or set(requested) != set(delivered):
        _fail("NAMED_MAPPING_INVALID", "AWG lane names")
    arrays: dict[str, tuple[np.ndarray, str]] = {}
    for name, value in {"time_center_ns": logical_time, **logical_xy,
                        **{f"{mode}_flux_delta": logical_flux[mode] for mode in ("q1", "q2", "c")}}.items():
        arrays[f"logical/{name}"] = (value, "ns" if name == "time_center_ns" else "GHz" if name.endswith(("_i", "_q")) else "Phi/Phi0")
    arrays["awg/time_center_ns"] = (awg_time, "ns")
    for lane in requested:
        arrays[f"awg/{lane}/requested_voltage"] = (requested[lane], "V")
        arrays[f"awg/{lane}/dac_codes"] = (codes[lane], "DAC_code")
        arrays[f"awg/{lane}/reconstructed_voltage"] = (reconstructed[lane], "V")
        arrays[f"awg/{lane}/delivered_voltage"] = (delivered[lane], "V")
    arrays["effective/time_center_ns"] = (effective_time, "ns")
    for mode in ("q1", "q2"):
        arrays[f"effective/{mode}_i"] = (effective_xy[f"{mode}_i"], "GHz")
        arrays[f"effective/{mode}_q"] = (effective_xy[f"{mode}_q"], "GHz")
    for mode in ("q1", "q2", "c"):
        arrays[f"effective/{mode}_flux_delta"] = (effective_delta[mode], "Phi/Phi0")
        arrays[f"effective/{mode}_flux_absolute"] = (effective_absolute[mode], "Phi/Phi0")
    source_snapshot = _mapping(_field(context, "compiler_source_snapshot", {}), "source snapshot")
    environment_snapshot = _mapping(_field(context, "environment_snapshot", {}), "environment snapshot")
    authority_binding = _mapping(_field(compilation, "authority_binding"), "authority binding")
    authority_binding["compiler_source_snapshot_sha256"] = hashlib.sha256(canonical_json_bytes(source_snapshot)).hexdigest().upper()
    authority_binding["environment_snapshot_sha256"] = hashlib.sha256(canonical_json_bytes(environment_snapshot)).hexdigest().upper()
    authority_binding["control_config_sha256"] = raw_file_sha256(context.control_chain_config.source_path)
    authority_binding["channel_registry_sha256"] = raw_file_sha256(context.channel_registry.source_path)
    authority_binding["device_flux_limits_sha256"] = hashlib.sha256(canonical_json_bytes(_plain(context.device_flux_limits_phi0))).hexdigest().upper()
    authority_binding["device_limit_authority_sha256"] = context.device_limit_authority_sha256
    authority_binding["publication_policy_sha256"] = hashlib.sha256(canonical_json_bytes(_plain(context.publication_policy))).hexdigest().upper()
    return {
        "point_id": point_id, "clock": {"dt_ns": float(dt), "logical_sample_count": n, "awg_sample_count": awg_time.size, "effective_sample_count": p},
        "source_binding": _mapping(_field(compilation, "source_binding"), "source binding"), "authority_binding": authority_binding,
        "arrays": arrays, "metrics": _mapping(_field(compilation, "metrics", {}), "metrics"),
        "checks": _checks(_field(compilation, "checks")), "source_snapshot": source_snapshot,
        "environment_snapshot": environment_snapshot,
        "frame_reference_frequency_GHz": _frame(logical),
    }


def _xy_arrays(value: Any, label: str) -> dict[str, np.ndarray]:
    if not isinstance(value, Mapping) or set(value) != {"q1", "q2"}:
        _fail("ARRAY_CONTRACT_INVALID", label)
    result = {}
    for mode in ("q1", "q2"):
        row = value[mode]
        if isinstance(row, Mapping) and set(row) == {"i", "q"}:
            result[f"{mode}_i"], result[f"{mode}_q"] = _array(row["i"], label, "<f8"), _array(row["q"], label, "<f8")
        else:
            complex_row = np.asarray(row)
            if complex_row.dtype != np.dtype("<c16") or complex_row.ndim != 1 or not np.all(np.isfinite(complex_row)):
                _fail("ARRAY_CONTRACT_INVALID", label)
            result[f"{mode}_i"], result[f"{mode}_q"] = np.asarray(complex_row.real, dtype="<f8"), np.asarray(complex_row.imag, dtype="<f8")
    return result


def _named_arrays(value: Any, names: tuple[str, ...], label: str) -> dict[str, np.ndarray]:
    if not isinstance(value, Mapping) or set(value) != set(names):
        _fail("ARRAY_CONTRACT_INVALID", label)
    return {name: _array(value[name], label, "<f8") for name in names}


def _lane_arrays(value: Any, label: str, dtype: str) -> dict[str, np.ndarray]:
    if not isinstance(value, Mapping) or not value or any(not isinstance(name, str) or not name.isascii() for name in value):
        _fail("ARRAY_CONTRACT_INVALID", label)
    return {name: _array(row, label, dtype) for name, row in value.items()}


def _awg_arrays(value: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]:
    lanes = {name: row for name, row in value.items() if name != "time_center_ns"}
    if not lanes or any(not isinstance(name, str) or not isinstance(row, Mapping) for name, row in lanes.items()):
        _fail("ARRAY_CONTRACT_INVALID", "AWG lanes")
    aliases = {
        "requested": ("requested_voltage_V", "requested_voltage"), "codes": ("dac_codes",),
        "reconstructed": ("reconstructed_voltage_V", "reconstructed_voltage"), "delivered": ("delivered_voltage_V", "delivered_voltage"),
    }
    result: dict[str, dict[str, np.ndarray]] = {key: {} for key in aliases}
    for lane, row in lanes.items():
        for name, options in aliases.items():
            selected = next((option for option in options if option in row), None)
            if selected is None:
                _fail("ARRAY_CONTRACT_INVALID", f"AWG {lane}:{name}")
            result[name][lane] = _array(row[selected], f"AWG {lane}:{name}", "<i8" if name == "codes" else "<f8")
    return result["requested"], result["codes"], result["reconstructed"], result["delivered"]


def _array(value: Any, label: str, dtype: str) -> np.ndarray:
    result = np.asarray(value)
    if result.dtype != np.dtype(dtype) or result.ndim != 1 or not result.flags.c_contiguous or (result.dtype.kind == "f" and not np.all(np.isfinite(result))):
        _fail("ARRAY_CONTRACT_INVALID", label)
    return result


def _frame(logical: Mapping[str, Any]) -> dict[str, float]:
    value = logical.get("frame_reference_frequency_GHz")
    if not isinstance(value, Mapping) or set(value) != {"q1", "q2"} or any(not _finite(item) or float(item) <= 0 for item in value.values()):
        _fail("PLAN_SCHEMA_INVALID", "frame reference frequencies")
    return {key: float(value[key]) for key in ("q1", "q2")}


def _write_arrays(root: Path, arrays: Mapping[str, tuple[np.ndarray, str]]) -> dict[str, Any]:
    rows = []
    for name in sorted(arrays):
        value, unit = arrays[name]
        path = root / "arrays" / f"{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value.tobytes(order="C")
        path.write_bytes(raw)
        rows.append({"name": name, "path": path.relative_to(root).as_posix(), "dtype": value.dtype.str, "shape": [int(value.size)], "unit": unit, "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest().upper()})
    return {"schema_version": "0.1", "artifact_type": "stage_04_1_array_inventory", "artifact_version": "0.1", "arrays": rows}


def _rows(inventory: Mapping[str, Any], prefix: str) -> list[dict[str, Any]]:
    return [dict(row) for row in inventory["arrays"] if row["name"].startswith(prefix)]


def _effective_sha(inventory: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes({"arrays": _rows(inventory, "effective/")})).hexdigest().upper()


def _control_id(value: Mapping[str, Any], inventory: Mapping[str, Any], effective_sha: str) -> str:
    content = {"point_id": value["point_id"], "clock": value["clock"], "source_binding": value["source_binding"], "authority_binding": value["authority_binding"], "array_inventory": inventory, "effective_control_sha256": effective_sha}
    return hashlib.sha256(canonical_json_bytes(content)).hexdigest().upper()


def _checks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or not value:
        _fail("PLAN_SCHEMA_INVALID", "checks")
    rows = [dict(item) for item in value if isinstance(item, Mapping)]
    names = [item.get("name") for item in rows]
    if len(rows) != len(value) or set(names) != _REQUIRED_CHECKS or len(names) != len(set(names)) or any(set(item) != {"name", "passed", "reason_code", "evidence_ref"} or item["passed"] is not True for item in rows):
        _fail("PLAN_SCHEMA_INVALID", "checks")
    return rows


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("PLAN_SCHEMA_INVALID", label)
    return {str(key): _plain(item) for key, item in value.items()}


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _field(value: Any, name: str, default: Any = ... ) -> Any:
    if isinstance(value, Mapping):
        if name in value:
            return value[name]
    elif hasattr(value, name):
        return getattr(value, name)
    if default is ...:
        _fail("PLAN_SCHEMA_INVALID", f"missing {name}")
    return default


def _inside(value: Path, root: Path) -> Path:
    candidate = value.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise Stage41ArtifactError("PUBLICATION_CONFLICT", "path is outside output root") from exc
    return candidate


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal)) and math.isfinite(float(value))


def _fail(code: str, detail: str) -> None:
    raise Stage41ArtifactError(code, detail)
