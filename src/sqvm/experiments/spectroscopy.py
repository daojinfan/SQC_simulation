"""Generic single- and two-qubit lockstep spectroscopy over ``run_circuits``."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np

from sqvm.circuits import (
    CircuitExecutionContext,
    CircuitResult,
    QCISCircuit,
    compile_circuit,
    run_circuits,
)
from sqvm.qcis.canonical import canonical_float, sha256_bytes, sha256_json


EXPERIMENT_ID = "qubit_spectroscopy_v1"
BUILDER_ID = "qubit_spectroscopy_circuit_builder_v1"
CAPABILITY_ADAPTER_ID = "qubit_capability_adapter_v1"
BACKEND_OBSERVABLE_SET_ID = "dressed_computational_populations_v1"
EXPERIMENT_OBSERVABLE_SET_ID = "qubit_spectroscopy_observables_v1"
_MAX_POINTS = 64
_COMPONENT_ORDER = {"q1": 0, "q2": 1}
_COMPONENT_PROJECTOR_BIT = {"q1": 0, "q2": 1}


class SpectroscopyMode(StrEnum):
    SINGLE = "single"
    PARALLEL_LOCKSTEP = "parallel_lockstep"


class SpectroscopyReasonCode(StrEnum):
    CAPABILITY_INVALID = "SPECTROSCOPY_CAPABILITY_INVALID"
    REQUEST_INVALID = "SPECTROSCOPY_REQUEST_INVALID"
    TARGET_INVALID = "SPECTROSCOPY_TARGET_INVALID"
    TARGET_ORDER_INVALID = "SPECTROSCOPY_TARGET_ORDER_INVALID"
    AXIS_INVALID = "SPECTROSCOPY_AXIS_INVALID"
    PULSE_POLICY_INVALID = "SPECTROSCOPY_PULSE_POLICY_INVALID"
    POINT_LIMIT_EXCEEDED = "SPECTROSCOPY_POINT_LIMIT_EXCEEDED"
    POINT_ID_COLLISION = "SPECTROSCOPY_POINT_ID_COLLISION"
    RESULT_INVALID = "SPECTROSCOPY_RESULT_INVALID"
    ANALYSIS_INVALID = "SPECTROSCOPY_ANALYSIS_INVALID"


class SpectroscopyError(ValueError):
    def __init__(self, code: SpectroscopyReasonCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else str(code))


@dataclass(frozen=True, slots=True)
class QubitCapability:
    qagent: str
    backend_component_slot: str
    xy_channel: str
    reference_frequency_GHz: float
    reference_frequency_authority_sha256: str
    idle_flux_key: str
    projector_bit_index: int
    canonical_order_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "qagent": self.qagent,
            "backend_component_slot": self.backend_component_slot,
            "xy_channel": self.xy_channel,
            "reference_frequency_GHz": self.reference_frequency_GHz,
            "reference_frequency_authority_sha256": self.reference_frequency_authority_sha256,
            "idle_flux_key": self.idle_flux_key,
            "projector_bit_index": self.projector_bit_index,
            "canonical_order_index": self.canonical_order_index,
        }


@dataclass(frozen=True, slots=True)
class QubitCapabilityAdapter:
    capabilities: Mapping[str, QubitCapability]
    authority_sha256: str

    def resolve(self, qagent: str) -> QubitCapability:
        try:
            return self.capabilities[qagent]
        except KeyError as exc:
            _fail(SpectroscopyReasonCode.TARGET_INVALID, f"unsupported target: {qagent}")
            raise AssertionError from exc


@dataclass(frozen=True, slots=True)
class SpectroscopyAxis:
    qagent: str
    frequencies_GHz: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class SpectroscopyPulsePolicy:
    qagent: str
    length_samples: int
    amplitude_GHz: float
    r_sigma_samples: float
    policy_id: str = "qubit_spectroscopy_pulse_policy_v1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "qagent": self.qagent,
            "wave_index": 1,
            "length_samples": self.length_samples,
            "amplitude_GHz": self.amplitude_GHz,
            "r_sigma_samples": self.r_sigma_samples,
            "phase_rad": 0.0,
            "drag_alpha": 0.0,
        }


@dataclass(frozen=True, slots=True)
class SpectroscopyRequest:
    execution_mode: SpectroscopyMode
    run_phase: str
    targets: tuple[str, ...]
    axes: tuple[SpectroscopyAxis, ...]
    pulse_policies: tuple[SpectroscopyPulsePolicy, ...]
    max_points: int = _MAX_POINTS


@dataclass(frozen=True, slots=True)
class SpectroscopyPoint:
    point_index: int
    coordinates_GHz: Mapping[str, float]
    point_input_sha256: str
    circuit: QCISCircuit

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_index": self.point_index,
            "coordinates_GHz": dict(self.coordinates_GHz),
            "point_input_sha256": self.point_input_sha256,
            "circuit_id": self.circuit.circuit_id,
            "qcis_source": self.circuit.source,
        }


@dataclass(frozen=True, slots=True)
class SpectroscopyPointResult:
    point: SpectroscopyPoint
    target_excited_population: Mapping[str, float]
    primary_observable_id: Mapping[str, str]
    circuit_result: CircuitResult

    def to_dict(self) -> dict[str, Any]:
        primitive = self.circuit_result.dressed_populations.to_dict()
        return {
            "point": self.point.to_dict(),
            "target_excited_population": dict(self.target_excited_population),
            "primary_observable_id": dict(self.primary_observable_id),
            "primitive_dressed_populations": primitive,
            "joint_excited_population": primitive["population_101"],
            "leakage": self.circuit_result.leakage,
            "norm_error": self.circuit_result.norm_error,
            "circuit_receipt_sha256": self.circuit_result.receipt_sha256,
        }


@dataclass(frozen=True, slots=True)
class SpectroscopyDataset:
    request: SpectroscopyRequest
    capability_adapter: QubitCapabilityAdapter
    points: tuple[SpectroscopyPointResult, ...]
    dataset_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return _dataset_payload(self.request, self.capability_adapter, self.points)


@dataclass(frozen=True, slots=True)
class SpectroscopyPeak:
    qagent: str
    valid: bool
    discrete_frequency_GHz: float | None
    estimated_frequency_GHz: float | None
    peak_population: float | None
    contrast: float | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "qagent": self.qagent,
            "valid": self.valid,
            "discrete_frequency_GHz": self.discrete_frequency_GHz,
            "estimated_frequency_GHz": self.estimated_frequency_GHz,
            "peak_population": self.peak_population,
            "contrast": self.contrast,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SpectroscopyAnalysis:
    dataset_sha256: str
    peaks: Mapping[str, SpectroscopyPeak]
    peak_quality_eligible: bool
    recommendation_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_sha256": self.dataset_sha256,
            "peaks": {
                target: peak.to_dict() for target, peak in self.peaks.items()
            },
            "peak_quality_eligible": self.peak_quality_eligible,
            "recommendation_eligible": self.recommendation_eligible,
        }


def build_qubit_capability_adapter(
    context: CircuitExecutionContext,
) -> QubitCapabilityAdapter:
    """Resolve registered qubit names onto the bounded q1/q2 backend slots."""

    registry = context.authorities.get("qagent_registry")
    if not isinstance(registry, Mapping):
        _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, "qagent_registry is missing")
    capabilities: dict[str, QubitCapability] = {}
    occupied_slots: set[str] = set()
    for qagent, raw in registry.items():
        if not isinstance(qagent, str) or not isinstance(raw, Mapping):
            continue
        component = raw.get("component")
        if component not in _COMPONENT_ORDER:
            continue
        xy_channel = raw.get("xy_channel")
        reference = raw.get("reference_frequency_authority")
        if not isinstance(xy_channel, str) or not xy_channel:
            _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} has no XY channel")
        if not isinstance(reference, Mapping):
            _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} has no frequency authority")
        _validate_reference_authority(qagent, reference)
        frequency = reference.get("reference_frequency_GHz")
        if not _is_finite_number(frequency) or float(frequency) <= 0.0:
            _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} frequency authority is invalid")
        if component in occupied_slots:
            _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"duplicate backend slot: {component}")
        occupied_slots.add(component)
        capabilities[qagent] = QubitCapability(
            qagent=qagent,
            backend_component_slot=component,
            xy_channel=xy_channel,
            reference_frequency_GHz=float(frequency),
            reference_frequency_authority_sha256=sha256_json(dict(reference)),
            idle_flux_key=component,
            projector_bit_index=_COMPONENT_PROJECTOR_BIT[component],
            canonical_order_index=_COMPONENT_ORDER[component],
        )
    if set(occupied_slots) != {"q1", "q2"}:
        _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, "bounded backend requires q1 and q2")
    payload = {
        "adapter_id": CAPABILITY_ADAPTER_ID,
        "capabilities": [
            capability.to_dict()
            for capability in sorted(capabilities.values(), key=lambda value: value.canonical_order_index)
        ],
    }
    return QubitCapabilityAdapter(
        MappingProxyType(capabilities),
        sha256_json(payload),
    )


def expand_qubit_spectroscopy_points(
    request: SpectroscopyRequest,
    context: CircuitExecutionContext,
) -> tuple[SpectroscopyPoint, ...]:
    """Expand single or paired axes without creating a Cartesian product."""

    adapter = build_qubit_capability_adapter(context)
    axes, policies = _validate_request(request, adapter)
    point_count = len(axes[request.targets[0]].frequencies_GHz)
    if point_count > request.max_points:
        _fail(
            SpectroscopyReasonCode.POINT_LIMIT_EXCEEDED,
            f"{point_count} points exceed request limit {request.max_points}",
        )
    points: list[SpectroscopyPoint] = []
    circuit_ids: set[str] = set()
    for index in range(point_count):
        coordinates = {
            target: axes[target].frequencies_GHz[index] for target in request.targets
        }
        source = _build_qcis(request.targets, coordinates, policies)
        payload = _point_identity_payload(request, context, adapter, policies, coordinates, source)
        point_hash = sha256_json(payload)
        circuit_id = "sp_" + point_hash.lower()[:32]
        if circuit_id in circuit_ids:
            _fail(SpectroscopyReasonCode.POINT_ID_COLLISION, circuit_id)
        circuit_ids.add(circuit_id)
        point = SpectroscopyPoint(
            index,
            MappingProxyType(coordinates),
            point_hash,
            QCISCircuit(circuit_id, source),
        )
        # All points are compiled before the runner can start the first evolution.
        compile_circuit(point.circuit, context)
        points.append(point)
    return tuple(points)


def run_qubit_spectroscopy(
    request: SpectroscopyRequest,
    context: CircuitExecutionContext,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    timeout_s: float = 180.0,
) -> SpectroscopyDataset:
    """Plan and execute a spectroscopy batch through the public circuit facade."""

    adapter = build_qubit_capability_adapter(context)
    points = expand_qubit_spectroscopy_points(request, context)
    if request.execution_mode == SpectroscopyMode.SINGLE:
        readout = [[request.targets[0]]]
    else:
        readout = [[target] for target in request.targets] + [list(request.targets)]
    circuit_results = run_circuits(
        tuple(point.circuit for point in points),
        context,
        output_root,
        repository_root,
        readout_qubit=readout,
        timeout_s=timeout_s,
        max_circuits=request.max_points,
    )
    if len(circuit_results) != len(points):
        _fail(SpectroscopyReasonCode.RESULT_INVALID, "runner result count mismatch")
    point_results = tuple(
        _build_point_result(request, adapter, point, result)
        for point, result in zip(points, circuit_results, strict=True)
    )
    payload = _dataset_payload(request, adapter, point_results)
    return SpectroscopyDataset(request, adapter, point_results, sha256_json(payload))


def analyze_qubit_spectroscopy(
    dataset: SpectroscopyDataset,
    *,
    min_contrast: float = 0.0,
    near_peak_tolerance: float = 1.0e-12,
    quadratic_refinement: bool = True,
) -> SpectroscopyAnalysis:
    """Return bounded peak candidates without changing any calibration setting."""

    if not _is_finite_number(min_contrast) or float(min_contrast) < 0.0:
        _fail(SpectroscopyReasonCode.ANALYSIS_INVALID, "min_contrast must be finite and nonnegative")
    if not _is_finite_number(near_peak_tolerance) or float(near_peak_tolerance) < 0.0:
        _fail(SpectroscopyReasonCode.ANALYSIS_INVALID, "near_peak_tolerance must be finite and nonnegative")
    if not dataset.points or dataset.dataset_sha256 != sha256_json(dataset.to_dict()):
        _fail(SpectroscopyReasonCode.ANALYSIS_INVALID, "dataset identity mismatch")
    peaks = {
        target: _analyze_target(
            dataset,
            target,
            float(min_contrast),
            float(near_peak_tolerance),
            quadratic_refinement,
        )
        for target in dataset.request.targets
    }
    return SpectroscopyAnalysis(
        dataset.dataset_sha256,
        MappingProxyType(peaks),
        all(peak.valid for peak in peaks.values()),
        False,
    )


def _validate_request(
    request: SpectroscopyRequest,
    adapter: QubitCapabilityAdapter,
) -> tuple[dict[str, SpectroscopyAxis], dict[str, SpectroscopyPulsePolicy]]:
    if not isinstance(request, SpectroscopyRequest):
        _fail(SpectroscopyReasonCode.REQUEST_INVALID, "typed request is required")
    try:
        mode = SpectroscopyMode(request.execution_mode)
    except (TypeError, ValueError) as exc:
        raise SpectroscopyError(SpectroscopyReasonCode.REQUEST_INVALID, "unsupported mode") from exc
    expected_count = 1 if mode == SpectroscopyMode.SINGLE else 2
    if len(request.targets) != expected_count or len(set(request.targets)) != expected_count:
        _fail(SpectroscopyReasonCode.TARGET_INVALID, f"{mode} requires {expected_count} unique target(s)")
    capabilities = [adapter.resolve(target) for target in request.targets]
    if [value.canonical_order_index for value in capabilities] != sorted(
        value.canonical_order_index for value in capabilities
    ):
        _fail(SpectroscopyReasonCode.TARGET_ORDER_INVALID, "targets are not in canonical order")
    if not isinstance(request.run_phase, str) or not request.run_phase.isascii() or not request.run_phase:
        _fail(SpectroscopyReasonCode.REQUEST_INVALID, "run_phase must be a nonempty ASCII string")
    if isinstance(request.max_points, bool) or not isinstance(request.max_points, int) or not 1 <= request.max_points <= _MAX_POINTS:
        _fail(SpectroscopyReasonCode.POINT_LIMIT_EXCEEDED, "max_points must be in [1, 64]")
    if len(request.axes) != expected_count:
        _fail(SpectroscopyReasonCode.AXIS_INVALID, "one axis is required per target")
    axes: dict[str, SpectroscopyAxis] = {}
    for axis in request.axes:
        if not isinstance(axis, SpectroscopyAxis) or axis.qagent not in request.targets or axis.qagent in axes:
            _fail(SpectroscopyReasonCode.AXIS_INVALID, "axis target set is not exact")
        values = axis.frequencies_GHz
        if not isinstance(values, tuple) or not values:
            _fail(SpectroscopyReasonCode.AXIS_INVALID, f"{axis.qagent} axis must be a nonempty tuple")
        if any(not _is_finite_number(value) or float(value) <= 0.0 for value in values):
            _fail(SpectroscopyReasonCode.AXIS_INVALID, f"{axis.qagent} frequencies must be positive and finite")
        if any(float(right) <= float(left) for left, right in zip(values, values[1:])):
            _fail(SpectroscopyReasonCode.AXIS_INVALID, f"{axis.qagent} axis must be strictly increasing")
        axes[axis.qagent] = axis
    if set(axes) != set(request.targets):
        _fail(SpectroscopyReasonCode.AXIS_INVALID, "axis target set is not exact")
    lengths = {len(axis.frequencies_GHz) for axis in axes.values()}
    if len(lengths) != 1:
        _fail(SpectroscopyReasonCode.AXIS_INVALID, "lockstep axes must have equal lengths")
    if len(request.pulse_policies) != expected_count:
        _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "one pulse policy is required per target")
    policies: dict[str, SpectroscopyPulsePolicy] = {}
    for policy in request.pulse_policies:
        if not isinstance(policy, SpectroscopyPulsePolicy) or policy.qagent not in request.targets or policy.qagent in policies:
            _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "pulse policy target set is not exact")
        if policy.policy_id != "qubit_spectroscopy_pulse_policy_v1":
            _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "unsupported pulse policy")
        if isinstance(policy.length_samples, bool) or not isinstance(policy.length_samples, int) or policy.length_samples <= 0:
            _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "length_samples must be positive")
        if not _is_finite_number(policy.amplitude_GHz) or float(policy.amplitude_GHz) == 0.0:
            _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "amplitude_GHz must be finite and nonzero")
        if not _is_finite_number(policy.r_sigma_samples) or float(policy.r_sigma_samples) <= 0.0:
            _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "r_sigma_samples must be positive")
        policies[policy.qagent] = policy
    if set(policies) != set(request.targets):
        _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "pulse policy target set is not exact")
    if mode == SpectroscopyMode.PARALLEL_LOCKSTEP and len(
        {policy.length_samples for policy in policies.values()}
    ) != 1:
        _fail(SpectroscopyReasonCode.PULSE_POLICY_INVALID, "parallel pulses require a shared length")
    return axes, policies


def _build_qcis(
    targets: tuple[str, ...],
    coordinates: Mapping[str, float],
    policies: Mapping[str, SpectroscopyPulsePolicy],
) -> str:
    lines = []
    for target in targets:
        policy = policies[target]
        lines.append(
            " ".join(
                (
                    "PLSXY",
                    target,
                    "1",
                    "0",
                    str(policy.length_samples),
                    canonical_float(policy.amplitude_GHz),
                    canonical_float(coordinates[target]),
                    "0",
                    "0",
                    canonical_float(policy.r_sigma_samples),
                )
            )
        )
    return "\n".join(lines) + "\n"


def _point_identity_payload(
    request: SpectroscopyRequest,
    context: CircuitExecutionContext,
    adapter: QubitCapabilityAdapter,
    policies: Mapping[str, SpectroscopyPulsePolicy],
    coordinates: Mapping[str, float],
    source: str,
) -> dict[str, Any]:
    expected = context.authorities.get("expected_sha256")
    return {
        "schema_version": "0.1",
        "experiment_id": EXPERIMENT_ID,
        "builder_id": BUILDER_ID,
        "capability_adapter_sha256": adapter.authority_sha256,
        "backend_observable_set_id": context.observable_set_id,
        "experiment_observable_set_id": EXPERIMENT_OBSERVABLE_SET_ID,
        "authority_sha256": dict(expected) if isinstance(expected, Mapping) else {},
        "execution_mode": str(request.execution_mode),
        "run_phase": request.run_phase,
        "targets": list(request.targets),
        "coordinates_GHz": dict(coordinates),
        "pulse_policies": [policies[target].to_dict() for target in request.targets],
        "initial_state_id": context.initial_state_id,
        "qcis_sha256": sha256_bytes(source.encode("ascii")),
    }


def _build_point_result(
    request: SpectroscopyRequest,
    adapter: QubitCapabilityAdapter,
    point: SpectroscopyPoint,
    result: CircuitResult,
) -> SpectroscopyPointResult:
    if result.circuit_id != point.circuit.circuit_id:
        _fail(SpectroscopyReasonCode.RESULT_INVALID, "circuit result order or identity mismatch")
    primitive = result.dressed_populations
    responses: dict[str, float] = {}
    observable_ids: dict[str, str] = {}
    for target in request.targets:
        component = adapter.resolve(target).backend_component_slot
        if request.execution_mode == SpectroscopyMode.SINGLE:
            responses[target] = (
                primitive.population_100 if component == "q1" else primitive.population_001
            )
            observable_ids[target] = f"{target}.target_excited_spectators_ground"
        else:
            responses[target] = (
                primitive.population_100 + primitive.population_101
                if component == "q1"
                else primitive.population_001 + primitive.population_101
            )
            observable_ids[target] = f"{target}.target_excited_marginal"
    return SpectroscopyPointResult(
        point,
        MappingProxyType(responses),
        MappingProxyType(observable_ids),
        result,
    )


def _dataset_payload(
    request: SpectroscopyRequest,
    adapter: QubitCapabilityAdapter,
    points: Sequence[SpectroscopyPointResult],
) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "experiment_id": EXPERIMENT_ID,
        "execution_mode": str(request.execution_mode),
        "run_phase": request.run_phase,
        "targets": list(request.targets),
        "capability_adapter_sha256": adapter.authority_sha256,
        "backend_observable_set_id": BACKEND_OBSERVABLE_SET_ID,
        "experiment_observable_set_id": EXPERIMENT_OBSERVABLE_SET_ID,
        "points": [point.to_dict() for point in points],
    }


def _analyze_target(
    dataset: SpectroscopyDataset,
    target: str,
    min_contrast: float,
    near_peak_tolerance: float,
    quadratic_refinement: bool,
) -> SpectroscopyPeak:
    frequencies = np.asarray(
        [point.point.coordinates_GHz[target] for point in dataset.points], dtype="<f8"
    )
    values = np.asarray(
        [point.target_excited_population[target] for point in dataset.points], dtype="<f8"
    )
    if frequencies.size < 3:
        return SpectroscopyPeak(target, False, None, None, None, None, "insufficient_points")
    maximum = float(np.max(values))
    contrast = maximum - float(np.min(values))
    candidates = np.flatnonzero(maximum - values <= near_peak_tolerance)
    if candidates.size != 1:
        return SpectroscopyPeak(target, False, None, None, maximum, contrast, "peak_nonunique")
    index = int(candidates[0])
    discrete = float(frequencies[index])
    if index in {0, frequencies.size - 1}:
        return SpectroscopyPeak(target, False, discrete, None, maximum, contrast, "peak_at_boundary")
    if contrast < min_contrast:
        return SpectroscopyPeak(target, False, discrete, None, maximum, contrast, "contrast_below_minimum")
    estimate = discrete
    if quadratic_refinement:
        estimate = _bounded_quadratic_vertex(
            frequencies[index - 1 : index + 2], values[index - 1 : index + 2], discrete
        )
    return SpectroscopyPeak(target, True, discrete, estimate, maximum, contrast, None)


def _bounded_quadratic_vertex(
    frequencies: np.ndarray,
    values: np.ndarray,
    fallback: float,
) -> float:
    try:
        a, b, _c = np.polyfit(frequencies, values, 2)
    except (TypeError, ValueError, np.linalg.LinAlgError):
        return fallback
    if not math.isfinite(float(a)) or not math.isfinite(float(b)) or a >= 0.0:
        return fallback
    vertex = float(-b / (2.0 * a))
    if not math.isfinite(vertex) or not float(frequencies[0]) <= vertex <= float(frequencies[-1]):
        return fallback
    return vertex


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _validate_reference_authority(qagent: str, reference: Mapping[str, Any]) -> None:
    required = {
        "reference_frequency_GHz",
        "frequency_source",
        "calibration_run_id",
        "revision",
        "setting_hash",
    }
    if set(reference) != required:
        _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} frequency authority fields are not exact")
    if reference["frequency_source"] not in {"bootstrap_seed", "accepted_simulation"}:
        _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} frequency source is invalid")
    if (
        not isinstance(reference["calibration_run_id"], str)
        or not reference["calibration_run_id"]
        or type(reference["revision"]) is not int
        or reference["revision"] <= 0
    ):
        _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} frequency lifecycle is invalid")
    expected_hash = sha256_json(
        {name: value for name, value in reference.items() if name != "setting_hash"}
    )
    if reference["setting_hash"] != expected_hash:
        _fail(SpectroscopyReasonCode.CAPABILITY_INVALID, f"{qagent} frequency authority hash mismatch")


def _fail(code: SpectroscopyReasonCode, detail: str) -> None:
    raise SpectroscopyError(code, detail)
