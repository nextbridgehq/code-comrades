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

VALID_STATUSES = {"pending", "done", "skipped", "error"}


def canonical_key(repo_relative_path, config):
    """Derives a stable, short identifier for a (path, config) pair.

    Config is included so that changing options (e.g. audience, style)
    yields a distinct manifest instead of silently reusing stale results
    from a run with different settings.

    Args:
        repo_relative_path: File path relative to the repo root.
        config: Skill configuration dict; serialized with sorted keys so
            the digest is independent of key ordering.

    Returns:
        A 16-character hex digest suitable for use in a filename.
    """
    payload = json.dumps({"path": repo_relative_path, "config": config}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def manifest_path(repo_root, skill, repo_relative_path, config):
    """Builds the on-disk manifest path for a given skill/path/config run.

    Returns:
        Path under `<repo_root>/.claude-batch-manifest/`, keyed by
        `canonical_key` so distinct runs don't collide.
    """
    key = canonical_key(repo_relative_path, config)
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


def new_manifest(skill, repo_relative_path, config, files):
    """Builds the initial manifest structure with every file marked pending.

    Args:
        skill: Name of the skill being dispatched.
        repo_relative_path: The target path passed to the dispatch run.
        config: Skill configuration in effect for this run.
        files: Discovered files to track.

    Returns:
        A manifest dict with one `files` entry per discovered file, each
        initialized to status "pending".
    """
    return {
        "skill": skill,
        "target_path": repo_relative_path,
        "config": config,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {
            f: {"status": "pending", "changed": False, "reason": None, "summary": None}
            for f in files
        },
    }


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
            if entry["status"] in ("pending", "error")
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
    counts = {"pending": 0, "done": 0, "skipped": 0, "error": 0}
    changed = 0
    for entry in manifest["files"].values():
        counts[entry["status"]] += 1
        if entry.get("changed"):
            changed += 1
    return {"counts": counts, "changed": changed, "total": len(manifest["files"])}


def _cmd_init(args):
    """Creates a manifest for a dispatch run, or reuses an existing one.

    An existing manifest at the same (skill, path, config) key is reused
    unless `--fresh` is passed, which is what allows a rerun to skip
    files already marked done.
    """
    config = json.loads(args.config_json)
    with open(args.files_file, "r", encoding="utf-8") as f:
        files = [line.strip() for line in f if line.strip()]

    path = manifest_path(args.repo_root, args.skill, args.target_path, config)
    existing = None if args.fresh else load_manifest(path)

    if existing is None:
        manifest = new_manifest(args.skill, args.target_path, config, files)
        save_manifest(path, manifest)
        created = True
    else:
        manifest = existing
        created = False

    print(json.dumps({
        "manifest_path": path,
        "created": created,
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

    args = parser.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
