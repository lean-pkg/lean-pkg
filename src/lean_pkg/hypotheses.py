# hypotheses -- the hyp-probe/1 section of the lower-tier verify log (proposal 0003, stages H1 and H3)
"""Facts for conditional claims (proposal 0003, issue #320), read from the BUILT environment into the sealed
verify log. Stage H1: the probe, the log section and its write-time structure check. Stage H3: `facts` and
`evaluate`, which the claims ledger calls to judge hypotheses, discharge and witnesses from the section.

How: `scripts/HypProbe.lean` (core Lean only; data-only import, `loadExts := false`, as ClaimAxioms.lean) prints
facts and never a verdict. The verify scripts (`receipt slurm --template`, the `verify.sh` that `lean-pkg new`
writes) run `hypotheses_run` (`job_sh()` below) after `claim_axioms_run`, appending to the log, before the seal:

    == lean-pkg hypotheses (hyp-probe/1) ==
    hyp-decl: {...}             decl part: every claim decl of claims.yaml (hyp-missing: one not in the build)
    hyp-challenge-build: {...}  Challenge part, only when claims.yaml uses hypotheses, discharges or witness:
    hyp-challenge: {...}          each Challenge module is built OUTSIDE the sealed log (a side log), so its
    hyp-corollary: {...}          `sorry` warnings never reach sorry_count; only its status line enters the log.
    hyp-proof: {...}              Then every configured Challenge theorem, and positive facts only.
    hyp-witness: {...}
    hyp-skip: {...}             the corollary search not run (more than 12 distinct hypothesis constants)
    hyp-error: {...}            a part that could not run (never a refusal: consumers read it as unchecked)
    hyp-done: {...}             the last line, written by the job with the counts

The line format is docs/formats.md, "The hypotheses section of the verify log". `report_sha256` seals the
section; the receipt schema does not change (no new key).

Write-time structure (`judge_structure`, called by `receipt slurm|systemd` and `--check` through
`receipt.hypotheses_evidence`): refused are a section without `hyp-done`, counts that do not match it, a line
printed twice, a malformed line (not one JSON object with the tag's exact keys), a line after `hyp-done`, a tag
line outside the section, and a redaction that alters a section line. A log without the section (an older
verify script) is accepted unchanged: the facts are simply absent.

Python API: `parse(text)`, `section_lines(text)`, `uses_keys(claims_bytes)`, `judge_structure(text,
claims_bytes=None, redacted=None)`, `job_sh()`, `probe(repo, toolchain=None, timeout=...)`.
Command line: `python3 -m lean_pkg.hypotheses --repo DIR [--toolchain TC] [--timeout S] [--json]` runs the
job's block on a built checkout (it builds the Challenge modules, as the job does) and prints the section (or
the structure judgement with --json). Exit 0 the section is complete, 1 refused or the probe failed, 2 usage.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SCRIPT = pathlib.Path(__file__).with_name("scripts") / "HypProbe.lean"
FORMAT = "hyp-probe/1"
HEADER = f"== lean-pkg hypotheses ({FORMAT}) =="
MAX_HYPS = 12               # HypProbe.MAX_HYPS: above it the corollary search prints hyp-skip
DEFAULT_TIMEOUT = 1800.0
SIDE_LOG = "hyp-challenge-build.log"
# The claims.yaml keys that switch the Challenge part on (the job greps the same pattern).
KEYS_RE = re.compile(r"^[ \t-]+(hypotheses|discharges|witness):", re.M)
_TAG_LINE = re.compile(r"^(hyp-[a-z-]+):(.*?)\r?$")

_BINDER_KEYS = {"i": int, "head": str, "kind": str, "module": str, "explicit": bool}
_DECL_KEYS = {"v": int, "theorem": str, "binders": list, "data_binders": int}
# tag -> {key: type} (exact key sets; `v` must be 1)
TAGS: dict[str, dict[str, type]] = {
    "hyp-decl": _DECL_KEYS,
    "hyp-missing": {"v": int, "theorem": str},
    "hyp-challenge-build": {"module": str, "ok": bool},
    "hyp-challenge": {**_DECL_KEYS, "config": str},
    "hyp-corollary": {"corollary": str, "of": str, "drops": list, "binders": list},
    "hyp-proof": {"proof": str, "proves": str},
    "hyp-witness": {"witness": str, "of": str},
    "hyp-skip": {"theorem": str, "reason": str},
    "hyp-error": {"phase": str, "message": str},
    "hyp-done": {"v": int, "decls": int, "challenge": int, "configs": list},
}
FACT_TAGS = ("hyp-corollary", "hyp-proof", "hyp-witness")


def _is(value, typ) -> bool:
    if typ is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    return isinstance(value, typ)


def _shape_problem(tag: str, obj) -> str | None:
    """Why `obj` is not a well-formed `tag` object (None when it is)."""
    if not isinstance(obj, dict):
        return "not a JSON object"
    keys = TAGS[tag]
    if set(obj) != set(keys):
        missing, extra = sorted(set(keys) - set(obj)), sorted(set(obj) - set(keys))
        return "keys " + "; ".join(x for x in (f"missing {missing}" if missing else "",
                                               f"unexpected {extra}" if extra else "") if x)
    for k, typ in keys.items():
        if not _is(obj[k], typ):
            return f"`{k}` is not a {typ.__name__}"
    if "v" in keys and obj["v"] != 1:
        return f"`v` is {obj['v']!r}, not 1"
    if "binders" in keys and tag != "hyp-corollary":
        for b in obj["binders"]:
            if not isinstance(b, dict) or set(b) != set(_BINDER_KEYS) or not all(
                    _is(b[k], t) for k, t in _BINDER_KEYS.items()):
                return "a binder is not {i, head, kind, module, explicit}"
    if tag == "hyp-corollary":
        if not obj["drops"] or not all(isinstance(x, str) and x for x in obj["drops"]):
            return "`drops` is not a non-empty list of names"
        if not obj["binders"] or not all(_is(x, int) for x in obj["binders"]):
            return "`binders` is not a non-empty list of positions"
    if tag == "hyp-error" and obj["phase"] not in ("decl", "challenge"):
        return f"`phase` is {obj['phase']!r}, not decl or challenge"
    if tag == "hyp-done" and not all(isinstance(x, str) for x in obj["configs"]):
        return "`configs` is not a list of paths"
    return None


def _identity(tag: str, obj: dict):
    """What may appear only once per section: one line per decl, per (Challenge theorem, configuration), per
    Challenge module build; any other line only once as written."""
    if tag in ("hyp-decl", "hyp-missing"):
        return ("decl", obj["theorem"])
    if tag == "hyp-challenge":
        return (tag, obj["theorem"], obj["config"])
    if tag == "hyp-challenge-build":
        return (tag, obj["module"])
    return None


def section_lines(text: str) -> list[str]:
    """The section's header and tag lines, as written (a redaction that alters one is refused)."""
    return [line.rstrip("\r") for line in text.splitlines() if line.rstrip("\r") == HEADER or _TAG_LINE.match(line)]


def parse(text: str) -> dict:
    """{present, records [(tag, object)], done (object or None), problems}: the hyp-probe/1 section of a log.
    `present` is False for a log with neither the header nor any `hyp-*:` line (an older verify script)."""
    records: list[tuple[str, dict]] = []
    problems: list[str] = []
    seen_lines: set[str] = set()
    seen_ids: set = set()
    header = False
    done: dict | None = None
    present = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if line == HEADER:
            present = True
            if header:
                problems.append(f"verify log line {n}: a second `{HEADER}` header")
            header = True
            continue
        m = _TAG_LINE.match(line)
        if not m:
            continue
        present = True
        tag, payload = m.group(1), m.group(2).strip()
        if tag not in TAGS:
            problems.append(f"verify log line {n}: `{tag}:` is not a {FORMAT} line")
            continue
        if not header:
            problems.append(f"verify log line {n}: a `{tag}:` line before the `{HEADER}` header")
            continue
        if done is not None:
            problems.append(f"verify log line {n}: a `{tag}:` line after `hyp-done:`")
            continue
        if line in seen_lines:
            problems.append(f"verify log line {n}: `{tag}:` printed twice: {payload[:160]}")
            continue
        seen_lines.add(line)
        try:
            obj = json.loads(payload)
        except ValueError:
            problems.append(f"verify log line {n}: `{tag}:` is not followed by one JSON object: {payload[:160]!r}")
            continue
        why = _shape_problem(tag, obj)
        if why:
            problems.append(f"verify log line {n}: malformed `{tag}:` line ({why})")
            continue
        ident = _identity(tag, obj)
        if ident is not None:
            if ident in seen_ids:
                problems.append(f"verify log line {n}: a second `{tag}:` line for {ident[1]}"
                                + (f" in {ident[2]}" if len(ident) > 2 else ""))
                continue
            seen_ids.add(ident)
        if tag == "hyp-done":
            done = obj
            continue
        records.append((tag, obj))
    return {"present": present, "records": records, "done": done, "problems": problems}


def uses_keys(claims_bytes: bytes | None) -> bool:
    """Whether a claims.yaml uses `hypotheses`, `discharges` or `witness` (the job's switch for the Challenge
    part, the same pattern)."""
    if not claims_bytes:
        return False
    return KEYS_RE.search(claims_bytes.decode("utf-8", errors="replace")) is not None


def judge_structure(text: str, claims_bytes: bytes | None = None,
                    redacted: str | None = None) -> tuple[dict | None, list[str], list[str]]:
    """(summary or None when the log has no section, refusals, check lines): the write-time structure check.
    `redacted`: the published log text, whose section lines must equal the raw log's. Nothing here judges a
    claim (the ledger does, `evaluate`); `hyp-error` and `hyp-skip` are reported, never refused."""
    sec = parse(text)
    keyed = uses_keys(claims_bytes)
    if not sec["present"]:
        lines = []
        if keyed:
            lines.append(f"· hypotheses: no {FORMAT} section in the verify log (a verify script without the "
                         f"probe): the claims that use hypotheses, discharges or witness will be unchecked; "
                         f"regenerate verify.sh or the sbatch template to get it")
        return None, [], lines
    refusals = list(sec["problems"])
    records, done = sec["records"], sec["done"]
    if redacted is not None and section_lines(text) != section_lines(redacted):
        refusals.append(f"redaction changed the {FORMAT} section of the verify log (its lines differ between the "
                        f"raw and the redacted log); narrow the --redact pattern so it leaves those lines intact")
    count = {t: sum(1 for tag, _ in records if tag == t) for t in TAGS}
    decls = count["hyp-decl"] + count["hyp-missing"]
    if done is None:
        refusals.append(f"verify log: a {FORMAT} section without its final `hyp-done:` line: the probe did not "
                        f"finish (or the log was cut)")
    else:
        if done["decls"] != decls:
            refusals.append(f"verify log: `hyp-done:` counts {done['decls']} decl line(s) but the section has "
                            f"{decls} (hyp-decl and hyp-missing): the output is inconsistent")
        if done["challenge"] != count["hyp-challenge"]:
            refusals.append(f"verify log: `hyp-done:` counts {done['challenge']} hyp-challenge line(s) but the "
                            f"section has {count['hyp-challenge']}: the output is inconsistent")
        stray = sorted({o["config"] for t, o in records if t == "hyp-challenge"} - set(done["configs"]))
        if stray:
            refusals.append(f"verify log: hyp-challenge lines name configuration(s) {stray} that `hyp-done:` does "
                            f"not list")
    summary = {"decls": decls, "missing": count["hyp-missing"], "challenge": count["hyp-challenge"],
               "facts": sum(count[t] for t in FACT_TAGS), "skipped": count["hyp-skip"],
               "errors": count["hyp-error"],
               "challenge_builds_failed": sum(1 for t, o in records if t == "hyp-challenge-build" and not o["ok"])}
    lines = []
    if not refusals:
        lines.append(f"✓ hypotheses: {FORMAT} section complete: {decls} decl(s), {summary['challenge']} Challenge "
                     f"theorem(s), {summary['facts']} fact(s) (sealed by report_sha256; judged by the claims ledger "
                     f"in a later stage)")
        for tag, obj in records:
            if tag == "hyp-error":
                lines.append(f"· hypotheses: the {obj['phase']} part did not run ({obj['message'][:200]})"
                             + ("; the claims that use hypotheses, discharges or witness will be unchecked"
                                if keyed else "; claims.yaml uses no hypotheses, discharges or witness"))
            elif tag == "hyp-skip":
                lines.append(f"· hypotheses: corollary search skipped for {obj['theorem']} ({obj['reason'][:200]})")
            elif tag == "hyp-challenge-build" and not obj["ok"]:
                lines.append(f"· hypotheses: the Challenge module {obj['module']} did not build (its output is in "
                             f"the job's side log {SIDE_LOG}, outside the sealed log)")
    return summary, refusals, lines


# ---------------------------------------------------------------------------------------------------------
# The ledger side (stage H3): the sealed facts, judged per claim. Lean printed the facts; this decides.

ADMISSIBLE_KINDS = ("def", "inductive")         # an assumption is a def or an inductive Prop (never an axiom)
GATE_STAGE_OPEN = "at the gate stage nothing is discharged (discharge needs the comparator tier: --stage promote)"


def facts(text: str | None) -> dict:
    """The section of a sealed verify log as the facts the ledger reads: {ok, why} and, when ok, `decls`
    {theorem: hyp-decl object}, `missing` (decls not in the build), `challenge` {theorem: [hyp-challenge
    objects]}, `corollaries` {(corollary, of): [drops as a frozenset, ...]}, `proofs` {(proof, proves)},
    `witnesses` {(witness, of)}, `skips` {theorem: reason}, `decl_error` and `challenge_error` (the first
    hyp-error message of each part, else None), `builds_failed` [module] and `configs` (hyp-done's list: empty
    when the Challenge part did not run). Not ok (`why` says which): no log, no section, or a section the
    write-time check would refuse."""
    if text is None:
        return {"ok": False, "why": "no sealed verify log of the gate receipt is readable here"}
    sec = parse(text)
    if not sec["present"]:
        return {"ok": False, "why": f"the sealed verify log has no {FORMAT} section (a verify script without the "
                                    f"probe): regenerate verify.sh or the sbatch template and re-gate"}
    if sec["problems"] or sec["done"] is None:
        first = sec["problems"][0] if sec["problems"] else "no final `hyp-done:` line"
        return {"ok": False, "why": f"the {FORMAT} section of the sealed verify log is incomplete ({first})"}
    fx: dict = {"ok": True, "why": None, "decls": {}, "missing": set(), "challenge": {}, "corollaries": {},
                "proofs": set(), "witnesses": set(), "skips": {}, "decl_error": None, "challenge_error": None,
                "builds_failed": [], "configs": list(sec["done"]["configs"])}
    for tag, o in sec["records"]:
        if tag == "hyp-decl":
            fx["decls"][o["theorem"]] = o
        elif tag == "hyp-missing":
            fx["missing"].add(o["theorem"])
        elif tag == "hyp-challenge":
            fx["challenge"].setdefault(o["theorem"], []).append(o)
        elif tag == "hyp-corollary":
            fx["corollaries"].setdefault((o["corollary"], o["of"]), []).append(frozenset(o["drops"]))
        elif tag == "hyp-proof":
            fx["proofs"].add((o["proof"], o["proves"]))
        elif tag == "hyp-witness":
            fx["witnesses"].add((o["witness"], o["of"]))
        elif tag == "hyp-skip":
            fx["skips"].setdefault(o["theorem"], o["reason"])
        elif tag == "hyp-error":
            fx[f"{o['phase']}_error"] = fx[f"{o['phase']}_error"] or o["message"]
        elif tag == "hyp-challenge-build" and not o["ok"]:
            fx["builds_failed"].append(o["module"])
    return fx


def _challenge_problem(fx: dict) -> str | None:
    """Why the Challenge part's facts cannot be used (None when they can)."""
    if fx["challenge_error"]:
        return f"the Challenge part of the probe did not run ({fx['challenge_error'][:160]})"
    if fx["builds_failed"]:
        return f"the Challenge module(s) {', '.join(fx['builds_failed'])} did not build"
    if not fx["configs"]:
        return "the Challenge part of the probe did not run (hyp-done lists no configuration)"
    return None


def _binder_problems(obj: dict, hyps: list[str], where: str) -> tuple[list[str], list[str]]:
    """(not-in-type, inadmissible) details for the listed hypotheses against one hyp-decl/hyp-challenge object:
    every binder headed by a listed name must be explicit, at least one must exist, and its head must be a def
    or an inductive Prop."""
    absent, bad = [], []
    for h in hyps:
        bs = [b for b in obj["binders"] if b["head"] == h]
        if not bs:
            absent.append(f"{h} is not the head of a hypothesis binder of {where}")
        elif not all(b["explicit"] for b in bs):
            absent.append(f"{h} is a binder of {where}, but not an explicit one (positions "
                          f"{[b['i'] for b in bs if not b['explicit']]})")
        elif bs[0]["kind"] not in ADMISSIBLE_KINDS:
            bad.append(f"{h} is a{'n' if bs[0]['kind'][:1] in 'aeiou' else ''} {bs[0]['kind']}, not a def or an "
                       f"inductive Prop (in {where})")
    return absent, bad


def resolve(cid: str, by_id: dict) -> str:
    """A claim id after following superseded_by (as the claims schema does), cycle-safe."""
    seen = [cid]
    while isinstance((by_id.get(seen[-1]) or {}).get("superseded_by"), str) \
            and by_id[seen[-1]]["superseded_by"] in by_id and by_id[seen[-1]]["superseded_by"] not in seen:
        seen.append(by_id[seen[-1]]["superseded_by"])
    return seen[-1]


def evaluate(claims: list[dict], fx: dict, *, stage: str, bound: str | None, comparator_ok: bool,
             credited: dict[str, set[str]], clean: dict[str, bool]) -> dict[str, dict]:
    """Proposal 0003, stage H3: judge every claim that uses `hypotheses`, `discharges` or `witness` (and every
    claim whose hypotheses a corollary discharges) from the sealed facts `fx` (`facts`). Recomputed on every
    call, never stored. `credited` {claim id: the non-smoke comparator names at the gate-bound commit that judge
    it} (empty at the gate stage); `clean` {claim id: no open, failed or unknown diagnostic so far}.

    {claim id: {"diags": [(code, detail)], "hypotheses": [{decl, state, by, via}] or None, "witness": {claim,
    state} or None}}. A hypothesis A of T is discharged only at the promote stage with a clean comparator tier,
    by a clean claim T' whose `discharges` names T (superseded_by followed) and A, when T, T' and each proof claim
    have exactly one credited Challenge name, the facts hold `hyp-corollary` T' of T dropping exactly the
    discharged names, and, for each proof P, `hyp-proof` P proves A, unless P's Challenge type quantifies over
    data (a parameterized assumption, open question 3: the corollary alone is the evidence and P is attribution).
    Anything the facts cannot settle is `hypotheses-unchecked`, never discharged."""
    from . import claims as claims_mod
    by_id = {str(c.get("id")): c for c in claims}
    out: dict[str, dict] = {}
    c12 = (bound or "")[:12] or "(no gate-bound commit)"

    def slot(cid):
        return out.setdefault(cid, {"diags": [], "hypotheses": None, "witness": None, "unchecked": []})

    def name_of(cid) -> tuple[str | None, str | None]:
        """(the one credited name, None) or (None, why not)."""
        ns = credited.get(cid) or set()
        if len(ns) == 1:
            return next(iter(ns)), None
        if not ns:
            return None, "not-compared"
        return None, f"{cid} is judged by more than one comparator name ({', '.join(sorted(ns))}): ambiguous"

    promote = stage == "promote"
    chal_why = _challenge_problem(fx) if fx.get("ok") and promote else None
    discharge_ok: dict[str, dict] = {}              # corollary id -> {target, hyps, proofs}
    witness_state: dict[str, str] = {}
    for c in claims:
        cid = str(c.get("id"))
        keys = claims_mod.uses_conditional_keys(c)
        if not keys:
            continue
        s = slot(cid)
        hyps = [h for h in c.get("hypotheses") or [] if isinstance(h, str)]
        if not fx.get("ok"):
            s["unchecked"].append(fx.get("why") or "no facts")
            continue
        decl = str(c.get("decl") or "")
        if hyps or c.get("witness") is not None:
            if fx["decl_error"]:
                s["unchecked"].append(f"the decl part of the probe did not run ({fx['decl_error'][:160]})")
            elif decl not in fx["decls"]:
                s["unchecked"].append(f"{decl} is not in the probe's decl facts"
                                      + (" (not in the built environment)" if decl in fx["missing"] else ""))
            else:
                absent, bad = _binder_problems(fx["decls"][decl], hyps, decl)
                s["diags"] += [("hypothesis-not-in-type", d) for d in absent]
                s["diags"] += [("hypothesis-inadmissible", d) for d in bad]
                if c.get("witness") is not None and fx["decls"][decl]["data_binders"] == 0:
                    s["diags"].append(("witness-type-mismatch", f"{decl} takes no parameters, so a witness shows "
                                                                f"nothing: prove its hypotheses instead"))
                    witness_state[cid] = "mismatch"
        if not promote:
            continue
        if chal_why:
            s["unchecked"].append(chal_why)
            continue
        if hyps:
            n, why = name_of(cid)
            if why and why != "not-compared":
                s["unchecked"].append(why)
            elif n and n not in fx["challenge"]:
                s["unchecked"].append(f"the Challenge theorem {n} is not in the probe's Challenge facts")
            elif n:
                for obj in fx["challenge"][n]:
                    absent, _bad = _binder_problems(obj, hyps, f"the Challenge theorem {n} ({obj['config']})")
                    s["diags"] += [("hypothesis-not-in-type", d) for d in absent if
                                   ("hypothesis-not-in-type", d) not in s["diags"]]
        d = c.get("discharges")
        if isinstance(d, dict) and isinstance(d.get("claim"), str):
            tid = resolve(d["claim"], by_id)
            dh = [h for h in d.get("hypotheses") or [] if isinstance(h, str)]
            ps = [p for p in d.get("proofs") or [] if isinstance(p, str)]
            names, missing, ambiguous = {}, [], []
            for who in [cid, tid, *ps]:
                n, why = name_of(who)
                if n:
                    names[who] = n
                elif why == "not-compared":
                    missing.append(who)
                else:
                    ambiguous.append(why)
            if ambiguous:
                s["unchecked"] += ambiguous
            elif missing:
                s["diags"].append(("discharge-not-compared",
                                   f"{', '.join(dict.fromkeys(missing))}: in no comparator configuration at {c12} "
                                   f"(not smoke); the corollary {cid}, the claim it discharges ({tid}) and each "
                                   f"proof claim must be compared in one build"))
            elif names[tid] in fx["skips"]:
                s["unchecked"].append(f"the corollary search was skipped for {names[tid]} "
                                      f"({fx['skips'][names[tid]][:160]})")
            else:
                ok = True
                got = fx["corollaries"].get((names[cid], names[tid]), [])
                if frozenset(dh) not in got:
                    ok = False
                    other = sorted(sorted(g) for g in got)
                    s["diags"].append(("discharge-type-mismatch",
                                       f"the Challenge theorem {names[cid]} is not {names[tid]}'s type with exactly "
                                       f"the binders of {dh} removed (compared strictly: no unfolding, binder order "
                                       f"and annotations kept)" + (f"; it drops {other} instead" if other else "")))
                params = {}
                for h, p in zip(dh, ps, strict=False):
                    pn = names[p]
                    if (pn, h) in fx["proofs"]:
                        params[h] = False
                    elif pn not in fx["challenge"]:
                        ok = False
                        s["unchecked"].append(f"the Challenge theorem {pn} ({p}) is not in the probe's Challenge "
                                              f"facts")
                    elif all(o["data_binders"] == 0 for o in fx["challenge"][pn]):
                        ok = False
                        s["diags"].append(("discharge-type-mismatch",
                                           f"the Challenge theorem {pn} ({p}) does not have exactly the type {h} "
                                           f"(compared strictly, no unfolding)"))
                    else:
                        params[h] = True            # quantified: a parameterized assumption, attribution only
                if ok and comparator_ok:
                    discharge_ok[cid] = {"target": tid, "hyps": dh, "proofs": dict(zip(dh, ps, strict=False))}
        w = c.get("witness")
        if isinstance(w, str) and cid not in witness_state:
            n, _why = name_of(cid)
            wn, _wwhy = name_of(w)
            if not (n and wn and comparator_ok):
                witness_state[cid] = "unchecked"
            elif any(o["data_binders"] == 0 for o in fx["challenge"].get(n, [])):
                s["diags"].append(("witness-type-mismatch", f"the Challenge theorem {n} takes no parameters, so a "
                                                            f"witness shows nothing: prove its hypotheses instead"))
                witness_state[cid] = "mismatch"
            elif (wn, n) in fx["witnesses"]:
                witness_state[cid] = "checked"
            elif wn not in fx["challenge"] or n not in fx["challenge"]:
                witness_state[cid] = "unchecked"
                s["unchecked"].append(f"the Challenge theorem {wn if wn not in fx['challenge'] else n} is not in "
                                      f"the probe's Challenge facts")
            else:
                s["diags"].append(("witness-type-mismatch",
                                   f"the Challenge theorem {wn} ({w}) is not the expected witness of {n}: ∃ over its "
                                   f"parameters, in order, of the ∧ of all its hypotheses, in order"))
                witness_state[cid] = "mismatch"

    def is_clean(cid) -> bool:
        s = out.get(cid)
        bad = s and (s["unchecked"] or any(code not in ("hypothesis-open",) for code, _ in s["diags"]))
        return bool(clean.get(cid)) and not bad

    for c in claims:                                # the hypotheses rows and hypothesis-open, from the discharges
        cid = str(c.get("id"))
        hyps = [h for h in c.get("hypotheses") or [] if isinstance(h, str)]
        if cid not in out and not hyps:
            continue
        s = slot(cid)
        if c.get("witness") is not None:
            s["witness"] = {"claim": c.get("witness"), "state": witness_state.get(cid, "unchecked")}
        if not hyps:
            continue
        rows = []
        for h in hyps:
            cands = [k for k, v in discharge_ok.items() if v["target"] == cid and h in v["hyps"]]
            good = [k for k in cands if is_clean(k) and is_clean(discharge_ok[k]["proofs"][h])]
            if good:
                k = good[0]
                rows.append({"decl": h, "state": "discharged",
                             "by": {"proof": discharge_ok[k]["proofs"][h], "corollary": k}, "via": None})
                continue
            rows.append({"decl": h, "state": "open", "by": None, "via": None})
            if s["unchecked"]:
                continue                            # unchecked says why; hypothesis-open would only repeat it
            claimed = [str(x.get("id")) for x in claims if isinstance(x.get("discharges"), dict)
                       and isinstance(x["discharges"].get("claim"), str)
                       and resolve(x["discharges"]["claim"], by_id) == cid
                       and h in (x["discharges"].get("hypotheses") or [])]
            if not promote:
                why = GATE_STAGE_OPEN
            elif not comparator_ok:
                why = f"the comparator tier is not clean at {c12}, so no discharge counts"
            elif claimed:
                why = (f"{', '.join(claimed)} claim{'s' if len(claimed) == 1 else ''} to discharge it, but the "
                       f"evidence does not establish it (see {'their' if len(claimed) > 1 else 'its'} diagnostics)")
            else:
                why = "no claim discharges it"
            s["diags"].append(("hypothesis-open", f"{h}: outstanding at {c12}; {why}"))
        s["hypotheses"] = rows
    for cid, s in out.items():
        if s["unchecked"]:
            s["diags"].append(("hypotheses-unchecked",
                               f"{cid} sets {', '.join(claims_mod.uses_conditional_keys(by_id[cid])) or 'hypotheses'}"
                               f", but the facts to judge them are missing (proposal 0003): "
                               + "; ".join(dict.fromkeys(s["unchecked"]))))
    return out

def job_sh() -> str:
    """Shell functions for a verify script: `hypotheses_run LOG DIR LEAN_PATH [SIDE_LOG]` appends the hyp-probe/1
    section to LOG (nothing when there is no claims.yaml), writing the Lean program and its scratch files under
    DIR and the Challenge build's output to SIDE_LOG (default DIR/hyp-challenge-build.log), never to LOG. It runs
    `$RUN env LEAN_PATH=... lean --run` like claim_axioms_run and `$RUN lake build` for the Challenge modules,
    reads the claims with `claims_decls` (claim_axioms.job_sh, which must come first), and never fails the job:
    a part that cannot run gives one `hyp-error` line."""
    lean = SCRIPT.read_text(encoding="utf-8")
    return ("# Conditional-claim facts read from the built environment (lean-pkg's HypProbe.lean, core Lean only;\n"
            "# proposal 0003): the `" + HEADER + "` section, one `hyp-*: {json}` line\n"
            "# per fact, then `hyp-done`. The Challenge is built into a side log, never into this log.\n"
            "hyp_json_str() {\n"
            "  printf '\"%s\"' \"$(printf '%s' \"$1\" | tr -d '\\000-\\037' | sed -e 's/\\\\/\\\\\\\\/g' "
            "-e 's/\"/\\\\\"/g')\"\n"
            "}\n"
            "hyp_error() {   # OUT PHASE EXIT FILE...: one hyp-error line, the first error line without paths\n"
            "  local out=$1 phase=$2 rc=$3 msg\n"
            "  shift 3\n"
            "  msg=$(cat \"$@\" 2>/dev/null | grep -m1 -i 'error' | sed -e 's#[^ ]*/##g' | cut -c1-200 || true)\n"
            "  echo \"hyp-error: {\\\"phase\\\":\\\"$phase\\\",\\\"message\\\":$(hyp_json_str \"exit $rc${msg:+: $msg}\")}\""
            " >>\"$out\"\n"
            "}\n"
            "hypotheses_run() {\n"
            "  [ -f claims.yaml ] || return 0\n"
            "  local dir=$2 side=${4:-$2/" + SIDE_LOG + "} pairs names mods rc cfgs=\"\" cfg m ok built=1 "
            "decls chal list=\"\"\n"
            "  mkdir -p \"$dir\"\n"
            "  local out=\"$dir/hyp.out\" part=\"$dir/hyp.part\" err=\"$dir/hyp.err\"\n"
            "  : >\"$out\"\n"
            "  cat >\"$dir/HypProbe.lean\" <<'LEAN_PKG_HYP_PROBE'\n" + lean + "LEAN_PKG_HYP_PROBE\n"
            "  pairs=$(claims_decls claims.yaml)\n"
            "  if [ -n \"$pairs\" ]; then\n"
            "    names=$(printf '%s\\n' \"$pairs\" | cut -f1)\n"
            "    mods=$(printf '%s\\n' \"$pairs\" | cut -f2 | grep '\\.lean$' | sed -e 's/\\.lean$//' -e 's|/|.|g'"
            " | sort -u || true)\n"
            "    rc=0\n"
            "    # shellcheck disable=SC2086 # one word per module and per name\n"
            "    ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/HypProbe.lean\" decl $mods -- $names >\"$part\" "
            "2>\"$err\" || rc=$?\n"
            "    if [ \"$rc\" = 0 ]; then grep -E '^hyp-[a-z-]+: ' \"$part\" >>\"$out\" || true\n"
            "    else hyp_error \"$out\" decl \"$rc\" \"$part\" \"$err\"; fi\n"
            "  fi\n"
            "  if grep -Eq '^[[:blank:]-]+(hypotheses|discharges|witness):' claims.yaml; then\n"
            "    for cfg in comparator*.json comparator/config*.json; do\n"
            "      if [ -f \"$cfg\" ]; then cfgs=\"$cfgs $cfg\"; list=\"$list${list:+,}$(hyp_json_str \"$cfg\")\"; fi\n"
            "    done\n"
            "    if [ -n \"$cfgs\" ]; then\n"
            "      rc=0\n"
            "      # shellcheck disable=SC2086 # one word per configuration\n"
            "      ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/HypProbe.lean\" modules $cfgs >\"$part\" "
            "2>\"$err\" || rc=$?\n"
            "      if [ \"$rc\" != 0 ]; then hyp_error \"$out\" challenge \"$rc\" \"$part\" \"$err\"\n"
            "      else\n"
            "        sed -n 's/^challenge-module: //p' \"$part\" >\"$dir/modules\"\n"
            "        : >\"$side\"\n"
            "        while IFS= read -r m; do\n"
            "          ok=true\n"
            "          ${RUN:-} lake build \"$m\" </dev/null >>\"$side\" 2>&1 || { ok=false; built=0; }\n"
            "          echo \"hyp-challenge-build: {\\\"module\\\":$(hyp_json_str \"$m\"),\\\"ok\\\":$ok}\" >>\"$out\"\n"
            "        done <\"$dir/modules\"\n"
            "        if [ \"$built\" = 1 ]; then\n"
            "          rc=0\n"
            "          # shellcheck disable=SC2086 # one word per configuration\n"
            "          ${RUN:-} env LEAN_PATH=\"$3\" lean --run \"$dir/HypProbe.lean\" challenge $cfgs >\"$part\" "
            "2>\"$err\" || rc=$?\n"
            "          if [ \"$rc\" = 0 ]; then grep -E '^hyp-[a-z-]+: ' \"$part\" >>\"$out\" || true\n"
            "          else hyp_error \"$out\" challenge \"$rc\" \"$part\" \"$err\"; fi\n"
            "        else\n"
            "          echo \"hyp-error: {\\\"phase\\\":\\\"challenge\\\",\\\"message\\\":\\\"a Challenge module did "
            "not build (its output: the side log " + SIDE_LOG + ")\\\"}\" >>\"$out\"\n"
            "        fi\n"
            "      fi\n"
            "    fi\n"
            "  fi\n"
            "  decls=$(grep -c -E '^hyp-(decl|missing): ' \"$out\" || true)\n"
            "  chal=$(grep -c '^hyp-challenge: ' \"$out\" || true)\n"
            "  {\n"
            "    echo \"" + HEADER + "\"\n"
            "    cat \"$out\"\n"
            "    echo \"hyp-done: {\\\"v\\\":1,\\\"decls\\\":$decls,\\\"challenge\\\":$chal,\\\"configs\\\":[$list]}\"\n"
            "  } >>\"$1\"\n"
            "}\n")


# ---------------------------------------------------------------------------------------------------------
# The local probe (tests, and an author's own check before a job)

class ProbeError(Exception):
    """The probe could not run: refused, never passed."""


def probe(repo, toolchain: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run the job's block (claim_axioms.job_sh's claims reader, then `hypotheses_run`) on a built checkout with
    its toolchain first on PATH; the section it appends, as a verify log holds it. It builds the Challenge
    modules (`lake build`, into the checkout's .lake, as the job does), so it is not sandboxed."""
    from . import claim_axioms as ca
    from . import dup_constants as dc
    repo = pathlib.Path(repo).resolve()
    try:
        _roots, prefix = dc.project_roots(repo, toolchain)
    except dc.ProbeError as e:
        raise ProbeError(str(e)) from None
    with tempfile.TemporaryDirectory(prefix="lean-pkg-hyp-") as d:
        log = pathlib.Path(d) / "verify.log"
        script = ("set -euo pipefail\n" + ca.job_sh() + job_sh()
                  + 'hypotheses_run "$1" "$2/work" "$(lake env printenv LEAN_PATH)" "$2/' + SIDE_LOG + '"\n')
        env = {**os.environ, "PATH": f"{prefix / 'bin'}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"}
        env.pop("RUN", None)
        try:
            p = subprocess.run(["bash", "-c", script, "hypotheses_run", str(log), d], cwd=repo, env=env,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                               start_new_session=True)
        except subprocess.TimeoutExpired:
            raise ProbeError("the probe did not finish in time (--timeout)") from None
        except OSError as e:
            raise ProbeError(f"cannot run the probe: {e}") from None
        if p.returncode != 0:
            raise ProbeError(f"the probe failed: {(p.stderr.strip().splitlines()[-1:] or [f'exit {p.returncode}'])[0][:400]}")
        return log.read_text(encoding="utf-8") if log.is_file() else ""


PROGRAM = "python3 -m lean_pkg.hypotheses"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=PROGRAM, formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Print the hyp-probe/1 section the lower-tier verify scripts write (proposal 0003): the "
                    "claims' Prop hypotheses read from the built environment and, when claims.yaml uses "
                    "hypotheses, discharges or witness, the Challenge facts (corollary, proof, witness). It "
                    "builds the Challenge modules, as the job does. Facts only: nothing is judged as a claim.",
        epilog="examples:\n"
               "  python3 -m lean_pkg.hypotheses --repo .\n"
               "  python3 -m lean_pkg.hypotheses --repo . --json\n"
               "exit codes: 0 the section is complete, 1 refused or the probe failed, 2 usage.\n"
               "docs: https://lean-pkg.github.io/lean-pkg/formats.html")
    ap.add_argument("--repo", required=True, metavar="DIR", help="the package checkout, a built Lake project")
    ap.add_argument("--toolchain", metavar="TC", help="default: the project's lean-toolchain")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS")
    ap.add_argument("--json", dest="json_out", action="store_true", help="the structure judgement as JSON on stdout")
    args = ap.parse_args(sys.argv[1:] if argv is None else list(argv))
    claims = pathlib.Path(args.repo) / "claims.yaml"
    if not claims.is_file():
        print(f"hypotheses: refused: {claims}: no claims.yaml (the probe reads its claims)", file=sys.stderr)
        return 1
    try:
        text = probe(args.repo, args.toolchain, args.timeout)
    except ProbeError as e:
        print(f"hypotheses: refused: {e}", file=sys.stderr)
        return 1
    summary, refusals, lines = judge_structure(text, claims.read_bytes())
    if summary is None:
        refusals = refusals + ["the probe wrote no section"]
    if args.json_out:
        print(json.dumps({"ok": not refusals, "format": FORMAT, "summary": summary, "refusals": refusals,
                          "lines": lines, "section": text.splitlines()}, indent=2))
    else:
        sys.stdout.write(text)
        for r in refusals:
            print(f"hypotheses: ✗ {r}", file=sys.stderr)
        for line in lines:
            print(f"hypotheses: {line}", file=sys.stderr)
    return 0 if not refusals else 1


if __name__ == "__main__":
    sys.exit(main())
