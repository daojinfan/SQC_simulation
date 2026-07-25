"""Resolve one immutable Active PlatformConfiguration into QCIS authorities."""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

from sqvm.circuits import CircuitExecutionContext
from sqvm.qcis.canonical import sha256_json
from sqvm.web.configuration_schema import (
    initial_simulation_configuration,
    load_frozen_schema,
    project_wave_indices,
    validate_document,
)
from sqvm.web.configuration_transactions import (
    ConfigurationTransactionError,
    ConfigurationTransactionManager,
)


# The selector remains part of the public SET path.  Its chosen record is
# resolved from the already validated authority, so callers cannot name a
# setting ID or widen the allowed numeric surface through a request.
_SETTABLE_NUMERIC_WAVEFORM_FIELDS = {
    "active_xy2_setting": frozenset({"amplitude_GHz"}),
}


class PlatformAuthorityResolutionError(ValueError):
    pass


class PlatformAuthorityResolver:
    """The sole production bridge from an Active snapshot to QCIS v0.3."""

    def __init__(self, repository_root: str | Path, storage_root: str | Path | None = None) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.schema = load_frozen_schema(self.repository_root)
        self.root = (Path(storage_root).resolve() if storage_root else self.repository_root / "output" / "platform-configurations")

    def resolve(self, device_id: str = "demo_2q1c2r") -> CircuitExecutionContext:
        authority_root = self.root
        transactions = ConfigurationTransactionManager(self.root)
        if transactions.head_exists(device_id):
            try:
                authority_root = transactions.committed_view(device_id).projection_root
            except ConfigurationTransactionError as exc:
                raise PlatformAuthorityResolutionError(
                    f"configuration transaction authority is invalid: {exc}"
                ) from exc
        pointer = self._load(
            authority_root / "active" / f"{device_id}.json",
            "active pointer",
        )
        if pointer.get("device_id") != device_id:
            raise PlatformAuthorityResolutionError("Active pointer device does not match request")
        snapshot_id = pointer.get("snapshot_id")
        if not isinstance(snapshot_id, str):
            raise PlatformAuthorityResolutionError("Active pointer snapshot id is invalid")
        snapshot = self._load(
            authority_root / "snapshots" / snapshot_id / "snapshot.json",
            "active snapshot",
        )
        if snapshot.get("snapshot_id") != snapshot_id or snapshot.get("device_id") != device_id:
            raise PlatformAuthorityResolutionError("Active pointer does not resolve its snapshot")
        if snapshot.get("content_sha256") != pointer.get("snapshot_content_sha256") or snapshot.get("content_sha256") != sha256_json(snapshot.get("editable")):
            raise PlatformAuthorityResolutionError("Active snapshot content hash mismatch")
        if snapshot.get("experiment_eligible") is not True or snapshot.get("requires_requalification") is not False:
            raise PlatformAuthorityResolutionError("Active snapshot is not eligible for execution")
        errors = validate_document(snapshot)
        if errors:
            raise PlatformAuthorityResolutionError(f"Active snapshot schema is invalid: {errors[0]['path']}")
        device = self._device(snapshot)
        calibration = snapshot["editable"]["calibration_values"]
        if not calibration:
            raise PlatformAuthorityResolutionError("uninitialized snapshot cannot resolve a compiler authority")
        authorities = self._authorities(snapshot, device, calibration)
        frozen = _freeze(authorities)
        simulation = snapshot["editable"]["control_values"].get(
            "simulation",
            initial_simulation_configuration(),
        )
        model_configuration = simulation.get("calibration_model") if isinstance(simulation, Mapping) else None
        if not isinstance(model_configuration, Mapping):
            raise PlatformAuthorityResolutionError("calibration simulation model is invalid")
        frozen_model_configuration = _freeze(model_configuration)
        settable_paths = self._settable_paths(frozen)
        context_hash = sha256_json(
            {
                "qcis_authorities": _plain(frozen),
                "calibration_model_configuration": _plain(frozen_model_configuration),
                "settable_paths": sorted(settable_paths),
            }
        )
        return CircuitExecutionContext(
            frozen,
            MappingProxyType({name: float(snapshot["editable"]["control_values"]["idle_flux_phi0"][name]) for name in ("q1", "q2", "c")} ),
            settable_paths,
            platform_snapshot_id=snapshot_id,
            platform_snapshot_content_sha256=snapshot["content_sha256"],
            authority_context_sha256=context_hash,
            calibration_model_configuration=frozen_model_configuration,
        )

    def _device(self, snapshot: Mapping[str, Any]) -> Mapping[str, Any]:
        ref = snapshot["readonly"]["device_ref"]
        relative = ref["path"]
        path = (self.repository_root / relative).resolve()
        try:
            path.relative_to(self.repository_root)
        except ValueError as exc:
            raise PlatformAuthorityResolutionError("device reference escapes repository") from exc
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest().upper() != str(ref["sha256"]).upper():
            raise PlatformAuthorityResolutionError("device authority hash mismatch")
        try:
            value = yaml.safe_load(raw.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            raise PlatformAuthorityResolutionError("device authority is unreadable") from exc
        device = value.get("device") if isinstance(value, Mapping) else None
        if not isinstance(device, Mapping) or device.get("topology") != "2q1c2r":
            raise PlatformAuthorityResolutionError("device topology is not 2q1c2r")
        components = device.get("components")
        if not isinstance(components, Mapping) or set(("q1", "q2", "c")) - set(components):
            raise PlatformAuthorityResolutionError("device components are incomplete")
        return device

    def _authorities(self, snapshot: Mapping[str, Any], device: Mapping[str, Any], calibration: Mapping[str, Any]) -> dict[str, Any]:
        refs = snapshot["readonly"]["authority_refs"]
        raw_device = (self.repository_root / snapshot["readonly"]["device_ref"]["path"]).read_bytes()
        if refs["device_sha256"].upper() != hashlib.sha256(raw_device).hexdigest().upper():
            raise PlatformAuthorityResolutionError("device reference and authority reference differ")
        profile = {"profile_id": "qcis_stage7_calibration_v3", "profile_version": "0.3"}
        compiler = {"compiler_id": "sqvm_qcis_compiler_v3"}
        if refs["instruction_profile_sha256"] != sha256_json(profile) or refs["compiler_snapshot_sha256"] != sha256_json(compiler):
            raise PlatformAuthorityResolutionError("frozen instruction or compiler authority hash mismatch")
        components = device["components"]
        qagents = {
            "Q1": {"component": "q1", "xy_channel": "q1_xy", "z_channel": "q1_z", "local_dimension": 3, "anharmonicity_GHz": float(device.get("priors", {}).get("q1", {}).get("estimated_anharmonicity_GHz", -0.25)), "reference_frequency_authority": copy.deepcopy(calibration["qagents"]["Q1"]["reference_frequency_authority"]), "flux_min_phi0": -1.0, "flux_max_phi0": 1.0},
            "Q2": {"component": "q2", "xy_channel": "q2_xy", "z_channel": "q2_z", "local_dimension": 3, "anharmonicity_GHz": float(device.get("priors", {}).get("q2", {}).get("estimated_anharmonicity_GHz", -0.25)), "reference_frequency_authority": copy.deepcopy(calibration["qagents"]["Q2"]["reference_frequency_authority"]), "flux_min_phi0": -1.0, "flux_max_phi0": 1.0},
            "C": {"component": "c", "z_channel": "c_flux", "endpoints": ["Q1", "Q2"]},
        }
        self._validate_raw_calibration(calibration)
        projected = project_wave_indices(calibration)
        authorities = {
            "instruction_profile": profile,
            "qagent_registry": qagents,
            "gate_configuration": projected["gate_configuration"],
            "waveform_registry": projected["waveform_registry"],
            "clock": copy.deepcopy(snapshot["editable"]["control_values"]["clock"]),
            "compiler": compiler,
        }
        self._validate_selected(authorities)
        authorities["expected_sha256"] = {name: sha256_json(authorities[name]) for name in ("instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")}
        return authorities

    @staticmethod
    def _settable_paths(authorities: Mapping[str, Any]) -> frozenset[str]:
        """Derive the fixed numeric SET policy from accepted selected settings."""

        gates = authorities.get("gate_configuration")
        registry = authorities.get("waveform_registry")
        settings = registry.get("settings") if isinstance(registry, Mapping) else None
        if not isinstance(gates, Mapping) or not isinstance(settings, Mapping):
            raise PlatformAuthorityResolutionError("settable-path authority is incomplete")
        paths: set[str] = set()
        for target in ("Q1", "Q2"):
            gate_config = gates.get(target)
            if not isinstance(gate_config, Mapping):
                raise PlatformAuthorityResolutionError(f"{target} gate configuration is invalid")
            for selector, fields in _SETTABLE_NUMERIC_WAVEFORM_FIELDS.items():
                setting_id = gate_config.get(selector)
                setting = settings.get(setting_id)
                if (
                    not isinstance(setting_id, str)
                    or not isinstance(setting, Mapping)
                    or setting.get("setting_id") != setting_id
                    or setting.get("target") != target
                    or setting.get("gate_type") != "XY2"
                    or setting.get("transition") != "01"
                    or setting.get("status") != "accepted"
                ):
                    raise PlatformAuthorityResolutionError(
                        f"{target}.{selector} is not an accepted XY2 setting"
                    )
                for field in fields:
                    value = setting.get(field)
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                        raise PlatformAuthorityResolutionError(
                            f"{target}.{selector}.{field} is not a finite numeric setting"
                        )
                    paths.add(f"{target}.setting.{selector}.{field}")
        return frozenset(paths)

    @staticmethod
    def _validate_raw_calibration(calibration: Mapping[str, Any]) -> None:
        for target in ("Q1", "Q2"):
            reference = calibration.get("qagents", {}).get(target, {}).get("reference_frequency_authority")
            if not isinstance(reference, Mapping) or reference.get("setting_hash") != sha256_json({key: value for key, value in reference.items() if key != "setting_hash"}):
                raise PlatformAuthorityResolutionError(f"{target} reference authority hash is invalid")
        registry = calibration.get("waveform_registry", {})
        for section, identifier in ((registry.get("settings", {}), "setting_id"), (registry.get("mappers", {}), "mapper_id")):
            if not isinstance(section, Mapping):
                raise PlatformAuthorityResolutionError("waveform registry is incomplete")
            for record_id, record in section.items():
                if not isinstance(record, Mapping) or record.get(identifier) != record_id or record.get("status") != "accepted" or record.get("setting_hash") != sha256_json({key: value for key, value in record.items() if key != "setting_hash"}):
                    raise PlatformAuthorityResolutionError(f"registry record {record_id} hash is invalid")

    def _validate_selected(self, authorities: Mapping[str, Any]) -> None:
        gates, registry = authorities["gate_configuration"], authorities["waveform_registry"]
        settings, mappers = registry.get("settings"), registry.get("mappers")
        if not isinstance(settings, Mapping) or not isinstance(mappers, Mapping):
            raise PlatformAuthorityResolutionError("waveform registry is incomplete")
        for records, identifier in ((settings, "setting_id"), (mappers, "mapper_id")):
            for record_id, record in records.items():
                if not isinstance(record, Mapping) or record.get(identifier) != record_id:
                    raise PlatformAuthorityResolutionError(f"registry key and {identifier} differ")
                if record.get("status") != "accepted":
                    raise PlatformAuthorityResolutionError(f"registry record {record_id} is not accepted")
        expected_settings = {"Q1": {"active_xy_setting": "XY", "active_xy2_setting": "XY2", "active_xy12_setting": "X12", "active_detune_setting": "DTN"}, "Q2": {"active_xy_setting": "XY", "active_xy2_setting": "XY2", "active_xy12_setting": "X12", "active_detune_setting": "DTN"}, "C": {"active_cz_setting": "CZ", "active_fsim_setting": "FSIM"}}
        for target, selections in expected_settings.items():
            for key, gate_type in selections.items():
                record = settings.get(gates[target].get(key))
                self._accepted(record, target, "gate_type", gate_type, key)
        for target in ("Q1", "Q2"):
            self._accepted(mappers.get(gates[target].get("active_f012zbias_mapper")), target, "mapper_type", "F012ZBIAS_MAPPER", "F012 mapper")
        self._accepted(mappers.get(gates["C"].get("active_g2zbias_mapper")), "C", "mapper_type", "G2ZBIAS_MAPPER", "G2 mapper")

    @staticmethod
    def _accepted(record: Any, target: str, type_name: str, type_value: str, label: str) -> None:
        if not isinstance(record, Mapping) or record.get("target") != target or record.get(type_name) != type_value or record.get("status") != "accepted":
            raise PlatformAuthorityResolutionError(f"selected {label} is not an accepted {type_value} record")

    @staticmethod
    def _load(path: Path, label: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PlatformAuthorityResolutionError(f"cannot load {label}") from exc
        if not isinstance(value, dict):
            raise PlatformAuthorityResolutionError(f"{label} must be an object")
        return value


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping): return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list): return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping): return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list): return [_plain(item) for item in value]
    return value


__all__ = ["PlatformAuthorityResolutionError", "PlatformAuthorityResolver"]
