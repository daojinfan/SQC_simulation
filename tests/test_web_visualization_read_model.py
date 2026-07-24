from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

import sqvm.web.index as index_module
from sqvm.calibration import SPECTROSCOPY_SCAN_WORKFLOW_ID
from sqvm.web.index import CalibrationWebIndex, WebArtifactError


SUMMARY_KEYS = {
    "run_id",
    "workflow_id",
    "experiment_kind",
    "status",
    "created_utc",
    "data_origin",
    "verification_status",
    "targets",
    "execution_mode",
    "recommendation_applicable",
    "recommendation_eligible",
    "parent_calibration",
    "gate_summary",
    "candidate_summary",
    "relative_path",
    "error",
    "storage_state",
    "carrier_alias",
}


def _workflow(
    run_id: str,
    *,
    created_utc: str = "2026-07-22T00:00:00Z",
    artifact_version: str = "0.2",
) -> dict:
    return {
        "schema_version": "0.1",
        "artifact_version": artifact_version,
        "run_id": run_id,
        "workflow_id": SPECTROSCOPY_SCAN_WORKFLOW_ID,
        "status": "completed",
        "created_utc": created_utc,
        "claim": {"evidence_class": "synthetic-test"},
        "request": {"targets": ["Q1"], "execution_mode": "simulation"},
        "analysis": {},
        "gates": [],
        "candidates": [],
        "recommendation_eligible": False,
    }


def _dataset() -> dict:
    return {
        "points": [
            {
                "point": {
                    "point_index": 0,
                    "circuit_id": "p0",
                    "coordinates_GHz": {"Q1": 5.0},
                },
                "primitive_dressed_populations": {
                    "population_000": 0.8,
                    "population_100": 0.2,
                    "population_001": 0.0,
                    "population_101": 0.0,
                },
                "leakage": 0.0,
                "norm_error": 0.0,
            }
        ]
    }


def _publish(
    output_root: Path,
    run_id: str,
    *,
    created_utc: str = "2026-07-22T00:00:00Z",
    artifact_version: str = "0.2",
) -> Path:
    run = output_root / "experiments" / run_id
    run.mkdir(parents=True)
    workflow_path = run / "workflow.json"
    dataset_path = run / "dataset.json"
    workflow = _workflow(
        run_id, created_utc=created_utc, artifact_version=artifact_version
    )
    dataset_raw = json.dumps(_dataset()).encode("utf-8")
    dataset_sha256 = hashlib.sha256(dataset_raw).hexdigest().upper()
    workflow["dataset"] = {"path": "dataset.json", "sha256": dataset_sha256}
    workflow_raw = json.dumps(workflow).encode("utf-8")
    workflow_path.write_bytes(workflow_raw)
    dataset_path.write_bytes(dataset_raw)
    receipt = {
        "schema_version": "0.1",
        "artifact_type": "qubit_spectroscopy_scan_receipt",
        "artifact_version": artifact_version,
        "run_id": run_id,
        "status": "completed",
        "workflow_sha256": hashlib.sha256(workflow_raw).hexdigest().upper(),
        "dataset_sha256": dataset_sha256,
    }
    if artifact_version == "0.3":
        manifest = {
            "schema_version": "0.1",
            "artifact_type": "stage_07_qubit_spectroscopy_scan_manifest",
            "artifact_version": "0.3",
            "run_id": run_id,
            "workflow_id": SPECTROSCOPY_SCAN_WORKFLOW_ID,
            "status": "completed",
            "payload_files": [
                {
                    "path": "dataset.json",
                    "raw_sha256": dataset_sha256,
                    "byte_length": len(dataset_raw),
                },
                {
                    "path": "workflow.json",
                    "raw_sha256": receipt["workflow_sha256"],
                    "byte_length": len(workflow_raw),
                },
                {
                    "path": "execution/large-evidence.bin",
                    "raw_sha256": "0" * 64,
                    "byte_length": 8,
                },
            ],
        }
        manifest_raw = json.dumps(manifest).encode("utf-8")
        manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest().upper()
        (run / "manifest.json").write_bytes(manifest_raw)
        report = {
            "schema_version": "0.1",
            "artifact_type": "stage_07_qubit_spectroscopy_scan_verification_report",
            "artifact_version": "0.3",
            "run_id": run_id,
            "status": "completed",
            "manifest_sha256": manifest_sha256,
            "ok": True,
        }
        report_raw = json.dumps(report).encode("utf-8")
        (run / "verification_report.json").write_bytes(report_raw)
        receipt.update(
            {
                "manifest_sha256": manifest_sha256,
                "verification_report_sha256": hashlib.sha256(report_raw)
                .hexdigest()
                .upper(),
            }
        )
    (run / "receipt.json").write_text(json.dumps(receipt), "utf-8")
    execution = run / "execution"
    execution.mkdir()
    (execution / "large-evidence.bin").write_bytes(b"evidence")
    return run


def _rewrite_workflow(run: Path, *, created_utc: str) -> None:
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text("utf-8"))
    workflow["created_utc"] = created_utc
    raw = json.dumps(workflow).encode("utf-8")
    workflow_path.write_bytes(raw)
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["workflow_sha256"] = hashlib.sha256(raw).hexdigest().upper()
    receipt_path.write_text(json.dumps(receipt), "utf-8")


def _rebind_v03_manifest(run: Path, manifest: dict) -> None:
    manifest_raw = json.dumps(manifest).encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_raw).hexdigest().upper()
    (run / "manifest.json").write_bytes(manifest_raw)
    report_path = run / "verification_report.json"
    report = json.loads(report_path.read_text("utf-8"))
    report["manifest_sha256"] = manifest_sha256
    report_raw = json.dumps(report).encode("utf-8")
    report_path.write_bytes(report_raw)
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["manifest_sha256"] = manifest_sha256
    receipt["verification_report_sha256"] = (
        hashlib.sha256(report_raw).hexdigest().upper()
    )
    receipt_path.write_text(json.dumps(receipt), "utf-8")


def _rebind_v03_report(run: Path, report: dict) -> None:
    report_raw = json.dumps(report).encode("utf-8")
    (run / "verification_report.json").write_bytes(report_raw)
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["verification_report_sha256"] = (
        hashlib.sha256(report_raw).hexdigest().upper()
    )
    receipt_path.write_text(json.dumps(receipt), "utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    output = tmp_path / "output"
    output.mkdir()
    calls: list[Path] = []

    original = index_module.CalibrationWebIndex._verify_published_web_projection

    def verify(self, directory, workflow_path, workflow) -> None:
        original(self, directory, workflow_path, workflow)
        calls.append(Path(directory))

    def unexpected_full_verifier(_path: str | Path) -> bool:
        raise AssertionError("Web read model invoked the full evidence verifier")

    monkeypatch.setattr(
        index_module.CalibrationWebIndex, "_verify_published_web_projection", verify
    )
    monkeypatch.setattr(index_module, "verify_qubit_spectroscopy_scan", unexpected_full_verifier)
    monkeypatch.setattr(
        index_module, "verify_qubit_spectroscopy_calibration", unexpected_full_verifier
    )
    return tmp_path, output, calls


def test_repeated_list_and_detail_reuse_verified_summary_and_keep_dto(workspace) -> None:
    repository, output, calls = workspace
    _publish(output, "run-1")
    index = CalibrationWebIndex(repository, output)

    first = index.experiments()
    second = index.experiments()
    detail = index.experiment("run-1")

    assert len(calls) == 1
    assert first == second
    assert set(first[0]) == SUMMARY_KEYS
    assert detail["renderer"] == "qubit_spectroscopy_scan"
    assert detail["datasets"]["scan"]["points"][0]["point"]["point_index"] == 0
    first[0]["targets"].append("CACHE-MUTATION")
    assert index.experiments()[0]["targets"] == ["Q1"]
    assert len(calls) == 1


def test_cache_discovers_addition_evicts_deletion_and_revalidates_identity_change(
    workspace,
) -> None:
    repository, output, calls = workspace
    first_run = _publish(output, "run-1", created_utc="2026-07-22T00:00:00Z")
    index = CalibrationWebIndex(repository, output)
    assert [row["run_id"] for row in index.experiments()] == ["run-1"]

    second_run = _publish(output, "run-2", created_utc="2026-07-22T01:00:00Z")
    index.reconcile_experiments()
    assert [row["run_id"] for row in index.experiments()] == ["run-2", "run-1"]
    assert len(calls) == 3

    for child in first_run.iterdir():
        if child.is_dir():
            for nested in child.iterdir():
                nested.unlink()
            child.rmdir()
        else:
            child.unlink()
    first_run.rmdir()
    index.reconcile_experiments()
    assert [row["run_id"] for row in index.experiments()] == ["run-2"]
    assert len(calls) == 4

    workflow_path = second_run / "workflow.json"
    changed = _workflow("run-2", created_utc="2026-07-22T02:00:00Z")
    dataset_sha256 = hashlib.sha256((second_run / "dataset.json").read_bytes()).hexdigest().upper()
    changed["dataset"] = {"path": "dataset.json", "sha256": dataset_sha256}
    changed_raw = json.dumps(changed).encode("utf-8")
    workflow_path.write_bytes(changed_raw)
    receipt = json.loads((second_run / "receipt.json").read_text("utf-8"))
    receipt["workflow_sha256"] = hashlib.sha256(changed_raw).hexdigest().upper()
    (second_run / "receipt.json").write_text(json.dumps(receipt), "utf-8")
    stat = workflow_path.stat()
    os.utime(workflow_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    index.reconcile_experiments()
    assert index.experiments()[0]["created_utc"] == "2026-07-22T02:00:00Z"
    assert len(calls) == 5


def test_overview_and_configuration_discovery_never_scandir_execution(
    workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, output, calls = workspace
    _publish(output, "run-1")
    index = CalibrationWebIndex(repository, output)
    assert index.experiments()[0]["run_id"] == "run-1"
    assert len(calls) == 1

    real_scandir = index_module.os.scandir

    def guarded_scandir(path):
        if Path(path).name == "execution":
            raise AssertionError("Web read model entered execution evidence")
        return real_scandir(path)

    monkeypatch.setattr(index_module.os, "scandir", guarded_scandir)
    overview = index.overview()
    assert overview["experiments"]["total"] == 1
    assert len(calls) == 1


def test_v03_cold_projection_binds_manifest_without_entering_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    _publish(output, "run-v03", artifact_version="0.3")
    real_scandir = index_module.os.scandir

    def guarded_scandir(path):
        if Path(path).name == "execution":
            raise AssertionError("v0.3 projection entered execution evidence")
        return real_scandir(path)

    monkeypatch.setattr(index_module.os, "scandir", guarded_scandir)
    rows = CalibrationWebIndex(tmp_path, output).experiments()

    assert rows[0]["run_id"] == "run-v03"
    assert rows[0]["verification_status"] == "verified"


@pytest.mark.parametrize("artifact_version", ("0.0", "0.4", "1.0"))
def test_known_workflow_unknown_artifact_version_fails_closed(
    tmp_path: Path, artifact_version: str
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    _publish(output, "run-unknown", artifact_version=artifact_version)

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["verification_status"] == "invalid"
    assert "artifact_version is unsupported" in row["error"]


def test_calibration_workflow_rejects_non_v01_artifact(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-calibration", artifact_version="0.2")
    workflow_path = run / "workflow.json"
    workflow = json.loads(workflow_path.read_text("utf-8"))
    workflow["workflow_id"] = index_module.SPECTROSCOPY_WORKFLOW_ID
    raw = json.dumps(workflow).encode("utf-8")
    workflow_path.write_bytes(raw)
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["workflow_sha256"] = hashlib.sha256(raw).hexdigest().upper()
    receipt_path.write_text(json.dumps(receipt), "utf-8")

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["verification_status"] == "invalid"
    assert "artifact_version is unsupported" in row["error"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("schema_version", "0.2"),
        ("artifact_type", "rebound_manifest"),
        ("artifact_version", "0.2"),
        ("run_id", "other-run"),
        ("workflow_id", "other_workflow"),
        ("status", "failed"),
    ),
)
def test_v03_manifest_identity_rejects_rebound_tampering(
    tmp_path: Path, field: str, bad_value: str
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-v03", artifact_version="0.3")
    manifest = json.loads((run / "manifest.json").read_text("utf-8"))
    manifest[field] = bad_value
    _rebind_v03_manifest(run, manifest)

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["verification_status"] == "invalid"
    assert "manifest identity" in row["error"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("path", "workflow-renamed.json"),
        ("raw_sha256", "0" * 64),
        ("byte_length", 1),
    ),
)
def test_v03_manifest_payload_binding_rejects_rebound_tampering(
    tmp_path: Path, field: str, bad_value
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-v03", artifact_version="0.3")
    manifest = json.loads((run / "manifest.json").read_text("utf-8"))
    workflow_row = next(
        row for row in manifest["payload_files"] if row["path"] == "workflow.json"
    )
    workflow_row[field] = bad_value
    _rebind_v03_manifest(run, manifest)

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["verification_status"] == "invalid"
    assert "manifest" in row["error"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("schema_version", "0.2"),
        ("artifact_type", "rebound_report"),
        ("manifest_sha256", "0" * 64),
        ("run_id", "other-run"),
        ("status", "failed"),
        ("artifact_version", "0.2"),
    ),
)
def test_v03_verification_report_binding_rejects_rebound_tampering(
    tmp_path: Path, field: str, bad_value: str
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-v03", artifact_version="0.3")
    report = json.loads((run / "verification_report.json").read_text("utf-8"))
    report[field] = bad_value
    _rebind_v03_report(run, report)

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["verification_status"] == "invalid"
    assert "verification report" in row["error"]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("schema_version", "0.2"),
        ("artifact_type", "rebound_receipt"),
        ("artifact_version", "0.2"),
        ("run_id", "other-run"),
        ("status", "failed"),
    ),
)
def test_v03_receipt_identity_rejects_tampering(
    tmp_path: Path, field: str, bad_value: str
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-v03", artifact_version="0.3")
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt[field] = bad_value
    receipt_path.write_text(json.dumps(receipt), "utf-8")

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["verification_status"] == "invalid"
    assert "receipt identity" in row["error"]


def test_v02_optional_manifest_and_report_validate_only_present_fields(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-v02", artifact_version="0.2")
    manifest_raw = json.dumps({"payload_files": []}).encode("utf-8")
    (run / "manifest.json").write_bytes(manifest_raw)
    report_raw = json.dumps({"ok": True}).encode("utf-8")
    (run / "verification_report.json").write_bytes(report_raw)
    receipt_path = run / "receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["manifest_sha256"] = hashlib.sha256(manifest_raw).hexdigest().upper()
    receipt["verification_report_sha256"] = (
        hashlib.sha256(report_raw).hexdigest().upper()
    )
    receipt_path.write_text(json.dumps(receipt), "utf-8")

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert row["run_id"] == "run-v02"
    assert row["verification_status"] == "verified"


def test_concurrent_readers_share_one_thread_safe_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    _publish(output, "run-1")
    lock = threading.Lock()
    calls = 0

    original = index_module.CalibrationWebIndex._verify_published_web_projection

    def verify(self, directory, workflow_path, workflow) -> None:
        nonlocal calls
        original(self, directory, workflow_path, workflow)
        with lock:
            calls += 1

    monkeypatch.setattr(
        index_module.CalibrationWebIndex, "_verify_published_web_projection", verify
    )
    index = CalibrationWebIndex(tmp_path, output)
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _value: index.experiments(), range(48)))

    assert calls == 1
    assert all(rows[0]["run_id"] == "run-1" for rows in results)


def test_projection_change_during_read_retries_without_caching_stale_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-1", created_utc="2026-07-22T00:00:00Z")
    original = index_module.CalibrationWebIndex._verify_published_web_projection
    calls = 0

    def verify_and_publish_change(self, directory, workflow_path, workflow) -> None:
        nonlocal calls
        calls += 1
        original(self, directory, workflow_path, workflow)
        if calls != 1:
            return
        changed = dict(workflow)
        changed["created_utc"] = "2026-07-22T03:00:00Z"
        changed_raw = json.dumps(changed).encode("utf-8")
        workflow_path.write_bytes(changed_raw)
        receipt_path = directory / "receipt.json"
        receipt = json.loads(receipt_path.read_text("utf-8"))
        receipt["workflow_sha256"] = hashlib.sha256(changed_raw).hexdigest().upper()
        receipt_path.write_text(json.dumps(receipt), "utf-8")

    monkeypatch.setattr(
        index_module.CalibrationWebIndex,
        "_verify_published_web_projection",
        verify_and_publish_change,
    )
    index = CalibrationWebIndex(tmp_path, output)

    assert index.experiments()[0]["created_utc"] == "2026-07-22T03:00:00Z"
    assert calls == 2
    assert index.experiments()[0]["created_utc"] == "2026-07-22T03:00:00Z"
    assert calls == 2
    assert (run / "workflow.json").is_file()


def test_projection_that_changes_twice_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-1")
    original = index_module.CalibrationWebIndex._verify_published_web_projection
    calls = 0

    def verify_and_keep_changing(self, directory, workflow_path, workflow) -> None:
        nonlocal calls
        calls += 1
        original(self, directory, workflow_path, workflow)
        _rewrite_workflow(run, created_utc=f"2026-07-22T0{calls}:00:00Z")

    monkeypatch.setattr(
        index_module.CalibrationWebIndex,
        "_verify_published_web_projection",
        verify_and_keep_changing,
    )

    row = CalibrationWebIndex(tmp_path, output).experiments()[0]

    assert calls == 2
    assert row["verification_status"] == "invalid"
    assert "changed repeatedly" in row["error"]


def test_detail_reads_persistent_projection_without_filesystem_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    run = _publish(output, "run-1")
    index = CalibrationWebIndex(tmp_path, output)
    assert index.experiments()[0]["verification_status"] == "verified"
    monkeypatch.setattr(
        index,
        "_read_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detail GET read the filesystem")
        ),
    )
    monkeypatch.setattr(
        index_module.os,
        "scandir",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("detail GET scanned the filesystem")
        ),
    )

    assert index.experiment("run-1")["renderer"] == "qubit_spectroscopy_scan"
    assert (run / "execution").is_dir()


def test_json_reader_rejects_duplicate_keys_depth_and_size_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = CalibrationWebIndex(tmp_path, tmp_path)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"value": 1, "value": 2}', "utf-8")
    with pytest.raises(WebArtifactError, match="duplicate JSON key"):
        index._read_json(duplicate, "duplicate fixture")

    deep = tmp_path / "deep.json"
    value = "0"
    for _index in range(index_module._MAX_JSON_DEPTH + 1):
        value = '{"child":' + value + "}"
    deep.write_text(value, "utf-8")
    with pytest.raises(WebArtifactError, match="maximum JSON depth"):
        index._read_json(deep, "deep fixture")

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b'{"value":"0123456789"}')
    monkeypatch.setattr(index_module, "_MAX_JSON_BYTES", 8)
    with pytest.raises(WebArtifactError, match="exceeds 8 bytes"):
        index._read_json(oversized, "oversized fixture")
    monkeypatch.setattr(index_module, "_MAX_HASH_BYTES", 8)
    with pytest.raises(WebArtifactError, match="exceeds 8 bytes"):
        index_module._raw_sha256(oversized)


def test_custom_output_root_without_experiments_container_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = tmp_path / "custom-runs"
    collection.mkdir()
    run = collection / "run-direct"
    run.mkdir()
    dataset_raw = json.dumps(_dataset()).encode("utf-8")
    dataset_sha256 = hashlib.sha256(dataset_raw).hexdigest().upper()
    workflow = _workflow("run-direct")
    workflow["dataset"] = {"path": "dataset.json", "sha256": dataset_sha256}
    workflow_raw = json.dumps(workflow).encode("utf-8")
    (run / "workflow.json").write_bytes(workflow_raw)
    (run / "dataset.json").write_bytes(dataset_raw)
    (run / "receipt.json").write_text(json.dumps({
        "run_id": "run-direct", "status": "completed",
        "workflow_sha256": hashlib.sha256(workflow_raw).hexdigest().upper(),
        "dataset_sha256": dataset_sha256,
    }), "utf-8")

    rows = CalibrationWebIndex(tmp_path, collection).experiments()

    assert [row["run_id"] for row in rows] == ["run-direct"]
