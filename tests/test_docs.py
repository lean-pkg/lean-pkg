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


import re
from pathlib import Path

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def _relative_targets(md: Path):
    for m in LINK_RE.finditer(md.read_text()):
        target = m.group(1)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        yield target.split("#", 1)[0]


def test_docs_llms_txt_mirrors_root():
    assert (ROOT / "docs" / "llms.txt").read_text() == (ROOT / "llms.txt").read_text(), \
        "docs/llms.txt must be byte-identical to llms.txt (it is the site's /llms.txt)"


def test_docs_site_has_entry_pages():
    for name in ("index.md", "motivation.md", "getting-started.md", "how-to.md",
                 "lifecycle.md", "receipts.md", "comparator.md", "cli.md",
                 "agents.md", "newcomers.md", "sample-workflow.md", "faq.md", "skills.md",
                 "_config.yml", "llms.txt"):
        assert (ROOT / "docs" / name).is_file(), name


def test_relative_links_resolve():
    broken = []
    pages = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md",
             ROOT / "DESIGN.md", *sorted((ROOT / "docs").glob("*.md"))]
    for md in pages:
        for target in _relative_targets(md):
            if not (md.parent / target).exists():
                broken.append(f"{md.relative_to(ROOT)} -> {target}")
    assert broken == [], "\n".join(broken)


def test_readme_has_motivation_quick_start_and_routes():
    text = (ROOT / "README.md").read_text()
    for heading in ("## Why lean-pkg", "## Who is this for", "## Not Palomar, not Mathlib",
                    "## In sixty seconds", "## Learn the workflow", "docs/newcomers.md",
                    "docs/sample-workflow.md", "docs/faq.md", "Functional Programming in Lean",
                    "Palomar", "llms.txt", "AGENTS.md", "## Citation", "## License", "```bibtex"):
        assert heading in text, heading


def test_site_index_has_citation_and_license():
    text = (ROOT / "docs" / "index.md").read_text()
    assert "## Citation" in text and "```bibtex" in text and "@software" in text
    assert "## License" in text and "Apache-2.0" in text
