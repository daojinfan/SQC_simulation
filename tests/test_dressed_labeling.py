import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

from dataclasses import replace

import numpy as np

from sqvm.spectrum import (
    BareStateCatalog,
    BareStateLabel,
    EigenstateTable,
    assign_dressed_states,
)


def test_bare_catalog_contains_required_labels(spectrum_session):
    labels = {row.key for row in spectrum_session["baseline"].bare_catalog.labels}
    assert {"000", "100", "010", "001", "110", "101", "011", "200", "020", "002"} <= labels


def test_assign_ground_state(spectrum_session):
    assignment = spectrum_session["baseline"].dressed_states.by_label()["000"]
    assert assignment.eigen_index == 0


def test_assign_single_excitation_states(spectrum_session):
    assigned = spectrum_session["baseline"].dressed_states.by_label()
    assert all(label in assigned for label in ("100", "010", "001"))


def test_low_overlap_produces_warning(spectrum_session):
    config = replace(spectrum_session["config"].dressed_labeling, min_overlap=1.0)
    table = assign_dressed_states(
        spectrum_session["baseline"].eigenstates,
        spectrum_session["baseline"].bare_catalog,
        config,
    )
    assert table.warnings
    assert any(row.status == "low_overlap" for row in table.assignments)


def test_global_assignment_is_one_to_one(spectrum_session):
    indices = [row.eigen_index for row in spectrum_session["baseline"].dressed_states.assignments]
    assert len(indices) == len(set(indices))


def _assignment_fixture(overlap):
    target_vectors = np.sqrt(np.asarray(overlap, dtype=float)).T
    catalog = BareStateCatalog(
        labels=(BareStateLabel(0, 0, 0), BareStateLabel(1, 0, 0)),
        target_vectors=target_vectors,
        mode_eigenvectors={},
        mode_eigenvalues_GHz={},
        dimensions={},
    )
    eigen = EigenstateTable(
        eigenvalues_GHz=np.array([0.0, 1.0]),
        eigenvectors=np.eye(2),
        backend="dense_eigh",
        solve_time_seconds=0.0,
        cutoffs=(1, 1, 1),
        flux_key="0.000000000000",
    )
    return catalog, eigen


def test_global_assignment_beats_greedy_counterexample(spectrum_session):
    catalog, eigen = _assignment_fixture([[0.9, 0.8], [0.85, 0.1]])
    table = assign_dressed_states(eigen, catalog, spectrum_session["config"].dressed_labeling)
    assert [row.eigen_index for row in table.assignments] == [1, 0]
    assert sum(row.overlap for row in table.assignments) == 1.65


def test_assignment_is_deterministic_for_ties(spectrum_session):
    catalog, eigen = _assignment_fixture([[0.5, 0.5], [0.5, 0.5]])
    first = assign_dressed_states(eigen, catalog, spectrum_session["config"].dressed_labeling)
    second = assign_dressed_states(eigen, catalog, spectrum_session["config"].dressed_labeling)
    assert [row.eigen_index for row in first.assignments] == [row.eigen_index for row in second.assignments]
