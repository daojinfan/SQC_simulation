from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import json
from pathlib import Path
import sqlite3

import pytest

from sqvm.web.index import CalibrationWebIndex, WebArtifactError
from sqvm.web.read_model import (
    ExperimentProjection,
    PersistentExperimentReadModel,
    ReadModelError,
)
from tests.support.web_projection import projection as _projection


def test_schema_migration_uses_wal_and_required_tables(tmp_path: Path) -> None:
    path = tmp_path / "read-model.sqlite"
    sqlite3.connect(path).close()

    PersistentExperimentReadModel(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "runs",
        "run_targets",
        "dataset_bindings",
        "plot_bindings",
        "projection_events",
        "aggregate_counters",
    } <= tables


def test_reconcile_is_idempotent_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "read-model.sqlite"
    model = PersistentExperimentReadModel(path)
    projections = tuple(_projection(index, eligible=index % 2 == 0) for index in range(12))

    first_revision = model.reconcile(projections)
    second_revision = model.reconcile(projections)
    restarted = PersistentExperimentReadModel(path)

    assert first_revision == second_revision == 1
    assert len(restarted.summaries()) == 12
    assert restarted.detail("run-0003")["renderer"] == "test"
    assert restarted.aggregate() == {"total": 12, "eligible": 6, "invalid": 0, "revision": 1}


def test_same_run_different_identity_is_conflict_and_broken_run_isolated(
    tmp_path: Path,
) -> None:
    model = PersistentExperimentReadModel(tmp_path / "read-model.sqlite")
    first = _projection(1, run_id="same-run", workflow_sha256="A" * 64)
    second = _projection(2, run_id="same-run", workflow_sha256="B" * 64)
    broken = _projection(3, run_id="broken-run")
    broken_summary = {**broken.summary, "verification_status": "invalid", "error": "broken"}
    broken = ExperimentProjection(
        **{**broken.__dict__, "summary": broken_summary, "detail": broken_summary}
    )

    model.reconcile((first, second, broken))
    rows = model.summaries()

    conflicts = [row for row in rows if row["run_id"] == "same-run"]
    assert len(conflicts) == 2
    assert all(row["error"] == "identity_conflict" for row in conflicts)
    assert next(row for row in rows if row["run_id"] == "broken-run")["error"] == "broken"
    assert model.aggregate()["invalid"] == 3
    with pytest.raises(ReadModelError, match="identity_conflict"):
        model.detail("same-run")


def test_keyset_page_cursor_filters_and_injection_resistance(tmp_path: Path) -> None:
    model = PersistentExperimentReadModel(tmp_path / "read-model.sqlite")
    projections = tuple(
        _projection(
            index,
            target="Q1" if index % 3 else "Q2",
            eligible=index % 2 == 0,
            applicable=index % 5 != 0,
            state="archive" if index % 7 == 0 else "hot",
        )
        for index in range(37)
    )
    model.reconcile(projections)

    seen = []
    cursor = None
    while True:
        page = model.page(limit=7, cursor=cursor)
        assert page["schema_version"] == "0.2"
        assert page["page"]["total"] == 37
        seen.extend(row["run_id"] for row in page["items"])
        cursor = page["page"]["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 37

    filtered = model.page(
        limit=50,
        filters={
            "target": ["Q2"],
            "recommendation_state": "eligible",
            "storage_state": "hot",
        },
    )
    assert filtered["page"]["total"] == len(filtered["items"])
    assert all(row["targets"] == ["Q2"] for row in filtered["items"])
    first = model.page(limit=3, filters={"target": ["Q1", "Q2"]})
    # Multi-target filters are OR: a run matching either selected target is included.
    assert first["page"]["total"] == 37
    with pytest.raises(ReadModelError, match="invalid or stale"):
        model.page(limit=3, cursor=first["page"]["next_cursor"] + "x", filters={"target": ["Q1", "Q2"]})
    with pytest.raises(ReadModelError, match="invalid or stale"):
        model.page(limit=3, cursor=first["page"]["next_cursor"], filters={"target": ["Q1"]})
    assert model.page(limit=10, filters={"q": "%_ OR 1=1"})["items"] == []


def test_unbounded_compatibility_list_exceeds_page_cap(tmp_path: Path) -> None:
    model = PersistentExperimentReadModel(tmp_path / "read-model.sqlite")
    model.reconcile(tuple(_projection(index) for index in range(225)))
    assert len(model.summaries()) == 225


def test_hot_reconcile_preserves_archive_and_trash_worker_rows(tmp_path: Path) -> None:
    model = PersistentExperimentReadModel(tmp_path / "read-model.sqlite")
    hot = _projection(1, run_id="hot-run")
    archived = _projection(2, run_id="archive-run", state="archive")
    trashed = _projection(3, run_id="trash-run", state="trash")
    model.reconcile((hot,))
    model.upsert_projection(archived)
    model.upsert_projection(trashed)

    model.reconcile(())

    assert {row["run_id"] for row in model.summaries()} == {"archive-run", "trash-run"}
    assert model.mark_carrier_state("archive-run", "trash", "trash/archive-run") > 0
    assert model.page(limit=10, filters={"storage_state": "trash"})["page"]["total"] == 2


@pytest.mark.parametrize("managed_state", ("archive", "invalid"))
def test_hot_reconcile_does_not_revert_worker_managed_carrier_or_oscillate_revision(
    tmp_path: Path, managed_state: str
) -> None:
    model = PersistentExperimentReadModel(tmp_path / "read-model.sqlite")
    projection = _projection(1, run_id="managed-run")
    assert model.reconcile((projection,)) == 1
    managed_revision = model.mark_carrier_state(
        "managed-run", managed_state, f"{managed_state}/managed-run"
    )

    first = model.reconcile((projection,))
    second = model.reconcile((projection,))

    assert first == second == managed_revision
    assert model.page(
        limit=10, filters={"storage_state": managed_state}
    )["page"]["total"] == 1


def test_stored_json_corruption_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "read-model.sqlite"
    model = PersistentExperimentReadModel(path)
    model.reconcile((_projection(1),))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET detail_json=?", ('{"x":NaN}',))
    with pytest.raises(ReadModelError, match="stored experiment detail JSON is invalid"):
        model.detail("run-0001")
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE runs SET summary_json=?", ('{"x":1,"x":2}',))
    with pytest.raises(ReadModelError, match="stored experiment summary JSON is invalid"):
        model.summaries()


def test_index_gets_use_db_only_and_path_projection_is_confined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    index = CalibrationWebIndex(tmp_path, output, initial_sync=False)
    projection = _projection(1)
    index.upsert_projected_detail(
        projection.summary,
        projection.detail,
        {
            "workflow_sha256": projection.workflow_sha256,
            "receipt_sha256": projection.receipt_sha256,
            "dataset_bindings": projection.dataset_bindings,
        },
    )
    restarted = CalibrationWebIndex(tmp_path, output, initial_sync=False)
    monkeypatch.setattr(
        restarted,
        "_published_experiment_workflows",
        lambda: (_ for _ in ()).throw(AssertionError("GET scanned experiment roots")),
    )
    monkeypatch.setattr(restarted, "configurations", lambda: [])
    monkeypatch.setattr(restarted, "_decisions", lambda: [])

    assert restarted.experiments()[0]["run_id"] == "run-0001"
    assert restarted.experiment("run-0001")["renderer"] == "test"
    assert restarted.overview()["experiments"]["total"] == 1

    carrier = tmp_path / "inbox" / "generic-run"
    carrier.mkdir(parents=True)
    (carrier / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "generic-run",
                "workflow_id": "generic_visualization_v1",
                "status": "completed",
                "created_utc": "2026-07-22T00:00:00Z",
                "plot_specs": [],
            }
        ),
        "utf-8",
    )
    result = restarted.project_experiment_path("inbox/generic-run")
    assert result["run_id"] == "generic-run"
    assert restarted.reconcile_path("inbox/generic-run")["revision"] == result["revision"]
    with pytest.raises(WebArtifactError, match="repository-relative"):
        restarted.project_experiment_path(carrier)
    with pytest.raises(WebArtifactError, match="repository-relative"):
        restarted.project_experiment_path("../outside")

    alias = tmp_path / "inbox" / "linked-run"
    alias.symlink_to(carrier, target_is_directory=True)
    with pytest.raises(WebArtifactError, match="contains a link"):
        restarted.project_experiment_path("inbox/linked-run")
