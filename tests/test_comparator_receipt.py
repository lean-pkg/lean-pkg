#!/usr/bin/env python3
"""comparator-receipt: runs comparator-check.sh and writes a receipt only on PASS."""

from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
from pathlib import Path

import pytest

from .conftest import RECEIPT_CLI, ROOT, subprocess_env

STUB = ROOT / "tests" / "fixtures" / "comparator-check-stub.sh"


def load_module():
    return importlib.import_module("lean_pkg.comparator_receipt")


@pytest.fixture()
def cr():
    return load_module()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
         "-c", "user.name=Tests", *args],
        check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    git(p, "init")
    (p / "lean-toolchain").write_text("leanprover/lean4:v4.32.0\n")
    (p / "comparator.json").write_text(json.dumps({
        "challenge_module": "Challenge", "solution_module": "Solution",
        "theorem_names": ["foo"], "definition_names": [],
        "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
        "enable_nanoda": True}, indent=2) + "\n")
    git(p, "add", "-A")
    git(p, "commit", "-m", "init")
    return p


def run_runner(project: Path, mode: str = "pass", allow: bool = True,
               extra_env: dict | None = None):
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "STUB_MODE": mode})
    env.pop("SLURM_JOB_ID", None)
    env.pop("COMPARATOR_RECEIPT_ALLOWED_HOSTS", None)
    if allow:
        env["COMPARATOR_RECEIPT_ALLOW_HOST"] = "1"
    else:
        env.pop("COMPARATOR_RECEIPT_ALLOW_HOST", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(RECEIPT_CLI, cwd=project, env=env,
                          capture_output=True, text=True)


# --- pure functions -------------------------------------------------------

def test_host_policy_refuses_unlisted_host_outside_slurm(cr):
    ok, why = cr.host_policy_ok(None, False, hostname="desktop01",
                                allowed_hosts={"buildbox"})
    assert ok is False
    # the refusal names all three ways to be allowed
    for needle in ("Slurm", "SLURM_JOB_ID", "COMPARATOR_RECEIPT_ALLOWED_HOSTS",
                   "COMPARATOR_RECEIPT_ALLOW_HOST=1"):
        assert needle in why, needle


def test_host_policy_refuses_with_no_allowlist(cr):
    ok, why = cr.host_policy_ok(None, False, hostname="login01")
    assert ok is False and "login01" in why


def test_host_policy_allows_slurm_job_on_any_host(cr):
    ok, why = cr.host_policy_ok("123456", False, hostname="node055")
    assert ok is True and "123456" in why


def test_host_policy_allows_listed_host(cr):
    ok, why = cr.host_policy_ok(None, False, hostname="buildbox",
                                allowed_hosts={"buildbox", "other"})
    assert ok is True and "buildbox" in why
    assert "COMPARATOR_RECEIPT_ALLOWED_HOSTS" in why


def test_host_policy_explicit_allow(cr):
    ok, why = cr.host_policy_ok(None, True, hostname="desktop01")
    assert ok is True and why == "allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"


def test_parse_allowed_hosts(cr):
    assert cr.parse_allowed_hosts(" a, b ,,c ") == {"a", "b", "c"}
    assert cr.parse_allowed_hosts("") == set()
    assert cr.parse_allowed_hosts(None) == set()


def test_default_check_script_is_packaged(cr):
    script = cr.default_check_script()
    assert script.is_file()
    assert "Your solution is okay!" in script.read_text()


def test_cfg_name_matches_shell_rule(cr):
    assert cr.cfg_name("comparator.json") == "comparator"
    assert cr.cfg_name("comparator/config-a.json") == "comparator_config-a"


def test_parse_pins(cr):
    text = ("== comparator @ " + "5" * 40 + "\n== lean4export @ " + "6" * 40
            + " (release v4.32.0)\n== nanoda_lib @ " + "7" * 40
            + "\n== landrun @ " + "8" * 40)
    assert cr.parse_pins(text) == {"comparator": "5" * 40, "lean4export": "6" * 40,
                                   "nanoda": "7" * 40, "landrun": "8" * 40}


def test_parse_pins_missing_raises(cr):
    with pytest.raises(cr.RunnerError):
        cr.parse_pins("== comparator @ " + "5" * 40)


# --- end to end with the stub ---------------------------------------------

def test_runner_pass_writes_receipt_and_logs(project: Path):
    r = run_runner(project)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec_path = project / ".lean-receipts" / f"{head[:12]}-comparator.json"
    assert rec_path.is_file()
    rec = json.loads(rec_path.read_text())
    assert rec["schema"] == "comparator-receipt/1"
    assert rec["commit"] == head
    assert rec["lean_toolchain"] == "leanprover/lean4:v4.32.0"
    assert rec["tools"] == {"comparator": "5" * 40, "lean4export": "6" * 40,
                            "nanoda": "7" * 40, "landrun": "8" * 40}
    assert rec["result"] == "pass" and len(rec["configs"]) == 1
    cfg = rec["configs"][0]
    assert cfg["path"] == "comparator.json"
    assert cfg["sha256"] == hashlib.sha256(
        (project / "comparator.json").read_bytes()).hexdigest()
    assert cfg["theorem_names"] == ["foo"]
    log = project / cfg["log"]
    assert cfg["log"] == f".lean-receipts/{head[:12]}-comparator-comparator.log"
    assert log.is_file()
    assert cfg["log_sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
    assert "Your solution is okay!" in log.read_text().splitlines()
    assert rec["job_id"].startswith("local-")
    assert "lean-pkg comparator" in r.stdout


def test_runner_fail_writes_nothing(project: Path):
    r = run_runner(project, mode="fail")
    assert r.returncode == 1
    assert not (project / ".lean-receipts").exists()
    assert "nothing written" in r.stderr


def test_runner_missing_log_writes_nothing(project: Path):
    r = run_runner(project, mode="nolog")
    assert r.returncode == 1
    assert not (project / ".lean-receipts").exists()


def test_runner_refuses_dirty_tree_names_files(project: Path):
    (project / "scratch.lean").write_text("-- untracked\n")
    r = run_runner(project)
    assert r.returncode == 1
    assert "scratch.lean" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_runner_refuses_without_comparator_config(project: Path):
    (project / "comparator.json").unlink()
    git(project, "add", "-A")
    git(project, "commit", "-m", "drop config")
    r = run_runner(project)
    assert r.returncode == 1
    assert "no comparator configuration" in r.stderr


def test_runner_host_policy_refusal_message(project: Path):
    r = run_runner(project, allow=False)
    assert r.returncode == 1 and "refusing" in r.stderr
    assert "COMPARATOR_RECEIPT_ALLOWED_HOSTS" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_runner_allows_listed_host_and_records_reason(project: Path):
    import socket
    host = socket.gethostname()
    r = run_runner(project, allow=False,
                   extra_env={"COMPARATOR_RECEIPT_ALLOWED_HOSTS": f"other,{host}"})
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["host_policy"] == f"host {host} listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS"


def test_runner_allows_slurm_job(project: Path):
    r = run_runner(project, allow=False, extra_env={"SLURM_JOB_ID": "987654"})
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["job_id"] == "987654"
    assert rec["host_policy"].startswith("Slurm job 987654")


# --- review fix pass: no artifacts on a late write failure; policy visibility

def test_runner_late_write_failure_leaves_nothing(project: Path):
    head = git(project, "rev-parse", "HEAD")
    blocker = project / ".lean-receipts" / f"{head[:12]}-comparator.json"
    blocker.mkdir(parents=True)          # empty dir at the receipt path: git-invisible,
                                         # tree still clean, final rename must fail
    r = run_runner(project)
    assert r.returncode == 1
    assert "nothing written" in r.stderr and "Traceback" not in r.stderr
    leftovers = [p for p in (project / ".lean-receipts").iterdir() if p != blocker]
    assert leftovers == []


def test_runner_records_host_policy_reason(project: Path):
    r = run_runner(project)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["host_policy"] == "allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"
