"""Capacitance matrix construction for the 2q1c subsystem."""

from __future__ import annotations

from dataclasses import dataclass

from sqvm.device.spec import CapacitorSpec, DeviceSpec


DEFAULT_2Q1C_NODE_ORDER = ("q1_p", "q1_m", "c", "q2_p", "q2_m")


@dataclass(frozen=True, slots=True)
class CapacitanceMatrix:
    nodes: tuple[str, ...]
    matrix_fF: tuple[tuple[float, ...], ...]

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.nodes), len(self.nodes))


def build_capacitance_matrix(device: DeviceSpec) -> CapacitanceMatrix:
    """Build the default 2q1c node capacitance matrix."""

    nodes = DEFAULT_2Q1C_NODE_ORDER
    index = {name: position for position, name in enumerate(nodes)}
    matrix = [[0.0 for _ in nodes] for _ in nodes]

    for capacitor in _intrinsic_capacitors(device):
        _add_capacitor(matrix, index, capacitor)
    for capacitor in device.capacitors:
        _add_capacitor(matrix, index, capacitor)

    return CapacitanceMatrix(nodes=nodes, matrix_fF=tuple(tuple(row) for row in matrix))


def _intrinsic_capacitors(device: DeviceSpec) -> tuple[CapacitorSpec, ...]:
    capacitors: list[CapacitorSpec] = []
    q1 = device.components.get("q1")
    q2 = device.components.get("q2")
    c = device.components.get("c")
    if q1 and q1.capacitance_fF is not None:
        capacitors.append(CapacitorSpec("C_q1_intrinsic", "q1_p", "q1_m", q1.capacitance_fF))
    if q2 and q2.capacitance_fF is not None:
        capacitors.append(CapacitorSpec("C_q2_intrinsic", "q2_p", "q2_m", q2.capacitance_fF))
    if c and c.capacitance_fF is not None:
        capacitors.append(CapacitorSpec("C_c_ground", "c", "ground", c.capacitance_fF))
    return tuple(capacitors)


def _add_capacitor(matrix: list[list[float]], index: dict[str, int], capacitor: CapacitorSpec) -> None:
    node_a = capacitor.node_a
    node_b = capacitor.node_b
    has_a = node_a in index
    has_b = node_b in index

    if not has_a and not has_b:
        return
    if has_a:
        i = index[node_a]
        matrix[i][i] += capacitor.capacitance_fF
    if has_b:
        j = index[node_b]
        matrix[j][j] += capacitor.capacitance_fF
    if has_a and has_b:
        i = index[node_a]
        j = index[node_b]
        matrix[i][j] -= capacitor.capacitance_fF
        matrix[j][i] -= capacitor.capacitance_fF
