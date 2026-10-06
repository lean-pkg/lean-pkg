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


def test_no_inline_code_spans_cross_lines():
    bad = []
    for md in [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md",
               ROOT / "DESIGN.md", *sorted((ROOT / "docs").glob("*.md"))]:
        in_fence = False
        for n, line in enumerate(md.read_text().split("\n"), 1):
            if line.strip().startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence and line.count("`") % 2:
                bad.append(f"{md.relative_to(ROOT)}:{n}")
    assert bad == [], "inline code spans must not cross lines (GitHub renders them badly): " + ", ".join(bad)


def test_citation_cff_matches_pyproject():
    import tomllib, yaml
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    cff = yaml.safe_load((ROOT / "CITATION.cff").read_text())
    assert cff["version"] == version and cff["license"] == "Apache-2.0"
    assert "chenle02/lean-pkg" in cff["repository-code"]


def test_site_has_search_and_theme_toggle():
    head = (ROOT / "docs" / "_includes" / "head-custom.html").read_text()
    assert "assets/site.css" in head and "assets/site.js" in head and "prefers-color-scheme" in head
    assert (ROOT / "docs" / "search.json").read_text().startswith("---\nlayout: null")
    js = (ROOT / "docs" / "assets" / "site.js").read_text()
    assert "search.json" in js and "data-theme" in js
    css = (ROOT / "docs" / "assets" / "site.css").read_text()
    assert 'html[data-theme="dark"]' in css


def test_workflow_diagram_exists_and_is_valid_svg():
    import xml.dom.minidom
    svg = ROOT / "docs" / "assets" / "workflow.svg"
    xml.dom.minidom.parse(str(svg))
    assert "assets/workflow.svg" in (ROOT / "docs" / "sample-workflow.md").read_text()


def test_release_files_agree_and_release_script_refuses_bad_input():
    import subprocess
    out = subprocess.run(["python3", str(ROOT / "scripts" / "check-versions.py")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("versions: ")
    text = (ROOT / "CHANGELOG.md").read_text()
    assert "## [Unreleased]" in text and "## [0.1.0] - 2026-10-05" in text
    assert (ROOT / "RELEASING.md").is_file() and (ROOT / "Makefile").read_text().startswith("#")
    assert "\t" not in (ROOT / "Makefile").read_text()
    bad = subprocess.run(["bash", str(ROOT / "scripts" / "release.sh"), "not-a-version"], capture_output=True, text=True)
    assert bad.returncode == 2 and "usage" in bad.stderr


def test_boundary_check_scans_only_files_git_would_ship(tmp_path):
    """Git-ignored scratch (worktree ledgers, caches) is not shipped, so it must not fail the check;
    an uncommitted but not-ignored file is shipped by the next commit, so it must."""
    import subprocess
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(ROOT), str(clone)], check=True)
    leak = "Le-AI-" + "Lab"                      # a forbidden string, assembled so this file stays clean
    with (clone / ".gitignore").open("a") as f:
        f.write("scratch/\n")
    (clone / "scratch").mkdir()
    (clone / "scratch" / "ledger.md").write_text(leak + "\n")
    script = str(ROOT / "scripts" / "private-boundary-check.sh")
    ok = subprocess.run(["bash", script, str(clone)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    (clone / "notes.md").write_text(leak + "\n")
    bad = subprocess.run(["bash", script, str(clone)], capture_output=True, text=True)
    assert bad.returncode == 1 and "notes.md" in bad.stdout + bad.stderr


def test_governance_files_exist_and_agree():
    import subprocess, sys, tomllib
    for f in ("CHANGELOG.md", "SECURITY.md", "CITATION.cff", "MAINTAINERS.md", "Makefile",
              "RELEASING.md", "scripts/check-versions.py", "scripts/release.sh",
              ".githooks/pre-push",
              ".github/ISSUE_TEMPLATE/bug_report.md", ".github/ISSUE_TEMPLATE/index_entry.md",
              ".github/PULL_REQUEST_TEMPLATE.md", "docs/decisions/README.md",
              "docs/decisions/0001-files-and-git-only.md",
              "docs/decisions/0002-benevolent-maintainer-with-deputies.md"):
        assert (ROOT / f).is_file(), f
    assert not (ROOT / "docs" / "release.md").exists()  # RELEASING.md is the runbook
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert version == "0.2.0"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-versions.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "0.2.0" in r.stdout
    assert "AI assistance" in (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()


def test_site_code_blocks_get_a_copy_button():
    js = (ROOT / "docs" / "assets" / "site.js").read_text()
    css = (ROOT / "docs" / "assets" / "site.css").read_text()
    assert "lp-copy" in js and "navigator.clipboard" in js and "execCommand" in js   # with a fallback
    assert ".lp-copy" in css and 'html[data-theme="dark"] .lp-copy' in css
