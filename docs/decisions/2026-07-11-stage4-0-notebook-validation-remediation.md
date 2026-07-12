# Stage 4.0 Notebook Validation and Runtime Dependency Remediation

## Status

Proposed for independent design review. This record is not an implementation or acceptance approval.

## Context

The first independent Stage 4.0 implementation test rejected the candidate for two reasons:

1. The notebook validator used a finite string blacklist. A genuinely executed notebook could read a file
   outside the candidate directory with `io.FileIO('../secret.json')`, update the report hash, and still be
   accepted.
2. `src/sqvm/control/artifacts.py` imported `nbclient.NotebookClient`, but `nbclient` was not declared in
   `pyproject.toml`.

Adding `nbclient` to `pyproject.toml` is not a local Stage 4 change. The accepted Stage 2.1 provenance
algorithm includes the raw `pyproject.toml` bytes in `stage2_model_source_tree_sha256`. Any edit therefore
invalidates the accepted Stage 2 artifact provenance and the Stage 3/3.1 trust chain even when the physics,
NumPy, SciPy, Hamiltonian code, and accepted artifacts are unchanged. A trial dependency edit reproduced
this conflict across the repository regression.

Stage 4.0 is an approved metadata overlay. Its frozen scope excludes physics changes and regeneration of
accepted Stage 1-3 artifacts. This remediation must not silently turn that overlay into a full physics
rebaseline.

## Decision

### 1. Notebook validation is an exact positive contract

The Stage 4.0 notebook generator and validator must share one immutable notebook template specification.
Validation must require all of the following:

- the exact total cell count, cell types, and cell order;
- the exact source text and static metadata for every markdown and code cell;
- the exact ten cell IDs, in order:
  `f23b4a12`, `a4a80a8c`, `0d7a14c9`, `1363ecab`, `9627b402`, `bf587910`, `6f9510ba`,
  `73c1ca22`, `921d67a9`, `db82b0b1`;
- the first code cell is the exact sibling `control_channel_manifest.json` loader;
- later code cells are the exact frozen read-only expressions over the in-memory `data` mapping;
- code-cell execution counts are exactly `1..N` in order;
- every code cell has exactly one `execute_result` output with the same execution count, empty output
  metadata, the exact `text/plain` value produced by the normative replay, and no other MIME key;
- no code cell has an error, stream, display-data, or update-display output;
- no extra, missing, reordered, or source-modified cell is accepted.

The validator must not use an import, API, filename, or token blacklist as its security boundary. Any source
change fails before approval, including `io.FileIO`, indirect paths, aliases, alternative imports, `eval`,
or code that otherwise reads outside the sibling manifest.

The existing formal exact-three notebook is not regenerated. It must pass the new template validator
byte-for-byte. The rejected external-read reproduction must fail even when its notebook is genuinely
executed and the report contains the matching tampered notebook hash.

Genuine execution is verified by deterministic replay, not by trusting persisted counts. The validator
must perform these exact steps before returning candidate-ready:

1. Verify that the running interpreter resolves, using Windows case-insensitive path comparison, to
   `C:\Users\fandaojin\anaconda3\python.exe`.
2. Resolve the `python3` kernel with
   `jupyter_client.kernelspec.KernelSpecManager().get_kernel_spec("python3")` after the `nbclient` preflight.
   Before launching it, require the exact argv list
   `[<approved interpreter>, "-Xfrozen_modules=off", "-m", "ipykernel_launcher", "-f",
   "{connection_file}"]`, where argv element zero resolves with the same Windows comparison rule to
   `C:\Users\fandaojin\anaconda3\python.exe`. A missing kernel, non-list argv, non-string element, extra or
   missing argument, different argument order, or mismatched executable fails before any Stage 4 staging or
   output directory is created.
3. In a system temporary directory outside the repository, write the already validated canonical manifest
   raw bytes as the sole input file named `control_channel_manifest.json`.
4. Build a fresh notebook from the shared immutable template, assigning the ten fixed cell IDs above rather
   than accepting or copying candidate IDs, and execute it once with the validated kernel and `nbclient`
   boundary below.
5. Validate both candidate and replay notebooks before normalization. Notebook-level metadata must have
   exact keys `{language_info, stage4_0_read_only}`; `stage4_0_read_only` is `true`; `language_info` must equal
   the replay value. Markdown metadata is exactly empty. Each code-cell metadata has the exact single key
   `execution`; that mapping has exact keys `{iopub.execute_input, iopub.status.busy, iopub.status.idle,
   shell.execute_reply}`. Every value is an ISO-8601 UTC string ending in `Z`; within each cell,
   `busy <= execute_input <= execute_reply <= idle`, and busy times are nondecreasing by execution count.
6. Remove only the validated per-code-cell `metadata.execution` mappings from both in-memory notebooks.
   Compare the remaining notebook mappings for exact deep equality, including nbformat versions, all static
   metadata, the exact required cell IDs, sources, execution counts, output types, output execution counts, MIME
   keys, and output values.
7. Delete the temporary directory. Any replay, metadata, cleanup, or equality failure rejects the candidate.

An exact-source notebook with fabricated counts, altered non-error output, extra output, altered MIME data,
or a self-consistent report hash therefore fails.

### 2. `nbclient` is a Stage 4 execution-environment prerequisite

For Stage 4 v0.1, `nbclient==0.10.2` is a required execution-environment prerequisite, not a change to the
accepted Stage 2 model dependency manifest.

The implementation must:

- avoid importing `nbclient` during ordinary `import sqvm` or read-only validation paths;
- obtain the version only with `importlib.metadata.version("nbclient")`, require exact string equality to
  `0.10.2`, and only then import `nbclient.NotebookClient` inside the notebook execution boundary;
- fail before creating a staging or output directory when package metadata is missing, the value is not the
  exact string `0.10.2`, or the subsequent import fails;
- emit one deterministic dependency error and never fall back to fabricated execution counts or outputs;
- include the actual `nbclient` version in the frozen Stage 4 environment fingerprint used by the Stage 4
  formal artifact;
- run the Stage 4.0 and Stage 4 formal workflows only with the recorded project interpreter
  `C:\Users\fandaojin\anaconda3\python.exe`, unless a later independently reviewed environment identity is
  approved.

`pyproject.toml` is restored to raw SHA-256
`43025E901AEBD5EAACE6CE98A6E93DD2BF1A6951D528B582BB68BBF69A003E20`. The restored Stage 2 model source
tree digest must equal the accepted
`0F50DB209F7069B82859A20A30B4785B2DBFEA56F59866EDD660AF0DD90DE972`. A future package-distribution change
may version the Stage 2 provenance algorithm or perform a full dependent rebaseline, but it is outside
Stage 4 v0.1.

### 3. Accepted upstream validation is unchanged

This remediation introduces no alternate historical validator and no skipped provenance equality. After
the required `pyproject.toml` restoration, Stage 4.0 must continue to call the existing current-byte
Stage 2.1 and Stage 3.1 validation path used by the D0 candidate.

The validation must recompute the restored current Stage 2 source-tree digest and require it to equal the
accepted value above. It must also retain all existing current-byte artifact, config, manifest, approval,
schema, canonical-byte, reviewer-role, blocking-finding, Stage 3.1 readiness, and hash-binding checks. The
only already-approved historical exception remains the Stage 2 CLI hash-at-rebaseline rule; this remediation
does not add another exception.

The trial compatibility helper that bypassed current source-tree recomputation is unapproved exploration and
must be reverted. A self-consistent but unanchored Stage 2 artifact/manifest/approval trio must still fail.

## Required Tests

The remediation is accepted only if all of these pass:

1. The original formal exact-three candidate validates without changing any candidate byte.
2. The executed `io.FileIO('../secret.json')` attack is rejected with a self-consistent report hash.
3. A changed import, indirect first-cell path, missing cell, extra cell, and reordered cell each fail.
4. Exact-source notebooks with a fabricated count, altered `text/plain` value, extra output, changed output
   type, changed output metadata, or changed execution metadata schema each fail with a self-consistent
   report hash.
5. A missing, changed, duplicated, or reordered cell ID fails. The replay template uses the fixed IDs and
   never derives them from candidate bytes.
6. Missing metadata, `0.9.9`, `0.10rc1`, `0.10.0`, `0.10.2+local`, malformed text, import failure after a
   matching metadata value, and the wrong interpreter each fail before staging/output creation and leave no
   partial directory. Exact `0.10.2` on the approved interpreter passes.
7. An approved host interpreter paired with a missing kernel, mismatched kernel executable, changed kernel
   argument, or extra kernel argument fails before staging/output creation. The exact frozen `python3`
   kernelspec passes.
8. Exact-three builder, exact-four approval validation, six bound-hash keys, and binding-mismatch behavior do
   not regress.
9. Focused tests, the safe full repository regression, external-cache compileall, and diff-check pass while
   restored `pyproject.toml`, accepted upstream files, and the formal exact-three hashes remain unchanged.
10. The restored current Stage 2 source-tree digest and existing Stage 3.1 readiness validator both pass; a
   current source-tree mismatch and an unanchored self-consistent Stage 2 trio both fail.

## Authorization Boundary

After independent approval of this record, development may modify only:

- `src/sqvm/control/artifacts.py`;
- `src/sqvm/control/compatibility.py`;
- `tests/test_control_channel_compatibility.py`.

As the sole fourth authorized protection action, development must restore `pyproject.toml` by removing only
the unapproved `nbclient>=0.10` line and must obtain the exact SHA-256 and Stage 2 source-tree digest specified
above. No other `pyproject.toml` change is allowed.

Development may not regenerate the formal
candidate, change the channel registry, modify accepted upstream artifacts, create an approval, start the
Stage 4 compiler, or change any physics, numerical, or acceptance contract.
