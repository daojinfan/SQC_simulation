from __future__ import annotations

import copy
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

import pytest

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.web.configuration_transactions import (
    ConfigurationTransactionError,
    ConfigurationTransactionManager,
)
from sqvm.web.configuration import PlatformConfigurationStore
import sqvm.web.configuration_transactions as transactions_module


pytestmark = pytest.mark.contract


DEVICE = "demo_2q1c2r"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def transaction_root():
    root = ROOT / "tmp" / f".configuration-transactions.{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _legacy_root(root: Path) -> tuple[Path, str]:
    snapshot_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    for name in ("current", "active", "snapshots", "pins", "audit"):
        (root / name).mkdir(parents=True)
    _write(
        root / "current" / f"{DEVICE}.json",
        {"device_id": DEVICE, "revision": 1, "content_sha256": "A" * 64},
    )
    _write(
        root / "active" / f"{DEVICE}.json",
        {"device_id": DEVICE, "snapshot_id": snapshot_id},
    )
    _write(
        root / "snapshots" / snapshot_id / "snapshot.json",
        {"device_id": DEVICE, "snapshot_id": snapshot_id, "content_sha256": "A" * 64},
    )
    _write(
        root / "pins" / f"{snapshot_id}.json",
        {"snapshot_id": snapshot_id, "actor_id": "test.actor"},
    )
    _write(
        root / "audit" / f"{event_id}.json",
        {
            "event_id": event_id,
            "created_utc": "2026-07-25T00:00:00Z",
            "details": {"device_id": DEVICE},
        },
    )
    return root, snapshot_id


def _increment(workspace: Path) -> dict:
    path = workspace / "current" / f"{DEVICE}.json"
    payload = json.loads(path.read_text("utf-8"))
    payload["revision"] += 1
    payload["content_sha256"] = chr(ord("A") + payload["revision"] - 1) * 64
    path.write_bytes(canonical_json_bytes(payload))
    return copy.deepcopy(payload)


def _contract_vectors() -> dict:
    return json.loads(
        (
            ROOT
            / "tests"
            / "fixtures"
            / "configuration_transaction_v1"
            / "vectors.json"
        ).read_text("utf-8")
    )


def test_generation_zero_import_and_committed_projection_are_verified(transaction_root: Path):
    root, snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)

    imported = manager.ensure_imported(DEVICE, root)
    reopened = ConfigurationTransactionManager(root).committed_view(DEVICE)

    assert imported.generation == reopened.generation == 0
    assert imported.transaction_id == reopened.transaction_id
    assert json.loads(
        (reopened.projection_root / "current" / f"{DEVICE}.json").read_text("utf-8")
    )["revision"] == 1
    assert (
        reopened.projection_root / "snapshots" / snapshot_id / "snapshot.json"
    ).is_file()
    assert len(reopened.idempotency_catalog["entries"]) == 1


def test_frozen_v01_field_vectors_match_generated_generation(transaction_root: Path):
    vectors = _contract_vectors()
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    view = manager.ensure_imported(DEVICE, root)
    receipt = json.loads((view.bundle_root / "receipt.json").read_text("utf-8"))
    entry = view.idempotency_catalog["entries"][0]

    assert sorted(view.head) == vectors["head_fields"]
    assert sorted(view.manifest) == vectors["manifest_fields"]
    assert sorted(receipt) == vectors["receipt_fields"]
    assert sorted(entry) == vectors["idempotency_entry_fields"]
    assert sorted(view.manifest["state_refs"]) == vectors["state_ref_fields"]


def test_all_frozen_authoritative_writes_accept_operation_id() -> None:
    operations = _contract_vectors()["authoritative_operations"]

    for operation in operations:
        signature = inspect.signature(getattr(PlatformConfigurationStore, operation))
        assert "operation_id" in signature.parameters
        assert signature.parameters["operation_id"].default is None


def test_supported_windows_extended_path_budget_commits(
    transaction_root: Path,
):
    vectors = _contract_vectors()["path_budget"]
    target_length = vectors["maximum_supported_configuration_root_characters"]
    suffix_length = max(1, target_length - len(str(transaction_root)) - 1)
    root, _snapshot_id = _legacy_root(transaction_root / ("p" * suffix_length))
    assert len(str(root)) <= target_length
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    committed = manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=str(uuid.uuid4()),
        request={"expected_revision": 1},
        legacy_root=root,
        transform=_increment,
    )
    view = manager.committed_view(DEVICE)
    longest = max(len(str(path)) for path in view.bundle_root.rglob("*"))

    assert committed.transaction["generation"] == 1
    assert longest <= vectors["maximum_tested_committed_file_characters"]


def test_commit_and_same_request_replay_return_one_stable_transaction(transaction_root: Path):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    operation_id = str(uuid.uuid4())
    request = {"expected_revision": 1, "value": 2}

    first = manager.execute(
        device_id=DEVICE,
        operation_type="update_current_configuration",
        operation_id=operation_id,
        request=request,
        legacy_root=root,
        transform=_increment,
    )
    replay = ConfigurationTransactionManager(root).execute(
        device_id=DEVICE,
        operation_type="update_current_configuration",
        operation_id=operation_id,
        request=request,
        legacy_root=root,
        transform=lambda _workspace: pytest.fail("replay executed transform"),
    )

    assert first.response == replay.response
    assert first.transaction == replay.transaction
    assert first.transaction == {
        "transaction_id": operation_id,
        "operation_id": operation_id,
        "generation": 1,
        "durability_status": "committed",
        "projection_status": "complete",
        "superseded": False,
    }
    assert json.loads((root / "current" / f"{DEVICE}.json").read_text("utf-8"))[
        "revision"
    ] == 2


def test_old_operation_replay_is_marked_superseded(transaction_root: Path):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    first_id, second_id = str(uuid.uuid4()), str(uuid.uuid4())
    first_request = {"expected_revision": 1}
    manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=first_id,
        request=first_request,
        legacy_root=root,
        transform=_increment,
    )
    manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=second_id,
        request={"expected_revision": 2},
        legacy_root=root,
        transform=_increment,
    )

    replay = manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=first_id,
        request=first_request,
        legacy_root=root,
        transform=lambda _workspace: pytest.fail("old replay executed transform"),
    )

    assert replay.response["revision"] == 2
    assert replay.transaction["generation"] == 1
    assert replay.transaction["superseded"] is True
    assert manager.committed_view(DEVICE).generation == 2


def test_operation_id_reuse_with_different_request_is_rejected(transaction_root: Path):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    operation_id = str(uuid.uuid4())
    manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=operation_id,
        request={"value": 1},
        legacy_root=root,
        transform=_increment,
    )

    with pytest.raises(ConfigurationTransactionError) as captured:
        manager.execute(
            device_id=DEVICE,
            operation_type="update",
            operation_id=operation_id,
            request={"value": 2},
            legacy_root=root,
            transform=_increment,
        )

    assert captured.value.code == "idempotency_conflict"
    assert captured.value.status == 409
    assert manager.committed_view(DEVICE).generation == 1


def test_published_bundle_before_head_is_resumed_by_same_request(
    transaction_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    operation_id = str(uuid.uuid4())
    request = {"expected_revision": 1}
    real_switch = manager._switch_head

    def fail_switch(*_args, **_kwargs):
        raise OSError("injected head failure")

    monkeypatch.setattr(manager, "_switch_head", fail_switch)
    with pytest.raises(ConfigurationTransactionError) as captured:
        manager.execute(
            device_id=DEVICE,
            operation_type="update",
            operation_id=operation_id,
            request=request,
            legacy_root=root,
            transform=_increment,
        )
    assert captured.value.code == "configuration_transaction_aborted"
    assert manager.committed_view(DEVICE).generation == 0

    monkeypatch.setattr(manager, "_switch_head", real_switch)
    resumed = manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=operation_id,
        request=request,
        legacy_root=root,
        transform=lambda _workspace: pytest.fail("resume executed transform"),
    )

    assert resumed.response["revision"] == 2
    assert resumed.transaction["generation"] == 1
    assert manager.committed_view(DEVICE).generation == 1


def test_failure_before_bundle_publish_keeps_old_generation(
    transaction_root: Path,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    original = manager.ensure_imported(DEVICE, root)
    operation_id = str(uuid.uuid4())

    with pytest.raises(ConfigurationTransactionError) as captured:
        manager.execute(
            device_id=DEVICE,
            operation_type="update",
            operation_id=operation_id,
            request={"expected_revision": 1},
            legacy_root=root,
            transform=lambda _workspace: (_ for _ in ()).throw(
                RuntimeError("injected transform failure")
            ),
        )

    assert captured.value.code == "configuration_transaction_aborted"
    reopened = ConfigurationTransactionManager(root).committed_view(DEVICE)
    assert reopened.transaction_id == original.transaction_id
    assert reopened.generation == 0
    assert not (manager.bundles_root / DEVICE / operation_id).exists()


def test_projection_failure_after_head_recovers_forward_idempotently(
    transaction_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    old_projection = (root / "current" / f"{DEVICE}.json").read_bytes()
    operation_id = str(uuid.uuid4())
    monkeypatch.setattr(
        manager,
        "materialize",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("projection failed")),
    )

    committed = manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=operation_id,
        request={"expected_revision": 1},
        legacy_root=root,
        transform=_increment,
    )

    assert committed.transaction["projection_status"] == "pending"
    assert (root / "current" / f"{DEVICE}.json").read_bytes() == old_projection
    reopened = ConfigurationTransactionManager(root)
    before = (reopened.heads_root / f"{DEVICE}.json").read_bytes()
    for _ in range(3):
        assert reopened.ensure_imported(DEVICE, root).generation == 1
        assert (reopened.heads_root / f"{DEVICE}.json").read_bytes() == before
    assert json.loads((root / "current" / f"{DEVICE}.json").read_text("utf-8"))[
        "revision"
    ] == 2


@pytest.mark.parametrize("cut,exit_code", [("before_head", 73), ("after_head", 74)])
def test_subprocess_hard_exit_recovers_old_or_new_generation(
    transaction_root: Path,
    cut: str,
    exit_code: int,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    operation_id = str(uuid.uuid4())
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests" / "support" / "configuration_transaction_crash_worker.py"),
            str(root),
            operation_id,
            cut,
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == exit_code, result.stderr

    reopened = ConfigurationTransactionManager(root)
    if cut == "before_head":
        assert reopened.committed_view(DEVICE).generation == 0
        resumed = reopened.execute(
            device_id=DEVICE,
            operation_type="crash_test_update",
            operation_id=operation_id,
            request={"expected_revision": 1},
            legacy_root=root,
            transform=lambda _workspace: pytest.fail("orphan resume reran transform"),
        )
        assert resumed.transaction["generation"] == 1
    else:
        assert reopened.committed_view(DEVICE).generation == 1
        reopened.ensure_imported(DEVICE, root)
    assert json.loads((root / "current" / f"{DEVICE}.json").read_text("utf-8"))[
        "revision"
    ] == 2


def test_same_device_threads_are_serialized_without_lost_update(
    transaction_root: Path,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    ConfigurationTransactionManager(root).ensure_imported(DEVICE, root)
    barrier = threading.Barrier(3)
    results: list[int] = []
    failures: list[BaseException] = []

    def run() -> None:
        try:
            barrier.wait(timeout=5)
            result = ConfigurationTransactionManager(root).execute(
                device_id=DEVICE,
                operation_type="concurrent_update",
                operation_id=str(uuid.uuid4()),
                request={"worker": threading.current_thread().name},
                legacy_root=root,
                transform=_increment,
            )
            results.append(int(result.response["revision"]))
        except BaseException as exc:
            failures.append(exc)

    workers = [threading.Thread(target=run, name=f"worker-{index}") for index in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=5)
    for worker in workers:
        worker.join(timeout=15)

    assert not failures
    assert not any(worker.is_alive() for worker in workers)
    assert sorted(results) == [2, 3]
    assert ConfigurationTransactionManager(root).committed_view(DEVICE).generation == 2


def test_lock_wait_is_bounded_and_returns_stable_busy_error(
    transaction_root: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    monkeypatch.setattr(transactions_module, "_LOCK_TIMEOUT_SECONDS", 0.05)
    entered = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with manager._device_lock(DEVICE, str(uuid.uuid4())):
            entered.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=hold)
    worker.start()
    assert entered.wait(timeout=5)
    started = time.monotonic()
    try:
        with pytest.raises(ConfigurationTransactionError) as captured:
            ConfigurationTransactionManager(root).execute(
                device_id=DEVICE,
                operation_type="blocked_update",
                operation_id=str(uuid.uuid4()),
                request={"expected_revision": 1},
                legacy_root=root,
                transform=_increment,
            )
    finally:
        release.set()
        worker.join(timeout=5)

    assert time.monotonic() - started < 1.0
    assert captured.value.code == "configuration_transaction_busy"
    assert captured.value.status == 503
    assert captured.value.retry_after == 1


def test_committed_bundle_tamper_fails_closed(transaction_root: Path):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    view = manager.ensure_imported(DEVICE, root)
    path = view.projection_root / "current" / f"{DEVICE}.json"
    path.write_bytes(path.read_bytes() + b" ")

    with pytest.raises(ConfigurationTransactionError) as captured:
        manager.committed_view(DEVICE)

    assert captured.value.code == "configuration_recovery_required"
    assert captured.value.status == 503


def test_parent_manifest_identity_tamper_fails_closed(transaction_root: Path):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)
    manager.execute(
        device_id=DEVICE,
        operation_type="update",
        operation_id=str(uuid.uuid4()),
        request={"expected_revision": 1},
        legacy_root=root,
        transform=_increment,
    )
    current = manager.committed_view(DEVICE)
    parent_path = root / current.manifest["parent"]["manifest_path"]
    parent = json.loads(parent_path.read_text("utf-8"))
    replacement = str(uuid.uuid4())
    parent["transaction_id"] = replacement
    parent["operation_id"] = replacement
    parent["aggregate_sha256"] = transactions_module.sha256_json(
        {key: value for key, value in parent.items() if key != "aggregate_sha256"}
    )
    parent_path.write_bytes(canonical_json_bytes(parent))
    parent_hash = manager._sha(parent_path.read_bytes())

    manifest_path = current.bundle_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["parent"]["manifest_raw_sha256"] = parent_hash
    manifest["aggregate_sha256"] = transactions_module.sha256_json(
        {key: value for key, value in manifest.items() if key != "aggregate_sha256"}
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    head_path = manager.heads_root / f"{DEVICE}.json"
    head = json.loads(head_path.read_text("utf-8"))
    head["manifest_raw_sha256"] = manager._sha(manifest_path.read_bytes())
    head["parent_manifest_raw_sha256"] = parent_hash
    head_path.write_bytes(canonical_json_bytes(head))

    with pytest.raises(ConfigurationTransactionError) as captured:
        manager.committed_view(DEVICE)

    assert captured.value.code == "configuration_recovery_required"
    assert "parent identity" in str(captured.value)


@pytest.mark.parametrize("operation_id", ["not-a-uuid", str(uuid.uuid1()).upper()])
def test_noncanonical_or_non_v4_operation_id_is_rejected(
    transaction_root: Path,
    operation_id: str,
):
    root, _snapshot_id = _legacy_root(transaction_root / "config")
    manager = ConfigurationTransactionManager(root)
    manager.ensure_imported(DEVICE, root)

    with pytest.raises(ConfigurationTransactionError) as captured:
        manager.execute(
            device_id=DEVICE,
            operation_type="update",
            operation_id=operation_id,
            request={"value": 1},
            legacy_root=root,
            transform=_increment,
        )

    assert captured.value.code == "invalid_operation_id"
    assert captured.value.status == 422
