# Stage 7 Entry And Phasing Proposal

- Date: 2026-07-14
- Status: approved for Stage 7.0 implementation; Stage 7.1-7.3 physics gates remain closed
- Scope: calibration-experiment design after the accepted Stage 6 platform runtime

## Decision summary

Stage 7 should remain one roadmap stage but be delivered through four independently reviewed gates:

```text
Stage 7.0  instruction compiler, parameterized-control rebaseline, evidence ledger, fake fixtures
Stage 7.1  model-derived single-qubit bootstrap: spectroscopy, Rabi/X2P, Ramsey, DRAG
Stage 7.2  model-derived coupler flux calibration
Stage 7.3  model-derived CZ coarse scan
```

All Stage 7 and later calibration experiments use QCIS as the sole external control-program language. The
execution boundary is QCIS template -> concrete point QCIS -> typed AST -> logical waveform arrays -> Stage 4.1
effective controls -> Stage 5.1/QuTiP. The earlier Gate/Pulse JSON models are internal compiler structures only,
not a second user-facing instruction language.

This QCIS architecture is user-confirmed on 2026-07-14. The detailed executable subset, formulas, profile,
test vectors, and design authorities are frozen by the companion Stage 7 QCIS design-freeze record.

Only Stage 7.0 design and pure compiler/evidence work may proceed while Stage 5 formal-scale qualification is
closed. Any model-derived simulation scan additionally requires approved Stage 4.1 parameterized control,
Stage 5.1 parameterized evolution, `Stage6ReadinessReport.stage6_ready=true`, the separately frozen Stage 6
physics-backend gate, and a reviewed Stage 7 model-evolution-backend entrance record. Stage 8 remains the entrance
for readout, IQ, shots, assignment, dissipation/noise-derived decay, and measurement claims.

## Required rebaseline gates

The current Stage 4 compiler accepts only its frozen smoke/formal scenario order. The current Stage 5 v0.2
runner rejects formal numerical execution and does not expose a qualified parameterized backend. Stage 7 must
not call internal helpers to bypass these contracts.

1. **Stage 4.1 parameterized-control rebaseline** extracts a public typed schedule compiler while proving the
   accepted Stage 4 frozen scenarios remain byte-identical and all channel, timing, conflict, DAC, latency,
   mixing, clipping, and effective-waveform checks remain active.
2. **Stage 5 formal-scale qualification** completes the already reviewed sparse per-hold formal work.
3. **Stage 5.1 parameterized-evolution rebaseline** exposes a bounded, hash-bound point-execution interface
   over approved Stage 4.1 effective controls, without readout or measurement semantics.

Each rebaseline requires its own plan, design, regression, artifact comparison, independent approval, and
content-hash binding before a Stage 7 model-evolution backend can be registered.

## Calibration bootstrap policy

Gate macros require an immutable accepted calibration snapshot. The current Stage 6 snapshot is explicitly
`accepted=false`, so initial spectroscopy cannot use `X2P`, `Y2P`, or `CZ`.

Stage 7.1 therefore starts with a separately frozen bootstrap policy:

- only explicit bounded QCIS `PLSXY` and `PLS` primitives may consume device priors;
- every prior-derived value is marked `bootstrap_seed`, never calibrated;
- bootstrap runs and fits remain `claim_class=simulation_only`;
- an explicit human accept decision creates a new immutable `accepted_simulation` calibration snapshot;
- `X2P`/`Y2P` become admissible only after their complete required key set is accepted;
- `CZ` becomes admissible only after both single-qubit and coupler prerequisite key sets are accepted.

Human acceptance means "accepted for this simulator configuration". It is not hardware calibration evidence.

## Approved product choices

1. **Delivery order:** use the four phases above, with Stage 7.0 first.
2. **Approval identity for the local CLI:** canonical actor ID plus mandatory reason and typed confirmation;
   cryptographic signatures are deferred until Stage 9 multi-user/Web identity exists.
3. **First executable model-simulation tranche after the gates:** complete q1 spectroscopy, Rabi-amplitude
   bootstrap at one policy-bound duration/width, Ramsey refinement, and DRAG on one append-only lineage, then
   repeat for q2. Only after a qubit's required
   values are accepted may its `X2P`/`Y2P` be materialized. Coupler and CZ remain later phases.
4. **Initial CZ direction:** support only `control=q1,target=q2` in the MVP; reverse direction is a later
   instruction-set version.

The user's 2026-07-14 instruction to complete the design, create a dedicated branch, and direct the AI team to
implement it accepts these four previously recommended choices. That authorization opens Stage 7.0 only; it
does not waive any model-physics entrance gate or authorize Stage 7.1-7.3 numerical scans.
