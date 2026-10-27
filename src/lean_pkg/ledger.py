# lean-pkg claims ledger -- per-claim closure computed from the recorded evidence, never stored
"""`lean-pkg claims ledger NAME [--stage gate|promote]` (#80).

For each claim in the package's claims file (as tracked at the gate-bound commit, else the registry
cache) the ledger lists named diagnostics, each a reason the recorded evidence does not close it. A
claim is `verified` only when its diagnostic list is empty. Nothing is written: the ledger is
re-derived from the registry record, the checkout and the receipts on every call, with the same
validators `gate` and `promote` use, so it can never accept less than they do.

Claim status, first match wins: `out-of-scope` (claims.yaml status superseded, or internal and not
judged), `blocked` (a depends_on claim in this package is not closed), `unknown` (evidence that cannot
be read), `open` (an open or failed diagnostic), `conditional` (proposal 0003: the claim lists hypotheses, or
inherits outstanding ones through depends_on), `verified` (no diagnostics), else `open`.

An internal theorem or lemma is judged (`judged_internal`, promote stage) when the comparator tier is
clean for the package (a valid comparator receipt, bound to the gate-bound commit, every configuration
passing) and a configuration at that commit names its declaration by the rule that credits a reusable
claim. A judged internal claim gets every diagnostic a reusable one gets and is verified on the same
evidence; it is never required, so the package status does not change. What the comparator establishes
for a theorem does not depend on the claim's status: the status says whether a result is offered for
reuse, not whether it is true.

Required claims are those with claims.yaml status `reusable`. At stage `gate` they need the lower
tier; at stage `promote` a reusable theorem or lemma also needs the comparator tier, so a lower-tier
receipt alone never closes a claim promotion needs.

Manuscript findings (`manuscript_findings`, #6) never change a status: each claim row lists them, and the
package-level `manuscript_findings` summary (what the formalization found: open vs fixed in a later arXiv
version) is what `publish --dry-run` and `palomar prepare` print. Only a lean_evidence declaration missing at
the gate-bound commit is a diagnostic (`finding-evidence-missing`, like `decl-missing`).

Conditional claims (proposal 0003, stage H3): when a claim sets `hypotheses`, `discharges` or `witness`, the
ledger reads the hyp-probe/1 section of the gate receipt's sealed verify log at the gate-bound commit X
(`hypotheses.evaluate`): each listed hypothesis must be an explicit binder of the declaration (and of its
Challenge theorem at the promote stage) headed by a def or an inductive Prop; a hypothesis is discharged only at
the promote stage, through a corollary claim and its proof claims compared in the same comparator build at X.
Discharge is recomputed on every call and never stored. A claim with hypotheses is `conditional`, never
`verified`, even when all are discharged; a claim that depends_on a conditional claim inherits its outstanding
hypotheses (`depends-on-conditional`). Facts that are missing (no section, a part that did not run, ambiguous
credit) give `hypotheses-unchecked` (open). A claims file without the keys gets exactly the rows, codes, keys
and digest it had.

Package status fails closed: `unknown` with no claims, no required claim, or a limitation (no
checkout, unreadable evidence); `failed` when recorded evidence contradicts a required claim;
`ready` only when every required claim is verified or conditional (open question 1: the qualifier
`conditional_required` and the `outstanding_hypotheses` are then reported); else `open`.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from . import gate as gate_mod
from . import core

LEDGER_SCHEMA = "claims-ledger/1"
STAGES = ("gate", "promote")
REQUIRED_STATUSES = ("reusable",)
OUT_OF_SCOPE_STATUSES = ("internal", "superseded")
JUDGED_STATUSES = ("internal",)                 # out of scope unless a passing comparator judges the declaration

# code -> (class, meaning). The class decides the claim status: "open" (evidence missing or stale),
# "unknown" (evidence that cannot be read), "failed" (recorded evidence contradicts the claim),
# "blocked" (a dependency is not closed), "conditional" (proposal 0003: closed under outstanding hypotheses).
# Documented in docs/lifecycle.md (#80).
CODES = {
    "no-checkout": ("unknown", "the package has no checkout at local_path on this host"),
    "no-gate-receipt": ("open", "no lower-tier gate receipt is recorded"),
    "receipt-unreadable": ("unknown", "the recorded gate receipt cannot be read as JSON"),
    "bound-commit-missing": ("unknown", "the gate receipt's commit is not in the checkout"),
    "receipt-commit-mismatch": ("open", "the gate receipt binds neither HEAD nor an evidence-only ancestor"),
    "receipt-invalid": ("failed", "the gate receipt fails the gate validator (toolchain, sorry count, fields)"),
    "report-log-missing": ("unknown", "the sealed verify log the gate receipt binds (report_sha256) is missing or "
                                      "unreadable"),
    "axiom-not-allowed": ("failed", "the gate receipt lists an axiom outside gate.axioms_allowed"),
    "decl-missing": ("failed", "the declaration does not occur in its file at the gate-bound commit"),
    "finding-evidence-missing": ("failed", "a manuscript finding's lean_evidence declaration does not occur in "
                                           "its file at the gate-bound commit"),
    "no-comparator-receipt": ("open", "promote stage: no comparator receipt is recorded"),
    "comparator-unreadable": ("unknown", "the recorded comparator receipt cannot be read as JSON"),
    "comparator-commit-mismatch": ("open", "the comparator receipt binds another commit than the gate receipt, "
                                           "or neither HEAD nor an evidence-only ancestor"),
    "comparator-invalid": ("failed", "the comparator receipt fails the comparator validator"),
    "no-comparator-config": ("open", "no comparator configuration at the gate-bound commit names the declaration"),
    "comparator-todo": ("open", "the declaration is listed only as a scaffold TODO entry"),
    "depends-on-open": ("blocked", "a depends_on claim in this package is not verified"),
    "depends-on-unknown": ("unknown", "a depends_on claim is in another package or not in this claims file"),
    # proposal 0003 (conditional claims), judged from the hyp-probe/1 section of the sealed verify log
    "hypotheses-unchecked": ("open", "the claim uses hypotheses, discharges or witness, but the sealed verify "
                                     "log at the gate-bound commit lacks the facts to judge them (no hyp-probe/1 "
                                     "section, a part of the probe that did not run, ambiguous credit): re-gate"),
    "hypothesis-not-in-type": ("failed", "a listed hypothesis is not an explicit binder of the declaration's type "
                                         "(or of its Challenge theorem) at the gate-bound commit"),
    "hypothesis-inadmissible": ("failed", "a listed hypothesis is an axiom, opaque or theorem constant, not a def "
                                          "or an inductive Prop"),
    "hypothesis-open": ("conditional", "a listed hypothesis has no discharge at the gate-bound commit"),
    "discharge-type-mismatch": ("failed", "the corollary's Challenge type is not the discharged claim's type minus "
                                          "the discharged binders, or a proof's type is not the hypothesis"),
    "discharge-not-compared": ("open", "promote stage: the corollary, the claim it discharges or a proof claim is "
                                       "in no comparator configuration at the gate-bound commit"),
    "witness-type-mismatch": ("failed", "the witness claim's Challenge type is not the expected witness of the "
                                        "claim's hypotheses, or the claim takes no parameters"),
    "depends-on-conditional": ("conditional", "a depends_on claim in this package is conditional: its outstanding "
                                              "hypotheses are inherited"),
}
CONDITIONAL_CODES = tuple(c for c, (cls, _) in CODES.items() if cls == "conditional")


def _diag(code: str, detail: str) -> dict:
    assert code in CODES, code
    return {"code": code, "detail": detail}


def _sha256_file(path: pathlib.Path | None) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() if path is not None else None
    except OSError:
        return None


def _gate_evidence(pkg: dict, repo: pathlib.Path) -> tuple[list[dict], str | None, dict]:
    """Package-wide lower-tier diagnostics, the gate-bound commit (None when unusable) and the hashes."""
    gate = pkg.get("gate") or {}
    rel = gate.get("receipt")
    hashes = {"receipt_sha256": _sha256_file(repo / rel) if rel else None}
    if not rel:
        return [_diag("no-gate-receipt", f"run `lean-pkg gate {pkg['name']} --receipt R`")], None, hashes
    rec = gate_mod.recorded_json(repo, rel)
    if not isinstance(rec, dict):
        return [_diag("receipt-unreadable", f"{rel} is missing or not a JSON object")], None, hashes
    bound = str(rec.get("commit") or "")
    if not core._HEX40.fullmatch(bound) or core.git(repo, "cat-file", "-e", f"{bound}^{{commit}}").returncode:
        return [_diag("bound-commit-missing", f"{rel}: commit {bound[:12] or '(none)'} is not in the checkout "
                                              f"(fetch it)")], None, hashes
    diags: list[dict] = []
    head = core.git_head(repo) or ""
    ok, note, _docs = gate_mod.package_descendant(repo, bound, head, pkg, checker=rec.get("checker"))
    if not ok:
        diags.append(_diag("receipt-commit-mismatch", f"receipt {bound[:12]}, HEAD {head[:12]} ({note}); re-gate"))
    allowed = set(gate.get("axioms_allowed") or core.DEFAULT_AXIOMS)
    axioms = rec.get("axioms")
    extra = sorted(set(axioms) - allowed) if isinstance(axioms, list) and all(
        isinstance(a, str) for a in axioms) else []
    if extra:
        diags.append(_diag("axiom-not-allowed", f"axioms {extra} outside {sorted(allowed)}"))
    if ok:
        try:
            gate_mod.validate_receipt(pkg, repo, rec)
        except core.Fail as e:
            if not (extra and str(e).startswith("axioms ")):
                diags.append(_diag("receipt-invalid", str(e)))
        else:
            why = gate_mod.recorded_report_log_problem(repo, rec, rel)
            if why:
                diags.append(_diag("report-log-missing" if why.startswith(gate_mod.REPORT_LOG_MISSING)
                                   else "receipt-invalid", why))
    return diags, bound, hashes


def _comparator_evidence(pkg: dict, repo: pathlib.Path, bound: str | None) -> tuple[list[dict], dict]:
    """Package-wide comparator-tier diagnostics (promote stage) and the receipt hash."""
    rel = (pkg.get("gate") or {}).get("comparator")
    hashes = {"comparator_sha256": _sha256_file(repo / rel) if rel else None}
    if not rel:
        return [_diag("no-comparator-receipt", f"run comparator-receipt, then `lean-pkg comparator "
                                               f"{pkg['name']} --receipt C`")], hashes
    comp = gate_mod.recorded_json(repo, rel)
    if not isinstance(comp, dict):
        return [_diag("comparator-unreadable", f"{rel} is missing or not a JSON object")], hashes
    commit = str(comp.get("commit") or "")
    if bound and commit != bound:
        return [_diag("comparator-commit-mismatch", f"comparator {commit[:12] or '(none)'}, gate receipt "
                                                    f"{bound[:12]}: both tiers must bind one commit")], hashes
    head = core.git_head(repo) or ""
    ok, note, _docs = (gate_mod.package_descendant(repo, commit, head, pkg)
                       if core._HEX40.fullmatch(commit) else (False, "not a 40-hex commit", None))
    if not ok:                                      # stale, not contradicted: re-run, do not call it invalid
        return [_diag("comparator-commit-mismatch", f"comparator {commit[:12] or '(none)'}, HEAD {head[:12]} "
                                                    f"({note})")], hashes
    try:
        gate_mod.validate_comparator(pkg, repo, comp, bound)
    except core.Fail as e:
        return [_diag("comparator-invalid", str(e))], hashes
    return [], hashes


def _claims_source(pkg: dict, repo: pathlib.Path, bound: str | None) -> tuple[list[dict], str]:
    from . import claims as claims_mod
    rel = pkg.get("claims_file") or claims_mod.DEFAULT_CLAIMS_FILE
    if bound:
        raw = core.git_show_bytes(repo, f"./{rel}", bound)
        if raw is not None:
            doc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), f"{rel} at {bound[:12]}")
            return [c for c in doc.get("claims") or [] if isinstance(c, dict)], f"{rel} at {bound[:12]}"
    cached = [c for c in pkg.get("claims") or [] if isinstance(c, dict)]
    synced = pkg.get("claims_synced_commit")
    if synced:
        return cached, f"the registry cache (synced at {str(synced)[:12]})"
    raw = core.git_show_bytes(repo, f"./{rel}", "HEAD")             # no usable gate: the committed file
    if raw is not None:
        doc = claims_mod.load_claims_text(raw.decode("utf-8", errors="replace"), f"{rel} at HEAD")
        return [c for c in doc.get("claims") or [] if isinstance(c, dict)], f"{rel} at HEAD (no usable gate receipt)"
    return cached, "the registry cache (empty)"


def compute_ledger(reg: dict, name: str, stage: str = "promote") -> dict:
    return ledger_for(core.get_pkg(reg, name), stage)


def ledger_for(pkg: dict, stage: str = "promote") -> dict:
    """The ledger of one registry record (what `promote`, `publish --dry-run` and `next` read)."""
    from . import claims as claims_mod
    from . import comparator_config as cc
    if stage not in STAGES:
        raise core.UsageError(f"--stage must be one of {', '.join(STAGES)}")
    name = pkg["name"]
    repo = core.repo_path(pkg) if pkg.get("local_path") else None
    limitations: list[str] = []
    hashes: dict = {"receipt_sha256": None, "comparator_sha256": None}
    bound = None
    if repo is None or not repo.is_dir() or core.git_head(repo) is None:
        limitations.append("no checkout at local_path on this host")
        pkg_diags = [_diag("no-checkout", f"local_path {pkg.get('local_path')!r} is not a git checkout here")]
        comp_diags: list[dict] = []
        claims, source = [c for c in pkg.get("claims") or [] if isinstance(c, dict)], "the registry cache"
    else:
        pkg_diags, bound, h = _gate_evidence(pkg, repo)
        hashes.update(h)
        comp_diags, h = _comparator_evidence(pkg, repo, bound) if stage == "promote" else ([], {})
        hashes.update(h)
        try:
            claims, source = _claims_source(pkg, repo, bound)
        except core.Fail as e:
            limitations.append(f"claims file unreadable: {e}")
            claims, source = [], "unreadable"
        if bound:
            configs = cc._configs_at(repo, bound) if stage == "promote" else {}
            smoke = cc.smoke_configs(repo, bound, "HEAD") if stage == "promote" else {}
            names, todo = (cc._names(configs, cc._sidecar_at(repo, bound), smoke)
                           if stage == "promote" else (set(), set()))
            smoke_thms = cc.smoke_names(configs, smoke)
            read = claims_mod._reader(repo, bound)
            qualified = cc.qualified_decls(read, claims) if stage == "promote" else set()
            real, mapped = names - todo, cc.mapped_names(claims)

    ids = {str(c.get("id")) for c in claims}
    rows: list[dict] = []
    for c in claims:
        cid, decl = str(c.get("id")), str(c.get("decl") or "")
        required = c.get("status") in REQUIRED_STATUSES
        diags = list(pkg_diags)
        if bound and decl and c.get("file"):
            diags += [_diag("decl-missing", p.split(": ", 1)[-1]) for p in claims_mod.decl_problems(read, [c])]
        if bound:
            diags += [_diag("finding-evidence-missing", p) for p in claims_mod.evidence_problems(read, [c])]
        needs_comparator = stage == "promote" and required and c.get("kind") in cc.CLAIM_KINDS
        # an internal theorem/lemma a passing, commit-bound comparator judged: the reusable checks, never required
        judged_internal = bool(stage == "promote" and bound and not comp_diags
                               and c.get("status") in JUDGED_STATUSES and c.get("kind") in cc.CLAIM_KINDS
                               and cc.claim_covered(real, c, qualified, mapped))
        if needs_comparator:
            diags += comp_diags
            if bound and not comp_diags:
                q = decl in qualified
                if not cc.claim_covered(real, c, qualified, mapped):
                    code = ("comparator-todo" if cc.claim_covered(todo, c, qualified, mapped)
                            else "no-comparator-config")
                    why = "" if q else (f" ({decl} is not the fully qualified name of a declaration in "
                                        f"{c.get('file')}: a bare or partial name is credited to no "
                                        f"comparator name)")
                    judged = cc.judged_name(c)
                    if judged and q and judged not in names:
                        why += (f" (judged_as {judged} is a theorem of a smoke configuration only, which verifies "
                                f"no claim)" if judged in smoke_thms else
                                f" (judged_as {judged} is in no comparator configuration's theorem_names)")
                    elif not judged and q and cc.covers(smoke_thms, decl, q):
                        why += " (only a smoke configuration names it, and a smoke check verifies no claim)"
                    near = cc.decl_hint(names, decl, q)     # #6: detail only; the digest covers codes
                    why += f"; hint: {near['hint']}" if near else ""
                    diags.append(_diag(code, f"{decl} at {bound[:12]}{why}"))
        for d in c.get("depends_on") or []:
            if d not in ids:
                diags.append(_diag("depends-on-unknown", f"{d} is not a claim of {name}'s claims file"))
        rows.append({"id": cid, "decl": decl, "kind": c.get("kind"), "claim_status": c.get("status"),
                     "required": required, "judged_internal": judged_internal,
                     "depends_on": list(c.get("depends_on") or []),
                     "statement_basis": c.get("statement_basis"), "manuscript_findings": claim_findings(c),
                     "diagnostics": diags})

    keyed = any(claims_mod.uses_conditional_keys(c) for c in claims)
    if keyed:                                       # proposal 0003: judge the keys from the sealed facts at X
        _conditional(claims, rows, pkg, repo if bound else None, bound, stage, comp_diags,
                     (real, qualified, mapped) if bound and stage == "promote" else None)

    def own_class(row) -> str:
        classes = {CODES[d["code"]][0] for d in row["diagnostics"] if d["code"] != "depends-on-open"}
        if "unknown" in classes:
            return "unknown"
        if classes - {"conditional"}:
            return "open"
        if row.get("conditional_own") or any(d["code"] == "depends-on-conditional" for d in row["diagnostics"]):
            return "conditional"
        return "verified" if not row["diagnostics"] else "open"

    def closes(dep) -> bool:                        # a conditional-class code never blocks a dependent
        return not any(CODES[x["code"]][0] != "conditional" for x in dep["diagnostics"])

    by_id = {r["id"]: r for r in rows}
    changed = True
    while changed:                                  # propagate open dependencies to a fixed point
        changed = False
        for r in rows:
            for d in r["depends_on"]:
                dep = by_id.get(d)
                if dep is not None and not closes(dep) and not any(
                        x["code"] == "depends-on-open" and x["detail"].startswith(d + " ") for x in r["diagnostics"]):
                    r["diagnostics"].append(_diag("depends-on-open", f"{d} is not verified"))
                    changed = True
    if keyed:
        _inherit(rows, by_id, closes)
    for r in rows:
        if r["claim_status"] in OUT_OF_SCOPE_STATUSES and not r["judged_internal"]:
            r["status"] = "out-of-scope"
        elif any(d["code"] == "depends-on-open" for d in r["diagnostics"]):
            r["status"] = "blocked"
        else:
            r["status"] = own_class(r)
        r["diagnostics"].sort(key=lambda d: (list(CODES).index(d["code"]), d["detail"]))
        r.pop("conditional_own", None)
        r.pop("stale_edge", None)

    required_rows = [r for r in rows if r["required"]]
    failed = [r["id"] for r in required_rows
              if any(CODES[d["code"]][0] == "failed" for d in r["diagnostics"])]
    reasons: list[str] = list(limitations)
    if not rows:
        reasons.append("no claims: nothing to verify (an empty claims set is not a clean one)")
    elif not required_rows:
        reasons.append("no claim has status reusable: nothing is required, so nothing is established; fix: "
                       "mark the results others should rely on as status: reusable (`lean-pkg claims mark "
                       f"{name} --from-comparator CONFIG.json --status reusable`, or edit claims.yaml), "
                       "commit, claims sync, re-gate")
    if reasons:
        pstatus = "unknown"
    elif failed:
        pstatus = "failed"
        reasons.append(f"recorded evidence contradicts {len(failed)} required claim(s): {', '.join(failed)}")
    elif any(r["status"] == "unknown" for r in required_rows):
        pstatus = "unknown"
        reasons.append("the evidence for a required claim cannot be read")
    elif all(r["status"] in CLOSED for r in required_rows):
        pstatus = "ready"                           # open question 1: a conditional required claim is closed
    else:
        pstatus = "open"
        n = sum(r["status"] not in CLOSED for r in required_rows)
        reasons.append(f"{n} of {len(required_rows)} required claim(s) not verified"
                       + (" or conditional" if keyed else ""))

    canonical = {"schema": LEDGER_SCHEMA, "package": name, "stage": stage, "bound_commit": bound,
                 **hashes, "package_status": pstatus,
                 "claims": [_canonical_row(r) for r in rows]}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    counts = {s: sum(r["status"] == s for r in rows)
              for s in ("verified", *(("conditional",) if keyed else ()), "open", "blocked", "unknown",
                        "out-of-scope")}
    cond: dict = {}
    if keyed:                                       # only when a claim uses the keys: other ledgers keep their keys
        creq = [r for r in required_rows if r["status"] == "conditional"]
        cond = {"conditional_required": [r["id"] for r in creq],
                "outstanding_hypotheses": sorted({h["decl"] for r in creq for h in r.get("hypotheses") or []
                                                  if h["state"] == "open"})}
    return {"ok": pstatus == "ready", "schema": LEDGER_SCHEMA, "name": name, "stage": stage,
            "package_status": pstatus, "reasons": reasons, "bound_commit": bound, "claims_source": source,
            **hashes, "counts": counts, "required": len(required_rows), **cond, "claims": rows,
            "manuscript_findings": {**findings_summary(rows), "source": source}, "ledger_digest": f"sha256:{digest}"}


CLOSED = ("verified", "conditional")                # the statuses that close a required claim (open question 1)


def _conditional(claims: list[dict], rows: list[dict], pkg: dict, repo, bound, stage: str, comp_diags: list,
                 cred) -> None:
    """Proposal 0003: add the hypotheses diagnostics and the `hypotheses`, `discharges` and `witness` row keys,
    from the hyp-probe/1 facts of the gate receipt's sealed log at X (`hypotheses.evaluate`). `cred`: (the
    non-smoke configured names, the qualified decls, the judged_as names) at the promote stage, else None."""
    from . import claims as claims_mod
    from . import comparator_config as cc
    from . import hypotheses as hyp
    text = None
    if repo is not None and bound:
        rel = (pkg.get("gate") or {}).get("receipt")
        rec = gate_mod.recorded_json(repo, rel) if rel else None
        text = gate_mod.sealed_log_text(repo, rec, rel) if isinstance(rec, dict) else None
    fx = hyp.facts(text)
    by_row = {r["id"]: r for r in rows}
    credited = ({str(c.get("id")): cc.credited_names(cred[0], c, cred[1], cred[2]) for c in claims}
                if cred else {})
    clean = {r["id"]: not any(CODES[d["code"]][0] in ("open", "failed", "unknown") for d in r["diagnostics"])
             for r in rows}
    res = hyp.evaluate(claims, fx, stage=stage, bound=bound, comparator_ok=stage == "promote" and not comp_diags,
                       credited=credited, clean=clean)
    for c in claims:
        r = by_row[str(c.get("id"))]
        own = [h for h in c.get("hypotheses") or [] if isinstance(h, str)]
        r["conditional_own"] = bool(own)
        e = res.get(r["id"]) or {}
        r["diagnostics"] += [_diag(code, detail) for code, detail in e.get("diags") or []]
        if e.get("hypotheses") is not None:
            r["hypotheses"] = e["hypotheses"]
        d = c.get("discharges")
        if isinstance(d, dict):
            r["discharges"] = {k: d.get(k) for k in claims_mod.DISCHARGE_KEYS}
        if e.get("witness") is not None:
            r["witness"] = e["witness"]
        # proposal 0003: an edge to a conditional claim is stale when Lean proved this claim unconditionally
        decl = fx["decls"].get(r["decl"]) if fx.get("ok") else None
        r["stale_edge"] = bool(not own and decl is not None and not decl["binders"]
                               and stage == "promote" and not comp_diags and credited.get(r["id"]))


def _inherit(rows: list[dict], by_id: dict, closes) -> None:
    """depends-on-conditional (proposal 0003): a claim inherits, to a fixed point, the outstanding hypotheses of
    each depends_on claim that is closed but conditional, marked via that claim; only outstanding hypotheses
    pass on (an all-discharged dependency passes on none)."""
    def outstanding(r) -> set[str]:
        return {h["decl"] for h in r.get("hypotheses") or [] if h["state"] == "open"}

    changed = True
    while changed:
        changed = False
        for r in rows:
            for d in r["depends_on"]:
                dep = by_id.get(d)
                if dep is None or not closes(dep):
                    continue
                have = {h["decl"] for h in r.get("hypotheses") or [] if h.get("via") == d}
                new = sorted(outstanding(dep) - have)
                if new:
                    r.setdefault("hypotheses", [])
                    r["hypotheses"] += [{"decl": h, "state": "open", "by": None, "via": d} for h in new]
                    changed = True
    for r in rows:
        for d in dict.fromkeys(r["depends_on"]):
            hs = sorted({h["decl"] for h in r.get("hypotheses") or [] if h.get("via") == d})
            if hs:
                stale = (f"; {r['id']}'s own type takes no hypothesis and the comparator checked it, so this "
                         f"depends_on edge looks stale: remove it" if r.get("stale_edge") else "")
                r["diagnostics"].append(_diag("depends-on-conditional",
                                              f"{d} is conditional: inherits outstanding {', '.join(hs)} via {d}"
                                              + stale))


def _canonical_row(r: dict) -> dict:
    """A row as the digest covers it; the basis and the findings only when set, so a ledger without them keeps
    the digest it had before #6."""
    out = {"id": r["id"], "decl": r["decl"], "kind": r["kind"], "claim_status": r["claim_status"],
           "depends_on": r["depends_on"], "status": r["status"], "codes": [d["code"] for d in r["diagnostics"]]}
    if r.get("statement_basis") is not None:
        out["statement_basis"] = r["statement_basis"]
    if r.get("manuscript_findings"):
        out["manuscript_findings"] = [{k: f.get(k) for k in ("id", "kind", "location", "lean_evidence", "fixed_in")}
                                      for f in r["manuscript_findings"]]
    for k in ("hypotheses", "discharges", "witness"):   # proposal 0003: only when set
        if r.get(k) is not None:
            out[k] = r[k]
    return out


def claim_findings(c: dict) -> list[dict]:
    """A claim's manuscript findings, normalized, each with `state` open (fixed_in null) or fixed."""
    from . import claims as claims_mod
    out = []
    for f in claims_mod.findings_of(c):
        nf = claims_mod.normalize_finding(f)
        nf["state"] = "fixed" if nf.get("fixed_in") else "open"
        out.append(nf)
    return out


def findings_summary(rows: list[dict]) -> dict:
    """What the formalization found in the manuscript, over the ledger rows (or rows carrying `id`,
    `statement_basis` and `manuscript_findings`): totals by kind and by state, the claims verified for a
    corrected statement, and one entry per finding. Informational: no status reads it."""
    from . import claims as claims_mod
    items = []
    for r in rows:
        for f in r.get("manuscript_findings") or []:
            loc = f.get("location") if isinstance(f.get("location"), dict) else {}
            items.append({"claim": r["id"], "id": f.get("id"), "kind": f.get("kind"), "label": loc.get("label"),
                          "summary": f.get("summary"), "fixed_in": f.get("fixed_in"), "state": f.get("state")})
    return {"total": len(items), "open": sum(i["state"] == "open" for i in items),
            "fixed": sum(i["state"] == "fixed" for i in items),
            "by_kind": {k: sum(i["kind"] == k for i in items) for k in claims_mod.FINDING_KINDS},
            "corrected_statements": [r["id"] for r in rows if r.get("statement_basis") == "corrected"],
            "findings": items}


def findings_line(fs: dict | None) -> str | None:
    """One line for publish --dry-run, palomar prepare and the ledger text; None when nothing was found."""
    if not fs or not fs.get("total"):
        return None
    kinds = ", ".join(f"{n} {k}" for k, n in fs["by_kind"].items() if n)
    line = (f"manuscript findings (what the formalization found): {fs['total']} ({kinds}); "
            f"{fs['open']} open, {fs['fixed']} fixed in a later arXiv version")
    if fs["corrected_statements"]:
        line += (f"; verified for the corrected statement: {', '.join(fs['corrected_statements'])}")
    return line + "; statuses unchanged"


def package_findings(pkg: dict, repo) -> dict:
    """findings_summary of the claims file the ledger reads (at the gate receipt's commit, else the cache, else
    HEAD), without re-checking any receipt: for `publish --dry-run` and `palomar prepare`. Unreadable input
    gives an empty summary with `source` saying why."""
    bound = None
    rel = (pkg.get("gate") or {}).get("receipt")
    rec = gate_mod.recorded_json(repo, rel) if rel else None
    if isinstance(rec, dict) and core._HEX40.fullmatch(str(rec.get("commit") or "")) and not core.git(
            repo, "cat-file", "-e", f"{rec['commit']}^{{commit}}").returncode:
        bound = rec["commit"]
    try:
        claims, source = _claims_source(pkg, repo, bound)
    except core.Fail as e:
        claims, source = [], f"unreadable: {e}"
    rows = [{"id": str(c.get("id")), "statement_basis": c.get("statement_basis"),
             "manuscript_findings": claim_findings(c)} for c in claims]
    return {**findings_summary(rows), "source": source}


def ledger_lines(led: dict) -> list[str]:
    verdict = (f"{led['name']}: claims ledger ({led['stage']} stage): {led['package_status'].upper()}; "
               f"{led['required']} required, " + ", ".join(f"{n} {s}" for s, n in led["counts"].items() if n))
    out = [verdict] + [f"  reason: {r}" for r in led["reasons"]]
    out.append(f"  claims from {led['claims_source']}"
               + (f"; gate-bound commit {led['bound_commit'][:12]}" if led["bound_commit"] else ""))
    fl = findings_line(led.get("manuscript_findings"))
    if fl:
        out.append(f"  {fl}")
    for r in led["claims"]:
        basis = " [corrected statement]" if r.get("statement_basis") == "corrected" else ""
        tag = "" if r["required"] else (" [internal, judged by the comparator]" if r.get("judged_internal")
                                        else f" [{r['claim_status']}]")
        out.append(f"  {r['status']:<12} {r['id']} ({r['decl']})" + tag + basis)
        out += [f"      {d['code']}: {d['detail']}" for d in r["diagnostics"]]
        for h in r.get("hypotheses") or []:
            how = (f"discharged (proof {h['by']['proof']}, corollary {h['by']['corollary']})"
                   if h["state"] == "discharged" else "outstanding" + (f" via {h['via']}" if h.get("via") else ""))
            out.append(f"      hypothesis {h['decl']}: {how}")
        if r.get("witness"):
            out.append(f"      witness {r['witness']['claim']}: {r['witness']['state']}")
        for f in r.get("manuscript_findings") or []:
            loc = f.get("location") or {}
            where = ", ".join([str(loc.get("label"))] + [f"{k.replace('_', ' ')} {loc[k]}"
                                                         for k in ("page", "tex_line") if loc.get(k)])
            state = f"fixed in {f['fixed_in']}" if f.get("fixed_in") else "open"
            out.append(f"      finding {f.get('id')} ({f.get('kind')}; {where}): {state}: {f.get('summary')}")
    out.append(f"  ledger_digest: {led['ledger_digest']}")
    if led["claims"]:
        out.append(verdict)
    return out


def first_open(led: dict) -> str | None:
    """`<claim id>: <code>` of the first required claim that is not verified, for a one-line reason."""
    for r in led["claims"]:
        if r["required"] and r["status"] not in CLOSED:
            return f"{r['id']}: {r['diagnostics'][0]['code']}" if r["diagnostics"] else r["id"]
    return None


def summary(led: dict) -> str:
    """One line: the package status and why (the first reason, else the first open claim)."""
    if led["package_status"] == "ready" and led.get("conditional_required"):
        hs = ", ".join(led.get("outstanding_hypotheses") or []) or "none (all discharged)"
        return (f"claims ledger ready: {led['required']} required claim(s) verified or conditional (conditional: "
                f"{len(led['conditional_required'])}; outstanding hypotheses: {hs}) ({led['ledger_digest'][:19]}…)")
    if led["package_status"] == "ready":
        return f"claims ledger ready: {led['required']} required claim(s) verified ({led['ledger_digest'][:19]}…)"
    why = led["reasons"][0] if led["reasons"] else "not ready"
    fo = first_open(led)
    return f"claims ledger {led['package_status']}: {why}" + (f"; first: {fo}" if fo else "")
