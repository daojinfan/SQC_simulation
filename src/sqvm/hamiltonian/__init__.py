"""Hamiltonian construction and verification interfaces."""

from sqvm.hamiltonian.artifacts import DeviceArtifacts, HamiltonianArtifactSet, load_device_artifacts
from sqvm.hamiltonian.basis import ChargeBasis
from sqvm.hamiltonian.builder import HamiltonianModel, build_hamiltonian
from sqvm.hamiltonian.capacitance import (
    EXPECTED_NODE_ORDER,
    MODE_ORDER,
    ECMatrix,
    ModeCapacitanceMatrix,
    ModeTransform,
    build_ec_matrix,
    build_mode_capacitance_matrix,
    build_mode_transform,
)
from sqvm.hamiltonian.config import BasisConfig, HamiltonianConfig, SolverConfig, load_hamiltonian_config
from sqvm.hamiltonian.junction import EffectiveJunction, resolve_effective_junctions
from sqvm.hamiltonian.provenance import (
    build_stage2_artifact_provenance,
    canonical_json_bytes,
    raw_file_sha256,
    stage2_model_source_tree_sha256,
)
from sqvm.hamiltonian.rebaseline import (
    RebaselineGateError,
    Stage2RebuildConsistencyReport,
    build_rebaseline_approval_payload,
    build_rebaseline_manifest_payload,
    rebuild_stage2_low_energy_spectrum,
    require_accepted_legacy_anchor,
    serialize_rebaseline_payload,
    validate_rebaseline_manifest,
)
from sqvm.hamiltonian.verify import HamiltonianVerificationReport, verify_hamiltonian

__all__ = [
    "BasisConfig",
    "ChargeBasis",
    "DeviceArtifacts",
    "ECMatrix",
    "EXPECTED_NODE_ORDER",
    "EffectiveJunction",
    "HamiltonianArtifactSet",
    "HamiltonianConfig",
    "HamiltonianModel",
    "HamiltonianVerificationReport",
    "MODE_ORDER",
    "ModeCapacitanceMatrix",
    "ModeTransform",
    "RebaselineGateError",
    "SolverConfig",
    "Stage2RebuildConsistencyReport",
    "build_rebaseline_approval_payload",
    "build_rebaseline_manifest_payload",
    "build_stage2_artifact_provenance",
    "canonical_json_bytes",
    "build_ec_matrix",
    "build_hamiltonian",
    "build_mode_capacitance_matrix",
    "build_mode_transform",
    "load_device_artifacts",
    "load_hamiltonian_config",
    "raw_file_sha256",
    "rebuild_stage2_low_energy_spectrum",
    "require_accepted_legacy_anchor",
    "resolve_effective_junctions",
    "serialize_rebaseline_payload",
    "stage2_model_source_tree_sha256",
    "validate_rebaseline_manifest",
    "verify_hamiltonian",
]
