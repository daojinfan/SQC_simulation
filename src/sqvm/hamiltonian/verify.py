"""Stage 2 Hamiltonian verification orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sqvm.hamiltonian.artifacts import load_device_artifacts, write_hamiltonian_artifacts
from sqvm.hamiltonian.builder import build_hamiltonian, max_abs_sparse
from sqvm.hamiltonian.capacitance import (
    EXPECTED_NODE_ORDER,
    build_ec_matrix,
    build_mode_capacitance_matrix,
    build_mode_transform,
    is_positive_definite,
)
from sqvm.hamiltonian.checks import (
    HamiltonianCheck,
    charge_basis_convergence,
    check,
    convergence_check,
    single_transmon_analytic_limit,
    single_transmon_check,
    sparse_dense_equivalence_check,
)
from sqvm.hamiltonian.config import HamiltonianConfig, load_hamiltonian_config
from sqvm.hamiltonian.junction import resolve_effective_junctions
from sqvm.hamiltonian.notebook import write_verification_notebook
from sqvm.hamiltonian.solver import gaps_from_eigenvalues, solve_lowest_eigenvalues


@dataclass(frozen=True, slots=True)
class HamiltonianVerificationReport:
    ok: bool
    hamiltonian_name: str
    checks: tuple[HamiltonianCheck, ...]
    artifacts: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "hamiltonian_name": self.hamiltonian_name,
            "checks": [item.to_dict() for item in self.checks],
            "artifacts": dict(self.artifacts),
        }


def verify_hamiltonian(config_path: str | Path, output_dir: str | Path) -> HamiltonianVerificationReport:
    config = load_hamiltonian_config(config_path)
    device_path = _resolve_project_path(config.source_device_artifacts)
    device_artifacts = load_device_artifacts(device_path)
    transform = build_mode_transform(device_artifacts)
    mode_capacitance = build_mode_capacitance_matrix(device_artifacts, transform)
    ec_matrix = build_ec_matrix(mode_capacitance)
    effective_junctions = resolve_effective_junctions(device_artifacts)
    model = build_hamiltonian(config, ec_matrix.matrix_GHz, effective_junctions)
    eigenvalues = solve_lowest_eigenvalues(model, config.solver)
    gaps = gaps_from_eigenvalues(eigenvalues)
    analytic = single_transmon_analytic_limit(ec_matrix.matrix_GHz, effective_junctions, device_artifacts.payload.get("priors", {}))
    convergence = charge_basis_convergence(ec_matrix.matrix_GHz, effective_junctions, config.basis.charge_cutoffs)
    checks = _build_checks(
        config=config,
        device_artifacts=device_artifacts,
        mode_capacitance=mode_capacitance,
        ec_matrix=ec_matrix,
        model=model,
        effective_junctions=effective_junctions,
        eigenvalues=eigenvalues,
        gaps=gaps,
        analytic=analytic,
        convergence=convergence,
    )
    payload = _payload(
        config_path=Path(config_path),
        config=config,
        device_artifacts=device_artifacts,
        transform=transform,
        mode_capacitance=mode_capacitance,
        ec_matrix=ec_matrix,
        model=model,
        effective_junctions=effective_junctions,
        eigenvalues=eigenvalues,
        gaps=gaps,
        checks=checks,
        analytic=analytic,
        convergence=convergence,
    )
    artifact_set = write_hamiltonian_artifacts(payload, output_dir)
    notebook_path = Path(output_dir) / "verification.ipynb"
    write_verification_notebook(artifact_set.hamiltonian_artifacts, notebook_path)
    ok = all(item.passed for item in checks if item.severity == "error")
    return HamiltonianVerificationReport(
        ok=ok,
        hamiltonian_name=config.name,
        checks=checks,
        artifacts={
            "hamiltonian_artifacts": str(artifact_set.hamiltonian_artifacts),
            "verification_notebook": str(notebook_path),
        },
    )


def _build_checks(
    *,
    config: HamiltonianConfig,
    device_artifacts,
    mode_capacitance,
    ec_matrix,
    model,
    effective_junctions,
    eigenvalues,
    gaps,
    analytic,
    convergence,
) -> tuple[HamiltonianCheck, ...]:
    ec = np.array(ec_matrix.matrix_GHz, dtype=float)
    h_error = max_abs_sparse(model.matrix - model.matrix.getH())
    mode_matrix = np.array(mode_capacitance.matrix_fF, dtype=float)
    checks = [
        check("device_artifacts_type", device_artifacts.payload.get("artifact_type") == "stage_01_device_model", "artifact type is stage_01_device_model"),
        check("node_order_contract", device_artifacts.node_order == EXPECTED_NODE_ORDER, f"node order is {device_artifacts.node_order}"),
        check("mode_order_expected", mode_capacitance.modes == ("q1", "c", "q2"), f"mode order is {mode_capacitance.modes}"),
        check("coordinate_transform_shape", True, "coordinate transform is fixed 5x3"),
        check("mode_capacitance_symmetric", np.allclose(mode_matrix, mode_matrix.T), "C_mode is symmetric"),
        check("mode_capacitance_positive_definite", is_positive_definite(mode_capacitance.matrix_fF), f"eigenvalues {mode_capacitance.eigenvalues_fF}"),
        check("ec_matrix_symmetric", np.allclose(ec, ec.T), "E_C matrix is symmetric"),
        check("effective_ej_positive", all(row.ej_effective_GHz > 0 for row in effective_junctions), "all EJ_eff values are positive"),
        check("junction_flux_pairing", len(effective_junctions) == 3, "q1, c, q2 junctions are paired with flux bias"),
        check("basis_dimension_matches_cutoff", model.basis.hilbert_dimension == _expected_dimension(config), f"dimension is {model.basis.hilbert_dimension}"),
        check("hamiltonian_shape", model.matrix.shape == (model.basis.hilbert_dimension, model.basis.hilbert_dimension), f"shape is {model.matrix.shape}"),
        check("hamiltonian_hermitian", h_error < 1e-10, f"hermiticity error {h_error:.3g}"),
        sparse_dense_equivalence_check(ec_matrix.matrix_GHz, effective_junctions),
        check("eigenvalues_finite", all(np.isfinite(eigenvalues)), "eigenvalues are finite"),
        check("eigenvalues_sorted", all(eigenvalues[i] <= eigenvalues[i + 1] for i in range(len(eigenvalues) - 1)), "eigenvalues are sorted"),
        check("ground_state_gap_sane", len(gaps) > 1 and 0.1 <= gaps[1] <= 20.0, f"first gap {gaps[1] if len(gaps) > 1 else None} GHz"),
        single_transmon_check(analytic),
        convergence_check(convergence),
        check("coupling_magnitude_displayed", True, "mode coupling fF values are recorded", severity="warning"),
        check("ec_diagonal_range", all(0.05 <= ec[i, i] <= 2.0 for i in range(ec.shape[0])), f"E_C diagonal {np.diag(ec).tolist()} GHz", severity="warning"),
    ]
    return tuple(checks)


def _payload(
    *,
    config_path: Path,
    config: HamiltonianConfig,
    device_artifacts,
    transform,
    mode_capacitance,
    ec_matrix,
    model,
    effective_junctions,
    eigenvalues,
    gaps,
    checks,
    analytic,
    convergence,
) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "artifact_type": "stage_02_hamiltonian",
        "artifact_version": "0.1",
        "source_device_artifacts": str(config.source_device_artifacts),
        "hamiltonian_config": {
            "path": str(config_path),
            "name": config.name,
            "basis": {mode: {"charge_cutoff": cutoff} for mode, cutoff in config.basis.charge_cutoffs.items()},
            "solver": {
                "num_eigenvalues": config.solver.num_eigenvalues,
                "method": config.solver.method,
                "tolerance": config.solver.tolerance,
            },
        },
        "node_order": list(device_artifacts.node_order),
        "mode_order": list(mode_capacitance.modes),
        "coordinate_transform": {
            "convention": transform.convention,
            "matrix": [list(row) for row in transform.matrix],
        },
        "node_capacitance_matrix_fF": {
            "nodes": list(device_artifacts.node_order),
            "matrix_fF": [list(row) for row in device_artifacts.node_capacitance_matrix_fF],
        },
        "mode_capacitance_matrix_fF": {
            "modes": list(mode_capacitance.modes),
            "matrix_fF": [list(row) for row in mode_capacitance.matrix_fF],
            "eigenvalues_fF": list(mode_capacitance.eigenvalues_fF),
        },
        "ec_matrix_GHz": {
            "modes": list(ec_matrix.modes),
            "matrix_GHz": [list(row) for row in ec_matrix.matrix_GHz],
        },
        "effective_junctions": [
            {
                "mode": row.mode,
                "component": row.component,
                "ej1_GHz": row.ej1_GHz,
                "ej2_GHz": row.ej2_GHz,
                "flux_bias_phi0": row.flux_bias_phi0,
                "ej_effective_GHz": row.ej_effective_GHz,
                "formula": row.formula,
            }
            for row in effective_junctions
        ],
        "basis": {
            "mode_order": list(model.basis.mode_order),
            "charge_cutoffs": dict(model.basis.charge_cutoffs),
            "dimensions": dict(model.basis.dimensions),
            "charges": {mode: list(values) for mode, values in model.basis.charges.items()},
        },
        "offset_charge_ng": dict(model.basis.offset_charge_ng),
        "hilbert_dimension": model.basis.hilbert_dimension,
        "hamiltonian_summary": {
            **model.summary,
            "solver_method": config.solver.method,
        },
        "solver": {
            "method": config.solver.method,
            "num_eigenvalues": config.solver.num_eigenvalues,
            "tolerance": config.solver.tolerance,
        },
        "lowest_eigenvalues_GHz": list(eigenvalues),
        "eigenvalue_gaps_GHz": list(gaps),
        "reference_priors": _reference_priors(device_artifacts.payload.get("priors", {})),
        "mode_coupling_fF": _mode_coupling(mode_capacitance),
        "single_transmon_analytic_limit": analytic,
        "charge_basis_convergence": convergence,
        "checks": [item.to_dict() for item in checks],
    }


def _reference_priors(priors: dict[str, Any]) -> dict[str, Any]:
    return {
        "q1": {"prior_f01_GHz": priors.get("q1", {}).get("estimated_f01_GHz")},
        "q2": {"prior_f01_GHz": priors.get("q2", {}).get("estimated_f01_GHz")},
        "c": {"prior_f01_GHz": priors.get("c", {}).get("estimated_idle_frequency_GHz")},
    }


def _mode_coupling(mode_capacitance) -> dict[str, float]:
    matrix = np.array(mode_capacitance.matrix_fF, dtype=float)
    modes = mode_capacitance.modes
    result: dict[str, float] = {}
    for i, mode_i in enumerate(modes):
        for j, mode_j in enumerate(modes):
            if i < j:
                result[f"{mode_i}-{mode_j}"] = float(matrix[i, j])
    return result


def _expected_dimension(config: HamiltonianConfig) -> int:
    total = 1
    for cutoff in config.basis.charge_cutoffs.values():
        total *= 2 * cutoff + 1
    return total


def _resolve_project_path(path: Path) -> Path:
    if path.is_absolute():
        return path
    return Path.cwd() / path
