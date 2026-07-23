"""Background coordination for inbox, carrier, and persistent Web projections."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import threading
import time
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.storage import flush_directory
from sqvm.web.index import CalibrationWebIndex, WebArtifactError


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
_MAX_EVENT_BYTES = 64 * 1024
_SUMMARY_KEYS = frozenset(
    {
        "run_id",
        "workflow_id",
        "experiment_kind",
        "status",
        "created_utc",
        "data_origin",
        "verification_status",
        "targets",
        "execution_mode",
        "recommendation_applicable",
        "recommendation_eligible",
        "parent_calibration",
        "gate_summary",
        "candidate_summary",
        "relative_path",
        "error",
    }
)


class WebProjectionCoordinator:
    """Single Web-process writer for persistent experiment projections."""

    def __init__(
        self,
        index: CalibrationWebIndex,
        storage: Any,
        *,
        repository_root: str | Path,
        storage_root: str | Path,
        poll_interval_s: float = 0.5,
        shallow_reconcile_interval_s: float = 30.0,
    ) -> None:
        self.index = index
        self.storage = storage
        self.repository_root = Path(repository_root).resolve()
        self.storage_root = Path(storage_root).resolve()
        self.inbox_root = self.storage_root / "index-inbox"
        self.quarantine_root = self.storage_root / "index-quarantine"
        self.poll_interval_s = poll_interval_s
        self.shallow_reconcile_interval_s = shallow_reconcile_interval_s
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._last_error: str | None = None
        self._last_success_utc: str | None = None
        self._pending_count = 0
        self._quarantine_count = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.index.reconcile_experiments()
        self._thread = threading.Thread(
            target=self._run,
            name="sqvm-web-projection-coordinator",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        self._thread = None

    def notify(self) -> None:
        self._wake.set()

    def status(self) -> dict[str, object]:
        with self._status_lock:
            return {
                "status": (
                    "degraded"
                    if self._last_error or self._pending_count or self._quarantine_count
                    else "ok"
                ),
                "last_error": self._last_error,
                "last_success_utc": self._last_success_utc,
                "pending_projection_count": self._pending_count,
                "quarantined_event_count": self._quarantine_count,
                "running": self._thread is not None and self._thread.is_alive(),
            }

    def run_once(self, *, shallow_reconcile: bool = False) -> None:
        failures: list[Exception] = []

        def attempt(operation) -> None:
            try:
                operation()
            except Exception as exc:
                failures.append(exc)

        attempt(self._consume_inbox)
        attempt(self._sync_storage_projections)
        if shallow_reconcile:
            attempt(self.index.reconcile_experiments)
            attempt(self._sync_storage_projections)
        if failures:
            with self._status_lock:
                self._last_error = type(failures[0]).__name__
        else:
            self._record_success()

    def _run(self) -> None:
        deadline = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            reconcile = now >= deadline
            self.run_once(shallow_reconcile=reconcile)
            if reconcile:
                deadline = now + self.shallow_reconcile_interval_s
            self._wake.wait(self.poll_interval_s)
            self._wake.clear()

    def _consume_inbox(self) -> None:
        events = self._event_files()
        failures: list[Exception] = []
        with self._status_lock:
            self._pending_count = len(events)
        for path in events:
            try:
                event = _read_event(path, self.inbox_root)
            except Exception:
                self._quarantine(path)
                continue
            try:
                projected = self.index.project_experiment_path(
                    event["source_relative_path"]
                )
                identity = projected.get("identity")
                if (
                    projected.get("run_id") != event["run_id"]
                    or not isinstance(identity, Mapping)
                    or identity.get("workflow_sha256") != event["workflow_sha256"]
                    or identity.get("receipt_sha256") != event["receipt_sha256"]
                ):
                    self._quarantine(path)
                    continue
                if not _matches_carrier_identity(
                    path, event["_carrier_identity"]
                ):
                    raise RuntimeError("index inbox event changed before consumption")
                path.unlink()
                flush_directory(self.inbox_root)
            except Exception as exc:
                # Projection/storage failures are retryable. Keep the durable
                # inbox hint so a later coordinator pass can recover it.
                failures.append(exc)
        with self._status_lock:
            self._pending_count = len(self._event_files())
            self._quarantine_count = len(self._quarantine_files())
        if failures:
            raise RuntimeError("one or more inbox projections remain pending") from failures[0]

    def _event_files(self) -> list[Path]:
        if not self.inbox_root.is_dir() or _is_link_or_reparse(self.inbox_root):
            return []
        try:
            entries = sorted(os.scandir(self.inbox_root), key=lambda row: row.name)
        except OSError:
            return []
        return [Path(entry.path) for entry in entries if entry.name.endswith(".json")]

    def _quarantine_files(self) -> list[Path]:
        if not self.quarantine_root.is_dir() or _is_link_or_reparse(self.quarantine_root):
            return []
        try:
            with os.scandir(self.quarantine_root) as entries:
                return [
                    Path(entry.path)
                    for entry in entries
                    if entry.name.endswith(".json")
                    and not entry.is_symlink()
                    and entry.is_file(follow_symlinks=False)
                ]
        except OSError:
            return []

    def _quarantine(self, path: Path) -> None:
        try:
            info = path.lstat()
            if (
                path.parent != self.inbox_root
                or _is_link_or_reparse(path, info)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
            ):
                return
            self.quarantine_root.mkdir(parents=False, exist_ok=True)
            if _is_link_or_reparse(self.quarantine_root) or not self.quarantine_root.is_dir():
                return
            target = self.quarantine_root / path.name
            if target.exists():
                target = self.quarantine_root / f"{path.stem}.{os.urandom(4).hex()}.json"
            os.replace(path, target)
            flush_directory(self.quarantine_root)
            flush_directory(self.inbox_root)
        except OSError:
            return

    def _sync_storage_projections(self) -> None:
        failures: list[Exception] = []
        for row in self.storage.projection_rows():
            run_id = row.get("run_id")
            state = row.get("storage_state")
            alias = row.get("carrier_alias")
            workflow_sha256 = row.get("workflow_sha256")
            receipt_sha256 = row.get("receipt_sha256")
            if not all(
                isinstance(value, str) and value
                for value in (run_id, state, workflow_sha256, receipt_sha256)
            ):
                continue
            try:
                self.index.mark_carrier_state(
                    run_id,
                    state,
                    alias if isinstance(alias, str) else None,
                    workflow_sha256=workflow_sha256,
                    receipt_sha256=receipt_sha256,
                )
                self._acknowledge_inbox_identity(
                    run_id, workflow_sha256, receipt_sha256
                )
                continue
            except WebArtifactError as exc:
                if exc.status != 404:
                    continue
            if state in {"hot", "trash"}:
                try:
                    source = self.storage.projection_directory(run_id)
                    if source is None:
                        continue
                    relative = Path(source).relative_to(self.repository_root).as_posix()
                    projected = self.index.project_experiment_path(relative)
                    identity = projected.get("identity")
                    if (
                        projected.get("run_id") != run_id
                        or not isinstance(identity, Mapping)
                        or identity.get("workflow_sha256") != workflow_sha256
                        or identity.get("receipt_sha256") != receipt_sha256
                    ):
                        raise ValueError("storage directory projection identity changed")
                    self.index.mark_carrier_state(
                        run_id,
                        state,
                        alias if isinstance(alias, str) else None,
                        workflow_sha256=workflow_sha256,
                        receipt_sha256=receipt_sha256,
                    )
                    self._acknowledge_inbox_identity(
                        run_id, workflow_sha256, receipt_sha256
                    )
                except Exception as exc:
                    failures.append(exc)
                continue
            if state not in {"archived", "archived_duplicate"}:
                continue
            try:
                detail = self.storage.experiment_detail(run_id)
                summary = _summary_from_detail(detail)
                self.index.upsert_projected_detail(
                    summary,
                    detail,
                    {
                        "workflow_sha256": workflow_sha256,
                        "receipt_sha256": receipt_sha256,
                    },
                    {
                        "state": state,
                        "alias": alias,
                    },
                )
                self._acknowledge_inbox_identity(
                    run_id, workflow_sha256, receipt_sha256
                )
            except Exception as exc:
                failures.append(exc)
        if failures:
            raise RuntimeError("one or more storage projections failed") from failures[0]

    def _acknowledge_inbox_identity(
        self, run_id: str, workflow_sha256: str, receipt_sha256: str
    ) -> None:
        """Consume stale hints once the same trusted identity is projected."""

        for path in self._event_files():
            try:
                event = _read_event(path, self.inbox_root)
            except Exception:
                continue
            if (
                event.get("run_id") != run_id
                or event.get("workflow_sha256") != workflow_sha256
                or event.get("receipt_sha256") != receipt_sha256
            ):
                continue
            if not _matches_carrier_identity(path, event["_carrier_identity"]):
                raise RuntimeError("index inbox event changed before acknowledgement")
            path.unlink()
            flush_directory(self.inbox_root)
        with self._status_lock:
            self._pending_count = len(self._event_files())

    def _record_success(self) -> None:
        with self._status_lock:
            self._last_error = None
            self._last_success_utc = datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )


def _read_event(path: Path, inbox_root: Path) -> dict[str, Any]:
    info = path.lstat()
    if (
        path.parent != inbox_root
        or _is_link_or_reparse(path, info)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_size > _MAX_EVENT_BYTES
    ):
        raise ValueError("index inbox event carrier is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        chunks = []
        remaining = _MAX_EVENT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 16 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > _MAX_EVENT_BYTES:
            raise ValueError("index inbox event exceeds its size limit")
    finally:
        os.close(descriptor)
    after = path.lstat()
    identity = (info.st_dev, info.st_ino, info.st_size)
    if (
        (opened.st_dev, opened.st_ino, opened.st_size) != identity
        or (after.st_dev, after.st_ino, after.st_size) != identity
        or opened.st_nlink != 1
        or after.st_nlink != 1
        or _is_link_or_reparse(path, after)
        or not stat.S_ISREG(after.st_mode)
        or len(raw) != info.st_size
    ):
        raise ValueError("index inbox event changed while reading")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate inbox event key")
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite inbox event value: {token}")
        ),
    )
    if (
        not isinstance(value, dict)
        or set(value) != _EVENT_KEYS
        or value.get("schema") != "sqvm.web.index_inbox_event"
        or value.get("type") != "published_run"
        or value.get("version") != "0.1"
        or not _finite(value)
        or raw != canonical_json_bytes(value)
        or path.name != f"{value.get('event_id')}.json"
    ):
        raise ValueError("index inbox event contract is invalid")
    for field in _EVENT_KEYS - {"schema", "type", "version"}:
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError("index inbox event identity is invalid")
    binding = {
        "run_id": value["run_id"],
        "workflow_sha256": value["workflow_sha256"],
        "receipt_sha256": value["receipt_sha256"],
    }
    expected_event_id = hashlib.sha256(canonical_json_bytes(binding)).hexdigest().upper()
    if value["event_id"] != expected_event_id:
        raise ValueError("index inbox event ID is invalid")
    value["_carrier_identity"] = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    return value


def _summary_from_detail(detail: Mapping[str, object]) -> dict[str, object]:
    summary = {key: detail.get(key) for key in _SUMMARY_KEYS}
    if not isinstance(summary.get("run_id"), str):
        raise ValueError("archived projection run_id is invalid")
    return summary


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite(item) for item in value)
    return True


def _is_link_or_reparse(
    path: Path, info: os.stat_result | None = None
) -> bool:
    try:
        inspected = info if info is not None else path.lstat()
    except OSError:
        return True
    return (
        path.is_symlink()
        or bool(getattr(path, "is_junction", lambda: False)())
        or bool(getattr(inspected, "st_reparse_tag", 0))
        or bool(
            getattr(inspected, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )
    )


def _matches_carrier_identity(path: Path, expected: object) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        expected
        == (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )
        and info.st_nlink == 1
        and stat.S_ISREG(info.st_mode)
        and not _is_link_or_reparse(path, info)
    )


__all__ = ["WebProjectionCoordinator"]
