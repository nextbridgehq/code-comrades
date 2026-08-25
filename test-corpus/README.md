# test-corpus

Fixed fixtures for scoring batchable-skill `SKILL.md` revisions, one
subfolder per skill: `code-commenter/`, `type-annotator/`,
`error-handling-auditor/`, `license-header-injector/`,
`import-sorter-cleaner/`. Do not edit these files by hand, and do not
add/rename functions or imports without updating the matching
`labels.json` — `eval/score.py` matches by name or exact import line, so
a rename silently breaks scoring for that entry.

`test-stub-generator` and the project skills (`readme-master`,
`changelog-fragment-extractor`, `readme-per-folder`) aren't covered here
— different execution model, see `docs/project-skills.md` in the main repo.

## Using it in the iteration loop

Never run `/code-comrades:dispatch` against these directories directly —
copy the relevant one to a scratch location first, so the fixture stays
clean for the next iteration:

```
cp -r test-corpus/code-commenter /tmp/run1
# inside a Claude Code session, from /tmp/run1:
# /code-comrades:dispatch code-commenter . --dry-run=false

cp -r test-corpus/code-commenter /tmp/run2
# repeat on /tmp/run2 for the idempotency check

python eval/score.py --skill code-commenter \
  --labels test-corpus/code-commenter/labels.json \
  --run-a /tmp/run1 --run-b /tmp/run2 \
  --note "describe the SKILL.md edit being tested"
```

Swap `code-commenter` for any of the other four skill names to score
that skill's `SKILL.md` instead — `eval/score.py --skill <name>` routes
to the right accuracy logic automatically (see `eval/rubric.md`).
