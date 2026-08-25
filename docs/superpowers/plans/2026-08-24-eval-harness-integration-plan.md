# Eval Harness Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the evaluation harness, test corpus, and iteration-loop tooling into the `code-comrades` repository as version `0.3.0`.

**Architecture:** A direct move of pre-staged files from `auto-improve` to the project root, followed by validation and a local commit.

**Tech Stack:** Bash, Python (pytest)

## Global Constraints

- No remote push, no PR yet — this stays local until reviewed.
- Pre-flight must pass before any changes are made.
- Test-suite parity with the pre-flight baseline is the actual completion gate here.

---

### Task 1: Pre-flight Checks

**Files:**
- Modify: None

**Interfaces:**
- Consumes: Existing test suite
- Produces: Baseline validation confirmation

- [ ] **Step 1: Check git status**

```bash
git status --short
```
Expected: Must be clean. Abort if not. (Note: `auto-improve/` is untracked, which is fine, but no tracked files should be modified).

- [ ] **Step 2: Update from main**

```bash
git checkout main
git pull
git checkout feature/auto-improve
# If git pull shows new commits on main, rebase here:
# git rebase main
```
Expected: Branch is up to date and rebased onto main if main moved.

- [ ] **Step 3: Run baseline tests**

```bash
python -m pytest scripts/ -q
```
Expected: 145 passed. Abort integration if this isn't green.

---

### Task 2: File Relocation

**Files:**
- Create/Modify: `ITERATE.md`, `eval/`, `test-corpus/`, `scripts/test_manifest_resume_integration.py`

**Interfaces:**
- Consumes: Files from `auto-improve/`
- Produces: New project layout

- [ ] **Step 1: Move root files**

```bash
mv auto-improve/ITERATE.md .
mv auto-improve/eval .
mv auto-improve/test-corpus .
```

- [ ] **Step 2: Move integration test script**

```bash
mv auto-improve/test_manifest_resume_integration.py scripts/
```
(Wait, the v2 spec says `scripts/test_manifest_resume_integration.py -> added into the existing scripts/ directory`. The source is `auto-improve/scripts/test_manifest_resume_integration.py` or `auto-improve/test_manifest_resume_integration.py`? My earlier ls showed it at `auto-improve/scripts/test_manifest_resume_integration.py` based on `find_by_name`. Let's use `cp -r` or `mv` correctly).

```bash
mv auto-improve/scripts/test_manifest_resume_integration.py scripts/
```

---

### Task 3: Metadata Updates

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `.claude-plugin/plugin.json`

**Interfaces:**
- Consumes: `auto-improve/repo-updates/`
- Produces: Updated v0.3.0 metadata

- [ ] **Step 1: Replace metadata files**

```bash
cp auto-improve/repo-updates/README.md .
cp auto-improve/repo-updates/CHANGELOG.md .
cp auto-improve/repo-updates/plugin.json .claude-plugin/plugin.json
```

---

### Task 4: Verification and Cleanup

**Files:**
- Modify: None (Deletes `auto-improve/`, `eval/log.md`)

**Interfaces:**
- Consumes: Project state
- Produces: Clean workspace

- [ ] **Step 1: Diff before trusting copy**

```bash
git status --short
git diff --stat README.md CHANGELOG.md .claude-plugin/plugin.json
```

- [ ] **Step 2: Verify destination files exist**

```bash
test -s eval/score.py && test -s ITERATE.md && echo "ok"
```
Expected: "ok"

- [ ] **Step 3: Delete staging directory**

```bash
rm -rf auto-improve/
```

- [ ] **Step 4: Clean eval log if present**

```bash
rm -f eval/log.md
```

---

### Task 5: Final Validation & Commit

**Files:**
- Modify: None

**Interfaces:**
- Consumes: Final project state
- Produces: 0.3.0 Commit

- [ ] **Step 1: Run validation tests**

```bash
python -m pytest scripts/ -q
```
Expected: 145 passed (or same as baseline).

- [ ] **Step 2: Validate metadata bump**

```bash
git diff .claude-plugin/plugin.json
```
Expected: Confirms version bumped 0.2.0 → 0.3.0.

- [ ] **Step 3: Commit changes**

```bash
git add -A
git commit -m "0.3.0: iteration-loop tooling, eval harness, resume-integration test"
```
