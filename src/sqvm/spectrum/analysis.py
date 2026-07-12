"""Bare-energy labeling, participation, metrics, and single-point analysis."""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import linalg
from scipy.optimize import linear_sum_assignment

from sqvm.hamiltonian.basis import cos_phi_operator
from sqvm.spectrum.context import rebuild_hamiltonian_for_spectrum
from sqvm.spectrum.models import (
    BareStateCatalog,
    BareStateLabel,
    DressedLabelConfig,
    DressedStateAssignment,
    DressedStateTable,
    EigenstateTable,
    ModeParticipationRow,
    ModeParticipationTable,
    SpectrumBuildContext,
    SpectrumConfig,
    StaticMetricTable,
    StaticSpectrumPointResult,
)
from sqvm.spectrum.solver import canonical_flux_text, solve_static_eigensystem


def build_bare_state_catalog(model: Any, label_config: DressedLabelConfig) -> BareStateCatalog:
    dimensions = dict(model.basis.dimensions)
    junctions = {row.mode: row.ej_effective_GHz for row in model.effective_junctions}
    mode_eigenvectors: dict[str, np.ndarray] = {}
    mode_eigenvalues: dict[str, np.ndarray] = {}
    for index, mode in enumerate(model.basis.mode_order):
        charges = np.asarray(model.basis.charges[mode], dtype=float)
        ec = float(model.ec_matrix_GHz[index][index])
        dense = np.diag(4.0 * ec * charges * charges) - junctions[mode] * cos_phi_operator(
            dimensions[mode], sparse_output=False
        )
        values, vectors = linalg.eigh(dense)
        mode_eigenvalues[mode] = np.asarray(values, dtype=float)
        mode_eigenvectors[mode] = np.asarray(vectors, dtype=float)

    labels: list[BareStateLabel] = []
    for q1 in range(label_config.max_excitations["q1"] + 1):
        for c in range(label_config.max_excitations["c"] + 1):
            for q2 in range(label_config.max_excitations["q2"] + 1):
                label = BareStateLabel(q1, c, q2)
                if label.total_excitations <= label_config.max_total_excitations:
                    labels.append(label)
    labels.sort(key=lambda row: (row.total_excitations, row.values))
    target_vectors = np.column_stack(
        [
            np.kron(
                np.kron(mode_eigenvectors["q1"][:, label.q1], mode_eigenvectors["c"][:, label.c]),
                mode_eigenvectors["q2"][:, label.q2],
            )
            for label in labels
        ]
    )
    return BareStateCatalog(
        labels=tuple(labels),
        target_vectors=target_vectors,
        mode_eigenvectors=mode_eigenvectors,
        mode_eigenvalues_GHz=mode_eigenvalues,
        dimensions=dimensions,
    )


def assign_dressed_states(
    eigenstates: EigenstateTable,
    bare_catalog: BareStateCatalog,
    label_config: DressedLabelConfig,
) -> DressedStateTable:
    overlap = np.abs(bare_catalog.target_vectors.conj().T @ eigenstates.eigenvectors) ** 2
    rows, columns = overlap.shape
    if rows > columns:
        raise ValueError("target bare labels exceed solved eigenstates")
    tie = np.arange(rows * columns, dtype=float).reshape(rows, columns) * np.finfo(float).eps
    label_indices, eigen_indices = linear_sum_assignment(-overlap + tie)
    assigned = dict(zip(label_indices.tolist(), eigen_indices.tolist(), strict=True))
    assignments: list[DressedStateAssignment] = []
    warnings: list[str] = []
    ground = float(eigenstates.eigenvalues_GHz[0])
    for label_index, label in enumerate(bare_catalog.labels):
        eigen_index = assigned[label_index]
        probability = float(overlap[label_index, eigen_index])
        status = "assigned" if probability >= label_config.min_overlap else "low_overlap"
        if status == "low_overlap":
            warnings.append(f"{label.key} overlap {probability:.6g} is below {label_config.min_overlap}")
        energy = float(eigenstates.eigenvalues_GHz[eigen_index])
        assignments.append(
            DressedStateAssignment(
                label=label,
                eigen_index=eigen_index,
                energy_GHz=energy,
                gap_from_ground_GHz=energy - ground,
                overlap=probability,
                status=status,
            )
        )
    return DressedStateTable(assignments=tuple(assignments), overlap_matrix=overlap, warnings=tuple(warnings))


def compute_mode_participation(
    eigenstates: EigenstateTable,
    bare_catalog: BareStateCatalog,
    dressed_states: DressedStateTable,
) -> ModeParticipationTable:
    dimensions = tuple(bare_catalog.dimensions[mode] for mode in ("q1", "c", "q2"))
    uq1 = bare_catalog.mode_eigenvectors["q1"].conj()
    uc = bare_catalog.mode_eigenvectors["c"].conj()
    uq2 = bare_catalog.mode_eigenvectors["q2"].conj()
    excitation_grids = np.meshgrid(
        np.arange(dimensions[0], dtype=float),
        np.arange(dimensions[1], dtype=float),
        np.arange(dimensions[2], dtype=float),
        indexing="ij",
    )
    rows: list[ModeParticipationRow] = []
    for assignment in dressed_states.assignments:
        charge_tensor = eigenstates.eigenvectors[:, assignment.eigen_index].reshape(dimensions)
        beta = np.einsum("ia,jb,kc,ijk->abc", uq1, uc, uq2, charge_tensor, optimize=True)
        probability = np.abs(beta) ** 2
        mean = {
            mode: float(np.sum(grid * probability))
            for mode, grid in zip(("q1", "c", "q2"), excitation_grids, strict=True)
        }
        total = float(sum(mean.values()))
        fractions = (
            None
            if assignment.label.key == "000" or total < 1e-12
            else {mode: value / total for mode, value in mean.items()}
        )
        rows.append(
            ModeParticipationRow(
                label=assignment.label.key,
                eigen_index=assignment.eigen_index,
                mean_excitations=mean,
                fractions=fractions,
                total_mean_excitation=total,
            )
        )
    return ModeParticipationTable(rows=tuple(rows))


def compute_static_metrics(
    dressed_states: DressedStateTable,
    eigenstates: EigenstateTable,
    participation: ModeParticipationTable,
) -> StaticMetricTable:
    del eigenstates, participation
    assigned = dressed_states.by_label()
    required = {"000", "100", "010", "001", "200", "020", "002", "101", "110", "011"}
    missing = sorted(required - set(assigned))
    if missing:
        raise ValueError(f"required metric labels are missing: {missing}")
    energy = {key: assigned[key].energy_GHz for key in required}
    transition = {
        "q1_01": energy["100"] - energy["000"],
        "c_01": energy["010"] - energy["000"],
        "q2_01": energy["001"] - energy["000"],
    }
    anharmonicity = {
        "q1_alpha": energy["200"] - 2.0 * energy["100"] + energy["000"],
        "c_alpha": energy["020"] - 2.0 * energy["010"] + energy["000"],
        "q2_alpha": energy["002"] - 2.0 * energy["001"] + energy["000"],
    }
    zz = {
        "zz_q1_q2": energy["101"] - energy["100"] - energy["001"] + energy["000"],
        "zz_q1_c": energy["110"] - energy["100"] - energy["010"] + energy["000"],
        "zz_c_q2": energy["011"] - energy["010"] - energy["001"] + energy["000"],
    }
    warnings = tuple(
        f"{key} has low overlap"
        for key in ("200", "020", "002")
        if assigned[key].status == "low_overlap"
    )
    return StaticMetricTable(
        transition_frequencies_GHz=transition,
        anharmonicities_GHz=anharmonicity,
        zz_metrics_GHz=zz,
        warnings=warnings,
    )


def analyze_static_point(
    context: SpectrumBuildContext,
    config: SpectrumConfig,
    flux_overrides_phi0: dict[str, float] | None = None,
    basis_overrides: dict[str, int] | None = None,
) -> StaticSpectrumPointResult:
    model = rebuild_hamiltonian_for_spectrum(context, flux_overrides_phi0, basis_overrides)
    eigenstates = solve_static_eigensystem(model, context.solver_spec)
    bare_catalog = build_bare_state_catalog(model, config.dressed_labeling)
    dressed = assign_dressed_states(eigenstates, bare_catalog, config.dressed_labeling)
    participation = compute_mode_participation(eigenstates, bare_catalog, dressed)
    metrics = compute_static_metrics(dressed, eigenstates, participation)
    bare_frequencies = {
        mode: float(values[1] - values[0])
        for mode, values in bare_catalog.mode_eigenvalues_GHz.items()
    }
    flux = next(row.flux_bias_phi0 for row in model.effective_junctions if row.mode == "c")
    return StaticSpectrumPointResult(
        flux_key=canonical_flux_text(flux),
        flux_bias_phi0=float(flux),
        cutoffs=dict(model.basis.charge_cutoffs),
        eigenstates=eigenstates,
        bare_catalog=bare_catalog,
        dressed_states=dressed,
        participation=participation,
        metrics=metrics,
        bare_transition_frequencies_GHz=bare_frequencies,
    )
