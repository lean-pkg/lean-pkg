#!/usr/bin/env python3
"""update NAME --path NEW: identity-preserving relocation (#13)."""

from __future__ import annotations

import json
import shutil

from .conftest import _git, make_repo, read_events, read_reg, set_lease, write_reg


def run(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), "update", *argv])


def test_relocate_happy_path_records_history_and_event(lp, env, tmp_path, capsys):
    new = make_repo(tmp_path / "alpha-dedicated")
    rc = run(lp, env, "alpha", "--path", str(new), "--reason", "companion repo split")
    assert rc == 0
    out = capsys.readouterr().out
    assert f"alpha: relocated {env.repo_a} -> {new}" in out
    assert "--redetect" in out                     # toolchain/pin note without --redetect
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["local_path"] == str(new)
    assert pkg["history"][-1]["event"] == "relocated"
    ev = read_events(env)[-1]
    assert ev["event"] == "relocated" and ev["name"] == "alpha"
    assert ev["old_path"] == str(env.repo_a) and ev["new_path"] == str(new)
    assert ev["reason"] == "companion repo split" and ev["cleared"] == []
    assert "agent" not in ev
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_relocate_requires_reason(lp, env, tmp_path, capsys):
    before = env.registry.read_text()
    assert run(lp, env, "alpha", "--path", str(tmp_path / "x")) == 1
    assert "--reason" in capsys.readouterr().err
    assert env.registry.read_text() == before and read_events(env) == []


def test_relocate_same_path_refused(lp, env, capsys):
    before = env.registry.read_text()
    assert run(lp, env, "alpha", "--path", str(env.repo_a) + "/", "--reason", "r") == 1
    assert "already at" in capsys.readouterr().err
    assert env.registry.read_text() == before and read_events(env) == []


def test_relocate_onto_another_packages_path_refused(lp, env, capsys):
    before = env.registry.read_text()
    assert run(lp, env, "alpha", "--path", str(env.repo_b), "--reason", "r") == 1
    assert "'beta'" in capsys.readouterr().err
    assert env.registry.read_text() == before and read_events(env) == []


def test_relocate_foreign_live_lease_refused(lp, env, tmp_path, capsys):
    set_lease(env, "alpha", agent="vera")
    before = env.registry.read_text()
    new = str(tmp_path / "elsewhere")
    assert run(lp, env, "alpha", "--path", new, "--reason", "r", "--agent", "max") == 1
    err = capsys.readouterr().err
    assert "vera" in err and "until" in err
    assert env.registry.read_text() == before
    assert run(lp, env, "alpha", "--path", new, "--reason", "r") == 1   # no --agent either
    capsys.readouterr()
    assert run(lp, env, "alpha", "--path", new, "--reason", "r", "--force") == 2  # not accepted
    capsys.readouterr()
    assert run(lp, env, "alpha", "--path", new, "--reason", "r", "--agent", "vera") == 0
    assert read_events(env)[-1]["agent"] == "vera"


def test_relocate_expired_lease_does_not_block(lp, env, tmp_path):
    set_lease(env, "alpha", agent="vera", hours_ago=30, ttl=24)
    assert run(lp, env, "alpha", "--path", str(tmp_path / "x"), "--reason", "r") == 0


def test_relocate_missing_path_notes_and_clears_bindings(lp, env, tmp_path, capsys):
    new = tmp_path / "not-here"
    assert run(lp, env, "beta", "--path", str(new), "--reason", "r") == 0
    out = capsys.readouterr().out
    assert "path not present on this host" in out
    assert not new.exists()                          # never touches the file system
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["gate"]["receipt"] is None and pkg["gate"]["comparator"] is None


def test_relocate_keeps_gate_when_new_head_matches(lp, env, tmp_path):
    new = tmp_path / "beta-dedicated"
    shutil.copytree(env.repo_b, new)                 # same HEAD, receipts included
    before = read_reg(env)["packages"]["beta"]["gate"]
    assert run(lp, env, "beta", "--path", str(new), "--reason", "split") == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["gate"] == before and pkg["status"] == "verified-private"
    assert read_events(env)[-1]["cleared"] == []
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_relocate_clears_gate_and_demotes_when_head_differs(lp, env, tmp_path, capsys):
    new = tmp_path / "beta-dedicated"
    shutil.copytree(env.repo_b, new)
    (new / "Extra.lean").write_text("-- new source\n")
    _git(new, "add", "Extra.lean")
    _git(new, "commit", "-m", "source change")
    assert run(lp, env, "beta", "--path", str(new), "--reason", "split", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cleared"] == ["gate.receipt", "gate.comparator"]
    assert payload["status"] == {"from": "verified-private", "to": "gate-pending"}
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["gate"]["receipt"] is None and pkg["gate"]["comparator"] is None
    assert pkg["gate"]["axioms_allowed"]             # the un-gated shape keeps the allow-list
    assert pkg["status"] == "gate-pending"
    events = read_events(env)
    assert [e["event"] for e in events[-2:]] == ["relocated", "status-changed"]
    assert events[-2]["cleared"] == ["gate.receipt", "gate.comparator"]
    assert events[-1]["status_from"] == "verified-private"
    assert events[-1]["status_to"] == "gate-pending"
    assert [h["event"] for h in pkg["history"][-2:]] == ["relocated", "status-changed"]
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_relocate_claims_cache_kept_only_when_head_matches(lp, env, tmp_path):
    head = _git(env.repo_b, "rev-parse", "HEAD").stdout.strip()
    reg = read_reg(env)
    reg["version"] = 3
    for p in reg["packages"].values():
        p.update({"claims_file": "claims.yaml", "claims": [], "claims_synced_commit": None})
    reg["packages"]["beta"]["claims"] = [{"id": "beta/x"}]
    reg["packages"]["beta"]["claims_synced_commit"] = head
    write_reg(env, reg)
    same = tmp_path / "beta-same"
    shutil.copytree(env.repo_b, same)
    assert run(lp, env, "beta", "--path", str(same), "--reason", "r") == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["claims_synced_commit"] == head and pkg["claims"]
    other = make_repo(tmp_path / "beta-other")
    assert run(lp, env, "beta", "--path", str(other), "--reason", "r") == 0
    pkg = read_reg(env)["packages"]["beta"]
    assert pkg["claims_synced_commit"] is None and pkg["claims"] == []
    assert "claims_synced_commit" in read_events(env)[-2]["cleared"]
    assert lp.main(["--registry", str(env.registry), "validate"]) == 0


def test_relocate_redetect_rereads_toolchain(lp, env, tmp_path, capsys):
    new = make_repo(tmp_path / "alpha-new", toolchain="leanprover/lean4:v4.40.0")
    assert run(lp, env, "alpha", "--path", str(new), "--reason", "r", "--redetect") == 0
    assert "--redetect" not in capsys.readouterr().out
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["lean_toolchain"] == "leanprover/lean4:v4.40.0"
    assert read_events(env)[-1]["redetected"]["lean_toolchain"]["to"] == "leanprover/lean4:v4.40.0"


def test_relocate_redetect_needs_present_path(lp, env, tmp_path, capsys):
    before = env.registry.read_text()
    assert run(lp, env, "alpha", "--path", str(tmp_path / "nope"), "--reason", "r",
               "--redetect") == 1
    assert "not present" in capsys.readouterr().err
    assert env.registry.read_text() == before


def test_relocate_dry_run_writes_nothing(lp, env, tmp_path, capsys):
    before = env.registry.read_text()
    new = tmp_path / "beta-moved"
    assert run(lp, env, "beta", "--path", str(new), "--reason", "r", "--dry-run",
               "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["cleared"] == ["gate.receipt", "gate.comparator"]
    assert env.registry.read_text() == before and read_events(env) == []


def test_relocate_json_shape(lp, env, tmp_path, capsys):
    new = make_repo(tmp_path / "alpha-json")
    assert run(lp, env, "alpha", "--path", str(new), "--reason", "r", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    for key in ("ok", "name", "event", "old_path", "new_path", "cleared", "notes", "dry_run"):
        assert key in payload
    assert payload["ok"] is True and payload["event"] == "relocated"
    assert payload["new_path"] == str(new) and payload["dry_run"] is False


def test_relocate_refuses_mixing_with_other_fields(lp, env, tmp_path, capsys):
    assert run(lp, env, "alpha", "--path", str(tmp_path / "x"), "--reason", "r",
               "--notes", "n") == 2
    assert "on its own" in capsys.readouterr().err


def test_update_dry_run_without_path_is_usage_error(lp, env, capsys):
    before = env.registry.read_text()
    assert run(lp, env, "alpha", "--notes", "n", "--dry-run") == 2
    assert env.registry.read_text() == before
