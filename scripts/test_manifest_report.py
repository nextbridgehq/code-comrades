"""Tests for the scope-aware manifest keying, incremental reconciliation,
and run-report rendering.

The report renderer is a pure function over a manifest dict, so these
assert on its output directly; the CLI-level cases go through main() to
pin the on-disk paths dispatch depends on.
"""

import json
import os

import manifest as m


def make_manifest(files=None, skill="code-commenter", scope=None):
    """Creates a basic manifest dict with the given configuration for testing."""
    files = files if files is not None else ["a.py"]
    return m.new_manifest(skill, ".", {"audience": "senior"}, files, scope)


def set_status(manifest, path, **fields):
    """Updates a file entry in the manifest with the provided status fields."""
    entry = {"status": "done", "changed": False, "reason": None, "summary": None}
    entry.update(fields)
    manifest["files"][path] = entry
    return manifest


# --- scope-aware keying -----------------------------------------------------

def test_key_without_scope_is_unchanged_by_the_scope_feature():
    """Existing manifests were keyed before scopes existed. A None scope must
    hash identically to the old two-field payload, or every user's resume
    state is orphaned on upgrade."""
    import hashlib
    legacy_payload = json.dumps({"path": "src", "config": {"a": 1}}, sort_keys=True)
    legacy_key = hashlib.sha256(legacy_payload.encode("utf-8")).hexdigest()[:16]
    assert m.canonical_key("src", {"a": 1}) == legacy_key
    assert m.canonical_key("src", {"a": 1}, None) == legacy_key


def test_scoped_and_unscoped_runs_get_distinct_manifests():
    """An incremental run must not consume a full run's resume state."""
    full = m.canonical_key("src", {"a": 1})
    changed = m.canonical_key("src", {"a": 1}, "changed")
    staged = m.canonical_key("src", {"a": 1}, "staged")
    assert len({full, changed, staged}) == 3


def test_since_scopes_with_different_refs_are_distinct():
    """'since main' and 'since v1.0' are different questions about different
    file sets; sharing a manifest would cross their results."""
    assert m.canonical_key("src", {}, "since:main") != m.canonical_key("src", {}, "since:v1.0")


def test_manifest_path_includes_skill_and_key(tmp_path):
    """The generated manifest file path must encode the skill and key hash 
    to prevent collisions across different tools and scopes."""
    p = m.manifest_path(str(tmp_path), "code-commenter", ".", {}, "changed")
    assert os.path.dirname(p).endswith(".claude-batch-manifest")
    assert os.path.basename(p).startswith("code-commenter-")
    assert p.endswith(".json")


# --- incremental reconciliation ---------------------------------------------

def test_reconcile_resets_previously_done_files():
    """The core incremental guarantee: a file git reports as in scope changed
    since it was last processed, so its 'done' result is stale. Carrying it
    over would skip exactly the file the user ran incremental mode to catch."""
    manifest = make_manifest(["a.py", "b.py"], scope="changed")
    set_status(manifest, "a.py", status="done", changed=True)
    m.reconcile_to_scope(manifest, ["a.py"])
    assert manifest["files"]["a.py"]["status"] == "pending"
    assert manifest["files"]["a.py"]["changed"] is False


def test_reconcile_drops_files_no_longer_in_scope():
    """Once a file is committed it leaves the changed set; it must leave the
    manifest too, or every report over-counts."""
    manifest = make_manifest(["a.py", "b.py"], scope="changed")
    m.reconcile_to_scope(manifest, ["a.py"])
    assert set(manifest["files"]) == {"a.py"}


def test_reconcile_adds_newly_in_scope_files():
    """A file that enters the scope must be added to the manifest as pending."""
    manifest = make_manifest(["a.py"], scope="changed")
    m.reconcile_to_scope(manifest, ["a.py", "new.py"])
    assert manifest["files"]["new.py"]["status"] == "pending"


def test_reconcile_to_empty_scope_leaves_nothing_tracked():
    """'nothing changed' is a valid outcome and must not resurrect old files."""
    manifest = make_manifest(["a.py"], scope="changed")
    m.reconcile_to_scope(manifest, [])
    assert manifest["files"] == {}
    assert m.summarize(manifest)["total"] == 0


def test_init_reconciles_scoped_run_but_resumes_full_run(tmp_path):
    """The two modes must behave differently on a second invocation: a full
    run resumes (keeps 'done'), a scoped run re-scopes (resets it)."""
    files_file = tmp_path / "files.txt"
    files_file.write_text("a.py\n")

    def init(scope=None):
        argv = ["init", "--repo-root", str(tmp_path), "--skill", "s",
                "--target-path", ".", "--config-json", "{}",
                "--files-file", str(files_file)]
        if scope:
            argv += ["--scope", scope]
        return m.main(argv)

    for scope in (None, "changed"):
        init(scope)
        path = m.manifest_path(str(tmp_path), "s", ".", {}, scope)
        manifest = m.load_manifest(path)
        set_status(manifest, "a.py", status="done", changed=True)
        m.save_manifest(path, manifest)

        init(scope)  # second invocation
        after = m.load_manifest(path)
        expected = "pending" if scope else "done"
        assert after["files"]["a.py"]["status"] == expected, f"scope={scope}"


def test_init_reports_reconciled_flag(tmp_path, capsys):
    """The init command must output JSON reporting whether it reconciled against 
    an existing manifest, allowing callers to track incremental state resets."""
    files_file = tmp_path / "files.txt"
    files_file.write_text("a.py\n")
    argv = ["init", "--repo-root", str(tmp_path), "--skill", "s", "--target-path", ".",
            "--config-json", "{}", "--files-file", str(files_file), "--scope", "changed"]
    m.main(argv)
    capsys.readouterr()
    m.main(argv)
    out = json.loads(capsys.readouterr().out)
    assert out["reconciled"] is True
    assert out["scope"] == "changed"


# --- report rendering -------------------------------------------------------

def test_report_counts_reconcile_with_summarize():
    """The report's headline table is the number a user acts on; it must not
    drift from the manifest's own tally."""
    manifest = make_manifest(["a.py", "b.py", "c.py", "d.py"])
    set_status(manifest, "a.py", status="done", changed=True)
    set_status(manifest, "b.py", status="done", changed=False)
    set_status(manifest, "c.py", status="skipped", reason="GENERATED")
    set_status(manifest, "d.py", status="error", reason="boom")

    report = m.render_markdown_report(manifest)
    s = m.summarize(manifest)
    assert f"| Changed | {s['changed']} |" in report
    assert f"| Errors | {s['counts']['error']} |" in report
    assert "| Done (no change needed) | 1 |" in report


def test_report_lists_changed_files_with_summaries():
    """Files modified during the run must be listed in the report alongside 
    their provided summary text."""
    manifest = make_manifest(["a.py"])
    set_status(manifest, "a.py", status="done", changed=True, summary="added 3 docstrings")
    report = m.render_markdown_report(manifest)
    assert "`a.py` — added 3 docstrings" in report


def test_report_groups_errors_by_reason():
    """Fifty files failing for one reason is one problem, not fifty."""
    manifest = make_manifest(["a.py", "b.py", "c.py"])
    set_status(manifest, "a.py", status="error", reason="timeout")
    set_status(manifest, "b.py", status="error", reason="timeout")
    set_status(manifest, "c.py", status="error", reason="parse failure")
    report = m.render_markdown_report(manifest)
    assert "**timeout** (2)" in report
    assert "**parse failure** (1)" in report


def test_report_buckets_missing_reason_rather_than_dropping_it():
    """A file with no reason must still appear — silently omitting it would
    make the report disagree with the counts above it."""
    manifest = make_manifest(["a.py"])
    set_status(manifest, "a.py", status="error", reason=None)
    report = m.render_markdown_report(manifest)
    assert "unspecified" in report
    assert "`a.py`" in report


def test_report_folds_in_verify_failure_detail():
    """The failing gate name lives in verify's report, not the manifest;
    without it a reverted file is unactionable."""
    manifest = make_manifest(["a.py"], scope=None)
    set_status(manifest, "a.py", status="reverted", changed=False)
    verify_report = {"failures": [
        {"file": "a.py", "gate": "mypy", "output": "error: incompatible return value"}
    ]}
    report = m.render_markdown_report(manifest, verify_report)
    assert "gate `mypy`" in report
    assert "incompatible return value" in report


def test_report_handles_reverted_without_verify_detail():
    """verify's report may be absent; the reverted file must still be named."""
    manifest = make_manifest(["a.py"])
    set_status(manifest, "a.py", status="reverted")
    report = m.render_markdown_report(manifest, None)
    assert "Reverted by verification" in report
    assert "`a.py`" in report


def test_report_flags_pending_files_as_resumable():
    """A capped or interrupted run is exactly when the report matters most."""
    manifest = make_manifest(["a.py", "b.py"])
    set_status(manifest, "a.py", status="done", changed=True)
    report = m.render_markdown_report(manifest)
    assert "## Pending" in report
    assert "resume" in report.lower()


def test_report_states_scope_for_incremental_runs():
    """A scoped report showing '12 files' without saying 'changed only' invites
    the reader to conclude the repo has 12 files."""
    scoped = m.render_markdown_report(make_manifest(["a.py"], scope="since:main"))
    full = m.render_markdown_report(make_manifest(["a.py"]))
    assert "`since:main` (incremental)" in scoped
    assert "full run" in full


def test_report_renders_for_legacy_manifest_without_scope_key():
    """Manifests written before scopes existed must still render."""
    manifest = make_manifest(["a.py"])
    del manifest["scope"]
    assert "full run" in m.render_markdown_report(manifest)


def test_report_survives_unicode_paths_and_summaries():
    """The report renderer must correctly handle unicode characters in both 
    file paths and summaries without encoding errors."""
    manifest = make_manifest(["café/文件.py"])
    set_status(manifest, "café/文件.py", status="done", changed=True, summary="añadido")
    report = m.render_markdown_report(manifest)
    assert "café/文件.py" in report and "añadido" in report


# --- report CLI -------------------------------------------------------------

def test_report_cli_writes_markdown_next_to_manifest(tmp_path, capsys):
    """The report command must default to saving a markdown file alongside 
    the manifest when no explicit output path is provided."""
    path = str(tmp_path / ".claude-batch-manifest" / "s-abc123.json")
    m.save_manifest(path, make_manifest(["a.py"]))
    m.main(["report", "--manifest-path", path])
    out = json.loads(capsys.readouterr().out)
    assert out["report_path"] == str(tmp_path / ".claude-batch-manifest" / "s-abc123-report.md")
    assert os.path.exists(out["report_path"])
    with open(out["report_path"], encoding="utf-8") as f:
        assert f.read().startswith("# Batch run report")


def test_report_cli_json_format_is_machine_readable(tmp_path, capsys):
    """When the format is json, the report CLI must output a structured 
    representation of the manifest counts and states."""
    path = str(tmp_path / "m.json")
    manifest = make_manifest(["a.py"])
    set_status(manifest, "a.py", status="done", changed=True)
    m.save_manifest(path, manifest)
    m.main(["report", "--manifest-path", path, "--format", "json"])
    out = json.loads(capsys.readouterr().out)
    with open(out["report_path"], encoding="utf-8") as f:
        data = json.load(f)
    assert data["summary"]["changed"] == 1
    assert data["files"]["a.py"]["status"] == "done"


def test_report_cli_honors_explicit_output_path(tmp_path, capsys):
    """The CLI must write the report to the path given by --output instead 
    of the default adjacent path."""
    path = str(tmp_path / "m.json")
    m.save_manifest(path, make_manifest(["a.py"]))
    out_path = str(tmp_path / "nested" / "custom.md")
    m.main(["report", "--manifest-path", path, "--output", out_path])
    assert os.path.exists(out_path)


def test_report_cli_tolerates_broken_verify_report(tmp_path, capsys):
    """A malformed verify report costs detail, never the whole report."""
    path = str(tmp_path / "m.json")
    m.save_manifest(path, make_manifest(["a.py"]))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert m.main(["report", "--manifest-path", path,
                   "--verify-report-file", str(bad)]) == 0
    assert "WARNING" in capsys.readouterr().err


def test_report_cli_errors_on_missing_manifest(tmp_path, capsys):
    """The CLI must fail gracefully with a non-zero exit code if the specified 
    manifest file does not exist."""
    assert m.main(["report", "--manifest-path", str(tmp_path / "nope.json")]) == 1
    assert "ERROR" in capsys.readouterr().err
