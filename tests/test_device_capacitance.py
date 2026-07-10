from pathlib import Path

from sqvm.device import build_capacitance_matrix, load_device


CONFIG = Path("configs/devices/2q1c2r.yaml")


def test_capacitance_matrix_node_order():
    matrix = build_capacitance_matrix(load_device(CONFIG))
    assert matrix.nodes == ("q1_p", "q1_m", "c", "q2_p", "q2_m")
    assert matrix.shape == (5, 5)


def test_capacitance_matrix_is_symmetric():
    matrix = build_capacitance_matrix(load_device(CONFIG)).matrix_fF
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            assert value == matrix[j][i]


def test_floating_transmon_internal_capacitance():
    matrix = build_capacitance_matrix(load_device(CONFIG))
    i = matrix.nodes.index("q1_p")
    j = matrix.nodes.index("q1_m")
    assert matrix.matrix_fF[i][j] == -75.0
    assert matrix.matrix_fF[j][i] == -75.0


def test_grounded_coupler_capacitance():
    matrix = build_capacitance_matrix(load_device(CONFIG))
    c = matrix.nodes.index("c")
    assert matrix.matrix_fF[c][c] >= 60.0


def test_readout_resonator_metadata_not_in_default_matrix():
    matrix = build_capacitance_matrix(load_device(CONFIG))
    assert "r1" not in matrix.nodes
    assert "r2" not in matrix.nodes
