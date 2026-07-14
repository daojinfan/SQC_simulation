"""Independent verification for immutable Stage 6 run evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root
from sqvm.runtime.dataset import CLAIM_ENVELOPE, dataset_manifest_sha256, validate_response_dataset
from sqvm.runtime.journal import verify_event_journal
from sqvm.runtime.models import ExperimentRun, RunVerificationReport
from sqvm.runtime.provenance import build_environment_snapshot, validate_locked_environment, verify_source_snapshot
from sqvm.runtime.storage import inventory_tree


MANIFEST_KEYS = {
    "schema_version", "artifact_type", "run_id", "status", "created_utc", "terminal_utc",
    "experiment_id", "backend_id", "output_root", "claim_envelope", "request_sha256",
    "point_table_sha256", "events_sha256", "event_tail_sha256", "snapshots",
    "backend_capabilities", "payload_files", "dataset_summary",
}
REPORT_KEYS = {
    "schema_version", "artifact_type", "run_id", "status", "ok", "manifest_sha256",
    "claim_envelope", "checks", "blocking_reasons",
}
RECEIPT_KEYS = {
    "schema_version", "artifact_type", "run_id", "status", "claim_envelope", "manifest_sha256",
    "verification_report_sha256", "event_tail_sha256", "elapsed_seconds", "published_relative_path",
    "run_lock_sha256", "resource_lock_sha256", "dataset_hashes",
}
SNAPSHOT_FILES = {
    "snapshots/device.yaml",
    "snapshots/calibration.json",
    "snapshots/environment.json",
    "snapshots/source.json",
    "snapshots/run_lock.json",
    "snapshots/resource_lock.json",
}


def load_experiment_run(run_dir: str | Path, repository_root: str | Path | None = None) -> ExperimentRun:
    directory = Path(run_dir).resolve()
    if repository_root is not None:
        directory.relative_to(Path(repository_root).resolve())
    return ExperimentRun(
        directory,
        _load_canonical(directory / "manifest.json"),
        _load_canonical(directory / "verification_report.json"),
        _load_canonical(directory / "receipt.json"),
    )


def verify_experiment_run(run_dir: str | Path, repository_root: str | Path | None = None) -> RunVerificationReport:
    directory = Path(run_dir).resolve()
    checks: list[dict[str, Any]] = []
    blockers: list[str] = []
    run_id = directory.name
    status = "unknown"
    manifest_sha = ""
    receipt_sha = ""
    loaded: ExperimentRun | None = None

    def check(name: str, action: Callable[[], None]) -> None:
        try:
            action()
            checks.append({"name": name, "passed": True, "message": "passed"})
        except Exception as exc:
            message = str(exc)
            checks.append({"name": name, "passed": False, "message": message})
            blockers.append(f"{name}: {message}")

    def load() -> None:
        nonlocal loaded, run_id, status, manifest_sha, receipt_sha
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError("run directory is missing or unsafe")
        loaded = load_experiment_run(directory, repository_root)
        run_id = str(loaded.manifest.get("run_id", directory.name))
        status = str(loaded.manifest.get("status", "unknown"))
        manifest_sha = _raw_sha256(directory / "manifest.json")
        receipt_sha = _raw_sha256(directory / "receipt.json")

    check("canonical_terminal_documents", load)
    if loaded is not None:
        check("manifest_contract_and_inventory", lambda: _verify_manifest(directory, loaded.manifest))
        check("request_point_and_claim_binding", lambda: _verify_request_point_claim(directory, loaded.manifest))
        check("event_chain_binding", lambda: _verify_events(directory, loaded.manifest))
        check("snapshot_and_source_binding", lambda: _verify_snapshots(directory, loaded.manifest, repository_root))
        check("dataset_contract", lambda: _verify_dataset(directory, loaded.manifest))
        check("report_receipt_hash_graph", lambda: _verify_report_receipt(directory, loaded))
    ok = not blockers
    return RunVerificationReport(ok, run_id, status, manifest_sha, receipt_sha, tuple(checks), tuple(blockers))


def _verify_manifest(directory: Path, manifest: Mapping[str, Any]) -> None:
    if set(manifest) != MANIFEST_KEYS or manifest.get("schema_version") != "0.1" or manifest.get("artifact_type") != "stage_06_experiment_run_manifest":
        raise ValueError("manifest schema is invalid")
    if manifest.get("run_id") != directory.name or manifest.get("status") not in {"completed", "failed", "cancelled"}:
        raise ValueError("manifest identity or status is invalid")
    if manifest.get("claim_envelope") != CLAIM_ENVELOPE:
        raise ValueError("manifest claim envelope is invalid")
    if manifest.get("experiment_id") != "platform_deterministic_smoke_v1" or manifest.get("backend_id") != "deterministic_fake_v1":
        raise ValueError("manifest experiment/backend identity is invalid")
    capabilities = manifest.get("backend_capabilities")
    values = ["cooperative_deadline_v1", "platform_test_fixture_v1"]
    expected_capabilities_sha = hashlib.sha256(canonical_json_bytes({"values": values})).hexdigest().upper()
    if capabilities != {"values": values, "sha256": expected_capabilities_sha}:
        raise ValueError("manifest backend capabilities binding is invalid")
    inventory = manifest.get("payload_files")
    if not isinstance(inventory, list) or any(not isinstance(row, dict) or set(row) != {"path", "byte_length", "raw_sha256"} for row in inventory):
        raise ValueError("manifest payload inventory is invalid")
    actual = inventory_tree(directory)
    terminal_paths = {"manifest.json", "verification_report.json", "receipt.json"}
    actual_payload = [row for row in actual if row["path"] not in terminal_paths]
    if inventory != actual_payload:
        raise ValueError("manifest payload inventory does not match run bytes")
    paths = {row["path"] for row in inventory}
    base = {"request.json", "point_table.json", "events.jsonl", *SNAPSHOT_FILES}
    if manifest["status"] == "completed":
        if paths != base | {"data/dataset.json", "data/response.bin"}:
            raise ValueError("completed run payload file set is invalid")
        if {row.name for row in directory.iterdir() if row.is_dir()} != {"snapshots", "data"}:
            raise ValueError("completed run directory set is invalid")
    else:
        if paths != base or (directory / "data").exists():
            raise ValueError("non-completed run payload file set is invalid")
        if {row.name for row in directory.iterdir() if row.is_dir()} != {"snapshots"}:
            raise ValueError("non-completed run directory set is invalid")


def _verify_request_point_claim(directory: Path, manifest: Mapping[str, Any]) -> None:
    request = _load_canonical(directory / "request.json")
    point_table = _load_canonical(directory / "point_table.json")
    request_keys = {
        "schema_version", "experiment_id", "backend_id", "device_snapshot", "calibration_snapshot",
        "parameters", "program", "scan", "execution", "publication", "claim_envelope",
    }
    if set(request) != request_keys or request.get("schema_version") != "0.1":
        raise ValueError("request schema is invalid")
    expected_scan = {
        "axes": [
            {"name": "x", "unit": "dimensionless", "values": [0.0, 0.5]},
            {"name": "y", "unit": "dimensionless", "values": [-1.0, 0.0, 1.0]},
        ],
        "repetitions": 1,
    }
    expected_execution = {
        "seed": 0,
        "max_points": 6,
        "point_budget_seconds": 1.0,
        "run_budget_seconds": 10.0,
        "fail_fast": True,
    }
    fixed = (
        request.get("experiment_id") == "platform_deterministic_smoke_v1"
        and request.get("backend_id") == "deterministic_fake_v1"
        and request.get("device_snapshot") == "configs/devices/2q1c2r.yaml"
        and request.get("calibration_snapshot") == "configs/calibration/platform_uncalibrated_v1.json"
        and request.get("parameters") == {}
        and request.get("program") is None
        and request.get("scan") == expected_scan
        and request.get("execution") == expected_execution
        and request.get("publication") == {"allow_existing_target": False}
        and request.get("claim_envelope") == CLAIM_ENVELOPE
    )
    if not fixed:
        raise ValueError("request frozen MVP contract is invalid")
    root = find_repository_root(directory)
    from sqvm.runtime.config import load_experiment_request
    from sqvm.runtime.runner import _request_payload
    from sqvm.runtime.scan import point_table_payload

    frozen_request = load_experiment_request(root / "configs/experiments/platform_deterministic_smoke_v1.yaml", root)
    if request != _request_payload(frozen_request) or point_table != point_table_payload(frozen_request):
        raise ValueError("request or point table does not match recomputed frozen configuration")
    if manifest["request_sha256"] != _raw_sha256(directory / "request.json"):
        raise ValueError("request hash binding is invalid")
    if manifest["point_table_sha256"] != _raw_sha256(directory / "point_table.json"):
        raise ValueError("point table hash binding is invalid")
    points = point_table.get("points")
    if set(point_table) != {"schema_version", "experiment_id", "points"} or point_table.get("schema_version") != "0.1" or point_table.get("experiment_id") != request["experiment_id"]:
        raise ValueError("point table schema or experiment identity is invalid")
    if not isinstance(points, list) or len(points) != 6:
        raise ValueError("point table is empty or invalid")
    expected_coordinates = [
        (0.0, -1.0), (0.0, 0.0), (0.0, 1.0),
        (0.5, -1.0), (0.5, 0.0), (0.5, 1.0),
    ]
    for index, point in enumerate(points):
        if not isinstance(point, dict) or set(point) != {"schema_version", "experiment_id", "point_index", "repetition", "coordinates", "seed", "point_id"} or point.get("point_index") != index:
            raise ValueError("point table index is invalid")
        x, y = expected_coordinates[index]
        expected_coordinate_payload = [
            {"axis": "x", "value": x, "unit": "dimensionless"},
            {"axis": "y", "value": y, "unit": "dimensionless"},
        ]
        if point.get("schema_version") != "0.1" or point.get("experiment_id") != request["experiment_id"] or point.get("repetition") != 0 or point.get("coordinates") != expected_coordinate_payload:
            raise ValueError("point table ordering or coordinates are invalid")
        point_id = point.get("point_id")
        payload = {key: value for key, value in point.items() if key != "point_id"}
        from sqvm.runtime.scan import canonical_json_bytes as point_bytes

        if point_id != hashlib.sha256(point_bytes(payload)).hexdigest().upper():
            raise ValueError("point_id does not match canonical point payload")


def _verify_events(directory: Path, manifest: Mapping[str, Any]) -> None:
    summary = verify_event_journal(directory / "events.jsonl", str(manifest["run_id"]))
    if summary.raw_sha256 != manifest["events_sha256"] or summary.tail_event_sha256 != manifest["event_tail_sha256"]:
        raise ValueError("event journal manifest binding is invalid")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    if not events or events[0]["event_type"] != "run_reserved":
        raise ValueError("event journal must begin with run_reserved")
    terminal = {"completed": "run_completed", "failed": "run_failed", "cancelled": "run_cancelled"}[manifest["status"]]
    if events[-1]["event_type"] != terminal or any(row["event_type"] in {"run_completed", "run_failed", "run_cancelled"} for row in events[:-1]):
        raise ValueError("event journal terminal semantics are invalid")
    point_table = _load_canonical(directory / "point_table.json")["points"]
    active: tuple[int, str] | None = None
    completed: set[int] = set()
    phase = "reserved"
    for row in events[1:-1]:
        event_type = row["event_type"]
        payload = row["payload"]
        if event_type == "run_prepared":
            if phase != "reserved":
                raise ValueError("run_prepared occurs outside reserved")
            phase = "prepared"
        elif event_type == "run_started":
            if phase != "prepared" or payload["point_count"] != len(point_table):
                raise ValueError("run_started occurs outside prepared or has wrong point count")
            phase = "running"
        elif event_type == "run_finalizing":
            if phase != "running" or active is not None or payload["completed_point_count"] != len(completed):
                raise ValueError("run_finalizing semantics are invalid")
            phase = "finalizing"
        elif event_type == "cancel_requested":
            if phase not in {"prepared", "running"}:
                raise ValueError("cancel_requested occurs outside cancellable state")
        elif event_type == "point_started":
            if phase != "running":
                raise ValueError("point_started occurs outside running")
            if active is not None:
                raise ValueError("event journal starts a point while another is active")
            index = payload["point_index"]
            if not 0 <= index < len(point_table) or payload["point_id"] != point_table[index]["point_id"] or index in completed:
                raise ValueError("point_started does not match point table")
            active = (index, payload["point_id"])
        elif event_type in {"point_completed", "point_failed"}:
            if phase != "running":
                raise ValueError("point terminal event occurs outside running")
            if active != (payload["point_index"], payload["point_id"]):
                raise ValueError("point terminal event does not match active point")
            if event_type == "point_completed":
                completed.add(payload["point_index"])
            active = None
        else:
            raise ValueError(f"unexpected nonterminal event: {event_type}")
    if active is not None:
        raise ValueError("event journal has an unterminated point")
    terminal_count = events[-1]["payload"].get("completed_point_count")
    if terminal_count != len(completed):
        raise ValueError("terminal completed point count is invalid")
    if manifest["status"] == "completed":
        if phase != "finalizing":
            raise ValueError("run_completed occurs outside finalizing")
        expected_types = ["run_reserved", "run_prepared", "run_started"]
        for _point in point_table:
            expected_types.extend(["point_started", "point_completed"])
        expected_types.extend(["run_finalizing", "run_completed"])
        if [row["event_type"] for row in events] != expected_types or completed != set(range(len(point_table))):
            raise ValueError("completed event sequence is invalid")
    if manifest["status"] == "cancelled" and not any(row["event_type"] == "cancel_requested" for row in events):
        raise ValueError("cancelled run lacks cancel_requested evidence")
    if manifest["status"] == "cancelled" and phase not in {"prepared", "running"}:
        raise ValueError("run_cancelled occurs outside a cancellable state")


def _verify_snapshots(directory: Path, manifest: Mapping[str, Any], repository_root: str | Path | None) -> None:
    snapshots = manifest.get("snapshots")
    if not isinstance(snapshots, dict) or set(snapshots) != {"device", "calibration", "environment", "source", "run_lock", "resource_lock"}:
        raise ValueError("manifest snapshots mapping is invalid")
    names = {
        "device": "device.yaml",
        "calibration": "calibration.json",
        "environment": "environment.json",
        "source": "source.json",
        "run_lock": "run_lock.json",
        "resource_lock": "resource_lock.json",
    }
    for key, name in names.items():
        if snapshots[key] != _raw_sha256(directory / "snapshots" / name):
            raise ValueError(f"snapshot hash is invalid: {key}")
    source_payload = _load_canonical(directory / "snapshots/source.json")
    environment_payload = _load_canonical(directory / "snapshots/environment.json")
    root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(directory)
    verify_source_snapshot(source_payload, root)
    current_environment = build_environment_snapshot()
    validate_locked_environment(root, current_environment)
    if environment_payload != current_environment:
        raise ValueError("environment snapshot does not match current locked environment")


def _verify_dataset(directory: Path, manifest: Mapping[str, Any]) -> None:
    if manifest["status"] != "completed":
        if manifest.get("dataset_summary") is not None:
            raise ValueError("non-completed run cannot have a dataset summary")
        return
    point_table = _load_canonical(directory / "point_table.json")
    points = point_table["points"]
    payload, values = validate_response_dataset(
        directory / "data",
        expected_point_count=len(points),
        point_table_sha256=manifest["point_table_sha256"],
        require_frozen_fixture=True,
    )
    expected = {
        "dataset_manifest_sha256": dataset_manifest_sha256(directory / "data"),
        "response_sha256": payload["variables"]["response"]["raw_sha256"],
        "point_count": len(values),
    }
    if manifest.get("dataset_summary") != expected:
        raise ValueError("dataset summary is invalid")


def _verify_report_receipt(directory: Path, run: ExperimentRun) -> None:
    report = run.report
    receipt = run.receipt
    manifest = run.manifest
    if set(report) != REPORT_KEYS or report.get("artifact_type") != "stage_06_experiment_run_verification_report":
        raise ValueError("embedded verification report schema is invalid")
    if set(receipt) != RECEIPT_KEYS or receipt.get("artifact_type") != "stage_06_experiment_run_receipt":
        raise ValueError("receipt schema is invalid")
    manifest_sha = _raw_sha256(directory / "manifest.json")
    report_sha = _raw_sha256(directory / "verification_report.json")
    common = (report.get("run_id") == manifest["run_id"] == receipt.get("run_id") and report.get("status") == manifest["status"] == receipt.get("status"))
    if not common or report.get("manifest_sha256") != manifest_sha or receipt.get("manifest_sha256") != manifest_sha:
        raise ValueError("report/receipt manifest binding is invalid")
    if receipt.get("verification_report_sha256") != report_sha or receipt.get("event_tail_sha256") != manifest["event_tail_sha256"]:
        raise ValueError("receipt report/event binding is invalid")
    if report.get("claim_envelope") != CLAIM_ENVELOPE or receipt.get("claim_envelope") != CLAIM_ENVELOPE:
        raise ValueError("report/receipt claim envelope is invalid")
    if receipt.get("run_lock_sha256") != manifest["snapshots"]["run_lock"] or receipt.get("resource_lock_sha256") != manifest["snapshots"]["resource_lock"]:
        raise ValueError("receipt lock binding is invalid")
    expected_hashes = {}
    if manifest["status"] == "completed":
        expected_hashes = {
            "dataset.json": _raw_sha256(directory / "data/dataset.json"),
            "response.bin": _raw_sha256(directory / "data/response.bin"),
        }
    if receipt.get("dataset_hashes") != expected_hashes:
        raise ValueError("receipt dataset hashes are invalid")
    if report.get("ok") is not True or report.get("blocking_reasons") != []:
        raise ValueError("embedded verification report does not claim successful construction checks")
    elapsed = receipt.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, int | float) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise ValueError("receipt elapsed_seconds is invalid")
    root = find_repository_root(directory)
    output_root = directory.parent.parent.relative_to(root).as_posix()
    if manifest.get("output_root") != output_root or receipt.get("published_relative_path") != f"{output_root}/runs/{manifest['run_id']}":
        raise ValueError("published path binding is invalid")


def _load_canonical(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError(f"{path.name} is not canonical mapping JSON")
    return payload


def _raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()
