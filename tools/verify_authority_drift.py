#!/usr/bin/env python3
"""Fail closed when WP2 changes a frozen Stage 2/5/6 authority input."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests" / "authority_baseline_v1.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _verify_runtime_v02_legacy_fixture(root: Path) -> None:
    """Verify the frozen v0.2 provenance without applying the Step1 schema."""
    fixture_root = root / "tests" / "fixtures" / "runtime_v02_legacy_v1"
    provenance_path = fixture_root / "provenance.json"
    if not fixture_root.is_dir() or fixture_root.is_symlink() or provenance_path.is_symlink():
        raise ValueError("fixture root or provenance is unavailable or linked")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if not isinstance(provenance, dict) or provenance.get("schema_version") != "0.2" or provenance.get("artifact_type") != "runtime_v02_legacy_v1_fixture_provenance":
        raise ValueError("legacy provenance schema is invalid")
    files = provenance.get("files")
    if not isinstance(files, list) or provenance.get("file_count") != len(files):
        raise ValueError("legacy provenance file count is invalid")
    expected_paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "byte_length", "raw_sha256"}:
            raise ValueError("legacy provenance file entry is invalid")
        relative = entry["path"]
        if not isinstance(relative, str) or not relative or "\\" in relative or relative.startswith("/") or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise ValueError("legacy provenance path is invalid")
        path = fixture_root.joinpath(*relative.split("/"))
        if not path.is_file() or path.is_symlink() or os.path.islink(path):
            raise ValueError(f"legacy fixture file is unavailable or linked: {relative}")
        if not isinstance(entry["byte_length"], int) or isinstance(entry["byte_length"], bool) or entry["byte_length"] < 0:
            raise ValueError("legacy provenance byte length is invalid")
        if not isinstance(entry["raw_sha256"], str) or len(entry["raw_sha256"]) != 64:
            raise ValueError("legacy provenance SHA-256 is invalid")
        if path.stat().st_size != entry["byte_length"] or _sha256(path) != entry["raw_sha256"]:
            raise ValueError(f"legacy fixture bytes do not match: {relative}")
        expected_paths.append(relative)
    if expected_paths != sorted(expected_paths):
        raise ValueError("legacy provenance files are not sorted")
    actual_paths = sorted(
        path.relative_to(fixture_root).as_posix()
        for path in fixture_root.rglob("*")
        if path.is_file() and path.name != "provenance.json"
    )
    if actual_paths != expected_paths:
        raise ValueError("legacy fixture has missing or unlisted files")
    encoded = json.dumps(files, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if provenance.get("file_manifest_aggregate_sha256") != hashlib.sha256(encoded).hexdigest().upper():
        raise ValueError("legacy provenance aggregate is invalid")


def verify(root: Path = ROOT, baseline_path: Path = BASELINE) -> list[str]:
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"authority baseline is unreadable: {exc}"]
    if set(baseline) != {
        "schema_version", "artifact_type", "artifact_version", "raw_sha256",
        "stage2_model_source_tree_sha256",
    } or baseline.get("schema_version") != "0.1" or baseline.get("artifact_type") != "development_authority_baseline" or baseline.get("artifact_version") != "1":
        return ["authority baseline schema is invalid"]
    expected = baseline.get("raw_sha256")
    if not isinstance(expected, dict) or not expected:
        return ["authority baseline raw_sha256 is invalid"]
    errors: list[str] = []
    for relative, expected_sha in expected.items():
        if not isinstance(relative, str) or not isinstance(expected_sha, str):
            errors.append("authority baseline contains a non-string binding")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"authority input is missing: {relative}")
        elif _sha256(path) != expected_sha:
            errors.append(f"authority raw SHA drift: {relative}")
    if errors:
        return errors
    try:
        _verify_runtime_v02_legacy_fixture(root)
    except Exception as exc:
        return [f"Stage 6 legacy fixture integrity failed: {exc}"]
    try:
        sys.path.insert(0, str(root / "src"))
        from sqvm.hamiltonian.provenance import stage2_model_source_tree_sha256

        actual_stage2 = stage2_model_source_tree_sha256(root)
    except Exception as exc:
        return [f"unable to calculate Stage 2 source identity: {exc}"]
    if actual_stage2 != baseline["stage2_model_source_tree_sha256"]:
        errors.append("Stage 2 source identity drift")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    arguments = parser.parse_args()
    errors = verify(arguments.repository_root.resolve(), arguments.baseline.resolve())
    if errors:
        raise SystemExit("authority drift guard failed:\n" + "\n".join(f"- {error}" for error in errors))


if __name__ == "__main__":
    main()
