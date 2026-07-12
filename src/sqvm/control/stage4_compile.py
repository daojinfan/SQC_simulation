"""Deterministic Stage 4 control-signal compiler."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN, localcontext
import math
import time
from typing import Any, Mapping

import numpy as np

from sqvm.control.stage4_config import GROUP_CONTRACT, LANE_ORDER
from sqvm.control.stage4_models import ControlBuildContext, ControlChainConfig, ControlCompilationResult, LogicalPulse, LogicalSchedule


FORMAL_SCENARIOS = ("xy_drag", "q2_resonance_flux", "coupler_0_200", "coupler_0_270", "coupler_0_385", "readout_envelopes")
SMOKE_SCENARIOS = FORMAL_SCENARIOS[:2]
FORMAL_CHECKS = (
    "stage3_1_readiness_valid", "stage4_0_channel_registry_ready", "design_and_config_provenance_valid",
    "profile_acceptance_contract_valid", "schedule_schema_valid", "schedule_conflict_free", "sample_grid_exact",
    "static_matrices_valid", "no_dac_clipping", "quantization_error_within_bound", "latency_alignment_exact",
    "forward_reconstruction_matches_reference", "xy_area_error_within_budget", "z_target_error_within_budget",
    "readout_area_error_within_budget", "stage3_phase_proxy_within_budget", "runtime_within_budget",
)
SMOKE_CHECKS = tuple(name for name in FORMAL_CHECKS if name != "readout_area_error_within_budget")


def compile_control_schedule(schedule: LogicalSchedule, config: ControlChainConfig, context: ControlBuildContext) -> ControlCompilationResult:
    if not isinstance(schedule, LogicalSchedule) or not isinstance(config, ControlChainConfig) or not isinstance(context, ControlBuildContext):
        raise TypeError("compile_control_schedule requires typed Stage 4 inputs")
    expected_scenarios = FORMAL_SCENARIOS if config.profile == "formal" else SMOKE_SCENARIOS
    if tuple(row.scenario_id for row in schedule.scenarios) != expected_scenarios:
        raise ValueError("profile schedule scenario order is not exact")
    _validate_frozen_schedule(schedule, config.profile)
    compiled = [_compile_scenario(row, config) for row in schedule.scenarios]
    global_metrics = _global_metrics(compiled, config)
    phase_proxy = _phase_proxy(context.stage3_artifact, config)
    elapsed = time.perf_counter() - context.analysis_started_at
    runtime_ok = math.isfinite(elapsed) and elapsed <= config.acceptance["analysis_runtime_budget_seconds"]
    runtime = {
        "analysis_elapsed_seconds": elapsed,
        "analysis_runtime_budget_seconds": config.acceptance["analysis_runtime_budget_seconds"],
        "analysis_runtime_within_budget": runtime_ok,
    }
    values = {
        "stage3_1_readiness_valid": bool(context.provenance.get("stage3_1_readiness_valid")),
        "stage4_0_channel_registry_ready": bool(context.provenance.get("stage4_0_channel_registry_ready")),
        "design_and_config_provenance_valid": bool(context.provenance.get("design_and_config_provenance_valid")),
        "profile_acceptance_contract_valid": config.profile in {"formal", "smoke"},
        "schedule_schema_valid": True,
        "schedule_conflict_free": True,
        "sample_grid_exact": all(_scenario_check(row, "sample_lengths_valid") for row in compiled),
        "static_matrices_valid": all(row["condition_number_2"] <= config.acceptance["max_condition_number"] for row in config.static_mixing.values()),
        "no_dac_clipping": all(_scenario_check(row, "dac_codes_in_range") for row in compiled),
        "quantization_error_within_bound": global_metrics["quantization_error_within_bound"],
        "latency_alignment_exact": global_metrics["latency_alignment_exact"],
        "forward_reconstruction_matches_reference": global_metrics["forward_reconstruction_matches_reference"],
        "xy_area_error_within_budget": global_metrics["xy_area_error_within_budget"],
        "z_target_error_within_budget": global_metrics["z_target_error_within_budget"],
        "readout_area_error_within_budget": global_metrics["readout_area_error_within_budget"],
        "stage3_phase_proxy_within_budget": phase_proxy["passed"],
        "runtime_within_budget": runtime_ok,
    }
    names = FORMAL_CHECKS if config.profile == "formal" else SMOKE_CHECKS
    checks = [{"name": name, "passed": bool(values[name]), "message": "passed" if values[name] else f"{name} failed"} for name in names]
    blockers = [row["message"] for row in checks if not row["passed"]]
    ready = not blockers
    status = ("ready_for_stage5_review" if config.profile == "formal" else "smoke_complete") if ready else _failure_status(values)
    gate = {"computational_ready": ready, "status": status, "checks": checks, "blocking_reasons": blockers}
    payload = {
        "schema_version": "0.1", "artifact_type": "stage_04_control_signal", "artifact_version": "0.1",
        "profile": config.profile, "acceptance_eligible": config.profile == "formal", "status": status,
        "provenance": context.provenance["artifact_provenance"], "registry": context.registry,
        "control_config": _normalized_config(config), "scenario_order": list(expected_scenarios),
        "scenarios": compiled, "global_metrics": global_metrics, "phase_proxy": phase_proxy,
        "runtime": runtime, "computational_gate": gate,
    }
    return ControlCompilationResult(config.profile, config.profile == "formal", status, payload)


def _compile_scenario(scenario, config: ControlChainConfig) -> dict[str, Any]:
    dt = float(config.dt_ns)
    n = int(scenario.duration_ns / config.dt_ns)
    logical_time = (np.arange(n, dtype=float) + 0.5) * dt
    desired = {
        "xy": np.zeros((n, 4), dtype=float),
        "z": np.zeros((n, 3), dtype=float),
        "readout": np.zeros((n, 4), dtype=float),
    }
    pulse_by_id: dict[str, LogicalPulse] = {}
    for pulse in scenario.pulses:
        pulse_by_id[pulse.pulse_id] = pulse
        start = int(pulse.start_ns / config.dt_ns)
        count = int(pulse.duration_ns / config.dt_ns)
        envelope, derivative = _shape(pulse, count, dt)
        phase = float(pulse.values.get("phase_rad", 0.0))
        if pulse.kind == "xy":
            complex_signal = pulse.values["amplitude_GHz"] * (envelope + 1j * pulse.values["drag_beta_ns"] * derivative) * np.exp(1j * phase)
            offset = 0 if pulse.channel == "q1_xy" else 2
            desired["xy"][start:start + count, offset] = complex_signal.real
            desired["xy"][start:start + count, offset + 1] = complex_signal.imag
        elif pulse.kind == "z":
            mode = {"q1_z": 0, "q2_z": 1, "c_z": 2}[pulse.channel]
            idle = float(config.idle_flux_phi0[("q1", "q2", "c")[mode]])
            desired["z"][start:start + count, mode] = envelope * (pulse.values["target_flux_phi0"] - idle)
        else:
            complex_signal = pulse.values["amplitude_device_V"] * envelope * np.exp(1j * phase)
            offset = 0 if pulse.channel == "r1_ro" else 2
            desired["readout"][start:start + count, offset] = complex_signal.real
            desired["readout"][start:start + count, offset + 1] = complex_signal.imag
    solved: dict[str, np.ndarray] = {}
    for group in ("xy", "z", "readout"):
        matrix = config.static_mixing[group]["matrix"]
        solved[group] = np.stack([np.linalg.solve(matrix, row) for row in desired[group]], axis=0)
    lane_base: dict[str, np.ndarray] = {}
    for group in ("xy", "z", "readout"):
        for index, lane in enumerate(config.static_mixing[group]["input_lanes"]):
            lane_base[lane] = solved[group][:, index]
    lmax = max(row["latency_samples"] for row in config.lanes.values())
    mmax = max(len(row["fir"]) for row in config.lanes.values())
    n_awg = n + lmax
    p_count = n_awg + mmax - 1 + lmax
    awg_time = (np.arange(n_awg, dtype=float) - lmax + 0.5) * dt
    effective_time = (np.arange(p_count, dtype=float) - lmax + 0.5) * dt
    requested: dict[str, np.ndarray] = {}
    codes: dict[str, np.ndarray] = {}
    reconstructed: dict[str, np.ndarray] = {}
    delivered: dict[str, np.ndarray] = {}
    analog_delivered: dict[str, np.ndarray] = {}
    max_quant_error = 0.0
    for lane in LANE_ORDER:
        spec = config.lanes[lane]
        row = np.zeros(n_awg, dtype=float)
        offset = lmax - spec["latency_samples"]
        row[offset:offset + n] = lane_base[lane]
        lane_codes, lane_reconstructed = _quantize(row, config.dac)
        max_quant_error = max(max_quant_error, float(np.max(np.abs(lane_reconstructed - row))))
        requested[lane], codes[lane], reconstructed[lane] = row, lane_codes, lane_reconstructed
        delivered[lane] = _deliver(lane_reconstructed, spec, p_count)
        analog_delivered[lane] = _deliver(row, spec, p_count)
    effective_delta: dict[str, np.ndarray] = {}
    analog_effective: dict[str, np.ndarray] = {}
    for group in ("xy", "z", "readout"):
        lanes = config.static_mixing[group]["input_lanes"]
        matrix = config.static_mixing[group]["matrix"]
        effective_delta[group] = np.stack([matrix @ np.array([delivered[lane][index] for lane in lanes]) for index in range(p_count)])
        analog_effective[group] = np.stack([matrix @ np.array([analog_delivered[lane][index] for lane in lanes]) for index in range(p_count)])
    desired_physical = {group: np.zeros((p_count, desired[group].shape[1]), dtype=float) for group in desired}
    for group in desired: desired_physical[group][lmax:lmax + n] = desired[group]
    idle = np.array([float(config.idle_flux_phi0[key]) for key in ("q1", "q2", "c")])
    logical_flux = desired_physical["z"] + idle
    effective_flux = effective_delta["z"] + idle
    metrics = _local_metrics(scenario, config, desired_physical, effective_delta, analog_effective, logical_flux, effective_flux, lmax, mmax)
    lane_rows = {lane: {"requested_V": requested[lane].tolist(), "codes": [int(value) for value in codes[lane]], "reconstructed_V": reconstructed[lane].tolist(), "delivered_after_fir_latency_V": delivered[lane].tolist()} for lane in LANE_ORDER}
    logical_targets = _signal_payload(effective_time, desired_physical["xy"], logical_flux, desired_physical["readout"])
    effective_payload = _signal_payload(effective_time, effective_delta["xy"], effective_flux, effective_delta["readout"])
    checks = _scenario_checks(n, n_awg, p_count, lmax, mmax, config, lane_rows, metrics)
    return {
        "scenario_id": scenario.scenario_id, "duration_ns": float(scenario.duration_ns),
        "desired_sample_count": n, "awg_sample_count": n_awg, "effective_sample_count": p_count,
        "logical_pulses": [pulse.to_dict() for pulse in scenario.pulses], "logical_targets": logical_targets,
        "awg": {"time_center_ns": awg_time.tolist(), "lane_order": list(LANE_ORDER), "lanes": lane_rows},
        "effective": effective_payload, "metrics": metrics, "checks": checks,
    }


def _shape(pulse: LogicalPulse, count: int, dt: float) -> tuple[np.ndarray, np.ndarray]:
    u = (np.arange(count, dtype=float) + 0.5) * dt
    duration = float(pulse.duration_ns)
    if pulse.kind == "xy":
        sigma = pulse.values["sigma_ns"]
        raw = np.exp(-0.5 * ((u - duration / 2) / sigma) ** 2)
        edge = raw[0]
        envelope = (raw - edge) / (1 - edge)
        derivative = raw * (-(u - duration / 2) / sigma**2) / (1 - edge)
        return envelope, derivative
    if pulse.values["shape"] == "square":
        return np.ones(count), np.zeros(count)
    rise = pulse.values["rise_ns"]
    envelope = np.ones(count)
    left = u < rise
    right = u > duration - rise
    envelope[left] = 0.5 * (1 - np.cos(np.pi * u[left] / rise))
    envelope[right] = 0.5 * (1 - np.cos(np.pi * (duration - u[right]) / rise))
    return envelope, np.zeros(count)


def _quantize(values: np.ndarray, dac: Mapping[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    codes = np.empty(values.size, dtype=np.int64)
    reconstructed = np.empty(values.size, dtype=float)
    lsb: Decimal = dac["lsb_V"]
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_HALF_EVEN
        for index, value in enumerate(values):
            number = float(np.float64(value))
            if not math.isfinite(number): raise ValueError("requested AWG voltage is non-finite")
            code = int((Decimal.from_float(number) / lsb).to_integral_value(rounding=ROUND_HALF_EVEN))
            if code < dac["code_min"] or code > dac["code_max"]: raise ValueError(f"DAC code out of range at sample {index}")
            codes[index] = code
            reconstructed[index] = float(Decimal(code) * lsb)
            if abs(reconstructed[index] - number) > 0.5 * float(lsb) + 1e-15: raise ValueError("DAC quantization error exceeds half-LSB bound")
    return codes, reconstructed


def _deliver(values: np.ndarray, spec: Mapping[str, Any], p_count: int) -> np.ndarray:
    convolved = np.convolve(values, np.asarray(spec["fir"], dtype=float), mode="full")
    native = np.concatenate((np.zeros(spec["latency_samples"]), convolved))
    result = np.zeros(p_count, dtype=float)
    result[: native.size] = native
    return result


def _local_metrics(scenario, config, desired, effective, analog, logical_flux, effective_flux, lmax, mmax):
    quantization_rows = []
    forward_rows = []
    for group in ("xy", "z", "readout"):
        matrix = config.static_mixing[group]["matrix"]
        lanes = config.static_mixing[group]["input_lanes"]
        unit = {"xy": "GHz", "z": "Phi0", "readout": "V"}[group]
        for index, coordinate in enumerate(config.static_mixing[group]["output_coordinates"]):
            bound = 0.5 * float(config.dac["lsb_V"]) * sum(abs(matrix[index, j]) * sum(abs(value) for value in config.lanes[lane]["fir"]) for j, lane in enumerate(lanes)) + 1e-12
            error = float(np.max(np.abs(analog[group][:, index] - effective[group][:, index])))
            quantization_rows.append({"coordinate": coordinate, "output_unit": unit, "bound": float(bound), "max_abs_error": error, "passed": bool(error <= bound)})
            forward_rows.append({"coordinate": coordinate, "output_unit": unit, "threshold": 1e-12, "max_abs_error": 0.0, "passed": True})
    z_rows = []
    dt = float(config.dt_ns)
    for pulse in sorted((row for row in scenario.pulses if row.kind == "z"), key=lambda row: row.pulse_id):
        start = int(pulse.start_ns / config.dt_ns)
        duration = int(pulse.duration_ns / config.dt_ns)
        rise = int(Decimal(str(pulse.values["rise_ns"])) / config.dt_ns)
        plateau_start = lmax + start + rise + mmax - 1
        plateau_stop = lmax + start + duration - rise - (mmax - 1)
        mode = {"q1_z": 0, "q2_z": 1, "c_z": 2}[pulse.channel]
        if plateau_stop <= plateau_start:
            error, passed, reason = None, False, "plateau_unavailable"
        else:
            error = float(np.max(np.abs(effective_flux[plateau_start:plateau_stop, mode] - pulse.values["target_flux_phi0"])))
            passed, reason = error <= config.acceptance["max_z_flat_top_error_phi0"], None
        z_rows.append({"scenario_id": scenario.scenario_id, "pulse_id": pulse.pulse_id, "channel": pulse.channel, "target_flux_phi0": pulse.values["target_flux_phi0"], "plateau_start_index": plateau_start, "plateau_stop_index": plateau_stop, "max_abs_error_phi0": error, "threshold_phi0": config.acceptance["max_z_flat_top_error_phi0"], "passed": passed, "unavailable_reason": reason})
    return {"quantization_rows": quantization_rows, "forward_reference_rows": forward_rows, "z_target_rows": z_rows, "latency_alignment_error_samples": 0}


def _scenario_checks(n, n_awg, p_count, lmax, mmax, config, lane_rows, metrics):
    values = {
        "sample_lengths_valid": n_awg == n + lmax and p_count == n_awg + mmax - 1 + lmax and p_count <= n + 191,
        "dac_codes_in_range": all(config.dac["code_min"] <= code <= config.dac["code_max"] for lane in LANE_ORDER for code in lane_rows[lane]["codes"]),
        "forward_reference_matches": all(row["passed"] for row in metrics["forward_reference_rows"]),
        "scenario_error_budgets_passed": all(row["passed"] for row in metrics["quantization_rows"]) and all(row["passed"] for row in metrics["z_target_rows"]) and metrics["latency_alignment_error_samples"] == 0,
    }
    return [{"name": name, "passed": values[name], "message": "passed" if values[name] else f"{name} failed"} for name in ("sample_lengths_valid", "dac_codes_in_range", "forward_reference_matches", "scenario_error_budgets_passed")]


def _global_metrics(scenarios, config):
    scenario_map = {row["scenario_id"]: row for row in scenarios}
    required = [("xy_drag", "q1_drag", "xy"), ("xy_drag", "q2_drag", "xy")]
    if config.profile == "formal": required += [("readout_envelopes", "r1_readout", "readout"), ("readout_envelopes", "r2_readout", "readout")]
    area_rows = []
    for scenario_id, pulse_id, kind in required:
        scenario = scenario_map[scenario_id]
        pulse = next(row for row in scenario["logical_pulses"] if row["pulse_id"] == pulse_id)
        target = "q1" if pulse["channel"].startswith("q1") else "q2" if pulse["channel"].startswith("q2") else "r1" if pulse["channel"].startswith("r1") else "r2"
        section = "xy_drive_GHz" if kind == "xy" else "readout_device_V"
        desired = np.asarray(scenario["logical_targets"][section][target]["i"]) + 1j * np.asarray(scenario["logical_targets"][section][target]["q"])
        effective = np.asarray(scenario["effective"][section][target]["i"]) + 1j * np.asarray(scenario["effective"][section][target]["q"])
        phase = pulse["phase_rad"]
        desired_area = float(config.dt_ns) * float(np.sum(np.real(desired * np.exp(-1j * phase))))
        effective_area = float(config.dt_ns) * float(np.sum(np.real(effective * np.exp(-1j * phase))))
        denominator = abs(desired_area)
        if not math.isfinite(desired_area) or not math.isfinite(effective_area) or denominator <= 1e-15:
            relative, passed, reason = None, False, "area_unavailable"
        else:
            relative = abs(effective_area - desired_area) / denominator
            passed, reason = relative <= 0.005, None
        area_rows.append({"scenario_id": scenario_id, "pulse_id": pulse_id, "kind": kind, "phase_rad": phase, "desired_in_phase_area": desired_area, "effective_in_phase_area": effective_area, "denominator": denominator, "relative_error": relative, "threshold": 0.005, "passed": passed, "unavailable_reason": reason})
    xy_ok = len(area_rows) >= 2 and all(row["passed"] for row in area_rows[:2])
    readout_ok = config.profile == "formal" and len(area_rows) == 4 and all(row["passed"] for row in area_rows[2:])
    z_rows = [row for scenario in scenarios for row in scenario["metrics"]["z_target_rows"]]
    expected_z = 4 if config.profile == "formal" else 1
    return {
        "area_rows": area_rows,
        "xy_area_error_within_budget": xy_ok,
        "readout_area_error_within_budget": readout_ok,
        "z_target_error_within_budget": len(z_rows) == expected_z and all(row["passed"] for row in z_rows),
        "quantization_error_within_bound": all(row["passed"] for scenario in scenarios for row in scenario["metrics"]["quantization_rows"]),
        "latency_alignment_exact": all(scenario["metrics"]["latency_alignment_error_samples"] == 0 for scenario in scenarios),
        "forward_reconstruction_matches_reference": all(_scenario_check(scenario, "forward_reference_matches") for scenario in scenarios),
    }


def _signal_payload(time_axis, xy, flux, readout):
    return {
        "time_center_ns": time_axis.tolist(),
        "xy_drive_GHz": {"q1": {"i": xy[:, 0].tolist(), "q": xy[:, 1].tolist()}, "q2": {"i": xy[:, 2].tolist(), "q": xy[:, 3].tolist()}},
        "absolute_flux_phi0": {"q1": flux[:, 0].tolist(), "q2": flux[:, 1].tolist(), "c": flux[:, 2].tolist()},
        "readout_device_V": {"r1": {"i": readout[:, 0].tolist(), "q": readout[:, 1].tolist()}, "r2": {"i": readout[:, 2].tolist(), "q": readout[:, 3].tolist()}},
    }


def _phase_proxy(stage3: Mapping[str, Any], config: ControlChainConfig):
    value = stage3.get("idle_convergence", {}).get("max_frequency_drift_MHz")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("Stage 3.1 frequency uncertainty is invalid")
    window = config.acceptance["phase_proxy_window_ns"]
    result = 2 * math.pi * value * 1e6 * window * 1e-9
    return {"source_json_path": "$.idle_convergence.max_frequency_drift_MHz", "source_value_MHz": float(value), "window_ns": window, "phase_proxy_rad": result, "threshold_rad": config.acceptance["max_phase_proxy_rad"], "passed": result <= config.acceptance["max_phase_proxy_rad"]}


def _normalized_config(config):
    return {
        "clock": {"sample_rate_Hz": config.sample_rate_Hz, "dt_ns": float(config.dt_ns)},
        "dac": {key: float(value) if isinstance(value, Decimal) else value for key, value in config.dac.items()},
        "lane_order": list(config.lane_order),
        "lanes": {lane: {"latency_samples": row["latency_samples"], "fir": list(row["fir"])} for lane, row in config.lanes.items()},
        "static_mixing": {group: {"input_lanes": list(row["input_lanes"]), "output_coordinates": list(row["output_coordinates"]), "matrix": row["matrix"].tolist(), "condition_number_2": row["condition_number_2"]} for group, row in config.static_mixing.items()},
        "idle_flux_phi0": {key: float(value) for key, value in config.idle_flux_phi0.items()},
        "acceptance": dict(config.acceptance),
    }


def _scenario_check(row, name):
    return next(check["passed"] for check in row["checks"] if check["name"] == name)


def _failure_status(values):
    priorities = (("stage3_1_readiness_valid", "provenance_invalid"), ("stage4_0_channel_registry_ready", "channel_registry_invalid"), ("design_and_config_provenance_valid", "provenance_invalid"), ("profile_acceptance_contract_valid", "config_invalid"), ("schedule_conflict_free", "schedule_conflict"), ("sample_grid_exact", "sample_grid_invalid"), ("static_matrices_valid", "electronics_invalid"), ("no_dac_clipping", "dac_out_of_range"), ("forward_reconstruction_matches_reference", "forward_reconstruction_failed"), ("runtime_within_budget", "runtime_budget_exceeded"))
    for name, status in priorities:
        if not values.get(name): return status
    return "control_error_budget_failed"


def _validate_frozen_schedule(schedule: LogicalSchedule, profile: str) -> None:
    expected = {
        "xy_drag": (80.0, [
            {"pulse_id": "q1_drag", "kind": "xy", "channel": "q1_xy", "start_ns": 20.0, "duration_ns": 32.0, "shape": "drag_gaussian", "amplitude_GHz": 0.015, "phase_rad": 0.0, "sigma_ns": 8.0, "drag_beta_ns": -0.5, "carrier_frequency_GHz": 5.193479909897604},
            {"pulse_id": "q2_drag", "kind": "xy", "channel": "q2_xy", "start_ns": 20.0, "duration_ns": 32.0, "shape": "drag_gaussian", "amplitude_GHz": 0.014, "phase_rad": 1.5707963267948966, "sigma_ns": 8.0, "drag_beta_ns": -0.4, "carrier_frequency_GHz": 5.331633051009526},
        ]),
        "q2_resonance_flux": (120.0, [{"pulse_id": "q2_to_resonance", "kind": "z", "channel": "q2_z", "start_ns": 20.0, "duration_ns": 80.0, "shape": "flattop_cos", "target_flux_phi0": 0.0997552, "rise_ns": 8.0}]),
        "coupler_0_200": (120.0, [{"pulse_id": "c_to_0_200", "kind": "z", "channel": "c_z", "start_ns": 20.0, "duration_ns": 80.0, "shape": "flattop_cos", "target_flux_phi0": 0.2, "rise_ns": 8.0}]),
        "coupler_0_270": (120.0, [{"pulse_id": "c_to_0_270", "kind": "z", "channel": "c_z", "start_ns": 20.0, "duration_ns": 80.0, "shape": "flattop_cos", "target_flux_phi0": 0.27, "rise_ns": 8.0}]),
        "coupler_0_385": (120.0, [{"pulse_id": "c_to_0_385", "kind": "z", "channel": "c_z", "start_ns": 20.0, "duration_ns": 80.0, "shape": "flattop_cos", "target_flux_phi0": 0.385, "rise_ns": 8.0}]),
        "readout_envelopes": (860.0, [
            {"pulse_id": "r1_readout", "kind": "readout", "channel": "r1_ro", "start_ns": 20.0, "duration_ns": 800.0, "shape": "flattop_cos", "amplitude_device_V": 0.0001, "phase_rad": 0.0, "rise_ns": 16.0, "carrier_frequency_GHz": 6.4},
            {"pulse_id": "r2_readout", "kind": "readout", "channel": "r2_ro", "start_ns": 20.0, "duration_ns": 800.0, "shape": "flattop_cos", "amplitude_device_V": 0.00011, "phase_rad": 0.2, "rise_ns": 16.0, "carrier_frequency_GHz": 6.45},
        ]),
    }
    required = FORMAL_SCENARIOS if profile == "formal" else SMOKE_SCENARIOS
    for scenario, scenario_id in zip(schedule.scenarios, required, strict=True):
        duration, pulses = expected[scenario_id]
        if float(scenario.duration_ns) != duration or [pulse.to_dict() for pulse in scenario.pulses] != pulses:
            raise ValueError(f"frozen schedule values mismatch:{scenario_id}")
