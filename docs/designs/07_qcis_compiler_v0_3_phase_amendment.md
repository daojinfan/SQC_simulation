# QCIS Compiler v0.3 Drive-Phase Amendment

- Date: 2026-07-16
- Status: reviewed initial design; user-confirmed on 2026-07-16; pending freeze
- Supersedes for physics admission: QCIS v0.2 scalar per-QAgent carrier handoff
- Preserves: all v0.2 source, AST, trace, waveform, and rejection byte oracles

## 1. Reason for the amendment

QCIS v0.2 builds complex XY envelopes from amplitude and initial phase, then records one physical carrier
frequency per QAgent. That representation cannot compile `X` and `X12` in one program because they drive the
`01` and `12` transitions at different frequencies. It also leaves pulse-frequency phase evolution to a future
backend even though QCIS is required to lower instructions into concrete input waveforms.

Version 0.3 replaces the scalar pulse-carrier handoff with a fixed qubit reference frame and compiles each
pulse's drive detuning into its logical complex I/Q samples before additive overlap.

## 2. Version boundary

The v0.2 profile and schema remain readable and byte-stable. No v0.2 array, trace, hash, fixture, or reason code
is silently reinterpreted.

The new exact profile is `qcis_stage7_calibration_v3` with compiler schema `0.3`. Stage 4.1 physics admission
accepts v0.3 only. A v0.2 compilation remains an identity fixture and cannot construct a
`VerifiedControlHandle`.

## 3. Frequency authorities

Each qubit QAgent configuration binds:

```text
reference_frequency_GHz
frequency_source = bootstrap_seed | accepted_simulation
calibration_run_id
revision
setting_hash
```

`reference_frequency_GHz` is the current calibrated idle `f01` and defines the rotating reference frequency
`f_ref`. Before the first accepted spectroscopy result, a device-model prior may be used only as a bound
`bootstrap_seed`. One run binds one immutable reference-frequency snapshot; it cannot adopt a new value midway.

Gate drive frequencies are:

```text
X/X2P/X2M/Y/Y2P/Y2M/XY/RX/RY/RXY: f_drive = f01
X12:                                  f_drive = f12
f12 = f01 + anharmonicity_GHz
direct PLSXY:                         f_drive = source frequency operand
```

`active_xy12_setting` declares `transition="12"` and owns its envelope/amplitude/duration/DRAG/phase offset. It
does not duplicate an absolute `f12`. A later direct f12 calibration updates anharmonicity or introduces a new
versioned authority; contradictory f01/f12/anharmonicity fields are forbidden.

## 4. Exact phase formula

For one XY contribution with actual start sample `s0`, local sample `k`, and clock `dt_ns`:

```text
t_abs_ns[k] = (s0 + k + 0.5) * dt_ns
detuning_GHz = f_drive_GHz - f_ref_GHz
theta_raw[k] = phase_total_rad + 2*pi*detuning_GHz*t_abs_ns[k]
theta[k] = remainder(theta_raw[k], 2*pi)
rotation[k] = complex(cos(-theta[k]), sin(-theta[k]))
epsilon[k] = scaled_envelope[k] * rotation[k]
```

Evaluation is scalar binary64 in increasing `k` with the displayed operation order. Each sample is computed from
absolute time; recursive rotation multiplication is forbidden. Freeze-level nonzero-detuning byte oracles own
the exact `remainder`, trigonometric, multiplication, and sign behavior.

The global QCIS `t=0` is the phase origin. `I` and `B` may change the actual start of append-style gates but do
not reset phase. Absolute-`tStart` `PLSXY` is not moved by `I/B` and uses its stated absolute start in the phase
formula. A future NCO/frame-reset operation requires a new opcode/profile; reset is never implicit.

## 5. Initial phase ownership

For calibrated gate macros:

```text
phase_total = gate_phase + setting.phase_offset + accumulated_RZ
```

Direct `PLSXY` uses its explicit absolute `phase` and does not consume accumulated `RZ`. Both forms add the
detuning ramp from Section 4. QCIS applies this complete phase exactly once before logical contributions are
summed.

Stage 4.1 applies only electronics. Stage 5.1 receives the effective complex I/Q plus q1/q2 reference
frequencies for its rotating frame. It does not reapply a pulse phase, pulse drive frequency, detuning ramp, or
sampled carrier. The Hamiltonian boundary owns the only `2*pi*GHz = rad/ns` conversion of Hamiltonian values;
the QCIS phase formula's explicit dimensionless `2*pi*f*t` is a separate, frozen waveform operation.

## 6. Additive multi-frequency behavior

Every XY contribution computes its own detuning phase before addition:

```text
logical_xy[k] = sum_j epsilon_j[k]
```

Consequently, `X` and `X12`, direct spectroscopy pulses, and other different-frequency contributions may coexist
or overlap on one logical lane. There is no scalar per-QAgent drive carrier and no
`CARRIER_CHANGE_UNSUPPORTED` rejection in v0.3. Aggregate DAC and device limits remain Stage 4.1 authorities.

## 7. Baseband admission

Because the sampled complex array represents drive detuning in the q1/q2 reference frame:

```text
abs(f_drive_GHz - f_ref_GHz) < sample_rate_Hz / 2 / 1e9
```

For the accepted 2 GHz sample rate, the strict bound is `abs(detuning_GHz) < 1 GHz`. Equality, non-finite
values, and out-of-band values reject with `DRIVE_DETUNING_OUT_OF_BAND`. Experiment or setting policies may
impose tighter bounds but cannot relax the clock bound. Folding, modulo-frequency aliasing, and warning-only
admission are forbidden.

## 8. v0.3 logical plan evidence

The plan binds:

```text
frame_reference_frequency_GHz.q1/q2
frame_reference_authority_sha256
drive_event_inventory
```

Each XY drive event records:

```text
event_id
source_instruction_index
target
transition
actual_start_sample
sample_count
phase_rule_id
phase_total_rad
f_drive_GHz
f_ref_GHz
detuning_GHz
logical_array_contribution_sha256
setting_evidence
```

The aggregate logical array inventory remains raw-byte authoritative. Event records are evidence and replay
inputs; no downstream layer regenerates a different waveform from their high-level labels.

## 9. Required tests

- v0.2 byte oracles and rejection behavior remain unchanged;
- v0.3 `X` has zero detuning when f01 equals the reference;
- v0.3 `X12` uses `f01 + anharmonicity` and has the expected nonzero phase ramp;
- `X` and `X12` coexist in one program without carrier conflict and sum sample by sample;
- direct PLSXY spectroscopy frequency scans change logical I/Q bytes point by point;
- global absolute-time phase differs at different starts while local envelope bytes remain equal;
- `I/B` changes append-style start and phase but does not reset the oscillator;
- absolute PLSXY ignores `I/B` placement and accumulated RZ;
- gate macros consume accumulated RZ once;
- positive/negative/zero detuning, phase wrapping, Nyquist-near values, exact Nyquist, and out-of-band values;
- tampered f01, anharmonicity, frequency authority, event detuning, contribution, or aggregate array fails closed;
- repeated same-environment compilation is byte-identical.

## 10. Implementation order

1. Add v0.3 profile/schema dispatch without modifying v0.2 execution.
2. Add strict reference-frequency authorities and event evidence.
3. Implement the exact detuning phase formula and baseband admission.
4. Replace the v0.3 scalar carrier map with frame-reference metadata.
5. Add byte oracles, X/X12 coexistence, scan-binding, and tamper tests.
6. Admit only verified v0.3 plans at the Stage 4.1 boundary.

