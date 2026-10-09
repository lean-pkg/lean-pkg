#!/usr/bin/env python3
"""comparator-receipt stage 04 (#39): judges stream to the stage log, never deadlock, heartbeat while
silent, and die with the runner."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from .conftest import RECEIPT_CLI, ROOT, subprocess_env

STUB_TC = ROOT / "tests" / "fixtures" / "verify-comparator-stub.sh"
STUB = ROOT / "tests" / "fixtures" / "comparator-check-stub.sh"
AXIOMS = ["propext", "Quot.sound", "Classical.choice"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
         "-c", "user.name=Tests", *args],
        check=True, capture_output=True, text=True).stdout.strip()


def make_project(tmp_path: Path, configs: dict | None = None) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    git(p, "init")
    (p / "lean-toolchain").write_text("leanprover/lean4:v4.35.0-rc2\n")
    configs = configs or {"comparator.json": ["foo"]}
    for rel, names in configs.items():
        (p / rel).parent.mkdir(parents=True, exist_ok=True)
        (p / rel).write_text(json.dumps({
            "challenge_module": "Challenge", "solution_module": "Solution",
            "theorem_names": names, "definition_names": [],
            "permitted_axioms": AXIOMS, "enable_nanoda": True}, indent=2) + "\n")
    git(p, "add", "-A")
    git(p, "commit", "-m", "init")
    return p


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def runner_env(extra: dict | None = None) -> dict:
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "COMPARATOR_VERIFY": str(STUB_TC),
                          "STUB_MODE": "pass", "COMPARATOR_RECEIPT_ALLOW_HOST": "1",
                          "COMPARATOR_FAKE_LAKE_OK": "1", **(extra or {})})
    for k in ("SLURM_JOB_ID", "COMPARATOR_RECEIPT_ALLOWED_HOSTS"):
        env.pop(k, None)
    for k in [k for k in env if k.startswith("LEAN_PKG_")]:
        env.pop(k)
    return env


def run(project: Path, *flags: str, mechanism: str = "toolchain", extra: dict | None = None,
        timeout: float = 60):
    return subprocess.run([*RECEIPT_CLI, "--mechanism", mechanism, *flags], cwd=project,
                          env=runner_env(extra), capture_output=True, text=True, timeout=timeout)


def start(project: Path, *flags: str, extra: dict | None = None, stdin=subprocess.DEVNULL):
    return subprocess.Popen([*RECEIPT_CLI, "--mechanism", "toolchain", *flags], cwd=project,
                            env=runner_env(extra), stdin=stdin, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def stage_dir(project: Path) -> Path:
    return project / ".lake" / "comparator-receipt" / "comparator-stages"


def stage_log(project: Path) -> str:
    p = stage_dir(project) / "04-comparator.log"
    return p.read_text(errors="replace") if p.exists() else ""


def wait_for(pred, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def receipt(project: Path) -> dict:
    head = git(project, "rev-parse", "HEAD")
    return json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())


# --- streaming and deadlock freedom ------------------------------------------------------------

def test_progress_reaches_the_stage_log_while_the_judge_runs(project: Path):
    """The field failure: output must be in 04-comparator.log before the judge exits, not after."""
    p = start(project, extra={"STUB_BEHAVIOUR": "slow", "STUB_SLEEP": "1"})
    try:
        assert wait_for(lambda: "stub progress 1" in stage_log(project)), stage_log(project)
        assert p.poll() is None                                    # still judging
        assert "Your solution is okay!" not in stage_log(project)
    finally:
        out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    assert "stub progress 3" in stage_log(project)


def test_python_judge_output_is_unbuffered(project: Path):
    p = start(project, extra={"STUB_BEHAVIOUR": "python", "STUB_SLEEP": "3"})
    try:
        assert wait_for(lambda: "stub python progress" in stage_log(project), 20), stage_log(project)
        assert p.poll() is None
    finally:
        p.communicate(timeout=60)
    assert p.returncode == 0


def test_judge_reading_stdin_gets_eof_not_a_hang(project: Path):
    """The runner's own stdin is a pipe nobody writes to; the judge must still see EOF (stdin=DEVNULL)."""
    p = start(project, extra={"STUB_BEHAVIOUR": "stdin"}, stdin=subprocess.PIPE)
    try:
        p.wait(timeout=20)                 # stdin stays open: communicate() would close it
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        pytest.fail("the judge inherited the runner's stdin and blocked on it")
    out, err = p.communicate()
    assert p.returncode == 0, err
    assert "stub stdin closed" in stage_log(project)


def test_judge_flooding_both_streams_does_not_deadlock(project: Path):
    r = run(project, extra={"STUB_BEHAVIOUR": "flood"}, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    log = stage_log(project)
    assert "e" * 1000 in log and "o" * 1000 in log
    assert "Your solution is okay!" in receipt_log(project)


def receipt_log(project: Path) -> str:
    head = git(project, "rev-parse", "HEAD")
    return (project / ".lean-receipts" / f"{head[:12]}-comparator-comparator.log").read_text()


def test_heartbeat_while_the_judge_is_silent(project: Path):
    r = run(project, extra={"STUB_BEHAVIOUR": "silent", "STUB_SLEEP": "1.5",
                            "COMPARATOR_HEARTBEAT_SECONDS": "0.3"})
    assert r.returncode == 0, r.stderr
    beats = [ln for ln in stage_log(project).splitlines() if ln.startswith("heartbeat:")]
    assert beats, stage_log(project)
    assert "judge silent" in beats[0] and "running" in beats[0]


def test_term_kills_the_judge_tree_and_keeps_the_log(project: Path, tmp_path: Path):
    pidfile = tmp_path / "judge.pid"
    p = start(project, extra={"STUB_BEHAVIOUR": "slow", "STUB_SLEEP": "30",
                              "STUB_PIDFILE": str(pidfile)})
    assert wait_for(lambda: "stub progress 1" in stage_log(project)), stage_log(project)
    judge_pid = int(pidfile.read_text())
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=30)
    assert p.returncode != 0
    assert "interrupted by SIGTERM" in err

    def gone():
        try:
            os.kill(judge_pid, 0)
        except ProcessLookupError:
            return True
        return False
    assert wait_for(gone, 15), "the judge outlived the runner"
    assert "stub progress 1" in stage_log(project)
    assert not (project / ".lean-receipts").exists()


def test_legacy_judge_streams_too(project: Path):
    r = run(project, mechanism="legacy", extra={"COMPARATOR_FAKE_LAKE_OK": "0"})
    assert r.returncode == 0, r.stderr
    assert "== comparator @ 5555" in stage_log(project)
