from dataclasses import replace

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


@pytest.fixture(scope="session")
def spectrum_session():
    config = load_spectrum_config("configs/spectra/2q1c_static_smoke.yaml")
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
