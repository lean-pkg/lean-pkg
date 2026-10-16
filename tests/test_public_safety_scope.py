#!/usr/bin/env python3
"""public-safety: what the scan covers. Every case plants one made-up private name (`zorkhost`, a
blocking line of the patterns file) in a form the scanner used to miss: a package that is a
subdirectory of its repository (promotion publishes the whole repository), a tracked symbolic link's
target, commit and tag identities, annotated tag messages, branch names, UTF-16 text, binary files,
long lines and files past the size cap. Every scan here is of a throwaway repository; no registry."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from lean_pkg import public_safety as ps

from .conftest import ROOT, base_pkg

TOKEN = "zorkhost"
AT = "@"   # addresses are built at run time: this file is scanned too, and must hold none
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "Pub", "GIT_AUTHOR_EMAIL": "pub@example.org",
       "GIT_COMMITTER_NAME": "Pub", "GIT_COMMITTER_EMAIL": "pub@example.org"}


def g(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env or ENV).stdout


def new_repo(path: Path, patterns: str = f"{TOKEN}\n", pkg_dir: str = "") -> Path:
    """A repository with README.md and a git-ignored patterns file in pkg_dir (the package)."""
    path.mkdir(parents=True)
    g(path, "init", "-q", "-b", "main")
    pkg = path / pkg_dir if pkg_dir else path
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / ".gitignore").write_text(ps.PATTERNS_FILE + "\n")
    (pkg / ps.PATTERNS_FILE).write_text(patterns)
    (pkg / "README.md").write_text("public\n")
    return path


def commit(repo: Path, msg: str = "c", env: dict | None = None) -> None:
    g(repo, "add", "-A")
    g(repo, "commit", "-qm", msg, env=env)


def scan(root: Path, **kw) -> dict:
    kw.setdefault("history", True)
    return ps.scan({"name": "x"}, root, **kw)


def token_free(result: dict) -> bool:
    return TOKEN not in json.dumps(result).lower()


def test_control_plain_file_fires(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "a.txt").write_text(f"see {TOKEN}\n")
    commit(r)
    res = scan(r)
    assert not res["ok"] and [h["path"] for h in res["hits"]] == ["a.txt"] and token_free(res)


# finding 1: a package in a subdirectory -- promotion publishes the whole repository

def mono(tmp_path: Path) -> Path:
    top = new_repo(tmp_path / "mono", pkg_dir="lean")
    (top / "lean" / "Main.lean").write_text("theorem t : True := trivial\n")
    commit(top, "package")
    (top / "ops").mkdir()
    (top / "ops" / "hosts.txt").write_text(f"ssh {TOKEN}\n")
    commit(top, f"deploy to {TOKEN}")
    return top


def test_subdirectory_whole_repo_scan_covers_tree_history_and_messages(tmp_path):
    top = mono(tmp_path)
    narrow = scan(top / "lean")
    assert narrow["ok"] and narrow["scope"] == "package"
    res = scan(top / "lean", whole_repo=True)
    assert not res["ok"] and res["scope"] == "repository" and res["package_prefix"] == "lean/"
    assert {(h["where"], h["path"]) for h in res["hits"]} == {("tree", "ops/hosts.txt"),
                                                              ("history", "commit-message")}
    assert ps.SCOPE_NOTE in ps.report_lines("x", res)[0]
    assert token_free(res)


def test_subdirectory_old_blob_outside_package_is_a_history_hit(tmp_path):
    top = mono(tmp_path)
    (top / "ops" / "hosts.txt").write_text("gone\n")
    commit(top, "scrub")
    res = scan(top / "lean", whole_repo=True)
    assert ("history", "ops/hosts.txt") in {(h["where"], h["path"]) for h in res["hits"]}


def test_subdirectory_scoped_allow_lines_stay_package_relative(tmp_path):
    # `allow: docs/**: ...` is about the package's docs/, never the repository's top-level docs/
    top = new_repo(tmp_path / "mono", patterns=f"{TOKEN}\nallow: docs/**: ^{TOKEN}$\n", pkg_dir="lean")
    for d in (top / "lean" / "docs", top / "docs"):
        d.mkdir(parents=True)
        (d / "a.md").write_text(f"{TOKEN}\n")
    commit(top)
    res = scan(top / "lean", whole_repo=True)
    assert [h["path"] for h in res["hits"]] == ["docs/a.md"]          # the top-level one only
    assert scan(top / "lean")["ok"]


def test_cli_public_safety_defaults_to_the_repository_scope(lp, tmp_path, capsys):
    top = mono(tmp_path)
    reg = tmp_path / "lean-packages.yaml"
    reg.write_text(yaml.safe_dump({"version": 2, "packages": {"pkg": base_pkg("pkg", top / "lean", "gate-pending")}},
                                  sort_keys=False))
    assert lp.main(["--registry", str(reg), "public-safety", "pkg"]) == 1
    out = capsys.readouterr().out
    assert ps.SCOPE_NOTE in out and TOKEN not in out
    assert lp.main(["--registry", str(reg), "public-safety", "pkg", "--package-only"]) == 0


# finding 2: tracked symbolic links

def test_symlink_target_is_scanned_in_tree(tmp_path):
    r = new_repo(tmp_path / "r")
    os.symlink(f"/srv/{TOKEN}/private/data", r / "data")
    commit(r)
    res = scan(r)
    assert [(h["where"], h["path"]) for h in res["hits"]] == [("tree", "data")] and token_free(res)


def test_symlink_target_is_scanned_in_history(tmp_path):
    r = new_repo(tmp_path / "r")
    os.symlink(f"/srv/{TOKEN}/x", r / "data")
    commit(r)
    (r / "data").unlink()
    commit(r, "drop link")
    res = scan(r)
    assert [(h["where"], h["path"]) for h in res["hits"]] == [("history", "data")]


def test_export_mode_scans_a_symlink_to_a_directory(tmp_path):
    d = tmp_path / "export"
    d.mkdir()
    (d / "README.md").write_text("ok\n")
    os.symlink(f"/srv/{TOKEN}", d / "data")
    res = ps.scan({"name": "x"}, d, export=True, patterns_root=new_repo(tmp_path / "r"))
    assert [h["path"] for h in res["hits"]] == ["data"]


# finding 3: identities, tags, branch names

def test_commit_author_and_committer_identities_are_scanned(tmp_path):
    r = new_repo(tmp_path / "r")
    commit(r, env={**ENV, "GIT_AUTHOR_NAME": f"le on {TOKEN}", "GIT_COMMITTER_EMAIL": f"le@{TOKEN}.lan"})
    res = scan(r)
    hits = [h for h in res["hits"] if h["path"] == "commit-identity"]
    assert {h["pattern"] for h in hits} == {".lean-pkg-private-patterns:1", "identity-email"}
    assert len({h["excerpt"] for h in hits}) >= 2 and token_free(res)


@pytest.mark.parametrize("addr", ["le@quuxbox", f"le{AT}quuxbox.lan", f"le{AT}quuxbox.local", f"le{AT}quuxbox.internal"])
def test_host_like_identity_email_is_a_hit_without_a_pattern(tmp_path, addr):
    # `user@hostname` or a local TLD names a machine: a hit even with no pattern naming it
    r = new_repo(tmp_path / "r", patterns="# none\n")
    commit(r, env={**ENV, "GIT_AUTHOR_EMAIL": addr})
    res = scan(r)
    assert [(h["path"], h["pattern"]) for h in res["hits"]] == [("commit-identity", "identity-email")]
    assert "quuxbox" not in json.dumps(res)


def test_ordinary_identity_is_declared_by_use_a_host_like_one_is_not(tmp_path):
    # an ordinary dotted address used in commits is declared, in the identity and in content alike
    r = new_repo(tmp_path / "r", patterns="# none\n")
    (r / "AUTHORS").write_text(f"someone{AT}uni.edu\n")
    commit(r, env={**ENV, "GIT_AUTHOR_EMAIL": f"someone{AT}uni.edu"})
    assert scan(r)["ok"]
    # a host-like one is not: the identity and its use in content are both hits
    (r / "AUTHORS").write_text(f"le{AT}quuxbox.lan\n")
    commit(r, "authors", env={**ENV, "GIT_AUTHOR_EMAIL": f"le{AT}quuxbox.lan"})
    res = scan(r)
    assert {(h["path"], h["pattern"]) for h in res["hits"]} == {("AUTHORS", "email"),
                                                                ("commit-identity", "identity-email")}
    # declared explicitly: an allow line, or a consent entry
    (r / ps.PATTERNS_FILE).write_text("allow: ^le@quuxbox\\.lan$\n")
    assert scan(r)["ok"]
    (r / ps.PATTERNS_FILE).write_text("# none\n")
    assert ps.scan({"name": "x", "consent": [{"who": f"le{AT}quuxbox.lan"}]}, r)["ok"]


def test_github_noreply_identities_stay_allowed(tmp_path):
    r = new_repo(tmp_path / "r", patterns="# none\n")
    commit(r, env={**ENV, "GIT_AUTHOR_EMAIL": "1234+someone@users.noreply.github.com",
                   "GIT_COMMITTER_NAME": "GitHub", "GIT_COMMITTER_EMAIL": "noreply@github.com"})
    assert scan(r)["ok"]


def test_annotated_tag_message_and_tagger_are_scanned(tmp_path):
    r = new_repo(tmp_path / "r")
    commit(r)
    g(r, "tag", "-a", "v1", "-m", f"built on {TOKEN}")
    g(r, "tag", "-a", "v2", "-m", "release", env={**ENV, "GIT_COMMITTER_EMAIL": f"le@{TOKEN}.lan"})
    res = scan(r)
    assert {h["path"] for h in res["hits"]} == {"tag-message", "tag-identity"} and token_free(res)


def test_branch_and_tag_names_are_scanned(tmp_path):
    r = new_repo(tmp_path / "r")
    commit(r)
    g(r, "branch", f"{TOKEN}-wip")
    res = scan(r)
    assert [h["path"] for h in res["hits"]] == ["ref-name"] and token_free(res)


# finding 4: no silent partial scan

def test_utf16_text_with_and_without_bom_is_decoded(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "bom.txt").write_bytes(f"host: {TOKEN}\n".encode("utf-16"))
    (r / "le.txt").write_bytes(f"host: {TOKEN}\n".encode("utf-16-le"))
    (r / "u32.txt").write_bytes(f"host: {TOKEN}\n".encode("utf-32"))
    commit(r)
    res = scan(r)
    assert sorted(h["path"] for h in res["hits"]) == ["bom.txt", "le.txt", "u32.txt"]
    assert res["skipped"] == []


def test_binary_file_printable_runs_are_scanned_and_listed(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "paper.pdf").write_bytes(b"%PDF-1.7\n\x00\x01" + f"/Author ({TOKEN})\n".encode())
    (r / "blank.bin").write_bytes(b"\x00\x01\x02" * 10)
    commit(r)
    res = scan(r)
    assert [(h["path"], h["line"]) for h in res["hits"]] == [("paper.pdf", None)]
    assert {(s["path"], s["reason"]) for s in res["skipped"]} == {("paper.pdf", "binary"), ("blank.bin", "binary")}
    assert res["skipped_blocking"] == 0
    assert "2 file(s)/blob(s) not fully scanned: 2 binary" in ps.report_lines("x", res)[0]


def test_long_line_is_scanned_whole_in_overlapping_chunks(tmp_path):
    r = new_repo(tmp_path / "r")
    (r / "data.json").write_text('{"pad": "' + "x" * 20000 + f'", "host": "{TOKEN}"}}\n')
    # straddling a chunk boundary: starts 4 characters before the second chunk would end
    edge = ps.LINE_CHUNK - ps.LINE_OVERLAP + ps.LINE_CHUNK - 4
    (r / "edge.txt").write_text("y" * edge + f" {TOKEN} " + "y" * 30000 + "\n")
    commit(r)
    res = scan(r)
    assert sorted((h["path"], h["line"]) for h in res["hits"]) == [("data.json", 1), ("edge.txt", 1)]


def test_bytes_past_the_cap_are_a_blocking_unscanned_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "MAX_SCAN_BYTES", 1000)
    r = new_repo(tmp_path / "r")
    (r / "big.log").write_text("ok\n" * 1000 + f"{TOKEN}\n")
    commit(r)
    res = scan(r, history=False)
    assert not res["ok"] and [(h["path"], h["pattern"]) for h in res["hits"]] == [("big.log", "unscanned")]
    assert res["skipped"] == [{"where": "tree", "path": "big.log", "reason": "over the 1000-byte scan cap",
                               "unscanned_bytes": 2009, "allowed": False}]
    assert res["skipped_blocking"] == 1
    # explicitly allowed by the patterns file: listed, not blocking
    (r / ps.PATTERNS_FILE).write_text(f"{TOKEN}\nallow: big.log: ^unscanned$\n")
    res = scan(r, history=False)
    assert res["ok"] and res["skipped"][0]["allowed"] is True and res["skipped_blocking"] == 0


def test_history_blob_past_the_cap_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "MAX_SCAN_BYTES", 1000)
    r = new_repo(tmp_path / "r")
    (r / "big.log").write_text("ok\n" * 1000)
    commit(r)
    (r / "big.log").unlink()
    commit(r, "drop")
    res = scan(r)
    assert [(h["where"], h["path"], h["pattern"]) for h in res["hits"]] == [("history", "big.log", "unscanned")]
    assert res["hits"][0]["commit"]


# finding 5: scripts/private-boundary-check.sh

SCRIPT = str(ROOT / "scripts" / "private-boundary-check.sh")


def boundary(repo: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    extra = tmp_path / "extra-forbidden"
    extra.write_text(TOKEN + "\n")
    return subprocess.run(["bash", SCRIPT, *args, str(repo)], capture_output=True, text=True,
                          env={**ENV, "PRIVATE_BOUNDARY_EXTRA": str(extra)})


def test_boundary_control_and_case_variants(tmp_path):
    r = new_repo(tmp_path / "r", patterns="")
    commit(r)
    assert boundary(r, tmp_path).returncode == 0
    (r / "a.txt").write_text(f"ssh {TOKEN.upper()}\n")        # the re-check used to be case-sensitive
    assert boundary(r, tmp_path).returncode == 1


def test_boundary_tree_binary_and_symlink(tmp_path):
    r = new_repo(tmp_path / "r", patterns="")
    (r / "paper.pdf").write_bytes(b"%PDF\x00\x01/Author (" + TOKEN.encode() + b")\n")
    commit(r)
    p = boundary(r, tmp_path)
    assert p.returncode == 1 and "paper.pdf:binary:" in p.stderr
    (r / "paper.pdf").unlink()
    os.symlink(f"/srv/{TOKEN}", r / "data")
    assert boundary(r, tmp_path).returncode == 1


@pytest.mark.parametrize("plant", ["identity", "tag", "binary"])
def test_boundary_history_identities_tags_binary(tmp_path, plant):
    r = new_repo(tmp_path / "r", patterns="")
    if plant == "identity":
        commit(r, env={**ENV, "GIT_AUTHOR_EMAIL": f"le@{TOKEN}.lan"})
    elif plant == "tag":
        commit(r)
        g(r, "tag", "-a", "v1", "-m", f"built on {TOKEN}")
    else:
        (r / "x.bin").write_bytes(b"\x00\x01" + TOKEN.encode())
        commit(r)
        (r / "x.bin").unlink()
        commit(r, "drop")
    p = boundary(r, tmp_path, "--history")
    assert p.returncode == 1, p.stdout + p.stderr
    assert {"identity": "commit-identity:", "tag": "tag-object@", "binary": ":binary:"}[plant] in p.stderr


def test_fresh_repo_export_commit_identity_is_checked(tmp_path, monkeypatch):
    from lean_pkg import fresh_repo
    r = new_repo(tmp_path / "r")
    sc = ps.Scanner({"name": "x"}, r, r)
    for k, v in (("GIT_AUTHOR_NAME", "Exporter"), ("GIT_AUTHOR_EMAIL", "exporter@example.com"),
                 ("GIT_COMMITTER_NAME", "Exporter"), ("GIT_COMMITTER_EMAIL", "exporter@example.com")):
        monkeypatch.setenv(k, v)
    assert fresh_repo.export_identity_hits(sc) == []
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", f"someone{AT}uni.edu")      # ordinary: declared by use
    assert fresh_repo.export_identity_hits(sc) == []
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", f"le@{TOKEN}.lan")
    hits = fresh_repo.export_identity_hits(sc)
    assert {h["path"] for h in hits} == {"export-commit-identity"} and len(hits) >= 1
    assert TOKEN not in json.dumps(hits)
