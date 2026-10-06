# lean-pkg search -- claim-level prior-art search before building
"""`lean-pkg search TERM`: one block per source, in a fixed order, each with its
revision and state (`ok` or `skipped: <reason>`), and a closing line naming every
source consulted. Local sources (the registry's cached claims, the public index)
always run and are scored deterministically: exact alias 3, keyword 2, every
term token present 1; no embeddings. A skipped source is never a zero.
"""

from __future__ import annotations

import hashlib
import pathlib
import re

from . import claims as cl
from . import cli as core
from . import index as idx

LOCAL_SOURCES = ("registry", "index")
SOURCES = LOCAL_SOURCES
LATE_STATUSES = ("experimental", "superseded")
HIT_KEYS = ("source", "id", "package", "decl", "kind", "summary", "trust", "status", "pin",
            "file", "score", "where")
_TOKEN_SPLIT = re.compile(r"[^0-9a-z]+")


def tokens(text) -> list[str]:
    """Lowercase alphanumeric tokens: `white-noise Fubini` -> ['white', 'noise', 'fubini']."""
    return [t for t in _TOKEN_SPLIT.split(str(text or "").lower()) if t]


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


def rank(hits: list[dict]) -> list[dict]:
    """Reusable before experimental and superseded; then by score, then by id."""
    return sorted(hits, key=lambda h: (h["status"] in LATE_STATUSES, -h["score"], str(h["id"])))


def _hit(source: str, c: dict, package: str, trust, pin, score: int, where: str) -> dict:
    return {"source": source, "id": c.get("id"), "package": package, "decl": c.get("decl"),
            "kind": c.get("kind"), "summary": c.get("summary"), "trust": trust or "none",
            "status": c.get("status") or "reusable", "pin": pin, "file": c.get("file"),
            "score": score, "where": where}


def block(source: str, state: str, reason: str | None = None, revision: str | None = None,
          hits: list | None = None) -> dict:
    return {"source": source, "state": state, "reason": reason, "revision": revision,
            "hits": hits or []}


# --------------------------------------------------------------------------
# local sources

def registry_revision(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def registry_block(term: str, reg_path: pathlib.Path) -> dict:
    if not reg_path.is_file():
        return block("registry", "skipped", "no registry file")
    reg = core.load_registry(reg_path)
    hits = []
    for name in sorted(reg.get("packages") or {}):
        pkg = reg["packages"][name]
        trust, pin = cl.package_trust(pkg), pkg.get("claims_synced_commit")
        for c in pkg.get("claims") or []:
            if isinstance(c, dict):
                s, where = score_claim(term, c)
                if s:
                    hits.append(_hit("registry", c, name, trust, pin, s, where))
    return block("registry", "ok", None, registry_revision(reg_path), rank(hits))


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
              offline: bool = False) -> tuple[list[str], dict]:
    if not tokens(term):
        raise core.UsageError("TERM needs at least one letter or digit")
    if package and reg_path.is_file() and package not in (core.load_registry(reg_path)["packages"]):
        raise core.UsageError(f"--package {package!r} is not in the registry")
    blocks = []
    for src in SOURCES:
        if src not in sources:
            blocks.append(block(src, "skipped", "not selected"))
        elif src == "registry":
            blocks.append(registry_block(term, reg_path))
        elif src == "index":
            blocks.append(index_block(term, index_dir))
    date = core.now_iso()[:10]
    payload = {"ok": True, "term": term, "date": date, "package": package, "offline": offline,
               "sources": blocks, "total_hits": sum(len(b["hits"]) for b in blocks)}
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
            if "id" in h:
                pin = f", pin {str(h['pin'])[:12]}" if h["pin"] else ""
                lines.append(f"  [{h['score']} {h['where']}] {h['id']}  {h['decl']}  "
                             f"({h['kind']}, {h['status']}, trust {h['trust']}{pin})")
                lines.append(f"      {h['summary']}")
            else:
                lines.append(f"  {h['name']}  {h.get('url') or ''}".rstrip())
                if h.get("summary"):
                    lines.append(f"      {h['summary']}")
    lines.append("consulted: " + ", ".join(
        f"{b['source']} ({'ok, ' + _n(len(b['hits'])) if b['state'] == 'ok' else state_text(b)})"
        for b in payload["sources"]))
    return lines


# --------------------------------------------------------------------------
# CLI glue: lean_pkg.cli.main calls add_parsers(sub, add_json)

def cmd_search(args) -> int:
    out, payload = do_search(args.term, reg_path=core.registry_path(args),
                             index_dir=idx.index_root(args), package=args.package,
                             sources=parse_sources(args.sources), offline=args.offline)
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
    s.add_argument("--sources", default=None, metavar="a,b,c",
                   help="subset of: " + ",".join(SOURCES) + " (order is fixed)")
    s.add_argument("--index", default=None,
                   help=f"index root (default: ${idx.INDEX_ENV}, else ./{idx.DEFAULT_INDEX_DIR})")
    add_json(s)
    s.set_defaults(fn=cmd_search)
