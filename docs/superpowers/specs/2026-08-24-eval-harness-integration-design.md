# Eval Harness Integration Design (v2)

## Overview
Integrate the evaluation harness, test corpus, and iteration-loop tooling into the `code-comrades` repository as version `0.3.0`, committed to a local `auto-improve` branch. No remote push, no PR yet — this stays local until reviewed.

## Pre-flight
- `git status --short` (must be clean; abort if not)
- `git checkout main && git pull` (stay updated) — if `git pull` shows new commits on main, rebase `feature/auto-improve` onto main before proceeding rather than continuing with a stale base.
- `git checkout feature/auto-improve` — using the existing branch rather than creating a new auto-improve branch.
- `python -m pytest scripts/ -q` (baseline gate — expect 145 passed. Abort integration if this isn't green before you've added anything.)

## Architecture & File Layout
- **`ITERATE.md`**: project root.
- **`eval/`**: project root (`score.py`, `rubric.md`). No `chmod +x`.
- **`test-corpus/`**: project root, all 5 skill subfolders.
- **`scripts/test_manifest_resume_integration.py`**: added into the existing `scripts/` directory.
- **`README.md`, `CHANGELOG.md`**: replaced by the versions in `auto-improve/repo-updates/`.
- **`.claude-plugin/plugin.json`**: replaced by the version in `auto-improve/repo-updates/plugin.json`.

## Data Flow & Cleanup
1. Copy files from `auto-improve/` staging to their destinations.
2. Diff before trusting the copy: `git status --short` and `git diff --stat README.md CHANGELOG.md .claude-plugin/plugin.json`.
3. Verify destination files exist and are non-empty before deleting staging (e.g., test if `eval/score.py` exists).
4. Delete the `auto-improve/` staging directory.
5. Clean `eval/log.md` if present before committing.

## Validation
- `python -m pytest scripts/ -q` (must show 145 passed, same as baseline)
- `git status --short` (confirms new-file layout matches plan)
- `git diff .claude-plugin/plugin.json` (confirms version bumped `0.2.0` → `0.3.0`)

## Commit (local only)
- `git add -A`
- `git commit -m "0.3.0: iteration-loop tooling, eval harness, resume-integration test"`
