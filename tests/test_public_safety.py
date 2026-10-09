#!/usr/bin/env python3
"""public-safety NAME: private patterns in the tree and the git history, redacted (#35)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import yaml

from .conftest import _git, base_pkg, make_repo, publish_evidence, repo_head

SECRET_PATH = "/home/jdoe/private-paper/draft.tex"
SECRET_WORD = "ProjectNightjar"


def make_env(tmp_path, repo, name="pkg", **kw):
    reg_path = tmp_path / "lean-packages.yaml"
    reg = {"version": 2, "packages": {name: base_pkg(name, repo, kw.pop("status", "gate-pending"), **kw)}}
    reg_path.write_text(yaml.safe_dump(reg, sort_keys=False))
    return reg_path


def run(lp, reg_path, *argv):
    return lp.main(["--registry", str(reg_path), *argv])


def history_leak_repo(tmp_path):
    """A clean tree whose history once held a private absolute path."""
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "theorem t : True := trivial\n"})
    (repo / "README.md").write_text(f"built from {SECRET_PATH}\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "readme")
    leaked = repo_head(repo)
    (repo / "README.md").write_text("A Lean package.\n")
    _git(repo, "commit", "-q", "-am", "scrub readme")
    return repo, leaked


def test_old_commit_only_hit_is_a_history_hit_redacted(lp, tmp_path, capsys):
    repo, leaked = history_leak_repo(tmp_path)
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 1
    out = capsys.readouterr().out
    assert "jdoe" not in out and SECRET_PATH not in out          # never the raw string
    assert "[home-path]" in out and "[…]" in out
    assert leaked[:12] in out and "README.md:1" in out
    assert "1 hit(s) (0 tree, 1 history)" in out
    assert "release pkg --fresh-repo DIR" in out
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False and payload["tree_hits"] == 0 and payload["history_hits"] == 1
    hit = payload["hits"][0]
    assert hit["where"] == "history" and hit["commit"] == leaked and hit["path"] == "README.md"
    assert hit["pattern"] == "home-path" and "[…]private-paper" in hit["excerpt"]
    assert "jdoe" not in json.dumps(payload)
    assert set(payload) >= {"ok", "hits", "tree_hits", "history_hits", "patterns_source"}
    # the tree alone is clean
    assert run(lp, reg, "public-safety", "pkg", "--no-history") == 0
    assert "ok: no private pattern found" in capsys.readouterr().out


def test_tracked_patterns_file_and_file_pattern_hits(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "Main.lean": f"-- see {SECRET_WORD} section 3\ntheorem t : True := trivial\n",
        ".lean-pkg-private-patterns": f"# private project names\n{SECRET_WORD}\nallow: ^me@lab\\.test$\n",
        "notes/todo.md": "plan\n",
        "AUTHORS": "me@lab.test\nother@lab.test\nremote git@github.com:you/pkg.git, see dev@example.org\n",
    })
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    blob = json.dumps(payload)
    assert SECRET_WORD not in blob and "other@lab" not in blob
    pats = {(h["where"], h["path"], h["pattern"]) for h in payload["hits"]}
    assert ("tree", ".lean-pkg-private-patterns", "patterns-file") in pats
    assert ("tree", "Main.lean", ".lean-pkg-private-patterns:2") in pats      # labelled by line, not text
    assert ("tree", "notes/todo.md", "internal-dir notes/") in pats
    assert ("tree", "AUTHORS", "email") in pats                                # other@ only: me@ is allowed
    assert [h["line"] for h in payload["hits"] if h["pattern"] == "email"] == [2]   # git@ remote, example.org
    assert payload["patterns_source"]["file"] == ".lean-pkg-private-patterns"
    assert payload["patterns_source"]["tracked"] is True
    assert any("is tracked" in w for w in payload["warnings"])
    assert all(h["where"] == "tree" for h in payload["hits"]) and payload["history_hits"] == 0


def test_untracked_patterns_file_is_read_not_reported(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={
        "Main.lean": "theorem t : True := trivial\n", ".gitignore": ".lean-pkg-private-patterns\n"})
    (repo / ".lean-pkg-private-patterns").write_text("trivial\n")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert [h["path"] for h in payload["hits"]] == ["Main.lean"]
    assert payload["patterns_source"]["tracked"] is False and payload["warnings"] == []


def test_clean_package_passes(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "theorem t : True := trivial\n"})
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg") == 0
    out = capsys.readouterr().out
    assert "0 hit(s) (0 tree, 0 history)" in out and "ok: no private pattern found" in out
    assert run(lp, reg, "public-safety", "pkg", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True and payload["hits"] == []


def test_registry_derived_names_are_patterns_and_never_printed(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "-- run on nodeheron by vera-bot\n"})
    reg = make_env(tmp_path, repo, workspace="nodeheron", agent="vera-bot")
    assert run(lp, reg, "public-safety", "pkg", "--json", "--no-history") == 1
    payload = json.loads(capsys.readouterr().out)
    assert {h["pattern"] for h in payload["hits"]} == {"registry workspace", "registry agent"}
    assert "nodeheron" not in json.dumps(payload["hits"]) and "vera-bot" not in json.dumps(payload["hits"])


def test_derived_user_from_home_local_path(lp):
    from lean_pkg import public_safety as ps
    pats = ps.derived_patterns({"local_path": "/home/jdoe/src/pkg", "agent": "w3", "workspace": None})
    assert [p.label for p in pats] == ["registry local_path (user)"]     # short agent token ignored
    assert pats[0].rx.search("cd /home/JDOE") and not pats[0].rx.search("jdoes")


def test_commit_message_hits(lp, tmp_path, capsys):
    repo = make_repo(tmp_path / "pkg-repo", extra_files={"Main.lean": "x\n"})
    (repo / "Main.lean").write_text("y\n")
    _git(repo, "commit", "-q", "-am", "copied from ~/Dropbox/drafts")
    reg = make_env(tmp_path, repo)
    assert run(lp, reg, "public-safety", "pkg", "--json") == 1
    hits = json.loads(capsys.readouterr().out)["hits"]
    assert [(h["path"], h["pattern"]) for h in hits] == [("commit-message", "cloud-sync-path")]
    assert hits[0]["excerpt"] == "copied from […]drafts"


def test_promote_refuses_while_hits_remain(lp, env, fake_gh, capsys):
    (env.repo_b / "NOTES.md").write_text("TODO-private: ask the referee\n")
    _git(env.repo_b, "add", "NOTES.md")
    assert run(lp, env.registry, "promote", "beta") == 1
    out = capsys.readouterr().out
    assert ("public-safety: 1 hit(s) — fix the tree, or publish a fresh-history tree with "
            "`release --fresh-repo`; see `lean-pkg public-safety beta`") in out
    assert "refusing: checklist has failures" in out
    assert run(lp, env.registry, "promote", "beta", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"] == {"skipped": False, "hits": 1, "tree_hits": 1, "history_hits": 0}
    assert run(lp, env.registry, "promote", "beta", "--skip-public-safety", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"] == {"skipped": True}
    assert any(c["status"] == "warn" and "public-safety skipped" in c["label"] for c in payload["checklist"])
    assert not any("public-safety:" in c["label"] for c in payload["checklist"])


def test_promote_clean_package_passes_public_safety(lp, env, fake_gh, capsys):
    publish_evidence(env.repo_b)
    assert run(lp, env.registry, "promote", "beta") == 0
    out = capsys.readouterr().out
    assert "✓  public-safety: no private pattern in the tree or the history" in out


def test_publish_public_safety_flag(lp, env, fake_gh, capsys):
    (env.repo_b / "x.private").write_text("a\n")
    assert run(lp, env.registry, "publish", "beta", "--public-safety", "--json") == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"]["hits"] == 1
    assert any(c["status"] == "fail" and c["label"].startswith("public-safety: 1 hit(s)")
               for c in payload["checklist"])
    assert run(lp, env.registry, "publish", "beta", "--dry-run", "--json") in (0, 1)
    assert "public_safety" not in json.loads(capsys.readouterr().out)     # only with the flag


def test_public_safety_is_read_only(lp, tmp_path, capsys):
    repo, _ = history_leak_repo(tmp_path)
    reg = make_env(tmp_path, repo)
    before = reg.read_text()
    run(lp, reg, "public-safety", "pkg")
    assert reg.read_text() == before and not (tmp_path / "lean-package-events.jsonl").exists()
    assert lp.is_write_invocation(SimpleNamespace(cmd="public-safety", name="pkg")) is False


def test_unknown_package_and_missing_path(lp, tmp_path, capsys):
    reg = make_env(tmp_path, tmp_path / "gone")
    assert run(lp, reg, "public-safety", "pkg") == 1
    assert "local_path does not exist" in capsys.readouterr().err
    assert run(lp, reg, "public-safety", "nope") == 1


def test_package_subdirectory_scans_only_its_own_history(lp, tmp_path, capsys):
    root = make_repo(tmp_path / "mono", extra_files={"paper/draft.tex": f"{SECRET_PATH}\n",
                                                      "lean/Main.lean": "x\n"})
    reg = make_env(tmp_path, root / "lean")
    assert run(lp, reg, "public-safety", "pkg", "--json") == 0
    assert json.loads(capsys.readouterr().out)["hits"] == []
