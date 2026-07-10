"""Charge basis helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import sparse

from sqvm.hamiltonian.config import BasisConfig, SUPPORTED_MODES


@dataclass(frozen=True, slots=True)
class ChargeBasis:
    mode_order: tuple[str, ...]
    charge_cutoffs: dict[str, int]
    charges: dict[str, tuple[int, ...]]
    dimensions: dict[str, int]
    hilbert_dimension: int
    offset_charge_ng: dict[str, float]


def build_charge_basis(config: BasisConfig) -> ChargeBasis:
    charges: dict[str, tuple[int, ...]] = {}
    dimensions: dict[str, int] = {}
    total = 1
    for mode in SUPPORTED_MODES:
        cutoff = config.charge_cutoffs[mode]
        values = tuple(range(-cutoff, cutoff + 1))
        charges[mode] = values
        dimensions[mode] = len(values)
        total *= len(values)
    return ChargeBasis(
        mode_order=SUPPORTED_MODES,
        charge_cutoffs=dict(config.charge_cutoffs),
        charges=charges,
        dimensions=dimensions,
        hilbert_dimension=total,
        offset_charge_ng={mode: 0.0 for mode in SUPPORTED_MODES},
    )


def number_operator(charges: tuple[int, ...], *, sparse_output: bool = True):
    values = np.array(charges, dtype=float)
    if sparse_output:
        return sparse.diags(values, format="csr")
    return np.diag(values)


def cos_phi_operator(size: int, *, sparse_output: bool = True):
    offdiag = np.full(size - 1, 0.5, dtype=float)
    if sparse_output:
        return sparse.diags([offdiag, offdiag], offsets=[-1, 1], shape=(size, size), format="csr")
    matrix = np.zeros((size, size), dtype=float)
    for i in range(size - 1):
        matrix[i, i + 1] = 0.5
        matrix[i + 1, i] = 0.5
    return matrix
