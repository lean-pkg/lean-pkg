#!/usr/bin/env python3
"""Refuse when a released CHANGELOG.md section differs from the same section at its tag.

scripts/check-changelog-releases.py [--repo DIR] [--require-tags]

A `## [X.Y.Z]` section is a claim about what the tag vX.Y.Z contains. An entry that a rebase
carries into an already-released section makes the claim false (three times on 2026-10-17;
fixed by #204, a #201 rebase and #224). So each released section must read exactly as it did
in `git show vX.Y.Z:CHANGELOG.md`, with one exception: the line scripts/record-doi.py adds after
tagging, `Zenodo version DOI: [DOI](https://doi.org/DOI)`, as the section's last line after a
blank line. Trailing blank lines at a section's end are not compared.

Only sections whose tag exists locally are checked; nothing is fetched. A section without a local
tag is reported as skipped: the newest one is the release `make cut-release` is cutting (its
section is written before the tag exists), any other means the clone lacks tags (`git fetch
--tags`). --require-tags (CI) fails on a skipped section other than the newest, so a clone without
tags cannot pass by checking nothing.

Sections that already differed when this check was introduced are in ALLOWED below, each pinned
to the SHA-256 of its current text: the entry documents the difference, and any further edit to
that section fails again.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "changelog-releases"
HEADING_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\][^\n]*$", re.M)
# The exact line scripts/record-doi.py (add_to_changelog) writes.
DOI_RE = re.compile(r"Zenodo version DOI: \[(10\.5281/zenodo\.\d+)\]\(https://doi\.org/\1\)")
DIFF_LINES = 40

# version -> (SHA-256 of the section's normalised current text, why it differs from its tag).
ALLOWED: dict[str, tuple[str, str]] = {
    "0.7.41": ("11229478c824900042901d810137592ee2b0b39df918f5c9420a415bdf7890dc",
               "post-tag `Zenodo version DOI: none.` note (9ae5253): Zenodo was unreachable at release, so "
               "record-doi had no DOI to write and the note was added by hand in its place"),
    "0.7.11": ("1b3ee27b2f6a21cbf49fa9218fb065786f2e2a790c781415977193b26f2f608b",
               "misplaced entry: the `make public-safety` entry (0ca6099) landed here after v0.7.11 was "
               "tagged; the target first shipped in 0.7.12. Kept as is: moving it would rewrite two "
               "released sections"),
    "0.7.3": ("ba1c15c79a398ecdd4d2af6095fad30fce2ff04f4cafa54c610fd4efd764145a",
              "misplaced entry: the Git environment scrub entry (#27, 383e4e8) landed here after v0.7.3 "
              "was tagged; the code first shipped in 0.7.4. Kept as is: moving it would rewrite two "
              "released sections"),
}


def sections(text: str) -> dict[str, str]:
    """`## [X.Y.Z]` heading line through the line before the next `## [` heading, per version."""
    heads = list(re.finditer(r"^## \[", text, re.M))
    out: dict[str, str] = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        m = HEADING_RE.match(text, h.start())
        if m and m.group(1) not in out:
            out[m.group(1)] = text[h.start():end]
    return out


def normalise(section: str) -> list[str]:
    """The section's lines without trailing blank lines and without record-doi's final DOI line."""
    lines = section.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and DOI_RE.fullmatch(lines[-1]):
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return lines


def digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def has_tag(repo: Path, v: str) -> bool:
    return git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{v}^{{commit}}").returncode == 0


def check(repo: Path, require_tags: bool = False) -> int:
    path = repo / "CHANGELOG.md"
    if not path.is_file():
        print(f"{NAME}: no CHANGELOG.md in {repo}", file=sys.stderr)
        return 2
    current = sections(path.read_text())
    if not current:
        print(f"{NAME}: no released sections; nothing to check")
        return 0
    newest = next(iter(current))
    checked, skipped, allowed, failed = 0, [], [], []
    for v, text in current.items():
        if not has_tag(repo, v):
            skipped.append(v)
            continue
        r = git(repo, "show", f"v{v}:CHANGELOG.md")
        tagged = sections(r.stdout).get(v) if r.returncode == 0 else None
        now = normalise(text)
        then = normalise(tagged) if tagged is not None else []
        checked += 1
        if now == then:
            continue
        if v in ALLOWED and ALLOWED[v][0] == digest(now):
            allowed.append(v)
            continue
        failed.append((v, then, now, tagged is None))

    for v, then, now, absent in failed:
        print(f"{NAME}: [{v}] differs from the same section at its tag v{v}"
              + (" (the tag's CHANGELOG.md has no such section)" if absent else "") + ":", file=sys.stderr)
        diff = list(difflib.unified_diff(then, now, f"v{v}:CHANGELOG.md", "CHANGELOG.md", n=2, lineterm=""))
        for line in diff[:DIFF_LINES]:
            print(f"  {line}", file=sys.stderr)
        if len(diff) > DIFF_LINES:
            print(f"  ... ({len(diff) - DIFF_LINES} more diff lines)", file=sys.stderr)
        if v in ALLOWED:
            print(f"  [{v}] is allow-listed ({ALLOWED[v][1]}) but its text changed since; the pinned "
                  f"SHA-256 is {ALLOWED[v][0][:12]}..., the section's is now {digest(now)[:12]}...", file=sys.stderr)
    if failed:
        print(f"{NAME}: a released section must read as it did at its tag (only record-doi's `Zenodo "
              f"version DOI:` line may be added after tagging). Fix: move the added entries under "
              f"`## [Unreleased]` and restore the section from `git show vX.Y.Z:CHANGELOG.md`.", file=sys.stderr)

    old_skipped = [v for v in skipped if v != newest]
    for v in skipped:
        why = ("the newest section: the release being cut, tagged next" if v == newest
               else "no such tag in this clone (run `git fetch --tags`)")
        print(f"{NAME}: [{v}] skipped: no local tag v{v}; {why}")
    if require_tags and old_skipped:
        print(f"{NAME}: --require-tags: {len(old_skipped)} released section(s) have no local tag "
              f"({', '.join(old_skipped[:5])}{', ...' if len(old_skipped) > 5 else ''}); fetch the tags "
              f"(actions/checkout `fetch-depth: 0`) so the check runs", file=sys.stderr)
        return 1
    if failed:
        return 1
    note = f", {len(allowed)} allow-listed ({', '.join(allowed)})" if allowed else ""
    print(f"{NAME}: {checked - len(allowed)} released section(s) match their tags{note}, {len(skipped)} skipped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=ROOT, help="repository root (default: this checkout)")
    ap.add_argument("--require-tags", action="store_true",
                    help="fail when a released section other than the newest has no local tag (CI)")
    a = ap.parse_args()
    return check(a.repo.resolve(), a.require_tags)


if __name__ == "__main__":
    sys.exit(main())
