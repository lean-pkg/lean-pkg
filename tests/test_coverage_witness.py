#!/usr/bin/env python3
"""Proposal 0011: non-vacuity witnesses on coverage rows. claims/1 accepts `witness` on any claim, not only a
conditional one, and a claim that sets only `witness` stays verified when the facts to judge it are missing;
coverage/1 rows (paper and library) gain `witness`, the claims.yaml witness of one of the row's claims, refused
on an outside row and when it is not such a witness; the page shows **Hypotheses can be met.** by the ledger's
state, a header clause and one sentence in the reading note; --json gains `witnesses` (coverage check) and
`verified_with_witness` (page, only when a row names a witness)."""

from __future__ import annotations

import json

import yaml

from lean_pkg import hypotheses as hyp
from lean_pkg import page as page_mod

from .conftest import CLAIMS_YAML
from .test_coverage_offline import offline, package
from .test_page import COVERAGE, EXPERIMENTAL_CLAIM, LIBRARY, _block, _refused, cov_check, mutate, page_json, prepare

WIT = "beta/baz-true"
# beta/foo-le-bar (Demo.foo_le_bar) names Demo.baz_true as its witness; both are theorems of Demo/Main.lean
WITNESSED = {"claims.yaml": CLAIMS_YAML + f"  witness: {WIT}\n" + EXPERIMENTAL_CLAIM,
             "Demo/Main.lean": "namespace Demo\n\ntheorem foo_le_bar : True := trivial\n\n"
                               "theorem baz_true : True := trivial\n\nend Demo\n"}


def _doc(**keys) -> dict:
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"][0].update(keys)
    return doc


# --- claims/1 and the ledger ----------------------------------------------------------------------

def _evaluate(claims: list[dict], fx: dict) -> dict:
    return hyp.evaluate(claims, fx, stage="promote", bound="0" * 40, comparator_ok=True,
                        credited={}, clean={str(c["id"]): True for c in claims})


def test_a_witness_only_claim_is_not_demoted_when_the_facts_are_missing():
    claims = [{"id": "p/main", "decl": "P.main", "kind": "theorem", "witness": "p/wit"},
              {"id": "p/wit", "decl": "P.wit", "kind": "theorem"}]
    out = _evaluate(claims, {"ok": False, "why": "the sealed verify log has no hyp-probe/1 section"})
    s = out["p/main"]
    assert s["diags"] == []                                     # no hypotheses-unchecked: the claim keeps its status
    assert s["witness"] == {"claim": "p/wit", "state": "unchecked",
                            "why": "the sealed verify log has no hyp-probe/1 section"}


def test_a_conditional_claim_with_a_witness_is_still_unchecked_when_the_facts_are_missing():
    claims = [{"id": "p/main", "decl": "P.main", "kind": "theorem", "hypotheses": ["Hyp.A"], "witness": "p/wit"},
              {"id": "p/wit", "decl": "P.wit", "kind": "theorem"}]
    out = _evaluate(claims, {"ok": False, "why": "no section"})
    assert [code for code, _ in out["p/main"]["diags"]] == ["hypotheses-unchecked"]   # proposal 0003, unchanged
    assert "why" not in out["p/main"]["witness"]


# --- coverage/1: validation ---------------------------------------------------------------------

def test_a_row_witness_must_be_the_witness_of_one_of_its_claims(lp, env, capsys, tmp_path):
    prepare(env, extra=WITNESSED)
    errs = _refused(lp, env, capsys, tmp_path, mutate(rows=[dict(COVERAGE["rows"][0], witness="beta/foo-le-bar"),
                                                            COVERAGE["rows"][1]]))
    assert any("rows[0].witness: 'beta/foo-le-bar' is not the witness of any claim the row names (beta/foo-le-bar)"
               in e and "set witness: beta/foo-le-bar on the claim" in e for e in errs), errs
    rc, data = cov_check(lp, env, capsys)                       # the fixture's own coverage: no row key, valid
    assert rc == 0 and data["witnesses"] == {"rows": 0, "checked": 0, "unchecked": 0, "mismatch": 0}


def test_a_row_witness_is_a_claim_id_and_not_on_an_outside_row(lp, env, capsys, tmp_path):
    prepare(env, extra=WITNESSED)
    for bad in ("baz-true", ["beta/baz-true"], 3):
        errs = _refused(lp, env, capsys, tmp_path, mutate(rows=[dict(COVERAGE["rows"][0], witness=bad),
                                                                COVERAGE["rows"][1]]))
        assert any("rows[0].witness: must be a claim id <package>/<slug>" in e for e in errs), (bad, errs)
    errs = _refused(lp, env, capsys, tmp_path, mutate(rows=[COVERAGE["rows"][0],
                                                            dict(COVERAGE["rows"][1], witness=WIT)]))
    assert any("rows[1]: an outside row has no Lean statement, so no hypotheses to witness: drop witness" in e
               for e in errs), errs


def test_a_library_row_may_name_a_witness(lp, env, capsys, tmp_path):
    doc = json.loads(json.dumps(LIBRARY))
    doc["rows"][0]["witness"] = WIT
    prepare(env, doc, extra=WITNESSED)
    rc, data = cov_check(lp, env, capsys)
    assert rc == 0 and data["ok"], data["errors"]
    assert data["witnesses"] == {"rows": 1, "checked": 0, "unchecked": 1, "mismatch": 0}


def test_the_offline_check_validates_the_witness_against_the_claims_file(lp, capsys, tmp_path):
    d = package(tmp_path, _doc(witness=WIT))
    (d / "claims.yaml").write_text(WITNESSED["claims.yaml"])
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and data["witnesses"] == {"rows": 1, "checked": None, "unchecked": None, "mismatch": None}
    assert any(c["check"] == "row witnesses" and c["status"] == "pass" for c in data["checks"])
    (d / "claims.yaml").write_text(CLAIMS_YAML + EXPERIMENTAL_CLAIM)             # nobody names the witness
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and any("is not the witness of any claim the row names" in e for e in data["errors"])
    d = package(tmp_path, _doc())                                               # no key: no new check
    rc, data, _err = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and not any(c["check"] == "row witnesses" for c in data["checks"])


# --- rendering ----------------------------------------------------------------------------------

def test_an_unchecked_witness_keeps_the_result_verified_and_says_why(lp, env, capsys, tmp_path):
    prepare(env, _doc(witness=WIT), extra=WITNESSED)
    site, html = tmp_path / "site", tmp_path / "v.html"
    rc, a = page_json(lp, env, capsys, "--site", str(site), "--html", str(html))
    assert rc == 0, a.get("problems")
    assert a["verified_rows"] == 1 and a["verified_with_witness"] == 0
    block = _block(a["markdown"], "Theorem 1: Main bound")
    assert block.startswith("\n**Verified.**")
    assert "\n\n**Hypotheses can be met:** claimed by `Demo.baz_true`, not yet checked (" in block
    assert block.index("**Hypotheses.**") < block.index("**Hypotheses can be met:**")
    assert page_mod.WITNESS_SENTENCE in a["markdown"]
    assert ("- Verified: 1 of the 1 result(s) in Lean; 0 of the verified have a checked witness that their "
            "hypotheses can be met.\n") in a["markdown"]
    assert "| Same assumptions<br>Witness: not yet checked |" in (site / "coverage.md").read_text()
    assert "*conditional* means" not in (site / "coverage.md").read_text()      # no conditional claim here


def test_a_page_without_a_row_witness_has_no_new_line_key_or_sentence(lp, env, capsys, tmp_path):
    prepare(env, extra=WITNESSED)               # the claim-level witness alone: the page says nothing new
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "verified_with_witness" not in a
    assert "Hypotheses can be met" not in a["markdown"] and page_mod.WITNESS_SENTENCE not in a["markdown"]
    assert "have a checked witness" not in a["markdown"]


def test_the_line_says_checked_unchecked_or_wrong_type():
    ctx = {"claims": {"beta/foo-le-bar": {"decl": "Demo.foo_le_bar", "witness": WIT},
                      WIT: {"decl": "Demo.baz_true"}}}
    row = {"claims": ["beta/foo-le-bar"], "witness": WIT}

    def line(state, **extra):
        by_id = {"beta/foo-le-bar": {"witness": {"claim": WIT, "state": state, **extra}}}
        return page_mod._witness_line(ctx, row, by_id)[0]
    assert line("checked") == ("**Hypotheses can be met.** Lean proves that some instance satisfies all of them "
                               "together (`Demo.baz_true`, checked by lean-pkg's probe against the Challenge).")
    assert line("mismatch") == ("**Hypotheses can be met:** the named witness `Demo.baz_true` does not have the "
                                "expected type, so nothing is shown.")
    assert line("unchecked") == "**Hypotheses can be met:** claimed by `Demo.baz_true`, not yet checked."
    assert line("unchecked", why="no facts").endswith("not yet checked (no facts).")
    assert page_mod._witness_line(ctx, {"claims": ["beta/foo-le-bar"]}, {}) == []
    assert yaml.safe_load(WITNESSED["claims.yaml"])["claims"][0]["witness"] == WIT
