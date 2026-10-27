#!/usr/bin/env python3
"""The comparator tier in CI (proposal 0008, stage S5): `comparator-receipt` (`lean-pkg receipt comparator`)
recognizes a GitHub-hosted runner as a third host-policy context, records `host_policy: "GitHub Actions run R
attempt A"`, `job_id: gha-R-A` and the run's `ci` block (less verify_log_sha256), and refuses when the runner's
variables are incomplete. A comparator receipt backs a `github-actions` compute job only with a block that
passes level 1, the CI counterpart of the sacct seal a Slurm claim needs; slurm_job_of is unchanged. verify.yml's
comparator job checks the judge route with `host-check --routes` after lifting the AppArmor restriction."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

from .conftest import ROOT, _git
from .test_ci_receipt_level1 import GHA, WORKFLOW, WORKFLOW_REL, coverage_check, sha
from .test_comparator_receipt import git, project, run_runner  # noqa: F401  (project: a fixture)
from .test_page import COVERAGE, page_json, prepare
from .test_receipt_check import check as receipt_check
from .test_receipt_check import referee_clone, verdicts

RUN, ATTEMPT = 123456789, 1
POLICY = f"GitHub Actions run {RUN} attempt {ATTEMPT}"


@pytest.fixture()
def cr():
    import importlib
    return importlib.import_module("lean_pkg.comparator_receipt")


def runner_env(head: str, **over) -> dict:
    """What a GitHub-hosted runner sets for a push run of `head` (the workflow file committed at head)."""
    e = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_SERVER_URL": "https://github.com",
         "GITHUB_REPOSITORY": "example/beta", "GITHUB_REPOSITORY_ID": "812345678", "GITHUB_RUN_ID": str(RUN),
         "GITHUB_RUN_ATTEMPT": str(ATTEMPT), "GITHUB_JOB": "comparator", "GITHUB_EVENT_NAME": "push",
         "GITHUB_SHA": head, "GITHUB_WORKFLOW_REF": f"example/beta/{WORKFLOW_REL}@refs/heads/main",
         "GITHUB_WORKFLOW_SHA": head, "RUNNER_OS": "Linux", "RUNNER_ARCH": "X64", "ImageOS": "ubuntu24",
         "ImageVersion": "20261020.1.0"}
    for k, v in over.items():
        if v is None:
            e.pop(k, None)
        else:
            e[k] = v
    return e


def comparator_ci(commit: str) -> dict:
    """The block comparator-receipt writes on a hosted runner: the lower-tier block less verify_log_sha256."""
    return {"provider": "github-actions", "server_url": "https://github.com", "repository": "example/beta",
            "repository_id": 812345678, "run_id": RUN, "run_attempt": ATTEMPT, "job": "comparator",
            "event": "push", "head_sha": commit,
            "workflow_ref": f"example/beta/{WORKFLOW_REL}@refs/heads/main", "workflow_sha": commit,
            "workflow_file_sha256": sha(WORKFLOW), "job_workflow": None,
            "runner": {"environment": "github-hosted", "os": "Linux", "arch": "X64", "image": "ubuntu24",
                       "image_version": "20261020.1.0"}}


# --------------------------------------------------------------------------
# the host policy: hosted, self-hosted, GITHUB_ACTIONS without RUNNER_ENVIRONMENT

def test_a_hosted_runner_is_the_github_actions_context(cr):
    ctx, problems = cr.github_actions_context(runner_env("a" * 40))
    assert problems == [] and ctx == {"run_id": RUN, "run_attempt": ATTEMPT, "job_id": GHA, "host_policy": POLICY}
    ok, why = cr.host_policy_ok(None, False, hostname="runnervm1", github=ctx)
    assert (ok, why) == (True, POLICY)
    # first, so host_policy, job_id and the ci block name one run: before the override and a Slurm job id
    assert cr.host_policy_ok("4242", True, hostname="runnervm1", github=ctx) == (True, POLICY)


@pytest.mark.parametrize("over", [
    {"RUNNER_ENVIRONMENT": "self-hosted"},          # the author's own machine
    {"RUNNER_ENVIRONMENT": None},                   # GITHUB_ACTIONS without RUNNER_ENVIRONMENT
    {"RUNNER_ENVIRONMENT": ""},
    {"GITHUB_ACTIONS": None},                       # a hosted value without GITHUB_ACTIONS
    {"GITHUB_ACTIONS": "false"},
], ids=["self-hosted", "no-runner-environment", "empty-runner-environment", "no-github-actions", "false"])
def test_other_runners_are_not_recognized(cr, over):
    assert cr.github_actions_context(runner_env("a" * 40, **over)) == (None, [])
    # not recognized: the other rules decide, as before (no allow-list, no workstation default: refused)
    ok, why = cr.host_policy_ok(None, False, hostname="runnervm1", github=None)
    assert ok is False and "GitHub Actions" not in why
    assert cr.host_policy_ok(None, True, hostname="runnervm1", github=None) == (
        True, "allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1")


@pytest.mark.parametrize("over, needle", [
    ({"GITHUB_RUN_ID": None}, "missing runner variable(s) GITHUB_RUN_ID"),
    ({"GITHUB_WORKFLOW_SHA": ""}, "missing runner variable(s) GITHUB_WORKFLOW_SHA"),
    ({"GITHUB_RUN_ATTEMPT": "0"}, "GITHUB_RUN_ATTEMPT is '0', not a positive integer"),
    ({"GITHUB_SHA": "abc"}, "GITHUB_SHA is 'abc', not 40 lowercase hex"),
    ({"GITHUB_EVENT_NAME": "pull_request"}, "event 'pull_request' is not accepted"),
    ({"GITHUB_SERVER_URL": "https://ghe.example.com"}, "GitHub Enterprise Server is not accepted"),
    ({"GITHUB_WORKFLOW_REF": "other/repo/.github/workflows/x.yml@refs/heads/main"}, "names repository 'other/repo'"),
])
def test_an_incomplete_hosted_runner_refuses(cr, over, needle):
    ctx, problems = cr.github_actions_context(runner_env("a" * 40, **over))
    assert ctx is None and any(needle in p for p in problems), problems


# --------------------------------------------------------------------------
# end to end: the receipt a hosted runner writes

@pytest.fixture()
def gproject(project: Path) -> Path:
    (project / WORKFLOW_REL).parent.mkdir(parents=True)
    (project / WORKFLOW_REL).write_text(WORKFLOW)
    git(project, "add", "-A")
    git(project, "commit", "-m", "workflow")
    return project


def receipt_of(project: Path) -> Path:
    return project / ".lean-receipts" / f"{git(project, 'rev-parse', 'HEAD')[:12]}-comparator.json"


def test_a_hosted_run_records_the_run(gproject: Path):
    head = git(gproject, "rev-parse", "HEAD")
    r = run_runner(gproject, allow=False, extra_env=runner_env(head))
    assert r.returncode == 0, r.stderr
    assert f"host policy: {POLICY}" in r.stdout
    rec = json.loads(receipt_of(gproject).read_text())
    assert rec["job_id"] == GHA and rec["host_policy"] == POLICY
    assert rec["ci"] == comparator_ci(head) and "verify_log_sha256" not in rec["ci"]
    from lean_pkg import ci_github
    problems, row = ci_github.level1(rec, gproject)
    assert problems == [] and row[0] == "pass"
    assert ci_github.gha_job_of(rec, gproject) == GHA


def test_an_allow_override_on_a_hosted_runner_still_records_the_run(gproject: Path):
    head = git(gproject, "rev-parse", "HEAD")
    r = run_runner(gproject, allow=True, extra_env=runner_env(head))
    assert r.returncode == 0, r.stderr
    rec = json.loads(receipt_of(gproject).read_text())
    assert rec["job_id"] == GHA and rec["host_policy"] == POLICY and "ci" in rec


@pytest.mark.parametrize("over", [{"RUNNER_ENVIRONMENT": "self-hosted"}, {"RUNNER_ENVIRONMENT": None}],
                         ids=["self-hosted", "no-runner-environment"])
def test_a_runner_not_recognized_writes_an_ordinary_receipt(gproject: Path, over):
    head = git(gproject, "rev-parse", "HEAD")
    r = run_runner(gproject, allow=False, extra_env=runner_env(head, **over))
    assert r.returncode == 1 and "is not an allowed host" in r.stderr     # the ordinary policy refuses
    assert not (gproject / ".lean-receipts").exists()
    r = run_runner(gproject, allow=True, extra_env=runner_env(head, **over))
    assert r.returncode == 0, r.stderr
    rec = json.loads(receipt_of(gproject).read_text())
    assert rec["job_id"].startswith("local-") and "ci" not in rec
    assert rec["host_policy"] == "allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"


@pytest.mark.parametrize("over, needle", [
    ({"GITHUB_RUN_ATTEMPT": None}, "without a complete run identity: missing runner variable(s) GITHUB_RUN_ATTEMPT"),
    ({"GITHUB_EVENT_NAME": "pull_request_target"}, "event 'pull_request_target' is not accepted"),
    ({"GITHUB_SHA": "f" * 40}, "ci.head_sha: ffffffffffff is not the receipt commit"),
    ({"GITHUB_WORKFLOW_SHA": "e" * 40}, "commit eeeeeeeeeeee (GITHUB_WORKFLOW_SHA) is not in this clone"),
    ({"GITHUB_WORKFLOW_REF": "example/beta/.github/workflows/gone.yml@refs/heads/main"},
     ".github/workflows/gone.yml does not exist"),
], ids=["missing-attempt", "event", "head-sha", "workflow-commit", "workflow-file"])
def test_a_hosted_run_refuses_and_writes_nothing(gproject: Path, over, needle):
    head = git(gproject, "rev-parse", "HEAD")
    r = run_runner(gproject, allow=True, extra_env=runner_env(head, **over))
    assert r.returncode == 1 and needle in r.stderr and "nothing written" in r.stderr, r.stderr
    assert not (gproject / ".lean-receipts").exists()


# --------------------------------------------------------------------------
# level 1 of a comparator receipt's block

def comp(commit: str = "c" * 40) -> dict:
    return {"schema": "comparator-receipt/2", "commit": commit, "job_id": GHA, "host_policy": POLICY,
            "ci": comparator_ci(commit)}


def test_a_comparator_block_passes_without_verify_log_sha256():
    from lean_pkg import ci_github
    problems, row = ci_github.level1(comp(), None)
    assert problems == [] and row[0] == "skip"
    assert "host_policy" in ci_github.summary(comp()) and "verify_log" not in ci_github.summary(comp())


@pytest.mark.parametrize("edit, needle", [
    (lambda r: r["ci"].update(verify_log_sha256="a" * 64), "ci.verify_log_sha256: a comparator receipt has no "
                                                           "verify log"),
    (lambda r: r.update(host_policy="allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"),
     "host_policy: 'allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1' is not 'GitHub Actions run 123456789 attempt 1'"),
    (lambda r: r.update(host_policy="GitHub Actions run 123456789 attempt 2"), "host_policy:"),
    (lambda r: r.update(job_id="gha-123456789-2"), "job_id: 'gha-123456789-2' is not gha-123456789-1"),
    (lambda r: r["ci"]["runner"].update(environment="self-hosted"), "receipt local"),
    (lambda r: r["ci"].update(head_sha="d" * 40), "ci.head_sha: dddddddddddd is not the receipt commit"),
    (lambda r: r["ci"].pop("runner"), "ci: missing required key(s) runner"),
    (lambda r: r["ci"].update(event="schedule"), "ci.event: 'schedule' is not accepted"),
], ids=["verify-log", "policy-override", "policy-attempt", "job-id", "self-hosted", "head-sha", "missing", "event"])
def test_each_comparator_near_miss_fails_level_1(edit, needle):
    from lean_pkg import ci_github
    rec = comp()
    edit(rec)
    problems, _row = ci_github.level1(rec, None)
    assert any(needle in p for p in problems), problems
    assert ci_github.gha_job_of(rec, None) is None


def test_slurm_job_of_is_unchanged_by_a_gha_comparator_receipt():
    from lean_pkg.coverage import slurm_job_of
    assert slurm_job_of(comp()) is None
    assert slurm_job_of({**comp(), "job_id": "5581013", "host_policy": "Slurm job 5581013 on h-1"}) is None


# --------------------------------------------------------------------------
# backing compute, gate --check and receipt check

def gha_comparator_page(env, edit=None, *, with_ci: bool = True, lower_too: bool = False) -> str:
    """beta made page-ready with a local lower-tier receipt and a comparator receipt from GitHub Actions run
    RUN attempt ATTEMPT of X; coverage.yaml lists that job under github-actions. lower_too: the lower-tier
    receipt is from the same run (verify.yml's verify job), with its own ci block."""
    doc = copy.deepcopy(COVERAGE)
    doc["compute"] = [{"cluster": "github-actions", "jobs": [GHA]}]
    x = prepare(env, doc, job=GHA if lower_too else "local-h-1f2e3d4c5b6a-1", extra={WORKFLOW_REL: WORKFLOW},
                comparator={"schema": "comparator-receipt/2", "mechanism": "legacy", "job_id": GHA,
                            "host_policy": POLICY})
    if lower_too:
        from .test_ci_receipt_level1 import ci_block
        lrel = f".lean-receipts/{x[:12]}-{GHA}.json"
        lrec = json.loads((env.repo_b / lrel).read_text())
        lrec["schema"] = "lower-receipt/1"
        lrec["ci"] = ci_block(lrec)
        (env.repo_b / lrel).write_text(json.dumps(lrec, indent=2))
        _git(env.repo_b, "add", "-f", lrel)
    rel = f".lean-receipts/{x[:12]}-comparator.json"
    if not with_ci:
        return x
    rec = json.loads((env.repo_b / rel).read_text())
    rec["ci"] = comparator_ci(x)
    if edit is not None:
        edit(rec)
    (env.repo_b / rel).write_text(json.dumps(rec, indent=2))
    _git(env.repo_b, "add", "-f", rel)
    _git(env.repo_b, "commit", "-q", "-m", "ci block")
    return x


def test_a_gha_comparator_receipt_backs_the_job(lp, env, capsys):
    gha_comparator_page(env)
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 0 and data["errors"] == [], data
    rc, a = page_json(lp, env, capsys)
    assert rc == 0, a
    assert a["sites"]["github-actions"] == {"backed": True, "jobs": [GHA], "level": 1, "levels": {GHA: 1},
                                            "sentence": False}


def test_the_lower_tier_receipt_of_the_same_run_is_the_one_indexed(lp, env, capsys):
    """verify.yml's verify and comparator jobs share gha-R-A; corroboration reads the lower-tier receipt's
    annotation, so the index names it even though the comparator receipt sorts first."""
    from lean_pkg import ci_github
    x = gha_comparator_page(env, lower_too=True)
    assert ci_github.jobs_at_head(env.repo_b) == {GHA: f".lean-receipts/{x[:12]}-{GHA}.json"}
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 0 and data["errors"] == [], data


def test_a_gha_comparator_receipt_without_the_block_backs_nothing(lp, env, capsys):
    gha_comparator_page(env, with_ci=False)
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 1 and any(f"has job_id {GHA} but no ci block, so it backs no GitHub Actions job" in e
                           for e in data["errors"]), data


def test_a_failing_comparator_block_is_named_and_refused(lp, env, capsys, tmp_path):
    x = gha_comparator_page(env, edit=lambda r: r.update(host_policy="allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"))
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 1 and any(f"has job_id {GHA} but its ci block fails: host_policy:" in e
                           for e in data["errors"]), data
    clone = referee_clone(env, tmp_path)
    rc, payload = receipt_check(lp, capsys, clone, x)
    rel = f".lean-receipts/{x[:12]}-comparator.json"
    row = next(c for c in payload["checks"] if c["id"] == f"comparator {rel}")
    assert rc == 1 and row["verdict"] == "fail" and "comparator ci block" in row["detail"], row


def test_receipt_check_shows_the_comparator_ci_rows(lp, env, capsys, tmp_path):
    x = gha_comparator_page(env)
    clone = referee_clone(env, tmp_path)
    _rc, payload = receipt_check(lp, capsys, clone, x)
    rel = f".lean-receipts/{x[:12]}-comparator.json"
    v = verdicts(payload)
    assert v[f"comparator {rel}"] == "pass" and v[f"comparator {rel}: ci"] == "pass", v
    assert v[f"comparator {rel}: ci workflow file"] == "pass"


def test_gate_check_shows_the_comparator_run(lp, env, capsys):
    x = gha_comparator_page(env)
    from lean_pkg import gate as gate_mod
    from .conftest import read_reg
    pkg = read_reg(env)["packages"]["beta"]
    rec = json.loads((env.repo_b / f".lean-receipts/{x[:12]}-comparator.json").read_text())
    gate_mod.validate_comparator(pkg, env.repo_b, rec, None)
    lines = gate_mod.comparator_check_lines(pkg, env.repo_b, rec, None)
    assert any(line.startswith(f"✓ ci: GitHub Actions run {RUN} attempt {ATTEMPT}") for line in lines), lines


# --------------------------------------------------------------------------
# verify.yml's comparator job

def test_verify_yml_comparator_job_checks_the_route_after_the_apparmor_fix():
    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "verify.yml").read_text())
    inputs = doc.get("on", doc.get(True))["workflow_call"]["inputs"]
    assert inputs["comparator"] == {"description": inputs["comparator"]["description"], "type": "boolean",
                                    "default": False}
    job = doc["jobs"]["comparator"]
    assert job["if"] == "${{ inputs.comparator }}" and "self-hosted" not in str(job["runs-on"])
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    runs = [s.get("run", "") for s in job["steps"]]
    fix = next(i for i, r in enumerate(runs) if "sysctl -w kernel.apparmor_restrict_unprivileged_userns=0" in r)
    route = next(i for i, r in enumerate(runs) if "lean-pkg comparator host-check --routes" in r)
    write = next(i for i, r in enumerate(runs) if "lean-pkg receipt comparator" in r)
    assert fix < route < write
    assert "route judge: yes" in runs[route] and "exit 1" in runs[route]
    assert "${{ inputs." not in "\n".join(runs)
    upload = [s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert len(upload) == 1 and upload[0]["with"]["name"] == \
        "lean-pkg-comparator-receipt-${{ github.run_id }}-${{ github.run_attempt }}"


def test_the_host_routes_line_the_workflow_greps_is_what_host_check_prints():
    from lean_pkg import host_routes
    rep = {"routes": [{"route": "judge", "supported": True, "reason": "r", "commands": [], "notes": []}],
           "executables": {}, "locale": {"level": "ok", "LANG": "C", "LC_ALL": None, "c_utf8": True, "detail": ""},
           "limits": {"level": "ok", "nofile_soft": 1, "nofile_hard": 1, "file_nr": None},
           "resources": {"cpus_allowed": 4, "cgroup_version": 2, "cgroup_memory_max": None, "mem_available": None,
                         "slurm": {}}, "advice": []}
    assert host_routes.render_text(rep)[0].startswith("route judge: yes")

