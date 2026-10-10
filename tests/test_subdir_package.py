#!/usr/bin/env python3
"""A package whose local_path is a subdirectory of its git repository (lean/, lean/contrib/)."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import _git, base_pkg, make_repo


def _commit(repo, rel, text="x\n"):
    f = repo / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"edit {rel}")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture(params=["lean", "lean/contrib"])
def sub(request, tmp_path):
    root = make_repo(tmp_path / "paper-repo", extra_files={
        "paper/main.tex": "\\documentclass{article}\n",
        f"{request.param}/lean-toolchain": "leanprover/lean4:v4.32.0\n",
        f"{request.param}/Foo.lean": "theorem foo : True := trivial\n"})
    bound = _git(root, "rev-parse", "HEAD").stdout.strip()
    return root, root / request.param, request.param, bound


def test_evidence_under_the_package_dir_is_evidence(lp, sub):
    root, pkg, prefix, bound = sub
    head = _commit(root, f"{prefix}/.lean-receipts/r.json", "{}\n")
    assert lp.evidence_only_descendant(pkg, bound, head)[0]


def test_changes_outside_the_package_dir_do_not_count(lp, sub):
    root, pkg, prefix, bound = sub
    _commit(root, f"{prefix}/.lean-receipts/r.json", "{}\n")
    head = _commit(root, "paper/main.tex", "\\documentclass{amsart}\n")
    ok, note = lp.evidence_only_descendant(pkg, bound, head)
    assert ok, note
    head2 = _commit(root, "README.md")                      # outside only, no evidence at all
    assert lp.evidence_only_descendant(pkg, bound, head2)[0]


def test_source_change_inside_the_package_dir_is_drift(lp, sub):
    root, pkg, prefix, bound = sub
    head = _commit(root, f"{prefix}/Foo.lean", "theorem foo : 1 = 1 := rfl\n")
    assert not lp.evidence_only_descendant(pkg, bound, head)[0]


def test_repo_root_rule_is_unchanged(lp, tmp_path):
    repo = make_repo(tmp_path / "r")
    bound = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert lp.evidence_only_descendant(repo, bound, _commit(repo, ".lean-receipts/a.json"))[0]
    assert not lp.evidence_only_descendant(repo, bound, _commit(repo, "paper/main.tex"))[0]


def test_audit_no_drift_after_a_paper_edit(lp, sub, tmp_path, fake_gh, capsys):
    root, pkg, prefix, bound = sub
    _git(root, "branch", "-M", "main")
    rel = ".lean-receipts/r.json"
    (pkg / ".lean-receipts").mkdir(exist_ok=True)
    (pkg / rel).write_text(json.dumps({"commit": bound}))
    _commit(root, "paper/main.tex", "\\documentclass{amsart}\n")
    reg = {"version": 2, "packages": {"p": base_pkg("p", pkg, "verified-private",
                                                    gate={"receipt": rel, "comparator": None})}}
    reg_p = tmp_path / "reg.yaml"
    reg_p.write_text(yaml.safe_dump(reg, sort_keys=False))
    assert lp.main(["--registry", str(reg_p), "audit", "--json"]) == 0
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert not [f for f in findings if f["check"] == "receipt-drift"], findings


def test_git_show_bytes_reads_relative_to_the_package_dir(lp, sub):
    """#21.2: a bare rel is the package directory's file, never the repository root's of the same name."""
    root, pkg, prefix, bound = sub
    _commit(root, "comparator.json", '{"at": "root"}\n')
    head = _commit(root, f"{prefix}/comparator.json", '{"at": "package"}\n')
    assert lp.git_show_bytes(pkg, "comparator.json", head) == b'{"at": "package"}\n'
    assert lp.git_show_bytes(pkg, "./comparator.json", head) == b'{"at": "package"}\n'
    assert lp.git_show_bytes(pkg, "Foo.lean", bound) == b"theorem foo : True := trivial\n"
    assert lp.git_show_bytes(pkg, "comparator.json", bound) is None     # not in the package dir yet
    assert lp.git_show_bytes(root, "comparator.json", head) == b'{"at": "root"}\n'
