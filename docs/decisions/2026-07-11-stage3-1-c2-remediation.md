# Stage 3.1 C2 implementation remediation

Date: 2026-07-11

Status: proposed for independent design review

## Decision

The first Stage 3.1 formal run is rejected as an implementation candidate. Its raw spectral data remains
historical diagnostic evidence, but its crossing classifications, acceptance-anchor decisions, runtime
report, notebook execution claim, and verification report are not eligible for final approval.

This remediation changes no device parameter, Hamiltonian, cutoff increment, flux grid, acceptance
anchor, numerical tolerance, status priority, solver specification, or 1381-solve ceiling. The frozen
Stage 3.1 design remains authoritative. C2.1 only brings the implementation into conformance with it.

## Rejected C2 evidence

The one and only C2 run produced:

- `q1_q2_coupling_artifacts.json`:
  `90972BB6A61D0D7C770F50AFAB3361F6A738A79C501DACDEC5E90647DFF620F5`
- `verification.ipynb`:
  `C8E152DB0FDFE102A1C686E9A1A4EA208FF012A040E8A3D5E02E7ADE9F6C29D3`
- `verification_report.json`:
  `BAEB0409031F3896230641AE3C812525274FDCD00751E7616733736968EA434C`

The files are canonical/finite and may be retained, but no acceptance approval may bind them.

## Required implementation corrections

### 1. Adiabatic branch identity and evidence domain

At each fixed coupler flux and cutoff signature, the leftmost evaluated q2 point establishes semantic
`100`, `010`, and `001` branch identities. Every later point must propagate those identities with the
existing deterministic adjacent-point one-to-one overlap assignment. A fresh bare-state assignment at
each point is diagnostic only and must not replace the tracked branch identity.

After every adaptive insertion, tracking is recomputed over the complete merged Decimal-sorted grid,
starting from the same fixed leftmost semantic anchor. Inserted points are not tracked only from their
insertion order. The existing deterministic overlap-assignment tie break is mandatory. Baseline and each
refined cutoff signature perform this operation independently and never compare vectors across Hilbert
dimensions.

For the tracked q1/q2 pair, the implementation must recompute energies, participation, target-projector
weights, adjacent overlaps, and target-subspace continuity at every ordered point. A synthetic avoided
crossing must demonstrate that the same tracked branch changes from q1-like to q2-like while the target
subspace remains continuous.

The baseline evidence domain must implement the frozen search exactly: starting at the minimum, select
the nearest left and right keys where the two tracked target branches have distinct dominant q1/q2
characters and each required fraction is at least 0.80. The domain is every evaluated Decimal-ordered key
between those endpoints, union the final bracket left/minimum/right keys. Missing endpoints fail closed.

Each refined-cutoff scan must use the baseline selected character endpoints as the endpoints of its
17-point uniform grid, union the baseline final bracket keys, then run the four frozen 11-point refinement
levels. It must track branches and select its own evidence domain within its own Hilbert dimension.

### 2. Runtime ledger and preflight

The order is acyclic and exact:

```text
verification start
-> provenance/solver preflight
-> idle baseline and idle refinements
-> baseline outer/inner scans
-> three-anchor q1/c/q2 cutoff convergence
-> capture analysis_elapsed_seconds and freeze phase ledger
-> construct RuntimeReport
-> pure finalize
-> pure modulation
-> pure computational gate
-> artifact/notebook/report publication
```

`analysis_elapsed_seconds` is captured immediately after cutoff convergence and before `RuntimeReport`
construction. It includes elapsed time from formal verification start through every numerical phase. It
does not include finalize, modulation, gate assembly, or publication; those operations consume the fixed
runtime report and perform no solver evaluation. This boundary removes any dependency on later
computational-gate inputs.

The phase ledger is an exact mapping with these four keys and no others:

```text
idle_baseline
idle_refinements
outer_inner_scans
anchor_cutoff_convergence
```

Each phase value contains exactly:

```text
evaluations_by_dimension = {"3375": nonnegative integer, "4275": nonnegative integer}
cache_hits_by_dimension  = {"3375": nonnegative integer, "4275": nonnegative integer}
elapsed_seconds          = finite nonnegative float
```

An evaluation is one actual solver call caused by a cache miss. A cache hit never increments
evaluations. A full cache identity reused within a phase/signature increments only cache hits; separate
solver calls in separate phase-local caches remain actual evaluations and are counted. Totals are exact
sums over the four phases and two dimensions. The conservative initial remaining counts are exactly
`{"3375": 874, "4275": 507}`, corresponding to 873 baseline outer scans plus one idle baseline, and 504
anchor refinements plus three idle refinements. Their sum is 1381.

Before each of the four numerical phases, the plan computes:

```text
projected_total_seconds =
    elapsed_seconds_so_far
    + remaining_by_dimension["3375"] * approved_p95_by_dimension["3375"]
    + remaining_by_dimension["4275"] * approved_p95_by_dimension["4275"]

projected_total_evaluations =
    completed_evaluations
    + remaining_by_dimension["3375"]
    + remaining_by_dimension["4275"]
```

`remaining_by_dimension` is the conservative, not adaptive-best-case, unscheduled bound at that phase
boundary. Missing, negative, non-integral, or non-finite ledger/p95 values fail closed. The next phase is
not started when projected seconds exceed 1800 or projected evaluations exceed 1381. After convergence,
remaining counts are exactly zero, `projected_total_seconds == analysis_elapsed_seconds`, and the final
runtime report contains the complete phase ledger, approved p95 mapping, actual totals, zero remaining
mapping, projected total, budget, ceiling, and both pass/fail results.

### 3. Computational gate schema

The frozen public return-contract name remains `StageGateDecision`. For Stage 3.1,
`Stage31ComputationalGate` is the implementation dataclass/alias for that frozen four-field contract;
`evaluate_stage3_1_gate` is annotated with the module-local `StageGateDecision` alias and returns that
object with exactly:

```text
computational_ready, status, ordered checks, blocking reasons
```

The existing `sqvm.spectrum.models.StageGateDecision` used by Stage 3 v0.1 is not modified, replaced, or
re-exported under a different meaning. Stage 3.1 must not import that legacy class. Its module-local alias
provides source compatibility with the frozen Stage 3.1 interface while `Stage31ComputationalGate`
provides an unambiguous implementation type.

The artifact top-level `checks` must contain the same ordered computational checks. The Stage 3.1 object
must not use the legacy fields `analysis_completed`, `stage4_ready`, or `candidate_statuses`.

The post-write `Stage31VerificationReport` must retain the original computational gate and add its own
artifact/notebook checks separately. It must not replace computational checks with post-write checks.

### 4. Transaction paths

The transaction may create files in a sibling staging directory, but `artifact_write.path` and
`notebook_write.path` embedded in the verification report must be the final paths under
`output/stage_03_1_q1_q2_coupling`. The report must be assembled with final-path write results before the
staging directory is renamed. Every reported path must exist after publication.

### 5. Real notebook execution

The notebook must contain a first code cell that loads only the sibling canonical artifact into `data`.
It must then be executed by the project's notebook execution mechanism in the staging directory. Manually
setting `execution_count` or outputs is forbidden. Publication requires every code cell to have a genuine
execution count and zero error outputs. A test must clear outputs and execute the generated notebook to
prove it is self-contained and read-only.

## Required tests

The C2.1 implementation must add focused tests for:

- tracked branch identity and character exchange across a synthetic two-level avoided crossing;
- deterministic character-endpoint selection and complete adjacent-edge continuity gating;
- refined 17-point grid endpoints and forced final-bracket keys;
- missing character endpoints, ambiguous tracking, or non-finite overlaps failing closed;
- exact runtime phase totals, cache-hit totals, 1381 ceiling, and budget preflight;
- exact computational-gate schema and preservation in the verification report;
- final transaction paths existing after rename and cleanup on failure;
- genuine notebook execution with a defined `data` object and no fabricated outputs.

All existing Stage 3.1 tests and the safe full repository suite must pass. No formal acceptance may run as
part of C2.1 development or solver revalidation.

## Revalidation and rerun lifecycle

Any source correction invalidates solver candidate
`197729DE734F72D3A4EA3A9F5DB5E13D9D05671BC3887775A82AD01E51E4A0AE` and approval
`43E8DBF6864B01E6420275ECA3485F58BF57F18334D6A7A7FD1AB238255CCF64`.

After C2.1 tests pass, development generates exactly one new 44-case candidate. Independent testing must
repeat the bounded solver review and issue a new source/hash-bound approval before any formal rerun.

Before the new formal run, the rejected three-file directory must be renamed without rewriting bytes to:

```text
output/stage_03_1_q1_q2_coupling_c2_rejected
```

The archive operation is fail closed. Before rename, the source directory must exist and contain exactly
the three named files above, no approval, no temporary file, and the three exact hashes recorded in this
decision. The archive target must not exist. The source and target parents must resolve to the same file
system, and the operation must be one directory rename; copy/delete, merge, nesting, overwrite, or
replacement of an existing target is forbidden. Any failed precondition leaves the source untouched and
blocks the new run.

After rename, the archive must contain exactly the same three files and hashes, the old formal path must
be absent, and the archive receives no approval file. A new-source C2.1 cycle is authorized to execute the
formal runner exactly once after the new solver approval; this is not a retry of the rejected source.

Stage 4 remains blocked until an independently approved, hash-bound Stage 3.1 acceptance exists.
