# Stage 4 Plan: Control Signal Chain

## Stage purpose

Convert explicit physical control requests into deterministic AWG waveforms and effective device signals
for the accepted `2q1c2r` virtual machine.

The stage establishes the stable input contract for Stage 5 time evolution:

```text
logical pulse schedule
  -> ideal rotating-frame / flux targets
  -> AWG I/Q/Z samples and DAC codes
  -> effective XY drive, flux, and readout envelopes
```

## References

1. Fan Daojin dissertation, *Research on Large-Scale Transmission and Quantum-Gate Calibration Schemes
   Based on Superconducting Quantum Computing*.
   - Used for the room-temperature AWG, IQ-mixer, XY/Z/readout transmission chain, 2 GS/s DAC example,
     16-bit encoding, timing alignment, crosstalk, and Z-waveform distortion context.
2. Li Shaowei dissertation, *High-Precision Control and High-Fidelity Two-Qubit Gates in Superconducting
   Qubits*.
   - Used for Gaussian/DRAG control, smooth flux pulses, tunable-coupler control, and calibration workflow
     context.
3. Previous project control documents and waveform examples.
   - Used only as a source of workflow lessons: explicit pulse events, separate logical/AWG/effective layers,
     and human-readable waveform plots.
   - Previous broad instruction sets, mutable parameter tables, and unvalidated default physics are not
     copied into this stage.
4. Accepted Stage 3.1 artifact and approval.
   - Supplies the trusted q1/q2/c operating points, idle spectrum, q1-q2 resonance point, and coupler-flux
     modulation evidence.

## Scope

Included:

```text
strict control-channel registry and compatibility approval
strict control and schedule YAML schemas
q1/q2 XY gaussian and DRAG pulses
q1/q2/c Z square and cosine-edge flat-top pulses
r1/r2 readout baseband envelopes
common 2 GS/s sample grid
16-bit signed DAC quantization
integer-sample latency compensation
causal FIR forward response
static XY/Z/readout channel-mixing matrices
schedule and physical-lane conflict checks
canonical artifacts, executed notebook, CLI, smoke profile, and formal profile
independent final acceptance and Stage 5 readiness gate
```

Excluded:

```text
QuTiP or other quantum-state evolution
gate instruction expansion or calibrated X/X2/CZ semantics
gate fidelity, leakage, or population claims
stochastic noise and nonlinear electronics
dynamic FIR/IIR predistortion optimization
ADC acquisition and readout discrimination
hardware instrument drivers
calibration-parameter acceptance or update workflows
```

## Inputs

Formal inputs:

```text
configs/control/2q1c2r_channels.yaml
output/stage_04_0_control_channel_rebaseline/control_channel_approval.json
configs/control/2q1c2r_control.yaml
configs/control/2q1c2r_control_demo.yaml
output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json
output/stage_03_1_q1_q2_coupling/acceptance_approval.json
Stage 4 design-freeze manifest
```

The Stage 3.1 acceptance must validate to `stage4_ready=true`; path presence or a self-declared decision is
not sufficient.

## Outputs

Code and configuration:

```text
src/sqvm/control/
configs/control/2q1c2r_channels.yaml
configs/control/2q1c2r_control.yaml
configs/control/2q1c2r_control_smoke.yaml
configs/control/2q1c2r_control_demo.yaml
configs/control/2q1c2r_control_demo_smoke.yaml
scripts/run_stage_04_control_signal.py
scripts/run_stage_04_control_signal_smoke.py
```

Formal result:

```text
output/stage_04_control_signal/control_signal_artifacts.json
output/stage_04_control_signal/verification.ipynb
output/stage_04_control_signal/verification_report.json
output/stage_04_control_signal/run_receipt.json
output/stage_04_control_signal/acceptance_approval.json
```

Smoke output is separate:

```text
output/stage_04_control_signal_smoke/
```

## Core objects

```text
ControlChannelRegistry
ControlChainConfig
DACSpec
PhysicalLaneSpec
StaticMixingSpec
LogicalPulse
LogicalSchedule
CompiledSchedule
AWGWaveformSet
EffectiveSignalSet
ControlCompilationResult
ControlVerificationReport
ControlRunReceipt
Stage5ReadinessReport
```

`ControlCompilationResult` is the only input accepted by the artifact writer.

## Stable public interfaces

```text
load_control_channel_registry(path) -> ControlChannelRegistry
load_control_chain_config(path) -> ControlChainConfig
load_logical_schedule(path) -> LogicalSchedule
validate_logical_schedule(schedule, registry, config) -> ScheduleValidationReport
compile_control_schedule(schedule, config, provenance) -> ControlCompilationResult
write_control_signal_artifacts(result, output_dir) -> ControlArtifactSet
verify_control_signal(config_path, schedule_path, output_dir) -> ControlRunReceipt
validate_stage4_acceptance_approval(...) -> Stage5ReadinessReport
```

All stable interfaces document units and reject booleans as numeric values, non-finite values, unknown
fields, unsupported versions, missing hashes, and stale paths.

## User workflow

```text
1. Inspect the accepted Stage 3.1 coupling artifact and Stage 4 channel registry.
2. Define physical logical pulses in a strict schedule YAML.
3. Run the smoke compiler while editing the schedule.
4. Plot logical targets, AWG samples, DAC codes, and effective signals.
5. Inspect conflict, clipping, quantization, timing, area, and provenance checks.
6. Run the formal compiler once after the design and inputs are frozen.
7. Independent test validates and approves or rejects the artifact.
```

Proposed commands:

```text
python -m sqvm verify-control configs/control/2q1c2r_control.yaml \
  configs/control/2q1c2r_control_demo.yaml \
  --output output/stage_04_control_signal

python scripts/run_stage_04_control_signal_smoke.py
```

Formal CLI exits `0` only when the final run receipt has `acceptance_candidate_ready=true`. Any computational
or publication failure returns `1` and leaves the requested formal output path absent. Diagnostics are
returned in memory/log output and may be exercised in temporary test fixtures, but no failed formal
artifact variant is published. Missing approvals, stale provenance, invalid configuration, or serialization
errors also fail before publishing output.

The separate smoke runner exits `0` when its profile-specific computational and publication checks pass. Its
artifact/report may have `computational_ready=true` and `status=smoke_complete`, but its receipt always has
`acceptance_eligible=false` and `acceptance_candidate_ready=false`; smoke output is never independently
approved or consumed by Stage 5.

## Reference scenarios

Formal verification contains independent scenarios rather than one overloaded schedule:

```text
xy_drag:
  simultaneous q1/q2 DRAG pulses with different phases

q2_resonance_flux:
  c fixed at 0.270 Phi0 and q2 moved from 0.0 to 0.0997552 Phi0

coupler_anchor_flux:
  separate c targets 0.200, 0.270, and 0.385 Phi0

readout_envelopes:
  simultaneous r1/r2 readout baseband pulses

conflict_fixture:
  overlapping same-port pulses rejected before sampling
```

The detailed design freezes the formal latency, FIR, XY/Z/readout mixing matrices, and every logical pulse
value. Development does not choose nominal electronics or reference amplitudes. The smoke schedule is an
exact two-scenario subset of the formal schedule.

The Stage 3.1 target values are checked as signal-delivery targets only. Stage 4 does not infer a quantum
transition or gate operation from the waveform.

## Correctness checks

Formal ordered computational checks:

```text
stage3_1_readiness_valid
stage4_0_channel_registry_ready
design_and_config_provenance_valid
profile_acceptance_contract_valid
schedule_schema_valid
schedule_conflict_free
sample_grid_exact
static_matrices_valid
no_dac_clipping
quantization_error_within_bound
latency_alignment_exact
forward_reconstruction_matches_reference
xy_area_error_within_budget
z_target_error_within_budget
readout_area_error_within_budget
stage3_phase_proxy_within_budget
runtime_within_budget
```

Formal acceptance requires all checks. A missing or non-finite metric is a failure, not a warning.

Smoke uses this exact ordered check set:

```text
stage3_1_readiness_valid
stage4_0_channel_registry_ready
design_and_config_provenance_valid
profile_acceptance_contract_valid
schedule_schema_valid
schedule_conflict_free
sample_grid_exact
static_matrices_valid
no_dac_clipping
quantization_error_within_bound
latency_alignment_exact
forward_reconstruction_matches_reference
xy_area_error_within_budget
z_target_error_within_budget
stage3_phase_proxy_within_budget
runtime_within_budget
```

The profile contract check requires formal/true or smoke/false for `acceptance_eligible`. Smoke omits the
readout area check because its exact schedule has no readout scenario; its persisted readout aggregate is
false, never vacuously true. A successful smoke run may have `computational_ready=true` and
`status=smoke_complete`, but its receipt always has `acceptance_candidate_ready=false`.

## Error budgets

```text
sample rate: exactly 2.0e9 samples/s
sample interval: exactly 0.5 ns
DAC: signed 16-bit, nominal range -0.33 V to +0.32998992919921875 V
per-lane DAC quantization: <= 0.5 LSB + 1e-15 V
latency-alignment error: exactly 0 samples
exact two required XY pulse-frame I-area rows: each <= 0.5%; null/missing/extra fails
exact two required readout pulse-frame I-area rows: each <= 0.5%; null/missing/extra fails
Z flat-top target error after settling: <= 2.0e-5 Phi0
Stage 3 frequency uncertainty phase proxy over 32 ns: <= 0.10 rad
formal analysis runtime: <= 10 s
formal end-to-end runtime: <= 60 s, checked after publication and bound by independent approval
smoke analysis runtime: <= 2 s
smoke end-to-end runtime: <= 10 s
formal sample count: <= 10000 desired-grid samples per scenario; published P <= N+191
```

The phase proxy is `2*pi*U_frequency_Hz*32 ns`. It connects the Stage 3 static frequency uncertainty to
the nominal XY control window but is not a gate-fidelity estimate.

## Implementation tasks

```text
1. Implement the Stage 4.0 channel compatibility gate and obtain independent approval.
2. Add strict channel, control-chain, and schedule loaders.
3. Add immutable typed models and finite validation helpers.
4. Implement exact time-grid construction and conflict validation.
5. Implement gaussian, DRAG, square, and flattop-cos shapes.
6. Implement static inverse mapping, DAC quantization, latency placement, FIR response, and forward mapping.
7. Implement independent reference reconstruction and error metrics.
8. Implement canonical atomic artifact/report writers and an actually executed read-only notebook.
9. Add `verify-control` CLI and separate formal/smoke runners.
10. Run focused tests, safe repository regression, smoke, and one formal candidate generation.
11. Hand the immutable candidate to independent test; development does not self-approve.
```

## Test plan

Functional tests:

```text
strict schema and unit parsing
exact sample count and half-open timing
identifier grammar, nonnegative starts, pulse-end bounds, and one-pulse-per-channel rule
gaussian symmetry and corrected endpoints
DRAG derivative/quadrature sign and zero-area behavior
flat-top cosine edges and target plateau
matrix inversion and singular/ill-conditioned rejection
signed DAC Decimal.from_float half-even tie/near-tie vectors, range, quantization bound, and clipping rejection
integer latency compensation
known FIR impulse and step response
different FIR/latency lengths produce exact common N_awg/P arrays without trimming
same-port overlap rejection and legal cross-port simultaneity
Stage 3.1 q2/c target compilation
exact four area rows, phase rotation, and null/empty/duplicate fail-closed aggregation
formal global metrics placement and smoke exact-two/nonacceptance report consistency
exact artifact/report nested schemas and cross-consistency
artifact canonicality, finite preflight, atomic publish, and notebook re-execution
```

Representative fail-closed tests:

```text
stale Stage 3.1 approval
stale Stage 4.0 registry approval
one missing registry channel
one noncanonical artifact
one nested non-finite value with no partial output
one syntactically valid smoke approval attack rejected by builder and Stage5 readiness validator
one tampered row per scenario-local check rejected against source metrics/arrays
one DAC code tampered to a different in-range integer rejected by reference-code equality
```

The test AI must not expand these into exhaustive fuzzing unless review finds a concrete uncovered risk.
Only one formal output generation is permitted per approved source/config identity.

## Artifacts and notebook

The development publication produces exactly four files:

```text
control_signal_artifacts.json
verification.ipynb
verification_report.json
run_receipt.json
```

The artifact and report use canonical JSON and recursively reject NaN/Infinity. The notebook reads only
its sibling artifact, is executed by a real notebook engine, contains sequential execution counts, and has
zero error outputs. Paths recorded in the report are the expected final paths, never staging paths. The
report records staging checks and the publication contract; it does not claim those final paths existed
before the directory rename. The runner and independent acceptance validator check final existence and raw
hashes after publication without rewriting the report. The runner writes those checks, the three raw hashes,
and the measured end-to-end runtime to canonical `run_receipt.json` only after the directory rename.

Independent approval later adds only:

```text
acceptance_approval.json
```

## Acceptance criteria

Stage 4 is complete when:

```text
the Stage 4.0 channel registry is independently approved
all ordered computational checks pass
all reference scenarios compile deterministically
artifact/report are canonical and finite
notebook is genuinely executed and visually shows every signal layer
focused and safe repository tests pass
independent test produces an approved hash-bound acceptance
Stage5ReadinessReport validates stage5_ready=true
the development log records actual commands, results, and limitations
```

## Open questions

No implementation-blocking product choice remains in v0.1. Later versions may decide whether to add
dynamic predistortion, nonlinear mixers, shared readout-line multiplexing, virtual frame instructions, or
hardware instrument adapters. Those are explicitly outside this freeze candidate.
