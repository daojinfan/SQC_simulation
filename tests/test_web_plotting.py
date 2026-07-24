from __future__ import annotations

import pytest as _pytest

pytestmark = _pytest.mark.integration

import copy
import math

import pytest

from sqvm.web.plotting import (
    MAX_LOD_OUTPUT_POINTS,
    PlotSpecError,
    build_spectroscopy_plot_spec,
    lookup_source_point,
    min_max_envelope,
    validate_plot_spec,
)


def _point(index: int = 0) -> dict:
    return {
        "point": {
            "point_index": index,
            "coordinates_GHz": {"Q1": 5.1 + index * 0.01, "Q2": 5.3 + index * 0.01},
            "circuit_id": f"circuit-{index}",
        },
        "primitive_dressed_populations": {
            "population_000": 0.55,
            "population_100": 0.15,
            "population_001": 0.10,
            "population_101": 0.10,
        },
        "leakage": 0.10,
        "norm_error": 1.0e-12,
    }


def _datasets() -> dict:
    return {
        "coarse": {"points": [_point(0), _point(1)]},
        "refined": {"points": [_point(2)]},
        "confirmations": {
            "Q1": {"points": [_point(3)]},
            "Q2": {"points": [_point(4)]},
        },
    }


def test_spectroscopy_adapter_exposes_object_metric_and_point_dimensions():
    spec = build_spectroscopy_plot_spec(["Q1", "Q2"], _datasets())

    assert spec["plot_type"] == "line"
    assert [row["id"] for row in spec["objects"]] == ["Q1", "Q2"]
    assert [row["id"] for row in spec["metrics"]] == ["P0", "P1", "leakage"]
    assert [row["id"] for row in spec["metrics"] if row["default_visible"]] == ["P1"]
    assert len(spec["series"]) == 18

    by_id = {row["id"]: row for row in spec["series"]}
    assert by_id["coarse:Q1:P0"]["points"][0]["y"] == pytest.approx(0.65)
    assert by_id["coarse:Q1:P1"]["points"][0]["y"] == pytest.approx(0.25)
    assert by_id["coarse:Q2:P0"]["points"][0]["y"] == pytest.approx(0.70)
    assert by_id["coarse:Q2:P1"]["points"][0]["y"] == pytest.approx(0.20)
    assert by_id["coarse:Q2:leakage"]["points"][0]["y"] == pytest.approx(0.10)


def test_spectroscopy_adapter_supports_one_unphased_scan_dataset():
    spec = build_spectroscopy_plot_spec(
        ["Q1", "Q2"],
        {"scan": {"points": [_point(0), _point(1)]}},
    )

    assert spec["groups"] == [{"id": "scan", "label": "扫描"}]
    assert len(spec["series"]) == 6
    assert {row["group_id"] for row in spec["series"]} == {"scan"}


def test_plot_protocol_accepts_selectable_heatmap_cells():
    spec = {
        "schema_version": "1.0",
        "plot_id": "rabi_2d",
        "plot_type": "heatmap",
        "title": "二维 Rabi",
        "objects": [{"id": "Q1", "label": "Q1", "default_visible": True}],
        "metrics": [{"id": "P1", "label": "P1", "default_visible": True}],
        "axes": {
            "x": {"label": "幅度", "unit": "GHz"},
            "y": {"label": "时长", "unit": "ns"},
        },
        "layers": [{
            "id": "Q1:P1",
            "object_id": "Q1",
            "metric_id": "P1",
            "cells": [
                {"id": "p0", "x": 0.01, "y": 10.0, "value": 0.2},
                {"id": "p1", "x": 0.02, "y": 10.0, "value": 0.4},
                {"id": "p2", "x": 0.01, "y": 20.0, "value": 0.6},
                {"id": "p3", "x": 0.02, "y": 20.0, "value": 0.8},
            ],
        }],
    }

    validate_plot_spec(spec)

    invalid_reference = copy.deepcopy(spec)
    invalid_reference["layers"][0]["object_id"] = "C"
    with pytest.raises(PlotSpecError, match="unknown object"):
        validate_plot_spec(invalid_reference)

    nonfinite = copy.deepcopy(spec)
    nonfinite["layers"][0]["cells"][0]["value"] = math.nan
    with pytest.raises(PlotSpecError, match="finite"):
        validate_plot_spec(nonfinite)


def _lod_series(series_id: str, ys: list[float], *, offset: float = 0.0) -> dict:
    return {
        "id": series_id,
        "points": [
            {"id": f"{series_id}-p{index}", "x": offset + index, "y": value, "metadata": {"exact": index}}
            for index, value in enumerate(ys)
        ],
    }


def test_min_max_envelope_is_deterministic_bounded_and_source_addressable():
    source = [_lod_series("s1", [0, 5, -4, 2, 1, 3, 9, -8, 4, 0])]
    source[0]["metric_id"] = "P1"

    first = min_max_envelope(source, 6)
    second = min_max_envelope(source, 6)

    assert first == second
    assert first["method"] == "min_max_envelope_v1"
    assert first["series"][0]["metric_id"] == "P1"
    assert first["bucket_edges"] == pytest.approx([0.0, 4.5, 9.0])
    points = first["series"][0]["points"]
    assert len(points) <= 6
    assert [point["source_index"] for point in points] == [0, 1, 2, 6, 7, 9]
    assert [point["point_id"] for point in points] == [
        "s1-p0", "s1-p1", "s1-p2", "s1-p6", "s1-p7", "s1-p9",
    ]
    assert points[0]["is_aggregate"] is False
    assert points[-1]["is_aggregate"] is False
    assert all(set(point) == {"x", "y", "source_index", "point_id", "bucket", "is_aggregate"} for point in points)

    exact = lookup_source_point(source, "s1-p7")
    assert exact == {
        "series_id": "s1",
        "source_index": 7,
        "point_id": "s1-p7",
        "x": 7.0,
        "y": -8.0,
        "point": source[0]["points"][7],
    }


def test_min_max_envelope_uses_shared_buckets_and_preserves_view_endpoints():
    source = [
        _lod_series("low", [0, 4, -3, 2, 1, 6, -5, 3, 2, 0]),
        _lod_series("high", [1, -4, 7, 2, 9, 0, 6, -2, 8, 1]),
    ]

    envelope = min_max_envelope(source, 6, x_min=2, x_max=7)

    assert envelope["x_min"] == 2.0
    assert envelope["x_max"] == 7.0
    assert envelope["bucket_edges"] == pytest.approx([2.0, 4.5, 7.0])
    for row in envelope["series"]:
        assert len(row["points"]) <= 6
        assert row["points"][0]["source_index"] == 2
        assert row["points"][-1]["source_index"] == 7
        assert [point["source_index"] for point in row["points"]] == sorted(
            point["source_index"] for point in row["points"]
        )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ([_lod_series("s", [0, 1, math.nan])], "finite"),
        ({"not": "a sequence"}, "sequence"),
        ([{"id": "s", "points": [{"id": "p0", "x": 1, "y": 0}, {"id": "p1", "x": 0, "y": 1}]}], "monotonic"),
        ([{"id": "s1", "points": [{"id": "same", "x": 0, "y": 0}]}, {"id": "s2", "points": [{"id": "same", "x": 0, "y": 1}]}], "globally unique"),
    ],
)
def test_min_max_envelope_rejects_invalid_source(source, message):
    with pytest.raises(PlotSpecError, match=message):
        min_max_envelope(source, 10)


def test_min_max_envelope_rejects_invalid_limits_and_source_overflow(monkeypatch):
    with pytest.raises(PlotSpecError, match="max_points"):
        min_max_envelope([], 1)
    with pytest.raises(PlotSpecError, match="max_points"):
        min_max_envelope([], MAX_LOD_OUTPUT_POINTS + 1)
    with pytest.raises(PlotSpecError, match="range is too large"):
        min_max_envelope(
            [{"id": "s", "points": [{"id": "p0", "x": -1e308, "y": 0}, {"id": "p1", "x": 1e308, "y": 1}]}],
            2,
        )

    import sqvm.web.plotting as plotting

    monkeypatch.setattr(plotting, "MAX_LOD_SOURCE_POINTS", 2)
    with pytest.raises(PlotSpecError, match="source points exceed"):
        min_max_envelope([_lod_series("s", [0, 1, 2])], 10)


def test_lookup_source_point_rejects_missing_or_ambiguous_identifiers():
    source = [_lod_series("s", [0, 1])]
    assert lookup_source_point(source, series_id="s", source_index=1)["point_id"] == "s-p1"
    with pytest.raises(PlotSpecError, match="non-empty"):
        lookup_source_point(source, "")
    with pytest.raises(PlotSpecError, match="series_id is required"):
        lookup_source_point(source, source_index=0)
    with pytest.raises(PlotSpecError, match="not found"):
        lookup_source_point(source, "absent")


def test_inline_plot_spec_tolerates_future_descriptor_and_data_url_metadata():
    spec = build_spectroscopy_plot_spec(["Q1"], {"scan": {"points": [_point(0)]}})
    spec["data_descriptor"] = {
        "format": "min_max_envelope_v1",
        "point_lookup": "source_index",
    }
    spec["data_url"] = "/api/v1/experiments/run-1/plots/qubit_spectroscopy"

    validate_plot_spec(spec)
    assert spec["series"]

    external = copy.deepcopy(spec)
    external.pop("series")
    validate_plot_spec(external)

    unsafe = copy.deepcopy(external)
    unsafe["data_url"] = "/api/v1/../private/result.json"
    with pytest.raises(PlotSpecError, match="API-relative"):
        validate_plot_spec(unsafe)
