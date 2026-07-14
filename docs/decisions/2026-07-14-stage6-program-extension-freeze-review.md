# Stage 6 Program Extension Freeze Review

Date: 2026-07-14

## Scope

This review supersedes the original Stage 6 design-freeze record as implementation authority. It preserves the
fake-platform MVP and additionally freezes a fail-closed `program=null` request field plus the future two-level
Gate/Macro IR to Pulse IR boundary. It does not enable instruction execution or a physical backend.

## Bound design authority

```text
docs/stages/06_experiment_runtime_plan.md
ED715B3165669BB3B96EEC6A367256299D5F9ED034B9D4F2FCFDF73C28D42669

docs/designs/06_experiment_runtime_design.md
67E7B781F4170CB963301C68C1F1418B853920F0A31B483756E7E4C6D017872B

docs/decisions/2026-07-14-stage6-platform-only-entry.md
936D1CC1B226FC3FE0346B6EE42C0DE33EABBF2951561DC72722F0C70DEFA837

docs/decisions/2026-07-14-stage6-program-extension-amendment.md
94D4B3B462FB66C1AD914BAC63C911FF3D430C2F081A836454F116E55E7861BA
```

Any byte change to a bound file invalidates this review.

## Independent decisions

1. Development lead: APPROVE amended design freeze. Thread
   `019f5c41-1ced-7d52-a65d-0adff0c0de9c`.
2. Physics and numerics lead: APPROVE amended design freeze. Thread
   `019f5c41-00f4-7042-a082-2f3682c4fe30`.
3. Independent test lead: APPROVE amended Stage 6 design freeze. Thread
   `019f5c41-4278-7403-87b1-3c23d231444e`.
4. Reproducibility and release lead replacement review: APPROVE amended design freeze. Thread
   `019f5e6f-62dc-7663-a848-2a07f14f2858`.

All reviewers reported no remaining blocker, high, or medium finding against the current bytes.

## Decision

The amended Stage 6 design is frozen and implementation may proceed. The second synthetic experiment verifies
admission and scan expansion only, with no backend dispatch or publication. Every non-null program remains an
admission failure until a later independently frozen request schema and instruction-set contract exist.
