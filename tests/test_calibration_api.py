from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

import pytest

from sqvm.calibration.api import (
    CalibrationExperimentError,
    run_active_qubit_spectroscopy_calibration,
)
from sqvm.web import CalibrationWebIndex, PlatformConfigurationStore
from test_spectroscopy_calibration_workflow import _install_synthetic_runner, _request


ROOT = Path(__file__).resolve().parents[1]


def _active_store(base: Path) -> PlatformConfigurationStore:
    store = PlatformConfigurationStore(ROOT, base / "platform-configurations")
    legacy = json.loads(
        (ROOT / "configs/calibration/platform_uncalibrated_v1.json").read_text("utf-8")
    )
    draft = store.create_draft(
        store.bootstrap_configuration(legacy),
        actor_id="project.manager",
        name="Active spectroscopy API",
    )
    draft = store.initialize_calibration_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
    )
    validation = store.validate_draft(
        draft["draft_id"],
        actor_id="project.manager",
    )
    assert validation["status"] == "valid"
    snapshot = store.publish_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name="Active spectroscopy API",
        reason="end-to-end API test",
    )
    store.set_active(
        snapshot["snapshot_id"],
        actor_id="project.manager",
        confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}",
    )
    return store


def test_active_spectroscopy_api_publishes_web_visible_data(monkeypatch):
    base = ROOT / "tmp" / f"calibration_api_{uuid.uuid4().hex}"
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    store = _active_store(base)
    try:
        run = run_active_qubit_spectroscopy_calibration(
            _request(),
            output_root=base / "experiments",
            configuration_storage_root=store.root,
            repository_root=ROOT,
            timeout_s=10.0,
        )

        assert run.root.parent == base / "experiments"
        assert len(calls) == 4
        assert all(
            call["execution_profile"] == "calibration_scan"
            for call in calls
        )
        workflow = json.loads((run.root / "workflow.json").read_text("utf-8"))
        assert workflow["created_utc"].endswith("Z")
        assert workflow["parent_calibration"]["path"].startswith(
            base.relative_to(ROOT).as_posix()
        )

        index = CalibrationWebIndex(ROOT, base)
        summary = index.experiments()[0]
        detail = index.experiment(run.run_id)
        assert summary["created_utc"] == workflow["created_utc"]
        assert detail["renderer"] == "qubit_spectroscopy"
        assert len(detail["datasets"]["refined"]["points"]) == 5
        assert index.experiment_asset(run.run_id, "spectroscopy.png")[0].is_file()
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_active_spectroscopy_api_rejects_missing_active_configuration():
    base = ROOT / "tmp" / f"calibration_api_missing_{uuid.uuid4().hex}"
    try:
        with pytest.raises(CalibrationExperimentError, match="exactly one Active"):
            run_active_qubit_spectroscopy_calibration(
                _request(),
                output_root=base / "experiments",
                configuration_storage_root=base / "platform-configurations",
                repository_root=ROOT,
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_top_level_active_spectroscopy_api_is_lazy_export():
    import sqvm
    import sqvm.calibration as calibration
    import sqvm.calibration_api as legacy_api
    import sqvm.experiments as legacy_experiments

    assert (
        sqvm.run_active_qubit_spectroscopy_calibration
        is run_active_qubit_spectroscopy_calibration
    )
    assert (
        calibration.run_active_qubit_spectroscopy_calibration
        is legacy_api.run_active_qubit_spectroscopy_calibration
    )
    assert calibration.SpectroscopyRequest is legacy_experiments.SpectroscopyRequest
