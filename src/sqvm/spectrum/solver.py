"""Deterministic dense/eigsh solving and solver-backend validation."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import platform
import struct
import sys
import time
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from typing import Any

import numpy as np
import scipy
from scipy import linalg
from scipy.sparse.linalg import eigsh

from sqvm.hamiltonian import load_hamiltonian_config
from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256
from sqvm.spectrum.config import spectrum_config_to_dict
from sqvm.spectrum.models import (
    EigenstateTable,
    ProvenanceReport,
    SolverBackendReport,
    SolverBackendValidationApproval,
    SolverBackendValidationArtifact,
    SpectrumConfig,
    ValidatedSolverSpec,
)


def stage3_solver_source_tree_sha256(repository_root: str | Path) -> str:
    root = Path(repository_root).resolve()
    paths = list((root / "src" / "sqvm" / "spectrum").rglob("*.py"))
    if not paths or any(not path.is_file() for path in paths):
        raise ValueError("Stage 3 solver source tree is empty or incomplete")
    digest = hashlib.sha256()
    ordered = sorted(
        ((path.relative_to(root).as_posix(), path) for path in paths),
        key=lambda item: item[0].encode("utf-8"),
    )
    for relative, path in ordered:
        raw = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(raw)).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw)
    return digest.hexdigest().upper()


def environment_fingerprint() -> tuple[dict[str, Any], str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        np.__config__.show()
    blas_text = buffer.getvalue().replace("\r\n", "\n").replace("\r", "\n")
    payload = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "blas_config_sha256": hashlib.sha256(blas_text.encode("utf-8")).hexdigest().upper(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
    }
    return payload, hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()


def canonical_flux_text(value: float | str | Decimal, decimal_places: int = 12) -> str:
    quantum = Decimal(1).scaleb(-decimal_places)
    quantized = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_EVEN)
    if quantized == 0:
        quantized = abs(quantized)
    return f"{quantized:.{decimal_places}f}"


def sha256_counter_seed(
    *,
    stage2_artifacts_sha256: str,
    cutoffs: tuple[int, int, int],
    flux_phi0: float | str | Decimal,
    num_states: int,
) -> tuple[bytes, str, str]:
    if len(stage2_artifacts_sha256) != 64 or stage2_artifacts_sha256.upper() != stage2_artifacts_sha256:
        raise ValueError("stage2_artifacts_sha256 must be 64 uppercase hexadecimal characters")
    try:
        int(stage2_artifacts_sha256, 16)
    except ValueError as exc:
        raise ValueError("stage2_artifacts_sha256 must be hexadecimal") from exc
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in cutoffs):
        raise ValueError("cutoffs must be unsigned integers")
    if isinstance(num_states, bool) or not isinstance(num_states, int) or num_states < 0:
        raise ValueError("num_states must be an unsigned integer")
    cutoff_text = f"q1={cutoffs[0]},c={cutoffs[1]},q2={cutoffs[2]}"
    seed_text = (
        "sqvm-eigsh-v0-v1\n"
        f"stage2_artifacts_sha256={stage2_artifacts_sha256}\n"
        f"cutoff={cutoff_text}\n"
        f"flux_phi0={canonical_flux_text(flux_phi0)}\n"
        f"num_states={num_states}\n"
    )
    seed = seed_text.encode("utf-8")
    seed_digest = hashlib.sha256(seed).hexdigest().upper()
    block0_digest = hashlib.sha256(seed + struct.pack(">Q", 0)).hexdigest().upper()
    return seed, seed_digest, block0_digest


def sha256_counter_v1(
    dimension: int,
    *,
    stage2_artifacts_sha256: str,
    cutoffs: tuple[int, int, int],
    flux_phi0: float | str | Decimal,
    num_states: int,
) -> np.ndarray:
    if dimension < 1:
        raise ValueError("dimension must be positive")
    seed, _, _ = sha256_counter_seed(
        stage2_artifacts_sha256=stage2_artifacts_sha256,
        cutoffs=cutoffs,
        flux_phi0=flux_phi0,
        num_states=num_states,
    )
    values: list[float] = []
    block = 0
    while len(values) < dimension:
        digest = hashlib.sha256(seed + struct.pack(">Q", block)).digest()
        for offset in range(0, 32, 8):
            word = int.from_bytes(digest[offset : offset + 8], "big", signed=False)
            values.append(2.0 * ((word >> 11) / float(2**53)) - 1.0)
            if len(values) == dimension:
                break
        block += 1
    vector = np.asarray(values, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("v0_generation_failed")
    return vector / norm


def sha256_counter_seed_v2(
    *,
    stage2_artifacts_sha256: str,
    cutoffs: tuple[int, int, int],
    fluxes_phi0: tuple[float | str | Decimal, float | str | Decimal, float | str | Decimal],
    num_states: int,
) -> tuple[bytes, str, str]:
    # Reuse v1 validation without inheriting its encoding.
    sha256_counter_seed(
        stage2_artifacts_sha256=stage2_artifacts_sha256,
        cutoffs=cutoffs,
        flux_phi0=fluxes_phi0[1],
        num_states=num_states,
    )
    seed_text = (
        "sqvm-eigsh-v0-v2\n"
        f"stage2_artifacts_sha256={stage2_artifacts_sha256}\n"
        f"cutoff=q1={cutoffs[0]},c={cutoffs[1]},q2={cutoffs[2]}\n"
        f"q1_flux_phi0={canonical_flux_text(fluxes_phi0[0])}\n"
        f"c_flux_phi0={canonical_flux_text(fluxes_phi0[1])}\n"
        f"q2_flux_phi0={canonical_flux_text(fluxes_phi0[2])}\n"
        f"num_states={num_states}\n"
    )
    seed = seed_text.encode("utf-8")
    return (
        seed,
        hashlib.sha256(seed).hexdigest().upper(),
        hashlib.sha256(seed + struct.pack(">Q", 0)).hexdigest().upper(),
    )


def sha256_counter_v2(
    dimension: int,
    *,
    stage2_artifacts_sha256: str,
    cutoffs: tuple[int, int, int],
    fluxes_phi0: tuple[float | str | Decimal, float | str | Decimal, float | str | Decimal],
    num_states: int,
) -> np.ndarray:
    if dimension < 1:
        raise ValueError("dimension must be positive")
    seed, _, _ = sha256_counter_seed_v2(
        stage2_artifacts_sha256=stage2_artifacts_sha256,
        cutoffs=cutoffs,
        fluxes_phi0=fluxes_phi0,
        num_states=num_states,
    )
    return _counter_vector(dimension, seed)


def _counter_vector(dimension: int, seed: bytes) -> np.ndarray:
    values: list[float] = []
    block = 0
    while len(values) < dimension:
        digest = hashlib.sha256(seed + struct.pack(">Q", block)).digest()
        for offset in range(0, 32, 8):
            word = int.from_bytes(digest[offset : offset + 8], "big", signed=False)
            values.append(2.0 * ((word >> 11) / float(2**53)) - 1.0)
            if len(values) == dimension:
                break
        block += 1
    vector = np.asarray(values, dtype=np.float64)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm == 0.0:
        raise ValueError("v0_generation_failed")
    return vector / norm


def solve_static_eigensystem(model: Any, solver_spec: ValidatedSolverSpec) -> EigenstateTable:
    count = solver_spec.num_states
    dimension = model.matrix.shape[0]
    if count < 1 or count >= dimension:
        raise ValueError("num_states must be positive and less than Hilbert dimension")
    started = time.perf_counter()
    if solver_spec.backend == "dense_eigh":
        values, vectors = linalg.eigh(
            model.matrix.toarray(),
            subset_by_index=[0, count - 1],
            eigvals_only=False,
        )
    elif solver_spec.backend == "validated_eigsh":
        cutoffs = tuple(model.basis.charge_cutoffs[mode] for mode in ("q1", "c", "q2"))
        flux_by_mode = {row.mode: row.flux_bias_phi0 for row in model.effective_junctions}
        if solver_spec.v0_rule == "sha256_counter_v2":
            v0 = sha256_counter_v2(
                dimension,
                stage2_artifacts_sha256=solver_spec.stage2_artifacts_sha256,
                cutoffs=cutoffs,
                fluxes_phi0=tuple(flux_by_mode[mode] for mode in ("q1", "c", "q2")),
                num_states=count,
            )
        else:
            v0 = sha256_counter_v1(
                dimension,
                stage2_artifacts_sha256=solver_spec.stage2_artifacts_sha256,
                cutoffs=cutoffs,
                flux_phi0=flux_by_mode["c"],
                num_states=count,
            )
        values, vectors = eigsh(
            model.matrix,
            k=count,
            which=solver_spec.which,
            tol=solver_spec.tolerance,
            maxiter=solver_spec.maxiter,
            ncv=solver_spec.ncv,
            v0=v0,
            return_eigenvectors=True,
        )
        order = np.argsort(values, kind="stable")
        values = values[order]
        vectors = vectors[:, order]
    else:
        raise ValueError(f"unsupported solver backend {solver_spec.backend}")
    elapsed = time.perf_counter() - started
    cutoffs = tuple(model.basis.charge_cutoffs[mode] for mode in ("q1", "c", "q2"))
    flux = next(row.flux_bias_phi0 for row in model.effective_junctions if row.mode == "c")
    return EigenstateTable(
        eigenvalues_GHz=np.asarray(values, dtype=float),
        eigenvectors=np.asarray(vectors, dtype=float),
        backend=solver_spec.backend,
        solve_time_seconds=elapsed,
        cutoffs=cutoffs,
        flux_key=canonical_flux_text(flux),
    )


def dense_reference_eigensystem(model: Any, num_states: int) -> tuple[np.ndarray, np.ndarray, float]:
    started = time.perf_counter()
    values, vectors = linalg.eigh(
        model.matrix.toarray(),
        subset_by_index=[0, num_states],
        eigvals_only=False,
    )
    return np.asarray(values), np.asarray(vectors), time.perf_counter() - started


def dense_near_degenerate_blocks(
    eigenvalues: np.ndarray,
    *,
    num_states: int,
    threshold_GHz: float,
    boundary_margin_GHz: float,
) -> tuple[tuple[tuple[int, int], ...], tuple[float, ...]]:
    if len(eigenvalues) < num_states + 1:
        raise ValueError("dense reference must contain num_states+1 eigenvalues")
    boundary_gaps = tuple(float(eigenvalues[i + 1] - eigenvalues[i]) for i in range(num_states))
    for gap in boundary_gaps[: num_states - 1]:
        if abs(gap - threshold_GHz) <= boundary_margin_GHz:
            raise ValueError("near_degenerate_partition_ambiguous")
    if boundary_gaps[num_states - 1] <= threshold_GHz + boundary_margin_GHz:
        raise ValueError("validation_block_truncated")
    blocks: list[tuple[int, int]] = []
    start = 0
    for index, gap in enumerate(boundary_gaps[: num_states - 1]):
        if gap > threshold_GHz:
            blocks.append((start, index))
            start = index + 1
    blocks.append((start, num_states - 1))
    return tuple(blocks), boundary_gaps


def projector_spectral_error(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape or left.ndim != 2:
        raise ValueError("subspace bases must have equal two-dimensional shapes")
    left_basis, _ = np.linalg.qr(left)
    right_basis, _ = np.linalg.qr(right)
    orthogonal_residual = left_basis - right_basis @ (right_basis.conj().T @ left_basis)
    return float(np.linalg.norm(orthogonal_residual, ord=2))


def load_solver_backend_validation(path: str | Path) -> SolverBackendValidationArtifact:
    source = Path(path)
    return SolverBackendValidationArtifact(path=source, payload=_load_mapping(source, "solver validation"))


def load_solver_backend_validation_approval(path: str | Path) -> SolverBackendValidationApproval:
    source = Path(path)
    return SolverBackendValidationApproval(path=source, payload=_load_mapping(source, "solver validation approval"))


def validate_solver_backend(
    validation: SolverBackendValidationArtifact,
    approval: SolverBackendValidationApproval,
    provenance: ProvenanceReport,
    config: SpectrumConfig,
) -> SolverBackendReport:
    root = find_repository_root(config.source_path)
    source_hash = stage3_solver_source_tree_sha256(root)
    environment, environment_hash = environment_fingerprint()
    artifact = validation.payload
    approval_payload = approval.payload
    errors: list[str] = []
    validation_hash = _safe_raw_hash(validation.path, "solver validation", errors)
    approval_hash = _safe_raw_hash(approval.path, "solver validation approval", errors)

    if validation.path.resolve() != _resolve_project_path(config.runtime.solver_validation_artifact, root):
        errors.append("solver validation path does not match SpectrumConfig")
    if approval.path.resolve() != _resolve_project_path(config.runtime.solver_validation_approval, root):
        errors.append("solver validation approval path does not match SpectrumConfig")
    _check_canonical_file(validation.path, artifact, "solver validation", errors)
    _check_canonical_file(approval.path, approval_payload, "solver validation approval", errors)

    expected_identity = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation",
        "artifact_version": "0.1",
        "profile": "solver_validation",
        "validation_passed": True,
        "acceptance_eligible": False,
    }
    for key, expected in expected_identity.items():
        if artifact.get(key) != expected:
            errors.append(f"solver validation {key} must be {expected}")
    spectrum_path = _resolve_payload_path(artifact.get("spectrum_config_path"), root, "spectrum config", errors)
    if spectrum_path != config.source_path.resolve():
        errors.append("solver validation spectrum config path mismatch")
    if artifact.get("spectrum_config_sha256") != raw_file_sha256(config.source_path):
        errors.append("solver validation spectrum config hash is stale")
    stage2_bindings = {
        "stage2_rebaseline_manifest_sha256": provenance.rebaseline_manifest_sha256,
        "stage2_rebaseline_approval_sha256": provenance.rebaseline_approval_sha256,
        "stage2_artifacts_sha256": provenance.stage2_artifacts_sha256,
        "hamiltonian_config_sha256": provenance.hamiltonian_config_sha256,
        "stage2_model_source_tree_sha256": provenance.stage2_model_source_tree_sha256,
    }
    for key, expected in stage2_bindings.items():
        if artifact.get(key) != expected:
            errors.append(f"solver validation {key} mismatch")
    if artifact.get("stage2_artifacts_sha256") != provenance.stage2_artifacts_sha256:
        errors.append("solver validation Stage 2 artifact hash mismatch")
    if artifact.get("stage3_solver_source_tree_sha256") != source_hash:
        errors.append("solver validation Stage 3 source hash is stale")
    if artifact.get("environment_fingerprint_sha256") != environment_hash:
        errors.append("solver validation environment fingerprint is stale")
    if artifact.get("environment_fingerprint") != environment:
        errors.append("solver validation environment fingerprint payload mismatch")
    expected_spec = {
        "which": config.eigen.eigsh.which,
        "tolerance": config.eigen.eigsh.tolerance,
        "maxiter": config.eigen.eigsh.maxiter,
        "ncv": config.eigen.eigsh.ncv,
        "v0_rule": config.eigen.eigsh.v0_rule,
        "num_states": config.eigen.num_states,
    }
    if artifact.get("eigsh_spec") != expected_spec:
        errors.append("solver validation eigsh spec does not exactly match SpectrumConfig")
    validation_config = config.eigen.solver_validation
    expected_thresholds = {
        "dense_config": {"solver": "scipy.linalg.eigh", "num_states_plus_one": config.eigen.num_states + 1},
        "near_degenerate_gap_threshold_GHz": validation_config.near_degenerate_gap_threshold_GHz,
        "partition_boundary_margin_GHz": validation_config.partition_boundary_margin_GHz,
        "projector_error_norm": validation_config.projector_error_norm,
        "projector_dense_tolerance": validation_config.projector_dense_tolerance,
        "projector_repeat_tolerance": validation_config.projector_repeat_tolerance,
    }
    for key, expected in expected_thresholds.items():
        if artifact.get(key) != expected:
            errors.append(f"solver validation {key} does not exactly match SpectrumConfig")

    expected_normative = {
        "seed_length": 166,
        "seed_sha256": "ED780C6FA5C04949197BEE6D43156B10391B1E6E3596F9CFA41D80D455FBF1A7",
        "block0_sha256": "2601A3794F3CD9A73B4B6B2D4DDDB15E3FC890D8593B2752672D366E83EA2950",
    }
    if artifact.get("v0_canonical_encoding_version") != "sha256_counter_v1":
        errors.append("solver validation v0 encoding version mismatch")
    if artifact.get("normative_test_vector") != expected_normative:
        errors.append("solver validation normative test vector mismatch")
    if artifact.get("normative_test_vector_passed") is not True:
        errors.append("solver validation normative test vector did not pass")

    approval_identity = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_solver_backend_validation_approval",
        "artifact_version": "0.1",
        "decision": "approved",
        "reviewer_role": "independent_test_review_ai",
        "validation_artifact_sha256": validation_hash,
    }
    for key, expected in approval_identity.items():
        if approval_payload.get(key) != expected:
            errors.append(f"solver validation approval {key} mismatch")
    if approval_payload.get("blocking_findings") != []:
        errors.append("approved solver validation contains blocking findings")
    required_approval_keys = set(approval_identity) | {"blocking_findings"}
    allowed_approval_keys = required_approval_keys | {"review_record_path"}
    if set(approval_payload) - allowed_approval_keys:
        errors.append("solver validation approval contains unsupported identity fields")
    if not required_approval_keys.issubset(approval_payload):
        errors.append("solver validation approval identity is incomplete")
    if "review_record_path" in approval_payload and (
        not isinstance(approval_payload["review_record_path"], str)
        or not approval_payload["review_record_path"].strip()
    ):
        errors.append("solver validation approval review_record_path must be a non-empty string")

    pilot, required_flux_keys = _validate_dense_pilot(artifact, config, provenance, root, errors)
    signatures = _expected_validation_signatures(config, errors)
    case_summary = _validate_cases(
        artifact.get("validation_cases"),
        required_flux_keys,
        signatures,
        config,
        errors,
    )
    expected_failed = case_summary["failed_cases"]
    if artifact.get("failed_cases") != expected_failed:
        errors.append("solver validation failed_cases does not match recomputed case failures")
    coverage_complete = case_summary["coverage_complete"]
    if artifact.get("coverage_complete") is not coverage_complete or not coverage_complete:
        errors.append("solver validation coverage_complete does not match required 4x7 coverage")

    aggregate_keys = (
        "max_gap_error_GHz",
        "max_repeat_gap_error_GHz",
        "max_projector_dense_error",
        "max_projector_repeat_error",
        "max_participation_dense_error",
        "max_participation_repeat_error",
        "max_metric_dense_error_GHz",
    )
    for key in aggregate_keys:
        reported = _finite_number(artifact.get(key))
        recomputed = case_summary["aggregates"].get(key)
        if reported is None:
            errors.append(f"solver validation {key} must be finite")
        elif recomputed is None or reported != recomputed:
            errors.append(f"solver validation {key} does not match recomputed cases")

    recomputed_passed = (
        artifact.get("normative_test_vector_passed") is True
        and coverage_complete
        and not expected_failed
        and all(case_summary["aggregates"].get(key) is not None for key in aggregate_keys)
    )
    if artifact.get("validation_passed") is not recomputed_passed or not recomputed_passed:
        errors.append("solver validation validation_passed does not match recomputed result")

    p50 = _validate_timing_map(artifact.get("p50_seconds_by_signature"), "p50", errors)
    p95 = _validate_timing_map(artifact.get("p95_seconds_by_signature"), "p95", errors)
    for key in ("3375", "4275"):
        if key in p50 and key in p95 and p95[key] < p50[key]:
            errors.append(f"solver validation p95 is below p50 for {key}")

    validated_error = case_summary["aggregates"].get("max_gap_error_GHz")
    if validated_error is None or not np.isfinite(validated_error):
        validated_error = 0.0
    spec = None
    if not errors:
        spec = ValidatedSolverSpec(
            backend="validated_eigsh",
            num_states=config.eigen.num_states,
            which=config.eigen.eigsh.which,
            tolerance=config.eigen.eigsh.tolerance,
            maxiter=config.eigen.eigsh.maxiter,
            ncv=config.eigen.eigsh.ncv,
            v0_rule=config.eigen.eigsh.v0_rule,
            stage2_artifacts_sha256=provenance.stage2_artifacts_sha256,
            validation_artifact_sha256=validation_hash,
            acceptance_eligible=True,
        )
    report = SolverBackendReport(
        ok=not errors,
        validated_spec=spec,
        validation_artifact_sha256=validation_hash,
        validation_approval_sha256=approval_hash,
        stage3_solver_source_tree_sha256=source_hash,
        environment_fingerprint_sha256=environment_hash,
        p95_seconds_by_signature=p95 if not errors else {},
        validated_solver_error_GHz=float(validated_error),
        errors=tuple(errors),
    )
    return report


def _validate_dense_pilot(
    artifact: Mapping[str, Any],
    config: SpectrumConfig,
    provenance: ProvenanceReport,
    root: Path,
    errors: list[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    pilot_path = _resolve_payload_path(artifact.get("dense_pilot_path"), root, "dense pilot", errors)
    if pilot_path is None:
        return {}, ()
    try:
        pilot = _load_mapping(pilot_path, "dense pilot")
    except ValueError as exc:
        errors.append(str(exc))
        return {}, ()
    _check_canonical_file(pilot_path, pilot, "dense pilot", errors)
    if artifact.get("dense_pilot_sha256") != _safe_raw_hash(pilot_path, "dense pilot", errors):
        errors.append("solver validation dense pilot hash mismatch")
    identity = {
        "schema_version": "0.1",
        "artifact_type": "stage_03_dense_pilot",
        "artifact_version": "0.1",
        "profile": "dense_pilot",
        "acceptance_eligible": False,
        "num_states": 12,
        "solver_backend": "dense_eigh",
    }
    for key, expected in identity.items():
        if pilot.get(key) != expected:
            errors.append(f"dense pilot {key} mismatch")
    pilot_config_path = _resolve_payload_path(pilot.get("spectrum_config_path"), root, "dense pilot config", errors)
    if pilot_config_path != config.source_path.resolve():
        errors.append("dense pilot spectrum config path mismatch")
    if pilot.get("spectrum_config_sha256") != raw_file_sha256(config.source_path):
        errors.append("dense pilot spectrum config hash mismatch")
    if pilot.get("stage2_artifacts_sha256") != provenance.stage2_artifacts_sha256:
        errors.append("dense pilot Stage 2 artifact hash mismatch")
    gap_report = pilot.get("stage2_dense_gap_consistency")
    if not isinstance(gap_report, Mapping) or gap_report.get("ok") is not True:
        errors.append("dense pilot Stage 2 gap consistency is invalid")
    elif (
        gap_report.get("compared_gap_count") != 12
        or (_finite_number(gap_report.get("max_abs_difference_GHz")) is None)
        or float(gap_report["max_abs_difference_GHz"]) > 1e-9
    ):
        errors.append("dense pilot Stage 2 gap consistency exceeds the frozen gate")

    expected_start = canonical_flux_text(config.flux_scan.start_phi0)
    expected_stop = canonical_flux_text(config.flux_scan.stop_phi0)
    if pilot.get("start_key") != expected_start or pilot.get("stop_key") != expected_stop:
        errors.append("dense pilot scan endpoints do not match SpectrumConfig")
    try:
        stage2_artifact = _load_mapping(config.source_hamiltonian_artifacts, "Stage 2 artifact")
        junctions = stage2_artifact.get("effective_junctions")
        couplers = [
            row
            for row in junctions
            if isinstance(row, Mapping) and row.get("mode") == "c"
        ] if isinstance(junctions, list) else []
        expected_idle = canonical_flux_text(couplers[0]["flux_bias_phi0"]) if len(couplers) == 1 else None
    except (KeyError, TypeError, ValueError) as exc:
        expected_idle = None
        errors.append(f"cannot derive dense pilot idle flux from Stage 2 artifact: {exc}")
    if expected_idle is None or pilot.get("idle_flux_key") != expected_idle:
        errors.append("dense pilot idle flux does not match Stage 2 artifact")

    points = pilot.get("points")
    point_keys = {
        row.get("flux_key")
        for row in points
        if isinstance(row, Mapping) and isinstance(row.get("flux_key"), str)
    } if isinstance(points, list) else set()
    if not isinstance(points, list) or len(point_keys) != len(points):
        errors.append("dense pilot points must have unique flux keys")

    required: set[str] = set()
    for field in ("idle_flux_key", "start_key", "stop_key"):
        key = _validated_flux_key(pilot.get(field), field, errors)
        if key is not None:
            required.add(key)
    candidates = pilot.get("candidates")
    by_name: dict[str, Mapping[str, Any]] = {}
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, Mapping) and isinstance(candidate.get("name"), str):
                by_name[candidate["name"]] = candidate
    if not isinstance(candidates, list) or len(candidates) != 2 or set(by_name) != {"q1-c", "c-q2"}:
        errors.append("dense pilot candidates must contain exactly q1-c and c-q2")
    for name in ("q1-c", "c-q2"):
        candidate = by_name.get(name, {})
        evidence_keys: list[str] = []
        for field in ("evidence_left_key", "minimum_key", "evidence_right_key"):
            key = _validated_flux_key(candidate.get(field), f"{name}.{field}", errors)
            if key is not None:
                required.add(key)
                evidence_keys.append(key)
                if key not in point_keys:
                    errors.append(f"dense pilot {name}.{field} is absent from pilot points")
        if len(evidence_keys) == 3 and not (
            Decimal(evidence_keys[0]) < Decimal(evidence_keys[1]) < Decimal(evidence_keys[2])
        ):
            errors.append(f"dense pilot {name} evidence keys are not strictly ordered")
    if len(required) != 7:
        errors.append("dense pilot must derive exactly seven solver-validation flux keys")
    return dict(pilot), tuple(sorted(required, key=Decimal))


def _expected_validation_signatures(
    config: SpectrumConfig,
    errors: list[str],
) -> dict[str, tuple[tuple[int, int, int], int]]:
    try:
        hamiltonian = load_hamiltonian_config(config.source_hamiltonian_config)
    except ValueError as exc:
        errors.append(f"cannot derive validation signatures: {exc}")
        return {}
    base = tuple(hamiltonian.basis.charge_cutoffs[mode] for mode in ("q1", "c", "q2"))
    signatures: dict[str, tuple[tuple[int, int, int], int]] = {}
    for name, mode_index in (("baseline", None), ("refined_q1", 0), ("refined_c", 1), ("refined_q2", 2)):
        values = list(base)
        if mode_index is not None:
            values[mode_index] += config.convergence.cutoff_increment
        signature = tuple(values)
        dimension = int(np.prod([2 * value + 1 for value in signature]))
        signatures[name] = (signature, dimension)
    return signatures


def _validate_cases(
    raw_cases: Any,
    flux_keys: tuple[str, ...],
    signatures: dict[str, tuple[tuple[int, int, int], int]],
    config: SpectrumConfig,
    errors: list[str],
) -> dict[str, Any]:
    expected_ids = {
        f"{name}@{flux_key}"
        for name in signatures
        for flux_key in flux_keys
    }
    if not isinstance(raw_cases, list):
        errors.append("solver validation validation_cases must be a list")
        raw_cases = []
    seen: list[str] = []
    failed: list[str] = []
    metric_rows: list[dict[str, float | None]] = []
    for index, raw_case in enumerate(raw_cases):
        case_errors: list[str] = []
        case = raw_case if isinstance(raw_case, Mapping) else {}
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or "@" not in case_id:
            case_errors.append("case_id is invalid")
            display_id = f"<case:{index}>"
            signature_name = ""
            flux_key = ""
        else:
            display_id = case_id
            signature_name, flux_key = case_id.split("@", 1)
            seen.append(case_id)
        expected_signature = signatures.get(signature_name)
        if expected_signature is None:
            case_errors.append("case signature name is invalid")
        else:
            signature, dimension = expected_signature
            if case.get("cutoff_signature") != list(signature):
                case_errors.append("cutoff signature mismatch")
            if case.get("dimension") != dimension:
                case_errors.append("dimension mismatch")
        if flux_key not in flux_keys or case.get("flux_key") != flux_key:
            case_errors.append("flux key mismatch")

        dense_gaps = _finite_number_list(case.get("dense_gaps_GHz"), config.eigen.num_states)
        eigsh_gaps = _finite_number_list(case.get("eigsh_gaps_GHz"), config.eigen.num_states)
        recomputed_gap = None
        if dense_gaps is None or eigsh_gaps is None:
            case_errors.append("dense/eigsh gaps must be finite length-48 arrays")
        else:
            recomputed_gap = float(np.max(np.abs(np.asarray(dense_gaps) - np.asarray(eigsh_gaps))))
        reported_gap = _finite_number(case.get("gap_dense_max_error_GHz"))
        if reported_gap is None or recomputed_gap is None or reported_gap != recomputed_gap:
            case_errors.append("reported dense gap error mismatch")
        elif recomputed_gap > config.eigen.solver_validation.gap_dense_tolerance_GHz:
            case_errors.append("dense gap error exceeds tolerance")
        repeat_gap = _finite_number(case.get("gap_repeat_max_error_GHz"))
        if repeat_gap is None or repeat_gap > 1e-10:
            case_errors.append("repeat gap error exceeds tolerance or is not finite")

        blocks = _validated_blocks(case.get("dense_block_index_ranges"), config.eigen.num_states, case_errors)
        boundary_gaps = _finite_number_list(case.get("boundary_gaps_GHz"), config.eigen.num_states)
        if boundary_gaps is None:
            case_errors.append("boundary gaps must be finite length-48 array")
        elif blocks is not None:
            _validate_partition_boundaries(blocks, boundary_gaps, config, case_errors)
        dense_projectors = _finite_number_list(case.get("projector_dense_errors_spectral_2"))
        repeat_projectors = _finite_number_list(case.get("projector_repeat_errors_spectral_2"))
        if blocks is None or dense_projectors is None or len(dense_projectors) != len(blocks):
            case_errors.append("dense projector errors do not match dense blocks")
        elif any(value > config.eigen.solver_validation.projector_dense_tolerance for value in dense_projectors):
            case_errors.append("dense projector error exceeds tolerance")
        if blocks is None or repeat_projectors is None or len(repeat_projectors) != len(blocks):
            case_errors.append("repeat projector errors do not match dense blocks")
        elif any(value > config.eigen.solver_validation.projector_repeat_tolerance for value in repeat_projectors):
            case_errors.append("repeat projector error exceeds tolerance")
        if case.get("truncation_status") != "not_truncated":
            case_errors.append("truncation_status must be not_truncated")

        physics = case.get("physics_comparison")
        part_dense = part_repeat = metric_dense = None
        if not isinstance(physics, Mapping):
            case_errors.append("physics comparison must be a mapping")
        else:
            assignments = [
                physics.get("assignments_dense"),
                physics.get("assignments_eigsh_run1"),
                physics.get("assignments_eigsh_run2"),
            ]
            if not all(isinstance(value, Mapping) for value in assignments) or not (
                assignments[0] == assignments[1] == assignments[2]
            ):
                case_errors.append("physics assignments are not identical")
            part_dense = _finite_number(physics.get("participation_dense_max_error"))
            part_repeat = _finite_number(physics.get("participation_repeat_max_error"))
            metric_dense = _finite_number(physics.get("metric_dense_max_error_GHz"))
            if part_dense is None or part_dense > 1e-6:
                case_errors.append("dense participation error exceeds tolerance or is not finite")
            if part_repeat is None or part_repeat > 1e-8:
                case_errors.append("repeat participation error exceeds tolerance or is not finite")
            if metric_dense is None or metric_dense > config.eigen.solver_validation.gap_dense_tolerance_GHz:
                case_errors.append("dense metric error exceeds tolerance or is not finite")
            if physics.get("passed") is not True:
                case_errors.append("physics comparison did not pass")
        if case.get("passed") is not True:
            case_errors.append("case passed must be true")
        if case_errors:
            failed.append(display_id)
            errors.extend(f"solver validation {display_id}: {message}" for message in case_errors)
        metric_rows.append(
            {
                "max_gap_error_GHz": recomputed_gap,
                "max_repeat_gap_error_GHz": repeat_gap,
                "max_projector_dense_error": max(dense_projectors, default=0.0) if dense_projectors is not None else None,
                "max_projector_repeat_error": max(repeat_projectors, default=0.0) if repeat_projectors is not None else None,
                "max_participation_dense_error": part_dense,
                "max_participation_repeat_error": part_repeat,
                "max_metric_dense_error_GHz": metric_dense,
            }
        )
    if len(seen) != len(set(seen)):
        errors.append("solver validation case_id values must be unique")
    coverage_complete = len(raw_cases) == len(expected_ids) and set(seen) == expected_ids and len(seen) == len(set(seen))
    if not coverage_complete:
        errors.append("solver validation cases do not exactly cover required 4x7 matrix")
    aggregate_keys = (
        "max_gap_error_GHz",
        "max_repeat_gap_error_GHz",
        "max_projector_dense_error",
        "max_projector_repeat_error",
        "max_participation_dense_error",
        "max_participation_repeat_error",
        "max_metric_dense_error_GHz",
    )
    aggregates: dict[str, float | None] = {}
    for key in aggregate_keys:
        values = [row[key] for row in metric_rows]
        aggregates[key] = max(values) if values and all(value is not None for value in values) else None
    return {"coverage_complete": coverage_complete, "failed_cases": failed, "aggregates": aggregates}


def _validated_blocks(raw: Any, count: int, errors: list[str]) -> list[tuple[int, int]] | None:
    if not isinstance(raw, list):
        errors.append("dense blocks must be a list")
        return None
    blocks: list[tuple[int, int]] = []
    cursor = 0
    for item in raw:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in item)
        ):
            errors.append("dense block range is invalid")
            return None
        left, right = item
        if left != cursor or right < left or right >= count:
            errors.append("dense blocks must be a complete contiguous partition")
            return None
        blocks.append((left, right))
        cursor = right + 1
    if cursor != count:
        errors.append("dense blocks must cover indices 0..47")
        return None
    return blocks


def _validate_partition_boundaries(
    blocks: list[tuple[int, int]],
    gaps: list[float],
    config: SpectrumConfig,
    errors: list[str],
) -> None:
    threshold = config.eigen.solver_validation.near_degenerate_gap_threshold_GHz
    margin = config.eigen.solver_validation.partition_boundary_margin_GHz
    partition_edges = {right for _, right in blocks[:-1]}
    for index, gap in enumerate(gaps[:-1]):
        if abs(gap - threshold) <= margin:
            errors.append("near-degenerate partition boundary is ambiguous")
            return
        if index in partition_edges and gap <= threshold:
            errors.append("dense block boundary contradicts boundary gaps")
            return
        if index not in partition_edges and gap > threshold:
            errors.append("dense block interior contradicts boundary gaps")
            return
    if gaps[-1] <= threshold + margin:
        errors.append("dense validation block is truncated at state 48")


def _validate_timing_map(raw: Any, display: str, errors: list[str]) -> dict[str, float]:
    if not isinstance(raw, Mapping) or set(raw) != {"3375", "4275"}:
        errors.append(f"solver validation {display} timings must contain exactly 3375 and 4275")
        return {}
    result: dict[str, float] = {}
    for key in ("3375", "4275"):
        value = _finite_number(raw.get(key))
        if value is None or value <= 0.0:
            errors.append(f"solver validation {display} timing for {key} must be finite and positive")
        else:
            result[key] = value
    return result


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        return None
    converted = float(value)
    return converted if np.isfinite(converted) else None


def _finite_number_list(value: Any, length: int | None = None) -> list[float] | None:
    if not isinstance(value, list) or (length is not None and len(value) != length):
        return None
    converted = [_finite_number(item) for item in value]
    if any(item is None for item in converted):
        return None
    return [float(item) for item in converted]


def _validated_flux_key(value: Any, display: str, errors: list[str]) -> str | None:
    if not isinstance(value, str):
        errors.append(f"dense pilot {display} must be a canonical flux key")
        return None
    try:
        if canonical_flux_text(value) != value:
            raise ValueError
        Decimal(value)
    except Exception:
        errors.append(f"dense pilot {display} must be a canonical flux key")
        return None
    return value


def _resolve_project_path(path: Path, root: Path) -> Path:
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _resolve_payload_path(value: Any, root: Path, display: str, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{display} path must be a non-empty string")
        return None
    return _resolve_project_path(Path(value), root)


def _safe_raw_hash(path: Path, display: str, errors: list[str]) -> str | None:
    try:
        return raw_file_sha256(path)
    except OSError as exc:
        errors.append(f"cannot hash {display}: {exc}")
        return None


def _check_canonical_file(path: Path, payload: Mapping[str, Any], display: str, errors: list[str]) -> None:
    try:
        canonical = canonical_json_bytes(payload)
        raw = path.read_bytes()
    except (OSError, TypeError, ValueError) as exc:
        errors.append(f"cannot verify canonical {display}: {exc}")
        return
    if raw != canonical:
        errors.append(f"{display} bytes are not canonical")


def build_nonacceptance_solver_report(config: SpectrumConfig, provenance: ProvenanceReport) -> SolverBackendReport:
    root = find_repository_root(config.source_path)
    source_hash = stage3_solver_source_tree_sha256(root)
    _, environment_hash = environment_fingerprint()
    spec = ValidatedSolverSpec(
        backend="dense_eigh",
        num_states=config.eigen.num_states,
        which=config.eigen.eigsh.which,
        tolerance=config.eigen.eigsh.tolerance,
        maxiter=config.eigen.eigsh.maxiter,
        ncv=config.eigen.eigsh.ncv,
        v0_rule=config.eigen.eigsh.v0_rule,
        stage2_artifacts_sha256=provenance.stage2_artifacts_sha256,
        validation_artifact_sha256=None,
        acceptance_eligible=False,
    )
    return SolverBackendReport(
        ok=True,
        validated_spec=spec,
        validation_artifact_sha256=None,
        validation_approval_sha256=None,
        stage3_solver_source_tree_sha256=source_hash,
        environment_fingerprint_sha256=environment_hash,
        p95_seconds_by_signature={"3375": 0.0, "4275": 0.0},
        validated_solver_error_GHz=0.0,
    )


def _load_mapping(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {description}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {description}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} root must be a mapping")
    return value
