from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from sqvm.storage.errors import StoragePolicyError, StorageValidationError
from sqvm.storage.policy import (
    POLICY_ARTIFACT_TYPE,
    POLICY_SCHEMA_VERSION,
    canonical_policy_sha256,
    load_storage_policy,
    parse_storage_policy,
)


ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "configs" / "runtime" / "experiment_storage_policy_v1.json"


def _payload() -> dict[str, object]:
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "artifact_type": POLICY_ARTIFACT_TYPE,
        "artifact_version": "0.1",
        "policy_id": "experiment_storage_policy_v1",
        "enabled": True,
        "compact_after_hours": 1,
        "simulation_retention_days": 30,
        "hardware_retention_days": 90,
        "failed_retention_days": 7,
        "trash_retention_days": 7,
        "simulation_keep_latest": 20,
        "hardware_keep_latest": 50,
        "minimum_free_disk_GiB": 10.0,
        "maximum_store_GiB": 20.0,
        "high_watermark_ratio": 0.8,
        "critical_watermark_ratio": 0.9,
        "estimate_safety_factor": 1.5,
        "stale_staging_hours": 24,
        "cleanup_plan_ttl_minutes": 10,
        "archive_compression": "stored",
        "updated_utc": "2026-07-21T00:00:00Z",
        "actor_id": "project.manager",
        "content_sha256": "0" * 64,
    }


def _valid_payload() -> dict[str, object]:
    payload = _payload()
    payload["content_sha256"] = canonical_policy_sha256(payload)
    return payload


def test_golden_policy_file_is_strict_and_hash_bound() -> None:
    policy = load_storage_policy(POLICY_PATH)

    assert policy.policy_id == "experiment_storage_policy_v1"
    assert policy.archive_compression == "stored"
    assert policy.content_sha256 == canonical_policy_sha256(policy.to_dict())
    assert policy.minimum_free_disk_bytes == 10 * 1024**3
    assert policy.maximum_store_bytes == 20 * 1024**3


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "0.2"),
        ("artifact_type", "wrong"),
        ("artifact_version", "0.2"),
        ("policy_id", "bad id"),
        ("enabled", 1),
        ("compact_after_hours", -1),
        ("simulation_retention_days", 0),
        ("hardware_retention_days", None),
        ("failed_retention_days", "7"),
        ("trash_retention_days", True),
        ("simulation_keep_latest", -1),
        ("hardware_keep_latest", 1.5),
        ("minimum_free_disk_GiB", 0.0),
        ("maximum_store_GiB", float("inf")),
        ("high_watermark_ratio", 0.0),
        ("critical_watermark_ratio", 1.0),
        ("estimate_safety_factor", 0.99),
        ("stale_staging_hours", -1),
        ("cleanup_plan_ttl_minutes", 0),
        ("archive_compression", "deflated"),
        ("updated_utc", "2026-07-21"),
        ("actor_id", ""),
        ("content_sha256", "a" * 64),
    ],
)
def test_policy_rejects_invalid_field_values(field: str, value: object) -> None:
    payload = _valid_payload()
    payload[field] = value

    with pytest.raises(StorageValidationError):
        parse_storage_policy(payload)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "null", "cross_field", "hash"])
def test_policy_rejects_schema_and_hash_violations(mutation: str) -> None:
    payload = _valid_payload()
    if mutation == "missing":
        del payload["enabled"]
    elif mutation == "unknown":
        payload["unexpected"] = "no"
    elif mutation == "null":
        payload["enabled"] = None
    elif mutation == "cross_field":
        payload["high_watermark_ratio"] = 0.9
        payload["critical_watermark_ratio"] = 0.8
        payload["content_sha256"] = canonical_policy_sha256(payload)
    else:
        payload["content_sha256"] = "F" * 64

    with pytest.raises(StorageValidationError):
        parse_storage_policy(payload)


def test_policy_hash_excludes_only_content_hash_and_is_canonical() -> None:
    first = _valid_payload()
    reordered = dict(reversed(list(first.items())))

    assert canonical_policy_sha256(first) == canonical_policy_sha256(reordered)
    assert canonical_policy_sha256(first) == first["content_sha256"]


def test_policy_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    payload = _valid_payload()
    raw = json.dumps(payload)
    duplicate = raw.removesuffix("}") + ',"enabled":false}'
    path = tmp_path / "policy.json"
    path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(StorageValidationError, match="duplicate JSON key"):
        load_storage_policy(path)


def test_policy_loader_rejects_non_object_and_non_finite_json(tmp_path: Path) -> None:
    for name, raw in (("array", "[]"), ("nan", '{"value":NaN}')):
        path = tmp_path / f"{name}.json"
        path.write_text(raw, encoding="utf-8")
        with pytest.raises(StorageValidationError):
            load_storage_policy(path)


def test_policy_model_is_immutable_and_round_trips() -> None:
    payload = _valid_payload()
    policy = parse_storage_policy(copy.deepcopy(payload))

    with pytest.raises(Exception):
        policy.enabled = False  # type: ignore[misc]
    assert policy.to_dict() == payload


def test_policy_loader_fails_closed_for_io_encoding_and_json_syntax(tmp_path: Path) -> None:
    missing = tmp_path / "missing-policy.json"
    with pytest.raises(StoragePolicyError, match="cannot read"):
        load_storage_policy(missing)

    invalid_utf8 = tmp_path / "invalid-utf8.json"
    invalid_utf8.write_bytes(b"\xff\xfe")
    with pytest.raises(StoragePolicyError, match="cannot read"):
        load_storage_policy(invalid_utf8)

    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"enabled":', encoding="utf-8")
    with pytest.raises(StoragePolicyError, match="JSON is invalid"):
        load_storage_policy(malformed)


@pytest.mark.parametrize("nested", [{"nested": [float("nan")]}, {"nested": {"value": float("inf")}}, {"nested": object()}])
def test_policy_rejects_nested_nonfinite_and_unsupported_values(nested: object) -> None:
    payload = _valid_payload()
    payload["enabled"] = nested
    with pytest.raises(StorageValidationError, match="non-finite|unsupported"):
        parse_storage_policy(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compact_after_hours", 87_601),
        ("simulation_keep_latest", 1_000_001),
        ("minimum_free_disk_GiB", 1_000_000.1),
        ("estimate_safety_factor", 10.1),
        ("updated_utc", "2026-02-30T00:00:00Z"),
    ],
)
def test_policy_rejects_upper_bounds_and_invalid_calendar_utc(field: str, value: object) -> None:
    payload = _valid_payload()
    payload[field] = value
    payload["content_sha256"] = canonical_policy_sha256(payload)
    with pytest.raises(StorageValidationError):
        parse_storage_policy(payload)
