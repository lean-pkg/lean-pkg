# lean-pkg prior art -- the citable search record and its event line
"""`lean-pkg search TERM --record` writes two things, with different roles:

- `docs/prior-art/<YYYY-MM-DD>-<slug>.md` in the research repository (the
  package's checkout, or --repo-dir): terms, date, every source with its
  revision, state and hit count (zeros included), the top candidates, and a
  `decision: pending` line a human edits to require | reuse | vendor |
  contribute | build with a reason;
- one `prior-art-searched` line in the registry's event log.

It never writes into the registry directory and never overwrites a record.
Claims of private packages other than the target appear only when the target
package is itself private, and the record then says `private-claims: yes`. A
record written into a folder that is not the package's own checkout (--repo-dir)
lists the package's own claims only when the package is public.
"""

from __future__ import annotations

import pathlib

from . import __version__
from . import cli as core

RECORD_DIR = "docs/prior-art"
TOP_PER_SOURCE = 5
DECISIONS = ("require", "reuse", "vendor", "contribute", "build")


SLUG_MAX = 60
NAV_REASON = "a strict MkDocs build needs a nav entry (or a literate-nav glob) for it"


MKDOCS_CONFIGS = ("mkdocs.yml", "mkdocs.yaml")


def has_mkdocs(target: pathlib.Path) -> bool:
    return any((target / f).is_file() for f in MKDOCS_CONFIGS)


def nav_hint(rel: str, target: pathlib.Path | None = None) -> dict | None:
    """#50: the new page is an error under `mkdocs build --strict` until nav lists it. A hint only:
    lean-pkg never edits the consumer's mkdocs.yml. None when the repository has no MkDocs config: a plain
    Lean repository has no nav to update (field report)."""
    if target is not None and not has_mkdocs(target):
        return None
    return {"path": rel, "reason": NAV_REASON}


def record_slug(term: str) -> str:
    """Kebab slug of the search term; a term longer than SLUG_MAX is cut there and gets an
    8-hex suffix of the full slug, so two long terms that agree on their first 60 characters
    still get distinct record files."""
    import hashlib
    from .search import tokens
    full = "-".join(tokens(term)).strip("-")
    if not full:
        return "search"
    if len(full) <= SLUG_MAX:
        return full
    return full[:SLUG_MAX].strip("-") + "-" + hashlib.sha256(full.encode()).hexdigest()[:8]


def record_rel(date: str, term: str) -> str:
    return f"{RECORD_DIR}/{date}-{record_slug(term)}.md"


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _q(term: str) -> str:
    return '"' + term.replace('"', "'") + '"'


def not_found_line(payload: dict) -> str | None:
    """The spec's zero-hit wording, over exactly the sources that ran and found nothing."""
    zero = [f"{b['source']} at {b['revision'] or 'unknown revision'}"
            + (f" ({', '.join(b['partial'])} search failed, not searched)" if b.get("partial") else "")
            for b in payload["sources"] if b["state"] == "ok" and not b["hits"] and not b.get("withheld")
            and not b.get("incomplete")]
    if not zero:
        return None
    return f"Not found in {', '.join(zero)} on {payload['date']} for {_q(payload['term'])}."


def not_fully_searched_line(payload: dict) -> str | None:
    """A local source that answered without reading everything (the registry's file cap or unreadable files,
    an unreadable index entry) is never `not found`: the record names what it did not read."""
    part = [f"{b['source']} ({b['incomplete']})" for b in payload["sources"]
            if b["state"] == "ok" and b.get("incomplete")]
    if not part:
        return None
    return f"Not fully searched, so a zero there is not an absence: {'; '.join(part)}."


def coverage_sentence(payload: dict) -> str | None:
    """Counts only (no package names: the record may land in a public repository)."""
    cov = payload.get("coverage")
    if not cov:
        return None
    from . import search as srch   # local: search imports this module
    blind = cov["declaration_only"] + len(cov["not_searched"])
    if not blind:
        return None
    text = (f"{blind} of {blind + cov['claims']} registered packages have no synced claims: "
            f"{cov['declaration_only']} searched by declaration only, {len(cov['not_searched'])} not searched "
            f"({srch.not_searched_groups(cov, names=False)})")
    capped = cov.get("declaration_files_capped") or []
    if capped:
        inspected = sum(c["inspected"] for c in capped)
        candidates = sum(c["candidates"] for c in capped)
        unreadable = sum(c.get("unreadable") or 0 for c in capped)
        why = (["file cap"] if candidates - inspected - unreadable > 0 else []) + \
            ([f"{unreadable} unreadable"] if unreadable else [])
        text += (f"; in {len(capped)} of them only {inspected} of {candidates} candidate file(s) were inspected "
                 f"({'; '.join(why)}), so a zero there is not an absence")
    return text + "."


def _candidate(h: dict) -> str:
    if h.get("hit_kind") == "declaration (unclaimed)":
        return (f"- `{h['decl']}` in package `{h['package']}` ({h['kind']}, declaration (unclaimed), trust "
                f"{h['trust']}, `{_cell(h['file'])}`). {_cell(h.get('summary') or '')}")
    if h.get("hit_kind") == "package":
        repo = f": {_cell(h['repo'])}" if h.get("repo") else ""
        return f"- package `{h['package']}` ({h['status']}){repo}"
    if "id" in h:
        pin = f", pin `{str(h['pin'])[:12]}`" if h.get("pin") else ""
        return (f"- `{h['id']}` (`{h['decl']}`): {h['kind']}, {h['status']}, trust {h['trust']}{pin}. "
                f"{_cell(h.get('summary') or '')}")
    name = f"[{_cell(h['name'])}]({h['url']})" if h.get("url") else f"`{h['name']}`"
    return f"- {name}: {_cell(h.get('summary') or '')}".rstrip()


def render_record(payload: dict, *, withheld: int, private_claims: bool) -> str:
    term, date = payload["term"], payload["date"]
    lines = [f"# Prior-art search: {_q(term)}", "",
             f"- date: {date}",
             f"- term: {_q(term)}",
             f"- package: {payload.get('package') or 'none'}",
             f"- tool: lean-pkg {__version__} (lean-pkg search --record)",
             f"- private-claims: {'yes' if private_claims else 'no'}", "",
             "## Sources", "",
             "| Source | Revision | State | Hits |", "|---|---|---|---|"]
    for b in payload["sources"]:
        state = "ok" if b["state"] == "ok" else f"skipped: {b['reason']}"
        if b["state"] == "ok" and (b.get("partial") or b.get("capped")):
            from . import search as srch   # local: search imports this module
            state = ("partial: " if b.get("partial") else "capped: ") + ", ".join(srch.part_problems(b))
        elif b["state"] == "ok" and b.get("incomplete"):
            state = f"incomplete: {b['incomplete']}"
        w = b.get("withheld") or 0
        hits = (f"{len(b['hits'])} shown (+{w} withheld)" if w else str(len(b["hits"]))) if b["state"] == "ok" else "-"
        lines.append(f"| {b['source']} | {_cell(b['revision'] or '-')} | {_cell(state)} | {hits} |")
    lines.append("")
    nf = not_found_line(payload)
    if nf:
        lines += [nf, ""]
    nfs = not_fully_searched_line(payload)
    if nfs:
        lines += [nfs, ""]
    cs = coverage_sentence(payload)
    if cs:
        lines += [cs, ""]
    capped = [b for b in payload["sources"] if b["state"] == "ok" and b.get("capped")]
    if capped:   # a source at its result limit lists only the first matches (field report)
        from . import search as srch
        lines += ["Incomplete: " + "; ".join(f"{b['source']}: {', '.join(srch.part_problems(b))}" for b in capped)
                  + "; more matches may exist than the ones listed.", ""]
    lines += ["A skipped source was not searched: it is not a zero. Zero hits do not prove absence.", ""]
    if withheld:
        lines += [f"{withheld} registry hit(s) of private packages matched and are not listed here.", ""]
    lines += ["## Candidates", ""]
    any_hits = False
    for b in payload["sources"]:
        if b["state"] != "ok" or not b["hits"]:
            continue
        any_hits = True
        lines += [f"### {b['source']}", ""]
        lines += [_candidate(h) for h in b["hits"][:TOP_PER_SOURCE]]
        if len(b["hits"]) > TOP_PER_SOURCE:
            lines.append(f"- and {len(b['hits']) - TOP_PER_SOURCE} more not listed")
        lines.append("")
    if not any_hits:
        lines += ["None.", ""]
    lines += ["## Decision", "",
              "decision: pending",
              "reason:", "",
              f"Replace `pending` with one of {' | '.join(DECISIONS)} and give the reason, "
              f"including why each candidate above was rejected.", ""]
    return "\n".join(lines)


def _target(reg_path: pathlib.Path, reg: dict | None, package: str | None,
            repo_dir: str | None) -> tuple[pathlib.Path, str, bool]:
    """(research repository directory, the target's visibility: private | public | unknown, whether the
    directory is the package's own checkout)."""
    pkgs = (reg or {}).get("packages") or {}
    pkg = pkgs.get(package) if package else None
    if repo_dir:
        target = pathlib.Path(repo_dir).expanduser()
    elif pkg is not None:
        target = core.repo_path(pkg)
    elif package:
        raise core.Fail(f"--record --package {package}: not in the registry; pass --repo-dir PATH")
    else:
        raise core.UsageError("--record needs --package NAME (its checkout) or --repo-dir PATH")
    if not target.is_dir():
        raise core.Fail(f"record target is not a directory: {target}")
    reg_dir = reg_path.parent.resolve()
    if reg_dir in (target.resolve(), (target / RECORD_DIR).resolve()):
        raise core.Fail("refusing to write a search record into the registry directory; "
                        "records live in the research repository (--package or --repo-dir)")
    vis = pkg.get("visibility") if pkg is not None else "unknown"
    own_checkout = pkg is not None
    if repo_dir and pkg is not None:
        try:
            own = core.repo_path(pkg).resolve()
        except Exception:
            own = None
        if own is None or own != target.resolve():
            vis, own_checkout = "unknown", False     # the privacy level follows the folder written into
    return target, vis if vis in ("private", "public") else "unknown", own_checkout


def _withhold(payload: dict, reg: dict | None, package: str | None, visibility: str,
              own_checkout: bool = True) -> tuple[dict, int, bool]:
    """A copy of the payload whose registry block hides other private packages' hits unless the target is
    private. The package's own hits are exempt only when the record goes into its own checkout: written into
    any other folder (--repo-dir), a non-public package's hits are withheld like any other private package's.
    Returns (payload copy, number withheld, whether hits of private packages other than the target's own
    checkout are listed)."""
    pkgs = (reg or {}).get("packages") or {}
    private = {n for n, p in pkgs.items() if isinstance(p, dict) and p.get("visibility") != "public"}
    exempt = package if own_checkout else None
    blocks, withheld, listed_private = [], 0, False
    for b in payload["sources"]:
        if b["source"] == "registry" and b["hits"]:
            keep = []
            for h in b["hits"]:
                foreign_private = h["package"] in private and h["package"] != exempt
                if foreign_private and visibility != "private":
                    withheld += 1
                    continue
                listed_private = listed_private or foreign_private
                keep.append(h)
            b = {**b, "hits": keep, "withheld": len(b["hits"]) - len(keep)}
        blocks.append(b)
    return {**payload, "sources": blocks}, withheld, listed_private


def write_record(payload: dict, *, reg_path: pathlib.Path, events_p: pathlib.Path | None,
                 repo_dir: str | None, actor: str | None = None) -> tuple[list[str], dict]:
    reg = core.load_registry(reg_path) if reg_path.is_file() else None
    package = payload.get("package")
    target, visibility, own_checkout = _target(reg_path, reg, package, repo_dir)
    rel = record_rel(payload["date"], payload["term"])
    dest = target / rel
    if dest.exists() or dest.is_symlink():
        raise core.Fail(f"{rel} already exists in {target}; a record is never overwritten "
                        f"(edit it, or search with another phrasing)")
    shown, withheld, private_claims = _withhold(payload, reg, package, visibility, own_checkout)
    text = render_record(shown, withheld=withheld, private_claims=private_claims)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(dest, "x", encoding="utf-8") as f:     # atomic create; refuses a symlink or a race
            f.write(text)
    except FileExistsError:
        raise core.Fail(f"{rel} appeared in {target} during the write; nothing overwritten")
    out = [f"record written: {dest} (decision: pending; edit it, then commit it in that repository)"]
    if has_mkdocs(target):
        out.append(f"note: {rel} is new; {NAV_REASON}")
    sources = {b["source"]: (b["state"] if b["state"] == "ok" else f"skipped: {b['reason']}")
               for b in payload["sources"]}
    hits = {b["source"]: (len(b["hits"]) if b["state"] == "ok" else None) for b in payload["sources"]}
    if events_p is not None:
        core.append_event(events_p, core.now_iso(), package, "prior-art-searched",
                          term=payload["term"], sources=sources, hits=hits, record=rel,
                          decision="pending", **({"actor": actor} if actor else {}))
        event = "prior-art-searched"
    else:
        out.append("no event log (no registry here and no --events): prior-art-searched not recorded")
        event = None
    return out, {"path": rel, "event": event, "withheld": withheld, "private_claims": private_claims,
                 "mkdocs": has_mkdocs(target)}
