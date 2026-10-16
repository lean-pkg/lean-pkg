#!/usr/bin/env python3
"""scripts/cut-release.py runs the RELEASING.md checklist end to end and resumes after a failure;
scripts/record-doi.py records the Zenodo version DOI (step 7) once and only once. A tmp repository
with a bare remote stands in for origin; `make public-safety` is a fake whose counts the test sets,
scripts/release.sh is a stub that logs its call, and Zenodo is a JSON file. Nothing reaches GitHub
or Zenodo."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD, V = "1.2.2", "1.2.3"
REAL_GIT = shutil.which("git")
CONCEPT = "10.5281/zenodo.100"
DOI = "10.5281/zenodo.123"
TODAY = dt.date.today().isoformat()

FAKE_MAKE = r'''#!/usr/bin/env python3
import os, sys
with open(os.path.join(os.environ["FAKE_STATE"], "make.log"), "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\n")
if sys.argv[1:] == ["check"]:
    if os.environ.get("FAKE_CHECK_FAIL"):
        sys.stderr.write("check: a gate failed (fake)\n"); sys.exit(2)
    print("check: all gates green (fake)"); sys.exit(0)
if sys.argv[1:] != ["public-safety"]:
    sys.stderr.write("fake make: unexpected " + " ".join(sys.argv[1:]) + "\n"); sys.exit(2)
h, b, w = (int(os.environ.get(k, "0")) for k in ("FAKE_HITS", "FAKE_BLOCKING", "FAKE_WARNINGS"))
print(f"public-safety for lean-pkg-self: {h} hit(s) ({h} tree, 0 history); {b} blocking, {w} warning(s)"
      " (--strict: warnings block); 0 from latex-label")
sys.exit(1 if b or w else 0)
'''

RELEASE_STUB = r'''#!/usr/bin/env bash
echo "$1" >> "$FAKE_STATE/release.log"
echo "release.sh" >> "$FAKE_STATE/make.log"
[[ -z "${FAKE_RELEASE_FAIL:-}" ]] || { echo "release: tag push failed (fake)" >&2; exit 1; }
echo "release: v$1 (fake)"
'''

CITATION = f'''cff-version: 1.2.0
title: "lean-pkg"
version: "{OLD}"
date-released: "2026-01-01"
doi: {CONCEPT}
identifiers:
  - type: doi
    value: {CONCEPT}
    description: Concept DOI (resolves to the latest version)
url: "https://example.org/"
'''

CHANGELOG = f"""# Changelog

## [Unreleased]

### Added

- `lean-pkg frob` frobs a package. (#9)

### Fixed

- A thing that was broken. (#8)

## [{OLD}] - 2026-01-01

### Fixed

- An older thing.
"""

BIBTEX = f"""# Citing

```bibtex
@software{{lean_pkg,
  title   = {{lean-pkg}},
  version = {{{OLD}}},
  note    = {{commit <sha>}}
}}
```
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "LEAN_PKG_", "FAKE_"))}
    env.update({
        "PATH": f"{tmp / 'bin'}{os.pathsep}{env.get('PATH', '')}", "FAKE_STATE": str(tmp / "state"),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org",
        "LEAN_PKG_ZENODO_RECORDS_FILE": str(tmp / "state" / "zenodo.json"),
        "LEAN_PKG_RELEASE_RETRY_DELAY": "0", "REAL_GIT": REAL_GIT,
    })
    env.update(extra)
    return env


def _g(tmp: Path, *args: str, repo: str = "work") -> str:
    return subprocess.run([REAL_GIT, "-C", str(tmp / repo), *args], check=True, capture_output=True,
                          text=True, env=_env(tmp)).stdout.strip()


def _setup(tmp: Path) -> Path:
    (tmp / "bin").mkdir()
    (tmp / "state").mkdir()
    _exe(tmp / "bin" / "make", FAKE_MAKE)
    _zenodo(tmp, [])
    env = _env(tmp)
    work = tmp / "work"
    subprocess.run([REAL_GIT, "init", "-q", "--bare", "-b", "main", str(tmp / "origin.git")], check=True, env=env)
    subprocess.run([REAL_GIT, "init", "-q", "-b", "main", str(work)], check=True, env=env)
    for d in ("scripts", "docs", "src/lean_pkg"):
        (work / d).mkdir(parents=True)
    for s in ("cut-release.py", "record-doi.py", "check-versions.py"):
        shutil.copy(ROOT / "scripts" / s, work / "scripts" / s)
    _exe(work / "scripts" / "release.sh", RELEASE_STUB)
    (work / "pyproject.toml").write_text(f'[project]\nname = "lean-pkg"\nversion = "{OLD}"\n')
    (work / "CITATION.cff").write_text(CITATION)
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    (work / "README.md").write_text(BIBTEX)
    (work / "docs" / "index.md").write_text(BIBTEX)
    (work / "src" / "lean_pkg" / "__init__.py").write_text(f'"""lean-pkg."""\n\n__version__ = "{OLD}"\n')
    _g(tmp, "add", "-A")
    _g(tmp, "commit", "-q", "-m", f"release: {OLD}")
    _g(tmp, "remote", "add", "origin", str(tmp / "origin.git"))
    _g(tmp, "push", "-q", "-u", "origin", "main")
    return work


def _zenodo(tmp: Path, versions: list[tuple[str, str, str, str]]) -> None:
    """versions: (version, doi, concept doi, tree url)."""
    hits = [{"doi": doi, "conceptdoi": concept,
             "metadata": {"version": f"v{ver}", "related_identifiers": [{"identifier": tree}]}}
            for ver, doi, concept, tree in versions]
    (tmp / "state" / "zenodo.json").write_text(json.dumps({"hits": {"hits": hits}}))


def _archived(tmp: Path, ver: str = V, doi: str = DOI, concept: str = CONCEPT, tree: str | None = None) -> None:
    _zenodo(tmp, [(ver, doi, concept, tree or f"https://github.com/lean-pkg/lean-pkg/tree/v{ver}")])


def _cut(tmp: Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    work = tmp / "work"
    return subprocess.run([sys.executable, str(work / "scripts" / "cut-release.py"), *args],
                          capture_output=True, text=True, env=_env(tmp, **extra), cwd=work)


def _doi(tmp: Path, *args: str, **extra: str) -> subprocess.CompletedProcess:
    work = tmp / "work"
    return subprocess.run([sys.executable, str(work / "scripts" / "record-doi.py"), *args],
                          capture_output=True, text=True, env=_env(tmp, **extra), cwd=work)


def _subjects(tmp: Path) -> list[str]:
    return _g(tmp, "log", "--format=%s").splitlines()


def _origin_head(tmp: Path) -> str:
    return _g(tmp, "rev-parse", "main", repo="origin.git")


def _releases(tmp: Path) -> list[str]:
    p = tmp / "state" / "release.log"
    return p.read_text().split() if p.exists() else []


def test_cut_release_runs_every_step_in_order(tmp_path):
    work = _setup(tmp_path)
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 0, r.stderr
    assert _subjects(tmp_path)[:2] == [f"release: {V}", f"citation: bibtex examples cite {V}"]
    assert _origin_head(tmp_path) == _g(tmp_path, "rev-parse", "HEAD")
    assert _releases(tmp_path) == [V]
    # step 0 shows what needs a documented home
    assert "`lean-pkg frob` frobs a package" in r.stdout
    # steps 1-3: the Unreleased body moves under the new heading, followed by the safety line
    chg = (work / "CHANGELOG.md").read_text()
    assert chg.startswith(f"# Changelog\n\n## [Unreleased]\n\n## [{V}] - {TODAY}\n\n### Added\n")
    section = chg.split(f"## [{V}]")[1].split(f"## [{OLD}]")[0]
    assert "A thing that was broken. (#8)" in section
    assert section.rstrip().endswith("public-safety (make public-safety, --strict, history): "
                                     "0 hit(s) (0 tree, 0 history); 0 blocking, 0 warning(s)")
    assert f'version = "{V}"' in (work / "pyproject.toml").read_text()
    cff = (work / "CITATION.cff").read_text()
    assert f'version: "{V}"' in cff and f'date-released: "{TODAY}"' in cff
    assert f'__version__ = "{V}"' in (work / "src" / "lean_pkg" / "__init__.py").read_text()
    # step 2a, in its own commit; the release commit carries exactly the four version files
    for f in ("README.md", "docs/index.md"):
        assert f"  version = {{{V}}}," in (work / f).read_text()
    assert sorted(_g(tmp_path, "show", "--format=", "--name-only", "HEAD~1").split()) == ["README.md", "docs/index.md"]
    assert sorted(_g(tmp_path, "show", "--format=", "--name-only", "HEAD").split()) == sorted(
        ["CHANGELOG.md", "CITATION.cff", "pyproject.toml", "src/lean_pkg/__init__.py"])


def test_cut_release_records_the_doi_when_zenodo_has_it(tmp_path):
    work = _setup(tmp_path)
    _archived(tmp_path)
    r = _cut(tmp_path, V, "--doi-wait", "0")
    assert r.returncode == 0, r.stderr
    assert _subjects(tmp_path)[0] == f"citation: Zenodo version DOI for {V}"
    assert _origin_head(tmp_path) == _g(tmp_path, "rev-parse", "HEAD")
    assert f"    value: {DOI}\n    description: Version DOI for {V}\n" in (work / "CITATION.cff").read_text()
    section = (work / "CHANGELOG.md").read_text().split(f"## [{V}]")[1].split(f"## [{OLD}]")[0]
    assert section.rstrip().endswith(f"Zenodo version DOI: [{DOI}](https://doi.org/{DOI})")


def test_doi_not_yet_archived_is_a_warning_and_the_release_stands(tmp_path):
    _setup(tmp_path)
    r = _cut(tmp_path, V, "--doi-wait", "0")
    assert r.returncode == 0, r.stderr
    assert "WARN: step 7 did not finish" in r.stdout and f"make record-doi VERSION={V}" in r.stdout
    assert _subjects(tmp_path)[0] == f"release: {V}"
    assert _releases(tmp_path) == [V]


def test_public_safety_hit_stops_before_the_release_commit_and_a_rerun_resumes(tmp_path):
    _setup(tmp_path)
    before = _origin_head(tmp_path)
    r = _cut(tmp_path, V, "--no-doi", FAKE_HITS="1", FAKE_BLOCKING="1")
    assert r.returncode == 1 and "public-safety is not clean" in r.stderr
    assert _subjects(tmp_path)[0] == f"citation: bibtex examples cite {V}"      # local only
    assert _origin_head(tmp_path) == before and _releases(tmp_path) == []
    assert _g(tmp_path, "status", "--porcelain") == ""
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 0, r.stderr
    assert "BibTeX examples already cite" in r.stdout
    assert _subjects(tmp_path)[:3] == [f"release: {V}", f"citation: bibtex examples cite {V}", f"release: {OLD}"]


def test_public_safety_warning_also_blocks(tmp_path):
    _setup(tmp_path)
    r = _cut(tmp_path, V, "--no-doi", FAKE_HITS="1", FAKE_WARNINGS="1")
    assert r.returncode == 1 and "public-safety is not clean" in r.stderr
    assert f"release: {V}" not in _subjects(tmp_path)


def test_release_script_failure_resumes_at_the_push_without_new_commits(tmp_path):
    _setup(tmp_path)
    r = _cut(tmp_path, V, "--no-doi", FAKE_RELEASE_FAIL="1")
    assert r.returncode == 1 and "scripts/release.sh failed" in r.stderr
    subjects = _subjects(tmp_path)
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 0, r.stderr
    assert "resuming at the push" in r.stdout
    assert _subjects(tmp_path) == subjects
    assert _releases(tmp_path) == [V, V]


def test_refuses_a_version_not_above_the_current_one(tmp_path):
    _setup(tmp_path)
    r = _cut(tmp_path, "1.2.1", "--no-doi")     # OLD itself at HEAD would be a resume, not a new release
    assert r.returncode == 1 and "is not above the current version" in r.stderr
    assert _subjects(tmp_path) == [f"release: {OLD}"]


def test_refuses_an_empty_unreleased_section(tmp_path):
    work = _setup(tmp_path)
    (work / "CHANGELOG.md").write_text(CHANGELOG.split("### Added")[0] + f"## [{OLD}] - 2026-01-01\n")
    _g(tmp_path, "commit", "-qam", "empty")
    _g(tmp_path, "push", "-q")
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 1 and "`## [Unreleased]` is empty" in r.stderr


def test_refuses_a_dirty_tree_and_unrelated_local_commits(tmp_path):
    work = _setup(tmp_path)
    (work / "stray.txt").write_text("x\n")
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 1 and "working tree not clean" in r.stderr
    _g(tmp_path, "add", "stray.txt")
    _g(tmp_path, "commit", "-qm", "unrelated work")
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 1 and "more than this release's own commits" in r.stderr


def test_record_doi_is_idempotent(tmp_path):
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    _archived(tmp_path)
    r = _doi(tmp_path, V)
    assert r.returncode == 0, r.stderr
    head = _g(tmp_path, "rev-parse", "HEAD")
    r = _doi(tmp_path, V)
    assert r.returncode == 0 and "nothing to do" in r.stdout
    assert _g(tmp_path, "rev-parse", "HEAD") == head == _origin_head(tmp_path)


def test_record_doi_refuses_a_record_of_another_concept_or_tag(tmp_path):
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    head = _g(tmp_path, "rev-parse", "HEAD")
    _archived(tmp_path, concept="10.5281/zenodo.999")
    r = _doi(tmp_path, V)
    assert r.returncode == 1 and "belongs to 10.5281/zenodo.999" in r.stderr
    _archived(tmp_path, tree="https://github.com/lean-pkg/lean-pkg/tree/v9.9.9")
    r = _doi(tmp_path, V)
    assert r.returncode == 1 and "does not point at" in r.stderr
    assert _g(tmp_path, "rev-parse", "HEAD") == head


def test_record_doi_follows_every_page_to_the_newest_version(tmp_path):
    """Zenodo serves at most 25 records per anonymous page, oldest first: the newest is on the last page."""
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    tree = "https://github.com/lean-pkg/lean-pkg/tree/v"
    records = [(f"0.0.{i}", f"10.5281/zenodo.{1000 + i}", CONCEPT, f"{tree}0.0.{i}") for i in range(30)]
    records.append((V, DOI, CONCEPT, f"{tree}{V}"))
    _zenodo(tmp_path, records[25:])
    last = tmp_path / "state" / "zenodo-page2.json"
    (tmp_path / "state" / "zenodo.json").rename(last)
    _zenodo(tmp_path, records[:25])
    first = tmp_path / "state" / "zenodo.json"
    first.write_text(json.dumps({**json.loads(first.read_text()), "links": {"next": str(last)}}))
    r = _doi(tmp_path, V)
    assert r.returncode == 0, r.stderr
    assert f"value: {DOI}" in (tmp_path / "work" / "CITATION.cff").read_text()


TRAILERS = ["Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", "Signed-off-by: Test <test@example.org>"]


def _trailers(tmp: Path, rev: str) -> list[str]:
    return _g(tmp, "log", "-1", "--format=%(trailers:only,unfold)", rev).splitlines()


def test_commit_trailer_lands_on_every_commit_the_release_makes(tmp_path):
    _setup(tmp_path)
    _archived(tmp_path)
    r = _cut(tmp_path, V, "--doi-wait", "0", LEAN_PKG_COMMIT_TRAILER="\n".join(TRAILERS) + "\n")
    assert r.returncode == 0, r.stderr
    assert _subjects(tmp_path)[:3] == [f"citation: Zenodo version DOI for {V}", f"release: {V}",
                                       f"citation: bibtex examples cite {V}"]
    for rev in ("HEAD", "HEAD~1", "HEAD~2"):          # step 7 (record-doi), step 4, step 2a
        assert _trailers(tmp_path, rev) == TRAILERS, rev


def test_no_trailer_unless_asked(tmp_path):
    _setup(tmp_path)
    _archived(tmp_path)
    assert _cut(tmp_path, V, "--doi-wait", "0").returncode == 0
    assert [_trailers(tmp_path, rev) for rev in ("HEAD", "HEAD~1", "HEAD~2")] == [[], [], []]


def test_malformed_trailer_refuses_before_anything_is_written(tmp_path):
    _setup(tmp_path)
    before, origin = _subjects(tmp_path), _origin_head(tmp_path)
    for bad in ("Co-Authored-By Claude <noreply@anthropic.com>", "Co-Authored-By:", "just a sentence",
                TRAILERS[0] + "\nnot a trailer"):
        for run in (_cut, _doi):
            r = run(tmp_path, V, LEAN_PKG_COMMIT_TRAILER=bad)
            assert r.returncode == 2 and "LEAN_PKG_COMMIT_TRAILER" in r.stderr, (run.__name__, bad, r.stderr)
    assert _subjects(tmp_path) == before and _origin_head(tmp_path) == origin
    assert _g(tmp_path, "status", "--porcelain") == "" and _releases(tmp_path) == []


# --- the gate runs before the release commit; resumes; record-doi's push (review findings) ---------

def _calls(tmp: Path) -> list[str]:
    p = tmp / "state" / "make.log"
    return p.read_text().splitlines() if p.exists() else []


def test_make_check_runs_before_the_release_commit_is_pushed(tmp_path):
    _setup(tmp_path)
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 0, r.stderr
    assert _calls(tmp_path) == ["public-safety", "check", "release.sh"]
    committed = f"committed {_g(tmp_path, 'rev-parse', '--short', 'HEAD')} release: {V}"
    assert r.stdout.index("make check on the release tree") < r.stdout.index(committed)
    assert r.stdout.index("make check on the release tree") < r.stdout.index("pushed main")


def test_failing_check_leaves_origin_untouched_and_a_rerun_releases(tmp_path):
    """Before: the release commit was pushed, release.sh's `make check` failed, origin/main said 1.2.3 without
    a tag, and the re-run refused (`1.2.3 is not above the current version 1.2.3`)."""
    work = _setup(tmp_path)
    before = _origin_head(tmp_path)
    r = _cut(tmp_path, V, "--no-doi", FAKE_CHECK_FAIL="1")
    assert r.returncode == 1 and "make check failed" in r.stderr and "nothing is pushed" in r.stderr
    assert _origin_head(tmp_path) == before and _releases(tmp_path) == []
    assert _subjects(tmp_path)[0] == f"citation: bibtex examples cite {V}"      # local only, no release commit
    assert _g(tmp_path, "status", "--porcelain") == ""                          # version files restored
    assert f'version = "{OLD}"' in (work / "pyproject.toml").read_text()
    assert "## [Unreleased]\n\n### Added" in (work / "CHANGELOG.md").read_text()
    # the fix is committed on top of the local citation commit and pushed with it
    (work / "fix.txt").write_text("fix\n")
    _g(tmp_path, "add", "fix.txt")
    _g(tmp_path, "commit", "-qm", "fix the failing gate")
    _g(tmp_path, "push", "-q")
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 0, r.stderr
    assert _subjects(tmp_path)[:3] == [f"release: {V}", "fix the failing gate", f"citation: bibtex examples cite {V}"]
    assert _origin_head(tmp_path) == _g(tmp_path, "rev-parse", "HEAD") and _releases(tmp_path) == [V]


def test_release_script_failure_then_a_pushed_fix_resumes_and_tags_the_fix(tmp_path):
    """origin/main carries `release: X.Y.Z` with no tag and a fix on top: the re-run resumes, not refuses."""
    work = _setup(tmp_path)
    r = _cut(tmp_path, V, "--no-doi", FAKE_RELEASE_FAIL="1")
    assert r.returncode == 1 and "scripts/release.sh failed" in r.stderr
    (work / "fix.txt").write_text("fix\n")
    _g(tmp_path, "add", "fix.txt")
    _g(tmp_path, "commit", "-qm", "fix the release step")
    _g(tmp_path, "push", "-q")
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 0, r.stderr
    assert "resuming at the push" in r.stdout and "1 commit(s) on top of it" in r.stdout
    assert _subjects(tmp_path)[:2] == ["fix the release step", f"release: {V}"]
    assert _releases(tmp_path) == [V, V]


def _tag_on_origin(tmp: Path, rev: str) -> None:
    _g(tmp, "tag", "-a", f"v{V}", rev, "-m", f"lean-pkg {V}")
    _g(tmp, "push", "-q", "origin", f"refs/tags/v{V}")


def test_an_already_tagged_version_with_other_commits_on_top_is_refused(tmp_path):
    work = _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    _tag_on_origin(tmp_path, "HEAD")
    (work / "later.txt").write_text("later\n")
    _g(tmp_path, "add", "later.txt")
    _g(tmp_path, "commit", "-qm", "later work")
    _g(tmp_path, "push", "-q")
    r = _cut(tmp_path, V, "--no-doi")
    assert r.returncode == 1 and f"v{V} is already tagged on origin" in r.stderr
    assert _releases(tmp_path) == [V]


def _block_push(tmp: Path) -> None:
    """Fetches still work; every push fails the way an unreachable remote does (transient)."""
    _g(tmp, "remote", "set-url", "--push", "origin", str(tmp / "unreachable.git"))


def _unblock_push(tmp: Path) -> None:
    _g(tmp, "remote", "set-url", "--delete", "--push", "origin", str(tmp / "unreachable.git"))


def test_record_doi_push_failure_is_retried_reported_and_resumed(tmp_path):
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    _archived(tmp_path)
    origin = _origin_head(tmp_path)
    _block_push(tmp_path)
    r = _doi(tmp_path, V, LEAN_PKG_RELEASE_RETRIES="3")
    assert r.returncode == 1 and "Traceback" not in r.stderr, r.stderr
    assert "push attempt 1/3 failed (transient: could not read from remote repository); retrying" in r.stderr
    assert "push attempt 3/3 failed (transient: could not read from remote repository; giving up)" in r.stderr
    assert "is committed on main but not pushed" in r.stderr and f"make record-doi VERSION={V}" in r.stderr
    assert _subjects(tmp_path)[0] == f"citation: Zenodo version DOI for {V}" and _origin_head(tmp_path) == origin
    head = _g(tmp_path, "rev-parse", "HEAD")
    _unblock_push(tmp_path)
    r = _doi(tmp_path, V)                       # before: "main differs from origin/main"
    assert r.returncode == 0, r.stderr
    assert "is committed but not on origin; pushing it" in r.stdout
    assert _g(tmp_path, "rev-parse", "HEAD") == head == _origin_head(tmp_path)
    r = _doi(tmp_path, V)
    assert r.returncode == 0 and "nothing to do" in r.stdout


def test_cut_release_rerun_pushes_an_unpushed_doi_commit_and_skips_the_tagged_release(tmp_path):
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    _tag_on_origin(tmp_path, "HEAD")              # step 5 done: origin has the tag at the release commit
    _archived(tmp_path)
    _block_push(tmp_path)
    r = _doi(tmp_path, V, LEAN_PKG_RELEASE_RETRIES="1")        # step 7's push fails
    assert r.returncode == 1 and _subjects(tmp_path)[0] == f"citation: Zenodo version DOI for {V}"
    _unblock_push(tmp_path)
    r = _cut(tmp_path, V, "--doi-wait", "0")      # before: "more than this release's own commits"
    assert r.returncode == 0, r.stderr
    assert "step 5: skipped; origin already has" in r.stdout and "nothing to do" in r.stdout
    assert _origin_head(tmp_path) == _g(tmp_path, "rev-parse", "HEAD")
    assert _releases(tmp_path) == [V]              # release.sh is not run again for a tagged release


def test_a_permanent_push_refusal_is_not_retried(tmp_path):
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    _archived(tmp_path)
    hook = tmp_path / "origin.git" / "hooks" / "pre-receive"
    _exe(hook, "#!/bin/sh\necho 'protected branch' >&2\nexit 1\n")
    r = _doi(tmp_path, V, LEAN_PKG_RELEASE_RETRIES="3")
    assert r.returncode == 1 and "Traceback" not in r.stderr
    assert "push attempt 1/3 failed (not transient; not retrying)" in r.stderr and "attempt 2/3" not in r.stderr


def test_a_transient_push_failure_that_clears_is_retried_to_success(tmp_path):
    _setup(tmp_path)
    assert _cut(tmp_path, V, "--no-doi").returncode == 0
    _archived(tmp_path)
    _exe(tmp_path / "bin" / "git", GIT_FLAKY_PUSH)
    r = _doi(tmp_path, V, FAKE_PUSH_FAILS="2")
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("failed (transient: connection reset); retrying") == 2
    assert _origin_head(tmp_path) == _g(tmp_path, "rev-parse", "HEAD")


GIT_FLAKY_PUSH = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
if "push" in args:
    counter = os.path.join(os.environ["FAKE_STATE"], "push.count")
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    if n < int(os.environ.get("FAKE_PUSH_FAILS", "0")):
        open(counter, "w").write(str(n + 1))
        sys.stderr.write("fatal: unable to access 'origin': Connection reset by peer\n"); sys.exit(128)
os.execv(os.environ["REAL_GIT"], ["git"] + args)
"""
