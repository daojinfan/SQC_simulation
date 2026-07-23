"""Reader-only verifier for archived spectroscopy scan v0.3 evidence."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from sqvm.calibration.spectroscopy_evidence import SCAN_ARTIFACT_VERSION
from sqvm.calibration.spectroscopy_run import (
    SPECTROSCOPY_SCAN_WORKFLOW_ID,
    _verify_dataset_coordinates,
    _verify_recommendation,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import canonical_json_bytes as dataset_json_bytes
from sqvm.storage.archive_format import EvidenceReader


_METADATA_LIMIT = 1024 * 1024
_STREAM_CHUNK = 64 * 1024
_TERMINAL = frozenset({"manifest.json", "verification_report.json", "receipt.json"})
_ROOT_FILES = frozenset({"workflow.json", "dataset.json", *_TERMINAL})


class SpectroscopyReaderVerificationError(ValueError):
    """The bounded evidence reader does not expose a valid v0.3 scan."""


def verify_qubit_spectroscopy_scan_evidence(reader: EvidenceReader) -> None:
    """Verify a completed v0.3 scan through a bounded, path-free reader.

    The function intentionally never materializes a directory and never calls
    ``read_sqrun_payload``.  Non-metadata content is hashed in fixed chunks.
    """

    try:
        paths = _validated_paths(reader)
        inventory = {path: _stream_inventory(reader, path) for path in paths}
        workflow_raw, workflow = _metadata(reader, "workflow.json", "workflow", canonical_json_bytes)
        receipt_raw, receipt = _metadata(reader, "receipt.json", "receipt", canonical_json_bytes)
        dataset_raw, dataset = _metadata(reader, "dataset.json", "dataset", dataset_json_bytes)
        manifest_raw, manifest = _metadata(reader, "manifest.json", "manifest", canonical_json_bytes)
        report_raw, report = _metadata(reader, "verification_report.json", "verification report", canonical_json_bytes)

        workflow_sha256 = _sha(workflow_raw)
        dataset_sha256 = _sha(dataset_raw)
        _verify_workflow(workflow, dataset, receipt, workflow_sha256, dataset_sha256)
        _verify_evidence_closure(
            paths, inventory, workflow, receipt, manifest_raw, manifest, report_raw, report,
            workflow_sha256, dataset_sha256,
        )
    except SpectroscopyReaderVerificationError:
        raise
    except Exception as exc:
        raise SpectroscopyReaderVerificationError(str(exc) or "spectroscopy reader verification failed") from exc


def _validated_paths(reader: EvidenceReader) -> tuple[str, ...]:
    paths = reader.paths()
    if not isinstance(paths, tuple) or not paths:
        raise SpectroscopyReaderVerificationError("evidence reader paths are invalid")
    if any(not isinstance(path, str) or not path or "\\" in path or "\x00" in path for path in paths):
        raise SpectroscopyReaderVerificationError("evidence path is invalid")
    if len(paths) != len(set(paths)) or len(paths) != len({path.casefold() for path in paths}):
        raise SpectroscopyReaderVerificationError("evidence paths are duplicate or case-colliding")
    for path in paths:
        parts = path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise SpectroscopyReaderVerificationError("evidence path escapes root")
    root_files = {path for path in paths if "/" not in path}
    execution = {path for path in paths if path.startswith("execution/")}
    if root_files != _ROOT_FILES or not execution or len(root_files) + len(execution) != len(paths):
        raise SpectroscopyReaderVerificationError("spectroscopy evidence root file set is invalid")
    return tuple(sorted(paths))


def _metadata(reader: EvidenceReader, path: str, label: str, canonicalizer):
    try:
        raw = reader.read_bytes(path, maximum_bytes=_METADATA_LIMIT)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except Exception as exc:
        raise SpectroscopyReaderVerificationError(f"cannot read spectroscopy {label}") from exc
    if not isinstance(value, dict) or raw != canonicalizer(value):
        raise SpectroscopyReaderVerificationError(f"spectroscopy {label} is not canonical JSON")
    _finite_tree(value)
    return raw, value


def _stream_inventory(reader: EvidenceReader, path: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    try:
        with reader.open_binary(path) as stream:
            chunk_size = _STREAM_CHUNK
            while True:
                try:
                    chunk = stream.read(chunk_size)
                except Exception:
                    # ZipEvidenceReader rejects a request larger than its
                    # remaining entry instead of returning a short read.
                    if chunk_size == 1:
                        raise
                    chunk_size //= 2
                    continue
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise SpectroscopyReaderVerificationError("evidence stream returned non-bytes")
                size += len(chunk)
                digest.update(chunk)
                chunk_size = _STREAM_CHUNK
    except SpectroscopyReaderVerificationError:
        raise
    except Exception as exc:
        raise SpectroscopyReaderVerificationError(f"cannot stream evidence {path}") from exc
    return {"path": path, "byte_length": size, "raw_sha256": digest.hexdigest().upper()}


def _verify_workflow(
    workflow: Mapping[str, Any], dataset: Mapping[str, Any], receipt: Mapping[str, Any],
    workflow_sha256: str, dataset_sha256: str,
) -> None:
    run_id = workflow.get("run_id")
    request = workflow.get("request")
    binding = workflow.get("dataset")
    analysis = workflow.get("analysis")
    parent = workflow.get("parent_configuration")
    if (
        workflow.get("workflow_id") != SPECTROSCOPY_SCAN_WORKFLOW_ID
        or workflow.get("artifact_type") != "qubit_spectroscopy_scan"
        or workflow.get("artifact_version") != SCAN_ARTIFACT_VERSION
        or workflow.get("status") != "completed"
        or not isinstance(run_id, str) or not run_id
        or workflow.get("archive_eligible") is not True
        or not isinstance(request, Mapping) or request.get("run_phase") != "scan"
    ):
        raise SpectroscopyReaderVerificationError("spectroscopy workflow identity is invalid")
    if (
        dataset.get("run_phase") != "scan"
        or dataset.get("targets") != request.get("targets")
        or dataset.get("execution_mode") != request.get("execution_mode")
    ):
        raise SpectroscopyReaderVerificationError("spectroscopy dataset request binding is invalid")
    _verify_dataset_coordinates(request, dataset)
    if (
        not isinstance(binding, Mapping) or binding.get("path") != "dataset.json"
        or binding.get("sha256") != dataset_sha256
        or not isinstance(analysis, Mapping) or analysis.get("dataset_sha256") != dataset_sha256
        or analysis.get("recommendation_eligible") is not False
    ):
        raise SpectroscopyReaderVerificationError("spectroscopy dataset or analysis binding is invalid")
    if (
        receipt.get("artifact_type") != "qubit_spectroscopy_scan_receipt"
        or receipt.get("artifact_version") != SCAN_ARTIFACT_VERSION
        or receipt.get("status") != "completed" or receipt.get("run_id") != run_id
        or receipt.get("workflow_sha256") != workflow_sha256
        or receipt.get("dataset_sha256") != dataset_sha256
        or receipt.get("archive_eligible") is not True
        or not isinstance(parent, Mapping) or receipt.get("parent_configuration_sha256") != parent.get("sha256")
    ):
        raise SpectroscopyReaderVerificationError("spectroscopy receipt binding is invalid")
    _verify_recommendation(workflow, dataset, dataset_sha256)
    if (
        receipt.get("recommendation_id") != workflow.get("recommendation_id")
        or receipt.get("recommendation_eligible") is not workflow.get("recommendation_eligible")
    ):
        raise SpectroscopyReaderVerificationError("spectroscopy recommendation receipt is invalid")


def _verify_evidence_closure(
    paths: tuple[str, ...], inventory: Mapping[str, Mapping[str, Any]], workflow: Mapping[str, Any],
    receipt: Mapping[str, Any], manifest_raw: bytes, manifest: Mapping[str, Any], report_raw: bytes,
    report: Mapping[str, Any], workflow_sha256: str, dataset_sha256: str,
) -> None:
    payload = [dict(inventory[path]) for path in paths if path not in _TERMINAL]
    execution = [dict(inventory[path]) for path in paths if path.startswith("execution/")]
    expected_manifest = {
        "schema_version": "0.1", "artifact_type": "stage_07_qubit_spectroscopy_scan_manifest",
        "artifact_version": SCAN_ARTIFACT_VERSION, "run_id": workflow["run_id"],
        "workflow_id": workflow["workflow_id"], "status": "completed",
        "payload_files": payload, "execution_files": execution,
    }
    if dict(manifest) != expected_manifest:
        raise SpectroscopyReaderVerificationError("spectroscopy evidence manifest inventory is invalid")
    manifest_sha256 = _sha(manifest_raw)
    expected_report = {
        "schema_version": "0.1", "artifact_type": "stage_07_qubit_spectroscopy_scan_verification_report",
        "artifact_version": SCAN_ARTIFACT_VERSION, "run_id": workflow["run_id"], "status": "completed",
        "ok": True, "manifest_sha256": manifest_sha256,
        "checks": [
            {"name": "exact_root_file_set", "passed": True},
            {"name": "no_follow_execution_inventory", "passed": True},
            {"name": "payload_hashes_bound", "passed": True},
        ], "blocking_reasons": [],
    }
    if dict(report) != expected_report:
        raise SpectroscopyReaderVerificationError("spectroscopy evidence verification report is invalid")
    expected_receipt = {
        "schema_version": "0.1", "artifact_type": "qubit_spectroscopy_scan_receipt",
        "artifact_version": SCAN_ARTIFACT_VERSION, "run_id": workflow["run_id"],
        "recommendation_id": workflow["recommendation_id"], "status": "completed",
        "workflow_sha256": workflow_sha256, "dataset_sha256": dataset_sha256,
        "manifest_sha256": manifest_sha256, "verification_report_sha256": _sha(report_raw),
        "recommendation_eligible": workflow["recommendation_eligible"],
        "parent_configuration_sha256": workflow["parent_configuration"]["sha256"], "archive_eligible": True,
    }
    if dict(receipt) != expected_receipt:
        raise SpectroscopyReaderVerificationError("spectroscopy evidence receipt is invalid")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite_tree(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
    elif isinstance(value, list):
        for item in value:
            _finite_tree(item)
        return
    elif isinstance(value, Mapping):
        for item in value.values():
            _finite_tree(item)
        return
    raise SpectroscopyReaderVerificationError("non-finite or unsupported spectroscopy value")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


__all__ = ["SpectroscopyReaderVerificationError", "verify_qubit_spectroscopy_scan_evidence"]
