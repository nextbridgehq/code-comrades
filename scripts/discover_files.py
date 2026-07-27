#!/usr/bin/env python3
"""Deterministic file discovery for code-comrades dispatch.

Walks a directory tree, filters by extension/exclude-dir/exclude-pattern/
size/gitignore/binary-content, optionally narrows the result to a git
scope (changed/staged/since a ref), and prints one relative path per line.
Contains no skill-specific logic — the orchestrator resolves a skill's
batch.yaml into CLI flags before invoking this script.
"""
import argparse
import fnmatch
import os
import subprocess
import sys

CODE_EXTENSIONS = [
    "py", "js", "ts", "tsx", "jsx", "go", "rs", "java", "kt", "kts",
    "rb", "php", "swift", "c", "cpp", "cc", "h", "hpp", "cs", "scala",
    "sc", "ex", "exs", "hs", "lua", "r", "R", "sh", "bash", "zsh",
    "dart", "zig", "ml", "mli", "pl", "pm",
]

DEFAULT_EXCLUDE_DIRS = [
    "node_modules", "vendor", "dist", "build", ".build", "target",
    "__pycache__", ".venv", "venv", "env", ".env", ".git", ".hg",
    ".svn", "coverage", ".nyc_output", ".pytest_cache", ".mypy_cache",
    ".tox", "eggs", "site-packages", "bower_components",
    "jspm_packages", ".next", ".nuxt", "out", ".output", "pkg",
    "Pods", "DerivedData", ".gradle", ".idea", ".vscode", "CMakeFiles",
]

DEFAULT_EXCLUDE_DIR_GLOBS = ["*.egg-info", "cmake-build-*"]

DEFAULT_EXCLUDE_PATTERNS = [
    "*.min.*", "*.generated.*", "*.pb.go", "*.pb.py", "*_pb2.py",
    "*_pb2_grpc.py", "*.g.dart", "*.freezed.dart", "*.lock",
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "Cargo.lock",
    "poetry.lock", "Gemfile.lock", "composer.lock", "*.map",
    "*.chunk.*", "bundle.*",
]


def resolve_extensions(spec):
    """Resolve an --extensions value ("default" or a comma list) to a set."""
    if spec.strip().lower() == "default":
        return set(CODE_EXTENSIONS)
    return {e.strip().lstrip(".") for e in spec.split(",") if e.strip()}


def resolve_exclude_patterns(mode, patterns):
    """Merge a skill's exclude patterns with the plugin-wide baseline per mode."""
    if mode == "override":
        return list(patterns)
    return DEFAULT_EXCLUDE_PATTERNS + list(patterns)


def parse_csv(spec):
    """Split a comma-separated string into trimmed, non-empty tokens.

    Args:
        spec: Comma-separated string, or falsy for an empty result.

    Returns:
        List of trimmed tokens with empty entries dropped.
    """
    if not spec:
        return []
    return [p.strip() for p in spec.split(",") if p.strip()]


def is_gitignored(filepath, root):
    """Check whether git considers a path ignored, failing open on errors.

    If git is missing or the check times out, the path is treated as not
    gitignored rather than aborting the scan.

    Args:
        filepath: Path to check, absolute or relative to root.
        root: Directory to run `git check-ignore` in.

    Returns:
        True if git reports the path as ignored, False otherwise.
    """
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", filepath],
            cwd=root, capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


class GitScopeError(RuntimeError):
    """Raised when a requested git scope cannot be resolved.

    Deliberately fatal rather than fail-open: if the caller asked for
    "only changed files" and git can't answer, silently widening the run
    to every file in the tree is the opposite of what they asked for.
    """


def _git_out(root, args, timeout=15):
    """Run a git command, returning stdout as text or raising GitScopeError.

    Args:
        root: Directory to run git in.
        args: Git arguments, excluding the leading "git".
        timeout: Seconds before the call is abandoned.

    Returns:
        Raw stdout decoded as UTF-8, with undecodable bytes preserved via
        surrogateescape so exotic filenames survive the round trip.

    Raises:
        GitScopeError: If git is missing, times out, or exits non-zero.
    """
    try:
        result = subprocess.run(
            ["git"] + args, cwd=root, capture_output=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise GitScopeError("git is not installed or not on PATH")
    except subprocess.TimeoutExpired:
        raise GitScopeError(f"git {' '.join(args)} timed out after {timeout}s")
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GitScopeError(detail or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", "surrogateescape")


def _git_paths(root, args):
    """Run a NUL-terminated git path command and return its paths.

    NUL separation (-z) is what makes this safe for filenames containing
    spaces, newlines, or non-ASCII characters, which git would otherwise
    quote and escape in its default output.
    """
    return [p for p in _git_out(root, args).split("\0") if p]


def _has_commits(root):
    """Check whether the repo has at least one commit (i.e. HEAD resolves)."""
    try:
        _git_out(root, ["rev-parse", "--verify", "HEAD"])
        return True
    except GitScopeError:
        return False


def git_scoped_paths(root, scope, since_ref=None):
    """Resolve a git scope to the set of paths it covers, relative to root.

    Paths are returned relative to `root` (not the repo root), so they can
    be intersected directly with the relative paths discover_files yields.
    Deleted files are excluded — a batch skill has nothing to edit in a
    file that no longer exists.

    Args:
        root: Directory to scope from; may be a subdirectory of the repo.
        scope: One of "all" (no scoping), "changed" (uncommitted work:
            staged + unstaged + untracked), "staged" (index only), or
            "since" (everything touched since diverging from since_ref,
            committed or not).
        since_ref: Git ref to compare against; required when scope="since".

    Returns:
        A set of relative paths, or None when scope is "all" (meaning no
        scoping should be applied at all — distinct from an empty set,
        which means "in scope: nothing").

    Raises:
        GitScopeError: If scope is unknown, root isn't a git repo, the ref
            doesn't resolve, or git is unavailable.
    """
    if scope == "all":
        return None
    if scope not in ("changed", "staged", "since"):
        raise GitScopeError(f"unknown git scope: {scope}")

    _git_out(root, ["rev-parse", "--is-inside-work-tree"])
    has_commits = _has_commits(root)

    if scope == "staged":
        if not has_commits:
            # No HEAD to diff against; everything in the index is staged.
            return set(_git_paths(root, ["ls-files", "-z"]))
        return set(_git_paths(
            root, ["diff", "--name-only", "-z", "--relative", "--diff-filter=d", "--cached"]
        ))

    if scope == "changed":
        untracked = _git_paths(root, ["ls-files", "-z", "--others", "--exclude-standard"])
        if not has_commits:
            return set(_git_paths(root, ["ls-files", "-z"])) | set(untracked)
        tracked = _git_paths(
            root, ["diff", "--name-only", "-z", "--relative", "--diff-filter=d", "HEAD"]
        )
        return set(tracked) | set(untracked)

    # scope == "since"
    if not since_ref:
        raise GitScopeError("--since-ref is required when --git-scope is 'since'")
    if not has_commits:
        raise GitScopeError("cannot resolve 'since' scope: repository has no commits")
    base = _git_out(root, ["merge-base", since_ref, "HEAD"]).strip()
    if not base:
        raise GitScopeError(f"no merge base between {since_ref} and HEAD")
    # Diff base -> working tree (not base...HEAD): this catches work that is
    # committed on the branch *and* work still uncommitted, which is what
    # "everything I've touched since branching" means to a caller.
    tracked = _git_paths(
        root, ["diff", "--name-only", "-z", "--relative", "--diff-filter=d", base]
    )
    # git diff only reports tracked files, so a new file the user created but
    # hasn't staged is invisible to it — yet it is unambiguously work done
    # since the branch point, and often the file that most needs processing.
    untracked = _git_paths(root, ["ls-files", "-z", "--others", "--exclude-standard"])
    return set(tracked) | set(untracked)


def matches_any_pattern(relative_path, filename, patterns):
    """Check a file against exclude globs by both filename and relative path.

    Matching both forms lets patterns like "*.min.js" (filename-only) and
    "src/*.py" (path-aware) coexist in the same pattern list.

    Args:
        relative_path: Path relative to the scan root, forward-slash separated.
        filename: Bare filename component.
        patterns: Glob patterns to test against.

    Returns:
        True if any pattern matches either the filename or the relative path.
    """
    return any(
        fnmatch.fnmatch(filename, p) or fnmatch.fnmatch(relative_path, p)
        for p in patterns
    )


def is_binary(filepath):
    """Heuristically detect binary content by sniffing for a NUL byte.

    Unreadable files are treated as binary so callers skip them instead of
    failing on permission errors or a file disappearing mid-walk.

    Args:
        filepath: Path to the file to inspect.

    Returns:
        True if a NUL byte appears in the first 1KB, or the file can't be read.
    """
    try:
        with open(filepath, "rb") as f:
            return b"\x00" in f.read(1024)
    except OSError:
        return True


def discover_files(root, extensions, exclude_dirs, exclude_dir_globs,
                    exclude_patterns, max_size_kb, respect_gitignore,
                    scoped_paths=None):
    """Walk root and collect relative paths that survive all filters.

    Filters apply in order, each an early-exit check: directory pruning
    (name and glob), extension allowlist, exclude patterns, git scope,
    gitignore status, max size, then binary-content sniffing.

    Args:
        root: Directory to walk.
        extensions: Allowed file extensions, without leading dots.
        exclude_dirs: Directory names to prune from the walk.
        exclude_dir_globs: Directory name globs to prune from the walk.
        exclude_patterns: Filename/path globs to exclude.
        max_size_kb: Maximum file size in KB; larger files are skipped.
        respect_gitignore: If True, skip files git would ignore.
        scoped_paths: Optional set of relative paths (from git_scoped_paths)
            to intersect the walk with. None means no scoping; an empty set
            means nothing is in scope and no file survives.

    Returns:
        Sorted list of relative paths (forward-slash separated).
    """
    discovered = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in exclude_dirs
            and not any(fnmatch.fnmatch(d, g) for g in exclude_dir_globs)
        ]
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            relative_path = os.path.relpath(filepath, root).replace(os.sep, "/")
            ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
            if ext not in extensions:
                continue
            if matches_any_pattern(relative_path, filename, exclude_patterns):
                continue
            if scoped_paths is not None and relative_path not in scoped_paths:
                continue
            if respect_gitignore and is_gitignored(filepath, root):
                continue
            try:
                if os.path.getsize(filepath) / 1024 > max_size_kb:
                    continue
            except OSError:
                continue
            if is_binary(filepath):
                continue
            discovered.append(relative_path)
    return sorted(discovered)


def main(argv=None):
    """CLI entry point: parse arguments, discover files, print one path per line.

    Args:
        argv: Argument list to parse instead of sys.argv; primarily for tests.

    Returns:
        Exit code, always 0.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True)
    parser.add_argument("--extensions", default="default")
    parser.add_argument("--exclude-patterns", default="")
    parser.add_argument("--exclude-patterns-mode", choices=["extend", "override"], default="extend")
    parser.add_argument("--max-size-kb", type=float, default=500)
    parser.add_argument("--no-gitignore", action="store_true")
    parser.add_argument(
        "--git-scope", choices=["all", "changed", "staged", "since"], default="all",
        help="Narrow discovery to files git reports as in scope. "
             "changed=uncommitted work, staged=index only, since=touched since --since-ref.",
    )
    parser.add_argument("--since-ref", default=None, help="Ref for --git-scope since.")
    args = parser.parse_args(argv)

    extensions = resolve_extensions(args.extensions)
    exclude_patterns = resolve_exclude_patterns(
        args.exclude_patterns_mode, parse_csv(args.exclude_patterns)
    )

    try:
        scoped_paths = git_scoped_paths(args.path, args.git_scope, args.since_ref)
    except GitScopeError as e:
        # Fail closed: a caller who asked for a narrow scope must never be
        # silently handed the whole tree instead.
        print(f"ERROR: cannot resolve --git-scope {args.git_scope}: {e}", file=sys.stderr)
        return 2

    files = discover_files(
        root=args.path,
        extensions=extensions,
        exclude_dirs=DEFAULT_EXCLUDE_DIRS,
        exclude_dir_globs=DEFAULT_EXCLUDE_DIR_GLOBS,
        exclude_patterns=exclude_patterns,
        max_size_kb=args.max_size_kb,
        respect_gitignore=not args.no_gitignore,
        scoped_paths=scoped_paths,
    )
    for f in files:
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
