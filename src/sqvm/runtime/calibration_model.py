"""Isolated QuTiP evolution for the local calibration-scan profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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

from sqvm.evolution.stage51_models import Stage51NumericalResult, VerifiedCoefficientHandle
from sqvm.evolution.stage51_worker import ARRAY_SPECS, _validate_worker_result
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import inventory_tree_no_follow


PROTOCOL_VERSION = "0.1"
MODEL_AUTHORITY_PATH = "configs/runtime/calibration_scan/model_authority_v1.json"
REQUEST_NAME = "request.json"
METADATA_NAME = "worker_result.json"
INVENTORY_NAME = "array_inventory.json"
_COEFFICIENT_ARRAYS = {
    "time_edge_ns": ("<f8", "arrays/time_edge_ns.bin"),
    "epsilon_q1": ("<c16", "arrays/epsilon_q1.bin"),
    "epsilon_q2": ("<c16", "arrays/epsilon_q2.bin"),
    "absolute_flux_q1": ("<f8", "arrays/absolute_flux_q1.bin"),
    "absolute_flux_c": ("<f8", "arrays/absolute_flux_c.bin"),
    "absolute_flux_q2": ("<f8", "arrays/absolute_flux_q2.bin"),
}


class CalibrationModelError(ValueError):
    pass


def load_calibration_model_authority(
    repository_root: str | Path,
) -> Mapping[str, Any]:
    root = Path(repository_root).resolve(strict=True)
    path = root / MODEL_AUTHORITY_PATH
    value = _canonical(path)
    expected = {
        "schema_version",
        "artifact_type",
        "artifact_version",
        "status",
        "model_authority_id",
        "model",
        "clock",
        "solver",
        "tolerances",
        "claim",
        "sources",
    }
    if (
        set(value) != expected
        or value.get("schema_version") != PROTOCOL_VERSION
        or value.get("artifact_type") != "stage_07_calibration_model_authority"
        or value.get("artifact_version") != PROTOCOL_VERSION
        or value.get("status") != "local_simulation_approved"
    ):
        raise CalibrationModelError("calibration model authority schema is invalid")
    identity = {key: item for key, item in value.items() if key != "model_authority_id"}
    if value.get("model_authority_id") != _sha(identity):
        raise CalibrationModelError("calibration model authority identity is invalid")
    _validate_model(value)
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise CalibrationModelError("calibration model sources are invalid")
    for row in sources:
        if not isinstance(row, Mapping) or set(row) != {"path", "raw_sha256"}:
            raise CalibrationModelError("calibration model source binding is invalid")
        source = (root / str(row["path"])).resolve(strict=True)
        _inside(source, root, "model source")
        if raw_file_sha256(source) != row["raw_sha256"]:
            raise CalibrationModelError(f"calibration model source changed: {row['path']}")
    return MappingProxyType(value)


def execute_calibration_model_worker(
    coefficients: VerifiedCoefficientHandle,
    repository_root: str | Path,
    *,
    timeout_s: float,
) -> Stage51NumericalResult:
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int | float)
        or not math.isfinite(float(timeout_s))
        or float(timeout_s) <= 0.0
    ):
        raise CalibrationModelError("worker timeout is invalid")
    if not isinstance(coefficients, VerifiedCoefficientHandle):
        raise CalibrationModelError("verified coefficient handle is required")
    root = Path(repository_root).resolve(strict=True)
    authority = load_calibration_model_authority(root)
    output_root = coefficients.artifact_root.parent.resolve()
    session = output_root / f".calibration-worker.{uuid.uuid4().hex}"
    request_path = session / REQUEST_NAME
    worker_output = session / "result"
    session.mkdir()
    request = {
        "schema_version": PROTOCOL_VERSION,
        "repository_root": str(root),
        "coefficient_artifact": str(coefficients.artifact_root.resolve(strict=True)),
        "model_authority": str((root / MODEL_AUTHORITY_PATH).resolve(strict=True)),
        "output_dir": str(worker_output.resolve()),
    }
    request_path.write_bytes(canonical_json_bytes(request))
    command = [sys.executable, "-m", "sqvm.runtime.calibration_model", str(request_path)]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str((root / "src").resolve())
    try:
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=float(timeout_s),
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise CalibrationModelError(f"calibration worker timeout: {timeout_s}s") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "worker failed").strip()[-1000:]
            raise CalibrationModelError(f"calibration worker failed: {detail}")
        return _read_worker_result(worker_output, authority)
    finally:
        if session.exists():
            shutil.rmtree(session, ignore_errors=True)


def run_effective_qutrit_model(
    coefficient_artifact: Path,
    authority: Mapping[str, Any],
) -> Stage51NumericalResult:
    arrays, plan = _read_coefficients(coefficient_artifact)
    edges = arrays["time_edge_ns"]
    epsilon_q1 = arrays["epsilon_q1"]
    epsilon_q2 = arrays["epsilon_q2"]
    if edges.size != epsilon_q1.size + 1 or epsilon_q1.shape != epsilon_q2.shape:
        raise CalibrationModelError("coefficient clock is inconsistent")
    model = authority["model"]
    idle = model["idle_flux_phi0"]
    for name in ("q1", "c", "q2"):
        values = arrays[f"absolute_flux_{name}"]
        if values.shape != epsilon_q1.shape or not np.all(values == float(idle[name])):
            raise CalibrationModelError(
                "effective calibration model currently admits idle-flux XY experiments only"
            )
    frame = plan.get("frame_reference_frequency_GHz")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"}:
        raise CalibrationModelError("coefficient frame authority is invalid")

    import qutip as qt

    if not str(qt.__version__).startswith(("5.1", "5.2", "5.3")):
        raise CalibrationModelError(f"unsupported QuTiP version: {qt.__version__}")
    identity = qt.qeye(3)
    a1 = qt.tensor(qt.destroy(3), identity)
    a2 = qt.tensor(identity, qt.destroy(3))
    n1, n2 = a1.dag() * a1, a2.dag() * a2
    q1, q2 = model["q1"], model["q2"]
    h0 = (
        (float(q1["f01_GHz"]) - float(frame["q1"])) * n1
        + 0.5 * float(q1["anharmonicity_GHz"]) * n1 * (n1 - 1.0)
        + (float(q2["f01_GHz"]) - float(frame["q2"])) * n2
        + 0.5 * float(q2["anharmonicity_GHz"]) * n2 * (n2 - 1.0)
        + float(model["coupling_GHz"]) * (a1.dag() * a2 + a1 * a2.dag())
    )
    starts = edges[:-1]
    if not np.array_equal(
        np.diff(edges),
        np.full(edges.size - 1, float(authority["clock"]["dt_ns"]), dtype="<f8"),
    ):
        raise CalibrationModelError("coefficient clock differs from model authority")

    def hamiltonian(time_ns: float, _args=None):
        index = min(max(int(np.searchsorted(starts, float(time_ns), side="right") - 1), 0), epsilon_q1.size - 1)
        drive = 0.5 * (
            epsilon_q1[index] * a1.dag()
            + np.conj(epsilon_q1[index]) * a1
            + epsilon_q2[index] * a2.dag()
            + np.conj(epsilon_q2[index]) * a2
        )
        return 2.0 * np.pi * (h0 + drive)

    initial = qt.tensor(qt.basis(3, 0), qt.basis(3, 0))
    projectors = {
        "000": qt.tensor(qt.basis(3, 0), qt.basis(3, 0)).proj(),
        "100": qt.tensor(qt.basis(3, 1), qt.basis(3, 0)).proj(),
        "001": qt.tensor(qt.basis(3, 0), qt.basis(3, 1)).proj(),
        "101": qt.tensor(qt.basis(3, 1), qt.basis(3, 1)).proj(),
    }
    solver = authority["solver"]
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = qt.sesolve(
            qt.QobjEvo(hamiltonian),
            initial,
            edges,
            e_ops=list(projectors.values()),
            options={
                "method": solver["method"],
                "rtol": solver["rtol"],
                "atol": solver["atol"],
                "nsteps": solver["nsteps"],
                "max_step": solver["max_step_ns"],
                "store_states": True,
                "store_final_state": True,
                "normalize_output": solver["normalize_output"],
                "progress_bar": None,
            },
        )
    if caught:
        raise CalibrationModelError(f"QuTiP solver warning: {caught[0].message}")
    states = tuple(np.asarray(state.full(), dtype="<c16").reshape(-1) for state in result.states)
    populations = {
        label: np.asarray(result.expect[index], dtype="<f8")
        for index, label in enumerate(projectors)
    }
    norm = np.asarray([float(np.vdot(state, state).real) for state in states], dtype="<f8")
    computational = sum(populations.values())
    projector_sha256 = {
        label: hashlib.sha256(np.asarray(value.full(), dtype="<c16").tobytes()).hexdigest().upper()
        for label, value in projectors.items()
    }
    numerical = Stage51NumericalResult(
        np.asarray(edges, dtype="<f8"),
        states[0],
        states[-1],
        MappingProxyType(populations),
        np.asarray(1.0 - computational, dtype="<f8"),
        np.asarray(np.abs(norm - 1.0), dtype="<f8"),
        MappingProxyType(projector_sha256),
        MappingProxyType(
            {
                "engine_id": model["model_id"],
                "model_authority_id": authority["model_authority_id"],
                "qutip_version": str(qt.__version__),
                "solver_spec": dict(solver),
            }
        ),
    )
    _validate_worker_result(numerical, authority["tolerances"])
    return numerical


def _write_worker_result(
    output: Path,
    result: Stage51NumericalResult,
    authority: Mapping[str, Any],
    elapsed_s: float,
) -> None:
    output.mkdir()
    arrays = {
        "edge_time_ns": result.edge_time_ns,
        "initial_state": result.initial_state,
        "final_state": result.final_state,
        **{f"population_{label}": result.populations[label] for label in ("000", "100", "001", "101")},
        "leakage": result.leakage,
        "norm_error": result.norm_error,
    }
    rows = []
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        array = np.asarray(arrays[name], dtype=dtype, order="C")
        raw = array.tobytes(order="C")
        path = output / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        rows.append(
            {
                "name": name,
                "path": relative,
                "dtype": dtype,
                "shape": [int(array.size)],
                "element_count": int(array.size),
                "byte_count": len(raw),
                "unit": unit,
                "sha256": hashlib.sha256(raw).hexdigest().upper(),
            }
        )
    inventory = {
        "schema_version": PROTOCOL_VERSION,
        "artifact_type": "stage_07_calibration_worker_array_inventory",
        "arrays": sorted(rows, key=lambda row: row["name"]),
    }
    metadata = {
        "schema_version": PROTOCOL_VERSION,
        "artifact_type": "stage_07_calibration_worker_result",
        "status": "complete",
        "worker_identity": {
            "python_executable": str(Path(sys.executable).resolve()),
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "qutip_version": result.diagnostics["qutip_version"],
        },
        "model_authority_id": authority["model_authority_id"],
        "runtime_s": float(elapsed_s),
        "projector_sha256": dict(result.projector_sha256),
        "diagnostics": dict(result.diagnostics),
    }
    (output / INVENTORY_NAME).write_bytes(canonical_json_bytes(inventory))
    (output / METADATA_NAME).write_bytes(canonical_json_bytes(metadata))


def _read_worker_result(
    output: Path,
    authority: Mapping[str, Any],
) -> Stage51NumericalResult:
    expected = {METADATA_NAME, INVENTORY_NAME, *{spec[2] for spec in ARRAY_SPECS.values()}}
    rows = inventory_tree_no_follow(output)
    if {row["path"] for row in rows if row.get("entry_type") == "file"} != expected:
        raise CalibrationModelError("calibration worker file set is invalid")
    metadata, inventory = _canonical(output / METADATA_NAME), _canonical(output / INVENTORY_NAME)
    if (
        set(metadata)
        != {
            "schema_version",
            "artifact_type",
            "status",
            "worker_identity",
            "model_authority_id",
            "runtime_s",
            "projector_sha256",
            "diagnostics",
        }
        or metadata.get("artifact_type") != "stage_07_calibration_worker_result"
        or metadata.get("status") != "complete"
        or metadata.get("model_authority_id") != authority["model_authority_id"]
    ):
        raise CalibrationModelError("calibration worker metadata is invalid")
    rows = inventory.get("arrays")
    if (
        set(inventory) != {"schema_version", "artifact_type", "arrays"}
        or inventory.get("artifact_type") != "stage_07_calibration_worker_array_inventory"
        or not isinstance(rows, list)
        or len(rows) != len(ARRAY_SPECS)
    ):
        raise CalibrationModelError("calibration worker inventory is invalid")
    by_name = {row.get("name"): row for row in rows if isinstance(row, Mapping)}
    arrays: dict[str, np.ndarray] = {}
    for name, (dtype, unit, relative) in ARRAY_SPECS.items():
        row = by_name.get(name)
        raw = (output / relative).read_bytes()
        if (
            not isinstance(row, Mapping)
            or row.get("path") != relative
            or row.get("dtype") != dtype
            or row.get("unit") != unit
            or row.get("byte_count") != len(raw)
            or row.get("sha256") != hashlib.sha256(raw).hexdigest().upper()
        ):
            raise CalibrationModelError(f"calibration worker array is invalid: {name}")
        array = np.frombuffer(raw, dtype=dtype).copy(order="C")
        array.setflags(write=False)
        arrays[name] = array
    diagnostics = {
        **metadata["diagnostics"],
        "worker_identity": metadata["worker_identity"],
        "runtime_s": metadata["runtime_s"],
    }
    result = Stage51NumericalResult(
        arrays["edge_time_ns"],
        arrays["initial_state"],
        arrays["final_state"],
        MappingProxyType({label: arrays[f"population_{label}"] for label in ("000", "100", "001", "101")}),
        arrays["leakage"],
        arrays["norm_error"],
        MappingProxyType(metadata["projector_sha256"]),
        MappingProxyType(diagnostics),
    )
    _validate_worker_result(result, authority["tolerances"])
    return result


def _read_coefficients(artifact: Path) -> tuple[dict[str, np.ndarray], Mapping[str, Any]]:
    inventory = _canonical(artifact / INVENTORY_NAME)
    plan = _canonical(artifact / "coefficient_plan.json")
    rows = inventory.get("arrays")
    if not isinstance(rows, list):
        raise CalibrationModelError("coefficient inventory is invalid")
    by_name = {row.get("name"): row for row in rows if isinstance(row, Mapping)}
    arrays = {}
    for name, (dtype, relative) in _COEFFICIENT_ARRAYS.items():
        row = by_name.get(name)
        raw = (artifact / relative).read_bytes()
        if (
            not isinstance(row, Mapping)
            or row.get("path") != relative
            or row.get("dtype") != dtype
            or row.get("sha256") != hashlib.sha256(raw).hexdigest().upper()
        ):
            raise CalibrationModelError(f"coefficient array is invalid: {name}")
        arrays[name] = np.frombuffer(raw, dtype=dtype).copy(order="C")
    return arrays, plan


def _validate_model(authority: Mapping[str, Any]) -> None:
    model = authority.get("model")
    solver = authority.get("solver")
    tolerances = authority.get("tolerances")
    claim = authority.get("claim")
    expected_model_keys = {
        "model_id",
        "tensor_order",
        "truncation",
        "q1",
        "q2",
        "coupling_GHz",
        "idle_flux_phi0",
    }
    expected_solver_keys = {
        "qutip_version_spec",
        "method",
        "rtol",
        "atol",
        "nsteps",
        "max_step_ns",
        "normalize_output",
    }
    if (
        not isinstance(model, Mapping)
        or set(model) != expected_model_keys
        or model.get("model_id") != "effective_two_qutrit_v1"
        or model.get("tensor_order") != ["q1", "q2"]
        or model.get("truncation") != [3, 3]
        or not isinstance(solver, Mapping)
        or set(solver) != expected_solver_keys
        or not isinstance(tolerances, Mapping)
        or set(tolerances) != {"norm_error", "population_bound"}
        or authority.get("clock") != {"dt_ns": 0.5}
        or solver.get("qutip_version_spec") != ">=5.1,<5.4"
        or solver.get("method") != "vern9"
        or solver.get("normalize_output") is not False
        or type(solver.get("nsteps")) is not int
        or solver["nsteps"] <= 0
        or claim
        != {
            "formal_scale_qualified": False,
            "hardware_measurement": False,
            "scope": "local_simulator_calibration_only",
        }
    ):
        raise CalibrationModelError("calibration model contract is invalid")
    numeric = [
        model.get("coupling_GHz"),
        authority.get("clock", {}).get("dt_ns"),
        solver.get("rtol"),
        solver.get("atol"),
        solver.get("max_step_ns"),
        tolerances.get("norm_error"),
        tolerances.get("population_bound"),
    ]
    for target in ("q1", "q2"):
        row = model.get(target)
        if not isinstance(row, Mapping) or set(row) != {
            "f01_GHz",
            "anharmonicity_GHz",
        }:
            raise CalibrationModelError("calibration qutrit parameters are invalid")
        numeric.extend((row.get("f01_GHz"), row.get("anharmonicity_GHz")))
    idle = model.get("idle_flux_phi0")
    if not isinstance(idle, Mapping) or set(idle) != {"q1", "c", "q2"}:
        raise CalibrationModelError("calibration idle flux is invalid")
    numeric.extend(idle.values())
    if any(isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)) for value in numeric):
        raise CalibrationModelError("calibration model numbers are invalid")
    if (
        float(solver["rtol"]) <= 0.0
        or float(solver["atol"]) <= 0.0
        or float(solver["max_step_ns"]) <= 0.0
        or float(tolerances["norm_error"]) <= 0.0
        or float(tolerances["population_bound"]) < 0.0
    ):
        raise CalibrationModelError("calibration solver numbers are invalid")


def _worker_main(request_path: Path) -> int:
    request = _canonical(request_path)
    if (
        set(request)
        != {
            "schema_version",
            "repository_root",
            "coefficient_artifact",
            "model_authority",
            "output_dir",
        }
        or request.get("schema_version") != PROTOCOL_VERSION
    ):
        raise CalibrationModelError("calibration worker request is invalid")
    root = Path(request["repository_root"]).resolve(strict=True)
    coefficient = Path(request["coefficient_artifact"]).resolve(strict=True)
    authority_path = Path(request["model_authority"]).resolve(strict=True)
    output = Path(request["output_dir"]).resolve()
    _inside(coefficient, root, "coefficient artifact")
    _inside(authority_path, root, "model authority")
    _inside(output, root, "worker output")
    if authority_path != root / MODEL_AUTHORITY_PATH or output.exists():
        raise CalibrationModelError("calibration worker path binding is invalid")
    authority = load_calibration_model_authority(root)
    started = time.perf_counter()
    result = run_effective_qutrit_model(coefficient, authority)
    _write_worker_result(output, result, authority, time.perf_counter() - started)
    return 0


def _canonical(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationModelError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise CalibrationModelError(f"noncanonical JSON: {path.name}")
    return value


def _inside(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CalibrationModelError(f"{label} is outside repository") from exc


def _sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


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


__all__ = [
    "CalibrationModelError",
    "MODEL_AUTHORITY_PATH",
    "execute_calibration_model_worker",
    "load_calibration_model_authority",
    "run_effective_qutrit_model",
]
