# lean-pkg comparator config -- scaffold comparator configuration entries from claims.yaml
"""`lean-pkg comparator config --from-claims NAME [--out comparator.json] [--dry-run]` (#23.6).

A package with hundreds of claims and a handful of hand-written comparator entries needs the
rest listed before anyone can write their Challenge statements. This command adds one
`theorem_names` entry per claimed theorem or lemma that no tracked comparator configuration
covers yet, and records what a human still has to do in a `lean_pkg_scaffold.todo` list in the
same file. It never removes, renames or reorders an existing entry or key.

The added names are Challenge names, `<Namespace>.<decl>` (default namespace: the package name
in CamelCase plus `Audit`, as docs/comparator.md recommends). Nothing here writes the Challenge
or the Solution: until a human states each one in Mathlib vocabulary and bridges it to the
project theorem, comparator fails on the missing name, which is the intended state. The
formalization renderer leaves names still listed under `lean_pkg_scaffold.todo` out of
`status.main_results`.

Claims come from the registry cache, which must be synced at HEAD (or HEAD an evidence-only
descendant of the synced commit): `lean-pkg claims sync NAME` first.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

from . import cli as core

SCAFFOLD_KEY = "lean_pkg_scaffold"
NOT_DONE = ["the package lease was not consulted",
            "the checkout's comparator.json was not written (copy the file there under the lease, or "
            "re-run without --no-registry)",
            "nothing was written to the registry, evidence or receipts"]
CLAIM_KINDS = ("theorem", "lemma")          # propositions a comparator configuration can certify
STANDARD_AXIOMS = ["propext", "Quot.sound", "Classical.choice"]
NEW_CONFIG = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": [],
              "definition_names": [], "permitted_axioms": STANDARD_AXIOMS, "enable_nanoda": True}
NAMESPACE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*(\.[A-Za-z_][A-Za-z0-9_']*)*$")


def default_namespace(name: str) -> str:
    """`rmt-foundations` -> `RmtFoundationsAudit`."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", name) if p]
    ns = "".join(p[:1].upper() + p[1:] for p in parts) or "Package"
    return (ns if ns[0].isalpha() else f"P{ns}") + "Audit"


def scaffold_todo_names(cfg: dict) -> set[str]:
    """Theorem names a scaffold still marks TODO (left out of formalization main_results)."""
    block = cfg.get(SCAFFOLD_KEY) if isinstance(cfg, dict) else None
    todo = block.get("todo") if isinstance(block, dict) else None
    return {str(t.get("theorem_name")) for t in todo or [] if isinstance(t, dict) and t.get("theorem_name")}


def covers(names: set[str], decl: str) -> bool:
    """A configured name covers a claim when it is the declaration, ends with `.<decl>`, or has the same
    last component (a hand-written `<Ns>Audit.<short>` entry)."""
    last = decl.rsplit(".", 1)[-1]
    return any(n == decl or n.endswith("." + decl) or n.rsplit(".", 1)[-1] == last for n in names)


def _load_out(repo: pathlib.Path, rel: str) -> dict | None:
    path = repo / rel
    if path.is_symlink():
        raise core.Fail(f"{rel} is a symlink; refusing to read or write through it")
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise core.Fail(f"{rel}: not valid JSON ({e}); nothing written")
    if not isinstance(cfg, dict):
        raise core.Fail(f"{rel}: not a JSON object; nothing written")
    tn = cfg.get("theorem_names", [])
    if not isinstance(tn, list) or not all(isinstance(n, str) for n in tn):
        raise core.Fail(f"{rel}: theorem_names must be a list of strings; nothing written")
    block = cfg.get(SCAFFOLD_KEY)
    if block is not None and not (isinstance(block, dict) and isinstance(block.get("todo", []), list)):
        raise core.Fail(f"{rel}: {SCAFFOLD_KEY} must be a mapping with a todo list; nothing written")
    return cfg


def synced_claims(pkg: dict, repo: pathlib.Path, checkout: bool = False) -> list[dict]:
    name = pkg["name"]
    rel = pkg.get("claims_file") or "claims.yaml"
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    if not core.tracked_at(repo, rel):
        raise core.Fail(f"{name}: {rel} is not tracked at HEAD {head[:12]}; nothing to scaffold from "
                        f"(write claims.yaml, commit it, then `lean-pkg claims sync {name}`)")
    synced = pkg.get("claims_synced_commit")
    if not synced or not pkg.get("claims"):
        raise core.Fail(f"{name}: claims are not synced (`lean-pkg claims sync {name}` first)")
    ok, note, _docs = core.package_descendant(repo, synced, head, pkg)
    if not ok:
        if checkout:     # #31: say how this checkout differs from the commit the claims are bound to
            raise core.Fail(f"{name}: --checkout {repo}: HEAD {head[:12]} is not the claims-synced commit "
                            f"{str(synced)[:12]} or an evidence-only descendant of it ({note}); check out "
                            f"{synced} there (or `lean-pkg claims sync {name}` at this HEAD)")
        raise core.Fail(f"{name}: claims synced at {str(synced)[:12]}, not at HEAD {head[:12]} "
                        f"(`lean-pkg claims sync {name}` first)")
    return [c for c in pkg["claims"] if isinstance(c, dict)]


def do_config_from_claims(reg: dict, name: str, out: str, dry_run: bool, namespace: str | None,
                          agent: str | None, types: dict | None = None, checkout: str | None = None,
                          no_registry: bool = False) -> tuple[list[str], dict, str | None]:
    """(lines, payload, would-be JSON text or None when nothing changes).

    `types` (#25), when given: {"mode": "local"|"emit"|"ingest", "emit": F, "from_output": F,
    "lake_env": DIR, "jobs": N, "timeout": S}; see comparator_types.
    `checkout` (#31): the package checkout to use instead of the registry's local_path.
    `no_registry` (#31): scratch mode; `out` is a file outside the checkout, merged from the
    checkout's comparator.json, and the lease is not consulted."""
    pkg = core.get_pkg(reg, name)
    if checkout:
        repo = pathlib.Path(os.path.abspath(pathlib.Path(checkout).expanduser()))
        if not repo.is_dir():
            raise core.Fail(f"{name}: --checkout {checkout}: not a directory")
    else:
        repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    scratch = None
    if no_registry:
        from . import comparator_types as ct
        scratch = ct.emit_path(repo, out, "--out")
        out = "comparator.json"
    rel = core._safe_rel(out, "--out")
    if not core.COMPARATOR_CONFIG_RE.match(rel):
        raise core.Fail(f"--out {rel}: not a comparator configuration path (comparator*.json at the "
                        f"package root or comparator/config*.json); the runner would not find it")
    from .comparator_receipt import cfg_name_collision
    clash = cfg_name_collision([c for c in core.head_comparator_configs(repo) if c != rel] + [rel])
    if clash:
        a, b, log = clash
        raise core.Fail(f"--out {rel}: {a} and {b} both map to the log name {log}.log, so "
                        f"comparator-receipt would refuse them; choose another --out")
    ns = namespace or default_namespace(name)
    if not NAMESPACE_RE.match(ns):
        raise core.Fail(f"--challenge-namespace {ns!r} is not a Lean name")
    claims = synced_claims(pkg, repo, checkout=bool(checkout))
    if checkout and types is not None:
        from . import comparator_types as ct
        ct.lake_dir_rel(repo, types.get("lake_env"))      # the Lake package must be there
    existing_cfg = _load_out(repo, rel)
    cfg = dict(existing_cfg) if existing_cfg is not None else json.loads(json.dumps(NEW_CONFIG))
    configured: set[str] = set(cfg.get("theorem_names") or [])
    for other in core.head_comparator_configs(repo):
        if other == rel:
            continue
        try:
            o = json.loads(core.git_show_bytes(repo, f"./{other}") or b"{}")
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(o.get("theorem_names"), list):
            configured |= {n for n in o["theorem_names"] if isinstance(n, str)}
    block = dict(cfg.get(SCAFFOLD_KEY) or {})
    todo = list(block.get("todo") or [])
    todo_claims = {str(t.get("claim")) for t in todo if isinstance(t, dict)}
    existing = skipped = 0
    added: list[dict] = []
    sources: list[dict] = []
    for c in claims:
        decl, cid = str(c.get("decl") or ""), str(c.get("id") or "")
        if c.get("kind") not in CLAIM_KINDS or c.get("status") == "superseded" or not decl:
            skipped += 1
            continue
        if covers(configured, decl) or cid in todo_claims:
            existing += 1
            continue
        tname = f"{ns}.{decl}"
        sources.append(c)
        added.append({"theorem_name": tname, "claim": cid, "decl": decl, "file": c.get("file"),
                      "challenge": f"TODO: state {tname} in {cfg.get('challenge_module') or 'Challenge'} in "
                                   f"Mathlib vocabulary, proof `sorry` (what {cid} claims, in the paper's words)",
                      "solution": f"TODO: restate {tname} in {cfg.get('solution_module') or 'Solution'} with "
                                  f"the same type and prove it from {decl} ({c.get('file')})"})
        configured.add(tname)
    counts = {"existing": existing, "added": len(added), "skipped": skipped}
    summary = (f"{name}: {len(claims)} claim(s): {existing} already configured, {len(added)} added, "
               f"{skipped} skipped (not a theorem/lemma, or superseded)")
    payload = {"ok": True, "name": name, "out": str(scratch) if scratch else rel, "dry_run": dry_run, **counts,
               "added_names": [a["theorem_name"] for a in added], "written": False}
    if checkout:
        payload["checkout"] = str(repo)
    if scratch:
        payload["no_registry"] = True
        payload["not_done"] = NOT_DONE
    if not added:
        return [summary, f"{rel}: nothing to add; nothing written"], payload, None
    lines = [summary]
    if types is not None:
        from . import comparator_types as ct
        head = core.git_head(repo) or ""
        cfgd = None
        if types.get("mode") in ("local", "emit"):
            jobs, timeout = ct.check_jobs_timeout(types.get("jobs"), types.get("timeout"))
            cfgd = ct.driver_config(name, head, ct.lake_dir_rel(repo, types.get("lake_env")), sources, jobs, timeout)
        if types.get("mode") == "emit":
            decls = [{k: d[k] for k in ("decl", "claim", "module")} for d in cfgd["decls"]]
            path = ct.emit_path(repo, types["emit"])
            epay = {"ok": True, "name": name, "dry_run": dry_run, "script": str(path), "commit": head,
                    "decls": decls, "written": False}
            elines = [summary, f"{len(decls)} declaration(s) to elaborate at {head[:12]}:"] + \
                [f"  {d['decl']}  ({d['module'] or 'no module: will be a type_error'})" for d in decls]
            if dry_run:
                return elines + ["dry run: no driver written"], epay, None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ct.driver_script(cfgd), encoding="utf-8")
            path.chmod(0o755)
            epay["written"] = True
            return elines + [f"wrote {path}; run: cd {repo} && bash {path} OUT.json, then "
                             f"lean-pkg comparator config --from-claims {name} --from-output OUT.json"], epay, None
        if types.get("mode") == "local":
            doc, source = ct.run_local(repo, cfgd), "the local run"
        else:
            src = pathlib.Path(types["from_output"]).expanduser()
            doc, source = ct.read_output(src, name, head, types["from_output"]), types["from_output"]
        by_decl = {str(r.get("decl")): r for r in doc["results"]}
        failed = ct.check_partial([by_decl.get(a["decl"]) for a in added], doc,
                                  "the local run" if types["mode"] == "local" else f"--from-output {source}",
                                  bool(types.get("allow_partial")))
        for a, c in zip(added, sources):
            ct.fill(a, by_decl.get(a["decl"]), str(c.get("summary") or f"what {a['claim']} claims"), source)
        ok = sum(1 for a in added if "type" in a)
        nr = sum(1 for a in added if a.get("needs_restatement"))
        rtf = sum(1 for a in added if a.get("restatement_reason") == "round_trip_failed")
        payload["types"] = {"mode": types["mode"], "commit": head, "toolchain": doc.get("toolchain"),
                            "prefilled": ok, "type_errors": len(added) - ok, "needs_restatement": nr,
                            "extraction_failed": failed, "round_trip_failed": rtf, "allow_partial": bool(types.get("allow_partial")),
                            "driver_summary": doc.get("summary") if isinstance(doc.get("summary"), dict) else None}
        lines.append(f"types: {ok} pre-filled from the elaborated type, {len(added) - ok} type error(s) "
                     f"(TODO kept, {rtf} of them: printed type failed the round-trip check), "
                     f"{nr} marked needs_restatement")
        if failed:
            lines.append(f"types: --allow-partial: {failed} extraction failure(s) kept their TODO lines "
                         f"(restatement_reason extraction_failed)")
    cfg["theorem_names"] = list(cfg.get("theorem_names") or []) + [a["theorem_name"] for a in added]
    block.setdefault("generated_by", f"lean-pkg comparator config --from-claims {name}")
    block["note"] = ("comparator fails on each TODO name until its Challenge and Solution exist; remove an "
                     "entry from todo once both are written, and this key once todo is empty")
    if types is not None:
        block["types_note"] = ("challenge/solution pre-filled from the elaborated type (Lean pretty-printer); "
                               "needs_restatement: true marks entries a human must restate in Mathlib "
                               "vocabulary; type_error says why an entry kept its TODO lines")
    block["todo"] = todo + added
    cfg[SCAFFOLD_KEY] = block
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    if scratch is not None:
        if dry_run:
            return lines + [f"{scratch} (dry run, nothing written):"], payload, text
        scratch.parent.mkdir(parents=True, exist_ok=True)
        tmp = scratch.with_name(scratch.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(scratch)
        payload["written"] = True
        return lines + [f"wrote {scratch} (scratch: {len(added)} entr{'y' if len(added) == 1 else 'ies'} merged "
                        f"into a copy of {rel})"] + [f"not done: {n}" for n in NOT_DONE], payload, None
    if dry_run:
        return lines + [f"{rel} (dry run, nothing written):"], payload, text
    lease = pkg.get("lease")
    core.refuse_invalid_lease(name, lease)
    if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) and agent != lease.get("agent"):
        raise core.Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                        f"nothing written")
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    payload["written"] = True
    return lines + [f"wrote {rel}: {len(added)} TODO entr{'y' if len(added) == 1 else 'ies'} under "
                     f"{SCAFFOLD_KEY}.todo (write each Challenge and Solution, then commit)"], payload, None


# --------------------------------------------------------------------------
# --coverage (#26.5): read-only. What the configuration tracked at the gate-bound commit covers, and why a
# changed or uncommitted configuration cannot certify that tree. Never substitutes a candidate config.

def _configs_at(repo: pathlib.Path, rev: str) -> dict[str, bytes]:
    return {c: core.git_show_bytes(repo, f"./{c}", rev) or b"" for c in core.head_comparator_configs(repo, rev)}


def _worktree_configs(repo: pathlib.Path) -> dict[str, bytes]:
    found = sorted(p for p in repo.glob("comparator*.json") if p.is_file()) + \
        sorted(p for p in (repo / "comparator").glob("config*.json") if p.is_file())
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in found}


def _names(configs: dict[str, bytes]) -> tuple[set[str], set[str]]:
    """(theorem names, of which scaffold TODO names) over a set of configuration blobs."""
    names: set[str] = set()
    todo: set[str] = set()
    for blob in configs.values():
        try:
            cfg = json.loads(blob or b"{}")
        except ValueError:
            continue
        if isinstance(cfg, dict) and isinstance(cfg.get("theorem_names"), list):
            names |= {n for n in cfg["theorem_names"] if isinstance(n, str)}
            todo |= scaffold_todo_names(cfg)
    return names, todo


def _classify(claims: list[dict], configs: dict[str, bytes]) -> dict:
    names, todo = _names(configs)
    real = names - todo
    out = {"covered": [], "todo": [], "not_covered": [], "not_applicable": 0}
    for c in claims:
        decl = str(c.get("decl") or "")
        if c.get("kind") not in CLAIM_KINDS or c.get("status") == "superseded" or not decl:
            out["not_applicable"] += 1
        elif covers(real, decl):
            out["covered"].append(c)
        elif covers(todo, decl):
            out["todo"].append(c)
        else:
            out["not_covered"].append(c)
    return out


def _claims_at(pkg: dict, repo: pathlib.Path, rev: str) -> tuple[list[dict], str]:
    from .claims import load_claims_text
    rel = pkg.get("claims_file") or "claims.yaml"
    raw = core.git_show_bytes(repo, f"./{rel}", rev)
    if raw is not None:
        doc = load_claims_text(raw.decode("utf-8", errors="replace"), f"{rel} at {rev[:12]}")
        return [c for c in doc.get("claims") or [] if isinstance(c, dict)], f"{rel} at {rev[:12]}"
    synced = pkg.get("claims_synced_commit")
    if synced and pkg.get("claims"):
        return ([c for c in pkg["claims"] if isinstance(c, dict)],
                f"the registry cache (synced at {str(synced)[:12]}; {rel} is not tracked at {rev[:12]})")
    raise core.Fail(f"{pkg['name']}: no claims to measure: {rel} is not tracked at the gate-bound commit "
                    f"{rev[:12]} and no claims are cached (`lean-pkg claims sync {pkg['name']}`)")


def do_coverage(reg: dict, name: str) -> tuple[list[str], dict]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    gate = pkg.get("gate") or {}
    rec = core.recorded_json(repo, gate.get("receipt"))
    bound = str((rec or {}).get("commit") or "")
    if not core._HEX40.fullmatch(bound):
        raise core.Fail(f"{name}: no gate-bound commit (no readable gate receipt recorded); gate first: "
                        f"lean-pkg gate {name} --receipt R")
    if core.git(repo, "cat-file", "-e", f"{bound}^{{commit}}").returncode != 0:
        raise core.Fail(f"{name}: the gate-bound commit {bound[:12]} is not in {repo} (fetch it first)")
    head = core.git_head(repo) or ""
    claims, source = _claims_at(pkg, repo, bound)
    at_bound = _configs_at(repo, bound)
    cls = _classify(claims, at_bound)
    work = _worktree_configs(repo)
    at_head = _configs_at(repo, head) if head else {}
    changed: list[dict] = []
    for path in sorted(set(at_bound) | set(work) | set(at_head)):
        b, w, h = at_bound.get(path), work.get(path), at_head.get(path)
        if b == w and b == h:
            continue
        if w is None and b is not None:
            why = "removed from the working tree"
        elif b is None:
            why = "untracked (never committed)" if h is None else f"added after {bound[:12]}"
        elif h != b:
            why = f"changed at HEAD {head[:12]} since {bound[:12]}"
        else:
            why = "modified in the working tree (uncommitted)"
        changed.append({"path": path, "change": why})
    candidate = None
    if changed:
        cc_ = _classify(claims, work)
        candidate = {"configs": sorted(work), "covered": len(cc_["covered"]), "todo": len(cc_["todo"]),
                     "not_covered": len(cc_["not_covered"]), "certifies": False}
    props = len(cls["covered"]) + len(cls["todo"]) + len(cls["not_covered"])
    statement = (f"Only the comparator configuration tracked at the gate-bound commit {bound[:12]} can certify "
                 f"that tree: a comparator receipt binds the configs tracked at its commit. A changed or "
                 f"uncommitted config cannot certify {bound[:12]}; a new gate at a commit that tracks the "
                 f"config is required.")
    nxt: list[str] = []
    fz = core.formalization_rel(pkg) or "formalization.yaml"
    regate = [("lean-pkg update " + name + " --status gate-pending --reason 'comparator config changed: re-gate'")
              ] if pkg.get("status") == "verified-private" else []
    tail = regate + [
        f"lean-pkg gate {name} --receipt R" + (" --refresh-formalization" if head and core.tracked_at(repo, fz, head)
                                               else "") + "   # R: a lower-tier receipt bound to the new HEAD",
        f"cd {repo} && comparator-receipt --prepare && comparator-receipt",
        f"lean-pkg comparator {name} --receipt .lean-receipts/<new-HEAD-12>-comparator.json"]
    if changed:
        paths = " ".join(c["path"] for c in changed if "removed" not in c["change"])
        if any("working tree" in c["change"] or "untracked" in c["change"] for c in changed):
            nxt.append(f"git -C {repo} add {paths} && git -C {repo} commit -m 'comparator config'")
        nxt += tail
    elif cls["not_covered"]:
        nxt += [f"lean-pkg comparator config --from-claims {name}   # scaffold TODO entries, then write each "
                f"Challenge/Solution and commit"] + tail
    elif cls["todo"]:
        nxt += [f"write the Challenge/Solution of each lean_pkg_scaffold.todo entry, commit, then:"] + tail
    lines = [f"{name}: coverage at the gate-bound commit {bound[:12]} (HEAD {head[:12]}); claims from {source}",
             f"  configs tracked at {bound[:12]}: {', '.join(at_bound) or 'none'}",
             f"  {props} theorem/lemma claim(s): {len(cls['covered'])} covered, {len(cls['not_covered'])} not "
             f"covered, {len(cls['todo'])} listed only as scaffold TODO; {cls['not_applicable']} other claim(s) not "
             f"compared (definitions, superseded)"]
    if cls["not_covered"]:
        lines.append("  not covered: " + ", ".join(f"{c.get('id')} ({c.get('decl')})" for c in cls["not_covered"][:10])
                     + (" ..." if len(cls["not_covered"]) > 10 else ""))
    if changed:
        lines += [f"  config changed since {bound[:12]}: " + "; ".join(f"{c['path']} {c['change']}" for c in changed),
                  f"  candidate (working tree; NOT counted above, certifies nothing): {candidate['covered']} covered, "
                  f"{candidate['todo']} TODO, {candidate['not_covered']} not covered"]
    else:
        lines.append(f"  config unchanged since {bound[:12]} (HEAD and working tree)")
    lines.append(statement)
    lines += ["next:"] + [f"  {c}" for c in nxt] if nxt else ["next: nothing to do (every theorem/lemma claim "
                                                             "is covered at the gate-bound commit)"]
    payload = {"ok": True, "name": name, "bound_commit": bound, "head": head, "claims_source": source,
               "configs": sorted(at_bound), "theorem_claims": props, "covered": len(cls["covered"]),
               "not_covered": len(cls["not_covered"]), "todo": len(cls["todo"]),
               "not_applicable": cls["not_applicable"],
               "uncovered": [{"id": c.get("id"), "decl": c.get("decl")} for c in cls["not_covered"]],
               "config_changed": bool(changed), "changed_configs": changed, "candidate": candidate,
               "statement": statement, "next_commands": nxt}
    return lines, payload


USAGE = ("usage: lean-pkg comparator config --from-claims NAME [--out comparator.json] [--dry-run] "
         "[--challenge-namespace NS] [--agent A] [--challenge-from-type | --emit-script F | --from-output F] "
         "[--allow-partial] [--checkout DIR] [--no-registry --out FILE] "
         "[--lake-env DIR] [--jobs N] [--timeout S]  |  lean-pkg comparator config --coverage NAME")


def types_mode(args) -> dict | None:
    emit, frm = getattr(args, "emit_script", None), getattr(args, "from_output", None)
    local = getattr(args, "challenge_from_type", False)
    tuning = [f for f, v in (("--lake-env", getattr(args, "lake_env", None)), ("--jobs", getattr(args, "jobs", None)),
                             ("--timeout", getattr(args, "timeout", None))) if v is not None]
    if emit and frm:
        raise core.UsageError("--emit-script and --from-output are two halves of one run; pass one of them")
    if getattr(args, "no_registry", False) and not (frm or local):
        raise core.UsageError("--no-registry: needs --from-output or --challenge-from-type, and --out FILE")
    if getattr(args, "allow_partial", False) and not (frm or local):
        raise core.UsageError("--allow-partial: needs --from-output or --challenge-from-type (it governs ingestion)")
    if frm and tuning:
        raise core.UsageError(f"{', '.join(tuning)}: belong to the driver (--challenge-from-type or --emit-script), "
                              f"not to --from-output")
    if not (emit or frm or local):
        if tuning:
            raise core.UsageError(f"{', '.join(tuning)}: need --challenge-from-type or --emit-script")
        return None
    mode = "emit" if emit else "ingest" if frm else "local"
    return {"mode": mode, "emit": emit, "from_output": frm, "lake_env": getattr(args, "lake_env", None),
            "jobs": getattr(args, "jobs", None), "timeout": getattr(args, "timeout", None),
            "allow_partial": bool(getattr(args, "allow_partial", False))}


def cmd_comparator_config(args) -> int:
    if getattr(args, "coverage", None):
        if args.from_claims or args.out or args.dry_run or types_mode(args) is not None or args.agent or \
                getattr(args, "checkout", None):
            raise core.UsageError("--coverage NAME is read-only and takes no other config flag")
        lines, payload = do_coverage(core.load_registry(core.registry_path(args)), args.coverage)
        if getattr(args, "json_out", False):
            core.print_json(payload)
        else:
            print("\n".join(lines))
        return 0
    if not args.from_claims:
        raise core.UsageError(USAGE)
    types = types_mode(args)
    no_registry = bool(getattr(args, "no_registry", False))
    if no_registry and not args.out:
        raise core.UsageError("--no-registry: needs --out FILE (a scratch file outside the checkout)")
    if no_registry and args.agent:
        raise core.UsageError("--no-registry: takes no --agent (the lease is not consulted)")
    reg = core.load_registry(core.registry_path(args))
    lines, payload, text = do_config_from_claims(reg, args.from_claims, args.out or "comparator.json",
                                                 args.dry_run, args.challenge_namespace, args.agent, types,
                                                 getattr(args, "checkout", None), no_registry)
    if getattr(args, "json_out", False):
        if text is not None:
            payload["config"] = json.loads(text)
        core.print_json(payload)
    else:
        print("\n".join(lines))
        if text is not None:
            print(text, end="")
    return 0
