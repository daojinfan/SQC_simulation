# Stage 6 Plan: Virtual Experiment Runtime Platform

## Stage purpose

Stage 6 builds the platform kernel that Stage 7 calibration experiments, Stage 8 measurement models, and the
future Web Lab will use. It turns a strict experiment request into an ordered scan, executes each point through
a typed backend, and publishes an immutable dataset with complete run metadata.

Stage 6 is infrastructure, not a calibration experiment. Its MVP proves the platform with a deterministic fake
backend. It does not turn the Stage 5 smoke result into a physical experiment result.

## References

1. `docs/00_project_vision.md`: preserve a narrow, transparent `2q1c2r` workflow.
2. `docs/10_development_process.md`: freeze design and stable interfaces before implementation.
3. `docs/20_roadmap.md`: load experiment config, run scans, save datasets, and record device/calibration state.
4. `docs/decisions/2026-07-14-stage5-v0-2-implementation-review.md`: Stage 5 smoke is complete, while formal
   numerical execution and Stage 6 physics readiness are not approved.
5. `docs/decisions/2026-07-14-stage6-platform-only-entry.md`: platform-only work is permitted without opening
   the physics-capable Stage 6 entrance.
6. `docs/designs/06_experiment_runtime_design.md`: normative Stage 6 MVP contract.
7. `docs/decisions/2026-07-14-stage6-program-extension-amendment.md`: reserves the fail-closed instruction-program
   boundary for Stage 7 without enabling instruction execution in the MVP.

This stage does not adopt distributed schedulers, cloud storage, arbitrary plugins, adaptive calibration logic,
readout/IQ models, or Web UI concerns.

## Scope

Stage 6 includes:

```text
strict experiment request admission
reserved versioned instruction-program field, fixed to null in MVP
deterministic explicit-axis scan expansion
immutable built-in experiment and backend registries
run reservation, lifecycle, cancellation, and crash recovery records
exclusive backend/device resource locking
deterministic fake backend for platform verification
portable raw dataset and canonical metadata publication
append-only hash-chained event journal
rebuildable SQLite run catalog
CLI commands for run, inspect, verify, cancel, recover, and reindex
```

Stage 6 excludes:

```text
qubit spectroscopy, Rabi, Ramsey, DRAG, coupler, or CZ experiment logic
automatic fitting or calibration recommendations
automatic acceptance or mutation of calibration parameters
formal Stage 5 numerical execution
Stage 5 smoke artifact ingestion or display
any physical simulation backend or physical-result claim
readout resonator, noise, measurement, synthetic IQ, or shot assignment models
arbitrary Python/plugin loading from experiment configuration
automatic retry, automatic resume, distributed execution, or remote workers
Web APIs and browser UI
```

## Inputs

- a repository-relative Stage 6 experiment YAML file;
- tracked device configuration and a typed calibration snapshot;
- the immutable built-in experiment and backend registries;
- Stage 6 source/environment provenance.

The MVP calibration snapshot is explicitly `uncalibrated` and contains no accepted calibration values. Missing
or ambiguous device/calibration identity is an admission failure.

## Outputs

- an immutable terminal run directory containing request, snapshots, event journal, manifest, and receipt;
- for completed runs only, raw dataset files and dataset metadata;
- a human-readable verification report;
- a rebuildable `catalog.sqlite` index that is never an integrity authority;
- an independent review record before Stage 6 implementation acceptance.

## Core objects

```text
ExperimentRequest       strict admitted request
ScanAxis / ScanPoint    ordered explicit scan definition and expanded point
ExperimentDefinition    built-in experiment contract
BackendCapabilities     immutable backend capability declaration
ExperimentBackend       typed point-execution protocol
RunReservation          run/resource ownership and lock metadata
RunEvent                hash-chained lifecycle event
PointResult             typed backend result before dataset assembly
DatasetManifest         dimensions, variables, units, dtype, shape, and hashes
RunManifest             immutable run file inventory and provenance
RunReceipt              terminal status and manifest/event-tail binding
RunArtifactSet          public paths, hashes, status, and catalog warning
```

## Public interfaces

```text
load_experiment_request(path, repository_root=None) -> ExperimentRequest
expand_scan(request) -> tuple[ScanPoint, ...]
get_builtin_experiment_registry() -> ExperimentRegistry
get_builtin_backend_registry() -> BackendRegistry
run_experiment(request_path, output_root, repository_root=None) -> RunArtifactSet
load_experiment_run(run_dir, repository_root=None) -> ExperimentRun
verify_experiment_run(run_dir, repository_root=None) -> RunVerificationReport
request_run_cancellation(output_root, run_id) -> CancellationReceipt
recover_interrupted_run(output_root, run_id) -> RecoveryArtifactSet
recover_terminal_resource_lock(output_root, run_id) -> ResourceLockRecoveryReceipt
rebuild_run_catalog(output_root) -> CatalogRebuildReport
```

All interfaces are stable only after design review. Registry construction and backend implementation helpers
remain internal. Config files never name import paths or arbitrary callables.

## Correctness checks

1. Duplicate/unknown YAML keys, non-finite values, absolute paths, `..`, symlink escape, invalid IDs, and
   unregistered experiment/backend IDs fail before reservation or output creation.
2. Scan expansion is a pure function. Axis order is preserved, the last axis changes fastest, and repeated
   expansion produces byte-identical canonical point tables and point IDs.
3. State transitions follow the frozen state machine; terminal states cannot transition or be overwritten.
4. Cancellation and execution budgets are cooperative at point boundaries and produce an immutable cancelled or
   failed run without a dataset; MVP makes no hard preemption claim.
5. Crash recovery never resumes work. It consumes the original run ID and publishes a separate interrupted
   recovery record bound to the stale lock and staging hashes.
6. A post-publication resource-lock cleanup failure can release only through the explicit terminal-run recovery
   API after exact receipt/lock verification; it never edits the terminal run.
7. Completed datasets contain every point exactly once, with matching coordinates, shape, units, and finite
   values. Partial or failed runs cannot publish a dataset.
8. Manifest, receipt, snapshots, event chain, dataset files, source, environment, device, and calibration
   hashes independently recompute.
9. Concurrent runs cannot share an exclusive backend/device resource. Lock acquisition order is fixed.
10. `catalog.sqlite` can be deleted and rebuilt from immutable run directories without changing evidence.
11. Windows and POSIX tests cover path normalization, atomic create/replace/rename, UTF-8/LF, cancellation,
     stale locks, case collisions, and file-in-use failures.

## User workflow

```text
1. Inspect the experiment request and device/calibration snapshot.
2. Run Stage 6 admission/scan preview without creating a run.
3. Start a deterministic platform smoke run.
4. Inspect the run receipt, point table, dataset summary, and verification report.
5. Verify hashes independently or rebuild the SQLite catalog.
6. Explicitly recover an interrupted run; never silently resume it.
```

## Implementation tasks

1. Freeze the Stage 6 scope and detailed design through independent review.
2. Implement strict request/config models and duplicate-key/path admission.
3. Implement the fail-closed `program=null` boundary, deterministic scan expansion, and point identity.
4. Implement immutable registries, deterministic fake experiment/backend, and the second-definition extension
   contract test.
5. Implement reservation, resource lock, event journal, cancellation, and recovery state machine.
6. Implement raw dataset encoding, manifests, receipts, canonical JSON, and hash validation.
7. Implement rebuildable SQLite catalog and catalog-rebuild verification.
8. Add CLI commands and the frozen bounded Stage 6 smoke configuration.
9. Add unit, adversarial, crash, concurrency, cross-platform, and end-to-end tests.
10. Generate a verification report and independent implementation/release review.
11. Update the development log only after implementation work actually begins.

## Tests

Required tests include:

```text
strict request schema and path attacks
non-null program rejection and arbitrary instruction/import rejection
explicit scan ordering, point IDs, limits, repetitions, and seed behavior
second synthetic experiment extension without runtime-module changes
registry/capability mismatch and arbitrary import rejection
every allowed and forbidden lifecycle transition
duplicate run/output and concurrent resource contention
cancellation/complete race and backend exception cleanup
stale lock, partial JSON/binary, event-chain tampering, and explicit recovery
dataset shape/dtype/endianness/units/finiteness and missing/duplicate points
manifest/receipt/snapshot/source/environment hash tampering
SQLite deletion, corruption isolation, and deterministic rebuild
fake-result claim-envelope tampering and formal/physical/readout fail-closed gates
Windows/POSIX path, newline, rename, locking, and locale/timezone behavior
```

## Expected artifacts

The bounded platform smoke writes below:

```text
output/stage_06_experiment_runtime_smoke/
  runs/<run_id>/
  locks/<run_id>.lock
  catalog.sqlite
  verification_report.json
```

The exact run file set is defined by the detailed design.

## Acceptance criteria

Stage 6 implementation is complete only when:

1. the plan, detailed design, schema, state machine, dataset format, and public APIs are independently approved;
2. the deterministic fake backend completes the exact bounded scan and produces a verified immutable dataset;
3. all admission, lifecycle, concurrency, cancellation, crash, integrity, and cross-platform tests pass;
4. the SQLite catalog is independently rebuilt from run directories;
5. request/config/registry admission cannot select Stage 5 or any physical backend, and the fake result claim
   envelope cannot be removed or upgraded;
6. no formal Stage 5, calibration, readout, or Web readiness is claimed;
7. a human-readable report, independent review, known-limitations section, and development-log entry exist.

## Open questions

There are no implementation-blocking product choices left in this draft. Independent design review may still
require changes before the design is frozen. Enabling a physical simulation backend remains separately blocked
by Stage 5 formal-scale qualification and is not an MVP completion criterion.
