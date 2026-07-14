# Stage 2-4 User Acceptance And Direct Stage 5 Development

- Date: 2026-07-14
- Decision owner: user / project sponsor
- Recorded by: project manager AI
- Decision: accepted

## Background

The current checkout contains the approved Stage 2 through Stage 4 source, configuration, design, and review
records, but the historical `output/` artifacts referenced by those records are not present locally or in the
available Git history. Exact historical artifact recovery was investigated and could not be completed from the
available repository, local filesystem, or retained Codex tasks.

## Decision

The user explicitly accepts Stage 2 through Stage 4 as correct and directs the project to continue with Stage 5.
The project therefore will not run `recovery-v1`, repeat Stage 2-4 acceptance, or reinterpret newly generated
files as the original historical artifacts.

Stage 5 may deterministically rebuild the numerical inputs it needs from the accepted, version-controlled
Stage 2-4 source and configuration. Such data is a `stage5_input_snapshot`, not a replacement Stage 2-4
acceptance artifact. The snapshot must bind the current source/configuration hashes, this decision record, the
scenario order, and all numerical arrays consumed by Stage 5.

## Considered Alternatives

- Recover the original exact artifact bytes. This was attempted but the files were not available.
- Re-run and re-approve Stage 2-4. The user explicitly waived this work.
- Block Stage 5 indefinitely. This conflicts with the project direction and provides no additional evidence.

## Impact

- The original Stage 5 v0.1 exact-five upstream gate is superseded only for the Stage 5 input-admission path.
- The frozen Stage 5 Hamiltonian, frame, unit, time-grid, state-labeling, solver, and numerical acceptance rules
  remain unchanged unless a later reviewed amendment says otherwise.
- The snapshot must expose no readout control to the evolution engine. Readout data may exist in the accepted
  Stage 4 schedule, but it is outside the Stage 5 numerical capability boundary.
- Provenance must state that Stage 2-4 acceptance is sponsor-provided and that input bytes were reconstructed.
- Any inability to reproduce the accepted fixed scenarios or model parameters from tracked inputs blocks Stage 5.

## Follow-up Actions

1. Freeze the Stage 5 v0.2 input-reconstruction amendment.
2. Implement and test deterministic snapshot construction before the evolution engine consumes any controls.
3. Obtain physics/numerics, independent test, and reproducibility/release reviews before publication.
4. Push the completed Stage 5 implementation and evidence to the configured GitHub remote.
