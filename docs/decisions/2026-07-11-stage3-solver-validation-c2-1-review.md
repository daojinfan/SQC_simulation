# Stage 3 C2.1/C2.1a Solver Validation Independent Review

Date: 2026-07-11

Decision: APPROVED

Reviewer role: `independent_test_review_ai`

Test task ID: `019f4f47-c038-7fc2-82c1-fbedf2a44364`

## Findings

None. Blocking findings: none.

The earlier C2 run failed before artifact serialization because a diagnostic path emitted a
non-finite value. That run remains classified as `implementation_error`; it is not treated as a
runtime-budget, numerical-convergence, geometry, or Stage 4 conclusion. This review did not run the
formal C2 acceptance profile.

## C2.1/C2.1a Disposition

The implementation now preserves a valid zero level-to-level drift, maps unavailable or non-finite
diagnostics to `null` plus deterministic availability/reason fields, requires `flux_slope_valid is
True`, and requires valid uncertainty inputs for both `resolved` and `geometry_too_weak` outcomes.

`_build_candidate_summary` derives `target_pair_participation_valid` only when both designated
branches contain `target_pair_at_minimum_ok is True`. Missing, false, or non-mapping branch data
fails closed. Character exchange remains an independent predicate and cannot substitute for target
pair participation.

The artifact writer performs a recursive finite preflight without modifying its payload, identifies
the first invalid JSON path, generates canonical bytes before creating the output directory, and
uses a temporary file plus atomic replace. Valid diagnostic failures still write a canonical
artifact and executed notebook; unexpected non-finite payloads leave no partial artifact.

Missing or stale solver approval fails before acceptance analysis. The archived C1.1 approval is
invalid for the new candidate and source tree.

## Representative Checks

```text
Old approval versus new candidate:
  report.ok=false, validated_spec=None, finite reported error

Original P0 minimal forged validation, attacker role, negative p95:
  report.ok=false, validated_spec=None, finite reported error

Zero versus null:
  zero drift remains available and canonical
  missing drift yields U_total_MHz=null, uncertainty_inputs_valid=false,
  unavailable_reasons=[level_to_level_drift_MHz_missing_or_nonfinite]

Invalid slope:
  local slope=null, flux equivalent=null, flux_slope_valid=false,
  deterministic baseline/refined reason, uncertainty gate failed

Target pair independence:
  character_exchange.passed=false with both designated target-pair flags true
  yields target_pair_participation_valid=true
```

## Automated Commands

```text
Targeted finite/null/slope/target-pair/stale-approval/no-partial tests:
  11 passed in 3.72s

Stage 3 safety suite, excluding the Stage 3 output-writing smoke runner:
  202 passed, 1 deselected in 21.70s

Repository suite, excluding the three Stage 1/2/3 output-writing runners:
  307 passed, 3 deselected in 55.23s

PYTHONPYCACHEPREFIX=<system-temp>/sqc_c21_pycache python -m compileall -q src tests scripts
  passed

git diff --check -- src/sqvm/spectrum configs/spectra scripts tests
  passed
```

## Candidate and Regeneration

```text
solver validation candidate:
  638400820D7950EF2147BD579D5A50B6B66D9FA5D5335F14D6F41DB7C2C623EE
dense pilot:
  DB85730B3C3CB8826A14CCE26656D2646EBBA47F773D6E6D7E85A6FA9FD15CBA
acceptance config:
  7F288CBFBCD483FCE948E651B9166307A51BE079DD96A56B165C697CACFBDD39
Stage 3 source tree:
  0E62640C053B30664879988E3B3116E5069385B91FDFB8051BD3C0FF3296D34A
environment:
  40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12
```

The formal candidate is canonical and contains 28 passing cases, exact 4-signature by 7-key
coverage, `failed_cases=[]`, `coverage_complete=true`, and `validation_passed=true`. Its seven
reported aggregates exactly equal independent recomputation and are finite:

```text
max_gap_error_GHz:                 4.334310688136611e-13
max_repeat_gap_error_GHz:          0.0
max_projector_dense_error:         1.4500804135649207e-10
max_projector_repeat_error:        2.2154613367239443e-15
max_participation_dense_error:     5.451195050909519e-13
max_participation_repeat_error:    0.0
max_metric_dense_error_GHz:        4.334310688136611e-13
```

Formal timings are finite, positive, and ordered:

```text
dimension 3375: p50=0.08958844999870053s, p95=0.09189120999944862s
dimension 4275: p50=0.11934404999919934s, p95=0.12679280501179163s
```

One independent system-temporary `generate_solver_validation` run completed in 66.04 seconds.
It reproduced all 28 case IDs, keys, signatures, dimensions, all non-timing scalar/projector/physics
values, all seven aggregates, the normative vector, and all provenance bindings exactly. Timing
values differed normally. The temporary directory was removed and regeneration was not repeated.

## Protection Review

The clarification decision, six frozen documents, Stage 2.1 chain, Hamiltonian config/source, dense
pilot, validation candidate, acceptance config, Stage 3 source, and environment hashes were unchanged
before approval. The previous solver approval was archived byte-for-byte with SHA-256
`E1863CF35C900A7C966148A0A944D10FC527FEB025030FAD1978C2070F19CCA1`; its original review record
remained unchanged. The formal Stage 3 output directory contained no artifact files and was not used.

## Decision

APPROVED. The C2.1/C2.1a solver-validation candidate has no blocking findings and may receive a new
canonical independent solver approval. Formal C2 acceptance remains a separate development-channel
operation and was not run by this reviewer.
