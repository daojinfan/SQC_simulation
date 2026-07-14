# Stage 6 Experiment Runtime Smoke Result

- Date: 2026-07-14
- Scope: platform-only deterministic experiment runtime
- Status: `completed`
- Physical experiment or calibration claim: none
- Stage 7 physics-capable scan readiness: not claimed

## Formal runs

Two independent output roots executed the exact frozen 2 x 3 scan:

```text
primary root  output/stage_06_experiment_runtime_smoke
primary run   2e86379d-5887-4e3c-982a-2580040615fe
replay root   output/stage_06_experiment_runtime_smoke_replay
replay run    27315831-c66e-4660-92ca-a8dd8e176c8e
```

Both runs returned `status=completed`, passed all seven independent verification checks, indexed one immutable
run, and rebuilt their SQLite catalogs with `ok=true` and no blocking reasons.

Primary terminal document hashes:

```text
manifest.json             C9216737662A3EA622F74ECF056674B2D24D8948D40F5E8B9B155DBC10271512
verification_report.json  E6301B38F66768704C1CE74BF8A5DDBB13AF295DB7F2F2EE12B8F9D520478140
receipt.json              EC9A64204E9A57D520E32EE8B8970144C9277CB7F11019AC25A7D4D6B71BF9C2
```

Replay terminal document hashes differ by design because run IDs, timestamps, output roots, events, and lock
identities are run-specific.

## Deterministic payload comparison

The following raw SHA-256 values were byte-identical across both output roots:

```text
request.json                ED0CD2CD19A57BCDF24D7281F541A478BF39FC291FE38585B7C893B0AD7D779F
point_table.json            65D050633DB71E192BA02E71C12AB57C68801F9C67F69B59FDFA87DBAD1C61D8
data/dataset.json           A9277CF704F0389BD498403D9789A8BB7FD2841D82E939E70245935A01DD94B5
data/response.bin           01279CD8EFD786E0F4ED0EA714EE57AF7C82BE12D7259339A5998FF7C120E1A0
snapshots/device.yaml       CA300E0AE08DCBBE7810F92AFDDFD713C6928A2E04E4A853FCE418B181E18D3F
snapshots/calibration.json  E3DD8BB9508AEE3984DAC6D01729466DB0023769DF437CF63A7C1BD15FEDD2F4
snapshots/environment.json  206239B6E05282A8F5BC7D40DE97DFCB971A5EA19AA76D9BDC10B78DE70C2905
snapshots/source.json       CDB42473BC09406A3DEC41FEC9C206FB46D1476C4A73B76620B4535E2D9CF8B9
source aggregate            7DD96650462B9AF439908A2014369982592E08B6CEC8C498F5EB956BFBE65EDB
```

The six little-endian `<f8` responses are exactly `[-2.0, 0.0, 2.0, -1.5, 0.5, 2.5]`.

## Verification

The independent Stage 6 test owner reran the three Stage 6 modules and reported `35 passed`. The project-manager
joint regression covering Stage 5 evolution, Hamiltonian provenance, and all Stage 6 modules reported
`56 passed`. Source compilation and `git diff --check` also passed.

The repository-wide historical suite cannot complete in this checkout because older Stage 1-4 tests require
ignored artifacts that are absent from `output/`. This is an existing fixture limitation, not represented as a
full-suite pass; Stage 2-4 remain covered by the sponsor acceptance recorded in
`docs/decisions/2026-07-14-stage2-stage4-user-acceptance.md`.

## Claim boundary

The only backend is `deterministic_fake_v1`. Every result binds `evidence_class=platform_test_fixture`,
`physics_claim=none`, `observation_model=absent`, and `measurement_payload=null`. The bound calibration state is
`uncalibrated` with `accepted=false`. This result validates the Stage 6 platform framework only.
