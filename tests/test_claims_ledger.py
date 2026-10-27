#!/usr/bin/env python3
"""#80: `claims ledger NAME` gives each claim named diagnostics from the recorded evidence; a claim is
verified only with zero diagnostics, and the package status fails closed."""

from __future__ import annotations

import importlib
import json

import pytest
import yaml

from .conftest import (put_receipt, ROOT, _git, commit_claims, drop_claims, make_comparator_receipt, make_receipt,
                       publish_evidence, read_events, read_reg, repo_head, write_reg, write_report_log)

LEAN = ("namespace Demo\n"   # the claims name Demo.<decl>: exact covers() (#80 fix)
        "theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\ndef qux := 1\n"
        "theorem old_one : True := trivial\ntheorem zap : True := trivial\n"
        "end Demo\n")
ALL_NAMES = ("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.baz", "BetaAudit.Demo.zap")


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


def gated(env, claims=None, names=ALL_NAMES, todo=(), receipt=None, comparator=True, log=None, lean=LEAN):
    """beta gated (and, unless comparator=False, compared) at one commit tracking LEAN, claims.yaml and
    comparator.json; the receipts are written untracked under .lean-receipts/ and recorded in the registry.
    log: the text of the sealed verify log (default: a fixture line, no hyp-probe/1 section)."""
    repo = env.repo_b
    cfg = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": list(names),
           "definition_names": [], "permitted_axioms": ["propext", "Quot.sound", "Classical.choice"],
           "enable_nanoda": True}
    if todo:
        cfg["lean_pkg_scaffold"] = {"todo": [{"theorem_name": t} for t in todo]}
    (repo / "comparator.json").write_text(json.dumps(cfg, indent=2) + "\n")
    doc = {"schema": "claims/1", "package": "beta", "claims": claims_list() if claims is None else claims}
    commit_claims(repo, yaml.safe_dump(doc, sort_keys=False), lean=lean)
    rec = make_receipt(repo, **(receipt or {}))
    if log is not None:
        (repo / ".lean-receipts").mkdir(exist_ok=True)
        write_report_log(repo / ".lean-receipts", rec, log)
    put_receipt(repo / ".lean-receipts" / "g.json", rec)
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
    gated(env, claims=claims_list(top=extra), names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))
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
    drop_claims(env.repo_b)                              # then gate the commit without claims.yaml
    put_receipt(env.repo_b / ".lean-receipts" / "g.json", make_receipt(env.repo_b))
    set_gate(env, receipt=".lean-receipts/g.json", comparator=None)
    rc, data = ledger(lp, env, capsys)
    assert rc == 1 and data["package_status"] == "unknown" and data["claims"] == []


def test_no_reusable_claim_is_unknown(lp, env, capsys):
    gated(env, claims=[claim("foo-le-bar", "Demo.foo_le_bar", status="internal")],
          names=("BetaAudit.Demo.zap",))           # not judged by the comparator (test_internal_claims.py)
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
                                  zap=claim("zap", "Demo.zap")), names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))
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


def _report_log_missing(lp, env, capsys):
    """Soundness: the gate receipt's sealed verify log is gone, so report_sha256 binds nothing (gate refuses
    such a receipt; the ledger, and so promote, must not count it)."""
    gated(env)
    for p in (env.repo_b / ".lean-receipts").glob("*-verify.redacted.log"):
        p.unlink()


def _axiom_not_allowed(lp, env, capsys):
    gated(env, receipt={"axioms": ["propext", "sorryAx"]})


def _decl_missing(lp, env, capsys):
    gated(env, claims=claims_list(**{"foo-le-bar": claim("foo-le-bar", "Demo.gone")}))


def _finding_evidence_missing(lp, env, capsys):
    gap = {"id": "gap-1", "kind": "proof-gap", "location": {"label": "Lemma 3.2"}, "summary": "A step is missing.",
           "lean_evidence": ["Demo.no_such_counterexample"], "fixed_in": None}
    gated(env, claims=claims_list(**{"foo-le-bar": {**claim("foo-le-bar", "Demo.foo_le_bar"),
                                                    "manuscript_findings": [gap]}}))


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
    gated(env, names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))


def _comparator_todo(lp, env, capsys):
    gated(env, names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.baz", "BetaAudit.Demo.zap"),
          todo=("BetaAudit.Demo.baz",))


def _depends_on_open(lp, env, capsys):
    gated(env, names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))


def _depends_on_unknown(lp, env, capsys):
    gated(env, claims=claims_list(zap=claim("zap", "Demo.zap", deps=["other-pkg/thing"])))


def _hypotheses_unchecked(lp, env, capsys):     # proposal 0003: a log without the hyp-probe/1 section
    gated(env, claims=claims_list(**{"foo-le-bar": dict(claim("foo-le-bar", "Demo.foo_le_bar"),
                                                       hypotheses=["Hyp.A"])}))


# --- proposal 0003 (conditional claims): demo/main under Hyp.A and Hyp.B, the proof of Hyp.A, and the corollary
# main-of-b discharging Hyp.A; the facts are the hyp-probe/1 section of the sealed verify log ------------------

LEAN_H = LEAN + ("namespace Demo\ntheorem main : True := trivial\ntheorem main_of_b : True := trivial\n"
                 "theorem proof_a : True := trivial\ntheorem wit : True := trivial\nend Demo\n")
COND_NAMES = ALL_NAMES + ("BetaAudit.Demo.main", "BetaAudit.Demo.main_of_b", "BetaAudit.Demo.proof_a")
HYP_HEADER = "== lean-pkg hypotheses (hyp-probe/1) =="


def cond_claims(**change):
    rows = {
        "main": dict(claim("main", "Demo.main"), hypotheses=["Hyp.A", "Hyp.B"]),
        "proof-a": claim("proof-a", "Demo.proof_a"),
        "main-of-b": dict(claim("main-of-b", "Demo.main_of_b"), hypotheses=["Hyp.B"],
                          discharges={"claim": "beta/main", "hypotheses": ["Hyp.A"], "proofs": ["beta/proof-a"]}),
    }
    rows.update(change)
    return claims_list(**rows)


def binder(i, head, kind="def", explicit=True):
    return {"i": i, "head": head, "kind": kind, "module": "Demo.Statements", "explicit": explicit}


def hyp_line(tag, **obj) -> str:
    return f"{tag}: " + json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def decl_line(theorem, *binders, data=0, config=None) -> str:
    obj = {"v": 1, "theorem": theorem, "binders": list(binders), "data_binders": data}
    if config:
        obj["config"] = config
    return hyp_line("hyp-challenge" if config else "hyp-decl", **obj)


AB = (binder(0, "Hyp.A"), binder(1, "Hyp.B"))


def hyp_log(*, decls=None, challenge=None, facts=None, extra=(), configs=("comparator.json",)) -> str:
    """A sealed verify log with a hyp-probe/1 section: hyp-decl lines (default: every decl of cond_claims, main
    under A and B, main_of_b under B), the Challenge build, hyp-challenge lines (default: the credited names, same
    binders) and the facts (default: the honest corollary and the proof of Hyp.A)."""
    if decls is None:
        decls = {"Demo.foo_le_bar": (), "Demo.baz": (), "Demo.qux": (), "Demo.old_one": (), "Demo.zap": (),
                 "Demo.main": AB, "Demo.proof_a": (), "Demo.main_of_b": (binder(0, "Hyp.B"),)}
    if challenge is None:
        challenge = {"BetaAudit.Demo.main": AB, "BetaAudit.Demo.main_of_b": (binder(0, "Hyp.B"),),
                     "BetaAudit.Demo.proof_a": (), "BetaAudit.Demo.foo_le_bar": (), "BetaAudit.Demo.baz": (),
                     "BetaAudit.Demo.zap": ()}
    if facts is None:
        facts = [hyp_line("hyp-corollary", corollary="BetaAudit.Demo.main_of_b", of="BetaAudit.Demo.main",
                          drops=["Hyp.A"], binders=[0]),
                 hyp_line("hyp-proof", proof="BetaAudit.Demo.proof_a", proves="Hyp.A")]
    lines = [decl_line(t, *bs) if not isinstance(bs, dict) else decl_line(t, *bs["binders"], data=bs["data"])
             for t, bs in decls.items()]
    if configs:
        lines.append(hyp_line("hyp-challenge-build", module="Challenge", ok=True))
        lines += [decl_line(t, *(bs if not isinstance(bs, dict) else bs["binders"]),
                            data=0 if not isinstance(bs, dict) else bs["data"], config=configs[0])
                  for t, bs in challenge.items()]
    lines += list(facts) + list(extra)
    n_decl = sum(x.startswith(("hyp-decl:", "hyp-missing:")) for x in lines)
    n_chal = sum(x.startswith("hyp-challenge:") for x in lines)
    done = hyp_line("hyp-done", v=1, decls=n_decl, challenge=n_chal, configs=list(configs))
    return "fixture verify log\n" + HYP_HEADER + "\n" + "".join(x + "\n" for x in lines) + done + "\n"


def cond_gated(env, claims=None, names=COND_NAMES, log=None, **kw):
    return gated(env, claims=cond_claims() if claims is None else claims, names=names,
                 log=hyp_log() if log is None else log, lean=LEAN_H, **kw)


def _hypothesis_not_in_type(lp, env, capsys):
    cond_gated(env, log=hyp_log(decls={"Demo.main": (binder(0, "Hyp.B"),), "Demo.main_of_b": (binder(0, "Hyp.B"),)}))


def _hypothesis_inadmissible(lp, env, capsys):
    ax = (binder(0, "Hyp.A", kind="axiom"), binder(1, "Hyp.B"))
    cond_gated(env, log=hyp_log(decls={"Demo.main": ax, "Demo.main_of_b": (binder(0, "Hyp.B"),)}))


def _hypothesis_open(lp, env, capsys):
    cond_gated(env)


def _discharge_type_mismatch(lp, env, capsys):
    cond_gated(env, log=hyp_log(facts=[hyp_line("hyp-proof", proof="BetaAudit.Demo.proof_a", proves="Hyp.A")]))


def _discharge_not_compared(lp, env, capsys):
    cond_gated(env, names=tuple(n for n in COND_NAMES if n != "BetaAudit.Demo.proof_a"))


def _witness_type_mismatch(lp, env, capsys):
    cond_gated(env, claims=cond_claims(main=dict(claim("main", "Demo.main"), hypotheses=["Hyp.A", "Hyp.B"],
                                                 witness="beta/foo-le-bar")))


def _depends_on_conditional(lp, env, capsys):
    cond_gated(env, claims=cond_claims(zap=claim("zap", "Demo.zap", deps=["beta/baz", "beta/main"])))


# code -> (scenario, the claim it lands on, that claim's status, the package status)
SCENARIOS = {
    "no-checkout": (_no_checkout, "foo-le-bar", "unknown", "unknown"),
    "no-gate-receipt": (_no_gate_receipt, "foo-le-bar", "open", "open"),
    "receipt-unreadable": (_receipt_unreadable, "foo-le-bar", "unknown", "unknown"),
    "bound-commit-missing": (_bound_commit_missing, "foo-le-bar", "unknown", "unknown"),
    "receipt-commit-mismatch": (_receipt_commit_mismatch, "foo-le-bar", "open", "open"),
    "receipt-invalid": (_receipt_invalid, "foo-le-bar", "open", "failed"),
    "report-log-missing": (_report_log_missing, "foo-le-bar", "unknown", "unknown"),
    "axiom-not-allowed": (_axiom_not_allowed, "foo-le-bar", "open", "failed"),
    "decl-missing": (_decl_missing, "foo-le-bar", "open", "failed"),
    "finding-evidence-missing": (_finding_evidence_missing, "foo-le-bar", "open", "failed"),
    "no-comparator-receipt": (_no_comparator_receipt, "foo-le-bar", "open", "open"),
    "comparator-unreadable": (_comparator_unreadable, "foo-le-bar", "unknown", "unknown"),
    "comparator-commit-mismatch": (_comparator_commit_mismatch, "foo-le-bar", "open", "open"),
    "comparator-invalid": (_comparator_invalid, "foo-le-bar", "open", "failed"),
    "no-comparator-config": (_no_comparator_config, "baz", "open", "open"),
    "comparator-todo": (_comparator_todo, "baz", "open", "open"),
    "depends-on-open": (_depends_on_open, "zap", "blocked", "open"),
    "depends-on-unknown": (_depends_on_unknown, "zap", "unknown", "unknown"),
    "hypotheses-unchecked": (_hypotheses_unchecked, "foo-le-bar", "open", "open"),
    "hypothesis-not-in-type": (_hypothesis_not_in_type, "main", "open", "failed"),
    "hypothesis-inadmissible": (_hypothesis_inadmissible, "main", "open", "failed"),
    "hypothesis-open": (_hypothesis_open, "main", "conditional", "ready"),
    "discharge-type-mismatch": (_discharge_type_mismatch, "main-of-b", "open", "failed"),
    "discharge-not-compared": (_discharge_not_compared, "main-of-b", "open", "open"),
    "witness-type-mismatch": (_witness_type_mismatch, "main", "open", "failed"),
    "depends-on-conditional": (_depends_on_conditional, "zap", "conditional", "ready"),
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
    assert data["package_status"] == package_status and rc == (package_status != "ready"), data["reasons"]
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


# --- wiring (#80): promote refuses unless ready; publish --dry-run and next read the ledger ---------

def run_json(lp, env, capsys, *argv):
    rc = lp.main(["--registry", str(env.registry), *argv, "--json"])
    return rc, json.loads(capsys.readouterr().out)


def ledger_items(data):
    return [c for c in data["checklist"] if c["label"].startswith("claims ledger")]


def failing(data):
    return [c["label"] for c in data["checklist"] if c["status"] == "fail"]


def test_promote_passes_the_ledger_item_when_ready(lp, env, fake_gh, capsys):
    gated(env)
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "promote", "beta")
    assert rc == 0 and failing(data) == [], failing(data)
    [item] = ledger_items(data)
    assert item["status"] == "pass" and item["label"].startswith("claims ledger ready: 4 required claim(s) verified")
    assert data["claims_ledger"]["package_status"] == "ready"
    assert data["claims_ledger"]["ledger_digest"].startswith("sha256:")


def test_promote_refuses_when_a_reusable_theorem_is_not_compared(lp, env, fake_gh, capsys):
    gated(env, names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))                 # baz: comparator PASS, but not for baz
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "promote", "beta", "--yes")
    assert rc == 1 and data["refused"] is True
    assert failing(data) == [ledger_items(data)[0]["label"]]          # the ledger is the only failure
    assert "claims ledger open" in failing(data)[0] and "beta/baz: no-comparator-config" in failing(data)[0]
    assert "lean-pkg claims ledger beta" in failing(data)[0]
    assert data["claims_ledger"]["package_status"] == "open"
    assert fake_gh.visibility == "PRIVATE" and read_events(env) == []   # nothing flipped, nothing recorded


def test_promote_refuses_a_package_without_claims(lp, env, fake_gh, capsys):
    gated(env, claims=[])
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "promote", "beta")
    assert rc == 1 and failing(data) == [ledger_items(data)[0]["label"]]
    assert failing(data)[0].startswith("claims ledger unknown: no claims")


def test_publish_dry_run_carries_the_ledger(lp, env, fake_gh, capsys):
    gated(env, names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "publish", "beta", "--dry-run")
    assert rc == 1 and data["ready"] is False
    assert any(lbl.startswith("claims ledger open") for lbl in failing(data))
    assert data["claims_ledger"]["package_status"] == "open"


def test_next_recommends_publish_only_when_the_ledger_is_ready(lp, env, capsys):
    gated(env)
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "next", "beta")
    assert rc == 0 and data["command"] == "lean-pkg publish beta --dry-run", data
    assert "claims ledger ready" in data["reason"]
    assert data["state"]["claims_ledger"]["package_status"] == "ready"


def test_next_names_the_coverage_fix_when_a_claim_is_not_compared(lp, env, capsys, monkeypatch):
    monkeypatch.setenv("LEAN_PKG_TOOLCHAIN_FLOOR", "v4.0.0")   # the fixture toolchain is below the default
    gated(env, names=("BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.zap"))
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "next", "beta")
    assert rc == 0 and data["command"] == "lean-pkg comparator config --from-claims beta", data
    assert data["reason"].startswith("promote would refuse: claims ledger open")
    assert data["state"]["claims_ledger"]["package_status"] == "open"


def test_next_points_at_the_ledger_for_other_open_claims(lp, env, capsys):
    gated(env, claims=[])
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "next", "beta")
    assert rc == 0 and data["command"] == "lean-pkg claims ledger beta", data
    assert "promote would refuse: claims ledger unknown: no claims" in data["reason"]
    assert "claims.yaml is committed at HEAD but not synced: lean-pkg claims sync beta" in data["reason"]
