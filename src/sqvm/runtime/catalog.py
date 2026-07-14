"""Rebuildable SQLite query catalog for immutable Stage 6 runs."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.journal import utc_now_text
from sqvm.runtime.models import CatalogRebuildReport
from sqvm.runtime.storage import flush_directory


SCHEMA_VERSION = 1


def index_run(run_dir: str | Path, output_root: str | Path) -> None:
    directory = Path(run_dir)
    root = Path(output_root)
    manifest = _load_canonical(directory / "manifest.json")
    receipt = _load_canonical(directory / "receipt.json")
    lock = root / "catalog.lock"
    _acquire(lock)
    try:
        connection = sqlite3.connect(root / "catalog.sqlite")
        try:
            _initialize(connection)
            _insert(connection, manifest, receipt, _raw_sha256(directory / "receipt.json"))
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
    finally:
        lock.unlink(missing_ok=True)
        flush_directory(root)


def index_recovery_record(run_dir: str | Path, output_root: str | Path) -> None:
    directory = Path(run_dir)
    root = Path(output_root)
    recovery = _load_canonical(directory / "recovery.json")
    manifest_sha = _raw_sha256(directory / "manifest.json")
    receipt_sha = _raw_sha256(directory / "receipt.json")
    lock = root / "catalog.lock"
    _acquire(lock)
    try:
        connection = sqlite3.connect(root / "catalog.sqlite")
        try:
            _initialize(connection)
            _insert_recovery(connection, recovery, manifest_sha, receipt_sha)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
    finally:
        lock.unlink(missing_ok=True)
        flush_directory(root)


def rebuild_run_catalog(output_root: str | Path) -> CatalogRebuildReport:
    root = Path(output_root).resolve()
    lock = root / "catalog.lock"
    _acquire(lock)
    temporary = root / f"catalog.sqlite.rebuild.{uuid.uuid4().hex}"
    blockers: list[str] = []
    count = 0
    try:
        connection = sqlite3.connect(temporary)
        try:
            _initialize(connection)
            from sqvm.runtime.verify import verify_experiment_run

            for run_dir in sorted((root / "runs").iterdir(), key=lambda path: path.name):
                if not run_dir.is_dir() or run_dir.is_symlink():
                    blockers.append(f"unsafe run catalog entry: {run_dir.name}")
                    continue
                if (run_dir / "recovery.json").is_file():
                    from sqvm.runtime.recovery import _verify_recovery_record

                    try:
                        _verify_recovery_record(run_dir)
                    except Exception:
                        blockers.append(f"unverified recovery excluded: {run_dir.name}")
                        continue
                    recovery = _load_canonical(run_dir / "recovery.json")
                    _insert_recovery(connection, recovery, _raw_sha256(run_dir / "manifest.json"), _raw_sha256(run_dir / "receipt.json"))
                else:
                    report = verify_experiment_run(run_dir)
                    if not report.ok:
                        blockers.append(f"unverified run excluded: {run_dir.name}")
                        continue
                    manifest = _load_canonical(run_dir / "manifest.json")
                    receipt = _load_canonical(run_dir / "receipt.json")
                    _insert(connection, manifest, receipt, _raw_sha256(run_dir / "receipt.json"))
                count += 1
            connection.commit()
            if connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] != count:
                raise ValueError("catalog rebuild row count mismatch")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        if blockers:
            temporary.unlink(missing_ok=True)
            return CatalogRebuildReport(False, count, root / "catalog.sqlite", tuple(blockers))
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, root / "catalog.sqlite")
        flush_directory(root)
        return CatalogRebuildReport(True, count, root / "catalog.sqlite", ())
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        return CatalogRebuildReport(False, count, root / "catalog.sqlite", (str(exc),))
    finally:
        lock.unlink(missing_ok=True)
        flush_directory(root)


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
    connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (SCHEMA_VERSION,))
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            terminal_utc TEXT NOT NULL,
            experiment_id TEXT NOT NULL,
            backend_id TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL
        )
        """
    )


def _insert(connection: sqlite3.Connection, manifest: Mapping[str, Any], receipt: Mapping[str, Any], receipt_sha256: str) -> None:
    connection.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
        "status=excluded.status,created_utc=excluded.created_utc,terminal_utc=excluded.terminal_utc,"
        "experiment_id=excluded.experiment_id,backend_id=excluded.backend_id,"
        "manifest_sha256=excluded.manifest_sha256,receipt_sha256=excluded.receipt_sha256",
        (
            manifest["run_id"],
            manifest["status"],
            manifest["created_utc"],
            manifest["terminal_utc"],
            manifest["experiment_id"],
            manifest["backend_id"],
            receipt["manifest_sha256"],
            receipt_sha256,
        ),
    )


def _insert_recovery(connection: sqlite3.Connection, recovery: Mapping[str, Any], manifest_sha256: str, receipt_sha256: str) -> None:
    connection.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET "
        "status=excluded.status,manifest_sha256=excluded.manifest_sha256,receipt_sha256=excluded.receipt_sha256",
        (
            recovery["recovery_id"],
            recovery["status"],
            "",
            "",
            "recovery_record",
            "none",
            manifest_sha256,
            receipt_sha256,
        ),
    )


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _load_canonical(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError(f"catalog input is not canonical: {path.name}")
    return payload


def _acquire(path: Path) -> None:
    payload = {"schema_version": "0.1", "created_utc": utc_now_text(), "process_id": os.getpid()}
    with path.open("xb") as stream:
        stream.write(canonical_json_bytes(payload))
        stream.flush()
        os.fsync(stream.fileno())
    flush_directory(path.parent)
