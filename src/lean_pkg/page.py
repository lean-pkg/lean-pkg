# lean-pkg page -- the generated verification section of a package README
"""`lean-pkg page NAME [--write | --check | --out PATH] [--site DIR] [--html PATH] [--repo DIR]
[--allow-private-manuscript] [--agent A]`.

Mathematics first (docs/design/page-for-mathematicians.md): one line telling the reader which statements to
compare by eye, then each result with the paper's statement beside the Lean statement, then what the checks
establish, and the receipts last. A public Lean verification repository needs one section a reader can trust: what is formalized and what
is not, under which assumptions, what the receipts prove, how to reproduce it, how to cite it and whom to
credit. Every number and status word in that section is rendered here from data, never typed:

* `coverage.yaml` (coverage/1, lean_pkg.coverage): the paper rows, the capsule sentence, the manuscript
  version, the compute declaration; for `kind: library` (a reusable library with no manuscript) the
  library's main results instead, and no section names a manuscript or a paper;
* `claims.yaml` at HEAD: the declarations of each row;
* the claims ledger (lean_pkg.ledger, promote stage): a row says `verified` only when the ledger closes
  every claim it names;
* the Lean sources at the gate-bound commit: each result's Lean statement (lean_scan.statement), from the
  Challenge module when the comparator compared it (or, for an umbrella Challenge, from the one package file
  of its import closure that declares the theorem, linked at its line), else the claim's file; a link when it
  cannot be read;
* the receipts committed at HEAD under `.lean-receipts/`: what the checks establish, the expected axioms,
  the receipt table (last, under "How this was checked"). Fields are copied from an allow-list only (commit, toolchain, Mathlib revision, sorry
  count, axioms, theorem names, permitted axioms, definition names, date, SHA-256 of the file, a Slurm job
  id, a container engine and image digest); hosts, paths, user names, checker commands and other job ids are never copied;
* `lean-toolchain`, `lake-manifest.json`, `CITATION.cff` (authors must be non-empty), the registry's
  `repo`, `license_spdx` and `doi`. The registry `status` is not rendered: it changes on promote while
  the page is committed at one commit (it stays in `lean-pkg status` and `--json`).

The section lives in the package's README.md between BEGIN and END. `--write` replaces exactly that block
(appending it when absent) and never touches the prose outside the markers. `--check` exits 1 when the
block is missing or stale, when the README lacks the lean-pkg credit, or when it carries the Easley
sentence without a backing Slurm receipt. Every rendered string passes the public-safety scanner with the
package's `.lean-pkg-private-patterns`; a blocking hit refuses.

`--site DIR` also writes DIR/verification.md (the full page) and DIR/coverage.md (every row with its
claims, declarations, ledger statuses, inputs and notes); `--html PATH` one static HTML file of both
(page_html: no script, no external asset). All outputs come from one context, are scanned together and
do not depend on DIR; with `--check` they are compared instead. Text from data is escaped by `_md` in
every output. `--repo DIR` reads another checkout for one call (CI), never with `--write`.

Easley rule, for every site profile (sites.py; Easley is the first). Receipts carry no cluster identity (a
Slurm receipt says `slurm-<J>`, not where J ran), so a site's sentence needs both an explicit declaration
and evidence: coverage.yaml `compute: [{cluster: easley, jobs: [slurm-<J>, ...]}]`, every listed job
matching the `job_id` of a receipt of the site's scheduler committed at HEAD under `.lean-receipts/`
(coverage.slurm_job_of for Slurm; a comparator receipt's job counts only with its sacct seal,
`comparator-receipt --seal-sacct`; ci_github.gha_job_of for GitHub Actions). Without both, no sentence.
And the level: a backed site renders its sentence only when the corroboration level its jobs reached is at
least its scheduler's sentence_level (sites.py). Offline every job is level 1, which is Slurm's; GitHub
Actions needs level 2 (a corroborated run, proposal 0008 stage S3), so its sentence is never rendered here
and a README carrying it fails `--check`.

Not a registry write: the registry is read, never written (no lock, no --commit), like `formalization
--write --no-registry`. `--write` honours a live lease held by another agent (pass --agent HOLDER).
"""

from __future__ import annotations

import json
import re

from . import cli as core
from . import coverage as cov
from . import sites

BEGIN = "<!-- lean-pkg:page:begin -->"
END = "<!-- lean-pkg:page:end -->"
README = "README.md"
LEAN_PKG_URL = "https://github.com/lean-pkg/lean-pkg"
CONCEPT_DOI = "10.5281/zenodo.23272098"
CREDIT = (f"Developed and verified with [lean-pkg]({LEAN_PKG_URL}) "
          f"([doi:{CONCEPT_DOI}](https://doi.org/{CONCEPT_DOI})).")
# Kept names (tests and callers since 0.7): the first built-in site profile's sentence and mention. The page
# itself reads every profile alike (sites.py); no code path below is special to one site.
_FIRST_SITE = next(iter(sites.builtin().values()))
EASLEY = _FIRST_SITE.acknowledgement
EASLEY_RE = _FIRST_SITE.mention_re
# The legacy top-level `page --json` key of a site, `{backed, jobs}` (since 0.7), kept for the first built-in
# profile only and dropped at the 1.0 freeze; every profile is under `sites`.
LEGACY_SITE_KEYS = ("easley",)
# Offline, the level every backed job reaches: consistency with the commit and the committed files
# (proposal 0008); corroboration against the run (level 2) is stage S3.
OFFLINE_LEVEL = 1
GENERATED_NOTE = ("<!-- Generated by `lean-pkg page {name}` from coverage.yaml, claims.yaml, the claims ledger "
                  "and the receipts committed under .lean-receipts/. Do not edit between the markers: change "
                  "the data and run `lean-pkg page {name} --write`. -->")
SITE_NOTE = ("<!-- Generated by `lean-pkg page {name}` (site output) from coverage.yaml, claims.yaml, the claims "
             "ledger and the receipts committed under .lean-receipts/. Do not edit: change the data and render "
             "it again. -->")
SITE_FILES = ("verification.md", "coverage.md")
HTML_NAME = "verification.html"                  # the name the scanner sees for --html PATH
SAFE_TOKEN = re.compile(r"^[^\s`|<>\[\]()*\\]+$")       # a Lean name, an axiom, a toolchain: copied only if this
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T")
LEDGER_WORDS = {
    "ready": "every required claim is closed by the recorded receipts",
    "open": "not every required claim is closed by the recorded receipts",
    "failed": "recorded evidence contradicts a required claim",
    "unknown": "the recorded evidence cannot be read, or establishes nothing yet",
}
def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


_MD_SPECIAL = re.compile(r"([\\`*_\[\]|~])")
_MD_LEADING = re.compile(r"^(?:[#+=-]|\d+[.)])")


def _md(s, lead: bool = False) -> str:
    """User text (coverage.yaml, CITATION.cff, the registry) as inert Markdown: one line; `&`, `<` and `>`
    as entities, so no raw HTML reaches a renderer; the inline markup characters backslash-escaped, so no
    emphasis, code span or link is made from data; with lead (text that starts a line) a leading block
    marker escaped too (no heading, no list)."""
    s = _MD_SPECIAL.sub(r"\\\1", str(s).replace("\n", " "))
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    m = _MD_LEADING.match(s) if lead else None
    return s[:m.end() - 1] + "\\" + s[m.end() - 1:] if m else s


def _code(s) -> str:
    """Text for inside a code span: no backtick, one line, `|` escaped for a table cell."""
    return _cell(str(s).replace("`", "'"))


def _safe(s) -> str | None:
    s = str(s) if s is not None else ""
    return s if s and SAFE_TOKEN.match(s) else None


def _c12(commit) -> str | None:
    return commit[:12] if isinstance(commit, str) and cov.HEX40.match(commit) else None


def _names(items) -> list[str]:
    return [x for x in (_safe(i) for i in items or []) if x]


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1] if names else ""


def _manuscript_ref(m: dict) -> str:
    v = m["version"]
    kind = cov.manuscript_kind(v)
    title = f"*{_md(m['title'])}*"
    if kind == "arxiv":
        ident = v.split(":", 1)[1]
        return f"{title}, [{v}](https://arxiv.org/abs/{ident})"
    if kind == "commit":
        repo = v["repo"].rstrip("/")
        repo = repo[:-4] if repo.endswith(".git") else repo
        return f"{title}, [{_md(repo)} at `{v['commit'][:12]}`]({repo}/tree/{v['commit']}) (file SHA-256 `{v['sha256']}`)"
    return f"{title}, an unposted draft"


# --------------------------------------------------------------------------
# inputs read at HEAD

def _head_text(repo, rel: str) -> str | None:
    raw = core.git_show_bytes(repo, rel)
    return raw.decode("utf-8", errors="replace") if raw is not None else None


def _mathlib_rev(repo) -> str | None:
    text = _head_text(repo, "lake-manifest.json")
    try:
        data = json.loads(text) if text else None
    except ValueError:
        return None
    for p in (data or {}).get("packages") or []:
        if isinstance(p, dict) and str(p.get("name", "")).lower() in ("mathlib", "«mathlib»"):
            rev = str(p.get("rev") or "")
            return rev if re.fullmatch(r"[0-9a-f]{7,40}", rev) else None
    return None


def citation(pkg: dict, repo) -> dict:
    """{authors[], title, version, doi} from CITATION.cff at HEAD; refuses without human authors."""
    import yaml
    name = pkg["name"]
    text = _head_text(repo, "CITATION.cff")
    if text is None:
        raise core.Fail(f"{name}: CITATION.cff is not committed at HEAD; a public page needs one with its "
                        f"authors (docs/page.md#citation)")
    try:
        cff = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise core.Fail(f"{name}: CITATION.cff at HEAD is not valid YAML: {' '.join(str(e).split())}") from e
    if not isinstance(cff, dict):
        raise core.Fail(f"{name}: CITATION.cff at HEAD is not a mapping")
    authors = []
    for a in cff.get("authors") or []:
        if not isinstance(a, dict):
            continue
        given, family, other = (str(a.get(k) or "").strip() for k in ("given-names", "family-names", "name"))
        who = " ".join(x for x in (given, family) if x) or other
        if who:
            authors.append(who)
    if not authors:
        raise core.Fail(f"{name}: CITATION.cff at HEAD has no author with family-names or name: a citation "
                        f"needs its human authors")
    title = str(cff.get("title") or "").strip()
    if not title:
        raise core.Fail(f"{name}: CITATION.cff at HEAD has no title")
    doi = str(cff.get("doi") or "").strip() or ((pkg.get("doi") or {}).get("concept") if isinstance(
        pkg.get("doi"), dict) else None)
    version = str(cff.get("version") or "").strip() or None
    return {"authors": authors, "title": title, "version": version, "doi": doi or None}


def license_file(pkg: dict, repo) -> tuple[str, str]:
    """(license_spdx, the license file committed at HEAD); refuses when they disagree or either is absent."""
    from . import index as index_mod
    name, spdx = pkg["name"], pkg.get("license_spdx")
    if not spdx:
        raise core.Fail(f"{name}: license_spdx is unset in the registry (`lean-pkg update {name} --license-spdx ID`)")
    problem = index_mod.license_problem(repo, spdx)
    if problem:
        raise core.Fail(f"{name}: license_spdx {spdx}: {problem.replace(' at the pin', ' in the checkout')}")
    rel = next((n for n in core.LICENSE_NAMES if core.tracked_at(repo, n)), None)
    if rel is None:
        raise core.Fail(f"{name}: the license file is not committed at HEAD")
    return spdx, rel


# --------------------------------------------------------------------------
# the data every output is rendered from

def _evidence(repo, gate: dict) -> tuple[list[dict], dict, dict]:
    """(table rows, gate receipt facts, comparator receipt facts) from receipts committed at HEAD."""
    rows = []
    for rel in cov.tracked_receipts(repo):
        rec, raw = cov.receipt_at_head(repo, rel)
        if rec is None or not _c12(rec.get("commit")):
            continue
        if rec.get("schema") in core.COMPARATOR_SCHEMAS:   # /2 is what the runner writes; /1 still validates
            tier = "statement comparison"
        elif "sorry_count" in rec and "axioms" in rec:
            tier = "build"
        else:
            continue
        m = DATE_RE.match(str(rec.get("generated_at") or ""))
        rows.append({"path": rel, "tier": tier, "commit": rec["commit"], "date": m.group(1) if m else None,
                     "sha256": core.sha256_bytes(raw), "job": cov.slurm_job_of(rec, repo),
                     "recorded": rel in (gate.get("receipt"), gate.get("comparator"))})
    lower, _ = cov.receipt_at_head(repo, gate["receipt"]) if gate.get("receipt") else (None, None)
    comp, _ = cov.receipt_at_head(repo, gate["comparator"]) if gate.get("comparator") else (None, None)
    return rows, lower or {}, comp or {}


def _receipt_view(pkg: dict, repo, gate: dict, lower: dict) -> dict:
    """`page --repo DIR` (field report): whether the recorded gate receipt validates at DIR's committed HEAD
    (core.validate_receipt: bound to HEAD or an evidence-only descendant, as `formalization render --repo`
    decides). {head, receipt, commit, included, binds_head, reason}. Two ways to fail, never confused:
    binds_head False (no receipt, or its commit is not HEAD nor an ancestor HEAD descends from by the binding
    rule, core.package_descendant): a candidate commit between gate runs, rendered as a preview;
    binds_head True with included False: the receipt binds HEAD but is invalid (axioms, sorry count,
    toolchain, Mathlib, a malformed field): a failed check, never called expected drift."""
    head = core.git_head(repo)
    commit = lower.get("commit") if lower else None
    view = {"head": head, "receipt": gate.get("receipt"), "commit": commit, "included": False,
            "binds_head": False, "reason": None}
    if not gate.get("receipt"):
        view["reason"] = "no gate receipt is recorded in the registry"
        return view
    if not lower:
        view["reason"] = f"the recorded gate receipt {gate['receipt']} is not committed at HEAD (or not JSON)"
        return view
    if head is not None and isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
        try:
            binds, note, _docs = core.package_descendant(repo, commit, head, pkg, checker=lower.get("checker"))
        except core.Fail as e:
            binds, note = False, str(e)
        if not binds:
            view["reason"] = (f"the recorded gate receipt {gate['receipt']} binds commit {commit[:12]}, which "
                              f"HEAD {head[:12]} is not an evidence-only descendant of ({note})")
            return view
    # the receipt binds HEAD (or its commit is malformed, which is no binding to wait for either): validate it
    view["binds_head"] = True
    try:
        core.validate_receipt(pkg, repo, lower)
        view["included"] = True
    except core.Fail as e:
        view["reason"] = f"the recorded gate receipt {gate['receipt']} does not validate ({e})"
    return view


def _context(pkg: dict, repo, allow_private: bool, override: dict | None = None) -> dict:
    """override (`page --repo DIR`): the source links and Lean statements are read at the commit the gate
    receipt binds when that receipt validates at DIR's committed HEAD (so a CI checkout renders the
    registered page), else at DIR's committed HEAD, and then no receipt fact is used (field report: a new
    claim was linked at the old bound commit, where its file did not exist)."""
    from . import ledger as ledger_mod
    from . import index as index_mod
    name = pkg["name"]
    doc, claims, warnings = cov.load_checked(pkg, repo, None, allow_private)
    gate = pkg.get("gate") or {}
    ev_rows, lower, comp = _evidence(repo, gate)
    view = _receipt_view(pkg, repo, gate, lower) if override is not None else None
    invalid = view is not None and not view["included"] and view["binds_head"]
    if view is not None and not view["included"]:
        lower, comp = {}, {}
        ev_rows = [{**e, "recorded": False} for e in ev_rows]
        if invalid:
            warnings.append(f"--repo {override.get('path')} at {str(view['head'])[:12]}: receipt facts omitted: "
                            f"{view['reason']}; the receipt binds this commit, so this is a failed check, not "
                            f"drift between gate runs")
        else:
            warnings.append(f"--repo {override.get('path')} at {str(view['head'])[:12]}: receipt facts omitted: "
                            f"{view['reason']}; the source links and Lean statements are read at "
                            f"{str(view['head'])[:12]} (the receipts committed there do not bind it)")
    profiles = sites.for_repo(repo)
    backed, _problems = cov.compute_backing(doc, repo, profiles)   # load_checked refused any unbacked job
    try:
        https = index_mod.https_repo(pkg.get("repo") or "")
    except core.Fail:
        https = None
        warnings.append(f"registry repo of {name} is not a public https or GitHub URL: the clone command shows "
                        f"a placeholder")
    prefix = core.git(repo, "rev-parse", "--show-prefix").stdout.strip().strip("/")
    bound = lower.get("commit") if _c12(lower.get("commit")) else None
    preview = view is not None and not view["included"] and not invalid
    if invalid:       # the receipt binds HEAD: read at the commit it names (same build inputs), as without --repo
        anchor = view["commit"] if _c12(view["commit"]) else view["head"]
    else:
        anchor = view["head"] if preview else bound      # the commit links and statements are read at
    if view is not None:
        view["anchor_commit"] = anchor
    github = bool(https and https.startswith("https://github.com/"))
    from . import claims as claims_mod
    from . import comparator_config as cc
    configs = [c for c in comp.get("configs") or [] if isinstance(c, dict)] if comp else []
    named = [claims[cid] for r in doc["rows"] for cid in r.get("claims") or [] if cid in claims]
    qualified = cc.qualified_decls(claims_mod._reader(repo, anchor or "HEAD"), named) if configs else set()
    ccommit = str(comp.get("commit") or "") if comp else ""
    smoke = cc.smoke_configs(repo, ccommit if core._HEX40.fullmatch(ccommit) else None, "HEAD") if configs else {}
    return {
        "name": name, "pkg": pkg, "repo": repo, "doc": doc, "claims": claims, "warnings": warnings,
        "led": ledger_mod.ledger_for(pkg, "promote"), "gate": gate, "evidence": ev_rows, "lower": lower,
        "comp": comp, "backed": backed, "sites": profiles, "cite": citation(pkg, repo), "license": license_file(pkg, repo),
        "https": https, "prefix": prefix, "bound": bound, "configs": configs, "qualified": qualified,
        "smoke": smoke,
        "anchor": anchor, "preview": view if preview else None, "invalid": view if invalid else None,
        "repo_view": view,
        "link_base": (f"{https}/blob/{anchor}" + (f"/{prefix}" if prefix else "")) if github and anchor else None,
        "head_base": (f"{https}/blob/HEAD" + (f"/{prefix}" if prefix else "")) if github else None,
        "library": cov.doc_kind(doc) == "library",
        "kind": None if cov.doc_kind(doc) == "library" else cov.manuscript_kind(doc["manuscript"]["version"]),
    }


def _decl_ref(c: dict, cid: str, link_base: str | None) -> str:
    decl = _safe(c.get("decl")) or cid
    path = str(c.get("file") or "")
    if link_base and re.fullmatch(r"[A-Za-z0-9_./«»'-]+\.lean", path) and ".." not in path:
        return f"[`{decl}`]({link_base}/{path})"
    return f"`{_code(decl)}`"


def _row_statuses(r: dict, by_id: dict) -> list[str]:
    return [(by_id.get(cid) or {}).get("status", "not in the ledger") for cid in r.get("claims") or []]


def _status_cells(r: dict, by_id: dict) -> list[str]:
    """Each claim's ledger status for a coverage table, an internal claim marked as such (claims.yaml status
    internal: a step of the formalization, not a result offered for reuse; verified only when the comparator
    judged it, claims ledger)."""
    return [s + (" (internal)" if (by_id.get(cid) or {}).get("claim_status") == "internal" else "")
            for cid, s in zip(r.get("claims") or [], _row_statuses(r, by_id), strict=False)]


def _internal_line(r: dict, by_id: dict) -> list[str]:
    ids = [cid for cid in r.get("claims") or [] if (by_id.get(cid) or {}).get("claim_status") == "internal"]
    if not ids:
        return []
    return [f"**Internal.** {', '.join(f'`{_code(c)}`' for c in ids)}: marked internal in `claims.yaml`, a step of "
            f"the formalization rather than a result offered for reuse. An internal claim counts as verified only "
            f"when the statement comparison judged its declaration, on the same evidence as any other result.", ""]


def _ledger_word(r: dict, statuses: list[str]) -> str:
    if r.get("class") == "outside":
        return "—"
    if statuses and all(s == "verified" for s in statuses):
        return "verified"
    return ", ".join(sorted({s for s in statuses if s != "verified"}))


def _file_ref(ctx: dict, rel: str, target: str) -> str:
    """A link to a file of the package: relative from the README; from a site page (whose directory is the
    caller's choice) the file on GitHub at the default branch, else the name alone."""
    if target == "readme":
        return f"[{rel}]({rel})"
    return f"[{rel}]({ctx['head_base']}/{rel})" if ctx["head_base"] else f"`{rel}`"


def _holes_line(configs: list[dict]) -> str:
    """The definition holes of the comparator receipt, each with the human who judged its body (the
    receipt's signoff copy of lean-pkg-signoff.json, else its deprecated judged_as, ADR-0058 D8):
    comparator compares a hole by name and type only."""
    parts = [_hole_text(c) for c in configs if _names(c.get("definition_names"))]
    return "; ".join(parts) or "none"


def _smoke_line(cfgs: list[dict], smoke: dict) -> str:
    """The theorems of the smoke configurations, each configuration with the reason its smoke mark gives."""
    return "; ".join(", ".join(f"`{_code(t)}`" for t in _names(c.get("theorem_names"))) + f" in `{_code(c['path'])}` "
                     f"({_md(smoke[c['path']])})" for c in cfgs)


def _hole_text(c: dict) -> str:
    from .holes import receipt_judged
    holes = _names(c.get("definition_names"))
    judged = receipt_judged(c)
    who = str(judged.get("reviewer") or "").strip()
    date = str(judged.get("date") or "")
    by = (f" (bodies judged by {_md(who)}" + (f" on {date}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else "")
          + ")") if who else " (no sign-off reviewer recorded)"
    return ", ".join(f"`{d}`" for d in holes) + by


# --------------------------------------------------------------------------
# the Lean statement of a claim: what the reader compares with the paper

def _fence(text: str) -> bool:
    """True when `text` can sit inside a fenced code block unchanged (no line opens or closes a fence)."""
    return not any(x.lstrip().startswith(("```", "~~~")) for x in text.splitlines())


def _lean_statement(ctx: dict, cid: str) -> dict:
    """{decl, file, source ('challenge' | 'declaration'), text or None, config or None} for one claim.
    When the recorded comparator receipt compares the claim, the statement is the one in that
    configuration's Challenge module (what the proof was checked against); otherwise the declaration in
    the claim's own file. Read at the gate-bound commit (HEAD when there is none; under `--repo` DIR's
    committed HEAD when the gate receipt does not validate there)."""
    from . import comparator_config as cc
    from . import lean_scan
    from .formalization import module_path
    c = ctx["claims"][cid]
    decl = str(c.get("decl") or "")
    rev = ctx["anchor"] or "HEAD"

    def read(rel: str | None) -> str | None:
        raw = core.git_show_bytes(ctx["repo"], rel, rev) if rel else None
        return raw.decode("utf-8", errors="replace") if raw is not None else None

    for cfg in ctx["configs"]:
        if cfg.get("path") in ctx.get("smoke", {}):         # a smoke check states no result
            continue
        for t in cfg.get("theorem_names") or []:
            if isinstance(t, str) and cc.claim_covered({t}, c, ctx["qualified"],
                                                       cc.mapped_names(list(ctx["claims"].values()))):
                rel = module_path(cfg.get("challenge_module"))
                src = read(rel)
                text = lean_scan.statement(src, t) if src else None
                line = None
                if src is not None and text is None:
                    # an umbrella Challenge: the theorem is declared in a module it imports, in the package
                    found = _challenge_index(ctx, str(cfg.get("challenge_module")).strip(), read).statement(t)
                    if found["text"] is not None and found["file"] != rel:
                        rel, line, text = found["file"], found["line"], found["text"]
                return {"decl": t, "file": rel, "line": line, "source": "challenge", "config": cfg,
                        "found": src is not None, "text": text if text and _fence(text) else None}
    rel = str(c.get("file") or "") or None
    src = read(rel)
    text = lean_scan.statement(src, decl) if src else None
    return {"decl": decl, "file": rel, "line": None, "source": "declaration", "config": None,
            "found": src is not None, "text": text if text and _fence(text) else None}


def _challenge_index(ctx: dict, module: str, read):
    """The declarations of a Challenge module's import closure within the package, at the gate-bound commit;
    read once per module for the whole page (the closure can hold large files)."""
    from . import lean_scan
    cache = ctx.setdefault("challenge_index", {})
    if module not in cache:
        cache[module] = lean_scan.DeclarationIndex(read, module)
    return cache[module]


def _compared(ctx: dict, st: dict) -> bool:
    """True when a passing configuration of the recorded comparator receipt, at the gate-bound commit,
    judged this statement. A claim the comparator is not required to check (an experimental claim, or a
    reusable definition) can be verified without it; the page then does not say that its statement was compared.
    An internal claim is verified only when the comparator judged it (claims ledger)."""
    comp, cfg = ctx["comp"], st.get("config")
    return bool(st["source"] == "challenge" and cfg and cfg.get("result") == "pass" and comp
                and comp.get("result") == "pass" and ctx["bound"] and comp.get("commit") == ctx["bound"])


def _row_compared(ctx: dict, sts: list[dict]) -> bool:
    return bool(sts) and all(_compared(ctx, st) for st in sts)


def _status_line(word: str, compared: bool) -> str:
    if word == "verified" and compared:
        return ("**Verified.** Lean accepts the proof, and lean-pkg's record shows every check of this result "
                "passed (the build, and the statement comparison).")
    if word == "verified":
        return ("**Verified.** Lean accepts the proof, and lean-pkg's record shows every check that applies to "
                "this result passed (the build; its statement was not compared by a second checker).")
    return (f"**Not yet verified.** lean-pkg's record of the checks says: {_md(word or 'nothing recorded')}. "
            f"Until it says verified, read the Lean statement below as work in progress.")


def _lean_block(ctx: dict, st: dict) -> list[str]:
    name = _safe(st["decl"]) or st["decl"]
    rel = st["file"] or ""
    line = st.get("line")
    where = (f"[{rel}]({ctx['link_base']}/{rel}" + (f"#L{line}" if line else "") + ")"
             if ctx["link_base"] and re.fullmatch(r"[A-Za-z0-9_./«»'-]+\.lean", rel) and ".." not in rel
             else f"`{_code(rel)}`" + (f" (line {line})" if line else "")) if rel else "its file"
    if st["source"] == "challenge":
        head = (f"**In Lean.** `{_code(name)}` in {where}: the statement the proof was checked against "
                f"(the proof is in a separate file, and a second checker confirmed it proves exactly this).")
    else:
        head = f"**In Lean.** `{_code(name)}` in {where}."
    if st["text"] is None and not st["found"]:
        at = "the commit this page reads" if ctx["preview"] or ctx["invalid"] else "the checked commit"
        return [head + f" The file is not committed at {at}, so its statement is not shown here.", ""]
    if st["text"] is None:
        return [head + " Its statement could not be read reliably from the source; open the file to read it.",
                ""]
    return [head, "", "```lean", st["text"], "```", ""]


def _depends_lines(ctx: dict, sts: list[dict]) -> list[str]:
    out = []
    holes = [_hole_text(s["config"]) for s in sts if s["config"] and _names(s["config"].get("definition_names"))]
    for h in dict.fromkeys(holes):
        out.append(f"**Definitions checked by a person.** {h}. The comparison checks a definition by its name "
                   f"and type only, not by what it computes: the named person read the definitions.")
    axioms = _names(ctx["lower"].get("axioms")) if ctx["lower"] else []
    if ctx["lower"]:
        out.append("**Axioms.** At most " + (", ".join(f"`{a}`" for a in axioms) if axioms else "none")
                   + " (what the build check records for the whole package).")
    return [x for line in out for x in (line, "")]


def _result_block(ctx: dict, r: dict, by_id: dict, heading: str, statement: str | None,
                  hypotheses: str | None) -> tuple[list[str], str, bool]:
    word = _ledger_word(r, _row_statuses(r, by_id))
    sts = [_lean_statement(ctx, cid) for cid in r.get("claims") or []]
    compared = _row_compared(ctx, sts)
    md = [f"### {heading}", "", _status_line(word, compared), "", *_internal_line(r, by_id)]
    if not ctx["library"]:
        md += ["**In the paper.**" + ("" if statement else " The statement is not quoted here; read it in the "
                                                          "paper."), ""]
        if statement:
            md += ["```latex", statement.rstrip(), "```", ""]
    elif statement:
        md += ["**In words.**", "", "```latex", statement.rstrip(), "```", ""]
    for st in sts:
        md += _lean_block(ctx, st)
    if hypotheses:
        md += [hypotheses, ""]
    md += _depends_lines(ctx, sts)
    if r.get("note"):
        md += [f"**Note.** {_md(r['note'])}", ""]
    return md, word, compared


def _hypotheses(r: dict) -> str:
    if r["class"] == "same-assumptions":
        return "**Hypotheses.** The paper's own: the Lean statement assumes nothing the paper does not."
    return ("**Hypotheses.** The paper's, plus these, which the Lean statement assumes and the paper proves or "
            "assumes elsewhere: " + "; ".join(_md(x) for x in r["inputs"]) + ".")


TRUST_LINE = ("**How to read this page.** Lean has checked every step of the proofs listed here. It cannot check "
              "that a Lean statement says what the paper says: that comparison is yours. For each result below, "
              "read the paper's statement beside the Lean statement, and check that the hypotheses match.")
LIBRARY_TRUST_LINE = ("**How to read this page.** Lean has checked every step of the proofs listed here. Each "
                      "result is exactly its Lean statement: read the statements below to see what is proved. The "
                      "titles, the words and the notes are the authors' description, which Lean does not check.")


def _body(ctx: dict, target: str) -> tuple[list[str], dict]:
    """Every section, mathematics first, and the facts. target: 'readme' (the README block), 'site'
    (verification.md beside coverage.md) or 'html' (one page holding both)."""
    doc, led = ctx["doc"], ctx["led"]
    by_id = {r["id"]: r for r in led["claims"]}
    md: list[str] = ["## Verification at a glance", "", LIBRARY_TRUST_LINE if ctx["library"] else TRUST_LINE, ""]
    if ctx["preview"]:
        pv = ctx["preview"]
        binds = (f"the recorded build check binds commit `{_c12(pv['commit'])}`" if _c12(pv.get("commit"))
                 else "no recorded build check is committed here")
        md += [f"> **Preview of commit `{_c12(pv['head'])}`.** The links to the Lean files and the Lean "
               f"statements below are read at `{_c12(pv['head'])}`. No check binds this commit yet ({binds}), "
               f"so no result below is verified and no check is reported for it.", ""]
    if ctx["invalid"]:
        iv = ctx["invalid"]
        md += [f"> **The recorded build check does not validate.** The recorded receipt "
               f"`{_code(str(iv['receipt']))}` binds this commit, but it does not validate: {_md(str(iv['reason']))}. "
               f"No result below is verified and no check is reported for it.", ""]
    if ctx["kind"] == "unposted":
        md += ["> **Manuscript not yet posted.** The paper numbers below refer to an unposted draft; a reader "
               "cannot check them until a version is posted.", ""]
    md += [_md(doc["summary"], lead=True), ""]
    blocks, words, n_compared = [], [], 0
    for r in doc["rows"]:
        if ctx["library"]:
            b, w, c = _result_block(ctx, r, by_id, _md(r["title"], lead=True), r.get("statement"), None)
        elif r["class"] == "outside":
            continue
        else:
            p = r["paper"]
            b, w, c = _result_block(ctx, r, by_id, f"{_md(p['number'], lead=True)}: {_md(p['title'])}",
                                 p.get("statement"), _hypotheses(r))
        blocks += b
        words.append(w)
        n_compared += w == "verified" and c
    n_verified = sum(w == "verified" for w in words)
    # "verified" is the ledger's word: a claim that is not reusable needs no comparator, so say how many of
    # the verified results also had their statement compared whenever that is not all of them
    also = (f" (for {n_compared} of them a second checker also compared the statement)"
            if n_compared < n_verified else "")
    if target != "readme":
        blocks += [f"Every result with each of its claims, in one table: [Coverage]"
                   f"({'coverage.md' if target == 'site' else '#coverage'}).", ""]
    if ctx["library"]:
        counts = None
        n = len(doc["rows"])
        md += [f"- Main results: {n}; verified: {n_verified} of {n}{also}.", ""]
        md += ["## Main results", "", *blocks]
    else:
        counts = {c: sum(r["class"] == c for r in doc["rows"]) for c in cov.CLASSES}
        formal = len(doc["rows"]) - counts["outside"]
        md += [f"- Paper: {_manuscript_ref(doc['manuscript'])}.",
               f"- Results listed: {len(doc['rows'])}. In Lean with the paper's own hypotheses: "
               f"{counts['same-assumptions']}; in Lean with extra hypotheses, named below: "
               f"{counts['inputs-supplied']}; not formalized: {counts['outside']}.",
               f"- Verified: {n_verified} of the {formal} result(s) in Lean{also}.", ""]
        md += ["## The paper's results in Lean", "", *blocks]
        md += ["## Not formalized", ""]
        outside = [r for r in doc["rows"] if r["class"] == "outside"]
        if outside:
            md += [f"- {_md(r['paper']['number'])} ({_md(r['paper']['title'])})"
                   + (f": {_md(r['note'])}" if r.get("note") else "") for r in outside]
        else:
            md.append(f"Nothing in the manuscript is excluded: {_md(doc['nothing_excluded']['reason'])}")
        md.append("")
    return _body_rest(ctx, target, md, counts, n_verified)


def _claim_axioms_lines(value) -> list[str]:
    """One bullet for the receipt's additive `claim_axioms` key (AX1), when present: the axioms of every claim
    were read from the built environment, not only from the `#print axioms` output. Nothing without the key."""
    if not isinstance(value, dict) or not isinstance(value.get("checked"), int):
        return []
    extra = sorted({a for e in value.get("nonstandard") or [] if isinstance(e, dict)
                    for a in e.get("axioms") or [] if isinstance(a, str)})
    return [f"- Axioms of every claim: read from the built environment (Lean's `collectAxioms`) for all "
            f"{value['checked']} claim(s) in `claims.yaml`, not only the theorems the build prints; "
            + (f"beyond the standard three, the allowed {', '.join(f'`{_code(a)}`' for a in extra)}."
               if extra else "none uses an axiom beyond `propext`, `Quot.sound` and `Classical.choice`.")]


def _container_clause(value) -> str:
    """`; run in a container (Apptainer), SIF SHA-256 `<hex>`` for a well-formed `container` receipt key (#236),
    else nothing. Only the engine and the digest are copied: never the image ref, which is a path."""
    from . import container as ctr
    if value is None or ctr.problems(value):
        return ""
    kind, digest = ctr.digest_of(value)
    return f"; run in a container ({ctr.LABELS[value['engine']]}), {kind} `{digest}`"


ADVISORIES_URL = "https://github.com/lean-pkg/lean-pkg/blob/main/src/lean_pkg/data/toolchain-advisories.yaml"


ADVISORY_PREFIX = "- Toolchain advisory: "


def _advisory_lines(toolchain, warnings: list[str], ctx: dict | None = None) -> list[str]:
    """One factual line per toolchain advisory (#237) the build check's toolchain is affected by, linked
    to the upstream release notes; each also becomes a page warning for the author. The matches are kept
    in ctx["toolchain_advisories"]; with ctx["advisories"] False (the staleness baseline) no line is made."""
    from . import toolchain_advisories as ta
    tc = _safe(str(toolchain or "").strip())
    found, _problem = ta.lookup(tc)
    if ctx is not None:
        ctx["toolchain_advisories"] = found
        if not ctx.get("advisories", True):
            return []
    out = []
    for v in found:
        out.append(f"{ADVISORY_PREFIX}the build check ran on Lean `{tc}`, which has a known soundness bug "
                   f"fixed in `{v['fixed_in']}` ([{v['id']}]({v['links'][0]}); lean-pkg's "
                   f"[toolchain advisories]({ADVISORIES_URL})).")
        warnings.append(f"toolchain advisory {v['id']}: the build check ran on {tc}; fixed in {v['fixed_in']}")
    return out


def _stack_lines(doc: dict, repo, warnings: list[str]) -> list[str]:
    """The LTS stack line (proposal 0004): only when coverage.yaml declares a stack this lean-pkg declares and
    the committed lean-toolchain and Mathlib commit match it; otherwise a warning and no line."""
    name = doc.get("stack")
    if not isinstance(name, str):
        return []
    from . import stacks
    res = stacks.check(repo, name)
    warnings += res["warnings"]
    if not (res["declared"] and res["matches"]):
        return []
    return [f"- Built on lean-pkg stack [{name}]({stacks.page_url(name)}) (Lean "
            f"`{stacks.toolchain_version(stacks.declared()[name])}`, Mathlib "
            f"`{stacks.declared()[name]['mathlib']['rev']}`), supported until {res['supported_until']}."]


def _body_rest(ctx: dict, target: str, md: list[str], counts: dict | None, n_verified: int
               ) -> tuple[list[str], dict]:
    """What the checks establish, How this was checked, and the citation, license and acknowledgments."""
    name, pkg, repo, doc = (ctx[k] for k in ("name", "pkg", "repo", "doc"))
    lower, comp, gate, cite, led = ctx["lower"], ctx["comp"], ctx["gate"], ctx["cite"], ctx["led"]
    https, prefix, bound, kind = ctx["https"], ctx["prefix"], ctx["bound"], ctx["kind"]
    pstatus = led["package_status"]
    configs = ctx["configs"]
    warnings: list[str] = []
    allowed = _names(gate.get("axioms_allowed") or core.DEFAULT_AXIOMS)
    # what the checks establish, in plain words
    md += ["## What the checks establish", ""]
    if lower:
        axioms = _names(lower.get("axioms"))
        sc = lower.get("sorry_count")
        sorry = ("no `sorry` (an unfinished proof) remains" if sc == 0 else
                 f"the `sorry` count is {sc}" if isinstance(sc, int) else "the `sorry` count is not recorded")
        md.append(f"- **Every proof is checked by Lean.** The package builds; {sorry}; the only axioms used are "
                  f"{', '.join(f'`{a}`' for a in axioms) or 'none'} (allowed: "
                  f"{', '.join(f'`{a}`' for a in allowed)}).")
    else:
        md.append("- **No build check is recorded yet**: nothing on this page is backed by one.")
        warnings.append("the recorded gate receipt is not committed at HEAD (or none is recorded)")
    smoke = ctx.get("smoke") or {}
    smoke_cfgs = [c for c in configs if c.get("path") in smoke]
    if comp and len(smoke_cfgs) < len(configs):
        thms = sum(len(c.get("theorem_names") or []) for c in configs if c.get("path") not in smoke)
        md.append(f"- **The statements shown are the ones proved.** For {thms} statement(s), a second checker "
                  f"(comparator) confirmed that the proof proves exactly the statement shown, with only the "
                  f"permitted axioms, and independent Lean kernels replayed the proof.")
    else:
        md.append("- **No statement comparison is recorded yet**: no Lean statement here has been compared "
                  "with its proof by a second checker.")
    if ctx["library"]:
        md.append("- **Not checked by any tool:** that a title or note describes its Lean statement. A "
                  "definition listed under *Definitions checked by a person* is compared by name and type only.")
    else:
        md.append("- **Not checked by any tool:** that a Lean statement says what the paper says. That is the "
                  "comparison this page asks you to make. A definition listed under *Definitions checked by a "
                  "person* is compared by name and type only.")
    md += ["", "The full account of what each check covers and what it takes on trust is lean-pkg's "
               "[trust model](https://github.com/lean-pkg/lean-pkg/blob/main/docs/trust-model.md).", ""]
    # how this was checked: the receipts, last
    md += ["## How this was checked", ""]
    req = led["required"]
    n_req_ok = sum(1 for r in led["claims"] if r["required"] and r["status"] == "verified")
    md.append(f"- lean-pkg's claims ledger: **{pstatus}**, {LEDGER_WORDS.get(pstatus, _md(pstatus))}; "
              f"{n_req_ok} of {req} required claim(s) verified (`{led['ledger_digest'][:23]}…`).")
    if lower:
        sc = lower.get("sorry_count")
        md.append(f"- Build check (`lean-pkg gate`) at commit `{_c12(lower.get('commit'))}`: `sorry` count "
                  f"{sc if isinstance(sc, int) else '(not recorded)'}; axioms used: "
                  f"{', '.join(f'`{a}`' for a in _names(lower.get('axioms'))) or 'none'}"
                  f"{_container_clause(lower.get('container'))}.")
        md += _claim_axioms_lines(lower.get("claim_axioms"))
        md += _advisory_lines(lower.get("lean_toolchain"), warnings, ctx)
    if comp and configs:
        thms = sum(len(c.get("theorem_names") or []) for c in configs)
        permitted = sorted({a for c in configs for a in _names(c.get("permitted_axioms"))})
        md.append(f"- Statement comparison (comparator) at commit `{_c12(comp.get('commit'))}`: "
                  f"{len(configs)} configuration(s), {thms} theorem(s) compared (each Challenge statement "
                  f"against its Solution proof); permitted axioms: "
                  f"{', '.join(f'`{a}`' for a in permitted) or 'none'}; definitions compared by name only: "
                  f"{_holes_line(configs)}.")
        if smoke_cfgs:
            md.append(f"- Smoke checks (not results): {_smoke_line(smoke_cfgs, smoke)}. Each passed the comparison "
                      f"like the others, to show the comparator set-up works; it states nothing of this package "
                      f"and verifies no result on this page.")
    md += _stack_lines(doc, repo, warnings)
    md.append("")
    ev_rows = ctx["evidence"]
    md += ["### Receipts", ""]
    if ev_rows:
        md += ["| Receipt | Check | Commit | Date | Job | SHA-256 |", "|---|---|---|---|---|---|"]
        for e in ev_rows:
            md.append(f"| `{_code(e['path'])}`{' (used here)' if e['recorded'] else ''} | {e['tier']} | "
                      f"`{e['commit'][:12]}` | {e['date'] or '—'} | {e['job'] or '—'} | `{e['sha256']}` |")
        if ctx["preview"]:
            md += ["", f"None of these receipts binds `{_c12(ctx['anchor'])}`, the commit this page reads: the "
                       f"lines above use none of them. SHA-256 is of the file as committed.", ""]
        elif ctx["invalid"]:
            md += ["", "The receipt the lean-pkg registry holds for this package does not validate: the lines "
                       "above use none of these receipts. SHA-256 is of the file as committed.", ""]
        else:
            md += ["", "*(used here)*: the receipt the lean-pkg registry holds for this package, which the lines "
                       "above read. SHA-256 is of the file as committed.", ""]
    else:
        md += ["No receipt is committed under `.lean-receipts/`.", ""]
    # reproduce
    toolchain = _safe((_head_text(repo, "lean-toolchain") or "").strip())
    rev = _mathlib_rev(repo)
    md += ["### Reproduce", "",
           f"- Lean toolchain: `{toolchain or '(lean-toolchain not committed)'}`.",
           "- Mathlib: " + (f"`{rev}` (lake-manifest.json)." if rev else "not a dependency (lake-manifest.json)."),
           "", "```bash"]
    clone = https or "<repository URL>"
    dirname = (https.rstrip("/").rsplit("/", 1)[-1] if https else "<checkout>")
    md += [f"git clone {clone}", f"cd {dirname}" + (f"/{prefix}" if prefix else "")]
    if bound:
        md.append(f"git checkout {bound}   # the commit the build check binds")
    elif ctx["preview"] and ctx["anchor"]:
        md.append(f"git checkout {ctx['anchor']}   # the commit this page reads; no check binds it yet")
    elif ctx["invalid"] and ctx["anchor"]:
        md.append(f"git checkout {ctx['anchor']}   # the commit this page reads; its recorded check does not "
                  f"validate")
    if rev:
        md.append("lake exe cache get")
    md += ["lake build", "```", ""]
    if lower:
        axioms = _names(lower.get("axioms"))
        md.append("Expected: `#print axioms` on any declaration above reports a subset of "
                  + ("{" + ", ".join(f"`{a}`" for a in axioms) + "}" if axioms else "no axioms")
                  + " (the union the build check records); `sorryAx` never appears.")
        md.append("")
    # citation
    md += ["## Citation", ""]
    line = f"{_join([_md(a, lead=not i) for i, a in enumerate(cite['authors'])])}. *{_md(cite['title'])}*."
    if cite["version"]:
        line += f" Version {_md(cite['version'])}."
    if cite["doi"] and re.fullmatch(r"10\.\d{4,9}/[^\s()\[\]<>`]+", cite["doi"]):
        line += f" [doi:{_md(cite['doi'])}](https://doi.org/{cite['doi']})."
    formalizes = "" if ctx["library"] else f"It formalizes results of {_manuscript_ref(doc['manuscript'])}. "
    md += [line, "", f"{formalizes}See {_file_ref(ctx, 'CITATION.cff', target)}.", ""]
    # license
    spdx, lic_rel = ctx["license"]
    md += ["## License", "", f"Released under `{_code(spdx)}`; see {_file_ref(ctx, lic_rel, target)}.", ""]
    # acknowledgments
    md += ["## Acknowledgments", "", CREDIT]
    lv = doc.get("lean_pkg_version")
    if lv:
        md.append(f"Version used: lean-pkg {lv['version']} ([doi:{lv['doi']}](https://doi.org/{lv['doi']})).")
    site_facts = {n: site_fact(s, ctx["backed"].get(n) or []) for n, s in ctx["sites"].items()}
    md += [s.acknowledgement for n, s in ctx["sites"].items() if site_facts[n]["sentence"]]
    facts = {"rows": len(doc["rows"]), "counts": counts, "verified_rows": n_verified,
             "ledger_status": pstatus, "ledger_digest": led["ledger_digest"],
             "private_manuscript": kind == "unposted", "manuscript": kind,
             "kind": "library" if ctx["library"] else "paper",
             # the legacy top-level key (`easley` since 0.7, its 0.7 shape) and `sites` for every profile,
             # the package's own included
             **{n: {"backed": f["backed"], "jobs": f["jobs"]} for n, f in site_facts.items()
                if n in LEGACY_SITE_KEYS},
             "sites": site_facts,
             "receipts": len(ev_rows),
             "toolchain_advisories": list(ctx.get("toolchain_advisories") or [])}
    return md, {"facts": facts, "warnings": warnings}


def site_fact(site, jobs: list[str]) -> dict:
    """`page --json` sites.<name>: backed, jobs, the corroboration level of each job (`levels`), the lowest
    (`level`; null when no job is listed) and whether the page carries the site's sentence (backed, and the
    level at least the scheduler's sentence_level)."""
    levels = {j: OFFLINE_LEVEL for j in jobs}
    level = min(levels.values()) if levels else None
    sched = sites.scheduler_of(site)
    return {"backed": bool(jobs), "jobs": list(jobs), "level": level, "levels": levels,
            "sentence": bool(jobs) and level >= sched.sentence_level}


def _coverage_lines(ctx: dict, target: str) -> list[str]:
    """coverage.md: every row of coverage.yaml, one table line each, with every claim, its declaration and
    its own ledger status, the inputs and the note (labels stay in coverage.yaml)."""
    doc, claims, led = ctx["doc"], ctx["claims"], ctx["led"]
    by_id = {r["id"]: r for r in led["claims"]}
    md = ["# Coverage", ""]
    if target == "site":
        md += [SITE_NOTE.format(name=ctx["name"]), ""]
    if ctx["library"]:
        return md + _library_coverage_lines(ctx, target)
    counts = {c: sum(r["class"] == c for r in doc["rows"]) for c in cov.CLASSES}
    md += [f"Manuscript: {_manuscript_ref(doc['manuscript'])}.", "",
           f"- Paper results: {len(doc['rows'])} ({counts['same-assumptions']} same assumptions, "
           f"{counts['inputs-supplied']} inputs supplied, {counts['outside']} outside this formalization).",
           f"- lean-pkg's claims ledger: **{led['package_status']}** (`{led['ledger_digest'][:23]}…`).", "",
           "| Paper | Title | Correspondence | Claims | Lean declarations | Ledger | Inputs | Note |",
           "|---|---|---|---|---|---|---|---|"]
    for r in doc["rows"]:
        ids = list(r.get("claims") or [])
        md.append(f"| {_md(r['paper']['number'])} | {_md(r['paper']['title'])} | {cov.CLASS_GLOSS[r['class']]} | "
                  f"{'<br>'.join(f'`{_code(c)}`' for c in ids) or '—'} | "
                  f"{'<br>'.join(_decl_ref(claims[c], c, ctx['link_base']) for c in ids) or '—'} | "
                  f"{'<br>'.join(_status_cells(r, by_id)) or '—'} | "
                  f"{'<br>'.join(_md(x) for x in r.get('inputs') or []) or '—'} | "
                  f"{_md(r['note']) if r.get('note') else '—'} |")
    md += ["", "One line per paper result of `coverage.yaml`. *Ledger*: the status `lean-pkg claims ledger` "
               "gives each claim, in the order of the claims; only *verified* means the "
               "recorded receipts close the claim.", ""]
    if not any(r["class"] == "outside" for r in doc["rows"]):
        md += [f"Nothing in the manuscript is excluded: {_md(doc['nothing_excluded']['reason'])}", ""]
    md.append(f"Back to [Verification]({'verification.md' if target == 'site' else '#verification'}).")
    return md


def _library_coverage_lines(ctx: dict, target: str) -> list[str]:
    """coverage.md of a kind: library coverage: every main result with every claim, its declaration and its
    own ledger status, and the note."""
    doc, claims, led = ctx["doc"], ctx["claims"], ctx["led"]
    by_id = {r["id"]: r for r in led["claims"]}
    md = ["Library: each row is a main result of the library.", "",
          f"- Main results: {len(doc['rows'])}.",
          f"- lean-pkg's claims ledger: **{led['package_status']}** (`{led['ledger_digest'][:23]}…`).", "",
          "| Result | Claims | Lean declarations | Ledger | Note |", "|---|---|---|---|---|"]
    for r in doc["rows"]:
        ids = list(r["claims"])
        md.append(f"| {_md(r['title'])} | {'<br>'.join(f'`{_code(c)}`' for c in ids)} | "
                  f"{'<br>'.join(_decl_ref(claims[c], c, ctx['link_base']) for c in ids)} | "
                  f"{'<br>'.join(_status_cells(r, by_id))} | {_md(r['note']) if r.get('note') else '—'} |")
    md += ["", "One line per main result of `coverage.yaml`. *Ledger*: the status `lean-pkg claims ledger` gives "
               "each claim, in the order of the claims; only *verified* means the recorded "
               "receipts close the claim.", "",
           f"Back to [Verification]({'verification.md' if target == 'site' else '#verification'})."]
    return md


def _scan(pkg: dict, repo, outputs: dict[str, str]) -> None:
    """Every generated byte through the public-safety scanner with the package's patterns; a blocking hit
    in any output refuses them all (nothing is written)."""
    from . import public_safety as ps
    sc = ps.Scanner(pkg, repo, repo)
    hits = []
    for where, text in outputs.items():
        hits += [(where, h) for h in sc.scan_text(text, where) if h[1].severity == ps.BLOCK]
    if hits:
        fams = sorted({h[1].label for _w, h in hits})
        raise core.Fail(f"{pkg['name']}: the rendered page carries {len(hits)} private string(s) ({', '.join(fams)}); "
                        f"nothing written. Fix the data it comes from:\n  "
                        + "\n  ".join(f"{w} line {n}: {ex}" for w, (n, _p, ex) in hits[:10]))


def render_all(pkg: dict, repo, allow_private: bool = False, advisories: bool = True,
               override: dict | None = None) -> tuple[dict[str, str], dict, list[str]]:
    """({'readme': block, 'verification.md', 'coverage.md', 'html'}: every output, each with a final
    newline; the facts for --json; warnings). One context feeds them all; all are scanned together.
    advisories=False leaves the toolchain advisory lines out: the baseline `--check` compares against.
    override (`--repo DIR`): see _context; the facts then carry `repo_view` {head, anchor_commit, receipt,
    commit, included, reason}."""
    from . import page_html
    ctx = _context(pkg, repo, allow_private, override)
    ctx["advisories"] = advisories
    name = ctx["name"]
    body, extra = _body(ctx, "readme")
    block = "\n".join([BEGIN, GENERATED_NOTE.format(name=name), "", *body, "", END]) + "\n"
    site_body, _ = _body(ctx, "site")
    verification = "\n".join(["# Verification", "", SITE_NOTE.format(name=name), "", *site_body]) + "\n"
    coverage = "\n".join(_coverage_lines(ctx, "site")) + "\n"
    html_body, _ = _body(ctx, "html")
    html = page_html.document(f"{name}: verification",
                              ["# Verification", "", *html_body, "", *_coverage_lines(ctx, "html")])
    outputs = {README: block, SITE_FILES[0]: verification, SITE_FILES[1]: coverage, HTML_NAME: html}
    _scan(pkg, repo, outputs)
    facts = extra["facts"] if ctx["repo_view"] is None else {**extra["facts"], "repo_view": ctx["repo_view"]}
    return ({"readme": block, SITE_FILES[0]: verification, SITE_FILES[1]: coverage, "html": html},
            facts, ctx["warnings"] + extra["warnings"])


def render(pkg: dict, repo, allow_private: bool = False) -> tuple[str, dict, list[str]]:
    """(README block text with markers and a final newline, facts for --json, warnings)."""
    outputs, facts, warnings = render_all(pkg, repo, allow_private)
    return outputs["readme"], facts, warnings

# --------------------------------------------------------------------------
# the README block

def split_block(text: str) -> tuple[str, str | None, str] | None:
    """(before, block or None, after): the block with its markers; None when the markers are unbalanced
    or repeated (refused). (text, None, '') when there is no block."""
    nb, ne = text.count(BEGIN), text.count(END)
    if nb == 0 and ne == 0:
        return text, None, ""
    if nb != 1 or ne != 1 or text.index(BEGIN) > text.index(END):
        return None
    i, j = text.index(BEGIN), text.index(END) + len(END)
    if j < len(text) and text[j] == "\n":
        j += 1
    return text[:i], text[i:j], text[j:]


def backed_sites(facts: dict) -> frozenset[str]:
    """The site profiles whose sentence the rendered page carries."""
    return frozenset(n for n, f in facts["sites"].items() if f["sentence"])


def unbacked_site_problems(readme: str, backed: frozenset[str], profiles: dict,
                           facts: dict | None = None) -> list[str]:
    """One problem per site the README mentions anywhere (inside the markers or not) whose sentence the page
    does not carry: no backing, or backing below the scheduler's sentence level (facts: the page's)."""
    out = []
    for s in profiles.values():
        if s.mention_re.search(readme) and s.name not in backed:
            sched = sites.SCHEDULERS[s.scheduler]
            fact = ((facts or {}).get("sites") or {}).get(s.name) or {}
            if fact.get("backed"):
                out.append(f"{README} acknowledges {s.mention}, but its {sched.label} jobs are corroborated to "
                           f"level {fact.get('level')} only, and its sentence needs level {sched.sentence_level}, a "
                           f"corroborated run (`--corroborate`, proposal 0008 stage S3, is not in this release): "
                           f"remove the sentence from {README}; the compute job stays backed")
                continue
            later = (f"; its sentence renders only from a corroborated run (level {sched.sentence_level})"
                     if sched.sentence_level > 1 else "")
            out.append(f"{README} acknowledges {s.mention}, but no {sched.label} receipt backs it (coverage.yaml "
                       f"compute: [{{cluster: {s.name}, jobs: [{sched.placeholder}]}}] with each job's receipt "
                       f"committed){later}")
    return out


def readme_problems(readme: str | None, block: str, backed: frozenset[str], profiles: dict,
                    facts: dict | None = None) -> list[str]:
    """Why `--check` fails, in a fixed order; [] when the README is current. backed: the site profiles
    whose sentence the page carries (backed_sites); profiles: every profile the package can name; facts:
    the page's facts (a site backed below its sentence level is named so)."""
    if readme is None:
        return [f"{README} is missing (`lean-pkg page NAME --write`)"]
    problems = []
    parts = split_block(readme)
    if parts is None:
        problems.append(f"{README} has unbalanced or repeated page markers ({BEGIN} / {END})")
    elif parts[1] is None:
        problems.append(f"{README} has no page block (`lean-pkg page NAME --write` appends it)")
    elif strip_advisories(parts[1]) != strip_advisories(block):
        problems.append(f"{README}: the page block is stale (`lean-pkg page NAME --write`)")
    if LEAN_PKG_URL not in readme or CONCEPT_DOI not in readme:
        problems.append(f"{README} lacks the lean-pkg credit ({LEAN_PKG_URL}, doi:{CONCEPT_DOI})")
    problems += unbacked_site_problems(readme, backed, profiles, facts)
    return problems


def strip_advisories(text: str) -> str:
    """The text without its toolchain advisory lines (#237): an advisory added to lean-pkg's list never
    makes a page stale; the next `--write` shows it."""
    return "".join(x for x in text.splitlines(keepends=True) if not x.startswith(ADVISORY_PREFIX))


def advisory_notes(facts: dict, readme: str | None) -> list[str]:
    """The non-failing NOTE lines of `--check`: one per advisory that applies and the README page block
    does not show yet."""
    parts = split_block(readme) if readme is not None else None
    shown = parts[1] if parts and parts[1] is not None else ""
    return [f"NOTE: toolchain advisory {v['id']} applies; the next page --write will show it"
            for v in facts.get("toolchain_advisories") or [] if f"[{v['id']}](" not in shown]


def replace_block(readme: str | None, block: str) -> str:
    if readme is None:
        return block
    parts = split_block(readme)
    if parts is None:
        raise core.Fail(f"{README} has unbalanced or repeated page markers; fix them by hand first")
    before, old, after = parts
    if old is None:
        sep = "" if not before else ("\n" if before.endswith("\n") else "\n\n")
        return before + sep + block
    return before + block + after


def _read_readme(repo):
    p = repo / README
    if p.is_symlink():
        raise core.Fail(f"{p} is a symlink; refusing to read or write through it")
    return p.read_text(encoding="utf-8") if p.is_file() else None


def _target_problem(p) -> str | None:
    if p.is_symlink():
        return f"{p} is a symlink; refusing to write through it"
    if p.exists() and not p.is_file():
        return f"{p} exists and is not a regular file"
    return None


def _site_targets(site: str | None, html: str | None) -> list[tuple[str, "object", str]]:
    """[(shown path, pathlib.Path, output key)] for --site DIR and --html PATH, in a fixed order."""
    import pathlib
    out = []
    if site is not None:
        d = pathlib.Path(site)
        if d.is_symlink():
            raise core.Fail(f"--site {site} is a symlink; refusing to write through it")
        if d.exists() and not d.is_dir():
            raise core.Fail(f"--site {site} exists and is not a directory")
        out += [(str(pathlib.PurePath(site) / f), d / f, f) for f in SITE_FILES]
    if html is not None:
        out.append((html, pathlib.Path(html), "html"))
    return out


def site_status(targets, outputs: dict[str, str], baseline: dict[str, str] | None = None
                ) -> list[tuple[str, str, str | None]]:
    """[(shown path, current|missing|stale|refused, problem or None)] for `--check --site/--html`. A file is
    current when it equals the output, or the baseline without toolchain advisory lines (#237); a Markdown
    file is compared with its advisory lines left out."""
    def same(p, key) -> bool:
        have = p.read_bytes()
        cands = [outputs[key]] + ([baseline[key]] if baseline else [])
        if any(have == c.encode("utf-8") for c in cands):
            return True
        if key != "html":
            text = have.decode("utf-8", "replace")
            return any(strip_advisories(text) == strip_advisories(c) for c in cands)
        return False
    out = []
    for shown, p, key in targets:
        problem = _target_problem(p)
        if problem:
            out.append((shown, "refused", problem))
        elif not p.is_file():
            out.append((shown, "missing", f"{shown} is missing (`lean-pkg page NAME --site DIR` / `--html PATH`)"))
        elif not same(p, key):
            out.append((shown, "stale", f"{shown} is stale (`lean-pkg page NAME --site DIR` / `--html PATH`)"))
        else:
            out.append((shown, "current", None))
    return out


def _write_atomic(p, text: str) -> None:
    import os
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _write_site(targets, outputs: dict[str, str]) -> list[dict]:
    for _shown, p, _key in targets:                    # refuse before writing any of them
        problem = _target_problem(p)
        if problem:
            raise core.Fail(problem)
    done = []
    for shown, p, key in targets:
        if p.is_file() and p.read_bytes() == outputs[key].encode("utf-8"):
            done.append({"path": shown, "status": "unchanged"})
            continue
        _write_atomic(p, outputs[key])
        done.append({"path": shown, "status": "written"})
    return done


def do_page(reg: dict, name: str, *, write: bool, check: bool, out: str | None, allow_private: bool,
            agent: str | None, site: str | None = None, html: str | None = None,
            repo_arg: str | None = None, strict_preview: bool = False) -> tuple[list[str], dict, list[str], int]:
    if sum(map(bool, (write, check, out))) > 1:
        raise core.UsageError("--write, --check and --out are mutually exclusive")
    if strict_preview and not (check and repo_arg is not None):
        raise core.UsageError("--strict-preview applies to `page --check --repo DIR` only (it makes the differences "
                              "of a preview from the receipt-bound files fail the check)")
    if repo_arg is not None and write:
        raise core.UsageError("--repo reads another checkout; --write edits the registered checkout's README.md "
                              "(drop one)")
    override = None
    if repo_arg is not None:
        reg, override = core.repo_override(reg, name, repo_arg)
        if note := core.repo_override_note(override):
            print(note, file=core.sys.stderr)
    pkg, repo = cov.package_checkout(reg, name)
    targets = _site_targets(site, html)
    outputs, facts, warnings = render_all(pkg, repo, allow_private, override=override)
    if override is not None:
        rv = facts.pop("repo_view")
        override = {**override, "head": rv["head"], "anchor_commit": rv["anchor_commit"],
                    "source": "committed-head",
                    "receipt_facts": {k: rv[k] for k in ("receipt", "commit", "included", "binds_head",
                                                         "reason")}}
        if core.git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
            warnings = [f"--repo {override['path']}: uncommitted changes to tracked files are not read (the page "
                        f"uses the committed HEAD only)"] + warnings
    block = outputs["readme"]
    rf = (override or {}).get("receipt_facts") or {}
    preview = override is not None and rf.get("included") is False and not rf.get("binds_head")
    payload = {"ok": True, "name": name, "readme": README, "markdown": block, "written": False,
               "unchanged": False, "current": None, "preview": preview, "problems": [], "warnings": warnings,
               "site_files": [], "repo_override": override, **facts}
    existing = new = None
    if write:                                          # a lease refusal comes before any file is written
        existing = _read_readme(repo)
        new = replace_block(existing, block)
        if new != existing:
            core.refuse_invalid_lease(name, pkg.get("lease"))
            lease = pkg.get("lease")
            if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) and agent != lease.get("agent"):
                raise core.Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                                f"nothing written")
    site_lines: list[str] = []
    if targets and not check:
        payload["site_files"] = _write_site(targets, outputs)
        site_lines = [f"{name}: {f['path']}: {'written' if f['status'] == 'written' else 'current; nothing written'}"
                      for f in payload["site_files"]]
    if out:
        import pathlib
        outp = pathlib.Path(out)
        if outp.is_symlink():
            raise core.Fail(f"{outp} is a symlink; refusing to write through it")
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(block)
        payload.update(written=True, out=str(outp))
        return site_lines + [f"{name}: wrote the page block to {outp}; {README} untouched"], payload, warnings, 0
    if check:
        readme = _read_readme(repo)
        problems = readme_problems(readme, block, backed_sites(facts), sites.for_repo(repo), facts)
        baseline = render_all(pkg, repo, allow_private, advisories=False, override=override)[0] \
            if facts.get("toolchain_advisories") \
            else None
        status = site_status(targets, outputs, baseline)
        payload["site_files"] = [{"path": shown, "status": st} for shown, st, _p in status]
        problems += [x for _s, _st, x in status if x]
        said = _override_problems(problems, override, [shown for shown, st, _p in status if st == "stale"])
        # option C (#291): under a preview, a difference that comes only from the preview is a warning (the
        # committed files are receipt-bound by design) unless --strict-preview; every other problem still fails
        drift = [x for x, is_drift in said if is_drift and not strict_preview]
        problems = [x for x, is_drift in said if not (is_drift and not strict_preview)]
        warnings += drift                                # the same list object as payload["warnings"]
        payload.update(ok=not problems, current=None if preview else not problems, problems=problems)
        where = README + "".join(f", {shown}" for shown, _p, _k in targets)
        notes = [f"  {n}" for n in advisory_notes(facts, readme)]   # #237: never a staleness reason
        if problems:
            return ([f"{name}: page: STALE ({len(problems)} problem(s))"] + [f"  {p}" for p in problems]
                    + notes + [f"{name}: page: STALE"]), payload, warnings, 1
        if preview:
            n = len(drift)
            return ([f"{name}: page: preview of commit {_c12(override.get('head'))} ({where}): "
                     + (f"{n} difference(s) from the receipt-bound files, expected between gate runs (warnings; "
                        f"--strict-preview fails on them)" if n else "no difference from the committed files")]
                    + notes), payload, warnings, 0
        return [f"{name}: page: current ({where})"] + notes, payload, warnings, 0
    if not write:
        if targets:
            return site_lines, payload, warnings, 0
        return [block.rstrip("\n")], payload, warnings, 0
    if new == existing:
        payload.update(unchanged=True, current=True)
        return site_lines + [f"{name}: {README} page block is current; nothing written"], payload, warnings, 0
    _write_atomic(repo / README, new)
    payload.update(written=True, current=True)
    return site_lines + [f"{name}: wrote the page block into {repo / README} — not committed; commit it yourself"], \
        payload, warnings, 0


def _override_problems(problems: list[str], override: dict | None, stale_sites: list[str]
                       ) -> list[tuple[str, bool]]:
    """[(problem, is preview drift)] for `--check --repo` when the receipt facts were withheld: a stale README
    block or --site/--html file is reworded to say why. A preview (the receipt does not bind HEAD) names both
    commits and is expected drift between gate runs (True: do_page makes it a warning unless --strict-preview);
    a receipt that binds HEAD but does not validate is a failed check, never called drift (False). Every other
    problem (missing block, markers, credit, an unbacked site sentence, a missing file) is never drift."""
    rf = (override or {}).get("receipt_facts") or {}
    if override is None or rf.get("included") is not False:
        return [(p, False) for p in problems]
    head = _c12(override.get("head")) or "HEAD"
    stale = {f"{README}: the page block is stale (`lean-pkg page NAME --write`)": f"{README}: the page block"}
    stale.update({f"{s} is stale (`lean-pkg page NAME --site DIR` / `--html PATH`)": s for s in stale_sites})

    def why(what: str) -> str:
        if rf.get("binds_head"):
            return (f"{what} differs from what --repo renders at commit {head}: the recorded receipt "
                    f"{rf.get('receipt')} binds that commit but does not validate ({rf.get('reason')}), so no receipt "
                    f"fact is shown; this is a failed check, not drift between gate runs (repair or rerun the gate)")
        return (f"{what} differs from this preview: --repo renders commit {head}, which the receipts (bound to "
                f"{_c12(rf.get('commit')) or 'none'}) do not cover, while the committed file is the receipt-bound "
                f"page; expected between gate runs, not a failed gate (`lean-pkg page NAME --check` without --repo "
                f"checks the official page)")
    return [(why(stale[p]), not rf.get("binds_head")) if p in stale else (p, False) for p in problems]


def publish_item(pkg: dict, repo) -> tuple[tuple[str, str], dict]:
    """The `publish --dry-run` checklist line for `page --check`, and its JSON. Adopted (coverage.yaml at
    HEAD or a page block in the README): any problem is ✗. Not adopted: WARN, except a site sentence (Easley's)
    with no backing, which is ✗ (it states where the evidence was produced)."""
    name = pkg["name"]
    try:
        readme = _read_readme(repo)
    except core.Fail as e:
        return ("✗", f"page: {e}"), {"status": "fail", "adopted": None, "problems": [str(e)]}
    adopted = core.tracked_at(repo, cov.DEFAULT_COVERAGE_FILE) or (readme is not None and BEGIN in readme)
    if not adopted:
        try:
            profiles = sites.for_repo(repo)
        except core.Fail as e:
            return ("✗", f"page: {e}"), {"status": "fail", "adopted": False, "problems": [str(e)]}
        problems = readme_problems(readme, "", frozenset(), profiles)
        problems = [p for p in problems if "no page block" not in p] or []
        unbacked = unbacked_site_problems(readme, frozenset(), profiles) if readme is not None else []
        mark = "✗" if unbacked else "WARN"
        # The route: adopt the page (coverage.yaml with compute, then --write); committing README.md and
        # coverage.yaml after the gated commit keeps the receipts binding only under the docs-only opt-in.
        hint = (f"; README.md and coverage.yaml committed after the gated commit keep the receipts binding only "
                f"with the docs-only opt-in (`lean-pkg update {name} --accept-docs-descendants --reason TEXT`)"
                if unbacked and pkg.get("docs_descendants") is not True else "")
        label = (f"no verification page: `lean-pkg page {name} --write` (docs/page.md)"
                 + (f"; {'; '.join(problems)}" if problems else "") + hint)
        return (mark, label), {"status": "fail" if unbacked else "warn", "adopted": False, "problems": problems}
    try:
        block, facts, _w = render(pkg, repo)
    except (core.Fail, core.UsageError) as e:
        # A multi-line refusal (coverage.yaml's) puts only a count on its first line: show the reasons too.
        lines = [x.strip() for x in str(e).splitlines() if x.strip()] or [type(e).__name__]
        reasons = lines[1:] or lines
        shown = " ".join(lines[:1] + ["; ".join(lines[1:])] if len(lines) > 1 else lines)
        label = "page cannot be rendered: " + (shown if len(shown) <= 600 else shown[:597] + "...")
        return ("✗", label), {"status": "fail", "adopted": True, "problems": reasons}
    problems = readme_problems(readme, block, backed_sites(facts), sites.for_repo(repo), facts)
    if problems:
        return ("✗", f"page: {'; '.join(problems)}"), {"status": "fail", "adopted": True, "problems": problems}
    return ("✓", f"page block in {README} is current (`lean-pkg page {name} --check`)"), \
        {"status": "pass", "adopted": True, "problems": []}




def cmd_page(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    lines, payload, warnings, rc = do_page(reg, args.name, write=args.write, check=args.check, out=args.out,
                                           allow_private=args.allow_private_manuscript, agent=args.agent,
                                           site=args.site, html=args.html, repo_arg=args.repo,
                                           strict_preview=getattr(args, "strict_preview", False))
    for w in warnings:
        print(f"lean-pkg: warning: {w}", file=core.sys.stderr)
    if args.write and payload.get("written") and rc == 0:
        lines += docs_only_opt_in(args, reg, payload)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def docs_only_opt_in(args, reg: dict, payload: dict) -> list[str]:
    """After `page --write` changed README.md of a gated package: turn on the docs-only exception the page
    commit needs, unless the record already says either way. Committing the README moves HEAD past the gated
    commit, and without the opt-in the receipts stop binding (the gate, publish and `receipt check` refuse a
    non-evidence descendant). Done through `lean-pkg update NAME --accept-docs-descendants --reason R`, its own
    invocation (its lock, committed mode and `updated` event); the lines say what it records."""
    from . import verify as verify_mod
    pkg = core.get_pkg(reg, args.name)
    gated = (pkg.get("gate") or {}).get("receipt")
    if not gated or pkg.get("docs_descendants") is not None:   # not gated, or opted in or out already
        return []
    bound = core.recorded_json(core.repo_path(pkg), gated) or {}
    c12 = str(bound.get("commit") or "")[:12] or "the gated commit"
    argv = verify_mod.global_flags(args) + ["update", args.name, "--accept-docs-descendants", "--reason",
                                            f"page --write: the README verification page is committed after {c12}"]
    argv += ["--agent", args.agent] if args.agent else []
    json_out = getattr(args, "json_out", False)
    rc, out = verify_mod.invoke(argv + (["--json"] if json_out else []), capture_json=json_out)
    shown = "lean-pkg " + core.shlex.join(argv)
    payload["docs_descendants"] = {"turned_on": rc == 0, "command": shown, "rc": rc,
                                   **({"update": out} if out is not None else {})}
    if rc != 0:
        payload["warnings"].append(f"the docs-only exception is not on (`{shown}` exited {rc}); committing "
                                   f"README.md moves HEAD past {c12} and the receipts stop binding")
        return [f"{args.name}: WARN: the docs-only exception is not on: `{shown}` exited {rc}; run it before "
                f"committing README.md"]
    return [f"{args.name}: turned on the docs-only exception (`{shown}`): the record now says docs_descendants: "
            f"true, so a commit after {c12} that changes only non-build files, such as this README, keeps the "
            f"receipts bound to {c12}; a changed build input (*.lean, lakefile, lake-manifest.json, "
            f"lean-toolchain, claims.yaml) still needs new receipts. Undo: lean-pkg update {args.name} "
            f"--no-accept-docs-descendants --reason R"]


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("page", help="render the README verification section from coverage.yaml, claims, the "
                                    "ledger and the receipts; stdout unless --write/--check/--out/--site/--html")
    s.add_argument("name")
    s.add_argument("--write", action="store_true",
                   help="replace the marked block in the package README.md (append it when absent); prose outside "
                        "the markers is untouched. For a gated package whose record says nothing about the "
                        "docs-only exception, it then turns it on (`update NAME --accept-docs-descendants`, its "
                        "own registry write), so committing the README keeps the receipts bound")
    s.add_argument("--check", action="store_true",
                   help="exit 1 when the README block is missing or stale, the lean-pkg credit is missing, or the "
                        "Easley sentence has no backing Slurm receipt; with --site/--html also when those files "
                        "are missing or stale; writes nothing")
    s.add_argument("--out", default=None, metavar="PATH", help="write the block here instead; README untouched")
    s.add_argument("--site", default=None, metavar="DIR",
                   help="also write DIR/verification.md (the full page) and DIR/coverage.md (every row, its "
                        "claims, inputs and notes) for MkDocs; with --check, compare them instead")
    s.add_argument("--html", default=None, metavar="PATH",
                   help="also write one static HTML file (no script, no external asset) from the same data; "
                        "with --check, compare it instead")
    s.add_argument("--repo", default=None, metavar="DIR",
                   help="read the checkout DIR instead of the registered local_path, for this call only (never "
                        "saved; for CI): DIR must be a git work tree, and when it has remotes one must be the "
                        "package's repo; not with --write. Its committed HEAD is read: when the recorded gate "
                        "receipt does not bind that HEAD (or an evidence-only descendant), the links and Lean "
                        "statements are read at HEAD, no receipt fact is shown and the page says so; with --check, "
                        "differences of such a preview from the receipt-bound files are warnings (exit 0)")
    s.add_argument("--strict-preview", action="store_true",
                   help="with --check --repo: a preview's differences from the receipt-bound README block and "
                        "--site/--html files fail the check (exit 1) instead of warning")
    s.add_argument("--allow-private-manuscript", action="store_true",
                   help="render against manuscript.version 'unposted'; the page then says so")
    s.add_argument("--agent", default=None, help="with --write: the lease holder, when the package is leased")
    add_json(s)
    s.set_defaults(fn=cmd_page)
