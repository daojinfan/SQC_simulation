# Stage 5 Detailed Design: QuTiP Time Evolution

## 1. Boundary and trust chain

Stage 5 evolves `2q1c` states from the approved Stage 4 exact-five directory only. Its numeric control
contract is restricted to `effective.time_center_ns`, `effective.xy_drive_GHz.q1/q2.{i,q}`, and
`effective.absolute_flux_phi0.q1/q2/c`. Readout is rejected, not ignored. It may inspect the same artifact's
XY `carrier_frequency_GHz` and `phase_rad` only for the frozen frame/provenance contract below. No AWG,
logical-target, requested, delivered, DAC, schedule-shape, or voltage value is a numerical input.

Before any Stage 5 config load, the implementation must call the existing public function:

```text
validate_stage4_acceptance_approval(approval_path, artifact_path, notebook_path,
                                    report_path, run_receipt_path, repository_root=None)
  -> Stage5ReadinessReport
```

It requires exactly `ok=true`, `stage5_ready=true`, `approval_decision="approved"`,
`acceptance_approval_valid=true`, every ordered check passed, and `blocking_reasons=[]`. It then validates
the future canonical Stage 5 design-freeze manifest. That manifest is created only after independent design
approval and has exactly:

```text
{schema_version,artifact_type,artifact_version,decision,reviewer_role,blocking_findings,
 document_sha256,stage4_approval_sha256,stage4_artifact_sha256,review_record_path,
 review_record_sha256}
```

Types are strings except `blocking_findings` (array of strings) and `document_sha256` (mapping with exactly
the three repository-relative document paths to uppercase 64-hex strings). Identity is `0.1`,
`stage_05_qutip_evolution_design_freeze`, `0.1`; decision is `approved`, reviewer role is
`independent_design_review_ai`, and findings are `[]`. Missing, unknown, stale, noncanonical, or outside-root
paths fail before output-directory creation.

## 2. Frozen interaction-picture model and XY-only RWA

### 2.1 Reference modes and frame

For each cutoff tuple `C=(Cq1,Cc,Cq2)`, form the local single-mode charge Hamiltonians at the first accepted
effective flux sample, using the accepted diagonal `E_C,mm`, effective `EJ_m(phi_m,0)`, and local charge/
cosine operators. Let their ascending, phase-fixed eigenvectors be `|l_m>` for `l=0..2Cm`, where phase fixing
makes the largest-magnitude charge coefficient real-positive and uses lowest index for a tie. Define

```text
N_q1 = sum_l l |l_q1><l_q1| tensor I_c tensor I_q2
N_q2 = I_q1 tensor I_c tensor sum_l l |l_q2><l_q2|
U(t) = exp[-i 2*pi*t*(f_ref[q1]*N_q1 + f_ref[q2]*N_q2)]
```

`t` is ns, each `f_ref` is cycles/ns because GHz equals cycles/ns, and `U(0)=I`. The exact carrier map comes
from the approved Stage 4 artifact's two XY pulse records, not from Stage 3.1 or a new configuration value.
The phase metadata is already encoded in `epsilon=I+iQ`; numerical use of `phase_rad` a second time is an
error. This explicitly consumes protected carrier frequency metadata while retaining its provenance.

### 2.2 Exact controlled-system transform

The derivation-only lab Hamiltonian in GHz is:

```text
H_lab,k = H_charge - sum_m EJ_m(phi_m,k) cos(phi_hat_m)
          + sum_q Re[epsilon_q,k exp(-i*2*pi*f_ref[q]*t)] n_q
epsilon_q,k = I_q,k + i Q_q,k
```

Stage 5 never samples or persists this carrier waveform. For every edge/hold interval it constructs the complete
accepted charge-basis static Hamiltonian `H_static(phi_k)`, including all diagonal charging terms, all
off-diagonal capacitance/exchange terms, and every flux-dependent Josephson term. The unique interaction-picture
controlled-system Hamiltonian before drive RWA is:

```text
H_IP,full(t,k) = U(t).dag H_static(phi_k) U(t)
                 - f_ref[q1] N_q1 - f_ref[q2] N_q2
                 + U(t).dag H_drive,lab(t,k) U(t)
```

`H_static` is exactly the accepted Stage 2.1 charge-basis Hamiltonian at the approved absolute flux triplet;
there is no new flux model. The counterterms are exactly `-f_ref N`, because `U(t)` is defined in GHz/cycles per
ns. Every operator, including charging/coupling and each flux-dependent cosine, is transformed by explicit
left/right QuTiP `Qobj` multiplication. There is no `P_(0,0)`, averaging, diagonalization, or truncation of
the controlled static/flux system after this transform.

Let `P[a,b]` be the joint projector onto the integer eigenspace `N_q1=a,N_q2=b`, with absent indices zero.
For `A`, define only the drive Fourier component `D_q,+ = sum_(a,b) P[a+e_q1,b+e_q2] n_q P[a,b]`, where the
increment is `(1,0)` for q1 and `(0,1)` for q2, and define `D_q,-=D_q,+.dag()`. The sole RWA is the exact
co-rotating part of the stated Stage 4-consistent carrier reconstruction:

```text
H_drive,IP,RWA(k) = 1/2 sum_q [epsilon_q,k D_q,+ + conjugate(epsilon_q,k) D_q,-]
H_IP,k(t) = U(t).dag H_static(phi_k) U(t) - sum_q f_ref[q] N_q
            + H_drive,IP,RWA(k)
H_rad_per_ns,k(t) = 2*pi * H_IP,k(t)
```

The `phase_rad` metadata is already represented in `epsilon=I+iQ`; the lab reconstruction is
`Re[epsilon*exp(-i*2*pi*f_ref*t)] n_q`, so applying metadata phase again is forbidden. The explicit
`U.dag H_static(phi) U` term means `q2_resonance_flux` and every coupler scenario preserve full q2/coupler
flux and exchange dynamics. QuTiP `QobjEvo` uses one piecewise-constant flux/I/Q callback indexed by the
Stage 4 hold interval and one deterministic `U(t)` coefficient callback; it never uses a sampled carrier.
At each callback value, Hermiticity is checked before `mesolve` receives the operator.

All Hamiltonian algebra is GHz until `angular_rad_per_ns(value_GHz)=2*pi*value_GHz`, the sole conversion
function. It accepts Hamiltonian/operator coefficients only; no caller may use `2*pi`, `1e9`, or `1e-9`.
Flux stays in `Phi0` and time stays ns.

## 3. Time, state frame, and independent labeling

The Stage 4 centers must be finite, strict ascending, and exact 0.5 ns spacing. Stage 5 derives edges in
Decimal arithmetic: `edge[0]=center[0]-0.25`; `edge[k+1]=center[k]+0.25`. Sample `k` is a zero-order hold on
`[edge[k],edge[k+1])`; the last interval is right-closed. A single bounds-checked callback maps every flux/I/Q
request to this interval. No interpolation, extrapolation, resampling, trimming, or carrier reconstruction is
allowed. `mesolve` receives the exact `edge` tlist and returns `N+1` rotating-frame kets.

The physical initial state is never selected from an interaction-frame quasienergy spectrum. At
`t0=edge[0]`, construct the complete physical lab Hamiltonian `H_static(phi_0)` with no XY drive and solve its
lab-frame eigensystem. The phase-fixed lowest lab eigenvector is `g_lab`; initial solver state is exactly
`psi_IP(t0)=U(t0).dag @ g_lab`. `U(0)=I` is only a coordinate convention and makes no statement that `t0=0`.
Final solver state and default observables are interaction-frame values. Its physical lab representative is
always `psi_lab(t)=U(t) @ psi_IP(t)`; lab-frame projectors below are transformed as `P_IP(t)=U(t).dag P_lab U(t)`
before every population/leakage expectation.

Stage 5 does not claim a nonexistent Stage 3.1 projector API. It independently constructs physical lab-frame
projectors at `t0`:

1. Build the local product catalog at first flux from `|l_q1> tensor |l_c> tensor |l_q2>` for exactly labels
   `000,100,001,101`, in this order.
2. Use `scipy.linalg.eigh(H_lab, driver="evr", subset_by_index=[0,15])` and require exactly
   `state_count=16` finite, ascending lab eigenpairs. Any adjacent lab eigenvalue gap `<=1e-10 GHz` among those
   candidates is a reference degeneracy failure; a candidate shortage is also a failure.
3. Form float64 squared overlaps. Define each assignment cost as the exact Decimal sum of
   `-Decimal.from_float(overlap[label,column])`. Enumerate the finite 16-permute-4 assignment tuples in label
   order. Choose the lowest total Decimal cost; among only exactly equal Decimal costs choose lexicographically
   smallest complete tuple. No epsilon perturbation or solver-dependent tie behavior is allowed.
4. Require four distinct assignments and every overlap `>=0.90`. Low overlap, nonfinite value, degeneracy,
   duplicate, unsupported label, or projector nonorthogonality `>1e-12` fails.
5. `P_lab,label=|v_lab,assigned><v_lab,assigned|`; `P_lab,comp=sum(P_lab,label)`. Store label, lab eigen-index,
   overlap, Decimal cost tuple, phase rule, projector complex encoding, and SHA-256 of canonical projector bytes.

For interaction-frame ket `psi_IP(t)`, `population[label]=real(psi_IP.dag P_IP,label(t) psi_IP)`,
`leakage=1-real(psi_IP.dag P_IP,comp(t) psi_IP)`, and `norm_error=abs(psi_IP.dag psi_IP-1)`. Unclipped values
are acceptance values; display clipping within `1e-12` is separately recorded. Bounds outside
`[-1e-10,1+1e-10]`, norm error above `1e-9`, or nonfinite result is numerical failure.

## 4. Exact data and public contracts

All JSON is UTF-8 canonical JSON, recursively finite, duplicate-key rejected, repository-relative paths, and
uppercase SHA-256. A complex scalar is exactly `{"re": finite_number, "im": finite_number}`; complex arrays
are row-major arrays of that mapping. Kets are `{"representation":"ket_charge_basis_v1","dimension":positive_int,"amplitudes":complex_array}`. Density matrices are forbidden in v0.1; a future schema change is required.

The exact config root keys are:

```text
{schema_version,experiment_type,profile,inputs,scenario_ids,model,frame,solver,
 tolerances,runtime,publication}
```

`schema_version/artifact versions` are string `0.1`; `experiment_type` is `qutip_time_evolution`; profile is
`smoke` or `formal`; `inputs` has exactly `{stage5_design_freeze_manifest,stage4_approval,stage4_artifact,
stage4_notebook,stage4_report,stage4_receipt}`; `scenario_ids` is ordered nonempty ASCII IDs; model has exactly
`{charge_cutoffs,convergence_charge_cutoffs,reference_state_count}`; each cutoff is exactly a three-positive-integer
array in `q1,c,q2` order and `reference_state_count` is a positive integer; frame exactly `{carrier_source,rwa_projection}` with values
`stage4_approved_xy_metadata` and `number_sector_v1`; solver exactly `{interpreter_path,qutip_version_spec,
method,rtol,atol,nsteps,max_step_ns,store_states,store_final_state,normalize_output,progress_bar}` and every
value must equal the profile's immutable options below, not an operator-selectable value; tolerances exactly
`{label_min_overlap,lab_degeneracy_GHz,projector_orthogonality,norm_error,population_bound,
embedding_isometry,projected_state_loss,projector_embedding,convergence_population,convergence_leakage,
convergence_final_infidelity}`; runtime exactly `{scenario_seconds,total_seconds,ipc_start_seconds,
watchdog_grace_seconds}`; publication exactly `{output_dir,
allow_existing_formal_target}` where formal value is false. Unknown/missing fields fail.

Both profiles pass this exact dict to QuTiP `mesolve` with no additions: `{"method":"adams","rtol":1e-8,
"atol":1e-10,"nsteps":100000,"max_step":0.05,"store_states":true,"store_final_state":true,
"normalize_output":false,"progress_bar":null}`. `max_step` is ns; `rtol`, `atol`, and `nsteps` are
dimensionless. Config key `max_step_ns` maps exactly to QuTiP option `max_step`; all other option names map
identically. The implementation must reject a config whose values differ rather than silently coerce them.

All tolerance comparisons are inclusive and use finite IEEE-754 binary64 values: label overlap `>=0.90`
(dimensionless); lab degeneracy gap `>1e-10 GHz`; projector orthogonality `<=1e-12`; norm error `<=1e-9`;
population/leakage bounds `[-1e-10,1+1e-10]`; embedding isometry `<=1e-14`; projected state discarded norm
`<=1e-6`; embedded-projector mismatch `<=1e-6`; and all-time population/leakage/final-infidelity convergence
`<=1e-4`. Every positive threshold must be finite and strictly positive; all equality-at-boundary values pass
except the explicit strict lab-degeneracy requirement.
These constants are the only truth source and replace every implicit threshold in this document.

Formal `scenario_ids` is exactly `["xy_drag","q2_resonance_flux","coupler_0_200","coupler_0_270","coupler_0_385"]`, cutoff `(7,7,7)`, comparison `(8,8,8)`, 120.0 s/scenario, 300.0 s total, 5.0 s IPC-start limit, and 5.0 s termination grace. Smoke is exactly `["xy_drag"]`, `(5,5,5)`, no comparison, 30.0 s/scenario/total, 5.0 s IPC-start limit, and 5.0 s termination grace. Both use the same approved Stage 4 formal artifact and all physics/solver tolerances; only listed limits differ.

Public functions are frozen:

```text
validate_stage5_solver_candidate(candidate_path, approval_path, repository_root=None) -> SolverReadinessReport
load_stage5_input(config_path, repository_root=None) -> Stage5Input
run_stage5_evolution(config_path, output_dir, authorization_path, repository_root=None) -> Stage5ArtifactSet
validate_stage5_acceptance_approval(approval_path, artifact_path, notebook_path, report_path, receipt_path,
                                    repository_root=None) -> Stage6ReadinessReport
```

All artifact scalar fields use JSON string/boolean/finite-number types as named; `attempt_id` is lowercase UUID4,
`scenario_order` is an ordered ASCII string array, `provenance/config/solver_validation/environment/runtime` are
mappings, `scenarios/checks` are arrays, and `computational_gate` is a mapping. The artifact exact keys are:

```text
{schema_version,artifact_type,artifact_version,profile,status,acceptance_eligible,attempt_id,provenance,
 config,scenario_order,scenarios,solver_validation,environment,computational_gate,runtime}
```

Each scenario exact keys are `{scenario_id,frame,edge_time_ns,control_digest,initial_reference,states,
populations,leakage,norm_error,convergence,checks}`. The report exact keys are
`{schema_version,artifact_type,artifact_version,execution_succeeded,profile,status,acceptance_eligible,
publication_pending,stage6_ready,approval_status,attempt_id,artifact_path,artifact_sha256,notebook_path,
notebook_sha256,computational_checks,staging_checks,blocking_reasons}`. Receipt exact keys are
`{schema_version,artifact_type,artifact_version,execution_succeeded,profile,status,attempt_id,authorization_sha256,
paths,sha256,elapsed_seconds,scenario_elapsed_seconds,runtime_budget_seconds,total_runtime_within_budget,
publication_checks,blocking_reasons}`. Notebook is an `.ipynb` with metadata exactly
`{language_info,stage5_read_only}`, top-level keys exactly `{cells,metadata,nbformat,nbformat_minor}`, and
immutable cell IDs/sources; it reads only sibling artifact. `states` is an ordered array of exactly `N+1` kets,
`edge_time_ns/populations/leakage/norm_error` are same-length finite arrays, `control_digest/initial_reference/
convergence` are mappings, and `checks` is an ordered check array. Report and receipt path fields are
repository-relative strings, hash fields uppercase 64-hex strings, elapsed fields finite nonnegative numbers,
and all `*_succeeded`, `*_eligible`, `*_pending`, readiness, and check `passed` fields are booleans.

The only artifact statuses are `ready_for_stage6_review`, `smoke_complete`, `implementation_error`,
`total_timeout`, `scenario_timeout`, `runtime_failure`, `numerical_failure`, and `convergence_failure`;
evaluation precedence is exactly the order shown in Section 6, with `smoke_complete` only for a passing smoke
profile. The approval exact keys are `{schema_version,artifact_type,artifact_version,decision,reviewer_role,
blocking_findings,stage5_design_freeze_manifest_sha256,stage4_approval_sha256,stage4_artifact_sha256,
config_sha256,solver_validation_approval_sha256,source_tree_sha256,authorization_sha256,artifact_sha256,
notebook_sha256,report_sha256,receipt_sha256,review_record_path,review_record_sha256}`. The readiness result
exact keys are `{ok,stage6_ready,approval_decision,acceptance_approval_valid,bound_hashes,checks,
blocking_reasons}`. Its true result requires every ordered hash, exact-five set, candidate readiness, and
approval contract to pass.

## 5. Solver-validation gate

No smoke/formal config or runner may execute until a separate exact-three solver-validation directory contains
`solver_validation_candidate.json`, `verification.ipynb`, and `verification_report.json`, followed only after
independent review by `solver_validation_approval.json` (exact-four). Candidate exact keys are
`{schema_version,artifact_type,artifact_version,freeze_document_sha256,stage5_design_freeze_manifest_path,
stage5_design_freeze_manifest_sha256,stage4_paths,stage4_sha256,source_tree_definition,source_tree_sha256,
interpreter,packages,platform,thread_env,probe_spec_id,probe_spec_sha256,probe_results,tests,checks,runtime}`. `freeze_document_sha256` exactly
maps the three Stage 5 document paths; `stage4_paths` exactly maps `{approval,artifact,notebook,report,receipt}`
to repository-relative paths; `stage4_sha256` has the same exact five keys; and `source_tree_definition` is
exactly `{included_paths,exclude_paths,hash_algorithm}`. Approval exact keys are
`{schema_version,artifact_type,artifact_version,decision,reviewer_role,blocking_findings,candidate_sha256,
notebook_sha256,report_sha256,freeze_document_sha256,stage5_design_freeze_manifest_sha256,stage4_sha256,
source_tree_sha256,interpreter_sha256,probe_spec_sha256,review_record_path,review_record_sha256}`. Identities
are `stage_05_qutip_solver_validation` and `stage_05_qutip_solver_validation_approval`, version `0.1`.

`source_tree_definition.included_paths` is the sorted, exact recursive file set under `src/sqvm/evolution/`,
plus `scripts/run_stage_05_qutip_evolution.py`, `pyproject.toml`, and the two Stage 5 config paths; exclusions
are exactly `__pycache__`, `*.pyc`, `output`, `tmp`, and `.git`; algorithm is `sha256_path_content_v1`, computed
as newline-separated `relative_posix_path + NUL + raw_sha256` bytes in sorted order. The validator recomputes
all document, manifest, Stage 4 path/hash, source-tree, interpreter, and probe bindings from current bytes;
it rejects candidate/approval reuse across any freeze, Stage 4 evidence, source tree, or interpreter change.

The candidate launches only the approved interpreter path, requires its resolved path to match exactly, QuTiP
`>=5.1.0,<5.2.0`, and records exact Python/QuTiP/NumPy/SciPy versions, BLAS configuration digest, platform,
and thread environment. It has no candidate-defined `expected`, `tolerance`, or custom `probe_spec` field:
it carries exactly `probe_spec_id="stage5_solver_probe_v1"`, `probe_spec_sha256` recomputed from the frozen
constant below, and ordered actual `probe_results`. The validator independently reconstructs every input,
reference, comparison, threshold, and aggregate from that constant; any missing/extra probe, differing ID/hash,
candidate expected value, tolerance, option, or probe field fails closed.

`stage5_solver_probe_v1` has this exact ordered case list and uses the immutable QuTiP options above:

1. `zero_sparse_v1`: dimension 2, basis `|0>,|1>`, CSR zero Hamiltonian, tlist `[0.0,0.5,1.0]` ns, initial
   `|0>`, observables `P0,P1,norm`; expected arrays are `P0=[1,1,1]`, `P1=[0,0,0]`, `norm=[1,1,1]`.
2. `piecewise_z_sparse_v1`: dimension 2, CSR `sigma_z/2`, hold coefficients GHz `[0.0,0.25]` over edge grid
   `[0.0,0.5,1.0]` ns, initial `(|0>+|1>)/sqrt(2)`, observables amplitudes and norm. The expected ket array
   is `[(1,1)/sqrt(2), (1,1)/sqrt(2), (exp(-i*pi/8),exp(i*pi/8))/sqrt(2)]`; the discontinuity is exactly
   at `0.5 ns`, and the Hamiltonian is `2*pi*coefficient*sigma_z/2` rad/ns.
3. `rabi_x_sparse_v1`: dimension 2, CSR `sigma_x`, constant `0.125 GHz`, tlist `[0.0,1.0,2.0]` ns, initial
   `|0>`, observable `P1`; expected `P1=[0.0,0.5,1.0]` from `sin^2(2*pi*0.125*t)`.
4. `interaction_survival_v1`: reconstructs the accepted Stage 2.1 charge-basis model at the fixed Stage 4
   initial flux and its fixed `U(t=0.125 ns)`. Select the first lexicographically indexed nonzero
   capacitance-coupling matrix element and first lexicographically indexed nonzero q2-flux-difference
   off-diagonal element. Expected comparisons are `abs((U.dag A U)[i,j])-abs(A[i,j])=0` and nonzero finite
   transformed q2-flux difference; selection failure is failure.

For cases 1--3, candidate actual arrays use the canonical complex encoding. Compare real/imaginary components
elementwise by max absolute error; zero/piecewise norm error is `max(abs(norm-1))`; Rabi error is max absolute
population error. Each must be `<=1e-9`. For case 4, coupling magnitude error and q2-flux survival tolerance
are `<=1e-12`, and the selected flux element magnitude must be `>1e-12`. Run the entire ordered four-case suite
three times; repeat error is the maximum componentwise difference across the three actual-result records and
must be `<=1e-12`. Aggregate passes iff all 12 case repetitions, all API/sparse/QobjEvo/mesolve construction
checks, and immutable notebook replay pass. Candidate result booleans are informational only; validator
recomputes them. Missing dependency, wrong interpreter/version/options, unexpected API, nonfinite value,
edge disagreement, coupling/flux loss, nondeterminism, or stale binding fails closed.

## 6. Convergence and run-attempt lifecycle

For each cutoff independently construct local bases, `N`, `U`, exact transformed static/flux/coupling terms,
XY-only RWA, lab labels, initial ket, and physical projectors. To
compare `(7,7,7)` with `(8,8,8)`, use canonical charge embedding
`J=J_q1 tensor J_c tensor J_q2`, where each `J_m` embeds equal integer charge values `-7..7` in the `-8..8`
ordered basis. Require `J.dag J=I` within `1e-14`. Compare `psi8_projected=J.dag psi8`, require projected norm
`>=1-1e-6`, renormalize only after recording discarded weight, then phase-align by making inner product with
`psi7` real-positive; zero overlap is failure. At each common edge transform the `(8,8,8)` physical projector
by `J.dag P8_lab J`, record its support loss, and compare it to the `(7,7,7)` lab projector before forming
populations. Any projector mismatch `>1e-6` or missing support fails convergence. Compare all common-edge
populations/leakage and final fidelity `abs(<psi7|psi8_projected>)^2`; thresholds are all `<=1e-4`. Direct
cross-dimension comparisons are forbidden.

Formal authorization is a separately approved canonical JSON file, not a user string. Its exact keys are
`{schema_version,artifact_type,artifact_version,attempt_id,profile,config_sha256,stage5_design_freeze_manifest_sha256,
solver_validation_approval_sha256,authorized_by,authorized_at_utc,decision}`; identity is
`stage_05_qutip_formal_run_authorization`, version `0.1`, decision `approved`. `attempt_id` is lowercase UUID4
and may appear in no prior receipt/target. The runner rejects an existing formal target, authorization reuse,
profile mismatch, or hash mismatch before staging.

The sole immutable attempt-audit root is repository-relative
`output/stage_05_qutip_evolution_attempts/`. For authorization digest `A` (uppercase SHA-256), the receipt
filename is exactly `attempt-` plus lowercase `A` plus `.json`; the reservation filename is the same stem plus
`.lock`. Before child creation, the parent creates the lock by exclusive create-new. A pre-existing lock or
receipt, prior receipt found by digest filename, an existing formal target, or any authorization digest already
referenced by a receipt is rejection; resume is forbidden. The lock is never deleted, including crash/timeout,
so it is permanent evidence that the authorization was consumed. Concurrent requests race only on exclusive
lock creation; the loser fails before any numerical work.

After termination the parent creates the receipt once by exclusive create-new, never overwrites it, and retains
the lock. Its exact keys are `{schema_version,artifact_type,artifact_version,attempt_id,profile,
authorization_sha256,status,elapsed_seconds,diagnostic_code,active_scenario_id,timeout_kind,
total_deadline_monotonic_ns,scenario_deadline_monotonic_ns,ipc_transcript_sha256,formal_target_path,
formal_target_sha256}`; target path/hash are null only for nonpublished attempts. Identity is
`stage_05_qutip_attempt_receipt`, version `0.1`. Terminal status is one of `published`,
`implementation_error`, `total_timeout`, `scenario_timeout`, `runtime_failure`, `numerical_failure`, or
`convergence_failure`; `timeout_kind` is null or exactly `total`/`scenario`. It contains no state, controls,
or partial artifact bytes. A crash before receipt is represented by the retained lock and is likewise
non-resumable. For `published`,
`formal_target_path` is the repository-relative formal directory and `formal_target_sha256` is exactly the
mapping `{artifact,notebook,report,receipt}` to uppercase 64-hex values; all other terminal states use null
for both fields.

Formal parent/child IPC is a newline-delimited canonical JSON pipe. Child must send immediately before each
scenario solver construction `{"event":"scenario_start","attempt_id":string,"scenario_id":string,
"monotonic_ns":positive_int}` and immediately after it sends the same exact keys with
`event="scenario_complete"`. Parent records its own `time.monotonic_ns()` receive timestamp and validates
attempt ID, exact schema, strictly ordered scenario IDs, one start/complete pair per ID, and child timestamp
monotonicity. No start message within 5.0 s of child launch, malformed/missing/duplicate/reordered message,
complete without active start, or child exit without complete is `implementation_error` and cannot publish.

Parent sets `total_deadline=parent_launch_monotonic+300.0 s` and, only on a valid start receive, sets
`scenario_deadline=parent_receive_monotonic+120.0 s`; it always waits to the earlier deadline. At deadline it
classifies `total_timeout` when total deadline is earlier or equal, otherwise `scenario_timeout`, terminates the
entire child process tree using the parent-owned OS job/process group, and allows 5.0 s only to observe child
exit and collect IPC. Grace never changes either acceptance deadline. Receipt records the active scenario,
parent elapsed time, both deadline timestamps, timeout kind, and transcript hash. Status precedence is
`implementation_error > total_timeout > scenario_timeout > runtime_failure > numerical_failure >
convergence_failure > ready_for_stage6_review`; a timeout always prevents formal exact-four publication.
Staging is sibling `.stage_05_qutip_evolution.staging.<attempt_id>` and is atomically renamed only after all
checks, canonical bytes, and notebook replay pass. The parent then writes the prepared immutable success receipt
with exclusive create-new. If that write fails, it removes only the just-created target after verifying its
attempt ID/hash, records the retained lock as an interrupted failure, and never leaves an acceptance candidate.
Failed staging is deleted; the immutable outside receipt retains attempt identity/status. Development formal
output is exact-four; independent approval alone may create the fifth file.

## 7. Negative-test matrix

Required independent tests reject: stale/missing Stage 4 exact-five or Stage 5 freeze; AWG/logical/readout
numeric access; carrier missing/duplicate/mismatch and phase double application; projection/deletion of any
static, capacitance-coupling, or flux term; lost transformed coupling matrix element; lost q2-flux off-diagonal
term; any alternate XY RWA term, counterterm, units, or edge interpolation; a physical lab ground differing
from but incorrectly replaced by an interaction-frame quasienergy ground; label ambiguity/low overlap/exact
tie/duplicate/candidate shortage; malformed complex/ket, unknown exact key, and density representation; wrong
approved interpreter/version/API and every solver probe; solver candidate/approval reuse across freeze, Stage 4
artifact, or source tree; wide rtol/atol, altered nsteps/max-step/store/normalize/progress option, candidate
expected/tolerance, custom/missing/extra probe, or falsified aggregate; cross-cutoff direct comparison, invalid
embedding, loss of projected norm, or phase-zero overlap; reused authorization/existing target/prior receipt/
concurrent receipt; a single scenario running 121 s while total remains below 300 s; total deadline expiry;
missing/duplicate/reordered IPC start; child no-response; timeout/staging leak; report/receipt/hash/notebook
tampering; smoke approval; and every failed Stage 6 readiness check.
