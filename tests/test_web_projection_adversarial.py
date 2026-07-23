from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid

import pytest

from sqvm.web.coordinator import WebProjectionCoordinator
from sqvm.web.index import CalibrationWebIndex, WebArtifactError
from sqvm.web.read_model import (
    PersistentExperimentReadModel,
    ReadModelError,
)
from sqvm.web.registrar import enqueue_published_run
from sqvm.web.server import (
    ExperimentStorageWebService,
    StorageWebError,
)
from test_web_persistent_read_model import _projection
from test_web_projection_end_to_end import (
    ROOT,
    _close_server,
    _experiment_page,
    _eventually,
    _publish_generic,
    _request,
    _start_server,
)


@pytest.fixture
def adversarial_base():
    base = ROOT / "tmp" / f"web_projection_adversarial_{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize(
    ("suffix", "link_kind"),
    (("", "symlink"), ("-wal", "hardlink"), ("-shm", "symlink")),
)
def test_database_and_sidecar_link_carriers_are_rejected(
    tmp_path: Path, suffix: str, link_kind: str
) -> None:
    database = tmp_path / "web-read-model.sqlite"
    carrier = Path(f"{database}{suffix}")
    outside = tmp_path / f"outside{suffix or '-db'}"
    outside.write_bytes(b"outside")
    if suffix:
        database.touch()
    try:
        if link_kind == "symlink":
            carrier.symlink_to(outside)
        else:
            os.link(outside, carrier)
    except OSError as exc:
        pytest.skip(f"{link_kind} is unavailable: {exc}")

    with pytest.raises(ReadModelError, match="linked or unsafe"):
        PersistentExperimentReadModel(database)


def test_sqlite_connection_is_closed_when_context_exits(tmp_path: Path) -> None:
    model = PersistentExperimentReadModel(tmp_path / "web-read-model.sqlite")

    with model._connect() as connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_corrupt_database_is_quarantined_and_reported_in_health(
    adversarial_base: Path,
) -> None:
    storage = adversarial_base / "storage"
    storage.mkdir()
    database = storage / "web-read-model.sqlite"
    database.write_bytes(b"not-a-sqlite-database")

    server, thread, base_url = _start_server(adversarial_base)
    try:
        health = _request(f"{base_url}/api/v1/health")[2]
        quarantined = list(storage.glob("web-read-model.corrupt.*.sqlite"))
        assert health["read_model_recovery"] == "corrupt_database_quarantined"
        assert len(quarantined) == 1
        assert quarantined[0].read_bytes() == b"not-a-sqlite-database"
        assert database.is_file()
        assert database.read_bytes().startswith(b"SQLite format 3\x00")
    finally:
        _close_server(server, thread)


@pytest.mark.parametrize("link_kind", ("symlink", "hardlink"))
def test_linked_database_is_rejected_not_quarantined(
    adversarial_base: Path, link_kind: str
) -> None:
    storage = adversarial_base / "storage"
    storage.mkdir()
    database = storage / "web-read-model.sqlite"
    outside = adversarial_base / "outside.sqlite"
    outside.write_bytes(b"not-a-sqlite-database")
    try:
        if link_kind == "symlink":
            database.symlink_to(outside)
        else:
            os.link(outside, database)
    except OSError as exc:
        pytest.skip(f"{link_kind} is unavailable: {exc}")

    with pytest.raises(ReadModelError, match="linked or unsafe"):
        _start_server(adversarial_base)

    assert not list(storage.glob("web-read-model.corrupt.*"))
    assert outside.read_bytes() == b"not-a-sqlite-database"


def test_carrier_update_is_identity_scoped_and_idempotent(tmp_path: Path) -> None:
    model = PersistentExperimentReadModel(tmp_path / "web-read-model.sqlite")
    first = _projection(1, run_id="identity-conflict", workflow_sha256="A" * 64)
    second = _projection(2, run_id="identity-conflict", workflow_sha256="B" * 64)
    model.reconcile((first, second))
    baseline = model.aggregate()["revision"]

    revision = model.mark_carrier_state(
        first.run_id,
        "archived",
        "archive",
        workflow_sha256=first.workflow_sha256,
        receipt_sha256=first.receipt_sha256,
    )

    assert revision == baseline + 1
    assert model.page(limit=10, filters={"storage_state": "archived"})["page"][
        "total"
    ] == 1
    assert model.page(limit=10, filters={"storage_state": "hot"})["page"][
        "total"
    ] == 1
    assert (
        model.mark_carrier_state(
            first.run_id,
            "archived",
            "archive",
            workflow_sha256=first.workflow_sha256,
            receipt_sha256=first.receipt_sha256,
        )
        == revision
    )


def test_coordinator_passes_catalog_identity_and_does_not_oscillate_revision(
    tmp_path: Path,
) -> None:
    model = PersistentExperimentReadModel(tmp_path / "web-read-model.sqlite")
    first = replace(
        _projection(1, run_id="identity-conflict", workflow_sha256="A" * 64),
        carrier_alias="hot",
    )
    second = replace(
        _projection(2, run_id="identity-conflict", workflow_sha256="B" * 64),
        carrier_alias="hot",
    )
    model.reconcile((first, second))

    class Index:
        def mark_carrier_state(self, run_id, state, alias, **identity):
            return model.mark_carrier_state(run_id, state, alias, **identity)

        def reconcile_experiments(self):
            return model.aggregate()["revision"]

    class Storage:
        def projection_rows(self):
            return (
                {
                    "run_id": first.run_id,
                    "workflow_sha256": first.workflow_sha256,
                    "receipt_sha256": first.receipt_sha256,
                    "storage_state": "archived",
                    "carrier_alias": "archive",
                },
            )

    coordinator = WebProjectionCoordinator(
        Index(),
        Storage(),
        repository_root=tmp_path,
        storage_root=tmp_path / "storage",
    )
    coordinator.run_once()
    revision = model.aggregate()["revision"]
    coordinator.run_once()

    assert model.aggregate()["revision"] == revision
    assert model.page(limit=10, filters={"storage_state": "archived"})["page"][
        "total"
    ] == 1
    assert model.page(limit=10, filters={"storage_state": "hot"})["page"][
        "total"
    ] == 1


def test_custom_hot_root_is_rebuilt_without_inbox(adversarial_base: Path) -> None:
    target = _publish_generic(
        adversarial_base,
        "cold-hot-run",
        created_utc="2026-07-22T10:00:00.000000Z",
        collection="hot",
    )
    server, thread, base_url = _start_server(adversarial_base)
    try:
        page = _eventually(
            lambda: _experiment_page(base_url),
            lambda value: any(
                row["run_id"] == "cold-hot-run" for row in value["items"]
            ),
        )
        assert page["page"]["total"] == 1
        assert target.is_dir()
    finally:
        _close_server(server, thread)


def test_stop_does_not_silently_return_with_live_worker(tmp_path: Path) -> None:
    class Index:
        pass

    class Storage:
        pass

    class StuckThread:
        alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            assert timeout is None
            self.alive = False

    coordinator = WebProjectionCoordinator(
        Index(),
        Storage(),
        repository_root=tmp_path,
        storage_root=tmp_path / "storage",
    )
    coordinator._thread = StuckThread()  # type: ignore[assignment]

    coordinator.stop()

    assert coordinator.status()["running"] is False


def test_bad_inbox_event_does_not_starve_later_valid_event(
    adversarial_base: Path,
) -> None:
    output = adversarial_base / "output"
    output.mkdir()
    storage_root = adversarial_base / "storage"
    index = CalibrationWebIndex(
        ROOT,
        output,
        read_model_path=storage_root / "web-read-model.sqlite",
        trusted_read_model_path=True,
        initial_sync=False,
    )

    class Storage:
        def projection_rows(self):
            return ()

    coordinator = WebProjectionCoordinator(
        index,
        Storage(),
        repository_root=ROOT,
        storage_root=storage_root,
    )
    targets = [
        _publish_generic(
            adversarial_base,
            f"batch-run-{number}",
            created_utc=f"2026-07-22T1{number}:00:00.000000Z",
        )
        for number in range(2)
    ]
    registrations = [
        enqueue_published_run(ROOT, target, storage_root=storage_root)
        for target in targets
    ]
    bad_index = min(range(2), key=lambda index_: registrations[index_].path.name)
    good_index = 1 - bad_index
    shutil.rmtree(targets[bad_index])

    coordinator.run_once()

    assert index.experiment(f"batch-run-{good_index}")["renderer"] == "generic"
    assert registrations[bad_index].path.is_file()
    assert not registrations[good_index].path.exists()
    assert coordinator.status()["status"] == "degraded"
    assert coordinator.status()["pending_projection_count"] == 1


def test_bad_archive_projection_does_not_starve_later_archive(tmp_path: Path) -> None:
    class Index:
        def __init__(self):
            self.upserts = []

        def mark_carrier_state(self, *_args, **_kwargs):
            raise WebArtifactError("missing projection", status=404)

        def upsert_projected_detail(self, summary, detail, identity, carrier):
            self.upserts.append((summary["run_id"], identity, carrier))

        def reconcile_experiments(self):
            return 0

    class Storage:
        def projection_rows(self):
            return (
                {
                    "run_id": "bad-archive",
                    "workflow_sha256": "A" * 64,
                    "receipt_sha256": "B" * 64,
                    "storage_state": "archived",
                    "carrier_alias": "archive",
                },
                {
                    "run_id": "good-archive",
                    "workflow_sha256": "C" * 64,
                    "receipt_sha256": "D" * 64,
                    "storage_state": "archived",
                    "carrier_alias": "archive",
                },
            )

        def experiment_detail(self, run_id):
            if run_id == "bad-archive":
                raise ValueError("corrupt archive")
            return {
                "run_id": run_id,
                "workflow_id": "scan-v1",
                "experiment_kind": "scan",
                "status": "completed",
                "created_utc": "2026-07-22T00:00:00Z",
                "data_origin": "test",
                "verification_status": "verified",
                "targets": ["Q1"],
                "execution_mode": "simulation",
                "recommendation_applicable": False,
                "recommendation_eligible": False,
                "parent_calibration": None,
                "gate_summary": {},
                "candidate_summary": [],
                "relative_path": f"archive:{run_id}",
                "error": None,
            }

    index = Index()
    coordinator = WebProjectionCoordinator(
        index,
        Storage(),
        repository_root=tmp_path,
        storage_root=tmp_path / "storage",
    )
    coordinator.run_once()

    assert [row[0] for row in index.upserts] == ["good-archive"]
    assert coordinator.status()["status"] == "degraded"


def test_replaced_inbox_inode_is_not_unlinked(
    adversarial_base: Path,
) -> None:
    output = adversarial_base / "output"
    output.mkdir()
    storage_root = adversarial_base / "storage"
    index = CalibrationWebIndex(
        ROOT,
        output,
        read_model_path=storage_root / "web-read-model.sqlite",
        trusted_read_model_path=True,
        initial_sync=False,
    )

    class Storage:
        def projection_rows(self):
            return ()

    coordinator = WebProjectionCoordinator(
        index,
        Storage(),
        repository_root=ROOT,
        storage_root=storage_root,
    )
    target = _publish_generic(
        adversarial_base,
        "inode-run",
        created_utc="2026-07-22T12:00:00.000000Z",
    )
    registration = enqueue_published_run(ROOT, target, storage_root=storage_root)
    raw = registration.path.read_bytes()
    project = index.project_experiment_path

    def replace_after_projection(relative_path):
        result = project(relative_path)
        registration.path.unlink()
        registration.path.write_bytes(raw + b" ")
        return result

    index.project_experiment_path = replace_after_projection  # type: ignore[method-assign]
    coordinator.run_once()

    assert registration.path.is_file()
    assert coordinator.status()["status"] == "degraded"
    assert index.experiment("inode-run")["run_id"] == "inode-run"


def test_catalog_refresh_failure_is_not_reported_as_projection_success(
    tmp_path: Path,
) -> None:
    hot = tmp_path / "hot"
    configuration = tmp_path / "configuration"
    hot.mkdir()
    configuration.mkdir()
    service = ExperimentStorageWebService(
        hot_root=hot,
        storage_root=tmp_path / "storage",
        configuration_root=configuration,
        experiment_output_root=hot,
    )
    service.bootstrap_roots()
    service.overview()
    service._catalog_refresh_error = True
    service._catalog_refresh_retry_after = time.monotonic() + 30.0

    with pytest.raises(StorageWebError, match="catalog"):
        service.projection_rows()
