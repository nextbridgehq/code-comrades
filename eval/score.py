#!/usr/bin/env python3
"""Score a batchable skill's run against its fixed test-corpus.

Generalized from the original code-commenter-only version. Five skills
share the same edit-in-place, fuzzy-judgment shape and reuse this one
scorer with a per-skill detector; `import-sorter-cleaner` doesn't fit
that shape (it's "keep/remove exactly the right imports", not "is there
a marker near this name") and gets its own accuracy function.

Zero third-party dependencies (stdlib only), matching the rest of the
code-comrades plugin. Optional judge component uses the Anthropic API
directly over urllib if ANTHROPIC_API_KEY is set; otherwise it's skipped
and the weights are re-normalized across whatever ran.

Usage:
    python eval/score.py --skill code-commenter \
        --labels test-corpus/code-commenter/labels.json \
        --run-a /tmp/scratch-run-1 --run-b /tmp/scratch-run-2 \
        --note "tightened trivial-function heuristic in SKILL.md"

Supported --skill values: code-commenter, type-annotator,
license-header-injector, error-handling-auditor, import-sorter-cleaner.

--run-b is optional; omit it to skip the idempotency component.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

WINDOW = 3  # lines above/below a found target to search for a marker
JUDGE_MODEL = "claude-sonnet-4-6"
JUDGE_SAMPLE_SIZE = 8

COMMENT_MARKERS = ("#", "//", "/*", "*", '"""', "'''", "///", "--")
AUDIT_MARKER_WORDS = ("AUDIT", "UNGUARDED", "UNCHECKED", "NULL-CHECK", "NULLCHECK")
HEADER_MARKERS = ("SPDX-License-Identifier", "Copyright")


# --- shared file/name-finding helpers -------------------------------------

def read_lines(base_dir, rel_path):
    full = os.path.join(base_dir, rel_path)
    with open(full, errors="replace") as f:
        return f.readlines()


def find_function_line(lines, name):
    """Locate a function by name rather than by original line number.

    Inserting a marker shifts every subsequent line, so trusting a
    pre-run line number against a post-run file silently misaligns labels
    for every target after the first edited one. Searching by name in
    the post-run file sidesteps that entirely.
    """
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    for i, line in enumerate(lines):
        if pattern.search(line):
            return i  # 0-indexed
    return None


# --- per-skill "is the marker there" detectors ----------------------------

def detect_comment(lines, line_idx):
    """code-commenter: any comment/docstring line near the function."""
    lo, hi = max(0, line_idx - WINDOW), min(len(lines), line_idx + WINDOW + 1)
    return any(lines[i].strip().startswith(COMMENT_MARKERS) for i in range(lo, hi))


def detect_type_hint(lines, line_idx):
    """type-annotator: PEP 484/585 annotation on the def line itself, or a
    JSDoc @param/@returns block immediately above it. Deliberately does
    NOT match on any-comment like detect_comment does — an unrelated
    comment near a function must not be mistaken for a type annotation.
    """
    def_line = lines[line_idx]
    if "->" in def_line or re.search(r":\s*[A-Za-z_][\w\[\], .]*\)", def_line):
        return True  # Python-style return/param annotation on the signature line
    lo = max(0, line_idx - WINDOW)
    for i in range(lo, line_idx):
        if "@param" in lines[i] or "@returns" in lines[i] or "@return" in lines[i]:
            return True
    return False


def detect_audit_marker(lines, line_idx):
    """error-handling-auditor: a comment carrying one of the audit
    keywords, not just any comment — this skill is audit-only by default,
    so the marker's specific wording is the whole signal.
    """
    lo, hi = max(0, line_idx - WINDOW), min(len(lines), line_idx + WINDOW + 1)
    for i in range(lo, hi):
        stripped = lines[i].strip()
        if stripped.startswith(COMMENT_MARKERS) and any(w in stripped.upper() for w in AUDIT_MARKER_WORDS):
            return True
    return False


DETECTORS = {
    "code-commenter": detect_comment,
    "type-annotator": detect_type_hint,
    "error-handling-auditor": detect_audit_marker,
}


# --- accuracy scorers ------------------------------------------------------

def score_accuracy_by_name(labels, run_dir, detector):
    """Shared scorer for the three skills whose targets are named
    functions with an expect: comment|skip label (code-commenter,
    type-annotator, error-handling-auditor)."""
    correct, total, misses = 0, 0, []
    for item in labels:
        total += 1
        try:
            lines = read_lines(run_dir, item["file"])
        except FileNotFoundError:
            misses.append({**item, "reason": "file missing in run output"})
            continue
        line_idx = find_function_line(lines, item["name"])
        if line_idx is None:
            misses.append({**item, "reason": "function not found in run output"})
            continue
        found = detector(lines, line_idx)
        expect_marker = item["expect"] in ("comment", "annotate", "flag")
        if found == expect_marker:
            correct += 1
        else:
            misses.append({**item, "reason": "mismatch"})
    pct = 100.0 * correct / total if total else 0.0
    return pct, misses


def score_accuracy_license_header(labels, run_dir):
    """license-header-injector: whole-file check — is a header present
    in the first few lines when expected, absent (or, for the
    already-has-one fixtures, unduplicated) when not."""
    correct, total, misses = 0, 0, []
    for item in labels:
        total += 1
        try:
            lines = read_lines(run_dir, item["file"])
        except FileNotFoundError:
            misses.append({**item, "reason": "file missing in run output"})
            continue
        head = "".join(lines[:8])
        marker_count = sum(head.count(m) for m in HEADER_MARKERS)
        if item["expect"] == "header":
            ok = marker_count >= 1
        elif item["expect"] == "no_duplicate":
            # A file that already had exactly one header must still have
            # exactly one — a duplicate is as much a failure as a missing one.
            ok = marker_count == item.get("existing_marker_count", 1)
        else:  # "skip" — file intentionally excluded (e.g. generated file)
            ok = marker_count == 0
        if ok:
            correct += 1
        else:
            misses.append({**item, "reason": "mismatch", "marker_count": marker_count})
    pct = 100.0 * correct / total if total else 0.0
    return pct, misses


def score_accuracy_import_sorter(labels, run_dir):
    """import-sorter-cleaner: doesn't fit the marker-near-name shape at
    all — correctness here means the right import lines are gone and the
    right ones survived, so it gets its own set-comparison scorer.
    Each label item: {file, must_remove: [...], must_keep: [...]}.
    """
    correct, total, misses = 0, 0, []
    for item in labels:
        try:
            lines = read_lines(run_dir, item["file"])
        except FileNotFoundError:
            total += len(item["must_remove"]) + len(item["must_keep"])
            misses.append({**item, "reason": "file missing in run output"})
            continue
        content = "".join(lines)
        for imp in item["must_remove"]:
            total += 1
            if imp not in content:
                correct += 1
            else:
                misses.append({"file": item["file"], "import": imp, "reason": "should have been removed"})
        for imp in item["must_keep"]:
            total += 1
            if imp in content:
                correct += 1
            else:
                misses.append({"file": item["file"], "import": imp, "reason": "should have been kept"})
    pct = 100.0 * correct / total if total else 0.0
    return pct, misses


# --- idempotency (shared across all skills) --------------------------------

def score_idempotency(run_a, run_b, files):
    total_lines, changed_lines = 0, 0
    diffs = []
    for rel in sorted(set(files)):
        try:
            a = read_lines(run_a, rel)
            b = read_lines(run_b, rel)
        except FileNotFoundError:
            diffs.append({"file": rel, "reason": "missing in one run"})
            continue
        total_lines += max(len(a), len(b))
        for i in range(max(len(a), len(b))):
            la = a[i] if i < len(a) else None
            lb = b[i] if i < len(b) else None
            if la != lb:
                changed_lines += 1
        if a != b:
            diffs.append({"file": rel, "changed": True})
    if total_lines == 0:
        return 100.0, diffs
    return max(0.0, 100.0 * (1 - changed_lines / total_lines)), diffs


# --- LLM-judged quality (shared, applies to the three marker-based skills) -

def call_judge(prompt):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    body = json.dumps({
        "model": JUDGE_MODEL,
        "max_tokens": 200,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    text = "".join(b.get("text", "") for b in data.get("content", []))
    try:
        return float(text.strip().split()[0])
    except (ValueError, IndexError):
        return None


def score_quality(labels, run_dir, rubric_text, skill):
    marker_items = [i for i in labels if i.get("expect") in ("comment", "annotate", "flag")][:JUDGE_SAMPLE_SIZE]
    if not marker_items:
        return None, []
    scores, details = [], []
    for item in marker_items:
        try:
            lines = read_lines(run_dir, item["file"])
        except FileNotFoundError:
            continue
        line_idx = find_function_line(lines, item["name"])
        if line_idx is None:
            continue
        lo = max(0, line_idx - WINDOW)
        hi = min(len(lines), line_idx + WINDOW + 5)
        snippet = "".join(lines[lo:hi])
        prompt = (
            f"Rubric (skill: {skill}):\n{rubric_text}\n\n"
            f"Rate ONLY the marker quality in this snippet on a 0-100 scale "
            f"per the '{skill}' quality criteria above. Reply with just the number.\n\n{snippet}"
        )
        result = call_judge(prompt)
        if result is not None:
            scores.append(result)
            details.append({"file": item["file"], "name": item["name"], "judge_score": result})
    if not scores:
        return None, details
    return sum(scores) / len(scores), details


# --- main -------------------------------------------------------------------

def load_labels(path, skill):
    with open(path) as f:
        data = json.load(f)
    key = "imports" if skill == "import-sorter-cleaner" else \
          "files" if skill == "license-header-injector" else "functions"
    return data[key]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", required=True, choices=[
        "code-commenter", "type-annotator", "license-header-injector",
        "error-handling-auditor", "import-sorter-cleaner",
    ])
    ap.add_argument("--labels", required=True)
    ap.add_argument("--run-a", required=True, help="post-run corpus directory")
    ap.add_argument("--run-b", help="second post-run copy, for idempotency check")
    ap.add_argument("--rubric", default=os.path.join(os.path.dirname(__file__), "rubric.md"))
    ap.add_argument("--out", default="eval/result.json")
    ap.add_argument("--log", default="eval/log.md")
    ap.add_argument("--note", default="", help="one-line description of the SKILL.md edit tried")
    args = ap.parse_args()

    labels = load_labels(args.labels, args.skill)
    rubric_text = ""
    if os.path.exists(args.rubric):
        with open(args.rubric) as f:
            rubric_text = f.read()

    if args.skill == "license-header-injector":
        accuracy, misses = score_accuracy_license_header(labels, args.run_a)
        idempotency_files = [i["file"] for i in labels]
        quality, judge_details = None, []
    elif args.skill == "import-sorter-cleaner":
        accuracy, misses = score_accuracy_import_sorter(labels, args.run_a)
        idempotency_files = [i["file"] for i in labels]
        quality, judge_details = None, []
    else:
        detector = DETECTORS[args.skill]
        accuracy, misses = score_accuracy_by_name(labels, args.run_a, detector)
        idempotency_files = [i["file"] for i in labels]
        quality, judge_details = score_quality(labels, args.run_a, rubric_text, args.skill)

    idempotency = None
    diffs = []
    if args.run_b:
        idempotency, diffs = score_idempotency(args.run_a, args.run_b, idempotency_files)

    components = {"accuracy": (accuracy, 0.40 if quality is not None else 0.80)}
    if idempotency is not None:
        components["idempotency"] = (idempotency, 0.20)
    if quality is not None:
        components["quality"] = (quality, 0.40)

    weight_sum = sum(w for _, w in components.values())
    final = sum(v * w for v, w in components.values()) / weight_sum if weight_sum else 0.0

    result = {
        "skill": args.skill,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "note": args.note,
        "final_score": round(final, 2),
        "components": {k: round(v, 2) for k, (v, _) in components.items()},
        "accuracy_misses": misses,
        "idempotency_diffs": diffs,
        "judge_details": judge_details,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)

    log_line = (
        f"- {result['timestamp']} — [{args.skill}] **{result['final_score']}** "
        f"(accuracy={components['accuracy'][0]:.1f}"
        + (f", idempotency={components['idempotency'][0]:.1f}" if "idempotency" in components else "")
        + (f", quality={components['quality'][0]:.1f}" if "quality" in components else "")
        + f") — {args.note or 'no note'}\n"
    )
    with open(args.log, "a") as f:
        f.write(log_line)

    print(json.dumps(result, indent=2))
    print(f"\nFinal score: {result['final_score']}", file=sys.stderr)


if __name__ == "__main__":
    main()
