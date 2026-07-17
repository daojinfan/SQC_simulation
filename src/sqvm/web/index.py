"""Filesystem-backed read models for the calibration Web console."""

from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

from sqvm.experiments import verify_qubit_spectroscopy_calibration


SPECTROSCOPY_WORKFLOW_ID = "qubit_spectroscopy_calibration_v1"


class WebArtifactError(ValueError):
    def __init__(self, message: str, *, status: int = 422) -> None:
        self.status = status
        super().__init__(message)


class CalibrationWebIndex:
    """Discover immutable calibration artifacts without treating an index as authority."""

    def __init__(
        self,
        repository_root: str | Path,
        output_root: str | Path | None = None,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.output_root = (
            self._inside(output_root, "output root")
            if output_root is not None
            else self.repository_root / "output"
        )

    def health(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1",
            "status": "ok",
            "capability": "configuration_management_and_result_viewer",
            "configuration_mutations_enabled": True,
            "experiment_execution_enabled": False,
        }

    def overview(self) -> dict[str, Any]:
        configurations = self.configurations()
        experiments = self.experiments()
        decisions = self._decisions()
        return {
            "schema_version": "0.1",
            "configurations": {
                "total": len(configurations),
                "accepted": sum(row["accepted"] is True for row in configurations),
            },
            "experiments": {
                "total": len(experiments),
                "eligible": sum(row["recommendation_eligible"] is True for row in experiments),
                "invalid": sum(row["verification_status"] == "invalid" for row in experiments),
            },
            "decisions": {
                "accepted": sum(row["decision"] == "accept" for row in decisions),
                "rejected": sum(row["decision"] == "reject" for row in decisions),
            },
            "latest_experiments": experiments[:5],
        }

    def configurations(self) -> list[dict[str, Any]]:
        paths = []
        config_root = self.repository_root / "configs" / "calibration"
        if config_root.is_dir():
            paths.extend(path for path in config_root.glob("*.json") if path.is_file())
        if self.output_root.is_dir():
            paths.extend(
                self._walk_named(self.output_root, {"calibration.json", "snapshot.json"})
            )
        rows = [self._configuration_summary(path) for path in sorted(set(paths))]
        counts = Counter(row["configuration_id"] for row in rows)
        for row in rows:
            if counts[row["configuration_id"]] > 1:
                row["verification_status"] = "invalid"
                row["error"] = "duplicate configuration_id"
        rows.sort(
            key=lambda row: (
                row["accepted"] is not True,
                row["status"],
                row["configuration_id"],
            )
        )
        return rows

    def configuration(self, configuration_id: str) -> dict[str, Any]:
        matches = [
            row for row in self.configurations() if row["configuration_id"] == configuration_id
        ]
        if not matches:
            raise WebArtifactError("configuration not found", status=404)
        if len(matches) != 1 or matches[0]["verification_status"] == "invalid":
            raise WebArtifactError("configuration identity is invalid")
        path = self.repository_root / matches[0]["relative_path"]
        payload = self._read_json(path, "configuration")
        if payload.get("artifact_type") == "platform_configuration_snapshot":
            return {
                **matches[0],
                "reference_frequencies": _platform_frequencies(payload),
                "values": payload.get("editable", {}).get("calibration_values", {}),
                "control_values": payload.get("editable", {}).get("control_values", {}),
                "authority_sha256s": payload.get("readonly", {}).get("authority_refs", {}),
                "lineage": payload.get("parent", {}),
                "raw": payload,
            }
        values = payload.get("values", {})
        qagents = values.get("qagents", {}) if isinstance(values, Mapping) else {}
        frequencies = []
        if isinstance(qagents, Mapping):
            for target, value in sorted(qagents.items()):
                reference = (
                    value.get("reference_frequency_authority")
                    if isinstance(value, Mapping)
                    else None
                )
                if isinstance(reference, Mapping):
                    frequencies.append(
                        {
                            "target": target,
                            "reference_frequency_GHz": reference.get("reference_frequency_GHz"),
                            "frequency_source": reference.get("frequency_source"),
                            "revision": reference.get("revision"),
                            "setting_hash": reference.get("setting_hash"),
                        }
                    )
        return {
            **matches[0],
            "reference_frequencies": frequencies,
            "values": values,
            "authority_sha256s": payload.get("authority_sha256s", {}),
            "lineage": {
                "parent_calibration_sha256": payload.get("parent_calibration_sha256"),
                "recommendation_id": payload.get("recommendation_id"),
                "recommendation_sha256": payload.get("recommendation_sha256"),
                "decision_id": payload.get("decision_id"),
                "decision_sha256": payload.get("decision_sha256"),
            },
            "raw": payload,
        }

    def experiments(self) -> list[dict[str, Any]]:
        paths = (
            self._walk_named(self.output_root, {"workflow.json"})
            if self.output_root.is_dir()
            else []
        )
        rows = [self._experiment_summary(path) for path in paths]
        counts = Counter(row["run_id"] for row in rows)
        for row in rows:
            if counts[row["run_id"]] > 1:
                row["verification_status"] = "invalid"
                row["error"] = "duplicate run_id"
        rows.sort(key=lambda row: (row["run_id"], row["relative_path"]), reverse=True)
        return rows

    def experiment(self, run_id: str) -> dict[str, Any]:
        matches = [row for row in self.experiments() if row["run_id"] == run_id]
        if not matches:
            raise WebArtifactError("experiment not found", status=404)
        if len(matches) != 1 or matches[0]["verification_status"] == "invalid":
            raise WebArtifactError(matches[0].get("error") or "experiment identity is invalid")
        summary = matches[0]
        directory = self.repository_root / summary["relative_path"]
        workflow = self._read_json(directory / "workflow.json", "workflow")
        decisions = [
            row
            for row in self._decisions()
            if row.get("recommendation_id") == workflow.get("recommendation_id")
        ]
        if summary["workflow_id"] != SPECTROSCOPY_WORKFLOW_ID:
            return {
                **summary,
                "renderer": "generic",
                "raw": workflow,
                "decision_refs": decisions,
                "assets": [],
            }
        datasets = {
            "coarse": self._read_json(directory / "datasets" / "coarse.json", "coarse dataset"),
            "refined": self._read_json(directory / "datasets" / "refined.json", "refined dataset"),
            "confirmations": {},
        }
        confirmation_hashes = workflow["datasets"].get("confirmations", {})
        for target in confirmation_hashes:
            datasets["confirmations"][target] = self._read_json(
                directory / "datasets" / f"confirmation_{target}.json",
                f"{target} confirmation dataset",
            )
        evidence_paths = [
            child.relative_to(self.repository_root).as_posix()
            for name in ("c", "f", "s0", "s1")
            if (child := directory / name).is_dir()
        ]
        return {
            **summary,
            "renderer": "qubit_spectroscopy",
            "recommendation_id": workflow.get("recommendation_id"),
            "claim": workflow["claim"],
            "request": workflow["request"],
            "datasets": datasets,
            "analyses": workflow["analyses"],
            "gates": workflow["gates"],
            "candidates": workflow["candidates"],
            "plot_url": f"/api/v1/experiments/{run_id}/asset/spectroscopy.png",
            "decision_refs": decisions,
            "evidence_paths": evidence_paths,
            "assets": ["spectroscopy.png"],
        }

    def experiment_asset(self, run_id: str, asset_name: str) -> tuple[Path, str]:
        if asset_name != "spectroscopy.png":
            raise WebArtifactError("asset not found", status=404)
        matches = [row for row in self.experiments() if row["run_id"] == run_id]
        if len(matches) != 1 or matches[0]["verification_status"] != "verified":
            raise WebArtifactError("experiment asset is unavailable", status=404)
        path = self.repository_root / matches[0]["relative_path"] / asset_name
        if not path.is_file():
            raise WebArtifactError("asset not found", status=404)
        return path, "image/png"

    def _configuration_summary(self, path: Path) -> dict[str, Any]:
        relative = path.resolve().relative_to(self.repository_root).as_posix()
        try:
            payload = self._read_json(path, "configuration")
            configuration_id = (
                payload.get("snapshot_id")
                or payload.get("calibration_id")
                or payload.get("state_id")
            )
            if not isinstance(configuration_id, str) or not configuration_id:
                raise WebArtifactError("configuration identity is missing")
            return {
                "configuration_id": configuration_id,
                "state_id": payload.get("state_id"),
                "status": payload.get("status", "unknown"),
                "accepted": payload.get("accepted") is True
                or payload.get("artifact_type") == "platform_configuration_snapshot",
                "device_snapshot_sha256": payload.get("device_snapshot_sha256"),
                "parent_calibration_sha256": payload.get("parent_calibration_sha256"),
                "accepted_targets": payload.get("accepted_targets", []),
                "name": payload.get("name"),
                "device_id": payload.get("device_id"),
                "experiment_eligible": payload.get("experiment_eligible"),
                "requires_requalification": payload.get("requires_requalification"),
                "relative_path": relative,
                "verification_status": "verified",
                "error": None,
            }
        except Exception as exc:
            return {
                "configuration_id": f"invalid:{relative}",
                "state_id": None,
                "status": "invalid",
                "accepted": False,
                "device_snapshot_sha256": None,
                "parent_calibration_sha256": None,
                "accepted_targets": [],
                "name": None,
                "device_id": None,
                "experiment_eligible": None,
                "requires_requalification": None,
                "relative_path": relative,
                "verification_status": "invalid",
                "error": str(exc),
            }

    def _experiment_summary(self, path: Path) -> dict[str, Any]:
        directory = path.parent.resolve()
        relative = directory.relative_to(self.repository_root).as_posix()
        try:
            workflow = self._read_json(path, "workflow")
            run_id = workflow.get("run_id")
            workflow_id = workflow.get("workflow_id")
            if not isinstance(run_id, str) or not run_id:
                raise WebArtifactError("run_id is missing")
            if not isinstance(workflow_id, str) or not workflow_id:
                raise WebArtifactError("workflow_id is missing")
            verification_status = "unverified_generic"
            if workflow_id == SPECTROSCOPY_WORKFLOW_ID:
                verify_qubit_spectroscopy_calibration(directory)
                verification_status = "verified"
            request = workflow.get("request")
            coarse = request.get("coarse_request", {}) if isinstance(request, Mapping) else {}
            gates = workflow.get("gates", [])
            candidates = workflow.get("candidates", [])
            passed = sum(row.get("passed") is True for row in gates if isinstance(row, Mapping))
            failed = sum(row.get("passed") is False for row in gates if isinstance(row, Mapping))
            return {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "experiment_kind": (
                    "Qubit spectroscopy"
                    if workflow_id == SPECTROSCOPY_WORKFLOW_ID
                    else workflow_id
                ),
                "status": workflow.get("status", "unknown"),
                "verification_status": verification_status,
                "targets": coarse.get("targets", []),
                "execution_mode": coarse.get("execution_mode"),
                "recommendation_eligible": workflow.get("recommendation_eligible") is True,
                "parent_calibration": workflow.get("parent_calibration"),
                "gate_summary": {"passed": passed, "failed": failed, "total": passed + failed},
                "candidate_summary": [
                    {
                        "target": row.get("target"),
                        "proposed_frequency_GHz": row.get("proposed_frequency_GHz"),
                        "recommendation_eligible": row.get("recommendation_eligible") is True,
                    }
                    for row in candidates
                    if isinstance(row, Mapping)
                ],
                "relative_path": relative,
                "error": None,
            }
        except Exception as exc:
            return {
                "run_id": f"invalid:{relative}",
                "workflow_id": "invalid",
                "experiment_kind": "Invalid artifact",
                "status": "invalid",
                "verification_status": "invalid",
                "targets": [],
                "execution_mode": None,
                "recommendation_eligible": False,
                "parent_calibration": None,
                "gate_summary": {"passed": 0, "failed": 0, "total": 0},
                "candidate_summary": [],
                "relative_path": relative,
                "error": str(exc),
            }

    def _decisions(self) -> list[dict[str, Any]]:
        if not self.output_root.is_dir():
            return []
        rows = []
        for path in self._walk_named(self.output_root, {"decision.json"}):
            relative = path.resolve().relative_to(self.repository_root).as_posix()
            try:
                payload = self._read_json(path, "decision")
                rows.append(
                    {
                        "decision_id": payload.get("decision_id"),
                        "recommendation_id": payload.get("recommendation_id"),
                        "decision": payload.get("decision"),
                        "accepted_targets": payload.get("accepted_targets", []),
                        "actor_id": payload.get("actor_id"),
                        "decided_utc": payload.get("decided_utc"),
                        "relative_path": relative,
                    }
                )
            except Exception:
                continue
        rows.sort(key=lambda row: (row.get("decided_utc") or "", row["relative_path"]), reverse=True)
        return rows

    def _walk_named(self, root: Path, names: set[str]) -> list[Path]:
        found: list[Path] = []

        def visit(directory: Path) -> None:
            if directory.is_symlink() or bool(getattr(directory, "is_junction", lambda: False)()):
                return
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError:
                return
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    visit(path)
                elif entry.is_file(follow_symlinks=False) and entry.name in names:
                    found.append(path)

        visit(root)
        return found

    def _read_json(self, path: Path, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text("utf-8"), parse_constant=_reject_constant)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise WebArtifactError(f"cannot read {label}: {exc}") from exc
        if not isinstance(payload, dict):
            raise WebArtifactError(f"{label} must be a JSON object")
        _finite(payload)
        return payload

    def _inside(self, value: str | Path, label: str) -> Path:
        path = Path(value)
        path = (
            (self.repository_root / path).resolve()
            if not path.is_absolute()
            else path.resolve()
        )
        try:
            path.relative_to(self.repository_root)
        except ValueError as exc:
            raise WebArtifactError(f"{label} is outside repository") from exc
        return path


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WebArtifactError("artifact contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _finite(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite(item)
        return
    raise WebArtifactError("artifact contains an unsupported value")


def _platform_frequencies(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    editable = payload.get("editable")
    calibration = editable.get("calibration_values") if isinstance(editable, Mapping) else None
    qagents = calibration.get("qagents") if isinstance(calibration, Mapping) else None
    rows = []
    if isinstance(qagents, Mapping):
        for target, value in sorted(qagents.items()):
            reference = value.get("reference_frequency_authority") if isinstance(value, Mapping) else None
            if isinstance(reference, Mapping):
                rows.append(
                    {
                        "target": target,
                        "reference_frequency_GHz": reference.get("reference_frequency_GHz"),
                        "frequency_source": reference.get("frequency_source"),
                        "revision": reference.get("revision"),
                        "setting_hash": reference.get("setting_hash"),
                    }
                )
    return rows
