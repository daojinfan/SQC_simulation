# Stage 7 QCIS Design Freeze

- Date: 2026-07-14
- Decision: approved for Stage 7.0 implementation
- Branch: `codex/stage7-qcis-calibration`
- Instruction profile: `qcis_stage7_calibration_v1`

## Frozen authority set

The following SHA-256 values bind the complete Stage 7.0 design authority. Any byte change invalidates this
record and requires a new reviewed freeze record.

```text
39965608A8A206CF41D8CE494129BDDDC12A17F130CF0648C915513B0F6B913B  docs/decisions/2026-07-14-stage7-entry-and-phasing.md
C40AA5D074CCE9A07B1C8D94D31D3D7895EC842495A48B45A0541BEC54AC49AB  docs/stages/07_calibration_experiments_plan.md
1881714C2B0E837B6CFF619765516A87374413A33C06B35226D5D208B98F110A  docs/designs/07_calibration_experiments_design.md
70958D220E3009AE60AB6847565321F6ACCF0BBCA6B67E3E267A5F1D34A60786  docs/designs/07_qcis_compiler_design.md
F833A0C74345DE0AA0BE24D942C704562F6AED7B029604E3E557AFCE007A1F3E  docs/designs/07_qcis_compiler_test_vectors.md
C638A4EE10E0F2D98B20E7A6B0E7ADD6718CADFC3EA00AEFC15F87961FF15338  docs/references/QCIS说明.md
E20669719C605557544C73A15011C82A45D17C996EF6DB8119BEDD5F2E2362A2  docs/references/qcis_source_snapshot.json
```

The repository QCIS reference is byte-identical to the user-supplied source: both are 14192 bytes with SHA-256
`C638A4EE10E0F2D98B20E7A6B0E7ADD6718CADFC3EA00AEFC15F87961FF15338`.

## Approved scope

This freeze opens only Stage 7.0:

```text
strict QCIS template admission and materialization
strict parser and typed AST
QAgent, gate, waveform, clock, compiler, and calibration authority resolution
deterministic logical I/Q/flux waveform compilation
compile verification and byte-oracle replay
deterministic fake fixtures and append-only evidence-ledger foundation
```

Stage 7.1-7.3 model-derived calibration scans remain closed. They require separately approved Stage 4.1,
Stage 5 formal qualification, Stage 5.1, Stage 6 readiness and physics-backend gates, experiment-specific
constants, feasibility evidence, and the Stage 7 model-evolution entrance record.

## Review record

Independent design reviews used the current authority bytes and reported no remaining blocker, high, or medium
finding in their owned domains:

```text
development/API review       thread 019f5c41-1ced-7d52-a65d-0adff0c0de9c
independent test review      thread 019f5c41-4278-7403-87b1-3c23d231444e
physics/numerical review     thread 019f5c41-00f4-7042-a082-2f3682c4fe30
```

The release review identified missing program-source and exact tamper-oracle bindings in an earlier byte set.
The frozen authority set closes both findings: the 284-byte program envelope contains `source`, and the logical,
effective-control, coefficient, template, and authority tamper cases bind exact bytes, SHA-256 values, stable
reason codes, and zero-side-effect requirements. The release-review task did not return a final acknowledgement
because its independent task remained active. This does not reopen the reviewed compiler semantics, but Stage 7.0
release remains prohibited until the release owner independently approves the implemented bytes and evidence.

## Product decisions

The user's instruction to complete this design and direct implementation accepts the previously recommended
four-phase delivery, local actor ID plus reason without signatures until Stage 9, q1-before-q2 calibration
lineage, and q1-to-q2-only CZ MVP direction.

## Change control

Implementation must not invent semantics for reserved QCIS operations, modify units/formulas/timing, weaken a
reason code, add a physics backend, or replace raw-byte equality with tolerance-only comparison. Any such need
is a design change and requires a new versioned decision before code changes.
