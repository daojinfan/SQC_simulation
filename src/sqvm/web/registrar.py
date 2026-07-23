"""Best-effort publication hints for the Web experiment read model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from typing import Any, Mapping
import uuid
import warnings

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.storage import flush_directory


_EVENT_KEYS = frozenset(
    {
        "schema",
        "type",
        "version",
        "event_id",
        "run_id",
        "workflow_id",
        "artifact_version",
        "workflow_sha256",
        "receipt_sha256",
        "source_relative_path",
        "created_utc",
    }
)
_SCHEMA = "sqvm.web.index_inbox_event"
_TYPE = "published_run"
_VERSION = "0.1"
_MAX_CONTROL_BYTES = 64 * 1024 * 1024
_LOCK_WAIT_SECONDS = 5.0


class PublicationRegistrarError(ValueError):
    """Raised when a published run cannot safely be registered."""


class PublicationRegistrarConflict(RuntimeError):
    """Raised when an event ID is already bound to different content."""


@dataclass(frozen=True, slots=True)
class PublicationRegistration:
    """Result of one idempotent inbox enqueue operation."""

    event_id: str
    path: Path
    created: bool


def enqueue_published_run(
    repository_root: str | Path,
    published_run_root: str | Path,
    *,
    storage_root: str | Path | None = None,
) -> PublicationRegistration:
    """Atomically enqueue one immutable, published run for Web projection."""

    root = _existing_repository_root(repository_root)
    source, source_relative_path = _published_source(
        published_run_root, root
    )
    workflow, workflow_raw = _read_canonical_mapping(
        source / "workflow.json", root, "published workflow"
    )
    receipt, receipt_raw = _read_canonical_mapping(
        source / "receipt.json", root, "published receipt"
    )
    identity = _published_identity(
        workflow,
        receipt,
        workflow_raw,
        receipt_raw,
        source_relative_path,
    )
    event_id = _event_id(identity)
    event = {
        "schema": _SCHEMA,
        "type": _TYPE,
        "version": _VERSION,
        "event_id": event_id,
        **identity,
    }
    if set(event) != _EVENT_KEYS:
        raise PublicationRegistrarError("index inbox event contract is invalid")

    inbox, storage = _prepare_inbox(root, storage_root)
    target = inbox / f"{event_id}.json"
    raw = canonical_json_bytes(event)
    existing = _existing_event(target, storage, raw, event_id)
    if existing is not None:
        return existing

    lock_path = inbox / f".{event_id}.lock"
    lock_token = canonical_json_bytes(
        {"event_id": event_id, "owner_pid": os.getpid(), "token": uuid.uuid4().hex}
    )
    acquired = _acquire_event_lock(
        lock_path,
        target,
        storage,
        raw,
        event_id,
        lock_token,
    )
    if acquired is not None:
        return acquired

    temporary = inbox / f".{event_id}.{uuid.uuid4().hex}.tmp"
    try:
        existing = _existing_event(target, storage, raw, event_id)
        if existing is not None:
            return existing
        _write_new_flushed(temporary, raw)
        _assert_regular_file(temporary, storage, "temporary inbox event")
        if os.path.lexists(target):
            return _require_matching_event(target, storage, raw, event_id)
        os.replace(temporary, target)
        flush_directory(inbox)
        _require_matching_event(target, storage, raw, event_id)
        return PublicationRegistration(event_id, target, True)
    finally:
        _remove_owned_temporary(temporary)
        _release_event_lock(lock_path, storage, lock_token)


def enqueue_published_run_best_effort(
    repository_root: str | Path,
    published_run_root: str | Path,
    *,
    storage_root: str | Path | None = None,
) -> PublicationRegistration | None:
    """Enqueue without changing the success semantics of run publication."""

    try:
        return enqueue_published_run(
            repository_root, published_run_root, storage_root=storage_root
        )
    except Exception as exc:
        try:
            warnings.warn(
                "published run could not be queued for Web indexing "
                f"({type(exc).__name__}: {exc})",
                RuntimeWarning,
                stacklevel=2,
            )
        except Exception:
            # A warnings filter may promote RuntimeWarning to an exception. Index
            # observability must still remain subordinate to run publication.
            pass
        return None


def _published_identity(
    workflow: Mapping[str, Any],
    receipt: Mapping[str, Any],
    workflow_raw: bytes,
    receipt_raw: bytes,
    source_relative_path: str,
) -> dict[str, str]:
    run_id = _identity_text(workflow.get("run_id"), "workflow run_id")
    if receipt.get("run_id") != run_id:
        raise PublicationRegistrarError("receipt run_id does not match workflow")
    workflow_id = _identity_text(workflow.get("workflow_id"), "workflow_id")
    artifact_version = _identity_text(
        workflow.get("artifact_version"), "artifact_version"
    )
    created_utc = _utc(workflow.get("created_utc"))
    workflow_sha256 = _sha256(workflow_raw)
    receipt_sha256 = _sha256(receipt_raw)
    claimed_workflow_sha256 = receipt.get("workflow_sha256")
    if (
        claimed_workflow_sha256 is not None
        and claimed_workflow_sha256 != workflow_sha256
    ):
        raise PublicationRegistrarError("receipt workflow identity is invalid")
    return {
        "run_id": run_id,
        "workflow_id": workflow_id,
        "artifact_version": artifact_version,
        "workflow_sha256": workflow_sha256,
        "receipt_sha256": receipt_sha256,
        "source_relative_path": source_relative_path,
        "created_utc": created_utc,
    }


def _event_id(identity: Mapping[str, str]) -> str:
    binding = {
        "run_id": identity["run_id"],
        "workflow_sha256": identity["workflow_sha256"],
        "receipt_sha256": identity["receipt_sha256"],
    }
    return _sha256(canonical_json_bytes(binding))


def _existing_repository_root(value: str | Path) -> Path:
    root = _absolute(value)
    if not os.path.lexists(root):
        raise PublicationRegistrarError("repository root does not exist")
    _assert_absolute_ancestors_safe(root)
    try:
        info = root.lstat()
    except OSError as exc:
        raise PublicationRegistrarError("repository root cannot be inspected") from exc
    if _is_link_or_reparse(root, info) or not stat.S_ISDIR(info.st_mode):
        raise PublicationRegistrarError("repository root is linked or unsafe")
    if root.resolve(strict=True) != root:
        raise PublicationRegistrarError("repository root has an indirect path")
    return root


def _published_source(value: str | Path, root: Path) -> tuple[Path, str]:
    source = _absolute(value)
    try:
        relative = source.relative_to(root)
    except ValueError as exc:
        raise PublicationRegistrarError("published run escapes repository root") from exc
    if relative == Path("."):
        raise PublicationRegistrarError("repository root is not a published run")
    _assert_safe_chain(source, root, "published run")
    if not source.is_dir():
        raise PublicationRegistrarError("published run is not a directory")
    return source, relative.as_posix()


def _prepare_inbox(
    root: Path, value: str | Path | None
) -> tuple[Path, Path]:
    storage = (
        root / "output" / "experiment-storage" if value is None else _absolute(value)
    )
    _mkdir_absolute_safe(storage)
    _assert_safe_chain(storage, storage, "experiment storage root")
    if not storage.is_dir():
        raise PublicationRegistrarError("experiment storage root is not a directory")
    inbox = storage / "index-inbox"
    _mkdir_beneath(inbox, storage)
    _assert_safe_chain(inbox, storage, "index inbox")
    if not inbox.is_dir():
        raise PublicationRegistrarError("index inbox is not a directory")
    return inbox, storage


def _mkdir_absolute_safe(path: Path) -> None:
    missing: list[Path] = []
    cursor = path
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise PublicationRegistrarError("cannot create experiment storage root")
        cursor = parent
    _assert_absolute_ancestors_safe(cursor)
    for component in reversed(missing):
        try:
            component.mkdir()
        except FileExistsError:
            pass
        _assert_absolute_ancestors_safe(component)
        try:
            info = component.lstat()
        except OSError as exc:
            raise PublicationRegistrarError(
                "experiment storage directory cannot be inspected"
            ) from exc
        if _is_link_or_reparse(component, info) or not stat.S_ISDIR(info.st_mode):
            raise PublicationRegistrarError("experiment storage directory is unsafe")


def _mkdir_beneath(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PublicationRegistrarError("directory escapes repository root") from exc
    missing: list[Path] = []
    cursor = path
    while not os.path.lexists(cursor):
        missing.append(cursor)
        if cursor == root:
            break
        cursor = cursor.parent
    _assert_safe_chain(cursor, root, "index inbox ancestor")
    for component in reversed(missing):
        try:
            component.mkdir()
        except FileExistsError:
            pass
        _assert_safe_chain(component, root, "index inbox directory")


def _read_canonical_mapping(
    path: Path, root: Path, label: str
) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_bytes_no_follow(path, root, label, _MAX_CONTROL_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda token: _invalid_constant(token),
        )
    except PublicationRegistrarError:
        raise
    except Exception as exc:
        raise PublicationRegistrarError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict) or not _finite(value):
        raise PublicationRegistrarError(f"{label} must contain a finite JSON object")
    if raw != canonical_json_bytes(value):
        raise PublicationRegistrarError(f"{label} is not canonical JSON")
    return value, raw


def _read_regular_bytes_no_follow(
    path: Path,
    root: Path,
    label: str,
    limit: int,
) -> bytes:
    before = _assert_regular_file(path, root, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        after = path.lstat()
    except OSError as exc:
        raise PublicationRegistrarError(f"{label} cannot be read safely") from exc
    if len(raw) > limit:
        raise PublicationRegistrarError(f"{label} exceeds its size limit")
    identity = (before.st_dev, before.st_ino)
    if (
        (opened.st_dev, opened.st_ino) != identity
        or (after.st_dev, after.st_ino) != identity
        or opened.st_nlink != 1
        or after.st_nlink != 1
        or _is_link_or_reparse(path, after)
        or not stat.S_ISREG(after.st_mode)
        or opened.st_size != len(raw)
        or after.st_size != len(raw)
    ):
        raise PublicationRegistrarError(f"{label} identity changed while reading")
    return raw


def _assert_regular_file(path: Path, root: Path, label: str) -> os.stat_result:
    _assert_safe_chain(path.parent, root, f"{label} parent")
    try:
        info = path.lstat()
    except OSError as exc:
        raise PublicationRegistrarError(f"{label} cannot be inspected") from exc
    if (
        _is_link_or_reparse(path, info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
    ):
        raise PublicationRegistrarError(f"{label} is linked or unsafe")
    return info


def _assert_safe_chain(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise PublicationRegistrarError(f"{label} escapes repository root") from exc
    cursor = path
    while True:
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise PublicationRegistrarError(f"{label} cannot be inspected") from exc
        if _is_link_or_reparse(cursor, info):
            raise PublicationRegistrarError(f"{label} contains a linked path")
        if cursor != path and not stat.S_ISDIR(info.st_mode):
            raise PublicationRegistrarError(f"{label} contains a non-directory ancestor")
        if cursor == root:
            break
        cursor = cursor.parent


def _assert_absolute_ancestors_safe(path: Path) -> None:
    cursor = path
    anchor = Path(path.anchor)
    while True:
        if os.path.lexists(cursor):
            try:
                info = cursor.lstat()
            except OSError as exc:
                raise PublicationRegistrarError("path ancestor cannot be inspected") from exc
            if _is_link_or_reparse(cursor, info):
                raise PublicationRegistrarError("path contains a linked ancestor")
        if cursor == anchor:
            break
        cursor = cursor.parent


def _existing_event(
    target: Path,
    root: Path,
    raw: bytes,
    event_id: str,
) -> PublicationRegistration | None:
    if not os.path.lexists(target):
        return None
    return _require_matching_event(target, root, raw, event_id)


def _require_matching_event(
    target: Path,
    root: Path,
    raw: bytes,
    event_id: str,
) -> PublicationRegistration:
    try:
        existing = _read_regular_bytes_no_follow(
            target, root, "index inbox event", len(raw)
        )
    except PublicationRegistrarError as exc:
        raise PublicationRegistrarConflict(
            "existing index inbox event is unsafe"
        ) from exc
    if existing != raw:
        raise PublicationRegistrarConflict(
            "event_id is already bound to different inbox content"
        )
    return PublicationRegistration(event_id, target, False)


def _acquire_event_lock(
    lock_path: Path,
    target: Path,
    root: Path,
    raw: bytes,
    event_id: str,
    token: bytes,
) -> PublicationRegistration | None:
    deadline = time.monotonic() + _LOCK_WAIT_SECONDS
    while True:
        try:
            _write_new_flushed(lock_path, token)
            return None
        except FileExistsError:
            existing = _existing_event(target, root, raw, event_id)
            if existing is not None:
                return existing
            try:
                _assert_regular_file(lock_path, root, "index inbox lock")
            except PublicationRegistrarError as exc:
                raise PublicationRegistrarConflict(
                    "index inbox event lock is unsafe"
                ) from exc
            if time.monotonic() >= deadline:
                raise PublicationRegistrarConflict("index inbox event is locked")
            time.sleep(0.005)


def _release_event_lock(path: Path, root: Path, token: bytes) -> None:
    if not os.path.lexists(path):
        raise PublicationRegistrarConflict("owned index inbox lock disappeared")
    try:
        current = _read_regular_bytes_no_follow(
            path, root, "index inbox lock", len(token)
        )
    except PublicationRegistrarError as exc:
        raise PublicationRegistrarConflict("owned index inbox lock is unsafe") from exc
    if current != token:
        raise PublicationRegistrarConflict("owned index inbox lock changed")
    try:
        path.unlink()
        flush_directory(path.parent)
    except OSError as exc:
        raise PublicationRegistrarError("index inbox lock could not be released") from exc


def _write_new_flushed(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    flush_directory(path.parent)


def _remove_owned_temporary(path: Path) -> None:
    if not os.path.lexists(path):
        return
    try:
        path.unlink()
    except OSError:
        pass


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PublicationRegistrarError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(token: str) -> None:
    raise PublicationRegistrarError(f"non-finite JSON value is invalid: {token}")


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _identity_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 0x20 for character in value)
        or any(character in value for character in ("/", "\\", ":"))
    ):
        raise PublicationRegistrarError(f"invalid {label}")
    return value


def _utc(value: Any) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PublicationRegistrarError("invalid workflow created_utc")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PublicationRegistrarError("invalid workflow created_utc") from exc
    return value


def _absolute(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise PublicationRegistrarError("path must be a string or Path")
    return Path(os.path.abspath(os.fspath(value)))


def _is_link_or_reparse(path: Path, info: os.stat_result) -> bool:
    return (
        path.is_symlink()
        or bool(getattr(path, "is_junction", lambda: False)())
        or bool(getattr(info, "st_reparse_tag", 0))
        or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()
