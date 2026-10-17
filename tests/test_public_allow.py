#!/usr/bin/env python3
""".lean-pkg-public-allow (#6 field report): `release --fresh-repo` drops the private patterns file, so its
benign `allow:` lines were lost and the new repository's public-safety reported what the export scan had
allowed. Only the allow lines are carried into a committed, public `.lean-pkg-public-allow`; an allow line
holding a private string is refused; the public file's own lines never exempt themselves from the private
block patterns; a non-allow line there is ignored with a warning."""

from __future__ import annotations

import json

import pytest
import yaml

from lean_pkg import public_safety as ps

from .conftest import _git, base_pkg, make_receipt, make_repo, read_events

AXIOMS = ["propext", "Quot.sound", "Classical.choice"]
RUNNER = "/" + "home/runner/"   # built, not written: lean-pkg's own public-safety scans this file
ADDRESS = "ci" + "@runner.invalid"
FIXTURE = f"contact = {ADDRESS}\n"                               # a fixture address under .invalid
CI_LOG = f"cd {RUNNER}work/pkg/pkg && lake build\n"             # a generic CI runner path
SCOPED = f"allow: logs/: {RUNNER}"
PATTERNS = f"Nightjar\nallow: ^ci@runner\\.invalid$\n{SCOPED}\n"


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "Exporter"), ("GIT_AUTHOR_EMAIL", "exporter@example.com"),
                 ("GIT_COMMITTER_NAME", "Exporter"), ("GIT_COMMITTER_EMAIL", "exporter@example.com")):
        monkeypatch.setenv(k, v)


class G:
    pass


def gated(tmp_path, patterns: str = PATTERNS, extra=None):
    files = {"Main.lean": "theorem t : True := trivial\n", "LICENSE": "MIT\n", "lake-manifest.json": "{}\n",
             "tests/fixture.txt": FIXTURE, "logs/ci.log": CI_LOG, **(extra or {})}
    repo = make_repo(tmp_path / "src-pkg", extra_files=files)
    (repo / ps.PATTERNS_FILE).write_text(patterns)
    with open(repo / ".git" / "info" / "exclude", "a") as f:
        f.write(ps.PATTERNS_FILE + "\n")
    (repo / ".lean-receipts").mkdir()
    (repo / ".lean-receipts" / "seed.json").write_text(json.dumps(make_receipt(repo), indent=2))
    _git(repo, "add", "-f", ".lean-receipts")
    _git(repo, "commit", "-q", "-m", "evidence")
    g = G()
    g.repo, g.registry = repo, tmp_path / "lean-packages.yaml"
    g.events, g.target = tmp_path / "lean-package-events.jsonl", tmp_path / "public" / "pkg"
    pkg = base_pkg("pkg", repo, "verified-private",
                   gate={"receipt": ".lean-receipts/seed.json", "comparator": None, "axioms_allowed": AXIOMS})
    g.registry.write_text(yaml.safe_dump({"version": 2, "packages": {"pkg": pkg}}, sort_keys=False))
    return g


def run(lp, g, *argv):
    return lp.main(["--registry", str(g.registry), *argv])


def test_allow_lines_are_carried_and_the_fresh_repo_passes_its_own_scan(lp, tmp_path, capsys):
    g = gated(tmp_path)
    assert run(lp, g, "public-safety", "pkg", "--no-history") == 0      # the private allow lines apply
    capsys.readouterr()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--dry-run") == 0
    out = capsys.readouterr().out
    assert (f"public allow: 2 allow line(s) of {ps.PATTERNS_FILE} (line(s) 2, 3) carried into "
            f"{ps.PUBLIC_ALLOW_FILE}") in out
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--agent", "vera") == 0
    capsys.readouterr()
    tracked = set(_git(g.target, "ls-files").stdout.split())
    assert ps.PUBLIC_ALLOW_FILE in tracked and ps.PATTERNS_FILE not in tracked
    text = (g.target / ps.PUBLIC_ALLOW_FILE).read_text()
    assert "Nightjar" not in text                                       # never a block line
    assert [ln for ln in text.splitlines() if not ln.startswith("#")] == PATTERNS.splitlines()[1:]
    assert run(lp, g, "public-safety", "pkg") == 0                       # tree and history of the new repo
    out = capsys.readouterr().out
    assert f"{ps.PUBLIC_ALLOW_FILE}: 2 allow" in out


def test_negative_control_without_the_public_allow_file_the_hits_return(lp, tmp_path, capsys):
    g = gated(tmp_path)
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target)) == 0
    _git(g.target, "rm", "-q", ps.PUBLIC_ALLOW_FILE)
    _git(g.target, "commit", "-q", "-m", "drop")
    capsys.readouterr()
    assert run(lp, g, "public-safety", "pkg", "--no-history", "--json") == 1
    pats = {h["pattern"] for h in json.loads(capsys.readouterr().out)["hits"]}
    assert {"email", "home-path"} <= pats                                # .invalid is not a declared address


def test_an_allow_line_holding_a_private_string_refuses_the_export(lp, tmp_path, capsys):
    g = gated(tmp_path, patterns="Nightjar\nallow: ^ci@runner\\.invalid$\nallow: Nightjar-mirror\\.org\n")
    before = g.registry.read_text()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target)) == 1
    err = capsys.readouterr().err
    assert "allow line(s) 3 of .lean-pkg-private-patterns hold a private string" in err
    assert "line 3: .lean-pkg-private-patterns:1" in err and "Nightjar" not in err
    assert not g.target.exists() and g.registry.read_text() == before and read_events(g) == []


def test_public_allow_lines_never_exempt_a_private_string_in_themselves(lp, tmp_path, capsys):
    g = gated(tmp_path, extra={ps.PUBLIC_ALLOW_FILE: "allow: Nightjar\nallow: ^ci@runner\\.invalid$\n"})
    assert run(lp, g, "public-safety", "pkg", "--json") == 1
    hits = json.loads(capsys.readouterr().out)["hits"]
    mine = [h for h in hits if h["pattern"] == ps.PUBLIC_ALLOW_PRIVATE]
    assert {(h["where"], h["path"], h["line"]) for h in mine} == {("tree", ps.PUBLIC_ALLOW_FILE, 1)}
    assert all("Nightjar" not in json.dumps(h) for h in hits)


def test_public_allow_file_is_read_and_non_allow_lines_are_ignored(lp, tmp_path, capsys):
    g = gated(tmp_path, patterns="Nightjar\n",
              extra={ps.PUBLIC_ALLOW_FILE: f"# fixtures\nallow: ^ci@runner\\.invalid$\n{SCOPED}\n"
                                           "block: Starling\n"})
    assert run(lp, g, "public-safety", "pkg", "--no-history", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["patterns_source"]["public_allow"] == 2 and data["patterns_source"]["allow"] == 0
    assert any("not an `allow:` line; ignored" in w and ":4:" in w for w in data["warnings"])


def test_existing_public_allow_file_is_merged_not_duplicated(lp, tmp_path, capsys):
    g = gated(tmp_path, extra={ps.PUBLIC_ALLOW_FILE: "allow: ^ci@runner\\.invalid$\n"})
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target), "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["public_allow"] == {"file": ps.PUBLIC_ALLOW_FILE, "carried": 1, "lines": [3]}
    lines = (g.target / ps.PUBLIC_ALLOW_FILE).read_text().splitlines()
    assert lines.count("allow: ^ci@runner\\.invalid$") == 1 and SCOPED in lines
