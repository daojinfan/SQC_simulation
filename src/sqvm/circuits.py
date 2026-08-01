"""QCIS circuit execution facade with circuit-local configuration overlays."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
from itertools import product
import json
import math
import os
from pathlib import Path
import re
import shutil
from types import MappingProxyType
from typing import Any, Callable
import uuid

import numpy as np

from sqvm.qcis import QCISCompilation, compile_qcis
from sqvm.hamiltonian.provenance import canonical_json_bytes as artifact_canonical_json_bytes
from sqvm.qcis.canonical import (
    canonical_float,
    parse_canonical_float,
    sha256_bytes,
    sha256_json,
    validate_canonical_source,
)
from sqvm.runtime.stage71 import run_bounded_model_point, verify_bounded_model_point
from sqvm.runtime.calibration_scan import (
    QUALIFICATION_SCOPE as CALIBRATION_SCAN_SCOPE,
    run_calibration_scan_point,
    verify_calibration_scan_point,
)
from sqvm.runtime.calibration_model import calibration_model_configuration_sha256
from sqvm.runtime.publication import publish_calibration_directory
from sqvm.runtime.storage import write_canonical_new


_CIRCUIT_ID = re.compile(r"[a-z][a-z0-9_]{0,63}$")
_SETTING_PATH = re.compile(
    r"setting\.active_[a-z0-9_]+_setting(?:\.[a-z][a-zA-Z0-9_]*)+$"
)
_AUTHORITY_NAMES = (
    "instruction_profile",
    "qagent_registry",
    "gate_configuration",
    "waveform_registry",
    "clock",
    "compiler",
)
_IDENTITY_FIELDS = frozenset(
    {
        "setting_id",
        "target",
        "revision",
        "calibration_run_id",
        "status",
        "setting_hash",
        "gate_type",
        "transition",
        "mapper_id",
        "mapper_type",
        "wave_index",
    }
)
_RESULT_ARRAYS = {
    "population_000": ("<f8", "observables/population_000.bin"),
    "population_100": ("<f8", "observables/population_100.bin"),
    "population_001": ("<f8", "observables/population_001.bin"),
    "population_101": ("<f8", "observables/population_101.bin"),
    "leakage": ("<f8", "observables/leakage.bin"),
    "norm_error": ("<f8", "observables/norm_error.bin"),
}
_MAX_CIRCUITS_PER_CALL = 64
_DEFAULT_CIRCUIT_SAMPLE_BUDGET = 64
_EXECUTION_EVIDENCE_DIR = "circuit_execution"
_EXECUTION_SCHEMA_VERSION = "0.2"
_WINDOWS_DIRECTORY_PATH_LIMIT = 248
_STAGING_UUID_HEX = "f" * 32


class CircuitReasonCode(StrEnum):
    EMPTY_BATCH = "CIRCUIT_EMPTY_BATCH"
    BATCH_LIMIT_EXCEEDED = "CIRCUIT_BATCH_LIMIT_EXCEEDED"
    DUPLICATE_ID = "CIRCUIT_DUPLICATE_ID"
    INVALID_ID = "CIRCUIT_INVALID_ID"
    SET_SYNTAX_INVALID = "CIRCUIT_SET_SYNTAX_INVALID"
    SET_POSITION_INVALID = "CIRCUIT_SET_POSITION_INVALID"
    SET_PATH_INVALID = "CIRCUIT_SET_PATH_INVALID"
    SET_PATH_NOT_ALLOWED = "CIRCUIT_SET_PATH_NOT_ALLOWED"
    SET_VALUE_INVALID = "CIRCUIT_SET_VALUE_INVALID"
    SET_TARGET_INVALID = "CIRCUIT_SET_TARGET_INVALID"
    CONFIG_AUTHORITY_INVALID = "CIRCUIT_CONFIG_AUTHORITY_INVALID"
    READOUT_QUBIT_INVALID = "CIRCUIT_READOUT_QUBIT_INVALID"
    RESULT_EVIDENCE_INVALID = "CIRCUIT_RESULT_EVIDENCE_INVALID"
    OUTPUT_PATH_TOO_LONG = "CIRCUIT_OUTPUT_PATH_TOO_LONG"


class CircuitExecutionProfile(StrEnum):
    BOUNDED_SMOKE = "bounded_smoke"
    CALIBRATION_SCAN = "calibration_scan"


class CircuitExecutionError(ValueError):
    def __init__(self, code: CircuitReasonCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else str(code))


@dataclass(frozen=True, slots=True)
class QCISCircuit:
    circuit_id: str
    source: str


@dataclass(frozen=True, slots=True)
class CircuitExecutionContext:
    authorities: Mapping[str, Any]
    idle_flux_phi0: Mapping[str, float]
    settable_paths: frozenset[str] = frozenset()
    initial_state_id: str = "lab_ground"
    observable_set_id: str = "dressed_computational_populations_v1"
    platform_snapshot_id: str | None = None
    platform_snapshot_content_sha256: str | None = None
    authority_context_sha256: str | None = None
    calibration_model_configuration: Mapping[str, Any] | None = None
    platform_configuration: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class QubitProbabilities:
    p0: float
    p1: float

    def to_dict(self) -> dict[str, float | str]:
        return {
            "P0": self.p0,
            "P1": self.p1,
            "computational_population": self.p0 + self.p1,
            "normalization": "computational_subspace_nonconditional",
        }


@dataclass(frozen=True, slots=True)
class ReadoutProbabilities:
    """Ordered dressed-computational projection selected by readout_qubit."""

    qagents: tuple[str, ...]
    probabilities: Mapping[str, float]

    @property
    def computational_population(self) -> float:
        return sum(self.probabilities.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "qagents": list(self.qagents),
            "probabilities": dict(self.probabilities),
            "computational_population": self.computational_population,
            "normalization": "computational_subspace_nonconditional",
        }


@dataclass(frozen=True, slots=True)
class DressedPopulations:
    population_000: float
    population_100: float
    population_001: float
    population_101: float

    @property
    def computational_population(self) -> float:
        return (
            self.population_000
            + self.population_100
            + self.population_001
            + self.population_101
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "population_000": self.population_000,
            "population_100": self.population_100,
            "population_001": self.population_001,
            "population_101": self.population_101,
            "computational_population": self.computational_population,
        }


@dataclass(frozen=True, slots=True)
class CompiledCircuit:
    circuit: QCISCircuit
    circuit_sha256: str
    executable_source: str
    initial_state_id: str
    observable_set_id: str
    settable_paths_sha256: str
    overlay_sha256: str
    overlays: tuple[Mapping[str, Any], ...]
    compilation: QCISCompilation
    platform_context: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class _ReadoutSelection:
    requested: tuple[tuple[str, ...], ...]
    effective: tuple[tuple[str, ...], ...]
    target_components: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CircuitResult:
    circuit_id: str
    circuit_sha256: str
    executable_qcis_sha256: str
    overlay_sha256: str
    dressed_populations: DressedPopulations
    readout_qubit: tuple[tuple[str, ...], ...]
    readout_probabilities: tuple[ReadoutProbabilities, ...]
    leakage: float
    norm_error: float
    evidence_root: Path
    model_evidence_root: Path
    receipt_sha256: str
    qualification_scope: str

    @property
    def probabilities(self) -> Mapping[str, QubitProbabilities]:
        """Backward-compatible view of requested singleton readout groups."""

        values = {
            result.qagents[0]: QubitProbabilities(
                result.probabilities["P0"],
                result.probabilities["P1"],
            )
            for result in self.readout_probabilities
            if len(result.qagents) == 1
        }
        return MappingProxyType(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "circuit_id": self.circuit_id,
            "circuit_sha256": self.circuit_sha256,
            "executable_qcis_sha256": self.executable_qcis_sha256,
            "overlay_sha256": self.overlay_sha256,
            "dressed_populations": self.dressed_populations.to_dict(),
            "readout_qubit": [list(group) for group in self.readout_qubit],
            "readout_probabilities": [value.to_dict() for value in self.readout_probabilities],
            "probabilities": {
                target: value.to_dict() for target, value in self.probabilities.items()
            },
            "leakage": self.leakage,
            "norm_error": self.norm_error,
            "evidence_root": str(self.evidence_root),
            "model_evidence_root": str(self.model_evidence_root),
            "receipt_sha256": self.receipt_sha256,
            "qualification_scope": self.qualification_scope,
        }


def compile_circuit(
    circuit: QCISCircuit,
    context: CircuitExecutionContext,
    *,
    max_samples: int | None = None,
) -> CompiledCircuit:
    """Compile one QCIS circuit after applying its non-persistent SET preamble."""

    _validate_circuit_id(circuit.circuit_id)
    _validate_context(context)
    executable_source, raw_overlays = _split_set_preamble(circuit.source)
    authorities, overlays = _apply_overlays(context, raw_overlays)
    circuit_sha256 = sha256_bytes(circuit.source.encode("utf-8"))
    settable_paths_sha256 = sha256_json(sorted(context.settable_paths))
    overlay_payload = {
        "schema_version": "0.1",
        "circuit_sha256": circuit_sha256,
        "settable_paths_sha256": settable_paths_sha256,
        "entries": [dict(entry) for entry in overlays],
    }
    overlay_sha256 = sha256_json(overlay_payload)
    template_id = f"circuit_{circuit_sha256.lower()}"
    authorities["templates"] = {
        template_id: {"source": executable_source, "bindings": {}}
    }
    macro_ops = {
        "X", "Y", "X2P", "X2M", "Y2P", "Y2M", "XY", "XY2P", "XY2M",
        "RX", "RY", "RXY", "X12", "CZ", "FSIM",
    }
    uses_macro = any(line.split(" ", 1)[0] in macro_ops for line in executable_source[:-1].split("\n"))
    expected_names = set(_AUTHORITY_NAMES)
    if uses_macro and isinstance(authorities.get("calibration"), Mapping):
        expected_names.add("calibration")
    authorities["expected_sha256"] = {
        name: sha256_json(authorities[name]) for name in sorted(expected_names)
    }
    program = {
        "program_schema_version": "0.3",
        "instruction_set_id": "qcis_stage7_calibration_v3",
        "template_id": template_id,
        "template_sha256": sha256_bytes(executable_source.encode("utf-8")),
        "source_format": "qcis_template",
        "source": executable_source,
        "bindings": {},
    }
    compilation = compile_qcis(
        program,
        authorities,
        idle_flux={name: float(context.idle_flux_phi0[name]) for name in ("q1", "q2", "c")},
        max_samples=_circuit_sample_budget(context, max_samples),
    )
    return CompiledCircuit(
        circuit,
        circuit_sha256,
        executable_source,
        context.initial_state_id,
        context.observable_set_id,
        settable_paths_sha256,
        overlay_sha256,
        overlays,
        compilation,
        MappingProxyType({
            "snapshot_id": context.platform_snapshot_id,
            "snapshot_content_sha256": context.platform_snapshot_content_sha256,
            "authority_context_sha256": context.authority_context_sha256,
            "calibration_model_configuration_sha256": calibration_model_configuration_sha256(
                context.calibration_model_configuration
            ) if context.calibration_model_configuration is not None else None,
            "platform_configuration_sha256": (
                sha256_json(_plain(context.platform_configuration))
                if context.platform_configuration is not None
                else None
            ),
        }) if context.platform_snapshot_id is not None else None,
    )


def run_circuits(
    circuits: Sequence[QCISCircuit],
    context: CircuitExecutionContext,
    output_root: str | Path,
    repository_root: str | Path | None = None,
    *,
    readout_qubit: Sequence[Sequence[str]] = ((),),
    timeout_s: float = 900.0,
    max_circuits: int = _MAX_CIRCUITS_PER_CALL,
    execution_profile: CircuitExecutionProfile = CircuitExecutionProfile.BOUNDED_SMOKE,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[CircuitResult, ...]:
    """Run ordered QCIS circuits through an explicit model-execution profile."""

    if isinstance(circuits, (str, bytes)) or not isinstance(circuits, Sequence) or not circuits:
        _fail(CircuitReasonCode.EMPTY_BATCH, "circuits must be a nonempty sequence")
    if any(not isinstance(circuit, QCISCircuit) for circuit in circuits):
        _fail(CircuitReasonCode.INVALID_ID, "each item must be QCISCircuit")
    ids = [circuit.circuit_id for circuit in circuits]
    for circuit_id in ids:
        _validate_circuit_id(circuit_id)
    if len(ids) != len(set(ids)):
        _fail(CircuitReasonCode.DUPLICATE_ID, "circuit_id values must be unique")
    if (
        isinstance(max_circuits, bool)
        or not isinstance(max_circuits, int)
        or not 1 <= max_circuits <= _MAX_CIRCUITS_PER_CALL
        or len(circuits) > max_circuits
    ):
        _fail(
            CircuitReasonCode.BATCH_LIMIT_EXCEEDED,
            f"batch size exceeds the admitted limit ({_MAX_CIRCUITS_PER_CALL})",
        )
    _validate_context(context)
    if not isinstance(execution_profile, CircuitExecutionProfile):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "execution profile is invalid")
    if progress_callback is not None and not callable(progress_callback):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "progress callback is invalid")
    readout_selection = _normalize_readout_qubit(readout_qubit, context)
    _preflight_windows_path_budget(output_root, ids, execution_profile)
    compiled_circuits = tuple(compile_circuit(circuit, context) for circuit in circuits)
    results: list[CircuitResult] = []
    for index, compiled in enumerate(compiled_circuits):
        circuit = compiled.circuit
        if progress_callback is not None:
            progress_callback(
                MappingProxyType(
                    {
                        "event": "circuit_started",
                        "completed": index,
                        "total": len(compiled_circuits),
                        "circuit_id": circuit.circuit_id,
                    }
                )
            )
        runner = (
            run_calibration_scan_point
            if execution_profile is CircuitExecutionProfile.CALIBRATION_SCAN
            else run_bounded_model_point
        )
        runner_kwargs: dict[str, Any] = {"timeout_s": timeout_s}
        if execution_profile is CircuitExecutionProfile.CALIBRATION_SCAN:
            runner_kwargs["model_configuration"] = context.calibration_model_configuration
            runner_kwargs["idle_flux_phi0"] = context.idle_flux_phi0
            control_values = _platform_control_values(context)
            if control_values is not None:
                runner_kwargs["control_values"] = control_values
        handle = runner(
            compiled.compilation,
            circuit.circuit_id,
            output_root,
            repository_root,
            **runner_kwargs,
        )
        arrays, observable_binding = _load_verified_final_observables(
            handle.artifact_root / "stage51" / "evolution"
        )
        dressed, leakage, norm_error = _final_values(arrays)
        readout_probabilities = _build_readout_probabilities(
            dressed, leakage, readout_selection
        )
        _validate_probabilities(readout_probabilities, leakage, norm_error)
        evidence_root, receipt_sha256 = _publish_circuit_execution_evidence(
            Path(output_root), compiled, handle, dressed, readout_selection,
            readout_probabilities, leakage, norm_error, observable_binding,
        )
        results.append(
            CircuitResult(
                circuit.circuit_id,
                compiled.circuit_sha256,
                compiled.compilation.concrete_source_sha256,
                compiled.overlay_sha256,
                dressed,
                readout_selection.effective,
                readout_probabilities,
                leakage,
                norm_error,
                evidence_root,
                handle.artifact_root,
                receipt_sha256,
                handle.qualification_scope,
            )
        )
        if progress_callback is not None:
            progress_callback(
                MappingProxyType(
                    {
                        "event": "circuit_completed",
                        "completed": index + 1,
                        "total": len(compiled_circuits),
                        "circuit_id": circuit.circuit_id,
                    }
                )
            )
    return tuple(results)


def verify_circuit_result(
    evidence_root: str | Path,
    context: CircuitExecutionContext,
    repository_root: str | Path | None = None,
) -> CircuitResult:
    """Recompile, replay-verify, and reopen one published circuit result."""

    root = Path(evidence_root).resolve()
    evidence = _read_canonical(root / "evidence.json")
    qcis = evidence.get("qcis")
    circuit_id = evidence.get("circuit_id")
    if not isinstance(qcis, Mapping) or not isinstance(circuit_id, str) or not isinstance(qcis.get("source"), str):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "published QCIS identity")
    compiled = compile_circuit(QCISCircuit(circuit_id, qcis["source"]), context)
    execution_contract = evidence.get("execution_contract")
    if not isinstance(execution_contract, Mapping):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "execution contract")
    requested_readout = execution_contract.get("readout_qubit_requested")
    if not isinstance(requested_readout, list):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "readout selector")
    readout_selection = _normalize_readout_qubit(requested_readout, context)
    model_binding = evidence.get("model_evidence")
    if not isinstance(model_binding, Mapping) or not isinstance(model_binding.get("relative_path"), str):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "model evidence binding")
    model_root = (root / model_binding["relative_path"]).resolve()
    try:
        qualification_scope = model_binding.get("qualification_scope")
        if qualification_scope == CALIBRATION_SCAN_SCOPE:
            verifier = verify_calibration_scan_point
        elif qualification_scope == "bounded_smoke_only":
            verifier = verify_bounded_model_point
        else:
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "unknown qualification scope")
        verifier_kwargs: dict[str, Any] = {}
        if qualification_scope == CALIBRATION_SCAN_SCOPE:
            verifier_kwargs["model_configuration"] = context.calibration_model_configuration
            verifier_kwargs["idle_flux_phi0"] = context.idle_flux_phi0
            control_values = _platform_control_values(context)
            if control_values is not None:
                verifier_kwargs["control_values"] = control_values
        handle = verifier(
            model_root,
            compiled.compilation,
            repository_root,
            **verifier_kwargs,
        )
    except Exception as exc:
        raise CircuitExecutionError(CircuitReasonCode.RESULT_EVIDENCE_INVALID, str(exc)) from exc
    _verify_circuit_execution_evidence(root, compiled, handle, readout_selection)
    arrays, _binding = _load_verified_final_observables(model_root / "stage51" / "evolution")
    dressed, leakage, norm_error = _final_values(arrays)
    readout_probabilities = _build_readout_probabilities(
        dressed, leakage, readout_selection
    )
    _validate_probabilities(readout_probabilities, leakage, norm_error)
    return CircuitResult(
        circuit_id,
        compiled.circuit_sha256,
        compiled.compilation.concrete_source_sha256,
        compiled.overlay_sha256,
        dressed,
        readout_selection.effective,
        readout_probabilities,
        leakage,
        norm_error,
        root,
        model_root,
        _raw_sha256(root / "receipt.json"),
        handle.qualification_scope,
    )


def _validate_circuit_id(value: str) -> None:
    if not isinstance(value, str) or not value.isascii() or _CIRCUIT_ID.fullmatch(value) is None:
        _fail(CircuitReasonCode.INVALID_ID, "circuit_id must match [a-z][a-z0-9_]{0,63}")


def _validate_context(context: CircuitExecutionContext) -> None:
    if not isinstance(context, CircuitExecutionContext):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "typed context is required")
    if not isinstance(context.authorities, Mapping):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "authorities must be a mapping")
    expected = context.authorities.get("expected_sha256")
    if not isinstance(expected, Mapping):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "expected_sha256 is required")
    base = set(_AUTHORITY_NAMES)
    allowed_sets = (base, base | {"calibration"})
    if set(expected) not in allowed_sets:
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "authority hash set is not exact")
    for name in expected:
        value = context.authorities.get(name)
        if not isinstance(value, Mapping) or expected.get(name) != sha256_json(_plain(value)):
            _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, f"{name} hash mismatch")
    idle = context.idle_flux_phi0
    if not isinstance(idle, Mapping) or set(idle) != {"q1", "q2", "c"}:
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "idle flux keys must be q1/q2/c")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in idle.values()
    ):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "idle flux values must be finite")
    if not isinstance(context.settable_paths, frozenset) or any(
        not isinstance(path, str) or not path.isascii() for path in context.settable_paths
    ):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "settable_paths must be a frozenset of ASCII paths")
    if context.initial_state_id != "lab_ground":
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "only lab_ground is admitted by the smoke backend")
    if context.observable_set_id != "dressed_computational_populations_v1":
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "unsupported observable_set_id")
    if context.calibration_model_configuration is not None:
        try:
            calibration_model_configuration_sha256(
                context.calibration_model_configuration
            )
        except ValueError as exc:
            raise CircuitExecutionError(
                CircuitReasonCode.CONFIG_AUTHORITY_INVALID,
                str(exc),
            ) from exc
    if context.platform_configuration is not None:
        configuration = context.platform_configuration
        if (
            not isinstance(configuration, Mapping)
            or set(configuration) != {"control_values", "calibration_values"}
            or not isinstance(configuration.get("control_values"), Mapping)
            or not isinstance(configuration.get("calibration_values"), Mapping)
        ):
            _fail(
                CircuitReasonCode.CONFIG_AUTHORITY_INVALID,
                "platform_configuration must contain control_values and calibration_values",
            )
        if (
            context.platform_snapshot_content_sha256 is not None
            and sha256_json(_plain(configuration))
            != context.platform_snapshot_content_sha256
        ):
            _fail(
                CircuitReasonCode.CONFIG_AUTHORITY_INVALID,
                "platform configuration hash does not match the Active snapshot",
            )
    platform = (context.platform_snapshot_id, context.platform_snapshot_content_sha256, context.authority_context_sha256)
    if any(value is not None for value in platform):
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9A-Fa-f]{64}", value) is not None for value in platform[1:]) or not isinstance(platform[0], str) or not platform[0]:
            _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "platform context binding is invalid")


def _platform_control_values(
    context: CircuitExecutionContext,
) -> Mapping[str, Any] | None:
    configuration = context.platform_configuration
    if configuration is None:
        return None
    control = configuration.get("control_values")
    if not isinstance(control, Mapping):
        _fail(
            CircuitReasonCode.CONFIG_AUTHORITY_INVALID,
            "Active platform control_values are unavailable",
        )
    return control


def _circuit_sample_budget(
    context: CircuitExecutionContext, override: int | None
) -> int:
    value: Any = override
    if value is None:
        value = _DEFAULT_CIRCUIT_SAMPLE_BUDGET
        configuration = context.platform_configuration
        if configuration is not None:
            control = configuration.get("control_values")
            acceptance = (
                control.get("acceptance") if isinstance(control, Mapping) else None
            )
            if isinstance(acceptance, Mapping) and (
                "max_formal_samples_per_scenario" in acceptance
            ):
                value = acceptance["max_formal_samples_per_scenario"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _fail(
            CircuitReasonCode.CONFIG_AUTHORITY_INVALID,
            "circuit sample budget must be a positive integer",
        )
    return value


def _normalize_readout_qubit(
    value: Sequence[Sequence[str]],
    context: CircuitExecutionContext,
) -> _ReadoutSelection:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "readout_qubit must be a nonempty nested sequence")
    requested: list[tuple[str, ...]] = []
    for raw_group in value:
        if isinstance(raw_group, (str, bytes)) or not isinstance(raw_group, Sequence):
            _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "each readout group must be a sequence")
        group = tuple(raw_group)
        if any(not isinstance(target, str) or not target.isascii() for target in group):
            _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "readout targets must be ASCII strings")
        requested.append(group)
    requested_tuple = tuple(requested)

    registry = context.authorities.get("qagent_registry")
    if not isinstance(registry, Mapping):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "qagent registry is missing")
    by_component: dict[str, str] = {}
    for target, raw in registry.items():
        if not isinstance(target, str) or not isinstance(raw, Mapping):
            continue
        component = raw.get("component")
        if component not in {"q1", "q2"} or not isinstance(raw.get("xy_channel"), str):
            continue
        if component in by_component:
            _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, f"duplicate {component} readout target")
        by_component[component] = target
    if set(by_component) != {"q1", "q2"}:
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "readout targets must resolve q1 and q2")
    target_components = {target: component for component, target in by_component.items()}

    if requested_tuple == ((),):
        effective = tuple((by_component[component],) for component in ("q1", "q2"))
    else:
        if any(not group for group in requested_tuple):
            _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "empty group is valid only as the [[]] default")
        seen: set[tuple[str, ...]] = set()
        for group in requested_tuple:
            if len(group) > len(target_components):
                _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "readout group exceeds backend qubit count")
            if len(group) != len(set(group)):
                _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "readout group contains a duplicate target")
            if any(target not in target_components for target in group):
                _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "unknown or non-qubit readout target")
            if group in seen:
                _fail(CircuitReasonCode.READOUT_QUBIT_INVALID, "duplicate readout group")
            seen.add(group)
        effective = requested_tuple
    return _ReadoutSelection(
        requested_tuple,
        effective,
        MappingProxyType(target_components),
    )


def _split_set_preamble(source: str) -> tuple[str, tuple[tuple[str, str, float], ...]]:
    validate_canonical_source(source, allow_placeholders=False)
    executable: list[str] = []
    overlays: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    body_started = False
    for line in source[:-1].split("\n"):
        tokens = line.split(" ")
        if tokens[0] != "SET":
            body_started = True
            executable.append(line)
            continue
        if body_started:
            _fail(CircuitReasonCode.SET_POSITION_INVALID, "SET is allowed only before executable instructions")
        if len(tokens) != 4:
            _fail(CircuitReasonCode.SET_SYNTAX_INVALID, "SET requires target, setting path, and value")
        target, path, token = tokens[1:]
        try:
            value = parse_canonical_float(token)
        except Exception as exc:
            raise CircuitExecutionError(CircuitReasonCode.SET_VALUE_INVALID, token) from exc
        if canonical_float(value) != token:
            _fail(CircuitReasonCode.SET_VALUE_INVALID, "SET value is not the canonical binary64 token")
        key = (target, path)
        if key in seen:
            _fail(CircuitReasonCode.SET_PATH_INVALID, "duplicate SET path in one circuit")
        seen.add(key)
        overlays.append((target, path, value))
    if not executable:
        _fail(CircuitReasonCode.SET_POSITION_INVALID, "circuit has no executable instruction")
    return "\n".join(executable) + "\n", tuple(overlays)


def _apply_overlays(
    context: CircuitExecutionContext,
    raw_overlays: tuple[tuple[str, str, float], ...],
) -> tuple[dict[str, Any], tuple[Mapping[str, Any], ...]]:
    authorities = copy.deepcopy(_plain(context.authorities))
    gate_configuration = authorities.get("gate_configuration")
    waveform_registry = authorities.get("waveform_registry")
    settings = waveform_registry.get("settings") if isinstance(waveform_registry, Mapping) else None
    qagents = authorities.get("qagent_registry")
    if not isinstance(gate_configuration, Mapping) or not isinstance(settings, Mapping) or not isinstance(qagents, Mapping):
        _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, "setting registries are incomplete")
    entries: list[dict[str, Any]] = []
    touched: dict[str, str] = {}
    for target, path, value in raw_overlays:
        if target not in qagents or target == "schema_version":
            _fail(CircuitReasonCode.SET_TARGET_INVALID, target)
        full_path = f"{target}.{path}"
        if full_path not in context.settable_paths:
            _fail(CircuitReasonCode.SET_PATH_NOT_ALLOWED, full_path)
        if _SETTING_PATH.fullmatch(path) is None:
            _fail(CircuitReasonCode.SET_PATH_INVALID, path)
        parts = path.split(".")
        active_key = parts[1]
        target_config = gate_configuration.get(target)
        if not isinstance(target_config, Mapping):
            _fail(CircuitReasonCode.SET_TARGET_INVALID, target)
        setting_id = target_config.get(active_key)
        setting = settings.get(setting_id) if isinstance(setting_id, str) else None
        if not isinstance(setting, dict):
            _fail(CircuitReasonCode.SET_PATH_INVALID, f"{target}.{active_key}")
        if str(setting_id) in touched:
            base_hash = touched[str(setting_id)]
        else:
            base_hash = setting.get("setting_hash")
            if not isinstance(base_hash, str) or base_hash not in {
                _setting_hash(setting),
                _legacy_setting_hash(setting),
            }:
                _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, f"{setting_id} base setting hash")
            if setting.get("status") != "accepted" or setting.get("target") != target:
                _fail(CircuitReasonCode.CONFIG_AUTHORITY_INVALID, f"{setting_id} is not an accepted target setting")
            touched[str(setting_id)] = base_hash
        parent: dict[str, Any] = setting
        field_path = parts[2:]
        if any(field in _IDENTITY_FIELDS for field in field_path):
            _fail(CircuitReasonCode.SET_PATH_INVALID, "identity fields are not settable")
        for field in field_path[:-1]:
            nested = parent.get(field)
            if not isinstance(nested, dict):
                _fail(CircuitReasonCode.SET_PATH_INVALID, full_path)
            parent = nested
        leaf = field_path[-1]
        current = parent.get(leaf)
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            _fail(CircuitReasonCode.SET_PATH_INVALID, f"{full_path} is not an existing finite numeric field")
        if type(current) is int:
            if not value.is_integer():
                _fail(CircuitReasonCode.SET_VALUE_INVALID, f"{full_path} requires an integer")
            effective: int | float = int(value)
        else:
            effective = float(value)
        parent[leaf] = effective
        entries.append(
            {
                "target": target,
                "path": path,
                "value": effective,
                "setting_id": setting_id,
                "base_setting_hash": base_hash,
            }
        )
    for setting_id, base_hash in touched.items():
        setting = settings[setting_id]
        setting["setting_hash"] = _setting_hash(setting)
        for entry in entries:
            if entry["setting_id"] == setting_id and entry["base_setting_hash"] == base_hash:
                entry["effective_setting_hash"] = setting["setting_hash"]
    expected = authorities["expected_sha256"]
    for name in tuple(expected):
        expected[name] = sha256_json(authorities[name])
    return authorities, tuple(MappingProxyType(entry) for entry in entries)


def _load_verified_final_observables(
    root: Path,
) -> tuple[Mapping[str, np.ndarray], Mapping[str, Any]]:
    inventory_path = root / "array_inventory.json"
    try:
        _reject_link(root)
        _reject_link(inventory_path)
        raw_inventory = inventory_path.read_bytes()
        inventory = json.loads(raw_inventory.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CircuitExecutionError(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "array inventory") from exc
    if raw_inventory != artifact_canonical_json_bytes(inventory) or not isinstance(inventory, Mapping):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "array inventory is not canonical")
    rows = inventory.get("arrays")
    if not isinstance(rows, list):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "array rows are missing")
    by_name = {row.get("name"): row for row in rows if isinstance(row, Mapping)}
    arrays: dict[str, np.ndarray] = {}
    bound_rows: dict[str, Any] = {}
    for name, (dtype, relative) in _RESULT_ARRAYS.items():
        row = by_name.get(name)
        if not isinstance(row, Mapping) or row.get("path") != relative or row.get("dtype") != dtype:
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, name)
        path = root / relative
        try:
            _reject_link(path.parent)
            _reject_link(path)
            raw = path.read_bytes()
        except OSError as exc:
            raise CircuitExecutionError(CircuitReasonCode.RESULT_EVIDENCE_INVALID, name) from exc
        if hashlib.sha256(raw).hexdigest().upper() != row.get("sha256") or len(raw) != row.get("byte_count"):
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, f"{name} hash")
        values = np.frombuffer(raw, dtype=dtype).copy(order="C")
        if values.size < 1 or not np.all(np.isfinite(values)):
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, f"{name} values")
        values.setflags(write=False)
        arrays[name] = values
        bound_rows[name] = {
            "path": relative,
            "dtype": dtype,
            "byte_count": len(raw),
            "sha256": row["sha256"],
        }
    binding = {
        "evolution_manifest_sha256": _raw_sha256(root / "manifest.json"),
        "evolution_receipt_sha256": _raw_sha256(root / "receipt.json"),
        "array_inventory_sha256": hashlib.sha256(raw_inventory).hexdigest().upper(),
        "arrays": bound_rows,
    }
    return MappingProxyType(arrays), MappingProxyType(binding)


def _validate_probabilities(
    readout_probabilities: Sequence[ReadoutProbabilities],
    leakage: float,
    norm_error: float,
) -> None:
    tolerance = 1.0e-8
    if not math.isfinite(leakage) or not math.isfinite(norm_error) or not -tolerance <= leakage <= 1.0 + tolerance or norm_error < 0.0:
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "leakage or norm")
    for result in readout_probabilities:
        expected_labels = {"P" + "".join(str(bit) for bit in bits) for bits in product((0, 1), repeat=len(result.qagents))}
        if set(result.probabilities) != expected_labels:
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "readout probability labels")
        if any(
            not math.isfinite(value) or not -tolerance <= value <= 1.0 + tolerance
            for value in result.probabilities.values()
        ):
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "readout probability values")
        if abs((result.computational_population + leakage) - 1.0) > tolerance:
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "readout probability normalization")


def _build_readout_probabilities(
    dressed: DressedPopulations,
    leakage: float,
    selection: _ReadoutSelection,
) -> tuple[ReadoutProbabilities, ...]:
    basis = {
        (0, 0): dressed.population_000,
        (1, 0): dressed.population_100,
        (0, 1): dressed.population_001,
        (1, 1): dressed.population_101,
    }
    component_index = {"q1": 0, "q2": 1}
    results: list[ReadoutProbabilities] = []
    for group in selection.effective:
        indexes = tuple(component_index[selection.target_components[target]] for target in group)
        values: dict[str, float] = {}
        for selected_bits in product((0, 1), repeat=len(group)):
            probability = sum(
                value
                for basis_bits, value in basis.items()
                if all(basis_bits[index] == selected for index, selected in zip(indexes, selected_bits))
            )
            label = "P" + "".join(str(bit) for bit in selected_bits)
            values[label] = probability
        results.append(ReadoutProbabilities(group, MappingProxyType(values)))
    return tuple(results)


def _legacy_probabilities(
    readout_probabilities: Sequence[ReadoutProbabilities],
) -> Mapping[str, QubitProbabilities]:
    return MappingProxyType(
        {
            result.qagents[0]: QubitProbabilities(
                result.probabilities["P0"],
                result.probabilities["P1"],
            )
            for result in readout_probabilities
            if len(result.qagents) == 1
        }
    )


def _validate_dressed_populations(populations: DressedPopulations, leakage: float) -> None:
    tolerance = 1.0e-8
    values = tuple(populations.to_dict().values())
    if any(not math.isfinite(value) or not -tolerance <= value <= 1.0 + tolerance for value in values):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "dressed populations")
    if abs(populations.computational_population + leakage - 1.0) > tolerance:
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "computational population normalization")


def _preflight_windows_path_budget(
    output_root: str | Path,
    circuit_ids: Sequence[str],
    execution_profile: CircuitExecutionProfile,
    *,
    platform_name: str | None = None,
) -> None:
    """Reject legacy-Windows output roots before compilation starts."""

    if (os.name if platform_name is None else platform_name) != "nt":
        return
    root = Path(output_root).resolve()
    longest = max(
        (
            path
            for circuit_id in circuit_ids
            for path in _planned_execution_paths(root, circuit_id, execution_profile)
        ),
        key=lambda path: len(str(path)),
    )
    if len(str(longest)) >= _WINDOWS_DIRECTORY_PATH_LIMIT:
        _fail(
            CircuitReasonCode.OUTPUT_PATH_TOO_LONG,
            "shorten output_root; planned execution path exceeds the Windows "
            f"{_WINDOWS_DIRECTORY_PATH_LIMIT}-character directory budget: {longest}",
        )


def _planned_execution_paths(
    root: Path,
    circuit_id: str,
    execution_profile: CircuitExecutionProfile,
) -> tuple[Path, ...]:
    if execution_profile is CircuitExecutionProfile.CALIBRATION_SCAN:
        point_staging = root / f".cs_{'0' * 8}"
        worker = ".calibration-worker"
    else:
        point_staging = root / f".{circuit_id}.staging.{_STAGING_UUID_HEX}"
        worker = ".stage51-worker"
    return (
        point_staging
        / "stage41"
        / f".control.staging.{_STAGING_UUID_HEX}"
        / "arrays"
        / "logical.xy_delta_GHz.q1.i.bin",
        point_staging
        / "stage51"
        / f".coefficient.staging.{_STAGING_UUID_HEX}"
        / "arrays"
        / "absolute_flux_q2.bin",
        point_staging
        / "stage51"
        / f"{worker}.{_STAGING_UUID_HEX}"
        / "result"
        / "observables"
        / "population_000.bin",
        root
        / _EXECUTION_EVIDENCE_DIR
        / f".p.{_STAGING_UUID_HEX}"
        / "verification_report.json",
        root / circuit_id / "stage51" / "evolution" / "observables" / "population_000.bin",
        root / _EXECUTION_EVIDENCE_DIR / circuit_id / "receipt.json",
    )


def _circuit_execution_staging(root: Path, token: str) -> Path:
    return root / f".p.{token}"


def _publish_circuit_execution_evidence(
    output_root: Path,
    compiled: CompiledCircuit,
    handle: Any,
    dressed: DressedPopulations,
    readout_selection: _ReadoutSelection,
    readout_probabilities: Sequence[ReadoutProbabilities],
    leakage: float,
    norm_error: float,
    observable_binding: Mapping[str, Any],
) -> tuple[Path, str]:
    root = output_root.resolve() / _EXECUTION_EVIDENCE_DIR
    target = root / compiled.circuit.circuit_id
    if target.exists():
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "circuit execution evidence already exists")
    root.mkdir(parents=True, exist_ok=True)
    staging = _circuit_execution_staging(root, uuid.uuid4().hex)
    try:
        staging.mkdir()
        model_manifest = _raw_sha256(handle.artifact_root / "manifest.json")
        model_receipt = _raw_sha256(handle.artifact_root / "receipt.json")
        if model_manifest != handle.manifest_sha256 or model_receipt != handle.receipt_sha256:
            _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "model evidence binding changed")
        probabilities = _legacy_probabilities(readout_probabilities)
        evidence = {
            "schema_version": _EXECUTION_SCHEMA_VERSION,
            "artifact_type": "qcis_circuit_execution_evidence",
            "status": "published",
            "circuit_id": compiled.circuit.circuit_id,
            "qcis": {
                "source": compiled.circuit.source,
                "source_sha256": compiled.circuit_sha256,
                "executable_source": compiled.executable_source,
                "executable_source_sha256": compiled.compilation.concrete_source_sha256,
            },
            "set_overlay": {
                "overlay_sha256": compiled.overlay_sha256,
                "settable_paths_sha256": compiled.settable_paths_sha256,
                "entries": [_plain(entry) for entry in compiled.overlays],
                "persistent_config_mutated": False,
            },
            "compilation": {
                "ast_sha256": compiled.compilation.plan.ast_sha256,
                "trace_sha256": compiled.compilation.plan.trace_sha256,
                "authority_sha256": sha256_json(_plain(compiled.compilation.plan.authority_sha256)),
            },
            "execution_contract": {
                "initial_state_id": compiled.initial_state_id,
                "observable_set_id": compiled.observable_set_id,
                "readout_qubit_requested": [list(group) for group in readout_selection.requested],
                "readout_qubit_effective": [list(group) for group in readout_selection.effective],
                "readout_target_components": dict(readout_selection.target_components),
                "result_selection_kind": "model_computational_population_projection",
            },
            "model_evidence": {
                "relative_path": f"../../{compiled.circuit.circuit_id}",
                "manifest_sha256": model_manifest,
                "receipt_sha256": model_receipt,
                "qualification_scope": handle.qualification_scope,
                "final_observable_binding": _plain(observable_binding),
            },
            "final_observables": {
                "dressed_populations": dressed.to_dict(),
                "readout_probabilities": [value.to_dict() for value in readout_probabilities],
                "qubit_computational_marginals": {
                    target_name: value.to_dict() for target_name, value in probabilities.items()
                },
                "leakage": leakage,
                "norm_error": norm_error,
            },
            "claim": {
                "model_evolution": True,
                "measurement": False,
                "readout": False,
                "calibration_eligible": False,
                "recommendation_eligible": False,
            },
        }
        if compiled.platform_context is not None:
            evidence["platform_configuration"] = dict(compiled.platform_context)
        evidence_sha256 = write_canonical_new(staging / "evidence.json", evidence)
        manifest_sha256 = write_canonical_new(
            staging / "manifest.json",
            {
                "schema_version": _EXECUTION_SCHEMA_VERSION,
                "artifact_type": "qcis_circuit_execution_manifest",
                "circuit_id": compiled.circuit.circuit_id,
                "evidence_sha256": evidence_sha256,
            },
        )
        receipt_sha256 = write_canonical_new(
            staging / "receipt.json",
            {
                "schema_version": _EXECUTION_SCHEMA_VERSION,
                "artifact_type": "qcis_circuit_execution_receipt",
                "status": "published",
                "circuit_id": compiled.circuit.circuit_id,
                "manifest_sha256": manifest_sha256,
                "qualification_scope": handle.qualification_scope,
                "recommendation_eligible": False,
            },
        )
        _verify_circuit_execution_evidence(staging, compiled, handle, readout_selection)
        publish_calibration_directory(staging, target)
        return target, receipt_sha256
    except CircuitExecutionError:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise CircuitExecutionError(CircuitReasonCode.RESULT_EVIDENCE_INVALID, str(exc)) from exc


def _verify_circuit_execution_evidence(
    root: Path,
    compiled: CompiledCircuit,
    handle: Any,
    readout_selection: _ReadoutSelection,
) -> None:
    if {path.name for path in root.iterdir()} != {"evidence.json", "manifest.json", "receipt.json"}:
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "circuit evidence file set")
    evidence = _read_canonical(root / "evidence.json")
    manifest = _read_canonical(root / "manifest.json")
    receipt = _read_canonical(root / "receipt.json")
    model_binding = evidence.get("model_evidence", {})
    model_root = (root / str(model_binding.get("relative_path", ""))).resolve()
    arrays, current_observable_binding = _load_verified_final_observables(
        model_root / "stage51" / "evolution"
    )
    dressed, leakage, norm_error = _final_values(arrays)
    readout_probabilities = _build_readout_probabilities(
        dressed, leakage, readout_selection
    )
    _validate_probabilities(readout_probabilities, leakage, norm_error)
    probabilities = _legacy_probabilities(readout_probabilities)
    expected_final_observables = {
        "dressed_populations": dressed.to_dict(),
        "readout_probabilities": [value.to_dict() for value in readout_probabilities],
        "qubit_computational_marginals": {
            target_name: value.to_dict() for target_name, value in probabilities.items()
        },
        "leakage": leakage,
        "norm_error": norm_error,
    }
    expected_qcis = {
        "source": compiled.circuit.source,
        "source_sha256": compiled.circuit_sha256,
        "executable_source": compiled.executable_source,
        "executable_source_sha256": compiled.compilation.concrete_source_sha256,
    }
    expected_overlay = {
        "overlay_sha256": compiled.overlay_sha256,
        "settable_paths_sha256": compiled.settable_paths_sha256,
        "entries": [_plain(entry) for entry in compiled.overlays],
        "persistent_config_mutated": False,
    }
    expected_compilation = {
        "ast_sha256": compiled.compilation.plan.ast_sha256,
        "trace_sha256": compiled.compilation.plan.trace_sha256,
        "authority_sha256": sha256_json(_plain(compiled.compilation.plan.authority_sha256)),
    }
    expected_execution_contract = {
        "initial_state_id": compiled.initial_state_id,
        "observable_set_id": compiled.observable_set_id,
        "readout_qubit_requested": [list(group) for group in readout_selection.requested],
        "readout_qubit_effective": [list(group) for group in readout_selection.effective],
        "readout_target_components": dict(readout_selection.target_components),
        "result_selection_kind": "model_computational_population_projection",
    }
    expected_model_binding = {
        "relative_path": f"../../{compiled.circuit.circuit_id}",
        "manifest_sha256": handle.manifest_sha256,
        "receipt_sha256": handle.receipt_sha256,
        "qualification_scope": handle.qualification_scope,
        "final_observable_binding": _plain(current_observable_binding),
    }
    expected_claim = {
        "model_evolution": True,
        "measurement": False,
        "readout": False,
        "calibration_eligible": False,
        "recommendation_eligible": False,
    }
    expected_manifest = {
        "schema_version": _EXECUTION_SCHEMA_VERSION,
        "artifact_type": "qcis_circuit_execution_manifest",
        "circuit_id": compiled.circuit.circuit_id,
        "evidence_sha256": _raw_sha256(root / "evidence.json"),
    }
    expected_receipt = {
        "schema_version": _EXECUTION_SCHEMA_VERSION,
        "artifact_type": "qcis_circuit_execution_receipt",
        "status": "published",
        "circuit_id": compiled.circuit.circuit_id,
        "manifest_sha256": _raw_sha256(root / "manifest.json"),
        "qualification_scope": handle.qualification_scope,
        "recommendation_eligible": False,
    }
    expected_evidence_keys = {
            "schema_version", "artifact_type", "status", "circuit_id", "qcis",
            "set_overlay", "compilation", "execution_contract", "model_evidence",
            "final_observables", "claim",
        }
    if compiled.platform_context is not None:
        expected_evidence_keys.add("platform_configuration")
    if (
        set(evidence) != expected_evidence_keys
        or evidence.get("schema_version") != _EXECUTION_SCHEMA_VERSION
        or evidence.get("artifact_type") != "qcis_circuit_execution_evidence"
        or evidence.get("status") != "published"
        or evidence.get("circuit_id") != compiled.circuit.circuit_id
        or evidence.get("qcis") != expected_qcis
        or evidence.get("set_overlay") != expected_overlay
        or evidence.get("compilation") != expected_compilation
        or evidence.get("execution_contract") != expected_execution_contract
        or model_binding != expected_model_binding
        or model_root != handle.artifact_root.resolve()
        or _raw_sha256(model_root / "manifest.json") != handle.manifest_sha256
        or _raw_sha256(model_root / "receipt.json") != handle.receipt_sha256
        or evidence.get("final_observables") != expected_final_observables
        or evidence.get("claim") != expected_claim
        or (compiled.platform_context is not None and evidence.get("platform_configuration") != dict(compiled.platform_context))
        or manifest != expected_manifest
        or receipt != expected_receipt
    ):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "circuit evidence verification")


def _final_values(
    arrays: Mapping[str, np.ndarray],
) -> tuple[DressedPopulations, float, float]:
    dressed = DressedPopulations(
        float(arrays["population_000"][-1]),
        float(arrays["population_100"][-1]),
        float(arrays["population_001"][-1]),
        float(arrays["population_101"][-1]),
    )
    leakage = float(arrays["leakage"][-1])
    norm_error = float(arrays["norm_error"][-1])
    if not math.isfinite(norm_error) or norm_error < 0.0:
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, "norm error")
    _validate_dressed_populations(dressed, leakage)
    return dressed, leakage, norm_error


def _read_canonical(path: Path) -> Mapping[str, Any]:
    _reject_link(path)
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, Mapping) or raw != artifact_canonical_json_bytes(value):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, path.name)
    return value


def _raw_sha256(path: Path) -> str:
    _reject_link(path)
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _reject_link(path: Path) -> None:
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        _fail(CircuitReasonCode.RESULT_EVIDENCE_INVALID, f"unsafe link: {path.name}")


def _setting_hash(setting: Mapping[str, Any]) -> str:
    """Match QCIS's accepted-record identity after resolver wave-index projection."""

    payload = {
        str(name): _plain(value)
        for name, value in setting.items()
        if name != "setting_hash"
    }
    return sha256_json(_without_generated_wave_index(payload))


def _legacy_setting_hash(setting: Mapping[str, Any]) -> str:
    """Accept historical records whose persisted hash predates wave-index projection."""

    return sha256_json({
        str(name): _plain(value)
        for name, value in setting.items()
        if name != "setting_hash"
    })


def _without_generated_wave_index(value: Any) -> Any:
    """Exclude resolver-only waveform compatibility fields at every nesting level."""

    if isinstance(value, Mapping):
        return {
            str(name): _without_generated_wave_index(item)
            for name, item in value.items()
            if name != "wave_index"
        }
    if isinstance(value, (tuple, list)):
        return [_without_generated_wave_index(item) for item in value]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(name): _plain(item) for name, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _fail(code: CircuitReasonCode, detail: str) -> None:
    raise CircuitExecutionError(code, detail)


__all__ = [
    "CircuitExecutionContext",
    "CircuitExecutionError",
    "CircuitReasonCode",
    "CircuitResult",
    "CompiledCircuit",
    "DressedPopulations",
    "QCISCircuit",
    "QubitProbabilities",
    "ReadoutProbabilities",
    "compile_circuit",
    "run_circuits",
    "verify_circuit_result",
]
