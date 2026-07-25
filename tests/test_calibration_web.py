from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
from sqvm.calibration import run_qubit_spectroscopy_calibration
from sqvm.calibration.spectroscopy_run import run_qubit_spectroscopy_scan
from sqvm.qcis.canonical import sha256_bytes
from sqvm.web import (
    CalibrationWebIndex,
    ConfigurationManagementError,
    PlatformConfigurationStore,
    create_calibration_web_server,
)
from sqvm.web.server import ExperimentStorageWebService, StorageWebError
from tests.support.contexts import spectroscopy_context as _context, spectroscopy_result as _result, single_spectroscopy_request as _single_request
from tests.support.calibration_requests import spectroscopy_calibration_request as _request
from tests.support.synthetic_runners import install_synthetic_spectroscopy_runner as _install_synthetic_runner
PARENT = Path(__file__).resolve().parents[1] / "configs" / "calibration" / "platform_uncalibrated_v1.json"


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def web_workspace(monkeypatch):
    base = ROOT / "tmp" / f"web_{uuid.uuid4().hex}"
    output = base / "output"
    storage = output / "platform-configurations"
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    run = run_qubit_spectroscopy_calibration(
        _request(),
        _context(),
        PARENT,
        output / "spectroscopy_run",
        ROOT,
    )
    try:
        yield base, output, storage, run
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _legacy_configuration(index: CalibrationWebIndex) -> dict:
    row = next(item for item in index.configurations() if item["state_id"] == "uncalibrated")
    return index.configuration(row["configuration_id"])["raw"]


def test_web_index_reads_configuration_and_spectroscopy_detail(web_workspace):
    _base, output, _storage, run = web_workspace
    index = CalibrationWebIndex(ROOT, output)

    overview = index.overview()
    assert overview["experiments"] == {"total": 1, "eligible": 1, "invalid": 0}
    summary = index.experiments()[0]
    assert summary["run_id"] == run.run_id
    assert summary["targets"] == ["Q1", "Q2"]
    detail = index.experiment(run.run_id)
    assert detail["renderer"] == "qubit_spectroscopy"
    assert set(detail["datasets"]["confirmations"]) == {"Q1", "Q2"}
    assert len(detail["datasets"]["coarse"]["points"]) == 3
    plot_spec = detail["plot_specs"][0]
    assert plot_spec["plot_type"] == "line"
    assert [row["id"] for row in plot_spec["objects"]] == ["Q1", "Q2"]
    assert [row["id"] for row in plot_spec["metrics"]] == ["P0", "P1", "leakage"]
    asset, content_type = index.experiment_asset(run.run_id, "spectroscopy.png")
    assert asset.is_file() and content_type == "image/png"


def test_generic_experiment_can_publish_validated_heatmap_plot_spec(web_workspace):
    _base, output, _storage, _run = web_workspace
    target = output / "generic_heatmap"
    target.mkdir()
    workflow = {
        "run_id": "generic-heatmap-1",
        "workflow_id": "rabi_2d_v1",
        "status": "completed",
        "plot_specs": [{
            "schema_version": "1.0",
            "plot_id": "rabi_2d",
            "plot_type": "heatmap",
            "title": "二维 Rabi",
            "objects": [{"id": "Q1", "label": "Q1", "default_visible": True}],
            "metrics": [{"id": "P1", "label": "P1", "default_visible": True}],
            "axes": {"x": {"label": "幅度", "unit": "GHz"}, "y": {"label": "时长", "unit": "ns"}},
            "layers": [{
                "id": "Q1:P1", "object_id": "Q1", "metric_id": "P1",
                "cells": [
                    {"id": "p0", "x": 0.01, "y": 10.0, "value": 0.2},
                    {"id": "p1", "x": 0.02, "y": 10.0, "value": 0.4},
                    {"id": "p2", "x": 0.01, "y": 20.0, "value": 0.6},
                    {"id": "p3", "x": 0.02, "y": 20.0, "value": 0.8},
                ],
            }],
        }],
    }
    (target / "workflow.json").write_text(json.dumps(workflow), "utf-8")

    index = CalibrationWebIndex(ROOT, output)
    detail = index.experiment("generic-heatmap-1")

    assert detail["renderer"] == "generic"
    assert detail["verification_status"] == "unverified_generic"
    assert detail["plot_specs"][0]["plot_type"] == "heatmap"


def test_configuration_store_draft_publish_active_and_requalification(web_workspace):
    _base, output, storage, _run = web_workspace
    index = CalibrationWebIndex(ROOT, output)
    store = PlatformConfigurationStore(ROOT, storage)
    base = store.bootstrap_configuration(_legacy_configuration(index))
    draft = store.create_draft(base, actor_id="project.manager", name="Control baseline")

    draft = store.update_draft(
        draft["draft_id"], actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"], name=draft["name"],
        note="structured v0.2 baseline", editable=draft["editable"],
    )
    validation = store.validate_draft(draft["draft_id"], actor_id="project.manager")
    assert validation["status"] == "valid"
    assert validation["requires_requalification"] is False
    snapshot = store.publish_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name="Reviewed baseline",
        reason="record calibration metadata",
        keep=True,
    )
    assert snapshot["experiment_eligible"] is True
    with pytest.raises(ConfigurationManagementError, match="uninitialized"):
        store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")

    second = store.create_draft(snapshot, actor_id="project.manager", name="Latency trial")
    changed = second["editable"]
    changed["control_values"]["lanes"]["q1_xy_i"]["latency_samples"] += 1
    second = store.update_draft(
        second["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=second["content_sha256"],
        name=second["name"],
        note="requires Stage 4.1 review",
        editable=changed,
    )
    assert store.validate_draft(second["draft_id"], actor_id="project.manager")[
        "requires_requalification"
    ] is True
    blocked = store.publish_draft(
        second["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=second["content_sha256"],
        name="Latency trial",
        reason="test latency adjustment",
    )
    assert blocked["experiment_eligible"] is False
    with pytest.raises(ConfigurationManagementError, match="requalification"):
        store.set_active(
            blocked["snapshot_id"],
            actor_id="project.manager",
            confirmation_phrase=f"SET ACTIVE {blocked['snapshot_id']}",
        )


def test_candidate_creates_draft_without_direct_activation(web_workspace):
    _base, output, storage, run = web_workspace
    index = CalibrationWebIndex(ROOT, output)
    store = PlatformConfigurationStore(ROOT, storage)
    base = store.bootstrap_configuration(_legacy_configuration(index))
    draft = store.create_draft(base, actor_id="project.manager", name="Spectroscopy candidate")
    detail = index.experiment(run.run_id)
    updated = store.apply_candidates_to_draft(
        draft["draft_id"],
        actor_id="project.manager",
        experiment_run_id=run.run_id,
        recommendation_id=detail["recommendation_id"],
        candidates=detail["candidates"],
    )

    qagents = updated["editable"]["calibration_values"]["qagents"]
    assert qagents["Q1"]["reference_frequency_authority"]["reference_frequency_GHz"] == pytest.approx(5.0)
    assert qagents["Q2"]["reference_frequency_authority"]["reference_frequency_GHz"] == pytest.approx(5.2)
    assert store.active_configurations() == []
    assert updated["source_candidate"]["experiment_run_id"] == run.run_id

    validation = store.validate_draft(updated["draft_id"], actor_id="project.manager")
    assert validation["status"] == "valid"
    assert validation["field_errors"] == []


def test_draft_checkpoint_retention_is_bounded(web_workspace):
    _base, output, storage, _run = web_workspace
    index = CalibrationWebIndex(ROOT, output)
    store = PlatformConfigurationStore(ROOT, storage)
    base = store.bootstrap_configuration(_legacy_configuration(index))
    draft = store.create_draft(base, actor_id="project.manager", name="Checkpoint test")
    for number in range(24):
        draft = store.update_draft(
            draft["draft_id"],
            actor_id="project.manager",
            expected_content_sha256=draft["content_sha256"],
            name=draft["name"],
            note=f"rolling checkpoints {number}",
            editable=draft["editable"],
        )
    assert len(store.draft(draft["draft_id"])["checkpoints"]) == 20
    assert store.validate_draft(draft["draft_id"], actor_id="project.manager")[
        "status"
    ] == "valid"
    snapshot = store.publish_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name="Checkpoint test",
        reason="prove the initial checkpoint survives rolling retention",
    )
    assert snapshot["snapshot_id"]


def test_validation_rejects_invalid_calibration_and_physical_fields(web_workspace):
    _base, output, storage, run = web_workspace
    index = CalibrationWebIndex(ROOT, output)
    store = PlatformConfigurationStore(ROOT, storage)
    base = store.bootstrap_configuration(_legacy_configuration(index))
    draft = store.create_draft(base, actor_id="project.manager", name="Invalid values")
    editable = draft["editable"]
    editable["calibration_values"]["capacitance_f"] = 1e-15
    with pytest.raises(ConfigurationManagementError) as captured:
        store.update_draft(draft["draft_id"], actor_id="project.manager", expected_content_sha256=draft["content_sha256"], name=draft["name"], note="must reject physical field", editable=editable)
    assert captured.value.field_errors[0]["path"] == "$.calibration_values.capacitance_f"


def test_validation_rejects_invalid_control_sections(web_workspace):
    _base, output, storage, _run = web_workspace
    index = CalibrationWebIndex(ROOT, output)
    store = PlatformConfigurationStore(ROOT, storage)
    base = store.bootstrap_configuration(_legacy_configuration(index))
    draft = store.create_draft(base, actor_id="project.manager", name="Invalid control")
    editable = draft["editable"]
    control = editable["control_values"]
    control["dac"]["bits"] = 0
    control["lane_order"] = control["lane_order"][:-1]
    control["static_mixing"]["z"]["matrix"][0] = [0.5]
    control["acceptance"]["max_condition_number"] = -1.0
    draft = store.update_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name=draft["name"],
        note="must fail control validation",
        editable=editable,
    )
    validation = store.validate_draft(draft["draft_id"], actor_id="project.manager")
    assert validation["status"] == "invalid"
    assert validation["field_errors"]


def test_parent_snapshot_cannot_be_deleted_while_child_exists(web_workspace):
    _base, output, storage, _run = web_workspace
    index = CalibrationWebIndex(ROOT, output)
    store = PlatformConfigurationStore(ROOT, storage)
    base = store.bootstrap_configuration(_legacy_configuration(index))
    draft = store.create_draft(base, actor_id="project.manager", name="Parent")
    draft = store.update_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name=draft["name"],
        note="parent snapshot",
        editable=draft["editable"],
    )
    store.validate_draft(draft["draft_id"], actor_id="project.manager")
    parent = store.publish_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name="Parent",
        reason="lineage test",
    )
    child = store.create_draft(parent, actor_id="project.manager", name="Child")
    with pytest.raises(ConfigurationManagementError, match="referenced"):
        store.delete_snapshot(parent["snapshot_id"], actor_id="project.manager")
    store.delete_draft(child["draft_id"], actor_id="project.manager")
    with pytest.raises(ConfigurationManagementError, match="referenced"):
        store.delete_snapshot(parent["snapshot_id"], actor_id="project.manager")


def test_http_api_serves_console_and_configuration_mutations(web_workspace):
    _base, output, storage, run = web_workspace
    setup_index = CalibrationWebIndex(ROOT, output)
    setup_store = PlatformConfigurationStore(ROOT, storage)
    seed = setup_store.create_draft(
        setup_store.bootstrap_configuration(_legacy_configuration(setup_index)),
        actor_id="project.manager",
        name="HTTP current seed",
    )
    setup_current = setup_store.current_configuration("demo_2q1c2r")
    setup_store.initialize_current_calibration(
        "demo_2q1c2r",
        actor_id="project.manager",
        expected_content_sha256=setup_current["content_sha256"],
    )
    setup_store.delete_draft(seed["draft_id"], actor_id="project.manager")
    server = create_calibration_web_server(
        ROOT,
        output_root=output,
        configuration_storage_root=storage,
        port=0,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        health = _http_json(f"{base_url}/api/v1/health")
        assert health["configuration_mutations_enabled"] is True
        assert health["experiment_execution_enabled"] is False
        html = urlopen(f"{base_url}/", timeout=5).read().decode("utf-8")
        assert "SQVM" in html and "校准控制台" in html and "app.js" in html
        configurations = _http_json(f"{base_url}/api/v1/configurations")["items"]
        legacy = next(row for row in configurations if row["state_id"] == "uncalibrated")
        configuration = _http_json(
            f"{base_url}/api/v1/configurations/{legacy['configuration_id']}"
        )
        assert set(configuration["control_values"]) == {
            "clock",
            "dac",
            "lane_order",
            "lanes",
            "static_mixing",
            "idle_flux_phi0",
            "acceptance",
            "simulation",
        }
        assert len(configuration["control_values"]["lanes"]) == 11
        assert set(configuration["control_values"]["static_mixing"]) == {
            "xy",
            "z",
            "readout",
        }
        experiments = _http_json(f"{base_url}/api/v1/experiments")
        assert experiments["items"][0]["run_id"] == run.run_id

        created = _http_json(
            f"{base_url}/api/v1/experiments/{run.run_id}/draft",
            method="POST",
            payload={
                "actor_id": "project.manager",
                    "candidate_ids": [
                        "Q1.reference_frequency_GHz",
                        "Q2.reference_frequency_GHz",
                    ],
                "name": "Candidate draft",
                "note": "from Web API",
            },
        )
        assert created["source_candidate"]["targets"] == ["Q1", "Q2"]
        diff = _http_json(
            f"{base_url}/api/v1/drafts/{created['draft_id']}/diff?against=parent"
        )
        assert diff["draft_id"] == created["draft_id"]
        assert diff["baseline_checkpoint"] == 0
        assert diff["changed_count"] > 0
        assert diff["control_changed"] is False
        assert {row["group"] for row in diff["changes"]} >= {"Q1", "Q2"}
        assert all("wave_index" not in row["path"] for row in diff["changes"])
        with pytest.raises(HTTPError) as captured:
            _http_json(
                f"{base_url}/api/v1/drafts/{created['draft_id']}/diff?against=checkpoint"
            )
        assert captured.value.code == 422
        with pytest.raises(HTTPError) as captured:
            _http_json(
                f"{base_url}/api/v1/drafts/{uuid.uuid4()}/diff?against=parent"
            )
        assert captured.value.code == 404
        management = _http_json(f"{base_url}/api/v1/configuration-management")
        assert len(management["drafts"]) == 1
        assert len(management["current"]) == 1
        current = _http_json(
            f"{base_url}/api/v1/current-configurations/demo_2q1c2r"
        )
        with pytest.raises(HTTPError) as captured:
            _http_json(
                f"{base_url}/api/v1/experiments/{run.run_id}/apply-current",
                method="POST",
                payload={
                    "actor_id": "project.manager",
                    "device_id": "demo_2q1c2r",
                    "expected_content_sha256": current["content_sha256"],
                    "candidate_ids": ["Q1.reference_frequency_GHz"],
                    "confirmation_phrase": "APPLY",
                },
            )
        assert captured.value.code == 422
        current = _http_json(
            f"{base_url}/api/v1/experiments/{run.run_id}/apply-current",
            method="POST",
            payload={
                "actor_id": "project.manager",
                "device_id": "demo_2q1c2r",
                "expected_content_sha256": current["content_sha256"],
                "candidate_ids": ["Q1.reference_frequency_GHz"],
                "confirmation_phrase": (
                    f"APPLY CALIBRATION CANDIDATES {run.run_id}"
                ),
            },
        )
        assert current["source_candidate"]["experiment_run_id"] == run.run_id
        assert current["source_candidate"]["targets"] == ["Q1"]
        operation_id = str(uuid.uuid4())
        update_payload = {
            "actor_id": "project.manager",
            "expected_content_sha256": current["content_sha256"],
            "name": "HTTP current configuration",
            "note": "saved directly through current API",
            "editable": current["editable"],
            "operation_id": operation_id,
        }
        updated_current = _http_json(
            f"{base_url}/api/v1/current-configurations/demo_2q1c2r",
            method="PUT",
            payload=update_payload,
        )
        assert updated_current["revision"] == current["revision"] + 1
        assert updated_current["name"] == "HTTP current configuration"
        assert updated_current["transaction"] == {
            "transaction_id": operation_id,
            "operation_id": operation_id,
            "generation": updated_current["transaction"]["generation"],
            "durability_status": "committed",
            "projection_status": "complete",
            "superseded": False,
        }
        replay = _http_json(
            f"{base_url}/api/v1/current-configurations/demo_2q1c2r",
            method="PUT",
            payload=update_payload,
        )
        assert replay == updated_current
        with pytest.raises(HTTPError) as captured:
            _http_json(
                f"{base_url}/api/v1/current-configurations/demo_2q1c2r",
                method="PUT",
                payload={**update_payload, "note": "different request"},
            )
        assert captured.value.code == 409
        conflict = json.loads(captured.value.read().decode("utf-8"))
        assert conflict["code"] == "idempotency_conflict"
        assert conflict["transaction_id"] == operation_id

        stale_payload = {
            "actor_id": "project.manager",
            "expected_content_sha256": "0" * 64,
            "name": created["name"],
            "note": created["note"],
            "editable": created["editable"],
        }
        with pytest.raises(HTTPError) as captured:
            _http_json(
                f"{base_url}/api/v1/drafts/{created['draft_id']}",
                method="PUT",
                payload=stale_payload,
            )
        assert captured.value.code == 409

        invalid_editable = copy.deepcopy(created["editable"])
        invalid_editable["control_values"]["dac"]["offset_V"] = 0.0
        with pytest.raises(HTTPError) as captured:
            _http_json(
                f"{base_url}/api/v1/drafts/{created['draft_id']}",
                method="PUT",
                payload={
                    **stale_payload,
                    "expected_content_sha256": created["content_sha256"],
                    "editable": invalid_editable,
                },
            )
        assert captured.value.code == 422
        unchanged = _http_json(f"{base_url}/api/v1/drafts/{created['draft_id']}")
        assert unchanged["content_sha256"] == created["content_sha256"]

        request = Request(
            f"{base_url}/api/v1/drafts/{created['draft_id']}/validate",
            method="POST",
            data=b'{"actor_id":"project.manager","keep":NaN}',
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(HTTPError) as captured:
            urlopen(request, timeout=5)
        assert captured.value.code == 400

        with pytest.raises(HTTPError) as captured:
            _http_json(f"{base_url}/api/v1/run", method="POST", payload={})
        assert captured.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_experiment_storage_http_contract_uses_server_authority_only(web_workspace):
    _base, output, storage, _run = web_workspace
    run_id = str(uuid.uuid4())
    row = {
        "schema_version": "0.1", "run_id": run_id, "workflow_id": "qubit_spectroscopy_scan_v1",
        "workflow_sha256": "A" * 64, "storage_state": "hot", "retention_state": "normal",
        "created_utc": "", "carrier": {"read_preference": "hot"}, "logical_bytes": 12,
        "allocated_bytes": 4096, "allocated_estimated": False, "reference_count": 0,
        "references": [], "delete_after_utc": None, "allowed_actions": ["archive", "trash"],
        "blockers": [], "catalog_revision": 7,
    }

    class StorageStub:
        def __init__(self): self.calls = []
        def overview(self):
            return {"schema_version": "0.1", "catalog_revision": 7, "logical_bytes": 12,
                    "allocated_bytes": 4096, "archive_bytes": 0, "reclaimable_now_bytes": 0,
                    "reclaimable_after_trash_bytes": 0, "volume_free_bytes": 10_000,
                    "volume_total_bytes": 20_000, "allocated_estimated": False, "items": [row]}
        def run(self, identifier, *, trash_only=False):
            if identifier != run_id or trash_only: raise __import__("sqvm.web.server", fromlist=["StorageWebError"]).StorageWebError("experiment_not_found", 404, "missing", run_id=identifier)
            return row
        def trash(self): return {"schema_version": "0.1", "catalog_revision": 7, "items": []}
        def mutate(self, identifier, action, payload):
            self.calls.append((identifier, action, payload)); return {"schema_version": "0.1", "operation_id": "op", "catalog_revision": 8, "item": row}

    server = create_calibration_web_server(ROOT, output_root=output, configuration_storage_root=storage, port=0)
    server.storage = StorageStub()
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    payload = {"actor_id": "project.manager", "expected_catalog_revision": 7,
               "expected_workflow_sha256": "A" * 64, "reason": "retention review"}
    try:
        assert _http_json(f"{base_url}/api/v1/experiment-storage")["items"][0]["run_id"] == run_id
        assert _http_json(f"{base_url}/api/v1/experiments/{run_id}/storage")["catalog_revision"] == 7
        assert _http_json(f"{base_url}/api/v1/experiment-trash")["items"] == []
        with pytest.raises(HTTPError) as captured:
            _http_json(f"{base_url}/api/v1/experiment-trash/{run_id}")
        assert captured.value.code == 404
        result = _http_json(f"{base_url}/api/v1/experiments/{run_id}/archive", method="POST", payload=payload)
        assert result["catalog_revision"] == 8 and server.storage.calls[0][1] == "archive"
        with pytest.raises(HTTPError) as captured:
            _http_json(f"{base_url}/api/v1/experiments/{run_id}/archive", method="POST", payload={**payload, "client_path": "C:/unsafe"})
        assert captured.value.code == 422
        error = json.loads(captured.value.read().decode("utf-8"))
        assert set(error) == {"code", "status", "error", "details"}
        with pytest.raises(HTTPError) as captured:
            _http_json(f"{base_url}/api/v1/experiment-trash/{run_id}/purge", method="POST", payload=payload)
        assert captured.value.code == 405
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_storage_get_is_lazy_read_only_after_first_catalog_build(tmp_path: Path) -> None:
    hot, storage, configuration = (tmp_path / name for name in ("hot", "storage", "configuration"))
    hot.mkdir(); configuration.mkdir()
    service = ExperimentStorageWebService(hot_root=hot, storage_root=storage,
        configuration_root=configuration, experiment_output_root=hot)

    first = service.overview()
    catalog = storage / "catalog.sqlite"
    before = catalog.stat().st_mtime_ns
    second = service.overview()

    assert first["catalog_revision"] == second["catalog_revision"]
    assert catalog.stat().st_mtime_ns == before


def test_storage_startup_roots_reject_symlinks_without_creating_in_target(tmp_path: Path) -> None:
    hot, configuration, target = tmp_path / "hot", tmp_path / "configuration", tmp_path / "target"
    hot.mkdir(); configuration.mkdir(); target.mkdir()
    storage_link = tmp_path / "storage-link"
    os.symlink(target, storage_link, target_is_directory=True)
    service = ExperimentStorageWebService(hot_root=hot, storage_root=storage_link,
        configuration_root=configuration, experiment_output_root=hot)
    with pytest.raises(StorageWebError, match="linked"):
        service._roots()
    assert not (target / "lifecycle").exists()

    storage = tmp_path / "storage"; archive_link = tmp_path / "archive-link"
    os.symlink(target, archive_link, target_is_directory=True)
    service = ExperimentStorageWebService(hot_root=hot, storage_root=storage,
        configuration_root=configuration, experiment_output_root=hot, archive_root=archive_link)
    with pytest.raises(StorageWebError, match="linked"):
        service._roots()
    assert not (target / "archives").exists()


def test_create_server_rejects_linked_startup_root_before_serving_and_releases_port(web_workspace) -> None:
    base, output, configuration, _run = web_workspace
    target, hot = base / "linked-target", base / "hot"
    target.mkdir(); hot.mkdir()
    storage_link = base / "storage-link"
    os.symlink(target, storage_link, target_is_directory=True)
    probe = socket.socket(); probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]; probe.close()

    with pytest.raises(StorageWebError, match="linked"):
        create_calibration_web_server(ROOT, output_root=output, configuration_storage_root=configuration,
            experiment_hot_root=hot, experiment_storage_root=storage_link, port=port)
    assert not any(target.iterdir())
    released = socket.socket()
    try:
        released.bind(("127.0.0.1", port))
    finally:
        released.close()


def test_create_server_bootstraps_fresh_authority_roots_without_catalog(web_workspace) -> None:
    base, _output, _configuration, _run = web_workspace
    output = base / "fresh-output"; output.mkdir()
    configuration = base / "fresh-configuration"
    server = create_calibration_web_server(
        ROOT,
        output_root=output,
        configuration_storage_root=configuration,
        port=0,
    )
    try:
        assert configuration.is_dir()
        assert server.store.root == configuration.resolve()
        assert (output / "experiments").is_dir()
        assert (output / "experiment-storage" / "lifecycle").is_dir()
        assert not (output / "experiment-storage" / "catalog.sqlite").exists()
    finally:
        server.server_close()


def test_storage_public_catalog_projection_never_exposes_carrier_paths() -> None:
    from sqvm.web.server import _public_catalog_row

    class Row:
        def to_dict(self):
            return {"carrier": {"hot_path": "C:/private/hot", "archive_path": "C:/private/archive.sqrun",
                                "trash_path": "C:/private/trash", "read_preference": "archive"}}

    assert _public_catalog_row(Row())["carrier"] == {"read_preference": "archive"}


def test_server_accepts_separate_trusted_archive_root_and_rejects_relative_root(web_workspace) -> None:
    base, output, configuration, _run = web_workspace
    hot, storage, archive = base / "hot", base / "storage", base / "archive"
    hot.mkdir(); archive.mkdir()
    server = create_calibration_web_server(ROOT, output_root=output, configuration_storage_root=configuration,
        experiment_hot_root=hot, experiment_storage_root=storage, experiment_archive_root=archive, port=0)
    try:
        assert server.storage.archive_root == archive.absolute()
    finally:
        server.server_close()
    with pytest.raises(ValueError, match="absolute local"):
        create_calibration_web_server(ROOT, output_root=output, configuration_storage_root=configuration,
            experiment_hot_root=hot, experiment_storage_root=storage, experiment_archive_root="relative-archive", port=0)


def test_web_storage_reads_operations_lifecycle_keep_and_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(circuits, _context_value, output_root, _repository_root, **kwargs):
        root = Path(output_root); rows = []
        for circuit in circuits:
            evidence = root / "circuits" / circuit.circuit_id; evidence.mkdir(parents=True)
            (evidence / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
            rows.append(replace(
                _result(circuit.circuit_id, .8, .19, 0, 0),
                circuit_sha256=sha256_bytes(circuit.source.encode("utf-8")),
                readout_qubit=tuple(tuple(group) for group in kwargs["readout_qubit"]),
                evidence_root=evidence,
                model_evidence_root=evidence,
            ))
        return tuple(rows)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)
    writer = ROOT / "tmp" / f"web_storage_{uuid.uuid4().hex}"
    try:
        scan = run_qubit_spectroscopy_scan(
            replace(_single_request(), run_phase="scan"), _context(),
            ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json",
            writer / f"qubit_spectroscopy_{uuid.uuid4().hex}", ROOT, timeout_s=10,
        )
        hot = tmp_path / "experiments"; hot.mkdir()
        shutil.copytree(scan.root, hot / scan.root.name)
        storage = tmp_path / "storage"
        references = tmp_path / "reference-authority"; references.mkdir()
        service = ExperimentStorageWebService(hot_root=hot, storage_root=storage, configuration_root=references,
            experiment_output_root=hot)
        from sqvm.storage.catalog import CatalogReferenceGraph
        from sqvm.storage.references import ReferenceGraph
        import sqvm.storage.operations as operations_module
        monkeypatch.setattr(service, "_reference_graph", lambda: CatalogReferenceGraph((), False, ()))
        monkeypatch.setattr(operations_module, "build_reference_graph", lambda **_kwargs: ReferenceGraph((), False, ()))
        row = service.overview()["items"][0]
        catalog = storage / "catalog.sqlite"
        first_mtime = catalog.stat().st_mtime_ns
        second_get = service.overview()
        assert second_get["catalog_revision"] == row["catalog_revision"]
        assert catalog.stat().st_mtime_ns == first_mtime
        request = {"actor_id": "web.test", "expected_catalog_revision": row["catalog_revision"],
                   "expected_workflow_sha256": row["workflow_sha256"], "reason": "Web retention test", "keep": True}
        keep_result = service.mutate(scan.run_id, "keep", request)
        assert keep_result["catalog_revision"] == row["catalog_revision"] + 1
        kept = keep_result["item"]
        assert kept["retention_state"] == "manual_keep" and kept["storage_state"] == "hot"
        release = {**request, "expected_catalog_revision": kept["catalog_revision"], "keep": False}
        release_result = service.mutate(scan.run_id, "keep", release)
        assert release_result["catalog_revision"] == kept["catalog_revision"] + 1
        unkept = release_result["item"]
        assert unkept["retention_state"] == "normal" and unkept["storage_state"] == "hot"
        original_same_volume = operations_module._same_volume
        def simulate_cross_volume(source, destination):
            source = Path(source)
            if source == scan.root or source.name == "payload":
                return False
            return original_same_volume(source, destination)
        monkeypatch.setattr(operations_module, "_same_volume", simulate_cross_volume)
        trash_result = service.mutate(scan.run_id, "trash", {"actor_id": "web.test",
            "expected_catalog_revision": unkept["catalog_revision"], "expected_workflow_sha256": unkept["workflow_sha256"],
            "reason": "Web recoverable cross-volume delete"})
        assert trash_result["catalog_revision"] == unkept["catalog_revision"] + 1
        trashed = trash_result["item"]
        assert trashed["storage_state"] == "trash" and "restore" in trashed["allowed_actions"], trashed
        assert "trash_carrier_invalid" not in trashed["blockers"]
        assert (storage / "trash" / scan.run_id / "payload").exists()
        trash_mtime = catalog.stat().st_mtime_ns
        assert service.run(scan.run_id)["catalog_revision"] == trashed["catalog_revision"]
        assert service.run(scan.run_id)["catalog_revision"] == trashed["catalog_revision"]
        assert catalog.stat().st_mtime_ns == trash_mtime
        restored_result = service.mutate(scan.run_id, "restore", {"actor_id": "web.test",
            "expected_catalog_revision": trashed["catalog_revision"], "expected_workflow_sha256": trashed["workflow_sha256"],
            "reason": "Web restore original carrier"})
        assert restored_result["catalog_revision"] == trashed["catalog_revision"] + 1
        restored = restored_result["item"]
        assert restored["storage_state"] == "hot" and (hot / scan.root.name).is_dir()
        assert not (storage / "trash" / scan.run_id).exists()
        assert not any(key.endswith("_path") for key in restored.get("carrier", {}))
    finally:
        shutil.rmtree(writer, ignore_errors=True)


@pytest.mark.parametrize("statement", (
    "import sqvm.storage.references",
    "import sqvm.calibration.spectroscopy",
    "import sqvm.web",
    "import sqvm.storage.references; import sqvm.web; import sqvm.calibration.spectroscopy",
    "import sqvm.web; import sqvm.calibration.spectroscopy; import sqvm.storage.references",
))
def test_import_order_isolated_subprocess_has_no_web_storage_cycle(statement: str) -> None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([sys.executable, "-c", statement], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def _http_json(url: str, *, method: str = "GET", payload=None):
    raw = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        method=method,
        data=raw,
        headers={"Content-Type": "application/json"} if raw is not None else {},
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_workbench_diff_uses_backend_requalification_field():
    source = (ROOT / "src" / "sqvm" / "web" / "static" / "app.js").read_text("utf-8")
    assert "result.requires_requalification" in source
    assert "result.requalification ?" not in source
    assert '<details class="diff-group">' in source
    assert 'class="parameter-folders"' in source
    assert 'class="data-type"' in source
    assert '"list[float]"' in source
    assert 'coupling_detune_GHz`, xs' in source
    assert 'zbias_offset_phi0`, ys' in source
    assert "新增配对点" not in source
    assert 'validation.status === "valid"' in source
    assert "配置校验通过" in source
    assert 'aria-label="${esc(accessibleLabel)}"' in source
    assert '<label for="${esc(id)}">' in source

    styles = (ROOT / "src" / "sqvm" / "web" / "static" / "styles.css").read_text("utf-8")
    assert ".field-errors[hidden] { display: none; }" in styles
    assert ".parameter-field" in styles
    assert ".parameter-folder" in styles


def test_snapshot_workbench_exposes_detailed_values_as_read_only_controls():
    source = (ROOT / "src" / "sqvm" / "web" / "static" / "app.js").read_text("utf-8")
    assert "readonlyControlWorkbench(editable.control_values || {})" in source
    assert "readonlySurface(settingGroups(settings, [target], false))" in source
    assert 'readonlySurface(mapperObjectGroups(mappers, "G2ZBIAS_MAPPER", "C"))' in source
    assert '<fieldset class="readonly-control-surface" disabled>' in source

    styles = (ROOT / "src" / "sqvm" / "web" / "static" / "styles.css").read_text("utf-8")
    assert ".readonly-control-surface input:disabled" in styles


def test_spectroscopy_view_exports_json_and_primitive_population_csv():
    source = (ROOT / "src" / "sqvm" / "web" / "static" / "app.js").read_text("utf-8")
    assert 'id="export-experiment-json"' in source
    assert 'id="export-experiment-csv"' in source
    assert '"population_000"' in source
    assert '"circuit_receipt_sha256"' in source
    assert "function spectroscopyCsv(detail)" in source
    assert "function installUnifiedPlots(specs)" in source
    assert "function drawXYPlot(" in source
    assert "function drawHeatmapPlot(" in source
    assert 'data-plot-filter="${esc(type)}"' in source
    assert "function selectPlotPoint(" in source
    assert "function schedulePlotHover(" in source
    assert "function showPlotHover(" in source
    assert "}, 300);" in source
    assert "function drawSpectroscopyChart(" not in source
    assert 'class="point-list-grid"' in source
    assert "function targetPointList(detail, target)" in source
    assert 'pointSeriesList("频率（GHz）", "frequency_GHz", frequencies)' in source
    assert 'pointSeriesList("P1", "P1", populations)' in source
    assert "function pointListItem(" not in source
    assert "pointTable(detail)" not in source
    assert '"synthetic-demo": "合成演示数据"' in source
