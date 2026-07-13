# Stage 5 Freeze-First Decision: QuTiP Time Evolution

- Date: 2026-07-12
- Status: proposed for independent design review; not an approval
- Scope: Stage 5 design only

## Decision

Stage 5 is a complete-charge-basis interaction-picture simulation. It applies the exact
`U.dag() * H_lab(t) * U - i*U.dag()*dU/dt` transformation to every accepted Stage 2.1 static, capacitance
coupling, and flux-dependent term. It never applies a zero-number-sector projection to those terms; exchange
coupling and q2/coupler flux off-diagonal dynamics are retained. RWA is used only for the analytically derived
XY carrier drive. It consumes only the approved Stage 4 `effective` numeric arrays; carrier references are
separately consumed from the same approved artifact's XY logical-pulse metadata. It never consumes or
recompiles logical targets, AWG codes, requested/delivered voltages, or readout envelopes.

The carrier reference is not display-only. The loader derives the global, immutable map from the approved
artifact by requiring exactly one finite XY `carrier_frequency_GHz` for each `q1_xy` and `q2_xy` channel:
`f_ref[q1]=5.193479909897604 GHz`, `f_ref[q2]=5.331633051009526 GHz` for the current artifact. A repeated
channel must have byte-identical frequency; missing, extra, or unequal values fail closed. The individual
`phase_rad` values are provenance fields only because phase is already represented exactly in effective I/Q;
applying it again is forbidden.

The complete machine contract, including the interaction transform, XY-only RWA, lab-frame reference/labeling,
exact schemas, run attempt
gate, solver-validation gate, cutoff embedding, publication lifecycle, and Stage 6 readiness is frozen in the
linked design. A future independent freeze manifest must bind these three documents and the approved Stage 4
exact-five evidence before implementation can proceed.

Physical initial state and computational projectors are selected only from the complete `t0` lab-frame
Hamiltonian eigensystem. Their interaction-frame representation is obtained with `U(t0).dag`; an
interaction-frame quasienergy ground is never a substitute. Formal attempt audit uses a permanent,
authorization-SHA-derived receipt/lock pair under `output/stage_05_qutip_evolution_attempts/`.

QuTiP options, tolerances, and solver probes are freeze constants, not candidate input. Solver validation
records only actual results and fixed spec identity/hash; its validator independently rebuilds expected arrays,
thresholds, and aggregates. Formal execution uses ordered parent/child IPC and hard 120.0 s scenario plus
300.0 s total monotonic deadlines; 5.0 s grace only terminates/collects and never extends acceptance time.

Interaction-survival uses no cross-representation matrix-element identity. It validates complete coupling and
the mandatory approved full-flux-triple difference between `q2_resonance_flux` indexes 0 and 66 using unitary
similarity invariants, and defines its off-diagonal dynamics only in the frozen common local-energy basis. An
isolated-q2 counterfactual, if later recorded, is diagnostic-only and cannot replace the approved input-chain
probe. Scenario IPC starts before all scenario-specific preprocessing and ends
only after its numerical/payload work; malformed, missing, ordered-state, or child-exit failures immediately
terminate the whole child tree and block formal publication.

## Solver Environment Decision

The approved interpreter is exactly `C:\Users\fandaojin\anaconda3\python.exe`; its current read-only probe
reports Python `3.12.7`, QuTiP `5.1.0`, NumPy `2.3.3`, and SciPy `1.16.1`. This is not a formal validation
result. Stage 5 supports only QuTiP `>=5.1.0,<5.2.0` and must run its separately approved validation gate
under that interpreter before smoke or formal evolution. A PATH-local interpreter is irrelevant to this gate.

## Protected Upstream Bindings

| Evidence | SHA-256 |
| --- | --- |
| Stage 2.1 approval | `CA2A799A01212C8FCF72ED5A9A046A9C9A3671A93A8C41C026512B28983CEBA9` |
| Stage 3.1 acceptance approval | `5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D` |
| Stage 3.1 artifact | `76C539FF22EAB54E5E10C93526E20AFB68A39507BCD9BE2DAD5A2897282FAD35` |
| Stage 4 design freeze | `99D2DFA48582DC9DBE792AD7157BD71CCC9E0610ADEEEE62791FF6E3EF52FBCD` |
| Stage 4 artifact | `034ED1C593935EEC1049DB2331148CECFCC46FBED44DD7CF00BC5CB3E50081A8` |
| Stage 4 approval | `AADFCA40390FE3EC16B3362DAF23588F9DABC181F3B69BF96D981324C923EF5A` |
| Stage 4 final review | `22ED82D5C2D0F7E6F1879F9E9D2B62C74DC006EED0168E618489139A9BF6FD50` |

## Consequences

There is no Stage 5 approval, freeze manifest, solver-validation candidate, run authorization, numerical
artifact, or acceptance result in this delivery. The documents remain proposed and cannot themselves permit a
smoke run, a formal attempt, or Stage 6.
