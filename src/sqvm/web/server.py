"""Standard-library HTTP server for the read-only calibration console."""

from __future__ import annotations

import copy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from sqvm.storage.archive_verify import ArchiveLimits, ZipEvidenceReader
from sqvm.storage.archive_verify import verify_sqrun
from sqvm.storage.workflow_verifiers import archive_evidence_verifier_registry
from sqvm.runtime.storage import flush_directory
from sqvm.web.index import CalibrationWebIndex, WebArtifactError
from sqvm.web.coordinator import WebProjectionCoordinator
from sqvm.web.plotting import (
    PlotSpecError,
    build_min_max_envelope,
    build_spectroscopy_plot_spec,
    lookup_source_point,
)
from sqvm.web.configuration import (
    ConfigurationManagementError,
    PlatformConfigurationStore,
)

if TYPE_CHECKING:
    from sqvm.storage.catalog import CatalogRoots
    from sqvm.storage.operations import StorageMutationRequest, StorageOperationError


_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
_INLINE_PLOT_POINT_LIMIT = 2_000
_INLINE_PLOT_BYTE_LIMIT = 2_000_000


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def _reject_duplicate_json_keys(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _strong_etag(*parts: object) -> str:
    raw = json.dumps(
        parts,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f'"{hashlib.sha256(raw).hexdigest().upper()}"'


class CalibrationWebServer(ThreadingHTTPServer):
    index: CalibrationWebIndex
    store: PlatformConfigurationStore
    storage: "ExperimentStorageWebService"
    coordinator: WebProjectionCoordinator
    read_model_recovery: str | None

    def server_close(self) -> None:
        coordinator = getattr(self, "coordinator", None)
        if coordinator is not None:
            coordinator.stop()
        storage = getattr(self, "storage", None)
        shutdown_storage = getattr(storage, "shutdown", None)
        if shutdown_storage is not None:
            shutdown_storage()
        super().server_close()


class StorageWebError(ValueError):
    """Stable public error surface for storage management endpoints."""

    def __init__(self, code: str, status: int, error: str, *, run_id: str | None = None,
                 blockers: tuple[str, ...] = (), retryable: bool = False,
                 catalog_revision: int | None = None) -> None:
        self.code, self.status, self.error = code, status, error
        self.details = {"run_id": run_id, "blockers": list(blockers), "retryable": retryable,
                        "catalog_revision": catalog_revision}
        super().__init__(error)

    def payload(self) -> dict[str, object]:
        return {"code": self.code, "status": self.status, "error": self.error,
                "details": self.details}


class ExperimentStorageWebService:
    """Web adapter over the catalog and mutation public APIs only."""

    def __init__(self, *, hot_root: Path, storage_root: Path, configuration_root: Path,
                 experiment_output_root: Path, archive_root: Path | None = None) -> None:
        self.hot_root = hot_root
        self.storage_root = storage_root
        self.configuration_root = configuration_root
        self.experiment_output_root = experiment_output_root
        self.archive_root = archive_root or storage_root / "archives"
        self._catalog_lock = threading.RLock()
        self._catalog_source_token: tuple[tuple[object, ...], ...] | None = None
        self._catalog_reconcile_required = False
        self._catalog_refresh_thread: threading.Thread | None = None
        self._catalog_refresh_error = False
        self._catalog_refresh_retry_after = 0.0
        self._catalog_overview_lock = threading.Lock()
        self._catalog_overview_cache: dict[str, object] | None = None
        self.on_change: Callable[[], None] | None = None

    def shutdown(self) -> None:
        refresh = self._catalog_refresh_thread
        if refresh is not None and refresh is not threading.current_thread():
            refresh.join()

    @property
    def catalog_path(self) -> Path:
        return self.storage_root / "catalog.sqlite"

    def _roots(self) -> CatalogRoots:
        from sqvm.storage.catalog import CatalogRoots
        self.bootstrap_roots()
        return CatalogRoots(self.hot_root, self.archive_root, self.storage_root,
                            self.storage_root / "tombstones", self.storage_root / "trash")

    def bootstrap_roots(self) -> None:
        """Validate/create only trusted authority directories, never the catalog."""

        verified: set[Path] = set()
        _safe_create_storage_directory(
            self.hot_root, "experiment hot root", verified=verified
        )
        _safe_create_storage_directory(
            self.storage_root, "experiment storage root", verified=verified
        )
        for name in ("lifecycle", "tombstones", "trash"):
            _safe_create_storage_directory(
                self.storage_root / name,
                f"experiment storage {name} root",
                verified=verified,
            )
        _safe_create_storage_directory(
            self.archive_root, "experiment archive root", verified=verified
        )
        if os.path.lexists(self.configuration_root):
            _safe_validate_storage_directory(
                self.configuration_root,
                "configuration storage root",
                verified=verified,
            )

    def _reference_graph(self):
        from sqvm.storage.references import build_reference_graph
        return build_reference_graph(configuration_root=self.configuration_root,
                                     experiment_output_root=self.experiment_output_root,
                                     lifecycle_root=self.storage_root / "lifecycle",
                                     pins_root=self.storage_root / "pins")

    def rebuild(self) -> int:
        from sqvm.storage.catalog import rebuild_catalog
        from sqvm.storage.errors import StorageError
        with self._catalog_lock:
            try:
                before = self._source_token()
                revision = rebuild_catalog(
                    self.catalog_path,
                    self._roots(),
                    reference_adapter=self._reference_graph,
                )
                after = self._source_token()
                # A publication that races the rebuild must force another
                # shallow reconciliation instead of making a stale catalog
                # look current.
                stable = before == after
                self._catalog_source_token = after if stable else None
                self._catalog_reconcile_required = not stable
                return revision
            except StorageWebError:
                raise
            except (StorageError, OSError, ValueError) as exc:
                raise StorageWebError("storage_catalog_unavailable", 500, "storage catalog rebuild failed", retryable=True) from exc

    def _ensure_catalog(self) -> None:
        refresh = self._catalog_refresh_thread
        if refresh is not None and refresh.is_alive():
            return
        if self._catalog_refresh_error and time.monotonic() < self._catalog_refresh_retry_after:
            return
        with self._catalog_lock:
            self.bootstrap_roots()
            source_token = self._source_token()
            if not self.catalog_path.is_file():
                self.rebuild()
                return
            if (
                self._catalog_source_token is None
                and not self._catalog_reconcile_required
                and not self._catalog_sources_newer()
            ):
                self._catalog_source_token = source_token
                return
            if source_token != self._catalog_source_token:
                self._start_catalog_refresh()

    def _catalog_sources_newer(self) -> bool:
        try:
            catalog_mtime = self.catalog_path.stat().st_mtime_ns
            roots = (
                self.hot_root,
                self.archive_root,
                self.storage_root / "trash",
                self.storage_root / "tombstones",
            )
            return any(root.stat().st_mtime_ns > catalog_mtime for root in roots)
        except OSError:
            return True

    def _start_catalog_refresh(self) -> None:
        refresh = self._catalog_refresh_thread
        if refresh is not None and refresh.is_alive():
            return
        self._catalog_refresh_error = False

        def refresh_catalog() -> None:
            try:
                self.rebuild()
            except Exception:
                self._catalog_refresh_error = True
                self._catalog_refresh_retry_after = time.monotonic() + 30.0
                self._catalog_source_token = None
                self._catalog_reconcile_required = True
            finally:
                self._catalog_refresh_thread = None

        self._catalog_refresh_thread = threading.Thread(
            target=refresh_catalog,
            name="sqvm-experiment-catalog-refresh",
            daemon=True,
        )
        self._catalog_refresh_thread.start()

    def _catalog_refresh_active(self) -> bool:
        refresh = self._catalog_refresh_thread
        return refresh is not None and refresh.is_alive()

    def _reject_catalog_refreshing_mutation(self) -> None:
        if self._catalog_refresh_active():
            raise StorageWebError(
                "storage_catalog_refreshing",
                409,
                "storage catalog is refreshing; retry after reconciliation",
                retryable=True,
            )
        if (
            self._catalog_refresh_error
            and time.monotonic() < self._catalog_refresh_retry_after
        ):
            raise StorageWebError(
                "storage_catalog_unavailable",
                503,
                "storage catalog refresh failed; retry after reconciliation",
                retryable=True,
            )

    def _source_token(self) -> tuple[tuple[object, ...], ...]:
        """Fingerprint only carrier roots; never descend into execution evidence."""

        rows: list[tuple[object, ...]] = []
        for label, root, skip_staging in (
            ("hot", self.hot_root, True),
            ("archive", self.archive_root, False),
            ("trash", self.storage_root / "trash", False),
            ("tombstone", self.storage_root / "tombstones", False),
        ):
            try:
                entries = sorted(os.scandir(root), key=lambda entry: entry.name)
            except OSError as exc:
                raise StorageWebError(
                    "storage_catalog_unavailable",
                    500,
                    "storage carrier roots cannot be inspected",
                    retryable=True,
                ) from exc
            for entry in entries:
                if skip_staging and entry.name.startswith("."):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise StorageWebError(
                        "storage_catalog_unavailable",
                        500,
                        "storage carrier roots cannot be inspected",
                        retryable=True,
                    ) from exc
                rows.append(
                    (
                        label,
                        entry.name,
                        stat.S_IFMT(info.st_mode),
                        info.st_size,
                        info.st_mtime_ns,
                    )
                )
        return tuple(rows)

    def overview(self) -> dict[str, object]:
        self._ensure_catalog()
        if self._catalog_refresh_thread is not None or self._catalog_refresh_error:
            with self._catalog_overview_lock:
                cached = copy.deepcopy(self._catalog_overview_cache)
            if cached is not None:
                cached["refreshing"] = self._catalog_refresh_thread is not None
                cached["refresh_error"] = self._catalog_refresh_error
                return cached

        from sqvm.storage.catalog import query_catalog, storage_summary
        from sqvm.storage.errors import StorageError
        try:
            refresh = self._catalog_refresh_thread
            summary = storage_summary(self.catalog_path, volume_root=self.storage_root)
            rows = [_public_catalog_row(row) for row in query_catalog(self.catalog_path)]
            if refresh is not None and self._catalog_refresh_thread is None:
                summary = storage_summary(self.catalog_path, volume_root=self.storage_root)
                rows = [_public_catalog_row(row) for row in query_catalog(self.catalog_path)]
        except StorageError as exc:
            raise StorageWebError("storage_catalog_unavailable", 500, "storage catalog is unreadable", retryable=True) from exc
        result = {"schema_version": "0.1", "catalog_revision": summary.catalog_revision,
                  "refreshing": self._catalog_refresh_thread is not None,
                  "refresh_error": self._catalog_refresh_error,
                  "logical_bytes": summary.logical_bytes, "allocated_bytes": summary.allocated_bytes,
                  "archive_bytes": summary.archive_bytes, "reclaimable_now_bytes": summary.reclaimable_now_bytes,
                  "reclaimable_after_trash_bytes": summary.reclaimable_after_trash_bytes,
                  "volume_free_bytes": summary.volume_free_bytes, "volume_total_bytes": summary.volume_total_bytes,
                  "allocated_estimated": summary.allocated_estimated, "items": rows}
        with self._catalog_overview_lock:
            self._catalog_overview_cache = copy.deepcopy(result)
        return result

    def run(self, run_id: str, *, trash_only: bool = False) -> dict[str, object]:
        self._ensure_catalog()
        from sqvm.storage.catalog import query_catalog
        rows = query_catalog(self.catalog_path, run_id)
        if len(rows) != 1 or (trash_only and rows[0].storage_state != "trash"):
            raise StorageWebError("experiment_not_found", 404, "experiment storage record was not found", run_id=run_id)
        return _public_catalog_row(rows[0])

    def projection_rows(self) -> tuple[dict[str, object], ...]:
        """Return path-free carrier identities for the background Web projector."""

        self._ensure_catalog()
        if self._catalog_refresh_error:
            raise StorageWebError(
                "storage_catalog_unavailable",
                503,
                "storage catalog refresh failed; projection is paused",
                retryable=True,
            )
        if self._catalog_refresh_active():
            return ()
        from sqvm.storage.catalog import query_catalog

        return tuple(
            {
                "run_id": row.run_id,
                "workflow_id": row.workflow_id,
                "workflow_sha256": row.workflow_sha256,
                "receipt_sha256": row.receipt_sha256,
                "storage_state": row.storage_state,
                "created_utc": row.created_utc,
                "carrier_alias": row.read_preference,
            }
            for row in query_catalog(self.catalog_path)
        )

    def projection_directory(self, run_id: str) -> Path | None:
        """Return a verified directory carrier for internal projection recovery."""

        self._ensure_catalog()
        if self._catalog_refresh_active() or self._catalog_refresh_error:
            return None
        from sqvm.storage.catalog import query_catalog

        rows = query_catalog(self.catalog_path, run_id)
        if len(rows) != 1:
            return None
        row = rows[0]
        raw = row.hot_path if row.storage_state == "hot" else (
            row.trash_path if row.storage_state == "trash" else None
        )
        if not raw:
            return None
        path = _lexical_absolute(raw)
        try:
            info = path.lstat()
        except OSError:
            return None
        if _linked_or_reparse(path, info) or not stat.S_ISDIR(info.st_mode):
            return None
        return path

    def trash(self) -> dict[str, object]:
        overview = self.overview()
        return {
            "schema_version": "0.1",
            "catalog_revision": overview["catalog_revision"],
            "refreshing": overview["refreshing"],
            "refresh_error": overview["refresh_error"],
            "items": [
                item for item in overview["items"] if item["storage_state"] == "trash"
            ],
        }

    def archive_asset(self, run_id: str, entry_path: str) -> tuple[bytes, str]:
        row = self._internal_archive_row(run_id)
        archive = row.archive_path
        if not archive:
            raise StorageWebError("experiment_not_found", 404, "archived experiment asset was not found", run_id=run_id)
        try:
            bundle = verify_sqrun(archive, verifier_registry=archive_evidence_verifier_registry(), require_source_verified=True)
            if bundle.run_id != row.run_id or bundle.workflow_sha256 != row.workflow_sha256:
                raise ValueError("archive identity changed")
            reader = ZipEvidenceReader(Path(archive), bundle.entries, ArchiveLimits())
            raw = reader.read_bytes(entry_path, maximum_bytes=2_000_000)
        except Exception as exc:
            raise StorageWebError("archive_verification_failed", 500, "archived asset could not be read", run_id=run_id, retryable=True) from exc
        suffix = Path(entry_path).suffix.lower()
        content_type = {".json": "application/json; charset=utf-8", ".csv": "text/csv; charset=utf-8",
                        ".png": "image/png", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}.get(suffix, "application/octet-stream")
        return raw, content_type

    def experiment_detail(self, run_id: str) -> dict[str, object]:
        """Read a v0.3 archive into the hot scan-detail DTO with one verification pass."""

        row = self._internal_archive_row(run_id)
        archive = row.archive_path
        assert archive is not None
        try:
            bundle = verify_sqrun(archive, verifier_registry=archive_evidence_verifier_registry(), require_source_verified=True)
            if bundle.run_id != row.run_id or bundle.workflow_sha256 != row.workflow_sha256:
                raise ValueError("archive identity changed")
            policy = ArchiveLimits()
            reader = ZipEvidenceReader(Path(archive), bundle.entries, policy)
            workflow = _archive_json_from_reader(reader, "workflow.json")
            dataset = _archive_json_from_reader(reader, "dataset.json")
            request = workflow.get("request")
            targets = request.get("targets") if isinstance(request, Mapping) else []
            if not isinstance(targets, list):
                raise ValueError("archive request is invalid")
            if workflow.get("workflow_id") != "qubit_spectroscopy_scan_v1":
                raise ValueError("archive workflow renderer is unavailable")
        except Exception as exc:
            raise StorageWebError("archive_verification_failed", 500, "archived experiment detail could not be read", run_id=run_id, retryable=True) from exc
        gates = workflow.get("gates", [])
        if not isinstance(gates, list):
            gates = []
        candidates = workflow.get("candidates", [])
        if not isinstance(candidates, list):
            candidates = []
        passed = sum(
            isinstance(gate, Mapping) and gate.get("passed") is True for gate in gates
        )
        claim = workflow.get("claim", {})
        return {
            **_public_catalog_row(row),
            "artifact_version": workflow.get("artifact_version"),
            "experiment_kind": "Qubit spectroscopy",
            "status": workflow.get("status", "completed"),
            "created_utc": workflow.get("created_utc") or row.created_utc,
            "data_origin": claim.get("evidence_class") if isinstance(claim, Mapping) else None,
            "verification_status": "verified",
            "targets": targets,
            "execution_mode": request.get("execution_mode") if isinstance(request, Mapping) else None,
            "recommendation_applicable": True,
            "recommendation_eligible": workflow.get("recommendation_eligible") is True,
            "parent_calibration": workflow.get("parent_calibration")
            or workflow.get("parent_configuration"),
            "gate_summary": {"passed": passed, "failed": len(gates) - passed, "total": len(gates)},
            "candidate_summary": [],
            "relative_path": f"archive:{run_id}",
            "error": None,
            "renderer": "qubit_spectroscopy_scan",
            "claim": claim,
            "request": request,
            "datasets": {"scan": dataset},
            "plot_specs": [build_spectroscopy_plot_spec(targets, {"scan": dataset})],
            "analysis": workflow.get("analysis", {}),
            "recommendation_id": workflow.get("recommendation_id"),
            "candidates": candidates,
            "gates": gates,
            "decision_refs": [],
            "evidence_paths": [f"archive:{run_id}"],
            "assets": [],
        }

    def _internal_archive_row(self, run_id: str):
        self._ensure_catalog()
        from sqvm.storage.catalog import query_catalog
        rows = query_catalog(self.catalog_path, run_id)
        if len(rows) != 1 or rows[0].storage_state not in {"archived", "archived_duplicate"} or not rows[0].archive_path:
            raise StorageWebError("experiment_not_found", 404, "archived experiment was not found", run_id=run_id)
        return rows[0]

    def mutate(self, run_id: str, action: str, payload: Mapping[str, object]) -> dict[str, object]:
        request, keep = _storage_request(payload)
        if (action == "keep") != (keep is not None):
            raise StorageWebError("invalid_storage_request", 422, "keep is required only for a keep mutation", run_id=run_id)
        self._reject_catalog_refreshing_mutation()
        with self._catalog_lock:
            self._reject_catalog_refreshing_mutation()
            self._ensure_catalog()
            self._reject_catalog_refreshing_mutation()
            from sqvm.storage.catalog import query_catalog
            from sqvm.storage.operations import ExperimentStorageOperations, StorageOperationError
            rows = query_catalog(self.catalog_path, run_id)
            if len(rows) != 1:
                raise StorageWebError("experiment_not_found", 404, "experiment storage record was not found", run_id=run_id)
            current = _public_catalog_row(rows[0])
            revision = current["catalog_revision"]
            if request.expected_catalog_revision != revision:
                raise StorageWebError("catalog_revision_conflict", 412, "catalog revision changed", run_id=run_id,
                                      catalog_revision=revision, retryable=True)
            if request.expected_workflow_sha256 != current["workflow_sha256"]:
                raise StorageWebError("workflow_hash_conflict", 412, "workflow identity changed", run_id=run_id,
                                      catalog_revision=revision, retryable=True)
            operations = ExperimentStorageOperations(hot_root=self.hot_root, storage_root=self.storage_root,
                configuration_root=self.configuration_root, experiment_output_root=self.experiment_output_root,
                archive_root=self.archive_root, catalog_revision=revision)
            try:
                if action == "keep":
                    assert keep is not None
                    result = operations.set_keep(run_id, keep, request)
                elif action == "archive": result = operations.archive(run_id, request)
                elif action == "restore-hot": result = operations.restore_hot(run_id, request)
                elif action == "trash": result = operations.trash(run_id, request)
                elif action == "restore": result = operations.restore_trash(run_id, request)
                else: raise StorageWebError("invalid_storage_request", 422, "storage action is not available", run_id=run_id)
            except StorageOperationError as exc:
                raise _storage_operation_error(exc, run_id, revision) from exc
            new_revision = self.rebuild()
            rows = query_catalog(self.catalog_path, run_id)
            if len(rows) != 1:
                raise StorageWebError("storage_operation_failed", 500, "storage mutation completed without a catalog record", run_id=run_id, catalog_revision=new_revision)
            response = {"schema_version": "0.1", "operation_id": result.operation_id, "catalog_revision": new_revision,
                        "item": _public_catalog_row(rows[0])}
        if self.on_change is not None:
            self.on_change()
        return response


class CalibrationWebHandler(BaseHTTPRequestHandler):
    server: CalibrationWebServer

    def do_GET(self) -> None:
        try:
            self._get()
        except StorageWebError as exc:
            self._json(exc.status, exc.payload())
        except (WebArtifactError, ConfigurationManagementError) as exc:
            payload = {"error": str(exc), "status": exc.status}
            if isinstance(exc, ConfigurationManagementError):
                payload["field_errors"] = exc.field_errors
            self._json(exc.status, payload)
        except Exception:
            self._json(500, {"error": "internal server error", "status": 500})

    def do_HEAD(self) -> None:
        try:
            path = urlsplit(self.path).path
            if path in _STATIC_FILES:
                file_name, content_type = _STATIC_FILES[path]
                self._bytes(200, (_STATIC_ROOT / file_name).read_bytes(), content_type, head=True)
                return
            self._json(404, {"error": "not found", "status": 404}, head=True)
        except Exception:
            self._json(500, {"error": "internal server error", "status": 500}, head=True)

    def do_POST(self) -> None:
        self._mutate("POST")

    def do_PUT(self) -> None:
        self._mutate("PUT")

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._mutate("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _get(self) -> None:
        request = urlsplit(self.path)
        path = unquote(request.path)
        if path in _STATIC_FILES:
            file_name, content_type = _STATIC_FILES[path]
            self._bytes(200, (_STATIC_ROOT / file_name).read_bytes(), content_type)
            return
        if path == "/api/v1/health":
            read_model_status = self.server.coordinator.status()
            health = self.server.index.health()
            if read_model_status["status"] != "ok":
                health["status"] = "degraded"
            self._json(
                200,
                {
                    **health,
                    "read_model": read_model_status,
                    "read_model_recovery": getattr(
                        self.server, "read_model_recovery", None
                    ),
                },
            )
            return
        if path == "/api/v1/overview":
            self._json(200, self.server.index.overview())
            return
        if path == "/api/v1/configurations":
            self._json(200, {"items": self.server.index.configurations()})
            return
        if path == "/api/v1/configuration-management":
            self._json(200, self.server.store.summary())
            return
        if path.startswith("/api/v1/current-configurations/"):
            device_id = path.removeprefix("/api/v1/current-configurations/")
            if "/" not in device_id and device_id:
                self._json(200, self.server.store.current_configuration(device_id))
                return
        if path.startswith("/api/v1/drafts/"):
            tail = path.removeprefix("/api/v1/drafts/").split("/")
            draft_id = tail[0]
            if tail[1:] == ["diff"]:
                query = parse_qs(request.query, keep_blank_values=True)
                if set(query) - {"against"} or len(query.get("against", ["parent"])) != 1:
                    raise ConfigurationManagementError("diff query is invalid")
                self._json(
                    200,
                    self.server.store.draft_diff(
                        draft_id,
                        against=query.get("against", ["parent"])[0],
                    ),
                )
                return
            if len(tail) == 1:
                self._json(200, self.server.store.draft(draft_id))
                return
        if path.startswith("/api/v1/platform-snapshots/"):
            snapshot_id = path.removeprefix("/api/v1/platform-snapshots/")
            self._json(200, self.server.store.snapshot(snapshot_id))
            return
        if path.startswith("/api/v1/configurations/"):
            identifier = path.removeprefix("/api/v1/configurations/")
            detail = self.server.index.configuration(identifier)
            raw = detail["raw"]
            if raw.get("artifact_type") != "platform_configuration_snapshot":
                bootstrap = self.server.store.bootstrap_configuration(raw)
                detail = {
                    **detail,
                    "control_values": bootstrap["editable"]["control_values"],
                    "values": bootstrap["editable"]["calibration_values"],
                    "device_ref": bootstrap["device_ref"],
                    "authority_refs": bootstrap["authority_refs"],
                    "configuration_source": "control_baseline_plus_calibration",
                }
            self._json(200, detail)
            return
        if path == "/api/v1/experiments":
            query = parse_qs(request.query, keep_blank_values=True)
            if not query:
                payload = {"items": self.server.index.experiments()}
                self._json_with_etag(
                    payload, _strong_etag("experiments-v1", payload)
                )
                return
            limit, cursor, filters = _experiment_page_request(query)
            try:
                payload = self.server.index.page_experiments(
                    limit=limit,
                    cursor=cursor,
                    filters=filters,
                )
            except ValueError as exc:
                raise WebArtifactError(str(exc), status=400) from exc
            etag_value = payload.pop("etag", None)
            payload.pop("etag_material", None)
            revision = payload.pop("revision", None)
            if revision is not None:
                payload["read_model_revision"] = revision
            etag = (
                f'"{etag_value}"'
                if isinstance(etag_value, str) and etag_value
                else _strong_etag("experiments-v2", payload)
            )
            self._json_with_etag(payload, etag)
            return
        if path == "/api/v1/experiment-storage":
            self._json(200, self.server.storage.overview())
            return
        if path == "/api/v1/experiment-trash":
            self._json(200, self.server.storage.trash())
            return
        if path.startswith("/api/v1/experiment-trash/"):
            run_id = path.removeprefix("/api/v1/experiment-trash/")
            if run_id and "/" not in run_id:
                self._json(200, self.server.storage.run(run_id, trash_only=True))
                return
        if path.startswith("/api/v1/experiments/"):
            tail = path.removeprefix("/api/v1/experiments/")
            parts = tail.split("/")
            if len(parts) >= 4 and parts[1:3] == ["storage", "asset"] and parts[0]:
                raw, content_type = self.server.storage.archive_asset(parts[0], "/".join(parts[3:]))
                self._bytes(200, raw, content_type)
                return
            if len(parts) == 2 and parts[1] == "storage" and parts[0]:
                self._json(200, self.server.storage.run(parts[0]))
                return
            if len(parts) == 2 and parts[1] == "plots" and parts[0]:
                detail = self.server.index.experiment(parts[0])
                payload = {
                    "schema_version": "0.1",
                    "run_id": parts[0],
                    "items": _plot_descriptors(parts[0], detail),
                }
                self._json_with_etag(
                    payload, _strong_etag("experiment-plots-v1", payload)
                )
                return
            if (
                len(parts) == 4
                and parts[1] == "plots"
                and parts[3] == "data"
                and parts[0]
                and parts[2]
            ):
                detail = self.server.index.experiment(parts[0])
                spec = _experiment_plot_spec(detail, parts[2])
                max_points, x_min, x_max = _plot_data_request(request.query)
                try:
                    envelope = build_min_max_envelope(
                        spec.get("series", []),
                        max_points,
                        x_min=x_min,
                        x_max=x_max,
                    )
                except PlotSpecError as exc:
                    raise WebArtifactError(str(exc), status=422) from exc
                payload = {
                    "schema_version": "0.1",
                    "run_id": parts[0],
                    "plot_id": parts[2],
                    **envelope,
                }
                self._json_with_etag(
                    payload, _strong_etag("experiment-plot-data-v1", payload)
                )
                return
            if (
                len(parts) == 5
                and parts[1] == "plots"
                and parts[3] == "points"
                and all((parts[0], parts[2], parts[4]))
            ):
                detail = self.server.index.experiment(parts[0])
                spec = _experiment_plot_spec(detail, parts[2])
                try:
                    _web_path_identifier(parts[4], "point_id")
                    point = lookup_source_point(
                        spec.get("series", []), point_id=parts[4]
                    )
                except PlotSpecError as exc:
                    status = 404 if "not found" in str(exc) else 422
                    raise WebArtifactError(str(exc), status=status) from exc
                payload = {
                    "schema_version": "0.1",
                    "run_id": parts[0],
                    "plot_id": parts[2],
                    **point,
                }
                self._json_with_etag(
                    payload, _strong_etag("experiment-plot-point-v1", payload)
                )
                return
            if len(parts) == 1 and parts[0]:
                try:
                    detail = self.server.index.experiment(parts[0])
                except WebArtifactError as exc:
                    if exc.status != 404:
                        raise
                    self.server.coordinator.notify()
                    raise WebArtifactError(
                        "experiment projection is pending", status=503
                    ) from exc
                public_detail = _public_experiment_detail(parts[0], detail)
                self._json_with_etag(
                    public_detail,
                    _strong_etag("experiment-detail-v1", public_detail),
                )
                return
            if len(parts) == 3 and parts[1] == "asset":
                asset, content_type = self.server.index.experiment_asset(parts[0], parts[2])
                self._bytes(200, asset.read_bytes(), content_type)
                return
        raise WebArtifactError("not found", status=404)

    def _mutate(self, method: str) -> None:
        try:
            self._mutation_route(method)
        except StorageWebError as exc:
            self._json(exc.status, exc.payload())
        except (WebArtifactError, ConfigurationManagementError) as exc:
            payload = {"error": str(exc), "status": exc.status}
            if isinstance(exc, ConfigurationManagementError):
                payload["field_errors"] = exc.field_errors
            self._json(exc.status, payload)
        except Exception:
            self._json(500, {"error": "internal server error", "status": 500})

    def _mutation_route(self, method: str) -> None:
        path = unquote(urlsplit(self.path).path)
        payload = self._request_json() if method in {"POST", "PUT"} else {}
        if method == "POST" and path.startswith("/api/v1/experiment-trash/") and path.endswith("/restore"):
            run_id = path.removeprefix("/api/v1/experiment-trash/").removesuffix("/restore")
            if run_id and "/" not in run_id:
                _validate_storage_action_payload("restore", payload)
                self._json(200, self.server.storage.mutate(run_id, "restore", payload))
                return
        if method == "POST" and path.startswith("/api/v1/experiments/"):
            tail = path.removeprefix("/api/v1/experiments/").split("/")
            if len(tail) == 2 and tail[0] and tail[1] in {"keep", "archive", "restore-hot", "trash"}:
                _validate_storage_action_payload(tail[1], payload)
                self._json(200, self.server.storage.mutate(tail[0], tail[1], payload))
                return
        if method == "POST" and path == "/api/v1/drafts":
            base = self.server.index.configuration(payload.get("base_configuration_id"))
            raw = base["raw"]
            if raw.get("artifact_type") != "platform_configuration_snapshot":
                raw = self.server.store.bootstrap_configuration(raw)
            result = self.server.store.create_draft(
                raw,
                actor_id=payload.get("actor_id"),
                name=payload.get("name"),
                note=payload.get("note", ""),
            )
            self._json(201, result)
            return
        if (
            method == "POST"
            and path.startswith("/api/v1/experiments/")
            and path.endswith("/apply-current")
        ):
            run_id = path.removeprefix("/api/v1/experiments/").removesuffix(
                "/apply-current"
            )
            detail = self.server.index.experiment(run_id)
            if detail.get("recommendation_applicable") is not True:
                raise ConfigurationManagementError(
                    "experiment has no configuration update adapter"
                )
            if detail.get("claim", {}).get("evidence_class") == "synthetic_demo":
                raise ConfigurationManagementError(
                    "synthetic demo candidates cannot update configuration"
                )
            expected_phrase = f"APPLY CALIBRATION CANDIDATES {run_id}"
            if payload.get("confirmation_phrase") != expected_phrase:
                raise ConfigurationManagementError(
                    "candidate update confirmation phrase is invalid"
                )
            candidates = _selected_candidates(detail, payload)
            result = self.server.store.apply_candidates_to_current_configuration(
                payload.get("device_id", "demo_2q1c2r"),
                actor_id=payload.get("actor_id"),
                expected_content_sha256=payload.get("expected_content_sha256"),
                experiment_run_id=run_id,
                recommendation_id=detail.get("recommendation_id") or run_id,
                candidates=candidates,
            )
            self._json(200, result)
            return
        if method == "POST" and path.startswith("/api/v1/experiments/") and path.endswith("/draft"):
            run_id = path.removeprefix("/api/v1/experiments/").removesuffix("/draft")
            detail = self.server.index.experiment(run_id)
            if detail.get("recommendation_applicable") is not True:
                raise ConfigurationManagementError("experiment has no configuration draft adapter")
            candidates = _selected_candidates(detail, payload)
            parent = detail.get("parent_calibration")
            relative = parent.get("path") if isinstance(parent, dict) else None
            matches = [
                row
                for row in self.server.index.configurations()
                if row["relative_path"] == relative
            ]
            if len(matches) != 1:
                raise ConfigurationManagementError("experiment parent configuration is unavailable")
            base = self.server.index.configuration(matches[0]["configuration_id"])["raw"]
            if base.get("artifact_type") != "platform_configuration_snapshot":
                base = self.server.store.bootstrap_configuration(base)
            draft = self.server.store.create_draft(
                base,
                actor_id=payload.get("actor_id"),
                name=payload.get("name"),
                note=payload.get("note", ""),
            )
            result = self.server.store.apply_candidates_to_draft(
                draft["draft_id"],
                actor_id=payload.get("actor_id"),
                experiment_run_id=run_id,
                recommendation_id=detail.get("recommendation_id") or run_id,
                candidates=candidates,
            )
            self._json(201, result)
            return
        if path.startswith("/api/v1/current-configurations/"):
            tail = path.removeprefix("/api/v1/current-configurations/").split("/")
            device_id = tail[0]
            if method == "PUT" and len(tail) == 1:
                result = self.server.store.update_current_configuration(
                    device_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                    name=payload.get("name"),
                    note=payload.get("note", ""),
                    editable=payload.get("editable"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["initialize-calibration"]:
                result = self.server.store.initialize_current_calibration(
                    device_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["snapshots"]:
                result = self.server.store.snapshot_current_configuration(
                    device_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                    name=payload.get("name"),
                    reason=payload.get("reason"),
                    keep=payload.get("keep") is True,
                )
                self._json(201, result)
                return
        if path.startswith("/api/v1/drafts/"):
            tail = path.removeprefix("/api/v1/drafts/").split("/")
            draft_id = tail[0]
            if method == "PUT" and len(tail) == 1:
                result = self.server.store.update_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                    name=payload.get("name"),
                    note=payload.get("note", ""),
                    editable=payload.get("editable"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["validate"]:
                result = self.server.store.validate_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["initialize-calibration"]:
                result = self.server.store.initialize_calibration_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["publish"]:
                result = self.server.store.publish_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                    name=payload.get("name"),
                    reason=payload.get("reason"),
                    keep=payload.get("keep") is True,
                )
                self._json(201, result)
                return
            if method == "DELETE" and len(tail) == 1:
                actor_id = self.headers.get("X-SQVM-Actor")
                self.server.store.delete_draft(draft_id, actor_id=actor_id)
                self._json(200, {"deleted": True, "draft_id": draft_id})
                return
        if path.startswith("/api/v1/platform-snapshots/"):
            tail = path.removeprefix("/api/v1/platform-snapshots/").split("/")
            snapshot_id = tail[0]
            if method == "POST" and tail[1:] == ["activate"]:
                result = self.server.store.set_active(
                    snapshot_id,
                    actor_id=payload.get("actor_id"),
                    confirmation_phrase=payload.get("confirmation_phrase"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["apply"]:
                result = self.server.store.apply_snapshot_to_current(
                    snapshot_id,
                    actor_id=payload.get("actor_id"),
                    expected_current_content_sha256=payload.get(
                        "expected_current_content_sha256"
                    ),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["keep"]:
                result = self.server.store.set_keep(
                    snapshot_id,
                    actor_id=payload.get("actor_id"),
                    keep=payload.get("keep") is True,
                )
                self._json(200, result)
                return
            if method == "DELETE" and len(tail) == 1:
                actor_id = self.headers.get("X-SQVM-Actor")
                self.server.store.delete_snapshot(snapshot_id, actor_id=actor_id)
                self._json(200, {"deleted": True, "snapshot_id": snapshot_id})
                return
        self._method_not_allowed()

    def _request_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise ConfigurationManagementError("request Content-Type must be application/json", status=415)
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ConfigurationManagementError("request Content-Length is invalid", status=400) from exc
        if not 0 < length <= 2_000_000:
            raise ConfigurationManagementError("request body size is invalid", status=413)
        try:
            payload = json.loads(
                self.rfile.read(length).decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ConfigurationManagementError("request JSON is invalid", status=400) from exc
        if not isinstance(payload, dict):
            raise ConfigurationManagementError("request JSON must be an object", status=400)
        return payload

    def _method_not_allowed(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, HEAD")
        self._common_headers("application/json; charset=utf-8", 0)
        self.end_headers()

    def _json(
        self,
        status: int,
        payload: Any,
        *,
        head: bool = False,
        headers: Mapping[str, str] | None = None,
        cache_control: str = "no-store",
    ) -> None:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self._bytes(
            status,
            raw,
            "application/json; charset=utf-8",
            head=head,
            headers=headers,
            cache_control=cache_control,
        )

    def _json_with_etag(self, payload: Any, etag: str) -> None:
        request_etag = self.headers.get("If-None-Match")
        if request_etag is not None:
            if len(request_etag) > 2048:
                raise WebArtifactError("If-None-Match is too large", status=400)
            candidates = {value.strip() for value in request_etag.split(",")}
            if "*" in candidates or etag in candidates:
                self.send_response(HTTPStatus.NOT_MODIFIED)
                self._common_headers(
                    "application/json; charset=utf-8",
                    0,
                    cache_control="private, max-age=0, must-revalidate",
                )
                self.send_header("ETag", etag)
                self.end_headers()
                return
        self._json(
            200,
            payload,
            headers={"ETag": etag},
            cache_control="private, max-age=0, must-revalidate",
        )

    def _bytes(
        self,
        status: int,
        raw: bytes,
        content_type: str,
        *,
        head: bool = False,
        headers: Mapping[str, str] | None = None,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self._common_headers(content_type, len(raw), cache_control=cache_control)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(raw)

    def _common_headers(
        self,
        content_type: str,
        length: int,
        *,
        cache_control: str = "no-store",
    ) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'",
        )


def create_calibration_web_server(
    repository_root: str | Path,
    *,
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    experiment_hot_root: str | Path | None = None,
    experiment_storage_root: str | Path | None = None,
    experiment_archive_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> CalibrationWebServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("bounded calibration Web server only binds to localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be in [0, 65535]")
    server = CalibrationWebServer((host, port), CalibrationWebHandler)
    server.read_model_recovery = None
    try:
        server.store = PlatformConfigurationStore(repository_root, configuration_storage_root)
        _safe_create_storage_directory(server.store.root, "configuration storage root")
        output = _lexical_absolute(output_root) if output_root is not None else _lexical_absolute(repository_root) / "output"
        server.storage = ExperimentStorageWebService(
            hot_root=_lexical_absolute(experiment_hot_root) if experiment_hot_root is not None else output / "experiments",
            storage_root=_lexical_absolute(experiment_storage_root) if experiment_storage_root is not None else output / "experiment-storage",
            configuration_root=server.store.root,
            # Only completed experiment carriers are an authority root.  The
            # storage root contains derived archives/trash and must never feed the
            # reference resolver back into itself during cross-volume recovery.
            experiment_output_root=_lexical_absolute(experiment_hot_root) if experiment_hot_root is not None else output / "experiments",
            archive_root=_trusted_archive_root(experiment_archive_root) if experiment_archive_root is not None else None,
        )
        server.storage.bootstrap_roots()
        read_model_path = server.storage.storage_root / "web-read-model.sqlite"
        try:
            server.index = CalibrationWebIndex(
                repository_root,
                output_root,
                read_model_path=read_model_path,
                trusted_read_model_path=True,
                experiment_root=(server.storage.hot_root if experiment_hot_root is not None else None),
                trusted_experiment_root=experiment_hot_root is not None,
                initial_sync=False,
            )
        except sqlite3.DatabaseError:
            _quarantine_corrupt_read_model(read_model_path)
            server.read_model_recovery = "corrupt_database_quarantined"
            server.index = CalibrationWebIndex(
                repository_root,
                output_root,
                read_model_path=read_model_path,
                trusted_read_model_path=True,
                experiment_root=(server.storage.hot_root if experiment_hot_root is not None else None),
                trusted_experiment_root=experiment_hot_root is not None,
                initial_sync=False,
            )
        server.coordinator = WebProjectionCoordinator(
            server.index,
            server.storage,
            repository_root=repository_root,
            storage_root=server.storage.storage_root,
        )
        server.storage.on_change = server.coordinator.notify
        server.coordinator.start()
    except Exception:
        server.server_close()
        raise
    return server


def _experiment_page_request(
    query: Mapping[str, list[str]],
) -> tuple[int, str | None, dict[str, object]]:
    allowed = {
        "limit",
        "cursor",
        "q",
        "target",
        "workflow_id",
        "verification_status",
        "recommendation_state",
        "storage_state",
    }
    if set(query) - allowed:
        raise WebArtifactError("experiment list query contains unsupported fields", status=400)

    def single(name: str) -> str | None:
        values = query.get(name)
        if values is None:
            return None
        if len(values) != 1 or not values[0] or len(values[0]) > 2048:
            raise WebArtifactError(f"experiment list {name} is invalid", status=400)
        return values[0]

    limit_text = single("limit")
    try:
        limit = 50 if limit_text is None else int(limit_text)
    except ValueError as exc:
        raise WebArtifactError("experiment list limit is invalid", status=400) from exc
    if not 1 <= limit <= 200:
        raise WebArtifactError("experiment list limit must be in [1,200]", status=400)
    cursor = single("cursor")
    filters: dict[str, object] = {}
    for name in (
        "q",
        "workflow_id",
        "verification_status",
        "recommendation_state",
        "storage_state",
    ):
        value = single(name)
        if value is not None:
            if len(value) > 256:
                raise WebArtifactError(f"experiment list {name} is too large", status=400)
            filters[name] = value
    targets = query.get("target")
    if targets is not None:
        if (
            not targets
            or len(targets) > 32
            or len(targets) != len(set(targets))
            or any(not value or len(value) > 128 for value in targets)
        ):
            raise WebArtifactError("experiment list target filter is invalid", status=400)
        filters["target"] = tuple(sorted(targets))
    return limit, cursor, filters


def _experiment_plot_spec(
    detail: Mapping[str, object], plot_id: str
) -> Mapping[str, Any]:
    _web_path_identifier(plot_id, "plot_id")
    specs = detail.get("plot_specs")
    if not isinstance(specs, list):
        raise WebArtifactError("experiment plot specifications are unavailable", status=404)
    matches = [
        spec
        for spec in specs
        if isinstance(spec, Mapping) and spec.get("plot_id") == plot_id
    ]
    if len(matches) != 1:
        raise WebArtifactError("experiment plot was not found", status=404)
    return matches[0]


def _plot_point_count(spec: Mapping[str, Any]) -> int:
    plot_type = spec.get("plot_type")
    collection = spec.get("series") if plot_type in {"line", "scatter"} else spec.get("layers")
    if not isinstance(collection, list):
        return 0
    field = "points" if plot_type in {"line", "scatter"} else "cells"
    return sum(
        len(row.get(field, []))
        for row in collection
        if isinstance(row, Mapping) and isinstance(row.get(field), list)
    )


def _plot_descriptors(
    run_id: str, detail: Mapping[str, object]
) -> list[dict[str, object]]:
    specs = detail.get("plot_specs")
    if not isinstance(specs, list):
        return []
    rows = []
    for spec in specs:
        if not isinstance(spec, Mapping):
            continue
        plot_id = spec.get("plot_id")
        if not isinstance(plot_id, str) or not plot_id:
            continue
        _web_path_identifier(plot_id, "plot_id")
        point_count = _plot_point_count(spec)
        serialized_bytes = len(
            json.dumps(
                spec,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        )
        external = (
            spec.get("plot_type") in {"line", "scatter"}
            and (
                point_count > _INLINE_PLOT_POINT_LIMIT
                or serialized_bytes > _INLINE_PLOT_BYTE_LIMIT
            )
        )
        rows.append(
            {
                "plot_id": plot_id,
                "plot_type": spec.get("plot_type"),
                "title": spec.get("title"),
                "point_count": point_count,
                "data_mode": "paged" if external else "inline",
                "data_url": (
                    f"/api/v1/experiments/{run_id}/plots/{plot_id}/data"
                    if external
                    else None
                ),
                "point_url_template": (
                    f"/api/v1/experiments/{run_id}/plots/{plot_id}/points/{{point_id}}"
                    if external
                    else None
                ),
            }
        )
    return rows


def _public_experiment_detail(
    run_id: str, detail: Mapping[str, object]
) -> dict[str, object]:
    payload = dict(detail)
    specs = detail.get("plot_specs")
    if not isinstance(specs, list):
        return payload
    descriptors = {
        row["plot_id"]: row for row in _plot_descriptors(run_id, detail)
    }
    public_specs = []
    for raw in specs:
        if not isinstance(raw, Mapping):
            continue
        spec = dict(raw)
        descriptor = descriptors.get(spec.get("plot_id"))
        if descriptor is not None and descriptor["data_mode"] == "paged":
            spec["series"] = []
            spec.pop("layers", None)
            spec["data_url"] = descriptor["data_url"]
            spec["point_url_template"] = descriptor["point_url_template"]
            spec["data_descriptor"] = {
                "format": "min_max_envelope_v1",
                "source_point_count": descriptor["point_count"],
                "default_max_points": _INLINE_PLOT_POINT_LIMIT,
                "point_lookup": "point_id",
            }
        public_specs.append(spec)
    payload["plot_specs"] = public_specs
    return payload


def _plot_data_request(query_text: str) -> tuple[int, float | None, float | None]:
    query = parse_qs(query_text, keep_blank_values=True)
    if set(query) - {"max_points", "x_min", "x_max"}:
        raise WebArtifactError("plot data query contains unsupported fields", status=400)

    def single(name: str) -> str | None:
        values = query.get(name)
        if values is None:
            return None
        if len(values) != 1 or not values[0] or len(values[0]) > 128:
            raise WebArtifactError(f"plot data {name} is invalid", status=400)
        return values[0]

    max_points_text = single("max_points")
    try:
        max_points = 2_000 if max_points_text is None else int(max_points_text)
    except ValueError as exc:
        raise WebArtifactError("plot data max_points is invalid", status=400) from exc
    if not 2 <= max_points <= 10_000:
        raise WebArtifactError("plot data max_points must be in [2,10000]", status=400)

    values: list[float | None] = []
    for name in ("x_min", "x_max"):
        raw = single(name)
        try:
            value = None if raw is None else float(raw)
        except ValueError as exc:
            raise WebArtifactError(f"plot data {name} is invalid", status=400) from exc
        if value is not None and not math.isfinite(value):
            raise WebArtifactError(f"plot data {name} must be finite", status=400)
        values.append(value)
    if values[0] is not None and values[1] is not None and values[0] > values[1]:
        raise WebArtifactError("plot data x_min must not exceed x_max", status=400)
    return max_points, values[0], values[1]


def _web_path_identifier(value: str, label: str) -> None:
    if (
        len(value) > 128
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for character in value)
    ):
        raise WebArtifactError(f"experiment {label} is not URL-safe", status=422)


def _selected_candidates(
    detail: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    rows = detail.get("candidates")
    if not isinstance(rows, list):
        raise ConfigurationManagementError("experiment candidates are invalid")
    candidate_ids = payload.get("candidate_ids")
    targets = payload.get("targets")
    if candidate_ids is not None:
        if (
            not isinstance(candidate_ids, list)
            or not candidate_ids
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise ConfigurationManagementError("candidate_ids are required")
        candidate_map = {
            row.get("candidate_id"): row
            for row in rows
            if isinstance(row, Mapping) and isinstance(row.get("candidate_id"), str)
        }
        selected = [candidate_map[item] for item in candidate_ids if item in candidate_map]
        if len(selected) != len(candidate_ids):
            raise ConfigurationManagementError("candidate_ids are invalid")
    else:
        if (
            not isinstance(targets, list)
            or not targets
            or len(targets) != len(set(targets))
        ):
            raise ConfigurationManagementError("candidate_ids are required")
        selected = [
            row
            for row in rows
            if isinstance(row, Mapping)
            and any(
                subject in targets
                for subject in row.get(
                    "calibration_subjects", [row.get("target")]
                )
            )
        ]
        selected_subjects = {
            subject
            for row in selected
            for subject in row.get("calibration_subjects", [row.get("target")])
        }
        if not selected or not set(targets).issubset(selected_subjects):
            raise ConfigurationManagementError("candidate targets are invalid")
    if any(row.get("recommendation_eligible") is not True for row in selected):
        raise ConfigurationManagementError("selected candidate is not eligible")
    return selected


def serve_calibration_web(
    repository_root: str | Path,
    *,
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    experiment_hot_root: str | Path | None = None,
    experiment_storage_root: str | Path | None = None,
    experiment_archive_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    server = create_calibration_web_server(
        repository_root,
        output_root=output_root,
        configuration_storage_root=configuration_storage_root,
        experiment_hot_root=experiment_hot_root,
        experiment_storage_root=experiment_storage_root,
        experiment_archive_root=experiment_archive_root,
        host=host,
        port=port,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _storage_request(payload: Mapping[str, object]) -> tuple[StorageMutationRequest, bool | None]:
    from sqvm.storage.operations import StorageMutationRequest
    base = {"actor_id", "expected_catalog_revision", "expected_workflow_sha256", "reason"}
    fields = set(payload)
    if fields not in (base, base | {"keep"}):
        raise StorageWebError("invalid_storage_request", 422, "storage request fields are invalid")
    actor, revision, workflow, reason = (payload.get(name) for name in ("actor_id", "expected_catalog_revision", "expected_workflow_sha256", "reason"))
    if not isinstance(actor, str) or not actor.strip() or type(revision) is not int or revision < 0 or not isinstance(workflow, str) or len(workflow) != 64 or any(item not in "0123456789ABCDEF" for item in workflow) or not isinstance(reason, str) or not reason.strip():
        raise StorageWebError("invalid_storage_request", 422, "storage request values are invalid")
    keep = payload.get("keep")
    if "keep" in fields and type(keep) is not bool:
        raise StorageWebError("invalid_storage_request", 422, "keep must be boolean")
    return StorageMutationRequest(actor, revision, workflow, reason), keep if "keep" in fields else None


def _validate_storage_action_payload(action: str, payload: Mapping[str, object]) -> None:
    _request, keep = _storage_request(payload)
    if (action == "keep") != (keep is not None):
        raise StorageWebError("invalid_storage_request", 422, "keep is required only for a keep mutation")


def _storage_operation_error(error: StorageOperationError, run_id: str, revision: int) -> StorageWebError:
    message = str(error).lower()
    if "referenced" in message:
        return StorageWebError("experiment_referenced", 409, "experiment is referenced and cannot enter trash", run_id=run_id, catalog_revision=revision)
    if "keep" in message:
        return StorageWebError("experiment_kept", 409, "experiment is retained and cannot enter trash", run_id=run_id, catalog_revision=revision)
    if "stale" in message or "concurrency" in message:
        return StorageWebError("catalog_revision_conflict", 412, "storage state changed; refresh and retry", run_id=run_id, catalog_revision=revision, retryable=True)
    if "not " in message or "only " in message or "state" in message:
        return StorageWebError("invalid_storage_state", 409, "storage state does not permit this action", run_id=run_id, catalog_revision=revision)
    return StorageWebError("storage_operation_failed", 500, "storage operation failed; evidence was retained", run_id=run_id, catalog_revision=revision, retryable=True)


def _public_catalog_row(row) -> dict[str, object]:
    """The catalog carrier location is server-only, even before C7 lands."""

    payload = row.to_dict()
    carrier = payload.get("carrier")
    if isinstance(carrier, Mapping):
        payload["carrier"] = {key: value for key, value in carrier.items() if not key.endswith("_path")}
    return payload


def _archive_json_from_reader(
    reader: ZipEvidenceReader, entry: str
) -> Mapping[str, object]:
    raw = reader.read_bytes(entry, maximum_bytes=2_000_000)
    return _decode_archive_json(raw)


def _decode_archive_json(raw: bytes) -> Mapping[str, object]:
    value = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant,
                       object_pairs_hook=_reject_duplicate_json_keys)
    if not isinstance(value, dict):
        raise ValueError("archive JSON must be an object")
    return value


def _lexical_absolute(value: str | Path) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _quarantine_corrupt_read_model(path: Path) -> None:
    token = f"{time.time_ns():X}"
    carriers = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    for carrier in carriers:
        if not os.path.lexists(carrier):
            continue
        info = carrier.lstat()
        if (
            _linked_or_reparse(carrier, info)
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or carrier.parent != path.parent
        ):
            raise ValueError("corrupt Web read-model carrier is unsafe")
        suffix = carrier.name.removeprefix(path.name)
        target = path.parent / f"web-read-model.corrupt.{token}.sqlite{suffix}"
        os.replace(carrier, target)
    flush_directory(path.parent)


def _trusted_archive_root(value: str | Path) -> Path:
    raw = os.fspath(value)
    if not os.path.isabs(raw) or raw.startswith("\\\\"):
        raise ValueError("experiment archive root must be an absolute local path")
    return _lexical_absolute(raw)


def _linked_or_reparse(path: Path, info: os.stat_result) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()) or bool(getattr(info, "st_reparse_tag", 0))


def _safe_create_storage_directory(
    value: Path,
    label: str,
    *,
    verified: set[Path] | None = None,
) -> Path:
    """Create a trusted startup root without traversing link/reparse ancestors."""

    path = _lexical_absolute(value)
    anchor = Path(path.anchor)
    verified = verified if verified is not None else set()
    if anchor not in verified:
        try:
            anchor_info = anchor.lstat()
        except OSError as exc:
            raise StorageWebError("storage_unavailable", 422, f"{label} cannot be inspected") from exc
        if _linked_or_reparse(anchor, anchor_info):
            raise StorageWebError("storage_unavailable", 422, f"{label} is linked or reparse-backed")
        verified.add(anchor)
    current = anchor
    for part in path.parts[1:]:
        cached = current / part
        if cached in verified:
            current = cached
            continue
        try:
            matches = [entry for entry in os.scandir(current) if entry.name == part]
        except OSError as exc:
            raise StorageWebError("storage_unavailable", 422, f"{label} component cannot be inspected") from exc
        if not matches:
            candidate = current / part
            try:
                candidate.mkdir()
                info = candidate.lstat()
            except OSError as exc:
                raise StorageWebError("storage_unavailable", 422, f"{label} component cannot be created") from exc
        elif len(matches) == 1:
            candidate = current / matches[0].name
            try:
                info = matches[0].stat(follow_symlinks=False)
            except OSError as exc:
                raise StorageWebError("storage_unavailable", 422, f"{label} component cannot be inspected") from exc
        else:
            raise StorageWebError("storage_unavailable", 422, f"{label} component is ambiguous")
        if _linked_or_reparse(candidate, info) or not stat.S_ISDIR(info.st_mode):
            raise StorageWebError("storage_unavailable", 422, f"{label} contains a linked or non-directory component")
        current = candidate
        verified.add(current)
    return current


def _safe_validate_storage_directory(
    value: Path,
    label: str,
    *,
    verified: set[Path] | None = None,
) -> Path:
    """Inspect an existing trusted directory without creating missing pieces."""

    path = _lexical_absolute(value)
    anchor = Path(path.anchor)
    verified = verified if verified is not None else set()
    if anchor not in verified:
        try:
            anchor_info = anchor.lstat()
        except OSError as exc:
            raise StorageWebError("storage_unavailable", 422, f"{label} cannot be inspected") from exc
        if _linked_or_reparse(anchor, anchor_info):
            raise StorageWebError("storage_unavailable", 422, f"{label} is linked or reparse-backed")
        verified.add(anchor)
    current = anchor
    for part in path.parts[1:]:
        cached = current / part
        if cached in verified:
            current = cached
            continue
        try:
            matches = [entry for entry in os.scandir(current) if entry.name == part]
        except OSError as exc:
            raise StorageWebError("storage_unavailable", 422, f"{label} component cannot be inspected") from exc
        if len(matches) != 1:
            raise StorageWebError("storage_unavailable", 422, f"{label} component is missing or ambiguous")
        candidate = current / matches[0].name
        try:
            info = matches[0].stat(follow_symlinks=False)
        except OSError as exc:
            raise StorageWebError("storage_unavailable", 422, f"{label} component cannot be inspected") from exc
        if _linked_or_reparse(candidate, info) or not stat.S_ISDIR(info.st_mode):
            raise StorageWebError("storage_unavailable", 422, f"{label} contains a linked or non-directory component")
        current = candidate
        verified.add(current)
    return current
