#!/usr/bin/env python3
"""Validate a complete Stage 5.1/6 successor rebaseline manifest.

This verifier is intentionally independent of the production admission paths.  It
is a gate for the hand-off: a user authorization is not enough to make a new
authority current, and no v1 artifact may be presented as v2 evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
_SHA256_LENGTH = 64


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == _SHA256_LENGTH and all(char in "0123456789ABCDEF" for char in value)


def _safe_regular_file(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("binding path must be a non-empty string")
    candidate = Path(relative)
    if candidate.is_absolute() or "\\" in relative or any(part in {"", ".", ".."} for part in candidate.parts):
        raise ValueError(f"unsafe binding path: {relative!r}")
    root = root.resolve(strict=True)
    current = root
    for part in candidate.parts:
        current = current / part
        try:
            attributes = getattr(current.stat(), "st_file_attributes", 0)
        except OSError as exc:
            raise ValueError(f"binding is unavailable: {relative}") from exc
        if current.is_symlink() or os.path.islink(current) or attributes & 0x400:
            raise ValueError(f"binding is linked: {relative}")
    if not current.is_file():
        raise ValueError(f"binding is not a regular file: {relative}")
    if current.stat().st_nlink > 1:
        raise ValueError(f"binding is hardlinked: {relative}")
    return current


def _read_json(path: Path, label: str) -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} has duplicate key: {key}")
            value[key] = item
        return value

    def reject_nonfinite(token: str) -> None:
        raise ValueError(f"{label} has non-finite JSON value: {token}")

    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    if canonical_json_bytes(value) != raw:
        raise ValueError(f"{label} is not canonical JSON")
    return value


def _binding(root: Path, value: object, label: str, *, artifact_type: str | None = None) -> tuple[Path, dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"path", "raw_sha256"}:
        raise ValueError(f"{label} binding schema is invalid")
    path = _safe_regular_file(root, value["path"])
    if not _is_sha256(value["raw_sha256"]):
        raise ValueError(f"{label} binding SHA-256 is invalid")
    if sha256(path.read_bytes()) != value["raw_sha256"]:
        raise ValueError(f"{label} binding bytes do not match")
    payload = _read_json(path, label)
    if artifact_type is not None and payload.get("artifact_type") != artifact_type:
        raise ValueError(f"{label} artifact type is invalid")
    return path, payload


def _verify_source_snapshot(root: Path, binding: object, label: str, artifact_type: str) -> tuple[Path, dict[str, Any]]:
    path, snapshot = _binding(root, binding, label, artifact_type=artifact_type)
    if snapshot.get("artifact_version") != "0.2" or snapshot.get("schema_version") != "0.1":
        raise ValueError(f"{label} successor version is invalid")
    rows = snapshot.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{label} sources are invalid")
    previous = ""
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"path", "raw_sha256"}:
            raise ValueError(f"{label} source row schema is invalid")
        source_path = row["path"]
        if not isinstance(source_path, str) or source_path.encode("utf-8") <= previous.encode("utf-8"):
            raise ValueError(f"{label} source ordering is invalid")
        source = _safe_regular_file(root, source_path)
        if not _is_sha256(row["raw_sha256"]) or sha256(source.read_bytes()) != row["raw_sha256"]:
            raise ValueError(f"{label} source bytes do not match: {source_path}")
        previous = source_path
    return path, snapshot


def _verify_stage51(root: Path, value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {"source_snapshot", "environment_snapshot", "authority", "approval", "stage_04_0_raw_sha256"}:
        raise ValueError("stage51 schema is invalid")
    _source_path, _source = _verify_source_snapshot(root, value["source_snapshot"], "stage51 source snapshot", "stage_05_1_source_snapshot")
    environment_path, environment = _binding(root, value["environment_snapshot"], "stage51 environment snapshot", artifact_type="stage_05_1_environment_snapshot")
    authority_path, authority = _binding(root, value["authority"], "stage51 authority", artifact_type="stage_05_1_physics_authority")
    _approval_path, approval = _binding(root, value["approval"], "stage51 approval", artifact_type="stage_05_1_physics_approval")
    if environment.get("artifact_version") != "0.2" or environment.get("schema_version") != "0.1":
        raise ValueError("stage51 environment successor version is invalid")
    if authority.get("schema_version") != "0.1" or authority.get("artifact_version") != "0.2" or authority.get("status") != "approved":
        raise ValueError("stage51 authority successor version or status is invalid")
    if authority.get("source_snapshot_sha256") != value["source_snapshot"]["raw_sha256"] or authority.get("environment_snapshot_sha256") != value["environment_snapshot"]["raw_sha256"]:
        raise ValueError("stage51 authority does not bind its successor snapshots")
    if authority.get("stage_04_0_raw_sha256") != value["stage_04_0_raw_sha256"]:
        raise ValueError("stage51 authority does not bind the Stage 4.0 successor")
    if approval.get("schema_version") != "0.1" or approval.get("artifact_version") != "0.2" or approval.get("status") != "approved":
        raise ValueError("stage51 approval successor version or status is invalid")
    if approval.get("physics_authority_raw_sha256") != sha256(authority_path.read_bytes()):
        raise ValueError("stage51 approval does not bind the successor authority")
    if not isinstance(approval.get("reviewer_role"), str) or not approval["reviewer_role"]:
        raise ValueError("stage51 approval reviewer is invalid")
    if not environment_path.is_file():  # Retains a local regular-file assertion for static analyzers.
        raise ValueError("stage51 environment is unavailable")


def _verify_stage6(root: Path, value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {"source_snapshot", "environment_snapshot", "authority", "approval"}:
        raise ValueError("stage6 schema is invalid")
    _source_path, _source = _verify_source_snapshot(root, value["source_snapshot"], "stage6 source snapshot", "stage_06_source_snapshot")
    _environment_path, environment = _binding(root, value["environment_snapshot"], "stage6 environment snapshot", artifact_type="stage_06_environment_snapshot")
    authority_path, authority = _binding(root, value["authority"], "stage6 authority", artifact_type="stage_06_successor_authority")
    _approval_path, approval = _binding(root, value["approval"], "stage6 approval", artifact_type="stage_06_successor_approval")
    if environment.get("artifact_version") != "0.2" or environment.get("schema_version") != "0.1":
        raise ValueError("stage6 environment successor version is invalid")
    if authority.get("artifact_version") != "0.2" or authority.get("schema_version") != "0.1" or authority.get("status") != "approved":
        raise ValueError("stage6 authority successor version or status is invalid")
    if authority.get("source_snapshot_sha256") != value["source_snapshot"]["raw_sha256"] or authority.get("environment_snapshot_sha256") != value["environment_snapshot"]["raw_sha256"]:
        raise ValueError("stage6 authority does not bind its successor snapshots")
    if approval.get("artifact_version") != "0.2" or approval.get("schema_version") != "0.1" or approval.get("status") != "approved":
        raise ValueError("stage6 approval successor version or status is invalid")
    if approval.get("authority_raw_sha256") != sha256(authority_path.read_bytes()):
        raise ValueError("stage6 approval does not bind the successor authority")
    if not isinstance(approval.get("reviewer_role"), str) or not approval["reviewer_role"]:
        raise ValueError("stage6 approval reviewer is invalid")


_UPSTREAM_STAGES = (
    ("stage_02_1", "stage_02_1_successor_approval"),
    ("stage_03", "stage_03_successor_approval"),
    ("stage_03_1", "stage_03_1_successor_approval"),
    ("stage_04", "stage_04_successor_approval"),
    ("stage_04_0", "stage_04_0_successor_approval"),
)


def _verify_upstream(root: Path, value: object) -> str:
    if not isinstance(value, Mapping) or set(value) != {name for name, _type in _UPSTREAM_STAGES}:
        raise ValueError("upstream schema is invalid")
    predecessor_sha256: str | None = None
    for name, artifact_type in _UPSTREAM_STAGES:
        path, approval = _binding(root, value[name], f"upstream {name}", artifact_type=artifact_type)
        if set(approval) != {
            "schema_version", "artifact_type", "artifact_version", "status", "reviewer_role",
            "final_source_identity_sha256" if name == "stage_02_1" else "predecessor_raw_sha256",
        }:
            raise ValueError(f"upstream {name} schema is invalid")
        if approval.get("schema_version") != "0.1" or approval.get("artifact_version") != "0.2" or approval.get("status") != "approved" or approval.get("reviewer_role") != "independent_reviewer":
            raise ValueError(f"upstream {name} successor identity is invalid")
        if name == "stage_02_1":
            if not _is_sha256(approval["final_source_identity_sha256"]):
                raise ValueError("upstream stage_02_1 final source identity is invalid")
        elif approval["predecessor_raw_sha256"] != predecessor_sha256:
            raise ValueError(f"upstream {name} predecessor chain is invalid")
        predecessor_sha256 = sha256(path.read_bytes())
    assert predecessor_sha256 is not None
    return predecessor_sha256


def _successor_content_sha256(manifest: Mapping[str, Any]) -> str:
    return sha256(canonical_json_bytes({
        "upstream": manifest["upstream"],
        "stage51": manifest["stage51"],
        "stage6": manifest["stage6"],
        "generation_environment": manifest["generation_environment"],
        "selector": manifest["selector"],
    }))


def verify(root: Path, manifest_path: Path) -> list[str]:
    """Return fail-closed findings; an empty list means the successor is complete."""

    try:
        resolved_root = root.resolve(strict=True)
        relative_manifest = (
            manifest_path.relative_to(resolved_root).as_posix()
            if manifest_path.is_absolute()
            else manifest_path.as_posix()
        )
        manifest_file = _safe_regular_file(resolved_root, relative_manifest)
        manifest = _read_json(manifest_file, "successor rebaseline manifest")
        expected = {
            "schema_version", "artifact_type", "artifact_version", "status", "authorization",
            "predecessor", "upstream", "stage51", "stage6", "generation_environment", "selector",
            "successor_content_sha256",
        }
        if set(manifest) != expected or manifest.get("schema_version") != "0.1" or manifest.get("artifact_type") != "successor_rebaseline" or manifest.get("artifact_version") != "1" or manifest.get("status") != "approved":
            raise ValueError("successor rebaseline manifest schema is invalid")
        if manifest["authorization"] != {"source": "user_approved_successor_rebaseline", "authorized_on": "2026-07-23"}:
            raise ValueError("successor authorization is invalid")
        predecessor = manifest["predecessor"]
        if not isinstance(predecessor, Mapping) or set(predecessor) != {"version", "frozen_raw_sha256", "historical_evidence_reexecution", "evidence_reuse"}:
            raise ValueError("predecessor schema is invalid")
        old_hashes = predecessor["frozen_raw_sha256"]
        if predecessor["version"] != "v1" or predecessor["historical_evidence_reexecution"] != "unavailable" or predecessor["evidence_reuse"] != "prohibited" or not isinstance(old_hashes, list) or not old_hashes or not all(_is_sha256(item) for item in old_hashes):
            raise ValueError("predecessor evidence boundary is invalid")
        environment = manifest["generation_environment"]
        if not isinstance(environment, Mapping) or set(environment) != {"python_implementation", "python_version", "platform", "lock_sha256"} or not all(isinstance(environment[name], str) and environment[name] for name in ("python_implementation", "python_version", "platform")) or not _is_sha256(environment["lock_sha256"]):
            raise ValueError("generation environment is invalid")
        if manifest["selector"] != {"historical_artifact_version": "v1", "new_run_version": "v2", "current_selector": "v2"}:
            raise ValueError("versioned selector is invalid")
        upstream_stage_04_0_sha256 = _verify_upstream(root, manifest["upstream"])
        if not isinstance(manifest["stage51"], dict):
            raise ValueError("stage51 schema is invalid")
        if set(manifest["stage51"]) != {"source_snapshot", "environment_snapshot", "authority", "approval", "stage_04_0_raw_sha256"}:
            raise ValueError("stage51 schema is invalid")
        if manifest["stage51"]["stage_04_0_raw_sha256"] != upstream_stage_04_0_sha256:
            raise ValueError("stage51 Stage 4.0 manifest binding is invalid")
        for stage in (manifest["upstream"], manifest["stage51"], manifest["stage6"]):
            for binding in stage.values():
                if isinstance(binding, Mapping) and ("_v1." in binding["path"] or binding["raw_sha256"] in old_hashes):
                    raise ValueError("successor attempts to reuse frozen v1 evidence")
        _verify_stage51(root, manifest["stage51"])
        _verify_stage6(root, manifest["stage6"])
        if manifest["successor_content_sha256"] != _successor_content_sha256(manifest):
            raise ValueError("successor content SHA-256 is invalid")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [str(exc)]
    return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    errors = verify(arguments.repository_root.resolve(), arguments.manifest)
    if errors:
        raise SystemExit("successor rebaseline verification failed:\n" + "\n".join(f"- {error}" for error in errors))


if __name__ == "__main__":
    main()
