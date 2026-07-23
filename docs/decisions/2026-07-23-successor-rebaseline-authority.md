# Successor Rebaseline Authority

- Date: 2026-07-23
- Status: authorized work package; not a generated approval or selector activation
- Authorization source: the user's explicit instruction, "用户已批准 successor rebaseline"

## Decision

The authorization permits the complete successor chain required by section 5.3 of
`docs/designs/07_1_10_development_baseline_stabilization_design.md`.  It does not
permit changing a frozen v1 hash, calling a successor result v1 evidence, or
switching a current selector before the independent verifier accepts a complete
v2 manifest.

The required order remains: Stage 2/2.1 v2; Stage 3/3.1 v2; Stage 4 receipt,
Notebook policy, candidate, review, and approval v2; then Stage 5.1 and Stage 6
source/environment successors; finally a versioned selector. The verifier requires
the five machine approvals `stage_02_1 -> stage_03 -> stage_03_1 -> stage_04 ->
stage_04_0`, each independently reviewed and raw-hash chained. Stage 2.1 binds
the final source identity; Stage 5.1 then directly binds Stage 4.0. The verifier
is deliberately a post-generation gate, not an authority generator.

## Frozen Predecessor

The predecessor remains v1 and is preserved by raw bytes:

| Item | Raw SHA-256 |
| --- | --- |
| Stage 5.1 source snapshot | `A61531AD7207E7E0A472BC236B0981DDE250550C00ACF26FCAAD508D74B07373` |
| Stage 5.1 environment snapshot | `083493B10B20DDC3E100AB50F5D5776B7E68AB79A5E662CF6CC54A9D96154346` |
| Stage 5.1 physics authority | `908A814D0CD8F71CB7DFFBD2FAFBDEAF603A8250C8FDEEBBC1F90375F6089EE3` |
| Stage 5.1 physics approval | `5708689EFE5B4C06350D9CB65F10D05A2C98B0840EB1DCB12EA240E915269DCB` |
| Stage 6 fixture authority | `E55E955E54204DDB447D98830ADB76D161B3CA05965F7FF9631FB9A3238D864C` |
| Stage 6 fixture approval | `70D268BCE6DAFC1F49AB1E3A9BE9BEC82B32099E148517B2EAC30B50D071A445` |
| Stage 6 legacy fixture provenance | `03FB4C7724556CC56CF9D2D9ED61AC8840A2EB8DDB6C889D829C6DE192F67688` |

The historical v1 execution baseline is **not recoverable from successor source
bytes**.  Its old interpreter, kernel, source closure, and run inputs are not a
v2 execution environment.  It may only be byte-verified through its frozen
records.  A current test or successor run must never be labeled, replayed, or
published as old v1 evidence.

## Successor Content And Environment

No v2 authority/approval transaction is present in this scoped change, so no
successor is activated here.  A candidate becomes eligible only when its manifest
contains `successor_content_sha256`, calculated as SHA-256 of canonical JSON for
`upstream`, `stage51`, `stage6`, `generation_environment`, and `selector`.  The hash binds
the four successor file hashes for each stage and cannot be substituted with any
v1 hash or filename.

The approved current-test **lock target** is recorded separately from historical
evidence: CPython 3.12.10, NumPy 2.5.1, SciPy 1.18.0, and QuTiP 5.3.0; input lock SHA-256
`7094D95F102F93BE54A036DEC7C181FFA8DE03879B4F1C39CCA803B05A329955`;
Windows lock SHA-256
`22F481D0C6C5C13D7CBF25B14331444378F5549D20D694F1748340A0E917D55A`;
Linux lock SHA-256
`B4A6C5ADC7FC2FEE8A34B75173B0C0A8FAF9630838A19103D11A41BC2223F2DF`.
The local executor observed on this review is CPython 3.12.10 on Windows
10.0.19045 with NumPy 2.4.6, SciPy 1.17.1, and QuTiP 5.3.0. It is therefore
not a lock-qualified successor generation environment until the approved lock is
installed. Neither environment makes a claim of reproducing a historical v1
Notebook or physical-evidence run.

## Acceptance Boundary

`tools/verify_successor_rebaseline.py` fails closed unless a manifest binds new
Stage 5.1 source/environment/authority/approval files, new Stage 6 provenance
files, an approved independent reviewer, a v2 selector for new runs, and the
content hash above.  It rejects links, path traversal, byte drift, incomplete
source snapshots, v1 filenames/hashes in a successor, and a bad content hash.

After a non-implementer runs the positive, tamper, and old-byte-match checks from
an isolated directory, a separate configuration transaction may activate the v2
selector.  This record itself does not mint approval evidence and does not modify
historical evidence.
