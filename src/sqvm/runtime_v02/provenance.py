"""Source provenance extension for Runtime 0.2 without changing Runtime 0.1."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.provenance import build_source_snapshot
from sqvm.runtime_v02.core import _safe_regular_file, safe_directory_no_follow


FIXED_V02_FILES = (
    "src/sqvm/runtime_api.py",
    "configs/experiments/platform_qcis_compile_smoke_v1.yaml",
    "configs/runtime/stage6v02/compiler_fixture_authority_v1.json",
    "configs/runtime/stage6v02/compiler_fixture_approval_v1.json",
    "docs/designs/06_1_experiment_runtime_v02_design.md",
)


def build_source_snapshot_v02(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    package = safe_directory_no_follow(root / "src/sqvm/runtime_v02", root, "Runtime 0.2 source package")
    dynamic = []
    for path in package.rglob("*.py"):
        if path.is_symlink() or "__pycache__" in path.parts:
            raise ValueError("Runtime 0.2 source snapshot contains an unsafe path")
        dynamic.append(path.relative_to(root).as_posix())
    entries = [_file_entry(root, relative) for relative in sorted({*dynamic, *FIXED_V02_FILES})]
    legacy = build_source_snapshot(root)
    aggregate = hashlib.sha256(canonical_json_bytes({"legacy_runtime_v01": legacy, "files": entries})).hexdigest().upper()
    return {
        "schema_version": "0.2",
        "legacy_runtime_v01": legacy,
        "files": entries,
        "aggregate_sha256": aggregate,
    }


def verify_source_snapshot_v02(payload: Mapping[str, Any], repository_root: str | Path) -> None:
    if set(payload) != {"schema_version", "legacy_runtime_v01", "files", "aggregate_sha256"} or payload.get("schema_version") != "0.2":
        raise ValueError("Runtime 0.2 source snapshot schema is invalid")
    if payload != build_source_snapshot_v02(repository_root):
        raise ValueError("Runtime 0.2 source snapshot differs from current authority")


def _file_entry(root: Path, relative: str) -> dict[str, Any]:
    path = _safe_regular_file(root, relative)
    raw = path.read_bytes()
    return {"path": relative, "byte_length": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest().upper()}
