#!/usr/bin/env python3
"""Agent front doors stay in sync with the CLI contract."""

from __future__ import annotations

from .conftest import ROOT


def test_llms_txt_is_exactly_the_generated_text(lp):
    """Byte equality, not containment: stale text around the contract fails too (#81)."""
    text = (ROOT / "llms.txt").read_text()
    assert text == lp.llms_txt(), "llms.txt is stale: run `make docs`"


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
                 "formalization.md", "commands/index.md",
                 "_config.yml", "llms.txt"):
        assert (ROOT / "docs" / name).is_file(), name


def test_relative_links_resolve():
    broken = []
    pages = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CONTRIBUTING.md",
             ROOT / "DESIGN.md", ROOT / "GOVERNANCE.md", ROOT / "SECURITY.md",
             ROOT / "MAINTAINERS.md", *sorted((ROOT / "docs").glob("*.md")),
             *sorted((ROOT / "docs" / "proposals").glob("*.md")),
             *sorted((ROOT / "docs" / "commands").glob("*.md"))]
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
    assert "lean-pkg/lean-pkg" in cff["repository-code"]


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
    assert "../assets/workflow.svg" in (ROOT / "docs" / "design" / "two-tracks.md").read_text()
    readme = (ROOT / "README.md").read_text()                     # under the quickstart link, with alt text
    img = re.search(r'<img src="docs/assets/workflow\.svg"[^>]*>', readme)
    assert img and 'alt="One workflow, two tracks' in img.group(0) and "style=" not in img.group(0)
    assert readme.index("docs/quickstart.md") < img.start() < readme.index("## Why lean-pkg")
    text = svg.read_text()
    # both tracks, and every step of the sample workflow, drawn exactly once
    for label in ("Track P · a verified paper", "Track L · a reusable library", "register + claim",
                  "prove · build · audit", "state the claim", "comparator-receipt", "gate",
                  "commit the evidence", "verification page", "promote (dry run)"):
        assert text.count(f">{label}<") == 1, label


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
              ".github/ISSUE_TEMPLATE/field_report.md", ".github/ISSUE_TEMPLATE/proposal.md",
              ".github/PULL_REQUEST_TEMPLATE.md", "docs/decisions/README.md",
              "GOVERNANCE.md", "docs/proposals/README.md", "docs/proposals/0000-template.md",
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


def test_pr_template_asks_for_evidence_not_only_checkboxes():
    """The template asks what ran, how a fix fails on the base revision, and what is left open (#83)."""
    text = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    headings = [line for line in text.splitlines() if line.startswith("#")]
    for h in ("### Commands that actually ran", "### Regression evidence (fixes only)",
              "## Remaining proof gaps", "## Trust boundary"):
        assert h in headings, h
    flat = " ".join(text.split())                                   # immune to re-wrapping
    assert '"not run, because ...", never left out' in flat
    assert 'written "unknown", never "no"' in flat                  # Trust boundary
    assert 'if unsure, write "unknown"' in flat                     # Remaining proof gaps
    contributing = " ".join((ROOT / "CONTRIBUTING.md").read_text().split())
    assert "commands that actually ran" in contributing and "remaining proof gaps" in contributing


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
    from .conftest import cli_reference_text
    cli = cli_reference_text()
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


def test_make_test_uses_a_private_bytecode_cache():
    """Two concurrent `make test` runs must not share pytest's rewritten-bytecode cache."""
    mk = (ROOT / "Makefile").read_text()
    recipe = mk.split("\ntest:\n", 1)[1].split("\n\n", 1)[0]
    assert "PYTHONPYCACHEPREFIX=" in recipe and "mktemp -d" in recipe and "rm -rf" in recipe, recipe
    assert "-p no:cacheprovider" in recipe
    assert "safe to run concurrently" in (ROOT / "RELEASING.md").read_text()


def test_make_test_removes_its_bytecode_cache_on_sigint(tmp_path):
    """Interrupting `make test` (Ctrl-C reaches the whole process group) must not leak the temp dir.

    Reaped with our own WNOHANG loop, not `Popen.wait(timeout)`: under load that call once tripped
    CPython's internal `assert pid == self.pid or pid == 0` (#57). A child already reaped elsewhere
    (ECHILD) counts as gone; a group still alive at the deadline is SIGKILLed and fails the test."""
    import os, shutil, signal, subprocess, time
    import pytest
    make = shutil.which("make")
    if make is None:
        pytest.skip("make not installed")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    marker = tmp_path / "prefix.txt"
    (fakebin / "python3").write_text(f'#!/bin/sh\nprintf %s "$PYTHONPYCACHEPREFIX" > "{marker}"\n'
                                     'mkdir -p "$PYTHONPYCACHEPREFIX/x"\nexec sleep 30\n')
    (fakebin / "python3").chmod(0o755)
    env = dict(os.environ, PATH=f"{fakebin}{os.pathsep}{os.environ['PATH']}")
    # A suite started as `cmd &` from a script (or under nohup) runs with SIGINT ignored, and that is
    # inherited across exec; a shell cannot trap a signal ignored at entry, so the recipe's trap would never
    # run. Give make the default disposition, as a terminal would.
    p = subprocess.Popen([make, "-s", "-C", str(ROOT), "test"], env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    gone = False   # reaped by someone else: ECHILD from our waitpid

    def reap(deadline):
        nonlocal gone
        while p.returncode is None and not gone:
            try:
                pid, sts = os.waitpid(p.pid, os.WNOHANG)
            except ChildProcessError:
                gone = True
                break
            if pid == p.pid:
                p.returncode = os.waitstatus_to_exitcode(sts)
                break
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)
        return True

    try:
        # Ready only once the fake python3 has created its subdirectory: make, the recipe's shell
        # (traps installed before python3 starts) and the leaf are all running in the group.
        ready = time.monotonic() + 15
        prefix = ""
        while time.monotonic() < ready and p.poll() is None:
            if marker.is_file():
                prefix = marker.read_text()
                if prefix and os.path.isdir(os.path.join(prefix, "x")):
                    break
            time.sleep(0.05)
        assert prefix and os.path.isdir(os.path.join(prefix, "x")), \
            f"make test never reached the fake python3 (returncode {p.poll()})"
        time.sleep(0.1)   # let the leaf finish its exec into sleep
        os.killpg(p.pid, signal.SIGINT)
        if not reap(time.monotonic() + 15):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            reap(time.monotonic() + 5)
            pytest.fail("make test still running 15 s after SIGINT to its process group; SIGKILLed")
    finally:
        if p.returncode is None and not gone:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            reap(time.monotonic() + 5)
    assert gone or p.returncode != 0, f"make test exited {p.returncode} after SIGINT"
    for _ in range(40):
        if not os.path.exists(prefix):
            break
        time.sleep(0.05)
    leaked = os.path.exists(prefix)
    if leaked:
        shutil.rmtree(prefix, ignore_errors=True)
    assert not leaked, f"{prefix} leaked after SIGINT"


def test_the_sigint_test_passes_when_sigint_is_ignored_on_entry():
    """Regression: two suites launched as `( make test ) &` from a script ran with SIGINT ignored, and the
    test above then failed with "make test still running 15 s after SIGINT". Run it in a pytest that
    starts with SIGINT ignored, as such a launch does; it must pass."""
    import shutil, signal, subprocess, sys
    import pytest
    if shutil.which("make") is None:
        pytest.skip("make not installed")
    code = ("import signal, sys, pytest; signal.signal(signal.SIGINT, signal.SIG_IGN); "
            "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', "
            "'tests/test_docs.py::test_make_test_removes_its_bytecode_cache_on_sigint']))")
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "1 passed" in p.stdout


def test_copyright_statement_is_the_ipx_confirmed_one_everywhere():
    """Auburn IPX confirmed the notice on 2026-10-08 (decision 0004): every place that
    states lean-pkg's copyright must carry exactly this holder line."""
    holder = "2026 Le Chen and Auburn University"
    assert f"Copyright (c) {holder}" in (ROOT / "LICENSE").read_text()
    assert f"Copyright (c) {holder}" in (ROOT / "NOTICE").read_text()
    assert f'SPDX-FileCopyrightText = "{holder}"' in (ROOT / "REUSE.toml").read_text()
    assert f"Copyright (c) {holder}" in (ROOT / "README.md").read_text()
    for f in ("LICENSE", "NOTICE", "REUSE.toml", "README.md"):
        assert "2026 Le Chen\"" not in (ROOT / f).read_text() and "Copyright 2026 Le Chen\n" not in (ROOT / f).read_text()


def _subcommand_paths(lp):
    """Every subcommand path, top-level and nested, read from the parser `main` builds.

    `main` builds its parser inline; stop it at `parse_args` and walk the subparsers. The
    `comparator` keyword forms (`comparator host-check`, `comparator config`) are values of the
    `name` positional, not subparsers, so they come from the constants `main` dispatches on."""
    import argparse

    class _Stop(Exception):
        pass

    captured = {}

    def stop(self, *args, **kwargs):
        captured["parser"] = self
        raise _Stop

    orig = argparse.ArgumentParser.parse_args
    argparse.ArgumentParser.parse_args = stop
    try:
        lp.main([])
    except _Stop:
        pass
    finally:
        argparse.ArgumentParser.parse_args = orig

    def walk(parser, prefix):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, sub in action.choices.items():
                    yield prefix + (name,)
                    yield from walk(sub, prefix + (name,))

    paths = [" ".join(p) for p in walk(captured["parser"], ())]
    assert "comparator" in paths and "index add" in paths, paths   # the walk itself works
    paths += [f"comparator {lp.HOST_CHECK}", f"comparator {lp.CONFIG_CMD}"]
    return paths


def test_every_subcommand_has_a_cli_reference_entry(lp):
    """Every subcommand, nested ones, aliases and comparator's keyword forms included, is in the CLI
    reference: a section of its generated docs/commands/ page (or an alias line), or a form in its notes."""
    from .conftest import cli_reference_text
    text = cli_reference_text()
    missing = [p for p in _subcommand_paths(lp) if f"lean-pkg {p}" not in text and f"`{p}" not in text]
    assert missing == [], "no CLI reference entry for: " + ", ".join(missing)


def test_every_top_level_subcommand_has_a_front_door_home(lp):
    """Each top-level subcommand is shown as `lean-pkg <cmd>` in README.md or the docs front door
    (docs/index.md, docs/how-to.md): a feature without a front-door home is a documentation gap
    (RELEASING.md, checklist step 0)."""
    text = "\n".join((ROOT / f).read_text() for f in ("README.md", "docs/index.md", "docs/how-to.md"))
    top = sorted({p.split()[0] for p in _subcommand_paths(lp)})
    missing = [c for c in top if not re.search(r"lean-pkg " + re.escape(c) + r"(?![\w-])", text)]
    assert missing == [], "not in README.md, docs/index.md or docs/how-to.md: " + ", ".join(missing)


# Every file `make docs` writes. A new generator joins this list (and the Makefile target) so that CI's
# `make docs` + `git diff --exit-code` covers it; the test below fails when the two disagree (#81).
GENERATED = ("llms.txt", "docs/llms.txt", "index/README.md",
             *sorted(f"docs/commands/{p.name}" for p in (ROOT / "docs" / "commands").glob("*.md")))


def test_make_docs_rewrites_exactly_the_generated_files(tmp_path):
    import os
    import shutil
    import subprocess
    (tmp_path / "docs").mkdir()
    shutil.copy(ROOT / "Makefile", tmp_path / "Makefile")
    (tmp_path / "src").symlink_to(ROOT / "src")
    (tmp_path / "tests").symlink_to(ROOT / "tests")   # the command reference reads the parser and goldens
    shutil.copytree(ROOT / "index", tmp_path / "index")
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "command_docs.py", tmp_path / "scripts")
    shutil.copytree(ROOT / "docs" / "_commands", tmp_path / "docs" / "_commands")
    (tmp_path / "docs" / "commands").mkdir()
    for rel in GENERATED:
        (tmp_path / rel).write_text("stale\n")
    linked = ("src", "tests")
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file() and not set(linked) & set(p.relative_to(tmp_path).parts)}
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run(["make", "-s", "docs"], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    after = {p.relative_to(tmp_path).as_posix(): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file() and not set(linked) & set(p.relative_to(tmp_path).parts)}
    assert after.keys() == before.keys(), "make docs created or removed files"
    assert sorted(k for k in after if after[k] != before[k]) == sorted(GENERATED)
    for rel in GENERATED:
        assert after[rel] == (ROOT / rel).read_bytes(), f"{rel} is stale: run `make docs`"
