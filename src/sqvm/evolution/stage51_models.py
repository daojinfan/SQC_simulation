"""Immutable public contracts for Stage 5.1 verified-control evolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

import numpy as np


class Stage51FailureCode(StrEnum):
    HANDLE_SCHEMA_INVALID = "HANDLE_SCHEMA_INVALID"
    HANDLE_NOT_PUBLISHED = "HANDLE_NOT_PUBLISHED"
    CONTROL_BINDING_MISMATCH = "CONTROL_BINDING_MISMATCH"
    CONTROL_ARRAY_INVALID = "CONTROL_ARRAY_INVALID"
    CONTROL_CLOCK_MISMATCH = "CONTROL_CLOCK_MISMATCH"
    FRAME_AUTHORITY_MISMATCH = "FRAME_AUTHORITY_MISMATCH"
    PHYSICS_AUTHORITY_INVALID = "PHYSICS_AUTHORITY_INVALID"
    TENSOR_MAPPING_INVALID = "TENSOR_MAPPING_INVALID"
    OPERATOR_CONSTRUCTION_FAILED = "OPERATOR_CONSTRUCTION_FAILED"
    COEFFICIENT_PLAN_INVALID = "COEFFICIENT_PLAN_INVALID"
    ANGULAR_CONVERSION_VIOLATION = "ANGULAR_CONVERSION_VIOLATION"
    INITIAL_STATE_INVALID = "INITIAL_STATE_INVALID"
    OBSERVABLE_SPEC_INVALID = "OBSERVABLE_SPEC_INVALID"
    SOLVER_AUTHORITY_INVALID = "SOLVER_AUTHORITY_INVALID"
    WORKER_ADMISSION_FAILED = "WORKER_ADMISSION_FAILED"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"
    WORKER_EXECUTION_FAILED = "WORKER_EXECUTION_FAILED"
    NUMERICAL_RESULT_INVALID = "NUMERICAL_RESULT_INVALID"
    PUBLICATION_CONFLICT = "PUBLICATION_CONFLICT"
    ARTIFACT_VERIFICATION_FAILED = "ARTIFACT_VERIFICATION_FAILED"


class Stage51EvolutionError(ValueError):
    def __init__(self, code: Stage51FailureCode, detail: str = "") -> None:
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else str(code))


@dataclass(frozen=True, slots=True)
class Stage51PhysicsContext:
    repository_root: Path
    output_root: Path
    stage5_1_design_authority: Path
    stage5_1_approval_authority: Path
    accepted_stage5_physics_authority: Path
    accepted_device_artifact: Path
    accepted_hamiltonian_artifact: Path
    solver_validation_approval: Path
    source_snapshot: Path
    environment_snapshot: Path
    publication_policy: Path


@dataclass(frozen=True, slots=True)
class Stage51EvolutionInput:
    control_id: str
    control_binding: Mapping[str, str]
    time_center_ns: np.ndarray
    epsilon_q1: np.ndarray
    epsilon_q2: np.ndarray
    absolute_flux_phi0: Mapping[str, np.ndarray]
    frame_reference_frequency_GHz: Mapping[str, float]
    checks: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class EvolutionCoefficientPlan:
    schema_version: str
    coefficient_plan_id: str
    control_binding: Mapping[str, str]
    physics_authority_binding: Mapping[str, str]
    clock: Mapping[str, float]
    frame_reference_frequency_GHz: Mapping[str, float]
    operator_inventory: Mapping[str, Any]
    coefficient_inventory: Mapping[str, Mapping[str, Any]]
    initial_state_spec: Mapping[str, Any]
    observable_spec: Mapping[str, Any]
    solver_spec: Mapping[str, Any]
    checks: tuple[Mapping[str, Any], ...]
    arrays: Mapping[str, np.ndarray]


@dataclass(frozen=True, slots=True)
class VerifiedCoefficientHandle:
    coefficient_plan_id: str
    artifact_root: Path
    manifest_sha256: str
    receipt_sha256: str
    inventory_sha256: str
    physics_authority_id: str


@dataclass(frozen=True, slots=True)
class Stage51EvolutionArtifactSet:
    artifact_root: Path
    result_id: str
    manifest_sha256: str
    receipt_sha256: str
    status: str


@dataclass(frozen=True, slots=True)
class VerifiedEvolutionHandle:
    result_id: str
    artifact_root: Path
    manifest_sha256: str
    receipt_sha256: str
    replay_fidelity: float


@dataclass(frozen=True, slots=True)
class Stage51NumericalResult:
    edge_time_ns: np.ndarray
    initial_state: np.ndarray
    final_state: np.ndarray
    populations: Mapping[str, np.ndarray]
    leakage: np.ndarray
    norm_error: np.ndarray
    projector_sha256: Mapping[str, str]
    diagnostics: Mapping[str, Any]
