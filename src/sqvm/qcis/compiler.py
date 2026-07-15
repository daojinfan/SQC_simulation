"""Pure Stage 7.0 QCIS compiler: admission, timing and logical waveforms only."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from .canonical import canonical_json_bytes, canonical_float, sha256_bytes, sha256_json, validate_canonical_source
from .errors import QCISCompilationError, QCISReasonCode
from .models import (
    ProgramEnvelope,
    QCISBinding,
    QCISAuthorities,
    QCISCompilation,
    QCISLogicalWaveformPlan,
    frozen_array,
    frozen_mapping,
)
from .parser import INSTRUCTION_SET_ID, PROGRAM_SCHEMA_VERSION, SOURCE_FORMAT, parse_qcis
from .waveforms import analytic_waveform


_AUTHORITY_CODES = {
    "calibration": QCISReasonCode.CALIBRATION_AUTHORITY_HASH_MISMATCH,
    "instruction_profile": QCISReasonCode.PROFILE_AUTHORITY_HASH_MISMATCH,
    "qagent_registry": QCISReasonCode.QAGENT_AUTHORITY_HASH_MISMATCH,
    "gate_configuration": QCISReasonCode.GATE_CONFIG_AUTHORITY_HASH_MISMATCH,
    "waveform_registry": QCISReasonCode.WAVEFORM_REGISTRY_AUTHORITY_HASH_MISMATCH,
    "clock": QCISReasonCode.CLOCK_AUTHORITY_HASH_MISMATCH,
    "compiler": QCISReasonCode.COMPILER_AUTHORITY_HASH_MISMATCH,
}
_IDLE_DEFAULT = {"q1": 0.1, "q2": 0.0, "c": 0.27}
_BINDABLE_OPERANDS = {
    "PLS": {"amplitude": 5, "target_flux": 5, "length": 4},
    "PLSXY": {"amplitude": 5, "frequency": 6, "phase": 7, "drag_alpha": 8, "r_sigma": 9, "length": 4},
    "DTN": {"length": 2, "amplitude": 3},
    "RZ": {"phase": 2},
    "XY": {"phase": 2},
    "XY2P": {"phase": 2},
    "XY2M": {"phase": 2},
    "RX": {"altitude": 2},
    "RY": {"altitude": 2},
    "RXY": {"azimuth": 2, "altitude": 3},
}


def _integer_binding_position(tokens: list[str], token_index: int) -> bool:
    """Whether a placeholder occupies a pulse/detune sample-count field."""

    if tokens[0] not in {"PLS", "PLSXY", "DTN"}:
        return False
    # Numeric PLS/PLSXY has no separate length operand.
    if tokens[0] in {"PLS", "PLSXY"} and tokens[2] == "-1":
        return False
    return token_index == _BINDABLE_OPERANDS[tokens[0]]["length"]


def _canonical_positive_integer(value: float, name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(QCISReasonCode.NONCANONICAL_NUMBER, f"binding {name!r} must be a positive integer sample count")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0 or not numeric.is_integer():
        _fail(QCISReasonCode.NONCANONICAL_NUMBER, f"binding {name!r} must be a positive integer sample count")
    return str(int(numeric))


def materialize_program(source: str, bindings: Mapping[str, float]) -> str:
    """Replace registered placeholders with shortest round-trip binary64 decimals."""

    validate_canonical_source(source, allow_placeholders=True)
    result: list[str] = []
    seen: set[str] = set()
    for line in source[:-1].split("\n"):
        tokens = line.split(" ")
        values: list[str] = []
        for token_index, token in enumerate(tokens):
            if token.startswith("$"):
                name = token[1:]
                if name not in bindings:
                    raise QCISCompilationError(QCISReasonCode.BINDING_SET_MISMATCH, f"missing {name!r}")
                value = bindings[name]
                values.append(_canonical_positive_integer(value, name) if _integer_binding_position(tokens, token_index) else canonical_float(value))
                seen.add(name)
            else:
                values.append(token)
        result.append(" ".join(values))
    if set(bindings) != seen:
        raise QCISCompilationError(QCISReasonCode.BINDING_SET_MISMATCH, "binding set does not match placeholders")
    concrete = "\n".join(result) + "\n"
    validate_canonical_source(concrete, allow_placeholders=False)
    return concrete


def _fail(code: QCISReasonCode, detail: str) -> None:
    raise QCISCompilationError(code, detail)


def _canonical_plain(value: Any) -> Any:
    """Deep-thaw immutable mappings before canonical JSON provenance serialization."""

    if isinstance(value, Mapping):
        return {str(key): _canonical_plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_canonical_plain(item) for item in value]
    return value


def _strict_positive_integer(value: Any, *, invalid_code: QCISReasonCode, detail: str) -> int:
    if type(value) is not int:
        _fail(invalid_code, f"{detail} must be an integer")
    if value <= 0:
        _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, f"{detail} must be positive")
    return value


def _envelope(program: Mapping[str, Any]) -> ProgramEnvelope:
    exact = {"program_schema_version", "instruction_set_id", "template_id", "template_sha256", "source_format", "source", "bindings"}
    if not isinstance(program, Mapping) or set(program) != exact:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "program envelope keys are not exact")
    try:
        envelope = ProgramEnvelope(
            program_schema_version=str(program["program_schema_version"]),
            instruction_set_id=str(program["instruction_set_id"]),
            template_id=str(program["template_id"]),
            template_sha256=str(program["template_sha256"]),
            source_format=str(program["source_format"]),
            source=str(program["source"]),
            bindings=frozen_mapping(dict(program["bindings"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, str(exc))
    if envelope.program_schema_version != PROGRAM_SCHEMA_VERSION or envelope.instruction_set_id != INSTRUCTION_SET_ID:
        _fail(QCISReasonCode.PROFILE_AUTHORITY_HASH_MISMATCH, "program profile is not admitted")
    if envelope.source_format != SOURCE_FORMAT:
        _fail(QCISReasonCode.NONCANONICAL_SOURCE, "source format is not qcis_template")
    validate_canonical_source(envelope.source, allow_placeholders=True)
    return envelope


def _admit_template(envelope: ProgramEnvelope, authorities: Mapping[str, Any]) -> Mapping[str, Any]:
    templates = authorities.get("templates")
    if not isinstance(templates, Mapping) or envelope.template_id not in templates:
        _fail(QCISReasonCode.TEMPLATE_ID_MISMATCH, "unregistered template id")
    template = templates[envelope.template_id]
    if not isinstance(template, Mapping) or "source" not in template:
        _fail(QCISReasonCode.TEMPLATE_ID_MISMATCH, "invalid template record")
    source = str(template["source"])
    validate_canonical_source(source, allow_placeholders=True)
    expected_sha = sha256_bytes(source.encode("utf-8"))
    if envelope.template_sha256 != expected_sha or envelope.source != source:
        _fail(QCISReasonCode.TEMPLATE_SHA_MISMATCH, "template bytes or hash mismatch")
    declared = template.get("bindings", {})
    if not isinstance(declared, Mapping) or set(declared) != set(envelope.bindings):
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "binding key set mismatch")
    for name, spec in declared.items():
        if not isinstance(spec, Mapping) or not isinstance(spec.get("unit"), str):
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"invalid binding spec {name!r}")
        keys = set(spec)
        if keys == {"unit", "occurrences", "position"}:
            positions = (spec["position"],)
        elif keys == {"unit", "occurrences", "positions"} and isinstance(spec["positions"], list | tuple):
            positions = tuple(spec["positions"])
        else:
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"invalid binding spec {name!r}")
        occurrences = sum(token == f"${name}" for line in source[:-1].split("\n") for token in line.split(" "))
        expected_occurrences = _strict_positive_integer(
            spec["occurrences"],
            invalid_code=QCISReasonCode.BINDING_SET_MISMATCH,
            detail=f"binding {name!r} occurrences",
        )
        if expected_occurrences != occurrences or len(positions) != occurrences:
            _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} occurrence mismatch")
        declared_coordinates: set[tuple[int, int]] = set()
        for position in positions:
            if not (isinstance(position, list | tuple) and len(position) == 2 and type(position[0]) is int and isinstance(position[1], str)):
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} position invalid")
            lines = source[:-1].split("\n")
            if position[0] < 0 or position[0] >= len(lines):
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} position outside source")
            tokens = lines[position[0]].split(" ")
            fields = _BINDABLE_OPERANDS
            if tokens[0] in {"PLS", "PLSXY"} and tokens[2] == "-1":
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} cannot target numeric payload")
            if tokens[0] not in fields or fields[tokens[0]].get(position[1]) is None or tokens[fields[tokens[0]][position[1]]] != f"${name}":
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} position forbidden")
            declared_coordinates.add((position[0], fields[tokens[0]][position[1]]))
        actual_coordinates = {
            (line_index, token_index)
            for line_index, line in enumerate(source[:-1].split("\n"))
            for token_index, token in enumerate(line.split(" "))
            if token == f"${name}"
        }
        if len(declared_coordinates) != len(positions) or actual_coordinates != declared_coordinates:
            _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} positions differ from source")
    return template


def _finite_binary64(value: Any, detail: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        _fail(QCISReasonCode.NONCANONICAL_NUMBER, detail)
    return value


def _resolve_bindings(
    envelope: ProgramEnvelope,
    template: Mapping[str, Any],
    scan_values: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, float]:
    """Validate typed bindings and resolve their finite binary64 values."""

    specs = template["bindings"]
    referenced_axes: set[str] = set()
    typed: dict[str, QCISBinding] = {}
    for name, raw in envelope.bindings.items():
        if not isinstance(raw, Mapping):
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} must be a typed object")
        keys = set(raw)
        if keys == {"literal", "unit"}:
            if not isinstance(raw["unit"], str):
                _fail(QCISReasonCode.BINDING_UNIT_MISMATCH, f"binding {name!r} unit must be a string")
            typed[name] = QCISBinding(unit=raw["unit"], literal=_finite_binary64(raw["literal"], f"binding {name!r} literal"))
        elif keys == {"scan_ref", "unit"}:
            if not isinstance(raw["unit"], str) or not isinstance(raw["scan_ref"], str) or not raw["scan_ref"]:
                _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} scan_ref is invalid")
            typed[name] = QCISBinding(unit=raw["unit"], scan_ref=raw["scan_ref"])
            referenced_axes.add(raw["scan_ref"])
        else:
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} must select literal or scan_ref exactly")
        if typed[name].unit != specs[name]["unit"]:
            _fail(QCISReasonCode.BINDING_UNIT_MISMATCH, f"binding {name!r} unit differs from template")

    if scan_values is None:
        if referenced_axes:
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, "scan values are required for scan_ref bindings")
        return {name: binding.literal for name, binding in typed.items() if binding.literal is not None}
    if not isinstance(scan_values, Mapping) or set(scan_values) != referenced_axes:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "scan value axes differ from scan_ref bindings")
    resolved: dict[str, float] = {}
    for name, binding in typed.items():
        if binding.literal is not None:
            resolved[name] = binding.literal
            continue
        raw_scan = scan_values[binding.scan_ref or ""]
        if not isinstance(raw_scan, Mapping) or set(raw_scan) != {"value", "unit"}:
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"scan value {binding.scan_ref!r} has invalid keys")
        if not isinstance(raw_scan["unit"], str) or raw_scan["unit"] != binding.unit:
            _fail(QCISReasonCode.BINDING_UNIT_MISMATCH, f"scan value {binding.scan_ref!r} unit differs")
        resolved[name] = _finite_binary64(raw_scan["value"], f"scan value {binding.scan_ref!r}")
    return resolved


def _authority_hashes(authorities: Mapping[str, Any], program: ProgramEnvelope, *, macros: bool) -> dict[str, str]:
    required = ("instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler")
    values: dict[str, Any] = {}
    for name in required:
        if name not in authorities or not isinstance(authorities[name], Mapping):
            _fail(_AUTHORITY_CODES[name], f"missing {name}")
        values[name] = authorities[name]
    if macros:
        if not isinstance(authorities.get("calibration"), Mapping):
            _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, "macro calibration absent")
        values["calibration"] = authorities["calibration"]
    computed: dict[str, str] = {}
    for name, value in values.items():
        try:
            computed[name] = sha256_json(value)
        except (TypeError, ValueError, OverflowError) as exc:
            _fail(_AUTHORITY_CODES[name], f"{name} cannot be canonically hashed: {exc}")
    expected = authorities.get("expected_sha256", {})
    if expected is not None and not isinstance(expected, Mapping):
        _fail(QCISReasonCode.CALIBRATION_AUTHORITY_HASH_MISMATCH, "expected hashes invalid")
    if isinstance(expected, Mapping):
        for name, expected_digest in expected.items():
            if name not in computed or not isinstance(expected_digest, str):
                _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"unexpected authority hash entry {name!r}")
            if expected_digest != computed[name]:
                _fail(_AUTHORITY_CODES[name], f"{name} hash mismatch")
    program_payload = {
        "bindings": _canonical_plain(program.bindings),
        "instruction_set_id": program.instruction_set_id,
        "program_schema_version": program.program_schema_version,
        "source": program.source,
        "source_format": program.source_format,
        "template_id": program.template_id,
        "template_sha256": program.template_sha256,
    }
    computed["program"] = sha256_json(program_payload)
    return computed


def _registry(authorities: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    registry = authorities["qagent_registry"]
    return {str(name): value for name, value in registry.items() if name != "schema_version" and isinstance(value, Mapping)}


def _validate_idle_flux(idle_flux: Mapping[str, float] | None) -> dict[str, float]:
    if idle_flux is None:
        return dict(_IDLE_DEFAULT)
    if not isinstance(idle_flux, Mapping) or set(idle_flux) != set(_IDLE_DEFAULT):
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "idle_flux keys must be exactly q1, q2, c")
    values: dict[str, float] = {}
    for name in _IDLE_DEFAULT:
        value = idle_flux[name]
        if type(value) is not float or not math.isfinite(value):
            _fail(QCISReasonCode.NONCANONICAL_NUMBER, f"idle_flux.{name} must be a finite binary64")
        values[name] = value
    return values


def _capabilities(registry: Mapping[str, Mapping[str, Any]], target: str) -> tuple[bool, bool, str]:
    record = registry.get(target)
    if record is None:
        _fail(QCISReasonCode.UNKNOWN_QAGENT, target)
    component = str(record.get("component", ""))
    return ("xy_channel" in record, "z_channel" in record, component)


def _resolve_macro_setting(authorities: Mapping[str, Any], target: str, calibration_record: Mapping[str, Any]) -> str:
    """Resolve a calibrated XY2 macro through the frozen target-setting authority chain."""

    gate_configuration = authorities.get("gate_configuration")
    waveform_registry = authorities.get("waveform_registry")
    if not isinstance(gate_configuration, Mapping) or not isinstance(waveform_registry, Mapping):
        _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, "macro setting authorities are absent")
    target_configuration = gate_configuration.get(target)
    if not isinstance(target_configuration, Mapping):
        _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"no active XY2 setting for {target}")
    setting_id = target_configuration.get("active_xy2_setting")
    settings = waveform_registry.get("settings")
    if not isinstance(setting_id, str) or not setting_id or not isinstance(settings, Mapping):
        _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"invalid active XY2 setting for {target}")
    setting = settings.get(setting_id)
    if not isinstance(setting, Mapping):
        _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"setting {setting_id!r} is not registered")
    if "targets" in setting:
        targets = setting["targets"]
        if not isinstance(targets, list | tuple) or any(not isinstance(item, str) for item in targets) or target not in targets:
            _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"setting {setting_id!r} is not applicable to {target}")
    formula = setting.get("formula_id")
    if formula != "qcis_gaussian_drag_samples_v1" or formula != calibration_record.get("formula_id"):
        _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"setting {setting_id!r} formula does not match calibration")
    return setting_id


def compile_qcis(
    program: Mapping[str, Any],
    authorities: Mapping[str, Any] | QCISAuthorities,
    idle_flux: Mapping[str, float] | None = None,
    scan_values: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    max_samples: int = 10_000,
) -> QCISCompilation:
    """Compile admitted QCIS into frozen logical arrays; never dispatches a backend."""

    authority_map: Mapping[str, Any]
    if isinstance(authorities, QCISAuthorities):
        authority_map = {
            "instruction_profile": authorities.instruction_profile,
            "qagent_registry": authorities.qagent_registry,
            "gate_configuration": authorities.gate_configuration,
            "waveform_registry": authorities.waveform_registry,
            "clock": authorities.clock_profile,
            "compiler": authorities.compiler_snapshot,
            "calibration": authorities.calibration,
            "expected_sha256": authorities.expected_sha256,
            "templates": authorities.templates,
        }
    else:
        authority_map = authorities
    idle = _validate_idle_flux(idle_flux)
    envelope = _envelope(program)
    template = _admit_template(envelope, authority_map)
    concrete = materialize_program(envelope.source, _resolve_bindings(envelope, template, scan_values))
    registry = _registry(authority_map)
    parsed = parse_qcis(concrete, registry)
    macro_ops = {"X", "Y", "X2P", "X2M", "Y2P", "Y2M", "XY", "XY2P", "XY2M", "RX", "RY", "RXY", "X12", "CZ", "FSIM"}
    macros = any(item.op in macro_ops for item in parsed.instructions)
    authority_sha256 = _authority_hashes(authority_map, envelope, macros=macros and isinstance(authority_map.get("calibration"), Mapping))
    return _compile_plan(envelope, concrete, parsed, authority_map, registry, idle, authority_sha256, max_samples, macros)


def _is_v02_profile(authorities: Mapping[str, Any]) -> bool:
    profile = authorities.get("instruction_profile")
    return isinstance(profile, Mapping) and profile.get("profile_version") == "0.2"


def _record_hash(record: Mapping[str, Any]) -> str:
    """Hash a calibration record without its generated self-hash field."""

    return sha256_json({str(name): _canonical_plain(value) for name, value in record.items() if name != "setting_hash"})


def _accepted_record_evidence(
    record_id: str,
    record: Mapping[str, Any],
    *,
    id_field: str,
    expected_type: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Validate the immutable v0.2 calibration record contract and return trace evidence."""

    if record.get(id_field) != record_id:
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id}.{id_field} does not match its registry key")
    if expected_type is not None and record.get("gate_type", record.get("mapper_type")) != expected_type:
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id} has unexpected type")
    if target is not None and record.get("target") != target:
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id}.target does not match {target}")
    if record.get("status") != "accepted":
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id} is not accepted")
    revision = record.get("revision")
    if type(revision) is not int or revision <= 0:
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id}.revision must be a positive integer")
    run_id = record.get("calibration_run_id")
    if not isinstance(run_id, str) or not run_id:
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id}.calibration_run_id is required")
    setting_hash = record.get("setting_hash")
    if not isinstance(setting_hash, str) or setting_hash != _record_hash(record):
        _fail(QCISReasonCode.SETTING_INVALID, f"{record_id}.setting_hash does not match record content")
    return {id_field: record_id, "revision": revision, "setting_hash": setting_hash, "calibration_run_id": run_id}


def _setting_evidence(authorities: Mapping[str, Any], setting_id: str, setting: Mapping[str, Any]) -> dict[str, Any]:
    if not _is_v02_profile(authorities):
        return {"setting_id": setting_id}
    evidence = _accepted_record_evidence(
        setting_id,
        setting,
        id_field="setting_id",
        target=str(setting.get("target", "")),
    )
    return evidence


def _active_setting(
    authorities: Mapping[str, Any], target: str, key: str, *, expected_gate_type: str | None = None
) -> tuple[str, Mapping[str, Any]]:
    configuration = authorities.get("gate_configuration")
    waveform_registry = authorities.get("waveform_registry")
    target_configuration = configuration.get(target) if isinstance(configuration, Mapping) else None
    settings = waveform_registry.get("settings") if isinstance(waveform_registry, Mapping) else None
    setting_id = target_configuration.get(key) if isinstance(target_configuration, Mapping) else None
    if not isinstance(setting_id, str) or not isinstance(settings, Mapping):
        _fail(QCISReasonCode.SETTING_NOT_FOUND, f"{target}.{key}")
    setting = settings.get(setting_id)
    if not isinstance(setting, Mapping):
        _fail(QCISReasonCode.SETTING_NOT_FOUND, setting_id)
    targets = setting.get("targets")
    if targets is not None and (not isinstance(targets, (list, tuple)) or target not in targets):
        _fail(QCISReasonCode.SETTING_INVALID, f"setting {setting_id!r} does not apply to {target}")
    if _is_v02_profile(authorities):
        _accepted_record_evidence(
            setting_id,
            setting,
            id_field="setting_id",
            expected_type=expected_gate_type,
            target=target,
        )
    return setting_id, setting


def _finite_setting_number(record: Mapping[str, Any], names: tuple[str, ...], *, default: float | None = None) -> float:
    for name in names:
        if name in record:
            if isinstance(record[name], bool):
                break
            try:
                value = float(record[name])
            except (TypeError, ValueError, OverflowError):
                break
            if math.isfinite(value):
                return value
            break
    if default is not None:
        return default
    _fail(QCISReasonCode.SETTING_INVALID, f"missing finite setting field {names[0]}")


def _xy_setting(authorities: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]], target: str, key: str) -> dict[str, Any]:
    """Resolve both the v0.2 setting shape and the frozen v0.1 fixture shape."""

    setting_id, setting = _active_setting(authorities, target, key)
    _, _, component = _capabilities(registry, target)
    calibration = authorities.get("calibration")
    legacy = calibration.get(component) if isinstance(calibration, Mapping) else None
    source = legacy if isinstance(legacy, Mapping) and key == "active_xy2_setting" else setting
    if source is legacy:
        _resolve_macro_setting(authorities, target, legacy)
    waveform = source.get("waveform", source)
    if not isinstance(waveform, Mapping):
        _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id}.waveform")
    length_raw = waveform.get("length_samples", waveform.get("length"))
    length = _strict_positive_integer(length_raw, invalid_code=QCISReasonCode.SETTING_INVALID, detail=f"{setting_id}.length")
    amplitude = _finite_setting_number(waveform, ("amplitude_GHz", "amplitude"))
    try:
        frequency = _finite_setting_number(waveform, ("carrier_frequency_GHz", "frequency_GHz", "frequency"))
    except QCISCompilationError:
        if key != "active_xy12_setting":
            raise
        target_record = registry[target]
        f01 = _finite_setting_number(target_record, ("f01_GHz", "idle_f01_GHz"))
        anharmonicity = _finite_setting_number(target_record, ("anharmonicity_GHz",))
        frequency = f01 + anharmonicity + _finite_setting_number(waveform, ("frequency_detune_GHz", "detune_GHz"), default=0.0)
    drag = _finite_setting_number(waveform, ("dragAlpha_samples", "drag_alpha"), default=0.0)
    phase_offset = _finite_setting_number(waveform, ("phase_offset_rad", "phase"), default=0.0)
    index_value = waveform.get("wave_index")
    if index_value is None:
        envelope_class = str(waveform.get("envelope_class", "gaussian" if "r_sigma_samples" in waveform else "rectangle"))
        index_value = {"rectangle": 0, "rect": 0, "gaussian": 1, "flattop": 2}.get(envelope_class)
    if type(index_value) is not int or index_value not in {0, 1, 2}:
        _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id}.wave_index")
    if index_value == 0:
        shape_parameter = (int(waveform.get("width_samples", waveform.get("width", length))),)
    elif index_value == 1:
        shape_parameter = (_finite_setting_number(waveform, ("r_sigma_samples", "r_sigma")),)
    else:
        edge = waveform.get("edge_samples", waveform.get("edge"))
        if type(edge) is not int:
            _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id}.edge_samples")
        shape_parameter = (edge,)
    envelope, derivative = analytic_waveform(index_value, length, shape_parameter)
    base = amplitude * (envelope - 1j * drag * derivative)
    return {
        "setting_id": setting_id,
        "evidence": _setting_evidence(authorities, setting_id, setting),
        "length": length,
        "frequency": frequency,
        "phase_offset": phase_offset,
        "base": base.astype("<c16"),
    }


def _normalized_angle(value: float) -> float:
    result = (value + math.pi) % (2.0 * math.pi) - math.pi
    return math.pi if result == -math.pi else result


def _mapper_record(authorities: Mapping[str, Any], target: str, kind: str) -> tuple[str, Mapping[str, Any]]:
    configuration = authorities.get("gate_configuration")
    waveform_registry = authorities.get("waveform_registry")
    target_configuration = configuration.get(target) if isinstance(configuration, Mapping) else None
    mappers = waveform_registry.get("mappers") if isinstance(waveform_registry, Mapping) else None
    key = f"active_{kind.lower()}_mapper"
    raw = target_configuration.get(key) if isinstance(target_configuration, Mapping) else None
    expected_type = f"{kind.upper()}_MAPPER"
    if isinstance(raw, Mapping):
        if _is_v02_profile(authorities):
            _fail(QCISReasonCode.MAPPER_NOT_FOUND, f"{target}.{key} must select a registered mapper id")
        mapper_id = raw.get("mapper_id", key)
        if not isinstance(mapper_id, str) or not mapper_id:
            _fail(QCISReasonCode.MAPPER_NOT_FOUND, f"{target}.{key}")
        return mapper_id, raw
    if not isinstance(raw, str) or not isinstance(mappers, Mapping) or not isinstance(mappers.get(raw), Mapping):
        _fail(QCISReasonCode.MAPPER_NOT_FOUND, f"{target}.{key}")
    mapper = mappers[raw]
    if _is_v02_profile(authorities):
        _accepted_record_evidence(raw, mapper, id_field="mapper_id", expected_type=expected_type)
        if kind == "g2zbias" and (mapper.get("interpolation") != "piecewise_linear" or mapper.get("extrapolation") != "reject"):
            _fail(QCISReasonCode.SETTING_INVALID, f"{raw} must use piecewise_linear interpolation without extrapolation")
    return raw, mapper


def _mapper_evidence(authorities: Mapping[str, Any], mapper_id: str, mapper: Mapping[str, Any]) -> dict[str, Any]:
    if _is_v02_profile(authorities):
        return _accepted_record_evidence(
            mapper_id,
            mapper,
            id_field="mapper_id",
            expected_type=str(mapper.get("mapper_type", "")),
        )
    return {"mapper_id": mapper_id, "record_sha256": sha256_json(mapper)}


def _f012_to_flux(mapper: Mapping[str, Any], detune: float, current_flux: float, bounds: tuple[float, float] | None) -> float:
    fmax = _finite_setting_number(mapper, ("f01max_GHz",))
    k = _finite_setting_number(mapper, ("k_rad_per_phi0",))
    offset = _finite_setting_number(mapper, ("idle_flux_offset_phi0",))
    if fmax <= 0.0 or k == 0.0:
        _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, "invalid F012ZBIAS mapper parameters")

    def frequency(z: float) -> float:
        return fmax * math.sqrt(abs(math.cos(k * (z - offset))))

    def nearest_root(target_frequency: float, reference: float) -> float:
        ratio = target_frequency / fmax
        if ratio < 0.0 or ratio > 1.0:
            _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, f"frequency {target_frequency} GHz is outside mapper domain")
        alpha = math.acos(ratio * ratio)
        candidates = [offset + (sign * alpha + n * math.pi) / k for n in range(-64, 65) for sign in (-1.0, 1.0)]
        if bounds is not None:
            candidates = [value for value in candidates if bounds[0] <= value <= bounds[1]]
        if not candidates:
            _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, "F012ZBIAS has no root inside device bounds")
        return min(candidates, key=lambda value: abs(value - reference))

    z1 = nearest_root(frequency(current_flux), current_flux)
    z2 = nearest_root(frequency(z1) + detune, z1)
    return z2 - z1


def _g2_to_flux(mapper: Mapping[str, Any], detune: float) -> float:
    x = mapper.get("coupling_detune_GHz", mapper.get("g_GHz"))
    y = mapper.get("zbias_offset_phi0", mapper.get("flux_offset_phi0"))
    if not isinstance(x, (list, tuple)) or not isinstance(y, (list, tuple)) or len(x) != len(y) or len(x) < 2:
        _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, "G2ZBIAS arrays are invalid")
    try:
        xa = np.asarray(x, dtype="<f8")
        ya = np.asarray(y, dtype="<f8")
    except (TypeError, ValueError, OverflowError):
        _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, "G2ZBIAS arrays are not numeric")
    if not np.all(np.isfinite(xa)) or not np.all(np.isfinite(ya)) or not np.all(np.diff(xa) > 0.0):
        _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, "G2ZBIAS input must be finite and strictly increasing")
    zero = np.flatnonzero((xa == 0.0) & (ya == 0.0))
    if zero.size == 0 or detune < xa[0] or detune > xa[-1]:
        _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, "G2ZBIAS requires (0,0) and forbids extrapolation")
    return float(np.interp(detune, xa, ya))


def _compile_plan(envelope: ProgramEnvelope, concrete: str, parsed: Any, authorities: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]], idle: Mapping[str, float], authority_sha256: Mapping[str, str], max_samples: int, macros: bool) -> QCISCompilation:
    cursors: dict[str, dict[str, int]] = {}
    frames: dict[str, float] = {}
    events: list[tuple[str, str, int, np.ndarray]] = []
    steps: list[dict[str, Any]] = []
    carriers: dict[str, float] = {}

    def lanes(target: str) -> dict[str, int]:
        xy, z, _ = _capabilities(registry, target)
        item = cursors.setdefault(target, {})
        if xy:
            item.setdefault("xy", 0)
            frames.setdefault(target, 0.0)
        if z:
            item.setdefault("z", 0)
        return item

    def reserve(target: str, lane: str, start: int, length: int) -> tuple[int, int]:
        state = lanes(target)
        cursor = state.get(lane, 0)
        actual_start = cursor if start < 0 else start
        end = actual_start + length
        if end > max_samples:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, f"sample {end} exceeds budget")
        state[lane] = max(cursor, end)
        return actual_start, end

    def record_carrier(target: str, frequency: float) -> None:
        if target in carriers and carriers[target] != frequency:
            _fail(QCISReasonCode.CARRIER_CHANGE_UNSUPPORTED, f"carrier changes on {target}")
        carriers[target] = frequency

    def emit_xy(target: str, setting_key: str, phase: float, scale: float, instruction_index: int, source_op: str) -> None:
        xy, _, component = _capabilities(registry, target)
        if not xy or component not in {"q1", "q2"}:
            _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"macro target {target}")
        setting = _xy_setting(authorities, registry, target, setting_key)
        record_carrier(target, float(setting["frequency"]))
        start, end = reserve(target, "xy", -1, int(setting["length"]))
        absolute_phase = phase + float(setting["phase_offset"]) + frames[target]
        rotation = complex(math.cos(-absolute_phase), math.sin(-absolute_phase))
        samples = np.asarray(setting["base"], dtype="<c16") * scale * rotation
        events.append(("xy", component, start, samples.astype("<c16")))
        step = {"index": instruction_index, "op": source_op, "setting_id": setting["setting_id"], "phase": absolute_phase, "scale": scale, "emitted_interval": [start, end]}
        if _is_v02_profile(authorities):
            step["setting"] = setting["evidence"]
        steps.append(step)

    def target_configuration(target: str) -> Mapping[str, Any]:
        configuration = authorities.get("gate_configuration")
        value = configuration.get(target) if isinstance(configuration, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    def composite_waveform(spec: Mapping[str, Any], length: int) -> np.ndarray:
        wave_index = spec.get("wave_index")
        if wave_index is None:
            wave_index = {"rectangle": 0, "rect": 0, "gaussian": 1, "flattop": 2, "acz": 5}.get(str(spec.get("waveform_class", spec.get("envelope_class", "rectangle"))))
        if type(wave_index) is not int or wave_index not in {0, 1, 2, 5}:
            _fail(QCISReasonCode.SETTING_INVALID, "composite detune waveform class is invalid")
        if wave_index == 0:
            parameter = (int(spec.get("width_samples", spec.get("width", length))),)
        elif wave_index == 1:
            parameter = (_finite_setting_number(spec, ("r_sigma_samples", "r_sigma")),)
        elif wave_index == 2:
            edge = spec.get("edge_samples", spec.get("edge"))
            if type(edge) is not int:
                _fail(QCISReasonCode.SETTING_INVALID, "flattop edge_samples is missing")
            parameter = (edge,)
        else:
            params = spec.get("parameters", spec)
            if not isinstance(params, Mapping):
                _fail(QCISReasonCode.SETTING_INVALID, "acz parameters are invalid")
            parameter = tuple(_finite_setting_number(params, (name,)) for name in ("thf", "thi", "lam2", "lam3"))
        return analytic_waveform(wave_index, length, parameter)[0]

    def compile_composite(instruction: Any) -> None:
        coupler = str(instruction.fields["target"])
        _, coupler_z, component = _capabilities(registry, coupler)
        if not coupler_z or component != "c":
            _fail(QCISReasonCode.SETTING_INVALID, f"{instruction.op} target must be a coupler")
        setting_key = "active_cz_setting" if instruction.op == "CZ" else "active_fsim_setting"
        setting_id, setting = _active_setting(authorities, coupler, setting_key, expected_gate_type=instruction.op)
        coupler_record = registry[coupler]
        endpoints = coupler_record.get("endpoints", coupler_record.get("qubits", coupler_record.get("connected_qagents")))
        if not isinstance(endpoints, (list, tuple)) or len(endpoints) != 2 or any(item not in registry for item in endpoints):
            q0, q1 = setting.get("q0_target"), setting.get("q1_target")
            endpoints = (q0, q1)
        if not isinstance(endpoints, (list, tuple)) or len(endpoints) != 2 or any(not isinstance(item, str) or item not in registry for item in endpoints):
            _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id} cannot resolve two endpoint qubits")
        q0, q1 = str(endpoints[0]), str(endpoints[1])
        for target in (q0, q1):
            xy, z, endpoint_component = _capabilities(registry, target)
            if not xy or not z or endpoint_component not in {"q1", "q2"}:
                _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id} endpoint {target!r} lacks qubit XY/Z lanes")
        if q0 == q1:
            _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id} endpoints must be distinct")
        length_raw = setting.get("duration_samples", setting.get("length_samples"))
        length = _strict_positive_integer(length_raw, invalid_code=QCISReasonCode.SETTING_INVALID, detail=f"{setting_id}.duration_samples")
        waveforms = setting.get("waveforms", setting)
        if not isinstance(waveforms, Mapping):
            _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id}.waveforms")
        specs = {
            q0: waveforms.get("q0", waveforms.get("q0_detune")),
            q1: waveforms.get("q1", waveforms.get("q1_detune")),
            coupler: waveforms.get("coupler", waveforms.get("coupler_detune")),
        }
        if any(not isinstance(value, Mapping) for value in specs.values()):
            _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id} requires q0, q1, and coupler waveforms")

        all_targets = (q0, q1, coupler)
        start = max((cursor for target in all_targets for cursor in lanes(target).values()), default=0)
        end = start + length
        if end > max_samples:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, f"{instruction.op} exceeds sample budget")
        for target in all_targets:
            for lane in lanes(target):
                cursors[target][lane] = end

        use_f_raw = setting.get("use_f012zbias_mapper", False)
        use_g_raw = setting.get("use_g2zbias_mapper", False)
        if _is_v02_profile(authorities) and (type(use_f_raw) is not bool or type(use_g_raw) is not bool):
            _fail(QCISReasonCode.SETTING_INVALID, f"{setting_id} mapper switches must be boolean")
        use_f = bool(use_f_raw)
        use_g = bool(use_g_raw)
        mapper_evidence: dict[str, dict[str, Any]] = {}
        for target in (q0, q1):
            spec = specs[target]
            assert isinstance(spec, Mapping)
            if use_f:
                mapper_id, mapper = _mapper_record(authorities, target, "f012zbias")
                record = registry[target]
                lower = record.get("flux_min_phi0")
                upper = record.get("flux_max_phi0")
                bounds: tuple[float, float] | None = None
                if isinstance(lower, (int, float)) and not isinstance(lower, bool) and isinstance(upper, (int, float)) and not isinstance(upper, bool):
                    lower_value, upper_value = float(lower), float(upper)
                    if math.isfinite(lower_value) and math.isfinite(upper_value) and lower_value <= upper_value:
                        bounds = (lower_value, upper_value)
                if _is_v02_profile(authorities) and bounds is None:
                    _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, f"{target} requires finite device flux bounds for F012ZBIAS")
                _, _, target_component = _capabilities(registry, target)
                if bounds is not None and not bounds[0] <= idle[target_component] <= bounds[1]:
                    _fail(QCISReasonCode.MAPPER_DOMAIN_ERROR, f"{target} idle flux is outside device bounds")
                amplitude = _f012_to_flux(mapper, _finite_setting_number(spec, ("frequency_detune_GHz",)), idle[target_component], bounds)
                mapper_evidence[target] = _mapper_evidence(authorities, mapper_id, mapper)
            else:
                amplitude = _finite_setting_number(spec, ("flux_offset_phi0",), default=0.0)
            events.append(("flux", _capabilities(registry, target)[2], start, amplitude * composite_waveform(spec, length)))
        coupler_spec = specs[coupler]
        assert isinstance(coupler_spec, Mapping)
        if use_g:
            mapper_id, mapper = _mapper_record(authorities, coupler, "g2zbias")
            amplitude = _g2_to_flux(mapper, _finite_setting_number(coupler_spec, ("coupling_detune_GHz",)))
            mapper_evidence[coupler] = _mapper_evidence(authorities, mapper_id, mapper)
        else:
            amplitude = _finite_setting_number(coupler_spec, ("flux_offset_phi0",), default=0.0)
        events.append(("flux", component, start, amplitude * composite_waveform(coupler_spec, length)))
        q0_phase = _finite_setting_number(setting, ("q0_calibrated_dynamic_phase_rad",))
        q1_phase = _finite_setting_number(setting, ("q1_calibrated_dynamic_phase_rad",))
        frames[q0] -= q0_phase
        frames[q1] -= q1_phase
        step = {"index": instruction.index, "op": instruction.op, "setting_id": setting_id, "interval": [start, end], "mappers": mapper_evidence, "frame_corrections": {q0: -q0_phase, q1: -q1_phase}}
        if _is_v02_profile(authorities):
            step["setting"] = _setting_evidence(authorities, setting_id, setting)
        steps.append(step)

    for instruction in parsed.instructions:
        fields = dict(instruction.fields)
        if instruction.op in {"RZ", "Z", "S", "SD", "T", "TD"}:
            target = fields["target"]
            xy, _, _ = _capabilities(registry, target)
            if not xy:
                _fail(QCISReasonCode.UNKNOWN_QAGENT, f"RZ target lacks xy lane: {target}")
            if instruction.op != "RZ" and str(target_configuration(target).get("z_gate_impl", "VIRTUAL")).upper() == "PULSE":
                _fail(QCISReasonCode.UNSUPPORTED_GATE_IMPLEMENTATION, "pulse-implemented Z gates are reserved")
            lanes(target)
            phase = float(fields["phase"]) if instruction.op == "RZ" else {"Z": -math.pi, "S": -math.pi / 2.0, "SD": math.pi / 2.0, "T": -math.pi / 4.0, "TD": math.pi / 4.0}[instruction.op]
            frames[target] += phase
            steps.append({"index": instruction.index, "op": instruction.op, "frame_after": frames[target]})
        elif instruction.op == "I":
            target = fields["targets"][0]
            state = lanes(target)
            before = dict(state)
            for lane in state:
                state[lane] += fields["length"]
                if state[lane] > max_samples:
                    _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, "idle exceeds budget")
            steps.append({"index": instruction.index, "op": "I", "cursors_before": before, "cursors_after": dict(state)})
        elif instruction.op == "B":
            targets = fields["targets"]
            current = max((value for target in targets for value in lanes(target).values()), default=0)
            for target in targets:
                for lane in lanes(target):
                    cursors[target][lane] = current
            steps.append({"index": instruction.index, "op": "B", "max_cursor": current})
        elif instruction.op == "PLS":
            target = fields["target"]
            _, z, component = _capabilities(registry, target)
            if not z:
                _fail(QCISReasonCode.UNKNOWN_QAGENT, f"PLS target lacks z lane: {target}")
            start, end = reserve(target, "z", fields["t_start"], fields["length"])
            if fields["wave_index"] == -1:
                samples = np.asarray(fields["samples"], dtype="<f8")
            else:
                envelope_values, _ = analytic_waveform(fields["wave_index"], fields["length"], fields["shape_parameter"])
                samples = float(fields["amplitude"]) * envelope_values
            events.append(("flux", component, start, samples.astype("<f8")))
            steps.append({"index": instruction.index, "op": "PLS", "interval": [start, end]})
        elif instruction.op == "PLSXY":
            target = fields["target"]
            xy, _, component = _capabilities(registry, target)
            if not xy or component not in {"q1", "q2"}:
                _fail(QCISReasonCode.UNKNOWN_QAGENT, f"PLSXY target lacks a qubit xy lane: {target}")
            start, end = reserve(target, "xy", fields["t_start"], fields["length"])
            if fields["wave_index"] == -1:
                payload = np.asarray(fields["samples"], dtype="<f8")
                half = fields["length"]
                samples = payload[:half] + 1j * payload[half:]
            else:
                frequency = float(fields["frequency"])
                record_carrier(target, frequency)
                envelope_values, derivative = analytic_waveform(fields["wave_index"], fields["length"], fields["shape_parameter"])
                base = float(fields["amplitude"]) * (envelope_values - 1j * float(fields["drag_alpha"]) * derivative)
                phase = float(fields["phase"])
                samples = base * complex(math.cos(-phase), math.sin(-phase))
            events.append(("xy", component, start, np.asarray(samples, dtype="<c16")))
            steps.append({"index": instruction.index, "op": "PLSXY", "interval": [start, end]})
        elif instruction.op == "DTN":
            target = fields["target"]
            _, z, component = _capabilities(registry, target)
            if not z:
                _fail(QCISReasonCode.SETTING_INVALID, f"DTN target lacks z lane: {target}")
            detune_id, detune_setting = _active_setting(authorities, target, "active_detune_setting")
            if str(detune_setting.get("control_role", "z")) != "z" or str(detune_setting.get("input_unit", "phi_over_phi0")) != "phi_over_phi0":
                _fail(QCISReasonCode.SETTING_INVALID, "DTN setting must use z control and phi_over_phi0")
            start, end = reserve(target, "z", -1, fields["length"])
            events.append(("flux", component, start, np.full(fields["length"], float(fields["amplitude"]), dtype="<f8")))
            step = {"index": instruction.index, "op": "DTN", "setting_id": detune_id, "interval": [start, end]}
            if _is_v02_profile(authorities):
                step["setting"] = _setting_evidence(authorities, detune_id, detune_setting)
            steps.append(step)
        elif instruction.op in {"CZ", "FSIM"}:
            compile_composite(instruction)
        elif instruction.op in {"M", "RST", "SWD", "SWA"}:
            _fail(QCISReasonCode.PARSE_ONLY_OPERATION, f"{instruction.op} is parsed but has no Stage 7 lowering")
        else:
            target = fields["target"]
            op = instruction.op
            if op == "X12":
                dimension = registry[target].get("local_dimension", registry[target].get("dimension", registry[target].get("levels", 0)))
                if type(dimension) is not int or dimension < 3:
                    _fail(QCISReasonCode.SETTING_INVALID, "X12 requires local dimension >= 3")
                emit_xy(target, "active_xy12_setting", 0.0, 1.0, instruction.index, op)
                continue
            if op in {"X2P", "X2M", "Y2P", "Y2M", "XY2P", "XY2M"}:
                phases = {"X2P": 0.0, "X2M": math.pi, "Y2P": math.pi / 2.0, "Y2M": -math.pi / 2.0, "XY2P": float(fields.get("phase", 0.0)), "XY2M": float(fields.get("phase", 0.0)) + math.pi}
                emit_xy(target, "active_xy2_setting", phases[op], 1.0, instruction.index, op)
                continue
            if op in {"X", "Y", "XY"}:
                phase = {"X": 0.0, "Y": math.pi / 2.0, "XY": float(fields.get("phase", 0.0))}[op]
                if bool(target_configuration(target).get("xy_pi_impl", False)):
                    emit_xy(target, "active_xy_setting", phase, 1.0, instruction.index, op)
                else:
                    emit_xy(target, "active_xy2_setting", phase, 1.0, instruction.index, op)
                    emit_xy(target, "active_xy2_setting", phase, 1.0, instruction.index, op)
                continue
            if op in {"RX", "RY", "RXY"}:
                azimuth = 0.0 if op == "RX" else math.pi / 2.0 if op == "RY" else _normalized_angle(float(fields["azimuth"]))
                altitude = _normalized_angle(float(fields["altitude"]))
                phase = azimuth + (math.pi if altitude < 0.0 else 0.0)
                scale = abs(altitude) / math.pi
                if bool(target_configuration(target).get("xy_pi_impl", False)):
                    emit_xy(target, "active_xy_setting", phase, scale, instruction.index, op)
                else:
                    emit_xy(target, "active_xy2_setting", phase, scale, instruction.index, op)
                    emit_xy(target, "active_xy2_setting", phase, scale, instruction.index, op)
                continue
            _fail(QCISReasonCode.UNKNOWN_OPERATION, op)

    sample_count = max((value for state in cursors.values() for value in state.values()), default=0)
    q1_xy = np.zeros(sample_count, dtype="<c16")
    q2_xy = np.zeros(sample_count, dtype="<c16")
    q1_flux = np.zeros(sample_count, dtype="<f8")
    q2_flux = np.zeros(sample_count, dtype="<f8")
    c_flux = np.zeros(sample_count, dtype="<f8")
    xy_arrays = {"q1": q1_xy, "q2": q2_xy}
    flux_arrays = {"q1": q1_flux, "q2": q2_flux, "c": c_flux}
    for kind, component, start, samples in events:
        end = start + len(samples)
        if kind == "flux":
            flux_arrays[component][start:end] += samples
            continue
        xy_arrays[component][start:end] += samples

    ast_bytes = canonical_json_bytes(parsed.payload())
    trace: dict[str, Any] = {"final_cursors": cursors, "final_frames": frames, "final_sample_count": sample_count, "schema_version": "0.2", "steps": steps}
    if macros:
        trace["authority_sha256"] = dict(authority_sha256)
    trace_bytes = canonical_json_bytes(trace)
    q1_xy, q2_xy = frozen_array(q1_xy, "<c16"), frozen_array(q2_xy, "<c16")
    q1_flux, q2_flux, c_flux = (frozen_array(q1_flux, "<f8"), frozen_array(q2_flux, "<f8"), frozen_array(c_flux, "<f8"))
    arrays = {"q1_xy": q1_xy, "q2_xy": q2_xy, "q1_flux": q1_flux, "q2_flux": q2_flux, "c_flux": c_flux}
    array_sha256 = {name: sha256_bytes(value.tobytes()) for name, value in arrays.items()}
    carrier_metadata = frozen_mapping(carriers)
    carrier_metadata_bytes = canonical_json_bytes(dict(carrier_metadata))
    coefficient_inventory_bytes = canonical_json_bytes(
        {
            "angular_conversion": "2*pi*GHz_to_rad_per_ns_once",
            "carrier_metadata_sha256": sha256_bytes(carrier_metadata_bytes),
            "dt_ns": float(authorities["clock"]["dt_ns"]),
            "epsilon_q1_c16_sha256": array_sha256["q1_xy"],
            "epsilon_q2_c16_sha256": array_sha256["q2_xy"],
            "sample_count": sample_count,
            "schema_version": "0.2",
        }
    )
    plan = QCISLogicalWaveformPlan(
        source=concrete,
        program=parsed,
        ast_sha256=sha256_bytes(ast_bytes),
        trace=frozen_mapping(trace),
        trace_sha256=sha256_bytes(trace_bytes),
        q1_xy=q1_xy, q2_xy=q2_xy, q1_flux=q1_flux, q2_flux=q2_flux, c_flux=c_flux,
        carrier_metadata=carrier_metadata, array_sha256=frozen_mapping(array_sha256), authority_sha256=frozen_mapping(authority_sha256),
    )
    return QCISCompilation(
        envelope=envelope, concrete_source=concrete, concrete_source_sha256=sha256_bytes(concrete.encode("utf-8")), plan=plan,
        ast_bytes=ast_bytes, trace_bytes=trace_bytes, q1_xy=q1_xy, q2_xy=q2_xy, q1_flux=q1_flux, q2_flux=q2_flux, c_flux=c_flux,
        carrier_metadata_bytes=carrier_metadata_bytes, effective_q1_xy=q1_xy, effective_q2_xy=q2_xy,
        effective_q1_flux=q1_flux, effective_q2_flux=q2_flux, effective_c_flux=c_flux,
        coefficient_inventory_bytes=coefficient_inventory_bytes,
    )


# Kept here as well as in sqvm.qcis so consumers resolving the compiler module
# have the frozen hand-off tamper oracles at the same stable import surface.
from .verify import verify_coefficient_inventory, verify_compilation, verify_effective_controls
