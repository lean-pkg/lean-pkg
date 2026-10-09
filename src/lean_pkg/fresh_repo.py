# lean-pkg release --fresh-repo -- a single-commit public export of a package's gated tree
"""`lean-pkg release NAME --fresh-repo DIR [--remote URL] [--keep-receipts] [--source-commit SHA]
[--dry-run]` (#35): when a package's history cannot be published (it was incubated beside private
material), export its current gated tree into a NEW repository with one commit, and move the
registry entry there.

1. The source is the commit the recorded gate receipt binds (or --source-commit SHA).
2. `git archive` of that commit (the package directory only) is unpacked into a staging directory
   beside DIR, dropping the `.lean-pkg-private-patterns` file, the internal-file paths of
   `public-safety` (approvals/, notes/, drafts/, scratch/, *.priv, *.private) and `.lean-receipts/`
   (copied from the source checkout's HEAD only with --keep-receipts). Every dropped path is listed.
3. The staged tree is scanned with the package's public-safety patterns; any hit refuses, and
   nothing is written.
4. The staging directory becomes DIR: `git init`, one commit whose message records the source
   commit and the export date, an optional `origin` remote.
5. The registry entry is relocated with the `update NAME --path DIR` machinery (reason
   "fresh-history release"): the gate and comparator evidence, bound to the source commit, is
   cleared (verified-private becomes gate-pending), and a dated note keeps the old receipt paths and
   the source commit for provenance. --remote also sets the record's `repo`.

Kept receipts do not bind to the new commit: the gate and the comparator must be re-run on it.
--dry-run stages and scans in a temporary directory, prints the plan, and writes nothing.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile

from . import cli as core
from . import public_safety as ps

DEFAULT_REASON = "fresh-history release"
BRANCH = "main"
NOT_FROM = ("public", "upstreamed", "archived")


def _archive(repo: pathlib.Path, treeish: str) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", treeish], capture_output=True,
                       env=core.scrubbed_env(), check=False)
    if p.returncode != 0:
        raise core.Fail(f"git archive {treeish} failed: "
                        f"{p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return p.stdout


def _extract(data: bytes, dest: pathlib.Path, drop) -> list[dict]:
    """Unpack a tar stream into dest, skipping members drop(name) names a reason for; the dropped
    files, as [{path, reason}]."""
    import io
    dropped: list[dict] = []
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        keep = []
        for m in tf.getmembers():
            name = m.name.removeprefix("./")
            if not name or name == "pax_global_header":
                continue
            why = drop(name)
            if why:
                if not m.isdir():
                    dropped.append({"path": name, "reason": why})
                continue
            keep.append(m)
        try:
            tf.extractall(dest, members=keep, filter="data")
        except TypeError:   # a Python without extraction filters: refuse anything outside dest
            for m in keep:
                if os.path.isabs(m.name) or ".." in pathlib.PurePosixPath(m.name).parts or m.issym() \
                        or m.islnk():
                    raise core.Fail(f"refusing to unpack {m.name!r} (link or path outside the export)")
            tf.extractall(dest, members=keep)
    return dropped


def drop_reason(name: str, keep_receipts: bool) -> str | None:
    why = ps.excluded_from_export(name)
    if why:
        return why
    if name == ".lean-receipts" or name.startswith(".lean-receipts/"):
        return None if keep_receipts else ".lean-receipts/ (not copied without --keep-receipts)"
    return None


def source_commit(pkg: dict, repo: pathlib.Path, given: str | None) -> tuple[str, str]:
    """(40-hex commit, how it was chosen)."""
    name = pkg["name"]
    if given:
        sha = core._rev(repo, given)
        if not sha:
            raise core.Fail(f"{name}: --source-commit {given!r} is not a commit of {repo}")
        return sha, "--source-commit"
    rel = (pkg.get("gate") or {}).get("receipt")
    rec = core.recorded_json(repo, rel) if rel else None
    sha = str((rec or {}).get("commit") or "")
    if not rel:
        raise core.Fail(f"{name}: no gate receipt recorded, so there is no gate-bound commit to export; "
                        f"pass --source-commit SHA")
    if not core._HEX40.fullmatch(sha):
        raise core.Fail(f"{name}: the gate receipt {rel} is unreadable or names no commit; pass --source-commit SHA")
    if not core._rev(repo, sha):
        raise core.Fail(f"{name}: the gate-bound commit {sha[:12]} is not in {repo}; pass --source-commit SHA")
    return sha, f"gate receipt {rel}"


def commit_message(src: str, today: str) -> str:
    return (f"fresh-history export of {src[:12]}; receipts and gate must be re-run on this commit\n\n"
            f"source commit: {src}\nexport date: {today}\n")


def next_commands(name: str, target: pathlib.Path, new12: str, remote: str | None,
                  needs_gate_pending: bool) -> list[str]:
    cmds = [f"lean-pkg public-safety {name}   # the new repository: its tree and its one commit"]
    if remote:
        cmds.append(f"git -C {target} push -u origin {BRANCH}")
    if needs_gate_pending:
        cmds.append(f"lean-pkg update {name} --status gate-pending --reason 'check job submitted'   "
                    f"# gate needs gate-pending")
    cmds += [f"lean-pkg gate {name} --receipt R   # R: a lower-tier receipt bound to {new12}",
             f"comparator-receipt --project {target} --prepare",
             f"comparator-receipt --project {target}   # on a host its policy allows",
             f"lean-pkg comparator {name} --receipt .lean-receipts/{new12}-comparator.json",
             f"lean-pkg publish {name} --dry-run --public-safety",
             f"lean-pkg promote {name}"]
    return cmds


def do_fresh_repo(reg_path, reg, events_p, name, *, target_arg, remote, keep_receipts, given_commit,
                  agent, reason, dry_run) -> tuple[list[str], int, dict]:
    pkg = core.get_pkg(reg, name)
    status = pkg.get("status")
    if status in NOT_FROM:
        raise core.Fail(f"{name}: status {status}: a fresh-history export is for a package that has not been "
                        f"promoted (a public history is already exposed; an archived package is not published)")
    repo = core.repo_path(pkg)
    if not repo.is_dir() or core.git_head(repo) is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    if remote is not None:
        core.require_nonempty("--remote", remote)
    reason = reason or DEFAULT_REASON
    src, chosen = source_commit(pkg, repo, given_commit)
    stored, _key = core.normalize_local_path(target_arg, "--fresh-repo")
    target = core.HOME / pathlib.Path(stored)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise core.Fail(f"{name}: --fresh-repo {target} exists and is not an empty directory; refusing")
    top = core.git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if top and (target == pathlib.Path(top) or pathlib.Path(top) in target.parents):
        raise core.Fail(f"{name}: --fresh-repo {target} lies inside the source repository {top}; choose a "
                        f"directory outside it")
    if not dry_run and core.git(repo, "var", "GIT_COMMITTER_IDENT").returncode != 0:
        raise core.Fail(f"{name}: git has no committer identity here (git config user.name / user.email); "
                        f"the export commit needs one")
    # the relocation's own refusals (lease, another package's path), before anything is written
    _o, preview = core.do_relocate(reg_path, reg, events_p, name, new_path=stored, reason=reason,
                                   agent=agent, redetect=False, dry_run=True)

    prefix = core.git(repo, "rev-parse", "--show-prefix").stdout.strip()
    treeish = f"{src}:{prefix.rstrip('/')}" if prefix else src
    if dry_run:
        stage = pathlib.Path(tempfile.mkdtemp(prefix="lean-pkg-export-"))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = pathlib.Path(tempfile.mkdtemp(prefix=".lean-pkg-export-", dir=target.parent))
    moved = False
    try:
        dropped = _extract(_archive(repo, treeish), stage, lambda n: drop_reason(n, keep_receipts))
        receipts_note = None
        if keep_receipts:
            rel = f"{prefix}.lean-receipts"
            if core.tracked_at(repo, ".lean-receipts"):
                (stage / ".lean-receipts").mkdir(exist_ok=True)
                _extract(_archive(repo, f"HEAD:{rel}"), stage / ".lean-receipts", lambda n: None)
                receipts_note = (f".lean-receipts/ copied from the source HEAD: bound to {src[:12]} or earlier, "
                                 f"they do NOT bind to the new commit")
            else:
                receipts_note = "--keep-receipts: no .lean-receipts/ tracked at the source HEAD; nothing copied"
        result = ps.scan(pkg, stage, export=True, patterns_root=repo, source_repo=repo)
        if result["hits"]:
            lines = [ps.hit_line(h) for h in result["hits"][:20]]
            raise core.Fail(f"{name}: the exported tree of {src[:12]} has {len(result['hits'])} public-safety "
                            f"hit(s); nothing written:\n" + "\n".join(lines)
                            + "\nfix them at the source (commit, then --source-commit SHA), or move them under "
                              "an internal path (notes/, drafts/, ...) so the export drops them")
        files = sum(1 for _ in (p for p in stage.rglob("*") if p.is_file()))
        today = core.utc_today()
        msg = commit_message(src, today)
        plan = [f"{name}: fresh-history export of {src[:12]} ({chosen}) -> {target}",
                f"  {files} file(s) exported; {len(dropped)} dropped"]
        plan += [f"  dropped: {d['path']}  ({d['reason']})" for d in dropped]
        if receipts_note:
            plan.append(f"  note: {receipts_note}")
        plan.append("  public-safety on the exported tree: 0 hit(s)")
        payload = {"ok": True, "name": name, "dry_run": bool(dry_run), "source_commit": src,
                   "source": chosen, "export_path": stored, "files": files, "excluded": dropped,
                   "kept_receipts": bool(keep_receipts), "commit_message": msg, "remote": remote,
                   "public_safety": {"hits": 0, "tree_hits": 0}}
        status_after = (preview.get("status") or {}).get("to") or status
        if dry_run:
            nxt = next_commands(name, target, "<new12>", remote, status_after != "gate-pending")
            plan += [f"  new commit (on the real run): one root commit on {BRANCH}, message:",
                     *[f"    {x}".rstrip() for x in msg.rstrip("\n").split("\n")],
                     f"  registry: update {name} --path {stored} --reason {reason!r}"
                     + (f" --repo {remote}" if remote else "")
                     + f"; gate/comparator evidence cleared; status {status} -> {status_after}; dated note "
                       f"with the old receipt paths and {src[:12]}",
                     "DRY RUN — nothing written. Re-run without --dry-run to export.", "next:"]
            plan += [f"  {c}" for c in nxt]
            payload.update(new_commit=None, next_commands=nxt, relocation=preview)
            return plan, 0, payload

        if target.exists():
            target.rmdir()
        os.replace(stage, target)
        moved = True
    finally:
        if not moved:
            shutil.rmtree(stage, ignore_errors=True)

    for args in (("init", "-q"), ("symbolic-ref", "HEAD", f"refs/heads/{BRANCH}"), ("add", "-A", "-f", "."),
                 ("commit", "-q", "--no-verify", "-m", msg)):
        p = core.git(target, *args)
        if p.returncode != 0:
            raise core.Fail(f"{name}: git {args[0]} in {target} failed: {(p.stderr or p.stdout).strip()[:200]} "
                            f"(the exported files are in {target}; the registry is unchanged)")
    if remote:
        p = core.git(target, "remote", "add", "origin", remote)
        if p.returncode != 0:
            raise core.Fail(f"{name}: git remote add origin failed: {(p.stderr or p.stdout).strip()[:200]}")
    new = core.git_head(target) or ""

    old_gate = dict(pkg.get("gate") or {})
    reloc_out, reloc = core.do_relocate(reg_path, reg, events_p, name, new_path=stored, reason=reason,
                                        agent=agent, redetect=False, dry_run=False)
    pkg = core.get_pkg(reg, name)
    note = (f"fresh-history release: exported {src[:12]} to {stored} as {new[:12]} (one commit); earlier "
            f"evidence, bound to {src[:12]} and not to the new commit: gate.receipt="
            f"{old_gate.get('receipt') or 'none'}, gate.comparator={old_gate.get('comparator') or 'none'}")
    sets: dict = {"notes": {"text": note, "agent": (agent or "lean-pkg").strip()}}
    if remote:
        sets["repo"] = remote
    core.do_update(reg_path, reg, events_p, name, sets=sets, status=None, reason=reason, redetect=False,
                   agent=agent, force=False)
    status_after = pkg.get("status")
    nxt = next_commands(name, target, new[:12], remote, status_after != "gate-pending")
    lines = plan + [f"  new commit: {new[:12]} on {BRANCH}" + (f"; origin {remote}" if remote else "")]
    lines += [f"  registry: {ln.strip()}" for ln in reloc_out if ln.strip().startswith(("cleared:", f"{name}:"))]
    lines += [f"  status: {status} -> {status_after}", f"  note: {note}", "next:"] + [f"  {c}" for c in nxt]
    payload.update(new_commit=new, next_commands=nxt, relocation=reloc, note=note,
                   status={"from": status, "to": status_after})
    return lines, 0, payload


def cmd_release_fresh(args) -> int:
    if args.force:
        raise core.UsageError("--force releases a lease; it does not go with --fresh-repo")
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    lines, rc, payload = do_fresh_repo(reg_path, core.load_registry(reg_path), events_p, args.name,
                                       target_arg=args.fresh_repo, remote=args.remote,
                                       keep_receipts=args.keep_receipts, given_commit=args.source_commit,
                                       agent=args.agent, reason=args.reason, dry_run=args.dry_run)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc
