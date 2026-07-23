"""Fail-closed PlatformConfiguration v0.2 validation and projections.

The frozen Draft 2020-12 schema is intentionally kept as the source artifact.
This module does not depend on the optional ``jsonschema`` package: it enforces
the closed PlatformConfiguration field tree directly and reports JSON paths
that the structured editor can display.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
import re
from typing import Any, Mapping

from sqvm.qcis.canonical import sha256_json


_SHA256 = re.compile(r"^[0-9A-Fa-f]{64}$")
_RECORD_ID = re.compile(r"^[a-z][a-z0-9_-]{1,95}$")
_WAVE_INDEX = {"rectangle": 0, "gaussian": 1, "flattop": 2, "acz": 5}
_CONTROL = {"clock", "dac", "lane_order", "lanes", "static_mixing", "idle_flux_phi0", "acceptance"}
_SIMULATION_FIELDS = {
    "charge_cutoffs",
    "retained_energy_levels",
    "convergence_charge_cutoffs",
    "convergence_retained_energy_levels",
}
_SIMULATION_MODES = {"q1", "c", "q2"}
_LANES = {"q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q", "q1_z", "q2_z", "c_z", "r1_ro_i", "r1_ro_q", "r2_ro_i", "r2_ro_q"}
_ACCEPTANCE = {"max_condition_number", "max_xy_area_relative_error", "max_readout_area_relative_error", "max_z_flat_top_error_phi0", "max_phase_proxy_rad", "phase_proxy_window_ns", "max_formal_samples_per_scenario", "analysis_runtime_budget_seconds", "total_runtime_budget_seconds"}
_MIXING_COORDINATES = {
    "xy": (["q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q"], ["q1_drive_i_GHz", "q1_drive_q_GHz", "q2_drive_i_GHz", "q2_drive_q_GHz"]),
    "z": (["q1_z", "q2_z", "c_z"], ["q1_delta_flux_phi0", "q2_delta_flux_phi0", "c_delta_flux_phi0"]),
    "readout": (["r1_ro_i", "r1_ro_q", "r2_ro_i", "r2_ro_q"], ["r1_device_i_V", "r1_device_q_V", "r2_device_i_V", "r2_device_q_V"]),
}


def frozen_schema_path(repository_root: str | Path) -> Path:
    path = Path(repository_root).resolve() / "docs" / "designs" / "07_1_3_platform_configuration_v0_2.schema.json"
    if not path.is_file():
        raise ValueError("frozen PlatformConfiguration v0.2 schema is unavailable")
    return path


def load_frozen_schema(repository_root: str | Path) -> dict[str, Any]:
    """Load and identify the frozen Draft 2020-12 authority artifact."""

    import json

    try:
        schema = json.loads(frozen_schema_path(repository_root).read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("frozen PlatformConfiguration v0.2 schema cannot be loaded") from exc
    if not isinstance(schema, dict) or schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or schema.get("$id") != "https://sqvm.local/schemas/platform_configuration_v0_2.schema.json":
        raise ValueError("frozen PlatformConfiguration schema identity is invalid")
    return schema


def field_error(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def validate_editable(value: Any, *, published: bool) -> list[dict[str, str]]:
    """Validate the editable partition defined by the frozen v0.2 schema."""

    errors: list[dict[str, str]] = []
    _exact_object(value, {"control_values", "calibration_values"}, "$", errors)
    if not isinstance(value, Mapping):
        return errors
    _validate_control(value.get("control_values"), "$.control_values", errors)
    _validate_calibration(value.get("calibration_values"), "$.calibration_values", errors, published=published)
    return errors


def validate_document(value: Any) -> list[dict[str, str]]:
    """Validate the persisted Snapshot/Draft/legacy-migration envelope."""

    errors: list[dict[str, str]] = []
    if not isinstance(value, Mapping):
        return [field_error("$", "type", "configuration must be an object")]
    artifact = value.get("artifact_type")
    if value.get("schema_version") != "0.2" or value.get("artifact_version") != "0.2":
        errors.append(field_error("$.schema_version", "const", "PlatformConfiguration v0.2 is required"))
    if artifact == "platform_configuration_snapshot":
        _exact_object(value, _SNAPSHOT_FIELDS, "$", errors)
        _validate_common_envelope(value, errors)
        errors.extend(validate_editable(value.get("editable"), published=True))
    elif artifact == "platform_configuration_draft":
        _exact_object(value, _DRAFT_FIELDS, "$", errors)
        _validate_common_envelope(value, errors)
        errors.extend(validate_editable(value.get("editable"), published=False))
    elif artifact == "platform_configuration_legacy_migration":
        _exact_object(value, _LEGACY_FIELDS, "$", errors)
        _validate_readonly(value.get("readonly"), "$.readonly", errors)
        editable = value.get("editable")
        _exact_object(editable, {"control_values", "calibration_values"}, "$.editable", errors)
        if isinstance(editable, Mapping):
            _validate_control(editable.get("control_values"), "$.editable.control_values", errors)
            if editable.get("calibration_values") != {}:
                errors.append(field_error("$.editable.calibration_values", "const", "legacy migration calibration must be empty"))
    else:
        errors.append(field_error("$.artifact_type", "enum", "unknown PlatformConfiguration artifact"))
    return errors


_SNAPSHOT_FIELDS = {"schema_version", "artifact_type", "artifact_version", "snapshot_id", "state_id", "device_id", "name", "reason", "actor_id", "published_utc", "status", "keep", "parent", "readonly", "editable", "content_sha256", "requires_requalification", "experiment_eligible"}
_DRAFT_FIELDS = {"schema_version", "artifact_type", "artifact_version", "draft_id", "device_id", "name", "note", "actor_id", "created_utc", "updated_utc", "checkpoint", "parent", "readonly", "editable", "content_sha256", "validation"}
_LEGACY_FIELDS = {"schema_version", "artifact_type", "artifact_version", "state_id", "device_id", "status", "readonly", "editable", "legacy_source_sha256"}


def project_wave_indices(calibration: Mapping[str, Any]) -> dict[str, Any]:
    """Return QCIS-compatible authorities without persisting wave_index."""

    result = copy.deepcopy(dict(calibration))
    registry = result.get("waveform_registry")
    settings = registry.get("settings") if isinstance(registry, Mapping) else None
    if isinstance(settings, Mapping):
        for record in settings.values():
            _project_record(record)
    return result


def _project_record(record: Any) -> None:
    if not isinstance(record, dict):
        return
    waveform_class = record.get("waveform_class")
    if isinstance(waveform_class, str) and waveform_class in _WAVE_INDEX:
        record["wave_index"] = _WAVE_INDEX[waveform_class]
    waveforms = record.get("waveforms")
    if isinstance(waveforms, Mapping):
        for spec in waveforms.values():
            if isinstance(spec, dict):
                waveform_class = spec.get("waveform_class")
                if isinstance(waveform_class, str) and waveform_class in _WAVE_INDEX:
                    spec["wave_index"] = _WAVE_INDEX[waveform_class]


def editor_view() -> dict[str, Any]:
    return {
        "schema_version": "0.2",
        "mode": "structured",
        "generated_fields": ["setting_id", "mapper_id", "target", "gate_type", "mapper_type", "status", "revision", "setting_hash", "calibration_run_id", "wave_index"],
        "capabilities": {"create_calibrated_records": True, "advanced_json_writable": False, "direct_z_unit": "Phi/Phi0"},
        "typed_slots": {"qagents": ["Q1", "Q2"], "gate_configuration": ["Q1", "Q2", "C"], "settings": ["XY", "XY2", "X12", "DTN", "CZ", "FSIM"], "mappers": ["F012ZBIAS_MAPPER", "G2ZBIAS_MAPPER"]},
    }


def initial_typed_calibration() -> dict[str, Any]:
    """Create the complete, non-accepted structured editor template."""

    def setting(record_id: str, target: str, gate_type: str, **payload: Any) -> dict[str, Any]:
        return {"setting_id": record_id, "target": target, "gate_type": gate_type, "status": "draft", "base_revision": 0, "base_setting_hash": None, "revision": None, "setting_hash": None, "calibration_run_id": None, **payload}
    def mapper(record_id: str, target: str, mapper_type: str, **payload: Any) -> dict[str, Any]:
        return {"mapper_id": record_id, "target": target, "mapper_type": mapper_type, "status": "draft", "base_revision": 0, "base_setting_hash": None, "revision": None, "setting_hash": None, "calibration_run_id": None, **payload}
    def reference() -> dict[str, Any]:
        return {"reference_frequency_GHz": 5.0, "frequency_source": "bootstrap_seed", "status": "draft", "base_revision": 0, "base_setting_hash": None, "calibration_run_id": None, "revision": None, "setting_hash": None}
    settings: dict[str, Any] = {}
    for target, prefix in (("Q1", "q1"), ("Q2", "q2")):
        for suffix, gate_type, transition, amplitude in (("xy", "XY", "01", 0.1), ("xy2", "XY2", "01", 0.1), ("xy12", "X12", "12", 0.05)):
            record_id = f"{prefix}_{suffix}"
            settings[record_id] = setting(record_id, target, gate_type, transition=transition, waveform_class="rectangle", length_samples=2, amplitude_GHz=amplitude, phase_offset_rad=0.0, dragAlpha_samples=0.0, width_samples=2)
        record_id = f"{prefix}_dtn"
        settings[record_id] = setting(record_id, target, "DTN", control_role="z", envelope_class="rect", input_unit="phi_over_phi0")
    waveform = {"waveform_class": "rectangle", "width_samples": 2, "flux_offset_phi0": 0.0}
    for record_id, gate_type in (("c_cz", "CZ"), ("c_fsim", "FSIM")):
        settings[record_id] = setting(record_id, "C", gate_type, duration_samples=2, use_f012zbias_mapper=False, use_g2zbias_mapper=False, waveforms={"q0": dict(waveform), "q1": dict(waveform), "coupler": dict(waveform)}, q0_calibrated_dynamic_phase_rad=0.0, q1_calibrated_dynamic_phase_rad=0.0)
    return {
        "qagents": {"Q1": {"reference_frequency_authority": reference()}, "Q2": {"reference_frequency_authority": reference()}},
        "gate_configuration": {
            "Q1": {"active_xy_setting": "q1_xy", "active_xy2_setting": "q1_xy2", "active_xy12_setting": "q1_xy12", "active_detune_setting": "q1_dtn", "active_f012zbias_mapper": "q1_f012", "xy_pi_impl": False, "z_gate_impl": "VIRTUAL"},
            "Q2": {"active_xy_setting": "q2_xy", "active_xy2_setting": "q2_xy2", "active_xy12_setting": "q2_xy12", "active_detune_setting": "q2_dtn", "active_f012zbias_mapper": "q2_f012", "xy_pi_impl": False, "z_gate_impl": "VIRTUAL"},
            "C": {"active_cz_setting": "c_cz", "active_fsim_setting": "c_fsim", "active_g2zbias_mapper": "c_g2"},
        },
        "waveform_registry": {"settings": settings, "mappers": {
            "q1_f012": mapper("q1_f012", "Q1", "F012ZBIAS_MAPPER", f01max_GHz=6.0, k_rad_per_phi0=1.0, idle_flux_offset_phi0=0.0),
            "q2_f012": mapper("q2_f012", "Q2", "F012ZBIAS_MAPPER", f01max_GHz=6.0, k_rad_per_phi0=1.0, idle_flux_offset_phi0=0.0),
            "c_g2": mapper("c_g2", "C", "G2ZBIAS_MAPPER", coupling_detune_GHz=[-0.1, 0.0, 0.1], zbias_offset_phi0=[-0.1, 0.0, 0.1], interpolation="piecewise_linear", extrapolation="reject"),
        }},
        "fsim_characterizations": {},
    }


def initial_simulation_configuration() -> dict[str, Any]:
    """Return the editable local-calibration projection defaults."""

    return {
        "calibration_model": {
            "charge_cutoffs": {"q1": 7, "c": 7, "q2": 7},
            "retained_energy_levels": {"q1": 5, "c": 3, "q2": 5},
            "convergence_charge_cutoffs": {"q1": 8, "c": 8, "q2": 8},
            "convergence_retained_energy_levels": {"q1": 6, "c": 4, "q2": 6},
        }
    }


def draftify_calibration(value: Mapping[str, Any]) -> dict[str, Any]:
    """Turn published records into the generated-field-safe Draft representation."""

    if not value:
        return {}
    result = copy.deepcopy(dict(value))
    qagents = result.get("qagents", {})
    if isinstance(qagents, Mapping):
        for row in qagents.values():
            ref = row.get("reference_frequency_authority") if isinstance(row, Mapping) else None
            if isinstance(ref, dict) and ref.get("status") != "draft":
                base_hash, revision = ref.get("setting_hash"), ref.get("revision")
                ref.update({"status": "draft", "base_revision": revision if type(revision) is int else 0, "base_setting_hash": base_hash if isinstance(base_hash, str) else None, "revision": None, "setting_hash": None, "calibration_run_id": None})
    registry = result.get("waveform_registry", {})
    if isinstance(registry, Mapping):
        for section, identifier in (("settings", "setting_id"), ("mappers", "mapper_id")):
            rows = registry.get(section, {})
            if isinstance(rows, Mapping):
                for record in rows.values():
                    if isinstance(record, dict) and record.get("status") != "draft":
                        base_hash, revision = record.get("setting_hash"), record.get("revision")
                        record.update({"status": "draft", "base_revision": revision if type(revision) is int else 0, "base_setting_hash": base_hash if isinstance(base_hash, str) else None, "revision": None, "setting_hash": None, "calibration_run_id": None})
                        record.pop("wave_index", None)
    return result


def publish_calibration(draft: Mapping[str, Any], base: Mapping[str, Any], *, manual_run_id: str, candidate_runs: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Generate accepted immutable records and hashes from a valid Draft."""

    if not draft:
        return {}
    result = copy.deepcopy(dict(draft))
    base = base if isinstance(base, Mapping) else {}
    candidates = candidate_runs or {}
    for target, row in result["qagents"].items():
        ref = row["reference_frequency_authority"]
        old = base.get("qagents", {}).get(target, {}).get("reference_frequency_authority", {})
        row["reference_frequency_authority"] = _publish_reference(
            ref,
            old,
            manual_run_id=candidates.get(
                f"qagent:{target}", candidates.get(target, manual_run_id)
            ),
        )
    for section in ("settings", "mappers"):
        rows = result["waveform_registry"][section]
        old_rows = base.get("waveform_registry", {}).get(section, {})
        for record_id, record in list(rows.items()):
            resource_kind = "setting" if section == "settings" else "mapper"
            rows[record_id] = _publish_record(
                record,
                old_rows.get(record_id, {}),
                manual_run_id=candidates.get(
                    f"{resource_kind}:{record_id}",
                    candidates.get(record.get("target"), manual_run_id),
                ),
            )
    return result


def _publish_record(record: Mapping[str, Any], old: Mapping[str, Any], *, manual_run_id: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    for name in ("base_revision", "base_setting_hash"):
        result.pop(name, None)
    old_content = _without_generated(old)
    new_content = _without_generated(result)
    unchanged = bool(old) and old.get("status") == "accepted" and old_content == new_content
    if unchanged:
        return copy.deepcopy(dict(old))
    base_revision = record.get("base_revision")
    base_hash = record.get("base_setting_hash")
    if type(base_revision) is int and base_revision >= 0:
        if old.get("status") == "accepted" and (old.get("revision") != base_revision or old.get("setting_hash") != base_hash):
            raise ValueError("draft base record no longer matches its publication identity")
        revision = base_revision
    else:
        revision = old.get("revision", 0) if isinstance(old, Mapping) else 0
    result["status"] = "accepted"
    result["revision"] = int(revision) + 1 if type(revision) is int else 1
    result["calibration_run_id"] = manual_run_id
    result.pop("setting_hash", None)
    result["setting_hash"] = sha256_json(result)
    return result


def _publish_reference(record: Mapping[str, Any], old: Mapping[str, Any], *, manual_run_id: str) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    for name in ("status", "base_revision", "base_setting_hash"):
        result.pop(name, None)
    if old and isinstance(old.get("revision"), int) and isinstance(old.get("setting_hash"), str) and _without_generated(old) == _without_generated(result):
        return copy.deepcopy(dict(old))
    base_revision = record.get("base_revision")
    base_hash = record.get("base_setting_hash")
    if type(base_revision) is int and base_revision >= 0:
        if isinstance(old.get("revision"), int) and (old.get("revision") != base_revision or old.get("setting_hash") != base_hash):
            raise ValueError("draft base reference no longer matches its publication identity")
        revision = base_revision
    else:
        revision = old.get("revision", 0) if isinstance(old, Mapping) else 0
    result["revision"] = int(revision) + 1 if type(revision) is int else 1
    result["calibration_run_id"] = manual_run_id
    result.pop("setting_hash", None)
    result["setting_hash"] = sha256_json(result)
    return result


def _without_generated(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return {key: _without_generated(item) for key, item in value.items() if key not in {"status", "revision", "setting_hash", "calibration_run_id", "base_revision", "base_setting_hash"}}


def _validate_common_envelope(value: Mapping[str, Any], errors: list[dict[str, str]]) -> None:
    _validate_readonly(value.get("readonly"), "$.readonly", errors)
    editable = value.get("editable")
    if isinstance(editable, Mapping):
        digest = value.get("content_sha256")
        if not _sha(digest) or digest != sha256_json(editable):
            errors.append(field_error("$.content_sha256", "hash", "content hash does not match editable"))


def _validate_readonly(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    _exact_object(value, {"device_ref", "authority_refs"}, path, errors)
    if not isinstance(value, Mapping):
        return
    ref = value.get("device_ref")
    _exact_object(ref, {"path", "sha256"}, f"{path}.device_ref", errors)
    if isinstance(ref, Mapping):
        if not isinstance(ref.get("path"), str) or not ref["path"] or ref["path"].startswith(("/", "\\")) or ":" in ref["path"]:
            errors.append(field_error(f"{path}.device_ref.path", "pattern", "device path must be relative"))
        if not _sha(ref.get("sha256")):
            errors.append(field_error(f"{path}.device_ref.sha256", "pattern", "sha256 is invalid"))
    authorities = value.get("authority_refs")
    _exact_object(authorities, {"device_sha256", "instruction_profile_sha256", "compiler_snapshot_sha256"}, f"{path}.authority_refs", errors)
    if isinstance(authorities, Mapping):
        for name, item in authorities.items():
            if not _sha(item):
                errors.append(field_error(f"{path}.authority_refs.{name}", "pattern", "sha256 is invalid"))


def _validate_control(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(value, Mapping):
        errors.append(field_error(path, "type", "must be an object"))
        return
    actual = set(value)
    for name in sorted(_CONTROL - actual):
        errors.append(field_error(f"{path}.{name}", "required", "field is required"))
    for name in sorted(actual - (_CONTROL | {"simulation"})):
        errors.append(field_error(f"{path}.{name}", "additional", "field is not allowed"))
    clock = value.get("clock")
    _exact_object(clock, {"sample_rate_Hz", "dt_ns"}, f"{path}.clock", errors)
    if isinstance(clock, Mapping):
        _positive(clock.get("sample_rate_Hz"), f"{path}.clock.sample_rate_Hz", errors)
        _positive(clock.get("dt_ns"), f"{path}.clock.dt_ns", errors)
    dac = value.get("dac")
    _exact_object(dac, {"bits", "full_scale_min_V", "full_scale_max_exclusive_V", "rounding"}, f"{path}.dac", errors)
    if isinstance(dac, Mapping):
        if type(dac.get("bits")) is not int or not 1 <= dac["bits"] <= 32:
            errors.append(field_error(f"{path}.dac.bits", "range", "DAC bits must be 1..32"))
        _finite(dac.get("full_scale_min_V"), f"{path}.dac.full_scale_min_V", errors)
        _finite(dac.get("full_scale_max_exclusive_V"), f"{path}.dac.full_scale_max_exclusive_V", errors)
        if dac.get("rounding") != "half_even":
            errors.append(field_error(f"{path}.dac.rounding", "const", "rounding must be half_even"))
    order, lanes = value.get("lane_order"), value.get("lanes")
    if not isinstance(order, list) or len(order) != 11 or set(order) != _LANES:
        errors.append(field_error(f"{path}.lane_order", "exact", "lane_order must contain the 11 formal lanes"))
    _exact_object(lanes, _LANES, f"{path}.lanes", errors)
    if isinstance(lanes, Mapping):
        for name, lane in lanes.items():
            _exact_object(lane, {"latency_samples", "fir"}, f"{path}.lanes.{name}", errors)
            if isinstance(lane, Mapping):
                if type(lane.get("latency_samples")) is not int or lane["latency_samples"] < 0:
                    errors.append(field_error(f"{path}.lanes.{name}.latency_samples", "range", "latency must be a nonnegative integer"))
                fir = lane.get("fir")
                if not isinstance(fir, list) or not fir or any(not _is_number(item) for item in fir):
                    errors.append(field_error(f"{path}.lanes.{name}.fir", "type", "FIR must be finite numeric values"))
    mixing = value.get("static_mixing")
    _exact_object(mixing, {"xy", "z", "readout"}, f"{path}.static_mixing", errors)
    if isinstance(mixing, Mapping):
        for name, section in mixing.items():
            _exact_object(section, {"input_lanes", "output_coordinates", "matrix"}, f"{path}.static_mixing.{name}", errors)
            if isinstance(section, Mapping):
                inputs, outputs, matrix = section.get("input_lanes"), section.get("output_coordinates"), section.get("matrix")
                valid = isinstance(inputs, list) and isinstance(outputs, list) and isinstance(matrix, list) and len(matrix) == len(outputs) and bool(inputs) and bool(outputs) and all(isinstance(row, list) and len(row) == len(inputs) and all(_is_number(item) for item in row) for row in matrix)
                if not valid:
                    errors.append(field_error(f"{path}.static_mixing.{name}", "shape", "mixing matrix dimensions are invalid"))
                elif (inputs, outputs) != _MIXING_COORDINATES[name]:
                    errors.append(field_error(f"{path}.static_mixing.{name}", "const", "formal mixing coordinates are fixed"))
    flux = value.get("idle_flux_phi0")
    _exact_object(flux, {"q1", "q2", "c"}, f"{path}.idle_flux_phi0", errors)
    if isinstance(flux, Mapping):
        for name, item in flux.items(): _finite(item, f"{path}.idle_flux_phi0.{name}", errors)
    acceptance = value.get("acceptance")
    _exact_object(acceptance, _ACCEPTANCE, f"{path}.acceptance", errors)
    if isinstance(acceptance, Mapping):
        for name, item in acceptance.items():
            if name == "max_formal_samples_per_scenario":
                if type(item) is not int or item < 1: errors.append(field_error(f"{path}.acceptance.{name}", "range", "must be a positive integer"))
            else: _positive(item, f"{path}.acceptance.{name}", errors)
    if "simulation" in value:
        _validate_simulation(value.get("simulation"), f"{path}.simulation", errors)


def _validate_simulation(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    _exact_object(value, {"calibration_model"}, path, errors)
    if not isinstance(value, Mapping):
        return
    model = value.get("calibration_model")
    _exact_object(model, _SIMULATION_FIELDS, f"{path}.calibration_model", errors)
    if not isinstance(model, Mapping):
        return
    parsed: dict[str, dict[str, int]] = {}
    for field in sorted(_SIMULATION_FIELDS):
        values = model.get(field)
        field_path = f"{path}.calibration_model.{field}"
        _exact_object(values, _SIMULATION_MODES, field_path, errors)
        if not isinstance(values, Mapping):
            continue
        parsed[field] = {}
        for mode in ("q1", "c", "q2"):
            item = values.get(mode)
            if type(item) is not int or not 1 <= item <= 16:
                errors.append(field_error(f"{field_path}.{mode}", "range", "must be an integer in [1,16]"))
            else:
                parsed[field][mode] = item
    if set(parsed) != _SIMULATION_FIELDS or any(set(values) != _SIMULATION_MODES for values in parsed.values()):
        return
    baseline_cutoff = parsed["charge_cutoffs"]
    baseline_levels = parsed["retained_energy_levels"]
    comparison_cutoff = parsed["convergence_charge_cutoffs"]
    comparison_levels = parsed["convergence_retained_energy_levels"]
    for mode in ("q1", "c", "q2"):
        if baseline_levels[mode] > 2 * baseline_cutoff[mode] + 1:
            errors.append(field_error(f"{path}.calibration_model.retained_energy_levels.{mode}", "range", "retained levels exceed the charge basis"))
        if comparison_cutoff[mode] < baseline_cutoff[mode]:
            errors.append(field_error(f"{path}.calibration_model.convergence_charge_cutoffs.{mode}", "range", "convergence cutoff must not be below baseline"))
        if comparison_levels[mode] < baseline_levels[mode] or comparison_levels[mode] > 2 * comparison_cutoff[mode] + 1:
            errors.append(field_error(f"{path}.calibration_model.convergence_retained_energy_levels.{mode}", "range", "convergence levels must cover baseline within its charge basis"))
    baseline_dimension = math.prod(baseline_levels.values())
    comparison_dimension = math.prod(comparison_levels.values())
    if baseline_dimension > 512:
        errors.append(field_error(f"{path}.calibration_model.retained_energy_levels", "range", "baseline projected dimension exceeds 512"))
    if comparison_dimension > 512:
        errors.append(field_error(f"{path}.calibration_model.convergence_retained_energy_levels", "range", "convergence projected dimension exceeds 512"))
    if baseline_cutoff == comparison_cutoff and baseline_levels == comparison_levels:
        errors.append(field_error(f"{path}.calibration_model", "convergence", "convergence model must be stricter than baseline"))


def _validate_calibration(value: Any, path: str, errors: list[dict[str, str]], *, published: bool) -> None:
    if value == {}:
        return
    _exact_object(value, {"qagents", "gate_configuration", "waveform_registry", "fsim_characterizations"}, path, errors)
    if not isinstance(value, Mapping): return
    qagents = value.get("qagents")
    _exact_object(qagents, {"Q1", "Q2"}, f"{path}.qagents", errors)
    if isinstance(qagents, Mapping):
        for target, row in qagents.items():
            _exact_object(row, {"reference_frequency_authority"}, f"{path}.qagents.{target}", errors)
            if isinstance(row, Mapping): _validate_reference(row.get("reference_frequency_authority"), f"{path}.qagents.{target}.reference_frequency_authority", errors, published)
    gates = value.get("gate_configuration")
    _exact_object(gates, {"Q1", "Q2", "C"}, f"{path}.gate_configuration", errors)
    if isinstance(gates, Mapping):
        for target in ("Q1", "Q2"):
            _exact_object(gates.get(target), {"active_xy_setting", "active_xy2_setting", "active_xy12_setting", "active_detune_setting", "active_f012zbias_mapper", "xy_pi_impl", "z_gate_impl"}, f"{path}.gate_configuration.{target}", errors)
        _exact_object(gates.get("C"), {"active_cz_setting", "active_fsim_setting", "active_g2zbias_mapper"}, f"{path}.gate_configuration.C", errors)
    registry = value.get("waveform_registry")
    _exact_object(registry, {"settings", "mappers"}, f"{path}.waveform_registry", errors)
    if isinstance(registry, Mapping):
        for section, kind in (("settings", "setting"), ("mappers", "mapper")):
            records = registry.get(section)
            if not isinstance(records, Mapping):
                errors.append(field_error(f"{path}.waveform_registry.{section}", "type", "registry must be an object")); continue
            for key, record in records.items():
                if not _RECORD_ID.fullmatch(str(key)):
                    errors.append(field_error(f"{path}.waveform_registry.{section}.{key}", "pattern", "record id is invalid")); continue
                _validate_record(record, f"{path}.waveform_registry.{section}.{key}", errors, published, kind)
    _validate_characterizations(value.get("fsim_characterizations"), f"{path}.fsim_characterizations", errors)


def _validate_reference(value: Any, path: str, errors: list[dict[str, str]], published: bool) -> None:
    required = {"reference_frequency_GHz", "frequency_source", "calibration_run_id", "revision", "setting_hash"}
    if not published: required |= {"status", "base_revision", "base_setting_hash"}
    _exact_object(value, required, path, errors)
    if not isinstance(value, Mapping): return
    _positive(value.get("reference_frequency_GHz"), f"{path}.reference_frequency_GHz", errors)
    if value.get("frequency_source") not in {"bootstrap_seed", "accepted_simulation"}: errors.append(field_error(f"{path}.frequency_source", "enum", "source is invalid"))
    if published:
        if type(value.get("revision")) is not int or value["revision"] < 1: errors.append(field_error(f"{path}.revision", "range", "revision must be positive"))
        if not _sha(value.get("setting_hash")): errors.append(field_error(f"{path}.setting_hash", "pattern", "setting hash is invalid"))
        if not isinstance(value.get("calibration_run_id"), str) or not value["calibration_run_id"]: errors.append(field_error(f"{path}.calibration_run_id", "type", "run id is required"))
    elif value.get("status") != "draft" or value.get("revision") is not None or value.get("setting_hash") is not None or value.get("calibration_run_id") is not None:
        errors.append(field_error(path, "generated", "Draft publication fields are generated and must be null"))


def _validate_record(value: Any, path: str, errors: list[dict[str, str]], published: bool, kind: str) -> None:
    if not isinstance(value, Mapping): errors.append(field_error(path, "type", "record must be an object")); return
    id_name = "setting_id" if kind == "setting" else "mapper_id"
    identity = {id_name, "target", "status", "revision", "setting_hash", "calibration_run_id"}
    if kind == "setting": identity.add("gate_type")
    else: identity.add("mapper_type")
    if not published: identity |= {"base_revision", "base_setting_hash"}
    allowed = set(identity)
    record_type = value.get("gate_type" if kind == "setting" else "mapper_type")
    if kind == "setting" and record_type in {"XY", "XY2", "X12"}:
        allowed |= {"transition", "waveform_class", "length_samples", "amplitude_GHz", "phase_offset_rad", "dragAlpha_samples", "width_samples", "r_sigma_samples", "edge_samples"}
    elif kind == "setting" and record_type == "DTN":
        allowed |= {"control_role", "envelope_class", "input_unit"}
    elif kind == "setting" and record_type in {"CZ", "FSIM"}:
        allowed |= {"duration_samples", "use_f012zbias_mapper", "use_g2zbias_mapper", "waveforms", "q0_calibrated_dynamic_phase_rad", "q1_calibrated_dynamic_phase_rad"}
    elif kind == "mapper" and record_type == "F012ZBIAS_MAPPER": allowed |= {"f01max_GHz", "k_rad_per_phi0", "idle_flux_offset_phi0"}
    elif kind == "mapper" and record_type == "G2ZBIAS_MAPPER": allowed |= {"coupling_detune_GHz", "zbias_offset_phi0", "interpolation", "extrapolation"}
    _closed_object(value, allowed, path, errors)
    for name in sorted(identity - set(value)):
        errors.append(field_error(f"{path}.{name}", "required", "field is required"))
    _validate_identity(value, path, errors, published, id_name=id_name)
    if kind == "setting": _validate_setting_payload(value, path, errors)
    else: _validate_mapper_payload(value, path, errors)


def _validate_identity(value: Mapping[str, Any], path: str, errors: list[dict[str, str]], published: bool, id_name: str | None) -> None:
    if published:
        if value.get("status") != "accepted": errors.append(field_error(f"{path}.status", "const", "published records are accepted"))
        if type(value.get("revision")) is not int or value["revision"] < 1: errors.append(field_error(f"{path}.revision", "range", "revision must be positive"))
        if not _sha(value.get("setting_hash")): errors.append(field_error(f"{path}.setting_hash", "pattern", "setting hash is invalid"))
        if not isinstance(value.get("calibration_run_id"), str) or not value["calibration_run_id"]: errors.append(field_error(f"{path}.calibration_run_id", "type", "run id is required"))
    else:
        if value.get("status") != "draft" or value.get("revision") is not None or value.get("setting_hash") is not None or value.get("calibration_run_id") is not None:
            errors.append(field_error(path, "generated", "Draft publication fields are generated and must be null"))
        if type(value.get("base_revision")) is not int or value["base_revision"] < 0: errors.append(field_error(f"{path}.base_revision", "range", "base revision is invalid"))
        if value.get("base_setting_hash") is not None and not _sha(value.get("base_setting_hash")): errors.append(field_error(f"{path}.base_setting_hash", "pattern", "base hash is invalid"))
    if id_name and (not isinstance(value.get(id_name), str) or not _RECORD_ID.fullmatch(value[id_name])): errors.append(field_error(f"{path}.{id_name}", "pattern", "record id is invalid"))


def _validate_setting_payload(value: Mapping[str, Any], path: str, errors: list[dict[str, str]]) -> None:
    kind, target = value.get("gate_type"), value.get("target")
    if kind in {"XY", "XY2", "X12"}:
        if target not in {"Q1", "Q2"} or value.get("transition") != ("12" if kind == "X12" else "01"): errors.append(field_error(path, "contract", "XY setting target or transition is invalid"))
        _validate_shape(value, path, errors, allow_acz=False)
        for name in ("length_samples",):
            if type(value.get(name)) is not int or value[name] < 1: errors.append(field_error(f"{path}.{name}", "range", "must be positive integer"))
        for name in ("amplitude_GHz", "phase_offset_rad", "dragAlpha_samples"): _finite(value.get(name), f"{path}.{name}", errors)
    elif kind == "DTN":
        if target not in {"Q1", "Q2"} or (value.get("control_role"), value.get("envelope_class"), value.get("input_unit")) != ("z", "rect", "phi_over_phi0"): errors.append(field_error(path, "contract", "DTN must be direct Phi/Phi0 rectangle"))
    elif kind in {"CZ", "FSIM"}:
        if target != "C": errors.append(field_error(f"{path}.target", "const", "composite target must be C"))
        if type(value.get("duration_samples")) is not int or value["duration_samples"] < 1: errors.append(field_error(f"{path}.duration_samples", "range", "duration is invalid"))
        waves = value.get("waveforms")
        _exact_object(waves, {"q0", "q1", "coupler"}, f"{path}.waveforms", errors)
        if isinstance(waves, Mapping):
            direct_f, direct_g = value.get("use_f012zbias_mapper"), value.get("use_g2zbias_mapper")
            if type(direct_f) is not bool or type(direct_g) is not bool: errors.append(field_error(path, "type", "mapper switches must be boolean"))
            for name in ("q0", "q1", "coupler"):
                spec = waves.get(name)
                if isinstance(spec, Mapping):
                    amount = "frequency_detune_GHz" if name != "coupler" and direct_f else "coupling_detune_GHz" if name == "coupler" and direct_g else "flux_offset_phi0"
                    _closed_object(spec, {"waveform_class", "width_samples", "r_sigma_samples", "edge_samples", "parameters", amount}, f"{path}.waveforms.{name}", errors)
                    _validate_shape(spec, f"{path}.waveforms.{name}", errors, allow_acz=True)
                    _finite(spec.get(amount), f"{path}.waveforms.{name}.{amount}", errors)
    else: errors.append(field_error(f"{path}.gate_type", "enum", "setting type is invalid"))


def _validate_mapper_payload(value: Mapping[str, Any], path: str, errors: list[dict[str, str]]) -> None:
    kind, target = value.get("mapper_type"), value.get("target")
    if kind == "F012ZBIAS_MAPPER":
        if target not in {"Q1", "Q2"}: errors.append(field_error(f"{path}.target", "enum", "F012 mapper target is invalid"))
        _positive(value.get("f01max_GHz"), f"{path}.f01max_GHz", errors); _finite(value.get("k_rad_per_phi0"), f"{path}.k_rad_per_phi0", errors); _finite(value.get("idle_flux_offset_phi0"), f"{path}.idle_flux_offset_phi0", errors)
    elif kind == "G2ZBIAS_MAPPER":
        if target != "C" or value.get("interpolation") != "piecewise_linear" or value.get("extrapolation") != "reject": errors.append(field_error(path, "contract", "G2 mapper contract is invalid"))
        x, y = value.get("coupling_detune_GHz"), value.get("zbias_offset_phi0")
        if not isinstance(x, list) or not isinstance(y, list) or len(x) < 2 or len(x) != len(y) or any(not _is_number(item) for item in x + y) or any(a >= b for a, b in zip(x, x[1:])) or not any(a == 0 and b == 0 for a, b in zip(x, y)):
            errors.append(field_error(path, "domain", "G2 mapper requires increasing finite axis containing (0,0)"))
    else: errors.append(field_error(f"{path}.mapper_type", "enum", "mapper type is invalid"))


def _validate_shape(value: Mapping[str, Any], path: str, errors: list[dict[str, str]], *, allow_acz: bool) -> None:
    kind = value.get("waveform_class")
    allowed = {"rectangle", "gaussian", "flattop"} | ({"acz"} if allow_acz else set())
    if kind not in allowed: errors.append(field_error(f"{path}.waveform_class", "enum", "waveform class is invalid")); return
    required = {"rectangle": "width_samples", "gaussian": "r_sigma_samples", "flattop": "edge_samples", "acz": "parameters"}[kind]
    if required not in value: errors.append(field_error(f"{path}.{required}", "required", "shape parameter is required"))
    forbidden = {"width_samples", "r_sigma_samples", "edge_samples", "parameters"} - {required}
    for name in forbidden:
        if name in value: errors.append(field_error(f"{path}.{name}", "forbidden", "shape parameter does not match waveform class"))
    if required == "width_samples" and (type(value.get(required)) is not int or value[required] < 1): errors.append(field_error(f"{path}.{required}", "range", "width must be positive integer"))
    if required == "r_sigma_samples": _positive(value.get(required), f"{path}.{required}", errors)
    if required == "edge_samples" and (type(value.get(required)) is not int or value[required] < 0): errors.append(field_error(f"{path}.{required}", "range", "edge must be nonnegative integer"))
    if required == "parameters":
        params = value.get("parameters")
        _exact_object(params, {"thf", "thi", "lam2", "lam3"}, f"{path}.parameters", errors)
        if isinstance(params, Mapping):
            _positive(params.get("thf"), f"{path}.parameters.thf", errors)
            _positive(params.get("thi"), f"{path}.parameters.thi", errors)
            _finite(params.get("lam2"), f"{path}.parameters.lam2", errors)
            _finite(params.get("lam3"), f"{path}.parameters.lam3", errors)


def _validate_characterizations(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(value, Mapping):
        errors.append(field_error(path, "type", "characterizations must be an object")); return
    required = {"characterization_run_id", "source", "theta_rad", "zeta_rad", "chi_rad", "gamma_rad", "phi_rad", "metrics", "leakage", "method", "status"}
    for key, record in value.items():
        _exact_object(record, required, f"{path}.{key}", errors)
        if not isinstance(record, Mapping): continue
        if record.get("characterization_run_id") != key or not _RECORD_ID.fullmatch(str(key)):
            errors.append(field_error(f"{path}.{key}.characterization_run_id", "identity", "characterization id is invalid"))
        if not isinstance(record.get("source"), str) or not record["source"] or not isinstance(record.get("method"), str) or not record["method"] or record.get("status") != "accepted":
            errors.append(field_error(f"{path}.{key}", "contract", "characterization provenance is invalid"))
        for name in ("theta_rad", "zeta_rad", "chi_rad", "gamma_rad", "phi_rad"):
            _finite(record.get(name), f"{path}.{key}.{name}", errors)
        leakage = record.get("leakage")
        if not _is_number(leakage) or not 0.0 <= float(leakage) <= 1.0:
            errors.append(field_error(f"{path}.{key}.leakage", "range", "leakage must be in [0,1]"))
        metrics = record.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            errors.append(field_error(f"{path}.{key}.metrics", "type", "metrics must be nonempty")); continue
        for index, metric in enumerate(metrics):
            metric_path = f"{path}.{key}.metrics[{index}]"
            _closed_object(metric, {"metric_type", "value", "uncertainty"}, metric_path, errors)
            if not isinstance(metric, Mapping) or metric.get("metric_type") not in {"qpt_process_fidelity", "xeb_cycle_fidelity"} or not _is_number(metric.get("value")) or not 0.0 <= float(metric["value"]) <= 1.0:
                errors.append(field_error(metric_path, "contract", "metric is invalid"))
            elif "uncertainty" in metric and (not _is_number(metric["uncertainty"]) or float(metric["uncertainty"]) < 0.0):
                errors.append(field_error(f"{metric_path}.uncertainty", "range", "uncertainty must be nonnegative"))


def _exact_object(value: Any, required: set[str], path: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(value, Mapping): errors.append(field_error(path, "type", "must be an object")); return
    actual = set(value)
    for name in sorted(required - actual): errors.append(field_error(f"{path}.{name}", "required", "field is required"))
    for name in sorted(actual - required): errors.append(field_error(f"{path}.{name}", "additional", "field is not allowed"))


def _closed_object(value: Any, allowed: set[str], path: str, errors: list[dict[str, str]]) -> None:
    if not isinstance(value, Mapping):
        errors.append(field_error(path, "type", "must be an object")); return
    for name in sorted(set(value) - allowed):
        errors.append(field_error(f"{path}.{name}", "additional", "field is not allowed"))


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _finite(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    if not _is_number(value): errors.append(field_error(path, "type", "must be a finite number"))


def _positive(value: Any, path: str, errors: list[dict[str, str]]) -> None:
    if not _is_number(value) or float(value) <= 0: errors.append(field_error(path, "range", "must be a positive finite number"))


def _sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


__all__ = ["draftify_calibration", "editor_view", "field_error", "frozen_schema_path", "initial_simulation_configuration", "initial_typed_calibration", "load_frozen_schema", "project_wave_indices", "publish_calibration", "validate_document", "validate_editable"]
