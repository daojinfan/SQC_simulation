"""Single-user draft and platform-configuration snapshot management."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
from typing import Any, Mapping, Sequence
import uuid

import yaml

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json
from sqvm.runtime.journal import utc_now_text
from sqvm.web.configuration_schema import (
    draftify_calibration,
    editor_view,
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
_MAX_CHECKPOINTS = 20
_MAX_AUTOMATIC_SNAPSHOTS = 10
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
        self.active_root = self.root / "active"
        self.pins_root = self.root / "pins"
        self.audit_root = self.root / "audit"

    def summary(self) -> dict[str, Any]:
        drafts = self.drafts()
        snapshots = self.snapshots()
        active = self.active_configurations()
        return {
            "schema_version": "0.2",
            "checkpoint_limit": _MAX_CHECKPOINTS,
            "automatic_snapshot_limit": _MAX_AUTOMATIC_SNAPSHOTS,
            "drafts": drafts,
            "snapshots": snapshots,
            "active": active,
        }

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
        calibration = editable.setdefault("calibration_values", {})
        qagents = calibration.setdefault("qagents", {})
        if not isinstance(qagents, dict):
            raise ConfigurationManagementError("draft qagent calibration values are invalid")
        applied = []
        for candidate in candidates:
            target = candidate.get("target")
            frequency = candidate.get("proposed_frequency_GHz")
            if (
                not isinstance(target, str)
                or not _number(frequency, positive=True)
                or candidate.get("recommendation_eligible") is not True
            ):
                raise ConfigurationManagementError("candidate is not eligible")
            existing = qagents.setdefault(target, {}).get("reference_frequency_authority", {})
            revision = (existing.get("base_revision") if isinstance(existing, Mapping) and type(existing.get("base_revision")) is int else existing.get("revision") if isinstance(existing, Mapping) else None)
            reference = {
                "reference_frequency_GHz": float(frequency),
                "frequency_source": "accepted_simulation",
                "status": "draft",
                "base_revision": revision if type(revision) is int else 0,
                "base_setting_hash": existing.get("base_setting_hash") if isinstance(existing, Mapping) and isinstance(existing.get("base_setting_hash"), str) else existing.get("setting_hash") if isinstance(existing, Mapping) and isinstance(existing.get("setting_hash"), str) else None,
                "calibration_run_id": None,
                "revision": None,
                "setting_hash": None,
            }
            qagents[target]["reference_frequency_authority"] = reference
            applied.append(target)
        source_candidate = {
            "experiment_run_id": experiment_run_id,
            "recommendation_id": recommendation_id,
            "targets": applied,
            "candidate_values_GHz": {
                row["target"]: float(row["proposed_frequency_GHz"])
                for row in candidates
            },
        }
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
            {"draft_id": draft_id, "experiment_run_id": experiment_run_id, "targets": applied},
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
        candidate_runs = {target: str(source_candidate["experiment_run_id"]) for target in source_candidate.get("targets", [])} if isinstance(source_candidate, Mapping) and isinstance(source_candidate.get("experiment_run_id"), str) else {}
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
        payload = {
            "schema_version": "0.2",
            "artifact_type": "platform_configuration_active_pointer",
            "artifact_version": "0.2",
            "device_id": snapshot["device_id"],
            "snapshot_id": snapshot_id,
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
        path = self._draft_path(draft_id).parent
        shutil.rmtree(path)
        self._audit("draft_deleted", actor_id, {"draft_id": draft_id})

    def delete_snapshot(self, snapshot_id: str, *, actor_id: str) -> None:
        self._actor(actor_id)
        snapshot = self.snapshot(snapshot_id)
        if snapshot["active"]:
            raise ConfigurationManagementError("Active snapshot cannot be deleted")
        if snapshot["keep"]:
            raise ConfigurationManagementError("kept snapshot must be unpinned before deletion")
        if self._snapshot_referenced(snapshot_id):
            raise ConfigurationManagementError("referenced snapshot can only be archived")
        shutil.rmtree(self._snapshot_path(snapshot_id).parent)
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
        checks.append(
            self._check(
                "control_sections_complete",
                set(control) == required,
                "control sections are exact" if set(control) == required else "control sections differ from the editable contract",
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
            return copy.deepcopy(dict(base["editable"]))
        editable = base.get("editable")
        if isinstance(editable, Mapping):
            return copy.deepcopy(dict(editable))
        raise ConfigurationManagementError("base configuration has no editable sections")

    def _readonly_sections(self, base: Mapping[str, Any]) -> dict[str, Any]:
        if base.get("artifact_type") == "platform_configuration_snapshot":
            return copy.deepcopy(dict(base["readonly"]))
        return {
            "device_ref": copy.deepcopy(base.get("device_ref")),
            "authority_refs": copy.deepcopy(base.get("authority_refs", {})),
        }

    def _base_reference(self, base: Mapping[str, Any]) -> dict[str, Any]:
        editable = self._editable_sections(base)
        return {
            "configuration_id": base.get("snapshot_id") or base.get("configuration_id"),
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
        candidates = [
            row
            for row in self.snapshots()
            if row["device_id"] == device_id
            and not row["keep"]
            and not row["active"]
            and not self._snapshot_referenced(row["snapshot_id"])
        ]
        for row in candidates[_MAX_AUTOMATIC_SNAPSHOTS:]:
            shutil.rmtree(self._snapshot_path(row["snapshot_id"]).parent)

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
        roots = [self.drafts_root, self.snapshots_root]
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
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    @staticmethod
    def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "message": message}


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
