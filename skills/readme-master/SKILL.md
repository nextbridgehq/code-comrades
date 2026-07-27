---
name: readme-master
description: |
  Generate, improve, or synchronize a repository's README.md from evidence in the repository itself. Use when the user asks to write, create, generate, improve, rewrite, update, fix, or sync a README or top-level project documentation for the current repo or a given path. This is a code-comrades project skill (execution: project) — it inspects the whole repository once and produces a single artifact; it is not batchable and must never be run through the per-file batch engine.
---

# readme-master

Produce the README a senior maintainer would write for this repository — grounded in what the repository actually contains, not in what a template assumes. Every claim in the output must trace to evidence: a manifest field, a file that exists, a command that the project's own configuration defines, or documentation the repository itself provides. When evidence is missing, say so explicitly rather than inventing.

**Prime directive — inference before interrogation.** You are running inside the repository. Almost every question a README-writing wizard would ask (what language? what package manager? is there Docker? what license?) is answerable by reading the tree. Asking the user something the repo can answer is a failure of this skill. The inverse also holds: never guess something the repo *cannot* answer (a live demo URL, a security contact, the maintainer's roadmap) — those get one round of questions or an explicit `[REPLACE: ...]` placeholder.

**Corollary — exhaust all documentation sources.** The repository may document itself through any combination of manifests, markdown files, config schemas, code comments, and structured metadata. The skill's job is to find and synthesize *all* of it, not just the subset that fits a conventional package-manager mold. A `USAGE.md` at root is as authoritative as a `package.json` scripts block; a `.claude-plugin/plugin.json` is as valid an identity source as `pyproject.toml`.

## 1. Resolve the target and gather facts

Determine `REPO_ROOT`: if the user named a path, use it; otherwise run `git rev-parse --show-toplevel` from the working directory, falling back to the working directory itself if this isn't a git repo.

Run the facts script and read its output:

```bash
python "$CLAUDE_PLUGIN_ROOT/scripts/inspect_repo.py" --path "{REPO_ROOT}" > /tmp/code-comrades-repo-facts.json
```

This is the deterministic backbone of the run. The facts sheet includes:

Project identity — name, description, version, license, each with its source file.
Languages — by file share.
Manifests — recognized ecosystem manifests (package.json, pyproject.toml, Cargo.toml, go.mod, etc.).
Custom manifests — non-standard identity/config files detected by field heuristics (any JSON/YAML/TOML containing name, description, version, commands, permissions, or similar identity fields in the root or one directory deep). These cover plugin manifests, extension configs, tool descriptors, and any project that doesn't use a conventional package manager.
Documentation files — all markdown and structured text files in the repository tree, with line counts and heading outlines. This ensures visibility into USAGE.md, GUIDE.md, FEATURES.md, ARCHITECTURE.md, or any other documentation the maintainer has written, regardless of filename.
Package managers and frameworks — detected from lockfiles, imports, and config.
Entry points — main, CLI, and binary entry points by ecosystem convention.
CI systems — workflow names, filenames, and detected matrix.
Container files — Dockerfiles, compose files.
Notable directories — examples/, docs/, tests/, scripts/, etc.
Git remote identity — host, owner, repo slug, default branch, latest tag.
Existing README scan — headings, badge slugs, fenced commands, version claims.
Trust the facts sheet for what exists; do your own reading for what it means (Step 3).

## 2. Pick the mode
With `mode: auto` (the default), decide as follows — and state the chosen mode before doing anything:

| Situation | Mode |
| --- | --- |
| `readme.exists` is false | `generate` |
| README exists; user said improve / rewrite / polish / "make it better" | `improve` |
| README exists; user said update / sync / fix / "it's outdated" | `sync` |
| README exists; user just said "do the README" or similar | `sync` first; offer `improve` if the structure itself is weak |

An explicit `mode:` in config (or in the user's words) overrides the table. Never silently overwrite an existing README with a from-scratch generation — that destroys the maintainer's voice and history. `generate` mode over an existing README requires the user to have clearly asked for a rewrite.

## 3. Evidence deep-dive
The facts sheet tells you where to look; now look. Read, in this order, skipping what doesn't exist:

1. **All manifests** — every file listed in `manifests` AND `custom_manifests` (the full file, not just the parsed fields). For recognized manifests: scripts, bin, feature flags, workspace layout all inform Usage. For custom manifests: extract name, description, version, permissions, commands, capabilities, and any other README-relevant metadata exactly as you would from a package.json. A plugin manifest, extension descriptor, or tool config is a first-class identity source.

2. **The main entry point** — `entry_points.main`, `src/index.*`, `main.py`, `cmd/*/main.go`, `src/main.rs` — follow the ecosystem's convention.

3. **Examples** — `examples/` directory is the single best source of honest Usage snippets. Prefer adapting a real example over inventing one.

4. **All documentation files** — every `.md` file listed in `documentation_files` that isn't the README itself. Read in priority order:
   - *High priority (read in full)*: Files whose names or headings directly map to README framework sections — `USAGE.md`, `FEATURES.md`, `GUIDE.md`, `QUICKSTART.md`, `INSTALL.md`, `CONFIGURATION.md`, `API.md`, `ARCHITECTURE.md`, `FAQ.md`, or any file with headings that overlap the section framework in Step 5.
   - *Medium priority (read in full if < 200 lines, skim otherwise)*: Other root-level `.md` files, `docs/` index or top-level pages.
   - *Lower priority (skim first 50–80 lines for evidence)*: Deep `docs/` subpages, tutorial series pages.
   These files are authoritative evidence — a `USAGE.md` at root written by the maintainer is as trustworthy as code. Adapt its content into the README's structure; do not ignore it because it isn't on a hardcoded list.

5. **Existing named docs** — `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md` (latest one or two entries), `.env.example`. (Many of these will already be covered by step 4; don't re-read.)

6. **CI workflow files** — they reveal the real build/test commands and supported matrix (OS, language versions), which beat anything you'd guess.

7. **Tests' top-level structure** — enough to describe how to run them.

8. **Config schemas and environment files** — `.env.example`, `config.schema.json`, `settings.yaml`, CLI flag definitions in code. These inform the Configuration section.

Budget: read what changes the README's content, stop when additional files stop changing your understanding. On a large repo, breadth (skim many) beats depth (read few exhaustively). But never skip a documentation file that the facts sheet surfaced just because it has an unconventional name — if the maintainer wrote it, it likely contains evidence.

## 4. The one question round
Collect everything the repo genuinely cannot answer. The usual suspects: one-line tagline (if no manifest description and no description in any custom manifest or documentation file), live demo URL, whether screenshots/GIFs exist to link, security contact address, roadmap items. Then:

- If two or more such gaps would leave visible placeholders in prominent sections, ask *once*, as a single short message listing the gaps, offering "skip — use placeholders" as an explicit option.
- Otherwise proceed and mark each gap inline as `[REPLACE: what goes here]` — searchable, so the maintainer can grep for every spot needing real content.

Never ask a second round. Never ask about anything the facts sheet or your reading already answered. A description found in USAGE.md or plugin.json counts as answered — do not re-ask for it.

## 5. Produce the artifact
### Mode: generate
Build the README from this section framework. Skip any section without evidence to fill it — a skipped section is honest; a padded one is noise. Note skips briefly in the run report, not in the README.

**Critical rule:** if the evidence deep-dive surfaced rich content (detailed features, usage patterns, configuration, limitations, known issues, architecture notes) from ANY source — manifests, documentation files, code, examples — that content MUST be reflected in the appropriate sections below. A sparse README in the face of rich documentation is a skill failure, not an honesty virtue. The skill's job is to synthesize all evidence into a well-structured README, not to gate content behind specific file naming conventions.

- **Header** — `<p align="center">` project name as `<h1>`, italic one-line tagline. Logo `<img>` only if an actual logo file exists in the repo (check `assets/`, `docs/`, `.github/`); otherwise omit — no placeholder images.

- **Badges** — static shields.io URLs built from `git.remote.slug`: license, latest release/tag, CI workflow status (use the real workflow filename from the facts sheet), plus registry badges (npm/PyPI/crates.io/Docker Hub) only when the manifest shows the project is actually published (has a version and isn't `"private": true`). No git remote → no badges; don't fabricate a slug.

- **Table of contents** — collapsible `<details>` block, anchor-linked, only when the README ends up with more than ~6 sections (`toc: auto`).

- **Why this project / Overview** — 3–5 sentences (or more if the evidence supports it) from the manifest description, documentation files, code reading, and any existing docs: the problem, the approach, who it's for. This is the one section where you write prose from understanding rather than transcribe facts — keep it concrete and free of marketing filler. If the project's own docs explain its purpose clearly, adapt that explanation.

- **Features** — bulleted, bold lead-in per item, each traceable to something real in the code or documentation. Group into subheadings past ~6 items. Draw from ALL evidence sources: feature lists in USAGE.md or FEATURES.md, capabilities declared in plugin/extension manifests, commands defined in config, patterns visible in the code. A feature documented by the maintainer in any repo file is evidence.

- **Demo / screenshots** — only if a demo URL or image files exist (or the user provided them in Step 4). Otherwise omit entirely.

- **Installation** — every method the repo actually supports, detected not assumed:
  - the ecosystem's package-manager install if published (`npm i x`, `pip install x`, `cargo install x`, `go install module@latest`)
  - clone + install using the detected package manager (the lockfile decides: pnpm lockfile → `pnpm install`, not `npm install`)
  - Docker, only if a Dockerfile/compose file exists
  - plugin/extension-specific install methods if a custom manifest indicates one (e.g., "add to your editor's plugin directory", "register as a tool")
  - runtime requirement line from `project.runtime_requirement` (e.g. "Requires Node ≥ 18")
  - any installation instructions found in documentation files (INSTALL.md, USAGE.md install section, etc.)

- **Usage / quick start** — smallest real working example first (from `examples/`, tests, documentation files, or the entry point's actual API), then one or two advanced ones. Language-tagged code fences. For a CLI, show the real command names from `entry_points.cli`. If the repo has a USAGE.md or similar file with detailed usage patterns, commands, workflows, or recipes — synthesize that content here. This section should reflect the full depth of usage documentation the maintainer has written, not just what can be inferred from code alone.

- **Configuration** — markdown table (name / type / default / description) from real config surfaces: `.env.example`, config file schemas, CLI flags, plugin/extension settings declared in manifests. Omit if the project has no user-facing configuration. If documentation files describe configuration in prose, convert to structured format.

- **Architecture / How it works** — include when the repo has architecture docs, or when the project's structure is non-obvious and the evidence deep-dive revealed how components connect. Keep concise — a paragraph or short list, not an essay. Omit for simple projects.

- **Known limitations / Caveats** — include when documentation files, issues, or code comments explicitly call out limitations, known issues, or important caveats users should know. Omit if none are documented.

- **Security** — include when the repo has SECURITY.md (link it) or the project is the kind that needs disclosure instructions (network-facing, published library). Use the real contact or `[REPLACE: security contact]`.

- **Contributing** — link CONTRIBUTING.md if present; otherwise two or three honest inline steps (fork, branch, the repo's real test command, PR).

- **Roadmap** — only from evidence: user input, TODO/ROADMAP files, or "Unreleased" changelog entries. Never invent roadmap items.

- **Changelog** — link CHANGELOG.md and summarize its latest entry, if present.

- **FAQ** — only when the repo's issues/docs/existing README/documentation files suggest real recurring questions. An invented FAQ is filler; omit is the default. But a FAQ found in the repo's own docs IS evidence — include it.

- **License** — name it (from the facts sheet), link LICENSE.

- **Acknowledgements / footer** — credit key dependencies or inspirations when evident; keep to a few lines.

Formatting rules: emoji only in section headers and only when `emoji_headers` is true; `---` rules between major sections; every code fence language-tagged; markdown tables for reference data; no ALL-CAPS prose; badges and header centered with `<p align="center">`, everything else plain markdown.

### Mode: improve
The maintainer's voice and intent stay; the structure and completeness improve.
- Keep their prose wherever it's accurate — rewrite for clarity, don't replace for style.
- Reorder/retitle sections toward the framework above; add missing high-value sections (Installation variants, Configuration, License, Features, Architecture) from evidence gathered in Step 3; delete nothing that's still true without flagging it.
- Actively look for content in documentation files that the existing README doesn't cover — if USAGE.md has detailed command documentation but the README's Usage section is a stub, expand it.
- Apply every `sync`-mode correction (below) as part of the pass.

### Mode: sync
The README is treated as a set of claims; the facts sheet and your reading are the evidence; stale claims get corrected surgically with Edit — voice, structure, and accurate content untouched.

Check at minimum, using the facts sheet's readme scan as the starting worklist:
- Version claims (`readme.version_claims`) vs `project.runtime_requirement` and manifest versions — "Requires Python 3.9" when pyproject says `>=3.13`.
- Commands (`readme.fenced_commands`) vs detected package managers and manifest scripts — `npm install` in a pnpm repo, a `make test` target that no longer exists, a renamed CLI binary.
- Badge slugs (`readme.badge_slugs`) vs `git.remote.slug`, and badge workflow filenames vs actual `ci[].workflows`.
- License name vs `project.license`.
- Framework/stack statements vs `frameworks` — a README that says Flask for a repo whose only web dependency is FastAPI.
- Feature claims vs the code and documentation files, where cheaply checkable (a documented CLI flag that no longer parses, a documented file that no longer exists).
- Completeness gap — if the README is missing major sections that the evidence deep-dive can fill (e.g., the README has no Usage section but USAGE.md exists with rich content), flag this in the report and offer to run `improve` mode.

For each stale claim, fix it and record claim → evidence → fix for the report. If the README makes a claim you can neither verify nor refute, leave it alone and list it under "unverified" in the report — `sync` mode never deletes on suspicion. If nothing is stale, change nothing and say so: a no-op sync on an up-to-date README is the skill working, and it's what makes re-runs idempotent.

## 6. Write the file
Write the result to `{REPO_ROOT}/README.md` with the Write tool (Edit for sync-mode patches). The file on disk is the deliverable — never paste the README's full content into the conversation as the primary output. `produces` in `project.yaml` is your write allowlist: this skill touches `README.md` and nothing else. If work surfaces the need for a companion file (CONTRIBUTING, SECURITY), suggest it in the report; don't create it.

If the working tree was dirty when you started, mention it in the report so the maintainer knows git diff mixes their changes with yours.

## 7. Report
End with a short structured summary in the conversation:
- **What happened** — mode, file written/patched, section list (generate/improve) or claim → fix list plus unverified claims (sync).
- **Evidence sources used** — list all files read during the evidence deep-dive (not just manifests — include documentation files, custom manifests, config files). This makes the skill's reasoning transparent and auditable.
- **Assumption ledger** — one line per assumption made instead of asked, e.g. "Assumed the pnpm lockfile is authoritative over the npm one also present — remove whichever is stale." Every `[REPLACE: ...]` placeholder gets a line here too.
- **Quality footer** (when `quality_footer` is true) — a short checklist of what would raise the README further, drawn from real absences in the facts sheet: no screenshots/demo, no CONTRIBUTING.md, no CI badge because no CI, no CHANGELOG. Frame as suggestions, not scores — "Repository health: 92/100" theater helps nobody; "you have CI but no badge for it" does.
- **Review hint** — `git diff README.md`.

## Failure and edge behavior
- **Empty or near-empty repo** — say the repo doesn't contain enough evidence for a grounded README, generate the honest skeleton (name, license if present, `[REPLACE:]` placeholders), and say exactly that in the report.
- **Facts script fails** — report the error and stop; don't fall back to guessing a repository you couldn't inspect.
- **Monorepo** — the facts sheet reads root-level manifests only. If the root is a workspace shell (`workspaces` field, no root `src`), say so and ask whether the target is the root README or a specific package before writing anything.
- **Non-git repo** — everything works except badges and remote-derived links; omit them silently.
- **Non-standard project type** — if the facts sheet shows `custom_manifests` but no recognized manifests, the project uses a non-conventional ecosystem (editor plugin, CLI tool, browser extension, platform integration, etc.). This is normal — treat custom manifests and documentation files as primary evidence sources. Do not produce a sparse README just because the project doesn't match npm/PyPI/crates.io conventions.
- **Rich documentation exists but no standard manifest** — if the facts sheet shows extensive `documentation_files` but sparse manifests, the documentation IS the evidence. Read it thoroughly and synthesize. A well-documented project without a package.json deserves a comprehensive README.
- **Idempotency** — re-running `sync` (or `improve`) on an unchanged, up-to-date repo must be a no-op. That property is inherited from the facts script being deterministic; don't break it by injecting dates, "last updated" lines, or run-specific content into the README.
