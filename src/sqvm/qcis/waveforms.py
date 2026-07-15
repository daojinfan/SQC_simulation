"""Deterministic waveform kernels for the QCIS v0.2 compiler."""

from __future__ import annotations

import math

import numpy as np
from scipy.special import erf

from .errors import QCISCompilationError, QCISReasonCode


def _invalid(detail: str) -> None:
    raise QCISCompilationError(QCISReasonCode.SETTING_INVALID, detail)


def rectangle(length: int, width: int) -> tuple[np.ndarray, np.ndarray]:
    if length <= 0 or width < 1 or width > length:
        _invalid("rectangle requires length > 0 and 1 <= width <= length")
    envelope = np.zeros(length, dtype="<f8")
    envelope[:width] = 1.0
    return envelope, np.zeros(length, dtype="<f8")


def gaussian(length: int, r_sigma: float) -> tuple[np.ndarray, np.ndarray]:
    if length <= 0 or not math.isfinite(r_sigma) or r_sigma <= 0.0:
        _invalid("gaussian requires length > 0 and r_sigma > 0")
    coordinate = np.arange(length, dtype="<f8") - (length - 1) / 2.0
    envelope = np.exp(-0.5 * np.square(coordinate / r_sigma)).astype("<f8")
    derivative = envelope * (-coordinate / (r_sigma * r_sigma))
    return envelope, derivative.astype("<f8")


def flattop(length: int, edge: int) -> tuple[np.ndarray, np.ndarray]:
    if length <= 0 or edge <= 0 or 2 * edge > length:
        _invalid("flattop requires edge > 0 and 2*edge <= length")
    sigma = edge / 4.0
    left = edge / 2.0
    right = (length - 1) - edge / 2.0
    root_two_sigma = math.sqrt(2.0) * sigma
    samples = np.arange(length, dtype="<f8")

    def raw(value: np.ndarray | float) -> np.ndarray | float:
        return 0.5 * (erf((value - left) / root_two_sigma) - erf((value - right) / root_two_sigma))

    baseline = float(raw(0.0))
    peak = float(raw((length - 1) / 2.0))
    scale = peak - baseline
    if scale <= 0.0 or not math.isfinite(scale):
        _invalid("flattop normalization is singular")
    envelope = (np.asarray(raw(samples), dtype="<f8") - baseline) / scale
    derivative = (
        np.exp(-np.square((samples - left) / root_two_sigma))
        - np.exp(-np.square((samples - right) / root_two_sigma))
    ) / (math.sqrt(2.0 * math.pi) * sigma * scale)
    envelope[0] = 0.0
    envelope[-1] = 0.0
    if length % 2 == 1:
        envelope[length // 2] = 1.0
    return envelope.astype("<f8"), derivative.astype("<f8")


def acz(length: int, thf: float, thi: float, lam2: float, lam3: float) -> tuple[np.ndarray, np.ndarray]:
    values = (thf, thi, lam2, lam3)
    if length < 3 or not all(math.isfinite(value) for value in values) or not (0.0 < thi < thf < math.pi / 2.0):
        _invalid("acz requires length >= 3 and 0 < thi < thf < pi/2")
    s = np.linspace(0.0, 1.0, 4097, dtype="<f8")
    lam1 = 1.0 - lam3
    theta = thi + (thf - thi) * 0.5 * (
        lam1 * (1.0 - np.cos(2.0 * math.pi * s))
        + lam2 * (1.0 - np.cos(4.0 * math.pi * s))
        + lam3 * (1.0 - np.cos(6.0 * math.pi * s))
    )
    if np.any(theta <= 0.0) or np.any(theta >= math.pi / 2.0):
        _invalid("acz theta trajectory leaves (0,pi/2)")
    integrand = np.sin(theta)
    cumulative = np.empty_like(s)
    cumulative[0] = 0.0
    cumulative[1:] = np.cumsum((integrand[:-1] + integrand[1:]) * (0.5 / 4096.0), dtype="<f8")
    normalized_time = cumulative / cumulative[-1]
    output_time = np.linspace(0.0, 1.0, length, dtype="<f8")
    theta_t = np.interp(output_time, normalized_time, theta)
    cot = lambda value: 1.0 / np.tan(value)
    envelope = (cot(theta_t) - cot(thi)) / (cot(thf) - cot(thi))
    envelope[0] = 0.0
    envelope[-1] = 0.0
    return envelope.astype("<f8"), np.zeros(length, dtype="<f8")


def analytic_waveform(wave_index: int, length: int, shape_parameter: tuple[float, ...]) -> tuple[np.ndarray, np.ndarray]:
    if wave_index == 0:
        return rectangle(length, int(shape_parameter[0]))
    if wave_index == 1:
        return gaussian(length, float(shape_parameter[0]))
    if wave_index == 2:
        return flattop(length, int(shape_parameter[0]))
    if wave_index == 5:
        return acz(length, *(float(value) for value in shape_parameter))
    raise QCISCompilationError(QCISReasonCode.UNSUPPORTED_WAVE_INDEX, str(wave_index))
