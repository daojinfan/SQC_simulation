"""Hamiltonian verification checks and physical sanity helpers."""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Any

import numpy as np
from scipy import linalg

from sqvm.hamiltonian.basis import build_charge_basis
from sqvm.hamiltonian.builder import build_dense_hamiltonian, build_sparse_hamiltonian, max_abs_sparse
from sqvm.hamiltonian.config import BasisConfig
from sqvm.hamiltonian.junction import EffectiveJunction


@dataclass(frozen=True, slots=True)
class HamiltonianCheck:
    name: str
    passed: bool
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "message": self.message, "severity": self.severity}


def check(name: str, passed: bool, message: str, *, severity: str = "error") -> HamiltonianCheck:
    return HamiltonianCheck(name=name, passed=bool(passed), message=message, severity=severity)


def sparse_dense_equivalence_check(ec_matrix_GHz, effective_junctions) -> HamiltonianCheck:
    basis = build_charge_basis(BasisConfig({"q1": 2, "c": 2, "q2": 2}))
    sparse_matrix = build_sparse_hamiltonian(basis, ec_matrix_GHz, effective_junctions)
    dense_matrix = build_dense_hamiltonian(basis, ec_matrix_GHz, effective_junctions)
    error = float(np.max(np.abs(sparse_matrix.toarray() - dense_matrix)))
    return check("sparse_dense_small_cutoff_equivalent", error < 1e-10, f"max abs error {error:.3g}")


def single_transmon_analytic_limit(ec_matrix_GHz, effective_junctions, priors: dict[str, Any]) -> dict[str, Any]:
    rows = []
    ec = np.array(ec_matrix_GHz, dtype=float)
    for index, mode in enumerate(("q1", "c", "q2")):
        junction = next(row for row in effective_junctions if row.mode == mode)
        ec_diag = float(ec[index, index])
        analytic = sqrt(8.0 * ec_diag * junction.ej_effective_GHz) - ec_diag
        numeric = _single_mode_f01(ec_diag, junction.ej_effective_GHz, cutoff=7)
        prior = _prior_frequency(mode, priors)
        rows.append(
            {
                "mode": mode,
                "ec_GHz": ec_diag,
                "ej_effective_GHz": junction.ej_effective_GHz,
                "numeric_f01_GHz": numeric,
                "analytic_f01_GHz": analytic,
                "numeric_minus_analytic_GHz": numeric - analytic,
                "prior_f01_GHz": prior,
                "numeric_minus_prior_GHz": None if prior is None else numeric - prior,
            }
        )
    return {"rows": rows}


def single_transmon_check(analytic_payload: dict[str, Any], *, tolerance_GHz: float = 0.75) -> HamiltonianCheck:
    max_error = max(abs(row["numeric_minus_analytic_GHz"]) for row in analytic_payload["rows"])
    return check(
        "single_transmon_analytic_limit",
        max_error <= tolerance_GHz,
        f"max |numeric-analytic| {max_error:.3g} GHz",
        severity="warning",
    )


def charge_basis_convergence(ec_matrix_GHz, effective_junctions, cutoffs: dict[str, int]) -> dict[str, Any]:
    ec = np.array(ec_matrix_GHz, dtype=float)
    rows = []
    for index, mode in enumerate(("q1", "c", "q2")):
        junction = next(row for row in effective_junctions if row.mode == mode)
        base_cutoff = cutoffs[mode]
        base_gap = _single_mode_f01(float(ec[index, index]), junction.ej_effective_GHz, cutoff=base_cutoff)
        refined_gap = _single_mode_f01(float(ec[index, index]), junction.ej_effective_GHz, cutoff=base_cutoff + 2)
        rows.append(
            {
                "mode": mode,
                "charge_cutoff": base_cutoff,
                "refined_charge_cutoff": base_cutoff + 2,
                "gap_GHz": base_gap,
                "refined_gap_GHz": refined_gap,
                "drift_GHz": refined_gap - base_gap,
            }
        )
    return {"rows": rows}


def convergence_check(convergence_payload: dict[str, Any], *, tolerance_GHz: float = 0.05) -> HamiltonianCheck:
    max_drift = max(abs(row["drift_GHz"]) for row in convergence_payload["rows"])
    return check(
        "charge_basis_convergence",
        max_drift <= tolerance_GHz,
        f"max N->N+2 gap drift {max_drift:.3g} GHz",
        severity="warning",
    )


def _single_mode_f01(ec_GHz: float, ej_GHz: float, *, cutoff: int) -> float:
    charges = np.arange(-cutoff, cutoff + 1, dtype=float)
    hamiltonian = np.diag(4.0 * ec_GHz * charges**2)
    for i in range(len(charges) - 1):
        hamiltonian[i, i + 1] = -ej_GHz / 2.0
        hamiltonian[i + 1, i] = -ej_GHz / 2.0
    values = linalg.eigh(hamiltonian, subset_by_index=[0, 1], eigvals_only=True)
    return float(values[1] - values[0])


def _prior_frequency(mode: str, priors: dict[str, Any]) -> float | None:
    if mode in ("q1", "q2"):
        value = priors.get(mode, {}).get("estimated_f01_GHz")
    else:
        value = priors.get("c", {}).get("estimated_idle_frequency_GHz")
    return float(value) if isinstance(value, int | float) else None
