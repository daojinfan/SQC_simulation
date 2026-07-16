"""Repository-owned production authority admission for Stage 4.1 controls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any, Mapping

from sqvm.control.registry import load_control_channel_registry
from sqvm.control.stage4_1_config import build_parameterized_control_context
from sqvm.control.stage4_1_models import (
    ParameterizedControlContext,
    ParameterizedControlError,
    ParameterizedControlReasonCode,
)
from sqvm.control.stage4_config import load_control_chain_config
from sqvm.hamiltonian.provenance import canonical_json_bytes, raw_file_sha256


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
) -> ParameterizedControlContext:
    """Build the only production Stage 4.1 context from tracked authorities.

    Per-point QCIS authorities remain process-local caller input.  The factory
    validates their hash shape but never supplies or transforms a plan itself.
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
        registry = load_control_channel_registry(registry_path)
        authority_hashes = MappingProxyType(
            {
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
        )
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
        )
    except ParameterizedControlError:
        raise
    except Exception as exc:
        raise ParameterizedControlError(
            ParameterizedControlReasonCode.CONTROL_AUTHORITY_INVALID,
            str(exc)[:512],
        ) from exc


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
