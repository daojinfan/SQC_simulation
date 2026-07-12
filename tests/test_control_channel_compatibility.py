from __future__ import annotations

import json
import builtins
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import uuid

import nbformat as nbf
from nbclient import NotebookClient
import pytest
import yaml

from sqvm.control import (
    build_control_channel_approval,
    build_control_channel_manifest,
    load_control_channel_registry,
    publish_control_channel_candidate,
    validate_control_channel_approval,
    validate_control_channel_compatibility,
    validate_control_channel_manifest,
    validate_verification_candidate,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.hamiltonian import raw_file_sha256
from sqvm.hamiltonian import stage2_model_source_tree_sha256
import sqvm.control.compatibility as compatibility_module
import sqvm.control.upstream as upstream_module
from sqvm.control.upstream import FROZEN_RECEIPT, validate_frozen_stage4_upstream_receipt


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "configs/control/2q1c2r_channels.yaml"
FORMAL_CANDIDATE = ROOT / "output/stage_04_0_control_channel_rebaseline"


@pytest.fixture
def workspace_tmp():
    path = ROOT / "tmp" / f"stage40_{uuid.uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _registry_payload():
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def _write_registry(path: Path, payload) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _canonical(path: Path, payload) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def _sync_report_notebook_hash(target: Path) -> None:
    report_path = target / "verification_report.json"
    report = json.loads(report_path.read_bytes())
    report["notebook_sha256"] = raw_file_sha256(target / "verification.ipynb")
    _canonical(report_path, report)


def _copy_candidate(target: Path) -> None:
    target.mkdir()
    for name in ("control_channel_manifest.json", "verification.ipynb", "verification_report.json"):
        shutil.copyfile(FORMAL_CANDIDATE / name, target / name)
    report_path = target / "verification_report.json"
    report = json.loads(report_path.read_bytes())
    report["manifest_path"] = (target / "control_channel_manifest.json").relative_to(ROOT).as_posix()
    report["notebook_path"] = (target / "verification.ipynb").relative_to(ROOT).as_posix()
    _canonical(report_path, report)


def _receipt_arguments(root: Path) -> dict[str, Path]:
    return {
        "repository_root": root,
        "stage2_artifact_path": root / "output/stage_02_hamiltonian/hamiltonian_artifacts.json",
        "stage2_1_manifest_path": root / "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json",
        "stage2_1_approval_path": root / "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json",
        "stage3_1_artifact_path": root / "output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json",
        "stage3_1_report_path": root / "output/stage_03_1_q1_q2_coupling/verification_report.json",
        "stage3_1_approval_path": root / "output/stage_03_1_q1_q2_coupling/acceptance_approval.json",
    }


def _sync_stage31_notebook_bindings(root: Path, notebook: dict) -> None:
    notebook_path = root / "output/stage_03_1_q1_q2_coupling/verification.ipynb"
    notebook_path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    notebook_hash = raw_file_sha256(notebook_path)

    report_path = root / "output/stage_03_1_q1_q2_coupling/verification_report.json"
    report = json.loads(report_path.read_bytes())
    report["notebook_sha256"] = notebook_hash
    report["notebook_write"]["sha256"] = notebook_hash
    _canonical(report_path, report)
    report_hash = raw_file_sha256(report_path)

    approval_path = root / "output/stage_03_1_q1_q2_coupling/acceptance_approval.json"
    approval = json.loads(approval_path.read_bytes())
    approval["verification_notebook_sha256"] = notebook_hash
    approval["verification_report_sha256"] = report_hash
    _canonical(approval_path, approval)


def _sync_stage31_solver_bindings(root: Path, candidate: dict) -> None:
    candidate_path = root / "output/stage_03_1_solver_validation/eigsh_validation.json"
    _canonical(candidate_path, candidate)
    candidate_hash = raw_file_sha256(candidate_path)

    solver_approval_path = root / "output/stage_03_1_solver_validation/eigsh_validation_approval.json"
    solver_approval = json.loads(solver_approval_path.read_bytes())
    solver_approval["validation_artifact_sha256"] = candidate_hash
    _canonical(solver_approval_path, solver_approval)

    final_approval_path = root / "output/stage_03_1_q1_q2_coupling/acceptance_approval.json"
    final_approval = json.loads(final_approval_path.read_bytes())
    final_approval["solver_validation_sha256"] = candidate_hash
    final_approval["solver_validation_approval_sha256"] = raw_file_sha256(solver_approval_path)
    _canonical(final_approval_path, final_approval)


@pytest.fixture
def frozen_receipt_tmp(workspace_tmp):
    root = workspace_tmp / "repository"
    for _, relative, _ in FROZEN_RECEIPT:
        source = ROOT / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(ROOT / "pyproject.toml", root / "pyproject.toml")
    for relative in ("src/sqvm/device", "src/sqvm/hamiltonian", "src/sqvm/spectrum"):
        shutil.copytree(ROOT / relative, root / relative)
    return root


def test_positive_exact_seven_channel_merge():
    registry = load_control_channel_registry(REGISTRY)
    assert [row.name for row in registry.channels] == ["q1_xy", "q2_xy", "q1_z", "q2_z", "c_z", "r1_ro", "r2_ro"]
    report = validate_control_channel_compatibility(registry, repository_root=ROOT)
    assert report.ok and report.compatibility_candidate_ready
    assert [row["origin"] for row in report.merged_channels.values()].count("stage4_extension") == 2
    assert not report.blocking_reasons


def test_frozen_upstream_receipt_positive_and_exact_keys():
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(ROOT))
    assert report.ok and report.stage2_1_ready and report.stage3_1_ready
    assert report.errors == ()
    assert tuple(report.current_hashes) == tuple(row[0] for row in FROZEN_RECEIPT)
    assert len(report.current_hashes) == 26


@pytest.mark.parametrize(
    "metadata,expected_error",
    [
        ([], "Stage 3.1 notebook metadata must be a mapping"),
        ({"language_info": [], "stage3_1_read_only": True}, "Stage 3.1 notebook language_info must be a mapping"),
        ({"language_info": {}, "stage3_1_read_only": "true"}, "Stage 3.1 notebook stage3_1_read_only must be true"),
    ],
)
def test_stage31_notebook_malformed_metadata_returns_all_false(
    frozen_receipt_tmp, metadata, expected_error
):
    notebook_path = frozen_receipt_tmp / "output/stage_03_1_q1_q2_coupling/verification.ipynb"
    notebook = json.loads(notebook_path.read_bytes())
    notebook["metadata"] = metadata
    _sync_stage31_notebook_bindings(frozen_receipt_tmp, notebook)

    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))

    assert not report.ok and not report.stage2_1_ready and not report.stage3_1_ready
    assert f"Stage 3.1 receipt invalid: {expected_error}" in report.errors


def test_other_malformed_nested_mapping_returns_all_false(frozen_receipt_tmp):
    candidate_path = frozen_receipt_tmp / "output/stage_03_1_solver_validation/eigsh_validation.json"
    candidate = json.loads(candidate_path.read_bytes())
    candidate["aggregates"] = []
    _sync_stage31_solver_bindings(frozen_receipt_tmp, candidate)

    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))

    assert not report.ok and not report.stage2_1_ready and not report.stage3_1_ready
    assert "Stage 3.1 receipt invalid: Stage 3.1 solver aggregates fields are not exact" in report.errors


@pytest.mark.parametrize("key,relative,expected", FROZEN_RECEIPT, ids=lambda value: str(value)[:32])
def test_each_frozen_receipt_hash_tamper_fails_closed(frozen_receipt_tmp, key, relative, expected):
    path = frozen_receipt_tmp / relative
    path.write_bytes(path.read_bytes() + b"tamper")
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok and not report.stage2_1_ready and not report.stage3_1_ready
    assert tuple(report.current_hashes) == tuple(row[0] for row in FROZEN_RECEIPT)
    assert report.current_hashes[key] not in {"", expected}
    assert any(key in error for error in report.errors)


@pytest.mark.parametrize("key,relative,_", FROZEN_RECEIPT, ids=lambda value: str(value)[:32])
def test_each_missing_frozen_receipt_file_fails_closed(frozen_receipt_tmp, key, relative, _):
    (frozen_receipt_tmp / relative).unlink()
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok and not report.stage2_1_ready and not report.stage3_1_ready
    assert report.current_hashes[key] == ""
    assert any(key in error for error in report.errors)


@pytest.mark.parametrize("attack", ["duplicate", "nonfinite", "noncanonical", "root"])
def test_strict_receipt_json_attacks_fail_closed(frozen_receipt_tmp, attack):
    path = frozen_receipt_tmp / "output/stage_03_1_solver_validation/dense_pilot.json"
    if attack == "duplicate":
        path.write_text('{"schema_version":"0.1","schema_version":"0.1"}\n', encoding="utf-8")
    elif attack == "nonfinite":
        path.write_text('{"value":NaN}\n', encoding="utf-8")
    elif attack == "noncanonical":
        payload = json.loads(path.read_bytes())
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text("[]\n", encoding="utf-8")
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok
    assert any("stage3_1_dense_pilot" in error for error in report.errors)


def test_legacy_noncanonical_receipt_inputs_are_accepted_unchanged():
    for relative in (
        "output/stage_01_device_model/device_artifacts.json",
        "output/stage_02_1_hamiltonian_rebaseline/previous_hamiltonian_artifacts.json",
    ):
        raw = (ROOT / relative).read_bytes()
        payload = json.loads(raw)
        assert isinstance(payload, dict)
        assert raw != canonical_json_bytes(payload)
    assert validate_frozen_stage4_upstream_receipt(**_receipt_arguments(ROOT)).ok


@pytest.mark.parametrize("key", ["device_artifact", "previous_stage2_artifact"])
def test_legacy_receipt_parseable_tamper_still_fails(frozen_receipt_tmp, key):
    relative = dict((name, path) for name, path, _ in FROZEN_RECEIPT)[key]
    path = frozen_receipt_tmp / relative
    payload = json.loads(path.read_bytes())
    payload["schema_version"] = "tampered"
    path.write_text(json.dumps(payload), encoding="utf-8")
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok
    assert report.current_hashes[key] != dict((name, digest) for name, _, digest in FROZEN_RECEIPT)[key]


@pytest.mark.parametrize(
    "key,field,value",
    [
        ("stage2_artifact", "provenance", {}),
        ("stage2_1_approval", "reviewer_role", "attacker"),
        ("legacy_anchor", "decision", "pending"),
        ("stage3_1_artifact", "acceptance_eligible", False),
        ("stage3_1_report", "acceptance_candidate_ready", False),
        ("stage3_1_design_freeze_manifest", "decision", "rejected"),
        ("stage3_1_solver_validation", "coverage_complete", False),
        ("stage3_1_solver_validation_approval", "reviewer_role", "attacker"),
        ("stage3_1_approval", "reviewer_role", "attacker"),
    ],
)
def test_frozen_upstream_structural_attacks_fail_closed(frozen_receipt_tmp, key, field, value):
    relative = dict((name, path) for name, path, _ in FROZEN_RECEIPT)[key]
    path = frozen_receipt_tmp / relative
    payload = json.loads(path.read_bytes())
    payload[field] = value
    _canonical(path, payload)
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok and not report.stage2_1_ready and not report.stage3_1_ready


@pytest.mark.parametrize(
    "field",
    [
        "config_sha256", "design_freeze_manifest_sha256", "q1_q2_coupling_artifact_sha256",
        "review_record_sha256", "solver_validation_approval_sha256", "solver_validation_sha256",
        "stage3_1_source_tree_sha256", "verification_notebook_sha256", "verification_report_sha256",
    ],
)
def test_every_stage31_final_approval_binding_tamper_fails(frozen_receipt_tmp, field):
    path = frozen_receipt_tmp / "output/stage_03_1_q1_q2_coupling/acceptance_approval.json"
    payload = json.loads(path.read_bytes())
    payload[field] = "A" * 64
    _canonical(path, payload)
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok and any("stage3_1_approval" in error or "final approval" in error for error in report.errors)


@pytest.mark.parametrize("source_set", ["stage2", "stage3"])
def test_current_upstream_source_tree_tamper_fails(frozen_receipt_tmp, source_set):
    relative = "src/sqvm/hamiltonian/junction.py" if source_set == "stage2" else "src/sqvm/spectrum/stage31.py"
    path = frozen_receipt_tmp / relative
    path.write_bytes(path.read_bytes() + b"\n# source tamper\n")
    report = validate_frozen_stage4_upstream_receipt(**_receipt_arguments(frozen_receipt_tmp))
    assert not report.ok
    assert any(f"Stage {2 if source_set == 'stage2' else '3.1'}" in error and "source-tree" in error for error in report.errors)


def test_frozen_upstream_path_redirect_and_argument_type_fail_closed(workspace_tmp):
    arguments = _receipt_arguments(ROOT)
    redirected = workspace_tmp / "stage2.json"
    shutil.copyfile(arguments["stage2_artifact_path"], redirected)
    arguments["stage2_artifact_path"] = redirected
    report = validate_frozen_stage4_upstream_receipt(**arguments)
    assert not report.ok and any("stage2_artifact path mismatch" in error for error in report.errors)
    arguments = _receipt_arguments(ROOT)
    arguments["stage2_artifact_path"] = object()
    with pytest.raises(TypeError, match="stage2_artifact"):
        validate_frozen_stage4_upstream_receipt(**arguments)


@pytest.mark.parametrize("mutation", ["missing", "extra", "base_kind", "base_target", "base_port"])
def test_missing_extra_or_tampered_base_channel_fails(workspace_tmp, mutation):
    payload = _registry_payload()
    if mutation == "missing":
        del payload["channels"]["q2_z"]
    elif mutation == "extra":
        payload["channels"]["extra_z"] = {"kind": "z", "target": "q1", "port": "extra_z", "awg_lanes": ["extra_z"]}
    elif mutation == "base_kind":
        payload["channels"]["q1_xy"].update(kind="readout", target="r1")
    elif mutation == "base_target":
        payload["channels"]["q1_xy"]["target"] = "q2"
    else:
        payload["channels"]["q1_xy"]["port"] = "q1_other"
    source = _write_registry(workspace_tmp / "registry.yaml", payload)
    if mutation in {"missing", "extra"}:
        with pytest.raises(ValueError):
            load_control_channel_registry(source)
    else:
        report = validate_control_channel_compatibility(load_control_channel_registry(source), repository_root=ROOT)
        assert not report.ok
        assert not next(row for row in report.checks if row["name"] == "base_channel_subset_exact")["passed"]


@pytest.mark.parametrize("mutation", ["duplicate_port", "duplicate_lane", "lane_count"])
def test_port_lane_and_cardinality_fail_closed(workspace_tmp, mutation):
    payload = _registry_payload()
    if mutation == "duplicate_port":
        payload["channels"]["q2_z"]["port"] = "q1_z"
    elif mutation == "duplicate_lane":
        payload["channels"]["q2_z"]["awg_lanes"] = ["q1_z"]
    else:
        payload["channels"]["q1_z"]["awg_lanes"] = ["q1_z", "q1_z_q"]
    with pytest.raises(ValueError):
        load_control_channel_registry(_write_registry(workspace_tmp / "registry.yaml", payload))


@pytest.mark.parametrize("key", ["stage3_1_approval", "stage4_design_freeze_manifest"])
def test_stale_upstream_or_freeze_hash_fails(workspace_tmp, key):
    source = ROOT / ({
        "stage3_1_approval": "output/stage_03_1_q1_q2_coupling/acceptance_approval.json",
        "stage4_design_freeze_manifest": "docs/decisions/2026-07-11-stage4-design-freeze.json",
    }[key])
    stale = workspace_tmp / source.name
    stale.write_bytes(source.read_bytes() + b"\n")
    report = validate_control_channel_compatibility(
        load_control_channel_registry(REGISTRY), repository_root=ROOT, paths={key: stale}
    )
    assert not report.ok


def test_manifest_is_reconstructed_and_noncanonical_rejected(workspace_tmp):
    manifest = build_control_channel_manifest(repository_root=ROOT)
    path = workspace_tmp / "control_channel_manifest.json"
    _canonical(path, manifest)
    assert validate_control_channel_manifest(path, repository_root=ROOT) == manifest
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        validate_control_channel_manifest(path, repository_root=ROOT)


def test_transaction_exact_three_and_report_cross_consistency(workspace_tmp):
    target = workspace_tmp / "candidate"
    report = publish_control_channel_candidate(target, repository_root=ROOT)
    assert set(item.name for item in target.iterdir()) == {"control_channel_manifest.json", "verification.ipynb", "verification_report.json"}
    assert report["execution_succeeded"] is True
    assert validate_verification_candidate(target, repository_root=ROOT)["approval_status"] == "pending"
    with pytest.raises(FileExistsError):
        publish_control_channel_candidate(target, repository_root=ROOT)

    tampered = json.loads((target / "verification_report.json").read_bytes())
    tampered["manifest_sha256"] = "A" * 64
    _canonical(target / "verification_report.json", tampered)
    with pytest.raises(ValueError, match="manifest_sha256"):
        validate_verification_candidate(target, repository_root=ROOT)


def test_existing_formal_candidate_exact_three_projection_passes_normative_replay(workspace_tmp):
    candidate = workspace_tmp / "candidate"
    _copy_candidate(candidate)
    report = validate_verification_candidate(candidate, repository_root=ROOT)
    notebook = nbf.read(candidate / "verification.ipynb", as_version=4)
    assert report["execution_succeeded"] is True
    assert [cell.id for cell in notebook.cells] == list(compatibility_module.NOTEBOOK_CELL_IDS)


@pytest.mark.parametrize("attack", ["nonexecuted", "external_read"])
def test_notebook_must_be_genuinely_executed_and_read_only(workspace_tmp, attack):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    if attack == "nonexecuted":
        code[0].execution_count = None
    else:
        code[1].source = "Path('outside.json').read_text()"
    nbf.write(notebook, path)
    with pytest.raises(ValueError, match="notebook"):
        validate_verification_candidate(target, repository_root=ROOT)


def test_io_fileio_external_read_attack_with_self_consistent_hash_is_rejected(workspace_tmp):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    (workspace_tmp / "secret.json").write_text('{"secret": true}', encoding="utf-8")
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    code = [cell for cell in notebook.cells if cell.cell_type == "code"]
    code[1].source = "import io\nio.FileIO('../secret.json').read()"
    for cell in code:
        cell.execution_count = None
        cell.outputs = []
        cell.metadata = {}
    executed = NotebookClient(
        notebook,
        timeout=120,
        kernel_name="python3",
        resources={"metadata": {"path": str(target.resolve())}},
    ).execute()
    nbf.write(executed, path)
    _sync_report_notebook_hash(target)
    with pytest.raises(ValueError, match="canonical read-only template"):
        validate_verification_candidate(target, repository_root=ROOT)


def test_arbitrary_later_import_with_self_consistent_hash_is_rejected(workspace_tmp):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    [cell for cell in notebook.cells if cell.cell_type == "code"][2].source = "import math\nmath.pi"
    nbf.write(notebook, path)
    _sync_report_notebook_hash(target)
    with pytest.raises(ValueError, match="canonical read-only template"):
        validate_verification_candidate(target, repository_root=ROOT)


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nimport json\nname = 'control_channel_manifest.json'\ndata = json.loads(Path(name).read_text())\ndata",
        "from pathlib import Path\nimport json\ndata = json.loads(Path('../control_channel_manifest.json').read_text())\ndata",
    ],
)
def test_first_cell_path_indirection_or_non_sibling_path_rejected(workspace_tmp, source):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    [cell for cell in notebook.cells if cell.cell_type == "code"][0].source = source
    nbf.write(notebook, path)
    _sync_report_notebook_hash(target)
    with pytest.raises(ValueError, match="canonical read-only template"):
        validate_verification_candidate(target, repository_root=ROOT)


@pytest.mark.parametrize("attack", ["missing", "extra", "reordered"])
def test_missing_extra_or_reordered_code_cell_rejected(workspace_tmp, attack):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    if attack == "missing":
        del notebook.cells[3]
    elif attack == "extra":
        notebook.cells.append(nbf.v4.new_code_cell("data"))
    else:
        notebook.cells[3], notebook.cells[5] = notebook.cells[5], notebook.cells[3]
    nbf.write(notebook, path)
    _sync_report_notebook_hash(target)
    with pytest.raises(ValueError, match="notebook"):
        validate_verification_candidate(target, repository_root=ROOT)


@pytest.mark.parametrize(
    "attack",
    [
        "count", "text", "extra_output", "output_type", "output_metadata", "mime",
        "execution_metadata_missing", "execution_metadata_extra", "execution_timestamp_order",
    ],
)
def test_fabricated_outputs_or_execution_metadata_rejected(workspace_tmp, attack):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    cell = [row for row in notebook.cells if row.cell_type == "code"][0]
    output = cell.outputs[0]
    if attack == "count":
        cell.execution_count = 99
    elif attack == "text":
        output.data["text/plain"] = "fabricated"
    elif attack == "extra_output":
        cell.outputs.append(nbf.from_dict(json.loads(json.dumps(output))))
    elif attack == "output_type":
        cell.outputs[0] = nbf.v4.new_output("stream", name="stdout", text="fabricated")
    elif attack == "output_metadata":
        output.metadata["unexpected"] = True
    elif attack == "mime":
        output.data["text/html"] = "<b>fabricated</b>"
    elif attack == "execution_metadata_missing":
        del cell.metadata.execution["shell.execute_reply"]
    elif attack == "execution_metadata_extra":
        cell.metadata.execution["unexpected"] = "2026-07-11T00:00:00Z"
    else:
        cell.metadata.execution["iopub.status.busy"] = "2099-01-01T00:00:00Z"
    nbf.write(notebook, path)
    _sync_report_notebook_hash(target)
    with pytest.raises(ValueError, match="candidate notebook"):
        validate_verification_candidate(target, repository_root=ROOT)


@pytest.mark.parametrize("attack", ["missing", "changed", "duplicated", "reordered"])
def test_cell_ids_are_exact_and_ordered(workspace_tmp, attack):
    target = workspace_tmp / "candidate"
    _copy_candidate(target)
    path = target / "verification.ipynb"
    notebook = nbf.read(path, as_version=4)
    if attack == "missing":
        del notebook.cells[0]["id"]
    elif attack == "changed":
        notebook.cells[0].id = "deadbeef"
    elif attack == "duplicated":
        notebook.cells[1].id = notebook.cells[0].id
    else:
        notebook.cells[0].id, notebook.cells[1].id = notebook.cells[1].id, notebook.cells[0].id
    nbf.write(notebook, path)
    _sync_report_notebook_hash(target)
    with pytest.raises(ValueError, match="canonical read-only template"):
        validate_verification_candidate(target, repository_root=ROOT)


@pytest.mark.parametrize("version", ["0.9.9", "0.10rc1", "0.10.0", "0.10.2+local", "malformed"])
def test_nbclient_nonexact_version_fails_before_output(monkeypatch, workspace_tmp, version):
    monkeypatch.setattr(compatibility_module.importlib.metadata, "version", lambda name: version)
    target = workspace_tmp / "candidate"
    with pytest.raises(ValueError, match="nbclient version"):
        publish_control_channel_candidate(target, repository_root=ROOT)
    assert not target.exists() and not list(workspace_tmp.glob(".candidate.staging.*"))


def test_nbclient_missing_metadata_fails_before_output(monkeypatch, workspace_tmp):
    def missing(name):
        raise compatibility_module.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(compatibility_module.importlib.metadata, "version", missing)
    target = workspace_tmp / "candidate"
    with pytest.raises(ValueError, match="metadata is missing"):
        publish_control_channel_candidate(target, repository_root=ROOT)
    assert not target.exists() and not list(workspace_tmp.glob(".candidate.staging.*"))


def test_nbclient_import_failure_after_matching_metadata(monkeypatch, workspace_tmp):
    monkeypatch.setattr(compatibility_module.importlib.metadata, "version", lambda name: "0.10.2")
    original_import = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name == "nbclient":
            raise ImportError("blocked")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    target = workspace_tmp / "candidate"
    with pytest.raises(ValueError, match="nbclient import failed"):
        publish_control_channel_candidate(target, repository_root=ROOT)
    assert not target.exists() and not list(workspace_tmp.glob(".candidate.staging.*"))


def test_wrong_host_interpreter_fails_before_output(monkeypatch, workspace_tmp):
    monkeypatch.setattr(compatibility_module.sys, "executable", str(workspace_tmp / "python.exe"))
    target = workspace_tmp / "candidate"
    with pytest.raises(ValueError, match="host interpreter"):
        publish_control_channel_candidate(target, repository_root=ROOT)
    assert not target.exists() and not list(workspace_tmp.glob(".candidate.staging.*"))


@pytest.mark.parametrize("attack", ["missing", "wrong_executable", "changed_argument", "extra_argument", "malformed"])
def test_kernelspec_contract_fails_before_output(monkeypatch, workspace_tmp, attack):
    from jupyter_client.kernelspec import KernelSpecManager
    approved = str(compatibility_module.APPROVED_INTERPRETER)
    argv = [approved, "-Xfrozen_modules=off", "-m", "ipykernel_launcher", "-f", "{connection_file}"]
    if attack == "missing":
        def get_spec(self, name):
            raise KeyError(name)
    else:
        if attack == "wrong_executable": argv[0] = str(workspace_tmp / "python.exe")
        elif attack == "changed_argument": argv[1] = "-O"
        elif attack == "extra_argument": argv.append("extra")
        elif attack == "malformed": argv = "not-a-list"
        def get_spec(self, name):
            return SimpleNamespace(argv=argv)
    monkeypatch.setattr(KernelSpecManager, "get_kernel_spec", get_spec)
    target = workspace_tmp / "candidate"
    with pytest.raises(ValueError, match="kernelspec"):
        publish_control_channel_candidate(target, repository_root=ROOT)
    assert not target.exists() and not list(workspace_tmp.glob(".candidate.staging.*"))


def test_exact_runtime_preflight_and_protection_identity():
    assert compatibility_module._preflight_notebook_runtime().__name__ == "NotebookClient"
    assert raw_file_sha256(ROOT / "pyproject.toml") == "43025E901AEBD5EAACE6CE98A6E93DD2BF1A6951D528B582BB68BBF69A003E20"
    assert stage2_model_source_tree_sha256(ROOT) == "0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972"


def test_current_stage2_source_mismatch_fails_closed(monkeypatch):
    monkeypatch.setattr(upstream_module, "stage2_model_source_tree_sha256", lambda root: "A" * 64)
    report = validate_control_channel_compatibility(load_control_channel_registry(REGISTRY), repository_root=ROOT)
    assert not report.ok
    assert not next(row for row in report.checks if row["name"] == "stage2_1_approval_valid")["passed"]


def test_unanchored_self_consistent_stage2_trio_fails_closed(workspace_tmp):
    artifact_path = workspace_tmp / "stage2.json"
    manifest_path = workspace_tmp / "manifest.json"
    approval_path = workspace_tmp / "approval.json"
    artifact = json.loads((ROOT / "output/stage_02_hamiltonian/hamiltonian_artifacts.json").read_bytes())
    manifest = json.loads((ROOT / "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json").read_bytes())
    approval = json.loads((ROOT / "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json").read_bytes())
    artifact["provenance"]["stage2_model_source_tree_sha256"] = "A" * 64
    _canonical(artifact_path, artifact)
    manifest["sha256"]["stage2_model_source_tree_sha256"] = "A" * 64
    manifest["sha256"]["stage2_artifacts_sha256"] = raw_file_sha256(artifact_path)
    _canonical(manifest_path, manifest)
    approval["stage2_artifacts_sha256"] = raw_file_sha256(artifact_path)
    approval["manifest_sha256"] = raw_file_sha256(manifest_path)
    _canonical(approval_path, approval)
    report = validate_control_channel_compatibility(
        load_control_channel_registry(REGISTRY),
        repository_root=ROOT,
        paths={
            "stage2_artifact": artifact_path,
            "stage2_1_manifest": manifest_path,
            "stage2_1_approval": approval_path,
        },
    )
    assert not report.ok
    assert not next(row for row in report.checks if row["name"] == "stage2_1_approval_valid")["passed"]


def test_production_has_no_top_level_nbclient_import():
    assert "from nbclient import NotebookClient" not in (ROOT / "src/sqvm/control/artifacts.py").read_text(encoding="utf-8")


def test_control_production_has_no_spectrum_import():
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src/sqvm/control").glob("*.py"))
    assert "sqvm.spectrum" not in sources


def test_read_only_compatibility_validation_does_not_load_nbclient():
    source = (
        "import sys\n"
        "from pathlib import Path\n"
        "from sqvm.control import load_control_channel_registry, validate_control_channel_compatibility\n"
        "root = Path.cwd()\n"
        "registry = load_control_channel_registry(root / 'configs/control/2q1c2r_channels.yaml')\n"
        "before = 'nbclient' in sys.modules\n"
        "report = validate_control_channel_compatibility(registry, repository_root=root)\n"
        "assert report.ok and not before and 'nbclient' not in sys.modules\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", source], cwd=ROOT, env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_read_only_validation_succeeds_when_nbclient_is_unavailable():
    source = (
        "import importlib.abc, sys\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'nbclient' or fullname.startswith('nbclient.'):\n"
        "            raise ModuleNotFoundError(fullname)\n"
        "sys.meta_path.insert(0, Block())\n"
        "from pathlib import Path\n"
        "from sqvm.control import load_control_channel_registry, validate_control_channel_compatibility\n"
        "root = Path.cwd()\n"
        "registry = load_control_channel_registry(root / 'configs/control/2q1c2r_channels.yaml')\n"
        "assert validate_control_channel_compatibility(registry, repository_root=root).ok\n"
        "assert 'nbclient' not in sys.modules\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", source], cwd=ROOT, env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("statement", ["import sqvm", "import sqvm.control", "import sqvm.control.compatibility"])
def test_fresh_ordinary_import_does_not_load_nbclient(statement):
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", f"import sys; {statement}; assert 'nbclient' not in sys.modules"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("statement", ["import sqvm", "import sqvm.control", "import sqvm.control.compatibility"])
def test_ordinary_import_succeeds_when_nbclient_is_unavailable(statement):
    source = (
        "import importlib.abc, sys\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'nbclient' or fullname.startswith('nbclient.'):\n"
        "            raise ModuleNotFoundError(fullname)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"{statement}\n"
        "assert 'nbclient' not in sys.modules\n"
    )
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", source], cwd=ROOT, env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_root_verify_exports_resolve_to_original_functions():
    from sqvm import verify_q1_q2_coupling, verify_static_spectrum
    from sqvm.spectrum import (
        verify_q1_q2_coupling as original_q1_q2,
        verify_static_spectrum as original_static,
    )

    assert verify_q1_q2_coupling is original_q1_q2
    assert verify_static_spectrum is original_static


def test_approval_exact_three_exact_four_positive_and_attacks(workspace_tmp):
    target = workspace_tmp / "candidate"
    publish_control_channel_candidate(target, repository_root=ROOT)
    review = ROOT / "docs/decisions/2026-07-11-stage4-design-freeze-review.md"
    approval = build_control_channel_approval(target, review, repository_root=ROOT)
    approval_path = target / "control_channel_approval.json"
    _canonical(approval_path, approval)
    ready = validate_control_channel_approval(approval_path, repository_root=ROOT)
    assert ready.ok and ready.control_channel_ready and ready.approval_valid
    assert set(ready.bound_hashes) == {
        "stage4_design_freeze_manifest_sha256", "channel_registry_sha256",
        "control_channel_manifest_sha256", "verification_notebook_sha256",
        "verification_report_sha256", "review_record_sha256",
    }
    with pytest.raises(ValueError, match="file set"):
        build_control_channel_approval(target, review, repository_root=ROOT)

    for field, value in (
        ("reviewer_role", "attacker"),
        ("control_channel_manifest_sha256", "A" * 64),
    ):
        attacked = dict(approval)
        attacked[field] = value
        _canonical(approval_path, attacked)
        rejected = validate_control_channel_approval(approval_path, repository_root=ROOT)
        assert not rejected.ok and not rejected.control_channel_ready and not rejected.approval_valid
        assert set(rejected.bound_hashes) == set(approval) & {
            "stage4_design_freeze_manifest_sha256", "channel_registry_sha256",
            "control_channel_manifest_sha256", "verification_notebook_sha256",
            "verification_report_sha256", "review_record_sha256",
        }

    missing = dict(approval); del missing["review_record_sha256"]
    _canonical(approval_path, missing)
    assert not validate_control_channel_approval(approval_path, repository_root=ROOT).ok
    extra = {**approval, "unexpected": True}
    _canonical(approval_path, extra)
    assert not validate_control_channel_approval(approval_path, repository_root=ROOT).ok


def test_failed_transaction_leaves_no_partial_target(workspace_tmp):
    target = workspace_tmp / "candidate"
    stale = workspace_tmp / "stale_approval.json"
    stale.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError):
        publish_control_channel_candidate(target, repository_root=ROOT, paths={"stage3_1_approval": stale})
    assert not target.exists()
    assert not list(workspace_tmp.glob(".candidate.staging.*"))
