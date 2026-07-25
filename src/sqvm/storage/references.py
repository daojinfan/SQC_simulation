"""Fail-closed resolver for the configuration-to-experiment reference graph.

This module deliberately reads the on-disk contracts written by
``sqvm.web.configuration``.  It is not a generic JSON scraper: an unreadable or
unrecognised authority source makes destructive operations unavailable.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid
from datetime import datetime
from typing import Any, Iterable, Mapping

from sqvm.calibration.spectroscopy_workflow import (
    verify_qubit_spectroscopy_calibration,
    verify_qubit_spectroscopy_calibration_decision,
)
from sqvm.calibration.spectroscopy_run import verify_qubit_spectroscopy_scan
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json
from sqvm.web.configuration_schema import validate_document, validate_editable
from sqvm.web.configuration_transactions import ConfigurationTransactionManager


REFERENCE_TYPES = frozenset({
    "current_configuration", "draft_configuration", "snapshot_configuration",
    "active_snapshot", "accepted_decision", "applied_audit",
    "derived_experiment", "manual_keep",
})
_SHA256 = __import__("re").compile(r"^[A-F0-9]{64}$")
_ACTOR = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,63}$")


class ReferenceScanError(ValueError):
    """A source cannot be trusted as a complete reference authority."""


@dataclass(frozen=True, slots=True)
class ReferenceEdge:
    run_id: str
    reference_type: str
    source_path: str
    source_sha256: str
    recommendation_id: str | None = None
    evidence_paths: tuple[str, ...] = ()
    evidence_sha256s: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceGraph:
    references: tuple[ReferenceEdge, ...]
    scan_incomplete: bool
    blockers: tuple[str, ...]

    def for_run(self, run_id: str) -> tuple[ReferenceEdge, ...]:
        return tuple(edge for edge in self.references if edge.run_id == run_id)

    def can_archive(self, run_id: str) -> bool:
        del run_id
        return True

    def can_trash(self, run_id: str) -> bool:
        return not self.scan_incomplete and not self.for_run(run_id)

    def can_purge(self, run_id: str) -> bool:
        return self.can_trash(run_id)

    def destructive_blocked(self, run_id: str) -> bool:
        return not self.can_trash(run_id)

    def permanently_blocked(self, run_id: str) -> bool:
        return any(edge.reference_type == "applied_audit" for edge in self.for_run(run_id))


def build_reference_graph(
    *,
    configuration_root: str | Path,
    experiment_output_root: str | Path,
    lifecycle_root: str | Path | None = None,
    pins_root: str | Path | None = None,
) -> ReferenceGraph:
    """Scan the authoritative roots without following links.

    ``lifecycle_root`` is currently checked as an authority boundary only; it is
    intentionally not interpreted as a reference source.  A future derivation
    event adapter belongs here once that event contract is published.
    """
    blockers: list[str] = []
    edges: list[ReferenceEdge] = []
    try:
        config = _authority_root(configuration_root, required=True, label="configuration")
        experiments = _authority_root(experiment_output_root, required=True, label="experiment output")
        lifecycle = _authority_root(lifecycle_root, required=False, label="lifecycle")
        pin_root = _authority_root(pins_root, required=False, label="pins")
        if lifecycle is not None:
            _check_tree(lifecycle, allow_directories=True)
        transaction_manager = ConfigurationTransactionManager(config)
        head_devices = set(transaction_manager.head_devices())
        projections = tuple(
            transaction_manager.committed_view(device_id).projection_root
            for device_id in sorted(head_devices)
        )
        snapshots: dict[str, tuple[dict[str, Any], Path]] = {}
        if projections:
            for projection in projections:
                for snapshot_id, value in _scan_configurations(
                    projection,
                    edges,
                    include_drafts=False,
                ).items():
                    if snapshot_id in snapshots:
                        raise ReferenceScanError(
                            f"duplicate snapshot identity across committed devices: {snapshot_id}"
                        )
                    snapshots[snapshot_id] = value
                _scan_snapshot_pins(projection / "pins")
            _reject_unmigrated_flat_devices(
                config,
                head_devices=head_devices,
                committed_snapshot_ids=set(snapshots),
            )
            _scan_drafts(config, edges)
        else:
            snapshots = _scan_configurations(config, edges)
            _scan_snapshot_pins(config / "pins")
        _scan_workflows_and_decisions(experiments, edges)
        if pin_root is not None:
            _scan_run_pins(pin_root, edges)
        _validate_duplicate_identities(edges)
        del snapshots
    except Exception as exc:
        blockers.append(str(exc))
    deduped = {(e.run_id, e.reference_type, e.source_path, e.source_sha256, e.recommendation_id, e.evidence_paths, e.evidence_sha256s): e for e in edges}
    return ReferenceGraph(
        tuple(sorted(deduped.values(), key=lambda e: (e.run_id, e.reference_type, e.source_path))),
        bool(blockers), tuple(blockers),
    )


def _reject_unmigrated_flat_devices(
    root: Path,
    *,
    head_devices: set[str],
    committed_snapshot_ids: set[str],
) -> None:
    """Do not miss legacy references while a configuration root is partly migrated."""

    flat_devices: set[str] = set()
    for directory_name in ("current", "active"):
        directory = root / directory_name
        if directory.exists():
            flat_devices.update(path.stem for path in _json_children(directory))
    snapshots = root / "snapshots"
    if snapshots.exists():
        for directory in _id_directories(snapshots):
            if directory.name in committed_snapshot_ids:
                continue
            path = directory / "snapshot.json"
            if not path.is_file():
                raise ReferenceScanError(f"snapshot missing: {path}")
            device_id = _json(path).get("device_id")
            if not isinstance(device_id, str) or not device_id:
                raise ReferenceScanError(f"snapshot device identity is invalid: {path}")
            flat_devices.add(device_id)
    missing = sorted(flat_devices - head_devices)
    if missing:
        raise ReferenceScanError(
            "mixed transaction and legacy configuration authority: "
            f"devices without transaction heads: {', '.join(missing)}"
        )


def _scan_configurations(
    root: Path,
    edges: list[ReferenceEdge],
    *,
    include_drafts: bool = True,
) -> dict[str, tuple[dict[str, Any], Path]]:
    allowed = {"current", "drafts", "snapshots", "active", "audit", "pins"}
    _check_children(root, allowed, directories=allowed)
    snapshots: dict[str, tuple[dict[str, Any], Path]] = {}
    for directory in ("current", "drafts", "snapshots", "active", "audit"):
        path = root / directory
        if path.exists():
            _check_path(path, root)
    current = root / "current"
    if current.exists():
        for path in _json_children(current):
            payload = _json(path)
            _configuration(payload, "platform_configuration_current", "0.3", path)
            _source_edge(payload.get("source_candidate"), "current_configuration", path, root, edges)
    snapshots_root = root / "snapshots"
    if snapshots_root.exists():
        for directory in _id_directories(snapshots_root):
            _check_children(directory, {"snapshot.json", "source_candidate.json"}, directories=set())
            path = directory / "snapshot.json"
            if not path.exists():
                raise ReferenceScanError(f"snapshot missing: {path}")
            payload = _json(path)
            _configuration(payload, "platform_configuration_snapshot", "0.2", path)
            snapshot_id = _uuid(payload.get("snapshot_id"), "snapshot_id")
            if directory.name != snapshot_id or snapshot_id in snapshots:
                raise ReferenceScanError(f"duplicate/incorrect snapshot identity: {path}")
            snapshots[snapshot_id] = (payload, path)
            candidate = directory / "source_candidate.json"
            if candidate.exists():
                _source_edge(_source_candidate(_json(candidate)), "snapshot_configuration", candidate, root, edges)
    if include_drafts:
        _scan_drafts(root, edges)
    active = root / "active"
    if active.exists():
        for path in _json_children(active):
            pointer = _json(path)
            _exact(pointer, {"schema_version", "artifact_type", "artifact_version", "device_id", "snapshot_id", "snapshot_content_sha256", "actor_id", "activated_utc"}, path)
            if pointer.get("schema_version") != "0.2" or pointer.get("artifact_type") != "platform_configuration_active_pointer" or pointer.get("artifact_version") != "0.2":
                raise ReferenceScanError(f"active pointer schema: {path}")
            snapshot_id = _uuid(pointer.get("snapshot_id"), "snapshot_id")
            snapshot = snapshots.get(snapshot_id)
            if snapshot is None or pointer.get("snapshot_content_sha256") != snapshot[0].get("content_sha256"):
                raise ReferenceScanError(f"active pointer hash binding: {path}")
            candidate = (snapshot[1].parent / "source_candidate.json")
            if candidate.exists():
                source = _source_candidate(_json(candidate))
                edges.append(_edge_with_evidence(
                    source["experiment_run_id"], "active_snapshot", candidate, root,
                    source["recommendation_id"], (path, candidate), root,
                ))
    audit = root / "audit"
    if audit.exists():
        for path in _json_children(audit):
            event = _json(path)
            _exact(event, {"schema_version", "event_id", "event", "actor_id", "created_utc", "details"}, path)
            if event.get("schema_version") != "0.1" or path.stem != _uuid(event.get("event_id"), "event_id") or not isinstance(event.get("details"), Mapping):
                raise ReferenceScanError(f"audit schema: {path}")
            if event["event"] == "experiment_candidates_applied_to_current":
                details = event["details"]
                _exact(details, {"device_id", "experiment_run_id", "recommendation_id", "candidate_ids", "targets", "content_sha256"}, path)
                run = _uuid(details.get("experiment_run_id"), "experiment_run_id")
                recommendation = _uuid(details.get("recommendation_id"), "recommendation_id")
                _hash(details.get("content_sha256"), "content_sha256")
                edges.append(_edge(run, "applied_audit", path, root, recommendation))
    return snapshots


def _scan_drafts(root: Path, edges: list[ReferenceEdge]) -> None:
    drafts_root = root / "drafts"
    if not drafts_root.exists():
        return
    _check_path(drafts_root, root)
    for directory in _id_directories(drafts_root):
        _check_children(
            directory,
            {"draft.json", "source_candidate.json", "checkpoints"},
            directories={"checkpoints"},
        )
        path = directory / "draft.json"
        if not path.exists():
            raise ReferenceScanError(f"draft missing: {path}")
        payload = _json(path)
        _configuration(payload, "platform_configuration_draft", "0.2", path)
        if directory.name != _uuid(payload.get("draft_id"), "draft_id"):
            raise ReferenceScanError(f"incorrect draft identity: {path}")
        checkpoints = directory / "checkpoints"
        if checkpoints.exists():
            for checkpoint in _json_children(checkpoints):
                _checkpoint(_json(checkpoint), payload, checkpoint)
        candidate = directory / "source_candidate.json"
        if candidate.exists():
            _source_edge(
                _source_candidate(_json(candidate)),
                "draft_configuration",
                candidate,
                root,
                edges,
            )


def _scan_workflows_and_decisions(root: Path, edges: list[ReferenceEdge]) -> None:
    workflows: dict[str, tuple[str, Path]] = {}
    workflow_runs: set[str] = set()
    decisions: list[Path] = []
    _check_tree(root, allow_directories=True)
    for directory, _dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if "workflow.json" in files:
            _check_path(base / "workflow.json", root)
            workflow = _json(base / "workflow.json")
            workflow_id = workflow.get("workflow_id")
            try:
                if workflow_id == "qubit_spectroscopy_scan_v1":
                    if workflow.get("artifact_type") != "qubit_spectroscopy_scan" or workflow.get("artifact_version") not in {"0.1", "0.2", "0.3"}:
                        raise ReferenceScanError(f"scan workflow schema: {base}")
                    verify_qubit_spectroscopy_scan(base)
                elif workflow_id == "qubit_rabi_x2p_amplitude_scan_v1":
                    if workflow.get("artifact_type") != "qubit_rabi_x2p_amplitude_scan" or workflow.get("artifact_version") != "0.1":
                        raise ReferenceScanError(f"Rabi workflow schema: {base}")
                    # Lazy import avoids making the reference graph depend on
                    # calibration package initialization order.
                    from sqvm.calibration.rabi import verify_rabi_evidence_tree
                    verify_rabi_evidence_tree(base)
                elif workflow_id == "qubit_spectroscopy_calibration_v1":
                    if workflow.get("artifact_type") != "stage_07_qubit_spectroscopy_calibration" or workflow.get("artifact_version") != "0.1":
                        raise ReferenceScanError(f"calibration workflow schema: {base}")
                    verify_qubit_spectroscopy_calibration(base)
                else:
                    raise ReferenceScanError(f"unknown workflow verifier: {base}")
            except Exception as exc:
                raise ReferenceScanError(f"workflow verifier failed: {base}: {exc}") from exc
            run = _uuid(workflow.get("run_id"), "workflow run_id")
            if run in workflow_runs:
                raise ReferenceScanError(f"duplicate workflow run identity: {base}")
            workflow_runs.add(run)
            recommendation_value = workflow.get("recommendation_id")
            if recommendation_value is not None:
                recommendation = _uuid(recommendation_value, "workflow recommendation_id")
                if recommendation in workflows:
                    raise ReferenceScanError(f"duplicate workflow recommendation identity: {base}")
                workflows[recommendation] = (run, base)
        if "decision.json" in files:
            decisions.append(base)
    for directory in decisions:
        try:
            verify_qubit_spectroscopy_calibration_decision(directory)
        except Exception as exc:
            raise ReferenceScanError(f"invalid decision transaction: {directory}: {exc}") from exc
        decision = _json(directory / "decision.json")
        if decision.get("decision") != "accept":
            continue
        recommendation = _uuid(decision.get("recommendation_id"), "recommendation_id")
        workflow = workflows.get(recommendation)
        if workflow is None or decision.get("recommendation_sha256") != _raw_sha256(workflow[1] / "workflow.json"):
            raise ReferenceScanError(f"decision workflow reverse binding: {directory}")
        edges.append(_edge(workflow[0], "accepted_decision", directory / "decision.json", root, recommendation))


def _scan_snapshot_pins(root: Path) -> None:
    _check_absolute_ancestors(root)
    if not root.exists():
        return
    _check_path(root, root.parent)
    for path in _json_children(root):
        payload = _json(path)
        _exact(payload, {"schema_version", "snapshot_id", "actor_id", "pinned_utc"}, path)
        if payload.get("schema_version") != "0.1" or path.stem != _uuid(payload.get("snapshot_id"), "snapshot_id"):
            raise ReferenceScanError(f"pin schema: {path}")
        # Current published pin records protect snapshots, not an experiment run.
        # They are validated here but create no invented run edge.


def _scan_run_pins(root: Path, edges: list[ReferenceEdge]) -> None:
    for path in _json_children(root):
        payload = _json(path)
        keys = {"schema_version", "artifact_type", "artifact_version", "run_id", "workflow_sha256", "manual_keep", "actor_id", "updated_utc", "content_sha256"}
        _exact(payload, keys, path)
        run = _uuid(payload.get("run_id"), "run_id")
        if path.stem != run or payload.get("schema_version") != "0.1" or payload.get("artifact_type") != "sqvm_experiment_manual_keep" or payload.get("artifact_version") != "0.1" or payload.get("manual_keep") is not True or not isinstance(payload.get("actor_id"), str) or _ACTOR.fullmatch(payload["actor_id"]) is None or not _utc(payload.get("updated_utc")):
            raise ReferenceScanError(f"run pin schema: {path}")
        _hash(payload.get("workflow_sha256"), "pin workflow_sha256")
        _hash(payload.get("content_sha256"), "pin content_sha256")
        body = {key: value for key, value in payload.items() if key != "content_sha256"}
        if payload["content_sha256"] != hashlib.sha256(canonical_json_bytes(body)).hexdigest().upper():
            raise ReferenceScanError(f"run pin content hash: {path}")
        edges.append(_edge(run, "manual_keep", path, root, None))


def _source_edge(source: Any, kind: str, path: Path, root: Path, edges: list[ReferenceEdge]) -> None:
    if source is None:
        return
    source = _source_candidate(source)
    edges.append(_edge(source["experiment_run_id"], kind, path, root, source["recommendation_id"]))


def _source_candidate(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReferenceScanError("source_candidate must be an object")
    required = {"experiment_run_id", "recommendation_id", "candidate_ids", "calibration_subjects", "configuration_targets", "targets", "candidates"}
    allowed = required | {"candidate_values_GHz"}
    if set(value) != required and set(value) != allowed:
        raise ReferenceScanError("source_candidate schema keys")
    _uuid(value.get("experiment_run_id"), "experiment_run_id")
    _uuid(value.get("recommendation_id"), "recommendation_id")
    for key in ("candidate_ids", "calibration_subjects", "configuration_targets", "targets"):
        if not isinstance(value.get(key), list) or any(not isinstance(item, str) or not item for item in value[key]):
            raise ReferenceScanError(f"invalid source_candidate {key}")
        if len(value[key]) != len(set(value[key])):
            raise ReferenceScanError(f"duplicate source_candidate {key}")
    if not isinstance(value["candidates"], list) or not value["candidates"]:
        raise ReferenceScanError("invalid source_candidate candidates")
    candidate_ids: list[str] = []
    for candidate in value["candidates"]:
        if not isinstance(candidate, Mapping):
            raise ReferenceScanError("invalid source candidate")
        _exact(candidate, {"candidate_id", "candidate_type", "calibration_subjects", "configuration_resources", "configuration_targets", "target", "changes"}, Path("source_candidate"))
        if not all(isinstance(candidate.get(key), str) and candidate[key] for key in ("candidate_id", "candidate_type", "target")):
            raise ReferenceScanError("invalid candidate identity")
        candidate_ids.append(candidate["candidate_id"])
        if not isinstance(candidate.get("calibration_subjects"), list) or not isinstance(candidate.get("configuration_targets"), list) or not isinstance(candidate.get("configuration_resources"), list) or not isinstance(candidate.get("changes"), list) or not candidate["changes"]:
            raise ReferenceScanError("invalid candidate collection")
        for resource in candidate["configuration_resources"]:
            if not isinstance(resource, Mapping) or set(resource) != {"owner", "resource_id", "resource_type"} or any(not isinstance(resource[key], str) or not resource[key] for key in resource):
                raise ReferenceScanError("invalid candidate resource")
        for change in candidate["changes"]:
            if not isinstance(change, Mapping) or set(change) != {"parameter_path", "proposed_value", "unit", "configuration_resource"} or not isinstance(change.get("parameter_path"), str) or not isinstance(change.get("unit"), str) or not _finite(change.get("proposed_value")):
                raise ReferenceScanError("invalid candidate change")
            resource = change["configuration_resource"]
            if not isinstance(resource, Mapping) or set(resource) != {"owner", "resource_id", "resource_type"}:
                raise ReferenceScanError("invalid change resource")
    if candidate_ids != value["candidate_ids"]:
        raise ReferenceScanError("candidate id ordering/binding")
    if "candidate_values_GHz" in value:
        if not isinstance(value["candidate_values_GHz"], Mapping) or any(not isinstance(k, str) or not _numeric_finite(v) for k, v in value["candidate_values_GHz"].items()):
            raise ReferenceScanError("invalid candidate_values_GHz")
    return value


def _configuration(payload: Mapping[str, Any], artifact: str, version: str, path: Path) -> None:
    artifact_version = "0.1" if artifact == "platform_configuration_current" else version
    if payload.get("schema_version") != version or payload.get("artifact_type") != artifact or payload.get("artifact_version") != artifact_version:
        raise ReferenceScanError(f"configuration schema: {path}")
    if not isinstance(payload.get("editable"), Mapping) or not isinstance(payload.get("content_sha256"), str):
        raise ReferenceScanError(f"configuration content: {path}")
    _hash(payload["content_sha256"], "content_sha256")
    if payload["content_sha256"] != sha256_json(payload["editable"]):
        raise ReferenceScanError(f"configuration content hash: {path}")
    if artifact == "platform_configuration_current":
        # v0.3 current is a separate, mutable envelope.  Its published contract
        # deliberately allows accepted and draft calibration records together;
        # the frozen v0.2 Draft validator therefore cannot be applied wholesale.
        _exact(payload, {"schema_version", "artifact_type", "artifact_version", "current_id", "device_id", "name", "note", "actor_id", "created_utc", "updated_utc", "revision", "source_snapshot_id", "parent", "readonly", "editable", "content_sha256", "validation", "source_candidate"}, path)
        if payload.get("current_id") != payload.get("device_id") or type(payload.get("revision")) is not int or payload["revision"] < 1:
            raise ReferenceScanError(f"current configuration identity: {path}")
        errors = validate_editable(payload["editable"], published=False)
    else:
        errors = validate_document(dict(payload))
    if errors:
        raise ReferenceScanError(f"configuration validation: {path}")


def _checkpoint(payload: Mapping[str, Any], draft: Mapping[str, Any], path: Path) -> None:
    required = {"schema_version", "draft_id", "checkpoint", "updated_utc", "editable", "content_sha256"}
    _exact(payload, required, path)
    if payload.get("schema_version") != "0.1" or payload.get("draft_id") != draft.get("draft_id") or type(payload.get("checkpoint")) is not int or payload["checkpoint"] < 0:
        raise ReferenceScanError(f"checkpoint schema: {path}")
    _hash(payload.get("content_sha256"), "checkpoint content_sha256")
    if payload["content_sha256"] != sha256_json(payload["editable"]) or validate_editable(payload["editable"], published=False):
        raise ReferenceScanError(f"checkpoint validation: {path}")


def _edge(run: str, kind: str, path: Path, root: Path, recommendation: str | None) -> ReferenceEdge:
    relative = path.relative_to(root).as_posix()
    digest = _raw_sha256(path)
    return ReferenceEdge(run, kind, relative, digest, recommendation, (relative,), (digest,))


def _edge_with_evidence(run: str, kind: str, identity_path: Path, identity_root: Path, recommendation: str | None, evidence: tuple[Path, ...], evidence_root: Path) -> ReferenceEdge:
    relative = identity_path.relative_to(identity_root).as_posix()
    digest = _raw_sha256(identity_path)
    paths = tuple(item.relative_to(evidence_root).as_posix() for item in evidence)
    hashes = tuple(_raw_sha256(item) for item in evidence)
    return ReferenceEdge(run, kind, relative, digest, recommendation, paths, hashes)


def _authority_root(value: str | Path | None, *, required: bool, label: str) -> Path | None:
    if value is None:
        if required:
            raise ReferenceScanError(f"{label} root is required")
        return None
    path = Path(value).absolute()
    _check_absolute_ancestors(path)
    if not path.exists():
        if required:
            raise ReferenceScanError(f"{label} root is missing: {path}")
        return None
    if not path.is_dir():
        raise ReferenceScanError(f"{label} root is not a directory")
    return path


def _check_path(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ReferenceScanError(f"path escapes authority root: {path}") from exc
    _check_absolute_ancestors(path)
    cursor = path
    while True:
        stat = cursor.lstat()
        if cursor.is_symlink() or bool(getattr(cursor, "is_junction", lambda: False)()) or getattr(stat, "st_reparse_tag", 0):
            raise ReferenceScanError(f"link/reparse authority path: {cursor}")
        if cursor.is_file() and stat.st_nlink != 1:
            raise ReferenceScanError(f"hardlinked authority file: {cursor}")
        if cursor == root:
            break
        cursor = cursor.parent


def _check_absolute_ancestors(path: Path) -> None:
    cursor = path.absolute()
    anchor = Path(cursor.anchor)
    while True:
        if os.path.lexists(cursor):
            stat = cursor.lstat()
            if cursor.is_symlink() or bool(getattr(cursor, "is_junction", lambda: False)()) or getattr(stat, "st_reparse_tag", 0):
                raise ReferenceScanError(f"link/reparse authority ancestor: {cursor}")
        if cursor == anchor:
            return
        cursor = cursor.parent


def _check_children(root: Path, allowed: set[str], *, directories: set[str]) -> None:
    if not root.exists():
        return
    _check_path(root, root)
    for entry in os.scandir(root):
        path = Path(entry.path)
        _check_path(path, root)
        if entry.name not in allowed or (entry.is_dir(follow_symlinks=False) != (entry.name in directories)):
            raise ReferenceScanError(f"unknown authority entry: {path}")


def _check_tree(root: Path, *, allow_directories: bool) -> None:
    _check_path(root, root)
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            _check_path(Path(directory) / name, root)


def _json_children(root: Path) -> list[Path]:
    _check_path(root, root)
    paths: list[Path] = []
    for entry in os.scandir(root):
        path = Path(entry.path)
        _check_path(path, root)
        if not entry.is_file(follow_symlinks=False) or path.suffix != ".json":
            raise ReferenceScanError(f"unknown JSON authority entry: {path}")
        paths.append(path)
    return sorted(paths)


def _id_directories(root: Path) -> list[Path]:
    _check_path(root, root)
    paths: list[Path] = []
    for entry in os.scandir(root):
        path = Path(entry.path)
        _check_path(path, root)
        if not entry.is_dir(follow_symlinks=False):
            raise ReferenceScanError(f"unknown authority entry: {path}")
        _uuid(entry.name, "directory id")
        paths.append(path)
    return sorted(paths)


def _json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except Exception as exc:
        raise ReferenceScanError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict) or not _finite(value):
        raise ReferenceScanError(f"invalid JSON value: {path}")
    return value


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceScanError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise ReferenceScanError(f"non-finite JSON constant: {token}")


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _numeric_finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _exact(value: Mapping[str, Any], keys: set[str], path: Path) -> None:
    if set(value) != keys:
        raise ReferenceScanError(f"schema keys: {path}")


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ReferenceScanError(f"invalid {label}")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ReferenceScanError(f"invalid {label}") from exc


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ReferenceScanError(f"invalid {label}")
    return value


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _validate_duplicate_identities(edges: Iterable[ReferenceEdge]) -> None:
    # A run can legitimately appear in several configuration generations.  The
    # only global identities are workflows/decisions, checked before edge creation.
    if any(edge.reference_type not in REFERENCE_TYPES for edge in edges):
        raise ReferenceScanError("unknown reference type")
