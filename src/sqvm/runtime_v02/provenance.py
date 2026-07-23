"""Source provenance extension for Runtime 0.2 without changing Runtime 0.1."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.provenance import build_source_snapshot
from sqvm.runtime_v02.core import (
    CompilerFixtureBindingV02,
    _fixture_version,
    _safe_regular_file,
    current_compiler_fixture_version,
    safe_directory_no_follow,
)


LEGACY_V1_SOURCE_AGGREGATES = frozenset({
    "EB4ABF877D5CA3C56CF2F40B93D275E980199035D843C34776ACFD2557C4D2DF",
})


FIXED_V02_FILES = (
    "src/sqvm/runtime_api.py",
    "configs/experiments/platform_qcis_compile_smoke_v1.yaml",
    "docs/designs/06_1_experiment_runtime_v02_design.md",
)


def build_source_snapshot_v02(
    repository_root: str | Path,
    *,
    fixture_binding: CompilerFixtureBindingV02 | None = None,
) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    if fixture_binding is None:
        version = current_compiler_fixture_version()
        fixture = _fixture_version(version)
        approval = fixture["required_approval"]
        fixture_binding = CompilerFixtureBindingV02(version, str(fixture["authority_id"]), str(approval["authority_raw_sha256"]))
    fixture = _fixture_version(fixture_binding.version)
    approval = fixture["required_approval"]
    if fixture_binding.authority_id != fixture["authority_id"] or fixture_binding.authority_sha256 != approval["authority_raw_sha256"]:
        raise ValueError("Runtime 0.2 source fixture binding is invalid")
    package = safe_directory_no_follow(root / "src/sqvm/runtime_v02", root, "Runtime 0.2 source package")
    dynamic = []
    for path in package.rglob("*.py"):
        if path.is_symlink() or "__pycache__" in path.parts:
            raise ValueError("Runtime 0.2 source snapshot contains an unsafe path")
        dynamic.append(path.relative_to(root).as_posix())
    fixture_files = {str(fixture["authority_path"]), str(fixture["approval_path"])}
    entries = [_file_entry(root, relative) for relative in sorted({*dynamic, *FIXED_V02_FILES, *fixture_files})]
    legacy = build_source_snapshot(root)
    aggregate = hashlib.sha256(canonical_json_bytes({
        "compiler_fixture_version": fixture_binding.version,
        "compiler_fixture_authority_id": fixture_binding.authority_id,
        "compiler_fixture_authority_sha256": fixture_binding.authority_sha256,
        "legacy_runtime_v01": legacy,
        "files": entries,
    })).hexdigest().upper()
    return {
        "schema_version": "0.3",
        "compiler_fixture_version": fixture_binding.version,
        "compiler_fixture_authority_id": fixture_binding.authority_id,
        "compiler_fixture_authority_sha256": fixture_binding.authority_sha256,
        "legacy_runtime_v01": legacy,
        "files": entries,
        "aggregate_sha256": aggregate,
    }


def verify_source_snapshot_v02(
    payload: Mapping[str, Any],
    repository_root: str | Path,
    *,
    fixture_binding: CompilerFixtureBindingV02 | None = None,
) -> None:
    old_keys = {"schema_version", "legacy_runtime_v01", "files", "aggregate_sha256"}
    new_keys = {
        "schema_version", "compiler_fixture_version", "compiler_fixture_authority_id",
        "compiler_fixture_authority_sha256", "legacy_runtime_v01", "files", "aggregate_sha256",
    }
    if set(payload) == old_keys and payload.get("schema_version") == "0.2":
        # The old schema predates fixture persistence and is unambiguously v1.
        if fixture_binding is not None and fixture_binding.version != "v1":
            raise ValueError("Runtime 0.2 legacy source snapshot conflicts with fixture binding")
        aggregate = hashlib.sha256(canonical_json_bytes({
            "legacy_runtime_v01": payload["legacy_runtime_v01"], "files": payload["files"],
        })).hexdigest().upper()
        if payload["aggregate_sha256"] != aggregate:
            raise ValueError("Runtime 0.2 legacy source snapshot aggregate is invalid")
        if aggregate not in LEGACY_V1_SOURCE_AGGREGATES:
            raise ValueError("Runtime 0.2 legacy source aggregate is not registered")
        return
    if set(payload) != new_keys or payload.get("schema_version") != "0.3":
        raise ValueError("Runtime 0.2 source snapshot schema is invalid")
    persisted = CompilerFixtureBindingV02(
        str(payload["compiler_fixture_version"]), str(payload["compiler_fixture_authority_id"]),
        str(payload["compiler_fixture_authority_sha256"]),
    )
    if fixture_binding is not None and persisted != fixture_binding:
        raise ValueError("Runtime 0.2 source snapshot fixture binding differs from request")
    if payload != build_source_snapshot_v02(repository_root, fixture_binding=persisted):
        raise ValueError("Runtime 0.2 source snapshot differs from current authority")


def _file_entry(root: Path, relative: str) -> dict[str, Any]:
    path = _safe_regular_file(root, relative)
    raw = path.read_bytes()
    return {"path": relative, "byte_length": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest().upper()}
