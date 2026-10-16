#!/usr/bin/env python3
"""Docs-only descendants (opt-in per package, record field docs_descendants): a receipt bound to commit C is
also accepted at a descendant of C where no build input under the package directory changed. Without the
opt-in the evidence-only rule is unchanged (plus one hint clause when the docs rule would have admitted)."""

from __future__ import annotations

import json
import os

import pytest

from .conftest import (_git, add_comparator_config, make_comparator_receipt, make_receipt, read_events,
                       read_reg, repo_head, write_receipt, write_reg)

DOCS = {"README.md": "# alpha\n", "docs/x.md": "notes\n", "evidence/notes.md": "cluster job notes\n"}


def main(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def commit_files(repo, files: dict, msg="docs"):
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        _git(repo, "add", "-f", rel)
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


def opt_in(env, value=True, build_inputs=None):
    reg = read_reg(env)
    reg["packages"]["alpha"]["docs_descendants"] = value
    if build_inputs is not None:
        reg["packages"]["alpha"]["build_inputs"] = build_inputs
    write_reg(env, reg)


def gate_alpha(lp, env, tmp_path, receipt, *extra):
    p = write_receipt(tmp_path, receipt)
    return main(lp, env, "gate", "alpha", "--receipt", str(p), *extra)


# --- without the opt-in: unchanged, plus the hint -----------------------------------------------------

def test_without_opt_in_docs_descendant_refused_as_before_with_hint(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, DOCS)
    before = env.registry.read_text()
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    # the original message, unchanged
    assert (f"receipt commit {r['commit'][:12]}… != repo HEAD {head[:12]}… — the receipt must bind to HEAD "
            f"or an evidence-only descendant (HEAD {head[:12]} differs from {r['commit'][:12]} outside "
            f".lean-receipts/: ") in err
    # ... plus the hint
    assert "every changed path is a non-build file" in err
    assert "lean-pkg update alpha --accept-docs-descendants" in err
    assert env.registry.read_text() == before and read_events(env) == []


def test_without_opt_in_no_hint_when_a_build_input_changed(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"README.md": "x\n", "Main.lean": "-- changed\n"})
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert "outside .lean-receipts/" in err and "accept-docs-descendants" not in err


def test_without_opt_in_function_verdict_is_the_evidence_rule(lp, env):
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, DOCS)
    pkg = read_reg(env)["packages"]["alpha"]
    ok, note = lp.evidence_only_descendant(env.repo_a, bound, head)
    ok2, note2, docs = lp.package_descendant(env.repo_a, bound, head, pkg)
    assert (ok, ok2, docs) == (False, False, None)
    assert note2.startswith(note + "; every changed path is a non-build file")


# --- with the opt-in: admitted --------------------------------------------------------------------

def test_opt_in_admits_docs_only_descendant_event_and_json(lp, env, tmp_path, capsys):
    opt_in(env)
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, DOCS)
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "gate-passed" and data["commit"] == r["commit"]   # gated commit = receipt's
    assert data["descendant_of"] == r["commit"]
    assert data["docs_paths"] == sorted(DOCS)
    ev = read_events(env)[-1]
    assert ev["event"] == "gate-passed" and ev["commit"] == r["commit"]
    assert ev["descendant_of"] == r["commit"] and ev["docs_paths"] == sorted(DOCS)
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["status"] == "verified-private"
    assert pkg["gate"]["receipt"] == f".lean-receipts/{r['commit'][:12]}-slurm-12345.json"
    assert head != r["commit"]


def test_opt_in_text_line(lp, env, tmp_path, capsys):
    opt_in(env)
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, DOCS)
    assert gate_alpha(lp, env, tmp_path, r) == 0
    out = capsys.readouterr().out
    assert (f"✓ HEAD {head[:12]} is a docs-only descendant of {r['commit'][:12]} (3 non-build file(s): "
            f"README.md, docs/x.md, evidence/notes.md; opted in)") in out


def test_opt_in_gate_check_reports_and_writes_nothing(lp, env, tmp_path, capsys):
    opt_in(env)
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, DOCS)
    before = env.registry.read_text()
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 0
    out = capsys.readouterr().out
    assert f"✓ HEAD {head[:12]} is a docs-only descendant of {r['commit'][:12]}" in out
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["recorded"] is False and data["descendant_of"] == r["commit"]
    assert data["docs_paths"] == sorted(DOCS)
    assert env.registry.read_text() == before and read_events(env) == []
    assert not (env.repo_a / ".lean-receipts").exists()


def test_opt_in_evidence_only_descendant_carries_no_docs_fields(lp, env, tmp_path, capsys):
    """The evidence-only rule still decides first: an evidence-only HEAD is not a docs admission."""
    opt_in(env)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {".lean-receipts/earlier.json": "{}\n"})
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert "descendant_of" not in data and "docs_paths" not in data
    assert "descendant_of" not in read_events(env)[-1]


# --- with the opt-in: refused ---------------------------------------------------------------------

def _symlink(repo):
    os.symlink("README.md", repo / "link.md")
    _git(repo, "add", "link.md")
    _git(repo, "commit", "-m", "symlink")


def _gitlink(repo):
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{repo_head(repo)},vendor/dep")
    _git(repo, "commit", "-m", "gitlink")


def _rename_lean(repo):
    _git(repo, "mv", "Main.lean", "Main.md")
    _git(repo, "commit", "-m", "rename")


REFUSED = {
    "changed-lean": (lambda repo: commit_files(repo, {"Main.lean": "-- changed\n"}), "Main.lean (*.lean"),
    "new-lean": (lambda repo: commit_files(repo, {"Sub/New.lean": "-- new\n"}), "Sub/New.lean (*.lean"),
    "deleted-lean": (lambda repo: (_git(repo, "rm", "-q", "Main.lean"), _git(repo, "commit", "-m", "rm")),
                     "Main.lean (*.lean, deleted"),
    "lakefile": (lambda repo: commit_files(repo, {"lakefile.toml": "name = \"y\"\n"}), "lakefile.toml (lakefile.*"),
    "manifest": (lambda repo: commit_files(repo, {"lake-manifest.json": "{\"x\": 1}\n"}),
                 "lake-manifest.json (lake-manifest.json"),
    "toolchain": (lambda repo: commit_files(repo, {"lean-toolchain": "leanprover/lean4:v4.33.0\n"}),
                  "lean-toolchain (lean-toolchain"),
    "build-inputs-glob": (lambda repo: commit_files(repo, {"data/table.csv": "1,2\n"}),
                          "data/table.csv (build_inputs data/*.csv"),
    "claims": (lambda repo: commit_files(repo, {"claims.yaml": "schema: claims/1\nclaims: []\n"}),
               "claims.yaml (claims"),
    "symlink": (_symlink, "link.md (symlink"),
    "gitlink": (_gitlink, "vendor/dep (gitlink"),
    "gitattributes": (lambda repo: commit_files(repo, {".gitattributes": "*.md text\n"}),
                      ".gitattributes (.gitattributes"),
    "lfsconfig": (lambda repo: commit_files(repo, {".lfsconfig": "[lfs]\n"}), ".lfsconfig (.lfsconfig"),
    "rename-lean-to-md": (_rename_lean, "Main.lean (*.lean, deleted"),
}


@pytest.mark.parametrize("case", sorted(REFUSED))
def test_opt_in_refuses_build_input_change(lp, env, tmp_path, capsys, case):
    commit_files(env.repo_a, {"Main.lean": "-- main\n", "lakefile.toml": "name = \"x\"\n",
                              "lake-manifest.json": "{}\n"}, "sources")
    opt_in(env, build_inputs=["data/*.csv"])
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"README.md": "x\n"})
    make, needle = REFUSED[case]
    make(env.repo_a)
    before = env.registry.read_text()
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert "docs-only rule (opted in): build input(s) changed" in err, err
    assert needle in err, err
    assert "the receipt must be rebuilt at HEAD" in err
    assert "accept-docs-descendants" not in err
    assert env.registry.read_text() == before and read_events(env) == []


def test_opt_in_refuses_non_descendant(lp, env, tmp_path, capsys):
    opt_in(env)
    _git(env.repo_a, "checkout", "-q", "-b", "side")
    side = commit_files(env.repo_a, {"SIDE.md": "s\n"})
    _git(env.repo_a, "checkout", "-q", "-")
    commit_files(env.repo_a, {"README.md": "x\n"})
    assert gate_alpha(lp, env, tmp_path, make_receipt(env.repo_a, commit=side)) == 1
    err = capsys.readouterr().err
    assert f"{side[:12]} is not an ancestor of HEAD" in err and "accept-docs-descendants" not in err


def test_opt_in_formalization_keeps_the_derived_keys_rule(lp, env):
    commit_files(env.repo_a, {"formalization.yaml": "project:\n  name: alpha\n  description: a\n"})
    bound = repo_head(env.repo_a)
    pkg = {**read_reg(env)["packages"]["alpha"], "docs_descendants": True, "formalization_yaml": "formalization.yaml"}
    # a derived key (project.name) may change ...
    head = commit_files(env.repo_a, {"formalization.yaml": "project:\n  name: other\n  description: a\n",
                                     "README.md": "x\n"})
    ok, note, docs = lp.package_descendant(env.repo_a, bound, head, pkg)
    assert ok and docs == ["README.md"], note
    # ... a kept (hand-authored) key may not
    head = commit_files(env.repo_a, {"formalization.yaml": "project:\n  name: other\n  description: b\n"})
    ok, note, docs = lp.package_descendant(env.repo_a, bound, head, pkg)
    assert not ok and docs is None and "kept (hand-authored) key(s) project.description" in note


def test_opt_in_checker_file_is_a_build_input(lp, env):
    commit_files(env.repo_a, {"scripts/check.sh": "echo ok\n"})
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, {"scripts/check.sh": "echo changed\n"})
    pkg = {**read_reg(env)["packages"]["alpha"], "docs_descendants": True}
    ok, note, _ = lp.package_descendant(env.repo_a, bound, head, pkg, checker="bash scripts/check.sh --strict")
    assert not ok and "scripts/check.sh (checker" in note
    assert lp.package_descendant(env.repo_a, bound, head, pkg, checker="leancheck")[0]


def test_opt_in_subdirectory_package_ignores_paths_outside(lp, env, tmp_path):
    root = env.repo_a
    commit_files(root, {"lean/Main.lean": "-- m\n", "lean/lean-toolchain": "x\n"})
    bound = repo_head(root)
    head = commit_files(root, {"lean/README.md": "x\n", "paper/Other.lean": "-- outside\n"})
    pkg = {"name": "sub", "docs_descendants": True}
    ok, note, docs = lp.package_descendant(root / "lean", bound, head, pkg)
    assert ok and docs == ["README.md"], note


def _nested(env):
    root = env.repo_a
    commit_files(root, {"lean/contrib/Main.lean": "-- m\n", "lean/contrib/lean-toolchain": "x\n"})
    return root, root / "lean" / "contrib", repo_head(root), {"name": "sub", "docs_descendants": True}


@pytest.mark.parametrize("attr", [".gitattributes", "lean/.gitattributes", ".gitmodules", "lean/.lfsconfig"])
def test_opt_in_subdirectory_refuses_ancestor_attribute_files(lp, env, attr):
    root, pkg_dir, bound, pkg = _nested(env)
    head = commit_files(root, {"lean/contrib/README.md": "x\n", attr: "* text\n"})
    ok, note, docs = lp.package_descendant(pkg_dir, bound, head, pkg)
    assert not ok and docs is None
    assert f"{attr} (outside the package directory; can change the bytes the build sees)" in note, note


def test_opt_in_subdirectory_root_readme_still_admitted(lp, env):
    root, pkg_dir, bound, pkg = _nested(env)
    head = commit_files(root, {"lean/contrib/README.md": "x\n", "README.md": "root\n",
                               "paper/.gitattributes": "* text\n"})   # a sibling dir is not an ancestor
    ok, note, docs = lp.package_descendant(pkg_dir, bound, head, pkg)
    assert ok and docs == ["README.md"], note


# --- regate and comparator attach follow the same rule ---------------------------------------------

def test_regate_admits_docs_only_descendant(lp, env, tmp_path, capsys):
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = True
    write_reg(env, reg)
    prev = env.receipt_b["commit"]
    commit_files(env.repo_b, {"Demo/Extra.lean": "-- more\n"}, "source")
    r = make_receipt(env.repo_b, job_id="slurm-2")
    head = commit_files(env.repo_b, {"NOTES.md": "x\n"})
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "beta", "--receipt", str(p), "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "regated" and data["commit"] == r["commit"] and data["previous_commit"] == prev
    assert data["descendant_of"] == r["commit"] and data["docs_paths"] == ["NOTES.md"]
    ev = read_events(env)[-1]
    assert ev["event"] == "regated" and ev["descendant_of"] == r["commit"] and ev["docs_paths"] == ["NOTES.md"]
    assert head != r["commit"]


def test_regate_refused_without_opt_in(lp, env, tmp_path, capsys):
    commit_files(env.repo_b, {"Demo/Extra.lean": "-- more\n"}, "source")
    r = make_receipt(env.repo_b, job_id="slurm-2")
    commit_files(env.repo_b, {"NOTES.md": "x\n"})
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "beta", "--receipt", str(p), "--regate") == 1
    assert "lean-pkg update beta --accept-docs-descendants" in capsys.readouterr().err


def _gated_alpha_with_comparator(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    assert gate_alpha(lp, env, tmp_path, r) == 0
    capsys.readouterr()
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    return r, comp, head


def test_comparator_attach_admits_docs_only_descendant(lp, env, tmp_path, capsys):
    opt_in(env)
    r, comp, head = _gated_alpha_with_comparator(lp, env, tmp_path, capsys)
    p = write_receipt(tmp_path, comp, "comp.json")
    assert main(lp, env, "comparator", "alpha", "--receipt", str(p), "--check") == 0
    assert f"✓ HEAD {head[:12]} is a docs-only descendant of {r['commit'][:12]}" in capsys.readouterr().out
    assert main(lp, env, "comparator", "alpha", "--receipt", str(p), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "comparator-attached" and data["commit"] == r["commit"]
    assert data["descendant_of"] == r["commit"] and data["docs_paths"] == ["README.md"]
    ev = read_events(env)[-1]
    assert ev["event"] == "comparator-attached" and ev["docs_paths"] == ["README.md"]


def test_comparator_attach_refused_without_opt_in(lp, env, tmp_path, capsys):
    r, comp, _head = _gated_alpha_with_comparator(lp, env, tmp_path, capsys)
    p = write_receipt(tmp_path, comp, "comp.json")
    assert main(lp, env, "comparator", "alpha", "--receipt", str(p)) == 1
    err = capsys.readouterr().err
    assert "evidence-only" in err and "lean-pkg update alpha --accept-docs-descendants" in err


# --- update flags and the schema -------------------------------------------------------------------

def test_update_sets_and_clears_the_opt_in(lp, env, capsys):
    assert main(lp, env, "update", "alpha", "--accept-docs-descendants", "--reason", "docs commits") == 0
    pkg = read_reg(env)["packages"]["alpha"]
    assert pkg["docs_descendants"] is True
    ev = read_events(env)[-1]
    assert ev["event"] == "updated" and ev["fields"] == ["docs_descendants"] and ev["reason"] == "docs commits"
    assert ev["docs_descendants_from"] is None
    assert main(lp, env, "update", "alpha", "--no-accept-docs-descendants", "--reason", "back") == 0
    assert read_reg(env)["packages"]["alpha"]["docs_descendants"] is False
    assert read_events(env)[-1]["docs_descendants_from"] is True


def test_update_build_inputs(lp, env):
    assert main(lp, env, "update", "alpha", "--build-inputs", "data/*.csv, *.json", "--reason", "data") == 0
    assert read_reg(env)["packages"]["alpha"]["build_inputs"] == ["data/*.csv", "*.json"]
    assert main(lp, env, "update", "alpha", "--build-inputs", "../x", "--reason", "bad") == 1


def test_update_dry_run_writes_nothing(lp, env, capsys):
    before = env.registry.read_text()
    assert main(lp, env, "update", "alpha", "--accept-docs-descendants", "--reason", "r", "--dry-run",
                "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "update-dry-run"
    assert data["changes"] == {"docs_descendants": {"from": None, "to": True}}
    assert env.registry.read_text() == before and read_events(env) == []


@pytest.mark.parametrize("flag", [["--accept-docs-descendants"], ["--no-accept-docs-descendants"],
                                  ["--build-inputs", "data/*"]])
def test_update_refused_without_reason(lp, env, capsys, flag):
    if flag[0] == "--no-accept-docs-descendants":
        opt_in(env)
    before = env.registry.read_text()
    assert main(lp, env, "update", "alpha", *flag) == 1
    assert "requires --reason" in capsys.readouterr().err
    assert env.registry.read_text() == before


def test_update_flags_mutually_exclusive(lp, env):
    with pytest.raises(SystemExit):
        main(lp, env, "update", "alpha", "--accept-docs-descendants", "--no-accept-docs-descendants",
             "--reason", "r")


def test_validate_accepts_the_fields_and_rejects_bad_values(lp, env):
    reg = read_reg(env)
    reg["packages"]["alpha"]["docs_descendants"] = True
    reg["packages"]["alpha"]["build_inputs"] = ["data/*.csv"]
    assert lp.validate_registry(reg) == []
    reg["packages"]["alpha"]["docs_descendants"] = "yes"
    reg["packages"]["alpha"]["build_inputs"] = ["/abs", ""]
    errs = lp.validate_registry(reg)
    assert any("docs_descendants: must be true or false" in e for e in errs)
    assert sum("build_inputs" in e for e in errs) == 2
