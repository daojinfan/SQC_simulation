"""Repository-owned production authority admission for Stage 4.1 controls."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np

from sqvm.control.registry import load_control_channel_registry
from sqvm.control.stage4_1_config import (
    _control_config_payload,
    _runtime_idle_flux,
    _runtime_idle_flux_sha256,
    build_parameterized_control_context,
)
from sqvm.control.stage4_1_models import (
    ParameterizedControlContext,
    ParameterizedControlError,
    ParameterizedControlReasonCode,
)
from sqvm.control.stage4_config import GROUP_CONTRACT, LANE_ORDER, load_control_chain_config
from sqvm.control.stage4_models import ControlChainConfig
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256
from sqvm.qcis.canonical import sha256_json


_ROOT = "configs/control/stage41"
_AUTHORITY = f"{_ROOT}/production_authority_v1.json"
_APPROVAL = f"{_ROOT}/production_approval_v1.json"
_SOURCE = f"{_ROOT}/source_snapshot_v1.json"
_ENVIRONMENT = f"{_ROOT}/environment_snapshot_v1.json"
_PUBLICATION = f"{_ROOT}/publication_policy_v1.json"
_DESIGN = "docs/designs/07_qcis_compiler_design.md"
_CONTROL_CONFIG = "configs/control/2q1c2r_control_smoke.yaml"
_CHANNEL_REGISTRY = "configs/control/2q1c2r_channels.yaml"
_DEVICE_CONFIG = "configs/devices/2q1c2r.yaml"
_DEFAULT_OUTPUT_ROOT = "output/stage_04_1_parameterized_control"
_DEVICE_LIMITS = {
    "q1": {"min_phi0": -1.0, "max_phi0": 1.0},
    "q2": {"min_phi0": -1.0, "max_phi0": 1.0},
    "c": {"min_phi0": -1.0, "max_phi0": 1.0},
}
_SHA256_LENGTH = 64


def production_parameterized_control_context(
    expected_plan_authority_sha256: Mapping[str, str],
    repository_root: Path,
    output_root: Path,
    *,
    idle_flux_phi0: Mapping[str, Any] | None = None,
    control_values: Mapping[str, Any] | None = None,
) -> ParameterizedControlContext:
    """Build the only production Stage 4.1 context from tracked authorities.

    Per-point QCIS authorities remain process-local caller input. The factory
    admits an optional runtime idle operating point while keeping the tracked
    electronics and device-limit authorities unchanged.
    """

    try:
        root = Path(repository_root).resolve(strict=True)
        if not root.is_dir():
            _fail("repository root")
        admitted_output_root = _safe_output_root(root, output_root)
        authority_path = _safe_file(root, _AUTHORITY)
        approval_path = _safe_file(root, _APPROVAL)
        source_path = _safe_file(root, _SOURCE)
        environment_path = _safe_file(root, _ENVIRONMENT)
        publication_path = _safe_file(root, _PUBLICATION)
        authority = _canonical_object(authority_path)
        approval = _canonical_object(approval_path)
        source = _canonical_object(source_path)
        environment = _canonical_object(environment_path)
        publication = _canonical_object(publication_path)
        _verify_source_snapshot(root, source)
        _verify_environment(environment)
        _verify_publication(publication)
        authority_id, device_limit_sha = _verify_authority(
            root, authority, authority_path, source_path, environment_path, publication_path,
        )
        _verify_approval(root, approval, authority_path, authority_id, source_path, environment_path, publication_path)
        control_path = _safe_file(root, _CONTROL_CONFIG)
        registry_path = _safe_file(root, _CHANNEL_REGISTRY)
        control = load_control_chain_config(control_path)
        runtime_idle = None
        runtime_control = None
        if control_values is not None:
            control = _runtime_control_chain_config(control, control_values)
            runtime_control = _control_config_payload(control)
            runtime_idle = _runtime_idle_flux(runtime_control["idle_flux_phi0"])
            if idle_flux_phi0 is not None and _runtime_idle_flux(idle_flux_phi0) != runtime_idle:
                _fail("runtime idle flux differs from Active control_values")
        elif idle_flux_phi0 is not None:
            runtime_idle = _runtime_idle_flux(idle_flux_phi0)
            control = replace(control, idle_flux_phi0=runtime_idle)
        registry = load_control_channel_registry(registry_path)
        authority_hash_values = {
            "stage4_1_production_authority": raw_file_sha256(authority_path),
            "stage4_1_production_approval": raw_file_sha256(approval_path),
            "stage4_1_qcis_design": raw_file_sha256(_safe_file(root, _DESIGN)),
            "stage4_1_control_config": raw_file_sha256(control_path),
            "stage4_1_channel_registry": raw_file_sha256(registry_path),
            "stage4_1_device_config": raw_file_sha256(_safe_file(root, _DEVICE_CONFIG)),
            "stage4_1_source_snapshot": raw_file_sha256(source_path),
            "stage4_1_environment_snapshot": raw_file_sha256(environment_path),
            "stage4_1_publication_policy": raw_file_sha256(publication_path),
        }
        if runtime_idle is not None:
            authority_hash_values["stage4_1_runtime_idle_flux"] = (
                _runtime_idle_flux_sha256(runtime_idle)
            )
        if runtime_control is not None:
            authority_hash_values["stage4_1_runtime_control"] = sha256_json(
                runtime_control
            )
        authority_hashes = MappingProxyType(authority_hash_values)
        return build_parameterized_control_context(
            control,
            registry,
            _DEVICE_LIMITS,
            device_limit_authority_sha256=device_limit_sha,
            repository_root=root,
            output_root=admitted_output_root,
            authority_sha256=authority_hashes,
            expected_plan_authority_sha256=expected_plan_authority_sha256,
            stage4_compatibility_approved=True,
            compiler_source_snapshot=source,
            environment_snapshot=environment,
            publication_policy=publication,
            runtime_idle_flux_phi0=runtime_idle,
            runtime_control_values=runtime_control,
        )
    except ParameterizedControlError:
        raise
    except Exception as exc:
        raise ParameterizedControlError(
            ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID,
            str(exc)[:512],
        ) from exc


def _runtime_control_chain_config(
    base: ControlChainConfig,
    value: Mapping[str, Any],
) -> ControlChainConfig:
    if not isinstance(value, Mapping):
        raise ValueError("Active control_values must be a mapping")
    required = {
        "clock",
        "dac",
        "lane_order",
        "lanes",
        "static_mixing",
        "idle_flux_phi0",
        "acceptance",
    }
    actual = set(value)
    if actual != required and actual != required | {"simulation"}:
        raise ValueError("Active control_values sections are not exact")

    clock = _runtime_mapping(value["clock"], {"sample_rate_Hz", "dt_ns"}, "clock")
    rate = _runtime_integer(clock["sample_rate_Hz"], "clock.sample_rate_Hz", minimum=1)
    dt = _runtime_decimal(clock["dt_ns"], "clock.dt_ns")
    if dt <= 0 or not math.isclose(float(dt) * rate, 1e9, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("Active clock sample rate and dt_ns must be reciprocal")

    dac_raw = _runtime_mapping(
        value["dac"],
        {"bits", "full_scale_min_V", "full_scale_max_exclusive_V", "rounding"},
        "dac",
    )
    bits = _runtime_integer(dac_raw["bits"], "dac.bits", minimum=1, maximum=32)
    lower = _runtime_decimal(dac_raw["full_scale_min_V"], "dac.full_scale_min_V")
    upper = _runtime_decimal(
        dac_raw["full_scale_max_exclusive_V"],
        "dac.full_scale_max_exclusive_V",
    )
    if lower >= upper or dac_raw["rounding"] != "half_even":
        raise ValueError("Active DAC range or rounding mode is invalid")
    lsb = (upper - lower) / Decimal(2**bits)
    dac = {
        "bits": bits,
        "full_scale_min_V": lower,
        "full_scale_max_exclusive_V": upper,
        "rounding": "half_even",
        "code_min": -(2 ** (bits - 1)),
        "code_max": 2 ** (bits - 1) - 1,
        "lsb_V": lsb,
    }

    order_raw = value["lane_order"]
    if not isinstance(order_raw, (list, tuple)) or tuple(order_raw) != LANE_ORDER:
        raise ValueError("Active lane_order differs from the formal lane order")
    lanes_raw = _runtime_mapping(value["lanes"], set(LANE_ORDER), "lanes")
    lanes: dict[str, Mapping[str, Any]] = {}
    for lane in LANE_ORDER:
        row = _runtime_mapping(
            lanes_raw[lane], {"latency_samples", "fir"}, f"lanes.{lane}"
        )
        latency = _runtime_integer(
            row["latency_samples"], f"lanes.{lane}.latency_samples", minimum=0
        )
        fir_raw = row["fir"]
        if not isinstance(fir_raw, (list, tuple)) or not 1 <= len(fir_raw) <= 64:
            raise ValueError(f"lanes.{lane}.fir must contain 1..64 values")
        fir = tuple(_runtime_float(item, f"lanes.{lane}.fir") for item in fir_raw)
        if (
            not math.isclose(sum(fir), 1.0, rel_tol=0.0, abs_tol=1e-12)
            or sum(abs(item) for item in fir) > 4.0
            or fir[0] == 0.0
        ):
            raise ValueError(f"lanes.{lane}.fir is not an admitted normalized filter")
        lanes[lane] = {"latency_samples": latency, "fir": fir}

    acceptance_raw = _runtime_mapping(
        value["acceptance"], set(base.acceptance), "acceptance"
    )
    acceptance: dict[str, Any] = {}
    for name in base.acceptance:
        acceptance[name] = (
            _runtime_integer(acceptance_raw[name], f"acceptance.{name}", minimum=1)
            if name == "max_formal_samples_per_scenario"
            else _runtime_positive_float(acceptance_raw[name], f"acceptance.{name}")
        )

    mixing_raw = _runtime_mapping(
        value["static_mixing"], set(GROUP_CONTRACT), "static_mixing"
    )
    mixing: dict[str, Mapping[str, Any]] = {}
    max_condition = float(acceptance["max_condition_number"])
    for group, (input_lanes, output_coordinates) in GROUP_CONTRACT.items():
        row = _runtime_mapping(
            mixing_raw[group],
            {"input_lanes", "output_coordinates", "matrix"},
            f"static_mixing.{group}",
        )
        if tuple(row["input_lanes"]) != input_lanes or tuple(row["output_coordinates"]) != output_coordinates:
            raise ValueError(f"static_mixing.{group} coordinates are not exact")
        matrix = np.asarray(row["matrix"], dtype="<f8")
        expected = len(input_lanes)
        condition = (
            float(np.linalg.cond(matrix, 2))
            if matrix.shape == (expected, expected) and np.isfinite(matrix).all()
            else math.inf
        )
        if not math.isfinite(condition) or condition > max_condition:
            raise ValueError(f"static_mixing.{group} is singular or ill-conditioned")
        matrix.setflags(write=False)
        mixing[group] = {
            "input_lanes": input_lanes,
            "output_coordinates": output_coordinates,
            "matrix": matrix,
            "condition_number_2": condition,
        }

    idle_raw = _runtime_mapping(
        value["idle_flux_phi0"], {"q1", "q2", "c"}, "idle_flux_phi0"
    )
    idle = {
        name: _runtime_decimal(idle_raw[name], f"idle_flux_phi0.{name}")
        for name in ("q1", "q2", "c")
    }
    return ControlChainConfig(
        base.source_path,
        base.profile,
        base.inputs,
        rate,
        dt,
        dac,
        LANE_ORDER,
        MappingProxyType(lanes),
        MappingProxyType(mixing),
        MappingProxyType(idle),
        MappingProxyType(acceptance),
    )


def _runtime_mapping(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{label} fields are not exact")
    return value


def _runtime_integer(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{label} is outside its admitted integer range")
    return value


def _runtime_decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not result.is_finite():
        raise ValueError(f"{label} must be finite")
    return result


def _runtime_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _runtime_positive_float(value: Any, label: str) -> float:
    result = _runtime_float(value, label)
    if result <= 0.0:
        raise ValueError(f"{label} must be positive")
    return result


def _verify_authority(
    root: Path,
    authority: Mapping[str, Any],
    authority_path: Path,
    source_path: Path,
    environment_path: Path,
    publication_path: Path,
) -> tuple[str, str]:
    expected = {
        "schema_version", "artifact_type", "artifact_version", "status", "design", "control_config",
        "channel_registry", "device_config", "device_flux_limits_phi0", "source_snapshot_sha256",
        "environment_snapshot_sha256", "publication_policy_sha256", "authority_id",
    }
    if set(authority) != expected or authority.get("schema_version") != "0.1" or authority.get("artifact_type") != "stage_04_1_production_authority" or authority.get("artifact_version") != "0.1" or authority.get("status") != "approved":
        _fail("authority schema")
    payload = dict(authority)
    authority_id = payload.pop("authority_id")
    if not _sha(authority_id) or authority_id != _canonical_sha(payload):
        _fail("authority id")
    _verify_bound_file(root, authority["design"], _DESIGN)
    _verify_bound_file(root, authority["control_config"], _CONTROL_CONFIG)
    _verify_bound_file(root, authority["channel_registry"], _CHANNEL_REGISTRY)
    device = _verify_bound_file(root, authority["device_config"], _DEVICE_CONFIG)
    if authority["device_flux_limits_phi0"] != _DEVICE_LIMITS:
        _fail("derived device flux limits")
    device_limit_sha = _canonical_sha(
        {"device_config": device, "device_flux_limits_phi0": _DEVICE_LIMITS}
    )
    if authority["source_snapshot_sha256"] != raw_file_sha256(source_path) or authority["environment_snapshot_sha256"] != raw_file_sha256(environment_path) or authority["publication_policy_sha256"] != raw_file_sha256(publication_path):
        _fail("authority snapshot binding")
    return authority_id, device_limit_sha


def _verify_approval(
    root: Path,
    approval: Mapping[str, Any],
    authority_path: Path,
    authority_id: str,
    source_path: Path,
    environment_path: Path,
    publication_path: Path,
) -> None:
    expected = {
        "schema_version", "artifact_type", "artifact_version", "status", "authority_path",
        "authority_raw_sha256", "authority_id", "design_path", "design_raw_sha256",
        "context_bindings", "reviewer_role",
    }
    if set(approval) != expected or approval.get("schema_version") != "0.1" or approval.get("artifact_type") != "stage_04_1_production_approval" or approval.get("artifact_version") != "0.1" or approval.get("status") != "approved" or not isinstance(approval.get("reviewer_role"), str) or not approval["reviewer_role"]:
        _fail("approval schema")
    if approval["authority_path"] != authority_path.relative_to(root).as_posix() or approval["authority_raw_sha256"] != raw_file_sha256(authority_path) or approval["authority_id"] != authority_id:
        _fail("approval authority binding")
    design = _safe_file(root, _DESIGN)
    if approval["design_path"] != _DESIGN or approval["design_raw_sha256"] != raw_file_sha256(design):
        _fail("approval design binding")
    expected_context = {
        "source_snapshot": raw_file_sha256(source_path),
        "environment_snapshot": raw_file_sha256(environment_path),
        "publication_policy": raw_file_sha256(publication_path),
    }
    if approval["context_bindings"] != expected_context:
        _fail("approval context binding")


def _verify_source_snapshot(root: Path, source: Mapping[str, Any]) -> None:
    expected = {"schema_version", "artifact_type", "artifact_version", "sources"}
    if set(source) != expected or source.get("schema_version") != "0.1" or source.get("artifact_type") != "stage_04_1_source_snapshot" or source.get("artifact_version") != "0.1" or not isinstance(source.get("sources"), list):
        _fail("source snapshot schema")
    previous: str | None = None
    for row in source["sources"]:
        if not isinstance(row, Mapping) or set(row) != {"path", "raw_sha256"} or not isinstance(row["path"], str) or not _sha(row["raw_sha256"]):
            _fail("source snapshot row")
        if previous is not None and row["path"].encode("utf-8") <= previous.encode("utf-8"):
            _fail("source snapshot ordering")
        previous = row["path"]
        if raw_file_sha256(_safe_file(root, row["path"])) != row["raw_sha256"]:
            _fail(f"source drift: {row['path']}")


def _verify_environment(environment: Mapping[str, Any]) -> None:
    expected = {"schema_version", "artifact_type", "artifact_version", "environment"}
    if set(environment) != expected or environment.get("schema_version") != "0.1" or environment.get("artifact_type") != "stage_04_1_environment_snapshot" or environment.get("artifact_version") != "0.1" or not isinstance(environment.get("environment"), Mapping):
        _fail("environment snapshot")


def _verify_publication(publication: Mapping[str, Any]) -> None:
    expected = {"schema_version", "artifact_type", "artifact_version", "status", "configured_base_root", "mode"}
    if set(publication) != expected or publication.get("schema_version") != "0.1" or publication.get("artifact_type") != "stage_04_1_publication_policy" or publication.get("artifact_version") != "0.1" or publication.get("status") != "approved" or publication.get("configured_base_root") != _DEFAULT_OUTPUT_ROOT or publication.get("mode") != "atomic_no_replace":
        _fail("publication policy")


def _verify_bound_file(root: Path, value: Any, expected_path: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"path", "raw_sha256"} or value.get("path") != expected_path or not _sha(value.get("raw_sha256")):
        _fail(f"authority file binding: {expected_path}")
    path = _safe_file(root, expected_path)
    actual = raw_file_sha256(path)
    if actual != value["raw_sha256"]:
        _fail(f"authority raw hash: {expected_path}")
    return {"path": expected_path, "raw_sha256": actual}


def _canonical_object(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(f"canonical JSON: {path.name}: {exc}")
    if not isinstance(payload, dict) or raw != canonical_json_bytes(payload):
        _fail(f"non-canonical JSON: {path.name}")
    return payload


def _safe_file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or not relative.isascii():
        _fail("repository-relative path")
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts or ".." in windows.parts:
        _fail("repository-relative path")
    candidate = root.joinpath(*relative.replace("\\", "/").split("/"))
    try:
        candidate.relative_to(root)
        current = root
        for part in candidate.relative_to(root).parts:
            current /= part
            attributes = getattr(current.stat(), "st_file_attributes", 0)
            if current.is_symlink() or attributes & 0x400:
                _fail("symlink or reparse path")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        _fail(f"unsafe path: {relative}: {exc}")
    if not resolved.is_file():
        _fail(f"not a regular file: {relative}")
    return resolved


def _safe_output_root(root: Path, value: Path) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        _fail("production output root must be an absolute Path")
    try:
        lexical = value.relative_to(root)
        if ".." in lexical.parts:
            _fail("production output root escapes repository")
        current = root
        for part in lexical.parts:
            current /= part
            if not current.exists():
                break
            attributes = getattr(current.stat(), "st_file_attributes", 0)
            if current.is_symlink() or attributes & 0x400:
                _fail("production output root has a symlink or reparse component")
        resolved = value.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        _fail(f"production output root is unsafe: {exc}")
    if value.exists() and not value.is_dir():
        _fail("production output root is not a directory")
    return resolved


def _canonical_sha(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def _sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == _SHA256_LENGTH and all(character in "0123456789ABCDEF" for character in value)


def _fail(detail: str) -> None:
    raise ParameterizedControlError(ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID, detail)
