from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

import pytest

import sqvm.calibration.spectroscopy as spectroscopy_module
from sqvm.calibration.spectroscopy_run import run_qubit_spectroscopy_scan
from sqvm.calibration.spectroscopy_workflow import run_qubit_spectroscopy_calibration
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.web import registrar
from tests.support.contexts import spectroscopy_context as _context, spectroscopy_result as _result, single_spectroscopy_request as _single_request
from tests.support.calibration_requests import spectroscopy_calibration_request as _request
from tests.support.synthetic_runners import install_synthetic_spectroscopy_runner as _install_synthetic_runner


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json"
_workflow_request = _request
EVENT_KEYS = {
    "schema",
    "type",
    "version",
    "event_id",
    "run_id",
    "workflow_id",
    "artifact_version",
    "workflow_sha256",
    "receipt_sha256",
    "source_relative_path",
    "created_utc",
}


def _published_run(root: Path) -> tuple[Path, dict[str, str]]:
    run_id = str(uuid.uuid4())
    source = root / "custom" / "runs" / run_id
    source.mkdir(parents=True)
    workflow = {
        "schema_version": "0.1",
        "artifact_type": "test_run",
        "artifact_version": "0.3",
        "workflow_id": "registrar_test_v1",
        "run_id": run_id,
        "created_utc": "2026-07-22T08:30:00.000000Z",
        "status": "completed",
    }
    workflow_raw = canonical_json_bytes(workflow)
    workflow_sha256 = hashlib.sha256(workflow_raw).hexdigest().upper()
    receipt = {
        "schema_version": "0.1",
        "artifact_type": "test_run_receipt",
        "artifact_version": "0.3",
        "run_id": run_id,
        "status": "completed",
        "workflow_sha256": workflow_sha256,
    }
    receipt_raw = canonical_json_bytes(receipt)
    (source / "workflow.json").write_bytes(workflow_raw)
    (source / "receipt.json").write_bytes(receipt_raw)
    return source, {
        "run_id": run_id,
        "workflow_sha256": workflow_sha256,
        "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest().upper(),
    }


def test_enqueue_success_is_canonical_idempotent_and_supports_storage_root(
    tmp_path: Path,
) -> None:
    source, identity = _published_run(tmp_path)
    custom_storage = tmp_path.parent / f"{tmp_path.name}-derived-storage"

    try:
        first = registrar.enqueue_published_run(tmp_path, source)
        raw = first.path.read_bytes()
        event = json.loads(raw.decode("utf-8"))
        second = registrar.enqueue_published_run(tmp_path, source)
        custom = registrar.enqueue_published_run(
            tmp_path, source, storage_root=custom_storage
        )

        expected_event_id = hashlib.sha256(
            canonical_json_bytes(identity)
        ).hexdigest().upper()
        assert first.created is True
        assert second.created is False
        assert first.path == second.path
        assert first.event_id == expected_event_id
        assert custom.event_id == expected_event_id
        assert custom.path.parent == custom_storage / "index-inbox"
        assert first.path.parent == (
            tmp_path / "output" / "experiment-storage" / "index-inbox"
        )
        assert set(event) == EVENT_KEYS
        assert event == {
            "schema": "sqvm.web.index_inbox_event",
            "type": "published_run",
            "version": "0.1",
            "event_id": expected_event_id,
            "run_id": identity["run_id"],
            "workflow_id": "registrar_test_v1",
            "artifact_version": "0.3",
            "workflow_sha256": identity["workflow_sha256"],
            "receipt_sha256": identity["receipt_sha256"],
            "source_relative_path": source.relative_to(tmp_path).as_posix(),
            "created_utc": "2026-07-22T08:30:00.000000Z",
        }
        assert raw == canonical_json_bytes(event)
    finally:
        shutil.rmtree(custom_storage, ignore_errors=True)


def test_concurrent_enqueue_publishes_exactly_one_event(tmp_path: Path) -> None:
    source, _ = _published_run(tmp_path)
    storage = tmp_path / "storage"

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = tuple(
            executor.map(
                lambda _: registrar.enqueue_published_run(
                    tmp_path, source, storage_root=storage
                ),
                range(24),
            )
        )

    assert sum(result.created for result in results) == 1
    assert len({result.event_id for result in results}) == 1
    assert len(list((storage / "index-inbox").glob("*.json"))) == 1
    assert not list((storage / "index-inbox").glob(".*.lock"))
    assert not list((storage / "index-inbox").glob(".*.tmp"))


def test_existing_event_identity_conflict_fails_closed(tmp_path: Path) -> None:
    source, _ = _published_run(tmp_path)
    result = registrar.enqueue_published_run(tmp_path, source)
    conflicting = json.loads(result.path.read_text("utf-8"))
    conflicting["source_relative_path"] = "different/run"
    conflicting_raw = canonical_json_bytes(conflicting)
    result.path.write_bytes(conflicting_raw)

    with pytest.raises(registrar.PublicationRegistrarConflict, match="different"):
        registrar.enqueue_published_run(tmp_path, source)

    assert result.path.read_bytes() == conflicting_raw


def test_symlinked_inbox_is_rejected(tmp_path: Path) -> None:
    source, _ = _published_run(tmp_path)
    storage = tmp_path / "storage"
    outside = tmp_path / "outside-inbox"
    storage.mkdir()
    outside.mkdir()
    try:
        (storage / "index-inbox").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks are unavailable: {exc}")
    with pytest.raises(registrar.PublicationRegistrarError, match="linked"):
        registrar.enqueue_published_run(tmp_path, source, storage_root=storage)


def test_hardlinked_authority_is_rejected(tmp_path: Path) -> None:
    source, _ = _published_run(tmp_path)
    storage = tmp_path / "storage"
    workflow = source / "workflow.json"
    authority = source / "workflow-authority.json"
    workflow.replace(authority)
    try:
        os.link(authority, workflow)
    except OSError as exc:
        pytest.skip(f"hardlinks are unavailable: {exc}")
    with pytest.raises(registrar.PublicationRegistrarError, match="linked or unsafe"):
        registrar.enqueue_published_run(tmp_path, source, storage_root=storage)


def test_replace_failure_warns_without_mutating_published_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = _published_run(tmp_path)
    workflow_before = (source / "workflow.json").read_bytes()
    receipt_before = (source / "receipt.json").read_bytes()

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(registrar.os, "replace", fail_replace)
    with pytest.warns(RuntimeWarning, match="injected replace failure"):
        result = registrar.enqueue_published_run_best_effort(tmp_path, source)

    assert result is None
    assert source.is_dir()
    assert (source / "workflow.json").read_bytes() == workflow_before
    assert (source / "receipt.json").read_bytes() == receipt_before
    inbox = tmp_path / "output" / "experiment-storage" / "index-inbox"
    assert not list(inbox.glob("*.json"))
    assert not list(inbox.glob(".*.tmp"))
    assert not list(inbox.glob(".*.lock"))


def _install_scan_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(circuits, _context_value, output_root, _repository_root, **_kwargs):
        execution_root = Path(output_root)
        results = []
        for circuit in circuits:
            evidence_root = execution_root / "circuits" / circuit.circuit_id
            evidence_root.mkdir(parents=True)
            (evidence_root / "result.bin").write_bytes(
                circuit.circuit_id.encode("ascii")
            )
            results.append(
                replace(
                    _result(circuit.circuit_id, 0.8, 0.19, 0.0, 0.0),
                    evidence_root=evidence_root,
                    model_evidence_root=evidence_root,
                )
            )
        return tuple(results)

    monkeypatch.setattr(spectroscopy_module, "run_circuits", fake_run)


@pytest.mark.parametrize("publisher", ["scan", "calibration"])
def test_publishers_return_published_run_when_registrar_fails(
    publisher: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = ROOT / "tmp" / f"registrar_hook_{publisher}_{uuid.uuid4().hex}"
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fail_enqueue(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("injected registrar failure")

    monkeypatch.setattr(registrar, "enqueue_published_run", fail_enqueue)
    try:
        with pytest.warns(RuntimeWarning, match="injected registrar failure"):
            if publisher == "scan":
                _install_scan_runner(monkeypatch)
                run = run_qubit_spectroscopy_scan(
                    replace(_single_request(), run_phase="scan"),
                    _context(),
                    PARENT,
                    target,
                    ROOT,
                    timeout_s=10.0,
                )
            else:
                runner_calls: list[dict[str, object]] = []
                _install_synthetic_runner(monkeypatch, runner_calls)
                run = run_qubit_spectroscopy_calibration(
                    _workflow_request(),
                    _context(),
                    PARENT,
                    target,
                    ROOT,
                    timeout_s=10.0,
                )

        assert run.root == target
        assert target.is_dir()
        assert (target / "workflow.json").is_file()
        assert (target / "receipt.json").is_file()
        assert calls == [
            (
                (ROOT, target),
                {"storage_root": target.parent / "experiment-storage"},
            )
        ]
    finally:
        shutil.rmtree(target, ignore_errors=True)
