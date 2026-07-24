from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

import asyncio
import json
import os
from pathlib import Path
from dataclasses import replace
import subprocess

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
import sqvm.calibration.spectroscopy_run as run_module
from sqvm.calibration.spectroscopy_run import (
    SpectroscopyRunError,
    run_qubit_spectroscopy_scan,
    verify_qubit_spectroscopy_scan,
)
from tests.support.contexts import spectroscopy_context as _context, spectroscopy_result as _result, single_spectroscopy_request as _single_request


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "configs/calibration/platform_uncalibrated_v1.json"


def _install_runner(monkeypatch):
    def fake_run(circuits, _context_value, output_root, _repository_root, **_kwargs):
        execution_root = Path(output_root)
        results = []
        for circuit in circuits:
            evidence_root = execution_root / "circuits" / circuit.circuit_id
            evidence_root.mkdir(parents=True)
            (evidence_root / "result.bin").write_bytes(circuit.circuit_id.encode("ascii"))
            results.append(
                replace(
                    _result(circuit.circuit_id, 0.8, 0.19, 0.0, 0.0),
                    evidence_root=evidence_root,
                    model_evidence_root=evidence_root,
                )
            )
        return tuple(results)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


def _scan_request():
    return replace(_single_request(), run_phase="scan")


@pytest.fixture
def evidence_run(monkeypatch, tmp_path: Path):
    _install_runner(monkeypatch)
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    try:
        run = run_qubit_spectroscopy_scan(
            _scan_request(), _context(), PARENT, target, ROOT, timeout_s=10.0
        )
        yield run
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_scan_v03_binds_a_no_follow_execution_closure(evidence_run):
    root = evidence_run.root
    workflow = json.loads((root / "workflow.json").read_text("utf-8"))
    manifest = json.loads((root / "manifest.json").read_text("utf-8"))
    report = json.loads((root / "verification_report.json").read_text("utf-8"))
    receipt = json.loads((root / "receipt.json").read_text("utf-8"))

    assert workflow["artifact_version"] == "0.3"
    assert workflow["archive_eligible"] is True
    assert set(manifest) == {
        "schema_version", "artifact_type", "artifact_version", "run_id",
        "workflow_id", "status", "payload_files", "execution_files",
    }
    assert all(row["path"].startswith("execution/") for row in manifest["execution_files"])
    assert report["manifest_sha256"]
    assert receipt["archive_eligible"] is True
    assert verify_qubit_spectroscopy_scan(root)


@pytest.mark.parametrize("mutation", ("missing", "extra", "tamper"))
def test_scan_v03_rejects_execution_closure_changes(evidence_run, mutation: str):
    execution = evidence_run.root / "execution"
    payload = next(execution.rglob("result.bin"))
    if mutation == "missing":
        payload.unlink()
    elif mutation == "extra":
        (execution / "unexpected.bin").write_bytes(b"unexpected")
    else:
        payload.write_bytes(b"tampered")

    with pytest.raises(SpectroscopyRunError, match="execution|inventory|payload"):
        verify_qubit_spectroscopy_scan(evidence_run.root)


def test_scan_v03_rejects_execution_link(evidence_run):
    link = evidence_run.root / "execution" / "unsafe-link"
    try:
        os.symlink(evidence_run.root / "dataset.json", link)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink unavailable in test environment: {exc}")

    with pytest.raises(SpectroscopyRunError, match="link|unsafe"):
        verify_qubit_spectroscopy_scan(evidence_run.root)


def test_scan_v03_rejects_execution_hard_link(evidence_run):
    execution = evidence_run.root / "execution"
    source = next(execution.rglob("result.bin"))
    try:
        os.link(source, execution / "unsafe-hard-link.bin")
    except OSError as exc:
        pytest.skip(f"hard links unavailable in test environment: {exc}")

    with pytest.raises(SpectroscopyRunError, match="hard link"):
        verify_qubit_spectroscopy_scan(evidence_run.root)


def test_scan_v03_rejects_an_ancestor_symlink(evidence_run):
    link_parent = evidence_run.root.parent.parent / f"linked-parent-{evidence_run.run_id}"
    try:
        os.symlink(evidence_run.root.parent, link_parent, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink unavailable in test environment: {exc}")
    try:
        alias = link_parent / evidence_run.root.name
        with pytest.raises(SpectroscopyRunError, match="ancestor link"):
            verify_qubit_spectroscopy_scan(alias)
    finally:
        link_parent.unlink(missing_ok=True)


def test_scan_v03_rejects_intermediate_execution_junction(evidence_run):
    if os.name != "nt":
        pytest.skip("junction test is Windows-specific")
    execution = evidence_run.root / "execution"
    target = execution / "junction-target"
    target.mkdir()
    (target / "payload.bin").write_bytes(b"payload")
    junction = execution / "unsafe-junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction unavailable in test environment: {completed.stderr}")

    with pytest.raises(SpectroscopyRunError, match="link|unsafe"):
        verify_qubit_spectroscopy_scan(evidence_run.root)


def test_publication_failure_preserves_auditable_recovery_staging(monkeypatch, tmp_path: Path):
    _install_runner(monkeypatch)
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    monkeypatch.setattr(
        run_module,
        "publish_calibration_directory",
        lambda *_args: (_ for _ in ()).throw(OSError("locked")),
    )
    try:
        with pytest.raises(SpectroscopyRunError, match="recovery"):
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        staging = next(base.glob(".spectroscopy_*"))
        recovery = json.loads((staging / "recovery.json").read_text("utf-8"))
        assert recovery["status"] == "recovery_required"
        assert recovery["reason"] == "publication_failure"
        assert not target.exists()
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_post_rename_publication_failure_records_uncertain_state(monkeypatch, tmp_path: Path):
    _install_runner(monkeypatch)
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"

    def rename_then_raise(staging: Path, destination: Path) -> None:
        staging.rename(destination)
        raise OSError("post-rename directory flush failed")

    monkeypatch.setattr(run_module, "publish_calibration_directory", rename_then_raise)
    try:
        with pytest.raises(SpectroscopyRunError, match="outcome is uncertain"):
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        assert target.is_dir()
        assert verify_qubit_spectroscopy_scan(target)
        assert not (target / "recovery.json").exists()
        run_id = json.loads((target / "workflow.json").read_text("utf-8"))["run_id"]
        record = base / f".spectroscopy-publication-{run_id}.json"
        payload = json.loads(record.read_text("utf-8"))
        assert payload["status"] == "publication_uncertain"
        assert payload["target_directory_name"] == "scan"
        assert not list(base.glob(".spectroscopy_*"))
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_post_rename_keyboard_interrupt_preserves_interrupt_semantics(monkeypatch, tmp_path: Path):
    _install_runner(monkeypatch)
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"

    def rename_then_interrupt(staging: Path, destination: Path) -> None:
        staging.rename(destination)
        raise KeyboardInterrupt()

    monkeypatch.setattr(run_module, "publish_calibration_directory", rename_then_interrupt)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        assert target.is_dir()
        assert verify_qubit_spectroscopy_scan(target)
        run_id = json.loads((target / "workflow.json").read_text("utf-8"))["run_id"]
        payload = json.loads(
            (base / f".spectroscopy-publication-{run_id}.json").read_text("utf-8")
        )
        assert payload["status"] == "publication_uncertain"
        assert payload["failure_class"] == "KeyboardInterrupt"
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_post_rename_audit_failure_reports_the_preserved_target(monkeypatch, tmp_path: Path):
    _install_runner(monkeypatch)
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"

    def rename_then_raise(staging: Path, destination: Path) -> None:
        staging.rename(destination)
        raise OSError("post-rename directory flush failed")

    monkeypatch.setattr(run_module, "publish_calibration_directory", rename_then_raise)
    monkeypatch.setattr(
        run_module,
        "write_uncertain_publication",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("audit volume unavailable")),
    )
    try:
        with pytest.raises(SpectroscopyRunError, match="audit record failed") as captured:
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        message = str(captured.value)
        assert "target_absolute=" in message
        assert "target_relative=" in message
        assert "audit=OSError: audit volume unavailable" in message
        assert target.is_dir()
        assert verify_qubit_spectroscopy_scan(target)
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


@pytest.mark.parametrize(
    ("failure", "reason"),
    ((RuntimeError("worker failed"), "failed"), (asyncio.CancelledError(), "cancelled")),
)
def test_nonpublication_failures_preserve_recovery_staging(monkeypatch, tmp_path: Path, failure, reason):
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    monkeypatch.setattr(
        run_module,
        "run_qubit_spectroscopy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    try:
        with pytest.raises(type(failure)):
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        staging = next(base.glob(".spectroscopy_*"))
        recovery = json.loads((staging / "recovery.json").read_text("utf-8"))
        assert recovery["status"] == "recovery_required"
        assert recovery["reason"] == reason
        assert not (staging / "receipt.json").exists()
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_recovery_marker_preserves_auditable_state_when_primary_record_fails(monkeypatch, tmp_path: Path):
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    monkeypatch.setattr(
        run_module,
        "run_qubit_spectroscopy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("worker failed")),
    )
    monkeypatch.setattr(
        run_module,
        "write_recovery_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("primary record unavailable")),
    )
    try:
        with pytest.raises(RuntimeError, match="worker failed") as captured:
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        staging = next(base.glob(".spectroscopy_*"))
        marker = next(base.glob(".spectroscopy-recovery-*.marker"))
        payload = json.loads(marker.read_text("utf-8"))
        assert not (staging / "recovery.json").exists()
        assert payload["status"] == "recovery_required"
        assert payload["reason"] == "failed"
        assert payload["primary_failure_class"] == "OSError"
        assert any("fallback marker" in note for note in captured.value.__notes__)
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_double_recovery_record_failure_reports_staging_and_reason_chain(monkeypatch, tmp_path: Path):
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    monkeypatch.setattr(
        run_module,
        "run_qubit_spectroscopy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("worker failed")),
    )
    monkeypatch.setattr(
        run_module,
        "write_recovery_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("primary record unavailable")),
    )
    monkeypatch.setattr(
        run_module,
        "write_recovery_fallback_marker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fallback marker unavailable")),
    )
    try:
        with pytest.raises(SpectroscopyRunError, match="recovery recording failed") as captured:
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        message = str(captured.value)
        assert "staging_absolute=" in message
        assert "staging_relative=tmp/" in message
        assert "original=RuntimeError: worker failed" in message
        assert "primary=OSError: primary record unavailable" in message
        assert "fallback=OSError: fallback marker unavailable" in message
        assert next(base.glob(".spectroscopy_*"), None) is not None
        assert not target.exists()
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_keyboard_interrupt_uses_recovery_marker_without_changing_interrupt(monkeypatch, tmp_path: Path):
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    monkeypatch.setattr(
        run_module,
        "run_qubit_spectroscopy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        run_module,
        "write_recovery_required",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("primary record unavailable")),
    )
    try:
        with pytest.raises(KeyboardInterrupt) as captured:
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        assert next(base.glob(".spectroscopy-recovery-*.marker"), None) is not None
        assert any("fallback marker" in note for note in captured.value.__notes__)
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_keyboard_interrupt_preserves_recovery_staging(monkeypatch, tmp_path: Path):
    base = ROOT / "tmp" / tmp_path.name
    target = base / "scan"
    monkeypatch.setattr(
        run_module,
        "run_qubit_spectroscopy",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            run_qubit_spectroscopy_scan(_scan_request(), _context(), PARENT, target, ROOT)
        staging = next(base.glob(".spectroscopy_*"))
        recovery = json.loads((staging / "recovery.json").read_text("utf-8"))
        assert recovery["status"] == "recovery_required"
        assert recovery["reason"] == "interrupted"
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)
