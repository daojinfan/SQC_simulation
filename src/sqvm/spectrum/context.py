"""Verified Stage 3 build context and Stage 2 Hamiltonian reconstruction."""

from __future__ import annotations

from dataclasses import replace
import math
from numbers import Real
from pathlib import Path
from typing import Mapping

from sqvm.hamiltonian import (
    BasisConfig,
    build_ec_matrix,
    build_hamiltonian,
    build_mode_capacitance_matrix,
    build_mode_transform,
    load_device_artifacts,
    load_hamiltonian_config,
    resolve_effective_junctions,
)
from sqvm.spectrum.models import ProvenanceReport, SolverBackendReport, SpectrumBuildContext, SpectrumConfig


def build_spectrum_context(
    config: SpectrumConfig,
    provenance: ProvenanceReport,
    solver_backend_report: SolverBackendReport,
) -> SpectrumBuildContext:
    if not provenance.ok:
        raise ValueError("spectrum provenance must pass before context construction")
    if not solver_backend_report.ok or solver_backend_report.validated_spec is None:
        raise ValueError("solver backend report must provide a valid solver spec")
    hamiltonian_config = load_hamiltonian_config(config.source_hamiltonian_config)
    device_path = _resolve_project_path(hamiltonian_config.source_device_artifacts)
    device = load_device_artifacts(device_path)
    transform = build_mode_transform(device)
    mode_capacitance = build_mode_capacitance_matrix(device, transform)
    ec_matrix = build_ec_matrix(mode_capacitance)
    junctions = resolve_effective_junctions(device)
    return SpectrumBuildContext(
        config=config,
        provenance=provenance,
        solver_backend_report=solver_backend_report,
        solver_spec=solver_backend_report.validated_spec,
        hamiltonian_config=hamiltonian_config,
        device_artifacts=device,
        ec_matrix_GHz=ec_matrix.matrix_GHz,
        mode_capacitance_matrix_fF=mode_capacitance.matrix_fF,
        effective_junctions=junctions,
    )


def rebuild_hamiltonian_for_spectrum(
    context: SpectrumBuildContext,
    flux_overrides_phi0: Mapping[str, float] | None = None,
    basis_overrides: Mapping[str, int] | None = None,
):
    if flux_overrides_phi0:
        if set(flux_overrides_phi0) - {"q1", "c", "q2"}:
            raise ValueError("spectrum flux overrides only support q1, c, q2")
        for mode, value in flux_overrides_phi0.items():
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(float(value)):
                raise ValueError(f"flux override for {mode} must be a finite real Phi0 value")
    cutoffs = dict(context.hamiltonian_config.basis.charge_cutoffs)
    if basis_overrides:
        if set(basis_overrides) - {"q1", "c", "q2"}:
            raise ValueError("basis overrides only support q1, c, q2")
        for mode, value in basis_overrides.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"basis override for {mode} must be a positive integer")
            cutoffs[mode] = value
    config = replace(context.hamiltonian_config, basis=BasisConfig(charge_cutoffs=cutoffs))
    junctions = resolve_effective_junctions(context.device_artifacts, flux_overrides_phi0)
    return build_hamiltonian(config, context.ec_matrix_GHz, junctions)


def _resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else Path.cwd() / path
