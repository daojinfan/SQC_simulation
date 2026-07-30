from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import uuid

import pytest as _pytest

pytestmark = _pytest.mark.physics_slow

import pytest

from sqvm.calibration import run_rabi
from sqvm.calibration.rabi import RabiError
from sqvm.calibration.rabi_reader import (
    RabiReaderVerificationError,
    verify_qubit_rabi_scan_evidence,
)
from sqvm.candidate_protocol import calibration_candidate, parameter_change
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.storage.archive_format import DirectoryEvidenceReader
from sqvm.storage.inventory import inventory_tree
from sqvm.storage.models import ArchiveEntry
from sqvm.storage.operations import (
    ExperimentStorageOperations,
    StorageMutationRequest,
    StorageOperationError,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_FIXTURE = ROOT / "tests" / "fixtures" / "platform_configuration_reference_v1" / "platform-configurations"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reader(root: Path) -> DirectoryEvidenceReader:
    inventory = inventory_tree(root, confinement_root=root)
    entries = tuple(
        ArchiveEntry(row.relative_path, row.logical_bytes, _sha((root / row.relative_path).read_bytes()))
        for row in inventory.files
    )
    return DirectoryEvidenceReader(root, entries)


def _verify(root: Path) -> None:
    verify_qubit_rabi_scan_evidence(_reader(root))


def _refresh_top_level_bindings(root: Path) -> None:
    """Rebind public envelopes so semantic checks, not stale hashes, reject tampering."""

    workflow = _json(root / "workflow.json")
    dataset = _json(root / "dataset.json")
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))
    (root / "dataset.json").write_bytes(canonical_json_bytes(dataset))
    workflow_sha = _sha((root / "workflow.json").read_bytes())
    dataset_sha = _sha((root / "dataset.json").read_bytes())
    receipt = {
        "artifact_type": "rabi_scan_receipt",
        "status": "completed",
        "run_id": workflow["run_id"],
        "workflow_sha256": workflow_sha,
        "dataset_sha256": dataset_sha,
        "recommendation_id": workflow["recommendation_id"],
    }
    report = {
        "ok": True,
        "status": "completed",
        "run_id": workflow["run_id"],
        "workflow_sha256": workflow_sha,
        "dataset_sha256": dataset_sha,
    }
    (root / "receipt.json").write_bytes(canonical_json_bytes(receipt))
    (root / "verification_report.json").write_bytes(canonical_json_bytes(report))
    manifest = {
        "artifact_type": "rabi_scan_manifest",
        "workflow_sha256": workflow_sha,
        "dataset_sha256": dataset_sha,
        "receipt_sha256": _sha((root / "receipt.json").read_bytes()),
    }
    (root / "manifest.json").write_bytes(canonical_json_bytes(manifest))


def _complete_unpublished_fit_fixture(root: Path) -> None:
    """Make a bounded structural fixture from real Runtime evidence.

    The current product writes an empty curve for an ineligible, unbracketed
    scan, then its own reader rejects that staging tree.  Storage acceptance
    needs a reader-admissible carrier, so this only supplies the mandatory
    structural curve and rebinds the public envelopes.  Runtime batch and
    evidence files remain the production QuTiP output and the reader is never
    replaced.
    """

    workflow = _json(root / "workflow.json")
    analysis = workflow["analysis"]
    axis = workflow["request"]["axis"]["values"]
    analysis["dense_fit_curve"] = {
        "amplitude_GHz": [axis[-1] * index / 200.0 for index in range(201)],
        "P1": [0.0 for _ in range(201)],
    }
    if workflow["candidates"]:
        workflow["candidates"][0]["quality_metrics"] = analysis
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))
    _refresh_top_level_bindings(root)


def _add_legacy_fallback_candidate(root: Path) -> None:
    """Recreate the pre-fix no-fit candidate for compatibility checks."""

    workflow = _json(root / "workflow.json")
    request = workflow["request"]
    target = request["target"]
    setting_id = request["setting_id"]
    current = request["setting_amplitude_GHz"]
    dataset_sha = workflow["dataset"]["sha256"]
    resource = {
        "owner": target,
        "resource_type": "waveform_setting",
        "resource_id": setting_id,
    }
    workflow["candidates"] = [
        calibration_candidate(
            f"{target}:xy2_amplitude:{dataset_sha[:16]}",
            target,
            [
                parameter_change(
                    f"calibration_values.waveform_registry.settings.{setting_id}.amplitude_GHz",
                    current,
                    current,
                    unit="GHz",
                    configuration_resource=resource,
                )
            ],
            recommendation_eligible=False,
            candidate_type="xy2_amplitude",
            source_dataset_sha256s=[dataset_sha],
            quality_metrics=workflow["analysis"],
            reason="legacy no-fit fallback candidate",
        )
    ]
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))


def _mutation_request(root: Path) -> StorageMutationRequest:
    return StorageMutationRequest(
        "qa.rabi",
        3,
        _sha((root / "workflow.json").read_bytes()),
        "independent Rabi storage acceptance",
    )


def _commit_applied_reference(config_root: Path, run_id: str) -> None:
    event_id = str(uuid.uuid4())
    event = {
        "schema_version": "0.1",
        "event_id": event_id,
        "event": "experiment_candidates_applied_to_current",
        "actor_id": "qa.rabi",
        "created_utc": "2026-07-26T00:00:00Z",
        "details": {
            "device_id": "demo_2q1c2r",
            "experiment_run_id": run_id,
            "recommendation_id": str(uuid.uuid4()),
            "candidate_ids": ["rabi-candidate"],
            "targets": ["Q1"],
            "content_sha256": "A" * 64,
        },
    }

    audit = config_root / "audit"
    audit.mkdir(exist_ok=True)
    (audit / f"{event_id}.json").write_bytes(canonical_json_bytes(event))


@pytest.fixture(scope="module")
def real_rabi_tree():
    """One production QuTiP Rabi scan in a short Windows-safe repository path."""

    base = ROOT / "tmp" / f"r{uuid.uuid4().hex[:6]}"
    config = base / "c"
    collection = base / "e"
    operation_id = "0cfac653-1d5d-4406-9d6b-4bedfba5bd64"
    try:
        shutil.copytree(CONFIG_FIXTURE, config)
        try:
            run = run_rabi(
                target="Q1",
                amplitude_range_GHz=(0.0, 0.020),
                amplitude_step_GHz=0.005,
                output_root=collection,
                configuration_storage_root=config,
                repository_root=ROOT,
                operation_id=operation_id,
                timeout_s=120.0,
                batch_deadline_s=600.0,
            )
            root = Path(run.root)
        except RabiError as exc:
            assert str(exc) == "rabi evidence reader rejected artifact"
            staging = collection / f".rabi_{operation_id.replace('-', '')}"
            assert staging.is_dir()
            _complete_unpublished_fit_fixture(staging)
            root = collection / f"qubit_rabi_{operation_id.replace('-', '')}"
            shutil.copytree(staging, root)
            shutil.rmtree(staging)
        _verify(root)
        yield {"base": base, "config": config, "collection": collection, "root": root, "run_id": operation_id}
    finally:
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def storage_tree(real_rabi_tree, tmp_path):
    source = real_rabi_tree["root"]
    hot = tmp_path / "hot"
    hot.mkdir()
    target = hot / source.name
    shutil.copytree(source, target)
    config = tmp_path / "config"
    # The production run's configuration storage accumulates transactional
    # projections whose Windows paths are intentionally deep.  Lifecycle only
    # needs the authoritative configuration/reference graph, so start this
    # independent service from the immutable platform fixture.
    shutil.copytree(CONFIG_FIXTURE, config)
    storage = tmp_path / "storage"
    ops = ExperimentStorageOperations(
        hot_root=hot,
        storage_root=storage,
        configuration_root=config,
        experiment_output_root=hot,
        catalog_revision=3,
    )
    return ops, real_rabi_tree["run_id"], _mutation_request(target), hot, storage, config


def test_rabi_archive_restore_hot_trash_restore_keep_and_reference_rules(storage_tree) -> None:
    ops, run_id, request, hot, storage, config = storage_tree
    hot_alias = hot / f"qubit_rabi_{run_id.replace('-', '')}"

    archived = ops.archive(run_id, request)
    assert archived.state == "archived"
    assert not hot_alias.exists()
    assert (storage / "archives" / f"{run_id}.sqrun").is_file()

    restored = ops.restore_hot(run_id, request)
    assert restored.state == "hot"
    _verify(hot_alias)
    assert ops.set_keep(run_id, True, request).state == "hot"
    with pytest.raises(StorageOperationError):
        ops.trash(run_id, request)
    assert ops.set_keep(run_id, False, request).state == "hot"

    archived = ops.archive(run_id, request)
    assert archived.state == "archived"
    moved = ops.trash(run_id, request)
    assert moved.state == "trash"
    assert (storage / "trash" / run_id / "payload").is_file()
    restored = ops.restore_trash(run_id, request)
    assert restored.state == "archived"
    assert (storage / "archives" / f"{run_id}.sqrun").is_file()

    # v1 deliberately has no irreversible purge API; keep/reference prevent trash.
    assert not hasattr(ops, "purge")
    restored = ops.restore_hot(run_id, request)
    assert restored.state == "hot"
    _commit_applied_reference(config, run_id)
    with pytest.raises(StorageOperationError):
        ops.trash(run_id, request)
    _verify(hot_alias)


def _tamper_candidate_path(root: Path) -> None:
    _add_legacy_fallback_candidate(root)
    workflow = _json(root / "workflow.json")
    workflow["candidates"][0]["changes"][0]["parameter_path"] = "calibration_values.waveform_registry.settings.other.amplitude_GHz"
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))


def _tamper_candidate_value(root: Path) -> None:
    _add_legacy_fallback_candidate(root)
    workflow = _json(root / "workflow.json")
    workflow["candidates"][0]["changes"][0]["proposed_value"] += 0.001
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))


def _tamper_policy(root: Path) -> None:
    workflow = _json(root / "workflow.json")
    workflow["request"]["analysis_policy_sha256"] = "A" * 64
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))


def _tamper_analysis(root: Path) -> None:
    workflow = _json(root / "workflow.json")
    workflow["analysis"]["input_dataset_sha256"] = "A" * 64
    (root / "workflow.json").write_bytes(canonical_json_bytes(workflow))


def _tamper_phase(root: Path) -> None:
    dataset = _json(root / "dataset.json")
    dataset["points"][0]["phase_audit"]["second_start_sample"] += 1
    (root / "dataset.json").write_bytes(canonical_json_bytes(dataset))


def _tamper_batch_request(root: Path) -> None:
    request = _json(root / "execution" / "batch" / "request.json")
    request["circuits"][0]["qcis_source"] = "SET Q1 setting.active_xy2_setting.amplitude_GHz 0\nPLSXY Q1\n"
    (root / "execution" / "batch" / "request.json").write_bytes(canonical_json_bytes(request))


def _tamper_batch_head(root: Path) -> None:
    head = _json(root / "execution" / "batch" / "head.json")
    head["completed_points"] = head["completed_points"][:-1]
    (root / "execution" / "batch" / "head.json").write_bytes(canonical_json_bytes(head))


def _tamper_point(root: Path) -> None:
    receipt_path = next((root / "execution" / "batch").glob("points/*.json"))
    receipt = _json(receipt_path)
    receipt["result"]["leakage"] += 0.001
    receipt_path.write_bytes(canonical_json_bytes(receipt))


def _tamper_evidence(root: Path) -> None:
    evidence = next(path for path in (root / "execution").rglob("*") if path.is_file() and "batch" not in path.parts)
    evidence.write_bytes(evidence.read_bytes() + b"tamper")


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(_tamper_candidate_path, id="candidate-path"),
        pytest.param(_tamper_candidate_value, id="candidate-value"),
        pytest.param(_tamper_policy, id="policy-binding"),
        pytest.param(_tamper_analysis, id="analysis-binding"),
        pytest.param(_tamper_phase, id="phase-audit"),
        pytest.param(_tamper_batch_request, id="batch-request"),
        pytest.param(_tamper_batch_head, id="batch-head"),
        pytest.param(_tamper_point, id="point-receipt"),
        pytest.param(_tamper_evidence, id="evidence"),
    ],
)
def test_rabi_reader_fails_closed_for_bound_evidence_tampering(real_rabi_tree, tmp_path, tamper) -> None:
    root = tmp_path / real_rabi_tree["root"].name
    shutil.copytree(real_rabi_tree["root"], root)
    tamper(root)
    _refresh_top_level_bindings(root)
    with pytest.raises(RabiReaderVerificationError):
        _verify(root)


def test_rabi_reader_keeps_legacy_no_fit_candidate_compatible(real_rabi_tree, tmp_path) -> None:
    root = tmp_path / real_rabi_tree["root"].name
    shutil.copytree(real_rabi_tree["root"], root)
    _add_legacy_fallback_candidate(root)
    _refresh_top_level_bindings(root)

    _verify(root)


def test_corrupted_rabi_staging_and_idempotency_conflict_do_not_publish(real_rabi_tree) -> None:
    base = ROOT / "tmp" / f"s{uuid.uuid4().hex[:6]}"
    config = base / "c"
    collection = base / "e"
    source = real_rabi_tree["root"]
    run_id = real_rabi_tree["run_id"]
    operation_id = run_id
    target = collection / source.name
    staging = collection / f".rabi_{run_id.replace('-', '')}"
    try:
        shutil.copytree(real_rabi_tree["config"], config)
        shutil.copytree(source, staging)
        (staging / "dataset.json").write_bytes(b"{}")
        with pytest.raises(Exception):
            run_rabi(
                "Q1", (0.0, 0.020), 0.005,
                output_root=collection, configuration_storage_root=config,
                repository_root=ROOT, operation_id=operation_id,
                timeout_s=120.0, batch_deadline_s=600.0,
            )
        assert not target.exists()
        shutil.rmtree(staging)
        shutil.copytree(source, target)
        with pytest.raises(Exception):
            run_rabi(
                "Q1", (0.0, 0.016), 0.004,
                output_root=collection, configuration_storage_root=config,
                repository_root=ROOT, operation_id=operation_id,
                timeout_s=120.0, batch_deadline_s=600.0,
            )
        assert target.is_dir()
        _verify(target)
    finally:
        shutil.rmtree(base, ignore_errors=True)
