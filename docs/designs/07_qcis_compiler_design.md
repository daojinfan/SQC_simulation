# Stage 7 Detailed Design: QCIS Compiler And QuTiP Control Path

## 1. Status and authority

This document is frozen as Stage 7.0 implementation authority by the companion Stage 7 QCIS design-freeze
record. It incorporates the user-supplied `QCIS说明.md` into the Stage 7 calibration architecture. This reviewed
Stage 7 QCIS profile, rather than the extracted source note by itself, is the executable authority because the
source note reports blurred formulas, unrendered LaTeX, and TODO sections.

Source provenance at draft time:

```text
source path   D:/Backup/xwechat_files/wxid_4v647acxz5er22_8450/temp/RWTemp/2026-07/
              9e20f478899dc29eb19741386f9343c8/QCIS说明.md
source bytes  14192
source SHA256 C638A4EE10E0F2D98B20E7A6B0E7ADD6718CADFC3EA00AEFC15F87961FF15338
source basis  text extracted from video frames; no audio was used
```

The byte-identical repository mirror is `docs/references/QCIS说明.md`; its 14192 bytes and SHA-256 match the
reviewed external source. `docs/references/qcis_source_snapshot.json` binds both locations, limitations, size,
and hash. The mirror preserves the source but does not elevate incomplete formulas or TODO text into executable
authority.

## 2. Architectural decision

QCIS is the only external control-program language for Stage 7 and all later calibration experiments. The
previously proposed JSON Gate/Macro IR and Pulse IR are retained only as typed internal compiler structures;
experiment authors and backends do not submit them directly.

The mandatory execution chain is:

```text
Stage 6 scan point + QCIS template + typed bindings
  -> concrete point-local QCIS source
  -> strict lexical and grammatical parse
  -> typed QCIS AST
  -> QAgent/configuration/macro resolution
  -> discrete logical-lane waveform plan
  -> Stage 4.1 electronics and effective-control compilation
  -> verified effective I/Q/flux waveforms
  -> Stage 5.1 Hamiltonian coefficient construction
  -> QuTiP QobjEvo evolution
  -> registered model observables
```

No calibration experiment may construct Stage 4 schedules, waveform arrays, Hamiltonian callbacks, or QuTiP
objects directly. Static-spectrum evaluation may be used as a diagnostic or feasibility preflight, but it
cannot by itself publish a Stage 7 calibration recommendation. Recommendation-bearing calibration evidence
must pass through the QCIS-to-waveform-to-QuTiP path.

The backend receives only a verified control handle. It cannot parse QCIS, expand a gate, select a waveform
class, reinterpret a phase, or regenerate samples.

## 3. Stage 7 QCIS profile and request contract

The first executable profile is `qcis_stage7_calibration_v1`. The Stage 7 request `program` object has exact
keys:

```text
program_schema_version  "0.1"
instruction_set_id      "qcis_stage7_calibration_v1"
template_id             registered immutable experiment-template ID
template_sha256         uppercase SHA-256 of exact template source bytes
source_format           "qcis_template"
source                  nonempty canonical LF text
bindings                exact mapping of placeholder ID to typed literal or scan_ref
```

QCIS retains the source format `<Operation> <Target> <Parameters>`. Operation and target tokens are ASCII and
case-sensitive. Parameters in concrete QCIS are finite base-10 numbers only. A request template adds one
compile-time token, `$<binding_id>`, only where the selected opcode expects a numeric parameter. It cannot
replace an operation, target, wave index, parameter count, or structural token. It permits no expression,
arithmetic, interpolation, environment reference, include, import, macro definition, or executable object.

`tStart` is structural and cannot be bound or scanned in profile v1. `length` may be bound only when the exact
experiment definition permits it and every axis coordinate is a positive integer sample count. Rabi v1 forbids
a length binding and uses one policy-bound literal length; CZ coarse v1 permits a hold-length binding. This
keeps each registered template's instruction graph fixed and prevents cross-experiment reinterpretation.

Canonical Stage 7 source is UTF-8 without BOM, ASCII-token-only, LF-terminated, and contains one instruction per
nonempty line. Tokens are separated by one ASCII space; tabs, comments, blank lines, leading/trailing spaces,
CR, NUL, and Unicode lookalikes reject. Operation tokens match `[A-Z][A-Z0-9]*`, target tokens match the exact
QAgent registry, and placeholder IDs match `[a-z][a-z0-9_]{0,63}` after `$`. Concrete real tokens match
`-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE]-?(0|[1-9][0-9]*))?`; leading plus, exponent plus, leading zero, trailing
decimal point, and negative zero reject. Parsing must yield finite binary64. `NaN`, infinities, hex, locale
commas, unit suffixes, and `pi` expressions reject. Integer positions match `0|[1-9][0-9]*` unless an operand
explicitly admits the fixed negative sentinel `tStart=-1`.

For each Stage 6 point, the materializer replaces every placeholder with the canonical shortest round-trip
base-10 representation of its already validated binary64 value. The resulting concrete source is plain QCIS
with numeric parameters. Parsing and semantic validation run again on the concrete source. Unused bindings,
missing bindings, duplicate placeholder definitions, unit mismatch, a placeholder in a structural position,
or non-byte-identical rematerialization rejects before run reservation.

Admission loads the experiment definition's registered template and requires exact equality of `template_id`,
`template_sha256`, and `source` bytes. It also requires the binding-key set, every placeholder occurrence count,
unit, and allowed opcode/operand position to equal the definition. A syntactically valid unregistered QCIS
program cannot run under a registered calibration experiment. The verifier reloads the definition and repeats
all comparisons.

The exact version matrix is:

```text
request 0.1 -> program null -> Stage 6 run/dataset/verifier 0.1
request 0.2 -> qcis_stage7_calibration_v1 -> Stage 7 run/dataset/verifier 0.2
```

The earlier draft identifier `sqvm_calibration_ir_v1` is withdrawn before freeze and must never be accepted as
an alias. There is no silent upgrade from the Stage 6 null program or from any earlier draft.
Request/run `0.1` never parses, emits, inventories, or verifies a QCIS payload.

## 4. QAgent, configuration, and waveform registries

Instruction meaning is registry-driven, matching the QCIS source design. Every run binds immutable snapshots
and hashes for:

```text
QCISInstructionProfile   grammar, opcode schemas, units, phase convention, supported/reserved status
QAgentRegistry           QCIS target token -> named device component and logical channels
GateConfiguration        active setting IDs and implementation switches
WaveformRegistry         setting ID -> reviewed waveform class, operands, bounds, and formula version
ClockProfile             sample rate, dt, index convention, and rounding
CompilerSnapshot         parser/materializer/expander/waveform compiler source and environment
```

The default simulator QAgent registry maps canonical tokens `Q1`, `Q2`, and `C` to named components `q1`, `q2`,
and `c`; the mapping is data, not a hard-coded positional tuple. Every mapping continues to bind the Stage 2
tensor order `q1/c/q2`. Alternate spellings such as `Q01` require a later registry/profile version and are not
silent synonyms.

The qubit gate configuration includes reviewed equivalents of `active_xy_setting`, `active_xy2_setting`,
`xy_pi_impl`, `z_gate_impl`, carrier frequency, drive detuning, and DRAG coefficient. The coupler configuration
includes `active_cz_setting`. A named setting resolves to one immutable waveform-registry entry. Missing,
ambiguous, cyclic, out-of-range, device-mismatched, or hash-mismatched entries reject before waveform creation.

The simulator profile fixes units that the source note leaves hardware-dependent:

```text
tStart and length                 integer AWG samples
PLSXY amplitude                   GHz complex-drive envelope scale
PLSXY frequency                   GHz physical carrier frequency
PLSXY phase                       rad
PLSXY dragAlpha                   AWG samples in profile v1
PLS amplitude on Q1/Q2/C Z lanes absolute Phi/Phi0 under the selected setting
gate/config frequencies           GHz
all materialized samples          finite binary64
```

No DAC-code, Hz-to-bias, coupler-strength-to-bias, or arbitrary numeric-waveform conversion is inferred. Such
conversion requires an independently frozen mapper in the registry.

## 5. Opcode support matrix

The source QCIS vocabulary is preserved in the profile registry, but only opcodes with complete Stage 7
semantics are executable. Unknown and reserved opcodes fail closed; they are never ignored.

| Group | Stage 7 profile status | Lowering |
| --- | --- | --- |
| `X2P`, `Y2P` | executable after prerequisite calibration | reviewed XY/2 gate setting to `PLSXY` semantics |
| `X`, `Y`, `X2M`, `Y2M`, `XY`, `XY2P`, `XY2M` | reserved pending decomposition/sign review | no execution |
| `RX`, `RY`, `RXY` | reserved pending exact decomposition review | no execution |
| `PLSXY` | executable for reviewed wave indices/settings | explicit XY waveform |
| `PLS` | executable for reviewed wave indices/settings | explicit Z/flux waveform |
| `I` | executable | append zeros on the lanes of one QAgent |
| `RZ` | executable | zero-duration accumulated XY-frame update |
| `B` | executable | QAgent-lane alignment with zero padding |
| `CZ` | executable only after accepted CZ setting | reviewed coupler setting to `PLS` semantics |
| `Z`, `S`, `SD`, `T`, `TD` | reserved until phase/decomposition review | no execution |
| `DTN` | reserved until a versioned flux/frequency mapper exists | no execution |
| `X12` | reserved until level-2 operators/settings are qualified | no execution |
| `FSIM` | reserved because source handling is TODO | no execution |
| `M` | Stage 8 only | rejected before readout/measurement entrance |
| `RST`, `LRU` | reserved for reset/leakage-reduction physics | no execution |
| `SWD`, `SWA`, `SET`, `CONDX` | hardware/control-plane scope | no execution |

The profile records `X4P`, `X4M`, `Y4P`, and `Y4M` as reserved because the note lists them without a complete
standalone contract. A later profile may enable a reserved opcode without changing this version.

## 6. Discrete timing semantics

QCIS timing is resolved on the bound AWG sample grid before Stage 4.1. Every pulse occupies a half-open sample
interval `[start, start + length)`. This removes the apparent off-by-one difference between an inclusive last
sample and an exclusive end cursor.

Each QAgent owns an XY lane cursor and a Z lane cursor, initially zero. Its append cursor is the maximum of its
lane cursors.

- A calibrated gate without `tStart` begins at the target QAgent append cursor.
- `PLS` or `PLSXY` with `tStart=-1` begins at the target append cursor. `-1` is the only accepted negative value.
- `PLS` or `PLSXY` with `tStart>=0` begins at that absolute sample index. It may express parallel placement but
  cannot overlap another waveform on the same logical lane.
- Absolute placement never moves a cursor backward; the new cursor is the maximum existing end and pulse end.
- `I Q length` starts at Q's append cursor, appends zeros to both Q XY and Z lanes, and advances both. For a
  coupler it advances the available Z lane.
- `B Q1 Q2 ...` computes the maximum append cursor and zero-pads every listed QAgent lane to it.
- `RZ` has zero duration and changes only the target XY-frame accumulator.

Starts and lengths are exact integers. Invalid lengths, integer overflow, overlap, out-of-budget placement,
duplicate B targets, or missing lanes reject. Lowered pulses are sorted only after source-order cursor/frame
replay; sorting cannot alter semantics.

## 7. XY gates, phase, carrier, and DRAG

The supplied QCIS formula is the compatibility target for the reviewed XY subset:

```text
[envelope(t) + dragAlpha * envelope'(t) * exp(-i*pi/2)]
* exp(i * [carrier terms - gate_phase - input_phase - accumulated_RZ])
```

Profile v1 differentiates the envelope with respect to the integer sample coordinate, so `dragAlpha` is in
AWG samples. The compiler uses:

```text
qcis_phase = -(gate_phase + input_phase + accumulated_RZ)
complex_envelope = envelope - i * dragAlpha * envelope_derivative
```

QCIS waveform generation is complete before Stage 4.1. Stage 4.1 receives the resulting I/Q arrays and does not
recompute a Gaussian or DRAG derivative. If a legacy Stage 4 test adapter expresses the same formula as
`g+i*beta*d` with `d` differentiated in ns, the comparison-only conversion is
`beta_ns=-dragAlpha_samples*dt_ns`; it is not an alternate waveform authority.

The executable gate phases are `X2P=0` and `Y2P=pi/2`; phases for reserved gates remain source-profile test
vectors but do not grant execution. The simulator defines `RZ(theta)=exp(-i*theta*Z/2)`. Under the selected
QCIS convention, `RZ Q theta` adds `theta` to the accumulator and subsequent XY complex envelopes receive
`exp(-i*theta)`. It emits no waveform and contributes no Hamiltonian coefficient. The phase is applied exactly
once; an implementation must prove the state-level RZ convention with an independent QuTiP test, not only
compare waveform plots.

The project does not sample a GHz RF carrier in Stage 4. It uses a versioned analytic-carrier factorization:

```text
physical carrier metadata = f01 + drive_detune, or explicit PLSXY frequency
Stage 4 logical I/Q       = complex baseband envelope with QCIS gate/input/RZ phase
Stage 5.1 frame term      = bound carrier metadata, consumed exactly once
```

`fLO` and AWG-rate carrier sampling remain provenance but are not multiplied into Stage 4 baseband samples.
Stage 5.1 must reproduce the QCIS phase at each pulse start. The MVP permits one physical carrier per QAgent per
point; a mid-program change rejects until piecewise-carrier evolution is designed.

Endpoint removal, normalization, derivative variable, sample coordinate, and units belong to the selected
waveform formula ID and cannot be inferred from `gaussian`. In particular, QCIS wave index 1 and the accepted
Stage 4 demo Gaussian are not aliases unless an independent sample-vector test proves exact equality.

## 8. PLS and PLSXY waveform compilation

The source positional forms are retained. Stage 7 initially admits only registry entries with complete,
independently tested semantics:

```text
waveIndex 0  rectangle
waveIndex 1  gaussian/DRAG
```

Exact arity is selected by operation, target kind, wave index, and registry entry. Extra/missing parameters
reject. `PLSXY` produces a complex logical envelope and explicit I/Q lanes; `PLS` produces one logical Z/flux
lane. A listed wave index is not executable merely because it appears in the source note.

The initial exact executable forms are:

```text
PLSXY Q 1 tStart length amplitude frequency phase dragAlpha r_sigma
PLS   Z 0 tStart length target_flux 0 0 0 width
```

Here Q is a registered qubit and Z is a registered q1/q2/c flux-capable QAgent. `tStart`, `length`, and `width`
are integer samples; v1 requires `width=length`. The three unused PLS positions are literal zero and cannot be
bound. For PLSXY, `amplitude` is GHz, `frequency` is physical-carrier GHz, `phase` is rad, and `dragAlpha` and
`r_sigma` are in samples. Bounds come from the hash-frozen bootstrap or accepted-calibration policy.

For `k=0,...,length-1`, the QCIS Gaussian formula is exact:

```text
center = (length - 1)/2
g[k]   = exp(-0.5*((k-center)/r_sigma)^2)
dg[k]  = g[k] * (-(k-center)/r_sigma^2)
z[k]   = amplitude * (g[k] - i*dragAlpha*dg[k])
         * exp(-i*(phase + accumulated_RZ))
```

Evaluation is scalar binary64 in increasing k. The exact operation order is `base=complex(g,-dragAlpha*dg)`,
`scaled=amplitude*base`, `rotation=complex(cos(-phase_total),sin(-phase_total))`, then
`z=scaled*rotation`, without vectorized reassociation or fused-multiply-add substitution. The nonzero oracle in
`docs/designs/07_qcis_compiler_test_vectors.md` is authoritative for the exact operation result.

There is no endpoint subtraction or area normalization. The physical carrier is metadata and is consumed once
by Stage 5.1. A calibrated `X2P` or `Y2P` uses the same registered formula but adds its gate phase inside the
single leading minus sign.

For admitted rectangle PLS, the logical absolute-flux array starts at the named device idle flux. On the
half-open active interval it is exactly `target_flux`; after the interval it is idle again. Equivalently the
delta input to Stage 4.1 is `(target_flux-idle_flux)` times the unit rectangle. This simulator profile is more
specific than hardware QCIS and is identified by its profile/registry hashes.

Index `2` (`flattop`) is reserved: the source describes an error-function construction, while the existing
Stage 4 demo uses cosine edges, so they cannot share an implementation without a new exact formula ID. Indices
`3` (`rrring`), `4` (`pump`), `5` (`acz`), `6` (`nacz`), `7` (`slepian`), and `8` (`accz`) remain reserved
until their formula, normalization, units, interpolation/indexing, and bounds are frozen.

Numeric wave index `-1` is also reserved. The note does not establish whether samples are DAC codes, normalized
voltage, GHz drive, or Phi0, and its examples leave grammar/length ambiguity. A later profile must bind sample
units, lane order, length, scaling, and payload limit. Until then numeric PLS/PLSXY rejects.

For every admitted waveform the compiler records operation, target, wave index, setting, source parameters,
formula ID/hash, sample grid, logical samples, and per-lane SHA-256. No backend regenerates arrays from high-level
parameters.

## 9. CZ and flux controls

Before accepted coupler/CZ calibration exists, scans use explicit `PLS C ...` with typed scan bindings and a
reviewed waveform. `CZ C` becomes executable only after `active_cz_setting` identifies an accepted immutable
setting with complete prerequisites. Expansion records its ID/hash and emits the same logical waveform plan
that direct PLS would emit.

Admitted Z-lane PLS amplitude is absolute named-device flux `Phi/Phi0`. Stage 4.1 alone converts it to
idle-relative electronics input and reconstructs effective absolute q1/q2/c flux after latency, FIR, DAC, and
crosstalk. QuTiP receives only verified effective absolute flux. A scan-axis-to-Hamiltonian shortcut is forbidden.

`DTN` is not an alias for PLS. It may require `zbias2f01_mapper` or `zbias2g_mapper`, root selection,
interpolation, and DAC conversion, so it remains reserved until separately designed.

## 10. Stage 4.1 waveform artifact

The compiler lowers to a `QCISLogicalWaveformPlan` containing profile/point IDs, concrete source and AST hashes,
instruction expansion and cursor/frame traces, sample count/dt, logical q1/q2 XY arrays, logical q1/q2/c
absolute-flux arrays, carrier metadata, per-array dtype/shape/unit/hash, and all registry/compiler authority hashes.

Stage 4.1 validates the plan and applies the accepted electronics chain. It does not reparse source or rerun gate
expansion. Its output retains:

```text
effective.time_center_ns
effective.xy_drive_GHz.q1/q2.{i,q}
effective.absolute_flux_phi0.q1/q2/c
```

The Stage 4.1 rebaseline proves accepted Stage 4 scenarios remain byte-identical. It cannot relax the frozen
entry or bypass channel, conflict, DAC, latency, FIR, mixing, clipping, and reconstruction checks.

## 11. Stage 5.1 and QuTiP contract

Stage 5.1 accepts only a post-publication `VerifiedControlHandle`. For each Stage 4.1 hold interval it builds:

```text
H_static(Phi_q1[k], Phi_c[k], Phi_q2[k])
+ H_drive(epsilon_q1[k], epsilon_q2[k], carrier/frame metadata)
```

Named mapping remains `q1/c/q2`. Hamiltonian values remain GHz until the sole `2*pi*GHz = rad/ns` conversion.
QuTiP `QobjEvo` receives piecewise-constant callbacks indexed by the accepted edge grid. It never receives QCIS
text, calibration settings, logical pre-electronics samples, or sampled RF carrier values.

The evolution report binds the concrete QCIS, AST, expansion trace, waveform plan, effective-control inventory,
Hamiltonian/operator snapshot, QuTiP/environment snapshot, initial state, observables, solver diagnostics, and
result hashes.

## 12. Calibration experiment programs

Every Stage 7 definition owns an immutable QCIS template ID and allowed-binding schema:

| Experiment | Required QCIS construction |
| --- | --- |
| qubit spectroscopy | explicit `PLSXY` with frequency binding; optional `I/B` alignment |
| Rabi amplitude at policy duration | explicit `PLSXY` with amplitude binding and literal policy length |
| Ramsey frequency | two explicit bootstrap `PLSXY` pulses separated by `I`; calibrated `X2P` only after admission |
| DRAG dragAlpha | explicit `PLSXY` with `dragAlpha` binding |
| coupler flux calibration | explicit `PLS C`; trajectory comes only from compiled effective flux |
| CZ coarse scan | explicit `PLS C` with flux/length bindings; `CZ C` only after calibration |

Definitions cannot emit hidden waveforms or modify source after point binding. Allowed operations, targets,
binding positions/ranges, and observable set are exact. The verifier rematerializes and recompiles every point.

Bootstrap permits only explicit PLSXY/PLS forms registered for the experiment. Calibrated gates remain
unavailable until prerequisite keys exist in an accepted simulation calibration.

## 13. Evidence topology and verification

A completed Stage 7 run adds at least:

```text
program/template.qcis
program/bindings.json
program/instruction_profile.json
program/qagent_registry.json
program/gate_configuration.json
program/waveform_registry.json
program/compiler.json
program/point_sources.jsonl
program/point_asts.jsonl
program/expansion_traces.jsonl
controls/logical_waveform_inventory.json
controls/effective_control_inventory.json
```

Each point binds coordinates, concrete QCIS/AST/trace hashes, logical waveform hashes, effective-control SHA,
backend-command SHA, and result SHA. Verification reruns binding, parsing, expansion, waveform generation,
Stage 4.1 verification, and coefficient-inventory checks.

Required tests cover token/arity/number/target/wave-index strictness, unsupported opcodes, placeholders and units,
`tStart=-1`, absolute placement, overlap, `I/RZ/B`, calibrated gate expansion, phase/DRAG signs, formula/sample
rules, reserved-feature rejection, tamper detection, QCIS-to-QuTiP replay, and cross-environment determinism.
The freeze-level byte oracles and stable rejection reasons are defined in
`docs/designs/07_qcis_compiler_test_vectors.md` and are part of the design authority.

## 14. Unsupported source ambiguities outside the frozen v1 subset

The following remain blocking for their affected opcode, not for the minimal subset:

1. Authoritative formulas and units beyond the initial reviewed waveform set.
2. Numeric PLS/PLSXY sample units, scaling, length, and IQ split.
3. The extracted `Z/S/SD/T/TD` virtual-phase table and PULSE decompositions.
4. Exact `RXY/RX/RY` decomposition and boundary behavior.
5. Complete `DTN` mapper equations, tolerances, interpolation, and output units.
6. Complete `CZ`, `FSIM`, `M`, `RST`, `LRU`, and conditional/control-plane behavior.
7. Whether deployed QCIS uses `Q1`, `Q01`, or another QAgent naming registry.
8. Deployed-hardware interpretation of `PLSXY frequency` remains outside simulator compatibility claims. For
   `qcis_stage7_calibration_v1`, its meaning is already frozen as physical carrier GHz and is not unresolved.

These require an authoritative specification or separately reviewed simulator profile. Unsupported entries stay
visible and fail with stable reason codes; they never receive a best-effort interpretation.
