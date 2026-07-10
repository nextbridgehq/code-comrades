"""Tests for manifest.py: canonical key derivation, manifest creation and
persistence, resume-vs-fresh file selection, and result recording/summarization.
"""
import json
import os

import manifest as m


def test_canonical_key_stable_regardless_of_config_key_order():
    """Key generation must be order-independent since config dicts aren't guaranteed insertion order."""
    k1 = m.canonical_key("src/a.py", {"audience": "senior", "idempotent": True})
    k2 = m.canonical_key("src/a.py", {"idempotent": True, "audience": "senior"})
    assert k1 == k2


def test_canonical_key_differs_for_different_config():
    """Different configs must produce different keys so config changes force reprocessing."""
    k1 = m.canonical_key("src/a.py", {"audience": "senior"})
    k2 = m.canonical_key("src/a.py", {"audience": "junior"})
    assert k1 != k2


def test_canonical_key_differs_for_different_path():
    """Different paths must produce different keys to avoid manifest collisions across files."""
    k1 = m.canonical_key("src/a.py", {"audience": "senior"})
    k2 = m.canonical_key("src/b.py", {"audience": "senior"})
    assert k1 != k2


def test_new_manifest_marks_all_files_pending():
    """A freshly created manifest starts every file as pending and unchanged."""
    manifest = m.new_manifest("code-commenter", "src/", {"audience": "senior"}, ["a.py", "b.py"])
    assert manifest["skill"] == "code-commenter"
    assert manifest["files"]["a.py"]["status"] == "pending"
    assert manifest["files"]["b.py"]["status"] == "pending"
    assert manifest["files"]["a.py"]["changed"] is False


def test_files_to_process_resume_mode_skips_done_and_skipped():
    """Resume mode only re-dispatches files left pending or previously errored."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py", "c.py", "d.py"])
    manifest["files"]["a.py"]["status"] = "done"
    manifest["files"]["b.py"]["status"] = "skipped"
    manifest["files"]["c.py"]["status"] = "error"
    assert set(m.files_to_process(manifest, mode="resume")) == {"c.py", "d.py"}


def test_files_to_process_fresh_mode_includes_everything():
    """Fresh mode ignores prior status and redispatches the full file set."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py"])
    manifest["files"]["a.py"]["status"] = "done"
    assert set(m.files_to_process(manifest, mode="fresh")) == {"a.py", "b.py"}


def test_files_to_process_respects_max():
    """max_files caps the candidate list regardless of how many files are eligible."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py", "c.py"])
    assert len(m.files_to_process(manifest, mode="resume", max_files=2)) == 2


def test_files_to_process_resume_mode_includes_reverted():
    """A reverted file (verification rolled it back) is retried on resume, like an error."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py"])
    manifest["files"]["a.py"]["status"] = "reverted"
    assert set(m.files_to_process(manifest, mode="resume")) == {"a.py", "b.py"}


def test_record_results_updates_matched_files():
    """Worker results overwrite the corresponding manifest entries by file path."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py"])
    results = [
        {"file": "a.py", "status": "done", "changed": True, "reason": None, "summary": "3 comments added"},
        {"file": "b.py", "status": "done", "changed": False, "reason": None, "summary": "no changes needed"},
    ]
    updated = m.record_results(manifest, dispatched_files=["a.py", "b.py"], results=results)
    assert updated["files"]["a.py"]["status"] == "done"
    assert updated["files"]["a.py"]["changed"] is True
    assert updated["files"]["b.py"]["changed"] is False


def test_record_results_marks_missing_result_as_error():
    """A dispatched file with no matching result is marked as an error rather than left stale."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py"])
    results = [{"file": "a.py", "status": "done", "changed": True, "reason": None, "summary": "ok"}]
    updated = m.record_results(manifest, dispatched_files=["a.py", "b.py"], results=results)
    assert updated["files"]["b.py"]["status"] == "error"
    assert updated["files"]["b.py"]["reason"] == "worker produced no result block"


def test_summarize_counts_by_status_and_changed():
    """Summary counts must reflect explicit status/changed overrides, not just new_manifest defaults."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py", "c.py"])
    manifest["files"]["a.py"] = {"status": "done", "changed": True, "reason": None, "summary": None}
    manifest["files"]["b.py"] = {"status": "done", "changed": False, "reason": None, "summary": None}
    manifest["files"]["c.py"] = {"status": "error", "changed": False, "reason": "boom", "summary": None}
    result = m.summarize(manifest)
    assert result["counts"] == {"pending": 0, "done": 2, "skipped": 0, "error": 1, "reverted": 0}
    assert result["changed"] == 1
    assert result["total"] == 3


def test_summarize_counts_reverted():
    """Summary tallies reverted files separately from error, done, etc."""
    manifest = m.new_manifest("s", "p", {}, ["a.py", "b.py"])
    manifest["files"]["a.py"] = {"status": "reverted", "changed": False, "reason": "gate failed", "summary": None}
    manifest["files"]["b.py"] = {"status": "done", "changed": True, "reason": None, "summary": None}
    result = m.summarize(manifest)
    assert result["counts"]["reverted"] == 1
    assert result["counts"]["done"] == 1


def test_save_and_load_roundtrip(tmp_path):
    """Saving then loading a manifest must reproduce it exactly, including nested directory creation."""
    path = str(tmp_path / "nested" / "manifest.json")
    manifest = m.new_manifest("s", "p", {}, ["a.py"])
    m.save_manifest(path, manifest)
    loaded = m.load_manifest(path)
    assert loaded == manifest


def test_load_manifest_returns_none_when_missing(tmp_path):
    """Loading a nonexistent manifest returns None instead of raising."""
    assert m.load_manifest(str(tmp_path / "nope.json")) is None


def test_manifest_path_is_stable_and_namespaced_by_skill(tmp_path):
    """Manifest paths are deterministic for identical inputs and namespaced per skill to avoid cross-skill collisions."""
    p1 = m.manifest_path(str(tmp_path), "code-commenter", "src/", {"audience": "senior"})
    p2 = m.manifest_path(str(tmp_path), "code-commenter", "src/", {"audience": "senior"})
    p3 = m.manifest_path(str(tmp_path), "other-skill", "src/", {"audience": "senior"})
    assert p1 == p2
    assert p1 != p3
    assert ".claude-batch-manifest" in p1
    assert p1.startswith(str(tmp_path))
