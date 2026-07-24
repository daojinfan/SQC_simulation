from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import multiprocessing
import hashlib
import subprocess
import sys
from pathlib import Path
import uuid

import pytest

import sqvm.storage.lifecycle as lifecycle
from sqvm.storage.lifecycle import LifecycleConflict, LifecycleError, append_event, read_head
from sqvm.hamiltonian.provenance import canonical_json_bytes


RUN = "11111111-1111-4111-8111-111111111111"
WORKFLOW = "A" * 64
CARRIER = "B" * 64


def _payload(before, after, **extra):
    return {"from_state": before, "to_state": after, "workflow_sha256": WORKFLOW, "carrier_path": "hot/run", "carrier_sha256": CARRIER, "catalog_revision": 3, **extra}


def _append(root, event, payload, run=RUN, operation_id=None):
    head = read_head(root, run)
    return append_event(root, run, event, payload, actor_id="test.actor", operation_id=operation_id or str(uuid.uuid4()), expected_revision=head.revision, expected_tail_sha256=head.tail_sha256)


def _hot(root): return _append(root, "hot_discovered", _payload(None, "hot"))


def test_complete_archive_trash_restore_state_machine(tmp_path):
    archive, cleanup, trash = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    _hot(tmp_path); _append(tmp_path, "archive_started", _payload("hot", "archiving"), operation_id=archive); _append(tmp_path, "archive_verified", _payload("archiving", "archived_duplicate"), operation_id=archive); _append(tmp_path, "hot_cleanup_started", _payload("archived_duplicate", "archived_duplicate"), operation_id=cleanup); _append(tmp_path, "hot_cleanup_completed", _payload("archived_duplicate", "archived"), operation_id=cleanup); _append(tmp_path, "trash_started", _payload("archived", "archived"), operation_id=trash); _append(tmp_path, "trashed", _payload("archived", "trash", trash_previous_state="archived"), operation_id=trash); head = _append(tmp_path, "trash_restored", _payload("trash", "archived", restored_state="archived"))
    assert head.state == "archived" and head.trash_previous_state is None


def test_manual_keep_blocks_purge(tmp_path):
    trash = str(uuid.uuid4())
    _hot(tmp_path); _append(tmp_path, "keep_changed", _payload("hot", "hot", manual_keep=True)); _append(tmp_path, "trash_started", _payload("hot", "hot"), operation_id=trash); _append(tmp_path, "trashed", _payload("hot", "trash", trash_previous_state="hot"), operation_id=trash)
    with pytest.raises(LifecycleError, match="manual_keep"):
        _append(tmp_path, "purge_started", _payload("trash", "purging"))


def test_running_and_recovery_have_explicit_entries(tmp_path):
    head = _append(tmp_path, "staging_recovery_required", _payload(None, "recovery_required"))
    assert head.state == "recovery_required"
    assert _append(tmp_path, "hot_discovered", _payload(None, "hot"), run="22222222-2222-4222-8222-222222222222").state == "hot"


def test_optimistic_conflict_preserves_single_tail(tmp_path):
    head = _hot(tmp_path)
    _append(tmp_path, "keep_changed", _payload("hot", "hot", manual_keep=True))
    with pytest.raises(LifecycleConflict):
        append_event(tmp_path, RUN, "keep_changed", _payload("hot", "hot", manual_keep=False), actor_id="test.actor", operation_id=str(uuid.uuid4()), expected_revision=head.revision, expected_tail_sha256=head.tail_sha256)


def test_illegal_transition_and_payload_shape_rejected(tmp_path):
    with pytest.raises(LifecycleError): _append(tmp_path, "archive_started", _payload(None, "archiving"))
    _hot(tmp_path)
    bad = _payload("hot", "hot", manual_keep=True); bad["extra"] = 1
    with pytest.raises(LifecycleError): _append(tmp_path, "keep_changed", bad)


@pytest.mark.parametrize("event,payload", [
    ("unknown_event", _payload(None, "hot")),
    ("hot_discovered", {**_payload(None, "hot"), "operation_id": "not-a-uuid"}),
    ("hot_discovered", {**_payload(None, "hot"), "carrier_sha256": "bad"}),
    ("hot_discovered", {**_payload(None, "hot"), "catalog_revision": True}),
])
def test_public_append_rejects_unknown_and_invalid_event_fields(tmp_path, event, payload):
    with pytest.raises(LifecycleError):
        append_event(tmp_path, RUN, event, payload, actor_id="test.actor", operation_id=str(uuid.uuid4()), expected_revision=0, expected_tail_sha256=None)


def test_read_head_rejects_invalid_run_id_and_noncanonical_event(tmp_path):
    with pytest.raises(LifecycleError):
        read_head(tmp_path, "not-a-uuid")
    _hot(tmp_path)
    path = next((tmp_path / "lifecycle" / RUN).glob("*.json"))
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(LifecycleError):
        read_head(tmp_path, RUN)


def test_hash_tamper_missing_extra_and_reorder_fail_closed(tmp_path):
    _hot(tmp_path); _append(tmp_path, "keep_changed", _payload("hot", "hot", manual_keep=True))
    directory = tmp_path / "lifecycle" / RUN
    paths = sorted(directory.glob("*.json")); paths[0].write_bytes(paths[0].read_bytes() + b" ")
    with pytest.raises(LifecycleError): read_head(tmp_path, RUN)
    paths[0].unlink()
    with pytest.raises(LifecycleError): read_head(tmp_path, RUN)


def test_write_failure_leaves_previous_event_chain(tmp_path, monkeypatch):
    head = _hot(tmp_path)
    monkeypatch.setattr(lifecycle, "_write_new", lambda *_: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError): _append(tmp_path, "keep_changed", _payload("hot", "hot", manual_keep=True))
    assert read_head(tmp_path, RUN) == head


def test_duplicate_key_and_unknown_file_fail_closed(tmp_path):
    _hot(tmp_path); directory = tmp_path / "lifecycle" / RUN
    (directory / "00000001_bad.json").write_text('{"x":1,"x":2}', "utf-8")
    with pytest.raises(LifecycleError): read_head(tmp_path, RUN)


def _process_writer(root: str, revision: int, tail: str, queue):
    try:
        append_event(root, RUN, "keep_changed", _payload("hot", "hot", manual_keep=True), actor_id="test.actor", operation_id=str(uuid.uuid4()), expected_revision=revision, expected_tail_sha256=tail)
        queue.put("ok")
    except LifecycleConflict:
        queue.put("conflict")


def test_multiprocess_compare_and_append_has_one_winner(tmp_path):
    head = _hot(tmp_path)
    context = multiprocessing.get_context("spawn"); queue = context.Queue()
    first = context.Process(target=_process_writer, args=(str(tmp_path), head.revision, head.tail_sha256, queue)); second = context.Process(target=_process_writer, args=(str(tmp_path), head.revision, head.tail_sha256, queue))
    first.start(); second.start(); first.join(20); second.join(20)
    outcomes = sorted([queue.get(timeout=5), queue.get(timeout=5)])
    assert first.exitcode == second.exitcode == 0 and outcomes == ["conflict", "ok"]


def test_lock_identity_prevents_stale_release(tmp_path):
    _hot(tmp_path)
    token = lifecycle._acquire_lock(Path(tmp_path), RUN, "test.actor", "44444444-4444-4444-8444-444444444444")
    path = tmp_path / "locks" / f"{RUN}.storage.lock"
    with pytest.raises(LifecycleConflict): lifecycle.recover_stale_lock(tmp_path, RUN, expected_lock_sha256="A" * 64, actor_id="test.actor")
    lifecycle._release_lock(Path(tmp_path), RUN, token)


def test_stale_live_and_replaced_locks_are_fail_closed(tmp_path, monkeypatch):
    _hot(tmp_path)
    path = tmp_path / "locks" / f"{RUN}.storage.lock"
    path.parent.mkdir(exist_ok=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock = {"schema_version": "0.1", "artifact_type": "sqvm_experiment_storage_lock", "run_id": RUN, "owner_id": "test.actor", "operation_id": "55555555-5555-4555-8555-555555555555", "owner_pid": child.pid, "created_utc": "2026-07-21T00:00:00Z"}
        token = canonical_json_bytes(lock); path.write_bytes(token)
        digest = hashlib.sha256(token).hexdigest().upper()
        with pytest.raises(LifecycleConflict):
            lifecycle.recover_stale_lock(tmp_path, RUN, expected_lock_sha256=digest, actor_id="test.actor")
        child.terminate(); child.wait(timeout=10)
        lifecycle.recover_stale_lock(tmp_path, RUN, expected_lock_sha256=digest, actor_id="test.actor")
        assert not path.exists()
        path.write_bytes(token.replace(b"test.actor", b"next.actor"))
        with pytest.raises(LifecycleConflict):
            lifecycle.recover_stale_lock(tmp_path, RUN, expected_lock_sha256=digest, actor_id="test.actor")
    finally:
        if child.poll() is None:
            child.terminate(); child.wait(timeout=10)


def test_malformed_or_wrong_run_lock_is_rejected(tmp_path):
    _hot(tmp_path)
    path = tmp_path / "locks" / f"{RUN}.storage.lock"; path.parent.mkdir(exist_ok=True)
    path.write_text('{"bad":true}', "utf-8")
    with pytest.raises(LifecycleError):
        lifecycle.recover_stale_lock(tmp_path, RUN, expected_lock_sha256="A" * 64, actor_id="test.actor")
    other = "99999999-9999-4999-8999-999999999999"
    lock = {"schema_version": "0.1", "artifact_type": "sqvm_experiment_storage_lock", "run_id": other, "owner_id": "test.actor", "operation_id": "66666666-6666-4666-8666-666666666666", "owner_pid": 999999, "created_utc": "2026-07-21T00:00:00Z"}
    raw = canonical_json_bytes(lock); path.write_bytes(raw)
    with pytest.raises(LifecycleError, match="run_id"):
        lifecycle.recover_stale_lock(tmp_path, RUN, expected_lock_sha256=hashlib.sha256(raw).hexdigest().upper(), actor_id="test.actor")


def test_read_head_does_not_create_root(tmp_path):
    root = tmp_path / "absent"
    assert read_head(root, RUN).revision == 0
    assert not root.exists()


def test_completion_requires_started_event_and_same_operation(tmp_path):
    _hot(tmp_path)
    with pytest.raises(LifecycleError):
        _append(tmp_path, "trashed", _payload("hot", "trash", trash_previous_state="hot"))
    operation = str(uuid.uuid4())
    _append(tmp_path, "trash_started", _payload("hot", "hot"), operation_id=operation)
    with pytest.raises(LifecycleError, match="pending operation"):
        _append(tmp_path, "trashed", _payload("hot", "trash", trash_previous_state="hot"), operation_id=str(uuid.uuid4()))
    with pytest.raises(LifecycleError):
        _append(tmp_path, "hot_cleanup_completed", _payload("hot", "archived"))


def test_failure_completions_clear_pending_and_allow_new_operation(tmp_path):
    archive = str(uuid.uuid4())
    _hot(tmp_path); _append(tmp_path, "archive_started", _payload("hot", "archiving"), operation_id=archive)
    with pytest.raises(LifecycleError):
        _append(tmp_path, "archive_failed", _payload("archiving", "hot"), operation_id=str(uuid.uuid4()))
    head = _append(tmp_path, "archive_failed", _payload("archiving", "hot"), operation_id=archive)
    assert head.state == "hot" and head.pending_event_type is None
    assert _append(tmp_path, "archive_started", _payload("hot", "archiving"), operation_id=str(uuid.uuid4())).pending_event_type == "archive_started"


def test_invalid_append_request_creates_nothing(tmp_path):
    root = tmp_path / "new-root"
    with pytest.raises(LifecycleError):
        append_event(root, RUN, "hot_discovered", _payload(None, "hot"), actor_id=object(), operation_id=str(uuid.uuid4()), expected_revision=0, expected_tail_sha256=None)
    with pytest.raises(LifecycleError):
        append_event(root, RUN, "hot_discovered", _payload(None, "hot"), actor_id="test.actor", operation_id=str(uuid.uuid4()), expected_revision=False, expected_tail_sha256=None)
    assert not root.exists()


@pytest.mark.parametrize("alias", ["../hot", "hot\\run", "C:hot", "hot//run", "hot/./run", "hot/../run"])
def test_carrier_alias_rejects_unsafe_paths(tmp_path, alias):
    with pytest.raises(LifecycleError):
        _append(tmp_path, "hot_discovered", {**_payload(None, "hot"), "carrier_path": alias})


def test_lifecycle_root_ancestor_link_is_rejected(tmp_path):
    actual = tmp_path / "actual"; actual.mkdir()
    alias = tmp_path / "alias"; alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(LifecycleError):
        read_head(alias / "lifecycle", RUN)


def test_lifecycle_dangling_ancestor_link_is_rejected(tmp_path):
    dangling = tmp_path / "dangling"; dangling.symlink_to(tmp_path / "missing", target_is_directory=True)
    with pytest.raises(LifecycleError):
        read_head(dangling / "lifecycle", RUN)
