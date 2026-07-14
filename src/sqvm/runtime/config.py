"""Strict, fail-closed admission for Stage 6 platform-only requests."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256
from sqvm.runtime.models import ExecutionSettings, ExperimentRequest, ScanAxis, frozen_mapping
from sqvm.runtime.registry import BackendRegistry, ExperimentRegistry, get_builtin_backend_registry, get_builtin_experiment_registry


ROOT_KEYS = {
    "schema_version", "experiment_id", "backend_id", "device_snapshot", "calibration_snapshot",
    "parameters", "program", "scan", "execution", "publication",
}
SCAN_KEYS = {"axes", "repetitions"}
AXIS_KEYS = {"name", "unit", "values"}
EXECUTION_KEYS = {"seed", "max_points", "point_budget_seconds", "run_budget_seconds", "fail_fast"}
PUBLICATION_KEYS = {"allow_existing_target"}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
DEVICE_PATH = "configs/devices/2q1c2r.yaml"
DEVICE_SHA256 = "CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F"
CALIBRATION_PATH = "configs/calibration/platform_uncalibrated_v1.json"
CALIBRATION_SHA256 = "E3DD8BB9508AEE3984DAC6D01729466DB0023769DF437CF63A7C1BD15FEDD2F4"
CALIBRATION_PAYLOAD = {
    "accepted": False,
    "device_snapshot": DEVICE_PATH,
    "device_snapshot_sha256": DEVICE_SHA256,
    "schema_version": "0.1",
    "state_id": "uncalibrated",
    "status": "uninitialized",
    "values": {},
}


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys at every nesting level."""


def _construct_unique_mapping(loader: _UniqueKeySafeLoader, node: yaml.nodes.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark, "mapping key is not hashable", key_node.start_mark) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError("while constructing a mapping", node.start_mark, f"duplicate key {key!r}", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def load_experiment_request(
    path: str | Path,
    repository_root: str | Path | None = None,
    *,
    experiment_registry: ExperimentRegistry | None = None,
    backend_registry: BackendRegistry | None = None,
) -> ExperimentRequest:
    """Load a fully admitted Stage 6 request without producing any output."""

    source = Path(path).resolve()
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(source)
    _inside_root(source, root, "request path")
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot load Stage 6 request: {exc}") from exc
    root_mapping = _mapping(raw, "request")
    _exact_keys(root_mapping, ROOT_KEYS, "request")
    if root_mapping["schema_version"] != "0.1":
        raise ValueError("schema_version must be '0.1'")
    experiment_id = _identifier(root_mapping["experiment_id"], "experiment_id")
    backend_id = _identifier(root_mapping["backend_id"], "backend_id")
    if root_mapping["program"] is not None:
        raise ValueError("program must be null in Stage 6 schema 0.1")

    experiments = experiment_registry or get_builtin_experiment_registry()
    backends = backend_registry or get_builtin_backend_registry()
    definition = experiments.resolve(experiment_id)
    backends.resolve(backend_id)
    device = _resolve_relative(root_mapping["device_snapshot"], root, "device_snapshot")
    calibration = _resolve_relative(root_mapping["calibration_snapshot"], root, "calibration_snapshot")
    _validate_frozen_snapshots(root, device, calibration)
    parameters = _parameters(root_mapping["parameters"], definition)
    axes, repetitions = _scan(root_mapping["scan"], definition)
    execution = _execution(root_mapping["execution"])
    count = repetitions
    for axis in axes:
        count *= len(axis.values)
    if count > execution.max_points or count > 10_000:
        raise ValueError("expanded point count exceeds configured limit")
    publication = _mapping(root_mapping["publication"], "publication")
    _exact_keys(publication, PUBLICATION_KEYS, "publication")
    if publication["allow_existing_target"] is not False:
        raise ValueError("publication.allow_existing_target must be false")
    return ExperimentRequest(
        source,
        root,
        "0.1",
        experiment_id,
        backend_id,
        device,
        calibration,
        frozen_mapping(parameters),
        None,
        axes,
        repetitions,
        execution,
        False,
    )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} keys are invalid")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase ASCII identifier")
    return value


def _resolve_relative(value: Any, root: Path, label: str) -> Path:
    if not isinstance(value, str) or not value or not value.isascii():
        raise ValueError(f"{label} must be a non-empty repository-relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
        raise ValueError(f"{label} must stay inside the repository")
    parts = tuple(part for part in value.replace("\\", "/").split("/") if part)
    if not parts:
        raise ValueError(f"{label} must be a non-empty repository-relative path")
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve()
    _inside_root(resolved, root, label)
    if not resolved.is_file():
        raise ValueError(f"{label} must name an existing file")
    _validate_case(root, parts, label)
    return resolved


def _inside_root(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the repository") from exc


def _validate_case(root: Path, parts: tuple[str, ...], label: str) -> None:
    current = root
    for part in parts:
        matches = [entry.name for entry in current.iterdir() if entry.name.casefold() == part.casefold()]
        if len(matches) != 1 or matches[0] != part:
            raise ValueError(f"{label} has a case-collision ambiguity")
        current /= part


def _validate_frozen_snapshots(root: Path, device: Path, calibration: Path) -> None:
    if device.relative_to(root).as_posix() != DEVICE_PATH or raw_file_sha256(device) != DEVICE_SHA256:
        raise ValueError("device_snapshot differs from the frozen MVP device")
    if calibration.relative_to(root).as_posix() != CALIBRATION_PATH or raw_file_sha256(calibration) != CALIBRATION_SHA256:
        raise ValueError("calibration_snapshot differs from the frozen MVP calibration")
    try:
        calibration_payload = json.loads(calibration.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load calibration snapshot: {exc}") from exc
    if calibration_payload != CALIBRATION_PAYLOAD or calibration.read_bytes() != canonical_json_bytes(CALIBRATION_PAYLOAD):
        raise ValueError("calibration_snapshot canonical payload is invalid")


def _parameters(value: Any, definition: Any) -> dict[str, Any]:
    parameters = _mapping(value, "parameters")
    specs = definition.parameter_map()
    expected = {name for name, spec in specs.items() if not spec.scannable}
    if set(parameters) != expected:
        raise ValueError("parameters do not match the selected experiment")
    parsed: dict[str, Any] = {}
    for name, item in parameters.items():
        parsed[name] = _finite_value(item, f"parameters.{name}", specs[name].value_kind)
    return parsed


def _scan(value: Any, definition: Any) -> tuple[tuple[ScanAxis, ...], int]:
    scan = _mapping(value, "scan")
    _exact_keys(scan, SCAN_KEYS, "scan")
    repetitions = scan["repetitions"]
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or not 1 <= repetitions <= 1_000:
        raise ValueError("scan.repetitions must be an integer from 1 through 1000")
    raw_axes = scan["axes"]
    if not isinstance(raw_axes, list) or not raw_axes:
        raise ValueError("scan.axes must be a non-empty list")
    specs = definition.parameter_map()
    axes: list[ScanAxis] = []
    names: set[str] = set()
    for index, raw_axis in enumerate(raw_axes):
        axis = _mapping(raw_axis, f"scan.axes[{index}]")
        _exact_keys(axis, AXIS_KEYS, f"scan.axes[{index}]")
        name = _identifier(axis["name"], f"scan.axes[{index}].name")
        if name in names:
            raise ValueError("scan axis names must be unique")
        spec = specs.get(name)
        if spec is None or not spec.scannable:
            raise ValueError("scan axis must name a scannable parameter")
        unit = axis["unit"]
        if not isinstance(unit, str) or not unit.isascii() or not unit or unit != spec.unit:
            raise ValueError("scan axis unit does not match parameter unit")
        raw_values = axis["values"]
        if not isinstance(raw_values, list) or not raw_values:
            raise ValueError("scan axis values must be a non-empty list")
        values = tuple(_finite_value(item, f"scan.axes[{index}].values", spec.value_kind) for item in raw_values)
        if any(not isinstance(item, float) for item in values):
            raise ValueError("scan axis values must be binary64")
        names.add(name)
        axes.append(ScanAxis(name, unit, values))
    if names != {name for name, spec in specs.items() if spec.scannable}:
        raise ValueError("scan axes must provide every scannable parameter")
    return tuple(axes), repetitions


def _finite_value(value: Any, label: str, value_kind: str) -> Any:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be a finite scalar")
    if value_kind == "binary64" and not isinstance(value, float):
        raise ValueError(f"{label} must be binary64")
    return float(value) if value_kind == "binary64" else value


def _execution(value: Any) -> ExecutionSettings:
    execution = _mapping(value, "execution")
    _exact_keys(execution, EXECUTION_KEYS, "execution")
    seed = execution["seed"]
    maximum = execution["max_points"]
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError("execution.seed must be an unsigned 64-bit integer")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 10_000:
        raise ValueError("execution.max_points must be an integer from 1 through 10000")
    point_budget = _positive_float(execution["point_budget_seconds"], "execution.point_budget_seconds")
    run_budget = _positive_float(execution["run_budget_seconds"], "execution.run_budget_seconds")
    if run_budget < point_budget:
        raise ValueError("execution.run_budget_seconds must be at least point_budget_seconds")
    if execution["fail_fast"] is not True:
        raise ValueError("execution.fail_fast must be true")
    return ExecutionSettings(seed, maximum, point_budget, run_budget, True)


def _positive_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)) or float(value) <= 0.0:
        raise ValueError(f"{label} must be a finite positive number")
    return float(value)
