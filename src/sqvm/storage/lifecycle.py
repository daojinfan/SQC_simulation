"""Append-only, independently verifiable storage lifecycle journal."""
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
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.storage import flush_directory


EVENT_TYPES = frozenset({"hot_discovered", "keep_changed", "archive_started", "archive_verified", "archive_failed", "hot_cleanup_started", "hot_cleanup_completed", "hot_cleanup_failed", "archive_restore_started", "archive_restore_completed", "archive_restore_failed", "trash_started", "trashed", "trash_failed", "trash_restored", "purge_started", "purge_failed", "purged", "staging_recovery_required", "reference_scan_failed"})
_STATES = frozenset({"running", "hot", "archiving", "archived_duplicate", "archived", "trash", "purging", "purged", "recovery_required"})
_ACTOR = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{2,63}$")
_SHA = re.compile(r"^[A-F0-9]{64}$")
_EVENT_KEYS = {"schema_version", "artifact_type", "artifact_version", "event_id", "run_id", "sequence", "event_type", "utc_time", "actor_id", "operation_id", "payload", "previous_event_sha256", "event_sha256"}
_COMMON = {"from_state", "to_state", "workflow_sha256", "carrier_path", "carrier_sha256", "catalog_revision"}


class LifecycleError(ValueError):
    pass


class LifecycleConflict(RuntimeError):
    status = 409


@dataclass(frozen=True, slots=True)
class LifecycleHead:
    run_id: str
    revision: int
    tail_sha256: str | None
    state: str | None
    manual_keep: bool
    trash_previous_state: str | None = None
    pending_event_type: str | None = None
    pending_operation_id: str | None = None


def read_head(root: str | Path, run_id: str) -> LifecycleHead:
    if not isinstance(root, (str, Path)):
        raise LifecycleError("invalid lifecycle root")
    run_id = _uuid(run_id, "run_id")
    state: str | None = None
    keep = False
    previous_state: str | None = None
    tail: str | None = None
    pending_type: str | None = None
    pending_operation: str | None = None
    base = _read_root(root)
    if base is None:
        return LifecycleHead(run_id, 0, None, None, False)
    events = _read_events(base, run_id)
    for event in events:
        state, keep, previous_state, pending_type, pending_operation = _apply(event, state, keep, previous_state, pending_type, pending_operation)
        tail = event["event_sha256"]
    return LifecycleHead(run_id, len(events), tail, state, keep, previous_state, pending_type, pending_operation)


def append_event(root: str | Path, run_id: str, event_type: str, payload: Mapping[str, Any], *, actor_id: str, operation_id: str, expected_revision: int, expected_tail_sha256: str | None) -> LifecycleHead:
    if not isinstance(root, (str, Path)):
        raise LifecycleError("invalid lifecycle root")
    run_id = _uuid(run_id, "run_id")
    if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
        raise LifecycleError("unsupported lifecycle event")
    if not isinstance(actor_id, str) or _ACTOR.fullmatch(actor_id) is None:
        raise LifecycleError("invalid actor_id")
    _uuid(operation_id, "operation_id")
    if type(expected_revision) is not int or expected_revision < 0 or (expected_tail_sha256 is not None and (not isinstance(expected_tail_sha256, str) or _SHA.fullmatch(expected_tail_sha256) is None)):
        raise LifecycleError("invalid optimistic concurrency expectation")
    payload_copy = _payload(event_type, payload)
    base = _create_root(root)
    token = _acquire_lock(base, run_id, actor_id, operation_id)
    try:
        head = read_head(base, run_id)
        if head.revision != expected_revision or head.tail_sha256 != expected_tail_sha256:
            raise LifecycleConflict("lifecycle revision conflict")
        event = {
            "schema_version": "0.1", "artifact_type": "sqvm_experiment_lifecycle_event", "artifact_version": "0.1",
            "event_id": str(uuid.uuid4()), "run_id": run_id, "sequence": head.revision,
            "event_type": event_type, "utc_time": utc_now_text(), "actor_id": actor_id,
            "operation_id": operation_id, "payload": payload_copy,
            "previous_event_sha256": head.tail_sha256,
        }
        event["event_sha256"] = _hash({key: value for key, value in event.items() if key != "event_sha256"})
        _apply(event, head.state, head.manual_keep, head.trash_previous_state, head.pending_event_type, head.pending_operation_id)
        directory = base / "lifecycle" / run_id
        _mkdir_safe(directory, base)
        _write_new(directory / f"{head.revision:08d}_{event['event_id']}.json", event)
        return read_head(base, run_id)
    finally:
        _release_lock(base, run_id, token)


def recover_stale_lock(root: str | Path, run_id: str, *, expected_lock_sha256: str, actor_id: str) -> None:
    """Explicitly remove an abandoned lock only after identity and PID checks."""
    _uuid(run_id, "run_id")
    if not isinstance(root, (str, Path)) or not isinstance(expected_lock_sha256, str) or _SHA.fullmatch(expected_lock_sha256) is None or not isinstance(actor_id, str) or _ACTOR.fullmatch(actor_id) is None:
        raise LifecycleError("invalid stale lock recovery request")
    base = _read_root(root)
    if base is None:
        raise LifecycleError("lifecycle root does not exist")
    path = base / "locks" / f"{run_id}.storage.lock"
    _safe(path, base)
    data = _read_lock(path)
    if data["run_id"] != run_id:
        raise LifecycleError("lock run_id does not match recovery request")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest().upper() != expected_lock_sha256:
        raise LifecycleConflict("lock identity changed")
    if _pid_alive(data["owner_pid"]):
        raise LifecycleConflict("lock owner is still alive")
    # Compare the bytes again immediately before unlinking; this prevents a stale
    # recovery from deleting a lock acquired by a new owner.
    if path.read_bytes() != raw:
        raise LifecycleConflict("lock changed during stale recovery")
    path.unlink()
    flush_directory(path.parent)


def _payload(event_type: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LifecycleError("lifecycle payload must be an object")
    extra = {"manual_keep"} if event_type == "keep_changed" else ({"trash_previous_state"} if event_type == "trashed" else ({"restored_state"} if event_type == "trash_restored" else ({"blockers"} if event_type == "reference_scan_failed" else set())))
    if set(value) != _COMMON | extra:
        raise LifecycleError("invalid lifecycle payload keys")
    payload = dict(value)
    if payload["from_state"] is not None and payload["from_state"] not in _STATES:
        raise LifecycleError("invalid from_state")
    if payload["to_state"] not in _STATES:
        raise LifecycleError("invalid to_state")
    if not _carrier_alias(payload["carrier_path"]):
        raise LifecycleError("invalid carrier_path")
    if type(payload["catalog_revision"]) is not int or payload["catalog_revision"] < 0:
        raise LifecycleError("invalid catalog_revision")
    for key in ("workflow_sha256", "carrier_sha256"):
        if not isinstance(payload[key], str) or _SHA.fullmatch(payload[key]) is None:
            raise LifecycleError(f"invalid {key}")
    if event_type == "keep_changed" and type(payload["manual_keep"]) is not bool:
        raise LifecycleError("invalid manual_keep")
    if event_type == "trashed" and payload["trash_previous_state"] not in {"hot", "archived", "archived_duplicate"}:
        raise LifecycleError("invalid trash_previous_state")
    if event_type == "trash_restored" and payload["restored_state"] not in {"hot", "archived", "archived_duplicate"}:
        raise LifecycleError("invalid restored_state")
    if event_type == "reference_scan_failed" and (not isinstance(payload["blockers"], list) or not payload["blockers"] or any(not isinstance(item, str) or not item for item in payload["blockers"])):
        raise LifecycleError("invalid blockers")
    return payload


def _apply(event: Mapping[str, Any], state: str | None, keep: bool, trash_previous_state: str | None, pending_type: str | None = None, pending_operation: str | None = None) -> tuple[str, bool, str | None, str | None, str | None]:
    event_type = event["event_type"]
    payload = event["payload"]
    if payload["from_state"] != state:
        raise LifecycleError("lifecycle payload does not bind prior state")
    to_state = payload["to_state"]
    expected: dict[str, tuple[set[str | None], set[str]]] = {
        "hot_discovered": ({None, "running"}, {"hot"}),
        "keep_changed": ({"hot", "archived_duplicate", "archived", "trash"}, {"hot", "archived_duplicate", "archived", "trash"}),
        "archive_started": ({"hot"}, {"archiving"}), "archive_verified": ({"archiving"}, {"archived_duplicate"}), "archive_failed": ({"archiving"}, {"hot"}),
        "hot_cleanup_started": ({"archived_duplicate"}, {"archived_duplicate"}), "hot_cleanup_completed": ({"archived_duplicate"}, {"archived"}), "hot_cleanup_failed": ({"archived_duplicate"}, {"archived_duplicate"}),
        "archive_restore_started": ({"archived"}, {"archiving"}), "archive_restore_completed": ({"archiving"}, {"hot"}), "archive_restore_failed": ({"archiving"}, {"archived"}),
        "trash_started": ({"hot", "archived", "archived_duplicate"}, {"hot", "archived", "archived_duplicate"}),
        "trashed": ({"hot", "archived", "archived_duplicate"}, {"trash"}), "trash_failed": ({"hot", "archived", "archived_duplicate"}, {"hot", "archived", "archived_duplicate"}),
        "trash_restored": ({"trash"}, {"hot", "archived", "archived_duplicate"}),
        "purge_started": ({"trash"}, {"purging"}), "purge_failed": ({"purging"}, {"trash"}), "purged": ({"purging"}, {"purged"}),
        "staging_recovery_required": ({None, "running"}, {"recovery_required"}),
        "reference_scan_failed": ({"hot", "archived_duplicate", "archived", "trash", "recovery_required"}, {"hot", "archived_duplicate", "archived", "trash", "recovery_required"}),
    }
    allowed_from, allowed_to = expected[event_type]
    if state not in allowed_from or to_state not in allowed_to:
        raise LifecycleError("illegal lifecycle transition")
    starts = {"archive_started", "hot_cleanup_started", "archive_restore_started", "trash_started", "purge_started"}
    completes = {"archive_verified": "archive_started", "archive_failed": "archive_started", "hot_cleanup_completed": "hot_cleanup_started", "hot_cleanup_failed": "hot_cleanup_started", "archive_restore_completed": "archive_restore_started", "archive_restore_failed": "archive_restore_started", "trashed": "trash_started", "trash_failed": "trash_started", "purge_failed": "purge_started", "purged": "purge_started"}
    if event_type in starts:
        if pending_type is not None:
            raise LifecycleError("lifecycle operation is already pending")
        pending_type, pending_operation = event_type, event["operation_id"]
    elif event_type in completes:
        if pending_type != completes[event_type] or pending_operation != event["operation_id"]:
            raise LifecycleError("lifecycle completion does not match pending operation")
        pending_type = pending_operation = None
    if event_type == "keep_changed":
        keep = payload["manual_keep"]
    if event_type == "purge_started" and keep:
        raise LifecycleError("manual_keep blocks purge")
    if event_type == "trashed":
        if payload["trash_previous_state"] != state:
            raise LifecycleError("trash prior state mismatch")
        trash_previous_state = state
    if event_type == "trash_restored":
        if payload["restored_state"] != trash_previous_state or to_state != trash_previous_state:
            raise LifecycleError("trash restore prior state mismatch")
        trash_previous_state = None
    return to_state, keep, trash_previous_state, pending_type, pending_operation


def _read_events(base: Path, run_id: str) -> list[dict[str, Any]]:
    directory = base / "lifecycle" / run_id
    if not directory.exists():
        return []
    _safe(directory, base)
    paths = sorted(directory.iterdir())
    events: list[dict[str, Any]] = []
    previous: str | None = None
    for sequence, path in enumerate(paths):
        _safe(path, base)
        if not path.is_file() or not re.fullmatch(rf"{sequence:08d}_[0-9a-f-]{{36}}\.json", path.name):
            raise LifecycleError("unknown or unordered lifecycle entry")
        raw = path.read_bytes()
        event = _event(_json(path), run_id, sequence, previous)
        if path.name != f"{sequence:08d}_{event['event_id']}.json":
            raise LifecycleError("lifecycle event filename identity mismatch")
        if raw != canonical_json_bytes(event):
            raise LifecycleError("noncanonical lifecycle event")
        events.append(event)
        previous = event["event_sha256"]
    return events


def _event(value: dict[str, Any], run_id: str, sequence: int, previous: str | None) -> dict[str, Any]:
    if set(value) != _EVENT_KEYS or value.get("schema_version") != "0.1" or value.get("artifact_type") != "sqvm_experiment_lifecycle_event" or value.get("artifact_version") != "0.1":
        raise LifecycleError("invalid lifecycle event schema")
    if value.get("run_id") != run_id or value.get("sequence") != sequence or value.get("previous_event_sha256") != previous or value.get("event_type") not in EVENT_TYPES:
        raise LifecycleError("invalid lifecycle event identity")
    _uuid(value.get("event_id"), "event_id"); _uuid(value.get("operation_id"), "operation_id")
    if not isinstance(value.get("actor_id"), str) or _ACTOR.fullmatch(value["actor_id"]) is None or not _utc(value.get("utc_time")):
        raise LifecycleError("invalid lifecycle event metadata")
    _payload(value["event_type"], value.get("payload"))
    if not isinstance(value.get("event_sha256"), str) or _SHA.fullmatch(value["event_sha256"]) is None or value["event_sha256"] != _hash({key: item for key, item in value.items() if key != "event_sha256"}):
        raise LifecycleError("lifecycle event hash mismatch")
    return value


def _read_root(value: str | Path) -> Path | None:
    root = Path(value).absolute()
    _absolute_safe_existing(root)
    if not root.exists():
        return None
    _safe(root, root)
    return root


def _create_root(value: str | Path) -> Path:
    root = Path(value).absolute()
    _absolute_safe_existing(root)
    _mkdir_safe(root, None)
    _safe(root, root)
    return root


def _safe(path: Path, root: Path) -> None:
    try: path.relative_to(root)
    except ValueError as exc: raise LifecycleError("path escapes lifecycle root") from exc
    current = path
    while True:
        stat = current.lstat()
        if current.is_symlink() or bool(getattr(current, "is_junction", lambda: False)()) or getattr(stat, "st_reparse_tag", 0) or (current.is_file() and stat.st_nlink != 1):
            raise LifecycleError("linked, reparse, or hardlinked lifecycle path")
        if current == root: break
        current = current.parent


def _absolute_safe_existing(path: Path) -> None:
    cursor = path.absolute()
    anchor = Path(cursor.anchor)
    existing: list[Path] = []
    while True:
        if os.path.lexists(cursor):
            existing.append(cursor)
        if cursor == anchor:
            break
        cursor = cursor.parent
    for item in existing:
        stat = item.lstat()
        if item.is_symlink() or bool(getattr(item, "is_junction", lambda: False)()) or getattr(stat, "st_reparse_tag", 0):
            raise LifecycleError("linked or reparse lifecycle ancestor")


def _mkdir_safe(path: Path, root: Path | None) -> None:
    target = path.absolute()
    missing: list[Path] = []
    cursor = target
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise LifecycleError("cannot create lifecycle root")
        cursor = parent
    _absolute_safe_existing(cursor)
    for component in reversed(missing):
        component.mkdir()
        _absolute_safe_existing(component)
        if root is not None:
            _safe(component, root)


def _acquire_lock(base: Path, run_id: str, actor_id: str, operation_id: str) -> bytes:
    directory = base / "locks"; _mkdir_safe(directory, base); _safe(directory, base)
    path = directory / f"{run_id}.storage.lock"
    value = {"schema_version": "0.1", "artifact_type": "sqvm_experiment_storage_lock", "run_id": run_id, "owner_id": actor_id, "operation_id": operation_id, "owner_pid": os.getpid(), "created_utc": utc_now_text()}
    raw = canonical_json_bytes(value)
    try:
        with path.open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        flush_directory(directory)
    except FileExistsError as exc:
        raise LifecycleConflict("lifecycle operation locked") from exc
    return raw


def _release_lock(base: Path, run_id: str, token: bytes) -> None:
    path = base / "locks" / f"{run_id}.storage.lock"
    if not path.exists():
        raise LifecycleError("owned lifecycle lock disappeared before release")
    _safe(path, base)
    if path.read_bytes() != token:
        raise LifecycleConflict("lifecycle lock owner changed before release")
    try:
        path.unlink(); flush_directory(path.parent)
    except OSError as exc:
        raise LifecycleError("lifecycle lock release failed; recovery is required") from exc


def _read_lock(path: Path) -> dict[str, Any]:
    value = _json(path)
    if path.read_bytes() != canonical_json_bytes(value) or set(value) != {"schema_version", "artifact_type", "run_id", "owner_id", "operation_id", "owner_pid", "created_utc"} or value.get("schema_version") != "0.1" or value.get("artifact_type") != "sqvm_experiment_storage_lock" or not isinstance(value.get("owner_id"), str) or _ACTOR.fullmatch(value["owner_id"]) is None or type(value.get("owner_pid")) is not int or value["owner_pid"] < 1 or not _utc(value.get("created_utc")):
        raise LifecycleError("invalid lock metadata")
    _uuid(value.get("run_id"), "lock run_id"); _uuid(value.get("operation_id"), "lock operation_id")
    return value


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(process_query_limited_information | synchronize, False, pid)
        if not handle:
            # Access denied must never be interpreted as proof that a lock owner died.
            return ctypes.get_last_error() == 5
        exit_code = ctypes.c_ulong()
        try:
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try: os.kill(pid, 0)
    except PermissionError: return True
    except ProcessLookupError: return False
    except OSError: return True
    return True


def _json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda token: (_ for _ in ()).throw(LifecycleError(token)))
    except Exception as exc:
        raise LifecycleError(f"invalid lifecycle JSON: {path}") from exc
    if not isinstance(value, dict) or not _finite(value):
        raise LifecycleError("invalid lifecycle JSON value")
    return value


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result: raise LifecycleError("duplicate lifecycle JSON key")
        result[key] = value
    return result


def _finite(value: Any) -> bool:
    if isinstance(value, float): return math.isfinite(value)
    if isinstance(value, dict): return all(_finite(item) for item in value.values())
    if isinstance(value, list): return all(_finite(item) for item in value)
    return True


def _uuid(value: Any, label: str) -> str:
    if not isinstance(value, str): raise LifecycleError(f"invalid {label}")
    try: return str(uuid.UUID(value))
    except ValueError as exc: raise LifecycleError(f"invalid {label}") from exc


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _carrier_alias(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _utc(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def _write_new(path: Path, payload: Mapping[str, Any]) -> None:
    raw = canonical_json_bytes(payload)
    with path.open("xb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    flush_directory(path.parent)
