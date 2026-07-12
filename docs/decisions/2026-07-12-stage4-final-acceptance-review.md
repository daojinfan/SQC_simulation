# Stage 4 Final Acceptance Review

Date: 2026-07-12

Decision: APPROVED

Reviewer role: `independent_test_review_ai`

Blocking findings: none

## Scope and authority

This independent review validates the repaired Stage 4 D1.1 formal candidate against the frozen Stage 4
v0.1 contract and the approved D1 acceptance remediation. It did not run a formal, CLI, smoke, or production
runner and did not modify source, configuration, tests, or the existing formal evidence.

The remediation record is:

```text
docs/decisions/2026-07-12-stage4-d1-acceptance-remediation.md
8FCE19C23C5A6968F7B2F5B7BC0DFF0E6CC36AFDF4C59186CE0B5FADEA622C46
```

## Current trust bindings

```text
Stage 4 design-freeze manifest  99D2DFA48582DC9DBE792AD7157BD71CCC9E0610ADEEEE62791FF6E3EF52FBCD
Stage 4.0 channel approval       5EC04A8D10D1ACB49AFA88B34BD73685737C899835A0A25054313FC2F462310F
Stage 3.1 acceptance approval    5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D
Stage 4 source tree              31A0C256F0BE6EC56A15ACF8687C1F7BCE90E5F1DD60D26A3650BBCC83F49D3A
environment fingerprint          C153A03ED0104690D4F0899A95C0501D76AA5AF6CC379FE3D90F10B6E075538E
formal control config            68B51EB2ABBB7F30A808262CCEC858208BDF91E0C4B0FD416BBBE54423C255BB
formal logical schedule          5D60899971DEF17678F8BD4D1DE92DED977C52755F8F34E39BA263C736439A3B
```

## Formal candidate

The directory was exact-four before approval, with no approval, temporary, partial, or staging entry.

```text
control_signal_artifacts.json  034ED1C593935EEC1049DB2331148CECFCC46FBED44DD7CF00BC5CB3E50081A8
verification.ipynb             A01415F3F24C2BD21BABA23C36AC630452FB40B6CDAD92185A3C4F6371D39DD7
verification_report.json       D7A037F57DE05913FD804346DC55A6C8969D1ECA93C39B03A001B63E5EDC0B4D
run_receipt.json               EE2C2EC510101D82AEF050F845F4D7E443C000B421330522217460A4C343B58E
```

Artifact, report, and receipt are canonical and recursively finite. The notebook matches the immutable
template and a fresh normative replay, has five sequentially executed code cells, and has no error output.
Artifact/report/receipt schemas, final paths, raw hashes, profile, status, checks, blockers, and runtime fields
are cross-consistent. All 17 ordered computational checks pass, `computational_ready=true`,
`acceptance_candidate_ready=true`, `status=ready_for_stage5_review`, and `blocking_reasons=[]`.

Analysis runtime is `0.4383821999945212 s` against `10 s`; total runtime to receipt assembly is
`5.9751293000008445 s` against `60 s`.

## Independent numerical replay

An independent explicit double-loop reconstruction, rather than the production convolution path, covered
all six scenarios and every one of the 11 lanes. It recomputed full FIR convolution from persisted
`reconstructed_V`, applied latency, retained the full tail, and right-padded to common `P`.

```text
maximum delivered-lane difference       0.0 V
maximum effective-coordinate difference 1.3877787807814457e-17
```

The common `N/N_awg/P` formulas, matrix forward maps, Z idle offset, coordinate order, forward-reference
rows, four pulse-frame area rows, four Z target rows, phase proxy, and all global reductions were independently
recomputed. Published area errors remain `5.036013693966542e-6`, `7.975323454905837e-6`,
`2.9258728027552327e-5`, and `1.807762136459055e-5`. Published Z maximum errors remain
`1.9062499999333848e-7`, `2.0263671873799183e-7`, `0.0`, and `3.778076171911948e-7 Phi0`.
The phase proxy remains `0.01957651736909815 rad` and passes its `0.10 rad` limit.

## Fail-closed checks

The public approval builder has the frozen exact signature, is exported from `sqvm.control` and lazily from
`sqvm`, returns but does not write the approval payload, and shares `_validated_candidate_evidence` with the
final validator. Ordinary package import does not load `nbclient`.

Executed representative attacks rejected valid-range DAC-code changes; synchronized XY delivered/effective/
metric changes; synchronized Z delivered/absolute-flux/metric changes; missing, reordered, wrong-length, and
non-finite arrays; forward-row boolean/message tampering; smoke approval; missing and outside-repository review
records; existing approval; and stale source, config, schedule, receipt, or provenance. Builder and validator
both fail closed on the applicable cases.

## Test evidence

```text
focused Stage 4: 181 passed; one transient local ZMQ port-permission error
isolated retry of that exact test: 1 passed
safe repository suite: 529 passed, 3 deselected, 4 warnings
external-cache compileall: passed
git diff --check: passed
workspace __pycache__ directories / .pyc files: 0 / 0
```

The three deselected tests are the Stage 1, Stage 2, and Stage 3 production-output runners. No Stage 4
formal, CLI, smoke, or production runner was executed during independent review.

## Decision

The Stage 4 computational candidate and its publication evidence are approved. A canonical approval may be
built only from the current exact-four bytes and this current review record. Stage 5 becomes ready only if the
resulting exact-five directory passes `validate_stage4_acceptance_approval` with every ordered check passing,
`acceptance_approval_valid=true`, no blockers, and `stage5_ready=true`.
