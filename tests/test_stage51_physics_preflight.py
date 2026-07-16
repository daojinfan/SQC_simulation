"""Independent Stage 5.1 real-physics authority and preflight acceptance tests.

These tests intentionally refuse to synthesize an authority.  The numerical path
can only be accepted against a committed, independently approved authority corpus.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pytest

from sqvm.evolution.stage51_models import (
    Stage51EvolutionError, Stage51EvolutionInput, Stage51FailureCode,
    Stage51PhysicsContext,
)
from sqvm.evolution.stage51_physics import run_stage51_physics_preflight


ROOT = Path(__file__).resolve().parents[1]


def _json_objects(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    for path in root.rglob("*.json"):
        if ".git" in path.parts or path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append((path, value))
    return rows


def _only(rows: list[Path], label: str) -> Path:
    assert len(rows) == 1, f"Stage 5.1 production {label} must be committed exactly once; found: {rows}"
    return rows[0]


def _by_raw_sha256(root: Path, digest: str, label: str) -> Path:
    matches = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and hashlib.sha256(path.read_bytes()).hexdigest().upper() == digest
    ]
    return _only(matches, label)


def _production_paths(root: Path) -> Mapping[str, Path]:
    authority_root = root / "configs/evolution/stage51"
    json_rows = _json_objects(authority_root)
    authority_path = _only(
        [path for path, value in json_rows if value.get("artifact_type") == "stage_05_1_physics_authority"],
        "physics authority",
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority_relative = authority_path.relative_to(root).as_posix()
    approval_path = _only(
        [
            path
            for path, value in json_rows
            if value.get("artifact_type") == "stage_05_1_physics_approval"
            and value.get("physics_authority_path") == authority_relative
        ],
        "physics approval",
    )
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    return MappingProxyType(
        {
            "authority": authority_path,
            "approval": approval_path,
            "device": root / authority["device"]["path"],
            "hamiltonian": root / authority["hamiltonian"]["path"],
            "design": root / approval["design_path"],
            "solver_review": root / approval["review_record_path"],
            "source_snapshot": _by_raw_sha256(authority_root, authority["source_snapshot_sha256"], "source snapshot"),
            "environment_snapshot": _by_raw_sha256(authority_root, authority["environment_snapshot_sha256"], "environment snapshot"),
            "publication_policy": _by_raw_sha256(authority_root, authority["publication_policy_sha256"], "publication policy"),
            "stage5_design": root / "docs/designs/05_qutip_evolution_design.md",
            "stage5_amendment": root / "docs/decisions/2026-07-14-stage5-v0-2-amendment.md",
        }
    )


def _context(root: Path, paths: Mapping[str, Path]) -> Stage51PhysicsContext:
    return Stage51PhysicsContext(
        repository_root=root,
        output_root=root / "output",
        stage5_1_design_authority=paths["design"],
        stage5_1_approval_authority=paths["approval"],
        accepted_stage5_physics_authority=paths["authority"],
        accepted_device_artifact=paths["device"],
        accepted_hamiltonian_artifact=paths["hamiltonian"],
        solver_validation_approval=paths["solver_review"],
        source_snapshot=paths["source_snapshot"],
        environment_snapshot=paths["environment_snapshot"],
        publication_policy=paths["publication_policy"],
    )


def _readonly(values: Any, dtype: str) -> np.ndarray:
    result = np.asarray(values, dtype=dtype).copy(order="C")
    result.setflags(write=False)
    return result


def _input(*, flux_order: tuple[str, str, str] = ("q1", "c", "q2"), frame: Mapping[str, float] | None = None, epsilon_q1: np.ndarray | None = None, flux_q1: np.ndarray | None = None) -> Stage51EvolutionInput:
    centers = _readonly((-0.25, 0.25, 0.75), "<f8")
    q1_flux = _readonly((0.10, 0.10, 0.10), "<f8") if flux_q1 is None else flux_q1
    flux = {
        "q1": q1_flux,
        "c": _readonly((0.27, 0.27, 0.27), "<f8"),
        "q2": _readonly((0.00, 0.00, 0.00), "<f8"),
    }
    return Stage51EvolutionInput(
        control_id="independent-stage51-physics-preflight",
        control_binding=MappingProxyType(
            {
                "control_id": "independent-stage51-physics-preflight",
                "manifest_sha256": "0" * 64,
                "receipt_sha256": "1" * 64,
                "inventory_sha256": "2" * 64,
                "effective_control_sha256": "3" * 64,
            }
        ),
        time_center_ns=centers,
        epsilon_q1=_readonly((0.0j, 0.0j, 0.0j), "<c16") if epsilon_q1 is None else epsilon_q1,
        epsilon_q2=_readonly((0.0j, 0.0j, 0.0j), "<c16"),
        absolute_flux_phi0=MappingProxyType({name: flux[name] for name in flux_order}),
        frame_reference_frequency_GHz=MappingProxyType(dict(frame or {"q1": 5.10, "q2": 5.30})),
        checks=(MappingProxyType({"name": "independent_input_complete", "passed": True}),),
    )


@pytest.fixture
def production_context() -> Stage51PhysicsContext:
    return _context(ROOT, _production_paths(ROOT))


def _assert_code(expected: Stage51FailureCode, callback: Any) -> None:
    with pytest.raises(Stage51EvolutionError) as raised:
        callback()
    assert raised.value.code is expected


def test_committed_production_authority_preflight_has_named_projector_evidence(production_context):
    result = run_stage51_physics_preflight(_input(), production_context)
    assert set(result) == {"projector_sha256", "projector_checks"}
    assert set(result["projector_sha256"]) == {"000", "100", "001", "101"}
    assert all(len(value) == 64 and value == value.upper() for value in result["projector_sha256"].values())
    assert len(result["projector_checks"]) == 10
    assert all(row["passed"] is True for row in result["projector_checks"])


def test_q1_c_q2_mapping_is_name_based_not_mapping_order(production_context):
    canonical = run_stage51_physics_preflight(_input(), production_context)
    reordered = run_stage51_physics_preflight(_input(flux_order=("c", "q2", "q1")), production_context)
    assert reordered == canonical


def test_preflight_rejects_invalid_frame_schema_and_nonfinite_control_values(production_context):
    _assert_code(
        Stage51FailureCode.FRAME_AUTHORITY_MISMATCH,
        lambda: run_stage51_physics_preflight(_input(frame={"q1": 5.30, "readout": 7.10}), production_context),
    )
    _assert_code(
        Stage51FailureCode.FRAME_AUTHORITY_MISMATCH,
        lambda: run_stage51_physics_preflight(_input(frame={"q1": float("nan"), "q2": 5.30}), production_context),
    )
    _assert_code(
        Stage51FailureCode.CONTROL_ARRAY_INVALID,
        lambda: run_stage51_physics_preflight(_input(epsilon_q1=_readonly((0.0j, complex(float("nan"), 0.0), 0.0j), "<c16")), production_context),
    )
    _assert_code(
        Stage51FailureCode.CONTROL_ARRAY_INVALID,
        lambda: run_stage51_physics_preflight(_input(flux_q1=_readonly((0.10, float("inf"), 0.10), "<f8")), production_context),
    )


def _copied_context(tmp_path: Path) -> Stage51PhysicsContext:
    paths = _production_paths(ROOT)
    copied_root = tmp_path / "authority-copy"
    copied: dict[str, Path] = {}
    for name, source in paths.items():
        destination = copied_root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())
        copied[name] = destination
    return _context(copied_root, MappingProxyType(copied))


def test_single_byte_physics_authority_tamper_fails_before_real_preflight(tmp_path):
    context = _copied_context(tmp_path)
    raw = bytearray(context.accepted_stage5_physics_authority.read_bytes())
    raw[0] = ord("[")
    context.accepted_stage5_physics_authority.write_bytes(raw)
    _assert_code(
        Stage51FailureCode.PHYSICS_AUTHORITY_INVALID,
        lambda: run_stage51_physics_preflight(_input(), context),
    )
