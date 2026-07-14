"""Typed contracts for the Stage 5 v0.2 reconstruction and evolution path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np


class FormalScaleQualificationRequired(ValueError):
    """Raised when v0.2 is asked to execute an unqualified formal-scale run."""


@dataclass(frozen=True, slots=True)
class Stage5Config:
    source_path: Path
    profile: str
    inputs: Mapping[str, str]
    scenario_ids: tuple[str, ...]
    charge_cutoffs: tuple[int, int, int]
    convergence_charge_cutoffs: tuple[int, int, int] | None
    smoke_window_start_index: int | None
    smoke_sample_count: int | None
    reference_state_count: int
    solver: Mapping[str, Any]
    tolerances: Mapping[str, float]
    publication: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Stage5PathAdmission:
    repository_root: Path
    config: Stage5Config
    resolved_inputs: Mapping[str, Path]


@dataclass(frozen=True, slots=True)
class EffectiveScenario:
    """The entire public control capability exposed to Stage 5."""

    scenario_id: str
    time_center_ns: np.ndarray
    xy_iq_GHz: Mapping[str, tuple[np.ndarray, np.ndarray]]
    absolute_flux_phi0: Mapping[str, np.ndarray]
    carrier_frequency_GHz: Mapping[str, float]
    carrier_phase_rad: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class RebuiltModel:
    device_artifacts: Any
    hamiltonian_config: Any
    ec_matrix_GHz: tuple[tuple[float, ...], ...]


@dataclass(frozen=True, slots=True)
class Stage5Input:
    admission: Stage5PathAdmission
    snapshot: Mapping[str, Any]
    snapshot_sha256: str
    scenarios: Mapping[str, EffectiveScenario]
    rebuilt_model: RebuiltModel


@dataclass(frozen=True, slots=True)
class Stage5ScenarioResult:
    scenario_id: str
    calculation: str
    edge_time_ns: np.ndarray
    states: tuple[np.ndarray, ...]
    populations: Mapping[str, np.ndarray]
    leakage: np.ndarray
    norm_error: np.ndarray
    original_sample_count: int
    window_start_index: int
    window_sample_count: int
    checks: tuple[Mapping[str, Any], ...]
    initial_reference: Mapping[str, Any]
    control_digest: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Stage5ArtifactSet:
    output_dir: Path
    artifact_path: Path
    report_path: Path
    receipt_path: Path
    artifact_sha256: str
    report_sha256: str
    receipt_sha256: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_dir": self.output_dir.as_posix(),
            "artifact_path": self.artifact_path.as_posix(),
            "report_path": self.report_path.as_posix(),
            "receipt_path": self.receipt_path.as_posix(),
            "artifact_sha256": self.artifact_sha256,
            "report_sha256": self.report_sha256,
            "receipt_sha256": self.receipt_sha256,
            "status": self.status,
        }
