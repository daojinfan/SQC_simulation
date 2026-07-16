# Stage 7.1 Initial Detailed Design: Model Evolution Backend Entrance

- Status: initial draft, not an execution authority
- Depends on: Stage 6 runtime, QCIS v0.3, Stage 4.1 verified control, Stage 5.1 verified evolution
- Opens no calibration experiment or recommendation by itself

## 1. Purpose

The next tranche connects the accepted experiment platform to the verified model-evolution path without letting
Stage 6, a calibration definition, or a QCIS program bypass any compiler, control, physics, or publication gate.
It registers one capability-scoped backend and proves one bounded model point from request admission through a
verified Stage 5.1 result handle. Spectroscopy, Rabi, Ramsey, DRAG, coupler, and CZ interpretation remain outside
this entrance.

The backend boundary is:

```text
Stage 7 point
  -> concrete QCIS v0.3
  -> Stage 4.1 published control + VerifiedControlHandle
  -> Stage 5.1 coefficient artifact + VerifiedCoefficientHandle
  -> isolated QuTiP worker
  -> Stage 5.1 evolution artifact + VerifiedEvolutionHandle
  -> Stage 6 point evidence
```

## 2. Capability Registration

The first backend capability is exactly:

```text
backend_id                 sqvm.stage51.qutip.closed_system.v1
backend_kind               model_evolution
program_profile            qcis_stage7_calibration_v3
control_profile            stage_04_1_parameterized_control_v1
evolution_profile          stage_05_1_verified_control_v1
readout_capability         false
measurement_capability     false
recommendation_eligible    false
```

Registration binds raw hashes for the backend adapter source, QCIS authority, Stage 4.1 authority, Stage 5.1
physics authority, Stage 6 runtime authority, environment snapshot, and publication policy. A caller cannot
override solver options, tensor order, frame semantics, sample period, units, or output schemas.

## 3. Admission Truth Table

A model point may reserve runtime resources only when every term is true:

```text
request and point schema valid
AND backend capability exact
AND QCIS v0.3 compiler authority valid
AND Stage 4.1 production authority and approval valid
AND Stage 5.1 production authority and approval valid
AND Stage 6 physics-backend gate valid
AND Stage 7 model-evolution entrance approval valid
AND source/environment/publication bindings exact
```

The existing Stage 5 formal-scale gate remains separate. The smoke-qualified Stage 5.1 backend may be used only
for explicitly approved bounded entrance pilots until formal-scale qualification is accepted. A pilot cannot
publish a calibration recommendation or accepted calibration state.

## 4. Point Contract

Each admitted point contains exact keys:

```text
point_id
scan_coordinates
concrete_qcis_binding
compiler_authority_binding
control_authority_binding
evolution_authority_binding
backend_capability_binding
parent_calibration_binding
```

`scan_coordinates` are metadata owned by the experiment definition. They never enter the Hamiltonian or worker.
Only the compiled QCIS-derived control handle can affect numerical evolution. The adapter rejects direct logical
arrays, AWG arrays, mapper objects, readout fields, target gates, target states, and fit parameters.

## 5. Execution Sequence

1. Load and verify the Stage 7 request, definition, backend registration, and parent calibration snapshot.
2. Expand the point table deterministically and freeze traversal order.
3. Compile concrete QCIS v0.3 and publish the Stage 4.1 control artifact.
4. Reopen the artifact with the independent Stage 4.1 verifier and obtain a process-local handle.
5. Admit the handle into Stage 5.1 and publish the coefficient artifact.
6. Launch the isolated Stage 5.1 worker with only coefficient and physics authority paths.
7. Independently replay and publish the evolution artifact.
8. Bind the verified evolution handle into Stage 6 point evidence.
9. Emit the point terminal event only after all upstream receipts and hashes verify.

No successful point is emitted for a timeout, cancellation, crash, stale authority, partial artifact, nonfinite
result, replay mismatch, publication conflict, or unsupported instruction.

## 6. Stage 6 Adapter

The adapter implements a capability-specific point executor. It does not modify the Stage 6 request schema or
generic scheduler. Its durable result contains references, not copied physics arrays:

```text
control_id
control_manifest_sha256
coefficient_plan_id
coefficient_manifest_sha256
evolution_result_id
evolution_manifest_sha256
evolution_receipt_sha256
physics_authority_id
replay_fidelity
```

Raw states and observables remain owned by the Stage 5.1 artifact. Stage 6 catalogs only verified references and
point lifecycle evidence.

## 7. Failure Mapping

Stage 5.1 stable failure categories are preserved in point evidence. Stage 6 adds only lifecycle context:

```text
admission failure       -> point rejected before reservation
worker timeout/crash    -> point failed, no result handle
numerical/replay failure-> point failed, no physics claim
publication conflict    -> point conflict, existing target never overwritten
cancellation            -> point cancelled, worker terminated, staging removed
```

Failure details may be recorded for diagnostics; scheduling and callers depend on stable categories only.

## 8. Verification

Independent entrance verification must cover:

- approved QCIS v0.3 -> production Stage 4.1 handle -> production Stage 5.1 result;
- negative time origin, FIR tail, X/X12 coexistence, and named q1/c/q2 mapping;
- timeout, crash, cancellation, partial output, target-exists, and concurrent publication;
- control, coefficient, authority, result, manifest, report, and receipt single-byte tamper;
- Windows junction/reparse and POSIX symlink no-follow behavior;
- same-environment result byte determinism and exact observable replay;
- proof that scan coordinates, readout, target gates, and fit code never enter the worker.

## 9. Remaining Gates

This draft cannot be frozen until the following exist:

1. A versioned production Stage 4.1 authority/context/approval corpus and a production handle fixture.
2. A Stage 6 backend registration schema amendment and independent storage/recovery review.
3. A Stage 7 model-evolution entrance approval binding all upstream authorities.
4. A decision separating bounded smoke entrance pilots from formal-scale calibration scans.
5. Real subprocess timeout/crash and cross-platform publication qualification evidence.

After these gates pass, the first experiment-specific detailed design is q1 spectroscopy. Rabi, Ramsey, DRAG,
q2 calibration, coupler calibration, and CZ calibration follow in that order and each owns separate scan, fit,
acceptance, and recommendation authorities.
