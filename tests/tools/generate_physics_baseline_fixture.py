"""Generate the deterministic, test-only Stage 1/2/2.1 physics baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqvm.hamiltonian import (
    build_rebaseline_approval_payload,
    build_rebaseline_manifest_payload,
    canonical_json_bytes,
    raw_file_sha256,
    verify_hamiltonian,
)
from sqvm.spectrum import (
    load_spectrum_config,
    load_stage2_rebaseline_approval,
    load_stage2_rebaseline_manifest,
    run_stage2_dense_gap_consistency,
    validate_spectrum_provenance,
)
from tests.support.fixture_loader import _aggregate, verify_fixture_manifest


FIXTURE_ID = "physics_baseline_v1"
FIXED_CLOCK_UTC = "2026-07-24T00:00:00.000000Z"
AUTHORITY = "test_only_non_production"
NUMERIC_ABS_TOLERANCE = 1e-9
DERIVED_NUMERIC_FILES = {
    "artifacts/hamiltonian_artifacts.json",
    "metadata/rebaseline_approval.json",
    "metadata/rebaseline_manifest.json",
    "provenance.json",
}
NUMERIC_TEXT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _fixture_relative(repository_root: Path, path: Path) -> str:
    return path.relative_to(repository_root).as_posix()


def _derived_config(source: Path, replacements: dict[str, str]) -> str:
    text = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise ValueError(f"expected exactly one config source value: {old}")
        text = text.replace(old, new)
    return text if text.endswith("\n") else text + "\n"


def _write_manifest(target: Path) -> None:
    files: list[dict[str, Any]] = []
    paths = sorted(
        (path for path in target.rglob("*") if path.is_file() and path.name != "provenance.json"),
        key=lambda path: path.relative_to(target).as_posix().encode("utf-8"),
    )
    for path in paths:
        raw = path.read_bytes()
        files.append(
            {
                "path": path.relative_to(target).as_posix(),
                "byte_length": len(raw),
                "raw_sha256": _sha256(raw),
            }
        )
    rows = [(row["path"], row["byte_length"], row["raw_sha256"]) for row in files]
    _write_json(
        target / "provenance.json",
        {
            "schema_version": "0.1",
            "fixture_id": FIXTURE_ID,
            "generator_version": "0.1",
            "generator_raw_sha256": _sha256(Path(__file__).read_bytes()),
            "source_authority": AUTHORITY,
            "fixed_clock_utc": FIXED_CLOCK_UTC,
            "files": files,
            "aggregate_sha256": _aggregate(rows),
        },
    )


def generate(repository_root: Path, target: Path) -> None:
    repository_root = repository_root.resolve()
    target = target.resolve()
    expected_target = repository_root / "tests/fixtures" / FIXTURE_ID
    if target != expected_target:
        raise ValueError(f"fixture target must be {expected_target}")
    if Path.cwd().resolve() != repository_root:
        raise ValueError("generator working directory must equal --repository-root")
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"fixture target already exists: {target}")

    artifacts = target / "artifacts"
    configs = target / "configs"
    metadata = target / "metadata"
    artifacts.mkdir(parents=True)
    configs.mkdir()
    metadata.mkdir()

    device_artifact = artifacts / "device_artifacts.json"
    device_config = configs / "device.yaml"
    shutil.copy2(
        repository_root / "tests/fixtures/device_model_v1/output/stage_01_device_model/device_artifacts.json",
        device_artifact,
    )
    shutil.copy2(repository_root / "configs/devices/2q1c2r.yaml", device_config)

    authority_record = metadata / "fixture_authority.json"
    _write_json(
        authority_record,
        {
            "schema_version": "0.1",
            "artifact_type": "physics_test_fixture_authority",
            "artifact_version": "1",
            "authority": AUTHORITY,
            "production_evidence_claimed": False,
            "historical_replay_claimed": False,
            "purpose": "deterministic_clean_checkout_physics_tests",
        },
    )

    hamiltonian_config = configs / "hamiltonian.yaml"
    hamiltonian_config.write_text(
        _derived_config(
            repository_root / "configs/hamiltonians/2q1c_charge_basis.yaml",
            {
                "output/stage_01_device_model/device_artifacts.json": _fixture_relative(
                    repository_root, device_artifact
                )
            },
        ),
        encoding="utf-8",
        newline="\n",
    )
    stage2_output = target / "stage2-generated"
    report = verify_hamiltonian(hamiltonian_config.relative_to(repository_root), stage2_output)
    if not report.ok:
        raise ValueError(f"fixture Stage 2 generation failed: {report.errors}")
    candidate = artifacts / "hamiltonian_artifacts.json"
    shutil.move(stage2_output / "hamiltonian_artifacts.json", candidate)
    shutil.rmtree(stage2_output)
    candidate_payload = json.loads(candidate.read_bytes())
    candidate_payload["source_device_artifacts"] = _fixture_relative(repository_root, device_artifact)
    candidate_payload["hamiltonian_config"]["path"] = _fixture_relative(
        repository_root, hamiltonian_config
    )
    _write_json(candidate, candidate_payload)

    previous = artifacts / "previous_hamiltonian_artifacts.json"
    _write_json(
        previous,
        {
            "artifact_version": "0.1",
            "authority": AUTHORITY,
            "production_evidence_claimed": False,
        },
    )
    previous_hash = raw_file_sha256(previous)
    anchor = metadata / "legacy_baseline_anchor.json"
    _write_json(
        anchor,
        {
            "schema_version": "0.1",
            "artifact_type": "stage_02_legacy_baseline_anchor",
            "artifact_version": "0.1",
            "decision": "accepted",
            "previous_stage2_artifacts_sha256": previous_hash,
            "expected_sha256": previous_hash,
            "historical_review_path": _fixture_relative(repository_root, authority_record),
            "approved_by": "user",
            "acceptance_scope": AUTHORITY,
            "production_evidence_claimed": False,
        },
    )

    manifest_path = metadata / "rebaseline_manifest.json"
    manifest_payload = build_rebaseline_manifest_payload(
        legacy_anchor_path=anchor,
        previous_stage2_artifact_path=previous,
        candidate_stage2_artifact_path=candidate,
        device_config_path=device_config,
        device_artifacts_path=device_artifact,
        hamiltonian_config_path=hamiltonian_config,
        stage2_cli_path=repository_root / "src/sqvm/__main__.py",
        old_to_new_numeric_deltas={"authority": AUTHORITY, "not_physical_evidence": True},
        test_summary={"clean_checkout_fixture": True},
        verify_device_summary={"fixture_bytes_verified": True},
        verify_hamiltonian_summary={"ok": True},
        determinism_checks={"fixture_manifest_bound": True},
        repository_root=repository_root,
        git_commit="TEST-ONLY-NON-PRODUCTION",
        git_dirty=False,
    )
    _write_json(manifest_path, manifest_payload)
    approval_path = metadata / "rebaseline_approval.json"
    _write_json(
        approval_path,
        build_rebaseline_approval_payload(
            decision="approved",
            manifest_path=manifest_path,
            candidate_stage2_artifact_path=candidate,
            legacy_anchor_path=anchor,
            previous_stage2_artifact_path=previous,
            review_record_path=_fixture_relative(repository_root, authority_record),
            blocking_findings=(),
        ),
    )

    common_replacements = {
        "configs/hamiltonians/2q1c_charge_basis.yaml": _fixture_relative(repository_root, hamiltonian_config),
        "output/stage_02_hamiltonian/hamiltonian_artifacts.json": _fixture_relative(repository_root, candidate),
        "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json": _fixture_relative(
            repository_root, manifest_path
        ),
        "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json": _fixture_relative(
            repository_root, approval_path
        ),
    }
    for source_name, target_name in (
        ("2q1c_static_smoke.yaml", "spectrum_smoke.yaml"),
        ("2q1c_static.yaml", "spectrum_acceptance.yaml"),
    ):
        replacements = dict(common_replacements)
        replacements.update(
            {
                "output/stage_03_solver_validation/eigsh_validation.json": _fixture_relative(
                    repository_root, artifacts / "solver_validation.json"
                ),
                "output/stage_03_solver_validation/eigsh_validation_approval.json": _fixture_relative(
                    repository_root, artifacts / "solver_validation_approval.json"
                ),
            }
        )
        (configs / target_name).write_text(
            _derived_config(repository_root / "configs/spectra" / source_name, replacements),
            encoding="utf-8",
            newline="\n",
        )

    _write_manifest(target)
    validate_physics_fixture(target)


def validate_physics_fixture(target: Path) -> None:
    target = target.resolve()
    manifest = verify_fixture_manifest(target)
    if manifest["fixture_id"] != FIXTURE_ID or manifest["source_authority"] != AUTHORITY:
        raise ValueError("physics fixture identity or authority is invalid")
    if manifest["generator_raw_sha256"] != _sha256(Path(__file__).read_bytes()):
        raise ValueError("physics fixture generator bytes do not match provenance")
    authority = json.loads((target / "metadata/fixture_authority.json").read_bytes())
    if authority.get("authority") != AUTHORITY or authority.get("production_evidence_claimed") is not False:
        raise ValueError("physics fixture makes an invalid authority claim")
    for name in ("spectrum_smoke.yaml", "spectrum_acceptance.yaml"):
        config = load_spectrum_config(target / "configs" / name)
        rebaseline = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
        approval = load_stage2_rebaseline_approval(config.source_rebaseline_approval)
        provenance = validate_spectrum_provenance(config, rebaseline, approval)
        gap = run_stage2_dense_gap_consistency(config, provenance)
        if not gap.ok:
            raise ValueError(f"physics fixture Stage 2 dense gap check failed: {gap.errors}")


def _all_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _semantic_differences(actual: Any, expected: Any, path: str = "$") -> list[str]:
    if isinstance(actual, float) and isinstance(expected, float):
        if math.isclose(
            actual,
            expected,
            rel_tol=0.0,
            abs_tol=NUMERIC_ABS_TOLERANCE,
        ):
            return []
        return [f"{path}: generated={actual!r}, committed={expected!r}"]
    if type(actual) is not type(expected):
        return [
            f"{path}: type generated={type(actual).__name__}, "
            f"committed={type(expected).__name__}"
        ]
    if isinstance(actual, dict):
        if set(actual) != set(expected):
            return [f"{path}: object keys differ"]
        differences: list[str] = []
        for key in sorted(actual, key=lambda value: value.encode("utf-8")):
            differences.extend(
                _semantic_differences(actual[key], expected[key], f"{path}.{key}")
            )
            if len(differences) >= 10:
                break
        return differences
    if isinstance(actual, list):
        if len(actual) != len(expected):
            return [f"{path}: list lengths differ"]
        differences = []
        for index, (actual_item, expected_item) in enumerate(
            zip(actual, expected, strict=True)
        ):
            differences.extend(
                _semantic_differences(actual_item, expected_item, f"{path}[{index}]")
            )
            if len(differences) >= 10:
                break
        return differences
    if (
        isinstance(actual, str)
        and path.startswith("$.checks[")
        and path.endswith(".message")
    ):
        return _numeric_text_differences(actual, expected, path)
    if actual == expected:
        return []
    return [f"{path}: generated={actual!r}, committed={expected!r}"]


def _numeric_text_differences(actual: str, expected: str, path: str) -> list[str]:
    actual_values = [float(value) for value in NUMERIC_TEXT_PATTERN.findall(actual)]
    expected_values = [float(value) for value in NUMERIC_TEXT_PATTERN.findall(expected)]
    actual_scaffold = NUMERIC_TEXT_PATTERN.sub("{number}", actual)
    expected_scaffold = NUMERIC_TEXT_PATTERN.sub("{number}", expected)
    if actual_scaffold != expected_scaffold or len(actual_values) != len(expected_values):
        return [f"{path}: generated={actual!r}, committed={expected!r}"]
    for actual_value, expected_value in zip(actual_values, expected_values, strict=True):
        if not math.isclose(
            actual_value,
            expected_value,
            rel_tol=0.0,
            abs_tol=NUMERIC_ABS_TOLERANCE,
        ):
            return [f"{path}: generated={actual!r}, committed={expected!r}"]
    return []


def _assert_regeneration_equivalent(actual: Path, expected: Path) -> None:
    actual_files = _all_bytes(actual)
    expected_files = _all_bytes(expected)
    if set(actual_files) != set(expected_files):
        raise ValueError("generated physics fixture file set differs from --verify-against")
    exact_differences = [
        name
        for name in sorted(actual_files, key=lambda value: value.encode("utf-8"))
        if name not in DERIVED_NUMERIC_FILES and actual_files[name] != expected_files[name]
    ]
    if exact_differences:
        raise ValueError(
            "generated physics fixture non-numeric bytes differ from --verify-against: "
            + ", ".join(exact_differences)
        )
    actual_artifact = json.loads(actual_files["artifacts/hamiltonian_artifacts.json"])
    expected_artifact = json.loads(expected_files["artifacts/hamiltonian_artifacts.json"])
    numeric_differences = _semantic_differences(actual_artifact, expected_artifact)
    if numeric_differences:
        raise ValueError(
            "generated Hamiltonian exceeds regeneration tolerance: " + "; ".join(numeric_differences)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--verify-against", type=Path)
    args = parser.parse_args(argv)
    if args.verify is not None:
        if args.target is not None or args.verify_against is not None:
            raise ValueError("--verify cannot be combined with generation arguments")
        validate_physics_fixture(args.verify)
        return 0
    if args.target is None:
        parser.error("--target is required for generation")
    generate(args.repository_root, args.target)
    if args.verify_against is not None:
        verify_fixture_manifest(args.verify_against)
        _assert_regeneration_equivalent(args.target.resolve(), args.verify_against.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
