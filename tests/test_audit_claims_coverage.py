#!/usr/bin/env python3
"""audit flags packages whose claims coverage is not `synced` (none, empty or unsynced): `search` falls
back to declarations for them. Informational: level info (also under --promotion), never fixed,
archived and upstreamed packages skipped."""

from __future__ import annotations

import json

from .conftest import commit_claims, read_events, read_reg, write_reg

CHECK = "claims-coverage-none"


def _audit(lp, env, capsys, *extra) -> dict:
    assert lp.main(["--registry", str(env.registry), "audit", "--json", "--no-gh", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def _of(data, name=None):
    return [f for f in data["findings"] if f["check"] == CHECK and (name is None or f["name"] == name)]


def test_package_without_claims_is_flagged(lp, env, capsys):
    found = _of(_audit(lp, env, capsys), "alpha")
    assert len(found) == 1, found
    f = found[0]
    assert f["level"] == "info" and f["coverage"] == "none" and f["fixed"] is False
    assert f["detail"] == ("claims: none (search falls back to declarations; "
                           "run `lean-pkg claims sync alpha`)")


def test_synced_package_is_not_flagged(lp, env, capsys):
    commit_claims(env.repo_b)
    assert lp.main(["--registry", str(env.registry), "claims", "sync", "beta"]) == 0
    capsys.readouterr()
    data = _audit(lp, env, capsys)
    assert _of(data, "beta") == []
    assert [f["name"] for f in _of(data)] == ["alpha"]


def test_unsynced_and_empty_coverage_are_named(lp, env, capsys):
    commit_claims(env.repo_b)                                   # claims.yaml at HEAD, nothing cached
    reg = read_reg(env)
    reg["packages"]["alpha"]["claims_synced_commit"] = "0" * 40   # an empty list was synced
    write_reg(env, reg)
    data = _audit(lp, env, capsys)
    assert [f["coverage"] for f in _of(data, "beta")] == ["unsynced"]
    assert [f["coverage"] for f in _of(data, "alpha")] == ["empty"]
    assert _of(data, "beta")[0]["detail"].startswith("claims: unsynced (")


def test_archived_and_upstreamed_are_skipped(lp, env, capsys):
    reg = read_reg(env)
    reg["packages"]["alpha"]["status"] = "archived"
    reg["packages"]["beta"]["status"] = "upstreamed"
    write_reg(env, reg)
    assert _of(_audit(lp, env, capsys)) == []


def test_fix_leaves_it_and_writes_nothing(lp, env, capsys):
    before = env.registry.read_text()
    found = _of(_audit(lp, env, capsys, "--fix"))
    assert {f["name"] for f in found} == {"alpha", "beta"}
    assert all(f["fixed"] is False for f in found)
    assert env.registry.read_text() == before and read_events(env) == []


def test_promotion_keeps_it_info(lp, env, capsys):
    found = _of(_audit(lp, env, capsys, "--promotion"))
    assert found and all(f["level"] == "info" for f in found)


def test_counts_and_human_line(lp, env, capsys):
    data = _audit(lp, env, capsys)
    levels = [f["level"] for f in data["findings"]]
    assert data["counts"] == {"warn": levels.count("warn"), "info": levels.count("info")}
    assert lp.main(["--registry", str(env.registry), "audit", "--no-gh"]) == 0
    lines = capsys.readouterr().out.splitlines()
    line = next(ln for ln in lines if ln.startswith(f"info  {CHECK} ") and " alpha: claims: none (" in ln)
    assert line.endswith("run `lean-pkg claims sync alpha`)")
    assert lines[0] == lines[-1] == (f"{len(levels)} finding(s): {levels.count('warn')} warn, "
                                     f"{levels.count('info')} info")
