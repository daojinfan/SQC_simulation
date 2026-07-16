"""Immutable request-schema adapter registry for Stage 6 runtime lanes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol

from sqvm.runtime.models import ExperimentRequest, RunArtifactSet, RunVerificationReport
from sqvm.runtime_v02.core import ExperimentRequestV02, ScanPointV02


class RuntimeSchemaAdapter(Protocol):
    schema_version: str

    def load_request(self, path: str | Path, repository_root: str | Path | None = None) -> ExperimentRequest | ExperimentRequestV02: ...

    def canonical_request(self, request: ExperimentRequest | ExperimentRequestV02) -> Mapping[str, Any]: ...

    def expand_points(self, request: ExperimentRequest | ExperimentRequestV02) -> tuple[Any, ...]: ...

    def validate_point_result(self, point: Any, result: Mapping[str, Any]) -> None: ...

    def write_dataset(self, results: Iterable[Mapping[str, Any]], point_table_sha256: str, target: str | Path) -> Mapping[str, Any]: ...

    def run(self, request_path: str | Path, output_root: str | Path, repository_root: str | Path | None = None) -> RunArtifactSet: ...

    def verify_run(self, run_dir: str | Path, repository_root: str | Path | None = None) -> RunVerificationReport: ...


@dataclass(frozen=True, slots=True)
class RuntimeSchemaRegistry:
    _adapters: Mapping[str, RuntimeSchemaAdapter]

    def __init__(self, adapters: tuple[RuntimeSchemaAdapter, ...]):
        by_version = {adapter.schema_version: adapter for adapter in adapters}
        if len(by_version) != len(adapters):
            raise ValueError("duplicate runtime schema adapter")
        object.__setattr__(self, "_adapters", MappingProxyType(by_version))

    def resolve(self, schema_version: str) -> RuntimeSchemaAdapter:
        try:
            return self._adapters[schema_version]
        except KeyError as exc:
            raise ValueError(f"unsupported runtime schema_version: {schema_version}") from exc

    def versions(self) -> tuple[str, ...]:
        return tuple(self._adapters)


@dataclass(frozen=True, slots=True)
class RuntimeV01Adapter:
    schema_version: str = "0.1"

    def load_request(self, path, repository_root=None):
        from sqvm.runtime.config import load_experiment_request

        request = load_experiment_request(path, repository_root)
        if request.schema_version != self.schema_version:
            raise ValueError("Runtime 0.1 adapter received a cross-version request")
        return request

    def canonical_request(self, request):
        if not isinstance(request, ExperimentRequest) or request.schema_version != self.schema_version:
            raise ValueError("Runtime 0.1 request type is invalid")
        from sqvm.runtime.runner import _request_payload

        return _request_payload(request)

    def expand_points(self, request):
        from sqvm.runtime.scan import expand_scan

        return expand_scan(request)

    def validate_point_result(self, point, result):
        if set(result) != {"response"}:
            raise ValueError("Runtime 0.1 point result keys are invalid")
        from sqvm.runtime.runner import _validate_point_result

        _validate_point_result(result)

    def write_dataset(self, results, point_table_sha256, target):
        from sqvm.runtime.dataset import write_response_dataset

        return write_response_dataset(target, (row["response"] for row in results), point_table_sha256)

    def run(self, request_path, output_root, repository_root=None):
        from sqvm.runtime.runner import run_experiment

        return run_experiment(request_path, output_root, repository_root)

    def verify_run(self, run_dir, repository_root=None):
        from sqvm.runtime.verify import verify_experiment_run

        return verify_experiment_run(run_dir, repository_root)


@dataclass(frozen=True, slots=True)
class RuntimeV02Adapter:
    schema_version: str = "0.2"

    def load_request(self, path, repository_root=None):
        from sqvm.runtime_v02.core import load_experiment_request_v02

        return load_experiment_request_v02(path, repository_root)

    def canonical_request(self, request):
        if not isinstance(request, ExperimentRequestV02):
            raise ValueError("Runtime 0.2 request type is invalid")
        from sqvm.runtime_v02.core import canonical_request_payload_v02

        return canonical_request_payload_v02(request)

    def expand_points(self, request):
        if not isinstance(request, ExperimentRequestV02):
            raise ValueError("Runtime 0.2 request type is invalid")
        from sqvm.runtime_v02.core import expand_scan_v02

        return expand_scan_v02(request)

    def validate_point_result(self, point, result):
        if not isinstance(point, ScanPointV02):
            raise ValueError("Runtime 0.2 point type is invalid")
        from sqvm.runtime_v02.core import validate_point_result_v02

        validate_point_result_v02(point, result)

    def write_dataset(self, results, point_table_sha256, target):
        from sqvm.runtime_v02.core import write_dataset_v02

        return write_dataset_v02(target, results, point_table_sha256)

    def run(self, request_path, output_root, repository_root=None):
        from sqvm.runtime_v02.runner import run_experiment_v02

        return run_experiment_v02(request_path, output_root, repository_root)

    def verify_run(self, run_dir, repository_root=None):
        from sqvm.runtime_v02.verify import verify_experiment_run_v02

        return verify_experiment_run_v02(run_dir, repository_root)


_BUILTIN_RUNTIME_SCHEMAS = RuntimeSchemaRegistry((RuntimeV01Adapter(), RuntimeV02Adapter()))


def get_runtime_schema_registry() -> RuntimeSchemaRegistry:
    return _BUILTIN_RUNTIME_SCHEMAS


def get_runtime_schema_adapter(schema_version: str) -> RuntimeSchemaAdapter:
    return _BUILTIN_RUNTIME_SCHEMAS.resolve(schema_version)
