#!/usr/bin/env python3
"""scripts/release-version.py: the version a release must carry (RELEASING.md "When to tag"), derived from
the in-progress upgrade record and the `## [Unreleased]` headings; the CHANGELOG category check; the
record's provisional name; the history audit. Temporary git repositories only."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent


def _module():
    spec = importlib.util.spec_from_file_location("release_version_under_test", ROOT / "scripts" / "release-version.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _module()


def record(changes=(), nac=(), version="0.19.0"):
    return rv.Record(Path(f"docs/upgrades/{version}.yaml"), version, list(changes), list(nac))


def change(pr, kind, changelog, id_=None):
    return {"pr": pr, "id": id_ or f"c{pr}", "kind": kind, "changelog": changelog}


def entries(body: str):
    return rv.parse_entries(body)


# --------------------------------------------------------------------------
# the bump rule


def test_a_command_entry_makes_a_minor():
    e, h = entries("### Added\n\n- `lean-pkg frob`. (#5)\n\n### Fixed\n\n- a fix. (#6)\n")
    v = rv.derive(e, h, record([change(5, "command", "Added"), change(6, "behavior", "Fixed")]))
    assert v.problems == [] and v.bump == "minor" and v.reasons == ["#5 c5: kind command"]
    assert rv.next_version("0.18.0", v.bump) == "0.19.0"


def test_a_format_entry_makes_a_minor_even_under_changed_or_fixed():
    for heading in ("Changed", "Fixed"):
        e, h = entries(f"### {heading}\n\n- receipts: format 3, with a migration. (#7)\n")
        v = rv.derive(e, h, record([change(7, "format", heading)]))
        assert v.problems == [] and v.bump == "minor" and v.reasons == ["#7 c7: kind format"], heading


def test_a_requirement_entry_and_a_behavior_entry_under_added_make_a_minor():
    e, h = entries("### Changed\n\n- Python 3.12 is the floor. (#3)\n")
    assert rv.derive(e, h, record([change(3, "requirement", "Changed")])).bump == "minor"
    e, h = entries("### Added\n\n- `--fields` on status. (#4)\n")
    v = rv.derive(e, h, record([change(4, "behavior", "Added")]))
    assert v.bump == "minor" and "a new user-visible flag, field or feature" in v.reasons[0]


def test_fixes_and_docs_only_make_a_patch():
    e, h = entries("### Fixed\n\n- a fix. (#6)\n\n### Changed\n\n- release tooling. (#8)\n\n"
                   "### Documentation\n\n- a recipe. (#9)\n")
    v = rv.derive(e, h, record([change(6, "behavior", "Fixed")], nac=[8, 9]))
    assert v.problems == [] and v.bump == "patch"
    assert "no_author_change: #8, #9" in v.reasons and "CHANGELOG `### Documentation`: 1 entry" in v.reasons
    assert rv.next_version("0.18.0", v.bump) == "0.18.1"
    assert rv.derive(e, h, record([change(6, "behavior", "Fixed")], nac=[8, 9]), milestone="M2").bump == "minor"


# --------------------------------------------------------------------------
# the category check: the record and the CHANGELOG must agree


def test_a_no_author_change_entry_under_added_is_refused():
    e, h = entries("### Added\n\n- docs/recipes.md, a recipe. (#9)\n\n### Fixed\n\n- a fix. (#6)\n")
    v = rv.derive(e, h, record([change(6, "behavior", "Fixed")], nac=[9]))
    assert len(v.problems) == 1
    assert ('`### Added` entry "docs/recipes.md, a recipe. (#9)" is from #9, listed under no_author_change in '
            "docs/upgrades/0.19.0.yaml: a docs-only entry goes under `### Documentation`") in v.problems[0]
    assert v.bump == "patch"      # the entry is not counted; the refusal is what stops the release


def test_record_and_changelog_headings_must_agree():
    # a record entry naming a heading [Unreleased] lacks
    e, h = entries("### Fixed\n\n- a fix. (#6)\n")
    v = rv.derive(e, h, record([change(6, "behavior", "Fixed"), change(5, "command", "Added")]))
    assert any("entry #5 c5 says changelog: Added, but `## [Unreleased]` has no `### Added` entry" in p
               for p in v.problems)
    # an ### Added entry no record entry backs (never guessed: it may be a docs entry or a missing record entry)
    e, h = entries("### Added\n\n- something new.\n\n### Fixed\n\n- a fix. (#6)\n")
    v = rv.derive(e, h, record([change(6, "behavior", "Fixed")]))
    assert any("`### Added` has 1 entry (\"something new.\") but no entry of docs/upgrades/0.19.0.yaml says "
               "changelog: Added" in p for p in v.problems)
    # an unknown heading, and no record at all
    e, h = entries("### Docs\n\n- a page.\n")
    assert any("unknown CHANGELOG heading `### Docs`" in p for p in rv.derive(e, h, record(nac=[1])).problems)
    assert any("no in-progress upgrade record" in p for p in rv.derive(e, [], None).problems)


def test_parse_entries_keeps_continuations_and_skips_paragraphs():
    e, h = entries("### Added\n\n- one,\n  continued (#3).\n- two.\n\npublic-safety (make ...): 0 hit(s)\n\n"
                   "### Fixed\n\n- three. (#4)\n")
    assert h == ["Added", "Fixed"] and [x.heading for x in e] == ["Added", "Added", "Fixed"]
    assert e[0].text == "- one,\n  continued (#3)." and e[0].prs == {3} and e[1].prs == set() and e[2].prs == {4}


# --------------------------------------------------------------------------
# a repository: git attribution, --check, --check-version, --rename


def _git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "T",
                "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.org"})
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


CHANGELOG0 = "# Changelog\n\n## [Unreleased]\n\n## [0.18.0] - 2026-10-18\n\n### Added\n\n- old.\n"
PAGE = """---
title: Upgrading to 0.19.0
---

# Upgrading to 0.19.0 (from 0.18.0)

The record is [0.19.0.yaml](0.19.0.yaml), filled in until 0.19.0 is cut; lean-pkg 0.19.01 is not a version.
"""


def _write(repo: Path, files: dict[str, str]) -> None:
    for k, v in files.items():
        (repo / k).parent.mkdir(parents=True, exist_ok=True)
        (repo / k).write_text(v)


def _commit(repo: Path, subject: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", subject)


def _unreleased(repo: Path, body: str) -> None:
    (repo / "CHANGELOG.md").write_text(CHANGELOG0.replace("## [Unreleased]\n\n", f"## [Unreleased]\n\n{body}\n"))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "lp"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, {"pyproject.toml": '[project]\nname = "lean-pkg"\nversion = "0.18.0"\n', "CHANGELOG.md": CHANGELOG0})
    _commit(r, "release: 0.18.0")
    _git(r, "tag", "-a", "v0.18.0", "-m", "0.18.0")
    rec = {"schema": "lean-pkg-upgrade-record/1", "version": "0.19.0", "changes": [], "no_author_change": [10]}
    _write(r, {"docs/upgrades/0.19.0.yaml": "# the page is docs/upgrades/0.19.0.md\n" + yaml.safe_dump(rec, sort_keys=False),
               "docs/upgrades/0.19.0.md": PAGE})
    _unreleased(r, "### Documentation\n\n- docs/a.md: a page.\n")
    _commit(r, "[A] docs: a page (#10)")
    return r


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "release-version.py"), "--repo", str(repo), *args],
                          capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


def test_release_version_prints_the_next_version_and_the_provisional_record(repo):
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    assert 'the next release is 0.18.1 (patch over 0.18.0; RELEASING.md "When to tag")' in r.stdout
    assert "no_author_change: #10" in r.stdout
    assert ("docs/upgrades/0.19.0.yaml carries the provisional name 0.19.0; `make cut-release VERSION=0.18.1` "
            "renames it to docs/upgrades/0.18.1.yaml") in r.stdout
    assert _run(repo, "--bare").stdout == "0.18.1\n"
    assert _run(repo, "--bare", "--milestone", "M1").stdout == "0.19.0\n"
    assert _run(repo, "--check-version", "0.18.1").returncode == 0
    r = _run(repo, "--check-version", "0.19.0")
    assert r.returncode == 1 and "refusing 0.19.0" in r.stderr and "requires 0.18.1" in r.stderr
    assert _run(repo, "--check-version", "1.0.0").returncode == 0      # a major: the maintainer's decision


def test_check_attributes_an_entry_through_the_squash_commit(repo):
    # #11's entry cites no PR number; the squash commit that added its first line says it is #11's
    rec = yaml.safe_load((repo / "docs/upgrades/0.19.0.yaml").read_text())
    rec["no_author_change"].append(11)
    _write(repo, {"docs/upgrades/0.19.0.yaml": yaml.safe_dump(rec, sort_keys=False)})
    _unreleased(repo, "### Added\n\n- docs/b.md: a second page, which\n  continues.\n\n"
                      "### Documentation\n\n- docs/a.md: a page.\n")
    _commit(repo, "[B] docs: a second page (#11)")
    r = _run(repo, "--check")
    assert r.returncode == 1 and "disagree" in r.stderr
    assert ('`### Added` entry "docs/b.md: a second page, which" is from #11, listed under no_author_change in '
            "docs/upgrades/0.19.0.yaml") in r.stderr
    # moved to ### Documentation, the check passes and the release stays a patch
    _unreleased(repo, "### Documentation\n\n- docs/b.md: a second page, which\n  continues.\n- docs/a.md: a page.\n")
    _commit(repo, "changelog: move #11's entry")
    assert _run(repo, "--check").returncode == 0 and _run(repo, "--bare").stdout == "0.18.1\n"


def test_check_passes_with_nothing_unreleased(tmp_path):
    r = tmp_path / "lp"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, {"pyproject.toml": '[project]\nname = "lean-pkg"\nversion = "0.18.0"\n', "CHANGELOG.md": CHANGELOG0})
    _commit(r, "release: 0.18.0")
    p = _run(r, "--check")
    assert p.returncode == 0 and "nothing unreleased" in p.stdout


def test_rename_moves_both_files_and_rewrites_the_version(repo):
    r = _run(repo, "--rename", "0.18.1")
    assert r.returncode == 0, r.stderr
    up = repo / "docs" / "upgrades"
    assert sorted(p.name for p in up.iterdir()) == ["0.18.1.md", "0.18.1.yaml"]
    rec = (up / "0.18.1.yaml").read_text()
    assert yaml.safe_load(rec)["version"] == "0.18.1" and "docs/upgrades/0.18.1.md" in rec and "0.19.0" not in rec
    page = (up / "0.18.1.md").read_text()
    assert "title: Upgrading to 0.18.1\n" in page and "# Upgrading to 0.18.1 (from 0.18.0)" in page
    assert "[0.18.1.yaml](0.18.1.yaml)" in page and "until 0.18.1 is cut" in page
    assert "0.19.01 is not a version" in page                 # only the whole version string is replaced
    assert "R" in _git(repo, "status", "--porcelain")          # staged as a rename
    assert "already named for 0.18.1" in _run(repo, "--rename", "0.18.1").stdout
    assert _run(repo, "--check-version", "0.18.1").returncode == 0


def test_two_unreleased_records_or_a_misnamed_one_are_refused(repo):
    rec = {"schema": "lean-pkg-upgrade-record/1", "version": "0.20.0", "changes": [], "no_author_change": [12]}
    _write(repo, {"docs/upgrades/0.20.0.yaml": yaml.safe_dump(rec)})
    r = _run(repo, "--check")
    assert r.returncode == 1 and "more than one record is above the current version 0.18.0" in r.stderr
    (repo / "docs/upgrades/0.20.0.yaml").rename(repo / "docs/upgrades/0.17.9.yaml")
    r = _run(repo, "--check")
    assert r.returncode == 1 and "the file name must be the record's version (0.20.0.yaml)" in r.stderr


# --------------------------------------------------------------------------
# the history audit


def test_history_audit_computes_each_release_from_its_section_record_and_range(repo):
    # 0.19.0 is cut from fixes and docs only: the rule says 0.18.1, so the audit reports it
    _unreleased(repo, "### Added\n\n- docs/a.md: a page.\n\n### Fixed\n\n- a fix. (#13)\n")
    rec = {"schema": "lean-pkg-upgrade-record/1", "version": "0.19.0",
           "changes": [change(13, "behavior", "Fixed") | {"summary": "s", "action": "a", "breaking": False}],
           "no_author_change": [10]}
    _write(repo, {"docs/upgrades/0.19.0.yaml": yaml.safe_dump(rec)})
    _commit(repo, "[C] a fix (#13)")
    chg = (repo / "CHANGELOG.md").read_text().replace("## [Unreleased]\n\n", "## [Unreleased]\n\n## [0.19.0] - 2026-10-19\n\n")
    (repo / "CHANGELOG.md").write_text(chg)
    _commit(repo, "release: 0.19.0")
    _git(repo, "tag", "-a", "v0.19.0", "-m", "0.19.0")
    # 0.20.0 holds a command: a minor, as cut
    rec2 = {"schema": "lean-pkg-upgrade-record/1", "version": "0.20.0",
            "changes": [change(14, "command", "Added") | {"summary": "s", "action": "a", "breaking": False}]}
    _write(repo, {"docs/upgrades/0.20.0.yaml": yaml.safe_dump(rec2)})
    chg = (repo / "CHANGELOG.md").read_text().replace("## [Unreleased]\n\n",
                                                      "## [Unreleased]\n\n## [0.20.0] - 2026-10-20\n\n"
                                                      "### Added\n\n- `lean-pkg frob`. (#14)\n\n")
    (repo / "CHANGELOG.md").write_text(chg)
    _commit(repo, "[D] frob (#14)")
    _git(repo, "tag", "-a", "v0.20.0", "-m", "0.20.0")
    rows = {a.version: a for a in rv.audit(repo, "0.1.0")}
    assert set(rows) == {"0.19.0", "0.20.0"}
    a = rows["0.19.0"]
    assert (a.actual, a.required, a.should_be, a.matches) == ("minor", "patch", "0.18.1", False)
    assert any("from no_author_change PRs #10 (not counted)" in n for n in a.verdict.notes)
    b = rows["0.20.0"]
    assert (b.actual, b.required, b.matches) == ("minor", "minor", True)
    out = "\n".join(rv.audit_lines(list(rows.values())))
    assert "2 release(s) audited, 1 do not follow the rule: 0.19.0" in out


def _has_tags() -> bool:
    p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "-q", "--verify", "refs/tags/v0.18.0^{commit}"],
                       capture_output=True, text=True)
    return p.returncode == 0


def _expand(cell: str) -> set[str]:
    out = set()
    for part in cell.split(","):
        part = part.strip()
        m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) to \1\.\2\.(\d+)", part)
        if m:
            out |= {f"{m.group(1)}.{m.group(2)}.{k}" for k in range(int(m.group(3)), int(m.group(4)) + 1)}
        elif re.fullmatch(r"\d+\.\d+\.\d+", part):
            out.add(part)
    return out


@pytest.mark.skipif(not _has_tags(), reason="no release tags in this clone (git fetch --tags)")
def test_the_releasing_version_history_table_lists_every_mismatch():
    rows = rv.audit(ROOT, "0.1.0")
    bad = {a.version for a in rows if not a.matches}
    assert {"0.17.0"} <= bad and not {"0.8.0", "0.16.0", "0.18.0"} & bad
    r17 = next(a for a in rows if a.version == "0.17.0")
    assert (r17.actual, r17.required, r17.should_be) == ("minor", "patch", "0.16.1")
    text = (ROOT / "RELEASING.md").read_text()
    table = text.split("### Version history", 1)[1].split("\n\n")[2]
    listed = set()
    for line in table.splitlines()[2:]:
        listed |= _expand(line.split("|")[1])
    assert listed == bad
