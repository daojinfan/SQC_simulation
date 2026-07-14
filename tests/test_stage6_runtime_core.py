from __future__ import annotations

import hashlib
from pathlib import Path
import shutil

import pytest
import yaml

from sqvm.runtime import (
    BACKEND_ID,
    EXPERIMENT_ID,
    RESPONSE_BYTES,
    RESPONSE_SHA256,
    CancellationToken,
    CooperativeBudget,
    ExperimentDefinition,
    ExperimentRegistry,
    ParameterSpec,
    RunState,
    allowed_transitions,
    expand_scan,
    get_builtin_experiment_registry,
    get_builtin_backend_registry,
    load_experiment_request,
    transition,
)
from sqvm.runtime.models import BackendCommand, PreparedExperiment, frozen_mapping


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/experiments/platform_deterministic_smoke_v1.yaml"


def _config(tmp_path: Path) -> tuple[Path, dict]:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    path = tmp_path / "configs" / "experiments" / "request.yaml"
    path.parent.mkdir(parents=True)
    device = tmp_path / "configs" / "devices" / "2q1c2r.yaml"
    device.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "configs" / "devices" / "2q1c2r.yaml", device)
    calibration = tmp_path / "configs" / "calibration" / "platform_uncalibrated_v1.json"
    calibration.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json", calibration)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path, raw


def test_frozen_request_admits_and_program_is_null():
    request = load_experiment_request(CONFIG, ROOT)
    assert request.experiment_id == EXPERIMENT_ID
    assert request.backend_id == BACKEND_ID
    assert request.program is None
    assert request.parameters == {}


@pytest.mark.parametrize("field,value", [("program", {}), ("backend_id", "stage5"), ("unknown", True)])
def test_admission_rejects_program_unknown_and_unregistered(tmp_path, field, value):
    path, raw = _config(tmp_path)
    raw[field] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError):
        load_experiment_request(path, tmp_path)


def test_admission_rejects_duplicate_nested_key_and_path_escape(tmp_path):
    path, raw = _config(tmp_path)
    path.write_text(CONFIG.read_text(encoding="utf-8") + "\nscan:\n  axes: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_experiment_request(path, tmp_path)
    raw["device_snapshot"] = "../outside.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    with pytest.raises(ValueError, match="stay inside"):
        load_experiment_request(path, ROOT)


def test_scan_is_deterministic_with_last_axis_fastest():
    request = load_experiment_request(CONFIG, ROOT)
    first = expand_scan(request)
    second = expand_scan(request)
    assert first == second
    assert [point.coordinate_map() for point in first] == [
        {"x": 0.0, "y": -1.0}, {"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 1.0},
        {"x": 0.5, "y": -1.0}, {"x": 0.5, "y": 0.0}, {"x": 0.5, "y": 1.0},
    ]
    assert len({point.seed for point in first}) == 6
    assert all(len(point.point_id) == 64 for point in first)


def test_fake_backend_has_frozen_response_bytes_and_hash():
    request = load_experiment_request(CONFIG, ROOT)
    backend = get_builtin_backend_registry().resolve(BACKEND_ID)
    experiment = get_builtin_experiment_registry().resolve(EXPERIMENT_ID)
    prepared = experiment.prepare(request, {})
    values = [backend.execute_point(experiment.build_command(prepared, point), None).values["response"] for point in expand_scan(request)]
    assert values == [-2.0, 0.0, 2.0, -1.5, 0.5, 2.5]
    import struct

    actual = struct.pack("<6d", *values)
    assert actual == RESPONSE_BYTES
    assert hashlib.sha256(actual).hexdigest().upper() == RESPONSE_SHA256
    schema = experiment.dataset_schema
    with pytest.raises(TypeError):
        schema["response"]["dtype"] = ">f8"


def _synthetic_definition() -> ExperimentDefinition:
    def prepare(request, snapshots):
        return PreparedExperiment("test_extension_v1", request, frozen_mapping(snapshots))

    def build_command(prepared, point):
        return BackendCommand("test_extension_v1", point.point_id, point.coordinate_map())

    return ExperimentDefinition(
        "test_extension_v1",
        (ParameterSpec("z", "dimensionless", True, "binary64"),),
        "null_only_v1",
        frozenset(),
        frozen_mapping({"synthetic": "binary64"}),
        frozen_mapping({"synthetic": {"dimensions": ("point",)}}),
        prepare,
        build_command,
    )


def test_second_definition_admits_and_expands_without_backend(tmp_path):
    path, raw = _config(tmp_path)
    raw["experiment_id"] = "test_extension_v1"
    raw["scan"]["axes"] = [{"name": "z", "unit": "dimensionless", "values": [1.0, 2.0]}]
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    registry = ExperimentRegistry((_synthetic_definition(),))
    request = load_experiment_request(path, tmp_path, experiment_registry=registry, backend_registry=get_builtin_backend_registry())
    assert [point.coordinate_map() for point in expand_scan(request)] == [{"z": 1.0}, {"z": 2.0}]


def test_lifecycle_cancellation_and_cooperative_budget():
    assert RunState.PREPARED in allowed_transitions(RunState.RESERVED)
    assert transition(RunState.RUNNING, RunState.FINALIZING) is RunState.FINALIZING
    with pytest.raises(ValueError):
        transition(RunState.COMPLETED, RunState.RUNNING)
    token = CancellationToken()
    token.request()
    with pytest.raises(RuntimeError, match="cancelled"):
        token.raise_if_requested()
    budget = CooperativeBudget(1.0, 1.0)
    started = budget.before_point()
    budget.after_point(started)
