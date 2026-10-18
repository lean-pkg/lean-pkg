#!/usr/bin/env python3
"""A claim's explicit `judged_as` (claims/1, optional): the comparator theorem that judges it when the Challenge
theorem is not named after the claim's decl (an `Audit` namespace, a Mathlib-only restatement). The mapping is
checked, never trusted: it must name a configured theorem, its claim's decl must be declared in its file, one
name judges one claim, and a mapped name credits no other claim. `receipt check` reads it from the repository
alone, in claims.yaml at the bound commit, where the claims are bound."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess

import pytest
import yaml

from .conftest import CLAIMS_YAML, DEMO_LEAN, MATHLIB_MANIFEST, MIT_TEXT, _git, make_comparator_receipt, \
    make_receipt, read_reg, repo_head, write_reg
from .test_page import CITATION, COVERAGE

JOB = "slurm-12345"
AUDIT = "Audit.foo_bound"            # a Challenge name the naming rule does not credit to Demo.foo_le_bar


def claims_doc(**judged) -> dict:
    """The env fixture's claims file, with judged_as set on beta/foo-le-bar when given."""
    d = yaml.safe_load(CLAIMS_YAML)
    d["claims"][0].update(judged)
    return d


def write_claims(repo, d: dict, msg: str = "claims: judged_as") -> str:
    (repo / "claims.yaml").write_text(yaml.safe_dump(d, sort_keys=False))
    _git(repo, "add", "claims.yaml")
    _git(repo, "commit", "-q", "-m", msg)
    return repo_head(repo)


# --- the schema ------------------------------------------------------------------------------------------

def test_judged_as_is_an_accepted_optional_key(cl):
    assert cl.validate_claims(claims_doc(judged_as=AUDIT), "beta") == []
    assert cl.normalize_claim(claims_doc(judged_as=AUDIT)["claims"][0])["judged_as"] == AUDIT
    # absent (or null) keeps the normalized claim exactly as before, so older caches compare equal
    assert "judged_as" not in cl.normalize_claim(claims_doc()["claims"][0])
    assert "judged_as" not in cl.normalize_claim(claims_doc(judged_as=None)["claims"][0])


@pytest.mark.parametrize("value, needle", [
    ("not a name", "judged_as must be null or the full Lean name"),
    ("", "judged_as must be null or the full Lean name"),
    (["Audit.x"], "judged_as must be null or the full Lean name"),
    ("TODO", "judged_as must be null or the full Lean name"),
])
def test_a_malformed_judged_as_is_refused(cl, value, needle):
    errors = cl.validate_claims(claims_doc(judged_as=value), "beta")
    assert any(needle in e for e in errors), errors


def test_judged_as_on_a_definition_is_refused(cl):
    errors = cl.validate_claims(claims_doc(judged_as=AUDIT, kind="definition"), "beta")
    assert any("judges only theorem and lemma claims" in e for e in errors), errors


def test_one_name_judges_one_claim(cl):
    d = claims_doc(judged_as=AUDIT)
    other = copy.deepcopy(d["claims"][0])
    other.update(id="beta/other", decl="Demo.other")
    d["claims"].append(other)
    errors = cl.validate_claims(d, "beta")
    assert any("set on more than one claim (beta/foo-le-bar, beta/other)" in e for e in errors), errors


# --- the coverage rule -----------------------------------------------------------------------------------

def test_claim_covered_uses_the_mapping_only(lp):
    import lean_pkg.comparator_config as cc
    c = claims_doc(judged_as=AUDIT)["claims"][0]
    q = {"Demo.foo_le_bar"}
    assert cc.claim_covered({AUDIT}, c, q)
    # the explicit name replaces the naming rule for that claim: the name-matching theorem does not cover it
    assert not cc.claim_covered({"BetaAudit.Demo.foo_le_bar"}, c, q)
    # a decl not declared in its file is covered by nothing, mapping or not
    assert not cc.claim_covered({AUDIT}, c, set())
    plain = claims_doc()["claims"][0]
    assert not cc.claim_covered({AUDIT}, plain, q)                     # the control: no mapping, no credit
    assert cc.claim_covered({"BetaAudit.Demo.foo_le_bar"}, plain, q)


def test_a_mapped_name_credits_no_other_claim(lp):
    """B maps the name that, by the naming rule alone, would cover A: it judges B, and A is not covered."""
    import lean_pkg.comparator_config as cc
    a = claims_doc()["claims"][0]
    b = dict(a, id="beta/baz", decl="Demo.baz", judged_as="BetaAudit.Demo.foo_le_bar")
    cfg = {"comparator.json": json.dumps({"theorem_names": ["BetaAudit.Demo.foo_le_bar"]}).encode()}
    cls = cc._classify([a, b], cfg, {"Demo.foo_le_bar", "Demo.baz"})
    assert [c["id"] for c in cls["covered"]] == ["beta/baz"]
    assert [c["id"] for c in cls["not_covered"]] == ["beta/foo-le-bar"]
    assert cls["judged_as_problems"] == []


def test_judged_as_problems_name_each_false_mapping(lp):
    import lean_pkg.comparator_config as cc
    c = claims_doc(judged_as="Audit.nowhere")["claims"][0]
    assert cc.judged_as_problems([c], {AUDIT}, {"Demo.foo_le_bar"}) == [
        "beta/foo-le-bar: judged_as 'Audit.nowhere' is in no comparator configuration's theorem_names"]
    c = claims_doc(judged_as=AUDIT, decl="Demo.nope")["claims"][0]
    assert "is not the fully qualified name of a declaration in Demo/Main.lean" in \
        cc.judged_as_problems([c], {AUDIT}, set())[0]


# --- receipt check: a referee on a fresh clone, no registry ----------------------------------------------

def audit_fixture(lp, env, claims: dict | None = None) -> str:
    """beta with its comparator configuration naming the Challenge theorem Audit.foo_bound (not the decl), and
    `claims` (default: the fixture's, no mapping) as claims.yaml: X (config, claims, page inputs), then the
    evidence in an evidence-only descendant. Returns X."""
    repo = env.repo_b
    shutil.rmtree(repo / ".lean-receipts")
    (repo / ".lean-receipts").mkdir()
    cfg = json.loads((repo / "comparator.json").read_text())
    cfg["theorem_names"] = [AUDIT]
    files = {"comparator.json": json.dumps(cfg, indent=2) + "\n",
             "claims.yaml": yaml.safe_dump(claims or claims_doc(), sort_keys=False), "Demo/Main.lean": DEMO_LEAN,
             "coverage.yaml": yaml.safe_dump(COVERAGE, sort_keys=False), "CITATION.cff": CITATION,
             "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST}
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", "-A", "--", *files)
    _git(repo, "commit", "-q", "-m", "audit namespace")
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
    return x


def check(lp, capsys, env, tmp_path, x, *argv):
    dest = tmp_path / f"referee-{len(list(tmp_path.glob('referee-*')))}" / "beta"
    dest.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(env.repo_b), str(dest)], check=True, capture_output=True)
    capsys.readouterr()
    rc = lp.main(["--json", "receipt", "check", "--repo", str(dest), "--commit", x, *argv])
    return rc, json.loads(capsys.readouterr().out)


def by_id(payload) -> dict:
    return {c["id"]: c for c in payload["checks"]}


def fails(payload) -> list[dict]:
    return [c for c in payload["checks"] if c["verdict"] == "fail"]


def test_without_a_mapping_the_audit_name_covers_nothing(lp, env, capsys, tmp_path):
    """The case of the two public index packages: the claims name the library theorem, the comparator judges
    an Audit name, and nothing in the repository connects them."""
    x = audit_fixture(lp, env)
    rc, a = check(lp, capsys, env, tmp_path, x)
    v = by_id(a)
    assert rc == 1 and v["binding"]["verdict"] == "pass"
    assert v["claimed theorems"]["verdict"] == "fail"
    assert "beta/foo-le-bar (Demo.foo_le_bar)" in v["claimed theorems"]["detail"]
    assert v["coverage"]["verdict"] == "fail" and AUDIT in v["coverage"]["detail"]   # R11
    assert a["tier"] == "lower-tier"


def test_a_mapping_in_the_bound_claims_passes(lp, env, capsys, tmp_path):
    """judged_as in claims.yaml at X: the claimed-theorems and coverage (R11) checks pass, tier comparator,
    from the repository alone (strict rule: the evidence commit is an evidence-only descendant)."""
    x = audit_fixture(lp, env, claims_doc(judged_as=AUDIT))
    rc, a = check(lp, capsys, env, tmp_path, x)
    v = by_id(a)
    assert rc == 0 and a["ok"] and a["tier"] == "comparator", fails(a)
    assert "1 covered, 1 by an explicit judged_as" in v["claimed theorems"]["detail"]
    assert v["coverage"]["verdict"] == "pass" and v["claims"]["verdict"] == "pass"


def test_a_mapping_added_after_the_bound_commit_does_not_rebind(lp, env, capsys, tmp_path):
    """claims.yaml is bound to X like the sources: a later commit that adds the mapping is neither evidence-only
    nor docs-only, so the receipts no longer certify the published ref (re-gate at the claims commit)."""
    x = audit_fixture(lp, env)
    write_claims(env.repo_b, claims_doc(judged_as=AUDIT))
    rc, a = check(lp, capsys, env, tmp_path, x, "--docs-descendants")
    v = by_id(a)
    assert rc == 1 and v["binding"]["verdict"] == "fail"
    assert "claims.yaml changed after the receipt commit" in v["binding"]["detail"]
    assert v["claimed theorems"]["verdict"] == "fail"              # read at X, where there is no mapping


def test_a_mapping_to_an_unconfigured_name_fails_whatever_the_status(lp, env, capsys, tmp_path):
    x = audit_fixture(lp, env, claims_doc(judged_as="Audit.not_compared", status="internal"))
    rc, a = check(lp, capsys, env, tmp_path, x)
    d = by_id(a)["claimed theorems"]
    assert rc == 1 and d["verdict"] == "fail"
    assert "judged_as 'Audit.not_compared' is in no comparator configuration's theorem_names" in d["detail"]


def test_a_mapping_for_an_undeclared_decl_fails(lp, env, capsys, tmp_path):
    x = audit_fixture(lp, env, claims_doc(judged_as=AUDIT, decl="Demo.nope", id="beta/nope"))
    rc, a = check(lp, capsys, env, tmp_path, x)
    v = by_id(a)
    assert rc == 1 and v["claims"]["verdict"] == "fail" and "'nope' does not occur" in v["claims"]["detail"]
    assert v["claimed theorems"]["verdict"] == "fail"
    assert "is not the fully qualified name of a declaration" in v["claimed theorems"]["detail"]


def test_a_reusable_claim_the_comparator_never_judged_still_fails(lp, env, capsys, tmp_path):
    """The mapping is no exemption: a second reusable theorem with no configured name fails as before."""
    d = claims_doc(judged_as=AUDIT)
    extra = dict(copy.deepcopy(d["claims"][0]), id="beta/foo-le-bar-again")
    extra.pop("judged_as")
    d["claims"].append(extra)
    x = audit_fixture(lp, env, d)
    rc, a = check(lp, capsys, env, tmp_path, x)
    det = by_id(a)["claimed theorems"]
    assert rc == 1 and det["verdict"] == "fail" and "beta/foo-le-bar-again (Demo.foo_le_bar)" in det["detail"]
    assert "beta/foo-le-bar " not in det["detail"].replace("beta/foo-le-bar-again", "")


# --- the author side reads the same mapping --------------------------------------------------------------

def test_ledger_and_coverage_credit_the_mapping(lp, env, capsys, tmp_path):
    x = audit_fixture(lp, env, claims_doc(judged_as=AUDIT))
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "--json", "comparator", "config", "--coverage", "beta"]) == 0
    cov = json.loads(capsys.readouterr().out)
    assert cov["bound_commit"] == x and cov["covered"] == 1 and cov["not_covered"] == 0, cov
    assert cov["judged_as_problems"] == []
    assert lp.main(["--registry", str(env.registry), "--json", "claims", "ledger", "beta"]) in (0, 1)
    led = json.loads(capsys.readouterr().out)
    row = next(r for r in led["claims"] if r["id"] == "beta/foo-le-bar")
    codes = {d["code"] for d in row["diagnostics"]}
    assert not codes & {"no-comparator-config", "comparator-todo"}, row
