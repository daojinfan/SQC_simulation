"""Independent raw-array replay for Stage 4.1 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from sqvm.control.stage4_1_artifacts import (
    CONTROL_NAME, ENVIRONMENT_SNAPSHOT_NAME, INVENTORY_NAME, MANIFEST_NAME,
    RECEIPT_NAME, REPORT_NAME, SOURCE_SNAPSHOT_NAME, Stage41ArtifactError,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import inventory_tree, inventory_tree_no_follow
from sqvm.control.stage4_1_models import QCISV03LogicalWaveformPlan, ParameterizedControlContext, ParameterizedControlError
from sqvm.control.stage4_1_config import validate_parameterized_control_context
from sqvm.qcis.canonical import sha256_json as qcis_sha256_json


_EXPECTED_CONTROL_CHECKS = {
    "logical_plan_schema_valid", "logical_plan_hashes_valid", "logical_arrays_valid",
    "authority_bindings_valid", "sample_grid_exact", "named_mapping_exact",
    "static_matrices_valid", "latency_alignment_exact", "no_dac_clipping",
    "quantization_error_within_bound", "forward_reconstruction_matches_reference",
    "idle_added_exactly_once", "effective_arrays_finite", "device_limits_satisfied",
    "stage4_compatibility_approval_valid",
}


@dataclass(frozen=True, slots=True)
class VerifiedControlHandle:
    """Process-local capability; it is never serialized back into the artifact."""

    schema_version: str
    control_id: str
    artifact_root: Path
    manifest_sha256: str
    receipt_sha256: str
    inventory_sha256: str
    effective_control_sha256: str
    time_center_ns: np.ndarray
    xy_drive_GHz: Mapping[str, tuple[np.ndarray, np.ndarray]]
    absolute_flux_phi0: Mapping[str, np.ndarray]
    frame_reference_frequency_GHz: Mapping[str, float]
    verification_report: Mapping[str, Any]


def verify_parameterized_control_staging(root: str | Path, context: Any) -> Mapping[str, Any]:
    """Replay an incomplete staging tree before it may be published."""

    if not isinstance(context, ParameterizedControlContext):
        _fail("CONTROL_AUTHORITY_INVALID", "typed Stage 4.1 context is required")
    _validate_context(context)
    artifact = Path(root)
    control, inventory = _canonical(artifact / CONTROL_NAME), _canonical(artifact / INVENTORY_NAME)
    source = _canonical(artifact / SOURCE_SNAPSHOT_NAME)
    environment = _canonical(artifact / ENVIRONMENT_SNAPSHOT_NAME)
    _verify_context_bindings(control, source, environment, context)
    _verify_source_binding(control, inventory)
    _verify_control_payload(control, inventory, context)
    arrays = _arrays(artifact, inventory)
    checks = _replay_checks(arrays, control, context)
    if not all(row["passed"] for row in checks):
        raise Stage41ArtifactError("FORWARD_RECONSTRUCTION_MISMATCH", "staging replay failed")
    return {"ok": True, "checks": checks, "blocking_reasons": []}


def verify_parameterized_control_artifact(artifact_root: str | Path, context: Any, expected_plan: QCISV03LogicalWaveformPlan) -> VerifiedControlHandle:
    """Verify a published control point, then construct a read-only handle."""

    if not isinstance(context, ParameterizedControlContext):
        _fail("CONTROL_AUTHORITY_INVALID", "typed Stage 4.1 context is required")
    if not isinstance(expected_plan, QCISV03LogicalWaveformPlan):
        _fail("PLAN_SCHEMA_INVALID", "an independently admitted QCIS v0.3 plan is required")
    _validate_context(context)
    from .stage4_1_compile import readmit_qcis_v03_plan

    try:
        expected_plan = readmit_qcis_v03_plan(expected_plan, context)
    except ParameterizedControlError as exc:
        _fail(exc.code.value, exc.detail)
    root = _safe_root(artifact_root, context)
    _reject_links(root)
    control, inventory = _canonical(root / CONTROL_NAME), _canonical(root / INVENTORY_NAME)
    manifest, report, receipt = _canonical(root / MANIFEST_NAME), _canonical(root / REPORT_NAME), _canonical(root / RECEIPT_NAME)
    source = _canonical(root / SOURCE_SNAPSHOT_NAME)
    environment = _canonical(root / ENVIRONMENT_SNAPSHOT_NAME)
    if control.get("status") != "published" or report.get("ok") is not True or receipt.get("status") != "published":
        _fail("ARTIFACT_VERIFICATION_FAILED", "publication status")
    _verify_context_bindings(control, source, environment, context)
    _verify_bindings(root, control, inventory, manifest, report, receipt, context)
    _verify_expected_plan(control, expected_plan)
    arrays = _arrays(root, inventory)
    checks = _replay_checks(arrays, control, context)
    if not all(row["passed"] for row in checks):
        _fail("FORWARD_RECONSTRUCTION_MISMATCH", "post-publication replay")
    if report.get("checks") != checks or report.get("blocking_reasons") != []:
        _fail("ARTIFACT_VERIFICATION_FAILED", "verification report does not match independent replay")
    effective = arrays["effective"]
    frame = control.get("effective", {}).get("frame_reference_frequency_GHz")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"}:
        _fail("PLAN_SCHEMA_INVALID", "frame references")
    return VerifiedControlHandle(
        schema_version="0.1", control_id=control["control_id"], artifact_root=root, manifest_sha256=raw_file_sha256(root / MANIFEST_NAME),
        receipt_sha256=raw_file_sha256(root / RECEIPT_NAME), inventory_sha256=raw_file_sha256(root / INVENTORY_NAME),
        effective_control_sha256=control["effective"]["effective_control_sha256"], time_center_ns=_readonly(effective["time_center_ns"]),
        xy_drive_GHz=MappingProxyType({mode: (_readonly(effective[f"{mode}_i"]), _readonly(effective[f"{mode}_q"])) for mode in ("q1", "q2")}),
        absolute_flux_phi0=MappingProxyType({mode: _readonly(effective[f"{mode}_flux_absolute"]) for mode in ("q1", "q2", "c")}),
        frame_reference_frequency_GHz=MappingProxyType({mode: float(frame[mode]) for mode in ("q1", "q2")}),
        verification_report=_freeze_json(report),
    )


def _replay_checks(arrays: Mapping[str, Any], control: Mapping[str, Any], context: Any) -> list[dict[str, Any]]:
    config = _field(context, "control_chain_config")
    expected_awg, expected_effective = independent_electronics_replay(arrays["logical"], config)
    checks = [
        _check("sample_grid_exact", _equal_maps(arrays["awg"], expected_awg, "time_center_ns")),
        _check("forward_reconstruction_matches_reference", _equal_maps(arrays["effective"], expected_effective, "time_center_ns")),
        _check("idle_added_exactly_once", _idle_once(arrays["effective"], _field(config, "idle_flux_phi0"))),
        _check("effective_arrays_finite", all(np.all(np.isfinite(value)) for value in arrays["effective"].values())),
        _check("device_limits_satisfied", _limits(arrays["logical"], arrays["effective"], _field(config, "idle_flux_phi0"), _field(context, "device_flux_limits_phi0", None))),
    ]
    return checks


def independent_electronics_replay(logical: Mapping[str, np.ndarray], config: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Standalone electronics replay; it intentionally does not call the compiler."""

    common = {"time_center_ns", "q1_i", "q1_q", "q2_i", "q2_q"}
    delta_names = {f"{mode}_flux_delta" for mode in ("q1", "q2", "c")}
    absolute_names = {f"{mode}_flux_absolute" for mode in ("q1", "q2", "c")}
    if set(logical) != common | delta_names and set(logical) != common | absolute_names:
        _fail("ARRAY_CONTRACT_INVALID", "logical inventory")
    dt, lanes, mixing, idle, dac = _field(config, "dt_ns"), _field(config, "lanes"), _field(config, "static_mixing"), _field(config, "idle_flux_phi0"), _field(config, "dac")
    n = logical["time_center_ns"].size
    if n == 0 or not _finite(dt) or any(value.ndim != 1 or value.size != n for value in logical.values()):
        _fail("CLOCK_MISMATCH", "logical grid")
    expected_logical_time = (np.arange(n, dtype="<f8") + 0.5) * float(dt)
    if not np.array_equal(logical["time_center_ns"], expected_logical_time):
        _fail("CLOCK_MISMATCH", "logical time origin")
    uses_absolute = absolute_names.issubset(logical)
    groups = {"xy": ("q1_i", "q1_q", "q2_i", "q2_q"), "z": ("q1_flux_delta", "q2_flux_delta", "c_flux_delta")}
    descriptions: dict[str, tuple[list[str], np.ndarray]] = {}
    required_lanes: set[str] = set()
    for group, coordinates in groups.items():
        row = _field(mixing, group)
        lane_names = list(_field(row, "input_lanes"))
        output_names = tuple(_canon(item) for item in _field(row, "output_coordinates"))
        matrix = np.asarray(_field(row, "matrix"), dtype=float)
        if output_names != coordinates or matrix.shape != (len(coordinates), len(lane_names)) or np.linalg.matrix_rank(matrix) != len(coordinates) or not np.all(np.isfinite(matrix)) or np.linalg.cond(matrix) > 100.0:
            _fail("MIXING_MATRIX_INVALID", group)
        descriptions[group] = (lane_names, matrix)
        required_lanes.update(lane_names)
    if not required_lanes.issubset(set(lanes)):
        _fail("NAMED_MAPPING_INVALID", "lane registry")
    lmax = max(_latency(_field(lanes, lane)) for lane in required_lanes)
    fmax = max(len(_fir(_field(lanes, lane))) for lane in required_lanes)
    n_awg, p_count = n + lmax, n + lmax + fmax - 1 + lmax
    requested = {lane: np.zeros(n_awg, dtype="<f8") for lane in required_lanes}
    for group, coordinates in groups.items():
        lane_names, matrix = descriptions[group]
        if group == "z" and uses_absolute:
            desired = np.vstack([logical[f"{mode}_flux_absolute"] for mode in ("q1", "q2", "c")])
            desired -= np.asarray([float(_field(idle, mode)) for mode in ("q1", "q2", "c")])[:, None]
        else:
            desired = np.vstack([logical[name] for name in coordinates])
        try:
            solved = np.linalg.solve(matrix, desired)
        except np.linalg.LinAlgError as exc:
            raise Stage41ArtifactError("MIXING_MATRIX_INVALID", group) from exc
        for index, lane in enumerate(lane_names):
            offset = lmax - _latency(_field(lanes, lane))
            requested[lane][offset:offset + n] = solved[index]
    awg: dict[str, Any] = {"time_center_ns": (np.arange(n_awg, dtype="<f8") - lmax + 0.5) * float(dt)}
    delivered: dict[str, np.ndarray] = {}
    for lane, values in requested.items():
        codes, reconstructed = _quantize(values, dac)
        full = np.convolve(reconstructed, _fir(_field(lanes, lane)), mode="full")
        row = np.zeros(p_count, dtype="<f8")
        latency = _latency(_field(lanes, lane))
        row[latency:latency + full.size] = full
        delivered[lane] = row
        awg[lane] = {"requested_voltage": values, "dac_codes": codes, "reconstructed_voltage": reconstructed, "delivered_voltage": row}
    effective: dict[str, np.ndarray] = {"time_center_ns": (np.arange(p_count, dtype="<f8") - lmax + 0.5) * float(dt)}
    for group, coordinates in groups.items():
        lane_names, matrix = descriptions[group]
        output = matrix @ np.vstack([delivered[lane] for lane in lane_names])
        for index, name in enumerate(coordinates):
            effective[name] = np.asarray(output[index], dtype="<f8")
    for mode in ("q1", "q2", "c"):
        effective[f"{mode}_flux_absolute"] = np.asarray(effective[f"{mode}_flux_delta"] + float(_field(idle, mode)), dtype="<f8")
    return awg, effective


def _arrays(root: Path, inventory: Mapping[str, Any]) -> dict[str, Any]:
    if set(inventory) != {"schema_version", "artifact_type", "artifact_version", "arrays"} or inventory.get("schema_version") != "0.1" or inventory.get("artifact_type") != "stage_04_1_array_inventory" or inventory.get("artifact_version") != "0.1":
        _fail("ARRAY_CONTRACT_INVALID", "inventory envelope")
    rows = inventory.get("arrays")
    if not isinstance(rows, list):
        _fail("ARRAY_CONTRACT_INVALID", "inventory")
    result: dict[str, Any] = {"logical": {}, "awg": {}, "effective": {}}
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"name", "path", "dtype", "shape", "unit", "byte_length", "sha256"}:
            _fail("ARRAY_CONTRACT_INVALID", "inventory row")
        name, relative = row["name"], row["path"]
        expected_dtype = "<i8" if str(name).endswith("/dac_codes") else "<f8"
        expected_unit = _array_unit(str(name))
        if not isinstance(name, str) or name in seen_names or relative in seen_paths or relative != f"arrays/{name}.bin":
            _fail("ARRAY_CONTRACT_INVALID", "duplicate or non-canonical array path")
        if row["dtype"] != expected_dtype or row["unit"] != expected_unit or not isinstance(row["shape"], list) or len(row["shape"]) != 1 or type(row["shape"][0]) is not int or row["shape"][0] <= 0:
            _fail("ARRAY_CONTRACT_INVALID", name)
        if type(row["byte_length"]) is not int or row["byte_length"] != row["shape"][0] * np.dtype(expected_dtype).itemsize:
            _fail("ARRAY_CONTRACT_INVALID", name)
        seen_names.add(name)
        seen_paths.add(relative)
        path = _file(root, row["path"])
        raw = path.read_bytes()
        if len(raw) != row["byte_length"] or hashlib.sha256(raw).hexdigest().upper() != row["sha256"]:
            _fail("PLAN_HASH_MISMATCH", row["name"])
        array = np.frombuffer(raw, dtype=np.dtype(row["dtype"])).copy()
        if array.size != row["shape"][0] or (array.dtype.kind == "f" and not np.all(np.isfinite(array))):
            _fail("ARRAY_CONTRACT_INVALID", row["name"])
        parts = str(row["name"]).split("/")
        if parts == ["awg", "time_center_ns"]:
            result["awg"]["time_center_ns"] = array
        elif parts[0] == "awg" and len(parts) == 3:
            result["awg"].setdefault(parts[1], {})[parts[2]] = array
        elif parts[0] in {"logical", "effective"} and len(parts) == 2:
            result[parts[0]][parts[1]] = array
        else:
            _fail("ARRAY_CONTRACT_INVALID", row["name"])
    if [row["name"] for row in rows] != sorted(seen_names) or set(result["logical"]) != _logical_names() or set(result["effective"]) != _effective_names() or set(result["awg"]) != _awg_names():
        _fail("ARRAY_CONTRACT_INVALID", "array name set")
    return result


def _verify_bindings(root: Path, control: Mapping[str, Any], inventory: Mapping[str, Any], manifest: Mapping[str, Any], report: Mapping[str, Any], receipt: Mapping[str, Any], context: ParameterizedControlContext) -> None:
    _verify_envelopes(control, manifest, report, receipt)
    _verify_source_binding(control, inventory)
    _verify_control_payload(control, inventory, context)
    if manifest.get("control_id") != control.get("control_id") or receipt.get("control_id") != control.get("control_id") or report.get("control_id") != control.get("control_id"):
        _fail("PLAN_HASH_MISMATCH", "control_id")
    if len({control.get("point_id"), manifest.get("point_id"), report.get("point_id"), receipt.get("point_id")}) != 1:
        _fail("PLAN_HASH_MISMATCH", "point_id")
    if manifest.get("array_inventory_sha256") != raw_file_sha256(root / INVENTORY_NAME) or manifest.get("control_sha256") != raw_file_sha256(root / CONTROL_NAME):
        _fail("PLAN_HASH_MISMATCH", "manifest hashes")
    if receipt.get("manifest_sha256") != raw_file_sha256(root / MANIFEST_NAME) or report.get("manifest_sha256") != raw_file_sha256(root / MANIFEST_NAME) or receipt.get("verification_report_sha256") != raw_file_sha256(root / REPORT_NAME):
        _fail("PLAN_HASH_MISMATCH", "receipt hashes")
    effective_rows = [row for row in inventory["arrays"] if row["name"].startswith("effective/")]
    effective_sha = hashlib.sha256(canonical_json_bytes({"arrays": effective_rows})).hexdigest().upper()
    inventory_sha = raw_file_sha256(root / INVENTORY_NAME)
    if any(value != effective_sha for value in (control.get("effective", {}).get("effective_control_sha256"), manifest.get("effective_control_sha256"), report.get("effective_control_sha256"), receipt.get("effective_control_sha256"))):
        _fail("PLAN_HASH_MISMATCH", "effective control hash")
    if any(value != inventory_sha for value in (manifest.get("array_inventory_sha256"), report.get("inventory_sha256"), receipt.get("inventory_sha256"))):
        _fail("PLAN_HASH_MISMATCH", "inventory hash")
    control_content = {"point_id": control["point_id"], "clock": control["clock"], "source_binding": control["source_binding"], "authority_binding": control["authority_binding"], "array_inventory": inventory, "effective_control_sha256": effective_sha}
    expected_control_id = hashlib.sha256(canonical_json_bytes(control_content)).hexdigest().upper()
    if control.get("control_id") != expected_control_id:
        _fail("PLAN_HASH_MISMATCH", "content-derived control_id")
    if receipt.get("source_binding") != control.get("source_binding") or receipt.get("authority_binding") != control.get("authority_binding"):
        _fail("PLAN_HASH_MISMATCH", "receipt bindings")
    actual = [row for row in inventory_tree(root) if row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}]
    if manifest.get("payload_files") != actual:
        _fail("ARTIFACT_VERIFICATION_FAILED", "payload inventory")
    expected_paths = {CONTROL_NAME, INVENTORY_NAME, SOURCE_SNAPSHOT_NAME, ENVIRONMENT_SNAPSHOT_NAME, *(row["path"] for row in inventory["arrays"])}
    if {row["path"] for row in actual} != expected_paths:
        _fail("ARTIFACT_VERIFICATION_FAILED", "unexpected payload file")


def _verify_source_binding(control: Mapping[str, Any], inventory: Mapping[str, Any]) -> None:
    source = control.get("source_binding")
    fields = {"point_id", "concrete_source_sha256", "ast_sha256", "trace_sha256", "logical_array_inventory", "logical_event_inventory", "logical_event_inventory_sha256"}
    if not isinstance(source, Mapping) or set(source) != fields or source.get("point_id") != control.get("point_id"):
        _fail("PLAN_HASH_MISMATCH", "source binding fields")
    if any(not _sha256(source.get(name)) for name in ("concrete_source_sha256", "ast_sha256", "trace_sha256")):
        _fail("PLAN_HASH_MISMATCH", "source binding hashes")
    upstream = source.get("logical_array_inventory")
    if not isinstance(upstream, Mapping) or not isinstance(source.get("logical_event_inventory"), list):
        _fail("PLAN_HASH_MISMATCH", "logical inventory binding")
    event_sha = qcis_sha256_json(source["logical_event_inventory"])
    if source.get("logical_event_inventory_sha256") != event_sha:
        _fail("PLAN_HASH_MISMATCH", "logical event inventory hash")
    names = {
        "logical.xy_delta_GHz.q1.i": "logical/q1_i",
        "logical.xy_delta_GHz.q1.q": "logical/q1_q",
        "logical.xy_delta_GHz.q2.i": "logical/q2_i",
        "logical.xy_delta_GHz.q2.q": "logical/q2_q",
        "logical.flux_delta_phi0.q1": "logical/q1_flux_delta",
        "logical.flux_delta_phi0.q2": "logical/q2_flux_delta",
        "logical.flux_delta_phi0.c": "logical/c_flux_delta",
    }
    if set(upstream) != set(names):
        _fail("PLAN_HASH_MISMATCH", "logical inventory names")
    published = {row["name"]: row for row in inventory["arrays"]}
    for upstream_name, artifact_name in names.items():
        row, actual = upstream[upstream_name], published.get(artifact_name)
        if not isinstance(row, Mapping) or actual is None:
            _fail("PLAN_HASH_MISMATCH", upstream_name)
        expected_unit = "GHz" if ".xy_delta_" in upstream_name else "Phi/Phi0"
        if row.get("name") != upstream_name or row.get("dtype") != "<f8" or row.get("shape") != actual["shape"] or row.get("unit") != expected_unit or row.get("byte_length") != actual["byte_length"] or row.get("sha256") != actual["sha256"]:
            _fail("PLAN_HASH_MISMATCH", upstream_name)


def _verify_control_payload(control: Mapping[str, Any], inventory: Mapping[str, Any], context: Any) -> None:
    rows = inventory["arrays"]
    logical = [row for row in rows if row["name"].startswith("logical/")]
    awg = [row for row in rows if row["name"].startswith("awg/")]
    effective = [row for row in rows if row["name"].startswith("effective/")]
    effective_payload = control.get("effective")
    if control.get("logical_inventory") != logical or control.get("awg") != awg or not isinstance(effective_payload, Mapping) or set(effective_payload) != {"arrays", "effective_control_sha256", "frame_reference_frequency_GHz"} or effective_payload.get("arrays") != effective:
        _fail("PLAN_HASH_MISMATCH", "control array inventories")
    frame = effective_payload.get("frame_reference_frequency_GHz")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"} or any(not _finite(value) or float(value) <= 0.0 for value in frame.values()):
        _fail("PLAN_SCHEMA_INVALID", "frame reference frequencies")
    clock = control.get("clock")
    by_name = {row["name"]: row for row in rows}
    if not isinstance(clock, Mapping) or set(clock) != {"dt_ns", "logical_sample_count", "awg_sample_count", "effective_sample_count"} or clock.get("dt_ns") != float(_field(_field(context, "control_chain_config"), "dt_ns")):
        _fail("CLOCK_MISMATCH", "control clock")
    expected_counts = {
        "logical_sample_count": by_name["logical/time_center_ns"]["shape"][0],
        "awg_sample_count": by_name["awg/time_center_ns"]["shape"][0],
        "effective_sample_count": by_name["effective/time_center_ns"]["shape"][0],
    }
    if any(clock.get(name) != count for name, count in expected_counts.items()):
        _fail("CLOCK_MISMATCH", "control sample counts")
    _verify_event_inventory(control["source_binding"]["logical_event_inventory"], frame, expected_counts["logical_sample_count"])
    checks = control.get("checks")
    if not isinstance(checks, list) or {row.get("name") for row in checks if isinstance(row, Mapping)} != _EXPECTED_CONTROL_CHECKS or len(checks) != len(_EXPECTED_CONTROL_CHECKS) or any(not isinstance(row, Mapping) or set(row) != {"name", "passed", "reason_code", "evidence_ref"} or row.get("passed") is not True for row in checks):
        _fail("ARTIFACT_VERIFICATION_FAILED", "control checks")


def _verify_event_inventory(events: Any, frame: Mapping[str, Any], sample_count: int) -> None:
    keys = {"event_id", "source_instruction_index", "target", "transition", "actual_start_sample", "sample_count", "phase_rule_id", "phase_total_rad", "f_drive_GHz", "f_ref_GHz", "detuning_GHz", "logical_array_contribution_sha256", "setting_evidence"}
    if not isinstance(events, list):
        _fail("PLAN_HASH_MISMATCH", "logical event inventory")
    seen: set[str] = set()
    for event in events:
        if not isinstance(event, Mapping) or set(event) != keys:
            _fail("PLAN_HASH_MISMATCH", "logical event fields")
        event_id, target = event["event_id"], event["target"]
        component = {"Q1": "q1", "Q2": "q2"}.get(target)
        start, count = event["actual_start_sample"], event["sample_count"]
        if not isinstance(event_id, str) or not event_id or event_id in seen or component is None or type(start) is not int or type(count) is not int or start < 0 or count <= 0 or start + count > sample_count:
            _fail("PLAN_HASH_MISMATCH", "logical event identity or interval")
        if type(event["source_instruction_index"]) is not int or event["source_instruction_index"] < 0 or event["transition"] not in {"01", "12"} or event["phase_rule_id"] != "qcis_v03_absolute_detuning_phase_v1":
            _fail("PLAN_HASH_MISMATCH", "logical event metadata")
        if any(not _finite(event[name]) for name in ("phase_total_rad", "f_drive_GHz", "f_ref_GHz", "detuning_GHz")):
            _fail("PLAN_HASH_MISMATCH", "logical event numeric metadata")
        if float(event["f_ref_GHz"]) != float(frame[component]) or not math.isclose(float(event["f_drive_GHz"]) - float(event["f_ref_GHz"]), float(event["detuning_GHz"]), rel_tol=0.0, abs_tol=1e-12):
            _fail("PLAN_HASH_MISMATCH", "logical event frame or detuning")
        if not _sha256(event["logical_array_contribution_sha256"]):
            _fail("PLAN_HASH_MISMATCH", "logical event contribution hash")
        setting = event["setting_evidence"]
        if setting is not None and (not isinstance(setting, Mapping) or set(setting) != {"setting_id", "revision", "setting_hash", "calibration_run_id"} or not isinstance(setting["setting_id"], str) or not setting["setting_id"] or type(setting["revision"]) is not int or setting["revision"] <= 0 or not _sha256(setting["setting_hash"]) or not isinstance(setting["calibration_run_id"], str) or not setting["calibration_run_id"]):
            _fail("PLAN_HASH_MISMATCH", "logical event setting evidence")
        seen.add(event_id)


def _verify_context_bindings(control: Mapping[str, Any], source: Mapping[str, Any], environment: Mapping[str, Any], context: Any) -> None:
    authority = control.get("authority_binding")
    expected_fields = {"plan_authority_sha256", "frame_reference_authority_sha256", "control_authority_sha256", "compiler_source_snapshot_sha256", "environment_snapshot_sha256", "control_config_sha256", "channel_registry_sha256", "device_flux_limits_sha256", "device_limit_authority_sha256", "publication_policy_sha256"}
    if not isinstance(authority, Mapping) or set(authority) != expected_fields:
        _fail("PLAN_AUTHORITY_MISMATCH", "authority binding fields")
    if authority["plan_authority_sha256"] != _plain(_field(context, "expected_plan_authority_sha256")) or authority["control_authority_sha256"] != _plain(_field(context, "authority_sha256")):
        _fail("PLAN_AUTHORITY_MISMATCH", "bound authority differs from context")
    frame = authority["frame_reference_authority_sha256"]
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"} or any(not _sha256(value) for value in frame.values()):
        _fail("PLAN_AUTHORITY_MISMATCH", "frame reference authority")
    if source != _plain(_field(context, "compiler_source_snapshot", {})) or environment != _plain(_field(context, "environment_snapshot", {})):
        _fail("PLAN_AUTHORITY_MISMATCH", "source or environment snapshot")
    source_sha = hashlib.sha256(canonical_json_bytes(source)).hexdigest().upper()
    environment_sha = hashlib.sha256(canonical_json_bytes(environment)).hexdigest().upper()
    if authority["compiler_source_snapshot_sha256"] != source_sha or authority["environment_snapshot_sha256"] != environment_sha:
        _fail("PLAN_AUTHORITY_MISMATCH", "source or environment snapshot hash")
    expected_content = {
        "control_config_sha256": raw_file_sha256(context.control_chain_config.source_path),
        "channel_registry_sha256": raw_file_sha256(context.channel_registry.source_path),
        "device_flux_limits_sha256": hashlib.sha256(canonical_json_bytes(_plain(context.device_flux_limits_phi0))).hexdigest().upper(),
        "device_limit_authority_sha256": context.device_limit_authority_sha256,
        "publication_policy_sha256": hashlib.sha256(canonical_json_bytes(_plain(context.publication_policy))).hexdigest().upper(),
    }
    if any(authority[name] != digest for name, digest in expected_content.items()):
        _fail("PLAN_AUTHORITY_MISMATCH", "control context content hash")


def _verify_expected_plan(control: Mapping[str, Any], plan: QCISV03LogicalWaveformPlan) -> None:
    expected_inventory = {
        name: {"name": row.name, "dtype": row.dtype, "shape": list(row.shape), "unit": row.unit, "byte_length": row.byte_length, "sha256": row.sha256}
        for name, row in plan.array_inventory.items()
    }
    expected_source = {
        "point_id": plan.point_id,
        "concrete_source_sha256": plan.concrete_source_sha256,
        "ast_sha256": plan.ast_sha256,
        "trace_sha256": plan.trace_sha256,
        "logical_array_inventory": expected_inventory,
        "logical_event_inventory": [_plain(event) for event in plan.drive_event_inventory],
        "logical_event_inventory_sha256": plan.drive_event_inventory_sha256,
    }
    authority = control.get("authority_binding", {})
    effective = control.get("effective", {})
    if control.get("source_binding") != expected_source or authority.get("plan_authority_sha256") != _plain(plan.authority_sha256) or authority.get("frame_reference_authority_sha256") != _plain(plan.frame_reference_authority_sha256) or effective.get("frame_reference_frequency_GHz") != _plain(plan.frame_reference_frequency_GHz):
        _fail("PLAN_AUTHORITY_MISMATCH", "artifact differs from the independently admitted plan")


def _verify_envelopes(control: Mapping[str, Any], manifest: Mapping[str, Any], report: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    contracts = (
        (control, {"schema_version", "artifact_type", "artifact_version", "control_id", "point_id", "status", "clock", "source_binding", "authority_binding", "logical_inventory", "awg", "effective", "metrics", "checks"}, "stage_04_1_parameterized_control"),
        (manifest, {"schema_version", "artifact_type", "artifact_version", "control_id", "point_id", "status", "payload_files", "control_sha256", "array_inventory_sha256", "effective_control_sha256"}, "stage_04_1_parameterized_control_manifest"),
        (report, {"schema_version", "artifact_type", "artifact_version", "control_id", "point_id", "status", "ok", "checks", "blocking_reasons", "manifest_sha256", "inventory_sha256", "effective_control_sha256"}, "stage_04_1_parameterized_control_verification_report"),
        (receipt, {"schema_version", "artifact_type", "artifact_version", "control_id", "point_id", "status", "manifest_sha256", "verification_report_sha256", "inventory_sha256", "effective_control_sha256", "source_binding", "authority_binding"}, "stage_04_1_parameterized_control_receipt"),
    )
    for value, fields, artifact_type in contracts:
        if set(value) != fields or value.get("schema_version") != "0.1" or value.get("artifact_version") != "0.1" or value.get("artifact_type") != artifact_type or value.get("status") != "published":
            _fail("ARTIFACT_VERIFICATION_FAILED", artifact_type)


def _logical_names() -> set[str]:
    return {"time_center_ns", "q1_i", "q1_q", "q2_i", "q2_q", "q1_flux_delta", "q2_flux_delta", "c_flux_delta"}


def _effective_names() -> set[str]:
    return {"time_center_ns", "q1_i", "q1_q", "q2_i", "q2_q", "q1_flux_delta", "q2_flux_delta", "c_flux_delta", "q1_flux_absolute", "q2_flux_absolute", "c_flux_absolute"}


def _awg_names() -> set[str]:
    lanes = {"q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q", "q1_z", "q2_z", "c_z"}
    return {"time_center_ns", *lanes}


def _array_unit(name: str) -> str:
    if name.endswith("time_center_ns"):
        return "ns"
    if name.endswith("/dac_codes"):
        return "DAC_code"
    if "/awg/" in f"/{name}/" or name.startswith("awg/"):
        return "V"
    if name.endswith(("_i", "_q")):
        return "GHz"
    if "flux_" in name:
        return "Phi/Phi0"
    _fail("ARRAY_CONTRACT_INVALID", f"unknown array unit:{name}")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789ABCDEF" for character in value)


def _equal_maps(actual: Mapping[str, Any], expected: Mapping[str, Any], time_name: str) -> bool:
    if set(actual) != set(expected):
        return False
    for key in expected:
        if isinstance(expected[key], Mapping):
            if not isinstance(actual[key], Mapping) or not _equal_maps(actual[key], expected[key], time_name):
                return False
        elif not np.array_equal(actual[key], expected[key]):
            return False
    return True


def _idle_once(effective: Mapping[str, np.ndarray], idle: Any) -> bool:
    return all(np.array_equal(effective[f"{mode}_flux_absolute"] - effective[f"{mode}_flux_delta"], np.full(effective[f"{mode}_flux_delta"].size, float(_field(idle, mode)), dtype="<f8")) for mode in ("q1", "q2", "c"))


def _limits(logical: Mapping[str, np.ndarray], effective: Mapping[str, np.ndarray], idle: Any, limits: Any) -> bool:
    if limits is None:
        return True
    if not isinstance(limits, Mapping) or set(limits) != {"q1", "q2", "c"}:
        return False
    for mode in limits:
        if not isinstance(limits[mode], (tuple, list)) or len(limits[mode]) != 2:
            return False
        lower, upper = limits[mode]
        logical_absolute = logical[f"{mode}_flux_delta"] + float(_field(idle, mode))
        if not np.all((logical_absolute >= lower) & (logical_absolute <= upper)) or not np.all((effective[f"{mode}_flux_absolute"] >= lower) & (effective[f"{mode}_flux_absolute"] <= upper)):
            return False
    return True


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "reason_code": "OK" if passed else "FORWARD_RECONSTRUCTION_MISMATCH", "evidence_ref": "independent_raw_replay"}


def _quantize(values: np.ndarray, dac: Any) -> tuple[np.ndarray, np.ndarray]:
    lsb = Decimal(str(_field(dac, "lsb_V")))
    lower = Decimal(str(_field(dac, "full_scale_min_V")))
    minimum, maximum = int(_field(dac, "code_min")), int(_field(dac, "code_max"))
    codes = np.empty(values.size, dtype="<i8")
    for index, value in enumerate(values):
        level = int(
            ((Decimal.from_float(float(value)) - lower) / lsb).to_integral_value(
                rounding=ROUND_HALF_EVEN
            )
        )
        code = minimum + level
        if code < minimum or code > maximum:
            _fail("DAC_RANGE_EXCEEDED", str(index))
        codes[index] = code
    return codes, np.asarray(
        [float(lower + Decimal(int(code) - minimum) * lsb) for code in codes],
        dtype="<f8",
    )


def _latency(row: Any) -> int:
    value = _field(row, "latency_samples")
    if type(value) is not int or value < 0:
        _fail("LATENCY_CONTRACT_INVALID", "latency")
    return value


def _fir(row: Any) -> np.ndarray:
    value = _field(row, "fir")
    if not isinstance(value, (list, tuple)) or not value or not all(_finite(item) for item in value):
        _fail("LATENCY_CONTRACT_INVALID", "FIR")
    return np.asarray(value, dtype="<f8")


def _canon(name: Any) -> str:
    return {"q1_drive_i_GHz": "q1_i", "q1_drive_q_GHz": "q1_q", "q2_drive_i_GHz": "q2_i", "q2_drive_q_GHz": "q2_q", "q1_delta_flux_phi0": "q1_flux_delta", "q2_delta_flux_phi0": "q2_flux_delta", "c_delta_flux_phi0": "c_flux_delta"}.get(str(name), str(name))


def _safe_root(value: str | Path, context: Any) -> Path:
    raw, bound = Path(value), Path(_field(context, "output_root")).resolve()
    if _is_link(raw) or not raw.is_dir() or raw.name.startswith(".") or ".staging." in raw.name:
        _fail("ARTIFACT_VERIFICATION_FAILED", "artifact root")
    root = raw.resolve()
    try:
        root.relative_to(bound)
    except ValueError as exc:
        raise Stage41ArtifactError("ARTIFACT_VERIFICATION_FAILED", "outside output root") from exc
    return root


def _validate_context(context: ParameterizedControlContext) -> None:
    try:
        validate_parameterized_control_context(context)
    except ParameterizedControlError as exc:
        _fail(exc.code.value, exc.detail)


def _canonical(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail("ARTIFACT_VERIFICATION_FAILED", str(path.name))
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage41ArtifactError("ARTIFACT_VERIFICATION_FAILED", path.name) from exc
    if not isinstance(value, dict) or path.read_bytes() != canonical_json_bytes(value):
        _fail("ARTIFACT_VERIFICATION_FAILED", f"canonical:{path.name}")
    return value


def _file(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        _fail("ARTIFACT_VERIFICATION_FAILED", "array path")
    path = root.joinpath(*Path(relative).parts)
    if _is_link(path) or not path.is_file():
        _fail("ARTIFACT_VERIFICATION_FAILED", relative)
    return path


def _reject_links(root: Path) -> None:
    if any(row["entry_type"] in {"link", "other"} for row in inventory_tree_no_follow(root)):
        _fail("ARTIFACT_VERIFICATION_FAILED", "symlink")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _readonly(value: np.ndarray) -> np.ndarray:
    result = np.asarray(value, dtype="<f8").copy(order="C")
    result.setflags(write=False)
    return result


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _field(value: Any, name: str, default: Any = ... ) -> Any:
    if isinstance(value, Mapping) and name in value:
        return value[name]
    if hasattr(value, name):
        return getattr(value, name)
    if default is ...:
        _fail("PLAN_SCHEMA_INVALID", f"missing {name}")
    return default


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal)) and math.isfinite(float(value))


def _fail(code: str, detail: str) -> None:
    raise Stage41ArtifactError(code, detail)
