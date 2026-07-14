"""Stage 5 v0.2 tracked-input reconstruction and capability narrowing."""

from __future__ import annotations

from dataclasses import replace
import importlib.metadata
import platform
from pathlib import Path
import sys
import time
from typing import Any, Mapping

import numpy as np

from sqvm.control import compile_control_schedule, load_control_channel_registry, load_control_chain_config, load_logical_schedule, validate_logical_schedule
from sqvm.control.stage4_models import ControlBuildContext
from sqvm.device.capacitance import build_capacitance_matrix
from sqvm.device.junction import resolve_junction_parameters
from sqvm.device.spec import load_device
from sqvm.evolution.config import admit_stage5_config_paths
from sqvm.evolution.models import EffectiveScenario, RebuiltModel, Stage5Input
from sqvm.hamiltonian import BasisConfig, DeviceArtifacts, build_ec_matrix, build_mode_capacitance_matrix, build_mode_transform, load_hamiltonian_config
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256


PHASE_PROXY_MHZ = 0.09736560961925989
PHASE_PROXY_RAD = 0.01957651736909815
ACCEPTANCE_BASIS = "user_accepted_rebuild_snapshot_v1"
Q2_RESONANCE_INDEX_66_TRIPLE = {
    "q1": 0.10000162139892578,
    "q2": 0.00016910485839843748,
    "c": 0.26999850549316406,
}


def load_stage5_input(config_path: str | Path, repository_root: str | Path | None = None) -> Stage5Input:
    """Reconstruct the accepted numerical inputs before any evolution output exists."""

    admission = admit_stage5_config_paths(config_path, repository_root)
    _validate_bound_records(admission)
    for name, path in admission.resolved_inputs.items():
        if not path.is_file():
            raise ValueError(f"tracked Stage 5 input is missing: {name}")

    config = admission.config
    control = load_control_chain_config(admission.resolved_inputs["stage4_control_config"])
    if control.profile != "formal":
        raise ValueError("Stage 5 reconstruction must compile the accepted formal Stage 4 chain")
    schedule = load_logical_schedule(admission.resolved_inputs["stage4_logical_schedule"], dt_ns=control.dt_ns)
    registry = load_control_channel_registry(admission.resolved_inputs["stage4_channel_registry"])
    schedule_report = validate_logical_schedule(schedule, registry, control)
    if not schedule_report.ok:
        raise ValueError("accepted Stage 4 schedule does not validate: " + "; ".join(schedule_report.errors))
    compiled = compile_control_schedule(schedule, control, _control_context(admission, registry))
    if not compiled.payload["computational_gate"]["computational_ready"]:
        raise ValueError("tracked Stage 4 reconstruction failed its computational gate")
    flux_probe = _validate_q2_resonance_flux_probe(compiled.payload["scenarios"], control)
    scenarios = _extract_capability(compiled.payload["scenarios"], config.scenario_ids)
    rebuilt_model = _rebuild_model(admission)
    snapshot = _snapshot(admission, scenarios, flux_probe)
    snapshot_sha256 = _sha256(snapshot)
    snapshot = {**snapshot, "snapshot_sha256": snapshot_sha256}
    return Stage5Input(admission, snapshot, snapshot_sha256, scenarios, rebuilt_model)


def _validate_bound_records(admission) -> None:
    decision = admission.resolved_inputs["user_acceptance_decision"]
    amendment = admission.resolved_inputs["stage5_amendment"]
    phase = admission.resolved_inputs["phase_proxy_document"]
    if decision.name != "2026-07-14-stage2-stage4-user-acceptance.md" or amendment.name != "2026-07-14-stage5-v0-2-amendment.md":
        raise ValueError("Stage 5 v0.2 decision paths are not the accepted records")
    if phase.name != "04_control_signal_design.md":
        raise ValueError("phase-proxy source must be the accepted Stage 4 design")
    try:
        decision_text = decision.read_text(encoding="utf-8")
        amendment_text = amendment.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"cannot read Stage 5 decision record: {exc}") from exc
    if "Decision: accepted" not in decision_text or "stage5_input_snapshot" not in decision_text:
        raise ValueError("user acceptance decision does not authorize reconstructed input snapshot")
    if "deterministic, fail-closed `stage5_input_snapshot`" not in amendment_text:
        raise ValueError("Stage 5 v0.2 amendment is not the authorized admission rule")


def _control_context(admission, registry) -> ControlBuildContext:
    registry_payload = {
        "channel_order": [row.name for row in registry.channels],
        "channels": {row.name: {**row.to_dict(), "origin": "stage5_reconstructed"} for row in registry.channels},
    }
    provenance = {
        "stage3_1_readiness_valid": True,
        "stage4_0_channel_registry_ready": True,
        "design_and_config_provenance_valid": True,
        "artifact_provenance": {
            "acceptance_basis": ACCEPTANCE_BASIS,
            "paths": {key: path.relative_to(admission.repository_root).as_posix() for key, path in admission.resolved_inputs.items()},
            "sha256": {key: raw_file_sha256(path) for key, path in admission.resolved_inputs.items()},
        },
    }
    stage3 = {"idle_convergence": {"max_frequency_drift_MHz": PHASE_PROXY_MHZ}}
    return ControlBuildContext(admission.repository_root, provenance, registry_payload, stage3, time.perf_counter())


def _extract_capability(rows: Any, scenario_ids: tuple[str, ...]) -> dict[str, EffectiveScenario]:
    by_name = {row.get("scenario_id"): row for row in rows if isinstance(row, Mapping)}
    carrier_source = by_name.get("xy_drag")
    if not isinstance(carrier_source, Mapping):
        raise ValueError("Stage 4 reconstruction omitted xy_drag carrier source")
    carrier_map = _carrier_metadata(carrier_source, "xy_drag")
    result: dict[str, EffectiveScenario] = {}
    for scenario_id in scenario_ids:
        row = by_name.get(scenario_id)
        if not isinstance(row, Mapping):
            raise ValueError(f"Stage 4 reconstruction omitted required scenario: {scenario_id}")
        effective = row.get("effective")
        if not isinstance(effective, Mapping):
            raise ValueError(f"Stage 4 effective controls are missing: {scenario_id}")
        # Deliberately construct a new narrow object: no readout field reaches Stage 5.
        centers = _finite_array(effective.get("time_center_ns"), f"{scenario_id}.time_center_ns")
        _validate_centers(centers)
        xy = effective.get("xy_drive_GHz")
        flux = effective.get("absolute_flux_phi0")
        if not isinstance(xy, Mapping) or set(xy) != {"q1", "q2"} or not isinstance(flux, Mapping) or set(flux) != {"q1", "q2", "c"}:
            raise ValueError(f"Stage 4 capability names are invalid: {scenario_id}")
        iq = {
            mode: (_finite_array(xy[mode].get("i") if isinstance(xy[mode], Mapping) else None, f"{scenario_id}.{mode}.i"), _finite_array(xy[mode].get("q") if isinstance(xy[mode], Mapping) else None, f"{scenario_id}.{mode}.q"))
            for mode in ("q1", "q2")
        }
        flux_arrays = {mode: _finite_array(flux[mode], f"{scenario_id}.flux.{mode}") for mode in ("q1", "q2", "c")}
        if any(values.size != centers.size for pair in iq.values() for values in pair) or any(values.size != centers.size for values in flux_arrays.values()):
            raise ValueError(f"Stage 4 effective control lengths differ: {scenario_id}")
        carrier = carrier_map
        result[scenario_id] = EffectiveScenario(scenario_id, centers, iq, flux_arrays, {key: value[0] for key, value in carrier.items()}, {key: value[1] for key, value in carrier.items()})
    return result


def _carrier_metadata(row: Mapping[str, Any], scenario_id: str) -> dict[str, tuple[float, float]]:
    pulses = row.get("logical_pulses")
    found: dict[str, tuple[float, float]] = {}
    if not isinstance(pulses, list):
        raise ValueError(f"Stage 4 pulse metadata is missing: {scenario_id}")
    for pulse in pulses:
        if not isinstance(pulse, Mapping) or pulse.get("kind") != "xy":
            continue
        channel = pulse.get("channel")
        mode = {"q1_xy": "q1", "q2_xy": "q2"}.get(channel)
        if mode is None:
            continue
        frequency, phase = pulse.get("carrier_frequency_GHz"), pulse.get("phase_rad")
        if isinstance(frequency, bool) or not isinstance(frequency, (int, float)) or isinstance(phase, bool) or not isinstance(phase, (int, float)) or not np.isfinite(frequency) or not np.isfinite(phase):
            raise ValueError(f"Stage 4 XY carrier metadata is invalid: {scenario_id}.{mode}")
        candidate = (float(frequency), float(phase))
        if mode in found and found[mode] != candidate:
            raise ValueError(f"Stage 4 XY carrier metadata is ambiguous: {mode}")
        found[mode] = candidate
    if set(found) != {"q1", "q2"}:
        raise ValueError("Stage 4 xy_drag must carry one carrier per qubit")
    return found


def _rebuild_model(admission) -> RebuiltModel:
    device = load_device(admission.resolved_inputs["device_config"])
    capacitance = build_capacitance_matrix(device)
    junctions = resolve_junction_parameters(device)
    payload = {
        "capacitance_matrix": {"nodes": list(capacitance.nodes), "matrix_fF": [list(row) for row in capacitance.matrix_fF]},
        "junction_parameters": [{"component": row.component, "junction": row.junction, "rn_ohm": row.rn_ohm, "ej_GHz": row.ej_GHz, "source": row.source} for row in junctions.rows],
        "components": {name: {"squid": {"flux_bias_phi0": component.squid.flux_bias_phi0}} for name, component in device.components.items() if component.squid is not None},
    }
    artifacts = DeviceArtifacts(admission.resolved_inputs["device_config"], payload)
    transform = build_mode_transform(artifacts)
    ec = build_ec_matrix(build_mode_capacitance_matrix(artifacts, transform))
    hamiltonian = load_hamiltonian_config(admission.resolved_inputs["hamiltonian_config"])
    cutoffs = dict(zip(("q1", "c", "q2"), admission.config.charge_cutoffs, strict=True))
    return RebuiltModel(artifacts, replace(hamiltonian, basis=BasisConfig(cutoffs)), ec.matrix_GHz)


def _validate_q2_resonance_flux_probe(rows: Any, control) -> dict[str, Any]:
    by_name = {row.get("scenario_id"): row for row in rows if isinstance(row, Mapping)}
    row = by_name.get("q2_resonance_flux")
    if not isinstance(row, Mapping) or not isinstance(row.get("effective"), Mapping):
        raise ValueError("formal Stage 4 reconstruction omitted q2_resonance_flux effective controls")
    flux = row["effective"].get("absolute_flux_phi0")
    if not isinstance(flux, Mapping) or set(flux) != {"q1", "q2", "c"}:
        raise ValueError("q2_resonance_flux full flux triple is invalid")
    arrays = {mode: _finite_array(flux[mode], f"q2_resonance_flux.flux.{mode}") for mode in ("q1", "q2", "c")}
    if len({array.size for array in arrays.values()}) != 1 or arrays["q1"].size <= 66:
        raise ValueError("q2_resonance_flux lacks the required 0/66 full-flux probe indexes")
    idle = {mode: float(control.idle_flux_phi0[mode]) for mode in ("q1", "q2", "c")}
    at_zero = {mode: float(arrays[mode][0]) for mode in ("q1", "q2", "c")}
    if at_zero != idle:
        raise ValueError("q2_resonance_flux index 0 does not match the reconstructed named idle triple")
    at_sixty_six = {mode: float(arrays[mode][66]) for mode in ("q1", "q2", "c")}
    for mode, expected in Q2_RESONANCE_INDEX_66_TRIPLE.items():
        if at_sixty_six[mode].hex() != expected.hex():
            raise ValueError(f"q2_resonance_flux index 66 {mode} does not match the frozen binary64 triple")
    return {
        "scenario_id": "q2_resonance_flux",
        "indexes": [0, 66],
        "mode_order": ["q1", "q2", "c"],
        "absolute_flux_phi0": {"index_0": at_zero, "index_66": at_sixty_six},
    }


def _snapshot(admission, scenarios: Mapping[str, EffectiveScenario], flux_probe: Mapping[str, Any]) -> dict[str, Any]:
    bound = {key: path.relative_to(admission.repository_root).as_posix() for key, path in admission.resolved_inputs.items()}
    bound["stage5_config"] = admission.config.source_path.relative_to(admission.repository_root).as_posix()
    source_paths = [
        admission.repository_root / "pyproject.toml",
        admission.repository_root / "src" / "sqvm" / "__init__.py",
        admission.repository_root / "src" / "sqvm" / "__main__.py",
    ]
    for package in ("device", "hamiltonian", "control", "evolution"):
        source_paths.extend((admission.repository_root / "src" / "sqvm" / package).rglob("*.py"))
    source_rows = sorted(
        ((path.relative_to(admission.repository_root).as_posix(), path) for path in set(source_paths) if path.is_file()),
        key=lambda row: row[0].encode("utf-8"),
    )
    source_hashes = {relative: raw_file_sha256(path) for relative, path in source_rows}
    controls = {
        name: {
            "time_center_ns": scenario.time_center_ns.tolist(),
            "xy_drive_GHz": {mode: {"i": pair[0].tolist(), "q": pair[1].tolist()} for mode, pair in scenario.xy_iq_GHz.items()},
            "absolute_flux_phi0": {mode: values.tolist() for mode, values in scenario.absolute_flux_phi0.items()},
            "carrier_frequency_GHz": dict(scenario.carrier_frequency_GHz),
            "carrier_phase_rad": dict(scenario.carrier_phase_rad),
        }
        for name, scenario in scenarios.items()
    }
    return {
        "schema_version": "0.2",
        "artifact_type": "stage5_input_snapshot",
        "artifact_version": "0.2",
        "acceptance_basis": ACCEPTANCE_BASIS,
        "bound_paths": bound,
        "bound_sha256": {**{key: raw_file_sha256(path) for key, path in admission.resolved_inputs.items()}, "stage5_config": raw_file_sha256(admission.config.source_path)},
        "source_sha256": source_hashes,
        "phase_proxy": {"value_MHz": PHASE_PROXY_MHZ, "phase_proxy_rad": PHASE_PROXY_RAD, "source_path": bound["phase_proxy_document"], "source_sha256": raw_file_sha256(admission.resolved_inputs["phase_proxy_document"])},
        "environment": _environment(),
        "scenario_order": list(admission.config.scenario_ids),
        "q2_resonance_flux_probe": dict(flux_probe),
        "controls": controls,
    }


def _environment() -> dict[str, str]:
    packages = {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "qutip")}
    return {"python_executable": str(Path(sys.executable).resolve()), "python_version": platform.python_version(), "numpy_version": packages["numpy"], "scipy_version": packages["scipy"], "qutip_version": packages["qutip"]}


def _sha256(value: Mapping[str, Any]) -> str:
    import hashlib
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _finite_array(value: Any, label: str) -> np.ndarray:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    array = np.asarray(value, dtype=float)
    if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
        raise ValueError(f"{label} must be a non-empty finite vector")
    return array


def _validate_centers(centers: np.ndarray) -> None:
    if np.any(np.diff(centers) <= 0.0) or not np.allclose(np.diff(centers), 0.5, rtol=0.0, atol=0.0):
        raise ValueError("Stage 4 centers must be strict ascending with exact 0.5 ns spacing")
