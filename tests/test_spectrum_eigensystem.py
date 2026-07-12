import numpy as np
import pytest

from sqvm.spectrum import (
    rebuild_hamiltonian_for_spectrum,
    run_stage2_dense_gap_consistency,
    solve_static_eigensystem,
)


def test_rebuild_hamiltonian_for_spectrum(spectrum_session):
    model = rebuild_hamiltonian_for_spectrum(spectrum_session["context"])
    assert model.matrix.shape == (3375, 3375)
    assert model.basis.charge_cutoffs == {"q1": 7, "c": 7, "q2": 7}


def test_eigenvalues_are_sorted(spectrum_session):
    values = spectrum_session["baseline"].eigenstates.eigenvalues_GHz
    assert np.all(np.diff(values) >= 0)


def test_eigenvectors_are_normalized(spectrum_session):
    vectors = spectrum_session["baseline"].eigenstates.eigenvectors
    assert np.allclose(np.linalg.norm(vectors, axis=0), 1.0, atol=1e-10)


def test_eigenvectors_are_orthogonal(spectrum_session):
    vectors = spectrum_session["baseline"].eigenstates.eigenvectors
    assert np.allclose(vectors.T @ vectors, np.eye(vectors.shape[1]), atol=1e-10)


def test_eigenvector_columns_match_eigenvalues(spectrum_session):
    model = rebuild_hamiltonian_for_spectrum(spectrum_session["context"])
    table = spectrum_session["baseline"].eigenstates
    residual = model.matrix @ table.eigenvectors - table.eigenvectors * table.eigenvalues_GHz
    assert np.max(np.abs(residual)) < 1e-9


def test_stage2_gap_consistency_is_error_at_1e_9_GHz(spectrum_session, monkeypatch):
    import sqvm.spectrum.provenance as module

    class FakeReport:
        ok = False
        max_abs_difference_GHz = 1.1e-9
        compared_gap_count = 12
        errors = ("exceeds 1e-9",)

    monkeypatch.setattr(module, "rebuild_stage2_low_energy_spectrum", lambda *args: FakeReport())
    report = run_stage2_dense_gap_consistency(spectrum_session["config"], spectrum_session["provenance"])
    assert not report.ok
    assert report.max_abs_difference_GHz > 1e-9


def test_flux_override_rebuild_does_not_mutate_context(spectrum_session):
    context = spectrum_session["context"]
    before = tuple(context.effective_junctions)
    model = rebuild_hamiltonian_for_spectrum(context, {"c": 0.35})
    assert tuple(context.effective_junctions) == before
    assert next(row.flux_bias_phi0 for row in model.effective_junctions if row.mode == "c") == pytest.approx(0.35)
