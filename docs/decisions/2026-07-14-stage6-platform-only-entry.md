# Decision: Stage 6 Platform-Only Entry

Date: 2026-07-14

## Context

The frozen Stage 5 contract makes an independently approved
`Stage6ReadinessReport.stage6_ready=true` the only entrance to physics-capable Stage 6 work. Stage 5 v0.2 has
completed a bounded engineering smoke, but formal-scale execution, formal acceptance, and Stage 6 physics
readiness remain blocked.

Stage 6 is also the roadmap location for reusable experiment-platform infrastructure: strict requests, finite
scan planning, lifecycle state, immutable datasets, provenance, recovery, and a query catalog. Those mechanics
can be implemented and tested without executing or importing any superconducting-device physics.

## Decision

Stage 6 may design and implement a platform-only MVP using the built-in `deterministic_fake_v1` backend. This is
an infrastructure continuation, not satisfaction of the Stage 5 physics entrance and not a waiver of it.

The MVP must enforce all of the following:

1. Stage 5 artifacts are not imported, displayed, converted, or used as backend input.
2. No Stage 5, QuTiP, Hamiltonian, control, calibration, readout, or device-physics backend is registered.
3. Every completed dataset carries the immutable claim envelope
   `evidence_class="platform_test_fixture"`, `physics_claim="none"`,
   `observation_model="absent"`, and `measurement_payload=null` in request, manifest, dataset metadata, report,
   and receipt verification.
4. Unknown or modified claim metadata fails verification. Callers cannot supply or override the envelope.
5. Stage 6 implementation acceptance means only that the platform kernel is qualified against its fake fixture.
   It does not mean formal, calibrated, measured, or physics-ready.

## Future physics entrance

A physical backend remains blocked until the original Stage 5 readiness contract is satisfied and a separate
Stage 6 physics-backend design is independently frozen. That future decision must define authoritative gate
artifacts, public validators, source/environment/evidence hashes, independent approvals, and a fail-closed truth
table. This platform-only exception cannot be cited as precedent for bypassing those gates.

## Package decision

`src/sqvm/runtime/` is the owner of the platform kernel and supersedes the provisional `src/sqvm/simulation/`
name in `docs/10_development_process.md`. `src/sqvm/experiments/` remains reserved for Stage 7 experiment logic.

## Status

Approved as the entrance rule for Stage 6 design. Implementation remains blocked until the Stage 6 detailed
design itself receives independent design-freeze approval.
