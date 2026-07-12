import ast
import json
from pathlib import Path

import pytest

import sqvm.hamiltonian.rebaseline as rebaseline_module
from sqvm.hamiltonian import (
    RebaselineGateError,
    build_rebaseline_approval_payload,
    build_rebaseline_manifest_payload,
    canonical_json_bytes,
    raw_file_sha256,
    rebuild_stage2_low_energy_spectrum,
    validate_rebaseline_manifest,
    verify_hamiltonian,
)


DEVICE_ARTIFACT = Path("output/stage_01_device_model/device_artifacts.json").resolve()
DEVICE_CONFIG = Path("configs/devices/2q1c2r.yaml").resolve()
STAGE2_CLI = Path("src/sqvm/__main__.py").resolve()


def _write_small_config(tmp_path: Path) -> Path:
    config = tmp_path / "hamiltonian.yaml"
    config.write_text(
        "schema_version: '0.1'\n"
        "hamiltonian:\n"
        "  name: stage2_rebuild_fixture\n"
        f"  source_device_artifacts: '{DEVICE_ARTIFACT.as_posix()}'\n"
        "  basis:\n"
        "    q1: {charge_cutoff: 1}\n"
        "    c: {charge_cutoff: 1}\n"
        "    q2: {charge_cutoff: 1}\n"
        "  solver:\n"
        "    num_eigenvalues: 12\n"
        "    method: eigh\n",
        encoding="utf-8",
    )
    return config


def _candidate(tmp_path: Path) -> tuple[Path, Path]:
    config = _write_small_config(tmp_path)
    output = tmp_path / "candidate"
    verify_hamiltonian(config, output)
    return config, output / "hamiltonian_artifacts.json"


def _legacy_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "previous_hamiltonian_artifacts.json"
    path.write_bytes(canonical_json_bytes({"artifact_version": "0.1"}))
    return path


def _anchor_fixture(tmp_path: Path, legacy: Path, decision: str) -> Path:
    digest = raw_file_sha256(legacy)
    path = tmp_path / f"legacy_anchor_{decision}.json"
    path.write_bytes(
        canonical_json_bytes(
            {
                "schema_version": "0.1",
                "artifact_type": "stage_02_legacy_baseline_anchor",
                "artifact_version": "0.1",
                "decision": decision,
                "previous_stage2_artifacts_sha256": digest,
                "expected_sha256": digest,
                "historical_review_path": "docs/decisions/2026-07-09-stage2-implementation-review.md",
                "approved_by": "user",
            }
        )
    )
    return path


def _manifest_kwargs(tmp_path: Path, anchor: Path, legacy: Path) -> dict:
    config, candidate = _candidate(tmp_path)
    return {
        "legacy_anchor_path": anchor,
        "previous_stage2_artifact_path": legacy,
        "candidate_stage2_artifact_path": candidate,
        "device_config_path": DEVICE_CONFIG,
        "device_artifacts_path": DEVICE_ARTIFACT,
        "hamiltonian_config_path": config,
        "stage2_cli_path": STAGE2_CLI,
        "old_to_new_numeric_deltas": {"gaps_GHz": [0.0]},
        "test_summary": {"passed": 1},
        "verify_device_summary": {"ok": True},
        "verify_hamiltonian_summary": {"ok": True},
        "determinism_checks": {"byte_for_byte": True},
        "git_commit": "fixture-commit",
        "git_dirty": True,
    }


def _approval_kwargs(manifest: Path, manifest_kwargs: dict) -> dict:
    return {
        "manifest_path": manifest,
        "candidate_stage2_artifact_path": manifest_kwargs["candidate_stage2_artifact_path"],
        "legacy_anchor_path": manifest_kwargs["legacy_anchor_path"],
        "previous_stage2_artifact_path": manifest_kwargs["previous_stage2_artifact_path"],
        "review_record_path": "docs/decisions/review.md",
    }


def _valid_approval_fixture(tmp_path: Path) -> tuple[Path, dict, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    legacy = _legacy_fixture(tmp_path)
    anchor = _anchor_fixture(tmp_path, legacy, "accepted")
    manifest_kwargs = _manifest_kwargs(tmp_path, anchor, legacy)
    manifest = tmp_path / "rebaseline_manifest.json"
    manifest.write_bytes(canonical_json_bytes(build_rebaseline_manifest_payload(**manifest_kwargs)))
    return manifest, manifest_kwargs, _approval_kwargs(manifest, manifest_kwargs)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_bytes(canonical_json_bytes(payload))


def test_dense_12_gap_rebuild_matches_candidate(tmp_path):
    config, candidate = _candidate(tmp_path)
    report = rebuild_stage2_low_energy_spectrum(config, candidate)
    assert report.ok, report.errors
    assert report.compared_gap_count == 12
    assert report.max_abs_difference_GHz is not None
    assert report.max_abs_difference_GHz <= 1e-9


def test_dense_12_gap_rebuild_reports_error_above_tolerance(tmp_path):
    config, candidate = _candidate(tmp_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["eigenvalue_gaps_GHz"][1] += 2e-9
    candidate.write_bytes(canonical_json_bytes(payload))

    report = rebuild_stage2_low_energy_spectrum(config, candidate)
    assert not report.ok
    assert report.max_abs_difference_GHz > 1e-9
    assert any("exceeds" in error for error in report.errors)


def test_stage2_rebuild_module_has_no_spectrum_import():
    source = Path(rebaseline_module.__file__).read_text(encoding="utf-8")
    imports = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imports.update(
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert not any(name == "sqvm.spectrum" or name.startswith("sqvm.spectrum.") for name in imports)


@pytest.mark.parametrize("decision", ["pending", "rejected"])
def test_unaccepted_anchor_blocks_manifest_and_approval(tmp_path, decision):
    legacy = _legacy_fixture(tmp_path)
    anchor = _anchor_fixture(tmp_path, legacy, decision)
    kwargs = _manifest_kwargs(tmp_path, anchor, legacy)
    with pytest.raises(RebaselineGateError, match="not accepted"):
        build_rebaseline_manifest_payload(**kwargs)

    manifest, accepted_kwargs, approval_kwargs = _valid_approval_fixture(tmp_path / "approval")
    accepted_anchor = accepted_kwargs["legacy_anchor_path"]
    anchor_payload = _read_json(accepted_anchor)
    anchor_payload["decision"] = decision
    _write_json(accepted_anchor, anchor_payload)
    with pytest.raises(RebaselineGateError, match="not accepted"):
        build_rebaseline_approval_payload(
            decision="approved",
            blocking_findings=(),
            **approval_kwargs,
        )


def test_missing_anchor_blocks_manifest_and_approval(tmp_path):
    legacy = _legacy_fixture(tmp_path)
    missing = tmp_path / "missing_anchor.json"
    kwargs = _manifest_kwargs(tmp_path, missing, legacy)
    with pytest.raises(RebaselineGateError, match="missing"):
        build_rebaseline_manifest_payload(**kwargs)

    manifest, accepted_kwargs, approval_kwargs = _valid_approval_fixture(tmp_path / "approval")
    accepted_kwargs["legacy_anchor_path"].unlink()
    with pytest.raises(RebaselineGateError, match="missing"):
        build_rebaseline_approval_payload(
            decision="approved",
            blocking_findings=(),
            **approval_kwargs,
        )


def test_manifest_and_approval_bind_all_hashes_deterministically(tmp_path):
    legacy = _legacy_fixture(tmp_path)
    anchor = _anchor_fixture(tmp_path, legacy, "accepted")
    kwargs = _manifest_kwargs(tmp_path, anchor, legacy)

    first = build_rebaseline_manifest_payload(**kwargs)
    second = build_rebaseline_manifest_payload(**kwargs)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert first["candidate_stage2_artifact_version"] == "0.2"
    assert first["sha256"]["stage2_artifacts_sha256"] == raw_file_sha256(
        kwargs["candidate_stage2_artifact_path"]
    )
    assert first["sha256"]["stage2_model_source_tree_sha256"]

    manifest = tmp_path / "rebaseline_manifest.json"
    manifest.write_bytes(canonical_json_bytes(first))
    validated = validate_rebaseline_manifest(
        manifest_path=manifest,
        candidate_stage2_artifact_path=kwargs["candidate_stage2_artifact_path"],
        legacy_anchor_path=anchor,
        previous_stage2_artifact_path=legacy,
    )
    assert validated == first
    approval = build_rebaseline_approval_payload(
        decision="approved",
        manifest_path=manifest,
        candidate_stage2_artifact_path=kwargs["candidate_stage2_artifact_path"],
        legacy_anchor_path=anchor,
        previous_stage2_artifact_path=legacy,
        review_record_path="docs/decisions/review.md",
        blocking_findings=(),
    )
    assert approval["manifest_sha256"] == raw_file_sha256(manifest)
    assert approval["legacy_baseline_anchor_sha256"] == raw_file_sha256(anchor)
    assert approval["previous_stage2_artifacts_sha256"] == raw_file_sha256(legacy)


def test_approval_rejects_minimal_handwritten_manifest(tmp_path):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    _write_json(manifest, {"artifact_type": "stage_02_1_hamiltonian_rebaseline"})
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "9.9"),
        ("artifact_version", "9.9"),
        ("candidate_stage2_artifact_version", "9.9"),
    ],
)
def test_approval_rejects_manifest_version_tampering(tmp_path, field, value):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    payload[field] = value
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


def test_approval_rejects_missing_or_extra_manifest_paths(tmp_path):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    original = _read_json(manifest)
    for mutation in ("missing", "extra"):
        payload = json.loads(json.dumps(original))
        if mutation == "missing":
            del payload["paths"]["candidate_stage2_artifact"]
        else:
            payload["paths"]["unexpected"] = "unexpected.json"
        _write_json(manifest, payload)
        with pytest.raises(RebaselineGateError):
            build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    "field",
    ["candidate_stage2_artifact", "legacy_baseline_anchor", "previous_stage2_artifact"],
)
def test_approval_rejects_explicit_path_mismatch(tmp_path, field):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    original = Path(payload["paths"][field])
    replacement = tmp_path / f"other_{field}.json"
    replacement.write_bytes(original.read_bytes())
    payload["paths"][field] = replacement.as_posix()
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError, match="explicit approval path"):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize("field", ["device_config", "device_artifacts", "hamiltonian_config", "stage2_cli"])
def test_approval_rejects_other_critical_path_misdirection(tmp_path, field):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    payload["paths"][field] = STAGE2_CLI.as_posix()
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    "field",
    [
        "device_config_sha256",
        "device_artifacts_sha256",
        "hamiltonian_config_sha256",
        "stage2_model_source_tree_sha256",
        "legacy_baseline_anchor_sha256",
        "stage2_artifacts_sha256",
        "stage2_cli_sha256_at_rebaseline",
    ],
)
@pytest.mark.parametrize("mutation", ["missing", "tampered"])
def test_approval_rejects_each_missing_or_tampered_manifest_hash(tmp_path, field, mutation):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    if mutation == "missing":
        del payload["sha256"][field]
    else:
        payload["sha256"][field] = "0" * 64
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "9.9"),
        ("artifact_type", "not_stage_02_hamiltonian"),
        ("artifact_version", "9.9"),
    ],
)
def test_approval_rejects_candidate_identity_tampering(tmp_path, field, value):
    _, manifest_kwargs, approval_kwargs = _valid_approval_fixture(tmp_path)
    candidate = manifest_kwargs["candidate_stage2_artifact_path"]
    payload = _read_json(candidate)
    payload[field] = value
    _write_json(candidate, payload)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    "field",
    ["device_artifacts_sha256", "hamiltonian_config_sha256", "stage2_model_source_tree_sha256"],
)
def test_approval_rejects_candidate_provenance_tampering_with_updated_artifact_hash(tmp_path, field):
    manifest, manifest_kwargs, approval_kwargs = _valid_approval_fixture(tmp_path)
    candidate = manifest_kwargs["candidate_stage2_artifact_path"]
    candidate_payload = _read_json(candidate)
    candidate_payload["provenance"][field] = "0" * 64
    _write_json(candidate, candidate_payload)

    manifest_payload = _read_json(manifest)
    manifest_payload["sha256"]["stage2_artifacts_sha256"] = raw_file_sha256(candidate)
    _write_json(manifest, manifest_payload)
    with pytest.raises(RebaselineGateError, match="provenance"):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


def test_approval_rejects_noncanonical_manifest_bytes(tmp_path):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    manifest.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=4), encoding="utf-8")
    with pytest.raises(RebaselineGateError, match="canonical"):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_from_stage2_artifact_version", "9.9"),
        ("previous_stage2_artifacts_sha256", "0" * 64),
        ("legacy_baseline_anchor_sha256", "0" * 64),
    ],
)
def test_approval_rejects_previous_or_anchor_binding_tampering(tmp_path, field, value):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    payload[field] = value
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


def test_approval_rejects_previous_artifact_version_tampering(tmp_path):
    manifest, manifest_kwargs, approval_kwargs = _valid_approval_fixture(tmp_path)
    previous = manifest_kwargs["previous_stage2_artifact_path"]
    previous_payload = _read_json(previous)
    previous_payload["artifact_version"] = "9.9"
    _write_json(previous, previous_payload)

    anchor = manifest_kwargs["legacy_anchor_path"]
    anchor_payload = _read_json(anchor)
    new_previous_hash = raw_file_sha256(previous)
    anchor_payload["previous_stage2_artifacts_sha256"] = new_previous_hash
    anchor_payload["expected_sha256"] = new_previous_hash
    _write_json(anchor, anchor_payload)

    manifest_payload = _read_json(manifest)
    manifest_payload["previous_stage2_artifacts_sha256"] = new_previous_hash
    manifest_payload["legacy_baseline_anchor_sha256"] = raw_file_sha256(anchor)
    manifest_payload["sha256"]["legacy_baseline_anchor_sha256"] = raw_file_sha256(anchor)
    _write_json(manifest, manifest_payload)
    with pytest.raises(RebaselineGateError, match="previous Stage 2 artifact artifact_version"):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize("field", list(rebaseline_module._MANIFEST_MAPPING_FIELDS))
def test_approval_rejects_nonmapping_manifest_summaries(tmp_path, field):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    payload[field] = []
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError, match="mapping"):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(("field", "value"), [("git_commit", ""), ("git_dirty", None)])
def test_approval_rejects_invalid_git_fields(tmp_path, field, value):
    manifest, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    payload = _read_json(manifest)
    payload[field] = value
    _write_json(manifest, payload)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision="approved", blocking_findings=(), **approval_kwargs)


@pytest.mark.parametrize(
    ("decision", "findings"),
    [("invalid", ()), ("approved", ("blocking",))],
)
def test_approval_rejects_invalid_decision_or_approved_with_findings(tmp_path, decision, findings):
    _, _, approval_kwargs = _valid_approval_fixture(tmp_path)
    with pytest.raises(RebaselineGateError):
        build_rebaseline_approval_payload(decision=decision, blocking_findings=findings, **approval_kwargs)
