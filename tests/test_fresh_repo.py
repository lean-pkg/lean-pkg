#!/usr/bin/env python3
"""release NAME --fresh-repo DIR: single-commit public export + relocation (#35)."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import (_git, add_comparator_config, base_pkg, make_comparator_receipt, make_receipt,
                       make_repo, read_events, repo_head)

SECRET_PATH = "/home/jdoe/private-paper/draft.tex"
AXIOMS = ["propext", "Quot.sound", "Classical.choice"]


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "Exporter"), ("GIT_AUTHOR_EMAIL", "exporter@example.com"),
                 ("GIT_COMMITTER_NAME", "Exporter"), ("GIT_COMMITTER_EMAIL", "exporter@example.com")):
        monkeypatch.setenv(k, v)


NOREPLY = "12345+exporter@users.noreply.github.com"


def set_local_identity(repo, name="Exporter", email=NOREPLY):
    """#339: the export commit's identity comes from the source repository's local git config."""
    _git(repo, "config", "user.name", name)
    _git(repo, "config", "user.email", email)


class Pkg:
    pass


@pytest.fixture()
def gated(tmp_path):
    """A verified-private package whose history once held a private path; its gated tree has a
    tracked notes/ file and a tracked patterns file (both dropped by the export); receipts are
    committed in an evidence-only descendant of the gate-bound commit."""
    repo = make_repo(tmp_path / "src-pkg", extra_files={
        "Main.lean": "theorem t : True := trivial\n", "LICENSE": "MIT\n", "lake-manifest.json": "{}\n",
        "notes/plan.md": "ask the referee\n", ".lean-pkg-private-patterns": "Nightjar\n"})
    set_local_identity(repo)
    (repo / "README.md").write_text(f"built from {SECRET_PATH}\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "readme")
    (repo / "README.md").write_text("A Lean package.\n")
    _git(repo, "commit", "-q", "-am", "scrub readme")
    cfg = add_comparator_config(repo)
    bound = repo_head(repo)
    (repo / ".lean-receipts").mkdir()
    (repo / ".lean-receipts" / "seed.json").write_text(json.dumps(make_receipt(repo), indent=2))
    comp_rel = f".lean-receipts/{bound[:12]}-comparator.json"
    (repo / comp_rel).write_text(json.dumps(make_comparator_receipt(repo, cfg), indent=2))
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence")
    reg_path = tmp_path / "lean-packages.yaml"
    pkg = base_pkg("pkg", repo, "verified-private", notes="incubated beside a paper",
                   gate={"receipt": ".lean-receipts/seed.json", "comparator": comp_rel,
                         "axioms_allowed": AXIOMS})
    reg_path.write_text(yaml.safe_dump({"version": 2, "packages": {"pkg": pkg}}, sort_keys=False))
    g = Pkg()
    g.repo, g.bound, g.comp_rel, g.registry = repo, bound, comp_rel, reg_path
    g.events = tmp_path / "lean-package-events.jsonl"
    g.target = tmp_path / "public" / "pkg"
    return g


def run(lp, g, *argv):
    return lp.main(["--registry", str(g.registry), *argv])


def test_fresh_repo_exports_one_clean_commit_and_relocates(lp, gated, capsys):
    g = gated
    assert run(lp, g, "public-safety", "pkg") == 1                # the history leaks
    capsys.readouterr()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--agent", "vera") == 0
    out = capsys.readouterr().out
    assert f"fresh-history export of {g.bound[:12]} (gate receipt .lean-receipts/seed.json)" in out
    assert "dropped: notes/plan.md  (internal-dir notes/)" in out
    assert "dropped: .lean-pkg-private-patterns  (patterns file)" in out
    assert "jdoe" not in out
    # one commit, recording the source
    assert _git(g.target, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    msg = _git(g.target, "log", "-1", "--format=%B").stdout
    assert msg.startswith(f"fresh-history export of {g.bound[:12]}; receipts and gate must be re-run on this commit")
    assert f"source commit: {g.bound}" in msg and "export date: " in msg
    files = set(_git(g.target, "ls-files").stdout.split())
    assert files == {"Main.lean", "LICENSE", "lake-manifest.json", "README.md", "lean-toolchain", "comparator.json"}
    assert _git(g.target, "status", "--porcelain").stdout == ""
    # registry: relocated, evidence cleared, provenance note
    pkg = yaml.safe_load(g.registry.read_text())["packages"]["pkg"]
    assert pkg["local_path"] == str(g.target) and pkg["status"] == "gate-pending"
    assert pkg["gate"]["receipt"] is None and pkg["gate"]["comparator"] is None
    assert pkg["gate"]["axioms_allowed"] == AXIOMS
    new = repo_head(g.target)
    last = pkg["notes"].splitlines()[-1]
    assert pkg["notes"].startswith("incubated beside a paper\n")
    assert "(vera): fresh-history release: exported " + g.bound[:12] in last
    assert f"as {new[:12]}" in last and "gate.receipt=.lean-receipts/seed.json" in last and g.comp_rel in last
    events = [e["event"] for e in read_events(g)]
    assert events == ["relocated", "status-changed", "updated"]
    assert read_events(g)[0]["reason"] == "fresh-history release"
    # the new location passes public-safety, tree and history
    assert run(lp, g, "public-safety", "pkg") == 0
    assert lp.main(["--registry", str(g.registry), "validate"]) == 0
    assert "next:" in out and f"lean-pkg gate pkg --receipt R   # R: a lower-tier receipt bound to {new[:12]}" in out


def test_fresh_repo_dry_run_writes_nothing(lp, gated, capsys):
    g = gated
    before = g.registry.read_text()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--dry-run",
               "--remote", "git@github.com:example/pkg.git") == 0
    out = capsys.readouterr().out
    assert not g.target.exists() and not g.target.parent.exists()
    assert g.registry.read_text() == before and read_events(g) == []
    assert "DRY RUN — nothing written" in out and "dropped: notes/plan.md" in out
    assert f"fresh-history export of {g.bound[:12]}; receipts and gate must be re-run" in out
    assert f"comparator-receipt --repo {g.target} --prepare" in out
    assert "lean-pkg publish pkg --dry-run --public-safety" in out
    assert f"git -C {g.target} push -u origin main" in out
    assert "status verified-private -> gate-pending" in out
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--dry-run", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True and payload["new_commit"] is None
    assert payload["source_commit"] == g.bound and payload["public_safety"]["hits"] == 0
    assert {"path": "notes/plan.md", "reason": "internal-dir notes/"} in payload["excluded"]
    assert any("comparator-receipt" in c and "--prepare" in c for c in payload["next_commands"])
    assert not g.target.exists() and g.registry.read_text() == before


def test_fresh_repo_refuses_an_export_with_hits(lp, gated, capsys):
    g = gated
    (g.repo / "Main.lean").write_text(f"-- from {SECRET_PATH}\n")
    _git(g.repo, "commit", "-q", "-am", "leak again")
    before = g.registry.read_text()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target),
               "--source-commit", repo_head(g.repo)) == 1
    err = capsys.readouterr().err
    assert "public-safety hit(s); nothing written" in err and "[home-path]" in err
    assert "jdoe" not in err
    assert not g.target.exists() and g.registry.read_text() == before
    assert list(g.target.parent.glob(".lean-pkg-export-*")) == []   # the staging directory is removed


def test_fresh_repo_reports_warn_only_hits_without_refusing(lp, gated, capsys):
    """#36: a latex-label hit in the exported tree is a warning, not a refusal."""
    g = gated
    (g.repo / "Main.lean").write_text("/-- See lem:main_bound. -/\ntheorem t : True := trivial\n")
    _git(g.repo, "commit", "-q", "-am", "doc comment")
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--dry-run", "--json",
               "--source-commit", repo_head(g.repo)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["public_safety"] == {"hits": 1, "tree_hits": 1, "block_hits": 0, "warn_hits": 1}
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--dry-run",
               "--source-commit", repo_head(g.repo)) == 0
    out = capsys.readouterr().out
    assert ("public-safety on the exported tree: 0 blocking hit(s), 1 warning(s) (latex-label); "
            "review with `lean-pkg public-safety pkg --strict` before publishing") in out
    assert "main_bound" not in out


def test_fresh_repo_keep_receipts_and_remote(lp, gated, capsys):
    g = gated
    url = "git@github.com:example/pkg-public.git"
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--keep-receipts",
               "--remote", url, "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["kept_receipts"] is True and payload["new_commit"] == repo_head(g.target)
    tracked = set(_git(g.target, "ls-files").stdout.split())
    assert ".lean-receipts/seed.json" in tracked and g.comp_rel in tracked
    assert _git(g.target, "remote", "get-url", "origin").stdout.strip() == url
    pkg = yaml.safe_load(g.registry.read_text())["packages"]["pkg"]
    assert pkg["repo"] == url
    assert pkg["gate"]["receipt"] is None and pkg["gate"]["comparator"] is None   # they do not bind


def test_fresh_repo_refusals(lp, gated, capsys):
    g = gated
    g.target.mkdir(parents=True)
    (g.target / "x").write_text("x")
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target)) == 1
    assert "not an empty directory" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.repo / "sub")) == 1
    assert "inside the source repository" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--remote", "u") == 2
    assert "go(es) with `repo export NAME DIR`" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--force") == 2
    capsys.readouterr()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--source-commit", "nope") == 1
    assert "is not a commit" in capsys.readouterr().err


def test_fresh_repo_refuses_public_and_needs_a_bound_commit(lp, gated, capsys):
    g = gated
    reg = yaml.safe_load(g.registry.read_text())
    reg["packages"]["pkg"]["status"] = "public"
    g.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target)) == 1
    assert "has not been promoted" in capsys.readouterr().err
    reg["packages"]["pkg"]["status"] = "active"
    reg["packages"]["pkg"]["gate"] = {"receipt": None, "comparator": None, "axioms_allowed": AXIOMS}
    g.registry.write_text(yaml.safe_dump(reg, sort_keys=False))
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target)) == 1
    assert "pass --source-commit SHA" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--source-commit", g.bound) == 0
    out = capsys.readouterr().out
    assert "status: active -> active" in out and "update pkg --status gate-pending" in out


def test_fresh_repo_foreign_lease_refused_before_writing(lp, gated, capsys):
    from .conftest import set_lease
    g = gated
    set_lease(g, "pkg", agent="max")
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--agent", "vera") == 1
    assert "lease held by max" in capsys.readouterr().err
    assert not g.target.exists()


def test_plain_release_unchanged(lp, gated, capsys):
    assert run(lp, gated, "release", "pkg", "--agent", "vera") == 1
    assert "no lease held" in capsys.readouterr().err


# #37: --fresh-repo DIR --from-tree SRCDIR (a hand-cleaned working tree) and --adopt-repo DIR

def test_from_tree_exports_a_dirty_working_tree(lp, gated, capsys):
    """The working tree at SRCDIR, not a commit: uncommitted edits and untracked files are exported,
    ignored files and internal paths are not, and the source history is untouched."""
    g = gated
    clean = g.repo.parent / "cleaned"
    _git(g.repo, "clone", "-q", str(g.repo), str(clean))
    (clean / "Main.lean").write_text("theorem t' : True := trivial\n")            # modified, uncommitted
    (clean / "Extra.lean").write_text("theorem e : True := trivial\n")            # untracked
    (clean / ".gitignore").write_text("build/\n")
    (clean / "build").mkdir()
    (clean / "build" / "out.log").write_text(f"{SECRET_PATH}\n")                 # ignored: never copied
    (clean / ".lake").mkdir()
    (clean / ".lake" / "cache").write_text("x\n")
    _git(clean, "rm", "-q", "README.md")                                        # removed by the cleaning
    src_log = _git(g.repo, "log", "--format=%H").stdout
    clean_head = repo_head(clean)
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--from-tree", str(clean),
               "--agent", "vera") == 0
    out = capsys.readouterr().out
    assert f"fresh-history export of working tree {clean} (not a commit; git ls-files" in out
    assert f"provenance: gate-bound commit {g.bound[:12]}" in out
    assert "dropped: notes/plan.md  (internal-dir notes/)" in out
    files = set(_git(g.target, "ls-files").stdout.split())
    assert files == {"Main.lean", "Extra.lean", ".gitignore", "LICENSE", "lake-manifest.json", "lean-toolchain",
                     "comparator.json"}
    assert (g.target / "Main.lean").read_text() == "theorem t' : True := trivial\n"
    assert _git(g.target, "rev-list", "--count", "HEAD").stdout.strip() == "1"
    msg = _git(g.target, "log", "-1", "--format=%B").stdout
    assert msg.startswith("fresh-history export of working tree cleaned; receipts and gate must be re-run")
    assert "source commit: none (hand-cleaned tree)" in msg
    assert f"source package commit for provenance: {g.bound}" in msg
    # neither the source repository nor the cleaned clone was committed to
    assert _git(g.repo, "log", "--format=%H").stdout == src_log and repo_head(clean) == clean_head
    pkg = yaml.safe_load(g.registry.read_text())["packages"]["pkg"]
    assert pkg["local_path"] == str(g.target) and pkg["status"] == "gate-pending"
    assert pkg["gate"]["receipt"] is None and pkg["gate"]["comparator"] is None
    last = pkg["notes"].splitlines()[-1]
    assert "fresh-history release: exported the working tree" in last and "(hand-cleaned, not a commit)" in last
    assert f"source package commit for provenance: {g.bound[:12]}" in last
    assert run(lp, g, "public-safety", "pkg") == 0


def test_from_tree_plain_directory_dry_run_and_refusals(lp, gated, capsys):
    g = gated
    plain = g.repo.parent / "plain"
    (plain / ".lake").mkdir(parents=True)
    (plain / ".lake" / "junk").write_text(f"{SECRET_PATH}\n")
    (plain / "Main.lean").write_text("theorem t : True := trivial\n")
    (plain / "lean-toolchain").write_text("leanprover/lean4:v4.0.0\n")
    before = g.registry.read_text()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--from-tree", str(plain),
               "--dry-run", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_commit"] is None and payload["provenance_commit"] == g.bound
    assert payload["files"] == 2 and payload["from_tree"] == str(plain)
    assert "source commit: none (hand-cleaned tree)" in payload["commit_message"]
    assert not g.target.exists() and g.registry.read_text() == before
    # a leak in the working tree refuses, nothing written
    (plain / "Notes.lean").write_text(f"-- {SECRET_PATH}\n")
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--from-tree", str(plain)) == 1
    err = capsys.readouterr().err
    assert f"the working tree {plain} has 1 public-safety hit(s); nothing written" in err and "jdoe" not in err
    assert not g.target.exists() and g.registry.read_text() == before
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--from-tree", str(plain),
               "--source-commit", g.bound) == 2
    assert "give one" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(plain / "out"), "--from-tree", str(plain)) == 1
    assert "lies inside --from-tree" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--from-tree", str(plain / "nope")) == 1
    assert "is not a directory" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--from-tree", str(plain)) == 2
    assert "go(es) with `repo export NAME DIR`" in capsys.readouterr().err


def hand_built(g, *, commits=1, files=None):
    """A public repository built by hand: `git init` + one commit of the cleaned tree."""
    files = files or {"Main.lean": "theorem t : True := trivial\n", "LICENSE": "MIT\n"}
    g.target.parent.mkdir(parents=True, exist_ok=True)
    repo = make_repo(g.target, extra_files=files)
    for i in range(commits - 1):
        (repo / f"Step{i}.lean").write_text("theorem s : True := trivial\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"step {i}")
    return repo


def test_adopt_repo_relocates_without_a_new_commit(lp, gated, capsys):
    g = gated
    repo = hand_built(g)
    head = repo_head(repo)
    url = "git@github.com:example/pkg-public.git"
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--dry-run") == 0
    out = capsys.readouterr().out
    assert f"adopt {repo} at {head[:12]} (1 commit(s) on all refs) as the public history" in out
    assert "DRY RUN — nothing written" in out and f"previous gate-bound commit: {g.bound[:12]}" in out
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--remote", url, "--agent", "vera",
               "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adopted_commit"] == head and payload["commits"] == 1 and payload["new_commit"] is None
    assert payload["provenance_commit"] == g.bound and payload["status"] == {"from": "verified-private",
                                                                             "to": "gate-pending"}
    assert repo_head(repo) == head and _git(repo, "rev-list", "--all", "--count").stdout.strip() == "1"
    assert _git(repo, "remote", "get-url", "origin").stdout.strip() == url
    pkg = yaml.safe_load(g.registry.read_text())["packages"]["pkg"]
    assert pkg["local_path"] == str(repo) and pkg["repo"] == url and pkg["status"] == "gate-pending"
    assert pkg["gate"]["receipt"] is None and pkg["gate"]["comparator"] is None
    last = pkg["notes"].splitlines()[-1]
    assert f"(vera): adopted-repository release: adopted {repo} at {head[:12]} (1 commit(s))" in last
    assert f"previous gate-bound commit: {g.bound[:12]}" in last and "gate.receipt=.lean-receipts/seed.json" in last
    assert [e["event"] for e in read_events(g)] == ["relocated", "status-changed", "updated"]
    assert read_events(g)[0]["reason"] == "adopted-repository release"


def test_adopt_repo_refuses_history_unless_allowed(lp, gated, capsys):
    g = gated
    repo = hand_built(g, commits=2)
    before = g.registry.read_text()
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo)) == 1
    err = capsys.readouterr().err
    assert "has 2 commits on its refs; an adopted public history must be exactly one commit" in err
    assert "--allow-history" in err and g.registry.read_text() == before
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--allow-history") == 0
    assert "(2 commit(s) on all refs; --allow-history)" in capsys.readouterr().out
    assert yaml.safe_load(g.registry.read_text())["packages"]["pkg"]["local_path"] == str(repo)


def test_adopt_repo_refusals(lp, gated, capsys):
    g = gated
    repo = hand_built(g, files={"Main.lean": "theorem t : True := trivial\n"})
    before = g.registry.read_text()
    # a leak in the history (not in the tree) refuses
    (repo / "Main.lean").write_text(f"-- {SECRET_PATH}\n")
    _git(repo, "commit", "-q", "-am", "leak")
    (repo / "Main.lean").write_text("theorem t : True := trivial\n")
    _git(repo, "commit", "-q", "-am", "unleak")
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--allow-history") == 1
    err = capsys.readouterr().err
    assert "blocking public-safety hit(s) in its tree or history; nothing written" in err and "jdoe" not in err
    assert g.registry.read_text() == before
    # the package's own source repository, a subdirectory, modified tracked files, a different origin
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(g.repo), "--allow-history") == 1
    assert "is the package's own source repository" in capsys.readouterr().err
    (repo / "sub").mkdir()
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo / "sub")) == 1
    assert "not its top directory" in capsys.readouterr().err
    (repo / "Main.lean").write_text("theorem u : True := trivial\n")
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--allow-history") == 1
    assert "uncommitted changes to tracked files" in capsys.readouterr().err
    _git(repo, "checkout", "-q", "--", "Main.lean")
    _git(repo, "remote", "add", "origin", "git@github.com:example/a.git")
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--allow-history",
               "--remote", "git@github.com:example/b.git") == 1
    assert "already has origin git@github.com:example/a.git" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(repo), "--keep-receipts") == 2
    assert "do(es) not go with `repo adopt NAME DIR`" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--allow-history") == 2
    assert "--allow-history goes with `repo adopt NAME DIR`" in capsys.readouterr().err
    assert run(lp, g, "release", "pkg", "--adopt-repo", str(g.repo.parent / "missing")) == 1
    assert "is not a git repository with a commit" in capsys.readouterr().err
    assert g.registry.read_text() == before


# #339: the export commit's identity is explicit (--author, else the source repository's local git config),
# never git's ambient (global) one, and a non-noreply address needs --allow-author-email

PERSONAL = "jane.doe" + "@university.edu"       # an ordinary address: declared by use, so public-safety is silent


def exported_identity(g) -> str:
    return _git(g.target, "log", "-1", "--format=%an <%ae>|%cn <%ce>").stdout.strip()


@pytest.fixture()
def global_identity(tmp_path, monkeypatch):
    """A global git config holding a personal address, and no identity variables in the environment."""
    cfg = tmp_path / "global.gitconfig"
    cfg.write_text(f"[user]\n    name = Jane Doe\n    email = {PERSONAL}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    return cfg


def test_no_author_and_no_source_local_identity_refuses(lp, gated, global_identity, capsys):
    g = gated
    _git(g.repo, "config", "--unset", "user.name")
    _git(g.repo, "config", "--unset", "user.email")
    before = g.registry.read_text()
    for extra in ((), ("--dry-run",)):
        assert run(lp, g, "repo", "export", "pkg", str(g.target), *extra) == 1
        err = capsys.readouterr().err
        assert "repo export: no identity for the export commit: pass --author 'NAME <ID+LOGIN@users.noreply." \
               "github.com>'" in err and "the global git identity is never used" in err
        assert PERSONAL not in err
    _git(g.repo, "config", "user.email", NOREPLY)        # half an identity (no user.name) is none
    assert run(lp, g, "repo", "export", "pkg", str(g.target)) == 1
    assert "no identity for the export commit" in capsys.readouterr().err
    assert not g.target.exists() and not list(g.target.parent.glob(".lean-pkg-export-*"))
    assert g.registry.read_text() == before and read_events(g) == []


def test_source_local_noreply_identity_is_used_never_the_global_one(lp, gated, global_identity, capsys):
    g = gated
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--dry-run") == 0
    out = capsys.readouterr().out
    assert (f"commit identity: Exporter <{NOREPLY}> (author and committer; from the source repository's local "
            f"git config (user.name, user.email))") in out
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["identity"] == {"author": f"Exporter <{NOREPLY}>", "committer": f"Exporter <{NOREPLY}>",
                                "source": "the source repository's local git config (user.name, user.email)",
                                "noreply": True}
    assert exported_identity(g) == f"Exporter <{NOREPLY}>|Exporter <{NOREPLY}>"
    assert PERSONAL not in _git(g.target, "log", "--format=%an %ae %cn %ce %B").stdout


def test_author_flag_is_used_and_overrides_the_environment(lp, gated, capsys):
    g = gated                                              # GIT_AUTHOR_*/GIT_COMMITTER_* set by the fixture
    who = "Le Bot <41898282+le-bot[bot]@users.noreply.github.com>"
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--author", who) == 0
    assert f"commit identity: {who} (author and committer; from --author)" in capsys.readouterr().out
    assert exported_identity(g) == f"{who}|{who}"


def test_a_non_noreply_email_is_refused_then_accepted_with_the_flag(lp, gated, global_identity, capsys):
    g = gated
    before = g.registry.read_text()
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--author", f"Jane Doe <{PERSONAL}>") == 1
    err = capsys.readouterr().err
    assert (f"the export commit's author and committer email {PERSONAL} (from --author) is not a GitHub noreply "
            f"address, and the commit is public history: pass --author 'NAME <ID+LOGIN@users.noreply.github.com>', "
            f"or --allow-author-email to publish this address; nothing written") in err
    _git(g.repo, "config", "user.email", PERSONAL)         # the same from the source's local config
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--dry-run") == 1
    assert "(from the source repository's local git config" in capsys.readouterr().err
    assert not g.target.exists() and g.registry.read_text() == before and read_events(g) == []
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--allow-author-email") == 0
    out = capsys.readouterr().out
    assert "not a GitHub noreply address: published by --allow-author-email" in out
    assert exported_identity(g) == f"Exporter <{PERSONAL}>|Exporter <{PERSONAL}>"


def test_the_export_scan_checks_the_commit_identity(lp, gated, capsys):
    g = gated
    before = g.registry.read_text()
    for who in ("Nightjar Lab <1+lab@users.noreply.github.com>", "Lab <1+Nightjar@users.noreply.github.com>"):
        for extra in ((), ("--allow-author-email",)):         # the flag allows an address, never a hit
            assert run(lp, g, "repo", "export", "pkg", str(g.target), "--author", who, *extra) == 1
            err = capsys.readouterr().err
            assert "public-safety hit(s); nothing written" in err and "export-commit-identity" in err
            assert "Nightjar" not in err
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--author", "Lab <le@desk.lan>",
               "--allow-author-email") == 1                   # a host-like address: identity-email
    assert "identity-email" in capsys.readouterr().err
    assert not g.target.exists() and g.registry.read_text() == before


def test_author_flag_refusals(lp, gated, capsys):
    g = gated
    for bad in ("no address", "<1+x@users.noreply.github.com>", "Name <not-an-address>"):
        assert run(lp, g, "repo", "export", "pkg", str(g.target), "--author", bad) == 2
        assert "give a name and an address" in capsys.readouterr().err
    assert run(lp, g, "repo", "adopt", "pkg", str(g.repo), "--author", "A <1+a@users.noreply.github.com>") == 2
    assert "--author do(es) not go with `repo adopt NAME DIR`" in capsys.readouterr().err
    assert run(lp, g, "lease", "release", "pkg", "--allow-author-email") == 2
    assert "--allow-author-email go(es) with `repo export NAME DIR`" in capsys.readouterr().err
    assert not g.target.exists()
