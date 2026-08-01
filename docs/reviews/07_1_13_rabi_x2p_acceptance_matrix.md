# Rabi / X2P Independent Acceptance Matrix

## Scope and authority

- Design authority: `docs/designs/07_1_13_rabi_x2p_amplitude_calibration.md`
- Frozen baseline: `f8681ee`
- This matrix is owned by independent QA. It is intentionally not a product
  implementation specification and cannot be satisfied by synthetic API-only
  mocks.

## A: deterministic fast gates

| ID | Design clauses | Acceptance vector | Required oracle |
| --- | --- | --- | --- |
| A01 | 2, 7, 19.1 | `exact_qcis` | Every point source is exactly `SET amplitude`, `X2P`, `X2P`; `PLSXY` is absent and the circuit ID is index-based. |
| A02 | 4.1-4.3, 7 | `overlay_and_capability` | Only a capability-advertised QAgent is accepted; the pre-existing amplitude SET path is used; parent configuration bytes do not change; both gates bind one effective setting hash. |
| A03 | 5.1, 6 | `axis_decimal` | Decimal grid includes both endpoints, is strictly increasing, has no duplicates, starts at zero, and rejects non-integral, non-finite, negative, and greater-than-64 grids. |
| A04 | 8.1-8.3, 9 | `phase_noninteger_carrier`, `phase_integer_carrier`, `phase_detuned`, `phase_reset_attack` | Adjacent events share logical phase and obey absolute-time detuning/lab formulas. A local reset must fail with `rabi_phase_audit_failed`; equal wrapped lab phases are allowed only when the unwrapped formula is still correct. |
| A05 | 8.4-8.5, 19.2 | `electronics_once`, `frame_round_trip` | One full logical schedule enters electronics once. Segment-wise filtering is rejected, and the effective rotating-frame IQ is the QuTiP input, never the lab preview. |
| A06 | 11, 14 | `dataset_and_artifact` | Column lengths, finite values, canonical SHA, continuous indices, point receipts, bounded reader, and tamper rejection are all checked. |
| A07 | 12, 13 | `fake_fit_first_lobe` | Deterministic sine data recovers the first positive peak inside its three-point bracket. Zero contrast, no peak, edge peak, NaN, inconsistent columns, bad leakage/norm/RMSE, and a free-phase fit are rejected or ineligible. |
| A08 | 13 | `candidate_path_and_stale` | Candidate is `xy2_amplitude`, names the resolved active setting ID, is verification-dispatched explicitly, never mutates during run, and fails stale/repeated confirmation. |
| A09 | 17-18 | `recovery` | Tail-only resume, zero-execution replay, changed-axis/config/policy conflict, cancellation, deadline, publication recovery, and evidence drift use the stipulated stable errors. |
| A10 | 15-16, 19.5 | `web_notebook` | Notebook has the four public-API stages and preserves the operation ID. Web reads projection first, exposes P0/P1/leakage/fit plus candidate marker and source lookup, and does not start a run. |
| A11 | 19.3, 19.6 | `canonical_windows` | Fixture and canonical analysis bytes are identical on Windows and Linux; all output paths use `Path` rather than separator-sensitive string composition. |

## B: real-system and slow gates

| ID | Design clauses | Required execution | Required oracle |
| --- | --- | --- | --- |
| B01 | 8.4-8.5, 10 | Real Stage 4.1 + Stage 5.1 QuTiP small grid | At least five actual amplitudes execute with real effective IQ and verified evidence; no mocked runner can satisfy this gate. |
| B02 | 9, 10, 17 | Fault-injected Runtime 0.3 run | Precompile failure executes zero physical points; point failure/cancel/deadline resume only the uncommitted suffix. |
| B03 | 14, 15 | Published artifact through reader and Web server | Directory and archive reader reject the same tampering; catalog, archive, trash, restore, and lazy Web detail loading preserve the Rabi renderer. |
| B04 | 16, 19.5 | Notebook kernel execution from repository root | The documented Rabi notebook imports without `sys.path`, performs one controlled fake fixture workflow, and can confirm exactly one candidate. |
| B05 | 19.6 | Clean Windows checkout | `contract`, `integration`, `physics_slow`, `evidence`, strict marker collection, and fixture-integrity subsets run with the Windows lock file. |

## Current acceptance assets

`tests/fixtures/rabi_x2p_acceptance_v1/vectors.json` is a product-independent
oracle for the exact QCIS program, Decimal amplitude grid, absolute-time phase
formulas, and a first-lobe fake-fit dataset. `tests/test_rabi_acceptance_vectors.py`
validates that oracle and is intentionally runnable before Rabi implementation.

`tests/test_rabi_public_api_acceptance.py` is the integration-facing acceptance
contract. It requires the frozen public API, artifact topology, and real QuTiP
execution. It is expected to fail on `f8681ee`, where Rabi is intentionally not
implemented; this is a recorded baseline gap, not an xfail or a mock-based pass.

## Commands and budget

| Gate | Command | Expected budget |
| --- | --- | --- |
| A vector oracle | `python -m pytest -q tests/test_rabi_acceptance_vectors.py` | under 2 s |
| A public contract after integration | `python -m pytest -q tests/test_rabi_public_api_acceptance.py -m integration` | 5-30 s with fake/runtime fixture |
| B real QuTiP | `python -m pytest -q tests/test_rabi_real_qutip_acceptance.py` | 1-5 min on the approved local model |
| B Web/Notebook | `python -m pytest -q tests/test_rabi_public_api_acceptance.py -m 'integration or notebook'` | 1-3 min |
| Release regression | `python -m pytest -q` | environment-dependent; estimate 10-30 min |
| Windows qualification | `python -m pip install -r requirements-test-py312-windows-lock.txt` then `python -m pytest -q` | 15-45 min plus environment setup |

## Baseline gaps at f8681ee

1. No `run_rabi` or `cancel_rabi` public export exists, and the Rabi notebook is absent.
2. No Rabi planner, phase audit, analyzer, candidate verifier dispatch, reader,
   artifact publisher, Web renderer, or notebook exists.
3. Therefore B01-B05 and all product-facing A gates must remain red until the
   PM supplies an integration branch. The oracle gate is green now and guards
   against changing the independent vectors while implementation proceeds.
