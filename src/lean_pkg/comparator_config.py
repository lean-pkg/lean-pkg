# lean-pkg comparator config -- scaffold comparator configuration entries from claims.yaml
"""`lean-pkg comparator config --from-claims NAME [--out comparator.json] [--dry-run]` (#23.6).

A package with hundreds of claims and a handful of hand-written comparator entries needs the
rest listed before anyone can write their Challenge statements. This command adds one
`theorem_names` entry per claimed theorem or lemma that no tracked comparator configuration
covers yet, and records what a human still has to do in a `scaffold.todo` list for that configuration
in the sign-off sidecar `lean-pkg-signoff.json` (holes.py; never in the configuration itself, whose keys
stay Palomar's six). It never removes, renames or reorders an existing entry or key.

The added names are Challenge names, `<Namespace>.<decl>` (default namespace: the package name
in CamelCase plus `Audit`, as docs/comparator.md recommends). Nothing here writes the Challenge
or the Solution: until a human states each one in Mathlib vocabulary and bridges it to the
project theorem, comparator fails on the missing name, which is the intended state. The
formalization renderer leaves names still listed under `scaffold.todo` out of
`status.main_results`. A configuration still carrying the deprecated inline `lean_pkg_scaffold` key is
read as before, but not written: `--migrate-signoff PATH --write` first.

Claims come from the registry cache, which must be synced at HEAD (or HEAD an evidence-only
descendant of the synced commit): `lean-pkg claims sync NAME` first. Or (#13) from a working-copy
file, `--claims-file PATH`: schema-checked exactly like `claims check --file`, normalized the way
`claims sync` caches claims (so the generated configuration is the one the registry route would
produce for the same claims), and never written to the registry. The output names the source.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shlex

from . import gate as gate_mod
from . import core
from . import holes
from . import lean_scan

SCAFFOLD_KEY = "lean_pkg_scaffold"           # deprecated inline key; the scaffold lives in holes.SIDECAR
NOT_DONE = ["the package lease was not consulted",
            f"the checkout's comparator.json and {holes.SIDECAR} were not written (copy both files there under "
            "the lease, or re-run without --no-registry)",
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


def scaffold_todo_names(cfg: dict, rel: str | None = None, sidecar: dict | None = None) -> set[str]:
    """Theorem names a scaffold still marks TODO (left out of formalization main_results): the sidecar's
    scaffold block for `rel`, else the deprecated inline lean_pkg_scaffold key."""
    block = holes.scaffold_block(cfg, rel, sidecar)
    todo = block.get("todo") if isinstance(block, dict) else None
    return {str(t.get("theorem_name")) for t in todo or [] if isinstance(t, dict) and t.get("theorem_name")}


def _components(name: str) -> list[str] | None:
    """A Lean name's components, a «quoted» plain identifier unquoted (`«foo»` is `foo`); None when malformed."""
    parts = lean_scan.split_name(str(name).strip())
    if parts is None:
        return None
    return [p[1:-1] if p.startswith("«") and re.fullmatch(r"«[A-Za-z_][A-Za-z0-9_'!?]*»", p) else p
            for p in parts]


def qualified_decls(read_file, claims: list[dict]) -> set[str]:
    """The claims' `decl` values that are the fully qualified name of a declaration their `file` declares
    (lean_scan: `namespace`/`section`/`end` tracked, `_root_.` honoured), read with `read_file(rel)`.
    A bare or partial name (`foo` for `Demo.foo`) is not in the set: it does not say which declaration
    it means, so no prefixed configuration name can be credited to it."""
    out: set[str] = set()
    texts: dict[str, set[tuple[str, ...]]] = {}
    for c in claims:
        decl, rel = str(c.get("decl") or ""), c.get("file")
        want = _components(decl)
        if not decl or not rel or want is None:
            continue
        if rel not in texts:
            text = read_file(rel)
            found: set[tuple[str, ...]] = set()
            for row in lean_scan.declarations(text or ""):
                comps = _components(row[2])
                if comps is not None:
                    found.add(tuple(comps))
            texts[rel] = found
        if tuple(want) in texts[rel]:
            out.add(decl)
    return out


def covers(names: set[str], decl: str, qualified: bool) -> bool:
    """Does a configured comparator name cover a claim's declaration (#80 ledger, --coverage, next, the
    scaffold)? Only by the full name, and only for a declaration whose fully qualified name the claim
    states (`qualified`, from `qualified_decls`): the name IS the declaration, or it is `<Prefix>.<decl>`,
    the Challenge-namespace form the scaffold writes (`<Package>Audit.<decl>`). A shared last component
    never covers (`A.B.thm` is not `A.C.thm`), and a bare or partial `decl` is covered by nothing."""
    want = _components(decl)
    if not qualified or not want:
        return False
    for n in names:
        have = _components(n)
        if have and len(have) >= len(want) and have[-len(want):] == want:
            return True
    return False


def judged_name(claim) -> str | None:
    """A claim's explicit `judged_as` (claims/1, #CM): the comparator theorem name that judges it; None when unset
    or malformed (the claims schema check reports a malformed one)."""
    j = claim.get("judged_as") if isinstance(claim, dict) else None
    return j.strip() if isinstance(j, str) and j.strip() else None


def mapped_names(claims: list[dict]) -> set[str]:
    """Every name some claim's `judged_as` gives: such a name judges that claim only, so the naming rule never
    credits it to another claim."""
    return {j for c in claims for j in [judged_name(c)] if j}


def claim_covered(names, claim: dict, qualified: set[str], mapped: set[str] | None = None) -> bool:
    """Is `claim` judged by one of the configured `names`? With `judged_as` set: exactly when that name is
    configured, and only for a decl whose fully qualified name the claim states (`qualified`). Without it: the
    naming rule (`covers`) over the names no claim's `judged_as` takes (`mapped`, default from nothing)."""
    decl = str(claim.get("decl") or "")
    j = judged_name(claim)
    if j is not None:
        return decl in qualified and j in set(names)
    return covers(set(names) - set(mapped or ()), decl, decl in qualified)


def credited_names(names, claim: dict, qualified: set[str], mapped: set[str] | None = None) -> set[str]:
    """The configured `names` that judge `claim`, by the rule of `claim_covered` (each name alone): its
    `judged_as` when configured, else every name the naming rule credits to it. The claims ledger reads a
    conditional claim's Challenge facts under exactly one such name (proposal 0003); more than one is
    ambiguous."""
    decl = str(claim.get("decl") or "")
    j = judged_name(claim)
    if j is not None:
        return {j} if decl in qualified and j in set(names) else set()
    return {n for n in set(names) - set(mapped or ()) if covers({n}, decl, decl in qualified)}


def judged_as_problems(claims: list[dict], configured: set[str], qualified: set[str],
                       smoke: set[str] | None = None) -> list[str]:
    """What is wrong with the explicit mappings against the configurations at a commit: a `judged_as` that names
    no configured theorem (or only a theorem of a smoke configuration, `smoke`), or that is set on a claim whose
    decl is not the fully qualified name of a declaration its file declares. Each is a false statement about the
    comparator evidence, whatever the claim's status."""
    out = []
    for c in claims:
        j = judged_name(c)
        if j is None:
            continue
        if j not in configured and j in (smoke or ()):
            out.append(f"{c.get('id')}: judged_as {j!r} is a theorem of a smoke configuration only "
                       f"({holes.SIDECAR} smoke), which verifies no claim")
        elif j not in configured:
            out.append(f"{c.get('id')}: judged_as {j!r} is in no comparator configuration's theorem_names")
        elif str(c.get("decl") or "") not in qualified:
            out.append(f"{c.get('id')}: judged_as {j!r} is set, but decl {c.get('decl')!r} is not the fully qualified "
                       f"name of a declaration in {c.get('file')}")
    return out


COVER_RULE = ("a comparator theorem name covers a claim only when it ends in the claim's whole fully qualified "
              "decl: the decl itself, or <Prefix>.<decl> (FooAudit.Foo.Sec.thm covers the decl Foo.Sec.thm; "
              "FooAudit.Sec.thm, the root Foo dropped, covers nothing)")


def near_miss(name: str, decl: str) -> str | None:
    """How `name` misses `decl` by exactly its leading namespace component, or None (field report #6). Strict
    and explainable: `root-dropped` when the name is the decl without its root (`Sec.thm` for `Foo.Sec.thm`);
    `root-replaced` when the name has the decl's length and differs only in the first component
    (`FooAudit.Sec.thm` for `Foo.Sec.thm`). Every other component must agree, so a different suffix is never
    a near miss. A hint for the user, never a match: `covers` alone decides what is credited."""
    have, want = _components(name), _components(decl)
    if not have or not want or len(want) < 2:
        return None
    if have == want[1:]:
        return "root-dropped"
    if len(have) == len(want) and have[1:] == want[1:] and have[0] != want[0]:
        return "root-replaced"
    return None


def _hint(name: str, decl: str, shape: str) -> dict:
    root = _components(decl)[0]
    if shape == "root-replaced":
        pre = _components(name)[0]
        how, target = f"with its root namespace {root} replaced by {pre}", f"{pre}.{decl}"
    else:
        how, target = f"with its root namespace {root} dropped", f"<Prefix>.{decl}"
    return {"name": name, "decl": decl, "shape": shape, "rename_to": target,
            "hint": (f"{name} looks like the decl {decl} {how}, and covers nothing: the name must end in the "
                     f"whole decl, e.g. {target} (rename the Challenge and Solution theorem; never applied "
                     f"automatically)")}


def name_hints(names, decls: list[tuple[str, bool]]) -> list[dict]:
    """For each name that covers none of `decls` ((decl, qualified) pairs): the qualified decl it near-misses,
    when exactly one does (an ambiguous near miss gives no hint). [{name, decl, shape, rename_to, hint}]."""
    out = []
    for n in sorted(set(names)):
        if any(covers({n}, d, q) for d, q in decls):
            continue
        cands = sorted({(d, s) for d, q in decls if q for s in [near_miss(n, d)] if s})
        if len(cands) == 1:
            out.append(_hint(n, *cands[0]))
    return out


def decl_hint(names, decl: str, qualified: bool) -> dict | None:
    """The reverse direction (ledger, --coverage): for a qualified decl no name covers, the configured name
    that near-misses it, when exactly one does; else None."""
    if not qualified or covers(set(names), decl, qualified):
        return None
    cands = sorted({(n, s) for n in names for s in [near_miss(n, decl)] if s})
    return _hint(cands[0][0], decl, cands[0][1]) if len(cands) == 1 else None


def hint_lines(hints: list[dict], limit: int = 3, indent: str = "  ") -> list[str]:
    """Text lines for name hints: one line when every hint has the same shape and roots, then at most `limit`
    examples and a count of the rest."""
    if not hints:
        return []
    keys = {(h["shape"], _components(h["name"])[0], _components(h["decl"])[0]) for h in hints}
    out = []
    if len(hints) > 1 and len(keys) == 1:
        shape, pre, root = next(iter(keys))
        how, form = ((f"{root} replaced by {pre}", f"{pre}.{root}.<rest>") if shape == "root-replaced" else
                     (f"{root} dropped", f"<Prefix>.{root}.<rest>"))
        out.append(f"{indent}hint: all {len(hints)} look like a claim's decl with its root namespace {how}; "
                   f"rename them to {form} (the whole decl after the prefix)")
    out += [f"{indent}hint: {h['hint']}" for h in hints[:limit]]
    if len(hints) > limit:
        out.append(f"{indent}hint: ... {len(hints) - limit} more (--json lists every hint)")
    return out


def _load_out(repo: pathlib.Path, rel: str) -> dict | None:
    path = repo / rel
    if path.is_symlink():
        raise core.Fail(f"{rel} is a symlink; refusing to read or write through it")
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise core.Fail(f"{rel}: not valid JSON ({e}); nothing written") from e
    if not isinstance(cfg, dict):
        raise core.Fail(f"{rel}: not a JSON object; nothing written")
    tn = cfg.get("theorem_names", [])
    if not isinstance(tn, list) or not all(isinstance(n, str) for n in tn):
        raise core.Fail(f"{rel}: theorem_names must be a list of strings; nothing written")
    if SCAFFOLD_KEY in cfg:
        raise core.Fail(f"{rel}: carries the deprecated inline {SCAFFOLD_KEY} key (Palomar refuses it); the "
                        f"scaffold now lives in {holes.SIDECAR}: {holes.migrate_command(rel)} first; nothing written")
    return cfg


def load_sidecar(repo: pathlib.Path) -> dict:
    """The working copy's lean-pkg-signoff.json (holes.py), strictly parsed; empty when absent."""
    path = repo / holes.SIDECAR
    if path.is_symlink():
        raise core.Fail(f"{holes.SIDECAR} is a symlink; refusing to read or write through it")
    try:
        return holes.parse_sidecar(path.read_bytes() if path.is_file() else None)
    except ValueError as e:
        raise core.Fail(f"{e}; nothing written") from e


def _atomic_write(path: pathlib.Path, text: str) -> None:
    if path.is_symlink():
        raise core.Fail(f"{path.name} is a symlink; refusing to write through it")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


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
    ok, note, _docs = gate_mod.package_descendant(repo, synced, head, pkg)
    if not ok:
        if checkout:     # #31: say how this checkout differs from the commit the claims are bound to
            raise core.Fail(f"{name}: --checkout {repo}: HEAD {head[:12]} is not the claims-synced commit "
                            f"{str(synced)[:12]} or an evidence-only descendant of it ({note}); check out "
                            f"{synced} there (or `lean-pkg claims sync {name}` at this HEAD)")
        raise core.Fail(f"{name}: claims synced at {str(synced)[:12]}, not at HEAD {head[:12]} "
                        f"(`lean-pkg claims sync {name}` first)")
    return [c for c in pkg["claims"] if isinstance(c, dict)]


def file_claims(name: str, path: str) -> tuple[list[dict], dict]:
    """`--claims-file PATH` (#13): the claims of a working-copy claims.yaml, for a worker that cannot run
    `claims sync` (a registry write). Validated exactly like `claims check --file PATH` with NAME (schema,
    and the file's `package` must be NAME), then normalized as `claims sync` caches them. Reads no
    registry and writes nothing. Returns (claims, source)."""
    from .claims import load_claims_text, normalize_claim, schema_check_text
    p = pathlib.Path(str(path)).expanduser()
    if not p.is_file():
        raise core.Fail(f"{name}: --claims-file {path}: claims file not found; nothing written")
    raw = p.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    _n, errors = schema_check_text(text, str(p), name)
    if errors:
        raise core.Fail(f"{name}: --claims-file {p}: {len(errors)} schema error(s) (`lean-pkg claims check --file "
                        f"{p} {name}` lists them): " + "; ".join(errors) + "; nothing written")
    claims = [normalize_claim(c) for c in load_claims_text(text, str(p))["claims"]]
    return claims, {"kind": "file", "path": str(p.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
                    "synced_commit": None, "claims": len(claims)}


def source_line(src: dict) -> str:
    if src["kind"] == "file":
        return (f"claims source: --claims-file {src['path']} (sha256 {src['sha256']}; schema-checked like "
                f"`claims check --file`; not synced: the registry is unchanged)")
    return f"claims source: registry cache (synced at {str(src['synced_commit'])[:12]})"


def do_config_from_claims(reg: dict, name: str, out: str, dry_run: bool, namespace: str | None,
                          agent: str | None, types: dict | None = None, checkout: str | None = None,
                          no_registry: bool = False, claims_file: str | None = None
                          ) -> tuple[list[str], dict, str | None]:
    """(lines, payload, would-be JSON text or None when nothing changes).

    `types` (#25), when given: {"mode": "local"|"emit"|"ingest", "emit": F, "from_output": F,
    "lake_env": DIR, "jobs": N, "timeout": S}; see comparator_types.
    `checkout` (#31): the package checkout to use instead of the registry's local_path.
    `no_registry` (#31): scratch mode; `out` is a file outside the checkout, merged from the
    checkout's comparator.json, and the lease is not consulted.
    `claims_file` (#13): read the claims from this working-copy file instead of the synced cache
    (`file_claims`); the payload's claims_source says which was used."""
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
    rel = gate_mod._safe_rel(out, "--out")
    if not gate_mod.COMPARATOR_CONFIG_RE.match(rel):
        raise core.Fail(f"--out {rel}: not a comparator configuration path (comparator*.json at the "
                        f"package root or comparator/config*.json); the runner would not find it")
    from .comparator_receipt import cfg_name_collision
    clash = cfg_name_collision([c for c in gate_mod.head_comparator_configs(repo) if c != rel] + [rel])
    if clash:
        a, b, log = clash
        raise core.Fail(f"--out {rel}: {a} and {b} both map to the log name {log}.log, so "
                        f"comparator-receipt would refuse them; choose another --out")
    ns = namespace or default_namespace(name)
    if not NAMESPACE_RE.match(ns):
        raise core.Fail(f"--challenge-namespace {ns!r} is not a Lean name")
    if claims_file is not None:
        if core.git_head(repo) is None:
            raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
        claims, csource = file_claims(name, claims_file)
    else:
        claims = synced_claims(pkg, repo, checkout=bool(checkout))
        csource = {"kind": "registry", "path": None, "sha256": None,
                   "synced_commit": pkg.get("claims_synced_commit"), "claims": len(claims)}
    if checkout and types is not None:
        from . import comparator_types as ct
        ct.lake_dir_rel(repo, types.get("lake_env"))      # the Lake package must be there
    existing_cfg = _load_out(repo, rel)
    old_bytes = (repo / rel).read_bytes() if existing_cfg is not None else None
    cfg = dict(existing_cfg) if existing_cfg is not None else json.loads(json.dumps(NEW_CONFIG))
    sidecar = load_sidecar(repo)
    entry = dict(sidecar["configs"].get(rel) or {})
    smoke = set(holes.smoke_paths(sidecar)) | set(smoke_configs(repo, "HEAD"))
    if rel in smoke:
        raise core.Fail(f"--out {rel}: {holes.SIDECAR} marks it as a smoke check, which verifies no claim; "
                        f"scaffold the claims into another configuration (--out), or drop the smoke mark; "
                        f"nothing written")
    configured: set[str] = set(cfg.get("theorem_names") or [])
    for other in gate_mod.head_comparator_configs(repo):
        if other == rel or other in smoke:      # a smoke check's theorems configure no claim
            continue
        try:
            o = json.loads(core.git_show_bytes(repo, f"./{other}") or b"{}")
        except ValueError:
            continue
        if isinstance(o, dict) and isinstance(o.get("theorem_names"), list):
            configured |= {n for n in o["theorem_names"] if isinstance(n, str)}
    block = dict(entry.get("scaffold") or {})
    todo = list(block.get("todo") or [])
    todo_claims = {str(t.get("claim")) for t in todo if isinstance(t, dict)}
    from .claims import _reader
    qualified = qualified_decls(_reader(repo, core.git_head(repo) or "HEAD"), claims)
    existing = skipped = 0
    added: list[dict] = []
    sources: list[dict] = []
    for c in claims:
        decl, cid = str(c.get("decl") or ""), str(c.get("id") or "")
        if c.get("kind") not in CLAIM_KINDS or c.get("status") == "superseded" or not decl:
            skipped += 1
            continue
        if claim_covered(configured, c, qualified, mapped_names(claims)) or f"{ns}.{decl}" in configured \
                or cid in todo_claims:
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
               "added_names": [a["theorem_name"] for a in added], "written": False, "claims_source": csource}
    if checkout:
        payload["checkout"] = str(repo)
    if scratch:
        payload["no_registry"] = True
        payload["not_done"] = NOT_DONE
    if not added:
        return [summary, source_line(csource), f"{rel}: nothing to add; nothing written"], payload, None
    lines = [summary, source_line(csource)]
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
                    "decls": decls, "written": False, "claims_source": csource}
            elines = [summary, source_line(csource), f"{len(decls)} declaration(s) to elaborate at {head[:12]}:"] + \
                [f"  {d['decl']}  ({d['module'] or 'no module: will be a type_error'})" for d in decls]
            if dry_run:
                return elines + ["dry run: no driver written"], epay, None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ct.driver_script(cfgd), encoding="utf-8")
            path.chmod(0o755)
            epay["written"] = True
            return elines + [f"wrote {path}; run: cd {repo} && bash {path} OUT.json, then "
                             f"lean-pkg comparator config --from-claims {name} --from-output OUT.json"
                             + (f" --claims-file {shlex.quote(csource['path'])}" if csource["kind"] == "file"
                                else "")], epay, None
        if types.get("mode") == "local":
            doc, source = ct.run_local(repo, cfgd), "the local run"
        else:
            src = pathlib.Path(types["from_output"]).expanduser()
            doc, source = ct.read_output(src, name, head, types["from_output"]), types["from_output"]
        by_decl = {str(r.get("decl")): r for r in doc["results"]}
        failed = ct.check_partial([by_decl.get(a["decl"]) for a in added], doc,
                                  "the local run" if types["mode"] == "local" else f"--from-output {source}",
                                  bool(types.get("allow_partial")))
        for a, c in zip(added, sources, strict=False):
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
    entry["scaffold"] = block
    sidecar["configs"][rel] = entry
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    side_text = holes.dump_sidecar(sidecar)
    payload["signoff_sidecar"] = holes.SIDECAR
    if entry.get("definitions") and old_bytes is not None and \
            entry.get("config_sha256") == holes.sha256_bytes(old_bytes):
        lines.append(f"warning: {rel}'s definition-hole sign-off in {holes.SIDECAR} is bound to its current "
                     f"bytes (config_sha256); this change invalidates it until a human re-judges the bodies and "
                     f"sets config_sha256 to the new file's sha256")
    if scratch is not None:
        side = scratch.parent / holes.SIDECAR
        payload["sidecar_out"] = str(side)
        if dry_run:
            payload["signoff"] = json.loads(side_text)
            return lines + [f"{scratch} (dry run, nothing written):"], payload, text
        _atomic_write(side, side_text)
        _atomic_write(scratch, text)
        payload["written"] = True
        return lines + [f"wrote {scratch} (scratch: {len(added)} entr{'y' if len(added) == 1 else 'ies'} merged "
                        f"into a copy of {rel}) and {side} (its scaffold TODO markers)"] + \
            [f"not done: {n}" for n in NOT_DONE], payload, None
    if dry_run:
        payload["signoff"] = json.loads(side_text)
        return lines + [f"{rel} (dry run, nothing written):"], payload, text
    lease = pkg.get("lease")
    core.refuse_invalid_lease(name, lease)
    if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) and agent != lease.get("agent"):
        raise core.Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                        f"nothing written")
    _atomic_write(repo / holes.SIDECAR, side_text)       # the markers first: a config never lacks its TODO list
    _atomic_write(repo / rel, text)
    payload["written"] = True
    return lines + [f"wrote {rel}: {len(added)} theorem name(s), TODO entr{'y' if len(added) == 1 else 'ies'} "
                     f"under configs[{rel!r}].scaffold.todo in {holes.SIDECAR} (write each Challenge and "
                     f"Solution, then commit both files)"], payload, None


# --------------------------------------------------------------------------
# --coverage (#26.5): read-only. What the configuration tracked at the gate-bound commit covers, and why a
# changed or uncommitted configuration cannot certify that tree. Never substitutes a candidate config.

def _configs_at(repo: pathlib.Path, rev: str) -> dict[str, bytes]:
    return {c: core.git_show_bytes(repo, f"./{c}", rev) or b"" for c in gate_mod.head_comparator_configs(repo, rev)}


def _worktree_configs(repo: pathlib.Path) -> dict[str, bytes]:
    found = sorted(p for p in repo.glob("comparator*.json") if p.is_file()) + \
        sorted(p for p in (repo / "comparator").glob("config*.json") if p.is_file())
    return {p.relative_to(repo).as_posix(): p.read_bytes() for p in found}


def _sidecar_at(repo: pathlib.Path, rev: str) -> dict:
    """lean-pkg-signoff.json tracked at <rev> (holes.py); empty when not tracked; Fail when malformed."""
    try:
        return gate_mod.sidecar_at(repo, rev)
    except ValueError as e:
        raise core.Fail(str(e)) from e


def smoke_configs(repo: pathlib.Path, *revs: str | None) -> dict[str, str]:
    """{configuration path: reason} of the configurations lean-pkg-signoff.json marks as smoke checks at any
    of `revs` (the receipt's commit and HEAD, say). A mark at either commit takes credit away and nothing
    else, so the union is the safe reading: a mark committed after the receipt removes the smoke theorems'
    credit everywhere it exempts them from the coverage rule. Fail when a sidecar is malformed."""
    out: dict[str, str] = {}
    for rev in dict.fromkeys(r for r in revs if r):
        for rel, why in holes.smoke_paths(_sidecar_at(repo, rev)).items():
            out.setdefault(rel, why)
    return out


def smoke_names(configs: dict[str, bytes], smoke) -> set[str]:
    """The theorem names of the configurations among `configs` whose path is in `smoke`."""
    out: set[str] = set()
    for path, blob in configs.items():
        if path not in set(smoke or ()):
            continue
        try:
            cfg = json.loads(blob or b"{}")
        except ValueError:
            continue
        if isinstance(cfg, dict) and isinstance(cfg.get("theorem_names"), list):
            out |= {n for n in cfg["theorem_names"] if isinstance(n, str)}
    return out


def _names(configs: dict[str, bytes], sidecar: dict | None = None,
           smoke: set[str] | dict | None = None) -> tuple[set[str], set[str]]:
    """(theorem names, of which scaffold TODO names) over a set of configuration blobs; the TODO markers
    from `sidecar` (lean-pkg-signoff.json at the same commit), else a config's deprecated inline key. A
    smoke configuration (the sidecar's `smoke` mark, or a path in `smoke`) contributes no name: a smoke
    check states no result, so its theorems credit no claim."""
    names: set[str] = set()
    todo: set[str] = set()
    skip = set(holes.smoke_paths(sidecar)) | set(smoke or ())
    for path, blob in configs.items():
        if path in skip:
            continue
        try:
            cfg = json.loads(blob or b"{}")
        except ValueError:
            continue
        if isinstance(cfg, dict) and isinstance(cfg.get("theorem_names"), list):
            names |= {n for n in cfg["theorem_names"] if isinstance(n, str)}
            todo |= scaffold_todo_names(cfg, path, sidecar)
    return names, todo


def _classify(claims: list[dict], configs: dict[str, bytes], qualified: set[str],
              sidecar: dict | None = None, smoke: dict | set | None = None) -> dict:
    names, todo = _names(configs, sidecar, smoke)
    real = names - todo
    mapped = mapped_names(claims)
    smoke_thms = smoke_names(configs, set(holes.smoke_paths(sidecar)) | set(smoke or ()))
    out = {"covered": [], "todo": [], "not_covered": [], "not_applicable": 0,
           "judged_as_problems": judged_as_problems(claims, names, qualified, smoke_thms)}
    for c in claims:
        decl = str(c.get("decl") or "")
        if c.get("kind") not in CLAIM_KINDS or c.get("status") == "superseded" or not decl:
            out["not_applicable"] += 1
        elif claim_covered(real, c, qualified, mapped):
            out["covered"].append(c)
        elif claim_covered(todo, c, qualified, mapped):
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
    rec = gate_mod.recorded_json(repo, gate.get("receipt"))
    bound = str((rec or {}).get("commit") or "")
    if not core._HEX40.fullmatch(bound):
        raise core.Fail(f"{name}: no gate-bound commit (no readable gate receipt recorded); gate first: "
                        f"lean-pkg gate {name} --receipt R")
    if core.git(repo, "cat-file", "-e", f"{bound}^{{commit}}").returncode != 0:
        raise core.Fail(f"{name}: the gate-bound commit {bound[:12]} is not in {repo} (fetch it first)")
    head = core.git_head(repo) or ""
    claims, source = _claims_at(pkg, repo, bound)
    at_bound = _configs_at(repo, bound)
    from .claims import _reader
    qualified = qualified_decls(_reader(repo, bound), claims)
    side_bound = _sidecar_at(repo, bound)
    smoke = smoke_configs(repo, bound, "HEAD")
    cls = _classify(claims, at_bound, qualified, side_bound, smoke)
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
        cc_ = _classify(claims, work, qualified, load_sidecar(repo))
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
        nxt += [f"write the Challenge/Solution of each scaffold.todo entry ({holes.SIDECAR}), remove the entry, "
                f"commit, then:"] + tail
    lines = [f"{name}: coverage at the gate-bound commit {bound[:12]} (HEAD {head[:12]}); claims from {source}",
             f"  configs tracked at {bound[:12]}: {', '.join(at_bound) or 'none'}",
             f"  {props} theorem/lemma claim(s): {len(cls['covered'])} covered, {len(cls['not_covered'])} not "
             f"covered, {len(cls['todo'])} listed only as scaffold TODO; {cls['not_applicable']} other claim(s) not "
             f"compared (definitions, superseded)"]
    if cls["not_covered"]:
        lines.append("  not covered: " + ", ".join(f"{c.get('id')} ({c.get('decl')})" for c in cls["not_covered"][:10])
                     + (" ..." if len(cls["not_covered"]) > 10 else ""))
    lines += [f"  judged_as: {m}" for m in cls["judged_as_problems"]]
    configured = _names(at_bound, side_bound, smoke)[0]
    hints = [h for c in cls["not_covered"]
             for h in [decl_hint(configured, str(c.get("decl") or ""), str(c.get("decl") or "") in qualified)] if h]
    if hints:                                # #6: a configured name that misses the decl by its root namespace
        lines.append(f"  rule: {COVER_RULE}")
        lines += hint_lines(hints)
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
               "hints": hints, "judged_as_problems": cls["judged_as_problems"],
               "config_changed": bool(changed), "changed_configs": changed, "candidate": candidate,
               "statement": statement, "next_commands": nxt}
    return lines, payload


# --------------------------------------------------------------------------
# --migrate-signoff PATH [--write]: move the deprecated inline judged_as / lean_pkg_scaffold keys of one
# configuration into lean-pkg-signoff.json (holes.py). Dry run by default; never a registry write; the
# configuration's Palomar keys and their values are never changed.

PALOMAR_KEYS = ("challenge_module", "solution_module", "theorem_names", "definition_names", "permitted_axioms",
                "enable_nanoda")


def _locate(path: str) -> tuple[pathlib.Path, str, pathlib.Path]:
    """(package root, config path relative to it, the file) for a comparator configuration path."""
    f = pathlib.Path(os.path.abspath(pathlib.Path(path).expanduser()))
    if f.is_symlink():
        raise core.Fail(f"{path} is a symlink; refusing to read or write through it")
    if not f.is_file():
        raise core.Fail(f"{path}: no such comparator configuration file")
    if f.parent.name == "comparator" and holes.CONFIG_RE.match(f"comparator/{f.name}"):
        return f.parent.parent, f"comparator/{f.name}", f
    if holes.CONFIG_RE.match(f.name):
        return f.parent, f.name, f
    raise core.Fail(f"{path}: not a comparator configuration path (comparator*.json at the package root or "
                    f"comparator/config*.json)")


def do_migrate_signoff(path: str, write: bool) -> tuple[list[str], dict, str | None, str | None]:
    """(lines, payload, new config text, new sidecar text); the texts are None when nothing moves."""
    root, rel, f = _locate(path)
    raw = f.read_bytes()
    try:
        cfg = json.loads(raw.decode("utf-8"), object_pairs_hook=holes._strict_pairs)
    except (UnicodeDecodeError, ValueError) as e:
        raise core.Fail(f"{rel}: not valid strict JSON ({e}); nothing written") from e
    if not isinstance(cfg, dict):
        raise core.Fail(f"{rel}: not a JSON object; nothing written")
    before = holes.sha256_bytes(raw)
    payload = {"ok": True, "config": rel, "sidecar": holes.SIDECAR, "dry_run": not write, "written": False,
               "moved": [], "dropped": [], "config_sha256_before": before, "config_sha256_after": before,
               "palomar_keys_unchanged": True, "receipt_impact": None, "next_commands": [], "other_keys": [],
               "config_text": None, "signoff": None}        # the would-be files (dry run only)
    inline = [k for k in holes.INLINE_KEYS if k in cfg]
    if not inline:
        return [f"{rel}: no inline {' or '.join(holes.INLINE_KEYS)} key; nothing to migrate; nothing written"], \
            payload, None, None
    sidecar = load_sidecar(root)
    entry = dict(sidecar["configs"].get(rel) or {})
    new_cfg = {k: v for k, v in cfg.items() if k not in holes.INLINE_KEYS}
    new_text = json.dumps(new_cfg, indent=2, ensure_ascii=False) + "\n"
    new_bytes = new_text.encode("utf-8")
    after = holes.sha256_bytes(new_bytes)
    lines: list[str] = []
    hole_names = cfg.get("definition_names") or []
    if "judged_as" in cfg:
        if not isinstance(hole_names, list) or not all(isinstance(h, str) for h in hole_names):
            raise core.Fail(f"{rel}: definition_names must be a list of strings; nothing written")
        if hole_names:
            try:
                defs = holes._inline_check(cfg, hole_names, rel)
            except ValueError as e:
                raise core.Fail(f"{e}; fix the inline judged_as (or write the {holes.SIDECAR} entry by hand); "
                                f"nothing written") from e
            have = entry.get("definitions") or {}
            clash = sorted(h for h in defs if h in have and have[h] != defs[h])
            if clash:
                raise core.Fail(f"{rel}: {holes.SIDECAR} already signs off {', '.join(clash)} differently from the "
                                f"inline judged_as; reconcile them by hand; nothing written")
            entry["definitions"] = {**have, **defs}
            entry["config_sha256"] = after
            payload["moved"].append({"key": "judged_as", "to": f"configs[{rel!r}].definitions",
                                     "definitions": list(defs)})
            lines.append(f"judged_as -> {holes.SIDECAR} configs[{rel!r}].definitions: {len(defs)} definition "
                         f"hole(s) ({', '.join(defs)}), signed by {holes.summary({'definitions': defs})['reviewer']}; "
                         f"config_sha256 bound to the migrated file")
            listed = cfg["judged_as"].get("definitions") or []
            stale = [d for d in listed if d not in hole_names]
            if stale:
                payload["dropped"].append({"key": "judged_as.definitions", "names": stale})
                lines.append(f"judged_as.definitions {', '.join(map(str, stale))}: not a definition_names entry; "
                             f"dropped")
        else:
            payload["dropped"].append({"key": "judged_as", "names": []})
            lines.append("judged_as: the configuration has no definition holes, so there is nothing to sign off; "
                         "dropped")
    if "lean_pkg_scaffold" in cfg:
        block = cfg["lean_pkg_scaffold"]
        if not isinstance(block, dict) or not isinstance(block.get("todo", []), list):
            raise core.Fail(f"{rel}: {SCAFFOLD_KEY} must be a mapping with a todo list; nothing written")
        block = {k: v for k, v in block.items() if k in holes.SCAFFOLD_KEYS}
        block.setdefault("todo", [])
        if block["todo"]:
            if entry.get("scaffold") not in (None, block):
                raise core.Fail(f"{rel}: {holes.SIDECAR} already holds a different scaffold block for it; "
                                f"reconcile them by hand; nothing written")
            entry["scaffold"] = block
            payload["moved"].append({"key": SCAFFOLD_KEY, "to": f"configs[{rel!r}].scaffold",
                                     "todo": len(block["todo"])})
            lines.append(f"{SCAFFOLD_KEY} -> {holes.SIDECAR} configs[{rel!r}].scaffold: {len(block['todo'])} "
                         f"TODO marker(s)")
        else:
            payload["dropped"].append({"key": SCAFFOLD_KEY, "names": []})
            lines.append(f"{SCAFFOLD_KEY}: its todo list is empty; dropped")
    if entry:
        sidecar["configs"][rel] = entry
    side_text = holes.dump_sidecar(sidecar)
    try:                                   # the result must satisfy the rule it feeds
        doc = holes.parse_sidecar(side_text.encode("utf-8"))
        holes.check_definition_holes(new_cfg, rel, cfg_rel=rel, cfg_bytes=new_bytes, sidecar=doc)
    except ValueError as e:
        raise core.Fail(f"{rel}: the migrated sign-off would not pass: {e}; nothing written") from e
    assert all(new_cfg.get(k) == cfg.get(k) for k in PALOMAR_KEYS)
    others = sorted(set(new_cfg) - set(PALOMAR_KEYS))
    impact = (f"the configuration's bytes change (sha256 {before[:12]} -> {after[:12]}): a comparator receipt binds "
              f"each configuration's bytes (configs[].sha256), so once this is committed a recorded comparator "
              f"receipt no longer validates at the new HEAD; a gated package needs a new comparator receipt (and "
              f"a lower-tier receipt at the same commit)")
    payload.update(config_sha256_after=after, receipt_impact=impact, other_keys=others,
                   next_commands=[f"git -C {root} add {rel} {holes.SIDECAR} && git -C {root} commit -m "
                                  f"'comparator: move lean-pkg metadata to {holes.SIDECAR}'",
                                  f"cd {root} && comparator-receipt --prepare && comparator-receipt",
                                  "lean-pkg gate NAME --receipt R   # R: a lower-tier receipt bound to the new HEAD",
                                  "lean-pkg comparator NAME --receipt .lean-receipts/<new-HEAD-12>-comparator.json",
                                  f"lean-pkg palomar check NAME --config {rel}"])
    lines = [f"{rel}: migrate {' and '.join(inline)} to {holes.SIDECAR} (Palomar keys and values unchanged)"] + \
        lines + ([f"warning: {rel} still has key(s) outside Palomar's six: {', '.join(others)} (palomar check "
                  f"refuses them; this command moves only lean-pkg's own keys)"] if others else []) + \
        [f"receipt impact: {impact}"]
    if not write:
        payload["config_text"], payload["signoff"] = new_text, json.loads(side_text)
        return lines + ["dry run: nothing written (--write writes both files)"], payload, new_text, side_text
    _atomic_write(root / holes.SIDECAR, side_text)     # the sign-off first: the config never loses it
    _atomic_write(f, new_text)
    payload["written"] = True
    return lines + [f"wrote {holes.SIDECAR} and {rel}", "next:"] + \
        [f"  {c}" for c in payload["next_commands"]], payload, None, None


USAGE = ("usage: lean-pkg comparator config --from-claims NAME [--out comparator.json] [--dry-run] "
         "[--challenge-namespace NS] [--agent A] [--challenge-from-type | --emit-script F | --from-output F] "
         "[--allow-partial] [--repo DIR] [--claims-file PATH] [--no-registry --out FILE] "
         "[--lake-env DIR] [--jobs N] [--timeout S]  |  lean-pkg comparator coverage NAME  |  "
         "lean-pkg comparator migrate-signoff PATH [--write]")


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
    if getattr(args, "migrate_signoff", None):
        if args.from_claims or getattr(args, "coverage", None) or args.out or args.dry_run or \
                types_mode(args) is not None or args.agent or getattr(args, "checkout", None) or \
                getattr(args, "claims_file", None) or getattr(args, "no_registry", False):
            raise core.UsageError("--migrate-signoff PATH takes only --write and --json (a dry run without --write)")
        lines, payload, cfg_text, side_text = do_migrate_signoff(args.migrate_signoff, bool(args.write))
        if getattr(args, "json_out", False):
            core.print_json(payload)
        else:
            print("\n".join(lines))
            if cfg_text is not None:
                print(f"--- {payload['config']} (would be):")
                print(cfg_text, end="")
                print(f"--- {holes.SIDECAR} (would be):")
                print(side_text, end="")
        return 0
    if getattr(args, "write", False):
        raise core.UsageError("--write: only with --migrate-signoff PATH")
    if getattr(args, "coverage", None):
        if args.from_claims or args.out or args.dry_run or types_mode(args) is not None or args.agent or \
                getattr(args, "checkout", None) or getattr(args, "claims_file", None):
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
                                                 getattr(args, "checkout", None), no_registry,
                                                 getattr(args, "claims_file", None))
    if getattr(args, "json_out", False):
        if text is not None:
            payload["config"] = json.loads(text)
        core.print_json(payload)
    else:
        side = payload.pop("signoff", None)
        print("\n".join(lines))
        if text is not None:
            print(text, end="")
        if side is not None:
            print(f"--- {holes.SIDECAR} (dry run, nothing written):")
            print(holes.dump_sidecar(side), end="")
    return 0
