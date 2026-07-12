"""Deterministic Stage 3 artifact serialization."""

from __future__ import annotations

import math
import os
import tempfile
from collections.abc import Mapping
from numbers import Real
from pathlib import Path
from typing import Any

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.spectrum.config import spectrum_config_to_dict
from sqvm.spectrum.models import StaticSpectrumArtifactSet, StaticSpectrumResult
from sqvm.spectrum.notebook import write_spectrum_verification_notebook


def static_spectrum_result_to_payload(result: StaticSpectrumResult) -> dict[str, Any]:
    baseline = result.baseline
    assignments = [row.to_dict() for row in baseline.dressed_states.assignments]
    participation = [row.to_dict() for row in baseline.participation.rows]
    return {
        "schema_version": "0.1",
        "artifact_type": "stage_03_static_spectrum",
        "artifact_version": "0.1",
        "source_hamiltonian_config": result.config.source_hamiltonian_config.as_posix(),
        "source_hamiltonian_artifacts": result.config.source_hamiltonian_artifacts.as_posix(),
        "source_rebaseline_manifest": result.config.source_rebaseline_manifest.as_posix(),
        "source_rebaseline_approval": result.config.source_rebaseline_approval.as_posix(),
        "provenance": result.provenance.to_dict(),
        "spectrum_config": spectrum_config_to_dict(result.config),
        "mode_order": ["q1", "c", "q2"],
        "basis": {
            "charge_cutoffs": dict(baseline.cutoffs),
            "hilbert_dimension": int(baseline.eigenstates.eigenvectors.shape[0]),
        },
        "hamiltonian_summary": {
            "shape": [
                int(baseline.eigenstates.eigenvectors.shape[0]),
                int(baseline.eigenstates.eigenvectors.shape[0]),
            ],
            "solver_backend": baseline.eigenstates.backend,
        },
        "eigenvalues_GHz": [float(value) for value in baseline.eigenstates.eigenvalues_GHz],
        "eigenvalue_gaps_GHz": [float(value) for value in baseline.eigenstates.gaps_GHz],
        "acceptance_baseline": {
            "eigsh_eigenvalue_gaps_GHz": (
                [float(value) for value in baseline.eigenstates.gaps_GHz]
                if baseline.eigenstates.backend == "validated_eigsh"
                else []
            ),
            "acceptance_eligible": result.config.acceptance_eligible,
        },
        "dressed_state_assignments": assignments,
        "mode_participation": participation,
        **baseline.metrics.to_dict(),
        "overlap_summary": {
            "minimum": min((row.overlap for row in baseline.dressed_states.assignments), default=None),
            "warnings": list(baseline.dressed_states.warnings),
            "matrix": baseline.dressed_states.overlap_matrix.tolist(),
        },
        "numerical_convergence": result.metric_convergence.to_dict(),
        "flux_scan": result.flux_scan.to_dict(),
        "avoided_crossings": list(result.flux_scan.candidates),
        "crossing_convergence": result.crossing_convergence.to_dict(),
        "solver_backend_validation": result.solver_backend_report.to_dict(),
        "solver_backend_validation_approval": {
            "present": result.solver_backend_report.validation_approval_sha256 is not None,
            "sha256": result.solver_backend_report.validation_approval_sha256,
        },
        "stage3_solver_source_tree_sha256": result.solver_backend_report.stage3_solver_source_tree_sha256,
        "environment_fingerprint_sha256": result.solver_backend_report.environment_fingerprint_sha256,
        "runtime": result.runtime.to_dict(),
        "stage_gate": result.stage_gate.to_dict(),
        "warnings": list(result.warnings),
        "checks": list(result.checks),
    }


def write_static_spectrum_artifacts(
    result: StaticSpectrumResult,
    output_dir: str | Path,
) -> StaticSpectrumArtifactSet:
    if not isinstance(result, StaticSpectrumResult):
        raise TypeError("write_static_spectrum_artifacts requires StaticSpectrumResult")
    payload = static_spectrum_result_to_payload(result)
    assert_finite_json_payload(payload)
    artifact_bytes = canonical_json_bytes(payload)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    artifact_path = root / "static_spectrum_artifacts.json"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=root,
        prefix=f".{artifact_path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(artifact_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, artifact_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    notebook_path = root / "verification.ipynb"
    write_spectrum_verification_notebook(artifact_path, notebook_path)
    return StaticSpectrumArtifactSet(
        root=root,
        static_spectrum_artifacts=artifact_path,
        verification_notebook=notebook_path,
    )


def assert_finite_json_payload(value: Any, path: str = "$") -> None:
    """Reject the first non-finite number without modifying the result payload."""

    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item).encode("utf-8")):
            assert_finite_json_payload(value[key], f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_finite_json_payload(item, f"{path}[{index}]")
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, Real) and not math.isfinite(float(value)):
        raise ValueError(f"non-finite JSON number at {path}")
