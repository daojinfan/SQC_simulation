"""Bounded, evidence-backed waveform projections for the Web console."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Protocol

import numpy as np

from sqvm.web.plotting import PlotSpecError, build_min_max_envelope


_MAX_JSON_BYTES = 2_000_000
_MAX_ARRAY_BYTES = 16_000_000
_MAX_VIEW_BYTES = 128_000_000
_MAX_SAMPLES = _MAX_ARRAY_BYTES // 8
_MAX_CIRCUITS = 10_000
_VIEWS = {"awg", "effective", "logical"}
_HEX = frozenset("0123456789ABCDEF")


class WaveformEvidenceError(ValueError):
    """Stable failure raised when waveform evidence is absent or invalid."""

    def __init__(self, message: str, *, status: int = 422) -> None:
        self.status = status
        super().__init__(message)


class EvidenceReader(Protocol):
    def has(self, path: str) -> bool: ...

    def read_bytes(self, path: str, *, maximum_bytes: int) -> bytes: ...


class DirectoryEvidenceReader:
    """Read only regular, single-link files beneath one trusted carrier root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))

    def has(self, path: str) -> bool:
        try:
            self._file(path)
        except WaveformEvidenceError:
            return False
        return True

    def read_bytes(self, path: str, *, maximum_bytes: int) -> bytes:
        target = self._file(path)
        info = target.stat(follow_symlinks=False)
        if info.st_size > maximum_bytes:
            raise WaveformEvidenceError("波形证据文件超过 Web 读取上限")
        raw = target.read_bytes()
        if len(raw) != info.st_size:
            raise WaveformEvidenceError("读取过程中波形证据发生变化")
        return raw

    def _file(self, relative: str) -> Path:
        _safe_relative_path(relative)
        current = self.root
        for part in relative.split("/"):
            current = current / part
            try:
                info = current.lstat()
            except OSError as exc:
                raise WaveformEvidenceError("该实验没有可用的通道波形证据", status=404) from exc
            if current != self.root / Path(*relative.split("/")) and not current.is_dir():
                raise WaveformEvidenceError("波形证据路径无效")
            if current.is_symlink() or getattr(info, "st_reparse_tag", 0):
                raise WaveformEvidenceError("波形证据路径包含链接")
        info = current.stat(follow_symlinks=False)
        if not current.is_file() or info.st_nlink != 1:
            raise WaveformEvidenceError("波形证据文件类型无效")
        return current


class ArchiveEvidenceReader:
    """Small adapter over the verified archive reader."""

    def __init__(self, reader: Any) -> None:
        self.reader = reader
        self._paths = frozenset(reader.paths())

    def has(self, path: str) -> bool:
        return path in self._paths

    def read_bytes(self, path: str, *, maximum_bytes: int) -> bytes:
        try:
            return self.reader.read_bytes(path, maximum_bytes=maximum_bytes)
        except Exception as exc:
            raise WaveformEvidenceError("归档中的波形证据无法读取") from exc


def waveform_catalog(reader: EvidenceReader, run_id: str) -> dict[str, Any]:
    """Return lightweight point/QCIS bindings without opening waveform arrays."""

    workflow = _json(reader, "workflow.json")
    if workflow.get("run_id") != run_id:
        raise WaveformEvidenceError("实验与波形证据的 run_id 不一致")
    records = _circuit_records(reader)
    if not records:
        raise WaveformEvidenceError("该实验没有可用的通道波形证据", status=404)
    default = records[len(records) // 2]["circuit_id"]
    return {
        "schema_version": "0.1",
        "run_id": run_id,
        "default_circuit_id": default,
        "views": [
            {"id": "awg", "label": "真实 AWG 输出"},
            {"id": "effective", "label": "QuTiP 有效控制"},
            {"id": "logical", "label": "编译逻辑波形"},
        ],
        "points": [
            {key: value for key, value in row.items() if key != "qcis_source"}
            for row in records
        ],
    }


def waveform_point(
    reader: EvidenceReader,
    run_id: str,
    circuit_id: str,
    *,
    view: str = "awg",
    max_points: int = 2_000,
) -> dict[str, Any]:
    """Return one circuit's QCIS and one lazily selected waveform layer."""

    _identifier(circuit_id, "circuit_id")
    if view not in _VIEWS:
        raise WaveformEvidenceError("波形 view 必须是 awg、effective 或 logical", status=400)
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 2 <= max_points <= 10_000:
        raise WaveformEvidenceError("max_points 必须在 [2,10000] 内", status=400)
    workflow = _json(reader, "workflow.json")
    if workflow.get("run_id") != run_id:
        raise WaveformEvidenceError("实验与波形证据的 run_id 不一致")
    matches = [row for row in _circuit_records(reader) if row["circuit_id"] == circuit_id]
    if len(matches) != 1:
        raise WaveformEvidenceError("实验扫描点不存在", status=404)
    record = matches[0]
    prefix = f"execution/{circuit_id}/stage41/control/"
    inventory_raw = reader.read_bytes(prefix + "array_inventory.json", maximum_bytes=_MAX_JSON_BYTES)
    control_raw = reader.read_bytes(prefix + "control.json", maximum_bytes=_MAX_JSON_BYTES)
    manifest = _json(reader, f"execution/{circuit_id}/manifest.json")
    manifest_rows = _manifest_rows(manifest)
    _verify_manifest_raw(manifest_rows, "stage41/control/array_inventory.json", inventory_raw)
    _verify_manifest_raw(manifest_rows, "stage41/control/control.json", control_raw)
    inventory = _decode_json(inventory_raw, "array_inventory.json")
    control = _decode_json(control_raw, "control.json")
    if control.get("status") != "published":
        raise WaveformEvidenceError("Stage 4.1 控制产物未发布")
    control_id = _sha(control.get("control_id"), "control_id")
    rows = _inventory_rows(inventory)
    arrays = _read_view_arrays(reader, prefix, rows, manifest_rows, view)
    specs = _plot_specs(circuit_id, view, arrays, max_points)
    qcis_source = record["qcis_source"]
    source_sha = hashlib.sha256(qcis_source.encode("ascii")).hexdigest().upper()
    expected_sha = record.get("circuit_sha256")
    if expected_sha is not None and expected_sha != source_sha:
        raise WaveformEvidenceError("QCIS 指令与线路哈希不一致")
    sample_counts = {
        domain: len(values.get("time_center_ns", ()))
        for domain, values in arrays.items()
    }
    return {
        "schema_version": "0.1",
        "run_id": run_id,
        "point": {key: value for key, value in record.items() if key != "qcis_source"},
        "qcis": {"source": qcis_source, "sha256": source_sha},
        "control": {
            "control_id": control_id,
            "status": control.get("status"),
            "sample_counts": sample_counts,
        },
        "view": view,
        "plot_specs": specs,
    }


def _circuit_records(reader: EvidenceReader) -> list[dict[str, Any]]:
    dataset = _json(reader, "dataset.json")
    by_id: dict[str, dict[str, Any]] = {}
    batch_path = "execution/batch/request.json"
    if reader.has(batch_path):
        request = _json(reader, batch_path)
        circuits = request.get("circuits")
        if not isinstance(circuits, list) or len(circuits) > _MAX_CIRCUITS:
            raise WaveformEvidenceError("实验线路清单无效")
        for row in circuits:
            record = _circuit_record(row)
            by_id[record["circuit_id"]] = record

    points = dataset.get("points")
    if not isinstance(points, list) or len(points) > _MAX_CIRCUITS:
        raise WaveformEvidenceError("实验数据点清单无效")
    axis = dataset.get("axis") if isinstance(dataset.get("axis"), Mapping) else {}
    axis_values = axis.get("values") if isinstance(axis.get("values"), list) else []
    axis_name = axis.get("name") if isinstance(axis.get("name"), str) else None
    axis_unit = axis.get("unit") if isinstance(axis.get("unit"), str) else None
    for index, raw in enumerate(points):
        if not isinstance(raw, Mapping):
            raise WaveformEvidenceError("实验数据点无效")
        point = raw.get("point") if isinstance(raw.get("point"), Mapping) else raw
        circuit_id = point.get("circuit_id")
        if not isinstance(circuit_id, str):
            raise WaveformEvidenceError("实验数据点缺少 circuit_id")
        _identifier(circuit_id, "circuit_id")
        current = by_id.get(circuit_id, {})
        source = point.get("qcis_source", current.get("qcis_source"))
        if not isinstance(source, str) or not source or len(source.encode("utf-8")) > 256_000:
            raise WaveformEvidenceError("实验数据点缺少 QCIS 指令")
        try:
            source.encode("ascii")
        except UnicodeEncodeError as exc:
            raise WaveformEvidenceError("QCIS 指令必须是 ASCII") from exc
        point_index = point.get("point_index", raw.get("point_index", index))
        if type(point_index) is not int or point_index < 0:
            raise WaveformEvidenceError("实验 point_index 无效")
        coordinates = point.get("coordinates_GHz")
        if isinstance(coordinates, Mapping):
            scan_coordinates = {str(key): _finite(value, "扫描坐标") for key, value in coordinates.items()}
            coordinate_unit = "GHz"
        elif index < len(axis_values) and axis_name:
            scan_coordinates = {axis_name: _finite(axis_values[index], "扫描坐标")}
            coordinate_unit = axis_unit
        else:
            scan_coordinates = {}
            coordinate_unit = axis_unit
        circuit_sha = current.get("circuit_sha256", raw.get("circuit_sha256"))
        if circuit_sha is not None:
            circuit_sha = _sha(circuit_sha, "circuit_sha256")
        by_id[circuit_id] = {
            "point_index": point_index,
            "circuit_id": circuit_id,
            "circuit_sha256": circuit_sha,
            "scan_coordinates": scan_coordinates,
            "coordinate_unit": coordinate_unit,
            "label": _point_label(point_index, scan_coordinates, coordinate_unit),
            "qcis_source": source,
        }
    records = sorted(by_id.values(), key=lambda row: (row["point_index"], row["circuit_id"]))
    if len(records) != len(points) or len({row["point_index"] for row in records}) != len(records):
        raise WaveformEvidenceError("实验线路与数据点无法一一对应")
    for row in records:
        if not reader.has(f"execution/{row['circuit_id']}/stage41/control/array_inventory.json"):
            raise WaveformEvidenceError("实验扫描点缺少 Stage 4.1 波形证据")
    return records


def _circuit_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise WaveformEvidenceError("实验线路记录无效")
    circuit_id = value.get("circuit_id")
    source = value.get("qcis_source")
    point_index = value.get("point_index")
    if not isinstance(circuit_id, str) or not isinstance(source, str) or type(point_index) is not int:
        raise WaveformEvidenceError("实验线路记录无效")
    _identifier(circuit_id, "circuit_id")
    return {
        "circuit_id": circuit_id,
        "point_index": point_index,
        "circuit_sha256": _sha(value.get("circuit_sha256"), "circuit_sha256"),
        "qcis_source": source,
    }


def _inventory_rows(inventory: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if (
        inventory.get("schema_version") != "0.1"
        or inventory.get("artifact_type") != "stage_04_1_array_inventory"
        or not isinstance(inventory.get("arrays"), list)
    ):
        raise WaveformEvidenceError("Stage 4.1 波形清单格式无效")
    rows: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    for raw in inventory["arrays"]:
        if not isinstance(raw, Mapping) or set(raw) != {"name", "path", "dtype", "shape", "unit", "byte_length", "sha256"}:
            raise WaveformEvidenceError("Stage 4.1 波形清单条目无效")
        name, path, dtype, shape = raw["name"], raw["path"], raw["dtype"], raw["shape"]
        if not isinstance(name, str) or name in rows or not isinstance(path, str) or path in paths or path != f"arrays/{name}.bin":
            raise WaveformEvidenceError("Stage 4.1 波形清单路径无效")
        if name.split("/", 1)[0] not in {"logical", "awg", "effective"}:
            raise WaveformEvidenceError("Stage 4.1 波形域无效")
        if dtype not in {"<f8", "<i8"} or not isinstance(shape, list) or len(shape) != 1 or type(shape[0]) is not int or not 1 <= shape[0] <= _MAX_SAMPLES:
            raise WaveformEvidenceError("Stage 4.1 波形数组类型或长度无效")
        if raw["byte_length"] != shape[0] * 8 or raw["byte_length"] > _MAX_ARRAY_BYTES:
            raise WaveformEvidenceError("Stage 4.1 波形数组字节长度无效")
        _sha(raw["sha256"], "array sha256")
        rows[name] = dict(raw)
        paths.add(path)
    return rows


def _manifest_rows(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw_rows = manifest.get("payload_files")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise WaveformEvidenceError("线路级 manifest 缺少 payload_files")
    rows: dict[str, dict[str, Any]] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping) or set(raw) != {"path", "byte_length", "raw_sha256"}:
            raise WaveformEvidenceError("线路级 manifest 条目无效")
        path = raw.get("path")
        length = raw.get("byte_length")
        if not isinstance(path, str) or path in rows or type(length) is not int or length < 0:
            raise WaveformEvidenceError("线路级 manifest 路径或长度无效")
        _safe_relative_path(path)
        rows[path] = {
            "path": path,
            "byte_length": length,
            "raw_sha256": _sha(raw.get("raw_sha256"), "manifest sha256"),
        }
    return rows


def _verify_manifest_raw(rows: Mapping[str, Mapping[str, Any]], path: str, raw: bytes) -> None:
    row = rows.get(path)
    if row is None or row["byte_length"] != len(raw) or row["raw_sha256"] != hashlib.sha256(raw).hexdigest().upper():
        raise WaveformEvidenceError("波形证据与线路级 manifest 不一致")


def _read_view_arrays(
    reader: EvidenceReader,
    prefix: str,
    rows: Mapping[str, Mapping[str, Any]],
    manifest_rows: Mapping[str, Mapping[str, Any]],
    view: str,
) -> dict[str, dict[str, np.ndarray]]:
    names: set[str]
    if view == "awg":
        names = {name for name in rows if name.startswith("awg/")}
        names.add("effective/time_center_ns")
    else:
        names = {name for name in rows if name.startswith(view + "/")}
    if not names or not names.issubset(rows):
        raise WaveformEvidenceError("所选波形域缺少数组")
    if sum(int(rows[name]["byte_length"]) for name in names) > _MAX_VIEW_BYTES:
        raise WaveformEvidenceError("所选波形域超过 Web 总读取上限")
    result: dict[str, dict[str, np.ndarray]] = {}
    for name in sorted(names):
        row = rows[name]
        raw = reader.read_bytes(prefix + str(row["path"]), maximum_bytes=_MAX_ARRAY_BYTES)
        if len(raw) != row["byte_length"] or hashlib.sha256(raw).hexdigest().upper() != row["sha256"]:
            raise WaveformEvidenceError("波形数组与清单哈希不一致")
        _verify_manifest_raw(manifest_rows, "stage41/control/" + str(row["path"]), raw)
        value = np.frombuffer(raw, dtype=np.dtype(row["dtype"]))
        if value.size != row["shape"][0] or (value.dtype.kind == "f" and not np.all(np.isfinite(value))):
            raise WaveformEvidenceError("波形数组内容无效")
        domain, key = name.split("/", 1)
        result.setdefault(domain, {})[key] = value
    return result


def _plot_specs(
    circuit_id: str,
    view: str,
    arrays: Mapping[str, Mapping[str, np.ndarray]],
    max_points: int,
) -> list[dict[str, Any]]:
    if view == "awg":
        return _awg_specs(circuit_id, arrays, max_points)
    domain = arrays[view]
    time = _array(domain, "time_center_ns")
    xy = _coordinate_spec(
        circuit_id, view, "xy", time, domain,
        (("q1", "Q1", ("q1_i", "q1_q")), ("q2", "Q2", ("q2_i", "q2_q"))),
        (("i", "I", "GHz"), ("q", "Q", "GHz")), max_points,
    )
    flux = _coordinate_spec(
        circuit_id, view, "flux", time, domain,
        (("q1", "Q1", ("q1_flux_delta", "q1_flux_absolute")), ("q2", "Q2", ("q2_flux_delta", "q2_flux_absolute")), ("c", "C", ("c_flux_delta", "c_flux_absolute"))),
        (("delta", "相对空闲点", "Phi/Phi0"), ("absolute", "绝对磁通", "Phi/Phi0")), max_points,
    )
    titles = {
        "effective": ("QuTiP 有效 XY 控制", "QuTiP 有效磁通控制"),
        "logical": ("QCIS 编译逻辑 XY 波形", "QCIS 编译逻辑磁通波形"),
    }
    xy["title"], flux["title"] = titles[view]
    return [xy, flux]


def _awg_specs(circuit_id: str, arrays: Mapping[str, Mapping[str, np.ndarray]], max_points: int) -> list[dict[str, Any]]:
    awg = arrays["awg"]
    requested_time = _array(awg, "time_center_ns")
    delivered_time = _array(arrays["effective"], "time_center_ns")
    lanes = sorted({name.split("/", 1)[0] for name in awg if "/" in name})
    if not lanes:
        raise WaveformEvidenceError("AWG 通道清单为空")
    voltage_series = []
    code_series = []
    active: dict[str, bool] = {}
    for lane in lanes:
        delivered = _array(awg, f"{lane}/delivered_voltage")
        active[lane] = bool(np.any(delivered != 0.0))
        for metric, time in (("requested_voltage", requested_time), ("reconstructed_voltage", requested_time), ("delivered_voltage", delivered_time)):
            values = _array(awg, f"{lane}/{metric}")
            voltage_series.append(_series(circuit_id, "awg_voltage", lane, metric, time, values))
        codes = _array(awg, f"{lane}/dac_codes")
        code_series.append(_series(circuit_id, "awg_dac", lane, "dac_codes", requested_time, codes))
    if not any(active.values()):
        active[lanes[0]] = True
    objects = [{"id": lane, "label": _lane_label(lane), "default_visible": active[lane]} for lane in lanes]
    voltage = _base_spec(
        "waveform_awg_voltage", "真实 AWG 通道输出", objects,
        [
            {"id": "requested_voltage", "label": "请求电压", "default_visible": False},
            {"id": "reconstructed_voltage", "label": "DAC 重建电压", "default_visible": False},
            {"id": "delivered_voltage", "label": "实际输出电压", "default_visible": True},
        ], "电压", "V", voltage_series, max_points,
    )
    codes = _base_spec(
        "waveform_awg_dac", "物理通道 DAC 码", objects,
        [{"id": "dac_codes", "label": "DAC code", "default_visible": True}],
        "DAC code", "", code_series, max_points,
    )
    return [voltage, codes]


def _coordinate_spec(
    circuit_id: str,
    view: str,
    kind: str,
    time: np.ndarray,
    domain: Mapping[str, np.ndarray],
    objects: tuple[tuple[str, str, tuple[str, ...]], ...],
    metrics: tuple[tuple[str, str, str], ...],
    max_points: int,
) -> dict[str, Any]:
    object_rows = []
    series = []
    for object_id, label, names in objects:
        present = [name for name in names if name in domain]
        if not present:
            continue
        is_active = any(bool(np.any(_array(domain, name) != 0.0)) for name in present if not name.endswith("_absolute"))
        object_rows.append({"id": object_id, "label": label, "default_visible": is_active})
        for name, (metric_id, _metric_label, _unit) in zip(names, metrics, strict=True):
            if name in domain:
                series.append(_series(circuit_id, f"{view}_{kind}", object_id, metric_id, time, _array(domain, name)))
    if object_rows and not any(row["default_visible"] for row in object_rows):
        object_rows[0]["default_visible"] = True
    metric_rows = [
        {"id": metric_id, "label": label, "default_visible": index == 0}
        for index, (metric_id, label, _unit) in enumerate(metrics)
    ]
    return _base_spec(
        f"waveform_{view}_{kind}", "", object_rows, metric_rows,
        kind.upper(), metrics[0][2], series, max_points,
    )


def _base_spec(
    plot_id: str,
    title: str,
    objects: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    y_label: str,
    y_unit: str,
    series: list[dict[str, Any]],
    max_points: int,
) -> dict[str, Any]:
    try:
        envelope = build_min_max_envelope(series, max_points)
    except PlotSpecError as exc:
        raise WaveformEvidenceError("波形降采样失败") from exc
    return {
        "schema_version": "1.0",
        "plot_id": plot_id,
        "plot_type": "line",
        "title": title,
        "objects": objects,
        "metrics": metrics,
        "groups": [{"id": "waveform", "label": "波形"}],
        "axes": {
            "x": {"label": "时间", "unit": "ns"},
            "y": {"label": y_label, "unit": y_unit, "zero_baseline": False},
        },
        "series": envelope["series"],
        "data_descriptor": {
            "format": "min_max_envelope_v1",
            "max_points_per_series": max_points,
        },
    }


def _series(circuit_id: str, plot: str, object_id: str, metric_id: str, time: np.ndarray, values: np.ndarray) -> dict[str, Any]:
    if time.size != values.size:
        raise WaveformEvidenceError("波形时间轴与数据长度不一致")
    points = [
        {
            "id": f"wf:{circuit_id}:{plot}:{object_id}:{metric_id}:{index}",
            "x": float(x),
            "y": float(y),
            "metadata": {"sample_index": index},
        }
        for index, (x, y) in enumerate(zip(time, values, strict=True))
    ]
    return {
        "id": f"{plot}:{object_id}:{metric_id}",
        "object_id": object_id,
        "metric_id": metric_id,
        "group_id": "waveform",
        "points": points,
    }


def _array(domain: Mapping[str, np.ndarray], name: str) -> np.ndarray:
    value = domain.get(name)
    if not isinstance(value, np.ndarray) or value.ndim != 1 or not value.size:
        raise WaveformEvidenceError(f"波形数组 {name} 缺失")
    return value


def _json(reader: EvidenceReader, path: str) -> Mapping[str, Any]:
    return _decode_json(reader.read_bytes(path, maximum_bytes=_MAX_JSON_BYTES), path)


def _decode_json(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            object_pairs_hook=_unique_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WaveformEvidenceError(f"{label} 不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise WaveformEvidenceError(f"{label} 必须是 JSON 对象")
    return value


def _unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _point_label(index: int, coordinates: Mapping[str, float], unit: str | None) -> str:
    suffix = ", ".join(f"{key}={value:.9g}{(' ' + unit) if unit else ''}" for key, value in coordinates.items())
    return f"#{index} · {suffix}" if suffix else f"#{index}"


def _lane_label(lane: str) -> str:
    return lane.upper().replace("_XY_I", " XY-I").replace("_XY_Q", " XY-Q").replace("_Z", " Z")


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise WaveformEvidenceError(f"{label}必须是有限数")
    return float(value)


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise WaveformEvidenceError(f"{label} 无效")
    result = value.upper()
    if len(result) != 64 or any(character not in _HEX for character in result):
        raise WaveformEvidenceError(f"{label} 无效")
    return result


def _identifier(value: str, label: str) -> None:
    if not value or len(value) > 128 or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for character in value):
        raise WaveformEvidenceError(f"{label} 不是安全标识符", status=400)


def _safe_relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise WaveformEvidenceError("波形证据路径无效")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise WaveformEvidenceError("波形证据路径无效")


__all__ = [
    "ArchiveEvidenceReader",
    "DirectoryEvidenceReader",
    "WaveformEvidenceError",
    "waveform_catalog",
    "waveform_point",
]
