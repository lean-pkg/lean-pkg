#!/usr/bin/env python3
"""Field report #6: comparator names `<Root>Audit.X.y` (the project root replaced by the prefix) against claims
`<Root>.X.y` matched 0 claims, and nothing said why. The matching rule is unchanged (a name
covers a claim only when it ends in the whole fully qualified decl); `claims mark`, the promote ledger and
`comparator config --coverage` now state the rule and name a near miss that differs from a decl only in its
leading namespace component, never crediting it."""

from __future__ import annotations

import importlib
import json

import yaml

from .conftest import commit_claims
from .test_ledger_exact_covers import claim, codes, gated, ledger

LEAN = ("namespace Chemo\nnamespace Hopf\n\ntheorem cubic_coeff : True := trivial\n\n"
        "theorem first_lyapunov : True := trivial\n\nend Hopf\nend Chemo\n")
CUBIC, LYAP = "Chemo.Hopf.cubic_coeff", "Chemo.Hopf.first_lyapunov"


def cc():
    return importlib.import_module("lean_pkg.comparator_config")


def test_near_miss_rule_is_strict():
    m = cc().near_miss
    assert m("ChemoAudit.Hopf.cubic_coeff", CUBIC) == "root-replaced"          # the #6 shape
    assert m("Hopf.cubic_coeff", CUBIC) == "root-dropped"
    assert m("ChemoAudit.Hopf.first_lyapunov", CUBIC) is None                  # different suffix
    assert m("ChemoAudit.Other.cubic_coeff", CUBIC) is None                    # a middle component differs
    assert m("ChemoAudit.cubic_coeff", CUBIC) is None                          # only the last component shared
    assert m("ChemoAudit.Chemo.Hopf.cubic_coeff", CUBIC) is None               # that one covers: not a miss
    assert m("cubic_coeff", "cubic_coeff") is None                             # a one-component decl: no hint


def mark_setup(env, names):
    doc = {"schema": "claims/1", "package": "beta",
           "claims": [dict(claim("cubic", CUBIC), status="experimental"),
                      dict(claim("lyap", LYAP), status="experimental")]}
    commit_claims(env.repo_b, yaml.safe_dump(doc, sort_keys=False), lean=LEAN)
    (env.repo_b / "comparator.json").write_text(json.dumps({"theorem_names": list(names)}))


def mark(lp, env, capsys, json_out=True):
    rc = lp.main(["--registry", str(env.registry), "claims", "mark", "beta", "--from-comparator",
                  "comparator.json", "--status", "reusable"] + (["--json"] if json_out else []))
    out = capsys.readouterr().out
    return rc, (json.loads(out) if json_out else out)


def test_mark_root_dropped_names_match_nothing_and_say_why(lp, env, capsys):
    mark_setup(env, ["ChemoAudit.Hopf.cubic_coeff", "ChemoAudit.Hopf.first_lyapunov"])
    rc, data = mark(lp, env, capsys)
    assert rc == 0 and data["matched"] == [] and data["changed"] == 0
    assert "<Prefix>.<decl>" in data["rule"]
    assert [(h["name"], h["decl"], h["shape"], h["rename_to"]) for h in data["hints"]] == [
        ("ChemoAudit.Hopf.cubic_coeff", CUBIC, "root-replaced", f"ChemoAudit.{CUBIC}"),
        ("ChemoAudit.Hopf.first_lyapunov", LYAP, "root-replaced", f"ChemoAudit.{LYAP}")]
    rc, out = mark(lp, env, capsys, json_out=False)
    assert "rule: a comparator theorem name covers a claim only when it ends in the claim's whole" in out
    assert "hint: all 2 look like a claim's decl with its root namespace Chemo replaced by ChemoAudit" in out
    assert "rename the Challenge and Solution theorem" in out and f"ChemoAudit.{CUBIC}" in out


def test_mark_correct_names_still_match_with_no_hint(lp, env, capsys):
    mark_setup(env, [f"ChemoAudit.{CUBIC}", LYAP])
    rc, data = mark(lp, env, capsys)
    assert {m["decl"] for m in data["matched"]} == {CUBIC, LYAP}
    assert data["unmatched"] == [] and data["hints"] == []


def test_mark_negative_control_a_different_suffix_gives_no_hint(lp, env, capsys):
    mark_setup(env, ["ChemoAudit.Hopf.cubic_coefficient", "ChemoAudit.Other.cubic_coeff", "ChemoAudit.cubic_coeff"])
    rc, data = mark(lp, env, capsys)
    assert data["matched"] == [] and len(data["unmatched"]) == 3 and data["hints"] == []


def test_mark_partial_match_hints_only_the_near_miss(lp, env, capsys):
    mark_setup(env, [f"ChemoAudit.{CUBIC}", "ChemoAudit.Hopf.first_lyapunov"])
    rc, data = mark(lp, env, capsys)
    assert [m["decl"] for m in data["matched"]] == [CUBIC]
    assert [h["decl"] for h in data["hints"]] == [LYAP]


def test_ledger_and_coverage_name_the_near_miss_and_still_leave_the_claim_open(lp, env, capsys, monkeypatch):
    import tests.test_ledger_exact_covers as exact
    monkeypatch.setattr(exact, "LEAN", LEAN)                        # gate the Chemo declarations
    gated(env, [claim("cubic", CUBIC), claim("lyap", LYAP)],
          ["ChemoAudit.Hopf.cubic_coeff", f"ChemoAudit.{LYAP}"])
    rc, data, rows = ledger(lp, env, capsys)
    assert rows["lyap"]["status"] == "verified"
    assert rows["cubic"]["status"] == "open" and codes(rows["cubic"]) == ["no-comparator-config"]
    assert "hint: ChemoAudit.Hopf.cubic_coeff looks like the decl Chemo.Hopf.cubic_coeff" in \
        rows["cubic"]["diagnostics"][0]["detail"]
    assert lp.main(["--registry", str(env.registry), "comparator", "config", "--coverage", "beta", "--json"]) == 0
    cov = json.loads(capsys.readouterr().out)
    assert (cov["covered"], cov["not_covered"]) == (1, 1)
    assert [h["name"] for h in cov["hints"]] == ["ChemoAudit.Hopf.cubic_coeff"]
