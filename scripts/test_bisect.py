#!/usr/bin/env python3
"""Tests for checkpoint + bisect. Run: python3 -m unittest discover -s tests"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import verify


# Project gate: scans every f*.py in cwd. Fails if any contains "BAD".
# Counts its own invocations so tests can assert on gate economy.
PROJECT_GATE = '''\
import pathlib, sys
c = pathlib.Path("gate_calls"); c.write_text(str(int(c.read_text() or 0) + 1) if c.exists() else "1")
bad = any("BAD" in p.read_text() for p in pathlib.Path(".").glob("f*.py"))
sys.exit(1 if bad else 0)
'''

# Interaction gate: passes unless BOTH tokens P and Q are present.
INTERACTION_GATE = '''\
import pathlib, sys
c = pathlib.Path("gate_calls"); c.write_text(str(int(c.read_text() or 0) + 1) if c.exists() else "1")
text = "".join(p.read_text() for p in pathlib.Path(".").glob("f*.py"))
sys.exit(1 if ("P" in text and "Q" in text) else 0)
'''


class BisectBase(unittest.TestCase):
    gate_src = PROJECT_GATE

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / ".comrades").mkdir()
        (self.root / "pgate.py").write_text(self.gate_src)
        (self.root / ".comrades" / "verify.json").write_text(json.dumps({
            "gates": [{"name": "proj", "scope": "project", "timeout": 30,
                       "cmd": [sys.executable, "pgate.py"]}]
        }))

    def cli(self, *a):
        return verify.main(["--root", str(self.root), *a])

    def gate_calls(self) -> int:
        p = self.root / "gate_calls"
        return int(p.read_text()) if p.exists() else 0

    def make(self, n):
        files = []
        for i in range(n):
            f = self.root / f"f{i}.py"
            f.write_text(f"clean{i}\n")
            files.append(f)
        return files

    def stage_all(self, files, edits):
        """edits: dict index -> new content"""
        for i, f in enumerate(files):
            self.cli("begin", "--run", "r", "--file", str(f))
        for i, f in enumerate(files):
            if i in edits:
                f.write_text(edits[i])
            else:
                f.write_text(f"clean{i} edited\n")
            self.cli("stage", "--run", "r", "--file", str(f))


class TestCheckpointPass(BisectBase):
    def test_all_clean_kept_in_one_gate_run(self):
        files = self.make(8)
        self.stage_all(files, {})
        rc = self.cli("checkpoint", "--run", "r")
        self.assertEqual(rc, 0)
        self.assertEqual(self.gate_calls(), 1)  # no bisect needed
        for i, f in enumerate(files):
            self.assertEqual(f.read_text(), f"clean{i} edited\n")


class TestSingleCulprit(BisectBase):
    def test_one_bad_file_is_isolated_others_kept(self):
        files = self.make(8)
        self.stage_all(files, {5: "BAD edit\n"})
        rc = self.cli("checkpoint", "--run", "r")
        self.assertEqual(rc, 1)

        self.assertEqual(files[5].read_text(), "clean5\n")   # reverted
        for i, f in enumerate(files):
            if i != 5:
                self.assertEqual(f.read_text(), f"clean{i} edited\n")

    def test_bisect_cheaper_than_per_file_gating(self):
        files = self.make(16)
        self.stage_all(files, {11: "BAD edit\n"})
        self.cli("checkpoint", "--run", "r")
        # naive = one project gate per file = 16
        self.assertLess(self.gate_calls(), 16)


class TestMultipleCulprits(BisectBase):
    def test_two_bad_files_both_isolated(self):
        files = self.make(8)
        self.stage_all(files, {1: "BAD one\n", 6: "BAD two\n"})
        rc = self.cli("checkpoint", "--run", "r")
        self.assertEqual(rc, 1)

        self.assertEqual(files[1].read_text(), "clean1\n")
        self.assertEqual(files[6].read_text(), "clean6\n")
        for i in (0, 2, 3, 4, 5, 7):
            self.assertEqual(files[i].read_text(), f"clean{i} edited\n")


class TestInteraction(BisectBase):
    gate_src = INTERACTION_GATE

    def test_cross_half_interaction_flags_the_set_not_a_scapegoat(self):
        """Two edits that each pass alone but fail together must not
        result in one being blamed and the other silently kept."""
        files = self.make(4)
        # f0 gets P, f3 gets Q -> land in opposite halves
        self.stage_all(files, {0: "token P\n", 3: "token Q\n"})
        rc = self.cli("checkpoint", "--run", "r")
        self.assertEqual(rc, 1)

        # Whatever is kept, the tree must be GREEN. That is the invariant.
        text = "".join(f.read_text() for f in files)
        self.assertFalse("P" in text and "Q" in text)

        # innocent files must survive: only one edit needs to go
        kept = [i for i, f in enumerate(files)
                if f.read_text() != f"clean{i}\n"]
        self.assertGreaterEqual(len(kept), 2)

        out = [json.loads(l) for l in
               (self.root / ".comrades" / "runs" / "r" / "ledger.jsonl")
               .read_text().splitlines()]
        cp = [r for r in out if r["event"] == "checkpoint"][-1]
        self.assertEqual(cp["final"], "pass")


class TestCheckpointInvariants(BisectBase):
    def test_final_state_always_passes_the_gate(self):
        files = self.make(6)
        self.stage_all(files, {0: "BAD a\n", 3: "BAD b\n", 4: "BAD c\n"})
        self.cli("checkpoint", "--run", "r")
        text = "".join(f.read_text() for f in files)
        self.assertNotIn("BAD", text)

    def test_empty_checkpoint_is_noop(self):
        self.assertEqual(self.cli("checkpoint", "--run", "r"), 0)

    def test_revert_run_undoes_checkpoint_keeps(self):
        files = self.make(4)
        self.stage_all(files, {2: "BAD\n"})
        self.cli("checkpoint", "--run", "r")
        self.cli("revert-run", "--run", "r")
        for i, f in enumerate(files):
            self.assertEqual(f.read_text(), f"clean{i}\n")

    def test_report_counts_checkpoint_decisions(self):
        files = self.make(4)
        self.stage_all(files, {2: "BAD\n"})
        self.cli("checkpoint", "--run", "r")
        ledger = verify.Ledger(self.root, "r")
        decisions = [r["decision"] for r in ledger.records()
                     if r["event"] == "resolve"]
        self.assertEqual(sorted(decisions), ["keep", "keep", "keep", "reverted"])


class TestStage(BisectBase):
    def test_file_gate_failure_reverts_before_checkpoint(self):
        (self.root / ".comrades" / "verify.json").write_text(json.dumps({
            "gates": [
                {"name": "syn", "scope": "file", "timeout": 30,
                 "cmd": [sys.executable, "-m", "py_compile", "{file}"]},
                {"name": "proj", "scope": "project", "timeout": 30,
                 "cmd": [sys.executable, "pgate.py"]},
            ]}))
        f = self.root / "f0.py"
        f.write_text("x = 1\n")
        self.cli("begin", "--run", "r", "--file", str(f))
        f.write_text("def broken(\n")
        rc = self.cli("stage", "--run", "r", "--file", str(f))
        self.assertEqual(rc, 1)
        self.assertEqual(f.read_text(), "x = 1\n")
        # nothing staged -> checkpoint is a no-op, project gate never runs
        self.assertEqual(self.cli("checkpoint", "--run", "r"), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
