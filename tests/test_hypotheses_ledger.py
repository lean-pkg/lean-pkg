#!/usr/bin/env python3
"""Proposal 0003, stage H3 (#320): the claims ledger judges hypotheses, discharge and witnesses from the
hyp-probe/1 facts of the gate receipt's sealed verify log at the gate-bound commit X. A conditional claim is
never verified; a hypothesis is discharged only through a corollary compared in the same comparator build at X;
whatever the facts cannot settle stays open (hypotheses-unchecked). Each refusal path has a test here."""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from .conftest import _git, make_comparator_receipt, publish_evidence, read_reg, write_reg
from .test_claims_ledger import (AB, COND_NAMES, LEAN_H, binder, by_id, claim, codes, cond_claims, cond_gated,
                                 failing, hyp_line, hyp_log, ledger, ledger_items, run_json)

COROLLARY = hyp_line("hyp-corollary", corollary="BetaAudit.Demo.main_of_b", of="BetaAudit.Demo.main",
                     drops=["Hyp.A"], binders=[0])
PROOF_A = hyp_line("hyp-proof", proof="BetaAudit.Demo.proof_a", proves="Hyp.A")


def hyps(row):
    return [(h["decl"], h["state"], (h["by"] or {}).get("corollary"), h["via"]) for h in row.get("hypotheses") or []]


def detail(row, code):
    return " | ".join(d["detail"] for d in row["diagnostics"] if d["code"] == code)


# --- the honest case: discharged through the corollary, the claim stays conditional ----------------------------

def test_a_discharged_hypothesis_leaves_the_claim_conditional_and_the_package_ready(lp, env, capsys):
    g = cond_gated(env)
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rc == 0 and data["package_status"] == "ready", data["reasons"]
    main, cor = rows["main"], rows["main-of-b"]
    assert main["status"] == "conditional" and codes(main) == ["hypothesis-open"]
    assert hyps(main) == [("Hyp.A", "discharged", "beta/main-of-b", None), ("Hyp.B", "open", None, None)]
    assert main["hypotheses"][0]["by"] == {"proof": "beta/proof-a", "corollary": "beta/main-of-b"}
    assert detail(main, "hypothesis-open").startswith(f"Hyp.B: outstanding at {g[:12]}; no claim discharges it")
    assert cor["status"] == "conditional" and hyps(cor) == [("Hyp.B", "open", None, None)]
    assert cor["discharges"] == {"claim": "beta/main", "hypotheses": ["Hyp.A"], "proofs": ["beta/proof-a"]}
    assert rows["proof-a"]["status"] == "verified" and "hypotheses" not in rows["proof-a"]
    assert data["counts"]["conditional"] == 2 and data["counts"]["verified"] == 5   # never counted as verified
    assert data["conditional_required"] == ["beta/main", "beta/main-of-b"]
    assert data["outstanding_hypotheses"] == ["Hyp.B"]
    lp.main(["--registry", str(env.registry), "claims", "ledger", "beta"])
    text = capsys.readouterr().out
    assert "  conditional  beta/main (Demo.main)" in text
    assert "      hypothesis Hyp.A: discharged (proof beta/proof-a, corollary beta/main-of-b)" in text
    assert "      hypothesis Hyp.B: outstanding" in text


def test_all_discharged_the_claim_stays_conditional_with_nothing_outstanding(lp, env, capsys):
    claims = cond_claims(**{
        "main": dict(claim("main", "Demo.main"), hypotheses=["Hyp.A"]),
        "main-of-b": dict(claim("main-of-b", "Demo.main_of_b"),
                          discharges={"claim": "beta/main", "hypotheses": ["Hyp.A"], "proofs": ["beta/proof-a"]})})
    log = hyp_log(decls={"Demo.main": (binder(0, "Hyp.A"),), "Demo.main_of_b": (), "Demo.proof_a": ()},
                  challenge={"BetaAudit.Demo.main": (binder(0, "Hyp.A"),), "BetaAudit.Demo.main_of_b": (),
                             "BetaAudit.Demo.proof_a": ()})
    cond_gated(env, claims=claims, log=log)
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rc == 0 and data["package_status"] == "ready"
    assert rows["main"]["status"] == "conditional" and codes(rows["main"]) == []
    assert hyps(rows["main"]) == [("Hyp.A", "discharged", "beta/main-of-b", None)]
    assert rows["main-of-b"]["status"] == "verified" and "hypotheses" not in rows["main-of-b"]
    assert data["outstanding_hypotheses"] == [] and data["conditional_required"] == ["beta/main"]


def test_the_gate_stage_never_discharges(lp, env, capsys):
    cond_gated(env)
    rc, data = ledger(lp, env, capsys, stage="gate")
    rows = by_id(data)
    assert rc == 0 and data["package_status"] == "ready"
    assert hyps(rows["main"]) == [("Hyp.A", "open", None, None), ("Hyp.B", "open", None, None)]
    assert "at the gate stage nothing is discharged" in detail(rows["main"], "hypothesis-open")
    assert rows["main"]["status"] == "conditional"


# --- unchecked: the facts are not there ------------------------------------------------------------------------

def test_a_log_without_the_section_is_unchecked_at_both_stages(lp, env, capsys):
    cond_gated(env, log="fixture verify log\n")
    for stage in ("gate", "promote"):
        rc, data = ledger(lp, env, capsys, stage=stage)
        rows = by_id(data)
        for cid in ("main", "main-of-b"):
            assert codes(rows[cid]) == ["hypotheses-unchecked"] and rows[cid]["status"] == "open"
        assert "no hyp-probe/1 section" in detail(rows["main"], "hypotheses-unchecked")
        assert all(h[1] == "open" for h in hyps(rows["main"]))
        assert data["package_status"] == "open" and rc == 1


@pytest.mark.parametrize("extra, needle", [
    ([hyp_line("hyp-error", phase="challenge", message="exit 1: unknown module")],
     "the Challenge part of the probe did not run (exit 1: unknown module)"),
    ([hyp_line("hyp-skip", theorem="BetaAudit.Demo.main", reason="13 distinct hypothesis constants")],
     "the corollary search was skipped for BetaAudit.Demo.main"),
])
def test_a_part_that_did_not_run_is_unchecked(lp, env, capsys, extra, needle):
    cond_gated(env, log=hyp_log(extra=extra))
    rc, data = ledger(lp, env, capsys)
    row = by_id(data)["main-of-b"]
    assert "hypotheses-unchecked" in codes(row) and needle in detail(row, "hypotheses-unchecked")
    assert hyps(by_id(data)["main"])[0][1] == "open" and rc == 1


def test_a_decl_part_error_is_unchecked(lp, env, capsys):
    log = hyp_log(decls={}, extra=[hyp_line("hyp-error", phase="decl", message="exit 1")])
    cond_gated(env, log=log)
    rc, data = ledger(lp, env, capsys, stage="gate")
    assert "the decl part of the probe did not run" in detail(by_id(data)["main"], "hypotheses-unchecked")


def test_a_failed_challenge_build_is_unchecked(lp, env, capsys):
    log = hyp_log().replace('"module":"Challenge","ok":true', '"module":"Challenge","ok":false')
    cond_gated(env, log=log)
    rc, data = ledger(lp, env, capsys)
    assert "did not build" in detail(by_id(data)["main"], "hypotheses-unchecked") and rc == 1


def test_an_incomplete_section_is_unchecked(lp, env, capsys):
    log = hyp_log().rsplit("hyp-done:", 1)[0]
    cond_gated(env, log=log)
    rc, data = ledger(lp, env, capsys)
    assert "is incomplete" in detail(by_id(data)["main"], "hypotheses-unchecked") and rc == 1


def test_ambiguous_credit_is_unchecked(lp, env, capsys):
    cond_gated(env, names=COND_NAMES + ("OtherAudit.Demo.main",))
    rc, data = ledger(lp, env, capsys)
    assert "more than one comparator name" in detail(by_id(data)["main"], "hypotheses-unchecked")
    assert hyps(by_id(data)["main"])[0][1] == "open" and rc == 1


def test_the_sealed_log_missing_is_report_log_missing(lp, env, capsys):
    cond_gated(env)
    for p in (env.repo_b / ".lean-receipts").glob("*-verify.redacted.log"):
        p.unlink()
    rc, data = ledger(lp, env, capsys)
    row = by_id(data)["main"]
    assert "report-log-missing" in codes(row) and "hypotheses-unchecked" in codes(row)
    assert row["status"] == "unknown" and rc == 1


# --- hypothesis-not-in-type and hypothesis-inadmissible ------------------------------------------------------

@pytest.mark.parametrize("main_binders, needle", [
    ((binder(0, "Hyp.A2"), binder(1, "Hyp.B")), "Hyp.A is not the head of a hypothesis binder of Demo.main"),
    ((binder(0, "Hyp.A", explicit=False), binder(1, "Hyp.B")), "Hyp.A is a binder of Demo.main, but not an explicit"),
    ((binder(1, "Hyp.B"),), "Hyp.A is not the head of a hypothesis binder of Demo.main"),
])
def test_hypothesis_not_in_type(lp, env, capsys, main_binders, needle):
    cond_gated(env, log=hyp_log(decls={"Demo.main": main_binders, "Demo.main_of_b": (binder(0, "Hyp.B"),)}))
    for stage in ("gate", "promote"):
        rc, data = ledger(lp, env, capsys, stage=stage)
        row = by_id(data)["main"]
        assert needle in detail(row, "hypothesis-not-in-type"), row
        assert row["status"] == "open" and data["package_status"] == "failed" and rc == 1


def test_hypothesis_not_in_the_challenge_type_at_promote(lp, env, capsys):
    log = hyp_log(challenge={"BetaAudit.Demo.main": (binder(1, "Hyp.B"),),
                             "BetaAudit.Demo.main_of_b": (binder(0, "Hyp.B"),), "BetaAudit.Demo.proof_a": ()})
    cond_gated(env, log=log)
    rc, data = ledger(lp, env, capsys)
    assert "of the Challenge theorem BetaAudit.Demo.main" in detail(by_id(data)["main"], "hypothesis-not-in-type")
    assert data["package_status"] == "failed"
    rc, data = ledger(lp, env, capsys, stage="gate")
    assert "hypothesis-not-in-type" not in codes(by_id(data)["main"])


@pytest.mark.parametrize("kind", ["axiom", "opaque", "theorem"])
def test_hypothesis_inadmissible(lp, env, capsys, kind):
    cond_gated(env, log=hyp_log(decls={"Demo.main": (binder(0, "Hyp.A", kind=kind), binder(1, "Hyp.B")),
                                       "Demo.main_of_b": (binder(0, "Hyp.B"),)}))
    rc, data = ledger(lp, env, capsys)
    row = by_id(data)["main"]
    assert f"Hyp.A is a{'n' if kind[0] in 'aeiou' else ''} {kind}, not a def or an inductive Prop" in detail(
        row, "hypothesis-inadmissible")
    assert row["status"] == "open" and data["package_status"] == "failed"


def test_an_inductive_hypothesis_is_admissible(lp, env, capsys):
    ind = (binder(0, "Hyp.A", kind="inductive"), binder(1, "Hyp.B"))
    cond_gated(env, log=hyp_log(decls={"Demo.main": ind, "Demo.main_of_b": (binder(0, "Hyp.B"),)}))
    rc, data = ledger(lp, env, capsys)
    assert rc == 0 and by_id(data)["main"]["status"] == "conditional"


# --- discharge only through a corollary checked at the bound commit -----------------------------------------

def test_a_comparator_receipt_for_another_commit_discharges_nothing(lp, env, capsys):
    cond_gated(env)
    repo = env.repo_b
    comp = make_comparator_receipt(repo, "comparator.json")
    comp["commit"] = "f" * 40
    (repo / ".lean-receipts" / "c.json").write_text(json.dumps(comp))
    rc, data = ledger(lp, env, capsys)
    main = by_id(data)["main"]
    assert hyps(main)[0][:2] == ("Hyp.A", "open")
    assert "the comparator tier is not clean" in detail(main, "hypothesis-open") and rc == 1


def test_an_invalid_comparator_receipt_discharges_nothing(lp, env, capsys):
    cond_gated(env)
    p = env.repo_b / ".lean-receipts" / "c.json"
    comp = json.loads(p.read_text())
    comp["result"] = "fail"
    p.write_text(json.dumps(comp))
    rc, data = ledger(lp, env, capsys)
    main = by_id(data)["main"]
    assert "comparator-invalid" in codes(main) and hyps(main)[0][:2] == ("Hyp.A", "open")
    assert data["package_status"] == "failed"


def test_a_corollary_in_no_configuration_is_not_compared(lp, env, capsys):
    cond_gated(env, names=tuple(n for n in COND_NAMES if n != "BetaAudit.Demo.main_of_b"))
    rc, data = ledger(lp, env, capsys)
    cor = by_id(data)["main-of-b"]
    assert detail(cor, "discharge-not-compared").startswith("beta/main-of-b: in no comparator configuration")
    assert hyps(by_id(data)["main"])[0][:2] == ("Hyp.A", "open")
    assert "beta/main-of-b claims to discharge it" in detail(by_id(data)["main"], "hypothesis-open")


def test_a_corollary_in_a_smoke_configuration_only_gets_no_credit(lp, env, capsys):
    repo = env.repo_b
    smoke = {"challenge_module": "Challenge", "solution_module": "Solution",
             "theorem_names": ["BetaAudit.Demo.main_of_b"], "definition_names": [],
             "permitted_axioms": ["propext"], "enable_nanoda": True}
    (repo / "comparator.smoke.json").write_text(json.dumps(smoke, indent=2) + "\n")
    (repo / "lean-pkg-signoff.json").write_text(json.dumps(
        {"schema": "lean-pkg-signoff/1", "configs": {"comparator.smoke.json": {"smoke": {"reason": "set-up"}}}}))
    cond_gated(env, names=tuple(n for n in COND_NAMES if n != "BetaAudit.Demo.main_of_b"))
    rc, data = ledger(lp, env, capsys)
    cor = by_id(data)["main-of-b"]
    assert detail(cor, "discharge-not-compared").startswith("beta/main-of-b: in no comparator configuration"), cor
    assert hyps(by_id(data)["main"])[0][:2] == ("Hyp.A", "open")


def test_head_drift_after_x_takes_the_discharge_away(lp, env, capsys):
    cond_gated(env)
    repo = env.repo_b
    (repo / "Demo" / "Main.lean").write_text(LEAN_H + "-- drift\n")
    _git(repo, "commit", "-q", "-am", "drift")
    rc, data = ledger(lp, env, capsys)
    main = by_id(data)["main"]
    assert "receipt-commit-mismatch" in codes(main) and hyps(main)[0][:2] == ("Hyp.A", "open")
    assert data["package_status"] == "open" and rc == 1


def test_a_mismatched_corollary_is_a_type_mismatch(lp, env, capsys):
    wrong = hyp_line("hyp-corollary", corollary="BetaAudit.Demo.main_of_b", of="BetaAudit.Demo.main",
                     drops=["Hyp.B"], binders=[1])
    cond_gated(env, log=hyp_log(facts=[wrong, PROOF_A]))
    rc, data = ledger(lp, env, capsys)
    cor = by_id(data)["main-of-b"]
    assert "it drops [['Hyp.B']] instead" in detail(cor, "discharge-type-mismatch")
    assert cor["status"] == "open" and data["package_status"] == "failed"
    assert hyps(by_id(data)["main"])[0][:2] == ("Hyp.A", "open")


def test_a_proof_of_another_constant_is_a_type_mismatch(lp, env, capsys):
    other = hyp_line("hyp-proof", proof="BetaAudit.Demo.proof_a", proves="Other.A")
    cond_gated(env, log=hyp_log(facts=[COROLLARY, other]))
    rc, data = ledger(lp, env, capsys)
    cor = by_id(data)["main-of-b"]
    assert "BetaAudit.Demo.proof_a (beta/proof-a) does not have exactly the type Hyp.A" in detail(
        cor, "discharge-type-mismatch")
    assert hyps(by_id(data)["main"])[0][:2] == ("Hyp.A", "open") and data["package_status"] == "failed"


def test_a_parameterized_assumption_is_discharged_by_the_corollary_alone(lp, env, capsys):
    """Open question 3: a proof claim whose Challenge type quantifies over data (`∀ n, Hyp.PA n`) never has a
    hyp-proof fact; the corollary is the evidence and the proof claim is attribution."""
    log = hyp_log(challenge={"BetaAudit.Demo.main": AB, "BetaAudit.Demo.main_of_b": (binder(0, "Hyp.B"),),
                             "BetaAudit.Demo.proof_a": {"binders": (), "data": 1}}, facts=[COROLLARY])
    cond_gated(env, log=log)
    rc, data = ledger(lp, env, capsys)
    assert rc == 0 and hyps(by_id(data)["main"])[0] == ("Hyp.A", "discharged", "beta/main-of-b", None)


def test_a_superseded_discharge_target_is_followed(lp, env, capsys):
    claims = cond_claims(**{
        "old-main": dict(claim("old-main", "Demo.old_one", status="superseded", sb="beta/main")),
        "main-of-b": dict(claim("main-of-b", "Demo.main_of_b"), hypotheses=["Hyp.B"],
                          discharges={"claim": "beta/old-main", "hypotheses": ["Hyp.A"],
                                      "proofs": ["beta/proof-a"]})})
    cond_gated(env, claims=claims)
    rc, data = ledger(lp, env, capsys)
    assert rc == 0 and hyps(by_id(data)["main"])[0] == ("Hyp.A", "discharged", "beta/main-of-b", None)


def test_a_failing_corollary_row_discharges_nothing(lp, env, capsys):
    """The corollary's own evidence must be clean: a decl missing at X takes its discharge away."""
    lean = LEAN_H.replace("theorem main_of_b : True := trivial\n", "")
    from .test_claims_ledger import gated
    gated(env, claims=cond_claims(), names=COND_NAMES, log=hyp_log(), lean=lean)
    rc, data = ledger(lp, env, capsys)
    assert "decl-missing" in codes(by_id(data)["main-of-b"])
    assert hyps(by_id(data)["main"])[0][:2] == ("Hyp.A", "open")


# --- witnesses ------------------------------------------------------------------------------------------------

def _witness_setup(env, facts, data_binders=2):
    claims = cond_claims(main=dict(claim("main", "Demo.main"), hypotheses=["Hyp.A", "Hyp.B"], witness="beta/wit"),
                         wit=claim("wit", "Demo.wit", status="experimental"))
    log = hyp_log(decls={"Demo.main": {"binders": AB, "data": data_binders},
                         "Demo.main_of_b": (binder(0, "Hyp.B"),), "Demo.wit": ()},
                  challenge={"BetaAudit.Demo.main": {"binders": AB, "data": data_binders},
                             "BetaAudit.Demo.main_of_b": (binder(0, "Hyp.B"),), "BetaAudit.Demo.proof_a": (),
                             "BetaAudit.Demo.wit": ()},
                  facts=[COROLLARY, PROOF_A, *facts])
    cond_gated(env, claims=claims, names=COND_NAMES + ("BetaAudit.Demo.wit",), log=log)


def test_a_checked_witness(lp, env, capsys):
    _witness_setup(env, [hyp_line("hyp-witness", witness="BetaAudit.Demo.wit", of="BetaAudit.Demo.main")])
    rc, data = ledger(lp, env, capsys)
    assert rc == 0 and by_id(data)["main"]["witness"] == {"claim": "beta/wit", "state": "checked"}
    rc, data = ledger(lp, env, capsys, stage="gate")
    assert by_id(data)["main"]["witness"] == {"claim": "beta/wit", "state": "unchecked"}


def test_a_witness_of_the_wrong_type_is_a_mismatch(lp, env, capsys):
    _witness_setup(env, [])
    rc, data = ledger(lp, env, capsys)
    main = by_id(data)["main"]
    assert main["witness"]["state"] == "mismatch" and "is not the expected witness" in detail(
        main, "witness-type-mismatch")
    assert main["status"] == "open" and data["package_status"] == "failed"


def test_a_witness_of_a_claim_without_parameters_is_a_mismatch(lp, env, capsys):
    _witness_setup(env, [hyp_line("hyp-witness", witness="BetaAudit.Demo.wit", of="BetaAudit.Demo.main")],
                   data_binders=0)
    rc, data = ledger(lp, env, capsys, stage="gate")
    assert "takes no parameters" in detail(by_id(data)["main"], "witness-type-mismatch")


def test_a_witness_not_compared_is_unchecked_without_a_diagnostic(lp, env, capsys):
    claims = cond_claims(main=dict(claim("main", "Demo.main"), hypotheses=["Hyp.A", "Hyp.B"], witness="beta/wit"),
                         wit=claim("wit", "Demo.wit", status="experimental"))
    log = hyp_log(decls={"Demo.main": {"binders": AB, "data": 1}, "Demo.main_of_b": (binder(0, "Hyp.B"),)})
    cond_gated(env, claims=claims, log=log)
    rc, data = ledger(lp, env, capsys)
    main = by_id(data)["main"]
    assert main["witness"] == {"claim": "beta/wit", "state": "unchecked"}
    assert "witness-type-mismatch" not in codes(main) and rc == 0


# --- propagation through depends_on ---------------------------------------------------------------------------

def test_depends_on_conditional_reaches_a_fixed_point(lp, env, capsys):
    claims = cond_claims(zap=claim("zap", "Demo.zap", deps=["beta/baz", "beta/foo-le-bar"]),
                         **{"foo-le-bar": claim("foo-le-bar", "Demo.foo_le_bar", deps=["beta/main"])})
    cond_gated(env, claims=claims)
    rc, data = ledger(lp, env, capsys)
    rows = by_id(data)
    assert rc == 0 and data["package_status"] == "ready"
    assert rows["foo-le-bar"]["status"] == "conditional" and rows["zap"]["status"] == "conditional"
    assert hyps(rows["foo-le-bar"]) == [("Hyp.B", "open", None, "beta/main")]
    assert hyps(rows["zap"]) == [("Hyp.B", "open", None, "beta/foo-le-bar")]
    assert "beta/main is conditional: inherits outstanding Hyp.B via beta/main" in detail(
        rows["foo-le-bar"], "depends-on-conditional")
    # Lean proved foo-le-bar without hypotheses and the comparator checked it: the edge is named as stale
    assert "this depends_on edge looks stale: remove it" in detail(rows["foo-le-bar"], "depends-on-conditional")
    assert sorted(data["outstanding_hypotheses"]) == ["Hyp.B"]
    assert set(data["conditional_required"]) == {"beta/main", "beta/main-of-b", "beta/foo-le-bar", "beta/zap"}


def test_an_open_dependency_still_blocks(lp, env, capsys):
    claims = cond_claims(zap=claim("zap", "Demo.zap", deps=["beta/main"]))
    cond_gated(env, claims=claims, log=hyp_log(facts=[PROOF_A]))      # no corollary: main-of-b fails
    rc, data = ledger(lp, env, capsys)
    assert by_id(data)["zap"]["status"] == "conditional"             # main itself is closed, conditional
    claims = cond_claims(zap=claim("zap", "Demo.zap", deps=["beta/main-of-b"]))
    cond_gated(env, claims=claims, log=hyp_log(facts=[PROOF_A]))
    rc, data = ledger(lp, env, capsys)
    assert by_id(data)["zap"]["status"] == "blocked" and "depends-on-open" in codes(by_id(data)["zap"])


def test_a_package_without_the_keys_has_no_new_keys(lp, env, capsys):
    from .test_claims_ledger import gated
    gated(env)
    rc, data = ledger(lp, env, capsys)
    assert "conditional" not in data["counts"] and "conditional_required" not in data
    assert "outstanding_hypotheses" not in data
    assert not any(k in r for r in data["claims"] for k in ("hypotheses", "discharges", "witness"))


# --- Q1: promote, publish --dry-run and next accept a conditional package, naming what is outstanding ---------

def test_promote_accepts_conditional_required_claims_and_prints_the_outstanding_hypotheses(lp, env, fake_gh, capsys):
    cond_gated(env)
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "promote", "beta")
    assert rc == 0 and failing(data) == [], failing(data)
    [item] = ledger_items(data)
    assert item["status"] == "pass"
    assert item["label"].startswith("claims ledger ready: 7 required claim(s) verified or conditional "
                                    "(conditional: 2; outstanding hypotheses: Hyp.B) (sha256:")
    rc, data = run_json(lp, env, capsys, "publish", "beta", "--dry-run")
    assert any(c["label"].startswith("claims ledger ready") and "outstanding hypotheses: Hyp.B" in c["label"]
               for c in data["checklist"])


def test_next_names_the_outstanding_hypothesis_as_a_possible_step(lp, env, capsys):
    cond_gated(env)
    publish_evidence(env.repo_b)
    rc, data = run_json(lp, env, capsys, "next", "beta")
    assert rc == 0 and data["command"] == "lean-pkg publish beta --dry-run", data
    assert "conditional" in data["reason"] and "Hyp.B" in data["reason"]
    assert "not an error" in data["reason"]


def test_registry_without_a_checkout_is_unknown_not_conditional(lp, env, capsys):
    cond_gated(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["local_path"] = str(env.repo_b) + "-gone"
    write_reg(env, reg)
    rc, data = ledger(lp, env, capsys)
    assert data["package_status"] == "unknown" and rc == 1
    assert all(r["status"] != "verified" for r in data["claims"] if r["id"] in ("beta/main", "beta/main-of-b"))


# --- outputs stay fail closed until stage H4: the page never calls a conditional row verified -----------------

def _conditional_page(env, monkeypatch, outstanding=True):
    """The page fixture with beta/foo-le-bar (the one reusable claim) under Hyp.A, evaluated from a sealed
    hyp-probe/1 section: ready with one conditional required claim, Hyp.A outstanding unless discharged."""
    from lean_pkg import gate as gate_mod

    from .conftest import CLAIMS_YAML
    from .test_page import COVERAGE, prepare
    claims_text = yaml.safe_load(CLAIMS_YAML)
    claims_text["claims"][0]["hypotheses"] = ["Hyp.A"]
    cov = copy.deepcopy(COVERAGE)
    cov["rows"][0].update({"class": "inputs-supplied",
                           "inputs": [{"hypothesis": "Hyp.A", "text": "the a priori bound, Lemma 3.1"}]})
    prepare(env, cov, extra={"claims.yaml": yaml.safe_dump(claims_text, sort_keys=False)})
    names = json.loads((env.repo_b / "comparator.json").read_text())["theorem_names"]
    log = hyp_log(decls={"Demo.foo_le_bar": (binder(0, "Hyp.A"),)},
                  challenge={n: (binder(0, "Hyp.A"),) for n in names}, facts=[])
    monkeypatch.setattr(gate_mod, "sealed_log_text", lambda repo, rec, rel: log)
    if not outstanding:                     # every hypothesis discharged: the ledger names none outstanding
        from lean_pkg import ledger as ledger_mod
        real = ledger_mod.ledger_for

        def discharged(pkg, stage="promote"):
            led = real(pkg, stage)
            if "outstanding_hypotheses" in led:
                led["outstanding_hypotheses"] = []
            return led
        monkeypatch.setattr(ledger_mod, "ledger_for", discharged)


READY_CONDITIONAL = ("- lean-pkg's claims ledger: **ready**, every required claim is verified or conditional (proved "
                     "under its stated hypotheses) by the recorded receipts; 0 of 1 required claim(s) verified, 1 "
                     "conditional (outstanding hypotheses: `Hyp.A`) (`sha256:")


def test_a_ready_ledger_with_conditional_claims_says_so_on_the_page(lp, env, capsys, monkeypatch, tmp_path):
    from .test_page import page_json, run
    _conditional_page(env, monkeypatch)
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    assert rc == 0 and a["ledger_status"] == "ready" and a["verified_rows"] == 0
    [line] = [x for x in md.splitlines() if x.startswith("- lean-pkg's claims ledger:")]
    assert line.startswith(READY_CONDITIONAL), line
    assert line.endswith("…`). A conditional result says nothing when an outstanding assumption is false.")
    assert "every required claim is closed by the recorded receipts" not in md
    site = tmp_path / "site"
    rc, out, _ = run(lp, env, capsys, "page", "beta", "--site", str(site))
    cov_md = (site / "coverage.md").read_text()
    assert ("- lean-pkg's claims ledger: **ready**, with 1 conditional required claim(s) (outstanding hypotheses: "
            "`Hyp.A`) (`sha256:") in cov_md
    (env.repo_b / "README.md").write_text("# Beta\n")
    rc, _out, _err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 0 and READY_CONDITIONAL in (env.repo_b / "README.md").read_text()
    rc, _out, err = run(lp, env, capsys, "page", "beta", "--check", "--site", str(site))
    assert rc == 0, err                                  # --check round-trips the conditional line


def test_a_ready_page_with_every_hypothesis_discharged_says_none_outstanding(lp, env, capsys, monkeypatch):
    from .test_page import page_json
    _conditional_page(env, monkeypatch, outstanding=False)
    rc, a = page_json(lp, env, capsys)
    assert "1 conditional (outstanding hypotheses: none, all discharged)" in a["markdown"]


def test_the_page_shows_an_evaluated_conditional_row_as_not_yet_verified(lp, env, capsys, monkeypatch):
    from lean_pkg import gate as gate_mod

    from .conftest import CLAIMS_YAML
    from .test_page import COVERAGE, page_json, prepare, run
    claims_text = yaml.safe_load(CLAIMS_YAML)
    claims_text["claims"][0]["hypotheses"] = ["Hyp.A"]
    cov = copy.deepcopy(COVERAGE)
    cov["rows"][0].update({"class": "inputs-supplied",
                           "inputs": [{"hypothesis": "Hyp.A", "text": "the a priori bound, Lemma 3.1"}]})
    prepare(env, cov, extra={"claims.yaml": yaml.safe_dump(claims_text, sort_keys=False)})
    names = json.loads((env.repo_b / "comparator.json").read_text())["theorem_names"]
    log = hyp_log(decls={"Demo.foo_le_bar": (binder(0, "Hyp.A"),)},
                  challenge={n: (binder(0, "Hyp.A"),) for n in names}, facts=[])
    monkeypatch.setattr(gate_mod, "sealed_log_text", lambda repo, rec, rel: log)
    rc, out, _ = run(lp, env, capsys, "--json", "claims", "ledger", "beta")
    row = json.loads(out)["claims"][0]
    assert row["status"] == "conditional" and codes(row) == ["hypothesis-open"], row
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    block = md.split("### Theorem 1: Main bound\n", 1)[1].split("\n## ", 1)[0]
    assert a["verified_rows"] == 0 and "**Verified.**" not in md
    assert block.startswith("\n**Not yet verified.** lean-pkg's record of the checks says: conditional.")
