---
name: readme-per-folder
description: |
  Generate a short README.md for each immediate (non-recursive) subdirectory of a target path that lacks one, describing what it contains and how to get oriented in it. Use when asked to add READMEs to subfolders, document each directory in a project, or generate per-folder documentation. This is a code-comrades project skill (execution: project) — its unit of work is a directory producing a new file, not an edit to a discovered file, so it runs once per invocation rather than being sharded into per-file batch workers.
---

# readme-per-folder

**Why this is a project skill, not a batch skill:** `discover_files.py` walks a tree and returns *files* matching an extension list; it has no concept of "a directory that's missing a README." And the output here — a brand-new `README.md` — isn't an edit to any file a worker was dispatched with; it's synthesis from a directory's contents, the same category of problem `readme-master` solves at the whole-repo level. This skill is the same idea scoped down to one directory at a time.

**Why non-recursive:** scoped deliberately to the immediate children of the target path, one level, never deeper. A fully recursive walk turns "describe this folder" into "describe every folder in the tree in one pass," which risks shallow, templated output the deeper it goes and makes a single run's blast radius hard to reason about. Run this skill again with a nested path as the target to cover a deeper subtree explicitly — that's a feature, not a limitation: each invocation's `produces` stays small and reviewable.

Produce a short, honest README per qualifying subdirectory — grounded in what's actually in it, never a generic template. If a directory's purpose isn't inferable from its contents, say so briefly rather than padding.

## 1. Resolve the target and list immediate children

Determine `TARGET_PATH`: the user-named path, or the current working directory.

List only the **immediate** subdirectories of `TARGET_PATH` (one level — do not recurse into their children). Skip:

- The same directories `discover_files.py` always excludes (`node_modules`, `vendor`, `dist`, `build`, `__pycache__`, `.venv`, `.git`, and the rest of its `DEFAULT_EXCLUDE_DIRS` list) — this skill reuses that vocabulary so both execution models agree on what counts as "real" project structure, per `docs/project-skills.md`'s stated principle.
- Anything additionally listed in the repo's own `.gitignore`, if present.
- A subdirectory that is itself a git repository (a submodule) — its README is that project's own business, not this one's.
- A subdirectory that's a symlink — resolving it risks describing content that lives (and is already documented) elsewhere.

Run the facts script once for repo-level context (language shares, license, project identity — useful for phrasing, not required):

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/inspect_repo.py" --path "{TARGET_PATH}" > /tmp/code-comrades-repo-facts.json
```

## 2. Filter to qualifying subdirectories

For each immediate child directory:

- **`skip_if_exists: true`** (default) — if it already has a `README.md`, skip it entirely; never touch an existing one. (This skill only generates; it doesn't improve or sync — for that, point `readme-master` directly at the subdirectory as its own target.)
- **`skip_if_exists: false`** — regenerate every immediate child's `README.md` from scratch, overwriting.
- A directory with no files at all (only further subdirectories, none of which this pass looks inside) — skip, note as "no direct contents to describe" in the report. An empty directory or one containing only dotfiles — skip as trivial.

## 3. Gather evidence per directory

For each qualifying directory, look only at its **direct** contents (not nested subdirectories' contents — that's out of scope for a non-recursive pass):

1. **Source files** — by extension, to infer the primary language and, from filenames/a quick read of the shortest or most central file, the directory's evident purpose (e.g. a directory of files named `*_test.py` is a test suite; a directory with a single `index.js` and a `package.json` is a small module).
2. **Config/manifest files** — a `package.json`, `pyproject.toml`, or similar directly inside the directory signals it's an independent package/module, not just a grouping.
3. **Immediate subdirectory names only** (not their contents) — enough to summarize structure in one line ("contains `unit/` and `integration/` test suites") without describing what's inside those in depth.
4. **An existing doc comment or module-level docstring** in the directory's most central file (an `index.*`, `__init__.py`, or the only file present) — if the author already explained the directory's purpose there, adapt that explanation rather than re-deriving it from scratch.

If nothing in the directory's direct contents suggests a clear purpose, don't guess — write the honest, short version: what file types are present and how many, without inventing a narrative.

## 4. Produce the artifact

For each qualifying directory, write a README.md of roughly 50–150 words:

- One sentence: what this folder contains.
- One sentence: who reads/uses it (developers extending this area, contributors running its tests, end users of a generated artifact) — only if inferable; omit rather than guess.
- A short list of quick entry points: the main file(s), and the command to run if evident (a test directory's `pytest`/`jest` invocation, a module's entry file).

No headers-within-headers, no table of contents, no badges — this is a short orientation note, not a project-level README. Skip any of the three parts above that the directory's contents don't support; a two-sentence README is better than one padded to hit a word count.

## 5. Write the files

Write each `README.md` with the Write tool, directly inside its subdirectory. `produces` in `project.yaml` (`*/README.md`) is the write allowlist — immediate children only, never a nested path, never anything other than `README.md` itself.

If the working tree was dirty when you started, mention it in the report.

## 6. Report

- **What happened** — target path, count of subdirectories found, count skipped (already had a README, excluded pattern, submodule, symlink, empty/trivial), count written.
- **Evidence sources** — per written README, the one or two files that most informed its content.
- **Assumption ledger** — one line per directory where "who reads this" was inferred rather than obvious from an existing doc comment.
- **Review hint** — `git status` (new files won't show in `git diff`).

## Failure and edge behavior

- **No qualifying subdirectories** — report "0 folders needed a README" and write nothing; this is a normal, successful outcome.
- **Target path has no immediate subdirectories at all** (a leaf directory) — same as above.
- **Non-git directory** — works the same; only the `.gitignore`-based exclusion in Step 1 is unavailable, and the report should say so.
- **Idempotency** — re-running with `skip_if_exists: true` and no new qualifying subdirectories is a no-op. Re-running with `skip_if_exists: false` always regenerates every immediate child's README — that's the mode's documented behavior, not a bug.

---

## Constraints — Never Do

- Never recurse past the immediate children of the target path in a single run.
- Never overwrite an existing `README.md` when `skip_if_exists: true` (the default).
- Never write anywhere except `{child_dir}/README.md` for a direct child of the target path.
- Never describe a subdirectory's *nested* contents in depth — direct contents only, per Step 3.
- Never generate a README for a submodule, a symlinked directory, or an empty/trivial directory.
- Never pad a README to hit a word-count target; an honest short one beats a templated longer one.
- Never invent "who reads this" or a purpose the directory's contents don't support — omit instead.
