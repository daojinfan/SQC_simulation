"""Portable raw dataset encoding for the Stage 6 deterministic MVP."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import struct
from typing import Any, Iterable, Mapping

from sqvm.hamiltonian.provenance import canonical_json_bytes


CLAIM_ENVELOPE = {
    "evidence_class": "platform_test_fixture",
    "physics_claim": "none",
    "observation_model": "absent",
    "measurement_payload": None,
}
DATASET_KEYS = {"schema_version", "claim_envelope", "point_table_sha256", "dimensions", "variables"}
VARIABLE_KEYS = {"dimensions", "dtype", "shape", "order", "unit", "semantic_role", "byte_length", "raw_sha256"}
EXPECTED_RESPONSE = (-2.0, 0.0, 2.0, -1.5, 0.5, 2.5)
EXPECTED_RESPONSE_SHA256 = "01279CD8EFD786E0F4ED0EA714EE57AF7C82BE12D7259339A5998FF7C120E1A0"
ALLOWED_DTYPES = {"<f8", "<i8", "<u8", "<c16"}


def encode_numeric_values(values: Iterable[float | int | complex], dtype: str) -> bytes:
    """Encode one flat C-order numeric sequence using the portable Stage 6 dtypes."""

    if dtype not in ALLOWED_DTYPES:
        raise ValueError("unsupported Stage 6 dataset dtype")
    rows = tuple(values)
    if dtype == "<f8":
        parsed = tuple(float(value) for value in rows)
        if any(not math.isfinite(value) for value in parsed):
            raise ValueError("float dataset values must be finite")
        return struct.pack(f"<{len(parsed)}d", *parsed)
    if dtype in {"<i8", "<u8"}:
        parsed_int = []
        lower, upper = (-(2**63), 2**63 - 1) if dtype == "<i8" else (0, 2**64 - 1)
        for value in rows:
            if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
                raise ValueError("integer dataset value is outside the frozen dtype range")
            parsed_int.append(value)
        code = "q" if dtype == "<i8" else "Q"
        return struct.pack(f"<{len(parsed_int)}{code}", *parsed_int)
    parsed_complex = tuple(complex(value) for value in rows)
    if any(not math.isfinite(value.real) or not math.isfinite(value.imag) for value in parsed_complex):
        raise ValueError("complex dataset values must be finite")
    interleaved = tuple(component for value in parsed_complex for component in (value.real, value.imag))
    return struct.pack(f"<{len(interleaved)}d", *interleaved)


def decode_numeric_values(raw: bytes, dtype: str) -> tuple[float | int | complex, ...]:
    if dtype not in ALLOWED_DTYPES:
        raise ValueError("unsupported Stage 6 dataset dtype")
    width = 16 if dtype == "<c16" else 8
    if len(raw) % width:
        raise ValueError("dataset byte length is not aligned to dtype")
    count = len(raw) // width
    if dtype == "<f8":
        return struct.unpack(f"<{count}d", raw)
    if dtype == "<i8":
        return struct.unpack(f"<{count}q", raw)
    if dtype == "<u8":
        return struct.unpack(f"<{count}Q", raw)
    values = struct.unpack(f"<{count * 2}d", raw)
    return tuple(complex(values[index], values[index + 1]) for index in range(0, len(values), 2))


def write_response_dataset(
    data_dir: str | Path,
    responses: Iterable[float],
    point_table_sha256: str,
) -> dict[str, Any]:
    target = Path(data_dir)
    if target.exists():
        raise FileExistsError(f"dataset directory already exists: {target}")
    values = tuple(float(value) for value in responses)
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("dataset responses must be nonempty finite values")
    target.mkdir(parents=False)
    binary_path = target / "response.bin"
    raw = encode_numeric_values(values, "<f8")
    _write_fsynced(binary_path, raw)
    raw_sha = hashlib.sha256(raw).hexdigest().upper()
    payload = {
        "schema_version": "0.1",
        "claim_envelope": dict(CLAIM_ENVELOPE),
        "point_table_sha256": point_table_sha256,
        "dimensions": {"point": len(values)},
        "variables": {
            "response": {
                "dimensions": ["point"],
                "dtype": "<f8",
                "shape": [len(values)],
                "order": "C",
                "unit": "dimensionless",
                "semantic_role": "platform_fixture_response",
                "byte_length": len(raw),
                "raw_sha256": raw_sha,
            }
        },
    }
    _write_fsynced(target / "dataset.json", canonical_json_bytes(payload))
    validate_response_dataset(target, expected_point_count=len(values), point_table_sha256=point_table_sha256)
    return payload


def validate_response_dataset(
    data_dir: str | Path,
    *,
    expected_point_count: int,
    point_table_sha256: str,
    require_frozen_fixture: bool = False,
) -> tuple[dict[str, Any], tuple[float, ...]]:
    target = Path(data_dir)
    if not target.is_dir() or target.is_symlink():
        raise ValueError("dataset directory is missing or unsafe")
    if {row.name for row in target.iterdir()} != {"dataset.json", "response.bin"}:
        raise ValueError("dataset directory file set is invalid")
    manifest_path = target / "dataset.json"
    raw_manifest = manifest_path.read_bytes()
    try:
        payload = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("dataset manifest is invalid JSON") from exc
    if not isinstance(payload, dict) or raw_manifest != canonical_json_bytes(payload):
        raise ValueError("dataset manifest is not canonical")
    if set(payload) != DATASET_KEYS or payload.get("schema_version") != "0.1":
        raise ValueError("dataset manifest schema is invalid")
    if payload.get("claim_envelope") != CLAIM_ENVELOPE:
        raise ValueError("dataset claim envelope is invalid")
    if payload.get("point_table_sha256") != point_table_sha256:
        raise ValueError("dataset point table binding is invalid")
    if payload.get("dimensions") != {"point": expected_point_count}:
        raise ValueError("dataset dimensions are invalid")
    variables = payload.get("variables")
    if not isinstance(variables, dict) or set(variables) != {"response"}:
        raise ValueError("dataset variables are invalid")
    variable = variables["response"]
    expected_shape = [expected_point_count]
    if not isinstance(variable, dict) or set(variable) != VARIABLE_KEYS:
        raise ValueError("response variable schema is invalid")
    fixed = {
        "dimensions": ["point"],
        "dtype": "<f8",
        "shape": expected_shape,
        "order": "C",
        "unit": "dimensionless",
        "semantic_role": "platform_fixture_response",
        "byte_length": expected_point_count * 8,
    }
    if any(variable.get(key) != value for key, value in fixed.items()):
        raise ValueError("response variable metadata is invalid")
    raw = (target / "response.bin").read_bytes()
    actual_sha = hashlib.sha256(raw).hexdigest().upper()
    if len(raw) != expected_point_count * 8 or variable.get("raw_sha256") != actual_sha:
        raise ValueError("response binary length or hash is invalid")
    values = decode_numeric_values(raw, "<f8")
    if any(not math.isfinite(value) for value in values):
        raise ValueError("response binary contains non-finite values")
    if require_frozen_fixture and (values != EXPECTED_RESPONSE or actual_sha != EXPECTED_RESPONSE_SHA256):
        raise ValueError("response binary does not match frozen deterministic fixture")
    return payload, values


def dataset_manifest_sha256(data_dir: str | Path) -> str:
    return hashlib.sha256((Path(data_dir) / "dataset.json").read_bytes()).hexdigest().upper()


def _write_fsynced(path: Path, raw: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
