"""X2P + X2P amplitude Rabi calibration built on Runtime 0.3 batches.

The phase-audit implementation intentionally lives behind ``phase_auditor``.
It receives each precompiled circuit and returns the small serialisable audit
record stored in the dataset; it must return ``passed=True`` for a production
recommendation to be eligible.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any
import uuid

import numpy as np
from scipy.optimize import least_squares

from sqvm.candidate_protocol import calibration_candidate, parameter_change
from sqvm.calibration.rabi_phase import RabiPhaseAuditError, audit_two_x2p_phase
from sqvm.circuits import CircuitExecutionContext, CircuitExecutionProfile, QCISCircuit, compile_circuit
from sqvm.qcis.canonical import canonical_float, canonical_json_bytes, sha256_json
from sqvm.runtime.batch import (
    CircuitBatchError,
    CircuitBatchHandle,
    run_circuit_batch,
    verify_circuit_batch,
)
from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.lifecycle import CancellationToken
from sqvm.runtime.publication import publish_calibration_directory
from sqvm.runtime.storage import write_canonical_new
from sqvm.storage.archive_format import DirectoryEvidenceReader
from sqvm.storage.inventory import inventory_tree
from sqvm.storage.models import ArchiveEntry


RABI_EXPERIMENT_ID = "qubit_rabi_x2p_amplitude_v1"
RABI_SCAN_WORKFLOW_ID = "qubit_rabi_x2p_amplitude_scan_v1"
RABI_POLICY_PATH = "configs/calibration/rabi_x2p_analysis_policy_v1.json"

RABI_ERROR_CODES = frozenset({
    "rabi_request_invalid",
    "rabi_axis_invalid",
    "rabi_target_unsupported",
    "rabi_reference_frequency_unqualified",
    "rabi_xy2_setting_invalid",
    "rabi_set_path_unavailable",
    "rabi_compilation_invalid",
    "rabi_control_preflight_failed",
    "rabi_phase_audit_failed",
    "rabi_result_invalid",
    "rabi_fit_not_converged",
    "rabi_first_peak_not_bracketed",
    "rabi_candidate_ineligible",
    "rabi_publication_failed",
    "rabi_recovery_required",
})


class RabiError(ValueError):
    """Raised for a Rabi request or published Rabi artifact violation."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        if code not in RABI_ERROR_CODES:
            detail = code if detail is None else detail
            code = _rabi_error_code(detail)
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class RabiAmplitudeAxis:
    target: str
    amplitudes_GHz: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class RabiRequest:
    target: str
    axis: RabiAmplitudeAxis
    analysis_policy_id: str = "rabi_x2p_pilot_v1"
    analysis_policy_approved: bool = False
    analysis_policy_sha256: str | None = None
    analysis_policy: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RabiDataset:
    target: str
    amplitudes_GHz: tuple[float, ...]
    p0: tuple[float, ...]
    p1: tuple[float, ...]
    leakage: tuple[float, ...]
    norm_error: tuple[float, ...]
    points: tuple[Mapping[str, Any], ...]
    dataset_sha256: str
    runtime_batch: CircuitBatchHandle

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": RABI_EXPERIMENT_ID,
            "target": self.target,
            "axis": {"name": "amplitude_GHz", "unit": "GHz", "values": list(self.amplitudes_GHz)},
            "series": {self.target: {"P0": list(self.p0), "P1": list(self.p1), "leakage": list(self.leakage), "norm_error": list(self.norm_error)}},
            "points": [dict(point) for point in self.points],
        }


@dataclass(frozen=True, slots=True)
class RabiAnalysis:
    fit_converged: bool
    offset: float | None
    contrast: float | None
    x2p_amplitude_GHz: float | None
    rmse: float | None
    normalized_rmse: float | None
    r_squared: float | None
    first_peak_index: int | None
    peak_bracket_GHz: tuple[float, float] | None
    fitted_P1: tuple[float, ...]
    dense_fit_curve: Mapping[str, tuple[float, ...]] | None = None
    candidate_distance_to_edge_steps: int | None = None
    candidate_leakage: float | None = None
    max_norm_error: float | None = None
    phase_audit_passed: bool | None = None
    reason: str | None = None
    input_dataset_sha256: str | None = None
    optimizer_nfev: int | None = None
    residual_sum_squares: float | None = None
    analysis_policy_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm_version": "rabi_x2p_bounded_least_squares_v1",
            "fit_converged": self.fit_converged, "offset": self.offset,
            "contrast": self.contrast, "x2p_amplitude_GHz": self.x2p_amplitude_GHz,
            "rmse": self.rmse, "normalized_rmse": self.normalized_rmse,
            "r_squared": self.r_squared, "first_peak_index": self.first_peak_index,
            "peak_bracket_GHz": list(self.peak_bracket_GHz) if self.peak_bracket_GHz else None,
            "fitted_P1": list(self.fitted_P1), "reason": self.reason,
            "dense_fit_curve": (
                {"amplitude_GHz": list(self.dense_fit_curve["amplitude_GHz"]), "P1": list(self.dense_fit_curve["P1"])}
                if self.dense_fit_curve is not None else None
            ),
            "candidate_distance_to_edge_steps": self.candidate_distance_to_edge_steps,
            "candidate_leakage": self.candidate_leakage,
            "max_norm_error": self.max_norm_error,
            "phase_audit_passed": self.phase_audit_passed,
            "input_dataset_sha256": self.input_dataset_sha256,
            "optimizer_nfev": self.optimizer_nfev,
            "residual_sum_squares": self.residual_sum_squares,
            "analysis_policy_sha256": self.analysis_policy_sha256,
        }


@dataclass(frozen=True, slots=True)
class RabiRun:
    root: Path
    run_id: str
    recommendation_id: str
    dataset: RabiDataset
    analysis: RabiAnalysis
    recommendation_eligible: bool
    candidates: Mapping[str, Mapping[str, Any]]
    workflow_sha256: str
    receipt_sha256: str

    @property
    def data(self) -> Mapping[str, Mapping[str, tuple[float, ...]]]:
        return MappingProxyType({self.dataset.target: MappingProxyType({
            "amplitude_GHz": self.dataset.amplitudes_GHz, "P0": self.dataset.p0,
            "P1": self.dataset.p1, "leakage": self.dataset.leakage,
            "norm_error": self.dataset.norm_error, "P1_fit": self.analysis.fitted_P1,
        })})


PhaseAuditor = Callable[[int, Any], Mapping[str, Any]]


def amplitude_axis(amplitude_range_GHz: Sequence[float], amplitude_step_GHz: float, target: str) -> RabiAmplitudeAxis:
    """Construct the inclusive, Decimal-defined first-lobe amplitude axis."""
    if not isinstance(target, str) or not target:
        raise RabiError("target must be a nonempty string")
    if isinstance(amplitude_range_GHz, (str, bytes)) or not isinstance(amplitude_range_GHz, Sequence) or len(amplitude_range_GHz) != 2:
        raise RabiError("amplitude_range_GHz must be a (start, stop) pair")
    start, stop = (_decimal(item, "amplitude range") for item in amplitude_range_GHz)
    step = _decimal(amplitude_step_GHz, "amplitude_step_GHz")
    if start != Decimal("0"):
        raise RabiError("first-lobe amplitude_range_GHz start must be 0")
    if stop <= 0 or step <= 0:
        raise RabiError("amplitude range stop and step must be positive")
    intervals = stop / step
    if intervals != intervals.to_integral_value():
        raise RabiError("amplitude range is not exactly divisible by amplitude_step_GHz")
    count = int(intervals) + 1
    if not 3 <= count <= 64:
        raise RabiError("amplitude range must produce between 3 and 64 points")
    values = tuple(float(step * index) for index in range(count))
    if any(not left < right for left, right in zip(values, values[1:])):
        raise RabiError("amplitude axis must be strictly increasing")
    return RabiAmplitudeAxis(target, values)


def build_rabi_circuits(request: RabiRequest) -> tuple[QCISCircuit, ...]:
    _validate_request(request)
    target = request.target
    return tuple(QCISCircuit(
        f"rabi_{target.lower()}_{index:04d}",
        f"SET {target} setting.active_xy2_setting.amplitude_GHz {canonical_float(amplitude)}\nX2P {target}\nX2P {target}\n",
    ) for index, amplitude in enumerate(request.axis.amplitudes_GHz))


def run_qubit_rabi_scan(
    request: RabiRequest, context: CircuitExecutionContext, parent_configuration_path: str | Path,
    output_root: str | Path, repository_root: str | Path | None = None, *, timeout_s: float = 600.0,
    batch_deadline_s: float = 3600.0, operation_id: str | None = None,
    execution_profile: CircuitExecutionProfile = CircuitExecutionProfile.CALIBRATION_SCAN,
    cancellation_token: CancellationToken | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    phase_auditor: PhaseAuditor | None = None,
) -> RabiRun:
    """Run, resume, or replay one immutable X2P amplitude scan."""
    root = _root(repository_root)
    request = _bind_policy(request, root)
    _validate_request(request)
    target = _inside(output_root, root, "rabi output")
    parent = _inside(parent_configuration_path, root, "parent configuration")
    if not parent.is_file():
        raise RabiError("parent configuration does not exist")
    run_id = _operation_id(operation_id)
    parent_sha = _raw_sha(parent)
    if target.exists():
        return _open_published(target, request, context, parent_sha, root)
    staging = target.parent / f".rabi_{run_id.replace('-', '')}"
    staging.mkdir(parents=True, exist_ok=True)
    if (staging / "workflow.json").exists():
        # A crash may leave terminal-looking but incomplete evidence.  Never
        # reserve the immutable target until its complete request-bound
        # publication verifies locally.
        _verify_staging_for_publish(staging, request, context, parent_sha, root)
        publish_calibration_directory(staging, target)
        return _open_published(target, request, context, parent_sha, root)
    _validate_context_binding(request, context)
    circuits = build_rabi_circuits(request)
    # All static Rabi checks complete before Runtime is allowed to start any
    # point. Stage 4.1 validates the effective global schedule generically.
    # It is part of batch metadata, so it must remain stable across a resumed
    # operation even when no completed workflow has been published yet.
    recommendation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{RABI_SCAN_WORKFLOW_ID}:{run_id}"))
    audits = _static_phase_audits(circuits, context, request.axis.amplitudes_GHz)
    if phase_auditor is not None:
        supplied = _phase_audits(circuits, context, phase_auditor)
        if any(audit.get("passed") is False for audit in supplied):
            raise RabiError("rabi_phase_audit_failed")
    try:
        batch = run_circuit_batch(
            circuits, context, staging / "execution", root, batch_id=run_id,
            experiment_request=_request_payload(request, context, parent_sha),
            metadata={"run_id": run_id, "recommendation_id": recommendation_id, "created_utc": utc_now_text(), "parent_configuration_sha256": parent_sha},
            coordinator_root=target.parent / ".runtime-v03", readout_qubit=((request.target,),),
            point_timeout_s=timeout_s, batch_deadline_s=batch_deadline_s,
            execution_profile=execution_profile, cancellation_token=cancellation_token,
            progress_callback=progress_callback,
        )
    except CircuitBatchError as exc:
        if exc.code != "circuit_control_preflight_failed":
            raise
        try:
            staging.rmdir()
        except OSError:
            pass
        raise RabiError("rabi_control_preflight_failed", exc.detail) from exc
    created_utc = batch.metadata.get("created_utc")
    if not isinstance(created_utc, str) or not created_utc:
        raise RabiError(
            "rabi_recovery_required", "runtime batch creation time is invalid"
        )
    dataset = _dataset(request, batch, audits, context)
    dataset_sha = write_canonical_new(staging / "dataset.json", dataset.to_dict())
    dataset = replace(dataset, dataset_sha256=dataset_sha)
    analysis = analyze_rabi(dataset, policy=request.analysis_policy)
    setting_id, setting = _active_setting(request.target, context)
    phase_ok = bool(audits) and all(audit.get("event_count") == 2 for audit in audits)
    analysis = replace(
        analysis,
        phase_audit_passed=phase_ok,
        analysis_policy_sha256=request.analysis_policy_sha256,
    )
    eligible = bool(request.analysis_policy_approved and phase_ok and analysis.fit_converged and _accepted_frequency(request.target, context) and _policy_gates(request, dataset, analysis))
    candidates = _candidates(request, analysis, dataset_sha, setting_id, setting, eligible)
    workflow = {
        "schema_version": "0.1", "artifact_type": "qubit_rabi_x2p_amplitude_scan", "artifact_version": "0.1",
        "workflow_id": RABI_SCAN_WORKFLOW_ID, "run_id": run_id, "recommendation_id": recommendation_id,
        "created_utc": created_utc,
        "status": "completed", "parent_configuration": {"path": parent.relative_to(root).as_posix(), "sha256": parent_sha, "snapshot_id": context.platform_snapshot_id},
        "request": _request_payload(request, context, parent_sha),
        "dataset": {"path": "dataset.json", "sha256": dataset_sha}, "runtime_batch": _runtime_batch_payload(batch),
        "analysis": analysis.to_dict(), "phase_audit": {"passed": phase_ok, "implementation": "rabi_x2p_phase_v1"},
        "recommendation_eligible": eligible, "candidates": candidates, "archive_eligible": True,
        "claim": {"evidence_class": "model_calibration_simulation", "physics_claim": "model_derived_only", "hardware_measurement": False, "execution_profile": execution_profile.value, "recommendation_eligible": eligible},
    }
    workflow_sha = write_canonical_new(staging / "workflow.json", workflow)
    receipt = {
        "artifact_type": "rabi_scan_receipt", "status": "completed",
        "run_id": run_id, "workflow_sha256": workflow_sha,
        "dataset_sha256": dataset_sha, "recommendation_id": recommendation_id,
    }
    receipt_sha = write_canonical_new(staging / "receipt.json", receipt)
    write_canonical_new(staging / "verification_report.json", {
        "ok": True, "status": "completed", "run_id": run_id,
        "workflow_sha256": workflow_sha, "dataset_sha256": dataset_sha,
    })
    write_canonical_new(staging / "manifest.json", {"artifact_type": "rabi_scan_manifest", "workflow_sha256": workflow_sha, "dataset_sha256": dataset_sha, "receipt_sha256": receipt_sha})
    _verify_staging_for_publish(staging, request, context, parent_sha, root)
    publish_calibration_directory(staging, target)
    candidate_by_target = (
        MappingProxyType({request.target: MappingProxyType(candidates[0])})
        if candidates
        else MappingProxyType({})
    )
    return RabiRun(target, run_id, recommendation_id, _relocate_dataset(dataset, staging, target), analysis, eligible, candidate_by_target, workflow_sha, receipt_sha)


def analyze_rabi(dataset: RabiDataset, *, policy: Mapping[str, Any] | None = None) -> RabiAnalysis:
    """Fit the fixed-phase two-X2P first-lobe model deterministically."""
    x, y = np.asarray(dataset.amplitudes_GHz, dtype=float), np.asarray(dataset.p1, dtype=float)
    peaks = [index for index in range(1, len(y) - 1) if y[index] > y[index - 1] and y[index] >= y[index + 1]]
    if not peaks:
        return RabiAnalysis(False, None, None, None, None, None, None, None, None, (), None, None, None, max(dataset.norm_error), None, "first_peak_not_found", dataset.dataset_sha256)
    index = peaks[0]
    bracket = (float(x[index - 1]), float(x[index + 1]))
    bounds = _fit_bounds(policy, bracket)
    if bounds is None:
        return RabiAnalysis(False, None, None, None, None, None, None, index, bracket, (), None, None, None, max(dataset.norm_error), None, "fit_bounds_do_not_intersect_peak_bracket", dataset.dataset_sha256)
    initial = _quadratic_peak(x[index - 1:index + 2], y[index - 1:index + 2], float(x[index]))
    def residual(values: np.ndarray) -> np.ndarray:
        return values[0] + values[1] * np.sin(np.pi * x / (2.0 * values[2])) ** 2 - y
    try:
        lower, upper = bounds
        initial_values = np.clip((float(y[0]), max(float(y[index] - y[0]), 1e-12), initial), lower, upper)
        outcome = least_squares(residual, initial_values, bounds=(lower, upper), method="trf", ftol=1e-12, xtol=1e-12, gtol=1e-12, max_nfev=200)
    except (ValueError, np.linalg.LinAlgError):
        return RabiAnalysis(False, None, None, None, None, None, None, index, bracket, (), None, None, None, max(dataset.norm_error), None, "fit_failed", dataset.dataset_sha256)
    fitted = outcome.x[0] + outcome.x[1] * np.sin(np.pi * x / (2.0 * outcome.x[2])) ** 2
    rmse = float(np.sqrt(np.mean((fitted - y) ** 2)))
    scale = max(float(np.ptp(y)), 1e-12)
    ss_total = float(np.sum((y - np.mean(y)) ** 2))
    dense_x = np.linspace(float(x[0]), float(x[-1]), 201, dtype=float)
    dense_y = outcome.x[0] + outcome.x[1] * np.sin(np.pi * dense_x / (2.0 * outcome.x[2])) ** 2
    curve = MappingProxyType({"amplitude_GHz": tuple(float(value) for value in dense_x), "P1": tuple(float(value) for value in dense_y)})
    candidate_index = min(range(len(x)), key=lambda item: abs(float(x[item]) - float(outcome.x[2])))
    residual_sum_squares = float(np.sum((fitted - y) ** 2))
    return RabiAnalysis(bool(outcome.success), float(outcome.x[0]), float(outcome.x[1]), float(outcome.x[2]), rmse, rmse / scale, 1.0 - residual_sum_squares / ss_total if ss_total else 1.0, index, bracket, tuple(float(value) for value in fitted), curve, min(candidate_index, len(x) - candidate_index - 1), float(dataset.leakage[candidate_index]), max(dataset.norm_error), None, None if outcome.success else "fit_not_converged", dataset.dataset_sha256, int(outcome.nfev), residual_sum_squares)


def verify_rabi_scan(run_root: str | Path) -> bool:
    root = Path(run_root)
    try:
        _verify_rabi_evidence_root(root)
        workflow = _load(root / "workflow.json")
        verify_circuit_batch(root / "execution", expected_batch_id=workflow["run_id"])
    except Exception as exc:
        if isinstance(exc, RabiError):
            raise
        raise RabiError("rabi scan evidence is invalid") from exc
    return True


def verify_rabi_evidence_tree(run_root: str | Path) -> bool:
    """Verify the immutable Rabi envelope without replaying Stage 4.1/5.1.

    This is the storage/catalog verifier: the bounded reader checks every
    declared file hash plus Runtime receipts and Rabi semantics.  Explicit
    user verification remains ``verify_rabi_scan`` and additionally invokes
    the Runtime verifier.
    """
    _verify_rabi_evidence_root(Path(run_root))
    return True


def _runtime_batch_payload(batch: CircuitBatchHandle) -> dict[str, Any]:
    """Persist stable batch identity only; evidence paths are relative elsewhere."""
    return {
        "batch_id": batch.batch_id, "request_sha256": batch.request_sha256,
        "head_sha256": batch.head_sha256, "status": batch.status,
        "attempt_count": batch.attempt_count, "reused_point_count": batch.reused_point_count,
        "point_count": len(batch.results),
    }


def _verify_rabi_evidence_root(root: Path) -> None:
    from sqvm.calibration.rabi_reader import verify_qubit_rabi_scan_evidence
    try:
        inventory = inventory_tree(root, confinement_root=root)
        entries = tuple(ArchiveEntry(row.relative_path, row.logical_bytes, _raw_sha(root / row.relative_path)) for row in inventory.files)
        verify_qubit_rabi_scan_evidence(DirectoryEvidenceReader(root, entries))
    except Exception as exc:
        raise RabiError("rabi evidence reader rejected artifact") from exc


def _verify_staging_for_publish(staging: Path, request: RabiRequest, context: CircuitExecutionContext, parent_sha: str, root: Path) -> None:
    _verify_rabi_evidence_root(staging)
    workflow = _load(staging / "workflow.json")
    if workflow.get("run_id") != _operation_id(workflow.get("run_id")) or staging.name != f".rabi_{workflow['run_id'].replace('-', '')}" or workflow.get("request") != _request_payload(request, context, parent_sha):
        raise RabiError("rabi idempotency conflict")
    verify_circuit_batch(staging / "execution", expected_batch_id=workflow["run_id"])


def _dataset(request: RabiRequest, batch: CircuitBatchHandle, audits: Sequence[Mapping[str, Any]], context: CircuitExecutionContext) -> RabiDataset:
    if len(batch.results) != len(request.axis.amplitudes_GHz):
        raise RabiError("runtime result count differs from rabi axis")
    p0: list[float] = []; p1: list[float] = []; leakage: list[float] = []; norm_error: list[float] = []; points = []
    for index, (result, audit) in enumerate(zip(batch.results, audits)):
        dressed = result.dressed_populations
        excited = _target_excited_population(request.target, context, dressed)
        p1.append(float(excited)); p0.append(float(dressed.computational_population - excited)); leakage.append(float(result.leakage)); norm_error.append(float(result.norm_error))
        points.append({"point_index": index, "circuit_id": result.circuit_id, "circuit_sha256": result.circuit_sha256, "overlay_sha256": result.overlay_sha256, "phase_audit": dict(audit)})
    dataset = RabiDataset(
        request.target, request.axis.amplitudes_GHz, tuple(p0), tuple(p1),
        tuple(leakage), tuple(norm_error),
        tuple(MappingProxyType(row) for row in points), "", batch,
    )
    # This preliminary identity is used before publication.  The persisted
    # storage hash replaces it once the immutable dataset file is written.
    return RabiDataset(
        dataset.target, dataset.amplitudes_GHz, dataset.p0, dataset.p1,
        dataset.leakage, dataset.norm_error, dataset.points,
        sha256_json(dataset.to_dict()), batch,
    )


def _target_excited_population(target: str, context: CircuitExecutionContext, dressed: Any) -> float:
    """Project a target through its capability component, never its display name."""
    try:
        component = context.authorities["qagent_registry"][target]["component"]
    except (KeyError, TypeError) as exc:
        raise RabiError("target has no QAgent component capability") from exc
    if component == "q1":
        return float(dressed.population_100 + dressed.population_101)
    if component == "q2":
        return float(dressed.population_001 + dressed.population_101)
    raise RabiError("target component is not supported by the readout population basis")


def _phase_audits(circuits: Sequence[QCISCircuit], context: CircuitExecutionContext, auditor: PhaseAuditor | None) -> tuple[Mapping[str, Any], ...]:
    if auditor is None:
        return tuple(MappingProxyType({"passed": None, "status": "pending_external_phase_audit"}) for _ in circuits)
    audits = []
    for index, circuit in enumerate(circuits):
        audit = dict(auditor(index, compile_circuit(circuit, context)))
        if type(audit.get("passed")) is not bool:
            raise RabiError("rabi phase audit result must declare passed")
        audits.append(MappingProxyType(audit))
    return tuple(audits)


def _static_phase_audits(circuits: Sequence[QCISCircuit], context: CircuitExecutionContext, amplitudes: Sequence[float]) -> tuple[Mapping[str, Any], ...]:
    """Audit every precompiled Rabi point before Runtime may start a worker.

    Stage 4.1's effective global schedule remains validated by its generic
    verifier during control publication; this static audit deliberately does
    not invent an electronics receipt before that artifact exists.
    """
    audits = []
    for circuit, amplitude in zip(circuits, amplitudes, strict=True):
        try:
            compilation = compile_circuit(circuit, context).compilation
        except Exception as exc:
            raise RabiError("rabi_compilation_invalid", str(exc)) from exc
        try:
            source_operations = {int(step["index"]): str(step["op"]) for step in compilation.plan.trace["steps"]}
            audit = audit_two_x2p_phase(
                compilation.plan.drive_event_inventory,
                amplitude_GHz=float(amplitude), dt_ns=float(compilation.plan.dt_ns),
                logical_sample_count=int(compilation.q1_xy.size), electronics_schedule=None,
                source_operations=source_operations, require_electronics_schedule=False,
            )
            audits.append(MappingProxyType({"passed": True, **audit.to_dict()}))
        except (RabiPhaseAuditError, KeyError, TypeError, IndexError, ValueError) as exc:
            raise RabiError("rabi_phase_audit_failed") from exc
    return tuple(audits)


def _candidates(request: RabiRequest, analysis: RabiAnalysis, dataset_sha: str, setting_id: str, setting: Mapping[str, Any], eligible: bool) -> list[dict[str, Any]]:
    proposed = analysis.x2p_amplitude_GHz
    if proposed is None:
        return []
    path = f"calibration_values.waveform_registry.settings.{setting_id}.amplitude_GHz"
    return [calibration_candidate(f"{request.target}:xy2_amplitude:{dataset_sha[:16]}", request.target, [parameter_change(path, setting["amplitude_GHz"], proposed, unit="GHz", configuration_resource={"owner": request.target, "resource_type": "waveform_setting", "resource_id": setting_id})], recommendation_eligible=eligible, candidate_type="xy2_amplitude", source_dataset_sha256s=[dataset_sha], quality_metrics=analysis.to_dict(), reason=None if eligible else "Rabi recommendation requires an approved policy, accepted frequency, converged fit, and phase audit")]


def _validate_context_binding(request: RabiRequest, context: CircuitExecutionContext) -> None:
    setting_id, setting = _active_setting(request.target, context)
    if f"{request.target}.setting.active_xy2_setting.amplitude_GHz" not in context.settable_paths:
        raise RabiError("active XY2 amplitude SET path is not allowed")
    if setting.get("target") != request.target or setting.get("status") != "accepted" or setting.get("gate_type") not in {None, "XY2"} or setting.get("transition") not in {None, "01"}:
        raise RabiError("active XY2 setting is not an accepted target XY2 setting")
    if not _positive(setting.get("length_samples")) or not _positive(setting.get("amplitude_GHz")):
        raise RabiError("active XY2 setting has invalid length or amplitude")
    if not _accepted_frequency(request.target, context):
        # A bootstrap source may execute an explicitly marked development fixture, but never a candidate.
        return


def _active_setting(target: str, context: CircuitExecutionContext) -> tuple[str, Mapping[str, Any]]:
    try:
        setting_id = context.authorities["gate_configuration"][target]["active_xy2_setting"]
        setting = context.authorities["waveform_registry"]["settings"][setting_id]
    except (KeyError, TypeError) as exc:
        raise RabiError("active XY2 setting cannot be resolved") from exc
    if not isinstance(setting_id, str) or not isinstance(setting, Mapping):
        raise RabiError("active XY2 setting is invalid")
    return setting_id, setting


def _accepted_frequency(target: str, context: CircuitExecutionContext) -> bool:
    try:
        authority = context.authorities["qagent_registry"][target]["reference_frequency_authority"]
    except (KeyError, TypeError):
        return False
    return isinstance(authority, Mapping) and authority.get("frequency_source") == "accepted_simulation" and _positive(authority.get("reference_frequency_GHz"))


def _request_payload(request: RabiRequest, context: CircuitExecutionContext, parent_sha: str) -> dict[str, Any]:
    setting_id, setting = _active_setting(request.target, context)
    return {"experiment_id": RABI_EXPERIMENT_ID, "workflow_id": RABI_SCAN_WORKFLOW_ID, "target": request.target, "axis": {"name": "amplitude_GHz", "unit": "GHz", "values": list(request.axis.amplitudes_GHz)}, "gate_sequence": ["X2P", "X2P"], "setting_selector": "active_xy2_setting", "setting_id": setting_id, "setting_hash": setting.get("setting_hash"), "setting_amplitude_GHz": setting.get("amplitude_GHz"), "parent_configuration_sha256": parent_sha, "analysis_policy_id": request.analysis_policy_id, "analysis_policy_approved": request.analysis_policy_approved, "analysis_policy_sha256": request.analysis_policy_sha256}


def _open_published(target: Path, request: RabiRequest, context: CircuitExecutionContext, parent_sha: str, root: Path) -> RabiRun:
    verify_rabi_scan(target)
    workflow, payload = _load(target / "workflow.json"), _load(target / "dataset.json")
    if workflow.get("request") != _request_payload(request, context, parent_sha):
        raise RabiError("rabi idempotency conflict")
    target_name = request.target; series = payload["series"][target_name]
    batch = CircuitBatchHandle(workflow["run_id"], target / "execution", "", "", "completed", 0, len(payload["points"]), (), {})
    dataset = RabiDataset(target_name, tuple(payload["axis"]["values"]), tuple(series["P0"]), tuple(series["P1"]), tuple(series["leakage"]), tuple(series["norm_error"]), tuple(MappingProxyType(row) for row in payload["points"]), workflow["dataset"]["sha256"], batch)
    analysis = _analysis_from_payload(workflow["analysis"])
    candidates = workflow.get("candidates", [])
    return RabiRun(target, workflow["run_id"], workflow["recommendation_id"], dataset, analysis, bool(workflow.get("recommendation_eligible")), MappingProxyType({target_name: MappingProxyType(candidates[0])}) if candidates else MappingProxyType({}), _raw_sha(target / "workflow.json"), _raw_sha(target / "receipt.json"))


def _analysis_from_payload(row: Mapping[str, Any]) -> RabiAnalysis:
    bracket = row.get("peak_bracket_GHz")
    dense = row.get("dense_fit_curve")
    curve = MappingProxyType({"amplitude_GHz": tuple(dense["amplitude_GHz"]), "P1": tuple(dense["P1"])}) if isinstance(dense, Mapping) else None
    return RabiAnalysis(bool(row["fit_converged"]), row.get("offset"), row.get("contrast"), row.get("x2p_amplitude_GHz"), row.get("rmse"), row.get("normalized_rmse"), row.get("r_squared"), row.get("first_peak_index"), tuple(bracket) if bracket else None, tuple(row.get("fitted_P1", ())), curve, row.get("candidate_distance_to_edge_steps"), row.get("candidate_leakage"), row.get("max_norm_error"), row.get("phase_audit_passed"), row.get("reason"), row.get("input_dataset_sha256"), row.get("optimizer_nfev"), row.get("residual_sum_squares"), row.get("analysis_policy_sha256"))


def _relocate_dataset(dataset: RabiDataset, source: Path, destination: Path) -> RabiDataset:
    source_execution = source / "execution"
    target_execution = destination / "execution"
    def relocated(path: Path) -> Path:
        try:
            return target_execution / path.relative_to(source_execution)
        except ValueError:
            return path
    results = tuple(replace(result, evidence_root=relocated(result.evidence_root), model_evidence_root=relocated(result.model_evidence_root)) for result in dataset.runtime_batch.results)
    batch = replace(dataset.runtime_batch, root=target_execution, results=results)
    return replace(dataset, runtime_batch=batch)


def _quadratic_peak(x: np.ndarray, y: np.ndarray, fallback: float) -> float:
    try:
        a, b, _ = np.polyfit(x, y, 2); value = float(-b / (2 * a))
        return value if math.isfinite(value) and float(x[0]) <= value <= float(x[-1]) else fallback
    except (ValueError, np.linalg.LinAlgError, ZeroDivisionError):
        return fallback


def _fit_bounds(
    policy: Mapping[str, Any] | None,
    bracket: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Use approved authority bounds; diagnostics use explicit safe bounds."""
    if isinstance(policy, Mapping) and policy.get("approved") is True:
        source = policy["fit_parameter_bounds"]
        offset, contrast, amplitude = (source[name] for name in ("offset", "contrast", "x2p_amplitude_GHz"))
    else:
        offset, contrast, amplitude = (-1.0, 1.0), (0.0, 1.0), bracket
    amplitude_lower = max(float(amplitude[0]), bracket[0])
    amplitude_upper = min(float(amplitude[1]), bracket[1])
    if amplitude_lower >= amplitude_upper:
        return None
    return (
        np.asarray((float(offset[0]), float(contrast[0]), amplitude_lower)),
        np.asarray((float(offset[1]), float(contrast[1]), amplitude_upper)),
    )


def _validate_request(request: RabiRequest) -> None:
    if not isinstance(request, RabiRequest) or request.target != request.axis.target or not request.axis.amplitudes_GHz:
        raise RabiError("rabi request is invalid")
    values = request.axis.amplitudes_GHz
    if len(values) > 64 or values[0] != 0.0 or any(not _finite(value) for value in values) or any(left >= right for left, right in zip(values, values[1:])):
        raise RabiError("rabi amplitude axis is invalid")


def load_rabi_analysis_policy(repository_root: str | Path | None = None) -> Mapping[str, Any]:
    """Load the versioned analysis authority; pilot policies disable all gates."""
    root = _root(repository_root)
    policy = _load(root / RABI_POLICY_PATH)
    _validate_policy_mapping(policy)
    return MappingProxyType(policy)


def _bind_policy(request: RabiRequest, root: Path) -> RabiRequest:
    if request.analysis_policy is None:
        policy = load_rabi_analysis_policy(root)
        digest = _raw_sha(root / RABI_POLICY_PATH)
    else:
        # Keep test/private callers subject to the same schema as file-backed policy.
        policy = MappingProxyType(dict(request.analysis_policy))
        digest = sha256_json(dict(policy))
        _validate_policy_mapping(policy)
    if request.analysis_policy_sha256 is not None and request.analysis_policy_sha256 != digest:
        raise RabiError("rabi analysis policy hash differs")
    return replace(request, analysis_policy_id=policy["policy_id"], analysis_policy_approved=policy["approved"], analysis_policy_sha256=digest, analysis_policy=policy)


def _validate_policy_mapping(policy: Mapping[str, Any]) -> None:
    thresholds = ("minimum_point_count", "minimum_points_before_peak", "minimum_points_after_peak", "minimum_contrast", "minimum_r_squared", "maximum_normalized_rmse", "maximum_candidate_leakage", "maximum_norm_error", "minimum_edge_guard_steps")
    required = {"schema_version", "policy_id", "approved", "fit_parameter_bounds", *thresholds}
    if set(policy) != required or policy.get("schema_version") != "0.1" or not isinstance(policy.get("policy_id"), str) or not policy["policy_id"].strip() or type(policy.get("approved")) is not bool:
        raise RabiError("rabi analysis policy is invalid")
    if not policy["approved"]:
        if any(policy[name] is not None for name in thresholds) or policy["fit_parameter_bounds"] is not None:
            raise RabiError("unapproved rabi analysis policy must disable thresholds")
        return
    integer_names = ("minimum_point_count", "minimum_points_before_peak", "minimum_points_after_peak", "minimum_edge_guard_steps")
    if any(type(policy[name]) is not int or policy[name] < 0 for name in integer_names) or policy["minimum_point_count"] < 3:
        raise RabiError("approved rabi analysis policy integer thresholds are invalid")
    unit_names = ("minimum_contrast", "minimum_r_squared", "maximum_candidate_leakage")
    if any(not _finite(policy[name]) or not 0.0 <= float(policy[name]) <= 1.0 for name in unit_names) or not _finite(policy["maximum_normalized_rmse"]) or float(policy["maximum_normalized_rmse"]) < 0.0 or not _finite(policy["maximum_norm_error"]) or float(policy["maximum_norm_error"]) < 0.0:
        raise RabiError("approved rabi analysis policy thresholds are invalid")
    bounds = policy["fit_parameter_bounds"]
    if not isinstance(bounds, Mapping) or set(bounds) != {"offset", "contrast", "x2p_amplitude_GHz"}:
        raise RabiError("approved rabi analysis policy fit bounds are invalid")
    for name, pair in bounds.items():
        if not isinstance(pair, list) or len(pair) != 2 or not all(_finite(value) for value in pair) or pair[0] >= pair[1] or (name in {"offset", "contrast"} and not 0.0 <= pair[0] < pair[1] <= 1.0) or (name == "x2p_amplitude_GHz" and pair[0] < 0.0):
            raise RabiError("approved rabi analysis policy fit bounds are invalid")


def _policy_gates(request: RabiRequest, dataset: RabiDataset, analysis: RabiAnalysis) -> bool:
    policy = request.analysis_policy
    if not isinstance(policy, Mapping) or not policy.get("approved") or analysis.first_peak_index is None or analysis.x2p_amplitude_GHz is None or analysis.offset is None or analysis.contrast is None or analysis.r_squared is None or analysis.normalized_rmse is None:
        return False
    peak = analysis.first_peak_index
    bounds = policy["fit_parameter_bounds"]
    return bool(
        len(dataset.amplitudes_GHz) >= int(policy["minimum_point_count"])
        and peak >= int(policy["minimum_points_before_peak"])
        and len(dataset.amplitudes_GHz) - peak - 1 >= int(policy["minimum_points_after_peak"])
        and analysis.contrast >= float(policy["minimum_contrast"])
        and analysis.r_squared >= float(policy["minimum_r_squared"])
        and analysis.normalized_rmse <= float(policy["maximum_normalized_rmse"])
        and analysis.candidate_leakage is not None and analysis.candidate_leakage <= float(policy["maximum_candidate_leakage"])
        and analysis.max_norm_error is not None and analysis.max_norm_error <= float(policy["maximum_norm_error"])
        and analysis.candidate_distance_to_edge_steps is not None and analysis.candidate_distance_to_edge_steps >= int(policy["minimum_edge_guard_steps"])
        and float(bounds["offset"][0]) <= analysis.offset <= float(bounds["offset"][1])
        and float(bounds["contrast"][0]) <= analysis.contrast <= float(bounds["contrast"][1])
        and float(bounds["x2p_amplitude_GHz"][0]) <= analysis.x2p_amplitude_GHz <= float(bounds["x2p_amplitude_GHz"][1])
    )


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool): raise RabiError(f"{label} must be finite")
    try: result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc: raise RabiError(f"{label} must be finite") from exc
    if not result.is_finite(): raise RabiError(f"{label} must be finite")
    return result


def _positive(value: Any) -> bool:
    return _finite(value) and float(value) > 0


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _root(value: str | Path | None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]


def _inside(value: str | Path, root: Path, label: str) -> Path:
    path = Path(value); path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try: path.relative_to(root)
    except ValueError as exc: raise RabiError(f"{label} is outside repository") from exc
    return path


def _operation_id(value: str | None) -> str:
    raw = value or str(uuid.uuid4())
    try: parsed = uuid.UUID(raw)
    except (ValueError, AttributeError) as exc: raise RabiError("operation_id must be a canonical UUID4") from exc
    if parsed.version != 4 or str(parsed) != raw: raise RabiError("operation_id must be a canonical UUID4")
    return raw


def _raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict): raise RabiError("rabi artifact must be an object")
    return value


def _rabi_error_code(detail: str) -> str:
    """Classify legacy call sites while preserving a stable public code."""
    lowered = detail.lower()
    if "axis" in lowered or "range" in lowered or "step" in lowered or "first-lobe" in lowered:
        return "rabi_axis_invalid"
    if "target" in lowered or "qagent component" in lowered or "population basis" in lowered:
        return "rabi_target_unsupported"
    if "reference frequency" in lowered:
        return "rabi_reference_frequency_unqualified"
    if "set path" in lowered or "settable" in lowered:
        return "rabi_set_path_unavailable"
    if "active xy2" in lowered or "setting" in lowered:
        return "rabi_xy2_setting_invalid"
    if "compil" in lowered:
        return "rabi_compilation_invalid"
    if "phase" in lowered:
        return "rabi_phase_audit_failed"
    if "first_peak" in lowered or "first peak" in lowered:
        return "rabi_first_peak_not_bracketed"
    if "fit" in lowered:
        return "rabi_fit_not_converged"
    if "candidate" in lowered or "eligible" in lowered:
        return "rabi_candidate_ineligible"
    if "publish" in lowered:
        return "rabi_publication_failed"
    if "evidence" in lowered or "artifact" in lowered or "recovery" in lowered or "idempotency" in lowered:
        return "rabi_recovery_required"
    if "result" in lowered or "dataset" in lowered:
        return "rabi_result_invalid"
    return "rabi_request_invalid"


__all__ = ["RABI_ERROR_CODES", "RABI_EXPERIMENT_ID", "RABI_POLICY_PATH", "RABI_SCAN_WORKFLOW_ID", "RabiAmplitudeAxis", "RabiAnalysis", "RabiDataset", "RabiError", "RabiRequest", "RabiRun", "amplitude_axis", "analyze_rabi", "build_rabi_circuits", "load_rabi_analysis_policy", "run_qubit_rabi_scan", "verify_rabi_evidence_tree", "verify_rabi_scan"]
