"""Stage 6 platform-only runtime core public API."""

from sqvm.runtime.backend import ExperimentBackend, admit_backend
from sqvm.runtime.config import load_experiment_request
from sqvm.runtime.fake import BACKEND_ID, CLAIM_ENVELOPE, EXPERIMENT_ID, RESPONSE_BYTES, RESPONSE_SHA256, DeterministicFakeBackend
from sqvm.runtime.lifecycle import CancellationToken, CooperativeBudget, RunState, allowed_transitions, transition
from sqvm.runtime.models import (
    BackendAdmission,
    BackendCapabilities,
    BackendCommand,
    ExecutionSettings,
    ExperimentDefinition,
    ExperimentRequest,
    ParameterSpec,
    PointResult,
    PreparedExperiment,
    RunArtifactSet,
    RunVerificationReport,
    ExperimentRun,
    CancellationReceipt,
    RecoveryArtifactSet,
    ResourceLockRecoveryReceipt,
    CatalogRebuildReport,
    ScanAxis,
    ScanPoint,
)
from sqvm.runtime.registry import BackendRegistry, ExperimentRegistry, get_builtin_backend_registry, get_builtin_experiment_registry
from sqvm.runtime.scan import expand_scan, point_table_payload
from sqvm.runtime.runner import request_run_cancellation, run_experiment
from sqvm.runtime.verify import load_experiment_run, verify_experiment_run
from sqvm.runtime.recovery import recover_interrupted_run, recover_terminal_resource_lock
from sqvm.runtime.catalog import rebuild_run_catalog

__all__ = [
    "BACKEND_ID", "CLAIM_ENVELOPE", "EXPERIMENT_ID", "RESPONSE_BYTES", "RESPONSE_SHA256",
    "BackendAdmission", "BackendCapabilities", "BackendCommand", "BackendRegistry", "CancellationToken",
    "CooperativeBudget", "DeterministicFakeBackend", "ExecutionSettings", "ExperimentBackend",
    "ExperimentDefinition", "ExperimentRegistry", "ExperimentRequest", "ParameterSpec", "PointResult",
    "PreparedExperiment", "RunState", "ScanAxis", "ScanPoint", "admit_backend", "allowed_transitions",
    "expand_scan", "get_builtin_backend_registry", "get_builtin_experiment_registry", "load_experiment_request",
    "point_table_payload", "transition", "RunArtifactSet", "RunVerificationReport", "ExperimentRun",
    "CancellationReceipt", "RecoveryArtifactSet", "ResourceLockRecoveryReceipt", "CatalogRebuildReport",
    "run_experiment", "load_experiment_run", "verify_experiment_run", "request_run_cancellation",
    "recover_interrupted_run", "recover_terminal_resource_lock", "rebuild_run_catalog",
]
