"""Stage 3 static-spectrum public API."""

from sqvm.spectrum.analysis import (
    analyze_static_point,
    assign_dressed_states,
    build_bare_state_catalog,
    compute_mode_participation,
    compute_static_metrics,
)
from sqvm.spectrum.artifacts import write_static_spectrum_artifacts
from sqvm.spectrum.config import load_spectrum_config
from sqvm.spectrum.context import build_spectrum_context, rebuild_hamiltonian_for_spectrum
from sqvm.spectrum.convergence import (
    check_crossing_convergence,
    check_static_metric_convergence,
    crossing_uncertainty_summary,
)
from sqvm.spectrum.flux import (
    bare_detuning_sign_change,
    classify_crossing_candidate,
    decimal_flux_grid,
    decimal_flux_key,
    detect_character_exchange,
    minimum_with_neighbor_bracket,
    scan_coupler_flux,
    spectrum_cache_key,
    track_branches_one_to_one,
)
from sqvm.spectrum.models import *  # noqa: F403
from sqvm.spectrum.provenance import (
    load_stage2_rebaseline_approval,
    load_stage2_rebaseline_manifest,
    run_stage2_dense_gap_consistency,
    validate_spectrum_provenance,
)
from sqvm.spectrum.runtime import build_execution_plan, evaluate_stage3_gate
from sqvm.spectrum.solver import (
    build_nonacceptance_solver_report,
    canonical_flux_text,
    dense_near_degenerate_blocks,
    dense_reference_eigensystem,
    environment_fingerprint,
    load_solver_backend_validation,
    load_solver_backend_validation_approval,
    projector_spectral_error,
    sha256_counter_seed,
    sha256_counter_v1,
    solve_static_eigensystem,
    stage3_solver_source_tree_sha256,
    validate_solver_backend,
)
from sqvm.spectrum.verify import assemble_static_spectrum_result, verify_static_spectrum
from sqvm.spectrum.validation import generate_solver_validation, run_dense_pilot
from sqvm.spectrum.stage31 import (
    check_q1_q2_crossing_convergence,
    classify_q1_q2_crossing,
    evaluate_coupling_modulation,
    evaluate_stage3_1_gate,
    finalize_q1_q2_crossings,
    full_flux_keys,
    scan_q1_q2_coupling_vs_coupler,
    scan_q1_q2_crossing,
    stage31_cache_identity,
)
from sqvm.spectrum.stage31_artifacts import (
    assemble_stage3_1_verification_report,
    validate_stage3_1_acceptance_approval,
    write_q1_q2_coupling_artifacts,
    write_q1_q2_coupling_notebook,
    write_stage3_1_verification_report,
)
from sqvm.spectrum.stage31_config import load_q1_q2_coupling_config, validate_stage31_design_freeze
from sqvm.spectrum.stage31_models import *  # noqa: F403
from sqvm.spectrum.stage31_validation import (
    generate_stage3_1_solver_validation,
    run_stage3_1_dense_pilot,
    validate_stage3_1_solver_backend,
)
from sqvm.spectrum.stage31_verify import verify_q1_q2_coupling

__all__ = [name for name in globals() if not name.startswith("_")]
