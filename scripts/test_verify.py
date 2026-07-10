#!/usr/bin/env python3
"""Tests for verify.py. Stdlib unittest only. Run: python3 -m unittest -v"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import verify


# A gate that fails whenever the target file contains the token "BAD".
GATE_SRC = '''\
import sys
text = open(sys.argv[1]).read()
sys.exit(1 if "BAD" in text else 0)
'''

# NOTE: a gate exiting non-zero is a legitimate FAIL verdict. A gate that
# cannot execute at all (missing binary, timeout) is an ERROR. Only the
# latter is an infrastructure fault. We trigger it with a missing binary.
MISSING_BINARY = "comrades-no-such-gate-binary-xyz"


class Base(unittest.TestCase):
    """Base class for verify tests, providing a temporary directory and helper methods.
    
    Sets up a temporary working directory and a basic file-scoped gate that
    fails if the target file contains the "BAD" token.
    """
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / ".comrades").mkdir()
        (self.root / "gate.py").write_text(GATE_SRC)

    def write_config(self, cmd, **kw):
        cfg = {"gates": [{"name": "g", "cmd": cmd, "scope": "file"}]}
        cfg.update(kw)
        (self.root / ".comrades" / "verify.json").write_text(json.dumps(cfg))

    def good_gate(self, **kw):
        self.write_config([sys.executable, "gate.py", "{file}"], **kw)

    def crash_gate(self, **kw):
        self.write_config([MISSING_BINARY, "{file}"], **kw)

    def run_cli(self, *args):
        return verify.main(["--root", str(self.root), *args])

    def src(self, name="a.py", body="ok\n"):
        p = self.root / name
        p.write_text(body)
        return p


class TestKeepAndRevert(Base):
    def test_passing_edit_is_kept(self):
        self.good_gate()
        f = self.src(body="clean\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("still clean\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 0)
        self.assertEqual(f.read_text(), "still clean\n")

    def test_failing_edit_is_reverted_byte_for_byte(self):
        self.good_gate()
        original = "clean\nline two\n"
        f = self.src(body=original)
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("BAD content\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 1)
        self.assertEqual(f.read_text(), original)

    def test_untouched_file_reports_unchanged(self):
        self.good_gate()
        f = self.src(body="clean\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 0)

    def test_check_without_begin_errors(self):
        self.good_gate()
        f = self.src()
        self.assertEqual(self.run_cli("check", "--run", "r1", "--file", str(f)), 2)


class TestBaseline(Base):
    def test_begin_exits_3_when_gate_cannot_run(self):
        self.crash_gate()
        f = self.src(body="x\n")
        self.assertEqual(self.run_cli("begin", "--run", "r1", "--file", str(f)), 3)


    def test_prebroken_file_is_skipped_not_reverted(self):
        """A file already failing its gate cannot be verified -> skip."""
        self.good_gate()
        f = self.src(body="BAD already\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("BAD still, but edited\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 0)
        # skipped => edit rolled back, file untouched
        self.assertEqual(f.read_text(), "BAD already\n")

    def test_baseline_fail_allow_keeps_edit(self):
        self.good_gate(on_baseline_fail="allow")
        f = self.src(body="BAD already\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("BAD but improved\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 0)
        self.assertEqual(f.read_text(), "BAD but improved\n")

    def test_stage_baseline_fail_allow_keeps_edit(self):
        """Same scenario as test_baseline_fail_allow_keeps_edit, but through
        `stage` (checkpoint mode) instead of `check` (per_file mode) -- a
        pre-broken file-scope gate under `on_baseline_fail: allow` must be
        kept regardless of which mode is driving verification."""
        self.good_gate(on_baseline_fail="allow")
        f = self.src(body="BAD already\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("BAD but improved\n")
        rc = self.run_cli("stage", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 0)
        self.assertEqual(f.read_text(), "BAD but improved\n")


class TestGateError(Base):
    def test_unrunnable_gate_fails_closed(self):
        """A gate binary that does not exist must never silently pass."""
        self.crash_gate()
        original = "clean\n"
        f = self.src(body=original)
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("edited\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 1)
        self.assertEqual(f.read_text(), original)

    def test_timeout_is_error_not_hang(self):
        cfg = {"gates": [{"name": "slow", "scope": "file", "timeout": 1,
                          "cmd": [sys.executable, "-c",
                                  "import time; time.sleep(30)"]}]}
        (self.root / ".comrades" / "verify.json").write_text(json.dumps(cfg))
        f = self.src(body="x\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("y\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 1)
        self.assertEqual(f.read_text(), "x\n")


class TestRunUndo(Base):
    def test_revert_run_undoes_every_kept_edit(self):
        self.good_gate()
        files = []
        for i in range(3):
            f = self.src(f"f{i}.py", f"orig{i}\n")
            files.append(f)
            self.run_cli("begin", "--run", "r1", "--file", str(f))
            f.write_text(f"edited{i}\n")
            self.run_cli("check", "--run", "r1", "--file", str(f))

        for i, f in enumerate(files):
            self.assertEqual(f.read_text(), f"edited{i}\n")

        self.run_cli("revert-run", "--run", "r1")
        for i, f in enumerate(files):
            self.assertEqual(f.read_text(), f"orig{i}\n")

    def test_reverted_file_not_double_counted_in_undo(self):
        self.good_gate()
        f = self.src("x.py", "orig\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("BAD\n")
        self.run_cli("check", "--run", "r1", "--file", str(f))  # reverted
        self.run_cli("revert-run", "--run", "r1")
        self.assertEqual(f.read_text(), "orig\n")


class TestLedger(Base):
    def test_ledger_is_append_only_jsonl(self):
        self.good_gate()
        f = self.src(body="clean\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("BAD\n")
        self.run_cli("check", "--run", "r1", "--file", str(f))
        led = self.root / ".comrades" / "runs" / "r1" / "ledger.jsonl"
        recs = [json.loads(line) for line in led.read_text().splitlines()]
        self.assertEqual([r["event"] for r in recs], ["begin", "check"])
        self.assertEqual(recs[1]["decision"], "reverted")

    def test_snapshots_deduplicate_by_content_hash(self):
        self.good_gate()
        a = self.src("a.py", "same\n")
        b = self.src("b.py", "same\n")
        self.run_cli("begin", "--run", "r1", "--file", str(a))
        self.run_cli("begin", "--run", "r1", "--file", str(b))
        snaps = list((self.root / ".comrades" / "runs" / "r1" / "snapshots").iterdir())
        self.assertEqual(len(snaps), 1)


class TestGateLock(Base):
    def test_lock_is_exclusive_and_released(self):
        with verify.GateLock(self.root):
            with self.assertRaises(TimeoutError):
                lock2 = verify.GateLock(self.root, timeout=0)
                lock2.__enter__()
        # released -> reacquirable
        with verify.GateLock(self.root, timeout=1):
            pass

    def test_project_gate_serialized_end_to_end(self):
        cfg = {"gates": [{"name": "proj", "scope": "project", "timeout": 30,
                          "cmd": [sys.executable, "-c", "pass"]}]}
        (self.root / ".comrades" / "verify.json").write_text(json.dumps(cfg))
        f = self.src(body="a\n")
        # `begin` now fails closed if project-scoped gates are configured
        # and `baseline` was never called for this run (see
        # TestProjectBaseline.test_begin_fails_closed_...); call it first.
        self.run_cli("baseline", "--run", "r1")
        self.assertEqual(self.run_cli("begin", "--run", "r1", "--file", str(f)), 0)
        f.write_text("b\n")
        self.assertEqual(self.run_cli("check", "--run", "r1", "--file", str(f)), 0)
        self.assertFalse((self.root / ".comrades" / "gate.lock").exists())


class TestConfigOverride(Base):
    def test_config_flag_overrides_default_path(self):
        self.good_gate()  # default .comrades/verify.json: passes on non-BAD content
        alt_config = self.root / "alt-verify.json"
        alt_config.write_text(json.dumps({
            "gates": [{"name": "always-fail", "scope": "file",
                       "cmd": [sys.executable, "-c", "import sys; sys.exit(1)"]}]
        }))
        f = self.src(body="clean\n")
        self.run_cli("--config", str(alt_config), "begin",
                     "--run", "r1", "--file", str(f))
        led = self.root / ".comrades" / "runs" / "r1" / "ledger.jsonl"
        recs = [json.loads(line) for line in led.read_text().splitlines()]
        self.assertEqual(recs[0]["event"], "begin")
        self.assertEqual(recs[0]["baseline"], "fail")


class TestProjectBaseline(Base):
    def write_project_config(self, cmd, **kw):
        cfg = {"gates": [{"name": "proj", "cmd": cmd, "scope": "project",
                          "timeout": 30}]}
        cfg.update(kw)
        (self.root / ".comrades" / "verify.json").write_text(json.dumps(cfg))

    def test_baseline_records_pass_with_no_project_gates_configured(self):
        (self.root / ".comrades" / "verify.json").write_text(json.dumps({"gates": []}))
        rc = self.run_cli("baseline", "--run", "r1")
        self.assertEqual(rc, 0)
        led = self.root / ".comrades" / "runs" / "r1" / "ledger.jsonl"
        recs = [json.loads(line) for line in led.read_text().splitlines()]
        self.assertEqual(recs[0]["event"], "baseline_project")
        self.assertEqual(recs[0]["verdict"], "pass")

    def test_baseline_exits_3_when_project_gate_cannot_run(self):
        self.write_project_config([MISSING_BINARY])
        rc = self.run_cli("baseline", "--run", "r1")
        self.assertEqual(rc, 3)

    def test_baseline_idempotent_latest_record_wins(self):
        self.write_project_config([sys.executable, "-c", "import sys; sys.exit(0)"])
        self.run_cli("baseline", "--run", "r1")
        self.write_project_config([sys.executable, "-c", "import sys; sys.exit(1)"])
        rc = self.run_cli("baseline", "--run", "r1")
        self.assertEqual(rc, 0)
        led = self.root / ".comrades" / "runs" / "r1" / "ledger.jsonl"
        events = [json.loads(line) for line in led.read_text().splitlines()]
        baselines = [r for r in events if r["event"] == "baseline_project"]
        self.assertEqual(len(baselines), 2)
        self.assertEqual(baselines[-1]["verdict"], "fail")

    def test_begin_baseline_error_wins_even_if_file_gate_passes(self):
        self.write_project_config([MISSING_BINARY])
        cfg = json.loads((self.root / ".comrades" / "verify.json").read_text())
        cfg["gates"].append({"name": "g", "scope": "file",
                             "cmd": [sys.executable, "gate.py", "{file}"]})
        (self.root / ".comrades" / "verify.json").write_text(json.dumps(cfg))
        self.run_cli("baseline", "--run", "r1")
        f = self.src(body="clean\n")
        rc = self.run_cli("begin", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 3)
        led = self.root / ".comrades" / "runs" / "r1" / "ledger.jsonl"
        recs = [json.loads(line) for line in led.read_text().splitlines()]
        begin_rec = next(r for r in recs if r["event"] == "begin")
        self.assertEqual(begin_rec["baseline"], "error")

    def test_check_not_reverted_but_skipped_when_project_baseline_fails_and_policy_skip(self):
        self.write_project_config(
            [sys.executable, "-c", "import sys; sys.exit(1)"],
            on_baseline_fail="skip",
        )
        self.run_cli("baseline", "--run", "r1")
        f = self.src(body="clean\n")
        self.run_cli("begin", "--run", "r1", "--file", str(f))
        f.write_text("edited\n")
        rc = self.run_cli("check", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 0)
        self.assertEqual(f.read_text(), "clean\n")

    def test_begin_fails_closed_when_project_gates_configured_but_baseline_never_called(self):
        self.write_project_config([sys.executable, "-c", "import sys; sys.exit(0)"])
        # NOTE: deliberately no self.run_cli("baseline", ...) call here.
        f = self.src(body="clean\n")
        rc = self.run_cli("begin", "--run", "r1", "--file", str(f))
        self.assertEqual(rc, 3)
        led = self.root / ".comrades" / "runs" / "r1" / "ledger.jsonl"
        recs = [json.loads(line) for line in led.read_text().splitlines()]
        begin_rec = next(r for r in recs if r["event"] == "begin")
        self.assertEqual(begin_rec["baseline"], "error")
        self.assertTrue(begin_rec["contract_error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
