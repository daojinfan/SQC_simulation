"""Standard-library HTTP server for the read-only calibration console."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from sqvm.web.index import CalibrationWebIndex, WebArtifactError
from sqvm.web.configuration import (
    ConfigurationManagementError,
    PlatformConfigurationStore,
)


_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


class CalibrationWebServer(ThreadingHTTPServer):
    index: CalibrationWebIndex
    store: PlatformConfigurationStore


class CalibrationWebHandler(BaseHTTPRequestHandler):
    server: CalibrationWebServer

    def do_GET(self) -> None:
        try:
            self._get()
        except (WebArtifactError, ConfigurationManagementError) as exc:
            payload = {"error": str(exc), "status": exc.status}
            if isinstance(exc, ConfigurationManagementError):
                payload["field_errors"] = exc.field_errors
            self._json(exc.status, payload)
        except Exception:
            self._json(500, {"error": "internal server error", "status": 500})

    def do_HEAD(self) -> None:
        try:
            path = urlsplit(self.path).path
            if path in _STATIC_FILES:
                file_name, content_type = _STATIC_FILES[path]
                self._bytes(200, (_STATIC_ROOT / file_name).read_bytes(), content_type, head=True)
                return
            self._json(404, {"error": "not found", "status": 404}, head=True)
        except Exception:
            self._json(500, {"error": "internal server error", "status": 500}, head=True)

    def do_POST(self) -> None:
        self._mutate("POST")

    def do_PUT(self) -> None:
        self._mutate("PUT")

    def do_PATCH(self) -> None:
        self._method_not_allowed()

    def do_DELETE(self) -> None:
        self._mutate("DELETE")

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _get(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in _STATIC_FILES:
            file_name, content_type = _STATIC_FILES[path]
            self._bytes(200, (_STATIC_ROOT / file_name).read_bytes(), content_type)
            return
        if path == "/api/v1/health":
            self._json(200, self.server.index.health())
            return
        if path == "/api/v1/overview":
            self._json(200, self.server.index.overview())
            return
        if path == "/api/v1/configurations":
            self._json(200, {"items": self.server.index.configurations()})
            return
        if path == "/api/v1/configuration-management":
            self._json(200, self.server.store.summary())
            return
        if path.startswith("/api/v1/drafts/"):
            draft_id = path.removeprefix("/api/v1/drafts/")
            self._json(200, self.server.store.draft(draft_id))
            return
        if path.startswith("/api/v1/platform-snapshots/"):
            snapshot_id = path.removeprefix("/api/v1/platform-snapshots/")
            self._json(200, self.server.store.snapshot(snapshot_id))
            return
        if path.startswith("/api/v1/configurations/"):
            identifier = path.removeprefix("/api/v1/configurations/")
            detail = self.server.index.configuration(identifier)
            raw = detail["raw"]
            if raw.get("artifact_type") != "platform_configuration_snapshot":
                bootstrap = self.server.store.bootstrap_configuration(raw)
                detail = {
                    **detail,
                    "control_values": bootstrap["editable"]["control_values"],
                    "values": bootstrap["editable"]["calibration_values"],
                    "device_ref": bootstrap["device_ref"],
                    "authority_refs": bootstrap["authority_refs"],
                    "configuration_source": "control_baseline_plus_calibration",
                }
            self._json(200, detail)
            return
        if path == "/api/v1/experiments":
            self._json(200, {"items": self.server.index.experiments()})
            return
        if path.startswith("/api/v1/experiments/"):
            tail = path.removeprefix("/api/v1/experiments/")
            parts = tail.split("/")
            if len(parts) == 1 and parts[0]:
                self._json(200, self.server.index.experiment(parts[0]))
                return
            if len(parts) == 3 and parts[1] == "asset":
                asset, content_type = self.server.index.experiment_asset(parts[0], parts[2])
                self._bytes(200, asset.read_bytes(), content_type)
                return
        raise WebArtifactError("not found", status=404)

    def _mutate(self, method: str) -> None:
        try:
            self._mutation_route(method)
        except (WebArtifactError, ConfigurationManagementError) as exc:
            payload = {"error": str(exc), "status": exc.status}
            if isinstance(exc, ConfigurationManagementError):
                payload["field_errors"] = exc.field_errors
            self._json(exc.status, payload)
        except Exception:
            self._json(500, {"error": "internal server error", "status": 500})

    def _mutation_route(self, method: str) -> None:
        path = unquote(urlsplit(self.path).path)
        payload = self._request_json() if method in {"POST", "PUT"} else {}
        if method == "POST" and path == "/api/v1/drafts":
            base = self.server.index.configuration(payload.get("base_configuration_id"))
            raw = base["raw"]
            if raw.get("artifact_type") != "platform_configuration_snapshot":
                raw = self.server.store.bootstrap_configuration(raw)
            result = self.server.store.create_draft(
                raw,
                actor_id=payload.get("actor_id"),
                name=payload.get("name"),
                note=payload.get("note", ""),
            )
            self._json(201, result)
            return
        if method == "POST" and path.startswith("/api/v1/experiments/") and path.endswith("/draft"):
            run_id = path.removeprefix("/api/v1/experiments/").removesuffix("/draft")
            detail = self.server.index.experiment(run_id)
            if detail.get("renderer") != "qubit_spectroscopy":
                raise ConfigurationManagementError("experiment has no configuration draft adapter")
            targets = payload.get("targets")
            if not isinstance(targets, list) or not targets:
                raise ConfigurationManagementError("candidate targets are required")
            candidates = [
                row for row in detail["candidates"] if row.get("target") in targets
            ]
            if len(candidates) != len(set(targets)):
                raise ConfigurationManagementError("candidate targets are invalid")
            parent = detail.get("parent_calibration")
            relative = parent.get("path") if isinstance(parent, dict) else None
            matches = [
                row
                for row in self.server.index.configurations()
                if row["relative_path"] == relative
            ]
            if len(matches) != 1:
                raise ConfigurationManagementError("experiment parent configuration is unavailable")
            base = self.server.index.configuration(matches[0]["configuration_id"])["raw"]
            if base.get("artifact_type") != "platform_configuration_snapshot":
                base = self.server.store.bootstrap_configuration(base)
            draft = self.server.store.create_draft(
                base,
                actor_id=payload.get("actor_id"),
                name=payload.get("name"),
                note=payload.get("note", ""),
            )
            result = self.server.store.apply_candidates_to_draft(
                draft["draft_id"],
                actor_id=payload.get("actor_id"),
                experiment_run_id=run_id,
                recommendation_id=detail.get("recommendation_id") or run_id,
                candidates=candidates,
            )
            self._json(201, result)
            return
        if path.startswith("/api/v1/drafts/"):
            tail = path.removeprefix("/api/v1/drafts/").split("/")
            draft_id = tail[0]
            if method == "PUT" and len(tail) == 1:
                result = self.server.store.update_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                    name=payload.get("name"),
                    note=payload.get("note", ""),
                    editable=payload.get("editable"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["validate"]:
                result = self.server.store.validate_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["initialize-calibration"]:
                result = self.server.store.initialize_calibration_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["publish"]:
                result = self.server.store.publish_draft(
                    draft_id,
                    actor_id=payload.get("actor_id"),
                    expected_content_sha256=payload.get("expected_content_sha256"),
                    name=payload.get("name"),
                    reason=payload.get("reason"),
                    keep=payload.get("keep") is True,
                )
                self._json(201, result)
                return
            if method == "DELETE" and len(tail) == 1:
                actor_id = self.headers.get("X-SQVM-Actor")
                self.server.store.delete_draft(draft_id, actor_id=actor_id)
                self._json(200, {"deleted": True, "draft_id": draft_id})
                return
        if path.startswith("/api/v1/platform-snapshots/"):
            tail = path.removeprefix("/api/v1/platform-snapshots/").split("/")
            snapshot_id = tail[0]
            if method == "POST" and tail[1:] == ["activate"]:
                result = self.server.store.set_active(
                    snapshot_id,
                    actor_id=payload.get("actor_id"),
                    confirmation_phrase=payload.get("confirmation_phrase"),
                )
                self._json(200, result)
                return
            if method == "POST" and tail[1:] == ["keep"]:
                result = self.server.store.set_keep(
                    snapshot_id,
                    actor_id=payload.get("actor_id"),
                    keep=payload.get("keep") is True,
                )
                self._json(200, result)
                return
            if method == "DELETE" and len(tail) == 1:
                actor_id = self.headers.get("X-SQVM-Actor")
                self.server.store.delete_snapshot(snapshot_id, actor_id=actor_id)
                self._json(200, {"deleted": True, "snapshot_id": snapshot_id})
                return
        self._method_not_allowed()

    def _request_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise ConfigurationManagementError("request Content-Type must be application/json", status=415)
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ConfigurationManagementError("request Content-Length is invalid", status=400) from exc
        if not 0 < length <= 2_000_000:
            raise ConfigurationManagementError("request body size is invalid", status=413)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigurationManagementError("request JSON is invalid", status=400) from exc
        if not isinstance(payload, dict):
            raise ConfigurationManagementError("request JSON must be an object", status=400)
        return payload

    def _method_not_allowed(self) -> None:
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", "GET, HEAD")
        self._common_headers("application/json; charset=utf-8", 0)
        self.end_headers()

    def _json(
        self,
        status: int,
        payload: Any,
        *,
        head: bool = False,
    ) -> None:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        self._bytes(status, raw, "application/json; charset=utf-8", head=head)

    def _bytes(
        self,
        status: int,
        raw: bytes,
        content_type: str,
        *,
        head: bool = False,
    ) -> None:
        self.send_response(status)
        self._common_headers(content_type, len(raw))
        self.end_headers()
        if not head:
            self.wfile.write(raw)

    def _common_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'",
        )


def create_calibration_web_server(
    repository_root: str | Path,
    *,
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> CalibrationWebServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("bounded calibration Web server only binds to localhost")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("port must be in [0, 65535]")
    server = CalibrationWebServer((host, port), CalibrationWebHandler)
    server.index = CalibrationWebIndex(repository_root, output_root)
    server.store = PlatformConfigurationStore(repository_root, configuration_storage_root)
    return server


def serve_calibration_web(
    repository_root: str | Path,
    *,
    output_root: str | Path | None = None,
    configuration_storage_root: str | Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    server = create_calibration_web_server(
        repository_root,
        output_root=output_root,
        configuration_storage_root=configuration_storage_root,
        host=host,
        port=port,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
