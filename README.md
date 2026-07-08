# Code Comrades

A [Claude Code](https://claude.com/claude-code) plugin that applies different skills across an entire folder or repository — not just one file at a time.

Ask Claude to comment a single file and it does a great job. Ask it to comment 200 files across a real codebase, and you're stuck babysitting file-by-file requests.

Code Comrades closes that gap: point it at a directory, and it discovers the right files, dispatches a scoped worker per file, tracks progress so an interrupted run can resume, and reports back a clean summary — all through one command.

The plugin ships with one skill today, [`code-commenter`](skills/code-commenter/SKILL.md), which adds language-idiomatic comments and docstrings. Its batch-runner is deliberately generic: any skill that follows the same small contract (a `SKILL.md` plus a `batch.yaml` metadata file) can be run the same way, with no changes to the runner itself.

> **Note:** this is a *Claude Code* plugin, distinct from the general Claude Desktop chat app. It only works with Claude Code (CLI, IDE extensions, or the Claude Code desktop app) — see [`USAGE.md`](USAGE.md) for details if that distinction matters to your setup.

## Features

- **Batch execution over a whole codebase** — run any batchable skill across a folder or repository in one command, not one file at a time.
- **Skill-agnostic runner** — adding a new batchable skill requires no changes to the orchestrator; see [Extending](#extending-add-a-new-skill).
- **Safe by default** — dry-run preview, a confirmation prompt above a file-count threshold, and a dirty-tree warning before any edits begin.
- **Resumable** — progress is saved after every chunk of files, so an interrupted run picks up where it left off instead of starting over or reprocessing everything.
- **Idempotent** — re-running against an already-processed codebase is a safe no-op, not a source of duplicate edits.
- **Scoped workers** — each file is handled by a subagent restricted to Read/Edit/Write/Skill tools only (no shell access, no further sub-dispatch), bounding the blast radius of a batch run to the files it's actually meant to touch.

## Requirements

- The [Claude Code](https://claude.com/claude-code) CLI, installed and authenticated.
- Python 3.8+ on `PATH` — the plugin's bundled scripts have no runtime dependencies beyond the standard library.
- Git (recommended) — enables `.gitignore`-aware file discovery, a dirty-tree warning before batch runs, and lets you review every change as a diff afterward.

## Installation

```bash
cd path/to/code-comrades
claude plugin marketplace add ./ --scope user
claude plugin install code-comrades@code-comrades-marketplace --scope user
```

This registers the repo as a local marketplace source and installs from it — necessary because an unpublished plugin can't be installed directly by path. Verify it loaded:

```bash
claude plugin list
```

For a quick, one-off try without a persistent install, or full detail on both install paths (including a couple of real gotchas worth knowing before you rely on this — cache behavior for local-directory sources, and a stale-session issue), see **[`USAGE.md`](USAGE.md)**.

## Quick start

From inside the project you want commented:

```bash
claude
```

Then, in the session, preview before touching anything:

```
/code-comrades:dispatch code-commenter . --dry-run
```

Review the file list, then run for real:

```
/code-comrades:dispatch code-commenter .
```

You'll see normal Claude Code permission prompts as each file is read and edited — on a large run, expect one per file. Approve them as you would any other edit; the dispatch flow itself doesn't batch-approve, though Claude Code's own `--permission-mode` session flags (e.g. `acceptEdits`) can reduce prompt friction if you're comfortable with that tradeoff. When it's done, review the result the same way you'd review any change:

```bash
git diff
```

<details>
<summary>Example output</summary>

Before:
```python
def parse_csv(spec):
    if not spec:
        return []
    return [p.strip() for p in spec.split(",") if p.strip()]
```

After:
```python
def parse_csv(spec):
    """Split a comma-separated string into trimmed, non-empty tokens.

    Args:
        spec: Comma-separated string, or falsy for an empty result.

    Returns:
        List of trimmed tokens with empty entries dropped.
    """
    if not spec:
        return []
    return [p.strip() for p in spec.split(",") if p.strip()]
```

Real output from this plugin's own dogfood run against `scripts/discover_files.py`.
</details>

## Usage

```
/code-comrades:dispatch <skill-name> <path> [--dry-run] [--config key=value,...] [--max-files N]
```

| Flag | Purpose |
|---|---|
| `--dry-run` | List matching files without changing anything. |
| `--config key=value,...` | Override the skill's default config for this run, e.g. `--config audience=junior`. |
| `--max-files N` | Cap how many files are processed this session — useful for a first trial run on a large repo. |

Common examples:

```
/code-comrades:dispatch code-commenter src/                        # a subdirectory, not the whole repo
/code-comrades:dispatch code-commenter . --max-files 5              # a small trial run
/code-comrades:dispatch code-commenter . --config audience=junior   # more explanatory comments
```

Batch runs create a `.claude-batch-manifest/` directory in the target repo's root for resumable progress tracking. Add it to that repo's `.gitignore`.

Full walkthrough, including what happens step by step during a run and how resume works: **[`USAGE.md`](USAGE.md)**.

## Included skills

| Skill | What it does |
|---|---|
| [`code-commenter`](skills/code-commenter/SKILL.md) | Adds language-idiomatic comments and docstrings to source files across ~20 languages. Calibrates depth to audience (`junior`/`senior`/`api-consumer`) and code complexity, skips trivial code, never touches logic or formatting, and is idempotent — safe to re-run. |

## How it works

1. **`dispatch`** (`skills/dispatch/SKILL.md`) is the orchestrator. It reads the target skill's `batch.yaml`, discovers matching files via a bundled Python script, and manages a resumable manifest.
2. Files are processed in parallel chunks (default 5, configurable per skill via `batch.yaml`'s `max_parallel`). Each file is handed to a fresh **`batch-file-worker`** subagent (`agents/batch-file-worker.md`), scoped to Read/Edit/Write/Skill tools only.
3. The worker invokes the target skill (e.g. `code-commenter`) on its one file, applies the edit directly, and reports a status back to the orchestrator.
4. Progress is written to the manifest after every chunk — an interruption loses at most one chunk's worth of work, and a re-run can resume rather than restart.

## Extending: add a new skill

A skill becomes batchable by adding two files next to its existing `SKILL.md`:

- A `batch.yaml` declaring which file extensions it applies to, its default config, and how many files it can process in parallel — see [`skills/code-commenter/batch.yaml`](skills/code-commenter/batch.yaml) for a working example.
- A "batch mode" section in the skill's own `SKILL.md` describing how it behaves when it has live file-editing access, as opposed to its normal conversational behavior — see `code-commenter`'s [`SKILL.md`](skills/code-commenter/SKILL.md) for the pattern to follow.

That's the whole contract. `/code-comrades:dispatch <new-skill> <path>` works immediately once both files exist.

## Development

```bash
claude plugin validate . --strict   # validate the manifest and every component's frontmatter
python -m pytest scripts/           # run the bundled scripts' test suite
claude --plugin-dir .               # load the plugin for one session without installing
```

## Known limitations

- **Safety is prompt-constrained, not sandboxed.** Worker subagents are restricted to Read/Edit/Write/Skill tools, but that's an instruction-level boundary enforced by Claude Code's permission system, not an OS-level sandbox. Run against a clean git tree and review `git diff` before committing, so any unexpected edit is easy to spot and revert.
- **Non-deterministic judgment on borderline cases.** Whether a simple function counts as "trivial" (skip) or "worth a docstring" (comment) is an LLM judgment call and can vary between runs on the same code. This doesn't affect correctness, but don't expect byte-identical output across repeated runs on undocumented code.
- **No rollback beyond git.** There's no built-in undo; recovery is `git checkout`/`git diff` like any other change.

## Testing and validation

- **Automated:** `scripts/discover_files.py` and `scripts/manifest.py` — the plugin's deterministic layers — have real tests against actual filesystem behavior. `claude plugin validate . --strict` checks the manifest and every component's frontmatter structurally.
- **Live validation:** the full dispatch flow has been run against real code, including the subdirectory-target case (`path` other than the repo root), with results verified against on-disk artifacts rather than trusted from a transcript.
- **Known gaps:** `--config` overrides and resume-after-interruption are covered by manual test scenarios, not live-validated end to end yet.

## License

[MIT](LICENSE)
