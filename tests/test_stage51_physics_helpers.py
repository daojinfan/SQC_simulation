from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from sqvm.evolution.stage51_models import Stage51EvolutionError
from sqvm.evolution.stage51_physics import _phase_fixed, _physics_preflight, _projector_evidence, phase_invariant_overlap
from sqvm.evolution.input import load_stage5_input
from sqvm.evolution.physics import _smoke_window


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "configs/evolution/2q1c_qutip_smoke.yaml"


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


def test_real_accepted_model_passes_complete_stage51_physics_preflight():
    accepted = load_stage5_input(SMOKE, ROOT)
    source = accepted.scenarios["xy_drag"]
    scenario = replace(_smoke_window(source, 97, 4), scenario_id="stage51")
    admission = replace(accepted.admission, config=replace(accepted.admission.config, profile="stage51"))
    stage51 = replace(accepted, admission=admission, scenarios={"stage51": scenario})

    hashes, checks = _physics_preflight(stage51)

    assert set(hashes) == {"000", "100", "001", "101"}
    assert len(checks) == 10
    assert all(check["passed"] for check in checks)
