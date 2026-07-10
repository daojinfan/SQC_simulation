"""Mode capacitance and charging-energy matrices."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sqvm.device.junction import E_CHARGE_C, PLANCK_J_S
from sqvm.hamiltonian.artifacts import DeviceArtifacts


EXPECTED_NODE_ORDER = ("q1_p", "q1_m", "c", "q2_p", "q2_m")
MODE_ORDER = ("q1", "c", "q2")


@dataclass(frozen=True, slots=True)
class ModeTransform:
    node_order: tuple[str, ...]
    mode_order: tuple[str, ...]
    matrix: tuple[tuple[float, ...], ...]
    convention: str = "theta_node = A * theta_mode"


@dataclass(frozen=True, slots=True)
class ModeCapacitanceMatrix:
    modes: tuple[str, ...]
    matrix_fF: tuple[tuple[float, ...], ...]
    eigenvalues_fF: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ECMatrix:
    modes: tuple[str, ...]
    matrix_GHz: tuple[tuple[float, ...], ...]


def build_mode_transform(device_artifacts: DeviceArtifacts) -> ModeTransform:
    node_order = device_artifacts.node_order
    if node_order != EXPECTED_NODE_ORDER:
        raise ValueError(f"node_order must be {list(EXPECTED_NODE_ORDER)}")
    matrix = (
        (0.5, 0.0, 0.0),
        (-0.5, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 0.5),
        (0.0, 0.0, -0.5),
    )
    return ModeTransform(node_order=node_order, mode_order=MODE_ORDER, matrix=matrix)


def build_mode_capacitance_matrix(device_artifacts: DeviceArtifacts, transform: ModeTransform) -> ModeCapacitanceMatrix:
    c_node = np.array(device_artifacts.node_capacitance_matrix_fF, dtype=float)
    a_matrix = np.array(transform.matrix, dtype=float)
    c_mode = a_matrix.T @ c_node @ a_matrix
    eigenvalues = np.linalg.eigvalsh(c_mode)
    return ModeCapacitanceMatrix(
        modes=transform.mode_order,
        matrix_fF=tuple(tuple(float(value) for value in row) for row in c_mode),
        eigenvalues_fF=tuple(float(value) for value in eigenvalues),
    )


def build_ec_matrix(mode_capacitance_matrix: ModeCapacitanceMatrix) -> ECMatrix:
    c_f = np.array(mode_capacitance_matrix.matrix_fF, dtype=float) * 1e-15
    c_inv = np.linalg.inv(c_f)
    ec_hz = (E_CHARGE_C**2) / (2.0 * PLANCK_J_S) * c_inv
    ec_ghz = ec_hz / 1e9
    return ECMatrix(
        modes=mode_capacitance_matrix.modes,
        matrix_GHz=tuple(tuple(float(value) for value in row) for row in ec_ghz),
    )


def is_positive_definite(matrix: tuple[tuple[float, ...], ...], *, tolerance: float = 1e-12) -> bool:
    eigenvalues = np.linalg.eigvalsh(np.array(matrix, dtype=float))
    return bool(np.all(eigenvalues > tolerance))
