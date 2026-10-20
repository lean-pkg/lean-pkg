#!/usr/bin/env python3
"""Internal claims judged by a passing comparator (claims ledger, promote stage): a theorem or lemma with
claims.yaml status `internal` whose declaration a configuration names, with the comparator tier clean for
the package (a valid receipt bound to the gate-bound commit), is checked like a reusable claim and can be
verified; it is never required. Every other internal claim stays out of scope: one the comparator did not
judge is never credited."""

from __future__ import annotations

import json

from .conftest import CLAIMS_YAML, _git
from .test_claims_ledger import by_id, claim, claims_list, codes, gated, ledger, set_gate
from .test_page import page_json, prepare


def internal(cid, decl, kind="theorem", **kw):
    return claim(cid, decl, kind=kind, status="internal", **kw)


def test_an_internal_theorem_the_comparator_judged_is_verified_never_required(lp, env, capsys):
    gated(env, claims=claims_list(baz=internal("baz", "Demo.baz", kind="lemma")))
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rows["baz"]["status"] == "verified" and rows["baz"]["judged_internal"] is True
    assert rows["baz"]["required"] is False and rows["baz"]["diagnostics"] == []
    assert rc == 0 and data["package_status"] == "ready" and data["required"] == 3      # baz is not counted
    assert data["counts"]["out-of-scope"] == 1                                         # old-one only
    lp.main(["--registry", str(env.registry), "claims", "ledger", "beta"])
    out = capsys.readouterr().out
    assert "verified     beta/baz (Demo.baz) [internal, judged by the comparator]" in out


def test_an_internal_claim_the_comparator_did_not_judge_stays_out_of_scope(lp, env, capsys):
    gated(env, claims=claims_list(baz=internal("baz", "Demo.baz", kind="lemma")),
          names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))
    rows = by_id(ledger(lp, env, capsys)[1])
    assert rows["baz"]["status"] == "out-of-scope" and rows["baz"]["judged_internal"] is False
    assert rows["zap"]["status"] == "verified"               # an out-of-scope dependency blocks nothing, as before


def test_a_name_matching_only_the_last_component_judges_nothing(lp, env, capsys):
    # the exact-name rule of a reusable claim (#140): `Other.baz` or a bare `baz` is not `Demo.baz`
    gated(env, claims=claims_list(baz=internal("baz", "Demo.baz", kind="lemma")),
          names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap", "BetaAudit.Other.baz", "baz"))
    rows = by_id(ledger(lp, env, capsys)[1])
    assert rows["baz"]["status"] == "out-of-scope" and rows["baz"]["judged_internal"] is False


def test_a_scaffold_todo_name_judges_nothing(lp, env, capsys):
    gated(env, claims=claims_list(baz=internal("baz", "Demo.baz", kind="lemma")), todo=("BetaAudit.Demo.baz",))
    assert by_id(ledger(lp, env, capsys)[1])["baz"]["status"] == "out-of-scope"


def test_no_passing_bound_comparator_receipt_no_credit(lp, env, capsys):
    claims = claims_list(baz=internal("baz", "Demo.baz", kind="lemma"))
    gated(env, claims=claims, comparator=False)                                 # no comparator receipt
    assert by_id(ledger(lp, env, capsys)[1])["baz"]["status"] == "out-of-scope"
    gated(env, claims=claims)                                                   # bound to another commit
    comp = json.loads((env.repo_b / ".lean-receipts" / "c.json").read_text())
    comp["commit"] = _git(env.repo_b, "rev-parse", "HEAD~1").stdout.strip()
    (env.repo_b / ".lean-receipts" / "c.json").write_text(json.dumps(comp))
    assert by_id(ledger(lp, env, capsys)[1])["baz"]["status"] == "out-of-scope"
    gated(env, claims=claims)                                                   # a tampered log: invalid
    log = env.repo_b / json.loads((env.repo_b / ".lean-receipts" / "c.json").read_text())["configs"][0]["log"]
    log.write_text(log.read_text() + "tampered\n")
    assert by_id(ledger(lp, env, capsys)[1])["baz"]["status"] == "out-of-scope"
    gated(env, claims=claims)
    set_gate(env, comparator=".lean-receipts/missing-c.json")                   # unreadable
    assert by_id(ledger(lp, env, capsys)[1])["baz"]["status"] == "out-of-scope"


def test_gate_stage_definitions_and_superseded_claims_are_never_judged(lp, env, capsys):
    gated(env, claims=claims_list(baz=internal("baz", "Demo.baz", kind="lemma"),
                                  qux=internal("qux", "Demo.qux", kind="definition")),
          names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.baz", "BetaAudit.Demo.zap", "BetaAudit.Demo.old_one"))
    rows = by_id(ledger(lp, env, capsys)[1])
    assert rows["qux"]["status"] == "out-of-scope"           # a definition: the comparator judges no body
    assert rows["old-one"]["status"] == "out-of-scope"       # superseded, though a configuration names it
    assert rows["baz"]["status"] == "verified"
    assert by_id(ledger(lp, env, capsys, stage="gate")[1])["baz"]["status"] == "out-of-scope"


def test_a_judged_internal_claim_is_blocked_by_an_open_dependency(lp, env, capsys):
    gated(env, claims=claims_list(zap=internal("zap", "Demo.zap", deps=["beta/baz"])),
          names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))
    rows = by_id(ledger(lp, env, capsys)[1])
    assert rows["baz"]["status"] == "open" and rows["zap"]["status"] == "blocked"
    assert codes(rows["zap"]) == ["depends-on-open"]


def test_an_internal_claim_alone_still_leaves_the_package_unknown(lp, env, capsys):
    gated(env, claims=[internal("foo-le-bar", "Demo.foo_le_bar")])
    rc, data = ledger(lp, env, capsys)
    assert rc == 1 and data["package_status"] == "unknown" and data["required"] == 0
    assert by_id(data)["foo-le-bar"]["status"] == "verified"


def test_the_page_marks_a_verified_internal_claim(lp, env, capsys, tmp_path):
    prepare(env, extra={"claims.yaml": CLAIMS_YAML.replace("status: reusable", "status: internal")})
    rc, a = page_json(lp, env, capsys, "--site", str(tmp_path / "site"))
    md = a["markdown"]
    block = md.split("### Theorem 1: Main bound\n", 1)[1].split("\n## ", 1)[0]
    assert block.startswith("\n**Verified.** Lean accepts the proof") and "statement comparison" in block
    assert "**Internal.** `beta/foo-le-bar`: marked internal in `claims.yaml`" in block
    assert a["verified_rows"] == 1 and a["ledger_status"] == "unknown"        # nothing required: unchanged
    cov = (tmp_path / "site" / "coverage.md").read_text()
    assert "| verified (internal) |" in cov
