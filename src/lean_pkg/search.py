# lean-pkg search -- claim-level prior-art search before building
"""`lean-pkg search TERM`: one block per source, in a fixed order, each with its
revision and state (`ok` or `skipped: <reason>`), and a closing line naming every
source consulted. Local sources (the registry's packages and cached claims, the
public index) always run and are scored deterministically: exact alias (or package
name) 3, keyword 2, every term token present 1; no embeddings. A registry hit is a
package (`hit_kind: package`: name, notes, owner_project, repo) or a claim. A package with no synced
claims is searched by declaration instead: its checkout at HEAD (`git grep` over tracked `*.lean`
files, declaration names and their docstrings), `hit_kind: declaration (unclaimed)`, trust none,
ranked after the claim and package hits; a package without a git checkout is reported as not
searched (#49). Network sources (lean_pkg.adapters) run unless --offline; each is reported
separately and never merged into the local score. A skipped source is never a zero.
"""

from __future__ import annotations

import contextlib
import hashlib
import pathlib
import re
import subprocess
import sys

from . import claims as cl
from . import core
from . import index as idx
from . import lean_scan
from . import adapters
from .adapters import fold, github, leansearch, loogle, palomar, reservoir

LOCAL_SOURCES = ("registry", "index")
NETWORK_SOURCES = ("loogle", "leansearch", "reservoir", "palomar", "github")
SOURCES = LOCAL_SOURCES + NETWORK_SOURCES
ADAPTERS = {"loogle": loogle, "leansearch": leansearch, "reservoir": reservoir,
            "palomar": palomar, "github": github}
LATE_STATUSES = ("experimental", "superseded")
HIT_KEYS = ("source", "id", "package", "decl", "kind", "summary", "trust", "status", "pin",
            "file", "score", "where", "hit_kind", "repo")
# A claim hit gains `conditional: true` only when the claim is conditional (proposal 0003, #320; as `claims list`
# marks it): a registry claim that lists `hypotheses`, or an index claim whose derived `status` is conditional.
OPTIONAL_HIT_KEYS = ("conditional",)
PACKAGE_FIELDS = ("name", "notes", "owner_project", "repo")
_TOKEN_SPLIT = re.compile(r"[\W_]+", re.UNICODE)
# A home directory anywhere; a cluster or system root (/scratch/<x>, /work/<x>, /gpfs/<x>, /srv/<x>, /mnt/<x>,
# /tmp/<x>, /lustre/<x>) only where a path starts (not inside a URL such as https://host/work/page).
_PATHLIKE = re.compile(r"(?:~|/home/|/Users/|[A-Za-z]:\\|(?<![\w.:/-])/(?:scratch|work|gpfs|srv|mnt|tmp|lustre)/"
                       r"(?=[^\s/]))\S*")
ROUTES = ("signature", "natural-language")
SIGNATURE_SOURCES = ("loogle",)       # sources that only take declaration/type patterns
LOOGLE_NL_REASON = ("natural-language query; Loogle takes declaration patterns — try e.g. "
                    "`?a + ?b = ?b + ?a` (--loogle-query PATTERN sends a pattern of its own; --loogle forces "
                    "the term)")
# Appended to Loogle's own parse error ("Unknown identifier exponential", "expected end of input"): the term
# reached Loogle but mixed prose with Lean (field report).
LOOGLE_SYNTAX_HINT = ("; Loogle takes Lean names, terms and patterns (`Real.exp`, `?a * ?b = ?b * ?a`, "
                      "`\"martingale\"` for a name fragment), not prose: keep the prose as TERM and pass the "
                      "Lean part with --loogle-query")
_SIG_MARKERS = ("→", "->", "∀", "∃", "fun ", "λ", ":", "(", "?", "_", "⊢", '"', "↔", "<->")
_DOTTED = re.compile(r"\b[A-Z][A-Za-z0-9']*\.[A-Za-z_]")
DECL_HIT_KIND = "declaration (unclaimed)"
DECL_KEYWORDS = lean_scan.KEYWORDS
DECL_HITS_PER_PACKAGE = 10     # declaration hits kept per package; the rest are counted, not listed
DECL_FILES_MAX = 2000          # files read per package after the git grep prefilter; past it, coverage says so
EXCERPT_MAX = 160


def scrub_paths(text) -> str:
    """Replace anything that looks like a local path with <path> (skip reasons come from outside)."""
    return _PATHLIKE.sub("<path>", str(text or ""))


def tokens(text) -> list[str]:
    """Lowercase alphanumeric tokens of any script; accents folded; split on non-word runs."""
    return [tok for tok in _TOKEN_SPLIT.split(fold(text)) if tok]


def norm(text) -> str:
    return " ".join(tokens(text))


def score_claim(term: str, claim: dict) -> tuple[int, str | None]:
    """(score, where): 3 exact alias, 2 keyword, 1 every term token occurs in decl, summary,
    aliases, keywords or citation; 0 no match. Case-insensitive; tokens split on non-alphanumerics."""
    q, qt = norm(term), set(tokens(term))
    if not qt:
        return 0, None
    if any(norm(a) == q for a in claim.get("aliases") or []):
        return 3, "alias"
    if any(norm(k) == q for k in claim.get("keywords") or []):
        return 2, "keyword"
    words: set[str] = set()
    for f in ("decl", "summary", "citation"):
        words.update(tokens(claim.get(f)))
    for f in ("aliases", "keywords"):
        for a in claim.get(f) or []:
            words.update(tokens(a))
    return (1, "token") if qt <= words else (0, None)


def score_package(term: str, name: str, pkg: dict) -> tuple[int, str | None]:
    """(score, where) over a package's own metadata: 3 the name equals the term, 1 every term
    token occurs in name, notes, owner_project or repo; 0 no match."""
    q, qt = norm(term), set(tokens(term))
    if not qt:
        return 0, None
    if norm(name) == q:
        return 3, "name"
    words: set[str] = set()
    for f in PACKAGE_FIELDS:
        words.update(tokens(name if f == "name" else pkg.get(f)))
    return (1, "token") if qt <= words else (0, None)


def route_of(term: str) -> str:
    """`signature` when the term looks like a Lean declaration or type pattern (an arrow, binder,
    colon, parenthesis, `?` metavariable, `_`, `⊢`, a quoted name fragment, a math operator, or a
    dotted name such as `Nat.succ` / `Mathlib.Order`); otherwise `natural-language`."""
    import unicodedata
    t = str(term or "")
    if any(m in t for m in _SIG_MARKERS) or _DOTTED.search(t):
        return "signature"
    if any(unicodedata.category(ch) == "Sm" for ch in t):    # = + < > ≤ ∑ ∫ ∈ ⊆ ...
        return "signature"
    return "natural-language"


def rank(hits: list[dict]) -> list[dict]:
    """Reusable before experimental and superseded; then by score, then by id."""
    return sorted(hits, key=lambda h: (h["status"] in LATE_STATUSES, -h["score"], str(h["id"])))


def _hit(source: str, c: dict, package: str, trust, pin, score: int, where: str) -> dict:
    hit = {"source": source, "id": c.get("id"), "package": package, "decl": c.get("decl"),
           "kind": c.get("kind"), "summary": c.get("summary"), "trust": trust or "none",
           "status": c.get("status") or "reusable", "pin": pin, "file": c.get("file"),
           "score": score, "where": where, "hit_kind": "claim", "repo": None}
    if c.get("hypotheses") or c.get("conditional") is True:     # a conditional claim, always (proposal 0003)
        hit["conditional"] = True
    return hit


def _package_hit(name: str, pkg: dict, score: int, where: str) -> dict:
    return {"source": "registry", "id": name, "package": name, "decl": None, "kind": None,
            "summary": pkg.get("notes") or None, "trust": cl.package_trust(pkg),
            "status": pkg.get("status"), "pin": None, "file": None, "score": score, "where": where,
            "hit_kind": "package", "repo": scrub_paths(pkg.get("repo")) if pkg.get("repo") else None}


def block(source: str, state: str, reason: str | None = None, revision: str | None = None,
          hits: list | None = None, route: str | None = None, partial: list | None = None,
          capped: list | None = None, cap_limit: int | None = None, total: int | None = None,
          incomplete: str | None = None, cache_writes: list | None = None) -> dict:
    """One source's answer. `partial`/`capped` name parts of a network source that failed or hit their
    result limit (`total`: the matches it found, when it lists fewer); `incomplete` says why a local source
    did not read everything (the registry's file cap or unreadable files, the index's unreadable entries)."""
    return {"source": source, "state": state, "reason": scrub_paths(reason) if reason else reason,
            "revision": revision, "hits": hits or [], "route": route, "partial": list(partial or []),
            "capped": list(capped or []), "cap_limit": cap_limit if capped else None,
            "total": total if capped else None, "incomplete": incomplete,
            "cache_writes": list(cache_writes or [])}


# --------------------------------------------------------------------------
# declaration fallback (#49): packages without synced claims

def has_synced_claims(pkg: dict) -> bool:
    """True when the registry caches at least one synced claim for the package (`claims coverage`
    `synced`). `empty`, `unsynced` and `none` all leave search blind to the package's claims."""
    return bool(pkg.get("claims"))


def lean_declarations(text: str) -> list[tuple[int, str, str, str | None, str, str]]:
    """(line, keyword, qualified name, docstring or None, stripped line, name as written) for every
    declaration (lean_pkg.lean_scan: comments and strings skipped, docstrings attached across attribute
    lines, `namespace`/`section`/`end` tracked so the name is fully qualified)."""
    return lean_scan.declarations(text)


def _doc_text(doc: str | None) -> str:
    if not doc:
        return ""
    d = doc.strip()
    d = d[3:] if d.startswith("/--") else d
    d = d[:-2] if d.endswith("-/") else d
    return " ".join(d.split())


def _excerpt(text: str) -> str:
    t = scrub_paths(" ".join(str(text).split()))
    return t if len(t) <= EXCERPT_MAX else t[:EXCERPT_MAX - 1].rstrip() + "…"


def _decl_terms(term: str) -> list[str]:
    toks = tokens(term)
    long = [t for t in toks if len(t) >= 2]
    return sorted(set(long or toks))


def _decl_order(h: dict) -> tuple:
    """Name matches first, then package, file and line (numeric)."""
    rel, _, line = h["file"].rpartition(":")
    return (h["where"] != "name", h["package"], rel, int(line) if line.isdigit() else 0)


def _read_blobs(repo: pathlib.Path, rev: str, rels: list[str]) -> dict[str, bytes]:
    """{rel: bytes} for package-relative paths at `rev`, through one `git cat-file --batch` (one process for
    thousands of files instead of one `git show` each). A path git reports missing, or one cat-file cannot
    name (a newline in it), is left out: the caller counts it as unreadable."""
    rels = [r for r in rels if "\n" not in r]
    if not rels:
        return {}
    spec = "".join(f"{rev}:./{r}\n" for r in rels).encode("utf-8", errors="surrogateescape")
    p = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch"], input=spec, capture_output=True,
                       env=core.scrubbed_env())
    out, data, i = {}, p.stdout, 0
    for rel in rels:
        nl = data.find(b"\n", i)
        if nl < 0:
            break
        header = data[i:nl].split()
        i = nl + 1
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            out[rel] = data[i:i + size]
            i += size + 1                       # the object, then its trailing newline
    return out


def _accent_table() -> dict[str, list[str]]:
    """{ascii letter: [the letter in both cases, then every Latin letter that folds to it, precomposed and
    decomposed]}: what `git grep` must accept so a folded query word still finds `Itô` or `GÖDEL`."""
    import unicodedata
    table: dict[str, set[str]] = {}
    for cp in list(range(0xC0, 0x250)) + list(range(0x1E00, 0x1F00)):
        ch = chr(cp)
        f = fold(ch)
        if len(f) == 1 and f.isascii() and f.isalpha():
            table.setdefault(f, set()).update({ch, unicodedata.normalize("NFD", ch)})
    return {k: [k, k.upper()] + sorted(v - {k, k.upper()}) for k, v in table.items()}


_ACCENTS = _accent_table()


def grep_pattern(word: str) -> str:
    """An extended regular expression matching `word` (already folded) in any case and with any Latin
    accents: `ito` -> `(i|I|í|...)(t|T|ţ|...)(o|O|ô|...)`. Letters without variants stay literal."""
    out = []
    for ch in word:
        alts = _ACCENTS.get(ch)
        if alts:
            out.append("(" + "|".join(alts) + ")")
        else:
            out.append(re.sub(r"([\\.^$|?*+()\[\]{}])", r"\\\1", ch))
    return "".join(out)


def git_bytes(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    """`git -c core.quotePath=false ARGS` in a package checkout with byte output (file names are not always
    UTF-8); returncode 127 when git cannot be run."""
    cmd = ["git", "-C", str(repo), "-c", "core.quotePath=false", *args]
    try:
        return subprocess.run(cmd, capture_output=True, check=False, env=core.scrubbed_env())
    except OSError as e:
        return subprocess.CompletedProcess(cmd, 127, b"", str(e).encode())


def _candidate_files(repo: pathlib.Path, words: list[str], nested: tuple[str, ...]) -> list[str] | str:
    """Tracked `*.lean` files at HEAD containing every word (case- and accent-insensitive), sorted; a one-line
    failure (git_failure) when git grep failed. Paths come NUL-separated and unquoted, so a non-ASCII file name
    is kept as is."""
    args = ["grep", "-l", "-z", "-I", "-E", "--all-match"]
    for w in words:
        args += ["-e", grep_pattern(w)]
    args += ["HEAD", "--", "*.lean", *[f":(exclude){d}/" for d in nested]]
    p = git_bytes(repo, *args)
    if p.returncode not in (0, 1):
        return git_failure(p)
    names = [x.decode("utf-8", errors="surrogateescape") for x in p.stdout.split(b"\0") if x]
    return sorted(n.split(":", 1)[1] for n in names if ":" in n)


# Why a package was not searched by declaration: (label for the text line, read-only remedy). The remedy names
# commands keyed by the package name, never a host path, so the output can be shared (field report R2).
NOT_SEARCHED = {
    "no-path": ("no path", "no local_path is recorded; see `lean-pkg status {name} --fields local_path,repo`"),
    "no-checkout": ("no checkout", "local_path is not a directory on this host; see "
                    "`lean-pkg status {name} --fields local_path,repo` and clone the repository there"),
    "no-git-head": ("no git HEAD", "the checkout has no commit or is not a git work tree; see "
                    "`lean-pkg status {name} --fields local_path` and `lean-pkg doctor`"),
    "scan-error": ("scan error", "git grep failed in the checkout (detail: its exit status and cause); see `lean-pkg doctor` and check the checkout with `git status` and `git fsck`"),
}


class NotSearched(str):
    """The code a declaration search returns instead of hits (a key of NOT_SEARCHED); `detail` says what
    failed (for scan-error: git grep's exit status and a fixed cause, never its stderr), else None."""
    detail: str | None = None

    @classmethod
    def of(cls, code: str, detail: str | None = None) -> "NotSearched":
        out = cls(code)
        out.detail = detail
        return out


# git's stderr never reaches the output (it can name host paths and users; #112): a failure is reported as its
# exit status plus one of these fixed causes, matched on the stderr text, else `cause not recognised`.
GIT_FAILURE_CAUSES = (
    (("dubious ownership", "safe.directory"), "repository owned by another user (git safe.directory)"),
    (("promisor", "could not fetch", "unable to read", "missing blob", "bad object", "invalid object",
      "loose object"), "object missing or unreadable (a partial or damaged clone; try `git fsck`)"),
    (("packfile", ".pack", ".idx"), "pack file unreadable (damaged, or replaced by a concurrent repack or gc)"),
    (("index.lock", ".lock': file exists"), "a git lock file is held by another process"),
    (("too many open files",), "too many open files"),
    (("cannot allocate memory", "out of memory"), "out of memory"),
    (("regular expression", "regex", "regexp"), "the search pattern was rejected"),
    (("not a git repository",), "not a git work tree"),
)


def git_failure(p: subprocess.CompletedProcess) -> str:
    """`git grep exit 128: object missing or unreadable (...)` or `git grep killed by signal 11`: the exit status
    and a fixed cause from GIT_FAILURE_CAUSES; git's own stderr is never copied (it can name host paths)."""
    rc = p.returncode
    if rc < 0 or rc in (134, 137, 139):
        return f"git grep killed by signal {-rc if rc < 0 else rc - 128} (a crash; rerun, and check the host)"
    err = (p.stderr.decode("utf-8", errors="replace") if isinstance(p.stderr, bytes)
           else str(p.stderr or "")).lower()
    cause = next((c for keys, c in GIT_FAILURE_CAUSES if any(k in err for k in keys)), "cause not recognised")
    return f"git grep exit {rc}: {cause}"


def declaration_hits(term: str, name: str, pkg: dict,
                     nested: tuple[str, ...] = ()) -> tuple[list[dict], int, dict | None] | NotSearched:
    """(hits, omitted, capped) from the package's checkout at HEAD, or a NotSearched code (no-path,
    no-checkout, no-git-head, scan-error) when it could not be searched. A declaration matches when every query
    word (case-insensitive, accents folded on both sides, as a substring so camelCase names match) occurs in its
    name or its docstring; a name match ranks before a docstring-only match. Names are fully qualified
    (`namespace` tracked): a dotted query (`DemoA.heatFlowMass_le`) is matched against the qualified name, a
    plain one against the name as written. `omitted` counts matching declarations beyond the
    DECL_HITS_PER_PACKAGE shown; `capped` is None when every candidate file was read, else
    {"candidates", "inspected", "unreadable"}: more candidate files matched the git grep prefilter than
    DECL_FILES_MAX allows reading, or `unreadable` of them could not be read. Those files were never examined,
    so a zero is not an absence (field report: a theorem in the 201st file was silently missed). `nested`
    lists the checkout-relative roots of other registered packages inside this one; their files belong to them
    and are not scanned here (field report: a contrib package's declarations were attributed to its parent)."""
    if not pkg.get("local_path"):
        return NotSearched("no-path")
    try:
        repo = core.repo_path(pkg)
    except Exception:
        return NotSearched("no-path")
    if not repo.is_dir():
        return NotSearched("no-checkout")
    head = core.git_head(repo)
    if head is None:
        return NotSearched("no-git-head")
    words = _decl_terms(term)
    dotted = "." in str(term)
    candidates = _candidate_files(repo, words, nested)
    if isinstance(candidates, str):
        return NotSearched.of("scan-error", candidates)
    files = candidates[:DECL_FILES_MAX]
    blobs = _read_blobs(repo, head, files)
    unreadable = sum(1 for rel in files if rel not in blobs)
    capped = ({"candidates": len(candidates), "inspected": len(files) - unreadable, "unreadable": unreadable}
              if len(candidates) > len(files) or unreadable else None)
    found = []
    for rel in files:
        blob = blobs.get(rel)
        if blob is None:
            continue
        for line, kw, qual, doc, st, written in lean_declarations(blob.decode("utf-8", errors="replace")):
            dtext = _doc_text(doc)
            in_name = fold(qual if dotted else written)
            if not all(w in in_name + " " + fold(dtext) for w in words):
                continue
            where = "name" if all(w in in_name for w in words) else "docstring"
            found.append({"source": "registry", "id": f"{name}:{rel}:{line}", "package": name,
                          "decl": qual, "kind": kw, "summary": _excerpt(dtext or st), "trust": "none",
                          "status": None, "pin": head, "file": f"{rel}:{line}", "score": 1,
                          "where": where, "hit_kind": DECL_HIT_KIND, "repo": None})
    found.sort(key=_decl_order)
    return found[:DECL_HITS_PER_PACKAGE], max(0, len(found) - DECL_HITS_PER_PACKAGE), capped


def package_roots(pkgs: dict) -> dict[str, pathlib.Path]:
    """Each package's resolved checkout directory (packages without a usable local_path are left out)."""
    roots = {}
    for name, pkg in pkgs.items():
        if isinstance(pkg, dict) and pkg.get("local_path"):
            try:
                roots[name] = core.repo_path(pkg).resolve()
            except (OSError, RuntimeError, TypeError, ValueError):
                continue
    return roots


def nested_roots(name: str, roots: dict[str, pathlib.Path]) -> tuple[str, ...]:
    """The roots of other registered packages strictly inside `name`'s, relative to it, sorted."""
    mine = roots.get(name)
    if mine is None:
        return ()
    out = set()
    for other, root in roots.items():
        if other != name and root != mine and mine in root.parents:
            out.add(root.relative_to(mine).as_posix())
    return tuple(sorted(out))


def not_searched_groups(cov: dict, names: bool) -> str:
    """`no checkout: a, b; no git HEAD: c` (or with names=False `no checkout`, `no checkout: 2, no git HEAD: 1`)
    in NOT_SEARCHED order; `no checkout` alone when nothing was skipped, as before."""
    by: dict[str, list[str]] = {}
    for r in cov.get("not_searched_reasons") or []:
        by.setdefault(r["code"], []).append(
            f"{r['package']} ({r['detail']})" if names and r.get("detail") else r["package"])
    if not by:
        return "no checkout"
    if not names and list(by) == ["no-checkout"]:
        return "no checkout"
    parts = []
    for code in NOT_SEARCHED:
        if code in by:
            label = NOT_SEARCHED[code][0]
            parts.append(f"{label}: {', '.join(by[code])}" if names else f"{label}: {len(by[code])}")
    return ("; " if names else ", ").join(parts)


def coverage_text(cov: dict | None) -> str | None:
    """The blind-source line: how many packages search could only see by declaration, or not at all."""
    if cov is None:
        return None
    total = cov["claims"] + cov["declaration_only"] + len(cov["not_searched"])
    blind = total - cov["claims"]
    if not blind:
        return f"registry coverage: all {total} package(s) have synced claims"
    ns = cov["not_searched"]
    text = (f"registry coverage: {blind} of {total} packages have no synced claims: "
            f"{cov['declaration_only']} searched by declaration only, {len(ns)} not searched ("
            + not_searched_groups(cov, names=True) + ")")
    if cov.get("declaration_hits_omitted"):
        text += f"; {cov['declaration_hits_omitted']} more declaration hit(s) not listed"
    for c in cov.get("declaration_files_capped") or []:
        text += f"; {c['package']}: {capped_phrase(c)}, so a zero there is not an absence"
    return text


def capped_phrase(c: dict) -> str:
    """`only 3 of 5 candidate file(s) inspected (file cap)`, `(1 unreadable)` or `(file cap; 1 unreadable)`."""
    unreadable = c.get("unreadable") or 0
    why = (["file cap"] if c["candidates"] - c["inspected"] - unreadable > 0 else []) + \
        ([f"{unreadable} unreadable"] if unreadable else [])
    return f"only {c['inspected']} of {c['candidates']} candidate file(s) inspected ({'; '.join(why)})"


def registry_incomplete(cov: dict | None) -> str | None:
    """Why the registry source read only part of what it should have, or None."""
    capped = (cov or {}).get("declaration_files_capped") or []
    if not capped:
        return None
    cap = any(c["candidates"] - c["inspected"] - (c.get("unreadable") or 0) > 0 for c in capped)
    unreadable = sum(c.get("unreadable") or 0 for c in capped)
    why = (["a file cap"] if cap else []) + ([f"{unreadable} unreadable candidate file(s)"] if unreadable else [])
    return f"declaration search hit {' and '.join(why)} (see the coverage line)"


# --------------------------------------------------------------------------
# local sources

def registry_revision(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def registry_block(term: str, reg_path: pathlib.Path) -> dict:
    return registry_search(term, reg_path)[0]


def registry_search(term: str, reg_path: pathlib.Path) -> tuple[dict, dict | None]:
    """(registry block, coverage): coverage is {claims, declaration_only, not_searched[],
    declaration_hits_omitted} over the registered packages, or None when the registry was skipped."""
    if not reg_path.is_file():
        return block("registry", "skipped", "no registry file"), None
    try:
        reg = core.load_registry(reg_path)
    except core.Fail as e:
        return block("registry", "skipped", f"unreadable: {e}"), None
    pkgs = reg.get("packages") or {}
    bad = [n for n, p in pkgs.items() if not isinstance(p, dict)]
    if not isinstance(pkgs, dict) or bad:
        return block("registry", "skipped",
                     f"unreadable registry (packages.{bad[0] if bad else '?'} is not a mapping)"), None
    hits, decl_hits = [], []
    cov = {"claims": 0, "declaration_only": 0, "not_searched": [], "not_searched_reasons": [],
           "declaration_hits_omitted": 0, "declaration_files_capped": []}
    roots = package_roots(pkgs)
    for name in sorted(pkgs):
        if has_synced_claims(pkgs[name]):
            cov["claims"] += 1
            continue
        got = declaration_hits(term, name, pkgs[name], nested_roots(name, roots))
        if isinstance(got, NotSearched):
            cov["not_searched"].append(name)
            cov["not_searched_reasons"].append({"package": name, "code": str(got),
                                                "remedy": NOT_SEARCHED[got][1].format(name=name),
                                                "detail": got.detail})
        else:
            cov["declaration_only"] += 1
            decl_hits += got[0]
            cov["declaration_hits_omitted"] += got[1]
            if got[2]:
                cov["declaration_files_capped"].append({"package": name, **got[2]})
    decl_hits.sort(key=_decl_order)
    for name in sorted(pkgs):
        pkg = reg["packages"][name]
        trust, pin = cl.package_trust(pkg), pkg.get("claims_synced_commit")
        s, where = score_package(term, name, pkg)
        if s:
            hits.append(_package_hit(name, pkg, s, where))
        for c in pkg.get("claims") or []:
            if isinstance(c, dict):
                s, where = score_claim(term, c)
                if s:
                    hits.append(_hit("registry", c, name, trust, pin, s, where))
    return block("registry", "ok", None, registry_revision(reg_path), rank(hits) + decl_hits,
                 incomplete=registry_incomplete(cov)), cov


def entry_claims(entry: dict) -> list[dict]:
    """The claims of an index entry: its `claims[]` (index-entry/2) when present, else one
    claim per headline item (index-entry/1). Entry `fields` act as keywords."""
    name, trust = entry.get("name"), entry.get("trust")
    heads = {h.get("name"): h for h in entry.get("headline") or [] if isinstance(h, dict)}
    if isinstance(entry.get("claims"), list):
        base = [c for c in entry["claims"] if isinstance(c, dict)]
    else:
        base = [{"id": f"{name}/{cl.slug_of_decl(h)}", "decl": h, "kind": "theorem",
                 "summary": heads[h].get("summary"), "judged_as": heads[h].get("judged_as"),
                 "trust": trust} for h in heads if isinstance(h, str)]
    pin = (entry.get("pin") or {}).get("commit") if isinstance(entry.get("pin"), dict) else None
    return [{**c, "status": "reusable", "keywords": list(entry.get("fields") or []),
             "file": (heads.get(c.get("decl")) or {}).get("file"), "pin": pin,
             "trust": c.get("trust") or trust, **({"conditional": True} if c.get("status") == "conditional" else {})}
            for c in base]


def index_revision(root: pathlib.Path) -> str:
    p = core.git(root, "rev-parse", "--short=12", "HEAD")
    return f"git:{p.stdout.strip()}" if p.returncode == 0 else "unversioned"


def index_block(term: str, root: pathlib.Path) -> dict:
    if not root.is_dir():
        return block("index", "skipped", "no index directory")
    hits, unreadable = [], 0
    for n in idx.entry_names(root):
        try:
            entry = idx.load_entry(root / n / idx.ENTRY_FILE)
        except core.Fail:
            unreadable += 1
            continue
        if not isinstance(entry, dict):
            unreadable += 1
            continue
        for c in entry_claims(entry):
            s, where = score_claim(term, c)
            if s:
                hits.append(_hit("index", c, n, c["trust"], c["pin"], s, where))
    reason = f"{unreadable} unreadable entr{'y' if unreadable == 1 else 'ies'} not searched" if unreadable else None
    # An entry that could not be read was not searched: the index answered only in part (complete: false).
    return block("index", "ok", reason, index_revision(root), rank(hits), incomplete=reason)


def adapter_block(source: str, term: str, timeout: float = 10) -> dict:
    """Run one network adapter; any failure inside it is `skipped`, never fatal, never zero. The block's
    `cache_writes` lists what the adapter wrote under the lean-pkg cache meanwhile (relative paths)."""
    adapters.CACHE_WRITES.clear()
    b = _adapter_block(source, term, timeout)
    b["cache_writes"] = list(adapters.CACHE_WRITES)
    adapters.CACHE_WRITES.clear()
    if source == "loogle" and b["state"] == "skipped" and str(b["reason"] or "").startswith("query not understood"):
        b["reason"] += LOOGLE_SYNTAX_HINT
    return b


def _adapter_block(source: str, term: str, timeout: float = 10) -> dict:
    try:
        r = ADAPTERS[source].search(term, timeout=timeout)
    except Exception as e:                       # one broken source must not end the search
        return block(source, "skipped", f"adapter error: {type(e).__name__}")
    if not isinstance(r, dict) or r.get("state") not in ("ok", "skipped") or \
            not isinstance(r.get("hits"), list):
        return block(source, "skipped", "adapter returned an unexpected result")
    if r["state"] == "skipped":
        return block(source, "skipped", str(r.get("reason") or "unavailable"), r.get("revision"))
    partial = r.get("partial") if isinstance(r.get("partial"), list) else []
    capped = r.get("capped") if isinstance(r.get("capped"), list) else []
    limit = r.get("cap_limit") if isinstance(r.get("cap_limit"), int) else None
    total = r.get("total") if isinstance(r.get("total"), int) and not isinstance(r.get("total"), bool) else None
    return block(source, "ok", r.get("reason"), r.get("revision"), r["hits"],
                 partial=[str(x) for x in partial], capped=[str(x) for x in capped], cap_limit=limit,
                 total=total)


# --------------------------------------------------------------------------
# the search

def parse_sources(text: str | None) -> tuple[str, ...]:
    if text is None:
        return SOURCES
    chosen = [s.strip() for s in text.split(",") if s.strip()]
    unknown = [s for s in chosen if s not in SOURCES]
    if not chosen or unknown:
        raise core.UsageError(f"--sources: unknown or empty {unknown or text!r}; "
                              f"choose from {','.join(SOURCES)}")
    return tuple(s for s in SOURCES if s in chosen)


def do_search(term: str, *, reg_path: pathlib.Path, index_dir: pathlib.Path,
              package: str | None = None, sources: tuple[str, ...] = SOURCES,
              offline: bool = False, force_loogle: bool = False,
              loogle_query: str | None = None) -> tuple[list[str], dict]:
    """`loogle_query`, when given, is what Loogle receives instead of the term (whatever the term's route);
    every other source still gets the term."""
    if not tokens(term):
        raise core.UsageError("TERM needs at least one letter or digit")
    if loogle_query is not None:
        if not loogle_query.strip():
            raise core.UsageError("--loogle-query needs a Lean name, term or pattern")
        if "loogle" not in sources:
            raise core.UsageError("--loogle-query was given but --sources does not select loogle")
    if package and reg_path.is_file() and package not in (core.load_registry(reg_path)["packages"]):
        raise core.UsageError(f"--package {package!r} is not in the registry")
    blocks, route, cov = [], route_of(term), None
    for src in SOURCES:
        if src not in sources:
            blocks.append(block(src, "skipped", "not selected"))
        elif src == "registry":
            b, cov = registry_search(term, reg_path)
            blocks.append(b)
        elif src == "index":
            blocks.append(index_block(term, index_dir))
        elif offline:
            blocks.append(block(src, "skipped", "offline"))
        elif src == "loogle" and loogle_query is not None:
            blocks.append(adapter_block(src, loogle_query))
        elif src in SIGNATURE_SOURCES and route == "natural-language" and not force_loogle:
            blocks.append(block(src, "skipped", LOOGLE_NL_REASON))
        else:
            blocks.append(adapter_block(src, term))
        blocks[-1]["route"] = route
    date = core.now_iso()[:10]
    payload = {"ok": True, "term": term, "date": date, "package": package, "offline": offline,
               "route": route, "sources": blocks, "total_hits": sum(len(b["hits"]) for b in blocks),
               "coverage": cov, "loogle_query": loogle_query,
               "cache": {"root": adapters.cache_root_source(),
                         "writes": [w for b in blocks for w in b.get("cache_writes") or []]}}
    # Completeness at a glance (#14, G9): a source skipped (offline, an outage, a route) means a zero count
    # is not a negative result; callers that copy "0 hits" into a record must see that here.
    skipped = [b["source"] for b in blocks if b["state"] != "ok"]
    # A source that answered with one of its parts failed (GitHub code search rate-limited while the
    # repository search worked) is incomplete too, or its zero reads as an absence (field report).
    # A part that returned exactly its result limit (GitHub's 30), or listed fewer matches than it found
    # (Palomar, Reservoir, Loogle), is truncated the same way; so is a local source that could not read
    # everything (the registry's file cap or unreadable files, an unreadable index entry).
    truncated = [b["source"] for b in blocks
                 if b["state"] == "ok" and (b.get("partial") or b.get("capped") or b.get("incomplete"))]
    payload.update({"complete": not skipped and not truncated, "searched": len(blocks) - len(skipped),
                    "skipped_sources": skipped, "truncated_sources": truncated})
    return render_text(payload), payload


def _n(k: int) -> str:
    return f"{k} hit" + ("" if k == 1 else "s")


def state_text(b: dict) -> str:
    return "ok" if b["state"] == "ok" else f"skipped: {b['reason']}"


def consulted_state(b: dict) -> str:
    """`ok, 3 hits`; `partial, 0 hits` when a part of the source failed; `capped, 30 hits` when a part
    returned its result limit; else `skipped: <reason>`."""
    if b["state"] != "ok":
        return state_text(b)
    return ("partial, " if b.get("partial") else "capped, " if b.get("capped")
            else "incomplete, " if b.get("incomplete") else "ok, ") + _n(len(b["hits"]))


def part_problems(b: dict) -> list[str]:
    """`code search failed`, `code search returned its 30-result limit`: why a source answered in part."""
    lim = f"{b['cap_limit']}-result" if b.get("cap_limit") else "result"
    return ([f"{p} search failed" for p in b.get("partial") or []]
            + [f"{b['total']} matches found, first {b['cap_limit']} listed"
               if p == "results" and b.get("total") and b.get("cap_limit")
               else f"{p} search returned its {lim} limit" for p in b.get("capped") or []])


def truncation_text(payload: dict) -> str:
    """Why each source in `truncated_sources` answered only in part, e.g. `registry: declaration search hit a
    file cap (see the coverage line); github: code search failed`, `index: 1 unreadable entry not searched`
    or `palomar: 50 matches found, first 30 listed`."""
    parts = {b["source"]: part_problems(b) for b in payload["sources"]}
    out = []
    for src in payload.get("truncated_sources") or []:
        b = next(x for x in payload["sources"] if x["source"] == src)
        why = parts.get(src) or []
        if b.get("incomplete"):
            why = why + [b["incomplete"]]
        out.append(f"{src}: {', '.join(why)}" if why else f"{src} answered only in part")
    return "; ".join(out)


def render_text(payload: dict) -> list[str]:
    lines = [f'lean-pkg search "{payload["term"]}" on {payload["date"]}'
             + (f" for package {payload['package']}" if payload["package"] else "")
             + (f' (Loogle query "{payload["loogle_query"]}")' if payload.get("loogle_query") else "")]
    for b in payload["sources"]:
        if b["state"] != "ok":
            lines.append(f"{b['source']}: skipped: {b['reason']}")
            continue
        rev = f" at {b['revision']}" if b["revision"] else ""
        note = f" ({b['reason']})" if b["reason"] else ""
        lines.append(f"{b['source']}: ok{rev}, {_n(len(b['hits']))}{note}")
        for h in b["hits"]:
            if h.get("hit_kind") == DECL_HIT_KIND:
                lines.append(f"  [{h['score']} {h['where']}] {h['package']}: {h['decl']}  "
                             f"({h['kind']}, {DECL_HIT_KIND}, trust {h['trust']}, {h['file']})")
                lines.append(f"      {h['summary']}")
            elif h.get("hit_kind") == "package":
                repo = f" — {h['repo']}" if h["repo"] else ""
                lines.append(f"  [{h['score']} {h['where']}] package: {h['package']} ({h['status']}){repo}")
                if h["summary"]:
                    lines.append(f"      {h['summary']}")
            elif "id" in h:
                pin = f", pin {str(h['pin'])[:12]}" if h["pin"] else ""
                cond = " (conditional)" if h.get("conditional") else ""
                lines.append(f"  [{h['score']} {h['where']}] {h['id']}  {h['decl']}  "
                             f"({h['kind']}, {h['status']}{cond}, trust {h['trust']}{pin})")
                lines.append(f"      {h['summary']}")
            else:
                lines.append(f"  {h['name']}  {h.get('url') or ''}".rstrip())
                if h.get("summary"):
                    lines.append(f"      {h['summary']}")
    cov_line = coverage_text(payload.get("coverage"))
    if cov_line:
        lines.append(cov_line)
    cache = payload.get("cache") or {}
    if cache.get("writes"):
        root = {"--cache-dir": "the --cache-dir directory", "XDG_CACHE_HOME": "$XDG_CACHE_HOME/lean-pkg",
                "default": "~/.cache/lean-pkg"}.get(cache.get("root"), "the lean-pkg cache")
        lines.append(f"cache: wrote {', '.join(cache['writes'])} under {root} (a cache refresh; the registry was "
                     "not written; --cache-dir DIR keeps a search's cache private)")
    why = truncation_text(payload)
    if why and not payload.get("skipped_sources"):
        lines.append(f"incomplete: {why}; a zero here is not a negative result")
    if payload.get("skipped_sources"):
        n, k = len(payload["sources"]), len(payload["skipped_sources"])
        cap = f"; {why}" if why else ""
        lines.append(f"incomplete: searched {n - k} of {n} sources; skipped: {', '.join(payload['skipped_sources'])}"
                     f"{cap}; a zero here is not a negative result")
    lines.append("consulted: " + ", ".join(
        f"{b['source']} ({consulted_state(b)})"
        for b in payload["sources"]))
    return lines


# --------------------------------------------------------------------------
# CLI glue: lean_pkg.cli.main calls add_parsers(sub, add_json)

def cmd_search(args) -> int:
    if args.repo_dir and not args.record:
        raise core.UsageError("--repo is only used with --record")
    if args.agent and not args.record:
        raise core.UsageError("--agent is only used with --record")
    reg_path = core.registry_path(args)
    if args.cache_dir is not None and not str(args.cache_dir).strip():
        raise core.UsageError("--cache-dir needs a directory")
    saved_root = adapters.CACHE_ROOT
    try:
        if args.cache_dir is not None:
            adapters.CACHE_ROOT = pathlib.Path(args.cache_dir).expanduser().resolve()
        out, payload = do_search(args.term, reg_path=reg_path,
                                 index_dir=idx.index_root(args), package=args.package,
                                 sources=parse_sources(args.sources), offline=args.offline,
                                 force_loogle=args.loogle, loogle_query=args.loogle_query)
    finally:
        adapters.CACHE_ROOT = saved_root
    if args.record:
        from . import prior_art
        events_p = core.events_path(args) if (args.events or reg_path.is_file()) else None
        # the network search above runs unlocked; the record file and its event under the registry lock
        with core.registry_lock(reg_path) if reg_path.is_file() else contextlib.nullcontext():
            if reg_path.is_file() and args.package:    # #21.3: the lease-holder rule, as for record
                core.warn_lease(core.lease_actor(core.load_registry(reg_path), args.package, args.agent,
                                                 "search --record"))
            lines, payload["record"] = prior_art.write_record(payload, reg_path=reg_path,
                                                              events_p=events_p, repo_dir=args.repo_dir,
                                                              actor=args.agent)
        payload["nav_hint"] = (prior_art.nav_hint(payload["record"]["path"])
                               if payload["record"].get("mkdocs") else None)
        out += lines
    failed = args.require_complete and not payload["complete"]
    if failed:
        # Opt-in (field report): an incomplete search exits 1 so a script cannot read its zero as an absence.
        why = truncation_text(payload)
        msg = ("search incomplete (--require-complete): "
               + (f"skipped: {', '.join(payload['skipped_sources'])}" if payload["skipped_sources"] else "")
               + ("; " if payload["skipped_sources"] and why else "") + why)
        payload.update({"ok": False, "error": msg})
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    if failed:
        print(f"lean-pkg: {msg}", file=sys.stderr)
        return 1
    return 0


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("search", help="prior-art search over claims, the index and public sources",
                       description="Prior-art search. Packages without synced claims are searched by declaration "
                                   "(fully qualified names; accents folded). A source that skipped, failed in part, "
                                   "listed fewer matches than it found or could not read everything makes the "
                                   "search incomplete (complete: false): a zero there is not an absence.")
    s.add_argument("term", help="what to look for (quote a phrase)")
    s.add_argument("--package", default=None,
                   help="the registered package this search is for (it labels the output; every "
                        "package's claims are still searched)")
    s.add_argument("--offline", action="store_true", help="skip every network source")
    s.add_argument("--loogle", action="store_true",
                   help="send the term to Loogle even when it reads as natural language (by default "
                        "only declaration/type patterns go to Loogle)")
    s.add_argument("--loogle-query", default=None, metavar="PATTERN",
                   help="send PATTERN to Loogle instead of the term (a Lean name, term or pattern such as "
                        "'Real.exp' or '?a * ?b = ?b * ?a'); the other sources still get the term")
    s.add_argument("--require-complete", action="store_true",
                   help="exit 1 when the search is incomplete (a source skipped, failed in part, capped, or "
                        "read only part of what it should); the output is printed in full first")
    s.add_argument("--cache-dir", default=None, metavar="DIR",
                   help="use DIR as the lean-pkg cache for this search (Palomar snapshots, Reservoir index) "
                        "instead of $XDG_CACHE_HOME/lean-pkg or ~/.cache/lean-pkg")
    s.add_argument("--sources", default=None, metavar="a,b,c",
                   help="subset of: " + ",".join(SOURCES) + " (order is fixed)")
    s.add_argument("--index", default=None,
                   help=f"index root (default: ${idx.INDEX_ENV}, else ./{idx.DEFAULT_INDEX_DIR})")
    s.add_argument("--record", action="store_true",
                   help="write docs/prior-art/<date>-<slug>.md in the package checkout (or --repo) "
                        "and a prior-art-searched event")
    s.add_argument("--repo", dest="repo_dir", metavar="DIR", default=None, help="research repository for --record")
    s.add_argument("--agent", default=None,
                   help="with --record: the agent acting (recorded as the event's actor); another agent's live "
                        "lease on --package then refuses")
    add_json(s)
    s.set_defaults(fn=cmd_search)
