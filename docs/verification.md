# Verification

Optional, opt-in gating for `dispatch` batch runs: every worker edit is snapshotted before it happens, checked against a deterministic gate command after, and reverted byte-for-byte if it regresses. A 200-file batch becomes 200 independently verified edits instead of one all-or-nothing change.

**Applies only to skills that edit the file they were dispatched with.** `scripts/verify.py`'s `begin`/`check` cycle snapshots and re-checks one specific path: the file `dispatch` discovered and handed to the worker. That holds for `code-commenter`, `type-annotator`, `license-header-injector`, `error-handling-auditor`, and `import-sorter-cleaner` — each edits the dispatched file in place. It does **not** hold for `test-stub-generator`, whose whole point is to write a *new*, differently-named file (a test file) without touching the source file it was given — enabling `verify:` there would snapshot and re-check the untouched source file and never actually gate the generated artifact. `test-stub-generator`'s `batch.yaml` leaves `verify:` unset for exactly this reason. Project skills (`readme-master`, `changelog-fragment-extractor`, `readme-per-folder`) don't use this system at all — see [`docs/project-skills.md`](project-skills.md), whose review model is a plain `git diff`/`git status`, not a snapshot/gate/revert cycle.

## Enabling it

Add a `verify:` section to the skill's `batch.yaml`:

```yaml
verify:
  enabled: true
  mode: per_file                      # per_file | checkpoint
  checkpoint_every: 25                # only relevant when mode: checkpoint
  gate_config: .comrades/verify.json  # path to gate config, relative to repo root
  abort_threshold: 10                 # consecutive verify failures before halting the batch
```

And a gate config at the path named by `gate_config` — see `docs/examples/verify.node.json` and `docs/examples/verify.python.json` for working starting points.

Pass `--no-verify` on the `/code-comrades:dispatch` command to bypass verification for one run even if it's configured.

## Two modes

**`per_file`** (default) gates every edit immediately: snapshot, worker edits, gate, keep-or-revert. Simplest and safest. Correct for any gate; on a project-scoped gate it serializes each file's gate run (see below).

**`checkpoint`** is for expensive project-scoped gates (`tsc --noEmit`, a full test suite) where running the gate once per file would cost more than the edits themselves. Cheap file-scoped gates still run per edit; the project-scoped gate runs once over a whole window of `checkpoint_every` files. If it fails, delta-debugging bisection isolates which edits are responsible in `O(k log n)` gate runs for `k` culprits rather than `n`, reverts only those, and re-confirms the tree is green.

## Pre-existing failures

A file — or a whole project — can already be failing its gate before the batch touches anything (pre-existing lint debt, a project-wide type error unrelated to the files being edited). `on_baseline_fail` in the gate config controls what happens:

- `skip` (default) — the edit can't be attributed a verdict against an already-broken baseline, so it's rolled back and the file is left alone.
- `allow` — keep the edit regardless of gate outcome. Use this when the batch's purpose is to *fix* the condition the gate checks.
- `revert` — treat as failure. For `checkpoint` mode specifically, this means: run bisection anyway and revert whichever edits are still implicated, even against an already-broken baseline. Useful when some edits in the batch might fix the pre-existing failure while others don't — bisection is what tells them apart. If nothing in the batch can fix it (the break is in a file outside the batch entirely), this reverts the whole window.

This applies to project-scoped gates the same way it already applies to file-scoped ones — a `baseline` call captures the project gate's state once, before any file in the run is touched, so `checkpoint` mode's bisection is never blamed for a failure that predates the batch.

## Scope and concurrency

- `scope: file` — the gate runs against one file (`{file}` is substituted into the command). Safe to run concurrently with other workers.
- `scope: project` — the gate reads the whole tree and is automatically serialized behind a cross-platform lock, because it must never observe another worker's half-applied edit.

With `scope: project`, the gate lock serializes verification — workers still run concurrently, but gate checks execute one at a time. For large batches with slow project gates, use `mode: checkpoint` to amortize the cost instead of paying it once per file.

## Troubleshooting

**Exit code 3 — "gate cannot execute."** A typo'd command, a missing binary, or a gate that isn't on `PATH`. The run aborts immediately rather than silently treating every file as unverifiable. Fix the gate command in the config and re-run (or resume — files already processed aren't re-dispatched).

**Abort threshold hit.** `ABORT_THRESHOLD` (default 10) consecutive reverted files halts the run — this usually means the gate itself is misconfigured for what this skill produces (e.g. a linter rule the skill's output legitimately can't satisfy), not that the files are actually broken. Check a `report` before assuming otherwise:

```bash
python3 scripts/verify.py --root . report --run <id>
```

## Undo

```bash
python3 scripts/verify.py --root . revert-run --run <id>
```

Undoes every edit `verify.py` decided to keep during that run. This is manual only — never invoked automatically by `dispatch`.
