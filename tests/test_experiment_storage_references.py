from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import json
import shutil
import hashlib
import uuid
from pathlib import Path

import pytest

from sqvm.storage.references import build_reference_graph
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.calibration import decide_qubit_spectroscopy_calibration, run_qubit_spectroscopy_calibration
from tests.support.contexts import spectroscopy_context as _context
from tests.support.calibration_requests import spectroscopy_calibration_request as _request
from tests.support.synthetic_runners import install_synthetic_spectroscopy_runner as _install_synthetic_runner, install_evidence_runner as _install_runner, scan_spectroscopy_request as _scan_request
from sqvm.calibration.spectroscopy_run import run_qubit_spectroscopy_scan
from tests.support.fixture_loader import copy_fixture


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ID = "platform_configuration_reference_v1"


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    fixture = copy_fixture(FIXTURE_ID, tmp_path / "fixture")
    config = fixture / "platform-configurations"
    experiments = tmp_path / "experiments"
    experiments.mkdir()
    return config, experiments


def _graph(tmp_path: Path):
    config, experiments = _roots(tmp_path)
    return build_reference_graph(configuration_root=config, experiment_output_root=experiments), config, experiments


def _fixture_run(config: Path) -> str:
    return json.loads((config / "current" / "demo_2q1c2r.json").read_text("utf-8"))["source_candidate"]["experiment_run_id"]


def test_real_configuration_copy_contains_permanent_applied_audit(tmp_path):
    graph, config, _experiments = _graph(tmp_path)
    run = _fixture_run(config)
    types = {edge.reference_type for edge in graph.for_run(run)}
    assert graph.scan_incomplete is False
    assert {"current_configuration", "applied_audit"} <= types
    assert graph.permanently_blocked(run)
    assert graph.can_archive(run)
    assert not graph.can_trash(run) and not graph.can_purge(run)


def test_real_pretty_json_is_accepted_without_canonical_byte_requirement(tmp_path):
    graph, config, experiments = _graph(tmp_path)
    current = config / "current" / "demo_2q1c2r.json"
    current.write_text(json.dumps(json.loads(current.read_text("utf-8")), indent=4, ensure_ascii=False) + "\n", "utf-8")
    graph = build_reference_graph(configuration_root=config, experiment_output_root=experiments)
    assert not graph.scan_incomplete


def test_tampered_editable_hash_fails_closed(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    path = config / "current" / "demo_2q1c2r.json"
    value = json.loads(path.read_text("utf-8")); value["content_sha256"] = "A" * 64
    path.write_text(json.dumps(value), "utf-8")
    graph = build_reference_graph(configuration_root=config, experiment_output_root=experiments)
    assert graph.scan_incomplete and not graph.can_trash("unreferenced-run")


def test_extra_configuration_file_fails_closed(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    (config / "current" / "unknown.txt").write_text("x", "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_duplicate_json_key_and_nan_fail_closed(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    (config / "audit" / "bad.json").write_text('{"schema_version":"0.1","schema_version":"0.1"}', "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete
    (config / "audit" / "bad.json").write_text('{"schema_version":NaN}', "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_active_pointer_hash_binding_is_verified(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    path = next((config / "active").glob("*.json")); value = json.loads(path.read_text("utf-8"))
    value["snapshot_content_sha256"] = "B" * 64; path.write_text(json.dumps(value), "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_audit_schema_and_only_applied_event_are_strict(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    path = next((config / "audit").glob("*.json")); value = json.loads(path.read_text("utf-8"))
    value["details"]["unknown"] = True; path.write_text(json.dumps(value), "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_source_candidate_optional_candidate_values_is_strict(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    path = config / "current" / "demo_2q1c2r.json"; value = json.loads(path.read_text("utf-8"))
    value["source_candidate"]["candidate_values_GHz"] = {"Q1": float("nan")}
    path.write_text(json.dumps(value, allow_nan=True), "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete
    value = json.loads(path.read_text("utf-8")); value["source_candidate"]["candidate_values_GHz"] = {"Q1": True}
    path.write_text(json.dumps(value), "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_link_or_hardlink_source_fails_closed(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    path = config / "current" / "demo_2q1c2r.json"
    linked = config / "current" / "linked.json"
    linked.hardlink_to(path)
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_missing_optional_lifecycle_and_pin_roots_are_allowed(tmp_path):
    graph, config, experiments = _graph(tmp_path)
    graph = build_reference_graph(configuration_root=config, experiment_output_root=experiments, lifecycle_root=tmp_path / "absent", pins_root=tmp_path / "also-absent")
    assert not graph.scan_incomplete


def test_graph_for_run_is_sorted_deduplicated_and_unknown_schema_fails_closed(tmp_path):
    graph, config, experiments = _graph(tmp_path)
    edges = graph.for_run(_fixture_run(config))
    assert edges == tuple(sorted(edges, key=lambda edge: (edge.reference_type, str(edge.source_path))))
    assert len(edges) == len(set(edges))

    current = config / "current" / "demo_2q1c2r.json"
    value = json.loads(current.read_text("utf-8"))
    value["schema_version"] = "9.9"
    current.write_text(json.dumps(value), "utf-8")
    failed = build_reference_graph(configuration_root=config, experiment_output_root=experiments)
    assert failed.scan_incomplete and failed.for_run(_fixture_run(config)) == ()


def _run_pin(run_id: str) -> dict:
    value = {"schema_version": "0.1", "artifact_type": "sqvm_experiment_manual_keep", "artifact_version": "0.1", "run_id": run_id, "workflow_sha256": "A" * 64, "manual_keep": True, "actor_id": "test.actor", "updated_utc": "2026-07-21T00:00:00Z"}
    value["content_sha256"] = hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()
    return value


def test_explicit_run_pin_creates_manual_keep_edge_and_validates_hash(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    pins = tmp_path / "run-pins"; pins.mkdir(); run = str(uuid.uuid4())
    (pins / f"{run}.json").write_bytes(canonical_json_bytes(_run_pin(run)))
    graph = build_reference_graph(configuration_root=config, experiment_output_root=experiments, pins_root=pins)
    assert not graph.scan_incomplete
    assert graph.for_run(run)[0].reference_type == "manual_keep"
    pin = _run_pin(run); pin["content_sha256"] = "B" * 64
    (pins / f"{run}.json").write_bytes(canonical_json_bytes(pin))
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments, pins_root=pins).scan_incomplete
    pin = _run_pin(run); pin["actor_id"] = "x"; pin["content_sha256"] = hashlib.sha256(canonical_json_bytes({k: v for k, v in pin.items() if k != "content_sha256"})).hexdigest().upper()
    (pins / f"{run}.json").write_bytes(canonical_json_bytes(pin))
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments, pins_root=pins).scan_incomplete


def test_active_edge_retains_pointer_and_sidecar_evidence(tmp_path):
    graph, config, experiments = _graph(tmp_path)
    run = _fixture_run(config)
    edge = next(edge for edge in graph.for_run(run) if edge.reference_type == "active_snapshot") if any(edge.reference_type == "active_snapshot" for edge in graph.for_run(run)) else None
    # The checked-in snapshot has no sidecar; inject one by copying current's real source.
    if edge is None:
        source = json.loads((config / "current" / "demo_2q1c2r.json").read_text("utf-8"))["source_candidate"]
        snapshot = next((config / "snapshots").iterdir()); (snapshot / "source_candidate.json").write_text(json.dumps(source), "utf-8")
        graph = build_reference_graph(configuration_root=config, experiment_output_root=experiments)
        edge = next(item for item in graph.for_run(run) if item.reference_type == "active_snapshot")
    assert len(edge.evidence_paths) == len(edge.evidence_sha256s) == 2
    assert edge.evidence_paths[0].startswith("active/") and "source_candidate.json" in edge.evidence_paths[1]


def test_unknown_or_damaged_workflow_is_scan_incomplete(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    run = experiments / "bad"; run.mkdir(); (run / "workflow.json").write_text('{"workflow_id":"unknown"}', "utf-8")
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete


def test_authority_root_ancestor_link_fails_closed(tmp_path):
    actual = tmp_path / "actual"; actual.mkdir()
    fixture = copy_fixture(FIXTURE_ID, actual / "fixture")
    shutil.copytree(fixture / "platform-configurations", actual / "config")
    alias = tmp_path / "alias"; alias.symlink_to(actual, target_is_directory=True)
    experiments = tmp_path / "experiments"; experiments.mkdir()
    assert build_reference_graph(configuration_root=alias / "config", experiment_output_root=experiments).scan_incomplete


def test_optional_dangling_root_link_fails_closed(tmp_path):
    _graph0, config, experiments = _graph(tmp_path)
    dangling = tmp_path / "dangling-pins"; dangling.symlink_to(tmp_path / "missing", target_is_directory=True)
    assert build_reference_graph(configuration_root=config, experiment_output_root=experiments, pins_root=dangling).scan_incomplete


def test_duplicate_scan_workflow_run_id_fails_closed(tmp_path, monkeypatch):
    _graph0, config, experiments = _graph(tmp_path)
    _install_runner(monkeypatch)
    writer = ROOT / "tmp" / f"reference_scan_{uuid.uuid4().hex}"
    try:
        scan = run_qubit_spectroscopy_scan(_scan_request(), _context(), ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json", writer / "scan", ROOT, timeout_s=10.0)
        shutil.copytree(scan.root, experiments / "scan-one")
        shutil.copytree(scan.root, experiments / "scan-two")
        assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete
    finally:
        shutil.rmtree(writer, ignore_errors=True)


def test_accepted_decision_uses_real_verifier_and_workflow_reverse_binding(tmp_path, monkeypatch):
    _graph0, config, experiments = _graph(tmp_path)
    calls = []
    _install_synthetic_runner(monkeypatch, calls)
    writer = ROOT / "tmp" / f"reference_decision_{uuid.uuid4().hex}"
    try:
        workflow_root = writer / "workflow"
        run = run_qubit_spectroscopy_calibration(
            _request(), _context(), ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json", workflow_root, ROOT, timeout_s=10.0,
        )
        decision_root = writer / "decision"
        decide_qubit_spectroscopy_calibration(
            run.root, ROOT / "configs" / "calibration" / "platform_uncalibrated_v1.json", decision_root, _context(),
            decision="accept", actor_id="project.manager", reason="stable storage reference test",
            confirmation_phrase=f"ACCEPT SIMULATION CALIBRATION {run.recommendation_id}", accepted_targets=("Q1", "Q2"), repository_root=ROOT,
        )
        shutil.copytree(workflow_root, experiments / "workflow")
        shutil.copytree(decision_root, experiments / "decision")
        graph = build_reference_graph(configuration_root=config, experiment_output_root=experiments)
        edge = next(item for item in graph.for_run(run.run_id) if item.reference_type == "accepted_decision")
        assert not graph.scan_incomplete and edge.recommendation_id == run.recommendation_id
        decision = json.loads((experiments / "decision" / "decision.json").read_text("utf-8"))
        decision["recommendation_sha256"] = "A" * 64
        (experiments / "decision" / "decision.json").write_bytes(canonical_json_bytes(decision))
        assert build_reference_graph(configuration_root=config, experiment_output_root=experiments).scan_incomplete
    finally:
        shutil.rmtree(writer, ignore_errors=True)
