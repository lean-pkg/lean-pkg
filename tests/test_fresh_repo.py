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
    assert f"comparator-receipt --project {g.target} --prepare" in out
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
    assert "go(es) with --fresh-repo DIR" in capsys.readouterr().err
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
