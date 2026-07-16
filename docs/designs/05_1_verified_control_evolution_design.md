# Stage 5.1 Verified-Control Evolution Detailed Design

- Date: 2026-07-16
- Status: reviewed and frozen for Stage 5.1 implementation
- Depends on: accepted Stage 5 physics model, QCIS v0.3, and Stage 4.1 `VerifiedControlHandle`
- Opens: Stage 5.1 implementation and qualification only
- Keeps closed: Stage 6 physical backend and Stage 7 model scans

## 1. Purpose

Stage 5.1 is the only allowed numerical bridge from a verified parameterized control point to QuTiP evolution.
It replaces the fixed-scenario Stage 5 control input with `VerifiedControlHandle` while preserving the accepted
Hamiltonian, tensor ordering, frame convention, solver isolation, and single angular-frequency conversion.

The authorized direction is:

```text
VerifiedControlHandle
  -> Stage51EvolutionInput admission
  -> immutable EvolutionCoefficientPlan
  -> independently verified coefficient artifact
  -> isolated QuTiP worker
  -> immutable Stage51EvolutionArtifact
```

QCIS text, AST, scan coordinates, settings, mapper definitions, logical arrays, AWG values, and DAC codes may
not enter Stage 5.1. A future Stage 7 verifier may trace the handle back through Stage 4.1, but the evolution
kernel cannot use that upstream information numerically.

## 2. Scope and non-goals

Stage 5.1 owns:

- immediate revalidation of the process-local handle and its published receipt/inventory hashes;
- exact signed time-grid admission and zero-order-hold edge construction;
- named `q1/c/q2` absolute-flux and `q1/q2` complex-drive admission;
- construction of flux-dependent static Hamiltonian coefficients and XY RWA coefficients;
- the single `GHz -> 2*pi rad/ns` conversion;
- isolated QuTiP execution, deterministic observables, coefficient/result artifacts, and independent replay.

Stage 5.1 does not own:

- QCIS parsing, macro expansion, pulse phase, detuning ramps, mapper evaluation, or electronics;
- calibration-setting selection or mutation;
- scan expansion, scheduling, recommendations, fitting, or acceptance decisions;
- readout, resonators, IQ, shots, assignment, noise, dissipation, or measurement claims;
- Stage 6 backend registration or a non-null runtime program.

The initial version is closed-system state evolution only. Density matrices, collapse operators, stochastic
noise, and measurement require a new schema and design approval.

## 3. Input contract

### 3.1 VerifiedControlHandle

The sole numerical control input is a handle constructed by the Stage 4.1 verifier. Stage 5.1 admits exactly:

```text
schema_version = 0.1
control_id
artifact_root
manifest_sha256
receipt_sha256
inventory_sha256
effective_control_sha256
time_center_ns
xy_drive_GHz.q1/q2.(i,q)
absolute_flux_phi0.q1/q2/c
frame_reference_frequency_GHz.q1/q2
verification_report
```

Before coefficient construction and again immediately before worker launch, Stage 5.1 reopens the immutable
artifact and checks the receipt, inventory, effective-control hash, control ID, exact file set, and report. It
must equal the supplied handle. A handle is not a serializable trust token and cannot be reconstructed from a
JSON object supplied by an experiment.

All arrays must be read-only, one-dimensional, C-contiguous, finite, and equal length. Names are exact; tuple
position may not imply `q1/c/q2` ownership. Readout-like names or extra arrays reject the complete point.

### 3.2 Physics authority

`Stage51PhysicsContext` is immutable and contains accepted, hash-bound authorities for:

```text
repository_root
output_root
stage5_1_design_authority
stage5_1_approval_authority
accepted_stage5_physics_authority
accepted_device_artifact
accepted_hamiltonian_artifact
solver_validation_approval
source_snapshot
environment_snapshot
publication_policy
```

The accepted Stage 5 Hamiltonian definitions are reused without reinterpretation. The context fixes tensor
order `q1,c,q2`, charge cutoffs, local dimensions, operator construction, solver version/options, tolerances,
and initial-state rule. Experiments cannot override them. Missing, stale, unapproved, symlinked, out-of-root,
or hash-mismatched authorities fail before output reservation.

The numerical source is a separately approved `Stage51PhysicsAuthority`, not the legacy Stage 5 runtime config.
The legacy config contains Stage 4 logical schedule and control paths and therefore may be cited as review
provenance but may never be opened by the Stage 5.1 numerical path. The authority has exact schema:

```text
schema_version = 0.1
artifact_type = stage_05_1_physics_authority
artifact_version = 0.1
status = approved
device = {path, raw_sha256}
hamiltonian = {path, raw_sha256}
model = {
  tensor_order: [q1, c, q2],
  charge_cutoffs: [1, 1, 1],
  reference_state_count: 16
}
frame = {rwa_projection: number_sector_v1}
solver = <the exact accepted Stage 5 smoke solver mapping>
tolerances = <the exact accepted Stage 5 tolerance mapping>
accepted_stage5_bindings = {design_path/sha256, amendment_path/sha256}
source_snapshot_sha256
environment_snapshot_sha256
publication_policy_sha256
authority_id
```

`authority_id` is the SHA-256 of the canonical payload excluding only `authority_id`. A separate approval
artifact binds the exact authority ID, authority raw-file hash, this design raw-file hash, approval record, and
reviewer role. Device and Hamiltonian paths must be repository-relative regular files and are admitted with the
same no-follow, exact-key, canonical-value, and raw-hash rules as other Stage 5.1 authorities. Rebuilding the
model may read only those two numerical files. Source/environment snapshots and approval records are evidence,
not alternative numerical inputs.

## 4. Time and control semantics

Let `N` be the handle sample count and `dt=0.5 ns`. The signed centers must satisfy exactly:

```text
center[k] = center[0] + k*dt
edge[0] = center[0] - dt/2
edge[k+1] = center[k] + dt/2
```

The first center may be negative because Stage 4.1 preserves latency precompensation around QCIS `t=0`.
Stage 5.1 never rebases, trims, resamples, interpolates, or extrapolates the grid. Sample `k` is held on the
half-open interval `[edge[k], edge[k+1])`; the final interval is right-closed for result evaluation only.

For each sample:

```text
epsilon_q[k] = I_q[k] + i*Q_q[k]
phi[k] = {q1: q1_flux[k], c: c_flux[k], q2: q2_flux[k]}
```

`epsilon` already contains QCIS gate phase, setting phase, applicable RZ, and `(f_drive-f_ref)` absolute-time
phase. Stage 5.1 must not apply any of them again. Absolute flux already contains idle exactly once; Stage 5.1
must not subtract or add idle and must not run `F012ZBIAS_MAPPER` or `G2ZBIAS_MAPPER`.

## 5. Frame and Hamiltonian construction

Stage 5.1 keeps the accepted interaction-picture convention. For fixed references from the handle:

```text
U(t) = exp[-i*2*pi*t*(f_ref[q1]*N_q1 + f_ref[q2]*N_q2)]
```

At hold `k`, construct the complete accepted charge-basis Hamiltonian in GHz at the named absolute flux triple:

```text
H_static_GHz[k] = H_charge
                    + H_Josephson(phi_q1[k], phi_c[k], phi_q2[k])
                    + H_coupling
```

No term may be dropped, averaged, diagonalized away, or inferred from scan coordinates. The sole drive RWA is:

```text
H_drive_IP_GHz[k] = 1/2 * sum_q (
    epsilon_q[k] * D_q_plus + conjugate(epsilon_q[k]) * D_q_minus
)

H_IP_GHz(t,k) = U(t).dag * H_static_GHz[k] * U(t)
                - f_ref[q1]*N_q1 - f_ref[q2]*N_q2
                + H_drive_IP_GHz[k]

H_rad_per_ns(t,k) = 2*pi * H_IP_GHz(t,k)
```

The existing accepted operator construction defines `N_q`, `D_q_plus`, and `D_q_minus`. All algebra remains in
GHz until the named conversion function `angular_rad_per_ns`; no other module may contain a numerical `2*pi`,
`1e9`, or `1e-9` conversion for Hamiltonian coefficients.

Hermiticity, dimensions, tensor labels, finite coefficients, and operator hashes are checked before QuTiP sees
the plan and independently inside the worker.

## 6. EvolutionCoefficientPlan

The in-memory immutable plan has exact logical fields:

```text
schema_version = 0.1
coefficient_plan_id
control_binding
physics_authority_binding
clock
frame_reference_frequency_GHz
operator_inventory
coefficient_inventory
initial_state_spec
observable_spec
solver_spec
checks
```

`control_binding` contains the control ID and handle manifest/receipt/inventory/effective hashes.
`physics_authority_binding` contains all accepted device, Hamiltonian, design, approval, source, environment,
and solver-validation hashes. `coefficient_plan_id` is content-derived from both bindings, signed time axes,
operator inventory, coefficient raw bytes, initial-state specification, and solver specification.

The coefficient inventory stores raw little-endian arrays:

```text
time_center_ns              <f8  ns
time_edge_ns                <f8  ns
epsilon_q1                  <c16 GHz
epsilon_q2                  <c16 GHz
absolute_flux_q1            <f8  Phi/Phi0
absolute_flux_c             <f8  Phi/Phi0
absolute_flux_q2            <f8  Phi/Phi0
```

Static Hamiltonians are regenerated from the accepted physics authority during verification rather than stored
as an unbounded `N x D x D` JSON payload. The first version may store content-addressed raw operator matrices if
profiling proves regeneration is the runtime bottleneck; that optimization requires byte-identical replay and
does not change the public plan.

## 7. Initial state and observables

The initial-state and labeling rules remain those accepted by Stage 5 unless separately changed by review:

- form the complete lab-frame static Hamiltonian at the first effective absolute-flux sample;
- choose its phase-fixed lowest eigenvector as `g_lab`; phase fixing selects the first maximum-magnitude
  component, makes it real and non-negative, and rejects a nonfinite or zero vector;
- start the interaction-picture solver with `psi_IP(t0)=U(t0).dag*g_lab`;
- construct and validate the accepted lab-frame computational projectors in `q1,c,q2` order;
- transform projectors into the interaction frame at each requested edge;
- report populations for `000,100,001,101`, leakage, norm error, and final-state fidelity evidence.

Every computational projector must be finite and Hermitian and pass both
`||P_i^2-P_i|| <= projector_orthogonality` and, for `i != j`,
`||P_i P_j|| <= projector_orthogonality`. Projector matrices and their hashes are part of the result evidence.
The initial and final state bytes must be unchanged when an eigensolver supplies an otherwise equivalent input
eigenvector with a different global phase.

The Stage 5.1 fidelity is verifier replay consistency, not gate fidelity:

```text
replay_fidelity = |<psi_final_worker | psi_final_independent_replay>|^2
```

The independent verifier reruns the approved bounded smoke evolution from the published coefficient artifact
and physics authority, phase-aligns only for diagnostics, and evaluates the phase-invariant expression above.
No target gate, target ket, process fidelity, QPT, XEB, or calibration claim is inferred. Those belong to the
owning calibration experiment.

Stage 5.1 does not infer a gate label or target state from QCIS. A future calibration experiment owns the target
observable and fit interpretation outside this kernel.

## 8. Worker boundary and execution

The parent process validates and publishes a coefficient artifact before execution. The isolated worker receives
only its immutable path plus the accepted physics/solver authority paths. It rejects QCIS, logical controls,
AWG arrays, scan coordinates, and readout fields.

The worker:

1. independently verifies coefficient and authority hashes;
2. reconstructs operators and coefficient callbacks;
3. checks dimensions, Hermiticity, edge coverage, and the single angular conversion;
4. invokes the pinned QuTiP solver with exact approved options;
5. returns finite states/observables and deterministic diagnostics;
6. writes no final artifact directly.

The parent validates worker output, applies runtime/numerical gates, and atomically publishes. Timeout,
interpreter mismatch, worker crash, partial output, nonfinite value, or solver warning produces no successful
artifact and no physics claim.

## 9. Artifacts and verification

Each point has its own coefficient and evolution directories. The coefficient artifact contains:

```text
coefficient_plan.json
array_inventory.json
arrays/*.bin
source_snapshot.json
environment_snapshot.json
manifest.json
verification_report.json
receipt.json
```

The evolution artifact contains result arrays, state/observable inventory, solver diagnostics, the complete
control/coefficient/physics binding, and the same acyclic `manifest -> report -> receipt` topology used by
Stage 4.1. Publication is sibling staging plus same-volume atomic no-replace rename. Failed points publish no
successful artifact or reusable result handle.

Both artifact schemas use exact-key canonical JSON metadata plus raw little-endian arrays. The coefficient
inventory records, for every array, exact logical name, relative path, dtype, shape, element count, byte count,
and raw SHA-256. The evolution inventory records:

```text
states/initial_state.bin     <c16 [D]
states/final_state.bin       <c16 [D]
observables/population_000   <f8  [N+1]
observables/population_100   <f8  [N+1]
observables/population_001   <f8  [N+1]
observables/population_101   <f8  [N+1]
observables/leakage          <f8  [N+1]
observables/norm_error       <f8  [N+1]
```

The evolution payload also records solver diagnostics, projector hashes, replay fidelity, and exact control,
physics-authority, coefficient-plan, coefficient-manifest, source, and environment bindings. The manifest
hashes the payload, inventory, raw arrays, and snapshots. The verification report binds the manifest hash and
all required checks. The receipt binds the manifest hash, report hash, control ID, coefficient plan ID,
physics authority ID, and result ID. No file may hash a downstream file, preserving the acyclic topology.

The independent verifier starts from the coefficient raw arrays and accepted physics authorities. It rebuilds
edges, complex drives, flux triples, operators, initial state, and selected Hamiltonian probes; it then checks
the worker result and receipt and, for the bounded smoke profile, independently reruns the evolution to compute
replay fidelity. The owning Stage 7 verifier separately traces the control ID back through QCIS and Stage 4.1.

## 10. Public API draft

```text
admit_verified_control(
    handle: VerifiedControlHandle,
    context: Stage51PhysicsContext,
) -> Stage51EvolutionInput

build_evolution_coefficient_plan(
    admitted: Stage51EvolutionInput,
    context: Stage51PhysicsContext,
) -> EvolutionCoefficientPlan

publish_evolution_coefficient_artifact(
    plan: EvolutionCoefficientPlan,
    context: Stage51PhysicsContext,
    output_dir: Path,
) -> VerifiedCoefficientHandle

run_verified_control_evolution(
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
    output_dir: Path,
) -> Stage51EvolutionArtifactSet

verify_stage51_evolution_artifact(
    artifact_root: Path,
    coefficients: VerifiedCoefficientHandle,
    context: Stage51PhysicsContext,
) -> VerifiedEvolutionHandle
```

The API is intentionally separate from accepted Stage 5 fixed-scenario functions. No compatibility fallback may
turn a QCIS `effective_*` identity fixture or an unpublished Stage 4.1 compilation into a handle.

## 11. Stable failure categories

```text
HANDLE_SCHEMA_INVALID
HANDLE_NOT_PUBLISHED
CONTROL_BINDING_MISMATCH
CONTROL_ARRAY_INVALID
CONTROL_CLOCK_MISMATCH
FRAME_AUTHORITY_MISMATCH
PHYSICS_AUTHORITY_INVALID
TENSOR_MAPPING_INVALID
OPERATOR_CONSTRUCTION_FAILED
COEFFICIENT_PLAN_INVALID
ANGULAR_CONVERSION_VIOLATION
INITIAL_STATE_INVALID
OBSERVABLE_SPEC_INVALID
SOLVER_AUTHORITY_INVALID
WORKER_ADMISSION_FAILED
WORKER_TIMEOUT
WORKER_EXECUTION_FAILED
NUMERICAL_RESULT_INVALID
PUBLICATION_CONFLICT
ARTIFACT_VERIFICATION_FAILED
```

Failures are typed and fail closed. Messages may add diagnostics; callers depend only on the stable category.

## 12. Required checks

The coefficient gate requires every check to pass:

```text
verified_control_handle_valid
control_receipt_rechecked
effective_arrays_exact
signed_sample_grid_exact
zero_order_hold_edges_exact
named_tensor_mapping_exact
frame_reference_authority_valid
phase_not_reapplied
idle_not_reapplied
physics_authorities_valid
operators_finite_and_hermitian
coefficient_arrays_finite
angular_conversion_applied_once
initial_state_valid
initial_state_phase_canonical
computational_projectors_valid
solver_authority_valid
```

The evolution gate additionally checks worker identity, solver options, result shape/finiteness, norm,
population bounds, leakage, state/projector hashes, runtime, independent replay, and exact artifact bindings.

## 13. Verification matrix

Tests must include:

- real QCIS v0.3 -> Stage 4.1 artifact -> handle -> coefficient plan integration;
- negative Stage 4.1 time origin and positive FIR tail with exact edges;
- X and X12 coexistence without any second detuning or phase application;
- q1/q2 frame swap, q1/c/q2 flux swap, missing/extra/readout arrays, and stale handle rejection;
- idle-only, XY-only, Z-only, overlapping controls, quantization residuals, and device-limit boundaries;
- every control/authority/operator/coefficient/result single-byte tamper;
- duplicate `2*pi`, missing `2*pi`, direct QCIS access, mapper reuse, and logical/AWG access guards;
- two-level zero/constant/Rabi oracles and comparison against the accepted Stage 5 fixed scenario;
- global-phase perturbation with byte-identical serialized initial/final states, projector idempotence and
  pairwise orthogonality, and independent-replay fidelity;
- QuTiP worker crash, timeout, option drift, interpreter drift, nonfinite output, and partial publication;
- same-environment byte determinism and cross-environment numerical-tolerance evidence;
- Windows/POSIX path, symlink/junction, target-exists, and atomic-publication failures.

## 14. Migration and gates

Implementation sequence after review:

1. freeze handle admission, time/phase semantics, errors, and coefficient artifact schema;
2. implement pure coefficient construction and independent replay without launching QuTiP;
3. rebaseline accepted Stage 5 physics/operator oracles against handle-derived controls;
4. add isolated QuTiP worker and bounded smoke evolution;
5. complete tamper, determinism, timeout, and cross-platform qualification;
6. publish a separate Stage 5.1 approval and readiness report.

The frozen review decisions in Section 15 authorize this Stage 5.1 implementation sequence. Stage 6 non-null
programs, physical backend registration, and Stage 7 model-derived calibration scans still require their own
reviewed gates.

## 15. Frozen review decisions

The user confirmed all five Stage 5.1 review items on 2026-07-16:

1. Stage 5.1 retains the accepted Stage 5 closed-system interaction-picture Hamiltonian and initial-state rule.
2. The initial artifact records complete initial and final edge states, the four computational populations
   `000`, `100`, `001`, and `101`, normalization evidence, and computational-subspace leakage. It does not
   reduce the reusable result to one final scalar.
3. Coefficient artifacts are independently verified and published before QuTiP execution. Reuse requires an
   exact content-hash match; parameter similarity is not sufficient.
4. The first implementation profile uses accepted Stage 5 smoke cutoffs, solver options, and bounded sampling
   for integration and regression testing. Formal physical qualification requires a separate convergence and
   numerical-accuracy profile.
5. Dissipation, noise, readout, fitting, calibration recommendations, and calibration-setting updates remain
   outside Stage 5.1.
6. Stage 5.1 uses a separately approved physics authority containing only device, Hamiltonian, smoke model,
   frame, solver, tolerance, and provenance bindings. The legacy Stage 5 config is not a runtime input.
7. `replay_fidelity` is the phase-invariant final-state overlap between the worker and independent verifier
   replay. It is not a gate, process, QPT, or XEB fidelity.
8. Coefficient and evolution artifacts use exact-key JSON inventories, raw little-endian arrays, complete
   content hashes, and the acyclic `manifest -> verification_report -> receipt` publication topology defined
   in Section 9.

These decisions freeze the Stage 5.1 implementation boundary and authorize the implementation sequence in
Section 14. They do not authorize Stage 6 non-null programs, physical backend registration, or Stage 7 scans.
