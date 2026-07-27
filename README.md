# Code Comrades

*Repository-wide skills for Claude Code, under two execution models: a skill-agnostic batch runner and whole-repository project skills.*

![Version](https://img.shields.io/badge/version-0.2.0-lightgrey)
![Plugin Type](https://img.shields.io/badge/type-Claude%20Code%20Plugin-blue)
![Status](https://img.shields.io/badge/status-Active-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green.svg)

[GitHub](https://github.com/nextbridgehq/code-comrades) · [Issues](https://github.com/nextbridgehq/code-comrades/issues) · [Releases](https://github.com/nextbridgehq/code-comrades/releases) · [Changelog](CHANGELOG.md)

Maintained by [Nextbridge](https://nextbridge.com)

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Installation](#installation)
- [Usage / Quick Start](#usage--quick-start)
- [Configuration](#configuration)
- [Architecture / How it works](#architecture--how-it-works)
- [Known Limitations / Caveats](#known-limitations--caveats)
- [Contributing](#contributing)
- [Changelog](#changelog)
- [Keywords](#keywords)
- [License](#license)

---

## Overview

A [Claude Code](https://claude.com/claude-code) plugin that applies skills across an entire folder or repository — not just one file at a time — under two execution models, chosen by what the task actually needs.

Ask Claude to comment a single file and it does a great job. Ask it to comment 200 files across a real codebase, and you're stuck babysitting file-by-file requests. `code-comrades`'s **batch skills** close that gap: point one at a directory, and it discovers the right files, dispatches a scoped worker per file, tracks progress so an interrupted run can resume, and reports back a clean summary — all through one command.

Other tasks don't fit that shape at all. Generating a README, or extracting changelog fragments from git history, means understanding the whole repository at once and producing a single new artifact — not editing 200 files independently. `code-comrades`'s **project skills** handle that: one deterministic pass over the repository's facts, one round of judgment, one artifact written — no file discovery, no manifest, no worker pool, because none of that machinery would help here.

## Features

- **Batch execution over a whole codebase** — run any batchable skill across a folder or repository in one command.
- **Two execution models** — per-file *batch skills* (`code-commenter`, `type-annotator`, `license-header-injector`, `error-handling-auditor`, `import-sorter-cleaner`, `test-stub-generator`) and repository-wide *project skills* (`readme-master`, `changelog-fragment-extractor`, `readme-per-folder`).
- **Incremental (git-scoped)** — with `--changed`, `--staged`, or `--since <ref>`, a run covers only what git says you touched.
- **Resumable** — progress is saved to `.claude-batch-manifest/` after every chunk of files, so an interrupted run picks up where it left off instead of starting over.
- **Pipelines** — chain several skills over the same path in one command with one confirmation (e.g., `dispatch pipeline type-annotator,code-commenter src/`).
- **Scoped workers** — each file is handled by a subagent restricted to Read/Edit/Write/Skill tools only, bounding the blast radius.
- **Repository intelligence** — a deterministic facts script (`scripts/inspect_repo.py`) provides every project skill the same grounded view of the repo's manifests and documentation.
- **Optional per-file verification** — gate every edit behind a deterministic command (linter, typecheck, test suite) and auto-revert on regression.

## Available Skills

- **`code-commenter`** (Batch): Generates high-quality, idiomatic comments and docstrings. Calibrates to codebase complexity and audience level.
- **`type-annotator`** (Batch): Seamlessly adds PEP 484/585 Python type hints and JSDoc tags using inference without replacing existing annotations.
- **`license-header-injector`** (Batch): Uniformly inserts configurable SPDX + copyright headers across files while safely avoiding duplicates.
- **`error-handling-auditor`** (Batch): Flags unguarded async calls, unchecked errors, and missing null checks with review markers; audit-only by default, guard injection is opt-in.
- **`import-sorter-cleaner`** (Batch): Sorts and deduplicates imports, removing only provably-unused ones — never touches side-effect-only imports.
- **`test-stub-generator`** (Batch): Generates a starter test file per source file lacking one, with real calls and placeholder assertions — never overwrites an existing test file.
- **`readme-master`** (Project): Automates README generation and smartly synchronizes project documentation based on deterministic repository facts and structure.
- **`changelog-fragment-extractor`** (Project): Extracts one changelog fragment per commit-worthy change since the last tag, from conventional-commit prefixes and `TODO changelog:` markers.
- **`readme-per-folder`** (Project): Generates a short README.md for each immediate subdirectory of a target path that lacks one.

## Installation

As a Claude Code plugin, `code-comrades` is installed directly into your environment using the `claude` CLI. Python 3.8+ must be on your `PATH`.

**Persistent Install via Marketplace (Recommended)**
This registers the repo as a local marketplace source, making it available in any directory:
```bash
cd code-comrades/
claude plugin marketplace add ./ --scope user
claude plugin install code-comrades@code-comrades-marketplace --scope user
```
*(Do not move or delete the repository folder after installing this way).*

**One-off Session Testing**
Active for one session only:
```bash
claude --plugin-dir path/to/code-comrades
```

## Usage / Quick Start

Inside a Claude Code session (`claude`), you can dispatch a skill across an entire repository or a specific directory.

**Always dry-run first to review targeted files:**
```bash
/code-comrades:dispatch code-commenter . --dry-run
```

**Run for real against your active git changes:**
```bash
/code-comrades:dispatch type-annotator . --changed
```

**Run a pipeline of skills (order matters):**
```bash
/code-comrades:dispatch pipeline type-annotator,code-commenter src/ --since main
```

**Run a Project-Level Skill (e.g. readme-master):**
```bash
/code-comrades:dispatch readme-master .
```

*For an in-depth walkthrough on pipelines, reports, and verification, see [USAGE.md](USAGE.md).*

## Configuration

You can override a skill's default configuration at runtime using the `--config` flag, or adjust the dispatch scope with runtime flags:

| Flag / Setting | Description |
|---|---|
| `--dry-run` | List matching files without changing anything. |
| `--max-files N` | Cap how many files are processed this session (useful for large trial runs). |
| `--changed`, `--staged`, `--since <ref>` | Incremental git-scoping flags to narrow the run to recent changes. |
| `--no-verify` | Skip the verification gate for this run. |
| `--config audience=...` | Set to `junior`, `senior`, or `api-consumer` for `code-commenter`. |
| `--config style.python=...` | Set docstring style (`google`, `numpy`) for `code-commenter`. |
| `--config add_guards=true` | Opt in to guard injection for `error-handling-auditor` (default is audit-only — markers, no behavior change). |

## Architecture / How it works

**Batch skills** (`code-commenter`, `type-annotator`, `license-header-injector`, `error-handling-auditor`, `import-sorter-cleaner`, `test-stub-generator`) — declared via `batch.yaml`, one file in and one file edited out:

1. **`dispatch`** (`skills/dispatch/SKILL.md`) is the orchestrator. It reads the target skill's config, discovers matching files via a Python script, and manages a resumable manifest.
2. Files are processed in parallel chunks. Each file is handed to a fresh `batch-file-worker` subagent scoped to minimal tools.
3. The worker applies the edit directly.
4. Progress is written to the manifest (`.claude-batch-manifest/`) after every chunk.
5. If verified batching is enabled, each edit is snapshotted, gated by a verification command, and reverted automatically on regression.

**Project skills** (`readme-master`, `changelog-fragment-extractor`, `readme-per-folder`) — declared via `project.yaml`, one pass over the whole repository producing declared artifacts:

1. Invoked directly and conversationally (e.g. "generate a README for this repo"), or via `/code-comrades:dispatch <skill> <path>`, which recognizes the `project.yaml` contract and hands off — no discovery, no manifest, no worker pool, so batch-only flags (`--changed`, `--max-files`, `--no-verify`, resume) don't apply.
2. The skill runs its declared `inspect` script once — `scripts/inspect_repo.py` for every project skill today — a deterministic, network-free walk producing a JSON facts sheet (project identity, languages, manifests, docs, CI, git remote, and more).
3. The skill reads the facts sheet plus whatever source files/documentation the facts point to, then does the judgment a batch worker never does: picking a mode (e.g. `generate`/`improve`/`sync` for `readme-master`), synthesizing across the whole repo rather than one file at a time.
4. It writes the artifact(s) declared in its `produces` allowlist directly — a single file (`README.md`), a directory of generated files (`changelog-fragment-extractor`'s `changelog/fragments/`), or a one-level glob (`readme-per-folder`'s `*/README.md`).
5. There's no manifest or verification gate here — a run is one pass, and review is a plain `git diff`/`git status` against the skill's declared `produces` path. See [`docs/project-skills.md`](docs/project-skills.md) for the full contract.

## Known Limitations / Caveats

- **Safety is prompt-constrained, not sandboxed.** Worker subagents are restricted by Claude Code's permission system, not an OS-level sandbox. Run against a clean git tree and review `git diff` before committing.
- **Non-deterministic judgment.** Borderline cases (e.g. trivial vs worth a docstring) are LLM judgment calls and can vary between runs.
- **No rollback beyond git.** There is no built-in undo. Recovery is achieved via `git checkout`.
- **Incremental runs don't resume.** Incremental mode (`--changed`, etc.) re-scopes from git each invocation. Interrupted runs in this mode restart rather than picking up mid-set.

## Contributing

1. Fork and clone the repository.
2. Create a feature branch.
3. Validate plugin structure and test the python scripts locally:
   ```bash
   claude plugin validate . --strict
   python -m pytest scripts/
   ```
4. Push to your branch and submit a Pull Request.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). 

## Keywords

`batch` `codebase-wide` `code-comments` `documentation` `type-annotations` `license-headers` `pipeline` `incremental` `verification` `plugin-ecosystem` `project-skills` `readme` `repository-intelligence` `error-handling` `import-sorting` `test-generation` `changelog`

## License

[MIT](LICENSE) © [Nextbridge](https://nextbridge.com)

Built and maintained by **[Nextbridge](https://nextbridge.com)** — If Code Comrades helped you, a ⭐ would be much appreciated — it helps other developers discover the project too.
