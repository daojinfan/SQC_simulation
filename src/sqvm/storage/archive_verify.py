"""Streaming verifier and bounded evidence reader for v1 .sqrun containers."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
from typing import Any, BinaryIO, Mapping
import uuid
import zipfile

from sqvm.storage.archive_format import (
    ARCHIVE_FORMAT_VERSION,
    ARCHIVE_SCHEMA_VERSION,
    ARCHIVE_WRITER_VERSION,
    DirectoryEvidenceReader,
    EvidenceReader,
    EvidenceVerifier,
    _ENTRY_FIELDS,
    _FORMAT_FIELDS,
    _MANIFEST_FIELDS,
    _RECEIPT_FIELDS,
    _REPORT_FIELDS,
    _TOP_LEVEL,
    canonical_archive_json_bytes,
    sha256_bytes,
)
from sqvm.storage.errors import ArchiveFormatError
from sqvm.storage.inventory import _is_link_or_reparse
from sqvm.storage.models import ArchiveBundle, ArchiveEntry


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 100_000
    max_entry_bytes: int = 2 * 1024**3
    max_total_bytes: int = 128 * 1024**3
    max_metadata_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        for value in (self.max_entries, self.max_entry_bytes, self.max_total_bytes, self.max_metadata_bytes):
            if type(value) is not int or value <= 0:
                raise ArchiveFormatError("archive limit is invalid")


@dataclass(frozen=True)
class _ArchiveTail:
    central_directory_offset: int
    central_directory_size: int
    entry_count: int
    central_directory_end: int


_CarrierIdentity = tuple[int, int, int, int]


class ZipEvidenceReader:
    """A reader that exposes only manifest-declared ZIP entries, never extraction."""

    def __init__(self, archive_path: Path, entries: tuple[ArchiveEntry, ...], limits: ArchiveLimits) -> None:
        self._archive_path = archive_path
        self._entries = {entry.path: entry for entry in entries}
        self._limits = limits

    def paths(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def open_binary(self, path: str) -> BinaryIO:
        entry = self._entry(path)
        carrier, identity = _prepare_archive_carrier(self._archive_path)
        archive = _open_zip(carrier)
        try:
            info = archive.getinfo(f"run/{entry.path}")
            stream = archive.open(info, "r")
        except Exception:
            archive.close()
            raise ArchiveFormatError("cannot open declared archive evidence")
        return _ArchiveMemberStream(carrier, identity, archive, stream, entry)

    def read_bytes(self, path: str, *, maximum_bytes: int = 1024 * 1024) -> bytes:
        entry = self._entry(path)
        if type(maximum_bytes) is not int or maximum_bytes < 0 or entry.byte_length > maximum_bytes:
            raise ArchiveFormatError("evidence reader byte limit is exceeded")
        with self.open_binary(path) as stream:
            return _read_exact(stream, entry.byte_length, entry.raw_sha256)

    def _entry(self, path: str) -> ArchiveEntry:
        if not isinstance(path, str) or path not in self._entries:
            raise ArchiveFormatError("evidence reader path is not declared")
        return self._entries[path]


class _ArchiveMemberStream:
    def __init__(
        self,
        archive_path: Path,
        archive_identity: _CarrierIdentity,
        archive: zipfile.ZipFile,
        stream: BinaryIO,
        entry: ArchiveEntry,
    ) -> None:
        self._archive_path = archive_path
        self._archive_identity = archive_identity
        self._archive = archive
        self._stream = stream
        self._entry = entry
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        remaining = self._entry.byte_length - self._read
        if remaining == 0:
            return b""
        if size is None or size < 0:
            size = remaining
        # BinaryIO.read(size) may request more bytes than remain.  The
        # manifest-declared member length is still the hard upper bound; cap
        # the request instead of rejecting a normal streaming read.
        size = min(size, remaining)
        raw = self._stream.read(size)
        self._read += len(raw)
        return raw

    def __enter__(self) -> "_ArchiveMemberStream":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._stream.close()
        finally:
            self._archive.close()
        _assert_archive_identity(self._archive_path, self._archive_identity)


def archive_raw_sha256(path: str | Path) -> str:
    """Return external carrier identity; it is intentionally absent from ZIP metadata."""

    carrier, expected_identity = _prepare_archive_carrier(path)
    digest = hashlib.sha256()
    try:
        with carrier.open("rb") as stream:
            if _carrier_identity_from_stat(os.fstat(stream.fileno())) != expected_identity:
                raise ArchiveFormatError("archive carrier changed during read")
            while chunk := stream.read(64 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ArchiveFormatError("cannot read archive carrier") from exc
    _assert_archive_identity(carrier, expected_identity)
    return digest.hexdigest().upper()


def validate_archive_entry_name(name: str) -> None:
    """Reject all names that could escape the archive's logical payload root."""

    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name or ":" in name or any(not part for part in name.split("/")):
        raise ArchiveFormatError("archive entry name is unsafe")
    parsed = PurePosixPath(name)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ArchiveFormatError("archive entry name is unsafe")


def verify_sqrun(
    archive_path: str | Path,
    *,
    limits: ArchiveLimits | None = None,
    verifier_registry: Mapping[tuple[str, str], EvidenceVerifier] | None = None,
    require_source_verified: bool = False,
) -> ArchiveBundle:
    """Verify ZIP structure and hash closure, then optionally rerun a registered verifier."""

    policy = limits or ArchiveLimits()
    path, expected_identity = _prepare_archive_carrier(archive_path)
    first = _verify_sqrun_once(path, policy)
    _assert_archive_identity(path, expected_identity)
    second = _verify_sqrun_once(path, policy)
    _assert_archive_identity(path, expected_identity)
    if first != second:
        raise ArchiveFormatError("archive changed between read-only verification passes")
    bundle = second
    registry = verifier_registry or {}
    verifier_id = bundle.verification_report_payload["source_verifier_id"]
    verifier_version = bundle.verification_report_payload["source_verifier_version"]
    verifier = registry.get((verifier_id, verifier_version))
    source_verified = False
    if verifier is not None:
        try:
            verifier(ZipEvidenceReader(path, bundle.entries, policy))
        except ArchiveFormatError:
            raise
        except Exception as exc:
            raise ArchiveFormatError("registered source verifier failed") from exc
        _assert_archive_identity(path, expected_identity)
        source_verified = True
    if require_source_verified and not source_verified:
        raise ArchiveFormatError("source verifier is unknown or unavailable")
    return replace(bundle, source_verified=source_verified)


def _verify_sqrun_once(path: Path, policy: ArchiveLimits) -> ArchiveBundle:
    tail = _verify_archive_tail(path)
    archive = _open_zip(path)
    try:
        infos = archive.infolist()
        _verify_zip_infos(archive, infos, policy)
        _verify_local_headers(path, infos, tail)
        documents = {name: _read_metadata(archive, archive.getinfo(name), policy) for name in _TOP_LEVEL}
        bundle = _validate_documents(archive, infos, documents, policy)
    finally:
        archive.close()
    return bundle


def read_sqrun_payload(
    archive_path: str | Path,
    path: str,
    *,
    maximum_bytes: int,
    limits: ArchiveLimits | None = None,
) -> bytes:
    """Read one declared payload file after validating the complete archive, without extraction."""

    bundle = verify_sqrun(archive_path, limits=limits)
    carrier, expected_identity = _prepare_archive_carrier(archive_path)
    reader = ZipEvidenceReader(carrier, bundle.entries, limits or ArchiveLimits())
    entry = next((row for row in bundle.entries if row.path == path), None)
    if entry is None:
        raise ArchiveFormatError("archive payload path is not declared")
    if type(maximum_bytes) is not int or maximum_bytes < 0 or entry.byte_length > maximum_bytes:
        raise ArchiveFormatError("archive payload byte limit is exceeded")
    with reader.open_binary(path) as stream:
        raw = _read_exact(stream, entry.byte_length, entry.raw_sha256)
    _assert_archive_identity(carrier, expected_identity)
    return raw


def _prepare_archive_carrier(value: str | Path) -> tuple[Path, _CarrierIdentity]:
    """Return a lexical absolute, no-follow regular archive carrier."""

    path = Path(os.path.abspath(os.fspath(value)))
    anchor = Path(path.anchor)
    if not anchor.anchor:
        raise ArchiveFormatError("archive carrier path is not absolute")
    try:
        anchor_info = anchor.lstat()
    except OSError as exc:
        raise ArchiveFormatError("archive carrier root cannot be inspected") from exc
    if _is_link_or_reparse(anchor, anchor_info):
        raise ArchiveFormatError("archive carrier contains a linked or reparse-backed component")
    current = anchor
    parts = path.parts[1:]
    if not parts:
        raise ArchiveFormatError("archive carrier is not a regular file")
    for index, part in enumerate(parts):
        try:
            matches = [entry for entry in os.scandir(current) if entry.name == part]
        except OSError as exc:
            raise ArchiveFormatError("archive carrier component cannot be inspected") from exc
        if len(matches) != 1:
            raise ArchiveFormatError("archive carrier component is missing or has incorrect case")
        entry = matches[0]
        candidate = current / entry.name
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise ArchiveFormatError("archive carrier component cannot be inspected") from exc
        if _is_link_or_reparse(candidate, info):
            raise ArchiveFormatError("archive carrier contains a linked or reparse-backed component")
        if index != len(parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                raise ArchiveFormatError("archive carrier component is not a directory")
            current = candidate
            continue
        try:
            final_info = candidate.lstat()
        except OSError as exc:
            raise ArchiveFormatError("archive carrier cannot be inspected") from exc
        if _is_link_or_reparse(candidate, final_info):
            raise ArchiveFormatError("archive carrier contains a linked or reparse-backed component")
        if not stat.S_ISREG(final_info.st_mode) or final_info.st_nlink != 1:
            raise ArchiveFormatError("archive carrier is linked or not a regular file")
        return candidate, _carrier_identity_from_stat(final_info)
    raise ArchiveFormatError("archive carrier is not a regular file")


def _carrier_identity_from_stat(info: os.stat_result) -> _CarrierIdentity:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _assert_archive_identity(path: Path, expected: _CarrierIdentity) -> None:
    _carrier, observed = _prepare_archive_carrier(path)
    if observed != expected:
        raise ArchiveFormatError("archive carrier changed during read-only verification")


def _open_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path, "r", allowZip64=True)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveFormatError("archive central directory is invalid") from exc


def _verify_archive_tail(path: Path) -> _ArchiveTail:
    try:
        size = path.stat().st_size
        if size < 22:
            raise ArchiveFormatError("archive central directory is truncated")
        with path.open("rb") as stream:
            window = min(size, 22 + 0xFFFF)
            stream.seek(-window, 2)
            tail = stream.read(window)
    except OSError as exc:
        raise ArchiveFormatError("cannot inspect archive carrier") from exc
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or offset + 22 > len(tail):
        raise ArchiveFormatError("archive has trailing data or truncated central directory")
    record = tail[offset:offset + 22]
    comment_length = int.from_bytes(record[20:22], "little")
    if offset + 22 + comment_length != len(tail):
        raise ArchiveFormatError("archive has trailing data or truncated central directory")
    if comment_length:
        raise ArchiveFormatError("archive ZIP comment is forbidden")
    if any(int.from_bytes(record[start:start + 2], "little") != 0 for start in (4, 6)):
        raise ArchiveFormatError("archive multi-disk layout is forbidden")
    entries_on_disk = int.from_bytes(record[8:10], "little")
    entry_count = int.from_bytes(record[10:12], "little")
    central_size = int.from_bytes(record[12:16], "little")
    central_offset = int.from_bytes(record[16:20], "little")
    if entries_on_disk != entry_count:
        raise ArchiveFormatError("archive multi-disk entry count is invalid")
    eocd_offset = size - 22
    needs_zip64 = (
        entries_on_disk == 0xFFFF
        or entry_count == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    )
    if not needs_zip64:
        if central_offset + central_size != eocd_offset:
            raise ArchiveFormatError("archive has SFX prefix or central-directory gap")
        return _ArchiveTail(central_offset, central_size, entry_count, eocd_offset)

    locator_offset = eocd_offset - 20
    try:
        with path.open("rb") as stream:
            stream.seek(locator_offset)
            locator = stream.read(20)
            if len(locator) != 20 or locator[:4] != b"PK\x06\x07":
                raise ArchiveFormatError("archive ZIP64 locator is invalid")
            _signature, disk_number, zip64_offset, disk_count = struct.unpack("<IIQI", locator)
            if disk_number != 0 or disk_count != 1:
                raise ArchiveFormatError("archive multi-disk layout is forbidden")
            stream.seek(zip64_offset)
            header = stream.read(56)
    except OSError as exc:
        raise ArchiveFormatError("cannot inspect archive ZIP64 tail") from exc
    if len(header) != 56 or header[:4] != b"PK\x06\x06":
        raise ArchiveFormatError("archive ZIP64 end record is invalid")
    (
        _signature,
        record_size,
        _made_by,
        _needed,
        disk_number,
        central_disk,
        zip64_entries_on_disk,
        zip64_entry_count,
        zip64_central_size,
        zip64_central_offset,
    ) = struct.unpack("<IQHHIIQQQQ", header)
    if record_size != 44 or disk_number != 0 or central_disk != 0 or zip64_entries_on_disk != zip64_entry_count:
        raise ArchiveFormatError("archive ZIP64 end record is invalid")
    if zip64_offset + 56 != locator_offset or zip64_central_offset + zip64_central_size != zip64_offset:
        raise ArchiveFormatError("archive central-directory gap is forbidden")
    if entries_on_disk != 0xFFFF and entries_on_disk != zip64_entries_on_disk:
        raise ArchiveFormatError("archive ZIP64 entry count is inconsistent")
    if entry_count != 0xFFFF and entry_count != zip64_entry_count:
        raise ArchiveFormatError("archive ZIP64 entry count is inconsistent")
    if central_size != 0xFFFFFFFF and central_size != zip64_central_size:
        raise ArchiveFormatError("archive ZIP64 central size is inconsistent")
    if central_offset != 0xFFFFFFFF and central_offset != zip64_central_offset:
        raise ArchiveFormatError("archive ZIP64 central offset is inconsistent")
    return _ArchiveTail(zip64_central_offset, zip64_central_size, zip64_entry_count, zip64_offset)


def _verify_zip_infos(archive: zipfile.ZipFile, infos: list[zipfile.ZipInfo], limits: ArchiveLimits) -> None:
    if archive.comment:
        raise ArchiveFormatError("archive ZIP comment is forbidden")
    if not infos or len(infos) > limits.max_entries:
        raise ArchiveFormatError("archive entry count exceeds limit")
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise ArchiveFormatError("archive has duplicate entry names")
    if len({name.casefold() for name in names}) != len(names):
        raise ArchiveFormatError("archive has case-colliding entry names")
    if set(names[:4]) != set(_TOP_LEVEL) or any(not name.startswith("run/") for name in names if name not in _TOP_LEVEL):
        raise ArchiveFormatError("archive top-level entry layout is invalid")
    if names != sorted(names, key=lambda value: value.encode("utf-8")):
        raise ArchiveFormatError("archive entries are not UTF-8-byte ordered")
    for info in infos:
        validate_archive_entry_name(info.filename)
        if info.is_dir():
            raise ArchiveFormatError("archive directory entries are forbidden")
        if info.compress_type != zipfile.ZIP_STORED:
            raise ArchiveFormatError("archive compression is not stored")
        if info.flag_bits & 0x1:
            raise ArchiveFormatError("archive entry is encrypted")
        if info.flag_bits & 0x8:
            raise ArchiveFormatError("archive data descriptor is forbidden")
        if info.flag_bits not in {0, 0x800}:
            raise ArchiveFormatError("archive entry flags or filename encoding are invalid")
        expected_flags = 0x800 if not info.filename.isascii() else 0
        if info.flag_bits != expected_flags:
            raise ArchiveFormatError("archive entry flags or filename encoding are invalid")
        if info.date_time != (1980, 1, 1, 0, 0, 0):
            raise ArchiveFormatError("archive timestamp is non-deterministic")
        mode = (info.external_attr >> 16) & 0o170000
        if mode in {0o120000, 0o020000, 0o060000}:
            raise ArchiveFormatError("archive contains a link or special entry")
        if (
            info.create_system != 3
            or info.create_version != 45
            or info.extract_version != 45
            or info.external_attr != (0o100644 << 16)
            or info.internal_attr != 0
        ):
            raise ArchiveFormatError("archive platform metadata is invalid")
        _validate_zip64_extra(info)
        if info.file_size > limits.max_entry_bytes:
            raise ArchiveFormatError("archive entry size exceeds limit")


def _validate_zip64_extra(info: zipfile.ZipInfo) -> None:
    extra = info.extra
    position = 0
    zip64_count = 0
    while position < len(extra):
        if position + 4 > len(extra):
            raise ArchiveFormatError("archive extra field is malformed")
        field_id = int.from_bytes(extra[position:position + 2], "little")
        length = int.from_bytes(extra[position + 2:position + 4], "little")
        position += 4
        if position + length > len(extra):
            raise ArchiveFormatError("archive extra field is malformed")
        if field_id != 0x0001 or length not in {8, 16, 24, 28}:
            raise ArchiveFormatError("archive has unknown extra field")
        zip64_count += 1
        position += length
    if position != len(extra) or zip64_count > 1:
        raise ArchiveFormatError("archive extra field is invalid")


def _verify_local_headers(path: Path, infos: list[zipfile.ZipInfo], tail: _ArchiveTail) -> None:
    """Bind each central record to an adjacent deterministic local header."""

    cursor = 0
    try:
        with path.open("rb") as stream:
            for info in infos:
                if info.header_offset != cursor:
                    raise ArchiveFormatError("archive has SFX prefix or local-header gap")
                stream.seek(cursor)
                fixed = stream.read(30)
                if len(fixed) != 30 or fixed[:4] != b"PK\x03\x04":
                    raise ArchiveFormatError("archive local header is invalid")
                (_signature, version, flags, method, packed_time, packed_date, crc, compressed, uncompressed, name_length, extra_length) = struct.unpack("<IHHHHHIIIHH", fixed)
                if version != 45 or flags != info.flag_bits or method != info.compress_type or packed_time != 0 or packed_date != 33 or crc != info.CRC:
                    raise ArchiveFormatError("archive local header does not match central directory")
                encoded_name = info.filename.encode("utf-8") if flags & 0x800 else info.filename.encode("cp437")
                if stream.read(name_length) != encoded_name:
                    raise ArchiveFormatError("archive local header name does not match central directory")
                extra = stream.read(extra_length)
                expected_extra = struct.pack("<HHQQ", 0x0001, 16, info.file_size, info.compress_size)
                if compressed != 0xFFFFFFFF or uncompressed != 0xFFFFFFFF or extra != expected_extra:
                    raise ArchiveFormatError("archive local ZIP64 extra field is invalid")
                cursor += 30 + name_length + extra_length + info.compress_size
            local_end = cursor
            if local_end != tail.central_directory_offset:
                raise ArchiveFormatError("archive central-directory gap is forbidden")
            stream.seek(cursor)
            if stream.read(4) != b"PK\x01\x02":
                raise ArchiveFormatError("archive has central-directory gap")
            stream.seek(cursor)
            for info in infos:
                fixed = stream.read(46)
                if len(fixed) != 46 or fixed[:4] != b"PK\x01\x02":
                    raise ArchiveFormatError("archive central directory is invalid")
                (
                    _signature,
                    made_by,
                    version,
                    flags,
                    method,
                    packed_time,
                    packed_date,
                    crc,
                    compressed,
                    uncompressed,
                    name_length,
                    extra_length,
                    comment_length,
                    disk_start,
                    internal_attr,
                    external_attr,
                    header_offset,
                ) = struct.unpack("<IHHHHHHIIIHHHHHII", fixed)
                encoded_name = info.filename.encode("utf-8") if flags & 0x800 else info.filename.encode("cp437")
                if (
                    made_by != ((3 << 8) | 45)
                    or version != 45
                    or flags != info.flag_bits
                    or method != info.compress_type
                    or packed_time != 0
                    or packed_date != 33
                    or crc != info.CRC
                    or internal_attr != info.internal_attr
                    or external_attr != info.external_attr
                    or comment_length != 0
                    or stream.read(name_length) != encoded_name
                ):
                    raise ArchiveFormatError("archive central directory does not match local header")
                extra = stream.read(extra_length)
                expected_parts: list[bytes] = []
                if uncompressed == 0xFFFFFFFF:
                    expected_parts.append(struct.pack("<Q", info.file_size))
                elif uncompressed != info.file_size:
                    raise ArchiveFormatError("archive central uncompressed size is invalid")
                if compressed == 0xFFFFFFFF:
                    expected_parts.append(struct.pack("<Q", info.compress_size))
                elif compressed != info.compress_size:
                    raise ArchiveFormatError("archive central compressed size is invalid")
                if header_offset == 0xFFFFFFFF:
                    expected_parts.append(struct.pack("<Q", info.header_offset))
                elif header_offset != info.header_offset:
                    raise ArchiveFormatError("archive central local offset is invalid")
                if disk_start == 0xFFFF:
                    expected_parts.append(struct.pack("<I", 0))
                elif disk_start != 0:
                    raise ArchiveFormatError("archive multi-disk entry is forbidden")
                expected_extra = (
                    struct.pack("<HH", 0x0001, sum(len(part) for part in expected_parts)) + b"".join(expected_parts)
                    if expected_parts else b""
                )
                if extra != expected_extra:
                    raise ArchiveFormatError("archive central ZIP64 extra field is invalid")
                cursor = stream.tell()
            if cursor != tail.central_directory_end:
                raise ArchiveFormatError("archive central-directory gap is forbidden")
            if tail.central_directory_size != tail.central_directory_end - tail.central_directory_offset:
                raise ArchiveFormatError("archive central directory size is invalid")
            if tail.entry_count != len(infos):
                raise ArchiveFormatError("archive central directory entry count is invalid")
    except OSError as exc:
        raise ArchiveFormatError("cannot inspect archive local headers") from exc


def _read_metadata(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limits: ArchiveLimits) -> dict[str, Any]:
    if info.file_size > limits.max_metadata_bytes:
        raise ArchiveFormatError("archive metadata exceeds limit")
    raw = _read_stream(archive, info, info.file_size)
    return _strict_json(raw, info.filename)


def _validate_documents(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    documents: Mapping[str, Mapping[str, Any]],
    limits: ArchiveLimits,
) -> ArchiveBundle:
    format_payload = documents["format.json"]
    manifest = documents["bundle-manifest.json"]
    report = documents["bundle-verification-report.json"]
    receipt = documents["bundle-receipt.json"]
    _validate_format(format_payload)
    entries = _validate_manifest(manifest, infos, limits)
    _validate_report(report, manifest)
    _validate_receipt(receipt, manifest, report)
    run_id = manifest["run_id"]
    if not (format_payload["run_id"] == report["run_id"] == receipt["run_id"] == run_id):
        raise ArchiveFormatError("archive run identity does not agree")
    total = 0
    for entry in entries:
        info = archive.getinfo(f"run/{entry.path}")
        with _archive_stream(archive, info) as stream:
            _verify_stream(stream, entry.byte_length, entry.raw_sha256)
        total += entry.byte_length
        if total > limits.max_total_bytes:
            raise ArchiveFormatError("archive total size exceeds limit")
    if total != manifest["logical_bytes"]:
        raise ArchiveFormatError("archive logical size does not match manifest")
    by_path = {entry.path: entry for entry in entries}
    if by_path["workflow.json"].raw_sha256 != manifest["workflow_sha256"] or by_path["receipt.json"].raw_sha256 != manifest["receipt_sha256"]:
        raise ArchiveFormatError("archive workflow or receipt hash is invalid")
    return ArchiveBundle(
        run_id=run_id,
        workflow_id=manifest["workflow_id"],
        workflow_sha256=manifest["workflow_sha256"],
        receipt_sha256=manifest["receipt_sha256"],
        source_artifact_version=manifest["source_artifact_version"],
        original_directory_name=manifest["original_directory_name"],
        entries=entries,
        logical_bytes=total,
        format_payload=format_payload,
        manifest_payload=manifest,
        verification_report_payload=report,
        receipt_payload=receipt,
    )


def _validate_format(value: Mapping[str, Any]) -> None:
    _exact_fields(value, _FORMAT_FIELDS, "format")
    if type(value.get("zip64")) is not bool:
        raise ArchiveFormatError("archive format metadata is invalid")
    if value != {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "artifact_type": "sqvm_experiment_bundle_format",
        "artifact_version": ARCHIVE_SCHEMA_VERSION,
        "format_version": ARCHIVE_FORMAT_VERSION,
        "run_id": value.get("run_id"),
        "payload_prefix": "run/",
        "compression": "stored",
        "zip64": True,
        "entry_order": "utf8_posix_path_bytes",
    }:
        raise ArchiveFormatError("archive format metadata is invalid")
    _uuid(value["run_id"], "format run_id")


def _validate_manifest(value: Mapping[str, Any], infos: list[zipfile.ZipInfo], limits: ArchiveLimits) -> tuple[ArchiveEntry, ...]:
    _exact_fields(value, _MANIFEST_FIELDS, "manifest")
    _exact(value, "schema_version", ARCHIVE_SCHEMA_VERSION)
    _exact(value, "artifact_type", "sqvm_experiment_bundle_manifest")
    _exact(value, "artifact_version", ARCHIVE_SCHEMA_VERSION)
    _uuid(value["run_id"], "manifest run_id")
    _nonempty_text(value["workflow_id"], "workflow_id")
    _nonempty_text(value["source_artifact_version"], "source_artifact_version")
    _single_component(value["original_directory_name"])
    _sha(value["workflow_sha256"], "workflow_sha256")
    _sha(value["receipt_sha256"], "receipt_sha256")
    if value.get("compression") != {"method": "stored", "level": None, "writer_version": ARCHIVE_WRITER_VERSION}:
        raise ArchiveFormatError("manifest compression is invalid")
    rows = value["entries"]
    if not isinstance(rows, list) or not rows or len(rows) > limits.max_entries:
        raise ArchiveFormatError("manifest entry count is invalid")
    entries: list[ArchiveEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ArchiveFormatError("manifest entry is invalid")
        _exact_fields(row, _ENTRY_FIELDS, "manifest entry")
        validate_archive_entry_name(row["path"])
        if type(row["byte_length"]) is not int or row["byte_length"] < 0 or row["byte_length"] > limits.max_entry_bytes:
            raise ArchiveFormatError("manifest entry size is invalid")
        _sha(row["raw_sha256"], "manifest entry hash")
        entries.append(ArchiveEntry(row["path"], row["byte_length"], row["raw_sha256"]))
    paths = [entry.path for entry in entries]
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")) or len(set(paths)) != len(paths):
        raise ArchiveFormatError("manifest paths are not uniquely ordered")
    if [f"run/{entry.path}" for entry in entries] != [info.filename for info in infos if info.filename.startswith("run/")]:
        raise ArchiveFormatError("manifest does not close archive payload paths")
    if value["entry_count"] != len(entries) or type(value["entry_count"]) is not int:
        raise ArchiveFormatError("manifest entry count does not match")
    if type(value["logical_bytes"]) is not int or value["logical_bytes"] < 0:
        raise ArchiveFormatError("manifest logical size is invalid")
    return tuple(entries)


def _validate_report(value: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    _exact_fields(value, _REPORT_FIELDS, "verification report")
    _exact(value, "schema_version", ARCHIVE_SCHEMA_VERSION)
    _exact(value, "artifact_type", "sqvm_experiment_bundle_verification_report")
    _exact(value, "artifact_version", ARCHIVE_SCHEMA_VERSION)
    if value["run_id"] != manifest["run_id"] or value["ok"] is not True or value["blocking_reasons"] != [] or not isinstance(value["checks"], list):
        raise ArchiveFormatError("verification report is invalid")
    _nonempty_text(value["source_verifier_id"], "source_verifier_id")
    _nonempty_text(value["source_verifier_version"], "source_verifier_version")
    if value["bundle_manifest_sha256"] != sha256_bytes(canonical_archive_json_bytes(manifest)):
        raise ArchiveFormatError("verification report manifest hash is invalid")


def _validate_receipt(value: Mapping[str, Any], manifest: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    _exact_fields(value, _RECEIPT_FIELDS, "bundle receipt")
    _exact(value, "schema_version", ARCHIVE_SCHEMA_VERSION)
    _exact(value, "artifact_type", "sqvm_experiment_bundle_receipt")
    _exact(value, "artifact_version", ARCHIVE_SCHEMA_VERSION)
    if value["run_id"] != manifest["run_id"] or value["source_verifier_id"] != report["source_verifier_id"] or value["source_verifier_version"] != report["source_verifier_version"]:
        raise ArchiveFormatError("bundle receipt identity is invalid")
    if value["bundle_manifest_sha256"] != sha256_bytes(canonical_archive_json_bytes(manifest)) or value["bundle_verification_report_sha256"] != sha256_bytes(canonical_archive_json_bytes(report)):
        raise ArchiveFormatError("bundle receipt hash topology is invalid")
    if value["workflow_sha256"] != manifest["workflow_sha256"] or value["receipt_sha256"] != manifest["receipt_sha256"] or value["entry_count"] != manifest["entry_count"] or value["logical_bytes"] != manifest["logical_bytes"]:
        raise ArchiveFormatError("bundle receipt does not match manifest")


def _archive_stream(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> BinaryIO:
    try:
        return archive.open(info, "r")
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise ArchiveFormatError("cannot stream archive entry") from exc


def _read_stream(archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected: int) -> bytes:
    with _archive_stream(archive, info) as stream:
        return _read_exact(stream, expected, None)


def _read_exact(stream: BinaryIO, expected: int, digest: str | None) -> bytes:
    remaining = expected
    chunks: list[bytes] = []
    hasher = hashlib.sha256()
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            raise ArchiveFormatError("archive entry is truncated")
        chunks.append(chunk)
        hasher.update(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise ArchiveFormatError("archive entry exceeds declared length")
    raw = b"".join(chunks)
    if digest is not None and hasher.hexdigest().upper() != digest:
        raise ArchiveFormatError("archive entry hash does not match")
    return raw


def _verify_stream(stream: BinaryIO, expected: int, digest: str) -> None:
    remaining = expected
    hasher = hashlib.sha256()
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            raise ArchiveFormatError("archive entry is truncated")
        hasher.update(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise ArchiveFormatError("archive entry exceeds declared length")
    if hasher.hexdigest().upper() != digest:
        raise ArchiveFormatError("archive entry hash does not match")


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ArchiveFormatError(f"{label} has duplicate JSON key")
            result[key] = item
        return result
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ArchiveFormatError) as exc:
        raise ArchiveFormatError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or raw != canonical_archive_json_bytes(value):
        raise ArchiveFormatError(f"{label} JSON is not canonical")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ArchiveFormatError(f"{label} fields are not exact")


def _exact(value: Mapping[str, Any], key: str, expected: object) -> None:
    if value.get(key) != expected:
        raise ArchiveFormatError(f"{key} is invalid")


def _uuid(value: Any, label: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ArchiveFormatError(f"{label} is invalid") from exc
    if not isinstance(value, str) or str(parsed) != value:
        raise ArchiveFormatError(f"{label} is invalid")


def _sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[A-F0-9]{64}", value) is None:
        raise ArchiveFormatError(f"{label} is invalid")


def _nonempty_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ArchiveFormatError(f"{label} is invalid")


def _single_component(value: Any) -> None:
    if not isinstance(value, str) or not value or value in {".", ".."} or any(token in value for token in ("/", "\\", ":", "\x00")):
        raise ArchiveFormatError("original directory name is invalid")


__all__ = [
    "ArchiveLimits", "ZipEvidenceReader", "archive_raw_sha256", "read_sqrun_payload",
    "validate_archive_entry_name", "verify_sqrun",
]
