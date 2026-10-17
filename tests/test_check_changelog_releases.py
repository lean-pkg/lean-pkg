"""scripts/check-changelog-releases.py: a released CHANGELOG.md section must read as it did at its tag,
record-doi's DOI line aside. A tmp git repository with tagged releases stands in for lean-pkg; the
release being cut is written by cut-release.py's own steps 1-2 and the DOI line by record-doi.py's own
add_to_changelog, so the check is tested against the real release text."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-changelog-releases.py"
DOI = "10.5281/zenodo.123"
HEAD = "# Changelog\n\nIntro.\n\n## [Unreleased]\n\n"
S020 = "## [0.2.0] - 2000-01-02\n\n### Fixed\n\n- A fix in 0.2.0.\n\n"
S010 = "## [0.1.0] - 2000-01-01\n\n### Added\n\n- The first command.\n"


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def _commit(repo: Path, msg: str, tag: str | None = None) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    if tag:
        _git(repo, "tag", tag)


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo), *args],
                          capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Two tagged releases, v0.1.0 and v0.2.0, and an empty `## [Unreleased]`."""
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid"),
                 ("GIT_CONFIG_GLOBAL", "/dev/null"), ("GIT_CONFIG_NOSYSTEM", "1")):
        monkeypatch.setenv(k, v)
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "CHANGELOG.md").write_text(HEAD + S010)
    _commit(r, "release: 0.1.0", "v0.1.0")
    (r / "CHANGELOG.md").write_text(HEAD + S020 + S010)
    _commit(r, "release: 0.2.0", "v0.2.0")
    return r


def test_the_real_repository_passes():
    """This checkout's CHANGELOG.md passes (tags missing in a shallow clone are skipped, not failed)."""
    r = _run(ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_tree_matching_its_tags_passes(repo):
    (repo / "CHANGELOG.md").write_text(HEAD + "### Added\n\n- Unreleased work.\n\n" + S020 + S010)
    r = _run(repo, "--require-tags")
    assert r.returncode == 0, r.stderr
    assert "2 released section(s) match their tags" in r.stdout


def test_an_entry_inserted_into_a_released_section_fails(repo):
    bad = S020.replace("- A fix in 0.2.0.\n", "- A fix in 0.2.0.\n- Merged after the tag.\n")
    (repo / "CHANGELOG.md").write_text(HEAD + bad + S010)
    r = _run(repo)
    assert r.returncode == 1
    assert "[0.2.0] differs from the same section at its tag v0.2.0" in r.stderr
    assert "+- Merged after the tag." in r.stderr          # the unified diff names the line
    assert "--- v0.2.0:CHANGELOG.md" in r.stderr
    assert "move the added entries under `## [Unreleased]`" in r.stderr
    assert "[0.1.0]" not in r.stderr


def test_an_entry_removed_or_reworded_fails_too(repo):
    (repo / "CHANGELOG.md").write_text(HEAD + S020.replace("A fix", "The fix") + S010)
    r = _run(repo)
    assert r.returncode == 1 and "-- A fix in 0.2.0." in r.stderr and "+- The fix in 0.2.0." in r.stderr


def test_record_doi_line_alone_passes_and_more_fails(repo):
    rd = _module("record_doi_under_test", ROOT / "scripts" / "record-doi.py")
    chg = rd.add_to_changelog((repo / "CHANGELOG.md").read_text(), "0.2.0", DOI)
    assert f"Zenodo version DOI: [{DOI}](https://doi.org/{DOI})" in chg
    (repo / "CHANGELOG.md").write_text(chg)
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    # The exemption is that one line at the section's end: an entry after it, or a different DOI text, fails.
    (repo / "CHANGELOG.md").write_text(chg.replace(f"(https://doi.org/{DOI})\n", f"(https://doi.org/{DOI})\n\n- Late.\n"))
    assert _run(repo).returncode == 1
    (repo / "CHANGELOG.md").write_text(chg.replace("Zenodo version DOI: [", "Zenodo DOI: ["))
    assert _run(repo).returncode == 1


def test_a_missing_tag_is_skipped_not_failed(repo):
    _git(repo, "tag", "-d", "v0.1.0")
    (repo / "CHANGELOG.md").write_text(HEAD + S020 + S010.replace("first", "changed"))
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    assert "[0.1.0] skipped: no local tag v0.1.0" in r.stdout and "git fetch --tags" in r.stdout
    # CI passes --require-tags: an old untagged section means the clone lacks tags, and the check fails.
    r = _run(repo, "--require-tags")
    assert r.returncode == 1 and "--require-tags" in r.stderr and "0.1.0" in r.stderr


def test_cut_release_section_passes_before_the_tag_and_after_record_doi(repo, monkeypatch):
    """cut-release.py's steps 1-2 write `## [0.3.0]` and `make check` runs before v0.3.0 exists; then the
    tag, then record-doi's line. The check passes at every stage, and --require-tags too."""
    (repo / "CHANGELOG.md").write_text(HEAD + "### Added\n\n- The new thing.\n\n" + S020 + S010)
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.2.0"\n')
    (repo / "CITATION.cff").write_text('version: "0.2.0"\ndate-released: "2000-01-02"\n')
    (repo / "src" / "lean_pkg").mkdir(parents=True)
    (repo / "src" / "lean_pkg" / "__init__.py").write_text('__version__ = "0.2.0"\n')
    (repo / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check-versions.py", repo / "scripts" / "check-versions.py")
    _commit(repo, "the new thing")
    cr = _module("cut_release_for_changelog_check", ROOT / "scripts" / "cut-release.py")
    monkeypatch.setattr(cr, "ROOT", repo)
    cr.steps12_versions("0.3.0", "2000-01-03", "public-safety (fake): 0 blocking, 0 warning(s)")
    assert (repo / "CHANGELOG.md").read_text().index("## [0.3.0] - 2000-01-03") > 0

    r = _run(repo, "--require-tags")
    assert r.returncode == 0, r.stderr
    assert "[0.3.0] skipped: no local tag v0.3.0; the newest section: the release being cut" in r.stdout

    _commit(repo, "release: 0.3.0", "v0.3.0")
    r = _run(repo, "--require-tags")
    assert r.returncode == 0 and "3 released section(s) match" in r.stdout, r.stderr

    rd = _module("record_doi_for_changelog_check", ROOT / "scripts" / "record-doi.py")
    (repo / "CHANGELOG.md").write_text(rd.add_to_changelog((repo / "CHANGELOG.md").read_text(), "0.3.0", DOI))
    r = _run(repo, "--require-tags")
    assert r.returncode == 0, r.stderr

    # And the bug this check exists for: after the release, a rebased entry lands in [0.3.0].
    chg = (repo / "CHANGELOG.md").read_text().replace("- The new thing.\n", "- The new thing.\n- Rebased in.\n")
    (repo / "CHANGELOG.md").write_text(chg)
    r = _run(repo)
    assert r.returncode == 1 and "[0.3.0] differs" in r.stderr and "+- Rebased in." in r.stderr


def test_allow_listed_section_is_pinned_to_its_text(repo, monkeypatch):
    """An allow-listed difference passes only while the section's text is the pinned one."""
    mod = _module("changelog_check_under_test", SCRIPT)
    edited = S020.replace("- A fix in 0.2.0.\n", "- A fix in 0.2.0.\n- A historical late entry.\n")
    (repo / "CHANGELOG.md").write_text(HEAD + edited + S010)
    pinned = mod.digest(mod.normalise(edited))
    monkeypatch.setattr(mod, "ALLOWED", {"0.2.0": (pinned, "test reason")})
    assert mod.check(repo) == 0
    (repo / "CHANGELOG.md").write_text(HEAD + edited.replace("historical", "edited") + S010)
    assert mod.check(repo) == 1


def test_allow_list_entries_are_still_needed():
    """Every ALLOWED entry names a section that differs from its tag today (when the tag is here)."""
    mod = _module("changelog_check_allowed", SCRIPT)
    cur = mod.sections((ROOT / "CHANGELOG.md").read_text())
    for v, (pinned, reason) in mod.ALLOWED.items():
        assert v in cur and reason
        assert mod.digest(mod.normalise(cur[v])) == pinned, v
        if mod.has_tag(ROOT, v):
            tagged = mod.sections(_git(ROOT, "show", f"v{v}:CHANGELOG.md")).get(v)
            assert mod.normalise(tagged) != mod.normalise(cur[v]), f"{v} matches its tag: drop it from ALLOWED"
