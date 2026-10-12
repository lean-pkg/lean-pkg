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
  4   commits exactly those four files (`release: X.Y.Z`) and pushes main
  5   runs scripts/release.sh X.Y.Z (gates, tag, GitHub release, Pages build)
  7   runs scripts/record-doi.py X.Y.Z --wait SECONDS (default 900)

Resumable: re-running after a failure skips what is done. A citation commit already at
X.Y.Z is kept; a `release: X.Y.Z` commit at HEAD skips straight to the push and step 5,
which is idempotent itself. Step 7 timing out is a warning, not a failure: the release
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
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


def step4_commit(v: str) -> None:
    dirty = sorted(line[3:] for line in git("status", "--porcelain").splitlines())
    if dirty != sorted(VERSION_FILES):
        die(f"step 4: expected exactly {', '.join(VERSION_FILES)} modified, found {dirty}; not committing")
    git("add", *VERSION_FILES)
    git("commit", "-q", "-m", f"release: {v}", *trailer_args())
    say(f"step 4: committed {git('rev-parse', '--short', 'HEAD')} release: {v}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version")
    ap.add_argument("--doi-wait", type=int, default=900, metavar="SECONDS",
                    help="how long step 7 polls Zenodo for the version DOI (default 900)")
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
    ours = {f"citation: bibtex examples cite {v}", f"release: {v}"}
    ahead = git("log", "--format=%s", "origin/main..HEAD").splitlines()
    if git("rev-list", "--count", "HEAD..origin/main") != "0" or any(s not in ours for s in ahead):
        die("main differs from origin/main by more than this release's own commits (pull or push first)")

    released = git("log", "-1", "--format=%s") == f"release: {v}"
    if released:
        say(f"steps 0-4: HEAD is already `release: {v}`; resuming at the push")
    else:
        current = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
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
        step4_commit(v)

    git("push", "-q", "origin", "main")
    say(f"step 4: pushed main at {git('rev-parse', '--short', 'HEAD')}")

    say(f"step 5: scripts/release.sh {v}")
    if subprocess.run(["bash", str(ROOT / "scripts" / "release.sh"), v], cwd=ROOT).returncode != 0:
        die(f"step 5: scripts/release.sh failed (see above); fix the cause and run `make cut-release VERSION={v}` "
            "again: it resumes at the push and release.sh completes only the missing steps")

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
