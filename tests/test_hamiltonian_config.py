from pathlib import Path

import pytest

from sqvm.hamiltonian import load_hamiltonian_config


CONFIG = Path("configs/hamiltonians/2q1c_charge_basis.yaml")


def test_load_valid_hamiltonian_config():
    config = load_hamiltonian_config(CONFIG)
    assert config.name == "demo_2q1c_charge_basis"
    assert config.basis.charge_cutoffs == {"q1": 7, "c": 7, "q2": 7}
    assert config.solver.method == "eigh"


def test_reject_invalid_charge_cutoff(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "schema_version: '0.1'\n"
        "hamiltonian:\n"
        "  name: bad\n"
        "  source_device_artifacts: output/stage_01_device_model/device_artifacts.json\n"
        "  basis:\n"
        "    q1: {charge_cutoff: 0}\n"
        "    c: {charge_cutoff: 5}\n"
        "    q2: {charge_cutoff: 5}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="charge_cutoff"):
        load_hamiltonian_config(path)


def test_reject_invalid_solver(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "schema_version: '0.1'\n"
        "hamiltonian:\n"
        "  name: bad\n"
        "  source_device_artifacts: output/stage_01_device_model/device_artifacts.json\n"
        "  basis:\n"
        "    q1: {charge_cutoff: 5}\n"
        "    c: {charge_cutoff: 5}\n"
        "    q2: {charge_cutoff: 5}\n"
        "  solver:\n"
        "    method: eigsh\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="supports only eigh"):
        load_hamiltonian_config(path)
