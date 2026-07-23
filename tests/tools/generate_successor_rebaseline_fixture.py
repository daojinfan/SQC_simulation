"""Generate the non-physical successor authority closure for unavailable history.

This tool deliberately does not produce Stage 3, 3.1, 4, or 4.0 execution
evidence.  It records the old authority as unavailable and captures only the
current source-input closure that must be re-approved after a production
authority selector exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support.fixture_loader import _aggregate, verify_fixture_manifest
from sqvm.control.upstream import FROZEN_RECEIPT


FIXTURE_ID = "successor_rebaseline_authority_v1"
FIXED_CLOCK_UTC = "2026-07-23T00:00:00.000000Z"
SUCCESSOR_AUTHORITY_ID = "development_successor_rebaseline_v2"
SOURCE_PATHS = (
    "pyproject.toml",
    "configs/control/2q1c2r_channels.yaml",
    "configs/devices/2q1c2r.yaml",
    "configs/spectra/2q1c_static.yaml",
    "src/sqvm/control/artifacts.py",
    "src/sqvm/control/compatibility.py",
    "src/sqvm/control/stage4_provenance.py",
    "src/sqvm/control/stage4_verify.py",
    "src/sqvm/control/upstream.py",
    "src/sqvm/hamiltonian/provenance.py",
    "src/sqvm/hamiltonian/rebaseline.py",
    "src/sqvm/spectrum/provenance.py",
)
STAGES = ("stage_02_1", "stage_03", "stage_03_1", "stage_04", "stage_04_0")


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _load_canonical_mapping(path: Path, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} has a duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} has a non-finite JSON value: {value}")

    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} cannot be read: {exc}") from exc
    if not isinstance(payload, dict) or raw != _canonical_bytes(payload):
        raise ValueError(f"{label} is not canonical JSON")
    return payload


def _source_provenance(repository_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    aggregate_rows: list[tuple[str, int, str]] = []
    for relative in SOURCE_PATHS:
        path = repository_root / relative
        raw = path.read_bytes()
        digest = _sha256(raw)
        rows.append({"path": relative, "byte_length": len(raw), "raw_sha256": digest})
        aggregate_rows.append((relative, len(raw), digest))
    return {
        "schema_version": "0.1",
        "artifact_type": "successor_rebaseline_provisional_source_provenance",
        "artifact_version": "1",
        "authority_id": SUCCESSOR_AUTHORITY_ID,
        "status": "provisional_pre_selector",
        "historical_replay_claimed": False,
        "physical_execution_claimed": False,
        "source_files": rows,
        "source_closure_sha256": _aggregate(aggregate_rows),
    }


def _historical_disposition() -> dict[str, Any]:
    ledger = _frozen_expectation_ledger()
    return {
        "schema_version": "0.1",
        "artifact_type": "successor_rebaseline_historical_authority_disposition",
        "artifact_version": "1",
        "successor_authority_id": SUCCESSOR_AUTHORITY_ID,
        "disposition": "unavailable_replaced_by_successor",
        "historical_replay_claimed": False,
        "frozen_expectation_ledger": ledger,
        "stages": [
            {
                "stage": stage,
                "old_authority_status": "unavailable",
                "known_frozen_expectation_keys": [
                    row["key"] for row in ledger if _belongs_to_stage(row["key"], stage)
                ],
                "replacement_authority_id": SUCCESSOR_AUTHORITY_ID,
                "replacement_status": "pending_production_selector",
            }
            for stage in STAGES
        ],
    }


def _frozen_expectation_ledger() -> list[dict[str, str]]:
    """Expose expectations as expectations, never as recovered artifact bytes."""

    return [
        {"key": key, "path": path, "expected_raw_sha256": expected_raw_sha256}
        for key, path, expected_raw_sha256 in FROZEN_RECEIPT
    ]


def _belongs_to_stage(key: str, stage: str) -> bool:
    if stage == "stage_02_1":
        return key.startswith("stage2_1_")
    if stage == "stage_03":
        return key.startswith("stage3_") and not key.startswith("stage3_1_")
    if stage == "stage_03_1":
        return key.startswith("stage3_1_")
    if stage == "stage_04":
        return key.startswith("stage4_") and not key.startswith("stage4_0_")
    if stage == "stage_04_0":
        return key.startswith("stage4_0_")
    raise ValueError(f"unknown successor stage: {stage}")


def _authority_chain(disposition_raw: bytes, provenance_raw: bytes) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "artifact_type": "successor_rebaseline_authority_chain",
        "artifact_version": "1",
        "authority_id": SUCCESSOR_AUTHORITY_ID,
        "status": "pending_production_selector",
        "scope": "canonical_provenance_closure_only",
        "historical_replay_claimed": False,
        "physical_execution_claimed": False,
        "historical_authority_disposition_sha256": _sha256(disposition_raw),
        "provisional_source_provenance_sha256": _sha256(provenance_raw),
        "required_production_selector_contract": {
            "legacy_authority_remains_selectable": True,
            "successor_requires_explicit_selector": True,
            "legacy_and_successor_must_not_mix": True,
            "selector_must_bind_final_source_identity": True,
            "selector_must_require_stage2_1_through_stage4_0_approvals": True,
        },
        "stages": [
            {
                "stage": stage,
                "successor_evidence_status": "not_generated",
                "physical_execution_claimed": False,
                "blocked_by": ["production_authority_selector_v2", "independent_successor_approval"],
            }
            for stage in STAGES
        ],
    }


def _write(path: Path, payload: dict[str, Any]) -> bytes:
    raw = _canonical_bytes(payload)
    path.write_bytes(raw)
    return raw


def _write_manifest(target: Path) -> None:
    rows: list[dict[str, Any]] = []
    for path in sorted((row for row in target.rglob("*") if row.is_file() and row.name != "provenance.json"), key=lambda row: row.relative_to(target).as_posix().encode("utf-8")):
        raw = path.read_bytes()
        rows.append({"path": path.relative_to(target).as_posix(), "byte_length": len(raw), "raw_sha256": _sha256(raw)})
    aggregate_rows = [(row["path"], row["byte_length"], row["raw_sha256"]) for row in rows]
    _write(target / "provenance.json", {
        "schema_version": "0.1",
        "fixture_id": FIXTURE_ID,
        "generator_version": "0.1",
        "generator_raw_sha256": _sha256(Path(__file__).read_bytes()),
        "source_authority": "successor_rebaseline_provisional_closure_not_historical_replay",
        "fixed_clock_utc": FIXED_CLOCK_UTC,
        "files": rows,
        "aggregate_sha256": _aggregate(aggregate_rows),
    })


def validate_successor_fixture(fixture_root: Path, repository_root: Path | None = None) -> dict[str, Any]:
    fixture_root = fixture_root.resolve()
    manifest = verify_fixture_manifest(fixture_root)
    if manifest["generator_raw_sha256"] != _sha256(Path(__file__).read_bytes()):
        raise ValueError("successor fixture generator bytes do not match provenance")
    expected_files = {
        "authority_chain.json",
        "historical_authority_disposition.json",
        "provisional_source_provenance.json",
        "provenance.json",
    }
    actual_files = {path.relative_to(fixture_root).as_posix() for path in fixture_root.rglob("*") if path.is_file()}
    if actual_files != expected_files:
        raise ValueError("successor fixture file closure is not exact")
    disposition_path = fixture_root / "historical_authority_disposition.json"
    provenance_path = fixture_root / "provisional_source_provenance.json"
    chain = _load_canonical_mapping(fixture_root / "authority_chain.json", "authority chain")
    disposition = _load_canonical_mapping(disposition_path, "historical disposition")
    source = _load_canonical_mapping(provenance_path, "provisional source provenance")
    if set(chain) != {
        "schema_version", "artifact_type", "artifact_version", "authority_id", "status", "scope",
        "historical_replay_claimed", "physical_execution_claimed", "historical_authority_disposition_sha256",
        "provisional_source_provenance_sha256", "required_production_selector_contract", "stages",
    }:
        raise ValueError("successor authority chain fields are not exact")
    if chain["schema_version"] != "0.1" or chain["artifact_type"] != "successor_rebaseline_authority_chain" or chain["artifact_version"] != "1":
        raise ValueError("successor authority chain identity is invalid")
    if chain["authority_id"] != SUCCESSOR_AUTHORITY_ID or chain["status"] != "pending_production_selector" or chain["scope"] != "canonical_provenance_closure_only":
        raise ValueError("successor authority chain status is invalid")
    if chain["historical_replay_claimed"] is not False or chain["physical_execution_claimed"] is not False:
        raise ValueError("successor authority chain makes a prohibited evidence claim")
    if chain["required_production_selector_contract"] != {
        "legacy_authority_remains_selectable": True,
        "successor_requires_explicit_selector": True,
        "legacy_and_successor_must_not_mix": True,
        "selector_must_bind_final_source_identity": True,
        "selector_must_require_stage2_1_through_stage4_0_approvals": True,
    }:
        raise ValueError("successor authority selector contract is invalid")
    if chain["historical_authority_disposition_sha256"] != _sha256(disposition_path.read_bytes()) or chain["provisional_source_provenance_sha256"] != _sha256(provenance_path.read_bytes()):
        raise ValueError("successor authority chain hash binding is invalid")
    if set(disposition) != {
        "schema_version", "artifact_type", "artifact_version", "successor_authority_id", "disposition",
        "historical_replay_claimed", "frozen_expectation_ledger", "stages",
    } or disposition["schema_version"] != "0.1" or disposition["artifact_type"] != "successor_rebaseline_historical_authority_disposition" or disposition["artifact_version"] != "1" or disposition["successor_authority_id"] != SUCCESSOR_AUTHORITY_ID:
        raise ValueError("historical authority disposition identity is invalid")
    if disposition.get("disposition") != "unavailable_replaced_by_successor" or disposition.get("historical_replay_claimed") is not False:
        raise ValueError("historical authority disposition is invalid")
    ledger = disposition.get("frozen_expectation_ledger")
    if ledger != _frozen_expectation_ledger():
        raise ValueError("historical frozen expectation ledger differs from FROZEN_RECEIPT")
    records = disposition.get("stages")
    if not isinstance(records, list) or [record.get("stage") for record in records if isinstance(record, dict)] != list(STAGES):
        raise ValueError("historical authority disposition stages are invalid")
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("historical authority disposition record is invalid")
        stage = record.get("stage")
        if record != {
            "stage": stage,
            "old_authority_status": "unavailable",
            "known_frozen_expectation_keys": [row["key"] for row in ledger if _belongs_to_stage(row["key"], stage)],
            "replacement_authority_id": SUCCESSOR_AUTHORITY_ID,
            "replacement_status": "pending_production_selector",
        }:
            raise ValueError("historical authority disposition record is invalid")
    if set(source) != {
        "schema_version", "artifact_type", "artifact_version", "authority_id", "status",
        "historical_replay_claimed", "physical_execution_claimed", "source_files", "source_closure_sha256",
    } or source["schema_version"] != "0.1" or source["artifact_type"] != "successor_rebaseline_provisional_source_provenance" or source["artifact_version"] != "1" or source["authority_id"] != SUCCESSOR_AUTHORITY_ID:
        raise ValueError("provisional source provenance identity is invalid")
    if source.get("status") != "provisional_pre_selector" or source.get("historical_replay_claimed") is not False or source.get("physical_execution_claimed") is not False:
        raise ValueError("provisional source provenance makes a prohibited evidence claim")
    rows = source.get("source_files")
    if not isinstance(rows, list) or [row.get("path") for row in rows if isinstance(row, dict)] != list(SOURCE_PATHS):
        raise ValueError("provisional source provenance paths are invalid")
    aggregate_rows: list[tuple[str, int, str]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "byte_length", "raw_sha256"}:
            raise ValueError("provisional source provenance row is invalid")
        path, length, digest = row["path"], row["byte_length"], row["raw_sha256"]
        if not isinstance(length, int) or isinstance(length, bool) or length < 0 or not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("provisional source provenance row values are invalid")
        aggregate_rows.append((path, length, digest))
    if source.get("source_closure_sha256") != _aggregate(aggregate_rows):
        raise ValueError("provisional source provenance aggregate is invalid")
    stages = chain.get("stages")
    if not isinstance(stages, list) or [row.get("stage") for row in stages if isinstance(row, dict)] != list(STAGES):
        raise ValueError("successor authority chain stage order is invalid")
    for row in stages:
        if not isinstance(row, dict) or row.get("successor_evidence_status") != "not_generated" or row.get("physical_execution_claimed") is not False or row.get("blocked_by") != ["production_authority_selector_v2", "independent_successor_approval"]:
            raise ValueError("successor authority chain stage claim is invalid")
    if repository_root is not None:
        current = _source_provenance(repository_root.resolve())
        if current != source:
            raise ValueError("current source closure differs from the provisional successor provenance")
    return chain


def generate(repository_root: Path, target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"fixture target already exists: {target}")
    if not repository_root.is_dir():
        raise FileNotFoundError(f"repository root is unavailable: {repository_root}")
    target.mkdir(parents=True)
    provenance_raw = _write(target / "provisional_source_provenance.json", _source_provenance(repository_root))
    disposition_raw = _write(target / "historical_authority_disposition.json", _historical_disposition())
    _write(target / "authority_chain.json", _authority_chain(disposition_raw, provenance_raw))
    _write_manifest(target)
    validate_successor_fixture(target, repository_root)


def refresh_manifest(target: Path) -> None:
    """Refresh only generator metadata after this generator changes.

    Payload files are never overwritten; callers still need an absent target
    for generation.  This narrowly supports the manifest's generator-byte
    binding without inventing new authority evidence.
    """

    target = target.resolve()
    expected_payloads = {
        "authority_chain.json",
        "historical_authority_disposition.json",
        "provisional_source_provenance.json",
        "provenance.json",
    }
    actual = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
    if actual != expected_payloads:
        raise ValueError("refreshed successor fixture file closure is not exact")
    _write_manifest(target)


def _all_file_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--verify-against", type=Path)
    parser.add_argument("--refresh-manifest", action="store_true")
    args = parser.parse_args(argv)
    if args.refresh_manifest:
        if args.verify_against is not None:
            raise ValueError("--refresh-manifest cannot be combined with --verify-against")
        refresh_manifest(args.target)
        return 0
    generate(args.repository_root.resolve(), args.target.resolve())
    if args.verify_against is not None:
        validate_successor_fixture(args.verify_against.resolve(), args.repository_root.resolve())
        if _all_file_bytes(args.target.resolve()) != _all_file_bytes(args.verify_against.resolve()):
            raise ValueError("generated successor fixture differs from --verify-against")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
