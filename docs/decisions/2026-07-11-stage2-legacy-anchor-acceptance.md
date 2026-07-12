# Stage 2 Legacy Baseline Anchor Acceptance

Date:

```text
2026-07-11
```

Decision:

```text
accepted
```

Accepted artifact:

```text
output/stage_02_hamiltonian/hamiltonian_artifacts.json
```

Accepted SHA-256:

```text
222E9B7B3A0A3A8CEE7499E6E55AC167898877580EEC57A6B6E343E932D4E77C
```

Authorization:

```text
The user explicitly delegated handling of the legacy SHA-256 decision to the project manager in the
current Codex task. The project manager accepts the unchanged Stage 2 artifact bytes as the artifact
reviewed during the 2026-07-09 Stage 2 implementation review.
```

Evidence:

```text
The development AI, independent design-review AI, original test AI, replacement independent test AI,
and project manager independently computed the same SHA-256.
The production artifact remained unchanged throughout design freeze and Stage 2.1 package-A testing.
This decision establishes artifact identity only; the new Stage 2.1 candidate still requires full
regression, numerical-delta review, manifest validation, and independent approval.
```

Required handling:

```text
Archive the accepted bytes without transformation before overwriting the production Stage 2 artifact.
The archived bytes must retain the accepted SHA-256.
Set legacy_baseline_anchor.json decision=accepted and approved_by=user.
Bind the anchor, previous artifact, candidate artifact, manifest, and approval by raw-byte SHA-256.
```
