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
which lists its components: skills `code-commenter` and `dispatch`, agent `batch-file-worker`.

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

### 3f. Verification (optional)

If the skill's `batch.yaml` has a `verify:` section, every edit is additionally checked against a deterministic gate command and reverted automatically if it regresses. Pass `--no-verify` to skip this for one run:

```
/code-comrades:dispatch code-commenter . --no-verify
```

Full detail on gate configuration, modes, and troubleshooting: [`docs/verification.md`](docs/verification.md).

## Known limitations

See [`README.md`](README.md#known-limitations) and [`README.md`](README.md#testing-and-validation).
