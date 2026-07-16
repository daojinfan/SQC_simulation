from __future__ import annotations

import numpy as np
import pytest

from sqvm.evolution.stage51_models import Stage51EvolutionError
from sqvm.evolution.stage51_physics import _phase_fixed, _projector_evidence, phase_invariant_overlap


def test_phase_fix_is_global_phase_deterministic():
    source = np.asarray([1.0 + 2.0j, -0.25j, 0.5], dtype="<c16")
    expected = _phase_fixed(source)
    actual = _phase_fixed(source * np.exp(0.73j))
    assert expected.tobytes() == actual.tobytes()
    pivot = int(np.flatnonzero(np.abs(actual) == np.abs(actual).max())[0])
    assert actual[pivot].imag == 0.0
    assert actual[pivot].real >= 0.0


def test_projector_evidence_accepts_four_orthogonal_projectors():
    projectors = {}
    for index, label in enumerate(("000", "100", "001", "101")):
        vector = np.zeros(4, dtype="<c16")
        vector[index] = 1.0
        projectors[label] = np.outer(vector, vector.conj())
    hashes, checks = _projector_evidence(projectors, 1e-12)
    assert set(hashes) == set(projectors)
    assert len(checks) == 10
    assert all(row["passed"] for row in checks)


@pytest.mark.parametrize("failure", ("not_idempotent", "not_orthogonal"))
def test_projector_evidence_rejects_invalid_algebra(failure):
    projectors = {}
    for index, label in enumerate(("000", "100", "001", "101")):
        vector = np.zeros(4, dtype="<c16")
        vector[index] = 1.0
        projectors[label] = np.outer(vector, vector.conj())
    if failure == "not_idempotent":
        projectors["000"] = 0.5 * projectors["000"]
    else:
        projectors["100"] = projectors["000"].copy()
    with pytest.raises(Stage51EvolutionError):
        _projector_evidence(projectors, 1e-12)


def test_phase_invariant_overlap_normalizes_inputs():
    left = np.asarray([1.0, 1.0j], dtype="<c16")
    right = 3.0 * np.exp(0.42j) * left
    assert phase_invariant_overlap(left, right) == pytest.approx(1.0)
