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
                          "COMPARATOR_FAKE_LAKE_OK": "1"})
    for k in ("SLURM_JOB_ID", "COMPARATOR_RECEIPT_ALLOWED_HOSTS"):
        env.pop(k, None)
    for k in [k for k in env if k.startswith("LEAN_PKG_")]:
        env.pop(k)
    env.update(extra or {})
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


# --- progress lines, partial report, stages.json progress ---------------------------------------

def two_configs(tmp_path: Path) -> Path:
    return make_project(tmp_path, {"comparator.json": ["A.one", "A.two"],
                                   "comparator/config-b.json": ["B.three"]})


def stages_json(project: Path) -> dict:
    return json.loads((stage_dir(project) / "stages.json").read_text())


def test_progress_lines_name_slots_and_times(tmp_path: Path):
    project = two_configs(tmp_path)
    r = run(project)
    assert r.returncode == 0, r.stderr
    log = stage_log(project)
    assert "3 slot(s) in 2 config(s), 2 judge invocation(s)" in log
    assert "slot 1-2/3 comparator.json[2 names] started" in log
    assert "slot 3/3 B.three started" in log
    assert "slot 1-2/3 comparator.json[2 names] done in " in log
    assert "slot 3/3 B.three done in " in log and "(3/3 done)" in log
    st = stages_json(project)
    assert st["progress"] == {"done": 3, "total": 3, "failed": 0, "partial_report": None}
    assert not (stage_dir(project) / "04-comparator.partial.json").exists()
    assert receipt_log(project).splitlines()[0] == "== lake comparator (toolchain) comparator.json"


def test_failed_unit_leaves_a_partial_report(tmp_path: Path):
    project = two_configs(tmp_path)
    r = run(project, extra={"STUB_MODE": "fail"})
    assert r.returncode == 1
    assert "partial: 0/3 done, 2 failed" in r.stderr
    rep = json.loads((stage_dir(project) / "04-comparator.partial.json").read_text())
    assert rep["schema"] == "comparator-partial/1" and rep["state"] == "failed"
    assert [s["name"] for s in rep["slots_failed"]] == ["A.one", "A.two"]
    assert [s["name"] for s in rep["slots_pending"]] == ["B.three"]
    assert "failed (exit 1)" in stage_log(project)
    st = stages_json(project)
    assert st["failed_stage"] == "comparator"
    assert st["progress"]["partial_report"].endswith("04-comparator.partial.json")


def test_term_writes_partial_report_with_resume_command(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": ["A.one"],
                                      "comparator/config-b.json": ["B.two"]})
    p = start(project, "--evidence-dir", str(tmp_path / "ev"),
              extra={"STUB_BEHAVIOUR": "slow", "STUB_SLEEP": "30"})
    ev = tmp_path / "ev" / "comparator-stages"
    assert wait_for(lambda: (ev / "04-comparator.log").exists()
                    and "stub progress 1" in (ev / "04-comparator.log").read_text())
    p.send_signal(signal.SIGTERM)
    out, err = p.communicate(timeout=60)
    assert p.returncode == 128 + signal.SIGTERM
    assert "partial: 0/2 done" in err and "resume: comparator-receipt" in err
    rep = json.loads((ev / "04-comparator.partial.json").read_text())
    assert rep["state"] == "interrupted" and rep["done"] == 0 and rep["total"] == 2
    assert "--evidence-dir" in rep["resume_command"]
    st = json.loads((ev / "stages.json").read_text())
    comp = [e for e in st["stages"] if e["stage"] == "comparator"][0]
    assert comp["status"] == "partial" and "partial: 0/2 done" in comp["note"]
    assert st["progress"]["done"] == 0 and st["progress"]["total"] == 2
    assert not list(ev.glob("04-units/*.tmp"))       # the judge's temp exports are cleaned up


# --- resume -----------------------------------------------------------------------------------

def calls(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def test_resume_skips_accepted_slots_and_judges_the_rest(tmp_path: Path):
    project = two_configs(tmp_path)
    log = tmp_path / "calls.txt"
    r = run(project, extra={"STUB_FAIL_MATCH": "B.three", "STUB_CALLS": str(log)})
    assert r.returncode == 1
    rep = json.loads((stage_dir(project) / "04-comparator.partial.json").read_text())
    assert rep["done"] == 2 and [s["name"] for s in rep["slots_failed"]] == ["B.three"]
    assert len(list((stage_dir(project) / "04-slots").glob("*.json"))) == 2
    first_log = (stage_dir(project) / "04-slots" / "logs")
    assert len(list(first_log.glob("*.log"))) == 1
    kept = next(first_log.glob("*.log")).read_bytes()
    log.unlink()
    r = run(project, extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert [c.split()[0] for c in calls(log)] == ["comparator/config-b.json"]   # only the rest
    assert "slot 1-2/3 comparator.json[2 names] cached" in stage_log(project)
    rec = receipt(project)
    assert rec["resumed_slots"] == 2
    head = git(project, "rev-parse", "HEAD")
    assert (project / ".lean-receipts" / f"{head[:12]}-comparator-comparator.log").read_bytes() == kept
    assert "Your solution is okay!" in (project / ".lean-receipts"
                                        / f"{head[:12]}-comparator-comparator_config-b.log").read_text()


def test_no_resume_judges_everything_again(tmp_path: Path):
    project = two_configs(tmp_path)
    log = tmp_path / "calls.txt"
    assert run(project, extra={"STUB_FAIL_MATCH": "B.three"}).returncode == 1
    r = run(project, "--no-resume", extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert len(calls(log)) == 2 and "cached" not in stage_log(project)
    assert "resumed_slots" not in receipt(project)


def test_tampered_cached_log_is_judged_again(tmp_path: Path):
    project = two_configs(tmp_path)
    log = tmp_path / "calls.txt"
    assert run(project, extra={"STUB_FAIL_MATCH": "B.three"}).returncode == 1
    kept = next((stage_dir(project) / "04-slots" / "logs").glob("*.log"))
    kept.write_text(kept.read_text().replace("Your solution is okay!", "Your solution is okay?"))
    r = run(project, extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert len(calls(log)) == 2


def test_cache_does_not_cross_a_config_change(tmp_path: Path):
    project = two_configs(tmp_path)
    log = tmp_path / "calls.txt"
    assert run(project, extra={"STUB_FAIL_MATCH": "B.three"}).returncode == 1
    cfg = json.loads((project / "comparator/config-b.json").read_text())
    cfg["theorem_names"] = ["B.four"]
    (project / "comparator/config-b.json").write_text(json.dumps(cfg) + "\n")
    git(project, "commit", "-qam", "change config")
    r = run(project, extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert len(calls(log)) == 2          # new commit and config hash: nothing reused


# --- sharding (--judges) ----------------------------------------------------------------------

FIVE = ["N.a", "N.b", "N.c", "N.d", "N.e"]


def validate(lp, project: Path) -> dict:
    """The receipt through the real validator (test_only stripped: it marks the fake digests)."""
    comp = receipt(project)
    comp.pop("test_only", None)
    lp.validate_comparator({}, project, comp, None)
    return comp


def test_shards_are_contiguous_balanced_and_exact():
    from lean_pkg.comparator_stage import shards
    xs = list(range(1, 12))
    for n in range(1, 14):
        parts = shards(xs, n)
        assert [x for p in parts for x in p] == xs                 # a partition, order kept
        assert len(parts) == min(n, len(xs)) and all(parts)
        assert max(map(len, parts)) - min(map(len, parts)) <= 1


def test_judges_shard_a_config_and_the_merge_validates(tmp_path: Path, lp):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    log = tmp_path / "calls.txt"
    r = run(project, "--judges", "2", extra={"STUB_CALLS": str(log), "STUB_BEHAVIOUR": "slow",
                                             "STUB_SLEEP": "0.2"})
    assert r.returncode == 0, r.stderr
    judged = [c.split()[1].split(",") for c in calls(log)]
    assert sorted(judged) == [["N.a", "N.b", "N.c"], ["N.d", "N.e"]]   # exact partition
    assert all(c.split()[0].endswith(".json") and "04-units" in c for c in calls(log))
    assert "2 judge invocation(s), 2 at a time" in stage_log(project)
    assert "[1] stub progress 1" in stage_log(project) and "[2] stub progress 1" in stage_log(project)
    comp = validate(lp, project)
    assert comp["judges"] == 2
    units = comp["configs"][0]["units"]
    assert [u["slots"] for u in units] == [["N.a", "N.b", "N.c"], ["N.d", "N.e"]]
    text = receipt_log(project)
    assert text.count("Your solution is okay!") == 2 and text.count("== unit: slots ") == 2
    assert comp["configs"][0]["theorem_names"] == FIVE          # the configuration, not a shard


def test_one_rejecting_shard_rejects_the_config(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    r = run(project, "--judges", "2", extra={"STUB_FAIL_MATCH": "N.d"})
    assert r.returncode == 1
    assert not (project / ".lean-receipts").exists()
    rep = json.loads((stage_dir(project) / "04-comparator.partial.json").read_text())
    assert [s["name"] for s in rep["slots_failed"]] == ["N.d", "N.e"]


def test_jobs_env_and_bad_values(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    r = run(project, extra={"LEAN_PKG_COMPARATOR_JOBS": "5"})
    assert r.returncode == 0, r.stderr
    assert "5 judge invocation(s), 5 at a time" in stage_log(project)
    assert receipt(project)["judges"] == 5
    for bad in ("0", "x"):
        r = run(project, "--judges", bad)
        assert r.returncode == 2 and "positive integer" in r.stderr
    r = run(project, extra={"LEAN_PKG_COMPARATOR_JOBS": "-1"})
    assert r.returncode == 2 and "LEAN_PKG_COMPARATOR_JOBS" in r.stderr


def test_legacy_refuses_judges(project: Path):
    r = run(project, "--judges", "2", mechanism="legacy", extra={"COMPARATOR_FAKE_LAKE_OK": "0"})
    assert r.returncode == 1 and "cannot be sharded" in r.stderr
