"""Single-user draft and platform-configuration snapshot management."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from typing import Any, Callable, Mapping, Sequence
import uuid

import yaml

from sqvm.candidate_protocol import (
    CalibrationCandidateProtocolError,
    candidate_values_equal,
    normalize_calibration_candidate,
    set_parameter_value,
    value_at_parameter_path,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json
from sqvm.runtime.journal import utc_now_text
from sqvm.web.configuration_schema import (
    draftify_calibration,
    editor_view,
    initial_simulation_configuration,
    initial_typed_calibration,
    load_frozen_schema,
    publish_calibration,
    validate_document,
    validate_editable,
)


_ACTOR_ID = re.compile(r"[a-z][a-z0-9._-]{2,63}$")
_DEVICE_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}$")
_SYSTEM_FIELDS = {
    "setting_hash",
    "revision",
    "calibration_run_id",
    "calibration_id",
    "state_id",
    "status",
    "accepted",
    "parent_calibration_sha256",
    "recommendation_sha256",
    "decision_sha256",
}
_DIFF_EXCLUDED_FIELDS = _SYSTEM_FIELDS | {
    "base_revision",
    "base_setting_hash",
    "setting_id",
    "mapper_id",
    "target",
    "gate_type",
    "mapper_type",
    "wave_index",
}
_MAX_CHECKPOINTS = 20
_MAX_AUTOMATIC_SNAPSHOTS = 10
_WINDOWS_TRANSIENT_REPLACE_ERRORS = frozenset({5, 32, 33})
_CONFIGURATION_REPLACE_ATTEMPTS = 25
_PHYSICAL_FIELD_NAMES = {
    "capacitance",
    "capacitance_f",
    "capacitance_matrix",
    "inductance",
    "inductance_h",
    "josephson_energy",
    "junction_energy",
    "junctions",
    "e_c",
    "e_j",
    "ec",
    "ej",
}
_ACCEPTANCE_FIELDS = {
    "max_condition_number",
    "max_xy_area_relative_error",
    "max_readout_area_relative_error",
    "max_z_flat_top_error_phi0",
    "max_phase_proxy_rad",
    "phase_proxy_window_ns",
    "max_formal_samples_per_scenario",
    "analysis_runtime_budget_seconds",
    "total_runtime_budget_seconds",
}


class ConfigurationManagementError(ValueError):
    def __init__(self, message: str, *, status: int = 422, field_errors: Sequence[Mapping[str, str]] = ()) -> None:
        self.status = status
        self.field_errors = [dict(item) for item in field_errors]
        super().__init__(message)


class PlatformConfigurationStore:
    """Manage working drafts separately from immutable published snapshots."""

    def __init__(
        self,
        repository_root: str | Path,
        storage_root: str | Path | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.schema = load_frozen_schema(self.repository_root)
        self.root = self._inside(
            storage_root or self.repository_root / "output" / "platform-configurations",
            "configuration storage",
        )
        self.drafts_root = self.root / "drafts"
        self.snapshots_root = self.root / "snapshots"
        self.current_root = self.root / "current"
        self.active_root = self.root / "active"
        self.pins_root = self.root / "pins"
        self.audit_root = self.root / "audit"

    def summary(self) -> dict[str, Any]:
        drafts = self.drafts()
        snapshots = self.snapshots()
        active = self.active_configurations()
        current = self.current_configurations()
        return {
            "schema_version": "0.2",
            "checkpoint_limit": _MAX_CHECKPOINTS,
            "automatic_snapshot_limit": _MAX_AUTOMATIC_SNAPSHOTS,
            "drafts": drafts,
            "snapshots": snapshots,
            "current": current,
            "active": active,
        }

    def current_configurations(self) -> list[dict[str, Any]]:
        """Return one mutable current configuration per managed device.

        Existing installations are migrated lazily from their Active snapshot (or
        newest managed version) so the previous lifecycle remains readable.
        """

        devices = {
            row["device_id"] for row in self.active_configurations()
        } | {
            row["device_id"] for row in self.snapshots()
        } | {
            row["device_id"] for row in self.drafts()
        }
        for device_id in sorted(devices):
            self._ensure_current_configuration(device_id)
        if not self.current_root.is_dir():
            return []
        rows = []
        for path in sorted(self.current_root.glob("*.json")):
            try:
                payload = self._load_json(path, "current configuration")
                rows.append(self._current_summary(payload))
            except Exception:
                continue
        rows.sort(key=lambda row: (row["updated_utc"], row["device_id"]), reverse=True)
        return rows

    def current_configuration(self, device_id: str) -> dict[str, Any]:
        self._device(device_id)
        self._ensure_current_configuration(device_id)
        path = self._current_path(device_id)
        payload = self._load_json(path, "current configuration")
        return {
            **payload,
            "editor_view": editor_view(),
            "field_errors": payload.get("validation", {}).get("field_errors", []),
        }

    def update_current_configuration(
        self,
        device_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
        name: str,
        note: str,
        editable: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._actor(actor_id)
        self._device(device_id)
        self._text(name, "name", 1, 96)
        self._text(note, "note", 0, 1024)
        payload = self._load_json(self._current_path(device_id), "current configuration")
        if payload["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError("current configuration changed since it was loaded", status=409)
        normalized = self._validate_editable_shape(editable)
        self._assert_system_fields_unchanged(payload["editable"], normalized)
        write_errors = [
            row for row in validate_editable(normalized, published=False)
            if row["code"] in {"additional", "generated"}
            or "physical" in row["message"].lower()
        ]
        if write_errors:
            raise ConfigurationManagementError(
                "editable input contains generated or unsupported fields",
                field_errors=write_errors,
            )
        errors = validate_editable(normalized, published=False)
        if errors:
            raise ConfigurationManagementError(
                "current configuration must be valid before it can become effective",
                field_errors=errors,
            )
        source_candidate = _retained_candidate_source(
            payload.get("source_candidate"), normalized
        )
        payload.update(
            {
                "name": name,
                "note": note,
                "actor_id": actor_id,
                "updated_utc": utc_now_text(),
                "revision": int(payload.get("revision", 0)) + 1,
                "editable": normalized,
                "content_sha256": sha256_json(normalized),
                "source_candidate": source_candidate,
                "validation": {
                    "status": "valid" if not errors else "invalid",
                    "field_errors": errors,
                    "requires_requalification": self._requires_requalification(
                        {**payload, "editable": normalized}
                    ),
                },
            }
        )
        self._atomic_json(self._current_path(device_id), payload)
        self._audit(
            "current_configuration_updated",
            actor_id,
            {"device_id": device_id, "revision": payload["revision"]},
        )
        self.snapshot_current_configuration(
            device_id,
            actor_id=actor_id,
            expected_content_sha256=payload["content_sha256"],
            name=name,
            reason="当前配置保存后自动生效",
            _activate=True,
        )
        return self.current_configuration(device_id)

    def initialize_current_calibration(
        self,
        device_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        payload = self._load_json(self._current_path(device_id), "current configuration")
        if payload["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError("current configuration changed since it was loaded", status=409)
        if payload["editable"].get("calibration_values") != {}:
            raise ConfigurationManagementError("current calibration is already initialized")
        editable = copy.deepcopy(payload["editable"])
        editable["calibration_values"] = initial_typed_calibration()
        errors = validate_editable(editable, published=False)
        payload.update(
            {
                "actor_id": actor_id,
                "updated_utc": utc_now_text(),
                "revision": int(payload.get("revision", 0)) + 1,
                "editable": editable,
                "content_sha256": sha256_json(editable),
                "source_candidate": None,
                "validation": {
                    "status": "valid" if not errors else "invalid",
                    "field_errors": errors,
                    "requires_requalification": self._requires_requalification(
                        {**payload, "editable": editable}
                    ),
                },
            }
        )
        self._atomic_json(self._current_path(device_id), payload)
        self._audit("current_calibration_initialized", actor_id, {"device_id": device_id})
        if not errors:
            self.snapshot_current_configuration(
                device_id,
                actor_id=actor_id,
                expected_content_sha256=payload["content_sha256"],
                name=payload["name"],
                reason="校准配置初始化后自动生效",
                _activate=True,
            )
        return self.current_configuration(device_id)

    def apply_candidates_to_current_configuration(
        self,
        device_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
        experiment_run_id: str,
        recommendation_id: str,
        candidates: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Apply verified calibration candidates to the mutable current configuration."""

        self._actor(actor_id)
        self._device(device_id)
        if not candidates:
            raise ConfigurationManagementError("at least one candidate is required")
        payload = self._load_json(self._current_path(device_id), "current configuration")
        if payload["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError(
                "current configuration changed since it was loaded", status=409
            )
        editable = copy.deepcopy(payload["editable"])
        normalized_candidates = _apply_candidate_groups(editable, candidates)
        errors = validate_editable(editable, published=False)
        if errors:
            raise ConfigurationManagementError(
                "candidate update would make current configuration invalid",
                field_errors=errors,
            )
        source_candidate = _candidate_source(
            experiment_run_id,
            recommendation_id,
            normalized_candidates,
            editable,
        )
        payload.update(
            {
                "actor_id": actor_id,
                "updated_utc": utc_now_text(),
                "revision": int(payload.get("revision", 0)) + 1,
                "editable": editable,
                "content_sha256": sha256_json(editable),
                "source_candidate": source_candidate,
                "validation": {
                    "status": "valid",
                    "field_errors": [],
                    "requires_requalification": self._requires_requalification(
                        {**payload, "editable": editable}
                    ),
                },
            }
        )
        self._atomic_json(self._current_path(device_id), payload)
        self._audit(
            "experiment_candidates_applied_to_current",
            actor_id,
            {
                "device_id": device_id,
                "experiment_run_id": experiment_run_id,
                "recommendation_id": recommendation_id,
                "candidate_ids": source_candidate["candidate_ids"],
                "targets": source_candidate["targets"],
                "content_sha256": payload["content_sha256"],
            },
        )
        self.snapshot_current_configuration(
            device_id,
            actor_id=actor_id,
            expected_content_sha256=payload["content_sha256"],
            name=payload["name"],
            reason=f"实验 {experiment_run_id} 的校准候选保存后自动生效",
            _activate=True,
        )
        return self.current_configuration(device_id)

    def snapshot_current_configuration(
        self,
        device_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
        name: str,
        reason: str,
        keep: bool = False,
        _activate: bool = False,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        self._device(device_id)
        self._text(name, "name", 1, 96)
        self._text(reason, "reason", 1, 1024)
        current = self._load_json(self._current_path(device_id), "current configuration")
        if current["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError("current configuration changed since it was loaded", status=409)
        errors = validate_editable(current["editable"], published=False)
        if errors:
            raise ConfigurationManagementError(
                "current configuration must be valid before saving a snapshot",
                field_errors=errors,
            )
        snapshot_id = str(uuid.uuid4())
        base_calibration: Mapping[str, Any] = {}
        source_snapshot_id = current.get("source_snapshot_id")
        if isinstance(source_snapshot_id, str):
            base_calibration = self.snapshot(source_snapshot_id)["editable"].get(
                "calibration_values", {}
            )
        editable = copy.deepcopy(current["editable"])
        editable = _with_simulation_defaults(editable)
        source_candidate = current.get("source_candidate")
        candidate_runs = _candidate_run_bindings(source_candidate)
        try:
            editable["calibration_values"] = publish_calibration(
                editable["calibration_values"],
                base_calibration,
                manual_run_id=f"manual_{snapshot_id}",
                candidate_runs=candidate_runs,
            )
        except ValueError as exc:
            raise ConfigurationManagementError(str(exc)) from exc
        now = utc_now_text()
        requires_requalification = (
            False if _activate else self._requires_requalification(current)
        )
        snapshot = {
            "schema_version": "0.2",
            "artifact_type": "platform_configuration_snapshot",
            "artifact_version": "0.2",
            "snapshot_id": snapshot_id,
            "state_id": snapshot_id,
            "device_id": device_id,
            "name": name,
            "reason": reason,
            "actor_id": actor_id,
            "published_utc": now,
            "status": "published",
            "keep": bool(keep),
            "parent": current["parent"],
            "readonly": current["readonly"],
            "editable": editable,
            "content_sha256": sha256_json(editable),
            "requires_requalification": requires_requalification,
            "experiment_eligible": not requires_requalification,
        }
        document_errors = validate_document(snapshot)
        if document_errors:
            raise ConfigurationManagementError(
                "snapshot document is invalid", field_errors=document_errors
            )
        directory = self.snapshots_root / snapshot_id
        directory.mkdir(parents=True, exist_ok=False)
        self._atomic_json(directory / "snapshot.json", snapshot)
        if source_candidate:
            self._atomic_json(directory / "source_candidate.json", source_candidate)
        if keep:
            self._pin(snapshot_id, actor_id)

        current_editable = copy.deepcopy(snapshot["editable"])
        current_editable["calibration_values"] = draftify_calibration(
            snapshot["editable"]["calibration_values"]
        )
        current.update(
            {
                "actor_id": actor_id,
                "updated_utc": now,
                "revision": (
                    int(current.get("revision", 0))
                    if _activate
                    else int(current.get("revision", 0)) + 1
                ),
                "source_snapshot_id": snapshot_id,
                "parent": self._base_reference(snapshot),
                "editable": current_editable,
                "content_sha256": sha256_json(current_editable),
                "validation": {
                    "status": "valid",
                    "field_errors": [],
                    "requires_requalification": False,
                },
            }
        )
        self._atomic_json(self._current_path(device_id), current)
        self._audit(
            "current_snapshot_saved",
            actor_id,
            {"device_id": device_id, "snapshot_id": snapshot_id},
        )
        if _activate:
            self._activate_snapshot(snapshot, actor_id=actor_id)
        self._prune_automatic_snapshots(device_id)
        return self.snapshot(snapshot_id)

    def apply_snapshot_to_current(
        self,
        snapshot_id: str,
        *,
        actor_id: str,
        expected_current_content_sha256: str,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        snapshot = self.snapshot(snapshot_id)
        current = self._load_json(
            self._current_path(snapshot["device_id"]), "current configuration"
        )
        if current["content_sha256"] != expected_current_content_sha256:
            raise ConfigurationManagementError("current configuration changed since it was loaded", status=409)
        editable = _with_simulation_defaults(snapshot["editable"])
        editable["calibration_values"] = draftify_calibration(
            snapshot["editable"]["calibration_values"]
        )
        source_candidate = _retained_candidate_source(
            snapshot.get("source_candidate"), editable
        )
        errors = validate_editable(editable, published=False)
        current.update(
            {
                "name": snapshot["name"],
                "note": f"从快照 {snapshot_id} 恢复：{snapshot.get('reason', '')}".rstrip("："),
                "actor_id": actor_id,
                "updated_utc": utc_now_text(),
                "revision": int(current.get("revision", 0)) + 1,
                "source_snapshot_id": snapshot_id,
                "parent": self._base_reference(snapshot),
                "readonly": copy.deepcopy(snapshot["readonly"]),
                "editable": editable,
                "content_sha256": sha256_json(editable),
                "source_candidate": source_candidate,
                "validation": {
                    "status": "valid" if not errors else "invalid",
                    "field_errors": errors,
                    "requires_requalification": False,
                },
            }
        )
        self._atomic_json(self._current_path(snapshot["device_id"]), current)
        self._audit(
            "snapshot_applied_to_current",
            actor_id,
            {"device_id": snapshot["device_id"], "snapshot_id": snapshot_id},
        )
        if snapshot.get("experiment_eligible"):
            self._activate_snapshot(snapshot, actor_id=actor_id)
        else:
            self.snapshot_current_configuration(
                snapshot["device_id"],
                actor_id=actor_id,
                expected_content_sha256=current["content_sha256"],
                name=current["name"],
                reason=f"快照 {snapshot_id} 恢复后自动生效",
                _activate=True,
            )
        return self.current_configuration(snapshot["device_id"])

    def drafts(self) -> list[dict[str, Any]]:
        if not self.drafts_root.is_dir():
            return []
        rows = []
        for directory in sorted(self.drafts_root.iterdir()):
            path = directory / "draft.json"
            if not directory.is_dir() or not path.is_file():
                continue
            try:
                payload = self._load_json(path, "draft")
                rows.append(self._draft_summary(payload))
            except Exception:
                continue
        rows.sort(key=lambda row: (row["updated_utc"], row["draft_id"]), reverse=True)
        return rows

    def draft(self, draft_id: str) -> dict[str, Any]:
        path = self._draft_path(draft_id)
        payload = self._load_json(path, "draft")
        checkpoints = []
        checkpoint_root = path.parent / "checkpoints"
        if checkpoint_root.is_dir():
            for item in sorted(checkpoint_root.glob("*.json"), reverse=True):
                row = self._load_json(item, "draft checkpoint")
                checkpoints.append(
                    {
                        "checkpoint": row["checkpoint"],
                        "updated_utc": row["updated_utc"],
                        "content_sha256": row["content_sha256"],
                    }
                )
        source_path = path.parent / "source_candidate.json"
        source_candidate = self._load_json(source_path, "source candidate") if source_path.is_file() else None
        return {**payload, "checkpoints": checkpoints, "editor_view": editor_view(), "field_errors": [], "source_candidate": source_candidate}

    def draft_diff(self, draft_id: str, *, against: str = "parent") -> dict[str, Any]:
        """Return the current editable changes against the draft's initial checkpoint.

        Drafts are created from a parent configuration, but their editable state is
        normalized into Draft form at checkpoint zero.  That checkpoint is therefore
        the only stable, comparable parent baseline for the workbench.
        """

        if against != "parent":
            raise ConfigurationManagementError("diff baseline is invalid")
        path = self._draft_path(draft_id)
        draft = self._load_json(path, "draft")
        baseline = self._load_json(
            path.parent / "checkpoints" / "00000000.json",
            "initial draft checkpoint",
        )
        raw_before = baseline.get("editable")
        raw_after = draft.get("editable")
        before = _without_diff_excluded_fields(raw_before)
        after = _without_diff_excluded_fields(raw_after)
        changes: list[dict[str, Any]] = []
        _collect_editable_changes(before, after, "$", changes)
        for change in changes:
            change["group"] = _change_group(change["path"], raw_before, raw_after)
        control_changed = any(change["group"] == "control" for change in changes)
        return {
            "draft_id": draft_id,
            "against": "parent",
            "baseline_checkpoint": baseline["checkpoint"],
            "baseline_content_sha256": baseline["content_sha256"],
            "current_content_sha256": draft["content_sha256"],
            "changes": changes,
            "changed_count": len(changes),
            "control_changed": control_changed,
            "requires_requalification": self._requires_requalification(draft),
        }

    def create_draft(
        self,
        base_configuration: Mapping[str, Any],
        *,
        actor_id: str,
        name: str,
        note: str = "",
        source_candidate: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        self._text(name, "name", 1, 96)
        self._text(note, "note", 0, 1024)
        sections = self._editable_sections(base_configuration)
        sections["calibration_values"] = draftify_calibration(sections["calibration_values"])
        draft_id = str(uuid.uuid4())
        now = utc_now_text()
        payload = {
            "schema_version": "0.2",
            "artifact_type": "platform_configuration_draft",
            "artifact_version": "0.2",
            "draft_id": draft_id,
            "device_id": self._base_device_id(base_configuration),
            "name": name,
            "note": note,
            "actor_id": actor_id,
            "created_utc": now,
            "updated_utc": now,
            "checkpoint": 0,
            "parent": self._base_reference(base_configuration),
            "readonly": self._readonly_sections(base_configuration),
            "editable": sections,
            "content_sha256": sha256_json(sections),
            "validation": {
                "status": "not_validated",
                "validated_content_sha256": None,
                "requires_requalification": True,
            },
        }
        directory = self.drafts_root / draft_id
        directory.mkdir(parents=True, exist_ok=False)
        self._atomic_json(directory / "draft.json", payload)
        self._write_checkpoint(payload)
        if source_candidate:
            self._atomic_json(directory / "source_candidate.json", dict(source_candidate))
        self._audit("draft_created", actor_id, {"draft_id": draft_id})
        return self.draft(draft_id)

    def update_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
        name: str,
        note: str,
        editable: Mapping[str, Any],
    ) -> dict[str, Any]:
        self._actor(actor_id)
        self._text(name, "name", 1, 96)
        self._text(note, "note", 0, 1024)
        payload = self._load_json(self._draft_path(draft_id), "draft")
        if payload["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError("draft changed since it was loaded", status=409)
        normalized = self._validate_editable_shape(editable)
        self._assert_system_fields_unchanged(payload["editable"], normalized)
        write_errors = [row for row in validate_editable(normalized, published=False) if row["code"] in {"additional", "generated"} or "physical" in row["message"].lower()]
        if write_errors:
            raise ConfigurationManagementError("editable input contains generated or unsupported fields", field_errors=write_errors)
        payload["name"] = name
        payload["note"] = note
        payload["actor_id"] = actor_id
        payload["updated_utc"] = utc_now_text()
        payload["checkpoint"] += 1
        payload["editable"] = normalized
        payload["content_sha256"] = sha256_json(normalized)
        payload["validation"] = {
            "status": "not_validated",
            "validated_content_sha256": None,
            "requires_requalification": True,
        }
        source_path = self._draft_path(draft_id).parent / "source_candidate.json"
        if source_path.is_file():
            source = self._load_json(source_path, "source candidate")
            self._atomic_json(
                source_path,
                _retained_candidate_source(source, normalized) or {},
            )
        self._atomic_json(self._draft_path(draft_id), payload)
        self._write_checkpoint(payload)
        self._audit("draft_updated", actor_id, {"draft_id": draft_id})
        return self.draft(draft_id)

    def apply_candidates_to_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        experiment_run_id: str,
        recommendation_id: str,
        candidates: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        self._actor(actor_id)
        if not candidates:
            raise ConfigurationManagementError("at least one candidate is required")
        payload = self._load_json(self._draft_path(draft_id), "draft")
        editable = copy.deepcopy(payload["editable"])
        initializing_calibration = editable.get("calibration_values") == {}
        if initializing_calibration:
            editable["calibration_values"] = initial_typed_calibration()
        candidate_rows = (
            _rebase_candidates_to_initialized_calibration(editable, candidates)
            if initializing_calibration
            else candidates
        )
        normalized_candidates = _apply_candidate_groups(editable, candidate_rows)
        errors = validate_editable(editable, published=False)
        if errors:
            raise ConfigurationManagementError(
                "candidate update would make draft invalid",
                field_errors=errors,
            )
        source_candidate = _candidate_source(
            experiment_run_id,
            recommendation_id,
            normalized_candidates,
            editable,
        )
        payload["editable"] = editable
        payload["updated_utc"] = utc_now_text()
        payload["checkpoint"] += 1
        payload["content_sha256"] = sha256_json(editable)
        payload["validation"] = {
            "status": "not_validated",
            "validated_content_sha256": None,
            "requires_requalification": self._requires_requalification(payload),
        }
        self._atomic_json(self._draft_path(draft_id), payload)
        self._atomic_json(self._draft_path(draft_id).parent / "source_candidate.json", source_candidate)
        self._write_checkpoint(payload)
        self._audit(
            "experiment_candidates_applied",
            actor_id,
            {
                "draft_id": draft_id,
                "experiment_run_id": experiment_run_id,
                "candidate_ids": source_candidate["candidate_ids"],
                "targets": source_candidate["targets"],
            },
        )
        return self.draft(draft_id)

    def initialize_calibration_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
    ) -> dict[str, Any]:
        """Initialize the only supported structured calibration authoring template."""

        self._actor(actor_id)
        payload = self._load_json(self._draft_path(draft_id), "draft")
        if payload["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError("draft changed since it was loaded", status=409)
        if payload["editable"].get("calibration_values") != {}:
            raise ConfigurationManagementError("calibration draft is already initialized")
        editable = copy.deepcopy(payload["editable"])
        editable["calibration_values"] = initial_typed_calibration()
        errors = validate_editable(editable, published=False)
        if errors:
            raise ConfigurationManagementError("typed calibration template is invalid", field_errors=errors)
        payload["editable"] = editable
        payload["actor_id"] = actor_id
        payload["updated_utc"] = utc_now_text()
        payload["checkpoint"] += 1
        payload["content_sha256"] = sha256_json(editable)
        payload["validation"] = {"status": "not_validated", "validated_content_sha256": None, "requires_requalification": self._requires_requalification(payload)}
        self._atomic_json(self._draft_path(draft_id), payload)
        self._write_checkpoint(payload)
        self._audit("draft_calibration_initialized", actor_id, {"draft_id": draft_id})
        return self.draft(draft_id)

    def validate_draft(self, draft_id: str, *, actor_id: str) -> dict[str, Any]:
        self._actor(actor_id)
        payload = self._load_json(self._draft_path(draft_id), "draft")
        field_errors = validate_editable(payload.get("editable"), published=False)
        checks = [self._check("editable_schema", not field_errors, "v0.2 editable schema is valid" if not field_errors else field_errors[0]["message"])]
        passed = not field_errors
        payload["validation"] = {
            "status": "valid" if passed else "invalid",
            "validated_content_sha256": payload["content_sha256"] if passed else None,
            "requires_requalification": self._requires_requalification(payload),
        }
        payload["updated_utc"] = utc_now_text()
        self._atomic_json(self._draft_path(draft_id), payload)
        self._audit(
            "draft_validated",
            actor_id,
            {"draft_id": draft_id, "passed": passed},
        )
        return {**payload["validation"], "checks": checks, "field_errors": field_errors, "editor_view": editor_view()}

    def publish_draft(
        self,
        draft_id: str,
        *,
        actor_id: str,
        expected_content_sha256: str,
        name: str,
        reason: str,
        keep: bool = False,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        self._text(name, "name", 1, 96)
        self._text(reason, "reason", 1, 1024)
        draft = self._load_json(self._draft_path(draft_id), "draft")
        if draft["content_sha256"] != expected_content_sha256:
            raise ConfigurationManagementError("draft changed since validation", status=409)
        validation = draft.get("validation")
        if (
            not isinstance(validation, Mapping)
            or validation.get("status") != "valid"
            or validation.get("validated_content_sha256") != draft["content_sha256"]
        ):
            raise ConfigurationManagementError("draft must be validated before publish")
        parent_content = draft.get("parent", {}).get("content_sha256")
        if parent_content == draft["content_sha256"] and draft.get("parent", {}).get("configuration_id") != "uncalibrated":
            raise ConfigurationManagementError("configuration has no publishable changes")
        snapshot_id = str(uuid.uuid4())
        now = utc_now_text()
        base_checkpoint = self._load_json(
            self._draft_path(draft_id).parent / "checkpoints" / "00000000.json",
            "initial draft checkpoint",
        )
        source_path = self._draft_path(draft_id).parent / "source_candidate.json"
        source_candidate = self._load_json(source_path, "source candidate") if source_path.is_file() else {}
        candidate_runs = _candidate_run_bindings(source_candidate)
        editable = copy.deepcopy(draft["editable"])
        editable["calibration_values"] = publish_calibration(
            draft["editable"]["calibration_values"],
            base_checkpoint["editable"].get("calibration_values", {}),
            manual_run_id=f"manual_{snapshot_id}",
            candidate_runs=candidate_runs,
        )
        content_sha256 = sha256_json(editable)
        snapshot = {
            "schema_version": "0.2",
            "artifact_type": "platform_configuration_snapshot",
            "artifact_version": "0.2",
            "snapshot_id": snapshot_id,
            "state_id": snapshot_id,
            "device_id": draft["device_id"],
            "name": name,
            "reason": reason,
            "actor_id": actor_id,
            "published_utc": now,
            "status": "published",
            "keep": bool(keep),
            "parent": draft["parent"],
            "readonly": draft["readonly"],
            "editable": editable,
            "content_sha256": content_sha256,
            "requires_requalification": validation["requires_requalification"],
            "experiment_eligible": not validation["requires_requalification"],
        }
        errors = validate_document(snapshot)
        if errors:
            raise ConfigurationManagementError(errors[0]["message"])
        directory = self.snapshots_root / snapshot_id
        directory.mkdir(parents=True, exist_ok=False)
        self._atomic_json(directory / "snapshot.json", snapshot)
        if source_candidate:
            self._atomic_json(directory / "source_candidate.json", source_candidate)
        if keep:
            self._pin(snapshot_id, actor_id)
        self._audit(
            "snapshot_published",
            actor_id,
            {"snapshot_id": snapshot_id, "draft_id": draft_id},
        )
        self._prune_automatic_snapshots(draft["device_id"])
        return self.snapshot(snapshot_id)

    def snapshots(self) -> list[dict[str, Any]]:
        if not self.snapshots_root.is_dir():
            return []
        active = {row["snapshot_id"] for row in self.active_configurations()}
        rows = []
        for directory in sorted(self.snapshots_root.iterdir()):
            path = directory / "snapshot.json"
            if not path.is_file():
                continue
            try:
                payload = self._load_json(path, "platform snapshot")
            except Exception:
                continue
            rows.append(
                {
                    "snapshot_id": payload["snapshot_id"],
                    "device_id": payload["device_id"],
                    "name": payload["name"],
                    "status": payload["status"],
                    "published_utc": payload["published_utc"],
                    "actor_id": payload["actor_id"],
                    "content_sha256": payload["content_sha256"],
                    "keep": self._is_pinned(payload["snapshot_id"]),
                    "active": payload["snapshot_id"] in active,
                    "requires_requalification": payload["requires_requalification"],
                    "experiment_eligible": payload["experiment_eligible"],
                    "source_candidate": payload.get("source_candidate"),
                }
            )
        rows.sort(key=lambda row: (row["published_utc"], row["snapshot_id"]), reverse=True)
        return rows

    def snapshot(self, snapshot_id: str) -> dict[str, Any]:
        path = self._snapshot_path(snapshot_id)
        payload = self._load_json(path, "platform snapshot")
        return {
            **payload,
            "keep": self._is_pinned(snapshot_id),
            "active": any(
                row["snapshot_id"] == snapshot_id for row in self.active_configurations()
            ),
        }

    def set_active(
        self,
        snapshot_id: str,
        *,
        actor_id: str,
        confirmation_phrase: str,
    ) -> dict[str, Any]:
        self._actor(actor_id)
        snapshot = self.snapshot(snapshot_id)
        expected = f"SET ACTIVE {snapshot_id}"
        if confirmation_phrase != expected:
            raise ConfigurationManagementError("active confirmation phrase is invalid")
        if not snapshot["experiment_eligible"]:
            raise ConfigurationManagementError(
                "snapshot requires control requalification before it can become Active"
            )
        if not snapshot.get("editable", {}).get("calibration_values"):
            raise ConfigurationManagementError("uninitialized snapshot cannot become Active")
        errors = validate_document(self._load_json(self._snapshot_path(snapshot_id), "platform snapshot"))
        if errors:
            raise ConfigurationManagementError("snapshot is not a valid PlatformConfiguration v0.2", field_errors=errors)
        return self._activate_snapshot(snapshot, actor_id=actor_id)

    def _activate_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        actor_id: str,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "0.2",
            "artifact_type": "platform_configuration_active_pointer",
            "artifact_version": "0.2",
            "device_id": snapshot["device_id"],
            "snapshot_id": snapshot["snapshot_id"],
            "snapshot_content_sha256": snapshot["content_sha256"],
            "actor_id": actor_id,
            "activated_utc": utc_now_text(),
        }
        self._atomic_json(self.active_root / f"{snapshot['device_id']}.json", payload)
        self._audit("snapshot_activated", actor_id, payload)
        return payload

    def set_keep(self, snapshot_id: str, *, actor_id: str, keep: bool) -> dict[str, Any]:
        self._actor(actor_id)
        self.snapshot(snapshot_id)
        if keep:
            self._pin(snapshot_id, actor_id)
        else:
            (self.pins_root / f"{snapshot_id}.json").unlink(missing_ok=True)
        self._audit(
            "snapshot_keep_changed",
            actor_id,
            {"snapshot_id": snapshot_id, "keep": bool(keep)},
        )
        return self.snapshot(snapshot_id)

    def delete_draft(self, draft_id: str, *, actor_id: str) -> None:
        self._actor(actor_id)
        self._delete_managed_directory(
            self.drafts_root,
            draft_id,
            "draft",
            "draft.json",
        )
        self._audit("draft_deleted", actor_id, {"draft_id": draft_id})

    def delete_snapshot(self, snapshot_id: str, *, actor_id: str) -> None:
        self._actor(actor_id)
        payload_path = self._managed_payload_path(
            self.snapshots_root,
            snapshot_id,
            "snapshot",
            "snapshot.json",
        )
        payload = self._load_json(payload_path, "platform snapshot")
        snapshot = {
            **payload,
            "keep": self._is_pinned(snapshot_id),
            "active": any(
                row["snapshot_id"] == snapshot_id
                for row in self.active_configurations()
            ),
        }
        if snapshot["active"]:
            raise ConfigurationManagementError("Active snapshot cannot be deleted")
        if snapshot["keep"]:
            raise ConfigurationManagementError("kept snapshot must be unpinned before deletion")
        if self._snapshot_referenced(snapshot_id):
            raise ConfigurationManagementError("referenced snapshot can only be archived")
        self._delete_managed_directory(
            self.snapshots_root,
            snapshot_id,
            "snapshot",
            "snapshot.json",
        )
        self._audit("snapshot_deleted", actor_id, {"snapshot_id": snapshot_id})

    def active_configurations(self) -> list[dict[str, Any]]:
        if not self.active_root.is_dir():
            return []
        rows = []
        for path in sorted(self.active_root.glob("*.json")):
            try:
                rows.append(self._load_json(path, "active configuration"))
            except Exception:
                continue
        return rows

    def resolve_active_context(self, device_id: str = "demo_2q1c2r") -> Any:
        """Resolve the only execution authority from this store's Active pointer."""

        from sqvm.web.configuration_resolver import PlatformAuthorityResolver

        return PlatformAuthorityResolver(self.repository_root, self.root).resolve(device_id)

    def bootstrap_configuration(self, calibration: Mapping[str, Any]) -> dict[str, Any]:
        control_path = self.repository_root / "configs" / "control" / "2q1c2r_control.yaml"
        try:
            control = yaml.safe_load(control_path.read_text("utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationManagementError(f"cannot load bootstrap control config: {exc}") from exc
        if not isinstance(control, dict):
            raise ConfigurationManagementError("bootstrap control config is invalid")
        control_values = {
            key: copy.deepcopy(control[key])
            for key in (
                "clock",
                "dac",
                "lane_order",
                "lanes",
                "static_mixing",
                "idle_flux_phi0",
                "acceptance",
            )
        }
        control_values["simulation"] = initial_simulation_configuration()
        editable = {
            "control_values": control_values,
            "calibration_values": {},
        }
        device_path = calibration.get("device_snapshot", "configs/devices/2q1c2r.yaml")
        device_ref = self.repository_root / str(device_path)
        device_sha = hashlib.sha256(device_ref.read_bytes()).hexdigest().upper()
        instruction_profile = {"profile_id": "qcis_stage7_calibration_v3", "profile_version": "0.3"}
        compiler = {"compiler_id": "sqvm_qcis_compiler_v3"}
        return {
            "schema_version": "0.2",
            "artifact_type": "platform_configuration_legacy_migration",
            "artifact_version": "0.2",
            "configuration_id": calibration.get("calibration_id") or calibration.get("state_id", "uninitialized"),
            "state_id": calibration.get("state_id"),
            "device_id": "demo_2q1c2r",
            "status": "uninitialized",
            "device_ref": {
                "path": str(device_path).replace("\\", "/"),
                "sha256": device_sha,
            },
            "authority_refs": {
                "device_sha256": device_sha,
                "instruction_profile_sha256": sha256_json(instruction_profile),
                "compiler_snapshot_sha256": sha256_json(compiler),
            },
            "editable": editable,
            "legacy_source_sha256": sha256_json(calibration),
        }

    def _validation_checks(self, draft: Mapping[str, Any]) -> list[dict[str, Any]]:
        editable = draft.get("editable")
        checks = []
        try:
            normalized = self._validate_editable_shape(editable)
            checks.append(self._check("editable_schema", True, "editable sections are valid"))
        except Exception as exc:
            return [self._check("editable_schema", False, str(exc))]
        control = normalized["control_values"]
        required = {
            "clock",
            "dac",
            "lane_order",
            "lanes",
            "static_mixing",
            "idle_flux_phi0",
            "acceptance",
        }
        actual_control_sections = frozenset(control)
        checks.append(
            self._check(
                "control_sections_complete",
                actual_control_sections in {frozenset(required), frozenset(required | {"simulation"})},
                "control sections are exact" if actual_control_sections in {frozenset(required), frozenset(required | {"simulation"})} else "control sections differ from the editable contract",
            )
        )
        clock = control.get("clock")
        clock_ok = (
            isinstance(clock, Mapping)
            and _number(clock.get("sample_rate_Hz"), positive=True)
            and _number(clock.get("dt_ns"), positive=True)
            and math.isclose(
                float(clock["sample_rate_Hz"]) * float(clock["dt_ns"]),
                1e9,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        )
        checks.append(self._check("clock_consistent", clock_ok, "sample rate and dt are reciprocal"))
        dac = control.get("dac")
        dac_ok = (
            isinstance(dac, Mapping)
            and type(dac.get("bits")) is int
            and 1 <= dac["bits"] <= 32
            and _number(dac.get("full_scale_min_V"))
            and _number(dac.get("full_scale_max_exclusive_V"))
            and float(dac["full_scale_min_V"]) < float(dac["full_scale_max_exclusive_V"])
            and dac.get("rounding") == "half_even"
        )
        checks.append(self._check("dac_valid", dac_ok, "DAC resolution, range and rounding are valid"))
        lanes = control.get("lanes")
        lane_order = control.get("lane_order")
        lane_order_ok = (
            isinstance(lane_order, list)
            and bool(lane_order)
            and all(isinstance(value, str) and value for value in lane_order)
            and len(lane_order) == len(set(lane_order))
            and isinstance(lanes, Mapping)
            and set(lane_order) == set(lanes)
        )
        checks.append(self._check("lane_order_valid", lane_order_ok, "lane order exactly covers configured lanes"))
        lanes_ok = lane_order_ok
        if lanes_ok:
            for row in lanes.values():
                if not isinstance(row, Mapping):
                    lanes_ok = False
                    break
                latency = row.get("latency_samples")
                fir = row.get("fir")
                if (
                    type(latency) is not int
                    or latency < 0
                    or not isinstance(fir, list)
                    or not fir
                    or any(not _number(value) for value in fir)
                    or not math.isclose(sum(float(value) for value in fir), 1.0, abs_tol=1e-12)
                ):
                    lanes_ok = False
                    break
        checks.append(self._check("lane_filters_valid", lanes_ok, "lane latency and FIR values are valid"))
        mixing_ok = _static_mixing_valid(control.get("static_mixing"))
        checks.append(self._check("static_mixing_valid", mixing_ok, "static mixing matrices are finite and dimensionally consistent"))
        flux = control.get("idle_flux_phi0")
        flux_ok = (
            isinstance(flux, Mapping)
            and set(flux) == {"q1", "q2", "c"}
            and all(_number(value) for value in flux.values())
        )
        checks.append(self._check("idle_flux_units_valid", flux_ok, "idle flux uses finite Phi/Phi0 values"))
        acceptance_ok = _acceptance_valid(control.get("acceptance"))
        checks.append(self._check("acceptance_valid", acceptance_ok, "acceptance thresholds are complete and positive"))
        simulation_ok = _simulation_configuration_valid(control.get("simulation"))
        checks.append(self._check("simulation_model_valid", simulation_ok, "simulation truncation and convergence dimensions are valid"))
        calibration = normalized["calibration_values"]
        checks.append(
            self._check(
                "calibration_values_valid",
                _calibration_values_valid(calibration),
                "calibration values and reference-frequency authorities are valid",
            )
        )
        checks.append(
            self._check(
                "physical_fields_excluded",
                not _contains_physical_field(normalized),
                "physical device fields are excluded from editable sections",
            )
        )
        checks.append(
            self._check(
                "physical_device_immutable",
                set(draft.get("readonly", {})) == {"device_ref", "authority_refs"},
                "device and authority references are read-only",
            )
        )
        return checks

    def _requires_requalification(self, draft: Mapping[str, Any]) -> bool:
        parent = draft.get("parent")
        if not isinstance(parent, Mapping):
            return True
        return parent.get("control_values_sha256") != sha256_json(
            draft["editable"]["control_values"]
        )

    def _regenerate_system_fields(
        self,
        editable: Mapping[str, Any],
        base_editable: Mapping[str, Any],
        *,
        calibration_run_id: str,
        source_candidate: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = copy.deepcopy(dict(editable))
        qagents = result.get("calibration_values", {}).get("qagents")
        base_qagents = base_editable.get("calibration_values", {}).get("qagents", {})
        if isinstance(qagents, dict):
            for target, value in qagents.items():
                reference = value.get("reference_frequency_authority") if isinstance(value, dict) else None
                if isinstance(reference, dict):
                    base_value = base_qagents.get(target, {}) if isinstance(base_qagents, Mapping) else {}
                    base_reference = (
                        base_value.get("reference_frequency_authority")
                        if isinstance(base_value, Mapping)
                        else None
                    )
                    if _without_system(reference) == _without_system(base_reference):
                        continue
                    revision = reference.get("revision")
                    reference["revision"] = int(revision) + 1 if type(revision) is int else 1
                    reference["calibration_run_id"] = _candidate_run_id(
                        source_candidate,
                        target,
                        reference,
                    ) or calibration_run_id
                    reference.pop("setting_hash", None)
                    reference["setting_hash"] = sha256_json(reference)
        return result

    def _editable_sections(self, base: Mapping[str, Any]) -> dict[str, Any]:
        if base.get("artifact_type") == "platform_configuration_snapshot":
            return _with_simulation_defaults(base["editable"])
        editable = base.get("editable")
        if isinstance(editable, Mapping):
            return _with_simulation_defaults(editable)
        raise ConfigurationManagementError("base configuration has no editable sections")

    def _readonly_sections(self, base: Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(base.get("readonly"), Mapping):
            return copy.deepcopy(dict(base["readonly"]))
        return {
            "device_ref": copy.deepcopy(base.get("device_ref")),
            "authority_refs": copy.deepcopy(base.get("authority_refs", {})),
        }

    def _base_reference(self, base: Mapping[str, Any]) -> dict[str, Any]:
        editable = self._editable_sections(base)
        return {
            "configuration_id": base.get("snapshot_id")
            or base.get("current_id")
            or base.get("configuration_id"),
            "state_id": base.get("state_id"),
            "content_sha256": base.get("content_sha256") or sha256_json(editable),
            "control_values_sha256": sha256_json(editable["control_values"]),
            "calibration_values_sha256": sha256_json(editable["calibration_values"]),
        }

    def _base_device_id(self, base: Mapping[str, Any]) -> str:
        value = base.get("device_id", "demo_2q1c2r")
        if not isinstance(value, str) or _DEVICE_ID.fullmatch(value) is None:
            raise ConfigurationManagementError("base device_id is invalid")
        return value

    def _validate_editable_shape(self, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "control_values",
            "calibration_values",
        }:
            raise ConfigurationManagementError("editable root sections are not exact")
        if not all(isinstance(value[name], Mapping) for name in value):
            raise ConfigurationManagementError("editable sections must be objects")
        normalized = copy.deepcopy(dict(value))
        _finite_tree(normalized)
        return normalized

    def _assert_system_fields_unchanged(
        self,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> None:
        old = _collect_system_fields(before)
        new = _collect_system_fields(after)
        if old != new:
            raise ConfigurationManagementError("system-generated fields cannot be edited")

    def _write_checkpoint(self, payload: Mapping[str, Any]) -> None:
        directory = self._draft_path(payload["draft_id"]).parent / "checkpoints"
        directory.mkdir(exist_ok=True)
        checkpoint = {
            "schema_version": "0.1",
            "draft_id": payload["draft_id"],
            "checkpoint": payload["checkpoint"],
            "updated_utc": payload["updated_utc"],
            "content_sha256": payload["content_sha256"],
            "editable": payload["editable"],
        }
        self._atomic_json(directory / f"{payload['checkpoint']:08d}.json", checkpoint)
        files = sorted(directory.glob("*.json"))
        if len(files) > _MAX_CHECKPOINTS:
            preserved = {files[0], *files[-(_MAX_CHECKPOINTS - 1) :]}
            for path in files:
                if path not in preserved:
                    path.unlink()

    def _prune_automatic_snapshots(self, device_id: str) -> None:
        automatic = [
            row
            for row in self.snapshots()
            if row["device_id"] == device_id
            and not row["keep"]
            and not row["active"]
        ]
        if len(automatic) <= _MAX_AUTOMATIC_SNAPSHOTS:
            return
        candidates = [
            row
            for row in automatic
            if not self._snapshot_referenced(row["snapshot_id"])
        ]
        for row in candidates[_MAX_AUTOMATIC_SNAPSHOTS:]:
            self._delete_managed_directory(
                self.snapshots_root,
                row["snapshot_id"],
                "snapshot",
                "snapshot.json",
            )

    def _snapshot_referenced(self, snapshot_id: str) -> bool:
        output = self.repository_root / "output"
        token = snapshot_id.encode("utf-8")
        target_root = self._snapshot_path(snapshot_id).parent.resolve()
        ignored_roots = {
            self.active_root.resolve(),
            self.pins_root.resolve(),
            self.audit_root.resolve(),
        }
        seen: set[Path] = set()
        roots = [self.current_root, self.drafts_root, self.snapshots_root]
        if output.is_dir():
            roots.append(output)
        for path in (item for root in roots if root.is_dir() for item in root.rglob("*.json")):
            try:
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                if target_root in resolved.parents or any(
                    root == resolved or root in resolved.parents for root in ignored_roots
                ):
                    continue
                if token in path.read_bytes():
                    return True
            except OSError:
                continue
        return False

    def _pin(self, snapshot_id: str, actor_id: str) -> None:
        self._atomic_json(
            self.pins_root / f"{snapshot_id}.json",
            {
                "schema_version": "0.1",
                "snapshot_id": snapshot_id,
                "actor_id": actor_id,
                "pinned_utc": utc_now_text(),
            },
        )

    def _is_pinned(self, snapshot_id: str) -> bool:
        return (self.pins_root / f"{snapshot_id}.json").is_file()

    def _audit(self, event: str, actor_id: str, details: Mapping[str, Any]) -> None:
        event_id = str(uuid.uuid4())
        self._atomic_json(
            self.audit_root / f"{event_id}.json",
            {
                "schema_version": "0.1",
                "event_id": event_id,
                "event": event,
                "actor_id": actor_id,
                "created_utc": utc_now_text(),
                "details": dict(details),
            },
        )

    def _draft_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "draft_id": payload["draft_id"],
            "device_id": payload["device_id"],
            "name": payload["name"],
            "note": payload["note"],
            "updated_utc": payload["updated_utc"],
            "checkpoint": payload["checkpoint"],
            "content_sha256": payload["content_sha256"],
            "validation_status": payload["validation"]["status"],
            "source_candidate": payload.get("source_candidate"),
        }

    def _current_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        qagents = payload.get("editable", {}).get("calibration_values", {}).get(
            "qagents", {}
        )
        frequencies = {
            target: row.get("reference_frequency_authority", {}).get(
                "reference_frequency_GHz"
            )
            for target, row in qagents.items()
            if isinstance(row, Mapping)
            and isinstance(row.get("reference_frequency_authority"), Mapping)
        }
        return {
            "current_id": payload["current_id"],
            "device_id": payload["device_id"],
            "name": payload["name"],
            "note": payload["note"],
            "updated_utc": payload["updated_utc"],
            "revision": payload["revision"],
            "content_sha256": payload["content_sha256"],
            "validation_status": payload["validation"]["status"],
            "requires_requalification": payload["validation"].get(
                "requires_requalification", True
            ),
            "source_snapshot_id": payload.get("source_snapshot_id"),
            "reference_frequencies_GHz": frequencies,
        }

    def _ensure_current_configuration(self, device_id: str) -> None:
        self._device(device_id)
        path = self.current_root / f"{device_id}.json"
        if path.is_file():
            payload = self._load_json(path, "current configuration")
            upgraded = _with_simulation_defaults(payload["editable"])
            if upgraded != payload["editable"]:
                errors = validate_editable(upgraded, published=False)
                payload.update(
                    {
                        "actor_id": "system.migration",
                        "updated_utc": utc_now_text(),
                        "revision": int(payload.get("revision", 0)) + 1,
                        "editable": upgraded,
                        "content_sha256": sha256_json(upgraded),
                        "validation": {
                            "status": "valid" if not errors else "invalid",
                            "field_errors": errors,
                            "requires_requalification": self._requires_requalification(
                                {**payload, "editable": upgraded}
                            ),
                        },
                    }
                )
                self._atomic_json(path, payload)
            return
        base: Mapping[str, Any] | None = None
        active = next(
            (
                row for row in self.active_configurations()
                if row.get("device_id") == device_id
            ),
            None,
        )
        if active:
            try:
                base = self.snapshot(active["snapshot_id"])
            except ConfigurationManagementError:
                base = None
        if base is None:
            newest_snapshot = next(
                (row for row in self.snapshots() if row["device_id"] == device_id),
                None,
            )
            if newest_snapshot:
                base = self.snapshot(newest_snapshot["snapshot_id"])
        if base is None:
            newest_draft = next(
                (row for row in self.drafts() if row["device_id"] == device_id),
                None,
            )
            if newest_draft:
                base = self.draft(newest_draft["draft_id"])
        if base is None:
            return
        editable = self._editable_sections(base)
        editable["calibration_values"] = draftify_calibration(
            editable.get("calibration_values", {})
        )
        errors = validate_editable(editable, published=False)
        now = utc_now_text()
        payload = {
            "schema_version": "0.3",
            "artifact_type": "platform_configuration_current",
            "artifact_version": "0.1",
            "current_id": device_id,
            "device_id": device_id,
            "name": base.get("name") or f"{device_id} 当前配置",
            "note": "由现有配置版本迁移生成",
            "actor_id": "system.bootstrap",
            "created_utc": now,
            "updated_utc": now,
            "revision": 1,
            "source_snapshot_id": base.get("snapshot_id"),
            "parent": self._base_reference(base),
            "readonly": self._readonly_sections(base),
            "editable": editable,
            "content_sha256": sha256_json(editable),
            "validation": {
                "status": "valid" if not errors else "invalid",
                "field_errors": errors,
                "requires_requalification": self._requires_requalification(
                    {"parent": self._base_reference(base), "editable": editable}
                ),
            },
        }
        self._atomic_json(path, payload)

    def _current_path(self, device_id: str) -> Path:
        self._device(device_id)
        path = self.current_root / f"{device_id}.json"
        if not path.is_file():
            raise ConfigurationManagementError("current configuration not found", status=404)
        return path

    def _draft_path(self, draft_id: str) -> Path:
        _uuid(draft_id, "draft_id")
        path = self.drafts_root / draft_id / "draft.json"
        if not path.is_file():
            raise ConfigurationManagementError("draft not found", status=404)
        return path

    def _snapshot_path(self, snapshot_id: str) -> Path:
        _uuid(snapshot_id, "snapshot_id")
        path = self.snapshots_root / snapshot_id / "snapshot.json"
        if not path.is_file():
            raise ConfigurationManagementError("snapshot not found", status=404)
        return path

    def _managed_payload_path(
        self,
        container: Path,
        identifier: str,
        label: str,
        payload_name: str,
    ) -> Path:
        """Return a deletion candidate only after lexical, no-follow validation."""

        _uuid(identifier, f"{label}_id")
        target, _components = self._managed_directory_components(
            container,
            identifier,
            label,
        )
        payload = target / payload_name
        self._regular_lstat(payload, f"{label} payload", not_found=f"{label} not found")
        return payload

    def _delete_managed_directory(
        self,
        container: Path,
        identifier: str,
        label: str,
        payload_name: str,
    ) -> None:
        """Delete a managed tree only after a no-follow inventory and rechecks.

        `shutil.rmtree` is deliberately not used here: its pathname traversal is
        unsuitable for a lifecycle boundary that must reject reparse points and
        detect replacement after validation.
        """

        payload = self._managed_payload_path(container, identifier, label, payload_name)
        target = payload.parent
        _target, components = self._managed_directory_components(
            container,
            identifier,
            label,
        )
        directories, files = self._inventory_deletion_tree(target, label)

        # Delete only regular, single-link files from the inventory.  Every
        # operation revalidates the lexical ancestor chain and the node identity.
        for path in sorted(files, key=lambda item: (len(item.parts), str(item)), reverse=True):
            self._verify_deletion_path(path, target, components, directories, files[path], label)
            try:
                os.unlink(path)
            except OSError as exc:
                raise ConfigurationManagementError(
                    f"cannot delete {label} file: {exc}"
                ) from exc

        for path in sorted(directories, key=lambda item: (len(item.parts), str(item)), reverse=True):
            self._verify_deletion_directory(path, target, components, directories, label)
            try:
                os.rmdir(path)
            except OSError as exc:
                raise ConfigurationManagementError(
                    f"cannot remove {label} directory: {exc}"
                ) from exc

    def _managed_directory_components(
        self,
        container: Path,
        identifier: str,
        label: str,
    ) -> tuple[Path, dict[Path, tuple[int, int, int]]]:
        root = self._lexical_absolute(self.root)
        container_path = self._lexical_absolute(container)
        target = container_path / identifier
        self._lexically_confined(root, container_path, f"{label} container")
        self._lexically_confined(container_path, target, label)
        components: dict[Path, tuple[int, int, int]] = {}
        current = root
        components[current] = self._directory_lstat(current, "configuration storage")
        for part in container_path.relative_to(root).parts:
            current = current / part
            components[current] = self._directory_lstat(current, f"{label} container")
        for part in target.relative_to(container_path).parts:
            current = current / part
            components[current] = self._directory_lstat(current, label, not_found=f"{label} not found")
        return target, components

    def _inventory_deletion_tree(
        self,
        target: Path,
        label: str,
    ) -> tuple[dict[Path, tuple[int, int, int]], dict[Path, tuple[int, int, int]]]:
        directories: dict[Path, tuple[int, int, int]] = {
            target: self._directory_lstat(target, label)
        }
        files: dict[Path, tuple[int, int, int]] = {}

        def visit(directory: Path) -> None:
            expected = directories[directory]
            self._same_directory(directory, expected, label)
            try:
                with os.scandir(directory) as entries:
                    names = sorted(entry.name for entry in entries)
            except OSError as exc:
                raise ConfigurationManagementError(
                    f"cannot inspect {label} directory: {exc}"
                ) from exc
            # Do not trust entries obtained through a directory that might have
            # been replaced while it was being opened.
            self._same_directory(directory, expected, label)
            for name in names:
                path = directory / name
                result = self._no_follow_lstat(path, f"{label} entry")
                mode = stat.S_IFMT(result.st_mode)
                identity = self._identity(result)
                if stat.S_ISDIR(mode):
                    directories[path] = identity
                    visit(path)
                elif stat.S_ISREG(mode):
                    if result.st_nlink != 1:
                        raise ConfigurationManagementError(
                            f"{label} contains a hardlink"
                        )
                    files[path] = identity
                else:
                    raise ConfigurationManagementError(
                        f"{label} contains an unsupported special item"
                    )
            self._same_directory(directory, expected, label)

        visit(target)
        return directories, files

    def _verify_deletion_path(
        self,
        path: Path,
        target: Path,
        components: Mapping[Path, tuple[int, int, int]],
        directories: Mapping[Path, tuple[int, int, int]],
        expected: tuple[int, int, int],
        label: str,
    ) -> None:
        self._same_components(components, label)
        self._same_directory_chain(path.parent, target, directories, label)
        result = self._regular_lstat(path, f"{label} entry")
        if self._identity(result) != expected:
            raise ConfigurationManagementError(f"{label} entry changed during deletion")

    def _verify_deletion_directory(
        self,
        path: Path,
        target: Path,
        components: Mapping[Path, tuple[int, int, int]],
        directories: Mapping[Path, tuple[int, int, int]],
        label: str,
    ) -> None:
        self._same_components(components, label)
        self._same_directory_chain(path, target, directories, label)

    def _same_directory_chain(
        self,
        path: Path,
        target: Path,
        directories: Mapping[Path, tuple[int, int, int]],
        label: str,
    ) -> None:
        current = target
        self._same_directory(current, directories[current], label)
        for part in path.relative_to(target).parts:
            current = current / part
            self._same_directory(current, directories[current], label)

    def _same_components(
        self,
        components: Mapping[Path, tuple[int, int, int]],
        label: str,
    ) -> None:
        for path, expected in components.items():
            self._same_directory(path, expected, label)

    def _same_directory(
        self,
        path: Path,
        expected: tuple[int, int, int],
        label: str,
    ) -> None:
        if self._directory_lstat(path, label) != expected:
            raise ConfigurationManagementError(f"{label} directory changed during deletion")

    def _directory_lstat(
        self,
        path: Path,
        label: str,
        *,
        not_found: str | None = None,
    ) -> tuple[int, int, int]:
        result = self._no_follow_lstat(path, label, not_found=not_found)
        if not stat.S_ISDIR(result.st_mode):
            raise ConfigurationManagementError(f"{label} is not a directory")
        return self._identity(result)

    def _regular_lstat(
        self,
        path: Path,
        label: str,
        *,
        not_found: str | None = None,
    ) -> os.stat_result:
        result = self._no_follow_lstat(path, label, not_found=not_found)
        if not stat.S_ISREG(result.st_mode):
            raise ConfigurationManagementError(f"{label} is not a regular file")
        if result.st_nlink != 1:
            raise ConfigurationManagementError(f"{label} is a hardlink")
        return result

    def _no_follow_lstat(
        self,
        path: Path,
        label: str,
        *,
        not_found: str | None = None,
    ) -> os.stat_result:
        try:
            result = os.lstat(path)
        except FileNotFoundError as exc:
            if not_found:
                raise ConfigurationManagementError(not_found, status=404) from exc
            raise ConfigurationManagementError(f"{label} is missing") from exc
        except OSError as exc:
            raise ConfigurationManagementError(f"cannot inspect {label}: {exc}") from exc
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        attributes = getattr(result, "st_file_attributes", 0)
        if stat.S_ISLNK(result.st_mode) or attributes & reparse_flag:
            raise ConfigurationManagementError(f"{label} is linked or a reparse point")
        return result

    @staticmethod
    def _identity(result: os.stat_result) -> tuple[int, int, int]:
        return (result.st_dev, result.st_ino, stat.S_IFMT(result.st_mode))

    @staticmethod
    def _lexical_absolute(path: Path) -> Path:
        return Path(os.path.abspath(os.fspath(path)))

    @staticmethod
    def _lexically_confined(root: Path, path: Path, label: str) -> None:
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ConfigurationManagementError(f"{label} is outside configuration storage") from exc

    def _device(self, value: str) -> str:
        if not isinstance(value, str) or _DEVICE_ID.fullmatch(value) is None:
            raise ConfigurationManagementError("device_id is invalid")
        return value

    def _actor(self, value: str) -> str:
        if not isinstance(value, str) or _ACTOR_ID.fullmatch(value) is None:
            raise ConfigurationManagementError("actor_id is invalid")
        return value

    def _text(self, value: str, label: str, minimum: int, maximum: int) -> str:
        if (
            not isinstance(value, str)
            or not minimum <= len(value) <= maximum
            or "\r" in value
            or "\n" in value
        ):
            raise ConfigurationManagementError(f"{label} is invalid")
        return value

    def _inside(self, value: str | Path, label: str) -> Path:
        path = Path(value)
        path = (self.repository_root / path).resolve() if not path.is_absolute() else path.resolve()
        try:
            path.relative_to(self.repository_root)
        except ValueError as exc:
            raise ConfigurationManagementError(f"{label} is outside repository") from exc
        return path

    def _load_json(self, path: Path, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text("utf-8"), parse_constant=_reject_constant)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ConfigurationManagementError(f"cannot load {label}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConfigurationManagementError(f"{label} must be an object")
        _finite_tree(payload)
        return payload

    def _atomic_json(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = canonical_json_bytes(payload)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _replace_configuration_file(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "message": message}


def _replace_configuration_file(
    source: Path,
    destination: Path,
    *,
    replacer: Callable[[Path, Path], None] | None = None,
    platform_name: str | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> None:
    """Replace one configuration file across transient Windows sharing locks."""

    replacer = replacer or os.replace
    platform_name = platform_name or os.name
    sleeper = sleeper or time.sleep
    delay_s = 0.01
    for attempt in range(_CONFIGURATION_REPLACE_ATTEMPTS):
        try:
            replacer(source, destination)
            return
        except OSError as exc:
            error = getattr(exc, "winerror", None) or exc.errno
            retryable = (
                platform_name == "nt"
                and error in _WINDOWS_TRANSIENT_REPLACE_ERRORS
                and attempt < _CONFIGURATION_REPLACE_ATTEMPTS - 1
            )
            if not retryable:
                raise
            sleeper(delay_s)
            delay_s = min(delay_s * 2.0, 0.1)


def _collect_system_fields(value: Any, path: str = "$") -> dict[str, Any]:
    found = {}
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in _SYSTEM_FIELDS:
                found[child] = item
            found.update(_collect_system_fields(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(_collect_system_fields(item, f"{path}[{index}]"))
    return found


def _without_system(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _without_system(item)
            for key, item in value.items()
            if key not in _SYSTEM_FIELDS
        }
    if isinstance(value, list):
        return [_without_system(item) for item in value]
    return value


_DIFF_MISSING = object()


def _without_diff_excluded_fields(value: Any) -> Any:
    """Keep the workbench diff focused on editable calibration values."""

    if isinstance(value, Mapping):
        return {
            key: _without_diff_excluded_fields(item)
            for key, item in value.items()
            if key not in _DIFF_EXCLUDED_FIELDS
        }
    if isinstance(value, list):
        return [_without_diff_excluded_fields(item) for item in value]
    return copy.deepcopy(value)


def _collect_editable_changes(
    before: Any,
    after: Any,
    path: str,
    changes: list[dict[str, Any]],
) -> None:
    if before is _DIFF_MISSING and isinstance(after, Mapping):
        if not after:
            changes.append({"path": path, "before": None, "after": {}, "kind": "added"})
        for key in sorted(after):
            _collect_editable_changes(
                _DIFF_MISSING, after[key], f"{path}.{key}", changes
            )
        return
    if after is _DIFF_MISSING and isinstance(before, Mapping):
        if not before:
            changes.append({"path": path, "before": {}, "after": None, "kind": "removed"})
        for key in sorted(before):
            _collect_editable_changes(
                before[key], _DIFF_MISSING, f"{path}.{key}", changes
            )
        return
    if before is _DIFF_MISSING and isinstance(after, list):
        if not after:
            changes.append({"path": path, "before": None, "after": [], "kind": "added"})
        for index, item in enumerate(after):
            _collect_editable_changes(_DIFF_MISSING, item, f"{path}[{index}]", changes)
        return
    if after is _DIFF_MISSING and isinstance(before, list):
        if not before:
            changes.append({"path": path, "before": [], "after": None, "kind": "removed"})
        for index, item in enumerate(before):
            _collect_editable_changes(item, _DIFF_MISSING, f"{path}[{index}]", changes)
        return
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        for key in sorted(set(before) | set(after)):
            _collect_editable_changes(
                before.get(key, _DIFF_MISSING),
                after.get(key, _DIFF_MISSING),
                f"{path}.{key}",
                changes,
            )
        return
    if isinstance(before, list) and isinstance(after, list):
        for index in range(max(len(before), len(after))):
            _collect_editable_changes(
                before[index] if index < len(before) else _DIFF_MISSING,
                after[index] if index < len(after) else _DIFF_MISSING,
                f"{path}[{index}]",
                changes,
            )
        return
    if before is _DIFF_MISSING:
        changes.append(
            {"path": path, "before": None, "after": copy.deepcopy(after), "kind": "added"}
        )
    elif after is _DIFF_MISSING:
        changes.append(
            {"path": path, "before": copy.deepcopy(before), "after": None, "kind": "removed"}
        )
    elif before != after:
        changes.append(
            {
                "path": path,
                "before": copy.deepcopy(before),
                "after": copy.deepcopy(after),
                "kind": "changed",
            }
        )


def _change_group(path: str, before: Any, after: Any) -> str:
    if path == "$.control_values" or path.startswith("$.control_values."):
        return "control"
    for target in ("Q1", "Q2", "C"):
        for root in (
            f"$.calibration_values.qagents.{target}",
            f"$.calibration_values.gate_configuration.{target}",
        ):
            if path == root or path.startswith(f"{root}."):
                return target
    prefix = "$.calibration_values.waveform_registry."
    if path.startswith(prefix):
        remainder = path[len(prefix) :].split(".", 2)
        if len(remainder) >= 2 and remainder[0] in {"settings", "mappers"}:
            target = _registry_record_target(before, remainder[0], remainder[1])
            if target is None:
                target = _registry_record_target(after, remainder[0], remainder[1])
            if target in {"Q1", "Q2", "C"}:
                return target
    return "other"


def _registry_record_target(editable: Any, collection: str, record_id: str) -> str | None:
    if not isinstance(editable, Mapping):
        return None
    calibration = editable.get("calibration_values")
    registry = calibration.get("waveform_registry") if isinstance(calibration, Mapping) else None
    records = registry.get(collection) if isinstance(registry, Mapping) else None
    record = records.get(record_id) if isinstance(records, Mapping) else None
    target = record.get("target") if isinstance(record, Mapping) else None
    return target if isinstance(target, str) else None


def _calibration_values_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    qagents = value.get("qagents")
    if qagents is None:
        return True
    if not isinstance(qagents, Mapping):
        return False
    required = {
        "reference_frequency_GHz",
        "frequency_source",
        "calibration_run_id",
        "revision",
        "setting_hash",
    }
    for record in qagents.values():
        if not isinstance(record, Mapping):
            return False
        reference = record.get("reference_frequency_authority")
        if reference is None:
            continue
        if not isinstance(reference, Mapping) or set(reference) != required:
            return False
        frequency = reference.get("reference_frequency_GHz")
        revision = reference.get("revision")
        if (
            not _number(frequency, positive=True)
            or reference.get("frequency_source")
            not in {"bootstrap_seed", "accepted_simulation"}
            or not isinstance(reference.get("calibration_run_id"), str)
            or not reference["calibration_run_id"]
            or type(revision) is not int
            or revision < 0
        ):
            return False
        setting_hash = reference.get("setting_hash")
        if not isinstance(setting_hash, str) or re.fullmatch(
            r"[0-9A-Fa-f]{64}", setting_hash
        ) is None:
            return False
    return True


def _static_mixing_valid(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {"xy", "z", "readout"}:
        return False
    for section in value.values():
        if not isinstance(section, Mapping) or set(section) != {
            "input_lanes",
            "output_coordinates",
            "matrix",
        }:
            return False
        inputs = section["input_lanes"]
        outputs = section["output_coordinates"]
        matrix = section["matrix"]
        if (
            not isinstance(inputs, list)
            or not inputs
            or not all(isinstance(item, str) and item for item in inputs)
            or not isinstance(outputs, list)
            or not outputs
            or not all(isinstance(item, str) and item for item in outputs)
            or not isinstance(matrix, list)
            or len(matrix) != len(outputs)
        ):
            return False
        if any(
            not isinstance(row, list)
            or len(row) != len(inputs)
            or any(not _number(item) for item in row)
            for row in matrix
        ):
            return False
    return True


def _with_simulation_defaults(editable: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(editable))
    control = result.get("control_values")
    if isinstance(control, dict) and "simulation" not in control:
        control["simulation"] = initial_simulation_configuration()
    return result


def _simulation_configuration_valid(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping) or set(value) != {"calibration_model"}:
        return False
    model = value.get("calibration_model")
    fields = {
        "charge_cutoffs",
        "retained_energy_levels",
        "convergence_charge_cutoffs",
        "convergence_retained_energy_levels",
    }
    if not isinstance(model, Mapping) or set(model) != fields:
        return False
    modes = {"q1", "c", "q2"}
    if any(
        not isinstance(model[field], Mapping)
        or set(model[field]) != modes
        or any(type(item) is not int or not 1 <= item <= 16 for item in model[field].values())
        for field in fields
    ):
        return False
    baseline_cutoff = model["charge_cutoffs"]
    baseline_levels = model["retained_energy_levels"]
    comparison_cutoff = model["convergence_charge_cutoffs"]
    comparison_levels = model["convergence_retained_energy_levels"]
    if any(
        baseline_levels[mode] > 2 * baseline_cutoff[mode] + 1
        or comparison_cutoff[mode] < baseline_cutoff[mode]
        or comparison_levels[mode] < baseline_levels[mode]
        or comparison_levels[mode] > 2 * comparison_cutoff[mode] + 1
        for mode in modes
    ):
        return False
    if math.prod(baseline_levels.values()) > 512 or math.prod(comparison_levels.values()) > 512:
        return False
    return baseline_cutoff != comparison_cutoff or baseline_levels != comparison_levels


def _acceptance_valid(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == _ACCEPTANCE_FIELDS
        and all(_number(item, positive=True) for item in value.values())
        and type(value.get("max_formal_samples_per_scenario")) is int
    )


def _candidate_run_id(
    source: Mapping[str, Any] | None,
    target: str,
    reference: Mapping[str, Any],
) -> str | None:
    if not isinstance(source, Mapping):
        return None
    run_id = source.get("experiment_run_id")
    candidates = source.get("candidates")
    if isinstance(run_id, str) and run_id and isinstance(candidates, list):
        if any(
            isinstance(candidate, Mapping)
            and target
            in candidate.get("configuration_targets", [candidate.get("target")])
            for candidate in candidates
        ):
            return run_id
        return None
    values = source.get("candidate_values_GHz")
    expected = values.get(target) if isinstance(values, Mapping) else None
    actual = reference.get("reference_frequency_GHz")
    if (
        isinstance(run_id, str)
        and run_id
        and _number(expected, positive=True)
        and _number(actual, positive=True)
        and math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-12)
    ):
        return run_id
    return None


def _candidate_run_bindings(
    source: Mapping[str, Any] | None,
) -> dict[str, str]:
    if not isinstance(source, Mapping):
        return {}
    run_id = source.get("experiment_run_id")
    if not isinstance(run_id, str) or not run_id:
        return {}
    candidates = source.get("candidates")
    if isinstance(candidates, list):
        bindings = {}
        kinds = {
            "qagent_calibration": "qagent",
            "waveform_setting": "setting",
            "waveform_mapper": "mapper",
        }
        for candidate in candidates:
            resources = (
                candidate.get("configuration_resources")
                if isinstance(candidate, Mapping)
                else None
            )
            if not isinstance(resources, list):
                target = candidate.get("target") if isinstance(candidate, Mapping) else None
                if isinstance(target, str):
                    bindings[target] = run_id
                continue
            for resource in resources:
                if not isinstance(resource, Mapping):
                    continue
                prefix = kinds.get(resource.get("resource_type"))
                resource_id = resource.get("resource_id")
                if prefix is not None and isinstance(resource_id, str):
                    bindings[f"{prefix}:{resource_id}"] = run_id
        return bindings
    return {
        target: run_id
        for target in source.get("targets", [])
        if isinstance(target, str)
    }


def _retained_candidate_source(
    source: Mapping[str, Any] | None,
    editable: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Keep provenance only for candidate groups still equal to their applied values."""

    if not isinstance(source, Mapping):
        return None
    candidates = source.get("candidates")
    if isinstance(candidates, list):
        retained = []
        for candidate in candidates:
            changes = candidate.get("changes") if isinstance(candidate, Mapping) else None
            if not isinstance(changes, list) or not changes:
                continue
            try:
                matches = all(
                    candidate_values_equal(
                        value_at_parameter_path(editable, change.get("parameter_path")),
                        change.get("proposed_value"),
                    )
                    for change in changes
                    if isinstance(change, Mapping)
                ) and len(changes) == sum(isinstance(change, Mapping) for change in changes)
            except CalibrationCandidateProtocolError:
                matches = False
            if matches:
                retained.append(copy.deepcopy(dict(candidate)))
        if not retained:
            return None
        result = copy.deepcopy(dict(source))
        result["candidates"] = retained
        result["candidate_ids"] = [row["candidate_id"] for row in retained]
        result["calibration_subjects"] = list(
            dict.fromkeys(
                subject
                for row in retained
                for subject in row.get("calibration_subjects", [row.get("target")])
                if isinstance(subject, str)
            )
        )
        result["configuration_targets"] = list(
            dict.fromkeys(
                target
                for row in retained
                for target in row.get("configuration_targets", [row.get("target")])
                if isinstance(target, str)
            )
        )
        result["targets"] = result["configuration_targets"]
        frequency_values = _frequency_candidate_values(retained)
        if frequency_values:
            result["candidate_values_GHz"] = frequency_values
        else:
            result.pop("candidate_values_GHz", None)
        return result
    targets = source.get("targets")
    values = source.get("candidate_values_GHz")
    qagents = editable.get("calibration_values", {}).get("qagents", {})
    if not isinstance(targets, list) or not isinstance(values, Mapping) or not isinstance(qagents, Mapping):
        return None
    retained = []
    for target in targets:
        row = qagents.get(target) if isinstance(target, str) else None
        reference = row.get("reference_frequency_authority") if isinstance(row, Mapping) else None
        expected = values.get(target) if isinstance(target, str) else None
        actual = reference.get("reference_frequency_GHz") if isinstance(reference, Mapping) else None
        if (
            isinstance(target, str)
            and _number(expected, positive=True)
            and _number(actual, positive=True)
            and math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=1e-12)
        ):
            retained.append(target)
    if not retained:
        return None
    result = copy.deepcopy(dict(source))
    result["targets"] = retained
    result["candidate_values_GHz"] = {
        target: float(values[target]) for target in retained
    }
    return result


def _apply_candidate_groups(
    editable: dict[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    try:
        normalized = [
            normalize_calibration_candidate(_with_legacy_current_value(editable, row))
            for row in candidates
        ]
    except CalibrationCandidateProtocolError as exc:
        raise ConfigurationManagementError(str(exc)) from exc
    candidate_ids = [row["candidate_id"] for row in normalized]
    paths = [
        change["parameter_path"]
        for candidate in normalized
        for change in candidate["changes"]
    ]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ConfigurationManagementError("candidate_ids must be unique")
    if len(paths) != len(set(paths)):
        raise ConfigurationManagementError("candidate parameter paths must be unique")
    if any(row.get("recommendation_eligible") is not True for row in normalized):
        raise ConfigurationManagementError("candidate is not eligible")
    try:
        for candidate in normalized:
            for change in candidate["changes"]:
                _validate_change_resource(editable, change)
                set_parameter_value(editable, change)
                _apply_parameter_metadata(editable, change["parameter_path"])
    except CalibrationCandidateProtocolError as exc:
        status = 409 if "stale" in str(exc) else 422
        raise ConfigurationManagementError(str(exc), status=status) from exc
    return normalized


def _with_legacy_current_value(
    editable: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    if (
        candidate.get("changes") is not None
        or "current_frequency_GHz" in candidate
        or "proposed_frequency_GHz" not in candidate
        or not isinstance(candidate.get("target"), str)
    ):
        return candidate
    target = candidate["target"]
    path = (
        "calibration_values.qagents."
        f"{target}.reference_frequency_authority.reference_frequency_GHz"
    )
    enriched = dict(candidate)
    enriched["current_frequency_GHz"] = value_at_parameter_path(editable, path)
    return enriched


def _rebase_candidates_to_initialized_calibration(
    editable: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    rebased = []
    for candidate in candidates:
        row = copy.deepcopy(dict(candidate))
        changes = row.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict) and isinstance(change.get("parameter_path"), str):
                    change["current_value"] = copy.deepcopy(
                        value_at_parameter_path(editable, change["parameter_path"])
                    )
        elif isinstance(row.get("target"), str) and "proposed_frequency_GHz" in row:
            path = (
                "calibration_values.qagents."
                f"{row['target']}.reference_frequency_authority.reference_frequency_GHz"
            )
            row["current_frequency_GHz"] = value_at_parameter_path(editable, path)
        rebased.append(row)
    return rebased


def _apply_parameter_metadata(editable: dict[str, Any], parameter_path: str) -> None:
    suffix = ".reference_frequency_authority.reference_frequency_GHz"
    if not parameter_path.endswith(suffix):
        return
    parent_path = parameter_path.removesuffix(".reference_frequency_GHz")
    current: Any = editable
    for part in parent_path.split("."):
        current = current[part]
    if isinstance(current, dict):
        current["frequency_source"] = "accepted_simulation"


def _candidate_source(
    experiment_run_id: str,
    recommendation_id: str,
    candidates: Sequence[Mapping[str, Any]],
    editable: Mapping[str, Any],
) -> dict[str, Any]:
    rows = []
    for candidate in candidates:
        changes = [
            {
                "parameter_path": change["parameter_path"],
                "proposed_value": copy.deepcopy(change["proposed_value"]),
                "unit": change.get("unit"),
                "configuration_resource": copy.deepcopy(
                    _resource_for_parameter_path(
                        editable, change["parameter_path"]
                    )
                    or change["configuration_resource"]
                ),
            }
            for change in candidate["changes"]
        ]
        configuration_targets = list(
            dict.fromkeys(
                change["configuration_resource"]["owner"] for change in changes
            )
        )
        rows.append({
            "candidate_id": candidate["candidate_id"],
            "candidate_type": candidate["candidate_type"],
            "calibration_subjects": list(candidate["calibration_subjects"]),
            "configuration_resources": copy.deepcopy(
                candidate["configuration_resources"]
            ),
            "configuration_targets": configuration_targets,
            "target": candidate["target"],
            "changes": changes,
        })
    calibration_subjects = list(
        dict.fromkeys(
            subject for row in rows for subject in row["calibration_subjects"]
        )
    )
    configuration_targets = list(
        dict.fromkeys(
            target for row in rows for target in row["configuration_targets"]
        )
    )
    result = {
        "experiment_run_id": experiment_run_id,
        "recommendation_id": recommendation_id,
        "candidate_ids": [row["candidate_id"] for row in rows],
        "calibration_subjects": calibration_subjects,
        "configuration_targets": configuration_targets,
        "targets": configuration_targets,
        "candidates": rows,
    }
    frequency_values = _frequency_candidate_values(rows)
    if frequency_values:
        result["candidate_values_GHz"] = frequency_values
    return result


def _validate_change_resource(
    editable: Mapping[str, Any],
    change: Mapping[str, Any],
) -> None:
    expected = _resource_for_parameter_path(editable, change["parameter_path"])
    if expected is not None and change.get("configuration_resource") != expected:
        raise CalibrationCandidateProtocolError(
            f"candidate configuration resource does not own {change['parameter_path']}"
        )


def _resource_for_parameter_path(
    editable: Mapping[str, Any],
    parameter_path: str,
) -> dict[str, str] | None:
    parts = parameter_path.split(".")
    if len(parts) >= 4 and parts[:2] == ["calibration_values", "qagents"]:
        return {
            "owner": parts[2],
            "resource_type": "qagent_calibration",
            "resource_id": parts[2],
        }
    if len(parts) >= 5 and parts[:3] in (
        ["calibration_values", "waveform_registry", "settings"],
        ["calibration_values", "waveform_registry", "mappers"],
    ):
        registry = editable.get("calibration_values", {}).get(
            "waveform_registry", {}
        )
        section = registry.get(parts[2], {}) if isinstance(registry, Mapping) else {}
        record = section.get(parts[3]) if isinstance(section, Mapping) else None
        owner = record.get("target") if isinstance(record, Mapping) else None
        if not isinstance(owner, str):
            return None
        return {
            "owner": owner,
            "resource_type": (
                "waveform_setting" if parts[2] == "settings" else "waveform_mapper"
            ),
            "resource_id": parts[3],
        }
    if len(parts) >= 3 and parts[:2] == ["calibration_values", "gate_configuration"]:
        return {
            "owner": parts[2],
            "resource_type": "gate_configuration",
            "resource_id": parts[2],
        }
    return None


def _frequency_candidate_values(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    suffix = ".reference_frequency_authority.reference_frequency_GHz"
    values = {}
    for candidate in candidates:
        target = candidate.get("target")
        for change in candidate.get("changes", []):
            if (
                isinstance(target, str)
                and isinstance(change, Mapping)
                and str(change.get("parameter_path", "")).endswith(suffix)
                and _number(change.get("proposed_value"), positive=True)
            ):
                values[target] = float(change["proposed_value"])
    return values


def _contains_physical_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in _PHYSICAL_FIELD_NAMES or _contains_physical_field(item):
                return True
    elif isinstance(value, list):
        return any(_contains_physical_field(item) for item in value)
    return False


def _finite_tree(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ConfigurationManagementError("configuration contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _finite_tree(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite_tree(item)
        return
    raise ConfigurationManagementError("configuration contains an unsupported value")


def _number(value: Any, *, positive: bool = False) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and (not positive or float(value) > 0.0)
    )


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _uuid(value: str, label: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigurationManagementError(f"{label} is invalid") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ConfigurationManagementError(f"{label} is invalid")
    return value
