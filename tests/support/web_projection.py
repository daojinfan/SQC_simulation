"""Shared web projection fixtures and HTTP helpers for integration tests."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.runtime.publication import publish_calibration_directory
from sqvm.web.server import create_calibration_web_server


def projection(index: int, *, run_id: str | None = None, workflow_sha256: str | None = None, target: str = "Q1", eligible: bool = False, applicable: bool = True, state: str = "hot"):
    from sqvm.web.read_model import ExperimentProjection
    run_id = run_id or f"run-{index:04d}"
    summary = {"run_id": run_id, "workflow_id": "qubit_spectroscopy_scan_v1", "experiment_kind": "Qubit spectroscopy", "status": "completed", "created_utc": f"2026-07-{(index % 28) + 1:02d}T{index % 24:02d}:00:00Z", "data_origin": "synthetic-test", "verification_status": "verified", "targets": [target], "execution_mode": "simulation", "recommendation_applicable": applicable, "recommendation_eligible": eligible, "parent_calibration": None, "gate_summary": {"passed": 0, "failed": 0, "total": 0}, "candidate_summary": [], "relative_path": f"output/experiments/{run_id}", "error": None}
    detail = {**summary, "renderer": "test", "datasets": {"scan": {"points": []}}, "plot_specs": [{"plot_id": "spectrum", "plot_type": "line"}]}
    return ExperimentProjection(run_id=run_id, workflow_sha256=workflow_sha256 or f"W{index:063d}", receipt_sha256=f"R{index:063d}", summary=summary, detail=detail, targets=(target,), dataset_bindings=({"name": "scan", "path": "dataset.json", "sha256": "D" * 64},), plot_bindings=({"plot_id": "spectrum", "plot_type": "line"},), carrier_state=state, carrier_alias=summary["relative_path"])


def start_server(root: Path, base: Path):
    output = base / "output"; output.mkdir(exist_ok=True)
    server = create_calibration_web_server(root, output_root=output, configuration_storage_root=base / "configuration", experiment_hot_root=base / "hot", experiment_storage_root=base / "storage", experiment_archive_root=base / "storage" / "archives", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_address[1]}"


def close_server(server, thread: threading.Thread) -> None:
    server.shutdown(); server.server_close(); thread.join(timeout=5.0)
    assert not thread.is_alive()


def _line_plot(point_count: int, *, plot_id: str = "signal") -> dict[str, Any]:
    return {"schema_version": "1.0", "plot_id": plot_id, "plot_type": "line", "title": "Projected signal", "objects": [{"id": "Q1", "label": "Q1", "default_visible": True}], "metrics": [{"id": "P1", "label": "P1", "default_visible": True}], "axes": {"x": {"label": "Frequency", "unit": "GHz"}, "y": {"label": "Population", "unit": ""}}, "series": [{"id": "Q1:P1", "object_id": "Q1", "metric_id": "P1", "points": [{"id": f"point-{index}", "x": float(index), "y": float(math.sin(index / 17.0)), "metadata": {"source_index": index}} for index in range(point_count)]}]}


def publish_generic(base: Path, run_id: str, *, created_utc: str, point_count: int = 8, collection: str = "published") -> Path:
    parent = base / collection; parent.mkdir(exist_ok=True); staging = parent / f".{run_id}.staging"; target = parent / run_id; staging.mkdir()
    workflow = {"schema_version": "0.1", "artifact_type": "generic_visualization_run", "artifact_version": "0.1", "workflow_id": "generic_visualization_v1", "run_id": run_id, "status": "completed", "created_utc": created_utc, "request": {"targets": ["Q1"], "execution_mode": "simulation"}, "plot_specs": [_line_plot(point_count)]}
    workflow_raw = canonical_json_bytes(workflow)
    receipt = {"schema_version": "0.1", "artifact_type": "generic_visualization_receipt", "artifact_version": "0.1", "run_id": run_id, "status": "completed", "workflow_sha256": hashlib.sha256(workflow_raw).hexdigest().upper()}
    (staging / "workflow.json").write_bytes(workflow_raw); (staging / "receipt.json").write_bytes(canonical_json_bytes(receipt))
    evidence = staging / "execution"; evidence.mkdir(); (evidence / "large-evidence.bin").write_bytes(b"must-not-be-read-by-get")
    publish_calibration_directory(staging, target)
    return target


def request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[int, Any, Any]:
    raw = None if payload is None else json.dumps(payload).encode("utf-8"); request_headers = dict(headers or {})
    if raw is not None: request_headers["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, method=method, data=raw, headers=request_headers), timeout=10.0) as response:
            body = response.read(); return response.status, response.headers, json.loads(body.decode("utf-8")) if body else None
    except HTTPError as exc:
        if exc.code == 304: return exc.code, exc.headers, None
        raise AssertionError(f"HTTP {exc.code} from {url}: {exc.read().decode('utf-8', errors='replace')}") from exc


def eventually(probe: Callable[[], Any], predicate: Callable[[Any], bool], *, timeout_s: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout_s; last: Any = None
    while time.monotonic() < deadline:
        last = probe()
        if predicate(last): return last
        time.sleep(0.02)
    raise AssertionError(f"eventual condition was not reached; last value: {last!r}")


def experiment_page(base_url: str, query: str = "limit=200") -> dict[str, Any]:
    return request(f"{base_url}/api/v1/experiments?{query}")[2]
