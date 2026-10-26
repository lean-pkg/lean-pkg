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


def gated(tmp_path, patterns: str = PATTERNS, extra=None, agent=None):
    files = {"Main.lean": "theorem t : True := trivial\n", "LICENSE": "MIT\n", "lake-manifest.json": "{}\n",
             "tests/fixture.txt": FIXTURE, "logs/ci.log": CI_LOG, **(extra or {})}
    repo = make_repo(tmp_path / "src-pkg", extra_files=files)
    _git(repo, "config", "user.name", "Exporter")                       # #339: the export commit's identity
    _git(repo, "config", "user.email", "12345+exporter@users.noreply.github.com")
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
    pkg = base_pkg("pkg", repo, "verified-private", agent=agent,
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
    # #340: only a USED allow line is carried, so only a used one holding a private string refuses: here a
    # homepage URL holding the registry-derived user name, exempted in the tree by line 4
    g = gated(tmp_path, agent="vera",
              patterns=f"Nightjar\nallow: ^ci@runner\\.invalid$\n{SCOPED}\nallow: https://example\\.org/~vera/\n",
              extra={"docs/home.md": "https://example.org/~vera/\n"})
    before = g.registry.read_text()
    assert run(lp, g, "release", "pkg", "--fresh-repo", str(g.target)) == 1
    err = capsys.readouterr().err
    assert "allow line(s) 4 of .lean-pkg-private-patterns hold a private string" in err
    assert "line 4: registry agent" in err and "vera" not in err
    assert not g.target.exists() and g.registry.read_text() == before and read_events(g) == []


# #340: carry only the allow lines the export's own scan used

UNUSED = "allow: Nightjar-mirror\\.org/~starling"      # names private things, exempts nothing in the tree


def test_an_unused_allow_line_is_dropped_and_never_printed(lp, tmp_path, capsys):
    g = gated(tmp_path, patterns=f"Nightjar\nallow: ^ci@runner\\.invalid$\n{UNUSED}\n{SCOPED}\n")
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--dry-run") == 0
    cap = capsys.readouterr()
    assert (f"public allow: 2 allow line(s) of {ps.PATTERNS_FILE} (line(s) 2, 4) carried into "
            f"{ps.PUBLIC_ALLOW_FILE}") in cap.out
    assert "1 unused allow line(s) dropped" in cap.out
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--json") == 0
    cap2 = capsys.readouterr()
    data = json.loads(cap2.out)
    assert data["public_allow"] == {"file": ps.PUBLIC_ALLOW_FILE, "carried": 2, "lines": [2, 4], "unused": 1,
                                    "present": 0}
    text = (g.target / ps.PUBLIC_ALLOW_FILE).read_text()
    assert [ln for ln in text.splitlines() if not ln.startswith("#")] == ["allow: ^ci@runner\\.invalid$", SCOPED]
    for blob in (cap.out, cap.err, cap2.out, cap2.err, text, _git(g.target, "log", "--format=%B").stdout):
        assert "starling" not in blob and "Nightjar" not in blob
    assert run(lp, g, "public-safety", "pkg") == 0                       # the carried lines still suffice


def test_no_allow_line_used_writes_no_public_allow_file(lp, tmp_path, capsys):
    g = gated(tmp_path, patterns=f"Nightjar\n{UNUSED}\n",
              extra={"tests/fixture.txt": "plain\n", "logs/ci.log": "built\n"})
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--json") == 0
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["public_allow"] == {"file": None, "carried": 0, "lines": [], "unused": 1, "present": 0}
    assert not (g.target / ps.PUBLIC_ALLOW_FILE).exists()
    assert ps.PUBLIC_ALLOW_FILE not in _git(g.target, "ls-files").stdout
    assert "starling" not in cap.out + cap.err
    g2 = gated(tmp_path / "again", patterns=f"Nightjar\n{UNUSED}\n",
               extra={"tests/fixture.txt": "plain\n", "logs/ci.log": "built\n"})
    assert run(lp, g2, "repo", "export", "pkg", str(g2.target), "--dry-run") == 0
    out = capsys.readouterr().out
    assert (f"public allow: none of the 1 allow line(s) of {ps.PATTERNS_FILE} was used by the export's scan; "
            f"none carried, no {ps.PUBLIC_ALLOW_FILE} written") in out and "starling" not in out


def test_an_allow_line_used_only_by_the_commit_identity_is_carried(lp, tmp_path, capsys):
    # the identity is history too: an allow line exempting its address is used, so it is carried
    g = gated(tmp_path, patterns="Nightjar\nallow: ^ci@runner\\.invalid$\nallow: ^bot@ci-host\\.lan$\n",
              extra={"logs/ci.log": "built\n"})
    assert run(lp, g, "repo", "export", "pkg", str(g.target), "--author", "CI Bot <bot@ci-host.lan>",
               "--allow-author-email", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["public_allow"]["lines"] == [2, 3]
    assert data["identity"] == {"author": "CI Bot <bot@ci-host.lan>", "committer": "CI Bot <bot@ci-host.lan>",
                                "source": "--author", "noreply": False}


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
    assert data["public_allow"] == {"file": ps.PUBLIC_ALLOW_FILE, "carried": 1, "lines": [3], "unused": 0,
                                    "present": 1}
    lines = (g.target / ps.PUBLIC_ALLOW_FILE).read_text().splitlines()
    assert lines.count("allow: ^ci@runner\\.invalid$") == 1 and SCOPED in lines
