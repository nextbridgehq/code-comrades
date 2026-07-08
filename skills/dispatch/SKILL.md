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

## 7. Chunked dispatch

Loop until no files remain:

1. `python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" pending --manifest-path "{manifest_path}" --max {min(max_parallel from batch.yaml, remaining --max-files budget if set)}` — prints this chunk's files, one per line.
2. If empty, exit the loop.
3. Dispatch one `Agent` tool call per file in this chunk, **all in a single message** (parallel — per standard practice for independent work), with `subagent_type` set to the identifier confirmed in `agents/batch-file-worker.md`'s header comment (`code-comrades:batch-file-worker` unless that file records a different confirmed value). Each dispatch's `prompt` must state: the file path relative to `{REPO_ROOT}`, the skill name `{SKILL_NAME}`, and the resolved config as JSON, instructing the worker to invoke that skill via the Skill tool on that file and report its outcome per its own instructions.
4. From each worker's response, extract its reported status/changed/reason/summary (the worker states these per `agents/batch-file-worker.md`'s contract). For any dispatched file with no extractable outcome, treat it as `status: error, changed: false, reason: "worker produced no result block"`.
5. Write this chunk's file list to `/tmp/code-comrades-batch-files.txt` (one per line) and its results to `/tmp/code-comrades-batch-results.json` (a JSON array of `{file, status, changed, reason, summary}`), then:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" record \
  --manifest-path "{manifest_path}" \
  --dispatched-files-file /tmp/code-comrades-batch-files.txt \
  --results-file /tmp/code-comrades-batch-results.json
```

This saves the manifest to disk immediately after every chunk, not just at the end of the run.

## 8. Report

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/manifest.py" summary --manifest-path "{manifest_path}"
```

Present its JSON as a plain-language summary: counts by status, total files changed, and any `error` entries with their reasons (look these up from the manifest file directly, since `summary` only gives counts).
