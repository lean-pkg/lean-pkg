#!/usr/bin/env python3
"""#385: a lease reader is told which registry commit its lease state comes from, and warned when its clone
is behind the origin/<branch> it last fetched. Field report: on a second machine `gate NAME --check` warned
the lease was EXPIRED and held by another agent; the live lease, pushed from the first machine, was the
reporter's own, and the second clone was simply behind origin. Read-only commands still never fetch."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import yaml

from .conftest import _git, patch_core, set_lease


def _lease(agent: str, hours_ago: float, ttl: int = 24) -> dict:
    claimed = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    return {"workspace": "w", "agent": agent, "claimed_at": claimed, "ttl_hours": ttl, "reason": "test"}


def _commit_lease(clone, name: str, lease: dict, msg: str) -> None:
    reg_p = clone / "lean-packages.yaml"
    reg = yaml.safe_load(reg_p.read_text())
    reg["packages"][name]["lease"] = lease
    reg_p.write_text(yaml.safe_dump(reg, sort_keys=False))
    _git(clone, "commit", "-q", "-am", msg)


def _clones(tmp_path, env):
    """A registry on a bare origin, cloned twice: `reader` (the second machine) and `writer` (the first).
    alpha's lease, as both clones first see it, lapsed long ago (held by old-agent)."""
    seed = tmp_path / "registry-seed"
    seed.mkdir()
    _git(seed, "init", "-q")
    _git(seed, "branch", "-M", "main")
    reg = yaml.safe_load(env.registry.read_text())
    reg["packages"]["alpha"]["lease"] = _lease("old-agent", hours_ago=30)
    (seed / "lean-packages.yaml").write_text(yaml.safe_dump(reg, sort_keys=False))
    (seed / "lean-package-events.jsonl").write_text("")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "registry")
    origin = tmp_path / "registry-origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True, capture_output=True)
    clones = []
    for name in ("reader", "writer"):
        subprocess.run(["git", "clone", "-q", str(origin), str(tmp_path / name)], check=True, capture_output=True)
        clones.append(tmp_path / name)
    return clones


def run(lp, reg, capsys, *argv):
    rc = lp.main(["--registry", str(reg), *argv])
    return rc, capsys.readouterr()


def _short(clone) -> str:
    return _git(clone, "rev-parse", "--short=7", "HEAD").stdout.strip()


def test_fetched_but_not_pulled_clone_warns_behind_and_stamps_the_local_commit(lp, env, tmp_path, capsys):
    reader, writer = _clones(tmp_path, env)
    _commit_lease(writer, "alpha", _lease("me", hours_ago=1), "lease: alpha claimed by me")
    _git(writer, "push", "-q", "origin", "main")
    _git(reader, "fetch", "-q", "origin")           # fetched, never pulled: local refs know it is behind
    local = _short(reader)
    reg = reader / "lean-packages.yaml"

    rc, cap = run(lp, reg, capsys, "status", "alpha")
    assert rc == 0
    last = cap.out.splitlines()[-1]
    assert last.startswith("lease: EXPIRED ") and f"(as of registry {local}, committed " in last
    assert last.endswith(" ago)")
    warn = [ln for ln in cap.err.splitlines() if "behind" in ln]
    assert len(warn) == 1
    assert warn[0].startswith("lean-pkg: WARN: registry clone is 1 commit(s) behind origin/main as last fetched")
    assert "lease state may be stale" in warn[0] and "pull --ff-only" in warn[0] and "doctor --fetch" in warn[0]

    rc, cap = run(lp, reg, capsys, "--json", "status", "alpha")
    st = json.loads(cap.out)["registry_state"]
    assert st["commit"].startswith(local) and len(st["commit"]) == 40
    assert st["branch"] == "main" and st["upstream"] == "origin/main"
    assert (st["behind"], st["ahead"], st["dirty"]) == (1, 0, False)
    assert isinstance(st["age_seconds"], int) and st["age_seconds"] >= 0
    assert datetime.fromisoformat(st["committed_at"])
    assert "registry clone is 1 commit(s) behind" in cap.err

    # the field case: gate --check on the stale clone; the lease WARN names the commit, behind is said once
    receipt = env.repo_b / ".lean-receipts" / "seed.json"
    rc, cap = run(lp, reg, capsys, "gate", "alpha", "--check", "--receipt", str(receipt))
    assert "lean-pkg: WARN: alpha lease: EXPIRED " in cap.err and f"(as of registry {local}, committed " in cap.err
    assert cap.err.count("registry clone is 1 commit(s) behind origin/main") == 1
    rc, cap = run(lp, reg, capsys, "--json", "gate", "beta", "--check", "--receipt", str(receipt))
    data = json.loads(cap.out)
    assert rc == 0 and data["registry_state"]["behind"] == 1
    assert cap.err.count("registry clone is 1 commit(s) behind") == 1

    # the lease list says it too
    rc, cap = run(lp, reg, capsys, "lease", "list")
    assert rc == 0 and cap.out.splitlines()[-1].startswith(f"(as of registry {local}, committed ")
    assert "registry clone is 1 commit(s) behind" in cap.err
    rc, cap = run(lp, reg, capsys, "--json", "lease", "list")
    assert json.loads(cap.out)["registry_state"]["behind"] == 1

    # another command naming the package: the lease WARN is stamped and followed by the behind warning
    rc, cap = run(lp, reg, capsys, "update", "alpha", "--notes", "x", "--dry-run")
    assert f"(as of registry {local}, committed " in cap.err and "registry clone is 1 commit(s) behind" in cap.err

    # after a pull: the live lease, no behind warning
    _git(reader, "pull", "-q", "--ff-only")
    rc, cap = run(lp, reg, capsys, "status", "alpha")
    assert cap.out.splitlines()[-1].startswith("lease: live, expires in ")
    assert f"(me) (as of registry {_short(reader)}, committed " in cap.out.splitlines()[-1]
    assert "behind" not in cap.err


def test_unfetched_clone_has_no_behind_warning_but_still_names_its_commit(lp, env, tmp_path, capsys, monkeypatch):
    reader, writer = _clones(tmp_path, env)
    _commit_lease(writer, "alpha", _lease("me", hours_ago=1), "lease: alpha claimed by me")
    _git(writer, "push", "-q", "origin", "main")
    calls = []
    real_git = lp.registry_git
    patch_core(monkeypatch, "registry_git", lambda r, *a: (calls.append(a), real_git(r, *a))[1])
    rc, cap = run(lp, reader / "lean-packages.yaml", capsys, "status", "alpha")
    rc2, cap2 = run(lp, reader / "lean-packages.yaml", capsys, "lease", "list")
    assert rc == 0 and rc2 == 0
    assert not any(a and a[0] in ("fetch", "pull") for a in calls)     # read-only: never fetches
    assert f"(as of registry {_short(reader)}, committed " in cap.out.splitlines()[-1]
    assert "behind" not in cap.err and "behind" not in cap2.err


def test_registry_outside_git_has_no_stamp_and_no_error(lp, env, capsys):
    set_lease(env, "alpha", agent="vera", hours_ago=30, ttl=24)
    rc, cap = run(lp, env.registry, capsys, "status", "alpha")
    assert rc == 0 and "as of registry" not in cap.out and cap.out.splitlines()[-1].startswith("lease: EXPIRED")
    rc, cap = run(lp, env.registry, capsys, "--json", "status", "alpha")
    assert rc == 0 and json.loads(cap.out)["registry_state"] is None
    rc, cap = run(lp, env.registry, capsys, "--json", "lease", "list")
    assert rc == 0 and json.loads(cap.out)["registry_state"] is None
    set_lease(env, "alpha", agent="vera", hours_ago=1, ttl=24)
    rc, cap = run(lp, env.registry, capsys, "status", "alpha")
    assert rc == 0 and "\nlease: " not in cap.out      # unchanged: a live lease prints no line without a stamp


def test_no_origin_and_detached_head_stamp_without_counts(lp, env, tmp_path, capsys):
    repo = tmp_path / "solo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "branch", "-M", "main")
    (repo / "lean-packages.yaml").write_text(env.registry.read_text())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "registry")
    reg = repo / "lean-packages.yaml"
    rc, cap = run(lp, reg, capsys, "--json", "status", "beta")
    st = json.loads(cap.out)["registry_state"]
    assert rc == 0 and st["branch"] == "main"
    assert (st["upstream"], st["behind"], st["ahead"]) == (None, None, None)
    rc, cap = run(lp, reg, capsys, "status", "beta")
    assert cap.out.splitlines()[-1].startswith(f"lease: none (as of registry {_short(repo)}, committed ")
    _git(repo, "checkout", "-q", "--detach")
    reg.write_text(reg.read_text() + "\n")             # uncommitted change to the registry file
    rc, cap = run(lp, reg, capsys, "--json", "status", "beta")
    st = json.loads(cap.out)["registry_state"]
    assert rc == 0 and st["branch"] is None and st["behind"] is None and st["dirty"] is True
    rc, cap = run(lp, reg, capsys, "status", "beta")
    assert cap.out.splitlines()[-1].endswith(" ago, registry file modified)")

