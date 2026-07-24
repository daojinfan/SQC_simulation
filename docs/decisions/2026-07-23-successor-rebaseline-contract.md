# Successor Rebaseline Manifest Contract

The verifier accepts a standalone JSON manifest with this exact top-level shape:

```json
{
  "schema_version": "0.1",
  "artifact_type": "successor_rebaseline",
  "artifact_version": "1",
  "status": "approved",
  "authorization": {"source": "user_approved_successor_rebaseline", "authorized_on": "2026-07-23"},
  "predecessor": {"version": "v1", "frozen_raw_sha256": ["..."], "historical_evidence_reexecution": "unavailable", "evidence_reuse": "prohibited"},
  "upstream": {"stage_02_1": {"path": "...", "raw_sha256": "..."}, "stage_03": {"path": "...", "raw_sha256": "..."}, "stage_03_1": {"path": "...", "raw_sha256": "..."}, "stage_04": {"path": "...", "raw_sha256": "..."}, "stage_04_0": {"path": "...", "raw_sha256": "..."}},
  "stage51": {"source_snapshot": {"path": "...", "raw_sha256": "..."}, "environment_snapshot": {"path": "...", "raw_sha256": "..."}, "authority": {"path": "...", "raw_sha256": "..."}, "approval": {"path": "...", "raw_sha256": "..."}, "stage_04_0_raw_sha256": "..."},
  "stage6": {"source_snapshot": {"path": "...", "raw_sha256": "..."}, "environment_snapshot": {"path": "...", "raw_sha256": "..."}, "authority": {"path": "...", "raw_sha256": "..."}, "approval": {"path": "...", "raw_sha256": "..."}},
  "generation_environment": {"python_implementation": "CPython", "python_version": "3.12.10", "platform": "windows", "lock_sha256": "..."},
  "selector": {"historical_artifact_version": "v1", "new_run_version": "v2", "current_selector": "v2"},
  "successor_content_sha256": "..."
}
```

`upstream` is exact and ordered: Stage 2.1, 3, 3.1, 4, then 4.0. Each bound JSON
uses its documented v2 artifact type, schema `0.1`, artifact version `0.2`,
`status=approved`, and `reviewer_role=independent_reviewer`. Stage 2.1 carries
`final_source_identity_sha256`; every later item carries the immediately previous
item's `predecessor_raw_sha256`. Stage 5.1 authority must repeat the final Stage
4.0 raw SHA. Every ellipsis represents an uppercase SHA-256 or an in-repository regular path,
as appropriate. Stage 6 source/environment/authority/approval artifacts must use
the v2 identity `stage_06_source_snapshot`, `stage_06_environment_snapshot`,
`stage_06_successor_authority`, and `stage_06_successor_approval`; the authority
binds both snapshot hashes and the approval binds its authority hash, status, and
non-empty independent reviewer role. `successor_content_sha256` is the canonical JSON SHA-256 of the
five fields `upstream`, `stage51`, `stage6`, `generation_environment`, and `selector`.  The
manifest is a verification input, not approval by itself: its `status` only
records that the independently produced successor approval files have already
been checked.

All JSON inputs must be byte-canonical (sorted keys, compact separators, ASCII
escaping), object keys must be unique, and `NaN`/`Infinity` are forbidden. Every
referenced file, including the final file, must be a regular, non-linked,
non-hardlinked file below the repository root.

Run the gate with:

```powershell
python tools/verify_successor_rebaseline.py path/to/successor_rebaseline_v2.json --repository-root .
```

No manifest is committed by this authorization record.  Creating it, generating
the referenced artifacts, and changing the production selector remain separate
transactions so a placeholder cannot be misrepresented as historical evidence.
