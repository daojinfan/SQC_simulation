from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.evidence

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import uuid

import pytest
import yaml

import sqvm.runtime_v02.runner as v02_runner
import sqvm.runtime_v02.core as v02_core
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json as qcis_sha256_json
from sqvm.runtime_v02 import (
    ExperimentRequestV02,
    BACKEND_ID as V02_BACKEND_ID,
    CLAIM_ENVELOPE as V02_CLAIM_ENVELOPE,
    EXPERIMENT_ID as V02_EXPERIMENT_ID,
    compile_point_v02,
    expand_scan_v02,
    get_runtime_schema_registry,
)
from sqvm.runtime_api import (
    load_experiment_request_versioned as load_experiment_request,
    rebuild_run_catalog_versioned as rebuild_run_catalog,
    recover_interrupted_run_versioned as recover_interrupted_run,
    recover_terminal_resource_lock_versioned,
    run_experiment_versioned as run_experiment,
    verify_experiment_run_versioned as verify_experiment_run,
)
from sqvm.runtime.models import frozen_mapping
from sqvm.runtime.journal import canonical_json_line_bytes
from sqvm.runtime.provenance import build_source_snapshot
from sqvm.runtime.storage import inventory_tree
from sqvm.runtime_v02.core import (
    RESULT_SCHEMA,
    canonical_request_payload_v02,
    compiler_fixture_versions,
    fixture_binding_from_persisted_payload_v02,
    load_compiler_fixture_authority,
    load_experiment_request_v02,
    validate_dataset_v02,
    write_dataset_v02,
)
from sqvm.runtime_v02.recovery import recover_interrupted_run_v02
from sqvm.runtime_v02.runner import run_experiment_v02
from sqvm.runtime_v02.verify import verify_experiment_run_v02


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/experiments/platform_qcis_compile_smoke_v1.yaml"
LEGACY_FIXTURE_ROOT = ROOT / "tests/fixtures/runtime_v02_legacy_v1"


@pytest.fixture
def output_root():
    relative = Path("tmp") / f"stage6_v02_{uuid.uuid4().hex}"
    absolute = ROOT / relative
    try:
        yield relative, absolute
    finally:
        if absolute.exists():
            shutil.rmtree(absolute)


def _tamper_canonical(path: Path, mutate) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_bytes(canonical_json_bytes(payload))


def _rebind_terminal(run_dir: Path) -> None:
    manifest_path = run_dir / "manifest.json"
    report_path = run_dir / "verification_report.json"
    receipt_path = run_dir / "receipt.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    manifest["events_sha256"] = hashlib.sha256((run_dir / "events.jsonl").read_bytes()).hexdigest().upper()
    manifest["event_tail_sha256"] = events[-1]["event_sha256"]
    manifest["payload_files"] = [
        row for row in inventory_tree(run_dir)
        if row["path"] not in {"manifest.json", "verification_report.json", "receipt.json"}
    ]
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest().upper()
    report_path.write_bytes(canonical_json_bytes(report))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["manifest_sha256"] = report["manifest_sha256"]
    receipt["verification_report_sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest().upper()
    receipt["event_tail_sha256"] = events[-1]["event_sha256"]
    receipt_path.write_bytes(canonical_json_bytes(receipt))


def _verify_legacy_fixture_provenance() -> dict[str, object]:
    provenance = json.loads((LEGACY_FIXTURE_ROOT / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["source_commit"] == "8e054797227ed58a53ec80a1e35df5ac205de032"
    assert provenance["source_compiler_sha256"] == "7E94B3B7D24EDA24AA720D532754F4ED564B9D8E700B2B125BC59522E01ACB23"
    assert provenance["generator_path"] == "tests/tools/generate_runtime_v02_legacy_v1_fixture.py"
    generator = ROOT / provenance["generator_path"]
    assert hashlib.sha256(generator.read_bytes()).hexdigest().upper() == provenance["generator_raw_sha256"]
    assert provenance["fixed_inputs"]["terminal_run_id"] == "11111111-1111-4111-8111-111111111111"
    assert provenance["fixed_inputs"]["interrupted_run_id"] == "22222222-2222-4222-8222-222222222222"
    assert provenance["fixed_inputs"]["excluded_derived_files"] == ["terminal-output/catalog_v02.sqlite"]
    files = provenance["files"]
    assert provenance["file_count"] == len(files) == 63
    for entry in files:
        path = LEGACY_FIXTURE_ROOT / entry["path"]
        assert path.is_file()
        assert path.stat().st_size == entry["byte_length"]
        assert hashlib.sha256(path.read_bytes()).hexdigest().upper() == entry["raw_sha256"]
    encoded = json.dumps(files, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest().upper() == provenance["file_manifest_aggregate_sha256"]
    return provenance


def _assert_tree_byte_equal(actual: Path, expected: Path) -> None:
    actual_files = {
        path.relative_to(actual).as_posix(): path
        for path in actual.rglob("*") if path.is_file()
    }
    expected_files = {
        path.relative_to(expected).as_posix(): path
        for path in expected.rglob("*") if path.is_file()
    }
    assert actual_files.keys() == expected_files.keys()
    for relative, expected_path in expected_files.items():
        assert actual_files[relative].read_bytes() == expected_path.read_bytes(), relative


def test_v02_frozen_legacy_fixture_generator_byte_matches_checked_evidence():
    _verify_legacy_fixture_provenance()
    generated = Path("D:/") / f"sqc_v02_{uuid.uuid4().hex[:8]}"
    assert not generated.exists()
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "tests/tools/generate_runtime_v02_legacy_v1_fixture.py",
                "--repository-root", ".",
                "--target", str(generated),
                "--verify-against", "tests/fixtures/runtime_v02_legacy_v1",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        _assert_tree_byte_equal(generated, LEGACY_FIXTURE_ROOT)
    finally:
        if generated.exists():
            shutil.rmtree(generated)


@pytest.fixture
def frozen_legacy_root():
    _verify_legacy_fixture_provenance()
    root = ROOT / "tmp" / f"legacy_v1_{uuid.uuid4().hex[:8]}"
    root.mkdir()
    try:
        shutil.copy2(ROOT / "pyproject.toml", root / "pyproject.toml")
        shutil.copy2(ROOT / "requirements-stage6-lock.txt", root / "requirements-stage6-lock.txt")
        shutil.copytree(ROOT / "configs", root / "configs")
        target = root / "tests/fixtures/runtime_v02_legacy_v1"
        target.parent.mkdir(parents=True)
        shutil.copytree(LEGACY_FIXTURE_ROOT, target)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_v02_public_loader_is_exact_and_keeps_v01_separate(tmp_path):
    assert get_runtime_schema_registry().versions() == ("0.1", "0.2")
    assert build_source_snapshot(ROOT)["aggregate_sha256"] == "357ACB3B4AE0A48E5FDCDCB95526D088476A81A39D98FB2E8ABA8ACD6386E425"
    request = load_experiment_request(CONFIG, ROOT)
    assert isinstance(request, ExperimentRequestV02)
    assert request.schema_version == "0.2"
    assert request.experiment_id == V02_EXPERIMENT_ID
    assert request.backend_id == V02_BACKEND_ID
    assert request.program["program_schema_version"] == "0.3"

    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    raw["schema_version"] = "0.1"
    path = ROOT / "tmp" / f"bad_cross_version_{uuid.uuid4().hex}.yaml"
    path.parent.mkdir(exist_ok=True)
    try:
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with pytest.raises(ValueError, match="program must be null"):
            load_experiment_request(path, ROOT)
        raw["schema_version"] = "9.9"
        path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version"):
            run_experiment(path, "tmp/never_created_v02", ROOT)
        assert not (ROOT / "tmp/never_created_v02").exists()
    finally:
        path.unlink(missing_ok=True)


def test_v02_authority_point_identity_and_compiler_evidence_are_deterministic():
    authority, authority_sha = load_compiler_fixture_authority(ROOT)
    assert authority["authority_id"] == "89168201511D2BF75667B3593969F4B9CC21AB7AC8DE9570DFE58A6BA897F4D5"
    request = load_experiment_request(CONFIG, ROOT)
    assert request.authority_sha256 == authority_sha
    first = expand_scan_v02(request)
    second = expand_scan_v02(request)
    assert first == second and len(first) == 2
    assert first[0].point_id != first[1].point_id

    changed_program = dict(request.program)
    changed_program["template_sha256"] = "A" * 64
    changed = replace(request, program=frozen_mapping(changed_program))
    assert expand_scan_v02(changed)[0].point_id != first[0].point_id

    compiled_a = compile_point_v02(request, first[0])
    compiled_b = compile_point_v02(request, first[0])
    assert compiled_a.concrete_source == b"PLSXY Q1 0 -1 2 1 5.2 0 0 2\n"
    assert compiled_a.ast_bytes == compiled_b.ast_bytes
    assert compiled_a.trace_bytes == compiled_b.trace_bytes
    assert compiled_a.result == compiled_b.result
    assert tuple(compiled_a.result["values"]) == tuple(RESULT_SCHEMA)
    forged = replace(first[0], point_id="F" * 64)
    with pytest.raises(ValueError, match="not canonical"):
        compile_point_v02(request, forged)


@pytest.mark.parametrize(("relative", "fixture_version"), [
    ("configs/runtime/stage6v02/compiler_fixture_authority_v1.json", "v1"),
    ("configs/runtime/stage6v02/compiler_fixture_approval_v1.json", "v1"),
    ("configs/runtime/stage6v02/compiler_fixture_authority_v2.json", "v2"),
    ("configs/runtime/stage6v02/compiler_fixture_approval_v2.json", "v2"),
])
def test_v02_authority_and_external_approval_are_raw_hash_anchored(relative, fixture_version):
    path = ROOT / relative
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" ")
        with pytest.raises(ValueError):
            load_compiler_fixture_authority(ROOT, fixture_version=fixture_version)
    finally:
        path.write_bytes(original)


def test_v02_compiler_fixture_v2_is_versioned_and_is_the_default_admission_authority():
    assert compiler_fixture_versions() == ("v1", "v2")
    authority = json.loads(
        (ROOT / "configs/runtime/stage6v02/compiler_fixture_authority_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(authority["source_bindings"]) == {
        "docs/decisions/2026-07-16-stage4-1-qcis-v0-3-design-freeze.md",
        "docs/designs/04_1_parameterized_control_design.md",
        "docs/designs/06_1_experiment_runtime_v02_design.md",
        "docs/designs/07_1_3_platform_configuration_v0_2.schema.json",
        "docs/designs/07_1_3_platform_configuration_v0_2_design.md",
        "docs/designs/07_qcis_compiler_design.md",
        "docs/designs/07_qcis_compiler_v0_3_phase_amendment.md",
        "src/sqvm/qcis/compiler.py",
        "src/sqvm/qcis/models.py",
        "src/sqvm/qcis/parser.py",
    }
    loaded, raw_sha = load_compiler_fixture_authority(ROOT)
    assert loaded["authority_id"] == authority["authority_id"]
    assert raw_sha == "4B7D901D910A706D24353CC7CC3C4B2DBE7427EEB77C8D6A4A6A8DD50F278BFF"
    with pytest.raises(ValueError, match="compiler fixture version is not registered"):
        load_compiler_fixture_authority(ROOT, fixture_version="v3")


def _synthetic_approved_fixture_registry():
    """Exercise activation wiring without minting an approval artifact in configs/."""

    template = json.loads((ROOT / "configs/runtime/stage6v02/compiler_fixture_authority_v2.json").read_text(encoding="utf-8"))
    # _safe_regular_file deliberately requires repository containment. Keep every
    # synthetic byte under one uniquely named, finally-cleaned test base.
    base = ROOT / "tmp" / f"synthetic_fixture_registry_{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    registry = {}
    for version in ("v1", "v2"):
        authority = json.loads(json.dumps(template))
        authority["qcis_authorities"]["compiler"]["compiler_id"] = f"synthetic_{version}"
        compiler = authority["qcis_authorities"]["compiler"]
        authority["qcis_authorities"]["expected_sha256"]["compiler"] = qcis_sha256_json(compiler)
        authority["authority_id"] = hashlib.sha256(canonical_json_bytes({
            key: value for key, value in authority.items() if key != "authority_id"
        })).hexdigest().upper()
        authority_path = base / f"authority_{version}.json"
        authority_path.write_bytes(canonical_json_bytes(authority))
        authority_sha = hashlib.sha256(authority_path.read_bytes()).hexdigest().upper()
        approval = {
            "schema_version": "0.1",
            "artifact_type": "stage_06_qcis_compiler_fixture_approval",
            "artifact_version": "2",
            "authority_id": authority["authority_id"],
            "authority_raw_sha256": authority_sha,
            "design_path": "docs/designs/06_1_experiment_runtime_v02_design.md",
            "design_sha256": authority["source_bindings"]["docs/designs/06_1_experiment_runtime_v02_design.md"],
            "reviewer_role": "independent_test",
            "decision": "APPROVE",
            "blocking_findings": [],
        }
        approval_path = base / f"approval_{version}.json"
        approval_path.write_bytes(canonical_json_bytes(approval))
        registry[version] = {
            "authority_path": authority_path.relative_to(ROOT).as_posix(),
            "approval_path": approval_path.relative_to(ROOT).as_posix(),
            "authority_id": authority["authority_id"],
            "approval_sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest().upper(),
            "authority_artifact_version": "2",
            "candidate": False,
            "source_paths": frozenset(authority["source_bindings"]),
            "required_approval": approval,
        }
    assert registry["v1"]["authority_id"] != registry["v2"]["authority_id"]
    return registry, base


def test_v02_unknown_fixture_rejects_before_reservation_or_output(tmp_path, monkeypatch):
    output = Path("tmp") / f"unknown_fixture_{uuid.uuid4().hex}"
    monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v3")
    with pytest.raises(ValueError, match="compiler fixture version is not registered"):
        run_experiment_v02(CONFIG, output, ROOT)
    assert not (ROOT / output).exists()


def test_v02_fixture_activation_persists_and_dispatches_without_default_drift(monkeypatch):
    registry, synthetic_base = _synthetic_approved_fixture_registry()
    monkeypatch.setattr(v02_core, "_FIXTURE_VERSIONS", registry)
    try:
        monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v2")
        v2_request = load_experiment_request_v02(CONFIG, ROOT)
        payload = canonical_request_payload_v02(v2_request)
        assert payload["compiler_fixture_version"] == "v2"
        assert payload["compiler_fixture_authority_id"] == v2_request.authority_id
        assert payload["compiler_fixture_authority_sha256"] == v2_request.authority_sha256
        assert all(
            point.compiler_fixture_version == "v2"
            and point.authority_id == registry["v2"]["authority_id"]
            and point.program_authority_sha256 == registry["v2"]["required_approval"]["authority_raw_sha256"]
            for point in expand_scan_v02(v2_request)
        )

        relative = synthetic_base.relative_to(ROOT) / "dispatch"
        monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v1")
        result = run_experiment_v02(CONFIG, relative, ROOT)
        request_payload = json.loads((result.run_dir / "request.json").read_text(encoding="utf-8"))
        assert request_payload["compiler_fixture_version"] == "v1"
        assert request_payload["compiler_fixture_authority_id"] == registry["v1"]["authority_id"]
        assert request_payload["compiler_fixture_authority_sha256"] == registry["v1"]["required_approval"]["authority_raw_sha256"]
        fixture_snapshot = json.loads((result.run_dir / "snapshots/compiler_fixture.json").read_text(encoding="utf-8"))
        assert fixture_snapshot == {
            "schema_version": "0.1",
            "compiler_fixture_version": "v1",
            "compiler_fixture_authority_id": registry["v1"]["authority_id"],
            "compiler_fixture_authority_sha256": registry["v1"]["required_approval"]["authority_raw_sha256"],
        }
        point_payload = json.loads((result.run_dir / "point_table.json").read_text(encoding="utf-8"))
        assert point_payload["compiler_fixture_version"] == "v1"
        assert all(
            point["compiler_fixture_authority_id"] == registry["v1"]["authority_id"]
            and point["compiler_fixture_authority_sha256"] == registry["v1"]["required_approval"]["authority_raw_sha256"]
            for point in point_payload["points"]
        )

        # A historical payload with no binding is always v1, even after v2 becomes default.
        legacy = dict(request_payload)
        for key in ("compiler_fixture_version", "compiler_fixture_authority_id", "compiler_fixture_authority_sha256"):
            legacy.pop(key)
        monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v2")
        assert fixture_binding_from_persisted_payload_v02(legacy).version == "v1"
        assert verify_experiment_run_v02(result.run_dir, ROOT).ok
        _tamper_canonical(result.run_dir / "request.json", lambda row: row.__setitem__("compiler_fixture_version", "missing"))
        assert not verify_experiment_run_v02(result.run_dir, ROOT).ok
        (result.run_dir / "request.json").write_bytes(canonical_json_bytes(request_payload))

        original_publish = v02_runner.atomic_publish
        monkeypatch.setattr(v02_runner, "atomic_publish", lambda _staging, _target: (_ for _ in ()).throw(OSError("stop")))
        interrupted_root = synthetic_base.relative_to(ROOT) / "recovery"
        with pytest.raises(OSError, match="stop"):
            monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v1")
            run_experiment_v02(CONFIG, interrupted_root, ROOT)
        staging = next((ROOT / interrupted_root / "staging").iterdir())
        monkeypatch.setattr(v02_runner, "atomic_publish", original_publish)
        monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v2")
        recovered = recover_interrupted_run_v02(ROOT / interrupted_root, staging.name)
        reason = json.loads((recovered.run_dir / "recovery.json").read_text(encoding="utf-8"))["reason"]
        assert "compiler_fixture_version=v1" in reason
        assert registry["v1"]["authority_id"] in reason
        assert registry["v2"]["authority_id"] not in reason

        tampered = dict(request_payload)
        tampered["compiler_fixture_version"] = "missing"
        with pytest.raises(ValueError, match="not registered"):
            fixture_binding_from_persisted_payload_v02(tampered)
        tampered = dict(request_payload)
        tampered["compiler_fixture_authority_sha256"] = "0" * 64
        with pytest.raises(ValueError, match="binding"):
            fixture_binding_from_persisted_payload_v02(tampered)
        tampered = dict(request_payload)
        tampered["compiler_fixture_authority_id"] = registry["v2"]["authority_id"]
        with pytest.raises(ValueError, match="binding"):
            fixture_binding_from_persisted_payload_v02(tampered)
    finally:
        shutil.rmtree(synthetic_base, ignore_errors=True)


def test_v02_frozen_legacy_v1_evidence_replays_and_recovers_after_v2_default(frozen_legacy_root, monkeypatch):
    synthetic, synthetic_base = _synthetic_approved_fixture_registry()
    registry = dict(v02_core._FIXTURE_VERSIONS)
    registry["v2"] = synthetic["v2"]
    monkeypatch.setattr(v02_core, "_FIXTURE_VERSIONS", registry)
    try:
        monkeypatch.setattr(v02_core, "DEFAULT_COMPILER_FIXTURE_VERSION", "v2")
        terminal_output = frozen_legacy_root / "tests/fixtures/runtime_v02_legacy_v1/terminal-output"
        terminal = next((terminal_output / "runs").iterdir())
        legacy_request = json.loads((terminal / "request.json").read_text(encoding="utf-8"))
        legacy_points = json.loads((terminal / "point_table.json").read_text(encoding="utf-8"))
        assert "compiler_fixture_version" not in legacy_request
        assert "compiler_fixture.json" not in {row.name for row in (terminal / "snapshots").iterdir()}
        assert all("compiler_fixture_version" not in point for point in legacy_points["points"])
        assert verify_experiment_run_v02(terminal, frozen_legacy_root).ok

        request_path = terminal / "request.json"
        request_raw = request_path.read_bytes()
        request_path.write_bytes(request_raw + b" ")
        assert not verify_experiment_run_v02(terminal, frozen_legacy_root).ok
        request_path.write_bytes(request_raw)
        event_path = terminal / "events.jsonl"
        event_raw = event_path.read_bytes()
        event_path.write_bytes(event_raw[:-1] + b" \n")
        assert not verify_experiment_run_v02(terminal, frozen_legacy_root).ok
        event_path.write_bytes(event_raw)
        authority_path = terminal / "snapshots/compiler_authority.json"
        authority_raw = authority_path.read_bytes()
        authority_path.write_bytes(authority_raw + b" ")
        assert not verify_experiment_run_v02(terminal, frozen_legacy_root).ok
        authority_path.write_bytes(authority_raw)
        assert verify_experiment_run_v02(terminal, frozen_legacy_root).ok

        link = frozen_legacy_root / "legacy-run-link"
        try:
            link.symlink_to(terminal, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable: {exc}")
        assert not verify_experiment_run_v02(link, frozen_legacy_root).ok
        link.unlink()

        interrupted = frozen_legacy_root / "tests/fixtures/runtime_v02_legacy_v1/interrupted-output"
        run_id = next((interrupted / "staging").iterdir()).name
        recovered = recover_interrupted_run_v02(interrupted, run_id)
        recovery = json.loads((recovered.run_dir / "recovery.json").read_text(encoding="utf-8"))
        assert "compiler_fixture_version=v1" in recovery["reason"]
        assert registry["v1"]["authority_id"] in recovery["reason"]
        assert registry["v2"]["authority_id"] not in recovery["reason"]
    finally:
        shutil.rmtree(synthetic_base, ignore_errors=True)


def test_v02_multi_variable_dataset_rejects_binary_and_order_tampering(tmp_path):
    request = load_experiment_request(CONFIG, ROOT)
    points = expand_scan_v02(request)
    results = [compile_point_v02(request, point).result for point in points]
    point_sha = "B" * 64
    data = tmp_path / "data"
    payload = write_dataset_v02(data, results, point_sha)
    assert payload["variable_order"] == list(RESULT_SCHEMA)
    validate_dataset_v02(data, 2, point_sha)

    binary = data / "trace_byte_count.bin"
    raw = bytearray(binary.read_bytes())
    raw[0] ^= 1
    binary.write_bytes(raw)
    with pytest.raises(ValueError, match="trace_byte_count"):
        validate_dataset_v02(data, 2, point_sha)


def test_v02_public_run_replay_catalog_and_claim_boundary(output_root):
    relative, absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    assert result.status == "completed" and result.catalog_indexed and result.warning is None
    report = verify_experiment_run(result.run_dir, ROOT)
    assert report.ok and report.status == "completed"
    manifest = json.loads((result.run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["claim_envelope"] == V02_CLAIM_ENVELOPE
    assert manifest["backend_id"] == "qcis_compiler_only_v1"
    assert set((result.run_dir / "data").iterdir()) == {
        result.run_dir / "data/dataset.json",
        *(result.run_dir / f"data/{name}.bin" for name in RESULT_SCHEMA),
    }
    rebuilt = rebuild_run_catalog(absolute)
    assert rebuilt.ok and rebuilt.indexed_runs == 1 and rebuilt.catalog_path.name == "catalog_v02.sqlite"
    connection = sqlite3.connect(rebuilt.catalog_path)
    try:
        assert connection.execute("SELECT runtime_schema,evidence_class FROM runs").fetchone() == ("0.2", "compiler_test_fixture")
    finally:
        connection.close()


def test_v02_failed_run_removes_partial_point_evidence_and_replays(output_root, monkeypatch):
    relative, _absolute = output_root
    original = v02_runner.compile_point_v02
    calls = 0

    def fail_second(request, point):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("injected compiler failure")
        return original(request, point)

    monkeypatch.setattr(v02_runner, "compile_point_v02", fail_second)
    result = run_experiment(CONFIG, relative, ROOT)
    assert result.status == "failed"
    assert not (result.run_dir / "points").exists()
    assert not (result.run_dir / "data").exists()
    assert verify_experiment_run(result.run_dir, ROOT).ok


@pytest.mark.parametrize("target", ["trace", "result", "claim", "dataset", "event_binding"])
def test_v02_verifier_rejects_replay_and_claim_tampering(output_root, target):
    relative, _absolute = output_root
    result = run_experiment(CONFIG, relative, ROOT)
    run_dir = result.run_dir
    point_id = next(iter(json.loads((run_dir / "point_table.json").read_text())["points"]))["point_id"]
    if target == "trace":
        path = run_dir / f"points/{point_id}/trace.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif target == "result":
        _tamper_canonical(run_dir / f"points/{point_id}/result.json", lambda payload: payload["values"].__setitem__("trace_byte_count", 1))
    elif target == "claim":
        _tamper_canonical(run_dir / "manifest.json", lambda payload: payload["claim_envelope"].__setitem__("physics_claim", "spectroscopy"))
    elif target == "dataset":
        path = run_dir / "data/logical_sample_count.bin"
        raw = bytearray(path.read_bytes())
        raw[-1] ^= 1
        path.write_bytes(raw)
    else:
        events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        completed = next(event for event in events if event["event_type"] == "point_completed")
        completed["payload"]["result_sha256"] = "A" * 64
        previous = None
        raw_lines = []
        for event in events:
            event["prev_event_sha256"] = previous
            base = {key: value for key, value in event.items() if key != "event_sha256"}
            event["event_sha256"] = hashlib.sha256(canonical_json_line_bytes(base)).hexdigest().upper()
            previous = event["event_sha256"]
            raw_lines.append(canonical_json_line_bytes(event))
        (run_dir / "events.jsonl").write_bytes(b"".join(raw_lines))
        _rebind_terminal(run_dir)
    report = verify_experiment_run(run_dir, ROOT)
    assert not report.ok


def test_v02_interrupted_atomic_publish_uses_versioned_no_resume_recovery(output_root, monkeypatch):
    relative, absolute = output_root
    original = v02_runner.atomic_publish

    def fail_publish(_staging, _target):
        raise OSError("injected v02 publication failure")

    monkeypatch.setattr(v02_runner, "atomic_publish", fail_publish)
    with pytest.raises(OSError, match="publication failure"):
        run_experiment(CONFIG, relative, ROOT)
    staging = [path for path in (absolute / "staging").iterdir() if path.is_dir()]
    assert len(staging) == 1
    run_id = staging[0].name
    monkeypatch.setattr(v02_runner, "atomic_publish", original)
    recovered = recover_interrupted_run(absolute, run_id)
    assert recovered.status == "interrupted"
    recovery_payload = json.loads((recovered.run_dir / "recovery.json").read_text(encoding="utf-8"))
    assert recovery_payload["reason"].startswith("runtime_v02|schema=0.2|compiler_fixture_version=v2|authority_id=")
    assert not (absolute / "staging" / run_id).exists()
    assert (absolute / "quarantine" / run_id).is_dir()


def test_v02_unreadable_recovery_request_is_quarantined_without_v01_fallback(output_root, monkeypatch):
    relative, absolute = output_root
    monkeypatch.setattr(v02_runner, "atomic_publish", lambda _staging, _target: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(OSError, match="stop"):
        run_experiment(CONFIG, relative, ROOT)
    staging = next(path for path in (absolute / "staging").iterdir() if path.is_dir())
    (staging / "request.json").write_bytes((staging / "request.json").read_bytes() + b" ")
    with pytest.raises(ValueError, match="quarantined"):
        recover_interrupted_run(absolute, staging.name)
    assert not staging.exists()
    assert (absolute / "quarantine" / staging.name).is_dir()
    record = json.loads((absolute / f"quarantine-records/{staging.name}.json").read_text(encoding="utf-8"))
    assert "canonical" in record["reason"]


def test_v02_request_and_run_verification_reject_links_before_read(output_root):
    relative, absolute = output_root
    request_link = ROOT / "tmp" / f"v02_request_link_{uuid.uuid4().hex}.yaml"
    request_link.parent.mkdir(exist_ok=True)
    try:
        request_link.symlink_to(CONFIG)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    try:
        with pytest.raises(ValueError, match="link"):
            load_experiment_request(request_link, ROOT)
    finally:
        request_link.unlink(missing_ok=True)

    result = run_experiment(CONFIG, relative, ROOT)
    run_link = absolute / "linked_run"
    try:
        run_link.symlink_to(result.run_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")
    assert not verify_experiment_run(run_link, ROOT).ok

    linked_run_id = str(uuid.uuid4())
    terminal_link = absolute / "runs" / linked_run_id
    try:
        terminal_link.symlink_to(result.run_dir, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"terminal directory symlink creation unavailable: {exc}")
    with pytest.raises(ValueError, match="cannot be determined"):
        recover_terminal_resource_lock_versioned(absolute, linked_run_id)

    point_id = json.loads((result.run_dir / "point_table.json").read_text())["points"][0]["point_id"]
    trace = result.run_dir / f"points/{point_id}/trace.json"
    backup = trace.with_suffix(".backup")
    trace.rename(backup)
    try:
        trace.symlink_to(backup.name)
    except OSError as exc:
        backup.rename(trace)
        pytest.skip(f"file symlink creation unavailable: {exc}")
    assert not verify_experiment_run(result.run_dir, ROOT).ok
