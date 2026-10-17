#!/usr/bin/env python3
"""`lean-pkg receipt check --repo DIR --commit X`: a referee validates a published package from git alone,
with no registry, no network and no Lean. The author side (registry, gate inputs, `page --write`) is built
with a throwaway registry; the referee side runs on a fresh clone with no registry at all. Negative
controls: an edited log, a receipt bound to another commit, a build-input change after the gate, a missing
comparator configuration, an edited page."""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import subprocess

import pytest
import yaml

from .conftest import CLI, MATHLIB_MANIFEST, MIT_TEXT, _git, make_comparator_receipt, make_receipt, read_reg, \
    repo_head, subprocess_env, write_reg
from .test_page import CITATION, COVERAGE

JOB = "slurm-12345"


def publish_fixture(lp, env, capsys) -> str:
    """beta as its author publishes it: page inputs committed (X), then the lower-tier receipt with its
    redacted log and the comparator receipt with its log in an evidence-only descendant, then the README
    page block rendered by `page --write` from the author's registry and committed (a docs-only
    descendant). Returns X."""
    repo = env.repo_b
    shutil.rmtree(repo / ".lean-receipts")
    (repo / ".lean-receipts").mkdir()
    files = {"coverage.yaml": yaml.safe_dump(COVERAGE, sort_keys=False), "CITATION.cff": CITATION,
             "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST}
    for rel, text in files.items():
        (repo / rel).write_text(text)
    _git(repo, "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", "page inputs")
    x = repo_head(repo)
    log = b"commit: " + x.encode() + b"\nBuild completed successfully\nsorry-count: 0\n"
    (repo / f".lean-receipts/{x[:12]}-{JOB}-verify.redacted.log").write_bytes(log)
    lower = f".lean-receipts/{x[:12]}-{JOB}.json"
    rec = make_receipt(repo, job_id=JOB, report_sha256=hashlib.sha256(log).hexdigest(),
                       manifest_sha256=hashlib.sha256(MATHLIB_MANIFEST.encode()).hexdigest())
    (repo / lower).write_text(json.dumps(rec, indent=2) + "\n")
    comp = f".lean-receipts/{x[:12]}-comparator.json"
    (repo / comp).write_text(json.dumps(make_comparator_receipt(repo), indent=2) + "\n")
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence")
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(receipt=lower, comparator=comp)
    reg["packages"]["beta"].update(license_spdx="MIT", docs_descendants=True)
    write_reg(env, reg)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "page", "beta", "--write"]) == 0
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "verification page")
    capsys.readouterr()
    return x


def referee_clone(env, tmp_path):
    """A fresh clone, as an outside referee makes it."""
    dest = tmp_path / "referee" / "beta"
    dest.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(env.repo_b), str(dest)], check=True, capture_output=True)
    return dest


def check(lp, capsys, repo, x, *argv):
    capsys.readouterr()
    rc = lp.main(["--json", "receipt", "check", "--repo", str(repo), "--commit", x, *argv])
    out = capsys.readouterr().out
    return rc, json.loads(out)


def verdicts(payload) -> dict:
    return {c["id"]: c["verdict"] for c in payload["checks"]}


def failed(payload) -> list[str]:
    return [f"{c['id']}: {c['detail']}" for c in payload["checks"] if c["verdict"] == "fail"]


@pytest.fixture()
def no_network(monkeypatch):
    def refuse(*_a, **_k):
        raise AssertionError("receipt check opened a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


# --- the referee's route ------------------------------------------------------------------------------

def test_end_to_end_on_a_fresh_clone_with_no_registry(lp, env, capsys, tmp_path):
    """The command as a referee runs it: a separate process, a fresh clone, HOME and the registry path
    pointing at nothing; exit 0, every check named, and no registry file appears."""
    x = publish_fixture(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    home = tmp_path / "referee-home"
    home.mkdir()
    absent = tmp_path / "no-registry" / "lean-packages.yaml"
    run_env = subprocess_env({"HOME": str(home), "LEAN_PKG_REGISTRY": str(absent)})
    for k in ("LEAN_PKG_EVENTS", "LEAN_PKG_COMMIT", "LEAN_PKG_PUSH", "LEAN_PKG_INDEX", "LEAN_PKG_HOME"):
        run_env.pop(k, None)
    before = subprocess.run(["git", "-C", str(clone), "status", "--porcelain"], capture_output=True, text=True).stdout
    p = subprocess.run([*CLI, "receipt", "check", "--repo", str(clone), "--commit", x, "--docs-descendants"],
                       capture_output=True, text=True, env=run_env, cwd=str(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    lines = p.stdout.splitlines()
    assert lines[0].startswith(f"receipt check: PASS beta at {x[:12]} (tier comparator") and lines[-1] == lines[0]
    assert "never runs Lean and never uses the network" in p.stdout
    assert "docs-only accepted by the referee: --docs-descendants" in p.stdout
    for cid in ("receipts", "binding", f"lower .lean-receipts/{x[:12]}-{JOB}.json",
                f"lower .lean-receipts/{x[:12]}-{JOB}.json: report log", "claims",
                f"comparator .lean-receipts/{x[:12]}-comparator.json", "claimed theorems", "coverage",
                "page: commit", "page: pins", "page: evidence", "page: trust boundary"):
        assert f"PASS {cid}: " in p.stdout, cid
    assert not absent.exists() and not absent.parent.exists() and not any(home.iterdir())
    assert subprocess.run(["git", "-C", str(clone), "status", "--porcelain"], capture_output=True,
                          text=True).stdout == before   # the referee's checkout is never touched


def test_json_names_every_check_and_the_rule(lp, env, capsys, tmp_path, no_network):
    x = publish_fixture(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone, x, "--docs-descendants")
    assert rc == 0 and a["ok"] is True, failed(a)
    assert a["tier"] == "comparator" and a["runs_lean"] is False and a["network"] is False
    assert a["docs_descendants"] is True and a["commit"] == x and a["at"] == repo_head(clone)
    assert [r["path"] for r in a["receipts"]["lower"]] == [f".lean-receipts/{x[:12]}-{JOB}.json"]
    assert a["counts"]["fail"] == 0 and all(set(c) == {"id", "verdict", "detail"} for c in a["checks"])
    assert verdicts(a)[f"lower .lean-receipts/{x[:12]}-{JOB}.json: evidence files"] == "pass"
    assert verdicts(a)[f"comparator .lean-receipts/{x[:12]}-comparator.json: olean manifest"] == "skip"


def test_without_the_docs_opt_in_the_page_commit_fails_the_binding(lp, env, capsys, tmp_path):
    """The README commit after the evidence is a docs-only descendant: accepted only when the referee says so."""
    x = publish_fixture(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone, x)
    assert rc == 1 and verdicts(a)["binding"] == "fail"
    assert "docs-only not accepted" in a["rule"]
    detail = next(c["detail"] for c in a["checks"] if c["id"] == "binding")
    assert "README.md" in detail
    # the evidence commit itself (before the page) is an evidence-only descendant: no opt-in needed
    rc, a = check(lp, capsys, clone, x, "--at", "HEAD~1")
    assert verdicts(a)["binding"] == "pass" and verdicts(a)["page"] == "skip" and rc == 0, failed(a)


def test_a_subdirectory_checkout_path_is_read_as_the_package(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone / "Demo", x, "--docs-descendants")
    assert rc == 1 and a["prefix"] == "Demo" and verdicts(a)["receipts"] == "fail"   # no receipts under Demo/


# --- negative controls -------------------------------------------------------------------------------

def test_an_edited_log_fails(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    comp = json.loads((repo / f".lean-receipts/{x[:12]}-comparator.json").read_text())
    log = repo / comp["configs"][0]["log"]
    log.write_text(log.read_text() + "edited\n")
    red = repo / f".lean-receipts/{x[:12]}-{JOB}-verify.redacted.log"
    red.write_text(red.read_text() + "edited\n")
    _git(repo, "commit", "-qam", "edit the logs")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    v = verdicts(a)
    assert rc == 1 and v[f"comparator .lean-receipts/{x[:12]}-comparator.json"] == "fail"
    assert v[f"lower .lean-receipts/{x[:12]}-{JOB}.json: report log"] == "fail"
    text = " ".join(failed(a))
    assert "log_sha256 mismatch" in text and "report_sha256 mismatch" in text
    assert a["tier"] is None


def test_a_receipt_bound_to_another_commit_fails(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    rel = repo / f".lean-receipts/{x[:12]}-{JOB}.json"
    data = json.loads(rel.read_text())
    data["commit"] = repo_head(repo)      # names the page commit, not X
    rel.write_text(json.dumps(data, indent=2) + "\n")
    _git(repo, "commit", "-qam", "rebind the receipt")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    v = verdicts(a)
    assert rc == 1 and v["receipts"] == "fail" and v[f"receipt .lean-receipts/{x[:12]}-{JOB}.json"] == "fail"
    assert "named for" in " ".join(failed(a))


def test_a_commit_the_receipts_do_not_bind_fails(lp, env, capsys, tmp_path):
    publish_fixture(lp, env, capsys)
    clone = referee_clone(env, tmp_path)
    other = _git(clone, "rev-parse", "HEAD~3").stdout.strip()
    rc, a = check(lp, capsys, clone, other, "--docs-descendants")
    assert rc == 1 and verdicts(a)["receipts"] == "fail" and "bind other commits" in failed(a)[0]


def test_a_build_input_change_after_the_gate_fails(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    (repo / "Demo" / "Main.lean").write_text((repo / "Demo" / "Main.lean").read_text() + "\n-- later\n")
    _git(repo, "commit", "-qam", "change a source file")
    clone = referee_clone(env, tmp_path)
    rc, a = check(lp, capsys, clone, x, "--docs-descendants")
    v = verdicts(a)
    assert rc == 1 and v["binding"] == "fail" and v[f"lower .lean-receipts/{x[:12]}-{JOB}.json"] == "fail"
    assert "Demo/Main.lean" in " ".join(failed(a))
    # the tree the receipts bind still checks: the published page commit before the change
    rc, a = check(lp, capsys, clone, x, "--docs-descendants", "--at", "HEAD~1")
    assert rc == 0, failed(a)


def test_a_missing_comparator_config_fails(lp, env, capsys, tmp_path):
    """The comparator receipt names a configuration that is not tracked at the bound commit."""
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    rel = repo / f".lean-receipts/{x[:12]}-comparator.json"
    data = json.loads(rel.read_text())
    extra = dict(data["configs"][0], path="comparator/config-extra.json")
    data["configs"].append(extra)
    rel.write_text(json.dumps(data, indent=2) + "\n")
    _git(repo, "commit", "-qam", "a config the commit does not track")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    assert rc == 1 and verdicts(a)[f"comparator .lean-receipts/{x[:12]}-comparator.json"] == "fail"
    assert "comparator/config-extra.json" in " ".join(failed(a))
    assert a["tier"] == "lower-tier"


def test_a_removed_comparator_config_at_the_commit_fails(lp, env, capsys, tmp_path):
    """A receipt bound to a commit that no longer tracks the configuration it names."""
    repo = env.repo_b
    x = publish_fixture(lp, env, capsys)
    _git(repo, "rm", "-q", "comparator.json")
    _git(repo, "commit", "-qm", "drop the comparator config")
    y = repo_head(repo)
    for rel in (f".lean-receipts/{x[:12]}-{JOB}.json", f".lean-receipts/{x[:12]}-comparator.json"):
        data = json.loads((repo / rel).read_text())
        data["commit"] = y
        (repo / rel.replace(x[:12], y[:12])).write_text(json.dumps(data, indent=2) + "\n")
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-qm", "evidence for y")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), y)
    assert rc == 1 and verdicts(a)[f"comparator .lean-receipts/{y[:12]}-comparator.json"] == "fail"
    assert "missing [], extra/duplicate ['comparator.json']" in " ".join(failed(a))


def test_an_edited_page_fails(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    readme = repo / "README.md"
    text = readme.read_text()
    sha = hashlib.sha256((repo / f".lean-receipts/{x[:12]}-{JOB}.json").read_bytes()).hexdigest()
    assert sha in text
    readme.write_text(text.replace(sha, "0" * 64).replace("`sorry` count 0", "`sorry` count 1"))
    _git(repo, "commit", "-qam", "edit the page")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    v = verdicts(a)
    assert rc == 1 and v["page: evidence"] == "fail" and v["page: trust boundary"] == "fail"
    assert v["page: commit"] == "pass" and v["page: pins"] == "pass"


def test_usage_errors_exit_2(lp, env, capsys, tmp_path):
    x = publish_fixture(lp, env, capsys)
    assert lp.main(["receipt", "check", "--repo", str(tmp_path / "nowhere"), "--commit", x]) == 2
    assert lp.main(["receipt", "check", "--repo", str(env.repo_b), "--commit", "f" * 40]) == 2
    assert lp.main(["receipt", "check", "--repo", str(env.repo_b), "--commit", x, "--at", "no-such-ref"]) == 2


def test_a_page_in_the_wording_before_0_11_is_still_checked(lp, env, capsys, tmp_path):
    """A page published with an earlier lean-pkg says "Lower-tier gate receipt", "(recorded) | comparator" and
    "the commit the gate receipt binds"; receipt check reads that wording too (and still catches an edit)."""
    import re
    x = publish_fixture(lp, env, capsys)
    repo = env.repo_b
    readme = repo / "README.md"
    text = readme.read_text()
    old = (text.replace("# the commit the build check binds", "# the commit the gate receipt binds")
           .replace(" (used here) | statement comparison |", " (recorded) | comparator |")
           .replace(" (used here) | build |", " (recorded) | lower tier |"))
    old = re.sub(r"^- Build check \(`lean-pkg gate`\) at commit `([0-9a-f]{12})`: `sorry` count (\d+); axioms used: "
                 r"(.*)\.$", r"- Lower-tier gate receipt at `\1` records: `sorry` count \2 after the build; axioms "
                 r"used: \3; allowed: `propext`.", old, flags=re.M)
    old = re.sub(r"^- Statement comparison \(comparator\) at commit `([0-9a-f]{12})`: ",
                 r"- Comparator receipt at `\1`: ", old, flags=re.M)
    assert "Lower-tier gate receipt at" in old and "(recorded) | lower tier |" in old and old != text
    readme.write_text(old)
    _git(repo, "commit", "-qam", "a page in the earlier wording")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path), x, "--docs-descendants")
    v = verdicts(a)
    assert rc == 0, failed(a)
    assert all(v[k] == "pass" for k in ("page: commit", "page: pins", "page: evidence", "page: trust boundary"))
    readme.write_text(old.replace("`sorry` count 0 after", "`sorry` count 1 after"))
    _git(repo, "commit", "-qam", "edit it")
    rc, a = check(lp, capsys, referee_clone(env, tmp_path / "again"), x, "--docs-descendants")
    assert rc == 1 and verdicts(a)["page: trust boundary"] == "fail"
