# Stage 4 Control-Signal Scope Decision

- Date: 2026-07-11
- Owner: design AI / project manager
- Status: proposed, pending independent design review
- Stage 3.1 readiness approval: `5BFCC41E684E8DDDD2794AF83673E12395F3F69076753C24B63B07AE8BEA851D`

## Decision

Stage 4 implements a deterministic control-signal compiler for the fixed `2q1c2r` virtual device:

```text
logical pulse schedule
  -> ideal effective baseband targets
  -> inverse static channel calibration
  -> sampled and quantized AWG lanes
  -> forward latency / FIR / channel-mixing model
  -> effective device signals
```

The stage represents XY, Z, and readout control paths. It does not perform quantum-state evolution,
gate calibration, readout discrimination, or fidelity estimation.

## Channel ownership

The Stage 1 device file contains an early channel reservation but does not declare `q1_z` or `q2_z`.
Changing the accepted Stage 1 file solely to add control metadata would invalidate the raw-hash provenance
of Stage 2 and Stage 3.1 while changing no Hamiltonian input.

Stage 4 therefore owns an authoritative, hash-bound control-channel registry at:

```text
configs/control/2q1c2r_channels.yaml
```

The registry must preserve every Stage 1 channel name, kind, target, and port exactly and may add only:

```text
q1_z: kind=z, target=q1, port=q1_z
q2_z: kind=z, target=q2, port=q2_z
```

A separate Stage 4.0 compatibility gate validates this merge, binds the accepted Stage 1 and Stage 3.1
hashes, and independently approves the resulting seven-channel registry. Stage 4 must not claim an
executable q1/q2 flux path until this approval exists.

If implementation discovers that any accepted device component, capacitor, SQUID, prior, Hamiltonian
input, or existing channel must change, this compatibility path is invalid. Work must stop and be
reclassified as a full upstream rebaseline.

## Signal frames

AWG arrays contain sampled baseband signals only:

```text
XY and readout: real I/Q baseband lanes plus carrier metadata
Z: real baseband flux-control lanes
```

The compiler must not sample the 5-8 GHz microwave carrier. Stage 5 consumes rotating-frame complex XY
envelopes and effective flux waveforms. Carrier reconstruction is visualization metadata, not a Stage 4
or Stage 5 numerical input.

## Electronics fidelity

Stage 4 v0.1 includes:

```text
2 GS/s common sample clock
16-bit signed DAC model
finite voltage range and fail-closed clipping checks
integer-sample lane latency and deterministic timing compensation
causal finite impulse-response filters
static XY, Z, and readout mixing matrices
DAC quantization and independent forward reconstruction
```

The formal configuration uses stable, invertible nominal calibrations. These values are simulation model
inputs, not accepted laboratory calibrations. Stage 7 may later propose updates.

Stage 4 v0.1 excludes stochastic noise, nonlinear amplifiers, mixer compression, dynamic predistortion,
ADC acquisition, and hardware-specific instrument drivers.

## Logical pulse set

The first version supports:

```text
XY: gaussian and drag_gaussian
Z: square and flattop_cos with absolute target flux in Phi0
readout: square and flattop_cos complex envelope
```

It does not define calibrated `X`, `X/2`, or `CZ` gates. Logical pulses are physical control requests, not
claims about the resulting unitary.

## Stage boundary

Stage 4 is complete only when independent review validates the channel registry, control artifact,
executed notebook, report, and approval. Development output remains `stage5_ready=false`. Only the
hash-bound independent approval may produce `Stage5ReadinessReport.stage5_ready=true`.

Stage 5 may consume:

```text
common effective time grid
complex rotating-frame q1/q2 XY envelopes in GHz
q1/q2/c effective flux traces in Phi0
carrier frequency and phase metadata
```

Readout effective envelopes are preserved for Stage 8 and are not interpreted as measurements in Stage 5.
