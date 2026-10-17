#!/usr/bin/env python3
"""A comparator name covers a claim only by the claim's full declaration name (#80 soundness fix).

Field case: claim tw-monotone-tw1r (`RMTFoundations.TracyWidom.monotone_TW1R`) was credited as verified
by the promote ledger because comparator.json compared `RMTFoundations.TWOneMonotone.monotone_TW1R`, a
different declaration with the same last component."""

from __future__ import annotations

import importlib
import json

import yaml

from .conftest import put_receipt, commit_claims, make_comparator_receipt, make_receipt, read_reg, repo_head, write_reg

TW, TWONE = "RMTFoundations.TracyWidom.monotone_TW1R", "RMTFoundations.TWOneMonotone.monotone_TW1R"
LEAN = ("namespace RMTFoundations.TracyWidom\n\ntheorem monotone_TW1R : True := trivial\n\n"
        "end RMTFoundations.TracyWidom\n\nnamespace RMTFoundations\nnamespace TWOneMonotone\n\n"
        "theorem monotone_TW1R : True := trivial\n\nend TWOneMonotone\nend RMTFoundations\n\n"
        "theorem rootThm : True := trivial\n")


def claim(cid, decl):
    return {"id": f"beta/{cid}", "decl": decl, "kind": "theorem", "summary": f"{cid} summary",
            "file": "Demo/Main.lean", "status": "reusable", "superseded_by": None, "depends_on": []}


def gated(env, claims, names):
    """beta gated and compared at one commit tracking LEAN, claims.yaml and comparator.json (names)."""
    repo = env.repo_b
    cfg = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": list(names),
           "definition_names": [], "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
           "enable_nanoda": True}
    (repo / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    doc = {"schema": "claims/1", "package": "beta", "claims": claims}
    commit_claims(repo, yaml.safe_dump(doc, sort_keys=False), lean=LEAN)
    put_receipt(repo / ".lean-receipts" / "g.json", make_receipt(repo))
    (repo / ".lean-receipts" / "c.json").write_text(json.dumps(make_comparator_receipt(repo, "comparator.json")))
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(receipt=".lean-receipts/g.json", comparator=".lean-receipts/c.json")
    write_reg(env, reg)
    return repo_head(repo)


def ledger(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "claims", "ledger", "beta", "--stage", "promote", "--json"])
    data = json.loads(capsys.readouterr().out)
    return rc, data, {c["id"].split("/", 1)[1]: c for c in data["claims"]}


def codes(row):
    return [d["code"] for d in row["diagnostics"]]


# --- the rule itself --------------------------------------------------------------------------------

def test_covers_matches_the_full_name_never_a_shared_last_component(lp):
    cc = importlib.import_module("lean_pkg.comparator_config")
    assert not cc.covers({TWONE}, TW, True)                         # the field case
    assert cc.covers({TW}, TW, True)                                 # the declaration itself
    assert cc.covers({f"RmtFoundationsAudit.{TW}"}, TW, True)        # the scaffold's <Ns>.<decl>
    assert not cc.covers({"RmtFoundationsAudit.monotone_TW1R"}, TW, True)   # a short-name Challenge
    assert not cc.covers({"monotone_TW1R"}, TW, True)
    assert not cc.covers({f"X{TW}"}, TW, True)                       # a suffix only at a component boundary
    assert cc.covers({"Audit.Demo.«foo»"}, "Demo.foo", True)         # «» optional on a plain identifier
    for names in ({"monotone_TW1R"}, {"PkgAudit.monotone_TW1R"}, {TWONE}):
        assert not cc.covers(names, "monotone_TW1R", False)          # a bare, unresolved decl: nothing covers


def test_qualified_decls_resolves_namespaces_and_rejects_bare_or_partial_names(lp):
    cc = importlib.import_module("lean_pkg.comparator_config")
    claims = [claim("a", TW), claim("b", TWONE), claim("c", "monotone_TW1R"), claim("d", "TracyWidom.monotone_TW1R"),
              claim("e", "rootThm"), claim("f", "RMTFoundations.rootThm")]
    q = cc.qualified_decls(lambda rel: LEAN if rel == "Demo/Main.lean" else None, claims)
    assert q == {TW, TWONE, "rootThm"}


# --- the promote ledger, --coverage and the scaffold --------------------------------------------------

def test_field_case_a_namesake_in_another_namespace_leaves_the_claim_open(lp, env, capsys):
    gated(env, [claim("tw-monotone-tw1r", TW), claim("tw1-monotone", TWONE)], [TWONE])
    rc, data, rows = ledger(lp, env, capsys)
    assert rows["tw1-monotone"]["status"] == "verified"
    assert rows["tw-monotone-tw1r"]["status"] == "open"
    assert codes(rows["tw-monotone-tw1r"]) == ["no-comparator-config"]
    assert rc == 1 and data["package_status"] == "open"


def test_exact_and_scaffold_prefixed_names_cover_and_the_ledger_is_ready(lp, env, capsys):
    gated(env, [claim("tw-monotone-tw1r", TW), claim("tw1-monotone", TWONE), claim("root", "rootThm")],
          [TWONE, f"BetaAudit.{TW}", "BetaAudit.rootThm"])
    rc, data, rows = ledger(lp, env, capsys)
    assert {k: r["status"] for k, r in rows.items()} == {
        "tw-monotone-tw1r": "verified", "tw1-monotone": "verified", "root": "verified"}
    assert rc == 0 and data["package_status"] == "ready", data["reasons"]


def test_a_bare_decl_is_credited_to_no_name_and_says_why(lp, env, capsys):
    gated(env, [claim("bare", "monotone_TW1R")], ["BetaAudit.monotone_TW1R", TW])
    rc, data, rows = ledger(lp, env, capsys)
    assert rows["bare"]["status"] == "open" and codes(rows["bare"]) == ["no-comparator-config"]
    assert "not the fully qualified name" in rows["bare"]["diagnostics"][0]["detail"]
    assert rc == 1 and data["package_status"] == "open"


def test_coverage_and_scaffold_agree_with_the_ledger(lp, env, capsys):
    gated(env, [claim("tw-monotone-tw1r", TW), claim("tw1-monotone", TWONE)], [TWONE])
    assert lp.main(["--registry", str(env.registry), "comparator", "config", "--coverage", "beta", "--json"]) == 0
    cov = json.loads(capsys.readouterr().out)
    assert (cov["covered"], cov["not_covered"], cov["todo"]) == (1, 1, 0)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta",
                    "--dry-run", "--json"]) == 0
    out = capsys.readouterr().out
    pay = json.loads(out[:out.index("\n}\n") + 2])
    assert pay["added_names"] == [f"BetaAudit.{TW}"] and pay["existing"] == 1
