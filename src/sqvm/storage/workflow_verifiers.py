"""Frozen trusted workflow-to-evidence-verifier registry."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from sqvm.storage.archive_format import EvidenceVerifier


SPECTROSCOPY_VERIFIER_ID = "qubit_spectroscopy_scan_evidence"
SPECTROSCOPY_VERIFIER_VERSION = "0.3"
RABI_VERIFIER_ID = "qubit_rabi_x2p_amplitude_scan_evidence"
RABI_VERIFIER_VERSION = "0.1"


def _spectroscopy_evidence_verifier(reader) -> None:
    from sqvm.calibration.spectroscopy_reader import verify_qubit_spectroscopy_scan_evidence
    verify_qubit_spectroscopy_scan_evidence(reader)


def _rabi_evidence_verifier(reader) -> None:
    from sqvm.calibration.rabi_reader import verify_qubit_rabi_scan_evidence
    verify_qubit_rabi_scan_evidence(reader)


_REGISTRY = MappingProxyType({
    ("qubit_spectroscopy_scan_v1", "0.3"): (
        SPECTROSCOPY_VERIFIER_ID,
        SPECTROSCOPY_VERIFIER_VERSION,
        _spectroscopy_evidence_verifier,
    ),
    ("qubit_rabi_x2p_amplitude_scan_v1", "0.1"): (
        RABI_VERIFIER_ID,
        RABI_VERIFIER_VERSION,
        _rabi_evidence_verifier,
    ),
})
_ARCHIVE_REGISTRY = MappingProxyType({
    (SPECTROSCOPY_VERIFIER_ID, SPECTROSCOPY_VERIFIER_VERSION): _spectroscopy_evidence_verifier,
    (RABI_VERIFIER_ID, RABI_VERIFIER_VERSION): _rabi_evidence_verifier,
})
_ALIASES = MappingProxyType({
    ("qubit_spectroscopy_scan_v1", "0.3"): "qubit_spectroscopy_",
    ("qubit_rabi_x2p_amplitude_scan_v1", "0.1"): "qubit_rabi_",
})


def get_workflow_evidence_verifier(
    workflow_id: str, artifact_version: str,
) -> tuple[str, str, EvidenceVerifier] | None:
    """Return the frozen production verifier or ``None`` for unarchivable versions."""

    if not isinstance(workflow_id, str) or not isinstance(artifact_version, str):
        return None
    return _REGISTRY.get((workflow_id, artifact_version))


def workflow_evidence_verifier_registry() -> MappingProxyType:
    """Expose an immutable lookup view for archive verification callers."""

    return _REGISTRY


def archive_evidence_verifier_registry() -> Mapping[tuple[str, str], EvidenceVerifier]:
    """Return the immutable archive verifier-id/version trust map."""

    return _ARCHIVE_REGISTRY


def workflow_hot_verifier_registry():
    """Return registered local path verifiers without making versions implicit."""
    from sqvm.calibration.rabi import verify_rabi_scan
    from sqvm.calibration.spectroscopy_run import verify_qubit_spectroscopy_scan
    return MappingProxyType({
        ("qubit_spectroscopy_scan_v1", "0.3"): verify_qubit_spectroscopy_scan,
        ("qubit_rabi_x2p_amplitude_scan_v1", "0.1"): verify_rabi_scan,
    })


def hot_alias_prefix(workflow_id: str, artifact_version: str) -> str | None:
    """Return a carrier prefix only for an explicitly registered workflow."""
    return _ALIASES.get((workflow_id, artifact_version))


def valid_hot_alias(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and "\x00" not in value and "/" not in value and "\\" not in value and ":" not in value and value not in {".", ".."} and any(value.startswith(prefix) for prefix in _ALIASES.values())


def hot_alias_prefixes() -> tuple[str, ...]:
    return tuple(_ALIASES.values())


__all__ = [
    "SPECTROSCOPY_VERIFIER_ID", "SPECTROSCOPY_VERIFIER_VERSION",
    "RABI_VERIFIER_ID", "RABI_VERIFIER_VERSION",
    "archive_evidence_verifier_registry", "get_workflow_evidence_verifier",
    "workflow_evidence_verifier_registry", "workflow_hot_verifier_registry",
    "hot_alias_prefix", "hot_alias_prefixes", "valid_hot_alias",
]
