# Stage 4.0 Plan: Control-Channel Compatibility Gate

## Purpose

Create an approved executable control-channel registry without modifying or pretending to replace the
accepted device/Hamiltonian/spectrum physics chain.

The gate closes the explicit Stage 3.1 limitation that the current Stage 1 device configuration has no
`q2_z` channel.

## References

1. `docs/designs/03_1_q1_q2_coupling_sweep_design.md`
   - Stage 4 may not claim an executable q2-flux path while `q2_z` is absent.
2. `docs/10_development_process.md`
   - Upstream changes must be content-hash bound and fail closed.
3. Stage 1 device design and artifact
   - Existing channel names, kinds, targets, and ports are the immutable base registry.

## Scope

Included:

```text
strict Stage 4 channel-registry schema
exact preservation of the five Stage 1 channels
addition of q1_z and q2_z
unique AWG-lane assignment
canonical merge result
read-only validation of accepted Stage 1, Stage 2.1, and Stage 3.1 trust chains
canonical manifest, executed notebook, independent review, and approval
```

Excluded:

```text
device component or physics changes
modification of configs/devices/2q1c2r.yaml
regeneration or overwrite of accepted Stage 1/2/3 artifacts
waveform compilation
quantum evolution
```

## Inputs

Required immutable inputs:

```text
configs/devices/2q1c2r.yaml
output/stage_01_device_model/device_artifacts.json
output/stage_02_hamiltonian/hamiltonian_artifacts.json
output/stage_02_1_hamiltonian_rebaseline/rebaseline_manifest.json
output/stage_02_1_hamiltonian_rebaseline/rebaseline_approval.json
output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json
output/stage_03_1_q1_q2_coupling/verification_report.json
output/stage_03_1_q1_q2_coupling/acceptance_approval.json
configs/control/2q1c2r_channels.yaml
Stage 4 design-freeze manifest
```

## Exact registry contract

The merged registry has exactly these keys and no others:

```text
q1_xy, q2_xy, q1_z, q2_z, c_z, r1_ro, r2_ro
```

For `q1_xy`, `q2_xy`, `c_z`, `r1_ro`, and `r2_ro`, `kind`, `target`, and `port` must equal the Stage 1
configuration byte-for-byte after YAML scalar parsing. `q1_z` and `q2_z` are the only legal additions.

Every channel has one or two globally unique AWG lanes:

```text
xy/readout: exactly two ordered lanes [I, Q]
z: exactly one lane
```

Every target must exist. XY and Z targets must have a SQUID where flux control is claimed; readout targets
must have role `readout`. All identifiers are ASCII and match `[a-z][a-z0-9_]{0,63}`.

## Compatibility manifest

Output path:

```text
output/stage_04_0_control_channel_rebaseline/control_channel_manifest.json
```

The canonical manifest has exactly these root fields and no others:

```text
schema_version=0.1
artifact_type=stage_04_0_control_channel_compatibility
artifact_version=0.1
paths:
  stage4_design_freeze_manifest
  base_device_config
  base_device_artifact
  stage2_artifact
  stage2_1_manifest
  stage2_1_approval
  stage3_1_artifact
  stage3_1_report
  stage3_1_approval
  channel_registry
sha256:
  the same ten exact keys with suffix _sha256
base_channels
added_channels
merged_channels
checks
blocking_reasons
compatibility_candidate_ready
```

`paths` has exactly the ten listed keys. `sha256` has exactly the corresponding ten keys with suffix
`_sha256`; values are uppercase 64-character SHA-256 text of the resolved raw bytes. `base_channels`,
`added_channels`, and `merged_channels` are mappings in the fixed channel order. Base rows have exact fields
`{kind, target, port}`; added rows have `{kind, target, port, awg_lanes}`; merged rows have
`{kind, target, port, awg_lanes, origin}` where origin is exactly `stage1_base` or `stage4_extension`.
Each ordered check row has the exact fields `{name, passed, message}` where `passed` is a JSON boolean and
`message` is a non-empty string. No extra channel-row or check-row field is legal.

The ordered checks are:

```text
stage4_design_freeze_valid
base_device_config_matches_stage1_artifact
stage2_1_approval_valid
stage3_1_readiness_valid
base_channel_subset_exact
only_q1_z_q2_z_added
targets_and_kinds_valid
ports_unique
awg_lanes_unique
merged_registry_canonical
```

All must pass. `compatibility_candidate_ready=true` if and only if all ten checks pass and
`blocking_reasons=[]`. Each blocking reason is a non-empty string in check order. Missing, extra, stale,
malformed, noncanonical, or non-finite content fails closed.

## Verification report and notebook

The development report path is:

```text
output/stage_04_0_control_channel_rebaseline/verification_report.json
```

It has exactly these fields:

```text
schema_version=0.1
artifact_type=stage_04_0_control_channel_verification_report
artifact_version=0.1
execution_succeeded
compatibility_candidate_ready
control_channel_ready=false
approval_status=pending
manifest_path
manifest_sha256
notebook_path
notebook_sha256
checks
blocking_reasons
```

The two paths are repository-relative POSIX paths to the sibling files. The two hashes bind their raw bytes.
The report `checks` and `blocking_reasons` exactly equal the bound manifest values.
`execution_succeeded=true` requires canonical manifest/report bytes and a genuinely executed notebook;
`compatibility_candidate_ready` exactly equals the manifest value. Development cannot set
`control_channel_ready=true` or any approval status other than `pending`.

The notebook's first code cell loads only the sibling `control_channel_manifest.json`. It shows the seven
channels, origins, ports, lanes, upstream hashes, and all checks. It is cleared and executed with a real
notebook engine, has sequential execution counts, and has zero error outputs. Counts or outputs are never
fabricated.

## Independent approval contract

The canonical `control_channel_approval.json` has exactly these fields:

```text
schema_version=0.1
artifact_type=stage_04_0_control_channel_approval
artifact_version=0.1
decision=approved
reviewer_role=independent_test_review_ai
blocking_findings=[]
stage4_design_freeze_manifest_sha256
channel_registry_sha256
control_channel_manifest_sha256
verification_notebook_sha256
verification_report_sha256
review_record_path
review_record_sha256
```

The review path is repository-relative POSIX text and points outside the output directory. Every hash is
uppercase SHA-256 of current raw bytes. The approval binds the exact Stage 4 design-freeze manifest and
registry already bound by the compatibility manifest; transitive or self-declared equivalence is not
sufficient. Missing, extra, stale, malformed, noncanonical, non-finite, non-approved, or non-independent
content fails closed.

`ControlChannelReadinessReport` is an immutable in-memory object with exactly:

```text
ok
control_channel_ready
approval_decision
approval_valid
bound_hashes
checks
blocking_reasons
```

`bound_hashes` has exactly the six approval hash fields. Its ordered check rows use exact
`{name, passed, message}` fields and names:

```text
design_freeze_hash_matches
registry_hash_matches
manifest_hash_matches
notebook_hash_matches
report_hash_matches
review_hash_matches
approval_contract_valid
```

The approval builder is allowed to write only when the directory is exact-three. The readiness validator
requires the current exact-four set. `ok=true` and `control_channel_ready=true` if and only if the
manifest/report candidate flags are true, all seven readiness checks pass,
`approval_decision=approved`, `approval_valid=true`, and `blocking_reasons=[]`. Any unavailable input yields
false, never a skipped check.

## Public interfaces

```text
load_control_channel_registry(path) -> ControlChannelRegistry
validate_control_channel_compatibility(...) -> ControlChannelCompatibilityReport
build_control_channel_manifest(...) -> dict[str, Any]
validate_control_channel_manifest(path, ...) -> dict[str, Any]
build_control_channel_approval(...) -> dict[str, Any]
validate_control_channel_approval(path, ...) -> ControlChannelReadinessReport
```

All validation APIs are pure and read-only. Writers accept only fully assembled aggregate objects and use
canonical UTF-8/LF/no-BOM/final-LF JSON with recursive finite preflight and atomic replace.

`validate_control_channel_approval` reloads and strictly validates the freeze manifest, registry, manifest,
notebook, report, review, and approval raw bytes. It verifies exact schemas, canonical JSON where applicable,
all path/hash bindings, report/manifest cross-consistency, genuine notebook execution evidence, and the
directory lifecycle before returning readiness.

## Execution order

```text
1. Freeze Stage 4 scope, plan, and detailed design through independent design review.
2. Development implements only registry loading and compatibility validation.
3. Development publishes a non-approved manifest, actually executed notebook, and pending report.
4. Independent test validates exact hashes, representative negative cases, and the merged registry.
5. Independent test writes a hash-bound approval or rejects the gate.
6. Stage 4 implementation may consume the registry only after approval validates ready=true.
```

## Tests

Required focused tests:

```text
accepted five-channel subset exact match
q1_z/q2_z valid additions
missing or extra channel
tampered kind/target/port
duplicate port or AWG lane
invalid lane count for channel kind
stale Stage 3.1 approval hash
noncanonical manifest
manifest/report hash or check mismatch
non-executed or externally-reading notebook
approval missing/extra/stale hash field or wrong reviewer role
exact-three/exact-four directory lifecycle
positive approval path
```

This is a narrow metadata gate. One representative tamper per risk class is sufficient; exhaustive fuzzing
or numerical solver regeneration is not required.

## Outputs

```text
configs/control/2q1c2r_channels.yaml
output/stage_04_0_control_channel_rebaseline/control_channel_manifest.json
output/stage_04_0_control_channel_rebaseline/verification.ipynb
output/stage_04_0_control_channel_rebaseline/verification_report.json
output/stage_04_0_control_channel_rebaseline/control_channel_approval.json
docs/decisions/<date>-stage4-0-control-channel-review.md
```

Development creates the registry config plus the three-file pending output set. Independent test creates
the external review and the approval. No accepted Stage 1/2/3 file is overwritten.

Before development publication, the formal target directory must not exist. The writer creates exactly the
manifest, notebook, and report in a sibling staging directory and promotes the whole directory with one
same-filesystem atomic rename. No copy/delete, merge, nested target, overwrite, or partial promotion is
allowed. After development it contains exactly these three files and no temporary files:

```text
control_channel_manifest.json
verification.ipynb
verification_report.json
```

Independent test first verifies that exact-three set and then atomically adds only
`control_channel_approval.json`; the accepted directory is exact-four. A pre-existing approval, extra file,
missing file, or failed hash check blocks approval without modifying the directory. The external review
record is not copied into the output directory.

## Acceptance

Stage 4.0 passes only when:

```text
the exact seven-channel merge is valid
all accepted upstream hashes validate
the manifest is canonical and reconstructable
the notebook is actually executed and reads only the sibling manifest
the independent approval validates control_channel_ready=true
```

If the compatibility gate fails, Stage 4 development remains blocked. Any requested physics or existing
channel change must be escalated to a full upstream rebaseline design.
