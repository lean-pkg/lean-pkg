#!/usr/bin/env python3
"""Proposal 0003, stage H5 (#320): open question 7, a warning (never an error) in `coverage check` and in
`receipt check`'s coverage item for an assumption a mapped theorem takes that is not listed, read only from the
hyp-probe/1 section of the gate receipt's sealed verify log; and the conditional marker of `search`. Neither
changes a byte for a package that does not trigger it."""

from __future__ import annotations

import copy
import json

import yaml

from .conftest import (MATHLIB_MANIFEST, MIT_TEXT, _git, make_receipt, make_repo, put_receipt, read_reg,
                       repo_head, run_search, write_reg, write_report_log)
from .test_claims_ledger import binder, hyp_log
from .test_conditional_outputs import STATEMENTS

STMT = {"Demo/Statements.lean": STATEMENTS}


def _beta(env, monkeypatch, *, binders, cls="same-assumptions", hyps=None, inputs=None, log=True, library=False):
    """beta made page-ready (test_page.prepare) with beta/foo-le-bar's declaration taking `binders` in a sealed
    hyp-probe/1 section (or no readable log when `log` is False)."""
    from lean_pkg import gate as gate_mod

    from .conftest import CLAIMS_YAML
    from .test_page import COVERAGE, LIBRARY, prepare
    claims = yaml.safe_load(CLAIMS_YAML)
    if hyps:
        claims["claims"][0]["hypotheses"] = list(hyps)
    cov = copy.deepcopy(LIBRARY if library else COVERAGE)
    if not library:
        cov["rows"][0]["class"] = cls
        if inputs is not None:
            cov["rows"][0]["inputs"] = inputs
    prepare(env, cov, extra={"claims.yaml": yaml.safe_dump(claims, sort_keys=False), **STMT})
    text = hyp_log(decls={"Demo.foo_le_bar": tuple(binders)}, challenge={}, facts=[], configs=())
    monkeypatch.setattr(gate_mod, "sealed_log_text", lambda repo, rec, rel: text if log else None)


def _check(lp, env, capsys):
    capsys.readouterr()
    rc = lp.main(["--registry", str(env.registry), "coverage", "check", "beta"])
    text = capsys.readouterr().out
    lp.main(["--registry", str(env.registry), "--json", "coverage", "check", "beta"])
    return rc, text, json.loads(capsys.readouterr().out)


def test_an_unlisted_package_assumption_of_a_same_assumptions_row_warns(lp, env, capsys, monkeypatch):
    _beta(env, monkeypatch, binders=[binder(0, "Hyp.A"), binder(1, "Hyp.B", explicit=False)])
    rc, text, data = _check(lp, env, capsys)
    assert rc == 0 and data["ok"] and text.startswith("beta: coverage: OK; 2 row(s)")      # never an error
    w = [x for x in data["warnings"] if "open question 7" in x]
    assert len(w) == 2, data["warnings"]
    assert w[0] == ("rows[0] (Theorem 1, same-assumptions): claim beta/foo-le-bar (Demo.foo_le_bar) takes the "
                    "assumption Hyp.A, a Prop def of the package (Demo.Statements), that neither the claim's "
                    "hypotheses nor the row's inputs name (the gate receipt's sealed verify log): the claims ledger "
                    "calls it verified without it. List it under hypotheses of beta/foo-le-bar and name it in the "
                    "row's inputs (class inputs-supplied), or make it part of the statement (proposal 0003, open "
                    "question 7)")
    assert "assumption Hyp.B," in w[1]
    assert f"  warning: {w[0]}" in text.splitlines()
    assert text.splitlines()[0] == ("beta: coverage: OK; 2 row(s): 1 same-assumptions, 0 inputs-supplied, 1 "
                                    "outside; manuscript arxiv")      # docs/page.md#unlisted-assumptions


def test_a_listed_assumption_does_not_warn_and_an_unlisted_one_beside_it_does(lp, env, capsys, monkeypatch):
    _beta(env, monkeypatch, binders=[binder(0, "Hyp.A"), binder(1, "Hyp.B")], cls="inputs-supplied",
          hyps=["Hyp.A"], inputs=[{"hypothesis": "Hyp.A", "text": "the a priori bound"}])
    rc, _text, data = _check(lp, env, capsys)
    w = [x for x in data["warnings"] if "open question 7" in x]
    assert rc == 0 and len(w) == 1 and "rows[0] (Theorem 1, inputs-supplied)" in w[0] and "Hyp.B" in w[0]
    assert "Hyp.A" not in w[0]


def test_no_warning_for_a_head_outside_the_package(lp, env, capsys, monkeypatch):
    mathlib = dict(binder(0, "MeasureTheory.Integrable"), module="Mathlib.MeasureTheory.Function.L1Space")
    _beta(env, monkeypatch, binders=[mathlib, dict(binder(1, "Hyp.Z"), module="Demo.Missing")])
    rc, _text, data = _check(lp, env, capsys)
    assert rc == 0 and not [x for x in data["warnings"] if "open question 7" in x]


def test_no_warning_for_a_head_that_is_not_a_def(lp, env, capsys, monkeypatch):
    _beta(env, monkeypatch, binders=[binder(0, "Hyp.A", kind="inductive"), binder(1, "Hyp.B", kind="theorem")])
    rc, _text, data = _check(lp, env, capsys)
    assert rc == 0 and not [x for x in data["warnings"] if "open question 7" in x]


def test_no_warning_without_a_readable_sealed_log(lp, env, capsys, monkeypatch):
    _beta(env, monkeypatch, binders=[binder(0, "Hyp.A")], log=False)
    rc, text, data = _check(lp, env, capsys)
    assert rc == 0 and data["warnings"] == [] and "warning" not in text


def test_no_warning_without_a_hyp_probe_section(lp, env, capsys):
    """The real sealed log of the fixture's gate receipt, which carries no hyp-probe/1 section."""
    from .test_page import prepare
    prepare(env)
    rc, text, data = _check(lp, env, capsys)
    assert rc == 0 and data["warnings"] == [] and "warning" not in text


def test_no_warning_for_a_library_row(lp, env, capsys, monkeypatch):
    _beta(env, monkeypatch, binders=[binder(0, "Hyp.A")], library=True)
    rc, _text, data = _check(lp, env, capsys)
    assert rc == 0 and data["kind"] == "library" and data["warnings"] == []


def test_no_warning_without_a_gate_receipt(lp, env, capsys, monkeypatch):
    _beta(env, monkeypatch, binders=[binder(0, "Hyp.A")])
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["receipt"] = None
    write_reg(env, reg)
    rc, _text, data = _check(lp, env, capsys)
    assert rc == 0 and data["warnings"] == []


# --- receipt check's coverage item ---------------------------------------------------------------------------

def _published(tmp_path, log: str, *, n_hyps: int = 1):
    """demo published with coverage.yaml mapping demo/main (no claim keys), its sealed log carrying `log`."""
    from .test_page import COVERAGE
    cov = copy.deepcopy(COVERAGE)
    cov["package"] = "demo"
    cov["rows"][0]["claims"] = ["demo/main"]
    claims = [{"id": "demo/main", "decl": "main", "kind": "theorem", "summary": "main summary",
               "file": "Demo/Main.lean", "status": "reusable", "superseded_by": None, "depends_on": []}]
    repo = make_repo(tmp_path / "demo-repo", extra_files={
        "LICENSE": MIT_TEXT, "lake-manifest.json": MATHLIB_MANIFEST, **STMT,
        "Demo/Main.lean": "theorem main : True := trivial\n",
        "claims.yaml": yaml.safe_dump({"schema": "claims/1", "package": "demo", "claims": claims}, sort_keys=False),
        "coverage.yaml": yaml.safe_dump(cov, sort_keys=False)})
    x = repo_head(repo)
    (repo / ".lean-receipts").mkdir()
    rec = make_receipt(repo)
    write_report_log(repo / ".lean-receipts", rec, log)
    put_receipt(repo / f".lean-receipts/{x[:12]}-slurm-12345.json", rec, indent=2, tail="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "evidence")
    return repo, x


def _coverage_item(lp, capsys, repo, x):
    capsys.readouterr()
    rc = lp.main(["receipt", "check", "--repo", str(repo), "--commit", x, "--json"])
    data = json.loads(capsys.readouterr().out)
    [item] = [i for i in data["checks"] if i["id"] == "coverage"]
    return rc, item, data


def test_receipt_check_coverage_item_warns_and_still_passes(lp, tmp_path, capsys):
    log = hyp_log(decls={"main": (binder(0, "Hyp.A"), binder(1, "Hyp.B"))}, challenge={}, facts=[], configs=())
    repo, x = _published(tmp_path, log)
    rc, item, data = _coverage_item(lp, capsys, repo, x)
    assert rc == 0 and item["verdict"] == "pass", item
    assert item["detail"].startswith("coverage.yaml: 2 row(s); every compared theorem is the declaration of a claim "
                                     "a row names (R11); warning: rows[0] (Theorem 1, same-assumptions): claim "
                                     "demo/main (main) takes the assumption Hyp.A,"), item
    assert item["detail"].endswith("(proposal 0003, open question 7) (and 1 more unlisted assumption(s))")
    assert "hypotheses" not in data                 # the claims use no key: no hypotheses check


def test_receipt_check_coverage_item_is_unchanged_without_the_section(lp, tmp_path, capsys):
    repo, x = _published(tmp_path, "fixture verify log\n")
    rc, item, _data = _coverage_item(lp, capsys, repo, x)
    assert rc == 0 and item["verdict"] == "pass"
    assert item["detail"] == ("coverage.yaml: 2 row(s); every compared theorem is the declaration of a claim a row "
                              "names (R11)")


def test_receipt_check_coverage_item_without_a_lower_receipt_reads_no_log(tmp_path, monkeypatch):
    from lean_pkg import gate as gate_mod
    from lean_pkg import receipt_check as rc_mod
    repo, _x = _published(tmp_path, hyp_log(decls={"main": (binder(0, "Hyp.A"),)}, challenge={}, facts=[],
                                            configs=()))
    monkeypatch.setattr(gate_mod, "sealed_log_text", lambda *a: (_ for _ in ()).throw(AssertionError("read")))
    c = rc_mod.Checks(repo, repo)
    rc_mod.coverage_check(c, repo, rc_mod.referee_pkg("demo", False), {"lower": [], "comparator": []})
    assert [(i["id"], i["verdict"]) for i in c.items] == [("coverage", "pass")] and "warning" not in c.items[0]["detail"]


# --- search ----------------------------------------------------------------------------------------------------

def test_search_marks_a_registry_claim_with_hypotheses_conditional(lp, srch, searchable, capsys):
    reg = read_reg(searchable.env)
    reg["packages"]["beta"]["claims"][0]["hypotheses"] = ["Hyp.A"]
    write_reg(searchable.env, reg)
    assert run_search(lp, searchable, "foo bound", "--offline", "--json") == 0
    [h] = json.loads(capsys.readouterr().out)["sources"][0]["hits"]
    assert h["conditional"] is True and set(h) == set(srch.HIT_KEYS) | set(srch.OPTIONAL_HIT_KEYS)
    assert h["status"] == "reusable"                # the status is the claim's; the marker is beside it
    assert run_search(lp, searchable, "foo bound", "--offline") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("  [3 alias] beta/foo-le-bar  Demo.foo_le_bar  (theorem, reusable (conditional), "
                             "trust comparator, pin "), out


def test_search_marks_an_index_claim_whose_derived_status_is_conditional(lp, srch, searchable, capsys):
    from lean_pkg import index as idx
    path = searchable.index / "demo" / idx.ENTRY_FILE
    entry = yaml.safe_load(path.read_text())
    entry["claims"][0].update(status="conditional", open_hypotheses=["Hyp.A"])
    path.write_text(idx.dump_entry(entry))
    assert run_search(lp, searchable, "trivial demonstration", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    [h] = [b for b in p["sources"] if b["source"] == "index"][0]["hits"]
    assert h["conditional"] is True and h["status"] == "reusable" and h["trust"] == "comparator"
    assert run_search(lp, searchable, "trivial demonstration", "--offline") == 0
    assert "(theorem, reusable (conditional), trust comparator, pin " in capsys.readouterr().out


def test_search_hits_without_hypotheses_carry_no_marker(lp, srch, searchable, capsys):
    assert run_search(lp, searchable, "demo", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    hits = [h for b in p["sources"] if b["source"] in ("registry", "index") for h in b["hits"]]
    assert hits and all("conditional" not in h for h in hits)
    assert run_search(lp, searchable, "demo", "--offline") == 0
    assert "conditional" not in capsys.readouterr().out
