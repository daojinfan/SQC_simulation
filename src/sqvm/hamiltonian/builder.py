"""Charge-basis Hamiltonian construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import sparse

from sqvm.hamiltonian.basis import ChargeBasis, build_charge_basis, cos_phi_operator
from sqvm.hamiltonian.config import HamiltonianConfig
from sqvm.hamiltonian.junction import EffectiveJunction


@dataclass(frozen=True, slots=True)
class HamiltonianModel:
    basis: ChargeBasis
    matrix: sparse.csr_matrix
    ec_matrix_GHz: tuple[tuple[float, ...], ...]
    effective_junctions: tuple[EffectiveJunction, ...]
    summary: dict[str, Any]


def build_hamiltonian(
    config: HamiltonianConfig,
    ec_matrix_GHz: tuple[tuple[float, ...], ...],
    effective_junctions: tuple[EffectiveJunction, ...],
) -> HamiltonianModel:
    basis = build_charge_basis(config.basis)
    matrix = build_sparse_hamiltonian(basis, ec_matrix_GHz, effective_junctions)
    summary = hamiltonian_summary(matrix)
    return HamiltonianModel(
        basis=basis,
        matrix=matrix,
        ec_matrix_GHz=ec_matrix_GHz,
        effective_junctions=effective_junctions,
        summary=summary,
    )


def build_sparse_hamiltonian(
    basis: ChargeBasis,
    ec_matrix_GHz: tuple[tuple[float, ...], ...],
    effective_junctions: tuple[EffectiveJunction, ...],
) -> sparse.csr_matrix:
    diagonal = _charge_diagonal(basis, ec_matrix_GHz)
    hamiltonian = sparse.diags(diagonal, format="csr")
    junctions = {row.mode: row.ej_effective_GHz for row in effective_junctions}
    for mode in basis.mode_order:
        hamiltonian = hamiltonian - junctions[mode] * _embedded_cos_phi(basis, mode, sparse_output=True)
    return hamiltonian.tocsr()


def build_dense_hamiltonian(
    basis: ChargeBasis,
    ec_matrix_GHz: tuple[tuple[float, ...], ...],
    effective_junctions: tuple[EffectiveJunction, ...],
) -> np.ndarray:
    diagonal = _charge_diagonal(basis, ec_matrix_GHz)
    hamiltonian = np.diag(diagonal)
    junctions = {row.mode: row.ej_effective_GHz for row in effective_junctions}
    for mode in basis.mode_order:
        hamiltonian = hamiltonian - junctions[mode] * _embedded_cos_phi(basis, mode, sparse_output=False)
    return hamiltonian


def _charge_diagonal(basis: ChargeBasis, ec_matrix_GHz: tuple[tuple[float, ...], ...]) -> np.ndarray:
    charges = [np.array(basis.charges[mode], dtype=float) for mode in basis.mode_order]
    grids = np.meshgrid(*charges, indexing="ij")
    diagonal = np.zeros_like(grids[0], dtype=float)
    ec = np.array(ec_matrix_GHz, dtype=float)
    for i in range(len(charges)):
        for j in range(len(charges)):
            diagonal += 4.0 * ec[i, j] * grids[i] * grids[j]
    return diagonal.reshape(-1)


def _embedded_cos_phi(basis: ChargeBasis, target_mode: str, *, sparse_output: bool):
    operators = []
    for mode in basis.mode_order:
        size = basis.dimensions[mode]
        if mode == target_mode:
            operators.append(cos_phi_operator(size, sparse_output=sparse_output))
        elif sparse_output:
            operators.append(sparse.identity(size, format="csr"))
        else:
            operators.append(np.identity(size))
    result = operators[0]
    for operator in operators[1:]:
        if sparse_output:
            result = sparse.kron(result, operator, format="csr")
        else:
            result = np.kron(result, operator)
    return result


def hamiltonian_summary(matrix: sparse.csr_matrix) -> dict[str, Any]:
    hermiticity_error = max_abs_sparse(matrix - matrix.getH())
    return {
        "representation": "sparse",
        "shape": list(matrix.shape),
        "nnz": int(matrix.nnz),
        "hermiticity_error": hermiticity_error,
    }


def max_abs_sparse(matrix: sparse.spmatrix) -> float:
    if matrix.nnz == 0:
        return 0.0
    return float(np.max(np.abs(matrix.data)))
