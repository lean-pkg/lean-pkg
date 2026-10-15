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

from . import claims as cl
from . import cli as core
from . import index as idx
from .adapters import github, leansearch, loogle, palomar, reservoir

LOCAL_SOURCES = ("registry", "index")
NETWORK_SOURCES = ("loogle", "leansearch", "reservoir", "palomar", "github")
SOURCES = LOCAL_SOURCES + NETWORK_SOURCES
ADAPTERS = {"loogle": loogle, "leansearch": leansearch, "reservoir": reservoir,
            "palomar": palomar, "github": github}
LATE_STATUSES = ("experimental", "superseded")
HIT_KEYS = ("source", "id", "package", "decl", "kind", "summary", "trust", "status", "pin",
            "file", "score", "where", "hit_kind", "repo")
PACKAGE_FIELDS = ("name", "notes", "owner_project", "repo")
_TOKEN_SPLIT = re.compile(r"[\W_]+", re.UNICODE)
_PATHLIKE = re.compile(r"(?:~|/home/|/Users/|[A-Za-z]:\\)\S*")
ROUTES = ("signature", "natural-language")
SIGNATURE_SOURCES = ("loogle",)       # sources that only take declaration/type patterns
LOOGLE_NL_REASON = ("natural-language query; Loogle takes declaration patterns — try e.g. "
                    "`?a + ?b = ?b + ?a` (--loogle forces it)")
_SIG_MARKERS = ("→", "->", "∀", "∃", "fun ", "λ", ":", "(", "?", "_", "⊢", '"', "↔", "<->")
_DOTTED = re.compile(r"\b[A-Z][A-Za-z0-9']*\.[A-Za-z_]")
DECL_HIT_KIND = "declaration (unclaimed)"
DECL_KEYWORDS = ("theorem", "lemma", "def", "structure", "class", "instance", "abbrev", "inductive")
_DECL_LINE = re.compile(
    r"^\s*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|partial|unsafe|nonrec|scoped)\s+)*"
    r"(" + "|".join(DECL_KEYWORDS) + r")\s+([^\s:({\[⦃]+)")
DECL_HITS_PER_PACKAGE = 10     # declaration hits kept per package; the rest are counted, not listed
DECL_FILES_MAX = 2000          # files read per package after the git grep prefilter; past it, coverage says so
EXCERPT_MAX = 160


def fold(text) -> str:
    """NFKD, strip combining marks, casefold: Itô -> ito, Black–Scholes keeps its words."""
    import unicodedata
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()


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
    return {"source": source, "id": c.get("id"), "package": package, "decl": c.get("decl"),
            "kind": c.get("kind"), "summary": c.get("summary"), "trust": trust or "none",
            "status": c.get("status") or "reusable", "pin": pin, "file": c.get("file"),
            "score": score, "where": where, "hit_kind": "claim", "repo": None}


def _package_hit(name: str, pkg: dict, score: int, where: str) -> dict:
    return {"source": "registry", "id": name, "package": name, "decl": None, "kind": None,
            "summary": pkg.get("notes") or None, "trust": cl.package_trust(pkg),
            "status": pkg.get("status"), "pin": None, "file": None, "score": score, "where": where,
            "hit_kind": "package", "repo": scrub_paths(pkg.get("repo")) if pkg.get("repo") else None}


def block(source: str, state: str, reason: str | None = None, revision: str | None = None,
          hits: list | None = None, route: str | None = None) -> dict:
    return {"source": source, "state": state, "reason": scrub_paths(reason) if reason else reason,
            "revision": revision, "hits": hits or [], "route": route}


# --------------------------------------------------------------------------
# declaration fallback (#49): packages without synced claims

def has_synced_claims(pkg: dict) -> bool:
    """True when the registry caches at least one synced claim for the package (`claims coverage`
    `synced`). `empty`, `unsynced` and `none` all leave search blind to the package's claims."""
    return bool(pkg.get("claims"))


def lean_declarations(text: str) -> list[tuple[int, str, str, str | None, str]]:
    """(line, keyword, name, docstring or None, stripped line) for every declaration line
    (`theorem|lemma|def|structure|class|instance|abbrev|inductive NAME`, after attributes and
    modifiers). A `/-- ... -/` docstring belongs to the declaration that follows it, with only blank
    or attribute lines between."""
    out: list[tuple[int, str, str, str | None, str]] = []
    doc, buf, in_doc = None, [], False
    for i, line in enumerate(text.splitlines(), 1):
        st = line.strip()
        if in_doc:
            buf.append(st)
            if "-/" in st:
                in_doc, doc = False, " ".join(buf)
            continue
        if st.startswith("/--"):
            buf = [st]
            if "-/" in st[3:]:
                doc = st
            else:
                in_doc = True
            continue
        m = _DECL_LINE.match(line)
        if m and m.group(2) not in DECL_KEYWORDS:
            out.append((i, m.group(1), m.group(2), doc, st))
            doc = None
        elif st and not st.startswith("@["):
            doc = None
    return out


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
    thousands of files instead of one `git show` each). A path git reports missing is left out."""
    if not rels:
        return {}
    spec = "".join(f"{rev}:./{r}\n" for r in rels).encode("utf-8")
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


def declaration_hits(term: str, name: str, pkg: dict) -> tuple[list[dict], int, dict | None] | None:
    """(hits, omitted, capped) from the package's checkout at HEAD, or None when there is no git checkout
    at local_path (not searched). A declaration matches when every query word (case-insensitive,
    accents folded, as a substring so camelCase names match) occurs in its name or its docstring;
    a name match ranks before a docstring-only match. `omitted` counts matching declarations beyond the
    DECL_HITS_PER_PACKAGE shown; `capped` is {"candidates", "inspected"} when more candidate files matched the
    git grep prefilter than DECL_FILES_MAX allows reading (else None): those files were never examined, so a
    zero is not an absence (field report: a theorem in the 201st file was silently missed)."""
    if not pkg.get("local_path"):
        return None
    try:
        repo = core.repo_path(pkg)
    except Exception:
        return None
    if not repo.is_dir():
        return None
    head = core.git_head(repo)
    if head is None:
        return None
    words = _decl_terms(term)
    args = ["grep", "-l", "-I", "-i", "-F", "--all-match"]
    for w in words:
        args += ["-e", w]
    p = core.git(repo, *args, "HEAD", "--", "*.lean")
    if p.returncode not in (0, 1):
        return None
    candidates = sorted(ln.split(":", 1)[1] for ln in p.stdout.splitlines() if ":" in ln)
    files = candidates[:DECL_FILES_MAX]
    capped = ({"candidates": len(candidates), "inspected": len(files)}
              if len(candidates) > len(files) else None)
    blobs = _read_blobs(repo, head, files)
    found = []
    for rel in files:
        blob = blobs.get(rel)
        if blob is None:
            continue
        for line, kw, dname, doc, st in lean_declarations(blob.decode("utf-8", errors="replace")):
            dtext = _doc_text(doc)
            in_name = fold(dname)
            if not all(w in in_name + " " + fold(dtext) for w in words):
                continue
            where = "name" if all(w in in_name for w in words) else "docstring"
            found.append({"source": "registry", "id": f"{name}:{rel}:{line}", "package": name,
                          "decl": dname, "kind": kw, "summary": _excerpt(dtext or st), "trust": "none",
                          "status": None, "pin": head, "file": f"{rel}:{line}", "score": 1,
                          "where": where, "hit_kind": DECL_HIT_KIND, "repo": None})
    found.sort(key=_decl_order)
    return found[:DECL_HITS_PER_PACKAGE], max(0, len(found) - DECL_HITS_PER_PACKAGE), capped


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
            f"{cov['declaration_only']} searched by declaration only, {len(ns)} not searched (no checkout"
            + (f": {', '.join(ns)}" if ns else "") + ")")
    if cov.get("declaration_hits_omitted"):
        text += f"; {cov['declaration_hits_omitted']} more declaration hit(s) not listed"
    for c in cov.get("declaration_files_capped") or []:
        text += (f"; {c['package']}: only {c['inspected']} of {c['candidates']} candidate file(s) inspected "
                 f"(file cap), so a zero there is not an absence")
    return text


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
    cov = {"claims": 0, "declaration_only": 0, "not_searched": [], "declaration_hits_omitted": 0,
           "declaration_files_capped": []}
    for name in sorted(pkgs):
        if has_synced_claims(pkgs[name]):
            cov["claims"] += 1
            continue
        got = declaration_hits(term, name, pkgs[name])
        if got is None:
            cov["not_searched"].append(name)
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
    return block("registry", "ok", None, registry_revision(reg_path), rank(hits) + decl_hits), cov


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
             "trust": c.get("trust") or trust} for c in base]


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
        for c in entry_claims(entry):
            s, where = score_claim(term, c)
            if s:
                hits.append(_hit("index", c, n, c["trust"], c["pin"], s, where))
    reason = f"{unreadable} unreadable entr{'y' if unreadable == 1 else 'ies'} not searched" if unreadable else None
    return block("index", "ok", reason, index_revision(root), rank(hits))


def adapter_block(source: str, term: str, timeout: float = 10) -> dict:
    """Run one network adapter; any failure inside it is `skipped`, never fatal, never zero."""
    try:
        r = ADAPTERS[source].search(term, timeout=timeout)
    except Exception as e:                       # one broken source must not end the search
        return block(source, "skipped", f"adapter error: {type(e).__name__}")
    if not isinstance(r, dict) or r.get("state") not in ("ok", "skipped") or \
            not isinstance(r.get("hits"), list):
        return block(source, "skipped", "adapter returned an unexpected result")
    if r["state"] == "skipped":
        return block(source, "skipped", str(r.get("reason") or "unavailable"), r.get("revision"))
    return block(source, "ok", r.get("reason"), r.get("revision"), r["hits"])


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
              offline: bool = False, force_loogle: bool = False) -> tuple[list[str], dict]:
    if not tokens(term):
        raise core.UsageError("TERM needs at least one letter or digit")
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
        elif src in SIGNATURE_SOURCES and route == "natural-language" and not force_loogle:
            blocks.append(block(src, "skipped", LOOGLE_NL_REASON))
        else:
            blocks.append(adapter_block(src, term))
        blocks[-1]["route"] = route
    date = core.now_iso()[:10]
    payload = {"ok": True, "term": term, "date": date, "package": package, "offline": offline,
               "route": route, "sources": blocks, "total_hits": sum(len(b["hits"]) for b in blocks),
               "coverage": cov}
    # Completeness at a glance (#14, G9): a source skipped (offline, an outage, a route) means a zero count
    # is not a negative result; callers that copy "0 hits" into a record must see that here.
    skipped = [b["source"] for b in blocks if b["state"] != "ok"]
    truncated = ["registry"] if (cov or {}).get("declaration_files_capped") else []
    payload.update({"complete": not skipped and not truncated, "searched": len(blocks) - len(skipped),
                    "skipped_sources": skipped, "truncated_sources": truncated})
    return render_text(payload), payload


def _n(k: int) -> str:
    return f"{k} hit" + ("" if k == 1 else "s")


def state_text(b: dict) -> str:
    return "ok" if b["state"] == "ok" else f"skipped: {b['reason']}"


def render_text(payload: dict) -> list[str]:
    lines = [f'lean-pkg search "{payload["term"]}" on {payload["date"]}'
             + (f" for package {payload['package']}" if payload["package"] else "")]
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
                lines.append(f"  [{h['score']} {h['where']}] {h['id']}  {h['decl']}  "
                             f"({h['kind']}, {h['status']}, trust {h['trust']}{pin})")
                lines.append(f"      {h['summary']}")
            else:
                lines.append(f"  {h['name']}  {h.get('url') or ''}".rstrip())
                if h.get("summary"):
                    lines.append(f"      {h['summary']}")
    cov_line = coverage_text(payload.get("coverage"))
    if cov_line:
        lines.append(cov_line)
    if payload.get("truncated_sources") and not payload.get("skipped_sources"):
        lines.append(f"incomplete: {', '.join(payload['truncated_sources'])} hit a file cap (see the coverage line); "
                     f"a zero here is not a negative result")
    if payload.get("skipped_sources"):
        n, k = len(payload["sources"]), len(payload["skipped_sources"])
        cap = (f"; {', '.join(payload['truncated_sources'])} hit a file cap"
               if payload.get("truncated_sources") else "")
        lines.append(f"incomplete: searched {n - k} of {n} sources; skipped: {', '.join(payload['skipped_sources'])}"
                     f"{cap}; a zero here is not a negative result")
    lines.append("consulted: " + ", ".join(
        f"{b['source']} ({'ok, ' + _n(len(b['hits'])) if b['state'] == 'ok' else state_text(b)})"
        for b in payload["sources"]))
    return lines


# --------------------------------------------------------------------------
# CLI glue: lean_pkg.cli.main calls add_parsers(sub, add_json)

def cmd_search(args) -> int:
    if args.repo_dir and not args.record:
        raise core.UsageError("--repo-dir is only used with --record")
    if args.agent and not args.record:
        raise core.UsageError("--agent is only used with --record")
    reg_path = core.registry_path(args)
    out, payload = do_search(args.term, reg_path=reg_path,
                             index_dir=idx.index_root(args), package=args.package,
                             sources=parse_sources(args.sources), offline=args.offline,
                             force_loogle=args.loogle)
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
        payload["nav_hint"] = prior_art.nav_hint(payload["record"]["path"])
        out += lines
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    return 0


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("search", help="prior-art search over claims, the index and public sources")
    s.add_argument("term", help="what to look for (quote a phrase)")
    s.add_argument("--package", default=None,
                   help="the registered package this search is for (it labels the output; every "
                        "package's claims are still searched)")
    s.add_argument("--offline", action="store_true", help="skip every network source")
    s.add_argument("--loogle", action="store_true",
                   help="send the term to Loogle even when it reads as natural language (by default "
                        "only declaration/type patterns go to Loogle)")
    s.add_argument("--sources", default=None, metavar="a,b,c",
                   help="subset of: " + ",".join(SOURCES) + " (order is fixed)")
    s.add_argument("--index", default=None,
                   help=f"index root (default: ${idx.INDEX_ENV}, else ./{idx.DEFAULT_INDEX_DIR})")
    s.add_argument("--record", action="store_true",
                   help="write docs/prior-art/<date>-<slug>.md in the package checkout (or --repo-dir) "
                        "and a prior-art-searched event")
    s.add_argument("--repo-dir", default=None, help="research repository for --record")
    s.add_argument("--agent", default=None,
                   help="with --record: the agent acting (recorded as the event's actor); another agent's live "
                        "lease on --package then refuses")
    add_json(s)
    s.set_defaults(fn=cmd_search)
