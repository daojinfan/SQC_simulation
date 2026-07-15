"""Strict Stage 7 QCIS template compilation without a physics backend."""

from sqvm.qcis.compiler import compile_qcis, materialize_program
from sqvm.qcis.errors import QCISCompilationError, QCISReasonCode
from sqvm.qcis.models import (
    BindingPosition,
    BindingSpec,
    QCISBinding,
    QCISCharacterizationMetric,
    ProgramEnvelope,
    QCISAuthorities,
    QCISCompilation,
    QCISInstruction,
    QCISLogicalWaveformPlan,
    QCISProgram,
    QCISScanValue,
    QCISTemplate,
    PhasedFSimCharacterization,
)
from sqvm.qcis.parser import admit_program, parse_qcis
from sqvm.qcis.verify import verify_compilation, verify_coefficient_inventory, verify_effective_controls

__all__ = [
    "BindingPosition", "BindingSpec", "PhasedFSimCharacterization", "ProgramEnvelope", "QCISAuthorities", "QCISBinding", "QCISCharacterizationMetric", "QCISCompilation",
    "QCISCompilationError", "QCISInstruction", "QCISLogicalWaveformPlan", "QCISProgram",
    "QCISReasonCode", "QCISScanValue", "QCISTemplate", "admit_program", "compile_qcis", "materialize_program",
    "parse_qcis", "verify_coefficient_inventory", "verify_compilation", "verify_effective_controls",
]
