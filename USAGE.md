# Using code-comrades

A step-by-step guide to loading this plugin and running it against a real project.

This is a Claude Code plugin, not a Claude Desktop feature — it only works with the `claude` CLI (or the VS Code/JetBrains extensions and Claude Code desktop app, which all use the same engine). Commands below assume `claude` is installed and authenticated.

## Prerequisites

- `claude` CLI installed and authenticated.
- Python 3.8+ on `PATH` (the plugin's bundled scripts, `discover_files.py` and `manifest.py`, are plain stdlib Python — no `pip install` needed to *run* the plugin; `pytest` is only needed if you want to re-run the plugin's own test suite).
- Git, recommended: enables `.gitignore`-aware file discovery, the dirty-tree warning, and lets you review every change as a diff afterward.
- The project you want commented should ideally have a clean git working tree before you start, so any change the plugin makes is trivially visible in `git diff` and revertable with `git checkout -- <file>` if something goes wrong.

## Step 1 — Load the plugin

There's no "install from a local folder" one-liner — `claude plugin install` only installs from a registered marketplace, and a marketplace needs its own `.claude-plugin/marketplace.json` (a different file from `.claude-plugin/plugin.json`). This repo has both, so there are two ways to load it.

### Option A — Persistent install via the marketplace (recommended)

Shows up in `claude plugin list`, survives without any flag, and is available from **any** directory you start `claude` in — not just this repo.

```bash
cd path/to/code-comrades
claude plugin marketplace add ./ --scope user
claude plugin install code-comrades@code-comrades-marketplace --scope user
```

`--scope user` matters — it's what makes the plugin available everywhere, not just when you happen to be in this repo's directory (`--scope local` or `--scope project` would tie it to wherever you ran the install from).

Because the marketplace source is a local directory rather than a git URL, the components that load at runtime (`$CLAUDE_PLUGIN_ROOT`) reference this repo's path directly, the same way `--plugin-dir` does. **Don't move or delete this repo** after installing — if you need to relocate it, uninstall first, move the folder, then reinstall.

The plugin is version-pinned (`plugin.json`'s `version` field). Claude Code only refreshes the cache when that string changes, so pushing commits without bumping it has no effect and `claude plugin update` will report "already at the latest version." If you're developing the plugin, bump `version` on each change, or just re-run the install commands above.

To remove it later:
```bash
claude plugin uninstall code-comrades@code-comrades-marketplace --scope user
claude plugin marketplace remove code-comrades-marketplace
```

### Option B — `--plugin-dir` (quick, one-off testing)

No install step, active for one session only:

```bash
claude --plugin-dir path/to/code-comrades
```

To avoid retyping the flag, add a shell alias/function.

Bash / git-bash (`~/.bashrc` or `~/.bash_profile`):
```bash
alias claude-comrades='claude --plugin-dir path/to/code-comrades'
```

PowerShell (`$PROFILE`):
```powershell
function claude-comrades { claude --plugin-dir path/to/code-comrades @args }
```

Sessions don't hot-reload plugins. If you install or update while a session is running, restart it (or run `/reload-plugins`) before invoking the command — otherwise you'll get "Unknown command" even though the install succeeded.

## Step 2 — Verify it loaded

If you used **Option A** (marketplace install):
```bash
claude plugin list
```
should show `code-comrades@code-comrades-marketplace`, scope `user`, status enabled. You can also inspect exactly what it contributes:
```bash
claude plugin details code-comrades@code-comrades-marketplace
```
which lists its components: skills `code-commenter`, `type-annotator`, `license-header-injector`, `error-handling-auditor`, `import-sorter-cleaner`, `test-stub-generator`, `readme-master`, `changelog-fragment-extractor`, `readme-per-folder`, and `dispatch`; agent `batch-file-worker`.

If you used **Option B** (`--plugin-dir`): `/plugin list` does **not** show session-loaded plugins (only marketplace installs) — that's expected, not a failure. Instead, verify with a dry-run against any path once the session is open:
```
/code-comrades:dispatch code-commenter . --dry-run
```
If the skill responds (even with "0 files matched"), it loaded correctly.

Either way, you can sanity-check the plugin itself before loading it anywhere:
```bash
cd path/to/code-comrades
claude plugin validate . --strict
```
Should print `✔ Validation passed`.

## Step 3 — Run it against a project

Go to the project you want commented and start a session:

```bash
cd your-project/
claude
```

If you used **Option A** (marketplace install), that's it — the plugin loads automatically in every session. If you used **Option B** (`--plugin-dir`), you still need the flag every time: `claude --plugin-dir path/to/code-comrades`.

Then, inside the session:

### 3a. Always dry-run first

```
/code-comrades:dispatch code-commenter . --dry-run
```

This lists every file that would be touched — nothing is written. Check the list makes sense (right files included, generated/vendored code excluded) before doing a real run.

### 3b. Run for real

```
/code-comrades:dispatch code-commenter .
```

What happens:
- If more than 10 files matched, it asks you to confirm before doing anything (and separately warns if your git tree is dirty).
- It dispatches one scoped subagent per file, several in parallel per chunk. Each subagent only has Read/Edit/Write/Skill access — no shell, no further sub-dispatch — and edits its one file directly.
- You'll see normal Claude Code permission prompts as each file gets read/edited — approve them as you would for any other edit.
- Progress is saved to `.claude-batch-manifest/` in your project's repo root after every chunk, so an interruption only loses at most one chunk's worth of work.
- At the end you get a summary: how many files were changed, how many were already fully documented (skipped as no-op), and any errors with reasons.

### 3c. Review before committing

```bash
git diff
```

Read through the added comments like you would any other change before committing. The skill never touches logic, formatting, or variable names — only comments — but review it anyway.

### 3d. Re-running is safe

Running the same command again just confirms everything's already documented (`unchanged`/no-op) rather than duplicating comments — the skill is idempotent by design. If a previous run was interrupted, re-running offers to **resume** (skip completed files, retry only what didn't finish) or **start fresh**.

### 3e. Useful variations

Only a subdirectory:
```
/code-comrades:dispatch code-commenter src/
```

Cap how many files get processed this session (handy for a first trial run on a large repo):
```
/code-comrades:dispatch code-commenter . --max-files 5
```

Override the default config — e.g. more explanatory comments for a codebase new hires will read:
```
/code-comrades:dispatch code-commenter . --config audience=junior
```

Other config keys you can override this way: `audience` (`junior`/`senior`/`api-consumer`), `style.python` (`google`/`numpy`), `style.max_docstring_lines`, `style.include_examples`, `idempotent`.

Every batch skill has its own config keys, documented in its `SKILL.md` — the one worth knowing up front: `error-handling-auditor` defaults to `add_guards: false` (audit-only, review markers no behavior change); pass `--config add_guards=true` to opt into actual try/catch injection.

### 3f. Only what you changed (incremental)

Running over a whole repo is a big first step. Usually you want just your own work:

```
/code-comrades:dispatch type-annotator . --changed      # uncommitted work: staged, unstaged, untracked
/code-comrades:dispatch type-annotator . --staged        # the index only — pre-commit scope
/code-comrades:dispatch code-commenter . --since main    # everything on this branch, committed or not
```

This is the mode to reach for day to day. `--staged` is what you'd wire into a pre-commit hook; `--since main` matches what a reviewer sees in your PR.

Notes worth knowing:

- If the scope can't be resolved (typo'd ref, not a git repo, git missing), the run **stops** with an error. It will not fall back to processing everything — a flag that quietly widens its own scope is how a repo gets rewritten by accident.
- Incremental runs re-scope from git each time rather than resuming. Interrupted ones restart; since the sets are small and the skills idempotent, this costs little.
- After a `--changed` run, the files it edited are still "changed" to git until you commit. Re-running `--changed` will reprocess them and report no changes.

### 3g. Chaining skills (pipelines)

```
/code-comrades:dispatch pipeline type-annotator,code-commenter src/ --changed
```

One confirmation, one combined summary, skills run left to right. Order matters — annotate types **before** commenting, so docstrings don't restate types the annotator makes redundant; put `license-header-injector` last. Dispatch will flag an order that produces a worse result before it starts.

Each skill in the pipeline writes its own report as it finishes, so if the pipeline stops partway you still have a record of what completed.

### 3h. The run report

Every run writes a markdown report next to its manifest:

```
.claude-batch-manifest/type-annotator-<key>-report.md
```

It lists what changed (with per-file summaries), what errored grouped by reason, what verification reverted and which gate failed, and what's still pending. It's generated from the manifest, so you can regenerate it any time — including for a run that was interrupted:

```bash
python scripts/manifest.py report --manifest-path .claude-batch-manifest/<skill>-<key>.json
```

Pass `--format json` for CI, or `--no-report` on the dispatch call to skip writing the file.

### 3i. Verification (optional)

If the skill's `batch.yaml` has a `verify:` section, every edit is additionally checked against a deterministic gate command and reverted automatically if it regresses. Pass `--no-verify` to skip this for one run:

```
/code-comrades:dispatch code-commenter . --no-verify
```

Full detail on gate configuration, modes, and troubleshooting: [`docs/verification.md`](docs/verification.md).

### 3j. Project skills (readme-master, changelog-fragment-extractor, readme-per-folder)

Not everything is a batch. Three skills are *project skills*: each analyzes the repository (or, for `readme-per-folder`, its immediate subdirectories) once and writes declared artifacts directly, so there's no discovery, manifest, or worker pool involved. Invoke any of them directly, conversationally:

```
Generate a README for this repo
```

```
My README is outdated — sync it with the code
```

```
Extract changelog fragments since the last release
```

```
Add a README to every subfolder that's missing one
```

**`readme-master`** runs the bundled facts script first (`scripts/inspect_repo.py`), reads the files the facts point at, and then generates, improves, or syncs — asking at most one round of questions, and only about things the repository can't answer (a demo URL, a security contact). Sync mode is the one to build a habit around: run it before a release and it patches stale claims (versions, install commands, badges, license) without touching your voice; if nothing is stale it changes nothing.

**`changelog-fragment-extractor`** reads `git log` since the last tag (or the whole history on a repo with no tags yet), classifies commits by conventional-commit prefix or an explicit `TODO changelog:` comment, and writes one fragment file per commit-worthy change to `changelog/fragments/` — never to `CHANGELOG.md` directly. Its unit of work is a commit, not a file, which is why it's a project skill rather than a batch one.

**`readme-per-folder`** writes a short `README.md` to each *immediate* (non-recursive) subdirectory of the target path that's missing one. Run it again with a nested path as the target to cover a deeper subtree — one invocation only ever covers one level.

`/code-comrades:dispatch <skill> .` also works for any of the three — dispatch recognizes the `project.yaml` contract and hands off, noting that batch flags don't apply. Project skills can't be pipeline members; run them after a pipeline instead. Details, including the `produces` write-allowlist contract each one declares: [`docs/project-skills.md`](docs/project-skills.md).

## Known limitations

See [`README.md`](README.md#known-limitations--caveats).
