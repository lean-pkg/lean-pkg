#!/usr/bin/env python3
"""comparator-receipt: runs comparator-check.sh and writes a receipt only on PASS."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
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
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "STUB_MODE": mode,
                          "COMPARATOR_FAKE_LAKE_OK": "0"})   # hermetic: never probe a real toolchain
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
    assert rec["schema"] == "comparator-receipt/2" and rec["mechanism"] == "legacy"
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


# --- mechanisms (ADR-0058 D7): toolchain (lake comparator) vs legacy (pinned stack) ---

STUB_TC = ROOT / "tests" / "fixtures" / "verify-comparator-stub.sh"


def run_runner_mech(project: Path, mechanism: str, stub_mode: str = "pass", lake_ok: bool = True):
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "COMPARATOR_VERIFY": str(STUB_TC),
                          "STUB_MODE": stub_mode, "COMPARATOR_RECEIPT_ALLOW_HOST": "1",
                          "COMPARATOR_FAKE_LAKE_OK": "1" if lake_ok else "0"})
    env.pop("SLURM_JOB_ID", None)
    env.pop("COMPARATOR_RECEIPT_ALLOWED_HOSTS", None)
    return subprocess.run([*RECEIPT_CLI, "--mechanism", mechanism], cwd=project, env=env,
                          capture_output=True, text=True)


def test_toolchain_mechanism_writes_schema2_receipt(project: Path):
    r = run_runner_mech(project, "toolchain")
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["schema"] == "comparator-receipt/2" and rec["mechanism"] == "toolchain"
    for k in ("lean_toolchain", "nanoda_bin_sha256", "con-ron_sha256", "lake_sha256"):
        assert k in rec["tools"], k
    assert all(len(v) == 64 for k, v in rec["tools"].items() if k.endswith("_sha256"))


def test_legacy_mechanism_writes_schema2_with_pins(project: Path):
    r = run_runner_mech(project, "legacy")
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["schema"] == "comparator-receipt/2" and rec["mechanism"] == "legacy"
    assert set(rec["tools"]) == {"comparator", "lean4export", "nanoda", "landrun"}


def test_toolchain_mechanism_refuses_old_toolchain(project: Path):
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.32.2\n")
    git(project, "commit", "-am", "old toolchain")
    r = run_runner_mech(project, "toolchain", lake_ok=False)
    assert r.returncode == 1
    assert "v4.35.0-rc2" in r.stderr and "lake comparator" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_auto_picks_toolchain_when_available(project: Path):
    r = run_runner_mech(project, "auto", lake_ok=True)
    assert r.returncode == 0, r.stderr
    assert "mechanism: toolchain" in r.stdout


def test_auto_falls_back_to_legacy(project: Path):
    r = run_runner_mech(project, "auto", lake_ok=False)
    assert r.returncode == 0, r.stderr
    assert "mechanism: legacy" in r.stdout


def test_toolchain_mechanism_writes_nothing_on_reject(project: Path):
    r = run_runner_mech(project, "toolchain", stub_mode="fail")
    assert r.returncode == 1
    assert not (project / ".lean-receipts").exists()


# --- bwrap preflight: Ubuntu 24.04 forbids unprivileged user namespaces by default ---

def _fake_toolchain_host(tmp_path: Path, bwrap_ok: bool) -> dict:
    """A PATH with a fake `lean` (prints a prefix whose bin/ holds the five judges) and a
    fake `bwrap` that either works or fails the way AppArmor makes it fail."""
    prefix = tmp_path / "toolchain"
    (prefix / "bin").mkdir(parents=True)
    for tool in ("lake", "leanexport", "leanchecker", "nanoda_bin", "con-ron"):
        (prefix / "bin" / tool).write_text("#!/bin/sh\nexit 0\n")
        (prefix / "bin" / tool).chmod(0o755)
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "lean").write_text(f"#!/bin/sh\n[ \"$1\" = --print-prefix ] && echo {prefix}\nexit 0\n")
    body = "#!/bin/sh\nexit 0\n" if bwrap_ok else \
        "#!/bin/sh\necho 'bwrap: setting up uid map: Permission denied' >&2\nexit 1\n"
    (fakes / "bwrap").write_text(body)
    # #41: no elan on this fake host, so PATH's lake is used and must name the project's Lean
    (fakes / "lake").write_text("#!/bin/sh\necho 'Lake version 5.0.0 (Lean version 4.32.0)'\n")
    for f in ("lean", "bwrap", "lake"):
        (fakes / f).chmod(0o755)
    env = subprocess_env({"COMPARATOR_VERIFY": str(STUB_TC), "STUB_MODE": "pass",
                          "COMPARATOR_RECEIPT_ALLOW_HOST": "1"})
    env.pop("COMPARATOR_FAKE_LAKE_OK", None)
    env.pop("SLURM_JOB_ID", None)
    env["PATH"] = f"{fakes}:{env.get('PATH', '')}"
    env["ELAN_HOME"] = str(tmp_path / "no-elan")
    return env


def test_toolchain_mechanism_refuses_when_bwrap_cannot_unshare(project: Path, tmp_path: Path):
    env = _fake_toolchain_host(tmp_path, bwrap_ok=False)
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "toolchain"], cwd=project, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "bwrap" in r.stderr and "user namespace" in r.stderr and "apparmor" in r.stderr.lower()
    assert not (project / ".lean-receipts").exists()


def test_toolchain_mechanism_runs_when_bwrap_works(project: Path, tmp_path: Path):
    env = _fake_toolchain_host(tmp_path, bwrap_ok=True)
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "toolchain"], cwd=project, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["mechanism"] == "toolchain" and len(rec["tools"]["lake_sha256"]) == 64


def test_toolchain_probe_requires_executable_binaries(cr, tmp_path: Path, monkeypatch):
    prefix = tmp_path / "tc"
    (prefix / "bin").mkdir(parents=True)
    for tool in cr.TOOLCHAIN_TOOLS:
        (prefix / "bin" / tool).write_text("#!/bin/sh\nexit 0\n")   # present but NOT executable
    fakes = tmp_path / "fakes"
    fakes.mkdir()
    (fakes / "lean").write_text(f"#!/bin/sh\n[ \"$1\" = --print-prefix ] && echo {prefix}\nexit 0\n")
    (fakes / "lean").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakes}:{os.environ.get('PATH', '')}")
    monkeypatch.delenv("COMPARATOR_FAKE_LAKE_OK", raising=False)
    assert cr.toolchain_available(tmp_path) is False
    for tool in cr.TOOLCHAIN_TOOLS:
        (prefix / "bin" / tool).chmod(0o755)
    assert cr.toolchain_available(tmp_path) is True


def test_fake_digest_override_marks_receipt_test_only(project: Path):
    r = run_runner_mech(project, "toolchain")
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["test_only"] is True


def test_runner_host_label_env_replaces_the_hostname(project: Path):
    r = run_runner(project, extra_env={"COMPARATOR_RECEIPT_HOST_LABEL": "lab-workstation"})
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["host"] == "lab-workstation"


def test_host_label_also_covers_the_host_policy_reason(project: Path):
    """A public-safe label must replace the host name everywhere in the receipt, including the
    host_policy sentence (a copied receipt leaked the real host name through it on 2026-10-06)."""
    import socket
    real = socket.gethostname()
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "STUB_MODE": "pass",
                          "COMPARATOR_FAKE_LAKE_OK": "0",
                          "COMPARATOR_RECEIPT_ALLOWED_HOSTS": real,
                          "COMPARATOR_RECEIPT_HOST_LABEL": "lab-workstation"})
    env.pop("SLURM_JOB_ID", None)
    env.pop("COMPARATOR_RECEIPT_ALLOW_HOST", None)
    r = subprocess.run(RECEIPT_CLI, cwd=project, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    text = (project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text()
    assert real not in text and "lab-workstation" in text
    policy = next(l for l in r.stdout.splitlines() if "host policy:" in l).split("host policy:", 1)[1]
    assert real not in policy and "lab-workstation" in policy


# --- definition holes: refused before the check script runs ---------------------

def _write_cfg(project: Path, cfg: dict) -> None:
    (project / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    git(project, "add", "comparator.json")
    git(project, "commit", "-m", "cfg")


def _base_cfg(**over) -> dict:
    cfg = {"challenge_module": "Challenge", "solution_module": "Solution",
           "theorem_names": ["foo"], "definition_names": [],
           "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
           "enable_nanoda": True}
    cfg.update(over)
    return cfg


def test_runner_refuses_holes_without_sign_off_and_writes_nothing(project: Path):
    _write_cfg(project, _base_cfg(definition_names=["Foo.bar"]))
    r = run_runner(project)
    assert r.returncode == 1
    assert "definition_names is non-empty" in r.stderr
    assert "nothing written" in r.stderr
    assert "== comparator @" not in r.stdout        # the stub never ran
    assert not (project / ".lean-receipts").exists()


def test_runner_refuses_agent_reviewer(project: Path):
    _write_cfg(project, _base_cfg(definition_names=["Foo.bar"], judged_as={
        "reviewer": "opencode-worker", "date": "2026-10-07", "definitions": ["Foo.bar"]}))
    r = run_runner(project)
    assert r.returncode == 1
    assert "agent/model token" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_runner_refuses_nonstandard_axiom_before_running(project: Path):
    _write_cfg(project, _base_cfg(permitted_axioms=["propext", "Lean.ofReduceBool"]))
    r = run_runner(project)
    assert r.returncode == 1
    assert "permitted_axioms" in r.stderr and "nothing written" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_runner_records_judged_as_with_human_sign_off(project: Path):
    signoff = {"reviewer": "Le Chen", "date": "2026-10-07",
               "definitions": ["Foo.bar"], "basis": "compared bodies"}
    _write_cfg(project, _base_cfg(definition_names=["Foo.bar"], judged_as=signoff))
    r = run_runner(project)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    cfg = rec["configs"][0]
    assert cfg["definition_names"] == ["Foo.bar"]
    assert cfg["judged_as"] == signoff


def test_runner_omits_judged_as_when_absent(project: Path):
    r = run_runner(project)
    assert r.returncode == 0, r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert "judged_as" not in rec["configs"][0]


# --- stage logs and resumable setup (#26.1) ---------------------------------------

LAKE_STUB = ROOT / "tests" / "fixtures" / "lake-stub.sh"


def _lake_project(project: Path, mathlib: bool = True) -> Path:
    (project / "lakefile.toml").write_text('name = "proj"\n')
    pkgs = [{"name": "mathlib", "rev": "0" * 40}] if mathlib else []
    (project / "lake-manifest.json").write_text(json.dumps({"version": 7, "packages": pkgs}) + "\n")
    git(project, "add", "-A")
    git(project, "commit", "-m", "lake project")
    return project


def run_staged(project: Path, *flags: str, fail: str = "", stub_mode: str = "pass",
               extra_env: dict | None = None):
    calls = project.parent / "lake-calls.txt"
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "COMPARATOR_VERIFY": str(STUB_TC),
                          "STUB_MODE": stub_mode, "COMPARATOR_RECEIPT_ALLOW_HOST": "1",
                          "COMPARATOR_FAKE_LAKE_OK": "1", "COMPARATOR_LAKE": str(LAKE_STUB),
                          "LAKE_STUB_CALLS": str(calls), "LAKE_STUB_FAIL": fail, **(extra_env or {})})
    env.pop("SLURM_JOB_ID", None)
    env.pop("COMPARATOR_RECEIPT_ALLOWED_HOSTS", None)
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "toolchain", *flags], cwd=project, env=env,
                       capture_output=True, text=True)
    lines = calls.read_text().splitlines() if calls.exists() else []
    return r, lines


def _stages(project: Path) -> dict:
    return json.loads((project / ".lake" / "comparator-receipt" / "comparator-stages"
                       / "stages.json").read_text())


def test_stages_logged_for_every_stage_and_summarised_in_receipt(project: Path):
    _lake_project(project)
    r, calls = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert calls == ["lake env true", "lake exe cache get", "lake build Challenge Solution", "lake env printenv LEAN_PATH"]
    st = _stages(project)
    assert [e["stage"] for e in st["stages"]] == ["checkout", "cache-get", "build", "comparator", "receipt"]
    assert all(e["status"] == "ok" and e["exit"] == 0 for e in st["stages"])
    assert st["failed_stage"] is None and st["setup_complete"] is True
    assert st["git_env_scrubbed"] == []                     # #27: nothing leaked, nothing scrubbed
    d = project / ".lake" / "comparator-receipt" / "comparator-stages"
    for n, s in enumerate(["checkout", "cache-get", "build", "comparator", "receipt"], 1):
        assert (d / f"{n:02d}-{s}.log").is_file()
    assert "Your solution is okay!" in (d / "04-comparator.log").read_text()
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert [s["stage"] for s in rec["stages"]] == ["checkout", "cache-get", "build", "comparator"]
    assert all(set(s) == {"stage", "status", "exit", "wall_s"} for s in rec["stages"])   # no paths


def test_stage_run_scrubs_a_leaked_git_index_file(project: Path):
    """#27: a launcher's GIT_INDEX_FILE must not reach lake's dependency checkout (or the runner's git)."""
    _lake_project(project)
    bogus = str(project.parent / "no-such-dir" / "index")
    r, calls = run_staged(project, extra_env={"GIT_INDEX_FILE": bogus, "GIT_NAMESPACE": "elsewhere"})
    assert r.returncode == 0, r.stderr
    assert calls == ["lake env true", "lake exe cache get", "lake build Challenge Solution", "lake env printenv LEAN_PATH"]   # no `leaked`
    st = _stages(project)
    assert st["git_env_scrubbed"] == ["GIT_INDEX_FILE", "GIT_NAMESPACE"]
    assert all(e["status"] == "ok" for e in st["stages"])
    head = git(project, "rev-parse", "HEAD")
    assert (project / ".lean-receipts" / f"{head[:12]}-comparator.json").is_file()


def test_failed_stage_keeps_its_log_and_is_named(project: Path):
    _lake_project(project)
    r, calls = run_staged(project, fail="env")
    assert r.returncode == 1
    assert calls == ["lake env true"]
    log = project / ".lake" / "comparator-receipt" / "comparator-stages" / "01-checkout.log"
    assert "stage 01-checkout failed" in r.stderr and str(log) in r.stderr
    assert "could not clone mathlib" in log.read_text()
    st = _stages(project)
    assert st["failed_stage"] == "checkout" and st["stages"][0]["exit"] == 3
    assert not (project / ".lean-receipts").exists()


def test_prepare_runs_only_setup_then_the_run_skips_it(project: Path):
    _lake_project(project)
    r, calls = run_staged(project, "--prepare")
    assert r.returncode == 0, r.stderr
    assert len(calls) == 4 and "== lake comparator" not in r.stdout
    assert not (project / ".lean-receipts").exists()
    assert _stages(project)["prepare"] is True and _stages(project)["setup_complete"] is True
    r, calls = run_staged(project, "--prepare")               # idempotent: nothing reruns
    assert r.returncode == 0 and "already prepared" in r.stdout and len(calls) == 4
    r, calls = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert len(calls) == 4 and "setup reused" in r.stderr     # straight to the comparison
    st = _stages(project)
    assert [e["status"] for e in st["stages"]] == ["prepared"] * 3 + ["ok", "ok"]


def test_comparison_failure_after_prepare_is_retryable_without_setup(project: Path):
    _lake_project(project)
    run_staged(project, "--prepare")
    r, calls = run_staged(project, stub_mode="fail")
    assert r.returncode == 1 and "stage 04-comparator failed" in r.stderr
    assert _stages(project)["failed_stage"] == "comparator"
    r, calls = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert len(calls) == 4


def test_prepared_state_is_bound_to_the_config_hash(project: Path):
    _lake_project(project)
    run_staged(project, "--prepare")
    cfg = json.loads((project / "comparator.json").read_text())
    cfg["theorem_names"] = ["foo", "bar"]
    (project / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    git(project, "commit", "-am", "more names")
    r, calls = run_staged(project)
    assert r.returncode == 0, r.stderr
    assert "no reusable prepared state" in r.stderr and len(calls) == 8


def test_from_prepared_refuses_a_mismatched_state(project: Path, tmp_path: Path):
    _lake_project(project)
    other = tmp_path / "elsewhere"
    r, _ = run_staged(project, "--prepare", "--evidence-dir", str(other))
    assert r.returncode == 0, r.stderr
    git(project, "commit", "--allow-empty", "-m", "new commit")
    r, _ = run_staged(project, "--from-prepared", str(other))
    assert r.returncode == 1 and "--from-prepared" in r.stderr and "commit" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_from_prepared_reuses_a_matching_state_elsewhere(project: Path, tmp_path: Path):
    _lake_project(project)
    other = tmp_path / "elsewhere"
    run_staged(project, "--prepare", "--evidence-dir", str(other))
    r, calls = run_staged(project, "--from-prepared", str(other))
    assert r.returncode == 0, r.stderr
    assert len(calls) == 4


def test_cache_get_skipped_without_mathlib(project: Path):
    _lake_project(project, mathlib=False)
    r, calls = run_staged(project, extra_env={"COMPARATOR_RECEIPT_NO_MATHLIB": "1"})   # #62: declared
    assert r.returncode == 0, r.stderr
    assert calls == ["lake env true", "lake build Challenge Solution", "lake env printenv LEAN_PATH"]
    assert _stages(project)["stages"][1]["status"] == "skipped"


def test_prepare_and_from_prepared_are_exclusive(project: Path):
    r, _ = run_staged(project, "--prepare", "--from-prepared", "x")
    assert r.returncode == 2


def test_prepared_mismatch_names_the_config_hash(cr):
    ok = [{"stage": s, "status": "ok"} for s in cr.SETUP_STAGES]
    state = {"schema": cr.STAGES_SCHEMA, "commit": "a" * 40, "lean_toolchain": "t",
             "config_hash": "1" * 64, "stages": ok}
    binding = {"commit": "a" * 40, "lean_toolchain": "t", "config_hash": "1" * 64}
    assert cr.prepared_mismatch(state, binding) is None
    assert "config_hash" in cr.prepared_mismatch(state, {**binding, "config_hash": "2" * 64})
    assert "toolchain" in cr.prepared_mismatch(state, {**binding, "lean_toolchain": "u"})
    state["stages"][2]["status"] = "failed"
    assert "build" in cr.prepared_mismatch(state, binding)


# --- stack limit (#52) -----------------------------------------------------

MIB = 1024 * 1024


def _fake_rlimit(monkeypatch, cr, soft, hard):
    monkeypatch.setattr(cr, "_STACK_SOFT", [])             # restored afterwards: no plan leaks to other tests
    monkeypatch.setattr(cr.resource, "getrlimit", lambda which: (soft, hard))


def test_stack_warning_when_soft_limit_is_8_mib(cr, monkeypatch):
    _fake_rlimit(monkeypatch, cr, 8 * MIB, cr.resource.RLIM_INFINITY)
    rec, lines = cr.plan_stack(None)
    assert lines == [cr.STACK_WARNING]
    assert "exit 139" in cr.STACK_WARNING and "sharecommon_quick_fn::visit_ctor" in cr.STACK_WARNING
    assert "COMPARATOR_RECEIPT_STACK=unlimited" in cr.STACK_WARNING
    assert rec == {"soft_kb": 8192, "hard_kb": "unlimited", "source": "inherited"}
    assert cr.stack_preexec() is None                       # inherited: no preexec at all


def test_no_stack_warning_above_8_mib(cr, monkeypatch):
    _fake_rlimit(monkeypatch, cr, cr.resource.RLIM_INFINITY, cr.resource.RLIM_INFINITY)
    rec, lines = cr.plan_stack("")
    assert lines == [] and rec["soft_kb"] == "unlimited"


def test_stack_env_sets_the_soft_limit_in_preexec(cr, monkeypatch):
    _fake_rlimit(monkeypatch, cr, 8 * MIB, cr.resource.RLIM_INFINITY)
    calls = []
    monkeypatch.setattr(cr.resource, "setrlimit", lambda which, lim: calls.append((which, lim)))
    rec, lines = cr.plan_stack("unlimited")
    assert rec == {"soft_kb": "unlimited", "hard_kb": "unlimited", "source": "COMPARATOR_RECEIPT_STACK"}
    assert len(lines) == 1 and "set to unlimited" in lines[0] and "was 8192 kB" in lines[0]
    cr.stack_preexec()()
    assert calls == [(cr.resource.RLIMIT_STACK, (cr.resource.RLIM_INFINITY, cr.resource.RLIM_INFINITY))]


def test_stack_env_never_exceeds_the_hard_limit(cr, monkeypatch):
    _fake_rlimit(monkeypatch, cr, 8 * MIB, 64 * MIB)
    calls = []
    monkeypatch.setattr(cr.resource, "setrlimit", lambda which, lim: calls.append(lim))
    rec, lines = cr.plan_stack("unlimited")
    assert rec["soft_kb"] == 65536 and "clipped" in lines[0]
    cr.stack_preexec()()
    assert calls == [(64 * MIB, 64 * MIB)]
    rec, lines = cr.plan_stack("4096")                     # explicit and low: applied, and still warned
    assert rec["soft_kb"] == 4096 and lines[-1] == cr.STACK_WARNING


@pytest.mark.parametrize("bad", ["lots", "-1", "0", "8M", "1.5"])
def test_stack_env_invalid_value_is_a_usage_error(cr, project: Path, bad):
    with pytest.raises(ValueError):
        cr.parse_stack(bad)
    r = run_runner(project, extra_env={"COMPARATOR_RECEIPT_STACK": bad})
    assert r.returncode == 2 and "COMPARATOR_RECEIPT_STACK" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_stack_env_reaches_the_lake_children_and_the_receipt(cr, project: Path, tmp_path: Path):
    import resource
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    want_kb = 12345
    if hard != resource.RLIM_INFINITY and hard < want_kb * 1024:
        pytest.skip("hard stack limit below the test value")
    _lake_project(project)
    seen = tmp_path / "ulimit.txt"
    wrapper = tmp_path / "lake-wrapper.sh"
    wrapper.write_text(f'#!/usr/bin/env bash\nulimit -s >> "{seen}"\nexec "{LAKE_STUB}" "$@"\n')
    wrapper.chmod(0o755)
    r, calls = run_staged(project, extra_env={"COMPARATOR_LAKE": str(wrapper),
                                              "COMPARATOR_RECEIPT_STACK": str(want_kb)})
    assert r.returncode == 0, r.stderr
    assert seen.read_text().split() == [str(want_kb)] * 6   # every setup child (4) and target probe (2)
    assert f"COMPARATOR_RECEIPT_STACK={want_kb}" in r.stderr and f"set to {want_kb} kB" in r.stderr
    head = git(project, "rev-parse", "HEAD")
    rec = json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())
    assert rec["stack_limit"]["soft_kb"] == want_kb and rec["stack_limit"]["source"] == "COMPARATOR_RECEIPT_STACK"


def test_stack_warning_printed_before_the_build_stage(project: Path):
    import resource
    _lake_project(project)
    hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
    if hard != resource.RLIM_INFINITY and hard < 8 * MIB:
        pytest.skip("hard stack limit below 8 MiB")
    calls = project.parent / "lake-calls.txt"
    env = subprocess_env({"COMPARATOR_CHECK": str(STUB), "COMPARATOR_VERIFY": str(STUB_TC),
                          "STUB_MODE": "pass", "COMPARATOR_RECEIPT_ALLOW_HOST": "1",
                          "COMPARATOR_FAKE_LAKE_OK": "1", "COMPARATOR_LAKE": str(LAKE_STUB),
                          "LAKE_STUB_CALLS": str(calls)})
    env.pop("SLURM_JOB_ID", None)
    env.pop("COMPARATOR_RECEIPT_STACK", None)
    r = subprocess.run([*RECEIPT_CLI, "--mechanism", "toolchain", "--prepare"], cwd=project, env=env,
                       capture_output=True, text=True,
                       preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_STACK, (8 * MIB, hard)))
    assert r.returncode == 0, r.stderr
    assert "WARN: soft stack limit is 8 MiB" in r.stderr


# --- build provenance (#56) ------------------------------------------------

def _ignored_lake(project: Path) -> Path:
    """A lake project whose .lake/ is gitignored (as in a real package), so built oleans keep the tree clean."""
    (project / ".gitignore").write_text(".lake/\n")
    return _lake_project(project)


def _receipt(project: Path) -> dict:
    head = git(project, "rev-parse", "HEAD")
    return json.loads((project / ".lean-receipts" / f"{head[:12]}-comparator.json").read_text())


def test_build_provenance_rule(cr):
    built = "✔ [1/2] Built A (0.4s)\n✔ [2/2] Built B\n"
    bp = cr.build_provenance({}, {"lean/A.olean": 1, "lean/B.olean": 2}, built, None)
    assert bp == {"compiled": True, "modules_built": 2, "prebuilt": False, "source": None}
    same = {"lean/A.olean": 1}
    assert cr.build_provenance(same, dict(same), "Build completed successfully.\n", "job 7") == \
        {"compiled": False, "modules_built": 0, "prebuilt": True, "source": "job 7"}
    touched = cr.build_provenance(same, {"lean/A.olean": 5}, "✔ [1/1] Built A\n", None)
    assert touched["compiled"] is True and touched["modules_built"] == 1        # rewritten in place
    lines = "✔ [1/3] Built Challenge (0.4s)\n✔ [2/3] Built Solution\n✔ [3/3] Built Solution\n"
    assert cr.build_provenance({}, {}, lines, None)["modules_built"] == 2       # lake's lines, no olean tree
    assert cr.build_provenance(None, None, "", None) == \
        {"compiled": False, "modules_built": None, "prebuilt": False, "source": None}


def test_build_provenance_counts_only_built_lines_as_compiled(cr):
    """Lake's artifact cache writes oleans without compiling them (Reused / Replayed / Unpacked): an olean
    written with no `Built` line for its module is `restored`, never compiled (lake_hygiene)."""
    bp = cr.build_provenance({}, {"lean/A.olean": 1, "lean/B/C.olean": 2}, "", None)
    assert bp == {"compiled": False, "modules_built": 0, "prebuilt": True, "source": None, "restored": 2}
    same = {"lean/A.olean": 1}
    touched = cr.build_provenance(same, {"lean/A.olean": 5}, "", None)
    assert touched["compiled"] is False and touched["restored"] == 1
    log = "ℹ [1/3] Reused A\n⚠ [2/3] Replayed B.C\n✔ [3/3] Unpacked D\n"
    bp = cr.build_provenance({}, {"lean/A.olean": 1, "lean/B/C.olean": 2}, log, None)
    assert bp == {"compiled": False, "modules_built": 0, "prebuilt": True, "source": None, "cache_reused": 3,
                  "restored": 2}
    mixed = cr.build_provenance({}, {"lean/A.olean": 1, "lean/B/C.olean": 2}, "✔ [1/2] Built B.C\n" + log, None)
    assert mixed["compiled"] is True and mixed["modules_built"] == 1
    assert mixed["restored"] == 1 and mixed["cache_reused"] == 2
    assert cr.build_provenance_line(mixed) == ("build: compiled 1 module(s); 1 olean(s) restored, not compiled, "
                                               "2 module(s) Lake reports reused from a cache")
    # a Replayed after the module's own Built is a warning replay (a later `lake lean`), not a cache restore;
    # one before it still counts
    after = cr.build_provenance({}, {"lean/A.olean": 1}, "✔ [1/2] Built A\n⚠ [2/2] Replayed A\n", None)
    assert after["compiled"] is True and "cache_reused" not in after and "restored" not in after
    before = cr.build_provenance({}, {"lean/A.olean": 1}, "⚠ [1/2] Replayed A\n✔ [2/2] Built A\n", None)
    assert before["cache_reused"] == 1


def test_a_shadowing_dependency_fails_the_build_stage(project: Path, tmp_path: Path):
    """A LEAN_PATH entry before the project's own lib directory that holds its root namespace refuses the run
    after the build stage: Lean would load the dependency's copy under `lake env` (lake_hygiene)."""
    _lake_project(project)
    dep = tmp_path / "dep-lib"
    (dep / "Challenge").mkdir(parents=True)
    r, calls = run_staged(project, extra_env={
        "LAKE_STUB_LEAN_PATH": f"{dep}:{project}/.lake/build/lib/lean"})
    assert r.returncode == 1
    assert "LEAN_PATH shadows the package's own modules" in r.stderr and "dep-lib/Challenge" in r.stderr
    assert calls[-1] == "lake env printenv LEAN_PATH"
    assert _stages(project)["failed_stage"] == "build"
    assert not list(project.glob(".lean-receipts/*-comparator.json"))


def test_build_provenance_compiled_is_recorded(project: Path):
    _ignored_lake(project)
    r, _ = run_staged(project, extra_env={"LAKE_STUB_OLEANS": "Challenge Solution"})
    assert r.returncode == 0, r.stderr
    assert "comparator-receipt: build: compiled 2 module(s)" in r.stderr
    want = {"compiled": True, "modules_built": 2, "prebuilt": False, "source": None}
    assert _receipt(project)["build_provenance"] == want
    assert _stages(project)["build_provenance"] == want


def test_build_provenance_prebuilt_records_the_redacted_source(project: Path):
    _ignored_lake(project)
    for m in ("Challenge", "Solution"):                     # oleans built elsewhere, copied in
        f = project / ".lake" / "build" / "lib" / "lean" / f"{m}.olean"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"olean {m}\n")
    src = f"cluster job 4711 ({project}/.lake)"
    r, _ = run_staged(project, extra_env={"COMPARATOR_RECEIPT_BUILD_SOURCE": src})
    assert r.returncode == 0, r.stderr
    bp = _receipt(project)["build_provenance"]
    assert bp == {"compiled": False, "modules_built": 0, "prebuilt": True,
                  "source": "cluster job 4711 (<checkout>/.lake)"}
    assert str(project) not in json.dumps(_stages(project)["build_provenance"])
    assert "build: prebuilt (source: cluster job 4711 (<checkout>/.lake))" in r.stderr


def test_build_provenance_is_carried_through_from_prepared(project: Path, tmp_path: Path):
    _ignored_lake(project)
    other = tmp_path / "elsewhere"
    r, _ = run_staged(project, "--prepare", "--evidence-dir", str(other),
                      extra_env={"LAKE_STUB_OLEANS": "Challenge Solution",
                                 "COMPARATOR_RECEIPT_BUILD_SOURCE": "prepare job 1"})
    assert r.returncode == 0, r.stderr
    r, calls = run_staged(project, "--from-prepared", str(other))
    assert r.returncode == 0, r.stderr
    assert len(calls) == 4                                  # no new build
    assert _receipt(project)["build_provenance"] == \
        {"compiled": True, "modules_built": 2, "prebuilt": False, "source": "prepare job 1"}
    assert "build: compiled 2 module(s) (source: prepare job 1)" in r.stderr


def test_build_provenance_of_an_older_prepared_state_is_unknown(cr):
    assert cr.carried_provenance({"stages": []}) == \
        {"compiled": None, "modules_built": None, "prebuilt": None, "source": None}
    from lean_pkg.cli import build_provenance_line
    assert "not recorded" in build_provenance_line(cr.carried_provenance(None))
    assert build_provenance_line({"compiled": False, "modules_built": 0, "prebuilt": True,
                                  "source": None}) == "build: prebuilt (source: not declared)"


# --- lean threads (#55) ----------------------------------------------------

@pytest.mark.parametrize("bad", ["0", "-2", "two", "1.5", "4 cores"])
def test_lean_threads_invalid_value_is_a_usage_error(cr, project: Path, bad):
    with pytest.raises(ValueError):
        cr.parse_lean_threads(bad)
    r = run_runner(project, extra_env={"COMPARATOR_RECEIPT_LEAN_THREADS": bad})
    assert r.returncode == 2 and "COMPARATOR_RECEIPT_LEAN_THREADS must be a positive integer" in r.stderr
    assert not (project / ".lean-receipts").exists()


def test_lean_threads_reach_the_lake_children_the_judges_and_the_receipt(project: Path, tmp_path: Path):
    _lake_project(project)
    seen = tmp_path / "env.txt"
    judge_seen = tmp_path / "judge-env.txt"
    judge = tmp_path / "verify-wrapper.sh"
    judge.write_text(f'#!/usr/bin/env bash\necho "LEAN_NUM_THREADS=${{LEAN_NUM_THREADS-unset}}" >> "{judge_seen}"\n'
                     f'exec "{STUB_TC}" "$@"\n')
    judge.chmod(0o755)
    r, _ = run_staged(project, extra_env={"LAKE_STUB_ENV": str(seen), "COMPARATOR_VERIFY": str(judge),
                                          "LEAN_NUM_THREADS": "16", "COMPARATOR_RECEIPT_LEAN_THREADS": "1"})
    assert r.returncode == 0, r.stderr
    assert seen.read_text().split() == ["LEAN_NUM_THREADS=1"] * 4     # every setup child
    assert judge_seen.read_text().split() == ["LEAN_NUM_THREADS=1"]   # the comparator stage's judge
    assert "COMPARATOR_RECEIPT_LEAN_THREADS=1: LEAN_NUM_THREADS=1 for the lake/lean children" in r.stderr
    assert _receipt(project)["lean_threads"] == 1


def test_lean_threads_unset_leaves_the_environment_alone(project: Path, tmp_path: Path):
    _lake_project(project)
    seen = tmp_path / "env.txt"
    r, _ = run_staged(project, extra_env={"LAKE_STUB_ENV": str(seen), "LEAN_NUM_THREADS": "16"})
    assert r.returncode == 0, r.stderr
    assert seen.read_text().split() == ["LEAN_NUM_THREADS=16"] * 4     # inherited, untouched
    assert _receipt(project)["lean_threads"] is None and "lean threads:" not in r.stderr
