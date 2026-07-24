import pytest as _pytest

pytestmark = _pytest.mark.contract

from pathlib import Path

import numpy as np
import pytest

from sqvm.hamiltonian import (
    EXPECTED_NODE_ORDER,
    build_ec_matrix,
    build_mode_capacitance_matrix,
    build_mode_transform,
    load_device_artifacts,
)
from sqvm.hamiltonian.artifacts import DeviceArtifacts
from tests.support.fixture_loader import fixture_path


ARTIFACTS = fixture_path("device_model_v1") / "output/stage_01_device_model/device_artifacts.json"


def test_mode_order_and_transform_values():
    device = load_device_artifacts(ARTIFACTS)
    transform = build_mode_transform(device)
    assert transform.node_order == EXPECTED_NODE_ORDER
    assert transform.mode_order == ("q1", "c", "q2")
    assert transform.matrix[0] == (0.5, 0.0, 0.0)


def test_reject_unsupported_node_order():
    device = load_device_artifacts(ARTIFACTS)
    payload = dict(device.payload)
    payload["capacitance_matrix"] = dict(payload["capacitance_matrix"])
    payload["capacitance_matrix"]["nodes"] = ["q1_m", "q1_p", "c", "q2_p", "q2_m"]
    with pytest.raises(ValueError, match="node_order"):
        build_mode_transform(DeviceArtifacts(path=device.path, payload=payload))


def test_mode_capacitance_matches_a_t_c_a():
    device = load_device_artifacts(ARTIFACTS)
    transform = build_mode_transform(device)
    mode_cap = build_mode_capacitance_matrix(device, transform)
    c_node = np.array(device.node_capacitance_matrix_fF)
    a_matrix = np.array(transform.matrix)
    expected = a_matrix.T @ c_node @ a_matrix
    assert np.allclose(np.array(mode_cap.matrix_fF), expected)
    assert np.all(np.array(mode_cap.eigenvalues_fF) > 0)


def test_ec_matrix_is_symmetric_and_ghz_scale():
    device = load_device_artifacts(ARTIFACTS)
    mode_cap = build_mode_capacitance_matrix(device, build_mode_transform(device))
    ec = build_ec_matrix(mode_cap)
    matrix = np.array(ec.matrix_GHz)
    assert np.allclose(matrix, matrix.T)
    assert np.all(np.diag(matrix) > 0.05)
    assert np.all(np.diag(matrix) < 2.0)
