# lean-pkg public-safety -- private strings in a package's tree and git history
"""`lean-pkg public-safety NAME [--history|--no-history] [--strict]` (#35, #36): before a package
goes public, scan what would be published (the working tree git would ship) and, by default,
everything the public flip exposes (every blob, commit message, author and committer identity,
annotated tag and branch or tag name reachable from any ref, the approach of lean-pkg's own
`make history`) for private patterns. When the package is a subdirectory of its git repository, the
scan covers the whole repository (`--package-only`: the package directory alone), because promotion
makes the whole GitHub repository public. A symbolic link is scanned as its target string; UTF-8/16/32
text with a byte-order mark (and BOM-less UTF-16) is decoded; long lines are scanned whole, in
overlapping chunks; a binary file is scanned through its printable runs and listed in `skipped[]`;
bytes past MAX_SCAN_BYTES are listed there too and are a blocking `unscanned` hit unless an
`allow: GLOB: ^unscanned$` line covers the file. Patterns:

- the package's `.lean-pkg-private-patterns` file: one regular expression per line, `#` comments;
  a bare line or `block: REGEX` is blocking, `warn: REGEX` is warn-only, `allow: REGEX` exempts a
  matched string (a public URL, a declared address). Each of the three takes an optional path scope
  (#38): `allow: GLOB: REGEX` exempts, and `block: GLOB: REGEX` / `warn: GLOB: REGEX` report, only in
  files whose package-relative path matches GLOB (`*`, `**`, `?`; a GLOB without `/` matches the
  file name at any depth; a trailing `/` matches everything under that directory). A scoped line
  never applies to commit messages. The file itself lists private strings, so it must stay out of
  the public tree: tracked (or in history) it is a hit.
- built-in defaults: absolute home paths, a cloud-sync folder path, `approvals/`, `notes/`,
  `*.private`, `TODO-private`, and email addresses other than the declared ones (the repository's
  commit and tag identities that are ordinary mail addresses, `consent[]` entries, `allow:` lines,
  reserved example domains, GitHub noreply, `git@` SSH remotes). A host-like identity address, one
  without a dotted domain (`user@hostname`) or under a local TLD (LOCAL_TLDS: an address under `.lan`), is
  not declared by being used: it is an `identity-email` hit.
- user and host names derived at scan time from the registry record's own `local_path`, `agent`
  and `workspace` fields and its lease (`agent`, `workspace`, `contact`; never stored). `allow:` lines see a derived token's whole non-space run
  (#38) and exempt it only where one of their matches covers the token, so allowing a public URL
  that contains the token does not exempt the bare token elsewhere.
- internal files by path: `approvals/`, `notes/`, `drafts/`, `scratch/`, `*.priv`, `*.private`.
- the warn-only built-in family `latex-label` (#36): references to a LaTeX manuscript, which leak an
  unposted paper even with no path in them: cross-reference keys (`lem:`, `thm:`, `eq:`, `sec:`, ...),
  `main.tex`, ref/eqref/cite/label/input/include command fragments, Overleaf, arXiv ids. Calibrated
  for Lean sources (#37): equality-API names, `def-eq`/`defeq` and lowercase hyphen prose are not
  reported (see LATEX_LABEL_RE and the built-in allow list LATEX_LABEL_ALLOW).

Every hit has a `severity`: `"block"` (everything above but the warn-only family and `warn:` lines)
or `"warn"`. Warn hits are reported but do not fail the scan, promote or a fresh-history export;
`--strict` makes them fail the scan too.

Every hit carries its location, a pattern label and a redacted excerpt: each match is replaced by
`[…]`, so the raw private string is never printed. A pattern from the file is labelled by its line
number, never by its text. Read-only.

`promote` runs the scan (unless --skip-public-safety) and refuses while hits remain;
`publish NAME --public-safety` adds it to the publish checklist; `release NAME --fresh-repo DIR`
scans the exported tree before it writes the new repository.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import subprocess

from . import cli as core

PATTERNS_FILE = ".lean-pkg-private-patterns"
INTERNAL_DIRS = ("approvals", "notes", "drafts", "scratch")
INTERNAL_SUFFIXES = (".priv", ".private")
REDACTED = "[…]"
CONTEXT = 20            # characters of context on each side of a redacted match (40 in all)
MAX_SCAN_BYTES = 64_000_000   # bytes of one file or blob scanned; the rest is listed as skipped (a block hit)
LINE_CHUNK = 20_000           # a longer line is scanned in overlapping chunks of this many characters
LINE_OVERLAP = 1_000          # overlap of two chunks: a match up to this long is never cut in two
UNSCANNED = "unscanned"       # what an `allow:` line must match in full to exempt an unscanned portion
EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
# reserved example domains, GitHub noreply, and git@host (an SSH remote, not a mailbox)
ALWAYS_ALLOWED_EMAIL = re.compile(
    r"@(?:[A-Za-z0-9-]+\.)*(?:example\.(?:com|org|net)|users\.noreply\.github\.com)$|^(?:noreply|git)@", re.I)
BUILTIN = (
    ("home-path", r"/home/[A-Za-z0-9._-]+/"),
    ("users-path", r"/Users/[A-Za-z0-9._-]+/"),
    ("cloud-sync-path", r"~/Dropbox/"),
    ("approvals-ref", r"(?<![A-Za-z0-9_.-])approvals/"),
    ("notes-ref", r"(?<![A-Za-z0-9_.-])notes/"),
    # Lean's own build artifacts (`X.olean.private`, `X.ilean.private`, module system) are not private files.
    ("private-file-ref", r"[A-Za-z0-9_-][A-Za-z0-9_./-]*(?<!\.olean)(?<!\.ilean)\.private\b"),
    ("todo-private", r"TODO-private"),
    ("email", EMAIL_RE),
)
# #36: a LaTeX manuscript's cross-reference keys, source file, commands, Overleaf and arXiv ids.
# Warn-only: rare in Lean sources, but a published paper may be cited legitimately.
# #37 calibration (Mathlib-style sources): a key is never part of a longer name (no letter, digit, `_`
# or `.` before it: `foo_eq_bar`, `Nat.eq:`); `key:` takes any label body (`eq:spectral`,
# `lem:bound-2.1`); `key-` is a label only when its body has a digit, a capital or an inner `.`, since
# all-lowercase hyphen tokens (`sec-intro`-like, `eq-of-foo`) are Lean prose; and `eq`, `def`, `ex`
# never take `-` (`def-eq`, `eq-of`, `ex-falso`).
_LL_BODY = r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]*[A-Za-z0-9_])?"
LATEX_LABEL_RE = (r"(?<![A-Za-z0-9_.])(?:lem|thm|cor|prop|def|eq|sec|rem|ex|fig|tab|conj):" + _LL_BODY
                  + r"|(?<![A-Za-z0-9_.])(?:lem|thm|cor|prop|sec|rem|fig|tab|conj)-"
                    r"(?=[a-z_-]*[A-Z0-9]|[a-z_-]*\.[A-Za-z0-9])" + _LL_BODY
                  + r"|\bmain\.tex\b"
                  r"|\\(?:ref|eqref|cite|label|input|include)\{"
                  r"|\b(?:Overleaf|arXiv:\s*\d{4}\.\d{4,5}(?:v\d+)?)")
BUILTIN_WARN = (
    ("latex-label", LATEX_LABEL_RE),
)
# #37: Lean prose the latex-label family never reports, whatever the regex above admits.
LATEX_LABEL_ALLOW = ("def-eq", "defeq", "eq-of", "eq-iff", "ex-falso")
_LATEX_LABEL_ALLOW_RE = re.compile(r"(?:" + "|".join(re.escape(a) for a in LATEX_LABEL_ALLOW) + r")(?![A-Za-z0-9])",
                                   re.I)
BLOCK, WARN = "block", "warn"
PATH_READABLE = ("approvals-ref", "notes-ref", "private-file-ref", "todo-private", "latex-label")
DERIVED_MIN_LEN = 4
DERIVED_STOPWORDS = {"none", "null", "test", "main", "local", "default", "force", "true", "false"}
_RUN_RE = re.compile(r"\S+")


class Pattern:
    __slots__ = ("label", "kind", "rx", "severity", "glob")

    def __init__(self, label: str, kind: str, rx: re.Pattern, severity: str = BLOCK,
                 glob: re.Pattern | None = None):
        self.label, self.kind, self.rx, self.severity, self.glob = label, kind, rx, severity, glob


# #38: `TAG: GLOB: REGEX`. GLOB is the text before the first colon followed by whitespace; it has no
# whitespace, colon, backslash or parenthesis, and at least one of `/ * ? .`, so `allow: https://...`
# and `block: (?i)foo: bar` stay global regexes (write `\:` to keep a `WORD.WORD: ...` regex global).
_SCOPE_RE = re.compile(r"^([A-Za-z0-9_.*?/!+-]+):\s+(\S.*)$")


def split_scope(text: str) -> tuple[str | None, str]:
    """(GLOB or None, REGEX) of the text after a pattern line's tag."""
    m = _SCOPE_RE.match(text)
    if m and any(c in m.group(1) for c in "/*?."):
        return m.group(1), m.group(2)
    return None, text


def glob_regex(glob: str) -> re.Pattern:
    """A package-relative path glob as a full-match regex: `**/` any directories (or none), `**` any
    characters, `*` and `?` within one path segment; no `/` (but a trailing one): the file name at any
    depth; a trailing `/`: everything under that directory."""
    g, under = (glob[:-1], True) if glob.endswith("/") else (glob, False)
    out, i = [], 0
    while i < len(g):
        if g.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("[^/]*")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    rx = "".join(out)
    if "/" not in g:
        rx = "(?:.*/)?" + rx
    if g.startswith("/"):
        rx = rx[1:] if rx.startswith("/") else rx
    if under:
        rx += "/.*"
    return re.compile(rx + r"\Z")


def internal_path(rel: str) -> str | None:
    """The internal-file rule a package-relative path falls under, else None."""
    parts = pathlib.PurePosixPath(rel).parts
    for d in INTERNAL_DIRS:
        if d in parts[:-1]:
            return f"internal-dir {d}/"
    for suf in INTERNAL_SUFFIXES:
        if rel.endswith(suf):
            return f"internal-file *{suf}"
    return None


def read_patterns_file(root: pathlib.Path) -> tuple[list[Pattern], list[tuple], list[str], bool]:
    """(patterns, allow entries (regex, path glob regex or None), problems, present) from
    <root>/.lean-pkg-private-patterns."""
    p = root / PATTERNS_FILE
    if not p.is_file():
        return [], [], [], False
    pats: list[Pattern] = []
    allow: list[tuple[re.Pattern, re.Pattern | None]] = []
    problems: list[str] = []
    for n, raw in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tag, _, rest = line.partition(":")
        tag = tag if tag in ("allow", "block", "warn") else None
        text = rest.strip() if tag else line
        glob, text = split_scope(text) if tag else (None, text)
        is_allow = tag == "allow"
        try:
            rx = re.compile(text)
        except re.error as e:
            problems.append(f"{PATTERNS_FILE}:{n}: not a valid regular expression ({e.msg}); skipped")
            continue
        grx = glob_regex(glob) if glob else None
        if is_allow:
            allow.append((rx, grx))
        else:
            pats.append(Pattern(f"{PATTERNS_FILE}:{n}", "file", rx, WARN if tag == "warn" else BLOCK, grx))
    return pats, allow, problems, True


def _tokens(value) -> list[str]:
    out = []
    for tok in re.split(r"[\s/@:]+", str(value or "")):
        tok = tok.strip()
        if len(tok) >= DERIVED_MIN_LEN and tok.casefold() not in DERIVED_STOPWORDS and re.search(r"[A-Za-z]", tok):
            out.append(tok)
    return out


def derived_patterns(pkg: dict) -> list[Pattern]:
    """User and host names from the record's own local_path, agent and workspace fields and its lease's
    agent, workspace and contact (a session address such as `lab-desk/n14-paper` names a machine), derived
    now (never stored). Labels name the field, never the value."""
    found: dict[str, str] = {}
    try:
        absolute = str(core.repo_path(pkg))
    except (KeyError, TypeError, ValueError):
        absolute = ""
    m = re.match(r"^/(?:home|Users)/([^/]+)/", absolute)
    if m and len(m.group(1)) >= 2:
        found.setdefault(m.group(1), "registry local_path (user)")
    lease = pkg.get("lease") if isinstance(pkg.get("lease"), dict) else {}
    for field, value in (("agent", pkg.get("agent")), ("workspace", pkg.get("workspace")),
                         ("lease.agent", lease.get("agent")), ("lease.workspace", lease.get("workspace")),
                         ("lease.contact", lease.get("contact"))):
        for tok in _tokens(value):
            found.setdefault(tok, f"registry {field}")
    pats = []
    for tok, label in found.items():
        rx = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])", re.I)
        pats.append(Pattern(label, "derived", rx))
    return pats


# The last label of a host-only or local mail domain: an identity address there names a machine, not a
# mailbox (an address under `.lan`), so it is never declared by being used, and it is an `identity-email` hit.
LOCAL_TLDS = frozenset({"lan", "local", "localdomain", "localhost", "home", "internal", "intranet", "corp",
                        "private", "test", "invalid", "example"})


def public_identity_email(email: str) -> bool:
    """A commit or tag address that is declared by being used: a mail address whose domain has a dot and
    whose last label is not a local or host-only TLD (LOCAL_TLDS)."""
    e = email.strip()
    if not re.fullmatch(EMAIL_RE, e):
        return False
    domain = e.rsplit("@", 1)[1]
    return "." in domain and domain.rsplit(".", 1)[1].casefold() not in LOCAL_TLDS


def declared_emails(pkg: dict, repo: pathlib.Path | None) -> set[str]:
    """The author's declared addresses: the repository's commit (author, committer) and tagger
    addresses that are ordinary mail addresses (public_identity_email: dotted domain, not a local or
    host-only TLD), plus any address in the record's consent entries. A host-like identity
    (`user@hostname`, an address under `.lan`) is not declared: it is an `identity-email` hit."""
    out: set[str] = set()
    if repo is not None and core.git_head(repo):
        p = core.git(repo, "log", "--all", "--format=%ae%n%ce")
        t = core.git(repo, "for-each-ref", "refs/tags", "--format=%(taggeremail)")
        found = p.stdout.splitlines() + [x.strip().strip("<>") for x in t.stdout.splitlines()]
        out.update(x.strip().casefold() for x in found if x.strip() and public_identity_email(x))
    for c in pkg.get("consent") or []:
        if isinstance(c, dict):
            for v in c.values():
                out.update(e.casefold() for e in re.findall(EMAIL_RE, str(v)))
    return out


IDENTITY_EMAIL = Pattern("identity-email", "builtin", re.compile(r"(?!)"))


class Scanner:
    def __init__(self, pkg: dict, patterns_root: pathlib.Path, repo_for_emails: pathlib.Path | None,
                 scope_prefix: str = ""):
        """scope_prefix: when the scan root is a repository whose package is the subdirectory
        `scope_prefix` (with its trailing `/`), path-scoped lines apply to the package-relative part of
        a path under it and never to a path outside the package."""
        self.scope_prefix = scope_prefix
        file_pats, self.allow, self.problems, self.file_present = read_patterns_file(patterns_root)
        builtin = [Pattern(label, "builtin", re.compile(rx)) for label, rx in BUILTIN]
        builtin += [Pattern(label, "builtin", re.compile(rx), WARN) for label, rx in BUILTIN_WARN]
        self.derived = derived_patterns(pkg)
        self.patterns = file_pats + builtin + self.derived
        self.n_file = len(file_pats)
        self.n_file_warn = sum(1 for p in file_pats if p.severity == WARN)
        self.n_scoped = sum(1 for p in file_pats if p.glob is not None)
        self.n_allow_scoped = sum(1 for _a, g in self.allow if g is not None)
        self.emails = declared_emails(pkg, repo_for_emails)
        self._active: dict = {}

    def active(self, path: str | None) -> tuple[list[Pattern], list[re.Pattern]]:
        """(patterns, allow regexes) that apply to a file at `path` (None: a commit message or a
        location string; path-scoped lines never apply there)."""
        if path is not None and self.scope_prefix:
            path = path[len(self.scope_prefix):] if path.startswith(self.scope_prefix) else None
        if path not in self._active:
            def hit(g):
                return g is None or (path is not None and g.fullmatch(path) is not None)
            self._active[path] = ([p for p in self.patterns if hit(p.glob)],
                                  [a for a, g in self.allow if hit(g)])
        return self._active[path]

    def allowed(self, pat: Pattern, text: str, allow: list[re.Pattern] | None = None) -> bool:
        if any(a.search(text) for a in (self.active(None)[1] if allow is None else allow)):
            return True
        if pat.label == "email":
            return text.casefold() in self.emails or bool(ALWAYS_ALLOWED_EMAIL.search(text))
        if pat.label == "latex-label":
            return bool(_LATEX_LABEL_ALLOW_RE.match(text))
        return False

    def unscanned_allowed(self, path: str | None) -> bool:
        """An unscanned portion of `path` is exempt only where an `allow:` line applying to it matches
        the word `unscanned` in full (`allow: GLOB: ^unscanned$`)."""
        return any(a.fullmatch(UNSCANNED) for a in self.active(path)[1])

    def identity_hits(self, name: str, email: str) -> list[tuple[int, Pattern, str]]:
        """Hits in a commit or tag identity `name <email>`: every pattern, as for any text, and
        `identity-email` (in place of the email family) for a host-like address, one without a dotted
        domain (`user@hostname`) or under a local TLD (such as `.lan`), unless an `allow:` line, a
        consent entry or the always-allowed list (example domains, GitHub noreply) covers it."""
        text = f"{name} <{email}>"
        spans = self.spans(text)
        e = email.strip()
        at = len(name) + 2                 # where the address starts in text
        if public_identity_email(e):       # an ordinary address is declared by being used
            spans = [x for x in spans if not (x[2].label == "email" and x[0] >= at)]
        elif e and not (
                any(a.search(e) for a in self.active(None)[1]) or e.casefold() in self.emails
                or ALWAYS_ALLOWED_EMAIL.search(e)):
            spans = [x for x in spans if not (x[2].label == "email" and x[0] >= at)]
            spans = sorted(spans + [(at, at + len(email), IDENTITY_EMAIL)], key=lambda x: (x[0], -x[1]))
        return self._line_hits(1, text, spans, set())

    def _line_hits(self, n: int, line: str, spans, seen: set) -> list[tuple[int, Pattern, str]]:
        hits = []
        if not spans:
            return hits
        red, starts = _redact(line, spans)
        for (_s, _e, pat), pos in zip(spans, starts):
            if pat.label in seen:
                continue
            seen.add(pat.label)
            lo, hi = max(0, pos - CONTEXT), min(len(red), pos + len(REDACTED) + CONTEXT)
            hits.append((n, pat, " ".join(red[lo:hi].split())))
        return hits

    def spans(self, line: str, path: str | None = None) -> list[tuple[int, int, Pattern]]:
        pats, allow = self.active(path)
        out = []
        for pat in pats:
            if pat.kind == "derived":
                out += self._derived_spans(pat, line, allow)
                continue
            for m in pat.rx.finditer(line):
                if m.end() > m.start() and not self.allowed(pat, m.group(0), allow):
                    out.append((m.start(), m.end(), pat))
        return sorted(out, key=lambda s: (s[0], -s[1]))

    @staticmethod
    def _derived_spans(pat: Pattern, line: str, allow: list[re.Pattern]) -> list[tuple[int, int, Pattern]]:
        """#38: a derived token is judged inside its surrounding non-space run: an allow line exempts
        an occurrence only when one of its matches in the run covers it (a public URL holding the
        token), never the bare token everywhere. The reported span stays the token itself."""
        out: list[tuple[int, int, Pattern]] = []
        if not pat.rx.search(line):
            return out
        for run in _RUN_RE.finditer(line):
            text = run.group(0)
            toks = [(m.start(), m.end()) for m in pat.rx.finditer(text) if m.end() > m.start()]
            if not toks:
                continue
            cover = [(a.start(), a.end()) for rx in allow for a in rx.finditer(text) if a.end() > a.start()]
            for s, e in toks:
                if not any(a <= s and e <= b for a, b in cover):
                    out.append((run.start() + s, run.start() + e, pat))
        return out

    def redact(self, text: str) -> str:
        """text with every non-allowed match of every pattern replaced by […]."""
        return _redact(text, self.spans(text))[0]

    def redact_path(self, path: str) -> str:
        """A location with private names replaced by […]; the directory-name rules (notes/, ...) are
        kept readable, since they are reported as internal files by path."""
        spans = [s for s in self.spans(path) if s[2].label not in PATH_READABLE]
        return _redact(path, spans)[0]

    def scan_text(self, text: str, path: str | None = None) -> list[tuple[int, Pattern, str]]:
        """(line number, pattern, redacted excerpt): one hit per pattern per line. `path`: the
        package-relative file the text is from, for path-scoped lines (#38). A line longer than
        LINE_CHUNK characters is scanned whole, in chunks overlapping by LINE_OVERLAP."""
        hits = []
        for n, line in enumerate(text.splitlines(), 1):
            seen: set = set()
            if len(line) <= LINE_CHUNK:
                hits += self._line_hits(n, line, self.spans(line, path), seen)
                continue
            for i in range(0, len(line), LINE_CHUNK - LINE_OVERLAP):
                chunk = line[i:i + LINE_CHUNK]
                hits += self._line_hits(n, chunk, self.spans(chunk, path), seen)
                if i + LINE_CHUNK >= len(line):
                    break
        return hits


def _redact(line: str, spans) -> tuple[str, list[int]]:
    """(redacted line, position in it of each span's placeholder). Overlapping spans share one."""
    out, starts, cursor, last = [], [], 0, None
    pos = 0
    for s, e, _pat in spans:
        if last is not None and s < cursor:      # overlaps the previous match: same placeholder
            starts.append(last)
            cursor = max(cursor, e)
            continue
        out.append(line[cursor:s])
        pos += s - cursor
        last = pos
        starts.append(pos)
        out.append(REDACTED)
        pos += len(REDACTED)
        cursor = e
    out.append(line[cursor:])
    return "".join(out), starts


_BOMS = ((b"\xff\xfe\x00\x00", "utf-32-le"), (b"\x00\x00\xfe\xff", "utf-32-be"), (b"\xef\xbb\xbf", "utf-8-sig"),
         (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\t]{4,}")


def _utf16_guess(data: bytes) -> str | None:
    """A BOM-less UTF-16 text: NUL in (nearly) every other byte of the first 8000 and in no other."""
    sample = data[:8000]
    if len(sample) < 8:
        return None
    even, odd = sample[0::2], sample[1::2]
    ze, zo = even.count(0) / len(even), odd.count(0) / len(odd)
    if zo > 0.6 and ze < 0.05:
        return "utf-16-le"
    if ze > 0.6 and zo < 0.05:
        return "utf-16-be"
    return None


def decode_blob(data: bytes) -> tuple[str, bool]:
    """(text to scan, binary). A UTF-8/16/32 byte-order mark, or BOM-less UTF-16, is decoded as such;
    other data with a NUL in its first 8000 bytes is binary, and its text is its printable ASCII runs
    of 4+ characters, one per line (what `strings` shows: metadata, embedded paths; compressed or
    otherwise encoded content is not inspected)."""
    for bom, enc in _BOMS:
        if data.startswith(bom):
            return data.decode(enc, errors="replace").lstrip("﻿"), False
    if b"\0" in data[:8000]:
        enc = _utf16_guess(data)
        if enc:
            return data.decode(enc, errors="replace"), False
        return "\n".join(m.group(0).decode("ascii") for m in _PRINTABLE_RUN.finditer(data)), True
    return data.decode("utf-8", errors="replace"), False


def _decode(data: bytes) -> str | None:
    """Compatibility: the text of data, None for binary (see decode_blob)."""
    text, binary = decode_blob(data[:MAX_SCAN_BYTES])
    return None if binary else text


def _hit(sc: Scanner, where: str, path: str, line, pat_label: str, kind: str, excerpt: str,
         commit: str | None = None, severity: str = BLOCK) -> dict:
    h = {"where": where, "path": sc.redact_path(path), "line": line, "pattern": pat_label, "kind": kind,
         "severity": severity, "excerpt": excerpt}
    if where == "history":
        h["commit"] = commit
    return h


def _skip(sc: Scanner, skipped: list[dict], hits: list[dict], where: str, path: str, reason: str,
          commit: str | None = None, unscanned_bytes: int | None = None) -> None:
    """Record a file or blob not (fully) scanned. A binary one is listed only (its printable runs were
    scanned); any other unscanned portion is also a blocking `unscanned` hit, unless an `allow:` line
    for the path matches `unscanned` in full."""
    rec = {"where": where, "path": sc.redact_path(path), "reason": reason}
    if commit:
        rec["commit"] = commit
    if unscanned_bytes is not None:
        rec["unscanned_bytes"] = unscanned_bytes
    if reason != "binary":
        rec["allowed"] = sc.unscanned_allowed(path)
        if not rec["allowed"]:
            hits.append(_hit(sc, where, path, None, UNSCANNED, "skipped",
                             f"not scanned: {reason}" + (f" ({unscanned_bytes} bytes)" if unscanned_bytes else ""),
                             commit))
    skipped.append(rec)


def _scan_bytes(sc: Scanner, data: bytes, size: int, where: str, rel: str, hits: list[dict],
                skipped: list[dict], commit=None, commit_of=None) -> None:
    """Scan one file's or blob's bytes (data: at most MAX_SCAN_BYTES of its `size` bytes). commit_of():
    the commit to label history hits with, computed only when there is one."""
    text, binary = decode_blob(data)
    found = sc.scan_text(text, rel)
    if found and commit_of is not None:
        commit = commit_of()
    for n, pat, excerpt in found:
        hits.append(_hit(sc, where, rel, None if binary else n, pat.label, pat.kind, excerpt, commit,
                         pat.severity))
    if binary:
        _skip(sc, skipped, hits, where, rel, "binary", commit)
    if size > len(data):
        if commit is None and commit_of is not None:
            commit = commit_of()
        _skip(sc, skipped, hits, where, rel, f"over the {MAX_SCAN_BYTES}-byte scan cap", commit, size - len(data))


def tree_files(root: pathlib.Path, git_mode: bool = True) -> tuple[list[str], set[str], bool]:
    """(package-relative files git would ship, the tracked ones, is a git checkout). Outside a git
    checkout, or with git_mode False (an export directory): every file under root except VCS metadata,
    a symbolic link to a directory included (as a file: its target is what is scanned)."""
    if git_mode and core.git(root, "rev-parse", "--is-inside-work-tree").returncode == 0:
        p = core.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        files = sorted({x for x in p.stdout.split("\0") if x})
        t = core.git(root, "ls-files", "-z", "--cached")
        return files, {x for x in t.stdout.split("\0") if x}, True
    files = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        links = [x for x in dirs if os.path.islink(os.path.join(d, x))]
        for nm in names + links:
            files.append(os.path.relpath(os.path.join(d, nm), root).replace(os.sep, "/"))
    return sorted(files), set(), False


def is_patterns_file(rel: str) -> bool:
    return rel.rsplit("/", 1)[-1] == PATTERNS_FILE


def scan_tree(sc: Scanner, root: pathlib.Path, git_mode: bool = True,
              skipped: list[dict] | None = None) -> tuple[list[dict], list[str], bool]:
    """(hits, warnings, patterns file tracked). A symbolic link is scanned as its target string (what
    git stores and publishes); files not fully scanned go to skipped."""
    files, tracked, is_git = tree_files(root, git_mode)
    hits: list[dict] = []
    warnings: list[str] = []
    skipped = [] if skipped is None else skipped
    pf_tracked = False
    for rel in files:
        if is_patterns_file(rel):
            if is_git and rel in tracked:
                pf_tracked = True
                warnings.append(f"{PATTERNS_FILE} is tracked: it lists the private strings, so publishing it "
                                f"publishes them (git rm --cached {PATTERNS_FILE}, and add it to .gitignore)")
                hits.append(_hit(sc, "tree", rel, None, "patterns-file", "internal-file",
                                 "the patterns file itself is tracked"))
            elif not is_git:
                hits.append(_hit(sc, "tree", rel, None, "patterns-file", "internal-file",
                                 "the patterns file is in the tree"))
            continue
        rule = internal_path(rel)
        if rule:
            hits.append(_hit(sc, "tree", rel, None, rule, "internal-file", "internal file by path"))
        f = root / rel
        if f.is_symlink():
            try:
                target = os.readlink(f)
            except OSError:
                _skip(sc, skipped, hits, "tree", rel, "unreadable symbolic link")
                continue
            for n, pat, excerpt in sc.scan_text(target, rel):
                hits.append(_hit(sc, "tree", rel, n, pat.label, pat.kind, excerpt, severity=pat.severity))
            continue
        if not f.is_file():
            continue
        try:
            size = f.stat().st_size
            with open(f, "rb") as fh:
                data = fh.read(MAX_SCAN_BYTES)
        except OSError:
            _skip(sc, skipped, hits, "tree", rel, "unreadable")
            continue
        _scan_bytes(sc, data, size, "tree", rel, hits, skipped)
    return hits, warnings, pf_tracked


def _clean_blob_shas(root: pathlib.Path) -> set[str]:
    """Blob ids of tracked files whose working-tree bytes are the index's: already scanned as tree
    (a symbolic link's blob is its target string, which the tree scan reads too)."""
    dirty = set(core.git(root, "diff", "--name-only", "--relative").stdout.split("\n"))
    out = set()
    for line in core.git(root, "ls-files", "-s").stdout.splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) >= 2 and path not in dirty:
            out.add(parts[1])
    return out


def _batch(root: pathlib.Path, args: list[str], stdin: bytes) -> bytes:
    p = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True,
                       env=core.scrubbed_env(), check=False)
    return p.stdout if p.returncode == 0 else b""


def _batch_objects(root: pathlib.Path, shas: list[str]):
    """(sha, size, the first MAX_SCAN_BYTES bytes) of each object, read with one cat-file --batch."""
    if not shas:
        return
    data = _batch(root, ["cat-file", "--batch"], ("\n".join(shas) + "\n").encode())
    i = 0
    while i < len(data):
        nl = data.index(b"\n", i)
        head = data[i:nl].decode().split()
        i = nl + 1
        if len(head) < 3 or head[1] == "missing":
            continue
        size = int(head[2])
        body, i = data[i:i + size], i + size + 1
        yield head[0], size, body[:MAX_SCAN_BYTES]


def _introducing_commit(root: pathlib.Path, sha: str) -> str | None:
    p = core.git(root, "log", "--all", "--format=%H", f"--find-object={sha}")
    lines = [x for x in p.stdout.split() if x]
    return lines[-1] if lines else None


def _parse_tag(body: bytes) -> tuple[str, str, str, str]:
    """(tag name, tagger name, tagger email, message) of an annotated tag object."""
    text = body.decode("utf-8", errors="replace")
    head, _, msg = text.partition("\n\n")
    tag = tname = temail = ""
    for line in head.splitlines():
        if line.startswith("tag "):
            tag = line[4:]
        elif line.startswith("tagger "):
            m = re.match(r"tagger (.*?) <([^>]*)>", line)
            if m:
                tname, temail = m.group(1), m.group(2)
    return tag, tname, temail, msg


def _short_ref(ref: str) -> str | None:
    """The published name of a branch, tag or remote-tracking ref (refs/remotes/R/x -> x); None for
    other refs (notes, stash, scratch namespaces), which carry no name of their own on GitHub."""
    parts = ref.split("/")
    if len(parts) >= 3 and parts[1] in ("heads", "tags"):
        return "/".join(parts[2:])
    if len(parts) >= 4 and parts[1] == "remotes":
        return "/".join(parts[3:])
    return None


def scan_history(sc: Scanner, root: pathlib.Path, skipped: list[dict] | None = None) -> list[dict]:
    """Every blob reachable from any ref under the scanned directory (once each, labelled by one path
    it had and the commit that introduced it; a symbolic link's blob is its target string), every
    commit message touching it, every author and committer identity of those commits, every annotated
    tag (name, tagger identity, message) and every branch and tag name. Root is the repository top
    when the whole repository is scanned."""
    skipped = [] if skipped is None else skipped
    prefix = core.git(root, "rev-parse", "--show-prefix").stdout.strip()
    skip = _clean_blob_shas(root)
    listing = core.git(root, "rev-list", "--all", "--objects").stdout
    check = _batch(root, ["cat-file", "--batch-check=%(objecttype) %(objectname) %(rest)"],
                   listing.encode()).decode("utf-8", errors="replace")
    blobs: dict[str, str] = {}
    tags: list[str] = []
    for line in check.splitlines():
        parts = line.split(" ", 2)
        if len(parts) >= 2 and parts[0] == "tag" and parts[1] not in tags:
            tags.append(parts[1])
            continue
        if len(parts) < 3 or parts[0] != "blob" or parts[1] in blobs:
            continue
        path = parts[2]
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        blobs[parts[1]] = path
    hits: list[dict] = []
    internal_seen: set[str] = set()
    to_read = []
    for sha, path in blobs.items():
        if sha in skip:
            continue                              # the same bytes are in the working tree: a tree hit
        if is_patterns_file(path):
            if path not in internal_seen:
                internal_seen.add(path)
                hits.append(_hit(sc, "history", path, None, "patterns-file", "internal-file",
                                 "the patterns file is in the history", _introducing_commit(root, sha)))
            continue
        rule = internal_path(path)
        if rule and path not in internal_seen:
            internal_seen.add(path)
            hits.append(_hit(sc, "history", path, None, rule, "internal-file", "internal file by path",
                             _introducing_commit(root, sha)))
        to_read.append(sha)
    for sha, size, body in _batch_objects(root, to_read):
        _scan_bytes(sc, body, size, "history", blobs[sha], hits, skipped,
                    commit_of=lambda s=sha: _introducing_commit(root, s))
    scope = ["--", "."] if prefix else []
    log = core.git(root, "log", "--all", "--format=%H%x00%B%x1e", *scope)
    for rec in log.stdout.split("\x1e"):
        sha, _, msg = rec.strip("\n").partition("\0")
        if not sha:
            continue
        for n, pat, excerpt in sc.scan_text(msg):
            hits.append(_hit(sc, "history", "commit-message", n, pat.label, pat.kind, excerpt, sha.strip(),
                             pat.severity))
    # author and committer identities: published with every commit; each distinct one once, labelled
    # by the oldest commit carrying it (git log lists newest first)
    idents: dict[tuple[str, str], str] = {}
    log = core.git(root, "log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce", *scope)
    for line in log.stdout.splitlines():
        f = line.split("\0")
        if len(f) == 5:
            idents[(f[1], f[2])] = f[0]
            idents[(f[3], f[4])] = f[0]
    for (name, email), sha in idents.items():
        for _n, pat, excerpt in sc.identity_hits(name, email):
            hits.append(_hit(sc, "history", "commit-identity", None, pat.label, pat.kind, excerpt, sha,
                             pat.severity))
    # annotated tags: name, tagger identity and message
    for sha, _size, body in _batch_objects(root, tags):
        tag, tname, temail, msg = _parse_tag(body)
        found = [("tag-name", n, p, ex) for n, p, ex in sc.scan_text(tag)]
        if tname or temail:
            found += [("tag-identity", None, p, ex) for _n, p, ex in sc.identity_hits(tname, temail)]
        found += [("tag-message", n, p, ex) for n, p, ex in sc.scan_text(msg)]
        for where, n, pat, excerpt in found:
            hits.append(_hit(sc, "history", where, n, pat.label, pat.kind, excerpt, sha, pat.severity))
    # branch and tag names (refs/remotes/R/x counts as x: what a push publishes)
    names: dict[str, None] = {}
    for ref in core.git(root, "for-each-ref", "--format=%(refname)").stdout.splitlines():
        short = _short_ref(ref.strip())
        if short and short != "HEAD":
            names.setdefault(short, None)
    for short in names:
        for n, pat, excerpt in sc.scan_text(short):
            hits.append(_hit(sc, "history", "ref-name", None, pat.label, pat.kind, excerpt, None, pat.severity))
    return hits


def blocking(hits: list[dict], strict: bool = False) -> list[dict]:
    """The hits that fail a scan: severity block, or every hit under --strict."""
    return [h for h in hits if strict or h.get("severity", BLOCK) == BLOCK]


def warn_families(hits: list[dict]) -> str:
    """The pattern families of the warn hits, for a summary line (file lines collapse to the file)."""
    fams = sorted({PATTERNS_FILE if h["kind"] == "file" else h["pattern"]
                   for h in hits if h.get("severity") == WARN})
    return ", ".join(fams)


def repository_scope(root: pathlib.Path) -> tuple[pathlib.Path, str] | None:
    """(repository top, the package's prefix with its trailing `/`) when root is a subdirectory of its
    git repository, else None."""
    p = core.git(root, "rev-parse", "--show-toplevel")
    top = p.stdout.strip()
    if p.returncode != 0 or not top or os.path.realpath(top) == os.path.realpath(root):
        return None
    prefix = core.git(root, "rev-parse", "--show-prefix").stdout.strip()
    return pathlib.Path(top), prefix


def scan(pkg: dict, root: pathlib.Path, history: bool = True,
         patterns_root: pathlib.Path | None = None, source_repo: pathlib.Path | None = None,
         export: bool = False, strict: bool = False, whole_repo: bool = False) -> dict:
    """The scan result as the --json payload body: ok, hits[], tree_hits, history_hits, block_hits,
    warn_hits, strict, scope, skipped[], patterns_source, warnings[]. ok: no blocking hit (strict: no
    hit). export=True: root is a plain exported directory (every file, no git, no history);
    patterns_root/source_repo then name the package checkout whose patterns file and consent apply
    (default: root). whole_repo=True (promotion): when root is a subdirectory of its git repository,
    the whole repository is scanned (tree, every blob, every commit), since making the GitHub
    repository public publishes all of it; the package's patterns file still applies."""
    scope, prefix = "package", ""
    if whole_repo and not export:
        rs = repository_scope(root)
        if rs is not None:
            patterns_root, source_repo = patterns_root or root, source_repo or root
            root, prefix = rs
            scope = "repository"
    sc = Scanner(pkg, patterns_root or root, source_repo or root, scope_prefix=prefix)
    skipped: list[dict] = []
    tree, warnings, pf_tracked = scan_tree(sc, root, git_mode=not export, skipped=skipped)
    history = bool(history and not export and core.git_head(root) is not None)
    hist = scan_history(sc, root, skipped) if history else []
    hits = tree + hist
    source = {"file": PATTERNS_FILE if sc.file_present else None, "file_patterns": sc.n_file,
              "allow": len(sc.allow), "allow_scoped": sc.n_allow_scoped, "scoped_patterns": sc.n_scoped,
              "tracked": pf_tracked,
              "builtin": [label for label, _rx in BUILTIN + BUILTIN_WARN],
              "warn_only": [label for label, _rx in BUILTIN_WARN], "file_warn_patterns": sc.n_file_warn,
              "latex_label_allow": list(LATEX_LABEL_ALLOW),
              "derived": sorted({p.label for p in sc.derived}),
              "internal_paths": [f"{d}/" for d in INTERNAL_DIRS] + [f"*{s}" for s in INTERNAL_SUFFIXES]}
    n_warn = sum(1 for h in hits if h["severity"] == WARN)
    return {"ok": not blocking(hits, strict), "hits": hits, "tree_hits": len(tree), "history_hits": len(hist),
            "block_hits": len(hits) - n_warn, "warn_hits": n_warn, "strict": bool(strict),
            "latex_label_hits": sum(1 for h in hits if h["pattern"] == "latex-label"),
            "history_scanned": history, "scope": scope, "package_prefix": prefix,
            "skipped": skipped,
            "skipped_blocking": sum(1 for s in skipped if s["reason"] != "binary" and not s.get("allowed")),
            "builtins_only": sc.n_file - sc.n_file_warn == 0,
            "patterns_source": source, "warnings": sc.problems + warnings}


BUILTINS_ONLY_NOTE = (f"  note: no project-specific patterns: built-ins only (no blocking line in {PATTERNS_FILE}); "
                      "0 hits means none of these patterns matched, not that nothing private is there")

SCOPE_NOTE = ("scanned the repository root: the package is a subdirectory, and promotion publishes the "
              "whole repository")


def skipped_note(result: dict) -> str:
    """'' or the summary-line note on what was not (fully) scanned."""
    sk = result.get("skipped") or []
    if not sk:
        return ""
    nb = sum(1 for s in sk if s["reason"] == "binary")
    other = len(sk) - nb
    parts = [f"{nb} binary (printable text only)"] if nb else []
    if other:
        parts.append(f"{other} not scanned in full ({result.get('skipped_blocking', other)} blocking)")
    return f"; {len(sk)} file(s)/blob(s) not fully scanned: " + ", ".join(parts)


def hit_line(h: dict) -> str:
    loc = h["path"] + (f":{h['line']}" if h.get("line") else "")
    if h["where"] == "history":
        loc = f"{str(h.get('commit') or '?')[:12]} {loc}"
    mark = "WARN " if h.get("severity") == WARN else ""
    return f"  {mark}{h['where']:<7}  {loc}  [{h['pattern']}]  {h['excerpt']}"


def report_lines(name: str, result: dict, quiet: bool = False) -> list[str]:
    """The human report. The summary line names no path (#38): it is pasted into release notes
    verbatim, and the checkout is the one the registry record names. With hits, the verdict line comes
    right after the summary and patterns lines and again last; blocking hits are listed before the
    warn-only ones, which follow under a count line. With no hit and no blocking line in the patterns
    file, a note line says the built-ins alone ran (field report). quiet (#44): the summary, patterns, note, warn-only count
    and verdict lines only, the verdict once; no WARN or hit line. --json is not affected."""
    src = result["patterns_source"]
    n = len(result["hits"])
    nb, nw = result["block_hits"], result["warn_hits"]
    lines = [f"public-safety for {name}: {n} hit(s) ({result['tree_hits']} tree, "
             f"{result['history_hits']} history" + ("" if result["history_scanned"] else "; history not scanned")
             + f"); {nb} blocking, {nw} warning(s)" + (" (--strict: warnings block)" if result["strict"] else "")
             + f"; {result['latex_label_hits']} from latex-label" + skipped_note(result)
             + (f"; {SCOPE_NOTE}" if result.get("scope") == "repository" else ""),
             f"  patterns: {src['file'] or 'no ' + PATTERNS_FILE} ({src['file_patterns']} pattern(s), "
             f"{src['allow']} allow, {src['file_warn_patterns']} warn-only, "
             f"{src['scoped_patterns'] + src['allow_scoped']} path-scoped) + {len(src['builtin'])} built-in + {len(src['derived'])} registry-derived "
             f"({', '.join(src['derived']) or 'none'}) + internal paths"]
    if not n and result.get("builtins_only"):   # field report: the built-ins cannot know a project's own private terms
        lines.append(BUILTINS_ONLY_NOTE)
    fail = blocking(result["hits"], result["strict"])
    rest = [h for h in result["hits"] if not any(h is f for f in fail)]
    if not n:
        verdict = "ok: no private pattern found"
    elif not fail:
        verdict = (f"ok: 0 blocking hit(s), {nw} warning(s) ({warn_families(result['hits'])}): review each; "
                   f"a `block: REGEX` line in {PATTERNS_FILE} or --strict makes them blocking")
    else:
        verdict = (f"{len(fail)} blocking hit(s): fix the tree (and rewrite history), or publish a fresh-history "
                   f"tree with `lean-pkg release {name} --fresh-repo DIR`")
    if quiet:
        if rest:
            lines.append(f"  warn-only hit(s): {len(rest)} ({warn_families(rest)}); they do not block")
        return lines + [verdict]
    if n:
        lines.append(verdict)   # also up front: dozens of warn lines must not push the verdict out of view
    lines += [f"  WARN {w}" for w in result["warnings"]]
    lines += [f"  skipped: {s['where']} {(s.get('commit') or '')[:12] + ' ' if s.get('commit') else ''}{s['path']}  "
              f"({s['reason']}" + ("; allowed by an `allow: ... unscanned` line" if s.get("allowed") else "") + ")"
              for s in result.get("skipped") or []]
    lines += [hit_line(h) for h in fail]           # blocking hits first, then the warn-only ones as a group
    if rest:
        lines.append(f"  warn-only hit(s): {len(rest)} ({warn_families(rest)}); they do not block")
        lines += [hit_line(h) for h in rest]
    lines.append(verdict)
    return lines


def refusal(name: str, n: int, n_warn: int = 0) -> str:
    extra = f" (+{n_warn} warning(s))" if n_warn else ""
    return (f"public-safety: {n} hit(s){extra} — fix the tree, or publish a fresh-history tree with "
            f"`release --fresh-repo`; see `lean-pkg public-safety {name}`")


def checklist_item(pkg: dict, repo: pathlib.Path) -> tuple[tuple[str, str], dict]:
    """The promote/publish checklist line and the counts for the JSON payload. The whole repository is
    scanned when the package is a subdirectory of it: the flip publishes all of it."""
    result = scan(pkg, repo, history=True, whole_repo=True)
    n, nb, nw = len(result["hits"]), result["block_hits"], result["warn_hits"]
    counts = {"skipped": False, "hits": n, "tree_hits": result["tree_hits"],
              "history_hits": result["history_hits"], "block_hits": nb, "warn_hits": nw,
              "scope": result["scope"], "unscanned": len(result["skipped"]),
              "unscanned_blocking": result["skipped_blocking"]}
    where = " (whole repository: the package is a subdirectory)" if result["scope"] == "repository" else ""
    if nb:
        return ("✗", refusal(pkg["name"], nb, nw) + where), counts
    if nw:   # #36: warn-only hits (latex-label, `warn:` lines) never block on their own
        return ("WARN", f"public-safety{where}: 0 blocking hit(s), {nw} warning(s) ({warn_families(result['hits'])}) "
                        f"— review with `lean-pkg public-safety {pkg['name']}`"), counts
    return ("✓", f"public-safety{where}: no private pattern in the tree or the history"
                 + skipped_note(result)), counts


def cmd_public_safety(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    pkg = core.get_pkg(reg, args.name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{args.name}: local_path does not exist: {repo}")
    result = scan(pkg, repo, history=args.history, strict=args.strict,
                  whole_repo=not getattr(args, "package_only", False))
    if getattr(args, "json_out", False):
        core.print_json(dict(result, name=args.name))
    else:
        print("\n".join(report_lines(args.name, result, quiet=getattr(args, "quiet", False))))
    return 0 if result["ok"] else 1


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("public-safety", help="read-only: scan the package's tree and git history for private "
                                             "patterns (exit 1 on hits; promote runs it)")
    s.add_argument("name")
    s.add_argument("--history", action=argparse.BooleanOptionalAction, default=True,
                   help="also scan every blob and commit message reachable from any ref (default: on)")
    s.add_argument("--strict", action="store_true",
                   help="warn-only hits (latex-label, `warn:` lines) fail the scan too (exit 1)")
    s.add_argument("--package-only", action="store_true",
                   help="scan only the package directory even when it is a subdirectory of its repository "
                        "(default: the whole repository then, which is what promote publishes; the package "
                        "directory alone is what `release --fresh-repo` exports)")
    s.add_argument("--quiet", action="store_true",
                   help="only the summary, patterns, warn-only count and verdict lines; no per-hit line "
                        "(--json is unchanged)")
    add_json(s)
    s.set_defaults(fn=cmd_public_safety)
