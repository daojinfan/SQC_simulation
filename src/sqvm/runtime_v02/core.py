"""Fail-closed Runtime 0.2 contracts for the compiler-evidence lane."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import math
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
from types import MappingProxyType
from typing import Any, Iterable, Mapping

import yaml

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root
from sqvm.qcis import compile_qcis, verify_compilation, verify_drive_event_inventory
from sqvm.qcis.canonical import sha256_json as qcis_sha256_json
from sqvm.runtime.config import (
    AXIS_KEYS,
    EXECUTION_KEYS,
    PUBLICATION_KEYS,
    _UniqueKeySafeLoader,
    _exact_keys,
    _execution,
    _identifier,
    _mapping,
    _resolve_relative,
    _validate_case,
    _validate_frozen_snapshots,
)
from sqvm.runtime.dataset import ALLOWED_DTYPES, decode_numeric_values, encode_numeric_values
from sqvm.runtime.models import ExecutionSettings, RunVerificationReport, ScanAxis, frozen_mapping


SCHEMA_VERSION = "0.2"
EXPERIMENT_ID = "platform_qcis_compile_smoke_v1"
BACKEND_ID = "qcis_compiler_only_v1"
AUTHORITY_PATH = "configs/runtime/stage6v02/compiler_fixture_authority_v1.json"
APPROVAL_PATH = "configs/runtime/stage6v02/compiler_fixture_approval_v1.json"
AUTHORITY_ID = "2B255F518ABA1A0949965CB5E53A7AE6B19BD619A8F1739D9A6D2B1458E34EB5"
APPROVAL_SHA256 = "70D268BCE6DAFC1F49AB1E3A9BE9BEC82B32099E148517B2EAC30B50D071A445"
REQUEST_PATH = "configs/experiments/platform_qcis_compile_smoke_v1.yaml"
ROOT_KEYS = {
    "schema_version", "experiment_id", "backend_id", "device_snapshot", "calibration_snapshot",
    "parameters", "program", "scan", "execution", "publication",
}
PROGRAM_KEYS = {
    "program_schema_version", "instruction_set_id", "template_id", "template_sha256",
    "source_format", "source", "bindings",
}
CLAIM_ENVELOPE = {
    "backend_execution": "absent",
    "calibration_claim": "none",
    "evidence_class": "compiler_test_fixture",
    "measurement_payload": None,
    "observation_model": "absent",
    "physics_claim": "none",
    "recommendation_eligible": False,
}
RESULT_SCHEMA = MappingProxyType({
    "instruction_count": MappingProxyType({"dtype": "<u8", "unit": "count", "semantic_role": "compiler_structure"}),
    "logical_sample_count": MappingProxyType({"dtype": "<u8", "unit": "samples", "semantic_role": "compiler_structure"}),
    "trace_byte_count": MappingProxyType({"dtype": "<u8", "unit": "bytes", "semantic_role": "compiler_structure"}),
    "logical_array_bytes": MappingProxyType({"dtype": "<u8", "unit": "bytes", "semantic_role": "compiler_structure"}),
})
_SHA256 = __import__("re").compile(r"^[0-9A-F]{64}$")


@dataclass(frozen=True, slots=True)
class ExperimentRequestV02:
    source_path: Path
    repository_root: Path
    schema_version: str
    experiment_id: str
    backend_id: str
    device_snapshot: Path
    calibration_snapshot: Path
    parameters: Mapping[str, Any]
    program: Mapping[str, Any]
    axes: tuple[ScanAxis, ...]
    repetitions: int
    execution: ExecutionSettings
    allow_existing_target: bool
    authority: Mapping[str, Any]
    authority_sha256: str


@dataclass(frozen=True, slots=True)
class ScanPointV02:
    point_index: int
    repetition: int
    coordinates: tuple[tuple[str, float, str], ...]
    seed: int
    point_id: str
    request_sha256: str
    program_authority_sha256: str

    def payload(self, *, include_point_id: bool = True) -> dict[str, Any]:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": EXPERIMENT_ID,
            "request_sha256": self.request_sha256,
            "program_authority_sha256": self.program_authority_sha256,
            "point_index": self.point_index,
            "repetition": self.repetition,
            "coordinates": [
                {"axis": name, "value": value, "unit": unit}
                for name, value, unit in self.coordinates
            ],
            "seed": self.seed,
        }
        if include_point_id:
            payload["point_id"] = self.point_id
        return payload


@dataclass(frozen=True, slots=True)
class CompiledPointV02:
    point: ScanPointV02
    concrete_source: bytes
    ast_bytes: bytes
    trace_bytes: bytes
    logical_inventory: Mapping[str, Any]
    result: Mapping[str, Any]


def peek_request_schema(path: str | Path, repository_root: str | Path | None = None) -> str:
    lexical_source = Path(path).absolute()
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(lexical_source)
    try:
        relative = lexical_source.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("runtime request path must stay inside the repository") from exc
    source = _safe_regular_file(root, relative)
    try:
        root = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot inspect runtime request schema: {exc}") from exc
    value = _mapping(root, "request").get("schema_version")
    if value not in {"0.1", SCHEMA_VERSION}:
        raise ValueError("unsupported runtime request schema_version")
    return value


def load_experiment_request_v02(
    path: str | Path,
    repository_root: str | Path | None = None,
) -> ExperimentRequestV02:
    lexical_source = Path(path).absolute()
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(lexical_source)
    try:
        relative_source = lexical_source.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("Runtime 0.2 request path must stay inside the repository") from exc
    source = _safe_regular_file(root, relative_source)
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load Runtime 0.2 request: {exc}") from exc
    request = _mapping(raw, "request")
    _exact_keys(request, ROOT_KEYS, "request")
    if request["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Runtime 0.2 loader requires schema_version '0.2'")
    if _identifier(request["experiment_id"], "experiment_id") != EXPERIMENT_ID:
        raise ValueError("Runtime 0.2 compiler fixture experiment is not registered")
    if _identifier(request["backend_id"], "backend_id") != BACKEND_ID:
        raise ValueError("Runtime 0.2 compiler fixture backend is not registered")
    if _mapping(request["parameters"], "parameters") != {}:
        raise ValueError("Runtime 0.2 compiler fixture parameters must be empty")

    device = _resolve_relative(request["device_snapshot"], root, "device_snapshot")
    calibration = _resolve_relative(request["calibration_snapshot"], root, "calibration_snapshot")
    _validate_frozen_snapshots(root, device, calibration)
    authority, authority_sha = load_compiler_fixture_authority(root)
    program = _validate_program(request["program"], authority)
    axes, repetitions = _scan_v02(request["scan"])
    execution = _execution(request["execution"])
    if execution.fail_fast is not True:
        raise ValueError("Runtime 0.2 compiler fixture requires fail_fast=true")
    count = repetitions
    for axis in axes:
        count *= len(axis.values)
    if count > execution.max_points or count > 10_000:
        raise ValueError("expanded point count exceeds configured limit")
    publication = _mapping(request["publication"], "publication")
    _exact_keys(publication, PUBLICATION_KEYS, "publication")
    if publication["allow_existing_target"] is not False:
        raise ValueError("publication.allow_existing_target must be false")
    return ExperimentRequestV02(
        source, root, SCHEMA_VERSION, EXPERIMENT_ID, BACKEND_ID, device, calibration,
        frozen_mapping({}), frozen_mapping(program), axes, repetitions, execution, False,
        frozen_mapping(authority), authority_sha,
    )


def load_compiler_fixture_authority(repository_root: str | Path) -> tuple[dict[str, Any], str]:
    root = Path(repository_root).resolve()
    approval_path = _safe_regular_file(root, APPROVAL_PATH)
    approval_raw = approval_path.read_bytes()
    try:
        approval = json.loads(approval_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compiler fixture approval is invalid JSON") from exc
    approval_keys = {
        "schema_version", "artifact_type", "artifact_version", "authority_id", "authority_raw_sha256",
        "design_path", "design_sha256", "reviewer_role", "decision", "blocking_findings",
    }
    if not isinstance(approval, dict) or approval_raw != canonical_json_bytes(approval) or set(approval) != approval_keys:
        raise ValueError("compiler fixture approval schema is invalid")
    if _raw_sha(approval_path) != APPROVAL_SHA256:
        raise ValueError("compiler fixture approval raw hash is invalid")
    if approval != {
        "schema_version": "0.1",
        "artifact_type": "stage_06_qcis_compiler_fixture_approval",
        "artifact_version": "1",
        "authority_id": AUTHORITY_ID,
        "authority_raw_sha256": "E55E955E54204DDB447D98830ADB76D161B3CA05965F7FF9631FB9A3238D864C",
        "design_path": "docs/designs/06_1_experiment_runtime_v02_design.md",
        "design_sha256": "D377E9DD8B92C62FA6D22913168E0FA65FC2176F7481942B323F8B22D6873E3B",
        "reviewer_role": "independent_test",
        "decision": "APPROVE",
        "blocking_findings": [],
    }:
        raise ValueError("compiler fixture approval decision or bindings are invalid")
    path = _safe_regular_file(root, AUTHORITY_PATH)
    raw = path.read_bytes()
    try:
        authority = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("compiler fixture authority is invalid JSON") from exc
    if not isinstance(authority, dict) or raw != canonical_json_bytes(authority):
        raise ValueError("compiler fixture authority must be canonical JSON")
    expected_keys = {
        "schema_version", "artifact_type", "artifact_version", "authority_id", "request_config_path",
        "request_config_sha256", "claim_envelope", "source_bindings", "idle_flux_phi0", "qcis_authorities",
    }
    if set(authority) != expected_keys:
        raise ValueError("compiler fixture authority keys are invalid")
    if authority["schema_version"] != "0.1" or authority["artifact_type"] != "stage_06_qcis_compiler_fixture_authority" or authority["artifact_version"] != "1":
        raise ValueError("compiler fixture authority identity is invalid")
    authority_id = _sha_payload({key: value for key, value in authority.items() if key != "authority_id"})
    if authority.get("authority_id") != authority_id or authority_id != AUTHORITY_ID:
        raise ValueError("compiler fixture authority ID is invalid")
    if _raw_sha(path) != approval["authority_raw_sha256"]:
        raise ValueError("compiler fixture authority raw hash differs from approval")
    if authority.get("claim_envelope") != CLAIM_ENVELOPE:
        raise ValueError("compiler fixture claim envelope is invalid")
    request_path = authority.get("request_config_path")
    if request_path != REQUEST_PATH or _raw_sha(_safe_regular_file(root, request_path)) != authority.get("request_config_sha256"):
        raise ValueError("compiler fixture request binding is invalid")
    source_bindings = authority.get("source_bindings")
    expected_source_paths = {
        "docs/designs/06_1_experiment_runtime_v02_design.md",
        "docs/designs/07_qcis_compiler_design.md",
        "src/sqvm/qcis/compiler.py",
        "src/sqvm/qcis/models.py",
        "src/sqvm/qcis/parser.py",
    }
    if not isinstance(source_bindings, dict) or set(source_bindings) != expected_source_paths:
        raise ValueError("compiler fixture source bindings are invalid")
    for relative, expected in source_bindings.items():
        if not isinstance(relative, str) or not _SHA256.fullmatch(str(expected)):
            raise ValueError("compiler fixture source binding is malformed")
        if _raw_sha(_safe_regular_file(root, relative)) != expected:
            raise ValueError(f"compiler fixture source binding drifted: {relative}")
    qcis = _mapping(authority.get("qcis_authorities"), "qcis authorities")
    expected_qcis_keys = {
        "instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry",
        "clock", "compiler", "templates", "expected_sha256",
    }
    if set(qcis) != expected_qcis_keys:
        raise ValueError("compiler fixture QCIS authority keys are invalid")
    hashes = _mapping(qcis["expected_sha256"], "expected_sha256")
    components = {"instruction_profile", "qagent_registry", "gate_configuration", "waveform_registry", "clock", "compiler"}
    if set(hashes) != components:
        raise ValueError("compiler fixture authority hash set is invalid")
    for name in components:
        if hashes[name] != qcis_sha256_json(qcis[name]):
            raise ValueError(f"compiler fixture QCIS authority hash is invalid: {name}")
    for target in ("Q1", "Q2"):
        reference = qcis["qagent_registry"][target]["reference_frequency_authority"]
        setting_hash = reference.get("setting_hash")
        if setting_hash != qcis_sha256_json({key: value for key, value in reference.items() if key != "setting_hash"}):
            raise ValueError(f"compiler fixture {target} frequency authority hash is invalid")
    idle = authority.get("idle_flux_phi0")
    if idle != {"q1": 0.1, "q2": 0.0, "c": 0.27}:
        raise ValueError("compiler fixture idle flux authority is invalid")
    return authority, _raw_sha(path)


def canonical_request_payload_v02(request: ExperimentRequestV02) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": request.experiment_id,
        "backend_id": request.backend_id,
        "device_snapshot": request.device_snapshot.relative_to(request.repository_root).as_posix(),
        "calibration_snapshot": request.calibration_snapshot.relative_to(request.repository_root).as_posix(),
        "parameters": {},
        "program": _plain(request.program),
        "program_authority_sha256": request.authority_sha256,
        "scan": {
            "axes": [{"name": axis.name, "unit": axis.unit, "values": list(axis.values)} for axis in request.axes],
            "repetitions": request.repetitions,
        },
        "execution": {
            "seed": request.execution.seed,
            "max_points": request.execution.max_points,
            "point_budget_seconds": request.execution.point_budget_seconds,
            "run_budget_seconds": request.execution.run_budget_seconds,
            "fail_fast": True,
        },
        "publication": {"allow_existing_target": False},
        "claim_envelope": dict(CLAIM_ENVELOPE),
    }


def expand_scan_v02(request: ExperimentRequestV02) -> tuple[ScanPointV02, ...]:
    request_sha = _sha_payload(canonical_request_payload_v02(request))
    points: list[ScanPointV02] = []
    combinations = itertools.product(*(axis.values for axis in request.axes))
    combinations = tuple(combinations)
    for repetition in range(request.repetitions):
        for values in combinations:
            coordinates = tuple(
                (axis.name, value, axis.unit)
                for axis, value in zip(request.axes, values, strict=True)
            )
            base = {
                "schema_version": SCHEMA_VERSION,
                "experiment_id": request.experiment_id,
                "request_sha256": request_sha,
                "program_authority_sha256": request.authority_sha256,
                "point_index": len(points),
                "repetition": repetition,
                "coordinates": [
                    {"axis": name, "value": value, "unit": unit}
                    for name, value, unit in coordinates
                ],
            }
            seed = int.from_bytes(
                hashlib.sha256(str(request.execution.seed).encode("ascii") + b"\0" + canonical_json_bytes(base)).digest()[:8],
                "big",
            )
            point_id = _sha_payload({**base, "seed": seed})
            points.append(ScanPointV02(len(points), repetition, coordinates, seed, point_id, request_sha, request.authority_sha256))
    return tuple(points)


def point_table_payload_v02(request: ExperimentRequestV02) -> dict[str, Any]:
    request_sha = _sha_payload(canonical_request_payload_v02(request))
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": request.experiment_id,
        "request_sha256": request_sha,
        "program_authority_sha256": request.authority_sha256,
        "points": [point.payload() for point in expand_scan_v02(request)],
    }


def compile_point_v02(request: ExperimentRequestV02, point: ScanPointV02) -> CompiledPointV02:
    canonical_points = expand_scan_v02(request)
    if not 0 <= point.point_index < len(canonical_points) or canonical_points[point.point_index] != point:
        raise ValueError("Runtime 0.2 point is not canonical for the admitted request")
    scan_values = {
        name: {"value": value, "unit": unit}
        for name, value, unit in point.coordinates
    }
    qcis_authorities = _plain(request.authority["qcis_authorities"])
    compilation = compile_qcis(
        _plain(request.program), qcis_authorities,
        idle_flux=_plain(request.authority["idle_flux_phi0"]),
        scan_values=scan_values,
    )
    verify_compilation(compilation)
    verify_drive_event_inventory(compilation)
    arrays = {
        "q1_xy": compilation.q1_xy,
        "q2_xy": compilation.q2_xy,
        "q1_flux": compilation.q1_flux,
        "q2_flux": compilation.q2_flux,
        "c_flux": compilation.c_flux,
    }
    inventory_rows = {}
    total_bytes = 0
    for name, array in arrays.items():
        raw = array.tobytes(order="C")
        total_bytes += len(raw)
        inventory_rows[name] = {
            "dtype": array.dtype.str,
            "shape": list(array.shape),
            "byte_length": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
        }
    inventory = {
        "schema_version": SCHEMA_VERSION,
        "point_id": point.point_id,
        "arrays": inventory_rows,
        "drive_event_inventory_sha256": compilation.plan.drive_event_inventory_sha256,
        "coefficient_inventory_sha256": hashlib.sha256(compilation.coefficient_inventory_bytes).hexdigest().upper(),
    }
    diagnostics = {
        "concrete_qcis_sha256": compilation.concrete_source_sha256,
        "ast_sha256": hashlib.sha256(compilation.ast_bytes).hexdigest().upper(),
        "trace_sha256": hashlib.sha256(compilation.trace_bytes).hexdigest().upper(),
        "logical_inventory_sha256": _sha_payload(inventory),
        "program_authority_sha256": request.authority_sha256,
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "point_id": point.point_id,
        "values": {
            "instruction_count": len(compilation.plan.program.instructions),
            "logical_sample_count": len(compilation.q1_xy),
            "trace_byte_count": len(compilation.trace_bytes),
            "logical_array_bytes": total_bytes,
        },
        "diagnostics": diagnostics,
    }
    validate_point_result_v02(point, result)
    return CompiledPointV02(
        point,
        compilation.concrete_source.encode("ascii"),
        compilation.ast_bytes,
        compilation.trace_bytes,
        frozen_mapping(inventory),
        frozen_mapping(result),
    )


def validate_point_result_v02(point: ScanPointV02, result: Mapping[str, Any]) -> None:
    if set(result) != {"schema_version", "point_id", "values", "diagnostics"}:
        raise ValueError("Runtime 0.2 point result keys are invalid")
    if result.get("schema_version") != SCHEMA_VERSION or result.get("point_id") != point.point_id:
        raise ValueError("Runtime 0.2 point result identity is invalid")
    values = _mapping(result.get("values"), "point result values")
    if tuple(values) != tuple(RESULT_SCHEMA):
        raise ValueError("Runtime 0.2 point result variable order is invalid")
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**64 - 1:
            raise ValueError(f"Runtime 0.2 point result {name} is not uint64")
    diagnostics = _mapping(result.get("diagnostics"), "point result diagnostics")
    expected = {
        "concrete_qcis_sha256", "ast_sha256", "trace_sha256", "logical_inventory_sha256",
        "program_authority_sha256",
    }
    if set(diagnostics) != expected or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in diagnostics.values()):
        raise ValueError("Runtime 0.2 point result diagnostics are invalid")
    if diagnostics["program_authority_sha256"] != point.program_authority_sha256:
        raise ValueError("Runtime 0.2 point result authority binding is invalid")


def write_dataset_v02(
    data_dir: str | Path,
    results: Iterable[Mapping[str, Any]],
    point_table_sha256: str,
) -> dict[str, Any]:
    target = Path(data_dir)
    rows = tuple(results)
    if target.exists() or not rows:
        raise ValueError("Runtime 0.2 dataset target exists or result set is empty")
    target.mkdir(parents=False)
    variables: dict[str, Any] = {}
    for name, spec in RESULT_SCHEMA.items():
        raw = encode_numeric_values((row["values"][name] for row in rows), spec["dtype"])
        _write_fsynced(target / f"{name}.bin", raw)
        variables[name] = {
            "dimensions": ["point"],
            "dtype": spec["dtype"],
            "shape": [len(rows)],
            "order": "C",
            "unit": spec["unit"],
            "semantic_role": spec["semantic_role"],
            "byte_length": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
        }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "point_table_sha256": point_table_sha256,
        "dimensions": {"point": len(rows)},
        "variable_order": list(RESULT_SCHEMA),
        "variables": variables,
    }
    _write_fsynced(target / "dataset.json", canonical_json_bytes(payload))
    validate_dataset_v02(target, len(rows), point_table_sha256)
    return payload


def validate_dataset_v02(data_dir: str | Path, point_count: int, point_table_sha256: str) -> dict[str, Any]:
    target = Path(data_dir)
    expected_files = {"dataset.json", *(f"{name}.bin" for name in RESULT_SCHEMA)}
    if not target.is_dir() or target.is_symlink() or {row.name for row in target.iterdir()} != expected_files:
        raise ValueError("Runtime 0.2 dataset file set is invalid")
    raw_manifest = (target / "dataset.json").read_bytes()
    try:
        payload = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Runtime 0.2 dataset manifest is invalid") from exc
    if raw_manifest != canonical_json_bytes(payload):
        raise ValueError("Runtime 0.2 dataset manifest is not canonical")
    if set(payload) != {"schema_version", "claim_envelope", "point_table_sha256", "dimensions", "variable_order", "variables"}:
        raise ValueError("Runtime 0.2 dataset schema is invalid")
    if payload["schema_version"] != SCHEMA_VERSION or payload["claim_envelope"] != CLAIM_ENVELOPE:
        raise ValueError("Runtime 0.2 dataset version or claim is invalid")
    if payload["point_table_sha256"] != point_table_sha256 or payload["dimensions"] != {"point": point_count}:
        raise ValueError("Runtime 0.2 dataset point binding is invalid")
    if payload["variable_order"] != list(RESULT_SCHEMA) or set(payload["variables"]) != set(RESULT_SCHEMA):
        raise ValueError("Runtime 0.2 dataset variable order is invalid")
    for name, spec in RESULT_SCHEMA.items():
        variable = payload["variables"][name]
        if set(variable) != {"dimensions", "dtype", "shape", "order", "unit", "semantic_role", "byte_length", "raw_sha256"}:
            raise ValueError(f"Runtime 0.2 dataset variable schema is invalid: {name}")
        raw = (target / f"{name}.bin").read_bytes()
        fixed = {
            "dimensions": ["point"], "dtype": spec["dtype"], "shape": [point_count], "order": "C",
            "unit": spec["unit"], "semantic_role": spec["semantic_role"], "byte_length": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest().upper(),
        }
        if variable != fixed or len(raw) != point_count * (16 if spec["dtype"] == "<c16" else 8):
            raise ValueError(f"Runtime 0.2 dataset variable metadata is invalid: {name}")
        decode_numeric_values(raw, spec["dtype"])
    return payload


def _validate_program(value: Any, authority: Mapping[str, Any]) -> dict[str, Any]:
    program = dict(_mapping(value, "program"))
    _exact_keys(program, PROGRAM_KEYS, "program")
    if program["program_schema_version"] != "0.3" or program["instruction_set_id"] != "qcis_stage7_calibration_v3":
        raise ValueError("Runtime 0.2 requires QCIS program/profile 0.3")
    if program["source_format"] != "qcis_template" or program["template_id"] != EXPERIMENT_ID:
        raise ValueError("Runtime 0.2 program template identity is invalid")
    template = authority["qcis_authorities"]["templates"][EXPERIMENT_ID]
    if program["source"] != template["source"] or program["template_sha256"] != hashlib.sha256(template["source"].encode("utf-8")).hexdigest().upper():
        raise ValueError("Runtime 0.2 program source differs from authority")
    bindings = _mapping(program["bindings"], "program.bindings")
    expected_binding = {"scan_ref": "drive_frequency", "unit": "GHz"}
    if bindings != {"drive_frequency": expected_binding}:
        raise ValueError("Runtime 0.2 compiler fixture binding is invalid")
    return program


def _scan_v02(value: Any) -> tuple[tuple[ScanAxis, ...], int]:
    scan = _mapping(value, "scan")
    _exact_keys(scan, {"axes", "repetitions"}, "scan")
    repetitions = scan["repetitions"]
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or not 1 <= repetitions <= 1000:
        raise ValueError("scan.repetitions is invalid")
    raw_axes = scan["axes"]
    if not isinstance(raw_axes, list) or len(raw_axes) != 1:
        raise ValueError("Runtime 0.2 compiler fixture requires one scan axis")
    axis = _mapping(raw_axes[0], "scan.axes[0]")
    _exact_keys(axis, AXIS_KEYS, "scan.axes[0]")
    if axis["name"] != "drive_frequency" or axis["unit"] != "GHz":
        raise ValueError("Runtime 0.2 compiler fixture scan axis is invalid")
    values = axis["values"]
    if not isinstance(values, list) or not values:
        raise ValueError("scan axis values must be nonempty")
    parsed = []
    for value_item in values:
        if isinstance(value_item, bool) or not isinstance(value_item, int | float) or not math.isfinite(float(value_item)):
            raise ValueError("scan axis values must be finite binary64")
        parsed.append(float(value_item))
    return (ScanAxis("drive_frequency", "GHz", tuple(parsed)),), repetitions


def _safe_regular_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("authority path is invalid")
    parts = Path(relative).parts
    if Path(relative).is_absolute() or ".." in parts:
        raise ValueError("authority path escapes repository")
    _validate_case(root, tuple(parts), "authority path")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"authority path contains a link: {relative}")
        info = current.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError(f"authority path contains a reparse point: {relative}")
    resolved = current.resolve()
    resolved.relative_to(root)
    if not resolved.is_file():
        raise ValueError(f"authority file is missing: {relative}")
    return resolved


def safe_directory_no_follow(path: str | Path, repository_root: str | Path, label: str) -> Path:
    """Resolve a repository-contained directory only after checking every lexical node."""

    root = Path(repository_root).resolve()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the repository") from exc
    _validate_case(root, tuple(relative.parts), label)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} contains a link")
        info = current.lstat()
        attributes = getattr(info, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError(f"{label} contains a reparse point")
    if not current.is_dir():
        raise ValueError(f"{label} is not a directory")
    resolved = current.resolve()
    resolved.relative_to(root)
    return resolved


def resolve_output_root_no_follow(value: str | Path, repository_root: str | Path) -> Path:
    """Resolve a repository-relative output root while rejecting existing linked components."""

    root = Path(repository_root).resolve()
    text = str(value).replace("\\", "/")
    posix = PurePosixPath(text)
    windows = PureWindowsPath(text)
    if not text or posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
        raise ValueError("output_root must be repository-relative")
    current = root
    for part in posix.parts:
        candidate = current / part
        if candidate.exists() or candidate.is_symlink():
            matches = [entry.name for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
            if len(matches) != 1 or matches[0] != part:
                raise ValueError("output_root has a case-collision ambiguity")
            info = candidate.lstat()
            if candidate.is_symlink() or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("output_root contains a link or reparse point")
            if not candidate.is_dir():
                raise ValueError("output_root parent is not a directory")
        current = candidate
    resolved = current.resolve()
    resolved.relative_to(root)
    return resolved


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _sha_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _raw_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _write_fsynced(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
