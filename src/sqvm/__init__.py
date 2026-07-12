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
]

__version__ = "0.1.0"


def __getattr__(name: str):
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
