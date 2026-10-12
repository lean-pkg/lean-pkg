#!/usr/bin/env python3
"""scripts/release.sh retries the tag push and the GitHub release on transient errors and is
idempotent on re-run (#73). A tmp repository with a bare remote stands in for origin; `git` is
the real git behind a wrapper that fails the first N calls of one subcommand, `gh` is a fake
that keeps its releases in a state directory, `make check` is a no-op. Nothing reaches GitHub."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V = "1.2.3"
TAG = f"v{V}"
REAL_GIT = shutil.which("git")

GIT_WRAPPER = r'''#!/usr/bin/env python3
import os, subprocess, sys
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
sub = next((a for a in args if not a.startswith("-")), "")
with open(os.path.join(state, "git.log"), "a") as f:
    f.write(" ".join(args) + "\n")
fail = int(os.environ.get("FAKE_GIT_FAIL_" + sub.replace("-", "_"), "0") or 0)
if fail:
    counter = os.path.join(state, "git-" + sub + ".count")
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    if n < fail:
        open(counter, "w").write(str(n + 1))
        sys.stderr.write(os.environ.get("FAKE_GIT_MSG_" + sub.replace("-", "_"), "") + "\n")
        sys.exit(128)
os.execv(os.environ["REAL_GIT"], ["git"] + args)
'''

GH_FAKE = r'''#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "gh.log"), "a") as f:
    f.write(" ".join(args) + "\n")
rel = os.path.join(state, "releases.json")
releases = json.load(open(rel)) if os.path.exists(rel) else {}

def maybe_fail(key):
    fail = int(os.environ.get("FAKE_GH_FAIL_" + key, "0") or 0)
    counter = os.path.join(state, "gh-" + key + ".count")
    n = int(open(counter).read()) if os.path.exists(counter) else 0
    if n < fail:
        open(counter, "w").write(str(n + 1))
        sys.stderr.write(os.environ.get("FAKE_GH_MSG_" + key, "") + "\n")
        sys.exit(1)

if args[:2] == ["release", "view"]:
    maybe_fail("view")
    tag = args[2]
    if tag not in releases:
        sys.stderr.write("release not found\n"); sys.exit(1)
    print(releases[tag]["url"]); sys.exit(0)
if args[:2] == ["release", "create"]:
    tag = args[2]
    if tag in releases:
        sys.stderr.write("HTTP 422: Validation Failed (already_exists)\n"); sys.exit(1)
    notes = open(args[args.index("--notes-file") + 1]).read()
    record = {"url": "https://github.com/OWNER/REPO/releases/tag/" + tag,
              "title": args[args.index("--title") + 1], "notes": notes, "verify_tag": "--verify-tag" in args}
    if os.environ.get("FAKE_GH_LAND_create"):        # the create lands server-side even when it reports a failure
        releases[tag] = record
        json.dump(releases, open(rel, "w"))
    maybe_fail("create")
    releases[tag] = record
    json.dump(releases, open(rel, "w"))
    sys.exit(0)
if args[:2] == ["repo", "view"]:
    print("OWNER/REPO"); sys.exit(0)
if args[:1] == ["api"]:
    maybe_fail("api")
    print("{}"); sys.exit(0)
sys.stderr.write("fake gh: unexpected " + " ".join(args) + "\n"); sys.exit(2)
'''

CHECK_VERSIONS = f'''#!/usr/bin/env python3
print("versions: {V} (pyproject.toml, CITATION.cff, CHANGELOG.md)")
'''

CHANGELOG = f"""# Changelog

## [Unreleased]

## [{V}] - 2026-10-11

### Fixed

- A thing that was broken.
"""


def _exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "LEAN_PKG_", "FAKE_"))}
    env.update({
        "PATH": f"{tmp / 'bin'}{os.pathsep}{env.get('PATH', '')}",
        "REAL_GIT": REAL_GIT, "FAKE_STATE": str(tmp / "state"),
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.org",
        "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.org",
        "LEAN_PKG_RELEASE_RETRY_DELAY": "0",
    })
    env.update(extra)
    return env


def _setup(tmp: Path) -> Path:
    (tmp / "bin").mkdir()
    (tmp / "state").mkdir()
    _exe(tmp / "bin" / "git", GIT_WRAPPER)
    _exe(tmp / "bin" / "gh", GH_FAKE)
    _exe(tmp / "bin" / "make", "#!/bin/sh\necho \"make $*: ok (fake)\"\n")
    env = _env(tmp)
    bare, work = tmp / "origin.git", tmp / "work"
    subprocess.run([REAL_GIT, "init", "-q", "--bare", "-b", "main", str(bare)], check=True, env=env)
    subprocess.run([REAL_GIT, "init", "-q", "-b", "main", str(work)], check=True, env=env)
    (work / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "release.sh", work / "scripts" / "release.sh")
    _exe(work / "scripts" / "check-versions.py", CHECK_VERSIONS)
    (work / "CHANGELOG.md").write_text(CHANGELOG)
    g = lambda *a: subprocess.run([REAL_GIT, "-C", str(work), *a], check=True, env=env, capture_output=True)
    g("add", "-A")
    g("commit", "-q", "-m", f"release: {V}")
    g("remote", "add", "origin", str(bare))
    g("push", "-q", "-u", "origin", "main")
    (tmp / "state" / "git.log").unlink(missing_ok=True)
    return work


def _run(tmp: Path, **extra: str) -> subprocess.CompletedProcess:
    work = tmp / "work"
    return subprocess.run(["bash", str(work / "scripts" / "release.sh"), V],
                          capture_output=True, text=True, env=_env(tmp, **extra), cwd=work)


def _remote_tag(tmp: Path) -> str:
    p = subprocess.run([REAL_GIT, "-C", str(tmp / "origin.git"), "rev-parse", "-q", "--verify", f"refs/tags/{TAG}"],
                       capture_output=True, text=True, env=_env(tmp))
    return p.stdout.strip()


def _local_tag(tmp: Path) -> str:
    return subprocess.run([REAL_GIT, "-C", str(tmp / "work"), "rev-parse", f"refs/tags/{TAG}"],
                          capture_output=True, text=True, env=_env(tmp), check=True).stdout.strip()


def _releases(tmp: Path) -> dict:
    p = tmp / "state" / "releases.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _log(tmp: Path, name: str) -> list[str]:
    p = tmp / "state" / name
    return p.read_text().splitlines() if p.exists() else []


def _pushes(tmp: Path) -> list[str]:
    return [line for line in _log(tmp, "git.log") if line.startswith("push ")]


def _creates(tmp: Path) -> list[str]:
    return [line for line in _log(tmp, "gh.log") if line.startswith("release create")]


def test_clean_release_pushes_tag_creates_release_and_requests_pages(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _remote_tag(tmp_path) == _local_tag(tmp_path)
    rel = _releases(tmp_path)[TAG]
    assert rel["title"] == f"lean-pkg {V}" and rel["verify_tag"] and "A thing that was broken." in rel["notes"]
    assert f"release: {TAG} at " in r.stdout and rel["url"] in r.stdout
    assert any(line.startswith("api -X POST repos/OWNER/REPO/pages/builds") for line in _log(tmp_path, "gh.log"))


def test_tag_push_recovers_from_transient_failures(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GIT_FAIL_push="2",
             FAKE_GIT_MSG_push="kex_exchange_identification: read: Connection reset by peer\n"
                               "fatal: Could not read from remote repository.")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "release: tag push attempt 1/4 failed (transient: connection reset); retrying in 0 s" in r.stderr
    assert "tag push attempt 2/4 failed (transient:" in r.stderr
    assert len(_pushes(tmp_path)) == 3
    assert _remote_tag(tmp_path) == _local_tag(tmp_path) and TAG in _releases(tmp_path)


def test_ls_remote_transient_failure_is_retried(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GIT_FAIL_ls_remote="1", FAKE_GIT_MSG_ls_remote="fatal: unable to access: HTTP 502")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tag push attempt 1/4 failed (transient: http 502)" in r.stderr


def test_release_create_recovers_from_transient_failures(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GH_FAIL_create="3",
             FAKE_GH_MSG_create="Post \"https://api.github.com/repos/OWNER/REPO/releases\": net/http: TLS handshake timeout")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GitHub release attempt 3/4 failed (transient: tls handshake timeout); retrying in 0 s" in r.stderr
    assert len(_creates(tmp_path)) == 4 and TAG in _releases(tmp_path)


def test_release_view_http_5xx_is_retried(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GH_FAIL_view="1", FAKE_GH_MSG_view="HTTP 503: Service Unavailable")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GitHub release attempt 1/4 failed (transient: http 503)" in r.stderr


def test_tag_already_on_origin_same_object_is_skipped(tmp_path):
    work = _setup(tmp_path)
    env = _env(tmp_path)
    subprocess.run([REAL_GIT, "-C", str(work), "tag", "-a", TAG, "-m", f"lean-pkg {V}"], check=True, env=env)
    subprocess.run([REAL_GIT, "-C", str(work), "push", "-q", "origin", TAG], check=True, env=env)
    (tmp_path / "state" / "git.log").unlink(missing_ok=True)
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reusing the local tag" in r.stdout and "already on origin (same object); push skipped" in r.stdout
    assert _pushes(tmp_path) == [] and TAG in _releases(tmp_path)


def test_tag_on_origin_at_different_object_stops_without_force(tmp_path):
    _setup(tmp_path)
    bare = tmp_path / "origin.git"
    env = _env(tmp_path)
    subprocess.run([REAL_GIT, "-C", str(bare), "tag", "-a", TAG, "-m", "someone else's tag", "main"],
                   check=True, env=env)
    theirs = _remote_tag(tmp_path)
    r = _run(tmp_path)
    assert r.returncode != 0
    assert "different object" in r.stderr and "never forced" in r.stderr
    assert _remote_tag(tmp_path) == theirs
    assert _pushes(tmp_path) == [] and _creates(tmp_path) == []


def test_local_tag_at_another_commit_stops(tmp_path):
    work = _setup(tmp_path)
    env = _env(tmp_path)
    g = lambda *a: subprocess.run([REAL_GIT, "-C", str(work), *a], check=True, env=env, capture_output=True)
    g("tag", "-a", TAG, "-m", "old")
    g("commit", "-q", "--allow-empty", "-m", "later")
    g("push", "-q", "origin", "main")
    r = _run(tmp_path)
    assert r.returncode == 1 and "already exists at another commit" in r.stderr
    assert _remote_tag(tmp_path) == ""


def test_release_already_exists_is_skipped(tmp_path):
    _setup(tmp_path)
    (tmp_path / "state" / "releases.json").write_text(json.dumps(
        {TAG: {"url": f"https://github.com/OWNER/REPO/releases/tag/{TAG}"}}))
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GitHub release v1.2.3 already exists; create skipped" in r.stdout
    assert _creates(tmp_path) == []
    assert f"https://github.com/OWNER/REPO/releases/tag/{TAG}" in r.stdout


def test_release_create_422_already_exists_counts_as_done(tmp_path):
    """A create that lost a race (another create landed first) gets a 422; view re-checks: done."""
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GH_LAND_create="1", FAKE_GH_FAIL_create="1",
             FAKE_GH_MSG_create="HTTP 422: Validation Failed (already_exists)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_creates(tmp_path)) == 1 and "attempt" not in r.stderr
    assert f"https://github.com/OWNER/REPO/releases/tag/{TAG}" in r.stdout


def test_timed_out_create_that_landed_is_not_created_twice(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GH_LAND_create="1", FAKE_GH_FAIL_create="1",
             FAKE_GH_MSG_create="Post \"https://api.github.com/repos/OWNER/REPO/releases\": i/o timeout")
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_creates(tmp_path)) == 1 and TAG in _releases(tmp_path)


def test_auth_failure_is_not_retried(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, FAKE_GIT_FAIL_push="9",
             FAKE_GIT_MSG_push="git@github.com: Permission denied (publickey).\n"
                               "fatal: Could not read from remote repository.")
    assert r.returncode != 0
    assert "tag push attempt 1/4 failed (not transient; not retrying)" in r.stderr
    assert "Permission denied (publickey)." in r.stderr
    assert len(_pushes(tmp_path)) == 1 and _creates(tmp_path) == []


def test_retries_exhausted_exits_nonzero_with_last_error(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, LEAN_PKG_RELEASE_RETRIES="3", FAKE_GIT_FAIL_push="9",
             FAKE_GIT_MSG_push="ssh: connect to host github.com port 22: Connection timed out")
    assert r.returncode != 0
    assert "tag push attempt 3/3 failed (transient: timed out); giving up after 3 attempts" in r.stderr
    assert r.stderr.rstrip().endswith("Connection timed out")
    assert len(_pushes(tmp_path)) == 3 and _remote_tag(tmp_path) == "" and _creates(tmp_path) == []


def test_rerun_after_partial_failure_completes_only_the_missing_step(tmp_path):
    _setup(tmp_path)
    first = _run(tmp_path, LEAN_PKG_RELEASE_RETRIES="2", FAKE_GH_FAIL_create="9",
                 FAKE_GH_MSG_create="HTTP 502: Bad Gateway")
    assert first.returncode != 0 and "giving up after 2 attempts" in first.stderr
    tag_obj = _local_tag(tmp_path)
    assert _remote_tag(tmp_path) == tag_obj and _releases(tmp_path) == {}
    for name in ("git.log", "gh.log", "gh-create.count"):
        (tmp_path / "state" / name).unlink()
    second = _run(tmp_path)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "reusing the local tag" in second.stdout and "push skipped" in second.stdout
    assert _pushes(tmp_path) == [] and _local_tag(tmp_path) == tag_obj
    assert len(_creates(tmp_path)) == 1 and TAG in _releases(tmp_path)
    third = _run(tmp_path)
    assert third.returncode == 0 and "create skipped" in third.stdout


def test_pages_build_failure_is_a_warning(tmp_path):
    _setup(tmp_path)
    r = _run(tmp_path, LEAN_PKG_RELEASE_RETRIES="2", FAKE_GH_FAIL_api="9", FAKE_GH_MSG_api="HTTP 500: oops")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN: Pages build request failed" in r.stderr and TAG in _releases(tmp_path)


if __name__ == "__main__":
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", __file__]))
