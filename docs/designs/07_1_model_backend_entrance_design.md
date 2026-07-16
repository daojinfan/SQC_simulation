# Stage 7.1 Detailed Design: Bounded Model-Evolution Entrance

- Status: implementation authority for the bounded entrance only
- Depends on: QCIS v0.3, production Stage 4.1 authority, production Stage 5.1 authority
- Does not approve: Stage 6 schema 0.2, a physics backend registration, a calibration experiment, analysis,
  recommendation, decision, setting mutation, readout, or hardware execution

## 1. Decision and scope

Stage 7.1 proves one real, bounded path from an already verified QCIS v0.3 compilation through Stage 4.1
electronics and Stage 5.1 closed-system evolution. It publishes an independently verifiable entrance evidence
artifact. The evidence qualifies the adapter boundary; it is not a calibration result.

The default Stage 6 registry remains platform-only. The repository does not yet contain the Stage 5 formal-scale
approval or the separate Stage 6 physics-backend approval required by the Stage 7 design. Consequently this
stage must not register a physical backend in `get_builtin_backend_registry()` and must not reinterpret request
schema `0.1`. A future schema `0.2` adapter may consume this entrance only after those gates exist.

```text
verified QCIS v0.3 compilation
  -> production Stage 4.1 context admission
  -> published and reopened VerifiedControlHandle
  -> production Stage 5.1 physics admission
  -> published and reopened VerifiedCoefficientHandle
  -> isolated QuTiP worker plus independent replay
  -> published and reopened VerifiedEvolutionHandle
  -> Stage 7.1 entrance evidence and receipt
```

No high-level gate, direct logical/AWG array, mapper, target state, readout field, fit parameter, or solver option
may enter below its owning authority boundary.

## 2. Capability descriptor

The entrance exposes a descriptor but not a registered Stage 6 backend:

```text
backend_id                 stage51_qutip_closed_system_v1
backend_kind               model_evolution
program_profile            qcis_stage7_calibration_v3
control_profile            stage_04_1_parameterized_control_v1
evolution_profile          stage_05_1_verified_control_v1
qualification_scope        bounded_smoke_only
response_kind              model_evolution_evidence_v1
measurement                false
readout                    false
formal_scale_qualified     false
calibration_eligible       false
recommendation_eligible    false
stage6_registered          false
```

The descriptor is exact and caller-immutable. The entrance rejects any attempt to request measurement, readout,
formal-scale, calibration, recommendation, arbitrary observables, or hardware capabilities.

## 3. Bounded envelope

The entrance authority freezes this exact resource envelope:

```text
max_point_count            1
max_logical_sample_count   64
max_logical_duration_ns    32.0
max_effective_sample_count 96
max_effective_duration_ns  48.0
dt_ns                      0.5
max_worker_wall_seconds    180.0
allowed_pilot_kind         compiled_qcis_single_point
allowed_cutoffs            q1=1,c=1,q2=1
solver_profile             stage_05_1_smoke
```

The adapter checks point count, plan clock, logical sample count/duration, exact profile, timeout, and Stage 5.1
authority before creating a staging directory. It checks the effective sample count/duration again after the
fixed Stage 4.1 latency/FIR tail and before launching a worker. A caller may choose a lower timeout but
cannot increase an authority limit. Memory is bounded indirectly by the fixed `(1,1,1)` Hilbert cutoff and
sample count; no user-selected cutoff or observable is accepted.

The public independent replay is structurally bounded by the same cutoff and effective sample envelope but has
no hard wall-clock guarantee in this standalone entrance. A future Stage 6 `0.2` hard deadline requires the
verifier itself to run in a runtime-owned terminable subprocess.

The point identity is a non-empty ASCII identifier supplied by the enclosing experiment and is bound into the
adapted Stage 4.1 plan and entrance evidence. The pilot accepts no parent calibration: `parent_calibration_binding`
is exactly `null`.

## 4. Authority truth table

A pilot can begin only when all terms are true:

```text
entrance design raw hash exact
AND entrance authority canonical and approved
AND capability descriptor exact
AND bounded envelope exact
AND QCIS v0.3 compilation independently verifies
AND QCIS plan profile and authority hashes independently verify
AND production Stage 4.1 authority and approval verify
AND production Stage 5.1 authority and approval verify
AND Stage 4.1 and Stage 5.1 source snapshots verify
AND publication policies are atomic no-replace
AND parent_calibration_binding is null
```

Any false, missing, stale, wrong-version, or capability-mismatched term fails before worker execution. This truth
table deliberately omits Stage 5 formal-scale and Stage 6 physics-backend approval because their absence keeps
`stage6_registered=false`; it does not silently treat them as passed.

## 5. Execution and publication

The public pilot API accepts only a typed `QCISCompilation`, point ID, repository root, repository-contained
output root, and bounded timeout. It performs:

1. independent QCIS compilation verification and envelope admission;
2. production Stage 4.1 context construction from repository authorities;
3. QCIS-to-Stage4.1 adaptation, electronics compilation, atomic control publication, and independent reopen;
4. production Stage 5.1 context admission, coefficient publication, and independent reopen;
5. isolated worker execution, independent replay, evolution publication, and public independent reopen;
6. entrance evidence, manifest, verification report, and receipt construction;
7. whole-point atomic no-replace publication followed by public entrance verification.

All nested artifacts are first produced under a point staging root. A failure removes the staging root and emits
no entrance receipt. Existing targets are never overwritten. Nested Stage 4.1 and Stage 5.1 receipts remain the
owners of their arrays and numerical claims.

## 6. Evidence contract

The entrance evidence has exact fields:

```text
schema_version
artifact_type
artifact_version
status
evidence_id
point_id
capability
claim_envelope
parent_calibration_binding
qcis_binding
control_binding
coefficient_binding
evolution_binding
authority_binding
```

The claim envelope is exact:

```text
response_kind              model_evolution_evidence_v1
claim_class                bounded_closed_system_smoke_simulation
measurement                false
readout                    false
formal_scale_qualified     false
calibration_eligible       false
recommendation_eligible    false
```

Durable bindings contain IDs and raw hashes only:

```text
concrete_qcis_sha256
ast_sha256
trace_sha256
plan_authority_sha256
control_id
control_manifest_sha256
control_receipt_sha256
coefficient_plan_id
coefficient_manifest_sha256
coefficient_receipt_sha256
evolution_result_id
evolution_manifest_sha256
evolution_receipt_sha256
physics_authority_id
replay_fidelity
```

`replay_fidelity` is evidence that the isolated result and independent replay agree. It is not gate fidelity,
process fidelity, XEB fidelity, calibration quality, an optimizer objective, or an experimental measurement.
Populations, leakage, norm error, states, projectors, and solver diagnostics remain only in the Stage 5.1
artifact. They are not copied into entrance evidence or a Stage 6 response.

## 7. Failure semantics

Stable Stage 4.1 and Stage 5.1 failure codes are preserved as causes. The entrance adds only these categories:

```text
ENTRANCE_AUTHORITY_INVALID
CAPABILITY_NOT_APPROVED
BOUNDED_ENVELOPE_EXCEEDED
QCIS_COMPILATION_INVALID
UPSTREAM_CONTROL_FAILED
UPSTREAM_EVOLUTION_FAILED
EVIDENCE_PUBLICATION_CONFLICT
EVIDENCE_VERIFICATION_FAILED
```

Timeout, crash, non-finite output, replay mismatch, partial artifact, stale authority, unsafe path, and target
conflict produce no successful entrance evidence. Cancellation is not claimed by the standalone pilot; Stage 6
schema `0.2` must own cancellation propagation when its physics gate is approved.

## 8. Independent verification

The public verifier accepts the published point directory and the original typed QCIS compilation. It:

- verifies the entrance authority and exact capability/envelope;
- inventories without following symlinks, junctions, or other reparse points;
- verifies the evidence/manifest/report/receipt hash graph;
- independently reopens Stage 4.1 control and Stage 5.1 coefficient artifacts;
- invokes the public Stage 5.1 evolution verifier, which performs a fresh numerical replay;
- recomputes every QCIS, control, coefficient, evolution, and authority binding;
- rejects any raw physics array or recommendation/calibration field in the entrance evidence.

Fast tests cover authority drift, admission limits, capability immutability, evidence schema, hash tampering,
no-recommendation, path safety, and Stage 6 schema `0.1` regression. One long test covers the real production
chain. Worker timeout/crash tests use the real subprocess boundary but do not repeat the full successful replay.

## 9. Deferred Stage 6 schema 0.2

Stage 6 schema `0.1` and its fake dataset remain byte-compatible and unchanged. The future `0.2` work requires
an explicit version adapter for request loading, point results, multi-variable datasets, claim envelopes,
manifest/report/receipt verification, recovery, and catalog rebuild. It must not branch conditionally inside the
`0.1` verifier.

Registration additionally requires:

1. Stage 5 formal-scale approval;
2. `Stage6ReadinessReport.stage6_ready=true`;
3. a separate Stage 6 physics-backend authority;
4. a Stage 7 model-evolution entrance approval referencing accepted qualification evidence;
5. an experiment-specific observable and result schema.

Only after all five gates exist may `stage6_registered` become true. Recommendation eligibility remains a
separate, experiment-specific approval and cannot be inherited from this entrance.
