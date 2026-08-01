"""Deterministic writer for the read-only SQVM v1 .sqrun evidence container."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, BinaryIO, Callable, Mapping, Protocol
import uuid
import zipfile

from sqvm.storage.errors import ArchiveFormatError, StorageInventoryError
from sqvm.storage.inventory import inventory_tree
from sqvm.storage.models import ArchiveBundle, ArchiveEntry


ARCHIVE_SCHEMA_VERSION = "0.1"
ARCHIVE_FORMAT_VERSION = "1"
ARCHIVE_WRITER_VERSION = "0.1"
_TOP_LEVEL = (
    "format.json",
    "bundle-manifest.json",
    "bundle-verification-report.json",
    "bundle-receipt.json",
)
_FORMAT_FIELDS = frozenset({
    "schema_version", "artifact_type", "artifact_version", "format_version", "run_id",
    "payload_prefix", "compression", "zip64", "entry_order",
})
_MANIFEST_FIELDS = frozenset({
    "schema_version", "artifact_type", "artifact_version", "run_id", "workflow_id",
    "source_artifact_version", "original_directory_name", "workflow_sha256", "receipt_sha256",
    "entry_count", "logical_bytes", "compression", "entries",
})
_REPORT_FIELDS = frozenset({
    "schema_version", "artifact_type", "artifact_version", "run_id", "ok",
    "bundle_manifest_sha256", "source_verifier_id", "source_verifier_version", "checks",
    "blocking_reasons",
})
_RECEIPT_FIELDS = frozenset({
    "schema_version", "artifact_type", "artifact_version", "run_id", "bundle_manifest_sha256",
    "bundle_verification_report_sha256", "workflow_sha256", "receipt_sha256",
    "source_verifier_id", "source_verifier_version", "entry_count", "logical_bytes",
})
_ENTRY_FIELDS = frozenset({"path", "byte_length", "raw_sha256"})
_SHA256 = re.compile(r"[A-F0-9]{64}$")
_CHUNK_BYTES = 64 * 1024
_METADATA_READ_LIMIT = 1024 * 1024


class EvidenceReader(Protocol):
    """A bounded read-only view over exactly the manifest-declared files."""

    def paths(self) -> tuple[str, ...]: ...

    def open_binary(self, path: str) -> BinaryIO: ...

    def read_bytes(self, path: str, *, maximum_bytes: int = _METADATA_READ_LIMIT) -> bytes: ...


EvidenceVerifier = Callable[[EvidenceReader], None]


class DirectoryEvidenceReader:
    """No-follow evidence reader used before a source directory is bundled."""

    def __init__(self, root: Path, entries: tuple[ArchiveEntry, ...]) -> None:
        self._root = root
        self._entries = {entry.path: entry for entry in entries}

    def paths(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def open_binary(self, path: str) -> BinaryIO:
        entry = self._entry(path)
        return _open_source_binary(self._root / entry.path, entry)

    def read_bytes(self, path: str, *, maximum_bytes: int = _METADATA_READ_LIMIT) -> bytes:
        entry = self._entry(path)
        if type(maximum_bytes) is not int or maximum_bytes < 0 or entry.byte_length > maximum_bytes:
            raise ArchiveFormatError("evidence reader byte limit is exceeded")
        with self.open_binary(path) as stream:
            return _read_stream_exact(stream, entry.byte_length, entry.raw_sha256)

    def _entry(self, path: str) -> ArchiveEntry:
        if not isinstance(path, str) or path not in self._entries:
            raise ArchiveFormatError("evidence reader path is not declared")
        return self._entries[path]


def canonical_archive_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Serialize archive metadata in its frozen, cross-platform byte form."""

    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def write_sqrun(
    run_root: str | Path,
    archive_path: str | Path,
    *,
    source_verifier_id: str,
    source_verifier_version: str,
    source_verifier: EvidenceVerifier,
) -> ArchiveBundle:
    """Create a deterministic ZIP64/STORED archive without altering source evidence."""

    source = Path(os.path.abspath(os.fspath(run_root)))
    target = Path(os.path.abspath(os.fspath(archive_path)))
    _validate_archive_target(source, target)
    if not isinstance(source_verifier_id, str) or not source_verifier_id:
        raise ArchiveFormatError("source verifier identity is invalid")
    if not isinstance(source_verifier_version, str) or not source_verifier_version:
        raise ArchiveFormatError("source verifier version is invalid")
    if not callable(source_verifier):
        raise ArchiveFormatError("source verifier callback is required")
    try:
        inventory = inventory_tree(source, confinement_root=source)
    except StorageInventoryError as exc:
        raise ArchiveFormatError("source inventory is unsafe") from exc
    if not inventory.files:
        raise ArchiveFormatError("source inventory is empty")
    entries = tuple(_source_entry(source, row.relative_path, row.logical_bytes) for row in inventory.files)
    identities = _source_identities(source, entries)
    _validate_source_layout(entries)
    try:
        source_verifier(DirectoryEvidenceReader(source, entries))
    except ArchiveFormatError:
        raise
    except Exception as exc:
        raise ArchiveFormatError("source verifier failed") from exc
    _assert_source_snapshot(source, entries, identities)
    workflow = _strict_metadata_json(
        DirectoryEvidenceReader(source, entries).read_bytes("workflow.json"), "workflow"
    )
    run_id, workflow_id, source_artifact_version = _workflow_identity(workflow)
    from sqvm.storage.workflow_verifiers import get_workflow_evidence_verifier, valid_hot_alias_for
    # This generic writer is also used by tests and future verifier owners;
    # operations decide whether a workflow is archivable.  Known production
    # workflows additionally bind their carrier name to the registry.
    if get_workflow_evidence_verifier(workflow_id, source_artifact_version) is not None:
        if not valid_hot_alias_for(workflow_id, source_artifact_version, run_id, source.name):
            raise ArchiveFormatError("source directory name does not match registered workflow")
    _safe_single_component(source.name, "original directory name")
    manifest = _manifest(run_id, workflow_id, source_artifact_version, source.name, entries)
    manifest_raw = canonical_archive_json_bytes(manifest)
    report = _report(run_id, sha256_bytes(manifest_raw), source_verifier_id, source_verifier_version)
    report_raw = canonical_archive_json_bytes(report)
    receipt = _receipt(
        run_id, sha256_bytes(manifest_raw), sha256_bytes(report_raw), manifest,
        source_verifier_id, source_verifier_version,
    )
    format_payload = _format(run_id)
    records = tuple(sorted((
        ("format.json", canonical_archive_json_bytes(format_payload)),
        ("bundle-manifest.json", manifest_raw),
        ("bundle-verification-report.json", report_raw),
        ("bundle-receipt.json", canonical_archive_json_bytes(receipt)),
    ), key=lambda row: row[0].encode("utf-8")))
    try:
        _validate_archive_target(source, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        _validate_archive_target(source, target)
        with target.open("xb") as stream, zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for name, raw in records:
                _write_member(archive, name, raw)
            for entry in entries:
                _write_source_member(archive, f"run/{entry.path}", source / entry.path, entry, identities[entry.path])
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveFormatError("cannot write deterministic archive") from exc
    expected_bundle = ArchiveBundle(
        run_id=run_id,
        workflow_id=workflow_id,
        workflow_sha256=manifest["workflow_sha256"],
        receipt_sha256=manifest["receipt_sha256"],
        source_artifact_version=source_artifact_version,
        original_directory_name=source.name,
        entries=entries,
        logical_bytes=manifest["logical_bytes"],
        format_payload=format_payload,
        manifest_payload=manifest,
        verification_report_payload=report,
        receipt_payload=receipt,
        source_verified=True,
    )
    from sqvm.storage.archive_verify import verify_sqrun

    verified_bundle = verify_sqrun(
        target,
        verifier_registry={(source_verifier_id, source_verifier_version): source_verifier},
        require_source_verified=True,
    )
    if verified_bundle != expected_bundle:
        raise ArchiveFormatError("post-write archive verification does not match source bundle")
    return verified_bundle


def _source_entry(root: Path, relative: str, expected_length: int) -> ArchiveEntry:
    byte_length, digest = _hash_source_file(root / relative)
    if byte_length != expected_length:
        raise ArchiveFormatError("source file size changed during inventory")
    return ArchiveEntry(relative, byte_length, digest)


def _open_source_binary(path: Path, expected: ArchiveEntry) -> BinaryIO:
    try:
        before = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
            raise ArchiveFormatError("source file is linked or special")
        stream = path.open("rb")
        opened = os_fstat(stream)
        after = path.lstat()
        if (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino) or after.st_nlink > 1:
            stream.close()
            raise ArchiveFormatError("source file changed during read")
    except OSError as exc:
        raise ArchiveFormatError("cannot read source file") from exc
    return stream


def _read_stream_exact(stream: BinaryIO, expected_length: int, expected_sha256: str) -> bytes:
    raw = stream.read(expected_length + 1)
    if len(raw) != expected_length or sha256_bytes(raw) != expected_sha256:
        raise ArchiveFormatError("source file changed during read")
    return raw


def _hash_source_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with _open_source_binary(path, ArchiveEntry("", 0, "")) as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest().upper()


def _source_identities(root: Path, entries: tuple[ArchiveEntry, ...]) -> dict[str, tuple[int, int, int]]:
    result: dict[str, tuple[int, int, int]] = {}
    for entry in entries:
        try:
            info = (root / entry.path).lstat()
        except OSError as exc:
            raise ArchiveFormatError("source file cannot be inspected") from exc
        if _is_link_or_reparse(root / entry.path, info) or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise ArchiveFormatError("source file is linked or special")
        result[entry.path] = (info.st_dev, info.st_ino, info.st_size)
    return result


def _assert_source_snapshot(source: Path, entries: tuple[ArchiveEntry, ...], identities: Mapping[str, tuple[int, int, int]]) -> None:
    try:
        refreshed = inventory_tree(source, confinement_root=source)
    except StorageInventoryError as exc:
        raise ArchiveFormatError("source inventory changed or is unsafe") from exc
    refreshed_entries = tuple(_source_entry(source, row.relative_path, row.logical_bytes) for row in refreshed.files)
    if refreshed_entries != entries or _source_identities(source, entries) != dict(identities):
        raise ArchiveFormatError("source inventory changed during verifier callback")


def os_fstat(stream: Any) -> Any:
    """Small indirection keeps the source-file identity check testable."""

    import os

    return os.fstat(stream.fileno())


def _validate_source_layout(entries: tuple[ArchiveEntry, ...]) -> None:
    paths = [entry.path for entry in entries]
    if paths != sorted(paths, key=lambda value: value.encode("utf-8")):
        raise ArchiveFormatError("source inventory order is invalid")
    if "workflow.json" not in paths or "receipt.json" not in paths:
        raise ArchiveFormatError("source workflow or receipt is missing")
    for path in paths:
        _safe_payload_path(path)


def _workflow_identity(workflow: Mapping[str, Any]) -> tuple[str, str, str]:
    run_id = workflow.get("run_id")
    workflow_id = workflow.get("workflow_id")
    artifact_version = workflow.get("artifact_version")
    try:
        parsed = uuid.UUID(str(run_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ArchiveFormatError("workflow identity is invalid") from exc
    if not isinstance(run_id, str) or str(parsed) != run_id:
        raise ArchiveFormatError("workflow identity is invalid")
    if not isinstance(workflow_id, str) or not workflow_id or len(workflow_id) > 128:
        raise ArchiveFormatError("workflow identity is invalid")
    if not isinstance(artifact_version, str) or not artifact_version or len(artifact_version) > 32:
        raise ArchiveFormatError("workflow identity is invalid")
    return run_id, workflow_id, artifact_version


def _format(run_id: str) -> dict[str, object]:
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "artifact_type": "sqvm_experiment_bundle_format",
        "artifact_version": ARCHIVE_SCHEMA_VERSION,
        "format_version": ARCHIVE_FORMAT_VERSION,
        "run_id": run_id,
        "payload_prefix": "run/",
        "compression": "stored",
        "zip64": True,
        "entry_order": "utf8_posix_path_bytes",
    }


def _manifest(
    run_id: str,
    workflow_id: str,
    source_artifact_version: str,
    directory_name: str,
    entries: tuple[ArchiveEntry, ...],
) -> dict[str, object]:
    _safe_single_component(directory_name, "original directory name")
    by_path = {entry.path: entry for entry in entries}
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "artifact_type": "sqvm_experiment_bundle_manifest",
        "artifact_version": ARCHIVE_SCHEMA_VERSION,
        "run_id": run_id,
        "workflow_id": workflow_id,
        "source_artifact_version": source_artifact_version,
        "original_directory_name": directory_name,
        "workflow_sha256": by_path["workflow.json"].raw_sha256,
        "receipt_sha256": by_path["receipt.json"].raw_sha256,
        "entry_count": len(entries),
        "logical_bytes": sum(entry.byte_length for entry in entries),
        "compression": {"method": "stored", "level": None, "writer_version": ARCHIVE_WRITER_VERSION},
        "entries": [
            {"path": entry.path, "byte_length": entry.byte_length, "raw_sha256": entry.raw_sha256}
            for entry in entries
        ],
    }


def _report(run_id: str, manifest_sha256: str, verifier_id: str, verifier_version: str) -> dict[str, object]:
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "artifact_type": "sqvm_experiment_bundle_verification_report",
        "artifact_version": ARCHIVE_SCHEMA_VERSION,
        "run_id": run_id,
        "ok": True,
        "bundle_manifest_sha256": manifest_sha256,
        "source_verifier_id": verifier_id,
        "source_verifier_version": verifier_version,
        "checks": [],
        "blocking_reasons": [],
    }


def _receipt(
    run_id: str,
    manifest_sha256: str,
    report_sha256: str,
    manifest: Mapping[str, object],
    verifier_id: str,
    verifier_version: str,
) -> dict[str, object]:
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "artifact_type": "sqvm_experiment_bundle_receipt",
        "artifact_version": ARCHIVE_SCHEMA_VERSION,
        "run_id": run_id,
        "bundle_manifest_sha256": manifest_sha256,
        "bundle_verification_report_sha256": report_sha256,
        "workflow_sha256": manifest["workflow_sha256"],
        "receipt_sha256": manifest["receipt_sha256"],
        "source_verifier_id": verifier_id,
        "source_verifier_version": verifier_version,
        "entry_count": manifest["entry_count"],
        "logical_bytes": manifest["logical_bytes"],
    }


def _write_member(archive: zipfile.ZipFile, name: str, raw: bytes) -> None:
    _safe_payload_path(name)
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 45
    info.extract_version = 45
    info.flag_bits = 0
    info.internal_attr = 0
    info.external_attr = 0o100644 << 16
    info.extra = b""
    with archive.open(info, "w", force_zip64=True) as stream:
        stream.write(raw)


def _write_source_member(
    archive: zipfile.ZipFile,
    name: str,
    path: Path,
    expected: ArchiveEntry,
    expected_identity: tuple[int, int, int],
) -> None:
    _safe_payload_path(name)
    info = _zip_info(name)
    digest = hashlib.sha256()
    byte_length = 0
    with _open_source_binary(path, expected) as source, archive.open(info, "w", force_zip64=True) as target:
        opened = os_fstat(source)
        if (opened.st_dev, opened.st_ino, opened.st_size) != expected_identity:
            raise ArchiveFormatError("source file identity changed during archive write")
        while chunk := source.read(_CHUNK_BYTES):
            target.write(chunk)
            digest.update(chunk)
            byte_length += len(chunk)
    if byte_length != expected.byte_length or digest.hexdigest().upper() != expected.raw_sha256:
        raise ArchiveFormatError("source file changed during archive write")


def _zip_info(name: str) -> zipfile.ZipInfo:
    _safe_payload_path(name)
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 45
    info.extract_version = 45
    info.flag_bits = 0
    info.internal_attr = 0
    info.external_attr = 0o100644 << 16
    info.extra = b""
    return info


def _safe_payload_path(path: str) -> None:
    if not isinstance(path, str) or not path or "\\" in path or "\x00" in path or ":" in path:
        raise ArchiveFormatError("archive path is unsafe")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ArchiveFormatError("archive path is unsafe")


def _safe_single_component(value: str, label: str) -> None:
    if not isinstance(value, str) or "/" in value or "\\" in value or value in {"", ".", ".."} or ":" in value or "\x00" in value:
        raise ArchiveFormatError(f"{label} is unsafe")


def _validate_archive_target(source: Path, target: Path) -> None:
    source_key = os.path.normcase(str(source))
    target_key = os.path.normcase(str(target))
    try:
        inside_source = os.path.commonpath((source_key, target_key)) == source_key
    except ValueError:
        inside_source = False
    if inside_source:
        raise ArchiveFormatError("archive target is inside source tree")
    if os.path.lexists(target):
        raise ArchiveFormatError("archive target already exists or is linked")
    _validate_target_parent_no_follow(target.parent)


def _validate_target_parent_no_follow(parent: Path) -> None:
    path = Path(os.path.abspath(os.fspath(parent)))
    anchor = Path(path.anchor)
    try:
        anchor_info = anchor.lstat()
    except OSError as exc:
        raise ArchiveFormatError("archive target root cannot be inspected") from exc
    if _is_link_or_reparse(anchor, anchor_info):
        raise ArchiveFormatError("archive target parent is linked or reparse-backed")
    current = anchor
    for part in path.parts[1:]:
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            raise ArchiveFormatError("archive target parent cannot be inspected") from exc
        matches = [entry for entry in entries if entry.name == part]
        if not matches:
            return
        if len(matches) != 1:
            raise ArchiveFormatError("archive target parent is ambiguous")
        entry = matches[0]
        candidate = current / entry.name
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ArchiveFormatError("archive target parent cannot be inspected") from exc
        if _is_link_or_reparse(candidate, info) or not stat.S_ISDIR(info.st_mode):
            raise ArchiveFormatError("archive target parent is linked or reparse-backed")
        current = candidate


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(
        reparse and getattr(info, "st_file_attributes", 0) & reparse
    )


def _strict_metadata_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ArchiveFormatError(f"{label} has duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ArchiveFormatError) as exc:
        raise ArchiveFormatError(f"{label} metadata is invalid") from exc
    if not isinstance(value, dict) or raw != canonical_archive_json_bytes(value):
        raise ArchiveFormatError(f"{label} metadata is not canonical")
    return value


__all__ = [
    "ARCHIVE_FORMAT_VERSION", "ARCHIVE_SCHEMA_VERSION", "ARCHIVE_WRITER_VERSION",
    "canonical_archive_json_bytes", "sha256_bytes", "write_sqrun",
]
