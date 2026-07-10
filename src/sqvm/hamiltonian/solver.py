"""Hamiltonian eigenvalue solvers."""

from __future__ import annotations

import numpy as np
from scipy import linalg

from sqvm.hamiltonian.config import SolverConfig
from sqvm.hamiltonian.builder import HamiltonianModel


def solve_lowest_eigenvalues(model: HamiltonianModel, solver: SolverConfig) -> tuple[float, ...]:
    if solver.method != "eigh":
        raise ValueError("only solver method eigh is implemented")
    dense = model.matrix.toarray()
    count = min(solver.num_eigenvalues, dense.shape[0])
    values = linalg.eigh(dense, subset_by_index=[0, count - 1], eigvals_only=True)
    return tuple(float(value) for value in np.sort(values))


def gaps_from_eigenvalues(eigenvalues: tuple[float, ...]) -> tuple[float, ...]:
    if not eigenvalues:
        return ()
    ground = eigenvalues[0]
    return tuple(float(value - ground) for value in eigenvalues)
