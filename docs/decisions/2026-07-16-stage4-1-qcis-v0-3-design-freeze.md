# Stage 4.1 And QCIS v0.3 Design Freeze

- Date: 2026-07-16
- Decision: accepted for implementation
- Approval basis: user-confirmed detailed-design decisions in the active project thread
- Scope opened: QCIS v0.3 drive-phase correction and Stage 4.1 parameterized control only

## Frozen authorities

```text
docs/designs/04_1_parameterized_control_design.md
SHA256 FAE6F72601FDF86B50118C1684C73E9B743F32F5497625B4A9317CC9A9D77D5E

docs/designs/07_qcis_compiler_v0_3_phase_amendment.md
SHA256 2F7C7666A17B887FBCBB147D34549AD8BA60F877073682940BDCC2AD15CAB1C8
```

Any byte change to either authority invalidates this record and requires a new review/hash record before the
changed behavior becomes an implementation authority.

## Authorized implementation

1. Preserve all QCIS v0.2 byte oracles and historical behavior.
2. Add QCIS v0.3 reference-frequency evidence and absolute-time detuning-phase compilation.
3. Add strict v0.3 baseband admission, X/X12 coexistence, replay evidence, and byte/tamper tests.
4. Add the Stage 4.1 typed plan/context/result contracts and parameterized electronics compiler.
5. Preserve accepted Stage 4 public behavior and prove same-environment byte compatibility.
6. Add full per-point control artifacts, independent replay verification, and process-local
   `VerifiedControlHandle` construction.
7. Use the existing independent AI team for implementation, bounded reviews, integration, and verification.

## Gates that remain closed

This decision does not authorize:

- Stage 5.1 Hamiltonian coefficient implementation or parameterized QuTiP execution;
- a non-null Stage 6 request `0.1` program or a Stage 6 physics backend;
- Stage 7.1-7.3 model-derived scans, recommendations, or calibration decisions;
- readout, IQ, shots, assignment, noise, dissipation, or measurement claims;
- mutation or reinterpretation of QCIS v0.2 or accepted Stage 4 evidence.

Opening any listed gate requires its separately reviewed design, regression evidence, and approval record.

