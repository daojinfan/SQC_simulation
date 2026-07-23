# Stage 7.1.3 PlatformConfiguration v0.2 Detailed Design

Status: implementation authority for PlatformConfiguration v0.2

## 1. Scope and authority

`PlatformConfiguration` is the versioned configuration authority consumed by the calibration Web console and
by the QCIS v0.3 execution entrance. It is not a device-model editor, a hardware readout configuration, or a
second waveform compiler. Its companion machine schema is
`docs/designs/07_1_3_platform_configuration_v0_2.schema.json`.

New writes target QCIS profile `qcis_stage7_calibration_v3` only. QCIS v0.2 authority shapes remain readable
for historical evidence replay, but a v0.2 carrier/frequency field is never accepted in a new v0.2 platform
draft. In particular, calibrated XY settings do not store a physical carrier frequency.

The authoritative layering is:

```text
immutable device artifact + immutable active PlatformConfiguration snapshot
  -> PlatformAuthorityResolver
  -> QCISInstructionProfile, QAgentRegistry, GateConfiguration,
     WaveformRegistry, ClockProfile, CompilerSnapshot, expected hashes
  -> immutable CircuitExecutionContext
  -> run_circuits / compile_qcis
```

The resolver is the only production path from an Active snapshot to a compiler or `run_circuits` context.
Callers must not assemble those authorities from form data, configuration fragments, or mutable Draft objects.

## 2. Canonical field tree

Published snapshots use this exact top-level shape. Drafts use the separate `platform_configuration_draft`
shape in the JSON Schema, but carry the same two editable partitions.

```text
PlatformConfigurationSnapshot
├─ schema_version: "0.2"
├─ artifact_type: "platform_configuration_snapshot"
├─ artifact_version: "0.2"
├─ snapshot_id, state_id, device_id, name, reason, actor_id, published_utc
├─ status: "published" | "active"
├─ keep
├─ parent {configuration_id, content_sha256, control_values_sha256,
│          calibration_values_sha256}
├─ readonly
│  ├─ device_ref {path, sha256}
│  └─ authority_refs {device_sha256, instruction_profile_sha256,
│                     compiler_snapshot_sha256}
├─ editable
│  ├─ control_values
│  │  ├─ clock {sample_rate_Hz, dt_ns}
│  │  ├─ dac {bits, full_scale_min_V, full_scale_max_exclusive_V, rounding}
│  │  ├─ lane_order, lanes.<lane> {latency_samples, fir}
│  │  ├─ static_mixing.{xy,z,readout} {input_lanes, output_coordinates, matrix}
│  │  ├─ idle_flux_phi0 {q1, q2, c}
│  │  ├─ simulation.calibration_model {charge_cutoffs, retained_energy_levels,
│  │  │                                  convergence_charge_cutoffs,
│  │  │                                  convergence_retained_energy_levels}
│  │  └─ acceptance {...Stage 4.1 thresholds...}
│  └─ calibration_values
│     ├─ qagents.{Q1,Q2}.reference_frequency_authority
│     ├─ gate_configuration.{Q1,Q2,C}
│     ├─ waveform_registry.settings.<setting_id>
│     ├─ waveform_registry.mappers.<mapper_id>
│     └─ fsim_characterizations.<characterization_run_id>
├─ content_sha256
├─ requires_requalification
└─ experiment_eligible
```

`readonly.device_ref` points to the device authority. It is the only source for physical components,
capacitances, inductances, junctions, QAgent channel capabilities, Q1/Q2/C topology, local dimensions,
anharmonicity, and flux bounds. `anharmonicity_GHz` therefore remains device/authority read-only in this
version. `Q1`, `Q2`, and `C` are canonical QCIS tokens; the resolved tensor order remains `q1/c/q2`.

`waveform_class` is the canonical waveform discriminator: `rectangle`, `gaussian`, `flattop`, or `acz`.
`wave_index` is generated only as a QCIS compatibility projection (`0`, `1`, `2`, `5` respectively), and is
not a stored editable input. Formula version and the class-to-index mapping are instruction-profile authority.

`control_values.simulation.calibration_model` is the editable numerical truncation used by local calibration
evolution. The default retained dimensions are `q1=5`, `c=3`, and `q2=5`, so the projected Hilbert dimension
is 75. The product is derived and is not stored separately. Old snapshots may omit `simulation`; the resolver
then uses the default model configuration, while mutable current configurations are migrated on read.

## 3. Canonical JSON example

```json
{
  "schema_version": "0.2",
  "artifact_type": "platform_configuration_snapshot",
  "artifact_version": "0.2",
  "snapshot_id": "8ae9c824-42bf-482d-b26d-e1595a6cfb13",
  "state_id": "8ae9c824-42bf-482d-b26d-e1595a6cfb13",
  "device_id": "demo_2q1c2r",
  "name": "Q1 frequency acceptance",
  "reason": "accepted spectroscopy candidate",
  "actor_id": "project.manager",
  "published_utc": "2026-07-18T10:00:00Z",
  "status": "published",
  "keep": false,
  "parent": {
    "configuration_id": "uncalibrated",
    "content_sha256": "a0c4f4d0d82672b34fe34b55dc0e6ee06b33a04b9cc3e6d79d6085ac1a033b60",
    "control_values_sha256": "c0c4f4d0d82672b34fe34b55dc0e6ee06b33a04b9cc3e6d79d6085ac1a033b60",
    "calibration_values_sha256": "d0c4f4d0d82672b34fe34b55dc0e6ee06b33a04b9cc3e6d79d6085ac1a033b60"
  },
  "readonly": {
    "device_ref": {
      "path": "configs/devices/2q1c2r.yaml",
      "sha256": "CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F"
    },
    "authority_refs": {
      "device_sha256": "CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F",
      "instruction_profile_sha256": "e0c4f4d0d82672b34fe34b55dc0e6ee06b33a04b9cc3e6d79d6085ac1a033b60",
      "compiler_snapshot_sha256": "f0c4f4d0d82672b34fe34b55dc0e6ee06b33a04b9cc3e6d79d6085ac1a033b60"
    }
  },
  "editable": {
    "control_values": {
      "clock": {"sample_rate_Hz": 2000000000, "dt_ns": 0.5},
      "dac": {"bits": 16, "full_scale_min_V": -0.33, "full_scale_max_exclusive_V": 0.33, "rounding": "half_even"},
      "lane_order": ["q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q", "q1_z", "q2_z", "c_z", "r1_ro_i", "r1_ro_q", "r2_ro_i", "r2_ro_q"],
      "lanes": {
        "q1_xy_i": {"latency_samples": 24, "fir": [0.8, 0.2]},
        "q1_xy_q": {"latency_samples": 24, "fir": [0.8, 0.2]},
        "q2_xy_i": {"latency_samples": 26, "fir": [0.8, 0.2]},
        "q2_xy_q": {"latency_samples": 26, "fir": [0.8, 0.2]},
        "q1_z": {"latency_samples": 10, "fir": [0.7, 0.2, 0.1]},
        "q2_z": {"latency_samples": 12, "fir": [0.7, 0.2, 0.1]},
        "c_z": {"latency_samples": 8, "fir": [0.7, 0.2, 0.1]},
        "r1_ro_i": {"latency_samples": 20, "fir": [0.85, 0.15]},
        "r1_ro_q": {"latency_samples": 20, "fir": [0.85, 0.15]},
        "r2_ro_i": {"latency_samples": 22, "fir": [0.85, 0.15]},
        "r2_ro_q": {"latency_samples": 22, "fir": [0.85, 0.15]}
      },
      "static_mixing": {
        "xy": {"input_lanes": ["q1_xy_i", "q1_xy_q", "q2_xy_i", "q2_xy_q"], "output_coordinates": ["q1_drive_i_GHz", "q1_drive_q_GHz", "q2_drive_i_GHz", "q2_drive_q_GHz"], "matrix": [[0.1, 0.002, 0.001, 0], [-0.001, 0.098, 0, 0.001], [0.0005, 0, 0.102, -0.0015], [0, 0.0007, 0.001, 0.099]]},
        "z": {"input_lanes": ["q1_z", "q2_z", "c_z"], "output_coordinates": ["q1_delta_flux_phi0", "q2_delta_flux_phi0", "c_delta_flux_phi0"], "matrix": [[0.5, 0.005, 0.01], [0.004, 0.5, 0.012], [0.008, 0.006, 0.5]]},
        "readout": {"input_lanes": ["r1_ro_i", "r1_ro_q", "r2_ro_i", "r2_ro_q"], "output_coordinates": ["r1_device_i_V", "r1_device_q_V", "r2_device_i_V", "r2_device_q_V"], "matrix": [[0.001, 0.00001, 0, 0], [-0.00001, 0.001, 0, 0], [0, 0, 0.00102, 0.00001], [0, 0, -0.00001, 0.00102]]}
      },
      "idle_flux_phi0": {"q1": 0.1, "q2": 0.0, "c": 0.27},
      "simulation": {
        "calibration_model": {
          "charge_cutoffs": {"q1": 7, "c": 7, "q2": 7},
          "retained_energy_levels": {"q1": 5, "c": 3, "q2": 5},
          "convergence_charge_cutoffs": {"q1": 8, "c": 8, "q2": 8},
          "convergence_retained_energy_levels": {"q1": 6, "c": 4, "q2": 6}
        }
      },
      "acceptance": {"max_condition_number": 100, "max_xy_area_relative_error": 0.005, "max_readout_area_relative_error": 0.005, "max_z_flat_top_error_phi0": 0.00002, "max_phase_proxy_rad": 0.1, "phase_proxy_window_ns": 32, "max_formal_samples_per_scenario": 10000, "analysis_runtime_budget_seconds": 10, "total_runtime_budget_seconds": 60}
    },
    "calibration_values": {
      "qagents": {
        "Q1": {"reference_frequency_authority": {"reference_frequency_GHz": 5.105, "frequency_source": "accepted_simulation", "calibration_run_id": "spectroscopy_0042", "revision": 4, "setting_hash": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}},
        "Q2": {"reference_frequency_authority": {"reference_frequency_GHz": 5.220, "frequency_source": "bootstrap_seed", "calibration_run_id": "bootstrap_q2", "revision": 1, "setting_hash": "1123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}}
      },
      "gate_configuration": {
        "Q1": {"active_xy_setting": "q1_xy_v4", "active_xy2_setting": "q1_xy2_v4", "active_xy12_setting": "q1_xy12_v2", "active_detune_setting": "q1_dtn_v1", "active_f012zbias_mapper": "q1_f012_v3", "xy_pi_impl": true, "z_gate_impl": "VIRTUAL"},
        "Q2": {"active_xy_setting": "q2_xy_v1", "active_xy2_setting": "q2_xy2_v1", "active_xy12_setting": "q2_xy12_v1", "active_detune_setting": "q2_dtn_v1", "active_f012zbias_mapper": "q2_f012_v1", "xy_pi_impl": true, "z_gate_impl": "VIRTUAL"},
        "C": {"active_cz_setting": "c_cz_v1", "active_fsim_setting": "c_fsim_v1", "active_g2zbias_mapper": "c_g2_v1"}
      },
      "waveform_registry": {
        "settings": {},
        "mappers": {}
      },
      "fsim_characterizations": {}
    }
  },
  "content_sha256": "90c4f4d0d82672b34fe34b55dc0e6ee06b33a04b9cc3e6d79d6085ac1a033b60",
  "requires_requalification": false,
  "experiment_eligible": true
}
```

The example leaves registries empty only to keep the top-level example focused; a resolver rejects that snapshot
until all selected records are present and accepted.

## 4. Typed calibration records

### 4.1 QAgent and gate selection

Q1 and Q2 each carry exactly one reference-frequency authority. `frequency_source` is `bootstrap_seed`
before the first accepted spectroscopy result and `accepted_simulation` afterward. QCIS v0.3 obtains `f01`
only from this record. `X`, `Y`, `XY`, `RX`, `RY`, and `RXY` drive at `f01`; `X12` drives at
`f01 + anharmonicity_GHz`. A setting may not duplicate `f01`, `f12`, `frequency_GHz`,
`carrier_frequency_GHz`, or a calibrated drive detuning.

`gate_configuration.Q1/Q2` selects `active_xy_setting`, `active_xy2_setting`, and
`active_xy12_setting`; it also contains `xy_pi_impl` and `z_gate_impl`. `gate_configuration.C` selects
`active_cz_setting`, `active_fsim_setting`, and `active_g2zbias_mapper`. Q1/Q2 select their own
`active_f012zbias_mapper` and `active_detune_setting`. Every selected setting or mapper must exist, have the
same target, be `accepted`, and have a valid generated hash before an Active resolver can emit an authority.

### 4.2 XY, X12, DTN, mappers, CZ, and FSIM

Every setting has generated identity/provenance fields:
`setting_id`, `target`, `gate_type`, `status`, `revision`, `setting_hash`, and
`calibration_run_id`. The editable calibrated content of an XY/XY2 setting is `waveform_class`,
`length_samples`, `amplitude_GHz`, `phase_offset_rad`, `dragAlpha_samples`, and class-specific shape
fields. `X12` additionally requires `transition="12"`; XY and XY2 use `transition="01"`.

The active DTN setting has `gate_type="DTN"`, `control_role="z"`, `envelope_class="rect"`, and
`input_unit="phi_over_phi0"`. Direct DTN amplitude is an idle-relative `Phi/Phi0` rectangle.

An `F012ZBIAS_MAPPER` belongs to a qubit and stores only `f01max_GHz`, `k_rad_per_phi0`, and
`idle_flux_offset_phi0`, with ordinary generated record fields. A `G2ZBIAS_MAPPER` belongs to C and stores
`coupling_detune_GHz[]`, `zbias_offset_phi0[]`, `interpolation="piecewise_linear"`, and
`extrapolation="reject"`. Its input axis is strictly increasing and contains `(0,0)`; output offsets are
deliberately not required to be monotonic.

CZ and FSIM are independent settings with `target="C"`, a common `duration_samples`, mapper switches, and
exactly `waveforms.q0`, `waveforms.q1`, and `waveforms.coupler`. If
`use_f012zbias_mapper=true`, qubit waveforms use `frequency_detune_GHz`; otherwise they use
`flux_offset_phi0`. If `use_g2zbias_mapper=true`, the coupler uses `coupling_detune_GHz`; otherwise it
uses `flux_offset_phi0`. Direct `flux_offset_phi0` is always a `Phi/Phi0` increment. It is not a DAC
offset and `control_values.dac` has no offset field. The two dynamic phases are negated into post-gate frames.

FSIM characterization is separate, never a waveform input, and contains
`theta_rad,zeta_rad,chi_rad,gamma_rad,phi_rad`, typed QPT/XEB metrics, leakage, method, and provenance.
This design does not add readout calibration, shots, IQ data, assignment matrices, or physical-device editing.

## 5. Editable matrix and lifecycle

| Field class | Draft | Validate | Publish | Active snapshot / resolver |
| --- | --- | --- | --- | --- |
| Draft name, note | editable | length/actor checks | copied as provenance | read-only |
| `control_values.*` | editable | exact control schema and finite values | canonicalized and hashed | read-only; change requires requalification |
| Reference frequency and calibrated numeric setting/mapper content | editable | typed record and target/reference checks | accepted revision/hash/provenance generated | read-only |
| Active setting/mapper selection | editable | compatible candidate/accepted target required | resolved to accepted ID | read-only |
| Device fields, `device_ref`, authority refs | read-only | immutable-reference check | copied unchanged | read-only |
| IDs, type discriminators, status, revisions, hashes, run IDs, lineage | generated/read-only | consistency checked | generated or regenerated | read-only |
| FSIM characterization | read-only experiment artifact | verifier result required | referenced/copied unchanged | read-only |

Save Draft stores only a mutable Draft plus bounded checkpoints. Validate never changes a value; it produces a
validation report and records whether the control hash differs from the parent. Publish requires successful
validation, creates an immutable snapshot, assigns new accepted record revisions and hashes for changed records,
and does not activate it. Activate checks `experiment_eligible`, verifies the full snapshot and external
authorities, then atomically updates the one Active pointer for its device.

A newly authored or modified Draft record carries `status="draft"`, `base_revision`, `base_setting_hash`,
and null publication fields. On publication it becomes `accepted`. Users never retain an old hash after a
value change.

## 6. Canonical hashing, resolver, and API contract

Canonical JSON is UTF-8, finite JSON, deterministic key ordering, and the project canonical JSON byte encoding.
`setting_hash` is SHA-256 over a published record excluding only `setting_hash`. `content_sha256` hashes
canonical `editable`; component hashes cover control, calibration, device reference, instruction profile,
QAgent registry, gate configuration, waveform registry, clock, and compiler. `revision` increments once for
each changed accepted record. Publishing takes `calibration_run_id` from verified candidate provenance or
generates explicit manual-acceptance provenance. Clients cannot supply those generated fields.

`PlatformAuthorityResolver.resolve(active_pointer, repository_root)` must load and hash-check the pointed
snapshot and device reference; reject path escape, topology/channel/tensor-order/flux-bound/device mismatch;
resolve the frozen QCIS v0.3 profile and compiler; merge device capabilities with permitted calibration overlays;
validate selected accepted records and mapper domains; then construct immutable `QCISAuthorities`, exact
`expected_sha256`, and an immutable `CircuitExecutionContext`. It returns no mutable dictionaries and rejects
v0.2 new-write shapes, missing records, unknown fields, or non-finite values before execution.

The API returns `schema_version`, complete `readonly`, complete `editable`, `content_sha256`,
`validation`, and an editability manifest. Structured forms submit the exact `editable` root with
`expected_content_sha256`; Advanced JSON is read-only evidence and is not a second write path. The server owns
lifecycle, generated fields, and concurrency checks. The resolver is backend-only. `SET` remains a circuit-local
overlay with its own policy hash and cannot persist configuration.

## 7. v0.1 migration and non-goals

A v0.1 `platform_uncalibrated_v1.json` with `values={}` migrates to an `uninitialized` v0.2 record with
the original device reference/hash, imported validated formal control profile, empty calibration values, and
immutable `legacy_source_sha256`. It must not fabricate frequency authorities, settings, mappers, revisions,
run IDs, hashes, or an Active pointer. It cannot resolve to a compiler/run context until accepted calibration
records exist.

Legacy QCIS v0.2 snapshots remain readable only through an explicit evidence-replay adapter. They are never
silently upgraded, rehashed as v0.3 records, or offered as editable PlatformConfiguration v0.2 drafts. This
version excludes DAC/lane static offsets, arbitrary numeric PLS units, readout calibration, physical-device
editing, direct Active mutation, user-authored hashes, v0.2 new-write carrier settings, and action-list
synthesis.
