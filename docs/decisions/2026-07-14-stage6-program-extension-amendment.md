# Stage 6 Program Extension Amendment

Date: 2026-07-14

## Decision

Stage 6 request schema `0.1` adds the exact root field `program`, whose only MVP value is `null`. This reserves a
stable, canonical, hash-bound location for a future versioned experiment instruction program while failing
closed against premature instruction execution, arbitrary code, or import paths.

Stage 7 may enable a non-null program only through a new request schema and a separately frozen
`ExperimentInstructionSet`. Its architecture is fixed to two levels:

1. calibrated Gate/Macro IR such as `X2P`, `Y2P`, and `CZ`;
2. Pulse IR such as `PLXY`, `PULSE`, `WAIT`, `FRAME_CHANGE`, and `BARRIER`.

Typed scan references bind Stage 6 point coordinates into the program. Macro expansion binds an immutable
accepted calibration snapshot. Pulse compilation binds compiler/instruction versions, units, timing, channels,
frames, expanded Pulse IR, Stage 4 logical controls, and effective waveform hashes before a physical backend may
consume the resulting `BackendCommand`.

## Stage 6 acceptance consequence

MVP must reject every non-null `program`. It must also demonstrate the registry extension boundary with a second
test-only synthetic experiment whose request is admitted and scan is expanded without backend dispatch or any
change to runtime-owned scan, lifecycle, storage, journal, verification, or catalog code.

## Non-claims

This amendment does not enable any instruction, physical backend, Stage 5 execution, calibration operation,
readout model, plugin system, or Web API. All original platform-only and physics-readiness gates remain in force.

## Freeze status

This amendment changes the byte-bound Stage 6 plan and detailed design. The 2026-07-14 original design-freeze
record is therefore superseded for implementation authority only after the amended files receive a new
hash-bound independent review.
