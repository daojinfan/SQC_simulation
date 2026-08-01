"""Validated plot specifications consumed by the calibration Web console."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


PLOT_SPEC_SCHEMA_VERSION = "1.0"
MAX_LOD_OUTPUT_POINTS = 10_000
MAX_LOD_SOURCE_POINTS = 1_000_000
MAX_LOD_SERIES = 256
_XY_PLOT_TYPES = {"line", "scatter"}
_PLOT_TYPES = {*_XY_PLOT_TYPES, "heatmap"}


class PlotSpecError(ValueError):
    """Raised when a Web plot specification is incomplete or inconsistent."""


def min_max_envelope(
    series: Sequence[Mapping[str, Any]],
    max_points: int,
    *,
    x_min: float | None = None,
    x_max: float | None = None,
) -> dict[str, Any]:
    """Return a deterministic, source-addressable min/max envelope.

    Series retain their original order. All series share the same x bucket
    boundaries so aligned metrics remain visually comparable.
    """

    if (
        isinstance(max_points, bool)
        or not isinstance(max_points, int)
        or max_points < 2
        or max_points > MAX_LOD_OUTPUT_POINTS
    ):
        raise PlotSpecError(
            f"max_points must be between 2 and {MAX_LOD_OUTPUT_POINTS}"
        )
    normalized = _validated_lod_series(series)
    all_x = [point["x"] for row in normalized for point in row["points"]]
    resolved_min = _optional_finite(x_min, "x_min")
    resolved_max = _optional_finite(x_max, "x_max")
    if resolved_min is None:
        resolved_min = min(all_x) if all_x else 0.0
    if resolved_max is None:
        resolved_max = max(all_x) if all_x else resolved_min
    if resolved_min > resolved_max:
        raise PlotSpecError("x_min must not exceed x_max")
    if not math.isfinite(resolved_max - resolved_min):
        raise PlotSpecError("LOD x range is too large")

    bucket_count = max(0, (max_points - 2) // 2)
    edges = _bucket_edges(resolved_min, resolved_max, bucket_count)
    output_series = []
    for row in normalized:
        visible = [
            point
            for point in row["points"]
            if resolved_min <= point["x"] <= resolved_max
        ]
        selected: dict[int, dict[str, Any]] = {}
        if visible:
            for endpoint in (visible[0], visible[-1]):
                selected[endpoint["source_index"]] = _envelope_point(
                    endpoint,
                    _bucket_for_x(
                        endpoint["x"], resolved_min, resolved_max, bucket_count
                    ),
                    False,
                )
        if bucket_count:
            buckets: list[list[dict[str, Any]]] = [
                [] for _unused in range(bucket_count)
            ]
            for point in visible:
                bucket = _bucket_for_x(
                    point["x"], resolved_min, resolved_max, bucket_count
                )
                assert bucket is not None
                buckets[bucket].append(point)
            for bucket, points in enumerate(buckets):
                if not points:
                    continue
                minimum = min(points, key=lambda point: (point["y"], point["source_index"]))
                maximum = max(points, key=lambda point: (point["y"], -point["source_index"]))
                aggregate = len(points) > len(
                    {minimum["source_index"], maximum["source_index"]}
                )
                for point in sorted(
                    {minimum["source_index"]: minimum, maximum["source_index"]: maximum}.values(),
                    key=lambda point: point["source_index"],
                ):
                    selected.setdefault(
                        point["source_index"],
                        _envelope_point(point, bucket, aggregate),
                    )
        envelope = [selected[index] for index in sorted(selected)]
        if len(envelope) > max_points:
            raise AssertionError("min/max envelope exceeded max_points")
        output_series.append({**row["source"], "points": envelope})
    return {
        "method": "min_max_envelope_v1",
        "max_points": max_points,
        "x_min": resolved_min,
        "x_max": resolved_max,
        "bucket_edges": edges,
        "series": output_series,
    }


def build_min_max_envelope(
    series: Sequence[Mapping[str, Any]],
    max_points: int,
    *,
    x_min: float | None = None,
    x_max: float | None = None,
) -> dict[str, Any]:
    """Compatibility name for callers that use build-style plot adapters."""

    return min_max_envelope(series, max_points, x_min=x_min, x_max=x_max)


def lookup_source_point(
    series: Sequence[Mapping[str, Any]],
    point_id: str | None = None,
    *,
    series_id: str | None = None,
    source_index: int | None = None,
) -> dict[str, Any]:
    """Return the exact source point addressed by point ID or series index."""

    if point_id is not None and (not isinstance(point_id, str) or not point_id):
        raise PlotSpecError("point_id must be a non-empty string")
    if series_id is not None and (not isinstance(series_id, str) or not series_id):
        raise PlotSpecError("series_id must be a non-empty string")
    if source_index is not None and (
        isinstance(source_index, bool)
        or not isinstance(source_index, int)
        or source_index < 0
    ):
        raise PlotSpecError("source_index must be a non-negative integer")
    if point_id is None and source_index is None:
        raise PlotSpecError("point_id or source_index is required")
    if source_index is not None and series_id is None:
        raise PlotSpecError("series_id is required with source_index")
    normalized = _validated_lod_series(series)
    for row in normalized:
        if series_id is not None and row["id"] != series_id:
            continue
        for point in row["points"]:
            if point_id is not None and point["point_id"] != point_id:
                continue
            if source_index is not None and point["source_index"] != source_index:
                continue
            if point_id is not None or source_index is not None:
                return {
                    "series_id": row["id"],
                    "source_index": point["source_index"],
                    "point_id": point["point_id"],
                    "x": point["x"],
                    "y": point["y"],
                    "point": dict(point["source"]),
                }
    raise PlotSpecError("source point was not found")


def _validated_lod_series(
    series: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(series, (str, bytes)) or not isinstance(series, Sequence):
        raise PlotSpecError("LOD series must be a sequence")
    if len(series) > MAX_LOD_SERIES:
        raise PlotSpecError(f"LOD series exceed the limit of {MAX_LOD_SERIES}")
    seen_series: set[str] = set()
    seen_points: set[str] = set()
    total_points = 0
    result = []
    for raw_series in series:
        row = _mapping(raw_series, "LOD series")
        series_id = row.get("id")
        if (
            not isinstance(series_id, str)
            or not series_id
            or series_id in seen_series
        ):
            raise PlotSpecError("LOD series IDs must be non-empty and unique")
        seen_series.add(series_id)
        raw_points = row.get("points")
        if not isinstance(raw_points, list):
            raise PlotSpecError("LOD series points are invalid")
        normalized_points = []
        previous_x: float | None = None
        for source_index, raw_point in enumerate(raw_points):
            total_points += 1
            if total_points > MAX_LOD_SOURCE_POINTS:
                raise PlotSpecError(
                    f"LOD source points exceed the limit of {MAX_LOD_SOURCE_POINTS}"
                )
            point = _mapping(raw_point, "LOD point")
            point_id = point.get("point_id", point.get("id"))
            if (
                not isinstance(point_id, str)
                or not point_id
                or point_id in seen_points
            ):
                raise PlotSpecError("LOD point IDs must be non-empty and globally unique")
            seen_points.add(point_id)
            x_value = _finite(point.get("x"), "LOD point x")
            y_value = _finite(point.get("y"), "LOD point y")
            if previous_x is not None and x_value < previous_x:
                raise PlotSpecError("LOD point x values must be monotonic")
            previous_x = x_value
            normalized_points.append(
                {
                    "point_id": point_id,
                    "source_index": source_index,
                    "x": x_value,
                    "y": y_value,
                    "source": point,
                }
            )
        result.append({"id": series_id, "points": normalized_points, "source": dict(row)})
    return result


def _bucket_edges(x_min: float, x_max: float, count: int) -> list[float]:
    if count <= 0:
        return []
    if x_min == x_max:
        return [x_min for _unused in range(count + 1)]
    span = x_max - x_min
    if not math.isfinite(span):
        raise PlotSpecError("LOD x range is too large")
    width = span / count
    return [x_min + width * index for index in range(count)] + [x_max]


def _bucket_for_x(
    x_value: float,
    x_min: float,
    x_max: float,
    count: int,
) -> int | None:
    if count <= 0:
        return None
    if x_min == x_max:
        return 0
    span = x_max - x_min
    if not math.isfinite(span):
        raise PlotSpecError("LOD x range is too large")
    return min(count - 1, int((x_value - x_min) / span * count))


def _envelope_point(
    point: Mapping[str, Any], bucket: int | None, is_aggregate: bool
) -> dict[str, Any]:
    return {
        "x": point["x"],
        "y": point["y"],
        "source_index": point["source_index"],
        "point_id": point["point_id"],
        "bucket": bucket,
        "is_aggregate": is_aggregate,
    }


def _optional_finite(value: Any, label: str) -> float | None:
    return None if value is None else _finite(value, label)


def build_spectroscopy_plot_spec(
    targets: Sequence[str],
    datasets: Mapping[str, Any],
) -> dict[str, Any]:
    """Adapt spectroscopy artifacts to the experiment-independent plot protocol."""

    object_rows = [
        {"id": target, "label": target, "default_visible": True}
        for target in targets
    ]
    metric_rows = [
        {"id": "P0", "label": "P0", "unit": "", "default_visible": False},
        {"id": "P1", "label": "P1", "unit": "", "default_visible": True},
        {
            "id": "leakage",
            "label": "Leakage",
            "unit": "",
            "default_visible": False,
        },
    ]
    single_scan = datasets.get("scan")
    groups = (
        [{"id": "scan", "label": "扫描"}]
        if single_scan is not None
        else [
            {"id": "coarse", "label": "粗扫"},
            {"id": "refined", "label": "细扫"},
            {"id": "confirmation", "label": "确认扫描"},
        ]
    )
    series = []
    for target in targets:
        phase_datasets = (
            (("scan", single_scan),)
            if single_scan is not None
            else (
                ("coarse", datasets.get("coarse")),
                ("refined", datasets.get("refined")),
                (
                    "confirmation",
                    _mapping(
                        datasets.get("confirmations"),
                        "confirmation datasets",
                    ).get(target),
                ),
            )
        )
        for phase, dataset in phase_datasets:
            if dataset is None:
                continue
            points = _mapping(dataset, f"{phase} dataset").get("points")
            if not isinstance(points, list):
                raise PlotSpecError(f"{phase} dataset points are invalid")
            for metric in ("P0", "P1", "leakage"):
                plot_points = []
                for point_row in points:
                    point = _mapping(point_row, f"{phase} point")
                    coordinate = _mapping(point.get("point"), "spectroscopy coordinate")
                    coordinates = _mapping(
                        coordinate.get("coordinates_GHz"),
                        "spectroscopy coordinates",
                    )
                    x_value = _finite(coordinates.get(target), f"{target} frequency")
                    y_value = _spectroscopy_metric(point, target, metric)
                    point_index = coordinate.get("point_index")
                    plot_points.append(
                        {
                            "id": f"{phase}:{target}:{point_index}:{metric}",
                            "x": x_value,
                            "y": y_value,
                            "metadata": {
                                "phase": phase,
                                "point_index": point_index,
                                "circuit_id": coordinate.get("circuit_id"),
                                "leakage": point.get("leakage"),
                                "norm_error": point.get("norm_error"),
                            },
                        }
                    )
                series.append(
                    {
                        "id": f"{phase}:{target}:{metric}",
                        "object_id": target,
                        "metric_id": metric,
                        "group_id": phase,
                        "points": plot_points,
                    }
                )

    spec = {
        "schema_version": PLOT_SPEC_SCHEMA_VERSION,
        "plot_id": "qubit_spectroscopy",
        "plot_type": "line",
        "title": "比特频谱",
        "objects": object_rows,
        "metrics": metric_rows,
        "groups": groups,
        "axes": {
            "x": {"label": "驱动频率", "unit": "GHz"},
            "y": {"label": "布居 / 泄漏", "unit": "", "zero_baseline": True},
        },
        "series": series,
        "interactions": {
            "object_filter": True,
            "metric_filter": True,
            "point_selection": True,
            "coordinate_readout": True,
        },
    }
    validate_plot_spec(spec)
    return spec


def build_rabi_amplitude_plot_spec(
    target: str,
    dataset: Mapping[str, Any],
    *,
    candidate_amplitude_GHz: float | None = None,
    fit_curve: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Adapt the published Rabi dataset without traversing execution evidence."""

    if not isinstance(target, str) or not target:
        raise PlotSpecError("Rabi target is required")
    axis = _mapping(dataset.get("axis"), "Rabi axis")
    if axis.get("name") != "amplitude_GHz" or axis.get("unit") != "GHz":
        raise PlotSpecError("Rabi axis must be amplitude_GHz in GHz")
    amplitudes = axis.get("values")
    if not isinstance(amplitudes, list) or not amplitudes:
        raise PlotSpecError("Rabi amplitude values are required")
    xs = [_finite(value, "Rabi amplitude") for value in amplitudes]
    if any(right <= left for left, right in zip(xs, xs[1:])):
        raise PlotSpecError("Rabi amplitudes must be strictly increasing")
    series_by_target = _mapping(dataset.get("series"), "Rabi series")
    values = _mapping(series_by_target.get(target), f"Rabi {target} series")

    metric_rows = [
        {"id": "P0", "label": "P0", "unit": "", "default_visible": False},
        {"id": "P1", "label": "P1", "unit": "", "default_visible": True},
        {"id": "leakage", "label": "Leakage", "unit": "", "default_visible": False},
        {"id": "norm_error", "label": "Norm error", "unit": "", "default_visible": False},
        {"id": "P1_fit", "label": "P1 fit", "unit": "", "default_visible": True},
    ]
    series = []
    for metric in ("P0", "P1", "leakage", "norm_error"):
        ys = values.get(metric)
        if not isinstance(ys, list) or len(ys) != len(xs):
            raise PlotSpecError(f"Rabi {metric} length must match amplitude axis")
        series.append(
            {
                "id": f"raw:{target}:{metric}",
                "object_id": target,
                "metric_id": metric,
                "group_id": "raw",
                "points": [
                    {
                        "id": f"raw:{target}:{metric}:{index}",
                        "x": x_value,
                        "y": _finite(y_value, f"Rabi {metric}"),
                        "metadata": {"point_index": index, "metric": metric},
                    }
                    for index, (x_value, y_value) in enumerate(zip(xs, ys))
                ],
            }
        )
    fit_xs = xs
    fit = values.get("P1_fit")
    if fit_curve is not None:
        fit_xs = fit_curve.get("amplitude_GHz")
        fit = fit_curve.get("P1", fit_curve.get("P1_fit"))
        if not isinstance(fit_xs, list) or not isinstance(fit, list):
            raise PlotSpecError("Rabi fit curve is invalid")
        fit_xs = [_finite(value, "Rabi fit amplitude") for value in fit_xs]
    if fit is not None:
        if not isinstance(fit, list) or len(fit) != len(fit_xs):
            raise PlotSpecError("Rabi P1_fit length must match its amplitude axis")
        series.append(
            {
                "id": f"fit:{target}:P1_fit",
                "object_id": target,
                "metric_id": "P1_fit",
                "group_id": "fit",
                "points": [
                    {
                        "id": f"fit:{target}:P1:{index}",
                        "x": x_value,
                        "y": _finite(y_value, "Rabi P1_fit"),
                        "metadata": {"point_index": index, "metric": "P1_fit"},
                    }
                    for index, (x_value, y_value) in enumerate(zip(fit_xs, fit))
                ],
            }
        )
    markers = []
    if candidate_amplitude_GHz is not None:
        markers.append(
            {
                "id": "candidate_amplitude",
                "label": "Candidate X2P amplitude",
                "x": _finite(candidate_amplitude_GHz, "Rabi candidate amplitude"),
            }
        )
    spec = {
        "schema_version": PLOT_SPEC_SCHEMA_VERSION,
        "plot_id": "qubit_rabi_x2p_amplitude",
        "plot_type": "line",
        "title": "X2P Rabi amplitude scan",
        "objects": [{"id": target, "label": target, "default_visible": True}],
        "metrics": metric_rows,
        "groups": [{"id": "raw", "label": "Measured"}, {"id": "fit", "label": "Fit"}],
        "axes": {
            "x": {"label": "Drive amplitude", "unit": "GHz"},
            "y": {"label": "Probability", "unit": "", "zero_baseline": True},
        },
        "series": series,
        "markers": markers,
        "interactions": {
            "object_filter": True,
            "metric_filter": True,
            "point_selection": True,
            "coordinate_readout": True,
        },
    }
    validate_plot_spec(spec)
    return spec


def validate_plot_spec(spec: Mapping[str, Any]) -> None:
    """Fail closed on malformed line, scatter, or heatmap plot specifications."""

    if spec.get("schema_version") != PLOT_SPEC_SCHEMA_VERSION:
        raise PlotSpecError("plot schema_version is unsupported")
    plot_id = spec.get("plot_id")
    if not isinstance(plot_id, str) or not plot_id:
        raise PlotSpecError("plot_id is required")
    plot_type = spec.get("plot_type")
    if plot_type not in _PLOT_TYPES:
        raise PlotSpecError("plot_type must be line, scatter, or heatmap")
    object_ids = _option_ids(spec.get("objects"), "objects")
    metric_ids = _option_ids(spec.get("metrics"), "metrics")
    axes = _mapping(spec.get("axes"), "axes")
    for axis_id in ("x", "y"):
        axis = _mapping(axes.get(axis_id), f"{axis_id} axis")
        if not isinstance(axis.get("label"), str) or not axis["label"]:
            raise PlotSpecError(f"{axis_id} axis label is required")
    markers = spec.get("markers", [])
    if not isinstance(markers, list):
        raise PlotSpecError("plot markers are invalid")
    for marker in markers:
        row = _mapping(marker, "plot marker")
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise PlotSpecError("plot marker ID is required")
        _finite(row.get("x"), "plot marker x")

    if plot_type in _XY_PLOT_TYPES:
        series = spec.get("series")
        if _has_external_plot_data(spec) and (series is None or series == []):
            return
        if not isinstance(series, list) or not series:
            raise PlotSpecError("line/scatter plots require series")
        _validate_series(series, object_ids, metric_ids)
        return

    layers = spec.get("layers")
    if _has_external_plot_data(spec) and (layers is None or layers == []):
        return
    if not isinstance(layers, list) or not layers:
        raise PlotSpecError("heatmap plots require layers")
    for layer in layers:
        row = _mapping(layer, "heatmap layer")
        _validate_reference(row, object_ids, metric_ids, "heatmap layer")
        cells = row.get("cells")
        if not isinstance(cells, list) or not cells:
            raise PlotSpecError("heatmap layer cells are required")
        for cell in cells:
            value = _mapping(cell, "heatmap cell")
            _finite(value.get("x"), "heatmap x")
            _finite(value.get("y"), "heatmap y")
            _finite(value.get("value"), "heatmap value")


def _has_external_plot_data(spec: Mapping[str, Any]) -> bool:
    data_url = spec.get("data_url")
    descriptor = spec.get("data_descriptor", spec.get("descriptor"))
    if data_url is None and descriptor is None:
        return False
    if (
        not isinstance(data_url, str)
        or not data_url.startswith("/api/v1/")
        or "\\" in data_url
        or ".." in data_url.split("?")[0].split("/")
    ):
        raise PlotSpecError("plot data_url must be an API-relative path")
    value = _mapping(descriptor, "plot data descriptor")
    data_format = value.get("format")
    if not isinstance(data_format, str) or not data_format:
        raise PlotSpecError("plot data descriptor format is required")
    return True


def _validate_series(
    series: list[Any],
    object_ids: set[str],
    metric_ids: set[str],
) -> None:
    seen = set()
    for item in series:
        row = _mapping(item, "plot series")
        series_id = row.get("id")
        if not isinstance(series_id, str) or not series_id or series_id in seen:
            raise PlotSpecError("plot series IDs must be non-empty and unique")
        seen.add(series_id)
        _validate_reference(row, object_ids, metric_ids, "plot series")
        points = row.get("points")
        if not isinstance(points, list):
            raise PlotSpecError("plot series points are invalid")
        for point in points:
            value = _mapping(point, "plot point")
            _finite(value.get("x"), "plot point x")
            _finite(value.get("y"), "plot point y")


def _validate_reference(
    row: Mapping[str, Any],
    object_ids: set[str],
    metric_ids: set[str],
    label: str,
) -> None:
    if row.get("object_id") not in object_ids:
        raise PlotSpecError(f"{label} references an unknown object")
    if row.get("metric_id") not in metric_ids:
        raise PlotSpecError(f"{label} references an unknown metric")


def _option_ids(value: Any, label: str) -> set[str]:
    if not isinstance(value, list) or not value:
        raise PlotSpecError(f"plot {label} are required")
    identifiers = []
    for item in value:
        row = _mapping(item, f"plot {label} option")
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise PlotSpecError(f"plot {label} IDs are required")
        identifiers.append(identifier)
    if len(set(identifiers)) != len(identifiers):
        raise PlotSpecError(f"plot {label} IDs must be unique")
    return set(identifiers)


def _spectroscopy_metric(point: Mapping[str, Any], target: str, metric: str) -> float:
    if metric == "leakage":
        return _finite(point.get("leakage"), "spectroscopy leakage")
    primitive = _mapping(
        point.get("primitive_dressed_populations"),
        "primitive dressed populations",
    )
    values = {
        key: _finite(primitive.get(key), key)
        for key in ("population_000", "population_100", "population_001", "population_101")
    }
    if target == "Q1":
        keys = ("population_000", "population_001") if metric == "P0" else (
            "population_100",
            "population_101",
        )
    elif target == "Q2":
        keys = ("population_000", "population_100") if metric == "P0" else (
            "population_001",
            "population_101",
        )
    else:
        raise PlotSpecError(f"spectroscopy population mapping is undefined for {target}")
    return values[keys[0]] + values[keys[1]]


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlotSpecError(f"{label} are invalid")
    return value


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlotSpecError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise PlotSpecError(f"{label} must be finite")
    return result


__all__ = [
    "MAX_LOD_OUTPUT_POINTS",
    "MAX_LOD_SOURCE_POINTS",
    "MAX_LOD_SERIES",
    "PLOT_SPEC_SCHEMA_VERSION",
    "PlotSpecError",
    "build_min_max_envelope",
    "build_rabi_amplitude_plot_spec",
    "build_spectroscopy_plot_spec",
    "lookup_source_point",
    "min_max_envelope",
    "validate_plot_spec",
]
