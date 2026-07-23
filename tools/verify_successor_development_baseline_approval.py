#!/usr/bin/env python3
"""Fail-closed approval gate for the hosted successor development fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
APPROVAL = Path("docs/decisions/2026-07-23-successor-development-baseline-approval.json")
FIXTURE_ID = "successor_rebaseline_authority_v1"
FIXTURE = Path("tests/fixtures") / FIXTURE_ID
GENERATOR = Path("tests/tools/generate_successor_rebaseline_fixture.py")
REQUIRED_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "fixture_id",
    "fixture_aggregate_sha256", "fixture_generator_raw_sha256",
    "fixture_provenance_raw_sha256", "source_closure_sha256",
    "authorization_record_path", "authorization_record_raw_sha256", "reviewer_role",
    "decision", "historical_replay_claimed", "physical_execution_claimed",
    "production_selector_activation",
}


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789ABCDEF" for char in value)


def _safe_path(root: Path, relative: object, *, require_file: bool = True) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("path is invalid")
    path = Path(relative)
    if path.is_absolute() or "\\" in relative or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path is unsafe")
    root = root.resolve(strict=True)
    current = root
    for part in path.parts:
        current = current / part
        try:
            stat = current.lstat()
        except OSError as exc:
            raise ValueError(f"path is unavailable: {relative}") from exc
        if current.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400):
            raise ValueError(f"path is linked: {relative}")
    if require_file and not current.is_file():
        raise ValueError(f"path is not a regular file: {relative}")
    if require_file and current.stat().st_nlink > 1:
        raise ValueError(f"path is hardlinked: {relative}")
    return current


def _read_canonical_json(path: Path, label: str) -> dict[str, Any]:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{label} has duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(token: str) -> None:
        raise ValueError(f"{label} has non-finite JSON value: {token}")

    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(payload, dict) or raw != _canonical_bytes(payload):
        raise ValueError(f"{label} is not canonical JSON")
    return payload


def verify(root: Path = ROOT, approval_path: Path = APPROVAL) -> list[str]:
    """Return findings instead of accepting a partial development-baseline approval."""

    try:
        root = root.resolve(strict=True)
        relative_approval = approval_path.relative_to(root).as_posix() if approval_path.is_absolute() else approval_path.as_posix()
        approval_file = _safe_path(root, relative_approval)
        approval = _read_canonical_json(approval_file, "development baseline approval")
        if set(approval) != REQUIRED_KEYS or approval.get("schema_version") != "0.1" or approval.get("artifact_type") != "successor_development_baseline_approval" or approval.get("artifact_version") != "1":
            raise ValueError("development baseline approval schema is invalid")
        if approval.get("fixture_id") != FIXTURE_ID or approval.get("reviewer_role") != "independent_authority_reviewer" or approval.get("decision") != "approved_for_ci_development_baseline":
            raise ValueError("development baseline approval identity is invalid")
        if approval.get("historical_replay_claimed") is not False or approval.get("physical_execution_claimed") is not False or approval.get("production_selector_activation") is not False:
            raise ValueError("development baseline approval makes a prohibited claim")
        for key in (
            "fixture_aggregate_sha256", "fixture_generator_raw_sha256",
            "fixture_provenance_raw_sha256", "source_closure_sha256",
            "authorization_record_raw_sha256",
        ):
            if not _is_sha256(approval.get(key)):
                raise ValueError(f"development baseline approval {key} is invalid")
        fixture_root = _safe_path(root, FIXTURE.as_posix(), require_file=False)
        generator = _safe_path(root, GENERATOR.as_posix())
        authorization = _safe_path(root, approval.get("authorization_record_path"))
        if approval["authorization_record_path"] != "docs/decisions/2026-07-23-successor-rebaseline-authority.md" or _sha256(authorization.read_bytes()) != approval["authorization_record_raw_sha256"]:
            raise ValueError("development baseline approval authorization binding is invalid")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tests.support.fixture_loader import verify_fixture_manifest
        from tests.tools.generate_successor_rebaseline_fixture import validate_successor_fixture

        manifest = verify_fixture_manifest(fixture_root)
        chain = validate_successor_fixture(fixture_root, root)
        if manifest["fixture_id"] != approval["fixture_id"] or manifest["aggregate_sha256"] != approval["fixture_aggregate_sha256"] or manifest["generator_raw_sha256"] != approval["fixture_generator_raw_sha256"]:
            raise ValueError("development baseline approval fixture binding is invalid")
        if _sha256((fixture_root / "provenance.json").read_bytes()) != approval["fixture_provenance_raw_sha256"] or _sha256(generator.read_bytes()) != approval["fixture_generator_raw_sha256"]:
            raise ValueError("development baseline approval provenance binding is invalid")
        source = _read_canonical_json(fixture_root / "provisional_source_provenance.json", "fixture source provenance")
        if source.get("source_closure_sha256") != approval["source_closure_sha256"] or chain.get("status") != "pending_production_selector":
            raise ValueError("development baseline approval source closure is invalid")
    except (OSError, ValueError, KeyError) as exc:
        return [str(exc)]
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--approval", type=Path, default=APPROVAL)
    args = parser.parse_args()
    errors = verify(args.repository_root, args.approval)
    if errors:
        raise SystemExit("successor development baseline approval failed:\n" + "\n".join(f"- {error}" for error in errors))


if __name__ == "__main__":
    main()
