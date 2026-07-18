"""Compatibility import for :mod:`sqvm.calibration.api`."""

from sqvm.calibration.api import (
    CalibrationExperimentError,
    run_active_qubit_spectroscopy_calibration,
)

__all__ = [
    "CalibrationExperimentError",
    "run_active_qubit_spectroscopy_calibration",
]
