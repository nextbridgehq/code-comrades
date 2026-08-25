"""Integration test: manifest.py's CLI, exercised end-to-end via subprocess,
simulating a dispatch run interrupted partway through and then resumed.

scripts/test_manifest.py already unit-tests the underlying functions
(files_to_process, record_results, etc.) directly in-process. What's
missing is a test that drives the actual CLI subcommands the way
dispatch's orchestrator does — init, then two separate `pending`/`record`
cycles with a process boundary between them, standing in for the
interruption. This is deliberately a *separate* file so it can be dropped
in without touching the existing manifest test suite.
"""
import json
import subprocess
import sys
from pathlib import Path

MANIFEST_PY = Path(__file__).parent / "manifest.py"


def run_cli(*args, cwd):
    result = subprocess.run(
        [sys.executable, str(MANIFEST_PY), *args],
        cwd=cwd, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"manifest.py {args} failed:\n{result.stderr}"
    return result.stdout


def test_interrupted_run_resumes_without_reprocessing_done_files(tmp_path):
    repo_root = tmp_path
    files_file = tmp_path / "files.json"
    files_file.write_text("a.py\nb.py\nc.py\nd.py\n")

    # --- Round 1: init a fresh run, "process" only the first two files,
    # then stop — simulating an interruption after one chunk. ---
    run_cli(
        "init",
        "--repo-root", str(repo_root),
        "--skill", "code-commenter",
        "--target-path", ".",
        "--config-json", "{}",
        "--files-file", str(files_file),
        cwd=repo_root,
    )

    manifest_path = repo_root / ".claude-batch-manifest" / next(
        p.name for p in (repo_root / ".claude-batch-manifest").iterdir()
    )

    pending_out = [l for l in run_cli(
        "pending", "--manifest-path", str(manifest_path), cwd=repo_root
    ).splitlines() if l]
    assert set(pending_out) == {"a.py", "b.py", "c.py", "d.py"}

    dispatched = ["a.py", "b.py"]
    dispatched_file = tmp_path / "dispatched1.json"
    dispatched_file.write_text("\n".join(dispatched) + "\n")
    results_file = tmp_path / "results1.json"
    results_file.write_text(json.dumps([
        {"file": "a.py", "status": "done", "changed": True,
         "reason": None, "summary": "commented"},
        {"file": "b.py", "status": "done", "changed": False,
         "reason": "already documented", "summary": None},
    ]))
    run_cli(
        "record",
        "--manifest-path", str(manifest_path),
        "--dispatched-files-file", str(dispatched_file),
        "--results-file", str(results_file),
        cwd=repo_root,
    )

    summary_mid = json.loads(run_cli(
        "summary", "--manifest-path", str(manifest_path), cwd=repo_root
    ))["summary"]
    assert summary_mid["counts"]["done"] == 2
    assert summary_mid["counts"]["pending"] == 2

    # --- Simulated interruption happens here: process exits, nothing
    # else touches the manifest until the "resume" below. ---

    # --- Round 2: a fresh CLI invocation (as a real resumed dispatch
    # would be) asks for pending work and must NOT re-offer a.py or b.py. ---
    pending_after_resume = [l for l in run_cli(
        "pending", "--manifest-path", str(manifest_path), cwd=repo_root
    ).splitlines() if l]
    assert set(pending_after_resume) == {"c.py", "d.py"}, (
        "resume must skip files already recorded as done, not reprocess them"
    )

    dispatched2_file = tmp_path / "dispatched2.json"
    dispatched2_file.write_text("\n".join(pending_after_resume) + "\n")
    results2_file = tmp_path / "results2.json"
    results2_file.write_text(json.dumps([
        {"file": "c.py", "status": "done", "changed": True,
         "reason": None, "summary": "commented"},
        # d.py deliberately omitted, simulating a worker that crashed
        # without emitting a result block.
    ]))
    run_cli(
        "record",
        "--manifest-path", str(manifest_path),
        "--dispatched-files-file", str(dispatched2_file),
        "--results-file", str(results2_file),
        cwd=repo_root,
    )

    final_summary = json.loads(run_cli(
        "summary", "--manifest-path", str(manifest_path), cwd=repo_root
    ))["summary"]
    assert final_summary["counts"]["done"] == 3
    assert final_summary["counts"]["error"] == 1, (
        "a dispatched file with no matching result must be recorded as "
        "error, not silently dropped or left pending forever"
    )
    assert final_summary["counts"]["pending"] == 0


def test_resumed_run_with_no_interruption_is_a_clean_no_op(tmp_path):
    """A resume against an already-fully-done manifest should offer
    nothing to process — the ordinary re-run case, not just the
    interrupted-mid-run case above."""
    repo_root = tmp_path
    files_file = tmp_path / "files.json"
    files_file.write_text("a.py\n")

    run_cli(
        "init",
        "--repo-root", str(repo_root),
        "--skill", "code-commenter",
        "--target-path", ".",
        "--config-json", "{}",
        "--files-file", str(files_file),
        cwd=repo_root,
    )
    manifest_path = repo_root / ".claude-batch-manifest" / next(
        p.name for p in (repo_root / ".claude-batch-manifest").iterdir()
    )

    dispatched_file = tmp_path / "dispatched.json"
    dispatched_file.write_text("a.py\n")
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps([
        {"file": "a.py", "status": "done", "changed": False,
         "reason": "already documented", "summary": None},
    ]))
    run_cli(
        "record",
        "--manifest-path", str(manifest_path),
        "--dispatched-files-file", str(dispatched_file),
        "--results-file", str(results_file),
        cwd=repo_root,
    )

    pending = [l for l in run_cli(
        "pending", "--manifest-path", str(manifest_path), cwd=repo_root
    ).splitlines() if l]
    assert pending == [], "a fully-done manifest must offer nothing on resume"
