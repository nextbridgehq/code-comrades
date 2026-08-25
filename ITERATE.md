# ITERATE.md — autonomous iteration loop for code-comrades

Point Claude Code at the repo root with this file and let it run
unattended (accept-edits / `--dangerously-skip-permissions` mode). Two
independent tracks below — run either one, or both in sequence. Never
run both against the same file in the same round.

---

## Track A — SKILL.md judgment tuning (5 batch skills)

**Targets:** one at a time —
`skills/code-commenter/SKILL.md`, `skills/type-annotator/SKILL.md`,
`skills/error-handling-auditor/SKILL.md`,
`skills/license-header-injector/SKILL.md`,
`skills/import-sorter-cleaner/SKILL.md`.

**Fixed, never edit:** `test-corpus/<skill>/`, `eval/score.py`,
`eval/rubric.md`.

**Do not use this track for:** `test-stub-generator` (writes a new file,
doesn't fit the harness) or the project skills (`readme-master`,
`changelog-fragment-extractor`, `readme-per-folder` — different execution
model). If asked to improve those, stop and say so rather than
improvising a scoring method — see "Out of scope" below.

### Loop, per skill, up to 10 rounds

1. Pick the skill for this round (rotate through the 5, or focus one if
   told to). Read its current `SKILL.md` and the tail of `eval/log.md`
   for that skill's prior scores.
2. Make **one** targeted edit to that skill's `SKILL.md` — a rule, a
   phrasing change, a guardrail. Not a rewrite.
3. In a scratch git worktree (never the real repo, never `test-corpus/`
   itself):
   ```
   cp -r test-corpus/<skill> /tmp/iter-run-1
   # from inside a Claude Code session at /tmp/iter-run-1:
   # /code-comrades:dispatch <skill> . --dry-run=false --no-verify
   cp -r test-corpus/<skill> /tmp/iter-run-2
   # repeat dispatch on /tmp/iter-run-2 for the idempotency check
   ```
   `--no-verify`: verification protects real edits against a correctness
   gate; it has nothing to score against on a fixture corpus, so it's
   pure overhead here.
4. Score it:
   ```
   python eval/score.py --skill <skill> \
     --labels test-corpus/<skill>/labels.json \
     --run-a /tmp/iter-run-1 --run-b /tmp/iter-run-2 \
     --note "<one line describing the edit just made>"
   ```
5. Compare `final_score` to that skill's last logged score in
   `eval/log.md`.
   - **Better or equal:** keep the `SKILL.md` edit.
   - **Worse:** `git checkout` that `SKILL.md`, and note in your own
     scratch notes (not `eval/log.md` — `score.py` already logged the
     failed attempt with its score) what was tried and why it likely
     regressed, so the next round doesn't repeat it.
6. Stop early for that skill if 3 consecutive rounds show no improvement
   over its current best. Move to the next skill or end the session.
7. Clean up `/tmp/iter-run-*` before the next round — a stale scratch
   copy is easy to accidentally score against by mistake.

### A note on `type-annotator` and `error-handling-auditor` specifically

Their `skip` label means "correctly left alone because genuinely
ambiguous / already guarded" — not "unchanged because the skill didn't
look at it." An edit that raises accuracy by making the skill *more
aggressive* (annotating or flagging more things) should be viewed with
suspicion until you've checked it isn't just inflating recall on the
`comment`/`annotate`/`flag` labels at the expense of the `skip` ones.
`eval/rubric.md`'s quality component exists partly to catch this, but
also just read `accuracy_misses` in the result before trusting a score.

### Out of scope for this track

If asked to tune `readme-master`, `changelog-fragment-extractor`, or
`readme-per-folder`: these are project skills — one pass over the whole
repo producing declared artifacts, not per-file batch workers — so
"accuracy against a fixed corpus of labeled functions" doesn't apply.
Say so rather than forcing the harness onto them. A project-skill loop
would need a different fixture (a small pinned repo snapshot) and a
different metric (does the generated README match repository evidence,
scored by a judge against the actual repo state) — flag that as a
separate piece of work, don't invent one on the spot.

---

## Track B — correctness fixes in `scripts/`

**Targets:** `scripts/manifest.py`, `scripts/verify.py`,
`scripts/discover_files.py`, `scripts/inspect_repo.py`, or their tests.

**Gate:** `python -m pytest scripts/ -q` — binary pass/fail, not a
scored rubric. This is the deterministic layer; it doesn't get the fuzzy
judgment treatment Track A gets.

### Loop, up to 10 rounds

1. Read the current gap or bug being addressed (e.g. a missing edge
   case, a CLI behavior not covered by `scripts/test_*.py`).
2. Make one targeted change — a fix, or a new test that exercises a real
   gap (check first whether it's actually uncovered; `test_manifest.py`
   unit-tests the manifest functions directly, so a new manifest test
   should exercise something they don't — the CLI end-to-end, an
   interruption scenario, a concurrency case — not duplicate them).
3. Run `python -m pytest scripts/ -q`.
4. **All pass:** keep the change.
   **Any fail:** revert via `git checkout`, note what broke and why.
5. Stop when the gap is closed, or after 10 rounds, whichever first.

### Known starting points

- *(No known starting points queued — the `0.3.0` integration closed the initial batch. Identify a real gap from the codebase before starting a round. Examples of valid gaps include CLI-level coverage a unit test misses, or a stale doc line contradicting the code. Do not invent busywork to fill the loop.)*

---

## Before starting either track

```
python -m pytest scripts/ -q          # confirm baseline is green
git status                            # confirm a clean tree to diff against
```

Never start a round against a dirty tree — you won't be able to tell
your edit's effect from pre-existing uncommitted changes.
