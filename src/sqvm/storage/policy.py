"""Strict, hash-bound experiment storage policy loading."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from sqvm.storage.errors import StoragePolicyError, StorageValidationError
from sqvm.storage.models import StoragePolicy


POLICY_SCHEMA_VERSION = "0.1"
POLICY_ARTIFACT_TYPE = "sqvm_experiment_storage_policy"
POLICY_ARTIFACT_VERSION = "0.1"
_POLICY_FIELDS = frozenset({
    "schema_version", "artifact_type", "artifact_version", "policy_id", "enabled",
    "compact_after_hours", "simulation_retention_days", "hardware_retention_days",
    "failed_retention_days", "trash_retention_days", "simulation_keep_latest",
    "hardware_keep_latest", "minimum_free_disk_GiB", "maximum_store_GiB",
    "high_watermark_ratio", "critical_watermark_ratio", "estimate_safety_factor",
    "stale_staging_hours", "cleanup_plan_ttl_minutes", "archive_compression",
    "updated_utc", "actor_id", "content_sha256",
})
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,95}$")
_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_SHA256 = re.compile(r"[A-F0-9]{64}$")
_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Return the canonical compact UTF-8/LF JSON representation."""

    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


def canonical_policy_sha256(payload: Mapping[str, Any]) -> str:
    """Hash the exact policy payload after excluding its self-referential digest."""

    value = dict(payload)
    value.pop("content_sha256", None)
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def load_storage_policy(path: str | Path) -> StoragePolicy:
    """Load a strict policy JSON object without accepting duplicate keys or NaN."""

    policy_path = Path(path)
    try:
        raw = policy_path.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise StoragePolicyError("cannot read storage policy") from exc
    return parse_storage_policy(_strict_json_object(raw))


def parse_storage_policy(payload: Mapping[str, Any]) -> StoragePolicy:
    """Validate the exact v1 storage-policy schema and its canonical SHA-256."""

    if not isinstance(payload, Mapping):
        raise StorageValidationError("storage policy must be an object")
    value = dict(payload)
    if set(value) != _POLICY_FIELDS:
        missing = sorted(_POLICY_FIELDS - set(value))
        unknown = sorted(set(value) - _POLICY_FIELDS)
        raise StorageValidationError(f"storage policy fields are not exact: missing={missing}, unknown={unknown}")
    _finite_tree(value)
    _exact(value, "schema_version", POLICY_SCHEMA_VERSION)
    _exact(value, "artifact_type", POLICY_ARTIFACT_TYPE)
    _exact(value, "artifact_version", POLICY_ARTIFACT_VERSION)
    _identifier(value["policy_id"], "policy_id", _IDENTIFIER)
    if type(value["enabled"]) is not bool:
        raise StorageValidationError("enabled must be a boolean")
    for name, minimum, maximum in (
        ("compact_after_hours", 0, 87_600),
        ("simulation_retention_days", 1, 36_500),
        ("hardware_retention_days", 1, 36_500),
        ("failed_retention_days", 1, 36_500),
        ("trash_retention_days", 1, 36_500),
        ("simulation_keep_latest", 0, 1_000_000),
        ("hardware_keep_latest", 0, 1_000_000),
        ("stale_staging_hours", 0, 87_600),
        ("cleanup_plan_ttl_minutes", 1, 10_080),
    ):
        _integer(value[name], name, minimum, maximum)
    for name in ("minimum_free_disk_GiB", "maximum_store_GiB"):
        _number(value[name], name, lower=0.0, upper=1_000_000.0, exclusive_lower=True)
    _number(value["high_watermark_ratio"], "high_watermark_ratio", lower=0.0, upper=1.0, exclusive_lower=True)
    _number(value["critical_watermark_ratio"], "critical_watermark_ratio", lower=0.0, upper=1.0, exclusive_lower=True)
    _number(value["estimate_safety_factor"], "estimate_safety_factor", lower=1.0, upper=10.0)
    if not value["high_watermark_ratio"] < value["critical_watermark_ratio"]:
        raise StorageValidationError("high_watermark_ratio must be lower than critical_watermark_ratio")
    _exact(value, "archive_compression", "stored")
    _utc(value["updated_utc"])
    _identifier(value["actor_id"], "actor_id", _ACTOR)
    if not isinstance(value["content_sha256"], str) or _SHA256.fullmatch(value["content_sha256"]) is None:
        raise StorageValidationError("content_sha256 must be an uppercase SHA-256")
    if value["content_sha256"] != canonical_policy_sha256(value):
        raise StorageValidationError("content_sha256 does not match canonical policy content")
    return StoragePolicy(**value)


def _strict_json_object(raw: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise StoragePolicyError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise StoragePolicyError(f"non-finite JSON value: {value}")

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    except (json.JSONDecodeError, RecursionError, StoragePolicyError) as exc:
        if isinstance(exc, StoragePolicyError):
            raise
        raise StoragePolicyError("storage policy JSON is invalid") from exc
    if not isinstance(value, dict):
        raise StoragePolicyError("storage policy JSON root must be an object")
    return value


def _finite_tree(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StorageValidationError("storage policy contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _finite_tree(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite_tree(item)
        return
    raise StorageValidationError("storage policy contains an unsupported value")


def _exact(value: Mapping[str, Any], name: str, expected: str) -> None:
    if value[name] != expected:
        raise StorageValidationError(f"{name} is invalid")


def _identifier(value: Any, name: str, pattern: re.Pattern[str]) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise StorageValidationError(f"{name} is invalid")


def _integer(value: Any, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise StorageValidationError(f"{name} is out of range")


def _number(value: Any, name: str, *, lower: float, upper: float, exclusive_lower: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise StorageValidationError(f"{name} must be a finite number")
    numeric = float(value)
    if numeric > upper or numeric < lower or (exclusive_lower and numeric == lower):
        raise StorageValidationError(f"{name} is out of range")


def _utc(value: Any) -> None:
    if not isinstance(value, str) or _UTC.fullmatch(value) is None:
        raise StorageValidationError("updated_utc must be UTC RFC3339 seconds")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise StorageValidationError("updated_utc must be UTC RFC3339 seconds") from exc
