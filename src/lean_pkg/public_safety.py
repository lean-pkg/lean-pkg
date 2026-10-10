# lean-pkg public-safety -- private strings in a package's tree and git history
"""`lean-pkg public-safety NAME [--history|--no-history] [--strict]` (#35, #36): before a package
goes public, scan what would be published (the working tree git would ship) and, by default,
everything the public flip exposes (every blob and commit message reachable from any ref, the
approach of lean-pkg's own `make history`) for private patterns:

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
  commit identities, `consent[]` entries, `allow:` lines, reserved example domains, `git@`
  SSH remotes).
- user and host names derived at scan time from the registry record's own `local_path`, `agent`
  and `workspace` fields (never stored).
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
MAX_SCAN_BYTES = 5_000_000
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
    ("private-file-ref", r"[A-Za-z0-9_-][A-Za-z0-9_./-]*\.private\b"),
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


def excluded_from_export(rel: str) -> str | None:
    """Why a path is dropped from a fresh-history export, else None."""
    if rel == PATTERNS_FILE or rel.endswith("/" + PATTERNS_FILE):
        return "patterns file"
    return internal_path(rel)


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
    """User and host names from the record's own local_path, agent and workspace fields, derived now
    (never stored). Labels name the field, never the value."""
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
                         ("lease.agent", lease.get("agent")), ("lease.workspace", lease.get("workspace"))):
        for tok in _tokens(value):
            found.setdefault(tok, f"registry {field}")
    pats = []
    for tok, label in found.items():
        rx = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok) + r"(?![A-Za-z0-9_])", re.I)
        pats.append(Pattern(label, "derived", rx))
    return pats


def declared_emails(pkg: dict, repo: pathlib.Path | None) -> set[str]:
    """The author's declared addresses: commit identities of the repository, plus any address in the
    record's consent entries."""
    out: set[str] = set()
    if repo is not None and core.git_head(repo):
        p = core.git(repo, "log", "--all", "--format=%ae%n%ce")
        out.update(x.strip().casefold() for x in p.stdout.splitlines() if x.strip())
    for c in pkg.get("consent") or []:
        if isinstance(c, dict):
            for v in c.values():
                out.update(e.casefold() for e in re.findall(EMAIL_RE, str(v)))
    return out


class Scanner:
    def __init__(self, pkg: dict, patterns_root: pathlib.Path, repo_for_emails: pathlib.Path | None):
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

    def spans(self, line: str, path: str | None = None) -> list[tuple[int, int, Pattern]]:
        pats, allow = self.active(path)
        out = []
        for pat in pats:
            for m in pat.rx.finditer(line):
                if m.end() > m.start() and not self.allowed(pat, m.group(0), allow):
                    out.append((m.start(), m.end(), pat))
        return sorted(out, key=lambda s: (s[0], -s[1]))

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
        package-relative file the text is from, for path-scoped lines (#38)."""
        hits = []
        for n, line in enumerate(text.splitlines(), 1):
            if len(line) > 20000:
                line = line[:20000]
            spans = self.spans(line, path)
            if not spans:
                continue
            red, starts = _redact(line, spans)
            seen = set()
            for (s, _e, pat), pos in zip(spans, starts):
                if pat.label in seen:
                    continue
                seen.add(pat.label)
                lo, hi = max(0, pos - CONTEXT), min(len(red), pos + len(REDACTED) + CONTEXT)
                excerpt = " ".join(red[lo:hi].split())
                hits.append((n, pat, excerpt))
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


def _decode(data: bytes) -> str | None:
    if b"\0" in data[:8000]:
        return None                               # binary
    return data[:MAX_SCAN_BYTES].decode("utf-8", errors="replace")


def _hit(sc: Scanner, where: str, path: str, line, pat_label: str, kind: str, excerpt: str,
         commit: str | None = None, severity: str = BLOCK) -> dict:
    h = {"where": where, "path": sc.redact_path(path), "line": line, "pattern": pat_label, "kind": kind,
         "severity": severity, "excerpt": excerpt}
    if where == "history":
        h["commit"] = commit
    return h


def tree_files(root: pathlib.Path, git_mode: bool = True) -> tuple[list[str], set[str], bool]:
    """(package-relative files git would ship, the tracked ones, is a git checkout). Outside a git
    checkout, or with git_mode False (an export directory): every file under root except VCS metadata."""
    if git_mode and core.git(root, "rev-parse", "--is-inside-work-tree").returncode == 0:
        p = core.git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        files = sorted({x for x in p.stdout.split("\0") if x})
        t = core.git(root, "ls-files", "-z", "--cached")
        return files, {x for x in t.stdout.split("\0") if x}, True
    files = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for nm in names:
            files.append(os.path.relpath(os.path.join(d, nm), root).replace(os.sep, "/"))
    return sorted(files), set(), False


def scan_tree(sc: Scanner, root: pathlib.Path, git_mode: bool = True) -> tuple[list[dict], list[str], bool]:
    """(hits, warnings, patterns file tracked)."""
    files, tracked, is_git = tree_files(root, git_mode)
    hits: list[dict] = []
    warnings: list[str] = []
    pf_tracked = False
    for rel in files:
        if rel == PATTERNS_FILE:
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
        if f.is_symlink() or not f.is_file():
            continue
        try:
            text = _decode(f.read_bytes())
        except OSError:
            continue
        if text is None:
            continue
        for n, pat, excerpt in sc.scan_text(text, rel):
            hits.append(_hit(sc, "tree", rel, n, pat.label, pat.kind, excerpt, severity=pat.severity))
    return hits, warnings, pf_tracked


def _clean_blob_shas(root: pathlib.Path) -> set[str]:
    """Blob ids of tracked files whose working-tree bytes are the index's: already scanned as tree."""
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


def _introducing_commit(root: pathlib.Path, sha: str) -> str | None:
    p = core.git(root, "log", "--all", "--format=%H", f"--find-object={sha}")
    lines = [x for x in p.stdout.split() if x]
    return lines[-1] if lines else None


def scan_history(sc: Scanner, root: pathlib.Path) -> list[dict]:
    """Every blob reachable from any ref under the package directory (once each, labelled by one path
    it had and the commit that introduced it) and every commit message touching it."""
    prefix = core.git(root, "rev-parse", "--show-prefix").stdout.strip()
    skip = _clean_blob_shas(root)
    listing = core.git(root, "rev-list", "--all", "--objects").stdout
    check = _batch(root, ["cat-file", "--batch-check=%(objecttype) %(objectname) %(rest)"],
                   listing.encode()).decode("utf-8", errors="replace")
    blobs: dict[str, str] = {}
    for line in check.splitlines():
        parts = line.split(" ", 2)
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
        if path == PATTERNS_FILE:
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
    if to_read:
        data = _batch(root, ["cat-file", "--batch"], ("\n".join(to_read) + "\n").encode())
        i = 0
        while i < len(data):
            nl = data.index(b"\n", i)
            head = data[i:nl].decode().split()
            i = nl + 1
            if len(head) < 3 or head[1] == "missing":
                continue
            size = int(head[2])
            body, i = data[i:i + size], i + size + 1
            text = _decode(body)
            if text is None:
                continue
            found = sc.scan_text(text, blobs[head[0]])
            if found:
                commit = _introducing_commit(root, head[0])
                for n, pat, excerpt in found:
                    hits.append(_hit(sc, "history", blobs[head[0]], n, pat.label, pat.kind, excerpt, commit,
                                     pat.severity))
    log = core.git(root, "log", "--all", "--format=%H%x00%B%x1e", *(["--", "."] if prefix else []))
    for rec in log.stdout.split("\x1e"):
        sha, _, msg = rec.strip("\n").partition("\0")
        if not sha:
            continue
        for n, pat, excerpt in sc.scan_text(msg):
            hits.append(_hit(sc, "history", "commit-message", n, pat.label, pat.kind, excerpt, sha.strip(),
                             pat.severity))
    return hits


def blocking(hits: list[dict], strict: bool = False) -> list[dict]:
    """The hits that fail a scan: severity block, or every hit under --strict."""
    return [h for h in hits if strict or h.get("severity", BLOCK) == BLOCK]


def warn_families(hits: list[dict]) -> str:
    """The pattern families of the warn hits, for a summary line (file lines collapse to the file)."""
    fams = sorted({PATTERNS_FILE if h["kind"] == "file" else h["pattern"]
                   for h in hits if h.get("severity") == WARN})
    return ", ".join(fams)


def scan(pkg: dict, root: pathlib.Path, history: bool = True,
         patterns_root: pathlib.Path | None = None, source_repo: pathlib.Path | None = None,
         export: bool = False, strict: bool = False) -> dict:
    """The scan result as the --json payload body: ok, hits[], tree_hits, history_hits, block_hits,
    warn_hits, strict, patterns_source, warnings[]. ok: no blocking hit (strict: no hit). export=True: root is a plain exported directory (every file, no git,
    no history); patterns_root/source_repo then name the package checkout whose patterns file and
    commit identities apply (default: root)."""
    sc = Scanner(pkg, patterns_root or root, source_repo or root)
    tree, warnings, pf_tracked = scan_tree(sc, root, git_mode=not export)
    history = bool(history and not export and core.git_head(root) is not None)
    hist = scan_history(sc, root) if history else []
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
            "history_scanned": history,
            "patterns_source": source, "warnings": sc.problems + warnings}


def hit_line(h: dict) -> str:
    loc = h["path"] + (f":{h['line']}" if h.get("line") else "")
    if h["where"] == "history":
        loc = f"{str(h.get('commit') or '?')[:12]} {loc}"
    mark = "WARN " if h.get("severity") == WARN else ""
    return f"  {mark}{h['where']:<7}  {loc}  [{h['pattern']}]  {h['excerpt']}"


def report_lines(name: str, result: dict) -> list[str]:
    """The human report. The summary line names no path (#38): it is pasted into release notes
    verbatim, and the checkout is the one the registry record names."""
    src = result["patterns_source"]
    n = len(result["hits"])
    nb, nw = result["block_hits"], result["warn_hits"]
    lines = [f"public-safety for {name}: {n} hit(s) ({result['tree_hits']} tree, "
             f"{result['history_hits']} history" + ("" if result["history_scanned"] else "; history not scanned")
             + f"); {nb} blocking, {nw} warning(s)" + (" (--strict: warnings block)" if result["strict"] else "")
             + f"; {result['latex_label_hits']} from latex-label",
             f"  patterns: {src['file'] or 'no ' + PATTERNS_FILE} ({src['file_patterns']} pattern(s), "
             f"{src['allow']} allow, {src['file_warn_patterns']} warn-only, "
             f"{src['scoped_patterns'] + src['allow_scoped']} path-scoped) + {len(src['builtin'])} built-in + {len(src['derived'])} registry-derived "
             f"({', '.join(src['derived']) or 'none'}) + internal paths"]
    lines += [f"  WARN {w}" for w in result["warnings"]]
    lines += [hit_line(h) for h in result["hits"]]
    nfail = len(blocking(result["hits"], result["strict"]))
    if not n:
        lines.append("ok: no private pattern found")
    elif not nfail:
        lines.append(f"ok: 0 blocking hit(s), {nw} warning(s) ({warn_families(result['hits'])}): review each; "
                     f"a `block: REGEX` line in {PATTERNS_FILE} or --strict makes them blocking")
    else:
        lines.append(f"{nfail} blocking hit(s): fix the tree (and rewrite history), or publish a fresh-history "
                     f"tree with `lean-pkg release {name} --fresh-repo DIR`")
    return lines


def refusal(name: str, n: int, n_warn: int = 0) -> str:
    extra = f" (+{n_warn} warning(s))" if n_warn else ""
    return (f"public-safety: {n} hit(s){extra} — fix the tree, or publish a fresh-history tree with "
            f"`release --fresh-repo`; see `lean-pkg public-safety {name}`")


def checklist_item(pkg: dict, repo: pathlib.Path) -> tuple[tuple[str, str], dict]:
    """The promote/publish checklist line and the counts for the JSON payload."""
    result = scan(pkg, repo, history=True)
    n, nb, nw = len(result["hits"]), result["block_hits"], result["warn_hits"]
    counts = {"skipped": False, "hits": n, "tree_hits": result["tree_hits"],
              "history_hits": result["history_hits"], "block_hits": nb, "warn_hits": nw}
    if nb:
        return ("✗", refusal(pkg["name"], nb, nw)), counts
    if nw:   # #36: warn-only hits (latex-label, `warn:` lines) never block on their own
        return ("WARN", f"public-safety: 0 blocking hit(s), {nw} warning(s) ({warn_families(result['hits'])}) "
                        f"— review with `lean-pkg public-safety {pkg['name']}`"), counts
    return ("✓", "public-safety: no private pattern in the tree or the history"), counts


def cmd_public_safety(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    pkg = core.get_pkg(reg, args.name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{args.name}: local_path does not exist: {repo}")
    result = scan(pkg, repo, history=args.history, strict=args.strict)
    if getattr(args, "json_out", False):
        core.print_json(dict(result, name=args.name))
    else:
        print("\n".join(report_lines(args.name, result)))
    return 0 if result["ok"] else 1


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("public-safety", help="read-only: scan the package's tree and git history for private "
                                             "patterns (exit 1 on hits; promote runs it)")
    s.add_argument("name")
    s.add_argument("--history", action=argparse.BooleanOptionalAction, default=True,
                   help="also scan every blob and commit message reachable from any ref (default: on)")
    s.add_argument("--strict", action="store_true",
                   help="warn-only hits (latex-label, `warn:` lines) fail the scan too (exit 1)")
    add_json(s)
    s.set_defaults(fn=cmd_public_safety)
