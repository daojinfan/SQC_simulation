from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import uuid

import pytest

from sqvm.calibration.api import (
    CalibrationExperimentError,
    SpectroscopyRun,
    apply_calibration_candidates_to_current_configuration,
    apply_spectroscopy_candidates_to_current_configuration,
    run_active_qubit_spectroscopy_calibration,
    run_spectroscopy,
)
from sqvm.calibration.spectroscopy_run import (
    SpectroscopyRunError,
    verify_qubit_spectroscopy_scan,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
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

        with pytest.raises(CalibrationExperimentError, match="confirmation phrase"):
            apply_calibration_candidates_to_current_configuration(
                run,
                confirmation_phrase="APPLY",
                configuration_storage_root=store.root,
                repository_root=ROOT,
            )

        update = apply_calibration_candidates_to_current_configuration(
            run,
            confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
            targets=("Q1", "Q2"),
            configuration_storage_root=store.root,
            repository_root=ROOT,
        )
        assert update.targets == ("Q1", "Q2")
        assert update.requires_requalification is False
        current = store.current_configuration("demo_2q1c2r")
        assert current["source_candidate"]["experiment_run_id"] == run.run_id
        qagents = current["editable"]["calibration_values"]["qagents"]
        assert qagents["Q1"]["reference_frequency_authority"]["reference_frequency_GHz"] == pytest.approx(
            run.candidates["Q1"]["proposed_frequency_GHz"]
        )
        assert qagents["Q2"]["reference_frequency_authority"]["reference_frequency_GHz"] == pytest.approx(
            run.candidates["Q2"]["proposed_frequency_GHz"]
        )
        active = store.active_configurations()
        assert len(active) == 1
        assert active[0]["snapshot_id"] == current["source_snapshot_id"]
        effective_qagents = store.snapshot(active[0]["snapshot_id"])["editable"][
            "calibration_values"
        ]["qagents"]
        assert effective_qagents["Q1"]["reference_frequency_authority"][
            "reference_frequency_GHz"
        ] == pytest.approx(run.candidates["Q1"]["proposed_frequency_GHz"])
        with pytest.raises(CalibrationExperimentError, match="stale"):
            apply_calibration_candidates_to_current_configuration(
                run,
                confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
                configuration_storage_root=store.root,
                repository_root=ROOT,
            )

        snapshot = store.snapshot_current_configuration(
            "demo_2q1c2r",
            actor_id="notebook.user",
            expected_content_sha256=current["content_sha256"],
            name="Notebook spectroscopy update",
            reason="verify experiment candidate provenance",
        )
        published_qagents = snapshot["editable"]["calibration_values"]["qagents"]
        assert published_qagents["Q1"]["reference_frequency_authority"]["calibration_run_id"] == run.run_id
        assert published_qagents["Q2"]["reference_frequency_authority"]["calibration_run_id"] == run.run_id

        current = store.current_configuration("demo_2q1c2r")
        edited = copy.deepcopy(current["editable"])
        edited["calibration_values"]["qagents"]["Q1"][
            "reference_frequency_authority"
        ]["reference_frequency_GHz"] += 0.01
        current = store.update_current_configuration(
            "demo_2q1c2r",
            actor_id="notebook.user",
            expected_content_sha256=current["content_sha256"],
            name=current["name"],
            note="manual correction after spectroscopy",
            editable=edited,
        )
        assert current["source_candidate"]["targets"] == ["Q2"]
        corrected = store.snapshot_current_configuration(
            "demo_2q1c2r",
            actor_id="notebook.user",
            expected_content_sha256=current["content_sha256"],
            name="Manual correction",
            reason="verify stale candidate provenance is removed",
        )
        corrected_qagents = corrected["editable"]["calibration_values"]["qagents"]
        assert corrected_qagents["Q1"]["reference_frequency_authority"][
            "calibration_run_id"
        ].startswith("manual_")
        assert corrected_qagents["Q2"]["reference_frequency_authority"][
            "calibration_run_id"
        ] == run.run_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_run_spectroscopy_builds_the_scan_from_ranges_and_step(monkeypatch):
    base = ROOT / "tmp" / f"simple_spectroscopy_api_{uuid.uuid4().hex}"
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    store = _active_store(base)
    try:
        run = run_spectroscopy(
            {
                "Q2": (5.0, 5.4),
                "Q1": (4.8, 5.2),
            },
            frequency_step_GHz=0.2,
            output_root=base / "experiments",
            configuration_storage_root=store.root,
            repository_root=ROOT,
            timeout_s=10.0,
        )

        assert isinstance(run, SpectroscopyRun)
        assert run.analysis.recommendation_eligible is False
        assert run.recommendation_eligible is True
        assert [len(call["circuits"]) for call in calls] == [3]
        workflow = json.loads((run.root / "workflow.json").read_text("utf-8"))
        scan = workflow["request"]
        assert workflow["workflow_id"] == "qubit_spectroscopy_scan_v1"
        assert workflow["artifact_version"] == "0.3"
        assert workflow["recommendation_eligible"] is True
        assert scan["run_phase"] == "scan"
        assert scan["targets"] == ["Q1", "Q2"]
        assert scan["execution_mode"] == "parallel_lockstep"
        assert scan["axes"] == [
            {"qagent": "Q1", "frequencies_GHz": [4.8, 5.0, 5.2]},
            {"qagent": "Q2", "frequencies_GHz": [5.0, 5.2, 5.4]},
        ]
        assert scan["pulse_policies"][0]["length_samples"] == 32
        assert scan["pulse_policies"][0]["amplitude_GHz"] == 0.02
        assert list(run.data["Q1"]["frequency_GHz"]) == [4.8, 5.0, 5.2]
        assert len(run.data["Q1"]["P0"]) == 3
        assert len(run.data["Q1"]["P1"]) == 3
        assert set(run.candidates) == {"Q1", "Q2"}
        assert run.candidates["Q1"]["proposed_frequency_GHz"] == pytest.approx(5.0)
        assert run.candidates["Q2"]["proposed_frequency_GHz"] == pytest.approx(5.2)
        assert all(
            candidate["recommendation_eligible"] for candidate in run.candidates.values()
        )
        assert verify_qubit_spectroscopy_scan(run.root)
        assert all(
            point.circuit_result.evidence_root.is_relative_to(run.root)
            for point in run.dataset.points
        )

        index = CalibrationWebIndex(ROOT, base)
        detail = index.experiment(run.run_id)
        assert detail["renderer"] == "qubit_spectroscopy_scan"
        assert set(detail["datasets"]) == {"scan"}
        assert [row["id"] for row in detail["plot_specs"][0]["groups"]] == ["scan"]
        assert len(detail["candidates"]) == 2
        assert detail["recommendation_applicable"] is True
        assert detail["recommendation_eligible"] is True

        update = apply_calibration_candidates_to_current_configuration(
            run,
            confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
            candidate_ids=("Q1.reference_frequency_GHz", "Q2.reference_frequency_GHz"),
            configuration_storage_root=store.root,
            repository_root=ROOT,
        )
        assert update.targets == ("Q1", "Q2")
        assert update.candidate_ids == (
            "Q1.reference_frequency_GHz",
            "Q2.reference_frequency_GHz",
        )
        assert update.calibration_subjects == ("Q1", "Q2")
        assert update.configuration_targets == ("Q1", "Q2")
        assert update.applied_values[
            "calibration_values.qagents.Q2.reference_frequency_authority.reference_frequency_GHz"
        ] == pytest.approx(5.2)

        dataset_path = run.root / "dataset.json"
        dataset_path.write_bytes(dataset_path.read_bytes() + b" ")
        with pytest.raises(SpectroscopyRunError, match="not canonical"):
            verify_qubit_spectroscopy_scan(run.root)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_run_spectroscopy_rejects_a_range_not_divisible_by_the_step():
    base = ROOT / "tmp" / f"simple_spectroscopy_invalid_{uuid.uuid4().hex}"
    store = _active_store(base)
    try:
        with pytest.raises(CalibrationExperimentError, match="not exactly divisible"):
            run_spectroscopy(
                {"Q1": (4.8, 5.25)},
                frequency_step_GHz=0.2,
                output_root=base / "experiments",
                configuration_storage_root=store.root,
                repository_root=ROOT,
            )
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_run_spectroscopy_accepts_a_single_target_with_an_even_point_count(monkeypatch):
    base = ROOT / "tmp" / f"simple_spectroscopy_single_{uuid.uuid4().hex}"
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    store = _active_store(base)
    try:
        run = run_spectroscopy(
            {"Q1": (4.8, 5.1)},
            frequency_step_GHz=0.1,
            output_root=base / "experiments",
            configuration_storage_root=store.root,
            repository_root=ROOT,
            timeout_s=10.0,
        )

        assert run.analysis.recommendation_eligible is False
        assert run.recommendation_eligible is True
        assert [len(call["circuits"]) for call in calls] == [4]
        workflow = json.loads((run.root / "workflow.json").read_text("utf-8"))
        scan = workflow["request"]
        assert scan["execution_mode"] == "single"
        assert scan["axes"][0]["frequencies_GHz"] == [4.8, 4.9, 5.0, 5.1]
        assert run.candidates["Q1"]["recommendation_eligible"] is True
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_scan_verifier_binds_generic_candidate_parameter_path(monkeypatch):
    base = ROOT / "tmp" / f"candidate_binding_{uuid.uuid4().hex}"
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    store = _active_store(base)
    try:
        run = run_spectroscopy(
            {"Q1": (4.8, 5.2)},
            frequency_step_GHz=0.2,
            output_root=base / "experiments",
            configuration_storage_root=store.root,
            repository_root=ROOT,
            timeout_s=10.0,
        )
        workflow_path = run.root / "workflow.json"
        receipt_path = run.root / "receipt.json"
        workflow = json.loads(workflow_path.read_text("utf-8"))
        workflow["candidates"][0]["changes"][0]["parameter_path"] = (
            "control_values.idle_flux_phi0.q1"
        )
        workflow_raw = canonical_json_bytes(workflow)
        workflow_path.write_bytes(workflow_raw)
        receipt = json.loads(receipt_path.read_text("utf-8"))
        receipt["workflow_sha256"] = hashlib.sha256(workflow_raw).hexdigest().upper()
        receipt_path.write_bytes(canonical_json_bytes(receipt))

        with pytest.raises(SpectroscopyRunError, match="candidate binding"):
            verify_qubit_spectroscopy_scan(run.root)
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
    assert sqvm.run_spectroscopy is run_spectroscopy
    assert sqvm.SpectroscopyRun is SpectroscopyRun
    assert (
        sqvm.apply_calibration_candidates_to_current_configuration
        is apply_calibration_candidates_to_current_configuration
    )
    assert (
        sqvm.apply_spectroscopy_candidates_to_current_configuration
        is apply_spectroscopy_candidates_to_current_configuration
    )
    assert (
        calibration.run_active_qubit_spectroscopy_calibration
        is legacy_api.run_active_qubit_spectroscopy_calibration
    )
    assert calibration.run_spectroscopy is legacy_api.run_spectroscopy
    assert calibration.SpectroscopyRequest is legacy_experiments.SpectroscopyRequest
