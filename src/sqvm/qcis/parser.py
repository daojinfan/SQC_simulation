"""Strict QCIS template admission and parser for the Stage 7.0 profile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .canonical import (
    canonical_float,
    parse_canonical_float,
    parse_canonical_integer,
    parse_signed_integer_token,
    sha256_bytes,
    tokenize_source,
    validate_canonical_source,
)
from .errors import QCISCompilationError, QCISReasonCode
from .models import (
    BindingPosition,
    BindingSpec,
    ProgramEnvelope,
    QCISInstruction,
    QCISProgram,
    QCISTemplate,
    frozen_mapping,
)


PROGRAM_SCHEMA_VERSION = "0.1"
INSTRUCTION_SET_ID = "qcis_stage7_calibration_v1"
SOURCE_FORMAT = "qcis_template"
_ENVELOPE_KEYS = frozenset(
    {
        "program_schema_version",
        "instruction_set_id",
        "template_id",
        "template_sha256",
        "source_format",
        "source",
        "bindings",
    }
)
_DEFAULT_QAGENTS = frozenset({"Q1", "Q2", "C"})


def _fail(code: QCISReasonCode, detail: str) -> None:
    raise QCISCompilationError(code, detail)


def _as_binding_spec(name: str, value: BindingSpec | Mapping[str, Any]) -> BindingSpec:
    if isinstance(value, BindingSpec):
        return value
    if not isinstance(value, Mapping):
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} must be an object")
    try:
        if not isinstance(value["unit"], str) or type(value["occurrences"]) is not int or value["occurrences"] <= 0:
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} occurrences must be a positive integer")
        positions = tuple(
            BindingPosition(line=item["line"], op=item["op"], operand=item["operand"])
            for item in value["positions"]
        )
        if any(type(item.line) is not int or not isinstance(item.op, str) or not isinstance(item.operand, str) for item in positions):
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} position types are invalid")
        return BindingSpec(
            binding_id=name,
            unit=value["unit"],
            positions=positions,
            occurrences=value["occurrences"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"invalid binding {name!r}: {exc}")


def _as_template(template: QCISTemplate | Mapping[str, Any]) -> QCISTemplate:
    if isinstance(template, QCISTemplate):
        return template
    if not isinstance(template, Mapping):
        _fail(QCISReasonCode.TEMPLATE_ID_MISMATCH, "template must be an object")
    try:
        source = str(template["source"])
        validate_canonical_source(source, allow_placeholders=True)
        actual_sha = sha256_bytes(source.encode("utf-8"))
        if "template_sha256" in template and str(template["template_sha256"]) != actual_sha:
            _fail(QCISReasonCode.TEMPLATE_SHA_MISMATCH, "template source digest does not match")
        bindings = {
            str(name): _as_binding_spec(str(name), value)
            for name, value in dict(template.get("bindings", {})).items()
        }
        return QCISTemplate(
            template_id=str(template["template_id"]),
            source=source,
            binding_specs=frozen_mapping(bindings),
        )
    except KeyError as exc:
        _fail(QCISReasonCode.TEMPLATE_ID_MISMATCH, f"template misses {exc.args[0]!r}")


def _as_envelope(envelope: ProgramEnvelope | Mapping[str, Any]) -> ProgramEnvelope:
    if isinstance(envelope, ProgramEnvelope):
        return envelope
    if not isinstance(envelope, Mapping) or set(envelope) != _ENVELOPE_KEYS:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "program envelope keys are not exact")
    try:
        return ProgramEnvelope(
            program_schema_version=str(envelope["program_schema_version"]),
            instruction_set_id=str(envelope["instruction_set_id"]),
            template_id=str(envelope["template_id"]),
            template_sha256=str(envelope["template_sha256"]),
            source_format=str(envelope["source_format"]),
            source=str(envelope["source"]),
            bindings=frozen_mapping(dict(envelope["bindings"])),
        )
    except (KeyError, TypeError) as exc:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"invalid program envelope: {exc}")


def _placeholder_positions(source: str) -> dict[str, list[BindingPosition]]:
    positions: dict[str, list[BindingPosition]] = {}
    for instruction_index, tokens in enumerate(tokenize_source(source)):
        for token_index, token in enumerate(tokens):
            if token.startswith("$"):
                positions.setdefault(token[1:], []).append(
                    BindingPosition(line=instruction_index + 1, op=tokens[0], operand=str(token_index))
                )
    return positions


def admit_program(
    envelope: ProgramEnvelope | Mapping[str, Any],
    template: QCISTemplate | Mapping[str, Any],
) -> ProgramEnvelope:
    """Validate the exact Stage 7.0 template envelope without materializing it."""

    program = _as_envelope(envelope)
    accepted_template = _as_template(template)
    if program.program_schema_version != PROGRAM_SCHEMA_VERSION:
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "unsupported program schema version")
    if program.instruction_set_id != INSTRUCTION_SET_ID:
        _fail(QCISReasonCode.PROFILE_AUTHORITY_HASH_MISMATCH, "wrong instruction set")
    if program.source_format != SOURCE_FORMAT:
        _fail(QCISReasonCode.NONCANONICAL_SOURCE, "source_format must be qcis_template")
    validate_canonical_source(program.source, allow_placeholders=True)
    if program.template_id != accepted_template.template_id:
        _fail(QCISReasonCode.TEMPLATE_ID_MISMATCH, "template id is not admitted")
    if program.template_sha256 != sha256_bytes(accepted_template.source.encode("utf-8")):
        _fail(QCISReasonCode.TEMPLATE_SHA_MISMATCH, "template hash is not admitted")
    if program.source != accepted_template.source:
        _fail(QCISReasonCode.TEMPLATE_SHA_MISMATCH, "template source bytes are not admitted")

    declared = {str(name): _as_binding_spec(str(name), spec) for name, spec in program.bindings.items()}
    expected = dict(accepted_template.binding_specs)
    if set(declared) != set(expected):
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "binding keys differ from template")
    found_positions = _placeholder_positions(program.source)
    if set(found_positions) != set(expected):
        _fail(QCISReasonCode.BINDING_SET_MISMATCH, "placeholder set differs from template")
    for name, expected_spec in expected.items():
        supplied = declared[name]
        if supplied.unit != expected_spec.unit:
            _fail(QCISReasonCode.BINDING_UNIT_MISMATCH, f"binding {name!r} unit differs")
        if supplied.occurrences != expected_spec.occurrences:
            _fail(QCISReasonCode.BINDING_SET_MISMATCH, f"binding {name!r} occurrence count differs")
        if tuple(supplied.positions) != tuple(expected_spec.positions):
            _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} positions differ")
        if tuple(expected_spec.positions) != tuple(found_positions[name]):
            _fail(QCISReasonCode.BINDING_POSITION_FORBIDDEN, f"binding {name!r} source positions differ")
    return program


def parse_qcis(source: str, qagents: Mapping[str, Any] | None = None) -> QCISProgram:
    """Parse a fully materialized canonical QCIS source into an immutable AST."""

    validate_canonical_source(source, allow_placeholders=False)
    allowed_agents = frozenset(qagents or _DEFAULT_QAGENTS)
    instructions: list[QCISInstruction] = []
    accepted = {
        "X", "Y", "X2P", "X2M", "Y2P", "Y2M", "XY", "XY2P", "XY2M",
        "RX", "RY", "RXY", "X12", "PLS", "PLSXY", "I", "RZ", "Z", "S",
        "SD", "T", "TD", "DTN", "CZ", "FSIM", "B", "M", "RST", "SWD", "SWA",
    }
    for index, tokens in enumerate(tokenize_source(source)):
        op = tokens[0]
        if op not in accepted:
            _fail(QCISReasonCode.UNKNOWN_OPERATION, f"operation {op!r} is reserved or unknown")
        instruction = _parse_instruction(index, tokens, allowed_agents)
        instructions.append(instruction)
    return QCISProgram(instructions=tuple(instructions))


def _agent(token: str, allowed_agents: frozenset[str]) -> str:
    if token not in allowed_agents:
        _fail(QCISReasonCode.UNKNOWN_QAGENT, f"unknown qagent {token!r}")
    return token


def _require_arity(tokens: Sequence[str], count: int) -> None:
    if len(tokens) != count:
        _fail(QCISReasonCode.ARITY_MISMATCH, f"{tokens[0]} requires {count - 1} operands")


def _integer(token: str, *, allow_minus_one: bool = False) -> int:
    value = parse_canonical_integer(token, allow_minus_one=allow_minus_one)
    return value


def _signed_integer(token: str) -> int:
    return parse_signed_integer_token(token)


def _number(token: str) -> float:
    return parse_canonical_float(token)


def _parse_instruction(index: int, tokens: Sequence[str], allowed_agents: frozenset[str]) -> QCISInstruction:
    op = tokens[0]
    if op in {"PLS", "PLSXY"}:
        return _parse_pulse(index, tokens, allowed_agents)
    if op == "I":
        _require_arity(tokens, 3)
        length = _integer(tokens[2])
        if length <= 0:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, "I length must be positive")
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"targets": (_agent(tokens[1], allowed_agents),), "length": length}))
    if op == "RZ":
        _require_arity(tokens, 3)
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "phase": _number(tokens[2])}))
    if op == "B":
        if len(tokens) < 3:
            _fail(QCISReasonCode.ARITY_MISMATCH, "B requires at least two qagents")
        targets = tuple(_agent(token, allowed_agents) for token in tokens[1:])
        if len(set(targets)) != len(targets):
            _fail(QCISReasonCode.ARITY_MISMATCH, "B qagents must be distinct")
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"targets": targets}))
    if op == "DTN":
        _require_arity(tokens, 4)
        length = _integer(tokens[2])
        if length <= 0:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, "DTN length must be positive")
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "length": length, "amplitude": _number(tokens[3])}))
    if op in {"XY", "XY2P", "XY2M"}:
        _require_arity(tokens, 3)
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "phase": _number(tokens[2])}))
    if op in {"RX", "RY"}:
        _require_arity(tokens, 3)
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "altitude": _number(tokens[2])}))
    if op == "RXY":
        _require_arity(tokens, 4)
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "azimuth": _number(tokens[2]), "altitude": _number(tokens[3])}))
    if op == "SWD":
        _require_arity(tokens, 4)
        length = _integer(tokens[2])
        if length <= 0:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, "SWD length must be positive")
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "length": length, "value": _number(tokens[3])}))
    if op == "SWA":
        _require_arity(tokens, 3)
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents), "value": _number(tokens[2])}))
    _require_arity(tokens, 2)
    return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": _agent(tokens[1], allowed_agents)}))


def _parse_pulse(index: int, tokens: Sequence[str], allowed_agents: frozenset[str]) -> QCISInstruction:
    op = tokens[0]
    if len(tokens) < 5:
        _fail(QCISReasonCode.ARITY_MISMATCH, f"{op} pulse operands are incomplete")
    target = _agent(tokens[1], allowed_agents)
    wave_index = _signed_integer(tokens[2])
    start = _signed_integer(tokens[3])
    if wave_index == -1:
        if len(tokens) < 5:
            _fail(QCISReasonCode.ARITY_MISMATCH, f"{op} numeric payload is empty")
        samples = tuple(_number(token) for token in tokens[4:])
        if op == "PLSXY" and len(samples) % 2 != 0:
            _fail(QCISReasonCode.ARITY_MISMATCH, "numeric PLSXY requires equal I and Q payloads")
        length = len(samples) if op == "PLS" else len(samples) // 2
        if length <= 0:
            _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, f"{op} numeric payload is empty")
        return QCISInstruction(index=index, op=op, fields=frozen_mapping({"target": target, "wave_index": -1, "t_start": start, "length": length, "samples": samples}))

    supported = {0, 1, 2} if op == "PLSXY" else {0, 1, 2, 5}
    if wave_index not in supported:
        _fail(QCISReasonCode.UNSUPPORTED_WAVE_INDEX, f"{op} wave index {wave_index}")
    expected = 13 if wave_index == 5 else 10
    _require_arity(tokens, expected)
    length = _integer(tokens[4])
    if length <= 0:
        _fail(QCISReasonCode.TIMING_OUT_OF_BUDGET, f"{op} length must be positive")
    amplitude = _number(tokens[5])
    frequency = _number(tokens[6])
    phase = _number(tokens[7])
    drag_alpha = _number(tokens[8])
    if op == "PLS" and tuple(tokens[6:9]) != ("0", "0", "0"):
        _fail(QCISReasonCode.ARITY_MISMATCH, "PLS frequency, phase, and drag placeholders must be literal 0")
    if op == "PLSXY" and wave_index == 0 and tokens[8] != "0":
        _fail(QCISReasonCode.SETTING_INVALID, "rectangle PLSXY does not support DRAG")

    if wave_index in {0, 2}:
        parameter = _integer(tokens[9])
        if wave_index == 0 and not (1 <= parameter <= length):
            _fail(QCISReasonCode.SETTING_INVALID, "rectangle requires 1 <= width <= length")
        if wave_index == 2 and not (parameter > 0 and 2 * parameter <= length):
            _fail(QCISReasonCode.SETTING_INVALID, "flattop requires edge > 0 and 2*edge <= length")
        shape_parameter: tuple[float | int, ...] = (parameter,)
    elif wave_index == 1:
        parameter = _number(tokens[9])
        if parameter <= 0.0:
            _fail(QCISReasonCode.SETTING_INVALID, "gaussian r_sigma must be positive")
        shape_parameter = (parameter,)
    else:
        shape_parameter = tuple(_number(token) for token in tokens[9:13])

    return QCISInstruction(index=index, op=op, fields=frozen_mapping({
        "target": target,
        "wave_index": wave_index,
        "t_start": start,
        "length": length,
        "amplitude": amplitude,
        "frequency": frequency,
        "phase": phase,
        "drag_alpha": drag_alpha,
        "shape_parameter": shape_parameter,
    }))


def ast_payload(program: QCISProgram) -> dict[str, Any]:
    """Return the frozen canonical AST payload used in the hash contract."""

    rows: list[dict[str, Any]] = []
    for instruction in program.instructions:
        row = {"index": instruction.index, "op": instruction.op}
        row.update(dict(instruction.fields))
        rows.append(row)
    return program.payload()
