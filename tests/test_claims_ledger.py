#!/usr/bin/env python3
"""#80: `claims ledger NAME` gives each claim named diagnostics from the recorded evidence; a claim is
verified only with zero diagnostics, and the package status fails closed."""

from __future__ import annotations

import importlib
import json

import pytest
import yaml

from .conftest import (ROOT, _git, commit_claims, make_comparator_receipt, make_receipt, read_events, read_reg,
                       repo_head, write_reg)

LEAN = ("theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\ndef qux := 1\n"
        "theorem old_one : True := trivial\ntheorem zap : True := trivial\n")
ALL_NAMES = ("foo_le_bar", "baz", "zap")


def claim(cid, decl, kind="theorem", status="reusable", sb=None, deps=()):
    return {"id": f"beta/{cid}", "decl": decl, "kind": kind, "summary": f"{cid} summary",
            "file": "Demo/Main.lean", "status": status, "superseded_by": sb, "depends_on": list(deps)}


def claims_list(**change):
    rows = {
        "foo-le-bar": claim("foo-le-bar", "Demo.foo_le_bar"),
        "baz": claim("baz", "Demo.baz", kind="lemma"),
        "qux": claim("qux", "Demo.qux", kind="definition"),         # never compared
        "old-one": claim("old-one", "Demo.old_one", status="superseded", sb="beta/zap"),
        "zap": claim("zap", "Demo.zap", deps=["beta/baz"]),
    }
    rows.update(change)
    return [r for r in rows.values() if r is not None]


def gated(env, claims=None, names=ALL_NAMES, todo=(), receipt=None, comparator=True):
    """beta gated (and, unless comparator=False, compared) at one commit tracking LEAN, claims.yaml and
    comparator.json; the receipts are written untracked under .lean-receipts/ and recorded in the registry."""
    repo = env.repo_b
    cfg = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": list(names),
           "definition_names": [], "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
           "enable_nanoda": True}
    if todo:
        cfg["lean_pkg_scaffold"] = {"todo": [{"theorem_name": t} for t in todo]}
    (repo / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    doc = {"schema": "claims/1", "package": "beta", "claims": claims_list() if claims is None else claims}
    commit_claims(repo, yaml.safe_dump(doc, sort_keys=False), lean=LEAN)
    (repo / ".lean-receipts" / "g.json").write_text(json.dumps(make_receipt(repo, **(receipt or {}))))
    comp_rel = None
    if comparator:
        comp_rel = ".lean-receipts/c.json"
        (repo / comp_rel).write_text(json.dumps(make_comparator_receipt(repo, "comparator.json")))
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(receipt=".lean-receipts/g.json", comparator=comp_rel)
    write_reg(env, reg)
    return repo_head(repo)


def set_gate(env, **kw):
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(kw)
    write_reg(env, reg)


def ledger(lp, env, capsys, stage="promote"):
    rc = lp.main(["--registry", str(env.registry), "claims", "ledger", "beta", "--stage", stage, "--json"])
    return rc, json.loads(capsys.readouterr().out)


def by_id(data):
    return {c["id"].split("/", 1)[1]: c for c in data["claims"]}


def codes(row):
    return [d["code"] for d in row["diagnostics"]]


# --- the status rules -----------------------------------------------------------------------------

def test_ready_only_when_every_required_claim_is_verified(lp, env, capsys):
    g = gated(env)
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rc == 0 and data["ok"] is True and data["package_status"] == "ready", data["reasons"]
    assert data["bound_commit"] == g and data["required"] == 4
    assert {k: r["status"] for k, r in rows.items()} == {
        "foo-le-bar": "verified", "baz": "verified", "qux": "verified", "old-one": "out-of-scope", "zap": "verified"}
    assert all(r["diagnostics"] == [] for r in rows.values())
    lp.main(["--registry", str(env.registry), "claims", "ledger", "beta"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("beta: claims ledger (promote stage): READY")       # verdict first
    assert out[-1] == out[0]                                                    # and last, the list is long


def test_lower_tier_alone_never_closes_a_claim_promote_needs(lp, env, capsys):
    gated(env, comparator=False)
    rc, data = ledger(lp, env, capsys, stage="gate")
    assert rc == 0 and data["package_status"] == "ready"
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rc == 1 and data["package_status"] == "open"
    for k in ("foo-le-bar", "baz", "zap"):
        assert rows[k]["status"] in ("open", "blocked") and "no-comparator-receipt" in codes(rows[k])
    assert rows["qux"]["status"] == "verified"            # a definition needs no comparator


def test_an_open_dependency_propagates_blocked(lp, env, capsys):
    extra = claim("top", "Demo.foo_le_bar", deps=["beta/zap"])          # top -> zap -> baz
    gated(env, claims=claims_list(top=extra), names=("foo_le_bar", "zap"))
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rows["baz"]["status"] == "open" and codes(rows["baz"]) == ["no-comparator-config"]
    assert rows["zap"]["status"] == "blocked" and codes(rows["zap"]) == ["depends-on-open"]
    assert rows["top"]["status"] == "blocked" and codes(rows["top"]) == ["depends-on-open"]
    assert rc == 1 and data["package_status"] == "open"


def test_an_empty_claims_set_is_unknown_not_ready(lp, env, capsys):
    gated(env, claims=[])
    rc, data = ledger(lp, env, capsys)
    assert rc == 1 and data["package_status"] == "unknown" and data["claims"] == []
    assert any("no claims" in r for r in data["reasons"])


def test_no_claims_file_and_no_cache_is_unknown(lp, env, capsys):
    rc, data = ledger(lp, env, capsys)                   # the fixture's beta: receipts, no claims anywhere
    assert rc == 1 and data["package_status"] == "unknown" and data["claims"] == []


def test_no_reusable_claim_is_unknown(lp, env, capsys):
    gated(env, claims=[claim("foo-le-bar", "Demo.foo_le_bar", status="internal")])
    rc, data = ledger(lp, env, capsys)
    assert rc == 1 and data["package_status"] == "unknown"
    assert by_id(data)["foo-le-bar"]["status"] == "out-of-scope"


def test_contradicting_evidence_fails_the_package(lp, env, capsys):
    gated(env, claims=claims_list(baz=claim("baz", "Demo.nope", kind="lemma")))
    rc, data = ledger(lp, env, capsys)
    assert rc == 1 and data["package_status"] == "failed"
    assert "decl-missing" in codes(by_id(data)["baz"]) and "beta/baz" in data["reasons"][-1]


def test_experimental_claims_are_reported_but_not_required(lp, env, capsys):
    gated(env, claims=claims_list(baz=claim("baz", "Demo.baz", kind="lemma", status="experimental"),
                                  zap=claim("zap", "Demo.zap")), names=("foo_le_bar", "zap"))
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rows["baz"]["required"] is False and rows["baz"]["status"] == "verified"   # comparator not asked
    assert rc == 0 and data["package_status"] == "ready"


# --- one scenario per diagnostic code -------------------------------------------------------------

def _no_checkout(lp, env, capsys):
    gated(env)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0   # the cache is all that is left
    capsys.readouterr()
    reg = read_reg(env)
    reg["packages"]["beta"]["local_path"] = str(env.repo_b.parent / "gone")
    write_reg(env, reg)


def _no_gate_receipt(lp, env, capsys):
    gated(env)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    set_gate(env, receipt=None)


def _receipt_unreadable(lp, env, capsys):
    gated(env)
    set_gate(env, receipt=".lean-receipts/missing.json")


def _bound_commit_missing(lp, env, capsys):
    gated(env, receipt={"commit": "f" * 40})


def _receipt_commit_mismatch(lp, env, capsys):
    gated(env)
    (env.repo_b / "Demo" / "Extra.lean").write_text("-- later work\n")
    _git(env.repo_b, "add", "-A")
    _git(env.repo_b, "commit", "-m", "later")


def _receipt_invalid(lp, env, capsys):
    gated(env, receipt={"sorry_count": 1})


def _axiom_not_allowed(lp, env, capsys):
    gated(env, receipt={"axioms": ["propext", "sorryAx"]})


def _decl_missing(lp, env, capsys):
    gated(env, claims=claims_list(**{"foo-le-bar": claim("foo-le-bar", "Demo.gone")}))


def _no_comparator_receipt(lp, env, capsys):
    gated(env, comparator=False)


def _comparator_unreadable(lp, env, capsys):
    gated(env)
    set_gate(env, comparator=".lean-receipts/missing-c.json")


def _comparator_commit_mismatch(lp, env, capsys):
    gated(env)
    comp = json.loads((env.repo_b / ".lean-receipts" / "c.json").read_text())
    comp["commit"] = _git(env.repo_b, "rev-parse", "HEAD~1").stdout.strip()
    (env.repo_b / ".lean-receipts" / "c.json").write_text(json.dumps(comp))


def _comparator_invalid(lp, env, capsys):
    gated(env)
    log = env.repo_b / json.loads((env.repo_b / ".lean-receipts" / "c.json").read_text())["configs"][0]["log"]
    log.write_text(log.read_text() + "tampered\n")


def _no_comparator_config(lp, env, capsys):
    gated(env, names=("foo_le_bar", "zap"))


def _comparator_todo(lp, env, capsys):
    gated(env, names=("foo_le_bar", "BetaAudit.baz", "zap"), todo=("BetaAudit.baz",))


def _depends_on_open(lp, env, capsys):
    gated(env, names=("foo_le_bar", "zap"))


def _depends_on_unknown(lp, env, capsys):
    gated(env, claims=claims_list(zap=claim("zap", "Demo.zap", deps=["other-pkg/thing"])))


# code -> (scenario, the claim it lands on, that claim's status, the package status)
SCENARIOS = {
    "no-checkout": (_no_checkout, "foo-le-bar", "unknown", "unknown"),
    "no-gate-receipt": (_no_gate_receipt, "foo-le-bar", "open", "open"),
    "receipt-unreadable": (_receipt_unreadable, "foo-le-bar", "unknown", "unknown"),
    "bound-commit-missing": (_bound_commit_missing, "foo-le-bar", "unknown", "unknown"),
    "receipt-commit-mismatch": (_receipt_commit_mismatch, "foo-le-bar", "open", "open"),
    "receipt-invalid": (_receipt_invalid, "foo-le-bar", "open", "failed"),
    "axiom-not-allowed": (_axiom_not_allowed, "foo-le-bar", "open", "failed"),
    "decl-missing": (_decl_missing, "foo-le-bar", "open", "failed"),
    "no-comparator-receipt": (_no_comparator_receipt, "foo-le-bar", "open", "open"),
    "comparator-unreadable": (_comparator_unreadable, "foo-le-bar", "unknown", "unknown"),
    "comparator-commit-mismatch": (_comparator_commit_mismatch, "foo-le-bar", "open", "open"),
    "comparator-invalid": (_comparator_invalid, "foo-le-bar", "open", "failed"),
    "no-comparator-config": (_no_comparator_config, "baz", "open", "open"),
    "comparator-todo": (_comparator_todo, "baz", "open", "open"),
    "depends-on-open": (_depends_on_open, "zap", "blocked", "open"),
    "depends-on-unknown": (_depends_on_unknown, "zap", "unknown", "unknown"),
}


def test_every_code_has_a_scenario_and_a_documented_meaning():
    ledger_mod = importlib.import_module("lean_pkg.ledger")
    assert set(SCENARIOS) == set(ledger_mod.CODES)
    doc = (ROOT / "docs" / "lifecycle.md").read_text()
    missing = [c for c in ledger_mod.CODES if f"`{c}`" not in doc]
    assert missing == [], "docs/lifecycle.md does not list: " + ", ".join(missing)


@pytest.mark.parametrize("code", sorted(SCENARIOS))
def test_each_code(lp, env, capsys, code):
    scenario, cid, claim_status, package_status = SCENARIOS[code]
    scenario(lp, env, capsys)
    rc, data = ledger(lp, env, capsys)
    row = by_id(data)[cid]
    assert code in codes(row), (code, row)
    assert row["status"] == claim_status, row
    assert data["package_status"] == package_status and rc == 1, data["reasons"]
    if code == "axiom-not-allowed":
        assert "receipt-invalid" not in codes(row)            # one fact, one code


# --- read-only, deterministic ---------------------------------------------------------------------

def test_ledger_writes_nothing(lp, env, capsys):
    gated(env)
    before = (env.registry.read_text(), read_events(env),
              _git(env.repo_b, "status", "--porcelain", "--untracked-files=all").stdout)
    ledger(lp, env, capsys)
    ledger(lp, env, capsys, stage="gate")
    assert (env.registry.read_text(), read_events(env),
            _git(env.repo_b, "status", "--porcelain", "--untracked-files=all").stdout) == before


def test_the_digest_is_stable_and_follows_the_evidence(lp, env, capsys):
    gated(env)
    _, a = ledger(lp, env, capsys)
    _, b = ledger(lp, env, capsys)
    assert a["ledger_digest"] == b["ledger_digest"] and a["ledger_digest"].startswith("sha256:")
    _, g = ledger(lp, env, capsys, stage="gate")
    assert g["ledger_digest"] != a["ledger_digest"]                  # the stage is part of it
    p = env.repo_b / ".lean-receipts" / "g.json"
    p.write_text(json.dumps(json.loads(p.read_text()), indent=1))  # same content, other bytes
    _, c = ledger(lp, env, capsys)
    assert c["package_status"] == "ready" and c["receipt_sha256"] != a["receipt_sha256"]
    assert c["ledger_digest"] != a["ledger_digest"]


def test_the_digest_does_not_depend_on_where_the_checkout_lives(lp, env, capsys, tmp_path):
    gated(env)
    _, a = ledger(lp, env, capsys)
    moved = tmp_path / "elsewhere" / "beta"
    moved.parent.mkdir()
    env.repo_b.rename(moved)
    reg = read_reg(env)
    reg["packages"]["beta"]["local_path"] = str(moved)
    write_reg(env, reg)
    _, b = ledger(lp, env, capsys)
    assert a["ledger_digest"] == b["ledger_digest"]


def test_unknown_package_and_bad_stage(lp, env, capsys):
    assert lp.main(["--registry", str(env.registry), "claims", "ledger", "nope"]) == 1
    capsys.readouterr()
    with pytest.raises(SystemExit) as e:
        lp.main(["--registry", str(env.registry), "claims", "ledger", "beta", "--stage", "public"])
    assert e.value.code == 2
