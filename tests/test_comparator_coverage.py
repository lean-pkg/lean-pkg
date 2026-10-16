#!/usr/bin/env python3
"""#26.5: `comparator config --coverage NAME` explains, read-only, what the comparator configuration tracked
at the gate-bound commit covers, and that a changed or uncommitted configuration cannot certify that tree."""

from __future__ import annotations

import json

import yaml

from .conftest import _git, commit_claims, make_receipt, read_events, read_reg, repo_head, write_reg

LEAN = ("namespace Demo\n"   # the claims name Demo.<decl>: exact covers() (#80 fix)
        "theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\ndef qux := 1\n"
        "theorem old_one : True := trivial\ntheorem zap : True := trivial\n"
        "end Demo\n")


def claim(cid, decl, kind="theorem", status="reusable", sb=None):
    return {"id": f"beta/{cid}", "decl": decl, "kind": kind, "summary": f"{cid} summary",
            "file": "Demo/Main.lean", "status": status, "superseded_by": sb}


CLAIMS = {"schema": "claims/1", "package": "beta", "claims": [
    claim("foo-le-bar", "Demo.foo_le_bar"),                    # covered by comparator.json
    claim("baz", "Demo.baz", kind="lemma"),
    claim("qux", "Demo.qux", kind="definition"),                # not compared
    claim("old-one", "Demo.old_one", status="superseded", sb="beta/zap"),
    claim("zap", "Demo.zap"),
]}


def gated_with_claims(lp, env, capsys) -> str:
    """beta re-gated at the commit that tracks claims.yaml and comparator.json; claims synced."""
    commit_claims(env.repo_b, yaml.safe_dump(CLAIMS, sort_keys=False), lean=LEAN)
    g = repo_head(env.repo_b)
    (env.repo_b / ".lean-receipts" / "g.json").write_text(json.dumps(make_receipt(env.repo_b)))
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"].update(receipt=".lean-receipts/g.json", comparator=None)
    write_reg(env, reg)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    return g


def coverage(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--coverage", "beta", *extra])
    return rc, capsys.readouterr()


def test_coverage_counts_at_the_bound_commit_and_writes_nothing(lp, env, capsys):
    g = gated_with_claims(lp, env, capsys)
    before_reg, before_ev = env.registry.read_text(), read_events(env)
    before_cfg = (env.repo_b / "comparator.json").read_bytes()
    rc, cap = coverage(lp, env, capsys)
    assert rc == 0
    assert f"coverage at the gate-bound commit {g[:12]}" in cap.out
    assert "3 theorem/lemma claim(s): 1 covered, 2 not covered, 0 listed only as scaffold TODO; 2 other" in cap.out
    assert "beta/baz (Demo.baz)" in cap.out and "config unchanged since" in cap.out
    assert "cannot certify" in cap.out and "a new gate at a commit that tracks the config is required" in cap.out
    assert "lean-pkg comparator config --from-claims beta" in cap.out
    assert env.registry.read_text() == before_reg and read_events(env) == before_ev
    assert (env.repo_b / "comparator.json").read_bytes() == before_cfg


def test_uncommitted_candidate_is_never_substituted(lp, env, capsys):
    g = gated_with_claims(lp, env, capsys)
    assert lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta"]) == 0
    capsys.readouterr()
    rc, cap = coverage(lp, env, capsys, "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["bound_commit"] == g
    assert (data["covered"], data["not_covered"], data["todo"]) == (1, 2, 0)     # still the bound config
    assert data["config_changed"] is True
    assert data["changed_configs"] == [{"path": "comparator.json",
                                        "change": "modified in the working tree (uncommitted)"}]
    assert data["candidate"]["certifies"] is False and data["candidate"]["todo"] == 2
    assert data["next_commands"][0].startswith(f"git -C {env.repo_b} add comparator.json")
    assert any(c.startswith("lean-pkg gate beta --receipt R") for c in data["next_commands"])
    assert any(c.startswith("lean-pkg update beta --status gate-pending") for c in data["next_commands"])
    assert any(c.startswith("lean-pkg comparator beta --receipt") for c in data["next_commands"])


def test_config_committed_after_the_gate_still_needs_a_new_gate(lp, env, capsys):
    g = gated_with_claims(lp, env, capsys)
    assert lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta"]) == 0
    _git(env.repo_b, "commit", "-qam", "scaffold")
    capsys.readouterr()
    rc, cap = coverage(lp, env, capsys)
    assert rc == 0
    assert f"comparator.json changed at HEAD {repo_head(env.repo_b)[:12]} since {g[:12]}" in cap.out
    assert "1 covered, 2 not covered" in cap.out                                  # judged at the bound commit
    assert "git -C" not in cap.out                                                # nothing left to commit


def test_coverage_needs_a_gate(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["gate"]["receipt"] = None
    write_reg(env, reg)
    rc, cap = coverage(lp, env, capsys)
    assert rc == 1 and "no gate-bound commit" in cap.err


def test_coverage_takes_no_write_flags(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--coverage", "beta", "--dry-run"])
    assert rc == 2 and "read-only" in capsys.readouterr().err
    rc = lp.main(["--registry", str(env.registry), "comparator", "beta", "--coverage", "beta"])
    assert rc == 2


def test_coverage_is_not_a_write_invocation(lp):
    import argparse
    args = argparse.Namespace(cmd="comparator", name="config", receipt=None, check=False)
    assert lp.is_write_invocation(args) is False
