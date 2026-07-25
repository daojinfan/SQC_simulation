from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

from sqvm.candidate_protocol import calibration_candidate, parameter_change

import sqvm.circuits as circuits_module
import sqvm.web.configuration as configuration_module
from sqvm.circuits import QCISCircuit, run_circuits
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.web import (
    ConfigurationManagementError,
    PlatformAuthorityResolutionError,
    PlatformConfigurationStore,
)
from sqvm.web.configuration_schema import validate_editable
from sqvm.web.configuration_schema import project_wave_indices
from sqvm.web.configuration_transactions import ConfigurationTransactionManager
from sqvm.runtime.calibration_model import calibration_model_configuration_sha256


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def platform_root():
    root = ROOT / "tmp" / f".platform-v02.{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _draft_record(setting_id: str, target: str, gate_type: str, **payload):
    return {
        "setting_id": setting_id, "target": target, "gate_type": gate_type,
        "status": "draft", "base_revision": 0, "base_setting_hash": None,
        "revision": None, "setting_hash": None, "calibration_run_id": None, **payload,
    }


def _draft_mapper(mapper_id: str, target: str, mapper_type: str, **payload):
    return {
        "mapper_id": mapper_id, "target": target, "mapper_type": mapper_type,
        "status": "draft", "base_revision": 0, "base_setting_hash": None,
        "revision": None, "setting_hash": None, "calibration_run_id": None, **payload,
    }


def test_configuration_replace_retries_transient_windows_sharing_failure():
    calls = []
    sleeps = []

    def replace(source, destination):
        calls.append((source, destination))
        if len(calls) < 3:
            error = PermissionError("sharing violation")
            error.winerror = 32
            raise error

    source = Path("source.tmp")
    destination = Path("configuration.json")
    configuration_module._replace_configuration_file(
        source,
        destination,
        replacer=replace,
        platform_name="nt",
        sleeper=sleeps.append,
    )

    assert calls == [(source, destination)] * 3
    assert sleeps == [0.01, 0.02]


@pytest.mark.parametrize("platform_name,error_code", [("posix", 32), ("nt", 2)])
def test_configuration_replace_does_not_retry_other_errors(
    platform_name,
    error_code,
):
    sleeps = []

    def replace(_source, _destination):
        error = PermissionError("not transient")
        error.winerror = error_code
        raise error

    with pytest.raises(PermissionError, match="not transient"):
        configuration_module._replace_configuration_file(
            Path("source.tmp"),
            Path("configuration.json"),
            replacer=replace,
            platform_name=platform_name,
            sleeper=sleeps.append,
        )

    assert sleeps == []


def test_configuration_replace_stops_after_bounded_attempts():
    calls = 0
    sleeps = []

    def replace(_source, _destination):
        nonlocal calls
        calls += 1
        error = PermissionError("still locked")
        error.winerror = 5
        raise error

    with pytest.raises(PermissionError, match="still locked"):
        configuration_module._replace_configuration_file(
            Path("source.tmp"),
            Path("configuration.json"),
            replacer=replace,
            platform_name="nt",
            sleeper=sleeps.append,
        )

    assert calls == configuration_module._CONFIGURATION_REPLACE_ATTEMPTS
    assert len(sleeps) == calls - 1


def _reference(frequency: float):
    return {
        "reference_frequency_GHz": frequency, "frequency_source": "bootstrap_seed",
        "status": "draft", "base_revision": 0, "base_setting_hash": None,
        "calibration_run_id": None, "revision": None, "setting_hash": None,
    }


def typed_calibration() -> dict:
    settings = {}
    for target, prefix in (("Q1", "q1"), ("Q2", "q2")):
        settings[f"{prefix}_xy"] = _draft_record(f"{prefix}_xy", target, "XY", transition="01", waveform_class="rectangle", length_samples=2, amplitude_GHz=0.1, phase_offset_rad=0.0, dragAlpha_samples=0.0, width_samples=2)
        settings[f"{prefix}_xy2"] = _draft_record(f"{prefix}_xy2", target, "XY2", transition="01", waveform_class="rectangle", length_samples=2, amplitude_GHz=0.1, phase_offset_rad=0.0, dragAlpha_samples=0.0, width_samples=2)
        settings[f"{prefix}_xy12"] = _draft_record(f"{prefix}_xy12", target, "X12", transition="12", waveform_class="rectangle", length_samples=2, amplitude_GHz=0.05, phase_offset_rad=0.0, dragAlpha_samples=0.0, width_samples=2)
        settings[f"{prefix}_dtn"] = _draft_record(f"{prefix}_dtn", target, "DTN", control_role="z", envelope_class="rect", input_unit="phi_over_phi0")
    waveform = {"waveform_class": "rectangle", "width_samples": 2, "flux_offset_phi0": 0.0}
    for gate in ("cz", "fsim"):
        settings[f"c_{gate}"] = _draft_record(f"c_{gate}", "C", gate.upper() if gate == "cz" else "FSIM", duration_samples=2, use_f012zbias_mapper=False, use_g2zbias_mapper=False, waveforms={"q0": dict(waveform), "q1": dict(waveform), "coupler": dict(waveform)}, q0_calibrated_dynamic_phase_rad=0.0, q1_calibrated_dynamic_phase_rad=0.0)
    return {
        "qagents": {"Q1": {"reference_frequency_authority": _reference(5.0)}, "Q2": {"reference_frequency_authority": _reference(5.2)}},
        "gate_configuration": {
            "Q1": {"active_xy_setting": "q1_xy", "active_xy2_setting": "q1_xy2", "active_xy12_setting": "q1_xy12", "active_detune_setting": "q1_dtn", "active_f012zbias_mapper": "q1_f012", "xy_pi_impl": False, "z_gate_impl": "VIRTUAL"},
            "Q2": {"active_xy_setting": "q2_xy", "active_xy2_setting": "q2_xy2", "active_xy12_setting": "q2_xy12", "active_detune_setting": "q2_dtn", "active_f012zbias_mapper": "q2_f012", "xy_pi_impl": False, "z_gate_impl": "VIRTUAL"},
            "C": {"active_cz_setting": "c_cz", "active_fsim_setting": "c_fsim", "active_g2zbias_mapper": "c_g2"},
        },
        "waveform_registry": {"settings": settings, "mappers": {
            "q1_f012": _draft_mapper("q1_f012", "Q1", "F012ZBIAS_MAPPER", f01max_GHz=6.0, k_rad_per_phi0=1.0, idle_flux_offset_phi0=0.0),
            "q2_f012": _draft_mapper("q2_f012", "Q2", "F012ZBIAS_MAPPER", f01max_GHz=6.0, k_rad_per_phi0=1.0, idle_flux_offset_phi0=0.0),
            "c_g2": _draft_mapper("c_g2", "C", "G2ZBIAS_MAPPER", coupling_detune_GHz=[-0.1, 0.0, 0.1], zbias_offset_phi0=[-0.1, 0.0, 0.1], interpolation="piecewise_linear", extrapolation="reject"),
        }},
        "fsim_characterizations": {},
    }


def _legacy():
    return {"state_id": "uncalibrated", "device_snapshot": "configs/devices/2q1c2r.yaml", "values": {}}


def _published_store(tmp_path: Path):
    store = PlatformConfigurationStore(ROOT, tmp_path / "platform-configurations")
    draft = store.create_draft(store.bootstrap_configuration(_legacy()), actor_id="project.manager", name="Typed v0.2")
    draft = store.initialize_calibration_draft(draft["draft_id"], actor_id="project.manager", expected_content_sha256=draft["content_sha256"])
    draft = store.update_draft(draft["draft_id"], actor_id="project.manager", expected_content_sha256=draft["content_sha256"], name=draft["name"], note="complete typed calibration", editable=draft["editable"])
    assert store.validate_draft(draft["draft_id"], actor_id="project.manager")["status"] == "valid"
    snapshot = store.publish_draft(draft["draft_id"], actor_id="project.manager", expected_content_sha256=draft["content_sha256"], name="Typed v0.2", reason="test publication")
    return store, snapshot


def _committed_projection(store: PlatformConfigurationStore) -> Path:
    return ConfigurationTransactionManager(store.root).committed_view(
        "demo_2q1c2r"
    ).projection_root


def test_configuration_delete_rejects_real_symlink_without_touching_external_target(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "platform-configurations")
    draft = store.create_draft(
        store.bootstrap_configuration(_legacy()),
        actor_id="project.manager",
        name="Linked draft",
    )
    draft_root = store.drafts_root / draft["draft_id"]
    external = platform_root / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("must remain", encoding="utf-8")
    linked = draft_root / "external-link"
    os.symlink(external, linked, target_is_directory=True)

    with pytest.raises(ConfigurationManagementError, match="linked|reparse"):
        store.delete_draft(draft["draft_id"], actor_id="project.manager")

    assert sentinel.read_text(encoding="utf-8") == "must remain"
    assert linked.is_symlink()
    assert draft_root.is_dir()


def test_configuration_snapshot_delete_rejects_real_symlink_without_touching_external_target(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    snapshot_root = _committed_projection(store) / "snapshots" / snapshot["snapshot_id"]
    external = platform_root / "external-snapshot"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("must remain", encoding="utf-8")
    linked = snapshot_root / "external-link"
    os.symlink(external, linked, target_is_directory=True)

    with pytest.raises(ConfigurationManagementError, match="linked|reparse"):
        store.delete_snapshot(snapshot["snapshot_id"], actor_id="project.manager")

    assert sentinel.read_text(encoding="utf-8") == "must remain"
    assert linked.is_symlink()
    assert snapshot_root.is_dir()


def test_configuration_delete_rejects_hardlinked_file(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "platform-configurations")
    draft = store.create_draft(
        store.bootstrap_configuration(_legacy()),
        actor_id="project.manager",
        name="Hardlinked draft",
    )
    draft_root = store.drafts_root / draft["draft_id"]
    external = platform_root / "outside.json"
    external.write_text('{"outside":true}', encoding="utf-8")
    linked = draft_root / "outside-link.json"
    os.link(external, linked)
    before_links = external.stat().st_nlink

    with pytest.raises(ConfigurationManagementError, match="hardlink"):
        store.delete_draft(draft["draft_id"], actor_id="project.manager")

    assert external.read_text(encoding="utf-8") == '{"outside":true}'
    assert external.stat().st_nlink == before_links
    assert linked.exists()
    assert draft_root.is_dir()


def test_configuration_delete_rechecks_identity_before_recursive_removal(platform_root: Path, monkeypatch):
    store = PlatformConfigurationStore(ROOT, platform_root / "platform-configurations")
    draft = store.create_draft(
        store.bootstrap_configuration(_legacy()),
        actor_id="project.manager",
        name="Replacement draft",
    )
    draft_root = store.drafts_root / draft["draft_id"]
    draft_json = draft_root / "draft.json"
    external = platform_root / "outside-draft.json"
    external.write_text('{"outside":true}', encoding="utf-8")
    original_unlink = configuration_module.os.unlink
    injected = False

    def replace_after_preflight(path, *args, **kwargs):
        nonlocal injected
        if not injected and Path(path).name == "00000000.json":
            injected = True
            original_unlink(draft_json)
            os.symlink(external, draft_json)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(configuration_module.os, "unlink", replace_after_preflight)
    with pytest.raises(ConfigurationManagementError, match="changed|linked|reparse"):
        store.delete_draft(draft["draft_id"], actor_id="project.manager")

    assert injected
    assert external.read_text(encoding="utf-8") == '{"outside":true}'
    assert os.path.lexists(draft_json)
    assert draft_root.exists()


def test_configuration_delete_removes_normal_draft_and_snapshot(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "platform-configurations")
    draft = store.create_draft(
        store.bootstrap_configuration(_legacy()),
        actor_id="project.manager",
        name="Delete draft",
    )
    draft_root = store.drafts_root / draft["draft_id"]
    store.delete_draft(draft["draft_id"], actor_id="project.manager")
    assert not draft_root.exists()

    published_store, _current_snapshot = _published_store(platform_root / "published")
    draft = published_store.draft(published_store.drafts()[0]["draft_id"])
    snapshot = published_store.publish_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name="Unreferenced snapshot",
        reason="exercise snapshot deletion",
    )
    snapshot_root = published_store.snapshots_root / snapshot["snapshot_id"]
    published_store.delete_snapshot(snapshot["snapshot_id"], actor_id="project.manager")
    assert not snapshot_root.exists()


def test_schema_rejects_unknown_generated_and_physical_fields(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "configs")
    draft = store.create_draft(store.bootstrap_configuration(_legacy()), actor_id="project.manager", name="Schema")
    editable = draft["editable"]
    editable["control_values"]["dac"]["offset_V"] = 0.0
    with pytest.raises(ConfigurationManagementError) as captured:
        store.update_draft(draft["draft_id"], actor_id="project.manager", expected_content_sha256=draft["content_sha256"], name="Schema", note="bad", editable=editable)
    assert captured.value.field_errors[0]["path"].endswith("offset_V")
    typed = typed_calibration()
    typed["waveform_registry"]["settings"]["q1_xy"]["wave_index"] = 0
    assert any(row["path"].endswith("wave_index") for row in validate_editable({"control_values": draft["editable"]["control_values"], "calibration_values": typed}, published=False))


def test_web_simulation_truncation_defaults_to_75_and_is_editable(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "configs")
    draft = store.create_draft(
        store.bootstrap_configuration(_legacy()),
        actor_id="project.manager",
        name="Simulation truncation",
    )
    model = draft["editable"]["control_values"]["simulation"]["calibration_model"]
    assert model["charge_cutoffs"] == {"q1": 7, "c": 7, "q2": 7}
    assert model["retained_energy_levels"] == {"q1": 5, "c": 3, "q2": 5}
    assert np.prod(list(model["retained_energy_levels"].values())) == 75

    editable = copy.deepcopy(draft["editable"])
    editable["control_values"]["simulation"]["calibration_model"][
        "retained_energy_levels"
    ]["c"] = 4
    editable["control_values"]["idle_flux_phi0"]["q1"] = 0.2
    updated = store.update_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=draft["content_sha256"],
        name=draft["name"],
        note="increase coupler levels",
        editable=editable,
    )
    assert updated["validation"]["status"] == "not_validated"
    assert validate_editable(updated["editable"], published=False) == []
    assert np.prod(list(updated["editable"]["control_values"]["simulation"]["calibration_model"]["retained_energy_levels"].values())) == 100


def test_web_simulation_truncation_rejects_invalid_convergence(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "configs")
    draft = store.create_draft(
        store.bootstrap_configuration(_legacy()),
        actor_id="project.manager",
        name="Invalid simulation truncation",
    )
    editable = copy.deepcopy(draft["editable"])
    editable["control_values"]["simulation"]["calibration_model"][
        "convergence_retained_energy_levels"
    ]["q1"] = 4
    errors = validate_editable(editable, published=False)
    assert any(
        row["path"].endswith("convergence_retained_energy_levels.q1")
        and row["code"] == "range"
        for row in errors
    )


def test_waveform_class_projection_preserves_published_hashes():
    calibration = typed_calibration()
    calibration["waveform_registry"]["settings"]["q1_xy"]["waveform_class"] = "flattop"
    setting = calibration["waveform_registry"]["settings"]["q1_xy"]
    setting.pop("width_samples"); setting["edge_samples"] = 1
    composite = calibration["waveform_registry"]["settings"]["c_cz"]
    composite["waveforms"]["q0"] = {"waveform_class": "acz", "parameters": {"thf": 1.0, "thi": 1.0, "lam2": 0.0, "lam3": 0.0}, "flux_offset_phi0": 0.0}
    # The resolver projects these only after publication; persisted hash identity has no wave_index.
    projected = project_wave_indices(calibration)
    assert projected["waveform_registry"]["settings"]["q1_xy"]["wave_index"] == 2
    assert projected["waveform_registry"]["settings"]["c_cz"]["waveforms"]["q0"]["wave_index"] == 5
    assert "wave_index" not in calibration["waveform_registry"]["settings"]["q1_xy"]


def test_nested_schema_rejects_acz_composite_fsim_and_formal_mixing_drift(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "configs")
    draft = store.create_draft(store.bootstrap_configuration(_legacy()), actor_id="project.manager", name="Nested")
    draft = store.initialize_calibration_draft(draft["draft_id"], actor_id="project.manager", expected_content_sha256=draft["content_sha256"])
    editable = draft["editable"]
    cz = editable["calibration_values"]["waveform_registry"]["settings"]["c_cz"]
    cz["waveforms"]["q0"] = {"waveform_class": "acz", "parameters": {"thf": 1.0, "thi": 1.0, "lam2": 0.0, "lam3": 0.0, "extra": 1}, "flux_offset_phi0": 0.0, "dac_offset": 0.0}
    editable["control_values"]["static_mixing"]["z"]["input_lanes"] = ["c_z", "q2_z", "q1_z"]
    editable["calibration_values"]["fsim_characterizations"] = {"fsim_1": {"characterization_run_id": "fsim_1", "source": "run", "theta_rad": 0.1, "zeta_rad": 0.1, "chi_rad": 0.1, "gamma_rad": 0.1, "phi_rad": 0.1, "metrics": [{"metric_type": "qpt_process_fidelity", "value": 2.0}], "leakage": 0.0, "method": "qpt", "status": "accepted"}}
    errors = validate_editable(editable, published=False)
    paths = {row["path"] for row in errors}
    assert "$.control_values.static_mixing.z" in paths
    assert "$.calibration_values.waveform_registry.settings.c_cz.waveforms.q0.dac_offset" in paths
    assert "$.calibration_values.waveform_registry.settings.c_cz.waveforms.q0.parameters.extra" in paths
    assert "$.calibration_values.fsim_characterizations.fsim_1.metrics[0]" in paths


def test_uninitialized_is_not_active_and_typed_snapshot_resolves_immutably(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "configs")
    empty = store.create_draft(store.bootstrap_configuration(_legacy()), actor_id="project.manager", name="Empty")
    store.validate_draft(empty["draft_id"], actor_id="project.manager")
    empty_snapshot = store.publish_draft(empty["draft_id"], actor_id="project.manager", expected_content_sha256=empty["content_sha256"], name="Empty", reason="legacy migration")
    with pytest.raises(ConfigurationManagementError, match="uninitialized"):
        store.set_active(empty_snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {empty_snapshot['snapshot_id']}")
    store, snapshot = _published_store(platform_root / "typed")
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    context = store.resolve_active_context()
    model_configuration = context.calibration_model_configuration
    assert model_configuration["retained_energy_levels"] == {"q1": 5, "c": 3, "q2": 5}
    assert context.platform_snapshot_id == snapshot["snapshot_id"]
    with pytest.raises(TypeError):
        context.authorities["clock"]["dt_ns"] = 1.0


def test_resolver_detects_record_and_pointer_tampering(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    path = _committed_projection(store) / "snapshots" / snapshot["snapshot_id"] / "snapshot.json"
    raw = __import__("json").loads(path.read_text("utf-8"))
    raw["editable"]["calibration_values"]["waveform_registry"]["settings"]["q1_xy"]["setting_hash"] = "0" * 64
    path.write_text(__import__("json").dumps(raw), "utf-8")
    with pytest.raises(PlatformAuthorityResolutionError):
        store.resolve_active_context()


def test_resolver_detects_active_pointer_tampering(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    pointer = _committed_projection(store) / "active" / "demo_2q1c2r.json"
    raw = __import__("json").loads(pointer.read_text("utf-8"))
    raw["snapshot_content_sha256"] = "0" * 64
    pointer.write_text(__import__("json").dumps(raw), "utf-8")
    with pytest.raises(PlatformAuthorityResolutionError):
        store.resolve_active_context()


def test_draft_update_rejects_stale_content_hash(platform_root: Path):
    store = PlatformConfigurationStore(ROOT, platform_root / "configs")
    draft = store.create_draft(store.bootstrap_configuration(_legacy()), actor_id="project.manager", name="Concurrency")
    with pytest.raises(ConfigurationManagementError) as captured:
        store.update_draft(
            draft["draft_id"],
            actor_id="project.manager",
            expected_content_sha256="0" * 64,
            name=draft["name"],
            note="stale write",
            editable=draft["editable"],
        )
    assert captured.value.status == 409


def test_current_configuration_saves_snapshots_and_restores_versions(platform_root: Path):
    store, original_snapshot = _published_store(platform_root)
    current = store.current_configuration("demo_2q1c2r")
    assert current["artifact_type"] == "platform_configuration_current"
    assert current["source_snapshot_id"] == original_snapshot["snapshot_id"]
    levels = current["editable"]["control_values"]["simulation"]["calibration_model"][
        "retained_energy_levels"
    ]
    assert np.prod(list(levels.values())) == 75

    editable = copy.deepcopy(current["editable"])
    editable["calibration_values"]["qagents"]["Q1"][
        "reference_frequency_authority"
    ]["reference_frequency_GHz"] = 5.03125
    editable["control_values"]["idle_flux_phi0"]["q1"] = 0.2
    editable["control_values"]["simulation"]["calibration_model"][
        "retained_energy_levels"
    ]["c"] = 4
    current = store.update_current_configuration(
        "demo_2q1c2r",
        actor_id="project.manager",
        expected_content_sha256=current["content_sha256"],
        name="Current working configuration",
        note="direct save",
        editable=editable,
    )
    assert current["validation"]["status"] == "valid"
    assert current["revision"] == 2
    levels = current["editable"]["control_values"]["simulation"]["calibration_model"][
        "retained_energy_levels"
    ]
    assert np.prod(list(levels.values())) == 100
    active = store.active_configurations()
    assert len(active) == 1
    assert active[0]["snapshot_id"] == current["source_snapshot_id"]
    assert store.snapshot(active[0]["snapshot_id"])["editable"]["control_values"][
        "idle_flux_phi0"
    ]["q1"] == pytest.approx(0.2)
    assert store.resolve_active_context().idle_flux_phi0["q1"] == pytest.approx(0.2)

    saved = store.snapshot_current_configuration(
        "demo_2q1c2r",
        actor_id="project.manager",
        expected_content_sha256=current["content_sha256"],
        name="Frequency checkpoint",
        reason="retain calibrated frequency",
    )
    assert saved["editable"]["calibration_values"]["qagents"]["Q1"][
        "reference_frequency_authority"
    ]["reference_frequency_GHz"] == pytest.approx(5.03125)
    assert saved["editable"]["control_values"]["simulation"]["calibration_model"][
        "retained_energy_levels"
    ]["c"] == 4
    current = store.current_configuration("demo_2q1c2r")
    assert current["source_snapshot_id"] == saved["snapshot_id"]

    restored = store.apply_snapshot_to_current(
        original_snapshot["snapshot_id"],
        actor_id="project.manager",
        expected_current_content_sha256=current["content_sha256"],
    )
    assert restored["source_snapshot_id"] == original_snapshot["snapshot_id"]
    assert restored["editable"]["calibration_values"]["qagents"]["Q1"][
        "reference_frequency_authority"
    ]["reference_frequency_GHz"] == pytest.approx(5.0)
    assert store.active_configurations()[0]["snapshot_id"] == original_snapshot["snapshot_id"]
    levels = restored["editable"]["control_values"]["simulation"][
        "calibration_model"
    ]["retained_energy_levels"]
    assert np.prod(list(levels.values())) == 75
    assert store.snapshot(saved["snapshot_id"])["content_sha256"] == saved[
        "content_sha256"
    ]


def test_two_processes_with_same_expected_hash_commit_exactly_once(
    platform_root: Path,
):
    store, _snapshot = _published_store(platform_root)
    before = store.current_configuration("demo_2q1c2r")
    coordination = platform_root / "concurrency"
    coordination.mkdir()
    go = coordination / "go"
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    processes = []
    result_paths = []
    for index in range(2):
        ready = coordination / f"ready-{index}"
        result_path = coordination / f"result-{index}.json"
        result_paths.append(result_path)
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    str(
                        ROOT
                        / "tests"
                        / "support"
                        / "configuration_store_concurrent_worker.py"
                    ),
                    str(ROOT),
                    str(store.root),
                    str(ready),
                    str(go),
                    str(result_path),
                    str(uuid.uuid4()),
                    f"worker-{index}",
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )
    deadline = time.monotonic() + 15
    while not all((coordination / f"ready-{index}").is_file() for index in range(2)):
        if time.monotonic() >= deadline:
            for process in processes:
                process.kill()
            raise AssertionError("concurrent workers did not become ready")
        time.sleep(0.02)
    go.write_text("go", encoding="ascii")
    diagnostics = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], diagnostics
    results = [json.loads(path.read_text("utf-8")) for path in result_paths]
    assert sorted(row["status"] for row in results) == [200, 409]
    failure = next(row for row in results if row["status"] == 409)
    assert "changed since it was loaded" in failure["message"]
    after = PlatformConfigurationStore(ROOT, store.root).current_configuration(
        "demo_2q1c2r"
    )
    assert after["revision"] == before["revision"] + 1


def test_draft_diff_uses_initial_checkpoint_and_groups_editable_changes(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    draft = store.create_draft(snapshot, actor_id="project.manager", name="Workbench diff")
    baseline_hash = draft["content_sha256"]
    editable = copy.deepcopy(draft["editable"])
    editable["control_values"]["lanes"]["q1_xy_i"]["latency_samples"] += 1
    editable["calibration_values"]["waveform_registry"]["settings"]["q1_xy"]["amplitude_GHz"] = 0.12
    editable["calibration_values"]["waveform_registry"]["settings"]["c_cz"]["q0_calibrated_dynamic_phase_rad"] = 0.1
    updated = store.update_draft(
        draft["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=baseline_hash,
        name=draft["name"],
        note="changed by workbench",
        editable=editable,
    )

    diff = store.draft_diff(draft["draft_id"])

    assert diff["against"] == "parent"
    assert diff["baseline_checkpoint"] == 0
    assert diff["baseline_content_sha256"] == baseline_hash
    assert diff["current_content_sha256"] == updated["content_sha256"]
    assert diff["changed_count"] == 3
    assert diff["control_changed"] is True
    assert diff["requires_requalification"] is True
    changes = {row["path"]: row for row in diff["changes"]}
    assert changes["$.control_values.lanes.q1_xy_i.latency_samples"]["group"] == "control"
    assert changes["$.calibration_values.waveform_registry.settings.q1_xy.amplitude_GHz"]["group"] == "Q1"
    assert changes["$.calibration_values.waveform_registry.settings.c_cz.q0_calibrated_dynamic_phase_rad"]["group"] == "C"
    assert all("wave_index" not in row["path"] for row in diff["changes"])
    assert all(row["kind"] == "changed" for row in diff["changes"])

    with pytest.raises(ConfigurationManagementError) as captured:
        store.draft_diff(draft["draft_id"], against="checkpoint")
    assert captured.value.status == 422
    with pytest.raises(ConfigurationManagementError) as captured:
        store.draft_diff(str(uuid.uuid4()))
    assert captured.value.status == 404


@pytest.mark.parametrize("field", ["device_sha256", "compiler_snapshot_sha256"])
def test_resolver_detects_readonly_authority_tampering(platform_root: Path, field: str):
    store, snapshot = _published_store(platform_root)
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    path = _committed_projection(store) / "snapshots" / snapshot["snapshot_id"] / "snapshot.json"
    raw = __import__("json").loads(path.read_text("utf-8"))
    raw["readonly"]["authority_refs"][field] = "0" * 64
    path.write_text(__import__("json").dumps(raw), "utf-8")
    with pytest.raises(PlatformAuthorityResolutionError):
        store.resolve_active_context()


def test_candidate_update_publishes_new_reference_revision_and_hash(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    original = snapshot["editable"]["calibration_values"]["qagents"]["Q1"]["reference_frequency_authority"]
    draft = store.create_draft(snapshot, actor_id="project.manager", name="Candidate frequencies")
    updated = store.apply_candidates_to_draft(
        draft["draft_id"], actor_id="project.manager", experiment_run_id="spectroscopy_0042",
        recommendation_id="recommendation_0042", candidates=[
            {"target": "Q1", "proposed_frequency_GHz": 5.01, "recommendation_eligible": True},
            {"target": "Q2", "proposed_frequency_GHz": 5.19, "recommendation_eligible": True},
        ],
    )
    assert store.validate_draft(updated["draft_id"], actor_id="project.manager")["status"] == "valid"
    published = store.publish_draft(updated["draft_id"], actor_id="project.manager", expected_content_sha256=updated["content_sha256"], name="Candidate frequencies", reason="accept spectroscopy")
    current = published["editable"]["calibration_values"]["qagents"]["Q1"]["reference_frequency_authority"]
    assert current["revision"] == original["revision"] + 1
    assert current["setting_hash"] != original["setting_hash"]
    assert current["calibration_run_id"] == "spectroscopy_0042"


def test_generic_candidate_group_applies_multiple_parameters_atomically(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    draft = store.create_draft(snapshot, actor_id="project.manager", name="XY candidate")
    candidate = calibration_candidate(
        "Q1_Q2.xy_amplitude_and_drag",
        ["Q1", "Q2"],
        [
            parameter_change(
                "calibration_values.waveform_registry.settings.q1_xy.amplitude_GHz",
                0.1,
                0.11,
                unit="GHz",
            ),
            parameter_change(
                "calibration_values.waveform_registry.settings.q1_xy.dragAlpha_samples",
                0.0,
                0.02,
                unit="samples",
            ),
            parameter_change(
                "calibration_values.waveform_registry.settings.q2_xy.amplitude_GHz",
                0.1,
                0.12,
                unit="GHz",
                configuration_resource={
                    "owner": "Q2",
                    "resource_type": "waveform_setting",
                    "resource_id": "q2_xy",
                },
            ),
        ],
        recommendation_eligible=True,
        candidate_type="xy_pulse_shape",
    )

    updated = store.apply_candidates_to_draft(
        draft["draft_id"],
        actor_id="project.manager",
        experiment_run_id="rabi_0042",
        recommendation_id="recommendation_0042",
        candidates=[candidate],
    )

    setting = updated["editable"]["calibration_values"]["waveform_registry"]["settings"]["q1_xy"]
    assert setting["amplitude_GHz"] == pytest.approx(0.11)
    assert setting["dragAlpha_samples"] == pytest.approx(0.02)
    q2_setting = updated["editable"]["calibration_values"]["waveform_registry"]["settings"]["q2_xy"]
    assert q2_setting["amplitude_GHz"] == pytest.approx(0.12)
    assert updated["source_candidate"]["candidate_ids"] == ["Q1_Q2.xy_amplitude_and_drag"]
    assert updated["source_candidate"]["calibration_subjects"] == ["Q1", "Q2"]
    assert updated["source_candidate"]["configuration_targets"] == ["Q1", "Q2"]
    assert len(updated["source_candidate"]["candidates"][0]["changes"]) == 3
    with pytest.raises(ConfigurationManagementError) as captured:
        store.apply_candidates_to_draft(
            draft["draft_id"],
            actor_id="project.manager",
            experiment_run_id="rabi_0042",
            recommendation_id="recommendation_0042",
            candidates=[candidate],
        )
    assert captured.value.status == 409


def test_cz_q1_phase_candidate_separates_subject_from_configuration_owner(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    draft = store.create_draft(snapshot, actor_id="project.manager", name="CZ Q1 phase")
    path = (
        "calibration_values.waveform_registry.settings."
        "c_cz.q0_calibrated_dynamic_phase_rad"
    )
    candidate = calibration_candidate(
        "CZ.c_cz.Q1.dynamic_phase",
        "Q1",
        [
            parameter_change(
                path,
                0.0,
                0.13,
                unit="rad",
                configuration_resource={
                    "owner": "C",
                    "resource_type": "waveform_setting",
                    "resource_id": "c_cz",
                },
            )
        ],
        recommendation_eligible=True,
        candidate_type="cz_dynamic_phase",
    )

    updated = store.apply_candidates_to_draft(
        draft["draft_id"],
        actor_id="project.manager",
        experiment_run_id="cz_phase_0042",
        recommendation_id="recommendation_0042",
        candidates=[candidate],
    )

    assert updated["source_candidate"]["calibration_subjects"] == ["Q1"]
    assert updated["source_candidate"]["configuration_targets"] == ["C"]
    assert updated["source_candidate"]["targets"] == ["C"]
    setting = updated["editable"]["calibration_values"]["waveform_registry"]["settings"]["c_cz"]
    assert setting["q0_calibrated_dynamic_phase_rad"] == pytest.approx(0.13)
    assert store.validate_draft(updated["draft_id"], actor_id="project.manager")["status"] == "valid"
    published = store.publish_draft(
        updated["draft_id"],
        actor_id="project.manager",
        expected_content_sha256=updated["content_sha256"],
        name="CZ Q1 phase",
        reason="accept Q1 dynamic phase from CZ experiment",
    )
    published_setting = published["editable"]["calibration_values"]["waveform_registry"]["settings"]["c_cz"]
    assert published_setting["calibration_run_id"] == "cz_phase_0042"

    missing_owner = calibration_candidate(
        "CZ.c_cz.Q1.invalid_owner",
        "Q1",
        [parameter_change(path, 0.0, 0.13, unit="rad")],
        recommendation_eligible=True,
        candidate_type="cz_dynamic_phase",
    )
    second = store.create_draft(snapshot, actor_id="project.manager", name="Invalid owner")
    with pytest.raises(ConfigurationManagementError, match="does not own"):
        store.apply_candidates_to_draft(
            second["draft_id"],
            actor_id="project.manager",
            experiment_run_id="cz_phase_invalid",
            recommendation_id="recommendation_invalid",
            candidates=[missing_owner],
        )


def test_active_context_binds_circuit_execution_evidence(platform_root: Path, monkeypatch):
    store, snapshot = _published_store(platform_root)
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    context = store.resolve_active_context()
    handles = {}
    def fake_run(_compilation, point_id, output_root, _root, *, timeout_s):
        root = Path(output_root) / point_id; root.mkdir(parents=True)
        manifest, receipt = canonical_json_bytes({"m": point_id}), canonical_json_bytes({"r": point_id})
        (root / "manifest.json").write_bytes(manifest); (root / "receipt.json").write_bytes(receipt)
        handle = SimpleNamespace(artifact_root=root, manifest_sha256=hashlib.sha256(manifest).hexdigest().upper(), receipt_sha256=hashlib.sha256(receipt).hexdigest().upper(), qualification_scope="bounded_smoke_only")
        handles[point_id] = handle
        return handle
    arrays = {"population_000": np.array([0.9]), "population_100": np.array([0.03]), "population_001": np.array([0.03]), "population_101": np.array([0.02]), "leakage": np.array([0.02]), "norm_error": np.array([0.0])}
    monkeypatch.setattr(circuits_module, "run_bounded_model_point", fake_run)
    monkeypatch.setattr(circuits_module, "_load_verified_final_observables", lambda _path: (arrays, {"arrays": {}, "evolution_manifest_sha256": "A" * 64, "evolution_receipt_sha256": "B" * 64, "array_inventory_sha256": "C" * 64}))
    result = run_circuits((QCISCircuit("active_case", "X2P Q1\n"),), context, platform_root / "runs", ROOT)[0]
    evidence = __import__("json").loads((result.evidence_root / "evidence.json").read_text("utf-8"))
    assert evidence["platform_configuration"] == {
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_content_sha256": snapshot["content_sha256"],
        "authority_context_sha256": context.authority_context_sha256,
        "calibration_model_configuration_sha256": calibration_model_configuration_sha256(
            context.calibration_model_configuration
        ),
    }
