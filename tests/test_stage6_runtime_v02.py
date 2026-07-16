from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid

import pytest
import yaml

import sqvm.runtime_v02.runner as v02_runner
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime_v02 import (
    ExperimentRequestV02,
    BACKEND_ID as V02_BACKEND_ID,
    CLAIM_ENVELOPE as V02_CLAIM_ENVELOPE,
    EXPERIMENT_ID as V02_EXPERIMENT_ID,
    compile_point_v02,
    expand_scan_v02,
    get_runtime_schema_registry,
)
from sqvm.runtime_api import (
    load_experiment_request_versioned as load_experiment_request,
    rebuild_run_catalog_versioned as rebuild_run_catalog,
    recover_interrupted_run_versioned as recover_interrupted_run,
    recover_terminal_resource_lock_versioned,
    run_experiment_versioned as run_experiment,
    verify_experiment_run_versioned as verify_experiment_run,
)
from sqvm.runtime.models import frozen_mapping
from sqvm.runtime.journal import canonical_json_line_bytes
from sqvm.runtime.provenance import build_source_snapshot
from sqvm.runtime.storage import inventory_tree
from sqvm.runtime_v02.core import RESULT_SCHEMA, load_compiler_fixture_authority, validate_dataset_v02, write_dataset_v02


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/experiments/platform_qcis_compile_smoke_v1.yaml"


@pytest.fixture
def output_root():
    relative = Path("tmp") / f"stage6_v02_{uuid.uuid4().hex}"
    absolute = ROOT / relative
    try:
        yield relative, absolute
    finally:
        if absolute.exists():
            shutil.rmtree(absolute)


def _tamper_canonical(path: Path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_bytes(canonical_json_bytes(payload))


def _rebind_terminal(run_dir: Path) -> None:
    manifest_path = run_dir / "manifest.json"
    report_path = run_dir / "verification_report.json"
    receipt_path = run_dir / "receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    manifest["events_sha256"] = hashlib.sha256((run_dir / "events.jsonl").read_bytes()).hexdigest().upper()
    manifest["event_tail_sha256"] = events[-1]["event_sha256"]
    manifest["payload_files"] = [
        row for row in inventory_tree(run_dir)
        if row["path"] not in {"manifest.json", "verification_report.json", "receipt.json"}
    ]
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    report_path.write_bytes(canonical_json_bytes(report))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256"] = report["manifest_sha256"]
    receipt["verification_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest().upper()
    receipt["event_tail_sha256"] = events[-1]["event_sha256"]
    receipt_path.write_bytes(canonical_json_bytes(receipt))


def test_v02_public_loader_is_exact_and_keeps_v01_separate(tmp_path):
    assert get_runtime_schema_registry().versions() == ("0.1", "0.2")
    assert build_source_snapshot(ROOT)["aggregate_sha256"] == "4AD30147E0044275320DDB04CD8FD2D5D1D5D7AFCB0C03AF2A253B256737BFEE"
    request = load_experiment_request(CONFIG, ROOT)
    assert isinstance(request, ExperimentRequestV02)
    assert request.schema_version == "0.2"
    assert request.experiment_id == V02_EXPERIMENT_ID
    assert request.backend_id == V02_BACKEND_ID
    assert request.program["program_schema_version"] == "0.3"

    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    raw["schema_version"] = "0.1"
    path = ROOT / "tmp" / f"bad_cross_version_{uuid.uuid4().hex}.yaml"
    path.parent.mkdir(exist_ok=True)
    try:
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with pytest.raises(ValueError, match="program must be null"):
            load_experiment_request(path, ROOT)
        raw["schema_version"] = "9.9"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version"):
            run_experiment(path, "tmp/never_created_v02", ROOT)
        assert not (ROOT / "tmp/never_created_v02").exists()
    finally:
        path.unlink(missing_ok=True)


def test_v02_authority_point_identity_and_compiler_evidence_are_deterministic():
    authority, authority_sha = load_compiler_fixture_authority(ROOT)
    assert authority["authority_id"] == "2B255F518ABA1A0949965CB5E53A7AE6B19BD619A8F1739D9A6D2B1458E34EB5"
    request = load_experiment_request(CONFIG, ROOT)
    assert request.authority_sha256 == authority_sha
    first = expand_scan_v02(request)
    second = expand_scan_v02(request)
    assert first == second and len(first) == 2
    assert first[0].point_id != first[1].point_id

    changed_program = dict(request.program)
    changed_program["template_sha256"] = "A" * 64
    changed = replace(request, program=frozen_mapping(changed_program))
    assert expand_scan_v02(changed)[0].point_id != first[0].point_id

    compiled_a = compile_point_v02(request, first[0])
    compiled_b = compile_point_v02(request, first[0])
    assert compiled_a.concrete_source == b"PLSXY Q1 0 -1 2 1 5.2 0 0 2\n"
    assert compiled_a.ast_bytes == compiled_b.ast_bytes
    assert compiled_a.trace_bytes == compiled_b.trace_bytes
    assert compiled_a.result == compiled_b.result
    assert tuple(compiled_a.result["values"]) == tuple(RESULT_SCHEMA)
    forged = replace(first[0], point_id="F" * 64)
    with pytest.raises(ValueError, match="not canonical"):
        compile_point_v02(request, forged)


@pytest.mark.parametrize("relative", [
    "configs/runtime/stage6v02/compiler_fixture_authority_v1.json",
    "configs/runtime/stage6v02/compiler_fixture_approval_v1.json",
])
def test_v02_authority_and_external_approval_are_raw_hash_anchored(relative):
    path = ROOT / relative
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError):
            load_compiler_fixture_authority(ROOT)
    finally:
        path.write_bytes(original)


def test_v02_multi_variable_dataset_rejects_binary_and_order_tampering(tmp_path):
    request = load_experiment_request(CONFIG, ROOT)
    points = expand_scan_v02(request)
    results = [compile_point_v02(request, point).result for point in points]
    point_sha = "B" * 64
    data = tmp_path / "data"
    payload = write_dataset_v02(data, results, point_sha)
    assert payload["variable_order"] == list(RESULT_SCHEMA)
    validate_dataset_v02(data, 2, point_sha)

    binary = data / "trace_byte_count.bin"
    raw = bytearray(binary.read_bytes())
    raw[0] ^= 1
    binary.write_bytes(raw)
    with pytest.raises(ValueError, match="trace_byte_count"):
        validate_dataset_v02(data, 2, point_sha)


def test_v02_public_run_replay_catalog_and_claim_boundary(output_root):
    relative, absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    assert result.status == "completed" and result.catalog_indexed and result.warning is None
    report = verify_experiment_run(result.run_dir, ROOT)
    assert report.ok and report.status == "completed"
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["claim_envelope"] == V02_CLAIM_ENVELOPE
    assert manifest["backend_id"] == "qcis_compiler_only_v1"
    assert set((result.run_dir / "data").iterdir()) == {
        result.run_dir / "data/dataset.json",
        *(result.run_dir / f"data/{name}.bin" for name in RESULT_SCHEMA),
    }
    rebuilt = rebuild_run_catalog(absolute)
    assert rebuilt.ok and rebuilt.indexed_runs == 1 and rebuilt.catalog_path.name == "catalog_v02.sqlite"
    connection = sqlite3.connect(rebuilt.catalog_path)
    try:
        assert connection.execute("SELECT runtime_schema,evidence_class FROM runs").fetchone() == ("0.2", "compiler_test_fixture")
    finally:
        connection.close()


def test_v02_failed_run_removes_partial_point_evidence_and_replays(output_root, monkeypatch):
    relative, _absolute = output_root
    original = v02_runner.compile_point_v02
    calls = 0

    def fail_second(request, point):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("injected compiler failure")
        return original(request, point)

    monkeypatch.setattr(v02_runner, "compile_point_v02", fail_second)
    result = run_experiment(CONFIG, relative, ROOT)
    assert result.status == "failed"
    assert not (result.run_dir / "points").exists()
    assert not (result.run_dir / "data").exists()
    assert verify_experiment_run(result.run_dir, ROOT).ok


@pytest.mark.parametrize("target", ["trace", "result", "claim", "dataset", "event_binding"])
def test_v02_verifier_rejects_replay_and_claim_tampering(output_root, target):
    relative, _absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    run_dir = result.run_dir
    point_id = next(iter(json.loads((run_dir / "point_table.json").read_text())["points"]))["point_id"]
    if target == "trace":
        path = run_dir / f"points/{point_id}/trace.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif target == "result":
        _tamper_canonical(run_dir / f"points/{point_id}/result.json", lambda payload: payload["values"].__setitem__("trace_byte_count", 1))
    elif target == "claim":
        _tamper_canonical(run_dir / "manifest.json", lambda payload: payload["claim_envelope"].__setitem__("physics_claim", "spectroscopy"))
    elif target == "dataset":
        path = run_dir / "data/logical_sample_count.bin"
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
    else:
        events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        completed = next(event for event in events if event["event_type"] == "point_completed")
        completed["payload"]["result_sha256"] = "A" * 64
        previous = None
        raw_lines = []
        for event in events:
            event["prev_event_sha256"] = previous
            base = {key: value for key, value in event.items() if key != "event_sha256"}
            event["event_sha256"] = hashlib.sha256(canonical_json_line_bytes(base)).hexdigest().upper()
            previous = event["event_sha256"]
            raw_lines.append(canonical_json_line_bytes(event))
        (run_dir / "events.jsonl").write_bytes(b"".join(raw_lines))
        _rebind_terminal(run_dir)
    report = verify_experiment_run(run_dir, ROOT)
    assert not report.ok


def test_v02_interrupted_atomic_publish_uses_versioned_no_resume_recovery(output_root, monkeypatch):
    relative, absolute = output_root
    original = v02_runner.atomic_publish

    def fail_publish(_staging, _target):
        raise OSError("injected v02 publication failure")

    monkeypatch.setattr(v02_runner, "atomic_publish", fail_publish)
    with pytest.raises(OSError, match="publication failure"):
        run_experiment(CONFIG, relative, ROOT)
    staging = [path for path in (absolute / "staging").iterdir() if path.is_dir()]
    assert len(staging) == 1
    run_id = staging[0].name
    monkeypatch.setattr(v02_runner, "atomic_publish", original)
    recovered = recover_interrupted_run(absolute, run_id)
    assert recovered.status == "interrupted"
    recovery_payload = json.loads((recovered.run_dir / "recovery.json").read_text(encoding="utf-8"))
    assert recovery_payload["reason"].startswith("runtime_v02|schema=0.2|authority_id=")
    assert not (absolute / "staging" / run_id).exists()
    assert (absolute / "quarantine" / run_id).is_dir()


def test_v02_unreadable_recovery_request_is_quarantined_without_v01_fallback(output_root, monkeypatch):
    relative, absolute = output_root
    monkeypatch.setattr(v02_runner, "atomic_publish", lambda _staging, _target: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError, match="stop"):
        run_experiment(CONFIG, relative, ROOT)
    staging = next(path for path in (absolute / "staging").iterdir() if path.is_dir())
    (staging / "request.json").write_bytes((staging / "request.json").read_bytes() + b" ")
    with pytest.raises(ValueError, match="quarantined"):
        recover_interrupted_run(absolute, staging.name)
    assert not staging.exists()
    assert (absolute / "quarantine" / staging.name).is_dir()
    record = json.loads((absolute / f"quarantine-records/{staging.name}.json").read_text(encoding="utf-8"))
    assert "canonical" in record["reason"]


def test_v02_request_and_run_verification_reject_links_before_read(output_root):
    relative, absolute = output_root
    request_link = ROOT / "tmp" / f"v02_request_link_{uuid.uuid4().hex}.yaml"
    request_link.parent.mkdir(exist_ok=True)
    try:
        request_link.symlink_to(CONFIG)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    try:
        with pytest.raises(ValueError, match="link"):
            load_experiment_request(request_link, ROOT)
    finally:
        request_link.unlink(missing_ok=True)

    result = run_experiment(CONFIG, relative, ROOT)
    run_link = absolute / "linked_run"
    try:
        run_link.symlink_to(result.run_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")
    assert not verify_experiment_run(run_link, ROOT).ok

    linked_run_id = str(uuid.uuid4())
    terminal_link = absolute / "runs" / linked_run_id
    try:
        terminal_link.symlink_to(result.run_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"terminal directory symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="cannot be determined"):
        recover_terminal_resource_lock_versioned(absolute, linked_run_id)

    point_id = json.loads((result.run_dir / "point_table.json").read_text())["points"][0]["point_id"]
    trace = result.run_dir / f"points/{point_id}/trace.json"
    backup = trace.with_suffix(".backup")
    trace.rename(backup)
    try:
        trace.symlink_to(backup.name)
    except OSError as exc:
        backup.rename(trace)
        pytest.skip(f"file symlink creation unavailable: {exc}")
    assert not verify_experiment_run(result.run_dir, ROOT).ok
