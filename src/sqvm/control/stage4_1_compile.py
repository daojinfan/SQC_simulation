"""Pure Stage 4.1 parameterized electronics compilation for QCIS v0.3 plans."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any

import numpy as np

from sqvm.control.stage4_config import GROUP_CONTRACT, LANE_ORDER
from sqvm.qcis.canonical import sha256_json
from .stage4_1_models import (
    LogicalArrayInventoryRow,
    ParameterizedControlCompilation,
    ParameterizedControlContext,
    ParameterizedControlError,
    ParameterizedControlReasonCode,
    QCISV03LogicalWaveformPlan,
    freeze_array,
    freeze_mapping,
)


_HASH = re.compile(r"^[0-9A-F]{64}$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ARRAYS = {
    "logical.xy_delta_GHz.q1.i": ("xy_delta_GHz", "q1", "i", "GHz"),
    "logical.xy_delta_GHz.q1.q": ("xy_delta_GHz", "q1", "q", "GHz"),
    "logical.xy_delta_GHz.q2.i": ("xy_delta_GHz", "q2", "i", "GHz"),
    "logical.xy_delta_GHz.q2.q": ("xy_delta_GHz", "q2", "q", "GHz"),
    "logical.flux_delta_phi0.q1": ("flux_delta_phi0", "q1", None, "Phi/Phi0"),
    "logical.flux_delta_phi0.q2": ("flux_delta_phi0", "q2", None, "Phi/Phi0"),
    "logical.flux_delta_phi0.c": ("flux_delta_phi0", "c", None, "Phi/Phi0"),
}
_TOP_LEVEL = {
    "schema_version", "profile_id", "point_id", "concrete_source_sha256", "ast_sha256", "trace_sha256",
    "sample_count", "dt_ns", "logical", "frame_reference_frequency_GHz", "frame_reference_authority_sha256",
    "array_inventory", "drive_event_inventory", "drive_event_inventory_sha256", "authority_sha256",
}
_EVENT_KEYS = {
    "event_id", "source_instruction_index", "target", "transition", "actual_start_sample", "sample_count",
    "phase_rule_id", "phase_total_rad", "f_drive_GHz", "f_ref_GHz", "detuning_GHz",
    "logical_array_contribution_sha256", "setting_evidence",
}


def compile_qcis_waveform_plan(
    plan: QCISV03LogicalWaveformPlan, context: ParameterizedControlContext
) -> ParameterizedControlCompilation:
    """Compile one admitted QCIS v0.3 logical plan without publishing an artifact."""

    if not isinstance(context, ParameterizedControlContext):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "ParameterizedControlContext is required")
    if not isinstance(plan, QCISV03LogicalWaveformPlan):
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "compile_qcis_waveform_plan requires a typed v0.3 plan")
    from .stage4_1_config import validate_parameterized_control_context

    validate_parameterized_control_context(context)
    logical = readmit_qcis_v03_plan(plan, context)
    config = context.control_chain_config
    _validate_matrices(config)
    n = logical.sample_count
    desired_xy = np.column_stack((logical.xy_q1_i, logical.xy_q1_q, logical.xy_q2_i, logical.xy_q2_q))
    desired_z = np.column_stack((logical.flux_q1, logical.flux_q2, logical.flux_c))
    idle = np.asarray([float(config.idle_flux_phi0[name]) for name in ("q1", "q2", "c")], dtype="<f8")
    logical_absolute = desired_z + idle
    _check_device_limits(logical_absolute, context, "logical aggregate")

    desired = {"xy": desired_xy, "z": desired_z}
    solved = {group: _solve(config.static_mixing[group]["matrix"], values, group) for group, values in desired.items()}
    lane_base: dict[str, np.ndarray] = {}
    for group in ("xy", "z"):
        for index, lane in enumerate(config.static_mixing[group]["input_lanes"]):
            lane_base[lane] = solved[group][:, index]
    if set(lane_base) != set(LANE_ORDER[:7]):
        _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, "Stage 4.1 lane ownership is not exact")

    lmax = max(config.lanes[lane]["latency_samples"] for lane in lane_base)
    mmax = max(len(config.lanes[lane]["fir"]) for lane in lane_base)
    n_awg = n + lmax
    p_count = n_awg + mmax - 1 + lmax
    requested: dict[str, np.ndarray] = {}
    codes: dict[str, np.ndarray] = {}
    reconstructed: dict[str, np.ndarray] = {}
    delivered: dict[str, np.ndarray] = {}
    max_quantization_error = 0.0
    for lane in LANE_ORDER[:7]:
        spec = config.lanes[lane]
        offset = lmax - spec["latency_samples"]
        if offset < 0:
            _fail(ParameterizedControlReasonCode.LATENCY_CONTRACT_INVALID, f"negative precompensation offset for {lane}")
        row = np.zeros(n_awg, dtype="<f8")
        row[offset:offset + n] = lane_base[lane]
        try:
            lane_codes, lane_reconstructed = _quantize_half_even(row, config.dac)
        except ValueError as exc:
            _fail(ParameterizedControlReasonCode.DAC_RANGE_EXCEEDED, str(exc))
        error = float(np.max(np.abs(row - lane_reconstructed))) if row.size else 0.0
        if error > 0.5 * float(config.dac["lsb_V"]) + 1e-15:
            _fail(ParameterizedControlReasonCode.QUANTIZATION_BOUND_EXCEEDED, lane)
        requested[lane] = freeze_array(row, "<f8")
        codes[lane] = freeze_array(lane_codes, "<i8")
        reconstructed[lane] = freeze_array(lane_reconstructed, "<f8")
        delivered[lane] = freeze_array(_deliver(lane_reconstructed, spec, p_count), "<f8")
        max_quantization_error = max(max_quantization_error, error)

    effective = {
        group: _forward(config.static_mixing[group]["matrix"], config.static_mixing[group]["input_lanes"], delivered, p_count)
        for group in ("xy", "z")
    }
    effective_absolute = effective["z"] + idle
    _check_device_limits(effective_absolute, context, "effective aggregate")
    if not np.all(np.isfinite(effective["xy"])) or not np.all(np.isfinite(effective_absolute)):
        _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, "effective arrays are non-finite")

    forward_error = _forward_reference_error(config, delivered, effective)
    idle_error = float(np.max(np.abs((effective_absolute - effective["z"]) - idle))) if p_count else 0.0
    checks = _checks(
        forward_error=forward_error,
        idle_error=idle_error,
        max_quantization_error=max_quantization_error,
    )
    logical_time = (np.arange(n, dtype="<f8") + 0.5) * float(config.dt_ns)
    awg_time = (np.arange(n_awg, dtype="<f8") - lmax + 0.5) * float(config.dt_ns)
    effective_time = (np.arange(p_count, dtype="<f8") - lmax + 0.5) * float(config.dt_ns)
    return ParameterizedControlCompilation(
        schema_version="0.1",
        artifact_type="stage_04_1_parameterized_control",
        status="compiled_prepublication",
        plan=logical,
        logical_flux_absolute_phi0=freeze_mapping({
            "q1": freeze_array(logical_absolute[:, 0], "<f8"),
            "q2": freeze_array(logical_absolute[:, 1], "<f8"),
            "c": freeze_array(logical_absolute[:, 2], "<f8"),
        }),
        requested_voltage_V=freeze_mapping(requested),
        dac_codes=freeze_mapping(codes),
        reconstructed_voltage_V=freeze_mapping(reconstructed),
        delivered_voltage_V=freeze_mapping(delivered),
        effective_xy_drive_GHz=freeze_mapping({
            "q1_i": freeze_array(effective["xy"][:, 0], "<f8"),
            "q1_q": freeze_array(effective["xy"][:, 1], "<f8"),
            "q2_i": freeze_array(effective["xy"][:, 2], "<f8"),
            "q2_q": freeze_array(effective["xy"][:, 3], "<f8"),
        }),
        effective_flux_delta_phi0=freeze_mapping({
            "q1": freeze_array(effective["z"][:, 0], "<f8"),
            "q2": freeze_array(effective["z"][:, 1], "<f8"),
            "c": freeze_array(effective["z"][:, 2], "<f8"),
        }),
        effective_absolute_flux_phi0=freeze_mapping({
            "q1": freeze_array(effective_absolute[:, 0], "<f8"),
            "q2": freeze_array(effective_absolute[:, 1], "<f8"),
            "c": freeze_array(effective_absolute[:, 2], "<f8"),
        }),
        logical_time_center_ns=freeze_array(logical_time, "<f8"),
        awg_time_center_ns=freeze_array(awg_time, "<f8"),
        effective_time_center_ns=freeze_array(effective_time, "<f8"),
        checks=tuple(freeze_mapping(row) for row in checks),
        metrics=freeze_mapping({
            "logical_sample_count": n,
            "awg_sample_count": n_awg,
            "effective_sample_count": p_count,
            "max_quantization_error_V": max_quantization_error,
            "forward_reference_max_abs_error": forward_error,
            "idle_reconstruction_max_abs_error": idle_error,
        }),
        context_authority_sha256=context.authority_sha256,
    )


def admit_qcis_v03_plan(plan: Mapping[str, Any] | Any, context: ParameterizedControlContext) -> QCISV03LogicalWaveformPlan:
    """Normalize the exact v0.3 logical-plan contract and reject v0.2 aliases."""

    if not isinstance(plan, Mapping):
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "v0.3 logical plan admission requires a mapping")
    if set(plan) != _TOP_LEVEL:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "v0.3 plan keys are not exact")
    if plan.get("schema_version") != "0.3" or plan.get("profile_id") != "qcis_stage7_calibration_v3":
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "only qcis v0.3 plans are admitted")
    point_id = plan.get("point_id")
    if not isinstance(point_id, str) or _IDENTIFIER.fullmatch(point_id) is None:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "point_id is invalid")
    for name in ("concrete_source_sha256", "ast_sha256", "trace_sha256"):
        if not isinstance(plan.get(name), str) or _HASH.fullmatch(plan[name]) is None:
            _fail(ParameterizedControlReasonCode.PLAN_HASH_MISMATCH, f"{name} is invalid")
    sample_count = plan.get("sample_count")
    dt_ns = plan.get("dt_ns")
    if type(sample_count) is not int or sample_count <= 0:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "sample_count must be a positive integer")
    if isinstance(dt_ns, bool) or not isinstance(dt_ns, (int, float)) or not math.isfinite(float(dt_ns)) or float(dt_ns) != float(context.control_chain_config.dt_ns):
        _fail(ParameterizedControlReasonCode.CLOCK_MISMATCH, "plan clock differs from the bound control clock")
    authorities = _hash_mapping(plan.get("authority_sha256"), ParameterizedControlReasonCode.PLAN_AUTHORITY_MISMATCH)
    if dict(authorities) != dict(context.expected_plan_authority_sha256):
        _fail(ParameterizedControlReasonCode.PLAN_AUTHORITY_MISMATCH, "plan authorities differ from the bound authorities")
    frame_reference = _frame_reference(plan.get("frame_reference_frequency_GHz"))
    frame_reference_authority = _hash_mapping(
        plan.get("frame_reference_authority_sha256"), ParameterizedControlReasonCode.PLAN_AUTHORITY_MISMATCH
    )
    if set(frame_reference_authority) != {"q1", "q2"}:
        _fail(ParameterizedControlReasonCode.PLAN_AUTHORITY_MISMATCH, "frame reference authority names are invalid")
    arrays = _logical_arrays(plan.get("logical"), plan.get("array_inventory"), sample_count)
    events = _events(
        plan.get("drive_event_inventory"), sample_count, frame_reference,
        float(context.control_chain_config.sample_rate_Hz),
    )
    event_sha256 = plan.get("drive_event_inventory_sha256")
    if not isinstance(event_sha256, str) or _HASH.fullmatch(event_sha256) is None or event_sha256 != sha256_json(list(events)):
        _fail(ParameterizedControlReasonCode.PLAN_HASH_MISMATCH, "drive event inventory hash differs")
    return QCISV03LogicalWaveformPlan(
        schema_version="0.3",
        profile_id="qcis_stage7_calibration_v3",
        point_id=point_id,
        concrete_source_sha256=plan["concrete_source_sha256"],
        ast_sha256=plan["ast_sha256"],
        trace_sha256=plan["trace_sha256"],
        sample_count=sample_count,
        dt_ns=float(dt_ns),
        xy_q1_i=arrays["logical.xy_delta_GHz.q1.i"],
        xy_q1_q=arrays["logical.xy_delta_GHz.q1.q"],
        xy_q2_i=arrays["logical.xy_delta_GHz.q2.i"],
        xy_q2_q=arrays["logical.xy_delta_GHz.q2.q"],
        flux_q1=arrays["logical.flux_delta_phi0.q1"],
        flux_q2=arrays["logical.flux_delta_phi0.q2"],
        flux_c=arrays["logical.flux_delta_phi0.c"],
        frame_reference_frequency_GHz=freeze_mapping(frame_reference),
        frame_reference_authority_sha256=freeze_mapping(frame_reference_authority),
        array_inventory=freeze_mapping(_inventory(plan["array_inventory"], arrays, sample_count)),
        drive_event_inventory=tuple(freeze_mapping(event) for event in events),
        drive_event_inventory_sha256=event_sha256,
        authority_sha256=freeze_mapping(authorities),
    )


def readmit_qcis_v03_plan(plan: QCISV03LogicalWaveformPlan, context: ParameterizedControlContext) -> QCISV03LogicalWaveformPlan:
    inventory = {
        name: {
            "name": row.name,
            "dtype": row.dtype,
            "shape": list(row.shape),
            "unit": row.unit,
            "byte_length": row.byte_length,
            "sha256": row.sha256,
        }
        for name, row in plan.array_inventory.items()
    }
    raw = {
        "schema_version": plan.schema_version,
        "profile_id": plan.profile_id,
        "point_id": plan.point_id,
        "concrete_source_sha256": plan.concrete_source_sha256,
        "ast_sha256": plan.ast_sha256,
        "trace_sha256": plan.trace_sha256,
        "sample_count": plan.sample_count,
        "dt_ns": plan.dt_ns,
        "logical": {
            "xy_delta_GHz": {
                "q1": {"i": plan.xy_q1_i, "q": plan.xy_q1_q},
                "q2": {"i": plan.xy_q2_i, "q": plan.xy_q2_q},
            },
            "flux_delta_phi0": {"q1": plan.flux_q1, "q2": plan.flux_q2, "c": plan.flux_c},
        },
        "frame_reference_frequency_GHz": plan.frame_reference_frequency_GHz,
        "frame_reference_authority_sha256": plan.frame_reference_authority_sha256,
        "array_inventory": inventory,
        "drive_event_inventory": [dict(event) for event in plan.drive_event_inventory],
        "drive_event_inventory_sha256": plan.drive_event_inventory_sha256,
        "authority_sha256": plan.authority_sha256,
    }
    return admit_qcis_v03_plan(raw, context)


def adapt_qcis_v03_compilation(
    compilation: Any, point_id: str, context: ParameterizedControlContext
) -> QCISV03LogicalWaveformPlan:
    """Adapt a QCIS v0.3 compilation by splitting its complex I/Q arrays once."""

    from sqvm.qcis import QCISCompilationError, verify_compilation, verify_drive_event_inventory
    from sqvm.qcis.models import QCISCompilation

    if not isinstance(compilation, QCISCompilation):
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "a typed QCISCompilation is required")
    source_plan = getattr(compilation, "plan", None)
    source_program = getattr(source_plan, "program", None)
    if source_plan is None or getattr(source_program, "schema_version", None) != "0.3":
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "QCISCompilation must contain a v0.3 logical plan")
    envelope = getattr(compilation, "envelope", None)
    if getattr(envelope, "program_schema_version", None) != "0.3" or getattr(envelope, "instruction_set_id", None) != "qcis_stage7_calibration_v3":
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "QCISCompilation profile is not qcis v3")
    if source_plan.dt_ns != float(context.control_chain_config.dt_ns) or source_plan.sample_rate_Hz != float(context.control_chain_config.sample_rate_Hz):
        _fail(ParameterizedControlReasonCode.CLOCK_MISMATCH, "QCIS v0.3 clock differs from the bound Stage 4.1 clock")
    try:
        verify_compilation(compilation)
        verify_drive_event_inventory(compilation)
    except QCISCompilationError as exc:
        _fail(ParameterizedControlReasonCode.PLAN_HASH_MISMATCH, f"QCIS compilation verification failed: {exc}")
    if not isinstance(point_id, str) or _IDENTIFIER.fullmatch(point_id) is None:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "point_id is invalid")
    q1_xy, q2_xy = getattr(compilation, "q1_xy", None), getattr(compilation, "q2_xy", None)
    q1_flux, q2_flux, c_flux = (getattr(compilation, name, None) for name in ("q1_flux", "q2_flux", "c_flux"))
    if any(not isinstance(value, np.ndarray) for value in (q1_xy, q2_xy, q1_flux, q2_flux, c_flux)):
        _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, "QCISCompilation arrays are absent")
    if q1_xy.dtype != np.dtype("<c16") or q2_xy.dtype != np.dtype("<c16") or any(value.dtype != np.dtype("<f8") for value in (q1_flux, q2_flux, c_flux)):
        _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, "QCISCompilation array dtypes are not v0.3 canonical")
    n = q1_xy.size
    if q2_xy.shape != (n,) or any(value.shape != (n,) for value in (q1_flux, q2_flux, c_flux)):
        _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, "QCISCompilation array lengths differ")
    arrays = {
        "logical.xy_delta_GHz.q1.i": np.ascontiguousarray(q1_xy.real, dtype="<f8"),
        "logical.xy_delta_GHz.q1.q": np.ascontiguousarray(q1_xy.imag, dtype="<f8"),
        "logical.xy_delta_GHz.q2.i": np.ascontiguousarray(q2_xy.real, dtype="<f8"),
        "logical.xy_delta_GHz.q2.q": np.ascontiguousarray(q2_xy.imag, dtype="<f8"),
        "logical.flux_delta_phi0.q1": np.ascontiguousarray(q1_flux, dtype="<f8"),
        "logical.flux_delta_phi0.q2": np.ascontiguousarray(q2_flux, dtype="<f8"),
        "logical.flux_delta_phi0.c": np.ascontiguousarray(c_flux, dtype="<f8"),
    }
    inventory = {
        name: {"name": name, "dtype": "<f8", "shape": [n], "unit": _ARRAYS[name][3], "byte_length": value.nbytes, "sha256": _sha(value)}
        for name, value in arrays.items()
    }
    raw = {
        "schema_version": "0.3",
        "profile_id": "qcis_stage7_calibration_v3",
        "point_id": point_id,
        "concrete_source_sha256": getattr(compilation, "concrete_source_sha256", None),
        "ast_sha256": getattr(source_plan, "ast_sha256", None),
        "trace_sha256": getattr(source_plan, "trace_sha256", None),
        "sample_count": n,
        "dt_ns": source_plan.dt_ns,
        "logical": {
            "xy_delta_GHz": {"q1": {"i": arrays["logical.xy_delta_GHz.q1.i"], "q": arrays["logical.xy_delta_GHz.q1.q"]}, "q2": {"i": arrays["logical.xy_delta_GHz.q2.i"], "q": arrays["logical.xy_delta_GHz.q2.q"]}},
            "flux_delta_phi0": {"q1": arrays["logical.flux_delta_phi0.q1"], "q2": arrays["logical.flux_delta_phi0.q2"], "c": arrays["logical.flux_delta_phi0.c"]},
        },
        "frame_reference_frequency_GHz": getattr(source_plan, "frame_reference_frequency_GHz", None),
        "frame_reference_authority_sha256": getattr(source_plan, "frame_reference_authority_sha256", None),
        "array_inventory": inventory,
        "drive_event_inventory": [dict(event) for event in source_plan.drive_event_inventory],
        "drive_event_inventory_sha256": source_plan.drive_event_inventory_sha256,
        "authority_sha256": getattr(source_plan, "authority_sha256", None),
    }
    return admit_qcis_v03_plan(raw, context)


def _logical_arrays(logical: Any, inventory: Any, n: int) -> dict[str, np.ndarray]:
    if not isinstance(logical, Mapping) or set(logical) != {"xy_delta_GHz", "flux_delta_phi0"}:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical coordinate groups are invalid")
    xy, flux = logical["xy_delta_GHz"], logical["flux_delta_phi0"]
    if not isinstance(xy, Mapping) or set(xy) != {"q1", "q2"} or not isinstance(flux, Mapping) or set(flux) != {"q1", "q2", "c"}:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical coordinate ownership is invalid")
    raw = {
        "logical.xy_delta_GHz.q1.i": xy["q1"].get("i") if isinstance(xy["q1"], Mapping) else None,
        "logical.xy_delta_GHz.q1.q": xy["q1"].get("q") if isinstance(xy["q1"], Mapping) else None,
        "logical.xy_delta_GHz.q2.i": xy["q2"].get("i") if isinstance(xy["q2"], Mapping) else None,
        "logical.xy_delta_GHz.q2.q": xy["q2"].get("q") if isinstance(xy["q2"], Mapping) else None,
        "logical.flux_delta_phi0.q1": flux["q1"],
        "logical.flux_delta_phi0.q2": flux["q2"],
        "logical.flux_delta_phi0.c": flux["c"],
    }
    if not isinstance(xy["q1"], Mapping) or set(xy["q1"]) != {"i", "q"} or not isinstance(xy["q2"], Mapping) or set(xy["q2"]) != {"i", "q"}:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "XY I/Q keys are invalid")
    if not isinstance(inventory, Mapping) or set(inventory) != set(_ARRAYS):
        _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, "array inventory names are invalid")
    result: dict[str, np.ndarray] = {}
    for name, value in raw.items():
        if not isinstance(value, np.ndarray) or value.dtype != np.dtype("<f8") or value.ndim != 1 or not value.flags.c_contiguous:
            _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, f"{name} must be contiguous little-endian float64")
        if value.shape != (n,) or not np.isfinite(value).all():
            _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, f"{name} shape or values are invalid")
        result[name] = freeze_array(value, "<f8")
    return result


def _inventory(raw: Mapping[str, Any], arrays: Mapping[str, np.ndarray], n: int) -> dict[str, LogicalArrayInventoryRow]:
    result: dict[str, LogicalArrayInventoryRow] = {}
    for name, (_, _, _, unit) in _ARRAYS.items():
        row = raw[name]
        if not isinstance(row, Mapping) or set(row) != {"name", "dtype", "shape", "unit", "byte_length", "sha256"}:
            _fail(ParameterizedControlReasonCode.ARRAY_CONTRACT_INVALID, f"{name} inventory fields are invalid")
        digest = _sha(arrays[name])
        if row.get("name") != name or row.get("dtype") != "<f8" or row.get("shape") != [n] or row.get("unit") != unit or row.get("byte_length") != arrays[name].nbytes or row.get("sha256") != digest:
            _fail(ParameterizedControlReasonCode.PLAN_HASH_MISMATCH, f"{name} inventory does not match raw bytes")
        result[name] = LogicalArrayInventoryRow(name, "<f8", (n,), unit, arrays[name].nbytes, digest)
    return result


def _frame_reference(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"q1", "q2"}:
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "frame reference names are invalid")
    result: dict[str, float] = {}
    for name, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) or float(item) <= 0.0:
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, f"frame reference {name} is invalid")
        result[name] = float(item)
    return result


def _events(value: Any, n: int, frame_reference: Mapping[str, float], sample_rate_hz: float) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical_event_inventory must be a list")
    seen: set[str] = set()
    events: list[Mapping[str, Any]] = []
    for row in value:
        if not isinstance(row, Mapping) or set(row) != _EVENT_KEYS:
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical event fields are invalid")
        event_id, target = row["event_id"], row["target"]
        if not isinstance(event_id, str) or not event_id or event_id in seen or target not in {"Q1", "Q2"}:
            _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, "logical event identity is invalid")
        start, count = row["actual_start_sample"], row["sample_count"]
        if type(start) is not int or type(count) is not int or start < 0 or count <= 0 or start + count > n:
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical event sample range is invalid")
        if type(row["source_instruction_index"]) is not int or row["source_instruction_index"] < 0 or row["transition"] not in {"01", "12"} or row["phase_rule_id"] != "qcis_v03_absolute_detuning_phase_v1":
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical event metadata is invalid")
        for name in ("phase_total_rad", "f_drive_GHz", "f_ref_GHz", "detuning_GHz"):
            item = row[name]
            if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)):
                _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, f"{name} is invalid")
        if not math.isclose(
            float(row["f_drive_GHz"]) - float(row["f_ref_GHz"]),
            float(row["detuning_GHz"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "drive event detuning does not match f_drive-f_ref")
        component = {"Q1": "q1", "Q2": "q2"}[target]
        if float(row["f_ref_GHz"]) != frame_reference[component] or abs(float(row["detuning_GHz"])) >= sample_rate_hz / 2.0 / 1e9:
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "drive event frame or Nyquist contract is invalid")
        if not isinstance(row["logical_array_contribution_sha256"], str) or _HASH.fullmatch(row["logical_array_contribution_sha256"]) is None:
            _fail(ParameterizedControlReasonCode.PLAN_HASH_MISMATCH, "logical event contribution hash is invalid")
        setting = row["setting_evidence"]
        if setting is not None and (not isinstance(setting, Mapping) or set(setting) != {"setting_id", "revision", "setting_hash", "calibration_run_id"} or not isinstance(setting["setting_id"], str) or not setting["setting_id"] or type(setting["revision"]) is not int or setting["revision"] <= 0 or not isinstance(setting["setting_hash"], str) or _HASH.fullmatch(setting["setting_hash"]) is None or not isinstance(setting["calibration_run_id"], str) or not setting["calibration_run_id"]):
            _fail(ParameterizedControlReasonCode.PLAN_SCHEMA_INVALID, "logical event setting evidence is invalid")
        seen.add(event_id)
        normalized = dict(row)
        normalized["setting_evidence"] = None if setting is None else dict(setting)
        events.append(normalized)
    return tuple(events)


def _hash_mapping(value: Any, code: ParameterizedControlReasonCode) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        _fail(code, "authority hashes are required")
    result = {str(key): item for key, item in value.items()}
    if any(not key or not isinstance(item, str) or _HASH.fullmatch(item) is None for key, item in result.items()):
        _fail(code, "authority hash mapping is invalid")
    return result


def _validate_matrices(config: Any) -> None:
    for group in ("xy", "z"):
        matrix = np.asarray(config.static_mixing[group]["matrix"], dtype="<f8")
        expected = len(GROUP_CONTRACT[group][0])
        condition = float(np.linalg.cond(matrix)) if matrix.shape == (expected, expected) and np.isfinite(matrix).all() else math.inf
        if not math.isfinite(condition) or condition > float(config.acceptance["max_condition_number"]):
            _fail(ParameterizedControlReasonCode.MIXING_MATRIX_INVALID, f"{group} matrix is invalid")


def _solve(matrix: Any, desired: np.ndarray, group: str) -> np.ndarray:
    try:
        result = np.linalg.solve(np.asarray(matrix, dtype="<f8"), desired.T).T
    except np.linalg.LinAlgError as exc:
        _fail(ParameterizedControlReasonCode.MIXING_MATRIX_INVALID, f"{group} matrix cannot be solved: {exc}")
    if not np.isfinite(result).all():
        _fail(ParameterizedControlReasonCode.MIXING_MATRIX_INVALID, f"{group} solve is non-finite")
    return result


def _deliver(values: np.ndarray, spec: Mapping[str, Any], p_count: int) -> np.ndarray:
    convolved = np.convolve(values, np.asarray(spec["fir"], dtype="<f8"), mode="full")
    native = np.concatenate((np.zeros(spec["latency_samples"], dtype="<f8"), convolved))
    result = np.zeros(p_count, dtype="<f8")
    result[:native.size] = native
    return result


def _quantize_half_even(values: np.ndarray, dac: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    """Frozen DAC conversion copied into the public Stage 4.1 core, not imported privately."""

    codes = np.empty(values.size, dtype="<i8")
    reconstructed = np.empty(values.size, dtype="<f8")
    lsb: Decimal = dac["lsb_V"]
    with localcontext() as decimal_context:
        decimal_context.prec = 80
        decimal_context.rounding = ROUND_HALF_EVEN
        for index, value in enumerate(values):
            number = float(np.float64(value))
            if not math.isfinite(number):
                raise ValueError("requested AWG voltage is non-finite")
            code = int((Decimal.from_float(number) / lsb).to_integral_value(rounding=ROUND_HALF_EVEN))
            if code < dac["code_min"] or code > dac["code_max"]:
                raise ValueError(f"DAC code out of range at sample {index}")
            codes[index] = code
            reconstructed[index] = float(Decimal(code) * lsb)
            if abs(reconstructed[index] - number) > 0.5 * float(lsb) + 1e-15:
                raise ValueError("DAC quantization error exceeds half-LSB bound")
    return codes, reconstructed


def _forward(matrix: Any, lanes: tuple[str, ...], delivered: Mapping[str, np.ndarray], p_count: int) -> np.ndarray:
    lane_values = np.column_stack(tuple(delivered[lane] for lane in lanes))
    if lane_values.shape[0] != p_count:
        _fail(ParameterizedControlReasonCode.LATENCY_CONTRACT_INVALID, "delivered lane lengths are inconsistent")
    return lane_values @ np.asarray(matrix, dtype="<f8").T


def _forward_reference_error(config: Any, delivered: Mapping[str, np.ndarray], effective: Mapping[str, np.ndarray]) -> float:
    maximum = 0.0
    for group in ("xy", "z"):
        matrix = np.asarray(config.static_mixing[group]["matrix"], dtype="<f8")
        lanes = config.static_mixing[group]["input_lanes"]
        reference = np.empty_like(effective[group])
        for index in range(reference.shape[0]):
            reference[index] = matrix @ np.asarray([delivered[lane][index] for lane in lanes], dtype="<f8")
        maximum = max(maximum, float(np.max(np.abs(reference - effective[group]))))
    return maximum


def _check_device_limits(values: np.ndarray, context: ParameterizedControlContext, layer: str) -> None:
    for index, name in enumerate(("q1", "q2", "c")):
        lower, upper = context.device_flux_limits_phi0[name]
        if not np.all((values[:, index] >= lower) & (values[:, index] <= upper)):
            _fail(ParameterizedControlReasonCode.DEVICE_LIMIT_EXCEEDED, f"{layer} {name} flux violates device bounds")


def _checks(*, forward_error: float, idle_error: float, max_quantization_error: float) -> tuple[dict[str, Any], ...]:
    rows = {
        "logical_plan_schema_valid": True,
        "logical_plan_hashes_valid": True,
        "logical_arrays_valid": True,
        "authority_bindings_valid": True,
        "sample_grid_exact": True,
        "named_mapping_exact": True,
        "static_matrices_valid": True,
        "latency_alignment_exact": True,
        "no_dac_clipping": True,
        "quantization_error_within_bound": max_quantization_error >= 0.0,
        "forward_reconstruction_matches_reference": forward_error <= 1e-12,
        "idle_added_exactly_once": idle_error <= 1e-15,
        "effective_arrays_finite": True,
        "device_limits_satisfied": True,
        "stage4_compatibility_approval_valid": True,
    }
    return tuple({"name": name, "passed": passed, "reason_code": None if passed else name.upper(), "evidence_ref": name} for name, passed in rows.items())


def _sha(array: np.ndarray) -> str:
    return hashlib.sha256(array.tobytes(order="C")).hexdigest().upper()


def _fail(code: ParameterizedControlReasonCode, detail: str) -> None:
    raise ParameterizedControlError(code, detail)
