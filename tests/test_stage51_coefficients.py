from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from sqvm.evolution.stage51_coefficients import (
    build_evolution_coefficient_plan, publish_evolution_coefficient_artifact,
    verify_evolution_coefficient_artifact,
)
from sqvm.evolution.stage51_models import (
    Stage51EvolutionError, Stage51EvolutionInput, Stage51PhysicsContext,
)


def _array(values, dtype):
    value = np.asarray(values, dtype=dtype)
    value.setflags(write=False)
    return value


def _input() -> Stage51EvolutionInput:
    return Stage51EvolutionInput(
        "control-1", MappingProxyType({"control_id": "control-1", "manifest_sha256": "A", "receipt_sha256": "B", "inventory_sha256": "C", "effective_control_sha256": "D"}),
        _array([-0.25, 0.25, 0.75], "<f8"),
        _array([0.0j, 0.125j, 0.0j], "<c16"), _array([0.0j, 0.0j, 0.0j], "<c16"),
        MappingProxyType({"q1": _array([0.1, 0.1, 0.1], "<f8"), "c": _array([0.27, 0.27, 0.27], "<f8"), "q2": _array([0.0, 0.0, 0.0], "<f8")}),
        MappingProxyType({"q1": 5.0, "q2": 5.2}), (),
    )


def _context(root: Path) -> Stage51PhysicsContext:
    for name in ("source.json", "environment.json"):
        (root / name).write_text("{}\n", encoding="utf-8")
    return Stage51PhysicsContext(root, root, root / "design.md", root / "approval.json", root / "authority.json", root / "device.yaml", root / "hamiltonian.yaml", root / "solver.json", root / "source.json", root / "environment.json", root / "policy.json")


def _patch_authority(monkeypatch):
    import sqvm.evolution.stage51_coefficients as module
    authority = {"solver": {"method": "vern9"}}
    binding = MappingProxyType({"physics_authority": "P", "physics_authority_id": "authority-1", "device": "D", "hamiltonian": "H", "design": "X", "approval": "Y", "solver_validation": "S", "source_snapshot": "O", "environment_snapshot": "E", "publication_policy": "L"})
    monkeypatch.setattr(module, "admit_physics_authority", lambda context: (authority, binding))
    monkeypatch.setattr(module, "_model_probe", lambda authority, context, flux: MappingProxyType({"tensor_order": ["q1", "c", "q2"], "dimension": 27, "static_probe_sha256": "M"}))
    monkeypatch.setattr(module, "admit_verified_control", lambda handle, context: _input())
    monkeypatch.setattr(module, "_physics_preflight", lambda admitted, context: MappingProxyType({"ok": True}))


def test_signed_zoh_plan_and_raw_artifact_are_deterministic(monkeypatch, tmp_path):
    _patch_authority(monkeypatch)
    context, admitted = _context(tmp_path), _input()
    first = build_evolution_coefficient_plan(admitted, context)
    second = build_evolution_coefficient_plan(admitted, context)
    assert first.coefficient_plan_id == second.coefficient_plan_id
    assert np.array_equal(first.arrays["time_edge_ns"], [-0.5, 0.0, 0.5, 1.0])
    source_handle = object()
    handle = publish_evolution_coefficient_artifact(first, context, tmp_path / "coefficient", source_handle)
    assert verify_evolution_coefficient_artifact(handle.artifact_root, context, source_handle).coefficient_plan_id == first.coefficient_plan_id
    path = handle.artifact_root / "arrays" / "epsilon_q1.bin"
    path.write_bytes(bytes([path.read_bytes()[0] ^ 1]) + path.read_bytes()[1:])
    with pytest.raises(Stage51EvolutionError):
        verify_evolution_coefficient_artifact(handle.artifact_root, context, source_handle)


def test_worker_payload_verification_does_not_mint_control_capability(monkeypatch, tmp_path):
    _patch_authority(monkeypatch)
    import sqvm.evolution.stage51_coefficients as module

    context = _context(tmp_path)
    plan = build_evolution_coefficient_plan(_input(), context)
    handle = publish_evolution_coefficient_artifact(plan, context, tmp_path / "coefficient", object())

    payload, arrays, receipt = module._verify_coefficient_payload(handle.artifact_root, context)
    assert payload["coefficient_plan_id"] == plan.coefficient_plan_id
    assert set(arrays) == set(plan.arrays)
    assert receipt["physics_authority_id"] == handle.physics_authority_id
    with pytest.raises(Stage51EvolutionError, match="process-local source control handle required"):
        verify_evolution_coefficient_artifact(handle.artifact_root, context)
