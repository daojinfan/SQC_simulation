# Stage 3 Formal Acceptance Diagnostic Review

Date: 2026-07-11

Reviewer role: `independent_test_review_ai`

Test task ID: `019f4f47-c038-7fc2-82c1-fbedf2a44364`

Decision:

```text
ANALYSIS APPROVED
STAGE 3 GATE REJECTED
STAGE4 REJECTED
```

## Review Finding

No implementation, provenance, serialization, or artifact-integrity finding was identified. The
formal result is a trustworthy and contract-complete Stage 3 failure diagnostic. Its failed gate is
not itself a reason to reject the artifact's validity.

The formal acceptance computation was not rerun by this reviewer. No approved Stage 3 acceptance
JSON was created.

## Formal Files

```text
output/stage_03_static_spectrum/static_spectrum_artifacts.json
SHA-256: 7D6D3D8DAD7187217F0559D324653B3E5A87E3AE623FA94A0F90715D0A4F5DE2

output/stage_03_static_spectrum/verification.ipynb
SHA-256: 0A5CAC7779ABECBEF5DFF8A365AAEE74EEAF30AE240EAB1E7EFBC34FBE8F578E
```

The formal output directory contained exactly these two files. The artifact is canonical JSON,
has schema/type/version `0.1/stage_03_static_spectrum/0.1`, and contains no NaN or infinity at any
recursive path. Unavailable diagnostic values use `null` together with explicit validity/status and
deterministic reason fields.

## Provenance and Solver Binding

The artifact's Stage 2.1 approval, manifest, Stage 2 artifact, Hamiltonian config/source, solver
validation candidate, solver approval, solver review, Stage 3 source tree, acceptance config, and
environment bindings matched current raw bytes and loaded validation reports.

```text
Stage 2.1 approval:  CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9
Stage 2.1 manifest:  4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D
Stage 2 artifact:    DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66
solver validation:   638400820D7950EF2147BD579D5A50B6B66D9FA5D5335F14D6F41DB7C2C623EE
solver approval:     8AD440A154326FE596DFF99AEB64D3EEFB9706698BB4BD58797B2DABFDBD9786
solver review:       4A697E961B7B7438D9C839B616D3303F6BD14BDAF1E1457F35DFDE7540AB2560
Stage 3 source tree: 0E62640C053B30664879988E3B3116E5069385B91FDFB8051BD3C0FF3296D34A
acceptance config:   7F288CBFBCD483FCE948E651B9166307A51BE079DD96A56B165C697CACFBDD39
environment:         40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12
```

The Stage 2 dense 12-gap rebuild compared 12 states with maximum absolute difference `0.0 GHz`.
The solver report is valid, error-free, acceptance-eligible, and records finite validated solver
error `4.334310688136611e-13 GHz`.

## Notebook and Baseline

The notebook reads only `static_spectrum_artifacts.json`. All 6 code cells have execution count 1,
there are no error outputs, and the displayed provenance, eigensystem, assignments, participation,
metrics, convergence, flux scan, crossings, runtime, warnings, checks, and stage gate come from the
reviewed artifact.

The baseline uses cutoffs `(7,7,7)`, Hilbert dimension 3375, and validated eigsh. Idle N-to-N+2
convergence passed all frozen tolerances:

```text
max frequency drift:     0.09736560983952813 MHz <= 0.50 MHz
max anharmonicity drift: 0.7909410039346199 MHz <= 1.00 MHz
max ZZ drift:            1.0317641851997905e-05 MHz <= 0.01 MHz
```

## Flux Evidence

The scan contains 80 unique, ordered Decimal12 flux keys from `0.200000000000` through
`0.450000000000`. All 240 branch-participation rows contain finite, nonnegative q1/c/q2 fractions.
Candidate-specific grids are unique, ordered, and inside the configured range. The scan records 80
solver evaluations and 18 cache hits; the runtime total includes one separate baseline solve.

### q1-c

The three refinement levels are consistent with the stored evidence:

```text
L0: minimum 0.397916666667, splitting 54.9579091419119 MHz, continue
L1: minimum 0.395833333333, splitting 6.829394024308044 MHz, continue
L2: minimum 0.393750000000, splitting 138.5106870680275 MHz, boundary
```

The final minimum is at the grid boundary. Right-hand character evidence and a final bracket are
unavailable, no q1/c/q2 cutoff-refinement rows are produced, and uncertainty values are `null` with
`uncertainty_inputs_valid=false` and explicit reasons. Reapplying the frozen classifier returns
`numerically_unconverged`, which has priority over the later boundary/continuity/character statuses.

### c-q2

The scan converged at `0.390243333334` with splitting `6.968262916615231 MHz`. Character exchange,
target-pair participation, bare-detuning sign change, interior/resolution evidence, all three cutoff
refinements, and uncertainty inputs are valid.

Independent recomputation from the three refinement rows exactly matched the artifact:

```text
U_cutoff_MHz = 0.00014322841224156946
U_flux_MHz   = 0.0007537379673127839
U_level_MHz  = 0.0013118855903826443
U_total_MHz  = 0.0013118855903826443
relative uncertainty = 0.000188265799680802
significance ratio   = 5311.639191480685
```

The final branch continuity is `0.7033494766689148`, below the frozen `0.90` threshold. Reapplying
the frozen priority classifier therefore returns `low_continuity`; this is not a
`geometry_too_weak` conclusion.

## Runtime, Checks, and Gate

```text
wall time:               31.695474300009664 s
solver evaluations:      81
scan points:             80
cache hits:              18
projected remaining:     51.14826501391071 s
projected total:         82.84373931392037 s
budget:                  1800 s
conservative ceiling:    509 solves
budget status:           within_budget
```

The projected time and 509-solve ceiling were independently recomputed from the dimension-specific
p95 values and job counts and matched exactly. There are 13 checks; only `stage4_ready` is false.

The final gate is contract-consistent:

```text
analysis_completed = true
status = unresolved_crossing
stage4_ready = false
blocking_reasons = [q1-c:numerically_unconverged, c-q2:low_continuity]
```

## Safe Verification

```text
Repository safety regression, excluding the three output-writing runners:
  307 passed, 3 deselected in 56.28s

compileall with external PYTHONPYCACHEPREFIX:
  passed

git diff --check:
  passed
```

The isolated smoke CLI failure-path test confirmed `python -m sqvm verify-spectrum` maps
`report.ok=false` to exit code 1. The VSCode acceptance runner test only inspected its formal paths;
the runner was not executed. A naturally completed VSCode process is not used as the Stage 3 gate.

All protected hashes, including both formal files, the clarification decision, six frozen documents,
Stage 2.1 chain, Hamiltonian config/source, solver candidate/approval/review, acceptance config,
Stage 3 source tree, and environment, were unchanged by review.

## Outcome and User Decision

```text
A. Stage 3 implementation and formal diagnostic delivery: COMPLETE
B. Stage 3 acceptance gate: NOT PASSED
C. Stage 4 permission: REJECTED
```

The user/project manager must choose a versioned next step. Valid options include:

1. Keep the frozen acceptance contract and authorize a new Stage 3 iteration to address the q1-c
   boundary/refinement failure and c-q2 continuity evidence, followed by fresh provenance,
   solver revalidation when source/config changes, and one new formal acceptance run.
2. Accept this artifact as the terminal diagnostic for the current design, keep Stage 4 blocked,
   and return to device/scan/branch-tracking design analysis. The current evidence does not authorize
   a `geometry_too_weak` classification.
3. Change a frozen grid, tolerance, continuity threshold, or status contract only through an explicit
   versioned design review. This independent artifact review does not waive any frozen gate.
