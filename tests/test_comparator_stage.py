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


# --- --unit-size (#60) ------------------------------------------------------------------------

def test_chunks_are_contiguous_units_of_at_most_k():
    from lean_pkg.comparator_stage import chunks
    xs = list(range(1, 12))
    for k in range(1, 14):
        parts = chunks(xs, k)
        assert [x for p in parts for x in p] == xs                 # a partition, order kept
        assert all(len(p) == k for p in parts[:-1]) and 1 <= len(parts[-1]) <= k
    assert chunks(xs, 1) == [[x] for x in xs]


def test_unit_size_cuts_units_and_judges_is_the_width(tmp_path: Path, lp):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    log = tmp_path / "calls.txt"
    r = run(project, "--unit-size", "2", extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert [c.split()[1] for c in calls(log)] == ["N.a,N.b", "N.c,N.d", "N.e"]   # in order, one at a time
    text = stage_log(project)
    assert text.count("units: 3 of at most 2 names, width 1") == 1
    assert "3 judge invocation(s), 1 at a time" in text
    comp = validate(lp, project)
    assert comp["judges"] == 1 and comp["unit_size"] == 2
    assert [u["slots"] for u in comp["configs"][0]["units"]] == [["N.a", "N.b"], ["N.c", "N.d"], ["N.e"]]
    assert receipt_log(project).count("== unit: slots ") == 3
    st = stages_json(project)
    assert st["judges"] == 1 and st["unit_size"] == 2


def test_unit_size_with_judges_runs_units_concurrently(tmp_path: Path, lp):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    log = tmp_path / "calls.txt"
    r = run(project, "--unit-size", "2", "--judges", "2",
            extra={"STUB_CALLS": str(log), "STUB_BEHAVIOUR": "slow", "STUB_SLEEP": "0.2"})
    assert r.returncode == 0, r.stderr
    assert sorted(c.split()[1] for c in calls(log)) == ["N.a,N.b", "N.c,N.d", "N.e"]
    assert "units: 3 of at most 2 names, width 2" in stage_log(project)
    assert "3 judge invocation(s), 2 at a time" in stage_log(project)
    comp = validate(lp, project)
    assert comp["judges"] == 2 and comp["unit_size"] == 2
    assert len(comp["configs"][0]["units"]) == 3


def test_unit_size_covering_the_config_is_one_whole_unit(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    log = tmp_path / "calls.txt"
    r = run(project, "--unit-size", "9", extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert [c.split()[0] for c in calls(log)] == ["comparator.json"]   # the configuration itself
    assert receipt_log(project).splitlines()[0] == "== lake comparator (toolchain) comparator.json"
    rec = receipt(project)
    assert rec["unit_size"] == 9 and "units" not in rec["configs"][0]


def test_default_records_unit_size_null(project: Path):
    r = run(project)
    assert r.returncode == 0, r.stderr
    assert receipt(project)["unit_size"] is None and receipt(project)["judges"] == 1
    assert stages_json(project)["unit_size"] is None
    assert "units: " not in stage_log(project)


def test_one_name_units_resume_exactly_where_a_kill_stopped(tmp_path: Path, lp):
    four = ["N.a", "N.b", "N.c", "N.d"]
    project = make_project(tmp_path, {"comparator.json": four})
    log = tmp_path / "calls.txt"
    p = start(project, "--unit-size", "1", "--judges", "1",
              extra={"STUB_CALLS": str(log), "STUB_HANG_MATCH": "N.c", "STUB_SLEEP": "30"})
    assert wait_for(lambda: "stub hanging" in stage_log(project))
    p.send_signal(signal.SIGTERM)
    _out, err = p.communicate(timeout=60)
    assert p.returncode == 128 + signal.SIGTERM
    assert "partial: 2/4 done" in err
    assert [c.split()[1] for c in calls(log)] == ["N.a", "N.b", "N.c"]   # sequential, one name each
    assert len(list((stage_dir(project) / "04-slots").glob("*.json"))) == 2
    rep = json.loads((stage_dir(project) / "04-comparator.partial.json").read_text())
    assert rep["state"] == "interrupted"
    assert [s["name"] for s in rep["slots_done"]] == ["N.a", "N.b"]
    assert [s["name"] for s in rep["slots_pending"]] == ["N.c", "N.d"]
    assert "--unit-size 1" in rep["resume_command"]
    log.unlink()
    r = run(project, "--unit-size", "1", extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert [c.split()[1] for c in calls(log)] == ["N.c", "N.d"]          # only the rest
    assert "slot 1-2/4 comparator.json[2 names] cached" in stage_log(project)
    assert "units: 2 of at most 1 names, width 1" in stage_log(project)
    comp = validate(lp, project)
    assert comp["resumed_slots"] == 2 and comp["unit_size"] == 1
    assert [u["slots"] for u in comp["configs"][0]["units"]] == [[n] for n in four]
    text = receipt_log(project)
    assert text.count("== unit: slots ") == 4 and text.count("Your solution is okay!") == 4
    assert [ln for ln in text.splitlines() if ln.startswith("== unit:")] == [
        f"== unit: slots {i} (1 names)" for i in range(1, 5)]


def test_unit_size_env_form(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    r = run(project, extra={"LEAN_PKG_COMPARATOR_UNIT_SIZE": "2"})
    assert r.returncode == 0, r.stderr
    assert "units: 3 of at most 2 names, width 1" in stage_log(project)
    assert receipt(project)["unit_size"] == 2


def test_unit_size_bad_values_exit_2_before_any_work(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": FIVE})
    log = tmp_path / "calls.txt"
    for bad in ("0", "-1", "x", "1.5", ""):
        r = run(project, "--unit-size", bad, extra={"STUB_CALLS": str(log)})
        assert r.returncode == 2 and "--unit-size must be a positive integer" in r.stderr, bad
    r = run(project, extra={"LEAN_PKG_COMPARATOR_UNIT_SIZE": "0", "STUB_CALLS": str(log)})
    assert r.returncode == 2 and "LEAN_PKG_COMPARATOR_UNIT_SIZE" in r.stderr
    assert not log.exists() and not stage_dir(project).exists()


def test_legacy_refuses_unit_size(project: Path):
    r = run(project, "--unit-size", "1", mechanism="legacy", extra={"COMPARATOR_FAKE_LAKE_OK": "0"})
    assert r.returncode == 1 and "cannot be cut into units" in r.stderr


# --- --slots ----------------------------------------------------------------------------------

def mk_slots(rows):
    from lean_pkg.comparator_stage import Slot
    return [Slot(i, c, n) for i, (c, n) in enumerate(rows, 1)]


def test_parse_slot_spec_forms_and_refusals():
    from lean_pkg.comparator_stage import parse_slot_spec
    sl = mk_slots([("a.json", "X.p"), ("a.json", "X.q"), ("a.json", "Y.r"), ("b.json", "X.p"),
                   ("b.json", "Z.s")])
    names = lambda spec: [(s.config, s.name) for s in parse_slot_spec(spec, sl)]   # noqa: E731
    assert names("2-3") == [("a.json", "X.q"), ("a.json", "Y.r")]
    assert names("Z.s,1") == [("a.json", "X.p"), ("b.json", "Z.s")]
    assert names("b.json:X.p") == [("b.json", "X.p")]
    assert names("4, 4,5") == [("b.json", "X.p"), ("b.json", "Z.s")]
    for bad, needle in (("X.p", "several configurations"), ("Nope", "no such slot"),
                        ("0", "1..5"), ("4-9", "1..5"), ("sample:0", "positive"), (" , ", "no slot")):
        with pytest.raises(ValueError, match=needle):
            parse_slot_spec(bad, sl)


def test_stratified_sample_spreads_across_namespaces():
    from lean_pkg.comparator_stage import stratified_sample
    rows = [("c.json", f"A.t{i}") for i in range(6)] + [("c.json", f"B.t{i}") for i in range(3)] \
        + [("c.json", "C.only")]
    picked = stratified_sample(mk_slots(rows), 3)
    assert sorted({s.name.split(".")[0] for s in picked}) == ["A", "B", "C"]
    assert [s.index for s in picked] == sorted(s.index for s in picked)
    assert len(stratified_sample(mk_slots(rows), 99)) == len(rows)
    assert stratified_sample(mk_slots(rows), 4) == stratified_sample(mk_slots(rows), 4)  # deterministic


def test_slots_subset_writes_a_partial_receipt_outside_the_repo(tmp_path: Path, lp):
    project = two_configs(tmp_path)
    log = tmp_path / "calls.txt"
    r = run(project, "--slots", "2,B.three", extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert "PARTIAL comparator receipt" in r.stdout and "diagnostic only" in r.stdout
    assert "refused as evidence" in r.stdout
    assert not (project / ".lean-receipts").exists()
    assert sorted(c.split()[1] for c in calls(log)) == ["A.two", "B.three"]
    head = git(project, "rev-parse", "HEAD")
    pr = stage_dir(project) / f"{head[:12]}-comparator.partial.json"
    comp = json.loads(pr.read_text())
    assert comp["partial"] is True and comp["slots_total"] == 3
    assert [s["name"] for s in comp["slots"]] == ["A.two", "B.three"]
    assert "--slots: 2 of 3 slot(s) (2-3)" in stage_log(project)
    comp.pop("test_only", None)
    with pytest.raises(lp.Fail, match="partial"):
        lp.validate_comparator({}, project, comp, None)
    log.unlink()
    r = run(project, extra={"STUB_CALLS": str(log)})          # the full run reuses what --slots judged
    assert r.returncode == 0, r.stderr
    assert [c.split()[1] for c in calls(log)] == ["A.one"]
    assert receipt(project)["resumed_slots"] == 2


def test_slots_refusals_write_nothing(project: Path):
    r = run(project, "--slots", "nope")
    assert r.returncode == 1 and "no such slot" in r.stderr and "nothing written" in r.stderr
    assert not stage_dir(project).exists()
    r = run(project, "--slots", "1", mechanism="legacy", extra={"COMPARATOR_FAKE_LAKE_OK": "0"})
    assert r.returncode == 1 and "cannot judge a subset" in r.stderr


def test_slots_selecting_everything_is_a_full_run(tmp_path: Path):
    project = two_configs(tmp_path)
    r = run(project, "--slots", "1-3")
    assert r.returncode == 0, r.stderr
    assert "partial" not in receipt(project)


# --- --estimate -------------------------------------------------------------------------------

def test_estimate_numbers_bounds_and_cap():
    from lean_pkg.comparator_receipt import estimate_numbers
    e = estimate_numbers([100.0, 200.0, 300.0], slots=250, judges=1)
    assert e["per_slot_seconds"] == 200.0 and e["estimated_seconds"] == 50000
    assert e["lower_bound_seconds"] == 300 and e["estimated_seconds_max"] == 75000
    # 3 of 250 slots sampled: below half coverage, so the cap uses the max model
    assert e["cap_model"] == "max" and "per_slot_max_seconds" in e["recommended_cap_formula"]
    assert e["recommended_cap"] == 94500 and e["recommended_cap"] % 300 == 0   # ceil_5min(1.25*75000+600)
    assert estimate_numbers([100.0], slots=250, judges=4)["estimated_seconds"] == 6250


def test_estimate_cap_model_switches_at_half_coverage():
    from lean_pkg.comparator_receipt import estimate_numbers
    half = estimate_numbers([100.0, 200.0, 300.0], slots=6, judges=1)     # 3 of 6: at half, mean model
    assert half["cap_model"] == "mean" and half["recommended_cap"] == 2100    # ceil_5min(1.25*1200+600)
    assert "per_slot_seconds" in half["recommended_cap_formula"]
    assert "per_slot_max_seconds" not in half["recommended_cap_formula"]
    below = estimate_numbers([100.0, 200.0, 300.0], slots=7, judges=1)    # 3 of 7: below half, max model
    assert below["cap_model"] == "max" and below["estimated_seconds"] == 1400
    assert below["recommended_cap"] == ceil5(1.25 * 2100 + 600)
    # the field case (#59): 2 of 16 slots at 69.5 s and 168.7 s; the mean model capped at 3000 s
    field = estimate_numbers([69.5, 168.7], slots=16, judges=1)
    assert field["estimated_seconds"] == 1906 and field["estimated_seconds_max"] == 2700
    assert ceil5(1.25 * field["estimated_seconds"] + 600) == 3000      # what the mean model gave
    assert field["cap_model"] == "max" and field["recommended_cap"] == 4200


def ceil5(x: float) -> int:
    import math
    return int(math.ceil(x / 300) * 300)


def test_sample_floor():
    from lean_pkg.comparator_receipt import sample_floor
    assert [sample_floor(n) for n in (1, 3, 5, 16, 20, 21, 100)] == [1, 3, 5, 5, 5, 6, 25]


def test_slot_weight_maps_the_longest_module_prefix(tmp_path: Path):
    from lean_pkg.comparator_receipt import olean_sizes, slot_weight
    lib = tmp_path / ".lake" / "build" / "lib"
    (lib / "Pkg" / "Big").mkdir(parents=True)
    (lib / "Pkg.olean").write_bytes(b"x" * 10)
    (lib / "Pkg" / "Big.olean").write_bytes(b"x" * 300)
    sizes = olean_sizes(tmp_path)
    assert sizes == {"Pkg": 10, "Pkg.Big": 300}
    assert slot_weight("Pkg.Big.thm", sizes) == (300, "Pkg.Big")
    assert slot_weight("Pkg.Big.Inner.thm", sizes) == (300, "Pkg.Big")
    assert slot_weight("Pkg.other", sizes) == (10, "Pkg")
    assert slot_weight("Pkg.Big", sizes) == (10, "Pkg")          # a proper prefix only
    assert slot_weight("Else.thm", sizes) == (0, None) and slot_weight("bare", sizes) == (0, None)


def test_weighted_sample_takes_the_heaviest_then_strata():
    from lean_pkg.comparator_stage import stratified_sample, weighted_sample
    sl = mk_slots([("c.json", f"N{i}.t") for i in range(20)])
    weights = {s.index: 10 * s.index for s in sl}                # N19 heaviest, then N18, ...
    picked, heavy = weighted_sample(sl, 5, weights)
    assert heavy == {20} and [s.index for s in picked] == [1, 2, 3, 4, 20]
    assert [s.index for s in stratified_sample(sl, 5)] == [1, 2, 3, 4, 5]   # strata alone miss it
    picked, heavy = weighted_sample(sl, 10, weights)             # k_heavy = ceil(0.2 * 10) = 2
    assert heavy == {19, 20} and len(picked) == 10
    assert weighted_sample(sl, 10, weights) == weighted_sample(sl, 10, weights)   # deterministic
    picked, heavy = weighted_sample(sl, 5, {})                   # no weights: the stratified sample
    assert heavy == set() and picked == stratified_sample(sl, 5)


def test_estimate_times_a_stratified_sample_and_writes_no_receipt(tmp_path: Path):
    names = [f"A.t{i}" for i in range(6)] + [f"B.t{i}" for i in range(3)] + ["C.only"]
    project = make_project(tmp_path, {"comparator.json": names})
    log = tmp_path / "calls.txt"
    r = run(project, "--estimate", "--json",       # default sample: the floor, max(5, ceil(2.5)) = 5
            extra={"STUB_CALLS": str(log), "STUB_BEHAVIOUR": "slow", "STUB_SLEEP": "0.1"})
    assert r.returncode == 0, r.stderr
    e = json.loads(r.stdout[r.stdout.index("{"):])
    assert e["schema"] == "comparator-estimate/2" and e["slots"] == 10
    assert e["sample_floor"] == 5 and e["sample_forced"] is False
    assert [row["name"] for row in e["sample"]] == ["A.t0", "A.t1", "B.t0", "B.t1", "C.only"]
    assert all(row["seconds"] >= 0.3 for row in e["sample"])        # three 0.1 s pauses each
    assert all(row["weight_bytes"] == 0 and row["heavy"] is False for row in e["sample"])   # no oleans
    assert e["per_slot_seconds"] >= 0.3 and e["recommended_cap"] % 300 == 0
    assert e["cap_model"] == "mean"                                  # 5 of 10: half coverage
    assert "--unit-size" in e["whole_unit_warning"] and "union" in e["whole_unit_warning"]
    for k in ("per_slot_seconds", "slots", "estimated_seconds", "estimated_seconds_max",
              "lower_bound_seconds", "recommended_cap"):
        assert k in e
    assert sorted(c.split()[1] for c in calls(log)) == ["A.t0", "A.t1", "B.t0", "B.t1", "C.only"]
    assert not (project / ".lean-receipts").exists()
    assert json.loads((stage_dir(project) / "04-comparator.estimate.json").read_text())["sample_floor"] == 5
    assert "--estimate: 5 sampled slot(s)" in stage_log(project)
    log.unlink()
    r = run(project, extra={"STUB_CALLS": str(log)})                # the real run reuses the sample
    assert r.returncode == 0, r.stderr
    assert receipt(project)["resumed_slots"] == 5
    assert len(calls(log)) == 1 and "C.only" not in calls(log)[0]


def test_estimate_includes_the_heaviest_olean(tmp_path: Path):
    names = [f"N{i}.t" for i in range(12)]
    project = make_project(tmp_path, {"comparator.json": names})
    (project / ".gitignore").write_text(".lake/\n")
    git(project, "add", ".gitignore")
    git(project, "commit", "-m", "ignore .lake")
    lib = project / ".lake" / "build" / "lib"
    lib.mkdir(parents=True)
    for i in range(11):                                              # N11 has no olean: weight 0
        (lib / f"N{i}.olean").write_bytes(b"x" * (100 + i))
    (lib / "N9.olean").write_bytes(b"x" * 5000)                      # the heaviest module
    r = run(project, "--estimate", "--json")
    assert r.returncode == 0, r.stderr
    e = json.loads(r.stdout[r.stdout.index("{"):])
    rows = {row["name"]: row for row in e["sample"]}
    assert len(rows) == 5 and "N9.t" in rows                         # strata alone pick N0..N4
    assert rows["N9.t"]["heavy"] is True and rows["N9.t"]["weight_bytes"] == 5000
    assert rows["N9.t"]["weight_module"] == "N9"
    assert [n for n, row in rows.items() if row["heavy"]] == ["N9.t"]   # k_heavy = ceil(0.2 * 5) = 1
    assert rows["N0.t"]["weight_bytes"] == 100 and rows["N0.t"]["heavy"] is False
    assert e["cap_model"] == "max"                                   # 5 of 12: below half coverage
    r = run(project, "--estimate")
    assert r.returncode == 0, r.stderr
    assert "N9.t" in r.stdout and "[heavy]" in r.stdout and "cap_model max" in r.stdout


def test_estimate_sample_floor_and_force_sample(tmp_path: Path):
    project = make_project(tmp_path, {"comparator.json": [f"A.t{i}" for i in range(10)]})
    r = run(project, "--estimate", "--sample", "3")
    assert r.returncode == 2 and "sample floor 5" in r.stderr and "--force-sample" in r.stderr
    assert "nothing written" in r.stderr and not stage_dir(project).exists()
    r = run(project, "--estimate", "--slots", "1-2")                 # --slots is held to the floor too
    assert r.returncode == 2 and "sample floor 5" in r.stderr
    r = run(project, "--estimate", "--sample", "3", "--force-sample", "--json")
    assert r.returncode == 0, r.stderr
    assert "warning: --force-sample" in r.stderr
    e = json.loads(r.stdout[r.stdout.index("{"):])
    assert e["sample_forced"] is True and len(e["sample"]) == 3 and e["sample_floor"] == 5
    r = run(project, "--estimate", "--sample", "3", "--force-sample")
    assert r.returncode == 0 and "WARNING: --force-sample" in r.stdout
    r = run(project, "--estimate", "--sample", "7", "--json", "--no-resume")   # above the floor: as asked
    assert r.returncode == 0, r.stderr
    e = json.loads(r.stdout[r.stdout.index("{"):])
    assert len(e["sample"]) == 7 and e["sample_forced"] is False


def test_estimate_text_output_and_refusals(project: Path):
    r = run(project, "--estimate")
    assert r.returncode == 0, r.stderr
    assert "recommended_cap" in r.stdout and "no receipt written" in r.stdout
    assert "whole_unit_warning:" in r.stdout and "cap_model mean" in r.stdout   # 1 of 1 slot
    assert run(project, "--sample", "3").returncode == 2
    assert run(project, "--force-sample").returncode == 2
    assert run(project, "--estimate", "--sample", "0").returncode == 2
    r = run(project, "--estimate", mechanism="legacy", extra={"COMPARATOR_FAKE_LAKE_OK": "0"})
    assert r.returncode == 1 and "legacy" in r.stderr


# --- #41: lake and lean resolved through elan per lean-toolchain, never a bare PATH lookup -------

def fake_lake(path: Path, lean_version: str, tag: str, calls_file: Path) -> None:
    path.write_text("#!/bin/sh\n"
                    f"echo \"{tag} ${{ELAN_TOOLCHAIN:-none}} lake $*\" >> {calls_file}\n"
                    f"if [ \"$1\" = --version ]; then echo 'Lake version 5.0.0 (Lean version {lean_version})'; fi\n"
                    "exit 0\n")
    path.chmod(0o755)


def toolchain_host(tmp_path: Path, elan: bool, system_version: str = "4.19.0"):
    """A fake elan home (elan + a lake of the project's Lean) and a fake system lake of another Lean
    first on PATH: the setup that made the field run use the wrong toolchain."""
    calls_file = tmp_path / "lake-calls.txt"
    home = tmp_path / "elan-home"
    (home / "bin").mkdir(parents=True)
    if elan:
        (home / "bin" / "elan").write_text("#!/bin/sh\nexit 0\n")
        (home / "bin" / "elan").chmod(0o755)
        fake_lake(home / "bin" / "lake", "4.35.0-rc2", "elan", calls_file)
    sysbin = tmp_path / "usr-bin"
    sysbin.mkdir()
    fake_lake(sysbin / "lake", system_version, "system", calls_file)
    extra = {"ELAN_HOME": str(home), "PATH": f"{sysbin}:{os.environ.get('PATH', '')}"}
    return extra, calls_file


def lake_project(tmp_path: Path) -> Path:
    project = make_project(tmp_path)
    (project / "lakefile.toml").write_text('name = "proj"\n')
    (project / "lake-manifest.json").write_text(json.dumps({"version": 7, "packages": []}) + "\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "lake project")
    return project


def prepare(project: Path, extra: dict):
    env = runner_env(extra)
    env.pop("COMPARATOR_FAKE_LAKE_OK", None)
    env.pop("COMPARATOR_LAKE", None)
    return subprocess.run([*RECEIPT_CLI, "--prepare"], cwd=project, env=env, capture_output=True,
                          text=True, timeout=60)


def test_setup_uses_elans_lake_pinned_to_lean_toolchain(tmp_path: Path):
    project = lake_project(tmp_path)
    extra, calls_file = toolchain_host(tmp_path, elan=True)
    r = prepare(project, extra)
    assert r.returncode == 0, r.stderr
    rows = calls(calls_file)
    assert rows and all(c.startswith("elan leanprover/lean4:v4.35.0-rc2 lake ") for c in rows), rows
    assert "elan leanprover/lean4:v4.35.0-rc2 lake build Challenge Solution" in rows
    res = stages_json(project)["toolchain_resolution"]
    assert res["source"] == "elan" and res["lake"].endswith("elan-home/bin/lake")
    assert "4.35.0-rc2" in res["version"]


def test_without_elan_a_mismatched_path_lake_is_refused(tmp_path: Path):
    project = lake_project(tmp_path)
    extra, calls_file = toolchain_host(tmp_path, elan=False)
    r = prepare(project, extra)
    assert r.returncode == 1
    assert "elan not found" in r.stderr and "4.19.0" in r.stderr and "4.35.0-rc2" in r.stderr
    assert not any("build" in c for c in calls(calls_file))
    assert not stage_dir(project).exists()


def test_without_elan_a_matching_path_lake_is_used_and_recorded(tmp_path: Path):
    project = lake_project(tmp_path)
    extra, _calls = toolchain_host(tmp_path, elan=False, system_version="4.35.0-rc2")
    r = prepare(project, extra)
    assert r.returncode == 0, r.stderr
    assert stages_json(project)["toolchain_resolution"]["source"] == "path"


def test_receipt_records_toolchain_resolution(tmp_path: Path):
    project = lake_project(tmp_path)
    extra, _calls = toolchain_host(tmp_path, elan=True)
    env = runner_env(extra)
    env.pop("COMPARATOR_LAKE", None)
    env["COMPARATOR_RECEIPT_NO_MATHLIB"] = "1"     # #62: the fixture's manifest has no Mathlib
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "toolchain"], cwd=project, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    res = receipt(project)["toolchain_resolution"]
    assert res["source"] == "elan" and "4.35.0-rc2" in res["version"]


# --- #41: receipts and logs never name host paths; --host-label ---------------------------------

def test_receipt_and_logs_carry_no_host_paths_and_still_validate(tmp_path: Path, lp):
    import socket
    home = tmp_path / "home" / "someone"
    home.mkdir(parents=True)
    tmpdir = home / "tmp"
    tmpdir.mkdir()
    project = make_project(home)
    r = run(project, "--host-label", "lab-node", extra={"HOME": str(home), "TMPDIR": str(tmpdir),
                                                        "STUB_BEHAVIOUR": "slow", "STUB_SLEEP": "0"})
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    files = sorted((project / ".lean-receipts").iterdir())
    assert len(files) == 2
    for f in files:
        text = f.read_text()
        assert "/home/" not in text and str(tmp_path) not in text, (f.name, text)
    assert "project <checkout>," in receipt_log(project)          # the stub printed its cwd
    st_log = stage_log(project)
    assert str(project) not in st_log and "<checkout>" in st_log
    comp = validate(lp, project)                                  # hashes cover bytes, not paths
    assert comp["host"] == "lab-node" and comp["host_label"] == "lab-node"
    assert "lab-node" in comp["job_id"] and socket.gethostname() not in json.dumps(comp)
    assert (project / ".lean-receipts" / f"{head[:12]}-comparator.json").is_file()


def test_evidence_dir_outside_the_checkout_never_reaches_a_committed_file(tmp_path: Path, lp):
    """A partial unit (--unit-size) hands the toolchain judge a config copy under the evidence dir;
    an evidence dir outside the checkout, HOME and TMPDIR must still not reach a committed log."""
    project = make_project(tmp_path, {"comparator.json": ["N.a", "N.b"]})
    ev = tmp_path / "scratch-cluster" / "ev"
    tmpdir = tmp_path / "t"
    tmpdir.mkdir()
    r = run(project, "--evidence-dir", str(ev), "--unit-size", "1",
            extra={"HOME": str(tmp_path / "no-home"), "TMPDIR": str(tmpdir)})
    assert r.returncode == 0, r.stderr
    files = sorted((project / ".lean-receipts").iterdir())
    assert len(files) == 2
    for f in files:
        text = f.read_text()
        assert str(ev) not in text and "scratch-cluster" not in text, (f.name, text)
    assert "<evidence>/comparator-stages/04-units/" in receipt_log(project)
    assert "scratch-cluster" not in (ev / "comparator-stages" / "04-comparator.log").read_text()
    validate(lp, project)


def test_configs_sharing_a_log_name_are_refused_before_anything_runs(tmp_path: Path):
    """comparator_config_a.json and comparator/config_a.json both map to comparator_config_a.log."""
    project = make_project(tmp_path, {"comparator_config_a.json": ["foo"], "comparator/config_a.json": ["bar"]})
    calls_file = tmp_path / "calls.txt"
    r = run(project, extra={"STUB_CALLS": str(calls_file)})
    assert r.returncode == 1
    assert "comparator_config_a.json and comparator/config_a.json" in r.stderr
    assert "comparator_config_a.log" in r.stderr and "nothing written" in r.stderr
    assert not (project / ".lean-receipts").exists() and not calls_file.exists()


def test_distinct_log_names_keep_their_existing_spelling():
    from lean_pkg.comparator_receipt import cfg_name, cfg_name_collision
    cfgs = ["comparator.json", "comparator_b.json", "comparator/config.json", "comparator/config_c.json"]
    assert [cfg_name(c) for c in cfgs] == ["comparator", "comparator_b", "comparator_config",
                                            "comparator_config_c"]
    assert cfg_name_collision(cfgs) is None
    assert cfg_name_collision(["comparator_config.json", "comparator/config.json"]) == (
        "comparator_config.json", "comparator/config.json", "comparator_config")


def test_evidence_dir_inside_the_checkout_keeps_the_checkout_spelling():
    from lean_pkg import comparator_stage as cs
    cs.set_redactions(Path("/work/u/pkg"), home="/home/u", tmp="/tmp", evidence=Path("/work/u/pkg/.lake/ev"))
    try:
        assert cs.redact("/work/u/pkg/.lake/ev/x") == "<checkout>/.lake/ev/x"
        cs.set_redactions(Path("/work/u/pkg"), home="/home/u", tmp="/tmp", evidence=Path("/home/u/ev"))
        assert cs.redact("/home/u/ev/x /home/u/evx /home/u/k") == "<evidence>/x <home>/evx <home>/k"
    finally:
        cs.set_redactions(None, home="", tmp="")


def test_redaction_respects_path_boundaries():
    from lean_pkg import comparator_stage as cs
    cs.set_redactions(Path("/work/u/pkg"), home="/home/u", tmp="/tmp")
    try:
        assert cs.redact("/home/u/x /home/u2/y /home/u") == "<home>/x /home/u2/y <home>"
        assert cs.redact("cd /work/u/pkg/.lake; /tmpfoo /tmp/a") == "cd <checkout>/.lake; /tmpfoo <tmp>/a"
        assert cs.redact_json({"a": ["/home/u/k"], "n": 3}) == {"a": ["<home>/k"], "n": 3}
    finally:
        cs.set_redactions(None, home="", tmp="")


def test_empty_host_label_is_refused(project: Path):
    r = run(project, "--host-label", " ")
    assert r.returncode == 2 and "--host-label" in r.stderr
