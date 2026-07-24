from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

import numpy as np
import pytest

from sqvm.evolution.physics import angular_rad_per_ns


POPULATION_ABS_ERROR = 1.0e-10


def _numerical_evidence(delta_GHz: float, epsilon_GHz: complex, times_ns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import qutip

    hamiltonian_GHz = qutip.Qobj(
        np.asarray(
            [
                [-0.5 * delta_GHz, 0.5 * np.conj(epsilon_GHz)],
                [0.5 * epsilon_GHz, 0.5 * delta_GHz],
            ],
            dtype=np.complex128,
        )
    )
    result = qutip.sesolve(
        angular_rad_per_ns(hamiltonian_GHz),
        qutip.basis(2, 0),
        times_ns,
        e_ops=[qutip.basis(2, 1).proj()],
        options={
            "method": "vern9", "rtol": 1.0e-13, "atol": 1.0e-15,
            "nsteps": 100000, "max_step": 0.0025, "normalize_output": False,
            "progress_bar": None, "store_states": True,
        },
    )
    population = np.asarray(result.expect[0], dtype="<f8")
    norm_error = np.asarray([abs(float(state.norm()) ** 2 - 1.0) for state in result.states], dtype="<f8")
    return population, norm_error


def _analytic_population(delta_GHz: float, epsilon_GHz: complex, times_ns: np.ndarray) -> np.ndarray:
    delta = 2.0 * np.pi * delta_GHz
    omega = 2.0 * np.pi * abs(epsilon_GHz)
    generalized = np.sqrt(delta**2 + omega**2)
    if generalized == 0.0:
        return np.zeros(times_ns.size, dtype="<f8")
    return np.asarray(
        (omega**2 / generalized**2) * np.sin(0.5 * generalized * times_ns) ** 2,
        dtype="<f8",
    )


def test_zero_drive_oracle_preserves_ground_population_and_norm():
    times = np.asarray([0.0, 0.25, 0.5, 1.0], dtype="<f8")
    population, norm_error = _numerical_evidence(0.0, 0.0j, times)
    assert np.max(np.abs(population)) <= POPULATION_ABS_ERROR
    assert np.max(norm_error) <= 1.0e-9


@pytest.mark.parametrize("epsilon", (0.20 + 0.0j, 0.12 + 0.16j))
def test_constant_detuned_drive_matches_closed_form_population(epsilon):
    times = np.linspace(0.0, 4.0, 33, dtype="<f8")
    numerical, _ = _numerical_evidence(0.125, epsilon, times)
    expected = _analytic_population(0.125, epsilon, times)
    assert np.max(np.abs(numerical - expected)) <= POPULATION_ABS_ERROR


def test_resonant_rabi_pi_time_reaches_excited_state():
    epsilon_GHz = 0.25
    pi_time_ns = 1.0 / (2.0 * epsilon_GHz)
    population, _ = _numerical_evidence(0.0, epsilon_GHz, np.asarray([0.0, pi_time_ns], dtype="<f8"))
    assert population[0] == pytest.approx(0.0, abs=POPULATION_ABS_ERROR)
    assert population[1] == pytest.approx(1.0, abs=POPULATION_ABS_ERROR)


def test_oracle_detects_missing_or_duplicate_angular_conversion():
    epsilon_GHz = 0.25
    pi_time_ns = 1.0 / (2.0 * epsilon_GHz)
    expected = _analytic_population(0.0, epsilon_GHz, np.asarray([pi_time_ns], dtype="<f8"))[0]
    missing = np.sin(0.5 * epsilon_GHz * pi_time_ns) ** 2
    duplicate = np.sin((2.0 * np.pi) ** 2 * 0.5 * epsilon_GHz * pi_time_ns) ** 2
    assert abs(missing - expected) > 0.1
    assert abs(duplicate - expected) > 0.1
