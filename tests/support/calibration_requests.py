"""Shared spectroscopy workflow request builders."""

from __future__ import annotations

from sqvm.calibration import SpectroscopyAxis, SpectroscopyCalibrationPolicy, SpectroscopyCalibrationRequest, SpectroscopyMode, SpectroscopyPulsePolicy, SpectroscopyRequest


def spectroscopy_calibration_request(mode: SpectroscopyMode = SpectroscopyMode.PARALLEL_LOCKSTEP) -> SpectroscopyCalibrationRequest:
    if mode == SpectroscopyMode.SINGLE:
        targets = ("Q1",)
        axes = (SpectroscopyAxis("Q1", (4.8, 5.0, 5.2)),)
    else:
        targets = ("Q1", "Q2")
        axes = (SpectroscopyAxis("Q1", (4.8, 5.0, 5.2)), SpectroscopyAxis("Q2", (5.0, 5.2, 5.4)))
    policies = tuple(SpectroscopyPulsePolicy(target, 5, 0.001, 2.0) for target in targets)
    return SpectroscopyCalibrationRequest(SpectroscopyRequest(mode, "coarse", targets, axes, policies), SpectroscopyCalibrationPolicy(fine_span_GHz=0.1, fine_points=5, confirmation_span_GHz=0.08, confirmation_points=5, min_contrast=0.1, max_leakage=0.01, max_norm_error=1.0e-8, max_coarse_refined_shift_GHz=0.05, max_parallel_peak_shift_GHz=0.01, max_cross_excitation=0.02, max_parallel_leakage_delta=0.005))
