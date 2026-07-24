from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import threading
import uuid

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
import sqvm.calibration.spectroscopy_run as spectroscopy_run_module
import sqvm.web.index as index_module
from sqvm.calibration.spectroscopy_run import run_qubit_spectroscopy_scan
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.web.registrar import enqueue_published_run
from tests.support.contexts import spectroscopy_context as _context, spectroscopy_result as _result, single_spectroscopy_request as _single_request
from tests.support.web_projection import close_server as _close_server, eventually as _eventually, experiment_page as _experiment_page, publish_generic as _publish_generic, request as _request, start_server as _shared_start_server


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json"


def _start_server(base: Path):
    return _shared_start_server(ROOT, base)


@pytest.fixture
def e2e_base():
    base = ROOT / "tmp" / f"web_projection_e2e_{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_server_coordinator_recovers_inbox_and_stops_cleanly(
    e2e_base: Path,
) -> None:
    server, thread, base_url = _start_server(e2e_base)
    target = _publish_generic(
        e2e_base,
        "eventual-run",
        created_utc="2026-07-22T09:00:00.000000Z",
    )
    failed = threading.Event()
    retry_entered = threading.Event()
    allow_retry = threading.Event()
    original = server.index.project_experiment_path
    attempts = 0

    def transient(relative_path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            failed.set()
            raise OSError("injected transient projection failure")
        if attempts == 2:
            retry_entered.set()
            assert allow_retry.wait(5.0)
        return original(relative_path)

    server.index.project_experiment_path = transient  # type: ignore[method-assign]
    try:
        health = _request(f"{base_url}/api/v1/health")[2]
        assert health["read_model"]["running"] is True

        first = enqueue_published_run(
            ROOT, target, storage_root=server.storage.storage_root
        )
        duplicate = enqueue_published_run(
            ROOT, target, storage_root=server.storage.storage_root
        )
        assert first.created is True and duplicate.created is False
        assert first.path == duplicate.path
        server.coordinator.notify()

        assert failed.wait(5.0)
        assert retry_entered.wait(5.0)
        degraded = server.coordinator.status()
        assert degraded["status"] == "degraded"
        assert degraded["last_error"] == "RuntimeError"
        assert degraded["pending_projection_count"] == 1
        assert first.path.is_file()
        allow_retry.set()

        page = _eventually(
            lambda: _experiment_page(base_url),
            lambda value: [row["run_id"] for row in value["items"]]
            == ["eventual-run"],
        )
        assert page["page"]["total"] == 1
        _eventually(lambda: first.path.exists(), lambda exists: exists is False)
        _eventually(
            server.coordinator.status,
            lambda status: status["status"] == "ok",
        )

        revision = page["read_model_revision"]
        replay = enqueue_published_run(
            ROOT, target, storage_root=server.storage.storage_root
        )
        assert replay.created is True
        server.coordinator.notify()
        _eventually(lambda: replay.path.exists(), lambda exists: exists is False)
        replayed_page = _experiment_page(base_url)
        assert replayed_page["read_model_revision"] == revision
        assert [row["run_id"] for row in replayed_page["items"]] == [
            "eventual-run"
        ]

        malformed = server.storage.storage_root / "index-inbox" / "malformed.json"
        malformed.write_bytes(canonical_json_bytes({}))
        server.coordinator.notify()
        quarantined = server.storage.storage_root / "index-quarantine" / malformed.name
        _eventually(lambda: quarantined.exists(), lambda exists: exists is True)
        assert not malformed.exists()
        assert server.coordinator.status()["status"] == "degraded"
        assert server.coordinator.status()["quarantined_event_count"] == 1
        assert _experiment_page(base_url)["page"]["total"] == 1
    finally:
        allow_retry.set()
        _close_server(server, thread)

    assert server.coordinator.status()["running"] is False


def test_http_pagination_etag_lod_and_db_only_gets(
    e2e_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, thread, base_url = _start_server(e2e_base)
    targets = []
    try:
        for index in range(5):
            target = _publish_generic(
                e2e_base,
                f"page-run-{index}",
                created_utc=f"2026-07-22T0{index}:00:00.000000Z",
                point_count=2_101 if index == 4 else 8,
            )
            targets.append(target)
            enqueue_published_run(
                ROOT, target, storage_root=server.storage.storage_root
            )
        server.coordinator.notify()
        _eventually(
            lambda: _experiment_page(base_url),
            lambda page: page["page"]["total"] == 5,
        )

        status, headers, first = _request(
            f"{base_url}/api/v1/experiments?limit=2"
        )
        etag = headers.get("ETag")
        assert status == 200 and etag
        assert first["page"]["has_more"] is True
        assert len(first["items"]) == 2
        cursor = first["page"]["next_cursor"]
        second = _request(
            f"{base_url}/api/v1/experiments?limit=2&cursor={cursor}"
        )[2]
        first_ids = {row["run_id"] for row in first["items"]}
        second_ids = {row["run_id"] for row in second["items"]}
        assert len(second["items"]) == 2
        assert first_ids.isdisjoint(second_ids)
        conditional = _request(
            f"{base_url}/api/v1/experiments?limit=2",
            headers={"If-None-Match": etag},
        )
        assert conditional[0] == 304 and conditional[2] is None

        run_id = "page-run-4"
        detail_url = f"{base_url}/api/v1/experiments/{run_id}"
        detail_status, detail_headers, detail = _request(detail_url)
        assert detail_status == 200 and detail_headers.get("ETag")
        spec = detail["plot_specs"][0]
        assert spec["series"] == []
        assert spec["data_descriptor"]["source_point_count"] == 2_101
        assert spec["data_url"].endswith("/plots/signal/data")

        data_url = (
            f"{base_url}/api/v1/experiments/{run_id}/plots/signal/data"
            "?max_points=20&x_min=100&x_max=1800"
        )
        data_status, data_headers, envelope = _request(data_url)
        assert data_status == 200 and data_headers.get("ETag")
        assert envelope["method"] == "min_max_envelope_v1"
        assert envelope["x_min"] == 100.0 and envelope["x_max"] == 1800.0
        assert len(envelope["series"][0]["points"]) <= 20
        assert _request(
            data_url, headers={"If-None-Match": data_headers["ETag"]}
        )[0] == 304

        point_url = (
            f"{base_url}/api/v1/experiments/{run_id}/plots/signal/points/point-42"
        )
        point = _request(point_url)[2]
        assert point["point_id"] == "point-42"
        assert point["source_index"] == 42
        assert point["x"] == 42.0
        assert point["point"]["metadata"] == {"source_index": 42}

        original_scandir = os.scandir

        def guarded_scandir(path):
            candidate = Path(path)
            if any(candidate == target or target in candidate.parents for target in targets):
                raise AssertionError("ordinary GET entered published evidence")
            return original_scandir(path)

        monkeypatch.setattr(index_module.os, "scandir", guarded_scandir)
        monkeypatch.setattr(
            server.index,
            "_read_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("ordinary GET read published JSON evidence")
            ),
        )
        assert _request(f"{base_url}/api/v1/experiments?limit=2")[0] == 200
        assert _request(detail_url)[2]["run_id"] == run_id
        assert _request(data_url)[2]["method"] == "min_max_envelope_v1"
        assert _request(point_url)[2]["point_id"] == "point-42"
    finally:
        _close_server(server, thread)


def _install_scan_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(circuits, _context_value, output_root, _repository_root, **_kwargs):
        execution_root = Path(output_root)
        rows = []
        for circuit in circuits:
            evidence = execution_root / "circuits" / circuit.circuit_id
            evidence.mkdir(parents=True)
            (evidence / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
            rows.append(
                replace(
                    _result(circuit.circuit_id, 0.8, 0.19, 0.0, 0.0),
                    evidence_root=evidence,
                    model_evidence_root=evidence,
                )
            )
        return tuple(rows)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


def test_archive_state_is_eventually_projected_and_detail_survives_hot_removal(
    e2e_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hot = e2e_base / "hot"
    hot.mkdir()
    _install_scan_runner(monkeypatch)
    monkeypatch.setattr(
        spectroscopy_run_module,
        "_enqueue_web_index_best_effort",
        lambda *_args, **_kwargs: None,
    )
    run = run_qubit_spectroscopy_scan(
        replace(_single_request(), run_phase="scan"),
        _context(),
        PARENT,
        hot / f"qubit_spectroscopy_{uuid.uuid4().hex}",
        ROOT,
        timeout_s=10.0,
    )
    server, thread, base_url = _start_server(e2e_base)
    try:
        enqueue_published_run(
            ROOT, run.root, storage_root=server.storage.storage_root
        )
        server.coordinator.notify()
        _eventually(
            lambda: _experiment_page(base_url),
            lambda page: any(row["run_id"] == run.run_id for row in page["items"]),
        )
        before_status, before_headers, _before = _request(
            f"{base_url}/api/v1/experiments?limit=10"
        )
        assert before_status == 200 and before_headers.get("ETag")

        storage = _eventually(
            lambda: _request(f"{base_url}/api/v1/experiment-storage")[2],
            lambda value: (
                value["refreshing"] is False
                and any(row["run_id"] == run.run_id for row in value["items"])
            ),
        )
        row = next(item for item in storage["items"] if item["run_id"] == run.run_id)
        mutation_failures: list[Exception] = []
        mutate = server.storage.mutate

        def observed_mutate(*args, **kwargs):
            try:
                return mutate(*args, **kwargs)
            except Exception as exc:
                mutation_failures.append(exc)
                raise

        server.storage.mutate = observed_mutate  # type: ignore[method-assign]
        try:
            archived = _request(
                f"{base_url}/api/v1/experiments/{run.run_id}/archive",
                method="POST",
                payload={
                    "actor_id": "web.acceptance",
                    "expected_catalog_revision": row["catalog_revision"],
                    "expected_workflow_sha256": row["workflow_sha256"],
                    "reason": "verify eventual archive projection",
                },
            )[2]
        except AssertionError:
            if mutation_failures:
                raise mutation_failures[0]
            raise
        assert archived["item"]["storage_state"] == "archived"
        assert not run.root.exists()

        projected = _eventually(
            lambda: _experiment_page(
                base_url, "limit=10&storage_state=archived"
            ),
            lambda page: any(row["run_id"] == run.run_id for row in page["items"]),
        )
        assert projected["page"]["total"] == 1
        after_headers = _request(
            f"{base_url}/api/v1/experiments?limit=10"
        )[1]
        assert after_headers["ETag"] != before_headers["ETag"]
        detail = _request(f"{base_url}/api/v1/experiments/{run.run_id}")[2]
        assert detail["run_id"] == run.run_id
        assert detail["renderer"] == "qubit_spectroscopy_scan"

        server.coordinator.run_once(shallow_reconcile=True)
        stable = _experiment_page(base_url, "limit=10&storage_state=archived")
        assert [row["run_id"] for row in stable["items"]] == [run.run_id]
    finally:
        _close_server(server, thread)


def test_trash_carrier_recovers_after_read_model_loss(
    e2e_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hot = e2e_base / "hot"
    hot.mkdir()
    _install_scan_runner(monkeypatch)
    monkeypatch.setattr(
        spectroscopy_run_module,
        "_enqueue_web_index_best_effort",
        lambda *_args, **_kwargs: None,
    )
    run = run_qubit_spectroscopy_scan(
        replace(_single_request(), run_phase="scan"),
        _context(),
        PARENT,
        hot / f"qubit_spectroscopy_{uuid.uuid4().hex}",
        ROOT,
        timeout_s=10.0,
    )
    server, thread, base_url = _start_server(e2e_base)
    try:
        enqueue_published_run(
            ROOT, run.root, storage_root=server.storage.storage_root
        )
        server.coordinator.notify()
        _eventually(
            lambda: _experiment_page(base_url),
            lambda page: any(row["run_id"] == run.run_id for row in page["items"]),
        )
        storage = _eventually(
            lambda: _request(f"{base_url}/api/v1/experiment-storage")[2],
            lambda value: (
                value["refreshing"] is False
                and any(row["run_id"] == run.run_id for row in value["items"])
            ),
        )
        row = next(item for item in storage["items"] if item["run_id"] == run.run_id)
        trashed = _request(
            f"{base_url}/api/v1/experiments/{run.run_id}/trash",
            method="POST",
            payload={
                "actor_id": "web.acceptance",
                "expected_catalog_revision": row["catalog_revision"],
                "expected_workflow_sha256": row["workflow_sha256"],
                "reason": "verify read-model loss recovery from trash",
            },
        )[2]
        assert trashed["item"]["storage_state"] == "trash"
        assert not run.root.exists()
    finally:
        _close_server(server, thread)

    database = e2e_base / "storage" / "web-read-model.sqlite"
    for carrier in (database, Path(f"{database}-wal"), Path(f"{database}-shm")):
        carrier.unlink(missing_ok=True)

    restarted, restarted_thread, restarted_url = _start_server(e2e_base)
    try:
        recovered = _eventually(
            lambda: _experiment_page(
                restarted_url, "limit=10&storage_state=trash"
            ),
            lambda page: any(row["run_id"] == run.run_id for row in page["items"]),
        )
        assert recovered["page"]["total"] == 1
        detail = _request(
            f"{restarted_url}/api/v1/experiments/{run.run_id}"
        )[2]
        assert detail["run_id"] == run.run_id
        assert detail["renderer"] == "qubit_spectroscopy_scan"
        recovered_health = _eventually(
            restarted.coordinator.status,
            lambda status: status["status"] == "ok",
        )
        assert recovered_health["pending_projection_count"] == 0
    finally:
        _close_server(restarted, restarted_thread)
