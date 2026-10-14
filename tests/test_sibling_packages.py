#!/usr/bin/env python3
"""Sibling packages in one repository (lean/, lean-r2/): `gate` refuses a lake path dependency outside
the package directory, and `register --like OTHER` takes a sibling's fields and warns about nesting."""

from __future__ import annotations

import json

from .conftest import (MATHLIB_MANIFEST, _git, make_receipt, make_repo, read_events, read_reg, repo_head,
                       write_receipt)


def manifest(*paths: tuple[str, str]) -> str:
    """lake-manifest.json with Mathlib from git plus one `path` package per (name, dir)."""
    data = json.loads(MATHLIB_MANIFEST)
    data["packages"] += [{"type": "path", "scope": "", "name": n, "manifestFile": "lake-manifest.json",
                          "inherited": False, "dir": d, "configFile": "lakefile.lean"} for n, d in paths]
    return json.dumps(data, indent=2) + "\n"


def commit_manifest(repo, text):
    (repo / "lake-manifest.json").write_text(text)
    _git(repo, "add", "lake-manifest.json")
    _git(repo, "commit", "-q", "-m", "manifest")


def gate(lp, env, tmp_path, *extra):
    p = write_receipt(tmp_path, make_receipt(env.repo_a))
    return lp.main(["--registry", str(env.registry), "gate", "alpha", "--receipt", str(p), *extra])


# gate: path dependencies ------------------------------------------------------------------------

def test_gate_refuses_a_path_dependency_outside_the_package_dir(lp, env, tmp_path, capsys):
    commit_manifest(env.repo_a, manifest(("R1", "../lean")))
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert "path dependencies outside the package directory: R1 (../lean)" in err
    assert "pinned to a rev" in err
    assert read_reg(env)["packages"]["alpha"]["status"] == "gate-pending"
    assert read_events(env) == []


def test_gate_refuses_an_absolute_or_escaping_path_dependency(lp, env, tmp_path, capsys):
    commit_manifest(env.repo_a, manifest(("Abs", "/srv/lean/R1"), ("Esc", "vendor/../../R1")))
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert "Abs (/srv/lean/R1)" in err and "Esc (vendor/../../R1)" in err


def commit_files(repo, files: dict[str, str], links: dict[str, str] | None = None):
    """Commit files and symlinks (path -> target) in one commit."""
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    for rel, target in (links or {}).items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.symlink_to(target)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "files")


def test_gate_accepts_a_tracked_in_package_path_dependency(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {"vendor/Helper/lakefile.lean": "-- helper\n",
                              "lake-manifest.json": manifest(("Helper", "./vendor/Helper"))})
    assert gate(lp, env, tmp_path) == 0
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


def test_gate_refuses_an_untracked_path_dependency(lp, env, tmp_path, capsys):
    """.lake/ is not committed: what the build reads there is not in the bound tree."""
    commit_manifest(env.repo_a, manifest(("Lake", ".lake/packages/x/sub")))
    assert gate(lp, env, tmp_path) == 1
    assert "Lake (.lake/packages/x/sub): .lake is not tracked at the bound commit" in capsys.readouterr().err


def test_gate_refuses_a_path_dependency_that_is_a_tracked_symlink(lp, env, tmp_path, capsys):
    """#4's review: dir vendor/dep looks in-package, but the committed vendor/dep is a symlink out."""
    commit_files(env.repo_a, {"lake-manifest.json": manifest(("Dep", "vendor/dep"))},
                 links={"vendor/dep": "../../external"})
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert "symlinks at" in err and "vendor/dep -> ../../external" in err
    assert read_events(env) == []


def test_gate_refuses_a_path_dependency_under_a_descending_symlink(lp, env, tmp_path, capsys):
    """Even a symlink that stays inside may not stand on a path dependency's way: the dir must be real."""
    commit_files(env.repo_a, {"real/dep/lakefile.lean": "-- dep\n",
                              "lake-manifest.json": manifest(("Dep", "vendor/dep"))},
                 links={"vendor": "real"})
    assert gate(lp, env, tmp_path) == 1
    assert "Dep (vendor/dep): vendor is a symlink at the bound commit" in capsys.readouterr().err


def test_gate_refuses_any_committed_symlink_that_climbs(lp, env, tmp_path, capsys):
    """No path dependency at all: a source file symlinked to ../paper is read by the build all the same."""
    commit_files(env.repo_a, {}, links={"Demo/Foo.lean": "../../paper/foo.lean", "Ok.lean": "Demo/Bar.lean"})
    assert gate(lp, env, tmp_path) == 1
    err = capsys.readouterr().err
    assert "Demo/Foo.lean -> ../../paper/foo.lean" in err and "Ok.lean" not in err


def test_gate_accepts_a_descending_symlink(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {"Demo/Bar.lean": "-- bar\n"}, links={"Ok.lean": "Demo/Bar.lean"})
    assert gate(lp, env, tmp_path, "--check") == 0
    assert "no symlink at" in capsys.readouterr().out


def gitlink(repo, rel):
    """A submodule entry, not checked out (its empty directory keeps `git add -A` from dropping it)."""
    (repo / rel).mkdir(parents=True)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},{rel}")


def test_gate_refuses_a_path_dependency_into_or_containing_a_submodule(lp, env, tmp_path, capsys):
    gitlink(env.repo_a, "vendor/sub")
    commit_files(env.repo_a, {"vendor/lakefile.lean": "-- v\n",
                              "lake-manifest.json": manifest(("Sub", "vendor/sub"))})
    assert gate(lp, env, tmp_path) == 1
    assert "Sub (vendor/sub): vendor/sub is a submodule (gitlink)" in capsys.readouterr().err
    commit_manifest(env.repo_a, manifest(("Vendor", "vendor")))
    assert gate(lp, env, tmp_path) == 1
    assert "Vendor (vendor): contains a submodule (gitlink) vendor/sub" in capsys.readouterr().err


def test_gate_check_refuses_and_names_the_dependency_writing_nothing(lp, env, tmp_path, capsys):
    commit_manifest(env.repo_a, manifest(("R1", "../lean")))
    assert gate(lp, env, tmp_path, "--check") == 1
    assert "R1 (../lean)" in capsys.readouterr().err
    assert read_events(env) == []


def test_gate_check_reports_the_manifest_line_when_clean(lp, env, tmp_path, capsys):
    commit_manifest(env.repo_a, MATHLIB_MANIFEST)
    assert gate(lp, env, tmp_path, "--check") == 0
    assert "no path dependency outside the package directory" in capsys.readouterr().out


def test_gate_refuses_a_manifest_that_is_not_json(lp, env, tmp_path, capsys):
    commit_manifest(env.repo_a, "{not json\n")
    assert gate(lp, env, tmp_path) == 1
    assert "lake-manifest.json at" in capsys.readouterr().err


def test_only_the_committed_manifest_counts(lp, env, tmp_path, capsys):
    """A working-tree edit adding a path dependency does not change what the bound commit holds."""
    commit_manifest(env.repo_a, MATHLIB_MANIFEST)
    (env.repo_a / "lake-manifest.json").write_text(manifest(("R1", "../lean")))
    assert gate(lp, env, tmp_path) == 0


# register --like, nesting, path dependencies ------------------------------------------------------

def paper_repo(tmp_path, extra=None):
    files = {"paper/main.tex": "\\documentclass{article}\n"}
    for d in ("lean", "lean-r2"):
        files[f"{d}/lean-toolchain"] = "leanprover/lean4:v4.32.0\n"
        files[f"{d}/lake-manifest.json"] = MATHLIB_MANIFEST
    files.update(extra or {})
    return make_repo(tmp_path / "Article-X", extra_files=files)


def reg(lp, env, *args):
    return lp.main(["--registry", str(env.registry), "register", *args])


def register_r1(lp, env, root):
    assert reg(lp, env, "x-lean", "--repo", "git@github.com:example/Article-X.git", "--path",
               str(root / "lean"), "--owner-project", "Article-X", "--upstream-target", "palomar",
               "--automation", "paper-lean-round") == 0


def test_register_like_copies_a_siblings_fields_and_compares_pins(lp, env, tmp_path, capsys):
    root = paper_repo(tmp_path)
    register_r1(lp, env, root)
    capsys.readouterr()
    assert reg(lp, env, "x-lean-r2", "--path", str(root / "lean-r2"), "--like", "x-lean", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    pkg = read_reg(env)["packages"]["x-lean-r2"]
    assert pkg["repo"] == "git@github.com:example/Article-X.git"
    assert (pkg["owner_project"], pkg["upstream_target"], pkg["automation"]) == \
        ("Article-X", "palomar", "paper-lean-round")
    assert pkg["visibility"] == "private" and pkg["status"] == "active" and pkg["lease"] is None
    assert out["like"]["copied"] == ["repo", "owner_project", "upstream_target", "automation"]
    assert out["like"]["toolchain"]["match"] and out["like"]["mathlib_pin"]["match"]
    assert out["warnings"] == []
    assert read_events(env)[-1]["like"] == "x-lean"


def test_register_like_given_flags_win_and_a_pin_difference_is_a_note(lp, env, tmp_path, capsys):
    root = paper_repo(tmp_path, {"lean-r2/lean-toolchain": "leanprover/lean4:v4.33.0\n"})
    register_r1(lp, env, root)
    capsys.readouterr()
    assert reg(lp, env, "x-lean-r2", "--path", str(root / "lean-r2"), "--like", "x-lean",
               "--owner-project", "Round2", "--automation", "") == 0
    err = capsys.readouterr().err
    assert "toolchain leanprover/lean4:v4.33.0 differs from x-lean's (leanprover/lean4:v4.32.0)" in err
    assert "Mathlib pin 81a5d257c8 matches x-lean's" in err
    pkg = read_reg(env)["packages"]["x-lean-r2"]
    assert pkg["owner_project"] == "Round2" and pkg["automation"] == "" and pkg["upstream_target"] == "palomar"


def test_register_like_refuses_another_repository_unless_repo_is_given(lp, env, tmp_path, capsys):
    root = paper_repo(tmp_path)
    register_r1(lp, env, root)
    other = make_repo(tmp_path / "Elsewhere")
    capsys.readouterr()
    assert reg(lp, env, "y", "--path", str(other), "--like", "x-lean") == 1
    assert "is not in x-lean's git repository" in capsys.readouterr().err
    assert "y" not in read_reg(env)["packages"]
    assert reg(lp, env, "y", "--path", str(other), "--like", "x-lean",
               "--repo", "git@github.com:example/Elsewhere.git") == 0


def test_register_like_unknown_package_and_missing_repo(lp, env, tmp_path, capsys):
    root = paper_repo(tmp_path)
    assert reg(lp, env, "x-lean-r2", "--path", str(root / "lean-r2"), "--like", "nope") == 1
    assert "unknown package: 'nope'" in capsys.readouterr().err
    assert reg(lp, env, "x-lean-r2", "--path", str(root / "lean-r2")) == 2
    assert "--repo" in capsys.readouterr().err
    assert read_events(env) == []


def test_register_warns_when_a_package_dir_is_nested(lp, env, tmp_path, capsys):
    root = paper_repo(tmp_path, {"lean/contrib/lean-toolchain": "leanprover/lean4:v4.32.0\n"})
    register_r1(lp, env, root)
    capsys.readouterr()
    assert reg(lp, env, "x-contrib", "--path", str(root / "lean/contrib"), "--like", "x-lean") == 0
    assert "is inside x-lean's package directory" in capsys.readouterr().out
    assert reg(lp, env, "x-all", "--path", str(root), "--like", "x-lean") == 0
    assert "contains x-lean's package directory" in capsys.readouterr().out


def test_register_warns_about_a_path_dependency_outside(lp, env, tmp_path, capsys):
    root = paper_repo(tmp_path, {"lean-r2/lake-manifest.json": manifest(("R1", "../lean"))})
    register_r1(lp, env, root)
    capsys.readouterr()
    assert reg(lp, env, "x-lean-r2", "--path", str(root / "lean-r2"), "--like", "x-lean") == 0
    assert "path dependencies outside the package directory: R1 (../lean); `gate` refuses" in \
        capsys.readouterr().out
    assert repo_head(root)   # the registration itself is unaffected


# bound_tree fails closed (#4's review of a3bc0d7) ---------------------------------------------------

def test_bound_tree_refuses_when_ls_tree_fails(lp, env):
    """An unknown commit makes git ls-tree exit non-zero: a refusal, never an empty (so safe) tree."""
    import pytest
    with pytest.raises(lp.Fail, match=r"git ls-tree exited \d+"):
        lp.bound_tree(env.repo_a, "0" * 40)
    with pytest.raises(lp.Fail, match="cannot read the tree"):
        lp.check_path_deps_at(env.repo_a, "0" * 40)


def test_bound_tree_refuses_when_cat_file_fails(lp, env, monkeypatch):
    """A symlink target that cannot be read is a refusal, not an empty (so descending) target."""
    import pytest
    import subprocess
    commit_files(env.repo_a, {}, links={"Ok.lean": "Demo/Bar.lean"})
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if "cat-file" in cmd:
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: unable to read blob")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(lp.subprocess, "run", fake)
    with pytest.raises(lp.Fail, match="git cat-file exited 128: fatal: unable to read blob"):
        lp.check_path_deps_at(env.repo_a, repo_head(env.repo_a))


def test_bound_tree_refuses_an_unparseable_record(lp, env, monkeypatch):
    import pytest
    import subprocess
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if "ls-tree" in cmd:
            return subprocess.CompletedProcess(cmd, 0, b"garbage without a tab\0", b"")
        return real(cmd, *a, **kw)
    monkeypatch.setattr(lp.subprocess, "run", fake)
    with pytest.raises(lp.Fail, match="unexpected git ls-tree record"):
        lp.bound_tree(env.repo_a, repo_head(env.repo_a))
