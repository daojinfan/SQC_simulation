# Stage 5 v0.2 Input Admission Amendment

- Date: 2026-07-14
- Status: implementation authorization draft
- Supersedes: Stage 5 v0.1 upstream exact-five admission only
- Does not supersede: Stage 5 v0.1 physics and numerical contracts

## Scope

This amendment implements the user decision in
`docs/decisions/2026-07-14-stage2-stage4-user-acceptance.md`. It replaces the unavailable historical Stage 4
exact-five admission with a deterministic, fail-closed `stage5_input_snapshot` built from tracked inputs.

## Admission Order

Stage 5 uses two phases:

1. `admit_stage5_config_paths` validates the config schema and repository-relative paths without loading model
   or control data.
2. `load_stage5_input` validates the user-decision record, hashes all bound inputs, rebuilds the control
   snapshot, validates it, and only then constructs the typed Stage 5 input.

No output directory may be created before both phases pass.

## Bound Inputs

The snapshot binds, at minimum:

- this amendment and the Stage 2-4 user-acceptance decision;
- the Stage 1 device config, Stage 2 Hamiltonian config, Stage 4 control-chain config, channel registry, and
  logical schedule;
- the source files used for device/model construction and Stage 4 compilation;
- the exact current Python, NumPy, SciPy, and QuTiP versions;
- the ordered Stage 5 scenario IDs and the canonical snapshot SHA-256.

The fixed formal scenario order is `xy_drag`, `q2_resonance_flux`, `coupler_0_200`, `coupler_0_270`, and
`coupler_0_385`. The Stage 4 `readout_envelopes` scenario is excluded from the Stage 5 capability surface.

## Reconstruction Rules

- Use the public Stage 4 config, registry, schedule-validation, and compilation code. Do not duplicate the
  pulse-chain mathematics in Stage 5.
- The only reconstructed Stage 3 datum needed by the Stage 4 compiler is
  `idle_convergence.max_frequency_drift_MHz`. Its value is the accepted Stage 3 documented value and must be
  stored with its source document path and hash in the snapshot.
- Consume only each scenario's `effective.time_center_ns`, `effective.xy_drive_GHz.q1/q2.{i,q}`, and
  `effective.absolute_flux_phi0.q1/q2/c`, plus XY carrier frequency and phase metadata for the frozen frame
  contract.
- Stage 4 flux arrays are named `q1`, `q2`, `c`; Stage 2 Hamiltonian tensor order is `q1`, `c`, `q2`. Mapping
  must be by explicit mode name. Positional mapping is forbidden.
- `epsilon = I + iQ`; `phase_rad` is provenance/validation metadata and must not be applied a second time.
- Readout rejection means the Stage 5 typed input and public evolution API expose no readout field or operator.
  It does not mean rejecting a valid accepted schedule merely because a separate readout scenario exists.
- Stage 4 centers remain strict ascending at 0.5 ns. Stage 5 derives ZOH edges exactly as frozen in v0.1.

## Physics And Numerical Contract

The complete charge-basis interaction-picture Hamiltonian, XY-only analytic RWA, `2*pi` GHz-to-rad/ns
conversion, lab-frame initial state, deterministic `000/100/001/101` labeling, projector transformation,
population/leakage definitions, QuTiP solver path, and frozen tolerances remain those of v0.1.

Environment binding is to the resolved running interpreter and recorded dependency fingerprint. A fixed
machine-specific interpreter path is not a portable configuration field. Supported dependency ranges must be
declared in `pyproject.toml` and validated at runtime.

## Public API Delta

The implementation adds stable single-scenario composition for Stage 6:

```text
admit_stage5_config_paths(config_path, repository_root=None) -> Stage5PathAdmission
load_stage5_input(config_path, repository_root=None) -> Stage5Input
evolve_stage5_scenario(stage5_input, scenario_id, *, calculation="primary") -> Stage5ScenarioResult
run_stage5_evolution(config_path, output_dir, repository_root=None) -> Stage5ArtifactSet
```

`evolve_stage5_scenario` is deterministic for a validated input and contains no artifact-publication side
effect. `run_stage5_evolution` is the orchestration/publication layer and must call the same scenario API.

## Acceptance

Stage 5 is complete only when unit tests, control-snapshot contract tests, Hamiltonian/frame/unit tests,
single-scenario smoke evolution, artifact validation, independent test review, and release reproducibility
review pass. Formal-scale execution may be separately identified when runtime exceeds the bounded local
verification budget, but it must never be reported as executed when only smoke evidence exists.

## Solver Amendment

The v0.1 QuTiP solver options were measured on the reconstructed interaction-picture model and did not meet
the unchanged `norm_error <= 1e-9` criterion. Stage 5 v0.2 therefore uses the closed-system ket solver
`qutip.sesolve` with exact options `{method: vern9, rtol: 1e-13, atol: 1e-15, nsteps: 100000,
max_step: 0.0025, store_states: true, store_final_state: true, normalize_output: false, progress_bar: null}`.
The approved internal target is maximum raw norm error `<=5e-11`; the public acceptance gate remains
`norm_error <= 1e-9` and no output normalization is permitted.

For every callback, Stage 5 computes `lambda(t)=Re(trace(H_rad_per_ns(t)))/dimension`, validates that the
trace is finite and real within numerical precision, and solves `H'=H-lambda I`. This is an identity-energy
gauge, not normalization or a physics-term deletion. At each reported edge it explicitly integrates lambda
over the ZOH intervals and multiplies the raw solver ket by `exp(-i theta)` before observables and serialization.
The original Hamiltonian, frame, ZOH controls, and norm threshold remain unchanged.

Smoke evidence is a fixed continuous active-drive window of the full hash-bound `xy_drag` snapshot. Artifact
records include `original_sample_count`, `window_start_index`, and `window_sample_count`; it is not formal or
full-scenario evidence. Formal configuration and construction are tested but no formal numerical run is claimed.

## Formal-Scale Guard

Implementation completion does not establish formal execution, formal acceptance, or Stage 6 readiness. v0.2
rejects all formal numerical runner and direct-scenario calls after formal input/snapshot admission and before
target, staging, QuTiP, or Hamiltonian work. A future formal path requires separate qualification of sparse
per-hold execution and C7/C8 convergence before it may run.
