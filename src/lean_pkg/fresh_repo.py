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

`--fresh-repo DIR --from-tree SRCDIR` (#37) exports the working tree at SRCDIR instead of a commit:
a tree cleaned by hand (files removed or edited) that is not a commit of the source repository. In a
git checkout the files are those `git ls-files --cached --others --exclude-standard` lists (the
source's .gitignore applies); otherwise every file but `.git/` and `.lake/`. Same exclusions, same
scan (the commit message too, since it names SRCDIR's basename), same single commit; its message
records "source commit: none (hand-cleaned tree)" and the registry's gate-bound commit, if any, for
provenance.

`release NAME --adopt-repo DIR [--remote URL] [--allow-history] [--dry-run]` (#37) adopts an
existing repository that already is the intended public history: refused unless it has exactly one
commit on all refs (or --allow-history), unless DIR is its top directory, while tracked files are
modified, and on any blocking public-safety hit in its tree or full history. No export and no new
commit: the registry entry is relocated to DIR as above, with a dated note naming the adopted commit
and the previous gate-bound commit.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tarfile
import tempfile

from . import cli as core
from . import export_paths as xp
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
    why = xp.excluded_from_export(name)
    if why:
        return why
    if name == ".lean-receipts" or name.startswith(".lean-receipts/"):
        return None if keep_receipts else ".lean-receipts/ (not copied without --keep-receipts)"
    return None


SKIP_DIRS = (".git", ".lake")


def tree_listing(srcdir: pathlib.Path) -> tuple[list[str], str]:
    """(SRCDIR-relative paths to export, how they were listed): git's view of the working tree
    (tracked + untracked, .gitignore honoured) in a checkout, else a walk; `.git/` and `.lake/`
    never."""
    p = core.git(srcdir, "rev-parse", "--is-inside-work-tree")
    if p.returncode == 0 and p.stdout.strip() == "true":
        ls = core.git(srcdir, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        if ls.returncode != 0:
            raise core.Fail(f"git ls-files in {srcdir} failed: {(ls.stderr or '').strip()[:200]}")
        files = sorted({x for x in ls.stdout.split("\0") if x})
        how = "git ls-files --cached --others --exclude-standard"
    else:
        files = []
        for d, dirs, names in os.walk(srcdir):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS)
            files += [os.path.relpath(os.path.join(d, nm), srcdir).replace(os.sep, "/") for nm in names]
        files.sort()
        how = "every file (not a git checkout)"
    return [f for f in files if not set(pathlib.PurePosixPath(f).parts) & set(SKIP_DIRS)], how


def _copy_tree(srcdir: pathlib.Path, dest: pathlib.Path, files: list[str], drop) -> list[dict]:
    """Copy the listed files from srcdir into dest, skipping those drop(name) names a reason for;
    a symlink is kept only when it is relative and stays inside the tree. The dropped files, as
    [{path, reason}]."""
    dropped: list[dict] = []
    for rel in files:
        why = drop(rel)
        src = srcdir / rel
        if not why and src.is_symlink():
            if xp.fs_link_leaves_tree(src, srcdir):
                why = "symlink leaving the tree (not exported)"
        elif not why and src.is_dir():
            why = "submodule or nested repository (not exported)"
        if why:
            dropped.append({"path": rel, "reason": why})
            continue
        if not src.is_symlink() and not src.is_file():
            continue                                  # tracked but deleted in the working tree
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), out)
        else:
            shutil.copy2(src, out)
    return dropped


def gate_bound_commit(pkg: dict, repo: pathlib.Path) -> str | None:
    """The commit the recorded gate receipt binds, when it can be read; provenance only."""
    rel = (pkg.get("gate") or {}).get("receipt")
    rec = core.recorded_json(repo, rel) if rel and repo.is_dir() else None
    sha = str((rec or {}).get("commit") or "")
    return sha if core._HEX40.fullmatch(sha) else None


def patterns_root(repo: pathlib.Path, fallback: pathlib.Path) -> pathlib.Path:
    """The package checkout's patterns file applies when it has one; else the given directory's."""
    return repo if (repo / ps.PATTERNS_FILE).is_file() else fallback


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


def export_identity_hits(sc: "ps.Scanner") -> list[dict]:
    """The blocking public-safety hits in the author and committer identity the export commit will
    carry (published with it): git's identity for a new repository, read in an empty scratch one.
    As for any commit identity: an ordinary mail address is declared by being used; a host-like one
    (`user@hostname`, a local TLD such as `.lan`) is a hit unless consent or an `allow:` line names it."""
    out: list[dict] = []
    seen: set = set()
    with tempfile.TemporaryDirectory(prefix="lean-pkg-ident-") as d:
        scratch = pathlib.Path(d)
        if core.git(scratch, "init", "-q").returncode != 0:
            return out
        for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            p = core.git(scratch, "var", var)
            m = re.match(r"(.*?) <([^>]*)>", p.stdout.strip()) if p.returncode == 0 else None
            if not m or m.groups() in seen:
                continue
            seen.add(m.groups())
            out += [ps._hit(sc, "tree", "export-commit-identity", None, pat.label, pat.kind, ex,
                            severity=pat.severity)
                    for _n, pat, ex in sc.identity_hits(m.group(1), m.group(2)) if pat.severity == ps.BLOCK]
    return out


def carry_public_allow(name: str, pkg: dict, pat_root: pathlib.Path, email_repo: pathlib.Path,
                       stage: pathlib.Path, today: str) -> dict:
    """#6: the patterns file is dropped (it lists private strings), so its benign `allow:` lines would be lost
    and the new repository's own public-safety would report what the export scan allowed. Carry ONLY those
    lines into the committed `.lean-pkg-public-allow` (merged with one the tree already has); never a
    block/warn line. A line a private block pattern (the patterns file's, a registry-derived name) matches
    refuses the export: publishing it would publish the private string."""
    lines = ps.allow_lines(pat_root)
    view = {"file": None, "carried": 0, "lines": []}
    if not lines:
        return view
    sc = ps.Scanner(pkg, pat_root, email_repo)
    bad = [(n, labels) for n, t in lines for labels in [ps.private_matches(sc, t)] if labels]
    if bad:
        raise core.Fail(f"{name}: allow line(s) {', '.join(str(n) for n, _l in bad)} of {ps.PATTERNS_FILE} hold a "
                        f"private string (" + "; ".join(f"line {n}: {', '.join(lab)}" for n, lab in bad)
                        + f"), so they cannot be carried into the public {ps.PUBLIC_ALLOW_FILE}; nothing written. "
                          f"Rewrite each without the private string (a narrower regex, or a path scope "
                          f"`allow: GLOB: REGEX`), then re-run")
    dest = stage / ps.PUBLIC_ALLOW_FILE
    if dest.is_symlink():
        raise core.Fail(f"{name}: the exported {ps.PUBLIC_ALLOW_FILE} is a symbolic link; refusing to write "
                        f"through it; nothing written")
    existing = dest.read_text(encoding="utf-8").splitlines() if dest.is_file() else []
    have = {x.strip() for x in existing}
    new = [(n, t) for n, t in lines if t not in have]
    if not new:
        return {"file": ps.PUBLIC_ALLOW_FILE, "carried": 0, "lines": []}
    head = [] if existing else [
        "# lean-pkg public allow list: `allow:` lines only (public-safety reads them; block and warn lines",
        f"# belong in the untracked {ps.PATTERNS_FILE}). Committed on purpose: nothing here is private."]
    body = existing + head + [f"# carried from {ps.PATTERNS_FILE} by `lean-pkg release --fresh-repo` ({today})"]
    dest.write_text("\n".join(body + [t for _n, t in new]) + "\n", encoding="utf-8")
    return {"file": ps.PUBLIC_ALLOW_FILE, "carried": len(new), "lines": [n for n, _t in new]}


def commit_message(src: str, today: str) -> str:
    return (f"fresh-history export of {src[:12]}; receipts and gate must be re-run on this commit\n\n"
            f"source commit: {src}\nexport date: {today}\n")


def tree_commit_message(srcdir: pathlib.Path, provenance: str | None, today: str) -> str:
    return (f"fresh-history export of working tree {srcdir.name}; receipts and gate must be re-run on "
            f"this commit\n\nsource commit: none (hand-cleaned tree)\n"
            f"source package commit for provenance: {provenance or 'none'}\nexport date: {today}\n")


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
                  agent, reason, dry_run, from_tree=None) -> tuple[list[str], int, dict]:
    pkg = core.get_pkg(reg, name)
    status = pkg.get("status")
    if status in NOT_FROM:
        raise core.Fail(f"{name}: status {status}: a fresh-history export is for a package that has not been "
                        f"promoted (a public history is already exposed; an archived package is not published)")
    repo = core.repo_path(pkg)
    srcdir = src_stored = None
    if from_tree is None:
        if not repo.is_dir() or core.git_head(repo) is None:
            raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    else:
        src_stored, _k = core.normalize_local_path(from_tree, "--from-tree")
        srcdir = core.HOME / pathlib.Path(src_stored)
        if not srcdir.is_dir():
            raise core.Fail(f"{name}: --from-tree {srcdir} is not a directory")
    if remote is not None:
        core.require_nonempty("--remote", remote)
    reason = reason or DEFAULT_REASON
    if srcdir is None:
        src, chosen = source_commit(pkg, repo, given_commit)
        provenance = None
    else:
        src, chosen = None, f"working tree {srcdir}"
        provenance = gate_bound_commit(pkg, repo)
    stored, _key = core.normalize_local_path(target_arg, "--fresh-repo")
    target = core.HOME / pathlib.Path(stored)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise core.Fail(f"{name}: --fresh-repo {target} exists and is not an empty directory; refusing")
    tops = []
    if repo.is_dir() and core.git_head(repo) is not None:
        tops.append(("the source repository", core.git(repo, "rev-parse", "--show-toplevel").stdout.strip()))
    if srcdir is not None:
        tops.append(("--from-tree", os.path.realpath(srcdir)))
    for what, top in tops:
        if top and (os.path.realpath(target) == top or pathlib.Path(top) in pathlib.Path(os.path.realpath(target)).parents):
            raise core.Fail(f"{name}: --fresh-repo {target} lies inside {what} {top}; choose a "
                            f"directory outside it")
    if not dry_run and core.git(repo if repo.is_dir() else srcdir, "var", "GIT_COMMITTER_IDENT").returncode != 0:
        raise core.Fail(f"{name}: git has no committer identity here (git config user.name / user.email); "
                        f"the export commit needs one")
    # the relocation's own refusals (lease, another package's path), before anything is written
    _o, preview = core.do_relocate(reg_path, reg, events_p, name, new_path=stored, reason=reason,
                                   agent=agent, redetect=False, dry_run=True)

    if dry_run:
        stage = pathlib.Path(tempfile.mkdtemp(prefix="lean-pkg-export-"))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = pathlib.Path(tempfile.mkdtemp(prefix=".lean-pkg-export-", dir=target.parent))
    moved = False
    try:
        receipts_note = listed = None
        if srcdir is None:
            prefix = core.git(repo, "rev-parse", "--show-prefix").stdout.strip()
            treeish = f"{src}:{prefix.rstrip('/')}" if prefix else src
            dropped = _extract(_archive(repo, treeish), stage, lambda n: drop_reason(n, keep_receipts))
            if keep_receipts:
                rel = f"{prefix}.lean-receipts"
                if core.tracked_at(repo, ".lean-receipts"):
                    (stage / ".lean-receipts").mkdir(exist_ok=True)
                    _extract(_archive(repo, f"HEAD:{rel}"), stage / ".lean-receipts", lambda n: None)
                    receipts_note = (f".lean-receipts/ copied from the source HEAD: bound to {src[:12]} or "
                                     f"earlier, they do NOT bind to the new commit")
                else:
                    receipts_note = "--keep-receipts: no .lean-receipts/ tracked at the source HEAD; nothing copied"
            pat_root, email_repo = repo, repo
        else:
            paths, listed = tree_listing(srcdir)
            dropped = _copy_tree(srcdir, stage, paths, lambda n: drop_reason(n, keep_receipts))
            if keep_receipts:
                receipts_note = (".lean-receipts/ kept from the working tree: they do NOT bind to the new commit"
                                 if (stage / ".lean-receipts").is_dir()
                                 else "--keep-receipts: no .lean-receipts/ in the working tree; nothing copied")
            pat_root = patterns_root(repo, srcdir)
            email_repo = repo if repo.is_dir() and core.git_head(repo) else srcdir
        files = sum(1 for _ in (p for p in stage.rglob("*") if p.is_file() or p.is_symlink()))
        if not files:
            raise core.Fail(f"{name}: nothing to export from {srcdir or src[:12]} (every file is excluded or "
                            f"ignored); nothing written")
        today = core.utc_today()
        carried = carry_public_allow(name, pkg, pat_root, email_repo, stage, today)
        files = sum(1 for _ in (p for p in stage.rglob("*") if p.is_file() or p.is_symlink()))
        result = ps.scan(pkg, stage, export=True, patterns_root=pat_root, source_repo=email_repo,
                         public_allow_root=stage)
        blocking = ps.blocking(result["hits"])   # #36: warn-only hits are reported, not refused
        msg = commit_message(src, today) if srcdir is None else tree_commit_message(srcdir, provenance, today)
        if srcdir is not None:   # the message names SRCDIR's basename: it is published too
            sc = ps.Scanner(pkg, pat_root, email_repo)
            blocking += [ps._hit(sc, "tree", "commit-message", n, pat.label, pat.kind, ex, severity=pat.severity)
                         for n, pat, ex in sc.scan_text(msg) if pat.severity == ps.BLOCK]
        blocking += export_identity_hits(ps.Scanner(pkg, pat_root, email_repo))
        if blocking:
            lines = [ps.hit_line(h) for h in blocking[:20]]
            if srcdir is None:
                raise core.Fail(f"{name}: the exported tree of {src[:12]} has {len(blocking)} public-safety "
                                f"hit(s); nothing written:\n" + "\n".join(lines)
                                + "\nfix them at the source (commit, then --source-commit SHA), or move them under "
                                  "an internal path (notes/, drafts/, ...) so the export drops them")
            raise core.Fail(f"{name}: the working tree {srcdir} has {len(blocking)} public-safety hit(s); nothing "
                            f"written:\n" + "\n".join(lines)
                            + "\nfix them in that tree (a commit-message hit: rename the directory), or move them "
                              "under an internal path (notes/, drafts/, ...) so the export drops them")
        what = f"{src[:12]} ({chosen})" if srcdir is None else f"working tree {srcdir} (not a commit; {listed})"
        plan = [f"{name}: fresh-history export of {what} -> {target}",
                f"  {files} file(s) exported; {len(dropped)} dropped"]
        if srcdir is not None:
            plan.append(f"  source commit: none (hand-cleaned tree); provenance: gate-bound commit "
                        f"{provenance[:12] if provenance else 'none'}")
        plan += [f"  dropped: {d['path']}  ({d['reason']})" for d in dropped]
        if receipts_note:
            plan.append(f"  note: {receipts_note}")
        if carried["carried"]:
            plan.append(f"  public allow: {carried['carried']} allow line(s) of {ps.PATTERNS_FILE} (line(s) "
                        f"{', '.join(map(str, carried['lines']))}) carried into {ps.PUBLIC_ALLOW_FILE} (committed; "
                        f"the new repository's public-safety reads it; block and warn lines are never carried)")
        nw = result["warn_hits"]
        plan.append("  public-safety on the exported tree: 0 hit(s)" if not nw else
                    f"  public-safety on the exported tree: 0 blocking hit(s), {nw} warning(s) "
                    f"({ps.warn_families(result['hits'])}); review with `lean-pkg public-safety {name} --strict` "
                    f"before publishing")
        payload = {"ok": True, "name": name, "dry_run": bool(dry_run), "source_commit": src,
                   "source": chosen, "export_path": stored, "files": files, "excluded": dropped,
                   "kept_receipts": bool(keep_receipts), "commit_message": msg, "remote": remote,
                   "public_allow": carried,
                   "public_safety": {"hits": nw, "tree_hits": nw, "block_hits": 0, "warn_hits": nw}}
        if srcdir is not None:
            payload.update(from_tree=src_stored, provenance_commit=provenance)
        status_after = (preview.get("status") or {}).get("to") or status
        if dry_run:
            nxt = next_commands(name, target, "<new12>", remote, status_after != "gate-pending")
            old = src[:12] if src else f"gate-bound commit {provenance[:12] if provenance else 'none'}"
            plan += [f"  new commit (on the real run): one root commit on {BRANCH}, message:",
                     *[f"    {x}".rstrip() for x in msg.rstrip("\n").split("\n")],
                     f"  registry: update {name} --path {stored} --reason {reason!r}"
                     + (f" --repo {remote}" if remote else "")
                     + f"; gate/comparator evidence cleared; status {status} -> {status_after}; dated note "
                       f"with the old receipt paths and {old}",
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
    if srcdir is None:
        note = (f"fresh-history release: exported {src[:12]} to {stored} as {new[:12]} (one commit); earlier "
                f"evidence, bound to {src[:12]} and not to the new commit: gate.receipt="
                f"{old_gate.get('receipt') or 'none'}, gate.comparator={old_gate.get('comparator') or 'none'}")
    else:
        note = (f"fresh-history release: exported the working tree {src_stored} (hand-cleaned, not a commit) to "
                f"{stored} as {new[:12]} (one commit); source package commit for provenance: "
                f"{provenance[:12] if provenance else 'none'}; earlier evidence, not bound to the new commit: "
                f"gate.receipt={old_gate.get('receipt') or 'none'}, "
                f"gate.comparator={old_gate.get('comparator') or 'none'}")
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


ADOPT_REASON = "adopted-repository release"


def do_adopt_repo(reg_path, reg, events_p, name, *, target_arg, remote, allow_history, agent, reason,
                  dry_run) -> tuple[list[str], int, dict]:
    """`release NAME --adopt-repo DIR` (#37): DIR already is the intended public history (built by
    hand); scan it, then relocate the package there. No export, no new commit."""
    pkg = core.get_pkg(reg, name)
    status = pkg.get("status")
    if status in NOT_FROM:
        raise core.Fail(f"{name}: status {status}: adopting a public history is for a package that has not been "
                        f"promoted (a public history is already exposed; an archived package is not published)")
    if remote is not None:
        core.require_nonempty("--remote", remote)
    reason = reason or ADOPT_REASON
    stored, _key = core.normalize_local_path(target_arg, "--adopt-repo")
    target = core.HOME / pathlib.Path(stored)
    head = core.git_head(target) if target.is_dir() else None
    if head is None:
        raise core.Fail(f"{name}: --adopt-repo {target} is not a git repository with a commit")
    top = core.git(target, "rev-parse", "--show-toplevel").stdout.strip()
    if os.path.realpath(top) != os.path.realpath(target):
        raise core.Fail(f"{name}: --adopt-repo {target} is inside the repository {top}, not its top directory; "
                        f"adopt a repository whose top directory is the package")
    repo = core.repo_path(pkg)
    if repo.is_dir() and core.git_head(repo) is not None:
        src_top = core.git(repo, "rev-parse", "--show-toplevel").stdout.strip()
        if src_top and os.path.realpath(src_top) == os.path.realpath(top):
            raise core.Fail(f"{name}: --adopt-repo {target} is the package's own source repository; adopt a "
                            f"separate repository, or export one with --fresh-repo DIR")
    count = core.git(target, "rev-list", "--all", "--count").stdout.strip()
    commits = int(count) if count.isdigit() else 0
    if commits != 1 and not allow_history:
        raise core.Fail(f"{name}: --adopt-repo {target} has {commits} commits on its refs; an adopted public "
                        f"history must be exactly one commit. Pass --allow-history if every commit is meant to "
                        f"be published (public-safety scans them all), or export a single commit with "
                        f"--fresh-repo DIR --from-tree {target}")
    if core.git(target, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise core.Fail(f"{name}: --adopt-repo {target} has uncommitted changes to tracked files; commit or "
                        f"discard them first (the adopted commit is what gets published)")
    origin = core.git(target, "remote", "get-url", "origin")
    origin_url = origin.stdout.strip() if origin.returncode == 0 else None
    if remote and origin_url and origin_url != remote:
        raise core.Fail(f"{name}: --adopt-repo {target} already has origin {origin_url}, not --remote {remote}; "
                        f"refusing to change it (git remote set-url origin URL yourself)")
    _o, preview = core.do_relocate(reg_path, reg, events_p, name, new_path=stored, reason=reason,
                                   agent=agent, redetect=False, dry_run=True)
    pat_root = patterns_root(repo, target)
    result = ps.scan(pkg, target, history=True, patterns_root=pat_root, source_repo=target)
    blocking = ps.blocking(result["hits"])
    if blocking:
        lines = [ps.hit_line(h) for h in blocking[:20]]
        raise core.Fail(f"{name}: --adopt-repo {target} has {len(blocking)} blocking public-safety hit(s) in its "
                        f"tree or history; nothing written:\n" + "\n".join(lines)
                        + "\nfix the tree (a history hit: rebuild the repository), then adopt again")
    provenance = gate_bound_commit(pkg, repo)
    prov12 = provenance[:12] if provenance else "none"
    nw = result["warn_hits"]
    new_remote = remote if remote and not origin_url else None
    plan = [f"{name}: adopt {target} at {head[:12]} ({commits} commit(s) on all refs"
            + ("; --allow-history" if allow_history and commits != 1 else "") + ") as the public history; "
            f"no export, no new commit",
            f"  previous gate-bound commit: {prov12}",
            "  public-safety on its tree and full history: 0 hit(s)" if not nw else
            f"  public-safety on its tree and full history: 0 blocking hit(s), {nw} warning(s) "
            f"({ps.warn_families(result['hits'])}); review with `lean-pkg public-safety {name} --strict` "
            f"before publishing"]
    if new_remote:
        plan.append(f"  git remote add origin {remote}")
    payload = {"ok": True, "name": name, "dry_run": bool(dry_run), "adopted_path": stored,
               "adopted_commit": head, "commits": commits, "allow_history": bool(allow_history),
               "remote": remote, "provenance_commit": provenance, "new_commit": None,
               "public_safety": {"hits": len(result["hits"]), "tree_hits": result["tree_hits"],
                                 "history_hits": result["history_hits"], "block_hits": 0, "warn_hits": nw}}
    status_after = (preview.get("status") or {}).get("to") or status
    if dry_run:
        nxt = next_commands(name, target, head[:12], remote or origin_url, status_after != "gate-pending")
        plan += [f"  registry: update {name} --path {stored} --reason {reason!r}"
                 + (f" --repo {remote}" if remote else "")
                 + f"; gate/comparator evidence not bound to {head[:12]} cleared; status {status} -> "
                   f"{status_after}; dated note naming {head[:12]} and {prov12}",
                 "DRY RUN — nothing written. Re-run without --dry-run to adopt.", "next:"]
        plan += [f"  {c}" for c in nxt]
        payload.update(next_commands=nxt, relocation=preview)
        return plan, 0, payload

    if new_remote:
        p = core.git(target, "remote", "add", "origin", remote)
        if p.returncode != 0:
            raise core.Fail(f"{name}: git remote add origin failed: {(p.stderr or p.stdout).strip()[:200]}")
    old_gate = dict(pkg.get("gate") or {})
    reloc_out, reloc = core.do_relocate(reg_path, reg, events_p, name, new_path=stored, reason=reason,
                                        agent=agent, redetect=False, dry_run=False)
    pkg = core.get_pkg(reg, name)
    note = (f"adopted-repository release: adopted {stored} at {head[:12]} ({commits} commit(s)) as the public "
            f"history, no export; previous gate-bound commit: {prov12}; evidence before adoption: gate.receipt="
            f"{old_gate.get('receipt') or 'none'}, gate.comparator={old_gate.get('comparator') or 'none'}")
    sets: dict = {"notes": {"text": note, "agent": (agent or "lean-pkg").strip()}}
    if remote:
        sets["repo"] = remote
    core.do_update(reg_path, reg, events_p, name, sets=sets, status=None, reason=reason, redetect=False,
                   agent=agent, force=False)
    status_after = pkg.get("status")
    nxt = next_commands(name, target, head[:12], remote or origin_url, status_after != "gate-pending")
    lines = plan + [f"  registry: {ln.strip()}" for ln in reloc_out
                    if ln.strip().startswith(("cleared:", f"{name}:"))]
    lines += [f"  status: {status} -> {status_after}", f"  note: {note}", "next:"] + [f"  {c}" for c in nxt]
    payload.update(next_commands=nxt, relocation=reloc, note=note, status={"from": status, "to": status_after})
    return lines, 0, payload


def cmd_release_adopt(args) -> int:
    extra = [f for f, on in (("--force", args.force), ("--fresh-repo", args.fresh_repo is not None),
                             ("--from-tree", args.from_tree is not None), ("--keep-receipts", args.keep_receipts),
                             ("--source-commit", args.source_commit is not None)) if on]
    if extra:
        raise core.UsageError(f"{', '.join(extra)} do(es) not go with --adopt-repo DIR (no export: the repository "
                              f"is adopted as it is)")
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    lines, rc, payload = do_adopt_repo(reg_path, core.load_registry(reg_path), events_p, args.name,
                                       target_arg=args.adopt_repo, remote=args.remote,
                                       allow_history=args.allow_history, agent=args.agent, reason=args.reason,
                                       dry_run=args.dry_run)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def cmd_release_fresh(args) -> int:
    if args.force:
        raise core.UsageError("--force releases a lease; it does not go with --fresh-repo")
    if args.allow_history:
        raise core.UsageError("--allow-history goes with --adopt-repo DIR")
    if args.from_tree is not None and args.source_commit is not None:
        raise core.UsageError("--from-tree exports a working tree, --source-commit a commit; give one")
    reg_path, events_p = core.registry_path(args), core.events_path(args)
    lines, rc, payload = do_fresh_repo(reg_path, core.load_registry(reg_path), events_p, args.name,
                                       target_arg=args.fresh_repo, remote=args.remote,
                                       keep_receipts=args.keep_receipts, given_commit=args.source_commit,
                                       agent=args.agent, reason=args.reason, dry_run=args.dry_run,
                                       from_tree=args.from_tree)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc
