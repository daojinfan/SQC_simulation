# Stage 4 Detailed Design: Control Signal Chain

## 1. Purpose and boundary

Stage 4 converts physical pulse requests into deterministic sampled electronics data and effective device
signals for the accepted `2q1c2r` model.

```text
LogicalSchedule
  -> IdealEffectiveTargets
  -> RequestedAWGVoltages
  -> QuantizedAWGWaveforms
  -> EffectiveSignalSet
  -> ControlCompilationResult
```

The stage answers:

```text
Which physical control channel is used?
When is the pulse active?
What baseband signal should the AWG generate?
What voltage is representable by the DAC?
What signal reaches each modeled device control coordinate after the electronics model?
Were timing, range, conflict, provenance, and numerical budgets satisfied?
```

It does not answer what quantum state results. Stage 5 owns time evolution. Stage 7 owns calibration
updates. Stage 8 owns readout dynamics and measurement data.

## 2. Reference principles

Adopted principles:

```text
AWGs generate sampled I/Q and Z baseband waveforms.
XY/readout carriers are represented by frequency and phase metadata rather than GHz-rate samples.
Z controls tune qubit/coupler flux and are sensitive to bandwidth, delay, distortion, and crosstalk.
DRAG adds a derivative quadrature to a smooth in-phase envelope.
Logical pulses, AWG samples, and effective device signals remain separate inspectable layers.
Timing alignment and same-resource overlap are explicit validation problems.
```

Not adopted in v0.1:

```text
optical-link nonlinearities and laser bias behavior
stochastic transmission noise
full gate/instruction language from the previous project
mutable global calibration tables
ADC/readout classification
automatic pulse optimization
```

## 3. Stage 4.0 channel registry

### 3.1 Registry file

Path:

```text
configs/control/2q1c2r_channels.yaml
```

Exact root schema:

```yaml
schema_version: "0.1"
artifact_type: control_channel_registry
artifact_version: "0.1"
device_name: demo_2q1c2r
base_device_config: configs/devices/2q1c2r.yaml
base_device_artifact: output/stage_01_device_model/device_artifacts.json
channels:
  q1_xy:
    kind: xy
    target: q1
    port: q1_xy
    awg_lanes: [q1_xy_i, q1_xy_q]
  q2_xy:
    kind: xy
    target: q2
    port: q2_xy
    awg_lanes: [q2_xy_i, q2_xy_q]
  q1_z:
    kind: z
    target: q1
    port: q1_z
    awg_lanes: [q1_z]
  q2_z:
    kind: z
    target: q2
    port: q2_z
    awg_lanes: [q2_z]
  c_z:
    kind: z
    target: c
    port: c_z
    awg_lanes: [c_z]
  r1_ro:
    kind: readout
    target: r1
    port: r1_ro
    awg_lanes: [r1_ro_i, r1_ro_q]
  r2_ro:
    kind: readout
    target: r2
    port: r2_ro
    awg_lanes: [r2_ro_i, r2_ro_q]
```

No extra root or channel fields are accepted. Channel order in runtime objects is the fixed order shown
above, independent of YAML insertion order.

### 3.2 Compatibility semantics

The Stage 1 file remains the accepted device-physics input. The Stage 4 registry is an approved control
extension, not a rewritten device artifact. The merged registry records the origin of every channel:

```text
q1_xy, q2_xy, c_z, r1_ro, r2_ro -> stage1_base
q1_z, q2_z                         -> stage4_extension
```

The Stage 4.0 approval must validate before any schedule is compiled. A caller cannot pass an in-memory
registry or suppress this gate for formal output.

## 4. Control-chain configuration

### 4.1 Paths and profiles

```text
formal: configs/control/2q1c2r_control.yaml
smoke:  configs/control/2q1c2r_control_smoke.yaml
```

Both use `schema_version="0.1"` and `experiment_type="control_signal_chain"`. Formal and smoke profiles
may differ only in allowed runtime/sample limits and schedule path. Electronics values and provenance
inputs must be identical.

### 4.2 Exact root schema

```yaml
schema_version: "0.1"
experiment_type: control_signal_chain
profile: formal
inputs:
  stage4_design_freeze_manifest: docs/decisions/<approved-freeze>.json
  channel_registry: configs/control/2q1c2r_channels.yaml
  channel_registry_approval: output/stage_04_0_control_channel_rebaseline/control_channel_approval.json
  stage3_1_artifact: output/stage_03_1_q1_q2_coupling/q1_q2_coupling_artifacts.json
  stage3_1_approval: output/stage_03_1_q1_q2_coupling/acceptance_approval.json
clock:
  sample_rate_Hz: 2000000000
  dt_ns: 0.5
dac:
  bits: 16
  full_scale_min_V: -0.33
  full_scale_max_exclusive_V: 0.33
  rounding: half_even
lane_order:
  - q1_xy_i
  - q1_xy_q
  - q2_xy_i
  - q2_xy_q
  - q1_z
  - q2_z
  - c_z
  - r1_ro_i
  - r1_ro_q
  - r2_ro_i
  - r2_ro_q
lanes:
  <each exact lane>:
    latency_samples: <nonnegative integer>
    fir: [<finite coefficients>]
static_mixing:
  xy:
    input_lanes: [q1_xy_i, q1_xy_q, q2_xy_i, q2_xy_q]
    output_coordinates: [q1_drive_i_GHz, q1_drive_q_GHz, q2_drive_i_GHz, q2_drive_q_GHz]
    matrix: <4x4 GHz_per_V>
  z:
    input_lanes: [q1_z, q2_z, c_z]
    output_coordinates: [q1_delta_flux_phi0, q2_delta_flux_phi0, c_delta_flux_phi0]
    matrix: <3x3 Phi0_per_V>
  readout:
    input_lanes: [r1_ro_i, r1_ro_q, r2_ro_i, r2_ro_q]
    output_coordinates: [r1_device_i_V, r1_device_q_V, r2_device_i_V, r2_device_q_V]
    matrix: <4x4 device_V_per_awg_V>
idle_flux_phi0:
  q1: 0.10
  q2: 0.00
  c: 0.27
acceptance:
  max_condition_number: 100.0
  max_xy_area_relative_error: 0.005
  max_readout_area_relative_error: 0.005
  max_z_flat_top_error_phi0: 0.00002
  max_phase_proxy_rad: 0.10
  phase_proxy_window_ns: 32.0
  max_formal_samples_per_scenario: 10000
  analysis_runtime_budget_seconds: 10.0
  total_runtime_budget_seconds: 60.0
```

Unknown, missing, or extra fields fail. `profile` is exactly `formal` or `smoke`. Booleans are never valid
integers/floats. All numeric values must be finite.

### 4.3 Clock contract

The following equality is checked with Decimal arithmetic:

```text
dt_ns = 1e9 / sample_rate_Hz = 0.5
```

`sample_rate_Hz` must be integer `2000000000`; `dt_ns` must parse from its YAML scalar to Decimal `0.5`.
The compiler never derives one with binary floating-point and then rounds the other.

### 4.4 DAC contract

For `bits=16`:

```text
code_min = -2^(bits-1) = -32768
code_max =  2^(bits-1)-1 = 32767
lsb_V = (full_scale_max_exclusive_V - full_scale_min_V) / 2^bits
      = 0.66 / 65536
      = 0.00001007080078125 V
reconstructed_V = code * lsb_V
```

The full-scale limits must be symmetric and code zero must map to exactly 0 V. A requested voltage is
valid only if its half-even rounded code lies in `[code_min, code_max]`. Formal compilation does not clip;
an out-of-range code is an error. The artifact may record the first offending lane/sample/requested value
in a diagnostic report, but it must not publish an acceptance candidate.

The numerical solve and waveform evaluation first produce one finite `numpy.float64` requested voltage.
Quantization converts it without an intermediate text representation:

```text
requested_binary64 = float(np.float64(requested_V))
requested_decimal = Decimal.from_float(requested_binary64)
```

Within `decimal.localcontext(prec=80, rounding=ROUND_HALF_EVEN)`, the implementation computes
`requested_decimal / lsb_decimal` and calls `to_integral_value(rounding=ROUND_HALF_EVEN)`. `lsb_decimal`
is constructed from the exact YAML decimal bounds and integer `2^bits`. Python `round`, `repr`, `.17g`,
or any other float-to-text conversion is forbidden. This fixes tie and near-tie behavior byte-for-byte.
For every accepted sample:

```text
abs(reconstructed_V - requested_V) <= 0.5 * lsb_V + 1e-15 V
```

### 4.5 Lane and FIR contract

`lanes` keys must exactly equal `lane_order`; no other lane exists. Each latency is an integer in `[0,64]`.
Each FIR is a list of length 1-64 with finite coefficients and:

```text
abs(sum(fir) - 1.0) <= 1e-12
sum(abs(fir)) <= 4.0
fir[0] != 0
```

Formal acceptance permits a causal forward FIR but no dynamic inverse/predistortion. A known FIR impulse
response is tested independently. The formal reference configuration must use nonnegative coefficients so
its flat-top settling interpretation is unambiguous.

### 4.6 Static matrix contract

Let `A_xy`, `A_z`, and `A_ro` be the matrices in the fixed coordinate orders. Each must have exact shape,
finite entries, nonzero determinant, and 2-norm condition number `<=100`.

At every effective-grid sample, the compiler constructs desired coordinate vectors and solves:

```text
requested_lane_vector = solve(A, desired_effective_vector)
```

No explicit matrix inverse is stored or multiplied. The forward reference uses:

```text
effective_vector = A @ delivered_lane_vector
```

The XY matrix captures per-channel IQ imbalance and q1/q2 cross-drive in one real 4x4 map. The Z matrix
captures static q1/q2/c flux crosstalk. The readout matrix captures baseband IQ mixing only. Matrix values
are nominal model inputs and must be displayed in the notebook.

`idle_flux_phi0` must equal the q1/q2/c SQUID biases in the accepted Stage 1 artifact exactly after Decimal
normalization. Z matrices map voltage to delta flux; effective absolute flux is `idle + delta`.

### 4.7 Frozen formal electronics values

The formal and smoke configs use the same exact values. Development may not substitute cleaner identity
matrices or filters.

```text
latency_samples:
  q1_xy_i=24, q1_xy_q=24
  q2_xy_i=26, q2_xy_q=26
  q1_z=10, q2_z=12, c_z=8
  r1_ro_i=20, r1_ro_q=20
  r2_ro_i=22, r2_ro_q=22

FIR:
  q1/q2 XY lanes: [0.8, 0.2]
  q1/q2/c Z lanes: [0.7, 0.2, 0.1]
  r1/r2 readout lanes: [0.85, 0.15]
```

Formal static matrices in the coordinate orders declared by the config are:

```text
A_xy (GHz/V) =
[[ 0.1000,  0.0020,  0.0010,  0.0000],
 [-0.0010,  0.0980,  0.0000,  0.0010],
 [ 0.0005,  0.0000,  0.1020, -0.0015],
 [ 0.0000,  0.0007,  0.0010,  0.0990]]

A_z (Phi0/V) =
[[0.500, 0.005, 0.010],
 [0.004, 0.500, 0.012],
 [0.008, 0.006, 0.500]]

A_ro (device V / AWG V) =
[[ 0.00100,  0.00001,  0.00000,  0.00000],
 [-0.00001,  0.00100,  0.00000,  0.00000],
 [ 0.00000,  0.00000,  0.00102,  0.00001],
 [ 0.00000,  0.00000, -0.00001,  0.00102]]
```

The loader recomputes and records the 2-norm condition number of each matrix. Tests independently verify
the values and the `<=100` gate. These nominal off-diagonal terms make crosstalk/IQ handling observable
without claiming they describe a measured device.

Normative design calculations, independently recomputed before freeze, are:

```text
condition numbers (2-norm):
  A_xy = 1.0495417347511131
  A_z  = 1.0528243806381707
  A_ro = 1.0199980198039404

maximum absolute requested AWG voltage at waveform peak/plateau before FIR:
  simultaneous XY reference = 0.14998480607431475 V
  q2 resonance target       = 0.19958271998688384 V
  c target 0.200 Phi0       = 0.14008437116138114 V
  c target 0.385 Phi0       = 0.23013860976512618 V
  simultaneous readout      = 0.10547326647757110 V

DAC:
  LSB = 0.00001007080078125 V
  worst Z coordinate half-LSB bound before FIR = 2.5982666015625002e-6 Phi0
```

All reference requests have margin to the positive/negative DAC rails. The implementation tests these
vectors but recomputes results from config rather than hard-coding pass flags.

## 5. Logical schedule

### 5.1 File and root schema

```text
formal: configs/control/2q1c2r_control_demo.yaml
smoke:  configs/control/2q1c2r_control_demo_smoke.yaml
```

```yaml
schema_version: "0.1"
artifact_type: logical_pulse_schedule
artifact_version: "0.1"
schedule_id: stage4_reference
scenarios:
  - scenario_id: xy_drag
    duration_ns: 80.0
    pulses: [<pulse mappings>]
```

The root fields and each scenario's three fields are exact. `schedule_id`, every `scenario_id`, `pulse_id`,
channel ID, port ID, and lane ID must be ASCII and match `[a-z][a-z0-9_]{0,63}`. Scenario IDs are unique.
Pulse IDs are globally unique within the schedule. Pulses in one
scenario share a time grid and electronics solve; different scenarios are independent and never combined.
`duration_ns` must be an exact positive multiple of `dt_ns`. Every pulse requires `start_ns>=0`,
`duration_ns>0`, and `start_ns+duration_ns<=scenario.duration_ns`; all three values are exact multiples of
`dt_ns`. Scenario duration controls
the desired effective-grid sample count before FIR tail/preamble expansion.

The compiler rejects rather than rounds. Intervals are half-open `[start_ns, start_ns+duration_ns)`. A
scenario contains at most one pulse per logical channel. This makes each required area comparison and its
FIR tail attributable to exactly one pulse; sequential same-channel pulses are outside v0.1.
Each pulse `kind` must exactly match the referenced registry channel kind.

### 5.2 XY pulse schema

Exact fields:

```yaml
pulse_id: q1_drag
kind: xy
channel: q1_xy
start_ns: 20.0
duration_ns: 32.0
shape: drag_gaussian
amplitude_GHz: 0.015
phase_rad: 0.0
sigma_ns: 8.0
drag_beta_ns: 0.0
carrier_frequency_GHz: 5.193479909897604
```

`shape` is `gaussian` or `drag_gaussian`. Gaussian permits `drag_beta_ns` only when it is exactly zero;
DRAG requires the field. `sigma_ns>0`, `duration_ns>=4*sigma_ns`, `amplitude_GHz>0`, and
`carrier_frequency_GHz>0`. Phase and
carrier frequency are metadata inputs; phase is normalized to `[0,2*pi)` only in derived output while the
raw configured value is retained.

The carrier frequency must match the accepted Stage 3.1 idle frequency for the target within `0.50 MHz`.
This does not establish calibration; it prevents a stale or obviously inconsistent reference schedule.

### 5.3 Z pulse schema

Exact fields:

```yaml
pulse_id: q2_to_resonance
kind: z
channel: q2_z
start_ns: 80.0
duration_ns: 80.0
shape: flattop_cos
target_flux_phi0: 0.0997552
rise_ns: 8.0
```

`shape` is `square` or `flattop_cos`. Square requires `rise_ns=0`; flattop cosine requires
`rise_ns>=dt_ns` and `2*rise_ns<duration_ns`. The target is absolute flux. Outside a Z pulse the desired
coordinate equals idle flux. Pulses on distinct Z channels may overlap and are solved together through
`A_z`.

Formal q1, q2, and c target values must lie in `[-0.5,0.5] Phi0`. This is a model-domain guard, not a hardware
calibration range.

### 5.4 Readout pulse schema

Exact fields:

```yaml
pulse_id: r1_readout
kind: readout
channel: r1_ro
start_ns: 180.0
duration_ns: 800.0
shape: flattop_cos
amplitude_device_V: 0.0001
phase_rad: 0.0
rise_ns: 8.0
carrier_frequency_GHz: 6.4
```

Readout carrier frequency must match the corresponding Stage 1 resonator frequency within `50 MHz`.
Stage 4 produces only the outgoing effective envelope. No resonator response, ADC sample, or IQ cluster is
generated.

Readout `shape` is exactly `square` or `flattop_cos`. Square requires `rise_ns=0`; flattop cosine requires
`rise_ns>=dt_ns` and `2*rise_ns<duration_ns`. `amplitude_device_V` must be finite and in
`(0,0.001]`; `carrier_frequency_GHz` must be finite and positive; `phase_rad` must be finite. These are
schema/model-domain guards, not accepted hardware calibrations.

### 5.5 Conflicts

Two pulses conflict when their half-open intervals overlap and any condition holds:

```text
same logical channel
same physical port
any shared AWG lane
```

Identical endpoints do not overlap. Cross-channel simultaneous XY/Z/readout pulses are legal when their
resources are distinct. Because static mixing is solved jointly, q1/q2 XY simultaneity and q1/q2/c Z
simultaneity are supported.

The compiler reports every conflict in deterministic order `(start_ns, channel, pulse_id)` and fails before
sampling. It never silently sums same-channel pulses.

## 6. Time grid and pulse shapes

### 6.1 Effective-grid coordinates

For a scenario duration `T_ns`:

```text
N = T_ns / dt_ns
t_center_ns[k] = (k + 0.5) * dt_ns, k=0..N-1
```

`N` must be exact and `<=max_formal_samples_per_scenario`. Pulses are evaluated at sample centers.

### 6.2 Corrected Gaussian

For pulse-local center times `u_k=(k+0.5)dt`, duration `D`, center `D/2`, sigma `s`:

```text
raw(u) = exp(-0.5 * ((u-D/2)/s)^2)
edge = raw(dt/2) = raw(D-dt/2)
g(u) = (raw(u)-edge)/(1-edge)
```

The first and last samples are exactly zero within floating tolerance, the sequence is symmetric, and its
peak is below or equal to one because an even sample count need not include the continuous center.

For Gaussian XY:

```text
base_complex_GHz = amplitude_GHz * g(u) * exp(i*phase)
```

### 6.3 DRAG

Use the analytic derivative of the corrected Gaussian:

```text
dg_dt_ns = raw(u) * (-(u-D/2)/s^2) / (1-edge)
base_complex_GHz = amplitude_GHz * (g(u) + i*drag_beta_ns*dg_dt_ns) * exp(i*phase)
```

The derivative quadrature is antisymmetric before phase rotation and has zero discrete area within
`1e-12 GHz*ns`. Sign follows the formula above and is covered by a fixed reference vector.

### 6.4 Flat-top cosine

For normalized envelope `e(u)` with rise `R` and duration `D`:

```text
u < R:       e(u)=0.5*(1-cos(pi*u/R))
R <= u <= D-R: e(u)=1
u > D-R:    e(u)=0.5*(1-cos(pi*(D-u)/R))
```

Evaluate only within `0<u<D`; outside the pulse `e=0`. For Z:

```text
desired_flux(u) = idle_flux + e(u)*(target_flux-idle_flux)
```

For readout:

```text
desired_complex_V(u) = amplitude_device_V * e(u) * exp(i*phase)
```

Square uses `e=1` at every pulse sample.

## 7. Compilation pipeline

### 7.1 Pure numerical order

```text
1. Validate all provenance and approvals.
2. Load and validate registry, control config, and schedule.
3. Detect schedule conflicts.
4. Build the exact effective time grid.
5. Evaluate desired XY, absolute-Z, and readout targets.
6. Convert absolute Z to delta-from-idle coordinates.
7. Solve each static mixing system sample by sample.
8. Allocate AWG lanes with latency preamble.
9. Quantize requested voltage to signed DAC codes and reconstruct delivered voltage.
10. Apply each causal FIR and modeled lane latency.
11. Apply forward static matrices.
12. Add Z idle flux to obtain absolute effective flux.
13. Independently reconstruct reference outputs and compute metrics/checks.
14. Assemble immutable ControlCompilationResult.
15. Preflight finite canonical bytes and publish the transaction.
```

No artifact, notebook, output directory, or temporary file is created before steps 1-14 pass and artifact
canonical bytes exist in memory.

### 7.2 Latency coordinates

Let `N` be the desired-grid sample count, `L_j` the lane latency, `L_max=max_j(L_j)`, `M_j` the FIR length,
and `M_max=max_j(M_j)`. Let `x_j[k]`, `k=0..N-1`, be the static-solve voltage. Every lane has one common
requested/reconstructed AWG length:

```text
N_awg = N + L_max
n = 0..N_awg-1
t_awg_center_ns[n] = (n - L_max + 0.5) * dt_ns
```

The AWG array is initialized to zero and places `x_j[k]` only at `n=k+L_max-L_j`. Thus every logical sample
is present exactly once and all unused preamble/tail slots are zero. Causal full convolution has native
length `N_awg+M_j-1`; applying the modeled latency by prefixing `L_j` zeros gives native delivered length
`N_awg+M_j-1+L_j`.

All lanes are then right-zero-padded, never trimmed, to the common published physical length:

```text
P = N_awg + (M_max - 1) + L_max
p = 0..P-1
t_effective_center_ns[p] = (p - L_max + 0.5) * dt_ns
```

Because `M_j<=M_max` and `L_j<=L_max`, every native delivered length is at most `P`; no nonzero native
sample is discarded. All eleven delivered lane arrays therefore have exactly length `P`, so each static
forward matrix is applied row-wise for every common `p` without implicit broadcasting or truncation.

Desired XY/readout coordinates and Z deltas are embedded into the common physical timeline at
`p=L_max+k`, `k=0..N-1`; values outside that range are zero. Absolute desired/effective Z adds the configured
idle flux at every `p`. The published interval includes preamble and every FIR/latency tail. Negative
effective times may contain only preamble/filter response and must be displayed; logical pulses remain at
their requested nonnegative times.

For the formal bounds, `L_max<=64`, `M_max<=64`, so `N_awg<=N+64` and `P<=N+191`. Artifact validators
recompute `N_awg`, `P`, every time array, and every lane length from the bound config. Requested, code, and
reconstructed arrays all have length `N_awg`; delivered and effective arrays all have length `P`.

`latency_alignment_error_samples` is recomputed from lane placement and must be exactly zero.

### 7.3 FIR order

For reconstructed DAC lane `v_j[n]` and FIR `h_j[m]`:

```text
filtered_j[n] = sum_m h_j[m] * v_j[n-m]
```

Out-of-range indices are zero. Full convolution and latency are retained through each native final sample,
then right-zero-padded to `P` as defined above. The independent reference implementation may use direct
loops while production may use `numpy.convolve`; their common-length arrays must agree within `1e-15 V`
absolute.

## 8. Metrics and fail-closed gates

### 8.1 Quantization isolation

Compute an analog forward result using requested, unquantized lane voltages and a quantized forward result
using reconstructed DAC voltage through identical FIR/latency/matrix operations.

For each static output row `a_r` and involved lane FIRs:

```text
bound_r = 0.5*lsb_V * sum_j(abs(a_rj)*sum_m(abs(h_jm))) + 1e-12*output_unit
```

The maximum analog-versus-quantized difference for that coordinate must not exceed `bound_r`. Bounds and
actual errors are stored per coordinate.

### 8.2 Independent forward reference

Production effective arrays must match independently recomputed direct-loop arrays:

```text
XY/readout absolute error <= 1e-12 in output units
Z absolute error <= 1e-12 Phi0
DAC code arrays equal exactly
```

### 8.3 Area budgets

Formal has exactly four required comparison rows in this order:

```text
xy_drag:q1_drag:xy
xy_drag:q2_drag:xy
readout_envelopes:r1_readout:readout
readout_envelopes:r2_readout:readout
```

Smoke has exactly the first two XY rows and no readout row because its frozen schedule omits
`readout_envelopes`. This is an explicit profile rule, not an empty-set pass.

For each row, let `phi` be the configured pulse phase and let `z_des[p]` and `z_eff[p]` be the desired and
effective complex coordinate on the common `P`-sample physical timeline. XY uses `drive_i+i*drive_q` in
GHz; readout uses `device_i+i*device_q` in device volts. Rotate both into the pulse frame and integrate the
signed in-phase coordinate over the entire common timeline, including all preamble and tail samples:

```text
u_des[p] = z_des[p] * exp(-i*phi)
u_eff[p] = z_eff[p] * exp(-i*phi)
A_des = dt_ns * sum_p(real(u_des[p]))
A_eff = dt_ns * sum_p(real(u_eff[p]))
denominator = abs(A_des)
```

Readout does not use `sum(abs(z))` or `abs(sum(z))`; it uses the same pulse-frame signed in-phase formula.
The at-most-one-pulse-per-channel schedule rule makes the complete FIR tail attributable to one required
pulse.

Each row has the exact fields:

```text
scenario_id
pulse_id
kind
phase_rad
desired_in_phase_area
effective_in_phase_area
denominator
relative_error
threshold=0.005
passed
unavailable_reason
```

If either array/area is missing or non-finite, or `denominator<=1e-15` in its area units,
`relative_error=null`, `passed=false`, and `unavailable_reason` is a deterministic non-empty string. Otherwise
`relative_error=abs(A_eff-A_des)/denominator`, `unavailable_reason=null`, and the row passes exactly when the
finite relative error is `<=0.005`.

`xy_area_error_within_budget=true` if and only if the exact two XY rows exist once, in order, have the
required IDs/kind, and both pass. For formal, `readout_area_error_within_budget` applies the same all-required
rule to the exact two readout rows. For smoke, `readout_area_error_within_budget=false`, the readout check is
absent from the exact smoke check set, and `acceptance_eligible=false`; absence never produces a true
aggregate. Missing, extra, duplicate, reordered, null, or failed required rows make the applicable aggregate
false; an empty set can never pass. DRAG derivative sign/antisymmetry and pre-rotation absolute discrete
area `<=1e-12 GHz*ns` remain separate waveform-construction checks and never substitute for the I-area gate.

### 8.4 Z target budget

For a flat-top pulse, the valid plateau excludes:

```text
configured rise and fall regions
max FIR length - 1 samples after the rise boundary
max FIR length - 1 samples before the fall boundary
```

The remaining plateau must be nonempty. For the target component:

```text
max(abs(effective_flux_phi0-target_flux_phi0)) <= 2.0e-5 Phi0
```

All spectator flux coordinates and their modeled crosstalk are displayed. No unstated spectator tolerance
is inferred; the effective signal itself is the downstream input.

### 8.5 Stage 3 phase proxy

Read the accepted Stage 3.1 artifact exact JSON path
`$.idle_convergence.max_frequency_drift_MHz`. The value must be a finite JSON number, must not be a boolean,
and must be `>=0`. Name that validated value `U_frequency_MHz` and compute:

```text
phase_proxy_rad = 2*pi*(U_frequency_MHz*1e6)*(32e-9)
```

It must be finite and `<=0.10 rad`. The exact source artifact path/hash, source field, window, and result are
recorded. This replaces the provisional Stage 3 phase estimate with the actual accepted uncertainty while
retaining the same conservative 32 ns control window.

For the accepted Stage 3.1 value `U_frequency=0.09736560961925989 MHz`, the normative result is:

```text
phase_proxy_rad = 0.01957651736909815
```

### 8.6 Status priority

The computational status uses the first matching failure:

```text
provenance_invalid
channel_registry_invalid
config_invalid
schedule_conflict
sample_grid_invalid
electronics_invalid
dac_out_of_range
forward_reconstruction_failed
control_error_budget_failed
runtime_budget_exceeded
ready_for_stage5_review
```

Every failure has `computational_ready=false` and ordered blocking reasons. Development never sets
`stage5_ready=true`.

## 9. Reference scenarios and required values

The formal runner compiles these isolated scenarios in fixed order:

```text
1. xy_drag
2. q2_resonance_flux
3. coupler_0_200
4. coupler_0_270
5. coupler_0_385
6. readout_envelopes
```

Required Stage 3.1-linked values:

```text
q1 idle frequency: 5.193479909897604 GHz
q2 idle frequency: 5.331633051009526 GHz
q2 resonance target at c=0.270: 0.0997552 Phi0
coupler targets: 0.200, 0.270, 0.385 Phi0
accepted |g_eff| values are displayed as provenance context only
```

The compiler must not derive pulse duration, drive amplitude, or a gate operation from `|g_eff|`.

Formal schedule identity and scenario order are hash-bound. A conflict fixture is a test input and is never
included in the accepted formal schedule.

### 9.1 Frozen formal schedule values

The formal schedule contains exactly the following logical requests:

```text
xy_drag, duration 80 ns:
  q1_drag on q1_xy start=20 ns, duration=32 ns, drag_gaussian, amplitude=0.015 GHz,
    phase=0, sigma=8 ns, drag_beta=-0.5 ns, carrier=5.193479909897604 GHz
  q2_drag on q2_xy start=20 ns, duration=32 ns, drag_gaussian, amplitude=0.014 GHz,
    phase=1.5707963267948966 rad, sigma=8 ns, drag_beta=-0.4 ns,
    carrier=5.331633051009526 GHz

q2_resonance_flux, duration 120 ns:
  q2_to_resonance on q2_z start=20 ns, duration=80 ns, flattop_cos, rise=8 ns,
    target=0.0997552 Phi0; c remains at idle 0.270 Phi0

coupler_0_200, duration 120 ns:
  c_to_0_200 on c_z start=20 ns, duration=80 ns, flattop_cos, rise=8 ns, target=0.200 Phi0

coupler_0_270, duration 120 ns:
  c_to_0_270 on c_z start=20 ns, duration=80 ns, flattop_cos, rise=8 ns, target=0.270 Phi0

coupler_0_385, duration 120 ns:
  c_to_0_385 on c_z start=20 ns, duration=80 ns, flattop_cos, rise=8 ns, target=0.385 Phi0

readout_envelopes, duration 860 ns:
  r1_readout on r1_ro start=20 ns, duration=800 ns, flattop_cos, rise=16 ns,
    amplitude=0.00010 device V, phase=0, carrier=6.400 GHz
  r2_readout on r2_ro start=20 ns, duration=800 ns, flattop_cos, rise=16 ns,
    amplitude=0.00011 device V, phase=0.2 rad, carrier=6.450 GHz
```

The smoke schedule contains only `xy_drag` and `q2_resonance_flux` with the same pulse values and scenario
durations. It is `acceptance_eligible=false` because it omits four formal scenarios.

## 10. Provenance

### 10.0 Design-freeze manifest

After independent design approval, the reviewer creates:

```text
docs/decisions/<date>-stage4-design-freeze-review.md
docs/decisions/<date>-stage4-design-freeze.json
```

The canonical manifest exact fields are:

```text
schema_version=0.1
artifact_type=stage_04_control_signal_design_freeze
artifact_version=0.1
decision=approved
reviewer_role=independent_design_review_ai
blocking_findings=[]
review_record_path
review_record_sha256
document_sha256:
  docs/decisions/2026-07-11-stage4-control-signal-scope.md
  docs/stages/04_0_control_channel_rebaseline_plan.md
  docs/stages/04_control_signal_plan.md
  docs/designs/04_control_signal_design.md
```

The mapping has exactly four document keys. Missing, extra, stale, malformed, noncanonical, or mismatched
content blocks Stage 4.0 and Stage 4 implementation.

### 10.1 Required bindings

The formal artifact binds raw uppercase SHA-256 for:

```text
Stage 4 design-freeze manifest
Stage 4.0 channel registry
Stage 4.0 channel approval
control-chain config
logical schedule
accepted Stage 3.1 artifact
accepted Stage 3.1 approval
Stage 4 source tree
Python/NumPy/SciPy/PyYAML/nbformat/nbclient environment fingerprint
```

The source-tree digest uses the existing repository-relative POSIX path plus raw-byte canonical hashing
convention. The exact Stage 4 source set is `src/sqvm/control/**/*.py`, the Stage 4 CLI dispatch in
`src/sqvm/__main__.py`, and the two Stage 4 runner scripts.

### 10.2 Validation order

Before analysis:

```text
validate Stage 3.1 final approval and obtain stage4_ready=true
validate Stage 4 design-freeze manifest
validate Stage 4.0 channel approval and obtain control_channel_ready=true
recompute every config/schedule/source/environment hash
then load numerical control inputs
```

A missing or stale approval prevents analysis and leaves the requested output path absent.

## 11. Aggregate models and interfaces

The public aggregate names and their exact persisted projections are:

```text
ControlCompilationResult
  profile
  acceptance_eligible
  status
  provenance
  registry
  control_config
  scenario_order
  scenarios
  global_metrics
  phase_proxy
  runtime
  computational_gate

ControlVerificationReport
  execution_succeeded
  profile
  computational_ready
  acceptance_eligible
  status
  publication_pending=true
  stage5_ready=false
  approval_status=pending
  artifact_path
  artifact_sha256
  notebook_path
  notebook_sha256
  computational_checks
  staging_checks
  blocking_reasons

ControlRunReceipt
  execution_succeeded
  profile
  acceptance_eligible
  acceptance_candidate_ready
  artifact_sha256
  notebook_sha256
  verification_report_sha256
  publication_checks
  analysis_elapsed_seconds
  total_elapsed_seconds_to_receipt_assembly
  total_runtime_within_budget
  blocking_reasons

Stage5ReadinessReport
  ok
  stage5_ready
  approval_decision
  acceptance_approval_valid
  bound_hashes
  checks
  blocking_reasons
```

These are exact fields, not minimum fields. `ControlCompilationResult` has no approval or publication
state. `ControlVerificationReport` has no run-receipt or final-existence state. Check rows everywhere have
the exact fields `{name, passed, message}` with a non-empty name/message and JSON-boolean `passed`.

Public signatures:

```text
load_control_channel_registry(path: str | Path) -> ControlChannelRegistry

load_control_chain_config(path: str | Path) -> ControlChainConfig

load_logical_schedule(path: str | Path, *, dt_ns: Decimal) -> LogicalSchedule

validate_logical_schedule(
    schedule: LogicalSchedule,
    registry: ControlChannelRegistry,
    config: ControlChainConfig,
) -> ScheduleValidationReport

compile_control_schedule(
    schedule: LogicalSchedule,
    config: ControlChainConfig,
    context: ControlBuildContext,
) -> ControlCompilationResult

write_control_signal_artifacts(
    result: ControlCompilationResult,
    output_dir: str | Path,
) -> ControlArtifactSet

verify_control_signal(
    config_path: str | Path,
    schedule_path: str | Path,
    output_dir: str | Path,
) -> ControlRunReceipt

validate_stage4_acceptance_approval(
    approval_path: str | Path,
    artifact_path: str | Path,
    notebook_path: str | Path,
    report_path: str | Path,
    run_receipt_path: str | Path,
    *,
    repository_root: str | Path | None = None,
) -> Stage5ReadinessReport
```

The writer accepts no partial set of arrays and does not reconstruct results from paths.

## 12. Artifact schema

`control_signal_artifacts.json` has exactly these root fields:

```text
schema_version=0.1
artifact_type=stage_04_control_signal
artifact_version=0.1
profile
acceptance_eligible
status
provenance
registry
control_config
scenario_order
scenarios[]
global_metrics
phase_proxy
runtime
computational_gate
```

`profile` is `formal` or `smoke`; `acceptance_eligible` is true exactly for formal and false exactly for
smoke. A computationally ready formal artifact has `status=ready_for_stage5_review`; a computationally ready
smoke artifact has `status=smoke_complete`. Computational failure returns exit 1 and leaves that profile's
requested output path absent; there is no persisted diagnostic-failure variant whose schema could be
mistaken for an acceptance candidate.

`provenance` has exactly two mappings:

```text
paths (exact keys):
  stage4_design_freeze_manifest
  channel_registry
  channel_registry_approval
  control_config
  logical_schedule
  stage3_1_artifact
  stage3_1_approval
sha256 (exact keys):
  the same seven keys with suffix _sha256
  stage4_source_tree_sha256
  environment_fingerprint_sha256
```

All paths are repository-relative POSIX text; all hashes are uppercase raw-byte SHA-256. `registry` has
exact keys `{channel_order, channels}`. `channel_order` is the exact seven-channel order in section 3;
`channels` has exactly those keys, and each value has exact fields
`{kind, target, port, awg_lanes, origin}`. `origin` is `stage1_base` or `stage4_extension` per section 3.

`control_config` has exact keys
`{clock, dac, lane_order, lanes, static_mixing, idle_flux_phi0, acceptance}`. Its nested key sets and values
are the normalized exact schemas in section 4: clock `{sample_rate_Hz, dt_ns}`; DAC
`{bits, full_scale_min_V, full_scale_max_exclusive_V, rounding, code_min, code_max, lsb_V}`; each lane
`{latency_samples, fir}`; each static-mixing entry
`{input_lanes, output_coordinates, matrix, condition_number_2}`; idle flux exactly q1/q2/c; acceptance
exactly the nine keys shown in section 4.2.

`scenario_order` is the exact ordered scenario-ID list. `scenarios` has one row per ID in that order and no
other row. Each scenario row has exact fields:

```text
scenario_id
duration_ns
desired_sample_count
awg_sample_count
effective_sample_count
logical_pulses
logical_targets
awg
effective
metrics
checks
```

`desired_sample_count=N`, `awg_sample_count=N_awg`, and `effective_sample_count=P` are positive integers and
must satisfy section 7.2. `logical_pulses` is the ordered list of exact kind-specific input pulse mappings
from section 5, with no derived/extra field. Its canonical order is `(start_ns, channel, pulse_id)`,
independent of YAML list order. `logical_targets` has exact keys
`{time_center_ns, xy_drive_GHz, absolute_flux_phi0, readout_device_V}`: desired time has length `P`; XY and
readout have exact target keys q1/q2 and r1/r2 respectively, each with exact `{i,q}` arrays; absolute flux
has exact q1/q2/c arrays. All target arrays have length `P`.

`awg` has exact keys `{time_center_ns, lane_order, lanes}`. Time has length `N_awg`; lane order is the exact
11-lane order; lane mapping has exactly those keys. Each lane row has exact fields
`{requested_V, codes, reconstructed_V, delivered_after_fir_latency_V}`. The first three arrays have length
`N_awg`, codes are JSON integers in DAC range, and delivered arrays have length `P`.

`effective` has exact keys
`{time_center_ns, xy_drive_GHz, absolute_flux_phi0, readout_device_V}` with the same coordinate mappings as
`logical_targets`; every array has length `P`. Complex values are encoded only as separate `i` and `q` real
arrays; JSON complex numbers or numeric strings are forbidden.

Each scenario-local `metrics` has exactly:

```text
quantization_rows
forward_reference_rows
z_target_rows
latency_alignment_error_samples
```

Each `quantization_rows` item has exact fields
`{coordinate, output_unit, bound, max_abs_error, passed}`. Each `forward_reference_rows` item has
`{coordinate, output_unit, threshold, max_abs_error, passed}`. Both lists use static output-coordinate order
and contain every coordinate exactly once. `z_target_rows` contains only Z pulses from that scenario and is
ordered by pulse ID; each has exact fields
`{scenario_id, pulse_id, channel, target_flux_phi0, plateau_start_index, plateau_stop_index,
max_abs_error_phi0, threshold_phi0, passed, unavailable_reason}`. Required nonempty plateau and null behavior
follow section 8.4. `latency_alignment_error_samples` is a JSON integer.

Root `global_metrics` is the only cross-scenario aggregation location and has exactly:

```text
area_rows
xy_area_error_within_budget
readout_area_error_within_budget
z_target_error_within_budget
quantization_error_within_bound
latency_alignment_exact
forward_reconstruction_matches_reference
```

For formal, `area_rows` is the exact four-row list in section 8.3; for smoke it is the exact two-row XY
list. `xy_area_error_within_budget` follows the two required XY rows. Formal
`readout_area_error_within_budget` follows the two required readout rows; smoke fixes it to false and has no
readout computational check. `z_target_error_within_budget` requires every profile-required Z row exactly
once and passing. Formal requires `q2_resonance_flux:q2_to_resonance`,
`coupler_0_200:c_to_0_200`, `coupler_0_270:c_to_0_270`, and `coupler_0_385:c_to_0_385`; smoke requires only
`q2_resonance_flux:q2_to_resonance`. `forward_reconstruction_matches_reference=true` if and only if every
scenario's recomputed `forward_reference_matches` check passes; this includes exact reference-code equality
and every coordinate row. The remaining booleans are all-scenario/all-coordinate reductions of the local
rows or alignment integers. Missing, extra, duplicate, reordered, null, or failed required evidence makes
the corresponding boolean false.

Each scenario `checks` list has exact ordered names
`sample_lengths_valid`, `dac_codes_in_range`, `forward_reference_matches`, and
`scenario_error_budgets_passed`. Their `passed` values are recomputed exactly:

```text
sample_lengths_valid =
  N, N_awg, and P equal the section 7.2 formulas
  and all logical-target/effective time and coordinate arrays have length P
  and AWG time/requested/code/reconstructed arrays have length N_awg
  and every delivered lane has length P
  and both time arrays equal their exact formulas sample by sample

dac_codes_in_range =
  every code in lane_order/sample-index order is a non-bool JSON integer
  and code_min <= code <= code_max

forward_reference_matches =
  every persisted DAC code equals the independently recomputed Decimal.from_float/half-even code
    from that lane/sample requested_V, traversed in lane_order then ascending sample index
  and every local forward_reference_rows item exists once in static coordinate order and passed=true

scenario_error_budgets_passed =
  every local quantization_rows item exists once in static coordinate order and passed=true
  and every local z_target_rows item in pulse-id order passed=true
  and latency_alignment_error_samples == 0
```

The scenario budget check never consumes root `area_rows`; XY/readout area is gated only by root
`global_metrics` and the profile computational checks. A scenario with no Z pulse may have an empty local Z
list, but its nonempty exact quantization rows and latency condition still prevent an empty-evidence pass.

Each passed row has exact `message="passed"`. A failed row uses the first applicable reason in check order.
Within a check, reason order is the formula order above; coordinates use static order, lanes use `lane_order`,
samples use ascending index, and Z rows use pulse-id order. Exact reason text is respectively:

```text
desired_sample_count_invalid
awg_sample_count_invalid
effective_sample_count_invalid
logical_target_length_or_time_mismatch:<first_json_path>
awg_length_or_time_mismatch:<first_json_path>
effective_length_or_time_mismatch:<first_json_path>
dac_code_invalid:<lane_id>:<sample_index>
dac_code_reference_mismatch:<lane_id>:<sample_index>
forward_row_failed:<coordinate>
quantization_row_failed:<coordinate>
z_target_row_failed:<pulse_id>
latency_alignment_nonzero
```

`<first_json_path>` uses the exact artifact key order defined above, then fixed coordinate/lane order, then
ascending array index. A validator rejects a check whose boolean or message differs from recomputation.

Artifact-level `phase_proxy` has exact fields
`{source_json_path, source_value_MHz, window_ns, phase_proxy_rad, threshold_rad, passed}` and binds the exact
path in section 8.5. `runtime` has exact fields
`{analysis_elapsed_seconds, analysis_runtime_budget_seconds, analysis_runtime_within_budget}`.

`computational_gate` has exactly
`{computational_ready, status, checks, blocking_reasons}`. Its checks have the exact order in the Stage 4
plan for the selected profile. `computational_ready=true` if and only if every profile-required check passes
and `blocking_reasons=[]`. When true, status is `ready_for_stage5_review` for formal and `smoke_complete` for
smoke. Computational readiness is profile completion, not acceptance eligibility; only the receipt combines
it with `acceptance_eligible`, so smoke may complete successfully but can never become an acceptance
candidate. Missing, extra, duplicate, null, non-finite, reordered, or inconsistent fields fail closed.

`verification_report.json` has exactly:

```text
schema_version=0.1
artifact_type=stage_04_control_signal_verification_report
artifact_version=0.1
execution_succeeded=true
profile
computational_ready
acceptance_eligible
status
publication_pending=true
stage5_ready=false
approval_status=pending
artifact_path
artifact_sha256
notebook_path
notebook_sha256
computational_checks
staging_checks
blocking_reasons
```

Paths are expected final repository-relative POSIX paths. `artifact_sha256` binds the raw canonical artifact;
`notebook_sha256` binds the actually executed notebook bytes. `computational_checks` exactly equal the bound
artifact gate checks, including order and messages. `profile`, `computational_ready`, `acceptance_eligible`, `status`,
and `blocking_reasons` exactly equal the artifact values. A successfully published formal or smoke report
therefore has true computational readiness and empty blocking reasons, while its eligibility and status
remain profile-specific. `staging_checks` have the exact five names in section 13. The report cannot add,
replace, or reinterpret a computational check. Any mismatch fails validation.

Canonical JSON is sorted-key, indent-2, UTF-8, LF, no BOM/CR, final LF, `allow_nan=false`. A recursive
finite preflight reports the first deterministic JSON path. Canonical bytes are built before directory
creation.

## 13. Publish transaction and notebook

The writer uses a sibling staging directory and publishes by one same-filesystem directory rename. Target
must not exist. Staging is deleted on failure. No merge, overwrite, nested target, or partial promotion is
allowed.

The report is assembled in staging with expected final target paths before rename. Computational checks
are copied from the result without replacement. The report's staging/post-assembly checks are separate:

```text
staged_artifact_exists_and_hash_matches
artifact_canonical_and_finite
staged_notebook_exists_and_hash_matches
notebook_executed_without_errors
reported_paths_equal_expected_final_paths
```

These checks make no pre-rename claim that final paths already exist. After the atomic rename, the runner
performs non-mutating publication checks:

```text
final_directory_exact_three_before_receipt
final_artifact_exists_and_hash_matches
final_notebook_exists_and_hash_matches
final_report_exists_and_hash_matches
no_staging_or_partial_files
```

They are assembled into canonical `run_receipt.json` after the rename. The receipt binds all three raw
hashes, records `analysis_elapsed_seconds` and `total_elapsed_seconds_to_receipt_assembly`, and contains the
publication checks. It does not claim to hash itself. The receipt is written atomically as a fourth file.
Independent acceptance repeats the checks, verifies the receipt, and binds all four raw hashes. A failed
post-rename check or receipt write removes the newly created formal directory and makes the command fail;
it is never converted into a pass inside the report.

The development directory is exact-four before approval:

```text
control_signal_artifacts.json
verification.ipynb
verification_report.json
run_receipt.json
```

`run_receipt.json` has this exact schema/artifact version `0.1` field set:

```text
schema_version
artifact_type=stage_04_control_signal_run_receipt
artifact_version
execution_succeeded
profile
acceptance_eligible
acceptance_candidate_ready
paths:
  artifact
  notebook
  verification_report
sha256:
  artifact_sha256
  notebook_sha256
  verification_report_sha256
analysis_elapsed_seconds
total_elapsed_seconds_to_receipt_assembly
total_runtime_budget_seconds
total_runtime_within_budget
publication_checks
blocking_reasons
```

The three paths are repository-relative POSIX paths and must resolve to the explicit acceptance inputs. The
ordered publication checks are exactly the five checks listed above. `profile` and `acceptance_eligible`
must equal the bound artifact/report. `acceptance_candidate_ready=true` requires profile formal,
`acceptance_eligible=true`, every publication check, computational readiness read from the bound
artifact/report, and both runtime budgets. It is always false for smoke. Missing, extra, stale,
noncanonical, or non-finite receipt content fails closed.

After receipt creation either profile directory contains exactly the four development files. Only formal is
eligible for independent acceptance, which checks its exact-four set before adding approval; after approval
it requires the exact-five set. Smoke remains exact-four and is never approved.

The notebook:

```text
loads only sibling control_signal_artifacts.json in its first code cell
plots logical targets, requested/reconstructed AWG lanes, and effective signals
shows DAC codes, clipping headroom, latency, FIR, matrices, and error budgets
shows Stage 3.1 q2/c reference targets and provenance
shows all computational checks and artifact metrics
is cleared and genuinely executed with nbclient
contains sequential execution counts and zero error outputs
```

Execution counts or outputs must never be fabricated.

## 14. Runtime and profiles

The lifecycle is acyclic:

```text
provenance/config/schedule validation
  -> numerical compilation and metrics
  -> capture analysis_elapsed_seconds
  -> computational runtime gate
  -> immutable result and artifact bytes
  -> staging write and real notebook execution
  -> report bytes
  -> atomic three-file directory rename
  -> capture total_elapsed_seconds and publication checks
  -> atomic run_receipt.json write
  -> independent acceptance
```

`analysis_elapsed_seconds` starts immediately before provenance validation and stops after numerical
metrics, before the computational gate. It is the only runtime value consumed by the artifact's
computational gate. `total_elapsed_seconds_to_receipt_assembly` is captured after the three-file rename and
publication checks but before writing the receipt; it cannot flow backward into the artifact or report.
Independent acceptance validates the total budget from the canonical receipt. The artifact records analysis
runtime; the receipt records analysis and total runtime. The report never predicts or duplicates total time.

Formal limits:

```text
<=6 scenarios
<=10000 desired-grid samples (`N`) per scenario; `P<=N+191`
11 exact AWG lanes
<=64 FIR taps per lane
<=10 seconds analysis runtime
<=60 seconds end-to-end runtime
```

Smoke limits:

```text
<=2 scenarios
<=1000 desired-grid samples (`N`) per scenario; `P<=N+191`
same clock/DAC/matrices as formal
<=2 seconds analysis runtime
<=10 seconds end-to-end runtime
acceptance_eligible=false
```

No fallback may reduce sample rate, DAC bits, lane count, scenario checks, FIR semantics, or provenance.

## 15. Independent acceptance

Development may publish a formal acceptance candidate with:

```text
computational_ready=true
acceptance_eligible=true
status=ready_for_stage5_review
acceptance_candidate_ready=true
stage5_ready=false
approval_status=pending
```

`computational_ready` and status are artifact/report fields. `acceptance_candidate_ready` is a run-receipt
field and is not predicted in the pre-rename report.

Independent test verifies hashes, formulas, arrays, notebook execution, tests, and representative negative
cases. It writes:

```text
docs/decisions/<date>-stage4-final-acceptance-review.md
output/stage_04_control_signal/acceptance_approval.json
```

The canonical approval uses schema/artifact version `0.1` and this exact field set:

```text
schema_version
artifact_type=stage_04_control_signal_acceptance_approval
artifact_version
decision
reviewer_role
blocking_findings
stage4_design_freeze_manifest_sha256
control_channel_approval_sha256
control_config_sha256
logical_schedule_sha256
stage4_source_tree_sha256
stage3_1_acceptance_approval_sha256
control_signal_artifact_sha256
verification_notebook_sha256
verification_report_sha256
run_receipt_sha256
review_record_path
review_record_sha256
```

It binds the design freeze, registry approval, config, schedule, source, Stage 3.1 approval, artifact,
notebook, report, run receipt, and review. It requires:

```text
reviewer_role=independent_test_review_ai
decision=approved
blocking_findings=[]
```

The approval builder is fail-closed before writing. In addition to the exact-four directory and all raw-hash
bindings, it requires the bound artifact/report/receipt to satisfy every condition in
`formal_acceptance_candidate_ready` below. A smoke exact-four directory, even with valid hashes and a
syntactically valid proposed approval payload, is never eligible for an approval write.

Only `validate_stage4_acceptance_approval` may produce `stage5_ready=true`.

`Stage5ReadinessReport` has exactly
`{ok, stage5_ready, approval_decision, acceptance_approval_valid, bound_hashes, checks, blocking_reasons}`.
`bound_hashes` has exactly the eleven approval hash fields. Ordered checks use exact check-row fields and
names:

```text
design_freeze_hash_matches
channel_approval_hash_matches
config_hash_matches
schedule_hash_matches
source_hash_matches
stage3_1_approval_hash_matches
artifact_hash_matches
notebook_hash_matches
report_hash_matches
run_receipt_hash_matches
review_hash_matches
formal_acceptance_candidate_ready
approval_contract_valid
exact_five_file_set
```

The validator reloads every bound file, strictly validates artifact/report/receipt schemas and their
cross-consistency, verifies the exact-five directory, and recomputes all hashes.
`formal_acceptance_candidate_ready` passes if and only if all of these are true:

```text
artifact.profile == formal
artifact.acceptance_eligible == true
artifact.computational_gate.computational_ready == true
artifact.status == ready_for_stage5_review
artifact.scenario_order is the exact six-scenario formal order
artifact provenance binds the formal config and formal schedule
report.execution_succeeded == true
report.profile == formal and equals artifact.profile
report.acceptance_eligible == true
report.computational_ready == true
report.status == ready_for_stage5_review
report.blocking_reasons == []
receipt.execution_succeeded == true
receipt.profile == formal
receipt.acceptance_eligible == true
receipt.acceptance_candidate_ready == true
receipt.total_runtime_within_budget == true
receipt.blocking_reasons == []
```

No self-declared approval field can substitute for these current-byte checks. `ok=true` and
`stage5_ready=true` if and only if every check passes, the approval decision is approved,
`acceptance_approval_valid=true`, and `blocking_reasons=[]`; missing or unavailable evidence is false, never
skipped.

## 16. Required tests

### 16.1 Config and registry

```text
exact schemas and unknown-field rejection
bool/NaN/Infinity/string-numeric rejection
clock reciprocal equality
lane order and global uniqueness
FIR constraints
matrix shape, determinant, and condition number
idle flux equality with Stage 1
stale approval/hash failure before analysis
```

### 16.2 Sampling and shapes

```text
exact multiple-of-dt acceptance and off-grid rejection
half-open interval adjacency
identifier grammar, nonnegative start, end-within-scenario, and one-pulse-per-channel rejection
corrected Gaussian symmetry/endpoints
DRAG analytic derivative sign/antisymmetry/area
square and flattop-cos reference vectors
zero/negative duration and invalid rise rejection
```

### 16.3 Electronics

```text
Decimal.from_float half-even code vectors including ties, near-ties, and signed endpoints
out-of-range rejection without clipping
known static-matrix inverse/forward recovery
singular and condition-number rejection
latency alignment with different lane delays
direct-loop FIR equivalence, retained tail, and exact common N_awg/P padding
quantization bound formula
Z crosstalk and absolute idle restoration
```

### 16.4 Schedule and artifacts

```text
same-channel, same-port, and shared-lane conflicts
legal simultaneous q1/q2 XY and q1/q2/c Z
reference q2 resonance/coupler targets
exact four area comparisons, phase-frame rotation, and null/empty/duplicate fail-closed aggregation
formal root global metrics versus scenario-local metrics with no duplicated cross-scenario rows
smoke exact-two XY area rows, false readout aggregate, computational_ready=true, acceptance false
complex arrays encoded as I/Q
exact nested artifact/report schemas and cross-consistency
finite preflight JSON path and no partial output
target-exists transaction rejection
final report paths after rename
notebook clear-and-reexecute test
approval positive path and representative stale/tampered paths
syntactically valid hash-bound approval added to smoke exact-four is rejected before write and by validator
tampered scenario check boolean/message versus source rows is rejected for each of the four checks
one persisted DAC code changed to a different in-range integer is rejected against independent recomputation
```

## 17. Stage 5 interface

Stage 5 may consume only an artifact whose approval validates `stage5_ready=true`. It uses:

```text
effective time_center_ns
q1/q2 complex drive envelopes in GHz
q1/q2/c absolute flux in Phi0
carrier frequency and phase metadata
coordinate orders and provenance hashes
```

Stage 5 must not re-run control compilation, reinterpret AWG codes, or substitute logical targets for
effective signals. It converts GHz to angular units in one documented location when building the
time-dependent Hamiltonian.

Stage 5 ignores readout envelopes; Stage 8 consumes them later.

## 18. Freeze checklist

```text
[ ] Stage 4.0 ownership and seven-channel registry are unambiguous.
[ ] Config, schedule, artifact, report, and approval schemas are exact.
[ ] Clock/sample-center/half-open interval rules are fixed.
[ ] Pulse formulas and units are fixed.
[ ] Matrix directions and coordinate orders are fixed.
[ ] DAC code range, half-even rounding, and clipping semantics are fixed.
[ ] FIR/latency ordering and time origin are fixed.
[ ] Conflict semantics and deterministic ordering are fixed.
[ ] Error formulas, thresholds, finite/null behavior, and status priority are fixed.
[ ] Provenance and independent readiness lifecycle are acyclic and fail closed.
[ ] Runtime/sample ceilings and smoke/formal separation are fixed.
[ ] Stage 5 consumes effective signals only.
```
