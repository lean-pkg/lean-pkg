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


def test_gate_accepts_path_dependencies_inside_the_package_dir(lp, env, tmp_path, capsys):
    commit_manifest(env.repo_a, manifest(("Vendored", "./vendor/Helper"), ("Lake", ".lake/packages/x/sub")))
    assert gate(lp, env, tmp_path) == 0
    assert read_reg(env)["packages"]["alpha"]["status"] == "verified-private"


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
