#!/usr/bin/env python3
"""
verify.py — deterministic per-file verification gate for Code Comrades.

Wraps each worker edit in: snapshot -> edit -> gate -> keep | revert.

Design notes:
  * Runs in the ORCHESTRATOR, never in the worker. Workers keep their
    no-shell sandbox; only this script executes gate commands.
  * Snapshots are content copies, not git. Works on dirty trees, gives
    per-file revert granularity, and never touches the user's index.
  * Differential gating: a file whose gate ALREADY failed before we
    touched it cannot be verified, so it is skipped, not reverted.
  * Fail-closed: gate crash/timeout counts as failure by default.

Zero dependencies beyond the Python standard library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

STATE_DIR = ".comrades"
CONFIG_NAME = "verify.json"

PASS, FAIL, ERROR = "pass", "fail", "error"
KEEP, REVERTED, SKIPPED, UNCHANGED = "keep", "reverted", "skipped", "unchanged"


# ---------------------------------------------------------------- config


DEFAULT_CONFIG: dict[str, Any] = {
    "gates": [],
    # what to do when the gate itself crashes or times out
    "on_gate_error": "revert",          # revert | keep
    # what to do when the gate was already failing before the edit
    "on_baseline_fail": "skip",         # skip | allow | revert
    "output_tail_chars": 2000,
}


def load_config(root: Path, config_override: Optional[str] = None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = Path(config_override) if config_override else root / STATE_DIR / CONFIG_NAME
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    for gate in cfg["gates"]:
        gate.setdefault("scope", "file")     # file | project
        gate.setdefault("timeout", 300)
        gate.setdefault("name", " ".join(gate["cmd"][:2]))
    return cfg


# ---------------------------------------------------------------- results


@dataclass
class GateResult:
    """Records the outcome of executing a single gate command against a file or project."""
    name: str
    status: str          # pass | fail | error
    exit_code: Optional[int]
    duration_ms: int
    output_tail: str


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "<absent>"
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- gating


def run_gate(gate: dict[str, Any], root: Path, file: Optional[Path],
             tail: int) -> GateResult:
    cmd = [
        part.replace("{file}", str(file)) if file else part
        for part in gate["cmd"]
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=gate["timeout"],
            # never inherit a stdin that a gate could block on
            stdin=subprocess.DEVNULL,
        )
        status = PASS if proc.returncode == 0 else FAIL
        code = proc.returncode
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        status, code, out = ERROR, None, f"timeout after {gate['timeout']}s"
    except (OSError, ValueError) as exc:
        status, code, out = ERROR, None, f"{type(exc).__name__}: {exc}"

    return GateResult(
        name=gate["name"],
        status=status,
        exit_code=code,
        duration_ms=int((time.monotonic() - started) * 1000),
        output_tail=out[-tail:],
    )


class GateLock:
    """Cross-platform mutex (atomic mkdir; no fcntl, works on Windows).

    A project-scoped gate reads the entire tree, so it must never observe
    another worker's half-applied edit. File-scoped gates need no lock.
    """

    def __init__(self, root: Path, timeout: int = 900):
        self.path = root / STATE_DIR / "gate.lock"
        self.timeout = timeout
        self.held = False

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir()
                self.held = True
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"gate lock held >{self.timeout}s")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self.held:
            try:
                self.path.rmdir()
            except OSError:
                pass
        return False


def run_gates(cfg: dict[str, Any], root: Path, file: Path,
              scopes: tuple[str, ...] = ("file", "project")) -> list[GateResult]:
    active = [g for g in cfg["gates"] if g["scope"] in scopes]
    if any(g["scope"] == "project" for g in active):
        with GateLock(root):
            return _run_gates_inner(cfg, root, file, scopes)
    return _run_gates_inner(cfg, root, file, scopes)


def _run_gates_inner(cfg: dict[str, Any], root: Path, file: Path,
                     scopes: tuple[str, ...]) -> list[GateResult]:
    results = []
    for gate in cfg["gates"]:
        if gate["scope"] not in scopes:
            continue
        target = file if gate["scope"] == "file" else None
        res = run_gate(gate, root, target, cfg["output_tail_chars"])
        results.append(res)
        if res.status != PASS:
            break  # short-circuit: first failing gate decides
    return results


def verdict(results: list[GateResult], cfg: dict[str, Any]) -> str:
    """Collapse gate results into pass | fail | error."""
    if any(r.status == ERROR for r in results):
        return ERROR
    if any(r.status == FAIL for r in results):
        return FAIL
    return PASS


def combine_baseline(file_base: str, project_base: Optional[str]) -> str:
    """Merge a file-scope baseline with a run-level project-scope baseline.

    ERROR outranks FAIL outranks PASS. A missing project baseline (the
    `baseline` command was never called this run) leaves file_base
    unchanged, so runs that skip `baseline` keep today's exact behavior.
    """
    if project_base is None:
        return file_base
    rank = {PASS: 0, FAIL: 1, ERROR: 2}
    return file_base if rank[file_base] >= rank[project_base] else project_base


# ---------------------------------------------------------------- ledger


class Ledger:
    """Append-only JSONL record of every decision. Enables resume + undo."""

    def __init__(self, root: Path, run_id: str):
        self.root = root
        self.dir = root / STATE_DIR / "runs" / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "ledger.jsonl"
        self.snapshots = self.dir / "snapshots"
        self.snapshots.mkdir(exist_ok=True)

    def append(self, record: dict[str, Any]) -> None:
        record["ts"] = time.time()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def latest_by_file(self, event: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for rec in self.records():
            if rec.get("event") == event:
                out[rec["file"]] = rec
        return out

    # -- snapshots -----------------------------------------------------

    def snapshot(self, file: Path, root: Path) -> str:
        # `file` is root-relative (it's also used as a stable ledger key);
        # resolve against `root` explicitly rather than trusting the
        # process's CWD to happen to match it.
        abs_file = root / file
        digest = sha256_file(abs_file)
        blob = self.snapshots / digest
        if not blob.exists() and abs_file.exists():
            shutil.copy2(abs_file, blob)
        return digest

    def restore(self, digest: str, file: Path) -> bool:
        abs_file = self.root / file
        blob = self.snapshots / digest
        if not blob.exists():
            return False
        abs_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blob, abs_file)
        return True

    def resolved(self) -> set[str]:
        """Files that already reached a terminal decision this run."""
        out = set()
        for rec in self.records():
            if rec.get("event") in ("check", "resolve"):
                out.add(rec["file"])
            elif rec.get("event") == "stage":
                out.discard(rec["file"])
        return out

    def staged(self) -> dict[str, dict[str, Any]]:
        """file -> {orig, edited} for files staged but not yet resolved."""
        stage = {r["file"]: r for r in self.records() if r.get("event") == "stage"}
        done = self.resolved()
        return {f: r for f, r in stage.items() if f not in done}

    def project_baseline(self) -> Optional[str]:
        """Latest recorded project-gate baseline verdict for this run, or
        None if `baseline` was never called."""
        latest = None
        for rec in self.records():
            if rec.get("event") == "baseline_project":
                latest = rec["verdict"]
        return latest


def file_gates(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in cfg["gates"] if g["scope"] == "file"]


def project_gates(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return [g for g in cfg["gates"] if g["scope"] == "project"]


def run_project_gates(cfg: dict[str, Any], root: Path) -> list[GateResult]:
    results = []
    with GateLock(root):
        for gate in project_gates(cfg):
            res = run_gate(gate, root, None, cfg["output_tail_chars"])
            results.append(res)
            if res.status != PASS:
                break
    return results


# ---------------------------------------------------------------- commands


def cmd_baseline(args) -> int:
    """Capture the project-scoped gate baseline once, before any file in
    the run is touched. Safe to call with zero project gates configured
    (records verdict "pass") and safe to call more than once (the latest
    record wins via `Ledger.project_baseline`)."""
    root = Path(args.root).resolve()
    cfg = load_config(root, config_override=args.config)
    ledger = Ledger(root, args.run)

    results = run_project_gates(cfg, root)
    base = verdict(results, cfg)

    ledger.append({
        "event": "baseline_project",
        "file": "*",
        "verdict": base,
        "gates": [asdict(r) for r in results],
    })
    failing = next((r for r in results if r.status == ERROR), None)
    print(json.dumps({"verdict": base,
                      "gate_error": failing.output_tail if failing else None}))
    # Exit 3 == the project gate could not execute at all. Abort before any
    # worker is dispatched -- catching this here is strictly earlier than
    # the first per-file `begin` call would catch it.
    return 3 if base == ERROR else 0


def cmd_begin(args) -> int:
    """Snapshot the file and record its pre-edit gate status (baseline)."""
    root, file = Path(args.root).resolve(), Path(args.file)
    cfg = load_config(root, config_override=args.config)
    ledger = Ledger(root, args.run)

    digest = ledger.snapshot(file, root)

    # File-scope baseline for this one file, merged with the run-level
    # project-scope baseline captured once by `baseline` (if it was called).
    results = run_gates(cfg, root, file, scopes=("file",))
    project_base = ledger.project_baseline()
    # Project-scoped gates are configured but `baseline` was never called
    # for this run -- fail closed rather than silently falling back to a
    # file-only baseline, which would reopen the exact bug `baseline`
    # exists to close.
    contract_error = project_base is None and bool(project_gates(cfg))
    base = ERROR if contract_error else combine_baseline(verdict(results, cfg), project_base)

    ledger.append({
        "event": "begin",
        "file": str(file),
        "snapshot": digest,
        "baseline": base,
        "gates": [asdict(r) for r in results],
        "contract_error": contract_error,
    })
    failing = next((r for r in results if r.status == ERROR), None)
    hint = ("project-scoped gates are configured but `baseline` was never "
            "called for this run" if contract_error else None)
    print(json.dumps({"snapshot": digest, "baseline": base,
                      "gate_error": hint or (failing.output_tail if failing else None)}))
    # Exit 3 == the gate could not execute. The orchestrator must abort the
    # run rather than dispatch workers against a gate that cannot verify.
    return 3 if base == ERROR else 0


def cmd_check(args) -> int:
    """Run gates after the edit; revert the file if it regressed."""
    root, file = Path(args.root).resolve(), Path(args.file)
    cfg = load_config(root, config_override=args.config)
    ledger = Ledger(root, args.run)

    begins = ledger.latest_by_file("begin")
    rec = begins.get(str(file))
    if rec is None:
        print(json.dumps({"error": "no begin record; call `begin` first"}),
              file=sys.stderr)
        return 2

    post_digest = sha256_file(root / file)
    if post_digest == rec["snapshot"]:
        ledger.append({"event": "check", "file": str(file),
                       "decision": UNCHANGED, "gates": []})
        print(json.dumps({"decision": UNCHANGED}))
        return 0

    # The gate itself was broken before we touched anything (bad command,
    # missing binary, timeout). That is an infrastructure fault, not a
    # verdict on the edit. Never silently skip it -- a typo'd gate command
    # would otherwise no-op an entire 200-file run and report success.
    if rec["baseline"] == ERROR and cfg["on_gate_error"] != "keep":
        ledger.restore(rec["snapshot"], file)
        ledger.append({"event": "check", "file": str(file),
                       "decision": REVERTED,
                       "reason": "gate_error_at_baseline", "gates": []})
        print(json.dumps({"decision": REVERTED,
                          "reason": "gate_error_at_baseline",
                          "hint": "gate command failed to execute; abort run"}))
        return 1

    # The file was already failing its gate before we touched it -> a
    # failure now cannot be attributed to this edit. Honour the policy.
    if rec["baseline"] == FAIL and cfg["on_baseline_fail"] == "skip":
        ledger.restore(rec["snapshot"], file)
        ledger.append({"event": "check", "file": str(file),
                       "decision": SKIPPED,
                       "reason": f"baseline={rec['baseline']}", "gates": []})
        print(json.dumps({"decision": SKIPPED,
                          "reason": f"baseline={rec['baseline']}"}))
        return 0

    results = run_gates(cfg, root, file)
    outcome = verdict(results, cfg)

    keep = outcome == PASS
    if outcome == ERROR and cfg["on_gate_error"] == "keep":
        keep = True
    if rec["baseline"] == FAIL and cfg["on_baseline_fail"] == "allow":
        keep = True

    decision = KEEP
    if not keep:
        ledger.restore(rec["snapshot"], file)
        decision = REVERTED

    ledger.append({
        "event": "check",
        "file": str(file),
        "decision": decision,
        "outcome": outcome,
        "post_sha": post_digest,
        "gates": [asdict(r) for r in results],
    })
    failing = next((r for r in results if r.status != PASS), None)
    print(json.dumps({
        "decision": decision,
        "outcome": outcome,
        "failing_gate": failing.name if failing else None,
        "output_tail": failing.output_tail if failing else "",
    }))
    return 0 if decision in (KEEP, UNCHANGED) else 1


def cmd_stage(args) -> int:
    """Run only the cheap file-scoped gates; bank the edit for a later
    project-gate checkpoint. Regressions are reverted immediately."""
    root, file = Path(args.root).resolve(), Path(args.file)
    cfg = load_config(root, config_override=args.config)
    ledger = Ledger(root, args.run)

    begins = ledger.latest_by_file("begin")
    rec = begins.get(str(file))
    if rec is None:
        print(json.dumps({"error": "no begin record"}), file=sys.stderr)
        return 2

    edited = sha256_file(root / file)
    if edited == rec["snapshot"]:
        ledger.append({"event": "check", "file": str(file),
                       "decision": UNCHANGED, "gates": []})
        print(json.dumps({"decision": UNCHANGED}))
        return 0

    if rec["baseline"] == ERROR and cfg["on_gate_error"] != "keep":
        ledger.restore(rec["snapshot"], file)
        ledger.append({"event": "check", "file": str(file),
                       "decision": REVERTED,
                       "reason": "gate_error_at_baseline", "gates": []})
        print(json.dumps({"decision": REVERTED,
                          "reason": "gate_error_at_baseline"}))
        return 1

    if rec["baseline"] == FAIL and cfg["on_baseline_fail"] == "skip":
        ledger.restore(rec["snapshot"], file)
        ledger.append({"event": "check", "file": str(file),
                       "decision": SKIPPED, "reason": "baseline=fail",
                       "gates": []})
        print(json.dumps({"decision": SKIPPED, "reason": "baseline=fail"}))
        return 0

    results = _run_gates_inner(cfg, root, file, ("file",))
    outcome = verdict(results, cfg)
    keep = outcome == PASS or (outcome == ERROR and cfg["on_gate_error"] == "keep")
    if rec["baseline"] == FAIL and cfg["on_baseline_fail"] == "allow":
        keep = True

    if not keep:
        ledger.restore(rec["snapshot"], file)
        ledger.append({"event": "check", "file": str(file),
                       "decision": REVERTED, "outcome": outcome,
                       "gates": [asdict(r) for r in results]})
        bad = next((r for r in results if r.status != PASS), None)
        print(json.dumps({"decision": REVERTED, "outcome": outcome,
                          "failing_gate": bad.name if bad else None,
                          "output_tail": bad.output_tail if bad else ""}))
        return 1

    # bank the edited bytes so a checkpoint can re-apply or drop them
    ledger.snapshot(file, root)
    ledger.append({"event": "stage", "file": str(file),
                   "orig": rec["snapshot"], "edited": edited,
                   "gates": [asdict(r) for r in results]})
    print(json.dumps({"decision": "staged", "edited": edited}))
    return 0


# ------------------------------------------------------- checkpoint/bisect


def _apply(ledger: Ledger, staged: dict[str, dict[str, Any]],
           subset: set[str]) -> None:
    """Materialize exactly `subset` as edited; everything else original."""
    for f, rec in staged.items():
        ledger.restore(rec["edited"] if f in subset else rec["orig"], Path(f))


def bisect_culprits(cfg, root, ledger, staged, counter: list[int]) -> list[str]:
    """Delta-debug the staged set down to the edits responsible for failure.

    Returns a minimal-ish culprit list. If two halves each pass alone but
    fail together, the interaction cannot be attributed to any single file,
    so the whole candidate set is returned rather than blaming one at random.
    """

    def fails(subset: list[str]) -> bool:
        _apply(ledger, staged, set(subset))
        counter[0] += 1
        return verdict(run_project_gates(cfg, root), cfg) != PASS

    def minimize(cands: list[str]) -> list[str]:
        """No single half fails alone -> the failure is an interaction.
        Probe whether dropping one edit clears it before condemning the
        whole set. Costs len(cands) gate runs, but only on this branch."""
        for f in cands:
            if not fails([c for c in cands if c != f]):
                return [f]
        return cands

    def recurse(cands: list[str]) -> list[str]:
        if len(cands) == 1:
            return cands
        mid = len(cands) // 2
        left, right = cands[:mid], cands[mid:]
        fl, fr = fails(left), fails(right)
        if fl and fr:
            return recurse(left) + recurse(right)
        if fl:
            return recurse(left)
        if fr:
            return recurse(right)
        return minimize(cands)

    return recurse(sorted(staged))


def cmd_checkpoint(args) -> int:
    """Run the project gate once over all staged edits; bisect on failure."""
    root = Path(args.root).resolve()
    cfg = load_config(root, config_override=args.config)
    ledger = Ledger(root, args.run)
    staged = ledger.staged()

    if not staged:
        print(json.dumps({"checkpoint": "empty"}))
        return 0
    if not project_gates(cfg):
        for f in staged:
            ledger.append({"event": "resolve", "file": f, "decision": KEEP})
        print(json.dumps({"kept": sorted(staged), "culprits": [],
                          "gate_runs": 0}))
        return 0

    proj_base = ledger.project_baseline()
    if proj_base not in (None, PASS) and cfg["on_baseline_fail"] == "allow":
        # The project gate was already broken before this batch touched
        # anything, and policy says keep edits regardless. cmd_stage only
        # ever re-checks file-scope gates, so without this, a file staged
        # under `allow` would still reach bisection below and get reverted
        # for a failure that predates it -- exactly what `allow` should
        # prevent.
        for f in staged:
            ledger.append({"event": "resolve", "file": f, "decision": KEEP})
        print(json.dumps({"kept": sorted(staged), "culprits": [],
                          "gate_runs": 0}))
        return 0

    counter = [0]
    _apply(ledger, staged, set(staged))
    counter[0] += 1
    results = run_project_gates(cfg, root)
    outcome = verdict(results, cfg)

    if outcome == PASS:
        for f in staged:
            ledger.append({"event": "resolve", "file": f, "decision": KEEP})
        print(json.dumps({"kept": sorted(staged), "culprits": [],
                          "gate_runs": counter[0]}))
        return 0

    if outcome == ERROR and cfg["on_gate_error"] == "keep":
        for f in staged:
            ledger.append({"event": "resolve", "file": f, "decision": KEEP})
        print(json.dumps({"kept": sorted(staged), "culprits": [],
                          "outcome": ERROR, "gate_runs": counter[0]}))
        return 0

    culprits = bisect_culprits(cfg, root, ledger, staged, counter)
    kept = [f for f in sorted(staged) if f not in set(culprits)]

    # settle on the kept set and confirm the tree is actually green again
    _apply(ledger, staged, set(kept))
    counter[0] += 1
    final = verdict(run_project_gates(cfg, root), cfg)
    if final != PASS:
        _apply(ledger, staged, set())          # nothing kept; full rollback
        culprits, kept = sorted(staged), []

    bad = next((r for r in results if r.status != PASS), None)
    for f in kept:
        ledger.append({"event": "resolve", "file": f, "decision": KEEP})
    for f in culprits:
        ledger.append({"event": "resolve", "file": f, "decision": REVERTED,
                       "reason": "project_gate",
                       "gates": [asdict(bad)] if bad else []})

    ledger.append({"event": "checkpoint", "file": "*", "kept": kept,
                   "culprits": culprits, "gate_runs": counter[0],
                   "final": final})
    print(json.dumps({"kept": kept, "culprits": culprits,
                      "gate_runs": counter[0], "final": final,
                      "failing_gate": bad.name if bad else None,
                      "output_tail": bad.output_tail if bad else ""}))
    return 1 if culprits else 0


def cmd_revert_run(args) -> int:
    """Undo every kept edit from a run, newest-first."""
    root = Path(args.root).resolve()
    ledger = Ledger(root, args.run)
    begins = ledger.latest_by_file("begin")

    reverted = []
    for rec in reversed(ledger.records()):
        if rec.get("event") not in ("check", "resolve"):
            continue
        if rec.get("decision") != KEEP:
            continue
        f = rec["file"]
        if f in reverted:
            continue
        snap = begins.get(f, {}).get("snapshot")
        if snap and ledger.restore(snap, Path(f)):
            reverted.append(f)

    ledger.append({"event": "revert_run", "file": "*", "reverted": reverted})
    print(json.dumps({"reverted": reverted, "count": len(reverted)}))
    return 0


def cmd_report(args) -> int:
    root = Path(args.root).resolve()
    ledger = Ledger(root, args.run)
    tally: dict[str, int] = {}
    failures = []
    for rec in ledger.records():
        if rec.get("event") not in ("check", "resolve"):
            continue
        tally[rec["decision"]] = tally.get(rec["decision"], 0) + 1
        if rec["decision"] == REVERTED:
            bad = next((g for g in rec.get("gates", [])
                        if g["status"] != PASS), None)
            failures.append({"file": rec["file"],
                             "gate": bad["name"] if bad else "?",
                             "output_tail": bad["output_tail"] if bad else ""})
    print(json.dumps({"run": args.run, "tally": tally,
                      "failures": failures}, indent=2))
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="verify.py")
    p.add_argument("--root", default=".")
    p.add_argument("--config", default=None,
        help="Path to gate config JSON. Overrides {root}/.comrades/verify.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn, needs_file in (
        ("baseline", cmd_baseline, False),
        ("begin", cmd_begin, True),
        ("check", cmd_check, True),
        ("stage", cmd_stage, True),
        ("checkpoint", cmd_checkpoint, False),
        ("revert-run", cmd_revert_run, False),
        ("report", cmd_report, False),
    ):
        sp = sub.add_parser(name)
        sp.add_argument("--run", required=True)
        if needs_file:
            sp.add_argument("--file", required=True)
        sp.set_defaults(func=fn)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
