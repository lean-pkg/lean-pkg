#!/usr/bin/env python3
"""`lean-pkg page NAME --repo DIR` when DIR's committed HEAD is newer than the commit the recorded receipts
bind (a field report): the source links and Lean statements are read at DIR's HEAD; receipt facts appear
only when the recorded gate receipt validates at that HEAD."""

from __future__ import annotations

import json

import pytest
import yaml

from .conftest import _git, read_reg, repo_head, write_reg
from .test_page import COVERAGE, page_json, prepare, run

NEW_LEAN = """\
namespace Demo

theorem new_bound (n : Nat) : n ≤ n + 1 := by
  omega

end Demo
"""
NEW_CLAIM = {"id": "beta/new-bound", "decl": "Demo.new_bound", "kind": "theorem",
             "summary": "A second demonstration bound.", "file": "Demo/New.lean", "status": "reusable",
             "superseded_by": None, "citation": "Demo 2026, Theorem 3", "depends_on": []}
NEW_ROW = {"paper": {"number": "Theorem 3", "title": "New bound"}, "class": "same-assumptions",
           "claims": ["beta/new-bound"]}


def clone_of(env, tmp_path, name: str = "candidate"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    clone = tmp_path / name
    _git(tmp_path, "clone", "-q", str(env.repo_b), str(clone))
    _git(clone, "remote", "set-url", "origin", "https://github.com/example/beta")
    return clone


def candidate(env, tmp_path, *, commit_new: bool = True):
    """A CI-style clone of beta with one more source commit: its claim and coverage row for Demo/New.lean,
    and the file itself (committed unless commit_new is False, then left in the working tree only)."""
    clone = clone_of(env, tmp_path)
    claims = yaml.safe_load((clone / "claims.yaml").read_text())
    claims["claims"].append(NEW_CLAIM)
    (clone / "claims.yaml").write_text(yaml.safe_dump(claims, sort_keys=False))
    doc = json.loads(json.dumps(COVERAGE))
    doc["rows"].insert(1, NEW_ROW)
    (clone / "coverage.yaml").write_text(yaml.safe_dump(doc, sort_keys=False))
    (clone / "Demo" / "New.lean").write_text(NEW_LEAN)
    paths = ["claims.yaml", "coverage.yaml"] + (["Demo/New.lean"] if commit_new else [])
    _git(clone, "add", *paths)
    _git(clone, "commit", "-q", "-m", "a new result")
    return clone, repo_head(clone)


def block(md: str, heading: str) -> str:
    return md.split(f"### {heading}\n", 1)[1].split("\n### ", 1)[0].split("\n## ", 1)[0]


def test_new_file_at_head_is_anchored_at_head_and_no_stale_receipt_fact(lp, env, capsys, tmp_path):
    bound = prepare(env)
    clone, head = candidate(env, tmp_path)
    rc, a = page_json(lp, env, capsys, "--repo", str(clone))
    md = a["markdown"]
    assert rc == 0, a
    new = block(md, "Theorem 3: New bound")
    assert f"[Demo/New.lean](https://github.com/example/beta/blob/{head}/Demo/New.lean)" in new
    assert "theorem new_bound (n : Nat) : n ≤ n + 1" in new and "not committed" not in new
    old = block(md, "Theorem 1: Main bound")                 # an unchanged file: anchored at HEAD too
    assert f"/blob/{head}/" in old and f"/blob/{bound}/" not in md
    assert a["verified_rows"] == 0 and "**Verified.**" not in md
    # no fact of a receipt that binds another commit
    assert "**Axioms.**" not in md and "Build check (`lean-pkg gate`) at commit" not in md
    assert "Statement comparison (comparator) at commit" not in md and "(used here)" not in md
    assert f"git checkout {bound}" not in md and "**Every proof is checked by Lean.**" not in md
    assert "a second checker confirmed it proves exactly this" not in md
    # the page says which commit it reads and which commit the receipts bind
    assert f"`{head[:12]}`" in md and f"`{bound[:12]}`" in md
    view = a["repo_override"]
    assert view["head"] == head and view["anchor_commit"] == head
    assert view["receipt_facts"]["included"] is False and view["receipt_facts"]["commit"] == bound
    assert any("not bind" in w for w in a["warnings"])
    # the site pages and the coverage table follow the same anchors
    rc, s = page_json(lp, env, capsys, "--repo", str(clone), "--site", str(tmp_path / "site"))
    covmd = (tmp_path / "site" / "coverage.md").read_text()
    assert rc == 0 and f"/blob/{head}/Demo/New.lean" in covmd and f"/blob/{bound}/" not in covmd


def test_uncommitted_content_is_never_read(lp, env, capsys, tmp_path):
    prepare(env)
    clone, _head = candidate(env, tmp_path, commit_new=False)    # the file in the working tree only
    rc, a = page_json(lp, env, capsys, "--repo", str(clone))
    assert rc == 1 and "'Demo/New.lean', which is not committed at HEAD" in a["error"]
    clone2, head = candidate(env, tmp_path / "again")        # committed, then edited in the working tree
    (clone2 / "Demo" / "New.lean").write_text(NEW_LEAN.replace("n ≤ n + 1", "n ≤ n + 2"))
    rc, out, err = run(lp, env, capsys, "--json", "page", "beta", "--repo", str(clone2))
    a = json.loads(out)
    new = block(a["markdown"], "Theorem 3: New bound")
    assert rc == 0 and "n ≤ n + 1" in new and "n + 2" not in new and f"/blob/{head}/Demo/New.lean" in new
    assert any("uncommitted changes to tracked files are not read" in w for w in a["warnings"])


def test_receipt_binding_head_keeps_the_bound_page(lp, env, capsys, tmp_path):
    """DIR at an evidence-only descendant of the bound commit: the page is the registered checkout's page."""
    bound = prepare(env)
    rc, base = page_json(lp, env, capsys)
    clone = clone_of(env, tmp_path, "ci")
    rc2, a = page_json(lp, env, capsys, "--repo", str(clone))
    assert rc == rc2 == 0 and a["markdown"] == base["markdown"] and a["preview"] is False
    assert f"/blob/{bound}/" in a["markdown"] and a["verified_rows"] == 1
    view = a["repo_override"]
    assert view["receipt_facts"]["included"] is True and view["anchor_commit"] == bound
    assert view["head"] == repo_head(clone)


def test_page_and_formalization_render_agree_on_the_receipt(lp, env, capsys, tmp_path):
    """`formalization render --repo` and `page --repo` decide receipt facts by the same rule."""
    prepare(env)
    clone, head = candidate(env, tmp_path)
    rc, a = page_json(lp, env, capsys, "--repo", str(clone))
    rc2, out, _err = run(lp, env, capsys, "--json", "formalization", "render", "beta", "--repo", str(clone))
    f = json.loads(out)
    assert rc == 0 and rc2 == 0
    assert a["repo_override"]["head"] == f["repo_override"]["head"] == head
    assert a["repo_override"]["receipt_facts"]["included"] is f["repo_override"]["receipt_facts"]["included"] \
        is False


def test_check_on_a_preview_names_both_commits_and_the_official_check_passes(lp, env, capsys, tmp_path):
    # The official page stays receipt-bound; a candidate's preview differs from it until the next gate.
    bound = prepare(env)
    (env.repo_b / "README.md").write_text("# Beta\n")
    assert run(lp, env, capsys, "page", "beta", "--write")[0] == 0
    _git(env.repo_b, "add", "README.md")
    _git(env.repo_b, "commit", "-q", "-m", "page")
    assert run(lp, env, capsys, "page", "beta", "--check")[0] == 0
    clone, head = candidate(env, tmp_path)
    rc, out, err = run(lp, env, capsys, "page", "beta", "--check", "--repo", str(clone))
    assert rc == 0, out + err                              # option C: a preview's drift is a warning
    assert "lean-pkg: warning: README.md: the page block differs from this preview" in err, err
    assert f"renders commit {head[:12]}" in err and f"bound to {bound[:12]}" in err
    assert "expected between gate runs, not a failed gate" in err and "the page block is stale" not in out + err
    assert f"page: preview of commit {head[:12]}" in out and "STALE" not in out
    rc, out, err = run(lp, env, capsys, "page", "beta", "--check", "--repo", str(clone), "--strict-preview")
    assert rc == 1 and "the page block differs from this preview" in out and "STALE" in out, out + err
    rc, a = page_json(lp, env, capsys, "--check")
    assert rc == 0 and a["preview"] is False and a["current"] is True  # the official page is unaffected


# --- a receipt that binds HEAD but does not validate is a failed check, never a preview -------------------

def break_receipt(env, **fields) -> str:
    """Rewrite the recorded gate receipt with `fields` and commit it (an evidence-only change, so it still binds
    the new HEAD); the receipt's path."""
    rel = read_reg(env)["packages"]["beta"]["gate"]["receipt"]
    p = env.repo_b / rel
    rec = json.loads(p.read_text())
    rec.update(fields)
    p.write_text(json.dumps(rec, indent=2))
    _git(env.repo_b, "add", "-f", rel)
    _git(env.repo_b, "commit", "-q", "-m", "a broken receipt")
    return rel


def set_docs_descendants(env, value: bool) -> None:
    reg = read_reg(env)
    reg["packages"]["beta"]["docs_descendants"] = value
    write_reg(env, reg)


def write_official_page(lp, env, capsys, *extra) -> None:
    (env.repo_b / "README.md").write_text("# Beta\n")
    assert run(lp, env, capsys, "page", "beta", "--write", *extra)[0] == 0
    # page --write also leaves lean-pkg-binding.yaml in the working tree (proposal 0002, stage G3): as its
    # advice says, it is committed before the next verify run, never with the page
    _git(env.repo_b, "add", "-A", "--", ".", ":!lean-pkg-binding.yaml")
    _git(env.repo_b, "commit", "-q", "-m", "page")


INVALID = {
    "sorry_count": ({"sorry_count": 1}, "sorry_count must be 0, got 1"),
    "axioms": ({"axioms": ["propext", "Quot.sound", "Classical.choice", "Lean.ofReduceBool"]},
               "outside allowed set"),
    "toolchain": ({"lean_toolchain": "leanprover/lean4:v9.9.9"}, "toolchain mismatch"),
    "mathlib": ({"mathlib_rev": "0" * 40}, "mathlib mismatch"),
}


@pytest.mark.parametrize("docs_descendants", [True, False])
@pytest.mark.parametrize("cls", sorted(INVALID))
def test_receipt_binding_head_but_invalid_is_a_failed_check(lp, env, capsys, tmp_path, cls, docs_descendants):
    bound = prepare(env)
    set_docs_descendants(env, docs_descendants)
    fields, why = INVALID[cls]
    rel = break_receipt(env, **fields)
    clone = clone_of(env, tmp_path, "ci")
    rc, a = page_json(lp, env, capsys, "--repo", str(clone))
    md = a["markdown"]
    assert rc == 0, a
    rf = a["repo_override"]["receipt_facts"]
    assert rf["included"] is False and rf["binds_head"] is True and why in rf["reason"], rf
    assert a["repo_override"]["anchor_commit"] == bound and f"/blob/{bound}/" in md
    # said as a failed check, never as a preview between gate runs
    assert "**The recorded build check does not validate.**" in md and rel in md
    assert why.replace("_", "\\_") in md                 # the reason, Markdown-escaped
    assert "Preview of commit" not in md and "No check binds this commit" not in md
    assert a["verified_rows"] == 0 and "**Verified.**" not in md
    assert "**Axioms.**" not in md and "Build check (`lean-pkg gate`) at commit" not in md
    assert "Statement comparison (comparator) at commit" not in md and "(used here)" not in md
    assert not any("do not bind it" in w for w in a["warnings"])
    assert any("a failed check, not drift between gate runs" in w for w in a["warnings"])


@pytest.mark.parametrize("strict", [False, True])
def test_check_on_an_invalid_receipt_is_never_called_drift(lp, env, capsys, tmp_path, monkeypatch, strict):
    monkeypatch.chdir(tmp_path)                            # --site docs: a relative path, kept in tmp_path
    prepare(env)
    break_receipt(env, sorry_count=2)
    write_official_page(lp, env, capsys, "--site", "docs")
    clone = clone_of(env, tmp_path, "ci")
    flags = ["--strict-preview"] if strict else []
    rc, a = page_json(lp, env, capsys, "--check", "--repo", str(clone), "--site", "docs", *flags)
    assert rc == 1 and a["ok"] is False and a["preview"] is False and a["current"] is False, a
    rc, out, err = run(lp, env, capsys, "page", "beta", "--check", "--repo", str(clone), "--site", "docs", *flags)
    said = out + err
    assert rc == 1 and "STALE" in out, said                # a failed check: exit 1 with or without the flag
    assert "binds that commit but does not validate (" in said and "sorry_count must be 0, got 2" in said
    assert "a failed check, not drift between gate runs" in said
    assert "expected between gate runs" not in said and "differs from this preview" not in said
    assert "README.md: the page block differs" in said and "docs/verification.md differs" in said
    assert "is stale (`lean-pkg page" not in said


@pytest.mark.parametrize("strict", [False, True])
def test_check_on_a_preview_stale_readme_site_and_html(lp, env, capsys, tmp_path, monkeypatch, strict):
    """Option C: the differences of a preview are warnings (exit 0, preview true, current null);
    --strict-preview makes them problems again (exit 1)."""
    monkeypatch.chdir(tmp_path)                            # --site docs: a relative path, kept in tmp_path
    bound = prepare(env)
    write_official_page(lp, env, capsys, "--site", "docs", "--html", "docs/verification.html")
    clone, head = candidate(env, tmp_path)
    rc, a = page_json(lp, env, capsys, "--check", "--repo", str(clone), "--site", "docs",
                      "--html", "docs/verification.html", *(["--strict-preview"] if strict else []))
    assert a["preview"] is True and a["current"] is None, a
    stale = {f["path"] for f in a["site_files"] if f["status"] == "stale"}
    assert stale == {"docs/verification.md", "docs/coverage.md", "docs/verification.html"}, a["site_files"]
    said = a["problems"] if strict else a["warnings"]
    if strict:
        assert rc == 1 and a["ok"] is False
    else:
        assert rc == 0 and a["ok"] is True and a["problems"] == []
    for shown in sorted(stale) + ["README.md: the page block"]:
        hit = [p for p in said if p.startswith(f"{shown} differs from this preview")]
        assert len(hit) == 1, said
        assert f"renders commit {head[:12]}" in hit[0] and f"bound to {bound[:12]}" in hit[0]
        assert "expected between gate runs, not a failed gate" in hit[0]
    assert not any("is stale (" in p for p in a["problems"] + a["warnings"])


@pytest.mark.parametrize("problem", ["no block", "no credit"])
def test_a_real_problem_under_a_preview_still_fails(lp, env, capsys, tmp_path, problem):
    prepare(env)
    if problem == "no block":
        (env.repo_b / "README.md").write_text("# Beta\n")
        _git(env.repo_b, "add", "README.md")
        _git(env.repo_b, "commit", "-q", "-m", "readme")
    else:
        write_official_page(lp, env, capsys)
        readme = env.repo_b / "README.md"
        readme.write_text(readme.read_text().replace("https://github.com/lean-pkg/lean-pkg", "lean-pkg"))
        _git(env.repo_b, "commit", "-q", "-am", "no credit")
    clone, _head = candidate(env, tmp_path)
    rc, a = page_json(lp, env, capsys, "--check", "--repo", str(clone))
    want = "has no page block" if problem == "no block" else "lacks the lean-pkg credit"
    assert rc == 1 and a["ok"] is False and a["preview"] is True and a["current"] is None, a
    assert any(want in p for p in a["problems"]), a["problems"]
    assert not any("differs from this preview" in p for p in a["problems"])     # drift stays a warning


def test_strict_preview_needs_check_and_repo(lp, env, capsys, tmp_path):
    prepare(env)
    clone = clone_of(env, tmp_path, "ci")
    for argv in (["--check", "--strict-preview"], ["--repo", str(clone), "--strict-preview"],
                 ["--strict-preview"]):
        rc, out, err = run(lp, env, capsys, "page", "beta", *argv)
        assert rc == 2 and "--strict-preview applies to `page --check --repo DIR` only" in out + err, argv


def test_docs_only_descendant_needs_the_opt_in(lp, env, capsys, tmp_path):
    """A docs-only commit after the evidence: with docs_descendants the receipt still binds HEAD (the page is
    the bound one); without it the receipt does not bind HEAD, so --repo renders a preview, not a failure."""
    bound = prepare(env)
    (env.repo_b / "NOTES.md").write_text("Some notes.\n")
    _git(env.repo_b, "add", "NOTES.md")
    _git(env.repo_b, "commit", "-q", "-m", "docs")
    clone = clone_of(env, tmp_path, "ci")
    head = repo_head(clone)
    rc, a = page_json(lp, env, capsys, "--repo", str(clone))
    rf = a["repo_override"]["receipt_facts"]
    assert rc == 0 and rf["included"] is True and rf["binds_head"] is True and a["verified_rows"] == 1
    set_docs_descendants(env, False)
    rc, a = page_json(lp, env, capsys, "--repo", str(clone))
    rf = a["repo_override"]["receipt_facts"]
    assert rc == 0 and rf["included"] is False and rf["binds_head"] is False, rf
    assert a["repo_override"]["anchor_commit"] == head and f"Preview of commit `{head[:12]}`" in a["markdown"]
    assert f"`{bound[:12]}`" in a["markdown"] and "does not validate" not in a["markdown"]
