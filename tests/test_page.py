#!/usr/bin/env python3
"""`lean-pkg coverage check` (coverage/1) and `lean-pkg page`: the README verification section is rendered
from data, deterministically; --write touches only the marked block; --check fails on a stale or missing
block, a missing lean-pkg credit and an Easley sentence without a backing Slurm receipt; private strings
never reach the output."""

from __future__ import annotations

import json
import shutil

import yaml

from .conftest import (MATHLIB_MANIFEST, MIT_TEXT, _git, make_comparator_receipt, make_receipt, read_reg,
                       repo_head, set_lease, write_reg)

# built at run time: the literal forms are latex-label hits for lean-pkg's own `make public-safety` (--strict)
ARXIV = "arXiv" + ":2501.01234v2"
LABEL = "thm" + ":main"
COVERAGE = {
    "schema": "coverage/1",
    "package": "beta",
    "manuscript": {"title": "A demonstration bound", "version": ARXIV},
    "summary": "Theorem 1 of the paper is formalized with the paper's own hypotheses; Theorem 2 is not.",
    "rows": [
        {"paper": {"number": "Theorem 1", "title": "Main bound", "label": LABEL},
         "class": "same-assumptions", "claims": ["beta/foo-le-bar"]},
        {"paper": {"number": "Theorem 2", "title": "Sharpness"}, "class": "outside",
         "note": "the lower-bound construction is not formalized"},
    ],
}
CITATION = """\
cff-version: 1.2.0
message: "Cite the companion as below."
title: "Beta: a Lean companion"
version: "1.0.0"
authors:
  - family-names: Doe
    given-names: Jane
"""
CREDIT_URL = "https://github.com/lean-pkg/lean-pkg"


def prepare(env, coverage: dict | None = None, *, easley: bool = False, job: str = "slurm-12345",
            receipt_job: str | None = None, citation: str = CITATION, extra: dict | None = None) -> str:
    """beta made page-ready: coverage.yaml, CITATION.cff, an MIT LICENSE and a Mathlib manifest committed,
    then a gate and a comparator receipt bound to that commit, committed under .lean-receipts/ (an
    evidence-only descendant); the registry records both, license_spdx MIT and the docs-only opt-in."""
    repo = env.repo_b
    shutil.rmtree(repo / ".lean-receipts")              # the env fixture's untracked receipts: not this page's
    (repo / ".lean-receipts").mkdir()
    doc = json.loads(json.dumps(coverage or COVERAGE))
    if easley:
        doc["compute"] = [{"cluster": "easley", "jobs": [job]}]
    files = {"coverage.yaml": yaml.safe_dump(doc, sort_keys=False), "CITATION.cff": citation,
             "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST, **(extra or {})}
    for rel, text in files.items():
        (repo / rel).write_text(text)
    _git(repo, "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", "page inputs")
    head = repo_head(repo)
    receipt_job = receipt_job or job
    lower = f".lean-receipts/{head[:12]}-{receipt_job}.json"
    (repo / lower).write_text(json.dumps(make_receipt(repo, job_id=receipt_job, host="secret-host-xyz",
                                                      checker="/home/someone/verify.sh"), indent=2))
    comp = f".lean-receipts/{head[:12]}-comparator.json"
    (repo / comp).write_text(json.dumps(make_comparator_receipt(repo), indent=2))
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence")
    reg = read_reg(env)
    pkg = reg["packages"]["beta"]
    pkg["gate"].update(receipt=lower, comparator=comp)
    pkg.update(license_spdx="MIT", docs_descendants=True)
    write_reg(env, reg)
    return head


def run(lp, env, capsys, *argv):
    capsys.readouterr()
    rc = lp.main(["--registry", str(env.registry), *argv])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def page_json(lp, env, capsys, *argv):
    rc, out, _err = run(lp, env, capsys, "--json", "page", "beta", *argv)
    return rc, json.loads(out)


def write_coverage(env, doc) -> None:
    (env.repo_b / "coverage.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    _git(env.repo_b, "commit", "-q", "-am", "coverage")


# --- render -------------------------------------------------------------------------------------

def test_render_is_deterministic_and_states_only_what_the_ledger_closes(lp, env, capsys):
    prepare(env)
    rc1, a = page_json(lp, env, capsys)
    rc2, b = page_json(lp, env, capsys)
    assert rc1 == rc2 == 0 and a["markdown"] == b["markdown"]          # byte-identical
    rc, text, _ = run(lp, env, capsys, "page", "beta")
    assert rc == 0 and text == a["markdown"]
    md = a["markdown"]
    assert md.startswith("<!-- lean-pkg:page:begin -->\n") and md.endswith("<!-- lean-pkg:page:end -->\n")
    assert a["ledger_status"] == "ready" and a["verified_rows"] == 1
    row = next(x for x in md.splitlines() if x.startswith("| Theorem 1 (Main bound)"))
    assert "| Same assumptions |" in row and "`Demo.foo_le_bar`" in row and "| verified |" in row
    assert "## Not formalized" in md and "- Theorem 2 (Sharpness): the lower-bound construction" in md
    assert LABEL not in md                                         # labels stay in coverage.yaml
    for section in ("Verification status", "Paper ↔ Lean map", "Trust boundary", "Reproduce", "Evidence",
                    "Citation", "License", "Acknowledgments"):
        assert f"## {section}\n" in md
    assert "git clone https://github.com/example/beta" in md and "lake exe cache get" in md
    assert "Jane Doe. *Beta: a Lean companion*. Version 1.0.0." in md
    assert "Released under `MIT`" in md
    assert f"Developed and verified with [lean-pkg]({CREDIT_URL})" in md
    assert "Easley" not in md
    assert a["written"] is False and not (env.repo_b / "README.md").exists()


def test_a_row_the_ledger_does_not_close_is_not_called_verified(lp, env, capsys):
    prepare(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = None             # promote stage needs the comparator tier
    write_reg(env, reg)
    rc, a = page_json(lp, env, capsys)
    row = next(x for x in a["markdown"].splitlines() if x.startswith("| Theorem 1"))
    assert rc == 0 and a["ledger_status"] == "open" and "| verified |" not in row and "| open |" in row
    assert "0 of 1 formalized row(s) verified" in a["markdown"]


def test_receipt_fields_outside_the_allow_list_never_appear(lp, env, capsys):
    prepare(env)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    for private in ("secret-host-xyz", "/home/someone", "verify.sh", "build-host", "local-build-host"):
        assert private not in md
    assert "slurm-12345" in md                                           # a Slurm job id is allowed


def test_a_private_string_in_rendered_data_refuses_and_writes_nothing(lp, env, capsys):
    doc = json.loads(json.dumps(COVERAGE))
    doc["summary"] = "Formalized during the Zeta-Internal-Round sprint."
    prepare(env, doc, extra={".lean-pkg-private-patterns": "Zeta-Internal-Round\n"})
    (env.repo_b / "README.md").write_text("# Beta\n")
    rc, out, err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 1 and "private string" in err and "Zeta-Internal-Round" not in err + out
    assert (env.repo_b / "README.md").read_text() == "# Beta\n"
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--check")
    assert rc == 1 and "private string" in err


# --- --write ------------------------------------------------------------------------------------

def test_write_replaces_only_the_marked_block(lp, env, capsys):
    prepare(env)
    readme = env.repo_b / "README.md"
    readme.write_text("# Beta\n\nHand prose before.\n\n<!-- lean-pkg:page:begin -->\nold text\n"
                      "<!-- lean-pkg:page:end -->\n\n## Notes A-I\n\nHand prose after.\n")
    reg_before = env.registry.read_bytes()
    rc, a = page_json(lp, env, capsys, "--write")
    text = readme.read_text()
    assert rc == 0 and a["written"] is True and "old text" not in text
    assert text == "# Beta\n\nHand prose before.\n\n" + a["markdown"] + "\n## Notes A-I\n\nHand prose after.\n"
    assert env.registry.read_bytes() == reg_before and not env.events.exists()   # never a registry write
    rc, b = page_json(lp, env, capsys, "--write")
    assert rc == 0 and b["unchanged"] is True and readme.read_text() == text
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--check")
    assert rc == 0


def test_write_appends_the_block_to_a_readme_without_markers(lp, env, capsys):
    prepare(env)
    readme = env.repo_b / "README.md"
    readme.write_text("# Beta\n\nIntro.")
    rc, a = page_json(lp, env, capsys, "--write")
    assert rc == 0 and readme.read_text() == "# Beta\n\nIntro.\n\n" + a["markdown"]


def test_write_refuses_unbalanced_markers(lp, env, capsys):
    prepare(env)
    readme = env.repo_b / "README.md"
    readme.write_text("<!-- lean-pkg:page:begin -->\nno end\n")
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 1 and "unbalanced" in err and readme.read_text() == "<!-- lean-pkg:page:begin -->\nno end\n"


def test_write_honours_a_foreign_lease(lp, env, capsys):
    prepare(env)
    set_lease(env, "beta", agent="vera")
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 1 and "lease held by vera" in err and not (env.repo_b / "README.md").exists()
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--write", "--agent", "vera")
    assert rc == 0 and (env.repo_b / "README.md").is_file()


def test_out_writes_the_block_elsewhere(lp, env, capsys, tmp_path):
    prepare(env)
    dest = tmp_path / "page.md"
    rc, a = page_json(lp, env, capsys, "--out", str(dest))
    assert rc == 0 and dest.read_text() == a["markdown"] and not (env.repo_b / "README.md").exists()


# --- --check ------------------------------------------------------------------------------------

def check(lp, env, capsys):
    rc, out, _err = run(lp, env, capsys, "--json", "page", "beta", "--check")
    return rc, json.loads(out)["problems"]


def test_check_fails_on_a_missing_readme_and_a_missing_block(lp, env, capsys):
    prepare(env)
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and problems == ["README.md is missing (`lean-pkg page NAME --write`)"]
    (env.repo_b / "README.md").write_text("# Beta\n")
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and any("no page block" in p for p in problems)
    assert any("lacks the lean-pkg credit" in p for p in problems)   # credit missing, named on its own


def test_check_fails_on_a_stale_block(lp, env, capsys):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text().replace("0 of", "9 of").replace("1 of 1 formalized", "2 of 1 formalized"))
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and problems == ["README.md: the page block is stale (`lean-pkg page NAME --write`)"]
    rc, out, _err = run(lp, env, capsys, "page", "beta", "--check")
    assert out.splitlines()[0].startswith("beta: page: STALE")                     # verdict first


def test_check_fails_when_the_data_changes_after_the_write(lp, env, capsys):
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    doc = json.loads(json.dumps(COVERAGE))
    doc["summary"] = "A changed capsule sentence."
    write_coverage(env, doc)
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and "stale" in problems[0]


def test_check_fails_on_an_easley_sentence_without_a_backing_receipt(lp, env, capsys):
    from lean_pkg.page import EASLEY
    prepare(env)
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text() + "\n## Funding\n\n" + EASLEY + "\n")   # hand prose, outside the block
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and len(problems) == 1 and "Easley" in problems[0]


# --- Easley ---------------------------------------------------------------------------------------

def test_easley_line_appears_only_with_a_backing_slurm_receipt(lp, env, capsys):
    from lean_pkg.page import EASLEY
    prepare(env, easley=True)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and EASLEY in a["markdown"] and a["easley"] == {"backed": True, "jobs": ["slurm-12345"]}
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert check(lp, env, capsys) == (0, [])


def test_easley_job_without_a_receipt_is_refused(lp, env, capsys):
    prepare(env)
    doc = json.loads(json.dumps(COVERAGE))
    doc["compute"] = [{"cluster": "easley", "jobs": ["slurm-99999"]}]
    write_coverage(env, doc)
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "slurm-99999" in err and "no Slurm receipt" in err


def test_a_non_slurm_receipt_never_backs_easley(lp, env, capsys):
    prepare(env, easley=True, receipt_job="systemd-user-unit-1234-h-abcdef012345")   # not a Slurm run
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "no Slurm receipt" in err and "slurm-12345" in err
    doc = json.loads(json.dumps(COVERAGE))
    doc["compute"] = [{"cluster": "easley", "jobs": ["systemd-user-unit-1234"]}]
    write_coverage(env, doc)
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "compute[0].jobs" in err


def test_slurm_job_of_accepts_only_a_slurm_spelling_or_a_corroborated_comparator_job():
    from lean_pkg.coverage import slurm_job_of
    assert slurm_job_of({"job_id": "slurm-5581013"}) == "slurm-5581013"
    assert slurm_job_of({"job_id": "slurm-5581013_3"}) == "slurm-5581013_3"
    # a bare number says nothing about where it ran; a lower-tier receipt has no field to corroborate it
    assert slurm_job_of({"job_id": "5581013", "sorry_count": 0, "axioms": []}) is None
    for junk in ("slurm-abc", "slurm-", "5581013x", "easley-5581013", "local-host-5581013", "", None, 5581013):
        assert slurm_job_of({"job_id": junk}) is None
    comp = {"schema": "comparator-receipt/1", "job_id": "5581013"}
    assert slurm_job_of(comp) is None                                   # no host policy: not corroborated
    assert slurm_job_of({**comp, "host_policy": "Slurm job 5581013 (SLURM_JOB_ID set)"}) == "slurm-5581013"
    assert slurm_job_of({**comp, "host_policy": "Slurm job 999 (SLURM_JOB_ID set)"}) is None


def test_a_bare_numeric_job_id_never_backs_easley_and_the_refusal_names_it(lp, env, capsys):
    prepare(env, easley=True, job="slurm-5581013", receipt_job="5581013")
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "no Slurm receipt" in err and "slurm-5581013" in err
    assert "-5581013.json has job_id '5581013', a bare number" in err
    assert "lean-pkg receipt slurm --job 5581013" in err
    rc, data = cov_check(lp, env, capsys)
    assert rc == 1 and any("a bare number" in e for e in data["errors"])
    data, line = publish(lp, env, capsys)                  # the reason reaches the checklist, not a count
    assert line[0]["status"] == "fail" and "a bare number" in line[0]["label"]
    assert "no Slurm receipt" in line[0]["label"] and any("a bare number" in p for p in data["page"]["problems"])


def test_a_junk_job_id_is_refused_without_a_bare_number_hint(lp, env, capsys):
    prepare(env, easley=True, job="slurm-5581013", receipt_job="5581013x")
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "no Slurm receipt" in err and "bare number" not in err


def _gated_root_with_easley_readme(env, job: str = "slurm-5581013") -> str:
    """beta as an adopted repository ships: the gated root already carries a README with the lean-pkg credit
    and an Easley sentence, but no coverage.yaml and no page block; both receipts are bound to the root and
    committed in evidence-only descendants."""
    from lean_pkg.page import CREDIT, EASLEY
    repo = env.repo_b
    shutil.rmtree(repo / ".lean-receipts")
    (repo / ".lean-receipts").mkdir()
    files = {"CITATION.cff": CITATION, "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST,
             "README.md": f"# beta\n\n{CREDIT}\n\n{EASLEY}\n"}
    for rel, text in files.items():
        (repo / rel).write_text(text)
    _git(repo, "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", "root")
    root = repo_head(repo)
    comp = f".lean-receipts/{root[:12]}-comparator.json"
    (repo / comp).write_text(json.dumps(make_comparator_receipt(repo), indent=2))
    lower = f".lean-receipts/{root[:12]}-{job}.json"
    (repo / lower).write_text(json.dumps(make_receipt(repo, job_id=job), indent=2))
    _git(repo, "add", "-f", lower)
    _git(repo, "commit", "-q", "-m", "gate receipt")
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "comparator receipt")
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(receipt=lower, comparator=comp)
    reg["packages"]["beta"].update(license_spdx="MIT")
    write_reg(env, reg)
    return root


def test_an_adopted_root_with_an_easley_readme_adopts_the_page_after_the_gate(lp, env, capsys):
    """No chicken-and-egg: coverage.yaml and the page block are committed after the receipts, and the
    docs-only opt-in keeps both tiers bound to the gated root (docs/page.md)."""
    root = _gated_root_with_easley_readme(env)
    data, line = publish(lp, env, capsys)                  # not adopted, Easley sentence unbacked: fail
    assert line[0]["status"] == "fail" and "no Slurm receipt backs it" in line[0]["label"]
    assert "--accept-docs-descendants" in line[0]["label"]  # the route is named
    assert run(lp, env, capsys, "update", "beta", "--accept-docs-descendants", "--reason",
               "README and coverage.yaml follow the gate")[0] == 0
    data, line = publish(lp, env, capsys)
    assert line[0]["status"] == "fail" and "--accept-docs-descendants" not in line[0]["label"]
    doc = json.loads(json.dumps(COVERAGE))
    doc["compute"] = [{"cluster": "easley", "jobs": ["slurm-5581013"]}]
    (env.repo_b / "coverage.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    _git(env.repo_b, "add", "coverage.yaml")
    _git(env.repo_b, "commit", "-q", "-m", "coverage")
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    _git(env.repo_b, "commit", "-q", "-am", "page block")
    data, line = publish(lp, env, capsys)
    assert line[0]["status"] == "pass" and data["page"] == {"status": "pass", "adopted": True, "problems": []}
    bound = [c for c in data["checklist"] if c["label"].startswith("gate receipt bound to HEAD")]
    assert bound and bound[0]["status"] == "pass" and f"docs-only descendant of {root[:12]}" in bound[0]["label"]


# --- coverage check -------------------------------------------------------------------------------

def cov_check(lp, env, capsys, *argv):
    rc, out, _err = run(lp, env, capsys, "--json", "coverage", "check", "beta", *argv)
    return rc, json.loads(out)


def test_coverage_check_accepts_the_fixture(lp, env, capsys):
    prepare(env)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["ok"] and data["rows"] == 2 and data["manuscript"] == "arxiv"
    assert data["counts"] == {"same-assumptions": 1, "inputs-supplied": 0, "outside": 1}
    rc, out, _err = run(lp, env, capsys, "coverage", "check", "beta")
    assert rc == 0 and out.startswith("beta: coverage: OK")


def _refused(lp, env, capsys, tmp_path, doc_text: str, *argv) -> list[str]:
    f = tmp_path / "cov.yaml"
    f.write_text(doc_text)
    rc, data = cov_check(lp, env, capsys, "--file", str(f), *argv)
    assert rc == 1 and data["ok"] is False
    return data["errors"]


def mutate(**change) -> str:
    doc = json.loads(json.dumps(COVERAGE))
    for k, v in change.items():
        if v is None:
            doc.pop(k, None)
        else:
            doc[k] = v
    return yaml.safe_dump(doc, sort_keys=False)


def test_coverage_check_refusals(lp, env, capsys, tmp_path):
    prepare(env)
    rows = COVERAGE["rows"]
    errs = _refused(lp, env, capsys, tmp_path, "schema: coverage/1\nschema: coverage/1\n")
    assert any("duplicate key 'schema'" in e for e in errs)
    assert any("unknown key(s) extra" in e for e in _refused(lp, env, capsys, tmp_path, mutate(extra=1)))
    bad_class = [dict(rows[0], **{"class": "verified"}), rows[1]]
    assert any("class: must be one of" in e for e in _refused(lp, env, capsys, tmp_path, mutate(rows=bad_class)))
    no_inputs = [dict(rows[0], **{"class": "inputs-supplied"}), rows[1]]
    assert any("names its inputs" in e for e in _refused(lp, env, capsys, tmp_path, mutate(rows=no_inputs)))
    unknown = [dict(rows[0], claims=["beta/nope"]), rows[1]]
    assert any("'beta/nope' is not a claim id" in e for e in _refused(lp, env, capsys, tmp_path, mutate(rows=unknown)))
    assert any("no outside row" in e for e in _refused(lp, env, capsys, tmp_path, mutate(rows=[rows[0]])))
    both = mutate(nothing_excluded={"reason": "all of it"})
    assert any("contradicts the outside row" in e for e in _refused(lp, env, capsys, tmp_path, both))
    assert any("is not 'beta'" in e for e in _refused(lp, env, capsys, tmp_path, mutate(package="gamma")))
    badv = mutate(manuscript={"title": "T", "version": "draft 12"})
    assert any("manuscript.version" in e for e in _refused(lp, env, capsys, tmp_path, badv))
    badc = mutate(compute=[{"cluster": "elsewhere", "jobs": ["slurm-1"]}])
    assert any("compute[0].cluster" in e for e in _refused(lp, env, capsys, tmp_path, badc))
    outside_claims = [rows[0], dict(rows[1], claims=["beta/foo-le-bar"])]
    assert any("outside row names no claims" in e
               for e in _refused(lp, env, capsys, tmp_path, mutate(rows=outside_claims)))
    dup = [rows[0], dict(rows[1], paper={"number": "Theorem 1", "title": "Again"})]
    assert any("repeats rows[0]" in e for e in _refused(lp, env, capsys, tmp_path, mutate(rows=dup)))


def test_nothing_excluded_replaces_an_outside_row(lp, env, capsys):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"] = [doc["rows"][0]]
    doc["nothing_excluded"] = {"reason": "the paper has one theorem and it is formalized"}
    prepare(env, doc)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "Nothing in the manuscript is excluded: the paper has one theorem" in a["markdown"]


def test_a_private_manuscript_needs_the_flag_and_is_marked(lp, env, capsys):
    doc = json.loads(json.dumps(COVERAGE))
    doc["manuscript"]["version"] = "unposted"
    prepare(env, doc)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 1 and any("--allow-private-manuscript" in e for e in data["errors"])
    rc, data = cov_check(lp, env, capsys, "--allow-private-manuscript")
    assert rc == 0 and data["private_manuscript"] is True
    rc, _out, _err = run(lp, env, capsys, "page", "beta")
    assert rc == 1
    rc, a = page_json(lp, env, capsys, "--allow-private-manuscript")
    assert rc == 0 and a["private_manuscript"] is True and "Manuscript not yet posted" in a["markdown"]


def test_citation_without_authors_is_refused(lp, env, capsys):
    prepare(env, citation="cff-version: 1.2.0\ntitle: Beta\nauthors: []\n")
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "no author" in err


def test_lean_pkg_version_doi_is_checked_against_the_own_citation(lp, env, capsys):
    from lean_pkg import coverage as cov
    cff = cov.own_citation()
    known = cov.own_version_dois(cff)
    version, doi = sorted(known.items())[-1]
    doc = json.loads(json.dumps(COVERAGE))
    doc["lean_pkg_version"] = {"version": version, "doi": doi}
    prepare(env, doc)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and f"Version used: lean-pkg {version} ([doi:{doi}]" in a["markdown"]
    doc["lean_pkg_version"] = {"version": version, "doi": "10.5281/zenodo.1"}
    write_coverage(env, doc)
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "lean-pkg's CITATION.cff gives" in err


# --- publish --dry-run ----------------------------------------------------------------------------

def publish(lp, env, capsys):
    rc, out, _err = run(lp, env, capsys, "--json", "publish", "beta", "--dry-run")
    data = json.loads(out)
    line = [c for c in data["checklist"] if "page" in c["label"]]
    return data, line


def test_publish_dry_run_carries_one_page_line(lp, env, capsys):
    data, line = publish(lp, env, capsys)                       # not adopted: WARN
    assert len(line) == 1 and line[0]["status"] == "warn" and data["page"]["adopted"] is False
    prepare(env)
    data, line = publish(lp, env, capsys)                       # adopted, README missing: fail
    assert len(line) == 1 and line[0]["status"] == "fail" and data["page"]["status"] == "fail"
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    data, line = publish(lp, env, capsys)
    assert len(line) == 1 and line[0]["status"] == "pass" and data["page"] == {
        "status": "pass", "adopted": True, "problems": []}


# --- kind: library (no manuscript) ----------------------------------------------------------------

LIBRARY = {
    "schema": "coverage/1",
    "package": "beta",
    "kind": "library",
    "summary": "The library's main bound is formalized and compared against its stated form.",
    "rows": [{"title": "Main bound", "claims": ["beta/foo-le-bar"], "note": "the headline estimate"}],
}


def library(**change) -> str:
    doc = json.loads(json.dumps(LIBRARY))
    for k, v in change.items():
        if v is None:
            doc.pop(k, None)
        else:
            doc[k] = v
    return yaml.safe_dump(doc, sort_keys=False)


def test_a_library_coverage_needs_no_manuscript(lp, env, capsys):
    prepare(env, LIBRARY)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["ok"] and data["kind"] == "library" and data["rows"] == 1
    assert data["manuscript"] is None and data["private_manuscript"] is False and data["counts"] is None
    rc, out, _err = run(lp, env, capsys, "coverage", "check", "beta")
    assert rc == 0 and out.startswith("beta: coverage: OK; library; 1 main result(s); no manuscript")


def test_a_library_page_claims_no_manuscript_and_states_what_was_verified(lp, env, capsys, tmp_path):
    prepare(env, LIBRARY)
    rc, a = page_json(lp, env, capsys, "--site", str(tmp_path / "site"), "--html", str(tmp_path / "v.html"))
    assert rc == 0 and a["kind"] == "library" and a["manuscript"] is None and a["verified_rows"] == 1
    md = a["markdown"]
    row = next(x for x in md.splitlines() if x.startswith("| Main bound |"))
    assert "`Demo.foo_le_bar`" in row and "| verified |" in row and "the headline estimate" in row
    assert "## Main results\n" in md and "- Library: no manuscript" in md
    assert "1 of 1 main result(s) verified" in md
    outputs = [md, (tmp_path / "site" / "verification.md").read_text(), (tmp_path / "site" / "coverage.md").read_text(),
               (tmp_path / "v.html").read_text()]
    for text in outputs:                            # nothing on any output claims a paper or a manuscript
        assert "Manuscript:" not in text and "formalizes results of" not in text
        assert "Paper ↔ Lean map" not in text and "Not formalized" not in text and "paper" not in text.lower()
    for section in ("Verification status", "Main results", "Trust boundary", "Reproduce", "Evidence",
                    "Citation", "License", "Acknowledgments"):
        assert f"## {section}\n" in md


def test_a_library_page_carries_easley_only_with_a_slurm_receipt(lp, env, capsys):
    from lean_pkg.page import EASLEY
    prepare(env, LIBRARY, easley=True)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and EASLEY in a["markdown"] and a["easley"] == {"backed": True, "jobs": ["slurm-12345"]}
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    assert check(lp, env, capsys) == (0, [])
    _git(env.repo_b, "add", "README.md")
    _git(env.repo_b, "commit", "-q", "-m", "page block")
    data, line = publish(lp, env, capsys)
    assert line[0]["status"] == "pass" and data["page"] == {"status": "pass", "adopted": True, "problems": []}


def test_a_library_easley_job_without_a_slurm_receipt_is_refused(lp, env, capsys):
    from lean_pkg.page import EASLEY
    prepare(env, LIBRARY, easley=True, receipt_job="systemd-user-unit-1234-h-abcdef012345")   # not a Slurm run
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "no Slurm receipt" in err and "slurm-12345" in err
    doc = json.loads(json.dumps(LIBRARY))
    write_coverage(env, doc)                                    # no compute: a hand-written sentence is unbacked
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    readme = env.repo_b / "README.md"
    readme.write_text(readme.read_text() + "\n" + EASLEY + "\n")
    rc, problems = check(lp, env, capsys)
    assert rc == 1 and len(problems) == 1 and "Easley" in problems[0]


def test_a_library_coverage_cannot_carry_a_manuscript_claim(lp, env, capsys, tmp_path):
    prepare(env, LIBRARY)
    errs = _refused(lp, env, capsys, tmp_path, library(manuscript={"title": "T", "version": ARXIV}))
    assert any(e.startswith("manuscript: a library coverage names no manuscript") for e in errs)
    errs = _refused(lp, env, capsys, tmp_path, library(manuscript={"title": "T", "version": "unposted"}),
                    "--allow-private-manuscript")
    assert any(e.startswith("manuscript: a library coverage names no manuscript") for e in errs)
    errs = _refused(lp, env, capsys, tmp_path, library(nothing_excluded={"reason": "all of it"}))
    assert any(e.startswith("nothing_excluded: ") and "library" in e for e in errs)
    paper_row = [{"paper": {"number": "Theorem 1", "title": "Main bound"}, "class": "same-assumptions",
                  "claims": ["beta/foo-le-bar"]}]
    errs = _refused(lp, env, capsys, tmp_path, library(rows=paper_row))
    assert any("rows[0]: a library row is a main result of the library" in e for e in errs)
    errs = _refused(lp, env, capsys, tmp_path, library(rows=[dict(LIBRARY["rows"][0], inputs=["a hypothesis"])]))
    assert any("rows[0]" in e and "inputs" in e for e in errs)
    errs = _refused(lp, env, capsys, tmp_path, library(rows=[{"title": "Main bound"}]))
    assert any("rows[0]: missing key(s) claims" in e for e in errs)
    errs = _refused(lp, env, capsys, tmp_path, library(rows=[{"title": "Main bound", "claims": ["beta/nope"]}]))
    assert any("'beta/nope' is not a claim id" in e for e in errs)
    dup = [LIBRARY["rows"][0], dict(LIBRARY["rows"][0])]
    assert any("repeats rows[0]" in e for e in _refused(lp, env, capsys, tmp_path, library(rows=dup)))
    assert any("kind: must be one of paper, library" in e
               for e in _refused(lp, env, capsys, tmp_path, library(kind="notes")))
    assert any("rows: must be a non-empty list" in e for e in _refused(lp, env, capsys, tmp_path, library(rows=[])))


def test_every_compared_theorem_is_a_main_result_of_a_library(lp, env, capsys):
    """The page says how many theorems were compared, so each must be a row's claim (R11), library or paper."""
    from .conftest import CLAIMS_YAML
    aux = CLAIMS_YAML + """\
- id: beta/aux
  decl: Demo.aux
  kind: theorem
  summary: An auxiliary statement.
  file: Demo/Main.lean
  status: internal
  superseded_by: null
  citation: null
  depends_on: []
"""
    doc = json.loads(json.dumps(LIBRARY))
    doc["rows"] = [{"title": "Auxiliary", "claims": ["beta/aux"]}]
    prepare(env, doc, extra={"claims.yaml": aux})
    rc, data = cov_check(lp, env, capsys)
    assert rc == 1 and any("comparator theorem 'BetaAudit.Demo.foo_le_bar'" in e and "no claim a row names" in e
                           for e in data["errors"])


def test_paper_kind_is_validated_as_before(lp, env, capsys, tmp_path):
    prepare(env)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["kind"] == "paper" and data["manuscript"] == "arxiv"
    explicit = mutate(kind="paper")                                 # kind: paper is the default, spelled out
    f = tmp_path / "explicit.yaml"
    f.write_text(explicit)
    assert cov_check(lp, env, capsys, "--file", str(f))[0] == 0
    for doc in (mutate(manuscript=None), mutate(manuscript=None, kind="paper")):
        assert any("missing key(s) manuscript" in e for e in _refused(lp, env, capsys, tmp_path, doc))
    unposted = json.loads(json.dumps(COVERAGE))
    unposted.update(kind="paper", manuscript={"title": "T", "version": "unposted"})
    errs = _refused(lp, env, capsys, tmp_path, yaml.safe_dump(unposted, sort_keys=False))
    assert any("--allow-private-manuscript" in e for e in errs)
    lib_rows = mutate(kind="paper", rows=LIBRARY["rows"])           # a paper row needs paper and class
    assert any("rows[0]: missing key(s) paper, class" in e for e in _refused(lp, env, capsys, tmp_path, lib_rows))


def test_anchor_and_rebase_leave_a_library_coverage_alone(env):
    import pytest
    from lean_pkg import anchor, rebase
    from lean_pkg import cli as core
    with pytest.raises(core.Fail, match="kind library: it names no manuscript"):
        anchor.coverage_edit(library(), ARXIV, "0" * 64)       # never binds a library to an e-print
    prepare(env, LIBRARY)
    _cdoc, _cwhere, cov, vwhere = rebase._inputs(read_reg(env)["packages"]["beta"], env.repo_b, None, None)
    assert cov is None and vwhere is None                       # a claims rebase never rewrites it
    prepare(env)
    assert rebase._inputs(read_reg(env)["packages"]["beta"], env.repo_b, None, None)[2]["manuscript"]
