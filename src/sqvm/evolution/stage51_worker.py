"""Isolated Stage 5.1 worker protocol and parent-process execution gate."""

from __future__ import annotations

import argparse
from dataclasses import fields
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from types import MappingProxyType
from typing import Any, Mapping
import uuid
import warnings

import numpy as np

from sqvm.evolution.stage51_authority import (
    admit_physics_authority, fail, plain, read_json,
)
from sqvm.evolution.stage51_coefficients import verify_evolution_coefficient_artifact
from sqvm.evolution.stage51_models import (
    Stage51FailureCode, Stage51NumericalResult, Stage51PhysicsContext,
    VerifiedCoefficientHandle,
)
from sqvm.evolution.stage51_physics import run_stage51_worker_kernel
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.storage import inventory_tree_no_follow


PROTOCOL_VERSION = "0.1"
REQUEST_NAME = "request.json"
METADATA_NAME = "worker_result.json"
INVENTORY_NAME = "array_inventory.json"
ARRAY_SPECS = {
    "edge_time_ns": ("<f8", "ns", "arrays/edge_time_ns.bin"),
    "initial_state": ("<c16", "amplitude", "arrays/initial_state.bin"),
    "final_state": ("<c16", "amplitude", "arrays/final_state.bin"),
    "population_000": ("<f8", "probability", "arrays/population_000.bin"),
    "population_100": ("<f8", "probability", "arrays/population_100.bin"),
    "population_001": ("<f8", "probability", "arrays/population_001.bin"),
    "population_101": ("<f8", "probability", "arrays/population_101.bin"),
    "leakage": ("<f8", "probability", "arrays/leakage.bin"),
    "norm_error": ("<f8", "absolute", "arrays/norm_error.bin"),
}
_CONTEXT_FIELDS = tuple(field.name for field in fields(Stage51PhysicsContext))


def _context_payload(context: Stage51PhysicsContext) -> dict[str, str]:
    if not isinstance(context, Stage51PhysicsContext):
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "typed context required")
    return {name: str(Path(getattr(context, name)).resolve()) for name in _CONTEXT_FIELDS}


def _context_from_payload(payload: Any) -> Stage51PhysicsContext:
    if not isinstance(payload, Mapping) or set(payload) != set(_CONTEXT_FIELDS):
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "context fields")
    if any(not isinstance(payload[name], str) or not Path(payload[name]).is_absolute() for name in _CONTEXT_FIELDS):
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "context paths")
    return Stage51PhysicsContext(**{name: Path(payload[name]) for name in _CONTEXT_FIELDS})


def _request_payload(
    coefficient_artifact: Path,
    context: Stage51PhysicsContext,
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": PROTOCOL_VERSION,
        "coefficient_artifact": str(Path(coefficient_artifact).resolve()),
        "context": _context_payload(context),
        "output_dir": str(Path(output_dir).resolve()),
    }


def _parse_worker_request(value: Any) -> tuple[Path, Stage51PhysicsContext, Path]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "coefficient_artifact", "context", "output_dir"}:
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "request fields")
    if value.get("schema_version") != PROTOCOL_VERSION:
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "request version")
    coefficient, output = value.get("coefficient_artifact"), value.get("output_dir")
    if not isinstance(coefficient, str) or not isinstance(output, str) or not Path(coefficient).is_absolute() or not Path(output).is_absolute():
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "request paths")
    context = _context_from_payload(value.get("context"))
    coefficient_path, output_path = Path(coefficient), Path(output)
    try:
        coefficient_path.resolve(strict=True).relative_to(context.output_root.resolve(strict=True))
        output_path.parent.resolve(strict=True).relative_to(context.output_root.resolve(strict=True))
    except (OSError, ValueError):
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "request path boundary")
    if output_path.exists():
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "worker output exists")
    return coefficient_path, context, output_path


def _result_arrays(result: Stage51NumericalResult) -> dict[str, np.ndarray]:
    return {
        "edge_time_ns": result.edge_time_ns,
        "initial_state": result.initial_state,
        "final_state": result.final_state,
        **{f"population_{label}": result.populations[label] for label in ("000", "100", "001", "101")},
        "leakage": result.leakage,
        "norm_error": result.norm_error,
    }


def _write_worker_result(
    output_dir: Path,
    result: Stage51NumericalResult,
    context: Stage51PhysicsContext,
    elapsed_s: float,
) -> None:
    output_dir.mkdir()
    rows: list[dict[str, Any]] = []
    arrays = _result_arrays(result)
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        array = np.asarray(arrays[name], dtype=dtype, order="C")
        if array.ndim != 1 or array.size == 0 or not np.all(np.isfinite(array)):
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, name)
        raw = array.tobytes(order="C")
        path = output_dir / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        import hashlib
        rows.append({
            "name": name, "path": relative, "dtype": dtype, "shape": [int(array.size)],
            "element_count": int(array.size), "byte_count": len(raw), "unit": unit,
            "sha256": hashlib.sha256(raw).hexdigest().upper(),
        })
    authority, binding = admit_physics_authority(context)
    inventory = {
        "schema_version": PROTOCOL_VERSION,
        "artifact_type": "stage_05_1_worker_array_inventory",
        "artifact_version": PROTOCOL_VERSION,
        "arrays": sorted(rows, key=lambda row: row["name"]),
    }
    metadata = {
        "schema_version": PROTOCOL_VERSION,
        "artifact_type": "stage_05_1_worker_result",
        "artifact_version": PROTOCOL_VERSION,
        "status": "complete",
        "worker_identity": {
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "qutip_version": _qutip_version(),
        },
        "physics_authority_id": binding["physics_authority_id"],
        "solver_spec": plain(authority["solver"]),
        "runtime_s": float(elapsed_s),
        "projector_sha256": plain(result.projector_sha256),
        "diagnostics": plain(result.diagnostics),
    }
    (output_dir / INVENTORY_NAME).write_bytes(canonical_json_bytes(inventory))
    (output_dir / METADATA_NAME).write_bytes(canonical_json_bytes(metadata))


def _qutip_version() -> str:
    import qutip
    return str(qutip.__version__)


def _read_worker_result(output_dir: Path, context: Stage51PhysicsContext) -> Stage51NumericalResult:
    expected = {METADATA_NAME, INVENTORY_NAME, *{spec[2] for spec in ARRAY_SPECS.values()}}
    actual = {row["path"] for row in inventory_tree_no_follow(output_dir) if row.get("entry_type") == "file"}
    if actual != expected or any(row.get("entry_type") in {"link", "other"} for row in inventory_tree_no_follow(output_dir)):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker file set")
    metadata = read_json(output_dir / METADATA_NAME, Stage51FailureCode.NUMERICAL_RESULT_INVALID)
    inventory = read_json(output_dir / INVENTORY_NAME, Stage51FailureCode.NUMERICAL_RESULT_INVALID)
    metadata_keys = {
        "schema_version", "artifact_type", "artifact_version", "status", "worker_identity",
        "physics_authority_id", "solver_spec", "runtime_s", "projector_sha256", "diagnostics",
    }
    if set(metadata) != metadata_keys or metadata.get("schema_version") != PROTOCOL_VERSION or metadata.get("artifact_type") != "stage_05_1_worker_result" or metadata.get("artifact_version") != PROTOCOL_VERSION or metadata.get("status") != "complete":
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker metadata")
    identity = metadata.get("worker_identity")
    expected_identity = {
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "qutip_version": _qutip_version(),
    }
    if identity != expected_identity:
        fail(Stage51FailureCode.SOLVER_AUTHORITY_INVALID, "worker identity")
    authority, binding = admit_physics_authority(context)
    if metadata.get("physics_authority_id") != binding["physics_authority_id"] or metadata.get("solver_spec") != plain(authority["solver"]):
        fail(Stage51FailureCode.SOLVER_AUTHORITY_INVALID, "worker solver binding")
    runtime = metadata.get("runtime_s")
    if type(runtime) is not float or not np.isfinite(runtime) or runtime < 0.0:
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker runtime")
    rows = inventory.get("arrays") if isinstance(inventory, Mapping) else None
    if set(inventory) != {"schema_version", "artifact_type", "artifact_version", "arrays"} or inventory.get("schema_version") != PROTOCOL_VERSION or inventory.get("artifact_type") != "stage_05_1_worker_array_inventory" or inventory.get("artifact_version") != PROTOCOL_VERSION or not isinstance(rows, list) or len(rows) != len(ARRAY_SPECS):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker inventory")
    arrays: dict[str, np.ndarray] = {}
    seen: set[str] = set()
    import hashlib
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"name", "path", "dtype", "shape", "element_count", "byte_count", "unit", "sha256"}:
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker inventory row")
        name = row.get("name")
        if name not in ARRAY_SPECS or name in seen:
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "worker array name")
        seen.add(name)
        dtype, unit, relative = ARRAY_SPECS[name]
        count = row.get("element_count")
        if row.get("path") != relative or row.get("dtype") != dtype or row.get("unit") != unit or type(count) is not int or count <= 0 or row.get("shape") != [count] or row.get("byte_count") != count * np.dtype(dtype).itemsize:
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, name)
        path = output_dir / relative
        raw = path.read_bytes()
        if len(raw) != row["byte_count"] or hashlib.sha256(raw).hexdigest().upper() != row.get("sha256"):
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, name)
        array = np.frombuffer(raw, dtype=dtype).copy(order="C")
        if not np.all(np.isfinite(array)):
            fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, name)
        array.setflags(write=False)
        arrays[name] = array
    diagnostics = {
        **metadata["diagnostics"],
        "worker_identity": identity,
        "solver_spec": metadata["solver_spec"],
        "runtime_s": runtime,
    }
    result = Stage51NumericalResult(
        arrays["edge_time_ns"], arrays["initial_state"], arrays["final_state"],
        MappingProxyType({label: arrays[f"population_{label}"] for label in ("000", "100", "001", "101")}),
        arrays["leakage"], arrays["norm_error"], MappingProxyType(metadata["projector_sha256"]),
        MappingProxyType(diagnostics),
    )
    _validate_worker_result(result, authority["tolerances"])
    return result


def _validate_worker_result(result: Stage51NumericalResult, tolerances: Mapping[str, float]) -> None:
    if not isinstance(result, Stage51NumericalResult):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "typed result required")
    edges = result.edge_time_ns
    if edges.ndim != 1 or edges.size < 2 or not np.all(np.isfinite(edges)) or not np.all(np.diff(edges) > 0.0):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "edge time")
    edge_count = edges.size
    states = (result.initial_state, result.final_state)
    if any(state.ndim != 1 or state.size == 0 or not np.all(np.isfinite(state)) for state in states) or states[0].shape != states[1].shape:
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "states")
    observables = (*result.populations.values(), result.leakage, result.norm_error)
    if set(result.populations) != {"000", "100", "001", "101"} or any(value.ndim != 1 or value.size != edge_count or not np.all(np.isfinite(value)) for value in observables):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "observables")
    bound = float(tolerances["population_bound"])
    if any(np.any((value < -bound) | (value > 1.0 + bound)) for value in (*result.populations.values(), result.leakage)):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "population bounds")
    if np.max(result.norm_error) > float(tolerances["norm_error"]):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "norm error")
    if any(abs(float(np.vdot(state, state).real) - 1.0) > float(tolerances["norm_error"]) for state in states):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "state norm")
    hashes = result.projector_sha256
    if set(hashes) != {"000", "100", "001", "101"} or any(not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789ABCDEF" for character in value) for value in hashes.values()):
        fail(Stage51FailureCode.NUMERICAL_RESULT_INVALID, "projector hashes")


def execute_stage51_worker(
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
    *,
    timeout_s: float,
) -> Stage51NumericalResult:
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not np.isfinite(timeout_s) or timeout_s <= 0.0:
        fail(Stage51FailureCode.WORKER_ADMISSION_FAILED, "timeout")
    verified = verify_evolution_coefficient_artifact(
        coefficients.artifact_root, context, coefficients.source_control_handle,
    )
    output_root = context.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    session = output_root / f".stage51-worker.{uuid.uuid4().hex}"
    request_path, worker_output = session / REQUEST_NAME, session / "result"
    session.mkdir()
    request_path.write_bytes(canonical_json_bytes(_request_payload(verified.artifact_root, context, worker_output)))
    command = [sys.executable, "-m", "sqvm.evolution.stage51_worker", str(request_path)]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str((context.repository_root / "src").resolve())
    try:
        try:
            completed = subprocess.run(
                command, cwd=context.repository_root, capture_output=True, text=True,
                timeout=float(timeout_s), check=False, env=environment,
            )
        except subprocess.TimeoutExpired:
            fail(Stage51FailureCode.WORKER_TIMEOUT, f"timeout_s={float(timeout_s)}")
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "worker failed").strip()[-1000:]
            fail(Stage51FailureCode.WORKER_EXECUTION_FAILED, detail)
        return _read_worker_result(worker_output, context)
    finally:
        if session.exists():
            shutil.rmtree(session)


def _worker_main(request_path: Path) -> int:
    request = read_json(request_path, Stage51FailureCode.WORKER_ADMISSION_FAILED)
    coefficient, context, output = _parse_worker_request(request)
    started = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = run_stage51_worker_kernel(coefficient, context)
    if caught:
        fail(Stage51FailureCode.WORKER_EXECUTION_FAILED, f"solver warning: {caught[0].message}")
    _write_worker_result(output, result, context, time.perf_counter() - started)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    try:
        return _worker_main(args.request)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
