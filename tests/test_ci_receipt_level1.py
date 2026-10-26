#!/usr/bin/env python3
"""A compute receipt from a GitHub Actions run, level 1 (proposal 0008, stage S1): the `github-actions`
scheduler and its built-in site profile, the additive `ci` block of `lower-receipt/1` judged offline by
`gate`, `gate --check`, `coverage check`, `page` and `receipt check`. Every level-1 rule of the proposal's key
table has a refusal here; unknown keys are tolerated; a gha- receipt without the block gates but backs no
compute job; the page never carries the GitHub Actions sentence at level 1."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from .conftest import _git, make_receipt, read_reg, write_report_log
from .test_page import COVERAGE, page_json, prepare, run
from .test_page import check as page_check
from .test_receipt_check import check as receipt_check
from .test_receipt_check import referee_clone, verdicts
from .test_sites import OTHER

GHA = "gha-123456789-1"
WORKFLOW_REL = ".github/workflows/lean-pkg.yml"
WORKFLOW = "on: push\njobs:\n  verify:\n    uses: lean-pkg/lean-pkg/.github/workflows/verify.yml@" + "6" * 40 + "\n"
SENTENCE = "Verification ran on [GitHub Actions](https://docs.github.com/en/actions) hosted runners ({runs})."


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def ci_block(rec: dict) -> dict:
    """The block `receipt ci` would write for the receipt (every value as the runner sets it)."""
    return {"provider": "github-actions", "server_url": "https://github.com", "repository": "example/beta",
            "repository_id": 812345678, "run_id": 123456789, "run_attempt": 1, "job": "verify",
            "event": "push", "head_sha": rec["commit"],
            "workflow_ref": f"example/beta/{WORKFLOW_REL}@refs/heads/main", "workflow_sha": rec["commit"],
            "workflow_file_sha256": sha(WORKFLOW),
            "job_workflow": "lean-pkg/lean-pkg/.github/workflows/verify.yml@" + "6" * 40,
            "runner": {"environment": "github-hosted", "os": "Linux", "arch": "X64", "image": "ubuntu24",
                       "image_version": "20261020.1.0"},
            "verify_log_sha256": rec["report_sha256"]}


def _set(ci: dict, dotted: str, value) -> None:
    *head, last = dotted.split(".")
    for k in head:
        ci = ci[k]
    if value is DELETE:
        del ci[last]
    else:
        ci[last] = value


DELETE = object()


# --------------------------------------------------------------------------
# the scheduler and the built-in profile

def test_the_scheduler_is_built_in_with_its_sentence_level():
    from lean_pkg import sites
    g = sites.SCHEDULERS["github-actions"]
    assert g is sites.GITHUB_ACTIONS and g.label == "GitHub Actions" and g.placeholder == "gha-R-A"
    assert g.job_re.pattern == r"^gha-\d+-\d+$" and g.builtin_only
    assert sites.SLURM.sentence_level == 1 and g.sentence_level == 2 and not sites.SLURM.builtin_only
    for ok in ("gha-123456789-1", "gha-1-2"):
        assert g.job_re.match(ok)
    for bad in ("gha-123", "gha-1-1-1", "gha-a-1", "slurm-12345", "GHA-1-1", "gha--1"):
        assert not g.job_re.match(bad), bad
    site = sites.builtin()["github-actions"]
    assert site.acknowledgement == SENTENCE and site.scheduler == "github-actions" and site.source == "built-in"


@pytest.mark.parametrize("text, claims", [
    (SENTENCE, True),
    ("Verification ran on GitHub Actions hosted runners.", True),
    ("[![CI](https://github.com/example/beta/actions/workflows/ci.yml/badge.svg)]"
     "(https://github.com/example/beta/actions/workflows/ci.yml)", False),
    ("Tests run on GitHub Actions.", False),
    ("GitHub Actions", False),
    ("We use GitHub Actions for continuous integration.", False),
])
def test_the_mention_pattern_is_narrow(text, claims):
    from lean_pkg import sites
    assert bool(sites.builtin()["github-actions"].mention_re.search(text)) is claims


def test_a_package_profile_may_not_name_the_built_in_only_scheduler():
    from lean_pkg import sites
    site, errors = sites.parse(OTHER.replace("scheduler: slurm", "scheduler: github-actions"), "x.yaml", "x.yaml")
    assert site is None and any("scheduler: github-actions is built-in only" in e for e in errors), errors


@pytest.mark.parametrize("source", ["x.yaml", "built-in"])
def test_the_runs_token_is_refused_outside_a_level_2_built_in_profile(source):
    from lean_pkg import sites
    text = OTHER.replace("Cluster](https://example.org/cluster).", "Cluster](https://example.org/cluster) ({runs}).")
    site, errors = sites.parse(text, "x.yaml", source)
    assert site is None and any("acknowledgement: the token {runs} is allowed only" in e for e in errors), errors
    # a package profile naming the level-2 scheduler is refused for the scheduler and for the token
    site, errors = sites.parse(text.replace("scheduler: slurm", "scheduler: github-actions"), "x.yaml", "x.yaml")
    assert site is None and any("{runs}" in e for e in errors) and any("built-in only" in e for e in errors)


def test_a_package_may_not_redefine_the_github_actions_profile(lp, env, capsys):
    from .test_sites import _own_site
    prepare(env)
    _own_site(env, OTHER.replace("name: testsite", "name: github-actions"), name="github-actions")
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "'github-actions' is a built-in site profile" in err


# --------------------------------------------------------------------------
# gate and gate --check: the validator's level-1 rules

def gha_receipt(env, tmp_path, edit=None, *, with_ci: bool = True, job: str = GHA) -> tuple[dict, object]:
    """alpha with its workflow file committed, and a receipt from a GitHub Actions run bound to that HEAD,
    with its sealed log beside it; `edit(ci)` changes the block before it is written."""
    repo = env.repo_a
    if not (repo / WORKFLOW_REL).is_file():
        (repo / WORKFLOW_REL).parent.mkdir(parents=True)
        (repo / WORKFLOW_REL).write_text(WORKFLOW)
        _git(repo, "add", WORKFLOW_REL)
        _git(repo, "commit", "-q", "-m", "workflow")
    rec = make_receipt(repo, job_id=job, schema="lower-receipt/1")
    write_report_log(tmp_path, rec)
    if with_ci:
        rec["ci"] = ci_block(rec)
        if edit is not None:
            edit(rec["ci"])
    p = tmp_path / "receipt.json"
    p.write_text(json.dumps(rec))
    return rec, p


def gate(lp, env, capsys, p, *extra):
    capsys.readouterr()
    rc = lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), *extra])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_a_valid_block_gates_and_its_run_is_shown(lp, env, tmp_path, capsys):
    _rec, p = gha_receipt(env, tmp_path)
    rc, out, err = gate(lp, env, capsys, p, "--check")
    assert rc == 0, err
    assert "✓ ci: GitHub Actions run 123456789 attempt 1 of example/beta (event push, job verify" in out
    assert "level 1, offline" in out
    assert f"✓ ci: workflow file {WORKFLOW_REL} at" in out
    rc, out, err = gate(lp, env, capsys, p)
    assert rc == 0, err
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


@pytest.mark.parametrize("edit", [
    lambda ci: ci.update(extra_key="anything", nested={"a": 1}),
    lambda ci: ci["runner"].update(labels=["ubuntu-latest"]),
    lambda ci: ci.update(job_workflow=None),                       # no id-token: write; level 1 accepts it
    lambda ci: ci.update(attestation="4f1c2d3e4a5b-gha-123456789-1.sigstore.json"),
    lambda ci: ci["runner"].update(image=None, image_version=None),
    lambda ci: ci.update(event="workflow_dispatch"),
], ids=["unknown-keys", "unknown-runner-keys", "job-workflow-null", "attestation", "image-null", "dispatch"])
def test_tolerated_and_optional_values_pass(lp, env, tmp_path, capsys, edit):
    _rec, p = gha_receipt(env, tmp_path, edit)
    rc, _out, err = gate(lp, env, capsys, p, "--check")
    assert rc == 0, err


def test_a_workflow_commit_absent_from_the_clone_is_a_shown_skip(lp, env, tmp_path, capsys):
    _rec, p = gha_receipt(env, tmp_path, lambda ci: ci.update(workflow_sha="e" * 40))
    rc, out, err = gate(lp, env, capsys, p, "--check")
    assert rc == 0, err
    assert f"· ci: workflow file {WORKFLOW_REL} not rehashed: commit eeeeeeeeeeee (ci.workflow_sha) is not in " \
           f"this clone" in out


REFUSALS = [
    *[(f"missing-{k}", k, DELETE, f"ci: missing required key(s) {k}")
      for k in ("provider", "server_url", "repository", "repository_id", "run_id", "run_attempt", "job", "event",
                "head_sha", "workflow_ref", "workflow_sha", "workflow_file_sha256", "job_workflow", "runner",
                "verify_log_sha256")],
    ("provider", "provider", "gitlab-ci", "ci.provider: must be 'github-actions'"),
    ("server", "server_url", "https://ghe.example.com",
     "ci.server_url: must be https://github.com (GitHub Enterprise Server is not accepted)"),
    ("repository", "repository", "beta", "ci.repository: must be OWNER/REPO"),
    ("repository-id-text", "repository_id", "812345678",
     "ci.repository_id: must be an integer > 0"),
    ("repository-id-zero", "repository_id", 0, "ci.repository_id: must be an integer > 0"),
    ("run-id-zero", "run_id", 0, "ci.run_id: must be an integer > 0"),
    ("run-id-text", "run_id", "123456789", "ci.run_id: must be an integer > 0"),
    ("run-id-bool", "run_id", True, "ci.run_id: must be an integer > 0"),
    ("attempt-zero", "run_attempt", 0, "ci.run_attempt: must be an integer >= 1"),
    ("job-id-run", "run_id", 987, "job_id: 'gha-123456789-1' is not gha-987-1"),
    ("job-id-attempt", "run_attempt", 2, "job_id: 'gha-123456789-1' is not gha-123456789-2"),
    ("job-empty", "job", "", "ci.job: must be one non-empty line"),
    *[(f"event-{e}", "event", e,
       f"ci.event: '{e}' is not accepted, only push and workflow_dispatch")
      for e in ("pull_request", "pull_request_target", "schedule", "release", "workflow_run")],
    ("head-sha-mismatch", "head_sha", "b" * 40,
     "ci.head_sha: bbbbbbbbbbbb is not the receipt commit"),
    ("head-sha-malformed", "head_sha", "B" * 40, "ci.head_sha: must be 40 lowercase hex"),
    ("workflow-ref-malformed", "workflow_ref", "example/beta/ci.yml@main",
     "ci.workflow_ref: must be OWNER/REPO/.github/workflows/FILE.yml@REF"),
    ("workflow-ref-subdir", "workflow_ref", "example/beta/.github/workflows/a/b.yml@main",
     "ci.workflow_ref: must be OWNER/REPO/.github/workflows/FILE.yml@REF"),
    ("workflow-ref-repository", "workflow_ref", f"other/beta/{WORKFLOW_REL}@refs/heads/main",
     "ci.workflow_ref: names repository 'other/beta', not ci.repository 'example/beta'"),
    ("workflow-sha-malformed", "workflow_sha", "abc", "ci.workflow_sha: must be 40 lowercase hex"),
    ("workflow-file-malformed", "workflow_file_sha256", "xyz",
     "ci.workflow_file_sha256: must be 64 lowercase hex"),
    ("workflow-file-mismatch", "workflow_file_sha256", "d" * 64,
     f"ci.workflow_file_sha256: {WORKFLOW_REL} at"),
    ("workflow-file-absent", "workflow_ref", "example/beta/.github/workflows/other.yml@main",
     "ci.workflow_file_sha256: .github/workflows/other.yml does not exist at"),
    ("job-workflow-malformed", "job_workflow", "lean-pkg/lean-pkg/verify.yml@v1",
     "ci.job_workflow: must be null or OWNER/REPO/.github/workflows/FILE.yml@REF"),
    ("runner-not-mapping", "runner", "ubuntu-latest", "ci.runner: must be a mapping"),
    ("runner-key-missing", "runner.arch", DELETE, "ci.runner: missing required key(s) arch"),
    ("self-hosted", "runner.environment", "self-hosted",
     "ci.runner.environment: 'self-hosted', not github-hosted: a self-hosted runner is the author's own machine, "
     "and its receipt is worth what `lean-pkg receipt local` writes"),
    ("runner-os-empty", "runner.os", "", "ci.runner.os: must be one non-empty line"),
    ("runner-image-number", "runner.image", 24, "ci.runner.image: must be one line of text or null"),
    ("verify-log-mismatch", "verify_log_sha256", "c" * 64,
     "ci.verify_log_sha256: cccccccccccccccc... is not the receipt's report_sha256"),
    ("verify-log-malformed", "verify_log_sha256", "C" * 64,
     "ci.verify_log_sha256: must be 64 lowercase hex"),
    ("attestation-path", "attestation", "../x/bundle.json", "ci.attestation: must be a file name"),
]


@pytest.mark.parametrize("mode", ["--check", "gate"])
@pytest.mark.parametrize("key, value, needle", [r[1:] for r in REFUSALS], ids=[r[0] for r in REFUSALS])
def test_each_level_1_rule_refuses_in_gate_and_gate_check(lp, env, tmp_path, capsys, key, value, needle, mode):
    _rec, p = gha_receipt(env, tmp_path, lambda ci: _set(ci, key, value))
    before = env.registry.read_bytes()
    rc, _out, err = gate(lp, env, capsys, p, *(["--check"] if mode == "--check" else []))
    assert rc == 1 and "ci block (a GitHub Actions run, level 1" in err and needle in err, err
    assert env.registry.read_bytes() == before


@pytest.mark.parametrize("mode", ["--check", "gate"])
@pytest.mark.parametrize("value", ["x", ["provider"], None, 1])
def test_a_block_that_is_not_a_mapping_refuses(lp, env, tmp_path, capsys, value, mode):
    rec, p = gha_receipt(env, tmp_path)
    rec["ci"] = value
    p.write_text(json.dumps(rec))
    rc, _out, err = gate(lp, env, capsys, p, *(["--check"] if mode == "--check" else []))
    assert rc == 1 and "ci: must be a mapping (the GitHub Actions run block)" in err, err


def test_a_gha_receipt_without_the_block_still_gates(lp, env, tmp_path, capsys):
    _rec, p = gha_receipt(env, tmp_path, with_ci=False, job="gha-1-1")
    rc, out, err = gate(lp, env, capsys, p, "--check")
    assert rc == 0, err
    assert "· job_id gha-1-1 has no ci block: the receipt gates, but backs no GitHub Actions compute job" in out


def test_a_slurm_receipt_is_unchanged(lp, env, tmp_path, capsys):
    rec = make_receipt(env.repo_a)
    write_report_log(tmp_path, rec)
    p = tmp_path / "receipt.json"
    p.write_text(json.dumps(rec))
    rc, out, err = gate(lp, env, capsys, p, "--check")
    assert rc == 0 and "ci:" not in out and "ci block" not in out, err


# --------------------------------------------------------------------------
# coverage.yaml compute and the page

def gha_page(env, *, compute: list | None = None, with_ci: bool = True, edit=None, job: str = GHA) -> str:
    """beta made page-ready (test_page.prepare) with its workflow file committed at X and the gate receipt
    from a GitHub Actions run of X; `compute` is coverage.yaml's (default: github-actions [job])."""
    doc = copy.deepcopy(COVERAGE)
    doc["compute"] = [{"cluster": "github-actions", "jobs": [job]}] if compute is None else compute
    if not doc["compute"]:
        del doc["compute"]
    x = prepare(env, doc, job=job, extra={WORKFLOW_REL: WORKFLOW})
    rel = f".lean-receipts/{x[:12]}-{job}.json"
    path = env.repo_b / rel
    rec = json.loads(path.read_text())
    rec["schema"] = "lower-receipt/1"
    if with_ci:
        rec["ci"] = ci_block(rec)
        if edit is not None:
            edit(rec["ci"])
    path.write_text(json.dumps(rec, indent=2))
    _git(env.repo_b, "add", "-f", rel)
    _git(env.repo_b, "commit", "-q", "-m", "ci block")
    return x


def coverage_check(lp, env, capsys):
    rc, out, err = run(lp, env, capsys, "--json", "coverage", "check", "beta")
    return rc, json.loads(out)


def test_a_valid_receipt_backs_the_job_and_the_page_carries_no_sentence_at_level_1(lp, env, capsys):
    gha_page(env)
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 0 and data["errors"] == [], data
    rc, a = page_json(lp, env, capsys)
    assert rc == 0, a
    assert a["sites"]["github-actions"] == {"backed": True, "jobs": [GHA], "level": 1, "levels": {GHA: 1},
                                            "sentence": False}
    assert a["sites"]["easley"] == {"backed": False, "jobs": [], "level": None, "levels": {}, "sentence": False}
    assert "github-actions" not in a and a["easley"] == {"backed": False, "jobs": []}   # no new top-level key
    assert "GitHub Actions" not in a["markdown"] and "{runs}" not in a["markdown"]
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert page_check(lp, env, capsys) == (0, [])


def test_a_readme_carrying_the_sentence_fails_check_and_publish_at_level_1(lp, env, capsys):
    from .test_page import publish
    gha_page(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text() + "\n## Funding\n\n" + SENTENCE + "\n")
    rc, problems = page_check(lp, env, capsys)
    want = ("README.md acknowledges GitHub Actions, but its GitHub Actions jobs are corroborated to level 1 only, "
            "and its sentence needs level 2, a corroborated run (`--corroborate`")
    assert rc == 1 and len(problems) == 1 and problems[0].startswith(want), problems
    data, line = publish(lp, env, capsys)
    assert line[0]["status"] == "fail" and data["page"]["status"] == "fail"
    assert any(p.startswith(want) for p in data["page"]["problems"]), data["page"]


def test_an_unbacked_sentence_names_the_backing_and_the_level(lp, env, capsys):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text() + "\n" + SENTENCE + "\n")
    rc, problems = page_check(lp, env, capsys)
    assert rc == 1 and problems == [
        "README.md acknowledges GitHub Actions, but no GitHub Actions receipt backs it (coverage.yaml compute: "
        "[{cluster: github-actions, jobs: [gha-R-A]}] with each job's receipt committed); its sentence renders "
        "only from a corroborated run (level 2)"]


def test_a_ci_badge_and_prose_about_ci_are_not_a_claim(lp, env, capsys):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    badge = ("[![CI](https://github.com/example/beta/actions/workflows/ci.yml/badge.svg)]"
             "(https://github.com/example/beta/actions/workflows/ci.yml)")
    readme.write_text(badge + "\n\nTests run on GitHub Actions.\n\n" + readme.read_text())
    assert page_check(lp, env, capsys) == (0, [])


def test_a_listed_job_without_a_receipt_is_refused(lp, env, capsys):
    gha_page(env, compute=[{"cluster": "github-actions", "jobs": ["gha-999-1"]}])
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 1 and any("compute (github-actions): no GitHub Actions receipt tracked at HEAD under "
                           ".lean-receipts/ has job_id gha-999-1" in e for e in data["errors"]), data


def test_a_gha_receipt_without_the_block_backs_nothing_and_is_named(lp, env, capsys):
    gha_page(env, with_ci=False)
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 1 and any(f"has job_id {GHA} but no ci block, so it backs no GitHub Actions job" in e
                           for e in data["errors"]), data
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "no ci block" in err


def test_a_failing_block_is_named_with_its_reason(lp, env, capsys):
    gha_page(env, edit=lambda ci: ci.update(event="pull_request"))
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 1 and any(f"has job_id {GHA} but its ci block fails: ci.event: 'pull_request' is not accepted"
                           in e for e in data["errors"]), data


@pytest.mark.parametrize("cluster, job, needle", [
    ("github-actions", "slurm-12345", "compute[0].jobs: must be a non-empty list of GitHub Actions job ids "
                                      "(gha-123456789-1)"),
    ("easley", GHA, "compute[0].jobs: must be a non-empty list of Slurm job ids (slurm-12345)"),
])
def test_a_job_id_of_the_other_scheduler_is_refused(lp, env, capsys, cluster, job, needle):
    gha_page(env, compute=[])
    doc = copy.deepcopy(COVERAGE)
    doc["compute"] = [{"cluster": cluster, "jobs": [job]}]
    from .test_page import write_coverage
    write_coverage(env, doc)
    rc, data = coverage_check(lp, env, capsys)
    assert rc == 1 and any(needle in e for e in data["errors"]), data


# --------------------------------------------------------------------------
# receipt check: the ci row

def test_receipt_check_shows_the_ci_rows(lp, env, capsys, tmp_path):
    x = gha_page(env)
    clone = referee_clone(env, tmp_path)
    _rc, payload = receipt_check(lp, capsys, clone, x)
    rel = f".lean-receipts/{x[:12]}-{GHA}.json"
    v = verdicts(payload)
    assert v[f"lower {rel}"] == "pass" and v[f"lower {rel}: ci"] == "pass"
    assert v[f"lower {rel}: ci workflow file"] == "pass"
    row = next(c for c in payload["checks"] if c["id"] == f"lower {rel}: ci")
    assert row["detail"].startswith("GitHub Actions run 123456789 attempt 1 of example/beta") \
        and "level 1, offline" in row["detail"]


def test_receipt_check_skips_a_gha_receipt_without_the_block(lp, env, capsys, tmp_path):
    x = gha_page(env, with_ci=False, compute=[])
    clone = referee_clone(env, tmp_path)
    _rc, payload = receipt_check(lp, capsys, clone, x)
    rel = f".lean-receipts/{x[:12]}-{GHA}.json"
    assert verdicts(payload)[f"lower {rel}: ci"] == "skip"


def test_receipt_check_fails_a_failing_block(lp, env, capsys, tmp_path):
    x = gha_page(env, compute=[], edit=lambda ci: ci["runner"].update(environment="self-hosted"))
    clone = referee_clone(env, tmp_path)
    rc, payload = receipt_check(lp, capsys, clone, x)
    rel = f".lean-receipts/{x[:12]}-{GHA}.json"
    row = next(c for c in payload["checks"] if c["id"] == f"lower {rel}")
    assert rc == 1 and row["verdict"] == "fail" and "receipt local" in row["detail"]
