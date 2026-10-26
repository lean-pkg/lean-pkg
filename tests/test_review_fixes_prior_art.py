#!/usr/bin/env python3
"""Fix pass after the fresh-model review of m2/prior-art (2026-10-06)."""

from __future__ import annotations

import importlib


from .conftest import read_reg, run_search, write_reg
from .test_prior_art import record_file, today


def test_withheld_hits_never_render_as_not_found(lp, searchable, tmp_path):
    """I1: a public target must not say "Not found in registry" when registry hits were withheld."""
    s = searchable
    research = tmp_path / "research"
    research.mkdir()
    assert run_search(lp, s, "foo bound", "--offline", "--repo-dir", str(research), "--record") == 0
    text = record_file(research, today(lp), "foo-bound").read_text()
    assert "Not found in registry" not in text
    assert "0 shown (+1 withheld)" in text


def test_tokens_fold_unicode_and_accept_non_ascii_letters(lp, srch, searchable, capsys):
    """I2: Itô matches Ito; Greek and CJK letters are letters."""
    assert srch.tokens("Itô isometry") == ["ito", "isometry"]
    assert srch.tokens("Black–Scholes PDE") == ["black", "scholes", "pde"]
    s = searchable
    assert run_search(lp, s, "ε", "--offline") == 0
    assert run_search(lp, s, "黎曼", "--offline") == 0
    assert run_search(lp, s, "∫", "--offline") == 2          # no letter or digit anywhere
    assert "letter or digit" in capsys.readouterr().err


def test_repo_dir_outside_the_package_checkout_withholds_private_claims(lp, searchable, tmp_path):
    """I3: the privacy level follows the folder written into, not the --package flag."""
    s = searchable
    research = tmp_path / "research"
    research.mkdir()
    assert read_reg(s.env)["packages"]["alpha"]["visibility"] == "private"
    assert run_search(lp, s, "foo bound", "--offline", "--package", "alpha",
                      "--repo-dir", str(research), "--record") == 0
    text = record_file(research, today(lp), "foo-bound").read_text()
    assert "beta/foo-le-bar" not in text and "- private-claims: no" in text


def test_skip_reasons_carry_no_local_paths(lp, searchable, monkeypatch, capsys):
    """I4: an adapter's error text may name a local file; records and output must not."""
    gh = importlib.import_module("lean_pkg.adapters.github")
    monkeypatch.setattr(gh, "search", lambda term, timeout=10: {
        "state": "skipped", "reason": "open /home/someone/.config/gh/config.yml: no such file",
        "revision": None, "hits": []})
    s = searchable
    assert run_search(lp, s, "foo bound", "--sources", "github", "--package", "beta", "--record") == 0
    out = capsys.readouterr().out
    text = record_file(s.env.repo_b, today(lp), "foo-bound").read_text()
    assert "/home/someone" not in out and "/home/someone" not in text
    assert "<path>" in out


def test_record_refuses_a_dangling_symlink(lp, searchable, tmp_path):
    """Minor re-graded Important: a planted symlink must not send the record elsewhere."""
    s = searchable
    outside = tmp_path / "outside.md"
    rel = f"docs/prior-art/{today(lp)}-foo-bound.md"
    (s.env.repo_b / "docs" / "prior-art").mkdir(parents=True)
    (s.env.repo_b / rel).symlink_to(outside)
    assert run_search(lp, s, "foo bound", "--offline", "--package", "beta", "--record") == 1
    assert not outside.exists()


def test_malformed_registry_entry_is_skipped_not_a_traceback(lp, searchable, capsys):
    s = searchable
    reg = read_reg(s.env)
    reg["packages"]["broken"] = "not a mapping"
    write_reg(s.env, reg)
    assert run_search(lp, s, "foo bound", "--offline") == 0
    out = capsys.readouterr().out
    assert "registry: skipped: unreadable" in out
