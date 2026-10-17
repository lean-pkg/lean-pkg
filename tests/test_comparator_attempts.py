#!/usr/bin/env python3
"""comparator-receipt attempts: a run that replaces an earlier run's stage files first keeps them, read-only,
as comparator-stages/attempts/NNN/ (numbers never reused); resume and setup reuse read the current stage files
only; `--attempts` lists them read-only; the receipt binds the current run only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

from .conftest import RECEIPT_CLI, subprocess_env
from .test_comparator_receipt import (_lake_project, _receipt, _stages, cr, git, project,  # noqa: F401
                                      run_staged)
from .test_comparator_stage import calls, receipt, run, stage_dir, two_configs

STAGES = Path(".lake") / "comparator-receipt" / "comparator-stages"


def _attempts(project: Path) -> Path:
    return project / STAGES / "attempts"


def _snapshot(d: Path) -> dict[str, bytes]:
    """Every regular file directly in d (and in 04-units/), by relative name."""
    out = {p.name: p.read_bytes() for p in d.iterdir() if p.is_file() and not p.name.endswith(".tmp")}
    units = d / "04-units"
    if units.is_dir():
        out.update({f"04-units/{p.name}": p.read_bytes() for p in units.iterdir()
                    if p.is_file() and not p.name.endswith(".tmp")})
    return out


def _list(project: Path, *flags: str) -> subprocess.CompletedProcess:
    env = subprocess_env({"COMPARATOR_FAKE_LAKE_OK": "1"})
    for k in ("COMPARATOR_RECEIPT_ALLOW_HOST", "SLURM_JOB_ID", "COMPARATOR_RECEIPT_ALLOWED_HOSTS"):
        env.pop(k, None)                       # read-only: needs no host policy
    return subprocess.run([*RECEIPT_CLI, "--attempts", *flags], cwd=project, env=env,
                          capture_output=True, text=True)


def _writable(p: Path) -> bool:
    return bool(p.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _chmod_tree(p: Path) -> None:
    for q in [p, *p.rglob("*")]:
        q.chmod(0o755 if q.is_dir() else 0o644)


def test_failed_run_then_retry_keeps_attempt_001_byte_identical_and_read_only(project: Path):
    _lake_project(project)
    r, _ = run_staged(project, stub_mode="fail")
    assert r.returncode == 1 and "stage 04-comparator failed" in r.stderr
    before = _snapshot(project / STAGES)
    assert "stages.json" in before and "04-comparator.log" in before
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    a = _attempts(project) / "001"
    assert "kept read-only as comparator-stages/attempts/001/" in r.stderr
    kept = _snapshot(a)
    meta = json.loads(kept.pop("attempt.json"))
    assert kept == before                                    # byte-identical
    assert meta["schema"] == "comparator-attempt/1" and meta["n"] == 1
    assert meta["verdict"] == "failed" and meta["failed_stage"] == "comparator"
    assert meta["commit"] == git(project, "rev-parse", "HEAD")
    old = json.loads(before["stages.json"])
    assert meta["started_at"] == old["started_at"] and meta["finished_at"] == old["updated_at"]
    assert meta["files"] == {k: hashlib.sha256(v).hexdigest() for k, v in sorted(before.items())}
    assert not _writable(a) and not any(_writable(p) for p in a.rglob("*"))
    assert _stages(project)["failed_stage"] is None          # the current run is the passing one
    assert not (_attempts(project) / "002").exists()


def test_success_after_two_failures_leaves_001_and_002(project: Path):
    _lake_project(project)
    assert run_staged(project, fail="build")[0].returncode == 1          # setup fails
    assert run_staged(project, stub_mode="fail")[0].returncode == 1      # comparison fails
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    names = sorted(p.name for p in _attempts(project).iterdir() if p.is_dir())
    assert names == ["001", "002"]
    m1 = json.loads((_attempts(project) / "001" / "attempt.json").read_text())
    m2 = json.loads((_attempts(project) / "002" / "attempt.json").read_text())
    assert (m1["verdict"], m1["failed_stage"]) == ("failed", "build")
    assert (m2["verdict"], m2["failed_stage"]) == ("failed", "comparator")
    assert (_attempts(project) / "NEXT").read_text() == "3\n"


def test_a_prepared_setup_carries_over_without_an_attempt(project: Path):
    _lake_project(project)
    assert run_staged(project, "--prepare")[0].returncode == 0
    r, _ = run_staged(project, "--prepare")                   # idempotent: nothing kept
    assert r.returncode == 0 and "already prepared" in r.stdout
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert not _attempts(project).exists()
    git(project, "add", "-A")
    git(project, "commit", "-m", "receipt")
    r, _ = run_staged(project)                                # a rerun after a pass keeps the pass
    assert r.returncode == 0, r.stderr
    m = json.loads((_attempts(project) / "001" / "attempt.json").read_text())
    assert m["verdict"] == "pass" and m["failed_stage"] is None and m["reason"].startswith("setup rerun")


def test_resume_reads_the_current_stages_never_an_attempt(tmp_path: Path):
    project = two_configs(tmp_path)
    log = tmp_path / "calls.txt"
    r = run(project, extra={"STUB_FAIL_MATCH": "B.three", "STUB_CALLS": str(log)})
    assert r.returncode == 1
    log.unlink()
    r = run(project, extra={"STUB_CALLS": str(log)})
    assert r.returncode == 0, r.stderr
    assert [c.split()[0] for c in calls(log)] == ["comparator/config-b.json"]   # resumed as before
    assert receipt(project)["resumed_slots"] == 2
    a = stage_dir(project) / "attempts" / "001"
    meta = json.loads((a / "attempt.json").read_text())
    assert meta["verdict"] == "failed" and "04-comparator.partial.json" in meta["files"]
    assert any(k.startswith("04-units/") for k in meta["files"])   # the per-unit judge logs
    assert not (a / "04-slots").exists()                     # the slot cache stays where resume reads it


def test_an_attempt_is_never_reused_as_setup(project: Path):
    _lake_project(project)
    assert run_staged(project, "--prepare")[0].returncode == 0
    assert run_staged(project, stub_mode="fail")[0].returncode == 1
    assert run_staged(project, "--prepare")[0].returncode == 0             # keeps the failed comparison
    a = _attempts(project) / "001"
    assert (a / "stages.json").is_file()
    kept = _snapshot(a)
    for flag in ("--from-prepared", "--evidence-dir"):
        n = len(run_staged(project, "--attempts")[1])
        r, calls_ = run_staged(project, flag, str(a))
        assert r.returncode == 1 and "a kept attempt" in r.stderr and "nothing written" in r.stderr
        assert len(calls_) == n                                # no lake call ran
    assert _snapshot(a) == kept
    assert not list(project.glob(".lean-receipts/*-comparator.json"))


def test_numbering_survives_a_deleted_attempt(project: Path):
    _lake_project(project)
    for _ in range(2):
        assert run_staged(project, stub_mode="fail")[0].returncode == 1
    a2 = _attempts(project) / "001"
    assert a2.is_dir() and not (_attempts(project) / "002").exists()
    _chmod_tree(a2)                                           # pruning by hand, as documented
    shutil.rmtree(a2)
    assert run_staged(project, stub_mode="fail")[0].returncode == 1
    assert sorted(p.name for p in _attempts(project).iterdir() if p.is_dir()) == ["002"]
    (_attempts(project) / "NEXT").unlink()                    # without the counter: the highest + 1
    assert run_staged(project, stub_mode="fail")[0].returncode == 1
    assert sorted(p.name for p in _attempts(project).iterdir() if p.is_dir()) == ["002", "003"]


def test_attempts_listing_is_read_only_and_deterministic(project: Path):
    _lake_project(project)
    r = _list(project)
    assert r.returncode == 0 and "attempts: 0 kept" in r.stdout
    assert run_staged(project, fail="build")[0].returncode == 1
    assert run_staged(project, stub_mode="fail")[0].returncode == 1
    assert run_staged(project)[0].returncode == 0
    tree = sorted((p.relative_to(project).as_posix(), p.stat().st_mtime_ns) for p in project.rglob("*"))
    one, two = _list(project, "--json"), _list(project, "--json")
    assert one.returncode == 0 and one.stdout == two.stdout
    data = json.loads(one.stdout)
    assert [a["name"] for a in data["attempts"]] == ["001", "002"] and data["next"] == 3
    assert [a["verdict"] for a in data["attempts"]] == ["failed", "failed"]
    assert all(a["intact"] and a["problem"] is None for a in data["attempts"])
    assert str(project) not in one.stdout
    for where in (project / ".lake" / "comparator-receipt", project / STAGES, _attempts(project)):
        assert json.loads(_list(project, str(where), "--json").stdout)["attempts"] == data["attempts"]
    text = _list(project)
    assert text.returncode == 0 and "attempts: 2 kept" in text.stdout
    assert "001  failed (build)" in text.stdout and "002  failed (comparator)" in text.stdout
    assert sorted((p.relative_to(project).as_posix(), p.stat().st_mtime_ns)
                  for p in project.rglob("*")) == tree           # wrote nothing
    log = _attempts(project) / "002" / "04-comparator.log"
    os.chmod(log.parent, 0o755)
    os.chmod(log, 0o644)
    log.write_text("rewritten\n")
    r = _list(project)
    assert r.returncode == 1 and "NOT INTACT: 04-comparator.log changed" in r.stdout
    assert _list(project, "--prepare").returncode == 2


def test_the_receipt_binds_the_current_run_only(project: Path):
    _lake_project(project)
    assert run_staged(project, stub_mode="fail")[0].returncode == 1
    r, _ = run_staged(project)
    assert r.returncode == 0, r.stderr
    rec = _receipt(project)
    assert [s["stage"] for s in rec["stages"]] == ["checkout", "cache-get", "build", "comparator"]
    assert [s["status"] for s in rec["stages"]] == ["prepared"] * 3 + ["ok"]   # not the failed attempt's
    assert "attempts" not in json.dumps(rec)
    assert not list(_attempts(project).rglob("*-comparator.json"))


def test_attempts_are_evidence_files_like_the_stage_logs(tmp_path: Path):
    from lean_pkg import publish
    repo = tmp_path / "repo"
    d = repo / ".lean-receipts" / "comparator-stages"
    (d / "attempts" / "001").mkdir(parents=True)
    (d / "stages.json").write_text("{}\n")
    (d / "04-comparator.log").write_text("now\n")
    (d / "attempts" / "001" / "04-comparator.log").write_text("then\n")
    (d / "attempts" / "001" / "attempt.json").write_text("{}\n")
    git(repo.parent, "init", str(repo))
    paths = publish.evidence_paths({"gate": {}}, repo)
    assert ".lean-receipts/comparator-stages/04-comparator.log" in paths
    assert ".lean-receipts/comparator-stages/attempts/001/04-comparator.log" in paths
    assert ".lean-receipts/comparator-stages/attempts/001/attempt.json" in paths
