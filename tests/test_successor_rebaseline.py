from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from tools.verify_successor_rebaseline import canonical_json_bytes, verify
from tools.verify_successor_development_baseline_approval import verify as verify_development_baseline_approval


pytestmark = pytest.mark.evidence


ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_ID = "successor_rebaseline_authority_v1"
_APPROVAL = "docs/decisions/2026-07-23-successor-development-baseline-approval.json"
_AUTHORIZATION = "docs/decisions/2026-07-23-successor-rebaseline-authority.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(root: Path, relative: str, value: object) -> tuple[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
    return relative, _sha256(path)


def _binding(root: Path, relative: str, value: object) -> dict[str, str]:
    path, digest = _write_json(root, relative, value)
    return {"path": path, "raw_sha256": digest}


def _manifest(root: Path) -> Path:
    upstream: dict[str, dict[str, str]] = {}
    predecessor: str | None = None
    for name, artifact_type in (
        ("stage_02_1", "stage_02_1_successor_approval"),
        ("stage_03", "stage_03_successor_approval"),
        ("stage_03_1", "stage_03_1_successor_approval"),
        ("stage_04", "stage_04_successor_approval"),
        ("stage_04_0", "stage_04_0_successor_approval"),
    ):
        payload = {
            "schema_version": "0.1",
            "artifact_type": artifact_type,
            "artifact_version": "0.2",
            "status": "approved",
            "reviewer_role": "independent_reviewer",
        }
        if predecessor is None:
            payload["final_source_identity_sha256"] = "C" * 64
        else:
            payload["predecessor_raw_sha256"] = predecessor
        upstream[name] = _binding(root, f"configs/upstream/{name}_approval_v2.json", payload)
        predecessor = upstream[name]["raw_sha256"]

    source_file = root / "src/new_model.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("SUCCESSOR = 2\n", encoding="utf-8")
    source = _binding(root, "configs/stage51/source_snapshot_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_05_1_source_snapshot",
        "artifact_version": "0.2",
        "sources": [{"path": "src/new_model.py", "raw_sha256": _sha256(source_file)}],
    })
    environment = _binding(root, "configs/stage51/environment_snapshot_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_05_1_environment_snapshot",
        "artifact_version": "0.2",
    })
    authority = _binding(root, "configs/stage51/physics_authority_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_05_1_physics_authority",
        "artifact_version": "0.2",
        "status": "approved",
        "source_snapshot_sha256": source["raw_sha256"],
        "environment_snapshot_sha256": environment["raw_sha256"],
        "stage_04_0_raw_sha256": upstream["stage_04_0"]["raw_sha256"],
    })
    approval = _binding(root, "configs/stage51/physics_approval_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_05_1_physics_approval",
        "artifact_version": "0.2",
        "status": "approved",
        "physics_authority_raw_sha256": authority["raw_sha256"],
        "reviewer_role": "independent_reviewer",
    })
    stage6_source_file = root / "src/new_runtime.py"
    stage6_source_file.write_text("RUNTIME_SUCCESSOR = 2\n", encoding="utf-8")
    stage6_source = _binding(root, "configs/stage6/source_snapshot_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_06_source_snapshot",
        "artifact_version": "0.2",
        "sources": [{"path": "src/new_runtime.py", "raw_sha256": _sha256(stage6_source_file)}],
    })
    stage6_environment = _binding(root, "configs/stage6/environment_snapshot_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_06_environment_snapshot",
        "artifact_version": "0.2",
    })
    stage6_authority = _binding(root, "configs/stage6/authority_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_06_successor_authority",
        "artifact_version": "0.2",
        "status": "approved",
        "source_snapshot_sha256": stage6_source["raw_sha256"],
        "environment_snapshot_sha256": stage6_environment["raw_sha256"],
    })
    stage6_approval = _binding(root, "configs/stage6/approval_v2.json", {
        "schema_version": "0.1",
        "artifact_type": "stage_06_successor_approval",
        "artifact_version": "0.2",
        "status": "approved",
        "authority_raw_sha256": stage6_authority["raw_sha256"],
        "reviewer_role": "independent_reviewer",
    })
    stage6 = {
        "source_snapshot": stage6_source,
        "environment_snapshot": stage6_environment,
        "authority": stage6_authority,
        "approval": stage6_approval,
    }
    manifest = {
        "schema_version": "0.1",
        "artifact_type": "successor_rebaseline",
        "artifact_version": "1",
        "status": "approved",
        "authorization": {"source": "user_approved_successor_rebaseline", "authorized_on": "2026-07-23"},
        "predecessor": {
            "version": "v1",
            "frozen_raw_sha256": ["A" * 64],
            "historical_evidence_reexecution": "unavailable",
            "evidence_reuse": "prohibited",
        },
        "upstream": upstream,
        "stage51": {"source_snapshot": source, "environment_snapshot": environment, "authority": authority, "approval": approval, "stage_04_0_raw_sha256": upstream["stage_04_0"]["raw_sha256"]},
        "stage6": stage6,
        "generation_environment": {
            "python_implementation": "CPython",
            "python_version": "3.12.10",
            "platform": "windows",
            "lock_sha256": "B" * 64,
        },
        "selector": {"historical_artifact_version": "v1", "new_run_version": "v2", "current_selector": "v2"},
    }
    manifest["successor_content_sha256"] = hashlib.sha256(canonical_json_bytes({
        "upstream": manifest["upstream"],
        "stage51": manifest["stage51"],
        "stage6": manifest["stage6"],
        "generation_environment": manifest["generation_environment"],
        "selector": manifest["selector"],
    })).hexdigest().upper()
    path = root / "configs/successor_rebaseline_v2.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(manifest))
    return path


def test_complete_successor_manifest_is_admitted(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    assert verify(tmp_path, manifest.relative_to(tmp_path)) == []


@pytest.mark.parametrize("target", ["source", "authority", "approval", "content", "reuse_v1", "missing_upstream", "upstream_break", "upstream_v1", "upstream_v1_hash"])
def test_successor_manifest_fails_closed_for_tampering(tmp_path: Path, target: str) -> None:
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if target == "source":
        (tmp_path / "src/new_model.py").write_text("SUCCESSOR = 3\n", encoding="utf-8")
    elif target == "authority":
        manifest["stage51"]["authority"]["raw_sha256"] = "0" * 64
    elif target == "approval":
        approval = tmp_path / manifest["stage51"]["approval"]["path"]
        approval.write_text(approval.read_text(encoding="utf-8").replace("approved", "draft"), encoding="utf-8")
    elif target == "content":
        manifest["successor_content_sha256"] = "0" * 64
    elif target == "reuse_v1":
        v1 = tmp_path / "configs/stage6/authority_v1.json"
        v1.write_bytes((tmp_path / manifest["stage6"]["authority"]["path"]).read_bytes())
        manifest["stage6"]["authority"]["path"] = "configs/stage6/authority_v1.json"
    elif target == "missing_upstream":
        manifest["upstream"].pop("stage_04")
    elif target == "upstream_break":
        path = tmp_path / manifest["upstream"]["stage_03"]["path"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["predecessor_raw_sha256"] = "0" * 64
        path.write_bytes(canonical_json_bytes(payload))
        manifest["upstream"]["stage_03"]["raw_sha256"] = _sha256(path)
    elif target == "upstream_v1":
        source = tmp_path / manifest["upstream"]["stage_03"]["path"]
        v1 = tmp_path / "configs/upstream/stage_03_approval_v1.json"
        v1.write_bytes(source.read_bytes())
        manifest["upstream"]["stage_03"]["path"] = "configs/upstream/stage_03_approval_v1.json"
    else:
        manifest["upstream"]["stage_03"]["raw_sha256"] = "A" * 64
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    assert verify(tmp_path, manifest_path.relative_to(tmp_path))


@pytest.mark.link_privilege
def test_successor_manifest_rejects_linked_evidence(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    linked = tmp_path / "configs/stage6/linked_authority_v2.json"
    try:
        linked.symlink_to(tmp_path / "configs/stage6/authority_v2.json")
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stage6"]["authority"]["path"] = "configs/stage6/linked_authority_v2.json"
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    assert verify(tmp_path, manifest_path.relative_to(tmp_path))


def test_successor_manifest_rejects_hardlinked_evidence(tmp_path: Path) -> None:
    manifest_path = _manifest(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    authority = tmp_path / manifest["stage6"]["authority"]["path"]
    copy = tmp_path / "configs/stage6/authority_copy.json"
    copy.write_bytes(authority.read_bytes())
    authority.unlink()
    os.link(copy, authority)
    assert authority.stat().st_nlink == 2

    assert verify(tmp_path, manifest_path.relative_to(tmp_path))


@pytest.mark.parametrize("raw", [
    b'{"artifact_type":"successor_rebaseline","artifact_type":"successor_rebaseline"}',
    b'{"artifact_type":NaN}',
])
def test_successor_manifest_rejects_duplicate_keys_and_nonfinite_json(tmp_path: Path, raw: bytes) -> None:
    manifest_path = _manifest(tmp_path)
    manifest_path.write_bytes(raw)

    assert verify(tmp_path, manifest_path.relative_to(tmp_path))


def _approval_root(destination: Path) -> Path:
    fixture = ROOT / "tests/fixtures" / _FIXTURE_ID
    shutil.copytree(fixture, destination / "tests/fixtures" / _FIXTURE_ID)
    source = json.loads((fixture / "provisional_source_provenance.json").read_text(encoding="utf-8"))
    for row in source["source_files"]:
        target = destination / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / row["path"], target)
    for relative in (_APPROVAL, _AUTHORIZATION):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return destination


def test_committed_successor_development_baseline_approval_is_admitted() -> None:
    assert verify_development_baseline_approval(ROOT) == []


@pytest.mark.parametrize("target", ["tamper", "missing", "hardlink"])
def test_development_baseline_approval_fails_closed(tmp_path: Path, target: str) -> None:
    root = _approval_root(tmp_path / "repository")
    approval = root / _APPROVAL
    if target == "tamper":
        approval.write_bytes(approval.read_bytes() + b" ")
    elif target == "missing":
        approval.unlink()
    else:
        external = root / "external_approval.json"
        external.write_bytes(approval.read_bytes())
        approval.unlink()
        os.link(external, approval)
        assert approval.stat().st_nlink == 2

    assert verify_development_baseline_approval(root)
