"""Load immutable test fixtures into a test-owned writable directory."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
_MANIFEST_NAME = "provenance.json"


def fixture_path(fixture_id: str) -> Path:
    """Return a verified fixture root without exposing a writable fixture path."""

    root = _fixture_root(fixture_id)
    verify_fixture_manifest(root)
    return root


def copy_fixture(fixture_id: str, destination: str | Path) -> Path:
    """Verify and copy a fixture so tests may only mutate their own copy."""

    source = fixture_path(fixture_id)
    target = Path(destination)
    if target.exists() or os.path.lexists(target):
        raise FileExistsError(f"fixture destination already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, copy_function=shutil.copy2)
    return target


def verify_fixture_manifest(root: str | Path) -> dict[str, Any]:
    """Validate a fixture provenance manifest from raw bytes, fail closed."""

    root = Path(root)
    _reject_linked_path(root, "fixture root")
    manifest_path = root / _MANIFEST_NAME
    manifest = _load_strict_json(manifest_path)
    required = {
        "schema_version", "fixture_id", "generator_version", "generator_raw_sha256",
        "source_authority", "fixed_clock_utc", "files", "aggregate_sha256",
    }
    if set(manifest) != required or manifest["schema_version"] != "0.1":
        raise ValueError("fixture manifest schema is invalid")
    if not isinstance(manifest["fixture_id"], str) or not manifest["fixture_id"]:
        raise ValueError("fixture manifest fixture_id is invalid")
    for key in ("generator_version", "source_authority", "fixed_clock_utc"):
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise ValueError(f"fixture manifest {key} is invalid")
    _sha(manifest["generator_raw_sha256"], "generator_raw_sha256")
    files = manifest["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("fixture manifest files is invalid")
    rows: list[tuple[str, int, str]] = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "byte_length", "raw_sha256"}:
            raise ValueError("fixture manifest file entry is invalid")
        relative = entry["path"]
        if not isinstance(relative, str) or not _safe_relative_path(relative):
            raise ValueError("fixture manifest path is invalid")
        length = entry["byte_length"]
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise ValueError("fixture manifest byte_length is invalid")
        digest = entry["raw_sha256"]
        _sha(digest, "raw_sha256")
        path = root.joinpath(*relative.split("/"))
        _reject_linked_path(path, f"fixture file {relative}")
        raw = path.read_bytes()
        if len(raw) != length or hashlib.sha256(raw).hexdigest().upper() != digest:
            raise ValueError(f"fixture manifest bytes do not match: {relative}")
        rows.append((relative, length, digest))
    if rows != sorted(rows, key=lambda row: row[0].encode("utf-8")):
        raise ValueError("fixture manifest files are not sorted")
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("fixture manifest contains duplicate paths")
    actual = _aggregate(rows)
    _sha(manifest["aggregate_sha256"], "aggregate_sha256")
    if actual != manifest["aggregate_sha256"]:
        raise ValueError("fixture manifest aggregate does not match")
    actual_files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != _MANIFEST_NAME
    )
    if actual_files != [row[0] for row in rows]:
        raise ValueError("fixture has missing or unlisted files")
    return manifest


def _fixture_root(fixture_id: str) -> Path:
    if not isinstance(fixture_id, str) or not fixture_id or "/" in fixture_id or "\\" in fixture_id:
        raise ValueError("fixture_id is invalid")
    root = FIXTURES_ROOT / fixture_id
    if not root.is_dir():
        raise FileNotFoundError(f"fixture is unavailable: {fixture_id}")
    return root


def _load_strict_json(path: Path) -> dict[str, Any]:
    _reject_linked_path(path, "fixture manifest")
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"fixture manifest cannot be read: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("fixture manifest root is invalid")
    return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _safe_relative_path(value: str) -> bool:
    return value and "\\" not in value and not value.startswith("/") and all(part not in {"", ".", ".."} for part in value.split("/"))


def _sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789ABCDEF" for char in value):
        raise ValueError(f"fixture manifest {label} is invalid")


def _aggregate(rows: list[tuple[str, int, str]]) -> str:
    digest = hashlib.sha256()
    for path, length, raw_sha256 in rows:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(length).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw_sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def _reject_linked_path(path: Path, label: str) -> None:
    try:
        current = path.absolute()
        for candidate in reversed((current, *current.parents)):
            if not os.path.lexists(candidate):
                continue
            stat = candidate.lstat()
            is_reparse_point = bool(
                getattr(stat, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            is_hardlinked_file = candidate.is_file() and stat.st_nlink > 1
            if candidate.is_symlink() or is_reparse_point or is_hardlinked_file:
                raise ValueError(f"{label} is linked")
    except OSError as exc:
        raise ValueError(f"{label} cannot be inspected") from exc
