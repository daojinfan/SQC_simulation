# Stage 3.1 C2.2 Solver Revalidation Independent Review

- Date: 2026-07-11
- Delegation source thread: `019f4c1f-8c5e-7020-96ca-dc9a0db45406`
- Reviewer role: `independent_test_review_ai`
- Scope: C2.2 fixed-refinement remediation and 44-case solver candidate only
- Decision: `APPROVED`
- Findings: none

## Authority and bindings

- C2.2 remediation: `E356551C706F3C2372620311D69D8A7C4FF1E72825680D43C818D53B0746BB7C`
- C2 remediation: `9CA7FFA870DA6F83581D04DE067D5F618E388BAEC72162E0CBBF752487158C04`
- Candidate: `6D58A5D77988978E7B9377EC06841D9AD2F31D296D358C8874F7DF5C9EA2EE0D`
- Stage 3.1 source tree: `263125AAF4CBEE1572E254A41916F8E0B519BEDB6DA80956F8789AA5422A276E`
- Config: `B2AFD3FA9FF9EA35153DEBF8B5E6C77D0A3E7C490270CEB6C89AC0D317594F2A`
- Environment: `40D27F433DDA0D7DD15E900E4C7B6A6B82BE55060B07839FAA8877B0B778AD12`
- Dense pilot: `AFE2A8B6F531468360BB60B535D86B216652977C6685C9A6B1DDE4E7237DC916`
- Hamiltonian config: `B65D1C57083D26F3C0CE1B0F980B07B685F4F21BCBD955DCD73D933416D84E5E`
- Stage 2 artifact: `DB17729D2C75BF3BAE90382BA78F6F3015C8C870B57DE12448FE339E57B9EE66`
- Stage 2.1 manifest: `4368B50235E1755F9E03F1016084B7099CD5D2EC5D68BC8A0D7B6E963D4CEB8D`
- Stage 2.1 approval: `CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9`
- Stage 2 source tree: `0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972`

The canonical candidate has the exact twelve binding keys, 44 unique signature-major cases, complete coverage, `failed_cases=[]`, and `validation_passed=true`. Both remediation hashes and all current provenance hashes were independently recomputed.

The previous approval `DE4E9CCE40AFA51D46DD5E78201FA39602B9CED8B0CDD615BE4CC8ED08E61F23` was rejected for the new candidate with `ok=false`, no validated specification, and a candidate-hash mismatch. Its raw bytes were preserved as `output/stage_03_1_solver_validation/eigsh_validation_approval_c2_1.json` before replacement.

## State-machine review

Independent positive and negative checks confirmed:

- exact fixed levels L0 through L4 with valid final drift produce `fixed_refinement_completed` and `numerically_converged=true`;
- non-finite, mismatched, or excessive final drift, a missing level or final bracket, and an earlier boundary produce `fixed_refinement_invalid` or retain `boundary`;
- a representative physical-evidence failure does not rewrite valid fixed numerical completion and remains blocked by the existing physical/status priority;
- adaptive `converged` remains numerically converged and adaptive `max_refinement_exhausted` remains non-converged;
- missing, extra, stale, malformed, and tampered exact-twelve candidate bindings fail closed.

A temporary independent approval validated the current candidate with `ok=true` and an acceptance-eligible solver specification. Tampering the C2.2 remediation binding returned `ok=false` and no specification.

## Tests and regeneration

- Focused Stage 3.1 suite: `40 passed`.
- Spectrum suite: `243 passed`.
- Safe repository suite: `347 passed, 3 deselected`; only the three production/history-writing runner smoke tests were excluded.
- `compileall -q src tests` with external bytecode cache: passed.
- `git diff --check`: passed with line-ending warnings only.
- Workspace `__pycache__` directories after testing: zero.

Exactly one system-temporary 44-case regeneration was performed. All non-timing fields, ordered case identities, eight aggregates, normative vector, and exact-twelve bindings were exactly equal to the candidate. Temporary timing maps were finite, positive, and ordered:

- p50: dimension 3375 `0.0856948000000557 s`; dimension 4275 `0.11723849999543745 s`.
- p95: dimension 3375 `0.09343503998898087 s`; dimension 4275 `0.130221725004958 s`.

The temporary directory was removed and regeneration was not repeated.

## Numerical gates

- Maximum gap error: `6.536993168992922e-13 GHz`.
- Maximum repeat gap error: `0.0 GHz`.
- Maximum dense/repeat projector error: `2.1398451875878521e-10` / `2.2192320695495356e-15`.
- Maximum dense/repeat participation error: `4.728112346086277e-11` / `0.0`.
- Maximum dense metric error: `9.094947017729282e-13 GHz`.
- Maximum q1-q2 splitting error: `3.979039320256561e-13 GHz`.

## Protected formal history and decision

The current C2.1 formal directory remained an exact three-file set with hashes `4D7669E0319A75A3C29F7F273AB963D4422F4E3AB8649559E045C30AB149845A`, `A7194EB8157FDC979FD11BA5896C8E1908191B2A4B738525439023F1CC47B163`, and `7AD9DEF5CD047447F6F7EA453D81409162E1B0C46594DDEA75CED557C3E3E9F1`. The earlier C2 rejected archive also remained unchanged.

The C2.2 solver candidate is approved only for the separately controlled step that atomically archives the current rejected formal directory and performs one new-source formal run. This review does not approve the Stage 3.1 computational gate or Stage 4.
