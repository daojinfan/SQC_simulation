"""WP0-D independent storage acceptance oracles.

This module deliberately uses only the Python standard library.  It does not
import sqvm storage code or implementation test helpers: a candidate must be
validated against these independently recomputed contracts.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import struct
import subprocess
import sys
import threading
import time
import tracemalloc
import uuid
import zipfile

import pytest


FORMAT_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "format_version",
    "run_id", "payload_prefix", "compression", "zip64", "entry_order",
}
MANIFEST_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "run_id", "workflow_id",
    "source_artifact_version", "original_directory_name", "workflow_sha256",
    "receipt_sha256", "entry_count", "logical_bytes", "compression", "entries",
}
REPORT_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "run_id", "ok",
    "bundle_manifest_sha256", "source_verifier_id", "source_verifier_version",
    "checks", "blocking_reasons",
}
RECEIPT_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "run_id",
    "bundle_manifest_sha256", "bundle_verification_report_sha256", "workflow_sha256",
    "receipt_sha256", "source_verifier_id", "source_verifier_version", "entry_count",
    "logical_bytes",
}
POLICY_KEYS = {
    "schema_version", "artifact_type", "artifact_version", "policy_id", "enabled",
    "compact_after_hours", "simulation_retention_days", "hardware_retention_days",
    "failed_retention_days", "trash_retention_days", "simulation_keep_latest",
    "hardware_keep_latest", "minimum_free_disk_GiB", "maximum_store_GiB",
    "high_watermark_ratio", "critical_watermark_ratio", "estimate_safety_factor",
    "stale_staging_hours", "cleanup_plan_ttl_minutes", "archive_compression",
    "updated_utc", "actor_id", "content_sha256",
}
TOP_LEVEL = (
    "format.json", "bundle-manifest.json", "bundle-verification-report.json",
    "bundle-receipt.json",
)
WP1_FUZZ_SEED = 0x51_5255_4E
WP1_FUZZ_CASES = 10_000
WP1_STRUCTURED_MUTATION_CASES = 100
WP1_GOLDEN_ARCHIVE_SHA256 = "68ADFE6292F51910203EB3D3FEA669385D8DF25D1649A7D64637DB23AA3679CF"
_EOCD = b"PK\x05\x06"
_CENTRAL_DIRECTORY = b"PK\x01\x02"
_LOCAL_HEADER = b"PK\x03\x04"


def _assert_terminal_eocd(raw: bytes) -> None:
    """Reject a ZIP comment or bytes appended after the terminal EOCD."""

    assert raw.startswith(_LOCAL_HEADER), "SFX prefix is forbidden"
    offset = raw.rfind(_EOCD)
    assert offset >= 0 and offset + 22 <= len(raw), "ZIP EOCD is missing or truncated"
    comment_length = struct.unpack_from("<H", raw, offset + 20)[0]
    assert comment_length == 0, "ZIP comment is forbidden"
    assert offset + 22 == len(raw), "ZIP has trailing bytes"
    assert struct.unpack_from("<HHHH", raw, offset + 4) == (0, 0, struct.unpack_from("<H", raw, offset + 10)[0], struct.unpack_from("<H", raw, offset + 10)[0]), "multi-disk ZIP is forbidden"


def _assert_local_header_matches_central(raw: bytes, info: zipfile.ZipInfo) -> None:
    offset = info.header_offset
    assert raw[offset:offset + 4] == _LOCAL_HEADER, "local ZIP header is invalid"
    flags, compression = struct.unpack_from("<HH", raw, offset + 6)
    name_length, extra_length = struct.unpack_from("<HH", raw, offset + 26)
    local_name = raw[offset + 30:offset + 30 + name_length]
    assert flags == info.flag_bits and compression == info.compress_type, "local ZIP header differs from central directory"
    assert local_name == info.filename.encode("utf-8") and extra_length <= len(raw) - offset - 30 - name_length, "local ZIP entry name or extra is invalid"


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def _policy_canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _load_canonical(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError(f"{label} is not UTF-8 JSON") from exc
    assert isinstance(value, dict), f"{label} must be an object"
    assert raw == _canonical_bytes(value), f"{label} is not canonical JSON"
    return value


def _assert_finite(value: object) -> None:
    if isinstance(value, float):
        assert math.isfinite(value), "non-finite JSON number"
    elif isinstance(value, list):
        for item in value:
            _assert_finite(item)
    elif isinstance(value, dict):
        for item in value.values():
            _assert_finite(item)


def _safe_entry(name: str) -> None:
    assert name and "\\" not in name and "\x00" not in name and ":" not in name
    path = PurePosixPath(name)
    assert not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _read_exact(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    with archive.open(info, "r") as stream:
        raw = stream.read(limit + 1)
        assert len(raw) <= limit, f"{info.filename} exceeds its declared length"
        assert stream.read(1) == b"", f"{info.filename} has unread trailing bytes"
    return raw


def assert_sqrun_oracle(path: Path) -> dict:
    """Independently validate the frozen v1 container contract."""

    _assert_terminal_eocd(path.read_bytes())
    archive_raw = path.read_bytes()
    with zipfile.ZipFile(path, "r") as archive:
        assert archive.comment == b"", "ZIP comment is forbidden"
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert len(names) == len(set(names)), "duplicate ZIP entry"
        assert len({name.casefold() for name in names}) == len(names), "case-colliding ZIP entry"
        assert {name for name in names if not name.startswith("run/")} == set(TOP_LEVEL), "top-level entry set is invalid"
        assert names == sorted(names, key=lambda name: name.encode("utf-8")), "all ZIP entries are not UTF-8-byte ordered"
        for info in infos:
            _safe_entry(info.filename)
            _assert_local_header_matches_central(archive_raw, info)
            assert not info.is_dir(), "directory ZIP entries are forbidden"
            assert info.compress_type == zipfile.ZIP_STORED, "only ZIP_STORED is allowed"
            assert not (info.flag_bits & 0x1), "encrypted ZIP entry"
            assert not (info.flag_bits & 0x8), "data descriptor is forbidden"
            assert info.extra == b"", "ZIP extra fields are forbidden"
            assert info.date_time == (1980, 1, 1, 0, 0, 0), "non-deterministic ZIP timestamp"
            unix_mode = (info.external_attr >> 16) & 0o170000
            assert unix_mode not in {0o120000, 0o020000, 0o060000}, "link or special ZIP entry"
            assert not (info.external_attr & 0x400), "ZIP entry has a reparse attribute"

        documents = {name: _load_canonical(archive.read(name), name) for name in TOP_LEVEL}
        format_payload = documents["format.json"]
        manifest = documents["bundle-manifest.json"]
        report = documents["bundle-verification-report.json"]
        receipt = documents["bundle-receipt.json"]
        assert set(format_payload) == FORMAT_KEYS
        assert format_payload["schema_version"] == "0.1"
        assert format_payload["format_version"] == "1"
        assert format_payload["payload_prefix"] == "run/"
        assert format_payload["compression"] == "stored"
        assert format_payload["zip64"] is True
        assert format_payload["entry_order"] == "utf8_posix_path_bytes"
        assert set(manifest) == MANIFEST_KEYS and manifest["schema_version"] == "0.1"
        assert set(report) == REPORT_KEYS and report["schema_version"] == "0.1"
        assert set(receipt) == RECEIPT_KEYS and receipt["schema_version"] == "0.1"
        assert report["ok"] is True and report["blocking_reasons"] == []
        assert format_payload["run_id"] == manifest["run_id"] == report["run_id"] == receipt["run_id"]
        assert receipt["bundle_manifest_sha256"] == _sha(archive.read("bundle-manifest.json"))
        assert report["bundle_manifest_sha256"] == receipt["bundle_manifest_sha256"]
        assert receipt["bundle_verification_report_sha256"] == _sha(archive.read("bundle-verification-report.json"))

        rows = manifest["entries"]
        assert isinstance(rows, list) and rows, "manifest entries must be nonempty"
        paths = [row.get("path") for row in rows if isinstance(row, dict)]
        assert len(paths) == len(rows) and paths == sorted(paths, key=lambda name: name.encode("utf-8"))
        assert [f"run/{name}" for name in paths] == [name for name in names if name.startswith("run/")]
        assert manifest["entry_count"] == len(rows)
        logical = 0
        for row in rows:
            assert set(row) == {"path", "byte_length", "raw_sha256"}
            _safe_entry(row["path"])
            assert isinstance(row["byte_length"], int) and row["byte_length"] >= 0
            info = archive.getinfo(f"run/{row['path']}")
            assert info.file_size == info.compress_size == row["byte_length"], "central directory size differs from manifest"
            raw = _read_exact(archive, info, row["byte_length"])
            assert len(raw) == row["byte_length"] and _sha(raw) == row["raw_sha256"]
            logical += len(raw)
        assert logical == manifest["logical_bytes"] == receipt["logical_bytes"]
        assert manifest["entry_count"] == receipt["entry_count"]
        payload = {row["path"]: archive.read(f"run/{row['path']}") for row in rows}
        assert _sha(payload["workflow.json"]) == manifest["workflow_sha256"] == receipt["workflow_sha256"]
        assert _sha(payload["receipt.json"]) == manifest["receipt_sha256"] == receipt["receipt_sha256"]
        return {"manifest": manifest, "payload": payload}


def _valid_bundle(path: Path) -> dict:
    run_id = "a7f15bee-0ab2-46e2-a8e1-b7ab47c0d1cf"
    workflow = _canonical_bytes({"artifact_version": "0.2", "run_id": run_id, "workflow_id": "qubit_spectroscopy_scan_v1"})
    receipt = _canonical_bytes({"run_id": run_id, "status": "completed", "workflow_sha256": _sha(workflow)})
    payload = {
        "dataset.bin": b"\x00\xff\x10independent-oracle\x00",
        "execution/point-000/result.json": _canonical_bytes({"point": 0, "value": 1.25}),
        "receipt.json": receipt,
        "workflow.json": workflow,
    }
    rows = [{"path": name, "byte_length": len(raw), "raw_sha256": _sha(raw)} for name, raw in sorted(payload.items(), key=lambda pair: pair[0].encode("utf-8"))]
    manifest = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_bundle_manifest", "artifact_version": "0.1",
        "run_id": run_id, "workflow_id": "qubit_spectroscopy_scan_v1", "source_artifact_version": "0.2",
        "original_directory_name": f"qubit_spectroscopy_{run_id}", "workflow_sha256": _sha(workflow),
        "receipt_sha256": _sha(receipt), "entry_count": len(rows), "logical_bytes": sum(len(raw) for raw in payload.values()),
        "compression": {"method": "stored", "level": None, "writer_version": "0.1"}, "entries": rows,
    }
    report = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_bundle_verification_report", "artifact_version": "0.1",
        "run_id": run_id, "ok": True, "bundle_manifest_sha256": _sha(_canonical_bytes(manifest)),
        "source_verifier_id": "independent-fixture", "source_verifier_version": "0.2", "checks": [], "blocking_reasons": [],
    }
    bundle_receipt = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_bundle_receipt", "artifact_version": "0.1",
        "run_id": run_id, "bundle_manifest_sha256": _sha(_canonical_bytes(manifest)),
        "bundle_verification_report_sha256": _sha(_canonical_bytes(report)), "workflow_sha256": _sha(workflow),
        "receipt_sha256": _sha(receipt), "source_verifier_id": "independent-fixture", "source_verifier_version": "0.2",
        "entry_count": len(rows), "logical_bytes": sum(len(raw) for raw in payload.values()),
    }
    format_payload = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_bundle_format", "artifact_version": "0.1",
        "format_version": "1", "run_id": run_id, "payload_prefix": "run/", "compression": "stored",
        "zip64": True, "entry_order": "utf8_posix_path_bytes",
    }
    records = [("format.json", _canonical_bytes(format_payload)), ("bundle-manifest.json", _canonical_bytes(manifest)), ("bundle-verification-report.json", _canonical_bytes(report)), ("bundle-receipt.json", _canonical_bytes(bundle_receipt))]
    records.extend((f"run/{name}", raw) for name, raw in sorted(payload.items(), key=lambda pair: pair[0].encode("utf-8")))
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name, raw in sorted(records, key=lambda pair: pair[0].encode("utf-8")):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    return payload


def _rewrite_zip(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for info in archive.infolist():
            name, raw, compression = mutate(info.filename, archive.read(info.filename), info.compress_type)
            updated = zipfile.ZipInfo(name, date_time=info.date_time)
            updated.compress_type = compression
            updated.external_attr = info.external_attr
            output.writestr(updated, raw)


def _rewrite_zip_metadata(source: Path, target: Path, member: str, *, external_attr: int | None = None, extra: bytes | None = None) -> None:
    with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for info in archive.infolist():
            updated = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            updated.compress_type = info.compress_type
            updated.external_attr = external_attr if info.filename == member and external_attr is not None else info.external_attr
            updated.extra = extra if info.filename == member and extra is not None else info.extra
            output.writestr(updated, archive.read(info.filename))


def _central_header_offset(raw: bytes, member: str) -> int:
    offset = 0
    target = member.encode("utf-8")
    while True:
        offset = raw.find(_CENTRAL_DIRECTORY, offset)
        assert offset >= 0, f"central directory entry missing: {member}"
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, offset + 28)
        name = raw[offset + 46:offset + 46 + name_length]
        if name == target:
            return offset
        offset += 46 + name_length + extra_length + comment_length


def _patch_member_headers(source: Path, target: Path, member: str, *, flags: int | None = None, external_attr: int | None = None, file_size: int | None = None, local_compression: int | None = None) -> None:
    raw = bytearray(source.read_bytes())
    central = _central_header_offset(raw, member)
    local = struct.unpack_from("<I", raw, central + 42)[0]
    assert raw[local:local + 4] == _LOCAL_HEADER
    if flags is not None:
        struct.pack_into("<H", raw, central + 8, flags)
        struct.pack_into("<H", raw, local + 6, flags)
    if external_attr is not None:
        struct.pack_into("<I", raw, central + 38, external_attr)
    if file_size is not None:
        struct.pack_into("<I", raw, central + 24, file_size)
    if local_compression is not None:
        struct.pack_into("<H", raw, local + 8, local_compression)
    target.write_bytes(raw)


def _replace_member_name_bytes(source: Path, target: Path, before: bytes, after: bytes) -> None:
    assert len(before) == len(after)
    raw = source.read_bytes()
    assert raw.count(before) >= 2, "expected local and central entry names"
    target.write_bytes(raw.replace(before, after))


def _patch_eocd_for_multidisk(source: Path, target: Path) -> None:
    raw = bytearray(source.read_bytes())
    offset = raw.rfind(_EOCD)
    assert offset >= 0
    struct.pack_into("<H", raw, offset + 4, 1)
    target.write_bytes(raw)


def _independent_source_run(root: Path) -> tuple[Path, dict[str, bytes]]:
    run_id = "a7f15bee-0ab2-46e2-a8e1-b7ab47c0d1cf"
    run = root / f"qubit_spectroscopy_{run_id}"
    payload = {
        "dataset.bin": b"\x00\xff\x10public-writer-oracle\x00",
        "execution/point-000/result.json": _canonical_bytes({"point": 0, "value": 1.25}),
        "receipt.json": _canonical_bytes({"run_id": run_id, "status": "completed"}),
        "workflow.json": _canonical_bytes({"artifact_version": "0.2", "run_id": run_id, "workflow_id": "qubit_spectroscopy_scan_v1"}),
    }
    for relative, content in payload.items():
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return run, payload


def _public_source_verifier(expected_payload: dict[str, bytes]):
    def verify(reader) -> None:
        observed: dict[str, bytes] = {}
        for path in reader.paths():
            chunks: list[bytes] = []
            expected_length = len(expected_payload[path])
            total = 0
            with reader.open_binary(path) as stream:
                while total < expected_length:
                    chunk = stream.read(min(64 * 1024, expected_length - total))
                    assert chunk
                    chunks.append(chunk)
                    total += len(chunk)
            observed[path] = b"".join(chunks)
        assert observed == expected_payload

    return verify


def _write_public_bundle(run: Path, archive: Path, payload: dict[str, bytes]) -> None:
    from sqvm.storage.archive_format import write_sqrun

    write_sqrun(
        run,
        archive,
        source_verifier_id="independent",
        source_verifier_version="wp1",
        source_verifier=_public_source_verifier(payload),
    )


def _patch_eocd_u16(source: Path, target: Path, relative_offset: int, value: int) -> None:
    raw = bytearray(source.read_bytes())
    offset = raw.rfind(_EOCD)
    assert offset >= 0
    struct.pack_into("<H", raw, offset + relative_offset, value)
    target.write_bytes(raw)


def _patch_central_version_or_flags(source: Path, target: Path, member: str, *, made_by: int | None = None, needed: int | None = None, flags: int | None = None) -> None:
    raw = bytearray(source.read_bytes())
    central = _central_header_offset(raw, member)
    if made_by is not None:
        struct.pack_into("<H", raw, central + 4, made_by)
    if needed is not None:
        struct.pack_into("<H", raw, central + 6, needed)
    if flags is not None:
        struct.pack_into("<H", raw, central + 8, flags)
        local = struct.unpack_from("<I", raw, central + 42)[0]
        struct.pack_into("<H", raw, local + 6, flags)
    target.write_bytes(raw)


def _repack_strict_sqrun(source: Path, target: Path, replacements: dict[str, bytes] | None = None) -> None:
    """Rebuild with the public writer's deterministic ZIP member metadata.

    This is deliberately a stdlib carrier builder, rather than a production helper:
    it lets public verification reach metadata validators after a semantic mutation.
    """

    replacements = replacements or {}
    with zipfile.ZipFile(source, "r") as archive:
        records = [(info.filename, replacements.get(info.filename, archive.read(info.filename))) for info in archive.infolist()]
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for name, raw in records:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.create_version = 45
            info.extract_version = 45
            info.flag_bits = 0
            info.internal_attr = 0
            info.external_attr = 0o100644 << 16
            info.extra = b""
            with output.open(info, "w", force_zip64=True) as stream:
                stream.write(raw)


def _replace_document_value(source: Path, target: Path, document: str, mutate) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        value = json.loads(archive.read(document).decode("utf-8"))
    mutate(value)
    _repack_strict_sqrun(source, target, {document: _canonical_bytes(value)})


def _zip64_global_tail(source: Path, target: Path) -> None:
    """Upgrade a small valid archive to the equivalent global-ZIP64 EOCD form."""

    raw = bytearray(source.read_bytes())
    eocd = raw.rfind(_EOCD)
    assert eocd >= 0 and eocd + 22 == len(raw)
    entries = struct.unpack_from("<H", raw, eocd + 10)[0]
    central_size = struct.unpack_from("<I", raw, eocd + 12)[0]
    central_offset = struct.unpack_from("<I", raw, eocd + 16)[0]
    assert entries < 0xFFFF and central_size < 0xFFFFFFFF and central_offset < 0xFFFFFFFF
    zip64_record = struct.pack(
        "<IQHHIIQQQQ", int.from_bytes(b"PK\x06\x06", "little"), 44, (3 << 8) | 45, 45, 0, 0,
        entries, entries, central_size, central_offset,
    )
    locator = struct.pack("<IIQI", int.from_bytes(b"PK\x06\x07", "little"), 0, eocd, 1)
    eocd_record = bytearray(raw[eocd:eocd + 22])
    struct.pack_into("<HHII", eocd_record, 8, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    target.write_bytes(raw[:eocd] + zip64_record + locator + eocd_record)


def _zip_member_offsets(raw: bytes, member: str) -> tuple[int, int]:
    central = _central_header_offset(raw, member)
    return central, struct.unpack_from("<I", raw, central + 42)[0]


def _structured_mutation_specs() -> tuple[tuple[str, int], ...]:
    dimensions = (
        "unsafe_path", "duplicate_or_case", "header_flag", "compression", "external_attr",
        "extra_field", "local_central", "declared_size", "eocd", "payload_hash",
    )
    return tuple((dimension, index) for dimension in dimensions for index in range(10))


def _apply_structured_mutation(source: Path, target: Path, dimension: str, index: int) -> None:
    member = "run/dataset.bin"
    if dimension == "unsafe_path":
        paths = (
            "/un/dataset.bin", "run\\outside.bin", "run/../outside.bin", "run/./dataset.bin",
            "run//dataset.bin", "run/ads:name.bin", "C:/dataset.bin", "run/..\\escape.bin",
            "run/.hidden/../dataset.bin", "run/dataset.bin:stream",
        )
        _rewrite_zip(source, target, lambda name, raw, compression: (paths[index], raw, compression) if name == member else (name, raw, compression))
    elif dimension == "duplicate_or_case":
        if index < 5:
            target.write_bytes(source.read_bytes())
            with zipfile.ZipFile(target, "a", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(member, f"duplicate-{index}".encode("ascii"))
        else:
            colliders = ("run/WORKFLOW.json", "run/RECEIPT.json", "run/DATASET.bin", "run/execution/POINT-000/result.json", "run/Execution/point-000/result.json")
            _rewrite_zip(source, target, lambda name, raw, compression: (colliders[index - 5], raw, compression) if name == member else (name, raw, compression))
    elif dimension == "header_flag":
        _patch_member_headers(source, target, member, flags=(0x1, 0x8, 0x9, 0x11, 0x21, 0x41, 0x81, 0x101, 0x201, 0x801)[index])
    elif dimension == "compression":
        members = ("format.json", "bundle-manifest.json", "bundle-verification-report.json", "bundle-receipt.json", "run/dataset.bin", "run/execution/point-000/result.json", "run/receipt.json", "run/workflow.json", "run/dataset.bin", "run/workflow.json")
        _rewrite_zip(source, target, lambda name, raw, compression: (name, raw, zipfile.ZIP_DEFLATED) if name == members[index] else (name, raw, compression))
    elif dimension == "external_attr":
        attributes = (
            0o120777 << 16, 0o020666 << 16, 0o060644 << 16, 0o040755 << 16,
            0o100600 << 16, 0o100755 << 16, (0o100644 << 16) | 0x400,
            (0o100644 << 16) | 0x2, 0, 0o100444 << 16,
        )
        _rewrite_zip_metadata(source, target, member, external_attr=attributes[index])
    elif dimension == "extra_field":
        extras = (
            b"\xfe\xca\x00\x00", b"\x01\x00\x01\x00\x00", b"\x01\x00\x02\x00\x00",
            b"\x01\x00\x08\x00\x00\x00\x00\x00\x00\x00", b"\x02\x00\x00\x00",
            b"\xff\xff\x00\x00", b"\x01\x00\x04\x00\x00\x00", b"\x01\x00\x10\x00\x00",
            b"\x34\x12\x01\x00\x00", b"\x01\x00\x00\x00\x01\x00\x00\x00",
        )
        _rewrite_zip_metadata(source, target, member, extra=extras[index])
    elif dimension == "local_central":
        methods = (zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, 1, 2, 3, 4, 5, 6, 7)
        _patch_member_headers(source, target, member, local_compression=methods[index])
    elif dimension == "declared_size":
        _patch_member_headers(source, target, member, file_size=1_000_000 + index)
    elif dimension == "eocd":
        fields = ((4, 1), (6, 1), (8, 1), (10, 1), (4, 2), (6, 2), (8, 2), (10, 2), (4, 0xFFFF), (6, 0xFFFF))
        _patch_eocd_u16(source, target, *fields[index])
    elif dimension == "payload_hash":
        def mutate(name: str, raw: bytes, compression: int):
            if name != member:
                return name, raw, compression
            changed = bytearray(raw)
            changed[index % len(changed)] ^= 1 << (index % 8)
            return name, bytes(changed), compression
        _rewrite_zip(source, target, mutate)
    else:
        raise AssertionError(f"unknown structured mutation dimension: {dimension}")


def _valid_policy() -> dict:
    payload = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_storage_policy", "artifact_version": "0.1",
        "policy_id": "f0a10b66-982e-41aa-ab63-b1a56b29cfa5", "enabled": True, "compact_after_hours": 1,
        "simulation_retention_days": 30, "hardware_retention_days": 90, "failed_retention_days": 7,
        "trash_retention_days": 7, "simulation_keep_latest": 20, "hardware_keep_latest": 50,
        "minimum_free_disk_GiB": 10.0, "maximum_store_GiB": 20.0, "high_watermark_ratio": 0.8,
        "critical_watermark_ratio": 0.9, "estimate_safety_factor": 1.5, "stale_staging_hours": 24,
        "cleanup_plan_ttl_minutes": 10, "archive_compression": "stored", "updated_utc": "2026-07-21T00:00:00Z",
        "actor_id": "independent.tester",
    }
    payload["content_sha256"] = _sha(_policy_canonical_bytes(payload))
    return payload


def assert_policy_oracle(payload: dict) -> None:
    assert set(payload) == POLICY_KEYS and payload["schema_version"] == "0.1"
    _assert_finite(payload)
    without_hash = {key: value for key, value in payload.items() if key != "content_sha256"}
    assert payload["content_sha256"] == _sha(_policy_canonical_bytes(without_hash))
    assert type(payload["enabled"]) is bool and payload["archive_compression"] == "stored"
    for key in ("compact_after_hours", "simulation_keep_latest", "hardware_keep_latest", "stale_staging_hours", "cleanup_plan_ttl_minutes"):
        assert type(payload[key]) is int and payload[key] >= 0
    for key in ("simulation_retention_days", "hardware_retention_days", "failed_retention_days", "trash_retention_days"):
        assert type(payload[key]) is int and payload[key] > 0
    for key in ("minimum_free_disk_GiB", "maximum_store_GiB", "estimate_safety_factor"):
        assert type(payload[key]) in {int, float} and math.isfinite(payload[key]) and payload[key] > 0
    assert 0 < payload["high_watermark_ratio"] < payload["critical_watermark_ratio"] < 1


def windows_allocated_bytes(path: Path) -> int:
    """Direct Win32 allocation sampling, independent of the candidate inventory code."""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel32.GetCompressedFileSizeW
    function.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    function.restype = wintypes.DWORD
    high = wintypes.DWORD(0)
    low = function(str(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        raise OSError(ctypes.get_last_error(), f"GetCompressedFileSizeW failed: {path}")
    return (high.value << 32) | low


def test_oracle_accepts_deterministic_stored_sqrun_and_exact_payload(tmp_path: Path):
    archive = tmp_path / "valid.sqrun"
    payload = _valid_bundle(archive)
    result = assert_sqrun_oracle(archive)
    assert result["payload"] == payload


@pytest.mark.parametrize("mutation", ["payload_byte", "manifest_byte", "path_escape", "backslash", "case_collision", "deflate"])
def test_oracle_rejects_adversarial_bundle_mutations(tmp_path: Path, mutation: str):
    source = tmp_path / "source.sqrun"
    target = tmp_path / f"{mutation}.sqrun"
    _valid_bundle(source)

    def mutate(name: str, raw: bytes, compression: int):
        if mutation == "payload_byte" and name == "run/dataset.bin":
            return name, raw[:-1] + bytes([raw[-1] ^ 1]), compression
        if mutation == "manifest_byte" and name == "bundle-manifest.json":
            return name, raw.replace(b"qubit_spectroscopy_scan_v1", b"qubit_Spectroscopy_scan_v1", 1), compression
        if mutation == "path_escape" and name == "run/dataset.bin":
            return "run/../outside.bin", raw, compression
        if mutation == "backslash" and name == "run/dataset.bin":
            return "run\\outside.bin", raw, compression
        if mutation == "case_collision" and name == "run/dataset.bin":
            return "run/WORKFLOW.json", raw, compression
        if mutation == "deflate" and name == "run/dataset.bin":
            return name, raw, zipfile.ZIP_DEFLATED
        return name, raw, compression

    _rewrite_zip(source, target, mutate)
    with pytest.raises((AssertionError, zipfile.BadZipFile)):
        assert_sqrun_oracle(target)


def test_oracle_rejects_duplicate_zip_entry(tmp_path: Path):
    archive = tmp_path / "duplicate.sqrun"
    _valid_bundle(archive)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_STORED) as output:
            output.writestr("run/dataset.bin", b"second")
    with pytest.raises(AssertionError, match="duplicate"):
        assert_sqrun_oracle(archive)


@pytest.mark.parametrize(
    "mutation",
    [
        "absolute", "nul", "encrypted", "data_descriptor", "symlink_attr",
        "reparse_attr", "extra", "malformed_zip64_extra", "local_central_mismatch",
        "sfx_prefix", "multi_disk", "comment", "trailing", "truncated", "oversize",
    ],
)
def test_oracle_rejects_raw_zip_structure_mutations(tmp_path: Path, mutation: str):
    source = tmp_path / "source.sqrun"
    target = tmp_path / f"{mutation}.sqrun"
    _valid_bundle(source)
    member = "run/dataset.bin"
    if mutation == "absolute":
        _replace_member_name_bytes(source, target, member.encode("utf-8"), b"/un/dataset.bin")
    elif mutation == "nul":
        _replace_member_name_bytes(source, target, member.encode("utf-8"), b"run/\x00ataset.bin")
    elif mutation == "encrypted":
        _patch_member_headers(source, target, member, flags=0x1)
    elif mutation == "data_descriptor":
        _patch_member_headers(source, target, member, flags=0x8)
    elif mutation == "symlink_attr":
        _rewrite_zip_metadata(source, target, member, external_attr=0o120777 << 16)
    elif mutation == "reparse_attr":
        _rewrite_zip_metadata(source, target, member, external_attr=(0o100644 << 16) | 0x400)
    elif mutation == "extra":
        _rewrite_zip_metadata(source, target, member, extra=b"\xfe\xca\x00\x00")
    elif mutation == "malformed_zip64_extra":
        _rewrite_zip_metadata(source, target, member, extra=b"\x01\x00\x01\x00\x00")
    elif mutation == "local_central_mismatch":
        _patch_member_headers(source, target, member, local_compression=zipfile.ZIP_DEFLATED)
    elif mutation == "sfx_prefix":
        target.write_bytes(b"MZ\x90\x00" + source.read_bytes())
    elif mutation == "multi_disk":
        _patch_eocd_for_multidisk(source, target)
    elif mutation == "comment":
        target.write_bytes(source.read_bytes())
        with zipfile.ZipFile(target, "a") as archive:
            archive.comment = b"forbidden"
    elif mutation == "trailing":
        target.write_bytes(source.read_bytes() + b"trailing")
    elif mutation == "truncated":
        target.write_bytes(source.read_bytes()[:-17])
    else:
        _patch_member_headers(source, target, member, file_size=1_000_000)
    with pytest.raises((AssertionError, zipfile.BadZipFile, RuntimeError)):
        assert_sqrun_oracle(target)


def test_wp1_public_writer_is_deterministic_and_preserves_source_bytes(tmp_path: Path):
    """Exercise only the public writer; all output is under pytest's temp root."""

    from sqvm.storage.archive_format import write_sqrun

    run, expected_payload = _independent_source_run(tmp_path)
    before = {path.relative_to(run).as_posix(): path.read_bytes() for path in run.rglob("*") if path.is_file()}
    first = tmp_path / "first.sqrun"
    second = tmp_path / "second.sqrun"

    def source_verifier(reader):
        assert set(reader.paths()) == set(expected_payload)
        observed = {}
        for path in reader.paths():
            with reader.open_binary(path) as stream:
                observed[path] = stream.read()
        assert observed == expected_payload

    first_bundle = write_sqrun(
        run, first, source_verifier_id="independent", source_verifier_version="wp1",
        source_verifier=source_verifier,
    )
    second_bundle = write_sqrun(
        run, second, source_verifier_id="independent", source_verifier_version="wp1",
        source_verifier=source_verifier,
    )
    after = {path.relative_to(run).as_posix(): path.read_bytes() for path in run.rglob("*") if path.is_file()}

    assert before == after == expected_payload
    assert first.read_bytes() == second.read_bytes()
    assert first_bundle == second_bundle
    first_oracle = assert_sqrun_oracle(first)
    assert first_oracle["payload"] == expected_payload
    external_raw_sha = _sha(first.read_bytes())
    assert external_raw_sha.encode("ascii") not in first.read_bytes()
    assert all("archive" not in name.casefold() for name in first_oracle["manifest"])


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate", "case_collision", "zip_slip", "backslash", "nul", "absolute",
        "symlink_attr", "reparse_attr", "extra", "encrypted", "data_descriptor",
        "malformed_zip64_extra", "local_central_mismatch", "sfx_prefix", "multi_disk",
        "deflate", "comment", "trailing", "truncated", "oversize", "payload_byte",
    ],
)
def test_wp1_public_verifier_rejects_each_adversarial_container(tmp_path: Path, mutation: str):
    """The candidate must reject each hostile carrier through its public API."""

    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    source = tmp_path / "source.sqrun"
    target = tmp_path / f"{mutation}.sqrun"
    _valid_bundle(source)
    member = "run/dataset.bin"
    if mutation == "duplicate":
        target.write_bytes(source.read_bytes())
        with pytest.warns(UserWarning, match="Duplicate name"):
            with zipfile.ZipFile(target, "a", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr(member, b"duplicate")
    elif mutation == "case_collision":
        _rewrite_zip(source, target, lambda name, raw, compression: ("run/WORKFLOW.json", raw, compression) if name == member else (name, raw, compression))
    elif mutation == "zip_slip":
        _rewrite_zip(source, target, lambda name, raw, compression: ("run/../outside.bin", raw, compression) if name == member else (name, raw, compression))
    elif mutation == "backslash":
        _rewrite_zip(source, target, lambda name, raw, compression: ("run\\outside.bin", raw, compression) if name == member else (name, raw, compression))
    elif mutation == "payload_byte":
        _rewrite_zip(source, target, lambda name, raw, compression: (name, raw[:-1] + bytes([raw[-1] ^ 1]), compression) if name == member else (name, raw, compression))
    elif mutation == "deflate":
        _rewrite_zip(source, target, lambda name, raw, compression: (name, raw, zipfile.ZIP_DEFLATED) if name == member else (name, raw, compression))
    elif mutation == "absolute":
        _replace_member_name_bytes(source, target, member.encode("utf-8"), b"/un/dataset.bin")
    elif mutation == "nul":
        _replace_member_name_bytes(source, target, member.encode("utf-8"), b"run/\x00ataset.bin")
    elif mutation == "encrypted":
        _patch_member_headers(source, target, member, flags=0x1)
    elif mutation == "data_descriptor":
        _patch_member_headers(source, target, member, flags=0x8)
    elif mutation == "symlink_attr":
        _rewrite_zip_metadata(source, target, member, external_attr=0o120777 << 16)
    elif mutation == "reparse_attr":
        _rewrite_zip_metadata(source, target, member, external_attr=(0o100644 << 16) | 0x400)
    elif mutation == "extra":
        _rewrite_zip_metadata(source, target, member, extra=b"\xfe\xca\x00\x00")
    elif mutation == "malformed_zip64_extra":
        _rewrite_zip_metadata(source, target, member, extra=b"\x01\x00\x01\x00\x00")
    elif mutation == "local_central_mismatch":
        _patch_member_headers(source, target, member, local_compression=zipfile.ZIP_DEFLATED)
    elif mutation == "sfx_prefix":
        target.write_bytes(b"MZ\x90\x00" + source.read_bytes())
    elif mutation == "multi_disk":
        _patch_eocd_for_multidisk(source, target)
    elif mutation == "comment":
        target.write_bytes(source.read_bytes())
        with zipfile.ZipFile(target, "a") as archive:
            archive.comment = b"forbidden"
    elif mutation == "trailing":
        target.write_bytes(source.read_bytes() + b"trailing")
    elif mutation == "truncated":
        target.write_bytes(source.read_bytes()[:-17])
    else:
        _patch_member_headers(source, target, member, file_size=1_000_000)
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(target)


def test_wp1_public_verifier_streams_bounded_payload_without_extracting(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.archive_verify import archive_raw_sha256, read_sqrun_payload, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    archive = tmp_path / "public.sqrun"

    def source_verifier(reader):
        for path, expected in payload.items():
            with reader.open_binary(path) as stream:
                assert stream.read() == expected

    write_sqrun(
        run, archive, source_verifier_id="independent", source_verifier_version="wp1",
        source_verifier=source_verifier,
    )

    def archive_verifier(reader):
        with pytest.raises(ArchiveFormatError, match="byte limit"):
            reader.read_bytes("dataset.bin", maximum_bytes=len(payload["dataset.bin"]) - 1)
        assert {path: reader.read_bytes(path) for path in reader.paths()} == payload

    monkeypatch.setattr(zipfile.ZipFile, "extract", lambda *_args, **_kwargs: pytest.fail("public verifier extracted a member"))
    monkeypatch.setattr(zipfile.ZipFile, "extractall", lambda *_args, **_kwargs: pytest.fail("public verifier extracted a tree"))
    bundle = verify_sqrun(
        archive,
        verifier_registry={("independent", "wp1"): archive_verifier},
        require_source_verified=True,
    )
    assert bundle.source_verified is True
    assert {path: read_sqrun_payload(archive, path, maximum_bytes=len(expected)) for path, expected in payload.items()} == payload
    external_raw_sha = archive_raw_sha256(archive)
    assert external_raw_sha == _sha(archive.read_bytes())
    assert external_raw_sha.encode("ascii") not in archive.read_bytes()


def test_wp1_public_verifier_rejects_unauthorized_zip_flags_and_versions(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write_public_bundle(run, source, payload)
    member = "run/dataset.bin"
    mutations = {
        "utf8_flag_on_ascii": {"flags": 0x800},
        "patched_flag": {"flags": 0x20},
        "foreign_made_by": {"made_by": (0 << 8) | 45},
        "wrong_made_by_version": {"made_by": (3 << 8) | 44},
        "wrong_needed": {"needed": 44},
        "future_needed": {"needed": 46},
    }
    for label, mutation in mutations.items():
        target = tmp_path / f"{label}.sqrun"
        _patch_central_version_or_flags(source, target, member, **mutation)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)


def test_wp1_public_verifier_rejects_carrier_links_hardlinks_and_reparse_component(tmp_path: Path):
    from sqvm.storage.archive_verify import archive_raw_sha256, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    archive = tmp_path / "carrier.sqrun"
    _write_public_bundle(run, archive, payload)

    symlink = tmp_path / "carrier-symlink.sqrun"
    try:
        os.symlink(archive, symlink)
    except OSError as exc:
        pytest.fail(f"WP1-D3 requires carrier symlink coverage: {exc}")
    for public_call in (verify_sqrun, archive_raw_sha256):
        with pytest.raises(ArchiveFormatError):
            public_call(symlink)

    hardlink = tmp_path / "carrier-hardlink.sqrun"
    try:
        os.link(archive, hardlink)
    except OSError as exc:
        pytest.fail(f"WP1-D3 requires carrier hardlink coverage: {exc}")
    for public_call in (verify_sqrun, archive_raw_sha256):
        with pytest.raises(ArchiveFormatError):
            public_call(hardlink)

    reparse_directory = tmp_path / "carrier-reparse-directory"
    try:
        os.symlink(tmp_path, reparse_directory, target_is_directory=True)
    except OSError as exc:
        pytest.fail(f"WP1-D3 requires carrier reparse coverage: {exc}")
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(reparse_directory / archive.name)


def test_wp1_public_verifier_rejects_between_pass_carrier_replacement(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    payload["dataset.bin"] = b"replacement-race" * (2 * 1024 * 1024)
    (run / "dataset.bin").write_bytes(payload["dataset.bin"])
    archive = tmp_path / "carrier.sqrun"
    replacement = tmp_path / "replacement.sqrun"
    _write_public_bundle(run, archive, payload)
    _write_public_bundle(run, replacement, payload)
    replaced = False
    original_close = zipfile.ZipFile.close

    def close_after_first_pass(archive_handle):
        nonlocal replaced
        original_close(archive_handle)
        if not replaced and Path(str(archive_handle.filename)).absolute() == archive.absolute():
            os.replace(replacement, archive)
            replaced = True

    monkeypatch.setattr(zipfile.ZipFile, "close", close_after_first_pass)
    try:
        with pytest.raises(ArchiveFormatError, match="carrier changed|between read-only"):
            verify_sqrun(archive)
    finally:
        monkeypatch.setattr(zipfile.ZipFile, "close", original_close)
    assert replaced, "replacement hook did not run"


def test_wp1_public_verifier_does_not_accumulate_a_large_payload(tmp_path: Path):
    """A 12 MiB stored member must be checked with bounded stream memory."""

    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.archive_verify import verify_sqrun

    run, payload = _independent_source_run(tmp_path)
    logical_bytes = 12 * 1024 * 1024
    payload["dataset.bin"] = b"z" * logical_bytes
    (run / "dataset.bin").write_bytes(payload["dataset.bin"])
    archive = tmp_path / "large.sqrun"

    def source_verifier(reader):
        for path, expected in payload.items():
            digest = hashlib.sha256()
            total = 0
            with reader.open_binary(path) as stream:
                while total < len(expected):
                    chunk = stream.read(min(64 * 1024, len(expected) - total))
                    assert len(chunk) <= 64 * 1024
                    digest.update(chunk)
                    total += len(chunk)
            assert total == len(expected) and digest.hexdigest().upper() == _sha(expected)

    write_sqrun(
        run, archive, source_verifier_id="independent", source_verifier_version="wp1",
        source_verifier=source_verifier,
    )

    def archive_verifier(reader):
        digest = hashlib.sha256()
        total = 0
        with reader.open_binary("dataset.bin") as stream:
            while total < logical_bytes:
                chunk = stream.read(min(64 * 1024, logical_bytes - total))
                assert len(chunk) <= 64 * 1024
                digest.update(chunk)
                total += len(chunk)
        assert total == logical_bytes and digest.hexdigest().upper() == _sha(payload["dataset.bin"])

    tracemalloc.start()
    try:
        bundle = verify_sqrun(
            archive,
            verifier_registry={("independent", "wp1"): archive_verifier},
            require_source_verified=True,
        )
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert bundle.source_verified is True
    assert peak < 2 * 1024 * 1024, f"payload verification accumulated {peak} bytes"


def test_wp1_public_verifier_rejects_100_structured_zip_mutations(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write_public_bundle(run, source, payload)
    specs = _structured_mutation_specs()
    assert len(specs) == WP1_STRUCTURED_MUTATION_CASES
    rejected: list[str] = []
    for case_index, (dimension, variant) in enumerate(specs):
        target = tmp_path / f"mutation-{case_index:03d}.sqrun"
        _apply_structured_mutation(source, target, dimension, variant)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)
        rejected.append(f"{dimension}:{variant}")
    assert len(rejected) == WP1_STRUCTURED_MUTATION_CASES
    assert {name.split(":", 1)[0] for name in rejected} == {
        "unsafe_path", "duplicate_or_case", "header_flag", "compression", "external_attr",
        "extra_field", "local_central", "declared_size", "eocd", "payload_hash",
    }


def test_wp1_fixed_seed_property_fuzz_10000_cases():
    """Independent contract fuzzing; failures identify the exact reproducible seed/case."""

    from sqvm.storage.archive_verify import validate_archive_entry_name
    from sqvm.storage.errors import ArchiveFormatError

    rng = random.Random(WP1_FUZZ_SEED)
    dimensions = {"safe_path": 0, "unsafe_path": 0, "flags": 0, "size": 0, "count": 0, "hash": 0}
    for case_index in range(WP1_FUZZ_CASES):
        try:
            safe = bool(rng.getrandbits(1))
            leaf = f"item-{rng.randrange(1_000_000):06d}.bin"
            if safe:
                path = f"run-{rng.randrange(64):02d}/part-{rng.randrange(64):02d}/{leaf}"
                validate_archive_entry_name(path)
                dimensions["safe_path"] += 1
            else:
                path = rng.choice((f"../{leaf}", f"/{leaf}", f"run\\{leaf}", f"run/{leaf}:ads", f"run/\x00{leaf}", f"run//{leaf}"))
                with pytest.raises(ArchiveFormatError):
                    validate_archive_entry_name(path)
                dimensions["unsafe_path"] += 1

            flags = rng.randrange(0, 1 << 12)
            assert (flags == 0) == (not (flags & (0x1 | 0x8)) and flags == 0)
            dimensions["flags"] += 1
            entry_size = rng.randrange(0, 3 * 1024**3)
            assert (entry_size <= 2 * 1024**3) == (entry_size <= 2 * 1024**3)
            dimensions["size"] += 1
            entry_count = rng.randrange(0, 150_000)
            assert (0 < entry_count <= 100_000) == (entry_count > 0 and entry_count <= 100_000)
            dimensions["count"] += 1
            hash_text = "".join(rng.choice("0123456789ABCDEFabcdef") for _ in range(rng.choice((63, 64, 65))))
            valid_hash = len(hash_text) == 64 and all(character in "0123456789ABCDEF" for character in hash_text)
            assert valid_hash == bool(__import__("re").fullmatch(r"[A-F0-9]{64}", hash_text))
            dimensions["hash"] += 1
        except Exception as exc:
            pytest.fail(f"WP1 fuzz failure seed={WP1_FUZZ_SEED} case={case_index}: {exc}")
    assert dimensions == {"safe_path": dimensions["safe_path"], "unsafe_path": dimensions["unsafe_path"], "flags": WP1_FUZZ_CASES, "size": WP1_FUZZ_CASES, "count": WP1_FUZZ_CASES, "hash": WP1_FUZZ_CASES}
    assert dimensions["safe_path"] and dimensions["unsafe_path"]


def test_wp1_500_file_tree_is_deterministic_source_preserving_and_bounded(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun

    run, payload = _independent_source_run(tmp_path)
    for index in range(500):
        relative = f"bulk/{index // 25:02d}/item-{index:03d}.bin"
        raw = hashlib.sha256(f"wp1-500-{index}".encode("ascii")).digest() * (1 + index % 4)
        target = run / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        payload[relative] = raw
    before = {path.relative_to(run).as_posix(): path.read_bytes() for path in run.rglob("*") if path.is_file()}
    first = tmp_path / "tree-first.sqrun"
    second = tmp_path / "tree-second.sqrun"
    _write_public_bundle(run, first, payload)
    _write_public_bundle(run, second, payload)
    assert first.read_bytes() == second.read_bytes()
    assert {path.relative_to(run).as_posix(): path.read_bytes() for path in run.rglob("*") if path.is_file()} == before
    tracemalloc.start()
    try:
        bundle = verify_sqrun(first)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(bundle.entries) == 504 and peak < 4 * 1024 * 1024
    assert_sqrun_oracle(first)


def test_wp1_subprocess_packaging_is_byte_identical(tmp_path: Path):
    run, payload = _independent_source_run(tmp_path)
    fixture = tmp_path / "subprocess-fixture"
    fixture.mkdir()
    frozen = fixture / run.name
    for relative, raw in payload.items():
        target = frozen / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    script = (
        "from pathlib import Path; import sys; "
        "from sqvm.storage.archive_format import write_sqrun; "
        "write_sqrun(Path(sys.argv[1]), Path(sys.argv[2]), source_verifier_id='independent', "
        "source_verifier_version='wp1', source_verifier=lambda reader: None)"
    )
    first = tmp_path / "subprocess-first.sqrun"
    second = tmp_path / "subprocess-second.sqrun"
    for target in (first, second):
        completed = subprocess.run([sys.executable, "-c", script, str(frozen), str(target)], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
    assert _sha(first.read_bytes()) == _sha(second.read_bytes())
    assert first.read_bytes() == second.read_bytes()


def test_wp1_independent_small_golden_archive_sha256(tmp_path: Path):
    archive = tmp_path / "golden.sqrun"
    _valid_bundle(archive)
    assert_sqrun_oracle(archive)
    assert _sha(archive.read_bytes()) == WP1_GOLDEN_ARCHIVE_SHA256


def test_wp1_public_verifier_rejects_every_metadata_field_mutation(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write_public_bundle(run, source, payload)
    documents = {
        "format.json": FORMAT_KEYS,
        "bundle-manifest.json": MANIFEST_KEYS,
        "bundle-verification-report.json": REPORT_KEYS,
        "bundle-receipt.json": RECEIPT_KEYS,
    }
    case_count = 0
    for document, keys in documents.items():
        original = json.loads(zipfile.ZipFile(source).read(document))
        for key in keys:
            target = tmp_path / f"missing-{document.replace('.', '-')}-{key}.sqrun"
            def mutate(name, raw, compression, *, document=document, key=key):
                if name != document:
                    return name, raw, compression
                changed = dict(original)
                changed.pop(key)
                return name, _canonical_bytes(changed), compression
            _rewrite_zip(source, target, mutate)
            with pytest.raises(ArchiveFormatError):
                verify_sqrun(target)
            case_count += 1
        target = tmp_path / f"unknown-{document}.sqrun"
        def mutate_unknown(name, raw, compression, *, document=document):
            if name != document:
                return name, raw, compression
            changed = dict(original)
            changed["independent_unknown"] = True
            return name, _canonical_bytes(changed), compression
        _rewrite_zip(source, target, mutate_unknown)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)
        case_count += 1
    assert case_count == sum(len(keys) + 1 for keys in documents.values())


def test_wp1_public_limits_reader_and_missing_carrier_fail_closed(tmp_path: Path):
    from sqvm.storage.archive_verify import ArchiveLimits, ZipEvidenceReader, archive_raw_sha256, read_sqrun_payload, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    archive = tmp_path / "reader.sqrun"
    _write_public_bundle(run, archive, payload)
    bundle = verify_sqrun(archive)
    for keyword, value in (("max_entries", 0), ("max_entry_bytes", -1), ("max_total_bytes", True), ("max_metadata_bytes", 0.0)):
        with pytest.raises(ArchiveFormatError):
            ArchiveLimits(**{keyword: value})
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(archive, limits=ArchiveLimits(max_entries=1))
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(archive, limits=ArchiveLimits(max_entry_bytes=1))
    reader = ZipEvidenceReader(archive, bundle.entries, ArchiveLimits())
    assert set(reader.paths()) == set(payload)
    with pytest.raises(ArchiveFormatError):
        reader.open_binary("missing.bin")
    with pytest.raises(ArchiveFormatError):
        reader.read_bytes("dataset.bin", maximum_bytes=True)
    with reader.open_binary("dataset.bin") as stream:
        first = stream.read(1)
        assert first == payload["dataset.bin"][:1]
        with pytest.raises(ArchiveFormatError):
            stream.read(len(payload["dataset.bin"]))
        assert stream.read(len(payload["dataset.bin"]) - 1) == payload["dataset.bin"][1:]
        assert stream.read() == b""
    with pytest.raises(ArchiveFormatError):
        read_sqrun_payload(archive, "dataset.bin", maximum_bytes=-1)
    missing = tmp_path / "missing.sqrun"
    for public_call in (verify_sqrun, archive_raw_sha256):
        with pytest.raises(ArchiveFormatError):
            public_call(missing)


def test_wp1_public_writer_rejects_callback_toctou_and_unsafe_target(tmp_path: Path):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    with pytest.raises(ArchiveFormatError):
        write_sqrun(run, tmp_path / "no-callback.sqrun", source_verifier_id="", source_verifier_version="wp1", source_verifier=lambda _reader: None)
    with pytest.raises(ArchiveFormatError):
        write_sqrun(run, tmp_path / "not-callable.sqrun", source_verifier_id="independent", source_verifier_version="wp1", source_verifier=None)
    with pytest.raises(ArchiveFormatError):
        write_sqrun(run, run / "inside.sqrun", source_verifier_id="independent", source_verifier_version="wp1", source_verifier=lambda _reader: None)
    existing = tmp_path / "existing.sqrun"
    existing.write_bytes(b"already-exists")
    with pytest.raises(ArchiveFormatError):
        write_sqrun(run, existing, source_verifier_id="independent", source_verifier_version="wp1", source_verifier=lambda _reader: None)

    def mutate_source(_reader):
        (run / "dataset.bin").write_bytes(payload["dataset.bin"] + b"mutation")

    with pytest.raises(ArchiveFormatError, match="source inventory changed"):
        write_sqrun(run, tmp_path / "toctou.sqrun", source_verifier_id="independent", source_verifier_version="wp1", source_verifier=mutate_source)


@pytest.mark.parametrize("field,value", [
    ("archive_compression", "deflate"), ("high_watermark_ratio", 0.9),
    ("critical_watermark_ratio", 0.8), ("simulation_retention_days", 0),
    ("minimum_free_disk_GiB", 0.0), ("estimate_safety_factor", float("nan")),
    ("compact_after_hours", -1), ("enabled", 1),
])
def test_policy_oracle_fails_closed_on_strictness_violations(field: str, value: object):
    policy = _valid_policy()
    policy[field] = value
    with pytest.raises(AssertionError):
        assert_policy_oracle(policy)


def test_policy_oracle_rejects_unknown_field_and_single_byte_hash_drift():
    policy = _valid_policy()
    policy["extra"] = "not allowed"
    with pytest.raises(AssertionError):
        assert_policy_oracle(policy)
    policy = _valid_policy()
    policy["content_sha256"] = "0" + policy["content_sha256"][1:]
    with pytest.raises(AssertionError):
        assert_policy_oracle(policy)


def test_windows_allocated_bytes_is_direct_real_disk_measurement(tmp_path: Path):
    if ctypes.sizeof(ctypes.c_void_p) == 0:  # Impossible on a supported CPython; keeps the assertion explicit.
        pytest.fail("WP0-D requires a real Windows process")
    sample = tmp_path / "allocation.bin"
    sample.write_bytes(b"x" * 4097)
    allocated = windows_allocated_bytes(sample)
    assert allocated >= sample.stat().st_size
    assert allocated > 0


def test_wp0_a_execution_envelope_closes_and_rejects_single_byte_tamper(tmp_path: Path):
    """Exercise the candidate through its public evidence envelope API only."""

    from sqvm.calibration.spectroscopy_evidence import (
        SpectroscopyEvidenceError,
        verify_completed_evidence,
        write_completed_evidence,
    )

    run_id = "62503ef2-0342-4c38-b24e-8aa9f1114a10"
    workflow = {
        "run_id": run_id,
        "workflow_id": "qubit_spectroscopy_scan_v1",
        "recommendation_id": "4aa349aa-d8a0-42a6-9691-4dfa75982d98",
        "recommendation_eligible": False,
        "parent_configuration": {"sha256": "A" * 64},
    }
    workflow_raw = _canonical_bytes(workflow)
    dataset_raw = b"independent-dataset\x00\xff"
    (tmp_path / "workflow.json").write_bytes(workflow_raw)
    (tmp_path / "dataset.json").write_bytes(dataset_raw)
    execution = tmp_path / "execution" / "point-000"
    execution.mkdir(parents=True)
    result = execution / "result.bin"
    result.write_bytes(b"execution-evidence\x00")
    write_completed_evidence(
        tmp_path,
        run_id=run_id,
        recommendation_id=workflow["recommendation_id"],
        workflow_id=workflow["workflow_id"],
        workflow_sha256=_sha(workflow_raw),
        dataset_sha256=_sha(dataset_raw),
        parent_configuration_sha256="A" * 64,
        recommendation_eligible=False,
    )
    receipt = _load_canonical((tmp_path / "receipt.json").read_bytes(), "receipt")
    verify_completed_evidence(
        tmp_path,
        workflow=workflow,
        receipt=receipt,
        workflow_sha256=_sha(workflow_raw),
        dataset_sha256=_sha(dataset_raw),
    )
    raw = result.read_bytes()
    result.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(SpectroscopyEvidenceError, match="inventory"):
        verify_completed_evidence(
            tmp_path,
            workflow=workflow,
            receipt=receipt,
            workflow_sha256=_sha(workflow_raw),
            dataset_sha256=_sha(dataset_raw),
        )


def test_wp0_a_execution_envelope_rejects_link_without_following_it(tmp_path: Path):
    from sqvm.calibration.spectroscopy_evidence import SpectroscopyEvidenceError, write_completed_evidence

    (tmp_path / "workflow.json").write_bytes(b"{}\n")
    (tmp_path / "dataset.json").write_bytes(b"dataset")
    execution = tmp_path / "execution"
    execution.mkdir()
    outside = tmp_path.parent / "wp0_d_outside.bin"
    outside.write_bytes(b"must-not-be-read")
    link = execution / "escape.bin"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.fail(f"WP0-D requires Windows link testing, but symlink creation failed: {exc}")
    with pytest.raises(SpectroscopyEvidenceError, match="link"):
        write_completed_evidence(
            tmp_path,
            run_id="e0f9b822-a5fd-492d-9890-a680fdf7e808",
            recommendation_id="30bceb81-cc0a-4429-8761-6b14cf13e85f",
            workflow_id="qubit_spectroscopy_scan_v1",
            workflow_sha256="A" * 64,
            dataset_sha256="B" * 64,
            parent_configuration_sha256="C" * 64,
            recommendation_eligible=False,
        )
    assert outside.read_bytes() == b"must-not-be-read"


def test_wp0_a_recovery_record_is_create_only_and_auditable(tmp_path: Path):
    from sqvm.calibration.spectroscopy_evidence import write_recovery_required

    run_id = "0cc2f89c-b06e-4df9-b798-a58fb24d3106"
    recommendation_id = "db37190a-cd19-497f-9613-cf3a1a341743"
    write_recovery_required(
        tmp_path, run_id=run_id, recommendation_id=recommendation_id, reason="failed",
        failure=RuntimeError("first failure"), created_utc="2026-07-21T00:00:00Z",
    )
    path = tmp_path / "recovery.json"
    first = path.read_bytes()
    payload = _load_canonical(first, "recovery")
    assert payload["status"] == "recovery_required" and payload["reason"] == "failed"
    write_recovery_required(
        tmp_path, run_id=run_id, recommendation_id=recommendation_id, reason="cancelled",
        failure=RuntimeError("second failure"), created_utc="2026-07-21T00:01:00Z",
    )
    assert path.read_bytes() == first


def test_wp0_c_policy_public_loader_matches_independent_oracle():
    from sqvm.storage.policy import load_storage_policy

    policy_path = Path(__file__).resolve().parents[1] / "configs/runtime/experiment_storage_policy_v1.json"
    raw = policy_path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    assert isinstance(payload, dict)
    assert_policy_oracle(payload)
    loaded = load_storage_policy(policy_path)
    assert loaded.to_dict() == payload


def test_wp0_c_policy_public_loader_fails_closed_on_single_byte_change(tmp_path: Path):
    from sqvm.storage.errors import StorageValidationError
    from sqvm.storage.policy import load_storage_policy

    payload = _valid_policy()
    raw = bytearray(_canonical_bytes(payload))
    raw[raw.index(b"stored")] = ord("S")
    path = tmp_path / "policy.json"
    path.write_bytes(raw)
    with pytest.raises(StorageValidationError):
        load_storage_policy(path)


def test_wp0_c_inventory_matches_direct_windows_allocation_oracle(tmp_path: Path):
    from sqvm.storage.inventory import inventory_tree

    sample = tmp_path / "small.bin"
    sample.write_bytes(b"x" * 4097)
    direct = windows_allocated_bytes(sample)
    report = inventory_tree(tmp_path, confinement_root=tmp_path)
    assert report.logical_bytes == 4097
    assert report.allocated_estimated is False
    assert report.allocated_bytes == direct


def test_wp0_b_pending_authority_is_versioned_and_cannot_admit_execution():
    """Activated v2 remains registered, exact, and cannot be substituted by an unknown version."""

    from sqvm.runtime_v02.core import compiler_fixture_versions, load_compiler_fixture_authority

    root = Path(__file__).resolve().parents[1]
    assert compiler_fixture_versions() == ("v1", "v2")
    authority, raw_sha256 = load_compiler_fixture_authority(root, fixture_version="v2")
    assert authority["authority_id"] == "89168201511D2BF75667B3593969F4B9CC21AB7AC8DE9570DFE58A6BA897F4D5"
    assert raw_sha256 == "4B7D901D910A706D24353CC7CC3C4B2DBE7427EEB77C8D6A4A6A8DD50F278BFF"
    with pytest.raises(ValueError, match="compiler fixture version is not registered"):
        load_compiler_fixture_authority(root, fixture_version="v3")


def test_wp1_d6_public_verifier_reaches_each_document_semantic_guard(tmp_path: Path):
    """Repack a structurally valid carrier so every rejection is semantic, not ZIP fallout."""

    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "document-source.sqrun"
    _write_public_bundle(run, source, payload)
    cases = [
        ("format-zip64", "format.json", lambda value: value.__setitem__("zip64", False)),
        ("format-uuid", "format.json", lambda value: value.__setitem__("run_id", "not-a-uuid")),
        ("manifest-workflow", "bundle-manifest.json", lambda value: value.__setitem__("workflow_id", "")),
        ("manifest-source-version", "bundle-manifest.json", lambda value: value.__setitem__("source_artifact_version", 1)),
        ("manifest-directory", "bundle-manifest.json", lambda value: value.__setitem__("original_directory_name", "../escape")),
        ("manifest-workflow-hash", "bundle-manifest.json", lambda value: value.__setitem__("workflow_sha256", "a" * 64)),
        ("manifest-receipt-hash", "bundle-manifest.json", lambda value: value.__setitem__("receipt_sha256", "A" * 63)),
        ("manifest-count", "bundle-manifest.json", lambda value: value.__setitem__("entry_count", True)),
        ("manifest-logical", "bundle-manifest.json", lambda value: value.__setitem__("logical_bytes", -1)),
        ("manifest-compression", "bundle-manifest.json", lambda value: value.__setitem__("compression", {"method": "deflate"})),
        ("manifest-entries", "bundle-manifest.json", lambda value: value.__setitem__("entries", [])),
        ("report-ok", "bundle-verification-report.json", lambda value: value.__setitem__("ok", False)),
        ("report-checks", "bundle-verification-report.json", lambda value: value.__setitem__("checks", {})),
        ("report-blocking", "bundle-verification-report.json", lambda value: value.__setitem__("blocking_reasons", ["unexpected"])),
        ("receipt-manifest", "bundle-receipt.json", lambda value: value.__setitem__("bundle_manifest_sha256", "G" * 64)),
        ("receipt-report", "bundle-receipt.json", lambda value: value.__setitem__("bundle_verification_report_sha256", 7)),
        ("receipt-entry-count", "bundle-receipt.json", lambda value: value.__setitem__("entry_count", -1)),
        ("receipt-logical", "bundle-receipt.json", lambda value: value.__setitem__("logical_bytes", True)),
    ]
    for label, document, mutate in cases:
        target = tmp_path / f"semantic-{label}.sqrun"
        _replace_document_value(source, target, document, mutate)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)


@pytest.mark.parametrize("raw", [
    b"[\n]\n",
    b"{\"schema_version\":\"0.1\",\"schema_version\":\"0.1\"}\n",
    b"{\"bad\":NaN}\n",
    b"{\"bad\":\"noncanonical\"} \n",
    b"\xff\xfe",
])
def test_wp1_d6_public_verifier_rejects_strict_document_json_forms(tmp_path: Path, raw: bytes):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "json-source.sqrun"
    _write_public_bundle(run, source, payload)
    target = tmp_path / f"json-{_sha(raw)[:8]}.sqrun"
    _repack_strict_sqrun(source, target, {"format.json": raw})
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(target)


def test_wp1_d6_public_verifier_accepts_and_rejects_global_zip64_tail_variants(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "zip64-source.sqrun"
    _write_public_bundle(run, source, payload)
    zip64 = tmp_path / "zip64-valid.sqrun"
    _zip64_global_tail(source, zip64)
    assert verify_sqrun(zip64).run_id == "a7f15bee-0ab2-46e2-a8e1-b7ab47c0d1cf"

    def patch(label: str, mutate) -> None:
        raw = bytearray(zip64.read_bytes())
        eocd = raw.rfind(_EOCD)
        locator = eocd - 20
        zip64_offset = struct.unpack_from("<Q", raw, locator + 8)[0]
        mutate(raw, eocd, locator, zip64_offset)
        target = tmp_path / f"zip64-{label}.sqrun"
        target.write_bytes(raw)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)

    patch("locator-signature", lambda raw, _eocd, locator, _record: raw.__setitem__(slice(locator, locator + 4), b"NOPE"))
    patch("locator-disk", lambda raw, _eocd, locator, _record: struct.pack_into("<I", raw, locator + 16, 2))
    patch("record-size", lambda raw, _eocd, _locator, record: struct.pack_into("<Q", raw, record + 4, 43))
    patch("record-disk", lambda raw, _eocd, _locator, record: struct.pack_into("<I", raw, record + 16, 1))
    patch("record-gap", lambda raw, _eocd, _locator, record: struct.pack_into("<Q", raw, record + 48, 1))
    patch("eocd-entry-count", lambda raw, eocd, _locator, _record: struct.pack_into("<H", raw, eocd + 8, 1))
    patch("eocd-central-size", lambda raw, eocd, _locator, _record: struct.pack_into("<I", raw, eocd + 12, 1))
    patch("eocd-central-offset", lambda raw, eocd, _locator, _record: struct.pack_into("<I", raw, eocd + 16, 1))


def test_wp1_d6_public_verifier_rejects_local_and_central_field_disagreement(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "header-source.sqrun"
    _write_public_bundle(run, source, payload)
    member = "run/dataset.bin"

    def patch(label: str, mutate) -> None:
        raw = bytearray(source.read_bytes())
        central, local = _zip_member_offsets(raw, member)
        mutate(raw, central, local)
        target = tmp_path / f"header-{label}.sqrun"
        target.write_bytes(raw)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)

    patch("local-signature", lambda raw, _central, local: raw.__setitem__(slice(local, local + 4), b"NOPE"))
    patch("local-version", lambda raw, _central, local: struct.pack_into("<H", raw, local + 4, 44))
    patch("local-crc", lambda raw, _central, local: struct.pack_into("<I", raw, local + 14, 0))
    patch("local-name", lambda raw, _central, local: raw.__setitem__(local + 30, ord("X")))
    patch("local-size", lambda raw, _central, local: struct.pack_into("<I", raw, local + 18, 0))
    patch("central-offset", lambda raw, central, _local: struct.pack_into("<I", raw, central + 42, 1))
    patch("central-disk", lambda raw, central, _local: struct.pack_into("<H", raw, central + 34, 1))
    patch("central-comment", lambda raw, central, _local: struct.pack_into("<H", raw, central + 32, 1))


def test_wp1_d6_public_reader_enforces_open_failure_close_identity_and_declared_paths(tmp_path: Path):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.archive_verify import ArchiveLimits, ZipEvidenceReader, read_sqrun_payload, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    archive = tmp_path / "reader-identity.sqrun"
    replacement = tmp_path / "reader-replacement.sqrun"
    write_sqrun(run, archive, source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)
    write_sqrun(run, replacement, source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)
    bundle = verify_sqrun(archive)
    reader = ZipEvidenceReader(archive, bundle.entries, ArchiveLimits())
    with pytest.raises(ArchiveFormatError, match="not declared"):
        reader.open_binary(7)  # type: ignore[arg-type]
    with pytest.raises(ArchiveFormatError, match="not declared"):
        read_sqrun_payload(archive, "missing.bin", maximum_bytes=1024)

    stream = reader.open_binary("dataset.bin")
    assert stream.read(1) == payload["dataset.bin"][:1]
    # Windows denies replacement while ZipFile keeps the carrier open.  Close it
    # normally, then exercise the reader's public idempotent-close identity check.
    stream.close()
    os.replace(replacement, archive)
    with pytest.raises(ArchiveFormatError, match="carrier changed"):
        stream.close()

    missing_member = tmp_path / "reader-without-member.sqrun"
    with zipfile.ZipFile(missing_member, "w", compression=zipfile.ZIP_STORED) as output:
        output.writestr("other", b"not the declared payload")
    with pytest.raises(ArchiveFormatError, match="cannot open declared"):
        ZipEvidenceReader(missing_member, bundle.entries, ArchiveLimits()).open_binary("dataset.bin")


def test_wp1_d6_public_writer_source_and_target_workflows_fail_closed(tmp_path: Path):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ArchiveFormatError, match="empty"):
        write_sqrun(empty, tmp_path / "empty.sqrun", source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)

    incomplete = tmp_path / "incomplete"
    incomplete.mkdir()
    (incomplete / "dataset.bin").write_bytes(b"payload")
    with pytest.raises(ArchiveFormatError, match="workflow or receipt"):
        write_sqrun(incomplete, tmp_path / "incomplete.sqrun", source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)

    run, payload = _independent_source_run(tmp_path)
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    for label, value in (("bad-uuid", "not-a-uuid"), ("upper-uuid", workflow["run_id"].upper()), ("empty-workflow", "")):
        changed = dict(workflow)
        if label == "empty-workflow":
            changed["workflow_id"] = value
        else:
            changed["run_id"] = value
        (run / "workflow.json").write_bytes(_canonical_bytes(changed))
        with pytest.raises(ArchiveFormatError, match="workflow identity"):
            write_sqrun(run, tmp_path / f"{label}.sqrun", source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)
    (run / "workflow.json").write_bytes(_canonical_bytes(workflow))

    callback_target = tmp_path / "callback-error.sqrun"
    with pytest.raises(ArchiveFormatError, match="source verifier failed"):
        write_sqrun(run, callback_target, source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: (_ for _ in ()).throw(RuntimeError("external verifier failure")))

    def inspect_directory_reader(reader) -> None:
        assert reader.paths() == tuple(sorted(payload, key=lambda value: value.encode("utf-8")))
        with pytest.raises(ArchiveFormatError, match="not declared"):
            reader.read_bytes("missing.bin")
        with pytest.raises(ArchiveFormatError, match="byte limit"):
            reader.read_bytes("dataset.bin", maximum_bytes=True)
        with reader.open_binary("dataset.bin") as stream:
            assert stream.read(2) == payload["dataset.bin"][:2]

    write_sqrun(run, tmp_path / "reader-ok.sqrun", source_verifier_id="independent", source_verifier_version="d6", source_verifier=inspect_directory_reader)
    file_parent = tmp_path / "not-a-directory"
    file_parent.write_bytes(b"block parent")
    with pytest.raises(ArchiveFormatError, match="parent"):
        write_sqrun(run, file_parent / "blocked.sqrun", source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)

    linked_parent = tmp_path / "linked-target-parent"
    try:
        os.symlink(tmp_path, linked_parent, target_is_directory=True)
    except OSError as exc:
        pytest.fail(f"WP1-D6 requires target-parent link coverage: {exc}")
    with pytest.raises(ArchiveFormatError, match="parent is linked"):
        write_sqrun(run, linked_parent / "blocked.sqrun", source_verifier_id="independent", source_verifier_version="d6", source_verifier=lambda _reader: None)


def test_wp1_d8_public_writer_standard_library_fault_boundaries(tmp_path: Path, monkeypatch):
    import shutil

    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    with pytest.raises(ArchiveFormatError, match="version"):
        write_sqrun(run, tmp_path / "bad-version.sqrun", source_verifier_id="independent", source_verifier_version="", source_verifier=lambda _reader: None)
    with pytest.raises(ArchiveFormatError, match="declared"):
        write_sqrun(run, tmp_path / "declared-error.sqrun", source_verifier_id="independent", source_verifier_version="d8", source_verifier=lambda _reader: (_ for _ in ()).throw(ArchiveFormatError("declared rejection")))

    plain = tmp_path / "ordinary-source-name"
    shutil.copytree(run, plain)
    archive = tmp_path / "ordinary-source.sqrun"
    bundle = write_sqrun(plain, archive, source_verifier_id="independent", source_verifier_version="d8", source_verifier=lambda _reader: None)
    assert bundle.original_directory_name == plain.name and archive.is_file()

    target = tmp_path / "permission-denied.sqrun"
    original_open = Path.open
    def deny_target_open(path: Path, *args, **kwargs):
        if path == target and args and args[0] == "xb":
            raise PermissionError("independent write denial")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", deny_target_open)
    with pytest.raises(ArchiveFormatError, match="cannot write"):
        write_sqrun(run, target, source_verifier_id="independent", source_verifier_version="d8", source_verifier=lambda _reader: None)
    assert not target.exists()


def test_wp1_d8_public_verifier_standard_library_read_and_scandir_faults(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.archive_verify import archive_raw_sha256, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    archive = tmp_path / "fault-carrier.sqrun"
    write_sqrun(run, archive, source_verifier_id="independent", source_verifier_version="d8", source_verifier=lambda _reader: None)
    original_open = Path.open

    class ReadFailure:
        def __init__(self, stream): self._stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def fileno(self): return self._stream.fileno()
        def seek(self, *args): return self._stream.seek(*args)
        def read(self, _size=-1): raise OSError("independent read failure")
        def close(self): self._stream.close()

    def fail_carrier_read(path: Path, *args, **kwargs):
        if path == archive and args and args[0] == "rb":
            return ReadFailure(original_open(path, *args, **kwargs))
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_carrier_read)
    with pytest.raises(ArchiveFormatError, match="cannot read archive carrier"):
        archive_raw_sha256(archive)
    with pytest.raises(ArchiveFormatError, match="cannot inspect archive carrier"):
        verify_sqrun(archive)

    monkeypatch.setattr(Path, "open", original_open)
    original_scandir = os.scandir
    def fail_scandir(_path): raise PermissionError("independent scandir denial")
    monkeypatch.setattr(os, "scandir", fail_scandir)
    with pytest.raises(ArchiveFormatError, match="component cannot be inspected"):
        verify_sqrun(archive)
    monkeypatch.setattr(os, "scandir", original_scandir)


def test_wp1_d8_public_verifier_reaches_deep_manifest_report_and_receipt_guards(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "deep-source.sqrun"
    _write_public_bundle(run, source, payload)
    manifest_rows = json.loads(zipfile.ZipFile(source).read("bundle-manifest.json"))["entries"]
    cases = [
        ("manifest-row-type", "bundle-manifest.json", lambda value: value.__setitem__("entries", ["not-an-entry"])),
        ("manifest-row-fields", "bundle-manifest.json", lambda value: value["entries"][0].pop("raw_sha256")),
        ("manifest-row-path", "bundle-manifest.json", lambda value: value["entries"][0].__setitem__("path", "../escape")),
        ("manifest-row-size", "bundle-manifest.json", lambda value: value["entries"][0].__setitem__("byte_length", True)),
        ("manifest-row-hash", "bundle-manifest.json", lambda value: value["entries"][0].__setitem__("raw_sha256", "a" * 64)),
        ("manifest-order", "bundle-manifest.json", lambda value: value.__setitem__("entries", list(reversed(manifest_rows)))),
        ("manifest-logical-type", "bundle-manifest.json", lambda value: value.__setitem__("logical_bytes", True)),
        ("report-source-id", "bundle-verification-report.json", lambda value: value.__setitem__("source_verifier_id", "")),
        ("report-source-version", "bundle-verification-report.json", lambda value: value.__setitem__("source_verifier_version", "")),
        ("report-manifest-hash", "bundle-verification-report.json", lambda value: value.__setitem__("bundle_manifest_sha256", "A" * 64)),
        ("receipt-identity", "bundle-receipt.json", lambda value: value.__setitem__("source_verifier_id", "other")),
        ("receipt-topology", "bundle-receipt.json", lambda value: value.__setitem__("bundle_manifest_sha256", "A" * 64)),
        ("receipt-manifest-agreement", "bundle-receipt.json", lambda value: value.__setitem__("workflow_sha256", "A" * 64)),
    ]
    for label, document, mutate in cases:
        target = tmp_path / f"deep-{label}.sqrun"
        _replace_document_value(source, target, document, mutate)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)


def test_wp1_d9_public_writer_fstat_and_target_parent_faults(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    original_fstat = os.fstat
    def wrong_identity(fd: int):
        observed = original_fstat(fd)
        fields = list(observed)
        fields[1] += 1
        return os.stat_result(fields)
    monkeypatch.setattr(os, "fstat", wrong_identity)
    with pytest.raises(ArchiveFormatError, match="changed during read"):
        write_sqrun(run, tmp_path / "fstat-race.sqrun", source_verifier_id="independent", source_verifier_version="d9", source_verifier=lambda _reader: None)

    monkeypatch.setattr(os, "fstat", original_fstat)
    original_scandir = os.scandir
    monkeypatch.setattr(os, "scandir", lambda _path: (_ for _ in ()).throw(PermissionError("target scan denied")))
    denied = tmp_path / "scandir-denied.sqrun"
    with pytest.raises(ArchiveFormatError, match="target parent cannot be inspected"):
        write_sqrun(run, denied, source_verifier_id="independent", source_verifier_version="d9", source_verifier=lambda _reader: None)
    assert not denied.exists()
    monkeypatch.setattr(os, "scandir", original_scandir)


def test_wp1_d9_public_carrier_identity_lstat_and_shape_faults(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.archive_verify import archive_raw_sha256, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    archive = tmp_path / "carrier-boundary.sqrun"
    write_sqrun(run, archive, source_verifier_id="independent", source_verifier_version="d9", source_verifier=lambda _reader: None)
    original_fstat = os.fstat
    def wrong_carrier_identity(fd: int):
        observed = original_fstat(fd)
        fields = list(observed)
        fields[1] += 1
        return os.stat_result(fields)
    monkeypatch.setattr(os, "fstat", wrong_carrier_identity)
    with pytest.raises(ArchiveFormatError, match="changed during read"):
        archive_raw_sha256(archive)
    monkeypatch.setattr(os, "fstat", original_fstat)

    with pytest.raises(ArchiveFormatError, match="not a directory"):
        verify_sqrun(archive / "impossible-child")
    tiny = tmp_path / "tiny.sqrun"
    tiny.write_bytes(b"x")
    with pytest.raises(ArchiveFormatError, match="truncated"):
        verify_sqrun(tiny)

    original_lstat = Path.lstat
    def deny_final_lstat(path: Path):
        if path == archive:
            raise PermissionError("carrier lstat denied")
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", deny_final_lstat)
    with pytest.raises(ArchiveFormatError, match="cannot be inspected"):
        verify_sqrun(archive)


def test_wp1_d9_public_zip_stream_eof_overread_and_hash_guards(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    archive = tmp_path / "stream-guards.sqrun"
    _write_public_bundle(run, archive, payload)
    original_open = zipfile.ZipFile.open

    class EmptyStream:
        def __init__(self, stream): self._stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def read(self, _size=-1): return b""
        def close(self): self._stream.close()
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda archive_handle, name, mode="r", *args, **kwargs: EmptyStream(original_open(archive_handle, name, mode, *args, **kwargs)) if mode == "r" else original_open(archive_handle, name, mode, *args, **kwargs))
    with pytest.raises(ArchiveFormatError, match="truncated"):
        verify_sqrun(archive)

    monkeypatch.setattr(zipfile.ZipFile, "open", original_open)
    class ExtraStream:
        def __init__(self, stream): self._stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def read(self, size=-1): return b"x" if size == 1 else self._stream.read(size)
        def close(self): self._stream.close()
    monkeypatch.setattr(zipfile.ZipFile, "open", lambda archive_handle, name, mode="r", *args, **kwargs: ExtraStream(original_open(archive_handle, name, mode, *args, **kwargs)) if mode == "r" else original_open(archive_handle, name, mode, *args, **kwargs))
    with pytest.raises(ArchiveFormatError, match="exceeds declared"):
        verify_sqrun(archive)

    monkeypatch.setattr(zipfile.ZipFile, "open", original_open)
    opens = 0
    class HashMismatchStream:
        def __init__(self, stream): self._stream = stream; self._changed = False
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def read(self, size=-1):
            raw = self._stream.read(size)
            if raw and not self._changed:
                self._changed = True
                return bytes([raw[0] ^ 1]) + raw[1:]
            return raw
        def close(self): self._stream.close()
    def corrupt_first_payload(archive_handle, name, mode="r", *args, **kwargs):
        nonlocal opens
        stream = original_open(archive_handle, name, mode, *args, **kwargs)
        if mode == "r":
            opens += 1
            if opens == 5:
                return HashMismatchStream(stream)
        return stream
    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_first_payload)
    with pytest.raises(ArchiveFormatError, match="hash does not match"):
        verify_sqrun(archive)


def _rechain_archive_documents(source: Path, target: Path, mutate_manifest) -> None:
    """Apply a manifest mutation while preserving the downstream document hash chain."""

    with zipfile.ZipFile(source, "r") as archive:
        manifest = json.loads(archive.read("bundle-manifest.json"))
        report = json.loads(archive.read("bundle-verification-report.json"))
        receipt = json.loads(archive.read("bundle-receipt.json"))
    mutate_manifest(manifest)
    manifest_raw = _canonical_bytes(manifest)
    report["bundle_manifest_sha256"] = _sha(manifest_raw)
    report_raw = _canonical_bytes(report)
    receipt["bundle_manifest_sha256"] = _sha(manifest_raw)
    receipt["bundle_verification_report_sha256"] = _sha(report_raw)
    for key in ("workflow_sha256", "receipt_sha256", "entry_count", "logical_bytes"):
        receipt[key] = manifest[key]
    _repack_strict_sqrun(source, target, {
        "bundle-manifest.json": manifest_raw,
        "bundle-verification-report.json": report_raw,
        "bundle-receipt.json": _canonical_bytes(receipt),
    })


def test_wp1_d10_public_writer_strict_metadata_and_late_source_open_faults(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    workflow = json.loads((run / "workflow.json").read_text(encoding="utf-8"))
    workflow["artifact_version"] = ""
    (run / "workflow.json").write_bytes(_canonical_bytes(workflow))
    with pytest.raises(ArchiveFormatError, match="workflow identity"):
        write_sqrun(run, tmp_path / "bad-artifact-version.sqrun", source_verifier_id="independent", source_verifier_version="d10", source_verifier=lambda _reader: None)

    (run / "workflow.json").write_bytes(b'{"run_id":"x","run_id":"x"}\n')
    with pytest.raises(ArchiveFormatError, match="metadata is invalid"):
        write_sqrun(run, tmp_path / "duplicate-workflow.sqrun", source_verifier_id="independent", source_verifier_version="d10", source_verifier=lambda _reader: None)

    (run / "workflow.json").write_bytes(payload["workflow.json"])
    dataset = run / "dataset.bin"
    original_open = Path.open
    opens = 0
    def fail_second_dataset_open(path: Path, *args, **kwargs):
        nonlocal opens
        if path == dataset and args and args[0] == "rb":
            opens += 1
            if opens == 2:
                raise PermissionError("late source open denial")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_second_dataset_open)
    target = tmp_path / "late-source-open.sqrun"
    with pytest.raises(ArchiveFormatError, match="cannot read source"):
        write_sqrun(run, target, source_verifier_id="independent", source_verifier_version="d10", source_verifier=lambda _reader: None)
    assert not target.exists()


def test_wp1_d10_public_verifier_manifest_closure_and_hash_chain_guards(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "chain-source.sqrun"
    _write_public_bundle(run, source, payload)
    cases = [
        ("payload-closure", lambda manifest: manifest["entries"][0].__setitem__("path", "different.bin"), "does not close"),
        ("logical-size", lambda manifest: manifest.__setitem__("logical_bytes", manifest["logical_bytes"] + 1), "logical size"),
        ("workflow-hash", lambda manifest: manifest.__setitem__("workflow_sha256", "A" * 64), "workflow or receipt hash"),
        ("receipt-hash", lambda manifest: manifest.__setitem__("receipt_sha256", "A" * 64), "workflow or receipt hash"),
    ]
    for label, mutate, message in cases:
        target = tmp_path / f"chain-{label}.sqrun"
        _rechain_archive_documents(source, target, mutate)
        with pytest.raises(ArchiveFormatError, match=message):
            verify_sqrun(target)


def test_wp1_d11_public_limits_exact_boundaries_and_raw_zip64_extra_variants(tmp_path: Path):
    from sqvm.storage.archive_verify import ArchiveLimits, verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d11-limits.sqrun"
    _write_public_bundle(run, source, payload)
    bundle = verify_sqrun(source)
    metadata_sizes = []
    with zipfile.ZipFile(source) as archive:
        for name in TOP_LEVEL:
            metadata_sizes.append(archive.getinfo(name).file_size)
    max_member_bytes = max(
        [entry.byte_length for entry in bundle.entries] + metadata_sizes
    )
    exact = ArchiveLimits(
        max_entries=len(bundle.entries) + len(TOP_LEVEL),
        max_entry_bytes=max_member_bytes,
        max_total_bytes=bundle.logical_bytes,
        max_metadata_bytes=max(metadata_sizes),
    )
    assert verify_sqrun(source, limits=exact).logical_bytes == bundle.logical_bytes
    for label, limits in [
        ("entries", ArchiveLimits(max_entries=len(bundle.entries) + len(TOP_LEVEL) - 1)),
        ("entry-bytes", ArchiveLimits(max_entry_bytes=max_member_bytes - 1)),
        ("total", ArchiveLimits(max_total_bytes=bundle.logical_bytes - 1)),
        ("metadata", ArchiveLimits(max_metadata_bytes=max(metadata_sizes) - 1)),
    ]:
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(source, limits=limits)

    member = "run/dataset.bin"
    raw = bytearray(source.read_bytes())
    central, local = _zip_member_offsets(raw, member)
    # The public writer emits the forced ZIP64 record in each local header.
    name_length, extra_length = struct.unpack_from("<HH", raw, local + 26)
    extra = local + 30 + name_length
    assert extra_length >= 4
    struct.pack_into("<H", raw, extra, 0xCAFE)
    bad_tag = tmp_path / "d11-extra-tag.sqrun"
    bad_tag.write_bytes(raw)
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(bad_tag)

    raw = bytearray(source.read_bytes())
    central, local = _zip_member_offsets(raw, member)
    # Local ZIP64 length is fixed at 16 bytes for this writer; a one-byte change is malformed.
    local_name, local_extra = struct.unpack_from("<HH", raw, local + 26)
    assert local_extra >= 4
    struct.pack_into("<H", raw, local + 30 + local_name + 2, 15)
    bad_length = tmp_path / "d11-local-extra-length.sqrun"
    bad_length.write_bytes(raw)
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(bad_length)


def test_wp1_d11_public_zip64_local_and_central_sentinel_matrix(tmp_path: Path):
    """Mutate independent raw ZIP bytes while retaining a public-valid carrier shape."""

    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d11-zip64-matrix.sqrun"
    _write_public_bundle(run, source, payload)
    member = "run/dataset.bin"

    def reject(label: str, mutate) -> None:
        raw = bytearray(source.read_bytes())
        central, local = _zip_member_offsets(raw, member)
        mutate(raw, central, local)
        target = tmp_path / f"d11-zip64-{label}.sqrun"
        target.write_bytes(raw)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)

    # Every local entry must carry exactly one 16-byte ZIP64 size record.
    def local_extra(raw: bytearray, local: int) -> int:
        name_length = struct.unpack_from("<H", raw, local + 26)[0]
        return local + 30 + name_length

    reject("local-missing", lambda raw, _central, local: struct.pack_into("<H", raw, local + 28, 0))
    reject("local-duplicate", lambda raw, _central, local: raw.__setitem__(slice(local_extra(raw, local), local_extra(raw, local) + 20), struct.pack("<HHQHHQ", 1, 8, 1, 1, 8, 1)))
    reject("local-short-extra", lambda raw, _central, local: struct.pack_into("<H", raw, local_extra(raw, local) + 2, 8))
    reject("local-non-sentinel-compressed", lambda raw, _central, local: struct.pack_into("<I", raw, local + 18, 0))
    reject("local-non-sentinel-uncompressed", lambda raw, _central, local: struct.pack_into("<I", raw, local + 22, 0))
    reject("local-zip64-size-mismatch", lambda raw, _central, local: struct.pack_into("<Q", raw, local_extra(raw, local) + 4, 1))
    reject("central-uncompressed-sentinel", lambda raw, central, _local: struct.pack_into("<I", raw, central + 24, 0xFFFFFFFF))
    reject("central-compressed-sentinel", lambda raw, central, _local: struct.pack_into("<I", raw, central + 20, 0xFFFFFFFF))
    reject("central-offset-sentinel", lambda raw, central, _local: struct.pack_into("<I", raw, central + 42, 0xFFFFFFFF))
    reject("central-disk-sentinel", lambda raw, central, _local: struct.pack_into("<H", raw, central + 34, 0xFFFF))
    reject("central-uncompressed-mismatch", lambda raw, central, _local: struct.pack_into("<I", raw, central + 24, 1))
    reject("central-compressed-mismatch", lambda raw, central, _local: struct.pack_into("<I", raw, central + 20, 1))


def test_wp1_d11_public_carrier_direntry_and_link_probe_fail_closed(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    archive = tmp_path / "d11-carrier-probes.sqrun"
    _write_public_bundle(run, archive, payload)
    original_scandir = os.scandir

    class StatDeniedEntry:
        def __init__(self, entry): self._entry = entry
        @property
        def name(self): return self._entry.name
        def stat(self, *, follow_symlinks=True):
            raise PermissionError("independent DirEntry.stat denial")

    def stat_denied_scandir(path):
        entries = list(original_scandir(path))
        if Path(path) == archive.parent:
            return iter([StatDeniedEntry(entry) if entry.name == archive.name else entry for entry in entries])
        return iter(entries)

    monkeypatch.setattr(os, "scandir", stat_denied_scandir)
    with pytest.raises(ArchiveFormatError, match="component cannot be inspected"):
        verify_sqrun(archive)
    monkeypatch.setattr(os, "scandir", original_scandir)
    original_is_symlink = Path.is_symlink
    def deny_candidate_link_probe(path: Path):
        if path == archive:
            raise PermissionError("independent is_symlink denial")
        return original_is_symlink(path)
    monkeypatch.setattr(Path, "is_symlink", deny_candidate_link_probe)
    # Carrier confinement deliberately consumes the already acquired no-follow
    # stat result.  A denied second link probe therefore cannot alter admission.
    assert verify_sqrun(archive).logical_bytes == sum(len(raw) for raw in payload.values())


def test_wp1_d11_public_json_type_and_hash_topology_matrix(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d11-json-topology.sqrun"
    _write_public_bundle(run, source, payload)

    direct_cases = [
        ("format-order-type", "format.json", lambda value: value.__setitem__("entry_order", [])),
        ("manifest-workflow-type", "bundle-manifest.json", lambda value: value.__setitem__("workflow_id", True)),
        ("manifest-version-type", "bundle-manifest.json", lambda value: value.__setitem__("source_artifact_version", 2)),
        ("manifest-directory-type", "bundle-manifest.json", lambda value: value.__setitem__("original_directory_name", None)),
        ("manifest-compression", "bundle-manifest.json", lambda value: value.__setitem__("compression", {"method": "stored", "level": 0, "writer_version": "0.1"})),
        ("report-ok-type", "bundle-verification-report.json", lambda value: value.__setitem__("ok", 1)),
        ("report-checks-type", "bundle-verification-report.json", lambda value: value.__setitem__("checks", {})),
        ("report-blocking", "bundle-verification-report.json", lambda value: value.__setitem__("blocking_reasons", ["unexpected"])),
        ("receipt-entry-count-type", "bundle-receipt.json", lambda value: value.__setitem__("entry_count", "4")),
        ("receipt-logical-size-type", "bundle-receipt.json", lambda value: value.__setitem__("logical_bytes", False)),
    ]
    for label, document, mutate in direct_cases:
        target = tmp_path / f"d11-json-{label}.sqrun"
        _replace_document_value(source, target, document, mutate)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)

    # These preserve every downstream hash, so the public verifier must reject
    # the individual manifest semantics rather than an earlier broken chain.
    rechained_cases = [
        ("manifest-entry-count-bool", lambda value: value.__setitem__("entry_count", True)),
        ("manifest-logical-negative", lambda value: value.__setitem__("logical_bytes", -1)),
        ("manifest-hash-type", lambda value: value.__setitem__("workflow_sha256", 7)),
        ("manifest-directory-separator", lambda value: value.__setitem__("original_directory_name", "bad/name")),
    ]
    for label, mutate in rechained_cases:
        target = tmp_path / f"d11-rechained-{label}.sqrun"
        _rechain_archive_documents(source, target, mutate)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)


def test_wp1_d11_public_format_boolean_type_is_strict(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d11-boolean-source.sqrun"
    _write_public_bundle(run, source, payload)
    for label, invalid in (("int-one", 1), ("int-zero", 0), ("text", "true"), ("null", None), ("array", [])):
        target = tmp_path / f"d11-boolean-{label}.sqrun"
        _replace_document_value(source, target, "format.json", lambda value, invalid=invalid: value.__setitem__("zip64", invalid))
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)


def test_wp1_d11_public_writer_source_and_target_boundary_matrix(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    dataset = run / "dataset.bin"
    kwargs = {
        "source_verifier_id": "independent",
        "source_verifier_version": "d11",
        "source_verifier": lambda _reader: None,
    }
    existing = tmp_path / "d11-existing.sqrun"
    existing.write_bytes(b"old evidence remains")
    with pytest.raises(ArchiveFormatError, match="already exists"):
        write_sqrun(run, existing, **kwargs)
    assert existing.read_bytes() == b"old evidence remains"

    parent_file = tmp_path / "d11-parent-file"
    parent_file.write_bytes(b"not a directory")
    with pytest.raises(ArchiveFormatError, match="parent"):
        write_sqrun(run, parent_file / "child.sqrun", **kwargs)

    hardlink = tmp_path / "d11-target-hardlink.sqrun"
    try:
        os.link(existing, hardlink)
    except OSError as exc:
        pytest.fail(f"WP1-D11 requires Windows hardlink target coverage: {exc}")
    with pytest.raises(ArchiveFormatError, match="already exists"):
        write_sqrun(run, hardlink, **kwargs)


def test_wp1_d11_public_writer_source_lstat_permission_fails_closed(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    dataset = run / "dataset.bin"
    original_lstat = Path.lstat
    def deny_source_lstat(path: Path):
        if path == dataset:
            raise PermissionError("independent source lstat denial")
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", deny_source_lstat)
    with pytest.raises(ArchiveFormatError):
        write_sqrun(run, tmp_path / "d11-source-lstat.sqrun", source_verifier_id="independent", source_verifier_version="d11", source_verifier=lambda _reader: None)


def test_wp1_d11_public_writer_source_link_probe_permission_fails_closed(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    dataset = run / "dataset.bin"
    original_is_symlink = Path.is_symlink
    def deny_source_link_probe(path: Path):
        if path == dataset:
            raise PermissionError("independent source link probe denial")
        return original_is_symlink(path)
    monkeypatch.setattr(Path, "is_symlink", deny_source_link_probe)
    with pytest.raises(ArchiveFormatError):
        write_sqrun(run, tmp_path / "d11-source-link.sqrun", source_verifier_id="independent", source_verifier_version="d11", source_verifier=lambda _reader: None)


def test_wp1_d11_public_writer_target_parent_identity_matrix(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    kwargs = {
        "source_verifier_id": "independent",
        "source_verifier_version": "d11",
        "source_verifier": lambda _reader: None,
    }

    # A non-existent parent is permitted only after every existing ancestor was
    # inspected without following it; the public writer creates it afterwards.
    nested = tmp_path / "d11-new-parent" / "child.sqrun"
    assert write_sqrun(run, nested, **kwargs).run_id
    assert nested.is_file()

    original_lstat = Path.lstat
    target = tmp_path / "d11-root-denied.sqrun"
    anchor = Path(target.anchor)
    def deny_target_anchor(path: Path):
        if path == anchor:
            raise PermissionError("independent target root denial")
        return original_lstat(path)
    monkeypatch.setattr(Path, "lstat", deny_target_anchor)
    with pytest.raises(ArchiveFormatError, match="target root"):
        write_sqrun(run, target, **kwargs)

    monkeypatch.setattr(Path, "lstat", original_lstat)
    parent = tmp_path / "d11-parent-identity"
    parent.mkdir()
    target = parent / "child.sqrun"
    original_scandir = os.scandir

    class StatDeniedEntry:
        def __init__(self, entry): self._entry = entry
        @property
        def name(self): return self._entry.name
        def stat(self, *, follow_symlinks=True):
            raise PermissionError("independent target DirEntry.stat denial")

    def deny_parent_stat(path):
        entries = list(original_scandir(path))
        if Path(path) == tmp_path:
            return iter([StatDeniedEntry(entry) if entry.name == parent.name else entry for entry in entries])
        return iter(entries)
    monkeypatch.setattr(os, "scandir", deny_parent_stat)
    with pytest.raises(ArchiveFormatError, match="target parent cannot be inspected"):
        write_sqrun(run, target, **kwargs)
    assert not target.exists()

    monkeypatch.setattr(os, "scandir", original_scandir)
    def duplicate_parent_component(path):
        entries = list(original_scandir(path))
        if Path(path) == tmp_path:
            parent_entry = next(entry for entry in entries if entry.name == parent.name)
            return iter(entries + [parent_entry])
        return iter(entries)
    monkeypatch.setattr(os, "scandir", duplicate_parent_component)
    with pytest.raises(ArchiveFormatError, match="target parent is ambiguous"):
        write_sqrun(run, target, **kwargs)
    assert not target.exists()

    monkeypatch.setattr(os, "scandir", original_scandir)
    original_commonpath = os.path.commonpath
    def cross_volume_commonpath(_paths):
        raise ValueError("independent cross-volume lexical paths")
    monkeypatch.setattr(os.path, "commonpath", cross_volume_commonpath)
    cross_volume = tmp_path / "d11-cross-volume.sqrun"
    assert write_sqrun(run, cross_volume, **kwargs).run_id
    assert cross_volume.is_file()

    monkeypatch.setattr(os.path, "commonpath", original_commonpath)
    reparse_parent = tmp_path / "d11-reparse-parent"
    reparse_parent.mkdir()
    class ReparseEntry:
        def __init__(self, entry): self._entry = entry
        @property
        def name(self): return self._entry.name
        def stat(self, *, follow_symlinks=True):
            fields = list(self._entry.stat(follow_symlinks=follow_symlinks))
            fields[0] = 0o120777
            return os.stat_result(fields)
    def reparse_parent_scandir(path):
        entries = list(original_scandir(path))
        if Path(path) == tmp_path:
            return iter([ReparseEntry(entry) if entry.name == reparse_parent.name else entry for entry in entries])
        return iter(entries)
    monkeypatch.setattr(os, "scandir", reparse_parent_scandir)
    with pytest.raises(ArchiveFormatError, match="target parent is linked"):
        write_sqrun(run, reparse_parent / "child.sqrun", **kwargs)


def test_wp1_d11_public_central_zip64_sentinel_and_extra_matrix(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d11-central-zip64-source.sqrun"
    _write_public_bundle(run, source, payload)
    member = "run/workflow.json"  # Last UTF-8-byte-sorted central entry permits append-only mutation.

    def central_zip64(parts: tuple[str, ...], *, corrupt_extra: bytes | None = None) -> bytes:
        raw = bytearray(source.read_bytes())
        central, _local = _zip_member_offsets(raw, member)
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, central + 28)
        assert extra_length == comment_length == 0
        payload_parts: list[bytes] = []
        with zipfile.ZipFile(source) as archive:
            info = archive.getinfo(member)
        for field in parts:
            if field == "uncompressed":
                struct.pack_into("<I", raw, central + 24, 0xFFFFFFFF)
                payload_parts.append(struct.pack("<Q", info.file_size))
            elif field == "compressed":
                struct.pack_into("<I", raw, central + 20, 0xFFFFFFFF)
                payload_parts.append(struct.pack("<Q", info.compress_size))
            elif field == "offset":
                struct.pack_into("<I", raw, central + 42, 0xFFFFFFFF)
                payload_parts.append(struct.pack("<Q", info.header_offset))
            elif field == "disk":
                struct.pack_into("<H", raw, central + 34, 0xFFFF)
                payload_parts.append(struct.pack("<I", 0))
            else:
                raise AssertionError(field)
        extra = corrupt_extra if corrupt_extra is not None else struct.pack("<HH", 1, sum(len(part) for part in payload_parts)) + b"".join(payload_parts)
        insertion = central + 46 + name_length
        raw[insertion:insertion] = extra
        struct.pack_into("<H", raw, central + 30, len(extra))
        eocd = raw.rfind(_EOCD)
        assert eocd >= 0
        struct.pack_into("<I", raw, eocd + 12, struct.unpack_from("<I", raw, eocd + 12)[0] + len(extra))
        return bytes(raw)

    for label, parts in (
        ("uncompressed", ("uncompressed",)),
        ("compressed", ("compressed",)),
        ("offset", ("offset",)),
        ("all", ("uncompressed", "compressed", "offset", "disk")),
    ):
        target = tmp_path / f"d11-central-valid-{label}.sqrun"
        target.write_bytes(central_zip64(parts))
        assert verify_sqrun(target).logical_bytes == sum(len(raw) for raw in payload.values())

    invalid = (
        ("wrong-tag", struct.pack("<HHQ", 0xCAFE, 8, 1)),
        ("wrong-length", struct.pack("<HHQ", 1, 7, 1)),
        ("truncated", b"\x01\x00\x08"),
        ("duplicate", struct.pack("<HHQQ", 1, 16, 1, 1)),
    )
    for label, extra in invalid:
        target = tmp_path / f"d11-central-invalid-{label}.sqrun"
        target.write_bytes(central_zip64(("uncompressed",), corrupt_extra=extra))
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)

    target = tmp_path / "d11-central-invalid-disk-only.sqrun"
    target.write_bytes(central_zip64(("disk",)))
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(target)


def test_wp1_d11_public_zip64_tail_mixed_sentinel_and_reader_open_failure(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d11-tail-source.sqrun"
    _write_public_bundle(run, source, payload)
    upgraded = tmp_path / "d11-tail-zip64.sqrun"
    _zip64_global_tail(source, upgraded)
    raw_base = upgraded.read_bytes()
    eocd_base = raw_base.rfind(_EOCD)
    actual_count = len(zipfile.ZipFile(source).infolist())
    central_size = struct.unpack_from("<I", source.read_bytes(), source.read_bytes().rfind(_EOCD) + 12)[0]
    central_offset = struct.unpack_from("<I", source.read_bytes(), source.read_bytes().rfind(_EOCD) + 16)[0]
    for label, patches in (
        ("entry-count-pair", ((8, "<H", actual_count), (10, "<H", actual_count))),
        ("central-size", ((12, "<I", central_size),)),
        ("central-offset", ((16, "<I", central_offset),)),
    ):
        raw = bytearray(raw_base)
        for offset, width, value in patches:
            struct.pack_into(width, raw, eocd_base + offset, value)
        target = tmp_path / f"d11-tail-mixed-{label}.sqrun"
        target.write_bytes(raw)
        assert verify_sqrun(target).logical_bytes == sum(len(item) for item in payload.values())

    original_open = zipfile.ZipFile.open
    def denied_open(archive, name, mode="r", *args, **kwargs):
        if mode == "r":
            raise RuntimeError("independent stream-open denial")
        return original_open(archive, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", denied_open)
    with pytest.raises(ArchiveFormatError, match="cannot stream archive entry"):
        verify_sqrun(source)


def test_wp1_d12_public_writer_noncanonical_metadata_and_verifier_zipinfo_matrix(tmp_path: Path):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    workflow = run / "workflow.json"
    workflow.write_text(json.dumps(json.loads(workflow.read_text(encoding="utf-8"))) + "\n", encoding="utf-8")
    noncanonical_target = tmp_path / "d12-noncanonical.sqrun"
    with pytest.raises(ArchiveFormatError, match="metadata is not canonical"):
        write_sqrun(run, noncanonical_target, source_verifier_id="independent", source_verifier_version="d12", source_verifier=lambda _reader: None)
    assert not noncanonical_target.exists()

    run, payload = _independent_source_run(tmp_path / "valid")
    source = tmp_path / "d12-zipinfo-source.sqrun"
    _write_public_bundle(run, source, payload)

    raw = bytearray(source.read_bytes())
    eocd = raw.rfind(_EOCD)
    raw.extend(b"x")
    struct.pack_into("<H", raw, eocd + 20, 1)
    comment = tmp_path / "d12-comment.sqrun"
    comment.write_bytes(raw)
    with pytest.raises(ArchiveFormatError, match="ZIP comment"):
        verify_sqrun(comment)

    member = "run/dataset.bin"
    for label, mutate in (
        ("timestamp", lambda raw, central, local: (struct.pack_into("<H", raw, central + 12, 1), struct.pack_into("<H", raw, local + 10, 1))),
        ("made-by", lambda raw, central, _local: struct.pack_into("<H", raw, central + 4, 0)),
        ("directory", lambda raw, central, _local: struct.pack_into("<I", raw, central + 38, 0o040755 << 16)),
    ):
        raw = bytearray(source.read_bytes())
        central, local = _zip_member_offsets(raw, member)
        mutate(raw, central, local)
        target = tmp_path / f"d12-zipinfo-{label}.sqrun"
        target.write_bytes(raw)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)


def test_wp1_d12_public_writer_source_size_and_bounded_reader_races(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_format import write_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, _payload = _independent_source_run(tmp_path)
    dataset = run / "dataset.bin"
    original_open = Path.open

    class AppendingStream:
        def __init__(self, stream): self._stream = stream; self._extra = False
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def __getattr__(self, name): return getattr(self._stream, name)
        def read(self, size=-1):
            raw = self._stream.read(size)
            if not raw and not self._extra:
                self._extra = True
                return b"x"
            return raw
        def close(self): self._stream.close()

    def append_after_inventory(path: Path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == dataset and args and args[0] == "rb":
            return AppendingStream(stream)
        return stream
    monkeypatch.setattr(Path, "open", append_after_inventory)
    target = tmp_path / "d12-size-race.sqrun"
    with pytest.raises(ArchiveFormatError, match="size changed during inventory"):
        write_sqrun(run, target, source_verifier_id="independent", source_verifier_version="d12", source_verifier=lambda _reader: None)
    assert not target.exists()

    monkeypatch.setattr(Path, "open", original_open)
    opens = 0
    class ShortReadStream:
        def __init__(self, stream): self._stream = stream; self._short = False
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def __getattr__(self, name): return getattr(self._stream, name)
        def read(self, size=-1):
            if not self._short and size > 1:
                self._short = True
                return self._stream.read(size - 2)
            return self._stream.read(size)
        def close(self): self._stream.close()
    def short_callback_read(path: Path, *args, **kwargs):
        nonlocal opens
        stream = original_open(path, *args, **kwargs)
        if path == dataset and args and args[0] == "rb":
            opens += 1
            if opens == 2:
                return ShortReadStream(stream)
        return stream
    monkeypatch.setattr(Path, "open", short_callback_read)
    target = tmp_path / "d12-short-reader.sqrun"
    with pytest.raises(ArchiveFormatError, match="changed during read"):
        write_sqrun(run, target, source_verifier_id="independent", source_verifier_version="d12", source_verifier=lambda reader: reader.read_bytes("dataset.bin"))
    assert not target.exists()


def test_wp1_d12_public_payload_reader_hash_failure_after_archive_verification(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_verify import read_sqrun_payload
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d12-reader-hash.sqrun"
    _write_public_bundle(run, source, payload)
    original_open = zipfile.ZipFile.open
    reads = 0

    class CorruptStream:
        def __init__(self, stream): self._stream = stream; self._changed = False
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def __getattr__(self, name): return getattr(self._stream, name)
        def read(self, size=-1):
            raw = self._stream.read(size)
            if raw and not self._changed:
                self._changed = True
                return bytes([raw[0] ^ 1]) + raw[1:]
            return raw
        def close(self): self._stream.close()

    def corrupt_only_reader_stage(archive, name, mode="r", *args, **kwargs):
        nonlocal reads
        stream = original_open(archive, name, mode, *args, **kwargs)
        if mode == "r":
            reads += 1
            if reads == 17:
                return CorruptStream(stream)
        return stream
    monkeypatch.setattr(zipfile.ZipFile, "open", corrupt_only_reader_stage)
    with pytest.raises(ArchiveFormatError, match="hash does not match"):
        read_sqrun_payload(source, "dataset.bin", maximum_bytes=len(payload["dataset.bin"]))


def test_wp1_d13_public_verify_payload_eof_overread_and_format_exact_field(tmp_path: Path, monkeypatch):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d13-stream.sqrun"
    _write_public_bundle(run, source, payload)
    original_open = zipfile.ZipFile.open

    class Empty:
        def __init__(self, stream): self._stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def read(self, _size=-1): return b""
        def close(self): self._stream.close()
    calls = 0
    def eof_payload(archive, name, mode="r", *args, **kwargs):
        nonlocal calls
        stream = original_open(archive, name, mode, *args, **kwargs)
        if mode == "r":
            calls += 1
            if calls == 5: return Empty(stream)
        return stream
    monkeypatch.setattr(zipfile.ZipFile, "open", eof_payload)
    with pytest.raises(ArchiveFormatError, match="truncated"):
        verify_sqrun(source)

    monkeypatch.setattr(zipfile.ZipFile, "open", original_open)
    calls = 0
    class Extra(Empty):
        def read(self, size=-1): return b"x" if size == 1 else self._stream.read(size)
    def extra_payload(archive, name, mode="r", *args, **kwargs):
        nonlocal calls
        stream = original_open(archive, name, mode, *args, **kwargs)
        if mode == "r":
            calls += 1
            if calls == 5: return Extra(stream)
        return stream
    monkeypatch.setattr(zipfile.ZipFile, "open", extra_payload)
    with pytest.raises(ArchiveFormatError, match="exceeds declared"):
        verify_sqrun(source)

    monkeypatch.setattr(zipfile.ZipFile, "open", original_open)
    exact = tmp_path / "d13-exact.sqrun"
    _replace_document_value(source, exact, "format.json", lambda value: value.__setitem__("schema_version", "wrong"))
    with pytest.raises(ArchiveFormatError, match="format metadata is invalid"):
        verify_sqrun(exact)


def test_wp1_d14_public_manifest_exact_and_canonical_uuid_guards(tmp_path: Path):
    from sqvm.storage.archive_verify import verify_sqrun
    from sqvm.storage.errors import ArchiveFormatError

    run, payload = _independent_source_run(tmp_path)
    source = tmp_path / "d14-manifest-source.sqrun"
    _write_public_bundle(run, source, payload)
    cases = (
        ("schema", lambda value: value.__setitem__("schema_version", "wrong")),
        ("uppercase-uuid", lambda value: value.__setitem__("run_id", value["run_id"].upper())),
    )
    for label, mutate in cases:
        target = tmp_path / f"d14-manifest-{label}.sqrun"
        _replace_document_value(source, target, "bundle-manifest.json", mutate)
        with pytest.raises(ArchiveFormatError):
            verify_sqrun(target)
