"""Frozen trusted workflow-to-evidence-verifier registry."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from sqvm.calibration.spectroscopy_reader import verify_qubit_spectroscopy_scan_evidence
from sqvm.storage.archive_format import EvidenceVerifier


SPECTROSCOPY_VERIFIER_ID = "qubit_spectroscopy_scan_evidence"
SPECTROSCOPY_VERIFIER_VERSION = "0.3"
_REGISTRY = MappingProxyType({
    ("qubit_spectroscopy_scan_v1", "0.3"): (
        SPECTROSCOPY_VERIFIER_ID,
        SPECTROSCOPY_VERIFIER_VERSION,
        verify_qubit_spectroscopy_scan_evidence,
    ),
})
_ARCHIVE_REGISTRY = MappingProxyType({
    (SPECTROSCOPY_VERIFIER_ID, SPECTROSCOPY_VERIFIER_VERSION): verify_qubit_spectroscopy_scan_evidence,
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


__all__ = [
    "SPECTROSCOPY_VERIFIER_ID", "SPECTROSCOPY_VERIFIER_VERSION",
    "archive_evidence_verifier_registry", "get_workflow_evidence_verifier",
    "workflow_evidence_verifier_registry",
]
