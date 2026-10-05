#!/usr/bin/env python3
"""Agent front doors stay in sync with the CLI contract."""

from __future__ import annotations

from .conftest import ROOT


def test_llms_txt_contains_current_contract(lp):
    text = (ROOT / "llms.txt").read_text()
    assert lp.LLMS_TEXT in text, "llms.txt is stale: regenerate from `lean-pkg llms`"


def test_agents_md_points_to_contract():
    text = (ROOT / "AGENTS.md").read_text()
    assert "lean-pkg llms" in text and "pytest" in text
