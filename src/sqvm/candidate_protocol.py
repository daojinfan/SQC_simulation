"""Experiment-independent calibration candidate protocol."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import math
import re
from typing import Any, Mapping, Sequence
import unicodedata


CALIBRATION_CANDIDATE_SCHEMA = "calibration_candidate_v1"
_CANDIDATE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_PATH_SEGMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*$")
_FORBIDDEN_PATH_SEGMENTS = {
    "accepted",
    "base_revision",
    "base_setting_hash",
    "calibration_id",
    "calibration_run_id",
    "gate_type",
    "mapper_id",
    "mapper_type",
    "parent_calibration_sha256",
    "recommendation_sha256",
    "revision",
    "setting_hash",
    "setting_id",
    "state_id",
    "status",
    "target",
    "wave_index",
}


class CalibrationCandidateProtocolError(ValueError):
    """Raised when a calibration candidate does not satisfy the common protocol."""


class CandidateApplicationDecisionError(CalibrationCandidateProtocolError):
    """Raised when a caller's candidate application decision is invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CandidateApplicationDecision:
    """Normalized, auditable authority for applying calibration candidates."""

    mode: str = "recommended_only"
    source: str = "automation"
    reason: str | None = None

    def to_dict(
        self, *, overrode_recommendation: bool | None = None
    ) -> dict[str, Any]:
        result = {
            "mode": self.mode,
            "source": self.source,
            "reason": self.reason,
        }
        if overrode_recommendation is not None:
            result["overrode_recommendation"] = overrode_recommendation
        return result


_DECISION_MODES = frozenset({"recommended_only", "override_recommendation"})
_DECISION_SOURCES = frozenset(
    {"automation", "notebook_user", "web_user", "ai_assisted"}
)


def normalize_candidate_application_decision(
    decision: CandidateApplicationDecision | Mapping[str, Any] | None = None,
    *,
    mode: str = "recommended_only",
    source: str = "automation",
    reason: str | None = None,
) -> CandidateApplicationDecision:
    """Validate and normalize the authority to apply selected candidates.

    The optional keyword form keeps callers that have not yet adopted the
    decision object on the conservative ``recommended_only`` default.
    """

    if decision is not None:
        if isinstance(decision, CandidateApplicationDecision):
            mode, source, reason = decision.mode, decision.source, decision.reason
        elif isinstance(decision, Mapping):
            if set(decision).difference({"mode", "source", "reason", "overrode_recommendation"}):
                raise CandidateApplicationDecisionError(
                    "candidate_decision_invalid", "candidate decision fields are invalid"
                )
            mode = decision.get("mode", mode)
            source = decision.get("source", source)
            reason = decision.get("reason", reason)
        else:
            raise CandidateApplicationDecisionError(
                "candidate_decision_invalid", "candidate decision is invalid"
            )
    if (
        not isinstance(mode, str)
        or not isinstance(source, str)
        or mode not in _DECISION_MODES
        or source not in _DECISION_SOURCES
    ):
        raise CandidateApplicationDecisionError(
            "candidate_decision_invalid", "candidate decision mode or source is invalid"
        )
    if reason is not None:
        if not isinstance(reason, str):
            raise CandidateApplicationDecisionError(
                "candidate_decision_invalid", "candidate decision reason is invalid"
            )
        reason = reason.strip()
        if len(reason) > 2048 or any(unicodedata.category(char) == "Cc" for char in reason):
            raise CandidateApplicationDecisionError(
                "candidate_decision_invalid", "candidate decision reason is invalid"
            )
    if mode == "override_recommendation":
        if source == "automation":
            raise CandidateApplicationDecisionError(
                "candidate_override_source_invalid",
                "automation cannot override a candidate recommendation",
            )
        if not reason:
            raise CandidateApplicationDecisionError(
                "candidate_override_reason_required",
                "candidate override reason is required",
            )
    return CandidateApplicationDecision(mode=mode, source=source, reason=reason)


def parameter_change(
    parameter_path: str,
    current_value: Any,
    proposed_value: Any,
    *,
    unit: str | None = None,
    configuration_resource: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one immutable ``set`` change relative to ``editable``."""

    path = normalize_parameter_path(parameter_path)
    _finite_json_value(current_value, "current_value")
    _finite_json_value(proposed_value, "proposed_value")
    if unit is not None and (not isinstance(unit, str) or not unit.strip()):
        raise CalibrationCandidateProtocolError("candidate change unit is invalid")
    result = {
        "operation": "set",
        "parameter_path": path,
        "value_type": _value_type(proposed_value),
        "current_value": copy.deepcopy(current_value),
        "proposed_value": copy.deepcopy(proposed_value),
        "unit": unit,
    }
    if configuration_resource is not None:
        result["configuration_resource"] = _normalize_resource(
            configuration_resource
        )
    return result


def calibration_candidate(
    candidate_id: str,
    calibration_subjects: str | Sequence[str],
    changes: Sequence[Mapping[str, Any]],
    *,
    recommendation_eligible: bool,
    candidate_type: str,
    source_dataset_sha256s: Sequence[str] = (),
    quality_metrics: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Build a candidate group that can be applied atomically."""

    subjects = _normalize_subjects(calibration_subjects)
    row = {
        "schema": CALIBRATION_CANDIDATE_SCHEMA,
        "candidate_id": candidate_id,
        "candidate_type": candidate_type,
        "calibration_subjects": subjects,
        "target": subjects[0],
        "changes": [dict(change) for change in changes],
        "source_dataset_sha256s": list(source_dataset_sha256s),
        "quality_metrics": dict(quality_metrics or {}),
        "recommendation_eligible": recommendation_eligible,
        "reason": reason,
    }
    return normalize_calibration_candidate(row)


def normalize_calibration_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a common candidate, adapting legacy spectroscopy rows."""

    if not isinstance(candidate, Mapping):
        raise CalibrationCandidateProtocolError("candidate must be an object")
    raw_subjects = candidate.get("calibration_subjects")
    target = candidate.get("target")
    if raw_subjects is None:
        raw_subjects = target
    subjects = _normalize_subjects(raw_subjects)
    if target is not None and target not in subjects:
        raise CalibrationCandidateProtocolError(
            "legacy candidate target must be a calibration subject"
        )
    target = subjects[0]
    candidate_id = candidate.get("candidate_id")
    raw_changes = candidate.get("changes")
    if raw_changes is None:
        raw_changes = [_legacy_frequency_change(candidate, target)]
    if (
        isinstance(raw_changes, (str, bytes))
        or not isinstance(raw_changes, Sequence)
        or not raw_changes
    ):
        raise CalibrationCandidateProtocolError("candidate changes are required")
    changes = [
        _normalize_change(change, default_owner=subjects[0])
        for change in raw_changes
    ]
    paths = [change["parameter_path"] for change in changes]
    if len(paths) != len(set(paths)):
        raise CalibrationCandidateProtocolError("candidate parameter paths must be unique")
    if candidate_id is None:
        candidate_id = f"{target}:{paths[0]}"
    if not isinstance(candidate_id, str) or not _CANDIDATE_ID.fullmatch(candidate_id):
        raise CalibrationCandidateProtocolError("candidate_id is invalid")
    eligible = candidate.get("recommendation_eligible")
    if type(eligible) is not bool:
        raise CalibrationCandidateProtocolError("candidate eligibility is invalid")
    candidate_type = candidate.get("candidate_type")
    if candidate_type is None and "proposed_frequency_GHz" in candidate:
        candidate_type = "qubit_reference_frequency"
    if not isinstance(candidate_type, str) or not candidate_type.strip():
        raise CalibrationCandidateProtocolError("candidate_type is invalid")
    source_hashes = candidate.get("source_dataset_sha256s", [])
    if not isinstance(source_hashes, list) or any(
        not isinstance(item, str) or not item for item in source_hashes
    ):
        raise CalibrationCandidateProtocolError("candidate source hashes are invalid")
    resources = _unique_resources(
        change["configuration_resource"] for change in changes
    )
    declared_resources = candidate.get("configuration_resources")
    if declared_resources is not None:
        if (
            isinstance(declared_resources, (str, bytes))
            or not isinstance(declared_resources, Sequence)
        ):
            raise CalibrationCandidateProtocolError(
                "candidate configuration_resources are invalid"
            )
        normalized_declared = _unique_resources(
            _normalize_resource(resource) for resource in declared_resources
        )
        if normalized_declared != resources:
            raise CalibrationCandidateProtocolError(
                "candidate configuration_resources do not match changes"
            )
    result = copy.deepcopy(dict(candidate))
    result.update(
        {
            "schema": CALIBRATION_CANDIDATE_SCHEMA,
            "candidate_id": candidate_id,
            "candidate_type": candidate_type,
            "calibration_subjects": subjects,
            "configuration_resources": resources,
            "target": target,
            "changes": changes,
            "recommendation_eligible": eligible,
            "source_dataset_sha256s": list(source_hashes),
        }
    )
    return result


def normalize_parameter_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise CalibrationCandidateProtocolError("candidate parameter path is invalid")
    path = value.removeprefix("$.").removeprefix("editable.")
    if path.startswith("values."):
        path = "calibration_values." + path.removeprefix("values.")
    parts = path.split(".")
    if (
        len(parts) < 2
        or parts[0] not in {"calibration_values", "control_values"}
        or any(not _PATH_SEGMENT.fullmatch(part) for part in parts)
        or any(part in _FORBIDDEN_PATH_SEGMENTS for part in parts)
    ):
        raise CalibrationCandidateProtocolError("candidate parameter path is not writable")
    return ".".join(parts)


def value_at_parameter_path(editable: Mapping[str, Any], parameter_path: str) -> Any:
    current: Any = editable
    for part in normalize_parameter_path(parameter_path).split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise CalibrationCandidateProtocolError(
                f"candidate parameter does not exist: {parameter_path}"
            )
        current = current[part]
    return current


def set_parameter_value(editable: dict[str, Any], change: Mapping[str, Any]) -> None:
    normalized = _normalize_change(change)
    parts = normalized["parameter_path"].split(".")
    current: Any = editable
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            raise CalibrationCandidateProtocolError(
                f"candidate parameter does not exist: {normalized['parameter_path']}"
            )
        current = current[part]
    if not isinstance(current, dict) or parts[-1] not in current:
        raise CalibrationCandidateProtocolError(
            f"candidate parameter does not exist: {normalized['parameter_path']}"
        )
    actual = current[parts[-1]]
    if not candidate_values_equal(actual, normalized["current_value"]):
        raise CalibrationCandidateProtocolError(
            f"candidate is stale for {normalized['parameter_path']}"
        )
    current[parts[-1]] = copy.deepcopy(normalized["proposed_value"])


def candidate_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1.0e-12)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            candidate_values_equal(a, b) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _normalize_change(
    change: Mapping[str, Any],
    *,
    default_owner: str = "CONTROL",
) -> dict[str, Any]:
    if not isinstance(change, Mapping) or change.get("operation", "set") != "set":
        raise CalibrationCandidateProtocolError("only candidate set changes are supported")
    if "current_value" not in change or "proposed_value" not in change:
        raise CalibrationCandidateProtocolError("candidate change values are required")
    result = parameter_change(
        change.get("parameter_path"),
        change["current_value"],
        change["proposed_value"],
        unit=change.get("unit"),
        configuration_resource=(
            change.get("configuration_resource")
            or _infer_resource(change.get("parameter_path"), default_owner)
        ),
    )
    declared_type = change.get("value_type")
    if declared_type is not None and declared_type != result["value_type"]:
        raise CalibrationCandidateProtocolError("candidate change value_type is invalid")
    return result


def _legacy_frequency_change(candidate: Mapping[str, Any], target: str) -> dict[str, Any]:
    if "current_frequency_GHz" not in candidate or "proposed_frequency_GHz" not in candidate:
        raise CalibrationCandidateProtocolError("candidate changes are required")
    return parameter_change(
        (
            "calibration_values.qagents."
            f"{target}.reference_frequency_authority.reference_frequency_GHz"
        ),
        candidate.get("current_frequency_GHz"),
        candidate.get("proposed_frequency_GHz"),
        unit="GHz",
        configuration_resource={
            "owner": target,
            "resource_type": "qagent_calibration",
            "resource_id": target,
        },
    )


def _normalize_subjects(value: str | Sequence[str]) -> list[str]:
    subjects = [value] if isinstance(value, str) else list(value) if isinstance(value, Sequence) else []
    if (
        not subjects
        or len(subjects) != len(set(subjects))
        or any(not isinstance(subject, str) or not subject.strip() for subject in subjects)
    ):
        raise CalibrationCandidateProtocolError(
            "candidate calibration_subjects are invalid"
        )
    return subjects


def _normalize_resource(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise CalibrationCandidateProtocolError(
            "candidate configuration resource is invalid"
        )
    owner = value.get("owner")
    resource_type = value.get("resource_type")
    resource_id = value.get("resource_id")
    if any(
        not isinstance(item, str) or not item.strip() or len(item) > 192
        for item in (owner, resource_type, resource_id)
    ):
        raise CalibrationCandidateProtocolError(
            "candidate configuration resource is invalid"
        )
    return {
        "owner": owner,
        "resource_type": resource_type,
        "resource_id": resource_id,
    }


def _infer_resource(parameter_path: Any, default_owner: str) -> dict[str, str]:
    path = normalize_parameter_path(parameter_path)
    parts = path.split(".")
    if len(parts) >= 4 and parts[:2] == ["calibration_values", "qagents"]:
        return {
            "owner": parts[2],
            "resource_type": "qagent_calibration",
            "resource_id": parts[2],
        }
    if len(parts) >= 5 and parts[:3] == [
        "calibration_values",
        "waveform_registry",
        "settings",
    ]:
        return {
            "owner": default_owner,
            "resource_type": "waveform_setting",
            "resource_id": parts[3],
        }
    if len(parts) >= 5 and parts[:3] == [
        "calibration_values",
        "waveform_registry",
        "mappers",
    ]:
        return {
            "owner": default_owner,
            "resource_type": "waveform_mapper",
            "resource_id": parts[3],
        }
    if len(parts) >= 3 and parts[:2] == ["calibration_values", "gate_configuration"]:
        return {
            "owner": parts[2],
            "resource_type": "gate_configuration",
            "resource_id": parts[2],
        }
    return {
        "owner": default_owner,
        "resource_type": "configuration_section",
        "resource_id": ".".join(parts[:-1]),
    }


def _unique_resources(values: Any) -> list[dict[str, str]]:
    result = []
    seen = set()
    for value in values:
        resource = _normalize_resource(value)
        identity = (
            resource["owner"],
            resource["resource_type"],
            resource["resource_id"],
        )
        if identity not in seen:
            result.append(resource)
            seen.add(identity)
    return result


def _value_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if type(value) is int:
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if value is None:
        return "null"
    raise CalibrationCandidateProtocolError("candidate values must be scalar or list")


def _finite_json_value(value: Any, label: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CalibrationCandidateProtocolError(f"{label} must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _finite_json_value(item, label)
        return
    raise CalibrationCandidateProtocolError(f"{label} must be scalar or list")


__all__ = [
    "CALIBRATION_CANDIDATE_SCHEMA",
    "CandidateApplicationDecision",
    "CandidateApplicationDecisionError",
    "CalibrationCandidateProtocolError",
    "calibration_candidate",
    "candidate_values_equal",
    "normalize_candidate_application_decision",
    "normalize_calibration_candidate",
    "normalize_parameter_path",
    "parameter_change",
    "set_parameter_value",
    "value_at_parameter_path",
]
