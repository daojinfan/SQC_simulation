"""Immutable Stage 4.0 control-channel gate models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ControlChannelSpec:
    name: str
    kind: str
    target: str
    port: str
    awg_lanes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "target": self.target,
            "port": self.port,
            "awg_lanes": list(self.awg_lanes),
        }


@dataclass(frozen=True, slots=True)
class ControlChannelRegistry:
    schema_version: str
    artifact_type: str
    artifact_version: str
    device_name: str
    base_device_config: Path
    base_device_artifact: Path
    channels: tuple[ControlChannelSpec, ...]
    source_path: Path

    def channel_map(self) -> dict[str, ControlChannelSpec]:
        return {channel.name: channel for channel in self.channels}


@dataclass(frozen=True, slots=True)
class ControlChannelCompatibilityReport:
    ok: bool
    compatibility_candidate_ready: bool
    base_channels: dict[str, dict[str, str]]
    added_channels: dict[str, dict[str, Any]]
    merged_channels: dict[str, dict[str, Any]]
    checks: tuple[dict[str, Any], ...]
    blocking_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ControlChannelReadinessReport:
    ok: bool
    control_channel_ready: bool
    approval_decision: str
    approval_valid: bool
    bound_hashes: dict[str, str]
    checks: tuple[dict[str, Any], ...]
    blocking_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "control_channel_ready": self.control_channel_ready,
            "approval_decision": self.approval_decision,
            "approval_valid": self.approval_valid,
            "bound_hashes": dict(self.bound_hashes),
            "checks": list(self.checks),
            "blocking_reasons": list(self.blocking_reasons),
        }
