# Stage 3.1 execution plan: q1-q2 coupling sweep

Status: design review pending

## Outcome

Produce a verified curve of `q1-q2` avoided-crossing splitting versus coupler flux and prove that at least
one change in splitting is significant relative to numerical uncertainty.

Stage 3.1 does not modify the Stage 3 v0.1 artifact. It supersedes v0.1 only as the gate required before
Stage 4.

## Work packages

### D1. Design freeze

- Review the objective-correction decision and detailed design.
- Verify the q2 inner scan and fixed coupler grid against the current device model.
- Resolve all interface, solver-validation, runtime-ceiling, status, and artifact-schema findings.
- Create the canonical independent design-freeze manifest binding the three frozen document hashes.

Exit: independent design review approves freezing Stage 3.1 v0.1.

### D2. Full flux-vector foundation

- Generalize spectrum point identity from coupler-only flux to q1/c/q2 flux vectors.
- Add the strict schema-0.2 config and `q1_q2_coupling_vs_coupler` dispatch.
- Generalize Hamiltonian rebuild, deterministic cache key, and solver seed.
- Add `sha256_counter_v2` canonical serialization and normative vectors.
- Preserve Stage 2.1 provenance and input immutability.

Exit: unit tests prove distinct full flux vectors cannot collide.

### D3. q1-q2 crossing analyzer

- Implement fixed-coupler q2 scans and adaptive refinement.
- Track `|100>`, `|001>`, and spectator `|010>`.
- Add full-domain target-subspace continuity, bare-projector, character exchange, resonance alignment,
  participation, and boundary evidence.
- Compute minimum splitting and magnitude-only `abs(g_eff)` only after the pairwise/alignment gates pass.

Exit: synthetic tests and the reference coupler-point smoke scan resolve a q1-q2 crossing.

### D4. Coupler sweep and convergence

- Run the configured coupler grid.
- Refine q1/c/q2 cutoffs independently at the three acceptance anchors.
- Compute the frozen summed cutoff uncertainty, inner-flux stencil, level drift, solver error, and
  fail-closed modulation significance.
- Finalize immutable per-point status only after raw scan, convergence, and runtime evidence are complete;
  downstream modulation/gate/artifact code consumes only finalized points.
- Reject coupler-hybridized points from the pairwise coupling claim.
- Build a new exact execution ceiling and fail-closed runtime plan.

Exit: a non-acceptance pilot artifact contains the full curve and deterministic statuses.

### D5. Solver validation

- Generate the new 4-signature by 11-full-flux-vector candidate (44 exact cases).
- Run dense/eigsh, projector, repeatability, participation, metric, provenance, and canonical checks.
- Hand the candidate to independent test.
- Do not run formal acceptance before a new approval exists.

Exit: independent solver review writes a hash-bound approval.

### D6. Formal acceptance

- Snapshot the design-freeze manifest, Stage 2.1, solver candidate, approval, config, source, and environment hashes.
- Run exactly one formal Stage 3.1 acceptance.
- Write the canonical artifact and executed read-only notebook.
- Assemble and atomically publish the artifact, notebook, and pending-approval verification report as
  one staged directory transaction without rewriting the artifact.
- Run the bounded regression suite without rerunning formal acceptance.
- Hand the formal output to independent test/review; development cannot create acceptance approval.

Exit: independent review either writes a hash-bound approval that validates to `stage4_ready=true`, or
records a valid blocking diagnosis.

## Development handoff boundary

Development may modify only the Stage 3.1 implementation, new config, new runner, new tests, and new
Stage 3.1 output paths named in the frozen handoff. It must not modify:

- Stage 1/2/2.1 production inputs or approvals;
- Stage 3 v0.1 formal artifact or notebook;
- frozen Stage 3 v0.1 documents;
- device geometry or Hamiltonian parameters;
- frozen thresholds, flux points, or status priority.

Any required source change after solver approval invalidates that approval and returns to D5.

## Acceptance matrix

| Area | Required result |
|---|---|
| Provenance | Stage 2.1 chain and dense 12-gap consistency pass |
| Solver | new full-flux validation and independent approval pass |
| Far-detuned anchor | `c=0.200 Phi0` q1-q2 crossing resolved |
| Reference crossing | `c=0.270 Phi0` q1-q2 crossing resolved |
| Near-detuned anchor | `c=0.385 Phi0` q1-q2 crossing resolved |
| Pairwise identity | target bare-projector >=0.90, total excitation 0.90-1.10, c fraction <=0.05 across the full evidence domain |
| Resonance | deterministic zero-detuning root lies in the final evidence domain and detuning at minimum <=0.50 MHz |
| Continuity | every adjacent final-evidence edge has target-subspace continuity >=0.90 |
| Convergence | summed q1/c/q2 cutoff, exact inner-flux stencil, final level drift, and solver uncertainty pass the frozen 0.01 MHz/5%/5x gates |
| Modulation | at least one anchor differs from reference by >=5 combined uncertainties |
| Runtime | within 1800 seconds and no more than 1381 solver evaluations |
| Output | canonical finite artifact, executed read-only notebook, and pending-approval report published as one directory transaction |
| Gate | computational/report candidate passes, then only independent hash-bound approval may produce `stage4_ready=true` |

The frozen conservative execution ceiling is 1381 solver evaluations:

```text
873 baseline outer/inner scan solves
504 three-anchor, three-mode crossing-convergence solves
3 idle refined solves
1 idle baseline solve
```

## Test allocation

Development AI:

- implement and run all unit/integration tests;
- run one smoke scan and one solver-validation candidate generation;
- provide hashes and explicit protection evidence;
- never self-approve.

Independent test AI:

- review code and frozen contract alignment;
- run the full safe suite;
- run representative negative tests, not the entire attack matrix manually;
- regenerate the 44 solver cases once in a temporary directory;
- approve or reject solver validation;
- independently inspect the one formal acceptance artifact.
- only after candidate acceptance, write the canonical hash-bound Stage 3.1 acceptance approval.

Project/design AI:

- own scope, thresholds, scan points, and status semantics;
- adjudicate findings without changing physical criteria to force a pass;
- keep Stage 4 blocked until independent Stage 3.1 acceptance.
