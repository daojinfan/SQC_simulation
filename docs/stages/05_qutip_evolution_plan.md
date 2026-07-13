# Stage 5 Plan: QuTiP Time Evolution

## Status and entry gate

This plan is proposed, not approved. No implementation, dependency change, config, test, output, numerical run,
freeze manifest, review, approval, commit, or push is authorized by it. Implementation starts only after an
independent Stage 5 design review approves a hash-bound freeze manifest covering this plan, the decision, and
the detailed design, and after the existing Stage 4 exact-five validator returns `stage5_ready=true`.

## Frozen delivery sequence

1. Add the approved configuration and package dependency using only the approved interpreter
   `C:\Users\fandaojin\anaconda3\python.exe`. Pin the accepted support range `qutip>=5.1.0,<5.2.0`; capture
   exact QuTiP, NumPy, SciPy, Python, BLAS, platform, and thread environment. PATH discovery is not valid.
2. Implement the design-freeze loader and Stage 4-only effective-signal loader. Prove by tests that no AWG,
   logical-target, delivered voltage, readout, or control recompilation path is reachable.
3. Implement the unique local-number `U(t)`, counterterms, exact `U.dag H_static(phi) U` transformation of
   all Stage 2.1 charging/capacitance coupling/flux terms, XY-only RWA, carrier derivation, and one
   GHz-to-rad/ns helper. Do not apply a zero-sector projection to controlled-system physics or build a sampled
   lab-frame carrier time series.
4. Implement physical-lab-frame initial ground and independent 16-state lab labeling/projectors, exact Decimal
   assignment tie-breaking, edge hold, interaction-frame state output, and lab-frame observable transformation.
5. Implement the immutable QuTiP solver option/tolerance constants and solver-validation candidate, notebook,
   report, independent validator, and fail-closed preflight. Candidate records only fixed spec ID/hash and
   actual results; validator independently rebuilds all probes and expected values. It must pass independent
   review before smoke or formal execution.
6. Implement canonical complex ket artifact schema, immutable read-only notebook, report, receipt, exact-four
   staging publication, formal authorization, watchdog, and exact-five acceptance/readiness validator.
7. Implement cutoff embedding/convergence and all representative attacks listed in the detailed design.

## Profiles and numerical authority

| Property | Smoke | Formal |
| --- | --- | --- |
| Approved control source | Stage 4 formal exact-five | Stage 4 formal exact-five |
| Scenario IDs | `xy_drag` | `xy_drag`, `q2_resonance_flux`, `coupler_0_200`, `coupler_0_270`, `coupler_0_385` |
| Cutoff | `(5,5,5)` | `(7,7,7)` plus `(8,8,8)` convergence |
| Runtime | 30 s total | 120 s/scenario, 300 s total |
| Acceptance | never | candidate only after separate authorization |
| Approval | forbidden | independent exact-five review required |

Smoke must not write an approval and cannot be used as formal evidence. Formal execution has exactly one
authorization-bound attempt identity. The formal target must not exist, authorization must be unused, and a
parent watchdog must own the wall-clock deadline. Parent consumes fixed ordered scenario-start/complete IPC,
enforces the earlier of 120.0 s per-scenario and 300.0 s total hard deadlines, and treats 5.0 s grace as
termination-only. Attempts are permanently reserved by an exclusive lock and
audited at `output/stage_05_qutip_evolution_attempts/attempt-<authorization_sha256_lower>.json`; prior receipt,
lock, or concurrent reservation rejects before work. Timeout/failed attempts cannot be resumed or overwritten.

## Test and review gates

1. Static contract tests: every exact JSON key set/type, canonical complex encoding, path containment, hashes,
   status precedence, forbidden signal layer, frozen carrier map, and Stage 4/Stage 5 trust chain.
2. Physics/model tests: `U`, `-fN` counterterms, full transformed static/capacitance/flux physics, XY-only
   RWA, coupling-element survival, q2-flux off-diagonal survival, one conversion, Hermiticity, zero hold,
   discontinuity edges, physical-lab-ground versus quasienergy-ground attack, frame conversion, exact Decimal
   label tie-breaking/projectors, and population/leakage.
3. Solver gate tests: immutable exact solver options and tolerances; approved interpreter; Qobj/QobjEvo/
   mesolve/sparse operations; fixed zero, discontinuity, Rabi, interaction-survival probes; norm/reference/
   deterministic aggregate; notebook replay; and wide-tolerance, custom-expected/custom/missing-probe or
   option, and candidate/approval reuse attacks across freeze, Stage 4, source tree, interpreter, and spec.
4. Operational tests: canonical embedding/projection and all-time convergence; stale/reused authorization;
   existing formal target; prior/concurrent attempt receipt; 121 s single-scenario timeout; total timeout;
   missing/duplicate/reordered IPC or child-no-response; watchdog/staging cleanup; exact-four/exact-five
   attacks; report/receipt/notebook/artifact hash attacks; and Stage 6 readiness false cases.
5. Independent test review with zero blocking findings authorizes one smoke run. Independent review of solver
   validation must pass before that smoke run. A further explicit formal authorization is required before the
   one formal attempt.
6. A separate reviewer validates the formal exact-four candidate, then separately validates an exact-five
   approval. `Stage6ReadinessReport.stage6_ready=true` is the only entrance to Stage 6.

## Expected implementation targets

```text
configs/evolution/2q1c_qutip.yaml
configs/evolution/2q1c_qutip_smoke.yaml
src/sqvm/evolution/
scripts/run_stage_05_qutip_evolution.py
tests/test_qutip_evolution_*.py
output/stage_05_qutip_solver_validation/
output/stage_05_qutip_evolution/
```

These are not created by this design revision.

## Open Issues

None within the frozen v0.1 design boundary. Gate calibration, noise, measurement, readout, and gate-fidelity
claims remain explicitly out of scope for later stages; they are not blockers for the defined effective
rotating-frame simulation.
