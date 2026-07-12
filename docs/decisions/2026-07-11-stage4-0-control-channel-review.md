# Stage 4.0 Control-Channel Independent Review

- Date: 2026-07-12
- Delegation source thread: `019f4c1f-8c5e-7020-96ca-dc9a0db45406`
- Reviewer role: `independent_test_review_ai`
- Decision: `APPROVED`
- Blocking findings: none

## Remediation and implementation bindings

This review incorporates both required remediation decisions:

- Frozen upstream receipt remediation: `FB4C0867015577603D7454926493D2CE9AF5CA5CFBD029E98A5A0938739E37B4`
- Notebook validation remediation: `2B5C8A13ECC84A19A06470C79BB3FD6021C743F81E474B013D3981FFEF8F1514`

The reviewed D0.4 and unchanged dependency-boundary bytes are:

- `src/sqvm/control/upstream.py`: `89E2EE412A70DD38AB32BA0B86E6C98EA3099E1D029DC0615BCA0C08688C9998`
- `tests/test_control_channel_compatibility.py`: `2B8CB6CAFD1A82A1B5DD46D80B8C10B83FF55BB59CA91A7191AE8DC2BA048E31`
- Unchanged `src/sqvm/control/compatibility.py`: `9F5EE7655BBC03B8DFD1395756F477F2D1656BF842C428C38A80FD7940DC1B67`
- Unchanged `src/sqvm/control/artifacts.py`: `C239C3FDC23F7243CBF686CB23887C7413DF77427CE1E17AE3FF88679546DAC2`
- Root lazy-export boundary `src/sqvm/__init__.py`: `51410EFFEC717D8EDE8FE9E8158DEF68B0FB805EB7B049C788B4C7ED5EF25575`

The D0.4 changed block strictly requires the Stage 3.1 notebook metadata root and `language_info` to be
mappings and requires `stage3_1_read_only is True`. The matching tests preserve synchronized downstream
bindings and verify fail-closed all-false reports for malformed metadata and solver aggregates. The focused
line review found no implementation, schema, exception-boundary, or test-coverage defect.

## Formal candidate

Before approval, the formal directory contained exactly three files and no review, approval, temporary file,
subdirectory, or staging residue:

- Registry: `CDB10063BC5B61965E8FD6A9ACBB124CB5EE8F56AC1A19012DAAFC187A81D478`
- Control-channel manifest: `1BE614301C7EA74025602CF8C1B9E11576CC7C43F50BD9B6782B985575E34E1D`
- Executed verification notebook: `A64DA8BDCB158C96ED7AE35B5FA70B7A14659224AFAA0BA96875A50D9A61E78A`
- Pending verification report: `9DE8ECE5528881289A2BD1A227C93506D70A6EA6DCAECBCDF054D99A978E4BC2`

Read-only reconstruction confirmed `execution_succeeded=true`, `compatibility_candidate_ready=true`,
`control_channel_ready=false`, `approval_status=pending`, and an empty blocker list. The registry is the exact
seven-channel merge, and the candidate remains byte-for-byte unchanged; it was not regenerated.

## Independent verification

Inherited independent T0.4 evidence was accepted only after the current D0.4 and exact-three hashes were
recomputed and matched. That evidence includes the D0.4 line review; four synchronized binding attacks for
`metadata=[]`, `language_info=[]`, `stage3_1_read_only="true"`, and solver `aggregates=[]`, each returning an
exact 26-key all-false report without exception; `150 passed` focused tests; and passing external-cache
compileall and diff-check. Developer evidence additionally records `6 passed` targeted tests, `150 passed`
focused tests, `497 passed, 3 deselected` safe full regression, and passing compile/diff checks.

This replacement independent review ran one complete safe repository regression with the approved
`C:\Users\fandaojin\anaconda3\python.exe`, `PYTHONPATH=src`, and only the three production runner smoke tests
deselected. Result: `497 passed, 3 deselected`.

A fresh read-only validation independently returned the exact 26 frozen upstream hash keys with `ok=true`,
`stage2_1_ready=true`, `stage3_1_ready=true`, and no errors. The Stage 2.1 manifest and approval remain
`4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D` and
`CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9`; the Stage 3.1 artifact, report, and
approval remain `76C539FF22EAB54E5E10C93526E20AFB68A39507BCD9BE2DAD5A2897282FAD35`,
`F5F82FB7D9770CFE6ADEE6639A67C46F781891FF8F94F5247A54F4722655F3CD`, and
`5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D`. The Stage 4 design-freeze manifest
remains `99D2DFA48582DC9DBE792AD7157BD71CCC9E0610ADEEEE62791FF6E3EF52FBCD`.

Fresh ordinary compatibility validation passed with `nbclient` absent from `sys.modules` before and after
the read-only call. Because every current byte matched the inherited independent evidence, compileall and
diff-check were not repeated.

## Decision

Stage 4.0 control-channel rebaseline is approved with zero blocking findings. This approval closes only the
control-channel compatibility gate. It does not publish or regenerate a candidate, modify accepted upstream
artifacts, authorize physics changes, or start the Stage 4 compiler.
