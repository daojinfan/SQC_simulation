# Stage 7 Plan: Calibration Experiments

## Status and entry gate

This plan is frozen for Stage 7.0 implementation. It authorizes the QCIS compiler, schemas, deterministic fake
fixtures, and evidence-ledger foundation described below. It does not authorize a model-evolution backend,
numerical scan, or calibration decision from model-derived data.

Stage 6 is accepted only as a deterministic platform framework. Stage 7 model-derived physics execution remains
closed until Stage 4.1 parameterized control, Stage 5 formal-scale qualification, Stage 5.1 parameterized
evolution, `Stage6ReadinessReport.stage6_ready=true`, the separately frozen Stage 6 physics-backend gate, and
the Stage 7 model-evolution-backend entrance are independently approved. Stage 7.0 pure compilation,
schema, fake-fixture, and evidence-ledger work may be separately authorized without opening those physics gates.

## Purpose

Build the first useful simulator calibration workflows on the Stage 6 scan/runtime framework while preserving
strict separation between:

```text
program definition and deterministic compilation
model-derived execution and immutable raw data
analysis and calibration recommendation
explicit human accept/reject decision
new immutable simulator calibration state
```

Stage 7 never edits an existing run or calibration snapshot and never claims measured hardware behavior.

QCIS is the sole external control-program format for every Stage 7 and later calibration experiment. Each scan
point materializes concrete QCIS, parses it to reviewed logical waveforms, compiles those waveforms through
Stage 4.1, and supplies only verified effective controls to Stage 5.1/QuTiP. Direct experiment-to-waveform or
experiment-to-Hamiltonian shortcuts are forbidden.

## Scope and phases

### Stage 7.0: compiler and evidence foundation

- request schema `0.2` with a non-null, versioned program object;
- QCIS template, parser, QAgent/configuration/waveform registries, and internal typed IR schemas;
- typed `scan_ref` resolution;
- deterministic QCIS -> logical waveform plan -> Stage 4.1 effective-control compilation;
- Stage 4.1 and Stage 5.1 rebaseline designs and gates;
- append-only analysis/recommendation/decision/calibration evidence ledger;
- deterministic fake Rabi, Ramsey-frame, and CZ-barrier fixtures;
- no model-derived physics backend and no calibration acceptance from fake data.

### Stage 7.1: qubit model calibration

Ordered workflows:

```text
registered qubit target: spectroscopy -> Rabi amplitude at policy duration -> Ramsey frequency -> DRAG dragAlpha -> X2P/Y2P
```

Spectroscopy target selection is capability based and is not hard-coded to Q1 or Q2. One target may run in
`single` mode, or two supported targets may run in one `parallel_lockstep` QCIS circuit with equal-duration,
absolute-time PLSXY pulses. A parallel spectroscopy run has one parent snapshot and one joint evidence graph;
it is not two concurrent configuration-writing branches. Target-specific candidates remain independently
reviewable, and all accepted deltas are applied atomically in one child snapshot revision.

Parallel spectroscopy must pass registered single-drive confirmation and cross-impact gates before its
candidates become recommendation eligible. This amendment does not authorize parallel Rabi, Ramsey, DRAG,
or gate-setting calibration; those workflows remain ordered until separately designed and approved.

Implementation status: `qubit_spectroscopy_calibration_v1` now provides a bounded pilot of this complete
spectroscopy-only workflow. It reuses `run_circuits` for coarse, refined, and single-confirmation circuits,
publishes datasets/analysis/gates/plot/candidates atomically, and requires an explicit accept/reject transaction
before creating an `accepted_simulation` frequency snapshot. Its outputs remain `bounded_smoke_only`; this
implementation does not open the production physics authority described by the entry gate above and does not
authorize the later Rabi/Ramsey/DRAG workflows.

### Stage 7.2: coupler model calibration

- QCIS `PLS C` response scan through Stage 4.1 and QuTiP versus c flux and hold;
- model-derived idle/interaction-region recommendations from evolved populations/leakage/phase;
- bound transition/gap branch tracking as diagnostic evidence only, never standalone calibration authority;
- no hardware flux-transfer or measured avoided-crossing claim.

### Stage 7.3: CZ model calibration

- bounded coarse grid over coupler flux amplitude and hold time;
- computational-subspace conditional phase, truth-table populations, and leakage;
- local phase removal under a frozen convention;
- recommendation only, followed by human accept/reject.

## Inputs

Every Stage 7 object binds exact content hashes for:

- accepted Stage 6 runtime authority and source snapshot;
- device and parent calibration snapshots;
- QCIS source/profile, QAgent/gate/waveform registry, experiment-template, and compiler snapshots;
- Stage 4.1 approval, channel registry, control configuration, and compiler source;
- Stage 5 formal approval and Stage 5.1 parameterized-backend approval when physics execution is requested;
- experiment definition, analysis definition, environment lock, and source tree.

Bootstrap execution additionally binds the exact prior fields it consumed and a reviewed bootstrap-policy ID.

## Core objects

```text
InstructionSetSnapshot
QCISTemplate
ConcreteQCISProgram
TypedQCISAST
QCISLogicalWaveformPlan
CompiledPointProgram
ParameterizedControlArtifact
ModelPointResult
AnalysisReport
CalibrationRecommendation
CalibrationDecision
AcceptedSimulationCalibration
```

All public models are immutable. Mappings and sequences are deep-frozen before crossing module boundaries.

## Public interfaces

Proposed stable APIs:

```text
load_stage7_request(path) -> Stage7ExperimentRequest
compile_program(request, point, calibration, compiler_context) -> CompiledPointProgram
verify_compiled_program(compiled, authority) -> CompilationVerificationReport
run_stage7_experiment(request_path, output_root) -> RunArtifactSet
analyze_stage7_run(run_dir, analysis_id, output_root) -> AnalysisArtifactSet
propose_calibration(analysis_dir, parent_calibration, output_root) -> RecommendationArtifactSet
decide_calibration(recommendation_dir, decision, actor_id, reason, output_root) -> DecisionTransactionArtifactSet
verify_calibration_derivation(calibration_dir) -> CalibrationVerificationReport
```

`decide_calibration` is one atomic transaction. A reject publishes only a decision payload; an accept publishes
the decision and new full calibration payload in the same no-replace transaction directory and receipt. There
is no separately callable materialization step.

`run_stage7_experiment` is unavailable for model-derived experiments until all physics gates pass. Pure compile
preview and fake contract fixtures use separate capabilities and cannot publish calibration recommendations.

## Package ownership

```text
src/sqvm/runtime/       schema dispatch, lifecycle, locks, run evidence, catalog
src/sqvm/experiments/   experiment definitions, program IR, macro expansion, point commands
src/sqvm/control/       reviewed Stage 4.1 typed logical-control and waveform compilation
src/sqvm/evolution/     reviewed Stage 5.1 model-state execution backend
src/sqvm/analysis/      deterministic fits, diagnostics, plots, recommendations
src/sqvm/calibration/   append-only decisions and accepted simulator calibration snapshots
```

Experiment definitions cannot write files, perform fits, mutate calibration, or select arbitrary callables.
Analysis cannot execute a backend or edit a run. Calibration code cannot reinterpret raw data or fit results.

## Scientific outputs before Stage 8

Allowed outputs are explicitly `model_derived`:

- energy gaps and named-mode character;
- computational-projector populations and leakage;
- coherent amplitude and phase;
- conditional phase after frozen local-phase removal;
- deterministic fit parameters, residuals, conditioning, and refinement checks.

Forbidden outputs include IQ, shots, assignment, measured linewidth, measured `T1/T2`, readout fidelity,
measurement payloads, and any `measured` or hardware-calibrated label.

## User workflow

```text
1. Inspect the parent calibration and Stage 7 request.
2. Preview scan expansion and compile every point without reservation.
3. Verify compiler, Stage 4.1, Stage 5.1, and physics entrance gates.
4. Run a bounded immutable model-derived scan.
5. Inspect raw data and independently verify the run.
6. Run a named deterministic analysis and inspect diagnostics/plot.
7. Create a recommendation that remains separate from calibration state.
8. Explicitly accept or reject with actor ID and reason.
9. On accept only, atomically publish a new immutable `accepted_simulation` calibration snapshot.
```

## Implementation sequence

1. Review and freeze the Stage 7 decision, plan, detailed designs, schemas, and
   `docs/designs/07_qcis_compiler_test_vectors.md` byte oracles.
2. Design and approve Stage 4.1 parameterized-control rebaseline.
3. Implement Stage 7.0 IR models, strict loaders, compiler, fake fixtures, and verification.
4. Implement the append-only analysis/recommendation/decision/calibration ledger using fake-only evidence.
5. Complete Stage 5 formal-scale qualification and obtain `Stage6ReadinessReport.stage6_ready=true`.
6. Design, implement, and approve Stage 5.1, the Stage 6 physics-backend gate, and Stage 7 model-evolution entrance.
7. Implement Stage 7.1 experiments and independently approve each definition/analysis pair.
8. Implement and approve Stage 7.2, then Stage 7.3.
9. Generate immutable outputs, cross-platform evidence, independent reviews, and development-log entries per phase.

## Test matrix

Required coverage includes:

```text
strict program/version/opcode/key/type/path admission and arbitrary-code rejection
strict QCIS token/arity/target/wave-index/tStart/I/RZ/B and unsupported-opcode rejection
macro recursion/unknown macro/unknown calibration key rejection
typed scan_ref unit/range/axis/field binding and no string interpolation
channel, target, frame, cursor, barrier, timing-grid, overlap, and resource-conflict checks
map order, locale, timezone, thread count, and repeated compilation determinism
Stage 4.1 frozen-output equivalence and Stage 5.1 formal-gate attacks
fake/model backend capability separation and pre-reservation fail-closed behavior
raw dataset completeness, dtype, unit, finiteness, point binding, and tamper checks
fit algorithm/version/input/diagnostic binding and non-convergence rejection
recommendation expiry/applicability/parent-state and duplicate/concurrent decision attacks
accept/reject atomicity, no auto-accept, no old-run/snapshot mutation, and derivation verification
Windows junction/UNC/case/file-lock and POSIX symlink/no-replace/directory-flush behavior
Stage 8 IQ/shot/assignment/measurement/readout claims rejected
```

Minimum fake end-to-end fixtures are `fake_qcis_rabi_v1`, `fake_qcis_ramsey_frame_v1`, and
`fake_qcis_cz_barrier_v1`, plus
compile failure, backend failure, cancellation, recovery, incomplete analysis, and interrupted decision cases.

## Artifacts

Stage 7 extends each run with hash-bound program/compiler/control snapshots. Derived objects live in an
append-only root, proposed as:

```text
output/stage_07_calibration/
  analyses/<analysis_id>/
  recommendations/<recommendation_id>/
  decision-transactions/<decision_id>/
  staging/
  locks/
  recovery-locks/
  quarantine/
  catalog.sqlite
```

SQLite is a rebuildable verified index, never evidence authority.

## Acceptance criteria

Stage 7 is complete only when all four phases are independently accepted. A phase passes only when its frozen
schemas, public APIs, tests, end-to-end output, independent verification, human-readable result, limitations,
and release review are complete. Stage 7.0 acceptance does not imply any physics phase is open.

No calibration snapshot is accepted without an explicit human decision, and acceptance always means simulator
configuration only until a later hardware/measurement contract exists.

## Frozen choices and deferred physics constants

1. The four-phase delivery order is approved.
2. The local approval identity is canonical actor ID plus mandatory reason and typed confirmation; signatures
   remain deferred until Stage 9.
3. Model spectroscopy may target one registered qubit or run two supported targets in one authority-bound
   `parallel_lockstep` experiment. Target candidates remain independently reviewable and accepted deltas are
   applied in one append-only child revision. Rabi, Ramsey, DRAG, and gate-setting workflows remain ordered.
4. The MVP CZ direction is q1-to-q2 only.
5. Freeze numerical scan ranges, fit thresholds, refinement tolerances, and recommendation validity ranges only
   after Stage 5 formal qualification and a reviewed feasibility pilot; placeholder thresholds are forbidden.
