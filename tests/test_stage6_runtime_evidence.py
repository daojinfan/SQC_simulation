import json
from pathlib import Path

import pytest

import sqvm.runtime.storage as runtime_storage
from sqvm.runtime.journal import EventJournal, canonical_json_line_bytes, verify_event_journal
from sqvm.runtime.dataset import decode_numeric_values, encode_numeric_values
from sqvm.runtime.storage import atomic_publish, inventory_tree_no_follow


RUN_ID = "123e4567-e89b-42d3-a456-426614174000"
SHA_A = "A" * 64
SHA_B = "B" * 64
SHA_C = "C" * 64
SHA_D = "D" * 64


def test_event_journal_is_canonical_and_hash_chained(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path, RUN_ID)
    first = journal.append(
        "run_reserved",
        {
            "request_sha256": SHA_A,
            "point_table_sha256": SHA_B,
            "run_lock_sha256": SHA_C,
            "resource_lock_sha256": SHA_D,
        },
        utc_time="2026-07-14T00:00:00.000001Z",
        monotonic_ns=10,
    )
    second = journal.append(
        "run_prepared",
        {
            "experiment_id": "platform_deterministic_smoke_v1",
            "backend_id": "deterministic_fake_v1",
            "backend_capabilities_sha256": SHA_A,
        },
        utc_time="2026-07-14T00:00:00.000002Z",
        monotonic_ns=20,
    )
    summary = journal.close()

    assert first["prev_event_sha256"] is None
    assert second["prev_event_sha256"] == first["event_sha256"]
    assert summary.event_count == 2
    assert summary.tail_event_sha256 == second["event_sha256"]
    assert verify_event_journal(path, RUN_ID) == summary
    for line in path.read_bytes().splitlines(keepends=True):
        assert line == canonical_json_line_bytes(json.loads(line))


def test_event_journal_rejects_payload_and_tampering(tmp_path):
    path = tmp_path / "events.jsonl"
    journal = EventJournal(path, RUN_ID)
    with pytest.raises(ValueError, match="payload keys"):
        journal.append("run_started", {"wrong": 1})
    journal.append(
        "run_started",
        {"point_count": 1},
        utc_time="2026-07-14T00:00:00.000001Z",
        monotonic_ns=1,
    )
    journal.close()

    raw = path.read_bytes().replace(b'"point_count":1', b'"point_count":2')
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="event_sha256"):
        verify_event_journal(path, RUN_ID)


def test_event_journal_requires_new_file_and_bounded_message(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_bytes(b"occupied")
    with pytest.raises(FileExistsError):
        EventJournal(path, RUN_ID)

    clean = tmp_path / "clean.jsonl"
    journal = EventJournal(clean, RUN_ID)
    with pytest.raises(ValueError, match="message"):
        journal.append(
            "run_failed",
            {"error_class": "backend_failure", "message": "line one\nline two", "completed_point_count": 0},
        )


@pytest.mark.parametrize(
    ("dtype", "values"),
    [
        ("<f8", (1.5, -2.0)),
        ("<i8", (-2, 3)),
        ("<u8", (0, 2**64 - 1)),
        ("<c16", (1 + 2j, -3 + 0.5j)),
    ],
)
def test_portable_numeric_dtype_roundtrip(dtype, values):
    raw = encode_numeric_values(values, dtype)
    assert decode_numeric_values(raw, dtype) == values


def test_numeric_dtype_rejects_nonfinite_and_native_endian():
    with pytest.raises(ValueError, match="finite"):
        encode_numeric_values([float("nan")], "<f8")
    with pytest.raises(ValueError, match="unsupported"):
        encode_numeric_values([1.0], "f8")


def test_malformed_tree_inventory_does_not_follow_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    tree = tmp_path / "tree"
    tree.mkdir()
    link = tree / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    rows = inventory_tree_no_follow(tree)
    assert len(rows) == 1 and rows[0]["path"] == "linked.txt" and rows[0]["entry_type"] == "link"
    assert rows[0]["link_target"].endswith("outside.txt")
    assert "raw_sha256" not in rows[0]


def test_atomic_publish_rejects_destination_created_after_precheck(tmp_path, monkeypatch):
    staging = tmp_path / "staging" / "run-id"
    target = tmp_path / "runs" / "run-id"
    staging.mkdir(parents=True)
    target.parent.mkdir()
    (staging / "payload.bin").write_bytes(b"payload")
    original = runtime_storage._rename_directory_no_replace

    def race(source, destination):
        destination.mkdir()
        original(source, destination)

    monkeypatch.setattr(runtime_storage, "_rename_directory_no_replace", race)
    with pytest.raises(FileExistsError):
        atomic_publish(staging, target)
    assert staging.is_dir() and (staging / "payload.bin").read_bytes() == b"payload"
    assert target.is_dir() and not list(target.iterdir())
