from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
import subprocess

import numpy as np
import pytest

import sqvm.evolution.stage51_worker as worker
from sqvm.evolution.stage51_models import (
    Stage51EvolutionError, Stage51NumericalResult, Stage51PhysicsContext,
    VerifiedCoefficientHandle,
)


TOLERANCES = {"population_bound": 1.0e-10, "norm_error": 1.0e-9}


def _array(values, dtype="<f8"):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _result() -> Stage51NumericalResult:
    return Stage51NumericalResult(
        _array([0.0, 0.5]),
        _array([1.0, 0.0], "<c16"),
        _array([0.0, 1.0j], "<c16"),
        MappingProxyType({
            "000": _array([1.0, 0.0]),
            "100": _array([0.0, 1.0]),
            "001": _array([0.0, 0.0]),
            "101": _array([0.0, 0.0]),
        }),
        _array([0.0, 0.0]),
        _array([0.0, 0.0]),
        MappingProxyType({label: character * 64 for label, character in zip(("000", "100", "001", "101"), "ABCD", strict=True)}),
        MappingProxyType({"checks": []}),
    )


def _context(root: Path) -> Stage51PhysicsContext:
    paths = {name: root / f"{name}.json" for name in worker._CONTEXT_FIELDS if name not in {"repository_root", "output_root"}}
    return Stage51PhysicsContext(root, root / "output", **paths)


def _handle(root: Path) -> VerifiedCoefficientHandle:
    return VerifiedCoefficientHandle("plan", root / "coefficient", "M", "R", "I", "P", object())


def test_worker_result_gate_accepts_complete_finite_result():
    worker._validate_worker_result(_result(), TOLERANCES)


@pytest.mark.parametrize("failure", ("edge", "state_norm", "observable_length", "population", "norm", "hash"))
def test_worker_result_gate_rejects_invalid_results(failure):
    result = _result()
    if failure == "edge":
        result = replace(result, edge_time_ns=_array([0.0, 0.0]))
    elif failure == "state_norm":
        result = replace(result, final_state=_array([0.0, 2.0j], "<c16"))
    elif failure == "observable_length":
        result = replace(result, leakage=_array([0.0]))
    elif failure == "population":
        result = replace(result, leakage=_array([0.0, 1.1]))
    elif failure == "norm":
        result = replace(result, norm_error=_array([0.0, 1.0e-3]))
    else:
        result = replace(result, projector_sha256=MappingProxyType({"000": "bad"}))
    with pytest.raises(Stage51EvolutionError, match="NUMERICAL_RESULT_INVALID"):
        worker._validate_worker_result(result, TOLERANCES)


def test_worker_request_rejects_qcis_or_other_extra_fields(tmp_path):
    context = _context(tmp_path)
    context.output_root.mkdir()
    coefficient = context.output_root / "coefficient"
    coefficient.mkdir()
    session = context.output_root / "session"
    session.mkdir()
    request = worker._request_payload(coefficient, context, session / "result")
    request["qcis"] = "X Q1"
    with pytest.raises(Stage51EvolutionError, match="WORKER_ADMISSION_FAILED"):
        worker._parse_worker_request(request)


@pytest.mark.parametrize("failure", ("timeout", "crash"))
def test_parent_worker_failure_cleans_session(monkeypatch, tmp_path, failure):
    context = _context(tmp_path)
    context.output_root.mkdir()
    handle = _handle(context.output_root)
    monkeypatch.setattr(worker, "verify_evolution_coefficient_artifact", lambda *_args: handle)
    if failure == "timeout":
        monkeypatch.setattr(worker.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("worker", 0.1)))
        expected = "WORKER_TIMEOUT"
    else:
        monkeypatch.setattr(worker.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=7, stderr="boom", stdout=""))
        expected = "WORKER_EXECUTION_FAILED"
    with pytest.raises(Stage51EvolutionError, match=expected):
        worker.execute_stage51_worker(handle, context, timeout_s=0.1)
    assert not list(context.output_root.glob(".stage51-worker.*"))
