from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

import json
from pathlib import Path
import shutil

import pytest

from sqvm.evolution import production_stage51_physics_context
from sqvm.evolution.stage51_authority import admit_physics_authority
from sqvm.evolution.stage51_models import Stage51EvolutionError, Stage51FailureCode


_BOUND_FILES = (
    "configs/devices/2q1c2r.yaml",
    "configs/hamiltonians/2q1c_charge_basis.yaml",
    "configs/evolution/stage51/environment_snapshot_v1.json",
    "configs/evolution/stage51/physics_authority_v1.json",
    "configs/evolution/stage51/publication_policy_v1.json",
    "configs/evolution/stage51/solver_review_v1.json",
    "configs/evolution/stage51/source_snapshot_v1.json",
    "docs/decisions/2026-07-14-stage5-v0-2-amendment.md",
    "docs/designs/05_1_verified_control_evolution_design.md",
    "docs/designs/05_qutip_evolution_design.md",
)
_COPY_FILES = _BOUND_FILES + ("configs/evolution/stage51/physics_approval_v1.json",)


def _copy_production_chain(destination: Path) -> Path:
    source = Path(__file__).resolve().parents[1]
    source_snapshot = json.loads((source / "configs/evolution/stage51/source_snapshot_v1.json").read_text(encoding="utf-8"))
    for relative in (*_COPY_FILES, *(row["path"] for row in source_snapshot["sources"])):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    return destination


def test_production_context_is_real_and_admitted():
    context = production_stage51_physics_context()
    authority, binding = admit_physics_authority(context)

    assert authority["status"] == "approved"
    assert authority["authority_id"] == binding["physics_authority_id"]
    assert context.accepted_device_artifact.name == "2q1c2r.yaml"
    assert context.accepted_hamiltonian_artifact.name == "2q1c_charge_basis.yaml"


@pytest.mark.parametrize("relative", _BOUND_FILES)
def test_production_authority_rejects_any_bound_file_byte_drift(tmp_path: Path, relative: str):
    root = _copy_production_chain(tmp_path / "repository")
    target = root / relative
    target.write_bytes(target.read_bytes() + b"\n")

    with pytest.raises(Stage51EvolutionError) as error:
        production_stage51_physics_context(root)

    assert error.value.code is Stage51FailureCode.PHYSICS_AUTHORITY_INVALID


def test_production_authority_rejects_source_inventory_byte_drift(tmp_path: Path):
    root = _copy_production_chain(tmp_path / "repository")
    source = root / "src/sqvm/evolution/stage51_context.py"
    source.write_bytes(source.read_bytes() + b"\n")

    with pytest.raises(Stage51EvolutionError) as error:
        production_stage51_physics_context(root)

    assert error.value.code is Stage51FailureCode.PHYSICS_AUTHORITY_INVALID


def test_production_authority_rejects_approval_status_drift(tmp_path: Path):
    root = _copy_production_chain(tmp_path / "repository")
    approval = root / "configs/evolution/stage51/physics_approval_v1.json"
    approval.write_text(approval.read_text(encoding="utf-8").replace('"status": "approved"', '"status": "draft"'), encoding="utf-8")

    with pytest.raises(Stage51EvolutionError) as error:
        production_stage51_physics_context(root)

    assert error.value.code is Stage51FailureCode.PHYSICS_AUTHORITY_INVALID
