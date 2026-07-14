# Stage 7 Detailed Design: Calibration Experiments

## 1. Authority and non-goals

This design is frozen as Stage 7.0 implementation authority by the companion Stage 7 QCIS design-freeze
record. It depends on the Stage 7 plan and entry/phasing decision. Any byte change invalidates that freeze.

Stage 7 adds deterministic program compilation, model-derived calibration experiments, immutable analysis, and
human-reviewed simulator calibration state. It does not add measurement, readout, noise, dissipation, arbitrary
plugins, adaptive closed-loop calibration, distributed execution, or automatic calibration acceptance.

All Stage 7 and later calibration programs use QCIS as their sole external instruction format. The parser,
opcode profile, timing, waveform, Stage 4.1, and QuTiP boundary is defined in
`docs/designs/07_qcis_compiler_design.md`. JSON gate/pulse objects are compiler-internal only.

## 2. Version boundaries

Stage 6 request/evidence schema `0.1` remains unchanged and continues to require `program=null`. Stage 7 uses
request schema `0.2` and run evidence schema `0.2`. Readers dispatch by exact version; they never silently
upgrade a `0.1` request or reinterpret a `0.1` run.

The Stage 7 request root has exact keys `{schema_version,experiment_id,backend_id,device_snapshot,
calibration_ref,bootstrap_policy_ref,parameters,program,scan,execution,publication}`. It replaces the Stage 6
calibration path string with an exact authority reference and makes `program` an exact object:

```text
program_schema_version "0.1"
instruction_set_id      "qcis_stage7_calibration_v1"
template_id             registered immutable experiment-template ID
template_sha256         uppercase SHA-256 of exact source bytes
source_format           "qcis_template"
source                  nonempty canonical LF text
bindings                exact typed placeholder mapping
```

`calibration_ref` is an exact discriminated union:

```text
accepted simulation = {
  kind="accepted_simulation", calibration_id, calibration_sha256,
  authority_receipt_path, authority_receipt_sha256, snapshot_path
}

bootstrap = {
  kind="bootstrap_uninitialized", state_id="uncalibrated", calibration_sha256,
  snapshot_path
}
```

Paths are repository-relative POSIX paths with the Stage 6 containment, case, symlink, and junction rules. An
accepted authority receipt must be a verified accepted decision transaction; `snapshot_path` must be byte-
identical to its calibration payload. A tracked config export is only a mirror and never authority. Bootstrap
must name the exact Stage 6 `accepted=false` snapshot and use the root frozen bootstrap-policy reference;
Calibrated QCIS gate macros are forbidden in that variant. Unknown/mixed keys or kind/state/status mismatch reject before
reservation.

`bootstrap_policy_ref` is either null or exact `{policy_path,policy_sha256}`. It is required for the initial
uncalibrated parent and may remain present for an `accepted_simulation` partial calibration only to supply keys
that are still absent. Accepted calibration values always take precedence and can never be overridden by policy
seeds. Supplementing is limited to the policy's experiment/operand whitelist. Calibrated QCIS macros remain forbidden
until the calibration itself contains its complete prerequisite key set, regardless of policy availability.
The root key is always present: bootstrap-uninitialized requires the object; an accepted partial snapshot
requires the same object and SHA recorded in `calibration.json`; a complete accepted snapshot may use null or the
same inherited object but never a different policy. Canonical request hashing binds the value, compiled-point
evidence binds every consumed seed, and the `0.2` verifier reloads the policy and recomputes these conditions.

The compatibility matrix is exact:

```text
request 0.1 -> program null -> run/dataset/verifier 0.1
request 0.2 -> program schema 0.1 + qcis_stage7_calibration_v1 -> run/dataset/verifier 0.2
```

No other combination is admitted.

Unknown keys, versions, levels, opcodes, import paths, expressions, templates outside the immutable registry,
and executable references fail before reservation or output creation.

## 3. Typed values and scan binding

Every QCIS template placeholder is declared in `program.bindings` by exactly one of:

```json
{"literal": 0.025, "unit": "GHz"}
{"scan_ref": "drive_amplitude", "unit": "GHz"}
```

Objects have exact keys. `scan_ref` names one declared request axis; its unit must exactly match the QCIS
parameter position registered for that opcode and waveform setting. Resolution writes the canonical binary64
coordinate into a point-local concrete QCIS program before parsing and expansion. The only template syntax is a
whole-token `$binding_id` in a numeric parameter position. No string interpolation, arithmetic expression,
dependent axis, or implicit conversion is accepted. Operation, target, wave index, arity, and setting IDs are
never scannable.

The initial unit registry is exact:

```text
time             ns or integer AWG samples as declared by the QCIS operand
frequency/drive  GHz
phase            rad
flux             Phi0
dimensionless    dimensionless
```

Stage 4's `2*pi*GHz = rad/ns` boundary remains the sole angular conversion authority.

## 4. QCIS gate and macro instructions

QCIS uses `<Operation> <Target> <Parameters>`. The executable subset, reserved vocabulary, QAgent registry,
strict parser, and lowering rules are defined by the QCIS compiler design. `X2P`, `Y2P`, and `CZ` are QCIS
instructions, not a separate user-facing JSON language. Each parsed source line receives a deterministic
internal instruction index; users do not supply an `instruction_id`.

Initial calibrated macro opcodes include `X2P`, `Y2P`, and `CZ`. Bootstrap experiments use explicit QCIS
`PLSXY` and `PLS`, not calibrated macros.

### X2P and Y2P

The source forms are `X2P Q` and `Y2P Q`, where Q resolves through the bound QAgent registry. Expansion requires
an `accepted_simulation` calibration containing carrier frequency, amplitude, duration, waveform setting, and
DRAG coefficient. Gate phase and accumulated `RZ` follow the QCIS sign convention, and the final complex
waveform must match the reviewed QCIS formula sample by sample. User overrides are forbidden in macro form.

### CZ

The source form is `CZ C`, where C resolves to the coupler whose registry configuration names q1 and q2 as its
ordered operands. The MVP permits only the q1-to-q2 logical direction. Expansion requires accepted single-qubit
frame values plus an accepted `active_cz_setting`. It expands only through the reviewed coupler waveform
registry. No backend may reinterpret `CZ`.

Macro expansion is single-pass over acyclic immutable gate and waveform registries. QCIS contains no user macro
definitions. Every expansion records source span, configuration and calibration keys/hashes, waveform-setting
ID/hash, and emitted logical-waveform hashes.

### Bootstrap authority

Bootstrap is an independently hash-frozen `BootstrapPolicy` with exact keys `{schema_version,artifact_type,
artifact_version,policy_id,device_sha256,allowed_parent_calibration_sha256,allowed_experiment_ids,
allowed_prior_paths,pulse_seed_sets,operand_bounds,source_sha256}`. The only allowed device-prior paths are
`device.priors.q1.estimated_f01_GHz` and `device.priors.q2.estimated_f01_GHz`. Amplitude, duration, Gaussian
width, waveform formula ID, and dragAlpha seeds are policy constants, not invented device priors. Every seed set records unit, lower/upper bounds,
and the exact experiments/operands that may consume it.

The initial bootstrap exception accepts exactly the named Stage 6 uncalibrated parent. The same policy may then
supplement only missing keys along its single `accepted_simulation` partial-calibration lineage as defined by
`bootstrap_policy_ref`; every intermediate calibration binds the policy SHA. It requires explicit QCIS
`PLSXY`/`PLS`, forbids calibrated macro opcodes, and marks all resolved seed values `bootstrap_seed`.
Missing/out-of-range priors,
device/policy hash mismatch, an unlisted operand, or any attempt to recommend outside the policy bounds fails.
After human acceptance, each partial snapshot may become `accepted_simulation`; macro admission still checks the
complete required key set. The order is spectroscopy frequency, Rabi amplitude at one policy-bound duration
and width, Ramsey frequency
refinement, then DRAG dragAlpha. `X2P`/`Y2P` remain unavailable until carrier, amplitude, duration, Gaussian
width, waveform formula ID, and dragAlpha are all accepted for the target qubit.

## 5. QCIS pulse, timing, and frame instructions

The earlier internal concepts map to QCIS as follows:

```text
PLXY          -> PLSXY
PULSE         -> PLS
WAIT          -> I
FRAME_CHANGE  -> RZ
BARRIER       -> B
```

`PLSXY` retains the QCIS positional form and selects a reviewed waveform by `waveIndex`. `tStart` and `length`
are integer AWG samples; the Stage 7 simulator profile fixes amplitude, frequency, phase, DRAG, and shape units.
It emits q1/q2 complex logical XY samples. Only reviewed waveform indices are executable.

For a reviewed QCIS Gaussian/DRAG entry, the compatibility formula is envelope minus `i*dragAlpha` times its
sample-coordinate derivative, multiplied by `exp(-i*(gate_phase+input_phase+RZ_phase))`. QCIS generates the
complete complex samples before Stage 4.1; Stage 4.1 cannot regenerate them from a similarly named shape.
Endpoint removal, normalization, derivative variable, and sample rules belong to the selected formula ID.

`PLS` emits one q1/q2/c Z/flux lane under the selected setting. The initial simulator registry interprets its
admitted amplitude as absolute named-device flux `Phi/Phi0`, never a delta from idle. Stage 4.1 alone converts
it to idle-relative electronics input. `DTN` and numeric `waveIndex=-1` remain reserved until their mappers and
sample units are frozen.
Readout channels are forbidden before Stage 8.

`I Q length` appends zero samples to the target QAgent lanes. `RZ Q phase` accumulates a zero-duration virtual
frame change for subsequent XY waveforms. `B Q1 Q2 ...` aligns all listed QAgent lanes. Targets are explicit,
nonempty, and duplicate-free; no implicit `all` exists.

Each QAgent owns XY and Z cursors. `tStart=-1` appends at their maximum; nonnegative `tStart` is an absolute
sample index and can express parallel placement without allowing same-lane overlap. Pulses occupy half-open
intervals. `I`, `RZ`, and `B` replay exactly as specified in the QCIS compiler design. The compiler applies
accumulated phase exactly once when producing logical samples.

Named mapping is mandatory throughout: `q1`, `q2`, and `c` map explicitly to the Stage 2 tensor order
`q1/c/q2`; positional tuple inference is forbidden.

## 6. Compilation pipeline

For each Stage 7 request-`0.2` point, reusing the unchanged Stage 6 point schema `0.1`, compilation is a pure
pipeline:

```text
canonical request + point coordinates + QCIS template
  -> resolve typed bindings and materialize concrete QCIS
  -> strict parse to typed QCIS AST
  -> validate bootstrap or accepted-calibration policy
  -> resolve QAgents, gate settings, and waveform settings
  -> execute sample-index cursor/frame semantics and generate logical waveforms
  -> compile Stage 4.1 electronics and effective controls
  -> build immutable BackendCommand with hashes only
```

The compiled point record binds request, point, parent calibration, concrete QCIS bytes, AST, instruction
profile, QAgent/gate/waveform registries, compiler, expansion trace, logical waveform inventory, Stage 4.1
approval/config/channel registry, and effective-control hashes. The backend receives a
`VerifiedControlHandle`, not arbitrary paths, QCIS text, or high-level gates.

Repeated compilation under the same source/environment must be byte-identical. Map order, locale, timezone,
working directory, temporary path, process ID, and clocks cannot enter deterministic compiled payloads.

## 7. Stage 7 runtime adapter

Runtime version dispatch is an immutable registry keyed by exact request schema. It exposes:

```text
RuntimeSchemaAdapter.load_request(path) -> ExperimentRequestV01 | Stage7ExperimentRequestV02
RuntimeSchemaAdapter.canonical_request(request) -> mapping
RuntimeSchemaAdapter.expand_points(request) -> tuple[SharedScanPointV01, ...]
RuntimeSchemaAdapter.validate_point_result(definition, result) -> Stage7PointResultV02
RuntimeSchemaAdapter.write_dataset(results, definition, target) -> DatasetSummary
RuntimeSchemaAdapter.verify_run(run_dir, authorities) -> RunVerificationReport
```

The existing adapter for `0.1` delegates to the unchanged Stage 6 loader, one-variable dataset writer, and
verifier. The `0.2` adapter requires non-null program schema `0.1`, run/dataset schema `0.2`, and the expanded
program/control snapshots. Cross-version delegation is forbidden.

`SharedScanPointV01` is the unchanged Stage 6 point schema and point-ID/seed algorithm, deliberately shared by
request versions `0.1` and `0.2`. Its payload still records `schema_version="0.1"`; the enclosing request/run
version selects semantics. Any future point-schema change requires a new type and compatibility-matrix row.

`Stage7PointResultV02` has exact fields `{schema_version,point_id,values,diagnostics}`. `values` has the exact
ordered variable names declared by the experiment result schema; each value is one finite scalar with registered
dtype `<f8`, `<i8`, `<u8`, or `<c16`. Vector observables are declared as separate named scalar variables. The
dataset writer aggregates each variable in point-table order into one contiguous point-dimension array. MVP is
fail-fast: any point failure publishes a failed run with no dataset, so partial/missing result encoding is
forbidden. The verifier recomputes point order, variable order, dtype, units, byte lengths, and every raw hash.

The `0.2` completed run adds `program/template.qcis`, bindings, instruction profile, QAgent/gate/waveform
registries, compiler snapshot, point-local concrete QCIS/AST/expansion records, and logical/effective control
inventories to the Stage 6 evidence topology. Each point row binds point ID, concrete QCIS SHA, AST SHA,
expansion-trace SHA, logical waveform SHA set, and verified-control SHA. Exact filenames are frozen in the QCIS
compiler design. Manifest/report/receipt remain acyclic and version-specific.

## 8. Stage 4.1 parameterized-control contract

Stage 4.1 introduces a distinct `QCISLogicalWaveformPlan`, not a relaxed use of the frozen Stage 4 model. It
binds the concrete QCIS/AST/expansion hashes, sample count/grid, logical q1/q2 complex XY arrays, logical
q1/q2/c absolute-flux arrays, carrier metadata, cursor/frame replay trace, and per-array metadata/hashes.

The QCIS compiler permits multiple source pulses on one logical lane only when half-open sample intervals do not
overlap. Concurrent pulses remain subject to physical port, AWG lane, and shared-resource conflicts. Stage 4.1
independently verifies the source trace and array inventory, then applies the accepted electronics chain. It
does not parse QCIS or regenerate a waveform from high-level operands.

The public API is:

Stage 4.1 proposes:

```text
compile_qcis_waveform_plan(
    plan: QCISLogicalWaveformPlan,
    context: ParameterizedControlContext,
) -> ParameterizedControlArtifact
```

Stage 4.1 isolates its schedule validator and wrapper from the accepted strict Stage 4 entry. A reviewed common
electronics kernel may be extracted only if the original `compile_control_schedule` remains an exact strict
wrapper and all accepted Stage 4 logical targets, AWG codes, effective waveforms, metrics, reports, and hashes
remain byte-identical. Stage 7 cannot import `_compile_scenario` or bypass either public validator.

`VerifiedControlHandle` has exact fields `{schema_version,control_id,artifact_root,manifest_sha256,
receipt_sha256,inventory_sha256,effective_control_sha256,verification_report}`. Construction independently
verifies the repository/output-root-contained no-follow inventory and all hashes after final publication, then
opens effective arrays read-only. The Stage 5.1 backend accepts only this typed verified handle and rechecks its
receipt and inventory immediately before each execution.

## 9. Stage 5.1 model-evolution contracts

Stage 5.1 has two separate reviewed APIs:

```text
evaluate_static_spectrum(
    flux_by_name: {q1: Phi0, q2: Phi0, c: Phi0},
    spectrum_context: QualifiedSpectrumContext,
) -> StaticSpectrumPointResult

evolve_verified_control(
    control: VerifiedControlHandle,
    initial_state_ids: ordered tuple,
    observable_set_id: string,
    evolution_context: QualifiedEvolutionContext,
) -> EvolutionPointResult
```

`evaluate_static_spectrum` is diagnostic and feasibility support only. It cannot independently produce a
recommendation-bearing Stage 7 calibration run. Every accepted calibration recommendation must derive from
`evolve_verified_control` over a verified QCIS-compiled control artifact.

The static interface returns diagnostic energy gaps and named-mode character with q1/c/q2 tensor mapping,
eigenvector-overlap diagnostics, cutoff comparison, and solver/source hashes. The evolution interface permits
only registered initial states: `lab_ground` for single-qubit workflows; ordered `computational_10` and
`computational_01` for coupler response; and ordered
`computational_00,computational_01,computational_10,computational_11` for CZ. State labels/projectors, interaction-
to-lab frame conversion, `2*pi*GHz=rad/ns`, sample-center ZOH, norm, population, leakage, phase, cutoff, and raw
solver diagnostics remain Stage 5 authorities.

Registered observable sets are exact per experiment. The backend cannot select arbitrary operators or return
undeclared values. Initial-state overlap below the frozen threshold, missing mode labels, phase amplitude below
threshold, non-Hermitian/non-finite input, norm/population failure, or cutoff failure rejects the point.

Capabilities are split rather than implied by one broad flag:

Stage 5.1 proposes a registered backend capability set containing:

```text
model_state_evolution_v1
model_static_spectrum_v1
stage4_parameterized_control_v1
model_observables_v1
cooperative_deadline_v1
```

Registration is impossible unless Stage 5 formal qualification is approved,
`Stage6ReadinessReport.stage6_ready=true`, the Stage 6 physics-backend gate is approved, Stage 5.1 is approved,
and the Stage 7 model-evolution entrance record binds all four authorities.

## 10. Experiment definitions and analysis

Each experiment definition declares exact parameters, scan, QCIS template/binding schema, backend capability,
raw dataset, analysis, recommendation, and prerequisite-calibration schemas.

| Definition | Scan | Model-derived raw outputs | Deterministic analysis |
| --- | --- | --- | --- |
| qubit_spectroscopy_v1 | drive frequency GHz | target population, leakage, energy gap | bounded peak plus refined-grid consistency; no Lorentzian linewidth claim |
| rabi_x2p_v1 | amplitude GHz at fixed policy duration/sigma | target population, leakage, coherent phase | bounded coherent sinusoid fit and residual/conditioning checks |
| ramsey_frequency_v1 | delay ns or detuning GHz | coherence real/imag, target population, leakage | cosine fit without decay; no T2 claim |
| drag_alpha_v1 | QCIS dragAlpha samples | leakage, coherent phase error | bounded minimum of frozen weighted objective |
| coupler_flux_response_v1 | QCIS PLS c flux Phi0 x hold ns | evolved exchange populations, leakage, phase; gap/character diagnostic | dynamic ranking with bound branch diagnostics |
| cz_coarse_v1 | c flux Phi0 x hold ns | truth-table populations, leakage, conditional phase | deterministic constrained grid ranking; no hidden optimizer |

The optional coupler static diagnostic cannot publish a recommendation. Its branch tracking starts from
independently labeled q1/c/q2 states at the frozen anchor flux. It performs
two independent traversals: anchor to strictly increasing flux and anchor to strictly decreasing flux, each
sorted numerically outward from the anchor and recorded in the experiment definition and analysis evidence.
Point-table input order cannot alter traversal. At each next flux, it assigns branches by the maximum total
absolute-squared overlap with the previous accepted eigenvectors;
ties use lexicographic `(previous_label,current_energy_index)` order. Any assigned overlap below the frozen
threshold, competing assignment within the frozen ambiguity margin, or branch discontinuity above its bound
rejects the point and prevents interpolation. Gap sorting alone is forbidden. The recommendation-bearing
coupler result must come from QuTiP evolution over the QCIS `PLS C` waveform and may use the verified branch
diagnostic only as an additional bound input.

CZ uses computational basis order `00,01,10,11` with q1 as the first bit and q2 as the second. For each input
`b`, Stage 5.1 returns the projected diagonal amplitude `a_b=<b|U|b>` and all computational populations after
canonical frame conversion. If any `|a_b|` is below the frozen phase threshold, phase is unavailable. Global and
single-qubit local-Z phases are removed by fixing the phases of `a_00`, `a_01`, and `a_10` to zero; the remaining
invariant is `phi_CZ=Arg(a_11*a_00/(a_10*a_01))` on `(-pi,pi]`. Phase error is the circular principal value of
`phi_CZ-pi`. Worst-case leakage is the maximum over the four inputs of one minus total computational-subspace
population. Coarse ranking is lexicographic over validity, absolute circular phase error, worst leakage, flux,
then hold time. Cross-grid phase unwrapping and hidden continuous optimization are forbidden.

Exact scan ranges, point counts, objective weights, residual limits, convergence tolerances, refinement rules, and
recommendation validity ranges are freeze-time physics constants. They require Stage 5 formal qualification and
a reviewed feasibility pilot; this draft intentionally does not invent placeholder values.

The Rabi recommendation proposes the fitted amplitude together with the exact fixed bootstrap duration and sigma
used by every point, so all three values become explicit accepted calibration keys. Ramsey may refine the already
accepted frequency while still using explicit policy-supplemented PLSXY. DRAG then accepts QCIS dragAlpha and
the formula ID. Only the complete frequency/amplitude/duration/Gaussian-width/waveform-formula/dragAlpha set
enables `X2P` and `Y2P`.

Analysis is a separate append-only derivation from a verified immutable run. It cannot modify the run, execute a
backend, omit failed points, or convert model values into measurement claims. Plot rendering is non-authoritative
but hash-bound, with fixed dimensions, DPI, font, colors, locale, and stripped variable metadata.

## 11. Dataset and claim envelope

Stage 7 dataset schema `0.2` supports named little-endian arrays with exact dimensions, dtype, unit, semantic
role, byte length, and raw SHA-256. The point dimension is mandatory. Complex values use `<c16`; integer indices
use `<i8` or `<u8`; real values use `<f8`. Missing, duplicate, reordered, non-finite, or unbound points fail.

Every model-derived Stage 7 simulation run uses:

```json
{
  "evidence_class": "model_calibration_simulation",
  "physics_claim": "model_derived_only",
  "observation_model": "absent",
  "measurement_payload": null
}
```

Fake fixtures retain the Stage 6 platform-test claim class and cannot feed recommendations.

## 12. Append-only calibration derivation

The acyclic graph is:

```text
instruction/compiler/Stage4.1/Stage5.1 + parent calibration
  -> compiled program and immutable run
  -> analysis report
  -> calibration recommendation
  -> atomic human decision transaction
       -> accepted decision + new simulation calibration, or
       -> rejected decision only
```

All derived JSON uses schema `0.1`, artifact version `0.1`, canonical UTF-8/LF, exact keys, and uppercase SHA-256.
Exact payload keys are:

```text
analysis.json = {
  schema_version, artifact_type, artifact_version, analysis_id, analysis_definition_id,
  claim_class, run_receipt_sha256, dataset_sha256, algorithm, fit, diagnostics,
  recommendation_eligible, source_sha256, environment_sha256, plot_sha256
}

recommendation.json = {
  schema_version, artifact_type, artifact_version, recommendation_id, parent_calibration_ref,
  analysis_receipt_sha256s, proposed_delta, applicability, validity_predicates,
  prerequisite_keys, expiry_policy, source_sha256, environment_sha256
}

decision.json = {
  schema_version, artifact_type, artifact_version, decision_id, recommendation_sha256,
  parent_calibration_sha256, decision, actor_id, reason, confirmation_phrase, decided_utc,
  source_sha256, environment_sha256
}

calibration.json = {
  schema_version, artifact_type, artifact_version, calibration_id, status, hardware_claim,
  device_sha256, parent_calibration_sha256, recommendation_sha256, decision_sha256,
  analysis_receipt_sha256s, instruction_set_sha256, qagent_registry_sha256,
  gate_configuration_sha256, waveform_registry_sha256, compiler_sha256, stage4_1_approval_sha256,
  stage5_1_approval_sha256, bootstrap_policy_sha256, values, source_sha256, environment_sha256
}
```

Artifact types are respectively `stage_07_analysis`, `stage_07_calibration_recommendation`,
`stage_07_calibration_decision`, and `stage_07_simulation_calibration`.
Exact terminal statuses are `completed` for analysis, `proposed` for recommendation, and `accepted` or
`rejected` for a decision transaction. An included calibration payload is always `accepted_simulation`.
`parent_calibration_ref` reuses the exact request discriminated union and may name the frozen uncalibrated parent
only when the bound analysis and bootstrap policy authorize that first derivation.

Every published derived directory also has exact `manifest.json`, `verification_report.json`, and `receipt.json`.
Manifest keys are `{schema_version,artifact_type,artifact_version,object_id,status,payload_files}` and inventory
only payload files, never manifest/report/receipt. Report keys are `{schema_version,artifact_type,
artifact_version,object_id,status,ok,manifest_sha256,checks,blocking_reasons}`. Receipt keys are
`{schema_version,artifact_type,artifact_version,object_id,status,manifest_sha256,
verification_report_sha256,payload_hashes,derivation_parent_hashes}`. Report binds manifest; receipt binds
manifest/report plus exact payload and parent hashes. No payload points to its manifest, report, or receipt.

Every derived directory includes canonical `source_snapshot.json` and `environment_snapshot.json` payloads.
Their schemas reuse Stage 6 file-entry/environment rules under artifact version `0.1`; `source_sha256` and
`environment_sha256` are their raw hashes. The source snapshot covers the complete owning Stage 7 package,
runtime adapter, reused Stage 4.1/5.1 public modules, exact environment lock, experiment/analysis definitions,
QCIS source/profile/QAgent/gate/waveform authorities, and hash-frozen design authorities. Verification rebuilds
both snapshots from current authorities. A bare SHA
without its snapshot file is invalid.

All `analysis_id`, `recommendation_id`, `decision_id`, `calibration_id`, and transaction IDs are canonical
lowercase UUID4 values. Every SHA list is duplicate-free and sorted by uppercase SHA-256 bytewise ASCII order;
semantic order, when required, is stored separately as an explicit ID list.

### Analysis report

Binds verified run receipt, dataset, definition, algorithm/source/environment, fit parameters, residuals,
conditioning, convergence/refinement diagnostics, plot hash, and `claim_class=simulation_only`.
Its exact payload files are `analysis.json`, `plot.png`, `source_snapshot.json`, and
`environment_snapshot.json`; plot is fixed-render and non-authoritative.

### Recommendation

Binds one parent calibration, one or more verified analyses, a canonical proposed delta, exact applicability and
validity predicates, prerequisite key set, and expiry policy. The MVP expiry policy is exactly
`parent_head_only` with no wall-clock expiry: decision admission requires the parent remain the unique verified
lineage head and have no accepted child, recomputed by scanning verified immutable transactions rather than
trusting the catalog. It contains no decision or mutable status. Its payload files are `recommendation.json`,
`source_snapshot.json`, and `environment_snapshot.json`.

### Decision

Exact decision is `accept` or `reject`. `actor_id` matches `[a-z][a-z0-9._-]{2,63}`; reason is 1-1024 UTF-8
characters without CR/LF. Confirmation is exactly `ACCEPT SIMULATION CALIBRATION <recommendation_id>` or
`REJECT SIMULATION CALIBRATION <recommendation_id>`. Actor ID is an audit label rather than strong
authentication. Cryptographic signatures are deferred to Stage 9 unless the user chooses otherwise before
freeze.

`decide_calibration` is the only mutation API and publishes one transaction directory. Reject contains payload
`decision.json`; accept prepares both `decision.json` and `calibration.json`. Both variants also contain
`source_snapshot.json` and `environment_snapshot.json`. The accept path verifies their circular-free
construction using the decision payload hash embedded in calibration, then atomically publishes both under one
manifest/report/receipt. There is no separate materialization call, so an accepted decision without its
calibration cannot become authoritative.

The transaction envelope artifact type is exactly `stage_07_calibration_decision_transaction`, its
`transaction_id` and manifest/report/receipt `object_id` equal `decision_id`, and status is exactly `accepted` or
`rejected`. Accepted payload files are `{decision.json,calibration.json,source_snapshot.json,
environment_snapshot.json}`; rejected payload files are `{decision.json,source_snapshot.json,
environment_snapshot.json}`. The payload's own artifact types remain decision/calibration types; the envelope
type is never reused for an individual payload.

An exclusive lock keyed by recommendation SHA and parent-calibration SHA serializes decisions. Crash before
publication consumes only the attempt ID. Explicit recovery quarantines the attempt, publishes an interrupted
record, and then releases the operation lock so a new decision attempt may be made. A verified transaction
permanently consumes the recommendation. Concurrent, duplicate, stale-parent, or branching accept attempts fail.

### Accepted simulation calibration

Only an accepted decision can publish a new full snapshot. It binds parent, recommendation, decision, analyses,
device, QCIS profile/QAgent/gate/waveform/compiler authorities, Stage 4.1, Stage 5.1, and source/environment
hashes. It copies unchanged parent
values plus the exact approved delta, receives a new UUID4/state ID, and uses `status=accepted_simulation` and
`hardware_claim=none`. `bootstrap_policy_sha256` is the uppercase policy hash when the current or any inherited
partial calibration consumed bootstrap seeds, otherwise null; it cannot change within that lineage. Rejection
creates no calibration snapshot.

Old runs, recommendations, decisions, and calibrations are never edited. Repository config export, if required,
must copy identical accepted bytes under a new tracked path and verify the same SHA.

## 13. Storage, recovery, and catalog

Derived objects use exclusive operation locks, sibling staging, file and directory durability flushes, complete
preflight verification, and platform-native atomic no-replace directory rename. Failure never resumes an
analysis, recommendation, or decision transaction. Explicit recovery quarantines the original tree and
publishes a separate interrupted record using the Stage 6 recovery principles.

Catalog tables index verified runs, compiled programs, analyses, recommendations, decisions, calibrations, and
derivation edges. Numbered transactional migrations and WAL checkpoint are mandatory. The catalog can always be
deleted and rebuilt from immutable directories and never grants trust by itself.

## 14. Verification and release gates

Independent verification recomputes every schema, source, environment, parent edge, compiler expansion, control
hash, raw dataset, analysis input/output, recommendation predicate, decision uniqueness, and calibration delta.
Coordinated rehash attacks must fail by recomputation from frozen authorities.

Run admission uses an exact fail-closed truth table:

```text
common:
  request 0.2 valid AND instruction/compiler authority valid AND Stage 4.1 approved

platform compiler/fake fixture:
  common AND backend capability is fake-only AND recommendation_eligible=false

model-derived simulation:
  common AND backend capability is the exact registered Stage 5.1 model set
  AND Stage 5 formal approval valid
  AND Stage6ReadinessReport.stage6_ready=true
  AND Stage 6 physics-backend gate valid AND Stage 5.1 approval valid
  AND Stage 7 model-evolution entrance valid
  AND experiment definition/analysis constants frozen
  AND bootstrap policy or accepted calibration authority valid
  AND recommendation_eligible=true only for independently approved non-fake definitions
```

Every term is independently hash-verified before reservation. Missing, false, stale, wrong-version, or
capability-mismatched terms reject with no output.

Stage 7.0 release requires compiler test vectors, all fake fixtures, cross-platform storage tests, Stage 4.1
approval, independent test review, and release review. It makes no physics claim.

Stages 7.1-7.3 each additionally require Stage 5 formal and Stage 5.1 approval, frozen experiment-specific
physics constants, feasibility evidence, end-to-end model-derived runs, independent numerical review, and user
acceptance. A later phase cannot weaken or retroactively reinterpret an earlier artifact.

## 15. Frozen product decisions and deferred physics authority

The frozen product choices are four-phase delivery, local actor ID plus reason without signatures until Stage 9,
single-qubit-first order with q1 completed before q2, and q1-to-q2-only CZ for the MVP. Physics reviewers must
later freeze numerical scan/fit/acceptance constants from an authorized feasibility pilot after Stage 5 formal
qualification. Until then only Stage 7.0 implementation may advance.
