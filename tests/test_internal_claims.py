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


# --- an internal claim no comparator judged reads "Built, not compared", not "work in progress" (field report) ---

BUILT_INTERNAL = ("**Built, not compared.** Lean accepts the proof (the build passed), but its statement has not "
                  "been compared by a second checker, which lean-pkg needs before it counts the result as verified.")


def test_an_internal_claim_no_comparator_judged_says_built_not_compared(lp, env, capsys, tmp_path):
    from .test_page import EXPERIMENTAL_CLAIM, TWO_CLAIMS, _block, _uncompared_doc, run
    unjudged = EXPERIMENTAL_CLAIM.replace("status: experimental", "status: internal")    # no config names it
    doc = _uncompared_doc(("Side fact", ["beta/baz-true"]), ("Both", ["beta/foo-le-bar", "beta/baz-true"]))
    prepare(env, doc, extra={**TWO_CLAIMS, "claims.yaml": CLAIMS_YAML + unjudged})
    (env.repo_b / "README.md").write_text("# Beta\n")
    rc, a = page_json(lp, env, capsys, "--write", "--site", str(tmp_path / "site"), "--html",
                      str(tmp_path / "v.html"))
    md = a["markdown"]
    assert rc == 0 and a["verified_rows"] == 1, a.get("problems")                    # counts unchanged
    for heading in ("Theorem 3: Side fact", "Theorem 4: Both"):
        block = _block(md, heading)
        assert block.startswith("\n" + BUILT_INTERNAL + "\n"), block
        assert "**Internal.** `beta/baz-true`" in block
    assert "work in progress" not in md and "Not yet verified" not in md
    assert "- Verified: 1 of the 3 result(s) in Lean" in md
    assert "| out-of-scope (internal) |" in (tmp_path / "site" / "coverage.md").read_text()  # the ledger's word
    for text in ((tmp_path / "site" / "verification.md").read_text(), (tmp_path / "v.html").read_text()):
        assert text.count("Built, not compared.") == 2 and "work in progress" not in text
    assert BUILT_INTERNAL in (env.repo_b / "README.md").read_text()
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--check", "--site", str(tmp_path / "site"))
    assert rc == 0


def test_an_internal_definition_keeps_not_yet_verified(lp, env, capsys):
    # the comparator judges no definition, so "not compared" would promise a check that never comes; a
    # superseded claim cannot appear in a coverage row at all (coverage check: name its successor)
    from .test_page import EXPERIMENTAL_CLAIM, TWO_CLAIMS, _block, _uncompared_doc
    other = (EXPERIMENTAL_CLAIM.replace("status: experimental", "status: internal")
             .replace("kind: theorem", "kind: definition"))
    prepare(env, _uncompared_doc(("Side fact", ["beta/baz-true"])),
            extra={**TWO_CLAIMS, "claims.yaml": CLAIMS_YAML + other})
    rc, a = page_json(lp, env, capsys)
    assert rc == 0 and "Built, not compared" not in a["markdown"], a
    assert _block(a["markdown"], "Theorem 3: Side fact").startswith("\n**Not yet verified.**")


def test_the_internal_built_not_compared_line_needs_a_clean_internal_theorem():
    from lean_pkg import page
    nc = {"code": "no-comparator-config", "detail": "x"}

    def oos(**kw):
        return {"status": "out-of-scope", "claim_status": "internal", "kind": "theorem", "diagnostics": [], **kw}
    by_id = {"i": oos(), "l": oos(kind="lemma"), "o": {"status": "open", "diagnostics": [nc]},
             "v": {"status": "verified", "diagnostics": []}, "d": oos(kind="definition"),
             "s": oos(claim_status="superseded"), "g": oos(diagnostics=[{"code": "no-checkout", "detail": "z"}]),
             "b": {"status": "blocked", "diagnostics": [{"code": "depends-on-open", "detail": "o z"}]}}
    for ids in (["i"], ["l", "v"], ["i", "o"], ["i", "l", "v"]):
        assert page._only_uncompared({"claims": ids}, by_id) == "internal", ids
    assert page._only_uncompared({"claims": ["o", "v"]}, by_id) == "required"
    for ids in (["d"], ["s"], ["g"], ["i", "b"], ["i", "missing"], ["v"], []):
        assert not page._only_uncompared({"claims": ids}, by_id), ids
    assert page._status_line("out-of-scope", False, None, "internal") == BUILT_INTERNAL
    assert page._status_line("open, out-of-scope", False, None, "internal") == BUILT_INTERNAL
    assert page._status_line("out-of-scope", False).startswith("**Not yet verified.**")
    assert page._status_line("open", False, None, "required").endswith("which this result requires.")
