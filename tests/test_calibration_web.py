from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

import pytest

import sqvm.experiments.spectroscopy as spectroscopy_module
from sqvm.experiments import run_qubit_spectroscopy_calibration
from sqvm.web import (
    CalibrationWebIndex,
    ConfigurationManagementError,
    PlatformConfigurationStore,
    create_calibration_web_server,
)
from test_qubit_spectroscopy import _context
from test_spectroscopy_calibration_workflow import (
    PARENT,
    _install_synthetic_runner,
    _request,
)


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
    asset, content_type = index.experiment_asset(run.run_id, "spectroscopy.png")
    assert asset.is_file() and content_type == "image/png"


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
    assert validation["status"] == "invalid"
    assert validation["field_errors"]


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
    store.delete_snapshot(parent["snapshot_id"], actor_id="project.manager")


def test_http_api_serves_console_and_configuration_mutations(web_workspace):
    _base, output, storage, run = web_workspace
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
                "targets": ["Q1", "Q2"],
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
    assert 'class="setting-grid mapper-grid"' in source

    styles = (ROOT / "src" / "sqvm" / "web" / "static" / "styles.css").read_text("utf-8")
    assert ".field-errors[hidden] { display: none; }" in styles
    assert ".mapper-grid { grid-template-columns: minmax(0, 1fr); }" in styles
