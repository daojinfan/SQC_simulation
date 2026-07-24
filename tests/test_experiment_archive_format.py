from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid
import zipfile

import pytest

from sqvm.storage.archive_format import canonical_archive_json_bytes, write_sqrun
from sqvm.storage.archive_verify import (
    ArchiveLimits,
    archive_raw_sha256,
    read_sqrun_payload,
    validate_archive_entry_name,
    verify_sqrun,
)
from sqvm.storage.errors import ArchiveFormatError


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _source_run(root: Path) -> tuple[Path, dict[str, bytes]]:
    run_id = "a7f15bee-0ab2-46e2-a8e1-b7ab47c0d1cf"
    run = root / f"qubit_spectroscopy_{run_id}"
    run.mkdir()
    workflow = {
        "artifact_version": "0.2",
        "run_id": run_id,
        "workflow_id": "qubit_spectroscopy_scan_v1",
    }
    payload = {
        "dataset.bin": b"\x00\xff\x10archive-format\x00",
        "execution/point-000/result.json": canonical_archive_json_bytes({"point": 0, "value": 1.25}),
        "receipt.json": canonical_archive_json_bytes({"run_id": run_id, "status": "completed"}),
        "workflow.json": canonical_archive_json_bytes(workflow),
    }
    for relative, raw in payload.items():
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return run, payload


def _valid_verifier(reader) -> None:
    assert "workflow.json" in reader.paths()
    assert reader.read_bytes("workflow.json")


def _write(run: Path, target: Path, *, verifier=_valid_verifier):
    return write_sqrun(
        run,
        target,
        source_verifier_id="unit",
        source_verifier_version="0.1",
        source_verifier=verifier,
    )


def _rewrite(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for info in archive.infolist():
            name, raw, compression = mutate(info.filename, archive.read(info.filename), info.compress_type)
            updated = zipfile.ZipInfo(name, date_time=info.date_time)
            updated.compress_type = compression
            updated.create_system = info.create_system
            updated.external_attr = info.external_attr
            output.writestr(updated, raw)


def _rewrite_with_info(source: Path, target: Path, mutate) -> None:
    with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as output:
        for info in archive.infolist():
            updated, raw = mutate(info, archive.read(info.filename))
            output.writestr(updated, raw)


def _set_zip_flags(path: Path, bits: int) -> None:
    raw = bytearray(path.read_bytes())
    offset = 0
    while (offset := raw.find(b"PK\x03\x04", offset)) >= 0:
        raw[offset + 6] |= bits
        offset += 4
    offset = 0
    while (offset := raw.find(b"PK\x01\x02", offset)) >= 0:
        raw[offset + 8] |= bits
        offset += 4
    path.write_bytes(raw)


def _replace_zip_flags(path: Path, flags: int) -> None:
    raw = bytearray(path.read_bytes())
    offset = 0
    while (offset := raw.find(b"PK\x03\x04", offset)) >= 0:
        raw[offset + 6:offset + 8] = flags.to_bytes(2, "little")
        offset += 4
    offset = 0
    while (offset := raw.find(b"PK\x01\x02", offset)) >= 0:
        raw[offset + 8:offset + 10] = flags.to_bytes(2, "little")
        offset += 4
    path.write_bytes(raw)


def _set_central_version(path: Path, *, made_by: int | None = None, needed: int | None = None) -> None:
    raw = bytearray(path.read_bytes())
    offset = 0
    while (offset := raw.find(b"PK\x01\x02", offset)) >= 0:
        if made_by is not None:
            raw[offset + 4:offset + 6] = made_by.to_bytes(2, "little")
        if needed is not None:
            raw[offset + 6:offset + 8] = needed.to_bytes(2, "little")
        offset += 4
    path.write_bytes(raw)


def _set_local_needed_version(path: Path, needed: int) -> None:
    raw = bytearray(path.read_bytes())
    offset = 0
    while (offset := raw.find(b"PK\x03\x04", offset)) >= 0:
        raw[offset + 4:offset + 6] = needed.to_bytes(2, "little")
        offset += 4
    path.write_bytes(raw)


def test_writer_is_deterministic_and_keeps_raw_archive_hash_external(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    first = tmp_path / "first.sqrun"
    second = tmp_path / "second.sqrun"

    first_bundle = _write(run, first)
    second_bundle = _write(run, second)

    assert first.read_bytes() == second.read_bytes()
    assert archive_raw_sha256(first) == archive_raw_sha256(second)
    assert first_bundle == second_bundle
    assert "archive" not in json.dumps(first_bundle.to_dict()).lower()
    with zipfile.ZipFile(first) as container:
        assert [info.filename for info in container.infolist()] == sorted(
            (info.filename for info in container.infolist()), key=lambda value: value.encode("utf-8")
        )


def test_round_trip_verifies_streams_payload_without_extracting(tmp_path: Path) -> None:
    run, payload = _source_run(tmp_path)
    archive = tmp_path / "run.sqrun"
    _write(run, archive)

    bundle = verify_sqrun(archive)

    assert bundle.run_id == "a7f15bee-0ab2-46e2-a8e1-b7ab47c0d1cf"
    assert [entry.path for entry in bundle.entries] == sorted(payload, key=lambda value: value.encode("utf-8"))
    assert {name: read_sqrun_payload(archive, name, maximum_bytes=len(raw)) for name, raw in payload.items()} == payload
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "mutation",
    ["payload", "manifest", "report", "receipt", "unknown_format_field", "path_escape", "backslash", "case_collision", "deflate"],
)
def test_verifier_rejects_tampered_records_and_zip_structure(tmp_path: Path, mutation: str) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    target = tmp_path / f"{mutation}.sqrun"
    _write(run, source)

    def mutate(name: str, raw: bytes, compression: int) -> tuple[str, bytes, int]:
        if mutation == "payload" and name == "run/dataset.bin":
            return name, raw[:-1] + bytes([raw[-1] ^ 1]), compression
        if mutation == "manifest" and name == "bundle-manifest.json":
            return name, raw.replace(b"qubit_spectroscopy_scan_v1", b"qubit_Spectroscopy_scan_v1", 1), compression
        if mutation == "report" and name == "bundle-verification-report.json":
            return name, raw.replace(b'"ok": true', b'"ok": false'), compression
        if mutation == "receipt" and name == "bundle-receipt.json":
            return name, raw.replace(b'"logical_bytes": ', b'"logical_bytes": 9', 1), compression
        if mutation == "unknown_format_field" and name == "format.json":
            value = json.loads(raw)
            value["unexpected"] = True
            return name, canonical_archive_json_bytes(value), compression
        if mutation == "path_escape" and name == "run/dataset.bin":
            return "run/../outside.bin", raw, compression
        if mutation == "backslash" and name == "run/dataset.bin":
            return "run\\outside.bin", raw, compression
        if mutation == "case_collision" and name == "run/dataset.bin":
            return "run/WORKFLOW.json", raw, compression
        if mutation == "deflate" and name == "run/dataset.bin":
            return name, raw, zipfile.ZIP_DEFLATED
        return name, raw, compression

    _rewrite(source, target, mutate)

    with pytest.raises(ArchiveFormatError):
        verify_sqrun(target)


def test_verifier_rejects_duplicate_entry_truncation_and_trailing_data(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write(run, source)

    duplicate = tmp_path / "duplicate.sqrun"
    duplicate.write_bytes(source.read_bytes())
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "a", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("run/dataset.bin", b"duplicate")
    with pytest.raises(ArchiveFormatError, match="duplicate"):
        verify_sqrun(duplicate)

    truncated = tmp_path / "truncated.sqrun"
    truncated.write_bytes(source.read_bytes()[:-17])
    with pytest.raises(ArchiveFormatError):
        verify_sqrun(truncated)

    trailing = tmp_path / "trailing.sqrun"
    trailing.write_bytes(source.read_bytes() + b"trailing")
    with pytest.raises(ArchiveFormatError, match="trailing"):
        verify_sqrun(trailing)


@pytest.mark.parametrize("name", ["", "/absolute", "run//empty", "run/../parent", "run\\backslash", "run/nul\x00entry", "run/ads:name"])
def test_entry_name_validator_rejects_every_unsafe_path_variant(name: str) -> None:
    with pytest.raises(ArchiveFormatError):
        validate_archive_entry_name(name)


@pytest.mark.parametrize("mode", [0o120777, 0o020666, 0o060644])
def test_verifier_rejects_unix_link_and_special_entry_modes(tmp_path: Path, mode: int) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    target = tmp_path / f"mode-{mode:o}.sqrun"
    _write(run, source)

    def mutate(info: zipfile.ZipInfo, raw: bytes) -> tuple[zipfile.ZipInfo, bytes]:
        updated = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        updated.compress_type = info.compress_type
        updated.create_system = 3
        updated.external_attr = mode << 16
        return updated, raw

    _rewrite_with_info(source, target, mutate)
    with pytest.raises(ArchiveFormatError, match="link or special"):
        verify_sqrun(target)


def test_verifier_rejects_unknown_extra_encryption_data_descriptor_and_comment(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write(run, source)

    extra = tmp_path / "extra.sqrun"
    def add_extra(info: zipfile.ZipInfo, raw: bytes) -> tuple[zipfile.ZipInfo, bytes]:
        updated = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        updated.compress_type = info.compress_type
        updated.create_system = info.create_system
        updated.create_version = info.create_version
        updated.extract_version = info.extract_version
        updated.flag_bits = info.flag_bits
        updated.internal_attr = info.internal_attr
        updated.external_attr = info.external_attr
        updated.extra = b"\xfe\xca\x00\x00"
        return updated, raw
    _rewrite_with_info(source, extra, add_extra)
    with pytest.raises(ArchiveFormatError, match="extra"):
        verify_sqrun(extra)

    for name, bits, expected in (("encrypted", 0x1, "encrypted"), ("descriptor", 0x8, "data descriptor")):
        flagged = tmp_path / f"{name}.sqrun"
        flagged.write_bytes(source.read_bytes())
        _set_zip_flags(flagged, bits)
        with pytest.raises(ArchiveFormatError, match=expected):
            verify_sqrun(flagged)

    commented = tmp_path / "comment.sqrun"
    commented.write_bytes(source.read_bytes())
    with zipfile.ZipFile(commented, "a") as archive:
        archive.comment = b"forbidden"
    with pytest.raises(ArchiveFormatError, match="comment"):
        verify_sqrun(commented)


def test_verifier_rejects_unapproved_flags_and_filename_encoding_mismatch(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write(run, source)

    harmless = tmp_path / "harmless-flag.sqrun"
    harmless.write_bytes(source.read_bytes())
    _set_zip_flags(harmless, 0x2)
    with pytest.raises(ArchiveFormatError, match="flags or filename encoding"):
        verify_sqrun(harmless)

    ascii_utf8 = tmp_path / "ascii-utf8.sqrun"
    ascii_utf8.write_bytes(source.read_bytes())
    _replace_zip_flags(ascii_utf8, 0x800)
    with pytest.raises(ArchiveFormatError, match="flags or filename encoding"):
        verify_sqrun(ascii_utf8)

    (run / "测量.bin").write_bytes(b"unicode-name")
    unicode_archive = tmp_path / "unicode.sqrun"
    _write(run, unicode_archive)
    nonascii_without_utf8 = tmp_path / "unicode-no-utf8.sqrun"
    nonascii_without_utf8.write_bytes(unicode_archive.read_bytes())
    _replace_zip_flags(nonascii_without_utf8, 0)
    with pytest.raises(ArchiveFormatError, match="flags or filename encoding"):
        verify_sqrun(nonascii_without_utf8)


def test_verifier_rejects_made_by_and_needed_version_mutations(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write(run, source)

    made_by = tmp_path / "made-by.sqrun"
    made_by.write_bytes(source.read_bytes())
    _set_central_version(made_by, made_by=(0 << 8) | 45)
    with pytest.raises(ArchiveFormatError, match="platform metadata"):
        verify_sqrun(made_by)

    needed = tmp_path / "needed.sqrun"
    needed.write_bytes(source.read_bytes())
    _set_local_needed_version(needed, 44)
    _set_central_version(needed, needed=44)
    with pytest.raises(ArchiveFormatError, match="platform metadata"):
        verify_sqrun(needed)


def test_verifier_enforces_streaming_entry_and_total_bounds(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)

    with pytest.raises(ArchiveFormatError, match="entry size"):
        verify_sqrun(archive, limits=ArchiveLimits(max_entry_bytes=1))
    with pytest.raises(ArchiveFormatError, match="total size"):
        verify_sqrun(archive, limits=ArchiveLimits(max_total_bytes=1))
    with pytest.raises(ArchiveFormatError, match="entry count"):
        verify_sqrun(archive, limits=ArchiveLimits(max_entries=1))
    with pytest.raises(ArchiveFormatError, match="payload byte limit"):
        read_sqrun_payload(archive, "dataset.bin", maximum_bytes=1)


def test_verifier_rejects_sfx_prefix_and_local_header_mismatch(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write(run, source)

    prefixed = tmp_path / "prefixed.sqrun"
    prefixed.write_bytes(b"SFX!" + source.read_bytes())
    with pytest.raises(ArchiveFormatError, match="SFX prefix"):
        verify_sqrun(prefixed)

    mismatched = tmp_path / "mismatched.sqrun"
    raw = bytearray(source.read_bytes())
    local = raw.index(b"PK\x03\x04")
    name_length = int.from_bytes(raw[local + 26:local + 28], "little")
    assert name_length > 0
    raw[local + 30] ^= 1
    mismatched.write_bytes(raw)
    with pytest.raises(ArchiveFormatError, match="local header"):
        verify_sqrun(mismatched)


def test_verifier_rejects_eocd_multidisk_and_central_directory_gap(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    source = tmp_path / "source.sqrun"
    _write(run, source)

    multidisk = tmp_path / "multidisk.sqrun"
    raw = bytearray(source.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    raw[eocd + 4:eocd + 6] = (1).to_bytes(2, "little")
    multidisk.write_bytes(raw)
    with pytest.raises(ArchiveFormatError, match="multi-disk"):
        verify_sqrun(multidisk)

    gapped = tmp_path / "gapped.sqrun"
    raw = bytearray(source.read_bytes())
    eocd = raw.rfind(b"PK\x05\x06")
    central_size = int.from_bytes(raw[eocd + 12:eocd + 16], "little")
    raw[eocd + 12:eocd + 16] = (central_size - 1).to_bytes(4, "little")
    gapped.write_bytes(raw)
    with pytest.raises(ArchiveFormatError, match="central-directory"):
        verify_sqrun(gapped)


def test_structural_payload_verification_never_uses_zipfile_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)
    original_read = zipfile.ZipFile.read

    def guarded_read(self, name, *args, **kwargs):
        filename = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if isinstance(filename, str) and filename.startswith("run/"):
            raise AssertionError("payload must be streamed")
        return original_read(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "read", guarded_read)
    assert verify_sqrun(archive).logical_bytes > 0


def test_verifier_performs_two_independent_reopen_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)
    import sqvm.storage.archive_verify as archive_verify

    original = archive_verify._open_zip
    calls = 0

    def counted(path: Path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(archive_verify, "_open_zip", counted)
    verify_sqrun(archive)
    assert calls == 2


def test_writer_fails_when_post_write_verification_bundle_differs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _payload = _source_run(tmp_path)
    import sqvm.storage.archive_verify as archive_verify

    original = archive_verify.verify_sqrun

    def wrong_bundle(*args, **kwargs):
        return replace(original(*args, **kwargs), source_verified=False)

    monkeypatch.setattr(archive_verify, "verify_sqrun", wrong_bundle)
    with pytest.raises(ArchiveFormatError, match="post-write"):
        _write(run, tmp_path / "post-write-mismatch.sqrun")


def test_archive_carrier_rejects_linked_and_reparse_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)

    symlink = tmp_path / "carrier-link.sqrun"
    os.symlink(archive, symlink)
    with pytest.raises(ArchiveFormatError, match="linked or reparse"):
        verify_sqrun(symlink)
    with pytest.raises(ArchiveFormatError, match="linked or reparse"):
        archive_raw_sha256(symlink)

    hardlink = tmp_path / "carrier-hardlink.sqrun"
    os.link(archive, hardlink)
    with pytest.raises(ArchiveFormatError, match="linked or not a regular"):
        verify_sqrun(hardlink)
    with pytest.raises(ArchiveFormatError, match="linked or not a regular"):
        archive_raw_sha256(hardlink)

    import sqvm.storage.archive_verify as archive_verify

    original = archive_verify._is_link_or_reparse

    def reparse_marked(path: Path, info: os.stat_result) -> bool:
        return path == archive or original(path, info)

    monkeypatch.setattr(archive_verify, "_is_link_or_reparse", reparse_marked)
    with pytest.raises(ArchiveFormatError, match="linked or reparse"):
        verify_sqrun(archive)


def test_archive_carrier_replacement_between_verification_passes_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)
    replacement = tmp_path / "replacement.sqrun"
    replacement.write_bytes(archive.read_bytes())
    import sqvm.storage.archive_verify as archive_verify

    original = archive_verify._verify_sqrun_once
    calls = 0

    def replace_after_first_pass(path: Path, policy):
        nonlocal calls
        result = original(path, policy)
        calls += 1
        if calls == 1:
            replacement.replace(archive)
        return result

    monkeypatch.setattr(archive_verify, "_verify_sqrun_once", replace_after_first_pass)
    with pytest.raises(ArchiveFormatError, match="carrier changed"):
        verify_sqrun(archive)


def test_writer_rejects_hardlinked_source_entry(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    original = run / "dataset.bin"
    hardlink = run / "hardlinked.bin"
    os.link(original, hardlink)

    with pytest.raises(ArchiveFormatError, match="source inventory is unsafe"):
        _write(run, tmp_path / "hardlink.sqrun")


def test_writer_fails_closed_when_source_ancestor_is_reparse_marked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run, _payload = _source_run(tmp_path)
    ancestor = run.parent
    original = __import__("sqvm.storage.inventory", fromlist=["_is_link_or_reparse"])._is_link_or_reparse

    def flagged(path: Path, info: os.stat_result) -> bool:
        return path == ancestor or original(path, info)

    monkeypatch.setattr("sqvm.storage.inventory._is_link_or_reparse", flagged)
    with pytest.raises(ArchiveFormatError, match="source inventory"):
        _write(run, tmp_path / "reparse.sqrun")


def test_writer_rejects_missing_or_invalid_workflow_identity(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    (run / "workflow.json").write_bytes(canonical_archive_json_bytes({"run_id": str(uuid.uuid4())}))

    with pytest.raises(ArchiveFormatError, match="workflow identity"):
        _write(run, tmp_path / "invalid.sqrun")


def test_writer_rejects_target_inside_source_and_dangling_link_parent(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    with pytest.raises(ArchiveFormatError, match="inside source"):
        _write(run, run / "forbidden.sqrun")

    dangling_parent = tmp_path / "dangling"
    os.symlink(tmp_path / "missing-target", dangling_parent, target_is_directory=True)
    with pytest.raises(ArchiveFormatError, match="archive target"):
        _write(run, dangling_parent / "forbidden.sqrun")


@pytest.mark.parametrize("mutation", ["add", "remove", "replace"])
def test_writer_rejects_source_tree_changed_by_callback(tmp_path: Path, mutation: str) -> None:
    run, _payload = _source_run(tmp_path)

    def changing_verifier(reader) -> None:
        reader.read_bytes("workflow.json")
        if mutation == "add":
            (run / "new-evidence.bin").write_bytes(b"new")
        elif mutation == "remove":
            (run / "dataset.bin").unlink()
        else:
            (run / "dataset.bin").write_bytes(b"replacement")

    with pytest.raises(ArchiveFormatError, match="source inventory changed"):
        _write(run, tmp_path / f"{mutation}.sqrun", verifier=changing_verifier)


def test_verifier_registry_keeps_unknown_structural_and_known_semantic_states_distinct(tmp_path: Path) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)

    assert verify_sqrun(archive).source_verified is False
    with pytest.raises(ArchiveFormatError, match="unknown or unavailable"):
        verify_sqrun(archive, require_source_verified=True)
    assert verify_sqrun(archive, verifier_registry={("unit", "0.1"): _valid_verifier}, require_source_verified=True).source_verified is True


@pytest.mark.parametrize("verifier", [
    lambda reader: (_ for _ in ()).throw(RuntimeError("verification failed")),
    lambda reader: reader.read_bytes("not-declared.json"),
    lambda reader: reader.read_bytes("dataset.bin", maximum_bytes=1),
])
def test_verifier_failures_and_reader_boundary_violations_fail_closed(tmp_path: Path, verifier) -> None:
    run, _payload = _source_run(tmp_path)
    archive = tmp_path / "source.sqrun"
    _write(run, archive)

    with pytest.raises(ArchiveFormatError):
        verify_sqrun(archive, verifier_registry={("unit", "0.1"): verifier}, require_source_verified=True)
