"""Independent raw-array replay for Stage 4.1 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from sqvm.control.stage4_1_artifacts import (
    CONTROL_NAME, ENVIRONMENT_SNAPSHOT_NAME, INVENTORY_NAME, MANIFEST_NAME,
    RECEIPT_NAME, REPORT_NAME, SOURCE_SNAPSHOT_NAME, Stage41ArtifactError,
)
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.runtime.storage import inventory_tree


@dataclass(frozen=True, slots=True)
class VerifiedControlHandle:
    """Process-local capability; it is never serialized back into the artifact."""

    control_id: str
    artifact_root: Path
    manifest_sha256: str
    receipt_sha256: str
    inventory_sha256: str
    effective_control_sha256: str
    time_center_ns: np.ndarray
    xy_drive_GHz: Mapping[str, tuple[np.ndarray, np.ndarray]]
    absolute_flux_phi0: Mapping[str, np.ndarray]
    frame_reference_frequency_GHz: Mapping[str, float]
    verification_report: Mapping[str, Any]


def verify_parameterized_control_staging(root: str | Path, context: Any) -> Mapping[str, Any]:
    """Replay an incomplete staging tree before it may be published."""

    artifact = Path(root)
    control, inventory = _canonical(artifact / CONTROL_NAME), _canonical(artifact / INVENTORY_NAME)
    _canonical(artifact / SOURCE_SNAPSHOT_NAME)
    _canonical(artifact / ENVIRONMENT_SNAPSHOT_NAME)
    arrays = _arrays(artifact, inventory)
    checks = _replay_checks(arrays, control, context)
    if not all(row["passed"] for row in checks):
        raise Stage41ArtifactError("FORWARD_RECONSTRUCTION_MISMATCH", "staging replay failed")
    return {"ok": True, "checks": checks, "blocking_reasons": []}


def verify_parameterized_control_artifact(artifact_root: str | Path, context: Any) -> VerifiedControlHandle:
    """Verify a published control point, then construct a read-only handle."""

    root = _safe_root(artifact_root, context)
    _reject_links(root)
    control, inventory = _canonical(root / CONTROL_NAME), _canonical(root / INVENTORY_NAME)
    manifest, report, receipt = _canonical(root / MANIFEST_NAME), _canonical(root / REPORT_NAME), _canonical(root / RECEIPT_NAME)
    _canonical(root / SOURCE_SNAPSHOT_NAME)
    _canonical(root / ENVIRONMENT_SNAPSHOT_NAME)
    if control.get("status") != "published" or report.get("ok") is not True or receipt.get("status") != "published":
        _fail("ARTIFACT_VERIFICATION_FAILED", "publication status")
    _verify_bindings(root, control, inventory, manifest, report, receipt)
    arrays = _arrays(root, inventory)
    checks = _replay_checks(arrays, control, context)
    if not all(row["passed"] for row in checks):
        _fail("FORWARD_RECONSTRUCTION_MISMATCH", "post-publication replay")
    effective = arrays["effective"]
    frame = control.get("effective", {}).get("frame_reference_frequency_GHz")
    if not isinstance(frame, Mapping) or set(frame) != {"q1", "q2"}:
        _fail("PLAN_SCHEMA_INVALID", "frame references")
    return VerifiedControlHandle(
        control_id=control["control_id"], artifact_root=root, manifest_sha256=raw_file_sha256(root / MANIFEST_NAME),
        receipt_sha256=raw_file_sha256(root / RECEIPT_NAME), inventory_sha256=raw_file_sha256(root / INVENTORY_NAME),
        effective_control_sha256=control["effective"]["effective_control_sha256"], time_center_ns=_readonly(effective["time_center_ns"]),
        xy_drive_GHz={mode: (_readonly(effective[f"{mode}_i"]), _readonly(effective[f"{mode}_q"])) for mode in ("q1", "q2")},
        absolute_flux_phi0={mode: _readonly(effective[f"{mode}_flux_absolute"]) for mode in ("q1", "q2", "c")},
        frame_reference_frequency_GHz={mode: float(frame[mode]) for mode in ("q1", "q2")},
        verification_report={"ok": True, "checks": checks, "blocking_reasons": []},
    )


def _replay_checks(arrays: Mapping[str, Any], control: Mapping[str, Any], context: Any) -> list[dict[str, Any]]:
    config = _field(context, "control_chain_config")
    expected_awg, expected_effective = independent_electronics_replay(arrays["logical"], config)
    checks = [
        _check("sample_grid_exact", _equal_maps(arrays["awg"], expected_awg, "time_center_ns")),
        _check("forward_reconstruction_matches_reference", _equal_maps(arrays["effective"], expected_effective, "time_center_ns")),
        _check("idle_added_exactly_once", _idle_once(arrays["effective"], _field(config, "idle_flux_phi0"))),
        _check("effective_arrays_finite", all(np.all(np.isfinite(value)) for value in arrays["effective"].values())),
        _check("device_limits_satisfied", _limits(arrays["effective"], _field(context, "device_flux_limits_phi0", None))),
    ]
    return checks


def independent_electronics_replay(logical: Mapping[str, np.ndarray], config: Any) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Standalone electronics replay; it intentionally does not call the compiler."""

    common = {"time_center_ns", "q1_i", "q1_q", "q2_i", "q2_q"}
    delta_names = {f"{mode}_flux_delta" for mode in ("q1", "q2", "c")}
    absolute_names = {f"{mode}_flux_absolute" for mode in ("q1", "q2", "c")}
    if set(logical) != common | delta_names and set(logical) != common | absolute_names:
        _fail("ARRAY_CONTRACT_INVALID", "logical inventory")
    dt, lanes, mixing, idle, dac = _field(config, "dt_ns"), _field(config, "lanes"), _field(config, "static_mixing"), _field(config, "idle_flux_phi0"), _field(config, "dac")
    n = logical["time_center_ns"].size
    if n == 0 or not _finite(dt) or any(value.ndim != 1 or value.size != n for value in logical.values()):
        _fail("CLOCK_MISMATCH", "logical grid")
    uses_absolute = absolute_names.issubset(logical)
    groups = {"xy": ("q1_i", "q1_q", "q2_i", "q2_q"), "z": ("q1_flux_delta", "q2_flux_delta", "c_flux_delta")}
    descriptions: dict[str, tuple[list[str], np.ndarray]] = {}
    required_lanes: set[str] = set()
    for group, coordinates in groups.items():
        row = _field(mixing, group)
        lane_names = list(_field(row, "input_lanes"))
        output_names = tuple(_canon(item) for item in _field(row, "output_coordinates"))
        matrix = np.asarray(_field(row, "matrix"), dtype=float)
        if output_names != coordinates or matrix.shape != (len(coordinates), len(lane_names)) or np.linalg.matrix_rank(matrix) != len(coordinates) or not np.all(np.isfinite(matrix)) or np.linalg.cond(matrix) > 100.0:
            _fail("MIXING_MATRIX_INVALID", group)
        descriptions[group] = (lane_names, matrix)
        required_lanes.update(lane_names)
    if set(lanes) != required_lanes:
        _fail("NAMED_MAPPING_INVALID", "lane registry")
    lmax = max(_latency(_field(lanes, lane)) for lane in lanes)
    fmax = max(len(_fir(_field(lanes, lane))) for lane in lanes)
    n_awg, p_count = n + lmax, n + lmax + fmax - 1 + lmax
    requested = {lane: np.zeros(n_awg, dtype="<f8") for lane in lanes}
    for group, coordinates in groups.items():
        lane_names, matrix = descriptions[group]
        if group == "z" and uses_absolute:
            desired = np.vstack([logical[f"{mode}_flux_absolute"] for mode in ("q1", "q2", "c")])
            desired -= np.asarray([float(_field(idle, mode)) for mode in ("q1", "q2", "c")])[:, None]
        else:
            desired = np.vstack([logical[name] for name in coordinates])
        try:
            solved = np.linalg.solve(matrix, desired)
        except np.linalg.LinAlgError as exc:
            raise Stage41ArtifactError("MIXING_MATRIX_INVALID", group) from exc
        for index, lane in enumerate(lane_names):
            offset = lmax - _latency(_field(lanes, lane))
            requested[lane][offset:offset + n] = solved[index]
    awg: dict[str, Any] = {"time_center_ns": (np.arange(n_awg, dtype="<f8") - lmax + 0.5) * float(dt)}
    delivered: dict[str, np.ndarray] = {}
    for lane, values in requested.items():
        codes, reconstructed = _quantize(values, dac)
        full = np.convolve(reconstructed, _fir(_field(lanes, lane)), mode="full")
        row = np.zeros(p_count, dtype="<f8")
        latency = _latency(_field(lanes, lane))
        row[latency:latency + full.size] = full
        delivered[lane] = row
        awg[lane] = {"requested_voltage": values, "dac_codes": codes, "reconstructed_voltage": reconstructed, "delivered_voltage": row}
    effective: dict[str, np.ndarray] = {"time_center_ns": (np.arange(p_count, dtype="<f8") - lmax + 0.5) * float(dt)}
    for group, coordinates in groups.items():
        lane_names, matrix = descriptions[group]
        output = matrix @ np.vstack([delivered[lane] for lane in lane_names])
        for index, name in enumerate(coordinates):
            effective[name] = np.asarray(output[index], dtype="<f8")
    for mode in ("q1", "q2", "c"):
        effective[f"{mode}_flux_absolute"] = np.asarray(effective[f"{mode}_flux_delta"] + float(_field(idle, mode)), dtype="<f8")
    return awg, effective


def _arrays(root: Path, inventory: Mapping[str, Any]) -> dict[str, Any]:
    rows = inventory.get("arrays")
    if not isinstance(rows, list):
        _fail("ARRAY_CONTRACT_INVALID", "inventory")
    result: dict[str, Any] = {"logical": {}, "awg": {}, "effective": {}}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"name", "path", "dtype", "shape", "unit", "byte_length", "sha256"}:
            _fail("ARRAY_CONTRACT_INVALID", "inventory row")
        path = _file(root, row["path"])
        raw = path.read_bytes()
        if len(raw) != row["byte_length"] or hashlib.sha256(raw).hexdigest().upper() != row["sha256"]:
            _fail("PLAN_HASH_MISMATCH", row["name"])
        array = np.frombuffer(raw, dtype=np.dtype(row["dtype"])).copy()
        if array.size != row["shape"][0] or (array.dtype.kind == "f" and not np.all(np.isfinite(array))):
            _fail("ARRAY_CONTRACT_INVALID", row["name"])
        parts = str(row["name"]).split("/")
        if parts == ["awg", "time_center_ns"]:
            result["awg"]["time_center_ns"] = array
        elif parts[0] == "awg" and len(parts) == 3:
            result["awg"].setdefault(parts[1], {})[parts[2]] = array
        elif parts[0] in {"logical", "effective"} and len(parts) == 2:
            result[parts[0]][parts[1]] = array
        else:
            _fail("ARRAY_CONTRACT_INVALID", row["name"])
    return result


def _verify_bindings(root: Path, control: Mapping[str, Any], inventory: Mapping[str, Any], manifest: Mapping[str, Any], report: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    if manifest.get("control_id") != control.get("control_id") or receipt.get("control_id") != control.get("control_id") or report.get("control_id") != control.get("control_id"):
        _fail("PLAN_HASH_MISMATCH", "control_id")
    if manifest.get("array_inventory_sha256") != raw_file_sha256(root / INVENTORY_NAME) or manifest.get("control_sha256") != raw_file_sha256(root / CONTROL_NAME):
        _fail("PLAN_HASH_MISMATCH", "manifest hashes")
    if receipt.get("manifest_sha256") != raw_file_sha256(root / MANIFEST_NAME) or receipt.get("verification_report_sha256") != raw_file_sha256(root / REPORT_NAME):
        _fail("PLAN_HASH_MISMATCH", "receipt hashes")
    actual = [row for row in inventory_tree(root) if row["path"] not in {MANIFEST_NAME, REPORT_NAME, RECEIPT_NAME}]
    if manifest.get("payload_files") != actual:
        _fail("ARTIFACT_VERIFICATION_FAILED", "payload inventory")


def _equal_maps(actual: Mapping[str, Any], expected: Mapping[str, Any], time_name: str) -> bool:
    if set(actual) != set(expected):
        return False
    for key in expected:
        if isinstance(expected[key], Mapping):
            if not isinstance(actual[key], Mapping) or not _equal_maps(actual[key], expected[key], time_name):
                return False
        elif not np.array_equal(actual[key], expected[key]):
            return False
    return True


def _idle_once(effective: Mapping[str, np.ndarray], idle: Any) -> bool:
    return all(np.array_equal(effective[f"{mode}_flux_absolute"] - effective[f"{mode}_flux_delta"], np.full(effective[f"{mode}_flux_delta"].size, float(_field(idle, mode)), dtype="<f8")) for mode in ("q1", "q2", "c"))


def _limits(effective: Mapping[str, np.ndarray], limits: Any) -> bool:
    if limits is None:
        return True
    if not isinstance(limits, Mapping) or set(limits) != {"q1", "q2", "c"}:
        return False
    return all(isinstance(limits[mode], (tuple, list)) and len(limits[mode]) == 2 and np.all((effective[f"{mode}_flux_absolute"] >= limits[mode][0]) & (effective[f"{mode}_flux_absolute"] <= limits[mode][1])) for mode in limits)


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "reason_code": "OK" if passed else "FORWARD_RECONSTRUCTION_MISMATCH", "evidence_ref": "independent_raw_replay"}


def _quantize(values: np.ndarray, dac: Any) -> tuple[np.ndarray, np.ndarray]:
    lsb, minimum, maximum = Decimal(str(_field(dac, "lsb_V"))), int(_field(dac, "code_min")), int(_field(dac, "code_max"))
    codes = np.empty(values.size, dtype="<i8")
    for index, value in enumerate(values):
        code = int((Decimal.from_float(float(value)) / lsb).to_integral_value(rounding=ROUND_HALF_EVEN))
        if code < minimum or code > maximum:
            _fail("DAC_RANGE_EXCEEDED", str(index))
        codes[index] = code
    return codes, np.asarray([float(Decimal(int(code)) * lsb) for code in codes], dtype="<f8")


def _latency(row: Any) -> int:
    value = _field(row, "latency_samples")
    if type(value) is not int or value < 0:
        _fail("LATENCY_CONTRACT_INVALID", "latency")
    return value


def _fir(row: Any) -> np.ndarray:
    value = _field(row, "fir")
    if not isinstance(value, (list, tuple)) or not value or not all(_finite(item) for item in value):
        _fail("LATENCY_CONTRACT_INVALID", "FIR")
    return np.asarray(value, dtype="<f8")


def _canon(name: Any) -> str:
    return {"q1_drive_i_GHz": "q1_i", "q1_drive_q_GHz": "q1_q", "q2_drive_i_GHz": "q2_i", "q2_drive_q_GHz": "q2_q", "q1_delta_flux_phi0": "q1_flux_delta", "q2_delta_flux_phi0": "q2_flux_delta", "c_delta_flux_phi0": "c_flux_delta"}.get(str(name), str(name))


def _safe_root(value: str | Path, context: Any) -> Path:
    raw, bound = Path(value), Path(_field(context, "output_root")).resolve()
    if raw.is_symlink() or not raw.is_dir() or raw.name.startswith(".") or ".staging." in raw.name:
        _fail("ARTIFACT_VERIFICATION_FAILED", "artifact root")
    root = raw.resolve()
    try:
        root.relative_to(bound)
    except ValueError as exc:
        raise Stage41ArtifactError("ARTIFACT_VERIFICATION_FAILED", "outside output root") from exc
    return root


def _canonical(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail("ARTIFACT_VERIFICATION_FAILED", str(path.name))
    try:
        value = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Stage41ArtifactError("ARTIFACT_VERIFICATION_FAILED", path.name) from exc
    if not isinstance(value, dict) or path.read_bytes() != canonical_json_bytes(value):
        _fail("ARTIFACT_VERIFICATION_FAILED", f"canonical:{path.name}")
    return value


def _file(root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        _fail("ARTIFACT_VERIFICATION_FAILED", "array path")
    path = root.joinpath(*Path(relative).parts)
    if path.is_symlink() or not path.is_file():
        _fail("ARTIFACT_VERIFICATION_FAILED", relative)
    return path


def _reject_links(root: Path) -> None:
    if any(path.is_symlink() for path in root.rglob("*")):
        _fail("ARTIFACT_VERIFICATION_FAILED", "symlink")


def _readonly(value: np.ndarray) -> np.ndarray:
    result = np.asarray(value, dtype="<f8").copy(order="C")
    result.setflags(write=False)
    return result


def _field(value: Any, name: str, default: Any = ... ) -> Any:
    if isinstance(value, Mapping) and name in value:
        return value[name]
    if hasattr(value, name):
        return getattr(value, name)
    if default is ...:
        _fail("PLAN_SCHEMA_INVALID", f"missing {name}")
    return default


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float, Decimal)) and math.isfinite(float(value))


def _fail(code: str, detail: str) -> None:
    raise Stage41ArtifactError(code, detail)
