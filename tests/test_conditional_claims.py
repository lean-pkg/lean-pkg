#!/usr/bin/env python3
"""Proposal 0003, stage H2 (#320): the claims/1 keys `hypotheses`, `discharges` and `witness`, and coverage/1
inputs that name a hypothesis ({hypothesis, text}). Every refusal is named; until the ledger evaluates
discharge (stage H3) a claim using any of the keys is `hypotheses-unchecked` and never verified. A package
without the keys reads exactly as before."""

from __future__ import annotations

import copy
import json

import pytest
import yaml

from .conftest import CLAIMS_YAML, DEMO_LEAN, read_reg, write_reg
from .test_claims_ledger import by_id, claim, claims_list, codes, gated, ledger
from .test_coverage_offline import offline, package, status as check_status
from .test_page import COVERAGE, page_json, prepare, run

CROSS = "cross-package discharge is not implemented (proposal 0003, open question 2)"


def c(cid, kind="theorem", **keys) -> dict:
    d = {"id": f"p/{cid}", "decl": "D." + cid.replace("-", "_"), "kind": kind, "summary": f"{cid} summary",
         "file": "A.lean", "status": "reusable"}
    d.update(keys)
    return d


def proposal_doc() -> dict:
    """The proposal's example: demo/main under Hyp.A and Hyp.B, a proof of Hyp.A, the corollary under Hyp.B,
    and a witness for the main theorem."""
    return {"schema": "claims/1", "package": "p", "claims": [
        c("main", hypotheses=["Hyp.A", "Hyp.B"], witness="p/wit"),
        c("proof-a"),
        c("wit"),
        c("main-of-b", hypotheses=["Hyp.B"],
          discharges={"claim": "p/main", "hypotheses": ["Hyp.A"], "proofs": ["p/proof-a"]}),
        c("thing", kind="definition"),
    ]}


def errors_of(cl, change) -> list[str]:
    d = proposal_doc()
    change(d["claims"])
    return cl.validate_claims(d, "p")


def refused(cl, change, needle):
    errors = errors_of(cl, change)
    assert any(needle in e for e in errors), errors
    return errors


# --- claims/1: the keys --------------------------------------------------------------------------------------

def test_the_proposal_example_is_valid_and_normalizes_with_its_keys(cl):
    d = proposal_doc()
    assert cl.validate_claims(d, "p") == []
    n = [cl.normalize_claim(x) for x in d["claims"]]
    assert n[0]["hypotheses"] == ["Hyp.A", "Hyp.B"] and n[0]["witness"] == "p/wit"
    assert list(n[3]["discharges"]) == ["claim", "hypotheses", "proofs"]
    assert "hypotheses" not in n[1] and "discharges" not in n[1] and "witness" not in n[1]
    assert cl.uses_conditional_keys(d["claims"][0]) == ["hypotheses", "witness"]
    assert cl.uses_conditional_keys(d["claims"][1]) == []


def test_empty_or_absent_hypotheses_normalize_exactly_as_before(cl):
    base = yaml.safe_load(CLAIMS_YAML)["claims"][0]
    for extra in ({}, {"hypotheses": []}, {"hypotheses": None}, {"discharges": None, "witness": None}):
        x = dict(base, **extra)
        assert cl.normalize_claim(x) == cl.normalize_claim(base)
        assert cl.uses_conditional_keys(x) == []
        assert cl.validate_claims({"schema": "claims/1", "package": "beta", "claims": [x]}, "beta") == []


@pytest.mark.parametrize("change, needle", [
    (lambda cs: cs[4].update(hypotheses=["Hyp.A"]),
     "claims[4] (p/thing): hypotheses is set but kind is 'definition'; only theorem and lemma claims take "
     "hypotheses"),
    (lambda cs: cs[0].update(hypotheses=["Hyp.A", "Hyp.A"]), "claims[0].hypotheses repeats ['Hyp.A']"),
    (lambda cs: cs[0].update(hypotheses=["not a name"]),
     "claims[0].hypotheses[0] must be the fully qualified Lean name of an assumption (a def or inductive Prop), "
     "got 'not a name'"),
    (lambda cs: cs[0].update(hypotheses=[3]), "claims[0].hypotheses[0] must be the fully qualified Lean name"),
    (lambda cs: cs[0].update(hypotheses="Hyp.A"), "claims[0].hypotheses must be a list of fully qualified Lean names"),
    (lambda cs: cs[0].update(hypotheses=["TODO"]), "claims[0].hypotheses[0] is the placeholder TODO"),
])
def test_hypotheses_refusals(cl, change, needle):
    refused(cl, change, needle)


def _dis(**d):
    base = {"claim": "p/main", "hypotheses": ["Hyp.A"], "proofs": ["p/proof-a"]}
    base.update(d)
    return lambda cs: cs[3].update(discharges=base)


@pytest.mark.parametrize("change, needle", [
    (_dis(claim="p/nope"), "p/main-of-b: discharges.claim 'p/nope' is not a claim in this file"),
    (_dis(claim="p/main-of-b"),
     "claims[3] (p/main-of-b): discharges.claim names the claim itself"),
    (_dis(claim="Q/slug"), f"p/main-of-b: discharges.claim 'Q/slug' is a claim of another package: {CROSS}"),
    (_dis(proofs=["Q/proof-a"]), f"discharges.proofs entry 'Q/proof-a' is a claim of another package: {CROSS}"),
    (_dis(proofs=["p/proof-a", "p/wit"]),
     "claims[3] (p/main-of-b): discharges.proofs has 2 claim id(s) and discharges.hypotheses 1; give one proof "
     "claim per discharged hypothesis, in the same order"),
    (_dis(proofs=["p/main-of-b"]), "claims[3] (p/main-of-b): discharges.proofs names the claim itself"),
    (_dis(proofs=["p/thing"]), "p/main-of-b: discharges.proofs entry 'p/thing' has kind 'definition'"),
    (_dis(proofs=["p/nope"]), "p/main-of-b: discharges.proofs entry 'p/nope' is not a claim in this file"),
    (_dis(hypotheses=["Hyp.C"]),
     "p/main-of-b: discharges.hypotheses ['Hyp.C'] are not hypotheses of p/main (its hypotheses: "
     "['Hyp.A', 'Hyp.B'])"),
    (_dis(hypotheses=[], proofs=[]), "claims[3].discharges.hypotheses must name at least one hypothesis"),
    (_dis(hypotheses=["Hyp.A", "Hyp.A"], proofs=["p/proof-a", "p/proof-a"]),
     "claims[3].discharges.hypotheses repeats ['Hyp.A']"),
    (_dis(claim="p/thing"), "p/main-of-b: discharges.claim 'p/thing' has kind 'definition'"),
    (lambda cs: cs[3].update(hypotheses=["Hyp.B", "Hyp.C"]),
     "p/main-of-b: hypotheses ['Hyp.B', 'Hyp.C'] must equal the hypotheses of p/main minus the discharged ones, "
     "['Hyp.B']"),
    (lambda cs: cs[3].update(hypotheses=[]),
     "p/main-of-b: hypotheses [] must equal the hypotheses of p/main minus the discharged ones, ['Hyp.B']"),
    (lambda cs: cs[3].update(discharges={"claim": "p/main", "hypotheses": ["Hyp.A"]}),
     "claims[3].discharges must be a mapping with exactly the keys ['claim', 'hypotheses', 'proofs'], got "
     "['claim', 'hypotheses']"),
    (lambda cs: cs[3].update(discharges={"claim": "p/main", "hypotheses": ["Hyp.A"], "proofs": ["p/proof-a"],
                                         "by": "x"}),
     "claims[3].discharges must be a mapping with exactly the keys"),
    (lambda cs: cs[3].update(discharges="p/main"), "claims[3].discharges must be a mapping"),
    (lambda cs: cs[3].update(kind="definition"),
     "claims[3] (p/main-of-b): discharges is set but kind is 'definition'"),
    (_dis(claim="main"), "claims[3].discharges.claim must be a claim id <package>/<slug>, got 'main'"),
])
def test_discharges_refusals(cl, change, needle):
    refused(cl, change, needle)


def test_a_superseded_discharge_target_is_followed(cl):
    def supersede(cs):
        cs.append(c("main-v2", hypotheses=["Hyp.A", "Hyp.B"]))
        cs[0].update(status="superseded", superseded_by="p/main-v2", witness=None)
    assert errors_of(cl, supersede) == []

    def supersede_with_other_hyps(cs):
        supersede(cs)
        cs[-1]["hypotheses"] = ["Hyp.B", "Hyp.C"]
    refused(cl, supersede_with_other_hyps,
            "p/main-of-b: discharges.hypotheses ['Hyp.A'] are not hypotheses of p/main-v2 (superseded by p/main-v2)")

    def supersede_rest(cs):
        supersede(cs)
        cs[-1]["hypotheses"] = ["Hyp.A", "Hyp.B", "Hyp.C"]
    refused(cl, supersede_rest, "must equal the hypotheses of p/main-v2 (superseded by p/main-v2) minus the "
                                "discharged ones, ['Hyp.B', 'Hyp.C']")


@pytest.mark.parametrize("change, needle", [
    (lambda cs: cs[1].update(witness="p/wit"),
     "claims[1] (p/proof-a): witness is set but the claim has no hypotheses"),
    (lambda cs: cs[0].update(witness="p/main"), "claims[0] (p/main): witness names the claim itself"),
    (lambda cs: cs[0].update(witness="p/thing"), "p/main: witness 'p/thing' has kind 'definition'"),
    (lambda cs: cs[0].update(witness="p/nope"), "p/main: witness 'p/nope' is not a claim in this file"),
    (lambda cs: cs[0].update(witness="Q/wit"), f"p/main: witness 'Q/wit' is a claim of another package: {CROSS}"),
    (lambda cs: cs[0].update(witness=["p/wit"]), "claims[0].witness must be a claim id <package>/<slug>"),
])
def test_witness_refusals(cl, change, needle):
    refused(cl, change, needle)


def test_claims_check_file_names_the_refusal(lp, capsys, tmp_path):
    d = proposal_doc()
    d["claims"][3]["discharges"]["claim"] = "Q/slug"
    f = tmp_path / "claims.yaml"
    f.write_text(yaml.safe_dump(d, sort_keys=False))
    capsys.readouterr()
    assert lp.main(["--registry", str(tmp_path / "none.yaml"), "--json", "claims", "check", "--file", str(f)]) == 1
    assert any(CROSS in e for e in json.loads(capsys.readouterr().out)["errors"])


# --- claims list: a static marker ----------------------------------------------------------------------------

def test_claims_list_marks_a_claim_with_hypotheses_conditional(lp, env, capsys):
    reg = read_reg(env)
    base = yaml.safe_load(CLAIMS_YAML)["claims"][0]
    capsys.readouterr()
    assert lp.main(["--registry", str(env.registry), "--json", "claims", "list", "beta"]) == 0
    before = json.loads(capsys.readouterr().out)
    assert all("conditional" not in r for r in before["claims"])
    reg["packages"]["beta"]["claims"] = [dict(base, hypotheses=["Hyp.A"])]
    reg["packages"]["beta"]["claims_synced_commit"] = "0" * 40
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "--json", "claims", "list", "beta"]) == 0
    assert json.loads(capsys.readouterr().out)["claims"][0]["conditional"] is True
    assert lp.main(["--registry", str(env.registry), "claims", "list", "beta"]) == 0
    row = capsys.readouterr().out.splitlines()[1]
    assert "reusable (conditional)" in row


# --- the ledger: fail closed until stage H3 ------------------------------------------------------------------

@pytest.mark.parametrize("keys", [
    {"hypotheses": ["Hyp.A"]},
    {"hypotheses": ["Hyp.A"], "witness": "beta/baz"},
    {"discharges": {"claim": "beta/zap", "hypotheses": ["Hyp.A"], "proofs": ["beta/baz"]}},
])
def test_a_claim_using_the_keys_is_hypotheses_unchecked_never_verified(lp, env, capsys, keys):
    rows = claims_list(**{"foo-le-bar": dict(claim("foo-le-bar", "Demo.foo_le_bar"), **keys)})
    if "discharges" in keys:     # the target needs the hypothesis; the corollary keeps none
        rows = claims_list(**{"foo-le-bar": dict(claim("foo-le-bar", "Demo.foo_le_bar"), **keys),
                              "zap": dict(claim("zap", "Demo.zap", deps=["beta/baz"]), hypotheses=["Hyp.A"])})
    gated(env, claims=rows)
    for stage in ("promote", "gate"):
        rc, data = ledger(lp, env, capsys, stage=stage)
        row = by_id(data)["foo-le-bar"]
        assert codes(row) == ["hypotheses-unchecked"], row
        assert row["status"] == "open" and data["package_status"] == "open" and rc == 1
        assert data["counts"]["verified"] == sum(r["status"] == "verified" for r in data["claims"])
        assert "proposal 0003" in row["diagnostics"][0]["detail"]
    lp.main(["--registry", str(env.registry), "claims", "ledger", "beta"])
    text = capsys.readouterr().out
    assert "  open         beta/foo-le-bar" in text and "hypotheses-unchecked:" in text


def test_a_package_without_the_keys_keeps_its_ledger_digest(lp, env, capsys, cl):
    """The rows, codes and digest of a package that uses none of the keys do not change: the new code is the
    only change and it is never emitted for them (the digest was computed by the same function before H2,
    so equality with a ledger whose claims spell the keys as empty proves the keys do not enter it)."""
    gated(env)
    rc, a = ledger(lp, env, capsys)
    assert rc == 0 and a["package_status"] == "ready"
    rows = [dict(r, hypotheses=[]) for r in claims_list()]
    gated(env, claims=rows)
    rc, b = ledger(lp, env, capsys)
    assert rc == 0 and b["package_status"] == "ready"
    assert [(r["id"], r["status"], codes(r)) for r in a["claims"]] == [(r["id"], r["status"], codes(r))
                                                                       for r in b["claims"]]
    assert set(a) == set(b) and all(set(r) == set(s) for r, s in zip(a["claims"], b["claims"], strict=True))


def test_the_page_never_calls_a_conditional_row_verified(lp, env, capsys):
    claims_text = yaml.safe_load(CLAIMS_YAML)
    claims_text["claims"][0]["hypotheses"] = ["Hyp.A"]
    cov = copy.deepcopy(COVERAGE)
    cov["rows"][0].update({"class": "inputs-supplied",
                           "inputs": [{"hypothesis": "Hyp.A", "text": "the a priori bound, Lemma 3.1"}]})
    prepare(env, cov, extra={"claims.yaml": yaml.safe_dump(claims_text, sort_keys=False)})
    rc, a = page_json(lp, env, capsys)
    md = a["markdown"]
    block = md.split("### Theorem 1: Main bound\n", 1)[1].split("\n## ", 1)[0]
    assert rc == 0 and a["ledger_status"] == "open" and a["verified_rows"] == 0
    assert "**Verified.**" not in md and block.startswith("\n**Not yet verified.**")
    assert "the a priori bound, Lemma 3.1 (`Hyp.A`)" in block
    rc, out, _ = run(lp, env, capsys, "--json", "claims", "ledger", "beta")
    row = json.loads(out)["claims"][0]
    assert codes(row) == ["hypotheses-unchecked"] and row["status"] == "open"


# --- coverage/1: inputs that name a hypothesis ---------------------------------------------------------------

def _cov_with_inputs(inputs) -> dict:
    cov = copy.deepcopy(COVERAGE)
    cov["rows"][0].update({"class": "inputs-supplied", "inputs": inputs})
    return cov


def _claims_with_hyps(hyps) -> str:
    d = yaml.safe_load(CLAIMS_YAML)
    d["claims"][0]["hypotheses"] = hyps
    return yaml.safe_dump(d, sort_keys=False)


@pytest.mark.parametrize("inputs, needle", [
    ([{"hypothesis": "Hyp.A", "text": "x", "note": "y"}], "rows[0].inputs[0]: unknown key(s) note"),
    ([{"hypothesis": "Hyp.A"}], "rows[0].inputs[0]: missing key(s) text"),
    ([{"hypothesis": "not a name", "text": "x"}], "rows[0].inputs[0].hypothesis: must be the fully qualified"),
    ([{"hypothesis": "Hyp.A", "text": "two\nlines"}], "rows[0].inputs[0].text: must be one line"),
    ([{"hypothesis": "Hyp.A", "text": "x"}, {"hypothesis": "Hyp.A", "text": "y"}],
     "rows[0].inputs: names hypothesis Hyp.A more than once"),
    ([3], "rows[0].inputs[0]: must be a one-line string (at most 400 characters) or a mapping {hypothesis, text}"),
])
def test_input_mapping_refusals_offline(lp, capsys, tmp_path, inputs, needle):
    d = package(tmp_path, coverage=_cov_with_inputs(inputs), claims=_claims_with_hyps(["Hyp.A"]))
    rc, data, _ = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and any(needle in e for e in data["errors"]), data["errors"]


def test_an_input_hypothesis_must_be_a_hypothesis_of_the_rows_claims_offline(lp, capsys, tmp_path):
    good = _cov_with_inputs([{"hypothesis": "Hyp.A", "text": "the tail estimate"}, "a free-text input"])
    d = package(tmp_path, coverage=good, claims=_claims_with_hyps(["Hyp.A"]))
    rc, data, _ = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and check_status(data, "input hypotheses") == "pass", data["errors"]
    d = package(tmp_path, coverage=_cov_with_inputs([{"hypothesis": "Hyp.B", "text": "the tail estimate"}]),
                claims=_claims_with_hyps(["Hyp.A"]))
    rc, data, _ = offline(lp, capsys, tmp_path, d)
    assert rc == 1 and check_status(data, "input hypotheses") == "fail"
    assert any("rows[0].inputs[0].hypothesis: 'Hyp.B' is not in the hypotheses of any claim the row names "
               "(beta/foo-le-bar)" in e for e in data["errors"]), data["errors"]
    rc, data, _ = offline(lp, capsys, tmp_path, d, claims=False)
    assert check_status(data, "input hypotheses") == "skipped"


def test_string_inputs_add_no_check_entry(lp, capsys, tmp_path):
    d = package(tmp_path, coverage=_cov_with_inputs(["a free-text input"]))
    rc, data, _ = offline(lp, capsys, tmp_path, d)
    assert rc == 0 and "input hypotheses" not in [x["check"] for x in data["checks"]]


def test_an_input_hypothesis_is_checked_in_the_registered_mode(lp, env, capsys, tmp_path):
    claims_text = _claims_with_hyps(["Hyp.A"])
    prepare(env, extra={"claims.yaml": claims_text, "Demo/Main.lean": DEMO_LEAN})
    for hyp, ok in (("Hyp.A", True), ("Hyp.B", False)):
        f = tmp_path / f"cov-{hyp}.yaml"
        f.write_text(yaml.safe_dump(_cov_with_inputs([{"hypothesis": hyp, "text": "the bound"}]), sort_keys=False))
        rc, out, _ = run(lp, env, capsys, "--json", "coverage", "check", "beta", "--file", str(f))
        data = json.loads(out)
        assert data["ok"] is ok and rc == (0 if ok else 1), data["errors"]
        if not ok:
            assert any("'Hyp.B' is not in the hypotheses of any claim the row names (beta/foo-le-bar) in "
                       "claims.yaml at HEAD" in e for e in data["errors"]), data["errors"]
