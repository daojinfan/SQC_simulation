# Stage 6 Detailed Design: Virtual Experiment Runtime Platform

## 1. Decision and boundary

Stage 6 is the local, single-host platform kernel for repeatable virtual experiments. It owns request admission,
scan ordering, run lifecycle, backend dispatch, raw dataset assembly, provenance, immutable publication, and a
query index. It does not own pulse physics, calibration algorithms, measurement physics, or presentation.

The MVP uses one deterministic fake experiment/backend to prove the platform. Under
`docs/decisions/2026-07-14-stage6-platform-only-entry.md`, this work does not enter physics-capable Stage 6:
Stage 5 artifacts are not ingested and no physical backend is registered. No Stage 6 record may claim a physical
experiment result until the original Stage 5 readiness gate and a separate Stage 6 physics-backend review pass.

## 2. Package ownership

```text
src/sqvm/runtime/
  models.py        typed request, scan, result, run, manifest, and receipt contracts
  config.py        strict two-phase YAML/path admission
  scan.py          pure scan expansion and point identity
  registry.py      immutable built-in experiment/backend registries
  backend.py       backend protocol and capability admission
  fake.py          deterministic MVP experiment and backend
  lifecycle.py     transition table, cancellation, clocks, and error taxonomy
  journal.py       canonical hash-chained event writer/validator
  dataset.py       raw binary encoding and dataset manifest validation
  storage.py       locks, staging, atomic terminal publication, and recovery
  catalog.py       derived SQLite index and rebuild
  provenance.py    device/calibration/source/environment snapshots
  verify.py        independent run verification API
```

`analysis/`, `readout/`, and `web/` remain future package owners. Stage 7 adds experiment definitions and
analysis without changing the runtime state machine. Stage 8 adds observation/measurement payloads through a
new reviewed backend capability. Stage 9 consumes read-only run/query APIs.

`runtime/` is the frozen package name for the platform kernel and supersedes the provisional `simulation/` name
in `docs/10_development_process.md`. `experiments/` is reserved for Stage 7 and must not appear in the MVP.

## 3. Configuration contract

Stage 6 schema version is string `0.1`. The root keys are exactly:

```text
schema_version
experiment_id
backend_id
device_snapshot
calibration_snapshot
parameters
scan
execution
publication
```

IDs are lowercase ASCII matching `[a-z][a-z0-9_]{0,63}`. Config never contains a Python module, class, function,
shell command, URI, or arbitrary plugin path.

`device_snapshot` and `calibration_snapshot` are nonempty repository-relative paths. They must resolve inside
the repository without `..`, absolute/drive/UNC forms, symlink escape, or case-collision ambiguity. Admission
reads and validates the complete config before creating any lock, directory, database, or backend object.

`parameters` is an exact mapping defined by the selected built-in experiment. Values are finite JSON scalars or
strict arrays accepted by that definition; no permissive extra field is retained.

`scan` has exactly `{axes,repetitions}`. Each axis has exactly `{name,unit,values}`. Axis names are unique IDs,
units are nonempty ASCII registry values, and values are explicit nonempty finite scalar arrays. MVP has no
`linspace`, expression, adaptive, dependent, randomized, or externally generated axis. `repetitions` is an
integer from 1 through 1,000.

Each axis name must identify a parameter that its `ExperimentDefinition` marks scannable. That parameter is
absent from the base `parameters` mapping; the axis value supplies it for exactly one point. An axis unit must
exactly equal the parameter's declared unit. Non-scannable parameters and duplicate base/axis assignments fail
admission.

`execution` has exactly:

```text
seed                    unsigned 64-bit integer
max_points              integer 1..10000
point_budget_seconds    finite positive number
run_budget_seconds      finite positive number
fail_fast               true
```

The expanded point count including repetitions must not exceed both `max_points` and the immutable hard ceiling
10,000. The run budget must be at least the point budget. Both are cooperative budgets measured by the parent
runtime's monotonic clock before and after each `execute_point` call. MVP backends must declare capability
`cooperative_deadline_v1`; non-cooperative backends fail admission. A return after a point or run deadline fails
the run as `timeout`, but the MVP promises no mid-call preemption or hard latency bound. The trusted fake backend
does not block. Process isolation and forceful termination are future work.

`publication` has exactly `{allow_existing_target}` and its value is false. `output_root` is an operational API/
CLI argument and is deliberately absent from the semantic request. It is resolved once, containment-checked,
and bound into reservation/manifest metadata; there is no second output-root authority. It must be a
repository-relative path with the same absolute/drive/UNC/`..`/symlink/case-collision rejection as snapshot
paths.

The MVP request must name device snapshot `configs/devices/2q1c2r.yaml`, whose current raw SHA-256 is
`CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F`. It must name calibration snapshot
`configs/calibration/platform_uncalibrated_v1.json`, created as an implementation task, with the exact canonical
payload:

```json
{
  "accepted": false,
  "device_snapshot": "configs/devices/2q1c2r.yaml",
  "device_snapshot_sha256": "CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F",
  "schema_version": "0.1",
  "state_id": "uncalibrated",
  "status": "uninitialized",
  "values": {}
}
```

Its raw SHA-256 is `E3DD8BB9508AEE3984DAC6D01729466DB0023769DF437CF63A7C1BD15FEDD2F4`.
It satisfies provenance completeness but grants no calibration claim. Any change to either tracked file requires
a reviewed design amendment rather than silently accepting a new hash.

## 4. Deterministic scan expansion

Axis order is YAML array order. The Cartesian product is row-major with the last axis changing fastest.
Repetition is the outermost dimension: all points for repetition 0 precede repetition 1. No point order is
derived from mapping iteration.

The canonical point payload is:

```text
{schema_version,experiment_id,point_index,repetition,coordinates,seed}
```

`coordinates` is an ordered array of `{axis,value,unit}`. Each point seed is derived with SHA-256 from the
unsigned run seed and canonical payload without `seed`; it is never generated from process-global RNG state.
`point_id` is the uppercase SHA-256 of the complete canonical point payload. Expansion is a pure function and
returns an immutable tuple. `point_table.json` has exact root keys `{schema_version,experiment_id,points}`. Each
entry in `points` has the complete point payload plus `point_id`; the hash is recomputed from that entry with
`point_id` omitted. Its raw SHA-256 is independently bound by the manifest and every completed dataset manifest.
`point_index` must equal the array offset, and dataset row `point_index` is the sole coordinate/result join key.

## 5. Registries and backend protocol

Registries are immutable mappings constructed by code from built-in definitions. Duplicate IDs fail at
construction. Configuration can select only a registered ID; runtime registration and entry-point discovery are
not part of MVP.

An `ExperimentDefinition` declares:

```text
experiment_id
request_schema
required_backend_capabilities
result_schema
dataset_schema
prepare(request, snapshots) -> PreparedExperiment
build_command(prepared, point) -> BackendCommand
```

An `ExperimentBackend` declares:

```text
backend_id
capabilities() -> BackendCapabilities
admit(prepared_experiment) -> BackendAdmission
execute_point(command, context) -> PointResult
```

`context` contains run/point identity, point seed, monotonic deadline, and cooperative cancellation token. A
backend cannot choose output paths, publish files, mutate snapshots, or change point identity. Errors use the
frozen taxonomy `admission`, `capability`, `resource_busy`, `timeout`, `cancelled`, `backend_failure`,
`integrity_failure`, and `publication_failure`.

The MVP definition `platform_deterministic_smoke_v1` uses backend `deterministic_fake_v1` and the tracked config
`configs/experiments/platform_deterministic_smoke_v1.yaml`. The experiment has only two scannable parameters,
`x` and `y`, both binary64 and unit `dimensionless`; it has no base parameters. The config is frozen to seed 0,
one repetition, `x=[0.0,0.5]`, and `y=[-1.0,0.0,1.0]`, in that axis order. The backend computes exactly one
binary64 result per point with Python/IEEE-754 operations `response = x + (2.0 * y)` and returns no other value.
The ordered response values are `[-2.0,0.0,2.0,-1.5,0.5,2.5]`.

`data/response.bin` is exactly 48 bytes of little-endian `<f8`:

```text
00000000000000C000000000000000000000000000000040
000000000000F8BF000000000000E03F0000000000000440
```

Its raw SHA-256 is `01279CD8EFD786E0F4ED0EA714EE57AF7C82BE12D7259339A5998FF7C120E1A0`.
The variable is named `response`, has dimensions `['point']`, shape `[6]`, order `C`, dtype `<f8`, unit
`dimensionless`, and semantic role `platform_fixture_response`.

Fake claim metadata is runtime-owned and has exact values:

```text
evidence_class = "platform_test_fixture"
physics_claim = "none"
observation_model = "absent"
measurement_payload = null
```

## 6. Claim envelope and physics boundary

The runtime injects the exact fake claim envelope into `request.json`, `manifest.json`, `data/dataset.json`,
`verification_report.json`, and `receipt.json`. Configuration and backend results have no claim-metadata field.
The verifier recomputes the envelope from the registered experiment/backend pair and rejects a missing, renamed,
unknown, caller-supplied, or nonidentical value in any location.

Stage 5 artifacts and APIs are outside the MVP input graph. The registries contain no Stage 5 or physical backend;
requesting one fails before reservation. A future physical backend requires the original independently approved
`Stage6ReadinessReport.stage6_ready=true` and a separately frozen Stage 6 physics-backend gate. That future design
must specify authoritative gate schemas, public validators, source/environment/evidence hashes, independent
approvals, and a complete fail-closed truth table. The names of future gates do not constitute an interface.

## 7. Run identity and state machine

`run_id` is a lowercase UUID4 generated once after full admission. IDs are never user supplied and never reused.
MVP has no retry API, retry field, automatic retry, or resume. A user may submit the same semantic request again;
that is an unrelated run with a new ID. Provenance relationships require a later reviewed schema extension.

The only nonterminal transitions are:

```text
reserved -> prepared -> running -> finalizing -> completed
reserved/prepared/running/finalizing -> failed
prepared/running -> cancelled
```

Every terminal state is immutable. Config failure occurs before `reserved` and creates no run. `interrupted` is
not a transition of the original run: it is the terminal status of a separate `RecoveryRecord` with its own run
ID and exact storage schema in section 12.

Required events are `run_reserved`, `run_prepared`, `run_started`, `point_started`, `point_completed`,
`run_finalizing`, and one terminal event. Optional failure-path events are `cancel_requested`, `run_cancelled`,
`point_failed`, and `run_failed`. Recovery records use their separate schema and do not append to this journal.

Cancellation is requested by exclusive creation of a request file and is observed before and after each point.
Completion that has entered `finalizing` wins over a later cancellation request. Cancelled and failed runs
publish request, point table, snapshots, events, manifest, report, and receipt, but no `data/` directory or result
conclusion.

## 8. Locking and concurrency

Before staging, the runner exclusively creates `locks/<run_id>.lock`. It then acquires one exclusive resource
lock keyed by SHA-256 of `{backend_id,device_snapshot_sha256}`. If future work needs multiple locks, keys are
acquired in ascending byte order. A loser performs no backend work.

Locks record run ID, resource key, UTC creation time, host/process diagnostics, request hash, and environment
fingerprint. They contain no secret or absolute interpreter path. A lock is never silently stolen based on PID
or age. Before any terminal publication, byte-identical copies are written as `snapshots/run_lock.json` and
`snapshots/resource_lock.json`; their raw SHA-256 values are bound by manifest and receipt. After completed,
failed, or cancelled publication, the resource lock is deleted and the run-ID lock is retained as consumption
evidence. If publication does not complete, both locks remain and explicit recovery is required.

Stage 6 MVP is single-host. Network filesystems, distributed leases, and multi-worker scheduling are unsupported.

## 9. Storage layout and authority

The output layout is:

```text
<output_root>/
  runs/<run_id>/
    request.json
    point_table.json
    manifest.json
    verification_report.json
    receipt.json
    events.jsonl
    snapshots/
      device.yaml
      calibration.json
      environment.json
      source.json
      run_lock.json
      resource_lock.json
    data/
      <variable>.bin
      dataset.json
  staging/<run_id>/
  locks/<run_id>.lock
  resource-locks/<resource_key>.lock
  recovery-locks/<original_run_id>.lock
  cancellation/<run_id>.request
  quarantine/<original_run_id>/
  quarantine-records/<original_run_id>.json
  resource-release-records/<run_id>.json
  catalog.lock
  catalog.sqlite
```

An immutable terminal run directory is the sole evidence authority. SQLite is a derived query cache and raw
numeric data is never stored in it. All manifest paths are relative POSIX paths; exact file sets are validated.

The MVP unit registry contains exactly `dimensionless`; variables and coordinates may use no other unit. Raw
variables are uncompressed C-order little-endian `<f8`, `<i8`, `<u8`, or `<c16` binary files. `<c16` means one
little-endian float64 real value immediately followed by one little-endian float64 imaginary value per C-order
element. Object, string, native-endian, compressed, pickled, and non-finite numeric arrays are forbidden.
`dataset.json` has exact root keys `{schema_version,claim_envelope,point_table_sha256,dimensions,variables}`.
Dimension names and variable names are lowercase IDs. Each variable has exact keys
`{dimensions,dtype,shape,order,unit,semantic_role,byte_length,raw_sha256}`. Completed datasets bind the canonical
point table and contain every point exactly once.

The status-specific payload set, before manifest/report/receipt are written, is exact. Completed runs contain
`request.json`, `point_table.json`, `events.jsonl`, the six named snapshot files, `data/dataset.json`, and every
binary file declared by that dataset. Failed/cancelled runs contain the same non-data files and no `data/`
directory. Unknown files, symlinks, alternate case spellings, and empty extra directories fail publication and
verification.

Standalone JSON uses the existing `sqvm.hamiltonian.provenance.canonical_json_bytes` contract: UTF-8, LF, no
BOM, sorted keys, two-space indentation, finite JSON numbers, and a final LF. YAML snapshots retain raw accepted
bytes and are bound by raw SHA-256. JSONL uses the separate compact canonical-line contract defined next.

## 10. Event and hash chain

`events.jsonl` contains one compact canonical JSON object per line with exact keys:

```text
schema_version
run_id
sequence
event_type
utc_time
monotonic_ns
payload
prev_event_sha256
event_sha256
```

Each line is `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)` encoded
as UTF-8 plus one LF. `utc_time` is UTC RFC 3339 with exactly six fractional digits and `Z`, for example
`2026-07-14T03:04:05.123456Z`. Sequence starts at zero and increments by one. The first event has
`prev_event_sha256=null`. `event_sha256` is uppercase SHA-256 of the canonical line for the object with the
`event_sha256` key omitted; each next event binds it. The final manifest records the complete events file raw SHA
and tail event SHA.

Event payloads have no additional keys:

| event type | exact payload keys |
|---|---|
| `run_reserved` | `request_sha256`, `point_table_sha256`, `run_lock_sha256`, `resource_lock_sha256` |
| `run_prepared` | `experiment_id`, `backend_id`, `backend_capabilities_sha256` |
| `run_started` | `point_count` |
| `point_started` | `point_index`, `point_id` |
| `point_completed` | `point_index`, `point_id`, `result_sha256` |
| `point_failed` | `point_index`, `point_id`, `error_class`, `message` |
| `cancel_requested` | `cancellation_request_sha256` |
| `run_finalizing` | `completed_point_count` |
| `run_completed` | `dataset_manifest_sha256`, `completed_point_count` |
| `run_failed` | `error_class`, `message`, `completed_point_count` |
| `run_cancelled` | `completed_point_count` |

Messages are bounded UTF-8 text with at most 1,024 Unicode scalar values and contain no traceback, absolute
path, environment value, or secret. Point events must match the canonical point table; completed point indices
are unique and contiguous for the deterministic MVP run.

The hash graph is acyclic and exact:

1. `manifest.json` inventories only the status-specific payload set from section 9 using relative POSIX path,
   byte length, and raw SHA-256. It also records schema/identity/status, resolved output-root identity, request and
   point-table hashes, snapshot/source/environment/device/calibration aggregates, lock hashes, backend
   capabilities, claim envelope, and completed-only dataset summary. It excludes itself,
   `verification_report.json`, and `receipt.json`.
2. `verification_report.json` binds the raw manifest SHA-256 and records independently recomputed schema, event,
   point, lock, snapshot, claim-envelope, and completed-only dataset checks. It does not bind the receipt.
3. `receipt.json` binds raw manifest and report SHA-256, event-tail SHA-256, terminal status, elapsed runtime,
   published relative path, run/resource-lock SHA-256, claim envelope, and completed-only dataset hashes. It
   excludes itself and is not referenced by manifest or report.

The exact final run file set is the payload set plus these three files. Self-hashes and reverse edges are
forbidden.

Source snapshot covers the sorted raw file set under `src/sqvm/runtime/`, `src/sqvm/__main__.py`, the reused
`src/sqvm/hamiltonian/provenance.py` canonical serializer, the exact Stage 6 experiment and calibration configs,
`pyproject.toml`, and `requirements-stage6-lock.txt`. It also binds this plan, detailed design, platform-only entry
decision, and the later design-freeze review record as design-authority files. The implementation task must
create that exact lock and review file; falling back to `requirements-stage5-lock.txt` is forbidden.
`snapshots/source.json` lists every repository-relative POSIX path, byte length, and raw SHA-256, then binds the
aggregate canonical SHA-256.
Environment records Python implementation/version, exact package versions, OS/architecture, and BLAS/threading
summary. Absolute interpreter paths and environment values that can contain credentials are forbidden.

## 11. Atomic publication and catalog

All work occurs in `staging/<run_id>` after the runtime verifies that `staging/` and `runs/` have the same volume
identity. Before publication the writer closes and re-reads every file, validates schemas, finite values, shapes,
event chain, exact file set, and hashes, then writes manifest/report/receipt. It flushes every file with the host
durability primitive, flushes each changed directory, closes every handle, and performs one same-volume atomic
directory rename to the absent `runs/<run_id>` target. POSIX uses file/directory `fsync` plus rename; Windows uses
`FlushFileBuffers` on file and directory handles plus the Win32 same-volume directory move with no replacement.
Unsupported durability primitives fail admission rather than silently weakening the contract.

After rename, the runtime flushes `runs/` and its parent output-root directory before reporting publication
success or deleting the resource lock. Failure at this post-rename durability step leaves the published run and
resource lock intact, reports `publication_failure`, and requires explicit resource-lock recovery against the
verified terminal receipt; it never republishes or edits the run.

Target-exists, cross-volume, access-denied, sharing-violation/file-in-use, flush, or rename failure leaves staging
and both locks untouched. It returns `publication_failure` through the caller/CLI only; it cannot publish a
receipt that claims a terminal run. Explicit recovery is then the only mutating operation.

After the immutable directory exists, one SQLite transaction indexes run ID, terminal status, creation UTC,
terminal UTC, experiment/backend IDs, tags, manifest/receipt hashes, and derivation relationships. UUID4 is
identity only and is never used as a time ordering key. WAL and a schema
migration table are used. Catalog failure does not invalidate the run; `RunArtifactSet.catalog_indexed=false`
and a warning are returned. `rebuild_run_catalog` deletes no run bytes and reconstructs the database only from
verified run directories.

Normal catalog writes and rebuilds take the same exclusive `catalog.lock`. Rebuild writes and verifies a new
same-directory database, checkpoints/closes it, and atomically replaces `catalog.sqlite` while holding the lock.
It scans only immutable terminal directories, so active staging runs are never indexed. Failure preserves the
previous catalog and the evidence directories.

Published run directories are never overwritten, resumed, or edited. MVP readers accept exact schema `0.1` and
reject every other evidence version; evidence migration and derivation are deferred to a separately reviewed
schema extension. SQLite catalog schema changes use numbered, transactional migrations, but a failed migration
keeps the prior database and the catalog always remains rebuildable from exact-version run evidence.

## 12. Crash recovery

Startup may report stale staging/locks but cannot alter them. `recover_interrupted_run` first exclusively creates
`recovery-locks/<original_run_id>.lock`; a second recovery fails without mutation. It verifies the original run
lock, resource lock, request, point table, snapshots, event prefix, and every staging file; it never executes a
backend or completes missing points.

For a valid prefix, recovery closes handles, flushes bytes, and performs a same-volume absent-target rename from
`staging/<original_run_id>` to `quarantine/<original_run_id>`. It then creates a fresh recovery ID and publishes
`runs/<recovery_id>/` with the exact files `recovery.json`, `manifest.json`, `verification_report.json`, and
`receipt.json`. `recovery.json` has exact keys `{schema_version,recovery_id,status,original_run_id,reason,
original_run_lock_sha256,original_resource_lock_sha256,quarantine_relative_path,quarantine_inventory}` and
`status="interrupted"`. Its manifest inventories only `recovery.json`; report binds manifest; receipt binds
manifest/report and both original lock hashes using the acyclic topology in section 10.

Only after both quarantine rename and verified recovery-record publication succeed may recovery delete the stale
resource lock and its recovery lock. The original run lock is retained, and the original run ID remains
permanently consumed. Any failure retains the relevant locks and current bytes for another explicit attempt.
Malformed, symlinked, cross-root, or hash-inconsistent staging cannot produce a recovery run. While holding the
recovery lock, recovery records a no-follow raw tree inventory and reason in canonical
`quarantine-records/<original_run_id>.json`, flushes it, then same-volume renames the untouched tree to the absent
`quarantine/<original_run_id>`. If either write/flush/rename fails, nothing is released and the current locations
remain authoritative. A quarantined malformed attempt retains its run, resource, and recovery locks and blocks
resource release pending a future reviewed manual procedure. Automatic checkpoint/resume is explicitly deferred.

If an immutable terminal run exists but its matching resource lock remains after post-rename flush or lock-delete
failure, only `recover_terminal_resource_lock` may clean it up. Under `recovery-locks/<run_id>.lock`, it verifies
the terminal manifest/report/receipt, the run/resource lock snapshots, and byte equality of the live resource
lock; rejects any staging directory or hash mismatch; re-flushes the terminal and parent directories; then writes
and flushes canonical `resource-release-records/<run_id>.json` with exact keys `{schema_version,run_id,
terminal_receipt_sha256,resource_lock_sha256,reason,authorized_utc}`. It deletes the resource lock and flushes
`resource-locks/`, then deletes the recovery lock. The authorization record is operational audit data, not run
evidence. A failure retains the remaining locks and is retryable only through this same exact verification path.

## 13. CLI and user-visible operations

Planned commands are:

```text
python -m sqvm preview-experiment <config>
python -m sqvm run-experiment <config> --output <root>
python -m sqvm inspect-run <run-dir>
python -m sqvm verify-run <run-dir>
python -m sqvm cancel-run <output-root> <run-id>
python -m sqvm recover-run <output-root> <run-id>
python -m sqvm recover-resource-lock <output-root> <run-id>
python -m sqvm rebuild-run-catalog <output-root>
```

Preview performs admission and scan expansion with no output. Run prints the run ID immediately after reservation
and emits progress to the console; the immutable run directory appears only at terminal publication. Stage 9 may
later replace console progress with a reviewed service/event API without changing run evidence.

## 14. Verification matrix

The independent matrix must cover:

- every malformed config/key/type/ID/unit/value/path and registry mismatch;
- scan order under mapping reorder, repetitions, floating boundaries, limits, and seed replay;
- every permitted transition and forbidden terminal rollback;
- duplicate output/run identity, resource contention, fixed lock order, stale lock, and lock tampering;
- cancellation at each point boundary and cancellation/finalizing race;
- cooperative budget overrun/backend exception, partial file, disk error, rename failure, and catalog failure;
- dataset missing/duplicate/reordered point, dtype/endianness/shape/unit/finiteness corruption;
- event, manifest, receipt, snapshot, source, environment, and raw data tampering;
- interrupted recovery, quarantine, no automatic resume, and consumed run identity;
- SQLite deletion/corruption and exact rebuild from verified directories;
- unregistered Stage 5/physical backend requests and every fake-envelope attempt to obtain a physics/readout claim;
- Windows/POSIX paths, drive/UNC/case collision, file-in-use behavior, UTF-8/LF, locale, and timezone.

The bounded end-to-end smoke must run the exact deterministic 2x3 fake scan twice in separate roots and compare
point tables, dataset bytes, and all deterministic payloads after excluding run IDs and clocks.

## 15. Stage boundaries

Stage 7 may add spectroscopy/Rabi/Ramsey/DRAG/coupler/CZ definitions, fitting outputs, recommendations, and human
accept/reject records. It cannot mutate an existing run or calibration snapshot. Enabling those physical scans
requires formal Stage 5 qualification and a reviewed parameterized control-program boundary.

Stage 8 adds readout-resonator Hamiltonians, drive, dissipation/noise, measurement chain, IQ/assignment models,
and observation payloads. Before that gate, `observation_model="absent"` and `measurement_payload=null` are
mandatory.

Stage 9 reads verified immutable runs and the rebuildable catalog. It does not become an evidence authority and
cannot bypass admission, cancellation, recovery, or calibration acceptance rules.

## 16. MVP non-goals and release gate

MVP does not provide adaptive scans, early stopping, parallel points, distributed workers, remote storage,
automatic resume/retry, arbitrary plugins, large-data chunking, Web APIs, calibration fitting, or measurement.

Implementation may start only after independent review approves this design and a hash-bound design-freeze
record. Implementation acceptance additionally requires the complete verification matrix, a deterministic
smoke artifact/report, cross-platform evidence, catalog rebuild, independent test approval, and release review.
No result may be labeled formal, calibrated, measured, or Stage 6 physics-ready under the MVP fake backend.
