"""Strict Stage 4 control-chain and schedule loaders."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
from pathlib import Path
import re
from typing import Any, Mapping

import numpy as np
import yaml

from sqvm.control.registry import CHANNEL_ORDER, ControlChannelRegistry
from sqvm.control.stage4_models import (
    ControlChainConfig,
    LogicalPulse,
    LogicalScenario,
    LogicalSchedule,
    ScheduleValidationReport,
)


LANE_ORDER = (
    "q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q", "q1_z", "q2_z", "c_z",
    "r1_ro_i", "r1_ro_q", "r2_ro_i", "r2_ro_q",
)
GROUP_CONTRACT = {
    "xy": (("q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q"), ("q1_drive_i_GHz", "q1_drive_q_GHz", "q2_drive_i_GHz", "q2_drive_q_GHz")),
    "z": (("q1_z", "q2_z", "c_z"), ("q1_delta_flux_phi0", "q2_delta_flux_phi0", "c_delta_flux_phi0")),
    "readout": (("r1_ro_i", "r1_ro_q", "r2_ro_i", "r2_ro_q"), ("r1_device_i_V", "r1_device_q_V", "r2_device_i_V", "r2_device_q_V")),
}
ROOT_KEYS = {"schema_version", "experiment_type", "profile", "inputs", "clock", "dac", "lane_order", "lanes", "static_mixing", "idle_flux_phi0", "acceptance"}
INPUT_KEYS = {"stage4_design_freeze_manifest", "channel_registry", "channel_registry_approval", "stage3_1_artifact", "stage3_1_approval"}
ACCEPTANCE_KEYS = {"max_condition_number", "max_xy_area_relative_error", "max_readout_area_relative_error", "max_z_flat_top_error_phi0", "max_phase_proxy_rad", "phase_proxy_window_ns", "max_formal_samples_per_scenario", "analysis_runtime_budget_seconds", "total_runtime_budget_seconds"}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def load_control_chain_config(path: str | Path) -> ControlChainConfig:
    source = Path(path).resolve()
    raw = _yaml_mapping(source)
    _exact(raw, ROOT_KEYS, "control config")
    if raw.get("schema_version") != "0.1" or raw.get("experiment_type") != "control_signal_chain" or raw.get("profile") not in {"formal", "smoke"}:
        raise ValueError("control config identity is invalid")
    inputs = _mapping(raw["inputs"], INPUT_KEYS, "control inputs")
    for key, value in inputs.items():
        if not isinstance(value, str) or not value or "\\" in value or Path(value).is_absolute():
            raise ValueError(f"control input {key} must be repository-relative POSIX text")
    clock = _mapping(raw["clock"], {"sample_rate_Hz", "dt_ns"}, "clock")
    rate = _integer(clock["sample_rate_Hz"], "sample_rate_Hz")
    dt = _decimal(clock["dt_ns"], "dt_ns")
    if rate != 2_000_000_000 or dt != Decimal(10) ** 9 / Decimal(rate) or dt != Decimal("0.5"):
        raise ValueError("clock contract mismatch")
    dac_raw = _mapping(raw["dac"], {"bits", "full_scale_min_V", "full_scale_max_exclusive_V", "rounding"}, "dac")
    bits = _integer(dac_raw["bits"], "dac.bits")
    lower = _decimal(dac_raw["full_scale_min_V"], "dac.full_scale_min_V")
    upper = _decimal(dac_raw["full_scale_max_exclusive_V"], "dac.full_scale_max_exclusive_V")
    if bits != 16 or lower != Decimal("-0.33") or upper != Decimal("0.33") or dac_raw.get("rounding") != "half_even":
        raise ValueError("DAC contract mismatch")
    lsb = (upper - lower) / Decimal(2**bits)
    dac = {"bits": bits, "full_scale_min_V": lower, "full_scale_max_exclusive_V": upper, "rounding": "half_even", "code_min": -(2 ** (bits - 1)), "code_max": 2 ** (bits - 1) - 1, "lsb_V": lsb}
    lane_order = raw["lane_order"]
    if not isinstance(lane_order, list) or tuple(lane_order) != LANE_ORDER:
        raise ValueError("lane_order is not exact")
    lanes_raw = _mapping(raw["lanes"], set(LANE_ORDER), "lanes")
    lanes: dict[str, Mapping[str, Any]] = {}
    for lane in LANE_ORDER:
        row = _mapping(lanes_raw[lane], {"latency_samples", "fir"}, f"lane {lane}")
        latency = _integer(row["latency_samples"], f"{lane}.latency_samples")
        fir_raw = row["fir"]
        if latency < 0 or latency > 64 or not isinstance(fir_raw, list) or not 1 <= len(fir_raw) <= 64:
            raise ValueError(f"lane {lane} latency/FIR contract mismatch")
        fir = tuple(_float(value, f"{lane}.fir") for value in fir_raw)
        if abs(sum(fir) - 1.0) > 1e-12 or sum(abs(value) for value in fir) > 4.0 or fir[0] == 0 or any(value < 0 for value in fir):
            raise ValueError(f"lane {lane} FIR contract mismatch")
        lanes[lane] = {"latency_samples": latency, "fir": fir}
    mixing_raw = _mapping(raw["static_mixing"], set(GROUP_CONTRACT), "static_mixing")
    mixing: dict[str, Mapping[str, Any]] = {}
    max_condition = _float(_mapping(raw["acceptance"], ACCEPTANCE_KEYS, "acceptance")["max_condition_number"], "max_condition_number")
    for group, (input_lanes, coordinates) in GROUP_CONTRACT.items():
        row = _mapping(mixing_raw[group], {"input_lanes", "output_coordinates", "matrix"}, f"static_mixing.{group}")
        if tuple(row["input_lanes"]) != input_lanes or tuple(row["output_coordinates"]) != coordinates:
            raise ValueError(f"static_mixing.{group} coordinate contract mismatch")
        matrix = np.asarray(row["matrix"], dtype=float)
        if matrix.shape != (len(coordinates), len(input_lanes)) or not np.isfinite(matrix).all():
            raise ValueError(f"static_mixing.{group} matrix shape/finite contract mismatch")
        condition = float(np.linalg.cond(matrix, 2))
        if not math.isfinite(condition) or np.linalg.det(matrix) == 0 or condition > max_condition:
            raise ValueError(f"static_mixing.{group} matrix is singular or ill-conditioned")
        mixing[group] = {"input_lanes": input_lanes, "output_coordinates": coordinates, "matrix": matrix, "condition_number_2": condition}
    idle_raw = _mapping(raw["idle_flux_phi0"], {"q1", "q2", "c"}, "idle_flux_phi0")
    idle = {key: _decimal(idle_raw[key], f"idle_flux_phi0.{key}") for key in ("q1", "q2", "c")}
    if idle != {"q1": Decimal("0.10"), "q2": Decimal("0.00"), "c": Decimal("0.27")}:
        raise ValueError("idle flux contract mismatch")
    acceptance_raw = _mapping(raw["acceptance"], ACCEPTANCE_KEYS, "acceptance")
    acceptance = {key: (_integer(value, key) if key == "max_formal_samples_per_scenario" else _float(value, key)) for key, value in acceptance_raw.items()}
    expected_limits = (10_000, 10.0, 60.0) if raw["profile"] == "formal" else (1_000, 2.0, 10.0)
    if (acceptance["max_formal_samples_per_scenario"], acceptance["analysis_runtime_budget_seconds"], acceptance["total_runtime_budget_seconds"]) != expected_limits:
        raise ValueError("profile runtime/sample limits mismatch")
    fixed = {"max_condition_number": 100.0, "max_xy_area_relative_error": 0.005, "max_readout_area_relative_error": 0.005, "max_z_flat_top_error_phi0": 0.00002, "max_phase_proxy_rad": 0.10, "phase_proxy_window_ns": 32.0}
    if any(acceptance[key] != value for key, value in fixed.items()):
        raise ValueError("acceptance thresholds mismatch")
    return ControlChainConfig(source, raw["profile"], dict(inputs), rate, dt, dac, LANE_ORDER, lanes, mixing, idle, acceptance)


def load_logical_schedule(path: str | Path, *, dt_ns: Decimal) -> LogicalSchedule:
    source = Path(path).resolve()
    raw = _yaml_mapping(source)
    _exact(raw, {"schema_version", "artifact_type", "artifact_version", "schedule_id", "scenarios"}, "logical schedule")
    if (raw.get("schema_version"), raw.get("artifact_type"), raw.get("artifact_version")) != ("0.1", "logical_pulse_schedule", "0.1"):
        raise ValueError("logical schedule identity is invalid")
    schedule_id = _identifier(raw.get("schedule_id"), "schedule_id")
    scenario_rows = raw.get("scenarios")
    if not isinstance(scenario_rows, list) or not scenario_rows:
        raise ValueError("schedule scenarios must be a nonempty list")
    scenarios: list[LogicalScenario] = []
    scenario_ids: set[str] = set()
    pulse_ids: set[str] = set()
    for item in scenario_rows:
        row = _mapping(item, {"scenario_id", "duration_ns", "pulses"}, "scenario")
        scenario_id = _identifier(row["scenario_id"], "scenario_id")
        if scenario_id in scenario_ids:
            raise ValueError("scenario IDs must be unique")
        scenario_ids.add(scenario_id)
        duration = _grid_decimal(row["duration_ns"], dt_ns, "scenario.duration_ns", positive=True)
        if not isinstance(row["pulses"], list):
            raise ValueError("scenario pulses must be a list")
        pulses: list[LogicalPulse] = []
        for item_pulse in row["pulses"]:
            pulse = _load_pulse(item_pulse, dt_ns, duration)
            if pulse.pulse_id in pulse_ids:
                raise ValueError("pulse IDs must be globally unique")
            pulse_ids.add(pulse.pulse_id)
            pulses.append(pulse)
        pulses.sort(key=lambda value: (value.start_ns, value.channel, value.pulse_id))
        scenarios.append(LogicalScenario(scenario_id, duration, tuple(pulses)))
    return LogicalSchedule(source, schedule_id, tuple(scenarios))


def validate_logical_schedule(schedule: LogicalSchedule, registry: ControlChannelRegistry, config: ControlChainConfig) -> ScheduleValidationReport:
    if not isinstance(schedule, LogicalSchedule) or not isinstance(registry, ControlChannelRegistry) or not isinstance(config, ControlChainConfig):
        raise TypeError("schedule, registry, and config must use Stage 4 typed models")
    channels = registry.channel_map()
    errors: list[str] = []
    conflicts: list[Mapping[str, Any]] = []
    seen_channels: dict[tuple[str, str], LogicalPulse] = {}
    for scenario in schedule.scenarios:
        samples = int(scenario.duration_ns / config.dt_ns)
        if samples > config.acceptance["max_formal_samples_per_scenario"]:
            errors.append(f"scenario sample limit exceeded:{scenario.scenario_id}")
        for pulse in scenario.pulses:
            channel = channels.get(pulse.channel)
            if channel is None or channel.kind != pulse.kind:
                errors.append(f"pulse channel/kind mismatch:{pulse.pulse_id}")
                continue
            key = (scenario.scenario_id, pulse.channel)
            if key in seen_channels:
                errors.append(f"multiple pulses on logical channel:{scenario.scenario_id}:{pulse.channel}")
            seen_channels[key] = pulse
        for index, left in enumerate(scenario.pulses):
            left_channel = channels.get(left.channel)
            if left_channel is None:
                continue
            for right in scenario.pulses[index + 1 :]:
                right_channel = channels.get(right.channel)
                if right_channel is None or max(left.start_ns, right.start_ns) >= min(left.start_ns + left.duration_ns, right.start_ns + right.duration_ns):
                    continue
                reasons = []
                if left.channel == right.channel: reasons.append("same_logical_channel")
                if left_channel.port == right_channel.port: reasons.append("same_physical_port")
                if set(left_channel.awg_lanes) & set(right_channel.awg_lanes): reasons.append("shared_awg_lane")
                if reasons:
                    conflicts.append({"scenario_id": scenario.scenario_id, "left_pulse_id": left.pulse_id, "right_pulse_id": right.pulse_id, "reasons": reasons})
    conflicts.sort(key=lambda row: (row["scenario_id"], row["left_pulse_id"], row["right_pulse_id"]))
    if conflicts:
        errors.append("schedule contains resource conflicts")
    return ScheduleValidationReport(not errors, tuple(conflicts), tuple(errors))


def _load_pulse(value: Any, dt: Decimal, scenario_duration: Decimal) -> LogicalPulse:
    if not isinstance(value, Mapping):
        raise ValueError("pulse must be a mapping")
    kind = value.get("kind")
    common = {"pulse_id", "kind", "channel", "start_ns", "duration_ns", "shape"}
    extras = {
        "xy": {"amplitude_GHz", "phase_rad", "sigma_ns", "drag_beta_ns", "carrier_frequency_GHz"},
        "z": {"target_flux_phi0", "rise_ns"},
        "readout": {"amplitude_device_V", "phase_rad", "rise_ns", "carrier_frequency_GHz"},
    }
    if kind not in extras:
        raise ValueError("pulse kind is invalid")
    _exact(value, common | extras[kind], f"{kind} pulse")
    pulse_id = _identifier(value["pulse_id"], "pulse_id")
    channel = _identifier(value["channel"], "channel")
    start = _grid_decimal(value["start_ns"], dt, "pulse.start_ns", nonnegative=True)
    duration = _grid_decimal(value["duration_ns"], dt, "pulse.duration_ns", positive=True)
    if start + duration > scenario_duration:
        raise ValueError(f"pulse exceeds scenario duration:{pulse_id}")
    shape = value["shape"]
    values: dict[str, Any] = {"shape": shape}
    if kind == "xy":
        if shape not in {"gaussian", "drag_gaussian"}: raise ValueError("XY shape is invalid")
        amplitude = _float(value["amplitude_GHz"], "amplitude_GHz")
        phase = _float(value["phase_rad"], "phase_rad")
        sigma = _grid_decimal(value["sigma_ns"], dt, "sigma_ns", positive=True)
        beta = _float(value["drag_beta_ns"], "drag_beta_ns")
        carrier = _float(value["carrier_frequency_GHz"], "carrier_frequency_GHz")
        if amplitude <= 0 or carrier <= 0 or duration < 4 * sigma or (shape == "gaussian" and beta != 0): raise ValueError("XY pulse numeric contract mismatch")
        values.update({"amplitude_GHz": amplitude, "phase_rad": phase, "sigma_ns": float(sigma), "drag_beta_ns": beta, "carrier_frequency_GHz": carrier})
    elif kind == "z":
        if shape not in {"square", "flattop_cos"}: raise ValueError("Z shape is invalid")
        target = _float(value["target_flux_phi0"], "target_flux_phi0")
        rise = _grid_decimal(value["rise_ns"], dt, "rise_ns", nonnegative=True)
        if target < -0.5 or target > 0.5 or (shape == "square" and rise != 0) or (shape == "flattop_cos" and (rise < dt or 2 * rise >= duration)): raise ValueError("Z pulse numeric contract mismatch")
        values.update({"target_flux_phi0": target, "rise_ns": float(rise)})
    else:
        if shape not in {"square", "flattop_cos"}: raise ValueError("readout shape is invalid")
        amplitude = _float(value["amplitude_device_V"], "amplitude_device_V")
        phase = _float(value["phase_rad"], "phase_rad")
        rise = _grid_decimal(value["rise_ns"], dt, "rise_ns", nonnegative=True)
        carrier = _float(value["carrier_frequency_GHz"], "carrier_frequency_GHz")
        if not 0 < amplitude <= 0.001 or carrier <= 0 or (shape == "square" and rise != 0) or (shape == "flattop_cos" and (rise < dt or 2 * rise >= duration)): raise ValueError("readout pulse numeric contract mismatch")
        values.update({"amplitude_device_V": amplitude, "phase_rad": phase, "rise_ns": float(rise), "carrier_frequency_GHz": carrier})
    return LogicalPulse(pulse_id, kind, channel, start, duration, values)


def _yaml_mapping(path: Path) -> Mapping[str, Any]:
    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise yaml.YAMLError(f"duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load YAML: {exc}") from exc
    if not isinstance(value, Mapping): raise ValueError("YAML root must be a mapping")
    return value


def _mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys: raise ValueError(f"{label} fields are not exact")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys: raise ValueError(f"{label} fields are not exact")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or not value.isascii(): raise ValueError(f"{label} is invalid")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int): raise ValueError(f"{label} must be an integer")
    return value


def _float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)): raise ValueError(f"{label} must be finite numeric")
    return float(value)


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)): raise ValueError(f"{label} must be numeric")
    try: result = Decimal(str(value))
    except InvalidOperation as exc: raise ValueError(f"{label} must be finite Decimal") from exc
    if not result.is_finite(): raise ValueError(f"{label} must be finite Decimal")
    return result


def _grid_decimal(value: Any, dt: Decimal, label: str, *, positive: bool = False, nonnegative: bool = False) -> Decimal:
    result = _decimal(value, label)
    if (positive and result <= 0) or (nonnegative and result < 0) or result % dt != 0: raise ValueError(f"{label} violates sample grid")
    return result
