"""Superconducting quantum virtual machine."""

from sqvm.control import (
    build_control_channel_approval,
    build_control_channel_manifest,
    load_control_channel_registry,
    load_control_chain_config,
    load_logical_schedule,
    validate_logical_schedule,
    compile_control_schedule,
    write_control_signal_artifacts,
    verify_control_signal,
    validate_control_channel_approval,
    validate_control_channel_compatibility,
    validate_control_channel_manifest,
)

__all__ = [
    "build_control_channel_approval",
    "build_control_channel_manifest",
    "load_control_channel_registry",
    "load_control_chain_config",
    "load_logical_schedule",
    "validate_logical_schedule",
    "compile_control_schedule",
    "write_control_signal_artifacts",
    "verify_control_signal",
    "build_stage4_acceptance_approval",
    "validate_stage4_acceptance_approval",
    "validate_control_channel_approval",
    "validate_control_channel_compatibility",
    "validate_control_channel_manifest",
    "verify_q1_q2_coupling",
    "verify_static_spectrum",
    "CircuitExecutionContext",
    "CircuitExecutionProfile",
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
    "SpectroscopyAxis",
    "SpectroscopyDataset",
    "SpectroscopyMode",
    "SpectroscopyPulsePolicy",
    "SpectroscopyRequest",
    "analyze_qubit_spectroscopy",
    "expand_qubit_spectroscopy_points",
    "run_qubit_spectroscopy",
    "SpectroscopyCalibrationDecision",
    "SpectroscopyCalibrationPolicy",
    "SpectroscopyCalibrationRequest",
    "SpectroscopyCalibrationRun",
    "context_with_spectroscopy_calibration",
    "decide_qubit_spectroscopy_calibration",
    "run_qubit_spectroscopy_calibration",
    "verify_qubit_spectroscopy_calibration",
    "verify_qubit_spectroscopy_calibration_decision",
    "CalibrationExperimentError",
    "run_active_qubit_spectroscopy_calibration",
]

__version__ = "0.1.0"


def __getattr__(name: str):
    if name in {
        "CalibrationExperimentError",
        "run_active_qubit_spectroscopy_calibration",
    }:
        from sqvm.calibration.api import (
            CalibrationExperimentError,
            run_active_qubit_spectroscopy_calibration,
        )

        exports = {
            "CalibrationExperimentError": CalibrationExperimentError,
            "run_active_qubit_spectroscopy_calibration": run_active_qubit_spectroscopy_calibration,
        }
        globals().update(exports)
        return exports[name]
    if name in {
        "SpectroscopyCalibrationDecision", "SpectroscopyCalibrationPolicy",
        "SpectroscopyCalibrationRequest", "SpectroscopyCalibrationRun",
        "context_with_spectroscopy_calibration", "decide_qubit_spectroscopy_calibration",
        "run_qubit_spectroscopy_calibration", "verify_qubit_spectroscopy_calibration",
        "verify_qubit_spectroscopy_calibration_decision",
    }:
        from sqvm.calibration import (
            SpectroscopyCalibrationDecision, SpectroscopyCalibrationPolicy,
            SpectroscopyCalibrationRequest, SpectroscopyCalibrationRun,
            context_with_spectroscopy_calibration, decide_qubit_spectroscopy_calibration,
            run_qubit_spectroscopy_calibration, verify_qubit_spectroscopy_calibration,
            verify_qubit_spectroscopy_calibration_decision,
        )

        exports = {
            "SpectroscopyCalibrationDecision": SpectroscopyCalibrationDecision,
            "SpectroscopyCalibrationPolicy": SpectroscopyCalibrationPolicy,
            "SpectroscopyCalibrationRequest": SpectroscopyCalibrationRequest,
            "SpectroscopyCalibrationRun": SpectroscopyCalibrationRun,
            "context_with_spectroscopy_calibration": context_with_spectroscopy_calibration,
            "decide_qubit_spectroscopy_calibration": decide_qubit_spectroscopy_calibration,
            "run_qubit_spectroscopy_calibration": run_qubit_spectroscopy_calibration,
            "verify_qubit_spectroscopy_calibration": verify_qubit_spectroscopy_calibration,
            "verify_qubit_spectroscopy_calibration_decision": verify_qubit_spectroscopy_calibration_decision,
        }
        globals().update(exports)
        return exports[name]
    if name in {
        "SpectroscopyAxis", "SpectroscopyDataset", "SpectroscopyMode",
        "SpectroscopyPulsePolicy", "SpectroscopyRequest", "analyze_qubit_spectroscopy",
        "expand_qubit_spectroscopy_points", "run_qubit_spectroscopy",
    }:
        from sqvm.calibration import (
            SpectroscopyAxis, SpectroscopyDataset, SpectroscopyMode,
            SpectroscopyPulsePolicy, SpectroscopyRequest, analyze_qubit_spectroscopy,
            expand_qubit_spectroscopy_points, run_qubit_spectroscopy,
        )

        exports = {
            "SpectroscopyAxis": SpectroscopyAxis,
            "SpectroscopyDataset": SpectroscopyDataset,
            "SpectroscopyMode": SpectroscopyMode,
            "SpectroscopyPulsePolicy": SpectroscopyPulsePolicy,
            "SpectroscopyRequest": SpectroscopyRequest,
            "analyze_qubit_spectroscopy": analyze_qubit_spectroscopy,
            "expand_qubit_spectroscopy_points": expand_qubit_spectroscopy_points,
            "run_qubit_spectroscopy": run_qubit_spectroscopy,
        }
        globals().update(exports)
        return exports[name]
    if name in {
        "CircuitExecutionContext", "CircuitExecutionError", "CircuitExecutionProfile", "CircuitReasonCode", "CircuitResult",
        "CompiledCircuit", "DressedPopulations", "QCISCircuit", "QubitProbabilities", "ReadoutProbabilities",
        "compile_circuit", "run_circuits",
        "verify_circuit_result",
    }:
        from sqvm.circuits import (
            CircuitExecutionContext, CircuitExecutionError, CircuitExecutionProfile, CircuitReasonCode, CircuitResult,
            CompiledCircuit, DressedPopulations, QCISCircuit, QubitProbabilities, ReadoutProbabilities,
            compile_circuit, run_circuits,
            verify_circuit_result,
        )

        exports = {
            "CircuitExecutionContext": CircuitExecutionContext,
            "CircuitExecutionError": CircuitExecutionError,
            "CircuitExecutionProfile": CircuitExecutionProfile,
            "CircuitReasonCode": CircuitReasonCode,
            "CircuitResult": CircuitResult,
            "CompiledCircuit": CompiledCircuit,
            "DressedPopulations": DressedPopulations,
            "QCISCircuit": QCISCircuit,
            "QubitProbabilities": QubitProbabilities,
            "ReadoutProbabilities": ReadoutProbabilities,
            "compile_circuit": compile_circuit,
            "run_circuits": run_circuits,
            "verify_circuit_result": verify_circuit_result,
        }
        globals().update(exports)
        return exports[name]
    if name in {"build_stage4_acceptance_approval", "validate_stage4_acceptance_approval"}:
        from sqvm.control.stage4_artifacts import build_stage4_acceptance_approval, validate_stage4_acceptance_approval

        exports = {
            "build_stage4_acceptance_approval": build_stage4_acceptance_approval,
            "validate_stage4_acceptance_approval": validate_stage4_acceptance_approval,
        }
        globals().update(exports)
        return exports[name]
    if name in {"verify_q1_q2_coupling", "verify_static_spectrum"}:
        from sqvm.spectrum import verify_q1_q2_coupling, verify_static_spectrum

        exports = {
            "verify_q1_q2_coupling": verify_q1_q2_coupling,
            "verify_static_spectrum": verify_static_spectrum,
        }
        globals().update(exports)
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
