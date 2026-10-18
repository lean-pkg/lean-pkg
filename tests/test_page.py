#!/usr/bin/env python3
"""`lean-pkg coverage check` (coverage/1) and `lean-pkg page`: the README verification section is rendered
from data, deterministically; --write touches only the marked block; --check fails on a stale or missing
block, a missing lean-pkg credit and an Easley sentence without a backing Slurm receipt; private strings
never reach the output."""

from __future__ import annotations

import json
import shutil

import yaml

from .conftest import (CLAIMS_YAML, put_receipt, MATHLIB_MANIFEST, MIT_TEXT, _git, make_comparator_receipt,
                       make_receipt, read_reg, repo_head, set_lease, write_reg)

# built at run time: the literal forms are latex-label hits for lean-pkg's own `make public-safety` (--strict)
ARXIV = "arXiv" + ":2501.01234v2"
LABEL = "thm" + ":main"
COVERAGE = {
    "schema": "coverage/1",
    "package": "beta",
    "manuscript": {"title": "A demonstration bound", "version": ARXIV},
    "summary": "Theorem 1 of the paper is formalized with the paper's own hypotheses; Theorem 2 is not.",
    "rows": [
        {"paper": {"number": "Theorem 1", "title": "Main bound", "label": LABEL,
                   "statement": "For every integer $n > 0$,\n\\[ n \\le n^2 . \\]"},
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
# the Challenge module of the env fixture's comparator.json (theorem BetaAudit.Demo.foo_le_bar)
CHALLENGE = """\
namespace BetaAudit.Demo

/-- The main bound. -/
theorem foo_le_bar (n : Nat) (h : 0 < n) :
    n ≤ n * n := by
  sorry

end BetaAudit.Demo
"""
CHALLENGE_STATEMENT = "theorem foo_le_bar (n : Nat) (h : 0 < n) :\n    n ≤ n * n"
# internal words that never reach a rendered page (the reader is a mathematician, not a lean-pkg user)
JARGON = ("lower-tier", "lower tier", "evidence-only", "descendant", "promote stage", "R11", "R12",
          "same-assumptions", "inputs-supplied", "Tier", "trust boundary")


def prepare(env, coverage: dict | None = None, *, easley: bool = False, job: str = "slurm-12345",
            receipt_job: str | None = None, citation: str = CITATION, extra: dict | None = None,
            comparator: dict | None = None) -> str:
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
             "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST, "Challenge.lean": CHALLENGE,
             **(extra or {})}
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", "page inputs")
    head = repo_head(repo)
    receipt_job = receipt_job or job
    lower = f".lean-receipts/{head[:12]}-{receipt_job}.json"
    put_receipt(repo / lower, make_receipt(repo, job_id=receipt_job, host="secret-host-xyz",
                                           checker="/home/someone/verify.sh"), indent=2)
    comp = f".lean-receipts/{head[:12]}-comparator.json"
    (repo / comp).write_text(json.dumps(make_comparator_receipt(repo, **(comparator or {})), indent=2))
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
    block = md.split("### Theorem 1: Main bound\n", 1)[1].split("\n## ", 1)[0]
    assert block.startswith("\n**Verified.**")
    assert "**In the paper.**\n\n```latex\nFor every integer $n > 0$,\n\\[ n \\le n^2 . \\]\n```" in block
    assert f"**In Lean.** `BetaAudit.Demo.foo_le_bar` in [Challenge.lean]" in block
    assert f"```lean\n{CHALLENGE_STATEMENT}\n```" in block                 # the statement, without its proof
    assert "sorry\n```" not in block and "**Hypotheses.** The paper's own" in block
    assert "## Not formalized" in md and "- Theorem 2 (Sharpness): the lower-bound construction" in md
    assert "### Theorem 2" not in md                               # an outside row has no result block
    assert LABEL not in md                                         # labels stay in coverage.yaml
    heads = [x[3:] for x in md.splitlines() if x.startswith("## ")]
    assert heads == ["Verification at a glance", "The paper's results in Lean", "Not formalized",
                     "What the checks establish", "How this was checked", "Citation", "License", "Acknowledgments"]
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
    block = a["markdown"].split("### Theorem 1: Main bound\n", 1)[1].split("\n## ", 1)[0]
    assert rc == 0 and a["ledger_status"] == "open" and "**Verified.**" not in a["markdown"]
    assert block.startswith("\n**Not yet verified.** lean-pkg's record of the checks says: open.")
    assert "Verified: 0 of the 1 result(s) in Lean." in a["markdown"]


def test_receipt_fields_outside_the_allow_list_never_appear(lp, env, capsys):
    prepare(env)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    for private in ("secret-host-xyz", "/home/someone", "verify.sh", "h-1f2e3d4c5b6a"):
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
    readme.write_text(readme.read_text().replace("Verified: 1 of", "Verified: 9 of"))
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
    # SLURM_JOB_ID is any shell's to set: without an sacct seal (test_comparator_sacct_seal) no backing
    assert slurm_job_of({**comp, "host_policy": "Slurm job 5581013 (SLURM_JOB_ID set)"}) is None
    assert slurm_job_of({**comp, "host_policy": "Slurm job 999 (SLURM_JOB_ID set)"}) is None


def test_a_schema_2_comparator_receipt_is_listed(lp, env, capsys):
    # comparator-receipt writes schema 2; the Evidence table and the Easley rule once knew only schema 1
    # (that a sealed schema-2 receipt backs its Slurm job: test_comparator_sacct_seal)
    from lean_pkg.coverage import slurm_job_of
    comp2 = {"schema": "comparator-receipt/2", "mechanism": "toolchain", "job_id": "5581013",
             "host_policy": "Slurm job 5581013 on node-1"}
    assert slurm_job_of(comp2) is None                                  # unsealed
    assert slurm_job_of({**comp2, "host_policy": "allowed by COMPARATOR_RECEIPT_ALLOW_HOST=1"}) is None
    head = prepare(env)
    rel = f".lean-receipts/{head[:12]}-comparator.json"
    p = env.repo_b / rel
    rec = json.loads(p.read_text())
    rec.update(schema="comparator-receipt/2", mechanism="legacy")
    p.write_text(json.dumps(rec, indent=2))
    _git(env.repo_b, "commit", "-q", "-am", "schema 2 comparator receipt")
    rc, data = page_json(lp, env, capsys)
    assert rc == 0
    row = next(x for x in data["markdown"].splitlines() if x.startswith(f"| `{rel}`"))
    assert "(used here) | statement comparison |" in row


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
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", "root")
    root = repo_head(repo)
    comp = f".lean-receipts/{root[:12]}-comparator.json"
    (repo / comp).write_text(json.dumps(make_comparator_receipt(repo), indent=2))
    lower = f".lean-receipts/{root[:12]}-{job}.json"
    put_receipt(repo / lower, make_receipt(repo, job_id=job), indent=2)
    _git(repo, "add", "-f", lower, lower.replace(".json", "-verify.redacted.log"))
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
    block = md.split("### Main bound\n", 1)[1].split("\n## ", 1)[0]
    assert block.startswith("\n**Verified.**") and "**In Lean.** `BetaAudit.Demo.foo_le_bar`" in block
    assert f"```lean\n{CHALLENGE_STATEMENT}\n```" in block and "**Note.** the headline estimate" in block
    assert "**In words.**" not in block and "**Hypotheses.**" not in block     # no statement given; no class
    assert "## Main results\n" in md and "Each result is exactly its Lean statement" in md
    assert "- Main results: 1; verified: 1 of 1." in md
    outputs = [md, (tmp_path / "site" / "verification.md").read_text(), (tmp_path / "site" / "coverage.md").read_text(),
               (tmp_path / "v.html").read_text()]
    for text in outputs:                            # nothing on any output claims a paper or a manuscript
        assert "Manuscript:" not in text and "formalizes results of" not in text
        assert "Paper ↔ Lean map" not in text and "Not formalized" not in text and "paper" not in text.lower()
        assert "manuscript" not in text.lower()
    heads = [x[3:] for x in md.splitlines() if x.startswith("## ")]
    assert heads == ["Verification at a glance", "Main results", "What the checks establish",
                     "How this was checked", "Citation", "License", "Acknowledgments"]


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


# --- field report: a package's own private patterns, the registry status, the comparator evidence row ----

PATTERNS = "(?i)manuscript\n(?i)private\\b\n"      # a package guarding its release against such words
COMP2 = {"schema": "comparator-receipt/2", "mechanism": "legacy"}   # the schema comparator-receipt writes


def _outputs(tmp_path, md: str) -> list[str]:
    return [md, (tmp_path / "site" / "verification.md").read_text(),
            (tmp_path / "site" / "coverage.md").read_text(), (tmp_path / "v.html").read_text()]


def test_a_library_page_passes_patterns_against_manuscript_and_private(lp, env, capsys, tmp_path):
    import re
    prepare(env, LIBRARY, extra={".lean-pkg-private-patterns": PATTERNS}, comparator=COMP2)
    rc, a = page_json(lp, env, capsys, "--site", str(tmp_path / "site"), "--html", str(tmp_path / "v.html"))
    assert rc == 0 and a["kind"] == "library"
    for text in _outputs(tmp_path, a["markdown"]):      # lean-pkg's own wording on a library page
        assert not re.search(r"(?i)manuscript|paper|private\b", text)
    assert "Each result is exactly its Lean statement" in a["markdown"]
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    # negative control: a paper page names its manuscript, and the same patterns refuse it
    prepare(env, extra={".lean-pkg-private-patterns": PATTERNS})
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "private string(s) (.lean-pkg-private-patterns:1)" in err


def test_the_page_carries_no_registry_status_and_stays_current_on_promote(lp, env, capsys):
    for doc in (LIBRARY, COVERAGE):
        prepare(env, doc)
        assert read_reg(env)["packages"]["beta"]["status"] == "verified-private"
        rc, a = page_json(lp, env, capsys)
        assert rc == 0 and "registry status" not in a["markdown"] and "verified-private" not in a["markdown"]
        assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
        reg = read_reg(env)
        reg["packages"]["beta"]["status"] = "public"                # what promote changes
        write_reg(env, reg)
        assert check(lp, env, capsys) == (0, [])                     # the committed page is not stale
        reg["packages"]["beta"]["status"] = "verified-private"
        write_reg(env, reg)


def test_the_evidence_table_lists_a_comparator_receipt_of_the_current_schema(lp, env, capsys):
    from .conftest import sha256
    for doc in (LIBRARY, COVERAGE):
        prepare(env, doc, comparator=COMP2)
        rc, a = page_json(lp, env, capsys)
        assert rc == 0 and a["receipts"] == 2
        rel = read_reg(env)["packages"]["beta"]["gate"]["comparator"]
        raw = (env.repo_b / rel).read_bytes()
        rec = json.loads(raw)
        row = next(x for x in a["markdown"].splitlines() if x.startswith(f"| `{rel}`"))
        assert row == (f"| `{rel}` (used here) | statement comparison | `{rec['commit'][:12]}` | 2026-10-05 | — | "
                       f"`{sha256(raw)}` |")
        tiers = [x.split(" | ")[1] for x in a["markdown"].splitlines() if x.startswith("| `.lean-receipts/")]
        assert sorted(tiers) == ["build", "statement comparison"]


# --- the page for mathematicians: mathematics first, plain words, receipts last -------------------------

def _all_outputs(lp, env, capsys, tmp_path) -> tuple[dict, list[str]]:
    rc, a = page_json(lp, env, capsys, "--site", str(tmp_path / "site"), "--html", str(tmp_path / "v.html"))
    assert rc == 0
    return a, _outputs(tmp_path, a["markdown"])


def test_no_internal_jargon_reaches_any_output_of_either_kind(lp, env, capsys, tmp_path):
    library = json.loads(json.dumps(LIBRARY))
    library["rows"][0]["statement"] = "Every $n$ satisfies $n \\le n^2$."      # In words: no jargon either
    for doc in (COVERAGE, library):
        prepare(env, doc)
        _a, outputs = _all_outputs(lp, env, capsys, tmp_path)
        for text in outputs:
            for word in JARGON:
                if word[0].isupper():
                    assert word not in text, word
                else:
                    assert word not in text.lower(), word


def test_the_trust_line_opens_the_page_and_names_what_to_compare(lp, env, capsys, tmp_path):
    from lean_pkg.page import LIBRARY_TRUST_LINE, TRUST_LINE
    for doc, line in ((COVERAGE, TRUST_LINE), (LIBRARY, LIBRARY_TRUST_LINE)):
        prepare(env, doc)
        a, outputs = _all_outputs(lp, env, capsys, tmp_path)
        md = a["markdown"]
        assert md.split("## Verification at a glance\n\n", 1)[1].startswith(line + "\n")
        assert md.index(line) < md.index("##", md.index("## Verification at a glance") + 3)   # before any section
        assert line in outputs[1]                                           # verification.md
        assert "How to read this page." in outputs[3]                       # the HTML says it too
    assert "read the paper's statement beside the Lean statement" in TRUST_LINE
    assert "paper" not in LIBRARY_TRUST_LINE.lower()


def test_every_receipt_is_listed_in_how_this_was_checked_and_nowhere_before(lp, env, capsys, tmp_path):
    for doc in (COVERAGE, LIBRARY):
        prepare(env, doc, comparator=COMP2)
        a, _outputs_ = _all_outputs(lp, env, capsys, tmp_path)
        md = a["markdown"]
        before, after = md.split("## How this was checked\n", 1)
        after = after.split("\n## Citation", 1)[0]
        tracked = sorted(x for x in _git(env.repo_b, "ls-files", ".lean-receipts").stdout.split()
                         if x.endswith(".json"))
        assert len(tracked) == a["receipts"] == 2
        for rel in tracked:
            assert f"| `{rel}`" in after and rel not in before
        assert "sha256:" not in before and "```bash" not in before            # hashes and commands come last
        assert after.index("### Receipts") < after.index("### Reproduce")


def test_the_lean_statement_falls_back_to_a_link_when_it_cannot_be_read(lp, env, capsys):
    prepare(env, extra={"Challenge.lean": "namespace BetaAudit.Demo\ntheorem foo_le_bar : Nat → Nat\n"
                                          "  | 0 => 1\n  | _ => 2\nend BetaAudit.Demo\n"})
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "could not be read reliably from the source; open the file" in a["markdown"]
    assert "```lean" not in a["markdown"]                              # never a guess
    _git(env.repo_b, "rm", "-q", "Challenge.lean")
    _git(env.repo_b, "commit", "-q", "-m", "no challenge")
    prepare(env)
    _git(env.repo_b, "rm", "-q", "Challenge.lean")                     # after the gate: not at the bound commit
    _git(env.repo_b, "commit", "-q", "-m", "drop it")
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "```lean" in a["markdown"]                       # read at the gate-bound commit


def test_without_a_comparator_the_lean_statement_is_the_declaration_in_its_file(lp, env, capsys):
    prepare(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["comparator"] = None
    write_reg(env, reg)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and "**In Lean.** `Demo.foo_le_bar` in [Demo/Main.lean]" in md
    assert "```lean\ntheorem foo_le_bar : True\n```" in md and "checked against" not in md
    assert "**No statement comparison is recorded yet**" in md


def test_statement_extraction_reads_only_what_it_can_read_safely():
    from lean_pkg.lean_scan import statement
    text = ("namespace A\n/-- d -/\n@[simp] theorem t (n : Nat) {k : Nat := 3}\n    (h : n = k) :\n"
            "    n + 0 = k := by\n  simp [h]\nlemma two : True := trivial\nlemma two : True := trivial\n"
            "theorem e : Nat → Nat\n  | _ => 0\ntheorem nxt : True := trivial\nend A\n")
    assert statement(text, "A.t") == "theorem t (n : Nat) {k : Nat := 3}\n    (h : n = k) :\n    n + 0 = k"
    assert statement(text, "t") is None                                 # the qualified name, nothing else
    assert statement(text, "A.two") is None                             # twice: ambiguous
    assert statement(text, "A.e") is None                               # equation form: no `:=`
    assert statement(text, "A.nxt") == "theorem nxt : True"
    long = "theorem big : True ∧\n" + "  True ∧\n" * 50 + "  True := by sorry\n"
    assert statement(long, "big") is None                               # too long to show


# an umbrella Challenge.lean (#9 of the 0.11.0 field reports): only imports; the theorem is declared in a module
# it imports, reached through a cycle-safe walk of the package's own committed files
UMBRELLA = "/- an umbrella: import Fake.Module in a comment is not an import -/\nimport Mathlib\nimport Challenge.Ref.Main\n"
REF_MAIN = ("module\n\npublic import Challenge.Ref.Defs\n\nnamespace BetaAudit.Demo\n\n/-- The main bound. -/\n"
            "theorem foo_le_bar (n : Nat /- [definition] -/ ) (h : 0 < n) :\n    n ≤ n * n := by\n  sorry\n\n"
            "end BetaAudit.Demo\n")
REF_DEFS = "import Challenge.Ref.Main\nimport Challenge\n-- a cycle back to both\ndef helper : Nat := 0\n"


def test_an_umbrella_challenge_resolves_the_statement_in_the_declaring_module(lp, env, capsys):
    prepare(env, extra={"Challenge.lean": UMBRELLA, "Challenge/Ref/Main.lean": REF_MAIN,
                        "Challenge/Ref/Defs.lean": REF_DEFS})
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and "could not be read reliably" not in md
    assert ("**In Lean.** `BetaAudit.Demo.foo_le_bar` in [Challenge/Ref/Main.lean](https://github.com/example/beta/"
            "blob/") in md and "/Challenge/Ref/Main.lean#L8): the statement the proof was checked against" in md
    assert ("```lean\ntheorem foo_le_bar (n : Nat /- [definition] -/ ) (h : 0 < n) :\n    n ≤ n * n\n```"
            in md)                                                      # the binder's comment kept verbatim
    rc2, b = page_json(lp, env, capsys)
    assert rc2 == 0 and b["markdown"] == md                            # deterministic


def test_an_umbrella_challenge_keeps_the_fallback_when_the_declaration_is_not_unique(lp, env, capsys):
    dup = REF_DEFS + "namespace BetaAudit.Demo\ntheorem foo_le_bar : True := trivial\nend BetaAudit.Demo\n"
    prepare(env, extra={"Challenge.lean": UMBRELLA, "Challenge/Ref/Main.lean": REF_MAIN,
                        "Challenge/Ref/Defs.lean": dup})
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "could not be read reliably from the source" in a["markdown"]
    assert "```lean" not in a["markdown"] and "[Challenge.lean](" in a["markdown"]   # the Challenge, unlinked line


def test_an_umbrella_challenge_keeps_the_fallback_when_the_declaring_module_is_missing(lp, env, capsys):
    prepare(env, extra={"Challenge.lean": UMBRELLA + "import Challenge.Ref.Gone\n",
                        "Challenge/Ref/Defs.lean": "def helper : Nat := 0\n"})
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "could not be read reliably from the source" in a["markdown"]
    assert "```lean" not in a["markdown"]


def test_the_import_closure_never_leaves_the_package_and_refuses_when_unsure():
    from lean_pkg import lean_scan as ls
    assert ls.header_imports("/- import X -/ module\nprelude\npublic meta import all A.B -- import C\n"
                             "import «x.y».Z\nimport D\n\ntheorem t : True := trivial\nimport E\n") == \
        ["A.B", "«x.y».Z", "D"]
    assert ls.module_file("A.B.C") == "A/B/C.lean" and ls.module_file("«x.y».Z") == "x.y/Z.lean"
    for bad in ("«..».Evil", "A.«.»", "«a/b».C", "«».C", "A..B", ""):
        assert ls.module_file(bad) is None, bad
    files = {"Challenge.lean": "import «..».Evil\nimport Ref\n",
             "Ref.lean": "namespace N\ntheorem t : True := trivial\nend N\n"}
    asked: list[str] = []

    def read(rel):
        asked.append(rel)
        return files.get(rel)
    idx = ls.DeclarationIndex(read, "Challenge")
    assert all(".." not in r for r in asked) and not idx.complete
    got = idx.statement("N.t")
    assert got["text"] is None and got["file"] == "Challenge.lean"     # incomplete closure: no guess
    files["Challenge.lean"] = "import Ref\nimport Mathlib\n"
    got = ls.DeclarationIndex(read, "Challenge").statement("N.t")
    assert (got["file"], got["line"], got["text"]) == ("Ref.lean", 2, "theorem t : True")
    assert ls.DeclarationIndex(read, "Challenge").statement("N.nope")["text"] is None
    chain = {f"M{i}.lean": f"import M{i + 1}\n" for i in range(ls.IMPORT_MAX_DEPTH + 5)}
    assert not ls.DeclarationIndex(chain.get, "M0").complete                # bounded depth


def test_statement_extraction_survives_comments_and_strings_in_binders():
    from lean_pkg.lean_scan import statement
    text = ("/-- A doc with /- a nested comment -/ and `:=` in it. -/\n"
            "theorem c (x : Nat /- [definition] -/ ) (y : Nat /- outer /- inner := -/ ) -/ )\n"
            "    (h : \"-/ := (\" = \"s\") -- a line comment := with ( an open bracket\n"
            "    : x = x := rfl\n")
    assert statement(text, "c") == ("theorem c (x : Nat /- [definition] -/ ) (y : Nat /- outer /- inner := -/ ) -/ )\n"
                                    "    (h : \"-/ := (\" = \"s\") -- a line comment := with ( an open bracket\n"
                                    "    : x = x")
    unclosed = "theorem u (x : Nat /- never closed ) : x = x := rfl\n"
    assert statement(unclosed, "u") is None


# a top-level let / letI / have in the statement owns one `:=` (its value); the proof's `:=` is the next free one
# (lab field report of 0.16.0: `let n : Nat := 1` cut the statement to "let n : Nat")
LET_STATEMENTS = {
    "typedlet": ("theorem typedlet :\n  let n : Nat := 1\n  n = 1 := by rfl\n",
                 "theorem typedlet :\n  let n : Nat := 1\n  n = 1"),
    "untyped": ("theorem untyped :\n  let n := 1\n  n = 1 := by rfl\n", "theorem untyped :\n  let n := 1\n  n = 1"),
    "multiline": ("theorem multiline :\n  let f : Nat → Nat :=\n    fun k =>\n      k + 1\n  f 0 = 1 := by\n  rfl\n",
                  "theorem multiline :\n  let f : Nat → Nat :=\n    fun k =>\n      k + 1\n  f 0 = 1"),
    "inst": ("theorem inst :\n  letI inst : Inhabited Nat := ⟨0⟩\n  (default : Nat) = 0 := rfl\n",
             "theorem inst :\n  letI inst : Inhabited Nat := ⟨0⟩\n  (default : Nat) = 0"),
    "nested": ("theorem nested :\n  let a := let b := 1; b + 1\n  let c := a\n  c = 2 := rfl\n",
               "theorem nested :\n  let a := let b := 1; b + 1\n  let c := a\n  c = 2"),
    "hav": ("theorem hav (x : Nat) :\n  have hx : x = x := rfl\n  haveI := hx\n  x + 0 = x := by simp\n",
            "theorem hav (x : Nat) :\n  have hx : x = x := rfl\n  haveI := hx\n  x + 0 = x"),
    "inner": ("theorem inner :\n  let s : Foo := { x := 1, y := (f (a := 2) : Nat) }\n  s.x = 1 := rfl\n",
              "theorem inner :\n  let s : Foo := { x := 1, y := (f (a := 2) : Nat) }\n  s.x = 1"),
    "oneline": ("theorem oneline : let n := 1; n = 1 := by rfl\n", "theorem oneline : let n := 1; n = 1"),
    "absval": ("theorem absval (x : Int) :\n    |x| ≤ |x| + 1 := by omega\n", "theorem absval (x : Int) :\n    |x| ≤ |x| + 1"),
    "arms": ("theorem arms (n : Nat) :\n  match n with\n  | 0 => True\n  | _ => let k := n; k = k := by\n  cases n <;> simp\n",
             "theorem arms (n : Nat) :\n  match n with\n  | 0 => True\n  | _ => let k := n; k = k"),
}


def test_statement_extraction_keeps_let_and_have_values_and_the_conclusion():
    from lean_pkg import lean_scan as ls
    for name, (text, want) in LET_STATEMENTS.items():
        assert ls.statement(text, name) == want, name
    files = {"Challenge.lean": "import Ref\n", "Ref.lean": "namespace N\n" + LET_STATEMENTS["typedlet"][0] + "end N\n"}
    got = ls.DeclarationIndex(files.get, "Challenge").statement("N.typedlet")
    assert got["text"] == LET_STATEMENTS["typedlet"][1] and got["reason"] is None


def test_statement_extraction_refuses_rather_than_truncate():
    from lean_pkg.lean_scan import statement
    text = ("theorem plain (n : Nat) : n = n := rfl\n"
            "theorem samel : True := by trivial\n"
            "theorem wh : True := aux\nwhere aux : True := trivial\n"
            "theorem eqn : Nat → Nat\n  | 0 => 0\n  | n + 1 => let k := n; k\n"
            "theorem tac : (1 : Nat) = by exact 1 := rfl\n"
            "theorem steps : calc 1 = 1 := rfl\n  _ = 1 := rfl := trivial\n"
            "theorem «q := r» : True := trivial\n"
            "def d : Nat := 1\n")
    assert statement(text, "plain") == "theorem plain (n : Nat) : n = n"
    assert statement(text, "samel") == "theorem samel : True"
    assert statement(text, "wh") == "theorem wh : True"
    assert statement(text, "eqn") is None                               # equation compiler: no statement `:=`
    assert statement(text, "tac") is None                               # a tactic block in the type: unsure
    assert statement(text, "steps") is None                             # calc steps hold their own `:=`
    assert statement(text, "«q := r»") == "theorem «q := r» : True"     # a quoted name is one token
    assert statement(text, "d") is None                                 # only theorems and lemmas
    assert statement("theorem open_let :\n  let n : Nat\n", "open_let") is None   # no proof `:=` at all


def test_the_page_shows_a_let_statement_whole(lp, env, capsys):
    let_challenge = CHALLENGE.replace("theorem foo_le_bar (n : Nat) (h : 0 < n) :\n    n ≤ n * n := by",
                                      "theorem foo_le_bar (n : Nat) (h : 0 < n) :\n    let m : Nat := n * n\n"
                                      "    n ≤ m := by")
    prepare(env, extra={"Challenge.lean": let_challenge})
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and "could not be read reliably" not in md
    assert "```lean\ntheorem foo_le_bar (n : Nat) (h : 0 < n) :\n    let m : Nat := n * n\n    n ≤ m\n```" in md


def test_a_paper_statement_is_validated_and_a_library_statement_is_shown_in_words(lp, env, capsys, tmp_path):
    prepare(env)
    row = json.loads(json.dumps(COVERAGE["rows"][0]))
    for bad, why in ((["x"], "must be a non-empty string"), ("x" * 2001, "at most 2000"),
                     ("a\n```\nb", "may not start with ```"), ("a\n   ~~~\nb", "may not start with ```"),
                     ("x <!-- lean-pkg:page:end --> y", "page markers"), ("TODO state it", "placeholder")):
        row["paper"]["statement"] = bad
        errs = _refused(lp, env, capsys, tmp_path, mutate(rows=[row, COVERAGE["rows"][1]]))
        assert any(why in e for e in errs), (bad, errs)
    doc = json.loads(json.dumps(LIBRARY))
    doc["rows"][0]["statement"] = "Every $n$ satisfies $n \\le n^2$."
    prepare(env, doc)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "**In words.**\n\n```latex\nEvery $n$ satisfies $n \\le n^2$.\n```" in a["markdown"]


def test_a_paper_statement_reaches_every_output_inert(lp, env, capsys, tmp_path):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0]["paper"]["statement"] = "If $a<b$ then <script>x</script> **not bold** [l](https://e.org)"
    prepare(env, doc)
    _a, outputs = _all_outputs(lp, env, capsys, tmp_path)
    for text in outputs[:2]:                                               # README block, verification.md
        assert "```latex\nIf $a<b$ then <script>x</script> **not bold** [l](https://e.org)\n```" in text
    html_out = outputs[3]
    assert "<script>" not in html_out and "&lt;script&gt;" in html_out    # escaped, never markup
    assert '<pre class="latex"><code>If $a&lt;b$ then' in html_out and "<strong>not bold" not in html_out


def test_a_private_string_in_a_paper_statement_refuses_the_page(lp, env, capsys):
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0]["paper"]["statement"] = "For every $n$, zz_secret_bound holds."
    prepare(env, doc, extra={".lean-pkg-private-patterns": "zz_secret_bound\n"})
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "private string(s)" in err and "nothing written" in err


def test_a_private_string_in_a_lean_statement_refuses_the_page(lp, env, capsys):
    prepare(env, extra={".lean-pkg-private-patterns": "zz_secret_bound\n",
                        "Challenge.lean": CHALLENGE.replace("n ≤ n * n", "n ≤ n * n ∧ zz_secret_bound")})
    rc, _out, err = run(lp, env, capsys, "page", "beta")
    assert rc == 1 and "private string(s)" in err and "nothing written" in err


# --- a verified result the comparator did not check (claims that are not reusable need no comparator) ---

COMPARED = "(the build, and the statement comparison)"
NOT_COMPARED = "(the build; its statement was not compared by a second checker)"
EXPERIMENTAL_CLAIM = """\
- id: beta/baz-true
  decl: Demo.baz_true
  kind: theorem
  summary: A second demonstration result, not offered for reuse.
  file: Demo/Main.lean
  status: experimental
  superseded_by: null
  depends_on: []
"""
TWO_CLAIMS = {"claims.yaml": CLAIMS_YAML + EXPERIMENTAL_CLAIM,
              "Demo/Main.lean": "namespace Demo\n\ntheorem foo_le_bar : True := trivial\n\n"
                                "theorem baz_true : True := trivial\n\nend Demo\n"}


def _block(md: str, heading: str) -> str:
    return md.split(f"### {heading}\n", 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]


def test_a_verified_result_the_comparator_did_not_check_does_not_claim_a_statement_comparison(lp, env, capsys,
                                                                                            tmp_path):
    doc = json.loads(json.dumps(LIBRARY))
    doc["rows"].append({"title": "Side fact", "claims": ["beta/baz-true"]})
    prepare(env, doc, extra=TWO_CLAIMS)
    rc, a = page_json(lp, env, capsys, "--site", str(tmp_path / "site"), "--html", str(tmp_path / "v.html"))
    assert rc == 0 and a["verified_rows"] == 2, a.get("problems")
    md = a["markdown"]
    compared, side = _block(md, "Main bound"), _block(md, "Side fact")
    assert compared.startswith("\n**Verified.**") and COMPARED in compared and NOT_COMPARED not in compared
    assert side.startswith("\n**Verified.**") and NOT_COMPARED in side
    assert "statement comparison" not in side and "second checker confirmed" not in side    # regression
    assert "- Main results: 2; verified: 2 of 2 (for 1 of them a second checker also compared the statement)." in md
    for text in ((tmp_path / "site" / "verification.md").read_text(), (tmp_path / "v.html").read_text()):
        assert text.count("statement comparison).") == 1 and "not compared by a second checker" in text


def test_a_paper_page_names_the_compared_count_only_when_not_every_verified_result_was_compared(lp, env, capsys):
    prepare(env)
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "- Verified: 1 of the 1 result(s) in Lean.\n" in a["markdown"]       # all compared
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"].insert(1, {"paper": {"number": "Theorem 3", "title": "Side fact"}, "class": "same-assumptions",
                           "claims": ["beta/baz-true"]})
    prepare(env, doc, extra=TWO_CLAIMS)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and a["verified_rows"] == 2, a.get("problems")
    assert COMPARED in _block(md, "Theorem 1: Main bound")
    side = _block(md, "Theorem 3: Side fact")
    assert NOT_COMPARED in side and "statement comparison" not in side
    assert ("- Verified: 2 of the 2 result(s) in Lean (for 1 of them a second checker also compared the "
            "statement).") in md
