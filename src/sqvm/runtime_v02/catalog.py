"""Version-aware, rebuildable catalog isolated from frozen Runtime 0.1."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.models import CatalogRebuildReport
from sqvm.runtime.recovery import _verify_recovery_record
from sqvm.runtime.storage import flush_directory


CATALOG_NAME = "catalog_v02.sqlite"
LOCK_NAME = "catalog_v02.lock"


def index_run_v02(run_dir: str | Path, output_root: str | Path) -> None:
    directory = Path(run_dir)
    root = Path(output_root)
    manifest = _load_canonical(directory / "manifest.json")
    receipt = _load_canonical(directory / "receipt.json")
    _with_catalog(root, lambda connection: _insert_run(connection, manifest, receipt, _raw_sha(directory / "receipt.json")))


def index_recovery_record_v02(run_dir: str | Path, output_root: str | Path) -> None:
    directory = Path(run_dir)
    root = Path(output_root)
    recovery = _load_canonical(directory / "recovery.json")
    _verify_recovery_record(directory)
    _with_catalog(root, lambda connection: _insert_recovery(connection, recovery, _raw_sha(directory / "manifest.json"), _raw_sha(directory / "receipt.json")))


def rebuild_run_catalog_versioned(output_root: str | Path) -> CatalogRebuildReport:
    root = Path(output_root).resolve()
    lock = root / LOCK_NAME
    _acquire(lock)
    temporary = root / f"{CATALOG_NAME}.rebuild.{uuid.uuid4().hex}"
    blockers: list[str] = []
    count = 0
    try:
        connection = sqlite3.connect(temporary)
        try:
            _initialize(connection)
            for run_dir in sorted((root / "runs").iterdir(), key=lambda path: path.name):
                if not run_dir.is_dir() or run_dir.is_symlink():
                    blockers.append(f"unsafe run catalog entry: {run_dir.name}")
                    continue
                if (run_dir / "recovery.json").is_file():
                    try:
                        _verify_recovery_record(run_dir)
                    except Exception:
                        blockers.append(f"unverified recovery excluded: {run_dir.name}")
                        continue
                    recovery = _load_canonical(run_dir / "recovery.json")
                    _insert_recovery(connection, recovery, _raw_sha(run_dir / "manifest.json"), _raw_sha(run_dir / "receipt.json"))
                else:
                    from sqvm.runtime_api import verify_experiment_run_versioned

                    if not verify_experiment_run_versioned(run_dir).ok:
                        blockers.append(f"unverified run excluded: {run_dir.name}")
                        continue
                    manifest = _load_canonical(run_dir / "manifest.json")
                    receipt = _load_canonical(run_dir / "receipt.json")
                    _insert_run(connection, manifest, receipt, _raw_sha(run_dir / "receipt.json"))
                count += 1
            connection.commit()
            if connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] != count:
                raise ValueError("versioned catalog rebuild row count mismatch")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
        if blockers:
            temporary.unlink(missing_ok=True)
            return CatalogRebuildReport(False, count, root / CATALOG_NAME, tuple(blockers))
        with temporary.open("rb+") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, root / CATALOG_NAME)
        flush_directory(root)
        return CatalogRebuildReport(True, count, root / CATALOG_NAME, ())
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        return CatalogRebuildReport(False, count, root / CATALOG_NAME, (str(exc),))
    finally:
        lock.unlink(missing_ok=True)
        flush_directory(root)


def _with_catalog(root: Path, operation) -> None:
    lock = root / LOCK_NAME
    _acquire(lock)
    try:
        connection = sqlite3.connect(root / CATALOG_NAME)
        try:
            _initialize(connection)
            operation(connection)
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
    finally:
        lock.unlink(missing_ok=True)
        flush_directory(root)


def _initialize(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
    connection.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (1)")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY,status TEXT NOT NULL,created_utc TEXT NOT NULL,"
        "terminal_utc TEXT NOT NULL,experiment_id TEXT NOT NULL,backend_id TEXT NOT NULL,runtime_schema TEXT NOT NULL,"
        "evidence_class TEXT NOT NULL,manifest_sha256 TEXT NOT NULL,receipt_sha256 TEXT NOT NULL)"
    )


def _insert_run(connection: sqlite3.Connection, manifest: Mapping[str, Any], receipt: Mapping[str, Any], receipt_sha: str) -> None:
    claim = manifest.get("claim_envelope")
    if not isinstance(claim, Mapping) or not isinstance(claim.get("evidence_class"), str):
        raise ValueError("catalog run claim envelope is invalid")
    connection.execute(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (manifest["run_id"], manifest["status"], manifest["created_utc"], manifest["terminal_utc"],
         manifest["experiment_id"], manifest["backend_id"], manifest["schema_version"], claim["evidence_class"],
         receipt["manifest_sha256"], receipt_sha),
    )


def _insert_recovery(connection: sqlite3.Connection, recovery: Mapping[str, Any], manifest_sha: str, receipt_sha: str) -> None:
    reason = str(recovery.get("reason", ""))
    runtime_schema = "0.2" if reason.startswith("runtime_v02|") else "0.1"
    connection.execute(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
        (recovery["recovery_id"], recovery["status"], "", "", "recovery_record", "none", runtime_schema,
         "recovery_record", manifest_sha, receipt_sha),
    )


def _load_canonical(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError(f"versioned catalog input is not canonical: {path.name}")
    return payload


def _raw_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _acquire(path: Path) -> None:
    with path.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
        stream.flush()
        os.fsync(stream.fileno())
