#!/usr/bin/env python3
"""Record a release's Zenodo version DOI: scripts/record-doi.py X.Y.Z [--wait SECONDS]

RELEASING.md step 7. Zenodo archives each GitHub release under the concept DOI in
CITATION.cff (`doi:`). This finds the version record for vX.Y.Z, checks that it is a
version of that concept and points at the tag, then lists its DOI under `identifiers:`
in CITATION.cff and adds a `Zenodo version DOI:` line to the X.Y.Z section of
CHANGELOG.md, in one commit (`citation: Zenodo version DOI for X.Y.Z`) pushed to main.

Idempotent: when both files already carry the DOI it changes nothing. --wait polls
Zenodo (every 30 s) until the record appears or the time is spent; the archive usually
lands within minutes of the GitHub release. LEAN_PKG_ZENODO_API overrides the API base
(default https://zenodo.org/api); LEAN_PKG_ZENODO_RECORDS_FILE reads the search pages
from JSON files instead (tests). LEAN_PKG_COMMIT_TRAILER adds `Key: value` trailers
(one per line, e.g. `Co-Authored-By: Name <email>`) to the commit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLL = 30
REPO_URL = "https://github.com/lean-pkg/lean-pkg"


def die(msg: str, code: int = 1) -> None:
    print(f"record-doi: {msg}", file=sys.stderr)
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


def concept_doi(cff: str) -> str:
    m = re.search(r"^doi:\s*\"?(10\.5281/zenodo\.\d+)\"?\s*$", cff, re.M)
    if not m:
        die("CITATION.cff has no Zenodo concept DOI in `doi:`")
    return m.group(1)


def search(concept: str):
    """Every version record under the concept, a page at a time. Anonymous requests get at
    most 25 per page and Zenodo lists versions oldest first whatever `sort` says, so the
    newest release is on the last page: every next-page link is followed."""
    path = os.environ.get("LEAN_PKG_ZENODO_RECORDS_FILE")
    while path:                       # tests: a chain of page files linked by links.next
        page = json.loads(Path(path).read_text())
        yield from page["hits"]["hits"]
        path = page.get("links", {}).get("next")
    if os.environ.get("LEAN_PKG_ZENODO_RECORDS_FILE"):
        return
    base = os.environ.get("LEAN_PKG_ZENODO_API", "https://zenodo.org/api").rstrip("/")
    recid = concept.rsplit(".", 1)[1]
    url = f"{base}/records?q=conceptrecid:{recid}&all_versions=true&size=25"
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "lean-pkg-record-doi"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                page = json.load(r)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500 and e.code != 429:     # a refused request does not get better by waiting
                die(f"Zenodo refused the search (HTTP {e.code}): {e.read()[:300].decode(errors='replace')}")
            raise
        yield from page["hits"]["hits"]
        url = page.get("links", {}).get("next")


def find_version(v: str, concept: str) -> str | None:
    """The version DOI of vX.Y.Z under this concept, None while Zenodo has not archived it."""
    for rec in search(concept):
        if rec.get("metadata", {}).get("version") != f"v{v}":
            continue
        if rec.get("conceptdoi") != concept:
            die(f"Zenodo record for v{v} belongs to {rec.get('conceptdoi')}, not {concept}")
        related = [x.get("identifier", "") for x in rec["metadata"].get("related_identifiers", [])]
        if f"{REPO_URL}/tree/v{v}" not in related:
            die(f"Zenodo record {rec.get('doi')} does not point at {REPO_URL}/tree/v{v}: {related}")
        return rec["doi"]
    return None


def add_to_cff(cff: str, v: str, doi: str) -> str:
    if f"value: {doi}\n" in cff:
        return cff
    entry = f"  - type: doi\n    value: {doi}\n    description: Version DOI for {v}\n"
    # After the last identifier entry: the block ends at the first top-level key after `identifiers:`.
    m = re.search(r"^identifiers:\n((?:[ -].*\n)+)", cff, re.M)
    if not m:
        die("CITATION.cff has no `identifiers:` list")
    return cff[:m.end()] + entry + cff[m.end():]


def add_to_changelog(chg: str, v: str, doi: str) -> str:
    if doi in chg:
        return chg
    m = re.search(rf"^## \[{re.escape(v)}\] .*\n", chg, re.M)
    if not m:
        die(f"CHANGELOG.md has no `## [{v}]` section")
    nxt = re.search(r"^## \[", chg[m.end():], re.M)
    end = m.end() + (nxt.start() if nxt else len(chg) - m.end())
    body = chg[m.end():end].rstrip("\n")
    line = f"Zenodo version DOI: [{doi}](https://doi.org/{doi})"
    return chg[:m.end()] + body + "\n\n" + line + "\n\n" + chg[end:].lstrip("\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("version")
    ap.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                    help="poll Zenodo this long for the version record (default 0: ask once)")
    a = ap.parse_args()
    v = a.version
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        die("usage: scripts/record-doi.py X.Y.Z [--wait SECONDS]", 2)
    trailers = trailer_args()

    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        die("not on main")
    if git("status", "--porcelain"):
        die("working tree not clean")
    git("fetch", "-q", "--no-tags", "origin")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        die("main differs from origin/main (pull or push first)")

    cff_path, chg_path = ROOT / "CITATION.cff", ROOT / "CHANGELOG.md"
    cff, chg = cff_path.read_text(), chg_path.read_text()
    concept = concept_doi(cff)

    deadline = time.monotonic() + a.wait
    while True:
        try:
            doi = find_version(v, concept)
        except OSError as e:          # network or HTTP error: transient, poll again while time remains
            doi, err = None, e
        else:
            err = None
        if doi:
            break
        left = deadline - time.monotonic()
        if left <= 0:
            why = f" (last error: {err})" if err else ""
            die(f"Zenodo has not archived v{v} under {concept} yet{why}; "
                f"run `make record-doi VERSION={v}` again later")
        print(f"record-doi: v{v} not on Zenodo yet; polling again in {POLL} s ({int(left)} s left)")
        time.sleep(min(POLL, max(left, 0)))

    new_cff, new_chg = add_to_cff(cff, v, doi), add_to_changelog(chg, v, doi)
    if new_cff == cff and new_chg == chg:
        print(f"record-doi: {v} already records {doi}; nothing to do")
        return 0
    cff_path.write_text(new_cff)
    chg_path.write_text(new_chg)
    git("add", "CITATION.cff", "CHANGELOG.md")
    git("commit", "-q", "-m", f"citation: Zenodo version DOI for {v}",
        "-m", f"RELEASING.md step 7: Zenodo archived v{v} as {doi} (concept {concept}).", *trailers)
    git("push", "-q", "origin", "main")
    print(f"record-doi: {v} -> {doi}; committed {git('rev-parse', '--short', 'HEAD')} and pushed main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
