#!/usr/bin/env python3
"""`receipt systemd` (alias `receipt local`): a finished, capped systemd --user service unit (its captured
properties, raw verify log, stdout seal, stderr) becomes a lower-tier receipt that `gate --check` accepts; every
refusal writes nothing into .lean-receipts. No real systemd is used: the properties come from a file, or from a
fake `systemctl` / `systemd-run` on PATH. No registry is read."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from .conftest import MATHLIB_MANIFEST, MATHLIB_REV, TOOLCHAIN, _git, make_repo, repo_head

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "slurm"
LOG = (FIXTURES / "verify.log").read_text()
CHECKER = "gate.sh (lake build; #print axioms)"
UNIT = "lp-verify.service"
INV = "8adde655177b48ad86a75a9892431175"
HOST = "node7"
STD_AXIOMS = ["propext", "Classical.choice", "Quot.sound"]
PROPS = {
    "Id": UNIT, "LoadState": "loaded", "ActiveState": "active", "SubState": "exited", "Result": "success",
    "InvocationID": INV, "ExecMainCode": "1", "ExecMainStatus": "0",
    "ExecMainStartTimestamp": "Tue 2026-10-13 18:00:00 UTC", "ExecMainExitTimestamp": "Tue 2026-10-13 18:12:05 UTC",
    "ExecMainStartTimestampMonotonic": "1000000000", "ExecMainExitTimestampMonotonic": "1725000000",
    "NRestarts": "0", "MemoryPeak": str(3 << 29), "CPUUsageNSec": "4222000000000", "MemoryMax": str(8 << 30),
    "MemorySwapMax": "0", "CPUQuotaPerSecUSec": "4s", "RuntimeMaxUSec": "4h", "OOMPolicy": "stop",
}


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def props_text(host: str | None = HOST, extra: str = "", **over) -> str:
    """`systemctl --user show` output: PROPS with overrides (None drops a key), then Hostname."""
    p = {**PROPS, **over}
    text = "".join(f"{k}={v}\n" for k, v in p.items() if v is not None) + extra
    return text + (f"Hostname={host}\n" if host else "")


@pytest.fixture()
def repo(env) -> Path:
    (env.repo_a / "lake-manifest.json").write_text(MATHLIB_MANIFEST)
    _git(env.repo_a, "add", "lake-manifest.json")
    _git(env.repo_a, "commit", "-m", "manifest")
    return env.repo_a


class Unit:
    """What a finished unit leaves: the raw verify log, its stdout and stderr files, its saved properties."""

    def __init__(self, tmp_path: Path, log: str = LOG, *, seal: str | None = None, pre: str | None = None,
                 post: str | None = None, err: str = "", inv: str | None = INV, props: str | None = None):
        man = sha(MATHLIB_MANIFEST.encode())
        self.log = tmp_path / "verify.log"
        self.log.write_text(log)
        self.out = tmp_path / "unit.out"
        self.out.write_text(f"pre-manifest-sha256 {pre or man}\npost-manifest-sha256 {post or man}\n"
                            f"verify-log-sha256: {seal or sha(log.encode())}\n"
                            + (f"invocation-id {inv}\n" if inv else ""))
        self.err = tmp_path / "unit.err"
        self.err.write_text(err)
        self.props = tmp_path / "unit.properties"
        self.props.write_text(props_text() if props is None else props)


def run(lp, repo, u: Unit, *extra, cmd="systemd", props=True, stdout=True, stderr=True):
    argv = ["receipt", cmd, "--unit", "lp-verify", "--log", str(u.log), "--commit", repo_head(repo),
            "--repo", str(repo), "--checker", CHECKER]
    if props:
        argv += ["--properties-file", str(u.props)]
    if stdout:
        argv += ["--stdout", str(u.out)]
    if stderr:
        argv += ["--stderr", str(u.err)]
    return lp.main(argv + list(extra))


def rdir(repo) -> Path:
    return repo / ".lean-receipts"


def assert_nothing_written(repo):
    assert not rdir(repo).exists() or list(rdir(repo).iterdir()) == []


JOB_ID = f"systemd-user:{UNIT}:{INV}@{HOST}"
SLUG = f"systemd-user-{UNIT}-{INV}-{HOST}"


# --------------------------------------------------------------------------
# the positive path

def test_happy_path_receipt_passes_gate_check(lp, env, repo, tmp_path, capsys):
    before = env.registry.read_bytes()
    u = Unit(tmp_path, props=props_text(extra="ExecStart={ path=/home/someone/bin/bash ; argv[]=bash }\n"))
    assert run(lp, repo, u) == 0
    out = capsys.readouterr().out
    c12 = repo_head(repo)[:12]
    rp = rdir(repo) / f"{c12}-{SLUG}.json"
    lg = rdir(repo) / f"{c12}-{SLUG}-verify.redacted.log"
    pf = rdir(repo) / f"{c12}-{SLUG}-unit.properties"
    assert out.splitlines()[0].startswith(f"receipt systemd: ok, job {JOB_ID}")
    for needle in (f"✓ unit: {UNIT} invocation {INV[:12]}… on {HOST}: Result=success, exited 0",
                   "✓ seal: job stdout verify-log-sha256", "✓ manifest: pre = post", "✓ stderr: empty",
                   "✓ stdout: invocation-id = the unit's InvocationID", "✓ public-safety"):
        assert needle in out, needle
    assert f"## `{c12[:7]}`: lower-tier receipt, systemd unit {UNIT}" in out
    assert (f"  Result success, exit `0`; runtime 00:12:05.00, CPU 01:10:22.00, memory peak 1.5 GiB; caps "
            "MemoryMax 8.0 GiB, MemorySwapMax 0 B, CPUQuota 400%, RuntimeMax 4h.") in out
    receipt = json.loads(rp.read_text())
    assert list(receipt) == list(lp.RECEIPT_FIELDS) + ["job_stdout_sha256", "manifest_sha256",
                                                       "unit_properties_sha256"]
    assert receipt["job_id"] == JOB_ID and lp.receipt_job_slug(JOB_ID) == SLUG
    assert receipt["report_sha256"] == sha(lg.read_bytes())
    assert receipt["unit_properties_sha256"] == sha(pf.read_bytes())
    assert receipt["lean_toolchain"] == TOOLCHAIN and receipt["mathlib_rev"] == MATHLIB_REV
    assert receipt["axioms"] == STD_AXIOMS and receipt["sorry_count"] == 0 and receipt["checker"] == CHECKER
    # the durable unit evidence: the recognised properties only, in a fixed order, the host last
    kept = pf.read_text()
    assert kept == props_text() and "ExecStart" not in kept and "/home/" not in kept
    assert f"- Unit properties: `{pf.name}`, SHA-256 `{sha(pf.read_bytes())}`" in out
    assert env.registry.read_bytes() == before
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0
    assert "receipt valid at commit" in capsys.readouterr().out


def test_local_alias_and_json_payload(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path), "--json", cmd="local") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True and data["refusals"] == [] and data["job_id"] == JOB_ID and data["ran"] is False
    assert data["unit"]["result"] == "success" and data["unit"]["exec_main_code"] == "exited"
    assert data["unit"]["runtime_usec"] == 725000000 and data["unit"]["memory_peak"] == 3 << 29
    assert data["unit"]["host"] == HOST and data["unit"]["invocation_id"] == INV
    assert data["seal"]["manifest_sha256"] == sha(MATHLIB_MANIFEST.encode())
    assert Path(data["receipt"]).is_file()


def test_live_systemctl_takes_the_host_from_this_system(lp, repo, tmp_path, monkeypatch, capsys):
    """Without --properties-file the unit is read now with `systemctl --user show`, and the host is the
    running system's name, never a configured one."""
    from lean_pkg import receipt_systemd as rs
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "props.txt").write_text(props_text(host=None))
    ctl = bindir / "systemctl"
    ctl.write_text(f'#!/bin/sh\necho "$@" >> "{bindir}/calls.txt"\ncat "{bindir}/props.txt"\n')
    ctl.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(rs.socket, "gethostname", lambda: "box2")
    assert run(lp, repo, Unit(tmp_path), props=False) == 0
    assert f"systemd-user:{UNIT}:{INV}@box2" in capsys.readouterr().out
    assert (bindir / "calls.txt").read_text().split()[:4] == ["--user", "show", UNIT, "-p"]


def test_no_systemctl_names_the_properties_file_route(lp, repo, tmp_path, monkeypatch, capsys):
    from lean_pkg import receipt_systemd as rs
    monkeypatch.setattr(rs.shutil, "which", lambda name, *a, **k: None)
    assert run(lp, repo, Unit(tmp_path), props=False) == 1
    assert "--properties-file" in capsys.readouterr().err
    assert_nothing_written(repo)


# --------------------------------------------------------------------------
# the unit's terminal state: every case below is refused with nothing written

@pytest.mark.parametrize("over, needle", [
    ({"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead", "ExecMainCode": "0",
      "ExecMainExitTimestampMonotonic": "0"}, "not found (LoadState=not-found)"),
    ({"ActiveState": "active", "SubState": "running", "ExecMainCode": "0", "ExecMainExitTimestampMonotonic": "0"},
     "is not terminal (ActiveState=active, SubState=running)"),
    ({"ActiveState": "activating", "SubState": "start"}, "is not terminal (ActiveState=activating"),
    ({"ActiveState": "deactivating", "SubState": "stop-sigterm"}, "is not terminal"),
    ({"ActiveState": "inactive", "SubState": "dead", "ExecMainCode": "0", "ExecMainExitTimestampMonotonic": "0"},
     "its main process never exited"),
    ({"ActiveState": "failed", "SubState": "failed", "Result": "oom-kill", "ExecMainCode": "2",
      "ExecMainStatus": "9"}, "Result=oom-kill: the OOM killer stopped it (MemoryMax=8589934592)"),
    ({"ActiveState": "failed", "SubState": "failed", "Result": "exit-code", "ExecMainStatus": "3"},
     "main process exited 3; a receipt needs exit 0"),
    ({"ActiveState": "failed", "SubState": "failed", "Result": "signal", "ExecMainCode": "2", "ExecMainStatus": "9"},
     "main process killed by signal 9"),
    ({"ActiveState": "failed", "SubState": "failed", "Result": "core-dump", "ExecMainCode": "3",
      "ExecMainStatus": "11"}, "main process dumped by signal 11"),
    ({"ActiveState": "failed", "SubState": "failed", "Result": "timeout", "ExecMainCode": "2",
      "ExecMainStatus": "15"}, "Result=timeout: the runtime cap stopped it (RuntimeMaxUSec=4h)"),
    ({"ExecMainStatus": "1"}, "main process exited 1"),          # a success Result never excuses the exit
    ({"NRestarts": "1"}, "NRestarts=1; a receipt binds one run"),
    ({"Id": "other.service"}, "unit properties are for 'other.service', not --unit lp-verify.service"),
    ({"InvocationID": "abc"}, "InvocationID 'abc' is not 32 hex characters"),
    ({"InvocationID": None}, "no InvocationID= line"),
])
def test_unclean_unit_is_refused(lp, repo, tmp_path, capsys, over, needle):
    assert run(lp, repo, Unit(tmp_path, props=props_text(**over))) == 1
    err = capsys.readouterr().err
    assert needle in err, err
    assert "nothing written" in err.splitlines()[0]
    assert_nothing_written(repo)


def test_missing_properties_are_refused(lp, repo, tmp_path, capsys):
    """An empty capture (a --collect unit read after it vanished) and a capture without the host."""
    assert run(lp, repo, Unit(tmp_path, props="")) == 1
    assert "no Id=, LoadState=" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, props=props_text(host=None))) == 1
    assert "no `Hostname=` line" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, props=props_text() + "Result=success\n")) == 1
    assert "`Result=` appears 2 times" in capsys.readouterr().err
    assert_nothing_written(repo)
    u = Unit(tmp_path)
    assert lp.main(["receipt", "systemd", "--unit", "lp-verify", "--log", str(u.log), "--commit",
                    repo_head(repo), "--repo", str(repo), "--properties-file", str(tmp_path / "nope")]) == 2
    assert "no such file" in capsys.readouterr().err


# --------------------------------------------------------------------------
# the seal and the source: shared with receipt slurm

def test_bad_or_missing_seal_is_refused(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path, seal="c" * 64)) == 1
    assert "the log is not the one the job sealed" in capsys.readouterr().err
    u = Unit(tmp_path)
    u.log.write_text(LOG.replace("exit 0", "exit 0 (edited)"))
    assert run(lp, repo, u) == 1
    assert "the log is not the one the job sealed" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path), stdout=False) == 1          # no stdout, no in-log seal
    assert "no `verify-log-sha256: <64 hex>` line" in capsys.readouterr().err
    u = Unit(tmp_path)
    u.out.write_text("".join(x for x in u.out.read_text().splitlines(True) if "verify-log" not in x))
    assert run(lp, repo, u) == 1
    assert "0 `verify-log-sha256` line(s)" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_stdout_from_another_invocation_is_refused(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path, inv="f" * 32)) == 1
    assert "is not the unit's InvocationID" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, inv=None)) == 0             # the line is optional


def test_changed_source_after_the_seal_is_refused(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path, post="e" * 64)) == 1
    assert "the manifest changed during the job" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, pre="f" * 64, post="f" * 64)) == 1
    assert "is not lake-manifest.json committed at" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, LOG + "commit: " + "e" * 40 + "\n")) == 1
    assert "`commit:`" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, LOG + "lean-toolchain: leanprover/lean4:v4.99.0\n")) == 1
    assert "toolchain mismatch" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_nonempty_stderr_sorry_and_axioms_are_refused(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path, err="boom\n")) == 1
    assert "job stderr is not empty (5 bytes)" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, LOG + "warning: D.lean:3:8: declaration uses 'sorry'\n")) == 1
    assert "sorry count is 1" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, "".join(x for x in LOG.splitlines(True) if "axioms" not in x))) == 1
    assert "no `axioms:` line" in capsys.readouterr().err
    assert_nothing_written(repo)


def test_log_job_id_line_must_name_this_invocation(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path, LOG + "job-id: slurm-123\n")) == 1
    assert "is neither the job_id" in capsys.readouterr().err
    assert run(lp, repo, Unit(tmp_path, LOG + f"job-id: {INV}\n")) == 0


def test_home_and_checkout_paths_are_redacted_in_the_copied_log(lp, repo, tmp_path, monkeypatch, capsys):
    """A local run prints absolute paths: the checkout becomes <repo> and $HOME <HOME>; a home path that is
    neither still refuses (never printed) until a --redact clears it."""
    monkeypatch.setenv("HOME", "/home/tester")
    log = LOG + f"workdir: {repo}/src\ncache: /home/tester/.cache/lake\nbus: /run/user/1000/bus\n"
    assert run(lp, repo, Unit(tmp_path, log)) == 0
    text = next(rdir(repo).glob("*.log")).read_text()
    assert "workdir: <repo>/src" in text and "cache: <HOME>/.cache/lake" in text
    assert "bus: /run/user/<UID>/bus" in text and "/home/tester" not in text and str(repo) not in text
    for p in rdir(repo).iterdir():
        p.unlink()
    capsys.readouterr()
    foreign = log + "source: /home/someone/proj/Main.lean\n"
    assert run(lp, repo, Unit(tmp_path, foreign)) == 1
    out, err = capsys.readouterr()
    assert "home-path" in err and "redacted verify log" in err and "/home/someone" not in out + err
    assert_nothing_written(repo)
    assert run(lp, repo, Unit(tmp_path, foreign), "--redact", r"/home/someone/") == 0


def test_private_hostname_is_refused_by_the_scan(lp, repo, tmp_path, capsys):
    (repo / ".lean-pkg-private-patterns").write_text("block: node7\n")
    assert run(lp, repo, Unit(tmp_path)) == 1
    err = capsys.readouterr().err
    assert "a private string remains in the receipt JSON" in err
    assert_nothing_written(repo)


# --------------------------------------------------------------------------
# --run: lean-pkg starts the bounded unit itself (fake systemd-run / systemctl on PATH)

FAKE_RUN = r'''#!{py}
import json, os, subprocess, sys
argv = sys.argv[1:]
with open({calls!r}, "a") as f:
    f.write(json.dumps(["systemd-run"] + argv) + "\n")
cmd = argv[argv.index("--") + 1:]
opts = argv[:argv.index("--")]
props = dict(opts[i + 1].split("=", 1) for i, a in enumerate(opts) if a == "-p")
env = dict(os.environ, INVOCATION_ID={inv!r})
env.update(dict(a[len("--setenv="):].split("=", 1) for a in opts if a.startswith("--setenv=")))
out = open(props["StandardOutput"].removeprefix("truncate:"), "w") if "StandardOutput" in props else None
err = open(props["StandardError"].removeprefix("truncate:"), "w") if "StandardError" in props else None
subprocess.run(cmd, cwd=props["WorkingDirectory"], stdout=out, stderr=err, env=env)
'''

FAKE_CTL = r'''#!{py}
import json, sys
with open({calls!r}, "a") as f:
    f.write(json.dumps(["systemctl"] + sys.argv[1:]) + "\n")
if sys.argv[2] == "show":
    sys.stdout.write(open({props!r}).read())
'''

VERIFY = r'''import hashlib, sys
log = {log!r}
open(sys.argv[1], "w").write(log)
man = hashlib.sha256({manifest!r}.encode()).hexdigest()
print("pre-manifest-sha256", man)
{touch}
print("post-manifest-sha256", man)
print("verify-log-sha256:", hashlib.sha256(log.encode()).hexdigest())
import os; print("invocation-id", os.environ["INVOCATION_ID"])
'''


@pytest.fixture()
def fake_systemd(tmp_path, monkeypatch):
    from lean_pkg import receipt_systemd as rs
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls, props = bindir / "calls.jsonl", bindir / "props.txt"
    props.write_text(props_text(host=None))
    for name, body in (("systemd-run", FAKE_RUN), ("systemctl", FAKE_CTL)):
        (bindir / name).write_text(body.format(py=sys.executable, calls=str(calls), props=str(props), inv=INV))
        (bindir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(rs, "POLL_SECONDS", 0)
    monkeypatch.setattr(rs.socket, "gethostname", lambda: HOST)

    def calls_made():
        return [json.loads(x) for x in calls.read_text().splitlines()] if calls.exists() else []
    return props, calls_made


def run_live(lp, repo, tmp_path, *extra, touch: str = "", outdir: str = "run"):
    d = tmp_path / outdir
    d.mkdir(exist_ok=True)
    script = tmp_path / "verify.py"
    script.write_text(VERIFY.format(log=LOG, manifest=MATHLIB_MANIFEST, touch=touch))
    argv = ["receipt", "systemd", "--unit", "lp-verify", "--log", str(d / "verify.log"), "--commit",
            repo_head(repo), "--repo", str(repo), "--checker", CHECKER, "--stdout", str(d / "unit.out"),
            "--stderr", str(d / "unit.err"), "--run", *extra, "--", sys.executable, str(script), str(d / "verify.log")]
    return lp.main(argv), d


def test_run_starts_a_bounded_unit_and_captures_before_cleanup(lp, env, repo, tmp_path, fake_systemd, capsys):
    _props, calls_made = fake_systemd
    rc, d = run_live(lp, repo, tmp_path, "--memory-max", "2G")
    assert rc == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "✓ ran lp-verify.service; properties captured before cleanup" in out
    calls = calls_made()
    start = calls[0]
    assert start[:4] == ["systemd-run", "--user", "--unit=lp-verify.service", "--quiet"]
    for prop in ("RemainAfterExit=yes", f"WorkingDirectory={repo.resolve()}", "OOMPolicy=stop", "MemoryMax=2G",
                 "MemorySwapMax=0", "CPUQuota=400%", "RuntimeMaxSec=4h",
                 f"StandardOutput=truncate:{(d / 'unit.out').resolve()}"):
        assert prop in start, prop
    assert "--collect" not in start and "--wait" not in start
    verbs = [c[2] for c in calls[1:]]
    assert verbs[-3:] == ["show", "stop", "reset-failed"]            # captured, then stopped and reset
    captured = (d / "verify.log.unit.properties").read_text()
    assert captured.endswith(f"Hostname={HOST}\n")
    rp = next(rdir(repo).glob("*.json"))
    assert json.loads(rp.read_text())["job_id"] == JOB_ID
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)]) == 0


def test_run_oom_keeps_the_captured_properties_and_refuses(lp, repo, tmp_path, fake_systemd, capsys):
    props, calls_made = fake_systemd
    props.write_text(props_text(host=None, ActiveState="failed", SubState="failed", Result="oom-kill",
                                ExecMainCode="2", ExecMainStatus="9"))
    rc, d = run_live(lp, repo, tmp_path)
    assert rc == 1
    assert "Result=oom-kill" in capsys.readouterr().err
    assert "Result=oom-kill" in (d / "verify.log.unit.properties").read_text()   # durable raw evidence
    assert [c[2] for c in calls_made()][-2:] == ["stop", "reset-failed"]
    assert_nothing_written(repo)


def test_run_refuses_a_dirty_checkout_and_runs_nothing(lp, repo, tmp_path, fake_systemd, capsys):
    _props, calls_made = fake_systemd
    (repo / "lean-toolchain").write_text("leanprover/lean4:v4.99.0\n")
    rc, _d = run_live(lp, repo, tmp_path)
    assert rc == 1
    assert "source changed before the run: 1 tracked file(s) differ" in capsys.readouterr().err
    assert calls_made() == []
    assert_nothing_written(repo)


def test_run_refuses_source_changed_during_the_run(lp, repo, tmp_path, fake_systemd, capsys):
    rc, _d = run_live(lp, repo, tmp_path, touch="open('lake-manifest.json', 'a').write(' ')")
    assert rc == 1
    assert "source changed during the run: 1 tracked file(s) differ" in capsys.readouterr().err
    assert_nothing_written(repo)


@pytest.fixture()
def nested(tmp_path) -> Path:
    """A package incubated beside a manuscript: the git repository is the article, the package is lean/."""
    article = make_repo(tmp_path / "article", extra_files={
        "paper.tex": "\\documentclass{article}\n", "lean/lean-toolchain": TOOLCHAIN + "\n",
        "lean/lake-manifest.json": MATHLIB_MANIFEST})
    return article / "lean"


def test_run_in_a_nested_package_ignores_tracked_changes_outside_it(lp, nested, tmp_path, fake_systemd, capsys):
    # An uncommitted manuscript edit (before the run) and one made during it are outside the package
    # directory, which is all the receipt binds (the evidence-only rule is package-scoped as well).
    (nested.parent / "paper.tex").write_text("\\documentclass{amsart}\n")
    rc, _d = run_live(lp, nested, tmp_path, touch="open('../paper.tex', 'a').write('% during')")
    assert rc == 0, capsys.readouterr().err
    assert next(rdir(nested).glob("*.json"))
    assert not rdir(nested.parent).exists()


def test_run_in_a_nested_package_still_refuses_a_change_inside_it(lp, nested, tmp_path, fake_systemd, capsys):
    _props, calls_made = fake_systemd
    (nested / "lean-toolchain").write_text("leanprover/lean4:v4.99.0\n")
    rc, _d = run_live(lp, nested, tmp_path)
    assert rc == 1
    assert "source changed before the run: 1 tracked file(s) differ" in capsys.readouterr().err
    assert calls_made() == []
    assert_nothing_written(nested)


@pytest.mark.parametrize("extra, needle", [
    (("--runtime-max", "infinity"), "a finite systemd value"),
    (("--memory-max", ""), "a finite systemd value"),
    (("--setenv", "1BAD"), "expected NAME=VALUE"),
    (("--properties-file", "x"), "--run and --properties-file are exclusive"),
])
def test_run_usage_errors(lp, repo, tmp_path, fake_systemd, capsys, extra, needle):
    _props, calls_made = fake_systemd
    rc, _d = run_live(lp, repo, tmp_path, *extra)
    assert rc == 2 and needle in capsys.readouterr().err
    assert calls_made() == []


def test_run_needs_fresh_output_paths_and_a_command(lp, repo, tmp_path, fake_systemd, capsys):
    d = tmp_path / "run"
    d.mkdir()
    (d / "verify.log").write_text("stale\n")
    rc, _d = run_live(lp, repo, tmp_path)
    assert rc == 2 and "already exists; --run writes it fresh" in capsys.readouterr().err
    base = ["receipt", "systemd", "--unit", "lp-verify", "--log", str(tmp_path / "x.log"), "--commit",
            repo_head(repo), "--repo", str(repo)]
    assert lp.main(base + ["--run"]) == 2
    assert "--run needs the command after `--`" in capsys.readouterr().err
    assert lp.main(base + ["--", "true"]) == 2
    assert "is only run with --run" in capsys.readouterr().err
    assert lp.main(base[:3] + ["x.socket"] + base[4:] + ["--run", "--", "true"]) == 2
    assert "only service units" in capsys.readouterr().err


def test_run_failing_to_start_is_refused(lp, repo, tmp_path, fake_systemd, capsys):
    bindir = tmp_path / "bin"
    (bindir / "systemd-run").write_text("#!/bin/sh\necho 'Failed to start transient service unit: Unit "
                                        "lp-verify.service was already loaded' >&2\nexit 1\n")
    rc, _d = run_live(lp, repo, tmp_path, "--json")
    assert rc == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and any("systemd-run exited 1" in r for r in data["refusals"])
    assert_nothing_written(repo)


def test_report_does_not_rerun_receipt_systemd(lp):
    from lean_pkg import report
    lp.main(["llms"])
    plan = report.plan_rerun(["receipt", "systemd", "--unit", "u", "--log", "x", "--commit", "abc"])
    assert plan["argv"] is None and "receipt systemd writes" in plan["skip"]


def test_help_names_the_sources_and_redactions(lp, capsys):
    with pytest.raises(SystemExit):
        lp.main(["receipt", "--help"])
    assert "{slurm,systemd}" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        lp.main(["receipt", "local", "--help"])
    out = " ".join(capsys.readouterr().out.split())
    for needle in ("--properties-file", "--run", "RemainAfterExit", "systemd-user:<U>:<InvocationID>@<host>",
                   "redacted to <repo>, <HOME>, /run/user/<UID>/"):
        assert needle in out, needle


def test_json_names_the_sealed_files_not_the_raw_capture(lp, repo, tmp_path, capsys):
    """Field report (SWE round 3): a coordinator took the raw properties capture (the same 21 pairs in another
    order) for the sealed sidecar and its seal check refused. `sealed[]` names every file the run wrote and
    bound, with its SHA-256 and the receipt key holding it; `properties` stays the raw, unsealed input."""
    lines = props_text().splitlines(keepends=True)
    u = Unit(tmp_path, props="".join(reversed(lines)))          # same pairs, different byte order
    assert run(lp, repo, u, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    roles = {e["role"]: e for e in data["sealed"]}
    assert set(roles) == {"receipt", "redacted-log", "unit.properties"}
    receipt = json.loads(Path(roles["receipt"]["path"]).read_text())
    for e in data["sealed"]:
        assert sha(Path(e["path"]).read_bytes()) == e["sha256"]
        if e["receipt_key"]:
            assert receipt[e["receipt_key"]] == e["sha256"]
    assert roles["unit.properties"]["receipt_key"] == "unit_properties_sha256"
    assert data["properties"] == str(u.props) and sha(u.props.read_bytes()) != roles["unit.properties"]["sha256"]


def test_json_sealed_is_empty_when_refused(lp, repo, tmp_path, capsys):
    assert run(lp, repo, Unit(tmp_path, props=props_text(Result="oom-kill")), "--json") == 1
    assert json.loads(capsys.readouterr().out)["sealed"] == []
    assert_nothing_written(repo)
