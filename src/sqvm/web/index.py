"""Filesystem-backed read models for the calibration Web console."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading
from typing import Any, Mapping

from sqvm.candidate_protocol import (
    CalibrationCandidateProtocolError,
    normalize_calibration_candidate,
)

from sqvm.calibration import (
    SPECTROSCOPY_SCAN_WORKFLOW_ID,
    verify_qubit_spectroscopy_calibration,
    verify_qubit_spectroscopy_scan,
)
from sqvm.web.plotting import build_spectroscopy_plot_spec, validate_plot_spec
from sqvm.web.read_model import (
    ExperimentProjection,
    PersistentExperimentReadModel,
    ReadModelError,
)


SPECTROSCOPY_WORKFLOW_ID = "qubit_spectroscopy_calibration_v1"

_KNOWN_WORKFLOW_VERSIONS = {
    SPECTROSCOPY_WORKFLOW_ID: frozenset({"0.1"}),
    SPECTROSCOPY_SCAN_WORKFLOW_ID: frozenset({"0.1", "0.2", "0.3"}),
}
_V03_RECEIPT_TYPE = "qubit_spectroscopy_scan_receipt"
_V03_MANIFEST_TYPE = "stage_07_qubit_spectroscopy_scan_manifest"
_V03_REPORT_TYPE = "stage_07_qubit_spectroscopy_scan_verification_report"

_EXPERIMENT_IDENTITY_FILES = frozenset(
    {
        "workflow.json",
        "dataset.json",
        "receipt.json",
        "manifest.json",
        "verification_report.json",
        "spectroscopy.png",
    }
)
_EVIDENCE_DIRECTORY_NAMES = frozenset(
    {"execution", "circuits", "datasets", "c", "f", "s0", "s1"}
)
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_HASH_BYTES = 64 * 1024 * 1024
_MAX_JSON_DEPTH = 64


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
        *,
        read_model_path: str | Path | None = None,
        trusted_read_model_path: bool = False,
        experiment_root: str | Path | None = None,
        trusted_experiment_root: bool = False,
        initial_sync: bool = True,
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.output_root = (
            self._inside(output_root, "output root")
            if output_root is not None
            else self.repository_root / "output"
        )
        self._experiment_reconcile_lock = threading.RLock()
        if experiment_root is None:
            self.experiment_root: Path | None = None
        elif trusted_experiment_root:
            self.experiment_root = Path(os.path.abspath(os.fspath(experiment_root)))
        else:
            self.experiment_root = self._inside(experiment_root, "experiment root")
        if read_model_path is None:
            model_path = self.output_root / "experiment-storage" / "web-read-model.sqlite"
        elif trusted_read_model_path:
            model_path = Path(os.path.abspath(os.fspath(read_model_path)))
        else:
            model_path = self._inside(read_model_path, "Web read-model path")
        self.read_model = PersistentExperimentReadModel(model_path)
        if initial_sync:
            self.reconcile_experiments()

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
        aggregate = self.read_model.aggregate()
        latest = self.page_experiments(limit=5)["items"]
        decisions = self._decisions()
        return {
            "schema_version": "0.1",
            "configurations": {
                "total": len(configurations),
                "accepted": sum(row["accepted"] is True for row in configurations),
            },
            "experiments": {
                "total": aggregate["total"],
                "eligible": aggregate["eligible"],
                "invalid": aggregate["invalid"],
            },
            "decisions": {
                "accepted": sum(row["decision"] == "accept" for row in decisions),
                "rejected": sum(row["decision"] == "reject" for row in decisions),
            },
            "latest_experiments": latest,
        }

    def configurations(self) -> list[dict[str, Any]]:
        paths = []
        config_root = self.repository_root / "configs" / "calibration"
        if config_root.is_dir():
            paths.extend(path for path in config_root.glob("*.json") if path.is_file())
        if self.output_root.is_dir():
            configuration_names = {"calibration.json", "snapshot.json"}
            paths.extend(
                path
                for name in configuration_names
                if (path := self.output_root / name).is_file()
            )
            try:
                output_children = sorted(os.scandir(self.output_root), key=lambda entry: entry.name)
            except OSError:
                output_children = []
            for entry in output_children:
                path = Path(entry.path)
                if (
                    entry.name == "experiments"
                    or entry.is_symlink()
                    or bool(getattr(path, "is_junction", lambda: False)())
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    continue
                paths.extend(
                    self._walk_named(
                        path,
                        configuration_names,
                        skip_directories=_EVIDENCE_DIRECTORY_NAMES,
                    )
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
        return self.read_model.summaries()

    def page_experiments(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            return self.read_model.page(limit=limit, cursor=cursor, filters=filters)
        except ReadModelError as exc:
            raise WebArtifactError(str(exc)) from exc

    def reconcile_experiments(self) -> int:
        with self._experiment_reconcile_lock:
            paths = self._published_experiment_workflows()
            projections = []
            for path in paths:
                projections.append(self._project_experiment(path))
            return self.read_model.reconcile(projections)

    def project_experiment_path(self, relative_path: str | Path) -> dict[str, Any]:
        lexical = Path(relative_path)
        if (
            lexical.is_absolute()
            or bool(lexical.drive)
            or ".." in lexical.parts
        ):
            raise WebArtifactError("experiment projection path must be repository-relative")
        current = self.repository_root
        for part in lexical.parts:
            current = current / part
            if _is_link_or_reparse(current):
                raise WebArtifactError("experiment projection path contains a link")
        directory = self._inside(lexical, "experiment projection path")
        if directory.name == "workflow.json":
            workflow_path = directory
            directory = directory.parent
        else:
            workflow_path = directory / "workflow.json"
        if (
            _is_link_or_reparse(directory)
            or not directory.is_dir()
            or not workflow_path.is_file()
            or _is_link_or_reparse(workflow_path)
        ):
            raise WebArtifactError("experiment projection path is not a published run")
        projection = self._project_experiment(workflow_path)
        revision = self.read_model.upsert_projection(projection)
        return {
            "run_id": projection.run_id,
            "revision": revision,
            "identity": {
                "workflow_sha256": projection.workflow_sha256,
                "receipt_sha256": projection.receipt_sha256,
            },
        }

    def reconcile_path(self, relative_path: str | Path) -> dict[str, Any]:
        return self.project_experiment_path(relative_path)

    def upsert_projected_detail(
        self,
        summary: Mapping[str, Any],
        detail: Mapping[str, Any],
        identity: Mapping[str, Any],
        carrier: Mapping[str, Any] | None = None,
    ) -> int:
        run_id = summary.get("run_id")
        workflow_sha256 = identity.get("workflow_sha256")
        receipt_sha256 = identity.get("receipt_sha256")
        if not all(isinstance(value, str) and value for value in (
            run_id, workflow_sha256, receipt_sha256
        )):
            raise WebArtifactError("projected experiment identity is invalid")
        projection = ExperimentProjection(
            run_id=run_id,
            workflow_sha256=workflow_sha256,
            receipt_sha256=receipt_sha256,
            summary=dict(summary),
            detail=dict(detail),
            targets=tuple(
                target for target in summary.get("targets", []) if isinstance(target, str)
            ),
            dataset_bindings=tuple(
                row
                for row in identity.get("dataset_bindings", ())
                if isinstance(row, Mapping)
            ),
            plot_bindings=_plot_bindings(detail),
            carrier_state=str((carrier or {}).get("state", "hot")),
            carrier_alias=(carrier or {}).get("alias") if carrier is not None else "hot",
        )
        try:
            return self.read_model.upsert_projection(projection)
        except ReadModelError as exc:
            raise WebArtifactError(str(exc)) from exc

    def mark_carrier_state(
        self,
        run_id: str,
        state: str,
        alias: str | None = None,
        *,
        workflow_sha256: str | None = None,
        receipt_sha256: str | None = None,
    ) -> int:
        try:
            return self.read_model.mark_carrier_state(
                run_id,
                state,
                alias,
                workflow_sha256=workflow_sha256,
                receipt_sha256=receipt_sha256,
            )
        except ReadModelError as exc:
            raise WebArtifactError(str(exc), status=404) from exc

    def _published_experiment_workflows(self) -> list[Path]:
        if self.experiment_root is not None:
            roots = [self.experiment_root]
        else:
            roots = [self.output_root]
            experiments_root = self.output_root / "experiments"
            if experiments_root.is_dir():
                roots.insert(0, experiments_root)
        paths = set()
        for root in roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(os.scandir(root), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                directory = Path(entry.path)
                if (
                    entry.is_symlink()
                    or bool(getattr(directory, "is_junction", lambda: False)())
                    or not entry.is_dir(follow_symlinks=False)
                ):
                    continue
                workflow = directory / "workflow.json"
                if workflow.is_file() and not workflow.is_symlink():
                    paths.add(workflow)
        return sorted(paths)

    def _experiment_fingerprint(
        self, directory: Path
    ) -> tuple[tuple[str, int, int, int], ...]:
        try:
            entries = os.scandir(directory)
        except OSError:
            return ()
        fingerprint = []
        with entries:
            for entry in entries:
                if entry.name not in _EXPERIMENT_IDENTITY_FILES or entry.is_symlink():
                    continue
                try:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                fingerprint.append(
                    (entry.name, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                )
        datasets = directory / "datasets"
        if datasets.is_dir() and not datasets.is_symlink():
            try:
                dataset_entries = os.scandir(datasets)
            except OSError:
                dataset_entries = None
            if dataset_entries is not None:
                with dataset_entries:
                    for entry in dataset_entries:
                        if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                            continue
                        try:
                            stat = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        fingerprint.append(
                            (
                                f"datasets/{entry.name}",
                                stat.st_size,
                                stat.st_mtime_ns,
                                stat.st_ctime_ns,
                            )
                        )
        return tuple(sorted(fingerprint))

    def _project_experiment(self, path: Path) -> ExperimentProjection:
        directory = path.parent.resolve()
        relative = self._experiment_relative(directory)
        try:
            _assert_projection_paths_unlinked(directory)
        except WebArtifactError as exc:
            summary = self._invalid_experiment_summary(relative, str(exc))
            return ExperimentProjection(
                run_id=summary["run_id"],
                workflow_sha256=hashlib.sha256(
                    f"linked-workflow:{relative}".encode("utf-8")
                ).hexdigest().upper(),
                receipt_sha256=hashlib.sha256(
                    f"linked-receipt:{relative}".encode("utf-8")
                ).hexdigest().upper(),
                summary=summary,
                detail=summary,
                carrier_state="hot",
                carrier_alias="hot",
            )
        for _attempt in range(2):
            before = self._experiment_fingerprint(directory)
            summary = self._experiment_summary(path)
            detail: Mapping[str, Any] = summary
            workflow: Mapping[str, Any] = {}
            if summary["verification_status"] != "invalid":
                try:
                    workflow = self._read_json(path, "workflow")
                    detail = self._experiment_detail(
                        summary, directory, workflow, summary["run_id"]
                    )
                except Exception as exc:
                    summary = self._invalid_experiment_summary(relative, str(exc))
                    detail = summary
            after = self._experiment_fingerprint(directory)
            if before == after:
                return ExperimentProjection(
                    run_id=summary["run_id"],
                    workflow_sha256=_projection_identity_hash(
                        path, f"missing-workflow:{relative}"
                    ),
                    receipt_sha256=_projection_identity_hash(
                        directory / "receipt.json", f"missing-receipt:{relative}"
                    ),
                    summary=summary,
                    detail=detail,
                    targets=tuple(
                        target
                        for target in summary.get("targets", [])
                        if isinstance(target, str)
                    ),
                    dataset_bindings=_dataset_bindings(workflow),
                    plot_bindings=_plot_bindings(detail),
                    carrier_state="hot",
                    carrier_alias="hot",
                )
        summary = self._invalid_experiment_summary(
            relative, "experiment projection changed repeatedly while reading"
        )
        return ExperimentProjection(
            run_id=summary["run_id"],
            workflow_sha256=_projection_identity_hash(path, f"unstable-workflow:{relative}"),
            receipt_sha256=_projection_identity_hash(
                directory / "receipt.json", f"unstable-receipt:{relative}"
            ),
            summary=summary,
            detail=summary,
            carrier_state="hot",
            carrier_alias="hot",
        )

    def experiment(self, run_id: str) -> dict[str, Any]:
        try:
            detail = self.read_model.detail(run_id)
        except ReadModelError as exc:
            raise WebArtifactError(str(exc)) from exc
        if detail is None:
            raise WebArtifactError("experiment not found", status=404)
        if detail.get("verification_status") == "invalid":
            raise WebArtifactError(detail.get("error") or "experiment identity is invalid")
        return detail

    def _experiment_detail(
        self,
        summary: Mapping[str, Any],
        directory: Path,
        workflow: Mapping[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        decisions = [
            row
            for row in self._decisions()
            if row.get("recommendation_id") == workflow.get("recommendation_id")
        ]
        if summary["workflow_id"] != SPECTROSCOPY_WORKFLOW_ID:
            if summary["workflow_id"] == SPECTROSCOPY_SCAN_WORKFLOW_ID:
                dataset = self._read_json(directory / "dataset.json", "spectroscopy dataset")
                datasets = {"scan": dataset}
                return {
                    **summary,
                    "renderer": "qubit_spectroscopy_scan",
                    "claim": workflow["claim"],
                    "request": workflow["request"],
                    "datasets": datasets,
                    "plot_specs": [
                        build_spectroscopy_plot_spec(summary["targets"], datasets)
                    ],
                    "analysis": workflow["analysis"],
                    "recommendation_id": workflow.get("recommendation_id"),
                    "candidates": _web_candidates(workflow.get("candidates")),
                    "gates": workflow.get("gates", []),
                    "decision_refs": [],
                    "evidence_paths": [
                        self._experiment_relative(directory / "execution")
                    ],
                    "assets": [],
                }
            plot_specs = _published_plot_specs(workflow)
            return {
                **summary,
                "renderer": "generic",
                "raw": workflow,
                "plot_specs": plot_specs,
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
            self._experiment_relative(child)
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
            "plot_specs": [build_spectroscopy_plot_spec(summary["targets"], datasets)],
            "analyses": workflow["analyses"],
            "gates": workflow["gates"],
            "candidates": _web_candidates(workflow.get("candidates")),
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
        path = self._experiment_source(matches[0]["relative_path"]) / asset_name
        if (
            not path.is_file()
            or _is_link_or_reparse(path)
            or _is_hardlinked_file(path)
        ):
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
        relative = self._experiment_relative(directory)
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
                self._verify_published_web_projection(directory, path, workflow)
                verification_status = "verified"
            elif workflow_id == SPECTROSCOPY_SCAN_WORKFLOW_ID:
                self._verify_published_web_projection(directory, path, workflow)
                verification_status = "verified"
            else:
                _published_plot_specs(workflow)
            request = workflow.get("request")
            claim = workflow.get("claim")
            scan_request = (
                request
                if workflow_id == SPECTROSCOPY_SCAN_WORKFLOW_ID
                else request.get("coarse_request", {})
                if isinstance(request, Mapping)
                else {}
            )
            gates = workflow.get("gates", [])
            candidates = workflow.get("candidates", [])
            passed = sum(row.get("passed") is True for row in gates if isinstance(row, Mapping))
            failed = sum(row.get("passed") is False for row in gates if isinstance(row, Mapping))
            return {
                "run_id": run_id,
                "workflow_id": workflow_id,
                "experiment_kind": (
                    "Qubit spectroscopy"
                    if workflow_id
                    in {SPECTROSCOPY_WORKFLOW_ID, SPECTROSCOPY_SCAN_WORKFLOW_ID}
                    else workflow_id
                ),
                "status": workflow.get("status", "unknown"),
                "created_utc": workflow.get("created_utc"),
                "data_origin": (
                    claim.get("evidence_class")
                    if isinstance(claim, Mapping)
                    else None
                ),
                "verification_status": verification_status,
                "targets": scan_request.get("targets", []),
                "execution_mode": scan_request.get("execution_mode"),
                "recommendation_applicable": (
                    workflow_id == SPECTROSCOPY_WORKFLOW_ID
                    or workflow_id == SPECTROSCOPY_SCAN_WORKFLOW_ID
                    and workflow.get("artifact_version") in {"0.2", "0.3"}
                ),
                "recommendation_eligible": workflow.get("recommendation_eligible") is True,
                "parent_calibration": workflow.get("parent_calibration")
                or workflow.get("parent_configuration"),
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
            return self._invalid_experiment_summary(relative, str(exc))

    def _invalid_experiment_summary(
        self, relative: str, error: str
    ) -> dict[str, Any]:
        return {
            "run_id": f"invalid:{relative}",
            "workflow_id": "invalid",
            "experiment_kind": "Invalid artifact",
            "status": "invalid",
            "created_utc": None,
            "data_origin": None,
            "verification_status": "invalid",
            "targets": [],
            "execution_mode": None,
            "recommendation_applicable": False,
            "recommendation_eligible": False,
            "parent_calibration": None,
            "gate_summary": {"passed": 0, "failed": 0, "total": 0},
            "candidate_summary": [],
            "relative_path": relative,
            "error": error,
        }

    def _verify_published_web_projection(
        self,
        directory: Path,
        workflow_path: Path,
        workflow: Mapping[str, Any],
    ) -> None:
        """Verify published visualization bindings without replaying execution evidence.

        A successful result means publication-time verification is still bound to
        the Web projection. It does not mean this request repeated the full evidence
        verification performed by publication and lifecycle operations.
        """

        receipt_path = directory / "receipt.json"
        receipt = self._read_json(receipt_path, "experiment receipt")
        artifact_version = workflow.get("artifact_version")
        allowed_versions = _KNOWN_WORKFLOW_VERSIONS.get(workflow.get("workflow_id"))
        if allowed_versions is None or artifact_version not in allowed_versions:
            raise WebArtifactError("published workflow artifact_version is unsupported")
        if artifact_version == "0.3" and (
            receipt.get("schema_version") != "0.1"
            or receipt.get("artifact_type") != _V03_RECEIPT_TYPE
            or receipt.get("artifact_version") != "0.3"
            or receipt.get("run_id") != workflow.get("run_id")
            or receipt.get("status") != workflow.get("status")
        ):
            raise WebArtifactError("v0.3 publication receipt identity is invalid")
        if (
            receipt.get("run_id") != workflow.get("run_id")
            or receipt.get("status") != workflow.get("status")
            or receipt.get("workflow_sha256") != _raw_sha256(workflow_path)
            or receipt.get("artifact_version", artifact_version) != artifact_version
        ):
            raise WebArtifactError("published workflow binding is invalid")
        workflow_id = workflow.get("workflow_id")
        if workflow_id == SPECTROSCOPY_SCAN_WORKFLOW_ID:
            dataset_binding = workflow.get("dataset")
            if not isinstance(dataset_binding, Mapping) or dataset_binding.get("path") != "dataset.json":
                raise WebArtifactError("published dataset binding is invalid")
            dataset_sha256 = _raw_sha256(directory / "dataset.json")
            if (
                dataset_binding.get("sha256") != dataset_sha256
                or receipt.get("dataset_sha256") != dataset_sha256
            ):
                raise WebArtifactError("published dataset binding is invalid")
        else:
            dataset_bindings = workflow.get("datasets")
            receipt_bindings = receipt.get("dataset_sha256s")
            if not isinstance(dataset_bindings, Mapping) or receipt_bindings != dataset_bindings:
                raise WebArtifactError("published dataset bindings are invalid")
            expected_paths = {
                "coarse": directory / "datasets" / "coarse.json",
                "refined": directory / "datasets" / "refined.json",
            }
            confirmations = dataset_bindings.get("confirmations")
            if not isinstance(confirmations, Mapping):
                raise WebArtifactError("published confirmation bindings are invalid")
            for target in confirmations:
                expected_paths[f"confirmations.{target}"] = (
                    directory / "datasets" / f"confirmation_{target}.json"
                )
            for key, dataset_path in expected_paths.items():
                expected = dataset_bindings
                for part in key.split("."):
                    if not isinstance(expected, Mapping):
                        break
                    expected = expected.get(part)
                if not isinstance(expected, str) or _raw_sha256(dataset_path) != expected:
                    raise WebArtifactError(f"published {key} dataset binding is invalid")

        for receipt_key, filename in (
            ("manifest_sha256", "manifest.json"),
            ("verification_report_sha256", "verification_report.json"),
            ("plot_sha256", "spectroscopy.png"),
        ):
            expected = receipt.get(receipt_key)
            if expected is not None and (
                not isinstance(expected, str)
                or _raw_sha256(directory / filename) != expected
            ):
                raise WebArtifactError(f"published {filename} binding is invalid")
        manifest_path = directory / "manifest.json"
        manifest_sha256 = receipt.get("manifest_sha256")
        if artifact_version == "0.3" and not isinstance(manifest_sha256, str):
            raise WebArtifactError("v0.3 publication manifest binding is missing")
        if isinstance(manifest_sha256, str):
            manifest = self._read_json(manifest_path, "experiment manifest")
            if artifact_version == "0.3" and (
                manifest.get("schema_version") != "0.1"
                or manifest.get("artifact_type") != _V03_MANIFEST_TYPE
                or manifest.get("artifact_version") != "0.3"
                or manifest.get("run_id") != workflow.get("run_id")
                or manifest.get("workflow_id") != workflow.get("workflow_id")
                or manifest.get("status") != workflow.get("status")
            ):
                raise WebArtifactError("v0.3 publication manifest identity is invalid")
            payload_files = manifest.get("payload_files")
            if artifact_version == "0.3" and not isinstance(payload_files, list):
                raise WebArtifactError("publication manifest payload_files are invalid")
            payload_by_path: dict[str, Mapping[str, Any]] = {}
            for value in payload_files if isinstance(payload_files, list) else []:
                if not isinstance(value, Mapping):
                    raise WebArtifactError("publication manifest payload entry is invalid")
                payload_path = value.get("path")
                if payload_path in {"workflow.json", "dataset.json"}:
                    if payload_path in payload_by_path:
                        raise WebArtifactError("publication manifest payload path is duplicated")
                    payload_by_path[payload_path] = value
            projection_payloads = {
                "workflow.json": workflow_path,
                "dataset.json": directory / "dataset.json",
            }
            payload_paths = (
                projection_payloads
                if artifact_version == "0.3"
                else {
                    key: value
                    for key, value in projection_payloads.items()
                    if key in payload_by_path
                }
            )
            for payload_path, source_path in payload_paths.items():
                value = payload_by_path.get(payload_path)
                try:
                    byte_length = source_path.stat().st_size
                except OSError as exc:
                    raise WebArtifactError(
                        f"cannot stat published {payload_path}: {exc}"
                    ) from exc
                if (
                    value is None
                    or value.get("path") != payload_path
                    or value.get("raw_sha256") != _raw_sha256(source_path)
                    or value.get("byte_length") != byte_length
                ):
                    raise WebArtifactError(
                        f"publication manifest {payload_path} binding is invalid"
                    )
        report_path = directory / "verification_report.json"
        report_sha256 = receipt.get("verification_report_sha256")
        if artifact_version == "0.3" and not isinstance(report_sha256, str):
            raise WebArtifactError("v0.3 verification report binding is missing")
        if isinstance(report_sha256, str):
            report = self._read_json(report_path, "verification report")
            if artifact_version == "0.3" and (
                report.get("schema_version") != "0.1"
                or report.get("artifact_type") != _V03_REPORT_TYPE
            ):
                raise WebArtifactError("v0.3 verification report identity is invalid")
            expected_report_fields = {
                "manifest_sha256": manifest_sha256,
                "run_id": workflow.get("run_id"),
                "status": workflow.get("status"),
                "artifact_version": artifact_version,
            }
            strict_report = artifact_version == "0.3"
            if report.get("ok") is not True or any(
                (strict_report or key in report) and report.get(key) != expected
                for key, expected in expected_report_fields.items()
            ):
                raise WebArtifactError("publication verification report is invalid")

    def _decisions(self) -> list[dict[str, Any]]:
        if not self.output_root.is_dir():
            return []
        rows = []
        for path in self._walk_named(
            self.output_root,
            {"decision.json"},
            skip_directories=_EVIDENCE_DIRECTORY_NAMES,
        ):
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

    def _walk_named(
        self,
        root: Path,
        names: set[str],
        *,
        skip_directories: frozenset[str] = frozenset(),
    ) -> list[Path]:
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
                    if entry.name in skip_directories:
                        continue
                    visit(path)
                elif entry.is_file(follow_symlinks=False) and entry.name in names:
                    found.append(path)

        visit(root)
        return found

    def _read_json(self, path: Path, label: str) -> dict[str, Any]:
        try:
            raw = _bounded_bytes(path, _MAX_JSON_BYTES, label)
            payload = json.loads(
                raw.decode("utf-8"),
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ) as exc:
            raise WebArtifactError(f"cannot read {label}: {exc}") from exc
        if not isinstance(payload, dict):
            raise WebArtifactError(f"{label} must be a JSON object")
        _finite(payload, depth=0)
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

    def _experiment_relative(self, value: str | Path) -> str:
        path = Path(value).resolve()
        try:
            return path.relative_to(self.repository_root).as_posix()
        except ValueError:
            if self.experiment_root is None:
                raise WebArtifactError("experiment path is outside repository")
            try:
                relative = path.relative_to(self.experiment_root.resolve())
            except ValueError as exc:
                raise WebArtifactError("experiment path is outside trusted root") from exc
            return (Path("experiment-hot") / relative).as_posix()

    def _experiment_source(self, relative: str) -> Path:
        lexical = Path(relative)
        if lexical.is_absolute() or ".." in lexical.parts:
            raise WebArtifactError("experiment source path is invalid")
        if lexical.parts[:1] == ("experiment-hot",):
            if self.experiment_root is None:
                raise WebArtifactError("experiment source root is unavailable")
            source = self.experiment_root.joinpath(*lexical.parts[1:])
            try:
                source.resolve().relative_to(self.experiment_root.resolve())
            except ValueError as exc:
                raise WebArtifactError("experiment source path escapes trusted root") from exc
            return source
        return self._inside(lexical, "experiment source path")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _bounded_bytes(path: Path, limit: int, label: str) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise WebArtifactError(f"cannot read {label}: {exc}") from exc
    if len(raw) > limit:
        raise WebArtifactError(f"{label} exceeds {limit} bytes")
    return raw


def _raw_sha256(path: Path) -> str:
    raw = _bounded_bytes(path, _MAX_HASH_BYTES, "published projection file")
    return hashlib.sha256(raw).hexdigest().upper()


def _projection_identity_hash(path: Path, missing_material: str) -> str:
    if path.is_file() and not _is_link_or_reparse(path) and not _is_hardlinked_file(path):
        try:
            return _raw_sha256(path)
        except WebArtifactError:
            pass
    return hashlib.sha256(missing_material.encode("utf-8")).hexdigest().upper()


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_hardlinked_file(path: Path) -> bool:
    try:
        value = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(value.st_mode) and value.st_nlink != 1


def _assert_projection_paths_unlinked(directory: Path) -> None:
    paths = [directory]
    paths.extend(directory / name for name in _EXPERIMENT_IDENTITY_FILES)
    datasets = directory / "datasets"
    if os.path.lexists(datasets):
        paths.append(datasets)
        if datasets.is_dir() and not _is_link_or_reparse(datasets):
            try:
                with os.scandir(datasets) as entries:
                    paths.extend(Path(entry.path) for entry in entries)
            except OSError as exc:
                raise WebArtifactError(f"cannot inspect visualization datasets: {exc}") from exc
    for path in paths:
        if not os.path.lexists(path):
            continue
        if _is_link_or_reparse(path) or _is_hardlinked_file(path):
            raise WebArtifactError(
                f"experiment projection contains a linked path: {path.name}"
            )


def _dataset_bindings(workflow: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    single = workflow.get("dataset")
    if isinstance(single, Mapping):
        return ({"name": "scan", "path": single.get("path"), "sha256": single.get("sha256")},)
    datasets = workflow.get("datasets")
    if not isinstance(datasets, Mapping):
        return ()
    rows = []
    for name in ("coarse", "refined"):
        sha256 = datasets.get(name)
        if isinstance(sha256, str):
            rows.append({"name": name, "path": f"datasets/{name}.json", "sha256": sha256})
    confirmations = datasets.get("confirmations")
    if isinstance(confirmations, Mapping):
        for target, sha256 in sorted(confirmations.items()):
            if isinstance(target, str) and isinstance(sha256, str):
                rows.append(
                    {
                        "name": f"confirmation:{target}",
                        "path": f"datasets/confirmation_{target}.json",
                        "sha256": sha256,
                    }
                )
    return tuple(rows)


def _plot_bindings(detail: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    specs = detail.get("plot_specs")
    if not isinstance(specs, list):
        return ()
    return tuple(
        {"plot_id": row.get("plot_id"), "plot_type": row.get("plot_type")}
        for row in specs
        if isinstance(row, Mapping) and isinstance(row.get("plot_id"), str)
    )


def _published_plot_specs(workflow: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = workflow.get("plot_specs", [])
    if not isinstance(value, list):
        raise WebArtifactError("workflow plot_specs must be a list")
    for spec in value:
        if not isinstance(spec, dict):
            raise WebArtifactError("workflow plot_spec must be an object")
        try:
            validate_plot_spec(spec)
        except ValueError as exc:
            raise WebArtifactError(f"workflow plot_spec is invalid: {exc}") from exc
    return value


def _web_candidates(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    try:
        return [normalize_calibration_candidate(row) for row in value]
    except CalibrationCandidateProtocolError as exc:
        raise WebArtifactError(f"workflow candidate is invalid: {exc}") from exc


def _finite(value: Any, *, depth: int) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise WebArtifactError(f"artifact exceeds maximum JSON depth {_MAX_JSON_DEPTH}")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WebArtifactError("artifact contains a non-finite number")
        return
    if isinstance(value, list):
        for item in value:
            _finite(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite(item, depth=depth + 1)
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
