"""Persistent, non-authoritative Web projections for experiment visualization."""

from __future__ import annotations

from dataclasses import dataclass
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
from typing import Any, Iterator, Mapping, Sequence


READ_MODEL_SCHEMA_VERSION = 1
PUBLIC_PAGE_SCHEMA_VERSION = "0.2"
_MAX_PAGE_SIZE = 200


class ReadModelError(ValueError):
    pass


@dataclass(frozen=True)
class ExperimentProjection:
    run_id: str
    workflow_sha256: str
    receipt_sha256: str
    summary: Mapping[str, Any]
    detail: Mapping[str, Any]
    targets: tuple[str, ...] = ()
    dataset_bindings: tuple[Mapping[str, Any], ...] = ()
    plot_bindings: tuple[Mapping[str, Any], ...] = ()
    carrier_state: str = "hot"
    carrier_alias: str | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.run_id, self.workflow_sha256, self.receipt_sha256


class PersistentExperimentReadModel:
    """SQLite read model; callers remain responsible for source verification."""

    def __init__(self, path: str | Path) -> None:
        self.path = _prepare_database_path(path)
        self._lock = threading.RLock()
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        _assert_database_carriers(self.path)
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=NORMAL")
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > READ_MODEL_SCHEMA_VERSION:
                raise ReadModelError("Web read-model schema is newer than this application")
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS read_model_meta(
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS schema_migrations(
                        version INTEGER PRIMARY KEY,
                        applied_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS runs(
                        projection_id INTEGER PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        workflow_sha256 TEXT NOT NULL,
                        receipt_sha256 TEXT NOT NULL,
                        created_utc TEXT NOT NULL,
                        workflow_id TEXT NOT NULL,
                        verification_status TEXT NOT NULL,
                        recommendation_eligible INTEGER NOT NULL,
                        relative_path TEXT NOT NULL,
                        summary_json TEXT NOT NULL,
                        detail_json TEXT NOT NULL,
                        carrier_state TEXT NOT NULL,
                        carrier_alias TEXT,
                        identity_conflict INTEGER NOT NULL DEFAULT 0,
                        active INTEGER NOT NULL DEFAULT 1,
                        updated_revision INTEGER NOT NULL,
                        UNIQUE(run_id, workflow_sha256, receipt_sha256)
                    );
                    CREATE INDEX IF NOT EXISTS runs_page_idx
                        ON runs(active, created_utc DESC, run_id DESC, workflow_sha256 DESC);
                    CREATE INDEX IF NOT EXISTS runs_run_id_idx ON runs(active, run_id);
                    CREATE TABLE IF NOT EXISTS run_targets(
                        projection_id INTEGER NOT NULL REFERENCES runs(projection_id) ON DELETE CASCADE,
                        target TEXT NOT NULL,
                        PRIMARY KEY(projection_id, target)
                    );
                    CREATE INDEX IF NOT EXISTS run_targets_target_idx ON run_targets(target, projection_id);
                    CREATE TABLE IF NOT EXISTS dataset_bindings(
                        projection_id INTEGER NOT NULL REFERENCES runs(projection_id) ON DELETE CASCADE,
                        binding_name TEXT NOT NULL,
                        path TEXT,
                        sha256 TEXT,
                        PRIMARY KEY(projection_id, binding_name)
                    );
                    CREATE TABLE IF NOT EXISTS plot_bindings(
                        projection_id INTEGER NOT NULL REFERENCES runs(projection_id) ON DELETE CASCADE,
                        plot_id TEXT NOT NULL,
                        plot_type TEXT,
                        PRIMARY KEY(projection_id, plot_id)
                    );
                    CREATE TABLE IF NOT EXISTS projection_events(
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        revision INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        run_id TEXT,
                        event_json TEXT NOT NULL,
                        created_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );
                    CREATE TABLE IF NOT EXISTS aggregate_counters(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        total INTEGER NOT NULL,
                        eligible INTEGER NOT NULL,
                        invalid INTEGER NOT NULL,
                        revision INTEGER NOT NULL
                    );
                    """
                )
                connection.execute(
                    "INSERT OR IGNORE INTO read_model_meta(key,value) VALUES('revision','0')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO read_model_meta(key,value) VALUES('source_digest','')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO read_model_meta(key,value) VALUES('cursor_secret',?)",
                    (secrets.token_hex(32),),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO aggregate_counters VALUES(1,0,0,0,0)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO schema_migrations(version) VALUES(?)",
                    (READ_MODEL_SCHEMA_VERSION,),
                )
                connection.execute(f"PRAGMA user_version={READ_MODEL_SCHEMA_VERSION}")

    def reconcile(self, projections: Sequence[ExperimentProjection]) -> int:
        normalized = tuple(sorted(projections, key=lambda row: row.identity))
        source_digest = _projection_digest(normalized)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current_digest = self._meta(connection, "source_digest")
            if current_digest == source_digest:
                return self._revision(connection)
            revision = self._revision(connection) + 1
            active_identities = set()
            for projection in normalized:
                active_identities.add(projection.identity)
                self._upsert(
                    connection,
                    projection,
                    revision,
                    preserve_managed_carrier=True,
                )
            for row in connection.execute(
                "SELECT run_id,workflow_sha256,receipt_sha256 FROM runs "
                "WHERE active=1 AND carrier_state='hot'"
            ):
                identity = tuple(row)
                if identity not in active_identities:
                    connection.execute(
                        "UPDATE runs SET active=0,updated_revision=? "
                        "WHERE run_id=? AND workflow_sha256=? AND receipt_sha256=?",
                        (revision, *identity),
                    )
            self._refresh_conflicts(connection)
            self._refresh_aggregates(connection, revision)
            self._set_meta(connection, "source_digest", source_digest)
            self._set_meta(connection, "revision", str(revision))
            connection.execute(
                "INSERT INTO projection_events(revision,event_type,event_json) VALUES(?,?,?)",
                (revision, "reconcile", _json({"active": len(active_identities)})),
            )
            return revision

    def upsert_projection(self, projection: ExperimentProjection) -> int:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT projection_id,summary_json,detail_json,carrier_state,carrier_alias,active "
                "FROM runs WHERE run_id=? AND workflow_sha256=? AND receipt_sha256=?",
                projection.identity,
            ).fetchone()
            if existing is not None and self._stored_material(connection, existing) == {
                "summary": _json(projection.summary),
                "detail": _json(projection.detail),
                "carrier_state": projection.carrier_state,
                "carrier_alias": projection.carrier_alias,
                "active": 1,
                "targets": sorted(set(projection.targets)),
                "datasets": sorted(
                    (
                        str(row.get("name")),
                        row.get("path"),
                        row.get("sha256"),
                    )
                    for row in projection.dataset_bindings
                ),
                "plots": sorted(
                    (str(row.get("plot_id")), row.get("plot_type"))
                    for row in projection.plot_bindings
                ),
            }:
                return self._revision(connection)
            revision = self._revision(connection) + 1
            self._upsert(connection, projection, revision)
            self._refresh_conflicts(connection)
            self._refresh_aggregates(connection, revision)
            self._set_meta(connection, "revision", str(revision))
            connection.execute(
                "INSERT INTO projection_events(revision,event_type,run_id,event_json) "
                "VALUES(?,?,?,?)",
                (revision, "upsert", projection.run_id, _json({"identity": projection.identity})),
            )
            return revision

    def mark_carrier_state(
        self,
        run_id: str,
        state: str,
        alias: str | None,
        *,
        workflow_sha256: str | None = None,
        receipt_sha256: str | None = None,
    ) -> int:
        if not run_id or not state:
            raise ReadModelError("run_id and carrier state are required")
        if (workflow_sha256 is None) != (receipt_sha256 is None):
            raise ReadModelError("carrier identity hashes must be provided together")
        identity_clause = ""
        identity_parameters: tuple[str, ...] = ()
        if workflow_sha256 is not None and receipt_sha256 is not None:
            identity_clause = " AND workflow_sha256=? AND receipt_sha256=?"
            identity_parameters = (workflow_sha256, receipt_sha256)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT projection_id,carrier_state,carrier_alias FROM runs "
                "WHERE active=1 AND run_id=?" + identity_clause,
                (run_id, *identity_parameters),
            ).fetchall()
            if not rows:
                raise ReadModelError("experiment projection not found")
            if all(row["carrier_state"] == state and row["carrier_alias"] == alias for row in rows):
                return self._revision(connection)
            revision = self._revision(connection) + 1
            connection.execute(
                "UPDATE runs SET carrier_state=?,carrier_alias=?,updated_revision=? "
                "WHERE active=1 AND run_id=?" + identity_clause,
                (state, alias, revision, run_id, *identity_parameters),
            )
            self._set_meta(connection, "revision", str(revision))
            self._refresh_aggregates(connection, revision)
            connection.execute(
                "INSERT INTO projection_events(revision,event_type,run_id,event_json) "
                "VALUES(?,?,?,?)",
                (revision, "carrier_state", run_id, _json({"state": state, "alias": alias})),
            )
            return revision

    def summaries(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE active=1 "
                "ORDER BY created_utc DESC,run_id DESC,workflow_sha256 DESC"
            ).fetchall()
        return [_public_summary(row) for row in rows]

    def detail(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT detail_json,identity_conflict,carrier_state,carrier_alias "
                "FROM runs WHERE active=1 AND run_id=?",
                (run_id,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1 or any(row["identity_conflict"] for row in rows):
            raise ReadModelError("experiment identity_conflict")
        detail = _load_object(rows[0]["detail_json"], "experiment detail")
        detail["storage_state"] = rows[0]["carrier_state"]
        detail["carrier_alias"] = rows[0]["carrier_alias"]
        return detail

    def page(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ReadModelError(f"page limit must be in [1,{_MAX_PAGE_SIZE}]")
        normalized_filters = _normalize_filters(filters or {})
        filter_digest = hashlib.sha256(_json(normalized_filters).encode("utf-8")).hexdigest()
        with self._connect() as connection:
            revision = self._revision(connection)
            secret = bytes.fromhex(self._meta(connection, "cursor_secret"))
            after = _decode_cursor(cursor, secret, filter_digest, revision) if cursor else None
            clauses = ["r.active=1"]
            parameters: list[Any] = []
            _apply_filters(clauses, parameters, normalized_filters)
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM runs r WHERE " + " AND ".join(clauses),
                    parameters,
                ).fetchone()[0]
            )
            if after is not None:
                clauses.append(
                    "(r.created_utc < ? OR (r.created_utc = ? AND r.run_id < ?) OR "
                    "(r.created_utc = ? AND r.run_id = ? AND r.workflow_sha256 < ?))"
                )
                parameters.extend(
                    [after[0], after[0], after[1], after[0], after[1], after[2]]
                )
            parameters.append(limit + 1)
            rows = connection.execute(
                "SELECT r.* FROM runs r WHERE " + " AND ".join(clauses)
                + " ORDER BY r.created_utc DESC,r.run_id DESC,r.workflow_sha256 DESC LIMIT ?",
                parameters,
            ).fetchall()
        has_more = len(rows) > limit
        visible = rows[:limit]
        items = [_public_summary(row) for row in visible]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            next_cursor = _encode_cursor(
                [last["created_utc"], last["run_id"], last["workflow_sha256"]],
                secret,
                filter_digest,
                revision,
            )
        etag_material = {
            "revision": revision,
            "filter_digest": filter_digest,
            "cursor": cursor or "",
            "limit": limit,
        }
        return {
            "schema_version": PUBLIC_PAGE_SCHEMA_VERSION,
            "items": items,
            "page": {
                "limit": limit,
                "total": total,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
            "revision": revision,
            "etag_material": etag_material,
            "etag": hashlib.sha256(_json(etag_material).encode("utf-8")).hexdigest(),
        }

    def aggregate(self) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM aggregate_counters WHERE singleton=1").fetchone()
        return {
            "total": row["total"],
            "eligible": row["eligible"],
            "invalid": row["invalid"],
            "revision": row["revision"],
        }

    def _upsert(
        self,
        connection: sqlite3.Connection,
        projection: ExperimentProjection,
        revision: int,
        *,
        preserve_managed_carrier: bool = False,
    ) -> None:
        summary_json, detail_json = _json(projection.summary), _json(projection.detail)
        created_utc = projection.summary.get("created_utc") or ""
        carrier_state_update = (
            "CASE WHEN runs.carrier_state='hot' THEN excluded.carrier_state "
            "ELSE runs.carrier_state END"
            if preserve_managed_carrier
            else "excluded.carrier_state"
        )
        carrier_alias_update = (
            "CASE WHEN runs.carrier_state='hot' THEN excluded.carrier_alias "
            "ELSE runs.carrier_alias END"
            if preserve_managed_carrier
            else "excluded.carrier_alias"
        )
        connection.execute(
            "INSERT INTO runs(run_id,workflow_sha256,receipt_sha256,created_utc,workflow_id,"
            "verification_status,recommendation_eligible,relative_path,summary_json,detail_json,"
            "carrier_state,carrier_alias,active,updated_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?) "
            "ON CONFLICT(run_id,workflow_sha256,receipt_sha256) DO UPDATE SET "
            "created_utc=excluded.created_utc,workflow_id=excluded.workflow_id,"
            "verification_status=excluded.verification_status,"
            "recommendation_eligible=excluded.recommendation_eligible,"
            "relative_path=excluded.relative_path,summary_json=excluded.summary_json,"
            f"detail_json=excluded.detail_json,carrier_state={carrier_state_update},"
            f"carrier_alias={carrier_alias_update},active=1,updated_revision=excluded.updated_revision",
            (
                projection.run_id,
                projection.workflow_sha256,
                projection.receipt_sha256,
                created_utc,
                projection.summary.get("workflow_id") or "invalid",
                projection.summary.get("verification_status") or "invalid",
                int(projection.summary.get("recommendation_eligible") is True),
                projection.summary.get("relative_path") or "",
                summary_json,
                detail_json,
                projection.carrier_state,
                projection.carrier_alias,
                revision,
            ),
        )
        row = connection.execute(
            "SELECT projection_id FROM runs WHERE run_id=? AND workflow_sha256=? AND receipt_sha256=?",
            projection.identity,
        ).fetchone()
        projection_id = row["projection_id"]
        for table in ("run_targets", "dataset_bindings", "plot_bindings"):
            connection.execute(f"DELETE FROM {table} WHERE projection_id=?", (projection_id,))
        connection.executemany(
            "INSERT INTO run_targets(projection_id,target) VALUES(?,?)",
            [(projection_id, target) for target in sorted(set(projection.targets))],
        )
        connection.executemany(
            "INSERT INTO dataset_bindings(projection_id,binding_name,path,sha256) VALUES(?,?,?,?)",
            [
                (projection_id, str(row.get("name")), row.get("path"), row.get("sha256"))
                for row in projection.dataset_bindings
            ],
        )
        connection.executemany(
            "INSERT INTO plot_bindings(projection_id,plot_id,plot_type) VALUES(?,?,?)",
            [
                (projection_id, str(row.get("plot_id")), row.get("plot_type"))
                for row in projection.plot_bindings
            ],
        )

    def _refresh_conflicts(self, connection: sqlite3.Connection) -> None:
        connection.execute("UPDATE runs SET identity_conflict=0 WHERE active=1")
        connection.execute(
            "UPDATE runs SET identity_conflict=1 WHERE active=1 AND run_id IN ("
            "SELECT run_id FROM runs WHERE active=1 GROUP BY run_id "
            "HAVING COUNT(DISTINCT workflow_sha256 || ':' || receipt_sha256)>1)"
        )

    @staticmethod
    def _stored_material(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        projection_id = row["projection_id"]
        return {
            "summary": row["summary_json"],
            "detail": row["detail_json"],
            "carrier_state": row["carrier_state"],
            "carrier_alias": row["carrier_alias"],
            "active": row["active"],
            "targets": [
                value[0]
                for value in connection.execute(
                    "SELECT target FROM run_targets WHERE projection_id=? ORDER BY target",
                    (projection_id,),
                )
            ],
            "datasets": [
                tuple(value)
                for value in connection.execute(
                    "SELECT binding_name,path,sha256 FROM dataset_bindings "
                    "WHERE projection_id=? ORDER BY binding_name",
                    (projection_id,),
                )
            ],
            "plots": [
                tuple(value)
                for value in connection.execute(
                    "SELECT plot_id,plot_type FROM plot_bindings "
                    "WHERE projection_id=? ORDER BY plot_id",
                    (projection_id,),
                )
            ],
        }

    def _refresh_aggregates(self, connection: sqlite3.Connection, revision: int) -> None:
        row = connection.execute(
            "SELECT COUNT(*) AS total,"
            "SUM(CASE WHEN recommendation_eligible=1 AND identity_conflict=0 THEN 1 ELSE 0 END) eligible,"
            "SUM(CASE WHEN verification_status='invalid' OR identity_conflict=1 THEN 1 ELSE 0 END) invalid "
            "FROM runs WHERE active=1"
        ).fetchone()
        connection.execute(
            "INSERT INTO aggregate_counters VALUES(1,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
            "total=excluded.total,eligible=excluded.eligible,invalid=excluded.invalid,revision=excluded.revision",
            (row["total"] or 0, row["eligible"] or 0, row["invalid"] or 0, revision),
        )

    @staticmethod
    def _meta(connection: sqlite3.Connection, key: str) -> str:
        row = connection.execute("SELECT value FROM read_model_meta WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ReadModelError(f"Web read-model metadata is missing: {key}")
        return row["value"]

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO read_model_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def _revision(self, connection: sqlite3.Connection) -> int:
        return int(self._meta(connection, "revision"))


def _prepare_database_path(value: str | Path) -> Path:
    path = Path(os.path.abspath(os.fspath(value)))
    missing: list[Path] = []
    cursor = path.parent
    while not os.path.lexists(cursor):
        missing.append(cursor)
        parent = cursor.parent
        if parent == cursor:
            raise ReadModelError("Web read-model directory cannot be created")
        cursor = parent
    _assert_safe_directory_chain(cursor)
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        _assert_safe_directory_chain(directory)
    _assert_safe_directory_chain(path.parent)
    if not os.path.lexists(path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
    _assert_database_carriers(path)
    return path


def _assert_safe_directory_chain(path: Path) -> None:
    cursor = path
    anchor = Path(path.anchor)
    while True:
        try:
            info = cursor.lstat()
        except OSError as exc:
            raise ReadModelError("Web read-model directory cannot be inspected") from exc
        if _is_link_or_reparse(cursor, info) or not stat.S_ISDIR(info.st_mode):
            raise ReadModelError("Web read-model directory is linked or unsafe")
        if cursor == anchor:
            return
        cursor = cursor.parent


def _assert_database_carriers(path: Path) -> None:
    _assert_safe_directory_chain(path.parent)
    for carrier in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if not os.path.lexists(carrier):
            continue
        try:
            info = carrier.lstat()
        except FileNotFoundError as exc:
            if carrier != path:
                continue
            raise ReadModelError("Web read-model carrier cannot be inspected") from exc
        except OSError as exc:
            raise ReadModelError("Web read-model carrier cannot be inspected") from exc
        if (
            _is_link_or_reparse(carrier, info)
            or not stat.S_ISREG(info.st_mode)
            or not _database_carrier_link_count_is_safe(carrier, path, info)
        ):
            raise ReadModelError("Web read-model carrier is linked or unsafe")


def _database_carrier_link_count_is_safe(
    carrier: Path, database: Path, info: os.stat_result
) -> bool:
    if info.st_nlink == 1:
        return True
    # A concurrent SQLite close can expose a deletion-pending sidecar with nlink=0.
    # It has no remaining directory link and cannot be an admitted hardlink.
    return carrier != database and info.st_nlink == 0


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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _load_object(raw: str, label: str) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        value = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ReadModelError(f"stored {label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise ReadModelError(f"stored {label} JSON must be an object")
    return value


def _projection_digest(projections: Sequence[ExperimentProjection]) -> str:
    material = [
        {
            "identity": row.identity,
            "summary": row.summary,
            "detail": row.detail,
            "targets": row.targets,
            "datasets": row.dataset_bindings,
            "plots": row.plot_bindings,
            "carrier": [row.carrier_state, row.carrier_alias],
        }
        for row in projections
    ]
    return hashlib.sha256(_json(material).encode("utf-8")).hexdigest()


def _public_summary(row: sqlite3.Row) -> dict[str, Any]:
    summary = _load_object(row["summary_json"], "experiment summary")
    summary["storage_state"] = row["carrier_state"]
    summary["carrier_alias"] = row["carrier_alias"]
    if row["identity_conflict"]:
        summary["verification_status"] = "invalid"
        summary["recommendation_eligible"] = False
        summary["error"] = "identity_conflict"
    return summary


def _normalize_filters(filters: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "q",
        "workflow_id",
        "verification_status",
        "status",
        "recommendation_state",
        "storage_state",
        "target",
    }
    if any(key not in allowed for key in filters):
        raise ReadModelError("unsupported experiment page filter")
    result = {}
    for key, value in filters.items():
        if key == "target":
            values = [value] if isinstance(value, str) else value
            if (
                not isinstance(values, (list, tuple))
                or not values
                or any(not isinstance(item, str) or not item for item in values)
            ):
                raise ReadModelError("target filter must contain non-empty strings")
            value = sorted(set(values))
        elif not isinstance(value, str) or not value.strip():
            raise ReadModelError(f"{key} filter must be a non-empty string")
        else:
            value = value.strip()
        if key == "recommendation_state" and value not in {"data-only", "eligible", "blocked"}:
            raise ReadModelError("recommendation_state filter is invalid")
        result[key] = value
    return dict(sorted(result.items()))


def _apply_filters(clauses: list[str], parameters: list[Any], filters: Mapping[str, Any]) -> None:
    column_filters = {
        "workflow_id": "r.workflow_id",
        "storage_state": "r.carrier_state",
    }
    for key, column in column_filters.items():
        if key in filters:
            clauses.append(f"{column}=?")
            value = filters[key]
            parameters.append(int(value) if isinstance(value, bool) else value)
    if "status" in filters:
        clauses.append("json_extract(r.summary_json,'$.status')=?")
        parameters.append(filters["status"])
    if "verification_status" in filters:
        if filters["verification_status"] == "invalid":
            clauses.append("(r.verification_status='invalid' OR r.identity_conflict=1)")
        else:
            clauses.append("r.verification_status=? AND r.identity_conflict=0")
            parameters.append(filters["verification_status"])
    if "recommendation_state" in filters:
        state = filters["recommendation_state"]
        if state == "eligible":
            clauses.append(
                "r.recommendation_eligible=1 AND r.identity_conflict=0 "
                "AND r.verification_status<>'invalid'"
            )
        elif state == "data-only":
            clauses.append(
                "COALESCE(json_extract(r.summary_json,'$.recommendation_applicable'),0)=0 "
                "AND r.identity_conflict=0 AND r.verification_status<>'invalid'"
            )
        else:
            clauses.append(
                "COALESCE(json_extract(r.summary_json,'$.recommendation_applicable'),0)=1 "
                "AND r.recommendation_eligible=0 AND r.identity_conflict=0 "
                "AND r.verification_status<>'invalid'"
            )
    if "target" in filters:
        values = filters["target"]
        placeholders = ",".join("?" for _value in values)
        clauses.append(
            "EXISTS(SELECT 1 FROM run_targets t WHERE t.projection_id=r.projection_id "
            f"AND t.target IN ({placeholders}))"
        )
        parameters.extend(values)
    if "q" in filters:
        escaped = (
            filters["q"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        query = f"%{escaped}%"
        clauses.append(
            "(r.run_id LIKE ? ESCAPE '\\' OR r.workflow_id LIKE ? ESCAPE '\\' "
            "OR r.summary_json LIKE ? ESCAPE '\\')"
        )
        parameters.extend((query, query, query))


def _encode_cursor(
    key: list[str], secret: bytes, filter_digest: str, revision: int
) -> str:
    raw = _json({"key": key, "filter": filter_digest, "revision": revision}).encode("utf-8")
    signature = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode("ascii")


def _decode_cursor(
    value: str, secret: bytes, filter_digest: str, revision: int
) -> tuple[str, str, str]:
    try:
        padding = "=" * (-len(value) % 4)
        packed = base64.urlsafe_b64decode(value + padding)
        raw, signature = packed[:-32], packed[-32:]
        if len(signature) != 32 or not hmac.compare_digest(
            signature, hmac.new(secret, raw, hashlib.sha256).digest()
        ):
            raise ValueError
        payload = _load_object(raw.decode("utf-8"), "cursor")
        key = payload.get("key")
        if (
            payload.get("filter") != filter_digest
            or payload.get("revision") != revision
            or not isinstance(key, list)
            or len(key) != 3
            or any(not isinstance(item, str) for item in key)
        ):
            raise ValueError
        return key[0], key[1], key[2]
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, ReadModelError) as exc:
        raise ReadModelError("experiment page cursor is invalid or stale") from exc
