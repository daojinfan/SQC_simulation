# Stage 4.0 Frozen Upstream Receipt Remediation

## Status

Proposed for independent design review. This record does not approve implementation, the existing Stage
4.0 exact-three candidate, or the Stage 4 compiler.

## Context

The approved Stage 4.0 notebook remediation requires ordinary imports and read-only validation paths not to
import `nbclient`. D0.2 removed the direct eager imports from `sqvm.control` and made the two root Spectrum
exports lazy. Fresh `import sqvm`, `import sqvm.control`, and `import sqvm.control.compatibility` then worked
without `nbclient`.

Independent T0.2 nevertheless found that `validate_control_channel_compatibility` still imported
`sqvm.spectrum.provenance` and the Stage 3.1 acceptance validator at its read-only validation boundary.
Python initializes `sqvm.spectrum.__init__` before either submodule, and that accepted package imports the
Stage 3.1 notebook writer, whose historical implementation imports `nbclient.NotebookClient` at module load.
The missing-dependency interpreter therefore failed before the Stage 4 notebook-runtime preflight.

Changing any file under `src/sqvm/spectrum` would change the accepted Stage 3.1 source-tree digest and would
force new solver validation, formal analysis, independent approval, and dependent Stage 4.0 candidate
generation even though no physics, solver, spectrum, or accepted upstream byte is wrong. Loading Spectrum
source with import hooks, fake modules, `sys.modules` mutation, or direct file loaders would instead create an
unreviewable bypass. Neither response is appropriate for this metadata-only gate.

## Decision

### 1. Stage 4.0 uses a frozen upstream receipt validator

Stage 4.0 replaces its calls into the importable `sqvm.spectrum` package with a Stage 4-owned, read-only
validator:

```python
validate_frozen_stage4_upstream_receipt(
    *,
    repository_root: str | Path,
    stage2_artifact_path: str | Path,
    stage2_1_manifest_path: str | Path,
    stage2_1_approval_path: str | Path,
    stage3_1_artifact_path: str | Path,
    stage3_1_report_path: str | Path,
    stage3_1_approval_path: str | Path,
) -> FrozenUpstreamReceiptReport
```

`FrozenUpstreamReceiptReport` is a Stage 4-owned immutable model with exact fields:

```text
ok
stage2_1_ready
stage3_1_ready
current_hashes
errors
```

`ok`, `stage2_1_ready`, and `stage3_1_ready` are booleans. `current_hashes` is the exact 26-key mapping listed
in section 2; its values are uppercase raw-byte SHA-256 or the empty string when the current file cannot be
read. `errors` is an ordered tuple of deterministic strings.

The function raises `TypeError` only when `repository_root` or a path argument is neither `str` nor `Path`.
Every evidence or validation failure, including a missing file, invalid path, I/O failure, parse failure,
schema failure, or binding mismatch, returns a report with `ok=false`, `stage2_1_ready=false`, and
`stage3_1_ready=false`. Errors are accumulated in the section-2 key order, then Stage 2 structural-check
order, then Stage 3 structural-check order. It never returns a partially ready report and never raises a
validation exception.

The implementation belongs in `src/sqvm/control/upstream.py`. It may import only Python standard-library
modules and existing helpers under `sqvm.hamiltonian` or `sqvm.control`. It must not import any
`sqvm.spectrum` module, use an import hook or direct source-file loader, modify `sys.modules`, spawn a
Spectrum-validation subprocess, or duplicate notebook execution.

This validator is not a new acceptance decision. It independently replays the already accepted, immutable
upstream receipt from current bytes. The existing Stage 2.1 and Stage 3.1 validators remain authoritative for
their stages and are not modified.

### 2. Normative current-byte identities

The following repository-relative POSIX paths and raw SHA-256 values are exact and form the Stage 4.0 frozen
upstream receipt. The `current_hashes` mapping has exactly these keys and no others:

```text
stage2_artifact:
  path: output/stage_02_hamiltonian/hamiltonian_artifacts.json
  sha256: DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66
stage2_1_manifest:
  path: output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json
  sha256: 4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D
stage2_1_approval:
  path: output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json
  sha256: CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9
stage3_1_artifact:
  path: output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json
  sha256: 76C539FF22EAB54E5E10C93526E20AFB68A39507BCD9BE2DAD5A2897282FAD35
stage3_1_notebook:
  path: output/stage_03_1_q1_q2_coupling/verification.ipynb
  sha256: 444DEEE581BA9B2EE1CC90E0D8291E3E0837BC7D407F14284F821A8C9927D441
stage3_1_report:
  path: output/stage_03_1_q1_q2_coupling/verification_report.json
  sha256: F5F82FB7D9770CFE6ADEE6639A67C46F781891FF8F94F5247A54F4722655F3CD
stage3_1_approval:
  path: output/stage_03_1_q1_q2_coupling/acceptance_approval.json
  sha256: 5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D
```

The explicit arguments for the six public paths must resolve to the exact paths above. The Stage 3.1
notebook path is the sibling of the explicit artifact path. Missing, extra, redirected, symlink-escaped, or
out-of-repository paths fail. Raw hashes are recomputed from current bytes; a caller cannot supply a hash.

The validator also reloads and binds these supporting current bytes:

```text
configs/devices/2q1c2r.yaml:
  CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F
output/stage_01_device_model/device_artifacts.json:
  B771B72ED33FE104E8940730684710B23BB9278451230318D2B52543C77CE86D
configs/hamiltonians/2q1c_charge_basis.yaml:
  B65D1C57083D26F3C0CE1B0F980B07B685F4F21BCBD955DCD73D933416D84E5E
output/stage_02_1_hamiltonian_rebaseline/legacy_baseline_anchor.json:
  FE489B2476FA3AE3121BEBB1FA06BF5EF1B74DEDAD4546458C7687EA7431C88D
output/stage_02_1_hamiltonian_rebaseline/previous_hamiltonian_artifacts.json:
  222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C
configs/spectra/2q1c_q1q2_coupling.yaml:
  B2AFD3FA9FF9EA35153DEBF8B5E6C77D0A3E7C490270CEB6C89AC0D317594F2A
docs/decisions/2026-07-11-stage3-1-design-freeze.json:
  AB7642A9C170E98B4319D1D453A5B4962FA50C3236F9DABA165E27EF7C9F7F32
output/stage_03_1_solver_validation/eigsh_validation.json:
  6D58A5D77988978E7B9377EC06841D9AD2F31D296D358C8874F7DF5C9EA2EE0D
output/stage_03_1_solver_validation/eigsh_validation_approval.json:
  093EFA68E396152A7F2BF82A412320F56944C2276752D7B289A701BB2CC154FA
docs/decisions/2026-07-11-stage3-1-final-acceptance-review.md:
  A01A20459C03602A0BA640D17AB7B7DB989F23FAE1B9972ED47B3FB401B274A0
docs/decisions/2026-07-11-stage2-legacy-anchor-acceptance.md:
  E85CD03A8FA4EF449C6C038B1CDDAD21E4321003C328057E0911069B398B307D
docs/decisions/2026-07-11-stage3-1-design-freeze-review.md:
  7EB52CECF32B2692AEBECAECCFEEE0AA249B6D075E756F1B697AEF5E95B2D64D
docs/decisions/2026-07-11-stage3-objective-correction.md:
  95A45E9E3205241265386FC9F7BDF8BB0DDB043E6A9D33D5714D7C68964BD795
docs/designs/03_1_q1_q2_coupling_sweep_design.md:
  A2C9E8E28F778813818A5B76C817C54EB459D848E85678F5850FAB1451123DE5
docs/stages/03_1_q1_q2_coupling_sweep_plan.md:
  5878A186A6A0673FC8FA07747D7822D022667895B9C04D78D892933A8C166F7C
docs/decisions/2026-07-11-stage3-1-solver-validation-c2-2-review.md:
  86D8AA27DB5AA1EA4A7BFF38C40C80FBB0EA40C68EEB31F581C9197F5DE93187
output/stage_03_1_solver_validation/dense_pilot.json:
  AFE2A8B6F531468360BB60B535D86B216652977C6685C9A6B1DDE4E7237DC916
docs/decisions/2026-07-11-stage3-1-c2-remediation.md:
  9CA7FFA870DA6F83581D04DE067D5F618E388BAEC72162E0CBBF752487158C04
docs/decisions/2026-07-11-stage3-1-c2-2-fixed-refinement-remediation.md:
  E356551C706F3C2372620311D69D8A7C4FF1E72825680D43C818D53B0746BB7C
```

`current_hashes` has exactly these 26 keys. Mapping order is not semantically significant, but error
collection and test enumeration use this order:

```text
stage2_artifact
stage2_1_manifest
stage2_1_approval
stage3_1_artifact
stage3_1_notebook
stage3_1_report
stage3_1_approval
device_config
device_artifact
hamiltonian_config
legacy_anchor
previous_stage2_artifact
stage3_1_config
stage3_1_design_freeze_manifest
stage3_1_solver_validation
stage3_1_solver_validation_approval
stage3_1_final_review
legacy_anchor_acceptance_record
stage3_1_design_freeze_review
stage3_1_objective_decision
stage3_1_detailed_design
stage3_1_plan
stage3_1_solver_validation_review
stage3_1_dense_pilot
stage3_1_c2_remediation
stage3_1_c2_2_remediation
```

### 3. Canonical and schema checks

Every section-2 path ending in `.json` must be loaded from UTF-8 raw bytes and reject duplicate JSON keys and
non-finite values. All such files must equal `canonical_json_bytes(parsed_mapping)` byte-for-byte except the
following two immutable legacy raw-byte inputs:

```text
output/stage_01_device_model/device_artifacts.json
output/stage_02_1_hamiltonian_rebaseline/previous_hamiltonian_artifacts.json
```

Those two files are explicitly never rewritten or canonicalized. Each must still have a mapping root, pass
its frozen structural checks, match its fixed section-2 raw hash, and match every owning provenance,
manifest, anchor, and approval binding. No other `.json` path is exempt. Fixed raw hashes do not replace
structural validation.

`output/stage_03_1_q1_q2_coupling/verification.ipynb` is explicitly not subject to
`canonical_json_bytes` equality and is never rewritten. It is parsed as strict UTF-8 JSON with duplicate-key
and non-finite rejection, has an exact mapping root with keys `cells`, `metadata`, `nbformat`, and
`nbformat_minor`, uses nbformat `4.5`, and has exact notebook metadata keys `language_info` and
`stage3_1_read_only` with `stage3_1_read_only=true`. It contains exactly 18 cells and exactly nine code
cells; code execution counts are exactly `1..9` in order and no output has `output_type=error`. Its fixed raw
hash must match `stage3_1_notebook`, the Stage 3.1 report must bind that same raw hash, and the final approval
must bind that same raw hash. The fixed raw hash owns the exact cells, IDs, sources, metadata, and non-error
outputs; the receipt validator does not execute the historical notebook.

Stage 2.1 is ready only when all of these hold:

1. The Stage 2 artifact identity is exactly schema `0.2`, type `stage_02_hamiltonian`, artifact version
   `0.2`. Its `provenance` mapping has exact keys `device_artifacts_sha256`,
   `hamiltonian_config_sha256`, and `stage2_model_source_tree_sha256` and exact values
   `B771B7...E86D`, `B65D1C...4E5E`, and `0F50DB...E972`.
2. The current Stage 2 source-tree digest is recomputed with the existing
   `stage2_model_source_tree_sha256` helper and equals
   `0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972`.
3. The manifest identity is exactly schema `0.1`, type `stage_02_1_hamiltonian_rebaseline`, artifact
   version `0.1`, and candidate Stage 2 artifact version `0.2`. Its paths resolve to the supporting files in
   section 2. Its `sha256` mapping binds the current device artifact, device config, Hamiltonian config,
   legacy anchor, Stage 2 artifact, historical Stage 2 CLI value, and current Stage 2 source digest. Its
   top-level previous and anchor hashes equal the section 2 values.
4. The approval has its existing exact field set, schema `0.1`, type
   `stage_02_1_hamiltonian_rebaseline_approval`, artifact version `0.1`, `decision=approved`,
   `reviewer_role=independent_test_review_ai`, and `blocking_findings=[]`. It binds the current manifest,
   Stage 2 artifact, anchor, and previous artifact hashes.
5. The legacy anchor has its existing exact field set, schema `0.1`, type
   `stage_02_legacy_baseline_anchor`, artifact version `0.1`, `decision=accepted`, `approved_by=user`, and
   equal expected/previous hashes. Its `acceptance_record_path` is exactly
   `docs/decisions/2026-07-11-stage2-legacy-anchor-acceptance.md`, and its acceptance-record hash equals the
   current receipt hash. The archived previous artifact raw hash equals both anchor hashes.

Stage 3.1 is ready only when Stage 2.1 is ready and all of these hold:

1. The artifact identity is exactly schema `0.1`, type `stage_03_1_q1_q2_coupling`, artifact version
   `0.1`; `acceptance_eligible=true`; its computational gate has `computational_ready=true`,
   `status=ready_for_stage4`, and no blockers. Its provenance has `ok=true`, `all_matches=true`, no errors,
   `approval_decision=approved`, dense-gap consistency `ok=true` with 12 gaps and zero maximum difference,
   and exact current Stage 2.1 manifest, approval, artifact, device, Hamiltonian config, and source hashes.
2. The report identity is exactly schema `0.1`, type
   `stage_03_1_q1_q2_coupling_verification_report`, artifact version `0.1`;
   `execution_succeeded=true`, `ok=true`, `acceptance_candidate_ready=true`; its computational gate and
   checks exactly equal the artifact, its post-write checks all pass, it binds the current artifact and
   notebook hashes, and its final recorded paths resolve to the current siblings.
3. The Stage 3.1 source-tree digest is recomputed without importing Spectrum. It uses the existing framing:
   every `src/sqvm/spectrum/**/*.py` file, sorted by repository-relative POSIX path UTF-8 bytes, contributes
   `path_utf8 + NUL + decimal_raw_byte_length_ascii + NUL + raw_bytes`. The uppercase digest must equal
   `263125AAF4CBEE1572E254A41916F8E0B519BEDB6DA80956F8789AA5422A276E`.
4. The Stage 3.1 design-freeze manifest has its existing exact field set, schema `0.1`, type
   `stage_03_1_design_freeze`, artifact version `0.1`, `decision=approved`,
   `reviewer_role=independent_design_review_ai`, and `blocking_findings=[]`. Its review path/hash equals the
   current `stage3_1_design_freeze_review` receipt input. Its `document_sha256` mapping is exactly:

   ```text
   docs/decisions/2026-07-11-stage3-objective-correction.md:
     95A45E9E3205241265386FC9F7BDF8BB0DDB043E6A9D33D5714D7C68964BD795
   docs/designs/03_1_q1_q2_coupling_sweep_design.md:
     A2C9E8E28F778813818A5B76C817C54EB459D848E85678F5850FAB1451123DE5
   docs/stages/03_1_q1_q2_coupling_sweep_plan.md:
     5878A186A6A0673FC8FA07747D7822D022667895B9C04D78D892933A8C166F7C
   ```

   Each mapped path and the review path resolve inside the repository and match current raw bytes.
5. The fixed solver candidate and approval raw hashes are independently approved receipt trust anchors. The
   receipt does not recompute eigensystems, projectors, timings, environment fingerprints, or the 44
   numerical cases. It does perform the following non-numerical structural replay:

   - candidate root keys are exactly `schema_version`, `artifact_type`, `artifact_version`, `profile`,
     `acceptance_eligible`, `validation_passed`, `coverage_complete`, `failed_cases`, `bindings`,
     `eigsh_spec`, `dense_spec`, `thresholds`, `normative_vector`, `cutoff_signatures`, `flux_vectors`,
     `validation_cases`, `aggregates`, `p50_seconds_by_dimension`, and `p95_seconds_by_dimension`;
   - identity is schema/artifact version `0.1`, type `stage_03_1_solver_backend_validation`, profile
     `solver_validation`, `acceptance_eligible=false`, `validation_passed=true`, `coverage_complete=true`,
     and `failed_cases=[]`;
   - `bindings` is exactly:

     ```text
     config_sha256: B2AFD3FA9FF9EA35153DEBF8B5E6C77D0A3E7C490270CEB6C89AC0D317594F2A
     dense_pilot_sha256: AFE2A8B6F531468360BB60B535D86B216652977C6685C9A6B1DDE4E7237DC916
     design_freeze_manifest_sha256: AB7642A9C170E98B4319D1D453A5B4962FA50C3236F9DABA165E27EF7C9F7F32
     environment_fingerprint_sha256: 40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12
     hamiltonian_config_sha256: B65D1C57083D26F3C0CE1B0F980B07B685F4F21BCBD955DCD73D933416D84E5E
     stage2_artifacts_sha256: DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66
     stage2_model_source_tree_sha256: 0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972
     stage2_rebaseline_approval_sha256: CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9
     stage2_rebaseline_manifest_sha256: 4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D
     stage3_1_c2_2_fixed_refinement_remediation_sha256: E356551C706F3C2372620311D69D8A7C4FF1E72825680D43C818D53B0746BB7C
     stage3_1_c2_remediation_sha256: 9CA7FFA870DA6F83581D04DE067D5F618E388BAEC72162E0CBBF752487158C04
     stage3_1_source_tree_sha256: 263125AAF4CBEE1572E254A41916F8E0B519BEDB6DA80956F8789AA5422A276E
     ```
   - the normative vector is exactly encoding `sha256_counter_v2`, seed length 224, seed hash
     `48245D6E13E15E65DA68E7AD4E957E07F97F0C1DF6E182E1806D6B1F88027CA0`, block-zero hash
     `44C000CA15E6923769C51CCC19623DAB311213490C1E50225D7E0AC79A33E322`, and `passed=true`;
   - cutoff signatures are exactly ordered as `baseline:[7,7,7]:3375`,
     `refined_q1:[9,7,7]:4275`, `refined_c:[7,9,7]:4275`, and
     `refined_q2:[7,7,9]:4275`; flux vectors are exactly ordered and mapped as:

     ```text
     idle:       q1=0.100000000000,c=0.270000000000,q2=0.000000000000
     c020_left:  q1=0.100000000000,c=0.200000000000,q2=0.080000000000
     c020_mid:   q1=0.100000000000,c=0.200000000000,q2=0.100000000000
     c020_right: q1=0.100000000000,c=0.200000000000,q2=0.120000000000
     c027_left:  q1=0.100000000000,c=0.270000000000,q2=0.080000000000
     c027_mid:   q1=0.100000000000,c=0.270000000000,q2=0.100000000000
     c027_right: q1=0.100000000000,c=0.270000000000,q2=0.120000000000
     c0385_left: q1=0.100000000000,c=0.385000000000,q2=0.080000000000
     c0385_mid:  q1=0.100000000000,c=0.385000000000,q2=0.100000000000
     c0385_right:q1=0.100000000000,c=0.385000000000,q2=0.120000000000
     c0396_mid:  q1=0.100000000000,c=0.396000000000,q2=0.100000000000
     ```

     The 44 case IDs are their signature-major Cartesian product, are unique, and every row has
     `passed=true` and `physics_checks.passed=true`;
   - `aggregates` has exact keys `max_gap_error_GHz`, `max_repeat_gap_error_GHz`,
     `max_projector_dense_error`, `max_projector_repeat_error`, `max_participation_dense_error`,
     `max_participation_repeat_error`, `max_metric_dense_error_GHz`, and
     `max_q1_q2_splitting_error_GHz`, each finite and nonnegative; p50/p95 mappings have exact keys `3375`
     and `4275`, finite positive values, and p95 is not below p50;
   - solver approval root keys are exactly `schema_version`, `artifact_type`, `artifact_version`, `decision`,
     `reviewer_role`, `blocking_findings`, `validation_artifact_sha256`, `review_record_path`, and
     `review_record_sha256`; identity is schema/artifact version `0.1`, type
     `stage_03_1_solver_backend_validation_approval`, `decision=approved`,
     `reviewer_role=independent_test_review_ai`, and `blocking_findings=[]`; candidate, review path, and review
     hash equal the current receipt inputs.

   The environment hash is historical solver evidence inside the immutable, independently approved
   candidate rather than a Stage 4 runtime claim. Stage 4 separately fingerprints its current execution
   environment, including `nbclient==0.10.2`, before formal compilation.
6. The final approval has the existing exact 16-field set, schema `0.1`, type
   `stage_03_1_q1_q2_coupling_acceptance_approval`, artifact version `0.1`, `decision=approved`,
   `reviewer_role=independent_test_review_ai`, and `blocking_findings=[]`. It binds the current artifact,
   notebook, report, final review, design-freeze manifest, config, recomputed source tree, solver candidate,
   and solver approval hashes exactly.

Any unknown, missing, extra, mistyped, boolean-as-number, malformed hash, mapping/list mismatch, failed flag,
or binding mismatch fails closed. Fixed hashes and equality relationships are both required.

### 4. Existing Stage 4.0 artifact bytes remain stable

`validate_control_channel_compatibility` consumes the frozen upstream receipt report. It emits the same two
ordered checks and exact messages as D0:

```text
stage2_1_approval_valid: Stage 2.1 manifest and approval validate from current bytes
stage3_1_readiness_valid: Stage 3.1 readiness approval validates from current bytes
```

No path, SHA-256 value, check order, check message, readiness flag, manifest field, notebook cell, or report
field changes. The existing Stage 4.0 exact-three candidate must remain byte-for-byte:

```text
control_channel_manifest.json:
  1BE614301C7EA74025602CF8C1B9E11576CC7C43F50BD9B6782B985575E34E1D
verification.ipynb:
  A64DA8BDCB158C96ED7AE35B5FA70B7A14659224AFAA0BA96875A50D9A61E78A
verification_report.json:
  9DE8ECE5528881289A2BD1A227C93506D70A6EA6DCAECBCDF054D99A978E4BC2
```

The Stage 4.0 independent review must cite this remediation and the earlier notebook remediation. The exact
Stage 4.0 approval schema remains unchanged; its review-record hash transitively binds both decisions and the
reviewed implementation hashes. The candidate is not regenerated.

### 5. Dependency boundary

After implementation:

- ordinary imports and `validate_control_channel_compatibility` must succeed in the recorded missing-
  `nbclient` interpreter without loading `nbclient`;
- no production file under `src/sqvm/control` may import `sqvm.spectrum`, at module scope or function scope;
- the Stage 4.0 notebook generator and notebook validator remain the only Stage 4.0 paths that call the
  exact `nbclient==0.10.2`, host, and KernelSpec preflight;
- Stage 4 formal compilation continues to require and record `nbclient==0.10.2` before notebook execution;
- `pyproject.toml`, all Spectrum files, and all accepted upstream artifacts remain unchanged.

## Required Tests

1. Fresh ordinary imports and read-only compatibility validation pass in both the approved interpreter and
   the missing-`nbclient` interpreter with `nbclient` absent from `sys.modules` before and after validation.
2. Static import inspection proves no `src/sqvm/control` production module imports `sqvm.spectrum`.
3. Every normative path and raw hash in section 2 has a positive test. Each of the 26 receipt hashes has one
   parameterized tamper that fails while `current_hashes` retains its exact keys and records the changed or
   empty current value.
4. Stage 2 artifact provenance, Stage 2.1 manifest path/hash, approval identity/reviewer/blockers, source-tree
   digest, anchor, and previous-artifact attacks fail. A self-consistent but unanchored Stage 2 trio fails.
5. Stage 3.1 artifact readiness/provenance, report cross-consistency, source-tree digest, freeze document and
   review graph, solver candidate identity/coverage/bindings, solver approval/review graph, final approval
   identity/reviewer/blockers, and every final approval binding have representative failures.
6. Duplicate JSON keys, noncanonical bytes, NaN/Infinity, missing/extra fields, path redirection, and
   malformed hashes fail closed. For the two explicit legacy JSON exceptions, changed raw bytes,
   duplicate keys, non-finite values, non-mapping roots, schema failures, and binding mismatches fail, while
   their unchanged frozen noncanonical bytes pass.
7. The unchanged Stage 3.1 notebook raw hash
   `444DEEE581BA9B2EE1CC90E0D8291E3E0837BC7D407F14284F821A8C9927D441` passes its notebook-specific rule
   without canonical rewriting. A copied notebook with one changed byte or a synchronized in-memory
   structure but changed raw hash fails.
8. The unchanged Stage 1 device artifact and archived previous Stage 2 artifact pass their explicit legacy
   raw-byte rules. A tamper to either file fails even when the modified JSON remains parseable.
9. The prior notebook exact-template/replay attacks, environment preflight tests, approval exact-three/four
   lifecycle, and six approval-bound hashes do not regress.
10. Focused tests, one safe full regression, external-cache compileall, and diff-check pass. The exact-three,
   `pyproject.toml`, Spectrum source tree, frozen documents, and all upstream bytes remain unchanged.

Independent T0.3 should rerun one representative attack per risk family rather than duplicating the full
parameterized developer matrix.

## Authorization Boundary

After independent approval of this record, development may modify only:

- `src/sqvm/control/compatibility.py`;
- new `src/sqvm/control/upstream.py`;
- `tests/test_control_channel_compatibility.py`.

The existing D0.2 lazy-root change in `src/sqvm/__init__.py` remains in place but is not modified again.
`src/sqvm/control/artifacts.py` and the existing exact-three remain unchanged.

Development may not modify `pyproject.toml`, any `src/sqvm/spectrum` file, the channel registry, frozen design
documents, accepted upstream artifacts, or Stage 4.0 output. It may not create a review or approval, run the
Stage 4 compiler, change physics/numerics/thresholds, or commit/push.
