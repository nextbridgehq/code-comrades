---
name: changelog-fragment-extractor
description: |
  Extract commit-worthy changelog fragments from git history since the last tag (or the full history on first run) and write one fragment file per qualifying commit. Use when asked to extract changelog fragments, generate changesets, prepare release notes fragments, or collect what's changed since the last release. This is a code-comrades project skill (execution: project) — its unit of work is git commits, not files, so it runs once against the repository's history rather than being sharded into per-file batch workers.
---

# changelog-fragment-extractor

**Why this is a project skill, not a batch skill:** every batch skill in this plugin (`code-commenter`, `type-annotator`, `license-header-injector`, `error-handling-auditor`, `import-sorter-cleaner`, `test-stub-generator`) operates on files discovered from the working tree — one file in, one file touched. This skill's actual unit of work is **a commit**, and its output is a **new fragment file per qualifying commit**, unrelated to any file `discover_files.py` could match. Forcing that through the batch engine would mean inventing a fake file-discovery pass over git history that the engine was never built for. A project skill — one pass, real git commands, a declared artifact location — is the honest fit.

Produce one small, human-readable fragment file per commit that represents a real user-facing change since the last extraction point. Every fragment must trace to a real commit SHA and a real message or diff comment — never invent a change that isn't in the log.

## 1. Resolve the target and gather facts

Determine `REPO_ROOT` the same way `readme-master` does: the user-named path, or `git rev-parse --show-toplevel`, falling back to the working directory if this isn't a git repo. **If this isn't a git repo, stop here** — see Failure and edge behavior below; there is no meaningful fallback for a skill whose entire input is git history.

Run the facts script for the shared substrate (mainly useful here for `git.latest_tag` and `git.remote`):

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/inspect_repo.py" --path "{REPO_ROOT}" > /tmp/code-comrades-repo-facts.json
```

## 2. Pick the mode

- `mode: since-tag` (default) — if `git.latest_tag` is non-null, scan `{latest_tag}..HEAD`. If `git.latest_tag` is null (no tags exist yet), behave as `full` for this run and say so.
- `mode: full` — scan the entire history (`git log --all` from the root commit), or an explicit user request to rebuild every fragment from scratch.

An explicit `mode:` in config or the user's words overrides the auto choice. State the chosen mode and the resolved commit range before doing anything else.

## 3. Evidence deep-dive

Run the real commit-range query — this is the deterministic backbone, same spirit as `inspect_repo.py` being the backbone for `readme-master`:

```bash
git -C "{REPO_ROOT}" log --no-merges --pretty=format:'%H%x1f%s%x1f%b%x1e' {RANGE}
```

(`--no-merges`: a merge commit is not itself a user-facing change; its constituent commits already appear individually in the range. `%x1f`/`%x1e` as field/record separators avoid ambiguity with commit message punctuation.)

For each commit, in order:

1. **Classify from the subject line, if `scan_messages: true`** (default): Conventional Commits prefix (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`, `perf:`, `build:`, `ci:`) or an explicit marker (`CHANGE:`, `FIX:`, `FEATURE:`, `BREAKING:`). Map to a changelog category (Language Reference below).
2. **Scan the commit's diff for `scan_comments: true`** (default): a comment matching `TODO changelog: <description>` (or the equivalent for the file's language) added or modified in that commit's diff is direct author intent — extract it verbatim as the fragment's description, category inferred from the marker if present (`TODO changelog: FIX: ...`) or from context otherwise.
3. **A commit with neither** — no conventional prefix, no marker, no changelog comment — is not commit-worthy on its own. Skip it. Do not paraphrase a raw, unstructured commit message into a fragment; that's inventing intent the commit didn't declare.

## 4. Confidence gate — what's commit-worthy

- **Include:** any commit classified in Step 3.1 or 3.2 as `feat`/`FEATURE`, `fix`/`FIX`, `refactor` (only when the subject itself signals a user-visible behavior change, not an internal cleanup — read the body if the subject is ambiguous), `BREAKING`/a `!` after the conventional-commit type (`feat!:`), or carrying an explicit `TODO changelog:` comment.
- **Exclude by default:** `chore:`, `test:`, `ci:`, `build:`, `docs:` (unless a `TODO changelog:` comment says otherwise), and any commit whose entire diff is confined to files this plugin's own discovery would exclude (`.claude-batch-manifest/`, lockfiles, generated code) — these are never user-facing.
- **Ambiguous** (unclear whether the change is user-facing): list it in the run report under "skipped as ambiguous" with the commit SHA and subject, rather than guessing either way.

## 5. Produce the artifacts

For each commit that clears Step 4, write one fragment file to `{REPO_ROOT}/changelog/fragments/{category}-{short-sha}.md`:

```markdown
### {category}

{one-line description, from the conventional-commit subject with the prefix stripped, or the TODO changelog: comment verbatim}

_Commit: {short-sha}_
```

`{category}` is one of `added`, `fixed`, `changed`, `removed`, `breaking` (Language Reference below). `{short-sha}` is the commit's abbreviated hash (`git rev-parse --short`), which also makes the filename collision-proof and the fragment traceable.

If a fragment file for a given commit SHA already exists from a prior run (idempotency — re-running `since-tag` mode after new commits landed should only add fragments for the *new* commits), skip regenerating it.

## 6. Write the files

Write each fragment with the Write tool under `{REPO_ROOT}/changelog/fragments/`. `produces` in `project.yaml` is the write allowlist: this skill touches only that directory, never `CHANGELOG.md` itself — collecting fragments into the actual changelog at release time is a separate, deliberate step (manual, or a future `changelog-master`-style skill), not something this extractor does automatically. Never delete a fragment file that already exists; only add.

If the working tree was dirty when you started, mention it in the report.

## 7. Report

- **What happened** — mode, resolved commit range, count of fragments written, count of commits skipped as not-commit-worthy, count skipped as ambiguous (with SHAs).
- **Evidence sources** — the exact `git log` range used, whether `scan_comments` found any `TODO changelog:` markers.
- **Assumption ledger** — one line per ambiguous classification call made instead of asked (e.g. "Classified `refactor: simplify token cache` as user-facing because the body mentions a behavior change; verify this belongs in the changelog.").
- **Review hint** — `git status changelog/fragments/` (new files won't show in `git diff`).

## Failure and edge behavior

- **Not a git repo** — this skill's entire input is git history; there is no meaningful fallback. Report that clearly and stop; do not fall back to scanning working-tree files for hints.
- **No tags and `mode: since-tag`** — behave as `full` and say so explicitly, so the user isn't surprised by a larger-than-expected fragment count on a repo's first run.
- **No qualifying commits in range** — report "0 fragments — nothing commit-worthy since {latest_tag}" and write nothing. This is a normal, successful outcome, not an error.
- **Idempotency** — re-running `since-tag` mode with no new commits since the last run must be a no-op (Step 5's per-SHA skip). Re-running `full` mode will re-emit fragments for every qualifying commit in history; if fragments from a prior `since-tag` run already exist for some of those SHAs, skip regenerating those specifically, same rule.

---

## Language Reference — Category Mapping

| Commit signal | Category |
|---|---|
| `feat:` / `FEATURE:` | `added` |
| `fix:` / `FIX:` | `fixed` |
| `refactor:` (only if user-visible per body) / `CHANGE:` | `changed` |
| A `TODO changelog:` comment naming a removal, or a commit removing a public API | `removed` |
| `feat!:`, `fix!:`, or `BREAKING:` / a `BREAKING CHANGE:` footer | `breaking` |

---

## Constraints — Never Do

- Never invent a fragment for a commit with no conventional-commit prefix, no explicit marker, and no `TODO changelog:` comment — paraphrasing a raw commit subject is inventing intent, not extracting it.
- Never write to `CHANGELOG.md` directly — only to `changelog/fragments/`, per `produces`.
- Never regenerate a fragment for a commit SHA that already has one on disk.
- Never delete an existing fragment file.
- Never run against a non-git directory by falling back to guessing changes from file contents.
- Never classify `chore:`/`test:`/`ci:`/`build:`/`docs:` commits as user-facing without an explicit `TODO changelog:` comment overriding the default.
