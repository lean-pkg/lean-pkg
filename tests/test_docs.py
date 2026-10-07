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
                 "agents.md", "newcomers.md", "sample-workflow.md", "faq.md", "skills.md", "index-catalog.md", "prior-art.md",
                 "formalization.md",
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
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version      # never hard-code the version here
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-versions.py")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert version in r.stdout
    assert "AI assistance" in (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()


def test_site_code_blocks_get_a_copy_button():
    js = (ROOT / "docs" / "assets" / "site.js").read_text()
    css = (ROOT / "docs" / "assets" / "site.css").read_text()
    assert "lp-copy" in js and "navigator.clipboard" in js and "execCommand" in js   # with a fallback
    assert ".lp-copy" in css and 'html[data-theme="dark"] .lp-copy' in css
def test_faq_has_reservoir_section_and_search_skill_is_listed():
    """Reservoir vs lean-pkg, automatic listing and search-before-building stay documented."""
    faq = (ROOT / "docs" / "faq.md").read_text()
    headings = [line for line in faq.splitlines() if line.startswith("#")]
    assert any("Reservoir" in h for h in headings), headings
    for question in ("How is lean-pkg different from Reservoir?",
                     "Do I need to register my package on Reservoir?",
                     "Should I search Reservoir before formalizing?"):
        assert question in faq, question
    for fact in ("reservoir := false", "/api/v1/packages/", "reservoir-listed", "2 stars",
                 "names* only", "search-before-building"):
        assert fact in faq, fact
    skill = ROOT / "skills" / "search-before-building" / "SKILL.md"
    assert skill.is_file()
    assert "name: search-before-building" in skill.read_text()
    assert "search-before-building/SKILL.md" in (ROOT / "skills" / "README.md").read_text()
    assert "`search-before-building`" in (ROOT / "docs" / "skills.md").read_text()
    assert "search-before-building" in (ROOT / "skills" / "lean-paper-companion" / "SKILL.md").read_text()
    assert "Reservoir" in (ROOT / "docs" / "motivation.md").read_text()


def test_index_catalog_page_is_linked_and_names_the_schema():
    page = (ROOT / "docs" / "index-catalog.md").read_text()
    for needle in ("index-entry/1", "lower-tier", "comparator", "palomar", "computed, never typed",
                   "skipped", "lean-pkg index check --clone"):
        assert needle in page, needle
    assert "index-catalog.md" in (ROOT / "docs" / "index.md").read_text()
    assert "docs/index-catalog.md" in (ROOT / "README.md").read_text()
    assert "index check --clone" in (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    assert "index check --clone" in (ROOT / "CONTRIBUTING.md").read_text()


def test_index_entry_issue_template_uses_schema_field_names():
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "index_entry.md").read_text()
    for key in ("name", "repo", "pin.commit", "pin.tag", "lean_toolchain", "mathlib_rev",
                "license_spdx", "headline", "receipts.lower", "receipts.comparator", "palomar",
                "doi", "reservoir", "formalization_yaml", "fields", "consent.recorded"):
        assert f"`{key}`" in text, key


def test_package_dunder_version_agrees_with_pyproject():
    import tomllib
    import lean_pkg
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert lean_pkg.__version__ == version


def test_prior_art_page_is_linked_and_names_the_contract():
    page = (ROOT / "docs" / "prior-art.md").read_text()
    assert page.startswith("---\ntitle: Prior-art search\n---\n")
    assert "[&larr; lean-pkg documentation](index.md)" in page
    for needle in ("claims/1", "lean-pkg claims sync", "lean-pkg search", "--record", "--offline",
                   "skipped: <reason>", "decision: pending", "require", "reuse", "vendor", "contribute",
                   "build", "prior-art-searched", "private-claims: yes", "Not found in", "index-entry/2",
                   "never the site's name-only box", "30 days"):
        assert needle in page, needle
    assert "prior-art.md" in (ROOT / "docs" / "index.md").read_text()
    assert "docs/prior-art.md" in (ROOT / "README.md").read_text()
    cli = (ROOT / "docs" / "cli.md").read_text()
    for row in ("`claims sync NAME`", "`claims list [NAME]`", "`claims check [NAME]`", "`search TERM`"):
        assert row in cli, row
    assert "lean-pkg search" in (ROOT / "skills" / "search-before-building" / "SKILL.md").read_text()


def test_docs_explain_listing_an_unfinished_project():
    page = (ROOT / "docs" / "index-catalog.md").read_text()
    assert "## An unfinished project as an entry" in page
    for needle in ("what is proved", "notes", "experimental", "new pin"):
        assert needle in page, needle
    faq = (ROOT / "docs" / "faq.md").read_text()
    assert "not finished" in faq and "index-catalog.md#an-unfinished-project-as-an-entry" in faq


def test_boundary_check_history_mode_scans_every_commit(tmp_path):
    """`--history` fails on a forbidden string in ANY reachable blob or commit message, even when
    the current tree is clean (the public flip exposes the whole history, not just HEAD)."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "HOME": str(tmp_path),
           "PATH": __import__("os").environ["PATH"]}
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, env=env, capture_output=True)
    g("init", "-q"); leak = "Le-AI-" + "Lab"
    (repo / "notes.md").write_text(leak + "\n"); g("add", "."); g("commit", "-q", "-m", "first")
    (repo / "notes.md").write_text("clean\n"); g("add", "."); g("commit", "-q", "-m", "scrubbed")
    script = str(ROOT / "scripts" / "private-boundary-check.sh")
    tree = subprocess.run(["bash", script, str(repo)], capture_output=True, text=True)
    assert tree.returncode == 0, tree.stdout + tree.stderr          # HEAD is clean
    hist = subprocess.run(["bash", script, "--history", str(repo)], capture_output=True, text=True)
    assert hist.returncode == 1 and "notes.md" in hist.stdout + hist.stderr
    (repo / "x.md").write_text("ok\n"); g("add", "."); g("commit", "-q", "-m", "mentions " + "tele" + "gram")
    hist2 = subprocess.run(["bash", script, "--history", str(repo)], capture_output=True, text=True)
    assert hist2.returncode == 1 and "commit-message:" in hist2.stdout + hist2.stderr
