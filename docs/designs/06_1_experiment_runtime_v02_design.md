# Stage 6 Runtime Schema 0.2 Detailed Design

Date: 2026-07-16
Status: implementation authority for the compiler-evidence lane

## 1. Purpose and boundary

Runtime schema `0.2` is the versioned platform boundary between a non-empty QCIS program and later calibration
experiments. Its first executable profile is compiler-only: it materializes and compiles every scan point,
publishes immutable compiler evidence, writes a generic multi-variable dataset, and independently replays that
evidence. It never dispatches QuTiP or a physical backend.

This phase makes no spectroscopy, population, fidelity, calibration, recommendation, hardware, noise, readout,
or measurement claim. The built-in Stage 6 physical backend registry remains closed. Stage 7.1 bounded entrance
evidence is not registered by this change.

Schema `0.1` remains a separate frozen lane. Its package bytes, request, point identity, fake backend,
`response.bin`, manifest/report/receipt, verifier, recovery, catalog, and source-snapshot semantics are not
reinterpreted or upgraded. Runtime `0.2` lives in `sqvm.runtime_v02`; `sqvm.runtime_api` is the only
version-dispatched facade. The frozen `sqvm.runtime` package is not modified and remains the direct `0.1` API.

## 2. Exact compatibility matrix

```text
request 0.1 -> program null -> deterministic_fake_v1 -> artifacts/verifier/recovery 0.1
request 0.2 -> QCIS program 0.3 -> qcis_compiler_only_v1 -> artifacts/verifier/recovery 0.2
```

Unknown versions and every cross-row combination reject before reservation or output creation. Public dispatch
peeks only the exact root `schema_version`; it never tries one loader and falls back to another.

## 3. Request and program contract

The `0.2` request has exact root keys:

```text
schema_version, experiment_id, backend_id, device_snapshot, calibration_snapshot,
parameters, program, scan, execution, publication
```

The initial registered pair is exact:

```text
experiment_id = platform_qcis_compile_smoke_v1
backend_id    = qcis_compiler_only_v1
```

`device_snapshot` and `calibration_snapshot` remain the Stage 6 frozen uncalibrated fixture. They are evidence
inputs only and cannot authorize calibrated macros. `parameters` is an exact empty object. `program` is the
existing QCIS v0.3 envelope with exact keys:

```text
program_schema_version="0.3"
instruction_set_id="qcis_stage7_calibration_v3"
template_id
template_sha256
source_format="qcis_template"
source
bindings
```

The program must match the immutable compiler-fixture authority and template byte-for-byte. Only explicit
non-measurement pulse/timing instructions admitted by that authority are allowed. The initial template uses a
typed `scan_ref` for drive frequency. Arbitrary imports, paths, URIs, code, user-provided arrays, calibrated
macros, measurement instructions, fit definitions, and claim fields are forbidden.

The compiler-fixture authority is canonical JSON and binds its instruction profile, QAgent registry, gate
configuration, waveform registry, clock, compiler snapshot, templates, idle flux, exact component hashes, and
its own derived authority ID. A separate canonical approval binds the exact authority raw SHA-256, authority ID,
design hash, independent reviewer role, and `APPROVE` decision. Runtime code pins the approval raw hash and
authority ID. Any byte, hash, decision, reviewer, or cross-binding mismatch rejects.

## 4. Scan and point identity

Schema `0.2` uses `ScanPointV02`; it does not reinterpret `SharedScanPointV01`. Axes retain YAML order and the
last axis varies fastest. Each point payload binds:

```text
schema_version="0.2", experiment_id, request_sha256, program_authority_sha256,
point_index, repetition, coordinates, seed
```

The seed is derived from the run seed and the same payload without `seed`; `point_id` is the uppercase SHA-256
of the canonical payload including `seed`. Consequently equal inputs are stable, while changing any program,
template authority, coordinate, order, repetition, or run seed changes the point identity.

## 5. Point compilation and evidence

Every point calls the public QCIS `compile_qcis` function with the admitted authority, frozen idle flux, and
exact typed scan coordinates. It does not call Stage 4.1, Stage 5.1, Stage 7.1, or any backend protocol.

The point directory is `points/<point_id>/` and has the exact files:

```text
concrete.qcis
ast.json
trace.json
logical_inventory.json
result.json
```

`logical_inventory.json` records the canonical dtype, shape, byte length, and raw SHA-256 of the five logical
arrays plus the drive-event and coefficient-inventory hashes exposed by the compiler. Raw logical arrays are
not a dataset or backend command and are not persisted in this platform fixture. Independent verification
recompiles them from frozen inputs and compares the complete inventory.

`Stage7PointResultV02` has exact fields `{schema_version,point_id,values,diagnostics}`. For the compiler-only
definition, ordered `values` are:

```text
instruction_count      <u8  count        compiler_structure
logical_sample_count   <u8  samples      compiler_structure
trace_byte_count       <u8  bytes        compiler_structure
logical_array_bytes    <u8  bytes        compiler_structure
```

`diagnostics` contains only exact compiler evidence hashes. It contains no physical observable, population,
state, transition frequency, fit output, fidelity, or replay-quality interpretation.

## 6. Dataset 0.2

The generic dataset writer accepts an immutable ordered result schema. Each scalar variable is aggregated in
point-table order into its own `<name>.bin`. Allowed dtypes are `<f8`, `<i8`, `<u8`, and `<c16`; values must be
finite or in the exact integer range. All variables use the single `point` dimension and declare exact dtype,
shape, C order, unit, semantic role, byte length, and raw SHA-256.

`dataset.json` binds schema `0.2`, the exact compiler-only claim envelope, point-table SHA-256, ordered variable
names, point dimension, and metadata. Missing, extra, reordered, mis-typed, non-finite, length-mismatched, or
hash-mismatched data rejects.

## 7. Claim envelope

The claim envelope is code-derived and exact:

```json
{
  "backend_execution": "absent",
  "calibration_claim": "none",
  "evidence_class": "compiler_test_fixture",
  "measurement_payload": null,
  "observation_model": "absent",
  "physics_claim": "none",
  "recommendation_eligible": false
}
```

It is repeated and independently compared in request, dataset, manifest, verification report, and receipt.
Requests cannot supply or alter it.

## 8. Publication, verification, and recovery

The `0.2` runner reuses only Stage 6 infrastructure primitives: repository-contained output roots, exclusive
run/resource locks, sibling staging, fsync, immutable event journal, atomic no-replace publication, and
cooperative cancellation between points. It has its own source snapshot, artifact schemas, verifier, recovery,
and version-aware catalog rebuild. Its source snapshot binds the frozen `0.1` infrastructure snapshot plus all
`runtime_v02`, facade, request, authority, approval, and design bytes without changing the `0.1` snapshot.

Independent verification reloads the exact registered fixture request and authority, recomputes request and
point identities, recompiles every point, compares all point files and hashes, validates every dataset binary,
replays the event chain, verifies the payload inventory, and checks the acyclic manifest/report/receipt graph.
It never trusts catalog state or manifest claims.

Recovery remains no-resume. It first dispatches from the canonical staging `request.json` schema, validates the
maximal published prefix using the corresponding version verifier, quarantines invalid trees, and emits a new
interrupted record. A `0.2` prefix can never be interpreted with the fixed `0.1` response fixture rules.

## 9. Admission gates for later experiments

This design does not admit q1 spectroscopy. Before that experiment can enter Runtime `0.2`, all of the
following remain mandatory: Stage 5 formal-scale approval, `Stage6ReadinessReport.stage6_ready=true`, an
independently frozen physics-backend registration/claim authority, approved Stage 7 model-evolution entrance,
an experiment-specific observable/result/analysis definition, bootstrap or accepted-calibration authority,
independent numerical review, and user acceptance.

Adding an experiment is registry-driven but not arbitrary plugin loading. Every experiment/backend pair gets
an immutable definition, exact result schema, exact claim envelope, verifier, and authority entry. The
compiler-only fixture cannot be weakened or promoted into a physics profile.
