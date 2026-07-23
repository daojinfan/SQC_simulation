from __future__ import annotations

from dataclasses import replace
import json
from concurrent.futures import ThreadPoolExecutor
import shutil
from pathlib import Path
import uuid

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
import sqvm.storage.operations as operations
from sqvm.calibration.api import run_spectroscopy
from sqvm.storage.operations import ExperimentStorageOperations, StorageMutationRequest, StorageOperationError
from sqvm.storage.catalog import CatalogRoots, rebuild_catalog, query_catalog
from test_qubit_spectroscopy import _context, _result, _single_request


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json"


def _runner(monkeypatch):
    def fake(circuits, _context_value, output_root, _repository_root, **_kwargs):
        root = Path(output_root); rows = []
        for circuit in circuits:
            evidence = root / "circuits" / circuit.circuit_id; evidence.mkdir(parents=True)
            (evidence / "result.bin").write_bytes(circuit.circuit_id.encode())
            rows.append(replace(_result(circuit.circuit_id, .8, .19, 0, 0), evidence_root=evidence, model_evidence_root=evidence))
        return tuple(rows)
    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake)


@pytest.fixture
def service(monkeypatch, tmp_path):
    _runner(monkeypatch)
    repository = tmp_path / "repository"
    schema = repository / "docs" / "designs" / "07_1_3_platform_configuration_v0_2.schema.json"
    schema.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "docs" / "designs" / schema.name, schema)
    shutil.copytree(ROOT / "configs", repository / "configs")
    config = repository / "output" / "platform-configurations"; config.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "output" / "platform-configurations", config)
    hot = repository / "experiments"
    run = run_spectroscopy({"Q1": (4.8, 4.82)}, frequency_step_GHz=.01, output_root=hot, configuration_storage_root=config, repository_root=repository, timeout_s=10)
    target = run.root
    assert target.name.startswith("qubit_spectroscopy_") and target.name != f"qubit_spectroscopy_{run.run_id}"
    storage = tmp_path / "storage"; storage.mkdir()
    index = tmp_path / "experiment-index"; index.mkdir()
    ops = ExperimentStorageOperations(hot_root=hot, storage_root=storage, configuration_root=config, experiment_output_root=index, catalog_revision=7)
    ops._test_hot_alias = target.name
    request = StorageMutationRequest("test.actor", 7, __import__("hashlib").sha256((target / "workflow.json").read_bytes()).hexdigest().upper(), "retention test")
    yield ops, run.run_id, request, hot, storage


def _hot(ops, hot: Path) -> Path:
    return hot / ops._test_hot_alias


def _catalog_state(hot: Path, storage: Path, run_id: str) -> str:
    roots = CatalogRoots(hot, storage / "archives", storage, storage / "tombstones", storage / "trash")
    catalog = storage / "catalog" / "catalog.sqlite"
    rebuild_catalog(catalog, roots)
    return query_catalog(catalog, run_id)[0].storage_state


def test_archive_restore_hot_and_keep(service):
    ops, run, request, hot, storage = service
    result = ops.archive(run, request)
    assert result.state == "archived" and not _hot(ops, hot).exists()
    assert (storage / "archives" / f"{run}.sqrun").exists()
    restored = ops.restore_hot(run, request)
    assert restored.state == "hot" and (_hot(ops, hot) / "workflow.json").is_file()
    assert ops.set_keep(run, True, request).state == "hot"
    with pytest.raises(StorageOperationError): ops.trash(run, request)


def test_revision_hash_conflicts_and_reference_block_trash(service):
    ops, run, request, hot, _storage = service
    with pytest.raises(StorageOperationError): ops.archive(run, StorageMutationRequest("test.actor", 8, request.expected_workflow_sha256, "x"))
    with pytest.raises(StorageOperationError): ops.archive(run, StorageMutationRequest("test.actor", 7, "A" * 64, "x"))
    index = ops._experiment_root / "bad"; index.mkdir(); (index / "workflow.json").write_text('{"workflow_id":"unknown"}', "utf-8")
    with pytest.raises(StorageOperationError): ops.trash(run, request)


def test_trash_and_restore_hot_payload(service):
    ops, run, request, hot, storage = service
    moved = ops.trash(run, request)
    assert moved.state == "trash" and (storage / "trash" / run / "trash-record.json").is_file()
    assert not _hot(ops, hot).exists()
    restored = ops.restore_trash(run, request)
    assert restored.state == "hot" and (_hot(ops, hot) / "receipt.json").is_file()


def test_cross_volume_trash_and_restore_copy_verify_commit(service, monkeypatch):
    ops, run, request, hot, storage = service
    original = _hot(ops, hot)
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    moved = ops.trash(run, request)
    assert moved.state == "trash" and not original.exists()
    assert (storage / "trash" / run / "payload" / "workflow.json").is_file()
    restored = ops.restore_trash(run, request)
    assert restored.state == "hot" and _hot(ops, hot).exists()


def test_cross_volume_archive_payload_trash_and_restore(service, monkeypatch):
    ops, run, request, hot, storage = service
    ops.archive(run, request)
    archive = storage / "archives" / f"{run}.sqrun"
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    moved = ops.trash(run, request)
    assert moved.state == "trash" and not archive.exists()
    assert (storage / "trash" / run / "payload").is_file()
    restored = ops.restore_trash(run, request)
    assert restored.state == "archived" and archive.is_file()


def test_trash_record_write_failure_rolls_back_and_retry_is_safe(service, monkeypatch):
    ops, run, request, hot, storage = service
    original = _hot(ops, hot)
    write = operations._write_new
    def fail_record(path, value):
        if path.name == "trash-record.json":
            raise OSError("simulated record sharing violation")
        return write(path, value)
    monkeypatch.setattr(operations, "_write_new", fail_record)
    with pytest.raises(StorageOperationError, match="source was retained"):
        ops.trash(run, request)
    assert original.is_dir() and not (storage / "trash" / run).exists()
    monkeypatch.setattr(operations, "_write_new", write)
    assert ops.trash(run, request).state == "trash"


def test_cross_volume_source_cleanup_failure_retains_source_and_allows_retry(service, monkeypatch):
    ops, run, request, hot, storage = service
    original = _hot(ops, hot)
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    remove = operations._remove_carrier_safe
    def fail_source(path, kind):
        if path == original:
            raise PermissionError("simulated Windows sharing violation")
        return remove(path, kind)
    monkeypatch.setattr(operations, "_remove_carrier_safe", fail_source)
    with pytest.raises(StorageOperationError, match="source was retained"):
        ops.trash(run, request)
    assert original.is_dir() and not (storage / "trash" / run).exists()
    monkeypatch.setattr(operations, "_remove_carrier_safe", remove)
    assert ops.trash(run, request).state == "trash"


def test_copy_and_directory_flush_failures_preserve_hot_and_close_pending(service, monkeypatch):
    ops, run, request, hot, storage = service
    original = _hot(ops, hot)
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    copy = operations._copy_file_streaming
    monkeypatch.setattr(operations, "_copy_file_streaming", lambda *_args: (_ for _ in ()).throw(OSError("copy failed")))
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    assert original.is_dir() and not (storage / "trash" / run).exists()
    monkeypatch.setattr(operations, "_copy_file_streaming", copy)
    flush = operations._fsync_directory
    monkeypatch.setattr(operations, "_fsync_directory", lambda _path: (_ for _ in ()).throw(OSError("directory flush failed")))
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    assert original.is_dir() and not (storage / "trash" / run).exists()
    monkeypatch.setattr(operations, "_fsync_directory", flush)
    assert ops.trash(run, request).state == "trash"


def test_archive_publish_then_event_failure_is_resumable_without_mutating_bundle(service, monkeypatch):
    ops, run, request, hot, storage = service
    append = operations.append_event
    def fail_verified(*args, **kwargs):
        if args[2] == "archive_verified":
            raise operations.LifecycleError("simulated event write failure")
        return append(*args, **kwargs)
    monkeypatch.setattr(operations, "append_event", fail_verified)
    with pytest.raises(StorageOperationError, match="lifecycle completion is pending"):
        ops.archive(run, request)
    bundle = storage / "archives" / f"{run}.sqrun"
    before = bundle.read_bytes()
    assert bundle.is_file() and _hot(ops, hot).is_dir()
    monkeypatch.setattr(operations, "append_event", append)
    assert ops.archive(run, request).state == "archived"
    assert bundle.read_bytes() == before


def test_reference_added_after_initial_scan_blocks_before_destructive_move(service, monkeypatch):
    ops, run, request, hot, storage = service
    original_scan = ops._fresh_references
    calls = 0
    def raced_scan():
        nonlocal calls
        calls += 1
        if calls == 2:
            event_id = str(uuid.uuid4())
            audit = ops._config_root / "audit" / f"{event_id}.json"
            audit.write_text(json.dumps({"schema_version":"0.1", "event_id":event_id, "event":"experiment_candidates_applied_to_current", "actor_id":"test.actor", "created_utc":"2026-07-22T00:00:00Z", "details":{"device_id":"demo_2q1c2r", "experiment_run_id":run, "recommendation_id":str(uuid.uuid4()), "candidate_ids":["candidate"], "targets":["Q1"], "content_sha256":"A" * 64}}), "utf-8")
        return original_scan()
    monkeypatch.setattr(ops, "_fresh_references", raced_scan)
    with pytest.raises(StorageOperationError, match="references changed"):
        ops.trash(run, request)
    assert _hot(ops, hot).is_dir()
    assert not (storage / "trash" / run / "payload").exists()


def test_manual_keep_unknown_reference_and_real_applied_audit_block_trash(service):
    ops, run, request, hot, storage = service
    assert ops.set_keep(run, True, request).state == "hot"
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    assert ops.set_keep(run, False, request).state == "hot"
    unknown = ops._experiment_root / "unknown"; unknown.mkdir()
    (unknown / "workflow.json").write_text('{"workflow_id":"not_registered"}', "utf-8")
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    shutil.rmtree(unknown)
    event_id = str(uuid.uuid4())
    (ops._config_root / "audit" / f"{event_id}.json").write_text(json.dumps({"schema_version":"0.1", "event_id":event_id, "event":"experiment_candidates_applied_to_current", "actor_id":"test.actor", "created_utc":"2026-07-22T00:00:00Z", "details":{"device_id":"demo_2q1c2r", "experiment_run_id":run, "recommendation_id":str(uuid.uuid4()), "candidate_ids":["candidate"], "targets":["Q1"], "content_sha256":"B" * 64}}), "utf-8")
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    assert ops.archive(run, request).state == "archived"


def test_same_run_concurrent_archive_has_one_committer_and_verified_result(service):
    ops, run, request, _hot, storage = service
    def call():
        try:
            return ops.archive(run, request).state
        except StorageOperationError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _value: call(), range(2)))
    assert results.count("archived") == 1 and results.count("conflict") == 1
    assert (storage / "archives" / f"{run}.sqrun").is_file()


def test_archive_atomic_publish_failure_retains_hot_and_is_retryable(service, monkeypatch):
    ops, run, request, hot, storage = service
    publish = operations._publish_new
    monkeypatch.setattr(operations, "_publish_new", lambda *_args: (_ for _ in ()).throw(PermissionError("simulated rename sharing violation")))
    with pytest.raises(StorageOperationError, match="hot evidence was retained"):
        ops.archive(run, request)
    assert _hot(ops, hot).is_dir()
    assert not (storage / "archives" / f"{run}.sqrun").exists()
    monkeypatch.setattr(operations, "_publish_new", publish)
    assert ops.archive(run, request).state == "archived"


def test_cross_volume_restore_cleanup_failure_preserves_trash_for_retry(service, monkeypatch):
    ops, run, request, hot, storage = service
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    ops.trash(run, request)
    payload = storage / "trash" / run / "payload"
    target = _hot(ops, hot)
    remove = operations._remove_carrier_safe
    def fail_payload(path, kind):
        if path == payload:
            raise PermissionError("simulated payload sharing violation")
        return remove(path, kind)
    monkeypatch.setattr(operations, "_remove_carrier_safe", fail_payload)
    with pytest.raises(StorageOperationError, match="trash restore failed"):
        ops.restore_trash(run, request)
    assert payload.is_dir() and not target.exists() and ops._head(run).state == "trash"
    monkeypatch.setattr(operations, "_remove_carrier_safe", remove)
    assert ops.restore_trash(run, request).state == "hot"


def test_hot_carrier_replacement_before_move_is_rejected_without_payload(service, monkeypatch):
    ops, run, request, hot, storage = service
    original_scan = ops._fresh_references
    calls = 0
    workflow = _hot(ops, hot) / "workflow.json"
    def raced_scan():
        nonlocal calls
        calls += 1
        if calls == 2:
            workflow.write_bytes(workflow.read_bytes() + b" ")
        return original_scan()
    monkeypatch.setattr(ops, "_fresh_references", raced_scan)
    with pytest.raises(StorageOperationError, match="hot carrier verifier rejected"):
        ops.trash(run, request)
    assert _hot(ops, hot).is_dir()
    assert not (storage / "trash" / run / "payload").exists()


def test_cross_volume_file_fsync_failure_preserves_source_and_retry_is_safe(service, monkeypatch):
    ops, run, request, hot, storage = service
    original = _hot(ops, hot)
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    copy = operations._copy_file_streaming
    def fail_after_write(source, target):
        with source.open("rb") as input_stream, target.open("xb") as output_stream:
            output_stream.write(input_stream.read(64))
        raise OSError("simulated file fsync failure")
    monkeypatch.setattr(operations, "_copy_file_streaming", fail_after_write)
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    assert original.is_dir() and not (storage / "trash" / run).exists()
    monkeypatch.setattr(operations, "_copy_file_streaming", copy)
    assert ops.trash(run, request).state == "trash"


def test_hot_cleanup_failure_keeps_verified_duplicate_and_can_resume(service, monkeypatch):
    ops, run, request, hot, storage = service
    original = _hot(ops, hot)
    remove = operations._remove_tree_safe
    def fail_hot(path):
        if path == original:
            raise PermissionError("simulated hot cleanup sharing violation")
        return remove(path)
    monkeypatch.setattr(operations, "_remove_tree_safe", fail_hot)
    with pytest.raises(StorageOperationError, match="duplicate retained"):
        ops.archive(run, request)
    bundle = storage / "archives" / f"{run}.sqrun"
    assert original.is_dir() and bundle.is_file() and ops._head(run).state == "archived_duplicate"
    monkeypatch.setattr(operations, "_remove_tree_safe", remove)
    assert ops.archive(run, request).state == "archived"


def test_tampered_trash_record_is_rejected_before_restore(service):
    ops, run, request, _hot, storage = service
    ops.trash(run, request)
    record = storage / "trash" / run / "trash-record.json"
    value = json.loads(record.read_text("utf-8"))
    value["payload_logical_bytes"] = True
    value["record_sha256"] = operations._sha(operations._canonical({key: item for key, item in value.items() if key != "record_sha256"}))
    record.write_bytes(operations._canonical(value))
    with pytest.raises(StorageOperationError, match="trash record is invalid"):
        ops.restore_trash(run, request)


def test_published_trash_record_event_failure_resumes_same_operation(service, monkeypatch):
    ops, run, request, hot, storage = service
    append = operations.append_event
    def fail_completion(*args, **kwargs):
        if args[2] == "trashed":
            raise operations.LifecycleError("simulated event publication failure")
        return append(*args, **kwargs)
    monkeypatch.setattr(operations, "append_event", fail_completion)
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    payload = storage / "trash" / run / "payload"
    assert payload.is_dir() and not _hot(ops, hot).exists()
    operation_id = json.loads((storage / "trash" / run / "trash-record.json").read_text("utf-8"))["operation_id"]
    monkeypatch.setattr(operations, "append_event", append)
    resumed = ops.trash(run, request)
    assert resumed.state == "trash" and resumed.operation_id == operation_id


def test_real_api_alias_is_resolved_by_verified_workflow_identity(service):
    ops, run, request, hot, _storage = service
    assert _hot(ops, hot).name != f"qubit_spectroscopy_{run}"
    assert ops._find_hot(run, request.expected_workflow_sha256) == _hot(ops, hot)


def test_constructor_initializes_all_catalog_authority_roots(service):
    ops, run, _request, hot, storage = service
    assert all((storage / name).is_dir() for name in ("archives", "pins", "lifecycle", "trash", "tombstones"))
    assert _catalog_state(hot, storage, run) == "hot"


@pytest.mark.parametrize("payload", [
    pytest.param(b"{\xff}", id="invalid-utf8"),
    pytest.param(b'{"run_id":"x","run_id":"x"}', id="duplicate-key"),
    pytest.param(b'{"artifact_version":"0.3","workflow_id":"x","run_id":NaN}', id="nonfinite"),
    pytest.param(b"{" + b' ' * (64 * 1024) + b"}", id="oversize"),
])
def test_hot_control_workflow_rejects_malformed_or_oversized_bytes(service, payload):
    ops, run, request, hot, _storage = service
    workflow = _hot(ops, hot) / "workflow.json"
    workflow.write_bytes(payload)
    with pytest.raises(StorageOperationError):
        ops.archive(run, request)


def test_hot_control_workflow_hardlink_and_symlink_fail_closed(service):
    ops, run, request, hot, _storage = service
    workflow = _hot(ops, hot) / "workflow.json"
    link = workflow.with_name("workflow-copy.json")
    link.hardlink_to(workflow)
    with pytest.raises(StorageOperationError):
        ops.archive(run, request)
    link.unlink()
    saved = workflow.with_name("workflow-saved.json")
    workflow.rename(saved)
    workflow.symlink_to(saved.name)
    with pytest.raises(StorageOperationError):
        ops.archive(run, request)


def test_hot_control_workflow_replacement_between_lstat_and_open_fails_closed(service, monkeypatch):
    ops, run, request, hot, _storage = service
    workflow = _hot(ops, hot) / "workflow.json"
    original_open = Path.open
    changed = False
    def replace_before_open(path, *args, **kwargs):
        nonlocal changed
        if path == workflow and args and args[0] == "rb" and not changed:
            changed = True
            replacement = workflow.with_name("workflow-replacement.json")
            replacement.write_bytes(workflow.read_bytes())
            replacement.replace(workflow)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", replace_before_open)
    with pytest.raises(StorageOperationError, match="changed before open"):
        ops.archive(run, request)


def test_trash_record_control_reader_rejects_oversize_and_invalid_bytes(service):
    ops, run, request, _hot_root, storage = service
    ops.trash(run, request)
    record = storage / "trash" / run / "trash-record.json"
    record.write_bytes(b"{" + b" " * (64 * 1024) + b"}")
    with pytest.raises(StorageOperationError):
        ops.restore_trash(run, request)


def test_cross_volume_trash_rejects_real_execution_hardlink(service, monkeypatch):
    ops, run, request, hot, storage = service
    evidence = next(_hot(ops, hot).glob("execution/circuits/*/result.bin"))
    evidence.with_name("result-hardlink.bin").hardlink_to(evidence)
    monkeypatch.setattr(operations, "_same_volume", lambda source, target: source.parent == target)
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    assert _hot(ops, hot).is_dir() and not (storage / "trash" / run / "payload").exists()


def test_restore_hot_completion_event_failure_resumes_hot_only_pending(service, monkeypatch):
    ops, run, request, hot, storage = service
    ops.archive(run, request)
    append = operations.append_event
    def fail_completion(*args, **kwargs):
        if args[2] == "archive_restore_completed":
            raise operations.LifecycleError("simulated completion event failure")
        return append(*args, **kwargs)
    monkeypatch.setattr(operations, "append_event", fail_completion)
    with pytest.raises(StorageOperationError):
        ops.restore_hot(run, request)
    assert ops._head(run).state == "archiving" and _hot(ops, hot).is_dir()
    restored = _hot(ops, hot)
    assert restored.is_dir() and not (storage / "archives" / f"{run}.sqrun").exists()
    monkeypatch.setattr(operations, "append_event", append)
    result = ops.restore_hot(run, request)
    assert result.state == "hot" and ops._head(run).state == "hot" and restored.is_dir()


def test_restore_hot_target_conflict_preserves_archived_carrier(service):
    ops, run, request, _hot_root, storage = service
    ops.archive(run, request)
    conflict = _hot(ops, _hot_root); conflict.mkdir()
    with pytest.raises(StorageOperationError, match="target already exists"):
        ops.restore_hot(run, request)
    assert ops._head(run).state == "archived" and (storage / "archives" / f"{run}.sqrun").is_file()


def test_pending_trash_record_mismatch_is_not_resumed(service, monkeypatch):
    ops, run, request, hot, storage = service
    append = operations.append_event
    monkeypatch.setattr(operations, "append_event", lambda *args, **kwargs: (_ for _ in ()).throw(operations.LifecycleError("event failed")) if args[2] == "trashed" else append(*args, **kwargs))
    with pytest.raises(StorageOperationError):
        ops.trash(run, request)
    record = storage / "trash" / run / "trash-record.json"
    value = json.loads(record.read_text("utf-8")); value["operation_id"] = str(uuid.uuid4())
    value["record_sha256"] = operations._sha(operations._canonical({key: item for key, item in value.items() if key != "record_sha256"}))
    record.write_bytes(operations._canonical(value))
    monkeypatch.setattr(operations, "append_event", append)
    with pytest.raises(StorageOperationError, match="matching recovery record"):
        ops.trash(run, request)
    assert ops._head(run).pending_event_type == "trash_started" and (storage / "trash" / run / "payload").is_dir()


def test_set_keep_transitions_for_archived_and_trash(service):
    ops, run, request, _hot_root, storage = service
    ops.archive(run, request)
    archived = ops.set_keep(run, True, request)
    assert archived.state == "archived" and ops._head(run).manual_keep is True
    assert ops.set_keep(run, False, request).state == "archived"
    ops.trash(run, request)
    trashed = ops.set_keep(run, True, request)
    assert trashed.state == "trash" and ops._head(run).manual_keep is True and (storage / "trash" / run / "payload").exists()


def test_duplicate_or_unrelated_invalid_hot_candidate_fails_closed(service):
    ops, run, request, hot, _storage = service
    shutil.copytree(_hot(ops, hot), hot / "qubit_spectroscopy_duplicate")
    with pytest.raises(StorageOperationError, match="ambiguous"):
        ops.archive(run, request)


def test_unrelated_broken_published_candidate_fails_closed(service):
    ops, run, request, hot, _storage = service
    (hot / "qubit_spectroscopy_broken").mkdir()
    with pytest.raises(StorageOperationError, match="hot carrier scan could not be completed"):
        ops.archive(run, request)


@pytest.mark.parametrize("run_value,request_value", [
    ("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", None),
    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", None),
    (None, StorageMutationRequest("test.actor", 7, "a" * 64, "reason")),
    (None, StorageMutationRequest(" test.actor", 7, "A" * 64, "reason")),
    (None, StorageMutationRequest("test.actor", True, "A" * 64, "reason")),
    (None, StorageMutationRequest("test.actor", 7, "A" * 64, " reason")),
    (None, StorageMutationRequest("test\nactor", 7, "A" * 64, "reason")),
])
def test_public_request_identity_is_strict(service, run_value, request_value):
    ops, run, request, _hot_root, storage = service
    with pytest.raises(StorageOperationError):
        ops.archive(run if run_value is None else run_value, request if request_value is None else request_value)
    assert not (storage / "lifecycle" / run).exists()


def test_result_operation_ids_match_terminal_lifecycle_event(service):
    ops, run, request, hot, storage = service
    archived = ops.archive(run, request)
    archive_events = sorted((storage / "lifecycle" / run).glob("*.json"))
    assert json.loads(archive_events[-1].read_text("utf-8"))["operation_id"] == archived.operation_id
    restored = ops.restore_hot(run, request)
    assert not (storage / "archives" / f"{run}.sqrun").exists() and ops._head(run).state == "hot" and _catalog_state(hot, storage, run) == "hot"
    restore_events = sorted((storage / "lifecycle" / run).glob("*.json"))
    assert json.loads(restore_events[-1].read_text("utf-8"))["operation_id"] == restored.operation_id
    kept = ops.set_keep(run, True, request)
    keep_events = sorted((storage / "lifecycle" / run).glob("*.json"))
    assert json.loads(keep_events[-1].read_text("utf-8"))["operation_id"] == kept.operation_id


def test_restore_hot_retries_archive_cleanup_then_completes(service, monkeypatch):
    ops, run, request, hot, storage = service
    ops.archive(run, request)
    archive = storage / "archives" / f"{run}.sqrun"
    remove = operations._remove_carrier_safe
    calls = 0
    def fail_once(path, kind):
        nonlocal calls
        if path == archive:
            calls += 1
            if calls == 1:
                raise PermissionError("simulated archive sharing violation")
        return remove(path, kind)
    monkeypatch.setattr(operations, "_remove_carrier_safe", fail_once)
    result = ops.restore_hot(run, request)
    assert result.state == "hot" and calls == 2 and not archive.exists() and ops._head(run).state == "hot"


def test_restore_hot_cleanup_failure_rolls_hot_back_to_archived(service, monkeypatch):
    ops, run, request, hot, storage = service
    ops.archive(run, request)
    archive = storage / "archives" / f"{run}.sqrun"
    remove = operations._remove_carrier_safe
    monkeypatch.setattr(operations, "_remove_carrier_safe", lambda path, kind: (_ for _ in ()).throw(PermissionError("archive locked")) if path == archive else remove(path, kind))
    with pytest.raises(StorageOperationError, match="rolled back to archived"):
        ops.restore_hot(run, request)
    assert archive.is_file() and not _hot(ops, hot).exists() and ops._head(run).state == "archived" and _catalog_state(hot, storage, run) == "archived"


def test_restore_hot_cleanup_and_hot_rollback_failure_stays_pending_duplicate(service, monkeypatch):
    ops, run, request, hot, storage = service
    ops.archive(run, request)
    archive = storage / "archives" / f"{run}.sqrun"
    restored = _hot(ops, hot)
    remove_carrier = operations._remove_carrier_safe
    remove_tree = operations._remove_tree_safe
    monkeypatch.setattr(operations, "_remove_carrier_safe", lambda path, kind: (_ for _ in ()).throw(PermissionError("archive locked")) if path == archive else remove_carrier(path, kind))
    monkeypatch.setattr(operations, "_remove_tree_safe", lambda path: (_ for _ in ()).throw(PermissionError("hot locked")) if path == restored else remove_tree(path))
    with pytest.raises(StorageOperationError, match="both failed"):
        ops.restore_hot(run, request)
    assert archive.is_file() and restored.is_dir() and ops._head(run).state == "archiving"
