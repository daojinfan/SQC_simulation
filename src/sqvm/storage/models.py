"""Immutable DTOs shared by read-only storage services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StoragePolicy:
    schema_version: str
    artifact_type: str
    artifact_version: str
    policy_id: str
    enabled: bool
    compact_after_hours: int
    simulation_retention_days: int
    hardware_retention_days: int
    failed_retention_days: int
    trash_retention_days: int
    simulation_keep_latest: int
    hardware_keep_latest: int
    minimum_free_disk_GiB: float
    maximum_store_GiB: float
    high_watermark_ratio: float
    critical_watermark_ratio: float
    estimate_safety_factor: float
    stale_staging_hours: int
    cleanup_plan_ttl_minutes: int
    archive_compression: str
    updated_utc: str
    actor_id: str
    content_sha256: str

    @property
    def minimum_free_disk_bytes(self) -> int:
        return int(self.minimum_free_disk_GiB * 1024**3)

    @property
    def maximum_store_bytes(self) -> int:
        return int(self.maximum_store_GiB * 1024**3)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "artifact_type": self.artifact_type,
            "artifact_version": self.artifact_version,
            "policy_id": self.policy_id,
            "enabled": self.enabled,
            "compact_after_hours": self.compact_after_hours,
            "simulation_retention_days": self.simulation_retention_days,
            "hardware_retention_days": self.hardware_retention_days,
            "failed_retention_days": self.failed_retention_days,
            "trash_retention_days": self.trash_retention_days,
            "simulation_keep_latest": self.simulation_keep_latest,
            "hardware_keep_latest": self.hardware_keep_latest,
            "minimum_free_disk_GiB": self.minimum_free_disk_GiB,
            "maximum_store_GiB": self.maximum_store_GiB,
            "high_watermark_ratio": self.high_watermark_ratio,
            "critical_watermark_ratio": self.critical_watermark_ratio,
            "estimate_safety_factor": self.estimate_safety_factor,
            "stale_staging_hours": self.stale_staging_hours,
            "cleanup_plan_ttl_minutes": self.cleanup_plan_ttl_minutes,
            "archive_compression": self.archive_compression,
            "updated_utc": self.updated_utc,
            "actor_id": self.actor_id,
            "content_sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class StorageFile:
    relative_path: str
    logical_bytes: int
    allocated_bytes: int
    allocated_estimated: bool


@dataclass(frozen=True)
class StorageInventory:
    root: str
    files: tuple[StorageFile, ...]
    logical_bytes: int
    allocated_bytes: int
    allocated_estimated: bool

    @property
    def file_count(self) -> int:
        return len(self.files)


@dataclass(frozen=True)
class VolumeUsage:
    root: str
    free_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class ArchiveEntry:
    path: str
    byte_length: int
    raw_sha256: str


@dataclass(frozen=True)
class ArchiveBundle:
    run_id: str
    workflow_id: str
    workflow_sha256: str
    receipt_sha256: str
    source_artifact_version: str
    original_directory_name: str
    entries: tuple[ArchiveEntry, ...]
    logical_bytes: int
    format_payload: Mapping[str, object]
    manifest_payload: Mapping[str, object]
    verification_report_payload: Mapping[str, object]
    receipt_payload: Mapping[str, object]
    source_verified: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "workflow_sha256": self.workflow_sha256,
            "receipt_sha256": self.receipt_sha256,
            "source_artifact_version": self.source_artifact_version,
            "original_directory_name": self.original_directory_name,
            "entries": [
                {
                    "path": entry.path,
                    "byte_length": entry.byte_length,
                    "raw_sha256": entry.raw_sha256,
                }
                for entry in self.entries
            ],
            "logical_bytes": self.logical_bytes,
            "format": dict(self.format_payload),
            "manifest": dict(self.manifest_payload),
            "verification_report": dict(self.verification_report_payload),
            "receipt": dict(self.receipt_payload),
            "source_verified": self.source_verified,
        }
