# Changelog

All notable changes to code-comrades are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0]

### Added

- **Project skills — a second execution model.** Skills can now declare `execution: project` via a `project.yaml` contract: one pass over the whole repository producing declared artifacts, instead of per-file batch workers. Batch machinery deliberately does not apply. See [`docs/project-skills.md`](docs/project-skills.md).
- **`readme-master` — the first project skill.** Generates, improves, or *synchronizes* `README.md` from repository evidence. Inference-first: it asks at most one round of questions.
- **`scripts/inspect_repo.py` — the repository intelligence layer.** One deterministic, network-free walk emitting a JSON facts sheet. Byte-stable output for an unchanged tree is what project skills inherit their idempotency from. Fully tested.
- **`type-annotator` skill.** Adds Python type hints (PEP 484/585) and JSDoc type tags for plain JavaScript. Infers only from visible evidence and skips ambiguous cases.
- **`license-header-injector` skill.** Inserts a configurable SPDX + copyright header. Never duplicates an existing header. Optional year refresh via `update_existing`.
- **`error-handling-auditor` skill.** Flags unguarded async calls, unchecked errors, and missing null checks with a review marker. Audit-only by default (`add_guards: false`) — every other skill in this plugin promises to never change runtime behavior, so guard injection is opt-in only, never a default.
- **`import-sorter-cleaner` skill.** Sorts and deduplicates imports, removing only provably-unused ones. Never touches side-effect-only imports, star imports, or barrel files (`__init__.py`, `index.js`/`.ts`).
- **`test-stub-generator` skill.** Generates one new test file per source file lacking one (framework auto-detected: pytest/jest/Go testing), with real calls and placeholder assertions. Never overwrites an existing test file; never modifies the source file it was given.
- **`changelog-fragment-extractor` — a second project skill.** Extracts one changelog fragment per commit-worthy change since the last tag (conventional-commit prefixes and `TODO changelog:` markers), writing to `changelog/fragments/`. Its unit of work is a git commit, not a file — the reason it's a project skill rather than a batch one.
- **`readme-per-folder` — a third project skill.** Generates a short `README.md` for each immediate (non-recursive) subdirectory of a target path that lacks one.
- **Multi-artifact `produces`.** `project.yaml`'s `produces` field now also accepts a directory (`changelog/fragments/`) or a one-level glob (`*/README.md`), for project skills that write more than one file per run. See [`docs/project-skills.md`](docs/project-skills.md).
- **Incremental runs.** `--changed`, `--staged`, and `--since <ref>` narrow a run to what git reports you touched, instead of the whole tree. Scope resolution fails closed.
- **Pipelines.** `dispatch pipeline <skill1,skill2,...> <path>` runs several skills sequentially over one path with a single confirmation and a combined summary.
- **Optional per-file verification.** Gate each edit behind a deterministic command and auto-revert on regression, with a checkpoint/bisection mode. See [`docs/verification.md`](docs/verification.md).
- **Resumable runs and Run reports.** Progress is saved to a manifest after every chunk. Every run writes a markdown report next to its manifest, covering what changed, errored, or reverted. Regenerable via `manifest.py report`.
- **Dispatch routing.** `/code-comrades:dispatch <skill> <path>` routes automatically based on `batch.yaml` vs `project.yaml`.
- **Structural skill validation.** `scripts/test_skills_structure.py` automatically validates all skills to ensure their contracts are structurally sound.
- `--max-files` cap for trial runs.
- Edge-case test coverage: unicode filenames, symlinked files/directories, nested git repos, hidden directories, deep nesting.

### Changed

- `discover_files.py` gained `--git-scope` and `--since-ref`. Default behavior is unchanged when they're omitted.
- Plugin metadata enriched with homepage, repository, license, and keywords for marketplace discoverability.

### Fixed

- `--since <ref>` missed untracked files. `git diff` only reports tracked paths, so a newly created, unstaged file is now correctly included.

### Notes

- Fully backward compatible: existing manifests, resume state, and flags behave identically.
- Incremental runs re-scope from git on every invocation rather than resuming. Interrupted incremental runs restart.

## [0.1.0]

### Added

- Initial release: skill-agnostic batch runner (`dispatch`) with scoped per-file workers, dry-run preview, confirmation prompts, and the `code-commenter` skill.
