"""Canonical, hash-chained Stage 6 run event journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping


EVENT_KEYS = {
    "schema_version",
    "run_id",
    "sequence",
    "event_type",
    "utc_time",
    "monotonic_ns",
    "payload",
    "prev_event_sha256",
    "event_sha256",
}
PAYLOAD_KEYS = {
    "run_reserved": {"request_sha256", "point_table_sha256", "run_lock_sha256", "resource_lock_sha256"},
    "run_prepared": {"experiment_id", "backend_id", "backend_capabilities_sha256"},
    "run_started": {"point_count"},
    "point_started": {"point_index", "point_id"},
    "point_completed": {"point_index", "point_id", "result_sha256"},
    "point_failed": {"point_index", "point_id", "error_class", "message"},
    "cancel_requested": {"cancellation_request_sha256"},
    "run_finalizing": {"completed_point_count"},
    "run_completed": {"dataset_manifest_sha256", "completed_point_count"},
    "run_failed": {"error_class", "message", "completed_point_count"},
    "run_cancelled": {"completed_point_count"},
}
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_SHA = re.compile(r"^[0-9A-F]{64}$")


def canonical_json_line_bytes(payload: Mapping[str, Any]) -> bytes:
    """Encode one compact canonical JSON line."""

    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class JournalSummary:
    event_count: int
    tail_event_sha256: str
    raw_sha256: str


class EventJournal:
    """Append validated lifecycle events to a new journal file."""

    def __init__(self, path: str | Path, run_id: str):
        self.path = Path(path)
        self.run_id = run_id
        if self.path.exists():
            raise FileExistsError(f"event journal already exists: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("xb")
        self._sequence = 0
        self._tail: str | None = None

    @property
    def tail_event_sha256(self) -> str | None:
        return self._tail

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def append(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        utc_time: str | None = None,
        monotonic_ns: int | None = None,
    ) -> Mapping[str, Any]:
        if self._stream.closed:
            raise ValueError("event journal is closed")
        _validate_payload(event_type, payload)
        base = {
            "schema_version": "0.1",
            "run_id": self.run_id,
            "sequence": self._sequence,
            "event_type": event_type,
            "utc_time": utc_now_text() if utc_time is None else utc_time,
            "monotonic_ns": time.monotonic_ns() if monotonic_ns is None else monotonic_ns,
            "payload": dict(payload),
            "prev_event_sha256": self._tail,
        }
        digest = hashlib.sha256(canonical_json_line_bytes(base)).hexdigest().upper()
        event = {**base, "event_sha256": digest}
        _validate_event(event, expected_run_id=self.run_id, expected_sequence=self._sequence, expected_prev=self._tail)
        self._stream.write(canonical_json_line_bytes(event))
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._sequence += 1
        self._tail = digest
        return event

    def close(self) -> JournalSummary:
        if not self._stream.closed:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            self._stream.close()
        if self._tail is None:
            raise ValueError("event journal cannot be empty")
        return JournalSummary(self._sequence, self._tail, _raw_sha256(self.path))

    def __enter__(self) -> "EventJournal":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if not self._stream.closed:
            self._stream.close()


def verify_event_journal(path: str | Path, run_id: str) -> JournalSummary:
    source = Path(path)
    raw = source.read_bytes()
    if not raw or not raw.endswith(b"\n") or b"\r" in raw:
        raise ValueError("events journal must be nonempty UTF-8/LF JSONL")
    previous: str | None = None
    count = 0
    for line in raw.splitlines(keepends=True):
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("events journal contains invalid JSON") from exc
        if not isinstance(event, dict) or line != canonical_json_line_bytes(event):
            raise ValueError("events journal line is not canonical")
        _validate_event(event, expected_run_id=run_id, expected_sequence=count, expected_prev=previous)
        previous = event["event_sha256"]
        count += 1
    if previous is None:
        raise ValueError("events journal cannot be empty")
    return JournalSummary(count, previous, hashlib.sha256(raw).hexdigest().upper())


def _validate_event(event: Mapping[str, Any], *, expected_run_id: str, expected_sequence: int, expected_prev: str | None) -> None:
    if set(event) != EVENT_KEYS or event.get("schema_version") != "0.1":
        raise ValueError("event schema is invalid")
    if event.get("run_id") != expected_run_id or event.get("sequence") != expected_sequence:
        raise ValueError("event identity or sequence is invalid")
    if event.get("prev_event_sha256") != expected_prev:
        raise ValueError("event hash chain is invalid")
    utc_value = event.get("utc_time")
    if not isinstance(utc_value, str) or not _UTC.fullmatch(utc_value):
        raise ValueError("event utc_time is invalid")
    monotonic = event.get("monotonic_ns")
    if isinstance(monotonic, bool) or not isinstance(monotonic, int) or monotonic < 0:
        raise ValueError("event monotonic_ns is invalid")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("event payload must be a mapping")
    _validate_payload(str(event.get("event_type")), payload)
    digest = event.get("event_sha256")
    if not isinstance(digest, str) or not _SHA.fullmatch(digest):
        raise ValueError("event_sha256 is invalid")
    without_hash = {key: value for key, value in event.items() if key != "event_sha256"}
    expected = hashlib.sha256(canonical_json_line_bytes(without_hash)).hexdigest().upper()
    if digest != expected:
        raise ValueError("event_sha256 does not match canonical event bytes")


def _validate_payload(event_type: str, payload: Mapping[str, Any]) -> None:
    expected = PAYLOAD_KEYS.get(event_type)
    if expected is None or set(payload) != expected:
        raise ValueError(f"{event_type} payload keys are invalid")
    message = payload.get("message")
    if message is not None:
        if not isinstance(message, str) or len(message) > 1024 or "\n" in message or "\r" in message:
            raise ValueError("event message is invalid")


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
