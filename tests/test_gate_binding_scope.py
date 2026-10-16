#!/usr/bin/env python3
"""The scope of gate's HEAD binding (field reports #13 and #3).

1. The docs-only rule counts a `.lean` file as a build input only when a lean_lib or lean_exe of the package
   owns it (read from lakefile.toml), when the receipt's checker names it, or when the record's build_inputs
   match it; with a lakefile.lean (code) every `.lean` file still counts, and the refusal says why.
2. A change to claims.yaml alone keeps being refused (gate reads the claims at the receipt commit), with a
   message that says to commit claims.yaml before the verify run, or re-gate.
3. A nested registered package's files are not drift for the package around it, unless that package depends
   on it (a path package in lake-manifest.json, a lakefile naming it, a symlink into it)."""

from __future__ import annotations

import json
import os
import pathlib

import pytest

from .conftest import _git, base_pkg, make_receipt, read_events, read_reg, repo_head, write_receipt, write_reg

LIB_TOML = 'name = "chemotaxis"\n\n[[lean_lib]]\nname = "ChemotaxisBifurcation"\n'
GENERATOR = "verification/waves/w5-20261016/W5All.lean"


def main(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def commit_files(repo, files: dict, msg="change"):
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        _git(repo, "add", "-f", rel)
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


def opted(env, **kw):
    return {**read_reg(env)["packages"]["alpha"], "docs_descendants": True, **kw}


def bound_with(env, lakefile: dict):
    files = {"ChemotaxisBifurcation.lean": "import ChemotaxisBifurcation.Basic\n",
             "ChemotaxisBifurcation/Basic.lean": "-- basic\n", GENERATOR: "#check 1\n",
             "lake-manifest.json": json.dumps({"packages": []}) + "\n", **lakefile}
    return commit_files(env.repo_a, files, "sources")


def verdict(lp, env, pkg, change: dict, lakefile=None, checker="leancheck"):
    bound = bound_with(env, lakefile if lakefile is not None else {"lakefile.toml": LIB_TOML})
    head = commit_files(env.repo_a, change)
    return lp.package_descendant(env.repo_a, bound, head, pkg, checker=checker, nested=())


# --- 1. which .lean files are build inputs ----------------------------------------------------------

def test_generator_outside_every_lean_lib_is_admitted_with_the_opt_in(lp, env):
    ok, note, docs = verdict(lp, env, opted(env), {GENERATOR: "#print axioms foo\n"})
    assert ok and docs == [GENERATOR], note


def test_generator_outside_every_lean_lib_without_opt_in_gets_the_hint(lp, env):
    ok, note, docs = verdict(lp, env, read_reg(env)["packages"]["alpha"], {GENERATOR: "#print axioms foo\n"})
    assert not ok and docs is None
    assert "every changed path is a non-build file" in note, note


def test_gate_admits_a_generator_change_as_docs_only(lp, env, tmp_path, capsys):
    bound_with(env, {"lakefile.toml": LIB_TOML})
    reg = read_reg(env)
    reg["packages"]["alpha"]["docs_descendants"] = True
    write_reg(env, reg)
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, {GENERATOR: "#print axioms foo\n"})
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["descendant_of"] == r["commit"] and data["docs_paths"] == [GENERATOR]
    assert read_events(env)[-1]["event"] == "gate-passed" and head != r["commit"]


@pytest.mark.parametrize("path", ["ChemotaxisBifurcation.lean", "ChemotaxisBifurcation/Basic.lean",
                                  "ChemotaxisBifurcation/New/Deep.lean"])
def test_a_lean_lib_module_change_is_refused(lp, env, path):
    ok, note, _ = verdict(lp, env, opted(env), {path: "-- changed\n"})
    assert not ok and f"{path} (lean_lib ChemotaxisBifurcation" in note, note


def test_a_module_under_a_glob_is_refused(lp, env):
    toml = ('name = "x"\n[[lean_lib]]\nname = "Main"\nroots = ["Main"]\n'
            'globs = ["Main", "Extra.+"]\n')
    ok, note, _ = verdict(lp, env, opted(env), {"Extra/A.lean": "-- a\n"}, {"lakefile.toml": toml})
    assert not ok and "Extra/A.lean (lean_lib Main" in note, note


def test_lib_srcdir_and_package_srcdir_are_joined(lp, env):
    toml = 'name = "x"\nsrcDir = "lean"\n[[lean_lib]]\nname = "Lib"\nsrcDir = "src"\n'
    bound = bound_with(env, {"lakefile.toml": toml})
    head = commit_files(env.repo_a, {"Lib/X.lean": "-- outside the srcDir\n"})
    ok, note, docs = lp.package_descendant(env.repo_a, bound, head, opted(env), nested=())
    assert ok and docs == ["Lib/X.lean"], note
    head = commit_files(env.repo_a, {"lean/src/Lib/X.lean": "-- x\n"})
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, opted(env), nested=())
    assert not ok and "lean/src/Lib/X.lean (lean_lib Lib" in note, note


def test_lean_exe_root_is_a_build_input(lp, env):
    toml = LIB_TOML + '\n[[lean_exe]]\nname = "cli"\nroot = "Main"\n'
    ok, note, _ = verdict(lp, env, opted(env), {"Main.lean": "def main : IO Unit := pure ()\n"},
                          {"lakefile.toml": toml})
    assert not ok and "Main.lean (lean_exe cli" in note, note


@pytest.mark.parametrize("decl,path,label", [
    ('[[input_dir]]\nname = "data"\n', "data/table.csv", "input_dir data"),
    ('[[input_file]]\nname = "cfg"\npath = "conf/c.json"\n', "conf/c.json", "input_file cfg"),
    ('[[require]]\nname = "dep"\npath = "vendor/dep"\n', "vendor/dep/Dep.lean", "require dep path"),
])
def test_input_targets_and_path_requires_are_build_inputs(lp, env, decl, path, label):
    ok, note, _ = verdict(lp, env, opted(env), {path: "x\n"}, {"lakefile.toml": LIB_TOML + "\n" + decl})
    assert not ok and f"{path} ({label}" in note, note


def test_lean_file_named_by_the_checker_is_a_build_input(lp, env):
    ok, note, _ = verdict(lp, env, opted(env), {GENERATOR: "#print axioms foo\n"},
                          checker=f"lake env lean {GENERATOR}")
    assert not ok and f"{GENERATOR} (checker" in note, note


def test_lean_file_matching_build_inputs_is_a_build_input(lp, env):
    ok, note, _ = verdict(lp, env, opted(env, build_inputs=["verification/*"]), {GENERATOR: "-- x\n"})
    assert not ok and f"{GENERATOR} (build_inputs verification/*" in note, note


def test_lakefile_lean_keeps_every_lean_file_and_says_why(lp, env):
    lean = 'import Lake\nopen Lake DSL\npackage x\nlean_lib ChemotaxisBifurcation\n'
    ok, note, _ = verdict(lp, env, opted(env), {GENERATOR: "-- x\n"}, {"lakefile.lean": lean})
    assert not ok and f"{GENERATOR} (*.lean" in note, note
    assert "every *.lean counts as a build input: lakefile.lean" in note, note


@pytest.mark.parametrize("toml,why", [
    ('name = "x"\n', "declares no lean_lib or lean_exe"),
    (LIB_TOML + '\n[[mystery]]\nname = "m"\n', "declares [[mystery]]"),
    (LIB_TOML + 'srcDir = "../up"\n', "srcDir leaves the package directory"),
    ('name = "x"\n[[lean_lib]]\nname = 5\n', "without a name"),
    ("name = [unclosed\n", "does not parse"),
])
def test_an_unreadable_lakefile_toml_keeps_every_lean_file(lp, env, toml, why):
    ok, note, _ = verdict(lp, env, opted(env), {GENERATOR: "-- x\n"}, {"lakefile.toml": toml})
    assert not ok and f"{GENERATOR} (*.lean" in note and why in note, note


def test_scope_is_read_at_head_too(lp, env):
    """A lib added at HEAD (the lakefile change is refused anyway) never narrows the bound scope."""
    bound = bound_with(env, {"lakefile.toml": LIB_TOML})
    head = commit_files(env.repo_a, {"lakefile.toml": LIB_TOML + '\n[[lean_lib]]\nname = "verification"\n',
                                     GENERATOR: "-- x\n"})
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, opted(env), nested=())
    assert not ok and f"{GENERATOR} (lean_lib verification" in note and "lakefile.toml (lakefile.*" in note


# --- 2. claims.yaml changed after the receipt commit ------------------------------------------------

CLAIMS_MSG = "commit claims.yaml before the verify run, or re-gate"


@pytest.mark.parametrize("opt_in", [True, False])
def test_claims_only_change_says_commit_claims_first(lp, env, tmp_path, capsys, opt_in):
    if opt_in:
        reg = read_reg(env)
        reg["packages"]["alpha"]["docs_descendants"] = True
        write_reg(env, reg)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"claims.yaml": "schema: claims/1\nclaims: []\n", "README.md": "x\n"})
    p = write_receipt(tmp_path, r)
    before = env.registry.read_text()
    assert main(lp, env, "gate", "alpha", "--receipt", str(p)) == 1
    err = capsys.readouterr().err
    assert f"claims.yaml changed after the receipt commit {r['commit'][:12]}" in err, err
    assert CLAIMS_MSG in err and "docs/lifecycle.md#commit-the-claims-before-the-gate" in err
    assert "accept-docs-descendants" not in err
    assert env.registry.read_text() == before and read_events(env) == []


def test_claims_with_a_source_change_is_the_build_input_message(lp, env):
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, {"claims.yaml": "claims: []\n", "Main.lean": "-- x\n"})
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, opted(env), nested=())
    assert not ok and CLAIMS_MSG not in note and "build input(s) changed" in note, note


def test_lifecycle_documents_committing_claims_first():
    text = (pathlib.Path(__file__).resolve().parents[1] / "docs" / "lifecycle.md").read_text()
    assert "### Commit the claims before the gate" in text


# --- 3. nested registered packages ------------------------------------------------------------------

OUTER_TOML = 'name = "outer"\n\n[[lean_lib]]\nname = "Outer"\n'
INNER = {"contrib/lakefile.toml": 'name = "inner"\n[[lean_lib]]\nname = "Inner"\n',
         "contrib/lean-toolchain": "leanprover/lean4:v4.32.0\n", "contrib/Inner.lean": "-- inner\n",
         "contrib/claims.yaml": "claims: []\n"}
INNER_CHANGE = {"contrib/Inner.lean": "-- inner, changed\n",
                "contrib/.lean-receipts/abc-slurm-1.json": "{}\n", "contrib/claims.yaml": "claims: [x]\n"}


def nested_setup(env, register=True, manifest=None, outer=None):
    files = {"Outer.lean": "-- outer\n", "lakefile.toml": OUTER_TOML,
             "lake-manifest.json": json.dumps(manifest or {"packages": []}) + "\n", **INNER, **(outer or {})}
    commit_files(env.repo_a, files, "outer + inner")
    if register:
        reg = read_reg(env)
        reg["packages"]["alpha-contrib"] = base_pkg("alpha-contrib", env.repo_a / "contrib", "gate-pending")
        write_reg(env, reg)
    return make_receipt(env.repo_a)


def gate_check(lp, env, tmp_path, r, *extra):
    p = write_receipt(tmp_path, r)
    return main(lp, env, "gate", "alpha", "--receipt", str(p), "--check", *extra)


def test_nested_registered_package_change_is_not_outer_drift(lp, env, tmp_path, capsys):
    r = nested_setup(env)
    commit_files(env.repo_a, INNER_CHANGE)
    before = env.registry.read_text()
    code = gate_check(lp, env, tmp_path, r)
    out, err = capsys.readouterr()
    assert code == 0, err
    assert "outside nested registered package(s) contrib/" in out, out
    assert gate_check(lp, env, tmp_path, r, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["recorded"] is False and "docs_paths" not in data
    assert env.registry.read_text() == before


def test_nested_exclusion_note_and_outer_change_still_counts(lp, env):
    nested_setup(env)
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, INNER_CHANGE)
    lp.load_registry(env.registry)
    assert lp.registered_nested_dirs(env.repo_a) == ("contrib",)
    ok, note, docs = lp.package_descendant(env.repo_a, bound, head, read_reg(env)["packages"]["alpha"])
    assert ok and docs is None and "outside nested registered package(s) contrib/" in note, note
    head = commit_files(env.repo_a, {"Outer.lean": "-- outer, changed\n"})
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, read_reg(env)["packages"]["alpha"])
    assert not ok and "Outer.lean" in note and "contrib/" not in note.split("outside .lean-receipts/:")[1]


def test_nested_receipts_next_to_outer_evidence_are_admitted(lp, env):
    nested_setup(env)
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, {**INNER_CHANGE, ".lean-receipts/outer.json": "{}\n"})
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, {}, nested=("contrib",))
    assert ok and "ignored 3 change(s) under nested registered package(s) contrib/" in note, note


def test_outer_with_path_dependency_on_inner_is_still_refused(lp, env, tmp_path, capsys):
    man = {"packages": [{"name": "inner", "type": "path", "dir": "./contrib", "inherited": False}]}
    r = nested_setup(env, manifest=man)
    commit_files(env.repo_a, INNER_CHANGE)
    assert gate_check(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert "contrib/Inner.lean" in err and "still counted as build input(s): contrib/" in err, err
    assert "lake-manifest.json path package 'inner'" in err, err


@pytest.mark.parametrize("outer,needle", [
    ({"lakefile.toml": OUTER_TOML + '\n[[require]]\nname = "inner"\npath = "contrib"\n'},
     "lakefile.toml require inner path"),
    ({"lakefile.toml": 'name = "outer"\n[[lean_lib]]\nname = "Outer"\nsrcDir = "contrib"\n'},
     "lakefile.toml lean_lib Outer (sources under contrib/Outer)"),
    ({"lakefile.lean": "import Lake\nopen Lake DSL\npackage outer\nrequire inner from \"contrib\"\n"},
     "lakefile.lean mentions 'contrib'"),
    ({"lake-manifest.json": "{not json\n"}, "lake-manifest.json is not valid JSON"),
])
def test_outer_that_reads_the_inner_dir_keeps_it(lp, env, outer, needle):
    nested_setup(env, outer=outer)
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, INNER_CHANGE)
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, {}, nested=("contrib",))
    assert not ok and needle in note, note


def test_outer_symlink_into_inner_keeps_it(lp, env):
    nested_setup(env)
    os.symlink("contrib/Inner.lean", env.repo_a / "Linked.lean")
    _git(env.repo_a, "add", "Linked.lean")
    _git(env.repo_a, "commit", "-m", "link")
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, INNER_CHANGE)
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, {}, nested=("contrib",))
    assert not ok and "symlink Linked.lean resolves into it" in note, note


def test_unregistered_nested_dir_still_counts(lp, env, tmp_path, capsys):
    r = nested_setup(env, register=False)
    commit_files(env.repo_a, INNER_CHANGE)
    assert gate_check(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert "contrib/Inner.lean" in err and "nested registered" not in err, err


def test_nested_rule_applies_to_the_docs_rule_too(lp, env):
    nested_setup(env)
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, {**INNER_CHANGE, "README.md": "outer notes\n"})
    ok, note, docs = lp.package_descendant(env.repo_a, bound, head, {"docs_descendants": True},
                                           nested=("contrib",))
    assert ok and docs == ["README.md"], note
    assert "ignored 3 change(s) under nested registered package(s) contrib/" in note
