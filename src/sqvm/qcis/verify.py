"""Fail-closed byte verifiers for QCIS compiler hand-off artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from .canonical import canonical_json_bytes, sha256_bytes, sha256_json
from .errors import QCISCompilationError, QCISReasonCode
from .models import QCISCompilation


_LOGICAL_NAMES = ("q1_xy", "q2_xy", "q1_flux", "q2_flux", "c_flux")
_EFFECTIVE_NAMES = tuple(f"effective_{name}" for name in _LOGICAL_NAMES)


def _fail(code: QCISReasonCode, detail: str) -> None:
    raise QCISCompilationError(code, detail)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(name): _plain(item) for name, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _arrays(compilation: QCISCompilation, names: tuple[str, ...]) -> dict[str, np.ndarray]:
    return {name: getattr(compilation, name) for name in names}


def _candidate_arrays(
    expected: Mapping[str, np.ndarray],
    candidate: Any | None,
    *,
    compatibility_name: str,
    code: QCISReasonCode,
) -> Mapping[str, Any]:
    if candidate is None:
        return expected
    if not isinstance(candidate, Mapping):
        values = dict(expected)
        values[compatibility_name] = candidate
        return values
    if set(candidate) != set(expected):
        _fail(code, "array handoff key set differs")
    return candidate


def _verify_arrays(
    compilation: QCISCompilation,
    expected: Mapping[str, np.ndarray],
    candidate: Any | None,
    *,
    compatibility_name: str,
    code: QCISReasonCode,
    hash_names: Mapping[str, str],
) -> None:
    supplied = _candidate_arrays(expected, candidate, compatibility_name=compatibility_name, code=code)
    for name, reference in expected.items():
        value = supplied[name]
        if not isinstance(value, np.ndarray):
            _fail(code, f"{name} is not an ndarray")
        if value.dtype != reference.dtype or value.shape != reference.shape or not value.flags.c_contiguous:
            _fail(code, f"{name} dtype, shape, or layout differs")
        if sha256_bytes(value.tobytes()) != compilation.plan.array_sha256[hash_names[name]]:
            _fail(code, f"{name} raw bytes differ")


def verify_compilation(compilation: QCISCompilation, candidate: Any | None = None) -> None:
    """Verify all five logical waveform arrays before Stage 4.1 publication.

    ``candidate`` may be an exact five-key logical-array mapping. A non-mapping
    candidate remains the legacy q1-XY-only override while the other four arrays
    are still verified from the compilation artifact.
    """

    if not isinstance(compilation, QCISCompilation):
        _fail(QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH, "typed QCISCompilation is required")
    plan = compilation.plan
    if plan.source != compilation.concrete_source or sha256_bytes(compilation.concrete_source.encode("utf-8")) != compilation.concrete_source_sha256:
        _fail(QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH, "concrete source hash differs")
    if sha256_bytes(compilation.ast_bytes) != plan.ast_sha256 or compilation.ast_bytes != canonical_json_bytes(plan.program.payload()):
        _fail(QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH, "AST bytes differ")
    if sha256_bytes(compilation.trace_bytes) != plan.trace_sha256 or compilation.trace_bytes != canonical_json_bytes(_plain(plan.trace)):
        _fail(QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH, "trace bytes differ")
    expected = _arrays(compilation, _LOGICAL_NAMES)
    _verify_arrays(
        compilation,
        expected,
        candidate,
        compatibility_name="q1_xy",
        code=QCISReasonCode.LOGICAL_WAVEFORM_HASH_MISMATCH,
        hash_names={name: name for name in _LOGICAL_NAMES},
    )


def verify_effective_controls(compilation: QCISCompilation, candidate: Any | None = None) -> None:
    """Verify all identity-electronics effective arrays without regeneration."""

    expected = _arrays(compilation, _EFFECTIVE_NAMES)
    _verify_arrays(
        compilation,
        expected,
        candidate,
        compatibility_name="effective_q1_xy",
        code=QCISReasonCode.EFFECTIVE_CONTROL_HASH_MISMATCH,
        hash_names={name: name.removeprefix("effective_") for name in _EFFECTIVE_NAMES},
    )


def verify_drive_event_inventory(compilation: QCISCompilation, candidate: Any | None = None) -> None:
    """Verify v0.3 frame-reference and per-drive-event evidence as exact bytes."""

    plan = compilation.plan
    expected = {
        "dt_ns": plan.dt_ns,
        "sample_rate_Hz": plan.sample_rate_Hz,
        "frame_reference_frequency_GHz": dict(plan.frame_reference_frequency_GHz),
        "frame_reference_authority_sha256": dict(plan.frame_reference_authority_sha256),
        "drive_event_inventory": [_plain(event) for event in plan.drive_event_inventory],
    }
    value = expected if candidate is None else candidate
    if not isinstance(value, Mapping) or set(value) != set(expected) or value != expected:
        _fail(QCISReasonCode.DRIVE_EVENT_EVIDENCE_MISMATCH, "v0.3 drive event evidence differs")
    if not plan.drive_event_inventory_sha256 or sha256_json(expected["drive_event_inventory"]) != plan.drive_event_inventory_sha256:
        _fail(QCISReasonCode.DRIVE_EVENT_EVIDENCE_MISMATCH, "v0.3 drive event inventory hash differs")
    trace = _plain(plan.trace)
    if (
        trace.get("dt_ns") != expected["dt_ns"]
        or trace.get("sample_rate_Hz") != expected["sample_rate_Hz"]
        or trace.get("frame_reference_frequency_GHz") != expected["frame_reference_frequency_GHz"]
        or trace.get("frame_reference_authority_sha256") != expected["frame_reference_authority_sha256"]
        or trace.get("drive_event_inventory") != expected["drive_event_inventory"]
    ):
        _fail(QCISReasonCode.DRIVE_EVENT_EVIDENCE_MISMATCH, "v0.3 trace evidence differs")


def verify_coefficient_inventory(compilation: QCISCompilation, candidate: Any | None = None) -> None:
    """Verify the exact Stage 5.1 coefficient-inventory byte payload."""

    value = compilation.coefficient_inventory_bytes if candidate is None else candidate
    if not isinstance(value, bytes) or sha256_bytes(value) != sha256_bytes(compilation.coefficient_inventory_bytes):
        _fail(QCISReasonCode.COEFFICIENT_INVENTORY_MISMATCH, "coefficient inventory differs")
