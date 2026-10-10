# lean-pkg next -- the single recommended next command for a package
"""`lean-pkg next NAME [--agent A] [--json]` (#42): read the package's registry record and its
checkout, decide the one command to run next, and say why in one line.

Read-only and offline: nothing is written, the registry is not locked, no `gh` call and no network
request is made. The decision reuses the validators the writers use (`validate_receipt`,
`validate_comparator`, `evidence_only_descendant`) and the existing next-command producers
(`comparator config --coverage`, `publish --dry-run`), so it cannot recommend a command those would
refuse for a reason this module knows about.

Exit 0 when there is a command to run (or nothing left to do), 1 when the package is blocked: a
foreign live lease (with --agent), an unparseable lease, a missing checkout, a dirty tree where
evidence is being produced, an unreadable or invalid receipt. `blockers[]` names each one.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import shlex

from . import cli as core

TERMINAL = ("upstreamed", "archived")
CLEAN_TREE_STATUSES = ("gate-pending", "verified-private")   # evidence is produced from HEAD here


def _receipt_candidates(repo: pathlib.Path) -> list[tuple[str, dict | None]]:
    """(relative path, parsed object or None) for every lower-tier receipt candidate on disk:
    .lean-receipts/*.json that is not a comparator receipt (top level only; bundles live in subdirs)."""
    d = repo / ".lean-receipts"
    out: list[tuple[str, dict | None]] = []
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        if "-comparator" in p.name:
            continue
        try:
            obj = json.loads(p.read_text())
        except (OSError, ValueError):
            obj = None
        if obj is not None and not (isinstance(obj, dict) and "report_sha256" in obj):
            continue   # some other JSON file, not a lower-tier receipt
        out.append((f".lean-receipts/{p.name}", obj))
    return out


def lower_receipt(pkg: dict, repo: pathlib.Path) -> tuple[dict | None, list[dict]]:
    """The newest lower-tier receipt on disk that `gate` would accept at HEAD, as {path, commit,
    job_id}, and the candidates it rejected [{path, error}] (unparseable, or failing validate_receipt)."""
    good: list[tuple[str, dict]] = []
    bad: list[dict] = []
    for rel, obj in _receipt_candidates(repo):
        if obj is None:
            bad.append({"path": rel, "error": "not valid JSON"})
            continue
        try:
            core.validate_receipt(pkg, repo, obj)
        except core.Fail as e:
            bad.append({"path": rel, "error": str(e), "commit": str(obj.get("commit") or "")})
            continue
        good.append((rel, obj))
    if not good:
        return None, bad
    rel, obj = max(good, key=lambda g: (str(g[1].get("generated_at")), g[0]))
    return {"path": rel, "commit": obj["commit"], "job_id": str(obj.get("job_id"))}, bad


def comparator_on_disk(pkg: dict, repo: pathlib.Path, commit: str) -> tuple[str | None, str | None]:
    """(path, None) when .lean-receipts/<commit12>-comparator.json validates against `commit` at HEAD,
    (path, error) when it exists but does not, (None, None) when there is none."""
    rel = f".lean-receipts/{commit[:12]}-comparator.json"
    obj = core.recorded_json(repo, rel)
    if obj is None:
        return (rel, "not valid JSON") if (repo / rel).exists() else (None, None)
    try:
        core.validate_comparator(pkg, repo, obj, commit)
    except core.Fail as e:
        return rel, str(e)
    return rel, None


def _lease_view(lease, now: dt.datetime) -> dict | None:
    if not lease:
        return None
    view = dict(lease) if isinstance(lease, dict) else {"raw": lease}
    exp = core.lease_expiry_or_none(lease) if isinstance(lease, dict) else None
    view["expires_at"] = exp.isoformat() if exp else None
    view["live"] = core.lease_live_or_none(lease, now) if isinstance(lease, dict) else None
    return view


def _q(s: str) -> str:
    return shlex.quote(str(s))


def decide(reg: dict, name: str, agent: str | None = None) -> dict:
    """The decision as the --json payload: {ok, name, command, reason, blockers[], notes[], state{}}."""
    pkg = core.get_pkg(reg, name)
    status = pkg.get("status")
    now = dt.datetime.now(dt.timezone.utc)
    lease = pkg.get("lease")
    state: dict = {"status": status, "visibility": pkg.get("visibility"), "lease": _lease_view(lease, now),
                   "gate_commit": None, "comparator_commit": None, "head": None, "checkout": None,
                   "dirty": [], "receipt_candidate": None, "rejected_receipts": [], "comparator_configs": []}
    notes: list[str] = []
    ag = agent or "A"
    as_agent = f" --agent {_q(agent)}" if agent else ""

    def out(command, reason, blockers=()):
        blockers = list(blockers)
        return {"ok": not blockers, "name": name, "command": command, "reason": reason,
                "blockers": blockers, "notes": notes, "state": state}

    def blocked(kind, detail, command=None, reason=None):
        return out(command, reason or detail, [{"kind": kind, "detail": detail}])

    if status in TERMINAL:
        return out(None, f"status {status}: nothing left to do (terminal status)")

    # The checkout.
    try:
        repo = core.repo_path(pkg)
    except (KeyError, TypeError, ValueError):
        repo = None
    state["checkout"] = str(repo) if repo is not None else None
    if repo is None or not repo.is_dir():
        detail = core.missing_path_detail(name, pkg, repo)
        cmd = detail.split("`")[1] if detail.count("`") >= 2 else None   # its `lean-pkg update ... --dry-run`
        return blocked("missing-checkout", f"missing checkout: {detail}", cmd)
    head = core.git_head(repo)
    state["head"] = head
    if head is None:
        return blocked("no-commit", f"{repo} is not a git checkout with a commit",
                       None, f"missing checkout: {repo} is not a git checkout with a commit; commit the "
                             f"package there (or fix local_path with `lean-pkg update {name} --path DIR "
                             f"--reason TEXT`)")
    notice = core.branch_notice(repo)
    if notice:   # #48: HEAD is still what is judged; the note only says it is not the default branch
        notes.append(notice)

    # The lease.
    if core.lease_invalid(lease):
        return blocked("invalid-lease", f"lease fields unparseable (claimed_at={lease.get('claimed_at')!r}, "
                                        f"ttl_hours={lease.get('ttl_hours')!r})",
                       f"lean-pkg release {name} --force --reason \"unparseable lease\"",
                       "lease fields are unparseable: every write refuses until it is cleared")
    live = bool(lease) and core.lease_live(lease, now)
    holder = lease.get("agent") if live else None
    if live and agent and holder != agent:
        return blocked("foreign-lease",
                       f"foreign live lease: held by {holder} (workspace {lease.get('workspace')}) until "
                       f"{core.lease_expiry(lease).isoformat()}",
                       None, f"foreign live lease: {holder} holds {name} until "
                             f"{core.lease_expiry(lease).isoformat()}; wait for its release or expiry, or "
                             f"coordinate with the holder (do not --force without a human reason)")
    if live and not agent:
        notes.append(f"leased by {holder} (workspace {lease.get('workspace')}); pass --agent A to check "
                     f"the lease is yours")
    if lease and not live:
        notes.append(f"lease of {lease.get('agent')} has expired; `claim` takes it over")
    holder_flag = f" --agent {_q(holder)}" if live else as_agent

    # Recorded evidence.
    gate = pkg.get("gate") or {}
    rec = core.recorded_json(repo, gate.get("receipt"))
    comp = core.recorded_json(repo, gate.get("comparator"))
    state["gate_commit"] = (rec or {}).get("commit") if isinstance(rec, dict) else None
    state["comparator_commit"] = (comp or {}).get("commit") if isinstance(comp, dict) else None
    state["comparator_configs"] = core.head_comparator_configs(repo)
    if status in CLEAN_TREE_STATUSES:
        state["dirty"] = core.dirty_tracked(repo)

    if status in ("scratch", "active") and not live:
        return out(f"lean-pkg claim {name} --workspace W --agent {_q(ag)} --reason \"...\"",
                   f"status {status} and no live lease: claim the package before working on it")
    if status == "scratch":
        return out(f"lean-pkg update {name} --status active --reason \"work started\"{holder_flag}",
                   "scratch and claimed: name the work (scratch -> active needs owner_project named; "
                   f"owner_project is {pkg.get('owner_project')!r})")

    if status in ("active", "gate-pending"):
        cand, bad = lower_receipt(pkg, repo)
        state["receipt_candidate"] = cand
        state["rejected_receipts"] = bad
        if status == "active":
            if cand:
                return out(f"lean-pkg update {name} --status gate-pending --reason "
                           f"\"check job {cand['job_id']} submitted\"{holder_flag}",
                           f"a lower-tier receipt bound to HEAD {head[:12]} exists ({cand['path']}): move to "
                           f"gate-pending, then gate it")
            return out(f"lean-pkg gate {name} --check --receipt R",
                       f"produce the lower-tier receipt: no receipt in .lean-receipts/ binds HEAD {head[:12]}; "
                       f"run the checker job on HEAD (R: the receipt it writes) and validate it read-only "
                       f"with this command" + (f"; rejected: {bad[0]['path']}: {bad[0]['error']}" if bad else ""))
        # gate-pending
        if state["dirty"]:
            return _dirty(name, repo, state, out)
        if cand:
            crel, cerr = comparator_on_disk(pkg, repo, cand["commit"])
            comp_flag = f" --comparator {_q(repo / crel)}" if crel and not cerr else ""
            if crel and cerr:
                notes.append(f"comparator receipt {crel} not usable: {cerr}")
            return out(f"lean-pkg gate {name} --receipt {_q(repo / cand['path'])}{comp_flag}{as_agent}",
                       f"gate-pending with a lower-tier receipt bound to HEAD {head[:12]}; validate first: "
                       f"`lean-pkg gate {name} --check --receipt {_q(repo / cand['path'])}{comp_flag}`")
        at_head = [b for b in bad if b.get("commit") and core.evidence_only_descendant(
            repo, b["commit"], head, core.formalization_rel(pkg))[0]] if bad else []
        if at_head:
            b = at_head[0]
            return blocked("invalid-receipt", f"invalid receipt {b['path']}: {b['error']}",
                           f"lean-pkg gate {name} --check --receipt {_q(repo / b['path'])}",
                           f"unreadable receipt: {b['path']} binds HEAD {head[:12]} but `gate` would refuse "
                           f"it: {b['error']}")
        return out(f"lean-pkg gate {name} --check --receipt R",
                   f"gate-pending but no lower-tier receipt in .lean-receipts/ binds HEAD {head[:12]}: produce "
                   f"it (checker job on HEAD), validate it with this command, then `lean-pkg gate {name} "
                   f"--receipt R`")

    if status == "verified-private":
        if state["dirty"]:
            return _dirty(name, repo, state, out)
        if not isinstance(rec, dict):
            return blocked("unreadable-receipt",
                           f"unreadable receipt: gate receipt {gate.get('receipt')!r} is missing or not JSON in "
                           f"{repo}", None,
                           f"unreadable receipt: gate receipt {gate.get('receipt')!r} is missing or not JSON; "
                           f"restore it from git, or re-gate (`lean-pkg update {name} --status gate-pending "
                           f"--reason TEXT`)")
        try:
            core.validate_receipt(pkg, repo, rec)
        except core.Fail as e:
            return out(f"lean-pkg update {name} --status gate-pending --reason \"re-gate HEAD {head[:12]}\""
                       f"{holder_flag}",
                       f"the gate receipt no longer binds HEAD ({e}): re-gate the new commit")
        bound = str(rec["commit"])
        if gate.get("comparator"):
            if not isinstance(comp, dict):
                return blocked("unreadable-receipt",
                               f"unreadable receipt: comparator receipt {gate.get('comparator')!r} is missing "
                               f"or not JSON in {repo}")
            try:
                core.validate_comparator(pkg, repo, comp, bound)
            except core.Fail as e:
                notes.append(f"recorded comparator receipt is not valid at HEAD: {e}")
            else:
                return out(f"lean-pkg publish {name} --dry-run",
                           f"verified-private with comparator evidence bound to HEAD: run the read-only "
                           f"publish checklist, then `lean-pkg promote {name}` (a dry run; a human reads it "
                           f"before --yes)")
        crel, cerr = comparator_on_disk(pkg, repo, bound)
        if crel and not cerr:
            return out(f"lean-pkg comparator {name} --receipt {_q(repo / crel)}{as_agent}",
                       f"a comparator receipt for {bound[:12]} is on disk and validates: attach it")
        if crel and cerr:
            notes.append(f"comparator receipt {crel} not usable: {cerr}")
        if state["comparator_configs"]:
            return out(f"comparator-receipt --project {_q(repo)}",
                       f"no comparator evidence; {len(state['comparator_configs'])} comparator config(s) "
                       f"tracked at HEAD: run comparator-receipt on a host its policy allows (`lean-pkg "
                       f"comparator host-check`), then attach the receipt with `lean-pkg comparator {name} "
                       f"--receipt {_q(repo / '.lean-receipts' / (bound[:12] + '-comparator.json'))}`")
        try:   # reuse the coverage producer: it names the scaffold step and the re-gate tail
            from . import comparator_config as cc
            _lines, cov = cc.do_coverage(reg, name)
            nxt = cov.get("next_commands") or []
        except core.Fail as e:
            nxt = []
            notes.append(f"comparator config --coverage: {e}")
        if nxt:
            return out(nxt[0].split("   #")[0],
                       "no comparator config tracked at HEAD: scaffold one from the synced claims (committing "
                       "it moves HEAD, so re-gate afterwards: " + "; ".join(c.split("   #")[0] for c in nxt[1:3])
                       + ")")
        return out(f"lean-pkg claims sync {name}",
                   f"no comparator config tracked at HEAD and no claims to scaffold one from: sync claims.yaml, "
                   f"then `lean-pkg comparator config --from-claims {name}` (or write comparator.json by hand)")

    if status == "public":
        return _public(pkg, repo, head, out)

    return out(None, f"status {status!r}: no rule for it")


def _dirty(name, repo, state, out):
    shown = ", ".join(state["dirty"][:5]) + (", ..." if len(state["dirty"]) > 5 else "")
    detail = f"dirty tree: {len(state['dirty'])} tracked file(s) modified ({shown})"
    res = out(f"git -C {_q(repo)} status --short",
              f"{detail}; evidence binds commits and comparator-receipt needs a clean tree: commit or stash "
              f"them first", [{"kind": "dirty-tree", "detail": detail}])
    return res


def _public(pkg: dict, repo: pathlib.Path, head: str, out):
    from . import publish as publish_mod
    name = pkg["name"]
    ups = core.upstream_evidence(pkg)
    if ups:
        return out(f"lean-pkg update {name} --status upstreamed --reason \"{ups[0]}\"",
                   f"upstream evidence recorded ({ups[0]}): public -> upstreamed")
    tags = publish_mod.tags_at_head(repo)
    cmds = publish_mod.next_commands(pkg, repo, True, tags)   # the publish producer's tag/record commands
    if not tags:
        return out(cmds[0].split("   #")[0], f"public, no tag at HEAD {head[:12]}: tag the release "
                                            f"(Reservoir and doi-minted need one)")
    if not pkg.get("reservoir"):
        rec = next((c for c in cmds if "reservoir-listed" in c), "")
        return out(f"lean-pkg reservoir check {name}",
                   f"public and tagged, not recorded as listed on Reservoir: check eligibility; once listed: "
                   f"`{rec.split('   #')[0]}`")
    if pkg.get("upstream_target") == "palomar" and not pkg.get("palomar"):
        mark, label = publish_mod.consent_item(pkg)
        if mark != "✓":
            return out(f"lean-pkg record {name} consent-recorded --field who=W --field role=R --field pointer=P",
                       f"upstream_target palomar: palomar prepare refuses without consent ({label})")
        prepared = [h for h in core.live_history(pkg) if h.get("event") == "palomar-prepared"
                    and str(h.get("commit") or "") == head]
        if not prepared:
            return out(f"lean-pkg palomar prepare {name}",
                       f"upstream_target palomar and no bundle prepared at HEAD {head[:12]}: build it (no "
                       f"network; submits nothing)")
        sub = next((c for c in cmds if "palomar-submitted" in c), "")
        return out(sub.split("   #")[0].replace("<40hex>", head),
                   f"bundle prepared at HEAD {head[:12]}: submit it to Palomar by hand, then record the submission")
    if pkg.get("upstream_target") == "mathlib" and not any(
            h.get("event") == "mathlib-pr-opened" for h in core.live_history(pkg)):
        return out(f"lean-pkg record {name} mathlib-pr-opened --field url=https://github.com/OWNER/REPO/pull/N",
                   "upstream_target mathlib and no Mathlib PR recorded: open it, then record its URL")
    return out(None, "public, tagged and listed; nothing to do until upstream evidence arrives "
                     "(`lean-pkg record NAME mathlib-pr-merged` / `palomar-registered`)".replace("NAME", name))


def render(payload: dict) -> list[str]:
    name, st = payload["name"], payload["state"]
    lines = [f"{name} ({st.get('status')}): " + (payload["command"] or "nothing to run")]
    for b in payload["blockers"]:
        lines.append(f"  blocked: {b['kind']}: {b['detail']}")
    lines.append(f"  why: {payload['reason']}")
    for n in payload["notes"]:
        lines.append(f"  note: {n}")
    return lines


def cmd_next(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    if args.agent is not None:
        core.require_nonempty("--agent", args.agent)
    payload = decide(reg, args.name, args.agent)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(render(payload)))
    return 0 if payload["ok"] else 1


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("next", help="read-only: the single recommended next command for a package, with a "
                                    "one-line reason (exit 0 a command to run, 1 blocked)")
    s.add_argument("name")
    s.add_argument("--agent", default=None, metavar="A",
                   help="who is asking: a live lease held by another agent blocks (exit 1); without it a live "
                        "lease is only noted; also filled into the recommended command")
    add_json(s)
    s.set_defaults(fn=cmd_next)
