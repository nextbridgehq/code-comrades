"""Tests for discover_files: extension resolution, exclusion rules (dirs,
dir globs, filename patterns), size/binary filtering, gitignore integration,
output ordering, and the CLI entry point.

Most cases operate on a real filesystem via tmp_path; the gitignore case
additionally requires an actual `git init`, and the CLI case shells out to
the script via subprocess rather than calling df.discover_files directly.
"""

import os
import subprocess
import sys

import discover_files as df


def write(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_resolve_extensions_default_returns_full_registry():
    """The 'default' keyword must stay a live alias for CODE_EXTENSIONS, not
    a copy that can drift out of sync as the registry grows."""
    assert df.resolve_extensions("default") == set(df.CODE_EXTENSIONS)


def test_resolve_extensions_custom_list_strips_dots_and_whitespace():
    """User-supplied extension lists (e.g. from a CLI flag) may be copied
    from elsewhere with leading dots or stray spaces; both must be normalized."""
    assert df.resolve_extensions(" .py, js ,ts") == {"py", "js", "ts"}


def test_discovers_matching_extension_files(tmp_path):
    """Files outside the requested extension set are dropped even when no
    other exclusion rule applies."""
    write(tmp_path / "a.py", "print(1)\n")
    write(tmp_path / "b.txt", "not code\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"py"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=[], max_size_kb=500,
        respect_gitignore=False,
    )
    assert files == ["a.py"]


def test_excludes_junk_directories(tmp_path):
    """DEFAULT_EXCLUDE_DIRS must prune traversal into node_modules entirely,
    not just filter its files out after the fact."""
    write(tmp_path / "node_modules" / "pkg.js", "module.exports = {}\n")
    write(tmp_path / "src" / "app.js", "console.log(1)\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"js"}, exclude_dirs=df.DEFAULT_EXCLUDE_DIRS,
        exclude_dir_globs=df.DEFAULT_EXCLUDE_DIR_GLOBS, exclude_patterns=[],
        max_size_kb=500, respect_gitignore=False,
    )
    assert files == ["src/app.js"]


def test_excludes_dir_glob_pattern(tmp_path):
    """Directory names like "foo.egg-info" vary per package and can't be
    matched by DEFAULT_EXCLUDE_DIRS' literal names, so glob-based dir
    exclusion must catch them independently."""
    write(tmp_path / "foo.egg-info" / "PKG-INFO", "junk\n")
    write(tmp_path / "foo.egg-info" / "mod.py", "x = 1\n")
    write(tmp_path / "real.py", "x = 1\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"py"}, exclude_dirs=df.DEFAULT_EXCLUDE_DIRS,
        exclude_dir_globs=df.DEFAULT_EXCLUDE_DIR_GLOBS, exclude_patterns=[],
        max_size_kb=500, respect_gitignore=False,
    )
    assert files == ["real.py"]


def test_exclude_patterns_extend_adds_to_baseline(tmp_path):
    """The 'extend' mode must union custom patterns with
    DEFAULT_EXCLUDE_PATTERNS rather than replacing them."""
    write(tmp_path / "bundle.min.js", "junk\n")   # matches DEFAULT_EXCLUDE_PATTERNS
    write(tmp_path / "custom.skip.js", "junk\n")  # matches only the skill's own pattern
    write(tmp_path / "keep.js", "console.log(1)\n")
    patterns = df.resolve_exclude_patterns(mode="extend", patterns=["*.skip.js"])
    files = df.discover_files(
        root=str(tmp_path), extensions={"js"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=patterns, max_size_kb=500,
        respect_gitignore=False,
    )
    assert files == ["keep.js"]


def test_exclude_patterns_override_ignores_baseline(tmp_path):
    """The 'override' mode must discard DEFAULT_EXCLUDE_PATTERNS entirely,
    unlike 'extend' — the baseline should have no effect once overridden."""
    write(tmp_path / "bundle.min.js", "console.log(1)\n")  # would match baseline, but override drops it
    write(tmp_path / "custom.skip.js", "junk\n")
    patterns = df.resolve_exclude_patterns(mode="override", patterns=["*.skip.js"])
    files = df.discover_files(
        root=str(tmp_path), extensions={"js"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=patterns, max_size_kb=500,
        respect_gitignore=False,
    )
    assert files == ["bundle.min.js"]


def test_skips_files_over_size_limit(tmp_path):
    """max_size_kb must exclude oversized files rather than erroring or
    truncating them, so a single huge generated file can't derail a scan."""
    write(tmp_path / "big.py", "x = 1\n" * 100000)
    write(tmp_path / "small.py", "x = 1\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"py"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=[], max_size_kb=1,
        respect_gitignore=False,
    )
    assert files == ["small.py"]


def test_skips_binary_files(tmp_path):
    """A matching extension isn't sufficient — content sniffing must catch
    files that are actually binary (e.g. null bytes) despite the .py name."""
    with open(tmp_path / "data.py", "wb") as f:
        f.write(b"\x00\x01\x02binary")
    write(tmp_path / "real.py", "x = 1\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"py"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=[], max_size_kb=500,
        respect_gitignore=False,
    )
    assert files == ["real.py"]


def test_respects_gitignore(tmp_path):
    """respect_gitignore=True must defer to git's own ignore resolution
    rather than a hand-rolled pattern matcher; requires a real repo since
    git is the source of truth here."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    write(tmp_path / ".gitignore", "ignored.py\n")
    write(tmp_path / "ignored.py", "x = 1\n")
    write(tmp_path / "kept.py", "x = 1\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"py"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=[], max_size_kb=500,
        respect_gitignore=True,
    )
    assert files == ["kept.py"]


def test_output_sorted_relative_forward_slash_paths(tmp_path):
    """Output paths must be sorted and use forward slashes regardless of
    platform, since discover_files' output is meant to be diffed/consumed
    consistently across OSes."""
    write(tmp_path / "z" / "a.py", "x = 1\n")
    write(tmp_path / "a.py", "x = 1\n")
    files = df.discover_files(
        root=str(tmp_path), extensions={"py"}, exclude_dirs=[],
        exclude_dir_globs=[], exclude_patterns=[], max_size_kb=500,
        respect_gitignore=False,
    )
    assert files == ["a.py", "z/a.py"]


def test_cli_prints_one_path_per_line(tmp_path):
    """Exercises the CLI entry point as an external process (not a direct
    function call) to verify its actual stdout contract, including that an
    empty --exclude-patterns value doesn't break argument parsing."""
    write(tmp_path / "a.py", "x = 1\n")
    write(tmp_path / "b.js", "x = 1;\n")
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(__file__), "discover_files.py"),
         "--path", str(tmp_path), "--extensions", "py", "--exclude-patterns", "",
         "--exclude-patterns-mode", "extend", "--max-size-kb", "500", "--no-gitignore"],
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip().splitlines() == ["a.py"]
