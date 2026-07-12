import copy
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


def test_flux_override_none_and_empty_are_field_compatible():
    device = load_device_artifacts(ARTIFACTS)
    original = resolve_effective_junctions(device)
    assert resolve_effective_junctions(device, None) == original
    assert resolve_effective_junctions(device, {}) == original


def test_flux_overrides_apply_to_each_supported_mode_deterministically():
    device = load_device_artifacts(ARTIFACTS)
    overrides = {"q1": -0.125, "c": 0.375, "q2": 0.5}
    first = resolve_effective_junctions(device, overrides)
    second = resolve_effective_junctions(device, overrides)
    assert first == second
    assert {row.mode: row.flux_bias_phi0 for row in first} == overrides


@pytest.mark.parametrize(
    "overrides",
    [
        {"r1": 0.1},
        {"c": True},
        {"c": float("nan")},
        {"c": float("inf")},
        {"c": -float("inf")},
        {"c": 1 + 2j},
        {"c": "0.1"},
    ],
)
def test_flux_override_rejects_invalid_keys_and_values(overrides):
    device = load_device_artifacts(ARTIFACTS)
    with pytest.raises(ValueError):
        resolve_effective_junctions(device, overrides)


def test_flux_override_does_not_mutate_inputs_or_disk():
    device = load_device_artifacts(ARTIFACTS)
    payload_before = copy.deepcopy(device.payload)
    artifact_bytes_before = ARTIFACTS.read_bytes()
    overrides = {"c": 0.3125}
    overrides_before = dict(overrides)

    resolve_effective_junctions(device, overrides)

    assert device.payload == payload_before
    assert overrides == overrides_before
    assert ARTIFACTS.read_bytes() == artifact_bytes_before
