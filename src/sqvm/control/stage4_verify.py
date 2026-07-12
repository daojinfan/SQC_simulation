"""Stage 4 control-signal verification lifecycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time
import uuid

from sqvm.control.compatibility import _validate_freeze, validate_control_channel_approval
from sqvm.control.registry import ADDED_CHANNEL_ORDER, load_control_channel_registry
from sqvm.control.stage4_artifacts import PUBLICATION_CHECKS, RECEIPT_NAME, validate_control_development_set, write_control_signal_artifacts
from sqvm.control.stage4_compile import compile_control_schedule
from sqvm.control.stage4_config import load_control_chain_config, load_logical_schedule, validate_logical_schedule
from sqvm.control.stage4_models import ControlBuildContext, ControlRunReceipt
from sqvm.control.stage4_provenance import stage4_environment_fingerprint, stage4_source_tree_sha256
from sqvm.control.upstream import validate_frozen_stage4_upstream_receipt
from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root, raw_file_sha256


def verify_control_signal(config_path: str | Path, schedule_path: str | Path, output_dir: str | Path) -> ControlRunReceipt:
    total_started = time.perf_counter()
    config_source = Path(config_path).resolve()
    schedule_source = Path(schedule_path).resolve()
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"control signal output target already exists: {target}")
    root = find_repository_root(config_source)
    config = load_control_chain_config(config_source)
    schedule = load_logical_schedule(schedule_source, dt_ns=config.dt_ns)
    resolved = {key: (root / value).resolve() for key, value in config.inputs.items()}
    _validate_freeze(resolved["stage4_design_freeze_manifest"], root)
    channel_ready = validate_control_channel_approval(resolved["channel_registry_approval"], repository_root=root)
    if not channel_ready.ok or not channel_ready.control_channel_ready or not channel_ready.approval_valid:
        raise ValueError("Stage 4.0 control-channel approval is not ready")
    upstream = validate_frozen_stage4_upstream_receipt(
        repository_root=root,
        stage2_artifact_path=root / "output/stage_02_hamiltonian/hamiltonian_artifacts.json",
        stage2_1_manifest_path=root / "output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json",
        stage2_1_approval_path=root / "output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json",
        stage3_1_artifact_path=resolved["stage3_1_artifact"],
        stage3_1_report_path=resolved["stage3_1_artifact"].with_name("verification_report.json"),
        stage3_1_approval_path=resolved["stage3_1_approval"],
    )
    if not upstream.ok or not upstream.stage3_1_ready:
        raise ValueError("Stage 3.1 frozen readiness receipt is not valid")
    analysis_started = time.perf_counter()
    registry = load_control_channel_registry(resolved["channel_registry"])
    schedule_report = validate_logical_schedule(schedule, registry, config)
    if not schedule_report.ok:
        raise ValueError("; ".join(schedule_report.errors))
    stage3 = _load_canonical(resolved["stage3_1_artifact"], "Stage 3.1 artifact")
    source_sha = stage4_source_tree_sha256(root)
    environment, environment_sha = stage4_environment_fingerprint()
    paths = {
        "stage4_design_freeze_manifest": _relative(resolved["stage4_design_freeze_manifest"], root),
        "channel_registry": _relative(resolved["channel_registry"], root),
        "channel_registry_approval": _relative(resolved["channel_registry_approval"], root),
        "control_config": _relative(config_source, root), "logical_schedule": _relative(schedule_source, root),
        "stage3_1_artifact": _relative(resolved["stage3_1_artifact"], root),
        "stage3_1_approval": _relative(resolved["stage3_1_approval"], root),
    }
    sha = {f"{key}_sha256": raw_file_sha256(root / value) for key, value in paths.items()}
    sha.update({"stage4_source_tree_sha256": source_sha, "environment_fingerprint_sha256": environment_sha})
    provenance = {"paths": paths, "sha256": sha}
    registry_payload = {
        "channel_order": [row.name for row in registry.channels],
        "channels": {row.name: {**row.to_dict(), "origin": "stage4_extension" if row.name in ADDED_CHANNEL_ORDER else "stage1_base"} for row in registry.channels},
    }
    context = ControlBuildContext(
        root,
        {
            "stage3_1_readiness_valid": True, "stage4_0_channel_registry_ready": True,
            "design_and_config_provenance_valid": True, "artifact_provenance": provenance,
            "environment": environment,
        },
        registry_payload, stage3, analysis_started,
    )
    result = compile_control_schedule(schedule, config, context)
    if not result.payload["computational_gate"]["computational_ready"]:
        raise ValueError("Stage 4 computational gate failed")
    artifact_set = write_control_signal_artifacts(result, target)
    try:
        exact_three = {row.name for row in target.iterdir() if row.is_file()} == {"control_signal_artifacts.json", "verification.ipynb", "verification_report.json"} and all(row.is_file() for row in target.iterdir())
        publication = [
            exact_three,
            raw_file_sha256(artifact_set.artifact_path) == artifact_set.artifact_sha256,
            raw_file_sha256(artifact_set.notebook_path) == artifact_set.notebook_sha256,
            raw_file_sha256(artifact_set.report_path) == artifact_set.report_sha256,
            not list(target.parent.glob(f".{target.name}.staging.*")),
        ]
        checks = [{"name": name, "passed": passed, "message": "passed" if passed else f"{name} failed"} for name, passed in zip(PUBLICATION_CHECKS, publication, strict=True)]
        total = time.perf_counter() - total_started
        total_ok = total <= config.acceptance["total_runtime_budget_seconds"]
        candidate = config.profile == "formal" and result.acceptance_eligible and all(publication) and result.payload["runtime"]["analysis_runtime_within_budget"] and total_ok
        blockers = [row["message"] for row in checks if not row["passed"]]
        if not total_ok: blockers.append("total runtime budget exceeded")
        receipt = {
            "schema_version": "0.1", "artifact_type": "stage_04_control_signal_run_receipt", "artifact_version": "0.1",
            "execution_succeeded": True, "profile": config.profile, "acceptance_eligible": result.acceptance_eligible,
            "acceptance_candidate_ready": candidate,
            "paths": {"artifact": _relative(artifact_set.artifact_path, root), "notebook": _relative(artifact_set.notebook_path, root), "verification_report": _relative(artifact_set.report_path, root)},
            "sha256": {"artifact_sha256": artifact_set.artifact_sha256, "notebook_sha256": artifact_set.notebook_sha256, "verification_report_sha256": artifact_set.report_sha256},
            "analysis_elapsed_seconds": result.payload["runtime"]["analysis_elapsed_seconds"],
            "total_elapsed_seconds_to_receipt_assembly": total,
            "total_runtime_budget_seconds": config.acceptance["total_runtime_budget_seconds"],
            "total_runtime_within_budget": total_ok, "publication_checks": checks, "blocking_reasons": blockers,
        }
        if not all(publication) or not total_ok:
            raise ValueError("Stage 4 publication or total runtime gate failed")
        _atomic_file_write(target / RECEIPT_NAME, canonical_json_bytes(receipt))
        validate_control_development_set(target, repository_root=root)
        return ControlRunReceipt(receipt)
    except Exception:
        if target.exists(): shutil.rmtree(target)
        raise


def _atomic_file_write(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{uuid.uuid4().hex}")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def _load_canonical(path: Path, label: str):
    try: payload = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise ValueError(f"cannot load {label}: {exc}") from exc
    if not isinstance(payload, dict) or path.read_bytes() != canonical_json_bytes(payload): raise ValueError(f"{label} is not canonical")
    return payload


def _relative(path, root): return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
