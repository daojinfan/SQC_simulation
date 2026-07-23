# Runtime v0.2 Compiler Fixture v2 Activation

- Date: 2026-07-21
- Status: ACTIVE after independent test approval
- Scope: runtime v0.2 compiler-fixture authority closure and activation; the approval scope binds all ten listed source authorities, not only `compiler.py`.

## Active graph

The immutable v1 authority and approval remain in force and unchanged:

```text
v1 authority ID: 2B255F518ABA1A0949965CB5E53A7AE6B19BD619A8F1739D9A6D2B1458E34EB5
v1 authority raw SHA-256: E55E955E54204DDB447D98830ADB76D161B3CA05965F7FF9631FB9A3238D864C
v1 approval raw SHA-256: 70D268BCE6DAFC1F49AB1E3A9BE9BEC82B32099E148517B2EAC30B50D071A445
v1 compiler source SHA-256: 7E94B3B7D24EDA24AA720D532754F4ED564B9D8E700B2B125BC59522E01ACB23
```

v2 changes the runtime fixture authority closure and its derived identity. Its primary trigger is the compiler compatibility change, but the approval surface deliberately includes the complete ten-source authorization set below:

```text
v2 authority ID: 89168201511D2BF75667B3593969F4B9CC21AB7AC8DE9570DFE58A6BA897F4D5
v2 authority raw SHA-256: 4B7D901D910A706D24353CC7CC3C4B2DBE7427EEB77C8D6A4A6A8DD50F278BFF
v2 approval raw SHA-256: EBD33BF0B8BB895086BDBF07552C68FA8AEDED4E5FCE289D2FAF979558258124
v2 compiler source SHA-256: 656C761E258B20B5743AF6327FA47B42314354C5AA975D10FBD6174A360D6D03
```

## Authorization assessment

`5825aaa0c45523e25b0964c6ef790f5947de4f1d` added acceptance of a resolver-generated `wave_index` projection when verifying a calibrated record hash. The candidate binds the exact PlatformConfiguration v0.2 design and companion schema that specify this generated-only compatibility projection and canonical record hashing. It also binds the QCIS v0.3 phase amendment, Stage 4.1 design, and reviewed freeze record because the current compiler source includes the v0.3 absolute-time phase/clock/trace behavior introduced by those authorities. The v0.3 design freeze authorizes preserving v0.2 oracles and adding v0.3 evidence, and the current QCIS regression suite passes.

The independent test approval binds the authority raw hash, authority ID, design hash, reviewer role, and `APPROVE` decision. Registry v2 pins its formal approval raw SHA and is the explicit default for new admission. The public inspector with no version argument therefore resolves v2; persisted historical artifacts with no fixture binding remain explicitly v1 and are never reinterpreted through the current default.

## Historical Evidence

`tests/fixtures/runtime_v02_legacy_v1/` contains byte-frozen terminal and interrupted Runtime v0.2 v1 evidence generated only by the old runner from commit `8e054797227ed58a53ec80a1e35df5ac205de032`, whose compiler source matches the immutable v1 authority. `tests/tools/generate_runtime_v02_legacy_v1_fixture.py` safely archives that commit outside the target, fixes every evidence-affecting UUID, clock, host/PID, environment, relative output path, and invocation order, and injects the old runner's `atomic_publish` failure for the interrupted tree. Its provenance pins the generator raw SHA-256, prerequisites, each evidence file raw SHA-256, and the aggregate; the only excluded path is the non-evidence SQLite catalog cache. Historical verification validates the persisted v1 authority and approval raw pins plus the registered legacy source aggregate; it does not weaken source-binding validation for new request admission.

## Approval record

WP0-D7 independently reviewed the compiler diff and v0.2 point, replay, tamper, recovery, link-rejection, historical fixture, and QCIS evidence. The formal v2 approval is `APPROVE` with reviewer role `independent_test` and no blocking findings. Candidate filenames are retired; the authority bytes and all ten source bindings are unchanged.

## Reproduction commands

```powershell
python -m pytest -q tests/test_stage7_qcis_v2.py tests/test_stage7_qcis_v3.py tests/test_stage7_qcis_acceptance.py
python -m pytest -q tests/test_stage6_runtime_core.py tests/test_stage6_runtime_evidence.py tests/test_stage6_runtime_platform.py tests/test_stage6_runtime_v02.py tests/test_runtime_publication.py
python -m pytest -q tests/test_stage6_runtime_v02.py -k 'fixture or legacy or unknown'
python tests/tools/generate_runtime_v02_legacy_v1_fixture.py --repository-root . --target D:/sqvm_runtime_v02_legacy_v1_generated --verify-against tests/fixtures/runtime_v02_legacy_v1
```

All commands must pass. New v0.2 admission uses formal v2; immutable v1 bytes remain for historical evidence and legacy artifacts with no persisted fixture binding.
