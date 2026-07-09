---
name: dispatch
description: Apply a batchable code-comrades skill (e.g. code-commenter) across every matching file in a folder or repository. Invoked explicitly as /code-comrades:dispatch <skill> <path> [--dry-run] [--config key=value,...] [--max-files N] — never auto-invoke this from a conversational request.
disable-model-invocation: true
---

# dispatch

You are orchestrating a batch run of a code-comrades skill across many files. Follow these steps in order; do not skip or reorder them.

## 0. Parse arguments

`$ARGUMENTS` contains everything after the invocation name, e.g. `code-commenter src/ --dry-run --config audience=junior --max-files 5`.

Parse it as:
- First whitespace-separated token: `SKILL_NAME`.
- Second token: `TARGET_PATH` (relative to the current working directory).
- Remaining tokens, any order:
  - `--dry-run` — boolean flag.
  - `--config key=value,key2=value2` — comma-separated pairs merged over the skill's `default_config`. A dotted key like `style.python=numpy` sets a nested field.
  - `--max-files N` — integer cap on files processed *this session* (on a resume, this means N remaining, not N total across the whole historical run).
  - `--no-verify` — boolean flag. Disables verification for this run even if `batch.yaml` configures a `verify:` section. Has no effect if the skill has no `verify:` section to begin with.

If `SKILL_NAME` or `TARGET_PATH` is missing, stop and ask the user for the missing argument rather than guessing.

## 1. Resolve the skill

Read `"$CLAUDE_PLUGIN_ROOT/skills/{SKILL_NAME}/batch.yaml"`. If it doesn't exist, stop and report: "`{SKILL_NAME}` has no batch.yaml — it isn't a batchable skill." Otherwise extract: `extensions`, `exclude_patterns.mode`, `exclude_patterns.patterns`, `output_mode`, `max_parallel`, `discovery`, `default_config`.

Resolve the effective config: start from `default_config`, then apply any `--config` overrides on top.

## 2. Verify the path and determine the repo root

Check `{TARGET_PATH}` exists: `test -e "{TARGET_PATH}"`. If it doesn't, stop and report clearly: "`{TARGET_PATH}` does not exist." Do not proceed to discovery or create a manifest.

Run `git -C "{TARGET_PATH}" rev-parse --show-toplevel`. If it succeeds, that's `REPO_ROOT`, and gitignore filtering is available. If it fails (not a git repo), use the directory the command was invoked from as `REPO_ROOT`, and pass `--no-gitignore` to discovery in the next step.

Compute `TARGET_PATH_RELATIVE` as `TARGET_PATH` made relative to `REPO_ROOT` (this is what gets passed to `manifest.py --target-path`, never a cwd-dependent path).

## 3. Discover files

```bash
python "$CLAUDE_PLUGIN_ROOT/{discovery script path from batch.yaml, e.g. scripts/discover_files.py}" \
  --path "{TARGET_PATH}" \
  --extensions "{extensions value from batch.yaml}" \
  --exclude-patterns "{comma-joined exclude_patterns.patterns}" \
  --exclude-patterns-mode "{exclude_patterns.mode}" \
  --max-size-kb 500 \
  > /tmp/code-comrades-discovered-raw.txt
```

(Add `--no-gitignore` if Step 2 determined this isn't a git repo.)

`discover_files.py` prints paths relative to `{TARGET_PATH}`, not to `{REPO_ROOT}` — but every later step (the manifest, worker dispatch, the worker's own reported `file:`) uses `{REPO_ROOT}`-relative paths. Normalize this here, in one place, before anything downstream sees it:

- If `TARGET_PATH_RELATIVE` is `.` (the target is the repo root), the two frames already coincide: `cp /tmp/code-comrades-discovered-raw.txt /tmp/code-comrades-discovered.txt`.
- Otherwise, prefix every line with `{TARGET_PATH_RELATIVE}/` to convert it to a `{REPO_ROOT}`-relative path: `sed "s|^|{TARGET_PATH_RELATIVE}/|" /tmp/code-comrades-discovered-raw.txt > /tmp/code-comrades-discovered.txt`.

From here on, every file path this skill's instructions touch — the manifest's keys, the chunk lists, the paths given to workers, the paths workers report back — is this same `{REPO_ROOT}`-relative form. Never mix it with a `{TARGET_PATH}`-relative form.

Read `/tmp/code-comrades-discovered.txt`. If empty, report "0 files matched — nothing to do" and stop. Do not create a manifest.

## 4. Dry-run exit

If `--dry-run` was passed: print the full file list and its count, then stop. No manifest, no worker dispatch.

## 5. Confirm

Let `N` = the number of files that will actually be processed this session (the discovered count, capped by `--max-files` if given).

Check the tree: `git -C "{REPO_ROOT}" status --porcelain`. Non-empty output means the tree is dirty.

If `N > 10`:
- Dirty tree: ask "About to process {N} files with {SKILL_NAME}. Your working tree has uncommitted changes — a failed edit partway through a batch run can be hard to distinguish from your existing changes. Proceed anyway? [y/N]"
- Clean tree: ask "About to process {N} files with {SKILL_NAME}. Continue? [y/N]"

If declined, stop.

## 6. Manifest check

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" init \
  --repo-root "{REPO_ROOT}" \
  --skill "{SKILL_NAME}" \
  --target-path "{TARGET_PATH_RELATIVE}" \
  --config-json '{resolved config as JSON}' \
  --files-file /tmp/code-comrades-discovered.txt
```

This prints `{"manifest_path": ..., "created": bool, "summary": {...}}`.

If `created` is `false` and the summary shows any `pending` or `error` files: ask "Found an existing run with {done+skipped count} files already complete and {pending+error count} remaining. Resume (skip completed files) or start fresh? [resume/fresh]". If "fresh", re-run the same `init` command with `--fresh` appended, which resets every file to `pending`.

## 6.5. Verification setup

Determine whether verification is active for this run:

1. If `--no-verify` was passed, verification is inactive. Skip to Step 7.
2. If `batch.yaml` has no `verify:` section, or `verify.enabled` is `false`, verification is inactive. Skip to Step 7.
3. Resolve `VERIFY_CONFIG_PATH` as `{REPO_ROOT}/{verify.gate_config}` (default `.comrades/verify.json` if `gate_config` is omitted). If that file doesn't exist, print a warning — "verify.gate_config points to a missing file; running without verification" — and treat verification as inactive. Skip to Step 7.
4. Otherwise verification is active. Set:
   - `VERIFY_MODE` = `verify.mode` (default `per_file`)
   - `CHECKPOINT_EVERY` = `verify.checkpoint_every` (default `25`, only relevant if `VERIFY_MODE` is `checkpoint`)
   - `ABORT_THRESHOLD` = `verify.abort_threshold` (default `10`)
   - `RUN_ID` = a fresh identifier for this run, e.g. `date +%s` combined with a short random suffix, or any string unique enough not to collide with a prior run's `.comrades/runs/<id>/` directory
5. Call once, before dispatching any worker:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" baseline --run "{RUN_ID}"
```

   Exit `3` means the project-scoped gate itself is broken (missing binary, bad command) — stop the entire run here and report the error clearly, before any file has been touched or any worker dispatched. Exit `0` means the baseline was recorded (whether the gate passed or failed against the pristine tree) — continue to Step 7 either way.

## 7. Chunked dispatch

Track two counters across the whole loop, starting at 0: `files_attempted` (every file a chunk records a result for, regardless of outcome) and `consecutive_verify_failures` (resets to 0 on any kept/skipped/unchanged file, increments on any reverted file).

Loop until no files remain:

1. `python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" pending --manifest-path "{manifest_path}" --max {min(max_parallel from batch.yaml, remaining --max-files budget if set)}` — prints this chunk's files, one per line.
2. If empty, exit the loop.
3. **If verification is active:** for each file in the chunk, call `verify.py begin` before that file's worker is dispatched:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" begin --run "{RUN_ID}" --file "{file}"
```

   Exit `3` means the gate is broken — stop the entire run immediately (do not dispatch any worker in this chunk), and report which file's `begin` call failed. Exit `0` means the file is snapshotted; continue. Do this for every file in the chunk before moving to step 4 — the snapshot must exist before any worker can edit the file.
4. Dispatch one `Agent` tool call per file in this chunk, **all in a single message** (parallel — per standard practice for independent work), with `subagent_type` set to the identifier confirmed in `agents/batch-file-worker.md`'s header comment (`code-comrades:batch-file-worker` unless that file records a different confirmed value). Each dispatch's `prompt` must state: the file path relative to `{REPO_ROOT}`, the skill name `{SKILL_NAME}`, and the resolved config as JSON, instructing the worker to invoke that skill via the Skill tool on that file and report its outcome per its own instructions.
5. From each worker's response, extract its reported status/changed/reason/summary (the worker states these per `agents/batch-file-worker.md`'s contract). For any dispatched file with no extractable outcome, treat it as `status: error, changed: false, reason: "worker produced no result block"`.
6. **If verification is active and `VERIFY_MODE` is `per_file`:** for each file the worker reported `changed: true` for, call `check`:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" check --run "{RUN_ID}" --file "{file}"
```

   Exit `0` — kept, unchanged, or skipped (unverifiable against a pre-existing failure). Leave the worker's reported status as-is. Exit `1` — reverted; override this file's eventual manifest status to `reverted` regardless of what the worker reported, and increment `consecutive_verify_failures`. Exit `2` — bug (called without `begin`); treat as an `error` status and investigate, this should never happen if step 3 ran for every file. Any exit `0` here resets `consecutive_verify_failures` to 0.

   **If verification is active and `VERIFY_MODE` is `checkpoint`:** for each file the worker reported `changed: true` for, call `stage` instead:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" stage --run "{RUN_ID}" --file "{file}"
```

   Exit `0` — banked (staged for the next checkpoint), unchanged, or skipped. Exit `1` — reverted at stage time already (a cheap file-scoped gate caught it, or the project baseline was already broken with `on_baseline_fail: skip`); override this file's manifest status to `reverted`. Exit `2` — bug, same as above. A `stage` exit of `0` does **not** mean the file is permanently kept — that's only known once a `checkpoint` call resolves it (see next bullet). Then, if `files_attempted` (including this chunk) has reached a multiple of `CHECKPOINT_EVERY`, call:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" checkpoint --run "{RUN_ID}"
```

   Exit `0` — every staged file in this window passed and is kept. Exit `1` — the reported JSON's `culprits` list gives the files that were reverted; override those files' manifest status to `reverted`. Files in the reported `kept` list keep whatever status the worker originally reported.
7. **If verification is active:** if `consecutive_verify_failures >= ABORT_THRESHOLD`, stop the loop after this chunk's results are recorded in the next step — do not dispatch another chunk. Report that the run was aborted due to repeated verification failures — this is a signal the gate itself may be misconfigured for this skill, not that the files are actually broken.
8. Write this chunk's file list to `/tmp/code-comrades-batch-files.txt` (one per line) and its results to `/tmp/code-comrades-batch-results.json` (a JSON array of `{file, status, changed, reason, summary}`, with any verification overrides from step 6 applied), then:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" record \
  --manifest-path "{manifest_path}" \
  --dispatched-files-file /tmp/code-comrades-batch-files.txt \
  --results-file /tmp/code-comrades-batch-results.json
```

This saves the manifest to disk immediately after every chunk, not just at the end of the run. **Note for `checkpoint` mode:** a file whose `stage` call returned `0` but hasn't yet gone through a `checkpoint` call doesn't have a final verdict yet. Record it with its worker-reported status for now; if a later `checkpoint` call (this chunk's or a subsequent one) puts it in `culprits`, that file's manifest entry must be corrected to `reverted` at that point — don't treat the first `record` call as final for staged-but-unresolved files.
9. Increment `files_attempted` by the number of files this chunk recorded a result for (every dispatched file counts, whether kept, reverted, or skipped — this is what `--max-files` is measured against, not just successes).

After the loop exits (whether because no files remained, `--max-files` was reached, or the abort threshold tripped), **if verification is active and `VERIFY_MODE` is `checkpoint`**, call `checkpoint` once more unconditionally to flush any staged files left under the `CHECKPOINT_EVERY` threshold:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" checkpoint --run "{RUN_ID}"
```

This is a no-op (prints `{"checkpoint": "empty"}`, exits 0) if nothing was left staged. If it reports culprits, correct those files' manifest entries to `reverted` the same way step 6 does, via another `manifest.py record` call.

## 8. Report

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" summary --manifest-path "{manifest_path}"
```

Present its JSON as a plain-language summary: counts by status (including `reverted`, if verification was active), total files changed, and any `error` entries with their reasons (look these up from the manifest file directly, since `summary` only gives counts).

**If verification was active**, also run:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/verify.py" --root "{REPO_ROOT}" --config "{VERIFY_CONFIG_PATH}" report --run "{RUN_ID}"
```

and fold its `tally` (counts by decision: `keep`/`reverted`/`skipped`) and `failures` (file, failing gate name, output tail) into the summary presented to the user. If verification was inactive for this run, the report looks identical to a pre-verification run — no mention of verification at all.
