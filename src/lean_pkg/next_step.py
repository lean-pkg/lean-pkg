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
import re
import shlex
import sys

from . import cli as core

TERMINAL = ("upstreamed", "archived")
CLEAN_TREE_STATUSES = ("gate-pending", "verified-private")   # evidence is produced from HEAD here
# Subcommands that read the package checkout (a recommended one ignores `next --repo`).
READS_CHECKOUT = ("gate", "comparator", "publish", "claims", "palomar", "reservoir", "formalization",
                  "public-safety")


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
            why = core.recorded_report_log_problem(repo, obj, rel)   # gate refuses a receipt without its log
            if why:
                raise core.Fail(why)
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
                   "dirty": [], "receipt_candidate": None, "rejected_receipts": [], "comparator_configs": [],
                   "claims_ledger": None, "publication_hold": pkg.get("publication_hold")}
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
        at_head = [b for b in bad if b.get("commit") and core.package_descendant(
            repo, b["commit"], head, pkg)[0]] if bad else []
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
            # #67: one step (`gate --regate`), not `update --status gate-pending` then `gate`.
            cand, bad = lower_receipt(pkg, repo)
            state["receipt_candidate"] = cand
            state["rejected_receipts"] = bad
            prev = str(rec.get("commit") or "")
            lineage = ""
            if cand and prev and not core._is_ancestor(repo, prev, cand["commit"]):
                lineage = f" --reason \"HEAD {head[:12]} does not descend from {prev[:12]}\""
            if cand:
                crel, cerr = comparator_on_disk(pkg, repo, cand["commit"])
                comp_flag = f" --comparator {_q(repo / crel)}" if crel and not cerr else ""
                return out(f"lean-pkg gate {name} --receipt {_q(repo / cand['path'])}{comp_flag} --regate"
                           f"{lineage}{as_agent}",
                           f"the gate receipt no longer binds HEAD ({e}): re-gate the new commit in one step; "
                           f"validate first: `lean-pkg gate {name} --check --regate --receipt "
                           f"{_q(repo / cand['path'])}{comp_flag}`")
            return out(f"lean-pkg gate {name} --check --regate --receipt R",
                       f"the gate receipt no longer binds HEAD ({e}): produce a lower-tier receipt for HEAD "
                       f"{head[:12]} (checker job on HEAD), validate it with this command, then "
                       f"`lean-pkg gate {name} --receipt R --regate`")
        why = core.recorded_report_log_problem(repo, rec, gate["receipt"])
        if why:            # gate would refuse this receipt today: its sealed log is gone or differs
            rp = _q(repo / gate["receipt"])
            return blocked("invalid-receipt", f"invalid receipt {gate['receipt']}: {why}",
                           f"lean-pkg gate {name} --check --receipt {rp}",
                           f"invalid receipt: the recorded gate receipt {gate['receipt']} has no sealed log to "
                           f"check report_sha256 against: restore it (docs/receipts.md, \"Restoring preserved "
                           f"evidence\"), then validate with this command; a lost log needs a new run and "
                           f"`gate --regate`")
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
                return _ledger_step(reg, pkg, name, state, notes, out)
        crel, cerr = comparator_on_disk(pkg, repo, bound)
        if crel and not cerr:
            return out(f"lean-pkg comparator {name} --receipt {_q(repo / crel)}{as_agent}",
                       f"a comparator receipt for {bound[:12]} is on disk and validates: attach it")
        if crel and cerr:
            notes.append(f"comparator receipt {crel} not usable: {cerr}")
        if state["comparator_configs"]:
            return _comparator_step(pkg, repo, head, out, f"comparator-receipt --project {_q(repo)}",
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
            return _comparator_step(pkg, repo, head, out, nxt[0].split("   #")[0],
                       "no comparator config tracked at HEAD: scaffold one from the synced claims (committing "
                       "it moves HEAD, so re-gate afterwards: " + "; ".join(c.split("   #")[0] for c in nxt[1:3])
                       + ")")
        return out(f"lean-pkg claims sync {name}",
                   f"no comparator config tracked at HEAD and no claims to scaffold one from: sync claims.yaml, "
                   f"then `lean-pkg comparator config --from-claims {name}` (or write comparator.json by hand)")

    if status == "public":
        return _public(pkg, repo, head, out)

    return out(None, f"status {status!r}: no rule for it")


def _ledger_step(reg: dict, pkg: dict, name: str, state: dict, notes: list[str], out):
    """Both tiers bind HEAD: `promote` still refuses unless the claims ledger is ready (#80), so
    recommend `publish --dry-run` only then; otherwise the ledger's first fix."""
    if pkg.get("publication_hold"):   # #5: the owner withdrew publication; next never recommends undoing it
        return out(None, f"{core.hold_text(pkg['publication_hold'])}: both tiers bind HEAD, and nothing is "
                         f"published while the hold is set (promote refuses); lifting it is the owner's decision: "
                         f"`lean-pkg update {name} --release-publication-hold --reason TEXT`")
    from . import ledger
    led = ledger.ledger_for(pkg, "promote")
    state["claims_ledger"] = {"package_status": led["package_status"], "counts": led["counts"],
                              "required": led["required"], "ledger_digest": led["ledger_digest"]}
    if led["package_status"] == "ready":
        return out(f"lean-pkg publish {name} --dry-run",
                   f"verified-private with comparator evidence bound to HEAD and the claims ledger ready: run "
                   f"the read-only publish checklist, then `lean-pkg promote {name}` (a dry run; a human reads "
                   f"it before --yes)")
    why = ledger.summary(led)
    codes = {d["code"] for r in led["claims"] if r["required"] for d in r["diagnostics"]}
    if codes & {"no-comparator-config", "comparator-todo"}:
        try:   # the coverage producer names the scaffold step and the re-gate tail
            from . import comparator_config as cc
            nxt = cc.do_coverage(reg, name)[1].get("next_commands") or []
        except core.Fail as e:
            nxt = []
            notes.append(f"comparator config --coverage: {e}")
        if nxt:
            return _comparator_step(pkg, core.repo_path(pkg), state["head"], out, nxt[0].split("   #")[0],
                       f"promote would refuse: {why}; extend the comparator configuration to the uncovered claims "
                       f"(committing it moves HEAD, so re-gate afterwards: "
                       + "; ".join(c.split("   #")[0] for c in nxt[1:3]) + ")")
    hint = ""
    if not led["claims"]:          # nothing to verify: say how to get claims (write, migrate or sync them)
        from . import claims as claims_mod
        h = claims_mod.coverage(name, pkg).get("hint")
        hint = f"; {h}, then re-gate the commit that tracks it" if h else ""
    return out(f"lean-pkg claims ledger {name}",
               f"promote would refuse: {why}{hint}; the ledger lists each open claim and why")


def toolchain_below_floor(pkg: dict, repo: pathlib.Path, head: str | None) -> tuple[str, str] | None:
    """(toolchain, floor) when the lean-toolchain committed at HEAD (else the registry's) is below the
    comparator floor `audit` checks (below-toolchain-floor); None when it is not, or is not comparable."""
    raw = core.git_show_bytes(repo, "lean-toolchain", head) if head else None
    lines = raw.decode("utf-8", "replace").splitlines() if raw else []
    tc = (lines[0].strip() if lines else "") or str(pkg.get("lean_toolchain") or "").strip()
    try:
        floor = core.resolve_toolchain_floor(None)
    except core.UsageError:
        floor = core.DEFAULT_TOOLCHAIN_FLOOR
    key, floor_key = core.parse_lean_toolchain(tc), core.parse_lean_toolchain(floor)
    return (tc, floor) if key and floor_key and key < floor_key else None


def _comparator_step(pkg, repo, head, out, command, reason):
    """A comparator step (scaffold a config, run comparator-receipt) under the comparator toolchain floor
    is blocked first (field report): the toolchain mechanism, what Palomar runs, needs the floor."""
    below = toolchain_below_floor(pkg, repo, head)
    if below is None:
        return out(command, reason)
    tc, floor = below
    detail = f"toolchain {tc} is below the comparator floor {floor}"
    return out(None, f"{detail}: bump lean-toolchain (and Mathlib) to {floor} or later, commit, re-gate, then "
                     f"`{command}`; a legacy-mechanism comparator receipt (`comparator-receipt --mechanism "
                     f"legacy`) works below the floor but is not what Palomar runs, and `lean-pkg audit` warns "
                     f"below-toolchain-floor once comparator evidence is recorded",
               [{"kind": "below-toolchain-floor", "detail": detail, "lean_toolchain": tc, "floor": floor,
                 "deferred_command": command}])


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


def _with_repo(cmd: str, repo: str) -> str:
    """`gate --check` takes --repo too: carry it into that recommended command."""
    if cmd.startswith("lean-pkg gate ") and " --check" in cmd and " --repo " not in cmd:
        return f"{cmd} --repo {_q(repo)}"
    return cmd


def _carry_repo(payload: dict, repo: str) -> None:
    """--repo DIR is for `next` and `gate --check` only: fill it into those, and say that any other
    lean-pkg command recommended still reads the registered local_path."""
    cmd = payload.get("command")
    if cmd:
        payload["command"] = _with_repo(cmd, repo)
    payload["reason"] = re.sub(r"`(lean-pkg gate [^`]*)`", lambda m: f"`{_with_repo(m.group(1), repo)}`",
                               payload["reason"])
    words = (payload.get("command") or "").split()
    if words[:1] == ["lean-pkg"] and len(words) > 1 and words[1] in READS_CHECKOUT and "--repo" not in words:
        payload["notes"].append("the recommended command reads the registered local_path, not --repo: run it "
                                "where that checkout is current, or move the record with `lean-pkg update "
                                f"{payload['name']} --path DIR --reason TEXT`")


def cmd_next(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    if args.agent is not None:
        core.require_nonempty("--agent", args.agent)
    override = None
    if getattr(args, "repo", None) is not None:
        reg, override = core.repo_override(reg, args.name, args.repo)
        if note := core.repo_override_note(override):
            print(note, file=sys.stderr)
    payload = decide(reg, args.name, args.agent)
    payload["repo_override"] = override
    if override:
        _carry_repo(payload, override["path"])
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
    s.add_argument("--repo", default=None, metavar="DIR",
                   help="evaluate the checkout DIR instead of the registered local_path, for this call only "
                        "(never saved; stderr note, JSON repo_override): for a host whose registered path "
                        "holds another branch. DIR must be a git work tree, and when it has remotes one must "
                        "be the package's repo (Host:/p matches a local-path repo /p; the registered "
                        "local_path itself needs no remote)")
    add_json(s)
    s.set_defaults(fn=cmd_next)
