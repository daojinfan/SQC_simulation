"""Independent phase audit for the two-X2P Rabi calibration experiment.

The compiler's v0.3 logical IQ remains in the rotating reference frame.  This
module derives laboratory-frame values only for audit/display and never
returns a control object suitable for the evolution path.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


RABI_PHASE_AUDIT_FAILED = "rabi_phase_audit_failed"


class RabiPhaseAuditError(ValueError):
    """Stable failure envelope for a rejected Rabi phase audit."""

    code = RABI_PHASE_AUDIT_FAILED


def wrap_phase_rad(value: float) -> float:
    """Wrap a finite phase into the compiler's ``[-pi, pi]`` convention."""

    if not math.isfinite(value):
        raise RabiPhaseAuditError("phase must be finite")
    return math.remainder(value, 2.0 * math.pi)


def rotating_to_lab_waveform(
    rotating_iq_GHz: np.ndarray,
    *,
    time_center_ns: np.ndarray,
    f_ref_GHz: float,
) -> np.ndarray:
    """Derive a real laboratory-frame preview from rotating-reference IQ.

    ``GHz * ns`` is cycles, so the carrier factor is ``exp(-i*2*pi*f_ref*t)``.
    The returned real array is deliberately not an evolution input.
    """

    iq = np.asarray(rotating_iq_GHz, dtype=np.complex128)
    time = np.asarray(time_center_ns, dtype=float)
    if iq.ndim != 1 or time.ndim != 1 or iq.size != time.size:
        raise RabiPhaseAuditError("rotating IQ and time centers must be equal-length vectors")
    if not math.isfinite(f_ref_GHz) or not np.all(np.isfinite(iq)) or not np.all(np.isfinite(time)):
        raise RabiPhaseAuditError("laboratory preview inputs must be finite")
    return np.real(iq * np.exp(-1j * 2.0 * math.pi * f_ref_GHz * time))


@dataclass(frozen=True, slots=True)
class RabiPhaseInterval:
    source_instruction_index: int
    start_sample: int
    end_sample: int
    setting_id: str
    setting_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_instruction_index": self.source_instruction_index,
            "start_sample": self.start_sample,
            "end_sample": self.end_sample,
            "setting_id": self.setting_id,
            "setting_hash": self.setting_hash,
        }


@dataclass(frozen=True, slots=True)
class RabiElectronicsScheduleProof:
    """Minimal receipt proving one electronics-chain application over one schedule."""

    application_count: int
    logical_sample_count: int
    effective_sample_count: int
    effective_first_center_ns: float
    effective_dt_ns: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_count": self.application_count,
            "logical_sample_count": self.logical_sample_count,
            "effective_sample_count": self.effective_sample_count,
            "effective_first_center_ns": self.effective_first_center_ns,
            "effective_dt_ns": self.effective_dt_ns,
        }


@dataclass(frozen=True, slots=True)
class RabiPhaseAudit:
    """JSON-friendly, static evidence for a two-X2P Rabi point."""

    event_count: int
    first_interval: RabiPhaseInterval
    second_interval: RabiPhaseInterval
    amplitude_GHz: float
    phase_total_rad: float
    f_drive_GHz: float
    f_ref_GHz: float
    detuning_GHz: float
    rotating_first_sample_phase_rad: tuple[float, float]
    lab_first_sample_phase_rad: tuple[float, float]
    lab_phase_advance_unwrapped_rad: float
    lab_phase_advance_wrapped_rad: float
    absolute_start_time_ns: tuple[float, float]
    electronics_schedule: RabiElectronicsScheduleProof

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_count": self.event_count,
            "intervals": [self.first_interval.to_dict(), self.second_interval.to_dict()],
            "setting_hashes": [self.first_interval.setting_hash, self.second_interval.setting_hash],
            "amplitude_GHz": self.amplitude_GHz,
            "phase_total_rad": self.phase_total_rad,
            "f_drive_GHz": self.f_drive_GHz,
            "f_ref_GHz": self.f_ref_GHz,
            "detuning_GHz": self.detuning_GHz,
            "rotating_first_sample_phase_rad": list(self.rotating_first_sample_phase_rad),
            "lab_first_sample_phase_rad": list(self.lab_first_sample_phase_rad),
            "lab_phase_advance_unwrapped_rad": self.lab_phase_advance_unwrapped_rad,
            "lab_phase_advance_wrapped_rad": self.lab_phase_advance_wrapped_rad,
            "absolute_start_time_ns": list(self.absolute_start_time_ns),
            "electronics_schedule": self.electronics_schedule.to_dict(),
        }


def audit_two_x2p_phase(
    drive_events: Sequence[Mapping[str, Any]],
    *,
    amplitude_GHz: float,
    dt_ns: float,
    logical_sample_count: int,
    electronics_schedule: RabiElectronicsScheduleProof,
    source_operations: Mapping[int, str],
) -> RabiPhaseAudit:
    """Validate absolute rotating/lab phase rules for exactly two X2P events.

    Event fields intentionally mirror ``QCISLogicalWaveformPlan.drive_event_inventory``.
    The caller supplies the already resolved effective setting amplitude because the
    compiler's immutable event evidence stores setting identity, not its full record.
    """

    if not math.isfinite(amplitude_GHz) or amplitude_GHz <= 0.0:
        raise RabiPhaseAuditError("amplitude_GHz must be finite and positive")
    if not math.isfinite(dt_ns) or dt_ns <= 0.0 or type(logical_sample_count) is not int or logical_sample_count <= 0:
        raise RabiPhaseAuditError("logical clock evidence is invalid")
    _validate_electronics_proof(electronics_schedule, logical_sample_count, dt_ns)
    if len(drive_events) != 2:
        raise RabiPhaseAuditError("exactly two drive events are required")

    first, second = (_interval(event) for event in drive_events)
    if not isinstance(source_operations, Mapping) or any(source_operations.get(interval.source_instruction_index) != "X2P" for interval in (first, second)):
        raise RabiPhaseAuditError("each drive event must bind an X2P source instruction")
    if first.end_sample != second.start_sample:
        raise RabiPhaseAuditError("X2P intervals must be contiguous")
    if first.end_sample > logical_sample_count or second.end_sample > logical_sample_count:
        raise RabiPhaseAuditError("X2P interval exceeds the full logical schedule")

    normalized = [_event_values(event) for event in drive_events]
    phase_total, f_drive, f_ref, detuning = normalized[0]
    for candidate in normalized[1:]:
        if not _allclose_tuple(candidate, (phase_total, f_drive, f_ref, detuning)):
            raise RabiPhaseAuditError("X2P settings or rotating phase differ")
    if first.setting_id != second.setting_id or first.setting_hash != second.setting_hash:
        raise RabiPhaseAuditError("X2P setting evidence differs")

    starts = (first.start_sample, second.start_sample)
    starts_ns = tuple((start + 0.5) * dt_ns for start in starts)
    rotating = tuple(wrap_phase_rad(phase_total + 2.0 * math.pi * detuning * time) for time in starts_ns)
    lab = tuple(wrap_phase_rad(phase_total + 2.0 * math.pi * f_drive * time) for time in starts_ns)
    lab_advance = 2.0 * math.pi * f_drive * (second.start_sample - first.start_sample) * dt_ns
    return RabiPhaseAudit(
        event_count=2,
        first_interval=first,
        second_interval=second,
        amplitude_GHz=float(amplitude_GHz),
        phase_total_rad=phase_total,
        f_drive_GHz=f_drive,
        f_ref_GHz=f_ref,
        detuning_GHz=detuning,
        rotating_first_sample_phase_rad=rotating,
        lab_first_sample_phase_rad=lab,
        lab_phase_advance_unwrapped_rad=lab_advance,
        lab_phase_advance_wrapped_rad=wrap_phase_rad(lab_advance),
        absolute_start_time_ns=starts_ns,
        electronics_schedule=electronics_schedule,
    )


def _validate_electronics_proof(proof: RabiElectronicsScheduleProof, logical_count: int, dt_ns: float) -> None:
    if not isinstance(proof, RabiElectronicsScheduleProof):
        raise RabiPhaseAuditError("electronics schedule proof is required")
    if proof.application_count != 1 or proof.logical_sample_count != logical_count:
        raise RabiPhaseAuditError("electronics chain must consume the full schedule exactly once")
    if proof.effective_sample_count < logical_count or proof.effective_sample_count <= 0:
        raise RabiPhaseAuditError("effective schedule sample count is invalid")
    if not math.isfinite(proof.effective_first_center_ns) or not math.isclose(proof.effective_dt_ns, dt_ns, rel_tol=0.0, abs_tol=1e-12):
        raise RabiPhaseAuditError("effective schedule global time axis is invalid")


def _interval(event: Mapping[str, Any]) -> RabiPhaseInterval:
    if not isinstance(event, Mapping) or event.get("transition") != "01":
        raise RabiPhaseAuditError("each audited event must be a transition-01 drive")
    if event.get("phase_rule_id") != "qcis_v03_absolute_detuning_phase_v1":
        raise RabiPhaseAuditError("drive event does not declare the v0.3 absolute detuning phase rule")
    start, count, source = event.get("actual_start_sample"), event.get("sample_count"), event.get("source_instruction_index")
    evidence = event.get("setting_evidence")
    if type(start) is not int or type(count) is not int or start < 0 or count <= 0 or type(source) is not int:
        raise RabiPhaseAuditError("drive event interval is invalid")
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("setting_id"), str) or not isinstance(evidence.get("setting_hash"), str):
        raise RabiPhaseAuditError("drive event setting evidence is incomplete")
    return RabiPhaseInterval(source, start, start + count, evidence["setting_id"], evidence["setting_hash"])


def _event_values(event: Mapping[str, Any]) -> tuple[float, float, float, float]:
    values = tuple(event.get(name) for name in ("phase_total_rad", "f_drive_GHz", "f_ref_GHz", "detuning_GHz"))
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in values):
        raise RabiPhaseAuditError("drive event phase or frequency is invalid")
    phase, drive, reference, detuning = (float(value) for value in values)
    if not math.isclose(detuning, drive - reference, rel_tol=0.0, abs_tol=1e-12):
        raise RabiPhaseAuditError("drive event detuning disagrees with drive/reference frequencies")
    return phase, drive, reference, detuning


def _allclose_tuple(left: tuple[float, ...], right: tuple[float, ...]) -> bool:
    return all(math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12) for a, b in zip(left, right, strict=True))
