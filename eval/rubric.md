# scoring rubric — batchable skill iteration loop

Used by `eval/score.py` after each `/code-comrades:dispatch <skill>` run
against the matching `test-corpus/<skill>/`. Produces one number, 0-100,
per iteration. Higher is strictly better — that's the metric `ITERATE.md`
optimizes for whichever skill's `SKILL.md` is currently being edited.

Applies to the five batch skills that edit the dispatched file in place:
`code-commenter`, `type-annotator`, `error-handling-auditor`,
`license-header-injector`, `import-sorter-cleaner`. `test-stub-generator`
(writes a new file, doesn't edit the source) and the project skills
(`readme-master`, `changelog-fragment-extractor`, `readme-per-folder` —
a different execution model entirely, one pass over the repo rather than
per-file) don't fit this harness; see `docs/project-skills.md` for those.

## Inputs, per skill

- `test-corpus/<skill>/` — pinned sample files, untouched by the agent.
- `test-corpus/<skill>/labels.json` — hand-labeled ground truth. Shape
  varies by skill (see below).
- The **post-run** copy of that corpus, after the skill ran on it in a
  scratch worktree. A **second** post-run copy is optional, for the
  idempotency check.

## Score components

| Component | Weight | What it checks |
|---|---|---|
| Accuracy | 40% (80% for the two skills with no quality judge) | Skill-specific — see below. |
| Idempotency | 20% | Diff of two post-run copies. 100 if byte-identical, scaled down by fraction of changed lines. Skipped (weight redistributed) if only one run was provided. |
| Comment quality (LLM judge) | 40% | Only for the three marker-based skills below. A sample of "marker expected" targets is sent to a judge model, scored 0-100 on accuracy, non-redundancy with the code, idiom, and never touching logic. |

## Accuracy, per skill

- **`code-commenter`** — for each labeled function, is a comment/docstring
  present near it (found by name in the post-run file) when
  `expect: comment`, absent when `expect: skip`?
- **`type-annotator`** — same shape, `expect: annotate` (a type hint is
  present — `->`/param annotation in Python, JSDoc `@param`/`@returns` in
  JS) or `expect: skip`. **`skip` here means "genuinely ambiguous, correctly
  left untyped,"** not "was already typed" — a function that already had
  a hint before the run is indistinguishable from one the skill just
  annotated, so it can't carry the `skip` label.
- **`error-handling-auditor`** — same shape, `expect: flag` (an audit
  comment containing one of the audit keywords is present) or
  `expect: skip` (guarded code, correctly left alone). Matches only on
  audit-keyword comments, not any comment, so an unrelated comment near
  a function can't be mistaken for a flag.
- **`license-header-injector`** — whole-file, not per-function.
  `expect: header` (header present), `expect: no_duplicate` (a file that
  already had one header must still have exactly one, not two),
  `expect: skip` (e.g. a generated file — no header before, none after).
- **`import-sorter-cleaner`** — doesn't fit the marker-near-name shape at
  all. Each label lists `must_remove` and `must_keep` import lines for a
  file; accuracy is the fraction present/absent correctly. Side-effect-only
  imports (`import './polyfills.js'`) always belong in `must_keep` — the
  skill's whole contract is to never touch those.

## Why these three components

- **Accuracy** is the fuzzy, judgment-call part every skill's own docs
  flag as non-deterministic — the highest-value thing to tune.
- **Idempotency** is an explicit design guarantee; an edit that improves
  accuracy but breaks idempotency is a regression, not an improvement.
- **Quality** guards against gaming accuracy (e.g. flagging/commenting
  everything to inflate recall) by checking the markers are actually good,
  not just present. Not meaningful for `license-header-injector` (a header
  either matches the template or doesn't) or `import-sorter-cleaner`
  (an import is either correctly kept/removed or it isn't) — both skip it
  and put full weight on accuracy + idempotency instead.

## Known limitations

- Function boundaries are found by name match, not a real parser per
  language — fine for a fixed, hand-picked corpus with unique function
  names, but two same-named functions in one file would only score the
  first match.
- The judge score depends on judge-model consistency; treat small deltas
  (a few points) as noise, not signal.
- `type-annotator`'s ambiguous-vs-inferable line and `error-handling-
  auditor`'s guarded-vs-unguarded line are judgment calls in the fixture
  design itself, same as the skills they're testing — if a fixture's
  label looks wrong on inspection, fix the label, don't chase the score.
