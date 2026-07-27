#!/usr/bin/env python3
"""Manifest tracking for code-comrades dispatch runs.

The manifest records, per discovered file, the outcome of the last
worker dispatch for a given (skill, path, config) run — enabling resume
after an interrupted run without re-dispatching completed files.
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

VALID_STATUSES = {"pending", "done", "skipped", "error", "reverted"}


def canonical_key(repo_relative_path, config, scope=None):
    """Derives a stable, short identifier for a (path, config, scope) run.

    Config is included so that changing options (e.g. audience, style)
    yields a distinct manifest instead of silently reusing stale results
    from a run with different settings. Scope is included for the same
    reason — an incremental run over changed files must not share state
    with a full run over the same path.

    A None scope is omitted from the payload entirely rather than encoded
    as null, so keys generated before scopes existed still resolve to the
    same manifest after upgrading.

    Args:
        repo_relative_path: File path relative to the repo root.
        config: Skill configuration dict; serialized with sorted keys so
            the digest is independent of key ordering.
        scope: Optional git scope identifier, e.g. "changed" or
            "since:main". None (the default) means an unscoped full run.

    Returns:
        A 16-character hex digest suitable for use in a filename.
    """
    payload = {"path": repo_relative_path, "config": config}
    if scope is not None:
        payload["scope"] = scope
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def manifest_path(repo_root, skill, repo_relative_path, config, scope=None):
    """Builds the on-disk manifest path for a given skill/path/config/scope run.

    Returns:
        Path under `<repo_root>/.claude-batch-manifest/`, keyed by
        `canonical_key` so distinct runs don't collide.
    """
    key = canonical_key(repo_relative_path, config, scope)
    return os.path.join(repo_root, ".claude-batch-manifest", f"{skill}-{key}.json")


def load_manifest(path):
    """Loads a manifest file, returning None if none exists yet.

    Returns:
        The parsed manifest dict, or None on a fresh (never-run) target.
    """
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(path, data):
    """Writes the manifest to disk, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def new_manifest(skill, repo_relative_path, config, files, scope=None):
    """Builds the initial manifest structure with every file marked pending.

    Args:
        skill: Name of the skill being dispatched.
        repo_relative_path: The target path passed to the dispatch run.
        config: Skill configuration in effect for this run.
        files: Discovered files to track.
        scope: Optional git scope identifier this run was narrowed to.

    Returns:
        A manifest dict with one `files` entry per discovered file, each
        initialized to status "pending".
    """
    return {
        "skill": skill,
        "target_path": repo_relative_path,
        "config": config,
        "scope": scope,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            f: {"status": "pending", "changed": False, "reason": None, "summary": None}
            for f in files
        },
    }


def reconcile_to_scope(manifest, files):
    """Re-point an existing scoped manifest at a freshly computed file set.

    Incremental runs recompute their scope from git on every invocation, so
    git — not the manifest — is the authority on what needs processing:

    - Files git no longer reports in scope are dropped from tracking.
    - Files in scope but not yet tracked are added as pending.
    - Files in scope that are already tracked are **reset to pending**,
      even if previously "done". Being in a changed-file scope means the
      file was edited since it was last processed, so its prior result is
      stale by definition — carrying it over would skip exactly the file
      the user invoked incremental mode to catch.

    The tradeoff is that an interrupted incremental run restarts rather
    than resumes. That's cheap: incremental sets are small, and batchable
    skills are idempotent, so re-processed files report no change.

    Args:
        manifest: The manifest to reconcile in place.
        files: The freshly discovered in-scope files.

    Returns:
        The updated manifest (the same object passed in).
    """
    manifest["files"] = {
        f: {"status": "pending", "changed": False, "reason": None, "summary": None}
        for f in files
    }
    return manifest


def files_to_process(manifest, mode="resume", max_files=None):
    """Selects which files should be (re-)dispatched to workers.

    In "fresh" mode every tracked file is returned regardless of prior
    status. Otherwise only files still "pending" or previously "error"
    are returned — "done" and "skipped" files are left alone, which is
    what makes resuming an interrupted run cheap.

    Args:
        manifest: The loaded manifest.
        mode: "resume" (default) or "fresh".
        max_files: Optional cap on the number of files returned.

    Returns:
        List of file paths to dispatch, in manifest insertion order.
    """
    if mode == "fresh":
        candidates = list(manifest["files"].keys())
    else:
        candidates = [
            f for f, entry in manifest["files"].items()
            if entry["status"] in ("pending", "error", "reverted")
        ]
    if max_files is not None:
        candidates = candidates[:max_files]
    return candidates


def record_results(manifest, dispatched_files, results):
    """Merges worker results into the manifest by file path.

    A dispatched file with no matching entry in `results` is recorded as
    "error" — this covers a worker that crashed or otherwise failed to
    emit a result block, so it isn't silently dropped from the manifest.

    Args:
        manifest: The manifest to update in place.
        dispatched_files: Paths sent to workers this round.
        results: Result dicts keyed by "file", each carrying status,
            changed, reason, and summary fields.

    Returns:
        The updated manifest (the same object passed in).
    """
    results_by_file = {r["file"]: r for r in results}
    for f in dispatched_files:
        if f in results_by_file:
            r = results_by_file[f]
            manifest["files"][f] = {
                "status": r["status"],
                "changed": bool(r.get("changed", False)),
                "reason": r.get("reason"),
                "summary": r.get("summary"),
            }
        else:
            manifest["files"][f] = {
                "status": "error",
                "changed": False,
                "reason": "worker produced no result block",
                "summary": None,
            }
    return manifest


def summarize(manifest):
    """Tallies file statuses and change count for a manifest.

    Returns:
        Dict with per-status counts, total changed files, and file total.
    """
    counts = {"pending": 0, "done": 0, "skipped": 0, "error": 0, "reverted": 0}
    changed = 0
    for entry in manifest["files"].values():
        counts[entry["status"]] += 1
        if entry.get("changed"):
            changed += 1
    return {"counts": counts, "changed": changed, "total": len(manifest["files"])}


def _group_by_reason(manifest, status):
    """Group files with a given status by their reason string.

    Returns:
        Dict of reason -> sorted list of file paths. A missing reason is
        bucketed under "unspecified" rather than dropped, so no file in a
        report is ever unaccounted for.
    """
    groups = {}
    for path, entry in manifest["files"].items():
        if entry["status"] != status:
            continue
        groups.setdefault(entry.get("reason") or "unspecified", []).append(path)
    return {reason: sorted(paths) for reason, paths in sorted(groups.items())}


def render_markdown_report(manifest, verify_report=None):
    """Render a manifest as a human-reviewable markdown run report.

    The manifest already holds every fact a reader needs after a batch
    run; this presents them in review order — what changed, what broke,
    what was skipped and why — so a 500-file run doesn't have to be read
    as raw JSON.

    Args:
        manifest: The loaded manifest.
        verify_report: Optional parsed output of `verify.py report`, whose
            `failures` list carries the failing gate name and output tail
            per file — detail the manifest itself doesn't record.

    Returns:
        The report as a markdown string.
    """
    s = summarize(manifest)
    counts = s["counts"]
    scope = manifest.get("scope")
    lines = [
        f"# Batch run report — `{manifest['skill']}`",
        "",
        f"- **Target:** `{manifest['target_path']}`",
        f"- **Scope:** {'`' + scope + '` (incremental)' if scope else 'full run'}",
        f"- **Started:** {manifest['created_at']}",
        f"- **Reported:** {datetime.now(timezone.utc).isoformat()}",
        f"- **Files tracked:** {s['total']}",
        "",
        "## Outcome",
        "",
        "| Status | Files |",
        "| --- | --- |",
        f"| Changed | {s['changed']} |",
        f"| Done (no change needed) | {counts['done'] - s['changed']} |",
        f"| Skipped | {counts['skipped']} |",
        f"| Reverted by verification | {counts['reverted']} |",
        f"| Errors | {counts['error']} |",
        f"| Still pending | {counts['pending']} |",
        "",
    ]

    changed = sorted(f for f, e in manifest["files"].items() if e.get("changed"))
    if changed:
        lines += ["## Changed files", ""]
        for f in changed:
            summary = manifest["files"][f].get("summary")
            lines.append(f"- `{f}`" + (f" — {summary}" if summary else ""))
        lines.append("")

    if counts["error"]:
        lines += ["## Errors", ""]
        for reason, paths in _group_by_reason(manifest, "error").items():
            lines.append(f"**{reason}** ({len(paths)})")
            lines += [f"- `{p}`" for p in paths]
            lines.append("")

    if counts["reverted"]:
        lines += ["## Reverted by verification", ""]
        failures = {f["file"]: f for f in (verify_report or {}).get("failures", [])}
        for f in sorted(p for p, e in manifest["files"].items() if e["status"] == "reverted"):
            detail = failures.get(f)
            if detail:
                lines.append(f"- `{f}` — gate `{detail.get('gate', 'unknown')}`")
                tail = (detail.get("output") or "").strip()
                if tail:
                    lines += ["", "  ```", *[f"  {ln}" for ln in tail.splitlines()[-10:]], "  ```", ""]
            else:
                lines.append(f"- `{f}`")
        lines.append("")

    if counts["skipped"]:
        lines += ["## Skipped", ""]
        for reason, paths in _group_by_reason(manifest, "skipped").items():
            lines.append(f"- **{reason}** — {len(paths)} file(s)")
        lines.append("")

    if counts["pending"]:
        lines += [
            "## Pending",
            "",
            f"{counts['pending']} file(s) were never dispatched — the run was capped, "
            "interrupted, or aborted. Re-run the same command to resume.",
            "",
        ]

    lines += ["---", "", "Review the changes with `git diff` before committing."]
    return "\n".join(lines) + "\n"


def default_report_path(manifest_file_path, ext):
    """Derive a report path sitting next to its manifest.

    Args:
        manifest_file_path: Path to the manifest JSON.
        ext: Report extension, e.g. "md" or "json".

    Returns:
        The manifest path with its .json suffix replaced by `-report.<ext>`.
    """
    base = manifest_file_path[:-len(".json")] if manifest_file_path.endswith(".json") else manifest_file_path
    return f"{base}-report.{ext}"


def _cmd_report(args):
    """Writes a markdown or JSON run report derived from the manifest."""
    manifest = load_manifest(args.manifest_path)
    if manifest is None:
        print(f"ERROR: no manifest at {args.manifest_path}", file=sys.stderr)
        return 1

    verify_report = None
    if args.verify_report_file:
        try:
            with open(args.verify_report_file, "r", encoding="utf-8") as f:
                verify_report = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            # A missing or malformed verify report degrades the report's
            # detail; it must not cost the user the whole report.
            print(f"WARNING: ignoring --verify-report-file: {e}", file=sys.stderr)

    if args.format == "json":
        body = json.dumps({
            "skill": manifest["skill"],
            "target_path": manifest["target_path"],
            "config": manifest["config"],
            "created_at": manifest["created_at"],
            "summary": summarize(manifest),
            "files": manifest["files"],
        }, indent=2) + "\n"
    else:
        body = render_markdown_report(manifest, verify_report)

    out = args.output or default_report_path(args.manifest_path, args.format)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    print(json.dumps({"report_path": out, "format": args.format, "summary": summarize(manifest)}))
    return 0


def _cmd_init(args):
    """Creates a manifest for a dispatch run, or reuses an existing one.

    An existing manifest at the same (skill, path, config, scope) key is
    reused unless `--fresh` is passed, which is what allows a rerun to
    skip files already marked done. Scoped (incremental) runs get their
    own manifest and are reconciled against the freshly discovered file
    set rather than resumed — see `reconcile_to_scope`.
    """
    config = json.loads(args.config_json)
    with open(args.files_file, "r", encoding="utf-8") as f:
        files = [line.strip() for line in f if line.strip()]

    scope = args.scope or None
    path = manifest_path(args.repo_root, args.skill, args.target_path, config, scope)
    existing = None if args.fresh else load_manifest(path)

    if existing is None:
        manifest = new_manifest(args.skill, args.target_path, config, files, scope)
        created = True
    elif scope is not None:
        manifest = reconcile_to_scope(existing, files)
        created = False
    else:
        manifest = existing
        created = False

    save_manifest(path, manifest)
    print(json.dumps({
        "manifest_path": path,
        "created": created,
        "scope": scope,
        "reconciled": bool(existing is not None and scope is not None),
        "summary": summarize(manifest),
    }))


def _cmd_pending(args):
    """Prints files still needing dispatch, one per line, up to `--max`."""
    manifest = load_manifest(args.manifest_path)
    if manifest is None:
        print(f"ERROR: no manifest at {args.manifest_path}", file=sys.stderr)
        return 1
    for f in files_to_process(manifest, mode="resume", max_files=args.max):
        print(f)
    return 0


def _cmd_record(args):
    """Merges a batch of worker results back into the manifest on disk."""
    manifest = load_manifest(args.manifest_path)
    if manifest is None:
        print(f"ERROR: no manifest at {args.manifest_path}", file=sys.stderr)
        return 1
    with open(args.dispatched_files_file, "r", encoding="utf-8") as f:
        dispatched = [line.strip() for line in f if line.strip()]
    with open(args.results_file, "r", encoding="utf-8") as f:
        results = json.load(f)
    manifest = record_results(manifest, dispatched, results)
    save_manifest(args.manifest_path, manifest)
    print(json.dumps({"summary": summarize(manifest)}))
    return 0


def _cmd_summary(args):
    """Prints status counts for a manifest without modifying it."""
    manifest = load_manifest(args.manifest_path)
    if manifest is None:
        print(f"ERROR: no manifest at {args.manifest_path}", file=sys.stderr)
        return 1
    print(json.dumps({"summary": summarize(manifest)}))
    return 0


def main(argv=None):
    """Parses CLI arguments and dispatches to the selected subcommand handler.

    Args:
        argv: Argument list to parse; defaults to sys.argv when None.

    Returns:
        Process exit code returned by the subcommand handler, or 0 if falsy.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--repo-root", required=True)
    p_init.add_argument("--skill", required=True)
    p_init.add_argument("--target-path", required=True)
    p_init.add_argument("--config-json", required=True)
    p_init.add_argument("--files-file", required=True)
    p_init.add_argument("--scope", default=None,
                        help="Git scope identifier for an incremental run, e.g. 'changed' "
                             "or 'since:main'. Omit for a full run.")
    p_init.add_argument("--fresh", action="store_true")
    p_init.set_defaults(func=_cmd_init)

    p_pending = sub.add_parser("pending")
    p_pending.add_argument("--manifest-path", required=True)
    p_pending.add_argument("--max", type=int, default=None)
    p_pending.set_defaults(func=_cmd_pending)

    p_record = sub.add_parser("record")
    p_record.add_argument("--manifest-path", required=True)
    p_record.add_argument("--dispatched-files-file", required=True)
    p_record.add_argument("--results-file", required=True)
    p_record.set_defaults(func=_cmd_record)

    p_summary = sub.add_parser("summary")
    p_summary.add_argument("--manifest-path", required=True)
    p_summary.set_defaults(func=_cmd_summary)

    p_report = sub.add_parser("report")
    p_report.add_argument("--manifest-path", required=True)
    p_report.add_argument("--format", choices=["md", "json"], default="md")
    p_report.add_argument("--output", default=None)
    p_report.add_argument("--verify-report-file", default=None)
    p_report.set_defaults(func=_cmd_report)

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
