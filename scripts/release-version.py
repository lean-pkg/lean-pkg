#!/usr/bin/env python3
"""The version the next release must carry, derived from `## [Unreleased]` and the upgrade record.

scripts/release-version.py [--repo DIR] [--milestone NAME] [--bare]   print the required next version
scripts/release-version.py --check-version X.Y.Z [--milestone NAME]    refuse unless X.Y.Z is that version
scripts/release-version.py --check                                     the CHANGELOG category check only
scripts/release-version.py --curated [--pr N]                         the record names every merged PR (and N)
scripts/release-version.py --rename X.Y.Z                              name the in-progress record X.Y.Z
scripts/release-version.py --history [--since 0.7.0]                   the rule applied to every tag

RELEASING.md "When to tag" is the rule; this script applies it to two sources that must agree:

* the in-progress upgrade record, docs/upgrades/<provisional>.yaml: the one record whose version is above
  the current version (pyproject.toml). Its name is provisional until the release is cut: the first
  pull request after a release opens it as the next minor, and `--rename` (run by cut-release) gives it
  the version actually cut.
  `--rename` also rewrites src/lean_pkg/data/upgrade-breaking.yaml, which lists the records' breaking
  entries by version (scripts/upgrade-breaking.py).
* the `### <Heading>` sections of `## [Unreleased]` in CHANGELOG.md, each entry attributed to the pull
  requests that wrote it: a `(#N)` in its text, or a squash commit `... (#N)` since the last release
  whose CHANGELOG.md diff added the entry's first line.

A minor (0.(N+1).0) is required by any of: a record entry of kind command, format or requirement; a
record entry whose `changelog` heading is Added, Deprecated or Removed (a new user-visible flag, field or
feature, or a removal); a CHANGELOG entry under ### Added, ### Deprecated or ### Removed (each must be
backed by such a record entry); a roadmap milestone (--milestone). Everything else (behavior entries
under Fixed, Changed or Security, no_author_change pull requests, ### Fixed, ### Changed, ### Security
and ### Documentation entries) is a patch (0.N.(k+1)). Where the record and the CHANGELOG disagree (a
no_author_change pull request's entry under ### Added, a record entry naming a heading [Unreleased]
lacks, an ### Added entry no record entry backs), it refuses with the entries to fix and never guesses.

Exit 0 when consistent (and, with --check-version, when X.Y.Z is the required version), 1 on a refusal,
2 on a usage error. Read-only except --rename. Needs PyYAML (a lean-pkg dependency).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
NAME = "release-version"
RECORD_DIR = "docs/upgrades"
BREAKING_FILE = "src/lean_pkg/data/upgrade-breaking.yaml"   # generated from the records: scripts/upgrade-breaking.py
# Keep a Changelog 1.1.0's six types, plus Documentation for docs-only entries (RELEASING.md).
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security", "Documentation")
MINOR_HEADINGS = ("Added", "Deprecated", "Removed")
MINOR_KINDS = ("command", "format", "requirement")
PR_RE = re.compile(r"\(#(\d+)\)")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")


def vtuple(v: str) -> tuple[int, int, int]:
    a, b, c = (int(x) for x in v.split("."))
    return a, b, c


def next_version(current: str, bump: str) -> str:
    a, b, c = vtuple(current)
    return f"{a}.{b + 1}.0" if bump == "minor" else f"{a}.{b}.{c + 1}"


def bump_of(old: str, new: str) -> str:
    o, n = vtuple(old), vtuple(new)
    return "major" if n[0] != o[0] else "minor" if n[1] != o[1] else "patch"


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


# --------------------------------------------------------------------------
# CHANGELOG sections


@dataclass
class Entry:
    heading: str
    text: str
    prs: set[int] = field(default_factory=set)

    @property
    def first(self) -> str:
        return self.text.split("\n", 1)[0]

    def short(self, n: int = 70) -> str:
        s = self.first[2:].strip()
        return s if len(s) <= n else s[:n - 3] + "..."


def section_body(chg: str, version: str | None) -> str | None:
    """The body of `## [Unreleased]` (version None) or `## [X.Y.Z]`, without its heading line."""
    label = "Unreleased" if version is None else re.escape(version)
    m = re.search(rf"^## \[{label}\][^\n]*\n", chg, re.M)
    if not m:
        return None
    nxt = re.search(r"^## \[", chg[m.end():], re.M)
    return chg[m.end():m.end() + nxt.start()] if nxt else chg[m.end():]


def parse_entries(body: str) -> tuple[list[Entry], list[str]]:
    """The bullet entries of a section with their `### Heading`, and the headings found (in order)."""
    entries, headings, heading, cur = [], [], None, None
    for line in body.split("\n"):
        if line.startswith("### "):
            heading, cur = line[4:].strip(), None
            headings.append(heading)
        elif line.startswith("## "):
            break
        elif line.startswith("- ") and heading is not None:
            cur = Entry(heading, line)
            cur.prs = {int(n) for n in PR_RE.findall(line)}
            entries.append(cur)
        elif cur is not None and line.startswith(" ") and line.strip():
            cur.text += "\n" + line
            cur.prs |= {int(n) for n in PR_RE.findall(line)}
        elif not line.strip() and cur is not None:
            continue
        else:
            cur = None   # a paragraph that is not an entry (the public-safety line, the DOI line)
    return entries, headings


def added_first_lines(repo: Path, old: str | None, new: str) -> dict[str, set[int]]:
    """{entry first line: PR numbers} for the squash commits `... (#N)` in old..new whose CHANGELOG.md diff
    added that line. Without a base revision, nothing is attributed this way."""
    if old is None:
        return {}
    p = git(repo, "log", "--format=%x00%s", "-p", "-U0", "--no-color", f"{old}..{new}", "--", "CHANGELOG.md")
    out: dict[str, set[int]] = {}
    for chunk in p.stdout.split("\x00")[1:]:
        subject, _, diff = chunk.partition("\n")
        m = re.search(r"\(#(\d+)\)\s*$", subject)
        if not m:
            continue
        for line in diff.split("\n"):
            if line.startswith("+- "):
                out.setdefault(line[1:], set()).add(int(m.group(1)))
    return out


# --------------------------------------------------------------------------
# records


@dataclass
class Record:
    path: Path        # relative to the repository root
    version: str
    changes: list[dict]
    no_author_change: list[int]


def load_record(path: Path, text: str) -> Record:
    doc = yaml.safe_load(text) or {}
    if not isinstance(doc, dict) or not isinstance(doc.get("version"), str) \
            or not VERSION_RE.fullmatch(doc["version"]):
        raise ValueError(f"{path}: not an upgrade record with a `version: X.Y.Z` key")
    changes = [c for c in doc.get("changes") or [] if isinstance(c, dict)]
    nac = [n for n in doc.get("no_author_change") or [] if isinstance(n, int)]
    return Record(path, doc["version"], changes, nac)


def in_progress_record(repo: Path, current: str) -> tuple[Record | None, list[str]]:
    """The record above the current version (its name is provisional until the release is cut)."""
    found, problems = [], []
    for f in sorted((repo / RECORD_DIR).glob("*.yaml")):
        try:
            r = load_record(f.relative_to(repo), f.read_text())
        except (ValueError, yaml.YAMLError) as e:
            problems.append(str(e).splitlines()[0])
            continue
        if f.stem != r.version:
            problems.append(f"{r.path}: the file name must be the record's version ({r.version}.yaml)")
        if vtuple(r.version) > vtuple(current):
            found.append(r)
    if len(found) > 1:
        problems.append("more than one record is above the current version " + current + ": "
                        + ", ".join(str(r.path) for r in found) + "; only the next release's record may be "
                        "unreleased (merge them into one)")
    return (found[-1] if found else None), problems


# --------------------------------------------------------------------------
# the rule


@dataclass
class Verdict:
    bump: str                                           # "minor" or "patch"
    reasons: list[str]                                  # what decides it
    problems: list[str]                                 # record and CHANGELOG disagree: refuse
    notes: list[str] = field(default_factory=list)      # informational


def derive(entries: list[Entry], headings: list[str], record: Record | None, milestone: str | None = None,
           history: bool = False) -> Verdict:
    """The bump RELEASING.md "When to tag" requires for one release's CHANGELOG entries and record."""
    problems, notes, minor, patch = [], [], [], []
    rname = str(record.path) if record else "the record"
    for h in headings:
        if h not in HEADINGS:
            problems.append(f"unknown CHANGELOG heading `### {h}` (use one of {', '.join(HEADINGS)})")
    nonempty = {e.heading for e in entries}
    nac = set(record.no_author_change) if record else set()
    flagged = set()
    for i, e in enumerate(entries):
        if e.heading in MINOR_HEADINGS and e.prs and e.prs <= nac:
            flagged.add(i)
            prs = ", ".join(f"#{n}" for n in sorted(e.prs))
            problems.append(f"`### {e.heading}` entry \"{e.short()}\" is from {prs}, listed under no_author_change in "
                            f"{rname}: a docs-only entry goes under `### Documentation` (release tooling, tests "
                            f"or CI under `### Changed` or `### Fixed`), never under `### {e.heading}`")
    if record is None:
        if entries and history:
            notes.append("no upgrade record for this release")
        elif entries:
            problems.append("no in-progress upgrade record (docs/upgrades/<next>.yaml above the current "
                            "version); RELEASING.md step 0 needs one")
        for h in MINOR_HEADINGS:
            n = sum(1 for e in entries if e.heading == h)
            if n:
                minor.append(f"CHANGELOG `### {h}`: {n} entr{'y' if n == 1 else 'ies'}")
        if history and "Changed" in nonempty and not minor:
            notes.append("`### Changed` entries and no record: a patch unless one of them changed a format")
    else:
        for c in record.changes:
            h, kind, tag = c.get("changelog"), c.get("kind"), f"#{c.get('pr')} {c.get('id')}"
            if h is not None and h not in HEADINGS:
                problems.append(f"{rname}: entry {tag} has changelog: {h}, not a CHANGELOG heading")
            elif h is not None and h not in nonempty and not history:
                problems.append(f"{rname}: entry {tag} says changelog: {h}, but `## [Unreleased]` has no "
                                f"`### {h}` entry (add the entry there, or fix the record's heading)")
            if kind in MINOR_KINDS:
                minor.append(f"{tag}: kind {kind}")
            elif h in MINOR_HEADINGS:
                minor.append(f"{tag}: changelog {h} (a new user-visible flag, field or feature)")
            else:
                patch.append(f"{tag}: kind {kind}, changelog {h}")
        backed = {c.get("changelog") for c in record.changes}
        for h in MINOR_HEADINGS:
            rest = [e for i, e in enumerate(entries) if e.heading == h and i not in flagged]
            if rest and h not in backed:
                msg = (f"`### {h}` has {len(rest)} entr{'y' if len(rest) == 1 else 'ies'} (\"{rest[0].short()}\""
                       f"{', ...' if len(rest) > 1 else ''}) but no entry of {rname} says changelog: {h}: add the "
                       f"record entry, or move a docs-only CHANGELOG entry to `### Documentation`")
                (notes if history else problems).append(msg)
                minor.append(f"CHANGELOG `### {h}`: {len(rest)} entr{'y' if len(rest) == 1 else 'ies'} no record "
                             f"entry backs")
        if record.no_author_change:
            patch.append("no_author_change: " + ", ".join(f"#{n}" for n in record.no_author_change))
    if milestone:
        minor.append(f"roadmap milestone {milestone}")
    for h in HEADINGS:
        n = sum(1 for e in entries if e.heading == h)
        if n and h not in MINOR_HEADINGS:
            patch.append(f"CHANGELOG `### {h}`: {n} entr{'y' if n == 1 else 'ies'}")
    if history:   # an audit reports; one line for the docs entries under a minor heading
        nac_minor = sorted({n for i in flagged for n in entries[i].prs})
        problems = [p for p in problems if "listed under no_author_change" not in p]
        if flagged:
            problems.append(f"{len(flagged)} entr{'y' if len(flagged) == 1 else 'ies'} under a minor heading from "
                            "no_author_change PRs " + ", ".join(f"#{n}" for n in nac_minor) + " (not counted)")
        notes, problems = notes + problems, []
    return Verdict("minor" if minor else "patch", minor or patch, problems, notes)


# --------------------------------------------------------------------------
# the next release


def current_version(repo: Path) -> str:
    return tomllib.loads((repo / "pyproject.toml").read_text())["project"]["version"]


def release_commit(repo: Path, v: str) -> str | None:
    """Tag vX.Y.Z, else the commit whose subject is `release: X.Y.Z`; None when neither exists."""
    p = git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{v}^{{commit}}")
    if p.returncode == 0:
        return p.stdout.strip()
    p = git(repo, "log", "--format=%H %s", "--fixed-strings", f"--grep=release: {v}", "HEAD")
    for line in p.stdout.splitlines():
        h, _, s = line.partition(" ")
        if s == f"release: {v}":
            return h
    return None


@dataclass
class Next:
    current: str
    version: str
    verdict: Verdict
    record: Record | None
    entries: list[Entry]


def next_release(repo: Path, milestone: str | None = None) -> Next:
    current = current_version(repo)
    chg = (repo / "CHANGELOG.md").read_text()
    body = section_body(chg, None)
    if body is None:
        v = Verdict("patch", [], ["CHANGELOG.md has no `## [Unreleased]` heading"])
        return Next(current, next_version(current, "patch"), v, None, [])
    entries, headings = parse_entries(body)
    record, rproblems = in_progress_record(repo, current)
    by_line = added_first_lines(repo, release_commit(repo, current), "HEAD")
    for e in entries:
        e.prs |= by_line.get(e.first, set())
    v = derive(entries, headings, record, milestone)
    v.problems = rproblems + v.problems
    return Next(current, next_version(current, v.bump), v, record, entries)


def report(n: Next) -> list[str]:
    out = []
    if not n.entries:
        out.append(f"{NAME}: `## [Unreleased]` is empty; nothing to release yet (current version {n.current})")
        return out
    out.append(f"{NAME}: the next release is {n.version} ({n.verdict.bump} over {n.current}; RELEASING.md "
               f"\"When to tag\")")
    out.append("  decided by:" if n.verdict.bump == "minor" else "  no minor trigger; the release holds:")
    out += [f"    {r}" for r in n.verdict.reasons]
    if n.record is not None:
        if n.record.version == n.version:
            out.append(f"  record: {n.record.path} is named for {n.version}")
        else:
            out.append(f"  record: {n.record.path} carries the provisional name {n.record.version}; "
                       f"`make cut-release VERSION={n.version}` renames it to {RECORD_DIR}/{n.version}.yaml (and .md)")
    out += [f"  note: {x}" for x in n.verdict.notes]
    return out


def refusal(n: Next) -> str:
    lines = [f"{NAME}: the upgrade record and `## [Unreleased]` disagree; fix these, then run again "
             "(the required version is not guessed):"]
    lines += [f"  - {p}" for p in n.verdict.problems]
    return "\n".join(lines)


def check_version(n: Next, v: str) -> tuple[bool, str]:
    """(ok, message) for a requested release version X.Y.Z."""
    if n.verdict.problems:
        return False, refusal(n)
    if not n.entries:
        return False, f"{NAME}: `## [Unreleased]` is empty; nothing to release"
    if v == n.version:
        return True, f"{NAME}: {v} is the version RELEASING.md requires ({n.verdict.bump} over {n.current})"
    if vtuple(v) == (vtuple(n.current)[0] + 1, 0, 0):
        return True, (f"{NAME}: {v} is a major release over {n.current}: a maintainer decision (RELEASING.md, "
                      f"1.0.0 criteria), not derived; the rule alone would give {n.version}")
    why = "\n".join(f"    {r}" for r in n.verdict.reasons)
    return False, (f"{NAME}: refusing {v}: RELEASING.md \"When to tag\" requires {n.version} "
                   f"({n.verdict.bump} over {n.current}), and {v} is a {bump_of(n.current, v)} bump.\n"
                   f"  {'decided by' if n.verdict.bump == 'minor' else 'no minor trigger; the release holds'}:\n"
                   f"{why}\n  run `make cut-release VERSION={n.version}`"
                   + ("" if n.verdict.bump == "minor" else
                      " (a roadmap milestone makes it a minor: MILESTONE=Mk)"))


# --------------------------------------------------------------------------
# renaming the in-progress record


def rename_record(repo: Path, record: Record, new: str) -> list[str]:
    """Rename docs/upgrades/<old>.yaml and .md to <new>, rewriting the version string inside both (the
    `version:` key, the page title and heading, the links between the two). Returns the new paths."""
    old = record.version
    pat = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w]|\.\d)")
    done = []
    for ext in (".yaml", ".md"):
        src = repo / RECORD_DIR / f"{old}{ext}"
        dst = repo / RECORD_DIR / f"{new}{ext}"
        if not src.is_file():
            if ext == ".md":
                continue
            raise FileNotFoundError(src)
        if dst.exists():
            raise FileExistsError(dst)
        text = pat.sub(new, src.read_text())
        if git(repo, "mv", str(src.relative_to(repo)), str(dst.relative_to(repo))).returncode != 0:
            src.rename(dst)
        dst.write_text(text)
        done.append(str(dst.relative_to(repo)))
    check = load_record(Path(done[0]), (repo / done[0]).read_text())
    if check.version != new:
        raise ValueError(f"{done[0]}: version is {check.version} after the rename, not {new}")
    if (repo / BREAKING_FILE).is_file() and regenerate_breaking(repo):   # its versions follow the record's
        done.append(BREAKING_FILE)
    return done


def regenerate_breaking(repo: Path) -> bool:
    """Rewrite repo/BREAKING_FILE from repo's records with scripts/upgrade-breaking.py beside this script;
    True when its bytes changed."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("upgrade_breaking", Path(__file__).resolve().parent
                                                  / "upgrade-breaking.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.write(repo)


# --------------------------------------------------------------------------
# the history audit


@dataclass
class Audit:
    version: str
    previous: str
    actual: str
    required: str
    should_be: str
    verdict: Verdict

    @property
    def matches(self) -> bool:
        return self.actual == self.required or self.actual == "major"


def audit(repo: Path, since: str = "0.7.0") -> list[Audit]:
    """The rule applied to every tag from `since` on: each release's own CHANGELOG section (at its tag),
    the record of that version (at HEAD, where records are completed), PRs attributed in prev..tag."""
    tags = [t[1:] for t in git(repo, "tag", "-l", "v*").stdout.split() if VERSION_RE.fullmatch(t[1:])]
    tags.sort(key=vtuple)
    out = []
    for prev, v in zip(tags, tags[1:]):
        if vtuple(v) < vtuple(since):
            continue
        chg = git(repo, "show", f"v{v}:CHANGELOG.md").stdout
        body = section_body(chg, v) or ""
        entries, headings = parse_entries(body)
        by_line = added_first_lines(repo, f"v{prev}", f"v{v}")
        for e in entries:
            e.prs |= by_line.get(e.first, set())
        rec = None
        p = repo / RECORD_DIR / f"{v}.yaml"
        if p.is_file():
            rec = load_record(p.relative_to(repo), p.read_text())
        verdict = derive(entries, headings, rec, history=True)
        out.append(Audit(v, prev, bump_of(prev, v), verdict.bump, next_version(prev, verdict.bump), verdict))
    return out


def audit_lines(rows: list[Audit]) -> list[str]:
    out = [f"{'release':<9} {'over':<8} {'cut as':<7} {'rule':<6} {'should be':<10} verdict"]
    for a in rows:
        out.append(f"{a.version:<9} {a.previous:<8} {a.actual:<7} {a.required:<6} {a.should_be:<10} "
                   + ("ok" if a.matches else "MISMATCH: " + "; ".join(a.verdict.reasons))
                   + "".join(f" [note: {x}]" for x in a.verdict.notes))
    bad = [a.version for a in rows if not a.matches]
    out.append(f"{NAME}: {len(rows)} release(s) audited, {len(bad)} do not follow the rule"
               + (f": {', '.join(bad)}" if bad else ""))
    return out


# --------------------------------------------------------------------------


def squash_prs(repo: Path, since: str | None) -> list[int]:
    """The pull requests squash-merged after `since` (a commit; None = all history): subjects ending `(#N)`."""
    p = git(repo, "log", "--format=%s", f"{since}..HEAD" if since else "HEAD")
    return sorted({int(m.group(1)) for s in p.stdout.splitlines() if (m := re.search(r"\(#(\d+)\)\s*$", s))})


def curated(repo: Path, pr: int | None = None) -> tuple[bool, list[str]]:
    """Every pull request merged since the last release, and `pr` (CI passes the one under test, whose
    squash commit does not exist yet), is named by the in-progress record: a `changes` entry or
    `no_author_change`. doctor --upgrade-from lists the others as uncurated, which used to surface only
    when the release was cut (#285)."""
    current = current_version(repo)
    since = release_commit(repo, current)
    if since is None:
        # Mid-cut (cut-release step 4): the version files already name the release being cut, which has no tag
        # or `release:` commit yet. Its record is the one named for it; the range starts at the newest earlier
        # release. Reading the record above it and the whole history refused every cut (0.20.0).
        problems = []
        f = repo / RECORD_DIR / f"{current}.yaml"
        try:
            record = load_record(f.relative_to(repo), f.read_text()) if f.is_file() else None
        except (ValueError, yaml.YAMLError) as e:
            record, problems = None, [str(e).splitlines()[0]]
        earlier = [t[1:] for t in git(repo, "tag", "-l", "v*").stdout.split()
                   if VERSION_RE.fullmatch(t[1:]) and vtuple(t[1:]) < vtuple(current)]
        since = release_commit(repo, max(earlier, key=vtuple)) if earlier else None
    else:
        record, problems = in_progress_record(repo, current)
    want = squash_prs(repo, since) + ([pr] if pr is not None else [])
    named = ({c.get("pr") for c in record.changes} | set(record.no_author_change)) if record else set()
    missing = sorted(set(want) - named)
    if missing:
        where = str(record.path) if record else f"{RECORD_DIR}/{next_version(current, 'minor')}.yaml (new)"
        problems.append("pull request(s) " + ", ".join(f"#{n}" for n in missing) + f" not named by {where}: "
                        "add a `changes` entry (RELEASING.md) or list the number under `no_author_change`")
    return not problems, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=ROOT, help="repository root (default: this checkout)")
    ap.add_argument("--milestone", default=None, metavar="NAME",
                    help="a roadmap milestone lands with this release (RELEASING.md): it makes it a minor")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check-version", metavar="X.Y.Z", help="refuse unless X.Y.Z is the required version")
    g.add_argument("--check", action="store_true", help="only the CHANGELOG/record consistency check")
    g.add_argument("--rename", metavar="X.Y.Z", help="name the in-progress record for X.Y.Z")
    g.add_argument("--history", action="store_true", help="apply the rule to every tag (see --since)")
    g.add_argument("--curated", action="store_true",
                   help="refuse unless the in-progress record names every pull request merged since the last "
                        "release (and --pr N)")
    ap.add_argument("--pr", type=int, default=None, metavar="N",
                    help="with --curated: the pull request under test, not merged yet")
    ap.add_argument("--since", default="0.7.0", metavar="X.Y.Z", help="with --history: the first release audited")
    ap.add_argument("--bare", action="store_true", help="print only the required version")
    a = ap.parse_args(argv)
    repo = a.repo.resolve()
    for v in (a.check_version, a.rename, a.since):
        if v is not None and not VERSION_RE.fullmatch(v):
            ap.error(f"not a X.Y.Z version: {v}")

    if a.history:
        print("\n".join(audit_lines(audit(repo, a.since))))
        return 0
    if a.curated:
        ok, problems = curated(repo, a.pr)
        for p in problems:
            print(f"{NAME}: {p}", file=sys.stderr)
        if ok:
            print(f"{NAME}: the upgrade record names every pull request since the last release")
        return 0 if ok else 1
    n = next_release(repo, a.milestone)
    if a.rename:
        if n.record is None:
            print(f"{NAME}: no in-progress record above {n.current} to rename", file=sys.stderr)
            return 1
        if n.record.version == a.rename:
            print(f"{NAME}: {n.record.path} is already named for {a.rename}")
            return 0
        paths = rename_record(repo, n.record, a.rename)
        print(f"{NAME}: {n.record.path} renamed to {', '.join(paths)} (version {a.rename})")
        return 0
    if n.verdict.problems:
        print(refusal(n), file=sys.stderr)
        return 1
    if a.check:
        print(f"{NAME}: `## [Unreleased]` and the upgrade record agree"
              + (f" (next release {n.version})" if n.entries else " (nothing unreleased)"))
        return 0
    if a.check_version:
        ok, msg = check_version(n, a.check_version)
        print(msg, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    if a.bare:
        print(n.version if n.entries else "")
        return 0
    print("\n".join(report(n)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
