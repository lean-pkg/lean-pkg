#!/usr/bin/env python3
"""`receipt ci` (proposal 0008, stage S2): the writer inside a GitHub Actions job, `--collect RUN` on the
author's machine and `--template`, plus lean-pkg's reusable `.github/workflows/verify.yml`. The runner's
environment comes from fixture dictionaries (every GITHUB_*, RUNNER_* and ACTIONS_* variable of the real
environment is cleared first, so the suite behaves the same in lean-pkg's own CI); `gh` and the OIDC endpoint
are stubs. Every refusal writes nothing; a written receipt passes S1's level 1, `gate --check` and `receipt
check`, byte for byte the same on a repeat."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

import pytest
import yaml

from .conftest import MATHLIB_MANIFEST, ROOT, _git, repo_head

WORKFLOW_REL = ".github/workflows/lean-pkg.yml"
WORKFLOW = "on: push\njobs:\n  verify:\n    uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@" + "6" * 40 + "\n"
CHECKER = "verify.sh (lake build; #print axioms)"
WORKSPACE = "/home/runner/work/alpha/alpha"
RUN, ATTEMPT = 123456789, 1
JOB = f"gha-{RUN}-{ATTEMPT}"
TOKEN_SENTINEL = "SENTINEL-oidc-request-token-7f3a"
JOB_WORKFLOW = "lean-pkg/lean-pkg/.github/workflows/verify.yml@" + "6" * 40


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@pytest.fixture(autouse=True)
def _fixed_identity(monkeypatch):
    """The receipt scan blocks this machine's user and host names; pin both (as test_receipt_systemd)."""
    import getpass
    import socket
    monkeypatch.setattr(getpass, "getuser", lambda: "idtester")
    monkeypatch.setenv("USER", "idtester")
    monkeypatch.setenv("LOGNAME", "idtester")
    monkeypatch.setattr(socket, "gethostname", lambda: "idhost.example.org")


@pytest.fixture(autouse=True)
def _no_runner_env(monkeypatch):
    """Nothing of the real environment leaks in (lean-pkg's own CI runs on GitHub Actions)."""
    for k in list(os.environ):
        if k.startswith(("GITHUB_", "RUNNER_", "ACTIONS_")) or k in ("ImageOS", "ImageVersion"):
            monkeypatch.delenv(k)


@pytest.fixture()
def repo(env) -> Path:
    r = env.repo_a
    (r / "lake-manifest.json").write_text(MATHLIB_MANIFEST)
    (r / WORKFLOW_REL).parent.mkdir(parents=True)
    (r / WORKFLOW_REL).write_text(WORKFLOW)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "manifest and workflow")
    return r


def runner_env(repo: Path, tmp_path: Path, **over) -> dict:
    head = repo_head(repo)
    e = {"GITHUB_ACTIONS": "true", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "example/alpha",
         "GITHUB_REPOSITORY_ID": "812345678", "GITHUB_RUN_ID": str(RUN), "GITHUB_RUN_ATTEMPT": str(ATTEMPT),
         "GITHUB_JOB": "verify", "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": head,
         "GITHUB_WORKFLOW_REF": f"example/alpha/{WORKFLOW_REL}@refs/heads/main", "GITHUB_WORKFLOW_SHA": head,
         "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64",
         "ImageOS": "ubuntu24", "ImageVersion": "20261020.1.0", "GITHUB_WORKSPACE": WORKSPACE,
         "RUNNER_TEMP": "/home/runner/work/_temp", "RUNNER_TOOL_CACHE": "/opt/hostedtoolcache",
         "GITHUB_STEP_SUMMARY": str(tmp_path / "step-summary.md")}
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def set_env(monkeypatch, e: dict) -> None:
    for k, v in e.items():
        monkeypatch.setenv(k, v)


def log_text(repo: Path, *, sorry: bool = False, axioms_line: str | None = None, extra: str = "") -> str:
    body = (f"commit: {repo_head(repo)}\n"
            f"lean-toolchain: {(repo / 'lean-toolchain').read_text().strip()}\n"
            f"checker: {CHECKER}\n"
            f"workdir: {WORKSPACE}\n"
            f"tmp: /home/runner/work/_temp/lean-pkg\n"
            f"elan: /home/runner/.elan/toolchains/leanprover--lean4---v4.32.0/bin/lean\n"
            f"python: /opt/hostedtoolcache/Python/3.12.7/x64/bin/python\n"
            f"lake build: ok ({WORKSPACE}/.lake/build)\n"
            + ("Demo.lean:3:0: warning: declaration uses 'sorry'\n" if sorry else "")
            + "'Demo.main_theorem' depends on axioms: [propext, Classical.choice, Quot.sound]\n"
            + (f"axioms: {axioms_line}\n" if axioms_line else "")
            + extra
            + "generated-at: 2026-10-26T12:00:00Z\n")
    return body


def sealed(text: str) -> str:
    """The in-log seal verify.yml appends: the SHA-256 of the log without the line."""
    return text + f"verify-log-sha256: {sha(text.encode())}\n"


def write_log(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "ci" / "verify.log"
    p.parent.mkdir(exist_ok=True)
    p.write_text(text)
    return p


def rdir(repo: Path) -> Path:
    return repo / ".lean-receipts"


def assert_nothing_written(repo: Path) -> None:
    assert not rdir(repo).exists() or list(rdir(repo).iterdir()) == []


def write(lp, capsys, repo: Path, log: Path, *extra: str):
    capsys.readouterr()
    rc = lp.main(["receipt", "ci", "--log", str(log), "--repo", str(repo), *extra])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, *extra: str, **env_over):
    set_env(monkeypatch, runner_env(repo, tmp_path, **env_over))
    log = write_log(tmp_path, sealed(log_text(repo)))
    rc, out, err = write(lp, capsys, repo, log, *extra)
    assert rc == 0, err
    c12 = repo_head(repo)[:12]
    return rdir(repo) / f"{c12}-{JOB}.json", rdir(repo) / f"{c12}-{JOB}-verify.redacted.log", out, err


# --------------------------------------------------------------------------
# the positive path

def test_the_receipt_passes_level_1_gate_check_and_receipt_check(lp, env, repo, tmp_path, capsys, monkeypatch):
    rp, lg, out, _err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path)
    rec = json.loads(rp.read_text())
    head = repo_head(repo)
    assert rec["job_id"] == JOB and rec["commit"] == head and rec["schema"] == "lower-receipt/1"
    assert list(rec["ci"]) == ["provider", "server_url", "repository", "repository_id", "run_id", "run_attempt",
                               "job", "event", "head_sha", "workflow_ref", "workflow_sha", "workflow_file_sha256",
                               "job_workflow", "runner", "verify_log_sha256"]
    assert rec["ci"] == {
        "provider": "github-actions", "server_url": "https://github.com", "repository": "example/alpha",
        "repository_id": 812345678, "run_id": RUN, "run_attempt": ATTEMPT, "job": "verify", "event": "push",
        "head_sha": head, "workflow_ref": f"example/alpha/{WORKFLOW_REL}@refs/heads/main", "workflow_sha": head,
        "workflow_file_sha256": sha(WORKFLOW.encode()), "job_workflow": None,
        "runner": {"environment": "github-hosted", "os": "Linux", "arch": "X64", "image": "ubuntu24",
                   "image_version": "20261020.1.0"},
        "verify_log_sha256": rec["report_sha256"]}
    assert sha(lg.read_bytes()) == rec["report_sha256"]
    # S1's level 1, exactly as gate runs it
    from lean_pkg import ci_github
    problems, row = ci_github.level1(rec, repo)
    assert problems == [] and row[0] == "pass"
    # the runner's paths are tokens; the redacted log carries no /home/runner
    text = lg.read_text()
    assert "/home/runner" not in text and "/opt/hostedtoolcache" not in text
    assert "workdir: <GITHUB_WORKSPACE>" in text and "tmp: <RUNNER_TEMP>/lean-pkg" in text
    assert "elan: <HOME>/.elan/" in text and "python: <RUNNER_TOOL_CACHE>/Python" in text
    assert out.splitlines()[0].startswith(f"receipt ci: ok, job {JOB} at commit {head[:12]}")
    assert "✓ ci: level 1 holds" in out and "· ci.job_workflow: null" in out
    # exactly one receipt-bytes annotation, on stdout, naming the written bytes
    notes = [x for x in out.splitlines() if x.startswith("::notice")]
    assert notes == [f"::notice title=lean-pkg-receipt::receipt_sha256={sha(rp.read_bytes())} "
                     f"report_sha256={rec['report_sha256']}"]
    summary = (tmp_path / "step-summary.md").read_text()
    assert "### lean-pkg receipt" in summary and f"--collect {RUN} --attempt {ATTEMPT}" in summary
    assert sha(rp.read_bytes()) in summary
    # gate --check (the registry's alpha is this checkout)
    capsys.readouterr()
    rc = lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt", str(rp)])
    cap = capsys.readouterr()
    assert rc == 0, cap.err
    assert f"✓ ci: GitHub Actions run {RUN} attempt {ATTEMPT} of example/alpha" in cap.out
    # receipt check, once the evidence is committed (an evidence-only descendant)
    _git(repo, "add", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "receipt")
    capsys.readouterr()
    rc = lp.main(["--json", "receipt", "check", "--repo", str(repo), "--commit", head])
    payload = json.loads(capsys.readouterr().out)
    fails = [f"{c['id']}: {c['detail']}" for c in payload["checks"] if c["verdict"] == "fail"]
    assert rc == 0 and fails == [], fails
    assert any(c["id"].endswith(" ci") and c["verdict"] == "pass" for c in payload["checks"]), payload["checks"]


def test_a_repeat_writes_the_same_bytes(lp, env, repo, tmp_path, capsys, monkeypatch):
    rp, lg, out1, _ = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path)
    first = (rp.read_bytes(), lg.read_bytes())
    log = write_log(tmp_path, sealed(log_text(repo)))
    rc, out2, err = write(lp, capsys, repo, log)
    assert rc == 0, err
    assert (rp.read_bytes(), lg.read_bytes()) == first
    note = [x for x in out1.splitlines() if x.startswith("::notice")]
    assert note == [x for x in out2.splitlines() if x.startswith("::notice")]


def test_json_keeps_stdout_one_document_and_the_annotation_on_stderr(lp, env, repo, tmp_path, capsys, monkeypatch):
    rp, _lg, out, err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, "--json")
    payload = json.loads(out)
    assert payload["ok"] and payload["job_id"] == JOB and payload["ci"]["run_id"] == RUN
    assert payload["producer"]["lean_pkg"]
    assert f"::notice title=lean-pkg-receipt::receipt_sha256={sha(rp.read_bytes())}" in err


def test_the_commit_alias_and_an_explicit_github_sha(lp, env, repo, tmp_path, capsys, monkeypatch):
    head = repo_head(repo)
    rp, _lg, _out, err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, "--commit", head)
    assert rp.is_file()
    assert "--commit" in err and "--receipt-commit" in err   # the alias note


def test_job_workflow_comes_from_the_oidc_claim_and_the_token_is_never_kept(lp, env, repo, tmp_path, capsys,
                                                                             monkeypatch):
    from lean_pkg import receipt_ci
    seen = {}

    def fetch(url, bearer):
        seen.update(url=url, bearer=bearer)
        claims = base64.urlsafe_b64encode(json.dumps({"job_workflow_ref": JOB_WORKFLOW,
                                                      "secret": TOKEN_SENTINEL}).encode()).rstrip(b"=").decode()
        return json.dumps({"value": f"eyJhbGciOiJSUzI1NiJ9.{claims}.c2lnbmF0dXJl"}).encode()
    monkeypatch.setattr(receipt_ci, "_request_token", fetch)
    rp, lg, out, err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, "--json",
                                  ACTIONS_ID_TOKEN_REQUEST_URL="https://token.actions.example/x?api-version=2.0",
                                  ACTIONS_ID_TOKEN_REQUEST_TOKEN=TOKEN_SENTINEL)
    assert seen == {"url": "https://token.actions.example/x?api-version=2.0&audience=lean-pkg",
                    "bearer": TOKEN_SENTINEL}
    rec = json.loads(rp.read_text())
    assert rec["ci"]["job_workflow"] == JOB_WORKFLOW
    from lean_pkg import ci_github
    assert ci_github.level1(rec, repo)[0] == []
    for blob in (out, err, rp.read_text(), lg.read_text(), (tmp_path / "step-summary.md").read_text()):
        assert TOKEN_SENTINEL not in blob


def test_a_failing_token_request_refuses(lp, env, repo, tmp_path, capsys, monkeypatch):
    from lean_pkg import receipt_ci

    def fetch(url, bearer):
        raise OSError(f"boom {bearer}")
    monkeypatch.setattr(receipt_ci, "_request_token", fetch)
    set_env(monkeypatch, runner_env(repo, tmp_path, ACTIONS_ID_TOKEN_REQUEST_URL="https://t.example/x",
                                    ACTIONS_ID_TOKEN_REQUEST_TOKEN=TOKEN_SENTINEL))
    rc, out, err = write(lp, capsys, repo, write_log(tmp_path, sealed(log_text(repo))))
    assert rc == 1 and "ci.job_workflow: the OIDC token request failed (OSError)" in err
    assert TOKEN_SENTINEL not in out + err
    assert_nothing_written(repo)


# --------------------------------------------------------------------------
# refusals: nothing written, nothing in the step summary

def refused(lp, capsys, monkeypatch, repo, tmp_path, env_over: dict | None = None, *, text: str | None = None,
            extra=()) -> str:
    set_env(monkeypatch, runner_env(repo, tmp_path, **(env_over or {})))
    log = write_log(tmp_path, sealed(log_text(repo)) if text is None else text)
    rc, out, err = write(lp, capsys, repo, log, *extra)
    assert rc == 1, out + err
    assert "receipt ci: refused" in err and "nothing written" in err
    assert "::notice" not in out + err
    assert_nothing_written(repo)
    assert not (tmp_path / "step-summary.md").exists()
    return err


def test_outside_github_actions(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, {"GITHUB_ACTIONS": None})
    assert "not in GitHub Actions" in err and "receipt local" in err


def test_a_self_hosted_runner_names_receipt_local(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, {"RUNNER_ENVIRONMENT": "self-hosted"})
    assert "RUNNER_ENVIRONMENT is 'self-hosted', not github-hosted" in err and "lean-pkg receipt local" in err


@pytest.mark.parametrize("var", ["GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_REPOSITORY_ID", "GITHUB_RUN_ID",
                                 "GITHUB_RUN_ATTEMPT", "GITHUB_JOB", "GITHUB_EVENT_NAME", "GITHUB_SHA",
                                 "GITHUB_WORKFLOW_REF", "GITHUB_WORKFLOW_SHA", "RUNNER_ENVIRONMENT", "RUNNER_OS",
                                 "RUNNER_ARCH"])
def test_each_missing_runner_variable(lp, env, repo, tmp_path, capsys, monkeypatch, var):
    from lean_pkg import receipt_ci
    assert var in receipt_ci.REQUIRED_ENV
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, {var: None})
    assert f"missing runner variable(s) {var}:" in err


@pytest.mark.parametrize("event", ["pull_request", "pull_request_target", "schedule", "release", "workflow_run",
                                   "issue_comment", "merge_group"])
def test_each_disallowed_event(lp, env, repo, tmp_path, capsys, monkeypatch, event):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, {"GITHUB_EVENT_NAME": event})
    assert f"event {event!r} is not accepted, only push and workflow_dispatch" in err


def test_workflow_dispatch_is_accepted(lp, env, repo, tmp_path, capsys, monkeypatch):
    rp, *_ = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path, GITHUB_EVENT_NAME="workflow_dispatch")
    assert json.loads(rp.read_text())["ci"]["event"] == "workflow_dispatch"


def test_a_receipt_commit_other_than_github_sha(lp, env, repo, tmp_path, capsys, monkeypatch):
    first = _git(repo, "rev-list", "--max-parents=0", "HEAD").stdout.strip()
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, extra=("--receipt-commit", first))
    assert f"--receipt-commit {first} is not GITHUB_SHA" in err


def test_a_server_other_than_github_com(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, {"GITHUB_SERVER_URL": "https://ghe.example.com"})
    assert "GitHub Enterprise Server is not accepted" in err


@pytest.mark.parametrize("over, needle", [
    ({"GITHUB_RUN_ID": "12a"}, "GITHUB_RUN_ID is '12a', not a positive integer"),
    ({"GITHUB_RUN_ATTEMPT": "0"}, "GITHUB_RUN_ATTEMPT is '0', not a positive integer"),
    ({"GITHUB_REPOSITORY_ID": "-1"}, "GITHUB_REPOSITORY_ID is '-1', not a positive integer"),
    ({"GITHUB_SHA": "abc"}, "GITHUB_SHA is 'abc', not 40 lowercase hex"),
    ({"GITHUB_WORKFLOW_REF": "example/alpha/ci.yml@main"}, "not OWNER/REPO/.github/workflows/FILE.yml@REF"),
    ({"GITHUB_WORKFLOW_REF": f"other/repo/{WORKFLOW_REL}@refs/heads/main"},
     "GITHUB_WORKFLOW_REF names repository 'other/repo', not GITHUB_REPOSITORY 'example/alpha'"),
], ids=["run-id", "attempt", "repository-id", "sha", "workflow-ref", "workflow-ref-repo"])
def test_malformed_runner_values(lp, env, repo, tmp_path, capsys, monkeypatch, over, needle):
    assert needle in refused(lp, capsys, monkeypatch, repo, tmp_path, over)


def test_the_workflow_commit_missing_from_a_shallow_clone(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, {"GITHUB_WORKFLOW_SHA": "e" * 40})
    assert "commit eeeeeeeeeeee (GITHUB_WORKFLOW_SHA) is not in this clone; a shallow checkout lacks it" in err
    assert "fetch-depth: 0" in err


def test_the_workflow_file_missing_at_the_workflow_commit(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path,
                  {"GITHUB_WORKFLOW_REF": "example/alpha/.github/workflows/gone.yml@refs/heads/main"})
    assert "the workflow file .github/workflows/gone.yml does not exist at" in err


def test_a_home_path_left_after_the_built_in_redactions_is_blocked(lp, env, repo, tmp_path, capsys, monkeypatch):
    text = sealed(log_text(repo, extra="cache: /home/runneradmin/.cache/lake\n"))
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, text=text)
    assert "public-safety: a private string remains in the redacted verify log" in err


def test_produce_refuses_a_sorry(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, text=sealed(log_text(repo, sorry=True)))
    assert "sorry" in err


def test_produce_refuses_an_axioms_line_that_omits_an_axiom(lp, env, repo, tmp_path, capsys, monkeypatch):
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, text=sealed(log_text(repo, axioms_line="propext")))
    assert "axioms" in err


def test_produce_refuses_a_broken_seal(lp, env, repo, tmp_path, capsys, monkeypatch):
    text = sealed(log_text(repo)).replace("lake build: ok", "lake build: OK")
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, text=text)
    assert "the log was changed after it was sealed" in err


def test_a_log_job_id_of_another_run(lp, env, repo, tmp_path, capsys, monkeypatch):
    text = sealed(log_text(repo, extra="job-id: gha-1-1\n"))
    err = refused(lp, capsys, monkeypatch, repo, tmp_path, text=text)
    assert f"verify log: `job-id:` 'gha-1-1' is not this run's job id '{JOB}'" in err


def test_a_refusal_under_json(lp, env, repo, tmp_path, capsys, monkeypatch):
    set_env(monkeypatch, runner_env(repo, tmp_path, GITHUB_ACTIONS=None))
    rc, out, _err = write(lp, capsys, repo, write_log(tmp_path, sealed(log_text(repo))), "--json")
    payload = json.loads(out)
    assert rc == 1 and payload["ok"] is False and payload["receipt"] is None and payload["sealed"] == []
    assert payload["refusals"][0].startswith("not in GitHub Actions")


@pytest.mark.parametrize("argv, needle", [
    (["--template", "--log", "x"], "--template takes no other flag"),
    (["--template", "--collect", "5"], "--template takes no other flag"),
    (["--attempt", "2"], "--attempt goes with --collect RUN"),
    (["--collect", "5", "--log", "x"], "--log belong to the writer"),
    (["--collect", "0"], "positive integers"),
    ([], "receipt ci needs --log PATH"),
])
def test_usage_errors_exit_2(lp, capsys, argv, needle):
    capsys.readouterr()
    assert lp.main(["receipt", "ci", *argv]) == 2
    assert needle in capsys.readouterr().err


# --------------------------------------------------------------------------
# --collect, with a stubbed gh (and a git that records every call)

FAKE_GH = r'''#!PYTHON
import fnmatch, json, os, shutil, sys
store = os.environ["FAKE_GH_STORE"]
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(["gh"] + sys.argv[1:]) + "\n")
a = sys.argv[1:]
assert a[:2] == ["run", "download"], a
dest = a[a.index("--dir") + 1]
names = sorted(os.listdir(store)) if os.path.isdir(store) else []
if "--name" in a:
    want = a[a.index("--name") + 1]
    if want not in names:
        print(f"no artifact matches any of the names or patterns provided", file=sys.stderr)
        sys.exit(1)
    for n in os.listdir(os.path.join(store, want)):
        shutil.copy(os.path.join(store, want, n), dest)
else:
    pat = a[a.index("--pattern") + 1]
    hits = [n for n in names if fnmatch.fnmatch(n, pat)]
    if not hits:
        print("no valid artifacts found to download", file=sys.stderr)
        sys.exit(1)
    for n in hits:
        shutil.copytree(os.path.join(store, n), os.path.join(dest, n))
'''

FAKE_GIT = r'''#!PYTHON
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as f:
    f.write(json.dumps(["git"] + sys.argv[1:]) + "\n")
os.execv(REAL_GIT, [REAL_GIT] + sys.argv[1:])
'''


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture()
def tools(tmp_path, monkeypatch):
    """A bin directory with a fake gh and a recording git in front of PATH; returns (bin, store, calls)."""
    real_git = shutil.which("git")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _exe(bindir / "gh", FAKE_GH.replace("#!PYTHON", f"#!{sys.executable}"))
    _exe(bindir / "git", FAKE_GIT.replace("#!PYTHON", f"#!{sys.executable}").replace("REAL_GIT", repr(real_git)))
    store = tmp_path / "artifacts"
    store.mkdir()
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_GH_STORE", str(store))
    monkeypatch.setenv("FAKE_GH_LOG", str(calls))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return bindir, store, calls


def subcommands(calls: Path) -> list[str]:
    """The git subcommand of every recorded git call (after -C DIR and -c K=V)."""
    out = []
    for line in calls.read_text().splitlines() if calls.exists() else []:
        argv = json.loads(line)
        if argv[0] != "git":
            continue
        i = 1
        while i < len(argv) and argv[i] in ("-C", "-c", "--git-dir", "--work-tree"):
            i += 2
        while i < len(argv) and argv[i].startswith("-"):
            i += 1
        out.append(argv[i] if i < len(argv) else "")
    return out


def stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store: Path, name: str | None = None, edit=None):
    """Write the receipt as the job does, then move its two files into the fake artifact store (the
    checkout keeps no .lean-receipts/, as the author's clone before collecting)."""
    rp, lg, _out, _err = ok_receipt(lp, capsys, monkeypatch, repo, tmp_path)
    for k in list(os.environ):
        if k.startswith(("GITHUB_", "RUNNER_")):
            monkeypatch.delenv(k)
    art = store / (name or f"lean-pkg-receipt-{RUN}-{ATTEMPT}")
    art.mkdir()
    if edit is not None:
        rec = json.loads(rp.read_text())
        edit(rec)
        rp.write_text(json.dumps(rec, indent=2) + "\n")
    shutil.move(str(rp), art / rp.name)
    shutil.move(str(lg), art / lg.name)
    rdir(repo).rmdir()
    return art / rp.name, art / lg.name


def collect(lp, capsys, repo, *argv):
    capsys.readouterr()
    rc = lp.main(["receipt", "ci", "--collect", *argv, "--repo", str(repo)])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


@pytest.mark.parametrize("by_attempt", [True, False])
def test_collect_checks_and_copies_and_never_commits_or_pushes(lp, env, repo, tmp_path, capsys, monkeypatch, tools,
                                                               by_attempt):
    _bindir, store, calls = tools
    rsrc, lsrc = stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store)
    argv = [str(RUN), "--attempt", str(ATTEMPT)] if by_attempt else [str(RUN)]
    rc, out, err = collect(lp, capsys, repo, *argv)
    assert rc == 0, err
    assert (rdir(repo) / rsrc.name).read_bytes() == rsrc.read_bytes()
    assert (rdir(repo) / lsrc.name).read_bytes() == lsrc.read_bytes()
    assert "passes gate's validator" in out and "✓ report_sha256" in out
    assert "nothing committed or pushed" in out and "next: git add" in out
    gh_calls = [json.loads(x) for x in calls.read_text().splitlines() if json.loads(x)[0] == "gh"]
    assert len(gh_calls) == 1 and gh_calls[0][1:4] == ["run", "download", str(RUN)]
    subs = subcommands(calls)
    assert subs and not {"commit", "push"} & set(subs), subs
    # again: the same bytes are already there, nothing changes
    rc, out, err = collect(lp, capsys, repo, *argv)
    assert rc == 0 and "already present with the same bytes" in out, err
    # the copied receipt now gates from .lean-receipts/
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "gate", "alpha", "--check", "--receipt",
                    str(rdir(repo) / rsrc.name)]) == 0


def test_collect_json(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    rsrc, _lsrc = stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store)
    rc, out, err = collect(lp, capsys, repo, str(RUN), "--json")
    payload = json.loads(out)
    assert rc == 0 and payload["ok"], err
    assert payload["receipt"] == f".lean-receipts/{rsrc.name}"
    assert [c["path"] for c in payload["copied"]] == [f".lean-receipts/{rsrc.name}", payload["log"]]


def collect_refused(lp, capsys, repo, *argv) -> str:
    rc, out, err = collect(lp, capsys, repo, *argv)
    assert rc == 1, out + err
    assert "receipt ci --collect: refused" in err and "nothing copied" in err
    return err


def test_collect_without_gh(lp, env, repo, tmp_path, capsys, monkeypatch):
    bindir = tmp_path / "nogh"
    bindir.mkdir()
    os.symlink(shutil.which("git"), bindir / "git")
    monkeypatch.setenv("PATH", str(bindir))
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert "gh (the GitHub CLI) is not on PATH" in err
    assert_nothing_written(repo)


def test_collect_artifact_missing(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    err = collect_refused(lp, capsys, repo, str(RUN), "--attempt", "1")
    assert f"artifact lean-pkg-receipt-{RUN}-1 of run {RUN} could not be downloaded (gh exit 1" in err
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert "could not be downloaded" in err
    assert_nothing_written(repo)


def test_collect_two_attempts_need_attempt(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store)
    (store / f"lean-pkg-receipt-{RUN}-2").mkdir()
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert "the run has 2 receipt artifacts" in err and "--attempt N" in err
    assert_nothing_written(repo)


def test_collect_run_id_mismatch(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store, name="lean-pkg-receipt-999-1")
    err = collect_refused(lp, capsys, repo, "999", "--attempt", "1")
    assert f"ci.run_id is {RUN}, not RUN 999" in err
    assert_nothing_written(repo)


def test_collect_an_invalid_receipt(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store, edit=lambda r: r.update(sorry_count=1))
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert "sorry_count must be 0" in err
    assert_nothing_written(repo)


def test_collect_an_invalid_ci_block(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store,
                   edit=lambda r: r["ci"].update(event="pull_request"))
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert "ci.event: 'pull_request' is not accepted" in err
    assert_nothing_written(repo)


def test_collect_different_bytes_at_the_destination(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, _calls = tools
    rsrc, _lsrc = stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store)
    rdir(repo).mkdir()
    (rdir(repo) / rsrc.name).write_text("{}\n")
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert f"{rsrc.name} already exists with different content" in err
    assert (rdir(repo) / rsrc.name).read_text() == "{}\n"
    assert sorted(p.name for p in rdir(repo).iterdir()) == [rsrc.name]


def test_collect_into_a_symlinked_evidence_directory(lp, env, repo, tmp_path, capsys, monkeypatch, tools):
    _bindir, store, calls = tools
    stage_artifact(lp, capsys, monkeypatch, repo, tmp_path, store)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.symlink(elsewhere, rdir(repo))
    err = collect_refused(lp, capsys, repo, str(RUN))
    assert ".lean-receipts/ is a symlink" in err
    assert list(elsewhere.iterdir()) == []


# --------------------------------------------------------------------------
# --template and verify.yml

def _on(doc: dict) -> dict:
    return doc.get("on", doc.get(True))      # YAML 1.1 reads a bare `on` key as True


def _walk(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, v
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_the_template_is_a_pinned_caller(lp, capsys, monkeypatch):
    from lean_pkg import receipt_ci
    monkeypatch.setattr(receipt_ci, "install_commit", lambda: "c" * 40)
    capsys.readouterr()
    assert lp.main(["receipt", "ci", "--template"]) == 0
    text = capsys.readouterr().out
    doc = yaml.safe_load(text)
    assert set(_on(doc)) == {"push", "workflow_dispatch"}
    assert ".lean-receipts/**" in _on(doc)["push"]["paths-ignore"]
    assert doc["permissions"] == {"contents": "read", "id-token": "write"}
    assert doc["jobs"]["verify"]["uses"] == f"lean-pkg/lean-pkg/.github/workflows/verify.yml@{'c' * 40}"
    capsys.readouterr()
    assert lp.main(["receipt", "ci", "--template", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "template": text, "pinned": "c" * 40}


def test_the_template_without_an_install_commit_says_so(lp, capsys, monkeypatch):
    from lean_pkg import receipt_ci
    monkeypatch.setattr(receipt_ci, "install_commit", lambda: None)
    capsys.readouterr()
    assert lp.main(["receipt", "ci", "--template"]) == 0
    cap = capsys.readouterr()
    assert receipt_ci.PIN_PLACEHOLDER in cap.out and "replace" in cap.err


VERIFY_YML = ROOT / ".github" / "workflows" / "verify.yml"


def test_verify_yml_is_reusable_minimal_and_pinned():
    doc = yaml.safe_load(VERIFY_YML.read_text())
    on = _on(doc)
    assert list(on) == ["workflow_call"], on                     # never on lean-pkg's own pushes
    uses = [v for k, v in _walk(doc) if k == "uses"]
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}", u) for u in uses), uses
    perms = [v for k, v in _walk(doc) if k == "permissions"]
    assert perms and all(p == {"contents": "read", "id-token": "write"} for p in perms), perms
    assert all(v != "write" for k, v in _walk(doc) if k == "contents")
    steps = doc["jobs"]["verify"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)
    assert "lean-pkg receipt ci" in runs and "verify-log-sha256: " in runs
    assert "${{ inputs." not in runs                              # inputs reach shells through env only
    assert doc["jobs"]["verify"]["steps"][0]["with"]["ref"] == "${{ github.sha }}"
    upload = [s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(upload) == 1
    assert upload[0]["with"]["name"] == "lean-pkg-receipt-${{ github.run_id }}-${{ github.run_attempt }}"
    assert "self-hosted" not in str(doc["jobs"]["verify"]["runs-on"])


def test_the_template_names_the_verify_workflow_inputs():
    """The caller's `with:` keys are inputs verify.yml declares."""
    from lean_pkg import receipt_ci
    caller = yaml.safe_load(receipt_ci.template("d" * 40))
    inputs = _on(yaml.safe_load(VERIFY_YML.read_text()))["workflow_call"]["inputs"]
    assert set(caller["jobs"]["verify"]["with"]) <= set(inputs)
