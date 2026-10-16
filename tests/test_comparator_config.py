#!/usr/bin/env python3
"""#23.6: `comparator config --from-claims NAME` scaffolds TODO comparator entries for unconfigured claims."""

from __future__ import annotations

import json

import yaml

from .conftest import _git, commit_claims, drop_claims, read_events, read_reg, set_lease

LEAN = ("namespace Demo\n"   # the claims name Demo.<decl>: exact covers() (#80 fix)
        "theorem foo_le_bar : True := trivial\nlemma baz : True := trivial\ndef qux := 1\n"
        "theorem old_one : True := trivial\ntheorem zap : True := trivial\n"
        "end Demo\n")


def claim(cid, decl, kind="theorem", status="reusable", sb=None):
    return {"id": f"beta/{cid}", "decl": decl, "kind": kind, "summary": f"{cid} summary",
            "file": "Demo/Main.lean", "status": status, "superseded_by": sb}


CLAIMS = {"schema": "claims/1", "package": "beta", "claims": [
    claim("foo-le-bar", "Demo.foo_le_bar"),                    # configured by hand (comparator.json)
    claim("baz", "Demo.baz", kind="lemma"),
    claim("qux", "Demo.qux", kind="definition"),                # skipped: not a proposition
    claim("old-one", "Demo.old_one", status="superseded", sb="beta/zap"),   # skipped
    claim("zap", "Demo.zap"),
]}


def synced(lp, env, capsys):
    commit_claims(env.repo_b, yaml.safe_dump(CLAIMS, sort_keys=False), lean=LEAN)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()


def cc(lp, env, capsys, *extra):
    rc = lp.main(["--registry", str(env.registry), "comparator", "config", "--from-claims", "beta", *extra])
    return rc, capsys.readouterr()


def test_dry_run_prints_the_would_be_json_and_writes_nothing(lp, env, capsys):
    synced(lp, env, capsys)
    before = (env.repo_b / "comparator.json").read_text()
    before_reg, before_ev = env.registry.read_text(), read_events(env)
    rc, cap = cc(lp, env, capsys, "--dry-run")
    assert rc == 0
    assert "5 claim(s): 1 already configured, 2 added, 2 skipped" in cap.out
    cfg = json.loads(cap.out[cap.out.index("{"):])
    assert cfg["theorem_names"] == ["BetaAudit.Demo.foo_le_bar", "BetaAudit.Demo.baz", "BetaAudit.Demo.zap"]
    todo = cfg["lean_pkg_scaffold"]["todo"]
    assert [t["claim"] for t in todo] == ["beta/baz", "beta/zap"]
    assert all(t["challenge"].startswith("TODO: ") and t["solution"].startswith("TODO: ") for t in todo)
    assert (env.repo_b / "comparator.json").read_text() == before
    assert env.registry.read_text() == before_reg and read_events(env) == before_ev


def test_write_keeps_every_existing_key_and_entry_and_is_idempotent(lp, env, capsys):
    synced(lp, env, capsys)
    old = json.loads((env.repo_b / "comparator.json").read_text())
    rc, cap = cc(lp, env, capsys, "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["written"] is True and (data["existing"], data["added"], data["skipped"]) == (1, 2, 2)
    new = json.loads((env.repo_b / "comparator.json").read_text())
    for k, v in old.items():
        if k != "theorem_names":
            assert new[k] == v, k
    assert new["theorem_names"][:1] == old["theorem_names"]
    rc, cap = cc(lp, env, capsys, "--json")                      # second run: TODO entries count as existing
    data = json.loads(cap.out)
    assert rc == 0 and data["added"] == 0 and data["existing"] == 3 and data["written"] is False


def test_new_out_file_and_namespace(lp, env, capsys):
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--out", "comparator/config-claims.json", "--challenge-namespace", "Ns.Audit")
    assert rc == 0
    cfg = json.loads((env.repo_b / "comparator" / "config-claims.json").read_text())
    assert cfg["challenge_module"] == "Challenge" and cfg["permitted_axioms"] == ["propext", "Quot.sound",
                                                                                 "Classical.choice"]
    assert cfg["theorem_names"] == ["Ns.Audit.Demo.baz", "Ns.Audit.Demo.zap"]   # foo_le_bar: comparator.json


def test_refuses_without_a_tracked_claims_file(lp, env, capsys):
    drop_claims(env.repo_b)
    rc, cap = cc(lp, env, capsys)
    assert rc == 1 and "claims.yaml is not tracked at HEAD" in cap.err


def test_refuses_when_claims_are_not_synced(lp, env, capsys):
    commit_claims(env.repo_b, yaml.safe_dump(CLAIMS, sort_keys=False), lean=LEAN)
    rc, cap = cc(lp, env, capsys)
    assert rc == 1 and "claims are not synced" in cap.err


def test_refuses_when_claims_are_synced_at_an_older_commit(lp, env, capsys):
    synced(lp, env, capsys)
    (env.repo_b / "Demo" / "Extra.lean").write_text("-- more\n")
    _git(env.repo_b, "add", "-A")
    _git(env.repo_b, "commit", "-q", "-m", "more source")
    rc, cap = cc(lp, env, capsys)
    assert rc == 1 and "not at HEAD" in cap.err


def test_refuses_an_out_path_the_runner_would_not_find(lp, env, capsys):
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--out", "configs/x.json")
    assert rc == 1 and "not a comparator configuration path" in cap.err


def test_refuses_an_out_path_whose_log_name_collides(lp, env, capsys):
    """comparator_config_a.json and comparator/config_a.json share the log name comparator_config_a."""
    (env.repo_b / "comparator_config_a.json").write_text((env.repo_b / "comparator.json").read_text())
    _git(env.repo_b, "add", "comparator_config_a.json")
    _git(env.repo_b, "commit", "-q", "-m", "second config")
    synced(lp, env, capsys)
    rc, cap = cc(lp, env, capsys, "--out", "comparator/config_a.json")
    assert rc == 1 and "comparator_config_a.json and comparator/config_a.json" in cap.err
    assert "comparator_config_a.log" in cap.err
    assert not (env.repo_b / "comparator" / "config_a.json").exists()
    rc, cap = cc(lp, env, capsys, "--out", "comparator/config-b.json")   # no collision: written
    assert rc == 0, cap.err


def test_refuses_a_malformed_existing_config(lp, env, capsys):
    synced(lp, env, capsys)
    (env.repo_b / "comparator.json").write_text('{"theorem_names": "x"}\n')
    rc, cap = cc(lp, env, capsys)
    assert rc == 1 and "theorem_names must be a list of strings" in cap.err


def test_a_foreign_live_lease_refuses_the_write(lp, env, capsys):
    synced(lp, env, capsys)
    set_lease(env, "beta", agent="vera")
    before = (env.repo_b / "comparator.json").read_text()
    rc, cap = cc(lp, env, capsys)
    assert rc == 1 and "lease held by vera" in cap.err
    assert (env.repo_b / "comparator.json").read_text() == before
    assert cc(lp, env, capsys, "--agent", "vera")[0] == 0


def test_config_flags_elsewhere_are_a_usage_error(lp, env, capsys):
    rc = lp.main(["--registry", str(env.registry), "comparator", "beta", "--receipt", "x.json", "--dry-run"])
    assert rc == 2 and "comparator config --from-claims" in capsys.readouterr().err
    rc = lp.main(["--registry", str(env.registry), "comparator", "config"])
    assert rc == 2 and "--from-claims NAME" in capsys.readouterr().err


def test_todo_names_stay_out_of_formalization_main_results(lp, env, capsys):
    synced(lp, env, capsys)
    assert cc(lp, env, capsys)[0] == 0
    _git(env.repo_b, "add", "comparator.json")
    _git(env.repo_b, "commit", "-q", "-m", "scaffold")
    assert lp.main(["--registry", str(env.registry), "formalization", "beta", "--json"]) == 0
    out = capsys.readouterr()
    doc = json.loads(out.out)
    text = json.dumps(doc)
    assert "BetaAudit.Demo.baz" not in text and "foo_le_bar" in text
    assert "still marked TODO" in out.err or "still marked TODO" in text
    assert read_reg(env)["packages"]["beta"]["claims_synced_commit"]
