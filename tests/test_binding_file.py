#!/usr/bin/env python3
"""The committed docs-only opt-in, lean-pkg-binding.yaml (proposal 0002, stage G1, #319): the strict parse,
the read at the bound commit only, rules 1 to 5, the registry precedence, the build_inputs union, and what
gate, regate, gate --check, comparator, publish --dry-run and audit report."""

from __future__ import annotations

import json
import os

import pytest
import yaml

from lean_pkg import binding_file as bf

from .conftest import (_git, add_comparator_config, make_comparator_receipt, make_receipt, read_events,
                       read_reg, repo_head, write_receipt, write_reg)

FILE = "lean-pkg-binding.yaml"
OPT_IN = "schema: lean-pkg-binding/1\ndocs_descendants: true\n"
OPT_OUT = "schema: lean-pkg-binding/1\ndocs_descendants: false\n"


def main(lp, env, *argv):
    return lp.main(["--registry", str(env.registry), *argv])


def commit_files(repo, files: dict, msg="edit"):
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        _git(repo, "add", "-f", rel)
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


def remove_file(repo, rel, msg="rm"):
    _git(repo, "rm", "-q", rel)
    _git(repo, "commit", "-m", msg)
    return repo_head(repo)


def set_record(env, name="alpha", **fields):
    reg = read_reg(env)
    for k, v in fields.items():
        if v is None:
            reg["packages"][name].pop(k, None)
        else:
            reg["packages"][name][k] = v
    write_reg(env, reg)


def pkg_of(env, name="alpha"):
    return read_reg(env)["packages"][name]


def gate_alpha(lp, env, tmp_path, receipt, *extra):
    return main(lp, env, "gate", "alpha", "--receipt", str(write_receipt(tmp_path, receipt)), *extra)


# --- parse: valid --------------------------------------------------------------------------------

def test_parse_minimal_and_with_build_inputs():
    doc, problems = bf.parse(OPT_IN.encode())
    assert problems == [] and doc == {"schema": bf.SCHEMA, "docs_descendants": True, "build_inputs": []}
    doc, problems = bf.parse((OPT_IN + "build_inputs:\n  - data/*.csv\n  - 'Verify/strings.txt'\n").encode())
    assert problems == [] and doc["build_inputs"] == ["data/*.csv", "Verify/strings.txt"]
    doc, problems = bf.parse(("# what it permits\n" + OPT_OUT + "build_inputs: []\n").encode())
    assert problems == [] and doc["docs_descendants"] is False
    doc, problems = bf.parse(b"\xef\xbb\xbf" + OPT_IN.encode())          # a BOM is tolerated
    assert problems == [] and doc["docs_descendants"] is True


# --- parse: refused, each named ------------------------------------------------------------------

PARSE_REFUSED = {
    "missing-schema": ("docs_descendants: true\n", "schema: missing"),
    "newer-schema": ("schema: lean-pkg-binding/2\ndocs_descendants: true\n",
                     "schema: lean-pkg-binding/2 is newer than this lean-pkg reads (lean-pkg-binding/1); upgrade"),
    "other-schema": ("schema: claims/1\ndocs_descendants: true\n", "schema: must be lean-pkg-binding/1"),
    "unknown-key": (OPT_IN + "docs_descendant: true\n", "unknown key 'docs_descendant'"),
    "repeated-key": (OPT_IN + "docs_descendants: true\n", "docs_descendants: repeated key"),
    "yes": ("schema: lean-pkg-binding/1\ndocs_descendants: yes\n", "docs_descendants: must be the literal"),
    "on": ("schema: lean-pkg-binding/1\ndocs_descendants: on\n", "docs_descendants: must be the literal"),
    "True": ("schema: lean-pkg-binding/1\ndocs_descendants: True\n", "docs_descendants: must be the literal"),
    "quoted-true": ("schema: lean-pkg-binding/1\ndocs_descendants: \"true\"\n",
                    "docs_descendants: must be the literal true or false (unquoted)"),
    "one": ("schema: lean-pkg-binding/1\ndocs_descendants: 1\n", "docs_descendants: must be the literal"),
    "missing-docs": ("schema: lean-pkg-binding/1\n", "docs_descendants: missing"),
    "list-docs": ("schema: lean-pkg-binding/1\ndocs_descendants: [true]\n", "docs_descendants: must be the literal"),
    "build-inputs-scalar": (OPT_IN + "build_inputs: data/*.csv\n", "build_inputs: must be a list of globs"),
    "build-inputs-null": (OPT_IN + "build_inputs:\n", "build_inputs: must be a list of globs"),
    "build-inputs-abs": (OPT_IN + "build_inputs: [/abs]\n", "build_inputs: '/abs' must be package-relative"),
    "build-inputs-dotdot": (OPT_IN + "build_inputs: [../x]\n", "build_inputs: '../x' must be package-relative"),
    "build-inputs-empty": (OPT_IN + "build_inputs: ['']\n", "build_inputs: '' is not a non-empty string"),
    "build-inputs-int": (OPT_IN + "build_inputs: [1]\n", "build_inputs: 1 is not a non-empty string"),
    "build-inputs-map": (OPT_IN + "build_inputs: [{a: b}]\n", "is not a non-empty string"),
    "empty": ("", "empty"),
    "comment-only": ("# nothing\n", "empty"),
    "top-level-list": ("- schema\n", "top level is not a mapping"),
    "anchor": ("schema: lean-pkg-binding/1\ndocs_descendants: &t true\n", "anchor &t is not allowed"),
    "alias": ("x: &t true\nschema: lean-pkg-binding/1\ndocs_descendants: *t\n", "is not allowed"),
    "tag": ("schema: lean-pkg-binding/1\ndocs_descendants: !!str true\n", "explicit tag"),
    "tag-bool": ("schema: lean-pkg-binding/1\ndocs_descendants: !!bool yes\n", "explicit tag"),
    "not-yaml": ("schema: [\n", "not valid YAML"),
    "two-documents": (OPT_IN + "---\n" + OPT_IN, "not valid YAML"),
}


@pytest.mark.parametrize("case", sorted(PARSE_REFUSED))
def test_parse_refuses(case):
    text, needle = PARSE_REFUSED[case]
    doc, problems = bf.parse(text.encode(), "lean-pkg-binding.yaml at abc")
    assert doc is None and problems, case
    assert all(p.startswith("lean-pkg-binding.yaml at abc: ") for p in problems), problems
    assert any(needle in p for p in problems), problems


def test_parse_refuses_invalid_utf8_and_oversize():
    doc, problems = bf.parse(OPT_IN.encode() + b"# \xff\xfe\n")
    assert doc is None and "not valid UTF-8" in problems[0]
    big = (OPT_IN + "#" + "x" * bf.MAX_BYTES + "\n").encode()
    doc, problems = bf.parse(big)
    assert doc is None and "larger than the 65536-byte limit" in problems[0]


def test_yaml_safe_load_would_have_opted_in_on_yes():
    """Why the parse is node level: YAML 1.1 reads `yes` as true; the file must say what it means."""
    assert yaml.safe_load("docs_descendants: yes")["docs_descendants"] is True
    assert bf.parse(b"schema: lean-pkg-binding/1\ndocs_descendants: yes\n")[0] is None


# --- read at X ----------------------------------------------------------------------------------

def test_read_at_absent_and_regular_files(env):
    repo = env.repo_a
    x0 = repo_head(repo)
    assert bf.read_at(repo, x0).state == "absent"
    x1 = commit_files(repo, {FILE: OPT_IN})
    b = bf.read_at(repo, x1)
    assert b.state == "valid" and b.doc["docs_descendants"] is True
    os.chmod(repo / FILE, 0o755)
    _git(repo, "add", FILE)
    _git(repo, "commit", "-m", "exec bit")
    x2 = repo_head(repo)
    assert _git(repo, "ls-tree", x2, FILE).stdout.split()[0] == "100755"
    assert bf.read_at(repo, x2).state == "valid"
    assert bf.read_at(repo, x0).state == "absent"          # an earlier commit is read as it was


def test_read_at_refuses_a_symlink(env):
    repo = env.repo_a
    commit_files(repo, {"real.yaml": OPT_IN})
    os.symlink("real.yaml", repo / FILE)
    _git(repo, "add", FILE)
    _git(repo, "commit", "-m", "symlink")
    b = bf.read_at(repo, repo_head(repo))
    assert b.state == "invalid" and b.problems[0].endswith(f"{FILE} at {repo_head(repo)[:12]}: a symlink, "
                                                           f"not a regular file")


def test_read_at_refuses_a_gitlink(env):
    repo = env.repo_a
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{repo_head(repo)},{FILE}")
    _git(repo, "commit", "-m", "gitlink")
    b = bf.read_at(repo, repo_head(repo))
    assert b.state == "invalid" and "a gitlink, not a regular file" in b.problems[0]


def test_read_at_refuses_a_directory(env):
    repo = env.repo_a
    x = commit_files(repo, {f"{FILE}/inner.yaml": OPT_IN})
    b = bf.read_at(repo, x)
    assert b.state == "invalid" and "a directory, not a regular file" in b.problems[0]


def test_read_at_refuses_an_oversize_blob_before_reading_it(env):
    x = commit_files(env.repo_a, {FILE: OPT_IN + "#" + "x" * bf.MAX_BYTES + "\n"})
    b = bf.read_at(env.repo_a, x)
    assert b.state == "invalid" and "larger than the 65536-byte limit" in b.problems[0]


# --- rule 1: read at X only ----------------------------------------------------------------------

def test_rule1_working_tree_and_staged_files_have_no_effect(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    (env.repo_a / FILE).write_text(OPT_IN)                         # untracked
    assert bf.resolve_opt_in(env.repo_a, r["commit"], pkg_of(env)).rule == "evidence-only"
    assert gate_alpha(lp, env, tmp_path, r) == 1
    assert "outside .lean-receipts/" in capsys.readouterr().err
    _git(env.repo_a, "add", FILE)                                  # staged, not committed
    assert repo_head(env.repo_a) == head
    ok, note, docs = lp.package_descendant(env.repo_a, r["commit"], head, pkg_of(env))
    assert (ok, docs) == (False, None) and "was added after" not in note, note
    assert gate_alpha(lp, env, tmp_path, r) == 1


def test_rule1_file_added_and_removed_between_x_and_head_has_no_effect(lp, env):
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {FILE: OPT_IN})
    remove_file(env.repo_a, FILE)
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    ok, note, docs, oi = lp.package_binding(env.repo_a, r["commit"], head, pkg_of(env))
    assert (ok, docs) == (False, None) and oi.file_state == "absent" and oi.rule == "evidence-only"
    assert "every changed path is a non-build file" in note and "was added after" not in note, note


# --- rule 2: a file that exists at HEAD but not at X ----------------------------------------------

def test_rule2_file_committed_after_x_is_refused_with_the_clause(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, {FILE: OPT_IN, "README.md": "x\n"})
    before = env.registry.read_text()
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert (f"; {FILE} was added after {r['commit'][:12]}; the opt-in is read at the bound commit only"
            in err), err
    assert env.registry.read_text() == before and read_events(env) == []
    ok, note, _docs = lp.package_descendant(env.repo_a, r["commit"], head, pkg_of(env))
    assert not ok and note.endswith("the opt-in is read at the bound commit only"), note


def test_rule2_with_registry_true_refused_by_rule3_naming_the_file(lp, env, tmp_path, capsys):
    set_record(env, docs_descendants=True)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {FILE: OPT_IN, "README.md": "x\n"})
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert "docs-only rule (opted in by the registry record): build input(s) changed" in err, err
    assert f"{FILE} ({FILE})" in err and "was added after" in err, err


# --- rule 3: the file is a build input under the docs rule ------------------------------------------

RULE3 = {
    "flip": lambda repo: commit_files(repo, {FILE: OPT_OUT}),
    "add-glob": lambda repo: commit_files(repo, {FILE: OPT_IN + "build_inputs: [data/*.csv]\n"}),
    "whitespace": lambda repo: commit_files(repo, {FILE: OPT_IN + "\n"}),
    "removed": lambda repo: remove_file(repo, FILE),
    "nested-copy": lambda repo: commit_files(repo, {f"docs/{FILE}": OPT_IN}),
}


@pytest.mark.parametrize("case", sorted(RULE3))
def test_rule3_any_change_to_the_file_after_x_is_refused(lp, env, tmp_path, capsys, case):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"README.md": "x\n"})
    RULE3[case](env.repo_a)
    before = env.registry.read_text()
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert f"docs-only rule (opted in at {r['commit'][:12]} by {FILE}): build input(s) changed" in err, err
    path = f"docs/{FILE}" if case == "nested-copy" else FILE
    assert f"{path} ({FILE}{', deleted' if case == 'removed' else ''})" in err, err
    assert "the receipt must be rebuilt at HEAD" in err
    assert env.registry.read_text() == before and read_events(env) == []


def test_rule3_readme_only_change_is_admitted_with_the_source(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    assert gate_alpha(lp, env, tmp_path, r) == 0
    out = capsys.readouterr().out
    assert (f"✓ HEAD {head[:12]} is a docs-only descendant of {r['commit'][:12]} (1 non-build file(s): "
            f"README.md; opted in at {r['commit'][:12]} by {FILE})") in out, out


# --- rule 4: an invalid file at X fails closed -----------------------------------------------------

@pytest.mark.parametrize("text,key", [(OPT_IN + "docs_descendant: true\n", "unknown key 'docs_descendant'"),
                                      ("schema: lean-pkg-binding/1\ndocs_descendants: yes\n",
                                       "docs_descendants: must be the literal")])
def test_rule4_invalid_file_with_registry_true_is_evidence_only(lp, env, tmp_path, capsys, text, key):
    set_record(env, docs_descendants=True)
    commit_files(env.repo_a, {FILE: text}, "bad opt-in")
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"README.md": "x\n"})
    x12 = r["commit"][:12]
    assert gate_alpha(lp, env, tmp_path, r) == 1
    err = capsys.readouterr().err
    assert f"{FILE} at {x12} does not opt in: {key}" in err, err
    assert f"the registry record's docs_descendants: true is ignored once {FILE} exists at {x12}" in err, err
    assert "accept-docs-descendants" not in err


def test_rule4_symlink_at_x_does_not_opt_in(lp, env):
    commit_files(env.repo_a, {"real.yaml": OPT_IN})
    os.symlink("real.yaml", env.repo_a / FILE)
    _git(env.repo_a, "add", FILE)
    _git(env.repo_a, "commit", "-m", "symlink")
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    ok, note, docs, oi = lp.package_binding(env.repo_a, bound, head, {"name": "alpha"})
    assert (ok, docs, oi.rule, oi.file_state) == (False, None, "evidence-only", "invalid")
    assert f"{FILE} at {bound[:12]} does not opt in: a symlink, not a regular file" in note, note


# --- rule 5: only the package's own directory counts ------------------------------------------------

def test_rule5_copy_under_lean_receipts_is_not_read(lp, env):
    bound = commit_files(env.repo_a, {f".lean-receipts/{FILE}": OPT_IN})
    assert bf.read_at(env.repo_a, bound).state == "absent"
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    ok, _note, docs, oi = lp.package_binding(env.repo_a, bound, head, {"name": "alpha"})
    assert (ok, docs, oi.rule) == (False, None, "evidence-only")


def test_rule5_subdirectory_package_ignores_the_root_copy_and_reads_its_own(lp, env):
    root = env.repo_a
    bound = commit_files(root, {FILE: OPT_IN, "lean/Main.lean": "-- m\n", "lean/lean-toolchain": "x\n"})
    head = commit_files(root, {"lean/README.md": "x\n"})
    assert bf.read_at(root / "lean", bound).state == "absent"
    ok, _note, docs, oi = lp.package_binding(root / "lean", bound, head, {"name": "sub"}, nested=())
    assert (ok, docs, oi.file_state) == (False, None, "absent")
    bound = commit_files(root, {f"lean/{FILE}": OPT_IN})
    head = commit_files(root, {"lean/README.md": "y\n"})
    ok, note, docs, oi = lp.package_binding(root / "lean", bound, head, {"name": "sub"}, nested=())
    assert ok and docs == ["README.md"] and oi.source == "committed" and oi.path == FILE, note
    assert f"opted in at {bound[:12]} by {FILE}" in note


def test_rule5_nested_package_copy_is_not_the_outer_package_opt_in(lp, env):
    root = env.repo_a
    bound = commit_files(root, {f"contrib/{FILE}": OPT_IN, "contrib/Main.lean": "-- m\n",
                                "contrib/lean-toolchain": "x\n"})
    head = commit_files(root, {"README.md": "x\n", "contrib/README.md": "x\n"})
    assert bf.read_at(root, bound).state == "absent"
    ok, _note, docs, oi = lp.package_binding(root, bound, head, {"name": "alpha"}, nested=("contrib",))
    assert (ok, docs, oi.file_state) == (False, None, "absent")
    # the nested package reads its own
    ok, note, docs, oi = lp.package_binding(root / "contrib", bound, head, {"name": "contrib"}, nested=())
    assert ok and docs == ["README.md"] and oi.source == "committed", note


# --- precedence: the five rows -----------------------------------------------------------------------

def _row(lp, env, file_text, reg):
    pkg = {"name": "alpha"}
    if reg is not None:
        pkg["docs_descendants"] = reg
    if file_text is not None:
        commit_files(env.repo_a, {FILE: file_text}, "opt-in file")
    bound = repo_head(env.repo_a)
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    return bound, lp.package_binding(env.repo_a, bound, head, pkg)


@pytest.mark.parametrize("reg", [None, False])
def test_precedence_absent_file_without_registry_true_is_evidence_only(lp, env, reg):
    _bound, (ok, note, docs, oi) = _row(lp, env, None, reg)
    assert (ok, docs, oi.rule, oi.source) == (False, None, "evidence-only", None)
    assert "every changed path is a non-build file" in note


def test_precedence_absent_file_registry_true_is_the_registry_route(lp, env):
    _bound, (ok, note, docs, oi) = _row(lp, env, None, True)
    assert ok and docs == ["README.md"] and (oi.source, oi.path) == ("registry", None)
    assert note.endswith("README.md; opted in by the registry record)"), note


@pytest.mark.parametrize("reg", [None, True])
def test_precedence_committed_true_opts_in(lp, env, reg):
    bound, (ok, note, docs, oi) = _row(lp, env, OPT_IN, reg)
    assert ok and docs == ["README.md"] and oi.view() == {"source": "committed", "commit": bound, "path": FILE}
    assert note.endswith(f"README.md; opted in at {bound[:12]} by {FILE})"), note


def test_precedence_registry_false_vetoes_a_committed_true(lp, env, tmp_path, capsys):
    bound, (ok, note, docs, oi) = _row(lp, env, OPT_IN, False)
    assert (ok, docs, oi.rule, oi.source) == (False, None, "evidence-only", None)
    veto = (f"the registry record refuses the docs-only rule (docs_descendants: false), over {FILE} at "
            f"{bound[:12]}")
    assert veto in note and "accept-docs-descendants" not in note, note
    set_record(env, docs_descendants=False)
    assert gate_alpha(lp, env, tmp_path, make_receipt(env.repo_a, commit=bound)) == 1
    assert veto in capsys.readouterr().err


@pytest.mark.parametrize("reg", [None, False, True])
def test_precedence_committed_false_is_evidence_only(lp, env, reg):
    bound, (ok, note, docs, oi) = _row(lp, env, OPT_OUT, reg)
    assert (ok, docs, oi.rule, oi.file_state) == (False, None, "evidence-only", "false")
    assert f"{FILE} at {bound[:12]} says docs_descendants: false" in note, note
    assert ("is ignored once" in note) == (reg is True), note
    assert "accept-docs-descendants" not in note


# --- build_inputs: the union of the file's and the record's ------------------------------------------

@pytest.mark.parametrize("changed,admitted", [("data/t.csv", False), ("extra.txt", False), ("README.md", True)])
def test_build_inputs_union(lp, env, changed, admitted):
    commit_files(env.repo_a, {FILE: OPT_IN + "build_inputs: [data/*.csv]\n", "data/t.csv": "1\n",
                              "extra.txt": "e\n"}, "opt in")
    bound = repo_head(env.repo_a)
    pkg = {"name": "alpha", "build_inputs": ["extra.txt", "data/*.csv"]}
    head = commit_files(env.repo_a, {changed: "changed\n"})
    ok, note, docs, oi = lp.package_binding(env.repo_a, bound, head, pkg)
    assert oi.build_inputs == ("data/*.csv", "extra.txt")          # file first, no repeats
    assert ok is admitted, note
    if not admitted:
        assert f"{changed} (build_inputs " in note, note


# --- events, --json, gate --check, regate, comparator -----------------------------------------------

def test_gate_event_and_json_carry_opt_in_committed(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"README.md": "x\n"})
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    want = {"source": "committed", "commit": r["commit"], "path": FILE}
    assert data["opt_in"] == want and data["descendant_of"] == r["commit"] and data["docs_paths"] == ["README.md"]
    ev = read_events(env)[-1]
    assert ev["event"] == "gate-passed" and ev["opt_in"] == want


def test_gate_event_and_json_carry_opt_in_registry(lp, env, tmp_path, capsys):
    set_record(env, docs_descendants=True)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {"README.md": "x\n"})
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    want = {"source": "registry", "commit": r["commit"], "path": None}
    assert json.loads(capsys.readouterr().out)["opt_in"] == want
    assert read_events(env)[-1]["opt_in"] == want


def test_evidence_only_admission_has_no_opt_in(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, {".lean-receipts/earlier.json": "{}\n"})
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert not {"opt_in", "descendant_of", "docs_paths"} & set(data)
    assert "opt_in" not in read_events(env)[-1]


def test_gate_check_reports_the_opt_in_and_writes_nothing(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    r = make_receipt(env.repo_a)
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    before = env.registry.read_text()
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check") == 0
    assert (f"✓ HEAD {head[:12]} is a docs-only descendant of {r['commit'][:12]} (1 non-build file(s): "
            f"README.md; opted in at {r['commit'][:12]} by {FILE})") in capsys.readouterr().out
    assert main(lp, env, "gate", "alpha", "--receipt", str(p), "--check", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["recorded"] is False and data["opt_in"]["source"] == "committed"
    assert env.registry.read_text() == before and read_events(env) == []
    assert not (env.repo_a / ".lean-receipts").exists()
    assert _git(env.repo_a, "status", "--porcelain").stdout.strip() == ""


def test_regate_event_carries_opt_in(lp, env, tmp_path, capsys):
    commit_files(env.repo_b, {FILE: OPT_IN, "Demo/Extra.lean": "-- more\n"}, "source and opt-in")
    r = make_receipt(env.repo_b, job_id="slurm-2")
    commit_files(env.repo_b, {"NOTES.md": "x\n"})
    p = write_receipt(tmp_path, r)
    assert main(lp, env, "gate", "beta", "--receipt", str(p), "--regate", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["event"] == "regated" and data["opt_in"] == {"source": "committed", "commit": r["commit"],
                                                             "path": FILE}
    assert read_events(env)[-1]["opt_in"]["source"] == "committed"


def test_comparator_attach_event_carries_opt_in(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    assert gate_alpha(lp, env, tmp_path, r) == 0
    head = commit_files(env.repo_a, {"README.md": "x\n"})
    capsys.readouterr()
    p = write_receipt(tmp_path, comp, "comp.json")
    assert main(lp, env, "comparator", "alpha", "--receipt", str(p), "--check", "--json") == 0
    assert json.loads(capsys.readouterr().out)["opt_in"]["source"] == "committed"
    assert main(lp, env, "comparator", "alpha", "--receipt", str(p)) == 0
    assert f"✓ HEAD {head[:12]} is a docs-only descendant of {r['commit'][:12]}" in capsys.readouterr().out
    ev = read_events(env)[-1]
    assert ev["event"] == "comparator-attached" and ev["opt_in"] == {"source": "committed",
                                                                     "commit": r["commit"], "path": FILE}


# --- publish --dry-run and audit ---------------------------------------------------------------------

def test_publish_dry_run_line_names_the_source(lp, env, tmp_path, capsys):
    commit_files(env.repo_a, {FILE: OPT_IN}, "opt in")
    r = make_receipt(env.repo_a)
    assert gate_alpha(lp, env, tmp_path, r) == 0
    _git(env.repo_a, "add", "-f", ".lean-receipts")
    _git(env.repo_a, "commit", "-m", "evidence")
    commit_files(env.repo_a, {"README.md": "x\n"})
    capsys.readouterr()
    main(lp, env, "--json", "publish", "alpha", "--dry-run")
    data = json.loads(capsys.readouterr().out)
    bound = [c for c in data["checklist"] if c["label"].startswith("gate receipt bound to HEAD")]
    assert bound and bound[0]["status"] == "pass", data["checklist"]
    assert f"opted in at {r['commit'][:12]} by {FILE}" in bound[0]["label"], bound


def _beta_bound_with_file(env, text=OPT_IN):
    """Re-bind beta's gate receipt to a commit that carries the opt-in file."""
    _git(env.repo_b, "branch", "-M", "main")
    commit_files(env.repo_b, {FILE: text}, "opt in")
    r = make_receipt(env.repo_b)
    (env.repo_b / ".lean-receipts" / "seed.json").write_text(json.dumps(r, indent=2))
    return r


def _drift(lp, env, capsys):
    assert main(lp, env, "audit", "--json") == 0
    findings = json.loads(capsys.readouterr().out)["findings"]
    return [f for f in findings if f["name"] == "beta" and f["check"] == "receipt-drift"]


def test_audit_receipt_drift_follows_the_file(lp, env, fake_gh, capsys):
    _beta_bound_with_file(env)
    commit_files(env.repo_b, {"README.md": "docs\n"})
    assert _drift(lp, env, capsys) == []
    commit_files(env.repo_b, {FILE: OPT_IN + "\n"})                   # rule 3
    assert len(_drift(lp, env, capsys)) == 1


def test_audit_receipt_drift_without_the_file(lp, env, fake_gh, capsys):
    _beta_bound_with_file(env, OPT_OUT)
    commit_files(env.repo_b, {"README.md": "docs\n"})
    assert len(_drift(lp, env, capsys)) == 1


# --- the event key is documented ----------------------------------------------------------------------

def test_opt_in_is_a_documented_event_key(lp):
    for ev in ("gate-passed", "regated", "comparator-attached", "comparator-replaced"):
        assert "opt_in" in lp.EVENT_KEYS[ev], ev
