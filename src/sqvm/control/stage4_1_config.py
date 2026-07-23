"""Admission of fixed Stage 4 electronics for the parameterized Stage 4.1 core."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import math
from pathlib import Path
import re
from typing import Any, Mapping

import numpy as np

from sqvm.control.registry import CHANNEL_ORDER, load_control_channel_registry
from sqvm.control.stage4_config import GROUP_CONTRACT, LANE_ORDER, load_control_chain_config
from sqvm.control.stage4_models import ControlChainConfig
from sqvm.hamiltonian.provenance import canonical_json_bytes

from .models import ControlChannelRegistry
from .stage4_1_models import (
    ParameterizedControlContext,
    ParameterizedControlError,
    ParameterizedControlReasonCode,
    freeze_mapping,
)


_HASH = re.compile(r"^[0-9A-F]{64}$")
_FLUX_NAMES = ("q1", "q2", "c")
_REQUIRED_CHANNELS = {"q1_xy", "q2_xy", "q1_z", "q2_z", "c_z"}


def build_parameterized_control_context(
    control_chain_config: ControlChainConfig,
    channel_registry: ControlChannelRegistry,
    device_flux_limits_phi0: Mapping[str, Mapping[str, Any] | tuple[float, float]],
    *,
    device_limit_authority_sha256: str,
    repository_root: Path,
    output_root: Path,
    authority_sha256: Mapping[str, str],
    expected_plan_authority_sha256: Mapping[str, str],
    stage4_compatibility_approved: bool,
    compiler_source_snapshot: Mapping[str, Any],
    environment_snapshot: Mapping[str, Any],
    publication_policy: Mapping[str, Any],
    runtime_idle_flux_phi0: Mapping[str, Any] | None = None,
) -> ParameterizedControlContext:
    """Bind accepted electronics and an explicitly authorized idle operating point."""

    if not isinstance(control_chain_config, ControlChainConfig) or not isinstance(channel_registry, ControlChannelRegistry):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "typed Stage 4 config and registry are required")
    if not stage4_compatibility_approved:
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "accepted Stage 4 compatibility approval is required")
    if not isinstance(repository_root, Path) or not isinstance(output_root, Path) or not repository_root.is_absolute() or not output_root.is_absolute():
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "repository_root and output_root must be absolute Paths")
    repository_root = repository_root.resolve()
    for label, path in (("control config", control_chain_config.source_path), ("channel registry", channel_registry.source_path)):
        resolved = path.resolve()
        try:
            resolved.relative_to(repository_root)
        except ValueError:
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{label} is outside repository root")
        if path.is_symlink() or not resolved.is_file():
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{label} source is not an admitted file")
    for name, value in (("compiler_source_snapshot", compiler_source_snapshot), ("environment_snapshot", environment_snapshot), ("publication_policy", publication_policy)):
        if not isinstance(value, Mapping):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{name} must be a mapping")
    if float(control_chain_config.dt_ns) != 0.5 or control_chain_config.sample_rate_Hz != 2_000_000_000:
        _fail(ParameterizedControlReasonCode.CLOCK_MISMATCH, "Stage 4.1 binds the accepted 0.5 ns clock")
    if tuple(control_chain_config.lane_order) != LANE_ORDER or set(control_chain_config.lanes) != set(LANE_ORDER):
        _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, "AWG lane order differs from accepted Stage 4")
    dac = control_chain_config.dac
    if set(dac) != {"bits", "full_scale_min_V", "full_scale_max_exclusive_V", "rounding", "code_min", "code_max", "lsb_V"} or dac["bits"] != 16 or dac["rounding"] != "half_even" or dac["code_min"] != -32768 or dac["code_max"] != 32767 or float(dac["lsb_V"]) <= 0.0:
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "DAC contract differs from accepted Stage 4")
    for lane, row in control_chain_config.lanes.items():
        if set(row) != {"latency_samples", "fir"} or type(row["latency_samples"]) is not int or row["latency_samples"] < 0 or not isinstance(row["fir"], tuple) or not row["fir"] or any(not math.isfinite(float(value)) for value in row["fir"]):
            _fail(ParameterizedControlReasonCode.LATENCY_CONTRACT_INVALID, f"invalid lane electronics:{lane}")
    if set(control_chain_config.static_mixing) != set(GROUP_CONTRACT):
        _fail(ParameterizedControlReasonCode.MIXING_MATRIX_INVALID, "mixing groups differ from accepted Stage 4")
    for group, (lanes, coordinates) in GROUP_CONTRACT.items():
        row = control_chain_config.static_mixing[group]
        if tuple(row.get("input_lanes", ())) != lanes or tuple(row.get("output_coordinates", ())) != coordinates:
            _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, f"{group} mixing names differ")
        matrix = np.asarray(row.get("matrix"), dtype="<f8")
        condition = float(np.linalg.cond(matrix)) if matrix.shape == (len(lanes), len(lanes)) and np.isfinite(matrix).all() else math.inf
        maximum = float(control_chain_config.acceptance.get("max_condition_number", math.nan))
        reported = row.get("condition_number_2")
        if isinstance(reported, bool) or not isinstance(reported, (int, float)) or not math.isfinite(condition) or not math.isfinite(maximum) or condition > maximum or not math.isclose(condition, float(reported), rel_tol=1e-12, abs_tol=1e-12):
            _fail(ParameterizedControlReasonCode.MIXING_MATRIX_INVALID, f"{group} mixing matrix is not admitted")
    channel_map = channel_registry.channel_map()
    if set(channel_map) != set(CHANNEL_ORDER) or not _REQUIRED_CHANNELS.issubset(channel_map):
        _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, "channel registry is not the accepted seven-channel registry")
    if any(channel_map[name].kind == "readout" for name in _REQUIRED_CHANNELS):
        _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, "readout cannot own a Stage 4.1 coordinate")
    expected_lanes = {"q1_xy": ("q1_xy_i", "q1_xy_q"), "q2_xy": ("q2_xy_i", "q2_xy_q"), "q1_z": ("q1_z",), "q2_z": ("q2_z",), "c_z": ("c_z",)}
    for name, lanes in expected_lanes.items():
        if tuple(channel_map[name].awg_lanes) != lanes:
            _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, f"{name} lane ownership differs")
    idle = control_chain_config.idle_flux_phi0
    if set(idle) != set(_FLUX_NAMES):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "idle flux names are not exact")
    source_control = load_control_chain_config(control_chain_config.source_path)
    authority_hashes = _hashes(authority_sha256, "control authority")
    if runtime_idle_flux_phi0 is None:
        if not _same_control_config(control_chain_config, source_control):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "control config differs from its admitted source")
    else:
        runtime_idle = _runtime_idle_flux(runtime_idle_flux_phi0)
        if dict(idle) != runtime_idle:
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "runtime idle flux differs from control context")
        if not _same_control_config_except_idle(control_chain_config, source_control):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "control electronics differ from their admitted source")
        if authority_hashes.get("stage4_1_runtime_idle_flux") != _runtime_idle_flux_sha256(runtime_idle):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "runtime idle flux authority is not bound")
    limits = _limits(device_flux_limits_phi0)
    if not isinstance(device_limit_authority_sha256, str) or _HASH.fullmatch(device_limit_authority_sha256) is None:
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "device limit authority hash is invalid")
    for name in _FLUX_NAMES:
        idle_value = float(idle[name])
        if not limits[name][0] <= idle_value <= limits[name][1]:
            _fail(ParameterizedControlReasonCode.DEVICE_LIMIT_EXCEEDED, f"idle flux for {name} is outside device bounds")
    if channel_registry != load_control_channel_registry(channel_registry.source_path):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "channel registry differs from its admitted source")
    return ParameterizedControlContext(
        repository_root=repository_root,
        output_root=output_root.resolve(),
        control_chain_config=control_chain_config,
        channel_registry=channel_registry,
        device_flux_limits_phi0=freeze_mapping(limits),
        device_limit_authority_sha256=device_limit_authority_sha256,
        authority_sha256=freeze_mapping(authority_hashes),
        expected_plan_authority_sha256=freeze_mapping(_hashes(expected_plan_authority_sha256, "plan authority")),
        stage4_compatibility_approved=True,
        compiler_source_snapshot=freeze_mapping(compiler_source_snapshot),
        environment_snapshot=freeze_mapping(environment_snapshot),
        publication_policy=freeze_mapping(publication_policy),
    )


def validate_parameterized_control_context(context: ParameterizedControlContext) -> None:
    """Re-run admission so direct construction or nested mutation cannot bypass the factory."""

    if not isinstance(context, ParameterizedControlContext):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "typed Stage 4.1 context is required")
    runtime_idle = (
        context.control_chain_config.idle_flux_phi0
        if "stage4_1_runtime_idle_flux" in context.authority_sha256
        else None
    )
    build_parameterized_control_context(
        context.control_chain_config,
        context.channel_registry,
        context.device_flux_limits_phi0,
        device_limit_authority_sha256=context.device_limit_authority_sha256,
        repository_root=context.repository_root,
        output_root=context.output_root,
        authority_sha256=context.authority_sha256,
        expected_plan_authority_sha256=context.expected_plan_authority_sha256,
        stage4_compatibility_approved=context.stage4_compatibility_approved,
        compiler_source_snapshot=context.compiler_source_snapshot,
        environment_snapshot=context.environment_snapshot,
        publication_policy=context.publication_policy,
        runtime_idle_flux_phi0=runtime_idle,
    )


def _limits(value: Mapping[str, Mapping[str, Any] | tuple[float, float]]) -> dict[str, tuple[float, float]]:
    if not isinstance(value, Mapping) or set(value) != set(_FLUX_NAMES):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "device limits require q1, q2, c")
    result: dict[str, tuple[float, float]] = {}
    for name in _FLUX_NAMES:
        raw = value[name]
        if isinstance(raw, Mapping):
            if set(raw) != {"min_phi0", "max_phi0"}:
                _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{name} device limit keys are invalid")
            lower, upper = raw["min_phi0"], raw["max_phi0"]
        elif isinstance(raw, tuple) and len(raw) == 2:
            lower, upper = raw
        else:
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{name} device limits are invalid")
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(float(item)) for item in (lower, upper)):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{name} device limits must be finite")
        if float(lower) > float(upper):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{name} device limit order is invalid")
        result[name] = (float(lower), float(upper))
    return result


def _same_control_config(left: ControlChainConfig, right: ControlChainConfig) -> bool:
    scalar_fields = ("source_path", "profile", "inputs", "sample_rate_Hz", "dt_ns", "dac", "lane_order", "lanes", "idle_flux_phi0", "acceptance")
    if any(getattr(left, name) != getattr(right, name) for name in scalar_fields) or set(left.static_mixing) != set(right.static_mixing):
        return False
    for group in left.static_mixing:
        left_row, right_row = left.static_mixing[group], right.static_mixing[group]
        for name in ("input_lanes", "output_coordinates", "condition_number_2"):
            if left_row[name] != right_row[name]:
                return False
        if not np.array_equal(left_row["matrix"], right_row["matrix"]):
            return False
    return True


def _same_control_config_except_idle(
    left: ControlChainConfig,
    right: ControlChainConfig,
) -> bool:
    scalar_fields = (
        "source_path",
        "profile",
        "inputs",
        "sample_rate_Hz",
        "dt_ns",
        "dac",
        "lane_order",
        "lanes",
        "acceptance",
    )
    if any(getattr(left, name) != getattr(right, name) for name in scalar_fields):
        return False
    if set(left.static_mixing) != set(right.static_mixing):
        return False
    for group in left.static_mixing:
        left_row, right_row = left.static_mixing[group], right.static_mixing[group]
        for name in ("input_lanes", "output_coordinates", "condition_number_2"):
            if left_row[name] != right_row[name]:
                return False
        if not np.array_equal(left_row["matrix"], right_row["matrix"]):
            return False
    return True


def _runtime_idle_flux(value: Mapping[str, Any]) -> dict[str, Decimal]:
    if not isinstance(value, Mapping) or set(value) != set(_FLUX_NAMES):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "runtime idle flux names are not exact")
    normalized: dict[str, Decimal] = {}
    for name in _FLUX_NAMES:
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, (int, float, Decimal)):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"runtime idle flux for {name} is invalid")
        try:
            number = Decimal(str(item))
        except InvalidOperation:
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"runtime idle flux for {name} is invalid")
        if not number.is_finite():
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"runtime idle flux for {name} is invalid")
        normalized[name] = number
    return normalized


def _runtime_idle_flux_sha256(value: Mapping[str, Any]) -> str:
    normalized = _runtime_idle_flux(value)
    payload = {
        "idle_flux_phi0": {name: float(normalized[name]) for name in _FLUX_NAMES}
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


def _hashes(value: Mapping[str, str], label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{label} hashes are required")
    result = {str(key): item for key, item in value.items()}
    if any(not key or not isinstance(item, str) or _HASH.fullmatch(item) is None for key, item in result.items()):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{label} hashes are invalid")
    return result


def _fail(code: ParameterizedControlReasonCode, detail: str) -> None:
    raise ParameterizedControlError(code, detail)
