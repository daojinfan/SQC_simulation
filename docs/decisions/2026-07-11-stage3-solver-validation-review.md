# Stage 3 Solver Validation Independent Review

Date: 2026-07-11

Decision: APPROVED

Reviewer role: `independent_test_review_ai`

Test task ID: `019f4f47-c038-7fc2-82c1-fbedf2a44364`

## Findings

None. Blocking findings: none.

The original C1 P0 fail-open finding is closed. The validator now checks canonical files and
configured paths; approval identity, role, and artifact hash; dense-pilot, Stage 2.1, config,
source, and environment bindings; the normative vector; exact 4-by-7 coverage; every case's
arrays, blocks, projectors, physics comparisons, and thresholds; failed cases; seven recomputed
aggregates; and finite, positive, ordered p50/p95 timings. A validated solver specification is
created only when the complete error list is empty.

## Independent commands

```text
PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_solver_validation_gate.py
Result: 91 passed in 7.45s

PYTHONPATH=src python -m pytest -q -p no:cacheprovider tests/test_static_spectrum_verify.py -k "solver_validation or validated_solver_spec"
Result: 5 passed, 19 deselected in 4.20s

PYTHONPATH=src python -m pytest -q -p no:cacheprovider <nine Stage 3 test modules>
Result: 192 passed in 25.67s

PYTHONPATH=src python -m pytest -q -p no:cacheprovider -k "not test_vscode_runner_smoke and not test_vscode_stage1_runner_smoke"
Result: 296 passed, 3 deselected in 54.59s

PYTHONPYCACHEPREFIX=<system-temp>/sqc_c11_pycache python -m compileall -q src tests scripts
Result: passed

git diff --check -- src/sqvm/spectrum configs/spectra scripts/run_stage_03_static_spectrum.py tests
Result: passed
```

## Representative attacks

The original minimal noncanonical forged validation, attacker reviewer role, and negative p95
attack returned `report.ok=false`, `validated_spec=None`, and a finite reported solver error.
A canonical copy of the real candidate with `coverage_complete=false` also returned
`report.ok=false`, `validated_spec=None`, and the expected coverage error.

The automated gate suite covers the remaining malformed, missing, non-finite, wrongly typed,
self-declared, coverage, case, aggregate, binding, canonical, approval, and timing cases.

## Independent 28-case regeneration

`generate_solver_validation` was run once against the accepted config and dense pilot in a system
temporary directory. It completed in 66.05 seconds and the temporary directory was removed.
Timing values differed as expected; no repeat was requested or performed.

The regenerated evidence contained 28 cases: four cutoff signatures, each at the same seven required
flux keys. Case IDs, keys, signatures, dimensions, scalar and projector gates, all seven aggregates,
`failed_cases=[]`, `coverage_complete=true`, the normative vector, and all provenance hashes matched
the formal candidate. The only non-timing text difference was absolute versus repository-relative
config/pilot path spelling from the invocation; both resolved to the same files and hashes.

## Candidate evidence

```text
validation artifact SHA-256: CA2C83CC569D1D014E310AB98FB743EE74426341A1D9C020C2C005BE7FEAE85D
dense pilot SHA-256:         DB85730B3C3CB8826A14CCE26656D2646EBBA47F773D6E6D7E85A6FA9FD15CBA
acceptance config SHA-256:   7F288CBFBCD483FCE948E651B9166307A51BE079DD96A56B165C697CACFBDD39
Stage 3 source SHA-256:      7B5D060A8A7B9FAE450CEEA7E5383FC51CA0133246E5552A35C88078800D2D80
environment SHA-256:         40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12
```

The formal candidate is canonical. All 28 cases passed, `failed_cases=[]`, and
`coverage_complete=true`. The reported aggregates exactly equal independent recomputation:

```text
max_gap_error_GHz:                 4.334310688136611e-13
max_repeat_gap_error_GHz:          0.0
max_projector_dense_error:         1.4500804135649207e-10
max_projector_repeat_error:        2.2154613367239443e-15
max_participation_dense_error:     5.451195050909519e-13
max_participation_repeat_error:    0.0
max_metric_dense_error_GHz:        4.334310688136611e-13
```

Formal timing summaries are finite, positive, and ordered:

```text
dimension 3375: p50=0.0868590000027325s, p95=0.09258206500380765s
dimension 4275: p50=0.11790045000088867s, p95=0.12657775999468868s
```

## Protection review

The six frozen documents, Stage 2.1 approval/manifest/artifact chain, Hamiltonian config and source,
dense pilot, formal validation candidate, acceptance config, Stage 3 source, and environment hashes
were unchanged before approval. No formal Stage 3 acceptance output existed.

## Decision

APPROVED. The solver-validation candidate has no blocking findings and may receive the canonical
independent approval bound to this review record.
