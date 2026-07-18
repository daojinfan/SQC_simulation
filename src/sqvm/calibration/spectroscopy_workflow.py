"""End-to-end bounded qubit-spectroscopy calibration workflow."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
import uuid

import numpy as np

from sqvm.circuits import CircuitExecutionContext, CircuitExecutionProfile
from sqvm.calibration.spectroscopy import (
    SpectroscopyAnalysis,
    SpectroscopyAxis,
    SpectroscopyDataset,
    SpectroscopyMode,
    SpectroscopyPulsePolicy,
    SpectroscopyRequest,
    analyze_qubit_spectroscopy,
    build_qubit_capability_adapter,
    run_qubit_spectroscopy,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import (
    canonical_json_bytes as qcis_canonical_json_bytes,
    sha256_json,
)
from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.storage import atomic_publish, write_canonical_new


WORKFLOW_ID = "qubit_spectroscopy_calibration_v1"
_ACTOR_ID = re.compile(r"[a-z][a-z0-9._-]{2,63}$")
_SHA256 = re.compile(r"[0-9A-F]{64}$")


class SpectroscopyCalibrationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SpectroscopyCalibrationPolicy:
    fine_span_GHz: float
    fine_points: int
    confirmation_span_GHz: float
    confirmation_points: int
    min_contrast: float
    near_peak_tolerance: float = 1.0e-12
    max_leakage: float = 0.05
    max_norm_error: float = 1.0e-8
    max_coarse_refined_shift_GHz: float = 0.1
    max_parallel_peak_shift_GHz: float = 0.02
    max_cross_excitation: float = 0.05
    max_parallel_leakage_delta: float = 0.02

    def to_dict(self) -> dict[str, Any]:
        return {
            "fine_span_GHz": self.fine_span_GHz,
            "fine_points": self.fine_points,
            "confirmation_span_GHz": self.confirmation_span_GHz,
            "confirmation_points": self.confirmation_points,
            "min_contrast": self.min_contrast,
            "near_peak_tolerance": self.near_peak_tolerance,
            "max_leakage": self.max_leakage,
            "max_norm_error": self.max_norm_error,
            "max_coarse_refined_shift_GHz": self.max_coarse_refined_shift_GHz,
            "max_parallel_peak_shift_GHz": self.max_parallel_peak_shift_GHz,
            "max_cross_excitation": self.max_cross_excitation,
            "max_parallel_leakage_delta": self.max_parallel_leakage_delta,
        }


@dataclass(frozen=True, slots=True)
class SpectroscopyCalibrationRequest:
    coarse_request: SpectroscopyRequest
    policy: SpectroscopyCalibrationPolicy


@dataclass(frozen=True, slots=True)
class SpectroscopyCalibrationRun:
    root: Path
    run_id: str
    recommendation_id: str
    recommendation_eligible: bool
    candidates: Mapping[str, Mapping[str, Any]]
    workflow_sha256: str
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class SpectroscopyCalibrationDecision:
    root: Path
    decision_id: str
    decision: str
    calibration_path: Path | None
    receipt_sha256: str


def run_qubit_spectroscopy_calibration(
    request: SpectroscopyCalibrationRequest,
    context: CircuitExecutionContext,
    parent_calibration_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    timeout_s: float = 600.0,
    execution_profile: CircuitExecutionProfile = CircuitExecutionProfile.CALIBRATION_SCAN,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> SpectroscopyCalibrationRun:
    """Run coarse/refined spectroscopy and required parallel confirmations."""

    root = _repository_root(repository_root)
    target = _inside(output_root, root, "calibration output")
    if target.exists():
        raise FileExistsError(f"calibration output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    parent_path = _inside(parent_calibration_path, root, "parent calibration")
    parent = _load_json_mapping(parent_path, "parent calibration")
    parent_sha256 = _raw_sha256(parent_path)
    adapter = build_qubit_capability_adapter(context)
    _validate_parent_context(parent, context)
    _validate_calibration_request(request, adapter.capabilities)

    run_id = str(uuid.uuid4())
    recommendation_id = str(uuid.uuid4())
    staging = target.parent / f".s7sp_{run_id[:8]}"
    if staging.exists():
        raise FileExistsError(f"calibration staging already exists: {staging}")
    staging.mkdir()
    preserve_staging = False
    try:
        coarse_dataset = run_qubit_spectroscopy(
            request.coarse_request,
            context,
            staging / "c",
            root,
            timeout_s=timeout_s,
            execution_profile=execution_profile,
            progress_callback=progress_callback,
        )
        coarse_analysis = _analyze(coarse_dataset, request.policy)
        _require_peaks(coarse_analysis, "coarse")

        refined_request = _refined_request(request, coarse_analysis)
        refined_dataset = run_qubit_spectroscopy(
            refined_request,
            context,
            staging / "f",
            root,
            timeout_s=timeout_s,
            execution_profile=execution_profile,
            progress_callback=progress_callback,
        )
        refined_analysis = _analyze(refined_dataset, request.policy)

        confirmation_datasets: dict[str, SpectroscopyDataset] = {}
        confirmation_analyses: dict[str, SpectroscopyAnalysis] = {}
        if request.coarse_request.execution_mode == SpectroscopyMode.PARALLEL_LOCKSTEP:
            for index, target_name in enumerate(request.coarse_request.targets):
                confirmation_request = _confirmation_request(
                    request,
                    target_name,
                    refined_analysis,
                )
                dataset = run_qubit_spectroscopy(
                    confirmation_request,
                    context,
                    staging / f"s{index}",
                    root,
                    timeout_s=timeout_s,
                    execution_profile=execution_profile,
                    progress_callback=progress_callback,
                )
                confirmation_datasets[target_name] = dataset
                confirmation_analyses[target_name] = _analyze(dataset, request.policy)

        gates = _evaluate_gates(
            request,
            adapter.capabilities,
            coarse_dataset,
            coarse_analysis,
            refined_dataset,
            refined_analysis,
            confirmation_datasets,
            confirmation_analyses,
        )
        recommendation_eligible = all(gate["passed"] for gate in gates)
        candidates = _build_candidates(
            request,
            adapter.capabilities,
            coarse_dataset,
            refined_dataset,
            refined_analysis,
            confirmation_datasets,
            confirmation_analyses,
            recommendation_eligible,
        )

        dataset_hashes = _write_datasets(
            staging,
            coarse_dataset,
            refined_dataset,
            confirmation_datasets,
        )
        plot_path = staging / "spectroscopy.png"
        _write_plot(
            plot_path,
            request.coarse_request.targets,
            coarse_dataset,
            refined_dataset,
            confirmation_datasets,
        )
        plot_sha256 = _raw_sha256(plot_path)
        created_utc = utc_now_text()
        workflow = {
            "schema_version": "0.1",
            "artifact_type": "stage_07_qubit_spectroscopy_calibration",
            "artifact_version": "0.1",
            "workflow_id": WORKFLOW_ID,
            "run_id": run_id,
            "recommendation_id": recommendation_id,
            "created_utc": created_utc,
            "status": "completed",
            "claim": {
                "evidence_class": "model_calibration_simulation",
                "physics_claim": "model_derived_only",
                "hardware_measurement": False,
                "bounded_smoke_only": execution_profile is CircuitExecutionProfile.BOUNDED_SMOKE,
                "execution_profile": execution_profile.value,
                "numerical_replay_policy": (
                    "deferred_batch_review"
                    if execution_profile is CircuitExecutionProfile.CALIBRATION_SCAN
                    else "synchronous_full_replay"
                ),
                "calibration_update_scope": "simulator_configuration_only",
            },
            "parent_calibration": {
                "path": parent_path.relative_to(root).as_posix(),
                "sha256": parent_sha256,
                "state_id": parent.get("state_id"),
            },
            "request": _request_payload(request),
            "capability_adapter_sha256": adapter.authority_sha256,
            "datasets": dataset_hashes,
            "analyses": {
                "coarse": coarse_analysis.to_dict(),
                "refined": refined_analysis.to_dict(),
                "confirmations": {
                    target_name: analysis.to_dict()
                    for target_name, analysis in confirmation_analyses.items()
                },
            },
            "gates": gates,
            "candidates": candidates,
            "recommendation_eligible": recommendation_eligible,
            "plot_sha256": plot_sha256,
        }
        workflow_sha256 = write_canonical_new(staging / "workflow.json", workflow)
        receipt = {
            "schema_version": "0.1",
            "artifact_type": "stage_07_qubit_spectroscopy_calibration_receipt",
            "artifact_version": "0.1",
            "run_id": run_id,
            "status": "completed",
            "workflow_sha256": workflow_sha256,
            "plot_sha256": plot_sha256,
            "dataset_sha256s": dataset_hashes,
            "recommendation_eligible": recommendation_eligible,
            "parent_calibration_sha256": parent_sha256,
        }
        receipt_sha256 = write_canonical_new(staging / "receipt.json", receipt)
        _verify_workflow_directory(staging)
        try:
            atomic_publish(staging, target)
        except Exception as exc:
            preserve_staging = True
            raise SpectroscopyCalibrationError(
                f"workflow publication failed; verified staging was preserved at {staging}"
            ) from exc
        return SpectroscopyCalibrationRun(
            target,
            run_id,
            recommendation_id,
            recommendation_eligible,
            MappingProxyType(
                {candidate["target"]: MappingProxyType(candidate) for candidate in candidates}
            ),
            workflow_sha256,
            receipt_sha256,
        )
    except Exception:
        if (
            not preserve_staging
            and staging.exists()
            and staging.resolve().parent == target.parent.resolve()
        ):
            shutil.rmtree(staging)
        raise


def decide_qubit_spectroscopy_calibration(
    calibration_run_root: str | Path,
    parent_calibration_path: str | Path,
    output_root: str | Path,
    context: CircuitExecutionContext,
    *,
    decision: str,
    actor_id: str,
    reason: str,
    confirmation_phrase: str,
    accepted_targets: Sequence[str] = (),
    repository_root: str | Path | None = None,
) -> SpectroscopyCalibrationDecision:
    """Publish an explicit accept/reject transaction for one verified workflow."""

    root = _repository_root(repository_root)
    run_root = _inside(calibration_run_root, root, "calibration run")
    workflow, workflow_sha256, run_receipt_sha256 = _verify_workflow_directory(run_root)
    parent_path = _inside(parent_calibration_path, root, "parent calibration")
    parent = _load_json_mapping(parent_path, "parent calibration")
    parent_sha256 = _raw_sha256(parent_path)
    _validate_parent_context(parent, context)
    if parent_sha256 != workflow["parent_calibration"]["sha256"]:
        raise SpectroscopyCalibrationError("parent calibration no longer matches the workflow")
    if decision not in {"accept", "reject"}:
        raise SpectroscopyCalibrationError("decision must be accept or reject")
    if not isinstance(actor_id, str) or _ACTOR_ID.fullmatch(actor_id) is None:
        raise SpectroscopyCalibrationError("actor_id is invalid")
    if not isinstance(reason, str) or not 1 <= len(reason) <= 1024 or "\n" in reason or "\r" in reason:
        raise SpectroscopyCalibrationError("reason must contain 1-1024 characters without CR/LF")
    expected_phrase = f"{decision.upper()} SIMULATION CALIBRATION {workflow['recommendation_id']}"
    if confirmation_phrase != expected_phrase:
        raise SpectroscopyCalibrationError("confirmation phrase is invalid")
    candidate_map = {row["target"]: row for row in workflow["candidates"]}
    adapter = build_qubit_capability_adapter(context)
    if any(
        target not in adapter.capabilities
        or row["current_frequency_GHz"]
        != adapter.capabilities[target].reference_frequency_GHz
        for target, row in candidate_map.items()
    ):
        raise SpectroscopyCalibrationError("candidate frequency authority does not match context")
    selected = tuple(accepted_targets)
    if len(selected) != len(set(selected)) or any(target not in candidate_map for target in selected):
        raise SpectroscopyCalibrationError("accepted_targets are invalid")
    if decision == "accept":
        if not workflow["recommendation_eligible"]:
            raise SpectroscopyCalibrationError("workflow is not recommendation eligible")
        if not selected:
            raise SpectroscopyCalibrationError("accept requires at least one target")
        if any(candidate_map[target]["recommendation_eligible"] is not True for target in selected):
            raise SpectroscopyCalibrationError("selected candidate is not eligible")
    elif selected:
        raise SpectroscopyCalibrationError("reject cannot contain accepted_targets")

    target = _inside(output_root, root, "decision output")
    if target.exists():
        raise FileExistsError(f"decision output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    decision_id = str(uuid.uuid4())
    staging = target.parent / f".s7sd_{decision_id[:8]}"
    staging.mkdir()
    preserve_staging = False
    try:
        decision_payload = {
            "schema_version": "0.1",
            "artifact_type": "stage_07_calibration_decision",
            "artifact_version": "0.1",
            "decision_id": decision_id,
            "recommendation_id": workflow["recommendation_id"],
            "recommendation_sha256": workflow_sha256,
            "parent_calibration_sha256": parent_sha256,
            "decision": decision,
            "accepted_targets": list(selected),
            "actor_id": actor_id,
            "reason": reason,
            "confirmation_phrase": confirmation_phrase,
            "decided_utc": utc_now_text(),
        }
        decision_sha256 = write_canonical_new(staging / "decision.json", decision_payload)
        calibration_path: Path | None = None
        calibration_sha256: str | None = None
        if decision == "accept":
            calibration = _build_calibration_snapshot(
                parent,
                parent_sha256,
                workflow,
                workflow_sha256,
                run_receipt_sha256,
                decision_payload,
                decision_sha256,
                context,
                selected,
                candidate_map,
            )
            calibration_sha256 = write_canonical_new(staging / "calibration.json", calibration)
            calibration_path = target / "calibration.json"
        receipt = {
            "schema_version": "0.1",
            "artifact_type": "stage_07_calibration_decision_receipt",
            "artifact_version": "0.1",
            "decision_id": decision_id,
            "status": "accepted" if decision == "accept" else "rejected",
            "decision_sha256": decision_sha256,
            "calibration_sha256": calibration_sha256,
            "workflow_sha256": workflow_sha256,
            "run_receipt_sha256": run_receipt_sha256,
            "parent_calibration_sha256": parent_sha256,
        }
        receipt_sha256 = write_canonical_new(staging / "receipt.json", receipt)
        try:
            atomic_publish(staging, target)
        except Exception as exc:
            preserve_staging = True
            raise SpectroscopyCalibrationError(
                f"decision publication failed; verified staging was preserved at {staging}"
            ) from exc
        return SpectroscopyCalibrationDecision(
            target,
            decision_id,
            decision,
            calibration_path,
            receipt_sha256,
        )
    except Exception:
        if (
            not preserve_staging
            and staging.exists()
            and staging.resolve().parent == target.parent.resolve()
        ):
            shutil.rmtree(staging)
        raise


def context_with_spectroscopy_calibration(
    context: CircuitExecutionContext,
    calibration_path: str | Path,
) -> CircuitExecutionContext:
    """Return a new context carrying an accepted simulation frequency snapshot."""

    path = Path(calibration_path).resolve()
    _decision, snapshot, _receipt = _verify_decision_directory(path.parent)
    if snapshot is None or path != path.parent / "calibration.json":
        raise SpectroscopyCalibrationError("accepted calibration path is invalid")
    if (
        snapshot.get("artifact_type") != "stage_07_simulation_calibration"
        or snapshot.get("status") != "accepted_simulation"
        or snapshot.get("hardware_claim") != "none"
    ):
        raise SpectroscopyCalibrationError("calibration snapshot is not accepted simulation evidence")
    values = snapshot.get("values")
    qagent_values = values.get("qagents") if isinstance(values, Mapping) else None
    if not isinstance(qagent_values, Mapping):
        raise SpectroscopyCalibrationError("calibration snapshot has no qagent values")
    authorities = copy.deepcopy(dict(context.authorities))
    registry = authorities.get("qagent_registry")
    if not isinstance(registry, dict):
        raise SpectroscopyCalibrationError("context qagent registry is invalid")
    for target, value in qagent_values.items():
        if target not in registry or not isinstance(value, Mapping):
            raise SpectroscopyCalibrationError(f"calibration target is unsupported: {target}")
        reference = value.get("reference_frequency_authority")
        if not isinstance(reference, Mapping):
            raise SpectroscopyCalibrationError(f"calibration frequency authority is missing: {target}")
        registry[target]["reference_frequency_authority"] = copy.deepcopy(dict(reference))
    authorities["calibration"] = copy.deepcopy(snapshot)
    expected_names = (
        "instruction_profile",
        "qagent_registry",
        "gate_configuration",
        "waveform_registry",
        "clock",
        "compiler",
        "calibration",
    )
    authorities["expected_sha256"] = {
        name: sha256_json(authorities[name]) for name in expected_names
    }
    return CircuitExecutionContext(
        authorities,
        dict(context.idle_flux_phi0),
        context.settable_paths,
        context.initial_state_id,
        context.observable_set_id,
    )


def verify_qubit_spectroscopy_calibration(
    run_root: str | Path,
) -> bool:
    """Verify canonical workflow, dataset and receipt bindings."""

    _verify_workflow_directory(Path(run_root).resolve())
    return True


def verify_qubit_spectroscopy_calibration_decision(
    decision_root: str | Path,
) -> bool:
    """Verify one spectroscopy calibration decision transaction."""

    _verify_decision_directory(Path(decision_root).resolve())
    return True


def _validate_calibration_request(
    request: SpectroscopyCalibrationRequest,
    capabilities: Mapping[str, Any],
) -> None:
    if not isinstance(request, SpectroscopyCalibrationRequest):
        raise SpectroscopyCalibrationError("typed calibration request is required")
    coarse = request.coarse_request
    if not isinstance(coarse, SpectroscopyRequest) or coarse.run_phase != "coarse":
        raise SpectroscopyCalibrationError("coarse_request.run_phase must be coarse")
    if any(target not in capabilities for target in coarse.targets):
        raise SpectroscopyCalibrationError("coarse request contains an unsupported target")
    for axis in coarse.axes:
        if len(axis.frequencies_GHz) < 3 or len(axis.frequencies_GHz) % 2 == 0:
            raise SpectroscopyCalibrationError("coarse axes require an odd point count of at least three")
    policy = request.policy
    if not isinstance(policy, SpectroscopyCalibrationPolicy):
        raise SpectroscopyCalibrationError("typed calibration policy is required")
    for name in ("fine_points", "confirmation_points"):
        value = getattr(policy, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 3 or value % 2 == 0:
            raise SpectroscopyCalibrationError(f"{name} must be an odd integer of at least three")
    positive = (
        "fine_span_GHz",
        "confirmation_span_GHz",
        "max_norm_error",
        "max_coarse_refined_shift_GHz",
        "max_parallel_peak_shift_GHz",
    )
    nonnegative = (
        "min_contrast",
        "near_peak_tolerance",
        "max_leakage",
        "max_cross_excitation",
        "max_parallel_leakage_delta",
    )
    for name in positive:
        value = getattr(policy, name)
        if not _finite(value) or float(value) <= 0.0:
            raise SpectroscopyCalibrationError(f"{name} must be positive and finite")
    for name in nonnegative:
        value = getattr(policy, name)
        if not _finite(value) or float(value) < 0.0:
            raise SpectroscopyCalibrationError(f"{name} must be nonnegative and finite")


def _validate_parent_context(
    parent: Mapping[str, Any],
    context: CircuitExecutionContext,
) -> None:
    values = parent.get("values")
    qagent_values = values.get("qagents") if isinstance(values, Mapping) else None
    if qagent_values is None:
        return
    if not isinstance(qagent_values, Mapping):
        raise SpectroscopyCalibrationError("parent qagent values are invalid")
    registry = context.authorities.get("qagent_registry")
    if not isinstance(registry, Mapping):
        raise SpectroscopyCalibrationError("context qagent registry is invalid")
    for target, value in qagent_values.items():
        if not isinstance(value, Mapping) or target not in registry:
            raise SpectroscopyCalibrationError(f"parent calibration target is invalid: {target}")
        reference = value.get("reference_frequency_authority")
        if reference is not None and reference != registry[target].get("reference_frequency_authority"):
            raise SpectroscopyCalibrationError(
                f"context frequency authority does not match parent calibration: {target}"
            )


def _analyze(
    dataset: SpectroscopyDataset,
    policy: SpectroscopyCalibrationPolicy,
) -> SpectroscopyAnalysis:
    return analyze_qubit_spectroscopy(
        dataset,
        min_contrast=policy.min_contrast,
        near_peak_tolerance=policy.near_peak_tolerance,
        quadratic_refinement=True,
    )


def _require_peaks(analysis: SpectroscopyAnalysis, phase: str) -> None:
    if not analysis.peak_quality_eligible:
        reasons = {target: peak.reason for target, peak in analysis.peaks.items()}
        raise SpectroscopyCalibrationError(f"{phase} scan cannot seed the next phase: {reasons}")


def _refined_request(
    request: SpectroscopyCalibrationRequest,
    analysis: SpectroscopyAnalysis,
) -> SpectroscopyRequest:
    axes = tuple(
        SpectroscopyAxis(
            target,
            _centered_axis(
                _peak_frequency(analysis, target),
                request.policy.fine_span_GHz,
                request.policy.fine_points,
            ),
        )
        for target in request.coarse_request.targets
    )
    return SpectroscopyRequest(
        request.coarse_request.execution_mode,
        "refined",
        request.coarse_request.targets,
        axes,
        request.coarse_request.pulse_policies,
        request.coarse_request.max_points,
    )


def _confirmation_request(
    request: SpectroscopyCalibrationRequest,
    target: str,
    analysis: SpectroscopyAnalysis,
) -> SpectroscopyRequest:
    policies = {policy.qagent: policy for policy in request.coarse_request.pulse_policies}
    return SpectroscopyRequest(
        SpectroscopyMode.SINGLE,
        "confirmation",
        (target,),
        (
            SpectroscopyAxis(
                target,
                _centered_axis(
                    _peak_frequency(analysis, target),
                    request.policy.confirmation_span_GHz,
                    request.policy.confirmation_points,
                ),
            ),
        ),
        (policies[target],),
        request.coarse_request.max_points,
    )


def _centered_axis(center: float, span: float, count: int) -> tuple[float, ...]:
    values = np.linspace(center - span / 2.0, center + span / 2.0, count, dtype="<f8")
    if values[0] <= 0.0 or any(not math.isfinite(float(value)) for value in values):
        raise SpectroscopyCalibrationError("derived frequency axis is invalid")
    return tuple(float(value) for value in values)


def _peak_frequency(analysis: SpectroscopyAnalysis, target: str) -> float:
    peak = analysis.peaks[target]
    value = peak.estimated_frequency_GHz
    if not peak.valid or value is None or not math.isfinite(value):
        raise SpectroscopyCalibrationError(f"{target} has no valid peak")
    return value


def _evaluate_gates(
    request: SpectroscopyCalibrationRequest,
    capabilities: Mapping[str, Any],
    coarse_dataset: SpectroscopyDataset,
    coarse_analysis: SpectroscopyAnalysis,
    refined_dataset: SpectroscopyDataset,
    refined_analysis: SpectroscopyAnalysis,
    confirmations: Mapping[str, SpectroscopyDataset],
    confirmation_analyses: Mapping[str, SpectroscopyAnalysis],
) -> list[dict[str, Any]]:
    policy = request.policy
    gates: list[dict[str, Any]] = []
    _gate(gates, "coarse_peak_quality", coarse_analysis.peak_quality_eligible, {})
    _gate(gates, "refined_peak_quality", refined_analysis.peak_quality_eligible, {})
    all_datasets = [coarse_dataset, refined_dataset, *confirmations.values()]
    max_leakage = max(point.circuit_result.leakage for dataset in all_datasets for point in dataset.points)
    max_norm_error = max(point.circuit_result.norm_error for dataset in all_datasets for point in dataset.points)
    _gate(gates, "leakage_within_limit", max_leakage <= policy.max_leakage, {
        "actual": max_leakage, "limit": policy.max_leakage,
    })
    _gate(gates, "norm_error_within_limit", max_norm_error <= policy.max_norm_error, {
        "actual": max_norm_error, "limit": policy.max_norm_error,
    })
    for target in request.coarse_request.targets:
        shift = abs(_peak_frequency(coarse_analysis, target) - _peak_frequency(refined_analysis, target))
        _gate(gates, f"{target}.coarse_refined_peak_consistent", shift <= policy.max_coarse_refined_shift_GHz, {
            "actual_GHz": shift, "limit_GHz": policy.max_coarse_refined_shift_GHz,
        })
    if request.coarse_request.execution_mode == SpectroscopyMode.PARALLEL_LOCKSTEP:
        for target in request.coarse_request.targets:
            analysis = confirmation_analyses[target]
            _gate(gates, f"{target}.confirmation_peak_quality", analysis.peak_quality_eligible, {})
            if analysis.peak_quality_eligible and refined_analysis.peaks[target].valid:
                shift = abs(_peak_frequency(refined_analysis, target) - _peak_frequency(analysis, target))
                _gate(gates, f"{target}.parallel_peak_shift", shift <= policy.max_parallel_peak_shift_GHz, {
                    "actual_GHz": shift, "limit_GHz": policy.max_parallel_peak_shift_GHz,
                })
            else:
                _gate(gates, f"{target}.parallel_peak_shift", False, {"reason": "missing_valid_peak"})
            cross = _max_spectator_excitation(confirmations[target], target, capabilities)
            _gate(gates, f"{target}.cross_excitation", cross <= policy.max_cross_excitation, {
                "actual": cross, "limit": policy.max_cross_excitation,
            })
            parallel_leakage = max(point.circuit_result.leakage for point in refined_dataset.points)
            single_leakage = max(point.circuit_result.leakage for point in confirmations[target].points)
            delta = parallel_leakage - single_leakage
            _gate(gates, f"{target}.parallel_leakage_delta", delta <= policy.max_parallel_leakage_delta, {
                "actual": delta, "limit": policy.max_parallel_leakage_delta,
            })
    return gates


def _max_spectator_excitation(
    dataset: SpectroscopyDataset,
    driven_target: str,
    capabilities: Mapping[str, Any],
) -> float:
    driven_component = capabilities[driven_target].backend_component_slot
    values = []
    for point in dataset.points:
        primitive = point.circuit_result.dressed_populations
        values.append(
            primitive.population_001 + primitive.population_101
            if driven_component == "q1"
            else primitive.population_100 + primitive.population_101
        )
    return max(values)


def _gate(
    rows: list[dict[str, Any]],
    name: str,
    passed: bool,
    metrics: Mapping[str, Any],
) -> None:
    rows.append({"name": name, "passed": bool(passed), "metrics": dict(metrics)})


def _build_candidates(
    request: SpectroscopyCalibrationRequest,
    capabilities: Mapping[str, Any],
    coarse_dataset: SpectroscopyDataset,
    refined_dataset: SpectroscopyDataset,
    refined_analysis: SpectroscopyAnalysis,
    confirmations: Mapping[str, SpectroscopyDataset],
    confirmation_analyses: Mapping[str, SpectroscopyAnalysis],
    eligible: bool,
) -> list[dict[str, Any]]:
    rows = []
    for target in request.coarse_request.targets:
        source_analysis = confirmation_analyses.get(target, refined_analysis)
        proposed = (
            _peak_frequency(source_analysis, target)
            if source_analysis.peaks[target].valid
            else None
        )
        current = capabilities[target].reference_frequency_GHz
        rows.append({
            "schema": "qubit_reference_frequency_delta_v1",
            "target": target,
            "field": f"values.qagents.{target}.reference_frequency_authority",
            "current_frequency_GHz": current,
            "proposed_frequency_GHz": proposed,
            "delta_GHz": None if proposed is None else proposed - current,
            "source_dataset_sha256s": sorted({
                coarse_dataset.dataset_sha256,
                refined_dataset.dataset_sha256,
                *(
                    [confirmations[target].dataset_sha256]
                    if target in confirmations
                    else []
                ),
            }),
            "simulation_only": True,
            "recommendation_eligible": bool(eligible and proposed is not None),
        })
    return rows


def _write_datasets(
    staging: Path,
    coarse: SpectroscopyDataset,
    refined: SpectroscopyDataset,
    confirmations: Mapping[str, SpectroscopyDataset],
) -> dict[str, Any]:
    directory = staging / "datasets"
    directory.mkdir()
    hashes: dict[str, Any] = {
        "coarse": _write_dataset(directory / "coarse.json", coarse.to_dict()),
        "refined": _write_dataset(directory / "refined.json", refined.to_dict()),
        "confirmations": {},
    }
    for target, dataset in confirmations.items():
        hashes["confirmations"][target] = _write_dataset(
            directory / f"confirmation_{target}.json",
            dataset.to_dict(),
        )
    return hashes


def _write_plot(
    path: Path,
    targets: Sequence[str],
    coarse: SpectroscopyDataset,
    refined: SpectroscopyDataset,
    confirmations: Mapping[str, SpectroscopyDataset],
) -> None:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(len(targets), 1, figsize=(7.2, 3.4 * len(targets)), squeeze=False)
    for index, target in enumerate(targets):
        axis = axes[index, 0]
        for dataset, label, marker in (
            (coarse, "coarse", "o"),
            (refined, "refined", "s"),
        ):
            axis.plot(
                [point.point.coordinates_GHz[target] for point in dataset.points],
                [point.target_excited_population[target] for point in dataset.points],
                marker=marker,
                label=label,
            )
        if target in confirmations:
            dataset = confirmations[target]
            axis.plot(
                [point.point.coordinates_GHz[target] for point in dataset.points],
                [point.target_excited_population[target] for point in dataset.points],
                marker="^",
                label="single confirmation",
            )
        axis.set_title(f"{target} model-derived closed-system population")
        axis.set_xlabel("Drive frequency (GHz)")
        axis.set_ylabel("Excited population")
        axis.grid(True, alpha=0.25)
        axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=150, metadata={"Software": "sqvm"})
    plt.close(figure)


def _request_payload(request: SpectroscopyCalibrationRequest) -> dict[str, Any]:
    coarse = request.coarse_request
    return {
        "coarse_request": {
            "execution_mode": str(coarse.execution_mode),
            "run_phase": coarse.run_phase,
            "targets": list(coarse.targets),
            "axes": [
                {"qagent": axis.qagent, "frequencies_GHz": list(axis.frequencies_GHz)}
                for axis in coarse.axes
            ],
            "pulse_policies": [policy.to_dict() for policy in coarse.pulse_policies],
            "max_points": coarse.max_points,
        },
        "workflow_policy": request.policy.to_dict(),
    }


def _build_calibration_snapshot(
    parent: Mapping[str, Any],
    parent_sha256: str,
    workflow: Mapping[str, Any],
    workflow_sha256: str,
    run_receipt_sha256: str,
    decision: Mapping[str, Any],
    decision_sha256: str,
    context: CircuitExecutionContext,
    selected: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    adapter = build_qubit_capability_adapter(context)
    registry = context.authorities["qagent_registry"]
    values = copy.deepcopy(parent.get("values", {}))
    if not isinstance(values, dict):
        raise SpectroscopyCalibrationError("parent calibration values are invalid")
    qagents = values.setdefault("qagents", {})
    if not isinstance(qagents, dict):
        raise SpectroscopyCalibrationError("parent qagent calibration values are invalid")
    for target in adapter.capabilities:
        raw = registry[target]["reference_frequency_authority"]
        qagents.setdefault(target, {})["reference_frequency_authority"] = copy.deepcopy(dict(raw))
    for target in selected:
        previous = registry[target]["reference_frequency_authority"]
        reference = {
            "reference_frequency_GHz": candidates[target]["proposed_frequency_GHz"],
            "frequency_source": "accepted_simulation",
            "calibration_run_id": workflow["run_id"],
            "revision": int(previous["revision"]) + 1,
        }
        reference["setting_hash"] = sha256_json(reference)
        qagents[target]["reference_frequency_authority"] = reference
    expected = context.authorities["expected_sha256"]
    return {
        "schema_version": "0.1",
        "artifact_type": "stage_07_simulation_calibration",
        "artifact_version": "0.1",
        "calibration_id": str(uuid.uuid4()),
        "state_id": str(uuid.uuid4()),
        "status": "accepted_simulation",
        "accepted": True,
        "hardware_claim": "none",
        "device_snapshot": parent.get("device_snapshot"),
        "device_snapshot_sha256": parent.get("device_snapshot_sha256"),
        "parent_calibration_sha256": parent_sha256,
        "recommendation_id": workflow["recommendation_id"],
        "recommendation_sha256": workflow_sha256,
        "run_receipt_sha256": run_receipt_sha256,
        "decision_id": decision["decision_id"],
        "decision_sha256": decision_sha256,
        "accepted_targets": list(selected),
        "authority_sha256s": dict(expected),
        "values": values,
    }


def _verify_workflow_directory(directory: Path) -> tuple[dict[str, Any], str, str]:
    workflow_path = directory / "workflow.json"
    receipt_path = directory / "receipt.json"
    workflow = _load_canonical(workflow_path, "spectroscopy workflow")
    receipt = _load_canonical(receipt_path, "spectroscopy receipt")
    workflow_sha256 = _raw_sha256(workflow_path)
    receipt_sha256 = _raw_sha256(receipt_path)
    if (
        workflow.get("artifact_type") != "stage_07_qubit_spectroscopy_calibration"
        or workflow.get("workflow_id") != WORKFLOW_ID
        or workflow.get("status") != "completed"
    ):
        raise SpectroscopyCalibrationError("workflow identity is invalid")
    if (
        receipt.get("artifact_type") != "stage_07_qubit_spectroscopy_calibration_receipt"
        or receipt.get("run_id") != workflow.get("run_id")
        or receipt.get("workflow_sha256") != workflow_sha256
        or receipt.get("recommendation_eligible") is not workflow.get("recommendation_eligible")
    ):
        raise SpectroscopyCalibrationError("workflow receipt binding is invalid")
    if _raw_sha256(directory / "spectroscopy.png") != workflow.get("plot_sha256"):
        raise SpectroscopyCalibrationError("spectroscopy plot hash mismatch")
    dataset_hashes = workflow.get("datasets")
    if not isinstance(dataset_hashes, Mapping) or receipt.get("dataset_sha256s") != dataset_hashes:
        raise SpectroscopyCalibrationError("dataset inventory binding is invalid")
    expected_files = {
        "coarse": directory / "datasets" / "coarse.json",
        "refined": directory / "datasets" / "refined.json",
    }
    for name, path in expected_files.items():
        _load_dataset(path, f"{name} dataset")
        if _raw_sha256(path) != dataset_hashes.get(name):
            raise SpectroscopyCalibrationError(f"{name} dataset hash mismatch")
    confirmations = dataset_hashes.get("confirmations")
    if not isinstance(confirmations, Mapping):
        raise SpectroscopyCalibrationError("confirmation dataset inventory is invalid")
    for target, expected_hash in confirmations.items():
        path = directory / "datasets" / f"confirmation_{target}.json"
        _load_dataset(path, f"{target} confirmation dataset")
        if _raw_sha256(path) != expected_hash:
            raise SpectroscopyCalibrationError(f"{target} confirmation dataset hash mismatch")
    analyses = workflow.get("analyses")
    if not isinstance(analyses, Mapping):
        raise SpectroscopyCalibrationError("workflow analyses are invalid")
    for phase in ("coarse", "refined"):
        analysis = analyses.get(phase)
        if not isinstance(analysis, Mapping) or analysis.get("dataset_sha256") != dataset_hashes[phase]:
            raise SpectroscopyCalibrationError(f"{phase} analysis dataset binding is invalid")
    confirmation_analyses = analyses.get("confirmations")
    if not isinstance(confirmation_analyses, Mapping) or set(confirmation_analyses) != set(confirmations):
        raise SpectroscopyCalibrationError("confirmation analysis inventory is invalid")
    for target, analysis in confirmation_analyses.items():
        if not isinstance(analysis, Mapping) or analysis.get("dataset_sha256") != confirmations[target]:
            raise SpectroscopyCalibrationError(f"{target} confirmation analysis binding is invalid")
    gates = workflow.get("gates")
    if (
        not isinstance(gates, list)
        or not gates
        or any(
            not isinstance(row, Mapping)
            or set(row) != {"name", "passed", "metrics"}
            or not isinstance(row["name"], str)
            or type(row["passed"]) is not bool
            or not isinstance(row["metrics"], Mapping)
            for row in gates
        )
        or workflow.get("recommendation_eligible")
        is not all(row["passed"] for row in gates)
    ):
        raise SpectroscopyCalibrationError("workflow gate aggregation is invalid")
    candidates = workflow.get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(row, Mapping)
        or row.get("recommendation_eligible") is not workflow["recommendation_eligible"]
        for row in candidates
    ):
        raise SpectroscopyCalibrationError("candidate eligibility binding is invalid")
    request_payload = workflow.get("request")
    coarse_payload = (
        request_payload.get("coarse_request")
        if isinstance(request_payload, Mapping)
        else None
    )
    request_targets = coarse_payload.get("targets") if isinstance(coarse_payload, Mapping) else None
    if (
        not isinstance(request_targets, list)
        or [row.get("target") for row in candidates] != request_targets
    ):
        raise SpectroscopyCalibrationError("candidate target ordering is invalid")
    for candidate in candidates:
        target = candidate["target"]
        source_analysis = confirmation_analyses.get(target, analyses["refined"])
        peaks = source_analysis.get("peaks") if isinstance(source_analysis, Mapping) else None
        peak = peaks.get(target) if isinstance(peaks, Mapping) else None
        expected_frequency = (
            peak.get("estimated_frequency_GHz")
            if isinstance(peak, Mapping) and peak.get("valid") is True
            else None
        )
        expected_sources = sorted(
            {
                dataset_hashes["coarse"],
                dataset_hashes["refined"],
                *(
                    [confirmations[target]]
                    if target in confirmations
                    else []
                ),
            }
        )
        current_frequency = candidate.get("current_frequency_GHz")
        if not _finite(current_frequency):
            raise SpectroscopyCalibrationError(f"{target} candidate current frequency is invalid")
        expected_delta = (
            None
            if expected_frequency is None
            else expected_frequency - float(current_frequency)
        )
        if (
            candidate.get("proposed_frequency_GHz") != expected_frequency
            or candidate.get("source_dataset_sha256s") != expected_sources
            or candidate.get("delta_GHz") != expected_delta
        ):
            raise SpectroscopyCalibrationError(f"{target} candidate derivation is invalid")
    if receipt.get("parent_calibration_sha256") != workflow["parent_calibration"]["sha256"]:
        raise SpectroscopyCalibrationError("parent calibration receipt binding is invalid")
    return workflow, workflow_sha256, receipt_sha256


def _verify_decision_directory(
    directory: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    decision_path = directory / "decision.json"
    receipt_path = directory / "receipt.json"
    decision = _load_canonical(decision_path, "calibration decision")
    receipt = _load_canonical(receipt_path, "calibration decision receipt")
    if (
        decision.get("artifact_type") != "stage_07_calibration_decision"
        or decision.get("decision") not in {"accept", "reject"}
        or receipt.get("artifact_type") != "stage_07_calibration_decision_receipt"
        or receipt.get("decision_id") != decision.get("decision_id")
        or receipt.get("decision_sha256") != _raw_sha256(decision_path)
    ):
        raise SpectroscopyCalibrationError("calibration decision receipt binding is invalid")
    calibration: dict[str, Any] | None = None
    if decision["decision"] == "accept":
        calibration_path = directory / "calibration.json"
        calibration = _load_canonical(calibration_path, "accepted calibration")
        if (
            receipt.get("status") != "accepted"
            or receipt.get("calibration_sha256") != _raw_sha256(calibration_path)
            or calibration.get("artifact_type") != "stage_07_simulation_calibration"
            or calibration.get("status") != "accepted_simulation"
            or calibration.get("decision_id") != decision["decision_id"]
            or calibration.get("decision_sha256") != receipt["decision_sha256"]
            or calibration.get("recommendation_sha256") != receipt.get("workflow_sha256")
            or calibration.get("parent_calibration_sha256")
            != receipt.get("parent_calibration_sha256")
        ):
            raise SpectroscopyCalibrationError("accepted calibration transaction binding is invalid")
    elif (
        receipt.get("status") != "rejected"
        or receipt.get("calibration_sha256") is not None
        or (directory / "calibration.json").exists()
    ):
        raise SpectroscopyCalibrationError("rejected calibration transaction binding is invalid")
    return decision, calibration, receipt


def _repository_root(value: str | Path | None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]


def _inside(value: str | Path, root: Path, label: str) -> Path:
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SpectroscopyCalibrationError(f"{label} is outside repository") from exc
    return path


def _load_canonical(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SpectroscopyCalibrationError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise SpectroscopyCalibrationError(f"{label} is not canonical mapping JSON")
    _finite_tree(payload)
    return payload


def _load_json_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text("utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SpectroscopyCalibrationError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SpectroscopyCalibrationError(f"{label} must be a JSON mapping")
    _finite_tree(payload)
    return payload


def _write_dataset(path: Path, payload: Mapping[str, Any]) -> str:
    raw = qcis_canonical_json_bytes(payload)
    with path.open("xb") as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest().upper()


def _load_dataset(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SpectroscopyCalibrationError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict) or raw != qcis_canonical_json_bytes(payload):
        raise SpectroscopyCalibrationError(f"{label} is not canonical dataset JSON")
    _finite_tree(payload)
    return payload


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite_tree(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpectroscopyCalibrationError("non-finite workflow value")
        return
    if isinstance(value, list):
        for item in value:
            _finite_tree(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite_tree(item)
        return
    raise SpectroscopyCalibrationError("unsupported workflow value")


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _finite(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
