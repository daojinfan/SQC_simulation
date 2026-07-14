from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

import sqvm.evolution.artifacts as evolution_artifacts
from sqvm.evolution import FormalScaleQualificationRequired, admit_stage5_config_paths, angular_rad_per_ns, evolve_stage5_scenario, load_stage5_input, run_stage5_evolution, zoh_edges
from sqvm.evolution.input import Q2_RESONANCE_INDEX_66_TRIPLE, _validate_q2_resonance_flux_probe
from sqvm.evolution.physics import _flux_for_hamiltonian, _identity_energy_gauge, _static_hamiltonian


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "configs/evolution/2q1c_qutip_smoke.yaml"
FORMAL = ROOT / "configs/evolution/2q1c_qutip.yaml"


def _config(tmp_path: Path) -> tuple[Path, dict]:
    raw = yaml.safe_load(SMOKE.read_text(encoding="utf-8"))
    path = tmp_path / "stage5.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path, raw


def test_admission_rejects_unknown_root_key(tmp_path):
    path, raw = _config(tmp_path)
    raw["unknown"] = True
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="root keys"):
        admit_stage5_config_paths(path, ROOT)


def test_admission_rejects_path_traversal(tmp_path):
    path, raw = _config(tmp_path)
    raw["inputs"]["device_config"] = "../outside.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="stay inside"):
        admit_stage5_config_paths(path, ROOT)


def test_admission_rejects_symlink_escape(tmp_path):
    path, raw = _config(tmp_path)
    escaped = tmp_path / "escaped"
    escaped.symlink_to(tmp_path.parent, target_is_directory=True)
    raw["inputs"]["device_config"] = "escaped/device.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="stay inside"):
        admit_stage5_config_paths(path, tmp_path)


def test_invalid_admission_creates_no_output_directory(tmp_path):
    path, raw = _config(tmp_path)
    raw["unknown"] = True
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    output = ROOT / "stage5-admission-must-not-publish"
    assert not output.exists()
    with pytest.raises(ValueError, match="root keys"):
        run_stage5_evolution(path, output, ROOT)
    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.staging.*"))


def test_admission_rejects_duplicate_yaml_keys(tmp_path):
    path, _ = _config(tmp_path)
    path.write_text(SMOKE.read_text(encoding="utf-8") + "\nprofile: smoke\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        admit_stage5_config_paths(path, ROOT)


def test_snapshot_is_deterministic_and_readout_is_not_exposed():
    first = load_stage5_input(SMOKE, ROOT)
    second = load_stage5_input(SMOKE, ROOT)
    assert first.snapshot_sha256 == second.snapshot_sha256
    assert first.snapshot["snapshot_sha256"] == first.snapshot_sha256
    assert first.snapshot["bound_paths"]["stage5_config"] == "configs/evolution/2q1c_qutip_smoke.yaml"
    assert tuple(first.scenarios) == ("xy_drag",)
    scenario = first.scenarios["xy_drag"]
    assert not hasattr(scenario, "readout")
    assert "readout" not in str(first.snapshot["controls"])
    assert first.snapshot["acceptance_basis"] == "user_accepted_rebuild_snapshot_v1"


def test_snapshot_binds_stage5_execution_sources():
    stage5_input = load_stage5_input(SMOKE, ROOT)
    assert "src/sqvm/evolution/input.py" in stage5_input.snapshot["source_sha256"]
    assert "src/sqvm/evolution/physics.py" in stage5_input.snapshot["source_sha256"]
    assert "src/sqvm/evolution/artifacts.py" in stage5_input.snapshot["source_sha256"]


def test_flux_mapping_is_by_name_and_static_hamiltonian_is_hermitian():
    stage5_input = load_stage5_input(SMOKE, ROOT)
    scenario = stage5_input.scenarios["xy_drag"]
    mapped = _flux_for_hamiltonian(scenario, 0)
    assert tuple(mapped) == ("q1", "c", "q2")
    assert mapped["c"] == scenario.absolute_flux_phi0["c"][0]
    assert mapped["q2"] == scenario.absolute_flux_phi0["q2"][0]
    import qutip

    hamiltonian = _static_hamiltonian(stage5_input, scenario, 0, qutip)
    assert hamiltonian.isherm
    assert (hamiltonian - hamiltonian.dag()).norm() <= 1e-12


def test_formal_rebuild_preserves_q2_resonance_full_flux_probe():
    stage5_input = load_stage5_input(FORMAL, ROOT)
    scenario = stage5_input.scenarios["q2_resonance_flux"]
    assert tuple(scenario.absolute_flux_phi0) == ("q1", "q2", "c")
    assert tuple(scenario.absolute_flux_phi0[mode][0] for mode in ("q1", "q2", "c")) == (0.1, 0.0, 0.27)
    assert tuple(scenario.absolute_flux_phi0[mode][66] for mode in ("q1", "q2", "c")) == (
        0.10000162139892578,
        0.00016910485839843748,
        0.26999850549316406,
    )
    assert stage5_input.snapshot["q2_resonance_flux_probe"]["absolute_flux_phi0"]["index_66"] == Q2_RESONANCE_INDEX_66_TRIPLE


def test_formal_evolution_apis_fail_before_output_or_numerical_work(tmp_path):
    stage5_input = load_stage5_input(FORMAL, ROOT)
    with pytest.raises(FormalScaleQualificationRequired, match="requires separate qualification"):
        evolve_stage5_scenario(stage5_input, "xy_drag")
    target = tmp_path / "formal-stage5"
    with pytest.raises(FormalScaleQualificationRequired, match="requires separate qualification"):
        run_stage5_evolution(FORMAL, target, ROOT)
    assert not target.exists()
    assert not list(target.parent.glob(f".{target.name}.staging.*"))


def _fake_smoke_input(repository_root: Path):
    config = SimpleNamespace(profile="smoke", scenario_ids=("xy_drag",))
    admission = SimpleNamespace(repository_root=repository_root, config=config)
    return SimpleNamespace(admission=admission)


def test_runner_rejects_existing_target_before_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution_artifacts, "load_stage5_input", lambda *_args, **_kwargs: _fake_smoke_input(tmp_path))
    target = tmp_path / "existing-stage5"
    target.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        evolution_artifacts.run_stage5_evolution("unused.yaml", target, tmp_path)
    assert not list(tmp_path.glob(f".{target.name}.staging.*"))


def test_runner_cleans_staging_when_solver_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(evolution_artifacts, "load_stage5_input", lambda *_args, **_kwargs: _fake_smoke_input(tmp_path))
    monkeypatch.setattr(evolution_artifacts, "evolve_stage5_scenario", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("solver failed")))
    target = tmp_path / "failed-stage5"
    with pytest.raises(RuntimeError, match="solver failed"):
        evolution_artifacts.run_stage5_evolution("unused.yaml", target, tmp_path)
    assert not target.exists()
    assert not list(tmp_path.glob(f".{target.name}.staging.*"))


@pytest.mark.parametrize("mode", ("q1", "c"))
def test_q2_resonance_probe_rejects_tampered_frozen_q1_or_c_component(mode):
    arrays = {name: np.full(67, value, dtype=float) for name, value in {"q1": 0.1, "q2": 0.0, "c": 0.27}.items()}
    for name, value in Q2_RESONANCE_INDEX_66_TRIPLE.items():
        arrays[name][66] = value
    arrays[mode][66] = np.nextafter(arrays[mode][66], np.inf)
    rows = [{"scenario_id": "q2_resonance_flux", "effective": {"absolute_flux_phi0": {name: values.tolist() for name, values in arrays.items()}}}]
    control = SimpleNamespace(idle_flux_phi0={"q1": 0.1, "q2": 0.0, "c": 0.27})
    with pytest.raises(ValueError, match=rf"index 66 {mode}"):
        _validate_q2_resonance_flux_probe(rows, control)


def test_zoh_edges_and_only_conversion_helper():
    centers = np.array([10.25, 10.75, 11.25])
    assert zoh_edges(centers).tolist() == [10.0, 10.5, 11.0, 11.5]
    assert angular_rad_per_ns(0.125) == pytest.approx(0.25 * np.pi)


def test_smoke_window_is_active_and_snapshot_retains_full_control():
    stage5_input = load_stage5_input(SMOKE, ROOT)
    scenario = stage5_input.scenarios["xy_drag"]
    config = stage5_input.admission.config
    start = config.smoke_window_start_index
    count = config.smoke_sample_count
    assert start is not None and count is not None
    active = sum(np.max(np.abs(pair[0][start:start + count]) + np.abs(pair[1][start:start + count])) for pair in scenario.xy_iq_GHz.values())
    assert active > 0.0
    assert stage5_input.snapshot["controls"]["xy_drag"]["time_center_ns"].__len__() == scenario.time_center_ns.size


def test_active_window_and_solver_contract_are_fixed():
    stage5_input = load_stage5_input(SMOKE, ROOT)
    config = stage5_input.admission.config
    assert config.smoke_window_start_index == 97
    assert config.smoke_sample_count == 4
    assert config.solver == {"qutip_version_spec": ">=5.1,<5.4", "method": "vern9", "rtol": 1e-13, "atol": 1e-15, "nsteps": 100000, "max_step_ns": 0.0025, "store_states": True, "store_final_state": True, "normalize_output": False, "progress_bar": None}


def test_identity_energy_gauge_is_real_trace_per_dimension():
    import qutip

    hamiltonian = qutip.Qobj(np.diag([2.0, 4.0]))
    assert _identity_energy_gauge(hamiltonian) == 3.0
