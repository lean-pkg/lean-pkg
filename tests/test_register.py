#!/usr/bin/env python3
"""register: the validated, deduping write path for new packages."""

from __future__ import annotations

from .conftest import (MATHLIB_MANIFEST, TOOLCHAIN, make_repo, read_events,
                       read_reg)


def register(lp, env, name, path, *extra):
    return lp.main(["--registry", str(env.registry), "register", name,
                    "--repo", f"git@github.com:example/{name}.git",
                    "--path", str(path), *extra])


def test_register_creates_entry_with_autodetect(lp, env, tmp_path):
    repo = make_repo(tmp_path / "gamma-repo",
                     extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
    rc = register(lp, env, "gamma", repo)
    assert rc == 0
    pkg = read_reg(env)["packages"]["gamma"]
    assert pkg["lean_toolchain"] == TOOLCHAIN
    assert pkg["mathlib_pin"] == "81a5d257c8"
    assert pkg["status"] == "active"
    assert pkg["visibility"] == "private"
    assert pkg["lease"] is None
    assert pkg["gate"]["axioms_allowed"] == ["propext", "Quot.sound",
                                             "Classical.choice"]
    assert pkg["history"][-1]["event"] == "registered"
    events = read_events(env)
    assert events[-1]["event"] == "registered"
    assert events[-1]["name"] == "gamma"


def test_register_refuses_duplicate_name(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "other-repo")
    rc = register(lp, env, "alpha", repo)
    assert rc == 1
    err = capsys.readouterr().err
    assert "already registered" in err and "alpha" in err
    assert read_reg(env)["packages"]["alpha"]["local_path"] == str(env.repo_a)
    assert read_events(env) == []


def test_register_refuses_duplicate_local_path(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "gamma-repo")
    # a different spelling of alpha's path (trailing slash) must still dedupe
    rc = register(lp, env, "gamma", str(env.repo_a) + "/")
    assert rc == 1
    err = capsys.readouterr().err
    assert "local_path already registered" in err and "alpha" in err
    assert "gamma" not in read_reg(env)["packages"]
    assert repo.is_dir()  # untouched


def test_register_dry_run_writes_nothing(lp, env, tmp_path, capsys):
    before = env.registry.read_text()
    repo = make_repo(tmp_path / "gamma-repo",
                     extra_files={"lake-manifest.json": MATHLIB_MANIFEST})
    rc = register(lp, env, "gamma", repo, "--dry-run")
    assert rc == 0
    out = capsys.readouterr().out
    assert "gamma:" in out and "DRY RUN" in out
    assert env.registry.read_text() == before
    assert read_events(env) == []


def test_register_dry_run_still_refuses_duplicate_path(lp, env, capsys):
    rc = register(lp, env, "demo", str(env.repo_a), "--dry-run")
    assert rc == 1
    err = capsys.readouterr().err
    assert "local_path already registered" in err and "alpha" in err


def test_register_missing_mathlib_warns_but_registers(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "delta-repo")  # no lake-manifest.json
    rc = register(lp, env, "delta", repo)
    assert rc == 0
    out = capsys.readouterr().out
    assert "warn:" in out and "lake-manifest" in out
    pkg = read_reg(env)["packages"]["delta"]
    assert pkg["mathlib_pin"] is None
    assert pkg["lean_toolchain"] == TOOLCHAIN


def test_register_metadata_workspace_agent_is_not_a_claim(lp, env, tmp_path):
    repo = make_repo(tmp_path / "eps-repo")
    rc = register(lp, env, "epsilon", repo,
                  "--workspace", "w7", "--agent", "max",
                  "--owner-project", "Article-X", "--notes", "hello")
    assert rc == 0
    pkg = read_reg(env)["packages"]["epsilon"]
    assert pkg["workspace"] == "w7" and pkg["agent"] == "max"
    assert pkg["lease"] is None  # metadata only — claiming is a separate act
    assert pkg["owner_project"] == "Article-X" and pkg["notes"] == "hello"


def test_register_initialises_comparator_null(lp, env, tmp_path, capsys):
    repo = make_repo(tmp_path / "gamma-repo")
    rc = register(lp, env, "gamma", repo, "--dry-run")
    assert rc == 0
    assert "comparator: null" in capsys.readouterr().out


# --- #22.7: duplicate Lean root namespace (warn only) ---

def test_register_warns_on_a_shared_root_namespace(lp, env, tmp_path, capsys):
    import json
    (env.repo_a / "lakefile.lean").write_text('import Lake\nopen Lake DSL\npackage alpha\n\nlean_lib «Spectral» where\n')
    repo = make_repo(tmp_path / "gamma-repo", extra_files={
        "lakefile.toml": 'name = "gamma"\n\n[[lean_lib]]\nname = "Spectral.Core"\n'})
    assert register(lp, env, "gamma", repo, "--dry-run", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["notes"]) == 1 and "Spectral" in data["notes"][0] and "alpha" in data["notes"][0]
    assert register(lp, env, "gamma", repo) == 0          # a warning, never a refusal
    cap = capsys.readouterr()
    assert "lean-pkg: note: Lean root namespace Spectral is also used by alpha" in cap.err
    assert cap.out.strip().splitlines()[-1].startswith("gamma: registered")
    assert "gamma" in read_reg(env)["packages"]


def test_register_without_a_clash_has_empty_notes(lp, env, tmp_path, capsys):
    import json
    (env.repo_a / "Alpha.lean").write_text("theorem t : True := trivial\n")
    repo = make_repo(tmp_path / "gamma-repo", extra_files={"Gamma.lean": "theorem g : True := trivial\n"})
    assert register(lp, env, "gamma", repo, "--json") == 0
    assert json.loads(capsys.readouterr().out)["notes"] == []


def test_lean_root_namespaces_sources(lp, tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    assert lp.lean_root_namespaces(d) == set()
    (d / "Top.lean").write_text("")
    (d / "lakefile.lean").write_text("lean_lib Foo.Bar\nlean_lib «Baz» where\n  roots := #[`Baz]\n")
    assert lp.lean_root_namespaces(d) == {"Foo", "Baz"}       # the lakefile wins over top-level files
    (d / "lakefile.lean").write_text("package p\n")
    assert lp.lean_root_namespaces(d) == {"Top"}


def test_audit_reports_duplicate_namespace_informationally(lp, env, fake_gh, capsys):
    import json
    (env.repo_a / "Shared.lean").write_text("")
    (env.repo_b / "Shared.lean").write_text("")
    before = env.registry.read_text()
    assert lp.main(["--registry", str(env.registry), "audit", "--fix", "--json"]) == 0
    found = [f for f in json.loads(capsys.readouterr().out)["findings"] if f["check"] == "duplicate-namespace"]
    assert sorted(f["name"] for f in found) == ["alpha", "beta"]
    assert all(f["fixed"] is False and "Shared" in f["detail"] for f in found)
    assert env.registry.read_text() == before
