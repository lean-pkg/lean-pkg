# lean-pkg claims mark -- set the status of the claims a comparator configuration covers
"""`lean-pkg claims mark NAME --from-comparator CONFIG.json --status reusable [--write | --dry-run]`
(#6 field report): a package can sync hundreds of `experimental` claims and still have nothing the
claims ledger requires (`status: reusable`), so `publish` reports the ledger `unknown`. This helper sets
the status of exactly the claims whose `decl` a comparator configuration's `theorem_names` cover (the
matching rule of `comparator config --coverage` and the ledger: `comparator_config.covers`, full names
only, a fully qualified decl only), in the checkout's working copy of the claims file.

Read-only unless `--write`; `--write` edits the file in place (only the changed `status:` values; the
rest of the file, comments included, is kept byte for byte when the edit can be made that way, else the
file is re-dumped and the output says so) and never commits. Configuration names that cover no claim are
reported. Changing claims.yaml is a new commit: commit it, `claims sync`, and re-gate.

Kept in its own module so `claims.py` carries only the parser registration and the zero-reusable warning.
"""

from __future__ import annotations

import json
import pathlib

import yaml

from . import cli as core
from . import comparator_config as cc
from . import holes

MARK_STATUSES = ("reusable", "internal", "experimental")   # superseded needs superseded_by: edit by hand
NO_REUSABLE_FIX = ("mark the results others should rely on as status: reusable (`lean-pkg claims mark {name} "
                   "--from-comparator CONFIG.json --status reusable`, or edit claims.yaml)")


def no_reusable_warning(name: str, pkg: dict, claims: list | None = None) -> str | None:
    """A WARN line when a gated package (a gate receipt recorded) has claims and none is `reusable`: the
    promote-stage ledger then requires nothing, so it can never be ready."""
    claims = pkg.get("claims") if claims is None else claims
    if not claims or not (pkg.get("gate") or {}).get("receipt"):
        return None
    if any(isinstance(c, dict) and c.get("status") == "reusable" for c in claims):
        return None
    return (f"{name}: gated, {len(claims)} claim(s), none with status reusable: the claims ledger requires "
            f"nothing, so publish reports it unknown; " + NO_REUSABLE_FIX.format(name=name))


def _status_edits(text: str, changes: dict[int, str]) -> str | None:
    """`text` with the `status` scalar of claim i set to changes[i], everything else byte for byte; None when
    the file's shape does not allow that (the caller re-dumps instead)."""
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return None
    if not isinstance(root, yaml.MappingNode):
        return None
    seq = next((v for k, v in root.value if isinstance(k, yaml.ScalarNode) and k.value == "claims"), None)
    if not isinstance(seq, yaml.SequenceNode):
        return None
    spans = []
    for i, new in changes.items():
        if i >= len(seq.value) or not isinstance(seq.value[i], yaml.MappingNode):
            return None
        node = next((v for k, v in seq.value[i].value if isinstance(k, yaml.ScalarNode) and k.value == "status"),
                    None)
        if not isinstance(node, yaml.ScalarNode) or node.start_mark.line != node.end_mark.line:
            return None
        spans.append((node.start_mark.index, node.end_mark.index, new))
    out = text
    for start, end, new in sorted(spans, reverse=True):
        out = out[:start] + new + out[end:]
    return out


def do_claims_mark(reg: dict, name: str, config: str, status: str, write: bool,
                   agent: str | None = None) -> tuple[list[str], dict]:
    from . import claims as cl
    pkg = core.get_pkg(reg, name)
    if status not in MARK_STATUSES:
        raise core.Fail(f"--status must be one of {', '.join(MARK_STATUSES)} (superseded needs superseded_by: "
                        f"edit claims.yaml by hand)")
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    cpath = pathlib.Path(config).expanduser()
    cpath = cpath if cpath.is_absolute() else repo / cpath
    try:
        cfg = json.loads(cpath.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise core.Fail(f"{name}: comparator config not found: {cpath} (a relative path is read from the "
                        f"checkout {repo})")
    except ValueError as e:
        raise core.Fail(f"{cpath}: not valid JSON ({e})")
    names = cfg.get("theorem_names") if isinstance(cfg, dict) else None
    if not isinstance(names, list) or not names or not all(isinstance(n, str) and n for n in names):
        raise core.Fail(f"{cpath}: theorem_names must be a non-empty list of strings")
    try:
        crel = cpath.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        crel = None
    if status == "reusable" and crel in holes.smoke_paths(cc.load_sidecar(repo)):
        raise core.Fail(f"{crel}: {holes.SIDECAR} marks this configuration as a smoke check, which verifies no "
                        f"claim; mark claims from the configuration that judges them (docs/comparator.md, "
                        f"\"Smoke checks\")")
    rel = pkg.get("claims_file") or cl.DEFAULT_CLAIMS_FILE
    fpath = repo / rel
    if fpath.is_symlink() or not fpath.is_file():
        raise core.Fail(f"{name}: {rel} is missing (or a symbolic link) in {repo}")
    text = fpath.read_text(encoding="utf-8")
    doc = cl.load_claims_text(text, rel)
    errors = cl.validate_claims(doc, name)
    if errors:
        raise core.Fail(f"{name}: {rel} fails the claims/1 schema; fix it first: " + "; ".join(errors[:3]))
    claims = doc["claims"]

    def read(r: str) -> str | None:
        p = repo / r
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() and not p.is_symlink() else None
    qualified = cc.qualified_decls(read, claims)
    nameset = set(names)
    mapped = cc.mapped_names(claims)            # judged_as (#CM): a mapped name marks its own claim only
    matched, changes = [], {}
    for i, c in enumerate(claims):
        decl = str(c.get("decl") or "")
        if not cc.claim_covered(nameset, c, qualified, mapped):
            continue
        matched.append({"id": c.get("id"), "decl": decl, "from": c.get("status"), "to": status,
                        "changed": c.get("status") != status})
        if c.get("status") != status:
            changes[i] = status
    unmatched = sorted(n for n in nameset
                       if not any(cc.claim_covered({n}, c, qualified, mapped) for c in claims))
    lines = [f"{name}: {len(matched)} claim(s) covered by {len(nameset)} theorem name(s) of {cpath.name}; "
             f"{len(changes)} would change to status {status}" if not write else
             f"{name}: {len(matched)} claim(s) covered by {len(nameset)} theorem name(s) of {cpath.name}; "
             f"{len(changes)} set to status {status}"]
    lines += [f"  {'set' if m['changed'] else 'kept'} {m['id']} ({m['decl']}): {m['from']} -> {m['to']}"
              for m in matched]
    lines += [f"  unmatched theorem name: {n} (covers no claim's fully qualified decl)" for n in unmatched]
    hints = cc.name_hints(unmatched, [(str(c.get("decl") or ""), str(c.get("decl") or "") in qualified)
                                      for c in claims]) if unmatched else []
    if unmatched:                           # #6: say the rule, and the near misses it explains
        lines.append(f"  rule: {cc.COVER_RULE}")
        lines += cc.hint_lines(hints)
    payload = {"ok": True, "name": name, "file": rel, "config": str(config), "status": status,
               "matched": matched, "changed": len(changes), "unmatched": unmatched, "written": False,
               "reformatted": False, "rule": cc.COVER_RULE, "hints": hints}
    if not changes:
        lines.append(f"{name}: nothing to change; nothing written")
        return lines, payload
    if not write:
        lines.append(f"DRY RUN — nothing written; --write edits {rel} in the checkout (never commits)")
        return lines, payload
    lease = pkg.get("lease")
    core.refuse_invalid_lease(name, lease)
    if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) and agent != lease.get("agent"):
        raise core.Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                        f"nothing written")
    want = [dict(c, status=changes.get(i, c.get("status"))) for i, c in enumerate(claims)]
    new = _status_edits(text, changes)
    if new is None or (cl.load_claims_text(new, rel).get("claims") != want):
        new = cl.dump_claims(dict(doc, claims=want))
        payload["reformatted"] = True
        lines.append(f"  note: {rel} was re-dumped (its layout did not allow an in-place edit; comments are lost)")
    if cl.validate_claims(cl.load_claims_text(new, rel), name):
        raise core.Fail(f"{name}: the edited {rel} would fail the claims/1 schema; nothing written")
    fpath.write_text(new, encoding="utf-8")
    payload["written"] = True
    lines.append(f"wrote {rel} (NOT committed). Next: commit it, `lean-pkg claims sync {name}`, then re-gate "
                 f"(claims.yaml is bound by the gate receipt's commit)")
    return lines, payload


def run_mark(args) -> int:
    if args.write and args.dry_run:
        raise core.UsageError("--write and --dry-run are mutually exclusive")
    reg = core.load_registry(core.registry_path(args))
    out, payload = do_claims_mark(reg, args.name, args.from_comparator, args.status, bool(args.write), args.agent)
    if args.json_out:
        core.print_json(payload)
    else:
        print("\n".join(out))
    return 0


def add_parser(csub, add_json, cmd_claims) -> None:
    k = csub.add_parser("mark", help="set the status of the claims a comparator config's theorem_names cover "
                                     "(exact full-name match); read-only unless --write; never commits")
    k.add_argument("name")
    k.add_argument("--from-comparator", dest="from_comparator", required=True, metavar="CONFIG.json",
                   help="the comparator configuration whose theorem_names select the claims (relative paths are "
                        "read from the checkout)")
    k.add_argument("--status", required=True, choices=MARK_STATUSES, help="the status to set")
    k.add_argument("--write", action="store_true",
                   help="edit the claims file in the checkout (only the status values; never committed)")
    k.add_argument("--dry-run", dest="dry_run", action="store_true", help="print the changes; write nothing "
                                                                         "(the default)")
    k.add_argument("--agent", default=None, help="with --write: the lease holder, when the package is leased")
    add_json(k)
    k.set_defaults(fn=cmd_claims, claims_fn=run_mark)
