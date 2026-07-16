"""Stage 4.0 control-channel compatibility public API."""

from sqvm.control.artifacts import publish_control_channel_candidate
from sqvm.control.compatibility import (
    build_control_channel_approval,
    build_control_channel_manifest,
    validate_control_channel_approval,
    validate_control_channel_compatibility,
    validate_control_channel_manifest,
    validate_verification_candidate,
)
from sqvm.control.models import (
    ControlChannelCompatibilityReport,
    ControlChannelReadinessReport,
    ControlChannelRegistry,
    ControlChannelSpec,
)
from sqvm.control.registry import load_control_channel_registry
from sqvm.control.stage4_artifacts import build_stage4_acceptance_approval, validate_stage4_acceptance_approval, write_control_signal_artifacts
from sqvm.control.stage4_compile import compile_control_schedule
from sqvm.control.stage4_1_compile import adapt_qcis_v03_compilation, admit_qcis_v03_plan, compile_qcis_waveform_plan
from sqvm.control.stage4_1_config import build_parameterized_control_context
from sqvm.control.stage4_1_models import (
    LogicalArrayInventoryRow,
    ParameterizedControlCompilation,
    ParameterizedControlContext,
    ParameterizedControlError,
    ParameterizedControlReasonCode,
    QCISV03LogicalWaveformPlan,
)
from sqvm.control.stage4_config import load_control_chain_config, load_logical_schedule, validate_logical_schedule
from sqvm.control.stage4_models import (
    ControlArtifactSet,
    ControlBuildContext,
    ControlChainConfig,
    ControlCompilationResult,
    ControlRunReceipt,
    LogicalPulse,
    LogicalSchedule,
    ScheduleValidationReport,
    Stage5ReadinessReport,
)
from sqvm.control.stage4_verify import verify_control_signal

__all__ = [
    "ControlChannelCompatibilityReport",
    "ControlChannelReadinessReport",
    "ControlChannelRegistry",
    "ControlChannelSpec",
    "ControlArtifactSet",
    "ControlBuildContext",
    "ControlChainConfig",
    "ControlCompilationResult",
    "ControlRunReceipt",
    "LogicalArrayInventoryRow",
    "LogicalPulse",
    "LogicalSchedule",
    "ScheduleValidationReport",
    "Stage5ReadinessReport",
    "ParameterizedControlCompilation",
    "ParameterizedControlContext",
    "ParameterizedControlError",
    "ParameterizedControlReasonCode",
    "QCISV03LogicalWaveformPlan",
    "build_control_channel_approval",
    "build_control_channel_manifest",
    "load_control_channel_registry",
    "publish_control_channel_candidate",
    "validate_control_channel_approval",
    "validate_control_channel_compatibility",
    "validate_control_channel_manifest",
    "validate_verification_candidate",
    "load_control_chain_config",
    "load_logical_schedule",
    "validate_logical_schedule",
    "compile_control_schedule",
    "admit_qcis_v03_plan",
    "adapt_qcis_v03_compilation",
    "build_parameterized_control_context",
    "compile_qcis_waveform_plan",
    "write_control_signal_artifacts",
    "build_stage4_acceptance_approval",
    "verify_control_signal",
    "validate_stage4_acceptance_approval",
]
