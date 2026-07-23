from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from dataclasses import replace
import inspect
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest

import sqvm.evolution.stage51_artifacts as artifacts
from sqvm.evolution.stage51_models import (
    Stage51EvolutionError, Stage51NumericalResult, Stage51PhysicsContext,
    VerifiedCoefficientHandle,
)


def _array(values, dtype="<f8"):
    result = np.asarray(values, dtype=dtype)
    result.setflags(write=False)
    return result


def _result() -> Stage51NumericalResult:
    return Stage51NumericalResult(
        _array([0.0, 0.5]), _array([1.0, 0.0], "<c16"), _array([0.0, 1.0j], "<c16"),
        MappingProxyType({
            "000": _array([1.0, 0.0]), "100": _array([0.0, 1.0]),
            "001": _array([0.0, 0.0]), "101": _array([0.0, 0.0]),
        }),
        _array([0.0, 0.0]), _array([0.0, 0.0]),
        MappingProxyType({label: character * 64 for label, character in zip(("000", "100", "001", "101"), "ABCD", strict=True)}),
        MappingProxyType({
            "worker_identity": {"python": "accepted"}, "solver_spec": {"method": "vern9"},
            "runtime_s": 0.123, "checks": [],
        }),
    )


def _fixture(tmp_path: Path):
    output = tmp_path / "output"
    coefficient = output / "coefficient"
    coefficient.mkdir(parents=True)
    source = coefficient / "source_snapshot.json"
    environment = coefficient / "environment_snapshot.json"
    source.write_text("{}", encoding="utf-8")
    environment.write_text("{}", encoding="utf-8")
    (coefficient / "coefficient_plan.json").write_text(json.dumps({
        "control_binding": {"control_id": "control-1"},
        "physics_authority_binding": {"physics_authority_id": "physics-1"},
    }), encoding="utf-8")
    context = Stage51PhysicsContext(
        tmp_path, output, tmp_path / "design", tmp_path / "approval", tmp_path / "authority",
        tmp_path / "device", tmp_path / "hamiltonian", tmp_path / "solver", source,
        environment, tmp_path / "policy",
    )
    handle = VerifiedCoefficientHandle(
        "plan-1", coefficient, "M" * 64, "R" * 64, "I" * 64, "physics-1",
        SimpleNamespace(control_id="control-1"),
    )
    return context, handle


def _patch(monkeypatch, handle):
    result = _result()
    monkeypatch.setattr(artifacts, "verify_evolution_coefficient_artifact", lambda *_args: handle)
    monkeypatch.setattr(artifacts, "execute_stage51_worker", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(artifacts, "run_stage51_worker_kernel", lambda *_args: result)
    monkeypatch.setattr(artifacts, "admit_physics_authority", lambda *_args: (
        {"tolerances": {"population_bound": 1.0e-10, "norm_error": 1.0e-9}},
        {"physics_authority_id": "physics-1"},
    ))


def test_result_publication_is_deterministic_and_replay_verifiable(monkeypatch, tmp_path):
    context, handle = _fixture(tmp_path)
    _patch(monkeypatch, handle)

    first = artifacts.run_verified_control_evolution(handle, context, context.output_root / "result-a", timeout_s=1.0)
    second = artifacts.run_verified_control_evolution(handle, context, context.output_root / "result-b", timeout_s=1.0)
    verified = artifacts.verify_stage51_evolution_artifact(first.artifact_root, handle, context)

    assert verified.result_id == first.result_id == second.result_id
    assert verified.replay_fidelity == 1.0
    first_files = {path.relative_to(first.artifact_root).as_posix(): path.read_bytes() for path in first.artifact_root.rglob("*") if path.is_file()}
    second_files = {path.relative_to(second.artifact_root).as_posix(): path.read_bytes() for path in second.artifact_root.rglob("*") if path.is_file()}
    assert first_files == second_files


def test_result_single_byte_tamper_is_rejected(monkeypatch, tmp_path):
    context, handle = _fixture(tmp_path)
    _patch(monkeypatch, handle)
    published = artifacts.run_verified_control_evolution(handle, context, context.output_root / "result", timeout_s=1.0)
    path = published.artifact_root / "states" / "final_state.bin"
    raw = path.read_bytes()
    path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises(Stage51EvolutionError, match="ARTIFACT_VERIFICATION_FAILED"):
        artifacts.verify_stage51_evolution_artifact(published.artifact_root, handle, context)


def test_public_verifier_has_no_replay_injection_and_rejects_forged_observables(monkeypatch, tmp_path):
    context, handle = _fixture(tmp_path)
    _patch(monkeypatch, handle)
    forged = replace(_result(), leakage=_array([0.0, 0.125]))
    monkeypatch.setattr(artifacts, "execute_stage51_worker", lambda *_args, **_kwargs: forged)
    target = context.output_root / "forged-result"

    assert "independent_result" not in inspect.signature(artifacts.verify_stage51_evolution_artifact).parameters
    with pytest.raises(Stage51EvolutionError, match="NUMERICAL_RESULT_INVALID"):
        artifacts.run_verified_control_evolution(handle, context, target, timeout_s=1.0)
    assert not target.exists()
