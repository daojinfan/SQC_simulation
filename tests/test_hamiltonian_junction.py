from math import cos, pi
from pathlib import Path

import pytest

from sqvm.hamiltonian import load_device_artifacts, resolve_effective_junctions
from sqvm.hamiltonian.junction import effective_ej_GHz


ARTIFACTS = Path("output/stage_01_device_model/device_artifacts.json")


def test_effective_ej_symmetric_squid_limit():
    value = effective_ej_GHz(10.0, 10.0, 0.25)
    assert value == pytest.approx(20.0 * abs(cos(pi * 0.25)))


def test_effective_ej_asymmetric_formula():
    value = effective_ej_GHz(12.0, 8.0, 0.3)
    expected = ((20.0 * cos(pi * 0.3)) ** 2 + (4.0 * __import__("math").sin(pi * 0.3)) ** 2) ** 0.5
    assert value == pytest.approx(expected)


def test_resolve_effective_junctions_uses_flux_bias():
    rows = resolve_effective_junctions(load_device_artifacts(ARTIFACTS))
    q1 = next(row for row in rows if row.mode == "q1")
    assert q1.flux_bias_phi0 == pytest.approx(0.1)
    assert q1.ej_effective_GHz > 0
