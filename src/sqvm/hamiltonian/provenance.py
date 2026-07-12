"""Content provenance helpers for the Stage 2.1 rebaseline gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def raw_file_sha256(path: str | Path) -> str:
    """Return the uppercase SHA-256 of a file's unmodified bytes."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def stage2_model_source_tree_sha256(repository_root: str | Path) -> str:
    """Hash the frozen Stage 1/2 model source set using the Stage 2.1 framing."""

    root = Path(repository_root).resolve()
    paths = [root / "pyproject.toml"]
    paths.extend((root / "src" / "sqvm" / "device").rglob("*.py"))
    paths.extend((root / "src" / "sqvm" / "hamiltonian").rglob("*.py"))
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"Stage 2 model source file is missing: {missing[0]}")

    framed = hashlib.sha256()
    relative_paths = sorted(
        ((path.relative_to(root).as_posix(), path) for path in paths),
        key=lambda item: item[0].encode("utf-8"),
    )
    for relative, path in relative_paths:
        raw = path.read_bytes()
        framed.update(relative.encode("utf-8"))
        framed.update(b"\0")
        framed.update(str(len(raw)).encode("ascii"))
        framed.update(b"\0")
        framed.update(raw)
    return framed.hexdigest().upper()


def find_repository_root(start: str | Path) -> Path:
    """Find the nearest ancestor containing pyproject.toml, with cwd fallback."""

    candidate = Path(start).resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for directory in (candidate, *candidate.parents):
        if (directory / "pyproject.toml").is_file():
            return directory

    cwd = Path.cwd().resolve()
    for directory in (cwd, *cwd.parents):
        if (directory / "pyproject.toml").is_file():
            return directory
    raise ValueError("cannot locate repository root containing pyproject.toml")


def build_stage2_artifact_provenance(
    *,
    device_artifacts_path: str | Path,
    hamiltonian_config_path: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, str]:
    config_path = Path(hamiltonian_config_path)
    root = find_repository_root(config_path) if repository_root is None else Path(repository_root)
    return {
        "device_artifacts_sha256": raw_file_sha256(device_artifacts_path),
        "hamiltonian_config_sha256": raw_file_sha256(config_path),
        "stage2_model_source_tree_sha256": stage2_model_source_tree_sha256(root),
    }


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize JSON deterministically as UTF-8, LF, no BOM, with a final LF."""

    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")
