#!/usr/bin/env python3
"""Proposal 0002, stage G3 (#319): the on-ramp to the committed opt-in and the deprecation of the registry route.

- `update NAME --write-binding-file` writes lean-pkg-binding.yaml from the record, and every refusal leaves the
  path as it was;
- `new` writes the file, `new --evidence-only` leaves it out;
- `page --write` keeps the registry route (with the WARN) only when the gated commit has no file, and writes
  the file into the working tree, never over an existing path;
- `WARN: deprecated:` and `deprecations[]` appear when the registry route admitted HEAD (gate, gate --check,
  comparator), whenever the opt-in exists only in the record (publish), and for update
  --accept-docs-descendants; a package that uses neither route, or the file, sees none of it."""

from __future__ import annotations

import json
import os

import pytest

from lean_pkg import binding_file

from .conftest import (_git, add_comparator_config, make_comparator_receipt, make_receipt, read_events,
                       read_reg, repo_head, set_lease, write_receipt, write_reg)
from .test_docs_descendants import DOCS, commit_files, gate_alpha, main, opt_in

FILE = binding_file.FILE
WARN = "WARN: deprecated: "


def run(lp, env, capsys, *argv):
    capsys.readouterr()
    rc = main(lp, env, *argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def status_of(repo, rel=FILE) -> str:
    return _git(repo, "status", "--porcelain", "--", rel).stdout.strip()


# --- render ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize("globs", [[], ["data/*.csv", "Verify/strings: x.txt"]])
@pytest.mark.parametrize("comment", [True, False])
def test_render_round_trips_through_the_strict_parse(value, globs, comment):
    text = binding_file.render(value, globs, comment=comment)
    doc, problems = binding_file.parse(text.encode())
    assert problems == [] and doc == {"schema": binding_file.SCHEMA, "docs_descendants": value,
                                      "build_inputs": globs}
    assert text.startswith("# lean-pkg-binding.yaml") is comment


# --- update NAME --write-binding-file ---------------------------------------------------------------------

def test_write_binding_file_from_the_record(lp, env, capsys):
    opt_in(env, build_inputs=["data/*.csv", "extra.txt"])
    before = env.registry.read_text()
    rc, out, err = run(lp, env, capsys, "update", "alpha", "--write-binding-file")
    assert rc == 0 and err == "", err
    path = env.repo_a / FILE
    doc, problems = binding_file.parse(path.read_bytes())
    assert problems == [] and doc["docs_descendants"] is True and doc["build_inputs"] == ["data/*.csv", "extra.txt"]
    assert f"wrote {path}" in out and "not staged, not committed" in out
    assert "commit it in a commit of its own right before the next verify run" in out
    assert env.registry.read_text() == before and read_events(env) == []      # the registry is never written
    assert status_of(env.repo_a) == f"?? {FILE}"                              # never staged


def test_write_binding_file_json(lp, env, capsys):
    opt_in(env)
    rc, out, _err = run(lp, env, capsys, "--json", "update", "alpha", "--write-binding-file")
    data = json.loads(out)
    assert rc == 0 and data["ok"] is True and data["event"] == "binding-file-written" and data["written"] is True
    assert data["state"] == "written" and data["build_inputs"] == [] and data["path"] == str(env.repo_a / FILE)
    assert data["content"] == (env.repo_a / FILE).read_text() and "advice" in data


@pytest.mark.parametrize("value, said", [(None, "the record does not opt in"),
                                         (False, "the record vetoes the docs-only rule")])
def test_write_binding_file_writes_nothing_without_the_record_opt_in(lp, env, capsys, value, said):
    opt_in(env, value)
    rc, out, _err = run(lp, env, capsys, "update", "alpha", "--write-binding-file")
    assert rc == 0 and said in out and "nothing written" in out
    assert not (env.repo_a / FILE).exists()


def test_write_binding_file_identical_file_is_a_no_op(lp, env, capsys):
    opt_in(env)
    assert run(lp, env, capsys, "update", "alpha", "--write-binding-file")[0] == 0
    path = env.repo_a / FILE
    mtime = path.stat().st_mtime_ns
    rc, out, _err = run(lp, env, capsys, "--json", "update", "alpha", "--write-binding-file")
    data = json.loads(out)
    assert rc == 0 and data["event"] == "binding-file-unchanged" and data["written"] is False
    assert path.stat().st_mtime_ns == mtime
    path.write_text("schema: lean-pkg-binding/1\ndocs_descendants: true\n")    # same keys, other bytes
    rc, out, _err = run(lp, env, capsys, "update", "alpha", "--write-binding-file")
    assert rc == 0 and "already says docs_descendants: true" in out
    assert path.read_text() == "schema: lean-pkg-binding/1\ndocs_descendants: true\n"


@pytest.mark.parametrize("existing", ["schema: lean-pkg-binding/1\ndocs_descendants: false\n", "not: yaml: [\n"])
def test_write_binding_file_refuses_a_different_file(lp, env, capsys, existing):
    opt_in(env)
    path = env.repo_a / FILE
    path.write_text(existing)
    rc, out, _err = run(lp, env, capsys, "--json", "update", "alpha", "--write-binding-file")
    data = json.loads(out)
    assert rc == 1 and data["ok"] is False and data["event"] == "binding-file-refused"
    assert "says something else; nothing written" in data["problem"]
    assert path.read_text() == existing


def test_write_binding_file_refuses_a_symlink_and_a_directory(lp, env, capsys):
    opt_in(env)
    path = env.repo_a / FILE
    (env.repo_a / "elsewhere.yaml").write_text("x\n")
    os.symlink("elsewhere.yaml", path)
    rc, out, _err = run(lp, env, capsys, "update", "alpha", "--write-binding-file")
    assert rc == 1 and "is a symlink; nothing written" in out
    assert path.is_symlink() and (env.repo_a / "elsewhere.yaml").read_text() == "x\n"
    path.unlink()
    path.mkdir()
    rc, out, _err = run(lp, env, capsys, "update", "alpha", "--write-binding-file")
    assert rc == 1 and "is not a regular file" in out and path.is_dir()


def test_write_binding_file_dry_run_prints_the_file(lp, env, capsys):
    opt_in(env, build_inputs=["data/*.csv"])
    rc, out, _err = run(lp, env, capsys, "update", "alpha", "--write-binding-file", "--dry-run")
    assert rc == 0 and "would write" in out and "  docs_descendants: true" in out and '  - "data/*.csv"' in out
    assert not (env.repo_a / FILE).exists()


@pytest.mark.parametrize("extra", [["--notes", "n"], ["--path", "elsewhere", "--reason", "r"],
                                   ["--status", "archived", "--reason", "r"], ["--accept-docs-descendants"],
                                   ["--build-inputs", "x"], ["--redetect"]])
def test_write_binding_file_goes_on_its_own(lp, env, capsys, extra):
    opt_in(env)
    before = env.registry.read_text()
    rc, _out, err = run(lp, env, capsys, "update", "alpha", "--write-binding-file", *extra)
    assert rc == 2 and "--write-binding-file writes the package's lean-pkg-binding.yaml on its own" in err
    assert not (env.repo_a / FILE).exists() and env.registry.read_text() == before


def test_write_binding_file_follows_the_lease_rule(lp, env, capsys):
    opt_in(env)
    set_lease(env, "alpha", agent="vera")
    rc, _out, err = run(lp, env, capsys, "update", "alpha", "--write-binding-file")
    assert rc == 1 and "lease held by vera" in err and not (env.repo_a / FILE).exists()
    rc, _out, err = run(lp, env, capsys, "update", "alpha", "--write-binding-file", "--force")
    assert rc == 1 and "--force requires --reason" in err and not (env.repo_a / FILE).exists()
    assert run(lp, env, capsys, "update", "alpha", "--write-binding-file", "--agent", "vera")[0] == 0
    assert (env.repo_a / FILE).is_file()


def test_written_file_committed_then_verified_binds_through_the_file(lp, env, tmp_path, capsys):
    """The migration end to end: write, commit on its own, a new receipt there, a docs commit after it."""
    opt_in(env)
    assert run(lp, env, capsys, "update", "alpha", "--write-binding-file")[0] == 0
    _git(env.repo_a, "add", FILE)
    _git(env.repo_a, "commit", "-q", "-m", "opt in")
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, DOCS)
    capsys.readouterr()
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["opt_in"]["source"] == "committed" and "deprecations" not in data and WARN not in cap.err


# --- the deprecation: gate, gate --check, comparator --------------------------------------------------------

def test_gate_through_the_registry_route_warns_and_lists_the_deprecation(lp, env, tmp_path, capsys):
    opt_in(env)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, DOCS)
    capsys.readouterr()
    assert gate_alpha(lp, env, tmp_path, r) == 0
    err = capsys.readouterr().err
    assert err.count(WARN) == 1
    assert (f"{WARN}alpha: the docs-only rule admitted HEAD through the registry record's docs_descendants: true, "
            f"with no lean-pkg-binding.yaml at {r['commit'][:12]}; replacement: `lean-pkg update alpha "
            f"--write-binding-file`") in err
    assert "removed no earlier than 1.0.0" in err
    ev = read_events(env)[-1]
    assert ev["event"] == "gate-passed" and "deprecations" not in ev and ev["opt_in"]["source"] == "registry"


def test_gate_json_carries_deprecations(lp, env, tmp_path, capsys):
    opt_in(env)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, DOCS)
    capsys.readouterr()
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    [d] = data["deprecations"]
    assert d["id"] == "registry-docs-descendants" and d["earliest_removal"] == "1.0.0"
    assert "lean-pkg update alpha --write-binding-file" in d["replacement"] and "1.0.0" in d["removal_rule"]
    assert cap.err.count(WARN) == 1                                          # stderr, never in stdout's JSON


def test_gate_check_through_the_registry_route_warns(lp, env, tmp_path, capsys):
    opt_in(env)
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, DOCS)
    p = write_receipt(tmp_path, r)
    rc, out, err = run(lp, env, capsys, "--json", "gate", "alpha", "--receipt", str(p), "--check")
    assert rc == 0 and json.loads(out)["deprecations"][0]["id"] == "registry-docs-descendants"
    assert err.count(WARN) == 1


def test_registry_opt_in_without_a_docs_admission_does_not_warn_in_gate(lp, env, tmp_path, capsys):
    """HEAD is the receipt's commit: the docs-only rule admitted nothing, so the gate says nothing about it."""
    opt_in(env)
    r = make_receipt(env.repo_a)
    capsys.readouterr()
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    cap = capsys.readouterr()
    assert "deprecations" not in json.loads(cap.out) and WARN not in cap.err


def test_neither_route_output_has_no_deprecation(lp, env, tmp_path, capsys):
    r = make_receipt(env.repo_a)
    capsys.readouterr()
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    cap = capsys.readouterr()
    assert "deprecations" not in json.loads(cap.out) and WARN not in cap.err


def test_committed_file_admission_does_not_warn(lp, env, tmp_path, capsys):
    opt_in(env)                                                              # a record true too: the file decides
    commit_files(env.repo_a, {FILE: binding_file.render()}, msg="opt in")
    r = make_receipt(env.repo_a)
    commit_files(env.repo_a, DOCS)
    capsys.readouterr()
    assert gate_alpha(lp, env, tmp_path, r, "--json") == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["opt_in"]["source"] == "committed" and "deprecations" not in data and WARN not in cap.err


def _gated_alpha_with_comparator(lp, env, tmp_path, capsys):
    add_comparator_config(env.repo_a)
    r = make_receipt(env.repo_a)
    comp = make_comparator_receipt(env.repo_a)
    assert gate_alpha(lp, env, tmp_path, r) == 0
    commit_files(env.repo_a, {"README.md": "x\n"})
    capsys.readouterr()
    return write_receipt(tmp_path, comp, "comp.json")


@pytest.mark.parametrize("check", [True, False])
def test_comparator_through_the_registry_route_warns(lp, env, tmp_path, capsys, check):
    opt_in(env)
    p = _gated_alpha_with_comparator(lp, env, tmp_path, capsys)
    rc, out, err = run(lp, env, capsys, "--json", "comparator", "alpha", "--receipt", str(p),
                       *(["--check"] if check else []))
    data = json.loads(out)
    assert rc == 0 and data["deprecations"][0]["id"] == "registry-docs-descendants" and err.count(WARN) == 1
    if not check:
        assert "deprecations" not in read_events(env)[-1]


# --- the deprecation: update --accept-docs-descendants ------------------------------------------------------

def test_accept_docs_descendants_is_deprecated(lp, env, capsys):
    rc, out, err = run(lp, env, capsys, "--json", "update", "alpha", "--accept-docs-descendants", "--reason", "r")
    data = json.loads(out)
    assert rc == 0 and data["deprecations"][0]["id"] == "registry-docs-descendants"
    assert err.count(WARN) == 1 and "`update --accept-docs-descendants` sets the registry record's" in err
    assert read_reg(env)["packages"]["alpha"]["docs_descendants"] is True    # it still works


def test_the_veto_is_not_deprecated(lp, env, capsys):
    rc, out, err = run(lp, env, capsys, "--json", "update", "alpha", "--no-accept-docs-descendants",
                       "--reason", "r")
    assert rc == 0 and "deprecations" not in json.loads(out) and WARN not in err


# --- the deprecation: publish --dry-run ------------------------------------------------------------------

def test_publish_warns_while_the_opt_in_is_only_in_the_registry(lp, env, capsys):
    from .test_page import prepare
    prepare(env)                                                             # record true, no file at X
    rc, out, err = run(lp, env, capsys, "--json", "publish", "beta")
    data = json.loads(out)
    assert data["deprecations"][0]["id"] == "registry-docs-descendants" and err.count(WARN) == 1
    items = [c for c in data["checklist"] if "only in the registry record (deprecated)" in c["label"]]
    assert len(items) == 1 and items[0]["status"] == "warn"
    _rc, text, _err = run(lp, env, capsys, "publish", "beta")
    assert "WARN  docs-only opt-in only in the registry record (deprecated)" in text


@pytest.mark.parametrize("how", ["none", "file"])
def test_publish_says_nothing_without_the_registry_only_opt_in(lp, env, capsys, how):
    from .test_page import prepare
    prepare(env, extra={FILE: binding_file.render()} if how == "file" else None)
    if how == "none":
        reg = read_reg(env)
        reg["packages"]["beta"]["docs_descendants"] = None
        write_reg(env, reg)
    rc, out, err = run(lp, env, capsys, "--json", "publish", "beta")
    data = json.loads(out)
    assert "deprecations" not in data and WARN not in err
    assert not any("only in the registry record" in c["label"] for c in data["checklist"])


# --- page --write ------------------------------------------------------------------------------------------

def test_page_write_registry_only_warns_and_writes_the_file(lp, env, capsys):
    from .test_page import prepare
    bound = prepare(env)                                                     # record true, no file at X
    before = env.registry.read_text()
    rc, out, err = run(lp, env, capsys, "--json", "page", "beta", "--write")
    data = json.loads(out)
    assert rc == 0 and err.count(WARN) == 1 and "exists only in the registry record" in err
    assert data["deprecations"][0]["id"] == "registry-docs-descendants"
    assert data["binding_file"] == {"path": str(env.repo_b / FILE), "state": "written", "written": True}
    doc, problems = binding_file.parse((env.repo_b / FILE).read_bytes())
    assert problems == [] and doc["docs_descendants"] is True
    assert status_of(env.repo_b) == f"?? {FILE}"                              # never staged
    assert env.registry.read_text() == before                                # the record already said true
    (env.repo_b / "README.md").write_text("changed\n")
    rc, text, _err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 0 and "is already in the working tree (not overwritten)" in text
    assert f"after {bound[:12]} unbinds the current receipts" in text


def test_page_write_without_a_record_turns_it_on_and_warns_once(lp, env, capsys):
    from .test_page import prepare
    prepare(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = None
    write_reg(env, reg)
    rc, out, err = run(lp, env, capsys, "page", "beta", "--write")
    assert rc == 0 and "turned on the docs-only exception" in out and f"wrote {env.repo_b / FILE}" in out
    assert err.count(WARN) == 1                                              # the update's, not a second one
    assert read_reg(env)["packages"]["beta"]["docs_descendants"] is True


def test_page_write_leaves_the_veto_alone(lp, env, capsys):
    from .test_page import prepare
    prepare(env)
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = False
    write_reg(env, reg)
    rc, out, err = run(lp, env, capsys, "--json", "page", "beta", "--write")
    data = json.loads(out)
    assert rc == 0 and "deprecations" not in data and "binding_file" not in data and WARN not in err
    assert not (env.repo_b / FILE).exists()


@pytest.mark.parametrize("value", [True, False])
def test_page_write_with_the_file_at_the_gated_commit_does_nothing_about_the_opt_in(lp, env, capsys, value):
    from .test_page import prepare
    prepare(env, extra={FILE: binding_file.render(value)})
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = None
    write_reg(env, reg)
    before = env.registry.read_text()
    rc, out, err = run(lp, env, capsys, "--json", "page", "beta", "--write")
    data = json.loads(out)
    assert rc == 0 and "deprecations" not in data and "binding_file" not in data and "docs_descendants" not in data
    assert WARN not in err and env.registry.read_text() == before and status_of(env.repo_b) == ""


def test_page_write_never_overwrites_a_working_tree_file(lp, env, capsys):
    from .test_page import prepare
    prepare(env)
    (env.repo_b / FILE).write_text("schema: lean-pkg-binding/1\ndocs_descendants: false\n")
    rc, out, _err = run(lp, env, capsys, "--json", "page", "beta", "--write")
    data = json.loads(out)
    assert rc == 0 and data["binding_file"]["state"] == "refused" and data["binding_file"]["written"] is False
    assert (env.repo_b / FILE).read_text() == "schema: lean-pkg-binding/1\ndocs_descendants: false\n"


# --- new ------------------------------------------------------------------------------------------------

def _new(lp, tmp_path, capsys, *argv):
    capsys.readouterr()
    rc = lp.main(["--registry", str(tmp_path / "no-registry" / "lean-packages.yaml"), "new", "my-proof",
                  "--dir", str(tmp_path / "my-proof"), "--author", "Ada Lovelace", "--kind", "library", *argv])
    return rc, capsys.readouterr()


def test_new_writes_the_binding_file(lp, tmp_path, capsys):
    rc, cap = _new(lp, tmp_path, capsys, "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["binding_file"] == FILE and FILE in data["files"]
    text = (tmp_path / "my-proof" / FILE).read_text()
    doc, problems = binding_file.parse(text.encode())
    assert problems == [] and doc == {"schema": binding_file.SCHEMA, "docs_descendants": True, "build_inputs": []}
    assert text.startswith("# lean-pkg-binding.yaml: this package's docs-only opt-in")


def test_new_evidence_only_leaves_it_out(lp, tmp_path, capsys):
    rc, cap = _new(lp, tmp_path, capsys, "--evidence-only", "--json")
    data = json.loads(cap.out)
    assert rc == 0 and data["binding_file"] is None and FILE not in data["files"]
    assert not (tmp_path / "my-proof" / FILE).exists()
    rc, cap = _new(lp, tmp_path / "again", capsys, "--evidence-only")
    assert rc == 0 and "no lean-pkg-binding.yaml (--evidence-only)" in cap.out


def test_new_package_first_commit_opts_in_at_its_own_commit(lp, tmp_path, capsys):
    """The scaffold's `git add -A` commits the file with the package: the opt-in is read at that commit."""
    assert _new(lp, tmp_path, capsys)[0] == 0
    repo = tmp_path / "my-proof"
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=A", "-c", "user.email=a@example.org", "commit", "-q", "-m", "new package")
    oi = binding_file.resolve_opt_in(repo, repo_head(repo), None)
    assert oi.rule == "docs-only" and oi.source == "committed"
