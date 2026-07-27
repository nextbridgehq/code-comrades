"""Tests for git-scoped (incremental) discovery and filesystem edge cases.

Everything here runs against a real `git init` repo on a real filesystem
rather than mocking subprocess — the whole point of these paths is that
they agree with what git actually reports, which a mock can't tell us.

Covers: the changed/staged/since scopes, their interaction with the
existing filters, fail-closed behavior on unresolvable scopes, and the
edge cases most likely to break a naive tree walk (unicode filenames,
symlinks, nested repos, hidden dirs).
"""

import os
import subprocess
import sys

import pytest

import discover_files as df


def write(path, content="x = 1\n"):
    """Write string content to a file, creating parent directories if necessary.

    Args:
        path: The filesystem path to write.
        content: The text content to write to the file.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def git(repo, *args):
    """Execute a git command synchronously in the specified repository.

    Args:
        repo: The path to the repository working tree.
        *args: The git command and its arguments.

    Returns:
        The CompletedProcess instance containing stdout and stderr.

    Raises:
        subprocess.CalledProcessError: If the git command returns a non-zero exit code.
    """
    return subprocess.run(
        ["git"] + list(args), cwd=str(repo), capture_output=True, text=True, check=True,
    )


def init_repo(path):
    """Create a git repo with committer identity set and one initial commit.

    Args:
        path: The directory path where the repository should be initialized.

    Returns:
        The repository path, for convenience in test setup.
    """
    os.makedirs(str(path), exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "commit", "-q", "--allow-empty", "-m", "root")
    return path


def discover(root, **overrides):
    """Call discover_files with permissive defaults, overridable per test.

    Args:
        root: The base directory to start discovery from.
        **overrides: Arguments to override the permissive default settings.

    Returns:
        A list of discovered file paths relative to the root.
    """
    kwargs = dict(
        root=str(root), extensions={"py"}, exclude_dirs=[], exclude_dir_globs=[],
        exclude_patterns=[], max_size_kb=500, respect_gitignore=False,
        scoped_paths=None,
    )
    kwargs.update(overrides)
    return df.discover_files(**kwargs)


# --- git scope resolution ---------------------------------------------------

def test_scope_all_returns_none_not_empty_set(tmp_path):
    """'all' must be distinguishable from 'nothing in scope': None disables
    filtering entirely, whereas an empty set would exclude every file."""
    assert df.git_scoped_paths(str(tmp_path), "all") is None


def test_scope_changed_includes_modified_and_untracked(tmp_path):
    """'changed' means all uncommitted work — a tracked file edited since HEAD
    and a brand-new untracked file are both work the user hasn't processed."""
    init_repo(tmp_path)
    write(tmp_path / "tracked.py")
    write(tmp_path / "untouched.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "add files")

    write(tmp_path / "tracked.py", "x = 2\n")
    write(tmp_path / "brand_new.py")

    scoped = df.git_scoped_paths(str(tmp_path), "changed")
    assert scoped == {"tracked.py", "brand_new.py"}
    assert "untouched.py" not in scoped


def test_scope_staged_excludes_unstaged_edits(tmp_path):
    """'staged' is the index only — the pre-commit hook use case, where an
    unstaged edit must not be picked up and rewritten behind the user's back."""
    init_repo(tmp_path)
    write(tmp_path / "a.py")
    write(tmp_path / "b.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "add")

    write(tmp_path / "a.py", "x = 2\n")
    git(tmp_path, "add", "a.py")
    write(tmp_path / "b.py", "x = 3\n")  # left unstaged

    assert df.git_scoped_paths(str(tmp_path), "staged") == {"a.py"}


def test_scope_changed_excludes_deleted_files(tmp_path):
    """A deleted file is 'changed' to git but has nothing on disk to edit;
    including it would hand workers a path that no longer exists."""
    init_repo(tmp_path)
    write(tmp_path / "gone.py")
    write(tmp_path / "kept.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "add")

    os.remove(str(tmp_path / "gone.py"))
    write(tmp_path / "kept.py", "x = 9\n")

    scoped = df.git_scoped_paths(str(tmp_path), "changed")
    assert "gone.py" not in scoped
    assert "kept.py" in scoped


def test_scope_since_uses_merge_base_not_raw_diff(tmp_path):
    """'since main' must mean 'what I touched on my branch', not 'everything
    that differs from main' — otherwise files main moved ahead on would be
    swept into the run despite the user never touching them."""
    init_repo(tmp_path)
    write(tmp_path / "base.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    git(tmp_path, "branch", "-M", "main")

    git(tmp_path, "checkout", "-q", "-b", "feature")
    write(tmp_path / "mine.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "feature work")

    # main moves ahead independently, touching a file the branch never saw.
    git(tmp_path, "checkout", "-q", "main")
    write(tmp_path / "theirs.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "main work")
    git(tmp_path, "checkout", "-q", "feature")

    scoped = df.git_scoped_paths(str(tmp_path), "since", since_ref="main")
    assert "mine.py" in scoped
    assert "theirs.py" not in scoped


def test_scope_since_includes_uncommitted_work(tmp_path):
    """Work in progress on the branch counts as 'since main' — a user who
    hasn't committed yet is exactly who wants to run a skill first."""
    init_repo(tmp_path)
    git(tmp_path, "branch", "-M", "main")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    write(tmp_path / "wip.py")

    scoped = df.git_scoped_paths(str(tmp_path), "since", since_ref="main")
    assert "wip.py" in scoped


def test_scope_changed_on_repo_without_commits(tmp_path):
    """A freshly-init'd repo has no HEAD to diff against; everything present
    is new work rather than an error."""
    os.makedirs(str(tmp_path), exist_ok=True)
    git(tmp_path, "init", "-q")
    write(tmp_path / "new.py")
    assert df.git_scoped_paths(str(tmp_path), "changed") == {"new.py"}


def test_scope_since_without_ref_raises(tmp_path):
    """'since' with no ref is a caller bug, not a reason to widen the scope."""
    init_repo(tmp_path)
    with pytest.raises(df.GitScopeError):
        df.git_scoped_paths(str(tmp_path), "since", since_ref=None)


def test_scope_unresolvable_ref_raises(tmp_path):
    """A typo'd ref must fail loudly rather than silently resolving to
    everything — the fail-closed contract the CLI depends on."""
    init_repo(tmp_path)
    with pytest.raises(df.GitScopeError):
        df.git_scoped_paths(str(tmp_path), "since", since_ref="no-such-ref")


def test_scope_outside_git_repo_raises(tmp_path):
    """Asking for a git scope where git has no answer is an error, not 'all'."""
    plain = tmp_path / "plain"
    os.makedirs(str(plain))
    with pytest.raises(df.GitScopeError):
        df.git_scoped_paths(str(plain), "changed")


def test_unknown_scope_raises(tmp_path):
    """An unrecognized scope identifier must raise rather than failing open."""
    with pytest.raises(df.GitScopeError):
        df.git_scoped_paths(str(tmp_path), "yesterday")


# --- scope composed with the rest of the filter chain -----------------------

def test_scoped_paths_intersects_with_other_filters(tmp_path):
    """Scope narrows; it never widens. A changed file that fails the
    extension filter stays excluded."""
    files = discover(
        tmp_path, scoped_paths={"a.py", "b.js"}, extensions={"py"},
    )
    write(tmp_path / "a.py")
    write(tmp_path / "b.js")
    files = discover(tmp_path, scoped_paths={"a.py", "b.js"}, extensions={"py"})
    assert files == ["a.py"]


def test_empty_scope_yields_no_files(tmp_path):
    """An empty scope means 'nothing changed' — a legitimate no-op result,
    not a signal to fall back to the whole tree."""
    write(tmp_path / "a.py")
    assert discover(tmp_path, scoped_paths=set()) == []


def test_scope_applies_to_subdirectory_target(tmp_path):
    """Paths from git and paths from the walk must share a frame of
    reference; a subdirectory target is where that quietly breaks."""
    init_repo(tmp_path)
    write(tmp_path / "src" / "a.py")
    write(tmp_path / "src" / "b.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "add")
    write(tmp_path / "src" / "a.py", "x = 2\n")

    scoped = df.git_scoped_paths(str(tmp_path / "src"), "changed")
    assert scoped == {"a.py"}
    assert discover(tmp_path / "src", scoped_paths=scoped) == ["a.py"]


# --- CLI fail-closed contract ----------------------------------------------

def test_cli_exits_2_on_unresolvable_scope(tmp_path):
    """The CLI's exit 2 is what dispatch keys off to abort the run. If this
    ever became exit 0 with a full file list, a scoped run would silently
    rewrite the entire repo."""
    init_repo(tmp_path)
    write(tmp_path / "a.py")
    script = os.path.join(os.path.dirname(os.path.abspath(df.__file__)), "discover_files.py")
    result = subprocess.run(
        [sys.executable, script, "--path", str(tmp_path),
         "--extensions", "py", "--git-scope", "since", "--since-ref", "nope"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert result.stdout.strip() == ""
    assert "ERROR" in result.stderr


def test_cli_default_scope_is_unfiltered(tmp_path):
    """Omitting --git-scope must behave exactly as it did before scoping
    existed — no git required, every matching file returned."""
    write(tmp_path / "a.py")
    script = os.path.join(os.path.dirname(os.path.abspath(df.__file__)), "discover_files.py")
    result = subprocess.run(
        [sys.executable, script, "--path", str(tmp_path),
         "--extensions", "py", "--no-gitignore"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert result.stdout.split() == ["a.py"]


# --- filesystem edge cases --------------------------------------------------

def test_unicode_filenames_survive_discovery(tmp_path):
    """Non-ASCII paths are common outside English-speaking teams and are a
    classic place for encoding assumptions to fail."""
    write(tmp_path / "café.py")
    write(tmp_path / "文件.py")
    write(tmp_path / "naïve_数据.py")
    assert discover(tmp_path) == sorted(["café.py", "文件.py", "naïve_数据.py"])


def test_unicode_filenames_survive_git_scope(tmp_path):
    """git quotes non-ASCII paths in its default output; NUL-separated (-z)
    output is what keeps them intact through the scope resolver."""
    init_repo(tmp_path)
    write(tmp_path / "café.py")
    write(tmp_path / "文件.py")
    scoped = df.git_scoped_paths(str(tmp_path), "changed")
    assert scoped == {"café.py", "文件.py"}


def test_filename_with_spaces_survives_git_scope(tmp_path):
    """Spaces are the cheapest way to prove the scope resolver isn't
    splitting git's output on whitespace."""
    init_repo(tmp_path)
    write(tmp_path / "my module.py")
    assert df.git_scoped_paths(str(tmp_path), "changed") == {"my module.py"}


def test_symlinked_file_is_discovered_once(tmp_path):
    """A symlink to a real source file resolves to readable content, so it
    is discoverable — but it must not be double-counted with its target."""
    write(tmp_path / "real.py")
    os.symlink(str(tmp_path / "real.py"), str(tmp_path / "link.py"))
    files = discover(tmp_path)
    assert files == ["link.py", "real.py"]
    assert len(files) == len(set(files))


def test_broken_symlink_is_skipped_not_fatal(tmp_path):
    """A dangling symlink must not abort a whole-repo walk — it's just a
    file that can't be read, which is the binary-check's fail-safe path."""
    write(tmp_path / "real.py")
    os.symlink(str(tmp_path / "missing.py"), str(tmp_path / "dangling.py"))
    assert discover(tmp_path) == ["real.py"]


def test_symlinked_directory_is_not_followed_into_a_loop(tmp_path):
    """os.walk doesn't follow directory symlinks by default; this pins that
    behavior so a self-referential link can never hang a run."""
    write(tmp_path / "src" / "a.py")
    os.symlink(str(tmp_path / "src"), str(tmp_path / "src" / "loop"))
    files = discover(tmp_path)
    assert files == ["src/a.py"]


def test_nested_git_repo_files_are_still_discovered(tmp_path):
    """A vendored sub-repo's files are ordinary files to the walk. They're
    excluded by patterns or gitignore if at all — never by accident."""
    init_repo(tmp_path)
    write(tmp_path / "app.py")
    inner = tmp_path / "nested"
    init_repo(inner)
    write(inner / "lib.py")
    files = discover(tmp_path, exclude_dirs=df.DEFAULT_EXCLUDE_DIRS)
    assert files == ["app.py", "nested/lib.py"]


def test_nested_git_repo_scope_is_outer_repo_only(tmp_path):
    """git reports on the repo owning the cwd. A nested repo's dirty files
    are invisible to the outer repo's scope — the scope must not leak into
    a sub-repo the outer run has no business editing."""
    init_repo(tmp_path)
    write(tmp_path / "outer.py")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "outer")

    inner = tmp_path / "nested"
    init_repo(inner)
    write(inner / "inner.py")

    write(tmp_path / "outer.py", "x = 2\n")
    scoped = df.git_scoped_paths(str(tmp_path), "changed")
    assert "outer.py" in scoped
    assert not any(p.startswith("nested/inner") for p in scoped)


def test_git_dir_is_never_walked(tmp_path):
    """.git holds files with source extensions (hooks, patches); walking in
    would let a batch skill edit repository internals."""
    init_repo(tmp_path)
    write(tmp_path / ".git" / "hooks" / "pre-commit.py", "#!/usr/bin/env python\n")
    write(tmp_path / "app.py")
    assert discover(tmp_path, exclude_dirs=df.DEFAULT_EXCLUDE_DIRS) == ["app.py"]


def test_hidden_directories_are_walked_unless_excluded(tmp_path):
    """Hidden ≠ excluded: .github workflows and .config scripts are real
    source. Only the explicit exclude list should prune them."""
    write(tmp_path / ".github" / "scripts" / "release.py")
    write(tmp_path / "app.py")
    assert discover(tmp_path, exclude_dirs=df.DEFAULT_EXCLUDE_DIRS) == [
        ".github/scripts/release.py", "app.py",
    ]


def test_extensionless_and_dotfile_paths_do_not_crash(tmp_path):
    """`.env`-style names have no extension by rsplit's reckoning; the
    extension parser must treat them as unmatched, not raise."""
    write(tmp_path / "Makefile", "all:\n")
    write(tmp_path / ".env", "KEY=1\n")
    write(tmp_path / "app.py")
    assert discover(tmp_path) == ["app.py"]


def test_deeply_nested_paths_are_discovered(tmp_path):
    """Guards against any depth cap sneaking into the walk."""
    deep = tmp_path.joinpath(*[f"lvl{i}" for i in range(12)])
    write(deep / "deep.py")
    files = discover(tmp_path)
    assert len(files) == 1
    assert files[0].endswith("deep.py")
    assert files[0].count("/") == 12
