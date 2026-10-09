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
import pathlib
import re

from . import cli as core

SCAFFOLD_KEY = "lean_pkg_scaffold"
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


def synced_claims(pkg: dict, repo: pathlib.Path) -> list[dict]:
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
    ok, _note = core.evidence_only_descendant(repo, synced, head, core.formalization_rel(pkg))
    if not ok:
        raise core.Fail(f"{name}: claims synced at {str(synced)[:12]}, not at HEAD {head[:12]} "
                        f"(`lean-pkg claims sync {name}` first)")
    return [c for c in pkg["claims"] if isinstance(c, dict)]


def do_config_from_claims(reg: dict, name: str, out: str, dry_run: bool, namespace: str | None,
                          agent: str | None) -> tuple[list[str], dict, str | None]:
    """(lines, payload, would-be JSON text or None when nothing changes)."""
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    rel = core._safe_rel(out, "--out")
    if not core.COMPARATOR_CONFIG_RE.match(rel):
        raise core.Fail(f"--out {rel}: not a comparator configuration path (comparator*.json at the "
                        f"package root or comparator/config*.json); the runner would not find it")
    ns = namespace or default_namespace(name)
    if not NAMESPACE_RE.match(ns):
        raise core.Fail(f"--challenge-namespace {ns!r} is not a Lean name")
    claims = synced_claims(pkg, repo)
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
    for c in claims:
        decl, cid = str(c.get("decl") or ""), str(c.get("id") or "")
        if c.get("kind") not in CLAIM_KINDS or c.get("status") == "superseded" or not decl:
            skipped += 1
            continue
        if covers(configured, decl) or cid in todo_claims:
            existing += 1
            continue
        tname = f"{ns}.{decl}"
        added.append({"theorem_name": tname, "claim": cid, "decl": decl, "file": c.get("file"),
                      "challenge": f"TODO: state {tname} in {cfg.get('challenge_module') or 'Challenge'} in "
                                   f"Mathlib vocabulary, proof `sorry` (what {cid} claims, in the paper's words)",
                      "solution": f"TODO: restate {tname} in {cfg.get('solution_module') or 'Solution'} with "
                                  f"the same type and prove it from {decl} ({c.get('file')})"})
        configured.add(tname)
    counts = {"existing": existing, "added": len(added), "skipped": skipped}
    summary = (f"{name}: {len(claims)} claim(s): {existing} already configured, {len(added)} added, "
               f"{skipped} skipped (not a theorem/lemma, or superseded)")
    payload = {"ok": True, "name": name, "out": rel, "dry_run": dry_run, **counts,
               "added_names": [a["theorem_name"] for a in added], "written": False}
    if not added:
        return [summary, f"{rel}: nothing to add; nothing written"], payload, None
    cfg["theorem_names"] = list(cfg.get("theorem_names") or []) + [a["theorem_name"] for a in added]
    block.setdefault("generated_by", f"lean-pkg comparator config --from-claims {name}")
    block["note"] = ("comparator fails on each TODO name until its Challenge and Solution exist; remove an "
                     "entry from todo once both are written, and this key once todo is empty")
    block["todo"] = todo + added
    cfg[SCAFFOLD_KEY] = block
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    if dry_run:
        return [summary, f"{rel} (dry run, nothing written):"], payload, text
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
    return [summary, f"wrote {rel}: {len(added)} TODO entr{'y' if len(added) == 1 else 'ies'} under "
                     f"{SCAFFOLD_KEY}.todo (write each Challenge and Solution, then commit)"], payload, None


def cmd_comparator_config(args) -> int:
    if not args.from_claims:
        raise core.UsageError("usage: lean-pkg comparator config --from-claims NAME [--out comparator.json] "
                              "[--dry-run] [--challenge-namespace NS] [--agent A]")
    reg = core.load_registry(core.registry_path(args))
    lines, payload, text = do_config_from_claims(reg, args.from_claims, args.out or "comparator.json",
                                                 args.dry_run, args.challenge_namespace, args.agent)
    if getattr(args, "json_out", False):
        if text is not None:
            payload["config"] = json.loads(text)
        core.print_json(payload)
    else:
        print("\n".join(lines))
        if text is not None:
            print(text, end="")
    return 0
