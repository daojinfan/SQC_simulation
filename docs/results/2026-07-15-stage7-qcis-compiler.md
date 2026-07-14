# Stage 7.0 QCIS Compiler Result

- Date: 2026-07-15
- Scope: strict QCIS compilation and byte-verification foundation
- Status: `completed`
- Physics backend or calibration claim: none

## Implemented boundary

The `qcis_stage7_calibration_v1` compiler now provides:

```text
strict canonical source, envelope, template, binding, and authority admission
typed literal and scan_ref resolution with exact units and finite binary64 values
PLSXY, PLS, I, RZ, B, X2P, and Y2P parsing into immutable typed AST objects
QAgent -> gate setting -> waveform registry -> accepted calibration macro resolution
half-open absolute/append scheduling, cursor, frame, barrier, and overlap validation
Gaussian/DRAG complex XY and rectangle absolute-flux logical arrays
canonical AST, provenance trace, carrier metadata, coefficient inventory, and raw SHA-256 evidence
five-lane logical and identity-effective byte verification plus coefficient-inventory verification
```

Reserved QCIS operations remain fail-closed. This tranche does not implement Stage 4.1 electronics, Stage 5.1
QuTiP execution, model-derived scans, analysis, recommendation, or calibration decisions.

## Bound implementation bytes

```text
4187E97389F0EC54C1142F785A1C4A61DF65E8EA555ADC7D2795AFB0CFD0D95C  src/sqvm/qcis/__init__.py
616319FE15EAEBE5502BC50DAE5080D53C398912D2CAEE19C82E62B9A59A2700  src/sqvm/qcis/canonical.py
CB6CE3E0E7731D482363D9B85898E41F7ECA6FB358DFA213F17F77B05D12266C  src/sqvm/qcis/compiler.py
EC2BFAA812BADC71D0BDE0E0EE0D608FFA3FFC2531205A56D75A5780A81863E5  src/sqvm/qcis/errors.py
45375CEB1D8AB853B73D75121A8668E48B72C909BE27ACE5DE435583F796421B  src/sqvm/qcis/models.py
5AEFF30CF658320F66875400583AC66603F6DDE8153222F8C0658E1C701637D5  src/sqvm/qcis/parser.py
E3BF3D0758FE22E7D91E6D75F5C748D7359BDAF61D2698940E730996F886C114  src/sqvm/qcis/verify.py
6C06CAEC15897C74927731754D5359626764DFBAA7551BA7648D8EE96FA66989  tests/test_stage7_qcis_acceptance.py
```

These hashes describe the final reviewed candidate, including the all-lane verifier correction. The commit hash
and this inventory together identify the accepted compiler tranche.

## Verification

Environment:

```text
Python 3.12.10
pytest 9.0.3
```

Final focused result:

```text
Stage 7 QCIS acceptance: 99 passed
Stage 6 runtime regression: 35 passed
Combined focused run: 134 passed
python -m compileall -q src/sqvm/qcis: passed
git diff --check: passed
```

The full historical suite reported `266 passed, 140 failed, 232 errors`. The failures and errors are dominated
by absent ignored Stage 1-4 artifacts under `output/`, including Stage 1 device and Stage 2 Hamiltonian fixture
files. This is the same pre-existing checkout limitation recorded for Stage 6 and is not represented as a full
suite pass.

## Independent review

```text
development owner       thread 019f5c41-1ced-7d52-a65d-0adff0c0de9c
independent test owner  thread 019f5c41-4278-7403-87b1-3c23d231444e
physics/numerical owner thread 019f5c41-00f4-7042-a082-2f3682c4fe30
release replacement     thread 019f6194-b09c-7052-96be-7f2ac75586d1
```

The test, physics, and release owners reported no remaining blocker, high, or medium finding on the final
candidate. The release replacement independently verified the design hashes, 14192-byte QCIS mirror, byte
oracles, focused tests, and expected working-tree scope.

## Claim boundary

This result proves deterministic compilation and fail-closed evidence hand-offs only. It does not open Stage
7.1-7.3 or claim a physically qualified Stage 4.1/5.1 path. Model-derived QuTiP calibration remains blocked by
the gates listed in the frozen Stage 7 plan.
