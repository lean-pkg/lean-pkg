#!/usr/bin/env python3
"""status NAME: the scope block (#5 field report, P3): the gated source commit, the recorded privacy and
publication hold, the exact declarations the recorded comparator receipt compared and how many claimed
declarations they cover (never "full comparison" when it compared one theorem), and claim counts by
status (counts, never percentages). Offline and read-only."""

from __future__ import annotations

import json

from .conftest import read_reg, repo_head, write_reg


def status(lp, env, capsys, *extra) -> tuple[int, str]:
    rc = lp.main(["--registry", str(env.registry), "status", "beta", *extra])
    return rc, capsys.readouterr().out


def sync(lp, env, capsys) -> None:
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()


def test_scope_names_gated_commit_and_exact_comparator_declarations(lp, env, capsys):
    sync(lp, env, capsys)
    rc, out = status(lp, env, capsys, "--json")
    sc = json.loads(out)["scope"]
    head = repo_head(env.repo_b)
    assert sc["gate"]["commit"] == env.receipt_b["commit"] and sc["gate"]["head"] == head
    assert sc["publication"] == {"status": "verified-private", "visibility": "private", "publication_hold": None,
                                 "github": sc["publication"]["github"]}
    comp = sc["comparator"]
    assert comp["theorem_names"] == ["BetaAudit.Demo.foo_le_bar"] and comp["whole_package"] is False
    assert (comp["claimed_theorems"], comp["claimed_theorems_covered"]) == (1, 1)
    assert sc["claims"]["by_status"] == {"reusable": 1}


def test_a_one_theorem_comparator_never_reads_as_full_comparison(lp, env, capsys):
    sync(lp, env, capsys)
    reg = read_reg(env)
    extra = dict(reg["packages"]["beta"]["claims"][0], id="beta/mass-bound", decl="Demo.mass_bound")
    more = [dict(extra, id=f"beta/exp-{i}", decl=f"Demo.exp_{i}", status="experimental") for i in range(3)]
    reg["packages"]["beta"]["claims"] += [extra] + more
    write_reg(env, reg)
    rc, out = status(lp, env, capsys)
    assert rc == 0
    assert "compared exactly 1 declaration(s), not the whole package: BetaAudit.Demo.foo_le_bar" in out
    assert "covers 1 of 5 claimed theorem/lemma declaration(s) (1 of 2 reusable); the rest are not compared" in out
    assert "claim counts: 5 (3 experimental, 2 reusable); counts, not completion percentages" in out
    assert "%" not in out.split("scope:", 1)[1]


def test_scope_without_evidence_and_with_a_hold(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["receipt"] = None
    reg["packages"]["beta"]["gate"]["comparator"] = None
    reg["packages"]["beta"]["status"] = "active"
    reg["packages"]["beta"]["publication_hold"] = {"reason": "owner made it private",
                                                   "at": "2026-10-17T00:00:00+00:00", "by": "vera"}
    write_reg(env, reg)
    rc, out = status(lp, env, capsys)
    assert "gated source commit: none (no gate receipt recorded)" in out
    assert "comparator: none recorded" in out
    assert "publication hold: publication hold since 2026-10-17T00:00:00+00:00 by vera: owner made it private" in out


def test_fields_mode_is_unchanged(lp, env, capsys):
    rc, out = status(lp, env, capsys, "--fields", "status", "--json")
    assert "scope" not in json.loads(out)
