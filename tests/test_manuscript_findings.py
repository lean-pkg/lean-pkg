#!/usr/bin/env python3
"""#6 field report: optional per-claim `manuscript_findings` (a misprint, a proof gap the formalization found
in the manuscript) and `statement_basis: corrected` in claims/1. Strict inside each finding; never a status."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import commit_claims, publish_evidence, read_reg, write_reg
from .test_claims_ledger import LEAN, by_id, claim, claims_list, gated, ledger

GAP_LEAN = LEAN.replace("end Demo\n", "theorem origin_jets_insufficient : True := trivial\nend Demo\n")


def misprint(fid="eq-3-7", fixed_in=None):
    return {"id": fid, "kind": "misprint", "location": {"label": "(3.7)", "tex_line": 412, "page": 9},
            "summary": "The exponent in the displayed bound should be 2p, not p.",
            "lean_evidence": ["Demo.foo_le_bar"], "fixed_in": fixed_in}


def gap(fid="gap-lemma-4-2", evidence=("Demo.origin_jets_insufficient",)):
    return {"id": fid, "kind": "proof-gap", "location": {"label": "Lemma 4.2"},
            "summary": "Origin jets alone do not determine the limit; Lean proves it by counterexample.",
            "lean_evidence": list(evidence), "fixed_in": None}


def with_findings(c, *findings, basis=None):
    out = {**c, "manuscript_findings": list(findings)}
    if basis is not None:
        out["statement_basis"] = basis
    return out


def doc(*claims):
    return {"schema": "claims/1", "package": "beta", "claims": list(claims)}


def good_claims():
    return claims_list(**{
        "foo-le-bar": with_findings(claim("foo-le-bar", "Demo.foo_le_bar"), misprint(), basis="corrected"),
        "baz": with_findings(claim("baz", "Demo.baz", kind="lemma"), gap(),
                             gap("gap-2", evidence=({"decl": "Demo.zap", "file": "Demo/Main.lean"},))),
    })


# --- schema ---------------------------------------------------------------------------------------

def test_valid_findings_are_accepted(cl):
    assert cl.validate_claims(doc(*good_claims()), "beta") == []


def test_a_file_without_findings_normalizes_as_before(cl):
    c = claim("foo-le-bar", "Demo.foo_le_bar")
    assert list(cl.normalize_claim(c)) == list(cl.CLAIM_KEYS)                 # cached shape unchanged
    assert list(cl.normalize_claim({**c, "manuscript_findings": [], "statement_basis": None})) == list(cl.CLAIM_KEYS)
    n = cl.normalize_claim(with_findings(c, gap()))
    assert n["manuscript_findings"][0]["location"] == {"label": "Lemma 4.2", "tex_line": None, "page": None}


def _errors(cl, mutate):
    c = with_findings(claim("baz", "Demo.baz"), gap())
    mutate(c)
    return cl.validate_claims(doc(c), "beta")


@pytest.mark.parametrize("mutate, needle", [
    (lambda c: c["manuscript_findings"][0].update(kind="typo"), "kind must be one of"),
    (lambda c: c["manuscript_findings"][0].update(severity="high"), "unknown key(s) ['severity']"),
    (lambda c: c["manuscript_findings"][0].pop("fixed_in"), "missing key(s) ['fixed_in']"),
    (lambda c: c["manuscript_findings"][0].update(fixed_in="arXiv:2501.01234 v2"), "fixed_in must be null or"),
    (lambda c: c["manuscript_findings"][0].update(fixed_in="v2"), "fixed_in must be null or"),
    (lambda c: c["manuscript_findings"][0]["location"].pop("label"), "location: missing key(s) ['label']"),
    (lambda c: c["manuscript_findings"][0]["location"].update(section="4"), "location: unknown key(s)"),
    (lambda c: c["manuscript_findings"][0]["location"].update(page=0), "location.page must be null or"),
    (lambda c: c["manuscript_findings"][0].update(id="Gap 1"), ".id must be a slug"),
    (lambda c: c["manuscript_findings"][0].update(summary="two\nlines"), ".summary must be one sentence"),
    (lambda c: c["manuscript_findings"][0].update(lean_evidence="Demo.baz"), "lean_evidence must be a list"),
    (lambda c: c["manuscript_findings"][0].update(lean_evidence=["not a name"]), "must name a Lean declaration"),
    (lambda c: c["manuscript_findings"][0].update(lean_evidence=[{"decl": "Demo.baz"}]), "exactly ['decl', 'file']"),
    (lambda c: c["manuscript_findings"][0].update(lean_evidence=[{"decl": "Demo.baz", "file": "x.py"}]),
     ".file must be a repo-relative .lean path"),
    (lambda c: c.update(manuscript_findings={"id": "x"}), "manuscript_findings must be a list"),
    (lambda c: c.update(statement_basis="fixed"), "statement_basis must be null or one of"),
    (lambda c: c.update(statement_basis="corrected"), "statement_basis corrected needs a misprint finding"),
    (lambda c: c.update(manuscript_findings=[misprint()]), "need statement_basis: corrected"),
    (lambda c: c.update(manuscript_findings=[misprint()], statement_basis="as-printed"),
     "need statement_basis: corrected"),
])
def test_each_malformed_finding_is_refused(cl, mutate, needle):
    errors = _errors(cl, mutate)
    assert any(needle in e for e in errors), errors


def test_fixed_in_accepts_new_and_old_style_arxiv_versions(cl):
    for fx in ("arXiv:2501.01234v2", "arXiv:1012.3456v11", "arXiv:math.PR/0601001v3"):
        assert _errors(cl, lambda c: c["manuscript_findings"][0].update(fixed_in=fx)) == [], fx


def test_finding_ids_are_unique_in_the_package(cl):
    c1 = with_findings(claim("baz", "Demo.baz"), gap("same"))
    c2 = with_findings(claim("zap", "Demo.zap"), gap("same"))
    assert any("duplicate manuscript finding id(s) ['same']" in e for e in cl.validate_claims(doc(c1, c2), "beta"))
    c3 = with_findings(claim("qux", "Demo.qux"), gap("twice"), gap("twice"))
    assert any("duplicate manuscript finding id(s) ['twice']" in e for e in cl.validate_claims(doc(c3), "beta"))


def test_a_private_path_in_a_finding_is_refused(cl):
    errors = _errors(cl, lambda c: c["manuscript_findings"][0].update(summary="See /home/someone/notes.txt now."))
    assert any("private local path" in e for e in errors), errors


def test_claims_check_file_validates_findings(lp, tmp_path, capsys):
    bad = with_findings(claim("baz", "Demo.baz"), {**gap(), "kind": "typo"})
    f = tmp_path / "claims.yaml"
    f.write_text(yaml.safe_dump(doc(bad), sort_keys=False))
    assert lp.main(["claims", "check", "--file", str(f)]) == 1
    assert "kind must be one of ('misprint', 'proof-gap')" in capsys.readouterr().out
    f.write_text(yaml.safe_dump(doc(*good_claims()), sort_keys=False))
    assert lp.main(["claims", "check", "--file", str(f)]) == 0


# --- the declaration check: sync, check, gate, ledger ----------------------------------------------

def _commit(env, claims, lean=GAP_LEAN):
    return commit_claims(env.repo_b, yaml.safe_dump(doc(*claims), sort_keys=False), lean=lean)


def test_sync_and_check_look_up_lean_evidence(lp, env, capsys):
    _commit(env, good_claims())
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    cached = {c["id"]: c for c in read_reg(env)["packages"]["beta"]["claims"]}
    assert cached["beta/foo-le-bar"]["statement_basis"] == "corrected"
    assert [f["id"] for f in cached["beta/baz"]["manuscript_findings"]] == ["gap-lemma-4-2", "gap-2"]
    assert "manuscript_findings" not in cached["beta/qux"]                     # unset keys are not cached
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta"]) == 0
    capsys.readouterr()
    _commit(env, good_claims(), lean=LEAN)                                      # the counterexample is gone
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 1
    err = capsys.readouterr().err
    assert "beta/baz finding gap-lemma-4-2 lean_evidence[0]: 'origin_jets_insufficient' does not occur" in err


def test_cached_evidence_missing_at_the_synced_commit_fails_check(lp, env, capsys):
    _commit(env, good_claims())
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    reg = read_reg(env)
    reg["packages"]["beta"]["claims"][1]["manuscript_findings"][0]["lean_evidence"] = ["Demo.never_written"]
    write_reg(env, reg)
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "claims", "check", "beta", "--json"]) == 1
    rep = json.loads(capsys.readouterr().out)["packages"][0]
    assert any("lean_evidence[0]: 'never_written' does not occur in Demo/Main.lean" in f for f in rep["failures"])


def test_gate_check_refuses_missing_lean_evidence_at_the_gated_commit(lp, env, capsys, tmp_path):
    from .conftest import make_receipt, write_receipt
    _commit(env, good_claims(), lean=LEAN)
    r = write_receipt(tmp_path, make_receipt(env.repo_b))
    rc = lp.main(["--registry", str(env.registry), "gate", "beta", "--receipt", str(r), "--check", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    probs = data["claims_schema"]["declarations"]["problems"]
    assert any("finding gap-lemma-4-2 lean_evidence[0]" in p for p in probs), probs


# --- ledger, publish, palomar: listed, summarized, statuses unchanged -------------------------------

def test_the_ledger_lists_findings_and_keeps_every_status(lp, env, capsys):
    gated(env)
    rc0, plain = ledger(lp, env, capsys)
    assert plain["manuscript_findings"]["total"] == 0
    names = ("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.baz", "BetaAudit.Demo.zap")
    claims = claims_list(**{
        "foo-le-bar": with_findings(claim("foo-le-bar", "Demo.foo_le_bar"), misprint(fixed_in="arXiv:2501.01234v2"),
                                    basis="corrected"),
        "baz": with_findings(claim("baz", "Demo.baz", kind="lemma"), gap(evidence=("Demo.qux",)),
                             gap("gap-2", evidence=({"decl": "Demo.zap", "file": "Demo/Main.lean"},))),
    })
    gated(env, claims=claims, names=names)
    rc, data = ledger(lp, env, capsys)
    assert (rc, data["package_status"]) == (rc0, plain["package_status"]) == (0, "ready")
    assert {k: r["status"] for k, r in by_id(data).items()} == {k: r["status"] for k, r in by_id(plain).items()}
    rows = by_id(data)
    assert rows["foo-le-bar"]["statement_basis"] == "corrected" and rows["qux"]["statement_basis"] is None
    assert [f["state"] for f in rows["baz"]["manuscript_findings"]] == ["open", "open"]
    s = data["manuscript_findings"]
    assert (s["total"], s["open"], s["fixed"], s["by_kind"]) == (3, 2, 1, {"misprint": 1, "proof-gap": 2})
    assert s["corrected_statements"] == ["beta/foo-le-bar"]
    assert {f["id"]: f["state"] for f in s["findings"]} == {"eq-3-7": "fixed", "gap-lemma-4-2": "open",
                                                            "gap-2": "open"}
    assert data["ledger_digest"] != plain["ledger_digest"]                     # the digest covers the findings
    lp.main(["--registry", str(env.registry), "claims", "ledger", "beta"])
    out = capsys.readouterr().out
    assert "manuscript findings (what the formalization found): 3 (1 misprint, 2 proof-gap); 2 open, 1 fixed" in out
    assert "beta/foo-le-bar (Demo.foo_le_bar) [corrected statement]" in out
    assert "finding eq-3-7 (misprint; (3.7), page 9, tex line 412): fixed in arXiv:2501.01234v2:" in out
    assert out.splitlines()[0].startswith("beta: claims ledger (promote stage): READY")


def test_a_ledger_without_findings_keeps_its_digest(lp, env, capsys):
    ledger_mod = __import__("lean_pkg.ledger", fromlist=["x"])
    row = {"id": "beta/x", "decl": "D.x", "kind": "theorem", "claim_status": "reusable", "depends_on": [],
           "status": "verified", "diagnostics": [], "statement_basis": None, "manuscript_findings": []}
    assert set(ledger_mod._canonical_row(row)) == {"id", "decl", "kind", "claim_status", "depends_on", "status",
                                                    "codes"}


def test_publish_dry_run_surfaces_the_summary_without_changing_readiness(lp, env, fake_gh, capsys):
    gated(env)
    publish_evidence(env.repo_b)
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run", "--json"])
    before = json.loads(capsys.readouterr().out)
    gated(env, claims=claims_list(**{
        "baz": with_findings(claim("baz", "Demo.baz", kind="lemma"), gap(evidence=("Demo.qux",)))}))
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["manuscript_findings"]["total"] == 1 and data["manuscript_findings"]["open"] == 1
    assert data["claims_ledger"]["package_status"] == before["claims_ledger"]["package_status"]
    assert [c["status"] for c in data["checklist"]] == [c["status"] for c in before["checklist"]]
    assert data["ready"] == before["ready"] and rc == (0 if data["ready"] else 1)
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run"])
    out = capsys.readouterr().out
    assert sum(line.startswith("manuscript findings (what the formalization found): 1 (1 proof-gap)")
               for line in out.splitlines()) == 1


def test_publish_without_findings_prints_no_line(lp, env, fake_gh, capsys):
    gated(env)
    publish_evidence(env.repo_b)
    lp.main(["--registry", str(env.registry), "publish", "beta", "--dry-run"])
    assert "manuscript findings" not in capsys.readouterr().out


def test_palomar_prepare_surfaces_the_summary(lp, env, capsys, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("net")))
    reg = read_reg(env)
    reg["packages"]["beta"]["consent"] = [{"who": "Formalizer One", "role": "owner", "pointer": "consent/one.md",
                                           "recorded_at": "2026-10-09T00:00:00+00:00"}]
    reg["packages"]["beta"]["license_spdx"] = "MIT"
    write_reg(env, reg)
    gated(env, claims=claims_list(**{
        "foo-le-bar": with_findings(claim("foo-le-bar", "Demo.foo_le_bar"), misprint(), basis="corrected")}))
    publish_evidence(env.repo_b)
    rc = lp.main(["--registry", str(env.registry), "palomar", "prepare", "beta", "--dry-run", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0, data
    s = data["manuscript_findings"]
    assert (s["total"], s["open"], s["by_kind"]["misprint"], s["corrected_statements"]) == \
        (1, 1, 1, ["beta/foo-le-bar"])
    assert lp.main(["--registry", str(env.registry), "palomar", "prepare", "beta", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "  manuscript findings (what the formalization found): 1 (1 misprint); 1 open, 0 fixed" in out
    assert "verified for the corrected statement: beta/foo-le-bar" in out
