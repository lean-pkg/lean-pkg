#!/usr/bin/env python3
"""Cut a release end to end: scripts/cut-release.py X.Y.Z [--doi-wait SECONDS | --no-doi]

Runs the RELEASING.md checklist from `## [Unreleased]` to the recorded Zenodo DOI:

  0   lists the `### Added` entries being released (the documentation gap pass is a
      judgment; `make check` enforces its floor, the subcommand-coverage test)
  2a  points the BibTeX examples in README.md and docs/index.md at X.Y.Z and commits
      them on their own (`citation: bibtex examples cite X.Y.Z`)
  3   runs `make public-safety` and refuses unless it reports 0 blocking, 0 warnings
  1,2 moves `## [Unreleased]` to `## [X.Y.Z] - today`, appends the public-safety
      summary line, and sets the version in pyproject.toml, CITATION.cff
      (with date-released) and src/lean_pkg/__init__.py
  4   runs `make check` on that tree (the gate step 5 runs) and, when it fails,
      restores the four files and stops: nothing is committed or pushed past 2a
  4   commits exactly those four files (`release: X.Y.Z`) and pushes main (retried
      on transient errors like release.sh)
  5   runs scripts/release.sh X.Y.Z (gates, tag, GitHub release, Pages build)
  7   runs scripts/record-doi.py X.Y.Z --wait SECONDS (default 300)

Resumable: re-running after a failure skips what is done. A citation commit already at
X.Y.Z is kept. Once the version files say X.Y.Z and a `release: X.Y.Z` commit is in HEAD's
history, a re-run skips straight to the push and step 5, which is idempotent itself: the
tag goes on HEAD, so fixes committed on top of the release commit after a failed step 5
are part of the release. An unpushed `citation: Zenodo version DOI for X.Y.Z` commit (a
step 7 push that failed) is pushed, and step 5 is skipped when origin already has the tag
at the commit below it. Step 7 timing out is a warning, not a failure: the release
exists; `make record-doi VERSION=X.Y.Z` finishes it later.

LEAN_PKG_COMMIT_TRAILER adds `Key: value` trailers (one per line, e.g.
`Co-Authored-By: Name <email>`) to every commit it makes, step 7's included.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "cut-release"
VERSION_FILES = ("CHANGELOG.md", "CITATION.cff", "pyproject.toml", "src/lean_pkg/__init__.py")
CITATION_FILES = ("README.md", "docs/index.md")
BIBTEX_RE = re.compile(r"^(  version = \{)(\d+\.\d+\.\d+)(\},)$", re.M)
SAFETY_RE = re.compile(r"public-safety for \S+: (\d+) hit\(s\) \((\d+) tree, (\d+) history\); "
                       r"(\d+) blocking, (\d+) warning\(s\)")


def say(msg: str) -> None:
    print(f"cut-release: {msg}", flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"cut-release: {msg}", file=sys.stderr)
    sys.exit(code)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.rstrip("\n")  # never lstrip: porcelain starts with a space

TRAILER_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*: \S.*")


def trailer_args() -> list[str]:
    """`git commit --trailer` arguments from LEAN_PKG_COMMIT_TRAILER: one `Key: value` per
    line, e.g. `Co-Authored-By: Name <email>`. Checked before anything is written, so a
    malformed value refuses the run instead of landing in a commit message on main.
    (The same function is in scripts/cut-release.py and scripts/record-doi.py.)"""
    lines = [x.strip() for x in os.environ.get("LEAN_PKG_COMMIT_TRAILER", "").splitlines() if x.strip()]
    bad = [x for x in lines if not TRAILER_RE.fullmatch(x)]
    if bad:
        die(f"LEAN_PKG_COMMIT_TRAILER: not a `Key: value` trailer line: {bad[0]!r}", 2)
    return [a for x in lines for a in ("--trailer", x)]


# Failure classification for `git push`, the same markers as scripts/release.sh (#73): a permanent
# refusal is checked first (an SSH auth failure also prints "Could not read from remote repository").
PERMANENT_MARKERS = ("permission denied", "authentication failed", "bad credentials", "repository not found",
                     "[rejected]", "[remote rejected]", "already exists", "http 401", "http 403", "http 404",
                     "http 422")
TRANSIENT_MARKERS = ("connection reset", "kex_exchange_identification", "could not read from remote repository",
                     "tls handshake timeout", "i/o timeout", "timed out", "timeout", "connection closed by",
                     "connection refused", "the remote end hung up unexpectedly", "early eof", "broken pipe",
                     "could not resolve host", "temporary failure in name resolution", "network is unreachable",
                     "ssh: connect to host")
TRANSIENT_HTTP_RE = re.compile(r"(returned error:|http|status)\s*5\d\d(?!\d)")


def transient_reason(out: str) -> str | None:
    """The marker that makes a failed push's output transient, None when it is permanent or unknown.
    (The same function is in scripts/cut-release.py and scripts/record-doi.py.)"""
    low = out.lower()
    if any(m in low for m in PERMANENT_MARKERS):
        return None
    m = next((m for m in TRANSIENT_MARKERS if m in low), None) or TRANSIENT_HTTP_RE.search(low)
    return m if isinstance(m, str) or m is None else m.group(0)


def push_main() -> tuple[bool, str]:
    """`git push origin main`, retried on transient errors like scripts/release.sh: LEAN_PKG_RELEASE_RETRIES
    attempts (default 4), waiting LEAN_PKG_RELEASE_RETRY_DELAY s (default 10) doubled per retry.
    (ok, the last attempt's output). (The same function is in scripts/cut-release.py and scripts/record-doi.py.)"""
    def env_int(k: str, default: int, floor: int) -> int:
        v = os.environ.get(k, "")
        return int(v) if v.isdigit() and int(v) >= floor else default
    retries = env_int("LEAN_PKG_RELEASE_RETRIES", 4, 1)
    delay = env_int("LEAN_PKG_RELEASE_RETRY_DELAY", 10, 0)
    for n in range(1, retries + 1):
        r = subprocess.run(["git", "push", "-q", "origin", "main"], cwd=ROOT, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0:
            return True, out
        reason = transient_reason(out)
        if reason is None or n == retries:
            why = "not transient; not retrying" if reason is None else f"transient: {reason}; giving up"
            print(f"{NAME}: push attempt {n}/{retries} failed ({why}):\n{out}", file=sys.stderr)
            return False, out
        wait = delay * (1 << (n - 1))
        print(f"{NAME}: push attempt {n}/{retries} failed (transient: {reason}); retrying in {wait} s",
              file=sys.stderr, flush=True)
        time.sleep(wait)
    return False, ""


def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def unreleased(chg: str) -> tuple[int, int]:
    """Start and end offsets of the body of `## [Unreleased]`."""
    m = re.search(r"^## \[Unreleased\]\n", chg, re.M)
    if not m:
        die("CHANGELOG.md has no `## [Unreleased]` heading")
    nxt = re.search(r"^## \[", chg[m.end():], re.M)
    return m.end(), m.end() + (nxt.start() if nxt else len(chg) - m.end())


def step0_added(chg: str) -> None:
    s, e = unreleased(chg)
    m = re.search(r"^### Added\n(.*?)(?=^### |\Z)", chg[s:e], re.M | re.S)
    if m and m.group(1).strip():
        say("step 0: these `### Added` entries need a documented home (RELEASING.md step 0):")
        print(m.group(1).strip())
    else:
        say("step 0: no `### Added` entries; nothing new needs a documented home")


def step2a_citation(v: str) -> None:
    changed = []
    for f in CITATION_FILES:
        p = ROOT / f
        text = p.read_text()
        found = BIBTEX_RE.findall(text)
        if len(found) != 1:
            die(f"step 2a: expected one `  version = {{X.Y.Z}},` line in {f}, found {len(found)}")
        if found[0][1] != v:
            p.write_text(BIBTEX_RE.sub(rf"\g<1>{v}\g<3>", text))
            changed.append(f)
    if not changed:
        say(f"step 2a: BibTeX examples already cite {v}")
        return
    git("add", *changed)
    git("commit", "-q", "-m", f"citation: bibtex examples cite {v}", *trailer_args())
    say(f"step 2a: committed {git('rev-parse', '--short', 'HEAD')} citation: bibtex examples cite {v}")


def step3_public_safety() -> str:
    say("step 3: make public-safety (a fresh clone, history included, --strict)")
    r = subprocess.run(["make", "public-safety"], cwd=ROOT, capture_output=True, text=True)
    out = r.stdout + r.stderr
    m = SAFETY_RE.search(out)
    if r.returncode != 0 or not m or any(int(x) for x in m.groups()):
        print(out, file=sys.stderr)
        die("step 3: public-safety is not clean; fix the hit in the tree (or report a real private string) "
            "and run again. Nothing is tagged.")
    hits, tree, hist, block, warn = m.groups()
    return (f"public-safety (make public-safety, --strict, history): {hits} hit(s) ({tree} tree, "
            f"{hist} history); {block} blocking, {warn} warning(s)")


def steps12_versions(v: str, today: str, safety: str) -> None:
    p = ROOT / "CHANGELOG.md"
    chg = p.read_text()
    s, e = unreleased(chg)
    body = chg[s:e].strip("\n")
    p.write_text(chg[:s] + f"\n## [{v}] - {today}\n\n{body}\n\n{safety}\n\n" + chg[e:])

    def sub(f: str, pattern: str, repl: str) -> None:
        q = ROOT / f
        text, n = re.subn(pattern, repl, q.read_text(), count=1, flags=re.M)
        if n != 1:
            die(f"steps 1-2: no `{pattern}` line in {f}")
        q.write_text(text)

    sub("pyproject.toml", r'^version = "[^"]*"$', f'version = "{v}"')
    sub("CITATION.cff", r'^version: "[^"]*"$', f'version: "{v}"')
    sub("CITATION.cff", r'^date-released: "[^"]*"$', f'date-released: "{today}"')
    sub("src/lean_pkg/__init__.py", r'^__version__ = "[^"]*"$', f'__version__ = "{v}"')
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check-versions.py")],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or f"versions: {v} " not in r.stdout:
        print(r.stdout + r.stderr, file=sys.stderr)
        die("steps 1-2: the version files do not agree")
    say(f"steps 1-2: {r.stdout.strip()}")


def step4_check(v: str) -> None:
    """`make check` on the tree the release commit will hold, before anything is committed or pushed:
    a failing gate must not leave origin/main at X.Y.Z without a tag."""
    say("step 4: make check on the release tree (before the release commit; step 5 runs it again)")
    if subprocess.run(["make", "check"], cwd=ROOT).returncode != 0:
        git("checkout", "--", *VERSION_FILES)
        die(f"step 4: make check failed (see above); the version files are restored, nothing is committed past "
            f"step 2a and nothing is pushed. Fix the cause on main (a local `citation: bibtex examples cite {v}` "
            f"commit goes along with your fix's push), then run `make cut-release VERSION={v}` again")


def step4_commit(v: str) -> None:
    dirty = sorted(line[3:] for line in git("status", "--porcelain").splitlines())
    if dirty != sorted(VERSION_FILES):
        die(f"step 4: expected exactly {', '.join(VERSION_FILES)} modified, found {dirty}; not committing")
    git("add", *VERSION_FILES)
    git("commit", "-q", "-m", f"release: {v}", *trailer_args())
    say(f"step 4: committed {git('rev-parse', '--short', 'HEAD')} release: {v}")


def remote_tag_commit(v: str) -> str | None:
    """The commit origin's tag vX.Y.Z points at (peeled), None when origin has no such tag."""
    out = git("ls-remote", "--tags", "origin", f"refs/tags/v{v}", f"refs/tags/v{v}^{{}}")
    refs = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        refs[ref] = sha
    return refs.get(f"refs/tags/v{v}^{{}}") or refs.get(f"refs/tags/v{v}")


def is_ancestor(a: str, b: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version")
    ap.add_argument("--doi-wait", type=int, default=300, metavar="SECONDS",
                    help="how long step 7 polls Zenodo for the version DOI (default 300)")
    ap.add_argument("--no-doi", action="store_true", help="stop after step 5; record the DOI later")
    a = ap.parse_args()
    v = a.version
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        die("usage: scripts/cut-release.py X.Y.Z [--doi-wait SECONDS | --no-doi]", 2)
    trailer_args()                    # refuse a malformed LEAN_PKG_COMMIT_TRAILER before anything is written

    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        die("not on main")
    if git("status", "--porcelain"):
        die("working tree not clean")
    git("fetch", "-q", "--no-tags", "origin")
    # Resumable: HEAD may be ahead of origin/main only by this release's own commits.
    doi_commit = f"citation: Zenodo version DOI for {v}"
    ours = {f"citation: bibtex examples cite {v}", f"release: {v}", doi_commit}
    ahead = git("log", "--format=%s", "origin/main..HEAD").splitlines()
    if git("rev-list", "--count", "HEAD..origin/main") != "0" or any(s not in ours for s in ahead):
        die("main differs from origin/main by more than this release's own commits (pull or push first)")

    current = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    released = current == v and f"release: {v}" in git("log", "--format=%s", "HEAD").splitlines()
    skip_step5 = False
    if released:
        head = git("rev-parse", "HEAD")
        tagged = remote_tag_commit(v)
        if tagged and tagged != head:
            since = git("log", "--format=%s", f"{tagged}..HEAD").splitlines() if is_ancestor(tagged, head) else None
            if since is None or any(s != doi_commit for s in since):
                die(f"v{v} is already tagged on origin at {tagged[:12]} and HEAD has other commits since; "
                    f"nothing to resume ({v} is not above the current version {current})")
            skip_step5 = True
        rel = next(h for h, _, s in (x.partition(" ") for x in git("log", "--format=%h %s", "HEAD").splitlines())
                   if s == f"release: {v}")
        extra = git("rev-list", "--count", f"{rel}..HEAD")
        say(f"steps 0-4: the version files already say {v} and `release: {v}` is {rel}"
            + (f" ({extra} commit(s) on top of it)" if extra != "0" else " at HEAD") + "; resuming at the push")
    else:
        if vtuple(v) <= vtuple(current):
            die(f"{v} is not above the current version {current}")
        chg = (ROOT / "CHANGELOG.md").read_text()
        s, e = unreleased(chg)
        if not chg[s:e].strip():
            die("`## [Unreleased]` is empty; nothing to release")
        step0_added(chg)
        step2a_citation(v)
        safety = step3_public_safety()
        say(f"step 3: {safety}")
        steps12_versions(v, dt.date.today().isoformat(), safety)
        step4_check(v)
        step4_commit(v)

    if not push_main()[0]:
        die(f"step 4: could not push main (see above); the commits are local. Run `make cut-release VERSION={v}` "
            "again once the remote is reachable: it resumes at the push")
    say(f"step 4: pushed main at {git('rev-parse', '--short', 'HEAD')}")

    if skip_step5:
        say(f"step 5: skipped; origin already has v{v} at the commit below HEAD's `{doi_commit}`")
    else:
        say(f"step 5: scripts/release.sh {v}")
        if subprocess.run(["bash", str(ROOT / "scripts" / "release.sh"), v], cwd=ROOT).returncode != 0:
            die(f"step 5: scripts/release.sh failed (see above); fix the cause (commit and push a fix on main if "
                f"one is needed) and run `make cut-release VERSION={v}` again: it resumes at the push, the tag goes "
                "on HEAD, and release.sh completes only the missing steps")

    if a.no_doi:
        say(f"step 7: skipped (--no-doi); run `make record-doi VERSION={v}` once Zenodo has archived v{v}")
        return 0
    say(f"step 7: waiting up to {a.doi_wait} s for the Zenodo version DOI")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "record-doi.py"), v, "--wait", str(a.doi_wait)],
                       cwd=ROOT)
    if r.returncode != 0:
        say(f"WARN: step 7 did not finish; the release stands. Run `make record-doi VERSION={v}` later")
        return 0
    say(f"done: v{v} released and its DOI recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
