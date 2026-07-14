"""Pure deterministic scan expansion and point identity."""

from __future__ import annotations

import hashlib
import itertools
import json
from typing import Any

from sqvm.runtime.models import ExperimentRequest, ScanPoint


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _seed_payload(request: ExperimentRequest, point_index: int, repetition: int, coordinates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": request.schema_version,
        "experiment_id": request.experiment_id,
        "point_index": point_index,
        "repetition": repetition,
        "coordinates": coordinates,
    }


def _derive_seed(run_seed: int, payload: dict[str, Any]) -> int:
    digest = hashlib.sha256(str(run_seed).encode("ascii") + b"\0" + canonical_json_bytes(payload)).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def expand_scan(request: ExperimentRequest) -> tuple[ScanPoint, ...]:
    """Expand explicit axes in YAML order, with the last axis varying fastest."""

    combinations = tuple(itertools.product(*(axis.values for axis in request.axes)))
    points: list[ScanPoint] = []
    for repetition in range(request.repetitions):
        for values in combinations:
            point_index = len(points)
            coordinates = [
                {"axis": axis.name, "value": value, "unit": axis.unit}
                for axis, value in zip(request.axes, values, strict=True)
            ]
            seed_payload = _seed_payload(request, point_index, repetition, coordinates)
            seed = _derive_seed(request.execution.seed, seed_payload)
            payload = dict(seed_payload)
            payload["seed"] = seed
            point_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest().upper()
            points.append(
                ScanPoint(
                    request.schema_version,
                    request.experiment_id,
                    point_index,
                    repetition,
                    tuple((item["axis"], item["value"], item["unit"]) for item in coordinates),
                    seed,
                    point_id,
                )
            )
    if len(points) > request.execution.max_points or len(points) > 10_000:
        raise ValueError("expanded point count exceeds configured limit")
    return tuple(points)


def point_table_payload(request: ExperimentRequest) -> dict[str, Any]:
    return {
        "schema_version": request.schema_version,
        "experiment_id": request.experiment_id,
        "points": [point.payload(include_point_id=True) for point in expand_scan(request)],
    }
