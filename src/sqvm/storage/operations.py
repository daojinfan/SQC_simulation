"""Fail-closed v1 orchestration for experiment archive and recoverable trash.

Roots and verifier registry are fixed when an operations service is constructed.
Callers can supply only a run identity and optimistic request bindings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping
import uuid

from sqvm.storage.archive_format import DirectoryEvidenceReader, write_sqrun
from sqvm.storage.archive_verify import ArchiveLimits, ZipEvidenceReader, verify_sqrun
from sqvm.storage.lifecycle import LifecycleConflict, LifecycleError, append_event, read_head
from sqvm.storage.references import build_reference_graph
from sqvm.storage.workflow_verifiers import archive_evidence_verifier_registry, get_workflow_evidence_verifier, hot_alias_prefixes, valid_hot_alias, valid_hot_alias_for, workflow_hot_verifier_registry


class StorageOperationError(RuntimeError):
    """A storage mutation was rejected or left a recoverable state."""


@dataclass(frozen=True, slots=True)
class StorageMutationRequest:
    actor_id: str
    expected_catalog_revision: int
    expected_workflow_sha256: str
    reason: str


@dataclass(frozen=True, slots=True)
class StorageOperationResult:
    run_id: str
    state: str
    operation_id: str
    carrier: str


def _mutation(method):
    """Normalize storage boundary failures without swallowing cancellation."""
    def wrapped(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except StorageOperationError:
            raise
        except (OSError, LifecycleError, LifecycleConflict, ValueError) as exc:
            raise StorageOperationError("storage mutation failed at a protected filesystem boundary") from exc
    return wrapped


class ExperimentStorageOperations:
    """Trusted-root v1 mutation service.  Purge is deliberately absent."""

    def __init__(
        self,
        *,
        hot_root: str | Path,
        storage_root: str | Path,
        configuration_root: str | Path,
        experiment_output_root: str | Path | None = None,
        archive_root: str | Path | None = None,
        pins_root: str | Path | None = None,
        catalog_revision: int = 0,
        trash_retention_days: int = 7,
    ) -> None:
        if type(catalog_revision) is not int or catalog_revision < 0 or type(trash_retention_days) is not int or trash_retention_days < 1:
            raise StorageOperationError("trusted storage configuration is invalid")
        self._hot_root = _safe_root(hot_root, "hot root", create=False)
        self._storage_root = _safe_root(storage_root, "storage root", create=True)
        self._config_root = _safe_root(configuration_root, "configuration root", create=False)
        self._experiment_root = _safe_root(experiment_output_root or hot_root, "experiment output root", create=False)
        if _contains_path(self._experiment_root, self._storage_root):
            raise StorageOperationError("experiment reference root must not include storage carriers")
        self._archive_root = _safe_root(archive_root or self._storage_root / "archives", "archive root", create=True)
        self._pins_root = _safe_root(pins_root or self._storage_root / "pins", "pins root", create=True)
        self._lifecycle_root = _safe_root(self._storage_root / "lifecycle", "lifecycle root", create=True)
        self._trash_root = _safe_root(self._storage_root / "trash", "trash root", create=True)
        self._tombstones_root = _safe_root(self._storage_root / "tombstones", "tombstone root", create=True)
        self._catalog_revision = catalog_revision
        self._trash_retention_days = trash_retention_days
        self._archive_registry = dict(archive_evidence_verifier_registry())

    @_mutation
    def archive(self, run_id: str, request: StorageMutationRequest) -> StorageOperationResult:
        request = _request(request, self._catalog_revision)
        run_id = _run_id(run_id)
        hot = self._find_hot(run_id, request.expected_workflow_sha256)
        workflow = self._verified_hot(hot, run_id, request.expected_workflow_sha256)
        head = self._head(run_id)
        target = self._archive_root / f"{run_id}.sqrun"
        resumable_publish = head.state == "archiving" and head.pending_event_type == "archive_started" and os.path.lexists(target)
        if (head.pending_event_type is not None and not resumable_publish) or head.state not in {None, "hot", "archiving", "archived_duplicate"}:
            raise StorageOperationError("run is not archivable in its current lifecycle state")
        operation = str(uuid.uuid4())
        result_operation = operation
        if head.state is None:
            self._event(run_id, "hot_discovered", None, "hot", workflow, hot, request, operation)
            head = self._head(run_id)
        if os.path.lexists(target):
            # A process can publish the immutable bundle then fail while
            # appending archive_verified.  Resume that exact pending operation;
            # never rewrite a published bundle.
            if head.state == "archived_duplicate":
                bundle = self._verified_archive(target, run_id, request.expected_workflow_sha256)
            elif head.state == "hot":
                self._event(run_id, "archive_started", "hot", "archiving", workflow, hot, request, operation)
                bundle = self._verified_archive(target, run_id, request.expected_workflow_sha256)
                self._event(run_id, "archive_verified", "archiving", "archived_duplicate", workflow, target, request, operation)
            elif head.state == "archiving":
                bundle = self._verified_archive(target, run_id, request.expected_workflow_sha256)
                self._event(run_id, "archive_verified", "archiving", "archived_duplicate", workflow, target, request, self._head(run_id).pending_operation_id)
            else:
                raise StorageOperationError("archive already exists outside an archiving transition")
        else:
            self._event(run_id, "archive_started", "hot", "archiving", workflow, hot, request, operation)
            staging = self._archive_root / ".archive-staging" / operation
            try:
                _mkdir(staging)
                candidate = staging / f"{run_id}.sqrun"
                verifier = self._source_verifier(workflow)
                write_sqrun(hot, candidate, source_verifier_id=verifier[0], source_verifier_version=verifier[1], source_verifier=verifier[2])
                self._verified_archive(candidate, run_id, request.expected_workflow_sha256)
                self._verified_archive(candidate, run_id, request.expected_workflow_sha256)
                self._verified_hot(hot, run_id, request.expected_workflow_sha256)
                _publish_new(candidate, target)
                bundle = self._verified_archive(target, run_id, request.expected_workflow_sha256)
            except Exception as exc:
                self._failure(run_id, "archive_failed", "archiving", "hot", workflow, hot, request, operation)
                raise StorageOperationError("archive creation failed; hot evidence was retained") from exc
            finally:
                _remove_tree_if_safe(staging)
                try:
                    staging.parent.rmdir()
                    _fsync_directory(staging.parent.parent)
                except FileNotFoundError:
                    pass
                except OSError:
                    # Another archive may own a sibling staging directory.
                    pass
            try:
                self._event(run_id, "archive_verified", "archiving", "archived_duplicate", workflow, target, request, operation)
            except Exception as exc:
                # The bundle is complete and verified, and hot remains intact.
                # Keep the journal in its durable pending state for a retry to
                # resume, rather than falsely closing it as a failed archive.
                raise StorageOperationError("archive published but lifecycle completion is pending; hot duplicate retained") from exc
        if self._head(run_id).state == "archived_duplicate":
            cleanup = str(uuid.uuid4())
            self._event(run_id, "hot_cleanup_started", "archived_duplicate", "archived_duplicate", workflow, hot, request, cleanup)
            try:
                graph = self._fresh_references()  # Authority race gate immediately before destruction.
                if graph.scan_incomplete:
                    raise StorageOperationError("reference graph is incomplete before hot cleanup")
                self._verified_hot(hot, run_id, request.expected_workflow_sha256)
                self._verified_archive(target, run_id, request.expected_workflow_sha256)
                _remove_tree_safe(hot)
            except Exception as exc:
                self._failure(run_id, "hot_cleanup_failed", "archived_duplicate", "archived_duplicate", workflow, target, request, cleanup)
                raise StorageOperationError("archive is verified but hot cleanup failed; duplicate retained") from exc
            self._event(run_id, "hot_cleanup_completed", "archived_duplicate", "archived", workflow, target, request, cleanup)
            result_operation = cleanup
        return StorageOperationResult(run_id, "archived", result_operation, str(target))

    @_mutation
    def restore_hot(self, run_id: str, request: StorageMutationRequest) -> StorageOperationResult:
        request = _request(request, self._catalog_revision); run_id = _run_id(run_id)
        head = self._head(run_id)
        archive = self._archive_root / f"{run_id}.sqrun"
        if head.state == "archiving" and head.pending_event_type == "archive_restore_started":
            target = self._hot_alias_path(self._verified_archive(archive, run_id, request.expected_workflow_sha256).original_directory_name) if os.path.lexists(archive) else self._find_hot(run_id, request.expected_workflow_sha256)
            return self._resume_archive_restore(run_id, request, head, target)
        if head.state != "archived" or head.pending_event_type is not None:
            raise StorageOperationError("only archived runs can be restored hot")
        bundle = self._verified_archive(archive, run_id, request.expected_workflow_sha256)
        target = self._hot_alias_path(bundle.original_directory_name)
        if os.path.lexists(target):
            raise StorageOperationError("hot restore target already exists")
        workflow = {"workflow_sha256": bundle.workflow_sha256}
        operation = str(uuid.uuid4())
        self._event(run_id, "archive_restore_started", "archived", "archiving", workflow, archive, request, operation)
        staging = self._hot_root / f".restore-{operation}"
        try:
            self._extract_archive(archive, bundle, staging)
            self._verified_hot(staging, run_id, request.expected_workflow_sha256, require_alias=False)
            _publish_new(staging, target)
            self._verified_hot(target, run_id, request.expected_workflow_sha256)
            _remove_carrier_safe(archive, "sqrun")
        except Exception as exc:
            if os.path.lexists(target) and os.path.lexists(archive):
                return self._resume_archive_restore(run_id, request, self._head(run_id), target)
            self._failure(run_id, "archive_restore_failed", "archiving", "archived", workflow, archive, request, operation)
            raise StorageOperationError("archive restore failed; archive retained") from exc
        finally:
            _remove_tree_if_safe(staging)
        self._event(run_id, "archive_restore_completed", "archiving", "hot", workflow, target, request, operation)
        return StorageOperationResult(run_id, "hot", operation, str(target))

    def _resume_archive_restore(self, run_id: str, request: StorageMutationRequest, head, target: Path) -> StorageOperationResult:
        archive = self._archive_root / f"{run_id}.sqrun"
        hot_present, archive_present = os.path.lexists(target), os.path.lexists(archive)
        workflow = {"workflow_sha256": request.expected_workflow_sha256}
        operation = head.pending_operation_id
        if hot_present and archive_present:
            self._verified_hot(target, run_id, request.expected_workflow_sha256)
            self._verified_archive(archive, run_id, request.expected_workflow_sha256)
            try:
                _remove_carrier_safe(archive, "sqrun")
            except Exception as archive_error:
                try:
                    _remove_tree_safe(target)
                except Exception as rollback_error:
                    raise StorageOperationError("archive restore duplicate remains pending; archive cleanup and hot rollback both failed") from rollback_error
                self._event(run_id, "archive_restore_failed", "archiving", "archived", workflow, archive, request, operation)
                raise StorageOperationError("archive restore rolled back to archived after archive cleanup failure") from archive_error
            self._event(run_id, "archive_restore_completed", "archiving", "hot", workflow, target, request, operation)
            return StorageOperationResult(run_id, "hot", operation, str(target))
        if hot_present and not archive_present:
            self._verified_hot(target, run_id, request.expected_workflow_sha256)
            self._event(run_id, "archive_restore_completed", "archiving", "hot", workflow, target, request, operation)
            return StorageOperationResult(run_id, "hot", operation, str(target))
        if not hot_present and archive_present:
            self._verified_archive(archive, run_id, request.expected_workflow_sha256)
            self._event(run_id, "archive_restore_failed", "archiving", "archived", workflow, archive, request, operation)
            raise StorageOperationError("archive restore recovery converged to archived")
        raise StorageOperationError("archive restore recovery has no verified carrier")

    @_mutation
    def trash(self, run_id: str, request: StorageMutationRequest) -> StorageOperationResult:
        request = _request(request, self._catalog_revision); run_id = _run_id(run_id)
        graph = self._fresh_references()
        head = self._head(run_id)
        if head.pending_event_type == "trash_started":
            return self._resume_published_trash(run_id, request, head)
        if head.state is None:
            hot = self._find_hot(run_id, request.expected_workflow_sha256)
            workflow = self._verified_hot(hot, run_id, request.expected_workflow_sha256)
            discovered = str(uuid.uuid4())
            self._event(run_id, "hot_discovered", None, "hot", workflow, hot, request, discovered)
            head = self._head(run_id)
        if graph.scan_incomplete or graph.for_run(run_id) or head.manual_keep or head.pending_event_type is not None or head.state not in {"hot", "archived"}:
            raise StorageOperationError("referenced, kept, unknown, or nonterminal run cannot enter trash")
        if head.state == "hot":
            carrier, kind = self._find_hot(run_id, request.expected_workflow_sha256), "hot_directory"
            workflow = self._verified_hot(carrier, run_id, request.expected_workflow_sha256)
        else:
            carrier, kind = self._archive_root / f"{run_id}.sqrun", "sqrun"
            bundle = self._verified_archive(carrier, run_id, request.expected_workflow_sha256)
            workflow = {"workflow_id": bundle.workflow_id, "workflow_sha256": bundle.workflow_sha256, "receipt_sha256": bundle.receipt_sha256}
        operation = str(uuid.uuid4())
        self._event(run_id, "trash_started", head.state, head.state, workflow, carrier, request, operation)
        trash = self._storage_root / "trash" / run_id
        payload = trash / "payload"
        original_sha = _carrier_sha(carrier, kind)
        try:
            _mkdir(trash)
            graph = self._fresh_references()
            if graph.scan_incomplete or graph.for_run(run_id):
                raise StorageOperationError("references changed before trash publication")
            # Re-read both identity and bytes immediately before the destructive
            # move.  The initial verification must not authorize a replacement.
            self._verify_carrier(carrier, kind, run_id, request.expected_workflow_sha256)
            if _carrier_sha(carrier, kind) != original_sha:
                raise StorageOperationError("carrier changed before trash publication")
            copied = _move_or_copy(carrier, payload, kind)
            self._verify_carrier(payload, kind, run_id, request.expected_workflow_sha256)
            record = _trash_record(run_id, workflow, request, operation, head.state, kind, carrier.name, original_sha, payload, kind, self._head(run_id).tail_sha256, self._trash_retention_days)
            _write_new(trash / "trash-record.json", record)
            if copied:
                graph = self._fresh_references()
                if graph.scan_incomplete or graph.for_run(run_id):
                    raise StorageOperationError("references changed before source cleanup")
                self._verify_carrier(carrier, kind, run_id, request.expected_workflow_sha256)
                if _carrier_sha(carrier, kind) != original_sha:
                    raise StorageOperationError("carrier changed before source cleanup")
                _remove_carrier_safe(carrier, kind)
        except Exception as exc:
            rollback_error = _rollback_trash_payload(carrier, payload, trash, kind)
            self._failure(run_id, "trash_failed", head.state, head.state, workflow, carrier if carrier.exists() else payload, request, operation)
            suffix = "" if rollback_error is None else "; recovery duplicate retained"
            raise StorageOperationError(f"trash move failed; source was retained or recoverable{suffix}: {exc}") from exc
        self._event(run_id, "trashed", head.state, "trash", workflow, payload, request, operation, trash_previous_state=head.state)
        return StorageOperationResult(run_id, "trash", operation, str(payload))

    def _resume_published_trash(self, run_id: str, request: StorageMutationRequest, head) -> StorageOperationResult:
        trash = self._storage_root / "trash" / run_id
        record = _read_trash_record(trash / "trash-record.json", run_id)
        if record["operation_id"] != head.pending_operation_id or record["workflow_sha256"] != request.expected_workflow_sha256 or record["previous_storage_state"] != head.state or record["trash_started_event_sha256"] != head.tail_sha256:
            raise StorageOperationError("pending trash publication has no matching recovery record")
        payload = trash / "payload"; kind = record["payload_kind"]
        self._verify_carrier(payload, kind, run_id, request.expected_workflow_sha256)
        if _carrier_sha(payload, kind) != record["payload_sha256"]:
            raise StorageOperationError("pending trash payload changed")
        workflow = {"workflow_id": record["workflow_id"], "workflow_sha256": record["workflow_sha256"], "receipt_sha256": record["receipt_sha256"]}
        self._event(run_id, "trashed", head.state, "trash", workflow, payload, request, record["operation_id"], trash_previous_state=head.state)
        return StorageOperationResult(run_id, "trash", record["operation_id"], str(payload))

    @_mutation
    def restore_trash(self, run_id: str, request: StorageMutationRequest) -> StorageOperationResult:
        request = _request(request, self._catalog_revision); run_id = _run_id(run_id)
        head = self._head(run_id)
        if head.state != "trash" or head.pending_event_type is not None:
            raise StorageOperationError("run is not restorable from trash")
        trash = self._storage_root / "trash" / run_id
        record = _read_trash_record(trash / "trash-record.json", run_id)
        if record["workflow_sha256"] != request.expected_workflow_sha256:
            raise StorageOperationError("trash workflow hash changed")
        kind = record["payload_kind"]; payload = trash / "payload"
        self._verify_carrier(payload, kind, run_id, request.expected_workflow_sha256)
        if _carrier_sha(payload, kind) != record["payload_sha256"]:
            raise StorageOperationError("trash payload hash changed")
        target = self._hot_alias_path(record["original_carrier_alias"]) if kind == "hot_directory" else self._archive_root / f"{run_id}.sqrun"
        if os.path.lexists(target):
            raise StorageOperationError("restore target already exists")
        try:
            copied = _move_or_copy(payload, target, kind)
            self._verify_carrier(target, kind, run_id, request.expected_workflow_sha256)
            if copied:
                _remove_carrier_safe(payload, kind)
            operation = str(uuid.uuid4())
            self._event(run_id, "trash_restored", "trash", record["previous_storage_state"], {"workflow_sha256": record["workflow_sha256"]}, target, request, operation, restored_state=record["previous_storage_state"])
            _remove_tree_if_safe(trash)
        except Exception as exc:
            # While the lifecycle still says trash, retain its verified payload.
            # If the source remains, a newly published target is merely a
            # duplicate and can be removed to make the request safely retryable.
            if os.path.lexists(payload) and os.path.lexists(target):
                try:
                    _remove_carrier_safe(target, kind)
                except Exception:
                    pass
            elif not os.path.lexists(payload) and os.path.lexists(target):
                try:
                    copied_back = _move_or_copy(target, payload, kind)
                    self._verify_carrier(payload, kind, run_id, request.expected_workflow_sha256)
                    if copied_back:
                        _remove_carrier_safe(target, kind)
                except Exception:
                    pass
            raise StorageOperationError("trash restore failed; trash payload was retained or a duplicate is recoverable") from exc
        return StorageOperationResult(run_id, record["previous_storage_state"], operation, str(target))

    @_mutation
    def set_keep(self, run_id: str, keep: bool, request: StorageMutationRequest) -> StorageOperationResult:
        request = _request(request, self._catalog_revision); run_id = _run_id(run_id)
        if type(keep) is not bool:
            raise StorageOperationError("keep must be boolean")
        head = self._head(run_id)
        if head.state is None:
            hot = self._find_hot(run_id, request.expected_workflow_sha256)
            workflow = self._verified_hot(hot, run_id, request.expected_workflow_sha256)
            self._event(run_id, "hot_discovered", None, "hot", workflow, hot, request, str(uuid.uuid4()))
            head = self._head(run_id)
        if head.state not in {"hot", "archived", "archived_duplicate", "trash"} or head.pending_event_type is not None:
            raise StorageOperationError("keep cannot change in current state")
        workflow = self._workflow_for_state(run_id, head.state, request.expected_workflow_sha256)
        pin = self._pins_root / f"{run_id}.json"
        if keep:
            body = {"schema_version": "0.1", "artifact_type": "sqvm_experiment_manual_keep", "artifact_version": "0.1", "run_id": run_id, "workflow_sha256": request.expected_workflow_sha256, "manual_keep": True, "actor_id": request.actor_id, "updated_utc": _utc()}
            body["content_sha256"] = _sha(_canonical(body))
            _write_new(pin, body)
        else:
            if os.path.lexists(pin):
                _remove_file_safe(pin, self._pins_root)
        operation = str(uuid.uuid4())
        self._event(run_id, "keep_changed", head.state, head.state, workflow, self._carrier_for_state(run_id, head.state, request.expected_workflow_sha256), request, operation, manual_keep=keep)
        return StorageOperationResult(run_id, head.state, operation, str(pin))

    def _fresh_references(self):
        return build_reference_graph(configuration_root=self._config_root, experiment_output_root=self._experiment_root, lifecycle_root=self._lifecycle_root, pins_root=self._pins_root)

    def _head(self, run_id: str): return read_head(self._storage_root, run_id)
    def _find_hot(self, run_id: str, expected: str) -> Path:
        """Bind a run identity to exactly one verifier-approved published carrier."""
        candidates: list[Path] = []
        _safe_existing(self._hot_root, self._hot_root)
        try:
            entries = sorted(os.scandir(self._hot_root), key=lambda row: row.name)
            for entry in entries:
                if any(entry.name.lower().startswith(prefix) for prefix in hot_alias_prefixes()) and not valid_hot_alias(entry.name):
                    raise StorageOperationError("hot carrier candidate uses an invalid alias case")
                if not valid_hot_alias(entry.name):
                    continue
                path = Path(entry.path)
                info = path.lstat()
                if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                    raise StorageOperationError("hot carrier candidate is linked or invalid")
                workflow_path = path / "workflow.json"
                workflow_info = workflow_path.lstat()
                if not stat.S_ISREG(workflow_info.st_mode) or workflow_info.st_nlink > 1 or _is_link_or_reparse(workflow_info):
                    raise StorageOperationError("hot carrier candidate workflow is unsafe")
                workflow = _workflow_identity(workflow_path)
                verifier = workflow_hot_verifier_registry().get((workflow.get("workflow_id"), workflow.get("artifact_version")))
                if verifier is None:
                    if workflow.get("run_id") == run_id:
                        raise StorageOperationError("hot carrier is not explicitly registered")
                    continue
                try:
                    verifier(path)
                except Exception as exc:
                    raise StorageOperationError("hot carrier candidate verifier rejected run") from exc
                if workflow.get("run_id") != run_id:
                    continue
                self._verified_hot(path, run_id, expected)
                candidates.append(path)
        except StorageOperationError:
            raise
        except OSError as exc:
            raise StorageOperationError("hot carrier scan could not be completed") from exc
        if len(candidates) != 1:
            raise StorageOperationError("hot carrier identity is missing or ambiguous")
        return candidates[0]

    def _hot_alias_path(self, alias: str) -> Path:
        if not valid_hot_alias(alias):
            raise StorageOperationError("hot carrier alias is invalid")
        return self._hot_root / alias
    def _source_verifier(self, workflow: Mapping[str, Any]):
        value = get_workflow_evidence_verifier(workflow.get("workflow_id"), workflow.get("artifact_version"))
        if value is None: raise StorageOperationError("only registered v0.3 evidence is archivable")
        return value
    def _verified_hot(self, path: Path, run_id: str, expected: str, *, require_alias: bool = True) -> dict[str, Any]:
        _safe_existing(path, path)
        try:
            workflow = json.loads((path / "workflow.json").read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StorageOperationError("workflow cannot be read") from exc
        verifier = workflow_hot_verifier_registry().get((workflow.get("workflow_id"), workflow.get("artifact_version")))
        if verifier is None:
            raise StorageOperationError("hot carrier is not explicitly registered")
        if require_alias and not valid_hot_alias_for(
            workflow.get("workflow_id"), workflow.get("artifact_version"), run_id, path.name
        ):
            raise StorageOperationError("hot carrier alias does not bind run identity")
        try: verifier(path)
        except Exception as exc: raise StorageOperationError("hot carrier verifier rejected run") from exc
        if workflow.get("run_id") != run_id or workflow.get("archive_eligible") is not True or _sha_file(path / "workflow.json") != expected:
            raise StorageOperationError("hot carrier identity is invalid")
        self._source_verifier(workflow)
        return {"workflow_id": workflow["workflow_id"], "artifact_version": workflow["artifact_version"], "workflow_sha256": expected, "receipt_sha256": _sha_file(path / "receipt.json")}
    def _verified_archive(self, path: Path, run_id: str, expected: str):
        _safe_existing(path, path)
        try: bundle = verify_sqrun(path, verifier_registry=self._archive_registry, require_source_verified=True)
        except Exception as exc: raise StorageOperationError("archive verifier rejected carrier") from exc
        if bundle.run_id != run_id or bundle.workflow_sha256 != expected or get_workflow_evidence_verifier(bundle.workflow_id, bundle.source_artifact_version) is None: raise StorageOperationError("archive identity is invalid")
        return bundle
    def _verify_carrier(self, path: Path, kind: str, run: str, expected: str):
        # Trash payloads are intentionally named ``payload``; semantic
        # evidence still verifies, while final hot aliases are checked at the
        # hot discovery/publication boundary.
        return self._verified_hot(path, run, expected, require_alias=path.name != "payload") if kind == "hot_directory" else self._verified_archive(path, run, expected)
    def _carrier_for_state(self, run: str, state: str, expected: str) -> Path:
        if state in {"hot", "archived_duplicate"}: return self._find_hot(run, expected)
        if state == "archived": return self._archive_root / f"{run}.sqrun"
        return self._storage_root / "trash" / run / "payload"
    def _workflow_for_state(self, run: str, state: str, expected: str):
        carrier = self._carrier_for_state(run, state, expected)
        kind = "hot_directory" if state in {"hot", "archived_duplicate"} else "sqrun" if state == "archived" else _read_trash_record(self._storage_root / "trash" / run / "trash-record.json", run)["payload_kind"]
        verified = self._verify_carrier(carrier, kind, run, expected)
        if kind == "sqrun":
            return {"workflow_id": verified.workflow_id, "workflow_sha256": verified.workflow_sha256, "receipt_sha256": verified.receipt_sha256}
        return verified
    def _event(self, run, event, before, after, workflow, carrier, request, operation, **extra):
        head = self._head(run); carrier_hash = _carrier_sha(carrier, "hot_directory" if stat.S_ISDIR(carrier.lstat().st_mode) else "sqrun")
        payload = {"from_state": before, "to_state": after, "workflow_sha256": workflow["workflow_sha256"], "carrier_path": carrier.name, "carrier_sha256": carrier_hash, "catalog_revision": request.expected_catalog_revision, **extra}
        try: append_event(self._storage_root, run, event, payload, actor_id=request.actor_id, operation_id=operation, expected_revision=head.revision, expected_tail_sha256=head.tail_sha256)
        except (LifecycleError, LifecycleConflict, OSError) as exc: raise StorageOperationError("lifecycle concurrency conflict") from exc
    def _failure(self, run, event, before, after, workflow, carrier, request, operation):
        try: self._event(run, event, before, after, workflow, carrier, request, operation)
        except Exception: pass
    def _extract_archive(self, archive: Path, bundle, target: Path):
        _mkdir(target)
        reader = ZipEvidenceReader(archive, bundle.entries, ArchiveLimits())
        for entry in bundle.entries:
            destination = target / entry.path; _mkdir(destination.parent)
            with reader.open_binary(entry.path) as source, destination.open("xb") as sink:
                remaining = entry.byte_length
                while remaining:
                    data = source.read(min(64 * 1024, remaining))
                    if not data: raise StorageOperationError("archive entry truncated during restore")
                    sink.write(data); remaining -= len(data)
                sink.flush(); os.fsync(sink.fileno())
            _fsync_directory(destination.parent)
        _fsync_directory(target)


def _request(value: Any, revision: int) -> StorageMutationRequest:
    if not isinstance(value, StorageMutationRequest) or not _valid_actor(value.actor_id) or type(value.expected_catalog_revision) is not int or value.expected_catalog_revision != revision or not _valid_sha(value.expected_workflow_sha256) or not _valid_reason(value.reason):
        raise StorageOperationError("mutation request is invalid or stale")
    return value
def _run_id(value: Any) -> str:
    try:
        parsed = str(uuid.UUID(value)) if isinstance(value, str) else (_ for _ in ()).throw(ValueError())
    except (ValueError, AttributeError, TypeError) as exc:
        raise StorageOperationError("run_id is invalid") from exc
    if parsed != value:
        raise StorageOperationError("run_id is not canonical lowercase UUID")
    return parsed
def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789ABCDEF" for char in value)
def _valid_actor(value: Any) -> bool:
    return isinstance(value, str) and value == value.strip() and 3 <= len(value) <= 64 and value[0].isalpha() and all(char.isalnum() or char in "._-" for char in value)
def _valid_reason(value: Any) -> bool:
    return isinstance(value, str) and value == value.strip() and 1 <= len(value) <= 512 and all(ord(char) >= 32 and ord(char) != 127 for char in value)
def _safe_root(value, label, *, create):
    path = Path(value).absolute()
    if create: _mkdir(path)
    _safe_existing(path, path); return path
def _contains_path(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
def _is_link_or_reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0))
def _safe_hot_alias(alias: Any) -> bool:
    return valid_hot_alias(alias)
def _safe_existing(path: Path, root: Path):
    if not os.path.lexists(path): raise StorageOperationError("trusted path is missing")
    try: path.relative_to(root)
    except ValueError as exc: raise StorageOperationError("path escapes trusted root") from exc
    for item in (path, *path.parents):
        info = item.lstat()
        if _is_link_or_reparse(info): raise StorageOperationError("linked/reparse path is forbidden")
        if item == Path(item.anchor): break
def _mkdir(path: Path):
    target = path.absolute()
    anchor = Path(target.anchor)
    current = anchor
    _safe_existing(anchor, anchor)
    for part in target.parts[1:]:
        current = current / part
        if os.path.lexists(current):
            _safe_existing(current, current)
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise StorageOperationError("trusted directory component is not a directory")
            continue
        try:
            current.mkdir()
        except FileExistsError:
            pass
        _safe_existing(current, current)
        if not stat.S_ISDIR(current.lstat().st_mode):
            raise StorageOperationError("trusted directory component is not a directory")
def _publish_new(source: Path, target: Path):
    if os.path.lexists(target): raise StorageOperationError("publish target already exists")
    if not _same_volume(source, target.parent): raise StorageOperationError("cross-volume atomic publish is forbidden")
    os.replace(source, target)
def _remove_tree_safe(path: Path):
    _safe_existing(path, path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise StorageOperationError("tree removal requires a directory")
    for entry in os.scandir(path):
        child = Path(entry.path)
        child_info = child.lstat()
        if _is_link_or_reparse(child_info):
            raise StorageOperationError("linked carrier cannot be removed")
        if stat.S_ISDIR(child_info.st_mode):
            _remove_tree_safe(child)
        elif stat.S_ISREG(child_info.st_mode):
            if child_info.st_nlink > 1:
                raise StorageOperationError("hard-linked carrier cannot be removed")
            child.unlink()
        else:
            raise StorageOperationError("special carrier cannot be removed")
    path.rmdir()
def _remove_tree_if_safe(path: Path):
    if os.path.lexists(path): _remove_tree_safe(path)
def _remove_file_safe(path: Path, root: Path):
    _safe_existing(path, root)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise StorageOperationError("unsafe pin")
    path.unlink()
def _move_or_copy(source: Path, target: Path, kind: str) -> bool:
    if os.path.lexists(target): raise StorageOperationError("trash target already exists")
    if _same_volume(source, target.parent):
        os.replace(source, target); return False
    # Keep the staging component short: the evidence tree already has deeply
    # nested circuit paths and Windows' non-long-path APIs remain common.
    temporary = target.parent / f"{target.name}.tmp"
    try:
        if kind == "sqrun":
            _copy_file_streaming(source, temporary)
        else:
            _copy_tree_streaming(source, temporary)
        _fsync_directory(temporary.parent)
        _publish_new(temporary, target)
        return True
    except Exception:
        _remove_tree_if_safe(temporary)
        raise
def _copy_file_streaming(source: Path, target: Path):
    _safe_existing(source, source.parent)
    source_info = source.lstat()
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink > 1:
        raise StorageOperationError("unsafe cross-volume source")
    with source.open("rb") as input_stream, target.open("xb") as output_stream:
        while data := input_stream.read(64 * 1024): output_stream.write(data)
        output_stream.flush(); os.fsync(output_stream.fileno())
def _copy_tree_streaming(source: Path, target: Path):
    _safe_existing(source, source); _mkdir(target)
    def visit(base: Path, destination: Path):
        _mkdir(destination)
        for entry in sorted(os.scandir(base), key=lambda row: row.name):
            item = Path(entry.path); output = destination / entry.name
            info = item.lstat()
            if _is_link_or_reparse(info): raise StorageOperationError("linked cross-volume source")
            if stat.S_ISDIR(info.st_mode): visit(item, output)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1: raise StorageOperationError("unsafe cross-volume source")
                _copy_file_streaming(item, output)
            else: raise StorageOperationError("special cross-volume source")
        _fsync_directory(destination)
    visit(source, target)
def _fsync_directory(path: Path):
    # Windows does not support opening a directory as a normal file; existing
    # runtime helper is used where available and failures remain operation failures.
    from sqvm.runtime.storage import flush_directory
    flush_directory(path)
def _remove_carrier_safe(path: Path, kind: str):
    if kind == "sqrun": _remove_file_safe(path, path.parent)
    else: _remove_tree_safe(path)


def _rollback_trash_payload(carrier: Path, payload: Path, trash: Path, kind: str) -> Exception | None:
    """Prefer one retained source; otherwise leave a clearly recoverable duplicate."""
    try:
        carrier_present = os.path.lexists(carrier)
        payload_present = os.path.lexists(payload)
        if payload_present and not carrier_present and _same_volume(payload, carrier.parent):
            _publish_new(payload, carrier)
            payload_present = False
            carrier_present = True
        if payload_present and carrier_present:
            _remove_tree_safe(trash)
        elif carrier_present and os.path.lexists(trash):
            _remove_tree_safe(trash)
        return None
    except Exception as exc:  # A surviving duplicate is the fail-safe outcome.
        return exc
def _same_volume(source: Path, destination_directory: Path) -> bool:
    return source.stat().st_dev == destination_directory.stat().st_dev
def _carrier_sha(path: Path, kind: str) -> str:
    if kind == "sqrun":
        _safe_existing(path, path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise StorageOperationError("unsafe archive carrier")
        return _sha_file(path)
    digest = hashlib.sha256()
    _safe_existing(path, path)
    def visit(current: Path):
        for entry in sorted(os.scandir(current), key=lambda row: row.name):
            item = Path(entry.path)
            info = item.lstat()
            if _is_link_or_reparse(info):
                raise StorageOperationError("linked carrier cannot be hashed")
            relative = item.relative_to(path).as_posix().encode("utf-8")
            if stat.S_ISDIR(info.st_mode):
                digest.update(b"D\\0" + relative + b"\\0")
                visit(item)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1:
                    raise StorageOperationError("hard-linked carrier cannot be hashed")
                digest.update(b"F\\0" + relative + b"\\0")
                with item.open("rb") as stream:
                    while chunk := stream.read(64 * 1024):
                        digest.update(chunk)
            else:
                raise StorageOperationError("special carrier cannot be hashed")
    visit(path)
    return digest.hexdigest().upper()
def _trash_record(run, workflow, request, operation, state, kind, alias, original_sha, payload, payload_kind, started_sha, days):
    value = {"schema_version":"0.1","artifact_type":"sqvm_experiment_trash_record","artifact_version":"0.1","run_id":run,"workflow_id":workflow["workflow_id"],"workflow_sha256":workflow["workflow_sha256"],"receipt_sha256":workflow["receipt_sha256"],"operation_id":operation,"actor_id":request.actor_id,"reason":request.reason,"previous_storage_state":state,"original_carrier_kind":kind,"original_carrier_alias":alias,"original_carrier_sha256":original_sha,"payload_kind":payload_kind,"payload_sha256":_carrier_sha(payload,payload_kind),"payload_logical_bytes":_carrier_bytes(payload,payload_kind),"trashed_utc":_utc(),"purge_after_utc":_utc(days),"trash_started_event_sha256":started_sha,"record_sha256":""}
    value["record_sha256"] = _sha(_canonical({k:v for k,v in value.items() if k != "record_sha256"})); return value
def _read_trash_record(path: Path, run: str):
    value = _json(path); keys={"schema_version","artifact_type","artifact_version","run_id","workflow_id","workflow_sha256","receipt_sha256","operation_id","actor_id","reason","previous_storage_state","original_carrier_kind","original_carrier_alias","original_carrier_sha256","payload_kind","payload_sha256","payload_logical_bytes","trashed_utc","purge_after_utc","trash_started_event_sha256","record_sha256"}
    hashes = ("workflow_sha256", "receipt_sha256", "original_carrier_sha256", "payload_sha256", "trash_started_event_sha256", "record_sha256")
    try:
        valid_utc = lambda text: isinstance(text, str) and datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).isoformat() == text.replace("Z", "+00:00")
        alias = value.get("original_carrier_alias")
        valid = (
            set(value) == keys and value.get("schema_version") == "0.1" and value.get("artifact_type") == "sqvm_experiment_trash_record" and value.get("artifact_version") == "0.1"
            and _run_id(value.get("run_id")) == run and isinstance(value.get("workflow_id"), str) and value["workflow_id"]
            and _run_id(value.get("operation_id")) == value["operation_id"] and isinstance(value.get("actor_id"), str) and 3 <= len(value["actor_id"]) <= 64 and value["actor_id"][0].isalpha() and all(ch.isalnum() or ch in "._-" for ch in value["actor_id"])
            and isinstance(value.get("reason"), str) and value["reason"].strip() and value.get("previous_storage_state") in {"hot", "archived", "archived_duplicate"}
            and value.get("original_carrier_kind") in {"hot_directory", "sqrun"} and value.get("payload_kind") in {"hot_directory", "sqrun"}
            and type(value.get("payload_logical_bytes")) is int and value["payload_logical_bytes"] >= 0
            and valid_utc(value.get("trashed_utc")) and valid_utc(value.get("purge_after_utc"))
            and all(isinstance(value.get(key), str) and len(value[key]) == 64 and all(ch in "0123456789ABCDEF" for ch in value[key]) for key in hashes)
            and isinstance(alias, str) and alias and "/" not in alias and "\\" not in alias and ":" not in alias and alias not in {".", ".."}
            and value.get("record_sha256") == _sha(_canonical({k:v for k,v in value.items() if k!="record_sha256"}))
        )
    except (StorageOperationError, TypeError, ValueError):
        valid = False
    if not valid: raise StorageOperationError("trash record is invalid")
    return value
def _json(path):
    try:
        raw=_read_control_file(path, "canonical JSON"); value=json.loads(raw.decode(), object_pairs_hook=lambda pairs: _pairs(pairs), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except Exception as exc: raise StorageOperationError("canonical JSON cannot be read") from exc
    if not isinstance(value,dict) or raw != _canonical(value): raise StorageOperationError("JSON is not canonical")
    return value
def _workflow_identity(path: Path) -> dict[str, Any]:
    try:
        raw = _read_control_file(path, "workflow identity")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=lambda pairs: _pairs(pairs), parse_constant=lambda text: (_ for _ in ()).throw(ValueError(text)))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise StorageOperationError("hot carrier candidate workflow is invalid") from exc
    if not isinstance(value, dict) or not isinstance(value.get("workflow_id"), str) or not value["workflow_id"] or value.get("artifact_version") not in {"0.1", "0.2", "0.3"}:
        raise StorageOperationError("hot carrier candidate workflow is invalid")
    _run_id(value.get("run_id"))
    return value
def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
def _read_control_file(path: Path, label: str, *, limit: int = 64 * 1024) -> bytes:
    try:
        before = path.lstat()
        if _is_link_or_reparse(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise StorageOperationError(f"{label} is not a bounded regular file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if _file_identity(opened) != _file_identity(before):
                raise StorageOperationError(f"{label} changed before open")
            raw = stream.read(before.st_size + 1)
            if len(raw) != before.st_size or stream.read(1):
                raise StorageOperationError(f"{label} changed while reading")
        after = path.lstat()
        if _file_identity(after) != _file_identity(before):
            raise StorageOperationError(f"{label} changed while reading")
        return raw
    except StorageOperationError:
        raise
    except OSError as exc:
        raise StorageOperationError(f"{label} cannot be read safely") from exc
def _write_new(path: Path, value: Mapping[str, Any]):
    raw = _canonical(value)
    with path.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    _fsync_directory(path.parent)
def _pairs(pairs):
    value={}
    for key,item in pairs:
        if key in value: raise ValueError("duplicate key")
        value[key]=item
    return value
def _canonical(value): return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)+"\n").encode()
def _sha(raw): return hashlib.sha256(raw).hexdigest().upper()
def _carrier_bytes(path: Path, kind: str) -> int:
    if kind == "sqrun":
        try:
            info = path.lstat()
        except OSError as exc:
            raise StorageOperationError("archive carrier cannot be measured") from exc
        if _is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise StorageOperationError("archive carrier cannot be measured")
        return info.st_size
    total = 0
    def visit(current: Path):
        nonlocal total
        for entry in os.scandir(current):
            item = Path(entry.path); info = item.lstat()
            if _is_link_or_reparse(info):
                raise StorageOperationError("linked carrier cannot be measured")
            if stat.S_ISDIR(info.st_mode): visit(item)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink > 1: raise StorageOperationError("hard-linked carrier cannot be measured")
                total += info.st_size
            else: raise StorageOperationError("special carrier cannot be measured")
    visit(path)
    return total
def _sha_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024): digest.update(chunk)
    return digest.hexdigest().upper()
def _utc(days: int=0): return (datetime.now(timezone.utc)+timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
