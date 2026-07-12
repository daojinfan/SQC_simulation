# Stage 4 D1 acceptance remediation

Date: 2026-07-12

Status: proposed for independent design review

## 1. Scope and classification

The first Stage 4 formal candidate is rejected as an acceptance candidate because independent testing found
two implementation defects:

1. the frozen fail-closed acceptance approval builder is absent; and
2. persisted delivered signals are not independently bound to the reconstructed-DAC FIR/latency chain.

The formal numerical results remain useful development evidence, but they cannot be approved. Stage 5 remains
blocked. This remediation does not change the frozen physical model, control matrices, channel registry, pulse
definitions, schedules, sampling rate, DAC model, FIR taps, latency values, thresholds, scenario order, status
priority, runtime budgets, or Stage 4/Stage 5 boundary.

The authoritative frozen contracts remain:

- `docs/designs/04_control_signal_design.md`, SHA-256
  `56F5ED6CA1A10634F4D2D9D87507C056E5FC26CF9F875D30D1BC879C52FFA2DB`;
- `docs/stages/04_control_signal_plan.md`, SHA-256
  `166DB399B0F875C435B9E635CDC894A83447487D422371C36B7D942280640659`; and
- `docs/decisions/2026-07-11-stage4-design-freeze.json`, SHA-256
  `99D2DFA48582DC9DBE792AD7157BD71CCC9E0610ADEEEE62791FF6E3EF52FBCD`.

## 2. Rejected candidate identities

The rejected formal directory is `output/stage_04_control_signal` and contains exactly:

| File | SHA-256 |
| --- | --- |
| `control_signal_artifacts.json` | `E8357781D730ACB03D30DCE4801E968CF01855B1F38F18113631B640420566A8` |
| `verification.ipynb` | `1EF5D170BCDFD5315C99145C23D638FADB81491EC747F18F6395E2DD6D03B4CB` |
| `verification_report.json` | `54F400DAE5585C4E651919BADAAE014941927E741703C6E615C5E8880CDCC82F` |
| `run_receipt.json` | `01BD2E93B532197AF2DE8C7458D5D7D1F0F0DF6FB42D5CD6FB8F586BCF72CD1C` |

It contains no `acceptance_approval.json`, temporary file, or staging directory.

The associated smoke directory is `output/stage_04_control_signal_smoke` and contains exactly:

| File | SHA-256 |
| --- | --- |
| `control_signal_artifacts.json` | `A911913137C65182CD6023B020BD71C6883FB0967034DBA1C38057F6A73985C1` |
| `verification.ipynb` | `5A1E6EB74B4477362C80CBB3EB784B57FE81964A919130B8EBB20561E5110315` |
| `verification_report.json` | `464D0B15C0C35E95C5DA5296185974982BE9439FB85D0AC569BA85740EC5692D` |
| `run_receipt.json` | `EBB80DE633E10EA6917995F5B5A87D598D9C31101171CEC249F5FCC1F0F265EB` |

## 3. Required implementation changes

### 3.1 Fail-closed approval builder

Add and publicly export this exact interface:

```python
build_stage4_acceptance_approval(
    *,
    artifact_path: str | Path,
    notebook_path: str | Path,
    report_path: str | Path,
    run_receipt_path: str | Path,
    review_record_path: str | Path,
    repository_root: str | Path | None = None,
) -> dict[str, Any]
```

The builder is pure with respect to the filesystem: it reads current bytes and returns the exact approval
payload, but does not create or modify the approval file. It must fail before returning a payload unless all
of the following are true:

1. the four candidate paths resolve to the same directory;
2. that directory contains exactly the frozen development four-file set and no approval, temporary, partial,
   or staging entry;
3. artifact, notebook, report, and receipt pass the same strict schema, canonical/finite, notebook replay,
   current-provenance, cross-consistency, and recomputation checks used by the final validator;
4. `_formal_candidate_ready(...)` is true, including formal profile, acceptance eligibility, exact formal
   scenarios, computational readiness, successful publication, runtime readiness, and no blockers;
5. the review path resolves inside the repository, is a regular file, is non-empty, and its current raw hash is
   used in the payload;
6. all eleven `APPROVAL_HASH_FIELDS` are recomputed from current bytes; and
7. the returned mapping has exactly `APPROVAL_KEYS`, with `schema_version="0.1"`,
   `artifact_type="stage_04_control_signal_acceptance_approval"`, `artifact_version="0.1"`,
   `decision="approved"`, `reviewer_role="independent_test_review_ai"`, and `blocking_findings=[]`.

The implementation must share a single internal candidate-validation path with
`validate_stage4_acceptance_approval`; builder and validator may not drift into different evidence rules.
Smoke, stale source/config/schedule/provenance, altered arrays or metrics, missing evidence, a non-exact file
set, an existing approval, or a review outside the repository must fail closed.

The builder must be exported from `sqvm.control` and the lazy root `sqvm` API without introducing an eager
`nbclient` import on ordinary package import.

### 3.2 Reconstructed-DAC delivered-signal binding

For each scenario and each lane in the frozen lane order, validation must independently recompute the complete
published signal path from persisted `reconstructed_V`:

1. direct full FIR convolution using the frozen lane taps;
2. the frozen integer latency prefix after FIR;
3. placement on the frozen common physical index range;
4. right zero-padding to the exact common `P`;
5. the XY and readout effective arrays by their frozen static forward matrices;
6. the Z delta-flux array at every common index `p` as
   `expected_delta_z[p] = A_z @ expected_delivered_z[p]`; and
7. the persisted Z absolute-flux array as
   `expected_absolute_z[p] = expected_delta_z[p] + [idle_q1, idle_q2, idle_c]`, using the exact configured
   `idle_flux_phi0` values in the frozen Z coordinate order.

The validator must compare every independently recomputed delivered lane sample with persisted
`delivered_after_fir_latency_V`. The absolute per-sample tolerance is `1e-15 V`; missing, extra, non-finite,
misordered, or out-of-tolerance values fail closed. Persisted XY and readout effective arrays must then be
compared against the independently recomputed direct forward-matrix outputs. Persisted
`effective.absolute_flux_phi0` must be compared against `expected_absolute_z`, not against Z delta flux and
not against an expression that consumes persisted delivered arrays. The unchanged frozen forward tolerances
apply to effective-coordinate comparisons.

Each `forward_reference_rows.max_abs_error` is the actual maximum absolute error between the independently
recomputed expected effective coordinate and the persisted effective coordinate. For Z rows the expected
coordinate is `expected_absolute_z`, including idle flux. It must not be hard-coded to zero. Its `passed` value
is recomputed from that error and the unchanged frozen tolerance. The root forward aggregate and computational
gate consume these recomputed row results.

Independent DAC-code equality remains unchanged and continues to be recomputed from persisted `requested_V`.
The new delivered-signal comparison is an additional required link; it does not replace code-array validation.

## 4. Required tests

Focused tests must include at least:

1. a positive formal exact-four builder result with exact keys and current raw hashes;
2. builder rejection for smoke, stale source/config/schedule/provenance, missing/extra file, existing approval,
   missing review, review outside the repository, and non-ready receipt;
3. final approval positive validation using only the builder payload plus `canonical_json_bytes`;
4. replacement of all hand-built positive approval payloads with the builder;
5. one persisted DAC code changed to another valid in-range integer, rejected by both builder and validator;
6. one XY or readout delivered lane sample changed by `1e-9 V`, with effective arrays, local/global metrics,
   checks, report, receipt, and approval hashes updated consistently, rejected by both builder and validator;
7. one Z delivered lane sample changed by `1e-9 V`, with Z delta and absolute effective arrays, local/global
   metrics, checks, report, receipt, and approval hashes updated consistently, rejected by both builder and
   validator;
8. malformed, missing, extra, reordered, non-finite, or wrong-length delivered/effective arrays rejected;
9. actual non-zero `forward_reference_rows.max_abs_error` recomputation and boolean/message tamper rejection;
10. the formal positive candidate still reproduces all published metrics and all seventeen checks; and
11. ordinary `import sqvm`, `import sqvm.control`, and read-only compatibility validation do not load
    `nbclient`.

Run focused Stage 4 tests, the safe full repository suite excluding only production-output runners,
external-cache `compileall`, and `git diff --check`. Tests and temporary attacks must not rewrite either
production output directory.

## 5. Implementation authorization

Before the controlled archive and production-run step, development may modify exactly:

- `src/sqvm/control/stage4_artifacts.py`;
- `src/sqvm/control/__init__.py`, only to export the new builder;
- `src/sqvm/__init__.py`, only to expose the same builder while preserving lazy imports; and
- `tests/test_control_signal.py`.

No other source, test, script, config, schedule, design document, or output file is authorized in the
implementation and non-production-test phase. In particular, changes to `stage4_compile.py`,
`stage4_models.py`, `stage4_config.py`, `stage4_provenance.py`, `stage4_verify.py`, the formal or smoke runner,
the control registry, any YAML file, the frozen design or plan, upstream accepted artifacts, or either current
Stage 4 output directory are prohibited. Development may not create a review or approval.

The only later output mutations authorized by this record are the archive renames and the one smoke/one formal
run in sections 7 and 8, after implementation, non-production tests, hash protection, and every archive
precondition pass.

## 6. Source and provenance consequences

Any change under `src/sqvm/control` changes the Stage 4 source-tree digest. Therefore the rejected formal and
smoke candidates cannot be approved under the repaired source. Their bytes remain immutable historical
evidence. No approval may be created for either rejected directory.

The repaired source does not alter Stage 1, Stage 2, Stage 2.1, Stage 3, Stage 3.1, the Stage 4.0 registry or
approval, the frozen Stage 4 config values, or the frozen schedules. A physics rebaseline is not required.

## 7. Non-overwrite archive lifecycle

Before any repaired-source production smoke or formal run:

1. `output/stage_04_control_signal_d1_rejected` and
   `output/stage_04_control_signal_smoke_d1_rejected` must both be absent;
2. each source directory must exist, contain exactly the four files and hashes in section 2, and contain no
   approval, temporary, partial, or staging entry;
3. each source and target must resolve beneath the same `output` parent and on the same filesystem; and
4. both preflight checks must complete before either source is moved.

The deterministic rename order is:

1. `output/stage_04_control_signal_smoke` to
   `output/stage_04_control_signal_smoke_d1_rejected`; then
2. `output/stage_04_control_signal` to `output/stage_04_control_signal_d1_rejected`.

Each rename is one non-overwriting atomic directory rename. The pair is not claimed to be one atomic
transaction. Copy/delete, merge, nesting, overwrite, byte rewriting, and automatic rollback are forbidden. If
the first rename fails, both source directories remain live and no repaired-source production run is
authorized. If the first rename succeeds but the second fails, development must stop, preserve and report the
one-archived/one-live state, and perform no production run.

Recovery from that partial state requires independent read-only reinspection. The idempotent resume may perform
only the missing second rename, and only after it revalidates that:

- the smoke archive exists with exactly its four section-2 files and hashes and no approval, temporary, partial,
  or staging entry;
- the smoke source is absent;
- the formal source still exists with exactly its four section-2 files and hashes and no approval, temporary,
  partial, or staging entry;
- the formal archive target is absent; and
- source and target remain beneath the same `output` parent and on the same filesystem.

Any other partial state is not resumable under this record and requires a new independent decision. After both
renames, each archive must contain exactly its original four files and hashes, both original paths must be
absent, and neither archive ever receives an approval.

## 8. Controlled rerun and final acceptance

After implementation and all non-production tests pass, development may:

1. complete the archive lifecycle in section 7;
2. execute the repaired-source smoke runner exactly once and verify the unchanged non-acceptance contract;
3. freeze and report the repaired Stage 4 source/config/schedule/environment identities; and
4. if smoke and every preflight pass, execute the repaired-source formal runner exactly once.

There is no automatic fallback, tuning, or second run. An implementation exception or failed computational
gate is preserved and returned for independent review.

The repaired formal directory must again be exact-four before review. Independent final testing must review
the repaired implementation, rerun risk-scaled non-production tests, independently recompute DAC, FIR,
latency, delivered/effective arrays and metrics, exercise the builder and representative synchronized attacks,
and verify current provenance. Only a zero-finding independent test may create the review record, call
`build_stage4_acceptance_approval`, write its returned payload with `canonical_json_bytes`, and require
`validate_stage4_acceptance_approval` to return `ok=true`, `stage5_ready=true`,
`acceptance_approval_valid=true`, and no blockers.

Stage 5 remains blocked until that exact-five approval succeeds.
