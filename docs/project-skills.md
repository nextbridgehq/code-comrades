# Project skills

As of v0.6.0, code-comrades has two execution models. Which one a skill uses is declared by which contract file sits next to its `SKILL.md`:

| | Batch skill | Project skill |
|---|---|---|
| Contract file | `batch.yaml` | `project.yaml` |
| Unit of work | one file | the whole repository |
| Execution | dispatcher → N parallel scoped workers | the skill itself, in one pass |
| Output | edits to the input files | one or more declared artifacts |
| Manifest / resume | yes (per-chunk checkpointing) | no — a run is one pass |
| Verification / bisect | yes (`verify:` section) | no — review is `git diff` on the artifact |
| Examples | `code-commenter`, `type-annotator`, `license-header-injector`, `error-handling-auditor`, `import-sorter-cleaner`, `test-stub-generator` | `readme-master`, `changelog-fragment-extractor`, `readme-per-folder` |

The two models share a substrate but not machinery. A batch skill's value is safe parallelism over many independent files, so it gets manifests, chunking, verification, and revert. A project skill's value is synthesis — *understand the repository, produce an artifact* — so none of that applies, and pretending it does would only add ceremony.

## The contract: `project.yaml`

```yaml
skill: readme-master
version: 1
execution: project          # what makes this a project skill
produces:                   # write allowlist — the skill touches nothing else
  - README.md
inspect: scripts/inspect_repo.py   # deterministic facts script, run first
modes: [generate, improve, sync]   # optional, skill-defined
default_config:                    # merged under --config overrides, same as batch
  mode: auto
```

Field semantics:

- **`execution`** — currently only `project`. The field exists (rather than a boolean) so a future model — say `pipeline` or `interactive` — is a new value, not a new flag. Don't add values speculatively; add them when a skill needs one.
- **`produces`** — the artifacts the skill may create or edit, repo-root-relative. This is enforced by convention in the skill's own instructions: a project skill that wants to write a second artifact declares it here first. It's also what makes project skills reviewable — `git diff` on a known, short file list.
- **`inspect`** — the plugin-root-relative script the skill runs before any judgment. See "The intelligence layer" below.
- **`default_config`** — same semantics as `batch.yaml`: defaults, overridable per run.

### Multi-artifact `produces`

`readme-master` writes exactly one literal path (`README.md`), but not every project skill produces a single file. Two entries are valid beyond a literal path:

- **A directory, trailing slash** (`changelog/fragments/`) — the skill may write any number of files under that directory, never anything outside it. `changelog-fragment-extractor` uses this: one fragment file per qualifying commit, count not knowable until the run happens.
- **A one-level glob** (`*/README.md`) — the skill may write one file per immediate child of the target path, matching that exact pattern, never deeper and never a different filename. `readme-per-folder` uses this: one `README.md` per subdirectory it covers.

Both are still a write *allowlist*, same as a literal path — the skill's own instructions are what enforce it (there's no code-level sandboxing here, same as the single-file case). The point of writing either form explicitly in `project.yaml` is the same as a literal path: `git status`/`git diff` on a known, bounded location, not an unbounded "this skill can write anywhere" contract.

## How invocation works

Project skills are ordinary Claude Code skills — invoke them directly, conversationally ("generate a README for this repo") or by name. They don't need `/code-comrades:dispatch`, because there's nothing to dispatch: no discovery, no chunks, no workers.

`dispatch` still recognizes them, though, so the one-door UX holds: `/code-comrades:dispatch readme-master .` reads `project.yaml`, says it's routing to a project skill, and hands off — with a note that batch flags (`--changed`, `--max-files`, `--no-verify`, resume) don't apply. A project skill listed in a `dispatch pipeline` is rejected up front, before any batch skill in the pipeline has edited anything.

## The intelligence layer

`scripts/inspect_repo.py` is the shared substrate for all project skills: one deterministic walk of the repository emitting a JSON facts sheet — project identity (with per-field provenance), languages by share, manifests, package managers, frameworks, entry points, CI, containers, documentation files, git remote identity, and a structural scan of any existing README.

The division of labor it enforces is the same one `discover_files.py` established for the batch engine:

> **Everything deterministic lives in a tested script. Everything requiring judgment lives in the skill's SKILL.md.**

The script never edits, never touches the network, and is byte-stable for an unchanged tree — which is what lets project skills inherit the plugin's idempotency guarantee (a re-run against an unchanged repo is a no-op). It reuses `discover_files.py`'s exclusion vocabulary, so both execution models agree on what "the repository" means: a `node_modules/` full of JavaScript doesn't make your Python project look like a JS one to either engine.

New project skills should extend this script (with tests) rather than shelling out ad-hoc detection logic from their SKILL.md, so every skill benefits from every improvement.

## Adding a project skill

1. Create `skills/<name>/SKILL.md` with the skill's judgment: how to interpret the facts sheet, what to read beyond it, how to produce the artifact, and what the run report contains. Follow `readme-master`'s shape: facts → mode → evidence deep-dive → at most one round of questions → artifact → report with an assumption ledger.
2. Create `skills/<name>/project.yaml` declaring `execution: project`, the `produces` allowlist, the `inspect` script, and `default_config`.
3. If the skill needs facts the sheet doesn't carry yet, add them to `inspect_repo.py` **with tests** — that's the extension point, deliberately.

That's the whole contract. Candidates that fit this model cleanly: architecture review (`ARCHITECTURE.md`), dependency audit (`DEPENDENCIES.md`), contributor guide (`CONTRIBUTING.md`), changelog/release notes, API docs — each is "understand the repo, synthesize one artifact," and each reuses the same facts sheet.

## Why not force these through the batch engine?

A batch skill's contract is *one file in, one edit out, independent of every other file*. Repository synthesis violates every clause: the input is the whole tree, the output is a different file from any input, and value comes precisely from the cross-file understanding the batch model deliberately shards away. Wrapping README generation in a manifest and a worker pool wouldn't make it safer — it would only make it slower and the code dishonest about what's happening.
