"""Stable Python entry points for calibration experiments."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import uuid
from typing import Any, Sequence

from sqvm.candidate_protocol import (
    CalibrationCandidateProtocolError,
    normalize_calibration_candidate,
)
from sqvm.circuits import CircuitExecutionContext, CircuitExecutionProfile
from sqvm.runtime.batch import request_circuit_batch_cancellation
from sqvm.runtime.lifecycle import CancellationToken
from sqvm.calibration.spectroscopy import (
    SpectroscopyAxis,
    SpectroscopyMode,
    SpectroscopyPulsePolicy,
    SpectroscopyRequest,
    build_qubit_capability_adapter,
)
from sqvm.calibration.spectroscopy_workflow import (
    SpectroscopyCalibrationRequest,
    SpectroscopyCalibrationRun,
    run_qubit_spectroscopy_calibration,
    verify_qubit_spectroscopy_calibration,
)
from sqvm.calibration.spectroscopy_run import (
    SpectroscopyRun,
    run_qubit_spectroscopy_scan,
    verify_qubit_spectroscopy_scan,
)
from sqvm.calibration.rabi import (
    RABI_SCAN_WORKFLOW_ID,
    RabiRun,
    RabiRequest,
    amplitude_axis,
    run_qubit_rabi_scan,
    verify_rabi_scan,
)
from sqvm.web.configuration import PlatformConfigurationStore


class CalibrationExperimentError(ValueError):
    """Raised when an experiment cannot be bound to an Active configuration."""


@dataclass(frozen=True, slots=True)
class SpectroscopyParameterUpdate:
    """Receipt for spectroscopy candidates applied to the current configuration."""

    device_id: str
    run_id: str
    targets: tuple[str, ...]
    candidate_values_GHz: Mapping[str, float]
    current_revision: int
    current_content_sha256: str
    requires_requalification: bool


@dataclass(frozen=True, slots=True)
class CalibrationCandidateUpdate:
    """Receipt for experiment-independent candidates applied atomically."""

    device_id: str
    run_id: str
    candidate_ids: tuple[str, ...]
    calibration_subjects: tuple[str, ...]
    configuration_targets: tuple[str, ...]
    targets: tuple[str, ...]
    applied_values: Mapping[str, Any]
    current_revision: int
    current_content_sha256: str
    requires_requalification: bool


@dataclass(frozen=True, slots=True)
class _ActiveCalibrationConfiguration:
    root: Path
    context: CircuitExecutionContext
    parent_path: Path


def run_spectroscopy(
    frequency_ranges_GHz: Mapping[str, Sequence[float]],
    *,
    frequency_step_GHz: float = 0.01,
    pulse_length_samples: int = 32,
    pulse_amplitude_GHz: float = 0.02,
    pulse_r_sigma_samples: float = 8.0,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    timeout_s: float = 600.0,
    batch_deadline_s: float = 3600.0,
    operation_id: str | None = None,
    cancellation_token: CancellationToken | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> SpectroscopyRun:
    """Run one qubit-spectroscopy experiment from ranges and a shared step.

    ``frequency_ranges_GHz`` maps each target QAgent to ``(start, stop)``.
    One target runs alone; two targets run in lockstep and therefore require
    the same number of frequency points. The range includes both endpoints.
    Pulse settings and the per-worker watchdog have production defaults but
    remain overridable for hardware-specific experiments. Exactly one dataset
    is executed; repeated or narrower scans belong to the caller's calibration
    procedure.
    """

    axes_by_target = _spectroscopy_axes(frequency_ranges_GHz, frequency_step_GHz)
    binding = _active_configuration(
        device_id=device_id,
        configuration_storage_root=configuration_storage_root,
        repository_root=repository_root,
    )
    adapter = build_qubit_capability_adapter(binding.context)
    unsupported = set(axes_by_target).difference(adapter.capabilities)
    if unsupported:
        names = ", ".join(sorted(unsupported))
        raise CalibrationExperimentError(f"unsupported spectroscopy target(s): {names}")
    targets = tuple(
        sorted(
            axes_by_target,
            key=lambda target: adapter.capabilities[target].canonical_order_index,
        )
    )
    point_counts = {len(axes_by_target[target]) for target in targets}
    if len(point_counts) != 1:
        raise CalibrationExperimentError(
            "parallel spectroscopy ranges must produce the same number of points"
        )
    mode = SpectroscopyMode.SINGLE if len(targets) == 1 else SpectroscopyMode.PARALLEL_LOCKSTEP
    request = SpectroscopyRequest(
        execution_mode=mode,
        run_phase="scan",
        targets=targets,
        axes=tuple(
            SpectroscopyAxis(target, axes_by_target[target]) for target in targets
        ),
        pulse_policies=tuple(
            SpectroscopyPulsePolicy(
                qagent=target,
                length_samples=pulse_length_samples,
                amplitude_GHz=pulse_amplitude_GHz,
                r_sigma_samples=pulse_r_sigma_samples,
            )
            for target in targets
        ),
    )
    collection = _inside_repository(
        output_root
        if output_root is not None
        else binding.root / "output" / "experiments",
        binding.root,
        "experiment output root",
    )
    identifier = _canonical_operation_id(operation_id)
    target = collection / f"qubit_spectroscopy_{identifier.replace('-', '')}"
    return run_qubit_spectroscopy_scan(
        request,
        binding.context,
        binding.parent_path,
        target,
        binding.root,
        timeout_s=timeout_s,
        batch_deadline_s=batch_deadline_s,
        operation_id=identifier,
        execution_profile=CircuitExecutionProfile.CALIBRATION_SCAN,
        cancellation_token=cancellation_token,
        progress_callback=progress_callback,
    )


def cancel_spectroscopy(
    operation_id: str,
    *,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> Path:
    """Request cooperative cancellation before the next spectroscopy point."""

    identifier = _canonical_operation_id(operation_id)
    binding = _active_configuration(
        device_id=device_id,
        configuration_storage_root=configuration_storage_root,
        repository_root=repository_root,
    )
    collection = _inside_repository(
        output_root
        if output_root is not None
        else binding.root / "output" / "experiments",
        binding.root,
        "experiment output root",
    )
    return request_circuit_batch_cancellation(
        collection / ".runtime-v03",
        identifier,
        binding.root,
    )


def run_rabi(
    target: str,
    amplitude_range_GHz: Sequence[float],
    amplitude_step_GHz: float,
    *,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    timeout_s: float = 600.0,
    batch_deadline_s: float = 3600.0,
    operation_id: str | None = None,
    cancellation_token: CancellationToken | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> RabiRun:
    """Run one first-lobe X2P + X2P amplitude calibration scan.

    The amplitude range and step are mandatory because their physical scale is
    device-specific.  Each point contains exactly one SET and two X2P lines.
    """
    binding = _active_configuration(
        device_id=device_id, configuration_storage_root=configuration_storage_root,
        repository_root=repository_root,
    )
    axis = amplitude_axis(amplitude_range_GHz, amplitude_step_GHz, target)
    collection = _inside_repository(
        output_root if output_root is not None else binding.root / "output" / "experiments",
        binding.root, "experiment output root",
    )
    identifier = _canonical_operation_id(operation_id)
    return run_qubit_rabi_scan(
        RabiRequest(target, axis), binding.context, binding.parent_path,
        collection / f"qubit_rabi_{identifier.replace('-', '')}", binding.root,
        timeout_s=timeout_s, batch_deadline_s=batch_deadline_s, operation_id=identifier,
        execution_profile=CircuitExecutionProfile.CALIBRATION_SCAN,
        cancellation_token=cancellation_token, progress_callback=progress_callback,
    )


def cancel_rabi(
    operation_id: str,
    *,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
) -> Path:
    """Request cooperative cancellation before the next Rabi point."""
    identifier = _canonical_operation_id(operation_id)
    binding = _active_configuration(
        device_id=device_id, configuration_storage_root=configuration_storage_root,
        repository_root=repository_root,
    )
    collection = _inside_repository(
        output_root if output_root is not None else binding.root / "output" / "experiments",
        binding.root, "experiment output root",
    )
    return request_circuit_batch_cancellation(collection / ".runtime-v03", identifier, binding.root)


def run_active_qubit_spectroscopy_calibration(
    request: SpectroscopyCalibrationRequest,
    *,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    timeout_s: float = 600.0,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> SpectroscopyCalibrationRun:
    """Run spectroscopy from one immutable Active platform configuration.

    ``output_root`` is the experiment collection directory. One unique run
    directory is created below it, so callers do not need to allocate run IDs.
    The default is ``output/experiments`` and is discovered automatically by
    the calibration Web console. ``timeout_s`` is the watchdog for each
    isolated QuTiP worker, not a deadline for the complete scan.
    """

    binding = _active_configuration(
        device_id=device_id,
        configuration_storage_root=configuration_storage_root,
        repository_root=repository_root,
    )
    return _run_active_spectroscopy_request(
        request,
        binding=binding,
        output_root=output_root,
        timeout_s=timeout_s,
        progress_callback=progress_callback,
    )


def _active_configuration(
    *,
    device_id: str,
    configuration_storage_root: str | Path | None,
    repository_root: str | Path | None,
) -> _ActiveCalibrationConfiguration:
    root = _repository_root(repository_root)
    store = PlatformConfigurationStore(root, configuration_storage_root)
    active = [
        row
        for row in store.active_configurations()
        if row.get("device_id") == device_id
    ]
    if len(active) != 1:
        raise CalibrationExperimentError(
            f"device {device_id!r} must have exactly one Active configuration"
        )
    snapshot_id = active[0].get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise CalibrationExperimentError("Active configuration has no snapshot identity")
    snapshot = store.snapshot(snapshot_id)
    if snapshot.get("experiment_eligible") is not True:
        raise CalibrationExperimentError("Active configuration is not experiment eligible")

    context = store.resolve_active_context(device_id)
    if context.platform_snapshot_id != snapshot_id:
        raise CalibrationExperimentError("resolved context does not match the Active snapshot")
    return _ActiveCalibrationConfiguration(
        root=root,
        context=context,
        parent_path=store.snapshots_root / snapshot_id / "snapshot.json",
    )


def _run_active_spectroscopy_request(
    request: SpectroscopyCalibrationRequest,
    *,
    binding: _ActiveCalibrationConfiguration,
    output_root: str | Path | None,
    timeout_s: float,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
) -> SpectroscopyCalibrationRun:
    root = binding.root

    collection = _inside_repository(
        output_root if output_root is not None else root / "output" / "experiments",
        root,
        "experiment output root",
    )
    target = collection / f"qubit_spectroscopy_{uuid.uuid4().hex}"
    return run_qubit_spectroscopy_calibration(
        request,
        binding.context,
        binding.parent_path,
        target,
        root,
        timeout_s=timeout_s,
        execution_profile=CircuitExecutionProfile.CALIBRATION_SCAN,
        progress_callback=progress_callback,
    )


def _spectroscopy_axes(
    frequency_ranges_GHz: Mapping[str, Sequence[float]],
    frequency_step_GHz: float,
) -> dict[str, tuple[float, ...]]:
    if not isinstance(frequency_ranges_GHz, Mapping) or not 1 <= len(frequency_ranges_GHz) <= 2:
        raise CalibrationExperimentError(
            "frequency_ranges_GHz must contain one or two target ranges"
        )
    step = _positive_decimal(frequency_step_GHz, "frequency_step_GHz")
    axes: dict[str, tuple[float, ...]] = {}
    for target, bounds in frequency_ranges_GHz.items():
        if not isinstance(target, str) or not target:
            raise CalibrationExperimentError("spectroscopy target names must be nonempty strings")
        if (
            isinstance(bounds, (str, bytes))
            or not isinstance(bounds, Sequence)
            or len(bounds) != 2
        ):
            raise CalibrationExperimentError(
                f"frequency range for {target} must be a (start, stop) pair"
            )
        start = _positive_decimal(bounds[0], f"{target} start frequency")
        stop = _positive_decimal(bounds[1], f"{target} stop frequency")
        if stop <= start:
            raise CalibrationExperimentError(
                f"frequency range for {target} must have stop greater than start"
            )
        intervals = (stop - start) / step
        integral_intervals = intervals.to_integral_value()
        if intervals != integral_intervals:
            raise CalibrationExperimentError(
                f"frequency range for {target} is not exactly divisible by frequency_step_GHz"
            )
        point_count = int(integral_intervals) + 1
        if not 3 <= point_count <= 64:
            raise CalibrationExperimentError(
                f"frequency range for {target} must produce between 3 and 64 points"
            )
        axes[target] = tuple(float(start + step * index) for index in range(point_count))
    return axes


def _positive_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise CalibrationExperimentError(f"{label} must be positive and finite")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CalibrationExperimentError(f"{label} must be positive and finite") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise CalibrationExperimentError(f"{label} must be positive and finite")
    return decimal


def apply_calibration_candidates_to_current_configuration(
    run: Any,
    *,
    confirmation_phrase: str,
    candidate_ids: Sequence[str] | None = None,
    targets: Sequence[str] | None = None,
    device_id: str = "demo_2q1c2r",
    actor_id: str = "notebook.user",
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    expected_current_content_sha256: str | None = None,
    operation_id: str | None = None,
) -> CalibrationCandidateUpdate:
    """Verify and atomically apply eligible experiment candidates.

    This updates the mutable current configuration only. It deliberately does not
    publish or activate a snapshot; those remain explicit configuration lifecycle
    operations.
    """

    run_root_value = getattr(run, "root", None)
    run_id = getattr(run, "run_id", None)
    if run_root_value is None or not isinstance(run_id, str) or not run_id:
        raise CalibrationExperimentError("a calibration candidate run is required")
    root = _repository_root(repository_root)
    run_root = _inside_repository(run_root_value, root, "calibration run root")
    workflow = _verified_candidate_workflow(run_root)
    if workflow.get("claim", {}).get("evidence_class") == "synthetic_demo":
        raise CalibrationExperimentError(
            "synthetic demo candidates cannot update configuration"
        )
    if workflow.get("run_id") != run_id:
        raise CalibrationExperimentError("calibration run identity does not match workflow")
    expected_phrase = f"APPLY CALIBRATION CANDIDATES {run_id}"
    if confirmation_phrase != expected_phrase:
        raise CalibrationExperimentError("candidate update confirmation phrase is invalid")
    candidate_rows = workflow.get("candidates")
    if not isinstance(candidate_rows, list):
        raise CalibrationExperimentError("calibration workflow candidates are invalid")
    try:
        normalized = [normalize_calibration_candidate(row) for row in candidate_rows]
    except CalibrationCandidateProtocolError as exc:
        raise CalibrationExperimentError(str(exc)) from exc
    candidate_map = {row["candidate_id"]: row for row in normalized}
    if len(candidate_map) != len(normalized):
        raise CalibrationExperimentError("calibration candidate_ids are not unique")
    if candidate_ids is not None and targets is not None:
        raise CalibrationExperimentError("select candidates by candidate_ids or targets, not both")
    selected_ids = tuple(
        candidate_ids
        if candidate_ids is not None
        else [
            row["candidate_id"]
            for row in normalized
            if row.get("recommendation_eligible") is True
            and (
                targets is None
                or any(subject in targets for subject in row["calibration_subjects"])
            )
        ]
    )
    if (
        not selected_ids
        or len(selected_ids) != len(set(selected_ids))
        or any(candidate_id not in candidate_map for candidate_id in selected_ids)
    ):
        raise CalibrationExperimentError("candidate update selection is invalid")
    if targets is not None and (
        not targets
        or len(targets) != len(set(targets))
        or any(
            target not in {
                subject
                for row in normalized
                for subject in row["calibration_subjects"]
            }
            for target in targets
        )
    ):
        raise CalibrationExperimentError("candidate update targets are invalid")
    selected_candidates = [candidate_map[candidate_id] for candidate_id in selected_ids]
    if any(row.get("recommendation_eligible") is not True for row in selected_candidates):
        raise CalibrationExperimentError("selected calibration candidate is not eligible")

    store = PlatformConfigurationStore(root, configuration_storage_root)
    current = store.current_configuration(device_id)
    expected_hash = expected_current_content_sha256 or current["content_sha256"]
    try:
        updated = store.apply_candidates_to_current_configuration(
            device_id,
            actor_id=actor_id,
            expected_content_sha256=expected_hash,
            experiment_run_id=run_id,
            recommendation_id=workflow.get("recommendation_id") or run_id,
            candidates=selected_candidates,
            operation_id=operation_id,
        )
    except ValueError as exc:
        raise CalibrationExperimentError(str(exc)) from exc
    calibration_subjects = tuple(
        dict.fromkeys(
            subject
            for row in selected_candidates
            for subject in row["calibration_subjects"]
        )
    )
    configuration_targets = tuple(
        dict.fromkeys(
            resource["owner"]
            for row in selected_candidates
            for resource in row["configuration_resources"]
        )
    )
    return CalibrationCandidateUpdate(
        device_id=device_id,
        run_id=run_id,
        candidate_ids=selected_ids,
        calibration_subjects=calibration_subjects,
        configuration_targets=configuration_targets,
        targets=calibration_subjects,
        applied_values={
            change["parameter_path"]: change["proposed_value"]
            for candidate in selected_candidates
            for change in candidate["changes"]
        },
        current_revision=int(updated["revision"]),
        current_content_sha256=str(updated["content_sha256"]),
        requires_requalification=bool(
            updated.get("validation", {}).get("requires_requalification")
        ),
    )


def apply_spectroscopy_candidates_to_current_configuration(
    run: SpectroscopyCalibrationRun | SpectroscopyRun,
    *,
    confirmation_phrase: str,
    targets: Sequence[str] | None = None,
    device_id: str = "demo_2q1c2r",
    actor_id: str = "notebook.user",
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    expected_current_content_sha256: str | None = None,
    operation_id: str | None = None,
) -> SpectroscopyParameterUpdate:
    """Compatibility wrapper for the former spectroscopy-specific update API."""

    expected_phrase = f"APPLY SPECTROSCOPY CANDIDATES {run.run_id}"
    if confirmation_phrase != expected_phrase:
        raise CalibrationExperimentError("candidate update confirmation phrase is invalid")
    update = apply_calibration_candidates_to_current_configuration(
        run,
        confirmation_phrase=f"APPLY CALIBRATION CANDIDATES {run.run_id}",
        targets=targets,
        device_id=device_id,
        actor_id=actor_id,
        configuration_storage_root=configuration_storage_root,
        repository_root=repository_root,
        expected_current_content_sha256=expected_current_content_sha256,
        operation_id=operation_id,
    )
    values = {
        target: float(value)
        for path, value in update.applied_values.items()
        if (target := _frequency_target(path)) is not None
    }
    return SpectroscopyParameterUpdate(
        device_id=update.device_id,
        run_id=update.run_id,
        targets=update.targets,
        candidate_values_GHz=values,
        current_revision=update.current_revision,
        current_content_sha256=update.current_content_sha256,
        requires_requalification=update.requires_requalification,
    )


def _verified_candidate_workflow(run_root: Path) -> dict[str, Any]:
    try:
        workflow = json.loads((run_root / "workflow.json").read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationExperimentError("cannot read calibration candidate workflow") from exc
    workflow_id = workflow.get("workflow_id")
    if workflow_id == "qubit_spectroscopy_scan_v1":
        verify_qubit_spectroscopy_scan(run_root)
    elif workflow_id == "qubit_spectroscopy_calibration_v1":
        verify_qubit_spectroscopy_calibration(run_root)
    elif workflow_id == RABI_SCAN_WORKFLOW_ID:
        verify_rabi_scan(run_root)
    else:
        raise CalibrationExperimentError(
            f"calibration workflow has no candidate verifier: {workflow_id!r}"
        )
    return workflow


def _frequency_target(parameter_path: str) -> str | None:
    prefix = "calibration_values.qagents."
    suffix = ".reference_frequency_authority.reference_frequency_GHz"
    if not parameter_path.startswith(prefix) or not parameter_path.endswith(suffix):
        return None
    return parameter_path[len(prefix) : -len(suffix)]


def _repository_root(value: str | Path | None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[3]


def _canonical_operation_id(value: str | None) -> str:
    identifier = value or str(uuid.uuid4())
    try:
        parsed = uuid.UUID(identifier)
    except (ValueError, AttributeError) as exc:
        raise CalibrationExperimentError("operation_id must be a canonical UUID4") from exc
    if parsed.version != 4 or str(parsed) != identifier:
        raise CalibrationExperimentError("operation_id must be a canonical UUID4")
    return identifier


def _inside_repository(value: str | Path, root: Path, label: str) -> Path:
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CalibrationExperimentError(f"{label} is outside repository") from exc
    return path


__all__ = [
    "CalibrationCandidateUpdate",
    "CalibrationExperimentError",
    "SpectroscopyRun",
    "RabiRun",
    "SpectroscopyParameterUpdate",
    "apply_calibration_candidates_to_current_configuration",
    "apply_spectroscopy_candidates_to_current_configuration",
    "cancel_spectroscopy",
    "cancel_rabi",
    "run_active_qubit_spectroscopy_calibration",
    "run_spectroscopy",
    "run_rabi",
]
