from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from pathlib import Path
import json
from types import SimpleNamespace
import threading
import time

import sqvm.storage.catalog as catalog_module
import pytest

import sqvm.web.server as server_module
from sqvm.web.server import ExperimentStorageWebService, StorageWebError


def _service(tmp_path: Path) -> ExperimentStorageWebService:
    hot = tmp_path / "hot"
    storage = tmp_path / "storage"
    configuration = tmp_path / "configuration"
    hot.mkdir()
    configuration.mkdir()
    return ExperimentStorageWebService(
        hot_root=hot,
        storage_root=storage,
        configuration_root=configuration,
        experiment_output_root=hot,
    )


def test_storage_catalog_rebuilds_only_when_a_published_carrier_changes(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first = service.overview()
    second = service.overview()

    assert second["catalog_revision"] == first["catalog_revision"]

    original_rebuild = service.rebuild
    refresh_started = threading.Event()
    allow_refresh = threading.Event()

    def delayed_rebuild() -> int:
        refresh_started.set()
        assert allow_refresh.wait(5.0)
        return original_rebuild()

    service.rebuild = delayed_rebuild  # type: ignore[method-assign]
    published = service.hot_root / "published-run"
    published.mkdir()
    started = time.monotonic()
    pending = service.overview()
    assert time.monotonic() - started < 0.5
    assert pending["catalog_revision"] == second["catalog_revision"]
    assert pending["refreshing"] is True
    assert refresh_started.wait(1.0)
    allow_refresh.set()

    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        refreshed = service.overview()
        if not refreshed["refreshing"]:
            break
        time.sleep(0.01)
    else:
        raise AssertionError("catalog refresh did not finish")

    assert refreshed["catalog_revision"] > second["catalog_revision"]
    assert service.overview()["catalog_revision"] == refreshed["catalog_revision"]


def test_storage_catalog_token_ignores_running_staging_directories(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first = service.overview()

    staging = service.hot_root / ".spectroscopy_running"
    staging.mkdir()
    (staging / "partial.bin").write_bytes(b"partial")

    second = service.overview()

    assert second["catalog_revision"] == first["catalog_revision"]


def test_storage_bootstrap_creates_reference_pin_root_before_warmup(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    service.bootstrap_roots()

    assert (service.storage_root / "pins").is_dir()


def test_reference_token_tracks_contract_files_without_hashing_payload_churn(
    tmp_path: Path,
) -> None:
    configuration = tmp_path / "configuration"
    experiments = tmp_path / "experiments"
    pins = tmp_path / "pins"
    run = experiments / "published-run"
    for directory in (configuration, run, pins):
        directory.mkdir(parents=True)
    workflow = run / "workflow.json"
    workflow.write_text('{"revision":1}', encoding="utf-8")
    payload = run / "result.bin"
    payload.write_bytes(b"first")

    baseline = server_module._reference_authority_token(
        configuration, experiments, pins
    )
    payload.write_bytes(b"second")
    assert server_module._reference_authority_token(
        configuration, experiments, pins
    ) == baseline

    workflow.write_text('{"revision":2}', encoding="utf-8")
    workflow_changed = server_module._reference_authority_token(
        configuration, experiments, pins
    )
    assert workflow_changed != baseline

    (run / "decision.json").write_text('{"decision":"accept"}', encoding="utf-8")
    assert server_module._reference_authority_token(
        configuration, experiments, pins
    ) != workflow_changed


def test_failed_background_refresh_is_reported_and_backed_off(tmp_path: Path) -> None:
    service = _service(tmp_path)
    baseline = service.overview()
    attempts = 0
    attempted = threading.Event()

    def failed_rebuild() -> int:
        nonlocal attempts
        attempts += 1
        attempted.set()
        raise RuntimeError("injected catalog refresh failure")

    service.rebuild = failed_rebuild  # type: ignore[method-assign]
    (service.hot_root / "published-run").mkdir()
    started = time.monotonic()
    service.overview()
    assert time.monotonic() - started < 0.5
    assert attempted.wait(1.0)

    deadline = time.monotonic() + 2.0
    while service._catalog_refresh_thread is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    failed = service.overview()
    repeated = service.overview()

    assert failed["catalog_revision"] == baseline["catalog_revision"]
    assert failed["refreshing"] is False
    assert failed["refresh_error"] is True
    assert repeated["refresh_error"] is True
    assert attempts == 1


def test_catalog_change_during_rebuild_requires_another_reconciliation(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    service.bootstrap_roots()
    tokens = iter(
        (
            (("hot", "run-a", 0, 0, 1),),
            (("hot", "run-a", 0, 0, 1), ("hot", "run-b", 0, 0, 2)),
        )
    )
    monkeypatch.setattr(service, "_source_token", lambda: next(tokens))
    monkeypatch.setattr(catalog_module, "rebuild_catalog", lambda *_args, **_kwargs: 7)

    assert service.rebuild() == 7
    assert service._catalog_source_token is None
    assert service._catalog_reconcile_required is True


def test_storage_mutation_is_rejected_while_catalog_refreshes(tmp_path: Path) -> None:
    service = _service(tmp_path)
    release = threading.Event()
    refresh = threading.Thread(target=lambda: release.wait(5.0), daemon=True)
    refresh.start()
    service._catalog_refresh_thread = refresh
    payload = {
        "actor_id": "project.manager",
        "expected_catalog_revision": 0,
        "expected_workflow_sha256": "A" * 64,
        "reason": "test catalog isolation",
    }

    try:
        with pytest.raises(StorageWebError) as captured:
            service.mutate("run-1", "archive", payload)
        assert captured.value.code == "storage_catalog_refreshing"
        assert captured.value.status == 409
        assert captured.value.details["retryable"] is True
    finally:
        release.set()
        refresh.join(timeout=1.0)


def test_storage_mutation_is_rejected_during_refresh_failure_backoff(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service._catalog_refresh_error = True
    service._catalog_refresh_retry_after = time.monotonic() + 30.0
    payload = {
        "actor_id": "project.manager",
        "expected_catalog_revision": 0,
        "expected_workflow_sha256": "A" * 64,
        "reason": "test catalog failure isolation",
    }

    with pytest.raises(StorageWebError) as captured:
        service.mutate("run-1", "archive", payload)

    assert captured.value.code == "storage_catalog_unavailable"
    assert captured.value.status == 503
    assert captured.value.details["retryable"] is True


def test_archived_detail_uses_one_verified_reader_and_hot_dto(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _service(tmp_path)
    archive = tmp_path / "run.sqrun"
    archive.write_bytes(b"carrier")
    row_payload = {
        "schema_version": "0.1",
        "run_id": "run-1",
        "workflow_id": "qubit_spectroscopy_scan_v1",
        "workflow_sha256": "A" * 64,
        "storage_state": "archived",
    }
    row = SimpleNamespace(
        run_id="run-1",
        workflow_id="qubit_spectroscopy_scan_v1",
        workflow_sha256="A" * 64,
        archive_path=str(archive),
        created_utc="2026-07-22T00:00:00Z",
        to_dict=lambda: row_payload,
    )
    bundle = SimpleNamespace(
        run_id="run-1", workflow_sha256="A" * 64, entries=()
    )
    workflow = {
        "artifact_version": "0.3",
        "run_id": "run-1",
        "workflow_id": "qubit_spectroscopy_scan_v1",
        "status": "completed",
        "created_utc": "2026-07-22T00:00:00Z",
        "claim": {"evidence_class": "model-derived"},
        "request": {"targets": ["Q1"], "execution_mode": "simulation"},
        "analysis": {},
        "recommendation_eligible": False,
        "candidates": [],
        "gates": [],
    }
    dataset = {"points": []}
    verifier_calls = 0

    def verify(*_args, **_kwargs):
        nonlocal verifier_calls
        verifier_calls += 1
        return bundle

    class Reader:
        def __init__(self, *_args, **_kwargs):
            pass

        def read_bytes(self, entry: str, *, maximum_bytes: int) -> bytes:
            assert maximum_bytes == 2_000_000
            return json.dumps(workflow if entry == "workflow.json" else dataset).encode()

    monkeypatch.setattr(service, "_internal_archive_row", lambda _run_id: row)
    monkeypatch.setattr(server_module, "verify_sqrun", verify)
    monkeypatch.setattr(server_module, "ZipEvidenceReader", Reader)
    monkeypatch.setattr(server_module, "build_spectroscopy_plot_spec", lambda *_args: {"kind": "line"})

    detail = service.experiment_detail("run-1")

    assert verifier_calls == 1
    assert detail["renderer"] == "qubit_spectroscopy_scan"
    assert detail["targets"] == ["Q1"]
    assert detail["execution_mode"] == "simulation"
    assert detail["verification_status"] == "verified"
    assert detail["evidence_paths"] == ["archive:run-1"]
