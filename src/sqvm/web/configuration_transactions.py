"""Crash-consistent authority for platform-configuration generations."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from typing import Any, Callable, Iterator, Mapping
import uuid

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json
from sqvm.runtime.publication import publish_calibration_directory
from sqvm.runtime.storage import flush_directory, inventory_tree, write_canonical_new


_DEVICE_ID = __import__("re").compile(r"[a-z][a-z0-9_-]{0,63}$")
_SHA256 = __import__("re").compile(r"[A-F0-9]{64}$")
_TRANSACTION_SCHEMA = "0.1"
_LOCK_TIMEOUT_SECONDS = 5.0
_WINDOWS_TRANSIENT_REPLACE_ERRORS = frozenset({5, 32, 33})
_REPLACE_ATTEMPTS = 25
_ROOT_DIRECTORIES = (
    "current",
    "active",
    "snapshots",
    "pins",
    "audit",
)
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


class ConfigurationTransactionError(ValueError):
    """Stable failure raised at the configuration transaction boundary."""

    def __init__(
        self,
        code: str,
        status: int,
        message: str,
        *,
        transaction_id: str | None = None,
        retry_after: int | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.transaction_id = transaction_id
        self.retry_after = retry_after
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CommittedConfigurationView:
    device_id: str
    generation: int
    transaction_id: str
    head: Mapping[str, Any]
    manifest: Mapping[str, Any]
    bundle_root: Path
    projection_root: Path
    idempotency_catalog: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ConfigurationTransactionCommit:
    response: Mapping[str, Any]
    transaction: Mapping[str, Any]


class ConfigurationTransactionManager:
    """Publish and verify one immutable configuration generation at a time."""

    def __init__(self, configuration_root: str | Path) -> None:
        self.root = _logical_path(Path(configuration_root)).resolve()
        self.transactions_root = self.root / "transactions"
        self.heads_root = self.transactions_root / "heads"
        self.bundles_root = self.transactions_root / "bundles"
        self.staging_root = self.transactions_root / ".s"
        self.locks_root = self.transactions_root / "locks"
        self.projection_status_root = self.transactions_root / "projection-status"

    def head_exists(self, device_id: str) -> bool:
        self._device(device_id)
        if os.path.lexists(self.heads_root):
            self._safe_directory_chain(self.heads_root, "transaction heads")
        return os.path.lexists(self.heads_root / f"{device_id}.json")

    def head_devices(self) -> tuple[str, ...]:
        if not self.heads_root.is_dir():
            return ()
        self._safe_directory_chain(self.heads_root, "transaction heads")
        devices: list[str] = []
        for entry in sorted(os.scandir(self.heads_root), key=lambda row: row.name):
            path = Path(entry.path)
            if not entry.is_file(follow_symlinks=False) or not entry.name.endswith(".json"):
                raise self._recovery("transaction head directory contains an unknown entry")
            self._safe_file(path, "transaction head")
            device_id = entry.name[:-5]
            self._device(device_id)
            devices.append(device_id)
        return tuple(devices)

    def ensure_imported(self, device_id: str, legacy_root: str | Path) -> CommittedConfigurationView:
        """Create generation zero from one validated legacy-shaped projection."""

        self._device(device_id)
        if self.head_exists(device_id):
            view = self.committed_view(device_id)
            try:
                self.materialize(view, legacy_root)
            except Exception:
                pass
            return view
        operation_id = str(uuid.uuid4())
        request = {
            "schema_version": _TRANSACTION_SCHEMA,
            "operation_type": "legacy_import",
            "device_id": device_id,
        }
        with self._device_lock(device_id, operation_id):
            if self.head_exists(device_id):
                view = self.committed_view(device_id)
                try:
                    self.materialize(view, legacy_root)
                except Exception:
                    pass
                return view
            stage_parent = self._fresh_stage(device_id, operation_id)
            try:
                projection = stage_parent / "w"
                self._copy_legacy_device_projection(
                    Path(legacy_root).resolve(), projection, device_id
                )
                response = {
                    "schema_version": _TRANSACTION_SCHEMA,
                    "device_id": device_id,
                    "legacy_imported": True,
                }
                bundle, manifest, receipt = self._prepare_bundle(
                    stage_parent,
                    projection,
                    device_id=device_id,
                    generation=0,
                    operation_id=operation_id,
                    operation_type="legacy_import",
                    request=request,
                    parent=None,
                    previous_catalog=None,
                    response=response,
                )
                target = self._bundle_root(device_id, operation_id)
                self._publish_bundle(bundle, target, manifest)
                self._switch_head(device_id, manifest, target)
                view = self.committed_view(device_id)
                try:
                    self.materialize(view, legacy_root)
                except Exception:
                    pass
                del receipt
                return view
            except ConfigurationTransactionError:
                raise
            except Exception as exc:
                raise ConfigurationTransactionError(
                    "configuration_transaction_aborted",
                    500,
                    "configuration transaction aborted before commit",
                    transaction_id=operation_id,
                ) from exc
            finally:
                self._remove_stage(stage_parent)

    def execute(
        self,
        *,
        device_id: str,
        operation_type: str,
        operation_id: str | None,
        request: Mapping[str, Any],
        legacy_root: str | Path,
        transform: Callable[[Path], Mapping[str, Any]],
    ) -> ConfigurationTransactionCommit:
        """Commit one state transform or replay its stable first receipt."""

        self._device(device_id)
        operation = self._operation_id(operation_id or str(uuid.uuid4()))
        request_payload = copy.deepcopy(dict(request))
        request_sha256 = sha256_json(request_payload)
        with self._device_lock(device_id, operation):
            current = self.committed_view(device_id)
            replay = self._replay(
                current,
                operation_id=operation,
                request_sha256=request_sha256,
            )
            if replay is not None:
                return replay
            orphan = self._bundle_root(device_id, operation)
            if os.path.lexists(orphan):
                return self._resume_or_reject_orphan(
                    current,
                    orphan,
                    operation_id=operation,
                    request_sha256=request_sha256,
                    legacy_root=legacy_root,
                )
            stage_parent = self._fresh_stage(device_id, operation)
            committed = False
            try:
                workspace = stage_parent / "w"
                self._copy_projection(current.projection_root, workspace)
                response = copy.deepcopy(dict(transform(workspace)))
                generation = current.generation + 1
                bundle, manifest, _receipt = self._prepare_bundle(
                    stage_parent,
                    workspace,
                    device_id=device_id,
                    generation=generation,
                    operation_id=operation,
                    operation_type=operation_type,
                    request=request_payload,
                    parent=current,
                    previous_catalog=current.idempotency_catalog,
                    response=response,
                )
                target = self._bundle_root(device_id, operation)
                self._publish_bundle(bundle, target, manifest)
                self._switch_head(device_id, manifest, target)
                committed = True
                view = self.committed_view(device_id)
                projection_status = "complete"
                try:
                    self.materialize(view, legacy_root)
                except Exception:
                    projection_status = "pending"
                return self._receipt_commit(
                    view,
                    response,
                    projection_status=projection_status,
                    superseded=False,
                )
            except ConfigurationTransactionError:
                raise
            except Exception as exc:
                if hasattr(exc, "status"):
                    raise
                if committed:
                    raise ConfigurationTransactionError(
                        "configuration_committed_recovery_pending",
                        503,
                        "configuration committed and requires projection recovery",
                        transaction_id=operation,
                    ) from exc
                raise ConfigurationTransactionError(
                    "configuration_transaction_aborted",
                    500,
                    "configuration transaction aborted before commit",
                    transaction_id=operation,
                ) from exc
            finally:
                self._remove_stage(stage_parent)

    def committed_view(self, device_id: str) -> CommittedConfigurationView:
        """Validate head, current bundle, complete inventory, and parent chain."""

        self._device(device_id)
        head_path = self.heads_root / f"{device_id}.json"
        self._safe_directory_chain(self.heads_root, "transaction heads")
        head = self._load_canonical(head_path, "transaction head")
        self._exact(
            head,
            {
                "schema_version",
                "artifact_type",
                "artifact_version",
                "device_id",
                "generation",
                "transaction_id",
                "manifest_path",
                "manifest_raw_sha256",
                "parent_transaction_id",
                "parent_manifest_raw_sha256",
                "committed_utc",
            },
            "transaction head",
        )
        if (
            head.get("schema_version") != _TRANSACTION_SCHEMA
            or head.get("artifact_type") != "platform_configuration_transaction_head"
            or head.get("artifact_version") != _TRANSACTION_SCHEMA
            or head.get("device_id") != device_id
            or type(head.get("generation")) is not int
            or head["generation"] < 0
        ):
            raise self._recovery("transaction head identity is invalid")
        transaction_id = self._stored_operation_id(head.get("transaction_id"))
        expected_manifest = self._bundle_root(device_id, transaction_id) / "manifest.json"
        manifest_path = self._root_relative_path(
            head.get("manifest_path"), "transaction manifest path"
        )
        if manifest_path != expected_manifest:
            raise self._recovery("transaction head manifest path is invalid")
        manifest_raw = self._safe_file_bytes(
            _io_path(manifest_path),
            "transaction manifest",
        )
        if self._sha(manifest_raw) != self._hash(head.get("manifest_raw_sha256")):
            raise self._recovery("transaction head manifest hash differs")
        manifest = self._decode_canonical(manifest_raw, "transaction manifest")
        bundle = manifest_path.parent
        self._validate_bundle(_io_path(bundle), manifest)
        if (
            manifest.get("device_id") != device_id
            or manifest.get("transaction_id") != transaction_id
            or manifest.get("generation") != head["generation"]
        ):
            raise self._recovery("transaction head and manifest identity differ")
        self._validate_parent_chain(head, manifest)
        state_refs = manifest["state_refs"]
        catalog = self._load_bundle_ref(
            bundle,
            state_refs["idempotency_catalog"],
            "idempotency catalog",
        )
        self._validate_idempotency_catalog(catalog, device_id, head["generation"])
        projection_root = _io_path(bundle / "projection")
        self._safe_directory(projection_root, "transaction projection")
        return CommittedConfigurationView(
            device_id,
            head["generation"],
            transaction_id,
            head,
            manifest,
            bundle,
            projection_root,
            catalog,
        )

    def materialize(
        self,
        view: CommittedConfigurationView,
        legacy_root: str | Path,
    ) -> None:
        """Idempotently refresh the non-authoritative compatibility projection."""

        target_root = Path(legacy_root).resolve()
        if target_root != self.root:
            try:
                target_root.relative_to(self.root)
            except ValueError as exc:
                raise self._recovery("compatibility projection root is unsafe") from exc
        source = view.projection_root
        for name in _ROOT_DIRECTORIES:
            directory = target_root / name
            self._ensure_safe_directory(directory, f"compatibility {name} root")
        current_source = source / "current" / f"{view.device_id}.json"
        self._project_file(current_source, target_root / "current" / current_source.name)
        active_source = source / "active" / f"{view.device_id}.json"
        active_target = target_root / "active" / active_source.name
        if active_source.is_file():
            self._project_file(active_source, active_target)
        else:
            active_target.unlink(missing_ok=True)
        snapshot_ids: set[str] = set()
        source_snapshots = source / "snapshots"
        if source_snapshots.is_dir():
            for directory in sorted(source_snapshots.iterdir(), key=lambda path: path.name):
                self._stored_operation_id(directory.name)
                snapshot_ids.add(directory.name)
                target = target_root / "snapshots" / directory.name
                self._ensure_safe_directory(
                    target,
                    "compatibility snapshot directory",
                )
                for file in sorted(directory.iterdir(), key=lambda path: path.name):
                    self._project_file(file, target / file.name)
        for directory in sorted((target_root / "snapshots").iterdir(), key=lambda path: path.name):
            self._safe_directory(directory, "compatibility snapshot directory")
            if directory.name in snapshot_ids:
                continue
            try:
                payload = json.loads((directory / "snapshot.json").read_text("utf-8"))
            except Exception:
                continue
            if payload.get("device_id") == view.device_id:
                inventory_tree(directory)
                shutil.rmtree(directory)
        pin_ids: set[str] = set()
        source_pins = source / "pins"
        if source_pins.is_dir():
            for file in sorted(source_pins.iterdir(), key=lambda path: path.name):
                pin_ids.add(file.stem)
                self._project_file(file, target_root / "pins" / file.name)
        for file in sorted((target_root / "pins").glob("*.json")):
            try:
                payload = json.loads(file.read_text("utf-8"))
            except Exception:
                continue
            if payload.get("snapshot_id") not in pin_ids and payload.get("snapshot_id") in snapshot_ids:
                file.unlink(missing_ok=True)
        source_audit = source / "audit"
        if source_audit.is_dir():
            for file in sorted(source_audit.iterdir(), key=lambda path: path.name):
                self._project_file(file, target_root / "audit" / file.name)
        status = {
            "schema_version": _TRANSACTION_SCHEMA,
            "artifact_type": "platform_configuration_projection_status",
            "artifact_version": _TRANSACTION_SCHEMA,
            "device_id": view.device_id,
            "generation": view.generation,
            "transaction_id": view.transaction_id,
            "projection_status": "complete",
        }
        self._atomic_canonical(
            self.projection_status_root / f"{view.device_id}.json", status
        )

    def _prepare_bundle(
        self,
        stage_parent: Path,
        projection: Path,
        *,
        device_id: str,
        generation: int,
        operation_id: str,
        operation_type: str,
        request: Mapping[str, Any],
        parent: CommittedConfigurationView | None,
        previous_catalog: Mapping[str, Any] | None,
        response: Mapping[str, Any],
    ) -> tuple[Path, Mapping[str, Any], Mapping[str, Any]]:
        bundle = stage_parent / "bundle"
        bundle.mkdir()
        bundle_projection = bundle / "projection"
        self._copy_projection(projection, bundle_projection)
        state = bundle / "state"
        state.mkdir()
        request_sha256 = sha256_json(request)
        receipt = {
            "schema_version": _TRANSACTION_SCHEMA,
            "artifact_type": "platform_configuration_transaction_receipt",
            "artifact_version": _TRANSACTION_SCHEMA,
            "device_id": device_id,
            "generation": generation,
            "transaction_id": operation_id,
            "operation_id": operation_id,
            "operation_type": operation_type,
            "request_sha256": request_sha256,
            "response": copy.deepcopy(dict(response)),
        }
        receipt_sha256 = write_canonical_new(bundle / "receipt.json", receipt)
        catalogs = self._state_catalogs(
            bundle_projection,
            device_id=device_id,
            generation=generation,
        )
        for name, payload in catalogs.items():
            write_canonical_new(state / f"{name}.json", payload)
        previous_entries = (
            copy.deepcopy(list(previous_catalog.get("entries", [])))
            if previous_catalog is not None
            else []
        )
        if any(row.get("operation_id") == operation_id for row in previous_entries):
            raise ConfigurationTransactionError(
                "idempotency_conflict", 409, "operation ID is already committed"
            )
        previous_entries.append(
            {
                "operation_id": operation_id,
                "request_sha256": request_sha256,
                "transaction_id": operation_id,
                "generation": generation,
                "receipt_path": self._relative(
                    self._bundle_root(device_id, operation_id) / "receipt.json"
                ),
                "receipt_raw_sha256": receipt_sha256,
            }
        )
        idempotency = {
            "schema_version": _TRANSACTION_SCHEMA,
            "artifact_type": "platform_configuration_idempotency_catalog",
            "artifact_version": _TRANSACTION_SCHEMA,
            "device_id": device_id,
            "generation": generation,
            "entries": previous_entries,
        }
        write_canonical_new(state / "idempotency_catalog.json", idempotency)
        inventory = inventory_tree(bundle)
        state_refs = {
            "current": "state/current.json",
            "active": "state/active.json" if (state / "active.json").is_file() else None,
            "snapshot_catalog": "state/snapshot_catalog.json",
            "pin_catalog": "state/pin_catalog.json",
            "audit_head": "state/audit_head.json",
            "idempotency_catalog": "state/idempotency_catalog.json",
            "receipt": "receipt.json",
            "projection": "projection",
        }
        parent_payload = None
        if parent is not None:
            parent_payload = {
                "transaction_id": parent.transaction_id,
                "generation": parent.generation,
                "manifest_path": self._relative(parent.bundle_root / "manifest.json"),
                "manifest_raw_sha256": self._sha(
                    (parent.bundle_root / "manifest.json").read_bytes()
                ),
            }
        manifest_base = {
            "schema_version": _TRANSACTION_SCHEMA,
            "artifact_type": "platform_configuration_transaction_manifest",
            "artifact_version": _TRANSACTION_SCHEMA,
            "writer_version": "sqvm_configuration_transaction_v1",
            "device_id": device_id,
            "generation": generation,
            "transaction_id": operation_id,
            "operation_id": operation_id,
            "operation_type": operation_type,
            "request_sha256": request_sha256,
            "parent": parent_payload,
            "state_refs": state_refs,
            "files": inventory,
        }
        manifest = {
            **manifest_base,
            "aggregate_sha256": sha256_json(manifest_base),
        }
        write_canonical_new(bundle / "manifest.json", manifest)
        self._validate_bundle(bundle, manifest)
        return bundle, manifest, receipt

    def _state_catalogs(
        self,
        projection: Path,
        *,
        device_id: str,
        generation: int,
    ) -> dict[str, Mapping[str, Any]]:
        current_path = projection / "current" / f"{device_id}.json"
        current = self._load_canonical(current_path, "transaction current")
        if current.get("device_id") != device_id:
            raise self._recovery("transaction current device differs")
        write_current = {
            "schema_version": _TRANSACTION_SCHEMA,
            "artifact_type": "platform_configuration_current_ref",
            "artifact_version": _TRANSACTION_SCHEMA,
            "device_id": device_id,
            **self._file_ref(current_path, projection.parent),
        }
        active_path = projection / "active" / f"{device_id}.json"
        active_ref = None
        if active_path.is_file():
            active = self._load_canonical(active_path, "transaction active")
            if active.get("device_id") != device_id:
                raise self._recovery("transaction active device differs")
            active_ref = {
                "schema_version": _TRANSACTION_SCHEMA,
                "artifact_type": "platform_configuration_active_ref",
                "artifact_version": _TRANSACTION_SCHEMA,
                "device_id": device_id,
                **self._file_ref(active_path, projection.parent),
            }
        snapshots = []
        snapshots_root = projection / "snapshots"
        if snapshots_root.is_dir():
            for directory in sorted(snapshots_root.iterdir(), key=lambda path: path.name):
                snapshot_id = self._stored_operation_id(directory.name)
                snapshot_path = directory / "snapshot.json"
                snapshot = self._load_canonical(snapshot_path, "transaction snapshot")
                if snapshot.get("snapshot_id") != snapshot_id or snapshot.get("device_id") != device_id:
                    raise self._recovery("transaction snapshot identity is invalid")
                source = directory / "source_candidate.json"
                snapshots.append(
                    {
                        "snapshot_id": snapshot_id,
                        "snapshot": self._file_ref(snapshot_path, projection.parent),
                        "source_candidate": (
                            self._file_ref(source, projection.parent)
                            if source.is_file()
                            else None
                        ),
                    }
                )
        pins = []
        pins_root = projection / "pins"
        if pins_root.is_dir():
            for path in sorted(pins_root.glob("*.json")):
                snapshot_id = self._stored_operation_id(path.stem)
                payload = self._load_canonical(path, "transaction pin")
                if payload.get("snapshot_id") != snapshot_id:
                    raise self._recovery("transaction pin identity is invalid")
                pins.append(
                    {"snapshot_id": snapshot_id, "pin": self._file_ref(path, projection.parent)}
                )
        audits = []
        audit_root = projection / "audit"
        if audit_root.is_dir():
            for path in sorted(audit_root.glob("*.json")):
                event_id = self._stored_operation_id(path.stem)
                payload = self._load_canonical(path, "transaction audit event")
                if payload.get("event_id") != event_id:
                    raise self._recovery("transaction audit identity is invalid")
                audits.append(
                    {
                        "event_id": event_id,
                        "created_utc": payload.get("created_utc"),
                        "event": self._file_ref(path, projection.parent),
                    }
                )
        audits.sort(key=lambda row: (str(row["created_utc"]), row["event_id"]))
        return {
            "current": write_current,
            **({"active": active_ref} if active_ref is not None else {}),
            "snapshot_catalog": {
                "schema_version": _TRANSACTION_SCHEMA,
                "artifact_type": "platform_configuration_snapshot_catalog",
                "artifact_version": _TRANSACTION_SCHEMA,
                "device_id": device_id,
                "generation": generation,
                "entries": snapshots,
            },
            "pin_catalog": {
                "schema_version": _TRANSACTION_SCHEMA,
                "artifact_type": "platform_configuration_pin_catalog",
                "artifact_version": _TRANSACTION_SCHEMA,
                "device_id": device_id,
                "generation": generation,
                "entries": pins,
            },
            "audit_head": {
                "schema_version": _TRANSACTION_SCHEMA,
                "artifact_type": "platform_configuration_audit_head",
                "artifact_version": _TRANSACTION_SCHEMA,
                "device_id": device_id,
                "generation": generation,
                "entries": audits,
                "tail_event_id": audits[-1]["event_id"] if audits else None,
                "tail_event_raw_sha256": (
                    audits[-1]["event"]["raw_sha256"] if audits else None
                ),
            },
        }

    def _publish_bundle(
        self,
        source: Path,
        target: Path,
        manifest: Mapping[str, Any],
    ) -> None:
        self._ensure_safe_directory(
            target.parent,
            "transaction bundle device root",
        )
        publish_calibration_directory(source, target)
        published_root = _io_path(target)
        published = self._load_canonical(
            published_root / "manifest.json",
            "published manifest",
        )
        if published != manifest:
            raise self._recovery("published transaction manifest differs")
        self._validate_bundle(published_root, published)

    def _switch_head(
        self,
        device_id: str,
        manifest: Mapping[str, Any],
        bundle_root: Path,
    ) -> None:
        self._ensure_safe_directory(self.heads_root, "transaction heads")
        parent = manifest.get("parent")
        head = {
            "schema_version": _TRANSACTION_SCHEMA,
            "artifact_type": "platform_configuration_transaction_head",
            "artifact_version": _TRANSACTION_SCHEMA,
            "device_id": device_id,
            "generation": manifest["generation"],
            "transaction_id": manifest["transaction_id"],
            "manifest_path": self._relative(bundle_root / "manifest.json"),
            "manifest_raw_sha256": self._sha(
                _io_path(bundle_root / "manifest.json").read_bytes()
            ),
            "parent_transaction_id": (
                parent["transaction_id"] if isinstance(parent, Mapping) else None
            ),
            "parent_manifest_raw_sha256": (
                parent["manifest_raw_sha256"] if isinstance(parent, Mapping) else None
            ),
            "committed_utc": _utc_now(),
        }
        self._atomic_canonical(self.heads_root / f"{device_id}.json", head)

    def _validate_bundle(self, bundle: Path, manifest: Mapping[str, Any]) -> None:
        self._safe_directory_chain(bundle, "transaction bundle")
        self._exact(
            manifest,
            {
                "schema_version",
                "artifact_type",
                "artifact_version",
                "writer_version",
                "device_id",
                "generation",
                "transaction_id",
                "operation_id",
                "operation_type",
                "request_sha256",
                "parent",
                "state_refs",
                "files",
                "aggregate_sha256",
            },
            "transaction manifest",
        )
        if (
            manifest.get("schema_version") != _TRANSACTION_SCHEMA
            or manifest.get("artifact_type") != "platform_configuration_transaction_manifest"
            or manifest.get("artifact_version") != _TRANSACTION_SCHEMA
            or manifest.get("writer_version") != "sqvm_configuration_transaction_v1"
            or type(manifest.get("generation")) is not int
            or manifest["generation"] < 0
            or manifest.get("transaction_id") != manifest.get("operation_id")
        ):
            raise self._recovery("transaction manifest schema is invalid")
        self._device(manifest.get("device_id"))
        self._stored_operation_id(manifest.get("transaction_id"))
        self._hash(manifest.get("request_sha256"))
        base = {key: value for key, value in manifest.items() if key != "aggregate_sha256"}
        if manifest.get("aggregate_sha256") != sha256_json(base):
            raise self._recovery("transaction manifest aggregate hash is invalid")
        expected = manifest.get("files")
        if not isinstance(expected, list):
            raise self._recovery("transaction manifest inventory is invalid")
        try:
            actual = inventory_tree(bundle)
        except (OSError, ValueError) as exc:
            raise self._recovery(
                "transaction bundle contains a linked, reparse, or unreadable entry"
            ) from exc
        actual = [row for row in actual if row["path"] != "manifest.json"]
        for row in actual:
            self._safe_file(
                self._inside(bundle, row["path"], "transaction bundle object"),
                "transaction bundle object",
            )
        if expected != actual:
            raise self._recovery("transaction bundle inventory differs from manifest")
        refs = manifest.get("state_refs")
        if not isinstance(refs, Mapping) or set(refs) != {
            "current",
            "active",
            "snapshot_catalog",
            "pin_catalog",
            "audit_head",
            "idempotency_catalog",
            "receipt",
            "projection",
        }:
            raise self._recovery("transaction state references are invalid")
        inventory_paths = {row["path"] for row in actual}
        for name, relative in refs.items():
            if name == "active" and relative is None:
                continue
            if name == "projection":
                if relative != "projection":
                    raise self._recovery("transaction projection reference is invalid")
                continue
            if not isinstance(relative, str) or relative not in inventory_paths:
                raise self._recovery("transaction state reference is missing")

    def _validate_parent_chain(
        self,
        head: Mapping[str, Any],
        manifest: Mapping[str, Any],
    ) -> None:
        generation = manifest["generation"]
        parent = manifest.get("parent")
        seen = {manifest["transaction_id"]}
        if generation == 0:
            if parent is not None or head.get("parent_transaction_id") is not None:
                raise self._recovery("generation zero transaction has a parent")
            return
        if not isinstance(parent, Mapping):
            raise self._recovery("transaction parent is missing")
        if (
            head.get("parent_transaction_id") != parent.get("transaction_id")
            or head.get("parent_manifest_raw_sha256")
            != parent.get("manifest_raw_sha256")
        ):
            raise self._recovery("transaction head parent binding differs")
        expected_generation = generation - 1
        while parent is not None:
            self._exact(
                parent,
                {"transaction_id", "generation", "manifest_path", "manifest_raw_sha256"},
                "transaction parent",
            )
            transaction_id = self._stored_operation_id(parent.get("transaction_id"))
            if transaction_id in seen or parent.get("generation") != expected_generation:
                raise self._recovery("transaction parent chain is cyclic or skips a generation")
            seen.add(transaction_id)
            path = self._root_relative_path(parent.get("manifest_path"), "parent manifest")
            expected_path = self._bundle_root(manifest["device_id"], transaction_id) / "manifest.json"
            if path != expected_path:
                raise self._recovery("transaction parent path is invalid")
            raw = self._safe_file_bytes(_io_path(path), "parent manifest")
            if self._sha(raw) != self._hash(parent.get("manifest_raw_sha256")):
                raise self._recovery("transaction parent manifest hash differs")
            value = self._decode_canonical(raw, "parent manifest")
            self._validate_bundle(_io_path(path.parent), value)
            if (
                value.get("device_id") != manifest["device_id"]
                or value.get("transaction_id") != transaction_id
                or value.get("generation") != expected_generation
            ):
                raise self._recovery("transaction parent identity differs")
            expected_generation -= 1
            parent = value.get("parent")
        if expected_generation != -1:
            raise self._recovery("transaction parent chain is incomplete")

    def _validate_idempotency_catalog(
        self,
        value: Mapping[str, Any],
        device_id: str,
        generation: int,
    ) -> None:
        self._exact(
            value,
            {
                "schema_version",
                "artifact_type",
                "artifact_version",
                "device_id",
                "generation",
                "entries",
            },
            "idempotency catalog",
        )
        if (
            value.get("schema_version") != _TRANSACTION_SCHEMA
            or value.get("artifact_type") != "platform_configuration_idempotency_catalog"
            or value.get("artifact_version") != _TRANSACTION_SCHEMA
            or value.get("device_id") != device_id
            or value.get("generation") != generation
            or not isinstance(value.get("entries"), list)
        ):
            raise self._recovery("idempotency catalog identity is invalid")
        seen: set[str] = set()
        for row in value["entries"]:
            self._exact(
                row,
                {
                    "operation_id",
                    "request_sha256",
                    "transaction_id",
                    "generation",
                    "receipt_path",
                    "receipt_raw_sha256",
                },
                "idempotency entry",
            )
            operation = self._stored_operation_id(row.get("operation_id"))
            if operation in seen or row.get("transaction_id") != operation:
                raise self._recovery("idempotency catalog contains a duplicate operation")
            seen.add(operation)
            self._hash(row.get("request_sha256"))
            self._hash(row.get("receipt_raw_sha256"))
            receipt = self._root_relative_path(row.get("receipt_path"), "transaction receipt")
            if receipt != self._bundle_root(device_id, operation) / "receipt.json":
                raise self._recovery("idempotency receipt path is invalid")
            raw = self._safe_file_bytes(_io_path(receipt), "transaction receipt")
            if self._sha(raw) != row["receipt_raw_sha256"]:
                raise self._recovery("idempotency receipt hash differs")
            receipt_value = self._decode_canonical(raw, "transaction receipt")
            self._exact(
                receipt_value,
                {
                    "schema_version",
                    "artifact_type",
                    "artifact_version",
                    "device_id",
                    "generation",
                    "transaction_id",
                    "operation_id",
                    "operation_type",
                    "request_sha256",
                    "response",
                },
                "transaction receipt",
            )
            row_generation = row.get("generation")
            if (
                type(row_generation) is not int
                or not 0 <= row_generation <= generation
                or receipt_value.get("schema_version") != _TRANSACTION_SCHEMA
                or receipt_value.get("artifact_type")
                != "platform_configuration_transaction_receipt"
                or receipt_value.get("artifact_version") != _TRANSACTION_SCHEMA
                or receipt_value.get("device_id") != device_id
                or receipt_value.get("generation") != row_generation
                or receipt_value.get("transaction_id") != operation
                or receipt_value.get("operation_id") != operation
                or receipt_value.get("request_sha256") != row["request_sha256"]
                or not isinstance(receipt_value.get("operation_type"), str)
                or not receipt_value["operation_type"]
                or not isinstance(receipt_value.get("response"), Mapping)
            ):
                raise self._recovery("transaction receipt identity is invalid")

    def _replay(
        self,
        current: CommittedConfigurationView,
        *,
        operation_id: str,
        request_sha256: str,
    ) -> ConfigurationTransactionCommit | None:
        matches = [
            row
            for row in current.idempotency_catalog["entries"]
            if row["operation_id"] == operation_id
        ]
        if not matches:
            return None
        row = matches[0]
        if row["request_sha256"] != request_sha256:
            raise ConfigurationTransactionError(
                "idempotency_conflict",
                409,
                "operation ID was already used for a different request",
                transaction_id=operation_id,
            )
        receipt_path = self._root_relative_path(row["receipt_path"], "transaction receipt")
        receipt = self._load_canonical(
            _io_path(receipt_path),
            "transaction receipt",
        )
        response = receipt.get("response")
        if not isinstance(response, Mapping):
            raise self._recovery("transaction receipt response is invalid")
        transaction = {
            "transaction_id": operation_id,
            "operation_id": operation_id,
            "generation": row["generation"],
            "durability_status": "committed",
            "projection_status": "complete",
            "superseded": row["generation"] != current.generation,
        }
        return ConfigurationTransactionCommit(copy.deepcopy(dict(response)), transaction)

    def _resume_or_reject_orphan(
        self,
        current: CommittedConfigurationView,
        orphan: Path,
        *,
        operation_id: str,
        request_sha256: str,
        legacy_root: str | Path,
    ) -> ConfigurationTransactionCommit:
        manifest = self._load_canonical(orphan / "manifest.json", "orphan manifest")
        self._validate_bundle(_io_path(orphan), manifest)
        parent = manifest.get("parent")
        valid_parent = (
            isinstance(parent, Mapping)
            and parent.get("transaction_id") == current.transaction_id
            and parent.get("generation") == current.generation
            and parent.get("manifest_raw_sha256")
            == self._sha(
                _io_path(current.bundle_root / "manifest.json").read_bytes()
            )
        )
        if (
            manifest.get("transaction_id") != operation_id
            or manifest.get("request_sha256") != request_sha256
            or not valid_parent
        ):
            raise ConfigurationTransactionError(
                "idempotency_conflict",
                409,
                "orphan transaction does not match the current request and parent",
                transaction_id=operation_id,
            )
        self._switch_head(current.device_id, manifest, orphan)
        view = self.committed_view(current.device_id)
        receipt = self._load_canonical(
            _io_path(orphan / "receipt.json"),
            "transaction receipt",
        )
        response = receipt.get("response")
        if not isinstance(response, Mapping):
            raise self._recovery("orphan transaction receipt response is invalid")
        projection_status = "complete"
        try:
            self.materialize(view, legacy_root)
        except Exception:
            projection_status = "pending"
        return self._receipt_commit(
            view,
            response,
            projection_status=projection_status,
            superseded=False,
        )

    def _receipt_commit(
        self,
        view: CommittedConfigurationView,
        response: Mapping[str, Any],
        *,
        projection_status: str,
        superseded: bool,
    ) -> ConfigurationTransactionCommit:
        return ConfigurationTransactionCommit(
            copy.deepcopy(dict(response)),
            {
                "transaction_id": view.transaction_id,
                "operation_id": view.transaction_id,
                "generation": view.generation,
                "durability_status": "committed",
                "projection_status": projection_status,
                "superseded": superseded,
            },
        )

    def _copy_legacy_device_projection(
        self,
        source_root: Path,
        target_root: Path,
        device_id: str,
    ) -> None:
        self._safe_directory(source_root, "legacy configuration root")
        target_root.mkdir(parents=True)
        for name in _ROOT_DIRECTORIES:
            (target_root / name).mkdir()
        current = source_root / "current" / f"{device_id}.json"
        self._safe_directory(source_root / "current", "legacy current root")
        self._copy_safe_file(current, target_root / "current" / current.name)
        active = source_root / "active" / f"{device_id}.json"
        if os.path.lexists(source_root / "active"):
            self._safe_directory(source_root / "active", "legacy active root")
        if os.path.lexists(active):
            self._copy_safe_file(active, target_root / "active" / active.name)
        snapshot_ids: set[str] = set()
        snapshots = source_root / "snapshots"
        if os.path.lexists(snapshots):
            self._safe_directory(snapshots, "legacy snapshot root")
            for directory in sorted(snapshots.iterdir(), key=lambda path: path.name):
                self._safe_directory(directory, "legacy snapshot directory")
                snapshot_path = directory / "snapshot.json"
                payload = self._load_canonical(snapshot_path, "legacy snapshot")
                if payload.get("device_id") != device_id:
                    continue
                snapshot_id = self._stored_operation_id(payload.get("snapshot_id"))
                if snapshot_id != directory.name:
                    raise self._recovery("legacy snapshot directory identity differs")
                snapshot_ids.add(snapshot_id)
                target = target_root / "snapshots" / snapshot_id
                target.mkdir()
                self._copy_safe_file(snapshot_path, target / "snapshot.json")
                candidate = directory / "source_candidate.json"
                if os.path.lexists(candidate):
                    self._copy_safe_file(candidate, target / candidate.name)
                unknown = {
                    path.name for path in directory.iterdir()
                } - {"snapshot.json", "source_candidate.json"}
                if unknown:
                    raise self._recovery("legacy snapshot contains unknown entries")
        pins = source_root / "pins"
        if os.path.lexists(pins):
            self._safe_directory(pins, "legacy pin root")
            for path in sorted(pins.glob("*.json")):
                payload = self._load_canonical(path, "legacy pin")
                if payload.get("snapshot_id") in snapshot_ids:
                    self._copy_safe_file(path, target_root / "pins" / path.name)
        audit = source_root / "audit"
        if os.path.lexists(audit):
            self._safe_directory(audit, "legacy audit root")
            for path in sorted(audit.glob("*.json")):
                payload = self._load_canonical(path, "legacy audit")
                details = payload.get("details")
                if not isinstance(details, Mapping):
                    continue
                belongs = details.get("device_id") == device_id or details.get("snapshot_id") in snapshot_ids
                if belongs:
                    self._copy_safe_file(path, target_root / "audit" / path.name)

    def _copy_projection(self, source: Path, target: Path) -> None:
        self._safe_directory(source, "configuration projection")
        target.mkdir(parents=True)
        for name in _ROOT_DIRECTORIES:
            source_dir = source / name
            target_dir = target / name
            target_dir.mkdir()
            if not source_dir.is_dir():
                continue
            self._safe_directory(source_dir, f"configuration projection {name}")
            for path in sorted(source_dir.rglob("*"), key=lambda item: item.relative_to(source_dir).as_posix()):
                relative = path.relative_to(source_dir)
                destination = target_dir / relative
                info = os.lstat(path)
                if stat.S_ISDIR(info.st_mode):
                    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
                        raise self._recovery("configuration projection contains a link")
                    destination.mkdir(exist_ok=True)
                elif stat.S_ISREG(info.st_mode):
                    self._copy_safe_file(path, destination)
                else:
                    raise self._recovery("configuration projection contains an unsupported entry")

    def _copy_safe_file(self, source: Path, target: Path) -> None:
        raw = self._safe_file_bytes(source, "configuration projection file")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def _project_file(self, source: Path, target: Path) -> None:
        raw = self._safe_file_bytes(source, "committed projection file")
        self._ensure_safe_directory(
            target.parent,
            "compatibility projection directory",
        )
        target_io = _io_path(target)
        if os.path.lexists(target_io):
            self._safe_file(target_io, "compatibility projection file")
        temporary = _io_path(
            target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace_file(temporary, target_io)
            flush_directory(target.parent)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _atomic_canonical(self, path: Path, payload: Mapping[str, Any]) -> None:
        self._ensure_safe_directory(
            path.parent,
            "transaction metadata directory",
        )
        path_io = _io_path(path)
        if os.path.lexists(path_io):
            self._safe_file(path_io, "transaction metadata file")
        raw = canonical_json_bytes(payload)
        temporary = _io_path(
            path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._replace_file(temporary, path_io)
            flush_directory(path.parent)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _replace_file(source: Path, target: Path) -> None:
        delay_s = 0.01
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(source, target)
                return
            except OSError as exc:
                error = getattr(exc, "winerror", None) or exc.errno
                retryable = (
                    os.name == "nt"
                    and error in _WINDOWS_TRANSIENT_REPLACE_ERRORS
                    and attempt < _REPLACE_ATTEMPTS - 1
                )
                if not retryable:
                    raise
                time.sleep(delay_s)
                delay_s = min(delay_s * 2.0, 0.1)

    def _fresh_stage(self, device_id: str, operation_id: str) -> Path:
        self._device(device_id)
        self._operation_id(operation_id)
        self._ensure_safe_directory(self.staging_root, "transaction staging root")
        for _attempt in range(32):
            target = self.staging_root / uuid.uuid4().hex[:8]
            try:
                target.mkdir()
            except FileExistsError:
                continue
            return _io_path(target)
        raise ConfigurationTransactionError(
            "configuration_staging_unavailable",
            503,
            "configuration transaction staging is unavailable",
            transaction_id=operation_id,
            retry_after=1,
        )

    @staticmethod
    def _remove_stage(path: Path) -> None:
        try:
            shutil.rmtree(path)
        except OSError:
            pass

    @contextmanager
    def _device_lock(self, device_id: str, operation_id: str) -> Iterator[None]:
        self._ensure_safe_directory(self.locks_root, "transaction lock root")
        path = self.locks_root / f"{device_id}.lock"
        key = str(path.resolve()).casefold()
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        acquired = process_lock.acquire(timeout=_LOCK_TIMEOUT_SECONDS)
        if not acquired:
            raise ConfigurationTransactionError(
                "configuration_transaction_busy",
                503,
                "configuration transaction is busy",
                transaction_id=operation_id,
                retry_after=1,
            )
        stream = None
        try:
            stream = path.open("a+b")
            self._acquire_os_lock(stream, operation_id)
            yield
        finally:
            if stream is not None:
                try:
                    self._release_os_lock(stream)
                finally:
                    stream.close()
            process_lock.release()

    @staticmethod
    def _acquire_os_lock(stream: Any, operation_id: str) -> None:
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    return
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ConfigurationTransactionError(
                            "configuration_transaction_busy",
                            503,
                            "configuration transaction is busy",
                            transaction_id=operation_id,
                            retry_after=1,
                        )
                    time.sleep(0.02)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ConfigurationTransactionError(
                            "configuration_transaction_busy",
                            503,
                            "configuration transaction is busy",
                            transaction_id=operation_id,
                            retry_after=1,
                        )
                    time.sleep(0.02)

    @staticmethod
    def _release_os_lock(stream: Any) -> None:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _load_bundle_ref(
        self,
        bundle: Path,
        relative: Any,
        label: str,
    ) -> Mapping[str, Any]:
        if not isinstance(relative, str):
            raise self._recovery(f"{label} reference is invalid")
        path = self._inside(bundle, relative, label)
        return self._load_canonical(_io_path(path), label)

    def _file_ref(self, path: Path, base: Path) -> dict[str, Any]:
        raw = self._safe_file_bytes(path, "transaction state object")
        return {
            "path": path.relative_to(base).as_posix(),
            "byte_length": len(raw),
            "raw_sha256": self._sha(raw),
        }

    def _root_relative_path(self, relative: Any, label: str) -> Path:
        if not isinstance(relative, str):
            raise self._recovery(f"{label} is invalid")
        return self._inside(self.root, relative, label)

    @staticmethod
    def _inside(base: Path, relative: str, label: str) -> Path:
        candidate = Path(relative)
        if candidate.is_absolute() or "\\" in relative:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"{label} is unsafe"
            )
        normalized = candidate.as_posix()
        if not normalized or normalized != relative or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"{label} is unsafe"
            )
        resolved = (base / candidate).resolve()
        try:
            resolved.relative_to(base.resolve())
        except ValueError as exc:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"{label} escapes its authority root"
            ) from exc
        return resolved

    def _relative(self, path: Path) -> str:
        try:
            return _logical_path(path).resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise self._recovery("transaction path escapes configuration root") from exc

    def _bundle_root(self, device_id: str, operation_id: str) -> Path:
        self._device(device_id)
        self._operation_id(operation_id)
        return self.bundles_root / device_id / operation_id

    @staticmethod
    def _safe_directory(path: Path, label: str) -> None:
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"cannot inspect {label}"
            ) from exc
        is_junction = bool(getattr(path, "is_junction", lambda: False)())
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or is_junction:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"{label} is unsafe"
            )

    def _safe_directory_chain(self, path: Path, label: str) -> None:
        root = Path(os.path.abspath(self.root))
        target = Path(os.path.abspath(_logical_path(path)))
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise self._recovery(f"{label} escapes configuration root") from exc
        self._safe_directory(root, "configuration root")
        current = root
        for part in relative.parts:
            current = current / part
            self._safe_directory(current, label)

    def _ensure_safe_directory(self, path: Path, label: str) -> None:
        root = Path(os.path.abspath(self.root))
        target = Path(os.path.abspath(_logical_path(path)))
        try:
            relative = target.relative_to(root)
        except ValueError as exc:
            raise self._recovery(f"{label} escapes configuration root") from exc
        root.mkdir(parents=True, exist_ok=True)
        self._safe_directory(root, "configuration root")
        current = root
        for part in relative.parts:
            current = current / part
            if not os.path.lexists(current):
                try:
                    current.mkdir()
                except OSError as exc:
                    raise self._recovery(f"cannot create {label}") from exc
            self._safe_directory(current, label)

    @staticmethod
    def _safe_file(path: Path, label: str) -> None:
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"cannot inspect {label}"
            ) from exc
        if (
            not stat.S_ISREG(info.st_mode)
            or path.is_symlink()
            or info.st_nlink != 1
        ):
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"{label} is unsafe"
            )

    def _safe_file_bytes(self, path: Path, label: str) -> bytes:
        self._safe_file(path, label)
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise self._recovery(f"cannot read {label}") from exc
        self._safe_file(path, label)
        return raw

    def _load_canonical(self, path: Path, label: str) -> Mapping[str, Any]:
        return self._decode_canonical(self._safe_file_bytes(path, label), label)

    def _decode_canonical(self, raw: bytes, label: str) -> Mapping[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise self._recovery(f"{label} is unreadable") from exc
        if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
            raise self._recovery(f"{label} is not canonical JSON")
        return value

    @staticmethod
    def _exact(value: Mapping[str, Any], keys: set[str], label: str) -> None:
        if set(value) != keys:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, f"{label} fields are invalid"
            )

    @staticmethod
    def _device(value: Any) -> str:
        if not isinstance(value, str) or _DEVICE_ID.fullmatch(value) is None:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, "transaction device ID is invalid"
            )
        return value

    @staticmethod
    def _operation_id(value: Any) -> str:
        try:
            parsed = uuid.UUID(str(value), version=4)
        except (ValueError, TypeError, AttributeError) as exc:
            raise ConfigurationTransactionError(
                "invalid_operation_id", 422, "operation ID must be a canonical UUID4"
            ) from exc
        canonical = str(parsed)
        if value != canonical:
            raise ConfigurationTransactionError(
                "invalid_operation_id", 422, "operation ID must be a canonical UUID4"
            )
        return canonical

    def _stored_operation_id(self, value: Any) -> str:
        try:
            return self._operation_id(value)
        except ConfigurationTransactionError as exc:
            raise self._recovery("stored transaction UUID4 is invalid") from exc

    @staticmethod
    def _hash(value: Any) -> str:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ConfigurationTransactionError(
                "configuration_recovery_required", 503, "transaction SHA-256 is invalid"
            )
        return value

    @staticmethod
    def _sha(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest().upper()

    @staticmethod
    def _recovery(message: str) -> ConfigurationTransactionError:
        return ConfigurationTransactionError(
            "configuration_recovery_required", 503, message
        )


def committed_projection_roots(configuration_root: str | Path) -> tuple[Path, ...]:
    """Return verified current projections when transaction heads exist."""

    manager = ConfigurationTransactionManager(configuration_root)
    return tuple(manager.committed_view(device).projection_root for device in manager.head_devices())


def _logical_path(path: str | Path) -> Path:
    value = os.fspath(path)
    if os.name != "nt":
        return Path(value)
    if value.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + value[8:])
    if value.startswith("\\\\?\\"):
        return Path(value[4:])
    return Path(value)


def _io_path(path: str | Path) -> Path:
    logical = _logical_path(path)
    if os.name != "nt":
        return logical
    absolute = os.path.abspath(logical)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "CommittedConfigurationView",
    "ConfigurationTransactionCommit",
    "ConfigurationTransactionError",
    "ConfigurationTransactionManager",
    "committed_projection_roots",
]
