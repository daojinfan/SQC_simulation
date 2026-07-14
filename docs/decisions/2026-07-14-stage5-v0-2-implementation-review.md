# Stage 5 v0.2 Implementation Review

- Date: 2026-07-14
- Scope: reconstructed-input admission, fixed active-drive smoke, and reproducible source delivery
- Independent test reviewer: independent test lead AI
- Reproducibility/release reviewer: reproducibility and release lead AI
- Decision: approved for scoped commit and push

## Decision

Stage 5 v0.2 is approved as a smoke-level implementation and reproducible source delivery. This decision does
not approve formal numerical execution, formal acceptance, or Stage 6 readiness.

## Independent test review

The independent test review reported PASS with no blocker, high, medium, or low findings in the v0.2 scope.
The reviewed test module passed 18 tests after release-path additions. Coverage includes:

- strict config and repository-relative path admission;
- duplicate YAML and symlink-escape rejection;
- deterministic snapshot and Stage 5 execution-source binding;
- readout capability narrowing and named q1/c/q2 flux mapping;
- frozen q2-resonance index 0/66 full-triple checks and tamper rejection;
- Hamiltonian Hermiticity, ZOH edges, units, solver options, and identity gauge;
- formal fail-fast before numerical or publication work;
- existing-target rejection and staging cleanup after a solver exception.

## Evidence

The reviewed evidence and snapshot hashes are recorded in
`docs/results/2026-07-14-stage5-v0-2-smoke.md`. The three generated files are canonical JSON and their file,
cross-binding, bound-input, execution-source, and snapshot hashes were independently recomputed. The active
smoke window passed finite-observable, population-bound, and raw norm gates with maximum norm error
`8.443246102274315e-13` against the `1.0e-9` threshold.

## Release review

The release reviewer found no blocker and approved the Stage 5 v0.2 scoped commit/push after the project added:

- exact direct runtime/test dependency versions for the evidence environment;
- clean virtual-environment installation and CLI reproduction instructions;
- a tracked smoke result and complete hash record;
- atomic writer negative tests.

The remaining High residual limitation is that repository-wide historical regression cannot execute in this
checkout because Stage 2-4 tests require ignored historical `output/` artifacts that are unavailable. The user
accepted Stage 2-4 and waived their regeneration. This limitation is explicitly recorded and is not represented
as a full regression pass.

## Formal boundary

The formal config and input snapshot may be admitted and reconstructed, but v0.2 raises
`FormalScaleQualificationRequired` before QuTiP, Hamiltonian, target, or staging work. A later formal-scale
qualification must implement the reviewed sparse per-hold design and complete all five scenarios at primary
`(7,7,7)` and convergence `(8,8,8)` cutoffs. Until then, no formal artifact or Stage 6 readiness claim is valid.
