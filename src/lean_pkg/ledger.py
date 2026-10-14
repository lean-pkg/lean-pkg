# lean-pkg claims ledger -- per-claim closure computed from the recorded evidence, never stored
"""`lean-pkg claims ledger NAME [--stage gate|promote]` (#80).

For each claim in the package's claims file (as tracked at the gate-bound commit, else the registry
cache) the ledger lists named diagnostics, each a reason the recorded evidence does not close it. A
claim is `verified` only when its diagnostic list is empty. Nothing is written: the ledger is
re-derived from the registry record, the checkout and the receipts on every call, with the same
validators `gate` and `promote` use, so it can never accept less than they do.

Claim status, first match wins: `out-of-scope` (claims.yaml status internal or superseded),
`blocked` (a depends_on claim in this package is not verified), `unknown` (evidence that cannot be
read), `verified` (no diagnostics), else `open`.

Required claims are those with claims.yaml status `reusable`. At stage `gate` they need the lower
tier; at stage `promote` a reusable theorem or lemma also needs the comparator tier, so a lower-tier
receipt alone never closes a claim promotion needs.

Package status fails closed: `unknown` with no claims, no required claim, or a limitation (no
checkout, unreadable evidence); `failed` when recorded evidence contradicts a required claim;
`ready` only when every required claim is verified; else `open`.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

from . import cli as core

LEDGER_SCHEMA = "claims-ledger/1"
STAGES = ("gate", "promote")
REQUIRED_STATUSES = ("reusable",)
OUT_OF_SCOPE_STATUSES = ("internal", "superseded")

# code -> (class, meaning). The class decides the claim status: "open" (evidence missing or stale),
# "unknown" (evidence that cannot be read), "failed" (recorded evidence contradicts the claim),
# "blocked" (a dependency is not closed). Documented in docs/lifecycle.md (#80).
CODES = {
    "no-checkout": ("unknown", "the package has no checkout at local_path on this host"),
    "no-gate-receipt": ("open", "no lower-tier gate receipt is recorded"),
    "receipt-unreadable": ("unknown", "the recorded gate receipt cannot be read as JSON"),
    "bound-commit-missing": ("unknown", "the gate receipt's commit is not in the checkout"),
    "receipt-commit-mismatch": ("open", "the gate receipt binds neither HEAD nor an evidence-only ancestor"),
    "receipt-invalid": ("failed", "the gate receipt fails the gate validator (toolchain, sorry count, fields)"),
    "axiom-not-allowed": ("failed", "the gate receipt lists an axiom outside gate.axioms_allowed"),
    "decl-missing": ("failed", "the declaration does not occur in its file at the gate-bound commit"),
    "no-comparator-receipt": ("open", "promote stage: no comparator receipt is recorded"),
    "comparator-unreadable": ("unknown", "the recorded comparator receipt cannot be read as JSON"),
    "comparator-commit-mismatch": ("open", "the comparator receipt binds another commit than the gate receipt, "
                                           "or neither HEAD nor an evidence-only ancestor"),
    "comparator-invalid": ("failed", "the comparator receipt fails the comparator validator"),
    "no-comparator-config": ("open", "no comparator configuration at the gate-bound commit names the declaration"),
    "comparator-todo": ("open", "the declaration is listed only as a scaffold TODO entry"),
    "depends-on-open": ("blocked", "a depends_on claim in this package is not verified"),
    "depends-on-unknown": ("unknown", "a depends_on claim is in another package or not in this claims file"),
}


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
    rec = core.recorded_json(repo, rel)
    if not isinstance(rec, dict):
        return [_diag("receipt-unreadable", f"{rel} is missing or not a JSON object")], None, hashes
    bound = str(rec.get("commit") or "")
    if not core._HEX40.fullmatch(bound) or core.git(repo, "cat-file", "-e", f"{bound}^{{commit}}").returncode:
        return [_diag("bound-commit-missing", f"{rel}: commit {bound[:12] or '(none)'} is not in the checkout "
                                              f"(fetch it)")], None, hashes
    diags: list[dict] = []
    head = core.git_head(repo) or ""
    ok, note = core.evidence_only_descendant(repo, bound, head, core.formalization_rel(pkg))
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
            core.validate_receipt(pkg, repo, rec)
        except core.Fail as e:
            if not (extra and str(e).startswith("axioms ")):
                diags.append(_diag("receipt-invalid", str(e)))
    return diags, bound, hashes


def _comparator_evidence(pkg: dict, repo: pathlib.Path, bound: str | None) -> tuple[list[dict], dict]:
    """Package-wide comparator-tier diagnostics (promote stage) and the receipt hash."""
    rel = (pkg.get("gate") or {}).get("comparator")
    hashes = {"comparator_sha256": _sha256_file(repo / rel) if rel else None}
    if not rel:
        return [_diag("no-comparator-receipt", f"run comparator-receipt, then `lean-pkg comparator "
                                               f"{pkg['name']} --receipt C`")], hashes
    comp = core.recorded_json(repo, rel)
    if not isinstance(comp, dict):
        return [_diag("comparator-unreadable", f"{rel} is missing or not a JSON object")], hashes
    commit = str(comp.get("commit") or "")
    if bound and commit != bound:
        return [_diag("comparator-commit-mismatch", f"comparator {commit[:12] or '(none)'}, gate receipt "
                                                    f"{bound[:12]}: both tiers must bind one commit")], hashes
    head = core.git_head(repo) or ""
    ok, note = (core.evidence_only_descendant(repo, commit, head, core.formalization_rel(pkg))
                if core._HEX40.fullmatch(commit) else (False, "not a 40-hex commit"))
    if not ok:                                      # stale, not contradicted: re-run, do not call it invalid
        return [_diag("comparator-commit-mismatch", f"comparator {commit[:12] or '(none)'}, HEAD {head[:12]} "
                                                    f"({note})")], hashes
    try:
        core.validate_comparator(pkg, repo, comp, bound)
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
    from . import claims as claims_mod
    from . import comparator_config as cc
    if stage not in STAGES:
        raise core.UsageError(f"--stage must be one of {', '.join(STAGES)}")
    pkg = core.get_pkg(reg, name)
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
            names, todo = cc._names(cc._configs_at(repo, bound)) if stage == "promote" else (set(), set())
            read = claims_mod._reader(repo, bound)

    ids = {str(c.get("id")) for c in claims}
    rows: list[dict] = []
    for c in claims:
        cid, decl = str(c.get("id")), str(c.get("decl") or "")
        required = c.get("status") in REQUIRED_STATUSES
        diags = list(pkg_diags)
        if bound and decl and c.get("file"):
            diags += [_diag("decl-missing", p.split(": ", 1)[-1]) for p in claims_mod.decl_problems(read, [c])]
        needs_comparator = stage == "promote" and required and c.get("kind") in cc.CLAIM_KINDS
        if needs_comparator:
            diags += comp_diags
            if bound and not comp_diags:
                real = names - todo
                if not cc.covers(real, decl):
                    code = "comparator-todo" if cc.covers(todo, decl) else "no-comparator-config"
                    diags.append(_diag(code, f"{decl} at {bound[:12]}"))
        for d in c.get("depends_on") or []:
            if d not in ids:
                diags.append(_diag("depends-on-unknown", f"{d} is not a claim of {name}'s claims file"))
        rows.append({"id": cid, "decl": decl, "kind": c.get("kind"), "claim_status": c.get("status"),
                     "required": required, "depends_on": list(c.get("depends_on") or []), "diagnostics": diags})

    def own_class(row) -> str:
        classes = {CODES[d["code"]][0] for d in row["diagnostics"] if d["code"] != "depends-on-open"}
        return "unknown" if "unknown" in classes else ("verified" if not row["diagnostics"] else "open")

    by_id = {r["id"]: r for r in rows}
    changed = True
    while changed:                                  # propagate open dependencies to a fixed point
        changed = False
        for r in rows:
            for d in r["depends_on"]:
                dep = by_id.get(d)
                if dep is not None and dep["diagnostics"] and not any(
                        x["code"] == "depends-on-open" and x["detail"].startswith(d + " ") for x in r["diagnostics"]):
                    r["diagnostics"].append(_diag("depends-on-open", f"{d} is not verified"))
                    changed = True
    for r in rows:
        if r["claim_status"] in OUT_OF_SCOPE_STATUSES:
            r["status"] = "out-of-scope"
        elif any(d["code"] == "depends-on-open" for d in r["diagnostics"]):
            r["status"] = "blocked"
        else:
            r["status"] = own_class(r)
        r["diagnostics"].sort(key=lambda d: (list(CODES).index(d["code"]), d["detail"]))

    required_rows = [r for r in rows if r["required"]]
    failed = [r["id"] for r in required_rows
              if any(CODES[d["code"]][0] == "failed" for d in r["diagnostics"])]
    reasons: list[str] = list(limitations)
    if not rows:
        reasons.append("no claims: nothing to verify (an empty claims set is not a clean one)")
    elif not required_rows:
        reasons.append("no claim has status reusable: nothing is required, so nothing is established")
    if reasons:
        pstatus = "unknown"
    elif failed:
        pstatus = "failed"
        reasons.append(f"recorded evidence contradicts {len(failed)} required claim(s): {', '.join(failed)}")
    elif any(r["status"] == "unknown" for r in required_rows):
        pstatus = "unknown"
        reasons.append("the evidence for a required claim cannot be read")
    elif all(r["status"] == "verified" for r in required_rows):
        pstatus = "ready"
    else:
        pstatus = "open"
        n = sum(r["status"] != "verified" for r in required_rows)
        reasons.append(f"{n} of {len(required_rows)} required claim(s) not verified")

    canonical = {"schema": LEDGER_SCHEMA, "package": name, "stage": stage, "bound_commit": bound,
                 **hashes, "package_status": pstatus,
                 "claims": [{"id": r["id"], "decl": r["decl"], "kind": r["kind"], "claim_status": r["claim_status"],
                             "depends_on": r["depends_on"], "status": r["status"],
                             "codes": [d["code"] for d in r["diagnostics"]]} for r in rows]}
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    counts = {s: sum(r["status"] == s for r in rows)
              for s in ("verified", "open", "blocked", "unknown", "out-of-scope")}
    return {"ok": pstatus == "ready", "schema": LEDGER_SCHEMA, "name": name, "stage": stage,
            "package_status": pstatus, "reasons": reasons, "bound_commit": bound, "claims_source": source,
            **hashes, "counts": counts, "required": len(required_rows), "claims": rows,
            "ledger_digest": f"sha256:{digest}"}


def ledger_lines(led: dict) -> list[str]:
    verdict = (f"{led['name']}: claims ledger ({led['stage']} stage): {led['package_status'].upper()}; "
               f"{led['required']} required, " + ", ".join(f"{n} {s}" for s, n in led["counts"].items() if n))
    out = [verdict] + [f"  reason: {r}" for r in led["reasons"]]
    out.append(f"  claims from {led['claims_source']}"
               + (f"; gate-bound commit {led['bound_commit'][:12]}" if led["bound_commit"] else ""))
    for r in led["claims"]:
        out.append(f"  {r['status']:<12} {r['id']} ({r['decl']})" + ("" if r["required"] else
                                                                     f" [{r['claim_status']}]"))
        out += [f"      {d['code']}: {d['detail']}" for d in r["diagnostics"]]
    out.append(f"  ledger_digest: {led['ledger_digest']}")
    if led["claims"]:
        out.append(verdict)
    return out
