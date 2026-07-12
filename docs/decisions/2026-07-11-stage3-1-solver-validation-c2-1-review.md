# Stage 3.1 C2.1 Solver Revalidation Independent Review

- Date: 2026-07-11
- Delegation source thread: `019f4c1f-8c5e-7020-96ca-dc9a0db45406`
- Reviewer role: `independent_test_review_ai`
- Scope: C2 remediation implementation and 44-case solver candidate only
- Decision: `APPROVED`
- Findings: none

## Authority and protected history

- C2 remediation: `9CA7FFA870DA6F83581D04DE067D5F618E388BAEC72162E0CBBF752487158C04`
- Objective: `95A45E9E3205241265386FC9F7BDF8BB0DDB043E6A9D33D5714D7C68964BD795`
- Design: `A2C9E8E28F778813818A5B76C817C54EB459D848E85678F5850FAB1451123DE5`
- Plan: `5878A186A6A0673FC8FA07747D7822D022667895B9C04D78D892933A8C166F7C`
- Design review: `7EB52CECF32B2692AEBECAECCFEEE0AA249B6D075E756F1B697AEF5E95B2D64D`
- Freeze manifest: `AB7642A9C170E98B4319D1D453A5B4962FA50C3236F9DABA165E27EF7C9F7F32`

The rejected C2 formal files remained unchanged:

- Artifact: `90972BB6A61D0D7C770F50AFAB3361F6A738A79C501DACDEC5E90647DFF620F5`
- Notebook: `C8E152DB0FDFE102A1C686E9A1A4EA208FF012A040E8A3D5E02E7ADE9F6C29D3`
- Verification report: `BAEB0409031F3896230641AE3C812525274FDCD00751E7616733736968EA434C`

No formal Stage 3.1 acceptance, runner, CLI, production smoke, or production output mutation was performed.

## Candidate and bindings

- Candidate: `406FE9FAB8B39212D86C5507EF456F8B8E3B66243B7E649A5D155EBAF68F5495`
- Stage 3.1 source tree: `F4E42A4C13C362A9208AF0882F033161AF174BFD8D6E3FBB80AB8FC6FB831F0B`
- Config: `B2AFD3FA9FF9EA35153DEBF8B5E6C77D0A3E7C490270CEB6C89AC0D317594F2A`
- Environment: `40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12`
- Dense pilot: `AFE2A8B6F531468360BB60B535D86B216652977C6685C9A6B1DDE4E7237DC916`
- Hamiltonian config: `B65D1C57083D26F3C0CE1B0F980B07B685F4F21BCBD955DCD73D933416D84E5E`
- Stage 2 artifact: `DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66`
- Stage 2.1 manifest: `4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D`
- Stage 2.1 approval: `CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9`
- Stage 2 source tree: `0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972`

The candidate is canonical and has 44 unique signature-major cases, complete 4 by 11 coverage, `failed_cases=[]`, and `validation_passed=true`. Its remediation, freeze, provenance, source, environment, config, and pilot bindings matched current bytes.

The previous approval `43E8DBF6864B01E6420275ECA3485F58BF57F18334D6A7A7FD1AB238255CCF64` was independently confirmed stale for the new candidate: validation returned `ok=false`, no validated specification, and a candidate-hash mismatch. Its raw bytes were preserved as `output/stage_03_1_solver_validation/eigsh_validation_approval_c2.json` before issuing the replacement approval.

## Remediation review

The five rejected-C2 implementation areas were reviewed against the frozen design and remediation decision:

- complete Decimal-sorted grid retracking from a fixed left semantic anchor uses adjacent one-to-one overlap assignment and recomputes tracked energies, participation, projector weights, overlaps, and target-subspace continuity;
- baseline evidence selects the nearest left/right character endpoints, and refined cutoff scans use their endpoints for the 17-point grid with forced baseline bracket keys and four fixed refinement levels;
- the four-phase runtime ledger and conservative preflight cover idle baseline/refinements, outer/inner scans, and anchor cutoff convergence before immutable finalization, modulation, and gate construction;
- the Stage 3.1 computational gate preserves ordered computational checks separately from post-write checks;
- transaction reports bind final paths, and the self-contained read-only notebook is genuinely executed with `nbclient`.

A synthetic avoided crossing demonstrated tracked branch character exchange while a fresh bare relabel selected different eigenstates. A target-pair internal basis rotation was allowed by subspace continuity as required. A representative ambiguity involving the spectator was blocked by target-subspace continuity (`sigma_min_squared=1/3`, final status `low_subspace_continuity`). Non-finite overlap was rejected by the focused test.

Temporary positive validation with an independent hash-bound approval returned `ok=true` and a validated acceptance-eligible specification. Tampering the remediation binding returned `ok=false` and no specification.

## Tests and independent regeneration

- Focused Stage 3.1 suite: `31 passed`.
- Spectrum suite: `234 passed`.
- Safe repository suite: `338 passed, 3 deselected`; only the three production/history-writing runner smoke tests were excluded.
- `compileall -q src tests` with an external `PYTHONPYCACHEPREFIX`: passed.
- `git diff --check`: passed with line-ending warnings only.
- Workspace `__pycache__` directories after testing: zero.

Exactly one temporary 44-case regeneration was performed. All non-timing fields, ordered case identities, eight aggregates, normative vector, and bindings were exactly equal to the candidate. Temporary timings were finite, positive, and ordered:

- p50: dimension 3375 `0.08541159999731462 s`; dimension 4275 `0.11792625000089174 s`.
- p95: dimension 3375 `0.09619717499663238 s`; dimension 4275 `0.13008192499546567 s`.

The temporary directory was removed and regeneration was not repeated.

## Numerical gates

- Maximum gap error: `6.536993168992922e-13 GHz`.
- Maximum repeat gap error: `0.0 GHz`.
- Maximum dense/repeat projector error: `2.1398451875878521e-10` / `2.2192320695495356e-15`.
- Maximum dense/repeat participation error: `4.728112346086277e-11` / `0.0`.
- Maximum dense metric error: `9.094947017729282e-13 GHz`.
- Maximum q1-q2 splitting error: `3.979039320256561e-13 GHz`.

## Decision

The C2.1 solver candidate is approved for the separately controlled lifecycle step that atomically archives the rejected C2 formal directory and performs one new-source formal run. This review does not approve the Stage 3.1 computational gate or Stage 4.
