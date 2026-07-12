# Stage 3.1 C2.2 fixed-refinement remediation

Date: 2026-07-11

Status: proposed for independent design review

## Decision

The C2.1 formal result is rejected as an acceptance candidate because the fixed cutoff-refinement
state machine cannot report successful completion. This is an implementation error, not a physical,
numerical-convergence, runtime-budget, geometry, or coupling-modulation conclusion.

This correction changes no device parameter, Hamiltonian, cutoff, flux point, grid size, refinement
count, tolerance, acceptance anchor, status priority, solver specification, runtime budget, or
1381-solve ceiling. The frozen Stage 3.1 design and the approved C2 remediation remain authoritative.
C2.2 defines only the completion semantics already required by the frozen four-level cutoff scan.

## Rejected C2.1 evidence

The single C2.1 formal run produced:

- `q1_q2_coupling_artifacts.json`:
  `4D7669E0319A75A3C29F7F273AB963D4422F4E3AB8649559E045C30AB149845A`
- `verification.ipynb`:
  `A7194EB8157FDC979FD11BA5896C8E1908191B2A4B738525439023F1CC47B163`
- `verification_report.json`:
  `7AD9DEF5CD047447F6F7EA453D81409162E1B0C46594DDEA75CED557C3E3E9F1`

The files are canonical, finite, structurally valid diagnostic evidence. Their runtime ledger,
provenance, solver binding, final paths, and genuine notebook execution are valid. They are not
eligible for acceptance approval because the cutoff-refinement status is implementation-generated.

The rejected result independently shows:

- all ten baseline physical predicates pass at coupler anchors `0.200`, `0.270`, and `0.385 Phi0`;
- all nine refined q1/c/q2 minima lie inside the baseline bracket and their own evidence domain;
- the three `U_total` values are `3.247357938107598e-6`, `1.8682300151340314e-6`, and
  `1.332672638909571e-6 MHz`, and all frozen numerical uncertainty comparisons pass;
- the only common refined-row failure is `physical_predicates_preserved=false`.

## Required state-machine correction

### Adaptive baseline mode

Adaptive baseline behavior is unchanged. It uses the frozen `L0` initial grid and adaptive `L1` through
at most `L8`. Its existing `converged`, `boundary`, and `max_refinement_exhausted` meanings, including
the two-consecutive-level convergence rule, remain unchanged.

### Fixed cutoff-refinement mode

For each acceptance anchor and each refined q1/c/q2 cutoff signature, fixed mode must:

1. evaluate the 17-point initial evidence grid as `L0`;
2. execute exactly four 11-point refinement levels `L1`, `L2`, `L3`, and `L4`;
3. retain the existing fail-closed `boundary` result if any level lacks an interior minimum and valid
   immediate-neighbor bracket;
4. retain fail-closed numerical-structure results for a missing level, non-finite splitting or drift,
   invalid bracket, or excessive final drift;
5. after a numerically valid `L4`, set `termination_reason=fixed_refinement_completed` instead of
   `max_refinement_exhausted`.

`fixed_refinement_completed` is valid exactly when:

```text
fixed_levels is true
levels L0, L1, L2, L3, and L4 all exist
L4 is the final level
L4 has an interior minimum and distinct finite left/minimum/right bracket
L3 and L4 minimum splittings are finite
L4.level_to_level_drift_MHz = abs(L4.splitting_MHz - L3.splitting_MHz)
L4.level_to_level_drift_MHz <= existing level convergence tolerance
no earlier boundary or numerical-structure failure exists
```

No adaptive two-consecutive-level requirement applies to fixed mode. Completing four levels alone does
not bypass the finite final-drift comparison. Physical evidence is deliberately excluded from the
termination decision: it is evaluated afterward and must not rewrite a numerical termination reason.

For `_physical_evidence`, `numerically_converged=true` exactly when either:

```text
termination_reason == converged
```

for adaptive mode, or:

```text
termination_reason == fixed_refinement_completed
and the fixed-completion contract above is satisfied
```

for cutoff-refinement mode. `max_refinement_exhausted` remains non-converged and must never be silently
reinterpreted as fixed completion.

After numerical convergence is fixed, `_physical_evidence` independently evaluates tracking, character
endpoints, target-subspace continuity, projector, excitation, coupler fraction, detuning root,
alignment, and character exchange. A failed physical predicate remains fail closed through the existing
per-point status priority and through `physical_predicates_preserved=false` in a refined row. It does
not set `numerically_converged=false`, cancel `fixed_refinement_completed`, or rewrite termination.
Thus, for example, a numerically completed fixed scan with spectator leakage may correctly retain
`numerically_converged=true` while failing `subspace_continuity_valid` and being rejected by the physical
or uncertainty gate.

## Required tests

C2.2 development must add focused tests that prove:

- a valid fixed scan contains exactly `L0` through `L4`, returns
  `termination_reason=fixed_refinement_completed`, and can preserve all physical predicates;
- a fixed scan with a non-finite final drift, excessive final drift, missing level, missing bracket, or
  boundary does not produce fixed completion;
- adaptive convergence still requires its existing rule;
- adaptive `max_refinement_exhausted` remains non-converged;
- anchor convergence no longer fails solely because a valid fixed scan completed all four levels;
- a numerically valid fixed `L0-L4` scan with a representative physical-evidence failure retains
  `termination_reason=fixed_refinement_completed` and `numerically_converged=true`, while the relevant
  physical predicate, refined-row `physical_predicates_preserved`, uncertainty gate, and existing
  per-point status fail without being reclassified as numerical failure;
- no existing Stage 3 v0.1 or Stage 3.1 behavior outside this termination path regresses.

Focused Stage 3.1, the complete spectrum suite, and the safe full repository suite must pass. No formal
runner, formal CLI, or production-output smoke may run during development or solver revalidation.

## Revalidation and rerun lifecycle

The source correction invalidates:

- Stage 3.1 source `F4E42A4C13C362A9208AF0882F033161AF174BFD8D6E3FBB80AB8FC6FB831F0B`;
- solver candidate `406FE9FAB8B39212D86C5507EF456F8B8E3B66243B7E649A5D155EBAF68F5495`;
- solver approval `DE4E9CCE40AFA51D46DD5E78201FA39602B9CED8B0CDD615BE4CC8ED08E61F23`.

After C2.2 tests pass, development may generate exactly one new 44-case candidate. Its top-level
`bindings` mapping has the exact existing keys plus one new required key:

```text
config_sha256
dense_pilot_sha256
design_freeze_manifest_sha256
environment_fingerprint_sha256
hamiltonian_config_sha256
stage2_artifacts_sha256
stage2_model_source_tree_sha256
stage2_rebaseline_approval_sha256
stage2_rebaseline_manifest_sha256
stage3_1_c2_remediation_sha256
stage3_1_c2_2_fixed_refinement_remediation_sha256
stage3_1_source_tree_sha256
```

The existing `stage3_1_c2_remediation_sha256` remains exactly
`9CA7FFA870DA6F83581D04DE067D5F618E388BAEC72162E0CBBF752487158C04`.
The new `stage3_1_c2_2_fixed_refinement_remediation_sha256` equals the uppercase raw-file SHA-256 of
this document after independent approval. It does not replace or reinterpret any existing key.

The candidate generator and validator require the exact expanded key set, recompute both remediation
hashes from their fixed repository paths, and reject missing, extra, stale, malformed, or mismatched
bindings. The independent solver review and canonical approval validate that exact candidate mapping
and bind the candidate's raw SHA-256 before another formal run.

After the new solver approval and before the new formal run, the current three-file formal directory
must be renamed without rewriting bytes to:

```text
output/stage_03_1_q1_q2_coupling_c2_1_rejected
```

The source must contain exactly the three C2.1 files and hashes recorded above, no approval, temporary
file, or subdirectory. The target must not exist. Source and target have the same `output` parent and
the operation must be one non-overwriting directory rename. Copy/delete, merge, nesting, replacement,
or mutation of either historical archive is forbidden. Failed preconditions leave the source unchanged
and block the run.

After successful archive verification, the newly approved source may execute the formal runner exactly
once. The resulting computational gate must be independently reviewed. No result may receive final
acceptance merely because this implementation error is closed.

Stage 4 remains blocked until an independent, hash-bound Stage 3.1 final acceptance approval exists.
