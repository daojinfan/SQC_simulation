"""Immutable Stage 4 control-signal models."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class ControlChainConfig:
    source_path: Path
    profile: str
    inputs: Mapping[str, str]
    sample_rate_Hz: int
    dt_ns: Decimal
    dac: Mapping[str, Any]
    lane_order: tuple[str, ...]
    lanes: Mapping[str, Mapping[str, Any]]
    static_mixing: Mapping[str, Mapping[str, Any]]
    idle_flux_phi0: Mapping[str, Decimal]
    acceptance: Mapping[str, Any]


@dataclass(frozen=True)
class LogicalPulse:
    pulse_id: str
    kind: str
    channel: str
    start_ns: Decimal
    duration_ns: Decimal
    values: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        row = {
            "pulse_id": self.pulse_id,
            "kind": self.kind,
            "channel": self.channel,
            "start_ns": float(self.start_ns),
            "duration_ns": float(self.duration_ns),
        }
        row.update(self.values)
        return row


@dataclass(frozen=True)
class LogicalScenario:
    scenario_id: str
    duration_ns: Decimal
    pulses: tuple[LogicalPulse, ...]


@dataclass(frozen=True)
class LogicalSchedule:
    source_path: Path
    schedule_id: str
    scenarios: tuple[LogicalScenario, ...]


@dataclass(frozen=True)
class ScheduleValidationReport:
    ok: bool
    conflicts: tuple[Mapping[str, Any], ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ControlBuildContext:
    repository_root: Path
    provenance: Mapping[str, Any]
    registry: Mapping[str, Any]
    stage3_artifact: Mapping[str, Any]
    analysis_started_at: float


@dataclass(frozen=True)
class ControlCompilationResult:
    profile: str
    acceptance_eligible: bool
    status: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class ControlArtifactSet:
    artifact_path: Path
    notebook_path: Path
    report_path: Path
    artifact_sha256: str
    notebook_sha256: str
    report_sha256: str


@dataclass(frozen=True)
class ControlRunReceipt:
    payload: Mapping[str, Any]

    @property
    def acceptance_candidate_ready(self) -> bool:
        return bool(self.payload.get("acceptance_candidate_ready"))

    @property
    def execution_succeeded(self) -> bool:
        return bool(self.payload.get("execution_succeeded"))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


@dataclass(frozen=True)
class Stage5ReadinessReport:
    ok: bool
    stage5_ready: bool
    approval_decision: str
    acceptance_approval_valid: bool
    bound_hashes: Mapping[str, str]
    checks: tuple[Mapping[str, Any], ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "stage5_ready": self.stage5_ready,
            "approval_decision": self.approval_decision,
            "acceptance_approval_valid": self.acceptance_approval_valid,
            "bound_hashes": dict(self.bound_hashes),
            "checks": list(self.checks),
            "blocking_reasons": list(self.blocking_reasons),
        }
