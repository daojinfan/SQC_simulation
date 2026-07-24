from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import json
import hashlib
import os
from pathlib import Path
from dataclasses import replace
import shutil
import sqlite3
import threading
import time
import uuid

import pytest

from sqvm.storage.archive_format import canonical_archive_json_bytes, write_sqrun
from sqvm.storage.catalog import (
    CatalogReference,
    CatalogReferenceGraph,
    CatalogRoots,
    CatalogLifecycleHead,
    rebuild_catalog,
    query_catalog,
    storage_summary,
)
from sqvm.storage.errors import StorageError
from sqvm.storage.lifecycle import append_event
from sqvm.storage.operations import ExperimentStorageOperations, StorageMutationRequest
from sqvm.calibration.spectroscopy_run import run_qubit_spectroscopy_scan
from sqvm.calibration.spectroscopy_reader import verify_qubit_spectroscopy_scan_evidence
import sqvm.calibration.spectroscopy as spectroscopy_module
from tests.support.contexts import spectroscopy_context as _context, spectroscopy_result as _result, single_spectroscopy_request as _single_request


ROOT = Path(__file__).resolve().parents[1]
PARENT_CONFIGURATION = ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json"


def _roots(root: Path) -> CatalogRoots:
    hot, archives, tombstones = (root / name for name in ("hot", "archives", "tombstones"))
    storage = root / "storage"
    for path in (hot, archives, tombstones, storage / "lifecycle"):
        path.mkdir(parents=True)
    return CatalogRoots(hot, archives, storage, tombstones)


def _run(root: Path, run_id: str | None = None) -> Path:
    run_id = run_id or str(uuid.uuid4())
    directory = root / f"qubit_spectroscopy_{run_id}"
    directory.mkdir()
    workflow = {"artifact_version": "0.2", "created_utc": "2026-07-22T00:00:00Z", "run_id": run_id, "workflow_id": "scan-v1"}
    (directory / "workflow.json").write_bytes(canonical_archive_json_bytes(workflow))
    (directory / "receipt.json").write_bytes(canonical_archive_json_bytes({"run_id": run_id, "status": "completed"}))
    (directory / "dataset.bin").write_bytes(b"catalog-evidence")
    return directory


def _archive(run: Path, destination: Path) -> None:
    write_sqrun(
        run,
        destination,
        source_verifier_id="catalog-test",
        source_verifier_version="1",
        source_verifier=lambda reader: reader.read_bytes("workflow.json"),
    )


def _catalog(root: Path) -> Path:
    return root / "cache" / "catalog.sqlite"


def _rebuild(catalog: Path, roots: CatalogRoots, **kwargs) -> int:
    return rebuild_catalog(
        catalog,
        roots,
        hot_verifier_registry={("scan-v1", "0.2"): lambda _: True},
        archive_verifier_registry={("catalog-test", "1"): lambda reader: reader.read_bytes("workflow.json")},
        **kwargs,
    )


def _install_v03_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(circuits, _context_value, output_root, _repository_root, **_kwargs):
        execution_root = Path(output_root)
        rows = []
        for circuit in circuits:
            evidence_root = execution_root / "circuits" / circuit.circuit_id
            evidence_root.mkdir(parents=True)
            (evidence_root / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
            rows.append(replace(
                _result(circuit.circuit_id, 0.8, 0.19, 0.0, 0.0),
                evidence_root=evidence_root,
                model_evidence_root=evidence_root,
            ))
        return tuple(rows)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


def _real_v03_carriers(tmp_path: Path, roots: CatalogRoots, monkeypatch: pytest.MonkeyPatch) -> str:
    """Create a production-format v0.3 hot tree and matching sqrun in temp only."""

    _install_v03_runner(monkeypatch)
    writer = ROOT / "tmp" / f"catalog_v03_{uuid.uuid4().hex}"
    try:
        run = run_qubit_spectroscopy_scan(
            replace(_single_request(), run_phase="scan"), _context(), PARENT_CONFIGURATION,
            writer / "scan", ROOT, timeout_s=10.0,
        )
        hot = roots.hot_root / f"qubit_spectroscopy_{run.run_id}"
        shutil.copytree(run.root, hot)
        write_sqrun(
            hot, roots.archive_root / f"{run.run_id}.sqrun",
            source_verifier_id="qubit_spectroscopy_scan_evidence",
            source_verifier_version="0.3",
            source_verifier=verify_qubit_spectroscopy_scan_evidence,
        )
        return run.run_id
    finally:
        shutil.rmtree(writer, ignore_errors=True)


def test_rebuild_mixed_hot_archive_and_duplicate_prefers_hot(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    hot = _run(roots.hot_root)
    _archive(hot, roots.archive_root / f"{hot.name.removeprefix('qubit_spectroscopy_')}.sqrun")

    revision = _rebuild(_catalog(tmp_path), roots)
    rows = query_catalog(_catalog(tmp_path))

    assert revision == 1
    assert len(rows) == 1
    assert rows[0].storage_state == "archived_duplicate"
    assert rows[0].read_preference == "hot"
    assert rows[0].allowed_actions == ("cleanup_duplicate", "keep")
    dto = rows[0].to_dict()
    assert set(dto) == {
        "schema_version", "run_id", "workflow_id", "workflow_sha256", "storage_state", "retention_state",
        "created_utc", "carrier", "logical_bytes", "allocated_bytes", "allocated_estimated", "reference_count",
        "references", "delete_after_utc", "allowed_actions", "blockers", "catalog_revision",
    }
    assert dto["catalog_revision"] == 1 and dto["carrier"]["archive_bytes"] > 0


def test_default_registry_indexes_real_spectroscopy_v03_hot_and_sqrun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _roots(tmp_path)
    run_id = _real_v03_carriers(tmp_path, roots, monkeypatch)
    catalog = _catalog(tmp_path)

    assert rebuild_catalog(catalog, roots) == 1
    row = query_catalog(catalog, run_id)[0]

    assert row.workflow_id == "qubit_spectroscopy_scan_v1"
    assert row.storage_state == "archived_duplicate"
    assert row.read_preference == "hot"
    assert row.carrier["archive_bytes"] > 0
    assert row.logical_bytes > 0 and row.allowed_actions == ("cleanup_duplicate", "keep")


def test_rebuild_recovers_missing_and_corrupt_catalog_with_migrations_and_wal(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)

    assert _rebuild(catalog, roots) == 1
    connection = sqlite3.connect(catalog)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1
    finally:
        connection.close()
    catalog.write_bytes(b"not sqlite")

    with pytest.raises(StorageError, match="existing catalog revision is unreadable"):
        _rebuild(catalog, roots)


def test_revision_ledger_and_existing_rebuild_lock_fail_closed(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    ledger = catalog.parent / ".catalog.sqlite.revision-ledger"
    assert ledger.read_text(encoding="ascii").strip() == "1"
    lock = catalog.parent / ".catalog.sqlite.rebuild.lock"
    lock.write_bytes(b"999:0123456789abcdef0123456789abcdef\n")
    with pytest.raises(StorageError, match="conflict"):
        _rebuild(catalog, roots)
    assert ledger.read_text(encoding="ascii").strip() == "1"


def test_ledger_link_hardlink_and_malformed_contents_fail_closed(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    ledger = catalog.parent / ".catalog.sqlite.revision-ledger"
    outside = tmp_path / "outside-ledger"
    outside.write_text("8\n", encoding="ascii")

    ledger.unlink()
    os.symlink(outside, ledger)
    with pytest.raises(StorageError, match="linked or unsafe"):
        _rebuild(catalog, roots)
    ledger.unlink()

    os.link(outside, ledger)
    with pytest.raises(StorageError, match="linked or unsafe"):
        _rebuild(catalog, roots)
    ledger.unlink()

    ledger.write_bytes(b"0002\n")
    with pytest.raises(StorageError, match="malformed"):
        _rebuild(catalog, roots)


def test_ledger_permission_error_fails_closed_without_revision_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module
    ledger = catalog.parent / ".catalog.sqlite.revision-ledger"
    original_open = Path.open

    def deny_ledger_open(path: Path, *args, **kwargs):
        if path == ledger:
            raise PermissionError("ledger access denied")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_ledger_open)
    with pytest.raises(StorageError, match="cannot be read"):
        _rebuild(catalog, roots)
    monkeypatch.undo()
    assert _rebuild(catalog, roots) == 2


def test_replaced_lock_is_not_unlinked_during_release(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    import sqvm.storage.catalog as catalog_module

    catalog_module._safe_catalog_path(catalog)
    lock = catalog_module._acquire_rebuild_lock(catalog)
    replacement = lock.path.with_name(f"{lock.path.name}.replacement")
    replacement.write_bytes(b"777:fedcba9876543210fedcba9876543210\n")
    os.replace(replacement, lock.path)

    with pytest.raises(StorageError, match="owner changed"):
        catalog_module._release_rebuild_lock(lock)
    assert lock.path.read_bytes() == b"777:fedcba9876543210fedcba9876543210\n"


def test_bad_archive_duplicate_conflict_and_unknown_reference_are_blocked(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    (roots.archive_root / f"{run_id}.sqrun").write_bytes(b"broken")
    references = (CatalogReference("unknown-run", "manual_keep", "pin-1", "pins/x.json", "A" * 64),)

    _rebuild(_catalog(tmp_path), roots, reference_adapter=lambda: CatalogReferenceGraph(references, False, ()))
    row = query_catalog(_catalog(tmp_path), run_id)[0]

    assert row.allowed_actions == ()
    assert row.reference_status == "unknown"
    assert any("archive" in blocker for blocker in row.blockers)


def test_lifecycle_reference_cross_checks_preserve_archive_but_block_trash(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    reference = CatalogReference(run_id, "manual_keep", "pin", "pins/x.json", "A" * 64, ("pins/x.json", "audit/y.json"))
    _rebuild(
        _catalog(tmp_path), roots,
        reference_adapter=lambda: CatalogReferenceGraph((reference,), False, ()),
        lifecycle_adapter=lambda: {run_id: CatalogLifecycleHead(run_id, 1, "B" * 64, "hot", manual_keep=True)},
    )
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.allowed_actions == ("archive", "keep")
    assert row.references[0].evidence_paths == ("pins/x.json", "audit/y.json")

    conflict_catalog = tmp_path / "conflict" / "catalog.sqlite"
    _rebuild(conflict_catalog, roots, lifecycle_adapter=lambda: {run_id: CatalogLifecycleHead(run_id, 1, "B" * 64, "archived", pending_event_type="archive_started")})
    assert query_catalog(conflict_catalog, run_id)[0].storage_state == "invalid"


def test_catalog_input_links_fail_closed(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    linked = tmp_path / "linked-hot"
    os.symlink(roots.hot_root, linked, target_is_directory=True)
    bad_roots = CatalogRoots(linked, roots.archive_root, roots.lifecycle_root, roots.tombstone_root)
    with pytest.raises(StorageError):
        _rebuild(_catalog(tmp_path), bad_roots)

    target_parent = tmp_path / "catalog-link"
    os.symlink(tmp_path / "elsewhere", target_parent, target_is_directory=True)
    with pytest.raises(StorageError):
        _rebuild(target_parent / "catalog.sqlite", roots)

def test_failed_replace_keeps_previous_catalog_and_removes_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    _rebuild(catalog, roots)
    original = catalog.read_bytes()
    import sqvm.storage.catalog as catalog_module

    monkeypatch.setattr(catalog_module.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace blocked")))
    with pytest.raises(StorageError):
        _rebuild(catalog, roots)
    assert catalog.read_bytes() == original
    assert not list(catalog.parent.glob(".catalog.sqlite.rebuild.*"))


def test_checkpoint_failure_keeps_previous_catalog_and_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module

    monkeypatch.setattr(catalog_module, "_checkpoint_database", lambda _connection: (_ for _ in ()).throw(OSError("checkpoint blocked")))
    with pytest.raises(StorageError, match="rebuild failed"):
        _rebuild(catalog, roots)

    assert query_catalog(catalog)[0].catalog_revision == 1
    assert (catalog.parent / ".catalog.sqlite.revision-ledger").read_text(encoding="ascii").strip() == "1"


def test_temp_fsync_and_ledger_write_failures_do_not_publish_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module

    monkeypatch.setattr(catalog_module, "_fsync_file", lambda _path: (_ for _ in ()).throw(OSError("temp fsync blocked")))
    with pytest.raises(StorageError, match="rebuild failed"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 1

    monkeypatch.undo()
    monkeypatch.setattr(catalog_module, "_write_ledger_revision", lambda *_: (_ for _ in ()).throw(StorageError("ledger blocked")))
    with pytest.raises(StorageError, match="ledger blocked"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 1
    assert (catalog.parent / ".catalog.sqlite.revision-ledger").read_text(encoding="ascii").strip() == "1"


def test_ledger_replace_failure_keeps_previous_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module
    original_replace = catalog_module.os.replace

    def fail_ledger_replace(source, target):
        if Path(target).name.endswith(".revision-ledger"):
            raise OSError("ledger replace blocked")
        return original_replace(source, target)

    monkeypatch.setattr(catalog_module.os, "replace", fail_ledger_replace)
    with pytest.raises(StorageError, match="ledger write failed"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 1


def test_ledger_fsync_failure_keeps_previous_catalog_and_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module
    original_fsync = catalog_module.os.fsync
    calls = 0

    def fail_ledger_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        # lock, temporary SQLite, then the revision-ledger temporary file
        if calls == 3:
            raise OSError("ledger fsync blocked")
        original_fsync(descriptor)

    monkeypatch.setattr(catalog_module.os, "fsync", fail_ledger_fsync)
    with pytest.raises(StorageError, match="ledger write failed"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 1
    assert (catalog.parent / ".catalog.sqlite.revision-ledger").read_text(encoding="ascii").strip() == "1"


def test_catalog_replace_failure_reserves_revision_without_replacing_old_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module

    monkeypatch.setattr(catalog_module, "_atomic_replace", lambda *_: (_ for _ in ()).throw(OSError("catalog replace blocked")))
    with pytest.raises(StorageError, match="rebuild failed"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 1
    assert (catalog.parent / ".catalog.sqlite.revision-ledger").read_text(encoding="ascii").strip() == "2"

    monkeypatch.undo()
    assert _rebuild(catalog, roots) == 3
    assert query_catalog(catalog)[0].catalog_revision == 3


def test_final_directory_fsync_failure_never_reuses_published_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module
    original_fsync_directory = catalog_module._fsync_directory
    calls = 0

    def fail_after_ledger(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("catalog directory fsync blocked")
        original_fsync_directory(path)

    monkeypatch.setattr(catalog_module, "_fsync_directory", fail_after_ledger)
    with pytest.raises(StorageError, match="rebuild failed"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 2
    monkeypatch.undo()
    assert _rebuild(catalog, roots) == 3
    assert query_catalog(catalog)[0].catalog_revision == 3


def test_two_live_rebuilds_allow_one_winner_and_preserve_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    assert _rebuild(catalog, roots) == 1
    import sqvm.storage.catalog as catalog_module
    entered = threading.Event()
    release = threading.Event()
    original_checkpoint = catalog_module._checkpoint_database
    result: list[int] = []
    failures: list[BaseException] = []

    def hold_checkpoint(connection: sqlite3.Connection) -> None:
        entered.set()
        assert release.wait(5.0)
        original_checkpoint(connection)

    monkeypatch.setattr(catalog_module, "_checkpoint_database", hold_checkpoint)

    def first_rebuild() -> None:
        try:
            result.append(_rebuild(catalog, roots))
        except BaseException as exc:  # surfaced below from the worker boundary
            failures.append(exc)

    worker = threading.Thread(target=first_rebuild)
    worker.start()
    assert entered.wait(5.0)
    with pytest.raises(StorageError, match="conflict"):
        _rebuild(catalog, roots)
    assert query_catalog(catalog)[0].catalog_revision == 1
    release.set()
    worker.join(timeout=10.0)

    assert not failures and result == [2]
    assert query_catalog(catalog)[0].catalog_revision == 2


def test_concurrent_reader_large_rebuild_and_storage_summary_is_read_only(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    for _ in range(500):
        _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    _rebuild(catalog, roots)
    stop = threading.Event()
    reader_errors: list[Exception] = []

    def read_catalog() -> None:
        while not stop.is_set():
            try:
                query_catalog(catalog)
            except Exception as exc:  # no read failure is acceptable during replacement
                reader_errors.append(exc)
                return
            time.sleep(0.001)

    thread = threading.Thread(target=read_catalog)
    thread.start()
    try:
        assert _rebuild(catalog, roots) == 2
    finally:
        stop.set()
        thread.join()
    assert not reader_errors
    assert len(query_catalog(catalog)) == 500
    summary = storage_summary(catalog, volume_root=tmp_path)
    assert summary.logical_bytes > 0 and summary.allocated_bytes >= summary.logical_bytes
    assert summary.volume_total_bytes >= summary.volume_free_bytes >= 0
    assert not (roots.hot_root / "catalog.sqlite").exists()


def test_lifecycle_and_tombstone_scan_do_not_store_raw_arrays(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    tombstone = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_tombstone", "artifact_version": "0.1",
        "run_id": str(uuid.uuid4()), "workflow_id": "scan-v1", "workflow_sha256": "C" * 64,
        "receipt_sha256": "D" * 64, "last_bundle_sha256": "E" * 64, "original_created_utc": "2026-07-22T00:00:00Z", "purged_utc": "2026-07-22T00:00:01Z",
        "actor_id": "test", "reason": "test", "last_lifecycle_event_sha256": "F" * 64,
    }
    tombstone["tombstone_sha256"] = hashlib.sha256(canonical_archive_json_bytes(tombstone)).hexdigest().upper()
    (roots.tombstone_root / f"{tombstone['run_id']}.json").write_bytes(canonical_archive_json_bytes(tombstone))
    _rebuild(_catalog(tmp_path), roots, lifecycle_adapter=lambda: {run_id: CatalogLifecycleHead(run_id, 3, "B" * 64, "hot")})
    with sqlite3.connect(_catalog(tmp_path)) as connection:
        assert connection.execute("SELECT sequence FROM lifecycle_heads WHERE run_id=?", (run_id,)).fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0] == 1
        assert not any("array" in row[1].lower() for row in connection.execute("PRAGMA table_info(runs)"))


def test_optional_trash_root_is_indexed_fail_closed_without_lifecycle_mutation(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    run_id = str(uuid.uuid4())
    trash = trash_root / run_id
    (trash / "payload").mkdir(parents=True)
    payload = trash / "payload"
    workflow = {"artifact_version": "0.2", "created_utc": "2026-07-22T00:00:00Z", "run_id": run_id, "workflow_id": "scan-v1"}
    (payload / "workflow.json").write_bytes(canonical_archive_json_bytes(workflow))
    (payload / "receipt.json").write_bytes(canonical_archive_json_bytes({"run_id": run_id, "status": "completed"}))
    (payload / "evidence.bin").write_bytes(b"trashed")
    entries = []
    for path in sorted(payload.iterdir(), key=lambda item: item.name):
        raw = path.read_bytes()
        entries.append({"path": path.name, "byte_length": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest().upper()})
    from sqvm.storage.operations import _carrier_sha
    payload_sha = _carrier_sha(payload, "hot_directory")
    record = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_trash_record", "artifact_version": "0.1",
        "run_id": run_id, "workflow_id": "scan-v1", "workflow_sha256": hashlib.sha256((payload / "workflow.json").read_bytes()).hexdigest().upper(),
        "receipt_sha256": hashlib.sha256((payload / "receipt.json").read_bytes()).hexdigest().upper(), "operation_id": str(uuid.uuid4()),
        "actor_id": "catalog-test", "reason": "test", "previous_storage_state": "hot", "original_carrier_kind": "hot_directory",
        "original_carrier_alias": f"qubit_spectroscopy_{run_id}", "original_carrier_sha256": payload_sha, "payload_kind": "hot_directory", "payload_sha256": payload_sha,
        "payload_logical_bytes": sum(item["byte_length"] for item in entries), "trashed_utc": "2026-07-22T00:00:00Z", "purge_after_utc": "2026-07-23T00:00:00Z", "trash_started_event_sha256": "A" * 64,
    }
    record["record_sha256"] = hashlib.sha256((json.dumps({key: value for key, value in record.items() if key != "record_sha256"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest().upper()
    (trash / "trash-record.json").write_bytes((json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
    roots = CatalogRoots(roots.hot_root, roots.archive_root, roots.lifecycle_root, roots.tombstone_root, trash_root)

    common = {
        "workflow_sha256": record["workflow_sha256"], "carrier_path": record["original_carrier_alias"],
        "carrier_sha256": record["payload_sha256"], "catalog_revision": 0,
    }
    first = append_event(roots.lifecycle_root, run_id, "hot_discovered", {**common, "from_state": None, "to_state": "hot"}, actor_id="catalog.test", operation_id=str(uuid.uuid4()), expected_revision=0, expected_tail_sha256=None)
    operation = str(uuid.uuid4())
    second = append_event(roots.lifecycle_root, run_id, "trash_started", {**common, "from_state": "hot", "to_state": "hot"}, actor_id="catalog.test", operation_id=operation, expected_revision=first.revision, expected_tail_sha256=first.tail_sha256)
    append_event(roots.lifecycle_root, run_id, "trashed", {**common, "from_state": "hot", "to_state": "trash", "trash_previous_state": "hot"}, actor_id="catalog.test", operation_id=operation, expected_revision=second.revision, expected_tail_sha256=second.tail_sha256)

    _rebuild(_catalog(tmp_path), roots)
    row = query_catalog(_catalog(tmp_path), run_id)[0]

    assert row.storage_state == "trash", row.blockers
    assert row.read_preference == "trash"
    assert row.allowed_actions == ("restore", "keep")


@pytest.mark.parametrize("state,expected", [
    ("hot", ("archive", "trash", "keep")),
    ("archived", ("restore_hot", "trash", "keep")),
    ("archived_duplicate", ("cleanup_duplicate", "keep")),
    ("trash", ("restore", "keep")),
])
@pytest.mark.parametrize("manual_keep,reference_status,retention", [
    (False, "unreferenced", "normal"), (True, "unreferenced", "manual_keep"),
    (False, "protected", "referenced"), (True, "protected", "manual_keep"),
    (True, "unknown", "reference_unknown"),
])
def test_public_retention_and_keep_action_matrix(state, expected, manual_keep, reference_status, retention) -> None:
    import sqvm.storage.catalog as catalog_module
    row = (
        str(uuid.uuid4()), "scan", "A" * 64, "B" * 64, state, None, None, None, state,
        1, 1, 0, 0, "", "2026-07-22T00:00:00Z", int(manual_keep), reference_status, None,
    )
    references = (CatalogReference(row[0], "manual_keep", "x", "x", "C" * 64),) if reference_status == "protected" else ()
    result = catalog_module._catalog_run(row, references, 1)
    assert result.to_dict()["retention_state"] == retention
    assert result.manual_keep is manual_keep
    assert "keep" in result.allowed_actions
    if reference_status in {"protected", "unknown"} or manual_keep:
        assert "trash" not in result.allowed_actions
    else:
        assert result.allowed_actions == expected


def test_query_catalog_is_pure_read_and_rejects_invalid_run_id(tmp_path: Path) -> None:
    catalog = tmp_path / "absent" / "nested" / "catalog.sqlite"
    before = tuple(tmp_path.rglob("*"))
    with pytest.raises(StorageError, match="missing"):
        query_catalog(catalog)
    with pytest.raises(StorageError, match="run_id"):
        query_catalog(catalog, "not-a-uuid")
    assert tuple(tmp_path.rglob("*")) == before


def test_rebuild_preserves_primary_failure_when_lock_release_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    _rebuild(catalog, roots)
    import sqvm.storage.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "_atomic_replace", lambda *_: (_ for _ in ()).throw(OSError("replace failure")))
    monkeypatch.setattr(catalog_module, "_release_rebuild_lock", lambda _lock: (_ for _ in ()).throw(StorageError("lock owner changed")))
    with pytest.raises(StorageError, match="catalog rebuild failed") as raised:
        _rebuild(catalog, roots)
    assert isinstance(raised.value.__cause__, OSError)


def test_successful_rebuild_propagates_lock_release_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    import sqvm.storage.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "_release_rebuild_lock", lambda _lock: (_ for _ in ()).throw(StorageError("lock owner changed")))
    with pytest.raises(StorageError, match="lock owner changed"):
        _rebuild(_catalog(tmp_path), roots)


def test_legacy_random_alias_is_quarantined_without_polluting_v03(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    valid_id = _real_v03_carriers(tmp_path, roots, monkeypatch)
    legacy_id = str(uuid.uuid4())
    legacy = roots.hot_root / uuid.uuid4().hex
    legacy.mkdir()
    # Deliberately pretty/noncanonical: it is identity-only, not admission evidence.
    (legacy / "workflow.json").write_text(json.dumps({"run_id": legacy_id, "workflow_id": "scan-v1", "artifact_version": "0.2"}, indent=2), encoding="utf-8")
    (legacy / "receipt.json").write_text("{}", encoding="utf-8")
    (legacy / "data.bin").write_bytes(b"legacy")
    rebuild_catalog(_catalog(tmp_path), roots)
    rows = {row.run_id: row for row in query_catalog(_catalog(tmp_path))}
    assert rows[legacy_id].storage_state == "invalid"
    assert "legacy_archive_ineligible" in rows[legacy_id].blockers
    assert rows[legacy_id].allowed_actions == ()
    assert rows[valid_id].storage_state == "archived_duplicate"
    assert not rows[valid_id].blockers


def test_real_operations_random_hot_alias_trash_is_catalog_restorable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    run_id = _real_v03_carriers(tmp_path, roots, monkeypatch)
    # Operations locates authority by workflow.run_id, not by directory name.
    original = roots.hot_root / f"qubit_spectroscopy_{run_id}"
    alias = roots.hot_root / f"qubit_spectroscopy_{uuid.uuid4().hex}"
    original.rename(alias)
    storage = roots.lifecycle_root
    from tests.support.fixture_loader import copy_fixture
    config = copy_fixture("platform_configuration_reference_v1", tmp_path / "fixture") / "platform-configurations"
    index = tmp_path / "experiment-index"; index.mkdir()
    request = StorageMutationRequest("catalog.test", 7, hashlib.sha256((alias / "workflow.json").read_bytes()).hexdigest().upper(), "retention")
    operations = ExperimentStorageOperations(hot_root=roots.hot_root, storage_root=storage, configuration_root=config, experiment_output_root=index, catalog_revision=7)
    assert operations.trash(run_id, request).state == "trash"
    import sqvm.storage.catalog as catalog_module
    record = json.loads((storage / "trash" / run_id / "trash-record.json").read_text(encoding="utf-8"))
    payload = storage / "trash" / run_id / "payload"
    assert record["payload_sha256"] == catalog_module._tree_sha(payload, None)
    assert record["workflow_sha256"] == hashlib.sha256((payload / "workflow.json").read_bytes()).hexdigest().upper()
    assert record["receipt_sha256"] == hashlib.sha256((payload / "receipt.json").read_bytes()).hexdigest().upper()
    catalog_roots = CatalogRoots(roots.hot_root, storage / "archives", storage, roots.tombstone_root, storage / "trash")
    rebuild_catalog(_catalog(tmp_path), catalog_roots)
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.storage_state == "trash", row.blockers
    assert row.allowed_actions == ("restore", "keep")


def test_created_utc_roundtrip_and_conflict_is_invalid(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run_id = str(uuid.uuid4())
    first = _run(roots.hot_root, run_id)
    second = roots.hot_root / f"qubit_spectroscopy_{uuid.uuid4()}"
    shutil.copytree(first, second)
    workflow = json.loads((second / "workflow.json").read_text(encoding="utf-8"))
    workflow["created_utc"] = "2026-07-23T00:00:00Z"
    (second / "workflow.json").write_bytes(canonical_archive_json_bytes(workflow))
    _rebuild(_catalog(tmp_path), roots)
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.created_utc in {"2026-07-22T00:00:00Z", "2026-07-23T00:00:00Z"}
    assert row.storage_state == "invalid"
    assert "created_utc_carrier_conflict" in row.blockers


def _strict_trash_record() -> dict[str, object]:
    import sqvm.storage.catalog as catalog_module
    run_id = str(uuid.uuid4())
    value: dict[str, object] = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_trash_record", "artifact_version": "0.1",
        "run_id": run_id, "workflow_id": "workflow", "workflow_sha256": "A" * 64, "receipt_sha256": "B" * 64,
        "operation_id": str(uuid.uuid4()), "actor_id": "catalog.test", "reason": "retention",
        "previous_storage_state": "hot", "original_carrier_kind": "hot_directory",
        "original_carrier_alias": f"qubit_spectroscopy_{run_id}", "original_carrier_sha256": "C" * 64,
        "payload_kind": "hot_directory", "payload_sha256": "C" * 64, "payload_logical_bytes": 0,
        "trashed_utc": "2026-07-22T00:00:00Z", "purge_after_utc": "2026-07-23T00:00:00Z",
        "trash_started_event_sha256": "D" * 64, "record_sha256": "",
    }
    value["record_sha256"] = catalog_module._hash_trash_document(value)
    return value


@pytest.mark.parametrize("mutate", [
    lambda value: value.pop("reason"), lambda value: value.__setitem__("reason", None),
    lambda value: value.__setitem__("payload_logical_bytes", True), lambda value: value.__setitem__("extra", "x"),
    lambda value: value.__setitem__("trashed_utc", "2026-07-22T00:00:00+00:00"),
    lambda value: value.__setitem__("run_id", str(uuid.uuid4()).upper()),
    lambda value: value.__setitem__("payload_sha256", "a" * 64),
])
def test_strict_trash_schema_rejects_field_type_utc_uuid_hash_variants(mutate) -> None:
    import sqvm.storage.catalog as catalog_module
    value = _strict_trash_record()
    mutate(value)
    if "record_sha256" in value:
        value["record_sha256"] = catalog_module._hash_trash_document(value)
    with pytest.raises(ValueError):
        catalog_module._validate_trash_record(value)


def test_strict_trash_and_tombstone_canonical_records_are_accepted() -> None:
    """Exercise every frozen-field check after an authenticated record parses."""
    import sqvm.storage.catalog as catalog_module
    trash = _strict_trash_record()
    assert catalog_module._validate_trash_record(trash) == trash
    tombstone = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_tombstone", "artifact_version": "0.1",
        "run_id": str(uuid.uuid4()), "workflow_id": "workflow", "workflow_sha256": "A" * 64,
        "receipt_sha256": "B" * 64, "last_bundle_sha256": "C" * 64,
        "original_created_utc": "2026-07-22T00:00:00Z", "purged_utc": "2026-07-23T00:00:00Z",
        "actor_id": "catalog.test", "reason": "retention", "last_lifecycle_event_sha256": "D" * 64,
        "tombstone_sha256": "",
    }
    tombstone["tombstone_sha256"] = catalog_module._hash_document(tombstone, "tombstone_sha256")
    assert catalog_module._validate_tombstone(tombstone) == tombstone


def test_tombstone_symlink_and_hardlink_are_rejected(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run_id = str(uuid.uuid4())
    tombstone = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_tombstone", "artifact_version": "0.1", "run_id": run_id,
        "workflow_id": "workflow", "workflow_sha256": "A" * 64, "receipt_sha256": "B" * 64, "last_bundle_sha256": "C" * 64,
        "original_created_utc": "2026-07-22T00:00:00Z", "purged_utc": "2026-07-23T00:00:00Z", "actor_id": "catalog.test", "reason": "retention",
        "last_lifecycle_event_sha256": "D" * 64, "tombstone_sha256": "",
    }
    tombstone["tombstone_sha256"] = hashlib.sha256(canonical_archive_json_bytes({key: value for key, value in tombstone.items() if key != "tombstone_sha256"})).hexdigest().upper()
    source = tmp_path / "tombstone-source.json"; source.write_bytes(canonical_archive_json_bytes(tombstone))
    target = roots.tombstone_root / f"{run_id}.json"
    os.link(source, target)
    with pytest.raises(StorageError, match="invalid"):
        _rebuild(_catalog(tmp_path), roots)
    target.unlink()
    os.symlink(source, target)
    with pytest.raises(StorageError):
        _rebuild(_catalog(tmp_path), roots)


def test_public_rebuild_adapter_failure_and_unknown_carriers_fail_closed(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    (roots.hot_root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    (roots.archive_root / "unexpected.bin").write_bytes(b"unexpected")
    _rebuild(_catalog(tmp_path), roots, reference_adapter=lambda: (_ for _ in ()).throw(RuntimeError("scanner down")))
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.storage_state == "invalid"
    assert row.reference_status == "unknown"
    assert {"unknown_hot_root_entry", "unknown_archive_root_entry"} <= set(row.blockers)


def test_public_rebuild_lock_conflict_and_malformed_lock_payload(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    lock = catalog.parent / ".catalog.sqlite.rebuild.lock"
    lock.parent.mkdir()
    lock.write_bytes(b"123:not-a-token\n")
    with pytest.raises(StorageError, match="malformed"):
        _rebuild(catalog, roots)
    lock.unlink()
    lock.write_bytes(f"{os.getpid()}:0123456789abcdef0123456789abcdef\n".encode("ascii"))
    with pytest.raises(StorageError, match="conflict"):
        _rebuild(catalog, roots)


def test_public_query_and_summary_reject_corrupt_database_and_reference_json(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    catalog = _catalog(tmp_path)
    _rebuild(catalog, roots)
    with sqlite3.connect(catalog) as connection:
        connection.execute("INSERT INTO 'references' VALUES (?,?,?,?,?,?,?)", (run_id, "manual_keep", "pin", "pin.json", "A" * 64, "not-json", 1))
    with pytest.raises(StorageError, match="unreadable"):
        query_catalog(catalog)
    catalog.write_bytes(b"not sqlite")
    with pytest.raises(StorageError, match="unreadable"):
        storage_summary(catalog, volume_root=tmp_path)


def test_public_tombstone_conflict_marks_discovered_carrier_invalid(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    tombstone = {
        "schema_version": "0.1", "artifact_type": "sqvm_experiment_tombstone", "artifact_version": "0.1", "run_id": run_id,
        "workflow_id": "scan-v1", "workflow_sha256": "A" * 64, "receipt_sha256": "B" * 64, "last_bundle_sha256": "C" * 64,
        "original_created_utc": "2026-07-22T00:00:00Z", "purged_utc": "2026-07-23T00:00:00Z", "actor_id": "catalog.test", "reason": "retention",
        "last_lifecycle_event_sha256": "D" * 64, "tombstone_sha256": "",
    }
    import sqvm.storage.catalog as catalog_module
    tombstone["tombstone_sha256"] = catalog_module._hash_document(tombstone, "tombstone_sha256")
    (roots.tombstone_root / f"{run_id}.json").write_bytes(canonical_archive_json_bytes(tombstone))
    _rebuild(_catalog(tmp_path), roots)
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.storage_state == "invalid"
    assert "tombstone_carrier_conflict" in row.blockers


@pytest.mark.parametrize("carrier", ["hot", "archive"])
def test_real_hardlinked_hot_and_archive_carriers_are_quarantined(tmp_path: Path, carrier: str) -> None:
    roots = _roots(tmp_path)
    run = _run(roots.hot_root)
    run_id = run.name.removeprefix("qubit_spectroscopy_")
    if carrier == "hot":
        os.link(run / "dataset.bin", run / "duplicate.bin")
    else:
        archive = roots.archive_root / f"{run_id}.sqrun"
        _archive(run, archive)
        os.link(archive, tmp_path / "archive-alias.sqrun")
        with pytest.raises(StorageError, match="hard-linked"):
            _rebuild(_catalog(tmp_path), roots)
        return
    _rebuild(_catalog(tmp_path), roots)
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.storage_state == "invalid"
    assert any("carrier_invalid" in blocker for blocker in row.blockers)


def test_real_hardlinked_trash_record_is_quarantined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roots = _roots(tmp_path)
    run_id = _real_v03_carriers(tmp_path, roots, monkeypatch)
    hot = roots.hot_root / f"qubit_spectroscopy_{run_id}"
    storage = roots.lifecycle_root
    from tests.support.fixture_loader import copy_fixture
    config = copy_fixture("platform_configuration_reference_v1", tmp_path / "fixture") / "platform-configurations"
    index = tmp_path / "experiment-index"; index.mkdir()
    request = StorageMutationRequest("catalog.test", 7, hashlib.sha256((hot / "workflow.json").read_bytes()).hexdigest().upper(), "retention")
    operations = ExperimentStorageOperations(hot_root=roots.hot_root, storage_root=storage, configuration_root=config, experiment_output_root=index, catalog_revision=7)
    operations.trash(run_id, request)
    record = storage / "trash" / run_id / "trash-record.json"
    os.link(record, tmp_path / "trash-record-alias.json")
    catalog_roots = CatalogRoots(roots.hot_root, storage / "archives", storage, roots.tombstone_root, storage / "trash")
    rebuild_catalog(_catalog(tmp_path), catalog_roots)
    row = query_catalog(_catalog(tmp_path), run_id)[0]
    assert row.storage_state == "invalid"
    assert "trash_carrier_invalid" in row.blockers


def test_public_query_rejects_directory_symlink_and_hardlinked_catalog_targets(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    catalog.parent.mkdir()
    catalog.mkdir()
    with pytest.raises(StorageError):
        query_catalog(catalog)
    catalog.rmdir()
    outside = tmp_path / "outside.sqlite"; outside.write_bytes(b"not sqlite")
    os.symlink(outside, catalog)
    with pytest.raises(StorageError):
        storage_summary(catalog, volume_root=tmp_path)
    catalog.unlink()
    _rebuild(catalog, roots)
    os.link(catalog, tmp_path / "catalog-alias.sqlite")
    with pytest.raises(StorageError):
        query_catalog(catalog)


def test_public_query_rejects_malformed_catalog_meta_and_active_lock_is_never_stolen(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    _run(roots.hot_root)
    catalog = _catalog(tmp_path)
    _rebuild(catalog, roots)
    lock = catalog.parent / ".catalog.sqlite.rebuild.lock"
    lock.write_bytes(f"{os.getpid()}:0123456789abcdef0123456789abcdef\n".encode("ascii"))
    with pytest.raises(StorageError, match="conflict"):
        _rebuild(catalog, roots)
    lock.unlink()
    with sqlite3.connect(catalog) as connection:
        connection.execute("UPDATE catalog_meta SET value='zero' WHERE key='revision'")
    with pytest.raises(StorageError, match="unreadable"):
        query_catalog(catalog)
