"""The single production entry point for Stage 5.1 physics authorities."""

from __future__ import annotations

from pathlib import Path

from sqvm.evolution.stage51_authority import admit_physics_authority
from sqvm.evolution.stage51_models import Stage51PhysicsContext


_ARTIFACTS = {
    "stage5_1_design_authority": "docs/designs/05_1_verified_control_evolution_design.md",
    "stage5_1_approval_authority": "configs/evolution/stage51/physics_approval_v1.json",
    "accepted_stage5_physics_authority": "configs/evolution/stage51/physics_authority_v1.json",
    "accepted_device_artifact": "configs/devices/2q1c2r.yaml",
    "accepted_hamiltonian_artifact": "configs/hamiltonians/2q1c_charge_basis.yaml",
    "solver_validation_approval": "configs/evolution/stage51/solver_review_v1.json",
    "source_snapshot": "configs/evolution/stage51/source_snapshot_v1.json",
    "environment_snapshot": "configs/evolution/stage51/environment_snapshot_v1.json",
    "publication_policy": "configs/evolution/stage51/publication_policy_v1.json",
}


def production_stage51_physics_context(
    repository_root: Path | None = None,
    *,
    output_root: Path | None = None,
) -> Stage51PhysicsContext:
    """Return the admitted repository-owned Stage 5.1 production context.

    This deliberately has no legacy Stage 5 configuration argument.  The
    separately approved authority is the only numerical source admission path.
    """

    root = (repository_root or Path(__file__).resolve().parents[3]).resolve(strict=True)
    context = Stage51PhysicsContext(
        repository_root=root,
        output_root=(output_root or root / "output" / "stage_05_1").resolve(),
        **{name: root / relative for name, relative in _ARTIFACTS.items()},
    )
    admit_physics_authority(context)
    return context
