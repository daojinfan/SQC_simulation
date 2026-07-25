"""Collection-time test taxonomy enforcement for the current test baseline."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sqvm.spectrum import (
    analyze_static_point,
    build_nonacceptance_solver_report,
    build_spectrum_context,
    load_spectrum_config,
    load_stage2_rebaseline_approval,
    load_stage2_rebaseline_manifest,
    run_stage2_dense_gap_consistency,
    validate_spectrum_provenance,
)
from tests.support.physics_fixture import physics_smoke_config

PRIMARY_MARKERS = frozenset({"contract", "integration", "physics_slow", "evidence"})


@pytest.fixture(scope="session")
def spectrum_session():
    config = load_spectrum_config(physics_smoke_config())
    manifest = load_stage2_rebaseline_manifest(config.source_rebaseline_manifest)
    approval = load_stage2_rebaseline_approval(config.source_rebaseline_approval)
    provenance = validate_spectrum_provenance(config, manifest, approval)
    gap = run_stage2_dense_gap_consistency(config, provenance)
    provenance = replace(provenance, stage2_dense_gap_consistency=gap)
    solver = build_nonacceptance_solver_report(config, provenance)
    context = build_spectrum_context(config, provenance, solver)
    baseline = analyze_static_point(context, config)
    return {
        "config": config,
        "manifest": manifest,
        "approval": approval,
        "provenance": provenance,
        "solver": solver,
        "context": context,
        "baseline": baseline,
    }

# Audit registry for taxonomy review. Markers are declared in each test module;
# this registry is never used to assign or infer them during collection.
MODULE_PRIMARY_MARKERS = {
    "test_calibration_api.py": "integration",
    "test_calibration_scan_execution.py": "physics_slow",
    "test_calibration_web.py": "integration",
    "test_calibration_web_ui.py": "integration",
    "test_ci_workflows.py": "contract",
    "test_configuration_transactions.py": "contract",
    "test_control_channel_compatibility.py": "evidence",
    "test_control_signal.py": "evidence",
    "test_device_capacitance.py": "contract",
    "test_device_junction.py": "contract",
    "test_device_spec.py": "contract",
    "test_device_validation.py": "contract",
    "test_dressed_labeling.py": "physics_slow",
    "test_experiment_archive_format.py": "integration",
    "test_experiment_storage_catalog.py": "integration",
    "test_experiment_storage_independent_acceptance.py": "evidence",
    "test_experiment_storage_inventory.py": "integration",
    "test_experiment_storage_lifecycle.py": "integration",
    "test_experiment_storage_operations.py": "integration",
    "test_experiment_storage_policy.py": "integration",
    "test_experiment_storage_references.py": "integration",
    "test_fixture_loader.py": "contract",
    "test_successor_rebaseline.py": "evidence",
    "test_successor_rebaseline_fixture.py": "contract",
    "test_flux_scan.py": "physics_slow",
    "test_hamiltonian_builder.py": "physics_slow",
    "test_hamiltonian_capacitance.py": "contract",
    "test_hamiltonian_config.py": "contract",
    "test_hamiltonian_junction.py": "contract",
    "test_hamiltonian_provenance.py": "evidence",
    "test_hamiltonian_rebaseline.py": "evidence",
    "test_hamiltonian_verify.py": "physics_slow",
    "test_mode_participation.py": "physics_slow",
    "test_platform_configuration_v02.py": "integration",
    "test_qubit_spectroscopy.py": "integration",
    "test_runtime_publication.py": "integration",
    "test_runtime_v03_batch.py": "integration",
    "test_run_circuits.py": "integration",
    "test_solver_validation_gate.py": "evidence",
    "test_spectroscopy_calibration_workflow.py": "integration",
    "test_spectroscopy_evidence_closure.py": "evidence",
    "test_spectroscopy_reader_verifier.py": "integration",
    "test_spectrum_config.py": "evidence",
    "test_spectrum_convergence.py": "physics_slow",
    "test_spectrum_eigensystem.py": "physics_slow",
    "test_stage31_q1q2_coupling.py": "physics_slow",
    "test_stage41_production_authority.py": "evidence",
    "test_stage4_1_artifacts.py": "integration",
    "test_stage4_1_parameterized_control.py": "contract",
    "test_stage51_coefficients.py": "contract",
    "test_stage51_evolution_artifacts.py": "integration",
    "test_stage51_physics_helpers.py": "physics_slow",
    "test_stage51_physics_preflight.py": "evidence",
    "test_stage51_production_authority.py": "evidence",
    "test_stage51_two_level_oracles.py": "physics_slow",
    "test_stage51_verified_control_admission.py": "integration",
    "test_stage51_worker.py": "physics_slow",
    "test_stage5_evolution.py": "physics_slow",
    "test_stage6_runtime_core.py": "integration",
    "test_stage6_runtime_evidence.py": "evidence",
    "test_stage6_runtime_platform.py": "evidence",
    "test_stage6_runtime_v02.py": "evidence",
    "test_stage71_model_entrance.py": "evidence",
    "test_stage7_qcis_acceptance.py": "evidence",
    "test_stage7_qcis_v2.py": "contract",
    "test_stage7_qcis_v3.py": "contract",
    "test_static_metrics.py": "physics_slow",
    "test_static_spectrum_verify.py": "physics_slow",
    "test_authority_drift.py": "evidence",
    "test_test_taxonomy.py": "contract",
    "test_test_lock.py": "contract",
    "test_user_notebooks.py": "integration",
    "test_verify_device.py": "contract",
    "test_web_frontend_lazy_loading.py": "integration",
    "test_web_persistent_read_model.py": "integration",
    "test_web_plotting.py": "integration",
    "test_web_projection_adversarial.py": "integration",
    "test_web_projection_end_to_end.py": "integration",
    "test_web_publication_registrar.py": "integration",
    "test_web_storage_catalog_refresh.py": "integration",
    "test_web_visualization_read_model.py": "integration",
}

# Mixed modules retain a module-level audit owner, with these exact nodeids
# deliberately classified differently. This registry validates declarations;
# it never adds markers during collection.
ITEM_PRIMARY_MARKERS = {
    "tests/test_run_circuits.py::test_run_circuits_returns_final_q1_q2_probabilities_from_verified_evolution": "physics_slow",
    "tests/test_stage51_verified_control_admission.py::test_real_stage41_handle_runs_through_production_qutip_worker_and_replay": "physics_slow",
}

def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        module_name = Path(str(item.fspath)).name
        expected = ITEM_PRIMARY_MARKERS.get(item.nodeid, MODULE_PRIMARY_MARKERS.get(module_name))
        if expected is None:
            raise pytest.UsageError(f"unclassified test module: {module_name}")
        existing = [mark.name for mark in item.iter_markers() if mark.name in PRIMARY_MARKERS]
        if len(existing) != 1:
            raise pytest.UsageError(
                f"{item.nodeid} must declare exactly one primary marker; found {existing}"
            )
        if existing[0] != expected:
            raise pytest.UsageError(
                f"{item.nodeid} primary marker {existing[0]} disagrees with taxonomy audit {expected}"
            )
