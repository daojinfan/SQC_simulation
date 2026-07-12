"""Read-only validation of the frozen Stage 4 upstream receipt."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes, stage2_model_source_tree_sha256


_STAGE2_SOURCE_SHA256 = "0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972"
_STAGE3_SOURCE_SHA256 = "263125AAF4CBEE1572E254A41916F8E0B519BEDB6DA80956F8789AA5422A276E"
_ENVIRONMENT_SHA256 = "40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12"
_STAGE2_CLI_SHA256 = "06078C7EBFB6EBB48894753F6D10E6E4BCA06A93DA94D2A46E40242569A5D16B"

FROZEN_RECEIPT: tuple[tuple[str, str, str], ...] = (
    ("stage2_artifact", "output/stage_02_hamiltonian/hamiltonian_artifacts.json", "DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66"),
    ("stage2_1_manifest", "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json", "4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D"),
    ("stage2_1_approval", "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json", "CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9"),
    ("stage3_1_artifact", "output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json", "76C539FF22EAB54E5E10C93526E20AFB68A39507BCD9BE2DAD5A2897282FAD35"),
    ("stage3_1_notebook", "output/stage_03_1_q1_q2_coupling/verification.ipynb", "444DEEE581BA9B2EE1CC90E0D8291E3E0837BC7D407F14284F821A8C9927D441"),
    ("stage3_1_report", "output/stage_03_1_q1_q2_coupling/verification_report.json", "F5F82FB7D9770CFE6ADEE6639A67C46F781891FF8F94F5247A54F4722655F3CD"),
    ("stage3_1_approval", "output/stage_03_1_q1_q2_coupling/acceptance_approval.json", "5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D"),
    ("device_config", "configs/devices/2q1c2r.yaml", "CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F"),
    ("device_artifact", "output/stage_01_device_model/device_artifacts.json", "B771B72ED33FE104E8940730684710B23BB9278451230318D2B52543C77CE86D"),
    ("hamiltonian_config", "configs/hamiltonians/2q1c_charge_basis.yaml", "B65D1C57083D26F3C0CE1B0F980B07B685F4F21BCBD955DCD73D933416D84E5E"),
    ("legacy_anchor", "output/stage_02_1_hamiltonian_rebaseline/legacy_baseline_anchor.json", "FE489B2476FA3AE3121BEBB1FA06BF5EF1B74DEDAD4546458C7687EA7431C88D"),
    ("previous_stage2_artifact", "output/stage_02_1_hamiltonian_rebaseline/previous_hamiltonian_artifacts.json", "222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C"),
    ("stage3_1_config", "configs/spectra/2q1c_q1q2_coupling.yaml", "B2AFD3FA9FF9EA35153DEBF8B5E6C77D0A3E7C490270CEB6C89AC0D317594F2A"),
    ("stage3_1_design_freeze_manifest", "docs/decisions/2026-07-11-stage3-1-design-freeze.json", "AB7642A9C170E98B4319D1D453A5B4962FA50C3236F9DABA165E27EF7C9F7F32"),
    ("stage3_1_solver_validation", "output/stage_03_1_solver_validation/eigsh_validation.json", "6D58A5D77988978E7B9377EC06841D9AD2F31D296D358C8874F7DF5C9EA2EE0D"),
    ("stage3_1_solver_validation_approval", "output/stage_03_1_solver_validation/eigsh_validation_approval.json", "093EFA68E396152A7F2BF82A412320F56944C2276752D7B289A701BB2CC154FA"),
    ("stage3_1_final_review", "docs/decisions/2026-07-11-stage3-1-final-acceptance-review.md", "A01A20459C03602A0BA640D17AB7B7DB989F23FAE1B9972ED47B3FB401B274A0"),
    ("legacy_anchor_acceptance_record", "docs/decisions/2026-07-11-stage2-legacy-anchor-acceptance.md", "E85CD03A8FA4EF449C6C038B1CDDAD21E4321003C328057E0911069B398B307D"),
    ("stage3_1_design_freeze_review", "docs/decisions/2026-07-11-stage3-1-design-freeze-review.md", "7EB52CECF32B2692AEBECAECCFEEE0AA249B6D075E756F1B697AEF5E95B2D64D"),
    ("stage3_1_objective_decision", "docs/decisions/2026-07-11-stage3-objective-correction.md", "95A45E9E3205241265386FC9F7BDF8BB0DDB043E6A9D33D5714D7C68964BD795"),
    ("stage3_1_detailed_design", "docs/designs/03_1_q1_q2_coupling_sweep_design.md", "A2C9E8E28F778813818A5B76C817C54EB459D848E85678F5850FAB1451123DE5"),
    ("stage3_1_plan", "docs/stages/03_1_q1_q2_coupling_sweep_plan.md", "5878A186A6A0673FC8FA07747D7822D022667895B9C04D78D892933A8C166F7C"),
    ("stage3_1_solver_validation_review", "docs/decisions/2026-07-11-stage3-1-solver-validation-c2-2-review.md", "86D8AA27DB5AA1EA4A7BFF38C40C80FBB0EA40C68EEB31F581C9197F5DE93187"),
    ("stage3_1_dense_pilot", "output/stage_03_1_solver_validation/dense_pilot.json", "AFE2A8B6F531468360BB60B535D86B216652977C6685C9A6B1DDE4E7237DC916"),
    ("stage3_1_c2_remediation", "docs/decisions/2026-07-11-stage3-1-c2-remediation.md", "9CA7FFA870DA6F83581D04DE067D5F618E388BAEC72162E0CBBF752487158C04"),
    ("stage3_1_c2_2_remediation", "docs/decisions/2026-07-11-stage3-1-c2-2-fixed-refinement-remediation.md", "E356551C706F3C2372620311D69D8A7C4FF1E72825680D43C818D53B0746BB7C"),
)

_RECEIPT_BY_KEY = {key: (relative, digest) for key, relative, digest in FROZEN_RECEIPT}
_LEGACY_NONCANONICAL = {"device_artifact", "previous_stage2_artifact"}


@dataclass(frozen=True)
class FrozenUpstreamReceiptReport:
    ok: bool
    stage2_1_ready: bool
    stage3_1_ready: bool
    current_hashes: Mapping[str, str]
    errors: tuple[str, ...]


def validate_frozen_stage4_upstream_receipt(
    *,
    repository_root: str | Path,
    stage2_artifact_path: str | Path,
    stage2_1_manifest_path: str | Path,
    stage2_1_approval_path: str | Path,
    stage3_1_artifact_path: str | Path,
    stage3_1_report_path: str | Path,
    stage3_1_approval_path: str | Path,
) -> FrozenUpstreamReceiptReport:
    """Replay the immutable Stage 2.1 and Stage 3.1 receipt from current bytes."""

    arguments = {
        "repository_root": repository_root,
        "stage2_artifact": stage2_artifact_path,
        "stage2_1_manifest": stage2_1_manifest_path,
        "stage2_1_approval": stage2_1_approval_path,
        "stage3_1_artifact": stage3_1_artifact_path,
        "stage3_1_report": stage3_1_report_path,
        "stage3_1_approval": stage3_1_approval_path,
    }
    for label, value in arguments.items():
        if not isinstance(value, (str, Path)):
            raise TypeError(f"{label} must be str or Path")

    try:
        root = Path(repository_root).resolve()
    except (OSError, RuntimeError):
        return _failed_report("repository_root path resolution failed")
    expected_paths: dict[str, Path] = {}
    for key, relative, _ in FROZEN_RECEIPT:
        try:
            expected_paths[key] = (root / relative).resolve()
        except (OSError, RuntimeError):
            return _failed_report(f"{key} path resolution failed")
    supplied: dict[str, Path] = {}
    for key, value in arguments.items():
        if key == "repository_root":
            continue
        try:
            supplied[key] = Path(value).resolve()
        except (OSError, RuntimeError):
            return _failed_report(f"{key} path resolution failed")
    try:
        supplied["stage3_1_notebook"] = supplied["stage3_1_artifact"].with_name("verification.ipynb").resolve()
    except (OSError, RuntimeError):
        return _failed_report("stage3_1_notebook path resolution failed")
    paths = {**expected_paths, **supplied}
    errors: list[str] = []
    current: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}

    for key, relative, expected_hash in FROZEN_RECEIPT:
        path = paths[key]
        if path != expected_paths[key]:
            errors.append(f"{key} path mismatch: expected {relative}")
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"{key} path escapes repository root")
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        except (OSError, ValueError):
            digest = ""
        current[key] = digest
        if digest != expected_hash:
            errors.append(f"{key} raw SHA-256 mismatch")
        if relative.endswith(".json"):
            try:
                payloads[key] = _load_strict_json(path, canonical=key not in _LEGACY_NONCANONICAL)
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                errors.append(f"{key} JSON validation failed: {exc}")

    try:
        _validate_stage2(root, paths, current, payloads)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"Stage 2.1 receipt invalid: {exc}")
    try:
        _validate_stage3(root, paths, current, payloads)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"Stage 3.1 receipt invalid: {exc}")

    ok = not errors
    return FrozenUpstreamReceiptReport(
        ok=ok,
        stage2_1_ready=ok,
        stage3_1_ready=ok,
        current_hashes=MappingProxyType(current),
        errors=tuple(errors),
    )


def _validate_stage2(root: Path, paths: Mapping[str, Path], hashes: Mapping[str, str], payloads: Mapping[str, dict[str, Any]]) -> None:
    device = _payload(payloads, "device_artifact")
    _keys(device, {
        "schema_version", "artifact_type", "artifact_version", "source_config", "device_summary",
        "components", "channels", "priors", "capacitance_matrix", "junction_parameters",
        "validation", "checks",
    }, "Stage 1 device artifact")
    _identity(device, "0.1", "stage_01_device_model", "0.1")
    if not isinstance(device.get("channels"), Mapping) or set(device["channels"]) != {"q1_xy", "q2_xy", "c_z", "r1_ro", "r2_ro"}:
        raise ValueError("Stage 1 device artifact channel structure mismatch")
    if not isinstance(device.get("validation"), Mapping) or device["validation"].get("ok") is not True:
        raise ValueError("Stage 1 device artifact validation is not ready")

    artifact = _payload(payloads, "stage2_artifact")
    _identity(artifact, "0.2", "stage_02_hamiltonian", "0.2")
    expected_provenance = {
        "device_artifacts_sha256": hashes["device_artifact"],
        "hamiltonian_config_sha256": hashes["hamiltonian_config"],
        "stage2_model_source_tree_sha256": _STAGE2_SOURCE_SHA256,
    }
    _equal(artifact.get("provenance"), expected_provenance, "Stage 2 artifact provenance")
    if stage2_model_source_tree_sha256(root) != _STAGE2_SOURCE_SHA256:
        raise ValueError("Stage 2 model source-tree digest mismatch")

    manifest = _payload(payloads, "stage2_1_manifest")
    expected_manifest_keys = {
        "artifact_type", "artifact_version", "candidate_stage2_artifact_version",
        "created_from_stage2_artifact_version", "determinism_checks", "git_commit", "git_dirty",
        "legacy_baseline_anchor_path", "legacy_baseline_anchor_sha256", "old_to_new_numeric_deltas",
        "paths", "previous_stage2_artifacts_sha256", "schema_version", "sha256", "test_summary",
        "verify_device_summary", "verify_hamiltonian_summary",
    }
    _keys(manifest, expected_manifest_keys, "Stage 2.1 manifest")
    _identity(manifest, "0.1", "stage_02_1_hamiltonian_rebaseline", "0.1")
    if manifest.get("candidate_stage2_artifact_version") != "0.2" or manifest.get("created_from_stage2_artifact_version") != "0.1":
        raise ValueError("Stage 2.1 manifest artifact versions mismatch")
    expected_paths = {
        "candidate_stage2_artifact": _relative(paths["stage2_artifact"], root),
        "device_artifacts": _relative(paths["device_artifact"], root),
        "device_config": _relative(paths["device_config"], root),
        "hamiltonian_config": _relative(paths["hamiltonian_config"], root),
        "legacy_baseline_anchor": _relative(paths["legacy_anchor"], root),
        "previous_stage2_artifact": _relative(paths["previous_stage2_artifact"], root),
        "stage2_cli": "src/sqvm/__main__.py",
    }
    _equal(manifest.get("paths"), expected_paths, "Stage 2.1 manifest paths")
    expected_sha = {
        "device_artifacts_sha256": hashes["device_artifact"],
        "device_config_sha256": hashes["device_config"],
        "hamiltonian_config_sha256": hashes["hamiltonian_config"],
        "legacy_baseline_anchor_sha256": hashes["legacy_anchor"],
        "stage2_artifacts_sha256": hashes["stage2_artifact"],
        "stage2_cli_sha256_at_rebaseline": _STAGE2_CLI_SHA256,
        "stage2_model_source_tree_sha256": _STAGE2_SOURCE_SHA256,
    }
    _equal(manifest.get("sha256"), expected_sha, "Stage 2.1 manifest SHA-256 map")
    if manifest.get("legacy_baseline_anchor_sha256") != hashes["legacy_anchor"] or manifest.get("previous_stage2_artifacts_sha256") != hashes["previous_stage2_artifact"]:
        raise ValueError("Stage 2.1 manifest legacy bindings mismatch")
    for key in ("determinism_checks", "old_to_new_numeric_deltas", "test_summary", "verify_device_summary", "verify_hamiltonian_summary"):
        if not isinstance(manifest.get(key), Mapping):
            raise ValueError(f"Stage 2.1 manifest {key} must be a mapping")
    if not isinstance(manifest.get("git_commit"), str) or not manifest["git_commit"] or not isinstance(manifest.get("git_dirty"), bool):
        raise ValueError("Stage 2.1 manifest git fields are invalid")

    approval = _payload(payloads, "stage2_1_approval")
    approval_keys = {
        "artifact_type", "artifact_version", "blocking_findings", "decision",
        "legacy_baseline_anchor_sha256", "manifest_sha256", "previous_stage2_artifacts_sha256",
        "review_record_path", "reviewer_role", "schema_version", "stage2_artifacts_sha256",
    }
    _keys(approval, approval_keys, "Stage 2.1 approval")
    _identity(approval, "0.1", "stage_02_1_hamiltonian_rebaseline_approval", "0.1")
    _approved(approval, "Stage 2.1 approval")
    _equal(
        {key: approval.get(key) for key in ("manifest_sha256", "stage2_artifacts_sha256", "legacy_baseline_anchor_sha256", "previous_stage2_artifacts_sha256")},
        {
            "manifest_sha256": hashes["stage2_1_manifest"],
            "stage2_artifacts_sha256": hashes["stage2_artifact"],
            "legacy_baseline_anchor_sha256": hashes["legacy_anchor"],
            "previous_stage2_artifacts_sha256": hashes["previous_stage2_artifact"],
        },
        "Stage 2.1 approval bindings",
    )
    if not isinstance(approval.get("review_record_path"), str) or not approval["review_record_path"]:
        raise ValueError("Stage 2.1 approval review record is invalid")

    anchor = _payload(payloads, "legacy_anchor")
    _keys(anchor, {
        "acceptance_record_path", "acceptance_record_sha256", "approved_by", "artifact_type",
        "artifact_version", "decision", "expected_sha256", "historical_review_path",
        "previous_stage2_artifacts_sha256", "schema_version",
    }, "legacy anchor")
    _identity(anchor, "0.1", "stage_02_legacy_baseline_anchor", "0.1")
    expected_legacy = hashes["previous_stage2_artifact"]
    if anchor.get("decision") != "accepted" or anchor.get("approved_by") != "user":
        raise ValueError("legacy anchor is not accepted by user")
    if anchor.get("expected_sha256") != expected_legacy or anchor.get("previous_stage2_artifacts_sha256") != expected_legacy:
        raise ValueError("legacy anchor previous artifact bindings mismatch")
    if anchor.get("acceptance_record_path") != _RECEIPT_BY_KEY["legacy_anchor_acceptance_record"][0] or anchor.get("acceptance_record_sha256") != hashes["legacy_anchor_acceptance_record"]:
        raise ValueError("legacy anchor acceptance record binding mismatch")
    previous = _payload(payloads, "previous_stage2_artifact")
    _keys(previous, {
        "schema_version", "artifact_type", "artifact_version", "source_device_artifacts",
        "hamiltonian_config", "node_order", "mode_order", "coordinate_transform",
        "node_capacitance_matrix_fF", "mode_capacitance_matrix_fF", "ec_matrix_GHz",
        "effective_junctions", "basis", "offset_charge_ng", "hilbert_dimension",
        "hamiltonian_summary", "solver", "lowest_eigenvalues_GHz", "eigenvalue_gaps_GHz",
        "reference_priors", "mode_coupling_fF", "single_transmon_analytic_limit",
        "charge_basis_convergence", "checks",
    }, "previous Stage 2 artifact")
    _identity(previous, "0.1", "stage_02_hamiltonian", "0.1")


def _validate_stage3(root: Path, paths: Mapping[str, Path], hashes: Mapping[str, str], payloads: Mapping[str, dict[str, Any]]) -> None:
    artifact = _payload(payloads, "stage3_1_artifact")
    _keys(artifact, {
        "acceptance_eligible", "artifact_type", "artifact_version", "checks", "computational_gate",
        "coupler_points", "coupling_modulation", "idle_convergence", "idle_metrics", "provenance",
        "q1_q2_crossings", "runtime", "scan_definition", "schema_version", "solver_backend",
    }, "Stage 3.1 artifact")
    _identity(artifact, "0.1", "stage_03_1_q1_q2_coupling", "0.1")
    if artifact.get("acceptance_eligible") is not True:
        raise ValueError("Stage 3.1 artifact is not acceptance eligible")
    gate = artifact.get("computational_gate")
    _keys(gate, {"blocking_reasons", "checks", "computational_ready", "status"}, "Stage 3.1 computational gate")
    if not isinstance(gate, Mapping) or gate.get("computational_ready") is not True or gate.get("status") != "ready_for_stage4" or gate.get("blocking_reasons") != []:
        raise ValueError("Stage 3.1 computational gate is not ready")
    provenance = artifact.get("provenance")
    _keys(provenance, {
        "all_matches", "approval_decision", "device_artifacts_sha256", "errors",
        "hamiltonian_config_sha256", "ok", "rebaseline_approval_path",
        "rebaseline_approval_sha256", "rebaseline_manifest_path", "rebaseline_manifest_sha256",
        "stage2_artifact_version", "stage2_artifacts_sha256", "stage2_dense_gap_consistency",
        "stage2_model_source_tree_sha256",
    }, "Stage 3.1 provenance")
    expected_provenance = {
        "ok": True,
        "all_matches": True,
        "errors": [],
        "approval_decision": "approved",
        "rebaseline_manifest_sha256": hashes["stage2_1_manifest"],
        "rebaseline_approval_sha256": hashes["stage2_1_approval"],
        "stage2_artifacts_sha256": hashes["stage2_artifact"],
        "device_artifacts_sha256": hashes["device_artifact"],
        "hamiltonian_config_sha256": hashes["hamiltonian_config"],
        "stage2_model_source_tree_sha256": _STAGE2_SOURCE_SHA256,
        "stage2_artifact_version": "0.2",
        "rebaseline_manifest_path": _relative(paths["stage2_1_manifest"], root),
        "rebaseline_approval_path": _relative(paths["stage2_1_approval"], root),
    }
    for key, expected in expected_provenance.items():
        if provenance.get(key) != expected:
            raise ValueError(f"Stage 3.1 provenance {key} mismatch")
    dense = provenance.get("stage2_dense_gap_consistency")
    _keys(dense, {"compared_gap_count", "errors", "max_abs_difference_GHz", "ok"}, "Stage 3.1 dense Stage 2 consistency")
    if not isinstance(dense, Mapping) or dense.get("ok") is not True or dense.get("compared_gap_count") != 12 or dense.get("max_abs_difference_GHz") != 0.0 or dense.get("errors") != []:
        raise ValueError("Stage 3.1 dense Stage 2 consistency is invalid")

    report = _payload(payloads, "stage3_1_report")
    _keys(report, {
        "acceptance_candidate_ready", "approval_status", "artifact_sha256", "artifact_type",
        "artifact_version", "artifact_write", "blocking_reasons", "checks", "computational_gate",
        "execution_succeeded", "notebook_sha256", "notebook_write", "ok", "post_write_checks",
        "schema_version", "stage4_ready",
    }, "Stage 3.1 report")
    _identity(report, "0.1", "stage_03_1_q1_q2_coupling_verification_report", "0.1")
    if report.get("execution_succeeded") is not True or report.get("ok") is not True or report.get("acceptance_candidate_ready") is not True:
        raise ValueError("Stage 3.1 report is not ready")
    if report.get("computational_gate") != gate or report.get("checks") != artifact.get("checks"):
        raise ValueError("Stage 3.1 report computational evidence mismatch")
    post = report.get("post_write_checks")
    if not isinstance(post, list) or not post or any(not isinstance(row, Mapping) or row.get("passed") is not True for row in post):
        raise ValueError("Stage 3.1 post-write checks are invalid")
    if report.get("artifact_sha256") != hashes["stage3_1_artifact"] or report.get("notebook_sha256") != hashes["stage3_1_notebook"]:
        raise ValueError("Stage 3.1 report hash bindings mismatch")
    _write_binding(report.get("artifact_write"), paths["stage3_1_artifact"], hashes["stage3_1_artifact"], root, "artifact")
    _write_binding(report.get("notebook_write"), paths["stage3_1_notebook"], hashes["stage3_1_notebook"], root, "notebook")

    if _stage3_source_tree_sha256(root) != _STAGE3_SOURCE_SHA256:
        raise ValueError("Stage 3.1 source-tree digest mismatch")
    _validate_freeze(payloads, hashes)
    _validate_solver(payloads, hashes)
    _validate_stage3_notebook(paths["stage3_1_notebook"])

    approval = _payload(payloads, "stage3_1_approval")
    _keys(approval, {
        "artifact_type", "artifact_version", "blocking_findings", "config_sha256", "decision",
        "design_freeze_manifest_sha256", "q1_q2_coupling_artifact_sha256", "review_record_path",
        "review_record_sha256", "reviewer_role", "schema_version", "solver_validation_approval_sha256",
        "solver_validation_sha256", "stage3_1_source_tree_sha256", "verification_notebook_sha256",
        "verification_report_sha256",
    }, "Stage 3.1 final approval")
    _identity(approval, "0.1", "stage_03_1_q1_q2_coupling_acceptance_approval", "0.1")
    _approved(approval, "Stage 3.1 final approval")
    expected = {
        "config_sha256": hashes["stage3_1_config"],
        "design_freeze_manifest_sha256": hashes["stage3_1_design_freeze_manifest"],
        "q1_q2_coupling_artifact_sha256": hashes["stage3_1_artifact"],
        "review_record_path": _RECEIPT_BY_KEY["stage3_1_final_review"][0],
        "review_record_sha256": hashes["stage3_1_final_review"],
        "solver_validation_approval_sha256": hashes["stage3_1_solver_validation_approval"],
        "solver_validation_sha256": hashes["stage3_1_solver_validation"],
        "stage3_1_source_tree_sha256": _STAGE3_SOURCE_SHA256,
        "verification_notebook_sha256": hashes["stage3_1_notebook"],
        "verification_report_sha256": hashes["stage3_1_report"],
    }
    for key, value in expected.items():
        if approval.get(key) != value:
            raise ValueError(f"Stage 3.1 final approval {key} mismatch")


def _validate_freeze(payloads: Mapping[str, dict[str, Any]], hashes: Mapping[str, str]) -> None:
    freeze = _payload(payloads, "stage3_1_design_freeze_manifest")
    _keys(freeze, {
        "artifact_type", "artifact_version", "blocking_findings", "decision", "document_sha256",
        "review_record_path", "review_record_sha256", "reviewer_role", "schema_version",
    }, "Stage 3.1 freeze manifest")
    _identity(freeze, "0.1", "stage_03_1_design_freeze", "0.1")
    if freeze.get("decision") != "approved" or freeze.get("reviewer_role") != "independent_design_review_ai" or freeze.get("blocking_findings") != []:
        raise ValueError("Stage 3.1 freeze manifest is not approved")
    expected_docs = {
        _RECEIPT_BY_KEY["stage3_1_objective_decision"][0]: hashes["stage3_1_objective_decision"],
        _RECEIPT_BY_KEY["stage3_1_detailed_design"][0]: hashes["stage3_1_detailed_design"],
        _RECEIPT_BY_KEY["stage3_1_plan"][0]: hashes["stage3_1_plan"],
    }
    if freeze.get("document_sha256") != expected_docs:
        raise ValueError("Stage 3.1 freeze document bindings mismatch")
    if freeze.get("review_record_path") != _RECEIPT_BY_KEY["stage3_1_design_freeze_review"][0] or freeze.get("review_record_sha256") != hashes["stage3_1_design_freeze_review"]:
        raise ValueError("Stage 3.1 freeze review binding mismatch")


def _validate_solver(payloads: Mapping[str, dict[str, Any]], hashes: Mapping[str, str]) -> None:
    candidate = _payload(payloads, "stage3_1_solver_validation")
    _keys(candidate, {
        "schema_version", "artifact_type", "artifact_version", "profile", "acceptance_eligible",
        "validation_passed", "coverage_complete", "failed_cases", "bindings", "eigsh_spec", "dense_spec",
        "thresholds", "normative_vector", "cutoff_signatures", "flux_vectors", "validation_cases",
        "aggregates", "p50_seconds_by_dimension", "p95_seconds_by_dimension",
    }, "Stage 3.1 solver candidate")
    _identity(candidate, "0.1", "stage_03_1_solver_backend_validation", "0.1")
    expected_flags = {"profile": "solver_validation", "acceptance_eligible": False, "validation_passed": True, "coverage_complete": True, "failed_cases": []}
    for key, value in expected_flags.items():
        if candidate.get(key) != value:
            raise ValueError(f"Stage 3.1 solver candidate {key} mismatch")
    expected_bindings = {
        "config_sha256": hashes["stage3_1_config"],
        "dense_pilot_sha256": hashes["stage3_1_dense_pilot"],
        "design_freeze_manifest_sha256": hashes["stage3_1_design_freeze_manifest"],
        "environment_fingerprint_sha256": _ENVIRONMENT_SHA256,
        "hamiltonian_config_sha256": hashes["hamiltonian_config"],
        "stage2_artifacts_sha256": hashes["stage2_artifact"],
        "stage2_model_source_tree_sha256": _STAGE2_SOURCE_SHA256,
        "stage2_rebaseline_approval_sha256": hashes["stage2_1_approval"],
        "stage2_rebaseline_manifest_sha256": hashes["stage2_1_manifest"],
        "stage3_1_c2_2_fixed_refinement_remediation_sha256": hashes["stage3_1_c2_2_remediation"],
        "stage3_1_c2_remediation_sha256": hashes["stage3_1_c2_remediation"],
        "stage3_1_source_tree_sha256": _STAGE3_SOURCE_SHA256,
    }
    _equal(candidate.get("bindings"), expected_bindings, "Stage 3.1 solver bindings")
    _equal(candidate.get("normative_vector"), {
        "block0_sha256": "44C000CA15E6923769C51CCC19623DAB311213490C1E50225D7E0AC79A33E322",
        "encoding_version": "sha256_counter_v2", "passed": True, "seed_length": 224,
        "seed_sha256": "48245D6E13E15E65DA68E7AD4E957E07F97F0C1DF6E182E1806D6B1F88027CA0",
    }, "Stage 3.1 solver normative vector")
    signatures = [
        {"cutoffs": [7, 7, 7], "dimension": 3375, "id": "baseline"},
        {"cutoffs": [9, 7, 7], "dimension": 4275, "id": "refined_q1"},
        {"cutoffs": [7, 9, 7], "dimension": 4275, "id": "refined_c"},
        {"cutoffs": [7, 7, 9], "dimension": 4275, "id": "refined_q2"},
    ]
    _equal(candidate.get("cutoff_signatures"), signatures, "Stage 3.1 solver cutoff signatures")
    vectors = _solver_flux_vectors()
    _equal(candidate.get("flux_vectors"), vectors, "Stage 3.1 solver flux vectors")
    cases = candidate.get("validation_cases")
    if not isinstance(cases, list) or len(cases) != 44:
        raise ValueError("Stage 3.1 solver validation cases must contain exactly 44 rows")
    expected_ids = [f"{signature['id']}__{vector['id']}" for signature in signatures for vector in vectors]
    actual_ids: list[str] = []
    for index, (row, case_id) in enumerate(zip(cases, expected_ids, strict=True)):
        if not isinstance(row, Mapping) or row.get("case_id") != case_id or row.get("passed") is not True:
            raise ValueError("Stage 3.1 solver validation case identity or status mismatch")
        signature = signatures[index // len(vectors)]
        vector = vectors[index % len(vectors)]
        if row.get("cutoff_signature") != signature["id"] or row.get("cutoffs") != signature["cutoffs"] or row.get("dimension") != signature["dimension"] or row.get("flux_keys") != vector["flux_keys"]:
            raise ValueError("Stage 3.1 solver validation case binding mismatch")
        physics = row.get("physics_checks")
        if not isinstance(physics, Mapping) or physics.get("passed") is not True:
            raise ValueError("Stage 3.1 solver validation physics check failed")
        actual_ids.append(row["case_id"])
    if len(set(actual_ids)) != 44:
        raise ValueError("Stage 3.1 solver validation case IDs are not unique")
    aggregate_keys = {
        "max_gap_error_GHz", "max_repeat_gap_error_GHz", "max_projector_dense_error",
        "max_projector_repeat_error", "max_participation_dense_error", "max_participation_repeat_error",
        "max_metric_dense_error_GHz", "max_q1_q2_splitting_error_GHz",
    }
    aggregates = candidate.get("aggregates")
    _keys(aggregates, aggregate_keys, "Stage 3.1 solver aggregates")
    for key, value in aggregates.items():
        if not _finite_number(value) or value < 0:
            raise ValueError(f"Stage 3.1 solver aggregate {key} is invalid")
    p50 = candidate.get("p50_seconds_by_dimension")
    p95 = candidate.get("p95_seconds_by_dimension")
    _keys(p50, {"3375", "4275"}, "Stage 3.1 solver p50")
    _keys(p95, {"3375", "4275"}, "Stage 3.1 solver p95")
    for dimension in ("3375", "4275"):
        if not _finite_number(p50[dimension]) or not _finite_number(p95[dimension]) or p50[dimension] <= 0 or p95[dimension] < p50[dimension]:
            raise ValueError(f"Stage 3.1 solver timing {dimension} is invalid")

    approval = _payload(payloads, "stage3_1_solver_validation_approval")
    _keys(approval, {
        "schema_version", "artifact_type", "artifact_version", "decision", "reviewer_role",
        "blocking_findings", "validation_artifact_sha256", "review_record_path", "review_record_sha256",
    }, "Stage 3.1 solver approval")
    _identity(approval, "0.1", "stage_03_1_solver_backend_validation_approval", "0.1")
    _approved(approval, "Stage 3.1 solver approval")
    if approval.get("validation_artifact_sha256") != hashes["stage3_1_solver_validation"] or approval.get("review_record_path") != _RECEIPT_BY_KEY["stage3_1_solver_validation_review"][0] or approval.get("review_record_sha256") != hashes["stage3_1_solver_validation_review"]:
        raise ValueError("Stage 3.1 solver approval bindings mismatch")


def _validate_stage3_notebook(path: Path) -> None:
    notebook = _load_strict_json(path, canonical=False)
    _keys(notebook, {"cells", "metadata", "nbformat", "nbformat_minor"}, "Stage 3.1 notebook")
    if notebook.get("nbformat") != 4 or notebook.get("nbformat_minor") != 5:
        raise ValueError("Stage 3.1 notebook format must be 4.5")
    metadata = notebook.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("Stage 3.1 notebook metadata must be a mapping")
    _keys(metadata, {"language_info", "stage3_1_read_only"}, "Stage 3.1 notebook metadata")
    if not isinstance(metadata.get("language_info"), Mapping):
        raise ValueError("Stage 3.1 notebook language_info must be a mapping")
    if metadata.get("stage3_1_read_only") is not True:
        raise ValueError("Stage 3.1 notebook stage3_1_read_only must be true")
    cells = notebook.get("cells")
    if not isinstance(cells, list) or len(cells) != 18:
        raise ValueError("Stage 3.1 notebook must contain exactly 18 cells")
    if any(not isinstance(cell, Mapping) for cell in cells):
        raise ValueError("Stage 3.1 notebook cells must be mappings")
    code = [cell for cell in cells if cell.get("cell_type") == "code"]
    if len(code) != 9 or [cell.get("execution_count") for cell in code] != list(range(1, 10)):
        raise ValueError("Stage 3.1 notebook execution counts are invalid")
    for cell in code:
        outputs = cell.get("outputs")
        if not isinstance(outputs, list) or any(not isinstance(output, Mapping) for output in outputs):
            raise ValueError("Stage 3.1 notebook code outputs must be mappings")
        if any(output.get("output_type") == "error" for output in outputs):
            raise ValueError("Stage 3.1 notebook contains an error output")


def _solver_flux_vectors() -> list[dict[str, Any]]:
    rows = (
        ("idle", "0.270000000000", "0.000000000000"),
        ("c020_left", "0.200000000000", "0.080000000000"),
        ("c020_mid", "0.200000000000", "0.100000000000"),
        ("c020_right", "0.200000000000", "0.120000000000"),
        ("c027_left", "0.270000000000", "0.080000000000"),
        ("c027_mid", "0.270000000000", "0.100000000000"),
        ("c027_right", "0.270000000000", "0.120000000000"),
        ("c0385_left", "0.385000000000", "0.080000000000"),
        ("c0385_mid", "0.385000000000", "0.100000000000"),
        ("c0385_right", "0.385000000000", "0.120000000000"),
        ("c0396_mid", "0.396000000000", "0.100000000000"),
    )
    return [{"flux_keys": {"c": c, "q1": "0.100000000000", "q2": q2}, "id": name} for name, c, q2 in rows]


def _load_strict_json(path: Path, *, canonical: bool) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        payload = json.loads(text, object_pairs_hook=pairs, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be a mapping")
    try:
        _assert_finite(payload)
    except RecursionError as exc:
        raise ValueError("JSON nesting is too deep") from exc
    if canonical:
        try:
            expected_raw = canonical_json_bytes(payload)
        except RecursionError as exc:
            raise ValueError("JSON nesting is too deep") from exc
        if raw != expected_raw:
            raise ValueError("JSON bytes are not canonical")
    return payload


def _stage3_source_tree_sha256(root: Path) -> str:
    files = sorted(
        (path.relative_to(root).as_posix(), path)
        for path in (root / "src" / "sqvm" / "spectrum").rglob("*.py")
        if path.is_file()
    )
    digest = hashlib.sha256()
    for relative, path in sorted(files, key=lambda item: item[0].encode("utf-8")):
        raw = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(raw)).encode("ascii"))
        digest.update(b"\0")
        digest.update(raw)
    return digest.hexdigest().upper()


def _write_binding(value: Any, path: Path, digest: str, root: Path, label: str) -> None:
    expected_keys = {"canonical_and_finite", "completed", "path", "sha256"} if label == "artifact" else {
        "code_cell_count", "completed", "error_output_count", "executed_code_cell_count", "path", "sha256",
    }
    _keys(value, expected_keys, f"Stage 3.1 {label} write")
    if value.get("completed") is not True or value.get("path") != _relative(path, root) or value.get("sha256") != digest:
        raise ValueError(f"Stage 3.1 {label} write binding mismatch")
    if label == "artifact" and value.get("canonical_and_finite") is not True:
        raise ValueError("Stage 3.1 artifact write is not canonical and finite")
    if label == "notebook" and (value.get("code_cell_count") != 9 or value.get("executed_code_cell_count") != 9 or value.get("error_output_count") != 0):
        raise ValueError("Stage 3.1 notebook write execution summary mismatch")


def _identity(payload: Mapping[str, Any], schema: str, artifact_type: str, version: str) -> None:
    if payload.get("schema_version") != schema or payload.get("artifact_type") != artifact_type or payload.get("artifact_version") != version:
        raise ValueError(f"{artifact_type} identity mismatch")


def _approved(payload: Mapping[str, Any], label: str) -> None:
    if payload.get("decision") != "approved" or payload.get("reviewer_role") != "independent_test_review_ai" or payload.get("blocking_findings") != []:
        raise ValueError(f"{label} is not independently approved")


def _payload(payloads: Mapping[str, dict[str, Any]], key: str) -> dict[str, Any]:
    try:
        return payloads[key]
    except KeyError as exc:
        raise ValueError(f"{key} payload is unavailable") from exc


def _failed_report(error: str) -> FrozenUpstreamReceiptReport:
    return FrozenUpstreamReceiptReport(
        ok=False,
        stage2_1_ready=False,
        stage3_1_ready=False,
        current_hashes=MappingProxyType({key: "" for key, _, _ in FROZEN_RECEIPT}),
        errors=(error,),
    )


def _keys(value: Any, expected: set[str], label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError(f"{label} fields are not exact")


def _equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise ValueError(f"{label} mismatch")


def _relative(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _assert_finite(value: Any, path: str = "$") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite JSON value at {path}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _assert_finite(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{path}[{index}]")
        return
    raise ValueError(f"unsupported JSON value at {path}")
