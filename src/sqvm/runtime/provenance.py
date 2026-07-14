"""Stage 6 source and environment provenance snapshots."""

from __future__ import annotations

import hashlib
from importlib import metadata
from pathlib import Path
import platform
import sys
from typing import Any, Iterable, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes


DESIGN_AUTHORITY = (
    "docs/stages/06_experiment_runtime_plan.md",
    "docs/designs/06_experiment_runtime_design.md",
    "docs/decisions/2026-07-14-stage6-platform-only-entry.md",
    "docs/decisions/2026-07-14-stage6-program-extension-amendment.md",
    "docs/decisions/2026-07-14-stage6-program-extension-freeze-review.md",
)
FIXED_SOURCE_FILES = (
    "src/sqvm/__main__.py",
    "src/sqvm/hamiltonian/provenance.py",
    "configs/experiments/platform_deterministic_smoke_v1.yaml",
    "configs/calibration/platform_uncalibrated_v1.json",
    "pyproject.toml",
    "requirements-stage6-lock.txt",
    *DESIGN_AUTHORITY,
)
LOCKED_DISTRIBUTIONS = ("numpy", "scipy", "PyYAML", "nbformat", "matplotlib", "qutip", "pytest")


def build_source_snapshot(repository_root: str | Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    runtime_root = root / "src/sqvm/runtime"
    if not runtime_root.is_dir():
        raise ValueError("Stage 6 runtime source directory is missing")
    dynamic = []
    for path in runtime_root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Stage 6 source snapshot cannot contain symlinks")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            dynamic.append(path.relative_to(root).as_posix())
    paths = sorted({*dynamic, *FIXED_SOURCE_FILES})
    entries = [_file_entry(root, value) for value in paths]
    aggregate = hashlib.sha256(canonical_json_bytes({"files": entries})).hexdigest().upper()
    return {"schema_version": "0.1", "files": entries, "aggregate_sha256": aggregate}


def verify_source_snapshot(payload: Mapping[str, Any], repository_root: str | Path) -> None:
    if set(payload) != {"schema_version", "files", "aggregate_sha256"} or payload.get("schema_version") != "0.1":
        raise ValueError("source snapshot schema is invalid")
    expected = build_source_snapshot(repository_root)
    if payload != expected:
        raise ValueError("source snapshot does not match current Stage 6 authority")


def build_environment_snapshot() -> dict[str, Any]:
    packages = {}
    for name in LOCKED_DISTRIBUTIONS:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError as exc:
            raise ValueError(f"locked Stage 6 distribution is missing: {name}") from exc
    return {
        "schema_version": "0.1",
        "python": {"implementation": platform.python_implementation(), "version": platform.python_version()},
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "architecture": platform.architecture()[0],
        },
        "packages": dict(sorted(packages.items())),
        "blas_threading": _threadpool_summary(),
    }


def validate_locked_environment(repository_root: str | Path, payload: Mapping[str, Any]) -> None:
    root = Path(repository_root).resolve()
    lock = root / "requirements-stage6-lock.txt"
    expected = {}
    for raw_line in lock.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise ValueError("Stage 6 lock must contain exact name==version entries")
        name, version = line.split("==", 1)
        expected[name] = version
    if expected != payload.get("packages"):
        raise ValueError("current environment does not match requirements-stage6-lock.txt")
    if payload.get("python") != {"implementation": "CPython", "version": "3.12.10"}:
        raise ValueError("Stage 6 runtime requires the frozen CPython 3.12.10 interpreter")


def source_snapshot_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


def _file_entry(root: Path, relative: str) -> dict[str, Any]:
    path = root / Path(relative)
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"source snapshot path escapes repository: {relative}") from exc
    if path.is_symlink() or not resolved.is_file():
        raise ValueError(f"source snapshot file is missing or unsafe: {relative}")
    raw = resolved.read_bytes()
    return {"path": relative, "byte_length": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest().upper()}


def _threadpool_summary() -> list[dict[str, Any]]:
    try:
        from threadpoolctl import threadpool_info
    except ImportError:
        return []
    allowed = ("user_api", "internal_api", "prefix", "version", "num_threads", "architecture", "threading_layer")
    rows = []
    for source in threadpool_info():
        row = {key: source[key] for key in allowed if key in source and source[key] is not None}
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(str(row.get(key, "")) for key in allowed))
