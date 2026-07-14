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


def materialize_program(source: str, bindings: Mapping[str, float]) -> str:
    """Replace registered placeholders with shortest round-trip binary64 decimals."""

    validate_canonical_source(source, allow_placeholders=True)
    result: list[str] = []
    seen: set[str] = set()
    for line in source[:-1].split("\n"):
        values: list[str] = []
        for token in line.split(" "):
            if token.startswith("$"):
                name = token[1:]
                if name not in bindings:
                    raise QCISCompilationError(QCISReasonCode.BINDING_SET_MISMATCH, f"missing {name!r}")
                values.append(canonical_float(bindings[name]))
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
        if not isinstance(spec, Mapping) or set(spec) != {"unit", "occurrences", "position"} or not isinstance(spec["unit"], str):
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"invalid binding spec {name!r}")
        occurrences = source.count(f"${name}")
        if _strict_positive_integer(spec["occurrences"], invalid_code=QCISReasonCode.BINDING_SET_MISMATCH, detail=f"binding {name!r} occurrences") != occurrences:
            _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} occurrence mismatch")
        position = spec.get("position")
        if position is not None:
            if not (isinstance(position, list | tuple) and len(position) == 2 and type(position[0]) is int and isinstance(position[1], str)):
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} position invalid")
            lines = source[:-1].split("\n")
            if position[0] < 0 or position[0] >= len(lines):
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} position outside source")
            tokens = lines[position[0]].split(" ")
            fields = {"PLSXY": {"amplitude": 5, "frequency": 6, "phase": 7, "drag_alpha": 8, "r_sigma": 9}, "PLS": {"target_flux": 5}}
            if tokens[0] not in fields or fields[tokens[0]].get(position[1]) is None or tokens[fields[tokens[0]][position[1]]] != f"${name}":
                _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} position forbidden")
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
    macros = any(item.op in {"X2P", "Y2P"} for item in parsed.instructions)
    authority_sha256 = _authority_hashes(authority_map, envelope, macros=macros)
    return _compile_plan(envelope, concrete, parsed, authority_map, registry, idle, authority_sha256, max_samples, macros)


def _compile_plan(envelope: ProgramEnvelope, concrete: str, parsed: Any, authorities: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]], idle: Mapping[str, float], authority_sha256: Mapping[str, str], max_samples: int, macros: bool) -> QCISCompilation:
    cursors: dict[str, dict[str, int]] = {}
    intervals: dict[tuple[str, str], list[tuple[int, int]]] = {}
    frames: dict[str, float] = {}
    events: list[tuple[str, str, int, int, dict[str, float]]] = []
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
        actual_start = cursor if start == -1 else start
        end = actual_start + length
        if end > max_samples:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, f"sample {end} exceeds budget")
        lane_intervals = intervals.setdefault((target, lane), [])
        if any(actual_start < prior_end and end > prior_start for prior_start, prior_end in lane_intervals):
            _fail(QCISReasonCode.TIMING_OVERLAP, f"{target}.{lane} [{actual_start},{end})")
        lane_intervals.append((actual_start, end))
        state[lane] = max(cursor, end)
        return actual_start, end

    for instruction in parsed.instructions:
        fields = dict(instruction.fields)
        if instruction.op == "RZ":
            target = fields["target"]
            xy, _, _ = _capabilities(registry, target)
            if not xy:
                _fail(QCISReasonCode.UNKNOWN_QAGENT, f"RZ target lacks xy lane: {target}")
            lanes(target)
            frames[target] += float(fields["phase"])
            steps.append({"index": instruction.index, "op": "RZ", "frame_after": frames[target]})
        elif instruction.op == "I":
            target = fields["targets"][0]
            xy, z, _ = _capabilities(registry, target)
            state = lanes(target)
            start = max(state.values(), default=0)
            end = start + fields["length"]
            if end > max_samples:
                _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, "idle exceeds budget")
            if xy:
                state["xy"] = end
                intervals.setdefault((target, "xy"), []).append((start, end))
            if z:
                state["z"] = end
                intervals.setdefault((target, "z"), []).append((start, end))
            steps.append({"index": instruction.index, "op": "I", "interval": [start, end]})
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
            events.append(("flux", component, start, end, {"value": float(fields["target_flux"])}))
            steps.append({"index": instruction.index, "op": "PLS", "interval": [start, end]})
        elif instruction.op == "PLSXY":
            target = fields["target"]
            xy, _, component = _capabilities(registry, target)
            if not xy or component not in {"q1", "q2"}:
                _fail(QCISReasonCode.UNKNOWN_QAGENT, f"PLSXY target lacks a qubit xy lane: {target}")
            frequency = float(fields["frequency"])
            if target in carriers and carriers[target] != frequency:
                _fail(QCISReasonCode.CARRIER_CHANGE_UNSUPPORTED, f"carrier changes on {target}")
            carriers[target] = frequency
            start, end = reserve(target, "xy", fields["t_start"], fields["length"])
            events.append(("xy", component, start, end, {**{key: float(fields[key]) for key in ("amplitude", "drag_alpha", "r_sigma")}, "phase": float(fields["phase"]) + frames[target]}))
            steps.append({"index": instruction.index, "op": "PLSXY", "interval": [start, end]})
        else:  # X2P/Y2P
            target = fields["target"]
            xy, _, component = _capabilities(registry, target)
            if not xy or component not in {"q1", "q2"}:
                _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"macro target {target}")
            calibration = authorities.get("calibration", {})
            record = calibration.get(component) if isinstance(calibration, Mapping) else None
            required = ("amplitude_GHz", "carrier_frequency_GHz", "dragAlpha_samples", "formula_id", "length_samples", "r_sigma_samples")
            if (
                not isinstance(calibration, Mapping)
                or not isinstance(calibration.get("calibration_id"), str)
                or not calibration["calibration_id"]
                or calibration.get("status") != "accepted_simulation"
                or not isinstance(record, Mapping)
                or any(key not in record for key in required)
                or record.get("formula_id") != "qcis_gaussian_drag_samples_v1"
            ):
                _fail(QCISReasonCode.MACRO_CALIBRATION_INCOMPLETE, f"incomplete macro calibration for {target}")
            _resolve_macro_setting(authorities, target, record)
            try:
                amplitude = float(record["amplitude_GHz"])
                frequency = float(record["carrier_frequency_GHz"])
                drag_alpha = float(record["dragAlpha_samples"])
                r_sigma = float(record["r_sigma_samples"])
            except (TypeError, ValueError, OverflowError) as exc:
                _fail(QCISReasonCode.NONCANONICAL_NUMBER, f"macro calibration numeric field: {exc}")
            length = _strict_positive_integer(
                record["length_samples"],
                invalid_code=QCISReasonCode.NONCANONICAL_NUMBER,
                detail="macro length_samples",
            )
            if not all(math.isfinite(value) for value in (amplitude, frequency, drag_alpha, r_sigma)) or r_sigma <= 0.0:
                _fail(QCISReasonCode.NONCANONICAL_NUMBER, "macro waveform parameters are outside the formula domain")
            if target in carriers and carriers[target] != frequency:
                _fail(QCISReasonCode.CARRIER_CHANGE_UNSUPPORTED, f"carrier changes on {target}")
            carriers[target] = frequency
            start, end = reserve(target, "xy", -1, length)
            phase = (0.0 if instruction.op == "X2P" else math.pi / 2.0) + frames[target]
            events.append(("xy", component, start, end, {"amplitude": amplitude, "drag_alpha": drag_alpha, "r_sigma": r_sigma, "phase": phase}))
            steps.append({"index": instruction.index, "op": instruction.op, "phase": phase, "emitted_interval": [start, end], "calibration_keys": list(required)})

    sample_count = max((value for state in cursors.values() for value in state.values()), default=0)
    q1_xy = np.zeros(sample_count, dtype="<c16")
    q2_xy = np.zeros(sample_count, dtype="<c16")
    q1_flux = np.full(sample_count, idle["q1"], dtype="<f8")
    q2_flux = np.full(sample_count, idle["q2"], dtype="<f8")
    c_flux = np.full(sample_count, idle["c"], dtype="<f8")
    xy_arrays = {"q1": q1_xy, "q2": q2_xy}
    flux_arrays = {"q1": q1_flux, "q2": q2_flux, "c": c_flux}
    for kind, component, start, end, values in events:
        if kind == "flux":
            flux_arrays[component][start:end] = values["value"]
            continue
        length = end - start
        center = (length - 1) / 2.0
        sigma = values["r_sigma"]
        for offset in range(length):
            coordinate = float(offset) - center
            gaussian = math.exp(-0.5 * (coordinate / sigma) ** 2)
            derivative = gaussian * (-coordinate / (sigma**2))
            value = values["amplitude"] * complex(gaussian, -values["drag_alpha"] * derivative)
            xy_arrays[component][start + offset] = value * complex(math.cos(-values["phase"]), math.sin(-values["phase"]))

    ast_bytes = canonical_json_bytes(parsed.payload())
    trace: dict[str, Any] = {"final_cursors": cursors, "final_frames": frames, "final_sample_count": sample_count, "schema_version": "0.1", "steps": steps}
    if macros:
        trace = {"authority_sha256": dict(authority_sha256), "final_cursors": cursors, "final_sample_count": sample_count, "schema_version": "0.1", "steps": steps}
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
            "schema_version": "0.1",
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
