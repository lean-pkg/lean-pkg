#!/usr/bin/env python3
"""Record a release's Zenodo version DOI: scripts/record-doi.py X.Y.Z [--wait SECONDS]

RELEASING.md step 7. Zenodo archives each GitHub release under the concept DOI in
CITATION.cff (`doi:`). This finds the version record for vX.Y.Z, checks that it is a
version of that concept and points at the tag, then lists its DOI under `identifiers:`
in CITATION.cff and adds a `Zenodo version DOI:` line to the X.Y.Z section of
CHANGELOG.md, in one commit (`citation: Zenodo version DOI for X.Y.Z`) pushed to main.

Idempotent: when both files already carry the DOI it changes nothing. The push is retried on
transient errors as scripts/release.sh does (LEAN_PKG_RELEASE_RETRIES, LEAN_PKG_RELEASE_RETRY_DELAY);
when it still fails, the commit stays local and a re-run pushes it: a main ahead of origin/main by
exactly its own `citation: Zenodo version DOI for X.Y.Z` commit is resumed, not refused. --wait polls
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
NAME = "record-doi"
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
    own = f"citation: Zenodo version DOI for {v}"
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        ahead = git("log", "--format=%s", "origin/main..HEAD").splitlines()
        if git("rev-list", "--count", "HEAD..origin/main") != "0" or ahead != [own]:
            die("main differs from origin/main (pull or push first)")
        print(f"record-doi: {git('rev-parse', '--short', 'HEAD')} `{own}` is committed but not on origin; "
              "pushing it")
        push_or_die(v)
        return 0

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
    git("commit", "-q", "-m", own,
        "-m", f"RELEASING.md step 7: Zenodo archived v{v} as {doi} (concept {concept}).", *trailers)
    print(f"record-doi: {v} -> {doi}; committed {git('rev-parse', '--short', 'HEAD')}")
    push_or_die(v)
    return 0


def push_or_die(v: str) -> None:
    sha = git("rev-parse", "--short", "HEAD")
    ok, _ = push_main()
    if not ok:
        die(f"{sha} `citation: Zenodo version DOI for {v}` is committed on main but not pushed (see above); "
            f"nothing is lost: run `make record-doi VERSION={v}` again once the remote is reachable and it "
            f"pushes that commit (so does `make cut-release VERSION={v}`)")
    print(f"record-doi: pushed main at {sha}")


if __name__ == "__main__":
    sys.exit(main())
