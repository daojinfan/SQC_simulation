"""Immutable publication for one spectroscopy request and one dataset."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
import uuid
import warnings

from sqvm.calibration.spectroscopy import (
    SpectroscopyAnalysis,
    SpectroscopyDataset,
    SpectroscopyRequest,
    analyze_qubit_spectroscopy,
    run_qubit_spectroscopy,
)
from sqvm.candidate_protocol import (
    CalibrationCandidateProtocolError,
    calibration_candidate,
    normalize_calibration_candidate,
    parameter_change,
)
from sqvm.circuits import CircuitExecutionContext, CircuitExecutionProfile
from sqvm.calibration.spectroscopy_evidence import (
    SCAN_ARTIFACT_VERSION,
    SpectroscopyEvidenceError,
    verify_completed_evidence,
    write_completed_evidence,
    write_recovery_fallback_marker,
    write_recovery_required,
    write_uncertain_publication,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import canonical_json_bytes as dataset_json_bytes
from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.batch import CircuitBatchCancelledError, verify_circuit_batch
from sqvm.runtime.lifecycle import CancellationToken
from sqvm.runtime.publication import publish_calibration_directory
from sqvm.runtime.storage import write_canonical_new


SPECTROSCOPY_SCAN_WORKFLOW_ID = "qubit_spectroscopy_scan_v1"


class SpectroscopyRunError(ValueError):
    """Raised when a single spectroscopy run or its artifact is invalid."""


@dataclass(frozen=True, slots=True)
class SpectroscopyRun:
    """Published result of exactly one spectroscopy request."""

    root: Path
    run_id: str
    recommendation_id: str
    dataset: SpectroscopyDataset
    analysis: SpectroscopyAnalysis
    recommendation_eligible: bool
    candidates: Mapping[str, Mapping[str, Any]]
    gates: tuple[Mapping[str, Any], ...]
    workflow_sha256: str
    receipt_sha256: str

    @property
    def data(self) -> Mapping[str, Mapping[str, tuple[float, ...]]]:
        """Return plot-ready frequency, P0, P1, and leakage lists per target."""

        rows: dict[str, Mapping[str, tuple[float, ...]]] = {}
        for target in self.dataset.request.targets:
            rows[target] = MappingProxyType(
                {
                    "frequency_GHz": tuple(
                        point.point.coordinates_GHz[target]
                        for point in self.dataset.points
                    ),
                    "P0": tuple(
                        point.circuit_result.dressed_populations.computational_population
                        - point.target_excited_population[target]
                        for point in self.dataset.points
                    ),
                    "P1": tuple(
                        point.target_excited_population[target]
                        for point in self.dataset.points
                    ),
                    "leakage": tuple(
                        point.circuit_result.leakage for point in self.dataset.points
                    ),
                }
            )
        return MappingProxyType(rows)


def run_qubit_spectroscopy_scan(
    request: SpectroscopyRequest,
    context: CircuitExecutionContext,
    parent_configuration_path: str | Path,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    timeout_s: float = 600.0,
    batch_deadline_s: float = 3600.0,
    operation_id: str | None = None,
    execution_profile: CircuitExecutionProfile = CircuitExecutionProfile.CALIBRATION_SCAN,
    cancellation_token: CancellationToken | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> SpectroscopyRun:
    """Execute and publish exactly one spectroscopy request."""

    if not isinstance(request, SpectroscopyRequest) or request.run_phase != "scan":
        raise SpectroscopyRunError("a spectroscopy request with run_phase='scan' is required")
    root = _repository_root(repository_root)
    target = _inside(output_root, root, "spectroscopy output")
    target.parent.mkdir(parents=True, exist_ok=True)
    parent_path = _inside(parent_configuration_path, root, "parent configuration")
    if not parent_path.is_file():
        raise SpectroscopyRunError("parent configuration does not exist")
    parent_sha256 = _raw_sha256(parent_path)
    run_id = _operation_id(operation_id)
    recommendation_id = str(uuid.uuid4())
    coordinator = target.parent / ".runtime-v03"
    if target.exists():
        if operation_id is None:
            raise FileExistsError(f"spectroscopy output already exists: {target}")
        return _reopen_published_scan(
            target,
            request,
            context,
            parent_sha256,
            root,
            coordinator,
            timeout_s,
            batch_deadline_s,
            execution_profile,
            cancellation_token,
            progress_callback,
        )
    staging = target.parent / f".spectroscopy_{run_id.replace('-', '')}"
    if staging.exists() and operation_id is None:
        raise FileExistsError(f"spectroscopy staging already exists: {staging}")
    if not staging.exists():
        staging.mkdir()
    elif (staging / "workflow.json").exists():
        _clear_recovery_markers(staging, run_id)
        verify_qubit_spectroscopy_scan(staging)
        publish_calibration_directory(staging, target)
        return _reopen_published_scan(
            target,
            request,
            context,
            parent_sha256,
            root,
            coordinator,
            timeout_s,
            batch_deadline_s,
            execution_profile,
            cancellation_token,
            progress_callback,
        )
    publication_attempted = False
    created_utc = utc_now_text()
    try:
        execution_root = staging / "execution"
        execution_root.mkdir(exist_ok=True)
        dataset = run_qubit_spectroscopy(
            request,
            context,
            execution_root,
            root,
            timeout_s=timeout_s,
            batch_deadline_s=batch_deadline_s,
            batch_id=run_id,
            coordinator_root=coordinator,
            batch_metadata={
                "run_id": run_id,
                "recommendation_id": recommendation_id,
                "created_utc": created_utc,
                "parent_configuration_sha256": parent_sha256,
            },
            execution_profile=execution_profile,
            cancellation_token=cancellation_token,
            progress_callback=progress_callback,
        )
        if dataset.runtime_batch is None:
            raise SpectroscopyRunError("runtime batch binding is missing")
        metadata = dict(dataset.runtime_batch.metadata)
        if (
            metadata.get("run_id") != run_id
            or metadata.get("parent_configuration_sha256") != parent_sha256
            or not isinstance(metadata.get("recommendation_id"), str)
            or not isinstance(metadata.get("created_utc"), str)
        ):
            raise SpectroscopyRunError("runtime batch metadata binding is invalid")
        recommendation_id = metadata["recommendation_id"]
        created_utc = metadata["created_utc"]
        analysis = analyze_qubit_spectroscopy(
            dataset,
            min_contrast=0.01,
            quadratic_refinement=True,
        )
        dataset_sha256 = _write_dataset(staging / "dataset.json", dataset.to_dict())
        if dataset_sha256 != dataset.dataset_sha256:
            raise SpectroscopyRunError("published dataset identity changed")
        gates = _quality_gates(dataset, analysis)
        candidates = _build_candidates(dataset, analysis, gates)
        recommendation_eligible = any(
            candidate["recommendation_eligible"] for candidate in candidates
        )
        _clear_recovery_markers(staging, run_id)
        workflow = {
            "schema_version": "0.1",
            "artifact_type": "qubit_spectroscopy_scan",
            "artifact_version": SCAN_ARTIFACT_VERSION,
            "workflow_id": SPECTROSCOPY_SCAN_WORKFLOW_ID,
            "run_id": run_id,
            "recommendation_id": recommendation_id,
            "created_utc": created_utc,
            "status": "completed",
            "claim": {
                "evidence_class": "model_calibration_simulation",
                "physics_claim": "model_derived_only",
                "hardware_measurement": False,
                "execution_profile": execution_profile.value,
                "recommendation_eligible": recommendation_eligible,
            },
            "parent_configuration": {
                "path": parent_path.relative_to(root).as_posix(),
                "sha256": parent_sha256,
                "snapshot_id": context.platform_snapshot_id,
            },
            "request": _request_payload(request),
            "input_authority": {
                "capability_adapter_sha256": dataset.capability_adapter.authority_sha256,
                "reference_frequencies_GHz": {
                    target: dataset.capability_adapter.resolve(target).reference_frequency_GHz
                    for target in request.targets
                },
            },
            "dataset": {
                "path": "dataset.json",
                "sha256": dataset_sha256,
            },
            "runtime_batch": _runtime_batch_payload(dataset.runtime_batch),
            "analysis": analysis.to_dict(),
            "gates": gates,
            "recommendation_eligible": recommendation_eligible,
            "candidates": candidates,
            "archive_eligible": True,
        }
        workflow_sha256 = write_canonical_new(staging / "workflow.json", workflow)
        _, receipt_sha256 = write_completed_evidence(
            staging,
            run_id=run_id,
            recommendation_id=recommendation_id,
            workflow_id=SPECTROSCOPY_SCAN_WORKFLOW_ID,
            workflow_sha256=workflow_sha256,
            dataset_sha256=dataset_sha256,
            parent_configuration_sha256=parent_sha256,
            recommendation_eligible=recommendation_eligible,
        )
        verify_qubit_spectroscopy_scan(staging)
        try:
            publication_attempted = True
            publish_calibration_directory(staging, target)
        except Exception as exc:
            raise SpectroscopyRunError(
                "spectroscopy publication failed; recovery is required for the preserved staging directory"
            ) from exc
        published_run = SpectroscopyRun(
            root=target,
            run_id=run_id,
            recommendation_id=recommendation_id,
            dataset=_relocate_dataset(dataset, staging, target),
            analysis=analysis,
            recommendation_eligible=recommendation_eligible,
            candidates=MappingProxyType(
                {
                    candidate["target"]: MappingProxyType(candidate)
                    for candidate in candidates
                }
            ),
            gates=tuple(MappingProxyType(gate) for gate in gates),
            workflow_sha256=workflow_sha256,
            receipt_sha256=receipt_sha256,
        )
        _enqueue_web_index_best_effort(root, target)
        return published_run
    except BaseException as exc:
        if staging.exists():
            reason = _recovery_reason(exc, publication_attempted)
            _record_recovery_required(
                exc,
                staging=staging,
                root=root,
                run_id=run_id,
                recommendation_id=recommendation_id,
                reason=reason,
                created_utc=created_utc,
            )
        elif publication_attempted and os.path.lexists(target):
            try:
                record = write_uncertain_publication(
                    target.parent,
                    run_id=run_id,
                    recommendation_id=recommendation_id,
                    target_directory_name=target.name,
                    target_relative_path=target.relative_to(root).as_posix(),
                    failure=exc,
                    created_utc=created_utc,
                )
            except Exception as record_error:
                detail = _publication_recording_failure_detail(
                    target, root, exc, record_error
                )
                if _preserves_cancellation_semantics(exc):
                    _add_exception_note(exc, detail)
                    raise exc
                raise SpectroscopyRunError(detail) from record_error
            else:
                if _preserves_cancellation_semantics(exc):
                    raise
                raise SpectroscopyRunError(
                    "spectroscopy publication outcome is uncertain; "
                    f"inspect {record.name} before any lifecycle action"
                ) from exc
        raise


def verify_qubit_spectroscopy_scan(run_root: str | Path) -> bool:
    """Verify one published single-scan spectroscopy artifact."""

    directory = Path(run_root).absolute()
    workflow = _load_canonical(directory / "workflow.json", "workflow")
    receipt = _load_canonical(directory / "receipt.json", "receipt")
    dataset = _load_dataset(directory / "dataset.json")
    workflow_sha256 = _raw_sha256(directory / "workflow.json")
    dataset_sha256 = _raw_sha256(directory / "dataset.json")
    run_id = workflow.get("run_id")
    request = workflow.get("request")
    dataset_binding = workflow.get("dataset")
    analysis = workflow.get("analysis")
    parent = workflow.get("parent_configuration")
    artifact_version = workflow.get("artifact_version")
    if (
        workflow.get("workflow_id") != SPECTROSCOPY_SCAN_WORKFLOW_ID
        or workflow.get("artifact_type") != "qubit_spectroscopy_scan"
        or workflow.get("status") != "completed"
        or not isinstance(run_id, str)
        or not run_id
        or artifact_version not in {"0.1", "0.2", SCAN_ARTIFACT_VERSION}
    ):
        raise SpectroscopyRunError("spectroscopy workflow identity is invalid")
    if not isinstance(request, Mapping) or request.get("run_phase") != "scan":
        raise SpectroscopyRunError("spectroscopy request binding is invalid")
    if (
        dataset.get("run_phase") != "scan"
        or dataset.get("targets") != request.get("targets")
        or dataset.get("execution_mode") != request.get("execution_mode")
    ):
        raise SpectroscopyRunError("spectroscopy dataset request binding is invalid")
    _verify_dataset_coordinates(request, dataset)
    if (
        not isinstance(dataset_binding, Mapping)
        or dataset_binding.get("path") != "dataset.json"
        or dataset_binding.get("sha256") != dataset_sha256
        or not isinstance(analysis, Mapping)
        or analysis.get("dataset_sha256") != dataset_sha256
        or analysis.get("recommendation_eligible") is not False
    ):
        raise SpectroscopyRunError("spectroscopy dataset or analysis binding is invalid")
    if (
        receipt.get("artifact_type") != "qubit_spectroscopy_scan_receipt"
        or receipt.get("status") != "completed"
        or receipt.get("run_id") != run_id
        or receipt.get("workflow_sha256") != workflow_sha256
        or receipt.get("dataset_sha256") != dataset_sha256
        or not isinstance(parent, Mapping)
        or receipt.get("parent_configuration_sha256") != parent.get("sha256")
    ):
        raise SpectroscopyRunError("spectroscopy receipt binding is invalid")
    if artifact_version == "0.1":
        if (
            workflow.get("recommendation_eligible") is not False
            or workflow.get("candidates") != []
            or receipt.get("recommendation_eligible") is not False
        ):
            raise SpectroscopyRunError("legacy spectroscopy recommendation is invalid")
    else:
        _verify_recommendation(workflow, dataset, dataset_sha256)
        if (
            receipt.get("recommendation_id") != workflow.get("recommendation_id")
            or receipt.get("recommendation_eligible")
            is not workflow.get("recommendation_eligible")
        ):
            raise SpectroscopyRunError("spectroscopy recommendation receipt is invalid")
    if artifact_version == SCAN_ARTIFACT_VERSION:
        if workflow.get("archive_eligible") is not True:
            raise SpectroscopyRunError("spectroscopy archive eligibility is invalid")
        try:
            verify_completed_evidence(
                directory,
                workflow=workflow,
                receipt=receipt,
                workflow_sha256=workflow_sha256,
                dataset_sha256=dataset_sha256,
            )
        except SpectroscopyEvidenceError as exc:
            raise SpectroscopyRunError(str(exc)) from exc
    elif receipt.get("archive_eligible", False) is not False:
        raise SpectroscopyRunError("legacy spectroscopy archive eligibility is invalid")
    if not (directory / "execution").is_dir():
        raise SpectroscopyRunError("spectroscopy execution evidence is missing")
    runtime_batch = workflow.get("runtime_batch")
    if artifact_version == SCAN_ARTIFACT_VERSION and runtime_batch is not None:
        if not isinstance(runtime_batch, Mapping):
            raise SpectroscopyRunError("spectroscopy runtime batch binding is invalid")
        try:
            batch = verify_circuit_batch(
                directory / "execution",
                expected_batch_id=run_id,
            )
        except Exception as exc:
            raise SpectroscopyRunError("spectroscopy runtime batch is invalid") from exc
        if runtime_batch != _runtime_batch_payload(batch):
            raise SpectroscopyRunError("spectroscopy runtime batch binding differs")
    return True


def _runtime_batch_payload(batch) -> dict[str, Any]:
    return {
        "batch_id": batch.batch_id,
        "request_sha256": batch.request_sha256,
        "head_sha256": batch.head_sha256,
        "attempt_count": batch.attempt_count,
        "point_count": len(batch.results),
    }


def _operation_id(value: str | None) -> str:
    identifier = value or str(uuid.uuid4())
    try:
        parsed = uuid.UUID(identifier)
    except (ValueError, AttributeError) as exc:
        raise SpectroscopyRunError("operation_id must be a canonical UUID4") from exc
    if parsed.version != 4 or str(parsed) != identifier:
        raise SpectroscopyRunError("operation_id must be a canonical UUID4")
    return identifier


def _reopen_published_scan(
    target: Path,
    request: SpectroscopyRequest,
    context: CircuitExecutionContext,
    parent_sha256: str,
    repository_root: Path,
    coordinator: Path,
    timeout_s: float,
    batch_deadline_s: float,
    execution_profile: CircuitExecutionProfile,
    cancellation_token: CancellationToken | None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
) -> SpectroscopyRun:
    verify_qubit_spectroscopy_scan(target)
    workflow = _load_canonical(target / "workflow.json", "workflow")
    parent = workflow.get("parent_configuration")
    if not isinstance(parent, Mapping) or parent.get("sha256") != parent_sha256:
        raise SpectroscopyRunError("published spectroscopy parent configuration differs")
    run_id = _operation_id(workflow.get("run_id"))
    dataset = run_qubit_spectroscopy(
        request,
        context,
        target / "execution",
        repository_root,
        timeout_s=timeout_s,
        batch_deadline_s=batch_deadline_s,
        batch_id=run_id,
        coordinator_root=coordinator,
        execution_profile=execution_profile,
        cancellation_token=cancellation_token,
        progress_callback=progress_callback,
    )
    stored_dataset = _load_dataset(target / "dataset.json")
    analysis = analyze_qubit_spectroscopy(
        dataset,
        min_contrast=0.01,
        quadratic_refinement=True,
    )
    if dataset.to_dict() != stored_dataset or analysis.to_dict() != workflow.get("analysis"):
        raise SpectroscopyRunError("published spectroscopy semantic replay differs")
    if dataset.runtime_batch is None or workflow.get("runtime_batch") != _runtime_batch_payload(
        dataset.runtime_batch
    ):
        raise SpectroscopyRunError("published spectroscopy runtime batch differs")
    candidates = workflow.get("candidates")
    gates = workflow.get("gates")
    if not isinstance(candidates, list) or not isinstance(gates, list):
        raise SpectroscopyRunError("published spectroscopy result payload is invalid")
    receipt_sha256 = _raw_sha256(target / "receipt.json")
    return SpectroscopyRun(
        root=target,
        run_id=run_id,
        recommendation_id=str(workflow["recommendation_id"]),
        dataset=dataset,
        analysis=analysis,
        recommendation_eligible=workflow.get("recommendation_eligible") is True,
        candidates=MappingProxyType(
            {
                candidate["target"]: MappingProxyType(dict(candidate))
                for candidate in candidates
            }
        ),
        gates=tuple(MappingProxyType(dict(gate)) for gate in gates),
        workflow_sha256=_raw_sha256(target / "workflow.json"),
        receipt_sha256=receipt_sha256,
    )


def _clear_recovery_markers(staging: Path, run_id: str) -> None:
    (staging / "recovery.json").unlink(missing_ok=True)
    (staging.parent / f".spectroscopy-recovery-{run_id}.marker").unlink(missing_ok=True)


def _request_payload(request: SpectroscopyRequest) -> dict[str, Any]:
    return {
        "execution_mode": str(request.execution_mode),
        "run_phase": request.run_phase,
        "targets": list(request.targets),
        "axes": [
            {"qagent": axis.qagent, "frequencies_GHz": list(axis.frequencies_GHz)}
            for axis in request.axes
        ],
        "pulse_policies": [policy.to_dict() for policy in request.pulse_policies],
        "max_points": request.max_points,
    }


def _quality_gates(
    dataset: SpectroscopyDataset,
    analysis: SpectroscopyAnalysis,
) -> list[dict[str, Any]]:
    max_leakage = max(point.circuit_result.leakage for point in dataset.points)
    max_norm_error = max(point.circuit_result.norm_error for point in dataset.points)
    gates = [
        {
            "gate_id": f"{target}.peak_quality",
            "passed": analysis.peaks[target].valid,
            "details": {
                "contrast": analysis.peaks[target].contrast,
                "reason": analysis.peaks[target].reason,
            },
        }
        for target in dataset.request.targets
    ]
    gates.extend(
        (
            {
                "gate_id": "batch.max_leakage",
                "passed": max_leakage <= 0.05,
                "details": {"observed": max_leakage, "limit": 0.05},
            },
            {
                "gate_id": "batch.max_norm_error",
                "passed": max_norm_error <= 1.0e-8,
                "details": {"observed": max_norm_error, "limit": 1.0e-8},
            },
        )
    )
    return gates


def _build_candidates(
    dataset: SpectroscopyDataset,
    analysis: SpectroscopyAnalysis,
    gates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gate_map = {gate["gate_id"]: gate["passed"] for gate in gates}
    batch_eligible = gate_map["batch.max_leakage"] and gate_map["batch.max_norm_error"]
    candidates = []
    for target in dataset.request.targets:
        peak = analysis.peaks[target]
        current = dataset.capability_adapter.resolve(target).reference_frequency_GHz
        proposed = peak.estimated_frequency_GHz if peak.valid else None
        eligible = bool(batch_eligible and gate_map[f"{target}.peak_quality"])
        candidate = calibration_candidate(
            f"{target}.reference_frequency_GHz",
            target,
            [
                parameter_change(
                    (
                        "calibration_values.qagents."
                        f"{target}.reference_frequency_authority.reference_frequency_GHz"
                    ),
                    current,
                    proposed,
                    unit="GHz",
                )
            ],
            recommendation_eligible=eligible,
            candidate_type="qubit_reference_frequency",
            source_dataset_sha256s=[dataset.dataset_sha256],
            quality_metrics={"contrast": peak.contrast},
            reason=None if eligible else peak.reason or "batch_quality_gate_failed",
        )
        candidate.update(
            {
                "current_frequency_GHz": current,
                "proposed_frequency_GHz": proposed,
                "delta_GHz": proposed - current if proposed is not None else None,
                "fit_method": "bounded_quadratic_peak",
                "contrast": peak.contrast,
                "simulation_only": True,
            }
        )
        candidates.append(candidate)
    return candidates


def _verify_recommendation(
    workflow: Mapping[str, Any],
    dataset: Mapping[str, Any],
    dataset_sha256: str,
) -> None:
    request = workflow.get("request")
    analysis = workflow.get("analysis")
    authority = workflow.get("input_authority")
    gates = workflow.get("gates")
    candidates = workflow.get("candidates")
    if (
        not isinstance(request, Mapping)
        or not isinstance(analysis, Mapping)
        or not isinstance(authority, Mapping)
        or not isinstance(gates, list)
        or not isinstance(candidates, list)
    ):
        raise SpectroscopyRunError("spectroscopy recommendation payload is invalid")
    targets = request.get("targets")
    peaks = analysis.get("peaks")
    references = authority.get("reference_frequencies_GHz")
    if (
        not isinstance(targets, list)
        or not isinstance(peaks, Mapping)
        or not isinstance(references, Mapping)
        or authority.get("capability_adapter_sha256")
        != dataset.get("capability_adapter_sha256")
    ):
        raise SpectroscopyRunError("spectroscopy recommendation authority is invalid")
    gate_map = {
        gate.get("gate_id"): gate
        for gate in gates
        if isinstance(gate, Mapping) and isinstance(gate.get("gate_id"), str)
    }
    expected_gate_ids = {
        *(f"{target}.peak_quality" for target in targets),
        "batch.max_leakage",
        "batch.max_norm_error",
    }
    if set(gate_map) != expected_gate_ids:
        raise SpectroscopyRunError("spectroscopy recommendation gates are invalid")
    points = dataset.get("points")
    if not isinstance(points, list) or not points:
        raise SpectroscopyRunError("spectroscopy recommendation dataset is invalid")
    observed_leakage = max(point.get("leakage") for point in points)
    observed_norm_error = max(point.get("norm_error") for point in points)
    expected_batch = {
        "batch.max_leakage": (observed_leakage, 0.05),
        "batch.max_norm_error": (observed_norm_error, 1.0e-8),
    }
    for gate_id, (observed, limit) in expected_batch.items():
        gate = gate_map[gate_id]
        details = gate.get("details")
        if (
            not isinstance(details, Mapping)
            or details.get("observed") != observed
            or details.get("limit") != limit
            or gate.get("passed") is not (observed <= limit)
        ):
            raise SpectroscopyRunError("spectroscopy batch quality gate is invalid")
    candidate_map = {
        candidate.get("target"): candidate
        for candidate in candidates
        if isinstance(candidate, Mapping)
    }
    if set(candidate_map) != set(targets):
        raise SpectroscopyRunError("spectroscopy candidate targets are invalid")
    batch_passed = all(gate_map[gate_id]["passed"] for gate_id in expected_batch)
    for target in targets:
        peak = peaks.get(target)
        gate = gate_map[f"{target}.peak_quality"]
        candidate = candidate_map[target]
        if not isinstance(peak, Mapping):
            raise SpectroscopyRunError("spectroscopy peak payload is invalid")
        peak_valid = peak.get("valid") is True
        proposed = peak.get("estimated_frequency_GHz") if peak_valid else None
        current = references.get(target)
        eligible = bool(peak_valid and batch_passed)
        try:
            normalized_candidate = normalize_calibration_candidate(candidate)
        except CalibrationCandidateProtocolError as exc:
            raise SpectroscopyRunError(
                "spectroscopy candidate protocol is invalid"
            ) from exc
        expected_path = (
            "calibration_values.qagents."
            f"{target}.reference_frequency_authority.reference_frequency_GHz"
        )
        expected_resource = {
            "owner": target,
            "resource_type": "qagent_calibration",
            "resource_id": target,
        }
        changes = normalized_candidate["changes"]
        if (
            gate.get("passed") is not peak_valid
            or candidate.get("current_frequency_GHz") != current
            or candidate.get("proposed_frequency_GHz") != proposed
            or candidate.get("delta_GHz")
            != (proposed - current if proposed is not None else None)
            or candidate.get("source_dataset_sha256s") != [dataset_sha256]
            or candidate.get("recommendation_eligible") is not eligible
            or len(changes) != 1
            or changes[0]["parameter_path"] != expected_path
            or changes[0]["current_value"] != current
            or changes[0]["proposed_value"] != proposed
            or changes[0]["unit"] != "GHz"
            or changes[0]["configuration_resource"] != expected_resource
            or normalized_candidate["calibration_subjects"] != [target]
            or normalized_candidate["configuration_resources"] != [expected_resource]
            or (
                "candidate_id" in candidate
                and candidate.get("candidate_id") != f"{target}.reference_frequency_GHz"
            )
        ):
            raise SpectroscopyRunError("spectroscopy candidate binding is invalid")
    if workflow.get("recommendation_eligible") is not any(
        candidate["recommendation_eligible"] for candidate in candidates
    ):
        raise SpectroscopyRunError("spectroscopy recommendation eligibility is invalid")


def _verify_dataset_coordinates(
    request: Mapping[str, Any],
    dataset: Mapping[str, Any],
) -> None:
    targets = request.get("targets")
    axes = request.get("axes")
    points = dataset.get("points")
    if not isinstance(targets, list) or not isinstance(axes, list) or not isinstance(points, list):
        raise SpectroscopyRunError("spectroscopy request axes are invalid")
    axis_map = {
        row.get("qagent"): row.get("frequencies_GHz")
        for row in axes
        if isinstance(row, Mapping)
    }
    if set(axis_map) != set(targets) or any(
        not isinstance(axis_map[target], list) for target in targets
    ):
        raise SpectroscopyRunError("spectroscopy request axis targets are invalid")
    lengths = {len(axis_map[target]) for target in targets}
    if len(lengths) != 1 or lengths != {len(points)}:
        raise SpectroscopyRunError("spectroscopy dataset point count is invalid")
    for index, point in enumerate(points):
        point_row = point.get("point") if isinstance(point, Mapping) else None
        coordinates = (
            point_row.get("coordinates_GHz")
            if isinstance(point_row, Mapping)
            else None
        )
        if (
            not isinstance(coordinates, Mapping)
            or point_row.get("point_index") != index
            or set(coordinates) != set(targets)
            or any(coordinates[target] != axis_map[target][index] for target in targets)
        ):
            raise SpectroscopyRunError("spectroscopy dataset coordinates are invalid")


def _relocate_dataset(
    dataset: SpectroscopyDataset,
    source_root: Path,
    target_root: Path,
) -> SpectroscopyDataset:
    points = []
    for point in dataset.points:
        result = point.circuit_result
        relocated = replace(
            result,
            evidence_root=_relocate_path(result.evidence_root, source_root, target_root),
            model_evidence_root=_relocate_path(
                result.model_evidence_root,
                source_root,
                target_root,
            ),
        )
        points.append(replace(point, circuit_result=relocated))
    batch = dataset.runtime_batch
    relocated_batch = (
        replace(
            batch,
            root=_relocate_path(batch.root, source_root, target_root),
            results=tuple(point.circuit_result for point in points),
        )
        if batch is not None
        else None
    )
    return replace(dataset, points=tuple(points), runtime_batch=relocated_batch)


def _relocate_path(path: Path, source_root: Path, target_root: Path) -> Path:
    return target_root / path.resolve().relative_to(source_root.resolve())


def _write_dataset(path: Path, payload: Mapping[str, Any]) -> str:
    raw = dataset_json_bytes(payload)
    with path.open("xb") as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest().upper()


def _load_dataset(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SpectroscopyRunError(f"cannot load spectroscopy dataset: {exc}") from exc
    if not isinstance(payload, dict) or raw != dataset_json_bytes(payload):
        raise SpectroscopyRunError("spectroscopy dataset is not canonical JSON")
    _finite_tree(payload)
    return payload


def _load_canonical(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SpectroscopyRunError(f"cannot load spectroscopy {label}: {exc}") from exc
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise SpectroscopyRunError(f"spectroscopy {label} is not canonical JSON")
    _finite_tree(payload)
    return payload


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite_tree(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SpectroscopyRunError("non-finite spectroscopy value")
        return
    if isinstance(value, list):
        for item in value:
            _finite_tree(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite_tree(item)
        return
    raise SpectroscopyRunError("unsupported spectroscopy value")


def _repository_root(value: str | Path | None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]


def _enqueue_web_index_best_effort(root: Path, target: Path) -> None:
    """Keep derived Web indexing outside the immutable publication boundary."""

    try:
        from sqvm.web.registrar import enqueue_published_run_best_effort

        enqueue_published_run_best_effort(
            root,
            target,
            storage_root=_web_storage_root(target),
        )
    except Exception as exc:
        try:
            warnings.warn(
                "published spectroscopy run could not be queued for Web indexing "
                f"({type(exc).__name__}: {exc})",
                RuntimeWarning,
                stacklevel=2,
            )
        except Exception:
            pass


def _web_storage_root(target: Path) -> Path:
    collection = target.parent
    return (
        collection.parent / "experiment-storage"
        if collection.name == "experiments"
        else collection / "experiment-storage"
    )


def _inside(value: str | Path, root: Path, label: str) -> Path:
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise SpectroscopyRunError(f"{label} is outside repository") from exc
    return path


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _recovery_reason(exc: BaseException, publication_attempted: bool) -> str:
    if publication_attempted:
        return "publication_failure"
    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
        return "interrupted"
    if isinstance(exc, CircuitBatchCancelledError) or exc.__class__.__name__ == "CancelledError":
        return "cancelled"
    return "failed"


def _preserves_cancellation_semantics(exc: BaseException) -> bool:
    return isinstance(exc, (KeyboardInterrupt, SystemExit)) or exc.__class__.__name__ == "CancelledError"


def _record_recovery_required(
    failure: BaseException,
    *,
    staging: Path,
    root: Path,
    run_id: str,
    recommendation_id: str,
    reason: str,
    created_utc: str,
) -> None:
    try:
        write_recovery_required(
            staging,
            run_id=run_id,
            recommendation_id=recommendation_id,
            reason=reason,
            failure=failure,
            created_utc=created_utc,
        )
        return
    except Exception as primary_error:
        try:
            marker = write_recovery_fallback_marker(
                staging,
                run_id=run_id,
                recommendation_id=recommendation_id,
                reason=reason,
                failure=failure,
                primary_failure=primary_error,
                created_utc=created_utc,
            )
        except Exception as marker_error:
            detail = _recovery_recording_failure_detail(
                staging, root, failure, primary_error, marker_error
            )
            if _preserves_cancellation_semantics(failure):
                _add_exception_note(failure, detail)
                return
            raise SpectroscopyRunError(detail) from marker_error
        _add_exception_note(
            failure,
            f"primary recovery record failed ({primary_error}); fallback marker: {marker.name}",
        )


def _recovery_recording_failure_detail(
    staging: Path,
    root: Path,
    failure: BaseException,
    primary_error: Exception,
    marker_error: Exception,
) -> str:
    absolute = staging.absolute()
    try:
        relative = absolute.relative_to(root.absolute()).as_posix()
    except ValueError:
        relative = "outside_repository"
    return (
        "spectroscopy recovery recording failed; "
        f"staging_absolute={absolute}; staging_relative={relative}; "
        f"original={failure.__class__.__name__}: {failure}; "
        f"primary={primary_error.__class__.__name__}: {primary_error}; "
        f"fallback={marker_error.__class__.__name__}: {marker_error}"
    )


def _publication_recording_failure_detail(
    target: Path,
    root: Path,
    failure: BaseException,
    record_error: Exception,
) -> str:
    absolute = target.absolute()
    try:
        relative = absolute.relative_to(root.absolute()).as_posix()
    except ValueError:
        relative = "outside_repository"
    return (
        "spectroscopy publication outcome is uncertain and its audit record failed; "
        f"target_absolute={absolute}; target_relative={relative}; "
        f"original={failure.__class__.__name__}: {failure}; "
        f"audit={record_error.__class__.__name__}: {record_error}"
    )


def _add_exception_note(exc: BaseException, note: str) -> None:
    add_note = getattr(exc, "add_note", None)
    if callable(add_note):
        add_note(note)


__all__ = [
    "SPECTROSCOPY_SCAN_WORKFLOW_ID",
    "SpectroscopyRun",
    "SpectroscopyRunError",
    "run_qubit_spectroscopy_scan",
    "verify_qubit_spectroscopy_scan",
]
