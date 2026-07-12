from pathlib import Path

import numpy as np

from sqvm.hamiltonian import (
    build_ec_matrix,
    build_hamiltonian,
    build_mode_capacitance_matrix,
    build_mode_transform,
    load_device_artifacts,
    load_hamiltonian_config,
    resolve_effective_junctions,
)
from sqvm.hamiltonian.checks import charge_basis_convergence, single_transmon_analytic_limit


CONFIG = Path("configs/hamiltonians/2q1c_charge_basis.yaml")
ARTIFACTS = Path("output/stage_01_device_model/device_artifacts.json")


def _model():
    config = load_hamiltonian_config(CONFIG)
    device = load_device_artifacts(ARTIFACTS)
    mode_cap = build_mode_capacitance_matrix(device, build_mode_transform(device))
    ec = build_ec_matrix(mode_cap)
    junctions = resolve_effective_junctions(device)
    return config, device, ec, junctions, build_hamiltonian(config, ec.matrix_GHz, junctions)


def test_hamiltonian_shape_and_hermitian():
    _, _, _, _, model = _model()
    assert model.basis.hilbert_dimension == 3375
    assert model.matrix.shape == (3375, 3375)
    assert (model.matrix - model.matrix.getH()).nnz == 0


def test_single_transmon_analytic_limit_payload():
    _, device, ec, junctions, _ = _model()
    payload = single_transmon_analytic_limit(ec.matrix_GHz, junctions, device.payload["priors"])
    assert {row["mode"] for row in payload["rows"]} == {"q1", "c", "q2"}
    assert all(row["numeric_f01_GHz"] > 0 for row in payload["rows"])


def test_charge_basis_convergence_payload():
    config, _, ec, junctions, _ = _model()
    payload = charge_basis_convergence(ec.matrix_GHz, junctions, config.basis.charge_cutoffs)
    assert len(payload["rows"]) == 3
    assert all(np.isfinite(row["drift_GHz"]) for row in payload["rows"])


def test_repeated_flux_override_build_is_identical():
    config = load_hamiltonian_config(CONFIG)
    device = load_device_artifacts(ARTIFACTS)
    mode_cap = build_mode_capacitance_matrix(device, build_mode_transform(device))
    ec = build_ec_matrix(mode_cap)
    overrides = {"c": 0.3125}

    first = build_hamiltonian(config, ec.matrix_GHz, resolve_effective_junctions(device, overrides))
    second = build_hamiltonian(config, ec.matrix_GHz, resolve_effective_junctions(device, overrides))

    assert first.effective_junctions == second.effective_junctions
    assert (first.matrix != second.matrix).nnz == 0
