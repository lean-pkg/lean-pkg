#!/usr/bin/env python3
"""search: what the declaration fallback finds (namespaces, accents, non-ASCII file names) and how a
local source that did not read everything is reported (file cap, unreadable files or index entries), in
the text, the JSON and the --record page. All offline; every registry and index is a tmp fixture."""

from __future__ import annotations

import json

import pytest

from .conftest import _git, base_pkg, make_repo, run_search


def pkg_repo(tmp_path, files: dict):
    repo = make_repo(tmp_path / "pkgrepo")
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "lean files")
    return repo, base_pkg("p", repo, "gate-pending")


def today(lp):
    return lp.now_iso()[:10]


# --- 1: namespaces --------------------------------------------------------------------------------

def test_a_dotted_query_finds_a_namespaced_declaration(srch, tmp_path):
    _, pkg = pkg_repo(tmp_path, {"Demo/Heat.lean":
                                 "namespace DemoA\n\ntheorem heatFlowMass_le : True := trivial\n\nend DemoA\n"})
    hits, omitted, capped = srch.declaration_hits("DemoA.heatFlowMass_le", "p", pkg)
    assert [h["decl"] for h in hits] == ["DemoA.heatFlowMass_le"] and hits[0]["where"] == "name"
    assert omitted == 0 and capped is None


def test_a_bare_query_still_matches_the_name_as_written(srch, tmp_path):
    _, pkg = pkg_repo(tmp_path, {"Demo/Heat.lean": "namespace DemoA\ntheorem heatFlowMass_le : True := trivial\n"
                                                   "theorem other : True := trivial\nend DemoA\n"})
    assert [h["decl"] for h in srch.declaration_hits("heat flow mass", "p", pkg)[0]] == ["DemoA.heatFlowMass_le"]
    assert srch.declaration_hits("demoa", "p", pkg)[0] == []     # the namespace alone is not a name match


# --- 2: accents and non-ASCII file names ----------------------------------------------------------

@pytest.mark.parametrize("term", ["Itô formula", "ito formula", "ITO FORMULA"])
def test_an_accented_docstring_matches_with_or_without_the_accent(srch, tmp_path, term):
    _, pkg = pkg_repo(tmp_path, {"Demo/S.lean":
                                 "/-- Itô's formula for semimartingales. -/\ntheorem chain_rule : True := trivial\n"})
    assert [h["decl"] for h in srch.declaration_hits(term, "p", pkg)[0]] == ["chain_rule"]


def test_an_accented_query_finds_a_plain_text_docstring(srch, tmp_path):
    _, pkg = pkg_repo(tmp_path, {"Demo/G.lean": "/-- Godel numbering. -/\ndef godelNum : Nat := 0\n"})
    assert [h["decl"] for h in srch.declaration_hits("Gödel numbering", "p", pkg)[0]] == ["godelNum"]


def test_decomposed_accents_in_the_file_match_too(srch, tmp_path):
    _, pkg = pkg_repo(tmp_path, {"Demo/S.lean": "/-- Itô calculus. -/\ndef itoCalc : Nat := 0\n"})
    assert [h["decl"] for h in srch.declaration_hits("Itô calculus", "p", pkg)[0]] == ["itoCalc"]


def test_a_non_ascii_file_name_is_searched(srch, tmp_path):
    _, pkg = pkg_repo(tmp_path, {"Demo/Itô.lean": "theorem gumbel_limit : True := trivial\n"})
    hits, _, capped = srch.declaration_hits("gumbel limit", "p", pkg)
    assert [(h["decl"], h["file"]) for h in hits] == [("gumbel_limit", "Demo/Itô.lean:1")] and capped is None


def test_a_candidate_file_that_cannot_be_read_is_counted_not_skipped(srch, tmp_path, monkeypatch):
    _, pkg = pkg_repo(tmp_path, {"A.lean": "theorem gumbel_a : True := trivial\n",
                                 "B.lean": "theorem gumbel_b : True := trivial\n"})
    real = srch._read_blobs
    monkeypatch.setattr(srch, "_read_blobs", lambda repo, rev, rels: {
        k: v for k, v in real(repo, rev, rels).items() if k != "B.lean"})
    hits, _, capped = srch.declaration_hits("gumbel", "p", pkg)
    assert [h["decl"] for h in hits] == ["gumbel_a"]
    assert capped == {"candidates": 2, "inspected": 1, "unreadable": 1}


def test_unreadable_candidate_files_make_the_registry_incomplete(lp, srch, searchable, monkeypatch, capsys):
    repo_a = searchable.env.repo_a
    (repo_a / "G.lean").write_text("theorem gumbel_limit : True := trivial\n")
    _git(repo_a, "add", "-A")
    _git(repo_a, "commit", "-qm", "g")
    monkeypatch.setattr(srch, "_read_blobs", lambda repo, rev, rels: {})
    assert run_search(lp, searchable, "gumbel", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert p["complete"] is False and "registry" in p["truncated_sources"]
    assert p["coverage"]["declaration_files_capped"] == [
        {"package": "alpha", "candidates": 1, "inspected": 0, "unreadable": 1}]
    assert run_search(lp, searchable, "gumbel", "--offline") == 0
    out = capsys.readouterr().out
    assert "alpha: only 0 of 1 candidate file(s) inspected (1 unreadable), so a zero there is not an absence" in out
    assert "registry: declaration search hit 1 unreadable candidate file(s)" in out


# --- 3: the record page and the registry file cap -----------------------------------------------

def test_the_record_never_says_not_found_in_a_capped_registry(lp, srch, searchable, monkeypatch):
    monkeypatch.setattr(srch, "DECL_FILES_MAX", 0)
    repo_a = searchable.env.repo_a
    (repo_a / "G.lean").write_text("theorem gumbel_limit : True := trivial\n")
    _git(repo_a, "add", "-A")
    _git(repo_a, "commit", "-qm", "g")
    assert run_search(lp, searchable, "gumbel", "--offline", "--package", "beta", "--record") == 0
    text = (searchable.env.repo_b / "docs/prior-art" / f"{today(lp)}-gumbel.md").read_text()
    assert "Not found in registry" not in text
    assert "| registry |" in text and "incomplete: declaration search hit a file cap" in text
    assert "Not fully searched, so a zero there is not an absence: registry (" in text
    assert ("in 1 of them only 0 of 1 candidate file(s) were inspected (file cap), so a zero there is not an "
            "absence.") in text
    assert "alpha" not in text.split("## Candidates")[0].split("## Sources")[1]   # counts only, no names


# --- 4 and 8: unreadable index entries ------------------------------------------------------------

def test_an_unreadable_index_entry_makes_the_search_incomplete(lp, searchable, capsys):
    bad = searchable.index / "broken"
    bad.mkdir()
    (bad / "entry.yaml").write_text("schema: [unclosed\n")
    assert run_search(lp, searchable, "gumbel", "--offline", "--json", "--sources", "registry,index") == 0
    p = json.loads(capsys.readouterr().out)
    b = [x for x in p["sources"] if x["source"] == "index"][0]
    assert b["reason"] == "1 unreadable entry not searched" and b["incomplete"] == b["reason"]
    assert p["complete"] is False and p["truncated_sources"] == ["index"]
    assert run_search(lp, searchable, "gumbel", "--offline", "--sources", "registry,index") == 0
    out = capsys.readouterr().out
    assert "index: 1 unreadable entry not searched" in out and "index (incomplete, 0 hits)" in out


def test_the_record_names_the_unreadable_index_entry(lp, searchable):
    bad = searchable.index / "broken"
    bad.mkdir()
    (bad / "entry.yaml").write_text("schema: [unclosed\n")
    assert run_search(lp, searchable, "gumbel", "--offline", "--package", "beta", "--record") == 0
    text = (searchable.env.repo_b / "docs/prior-art" / f"{today(lp)}-gumbel.md").read_text()
    assert "Not found in registry at" in text and ", index at" not in text
    assert "| index |" in text and "incomplete: 1 unreadable entry not searched" in text
    assert "Not fully searched, so a zero there is not an absence: index (1 unreadable entry not searched)." in text


def test_a_non_utf8_index_entry_is_unreadable_not_a_crash(lp, searchable, capsys):
    bad = searchable.index / "latin1"
    bad.mkdir()
    (bad / "entry.yaml").write_bytes(b"name: caf\xe9\n")
    assert run_search(lp, searchable, "gumbel", "--offline", "--json") == 0
    p = json.loads(capsys.readouterr().out)
    assert [x for x in p["sources"] if x["source"] == "index"][0]["incomplete"] == "1 unreadable entry not searched"


def test_load_entry_names_a_non_utf8_file(lp, tmp_path):
    from lean_pkg import index as idx
    f = tmp_path / "entry.yaml"
    f.write_bytes(b"name: caf\xe9\n")
    with pytest.raises(lp.Fail, match="not UTF-8 text"):
        idx.load_entry(f)


# --- 12: scrub_paths ------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["/scratch/someone/x.lean", "/work/grp/u/run", "/gpfs/home/u/a",
                                  "/srv/data/u", "/mnt/disk1/u/repo", "/tmp/u-job4711/out", "/lustre/fs/u",
                                  "failed: '/scratch/u/x'", "(at /tmp/runner-42/x)"])
def test_scrub_paths_redacts_cluster_and_system_roots(srch, text):
    out = srch.scrub_paths(text)
    assert "<path>" in out and "/u" not in out.replace("<path>", "") and "someone" not in out


@pytest.mark.parametrize("text", ["https://example.org/work/page", "see docs/tmp/notes", "a/srv/b",
                                  "ratio 1/tmp", "/tmp", "/scratch/"])
def test_scrub_paths_leaves_urls_and_relative_paths(srch, text):
    assert srch.scrub_paths(text) == text
