"""Independent replay verifier for Runtime 0.2 compiler evidence."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes, find_repository_root
from sqvm.runtime.journal import verify_event_journal
from sqvm.runtime.models import RunVerificationReport
from sqvm.runtime.provenance import build_environment_snapshot, validate_locked_environment
from sqvm.runtime.recovery import _verify_lock_payload
from sqvm.runtime.storage import inventory_tree, inventory_tree_no_follow
from sqvm.runtime.storage import resource_key
from sqvm.runtime_v02.core import (
    BACKEND_ID,
    CLAIM_ENVELOPE,
    EXPERIMENT_ID,
    REQUEST_PATH,
    canonical_request_payload_v02,
    compile_point_v02,
    expand_scan_v02,
    load_experiment_request_v02,
    point_table_payload_v02,
    safe_directory_no_follow,
    validate_dataset_v02,
)
from sqvm.runtime_v02.runner import CAPABILITIES
from sqvm.runtime_v02.provenance import verify_source_snapshot_v02


def verify_experiment_run_v02(
    run_dir: str | Path,
    repository_root: str | Path | None = None,
) -> RunVerificationReport:
    lexical_directory = Path(run_dir).absolute()
    run_id = lexical_directory.name
    manifest_sha = ""
    receipt_sha = ""
    try:
        if lexical_directory.is_symlink():
            raise ValueError("Runtime 0.2 run directory is linked")
        root = Path(repository_root).resolve() if repository_root is not None else find_repository_root(lexical_directory)
        directory = safe_directory_no_follow(lexical_directory, root, "Runtime 0.2 run directory")
        no_follow_inventory = inventory_tree_no_follow(directory)
        if any(row.get("entry_type") in {"link", "other"} for row in no_follow_inventory):
            raise ValueError("Runtime 0.2 evidence tree contains a linked or special entry")
        manifest_sha = _safe_raw_sha(directory / "manifest.json")
        receipt_sha = _safe_raw_sha(directory / "receipt.json")
        request = load_experiment_request_v02(root / REQUEST_PATH, root)
        expected_request = canonical_request_payload_v02(request)
        expected_points = point_table_payload_v02(request)
        request_payload = _load_canonical(directory / "request.json")
        point_payload = _load_canonical(directory / "point_table.json")
        if request_payload != expected_request or point_payload != expected_points:
            raise ValueError("Runtime 0.2 request or point table replay mismatch")

        manifest = _load_canonical(directory / "manifest.json")
        report = _load_canonical(directory / "verification_report.json")
        receipt = _load_canonical(directory / "receipt.json")
        status = _verify_terminal_schemas(directory, manifest, report, receipt)
        output_relative = directory.parent.parent.relative_to(root).as_posix()
        if manifest["output_root"] != output_relative or receipt["published_relative_path"] != f"{output_relative}/runs/{run_id}":
            raise ValueError("Runtime 0.2 publication path binding is invalid")
        request_sha = _raw_sha(directory / "request.json")
        point_sha = _raw_sha(directory / "point_table.json")
        if manifest["request_sha256"] != request_sha or manifest["point_table_sha256"] != point_sha:
            raise ValueError("Runtime 0.2 manifest request binding is invalid")
        if point_payload["request_sha256"] != request_sha:
            raise ValueError("Runtime 0.2 point table request binding is invalid")

        _verify_snapshots(directory, root, request, manifest)
        _verify_program(directory, request)
        journal = verify_event_journal(directory / "events.jsonl", run_id)
        if manifest["events_sha256"] != journal.raw_sha256 or manifest["event_tail_sha256"] != journal.tail_event_sha256:
            raise ValueError("Runtime 0.2 event journal binding is invalid")
        if receipt["event_tail_sha256"] != journal.tail_event_sha256:
            raise ValueError("Runtime 0.2 receipt event binding is invalid")

        expected_payload_files = [
            row for row in inventory_tree(directory)
            if row["path"] not in {"manifest.json", "verification_report.json", "receipt.json"}
        ]
        if manifest["payload_files"] != expected_payload_files:
            raise ValueError("Runtime 0.2 payload inventory is invalid")
        if manifest["backend_capabilities"] != {**CAPABILITIES, "sha256": _sha_payload(CAPABILITIES)}:
            raise ValueError("Runtime 0.2 compiler capability binding is invalid")

        if status == "completed":
            results = _verify_points(directory, request)
            _verify_completed_event_semantics(directory, request)
            dataset = validate_dataset_v02(directory / "data", len(results), point_sha)
            expected_summary = {
                "dataset_manifest_sha256": _raw_sha(directory / "data/dataset.json"),
                "point_count": len(results),
                "variable_hashes": {name: value["raw_sha256"] for name, value in dataset["variables"].items()},
            }
            if manifest["dataset_summary"] != expected_summary:
                raise ValueError("Runtime 0.2 dataset summary is invalid")
            expected_dataset_hashes = {
                row.name: _raw_sha(row) for row in sorted((directory / "data").iterdir(), key=lambda item: item.name)
            }
            if receipt["dataset_hashes"] != expected_dataset_hashes:
                raise ValueError("Runtime 0.2 receipt dataset hashes are invalid")
        else:
            if (directory / "points").exists() or (directory / "data").exists():
                raise ValueError("Runtime 0.2 non-completed run retains point or dataset evidence")
            if manifest["dataset_summary"] is not None or receipt["dataset_hashes"] != {}:
                raise ValueError("Runtime 0.2 non-completed dataset binding is invalid")
            _verify_noncompleted_event_semantics(directory, request, status)

        return RunVerificationReport(
            True, run_id, status, _raw_sha(directory / "manifest.json"), _raw_sha(directory / "receipt.json"),
            tuple(report["checks"]), (),
        )
    except Exception as exc:
        return RunVerificationReport(False, run_id, "invalid", manifest_sha, receipt_sha, (), (str(exc),))


def verify_interrupted_prefix_v02(staging: Path, run_id: str, run_lock: Path, resource_lock_path: Path) -> None:
    """Validate a maximal Runtime 0.2 staging prefix without resuming it."""

    lexical_staging = Path(staging).absolute()
    if lexical_staging.is_symlink():
        raise ValueError("Runtime 0.2 interrupted staging root is linked")
    root = find_repository_root(lexical_staging)
    staging = safe_directory_no_follow(lexical_staging, root, "Runtime 0.2 interrupted staging")
    no_follow_inventory = inventory_tree_no_follow(staging)
    if any(row.get("entry_type") in {"link", "other"} for row in no_follow_inventory):
        raise ValueError("Runtime 0.2 interrupted tree contains a linked or special entry")
    request = load_experiment_request_v02(root / REQUEST_PATH, root)
    if _load_canonical(staging / "request.json") != canonical_request_payload_v02(request):
        raise ValueError("Runtime 0.2 interrupted request mismatch")
    if _load_canonical(staging / "point_table.json") != point_table_payload_v02(request):
        raise ValueError("Runtime 0.2 interrupted point table mismatch")
    _verify_program(staging, request)
    snapshots = staging / "snapshots"
    required_snapshots = {
        "device.yaml", "calibration.json", "environment.json", "source.json", "compiler_authority.json",
        "run_lock.json", "resource_lock.json",
    }
    if {row.name for row in snapshots.iterdir()} != required_snapshots:
        raise ValueError("Runtime 0.2 interrupted snapshot set is invalid")
    if (snapshots / "device.yaml").read_bytes() != request.device_snapshot.read_bytes() or (snapshots / "calibration.json").read_bytes() != request.calibration_snapshot.read_bytes():
        raise ValueError("Runtime 0.2 interrupted frozen snapshot mismatch")
    if _load_canonical(snapshots / "compiler_authority.json") != _plain(request.authority):
        raise ValueError("Runtime 0.2 interrupted compiler authority mismatch")
    environment = _load_canonical(snapshots / "environment.json")
    validate_locked_environment(root, build_environment_snapshot())
    if environment != build_environment_snapshot():
        raise ValueError("Runtime 0.2 interrupted environment mismatch")
    verify_source_snapshot_v02(_load_canonical(snapshots / "source.json"), root)
    if (snapshots / "run_lock.json").read_bytes() != run_lock.read_bytes() or (snapshots / "resource_lock.json").read_bytes() != resource_lock_path.read_bytes():
        raise ValueError("Runtime 0.2 interrupted live lock mismatch")
    run_payload = _load_canonical(run_lock)
    resource_payload = _load_canonical(resource_lock_path)
    if run_payload != resource_payload or run_payload.get("run_id") != run_id:
        raise ValueError("Runtime 0.2 interrupted lock identity mismatch")
    if run_payload.get("request_sha256") != _raw_sha(staging / "request.json"):
        raise ValueError("Runtime 0.2 interrupted request lock binding mismatch")
    if run_payload.get("environment_fingerprint") != _raw_sha(snapshots / "environment.json"):
        raise ValueError("Runtime 0.2 interrupted environment lock binding mismatch")
    if run_payload.get("resource_key") != resource_key(BACKEND_ID, _raw_sha(snapshots / "device.yaml")):
        raise ValueError("Runtime 0.2 interrupted resource key mismatch")
    verify_event_journal(staging / "events.jsonl", run_id)
    allowed_root = {
        "request.json", "point_table.json", "snapshots", "program", "events.jsonl", "points", "data",
        "manifest.json", "verification_report.json", "receipt.json",
    }
    if not {row.name for row in staging.iterdir()}.issubset(allowed_root):
        raise ValueError("Runtime 0.2 interrupted root contains an unknown entry")

    points = expand_scan_v02(request)
    by_id = {point.point_id: point for point in points}
    points_root = staging / "points"
    if points_root.exists():
        if points_root.is_symlink() or not points_root.is_dir():
            raise ValueError("Runtime 0.2 interrupted points root is unsafe")
        for point_dir in points_root.iterdir():
            point = by_id.get(point_dir.name)
            if point is None or point_dir.is_symlink() or not point_dir.is_dir():
                raise ValueError("Runtime 0.2 interrupted point directory is invalid")
            expected = compile_point_v02(request, point)
            ordered = [
                ("concrete.qcis", expected.concrete_source),
                ("ast.json", expected.ast_bytes),
                ("trace.json", expected.trace_bytes),
                ("logical_inventory.json", canonical_json_bytes(_plain(expected.logical_inventory))),
                ("result.json", canonical_json_bytes(_plain(expected.result))),
            ]
            names = {row.name for row in point_dir.iterdir()}
            prefixes = [{name for name, _raw in ordered[:index]} for index in range(len(ordered) + 1)]
            if names not in prefixes:
                raise ValueError("Runtime 0.2 interrupted point file prefix is invalid")
            for name, raw in ordered:
                if name in names and (point_dir / name).read_bytes() != raw:
                    raise ValueError("Runtime 0.2 interrupted point evidence mismatch")

    data = staging / "data"
    if data.exists():
        ordered_data = [
            "instruction_count.bin", "logical_sample_count.bin", "trace_byte_count.bin",
            "logical_array_bytes.bin", "dataset.json",
        ]
        names = {row.name for row in data.iterdir()}
        prefixes = [set(ordered_data[:index]) for index in range(len(ordered_data) + 1)]
        if names not in prefixes:
            raise ValueError("Runtime 0.2 interrupted dataset prefix is invalid")
        if "dataset.json" in names:
            validate_dataset_v02(data, len(points), _raw_sha(staging / "point_table.json"))

    terminal = {name for name in ("manifest.json", "verification_report.json", "receipt.json") if (staging / name).exists()}
    if terminal and terminal != {"manifest.json", "verification_report.json", "receipt.json"}:
        raise ValueError("Runtime 0.2 interrupted terminal evidence is incomplete")
    if terminal and not verify_experiment_run_v02(staging, root).ok:
        raise ValueError("Runtime 0.2 interrupted terminal evidence is invalid")


def _verify_completed_event_semantics(directory: Path, request) -> None:
    events = _load_events(directory / "events.jsonl")
    points = expand_scan_v02(request)
    _verify_common_event_prefix(directory, request, events, len(points))
    expected_types = ["run_reserved", "run_prepared", "run_started"]
    for _point in points:
        expected_types.extend(("point_started", "point_completed"))
    expected_types.extend(("run_finalizing", "run_completed"))
    if [event["event_type"] for event in events] != expected_types:
        raise ValueError("Runtime 0.2 completed event sequence is invalid")
    for index, point in enumerate(points):
        started = events[3 + (2 * index)]
        completed = events[4 + (2 * index)]
        identity = {"point_index": point.point_index, "point_id": point.point_id}
        if started["payload"] != identity:
            raise ValueError("Runtime 0.2 point_started identity is invalid")
        result_sha = _raw_sha(directory / f"points/{point.point_id}/result.json")
        if completed["payload"] != {**identity, "result_sha256": result_sha}:
            raise ValueError("Runtime 0.2 point_completed result binding is invalid")
    if events[-2]["payload"] != {"completed_point_count": len(points)}:
        raise ValueError("Runtime 0.2 finalizing count is invalid")
    if events[-1]["payload"] != {
        "dataset_manifest_sha256": _raw_sha(directory / "data/dataset.json"),
        "completed_point_count": len(points),
    }:
        raise ValueError("Runtime 0.2 completed event dataset binding is invalid")


def _verify_noncompleted_event_semantics(directory: Path, request, status: str) -> None:
    events = _load_events(directory / "events.jsonl")
    expected_tail = "run_cancelled" if status == "cancelled" else "run_failed"
    if events[-1]["event_type"] != expected_tail:
        raise ValueError("Runtime 0.2 non-completed terminal event is invalid")
    points = expand_scan_v02(request)
    if len(events) < 3:
        raise ValueError("Runtime 0.2 non-completed event sequence is incomplete")
    _verify_common_event_prefix(directory, request, events, len(points), require_started=False)
    cursor = 2
    if events[cursor]["event_type"] == "cancel_requested":
        if status != "cancelled" or cursor + 1 != len(events) - 1:
            raise ValueError("Runtime 0.2 pre-start cancellation sequence is invalid")
        return
    if events[cursor]["event_type"] != "run_started" or events[cursor]["payload"] != {"point_count": len(points)}:
        raise ValueError("Runtime 0.2 non-completed run_started event is invalid")
    cursor += 1
    completed_count = 0
    while cursor < len(events) - 1:
        event = events[cursor]
        if event["event_type"] == "run_finalizing":
            if status != "failed" or event["payload"] != {"completed_point_count": completed_count} or cursor + 1 != len(events) - 1:
                raise ValueError("Runtime 0.2 failed finalizing sequence is invalid")
            cursor += 1
            break
        if event["event_type"] == "cancel_requested":
            if status != "cancelled" or cursor + 1 != len(events) - 1:
                raise ValueError("Runtime 0.2 cancellation sequence is invalid")
            cursor += 1
            break
        if completed_count >= len(points) or event["event_type"] != "point_started":
            raise ValueError("Runtime 0.2 non-completed point sequence is invalid")
        point = points[completed_count]
        identity = {"point_index": point.point_index, "point_id": point.point_id}
        if event["payload"] != identity or cursor + 1 >= len(events):
            raise ValueError("Runtime 0.2 non-completed point identity is invalid")
        outcome = events[cursor + 1]
        if outcome["event_type"] == "point_failed":
            if status != "failed" or outcome["payload"]["point_index"] != point.point_index or outcome["payload"]["point_id"] != point.point_id or cursor + 2 != len(events) - 1:
                raise ValueError("Runtime 0.2 point failure sequence is invalid")
            cursor += 2
            break
        if outcome["event_type"] != "point_completed":
            raise ValueError("Runtime 0.2 point outcome is invalid")
        expected_result = compile_point_v02(request, point).result
        expected_sha = hashlib.sha256(canonical_json_bytes(_plain(expected_result))).hexdigest().upper()
        if outcome["payload"] != {**identity, "result_sha256": expected_sha}:
            raise ValueError("Runtime 0.2 failed-run point result binding is invalid")
        completed_count += 1
        cursor += 2
    tail_count = events[-1]["payload"].get("completed_point_count")
    if tail_count != completed_count:
        raise ValueError("Runtime 0.2 non-completed terminal point count is invalid")


def _verify_common_event_prefix(directory: Path, request, events: tuple[dict[str, Any], ...], point_count: int, *, require_started: bool = True) -> None:
    if [event["event_type"] for event in events[:2]] != ["run_reserved", "run_prepared"]:
        raise ValueError("Runtime 0.2 event prefix is invalid")
    expected_reserved = {
        "request_sha256": _raw_sha(directory / "request.json"),
        "point_table_sha256": _raw_sha(directory / "point_table.json"),
        "run_lock_sha256": _raw_sha(directory / "snapshots/run_lock.json"),
        "resource_lock_sha256": _raw_sha(directory / "snapshots/resource_lock.json"),
    }
    if events[0]["payload"] != expected_reserved:
        raise ValueError("Runtime 0.2 run_reserved bindings are invalid")
    if events[1]["payload"] != {
        "experiment_id": request.experiment_id,
        "backend_id": request.backend_id,
        "backend_capabilities_sha256": _sha_payload(CAPABILITIES),
    }:
        raise ValueError("Runtime 0.2 run_prepared bindings are invalid")
    if require_started and (events[2]["event_type"] != "run_started" or events[2]["payload"] != {"point_count": point_count}):
        raise ValueError("Runtime 0.2 run_started binding is invalid")


def _load_events(path: Path) -> tuple[dict[str, Any], ...]:
    events = []
    for line in path.read_bytes().splitlines(keepends=True):
        events.append(json.loads(line.decode("utf-8")))
    return tuple(events)


def _verify_points(directory: Path, request) -> tuple[Mapping[str, Any], ...]:
    points = expand_scan_v02(request)
    root = directory / "points"
    if not root.is_dir() or root.is_symlink() or {row.name for row in root.iterdir()} != {point.point_id for point in points}:
        raise ValueError("Runtime 0.2 point directory set is invalid")
    results = []
    for point in points:
        point_dir = root / point.point_id
        if point_dir.is_symlink() or {row.name for row in point_dir.iterdir()} != {
            "concrete.qcis", "ast.json", "trace.json", "logical_inventory.json", "result.json",
        }:
            raise ValueError("Runtime 0.2 point evidence file set is invalid")
        expected = compile_point_v02(request, point)
        comparisons = {
            "concrete.qcis": expected.concrete_source,
            "ast.json": expected.ast_bytes,
            "trace.json": expected.trace_bytes,
            "logical_inventory.json": canonical_json_bytes(_plain(expected.logical_inventory)),
            "result.json": canonical_json_bytes(_plain(expected.result)),
        }
        for name, raw in comparisons.items():
            if (point_dir / name).read_bytes() != raw:
                raise ValueError(f"Runtime 0.2 point replay mismatch: {point.point_id}/{name}")
        results.append(_load_canonical(point_dir / "result.json"))
    return tuple(results)


def _verify_snapshots(directory: Path, root: Path, request, manifest: Mapping[str, Any]) -> None:
    snapshots = directory / "snapshots"
    expected_names = {
        "device.yaml", "calibration.json", "environment.json", "source.json", "compiler_authority.json",
        "run_lock.json", "resource_lock.json",
    }
    if not snapshots.is_dir() or snapshots.is_symlink() or {row.name for row in snapshots.iterdir()} != expected_names:
        raise ValueError("Runtime 0.2 snapshot file set is invalid")
    if (snapshots / "device.yaml").read_bytes() != request.device_snapshot.read_bytes():
        raise ValueError("Runtime 0.2 device snapshot mismatch")
    if (snapshots / "calibration.json").read_bytes() != request.calibration_snapshot.read_bytes():
        raise ValueError("Runtime 0.2 calibration snapshot mismatch")
    if _load_canonical(snapshots / "compiler_authority.json") != _plain(request.authority):
        raise ValueError("Runtime 0.2 compiler authority snapshot mismatch")
    environment = _load_canonical(snapshots / "environment.json")
    validate_locked_environment(root, build_environment_snapshot())
    if environment != build_environment_snapshot():
        raise ValueError("Runtime 0.2 environment snapshot mismatch")
    verify_source_snapshot_v02(_load_canonical(snapshots / "source.json"), root)
    expected_hashes = {
        "device": _raw_sha(snapshots / "device.yaml"),
        "calibration": _raw_sha(snapshots / "calibration.json"),
        "environment": _raw_sha(snapshots / "environment.json"),
        "source": _raw_sha(snapshots / "source.json"),
        "compiler_authority": _raw_sha(snapshots / "compiler_authority.json"),
        "run_lock": _raw_sha(snapshots / "run_lock.json"),
        "resource_lock": _raw_sha(snapshots / "resource_lock.json"),
    }
    if manifest["snapshots"] != expected_hashes:
        raise ValueError("Runtime 0.2 snapshot hash inventory is invalid")
    run_lock = _load_canonical(snapshots / "run_lock.json")
    resource_lock = _load_canonical(snapshots / "resource_lock.json")
    _verify_lock_payload(run_lock, directory.name)
    _verify_lock_payload(resource_lock, directory.name)
    if run_lock != resource_lock or run_lock.get("run_id") != directory.name:
        raise ValueError("Runtime 0.2 lock snapshot identity is invalid")
    if run_lock.get("request_sha256") != _raw_sha(directory / "request.json"):
        raise ValueError("Runtime 0.2 lock request binding is invalid")
    if run_lock.get("environment_fingerprint") != _raw_sha(snapshots / "environment.json"):
        raise ValueError("Runtime 0.2 lock environment binding is invalid")
    if run_lock.get("resource_key") != resource_key(BACKEND_ID, _raw_sha(snapshots / "device.yaml")):
        raise ValueError("Runtime 0.2 lock resource key is invalid")


def _verify_program(directory: Path, request) -> None:
    program_dir = directory / "program"
    if not program_dir.is_dir() or program_dir.is_symlink() or {row.name for row in program_dir.iterdir()} != {"template.qcis", "program.json"}:
        raise ValueError("Runtime 0.2 program evidence file set is invalid")
    if (program_dir / "template.qcis").read_bytes() != request.program["source"].encode("ascii"):
        raise ValueError("Runtime 0.2 program template bytes mismatch")
    if _load_canonical(program_dir / "program.json") != _plain(request.program):
        raise ValueError("Runtime 0.2 program envelope mismatch")


def _verify_terminal_schemas(directory: Path, manifest: Mapping[str, Any], report: Mapping[str, Any], receipt: Mapping[str, Any]) -> str:
    manifest_keys = {
        "schema_version", "artifact_type", "run_id", "status", "created_utc", "terminal_utc", "experiment_id",
        "backend_id", "output_root", "claim_envelope", "request_sha256", "point_table_sha256", "events_sha256",
        "event_tail_sha256", "snapshots", "backend_capabilities", "payload_files", "dataset_summary",
    }
    report_keys = {
        "schema_version", "artifact_type", "run_id", "status", "ok", "manifest_sha256", "claim_envelope",
        "checks", "blocking_reasons",
    }
    receipt_keys = {
        "schema_version", "artifact_type", "run_id", "status", "claim_envelope", "manifest_sha256",
        "verification_report_sha256", "event_tail_sha256", "elapsed_seconds", "published_relative_path",
        "run_lock_sha256", "resource_lock_sha256", "dataset_hashes",
    }
    if set(manifest) != manifest_keys or set(report) != report_keys or set(receipt) != receipt_keys:
        raise ValueError("Runtime 0.2 terminal artifact keys are invalid")
    if any(row.get("schema_version") != "0.2" for row in (manifest, report, receipt)):
        raise ValueError("Runtime 0.2 terminal artifact version is invalid")
    if manifest["artifact_type"] != "stage_06_experiment_run_manifest" or report["artifact_type"] != "stage_06_experiment_run_verification_report" or receipt["artifact_type"] != "stage_06_experiment_run_receipt":
        raise ValueError("Runtime 0.2 terminal artifact identity is invalid")
    if any(row.get("run_id") != directory.name for row in (manifest, report, receipt)):
        raise ValueError("Runtime 0.2 terminal run identity is invalid")
    status = manifest["status"]
    if status not in {"completed", "failed", "cancelled"} or report["status"] != status or receipt["status"] != status:
        raise ValueError("Runtime 0.2 terminal status is invalid")
    if manifest["experiment_id"] != EXPERIMENT_ID or manifest["backend_id"] != BACKEND_ID:
        raise ValueError("Runtime 0.2 registered pair is invalid")
    if any(row.get("claim_envelope") != CLAIM_ENVELOPE for row in (manifest, report, receipt)):
        raise ValueError("Runtime 0.2 claim envelope is invalid")
    manifest_sha = _raw_sha(directory / "manifest.json")
    report_sha = _raw_sha(directory / "verification_report.json")
    if report["manifest_sha256"] != manifest_sha or receipt["manifest_sha256"] != manifest_sha or receipt["verification_report_sha256"] != report_sha:
        raise ValueError("Runtime 0.2 terminal hash graph is invalid")
    if report["ok"] is not True or report["blocking_reasons"] != []:
        raise ValueError("Runtime 0.2 embedded verification report is invalid")
    expected_checks = [
        {"name": "payload_inventory_verified", "passed": True},
        {"name": "event_chain_verified", "passed": True},
        {"name": "qcis_replay_verified", "passed": True, "message": "verified" if status == "completed" else "not_applicable"},
        {"name": "claim_envelope_verified", "passed": True},
        {"name": "dataset_contract_applied", "passed": True, "message": "verified" if status == "completed" else "not_applicable"},
    ]
    if report["checks"] != expected_checks:
        raise ValueError("Runtime 0.2 embedded verification checks are invalid")
    elapsed = receipt["elapsed_seconds"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, int | float) or not math.isfinite(float(elapsed)) or elapsed < 0:
        raise ValueError("Runtime 0.2 receipt elapsed time is invalid")
    if receipt["run_lock_sha256"] != manifest["snapshots"]["run_lock"] or receipt["resource_lock_sha256"] != manifest["snapshots"]["resource_lock"]:
        raise ValueError("Runtime 0.2 receipt lock hashes are invalid")
    return status


def _load_canonical(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        raise ValueError(f"Runtime 0.2 artifact is not canonical: {path.name}")
    return payload


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _sha_payload(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _raw_sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _safe_raw_sha(path: Path) -> str:
    try:
        return _raw_sha(path)
    except OSError:
        return ""
