from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import shutil
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

import sqvm.circuits as circuits_module
from sqvm.circuits import QCISCircuit, run_circuits
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.web import (
    ConfigurationManagementError,
    PlatformAuthorityResolutionError,
    PlatformConfigurationStore,
)
from sqvm.web.configuration_schema import validate_editable
from sqvm.web.configuration_schema import project_wave_indices


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def platform_root():
    root = ROOT / "output" / f".platform-v02.{uuid.uuid4().hex}"
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
    assert context.platform_snapshot_id == snapshot["snapshot_id"]
    with pytest.raises(TypeError):
        context.authorities["clock"]["dt_ns"] = 1.0


def test_resolver_detects_record_and_pointer_tampering(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    path = store.snapshots_root / snapshot["snapshot_id"] / "snapshot.json"
    raw = __import__("json").loads(path.read_text("utf-8"))
    raw["editable"]["calibration_values"]["waveform_registry"]["settings"]["q1_xy"]["setting_hash"] = "0" * 64
    path.write_text(__import__("json").dumps(raw), "utf-8")
    with pytest.raises(PlatformAuthorityResolutionError):
        store.resolve_active_context()


def test_resolver_detects_active_pointer_tampering(platform_root: Path):
    store, snapshot = _published_store(platform_root)
    store.set_active(snapshot["snapshot_id"], actor_id="project.manager", confirmation_phrase=f"SET ACTIVE {snapshot['snapshot_id']}")
    pointer = store.active_root / "demo_2q1c2r.json"
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

    editable = copy.deepcopy(current["editable"])
    editable["calibration_values"]["qagents"]["Q1"][
        "reference_frequency_authority"
    ]["reference_frequency_GHz"] = 5.03125
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
    assert store.snapshot(saved["snapshot_id"])["content_sha256"] == saved[
        "content_sha256"
    ]


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
    path = store.snapshots_root / snapshot["snapshot_id"] / "snapshot.json"
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
    assert evidence["platform_configuration"] == {"snapshot_id": snapshot["snapshot_id"], "snapshot_content_sha256": snapshot["content_sha256"], "authority_context_sha256": context.authority_context_sha256}
