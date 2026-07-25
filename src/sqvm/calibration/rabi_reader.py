"""Bounded reader-only verifier for X2P amplitude Rabi evidence v0.1."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from sqvm.candidate_protocol import normalize_calibration_candidate
from sqvm.hamiltonian.provenance import canonical_json_bytes
from sqvm.qcis.canonical import sha256_json
from sqvm.storage.archive_format import EvidenceReader


_LIMIT = 1024 * 1024
_TERMINAL = frozenset({"manifest.json", "verification_report.json", "receipt.json"})
_ROOT = frozenset({"workflow.json", "dataset.json", *_TERMINAL})
_WORKFLOW_ID = "qubit_rabi_x2p_amplitude_scan_v1"


class RabiReaderVerificationError(ValueError):
    """The supplied bounded evidence view is not a complete Rabi scan."""


def verify_qubit_rabi_scan_evidence(reader: EvidenceReader) -> None:
    """Verify Rabi publication bindings without materialising an archive."""

    try:
        paths = _paths(reader)
        raw = {name: _read(reader, name) for name in _ROOT}
        workflow = _json(raw["workflow.json"], "workflow")
        dataset = _json(raw["dataset.json"], "dataset")
        receipt = _json(raw["receipt.json"], "receipt")
        report = _json(raw["verification_report.json"], "verification report")
        manifest = _json(raw["manifest.json"], "manifest")
        workflow_sha = _sha(raw["workflow.json"])
        dataset_sha = _sha(raw["dataset.json"])
        _workflow(workflow, dataset, receipt, report, manifest, workflow_sha, dataset_sha)
        request = _json(_read(reader, "execution/batch/request.json"), "runtime batch request")
        head = _json(_read(reader, "execution/batch/head.json"), "runtime batch head")
        _batch(reader, workflow, dataset, request, head, paths)
    except RabiReaderVerificationError:
        raise
    except Exception as exc:
        raise RabiReaderVerificationError(str(exc) or "rabi reader verification failed") from exc


def _paths(reader: EvidenceReader) -> tuple[str, ...]:
    paths = reader.paths()
    if not isinstance(paths, tuple) or not paths or len(paths) != len(set(paths)):
        raise RabiReaderVerificationError("rabi evidence paths are invalid")
    if any(not isinstance(path, str) or not path or "\\" in path or "\x00" in path or any(part in {"", ".", ".."} for part in path.split("/")) for path in paths):
        raise RabiReaderVerificationError("rabi evidence path escapes root")
    root = {path for path in paths if "/" not in path}
    if root != _ROOT or not any(path.startswith("execution/") for path in paths):
        raise RabiReaderVerificationError("rabi evidence layout is invalid")
    return tuple(sorted(paths))


def _read(reader: EvidenceReader, path: str) -> bytes:
    try:
        return reader.read_bytes(path, maximum_bytes=_LIMIT)
    except Exception as exc:
        raise RabiReaderVerificationError(f"cannot read rabi {path}") from exc


def _json(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except Exception as exc:
        raise RabiReaderVerificationError(f"rabi {label} is not JSON") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise RabiReaderVerificationError(f"rabi {label} is not canonical")
    _finite(value)
    return value


def _workflow(workflow, dataset, receipt, report, manifest, workflow_sha, dataset_sha) -> None:
    request = workflow.get("request")
    analysis = workflow.get("analysis")
    run_id = workflow.get("run_id")
    if (workflow.get("workflow_id") != _WORKFLOW_ID or workflow.get("artifact_type") != "qubit_rabi_x2p_amplitude_scan" or workflow.get("artifact_version") != "0.1" or workflow.get("status") != "completed" or workflow.get("archive_eligible") is not True or not isinstance(run_id, str) or not run_id or not isinstance(request, Mapping) or not isinstance(analysis, Mapping)):
        raise RabiReaderVerificationError("rabi workflow identity is invalid")
    if request.get("workflow_id") != _WORKFLOW_ID or request.get("experiment_id") != "qubit_rabi_x2p_amplitude_v1" or request.get("gate_sequence") != ["X2P", "X2P"] or request.get("setting_selector") != "active_xy2_setting" or not isinstance(request.get("analysis_policy_id"), str) or not request["analysis_policy_id"] or type(request.get("analysis_policy_approved")) is not bool or not _sha_text(request.get("analysis_policy_sha256")) or not isinstance(request.get("setting_id"), str) or not request["setting_id"] or not _sha_text(request.get("setting_hash")) or not _number(request.get("setting_amplitude_GHz")):
        raise RabiReaderVerificationError("rabi request policy binding is invalid")
    eligible = workflow.get("recommendation_eligible")
    claim = workflow.get("claim")
    phase = workflow.get("phase_audit")
    if type(eligible) is not bool or (eligible and request["analysis_policy_approved"] is not True) or phase != {"passed": True, "implementation": "rabi_x2p_phase_v1"} or not isinstance(claim, Mapping) or claim.get("recommendation_eligible") is not eligible or claim.get("execution_profile") not in {"calibration_scan", "bounded_smoke"}:
        raise RabiReaderVerificationError("rabi recommendation authority is invalid")
    if workflow.get("dataset") != {"path": "dataset.json", "sha256": dataset_sha} or analysis.get("input_dataset_sha256") != dataset_sha:
        raise RabiReaderVerificationError("rabi analysis dataset binding is invalid")
    expected_receipt = {"artifact_type": "rabi_scan_receipt", "status": "completed", "run_id": run_id, "workflow_sha256": workflow_sha, "dataset_sha256": dataset_sha, "recommendation_id": workflow.get("recommendation_id")}
    expected_report = {"ok": True, "status": "completed", "run_id": run_id, "workflow_sha256": workflow_sha, "dataset_sha256": dataset_sha}
    if dict(receipt) != expected_receipt or dict(report) != expected_report:
        raise RabiReaderVerificationError("rabi receipt/report binding is invalid")
    if dict(manifest) != {"artifact_type": "rabi_scan_manifest", "workflow_sha256": workflow_sha, "dataset_sha256": dataset_sha, "receipt_sha256": _sha(canonical_json_bytes(receipt))}:
        raise RabiReaderVerificationError("rabi manifest binding is invalid")
    _dataset(workflow, dataset)
    _analysis(analysis, dataset_sha, request, dataset)
    _candidate(workflow, dataset_sha)


def _dataset(workflow: Mapping[str, Any], dataset: Mapping[str, Any]) -> None:
    request = workflow["request"]
    target = request.get("target")
    axis = dataset.get("axis", {})
    values = axis.get("values") if isinstance(axis, Mapping) else None
    series = dataset.get("series", {}).get(target) if isinstance(dataset.get("series"), Mapping) else None
    points = dataset.get("points")
    if dataset.get("experiment_id") != "qubit_rabi_x2p_amplitude_v1" or dataset.get("target") != target or axis.get("name") != "amplitude_GHz" or axis.get("unit") != "GHz" or values != request.get("axis", {}).get("values") or not isinstance(values, list) or not values or values[0] != 0.0 or len(values) > 64 or any(not _number(value) for value in values) or any(a >= b for a, b in zip(values, values[1:])) or not isinstance(series, Mapping) or not isinstance(points, list) or len(points) != len(values):
        raise RabiReaderVerificationError("rabi dataset axis or series is invalid")
    for key in ("P0", "P1", "leakage", "norm_error"):
        values_for_key = series.get(key)
        if not isinstance(values_for_key, list) or len(values_for_key) != len(values):
            raise RabiReaderVerificationError("rabi dataset series lengths are invalid")
    if any(not _number(value) or not 0.0 <= value <= 1.0 for key in ("P0", "P1", "leakage") for value in series[key]) or any(not _number(value) or value < 0.0 for value in series["norm_error"]):
        raise RabiReaderVerificationError("rabi dataset physical values are invalid")
    for index, point in enumerate(points):
        audit = point.get("phase_audit") if isinstance(point, Mapping) else None
        if not isinstance(point, Mapping) or point.get("point_index") != index or not _sha_text(point.get("circuit_sha256")) or not _sha_text(point.get("overlay_sha256")) or not isinstance(point.get("circuit_id"), str) or not point["circuit_id"]:
            raise RabiReaderVerificationError("rabi point binding is invalid")
        _phase(audit, float(values[index]), request.get("setting_id"))


def _phase(audit: Any, amplitude: float, setting_id: Any) -> None:
    if not isinstance(audit, Mapping) or audit.get("passed") is not True or audit.get("event_count") != 2 or audit.get("electronics_schedule") is not None or not all(type(audit.get(key)) is int and audit[key] >= 0 for key in ("first_start_sample", "second_start_sample", "length_samples")) or audit["second_start_sample"] != audit["first_start_sample"] + audit["length_samples"] or audit["length_samples"] <= 0 or not isinstance(audit.get("intervals"), list) or len(audit["intervals"]) != 2:
        raise RabiReaderVerificationError("rabi phase audit is invalid")
    first, second = audit["intervals"]
    if not all(isinstance(item, Mapping) for item in (first, second)) or first.get("start_sample") != audit["first_start_sample"] or first.get("end_sample") != audit["second_start_sample"] or second.get("start_sample") != audit["second_start_sample"] or second.get("end_sample") != audit["second_start_sample"] + audit["length_samples"] or type(first.get("source_instruction_index")) is not int or second.get("source_instruction_index") != first["source_instruction_index"] + 1 or first.get("setting_id") != setting_id or first.get("setting_id") != second.get("setting_id") or first.get("setting_hash") != second.get("setting_hash") or audit.get("setting_hashes") != [first.get("setting_hash"), second.get("setting_hash")]:
        raise RabiReaderVerificationError("rabi phase interval binding is invalid")
    if not _number(audit.get("amplitude_GHz")) or not math.isclose(float(audit["amplitude_GHz"]), amplitude, rel_tol=0.0, abs_tol=1e-12):
        raise RabiReaderVerificationError("rabi phase amplitude differs")
    for key in ("phase_total_rad", "f_drive_GHz", "f_ref_GHz", "detuning_GHz", "lab_phase_advance_unwrapped_rad", "lab_phase_advance_wrapped_rad"):
        if not _number(audit.get(key)):
            raise RabiReaderVerificationError("rabi phase values are invalid")
    if not all(isinstance(audit.get(key), list) and len(audit[key]) == 2 and all(_number(value) for value in audit[key]) for key in ("rotating_first_sample_phase_rad", "lab_first_sample_phase_rad", "absolute_start_time_ns")) or not math.isclose(float(audit["detuning_GHz"]), float(audit["f_drive_GHz"]) - float(audit["f_ref_GHz"]), rel_tol=0.0, abs_tol=1e-12):
        raise RabiReaderVerificationError("rabi phase relationship is invalid")
    times = [float(value) for value in audit["absolute_start_time_ns"]]
    phase, drive, detuning = float(audit["phase_total_rad"]), float(audit["f_drive_GHz"]), float(audit["detuning_GHz"])
    rotating = [_wrap(phase + 2.0 * math.pi * detuning * time) for time in times]
    lab = [_wrap(phase + 2.0 * math.pi * drive * time) for time in times]
    advance = 2.0 * math.pi * drive * (times[1] - times[0])
    if any(not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12) for actual, expected in zip(audit["rotating_first_sample_phase_rad"], rotating)) or any(not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-12) for actual, expected in zip(audit["lab_first_sample_phase_rad"], lab)) or not math.isclose(float(audit["lab_phase_advance_unwrapped_rad"]), advance, rel_tol=0.0, abs_tol=1e-12) or not math.isclose(float(audit["lab_phase_advance_wrapped_rad"]), _wrap(advance), rel_tol=0.0, abs_tol=1e-12):
        raise RabiReaderVerificationError("rabi phase reconstruction differs")


def _analysis(
    analysis: Mapping[str, Any],
    dataset_sha: str,
    request: Mapping[str, Any],
    dataset: Mapping[str, Any],
) -> None:
    expected_keys = {
        "algorithm_version", "fit_converged", "offset", "contrast",
        "x2p_amplitude_GHz", "rmse", "normalized_rmse", "r_squared",
        "first_peak_index", "peak_bracket_GHz", "fitted_P1", "reason",
        "dense_fit_curve", "candidate_distance_to_edge_steps",
        "candidate_leakage", "max_norm_error", "phase_audit_passed",
        "input_dataset_sha256", "optimizer_nfev", "residual_sum_squares",
        "analysis_policy_sha256",
    }
    if (
        set(analysis) != expected_keys
        or analysis.get("algorithm_version") != "rabi_x2p_bounded_least_squares_v1"
        or analysis.get("input_dataset_sha256") != dataset_sha
        or analysis.get("analysis_policy_sha256") != request.get("analysis_policy_sha256")
        or not _sha_text(analysis.get("analysis_policy_sha256"))
        or type(analysis.get("fit_converged")) is not bool
        or analysis.get("phase_audit_passed") is not True
    ):
        raise RabiReaderVerificationError("rabi analysis identity is invalid")

    axis = dataset["axis"]["values"]
    series = dataset["series"][dataset["target"]]
    peaks = [
        index for index in range(1, len(axis) - 1)
        if series["P1"][index] > series["P1"][index - 1]
        and series["P1"][index] >= series["P1"][index + 1]
    ]
    expected_max_norm = max(float(value) for value in series["norm_error"])
    if not _number(analysis.get("max_norm_error")) or not _close(analysis["max_norm_error"], expected_max_norm):
        raise RabiReaderVerificationError("rabi maximum norm error differs")

    model_fields = ("offset", "contrast", "x2p_amplitude_GHz", "rmse", "normalized_rmse", "r_squared", "residual_sum_squares")
    has_model = all(_number(analysis.get(key)) for key in model_fields)
    if has_model:
        if not peaks or analysis.get("first_peak_index") != peaks[0]:
            raise RabiReaderVerificationError("rabi first peak differs")
        peak = peaks[0]
        bracket = [axis[peak - 1], axis[peak + 1]]
        amplitude = float(analysis["x2p_amplitude_GHz"])
        if analysis.get("peak_bracket_GHz") != bracket or not float(bracket[0]) <= amplitude <= float(bracket[1]):
            raise RabiReaderVerificationError("rabi fit bracket differs")
        fitted = analysis.get("fitted_P1")
        if not isinstance(fitted, list) or len(fitted) != len(axis):
            raise RabiReaderVerificationError("rabi fitted series is invalid")
        offset, contrast = float(analysis["offset"]), float(analysis["contrast"])
        expected_fitted = [offset + contrast * math.sin(math.pi * float(value) / (2.0 * amplitude)) ** 2 for value in axis]
        if any(not _number(actual) or not _close(actual, expected) for actual, expected in zip(fitted, expected_fitted)):
            raise RabiReaderVerificationError("rabi fitted series differs")
        residual = [actual - float(observed) for actual, observed in zip(expected_fitted, series["P1"])]
        rss = sum(value * value for value in residual)
        rmse = math.sqrt(rss / len(residual))
        spread = max(series["P1"]) - min(series["P1"])
        normalized = rmse / max(float(spread), 1e-12)
        mean = sum(float(value) for value in series["P1"]) / len(series["P1"])
        total = sum((float(value) - mean) ** 2 for value in series["P1"])
        r_squared = 1.0 - rss / total if total else 1.0
        if not all((_close(analysis["residual_sum_squares"], rss), _close(analysis["rmse"], rmse), _close(analysis["normalized_rmse"], normalized), _close(analysis["r_squared"], r_squared))):
            raise RabiReaderVerificationError("rabi fit metrics differ")
        candidate_index = min(range(len(axis)), key=lambda item: abs(float(axis[item]) - amplitude))
        if analysis.get("candidate_distance_to_edge_steps") != min(candidate_index, len(axis) - candidate_index - 1) or not _number(analysis.get("candidate_leakage")) or not _close(analysis["candidate_leakage"], series["leakage"][candidate_index]):
            raise RabiReaderVerificationError("rabi candidate quality differs")
        curve = analysis.get("dense_fit_curve")
        if not isinstance(curve, Mapping) or set(curve) != {"amplitude_GHz", "P1"} or not isinstance(curve["amplitude_GHz"], list) or not isinstance(curve["P1"], list) or len(curve["amplitude_GHz"]) != 201 or len(curve["P1"]) != 201:
            raise RabiReaderVerificationError("rabi dense fit curve is invalid")
        expected_dense_x = [float(axis[0]) + (float(axis[-1]) - float(axis[0])) * index / 200.0 for index in range(201)]
        expected_dense_y = [offset + contrast * math.sin(math.pi * value / (2.0 * amplitude)) ** 2 for value in expected_dense_x]
        if any(not _number(actual) or not _close(actual, expected) for actual, expected in zip(curve["amplitude_GHz"], expected_dense_x)) or any(not _number(actual) or not _close(actual, expected) for actual, expected in zip(curve["P1"], expected_dense_y)):
            raise RabiReaderVerificationError("rabi dense fit curve differs")
        if type(analysis.get("optimizer_nfev")) is not int or analysis["optimizer_nfev"] <= 0:
            raise RabiReaderVerificationError("rabi optimizer evidence is invalid")
        expected_reason = None if analysis["fit_converged"] else "fit_not_converged"
        if analysis.get("reason") != expected_reason:
            raise RabiReaderVerificationError("rabi fit status differs")
        return

    failure_fields = ("offset", "contrast", "x2p_amplitude_GHz", "rmse", "normalized_rmse", "r_squared", "candidate_distance_to_edge_steps", "candidate_leakage", "optimizer_nfev", "residual_sum_squares")
    if analysis["fit_converged"] or any(analysis.get(key) is not None for key in failure_fields) or analysis.get("fitted_P1") != [] or analysis.get("dense_fit_curve") is not None:
        raise RabiReaderVerificationError("rabi failed fit payload is invalid")
    reason = analysis.get("reason")
    if reason == "first_peak_not_found":
        if peaks or analysis.get("first_peak_index") is not None or analysis.get("peak_bracket_GHz") is not None:
            raise RabiReaderVerificationError("rabi missing-peak evidence differs")
    elif reason in {"fit_bounds_do_not_intersect_peak_bracket", "fit_failed"}:
        if not peaks or analysis.get("first_peak_index") != peaks[0] or analysis.get("peak_bracket_GHz") != [axis[peaks[0] - 1], axis[peaks[0] + 1]]:
            raise RabiReaderVerificationError("rabi failed fit bracket differs")
    else:
        raise RabiReaderVerificationError("rabi failed fit reason is invalid")


def _candidate(workflow: Mapping[str, Any], dataset_sha: str) -> None:
    candidates = workflow.get("candidates")
    request = workflow["request"]
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], Mapping):
        raise RabiReaderVerificationError("rabi candidate count is invalid")
    candidate = candidates[0]
    try:
        normalized = normalize_calibration_candidate(candidate)
    except Exception as exc:
        raise RabiReaderVerificationError("rabi candidate protocol is invalid") from exc
    if dict(candidate) != normalized:
        raise RabiReaderVerificationError("rabi candidate is not normalized")
    changes = normalized.get("changes")
    expected_path = f"calibration_values.waveform_registry.settings.{request.get('setting_id')}.amplitude_GHz"
    if normalized.get("schema") != "calibration_candidate_v1" or not isinstance(normalized.get("candidate_id"), str) or not normalized["candidate_id"] or normalized.get("target") != request.get("target") or normalized.get("calibration_subjects") != [request.get("target")] or normalized.get("candidate_type") != "xy2_amplitude" or normalized.get("recommendation_eligible") is not workflow.get("recommendation_eligible") or normalized.get("source_dataset_sha256s") != [dataset_sha] or normalized.get("quality_metrics") != workflow.get("analysis") or not isinstance(changes, list) or len(changes) != 1 or (workflow.get("recommendation_eligible") and normalized.get("reason") is not None) or (not workflow.get("recommendation_eligible") and not isinstance(normalized.get("reason"), str)):
        raise RabiReaderVerificationError("rabi candidate binding is invalid")
    change = changes[0]
    proposed = workflow["analysis"].get("x2p_amplitude_GHz")
    expected_proposed = request.get("setting_amplitude_GHz") if proposed is None else proposed
    resource = {"owner": request.get("target"), "resource_type": "waveform_setting", "resource_id": request.get("setting_id")}
    if not isinstance(change, Mapping) or change.get("parameter_path") != expected_path or change.get("unit") != "GHz" or change.get("configuration_resource") != resource or normalized.get("configuration_resources") != [resource] or not _number(change.get("current_value")) or not _number(change.get("proposed_value")) or not _number(request.get("setting_amplitude_GHz")) or not math.isclose(float(change["current_value"]), float(request["setting_amplitude_GHz"]), rel_tol=0.0, abs_tol=1e-12) or not math.isclose(float(change["proposed_value"]), float(expected_proposed), rel_tol=0.0, abs_tol=1e-12) or (workflow.get("recommendation_eligible") and request.get("analysis_policy_approved") is not True) or (proposed is None and workflow.get("recommendation_eligible")):
        raise RabiReaderVerificationError("rabi candidate value is invalid")


def _batch(reader: EvidenceReader, workflow, dataset, request, head, paths) -> None:
    binding = workflow.get("runtime_batch")
    if not isinstance(binding, Mapping) or set(binding) != {"batch_id", "request_sha256", "head_sha256", "status", "attempt_count", "reused_point_count", "point_count"} or binding.get("batch_id") != workflow.get("run_id") or binding.get("status") != "completed" or binding.get("point_count") != len(dataset["points"]):
        raise RabiReaderVerificationError("rabi runtime batch summary is invalid")
    semantic = {key: value for key, value in request.items() if key not in {"metadata", "request_sha256"}}
    if request.get("batch_id") != workflow.get("run_id") or request.get("request_sha256") != sha256_json(semantic) or binding.get("request_sha256") != request.get("request_sha256") or request.get("experiment_request") != workflow.get("request") or request.get("execution", {}).get("readout_qubit") != [[workflow["request"]["target"]]] or request.get("execution", {}).get("execution_profile") != workflow.get("claim", {}).get("execution_profile") or head.get("status") != "completed" or head.get("failure") is not None or binding.get("head_sha256") != _sha(canonical_json_bytes(head)):
        raise RabiReaderVerificationError("rabi runtime batch request binding is invalid")
    circuits = request.get("circuits")
    completed = head.get("completed_points")
    if not isinstance(circuits, list) or not isinstance(completed, list) or len(circuits) != len(dataset["points"]) or len(completed) != len(circuits):
        raise RabiReaderVerificationError("rabi runtime batch point count is invalid")
    used = set(_ROOT) | {"execution/batch/request.json", "execution/batch/head.json"}
    for index, (circuit, done, point) in enumerate(zip(circuits, completed, dataset["points"])):
        source = f"SET {workflow['request']['target']} setting.active_xy2_setting.amplitude_GHz {_qcis_float(workflow['request']['axis']['values'][index])}\nX2P {workflow['request']['target']}\nX2P {workflow['request']['target']}\n"
        receipt_path = f"execution/batch/{done.get('receipt_path')}" if isinstance(done, Mapping) else ""
        if not isinstance(circuit, Mapping) or circuit.get("point_index") != index or circuit.get("circuit_id") != point["circuit_id"] or circuit.get("circuit_sha256") != point["circuit_sha256"] or circuit.get("qcis_source") != source or not isinstance(done, Mapping) or set(done) != {"point_index", "circuit_id", "receipt_path", "receipt_raw_sha256"} or done.get("point_index") != index or receipt_path not in paths or not _sha_text(done.get("receipt_raw_sha256")):
            raise RabiReaderVerificationError("rabi runtime circuit sequence is invalid")
        raw = _read(reader, receipt_path)
        if _sha(raw) != done["receipt_raw_sha256"]:
            raise RabiReaderVerificationError("rabi point receipt hash differs")
        receipt = _json(raw, "point receipt")
        result = receipt.get("result")
        if receipt.get("batch_id") != workflow["run_id"] or receipt.get("point_index") != index or receipt.get("circuit_id") != point["circuit_id"] or receipt.get("circuit_sha256") != point["circuit_sha256"] or not isinstance(result, Mapping) or result.get("circuit_id") != point["circuit_id"] or result.get("circuit_sha256") != point["circuit_sha256"] or result.get("overlay_sha256") != point["overlay_sha256"]:
            raise RabiReaderVerificationError("rabi point receipt binding is invalid")
        dressed, series = result.get("dressed_populations"), dataset["series"][dataset["target"]]
        if not isinstance(dressed, Mapping) or any(not _number(dressed.get(key)) for key in ("population_000", "population_100", "population_001", "population_101")) or not _number(result.get("leakage")) or not _number(result.get("norm_error")) or not math.isclose(float(result["leakage"]), float(series["leakage"][index]), rel_tol=0.0, abs_tol=1e-12) or not math.isclose(float(result["norm_error"]), float(series["norm_error"][index]), rel_tol=0.0, abs_tol=1e-12):
            raise RabiReaderVerificationError("rabi point result values differ")
        readout, probabilities = result.get("readout_qubit"), result.get("readout_probabilities")
        if readout != [[workflow["request"]["target"]]] or not isinstance(probabilities, list) or len(probabilities) != 1 or not isinstance(probabilities[0], Mapping) or probabilities[0].get("qagents") != [workflow["request"]["target"]] or not isinstance(probabilities[0].get("probabilities"), Mapping):
            raise RabiReaderVerificationError("rabi point readout binding is invalid")
        probability = probabilities[0]["probabilities"]
        if set(probability) != {"P0", "P1"} or any(not _number(probability.get(key)) for key in ("P0", "P1")) or not math.isclose(float(probability["P0"]), float(series["P0"][index]), rel_tol=0.0, abs_tol=1e-12) or not math.isclose(float(probability["P1"]), float(series["P1"][index]), rel_tol=0.0, abs_tol=1e-12):
            raise RabiReaderVerificationError("rabi point probability differs")
        computational = sum(float(dressed[key]) for key in ("population_000", "population_100", "population_001", "population_101"))
        if not math.isclose(float(series["P0"][index]) + float(series["P1"][index]), computational, rel_tol=0.0, abs_tol=1e-12) or not math.isclose(float(series["leakage"][index]), max(0.0, 1.0 - computational), rel_tol=0.0, abs_tol=1e-12):
            raise RabiReaderVerificationError("rabi dressed population closure differs")
        used.add(receipt_path)
        evidence = receipt.get("evidence")
        if not isinstance(evidence, list) or len(evidence) != 2:
            raise RabiReaderVerificationError("rabi point evidence is invalid")
        for kind, item in zip(("circuit", "model"), evidence, strict=True):
            if not isinstance(item, Mapping) or item.get("kind") != kind or not isinstance(item.get("path"), str) or not isinstance(item.get("files"), list):
                raise RabiReaderVerificationError("rabi evidence binding is invalid")
            for row in item["files"]:
                if not isinstance(row, Mapping) or set(row) != {"path", "byte_length", "raw_sha256"} or not isinstance(row["path"], str) or type(row["byte_length"]) is not int or row["byte_length"] < 0 or not _sha_text(row["raw_sha256"]):
                    raise RabiReaderVerificationError("rabi evidence inventory is invalid")
                evidence_path = f"execution/{item['path'].rstrip('/')}/{row['path']}"
                if evidence_path not in paths or _digest(reader, evidence_path) != (row["byte_length"], row["raw_sha256"]):
                    raise RabiReaderVerificationError("rabi evidence file differs")
                used.add(evidence_path)
    if set(paths) != used:
        raise RabiReaderVerificationError("rabi evidence contains unbound execution files")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _finite(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    if isinstance(value, list):
        for item in value:
            _finite(item)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _finite(item)
        return
    raise RabiReaderVerificationError("rabi evidence contains an invalid value")


def _number(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(float(value))


def _close(left: Any, right: Any) -> bool:
    return _number(left) and _number(right) and math.isclose(
        float(left), float(right), rel_tol=1e-10, abs_tol=1e-12
    )


def _sha_text(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789ABCDEF" for char in value)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest().upper()


def _wrap(value: float) -> float:
    return math.remainder(value, 2.0 * math.pi)


def _digest(reader: EvidenceReader, path: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    length = 0
    try:
        with reader.open_binary(path) as stream:
            while chunk := stream.read(64 * 1024):
                if not isinstance(chunk, bytes):
                    raise ValueError("non-bytes evidence stream")
                length += len(chunk)
                digest.update(chunk)
    except Exception as exc:
        raise RabiReaderVerificationError("cannot stream rabi evidence") from exc
    return length, digest.hexdigest().upper()


def _qcis_float(value: float) -> str:
    """The frozen QCIS decimal form, kept local to avoid compiler coupling."""
    if float(value) == 0.0:
        return "0"
    token = repr(float(value))
    if "e" not in token and "E" not in token:
        return token[:-2] if token.endswith(".0") else token
    mantissa, exponent = token.lower().split("e")
    return f"{mantissa[:-2] if mantissa.endswith('.0') else mantissa}e{int(exponent)}"


__all__ = ["RabiReaderVerificationError", "verify_qubit_rabi_scan_evidence"]
