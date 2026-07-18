"""Stable Python entry points for calibration experiments."""

from __future__ import annotations

from pathlib import Path
import uuid

from sqvm.calibration.spectroscopy_workflow import (
    SpectroscopyCalibrationRequest,
    SpectroscopyCalibrationRun,
    run_qubit_spectroscopy_calibration,
)
from sqvm.web.configuration import PlatformConfigurationStore


class CalibrationExperimentError(ValueError):
    """Raised when an experiment cannot be bound to an Active configuration."""


def run_active_qubit_spectroscopy_calibration(
    request: SpectroscopyCalibrationRequest,
    *,
    device_id: str = "demo_2q1c2r",
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    repository_root: str | Path | None = None,
    timeout_s: float = 180.0,
) -> SpectroscopyCalibrationRun:
    """Run spectroscopy from one immutable Active platform configuration.

    ``output_root`` is the experiment collection directory. One unique run
    directory is created below it, so callers do not need to allocate run IDs.
    The default is ``output/experiments`` and is discovered automatically by
    the calibration Web console.
    """

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
    parent_path = store.snapshots_root / snapshot_id / "snapshot.json"

    collection = _inside_repository(
        output_root if output_root is not None else root / "output" / "experiments",
        root,
        "experiment output root",
    )
    target = collection / f"qubit_spectroscopy_{uuid.uuid4().hex}"
    return run_qubit_spectroscopy_calibration(
        request,
        context,
        parent_path,
        target,
        root,
        timeout_s=timeout_s,
    )


def _repository_root(value: str | Path | None) -> Path:
    return Path(value).resolve() if value is not None else Path(__file__).resolve().parents[2]


def _inside_repository(value: str | Path, root: Path, label: str) -> Path:
    path = Path(value)
    path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise CalibrationExperimentError(f"{label} is outside repository") from exc
    return path


__all__ = [
    "CalibrationExperimentError",
    "run_active_qubit_spectroscopy_calibration",
]
