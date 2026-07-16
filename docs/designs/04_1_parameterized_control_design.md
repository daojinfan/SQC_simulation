# Stage 4.1 Parameterized Control Rebaseline Detailed Design

- Date: 2026-07-16
- Status: reviewed initial design; user-confirmed on 2026-07-16; not yet a freeze or execution authority
- Depends on: accepted Stage 4 and reviewed QCIS v0.3 drive-phase amendment
- Opens no gate: Stage 5.1, Stage 6 physics backend, and Stage 7.1 model scans remain closed

## 1. Purpose

Stage 4.1 converts one verified `QCISLogicalWaveformPlan` into immutable, electronics-processed effective
controls. It is the only allowed bridge between QCIS logical samples and a future Stage 5.1/QuTiP evolution
backend.

The rebaseline exists because the accepted Stage 4 API compiles only a fixed smoke/formal scenario order, while
Stage 7 needs a public parameterized point compiler. Stage 4.1 must add that capability without weakening or
reinterpreting the accepted Stage 4 contract.

The complete authority chain is:

```text
concrete QCIS
  -> typed AST and expansion trace
  -> QCIS logical delta arrays in physical coordinates
  -> Stage 4.1 electronics compilation
  -> published ParameterizedControlArtifact
  -> independently constructed VerifiedControlHandle
  -> future Stage 5.1 coefficient construction
```

Direct QCIS-to-Hamiltonian, scan-axis-to-Hamiltonian, logical-array-to-QuTiP, and unpublished-artifact shortcuts
are forbidden.

## 2. Scope

Stage 4.1 owns:

- strict admission and re-verification of a QCIS logical waveform plan;
- conversion from named physical-coordinate targets to named AWG lanes;
- lane latency precompensation, DAC range checking and quantization, FIR delivery, and forward static mixing;
- reconstruction of effective q1/q2 XY drive and q1/q2/c absolute flux;
- deterministic metrics, inventories, publication, and post-publication verification;
- exact regression compatibility with the accepted Stage 4 smoke/formal outputs.

Stage 4.1 does not own:

- QCIS parsing, macro expansion, cursor/barrier/frame replay, or waveform generation;
- `F012ZBIAS_MAPPER` or `G2ZBIAS_MAPPER` evaluation;
- gate-setting selection or calibration-setting mutation;
- Hamiltonian/operator construction, frame conversion, state evolution, observables, or fitting;
- readout, IQ, shots, assignment, noise, dissipation, or measurement claims.

Mapper evaluation remains in the QCIS compiler. Consequently, every admitted Stage 4.1 flux input is already an
idle-relative `Phi/Phi0` increment and binds the exact mapper/setting evidence used to produce it.

Stage 4.1 physics admission requires QCIS v0.3. That compiler uses the calibrated q1/q2 reference frequencies
to compile each pulse's `(f_drive-f_ref)` phase ramp into the complex I/Q array at global absolute sample time.
The companion `07_qcis_compiler_v0_3_phase_amendment.md` is the authority for that formula. Stage 4.1 does not
repeat it.

## 3. Version and compatibility boundaries

The new public contracts use schema and artifact version `0.1` under distinct Stage 4.1 artifact types. They do
not change the accepted Stage 4 `stage_04_control_signal` schema `0.1`.

The existing public entry remains strict and unchanged:

```text
compile_control_schedule(LogicalSchedule, ControlChainConfig, ControlBuildContext)
    -> ControlCompilationResult
```

The new public entry is separate:

```text
compile_qcis_waveform_plan(
    plan: QCISLogicalWaveformPlan,
    context: ParameterizedControlContext,
) -> ParameterizedControlCompilation
```

A shared private electronics kernel may be extracted only after a byte-for-byte oracle proves that all accepted
Stage 4 payloads, AWG codes, effective arrays, metrics, reports, receipts, and hashes are unchanged. The Stage 4
wrapper continues to enforce the frozen scenario order and cannot delegate validation to the more general Stage
4.1 schedule validator.

### 3.1 Retirement of current identity aliases

The current `QCISCompilation.effective_*` arrays are identity-electronics compatibility fixtures: they alias the
five logical arrays and are not Stage 4.1 results. Likewise, the current coefficient inventory describes logical
array hashes; it is not a Stage 5.1 coefficient object.

The migration is:

1. Mark `effective_*`, `verify_effective_controls`, and the current coefficient inventory as
   `identity_fixture_only` in their documentation and serialized fixture evidence.
2. Add the distinct `ParameterizedControlArtifact` and verifier; production code must accept only that type.
3. Move identity fixtures behind an explicit test-only compatibility API.
4. Remove the alias fields in the next QCIS compilation schema version. No automatic fallback is permitted.

An identity electronics configuration remains a valid Stage 4.1 test oracle, but its output must still be a real
published Stage 4.1 artifact with independent hashes. Equal array bytes do not make a QCIS compilation object an
effective-control artifact.

## 4. Input contracts

### 4.1 QCIS logical waveform plan

The Stage 4.1 adapter normalizes an admitted v0.3 plan into this exact logical inventory before compilation:

```text
schema_version
profile_id
point_id
concrete_source_sha256
ast_sha256
trace_sha256
sample_count
dt_ns
logical.xy_delta_GHz.q1/q2.{i,q}
logical.flux_delta_phi0.q1/q2/c
frame_reference_frequency_GHz.q1/q2
frame_reference_authority_sha256
array_inventory
logical_event_inventory
authority_sha256
```

`array_inventory` contains, for each logical lane, exact `{name,dtype,shape,unit,byte_length,sha256}`. Dtypes are
little-endian `<f8`; complex QCIS arrays are split into real I and Q `<f8` arrays at the Stage 4.1 boundary. All
arrays are one-dimensional, C-contiguous, finite, equal length, and read-only after admission.

The existing fields `q1_flux`, `q2_flux`, and `c_flux` mean idle-relative increments even though the short field
names omit `delta`. New Stage 4.1 payloads always use `flux_delta_phi0`; ambiguous `absolute_flux` input keys are
rejected.

`logical_event_inventory` contains the expansion event ID, source instruction index, QAgent, transition,
`f_drive`, `f_ref`, detuning, phase-rule ID, logical lane, half-open sample interval, and contribution hash for
every contribution. It is used to verify v0.3 phase replay and validate target ownership, physical-port,
AWG-lane, and shared-resource declarations. It never causes otherwise valid additive overlap to be removed or
reordered.

`dt_ns` must equal the bound control clock exactly. Stage 4.1 does not resample, interpolate, pad to a different
logical grid, or infer a clock from array length.

### 4.2 Parameterized control context

`ParameterizedControlContext` is immutable and contains:

```text
repository_root
output_root
stage4_1_design_authority
stage4_1_approval_authority
accepted_stage4_approval_authority
channel_registry
control_chain_config
device_limit_authority
compiler_source_snapshot
environment_snapshot
publication_policy
```

Each authority is bound by contained path, raw SHA-256, schema version, artifact type, status, and verification
report. Missing, stale, unapproved, out-of-root, symlinked, or wrong-version authorities fail before any output
reservation.

The first implementation binds the accepted Stage 4 `0.5 ns` clock, 16-bit half-even DAC, lane latency/FIR,
idle flux, and static mixing configuration. Experiments cannot override these electronics parameters. Any
change requires a new approved control-config version and content hash. The capability excludes readout lanes:

```text
logical coordinates: q1_i, q1_q, q2_i, q2_q, q1_flux, q2_flux, c_flux
AWG lanes:            q1_xy_i, q1_xy_q, q2_xy_i, q2_xy_q, q1_z, q2_z, c_z
effective coordinates:q1_i, q1_q, q2_i, q2_q, q1_flux, q2_flux, c_flux
```

All mapping is by exact names. Positional inference, especially an implicit q1/q2/c tuple order, is forbidden.

## 5. Logical semantics at the boundary

All QCIS waveforms are additive. Temporally overlapping source waveforms are summed sample by sample by the QCIS
compiler. Stage 4.1 receives and verifies the resulting aggregate arrays; it does not reject a plan merely
because its trace contains overlapping source events.

Physical admission applies to the aggregate signal. Non-finite sums, singular inverse mixing, or DAC/device-limit
violations reject the complete point. Overlap itself is never a resource conflict. A structural resource error
exists only when a lane has incompatible unit/group ownership, one physical port has conflicting owners, one
name resolves to multiple ports, a logical coordinate lacks a lane, or registry and matrix names disagree.
Stage 4.1 never silently clips, renormalizes, reorders, or drops a contributing waveform.

Absolute-timing QCIS instructions such as `PLS` and `PLSXY` have already been placed before this boundary. Stage
4.1 cannot apply `I` or `B` cursor effects and cannot apply `RZ`, pulse drive frequency, or detuning phase again.
The q1/q2 reference frequencies and their calibration authority are preserved for the Stage 5.1 rotating frame.

"Applied once" means "participates in a mathematical transform at exactly one named layer": QCIS applies gate,
`PLSXY`, applicable `RZ`, and pulse detuning phase when constructing complex I/Q; Stage 4.1 applies electronics
only; Stage 5.1 uses the fixed q1/q2 reference frequencies for its rotating frame and applies the single
Hamiltonian `2*pi*GHz = rad/ns` conversion. No downstream layer repeats an upstream phase rotation or detuning
ramp.

## 6. Electronics compilation algorithm

For one admitted plan with `N` logical samples, Stage 4.1 performs the following exact order.

### 6.1 Verify and materialize named desired coordinates

1. Recompute concrete-source, AST, trace, authority, and raw-array hashes.
2. Verify exact dtype, shape, unit, clock, sample count, and finite-value rules.
3. Split complex XY into the named desired vector
   `[q1_i,q1_q,q2_i,q2_q]` in GHz.
4. Build the named desired Z vector `[q1_delta,q2_delta,c_delta]` in `Phi/Phi0`.

No idle value is present in the desired Z vector.

### 6.2 Solve physical coordinates to AWG lanes

For each sample and group, solve the bound forward static-mixing equation:

```text
M_group * awg_lane_request = desired_physical_delta
```

The matrix shape, lane names, output-coordinate names, finiteness, determinant/condition limit, and configured
orientation are validated before solving. An inverse matrix is never accepted as a forward matrix by convention.

### 6.3 Apply latency precompensation

Let `Lmax` be the maximum latency over admitted Stage 4.1 lanes. Each requested lane is placed into an AWG vector
of length `N + Lmax` at offset `Lmax - lane.latency_samples`. This preserves a common logical sample center after
delivery. Negative placement, inconsistent clock units, or a latency above the configured bound rejects.

### 6.4 Quantize without clipping

Each requested voltage is converted to a DAC code using the bound Stage 4 rule: binary64 value to exact decimal,
division by the configured LSB, and half-even rounding. A code below `code_min` or above `code_max` rejects the
point. Saturating arithmetic and silent clipping are forbidden.

The reconstructed voltage must differ from the requested voltage by no more than one half LSB, subject only to
the frozen numerical comparison tolerance.

### 6.5 Deliver each lane

The reconstructed voltage is convolved with the lane's finite FIR coefficients in full mode, then delayed by the
lane's configured latency. All output padding is explicit zero padding. The artifact records requested voltage,
DAC codes, reconstructed voltage, and delivered voltage for every admitted lane.

### 6.6 Apply forward mixing and reconstruct physical controls

At every delivered sample, apply the same bound forward matrix to the delivered lane vector. This produces:

```text
effective.xy_drive_GHz.q1/q2.{i,q}
effective.flux_delta_phi0.q1/q2/c
```

Only now add the bound named idle vector once:

```text
effective.absolute_flux_phi0[name]
    = control_config.idle_flux_phi0[name]
    + effective.flux_delta_phi0[name]
```

The idle vector is not passed through inverse mixing, DAC, FIR, latency, or crosstalk. No other layer may add it.
The artifact retains both delta and absolute effective flux so verification can prove this identity exactly.

### 6.7 Apply device and control checks

Checks use limits from accepted device/control authorities, not user-defined gate-setting minima or maxima. The
logical aggregate absolute flux (`idle + logical delta`), requested lane, DAC code, delivered physical
coordinate, and effective absolute flux must satisfy their respective bound. A missing required limit is an
admission error; Stage 4.1 does not invent a default.

### 6.8 Preserve the expanded time axis

The effective sample grid retains QCIS `t=0` as its origin. Latency precompensation may create negative sample
centers before that origin, and FIR/latency delivery creates a positive tail after the logical program. Neither
Stage 4.1 nor Stage 5.1 rebases this grid to zero. Future evolution starts at the first effective edge and ends
at the final tail edge so carrier phase remains referenced to QCIS `t=0`.

## 7. Output model

`ParameterizedControlCompilation` is an in-memory immutable result. It becomes authoritative only after
publication and independent verification.

The canonical `control.json` payload has exact top-level fields:

```text
schema_version
artifact_type = stage_04_1_parameterized_control
artifact_version
control_id
point_id
status
clock
source_binding
authority_binding
logical_inventory
awg
effective
metrics
checks
```

`clock` records `dt_ns`, logical/effective sample counts, logical centers, AWG centers, and effective centers.
`source_binding` records the concrete QCIS, AST, trace, and logical-plan hashes. `authority_binding` records all
Stage 4.1, Stage 4, registry, config, device-limit, source, and environment hashes.

`effective` contains:

```text
time_center_ns
xy_drive_GHz.q1/q2.{i,q}
flux_delta_phi0.q1/q2/c
absolute_flux_phi0.q1/q2/c
frame_reference_frequency_GHz.q1/q2
```

Canonical JSON carries metadata only. Numeric arrays are separate raw binary files: floating-point arrays use
little-endian `<f8`; XY is stored as separate real I/Q arrays; DAC-code dtype is fixed by the frozen DAC schema.
Every inventory row records `{name,dtype,shape,unit,byte_length,sha256}`. Full arrays are not duplicated as JSON
float lists. The canonical effective-control SHA covers the exact metadata plus raw array bytes.

`control_id` is content-derived from the canonical source binding, authority binding, array inventory, and
effective-control SHA. Wall clocks, process IDs, temporary paths, and publication location cannot enter it.
`point_id` is part of the source binding, so two points never share a handle merely because their final waveform
bytes happen to match.

### 7.1 Required checks and metrics

The exact check set is:

```text
logical_plan_schema_valid
logical_plan_hashes_valid
logical_arrays_valid
authority_bindings_valid
sample_grid_exact
named_mapping_exact
static_matrices_valid
latency_alignment_exact
no_dac_clipping
quantization_error_within_bound
forward_reconstruction_matches_reference
idle_added_exactly_once
effective_arrays_finite
device_limits_satisfied
stage4_compatibility_approval_valid
```

Every check contains `{name,passed,reason_code,evidence_ref}`. A failed check makes the compilation
`rejected`; rejected results are diagnostic only and cannot publish an effective-control artifact. A typed
failure may be recorded in the owning Stage 7 point/run evidence, but no partial control directory or handle is
authoritative.

## 8. Publication and VerifiedControlHandle

Successful publication uses sibling staging and atomic no-replace directory rename. A completed artifact
directory contains:

```text
control.json
arrays/logical/*.bin
arrays/awg/*.bin
arrays/effective/*.bin
array_inventory.json
source_snapshot.json
environment_snapshot.json
manifest.json
verification_report.json
receipt.json
```

Every scan point publishes its own directory. The first version does not perform cross-point cache reuse or blob
deduplication. A later content-addressed array store may reduce storage only if each point retains an independent
and fully verifiable evidence binding.

Manifest inventories payload files only. The verification report binds the manifest. The receipt binds the
manifest, report, payload hashes, source plan hash, authority hashes, and parent Stage 4.1 approval hash without
forming a hash cycle.

`VerifiedControlHandle` retains the already proposed exact fields:

```text
schema_version
control_id
artifact_root
manifest_sha256
receipt_sha256
inventory_sha256
effective_control_sha256
verification_report
```

Only `verify_parameterized_control_artifact(...)` may construct it. Construction must:

1. admit a repository/output-root-contained, no-follow artifact path;
2. independently validate exact filenames, schemas, inventories, hashes, status, and authorities;
3. recompute logical-to-effective electronics output from raw logical arrays;
4. open effective arrays read-only only after verification succeeds.

Stage 4.1 verification begins from the published logical plan and does not reparse QCIS. The owning Stage 7
verifier separately rematerializes QCIS, reparses the AST, reruns setting/mapper expansion, regenerates the
logical plan, and then invokes Stage 4.1 replay. This preserves module ownership while providing end-to-end
verification.

The handle is process-local capability, not a serializable trust token. A future Stage 5.1 backend rechecks the
receipt, inventory, and effective-control hash immediately before each execution.

The first artifact version retains the full electronics chain for every admitted lane: logical delta,
requested voltage, DAC code, reconstructed voltage, delivered-after-FIR/latency voltage, effective delta, and
effective absolute flux. This is intentionally larger than a final-waveform-only artifact so verification and
diagnostics can localize every transformation.

## 9. Stable failure categories

Public failures use a typed `ParameterizedControlError` with one stable category and structured detail. Initial
categories are:

```text
PLAN_SCHEMA_INVALID
PLAN_AUTHORITY_MISMATCH
PLAN_HASH_MISMATCH
ARRAY_CONTRACT_INVALID
CLOCK_MISMATCH
NAMED_MAPPING_INVALID
CONTROL_AUTHORITY_INVALID
MIXING_MATRIX_INVALID
LATENCY_CONTRACT_INVALID
DAC_RANGE_EXCEEDED
QUANTIZATION_BOUND_EXCEEDED
DEVICE_LIMIT_EXCEEDED
FORWARD_RECONSTRUCTION_MISMATCH
IDLE_RECONSTRUCTION_MISMATCH
LEGACY_REGRESSION_MISMATCH
PUBLICATION_CONFLICT
ARTIFACT_VERIFICATION_FAILED
```

Messages may add detail, but tests and callers depend only on the stable category. No category authorizes
best-effort output. A failure before publication leaves no artifact; interrupted staging is quarantined or
removed by the separately authorized recovery path and is never resumed as the original attempt.

## 10. Stage 5.1 boundary

Stage 5.1 is a separate design and approval. This draft freezes only what Stage 4.1 will hand to it.

Stage 5.1 may consume from a `VerifiedControlHandle` only:

- effective sample centers/edges;
- q1/q2 effective complex XY in GHz;
- q1/c/q2 named effective absolute flux in `Phi/Phi0`;
- q1/q2 reference frequencies and their accepted calibration authority;
- exact array, control, authority, and environment hashes.

Stage 5.1 may not consume QCIS text, AST nodes, gate/settings, mapper definitions, scan coordinates, logical
pre-electronics samples, AWG codes, or unpublished paths.

For each accepted zero-order-hold interval it will construct:

```text
H_static(Phi_q1[k], Phi_c[k], Phi_q2[k])
  + H_drive(epsilon_q1[k], epsilon_q2[k], fixed reference frame)
```

Named tensor mapping remains `q1/c/q2`. Hamiltonian quantities remain in GHz until one documented conversion
`2*pi*GHz = rad/ns`. Stage 5.1 cannot reapply QCIS `RZ`, a pulse drive frequency, or the detuning ramp already
encoded in effective complex I/Q.

The effective time grid is passed without rebasing. Its first edge may be negative relative to QCIS `t=0`, and
the final edge includes the complete FIR/latency tail.

## 11. Verification matrix

### 11.1 Contract and tamper tests

- wrong plan/profile/version, missing key, extra key, wrong dtype/endianness/layout, unequal length, NaN/Inf;
- changed source, AST, trace, setting/mapper authority, array byte, config, registry, or approval hash;
- wrong `dt_ns`, point ID, coordinate name/order, carrier target, or array unit;
- path escape, symlink/junction/reparse point, case alias, missing file, extra file, and coordinated rehash attack.

### 11.2 Electronics tests

- identity electronics produces byte-exact logical/effective deltas in a real Stage 4.1 artifact;
- nonzero idle with zero delta produces the named idle triple and proves idle is added exactly once;
- a nonzero delta proves `absolute - delta == idle` for every output sample;
- simultaneous QCIS source waveforms remain summed and are rejected only if the aggregate violates a bound;
- inverse then forward named mixing matches the reference within the frozen error budget;
- unequal lane latency aligns a one-sample impulse exactly;
- FIR output length, zero padding, and sample-center grid match exact formulas;
- half-LSB ties use half-even rounding; one-code overflow rejects without clipping;
- q1/q2/c crosstalk orientation and named mapping cannot be transposed;
- device-bound violation is detected on the effective aggregate, not on each source waveform separately.

### 11.3 Compatibility tests

- accepted Stage 4 smoke and formal inputs produce byte-identical payloads and artifacts before and after kernel
  extraction;
- frozen Stage 4 rejection cases remain rejected with unchanged public behavior;
- Stage 4.1 rejects readout lanes and does not alter Stage 8 ownership;
- QCIS identity fixtures cannot construct a `VerifiedControlHandle`.

### 11.4 Cross-boundary tests

- only a post-publication verified handle reaches the Stage 5.1 adapter;
- handle tamper between verification and execution is detected;
- coefficient inventory proves QCIS detuning phase is not reapplied and Hamiltonian `2*pi` conversion occurs
  exactly once;
- v0.3 `X` and `X12` coexist with distinct drive frequencies but one bound qubit reference frame;
- Stage 6 request `0.1` with `program=null` remains unchanged and cannot select the physics backend;
- fake backends cannot publish model-derived results or calibration recommendations.

### 11.5 Determinism tests

- the same source, configuration, and environment snapshot produces byte-identical arrays and `control_id`;
- clock, locale, process ID, temporary path, and publication root cannot affect deterministic content;
- Python, NumPy, BLAS, or platform changes alter the bound environment and therefore produce a distinct
  `control_id` when raw bytes differ;
- cross-platform qualification uses frozen numerical/physical tolerances rather than claiming identical raw
  floating-point hashes across different numerical environments.

## 12. Implementation sequence

### 12.1 Stage 4.1-A: contract and compatibility oracle

1. Freeze the QCIS v0.3 phase amendment, this design, and their exact schemas.
2. Implement and approve QCIS v0.3 without changing v0.2 byte oracles.
3. Capture accepted Stage 4 smoke/formal byte oracles.
4. Add immutable Stage 4.1 input/output models and strict validators.
5. Label the current QCIS `effective_*` compatibility surface as identity-fixture-only.

No electronics refactor is allowed until the compatibility oracle is independently passing.

### 12.2 Stage 4.1-B: parameterized electronics compiler

1. Extract the common electronics kernel under the accepted Stage 4 wrapper.
2. Implement `compile_qcis_waveform_plan` with the exact pipeline in Section 6.
3. Add identity, idle-once, mixing, latency, FIR, DAC, aggregation, and tamper tests.
4. Prove accepted Stage 4 artifacts remain byte-identical.

### 12.3 Stage 4.1-C: artifact publication and verification

1. Implement immutable artifact publication and inventories.
2. Implement independent replay verification.
3. Implement process-local `VerifiedControlHandle` construction.
4. Generate cross-platform evidence and complete independent review.

Successful Stage 4.1-C approval opens only the control-artifact boundary.

### 12.4 Subsequent separately gated work

After Stage 4.1 approval:

1. complete Stage 5 formal-scale qualification;
2. design, implement, and approve Stage 5.1 parameterized evolution;
3. obtain `Stage6ReadinessReport.stage6_ready=true`;
4. freeze Stage 6 request `0.2` and its physics-backend capability gate;
5. approve the Stage 7 model-evolution entrance and experiment-specific constants;
6. begin Stage 7.1 q1 spectroscopy, Rabi, Ramsey, and DRAG in that order.

No QuTiP calibration scan or accepted-simulation calibration is authorized by this draft.

## 13. Confirmed review decisions

The user confirmed the following choices individually on 2026-07-16:

1. The next implementation tranche is Stage 4.1 only; Stage 5.1 is an interface consumer described here but is
   not implemented in the same tranche.
2. QCIS flux arrays are canonically idle-relative increments; Stage 4.1 adds named idle exactly once and publishes
   both effective delta and absolute flux.
3. Mapper evaluation remains wholly upstream in QCIS/setting compilation; Stage 4.1 accepts only `Phi/Phi0`.
4. Logical overlap is additive; admission is based on the aggregate waveform and physical bounds.
5. The accepted Stage 4 public API and artifact bytes are immutable compatibility oracles.
6. `VerifiedControlHandle` is created only after immutable publication and independent replay verification.
7. The first version binds the accepted Stage 4 clock/electronics configuration and excludes readout.
8. Device bounds come only from accepted device/control authorities and are checked before and after electronics.
9. Every scan point owns an independent artifact containing the complete intermediate electronics chain.
10. Failed points publish no control artifact or handle; only structured run diagnostics may persist.
11. QCIS owns pulse/RZ/detuning phase, Stage 4.1 owns electronics, and Stage 5.1 owns the fixed rotating frame and
    Hamiltonian angular conversion.
12. The signed effective time axis preserves QCIS `t=0` and includes precompensation and delivery tail.
13. All channel/mode mapping is by exact name; overlap is additive and only structural ownership is a conflict.
14. `control_id` is deterministic and content-derived, with `point_id` and environment in its binding.
15. Arrays use raw little-endian binary files plus canonical JSON inventories.
16. Same-environment output is byte-deterministic; cross-environment qualification is tolerance-based.
17. Stage 4.1 replay starts from the logical plan; Stage 7 owns full QCIS-to-control replay.
18. QCIS v0.3 uses the calibrated f01 as `f_ref`; each pulse compiles `(f_drive-f_ref)` into complex I/Q.
19. Phase uses global QCIS absolute sample-center time and never resets implicitly across `I/B`.
20. Direct PLSXY phase is absolute and ignores accumulated RZ; calibrated gate macros consume RZ once.
21. X12 derives `f12=f01+anharmonicity`; direct PLSXY uses its explicit drive frequency.
22. Stage 4.1/5.1 receive only fixed q1/q2 reference frequencies, not a per-pulse carrier/phase input.
23. Baseband detuning obeys the strict complex-sampling Nyquist bound with no alias folding.
24. QCIS v0.3 uses independent absolute-time phase evaluation with `2*pi` remainder reduction per sample.
