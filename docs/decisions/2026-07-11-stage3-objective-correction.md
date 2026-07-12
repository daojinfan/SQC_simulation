# Stage 3 objective correction: q1-q2 coupling versus coupler flux

Date: 2026-07-11

Status: proposed for independent design review

Review history:

```text
Round 1: NOT APPROVED. The physical experiment was accepted, but numerical acceptance,
resonance alignment, config schema, continuity domain, and post-write gate interfaces required closure.
Round 2 candidate closes those contracts before implementation handoff.
```

## User requirement

Stage 3 must validate the qubit-qubit avoided crossing, not the two qubit-coupler crossings.

The required evidence is:

1. Hold the coupler flux fixed, tune one qubit through the other qubit, and observe a resolved `q1-q2` avoided crossing.
2. Repeat the same qubit-resonance scan at different coupler flux values and show that the `q1-q2` minimum splitting, and therefore the magnitude of the effective coupling, changes.

## Mismatch in Stage 3 v0.1

The frozen Stage 3 v0.1 design and implementation scan only the coupler flux and treat these as the acceptance candidates:

```text
q1-c: |100> versus |010>
c-q2: |010> versus |001>
```

That implementation does not tune `q1` and `q2` through mutual resonance and does not produce
`q1-q2` splitting as a function of coupler flux. Its formal result is internally valid, but it answers a
different physical question.

Disposition:

```text
Stage 3 v0.1 implementation and diagnostic artifact remain immutable historical evidence.
The v0.1 result does not satisfy the corrected Stage 3 product objective.
Stage 4 remains blocked.
The v0.1 q1-c and c-q2 diagnostics may inform scan exclusions, but are not Stage 3.1 acceptance gates.
```

## Feasibility pilot

A read-only, non-acceptance pilot used the existing approved Hamiltonian and solver without writing files.
It held `q1=0.10 Phi0`, scanned `q2`, and repeated the scan at several coupler flux values.

The pilot found the `q1-q2` resonance near `q2=0.099755 Phi0` and obtained:

| coupler flux Phi0 | minimum splitting MHz | inferred `abs(g_eff)` MHz | maximum coupler participation |
|---:|---:|---:|---:|
| 0.200 | 4.952508 | 2.476254 | 0.000004 |
| 0.270 | 4.950134 | 2.475067 | 0.000006 |
| 0.360 | 4.932356 | 2.466178 | 0.000039 |
| 0.385 | 4.873674 | 2.436837 | 0.000346 |
| 0.394 | 4.430902 | 2.215451 | 0.012300 |
| 0.396 | 7.116979 | 3.558489 | 0.164630 |
| 0.400 | 5.164472 | 2.582236 | 0.001800 |

These values are range-selection evidence only. They are not cutoff-converged Stage 3.1 results.
The acceptance anchors stop at `0.385 Phi0`; points closer to the coupler resonance remain diagnostic.
The `0.396 Phi0` point demonstrates why a coupler-participation gate is required: near a three-mode
resonance, half the observed two-branch gap is not automatically an interpretable qubit-qubit coupling.

## Corrected physical experiment

The Stage 3.1 scan is nested:

```text
for each configured coupler flux:
    hold q1 flux fixed at 0.10 Phi0
    scan q2 flux across q1-q2 bare resonance
    track |100>, |001>, and spectator |010>
    resolve the q1-q2 minimum dressed splitting
    report abs(g_eff) = minimum_splitting / 2 only after pairwise and resonance-alignment gates pass
```

The experiment reports coupling magnitude only. Avoided-crossing splitting alone does not determine the
sign of the effective coupling. A signed coupling requires a separately reviewed effective-Hamiltonian
projection and is outside Stage 3.1. If target bare-projector, coupler-participation, or resonance-alignment
evidence fails, Stage 3.1 records the splitting but leaves `abs_g_eff` null.

## Gate decision

Stage 3.1 passes only if:

1. The `0.200`, `0.270`, and `0.385 Phi0` anchors each have a resolved `q1-q2` avoided crossing.
2. Resolution includes full-evidence-domain target bare-projector, total-excitation, coupler-participation,
   subspace-continuity, character-exchange, and resonance-alignment gates.
3. The difference in splitting between the reference and at least one comparison point is at least five
   times the combined numerical uncertainty.
4. Full flux-vector/config provenance, 44-case solver validation, summed cutoff convergence, exact
   inner-flux/level/solver uncertainty, runtime, canonical artifact/notebook, and post-write report pass.

Lowering a continuity threshold or accepting a coupler-hybridized point is not an allowed way to pass.

## Required sequencing

```text
1. Independently review and freeze the Stage 3.1 design.
2. Generalize full flux-vector keys and Hamiltonian overrides.
3. Regenerate solver validation for q2 and coupler flux coverage.
4. Obtain a new independent solver approval.
5. Run one formal Stage 3.1 acceptance.
6. Obtain independent artifact acceptance before Stage 4.
```
