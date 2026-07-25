from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
from sqvm.calibration.spectroscopy_evidence import write_completed_evidence
from sqvm.calibration.spectroscopy_reader import (
    SpectroscopyReaderVerificationError,
    verify_qubit_spectroscopy_scan_evidence,
)
from sqvm.calibration.spectroscopy_run import (
    run_qubit_spectroscopy_scan,
    verify_qubit_spectroscopy_scan,
)
from sqvm.storage.archive_format import DirectoryEvidenceReader
from sqvm.storage.archive_verify import ArchiveLimits, ZipEvidenceReader, verify_sqrun
from sqvm.storage.errors import ArchiveFormatError
from sqvm.storage.inventory import inventory_tree
from sqvm.storage.models import ArchiveEntry
from sqvm.storage.workflow_verifiers import (
    SPECTROSCOPY_VERIFIER_ID,
    SPECTROSCOPY_VERIFIER_VERSION,
    archive_evidence_verifier_registry,
    get_workflow_evidence_verifier,
    workflow_evidence_verifier_registry,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_bytes
from tests.support.contexts import spectroscopy_context as _context, spectroscopy_result as _result, single_spectroscopy_request as _single_request


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "configs/calibration/platform_uncalibrated_v1.json"


def _install_fake_circuits(monkeypatch) -> None:
    def fake_run(circuits, _context_value, output_root, _repository_root, **kwargs):
        execution_root = Path(output_root)
        results = []
        for circuit in circuits:
            evidence_root = execution_root / "circuits" / circuit.circuit_id
            evidence_root.mkdir(parents=True)
            (evidence_root / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
            results.append(replace(
                _result(circuit.circuit_id, 0.8, 0.19, 0.0, 0.0),
                circuit_sha256=sha256_bytes(circuit.source.encode("utf-8")),
                readout_qubit=tuple(tuple(group) for group in kwargs["readout_qubit"]),
                evidence_root=evidence_root, model_evidence_root=evidence_root,
            ))
        return tuple(results)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


@pytest.fixture
def scan_root(monkeypatch, tmp_path: Path):
    _install_fake_circuits(monkeypatch)
    # The production publisher confines the run beneath its repository root.
    base = ROOT / "tmp" / f"reader_verifier_{tmp_path.name}"
    target = base / "scan"
    try:
        run = run_qubit_spectroscopy_scan(
            replace(_single_request(), run_phase="scan"), _context(), PARENT, target, ROOT, timeout_s=10.0,
        )
        yield run.root
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _directory_reader(root: Path) -> DirectoryEvidenceReader:
    inventory = inventory_tree(root, confinement_root=root)
    entries = tuple(
        ArchiveEntry(row.relative_path, row.logical_bytes, hashlib.sha256((root / row.relative_path).read_bytes()).hexdigest().upper())
        for row in inventory.files
    )
    return DirectoryEvidenceReader(root, entries)


def _registered() -> tuple[str, str, object]:
    value = get_workflow_evidence_verifier("qubit_spectroscopy_scan_v1", "0.3")
    assert value == (SPECTROSCOPY_VERIFIER_ID, SPECTROSCOPY_VERIFIER_VERSION, verify_qubit_spectroscopy_scan_evidence)
    return value


def test_v03_reader_and_zip_reader_verify_real_scan(scan_root: Path, tmp_path: Path):
    verifier_id, verifier_version, verifier = _registered()
    verifier(_directory_reader(scan_root))
    archive = tmp_path / "scan.sqrun"
    from sqvm.storage.archive_format import write_sqrun

    write_sqrun(
        scan_root, archive, source_verifier_id=verifier_id, source_verifier_version=verifier_version,
        source_verifier=verifier,
    )
    registry = {(verifier_id, verifier_version): verifier}
    bundle = verify_sqrun(archive, verifier_registry=registry, require_source_verified=True)
    assert bundle.source_verified is True
    verify_qubit_spectroscopy_scan_evidence(ZipEvidenceReader(archive, bundle.entries, ArchiveLimits()))


def test_v03_reader_remains_compatible_with_pre_batch_scan(scan_root: Path):
    workflow = json.loads((scan_root / "workflow.json").read_text("utf-8"))
    workflow.pop("runtime_batch")
    (scan_root / "workflow.json").write_bytes(canonical_json_bytes(workflow))
    shutil.rmtree(scan_root / "execution" / "batch")
    for name in ("manifest.json", "verification_report.json", "receipt.json"):
        (scan_root / name).unlink()
    write_completed_evidence(
        scan_root,
        run_id=workflow["run_id"],
        recommendation_id=workflow["recommendation_id"],
        workflow_id=workflow["workflow_id"],
        workflow_sha256=hashlib.sha256((scan_root / "workflow.json").read_bytes()).hexdigest().upper(),
        dataset_sha256=hashlib.sha256((scan_root / "dataset.json").read_bytes()).hexdigest().upper(),
        parent_configuration_sha256=workflow["parent_configuration"]["sha256"],
        recommendation_eligible=workflow["recommendation_eligible"],
    )

    assert verify_qubit_spectroscopy_scan(scan_root)
    verify_qubit_spectroscopy_scan_evidence(_directory_reader(scan_root))


def test_registry_is_frozen_and_only_v03_is_archivable():
    assert get_workflow_evidence_verifier("qubit_spectroscopy_scan_v1", "0.1") is None
    assert get_workflow_evidence_verifier("qubit_spectroscopy_scan_v1", "0.2") is None
    assert get_workflow_evidence_verifier("unknown", "0.3") is None
    assert get_workflow_evidence_verifier(None, "0.3") is None
    assert get_workflow_evidence_verifier("qubit_spectroscopy_scan_v1", None) is None
    assert get_workflow_evidence_verifier([], "0.3") is None
    with pytest.raises(TypeError):
        workflow_evidence_verifier_registry()[("unknown", "0.3")] = ("x", "x", lambda _reader: None)
    archive_registry = archive_evidence_verifier_registry()
    assert archive_registry == {
        (SPECTROSCOPY_VERIFIER_ID, SPECTROSCOPY_VERIFIER_VERSION): verify_qubit_spectroscopy_scan_evidence,
    }
    with pytest.raises(TypeError):
        archive_registry[("unknown", "0.3")] = lambda _reader: None


@pytest.mark.parametrize("artifact_version", ["0.1", "0.2"])
def test_reader_rejects_legacy_scan_evidence(scan_root: Path, artifact_version: str):
    """A valid legacy artifact is read-compatible but deliberately unarchivable."""

    workflow = json.loads((scan_root / "workflow.json").read_text("utf-8"))
    workflow["artifact_version"] = artifact_version
    workflow.pop("archive_eligible")
    if artifact_version == "0.1":
        workflow["recommendation_eligible"] = False
        workflow["candidates"] = []
    (scan_root / "workflow.json").write_bytes(canonical_json_bytes(workflow))
    workflow_sha = hashlib.sha256((scan_root / "workflow.json").read_bytes()).hexdigest().upper()

    manifest = json.loads((scan_root / "manifest.json").read_text("utf-8"))
    manifest["artifact_version"] = artifact_version
    workflow_row = next(row for row in manifest["payload_files"] if row["path"] == "workflow.json")
    workflow_row["byte_length"] = (scan_root / "workflow.json").stat().st_size
    workflow_row["raw_sha256"] = workflow_sha
    (scan_root / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    manifest_sha = hashlib.sha256((scan_root / "manifest.json").read_bytes()).hexdigest().upper()
    report = json.loads((scan_root / "verification_report.json").read_text("utf-8"))
    report["artifact_version"] = artifact_version
    report["manifest_sha256"] = manifest_sha
    (scan_root / "verification_report.json").write_bytes(canonical_json_bytes(report))
    receipt = json.loads((scan_root / "receipt.json").read_text("utf-8"))
    receipt["artifact_version"] = artifact_version
    receipt["workflow_sha256"] = workflow_sha
    receipt["manifest_sha256"] = manifest_sha
    receipt["verification_report_sha256"] = hashlib.sha256(
        (scan_root / "verification_report.json").read_bytes()
    ).hexdigest().upper()
    receipt["archive_eligible"] = False
    (scan_root / "receipt.json").write_bytes(canonical_json_bytes(receipt))

    assert verify_qubit_spectroscopy_scan(scan_root)
    assert get_workflow_evidence_verifier("qubit_spectroscopy_scan_v1", artifact_version) is None
    with pytest.raises(SpectroscopyReaderVerificationError, match="workflow identity"):
        verify_qubit_spectroscopy_scan_evidence(_directory_reader(scan_root))


@pytest.mark.parametrize("relative, raw", [
    ("workflow.json", b'{"x":1,"x":2}'),
    ("dataset.json", b'{"value":NaN}'),
])
def test_reader_rejects_duplicate_keys_and_nan(scan_root: Path, relative: str, raw: bytes):
    (scan_root / relative).write_bytes(raw)
    with pytest.raises(SpectroscopyReaderVerificationError):
        verify_qubit_spectroscopy_scan_evidence(_directory_reader(scan_root))


@pytest.mark.parametrize("relative", [
    "workflow.json", "dataset.json", "receipt.json", "manifest.json", "verification_report.json",
    "execution/circuits/" + "{circuit}" + "/result.bin",
])
def test_path_and_reader_reject_the_same_tampered_v03_evidence(scan_root: Path, relative: str):
    if "{circuit}" in relative:
        relative = next(path.relative_to(scan_root).as_posix() for path in (scan_root / "execution").rglob("result.bin"))
    path = scan_root / relative
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(Exception):
        verify_qubit_spectroscopy_scan(scan_root)
    with pytest.raises(SpectroscopyReaderVerificationError):
        verify_qubit_spectroscopy_scan_evidence(_directory_reader(scan_root))


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_reader_rejects_missing_or_extra_evidence(scan_root: Path, mutation: str):
    execution = scan_root / "execution"
    if mutation == "missing":
        next(execution.rglob("result.bin")).unlink()
    else:
        (execution / "unexpected.bin").write_bytes(b"unexpected")
    with pytest.raises(Exception):
        verify_qubit_spectroscopy_scan(scan_root)
    with pytest.raises(SpectroscopyReaderVerificationError):
        verify_qubit_spectroscopy_scan_evidence(_directory_reader(scan_root))


def test_writer_rejects_symlink_and_hardlink_source_before_reader_verification(scan_root: Path, tmp_path: Path):
    link = scan_root / "execution" / "unsafe-link"
    os.symlink(scan_root / "dataset.json", link)
    verifier_id, verifier_version, verifier = _registered()
    from sqvm.storage.archive_format import write_sqrun

    with pytest.raises(ArchiveFormatError):
        write_sqrun(scan_root, tmp_path / "link.sqrun", source_verifier_id=verifier_id, source_verifier_version=verifier_version, source_verifier=verifier)
    link.unlink()
    hardlink = scan_root / "execution" / "unsafe-hardlink.bin"
    os.link(next((scan_root / "execution").rglob("result.bin")), hardlink)
    with pytest.raises(ArchiveFormatError):
        write_sqrun(scan_root, tmp_path / "hardlink.sqrun", source_verifier_id=verifier_id, source_verifier_version=verifier_version, source_verifier=verifier)


class _BoundedStream:
    def __init__(self, stream) -> None:
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= 64 * 1024
        return self._stream.read(size)

    def close(self) -> None:
        self._stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()


class _BoundedReader:
    def __init__(self, delegate) -> None:
        self._delegate = delegate

    def paths(self):
        return self._delegate.paths()

    def read_bytes(self, path: str, *, maximum_bytes: int):
        return self._delegate.read_bytes(path, maximum_bytes=maximum_bytes)

    def open_binary(self, path: str):
        return _BoundedStream(self._delegate.open_binary(path))


def test_reader_hashes_large_execution_file_in_bounded_chunks(scan_root: Path):
    large = scan_root / "execution" / "large.bin"
    large.write_bytes(b"x" * (2 * 1024 * 1024 + 3))
    workflow = json.loads((scan_root / "workflow.json").read_text("utf-8"))
    (scan_root / "manifest.json").unlink(); (scan_root / "verification_report.json").unlink(); (scan_root / "receipt.json").unlink()
    write_completed_evidence(
        scan_root, run_id=workflow["run_id"], recommendation_id=workflow["recommendation_id"],
        workflow_id=workflow["workflow_id"], workflow_sha256=hashlib.sha256((scan_root / "workflow.json").read_bytes()).hexdigest().upper(),
        dataset_sha256=hashlib.sha256((scan_root / "dataset.json").read_bytes()).hexdigest().upper(),
        parent_configuration_sha256=workflow["parent_configuration"]["sha256"],
        recommendation_eligible=workflow["recommendation_eligible"],
    )
    verify_qubit_spectroscopy_scan_evidence(_BoundedReader(_directory_reader(scan_root)))
