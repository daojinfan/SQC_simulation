# Stage 2.1 Rebaseline Independent Review

Date: 2026-07-11

Decision: APPROVED

Reviewer role: `independent_test_review_ai`

Test task ID: `019f4f47-c038-7fc2-82c1-fbedf2a44364`

## Scope and findings

The formal Stage 2.1 candidate, archived legacy artifact, accepted anchor, manifest,
verification notebook, configuration change, provenance, regression tests, and numerical
convergence evidence were reviewed independently. Findings: none. Blocking findings: none.

## Independent commands

```text
PYTHONPATH=src python -m pytest -q -p no:cacheprovider -k "not test_vscode_runner_smoke and not test_vscode_stage1_runner_smoke"
Result: 105 passed, 2 deselected in 36.12s

PYTHONPATH=src python -m sqvm verify-device configs/devices/2q1c2r.yaml --output <system-temp>/stage1
Result: ok=true, 7/7 checks passed

PYTHONPATH=src python -m sqvm verify-hamiltonian configs/hamiltonians/2q1c_charge_basis.yaml --output <system-temp>/stage2
Result: ok=true, 20 checks, no failed error check

PYTHONPYCACHEPREFIX=<system-temp>/sqc_stage21_formal_acceptance_pycache python -m compileall -q src tests scripts
Result: passed

git diff --check -- src/sqvm/hamiltonian tests configs/hamiltonians/2q1c_charge_basis.yaml scripts/run_stage_01_device.py scripts/run_stage_02_1_verification.py
Result: passed
```

The two deselected tests are the Stage 1 and Stage 2 VSCode runner smoke tests that write
production output. Their implementations and the manifest's completed 107-test evidence were
reviewed. Both verify commands were independently rerun against system-temporary output. The
temporary Stage 2 artifact was byte-identical to the production candidate.

## Hash chain

```text
authorization record: E85CD03A8FA4EF449C6C038B1CDDAD21E4321003C328057E0911069B398B307D
archived previous:     222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C
accepted anchor:       FE489B2476FA3AE3121BEBB1FA06BF5EF1B74DEDAD4546458C7687EA7431C88D
candidate artifact:    DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66
rebaseline manifest:   4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D
verification notebook: 285B1B56412F122E81F1E733174E3FE62FDA2773EDAFEEC24114CDFBE6419D36
Hamiltonian config:    B65D1C57083D26F3C0CE1B0F980B07B685F4F21BCBD955DCD73D933416D84E5E
device config:         CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F
device artifact:       B771B72ED33FE104E8940730684710B23BB9278451230318D2B52543C77CE86D
Stage 2 source tree:    0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972
Stage 2 CLI:            06078C7EBFB6EBB48894753F6D10E6E4BCA06A93DA94D2A46E40242569A5D16B
```

The anchor has `decision=accepted`, `approved_by=user`, and binds the authorization record
and archived previous bytes. `require_accepted_legacy_anchor` passed. The manifest was rebuilt
with `validate_rebaseline_manifest`; its mapping was exactly equal and its raw bytes were canonical.
The candidate schema/type/version and all candidate/manifest provenance hashes matched current bytes.

## Configuration and numerical review

The configuration diff contains only `q1`, `c`, and `q2` charge cutoffs changing from 5 to 7.
Solver and other physical fields are unchanged. The Hilbert dimension is 3375.

The independently recomputed old-to-new lowest-12 gap deltas in MHz were:

```text
[0.0, -1.119684083989, -2.106499929610, -31.689141554047,
 -8.308926643153, -15.242705029799, -3.234883862611,
 -32.810161783090, -33.797228867471, -35.913437784721,
 -192.911515709348, -64.093982940328]
```

They matched the manifest exactly. The maximum old-to-new changes in `C_mode`, `E_C`, and
`EJ_eff` were all zero. The dense 12-gap candidate rebuild passed with maximum absolute
difference `0.0 GHz` against the candidate artifact.

The independently recomputed N=7 to N=9 single-mode absolute drifts were:

```text
q1: 0.000386739774 MHz
c:  0.097365327832 MHz
q2: 0.001101029277 MHz
```

All are below the 0.50 MHz gate. Single-transmon numeric-minus-analytic differences were
`-13.281728`, `-9.051516`, and `-11.898568 MHz` for q1, c, and q2, respectively, and are
physically reasonable for the diagnostic approximation. All 20 candidate checks passed;
the four warning-severity checks are passed diagnostics.

## Notebook and protection review

The verification notebook reads only the archived previous artifact, accepted anchor,
candidate artifact, and manifest. All 6 code cells have execution counts, there are no error
outputs, and the outputs show the hash chain, deltas, convergence, tests, verifies, and checks.
It does not reference or claim a pre-existing approval.

The six frozen specification hashes, authorization record, candidate, manifest, anchor,
archived previous artifact, configuration, and Stage 2 source-tree hash matched before approval.
No Stage 3 code, configuration, or output exists.

## Decision

APPROVED. The Stage 2.1 rebaseline candidate has no blocking findings and is authorized for
the canonical independent approval payload bound to this review record.
