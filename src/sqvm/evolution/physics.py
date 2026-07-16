"""Frozen Stage 5 interaction-picture physics on the reconstructed input snapshot."""

from __future__ import annotations

from decimal import Decimal
from itertools import permutations
import math
from typing import Any, Mapping

import numpy as np
from scipy import linalg

from sqvm.evolution.models import EffectiveScenario, FormalScaleQualificationRequired, Stage5Input, Stage5ScenarioResult
from sqvm.hamiltonian import build_hamiltonian, resolve_effective_junctions
from sqvm.hamiltonian.basis import cos_phi_operator


MODE_ORDER = ("q1", "c", "q2")
LABELS = ("000", "100", "001", "101")


def angular_rad_per_ns(value_GHz):
    """The sole GHz-to-angular-radians-per-nanosecond conversion."""

    return 2.0 * np.pi * value_GHz


def zoh_edges(time_center_ns: np.ndarray) -> np.ndarray:
    """Derive the exact v0.1 zero-order-hold edges with Decimal arithmetic."""

    if time_center_ns.ndim != 1 or time_center_ns.size == 0:
        raise ValueError("time centers must be a non-empty vector")
    centers = [Decimal(str(value)) for value in time_center_ns]
    edges = [centers[0] - Decimal("0.25")]
    edges.extend(center + Decimal("0.25") for center in centers)
    return np.asarray([float(value) for value in edges], dtype=float)


def evolve_stage5_scenario(stage5_input: Stage5Input, scenario_id: str, *, calculation: str = "primary") -> Stage5ScenarioResult:
    """Evolve one admitted scenario without publishing any artifact."""

    if stage5_input.admission.config.profile == "formal":
        raise FormalScaleQualificationRequired("formal numerical execution is excluded from v0.2 and requires separate qualification")
    if calculation != "primary":
        raise ValueError("only calculation='primary' is implemented for the stable Stage 5 API")
    scenario = stage5_input.scenarios.get(scenario_id)
    if scenario is None:
        raise ValueError(f"scenario is not admitted: {scenario_id}")
    original_sample_count = int(scenario.time_center_ns.size)
    window_start_index = 0
    if stage5_input.admission.config.profile == "smoke":
        window_start_index = stage5_input.admission.config.smoke_window_start_index or 0
        scenario = _smoke_window(scenario, window_start_index, stage5_input.admission.config.smoke_sample_count)
    qt = _qutip()
    _validate_qutip_version(qt.__version__)
    edges = zoh_edges(scenario.time_center_ns)
    frame = _build_frame(stage5_input, scenario, qt)
    reference = _lab_reference(stage5_input, scenario, frame, qt)
    static_cache: dict[int, Any] = {}
    identity = qt.Qobj(np.eye(frame["dimension"], dtype=complex), dims=frame["dims"])

    def raw_hamiltonian(t, _args=None):
        index = min(max(int(np.searchsorted(edges, float(t), side="right") - 1), 0), scenario.time_center_ns.size - 1)
        h_static = static_cache.get(index)
        if h_static is None:
            h_static = _static_hamiltonian(stage5_input, scenario, index, qt)
            static_cache[index] = h_static
        u = frame["u"](float(t))
        epsilon = {mode: scenario.xy_iq_GHz[mode][0][index] + 1j * scenario.xy_iq_GHz[mode][1][index] for mode in ("q1", "q2")}
        drive = sum((0.5 * (epsilon[mode] * frame["d_plus"][mode] + np.conj(epsilon[mode]) * frame["d_plus"][mode].dag()) for mode in ("q1", "q2")), qt.Qobj(np.zeros((frame["dimension"], frame["dimension"]), dtype=complex), dims=frame["dims"]))
        hip = u.dag() * h_static * u - frame["f_generator"] + drive
        if not hip.isherm or float((hip - hip.dag()).norm()) > 1e-10:
            raise ValueError("interaction-picture Hamiltonian is not Hermitian")
        return angular_rad_per_ns(hip)

    def gauged_hamiltonian(t, _args=None):
        hamiltonian = raw_hamiltonian(t)
        energy = _identity_energy_gauge(hamiltonian)
        return hamiltonian - energy * identity

    theta = _gauge_phase_edges(edges, raw_hamiltonian)
    h_evo = qt.QobjEvo(gauged_hamiltonian)
    options = {"method": "vern9", "rtol": 1e-13, "atol": 1e-15, "nsteps": 100000, "max_step": 0.0025, "store_states": True, "store_final_state": True, "normalize_output": False, "progress_bar": None}
    result = qt.sesolve(h_evo, reference["psi_ip"], edges, e_ops=[], options=options)
    physical_states = tuple(state * np.exp(-1j * theta[index]) for index, state in enumerate(result.states))
    states = tuple(np.asarray(state.full(), dtype=complex).reshape(-1) for state in physical_states)
    populations = {label: np.empty(len(states), dtype=float) for label in LABELS}
    leakage = np.empty(len(states), dtype=float)
    norm_error = np.empty(len(states), dtype=float)
    comp = sum((reference["projectors"][label] for label in LABELS), qt.Qobj(np.zeros((frame["dimension"], frame["dimension"]), dtype=complex), dims=frame["dims"]))
    for index, state in enumerate(physical_states):
        u = frame["u"](float(edges[index]))
        for label in LABELS:
            projector_ip = u.dag() * reference["projectors"][label] * u
            populations[label][index] = float(np.real(_expectation(state, projector_ip)))
        comp_ip = u.dag() * comp * u
        leakage[index] = 1.0 - float(np.real(_expectation(state, comp_ip)))
        norm_error[index] = abs(float(np.real(_expectation(state, None))) - 1.0)
    checks = _scenario_checks(populations, leakage, norm_error, stage5_input.admission.config.tolerances)
    metadata = {**reference["metadata"], "identity_energy_gauge": {"theta_edge_rad": theta.tolist(), "solver": "qutip.sesolve"}}
    return Stage5ScenarioResult(scenario_id, calculation, edges, states, populations, leakage, norm_error, original_sample_count, window_start_index, int(scenario.time_center_ns.size), tuple(checks), metadata, _control_digest(scenario))


def _build_frame(stage5_input: Stage5Input, scenario: EffectiveScenario, qt):
    cutoffs = dict(zip(MODE_ORDER, stage5_input.admission.config.charge_cutoffs, strict=True))
    first_flux = _flux_for_hamiltonian(scenario, 0)
    junctions = resolve_effective_junctions(stage5_input.rebuilt_model.device_artifacts, first_flux)
    local_vectors: dict[str, np.ndarray] = {}
    local_numbers: dict[str, np.ndarray] = {}
    local_dims = tuple(2 * cutoffs[mode] + 1 for mode in MODE_ORDER)
    for index, mode in enumerate(MODE_ORDER):
        dimension = local_dims[index]
        charges = np.arange(-cutoffs[mode], cutoffs[mode] + 1, dtype=float)
        ej = next(row.ej_effective_GHz for row in junctions if row.mode == mode)
        local_h = np.diag(4.0 * stage5_input.rebuilt_model.ec_matrix_GHz[index][index] * charges**2) - ej * cos_phi_operator(dimension, sparse_output=False)
        _, vectors = linalg.eigh(local_h)
        local_vectors[mode] = _phase_fixed(vectors)
        local_numbers[mode] = local_vectors[mode] @ np.diag(np.arange(dimension, dtype=float)) @ local_vectors[mode].conj().T
    dims = [list(local_dims), list(local_dims)]
    identities = {mode: qt.qeye(local_dims[index]) for index, mode in enumerate(MODE_ORDER)}
    n_sector = {mode: _tensor_operator({mode: qt.Qobj(local_numbers[mode])}, identities, qt) for mode in ("q1", "q2")}
    f_generator = scenario.carrier_frequency_GHz["q1"] * n_sector["q1"] + scenario.carrier_frequency_GHz["q2"] * n_sector["q2"]

    generator_values, generator_vectors = linalg.eigh(f_generator.full())

    def u(time_ns: float):
        phases = np.exp(-1j * angular_rad_per_ns(float(time_ns) * generator_values))
        return qt.Qobj((generator_vectors * phases) @ generator_vectors.conj().T, dims=dims)

    d_plus = {}
    for mode in ("q1", "q2"):
        charge_values = np.arange(-cutoffs[mode], cutoffs[mode] + 1, dtype=float)
        n_energy = local_vectors[mode].conj().T @ np.diag(charge_values) @ local_vectors[mode]
        matrix = np.zeros_like(n_energy, dtype=complex)
        for level in range(n_energy.shape[0] - 1):
            matrix[level + 1, level] = n_energy[level + 1, level]
        d_local = local_vectors[mode] @ matrix @ local_vectors[mode].conj().T
        d_plus[mode] = _tensor_operator({mode: qt.Qobj(d_local)}, identities, qt)
    return {"u": u, "d_plus": d_plus, "f_generator": f_generator, "local_vectors": local_vectors, "dims": dims, "dimension": int(np.prod(local_dims))}


def _lab_reference(stage5_input: Stage5Input, scenario: EffectiveScenario, frame, qt):
    h0 = _static_hamiltonian(stage5_input, scenario, 0, qt)
    count = stage5_input.admission.config.reference_state_count
    values, vectors = linalg.eigh(h0.full(), subset_by_index=[0, count - 1], driver="evr")
    if len(values) != count or not np.all(np.isfinite(values)) or np.any(np.diff(values) <= stage5_input.admission.config.tolerances["lab_degeneracy_GHz"]):
        raise ValueError("lab reference eigensystem is insufficient or degenerate")
    vectors = _phase_fixed(vectors)
    catalog = _catalog_vectors(frame["local_vectors"])
    overlaps = np.abs(catalog.conj().T @ vectors) ** 2
    assignments = _decimal_assignment(overlaps)
    projectors = {label: qt.Qobj(np.outer(vectors[:, column], vectors[:, column].conj()), dims=frame["dims"]) for label, column in assignments.items()}
    if any(overlaps[row, assignments[label]] < stage5_input.admission.config.tolerances["label_min_overlap"] for row, label in enumerate(LABELS)):
        raise ValueError("lab reference label overlap is below the frozen threshold")
    psi_lab = qt.Qobj(vectors[:, 0], dims=[frame["dims"][0], [1, 1, 1]])
    edge0 = zoh_edges(scenario.time_center_ns)[0]
    psi_ip = frame["u"](float(edge0)).dag() * psi_lab
    return {"psi_ip": psi_ip, "projectors": projectors, "metadata": {"state": "physical_lab_ground", "t0_ns": float(edge0), "labels": [{"label": label, "lab_eigen_index": int(column), "overlap": float(overlaps[row, column])} for row, (label, column) in enumerate(assignments.items())]}}


def _static_hamiltonian(stage5_input: Stage5Input, scenario: EffectiveScenario, index: int, qt):
    junctions = resolve_effective_junctions(stage5_input.rebuilt_model.device_artifacts, _flux_for_hamiltonian(scenario, index))
    model = build_hamiltonian(stage5_input.rebuilt_model.hamiltonian_config, stage5_input.rebuilt_model.ec_matrix_GHz, junctions)
    return qt.Qobj(model.matrix.toarray(), dims=[list(model.basis.dimensions.values()), list(model.basis.dimensions.values())])


def _flux_for_hamiltonian(scenario: EffectiveScenario, index: int) -> dict[str, float]:
    # Stage 4 is q1,q2,c; Hamiltonian construction is q1,c,q2. The mapping is explicitly named.
    return {"q1": float(scenario.absolute_flux_phi0["q1"][index]), "c": float(scenario.absolute_flux_phi0["c"][index]), "q2": float(scenario.absolute_flux_phi0["q2"][index])}


def _smoke_window(scenario: EffectiveScenario, start_index: int, sample_count: int | None) -> EffectiveScenario:
    if sample_count is None or start_index + sample_count > scenario.time_center_ns.size:
        raise ValueError("smoke_sample_count exceeds reconstructed effective controls")
    active = sum(float(np.max(np.abs(pair[0][start_index:start_index + sample_count]) + np.abs(pair[1][start_index:start_index + sample_count]))) for pair in scenario.xy_iq_GHz.values())
    if active <= 0.0:
        raise ValueError("smoke window must cover non-zero XY I/Q controls")
    end = start_index + sample_count
    return EffectiveScenario(scenario.scenario_id, scenario.time_center_ns[start_index:end], {mode: (pair[0][start_index:end], pair[1][start_index:end]) for mode, pair in scenario.xy_iq_GHz.items()}, {mode: values[start_index:end] for mode, values in scenario.absolute_flux_phi0.items()}, scenario.carrier_frequency_GHz, scenario.carrier_phase_rad)


def _tensor_operator(operators, identities, qt):
    return qt.tensor(*(operators.get(mode, identities[mode]) for mode in MODE_ORDER))


def _expectation(state, operator):
    value = state.dag() * state if operator is None else state.dag() * operator * state
    return value.full()[0, 0] if hasattr(value, "full") else complex(value)


def _identity_energy_gauge(hamiltonian) -> float:
    trace = complex(hamiltonian.tr())
    if not math.isfinite(trace.real) or not math.isfinite(trace.imag) or abs(trace.imag) > 1e-10:
        raise ValueError("Hamiltonian trace is not finite and real for identity gauge")
    return trace.real / hamiltonian.shape[0]


def _gauge_phase_edges(edges: np.ndarray, raw_hamiltonian) -> np.ndarray:
    theta = np.zeros(edges.size, dtype=float)
    for index in range(edges.size - 1):
        midpoint = 0.5 * (edges[index] + edges[index + 1])
        theta[index + 1] = theta[index] + _identity_energy_gauge(raw_hamiltonian(float(midpoint))) * (edges[index + 1] - edges[index])
    return theta


def _phase_fixed(vectors: np.ndarray) -> np.ndarray:
    result = np.array(vectors, dtype=complex, copy=True)
    for column in range(result.shape[1]):
        vector = result[:, column]
        magnitude = np.abs(vector)
        index = int(np.flatnonzero(magnitude == magnitude.max())[0])
        result[:, column] *= np.exp(-1j * np.angle(vector[index]))
        if result[index, column].real < 0:
            result[:, column] *= -1.0
    return result


def _catalog_vectors(local_vectors: Mapping[str, np.ndarray]) -> np.ndarray:
    rows = []
    for label in LABELS:
        q1, c, q2 = (int(value) for value in label)
        rows.append(np.kron(np.kron(local_vectors["q1"][:, q1], local_vectors["c"][:, c]), local_vectors["q2"][:, q2]))
    return np.column_stack(rows)


def _decimal_assignment(overlaps: np.ndarray) -> dict[str, int]:
    best: tuple[Any, tuple[int, ...]] | None = None
    for candidate in permutations(range(overlaps.shape[1]), len(LABELS)):
        cost = sum((-Decimal.from_float(float(overlaps[row, column])) for row, column in enumerate(candidate)), Decimal(0))
        if best is None or cost < best[0] or (cost == best[0] and candidate < best[1]):
            best = (cost, candidate)
    if best is None or len(set(best[1])) != len(LABELS):
        raise ValueError("lab reference assignment failed")
    return dict(zip(LABELS, best[1], strict=True))


def _scenario_checks(populations, leakage, norm_error, tolerances):
    finite = all(np.all(np.isfinite(values)) for values in (*populations.values(), leakage, norm_error))
    bounds = all(np.all((values >= -tolerances["population_bound"]) & (values <= 1.0 + tolerances["population_bound"])) for values in (*populations.values(), leakage))
    return (
        {"name": "finite_observables", "passed": bool(finite)},
        {"name": "population_bounds", "passed": bool(bounds)},
        {"name": "norm_error", "passed": bool(np.max(norm_error) <= tolerances["norm_error"]), "max_error": float(np.max(norm_error)), "threshold": tolerances["norm_error"]},
    )


def _control_digest(scenario: EffectiveScenario) -> dict[str, Any]:
    import hashlib
    from sqvm.hamiltonian.provenance import canonical_json_bytes
    payload = {"time_center_ns": scenario.time_center_ns.tolist(), "xy": {mode: {"i": pair[0].tolist(), "q": pair[1].tolist()} for mode, pair in scenario.xy_iq_GHz.items()}, "flux": {mode: values.tolist() for mode, values in scenario.absolute_flux_phi0.items()}}
    return {"sha256": hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper(), "sample_count": int(scenario.time_center_ns.size)}


def _qutip():
    try:
        import qutip
    except ImportError as exc:
        raise ValueError("QuTiP is required for Stage 5 evolution") from exc
    return qutip


def _validate_qutip_version(version: str) -> None:
    try:
        major, minor = (int(value) for value in version.split(".")[:2])
    except ValueError as exc:
        raise ValueError("QuTiP version is invalid") from exc
    if major != 5 or minor < 1 or minor >= 4:
        raise ValueError("QuTiP version must satisfy >=5.1,<5.4")
