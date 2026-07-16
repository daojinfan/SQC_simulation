"""Admission of fixed Stage 4 electronics for the parameterized Stage 4.1 core."""

from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any, Mapping

from sqvm.control.registry import CHANNEL_ORDER
from sqvm.control.stage4_config import GROUP_CONTRACT, LANE_ORDER
from sqvm.control.stage4_models import ControlChainConfig

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
    repository_root: Path,
    output_root: Path,
    authority_sha256: Mapping[str, str],
    expected_plan_authority_sha256: Mapping[str, str],
    stage4_compatibility_approved: bool,
    compiler_source_snapshot: Mapping[str, Any],
    environment_snapshot: Mapping[str, Any],
    publication_policy: Mapping[str, Any],
) -> ParameterizedControlContext:
    """Bind exactly the accepted Stage 4 electronics; experiments cannot override it."""

    if not isinstance(control_chain_config, ControlChainConfig) or not isinstance(channel_registry, ControlChannelRegistry):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "typed Stage 4 config and registry are required")
    if not stage4_compatibility_approved:
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "accepted Stage 4 compatibility approval is required")
    if not isinstance(repository_root, Path) or not isinstance(output_root, Path) or not repository_root.is_absolute() or not output_root.is_absolute():
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, "repository_root and output_root must be absolute Paths")
    for name, value in (("compiler_source_snapshot", compiler_source_snapshot), ("environment_snapshot", environment_snapshot), ("publication_policy", publication_policy)):
        if not isinstance(value, Mapping):
            _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{name} must be a mapping")
    if float(control_chain_config.dt_ns) != 0.5 or control_chain_config.sample_rate_Hz != 2_000_000_000:
        _fail(ParameterizedControlReasonCode.CLOCK_MISMATCH, "Stage 4.1 binds the accepted 0.5 ns clock")
    if tuple(control_chain_config.lane_order) != LANE_ORDER or set(control_chain_config.lanes) != set(LANE_ORDER):
        _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, "AWG lane order differs from accepted Stage 4")
    if set(control_chain_config.static_mixing) != set(GROUP_CONTRACT):
        _fail(ParameterizedControlReasonCode.MIXING_MATRIX_INVALID, "mixing groups differ from accepted Stage 4")
    for group, (lanes, coordinates) in GROUP_CONTRACT.items():
        row = control_chain_config.static_mixing[group]
        if tuple(row.get("input_lanes", ())) != lanes or tuple(row.get("output_coordinates", ())) != coordinates:
            _fail(ParameterizedControlReasonCode.NAMED_MAPPING_INVALID, f"{group} mixing names differ")
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
    limits = _limits(device_flux_limits_phi0)
    for name in _FLUX_NAMES:
        idle_value = float(idle[name])
        if not limits[name][0] <= idle_value <= limits[name][1]:
            _fail(ParameterizedControlReasonCode.DEVICE_LIMIT_EXCEEDED, f"idle flux for {name} is outside device bounds")
    return ParameterizedControlContext(
        repository_root=repository_root,
        output_root=output_root,
        control_chain_config=control_chain_config,
        channel_registry=channel_registry,
        device_flux_limits_phi0=freeze_mapping(limits),
        authority_sha256=freeze_mapping(_hashes(authority_sha256, "control authority")),
        expected_plan_authority_sha256=freeze_mapping(_hashes(expected_plan_authority_sha256, "plan authority")),
        stage4_compatibility_approved=True,
        compiler_source_snapshot=freeze_mapping(compiler_source_snapshot),
        environment_snapshot=freeze_mapping(environment_snapshot),
        publication_policy=freeze_mapping(publication_policy),
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


def _hashes(value: Mapping[str, str], label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{label} hashes are required")
    result = {str(key): item for key, item in value.items()}
    if any(not key or not isinstance(item, str) or _HASH.fullmatch(item) is None for key, item in result.items()):
        _fail(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, f"{label} hashes are invalid")
    return result


def _fail(code: ParameterizedControlReasonCode, detail: str) -> None:
    raise ParameterizedControlError(code, detail)
