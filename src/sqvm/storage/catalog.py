"""Rebuildable, read-only experiment storage catalog cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from typing import Callable, Mapping, Protocol
import uuid

from sqvm.storage.archive_format import canonical_archive_json_bytes
from sqvm.storage.archive_format import EvidenceVerifier
from sqvm.storage.archive_verify import ArchiveLimits, ZipEvidenceReader, archive_raw_sha256, verify_sqrun
from sqvm.storage.errors import ArchiveFormatError, StorageError, StorageInventoryError
from sqvm.storage.inventory import _allocated_bytes, _is_link_or_reparse, _windows_cluster_size, inventory_tree, volume_usage


_MIGRATION_VERSION = 1
_SHA256 = set("0123456789ABCDEF")
_TOMBSTONE_FIELDS = frozenset({"schema_version", "artifact_type", "artifact_version", "run_id", "workflow_id", "workflow_sha256", "receipt_sha256", "last_bundle_sha256", "original_created_utc", "purged_utc", "actor_id", "reason", "last_lifecycle_event_sha256", "tombstone_sha256"})
_TRASH_FIELDS = frozenset({"schema_version", "artifact_type", "artifact_version", "run_id", "workflow_id", "workflow_sha256", "receipt_sha256", "operation_id", "actor_id", "reason", "previous_storage_state", "original_carrier_kind", "original_carrier_alias", "original_carrier_sha256", "payload_kind", "payload_sha256", "payload_logical_bytes", "trashed_utc", "purge_after_utc", "trash_started_event_sha256", "record_sha256"})


@dataclass(frozen=True)
class CatalogRoots:
    hot_root: Path
    archive_root: Path
    # This is the storage authority root consumed by lifecycle.read_head(),
    # whose journal layout is ``<storage_root>/lifecycle/<run_id>``.
    lifecycle_root: Path
    tombstone_root: Path
    trash_root: Path | None = None


@dataclass(frozen=True)
class CatalogReference:
    run_id: str
    reference_type: str
    object_id: str
    evidence_path: str
    evidence_sha256: str
    evidence_paths: tuple[str, ...] = ()
    permanent: bool = False


@dataclass(frozen=True)
class CatalogLifecycleHead:
    run_id: str
    sequence: int
    event_sha256: str
    state: str | None = None
    manual_keep: bool = False
    trash_previous_state: str | None = None
    pending_event_type: str | None = None
    pending_operation_id: str | None = None


@dataclass(frozen=True)
class CatalogReferenceGraph:
    references: tuple[CatalogReference, ...]
    scan_incomplete: bool
    blockers: tuple[str, ...]


class CatalogReferenceAdapter(Protocol):
    def resolve(self) -> CatalogReferenceGraph: ...


class CatalogLifecycleHeadAdapter(Protocol):
    def resolve(self) -> Mapping[str, CatalogLifecycleHead]: ...


@dataclass(frozen=True)
class CatalogRun:
    run_id: str
    workflow_id: str
    workflow_sha256: str
    receipt_sha256: str
    storage_state: str
    hot_path: str | None
    archive_path: str | None
    trash_path: str | None
    read_preference: str | None
    logical_bytes: int
    allocated_bytes: int
    archive_bytes: int
    reference_status: str
    manual_keep: bool
    blockers: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    created_utc: str = ""
    carrier: Mapping[str, object] | None = None
    references: tuple[CatalogReference, ...] = ()
    delete_after_utc: str | None = None
    catalog_revision: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "0.1", "run_id": self.run_id, "workflow_id": self.workflow_id,
            "workflow_sha256": self.workflow_sha256, "storage_state": self.storage_state,
            "retention_state": "reference_unknown" if self.reference_status == "unknown" else "manual_keep" if self.manual_keep else "referenced" if self.references else "normal", "created_utc": self.created_utc,
            "carrier": dict(self.carrier or {}), "logical_bytes": self.logical_bytes,
            "allocated_bytes": self.allocated_bytes, "allocated_estimated": bool((self.carrier or {}).get("allocated_estimated", False)),
            "reference_count": len(self.references), "references": [item.__dict__ for item in self.references],
            "delete_after_utc": self.delete_after_utc, "allowed_actions": list(self.allowed_actions),
            "blockers": list(self.blockers), "catalog_revision": self.catalog_revision,
        }


@dataclass(frozen=True)
class CatalogStorageSummary:
    catalog_revision: int
    logical_bytes: int
    allocated_bytes: int
    archive_bytes: int
    reclaimable_now_bytes: int
    reclaimable_after_trash_bytes: int
    volume_free_bytes: int
    volume_total_bytes: int
    allocated_estimated: bool


def rebuild_catalog(
    catalog_path: str | Path,
    roots: CatalogRoots,
    *,
    reference_adapter: CatalogReferenceAdapter | Callable[[], CatalogReferenceGraph] | None = None,
    lifecycle_adapter: CatalogLifecycleHeadAdapter | Callable[[], Mapping[str, CatalogLifecycleHead]] | None = None,
    hot_verifier_registry: Mapping[tuple[str, str], Callable[[Path], bool]] | None = None,
    archive_verifier_registry: Mapping[tuple[str, str], EvidenceVerifier] | None = None,
) -> int:
    """Rebuild a derived SQLite cache without mutating any authority root."""

    if hot_verifier_registry is None:
        from sqvm.storage.workflow_verifiers import workflow_hot_verifier_registry
        hot_verifier_registry = workflow_hot_verifier_registry()
    if archive_verifier_registry is None:
        from sqvm.storage.workflow_verifiers import archive_evidence_verifier_registry
        archive_verifier_registry = archive_evidence_verifier_registry()
    hot, archives, lifecycle, tombstones = (_safe_directory(value, label) for value, label in (
        (roots.hot_root, "hot root"), (roots.archive_root, "archive root"),
        (roots.lifecycle_root, "lifecycle root"), (roots.tombstone_root, "tombstone root"),
    ))
    trash = _safe_directory(roots.trash_root, "trash root") if roots.trash_root is not None else None
    catalog = _safe_catalog_path(catalog_path)
    lock = _acquire_rebuild_lock(catalog)
    temporary: Path | None = None
    primary_error: BaseException | None = None
    try:
        prior_revision = max(_existing_revision(catalog), _ledger_revision(catalog))
        temporary = catalog.parent / f".{catalog.name}.rebuild.{uuid.uuid4().hex}"
        archive_inventory = inventory_tree(archives, confinement_root=archives)
        archive_allocations = {row.relative_path: row for row in archive_inventory.files}
        discovered, global_blockers = _discover_runs(hot, archives, hot_verifier_registry or {}, archive_verifier_registry or {}, archive_allocations)
        if trash is not None:
            _discover_trash(trash, discovered, hot_verifier_registry or {}, archive_verifier_registry or {})
        references, references_unknown = _resolve_references(reference_adapter)
        heads, lifecycle_unknown = _resolve_heads(lifecycle, lifecycle_adapter)
        tombstone_rows = _discover_tombstones(tombstones)
        for tombstone in tombstone_rows:
            if tombstone["run_id"] in discovered:
                discovered[tombstone["run_id"]].blockers.add("tombstone_carrier_conflict")
        connection = sqlite3.connect(temporary)
        try:
            _initialize(connection)
            revision = prior_revision + 1
            connection.execute("INSERT INTO catalog_meta(key, value) VALUES (?, ?)", ("revision", str(revision)))
            _insert_discovered(connection, discovered, references, references_unknown, heads, lifecycle_unknown, global_blockers)
            _insert_tombstones(connection, tombstone_rows)
            _validate_database(connection)
            connection.commit()
            _checkpoint_database(connection)
            connection.commit()
        finally:
            connection.close()
        _fsync_file(temporary)
        _write_ledger_revision(catalog, revision)
        # The ledger is a durable reservation.  A failed catalog replacement can
        # consume a revision, but must never make it available for reuse.
        _atomic_replace(temporary, catalog)
        _fsync_directory(catalog.parent)
        return revision
    except Exception as exc:
        primary_error = exc
        if temporary is not None:
            _remove_temporary(temporary)
        if isinstance(exc, StorageError):
            raise
        raise StorageError("catalog rebuild failed") from exc
    finally:
        # A replaced/stale lock must be reported when publication succeeded,
        # but must never conceal the error that made the rebuild fail.
        try:
            _release_rebuild_lock(lock)
        except Exception:
            if primary_error is None:
                raise


def query_catalog(catalog_path: str | Path, run_id: str | None = None) -> tuple[CatalogRun, ...]:
    if run_id is not None:
        try:
            _run_id(run_id)
        except Exception as exc:
            raise StorageError("catalog run_id is invalid") from exc
    catalog = _safe_existing_catalog(catalog_path)
    try:
        connection = sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True)
        try:
            revision = int(connection.execute("SELECT value FROM catalog_meta WHERE key='revision'").fetchone()[0])
            query = (
                "SELECT runs.*, retention.manual_keep, retention.reference_status, retention.delete_after_utc FROM runs JOIN retention USING(run_id)"
                + (" WHERE runs.run_id=?" if run_id is not None else "")
                + " ORDER BY runs.run_id"
            )
            rows = connection.execute(query, (run_id,) if run_id is not None else ()).fetchall()
            references = {
                key: [] for key in (row[0] for row in rows)
            }
            for item in connection.execute("SELECT run_id, reference_type, object_id, evidence_path, evidence_sha256, evidence_paths, permanent FROM 'references' ORDER BY run_id, reference_type, evidence_path"):
                if item[0] in references:
                    references[item[0]].append(CatalogReference(*item[:5], tuple(json.loads(item[5])), bool(item[6])))
        finally:
            connection.close()
    except (sqlite3.DatabaseError, TypeError, ValueError, json.JSONDecodeError, IndexError) as exc:
        raise StorageError("catalog is unreadable; rebuild is required") from exc
    try:
        return tuple(_catalog_run(row, tuple(references[row[0]]), revision) for row in rows)
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise StorageError("catalog is unreadable; rebuild is required") from exc


def storage_summary(catalog_path: str | Path, *, volume_root: str | Path) -> CatalogStorageSummary:
    catalog = _safe_existing_catalog(catalog_path)
    volume = _safe_directory(volume_root, "volume root")
    try:
        connection = sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True)
        try:
            revision = int(connection.execute("SELECT value FROM catalog_meta WHERE key='revision'").fetchone()[0])
            logical, allocated, archive = connection.execute(
                "SELECT COALESCE(SUM(logical_bytes),0), COALESCE(SUM(allocated_bytes),0), COALESCE(SUM(archive_bytes),0) FROM runs"
            ).fetchone()
            reclaimable_now, reclaimable_after_trash = connection.execute(
                "SELECT COALESCE(SUM(CASE WHEN storage_state='archived_duplicate' THEN allocated_bytes ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN storage_state='trash' THEN allocated_bytes ELSE 0 END),0) FROM runs"
            ).fetchone()
            estimated = bool(connection.execute("SELECT COUNT(*) FROM runs WHERE allocated_estimated=1").fetchone()[0])
        finally:
            connection.close()
    except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
        raise StorageError("catalog is unreadable; rebuild is required") from exc
    usage = volume_usage(volume, confinement_root=volume)
    return CatalogStorageSummary(revision, logical, allocated, archive, reclaimable_now, reclaimable_after_trash, usage.free_bytes, usage.total_bytes, estimated)


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (_MIGRATION_VERSION,))
    connection.execute("CREATE TABLE catalog_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE runs(run_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_sha256 TEXT NOT NULL, "
        "receipt_sha256 TEXT NOT NULL, storage_state TEXT NOT NULL, hot_path TEXT, archive_path TEXT, trash_path TEXT, read_preference TEXT, "
        "logical_bytes INTEGER NOT NULL, allocated_bytes INTEGER NOT NULL, archive_bytes INTEGER NOT NULL, "
        "allocated_estimated INTEGER NOT NULL, blockers TEXT NOT NULL, created_utc TEXT NOT NULL)"
    )
    connection.execute("CREATE TABLE retention(run_id TEXT PRIMARY KEY, manual_keep INTEGER NOT NULL, latest_hold INTEGER NOT NULL, reference_status TEXT NOT NULL, delete_after_utc TEXT)")
    connection.execute("CREATE TABLE 'references'(run_id TEXT NOT NULL, reference_type TEXT NOT NULL, object_id TEXT NOT NULL, evidence_path TEXT NOT NULL, evidence_sha256 TEXT NOT NULL, evidence_paths TEXT NOT NULL, permanent INTEGER NOT NULL)")
    connection.execute("CREATE TABLE lifecycle_heads(run_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, event_sha256 TEXT NOT NULL)")
    connection.execute("CREATE TABLE tombstones(run_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, workflow_sha256 TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, last_bundle_sha256 TEXT NOT NULL, purged_utc TEXT NOT NULL, tombstone_sha256 TEXT NOT NULL)")
    connection.execute("CREATE TABLE reservations(reservation_id TEXT PRIMARY KEY, run_id TEXT, reserved_bytes INTEGER NOT NULL, expires_utc TEXT NOT NULL)")


@dataclass
class _Found:
    run_id: str
    workflow_id: str = ""
    workflow_sha256: str = ""
    receipt_sha256: str = ""
    hot_path: str | None = None
    archive_path: str | None = None
    trash_path: str | None = None
    logical_bytes: int = 0
    allocated_bytes: int = 0
    archive_bytes: int = 0
    allocated_estimated: bool = False
    created_utc: str = ""
    blockers: set[str] | None = None

    def __post_init__(self) -> None:
        if self.blockers is None:
            self.blockers = set()


def _discover_runs(
    hot_root: Path,
    archive_root: Path,
    hot_registry: Mapping[tuple[str, str], Callable[[Path], bool]],
    archive_registry: Mapping[tuple[str, str], EvidenceVerifier],
    archive_allocations,
) -> tuple[dict[str, _Found], set[str]]:
    rows: dict[str, _Found] = {}
    global_blockers: set[str] = set()
    for entry in _safe_entries(hot_root, "hot root"):
        if entry.name == ".runtime-v03":
            if not stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
                global_blockers.add("runtime_coordinator_invalid")
            continue
        if not stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
            global_blockers.add("unknown_hot_root_entry")
            continue
        directory = Path(entry.path)
        try:
            inventory = inventory_tree(directory, confinement_root=hot_root)
            workflow = _identity_json(directory / "workflow.json")
            run_id = _run_id(workflow.get("run_id"))
            workflow_id = workflow.get("workflow_id")
            artifact_version = workflow.get("artifact_version", workflow.get("workflow_version"))
            if not isinstance(workflow_id, str) or not workflow_id or not isinstance(artifact_version, str) or not artifact_version:
                raise ValueError("workflow identity fields invalid")
            item = rows.setdefault(run_id, _Found(run_id))
            verifier = hot_registry.get((workflow_id, artifact_version))
            if verifier is None:
                _bind_hot_quarantine(item, directory, workflow, inventory)
                item.blockers.add("legacy_archive_ineligible" if artifact_version in {"0.1", "0.2"} else "workflow_verifier_unavailable")
                continue
            workflow = _canonical_json(directory / "workflow.json")
            if verifier(directory) is not True:
                raise ValueError("hot source verifier rejected run")
            _bind_hot(item, directory, workflow, inventory)
        except Exception:
            guessed = _name_run_id(directory.name)
            if guessed is not None:
                rows.setdefault(guessed, _Found(guessed)).blockers.add("hot_carrier_invalid")
            else:
                global_blockers.add("unidentified_hot_carrier")
    for entry in _safe_entries(archive_root, "archive root"):
        if not entry.name.endswith(".sqrun") or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
            global_blockers.add("unknown_archive_root_entry")
            continue
        carrier = Path(entry.path)
        try:
            bundle = verify_sqrun(carrier, verifier_registry=archive_registry, require_source_verified=False)
            item = rows.setdefault(bundle.run_id, _Found(bundle.run_id))
            allocation = archive_allocations.get(carrier.name)
            if allocation is None:
                raise ValueError("archive allocation missing")
            workflow_raw = ZipEvidenceReader(carrier, bundle.entries, ArchiveLimits(max_metadata_bytes=64 * 1024)).read_bytes("workflow.json", maximum_bytes=64 * 1024)
            workflow = _identity_json_bytes(workflow_raw)
            if _run_id(workflow.get("run_id")) != bundle.run_id or workflow.get("workflow_id") != bundle.workflow_id:
                raise ValueError("archive workflow identity invalid")
            if not bundle.source_verified:
                _bind_archive_quarantine(item, carrier, bundle, workflow, allocation)
                item.blockers.add("legacy_archive_ineligible" if workflow.get("artifact_version") in {"0.1", "0.2"} else "workflow_verifier_unavailable")
                continue
            workflow = _canonical_json_bytes(workflow_raw)
            _bind_archive(item, carrier, bundle.workflow_id, bundle.workflow_sha256, bundle.receipt_sha256, _archive_created_utc(carrier, bundle), bundle.logical_bytes, allocation)
        except Exception:
            guessed = _name_run_id(carrier.stem)
            if guessed is not None:
                rows.setdefault(guessed, _Found(guessed)).blockers.add("archive_carrier_invalid")
            else:
                global_blockers.add("unidentified_archive_carrier")
    return rows, global_blockers


def _bind_hot(item: _Found, path: Path, workflow: Mapping[str, object], inventory) -> None:
    workflow_sha = _sha_file(path / "workflow.json")
    receipt_sha = _sha_file(path / "receipt.json")
    workflow_id = workflow.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        raise ValueError("workflow identity invalid")
    if item.hot_path is not None or (item.workflow_sha256 and item.workflow_sha256 != workflow_sha):
        item.blockers.add("duplicate_run_conflict")
    _bind_created_utc(item, _workflow_created_utc(workflow))
    item.workflow_id, item.workflow_sha256, item.receipt_sha256 = workflow_id, workflow_sha, receipt_sha
    item.hot_path, item.logical_bytes, item.allocated_bytes = str(path), inventory.logical_bytes, inventory.allocated_bytes
    item.allocated_estimated = inventory.allocated_estimated


def _bind_hot_quarantine(item: _Found, path: Path, workflow: Mapping[str, object], inventory) -> None:
    if item.hot_path is not None:
        item.blockers.add("duplicate_run_conflict")
    item.workflow_id = workflow.get("workflow_id") if isinstance(workflow.get("workflow_id"), str) else ""
    item.hot_path, item.logical_bytes, item.allocated_bytes = str(path), inventory.logical_bytes, inventory.allocated_bytes
    item.allocated_estimated = inventory.allocated_estimated
    try:
        item.workflow_sha256, item.receipt_sha256 = _sha_file(path / "workflow.json"), _sha_file(path / "receipt.json")
        _bind_created_utc(item, _workflow_created_utc(workflow))
    except Exception:
        item.blockers.add("hot_carrier_invalid")


def _bind_archive(item: _Found, path: Path, workflow_id: str, workflow_sha: str, receipt_sha: str, created_utc: str, logical: int, allocation) -> None:
    if item.archive_path is not None or (item.workflow_sha256 and item.workflow_sha256 != workflow_sha):
        item.blockers.add("duplicate_run_conflict")
    if not item.workflow_id:
        item.workflow_id, item.workflow_sha256, item.receipt_sha256 = workflow_id, workflow_sha, receipt_sha
    _bind_created_utc(item, created_utc)
    archive_raw_sha256(path)
    item.archive_path, item.archive_bytes = str(path), allocation.logical_bytes
    if not item.hot_path:
        item.allocated_bytes, item.allocated_estimated = allocation.allocated_bytes, allocation.allocated_estimated
    if not item.logical_bytes:
        item.logical_bytes = logical


def _bind_archive_quarantine(item: _Found, path: Path, bundle, workflow: Mapping[str, object], allocation) -> None:
    if item.archive_path is not None:
        item.blockers.add("duplicate_run_conflict")
    if item.workflow_sha256 and item.workflow_sha256 != bundle.workflow_sha256:
        item.blockers.add("duplicate_run_conflict")
    if not item.workflow_id:
        item.workflow_id, item.workflow_sha256, item.receipt_sha256 = bundle.workflow_id, bundle.workflow_sha256, bundle.receipt_sha256
    item.archive_path, item.archive_bytes = str(path), allocation.logical_bytes
    if not item.hot_path:
        item.allocated_bytes, item.allocated_estimated = allocation.allocated_bytes, allocation.allocated_estimated
    if not item.logical_bytes:
        item.logical_bytes = bundle.logical_bytes
    try:
        _bind_created_utc(item, _workflow_created_utc(workflow))
    except Exception:
        item.blockers.add("archive_carrier_invalid")


def _discover_trash(root: Path, rows: dict[str, _Found], hot_registry, archive_registry) -> None:
    for entry in _safe_entries(root, "trash root"):
        if not stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
            raise StorageError("trash root contains an unknown entry")
        directory = Path(entry.path)
        try:
            _safe_directory(directory, "trash carrier")
            record = _validate_trash_record(_canonical_trash_json(directory / "trash-record.json"))
            run_id = record["run_id"]
            if directory.name != run_id:
                raise ValueError("trash directory does not match run_id")
            alias = record["original_carrier_alias"]
            if (record["payload_kind"] == "hot_directory" and not _is_hot_alias(alias)) or (record["payload_kind"] == "sqrun" and alias != f"{run_id}.sqrun"):
                raise ValueError("trash carrier alias does not match kind")
            payload = directory / "payload"
            if record["payload_kind"] == "hot_directory":
                _safe_directory(payload, "trash payload")
                inventory = inventory_tree(payload, confinement_root=directory)
                workflow = _canonical_json(payload / "workflow.json")
                verifier = hot_registry.get((workflow.get("workflow_id"), workflow.get("artifact_version")))
                if verifier is None or verifier(payload) is not True:
                    raise ValueError("trash hot verifier unavailable")
                payload_sha, logical = _tree_sha(payload, inventory), inventory.logical_bytes
                workflow_sha, receipt_sha = _sha_file(payload / "workflow.json"), _sha_file(payload / "receipt.json")
                if workflow.get("workflow_id") != record["workflow_id"]:
                    raise ValueError("trash workflow_id binding invalid")
                created_utc = _workflow_created_utc(workflow)
            else:
                payload_info = _regular_file_info(payload, "trash sqrun payload")
                bundle = verify_sqrun(payload, verifier_registry=archive_registry, require_source_verified=True)
                payload_sha, logical = archive_raw_sha256(payload), bundle.logical_bytes
                workflow_sha, receipt_sha = bundle.workflow_sha256, bundle.receipt_sha256
                if bundle.run_id != run_id:
                    raise ValueError("trash sqrun identity invalid")
                created_utc = _archive_created_utc(payload, bundle)
                allocated, estimated = _allocated_bytes(payload, payload_info, _windows_cluster_size(payload.parent) if os.name == "nt" else None)
            if record["payload_sha256"] != payload_sha or record["payload_logical_bytes"] != logical or record["workflow_sha256"] != workflow_sha or record["receipt_sha256"] != receipt_sha:
                raise ValueError("trash payload binding invalid")
            item = rows.setdefault(run_id, _Found(run_id))
            if item.trash_path is not None or item.hot_path is not None or item.archive_path is not None:
                item.blockers.add("duplicate_run_conflict")
            item.trash_path = str(payload)
            item.workflow_id, item.workflow_sha256, item.receipt_sha256 = record["workflow_id"], workflow_sha, receipt_sha
            _bind_created_utc(item, created_utc)
            item.logical_bytes = logical
            if record["payload_kind"] == "hot_directory":
                item.allocated_bytes, item.allocated_estimated = inventory.allocated_bytes, inventory.allocated_estimated
            else:
                item.allocated_bytes, item.allocated_estimated = allocated, estimated
        except Exception:
            guessed = _name_run_id(directory.name)
            if guessed is not None:
                rows.setdefault(guessed, _Found(guessed)).blockers.add("trash_carrier_invalid")
            else:
                raise StorageError("unidentified trash carrier")


def _insert_discovered(connection, rows, references, references_unknown, heads, lifecycle_unknown, global_blockers) -> None:
    known = set(rows)
    unknown_reference = references_unknown or any(reference.run_id not in known for reference in references)
    by_run: dict[str, list[CatalogReference]] = {}
    for reference in references:
        by_run.setdefault(reference.run_id, []).append(reference)
    for run_id, item in sorted(rows.items()):
        if lifecycle_unknown:
            item.blockers.add("lifecycle_unknown")
        item.blockers.update(global_blockers)
        carrier_state = "archived_duplicate" if item.hot_path and item.archive_path else "hot" if item.hot_path else "archived" if item.archive_path else "trash"
        head = heads.get(run_id)
        if head is not None and (head.pending_event_type is not None or (head.state is not None and head.state != carrier_state)):
            item.blockers.add("lifecycle_carrier_conflict")
        state = "invalid" if item.blockers else carrier_state
        preference = "hot" if item.hot_path else "archive" if item.archive_path else "trash" if item.trash_path else None
        ref_status = "unknown" if unknown_reference else "protected" if run_id in by_run else "unreferenced"
        blockers = ";".join(sorted(item.blockers))
        connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, item.workflow_id, item.workflow_sha256, item.receipt_sha256, state, item.hot_path, item.archive_path, item.trash_path, preference, item.logical_bytes, item.allocated_bytes, item.archive_bytes, int(item.allocated_estimated), blockers, item.created_utc))
        connection.execute("INSERT INTO retention VALUES (?,?,?,?,?)", (run_id, int(heads.get(run_id).manual_keep) if run_id in heads else 0, 0, ref_status, None))
        for reference in by_run.get(run_id, []):
            connection.execute("INSERT INTO 'references' VALUES (?,?,?,?,?,?,?)", (reference.run_id, reference.reference_type, reference.object_id, reference.evidence_path, reference.evidence_sha256, json.dumps(reference.evidence_paths), int(reference.permanent)))
        if run_id in heads:
            head = heads[run_id]
            connection.execute("INSERT INTO lifecycle_heads VALUES (?,?,?)", (head.run_id, head.sequence, head.event_sha256))


def _resolve_references(adapter) -> tuple[tuple[CatalogReference, ...], bool]:
    if adapter is None:
        return (), False
    try:
        graph = adapter.resolve() if hasattr(adapter, "resolve") else adapter()
        if not hasattr(graph, "references") or type(graph.scan_incomplete) is not bool or not isinstance(graph.blockers, tuple):
            raise ValueError("reference adapter did not return a typed graph")
        result = tuple(
            value if isinstance(value, CatalogReference) else CatalogReference(
                value.run_id, value.reference_type, value.recommendation_id or "", value.source_path,
                value.source_sha256, tuple(value.evidence_paths), value.reference_type == "applied_audit",
            )
            for value in graph.references
        )
        for value in result:
            _validate_reference(value)
        return result, graph.scan_incomplete or bool(graph.blockers)
    except Exception:
        return (), True


def _resolve_heads(root: Path, adapter) -> tuple[Mapping[str, CatalogLifecycleHead], bool]:
    if adapter is not None:
        try:
            heads = adapter.resolve() if hasattr(adapter, "resolve") else adapter()
            if not isinstance(heads, Mapping):
                raise ValueError("lifecycle adapter did not return a mapping")
            heads = {
                run_id: head if isinstance(head, CatalogLifecycleHead) else CatalogLifecycleHead(
                    head.run_id, head.revision, head.tail_sha256 or "", head.state, head.manual_keep,
                    head.trash_previous_state, head.pending_event_type, head.pending_operation_id,
                )
                for run_id, head in heads.items()
            }
            for run_id, head in heads.items():
                _validate_lifecycle_head(run_id, head)
            return heads, False
        except Exception:
            return {}, True
    heads: dict[str, CatalogLifecycleHead] = {}
    try:
        layout = root / "lifecycle"
        if not layout.exists():
            if any(True for _ in _safe_entries(root, "lifecycle root")):
                raise ValueError("unknown lifecycle root entry")
            return heads, False
        layout = _safe_directory(layout, "lifecycle directory")
        from sqvm.storage.lifecycle import read_head
        for entry in _safe_entries(layout, "lifecycle directory"):
            if not stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
                raise ValueError("unknown lifecycle entry")
            run_id = _run_id(entry.name)
            source = read_head(root, run_id)
            head = CatalogLifecycleHead(run_id, source.revision, source.tail_sha256 or "", source.state, source.manual_keep, source.trash_previous_state, source.pending_event_type, source.pending_operation_id)
            _validate_lifecycle_head(run_id, head)
            heads[run_id] = head
        return heads, False
    except Exception:
        return {}, True


def _validate_lifecycle_head(run_id: object, head: object) -> None:
    if not isinstance(head, CatalogLifecycleHead) or _run_id(run_id) != head.run_id or type(head.sequence) is not int or head.sequence < 0:
        raise ValueError("lifecycle head invalid")
    if head.sequence == 0:
        if head.event_sha256 not in {"", None} or head.state is not None:
            raise ValueError("empty lifecycle head invalid")
    elif not _is_sha(head.event_sha256):
        raise ValueError("lifecycle tail hash invalid")
    if head.state not in {None, "running", "hot", "archiving", "archived_duplicate", "archived", "trash", "purging", "purged", "recovery_required"} or type(head.manual_keep) is not bool:
        raise ValueError("lifecycle state invalid")


def _discover_tombstones(root: Path) -> tuple[Mapping[str, object], ...]:
    rows = []
    for entry in _safe_entries(root, "tombstone root"):
        if not entry.name.endswith(".json") or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
            raise StorageError("tombstone root contains an unknown entry")
        try:
            path = Path(entry.path)
            _regular_file_info(path, "tombstone")
            value = _validate_tombstone(_canonical_json(path))
            if path.name != f"{value['run_id']}.json":
                raise ValueError("tombstone filename does not match run_id")
            rows.append(value)
        except Exception as exc:
            raise StorageError("tombstone input is invalid") from exc
    return tuple(rows)


def _insert_tombstones(connection, rows) -> None:
    for row in rows:
        connection.execute("INSERT INTO tombstones VALUES (?,?,?,?,?,?,?)", tuple(row[key] for key in ("run_id", "workflow_id", "workflow_sha256", "receipt_sha256", "last_bundle_sha256", "purged_utc", "tombstone_sha256")))


def _validate_database(connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise StorageError("catalog integrity check failed")


def _catalog_run(row, references: tuple[CatalogReference, ...], revision: int) -> CatalogRun:
    blockers = tuple(filter(None, row[13].split(";")))
    manual_keep, reference_status, delete_after = bool(row[15]), row[16], row[17]
    actions_by_state = {
        "hot": ("archive", "trash", "keep"),
        "archived": ("restore_hot", "trash", "keep"),
        "archived_duplicate": ("cleanup_duplicate", "keep"),
        "trash": ("restore", "keep"),
    }
    actions = actions_by_state.get(row[4], ()) if not blockers else ()
    if manual_keep or reference_status in {"protected", "unknown"}:
        actions = tuple(action for action in actions if action != "trash")
    carrier_path = row[5] or row[6] or row[7]
    carrier = {
        "kind": "hot_directory" if row[5] else "sqrun" if row[6] else "trash",
        "alias": Path(carrier_path).name if carrier_path else None,
        "read_preference": row[8], "archive_bytes": row[11], "allocated_estimated": bool(row[12]),
    }
    return CatalogRun(
        row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8], row[9], row[10], row[11],
        reference_status, manual_keep, blockers, actions, created_utc=row[14], carrier=carrier, references=references, delete_after_utc=delete_after,
        catalog_revision=revision,
    )


def _safe_directory(value: str | Path, label: str) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    anchor, current = Path(path.anchor), None
    if not anchor.anchor:
        raise StorageError(f"{label} is not absolute")
    try:
        info = anchor.lstat()
    except OSError as exc:
        raise StorageError(f"{label} cannot be inspected") from exc
    if _is_link_or_reparse(anchor, info):
        raise StorageError(f"{label} is linked or reparse-backed")
    current = anchor
    for part in path.parts[1:]:
        matches = [entry for entry in os.scandir(current) if entry.name == part]
        if len(matches) != 1:
            raise StorageError(f"{label} component is missing or has incorrect case")
        entry, candidate = matches[0], current / matches[0].name
        info = entry.stat(follow_symlinks=False)
        if _is_link_or_reparse(candidate, info):
            raise StorageError(f"{label} contains a linked or reparse-backed component")
        if not stat.S_ISDIR(info.st_mode):
            raise StorageError(f"{label} is not a directory")
        current = candidate
    return current


def _safe_catalog_path(value: str | Path) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    parent = _ensure_safe_directory(path.parent, "catalog parent")
    target = parent / path.name
    if os.path.lexists(target):
        info = target.lstat()
        if _is_link_or_reparse(target, info) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise StorageError("catalog target is linked or unsafe")
    return target


def _ensure_safe_directory(value: str | Path, label: str) -> Path:
    """Create missing catalog-parent components without ever traversing a link."""

    path = Path(os.path.abspath(os.fspath(value)))
    anchor = Path(path.anchor)
    if not anchor.anchor:
        raise StorageError(f"{label} is not absolute")
    try:
        info = anchor.lstat()
    except OSError as exc:
        raise StorageError(f"{label} root cannot be inspected") from exc
    if _is_link_or_reparse(anchor, info):
        raise StorageError(f"{label} is linked or reparse-backed")
    current = anchor
    for part in path.parts[1:]:
        try:
            matches = [entry for entry in os.scandir(current) if entry.name == part]
        except OSError as exc:
            raise StorageError(f"{label} component cannot be inspected") from exc
        if not matches:
            candidate = current / part
            try:
                candidate.mkdir()
                info = candidate.lstat()
            except OSError as exc:
                raise StorageError(f"{label} component cannot be created") from exc
        elif len(matches) == 1:
            candidate = current / matches[0].name
            try:
                info = matches[0].stat(follow_symlinks=False)
            except OSError as exc:
                raise StorageError(f"{label} component cannot be inspected") from exc
        else:
            raise StorageError(f"{label} component is ambiguous")
        if _is_link_or_reparse(candidate, info) or not stat.S_ISDIR(info.st_mode):
            raise StorageError(f"{label} contains a linked or non-directory component")
        current = candidate
    return current


def _safe_existing_catalog(value: str | Path) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    parent = _safe_directory(path.parent, "catalog parent")
    path = parent / path.name
    try:
        info = _regular_file_info(path, "catalog target")
    except StorageError as exc:
        raise StorageError("catalog is missing; rebuild is required") from exc
    if not stat.S_ISREG(info.st_mode):
        raise StorageError("catalog is missing; rebuild is required")
    return path


def _safe_entries(root: Path, label: str):
    try:
        entries = sorted(os.scandir(root), key=lambda item: item.name.encode("utf-8"))
    except OSError as exc:
        raise StorageError(f"{label} cannot be scanned") from exc
    for entry in entries:
        path = Path(entry.path)
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise StorageError(f"{label} entry cannot be inspected") from exc
        if _is_link_or_reparse(path, info):
            raise StorageError(f"{label} contains a linked or reparse-backed entry")
        yield entry


def _canonical_json(path: Path) -> Mapping[str, object]:
    raw, _identity = _read_regular_bytes_no_follow(path, "canonical JSON", limit=64 * 1024)
    return _canonical_json_bytes(raw)


def _canonical_trash_json(path: Path) -> Mapping[str, object]:
    raw, _identity = _read_regular_bytes_no_follow(path, "trash record JSON", limit=64 * 1024)
    value = _identity_json_bytes(raw)
    if raw != _compact_json_bytes(value):
        raise ValueError("noncanonical trash record")
    return value


def _identity_json(path: Path) -> Mapping[str, object]:
    raw, _identity = _read_regular_bytes_no_follow(path, "workflow identity JSON", limit=64 * 1024)
    return _identity_json_bytes(raw)


def _identity_json_bytes(raw: bytes) -> Mapping[str, object]:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    if not isinstance(value, dict):
        raise ValueError("workflow identity is not an object")
    return value


def _canonical_json_bytes(raw: bytes) -> Mapping[str, object]:
    value = _identity_json_bytes(raw)
    if not isinstance(value, dict) or raw != canonical_archive_json_bytes(value):
        raise ValueError("noncanonical input")
    return value


def _hash_document(value: Mapping[str, object], hash_field: str) -> str:
    return hashlib.sha256(canonical_archive_json_bytes({key: item for key, item in value.items() if key != hash_field})).hexdigest().upper()


def _compact_json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _hash_trash_document(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_compact_json_bytes({key: item for key, item in value.items() if key != "record_sha256"})).hexdigest().upper()


def _tree_sha(root: Path, inventory) -> str:
    # Trash records are authored by the frozen operations carrier contract.
    # Reuse its no-follow tree digest so a future encoding change cannot split
    # record publication from catalog verification.
    from sqvm.storage.operations import _carrier_sha
    try:
        return _carrier_sha(root, "hot_directory")
    except Exception as exc:
        raise StorageError("trash payload tree is unsafe") from exc


def _sha_file(path: Path) -> str:
    raw, _identity = _read_regular_bytes_no_follow(path, "hashed file", limit=8 * 1024 * 1024)
    return hashlib.sha256(raw).hexdigest().upper()


def _run_id(value: object) -> str:
    parsed = uuid.UUID(str(value))
    if not isinstance(value, str) or str(parsed) != value:
        raise ValueError("run_id invalid")
    return value


def _name_run_id(name: str) -> str | None:
    from sqvm.storage.workflow_verifiers import hot_alias_prefixes
    try:
        return _run_id(name)
    except Exception:
        pass
    for prefix in hot_alias_prefixes():
        if name.startswith(prefix):
            try:
                return _run_id(name.removeprefix(prefix))
            except Exception:
                return None
    return None


def _is_sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256


def _valid_utc(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    formats = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ")
    for pattern in formats:
        try:
            parsed = datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        rendered = parsed.strftime(pattern)
        if rendered == value:
            return True
    return False


def _valid_alias(value: object) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).name == value and value not in {".", ".."} and ":" not in value


def _is_hot_alias(value: object) -> bool:
    from sqvm.storage.workflow_verifiers import valid_hot_alias
    return _valid_alias(value) and valid_hot_alias(value) and all(ord(char) >= 32 and ord(char) != 127 for char in value)


def _workflow_created_utc(workflow: Mapping[str, object]) -> str:
    value = workflow.get("created_utc")
    if not _valid_utc(value):
        raise ValueError("workflow created_utc is invalid")
    return value


def _archive_created_utc(path: Path, bundle) -> str:
    reader = ZipEvidenceReader(path, bundle.entries, ArchiveLimits(max_metadata_bytes=64 * 1024))
    raw = reader.read_bytes("workflow.json", maximum_bytes=64 * 1024)
    return _workflow_created_utc(_canonical_json_bytes(raw))


def _bind_created_utc(item: _Found, value: str) -> None:
    if item.created_utc and item.created_utc != value:
        item.blockers.add("created_utc_carrier_conflict")
    elif not item.created_utc:
        item.created_utc = value


def _validate_trash_record(value: Mapping[str, object]) -> Mapping[str, object]:
    hashes = ("workflow_sha256", "receipt_sha256", "original_carrier_sha256", "payload_sha256", "trash_started_event_sha256", "record_sha256")
    if set(value) != _TRASH_FIELDS:
        raise ValueError("trash record fields invalid")
    if value.get("schema_version") != "0.1" or value.get("artifact_type") != "sqvm_experiment_trash_record" or value.get("artifact_version") != "0.1":
        raise ValueError("trash record version invalid")
    run_id = _run_id(value.get("run_id"))
    if _run_id(value.get("operation_id")) != value["operation_id"] or not isinstance(value.get("workflow_id"), str) or not value["workflow_id"]:
        raise ValueError("trash record identity invalid")
    if not all(isinstance(value.get(key), str) and _is_sha(value[key]) for key in hashes):
        raise ValueError("trash record hash invalid")
    if not isinstance(value.get("actor_id"), str) or not value["actor_id"].strip() or not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("trash record actor or reason invalid")
    if value.get("previous_storage_state") not in {"hot", "archived", "archived_duplicate"}:
        raise ValueError("trash previous state invalid")
    kind = value.get("payload_kind")
    if kind not in {"hot_directory", "sqrun"} or value.get("original_carrier_kind") != kind or not _valid_alias(value.get("original_carrier_alias")):
        raise ValueError("trash carrier invalid")
    if type(value.get("payload_logical_bytes")) is not int or value["payload_logical_bytes"] < 0:
        raise ValueError("trash byte count invalid")
    if not _valid_utc(value.get("trashed_utc")) or not _valid_utc(value.get("purge_after_utc")) or value["purge_after_utc"] <= value["trashed_utc"]:
        raise ValueError("trash timestamps invalid")
    if value["record_sha256"] != _hash_trash_document(value):
        raise ValueError("trash record hash mismatch")
    if run_id != value["run_id"]:
        raise ValueError("trash run_id invalid")
    return value


def _validate_tombstone(value: Mapping[str, object]) -> Mapping[str, object]:
    hashes = ("workflow_sha256", "receipt_sha256", "last_bundle_sha256", "last_lifecycle_event_sha256", "tombstone_sha256")
    if set(value) != _TOMBSTONE_FIELDS:
        raise ValueError("tombstone fields invalid")
    if value.get("schema_version") != "0.1" or value.get("artifact_type") != "sqvm_experiment_tombstone" or value.get("artifact_version") != "0.1":
        raise ValueError("tombstone version invalid")
    _run_id(value.get("run_id"))
    if not isinstance(value.get("workflow_id"), str) or not value["workflow_id"] or not isinstance(value.get("actor_id"), str) or not value["actor_id"].strip() or not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("tombstone identity invalid")
    if not all(isinstance(value.get(key), str) and _is_sha(value[key]) for key in hashes):
        raise ValueError("tombstone hash invalid")
    if not _valid_utc(value.get("original_created_utc")) or not _valid_utc(value.get("purged_utc")) or value["purged_utc"] < value["original_created_utc"]:
        raise ValueError("tombstone timestamps invalid")
    if value["tombstone_sha256"] != _hash_document(value, "tombstone_sha256"):
        raise ValueError("tombstone hash mismatch")
    return value


def _validate_reference(value: CatalogReference) -> None:
    if value.reference_type not in {"current_configuration", "draft_configuration", "snapshot_configuration", "active_snapshot", "accepted_decision", "applied_audit", "derived_experiment", "manual_keep"} or not _is_sha(value.evidence_sha256):
        raise StorageError("reference adapter returned invalid data")
    _run_id(value.run_id)


def _existing_revision(path: Path) -> int:
    if not os.path.lexists(path):
        return 0
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = connection.execute("SELECT value FROM catalog_meta WHERE key='revision'").fetchone()
            if row is None or len(row) != 1 or not isinstance(row[0], str) or not row[0].isdigit() or row[0].startswith("0"):
                raise ValueError("catalog revision is malformed")
            return int(row[0])
        finally:
            connection.close()
    except (sqlite3.DatabaseError, OSError, ValueError, TypeError, IndexError) as exc:
        raise StorageError("existing catalog revision is unreadable") from exc


def _ledger_path(catalog: Path) -> Path:
    return catalog.parent / f".{catalog.name}.revision-ledger"


def _ledger_revision(catalog: Path) -> int:
    path = _ledger_path(catalog)
    try:
        path.lstat()
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise StorageError("catalog revision ledger cannot be inspected") from exc
    raw, _identity = _read_regular_bytes_no_follow(path, "catalog revision ledger")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise StorageError("catalog revision ledger is malformed")
    number = raw[:-1]
    if not number or number[:1] == b"0" or not number.isascii() or not number.isdigit():
        raise StorageError("catalog revision ledger is malformed")
    try:
        return int(number.decode("ascii"))
    except ValueError as exc:  # pragma: no cover - guarded above, kept fail-closed.
        raise StorageError("catalog revision ledger is malformed") from exc


def _write_ledger_revision(catalog: Path, revision: int) -> None:
    if revision <= _ledger_revision(catalog):
        raise StorageError("catalog revision is not monotonic")
    target = _ledger_path(catalog)
    temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="ascii", newline="\n") as stream:
            stream.write(f"{revision}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if _ledger_revision(catalog) != revision:
            raise StorageError("catalog revision ledger identity changed")
        _fsync_directory(target.parent)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise StorageError("catalog revision ledger write failed") from exc


@dataclass(frozen=True, slots=True)
class _RebuildLock:
    path: Path
    identity: tuple[int, int]
    token: str


def _acquire_rebuild_lock(catalog: Path) -> _RebuildLock:
    path = catalog.parent / f".{catalog.name}.rebuild.lock"
    token = uuid.uuid4().hex
    payload = f"{os.getpid()}:{token}\n".encode("ascii")
    try:
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        raw, identity = _read_regular_bytes_no_follow(path, "catalog rebuild lock")
    except FileExistsError as exc:
        # An existing malformed, linked, or unreadable lock is unsafe rather
        # than a normal optimistic conflict.
        _read_lock_payload(path)
        raise StorageError("catalog rebuild conflict") from exc
    except OSError as exc:
        raise StorageError("catalog rebuild lock cannot be created") from exc
    if raw != payload:
        raise StorageError("catalog rebuild lock identity changed")
    return _RebuildLock(path, identity, token)


def _release_rebuild_lock(lock: _RebuildLock) -> None:
    raw, identity = _read_regular_bytes_no_follow(lock.path, "catalog rebuild lock")
    if identity != lock.identity or _read_lock_payload_bytes(raw) != lock.token:
        raise StorageError("catalog rebuild lock owner changed before release")
    try:
        lock.path.unlink()
    except OSError as exc:
        raise StorageError("catalog rebuild lock cannot be released") from exc


def _read_lock_payload(path: Path) -> str:
    raw, _identity = _read_regular_bytes_no_follow(path, "catalog rebuild lock")
    return _read_lock_payload_bytes(raw)


def _read_lock_payload_bytes(raw: bytes) -> str:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise StorageError("catalog rebuild lock is malformed")
    body = raw[:-1]
    try:
        pid, token = body.decode("ascii").split(":")
    except (UnicodeDecodeError, ValueError) as exc:
        raise StorageError("catalog rebuild lock is malformed") from exc
    if not pid.isdecimal() or str(int(pid)) != pid or len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        raise StorageError("catalog rebuild lock is malformed")
    return token


def _regular_file_info(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise StorageError(f"{label} cannot be inspected") from exc
    if _is_link_or_reparse(path, info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise StorageError(f"{label} is linked or unsafe")
    return info


def _read_regular_bytes_no_follow(path: Path, label: str, *, limit: int = 256) -> tuple[bytes, tuple[int, int]]:
    """Read one bounded control file while rejecting links and replacement."""

    try:
        before = _regular_file_info(path, label)
    except StorageError:
        raise
    identity = (before.st_dev, before.st_ino)
    try:
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            raw = stream.read(limit)
            if stream.read(1):
                raise StorageError(f"{label} is malformed")
        after = path.lstat()
    except StorageError:
        raise
    except OSError as exc:
        raise StorageError(f"{label} cannot be read") from exc
    if (
        (opened.st_dev, opened.st_ino) != identity
        or (after.st_dev, after.st_ino) != identity
        or after.st_nlink != 1
        or _is_link_or_reparse(path, after)
    ):
        raise StorageError(f"{label} identity changed")
    return raw, identity


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as stream:
        os.fsync(stream.fileno())


def _checkpoint_database(connection: sqlite3.Connection) -> None:
    """Force the temporary WAL into its main database before publication."""

    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _atomic_replace(temporary: Path, catalog: Path) -> None:
    deadline = time.monotonic() + 2.0
    while True:
        try:
            os.replace(temporary, catalog)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _fsync_directory(path: Path) -> None:
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _remove_temporary(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


__all__ = [
    "CatalogLifecycleHead", "CatalogReference", "CatalogRoots", "CatalogRun", "CatalogStorageSummary",
    "rebuild_catalog", "query_catalog", "storage_summary",
]
