import json
from pathlib import Path

import pytest
import yaml

from sqvm.spectrum import (
    load_spectrum_config,
    load_stage2_rebaseline_approval,
    load_stage2_rebaseline_manifest,
    validate_spectrum_provenance,
)


CONFIG = Path("configs/spectra/2q1c_static.yaml")


def _mutated_config(tmp_path, mutate):
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    mutate(payload["spectrum"])
    path = tmp_path / "spectrum.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_load_valid_spectrum_config():
    config = load_spectrum_config(CONFIG)
    assert config.profile == "acceptance"
    assert config.acceptance_eligible
    assert config.eigen.num_states == 48
    assert config.eigen.eigsh.ncv == 97


def test_reject_missing_source_hamiltonian_config(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value.pop("source_hamiltonian_config"))
    with pytest.raises(ValueError, match="source_hamiltonian_config"):
        load_spectrum_config(path)


def test_reject_missing_rebaseline_manifest(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value.update(source_rebaseline_manifest=str(tmp_path / "missing.json")))
    config = load_spectrum_config(path)
    with pytest.raises(ValueError, match="cannot read"):
        load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)


def test_reject_missing_rebaseline_approval(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value.update(source_rebaseline_approval=str(tmp_path / "missing.json")))
    config = load_spectrum_config(path)
    with pytest.raises(ValueError, match="cannot read"):
        load_stage2_rebaseline_approval(config.source_rebaseline_approval)


def test_reject_rejected_rebaseline_approval(tmp_path):
    approval = json.loads(Path("output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json").read_text())
    approval["decision"] = "rejected"
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    path = _mutated_config(tmp_path, lambda value: value.update(source_rebaseline_approval=str(approval_path)))
    config = load_spectrum_config(path)
    manifest = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
    with pytest.raises(ValueError, match="provenance"):
        validate_spectrum_provenance(config, manifest, load_stage2_rebaseline_approval(approval_path))


def test_reject_approval_manifest_hash_mismatch(tmp_path):
    approval = json.loads(Path("output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json").read_text())
    approval["manifest_sha256"] = "0" * 64
    path = tmp_path / "approval.json"
    path.write_text(json.dumps(approval), encoding="utf-8")
    config_path = _mutated_config(tmp_path, lambda value: value.update(source_rebaseline_approval=str(path)))
    config = load_spectrum_config(config_path)
    with pytest.raises(ValueError, match="provenance"):
        validate_spectrum_provenance(
            config,
            load_stage2_rebaseline_manifest(config.source_rebaseline_manifest),
            load_stage2_rebaseline_approval(path),
        )


@pytest.mark.parametrize("field", ["source_hamiltonian_config", "source_hamiltonian_artifacts"])
def test_reject_sha256_mismatch_for_each_bound_input(tmp_path, field):
    replacement = tmp_path / "wrong.json"
    replacement.write_text("{}", encoding="utf-8")
    path = _mutated_config(tmp_path, lambda value: value.update({field: str(replacement)}))
    config = load_spectrum_config(path)
    with pytest.raises(ValueError):
        validate_spectrum_provenance(
            config,
            load_stage2_rebaseline_manifest(config.source_rebaseline_manifest),
            load_stage2_rebaseline_approval(config.source_rebaseline_approval),
        )


def test_reject_invalid_num_states(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value["eigen"].update(num_states=0))
    with pytest.raises(ValueError, match="num_states"):
        load_spectrum_config(path)


def test_reject_invalid_min_overlap(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value["dressed_labeling"].update(min_overlap=1.1))
    with pytest.raises(ValueError, match="min_overlap"):
        load_spectrum_config(path)


def test_reject_invalid_max_total_excitations(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value["dressed_labeling"].update(max_total_excitations=0))
    with pytest.raises(ValueError, match="max_total_excitations"):
        load_spectrum_config(path)


def test_reject_num_states_smaller_than_target_catalog(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value["eigen"].update(num_states=9, **{"eigsh": {**value["eigen"]["eigsh"], "ncv": 25}}))
    with pytest.raises(ValueError, match="target bare catalog"):
        load_spectrum_config(path)


def test_reject_invalid_convergence_tolerance(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value["convergence"].update(frequency_tolerance_MHz=0))
    with pytest.raises(ValueError, match="frequency_tolerance"):
        load_spectrum_config(path)


def test_reject_invalid_flux_scan_range(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value["flux_scan"].update(start_phi0=0.5, stop_phi0=0.4))
    with pytest.raises(ValueError, match="start_phi0"):
        load_spectrum_config(path)


@pytest.mark.parametrize(("field", "value"), [("coarse_points", 8), ("refinement_points", 5)])
def test_reject_even_or_too_small_scan_points(tmp_path, field, value):
    path = _mutated_config(tmp_path, lambda raw: raw["flux_scan"].update({field: value}))
    with pytest.raises(ValueError, match=field):
        load_spectrum_config(path)


def test_reject_stage2_artifact_config_mismatch(tmp_path):
    path = _mutated_config(tmp_path, lambda value: value.update(source_hamiltonian_config=str(tmp_path / "wrong.yaml")))
    config = load_spectrum_config(path)
    with pytest.raises(ValueError, match="does not match"):
        validate_spectrum_provenance(
            config,
            load_stage2_rebaseline_manifest(config.source_rebaseline_manifest),
            load_stage2_rebaseline_approval(config.source_rebaseline_approval),
        )


def test_reject_stage2_artifact_version_before_0_2(monkeypatch, spectrum_session):
    import sqvm.spectrum.provenance as module

    original = module._load_mapping

    def fake(path, description):
        value = original(path, description)
        if description == "Stage 2 artifact":
            value["artifact_version"] = "0.1"
        return value

    monkeypatch.setattr(module, "_load_mapping", fake)
    with pytest.raises(ValueError, match="provenance"):
        validate_spectrum_provenance(
            spectrum_session["config"], spectrum_session["manifest"], spectrum_session["approval"]
        )
