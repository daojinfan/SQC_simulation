# Stage 7.1.4 Configuration Workbench v1 Detailed Design

Status: frozen first-version UI and view-model authority

## 1. Boundary

Configuration Workbench v1 is the object-oriented Web surface for the existing PlatformConfiguration v0.2 lifecycle. It replaces the single long Draft form with a device-centered workspace.

This document does not change PlatformConfiguration storage, the v0.2 JSON Schema, QCIS v0.3, compiler semantics, PlatformAuthorityResolver, lifecycle endpoints, or execution authority.

The browser remains a local configuration and evidence tool. It does not execute experiments, edit physical device parameters, create a compiler/run context, or bypass Save Draft, Validate, Publish, and Activate.

## 2. Information Architecture

The application-level navigation remains Overview, Configurations, and Experiments. Configurations is a device configuration index, not a long form.

- Devices
  - Device ID
    - Active
    - Drafts
    - Published
    - Legacy and Uninitialized
- Draft or Snapshot workspace
  - Overview
  - Qubits / Q1
  - Qubits / Q2
  - Coupler C
  - Control Chain
  - Review

The workspace header always shows device, configuration name, lifecycle state, abbreviated content hash, parent, and requalification state. It exposes only lifecycle commands allowed in the current state.

## 3. Routes

| Route | Purpose |
| --- | --- |
| #/configurations | Device configuration index |
| #/configurations/devices/device-id | Active, Draft, Published, Legacy index |
| #/configurations/drafts/draft-id/overview | Draft entry point |
| #/configurations/drafts/draft-id/qubits/Q1 | Q1 workspace |
| #/configurations/drafts/draft-id/qubits/Q2 | Q2 workspace |
| #/configurations/drafts/draft-id/coupler | Coupler C workspace |
| #/configurations/drafts/draft-id/control | Control Chain workspace |
| #/configurations/drafts/draft-id/review | Draft validation and publish review |
| #/configurations/snapshots/snapshot-id/section | Same hierarchy, immutable |

Existing unqualified Draft and Snapshot URLs redirect to Overview. Existing configuration-history URLs remain read-only details. Routes are presentation state only; field mutability and lifecycle admission remain server-side.

## 4. Device Landing Page

The device page contains four compact bands.

1. Active configuration: activation time, content hash, eligibility, and an immutable Snapshot link.
2. Draft queue: validation state, changed-object count, requalification marker, and update time.
3. Published history: parent, Keep marker, evidence-reference state, and publication time.
4. Legacy and uninitialized records: migration status and source evidence link.

The page never expands all control fields or calibration values. Starting a Draft first selects an eligible base and displays the parent and expected control impact.

## 5. Workspace Sections

### Overview

Overview answers whether the configuration can progress. It shows identity, lifecycle, parent and Active relationship, grouped change summary, validation summary, requalification consequence, and the next allowed action.

Change groups are Q1, Q2, C, Control Chain, and Provenance. Empty calibration is an explicit Uninitialized state. Its only primary command is Initialize typed calibration. That command invokes the existing initialization transition only; it does not invent physical values or activate a configuration.

### Q1 and Q2

Q1 and Q2 use the same object page structure.

- Summary: reference-frequency authority and lifecycle.
- Active configuration: XY, XY2, X12, DTN, and F012 mapper selections.
- Settings library: named XY, XY2, X12, and DTN records for the QAgent.
- Flux mapper: F012ZBIAS mapper.
- Advanced details: waveform shape, phase and DRAG, generated projection, provenance.

A setting is a named calibration record, not a field group. Lists show ID, type, lifecycle, revision or base revision, and active status. A selection can reference only a compatible record for the same QAgent.

Reference frequency is common editable Draft content. Frequency source, status, revisions, hashes, and run provenance are read-only. Anharmonicity, local dimension, flux bounds, channels, and component mapping are device authority and appear only as context.

### Coupler C

The Coupler page owns endpoint topology, active CZ, FSIM, and G2 selections, the composite setting library, G2ZBIAS mapper, and read-only FSIM characterization.

Composite settings always show synchronized Q0, Q1, and Coupler columns. Common duration and dynamic phases appear above the columns.

| Mapper state | Q0 and Q1 input | Coupler input |
| --- | --- | --- |
| F012 enabled | frequency detune in GHz | not applicable |
| F012 disabled | flux offset in Phi/Phi0 | not applicable |
| G2 enabled | not applicable | coupling detune in GHz |
| G2 disabled | not applicable | flux offset in Phi/Phi0 |

Direct flux is not a DAC offset. Waveform class selects shape controls. Wave index is generated read-only compatibility information and never appears in an edit form.

### Control Chain

Control Chain owns control values by signal-chain meaning.

- Timing and DAC: clock, DAC range, rounding.
- Lanes: order, latency, FIR.
- Flux and mixing: idle flux plus XY, Z, and readout matrices.
- Qualification policy: Stage 4.1 thresholds.

Every editable control field carries a requires Stage 4.1 requalification marker. The first control edit visibly sets pending requalification. No page offers a physics calculation or activation bypass.

### Review

Review alone combines objects. It contains validation errors linked to object routes, parent-versus-current diff, publish readiness, requalification status, and read-only device, authority, lineage, and canonical JSON evidence.

Publish is not an Apply command. Activate exists only in an immutable eligible Snapshot Review page and uses the existing confirmation phrase. Resolver failures are blocking authority errors, not editable form errors.

## 6. Field Ownership and Modes

| Object | Owner | Common Draft controls | Advanced controls | Read-only |
| --- | --- | --- | --- | --- |
| Draft metadata | Overview | name, note | none | IDs, actor, audit times |
| Reference authority | Q1 or Q2 | frequency | none | source, status, revision, hash, run ID |
| Gate configuration | Q1, Q2, C | compatible active selection | XY pi implementation | Z implementation, IDs |
| XY, XY2, X12 setting | Q1 or Q2 | amplitude, length | shape, phase, DRAG | identity, target, transition, lifecycle |
| DTN setting | Q1 or Q2 | active selection | none in v1 | record structure |
| F012 mapper | Q1 or Q2 | none | mapper coefficients | type, target, lifecycle |
| CZ or FSIM setting | Coupler C | active selection, duration, mode switches | shapes, detunes, phases | identity, target, lifecycle |
| G2 mapper | Coupler C | none | point table | type, target, lifecycle |
| FSIM characterization | Coupler C | none | none | complete artifact |
| Control Chain | Control Chain | permitted idle flux | timing, DAC, lanes, mixing, thresholds | generated state |
| Device and authority | Review | none | none | complete object |

Common mode is the default. Advanced mode is a local disclosure inside an object detail, not a global JSON mode. Raw JSON is evidence only. The browser never provides writable raw JSON or form fields for physical device fields, hashes, revisions, status, provenance, or generated wave index.

## 7. Initialization and Record Workflow

1. Create a Draft from a legacy or base configuration.
2. Overview exposes initialization only when calibration is empty.
3. After initialization, Q1, Q2, and C show missing-object cards, not validation errors.
4. New records use a target-first, type-second dialog. The service supplies Draft lifecycle fields; the form supplies only permitted typed content.
5. Active selection becomes available only when target and gate or mapper type are compatible.
6. Validate reports incompleteness without manufacturing calibration. Publish converts valid Draft records into accepted immutable records. Activate still requires existing eligibility checks.

Experiment candidates may prefill supported Draft records. The workbench visibly carries candidate provenance but still requires Validate, Publish, and explicit Activate.

## 8. Diff View-Model Contract

The client does not diff rendered labels or raw JSON. The API, or a server-owned adapter, provides a stable finite-JSON view model.

| Field | Meaning |
| --- | --- |
| schema version | View-model version |
| base | Parent configuration ID and content hash |
| current | Draft or Snapshot ID, hash, kind |
| summary | Changed-object count, control change, requalification, publish readiness |
| sections | Q1, Q2, Coupler, Control, Provenance groups |
| section severity | Info, warning, or blocking |
| item object type | Reference authority, gate configuration, setting, mapper, control, authority |
| item path | Canonical dotted platform path |
| item change | Added, modified, removed, generated |
| before and after | Finite JSON value or null |
| item impact | Calibration only, requires requalification, authority |

The object keys drive workspace change badges, Review rows, and validation-error routing. Hashes, revisions, and publication provenance are generated changes and cannot be edited. The view model is informational; the server remains publishing and activation authority.

## 9. Visual and Responsive Rules

The workbench is a dense operational tool. Desktop uses persistent left workspace navigation, compact header actions, and an unframed main work area. Do not nest cards. Use object headers, section bands, tables, and inset detail panels.

- Every status has text plus restrained color: Draft, Valid, Invalid, Published, Active, Uninitialized, Requalification required, Blocking.
- Q1, Q2, and C are primary labels. A small Q1 - C - Q2 line provides topology context.
- Units belong in labels: GHz, rad, samples, V, Phi/Phi0.
- Matrix and mapper tables use fixed columns and horizontal scroll containers.
- Disabled destructive actions have a textual reason.
- Autosave retains existing debounce. Header state is Saved, Saving, Unsaved, or conflict.
- Focus follows route changes and returns to its invoking control after a dialog closes.

Below 900 px, the rail becomes a section picker and object summary precedes details. Below 600 px, grids become one column; Q0, Q1, and C waveform columns stack as labelled panels; tables scroll rather than compress. Mobile keeps Save, Validate, lifecycle status, validation errors, requalification impact, and Active state visible without page-level horizontal overflow.

## 10. Acceptance Criteria

1. Device index reaches a Draft object route without displaying an all-fields editor.
2. Q1, Q2, C, Control Chain, and Review render only owned objects plus necessary read-only context.
3. Each validation error routes to one workspace section and object detail.
4. Any Control Chain edit visibly requires requalification before Publish or Activate.
5. Mapper switches show only direct or mapped inputs, never both.
6. Snapshot routes have the same hierarchy and no editable controls.
7. Empty legacy calibration renders Uninitialized and cannot fabricate records or become Active.
8. Existing lifecycle endpoints and persistence shapes remain compatible; this is a view and view-model redesign.
9. Desktop 1440 by 900 and mobile 390 by 844 have no overlap or page-level horizontal overflow.
10. The browser receives neither mutable compiler authority nor a route that bypasses PlatformAuthorityResolver.

