from __future__ import annotations

import json
import hashlib
from pathlib import Path
import shutil
import threading
import time
import uuid

import pytest

import sqvm.runtime.runner as runtime_runner
import sqvm.runtime.recovery as runtime_recovery
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime import (
    rebuild_run_catalog,
    recover_interrupted_run,
    recover_terminal_resource_lock,
    request_run_cancellation,
    run_experiment,
    verify_experiment_run,
)
from sqvm.runtime.dataset import EXPECTED_RESPONSE_SHA256
from sqvm.runtime.fake import DeterministicFakeBackend
from sqvm.runtime.journal import canonical_json_line_bytes
from sqvm.runtime.storage import inventory_tree


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/experiments/platform_deterministic_smoke_v1.yaml"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _rebind_terminal_documents(run_dir: Path) -> None:
    manifest_path = run_dir / "manifest.json"
    report_path = run_dir / "verification_report.json"
    receipt_path = run_dir / "receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    manifest["request_sha256"] = _sha(run_dir / "request.json")
    manifest["events_sha256"] = _sha(run_dir / "events.jsonl")
    manifest["event_tail_sha256"] = events[-1]["event_sha256"]
    terminal = {"manifest.json", "verification_report.json", "receipt.json"}
    manifest["payload_files"] = [row for row in inventory_tree(run_dir) if row["path"] not in terminal]
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manifest_sha256"] = _sha(manifest_path)
    report_path.write_bytes(canonical_json_bytes(report))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256"] = _sha(manifest_path)
    receipt["verification_report_sha256"] = _sha(report_path)
    receipt["event_tail_sha256"] = manifest["event_tail_sha256"]
    receipt_path.write_bytes(canonical_json_bytes(receipt))


@pytest.fixture
def output_root():
    relative = Path("tmp") / f"stage6_test_{uuid.uuid4().hex}"
    absolute = ROOT / relative
    try:
        yield relative, absolute
    finally:
        if absolute.exists():
            shutil.rmtree(absolute)


def test_end_to_end_run_verify_raw_bytes_and_catalog_rebuild(output_root):
    relative, absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    assert result.status == "completed" and result.catalog_indexed
    report = verify_experiment_run(result.run_dir, ROOT)
    assert report.ok
    assert (result.run_dir / "data/response.bin").read_bytes().hex().upper() == (
        "00000000000000C000000000000000000000000000000040"
        "000000000000F8BF000000000000E03F0000000000000440"
    )
    dataset = json.loads((result.run_dir / "data/dataset.json").read_text(encoding="utf-8"))
    assert dataset["variables"]["response"]["raw_sha256"] == EXPECTED_RESPONSE_SHA256

    (absolute / "catalog.sqlite").unlink()
    rebuilt = rebuild_run_catalog(absolute)
    assert rebuilt.ok and rebuilt.indexed_runs == 1 and rebuilt.catalog_path.is_file()
    with pytest.raises(ValueError, match="active staging"):
        request_run_cancellation(absolute, result.run_id)


def test_tampering_fails_independent_verification(output_root):
    relative, _absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    response = result.run_dir / "data/response.bin"
    raw = response.read_bytes()
    response.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    report = verify_experiment_run(result.run_dir, ROOT)
    assert not report.ok
    assert any("inventory" in reason or "dataset" in reason for reason in report.blocking_reasons)


def test_coordinated_request_rehash_still_fails_frozen_recomputation(output_root):
    relative, _absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    request_path = result.run_dir / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["execution"]["max_points"] = 7
    request_path.write_bytes(canonical_json_bytes(request))
    _rebind_terminal_documents(result.run_dir)
    report = verify_experiment_run(result.run_dir, ROOT)
    assert not report.ok
    assert any("frozen" in reason or "recomputed" in reason for reason in report.blocking_reasons)


def test_coordinated_event_rehash_still_fails_lifecycle_semantics(output_root):
    relative, _absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    path = result.run_dir / "events.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events[1], events[2] = events[2], events[1]
    previous = None
    rebuilt = []
    for index, event in enumerate(events):
        event["sequence"] = index
        event["prev_event_sha256"] = previous
        without_hash = {key: value for key, value in event.items() if key != "event_sha256"}
        event["event_sha256"] = hashlib.sha256(canonical_json_line_bytes(without_hash)).hexdigest().upper()
        previous = event["event_sha256"]
        rebuilt.append(canonical_json_line_bytes(event))
    path.write_bytes(b"".join(rebuilt))
    _rebind_terminal_documents(result.run_dir)
    report = verify_experiment_run(result.run_dir, ROOT)
    assert not report.ok
    assert any("run_started" in reason or "sequence" in reason for reason in report.blocking_reasons)


def test_backend_failure_publishes_verified_failed_record_without_dataset(output_root, monkeypatch):
    relative, _absolute = output_root

    def fail(_self, _command, _context):
        raise RuntimeError("injected backend failure")

    monkeypatch.setattr(DeterministicFakeBackend, "execute_point", fail)
    result = run_experiment(CONFIG, relative, ROOT)
    assert result.status == "failed"
    assert not (result.run_dir / "data").exists()
    assert verify_experiment_run(result.run_dir, ROOT).ok
    events = (result.run_dir / "events.jsonl").read_text(encoding="utf-8")
    assert '"event_type":"point_failed"' in events and '"event_type":"run_failed"' in events


def test_publication_failure_is_explicitly_recovered_without_resume(output_root, monkeypatch):
    relative, absolute = output_root

    def fail_publish(_staging, _target):
        raise OSError("injected rename failure")

    monkeypatch.setattr(runtime_runner, "atomic_publish", fail_publish)
    with pytest.raises(OSError, match="rename failure"):
        run_experiment(CONFIG, relative, ROOT)
    run_ids = [row.name for row in (absolute / "staging").iterdir() if row.is_dir()]
    assert len(run_ids) == 1
    monkeypatch.undo()

    original_recovery_publish = runtime_recovery.atomic_publish

    def fail_recovery_publish(_staging, _target):
        raise OSError("injected recovery publish failure")

    monkeypatch.setattr(runtime_recovery, "atomic_publish", fail_recovery_publish)
    with pytest.raises(OSError, match="recovery publish failure"):
        recover_interrupted_run(absolute, run_ids[0])
    assert (absolute / "quarantine" / run_ids[0]).is_dir()
    assert not (absolute / "recovery-locks" / f"{run_ids[0]}.lock").exists()
    monkeypatch.setattr(runtime_recovery, "atomic_publish", original_recovery_publish)

    recovered = recover_interrupted_run(absolute, run_ids[0])
    assert recovered.status == "interrupted"
    assert (absolute / "quarantine" / run_ids[0]).is_dir()
    assert not (absolute / "staging" / run_ids[0]).exists()
    assert {row.name for row in recovered.run_dir.iterdir()} == {
        "recovery.json", "manifest.json", "verification_report.json", "receipt.json"
    }
    rebuilt = rebuild_run_catalog(absolute)
    assert rebuilt.ok and rebuilt.indexed_runs == 1


def test_terminal_resource_lock_cleanup_requires_verified_run(output_root, monkeypatch):
    relative, absolute = output_root

    def fail_release(_path):
        raise OSError("injected lock delete failure")

    monkeypatch.setattr(runtime_runner, "remove_resource_lock", fail_release)
    with pytest.raises(OSError, match="lock delete failure"):
        run_experiment(CONFIG, relative, ROOT)
    run_dirs = [row for row in (absolute / "runs").iterdir() if row.is_dir()]
    assert len(run_dirs) == 1
    run_id = run_dirs[0].name
    monkeypatch.undo()

    receipt = recover_terminal_resource_lock(absolute, run_id)
    assert receipt.authorization_path.is_file()
    assert not list((absolute / "resource-locks").glob("*.lock"))


def test_terminal_resource_lock_cleanup_rejects_live_run_lock_tampering(output_root, monkeypatch):
    relative, absolute = output_root

    def fail_release(_path):
        raise OSError("injected lock delete failure")

    monkeypatch.setattr(runtime_runner, "remove_resource_lock", fail_release)
    with pytest.raises(OSError, match="lock delete failure"):
        run_experiment(CONFIG, relative, ROOT)
    monkeypatch.undo()
    run_dir = next(row for row in (absolute / "runs").iterdir() if row.is_dir())
    run_lock = absolute / "locks" / f"{run_dir.name}.lock"
    original = run_lock.read_bytes()
    payload = json.loads(original)
    payload["process_id"] += 1
    run_lock.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(ValueError, match="run lock"):
        recover_terminal_resource_lock(absolute, run_dir.name)
    assert not (absolute / "recovery-locks" / f"{run_dir.name}.lock").exists()
    assert list((absolute / "resource-locks").glob("*.lock"))

    run_lock.write_bytes(original)
    receipt = recover_terminal_resource_lock(absolute, run_dir.name)
    assert receipt.authorization_path.is_file()
    assert not list((absolute / "resource-locks").glob("*.lock"))


def test_interrupted_recovery_rejects_snapshot_tampering(output_root, monkeypatch):
    relative, absolute = output_root

    def fail_publish(_staging, _target):
        raise OSError("injected rename failure")

    monkeypatch.setattr(runtime_runner, "atomic_publish", fail_publish)
    with pytest.raises(OSError, match="rename failure"):
        run_experiment(CONFIG, relative, ROOT)
    monkeypatch.undo()
    staging = next(row for row in (absolute / "staging").iterdir() if row.is_dir())
    source_snapshot = staging / "snapshots/source.json"
    payload = json.loads(source_snapshot.read_text(encoding="utf-8"))
    payload["aggregate_sha256"] = "0" * 64
    source_snapshot.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(ValueError, match="source snapshot"):
        recover_interrupted_run(absolute, staging.name)
    assert (absolute / "quarantine" / staging.name).is_dir()
    assert (absolute / "quarantine-records" / f"{staging.name}.json").is_file()
    assert (absolute / "recovery-locks" / f"{staging.name}.lock").is_file()
    assert list((absolute / "resource-locks").glob("*.lock"))


def test_public_run_id_inputs_reject_path_traversal_without_mutation(tmp_path):
    output = tmp_path / "must-not-exist"
    for operation in (request_run_cancellation, recover_interrupted_run, recover_terminal_resource_lock):
        with pytest.raises(ValueError, match="UUID4"):
            operation(output, "../outside")
        assert not output.exists()
    with pytest.raises(ValueError, match="UUID4"):
        recover_interrupted_run(output, str(uuid.uuid1()))
    assert not output.exists()


def test_staging_creation_failure_cleans_new_reservation_locks(output_root, monkeypatch):
    relative, absolute = output_root
    original = Path.mkdir

    def fail_staging(self, *args, **kwargs):
        if self.parent.name == "staging":
            raise OSError("injected staging mkdir failure")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", fail_staging)
    with pytest.raises(OSError, match="staging mkdir failure"):
        run_experiment(CONFIG, relative, ROOT)
    assert not list((absolute / "locks").glob("*.lock"))
    assert not list((absolute / "resource-locks").glob("*.lock"))
    assert not list((absolute / "staging").iterdir())


def test_existing_recovery_record_rejects_live_lock_tampering(output_root, monkeypatch):
    relative, absolute = output_root

    def fail_publish(_staging, _target):
        raise OSError("injected terminal publish failure")

    monkeypatch.setattr(runtime_runner, "atomic_publish", fail_publish)
    with pytest.raises(OSError, match="terminal publish failure"):
        run_experiment(CONFIG, relative, ROOT)
    monkeypatch.undo()
    original_remove = runtime_recovery.remove_resource_lock

    def fail_remove(_path):
        raise OSError("injected resource cleanup failure")

    monkeypatch.setattr(runtime_recovery, "remove_resource_lock", fail_remove)
    run_id = next(row.name for row in (absolute / "staging").iterdir() if row.is_dir())
    with pytest.raises(OSError, match="resource cleanup failure"):
        recover_interrupted_run(absolute, run_id)
    recovery_dir = next(row for row in (absolute / "runs").iterdir() if row.is_dir())
    assert (recovery_dir / "recovery.json").is_file()
    monkeypatch.setattr(runtime_recovery, "remove_resource_lock", original_remove)

    run_lock = absolute / "locks" / f"{run_id}.lock"
    original_lock = run_lock.read_bytes()
    payload = json.loads(original_lock)
    payload["process_id"] += 1
    run_lock.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ValueError, match="published recovery record"):
        recover_interrupted_run(absolute, run_id)
    assert list((absolute / "resource-locks").glob("*.lock"))

    run_lock.write_bytes(original_lock)
    recovered = recover_interrupted_run(absolute, run_id)
    assert recovered.run_dir == recovery_dir
    assert not list((absolute / "resource-locks").glob("*.lock"))


def test_admission_failure_creates_no_output(output_root):
    relative, absolute = output_root
    bad = ROOT / "tmp" / f"stage6_bad_{uuid.uuid4().hex}.yaml"
    payload = CONFIG.read_text(encoding="utf-8").replace("program: null", "program: {}")
    bad.parent.mkdir(exist_ok=True)
    bad.write_text(payload, encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="program must be null"):
            run_experiment(bad, relative, ROOT)
        assert not absolute.exists()
    finally:
        bad.unlink(missing_ok=True)


def test_cooperative_cancellation_publishes_cancelled_record(output_root, monkeypatch):
    relative, absolute = output_root
    entered = threading.Event()
    original = DeterministicFakeBackend.execute_point

    def delayed(self, command, context):
        entered.set()
        time.sleep(0.08)
        return original(self, command, context)

    monkeypatch.setattr(DeterministicFakeBackend, "execute_point", delayed)
    result_box = {}

    def execute():
        result_box["result"] = run_experiment(CONFIG, relative, ROOT)

    thread = threading.Thread(target=execute)
    thread.start()
    assert entered.wait(timeout=2.0)
    run_locks = list((absolute / "locks").glob("*.lock"))
    assert len(run_locks) == 1
    request_run_cancellation(absolute, run_locks[0].stem)
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    result = result_box["result"]
    assert result.status == "cancelled"
    assert not (result.run_dir / "data").exists()
    assert verify_experiment_run(result.run_dir, ROOT).ok


def test_exclusive_resource_lock_rejects_concurrent_run(output_root, monkeypatch):
    relative, _absolute = output_root
    entered = threading.Event()
    release = threading.Event()
    original = DeterministicFakeBackend.execute_point

    def blocked(self, command, context):
        entered.set()
        assert release.wait(timeout=5.0)
        return original(self, command, context)

    monkeypatch.setattr(DeterministicFakeBackend, "execute_point", blocked)
    result_box = {}

    def execute():
        result_box["result"] = run_experiment(CONFIG, relative, ROOT)

    thread = threading.Thread(target=execute)
    thread.start()
    assert entered.wait(timeout=2.0)
    with pytest.raises(FileExistsError):
        run_experiment(CONFIG, relative, ROOT)
    release.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert result_box["result"].status == "completed"


def test_two_runs_have_identical_deterministic_payloads(output_root):
    relative, absolute = output_root
    first = run_experiment(CONFIG, relative, ROOT)
    second = run_experiment(CONFIG, relative, ROOT)
    for name in ("request.json", "point_table.json", "snapshots/source.json", "snapshots/environment.json", "data/dataset.json", "data/response.bin"):
        assert (first.run_dir / name).read_bytes() == (second.run_dir / name).read_bytes()
    rebuilt = rebuild_run_catalog(absolute)
    assert rebuilt.ok and rebuilt.indexed_runs == 2
