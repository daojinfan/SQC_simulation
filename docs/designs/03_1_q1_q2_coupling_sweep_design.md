# Stage 3.1 detailed design: q1-q2 avoided crossing and coupler-controlled coupling

Version: 0.1 review candidate

Status: not frozen

## 1. Scope

Stage 3.1 replaces the Stage 3 v0.1 acceptance target while preserving all approved Stage 2.1 provenance,
Hamiltonian, deterministic solver, finite-artifact, and independent-approval requirements.

In scope:

- resolve the `q1-q2` avoided crossing at a fixed coupler flux;
- repeat the qubit-resonance scan at multiple coupler flux values;
- extract the minimum splitting and `abs(g_eff) = splitting / 2`;
- demonstrate statistically significant splitting modulation;
- identify and reject points where coupler participation prevents a two-qubit interpretation.

Out of scope:

- treating `q1-c` or `c-q2` crossings as Stage 3.1 success criteria;
- extracting the sign of `g_eff` from splitting alone;
- changing device capacitances, junction parameters, or the Stage 2 Hamiltonian;
- lowering convergence or continuity thresholds to obtain a pass;
- pulse design or time-domain gate simulation.

## 2. Scan coordinates

The device has tunable SQUIDs for `q1`, `c`, and `q2`. Stage 2 already supports simultaneous in-memory
overrides for all three modes. Stage 3.1 uses the full flux vector:

```text
FluxPoint(q1_phi0, c_phi0, q2_phi0)
```

The acceptance scan fixes:

```text
q1_phi0 = 0.10
q2 scan = [0.08, 0.12]
```

`q2` is the inner scan axis because it moves monotonically downward from its sweet spot through the
current `q1` frequency, while the resonance remains interior to the interval. The pilot locates it near
`q2=0.099755 Phi0`.

The deterministic coupler grid is:

```text
0.200, 0.270, 0.360, 0.380, 0.385, 0.390, 0.394, 0.396, 0.400 Phi0
```

Acceptance anchor points are `0.200`, `0.270`, and `0.385 Phi0`. All grid points are recorded. Points
from `0.390` through `0.400 Phi0` are diagnostic near the three-mode region and are rejected from the
pairwise claim whenever their coupler participation exceeds the limit. They are not selected as mandatory
anchors merely because their splitting modulation is larger.

The inner `q2` scan uses:

```text
coarse_points = 25
refinement_points = 11
min_refinement_levels = 4
max_refinement_levels = 8
flux_key_decimal_places = 12
```

Each refinement grid is built from the previous level's explicit bracket. Endpoints are reused through
the cache. The minimum must be interior. A boundary result is diagnostic failure, not an extrapolated
crossing.

Baseline level `L0` is the 25-point coarse grid. Each `L1..L8` grid contains 11 uniform Decimal12 keys
over the immediate-neighbor bracket from the preceding level; its two endpoints are cache hits. For each
level record the minimum, bracket, splitting, level-to-level splitting drift, and the Section 5
immediate-neighbor flux-energy estimate.

The baseline scan cannot stop before `L4`. At `L>=4`, it stops with `termination_reason=converged` only
when the two most recent level transitions both satisfy all of:

```text
minimum is interior
abs(Delta_level - Delta_previous_level) <= 0.005 MHz
U_inner_flux_at_level <= 0.005 MHz
the new bracket is a strict subset of the preceding bracket
```

Thus an L4 stop requires both L2->L3 and L3->L4 to pass. A boundary terminates immediately with
`boundary=true`; numerical convergence is not evaluated for that point. Missing, non-finite,
equal-width, or non-shrinking evidence does not count as a passing transition. If L8 completes without
two consecutive passing transitions and without a boundary, termination is `max_levels_exhausted`,
`numerically_converged=false`, and status is `numerically_unconverged` unless runtime failed.
Implementations may not choose to run all eight levels after the deterministic stop condition has passed.

### 2.1 Machine-readable configuration

The only formal config path is:

```text
configs/spectra/2q1c_q1q2_coupling.yaml
```

It is UTF-8 YAML with this complete schema. Unknown, missing, duplicate, non-finite, bool-as-number, or
wrong-type fields fail loading. Lists are ordered and duplicates are rejected after Decimal12
canonicalization.

```yaml
schema_version: "0.2"

spectrum:
  experiment_type: q1_q2_coupling_vs_coupler
  name: demo_2q1c_q1q2_coupling
  profile: acceptance
  acceptance_eligible: true
  source_hamiltonian_config: configs/hamiltonians/2q1c_charge_basis.yaml
  source_hamiltonian_artifacts: output/stage_02_hamiltonian/hamiltonian_artifacts.json
  source_rebaseline_manifest: output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json
  source_rebaseline_approval: output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json
  source_design_freeze_manifest: docs/decisions/2026-07-11-stage3-1-design-freeze.json

  eigen:
    num_states: 48
    solver: validated_eigsh
    eigsh:
      which: SA
      tolerance: 1.0e-10
      maxiter: 200000
      ncv: 97
      v0_rule: sha256_counter_v2
    solver_validation:
      gap_dense_tolerance_GHz: 1.0e-6
      q1_q2_splitting_dense_tolerance_GHz: 2.0e-6
      near_degenerate_gap_threshold_GHz: 1.0e-5
      partition_boundary_margin_GHz: 2.0e-6
      projector_error_norm: spectral_2
      projector_dense_tolerance: 1.0e-6
      projector_repeat_tolerance: 1.0e-8

  dressed_labeling:
    max_excitations: {q1: 2, c: 2, q2: 2}
    max_total_excitations: 2
    min_overlap: 0.50
    assignment: global_overlap

  metrics:
    compute_zz: true
    compute_anharmonicity: true
    compute_mode_participation: true
    compute_target_bare_projector: true

  scan:
    fixed_q1_flux_phi0: 0.10
    inner_target: q2
    inner_start_phi0: 0.08
    inner_stop_phi0: 0.12
    inner_coarse_points: 25
    refinement_points: 11
    min_refinement_levels: 4
    max_refinement_levels: 8
    flux_key_decimal_places: 12
    coupler_flux_points_phi0: [0.200, 0.270, 0.360, 0.380, 0.385, 0.390, 0.394, 0.396, 0.400]
    acceptance_anchor_fluxes_phi0: [0.200, 0.270, 0.385]
    reference_coupler_flux_phi0: 0.270

  evidence:
    bare_detuning_evidence_tolerance_MHz: 0.50
    resonance_alignment_tolerance_MHz: 0.50
    endpoint_character_min_fraction: 0.80
    character_exchange_min_delta: 0.60
    target_bare_projector_min_weight: 0.90
    target_total_excitation_min: 0.90
    target_total_excitation_max: 1.10
    coupler_fraction_max: 0.05
    target_subspace_continuity_min: 0.90

  convergence:
    cutoff_increment: 2
    crossing_refinement_coarse_points: 17
    crossing_refinement_levels: 4
    frequency_tolerance_MHz: 0.50
    anharmonicity_tolerance_MHz: 1.00
    zz_absolute_tolerance_MHz: 0.01
    crossing_absolute_tolerance_MHz: 0.01
    crossing_relative_tolerance: 0.05
    splitting_significance_min_ratio: 5.0
    modulation_significance_min_ratio: 5.0
    splitting_level_tolerance_MHz: 0.005
    flux_energy_resolution_MHz: 0.005

  runtime:
    acceptance_budget_seconds: 1800
    smoke_budget_seconds: 90
    conservative_solve_ceiling: 1381
    over_budget_fallback: fail
    solver_validation_artifact: output/stage_03_1_solver_validation/eigsh_validation.json
    solver_validation_approval: output/stage_03_1_solver_validation/eigsh_validation_approval.json
```

`profile` is exactly `acceptance`, `smoke`, `dense_pilot`, or `solver_validation`.
`acceptance_eligible` is true exactly for `acceptance`. Formal acceptance requires every literal value
shown above; smoke may reduce point counts only under a separate smoke config and is never acceptance
eligible. All paths resolve repository-relative, are included in config SHA-256 provenance, and must
match the Stage 2.1 manifest or runtime contract. The loader interface is:

```text
load_q1_q2_coupling_config(path) -> QubitCouplingConfig
```

The smoke path is `configs/spectra/2q1c_q1q2_coupling_smoke.yaml`. It has the same schema and differs
only as follows: `profile=smoke`, `acceptance_eligible=false`, `num_states=12`, `solver=dense_eigh`,
`ncv=25`, one coupler point `[0.270]`, empty acceptance anchors, inner coarse/refinement points `9/7`,
and minimum/maximum refinement levels `1/1`. Smoke sets computational status `not_run_smoke`,
`stage4_ready=false`, and does not evaluate modulation. Any other difference is rejected.

For acceptance, the anchor list must be an exact ordered subset of the coupler list, contain exactly
`[0.200,0.270,0.385]`, and contain the reference exactly once. Scan bounds must satisfy
`0 <= start < stop < 0.5`; point counts are odd integers >=3; minimum levels cannot exceed maximum;
all thresholds and budgets are finite positive values, fractions lie in `[0,1]`, and
`target_total_excitation_min < target_total_excitation_max`.

## 3. Full flux-vector identity

The current Stage 3 v0.1 cache and deterministic eigsh seed identify only coupler flux. Stage 3.1 must
replace that contract everywhere with all three canonical flux keys:

```text
(q1_cutoff, c_cutoff, q2_cutoff,
 q1_flux_key, c_flux_key, q2_flux_key,
 num_states, solver_backend)
```

Each flux key is Decimal text quantized to 12 places with ROUND_HALF_EVEN. The eigsh
`sha256_counter_v2` seed contains the same ordered fields in UTF-8/LF/no-BOM canonical form. Two models
that differ in any flux component must not share a cache entry or initial vector identity.

The byte-level seed is exactly:

```text
sqvm-eigsh-v0-v2\n
stage2_artifacts_sha256=<64 uppercase hexadecimal characters>\n
cutoff=q1=<unsigned decimal>,c=<unsigned decimal>,q2=<unsigned decimal>\n
q1_flux_phi0=<12-place Decimal text>\n
c_flux_phi0=<12-place Decimal text>\n
q2_flux_phi0=<12-place Decimal text>\n
num_states=<unsigned decimal>\n
```

There are no spaces, CR bytes, BOM, or trailing bytes after the final LF. Counter blocks use unsigned
64-bit big-endian encoding. Each digest is divided into four unsigned 64-bit big-endian words; the top
53 bits map to `2 * (word53 / 2**53) - 1`, and the final vector is normalized in float64 exactly as v1.

Normative vector input:

```text
stage2 artifact = DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66
cutoffs = q1=7,c=7,q2=7
fluxes = q1=0.100000000000,c=0.270000000000,q2=0.100000000000
num_states = 48
seed byte length = 224
SHA256(seed) = 48245D6E13E15E65DA68E7AD4E957E07F97F0C1DF6E182E1806D6B1F88027CA0
SHA256(seed || uint64_be(0)) = 44C000CA15E6923769C51CCC19623DAB311213490C1E50225D7E0AC79A33E322
```

`StaticSpectrumPointResult` is replaced or extended with:

```text
flux_keys: {q1, c, q2}
flux_biases_phi0: {q1, c, q2}
```

No API may infer the point identity from the coupler component alone.

The Stage 3.1 provenance report binds the raw config SHA-256, Stage 2 artifact, Stage 2.1 manifest and
approval, Hamiltonian config, device artifact, Stage 2 source tree, Stage 3.1 source tree, environment
fingerprint, solver-validation candidate, and solver approval. Every digest is recomputed from current
bytes. A path/hash/schema/version mismatch fails before scan analysis or output-directory creation.

After design approval, the independent reviewer creates canonical
`docs/decisions/2026-07-11-stage3-1-design-freeze.json` with identity
`schema_version=0.1`, `artifact_type=stage_03_1_design_freeze`, `artifact_version=0.1`,
`decision=approved`, `reviewer_role=independent_design_review_ai`, empty blocking findings,
review-record path/hash, and an exact `document_sha256` mapping for the objective decision, detailed
design, and execution plan paths. The provenance validator recomputes all three document hashes and the
manifest raw hash. Solver validation, formal artifact/report, and final independent approval bind that
manifest raw hash. A stale, missing, noncanonical, or self-declared replacement fails closed.

## 4. Branch and subspace tracking

The target pair is:

```text
q1-q2: |100> and |001>
spectator: |010>
```

At every inner-scan point the artifact records:

- the energies of all three single-excitation branches;
- one-to-one assignment and adjacent-point overlap;
- q1/c/q2 participation fractions for each branch;
- total mean excitation and target-bare-projector weight for each target branch;
- bare `q2-q1` detuning;
- the two-dimensional target-subspace continuity.

The target-subspace continuity is computed from the singular values of
`V_previous.conj().T @ V_next`, where each `V` contains the two tracked q1/q2 branch vectors. The hard
gate is the squared smallest singular value `>= 0.90`. Individual branch overlaps remain recorded as
diagnostics, but a basis rotation inside a valid near-degenerate q1/q2 subspace is not by itself a
failure. Non-finite singular values fail.

For each solved point, build the local bare projector from the same flux and cutoff model:

```text
P_q = |100><100| + |001><001|
target_bare_projector_weight(psi) = real(<psi|P_q|psi>)
```

This gate is distinct from normalized mode fractions. It prevents higher-excitation contamination from
passing merely because the normalized q1/c/q2 fractions add to one.

At each full flux vector and cutoff signature, bare detuning is computed from the two isolated single-mode
Hamiltonians built from that same model's diagonal `E_C,ii`, effective Josephson energy, and cutoff:

```text
H_i = 4 * E_C,ii * n_i^2 - EJ_eff_i * cos(phi_i)
f_bare_i_GHz = eigenvalue_i[1] - eigenvalue_i[0]
bare_q2_minus_q1_detuning_MHz = 1000 * (f_bare_q2_GHz - f_bare_q1_GHz)
```

Off-diagonal capacitive terms are excluded only from this isolated-mode diagnostic; they remain in the
full dressed Hamiltonian. Missing or non-finite isolated eigenvalues/detuning fail the evidence gate.

### 4.1 Deterministic evidence domain

The final baseline refinement provides `minimum_key` and immediate neighbor keys
`final_bracket_left/minimum/right`. Starting at the minimum on the merged, Decimal-sorted baseline grid:

1. Search leftward for the nearest key where both target branches have a dominant q1 or q2 mode fraction
   `>=0.80`.
2. Search rightward by the same rule.
3. The left and right keys must be distinct and strictly surround the minimum.
4. `final_evidence_keys` is every evaluated baseline key from the selected left key through the selected
   right key, inclusive, union the three final bracket keys.
5. Every adjacent edge in `final_evidence_keys` must have squared-minimum-singular-value continuity
   `>=0.90`; implementations may not select only favorable edges.

Missing keys, a non-interior minimum, a non-finite value, or inability to find both endpoints fails the
point. The three forced keys used by cutoff convergence are exactly the baseline final bracket
left/minimum/right keys.

Each refined-cutoff model starts with a 17-point uniform grid whose endpoints are the baseline selected
left/right character keys, union the baseline final bracket left/minimum/right keys. It then performs
four 11-point refinement levels with cached endpoints. It recomputes branch tracking, its own character
endpoints, full evidence domain, and continuity using only vectors from that same Hilbert dimension.
Every baseline evaluated key is not forced into the refined grid. Vectors from different cutoff
dimensions are never directly compared. Cross-cutoff comparison uses semantic q1-like/q2-like branches,
bare-projector weights, splitting, flux, and participation only.

### 4.2 Character, pairwise, and resonance evidence

The branch evidence must show all of the following:

1. Bare `q2-q1` detuning has at least one value `<=-0.50 MHz` and one value `>=+0.50 MHz` in
   `final_evidence_keys`.
2. Among adjacent final-evidence keys, a deterministic zero-detuning bracket exists. Qualifying pairs
   satisfy `delta_left * delta_right <= 0`; choose the pair minimizing
   `abs(delta_left)+abs(delta_right)`, then break ties by the ordered key pair.
3. Linear interpolation of the two finite detunings gives `bare_resonance_flux_phi0`; unequal endpoint
   detunings are required. The root must lie inside the final evidence domain and
   `abs(bare_detuning_at_minimum_MHz) <= 0.50`. It is not required to lie in the microscopic minimum
   bracket because a differential Lamb shift can move the dressed minimum away from the isolated-bare
   root while retaining a valid aligned two-level crossing.
4. At each selected endpoint the two branches have distinct dominant modes, one q1 and one q2, each
   `>=0.80`. For each branch, signed `q1_fraction-q2_fraction` changes sign, and the absolute
   endpoint-to-endpoint change is `>=0.60`.
5. At every `final_evidence_key`, both target branches have target bare-projector weight `>=0.90`, total
   mean excitation in `[0.90,1.10]`, and normalized coupler fraction `<=0.05`.
6. The minimum is interior and every target-subspace continuity edge passes Section 4.1.

Failure of item 5 due to coupler fraction gives `coupler_hybridized`; failure due to projector weight or
total excitation gives `pair_subspace_invalid`. Both statuses retain the observed splitting but set
`abs_g_eff_MHz=null` and cannot support a coupling-modulation claim.

## 5. Splitting and coupling metrics

For each coupler flux `phi_c`, define:

```text
Delta_q1q2(phi_c) = min over q2 flux of abs(E_branch_100 - E_branch_001)
abs_g_eff(phi_c) = Delta_q1q2(phi_c) / 2
```

The factor of two is valid only for a resolved pairwise avoided crossing satisfying every pairwise and
resonance-alignment condition in Section 4. Until those gates pass, the artifact records
`minimum_splitting_MHz` but sets `abs_g_eff_MHz=null`, `coupling_interpretation_valid=false`, and a stable
reason list. The artifact must label a valid value as magnitude and must not emit a signed coupling.

For each acceptance anchor, refine q1, c, and q2 cutoff separately by `+2`. Each refined model repeats
the forced evidence scan and returns a finite minimum splitting. Define:

```text
U_cutoff_q1 = abs(Delta_refined_q1 - Delta_baseline)
U_cutoff_c  = abs(Delta_refined_c  - Delta_baseline)
U_cutoff_q2 = abs(Delta_refined_q2 - Delta_baseline)
U_cutoff = U_cutoff_q1 + U_cutoff_c + U_cutoff_q2

S_left  = abs((Delta_min - Delta_left) / (phi_min - phi_left))
S_right = abs((Delta_right - Delta_min) / (phi_right - phi_min))
R_phi = max(phi_min - phi_left, phi_right - phi_min)
U_inner_flux = max(S_left, S_right) * R_phi

U_level = abs(Delta_final_level - Delta_previous_level)
U_solver = 1000 * max_q1_q2_splitting_error_GHz
U_total = max(U_cutoff, U_inner_flux, U_level, U_solver)
U_g = U_total / 2
```

`phi_left/min/right` and `Delta_left/min/right` are the final baseline level's immediate-neighbor
stencil. All differences use Decimal-ordered distinct keys; zero/non-finite denominators or values fail.
The final and previous refinement levels must both exist. Every refined cutoff result must independently
pass Section 4, contain exactly one q1/c/q2 row, and keep its minimum inside the baseline final evidence
domain. Missing or non-finite inputs produce null plus explicit validity/status/reason fields and fail.

`uncertainty_valid=true` exactly when:

```text
all uncertainty inputs are finite
Delta_baseline > 0
U_total > 0
U_inner_flux <= 0.005 MHz
U_level <= 0.005 MHz
U_total <= 0.01 MHz
U_total / Delta_baseline <= 0.05
Delta_baseline / U_total >= 5.0
all three refined cutoff results pass Section 4
```

Otherwise the status reaches `uncertainty_failed` after earlier-priority failures. No omitted comparison
defaults to true.

For two coupler points `i` and `j`:

```text
delta_splitting = abs(Delta_i - Delta_j)
U_delta = U_total_i + U_total_j
modulation_significance = delta_splitting / U_delta
```

Only a resolved non-reference anchor and the resolved `0.270 Phi0` reference form an acceptance pair.
`Delta_i`, `Delta_j`, `U_total_i`, `U_total_j`, `delta_splitting`, and `U_delta` must all be finite, the
coupler keys must be distinct, and `U_delta > 0`; zero or invalid denominator sets significance to null
and fails. The coupler-control claim requires `modulation_significance >= 5.0`. No monotonicity claim is
required, because the effective interaction may pass through a cancellation or a three-mode region.

## 6. Per-point and overall status

Smoke uses top-level computational status `not_run_smoke`. Formal per-coupler point status priority is:

```text
runtime_budget_exceeded
boundary
numerically_unconverged
low_subspace_continuity
pair_subspace_invalid
coupler_hybridized
resonance_misaligned
character_exchange_failed
uncertainty_failed
resolved
```

`classify_q1_q2_crossing` evaluates every predicate explicitly in this priority. Missing predicates are
false. `resolved` requires every Section 4 condition, `uncertainty_valid=true`, and runtime validity.

Raw scan objects never contain a final acceptance status or non-null `abs_g_eff`. After scan,
convergence, and runtime are complete, the pure function
`finalize_q1_q2_crossings(config, scan, convergence, runtime)` produces immutable finalized points and
applies this priority. No later interface mutates or reclassifies them. Modulation, computational gate,
artifact assembly, and notebook consume only the finalized result. Smoke returns raw evidence but the
top-level computational decision is `not_run_smoke`, never `resolved`.

The computational Stage 3.1 gate is ready only when all of the following are true:

```text
provenance_ok
solver_validation_ok
idle_metric_convergence_ok
anchor_0.200_status == resolved
reference_0.270_status == resolved
anchor_0.385_status == resolved
resolved_coupler_point_count >= 3
at least one anchor-versus-reference modulation_significance >= 5.0
runtime_within_budget
```

A diagnostic `coupler_hybridized` point does not block the gate unless it is one of the three acceptance
anchors. Any missing anchor blocks Stage 4.

The computational decision does not depend on files that have not yet been written. It contains
`computational_ready`, status, checks, and blocking reasons. After writing, a separate
`Stage31VerificationReport` combines:

```text
computational_gate.computational_ready
artifact_write.completed
artifact_bytes_canonical_and_finite
notebook_write.completed
notebook_all_code_cells_executed
notebook_error_output_count == 0
```

`Stage31VerificationReport.ok` and `.acceptance_candidate_ready` are true only if every item is true.
It always has `stage4_ready=false` and `approval_status=pending` when produced by development. The report
is returned to CLI/stdout and is not written back into the already canonical artifact. Only validation
of an independent hash-bound acceptance approval can produce a separate `Stage4ReadinessReport` with
`stage4_ready=true`; neither the artifact nor the runtime report self-approves Stage 4.

## 7. Solver validation

Any Stage 3.1 source change invalidates the Stage 3 v0.1 solver approval. A new validation candidate and
independent approval are mandatory.

Validation covers four cutoff signatures:

```text
baseline, refined_q1, refined_c, refined_q2
```

Their exact cutoffs and dimensions are:

```text
baseline:   (7,7,7), dimension 3375
refined_q1: (9,7,7), dimension 4275
refined_c:  (7,9,7), dimension 4275
refined_q2: (7,7,9), dimension 4275
```

Each signature covers these eleven full flux vectors:

```text
(0.10, 0.27, 0.00)  idle
(0.10, 0.200, 0.08) far-anchor left
(0.10, 0.200, 0.10) far-anchor crossing neighborhood
(0.10, 0.200, 0.12) far-anchor right
(0.10, 0.270, 0.08) reference left
(0.10, 0.270, 0.10) reference crossing neighborhood
(0.10, 0.270, 0.12) reference right
(0.10, 0.385, 0.08) near-anchor left
(0.10, 0.385, 0.10) near-anchor crossing neighborhood
(0.10, 0.385, 0.12) near-anchor right
(0.10, 0.396, 0.10) three-mode diagnostic
```

Flux-vector ids, in this exact order, are:

```text
idle, c020_left, c020_mid, c020_right, c027_left, c027_mid, c027_right,
c0385_left, c0385_mid, c0385_right, c0396_mid
```

Cases are ordered signature-major in the signature order above, then flux-vector order above. The case id
is `<signature>__<flux_vector_id>`; ids are unique and the exact set has 44 members.

The canonical candidate identity is:

```text
schema_version = 0.1
artifact_type = stage_03_1_solver_backend_validation
artifact_version = 0.1
profile = solver_validation
acceptance_eligible = false
```

Its exact top-level keys are identity fields plus:

```text
validation_passed, coverage_complete, failed_cases, bindings, eigsh_spec, dense_spec,
thresholds, normative_vector, cutoff_signatures, flux_vectors, validation_cases,
aggregates, p50_seconds_by_dimension, p95_seconds_by_dimension
```

`bindings` contains raw hashes for config, design-freeze manifest, Stage 2 artifact, Stage 2.1 manifest
and approval, Hamiltonian config, Stage 2 source tree, Stage 3.1 source tree, and environment fingerprint.
`cutoff_signatures` and `flux_vectors` equal the ordered contracts above. Each validation case contains
exactly:

```text
case_id, cutoff_signature, cutoffs, dimension, flux_keys, passed,
dense_gaps_GHz, eigsh_gaps_GHz, max_gap_error_GHz, repeat_gap_error_GHz,
near_degenerate_blocks, projector_dense_errors, projector_repeat_errors,
dense_q1_q2_splitting_GHz, eigsh_q1_q2_splitting_GHz,
q1_q2_splitting_error_GHz, assignments_match,
max_participation_dense_error, max_participation_repeat_error,
max_metric_dense_error_GHz, physics_checks
```

`aggregates` contains finite maxima for gap, repeat gap, dense/repeat projector,
dense/repeat participation, dense metric, and q1-q2 splitting error. Dimension timing maps have exact
keys `3375` and `4275`, finite positive values, and p95 >= p50.

The resulting 44 cases retain the existing dense-gap, near-degenerate projector, repeatability,
participation, metric, canonical-byte, environment, provenance, `failed_cases=[]`, and exact-coverage
gates. Each case also computes the q1-q2 target-branch splitting independently for dense and eigsh;
`max_q1_q2_splitting_error_GHz` is the finite maximum absolute dense/eigsh splitting difference across
all 44 cases and is the only solver aggregate used by `U_solver`. Each case must be
`<=2.0e-6 GHz`. Missing target identity, any failed case, or a missing/non-finite aggregate fails
validation. The new normative vector is for
`sha256_counter_v2` and includes all three flux keys.
The candidate binds the raw schema-0.2 config SHA-256 and all provenance hashes from Section 3. Approval
requires `reviewer_role=independent_test_review_ai`, canonical candidate/approval bytes, exact candidate
raw hash, no blocking findings, and complete revalidation. Missing or stale approval fails before any
acceptance solve.

The canonical solver approval has exact identity
`schema_version=0.1`, `artifact_type=stage_03_1_solver_backend_validation_approval`,
`artifact_version=0.1`, and exact remaining keys `decision`, `reviewer_role`, `blocking_findings`,
`validation_artifact_sha256`, `review_record_path`, `review_record_sha256`. Approval requires
`decision=approved`, the reviewer role above, and an empty list. Extra or missing fields fail.

## 8. Runtime budget

The formal budget remains 1800 seconds and smoke remains 90 seconds. No fallback solver or reduced
acceptance grid is allowed.

The execution plan must count the actual outer coupler points, inner q2 grids, forced evidence keys,
three cutoff refinements at the acceptance anchors, idle refinements, and solver-validation p95 values.
The plan fails before starting work that would exceed the remaining budget.

The conservative formal solve ceiling is:

```text
baseline outer scans = 9 * (25 + 8 * (11 - 2)) = 873
anchor cutoff convergence = 3 anchors * 3 modes * ((17 + 3 forced keys) + 4 * (11 - 2)) = 504
idle refined solves = 3
idle baseline solve = 1
total = 1381
```

The calculation assumes no overlap for the three forced left/minimum/right evidence keys in the initial
17-point convergence grid, so it is conservative. Refinement endpoints are required to be prior bracket
endpoints and therefore cache hits. `solver_evaluations > 1381` is an implementation error and fails
closed. The old 509 ceiling is not applicable.

## 9. Artifact contract

Formal output is written to:

```text
output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json
output/stage_03_1_q1_q2_coupling/verification.ipynb
output/stage_03_1_q1_q2_coupling/verification_report.json
output/stage_03_1_q1_q2_coupling/acceptance_approval.json  # independent review only
```

The artifact identity is:

```text
schema_version = 0.1
artifact_type = stage_03_1_q1_q2_coupling
artifact_version = 0.1
```

The verification-report identity is:

```text
schema_version = 0.1
artifact_type = stage_03_1_q1_q2_coupling_verification_report
artifact_version = 0.1
```

The unique writer input is `QubitCouplingSweepResult`. Required top-level sections are:

```text
provenance
solver_backend
idle_metrics
idle_convergence
scan_definition
coupler_points
q1_q2_crossings
coupling_modulation
runtime
computational_gate
checks
```

Each coupler point contains the complete ordered inner q2 flux grid, three-branch energies,
participation, bare detuning, subspace continuity, refinement levels, final bracket, minimum splitting,
`abs_g_eff`, uncertainty, and status.

The existing recursive finite preflight, canonical JSON bytes, atomic replace, no-partial-artifact rule,
and read-only executed notebook contract remain mandatory.

`verification_report.json` is canonical and written atomically last. It binds the raw artifact and
notebook hashes and contains the post-write `Stage31VerificationReport`; it does not include its own
hash. Failure to write it makes the CLI fail and leaves Stage 4 blocked.

The runner builds the first three files in a sibling staging directory. Only after all three canonical
files and hashes pass does it atomically rename the staging directory to the previously nonexistent
formal output directory. On any failure it removes staging and leaves the formal directory absent. It
never overwrites an existing formal directory.

Independent final acceptance alone may add `acceptance_approval.json`. It is canonical and contains
exactly `schema_version=0.1`,
`artifact_type=stage_03_1_q1_q2_coupling_acceptance_approval`, `artifact_version=0.1`,
`decision=approved`, `reviewer_role=independent_test_review_ai`, an empty
blocking-findings list, review-record path/hash, and raw hashes for config, Stage 3.1 source tree, solver
validation/approval, artifact, notebook, and verification report. Its validator recomputes every hash,
requires `verification_report.ok=true`, and rejects extra/missing fields or noncanonical bytes.

The exact binding keys are:

```text
review_record_path
review_record_sha256
config_sha256
design_freeze_manifest_sha256
stage3_1_source_tree_sha256
solver_validation_sha256
solver_validation_approval_sha256
q1_q2_coupling_artifact_sha256
verification_notebook_sha256
verification_report_sha256
```

The Stage 3 v0.1 output directory is never overwritten.

## 10. Public interfaces

The frozen interfaces are:

```text
rebuild_hamiltonian_for_spectrum(context, flux_overrides_phi0, basis_overrides=None)
analyze_static_point(context, config, flux_overrides_phi0, basis_overrides=None)
scan_q1_q2_crossing(context, config, coupler_flux_phi0) -> QubitCrossingScanEvidence
scan_q1_q2_coupling_vs_coupler(context, config) -> QubitCouplingScanResult
check_q1_q2_crossing_convergence(
    context, config, scan: QubitCouplingScanResult
) -> QubitCrossingConvergenceReport
finalize_q1_q2_crossings(
    config: QubitCouplingConfig,
    scan: QubitCouplingScanResult,
    crossing_convergence: QubitCrossingConvergenceReport,
    runtime: RuntimeReport,
) -> FinalizedQubitCouplingResult
evaluate_coupling_modulation(
    config: QubitCouplingConfig,
    finalized: FinalizedQubitCouplingResult,
) -> CouplingModulationReport
evaluate_stage3_1_gate(
    config: QubitCouplingConfig,
    provenance: ProvenanceReport,
    solver_backend: SolverBackendReport,
    idle_convergence: StaticMetricConvergenceReport,
    finalized: FinalizedQubitCouplingResult,
    crossing_convergence: QubitCrossingConvergenceReport,
    modulation: CouplingModulationReport,
    runtime: RuntimeReport,
) -> StageGateDecision
assemble_q1_q2_coupling_result(
    config, provenance, solver_backend, idle_metrics, idle_convergence,
    finalized, crossing_convergence, modulation, runtime, computational_gate
) -> QubitCouplingSweepResult
write_q1_q2_coupling_artifacts(result, output_dir) -> ArtifactWriteResult
write_q1_q2_coupling_notebook(artifact_path, output_dir) -> NotebookWriteResult
assemble_stage3_1_verification_report(
    computational_gate, artifact_write, notebook_write
) -> Stage31VerificationReport
write_stage3_1_verification_report(report, output_dir) -> VerificationReportWriteResult
verify_q1_q2_coupling(config_path, output_dir) -> Stage31VerificationReport
validate_stage3_1_acceptance_approval(
    config_path, artifact_path, notebook_path, verification_report_path, approval_path
) -> Stage4ReadinessReport
```

All arguments shown are required and no interface reloads, reselects, or mutates crossing data hidden
from its typed input. `QubitCouplingSweepResult` contains the computational decision and is the unique
artifact-writer input. `Stage31VerificationReport` is assembled only after artifact/notebook completion,
is written as a separate final report and printed/returned by the runner and CLI, and never causes an
artifact rewrite. The CLI returns success only after the report file is written successfully.

Minimum typed contents are:

```text
QubitCrossingScanEvidence:
  coupler_flux_key, evaluated_points, refinement_levels, final_bracket_keys,
  final_evidence_keys, resonance_root, branch/subspace evidence, minimum splitting,
  physical predicates; no final status and abs_g_eff is null

QubitCouplingScanResult:
  exact ordered config coupler keys, one QubitCrossingScanEvidence per key,
  solver evaluations, cache hits, elapsed time

QubitCrossingConvergenceReport:
  exact three anchor keys, exact q1/c/q2 refined rows per anchor,
  U_cutoff components, U_inner_flux, U_level, U_solver, U_total,
  uncertainty predicates only; no final crossing status

FinalizedQubitCouplingResult:
  exact ordered config coupler keys, immutable finalized crossing per key,
  physical/convergence/runtime predicates, final status, abs_g_eff or null

CouplingModulationReport:
  reference key, two distinct anchor/reference comparisons,
  delta splitting, U_delta, significance or null, passed

StageGateDecision:
  computational_ready, status, ordered checks, blocking reasons

Stage31VerificationReport:
  ok, acceptance_candidate_ready, stage4_ready=false, approval_status=pending,
  computational gate, artifact/notebook write reports,
  artifact raw SHA-256, notebook raw SHA-256, ordered checks, blocking reasons

Stage4ReadinessReport:
  ok, stage4_ready, approval decision/hash, all bound hashes, checks, blocking reasons
```

`flux_overrides_phi0` accepts only exact subsets of `q1`, `c`, and `q2`, validates finite real values,
and never mutates config, input artifacts, or disk state.

The existing `python -m sqvm verify-spectrum` command dispatches schema `0.2` plus
`experiment_type=q1_q2_coupling_vs_coupler` to `verify_q1_q2_coupling`. Its exit contract remains `0`
only when the formal `Stage31VerificationReport.ok=true`; valid diagnostic failure returns 1.

The current device config does not declare a `q2_z` control channel. This does not block a static
Hamiltonian flux override in Stage 3.1. It does mean Stage 4 must not claim an executable physical q2-flux
pulse path until the device/control-channel design is separately updated and rebaselined as required.

## 11. Required tests

Focused tests must include:

- strict schema-0.2 config, experiment dispatch, exact coupler grid, path, type, and rejection parsing;
- full flux-vector Decimal keys, cache identity, and `sha256_counter_v2` normative vector;
- simultaneous q1/c/q2 overrides and input immutability;
- synthetic q1-q2 avoided crossing location, splitting, and factor-of-two coupling magnitude;
- deterministic L4-L8 two-consecutive-level stop, boundary termination, and max-level exhaustion;
- bare detuning sign change, deterministic zero-root bracket, resonance alignment, and q1/q2 character exchange;
- target-subspace continuity under an internal basis rotation;
- full evidence-domain adjacency coverage and same-dimension refined continuity;
- target bare-projector/high-excitation rejection and coupler rejection of a three-mode crossing;
- three separate cutoff refinements at each acceptance anchor;
- summed cutoff uncertainty, immediate-neighbor flux stencil, absolute/relative/significance gates;
- finite/null uncertainty behavior and atomic artifact failure behavior;
- modulation significance arithmetic, zero-denominator null, and insufficient-modulation rejection;
- Stage 4 readiness requiring all three anchors and the modulation gate;
- new 44-case solver validation exact coverage and representative fail-closed attacks;
- computational-gate/post-write-report separation and no artifact rewrite;
- staged-directory cleanup/no-partial-formal-output and stale/tampered independent approval rejection;
- CLI, runner, canonical artifact/report, and executed notebook contracts.

Independent testing should run the automated suite, one representative solver-gate attack, one synthetic
two-level crossing, one coupler-hybridized rejection, and one temporary 44-case regeneration. It must not
expand into unbounded fuzz or repeated expensive regeneration unless a concrete finding requires it.
