# lean-pkg publish -- the read-only release checklist from verified-private to listed
"""`lean-pkg publish NAME` (#22.1): one checklist for the whole path from a verified-private
package to a public, tagged, listed one, composed from checks that already exist elsewhere
(the promotion checklist, the index license check, the formalization renderer, the claims
cache, `page --check`), plus the exact next commands. Read-only (`--dry-run` says so
explicitly): nothing is written, the registry is not locked, `--commit` is ignored.

`publish NAME --yes` publishes: the promotion checklist, then the GitHub repository goes public
(the pre-1.0 `promote NAME --yes`, a permanent alias); `--yes --dry-run` runs the same checklist
and flips nothing (the pre-1.0 `promote NAME`). Tagging and `record` stay commands of their own.
"""

from __future__ import annotations

import argparse
import re
import subprocess

from . import cli, gate as gate_mod
from . import core

LAKEFILE_PACKAGE_RE = re.compile(r"^\s*package\s+(«[^»\n]+»|[A-Za-z_][A-Za-z0-9_'!?.-]*)", re.MULTILINE)
PROMOTABLE = ("verified-private",)
ALREADY_PUBLIC = ("public", "upstreamed")


# Evidence that exists on disk but is git-ignored (#40): a default `.gitignore` with `*.log` hides the
# comparator logs `promote` requires committed, and nobody notices until promote time.
STAGES_DIR = "comparator-stages"
IGNORE_REMEDY = ("un-ignore it: add `!.lean-receipts/` and `!**/.lean-receipts/**` to .gitignore after "
                 "the ignoring rule (a `!PATH` line for evidence elsewhere), or `git add -f PATH`")


def evidence_paths(pkg: dict, repo) -> list[str]:
    """The evidence files on disk that publishing needs committed: the gate receipt, the comparator
    receipt and its logs, the gate receipt's sealed verify log, and the comparator-stages/ files
    (stages.json, stage logs) kept beside them."""
    gate = pkg.get("gate") or {}
    rels = [r for r in (gate.get("receipt"), gate.get("comparator")) if isinstance(r, str) and r]
    comp = gate_mod.recorded_json(repo, gate.get("comparator"))
    if isinstance(comp, dict) and isinstance(comp.get("configs"), list):
        rels += [c["log"] for c in comp["configs"]
                 if isinstance(c, dict) and isinstance(c.get("log"), str) and c["log"]]
    lower = gate_mod.recorded_json(repo, gate.get("receipt"))
    if isinstance(lower, dict):
        rels += gate_mod.report_log_rels(repo, lower)
    dirs = {".lean-receipts"} | {r.rsplit("/", 1)[0] for r in rels if "/" in r}
    for d in sorted(dirs):
        base = repo / d
        if not base.is_dir():
            continue
        for stages in sorted(base.rglob(STAGES_DIR)):
            if stages.is_dir():
                rels += [f.relative_to(repo).as_posix() for f in sorted(stages.rglob("*")) if f.is_file()]
    seen: set[str] = set()
    return [r for r in rels if not (r in seen or seen.add(r)) and (repo / r).is_file()]


def ignored_evidence(repo, rels: list[str]) -> list[dict]:
    """[{path, rule}] for each of `rels` that git ignores (`git check-ignore -v`; tracked files are never
    ignored); rule is SOURCE:LINE:PATTERN, e.g. `.gitignore:3:*.log`. A git failure reports nothing."""
    if not rels:
        return []
    try:
        p = subprocess.run(["git", "-C", str(repo), "check-ignore", "-v", "-z", "--stdin"],
                           input="\0".join(rels) + "\0", capture_output=True, text=True,
                           env=core.scrubbed_env(), check=False)
    except OSError:
        return []
    if p.returncode not in (0, 1):
        return []
    f = p.stdout.split("\0")
    out = []
    for i in range(0, len(f) - 3, 4):
        source, line, pattern, path = f[i:i + 4]
        if pattern and not pattern.startswith("!"):   # a matched negation means "not ignored"
            out.append({"path": path, "rule": f"{source}:{line}:{pattern}"})
    return out


def ignored_evidence_item(ignored: list[dict]) -> tuple[str, str] | None:
    """The checklist WARN naming each ignored evidence path and its rule, with the remedy."""
    if not ignored:
        return None
    return ("WARN", "evidence on disk is git-ignored, so `git add` skips it: "
            + ", ".join(f"{i['path']} ({i['rule']})" for i in ignored) + f"; {IGNORE_REMEDY}")


def warn_ignored_evidence(ignored: list[dict]) -> None:
    """stderr WARN lines for `gate`/`comparator` when the evidence they attached is git-ignored."""
    import sys
    for i in ignored:
        print(f"WARN: {i['path']} is git-ignored ({i['rule']}): `git add` skips it and `publish --yes` needs it "
              f"committed; {IGNORE_REMEDY}", file=sys.stderr)


def lakefile_package_name(repo) -> str | None:
    """The Lake package name from lakefile.lean (`package NAME`) or lakefile.toml (`name = ...`)."""
    try:
        m = LAKEFILE_PACKAGE_RE.search((repo / "lakefile.lean").read_text(encoding="utf-8", errors="replace"))
        if m:
            return m.group(1).strip("«»")
    except OSError:
        pass
    try:
        import tomllib
        name = tomllib.loads((repo / "lakefile.toml").read_text(encoding="utf-8")).get("name")
        return str(name) if name else None
    except (OSError, ValueError):
        return None


def tags_at_head(repo) -> list[str]:
    p = core.git(repo, "tag", "--points-at", "HEAD")
    return sorted(t for t in p.stdout.split() if t) if p.returncode == 0 else []


def license_items(pkg: dict, repo) -> list[tuple[str, str]]:
    from . import index as index_mod
    spdx = pkg.get("license_spdx")
    if not spdx:
        return [("✗", f"license_spdx unset in the registry (`lean-pkg update {pkg['name']} --license-spdx ID`)")]
    problem = index_mod.license_problem(repo, spdx)
    if problem and problem.startswith("no license file"):
        return [("✓", f"license_spdx {spdx}")]   # the promote checklist already reports the missing file
    if problem:
        return [("✗", f"license_spdx {spdx}: {problem.replace(' at the pin', ' at the repo root')}")]
    return [("✓", f"license_spdx {spdx}; the LICENSE file matches")]


def formalization_items(pkg: dict, repo) -> list[tuple[str, str]]:
    from . import formalization as fz
    name = pkg["name"]
    rel = pkg.get("formalization_yaml")
    palomar = pkg.get("upstream_target") == "palomar"
    if not rel:
        return [("✗" if palomar else "WARN",
                 f"no formalization.yaml recorded (`lean-pkg formalization {name} --write`)"
                 + (" — Palomar reads it" if palomar else ""))]
    try:
        text, doc, _w, existing = fz.render_for(pkg, repo)
    except (core.Fail, core.UsageError) as e:
        return [("✗", f"{rel} cannot be rendered: {str(e)[:160]}")]
    mode, errors = fz.validate_doc(doc)
    if errors:
        return [("✗", f"{rel} fails the {fz.SCHEMA_VERSION} schema ({mode}): {errors[0]}")]
    if existing is None:
        return [("✗", f"{rel} is missing (`lean-pkg formalization {name} --write`)")]
    if existing != text:
        return [("✗", f"{rel} is stale: {fz.stale_hint(name, existing, text)}")]
    placeholders = fz.find_placeholders(doc)
    if placeholders:
        return [("WARN", f"{rel} is current and valid ({mode}) but has {len(placeholders)} placeholder(s): "
                         + ", ".join(placeholders[:3]))]
    return [("✓", f"{rel} is current and valid ({mode})")]


def claims_item(pkg: dict, repo, head: str | None) -> tuple[str, str]:
    rel = pkg.get("claims_file") or "claims.yaml"
    if not head or not core.tracked_at(repo, rel):
        return ("✓", f"no {rel} tracked at HEAD (nothing to sync)")
    synced = pkg.get("claims_synced_commit")
    if synced:
        ok, _note, _docs = gate_mod.package_descendant(repo, synced, head, pkg)
        if ok:
            return ("✓", f"claims synced at {str(synced)[:12]}")
    return ("✗", f"{rel} not synced at HEAD {head[:12]} (`lean-pkg claims sync {pkg['name']}`)")


def consent_item(pkg: dict) -> tuple[str, str]:
    consent = cli.consent_unique(pkg)        # one name per (who, role), however often recorded (#23.1)
    if consent:
        who = ", ".join(str(c.get("who")) for c in consent)
        return ("✓", f"consent recorded ({who})")
    return ("✗", f"no consent recorded (`lean-pkg record {pkg['name']} consent-recorded --field who=W "
                 f"--field role=R --field pointer=P`)")


def github_remote(repo, pkg: dict) -> str | None:
    """The checkout's GitHub remote, found by URL (#23.2): `origin` may be a private mirror while
    GitHub is another remote. Prefers the remote whose URL names the package's `repo`, else the
    first github.com remote (by name); None when no remote points at GitHub."""
    p = core.git(repo, "remote", "-v")
    if p.returncode != 0:
        return None
    found: dict[str, str] = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "github.com" in parts[1].lower():
            found.setdefault(parts[0], parts[1])
    want = core.gh_nwo(pkg.get("repo") or "")
    for name in sorted(found):
        if want and (core.gh_nwo(found[name]) or "").casefold() == want.casefold():
            return name
    return sorted(found)[0] if found else None


def tag_command(pkg: dict, repo) -> str:
    """The tag-and-push hint, naming the GitHub remote (#23.2)."""
    name = pkg["name"]
    tag = f"git -C {repo} tag -a vX.Y.Z -m '{name} vX.Y.Z'"
    when = "" if pkg.get("status") in ALREADY_PUBLIC else ", after publish --yes"
    remote = github_remote(repo, pkg)
    if remote:
        return f"{tag} && git -C {repo} push {remote} vX.Y.Z   # push the tag to {remote} (GitHub){when}"
    return (f"{tag}   # no GitHub remote in this checkout: add one (git remote add NAME "
            f"git@github.com:OWNER/REPO.git) and push the tag there{when}")


def next_commands(pkg: dict, repo, ready: bool, tags: list[str]) -> list[str]:
    name, status = pkg["name"], pkg.get("status")
    cmds: list[str] = []
    if not ready:
        cmds.append(f"fix every ✗ above, then: lean-pkg publish {name} --dry-run")
    if status in PROMOTABLE:
        cmds += [f"lean-pkg publish {name} --yes --dry-run", f"lean-pkg publish {name} --yes   # ONE-WAY"]
    if not tags:
        cmds.append(tag_command(pkg, repo))
    nwo = core.gh_nwo(pkg.get("repo") or "")
    if not pkg.get("reservoir"):
        url = (f"https://reservoir.lean-lang.org/@{nwo}" if nwo
               else "https://reservoir.lean-lang.org/@OWNER/NAME")
        cmds.append(f"lean-pkg record {name} reservoir-listed --field url={url}   # once Reservoir lists it")
    if pkg.get("upstream_target") == "palomar" and not pkg.get("palomar"):
        cmds.append(f"lean-pkg record {name} palomar-submitted --field commit=<40hex> "
                    f"--field submission_id=ID --field comparator_config=REL   # after submitting")
    return cmds


def do_publish_check(reg: dict, name: str, public_safety: bool = False) -> tuple[list[str], int, dict]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    status = pkg.get("status")
    items: list[tuple] = []
    ignored: list[dict] = []
    led: dict | None = None
    if status in PROMOTABLE:
        items.append(("✓", f"status {status}"))
        led = {}
        items += cli.promote_checklist(pkg, repo, ignored_sink=ignored, ledger_sink=led)
    elif status in ALREADY_PUBLIC:
        items.append(("✓", f"status {status} (promoted at {pkg.get('promoted_at') or '?'}); publish --yes not needed"))
        items.append(cli.head_pushed_item(repo))
    else:
        items.append(("✗", f"status {status}: publishing starts from verified-private "
                           f"(`lean-pkg gate {name} --receipt R` first)"))
    if status not in PROMOTABLE:   # the promote checklist already carries this WARN
        ignored = ignored_evidence(repo, evidence_paths(pkg, repo))
        item = ignored_evidence_item(ignored)
        if item:
            items.append(item)
    safety = None
    if public_safety:   # #35: private strings in the tree or the history
        from . import public_safety as ps_mod
        item, safety = ps_mod.checklist_item(pkg, repo)
        items.append(item)
    from . import public_safety as ps_ident   # #339: the addresses the flip publishes
    if (item := ps_ident.identity_item(repo)) is not None:
        items.append(item)
    items.append(consent_item(pkg))
    items += license_items(pkg, repo)
    items += formalization_items(pkg, repo)
    items.append(claims_item(pkg, repo, head))
    from . import page as page_mod
    item, page_state = page_mod.publish_item(pkg, repo)
    items.append(item)
    lake_name = lakefile_package_name(repo)
    items.append(("✓", f"lakefile package name {lake_name}") if lake_name else
                 ("✗", "no package name in lakefile.lean or lakefile.toml (Reservoir indexes the Lake package)"))
    tags = tags_at_head(repo)
    items.append(("✓", f"tag at HEAD: {', '.join(tags)}") if tags else
                 ("WARN", f"no tag at HEAD {head[:12]} (tag the release after publish --yes)"))
    from . import palomar_check   # advisory (feature request #6): a WARN item, never a ✗
    palomar = palomar_check.summary(pkg, repo, head)
    if warning := palomar_check.summary_warning(palomar):
        items.append(("WARN", warning))
    ready = not any(i[0] == "✗" for i in items)
    cmds = next_commands(pkg, repo, ready, tags)
    from . import ledger as ledger_mod
    findings = (led or {}).get("manuscript_findings") or ledger_mod.package_findings(pkg, repo)   # #6: no status
    lines = [f"publish checklist for {name} ({repo}) — dry run, nothing written:"]
    lines += [f"  {i[0]}  {i[1]}" for i in items]
    fl = ledger_mod.findings_line(findings)
    if fl:
        lines.append(fl)
    lines.append("ready: every check passes" if ready else "not ready: the checklist has failures")
    lines.append("next:")
    lines += [f"  {c}" for c in cmds]
    payload = {"ok": ready, "name": name, "dry_run": True, "ready": ready, "head": head,
               "checklist": [dict({"status": cli._MARK_TO_STATUS[i[0]], "label": i[1]},
                                  **({"reason": i[2]} if len(i) > 2 else {})) for i in items],
               "next": cmds, "ignored_evidence": ignored, "claims_ledger": led, "manuscript_findings": findings,
               "palomar_check": palomar, "page": page_state}
    if safety is not None:
        payload["public_safety"] = safety
    return lines, 0 if ready else 1, payload


def cmd_publish(args) -> int:
    if args.yes:   # the promotion: the pre-1.0 `promote NAME [--yes]`
        if args.public_safety:
            raise core.UsageError("--public-safety adds the scan to the read-only checklist; --yes always runs it "
                                  "(unless --skip-public-safety)")
        return cli.cmd_promote(argparse.Namespace(**{**vars(args), "yes": not args.dry_run}))
    extra = [f for f, on in (("--skip-public-safety", args.skip_public_safety), ("--agent", args.agent is not None))
             if on]
    if extra:
        raise core.UsageError(f"{', '.join(extra)} go(es) with --yes (the read-only checklist takes no lease and "
                              f"always lists the public-safety state with --public-safety)")
    reg_path = core.registry_path(args)
    lines, rc, payload = do_publish_check(core.load_registry(reg_path), args.name, args.public_safety)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def add_parsers(sub, add_json) -> None:
    s = sub.add_parser("publish", help="the checklist from verified-private to listed, with the exact next commands "
                                       "(read-only); --yes makes the repository public (`promote` is the same)")
    s.add_argument("name")
    s.add_argument("--yes", action="store_true",
                   help="publish: run the promotion checklist, then gh repo edit --visibility public (ONE-WAY); "
                        "first fetches origin's branches, tags and pull-request heads so the public-safety scan "
                        "covers them (a failed fetch refuses). With --dry-run: the same checklist, nothing flipped")
    s.add_argument("--dry-run", action="store_true",
                   help="write nothing (the default without --yes); with --yes: the promotion checklist only")
    s.add_argument("--public-safety", action="store_true",
                   help="read-only checklist: also scan the tree and git history for private patterns "
                        "(`lean-pkg public-safety`); --yes always scans")
    s.add_argument("--skip-public-safety", action="store_true",
                   help="with --yes: do not run the public-safety scan of the tree and history (a WARN line "
                        "instead; the scan covers the whole repository when the package is a subdirectory of it)")
    s.add_argument("--agent", default=None,
                   help="with --yes: the agent acting (recorded as the promoted event's actor); another agent's "
                        "live lease then refuses")
    add_json(s)
    s.set_defaults(fn=cmd_publish)

