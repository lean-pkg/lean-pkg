#!/usr/bin/env python3
"""`receipt slurm --preflight-deps --fill-plan DONOR`: steps 1-4 of docs/receipts.md, "Filling missing
dependency artifacts from a pinned donor", read-only. It writes nothing except the files passed explicitly
with --plan-out; it never touches the base cache, the donor or the package's .lake/build.

1. The plan: the same dependency preflight as `--preflight-deps` on the base cache (--deps-tar or
   --deps-dir), keeping EVERY missing module (the 500 cap of missing[] is a display cap only). Problems other
   than missing files (a package at another rev, a stripped checkout, a module no package provides, a module
   whose source is not staged) are listed as not fillable from a donor, and refuse.
2. The donor checks: DONOR/lean-toolchain is byte for byte `git show SHA:lean-toolchain`; every manifest git
   package is in DONOR/.lake/packages at the manifest's rev (the preflight's own pin check, run on the donor);
   each such checkout has no source edits (`git status --porcelain --untracked-files=no` prints nothing, run
   with GIT_OPTIONAL_LOCKS=0 so git does not refresh the donor's index and with the checkout's core.fsmonitor
   off; no index entry is marked assume-unchanged or skip-worktree, which hide an edit from git status; and
   no untracked or ignored *.lean, lakefile, lean-toolchain or lake-manifest.json sits outside .lake/; a
   package with no `.git` directory, or one git cannot read, refuses: an edit cannot be ruled out); and the first 80 bytes of each candidate .olean
   (marker, format version, Lean version, Lean commit) equal those of the base cache's .olean files, which
   must agree with each other.
3. The artifact families: for each missing module, every regular file the donor holds under the module's
   path in its package's build directory (.lake/build/ or build/, in lib/lean/, lib/ or ir/): `<path>.<any
   extension>`, so .olean, .olean.private, .olean.server, .ilean, .ir, each .hash, .trace, ir/*.c, .c.hash
   and anything else present. A module whose .olean or .ilean the donor lacks is not fillable.
4. Additions only: no addition may be a member of the base (the base directory's entries, the tarball's
   members, and any --base-list FILE names), or sit under a base member that is not a directory; none may
   be under the package's own .lake/build or hold one of the package's root namespaces.

The archive is still written by the user (steps 5-7 of the recipe), and the official preflight rerun on it
is what confirms the fill. The provenance stub written with --plan-out is collector provenance, never a job
seal."""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib
import subprocess
import tarfile

from . import cli as core
from . import deps_closure as dc

BUILD_ROOTS = (".lake/build/", "build/")
FAMILY_DIRS = ("lib/lean/", "lib/", "ir/")
PROVENANCE_SCHEMA = "lean-pkg-fill-provenance/1"
PROVENANCE_KIND = "collector provenance: which bytes were added and where they came from; never a job seal"
SHOW_MODULES = 20
CHUNK = 1 << 20

# Refusal codes, one per check (JSON refusals[].code; the exit code is 1 for every refusal).
BASE_PROBLEM = "base-problem"              # a preflight problem a donor cannot fill
DONOR_CACHE = "donor-cache"                # DONOR holds no .lake/packages
DONOR_TOOLCHAIN = "donor-toolchain"
DONOR_MANIFEST = "donor-manifest"
DONOR_SOURCE = "donor-source-edits"
OLEAN_HEADER = "olean-header"
DONOR_LACKS = "donor-lacks-module"
BASE_OVERLAP = "base-overlap"
PACKAGE_BUILD = "package-build"            # under the package's own .lake/build or a root namespace
PLAN_OUT = "plan-out"


def sha256_file(path: pathlib.Path) -> tuple[int, str]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            size += len(chunk)
            h.update(chunk)
    return size, h.hexdigest()


def family_index(cache: dc.Cache) -> dict[str, list[str]]:
    """`.lake/packages/<pkg>/<build root><family dir><module path>` -> the regular files `<that>.<ext>`."""
    out: dict[str, list[str]] = {}
    for rel in cache.files:
        pkg, _, inner = rel[len(dc.PACKAGES):].partition("/")
        if not any(inner.startswith(b) for b in BUILD_ROOTS):
            continue
        head, _, name = rel.rpartition("/")
        stem, dot, _ext = name.partition(".")
        if dot and stem:
            out.setdefault(f"{head}/{stem}", []).append(rel)
    return out


def module_families(index: dict[str, list[str]], pkg: str, mpath: str) -> list[str]:
    files: set[str] = set()
    for b in BUILD_ROOTS:
        for d in FAMILY_DIRS:
            files.update(index.get(f"{dc.PACKAGES}{pkg}/{b}{d}{mpath}", []))
    return sorted(files)


def has_family(files: list[str], mpath: str, ext: str) -> bool:
    """`files` (one module's families) hold `<module path><ext>` in a lib/ directory."""
    return any(f.endswith(f"/lib/lean/{mpath}{ext}") or f.endswith(f"/lib/{mpath}{ext}") for f in files)


def module_part(rel: str) -> str:
    """The path below `<build root><family dir>` of a dependency build file (else the path in the package)."""
    inner = rel[len(dc.PACKAGES):].partition("/")[2]
    for b in BUILD_ROOTS:
        for d in FAMILY_DIRS:
            if inner.startswith(b + d):
                return inner[len(b + d):]
    return inner


# Untracked or ignored files git status never shows but Lake would read: Lean sources and the package's own
# configuration. `.lake/` is the one generated area (build outputs, nested packages) and is not looked at.
SOURCE_PATHSPECS = ("*.lean", "lakefile.lean", "lakefile.toml", "lean-toolchain", "lake-manifest.json",
                    ":(exclude).lake")


def git_status(checkout: pathlib.Path) -> tuple[bool, str]:
    """(clean, detail) for one donor dependency checkout; never writes (no optional locks, no index refresh),
    never discovers a repository above the checkout. Clean means: no tracked change (git status), no index
    entry hidden from git status (assume-unchanged, skip-worktree), and no untracked or ignored Lean source
    or Lake configuration outside .lake/. The checkout's own core.fsmonitor and untracked cache are off."""
    gitdir = checkout / ".git"
    if not gitdir.is_dir() or gitdir.is_symlink():
        return False, "no .git directory: not a git checkout, so a source edit cannot be ruled out"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_DIR=str(gitdir), GIT_WORK_TREE=str(checkout), GIT_OPTIONAL_LOCKS="0",
               GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-c", f"safe.directory={checkout}", "-c", "core.fsmonitor=false",
                               "-c", "core.untrackedCache=false", "--no-optional-locks", *args],
                              cwd=checkout, env=env, capture_output=True, text=True, timeout=600)

    def shown(names: list[str]) -> str:
        return ", ".join(names[:5]) + (f" (and {len(names) - 5} more)" if len(names) > 5 else "")

    try:
        p = git("status", "--porcelain", "--untracked-files=no", "--ignore-submodules=none")
        flags = git("ls-files", "-v", "-z") if p.returncode == 0 else p
        others = (git("ls-files", "--others", "-z", "--", *SOURCE_PATHSPECS)
                  if flags.returncode == 0 else flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"git status could not run ({type(e).__name__}): a source edit cannot be ruled out"
    if others.returncode != 0:
        why = (others.stderr.strip().splitlines() or ["no message"])[0]
        return False, f"git status failed ({why}): a source edit cannot be ruled out"
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    if lines:
        return False, f"{len(lines)} tracked file(s) differ from the checkout's commit: {shown([ln[3:] for ln in lines])}"
    # ls-files -v tags an assume-unchanged entry in lower case and a skip-worktree one with S (s if both).
    hidden = [e[2:] for e in flags.stdout.split("\0") if e and (e[0].islower() or e[0] == "S")]
    if hidden:
        return False, (f"{len(hidden)} tracked file(s) hidden from git status (assume-unchanged or "
                       f"skip-worktree), so an edit cannot be ruled out: {shown(hidden)}")
    extra = [e for e in others.stdout.split("\0") if e]
    if extra:
        return False, (f"{len(extra)} untracked or ignored Lean source or Lake configuration file(s) outside "
                       f".lake/: {shown(extra)}")
    return True, "no tracked change, no hidden index entry, no untracked Lean source"


def header_key(h: bytes | None) -> bytes | None:
    """The toolchain-identifying part of an .olean header: the first 80 bytes (format version 2: marker,
    version, flags, Lean version string, Lean commit; the per-module base address follows), or the first 46
    for format version 1 (marker, version, Lean commit; the base address follows at once)."""
    if h is None:
        return None
    return h[:46] if len(h) > 5 and h[5] == 1 else h[:dc.OLEAN_HEADER]


def _inside(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def cmd(args) -> int:
    repo = pathlib.Path(args.repo).resolve() if args.repo else pathlib.Path.cwd()
    rev = args.commit_sha or "HEAD"
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    commit = p.stdout.strip() if p.returncode == 0 else ""
    if not commit:
        raise core.UsageError(f"--commit {rev}: not a commit in the --repo checkout (default the current directory)")
    donor = pathlib.Path(args.fill_plan)
    if not donor.is_dir():
        raise core.UsageError(f"--fill-plan {args.fill_plan}: not a directory (the donor: a directory holding "
                              f".lake/packages)")
    if args.deps_tar:
        base_path = pathlib.Path(args.deps_tar)
        if not base_path.is_file():
            raise core.UsageError(f"--deps-tar {args.deps_tar}: not a file")
    else:
        base_path = pathlib.Path(args.deps_dir)
        if not base_path.is_dir():
            raise core.UsageError(f"--deps-dir {args.deps_dir}: not a directory")
    base_list: list[str] = []
    if args.base_list:
        try:
            raw = pathlib.Path(args.base_list).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            raise core.UsageError(f"--base-list {args.base_list}: cannot read it ({type(e).__name__})")
        base_list = [n for n in (dc._norm(ln.rstrip("\n")) for ln in raw.splitlines() if ln.strip()) if n]

    refusals: list[dict] = []
    checks: list[str] = []

    def refuse(code: str, reason: str) -> None:
        refusals.append({"code": code, "reason": reason})

    plan_out = pathlib.Path(args.plan_out) if args.plan_out else None
    prov_out = plan_out.with_name(plan_out.name + ".provenance.json") if plan_out else None
    if plan_out:
        for out in (plan_out, prov_out):
            if os.path.lexists(out):
                refuse(PLAN_OUT, f"--plan-out: {out} already exists (never overwritten)")
            for root, what in ((donor, "the donor"), (base_path if base_path.is_dir() else None, "the base cache"),
                               (repo / ".lake", "the package's .lake")):
                if root is not None and _inside(out.parent, root):
                    refuse(PLAN_OUT, f"--plan-out: {out} is inside {what}, which the plan never writes")

    # 1. the plan: the official preflight on the base, every missing module kept
    base = None
    try:
        base = dc.read_tar(base_path, headers=True) if args.deps_tar else dc.read_dir(base_path, headers=True)
    except (tarfile.TarError, OSError, EOFError) as e:
        refuse(BASE_PROBLEM, f"--deps-tar: cannot read the tarball ({type(e).__name__}: {e})")
    entries = list(args.entries) or list(dc.DEFAULT_ENTRIES)
    result = {"entries": [], "required_external_modules": 0, "missing_count": 0, "missing": [],
              "missing_all": [], "other_problems": [], "local_modules": [], "checks": []}
    if base is not None:
        if not any(f.startswith(dc.PACKAGES) for f in base.files):
            refuse(BASE_PROBLEM, f"{base.label}: no regular file under .lake/packages/ (the job restores only "
                                 f"that directory)")
        else:
            result = dc.check(repo, commit, base, entries)
    for pr in result["other_problems"]:
        refuse(BASE_PROBLEM, f"not fillable from a donor: {pr}")
    checks.extend(f"base: {c}" for c in result["checks"])
    missing_all = result["missing_all"]

    # 2. the donor checks
    donor_checks: list[dict] = []

    def donor_check(name: str, ok: bool, detail: str, code: str) -> None:
        donor_checks.append({"check": name, "ok": ok, "detail": detail})
        if ok:
            checks.append(f"✓ donor {name}: {detail}")
        else:
            refuse(code, f"donor {name}: {detail}")

    want_tc = core.git_show_bytes(repo, "./lean-toolchain", commit)
    tc_path = donor / "lean-toolchain"
    have_tc = tc_path.read_bytes() if tc_path.is_file() else None
    if want_tc is None:
        donor_check("lean-toolchain", False, f"no lean-toolchain committed at {commit[:12]}: nothing to compare "
                                             f"the donor's with", DONOR_TOOLCHAIN)
    elif have_tc is None:
        donor_check("lean-toolchain", False, "the donor has no lean-toolchain file", DONOR_TOOLCHAIN)
    elif have_tc != want_tc:
        donor_check("lean-toolchain", False, f"{have_tc.decode('utf-8', 'replace').strip()!r} is not the "
                                             f"commit's {want_tc.decode('utf-8', 'replace').strip()!r} (byte "
                                             f"for byte)", DONOR_TOOLCHAIN)
    else:
        donor_check("lean-toolchain", True, f"= {commit[:12]}:lean-toolchain byte for byte "
                                            f"({want_tc.decode('utf-8', 'replace').strip()})", DONOR_TOOLCHAIN)

    names, pins = dc.manifest_pins(repo, commit, [])
    donor_cache = dc.read_dir(donor, headers=True)
    heads: dict[str, str] = {}
    if not any(f.startswith(dc.PACKAGES) for f in donor_cache.files):
        donor_check("cache", False, "no regular file under DONOR/.lake/packages/", DONOR_CACHE)
    else:
        pin_problems: list[str] = []
        heads = dc.pin_check(donor_cache, names, pins, pin_problems, [])
        for pr in pin_problems:
            donor_check("manifest rev", False, pr, DONOR_MANIFEST)
        if names and not pin_problems:
            donor_check("manifest revs", True, f"all {len(names)} manifest git package(s) at the manifest's rev "
                                               f"(the preflight's pin check)", DONOR_MANIFEST)
        clean = 0
        for name in sorted(heads):
            ok, detail = git_status(donor / ".lake" / "packages" / name)
            if ok:
                clean += 1
            else:
                donor_check("source edits", False, f"{name}: {detail}", DONOR_SOURCE)
        if heads and clean == len(heads):
            donor_check("source edits", True, f"none in {clean} checkout(s) (git status, no "
                                              f"assume-unchanged/skip-worktree entry, no untracked or "
                                              f"ignored Lean source outside .lake/)", DONOR_SOURCE)

    # 3. the artifact families of every missing module
    index = family_index(donor_cache)
    plan: list[dict] = []
    for m in missing_all:
        mpath = dc.module_path(m["module"])
        files = module_families(index, m["package"], mpath)
        entry = {"module": m["module"], "package": m["package"], "lacking": list(m["files"]),
                 "required_by": m["required_by"], "fillable": True, "files": [], "bytes": 0}
        lacks = [ext for ext in (".olean", ".ilean") if not has_family(files, mpath, ext)]
        if lacks:
            entry["fillable"] = False
            refuse(DONOR_LACKS, f"module {m['module']} ({m['package']}): the donor has no {'/'.join(lacks)} "
                                f"either; build it instead")
        for rel in files:
            size, digest = sha256_file(donor / rel)
            entry["files"].append({"path": rel, "size": size, "sha256": digest})
            entry["bytes"] += size
        plan.append(entry)

    # the .olean headers: the base's agree, and every candidate equals them
    ref_header = None
    base_headers = {header_key(h) for h in base.headers.values()} if base is not None else set()
    if len(base_headers) > 1:
        refuse(OLEAN_HEADER, f"the base cache's .olean files do not share one {dc.OLEAN_HEADER}-byte header "
                             f"({len(base_headers)} different): no single toolchain build to compare with")
    elif not base_headers:
        if missing_all:
            refuse(OLEAN_HEADER, "the base cache holds no built .olean to compare the donor's headers with")
    else:
        ref_header = next(iter(base_headers))
        if not ref_header.startswith(b"olean"):
            refuse(OLEAN_HEADER, "the base cache's .olean files do not start with the olean marker")
            ref_header = None
    if ref_header is not None:
        bad = sorted(f["path"] for e in plan for f in e["files"]
                     if f["path"].endswith(".olean") and header_key(donor_cache.headers.get(f["path"])) != ref_header)
        n = sum(1 for e in plan for f in e["files"] if f["path"].endswith(".olean"))
        if bad:
            refuse(OLEAN_HEADER, f"{len(bad)} candidate .olean file(s) differ from the base's first "
                                 f"{dc.OLEAN_HEADER} bytes (another Lean build): {', '.join(bad[:5])}")
        elif n:
            checks.append(f"✓ olean headers: {n} candidate .olean file(s) = the base's first {dc.OLEAN_HEADER} "
                          f"bytes")

    # 4. additions only
    additions = sorted(f["path"] for e in plan for f in e["files"])
    roots = {lm.split("/", 1)[0] for lm in result["local_modules"]}
    overlap, under_file, own = [], [], []
    base_names = dict(base.members) if base is not None else {}
    for n in base_list:
        base_names.setdefault(n, "f")
    for rel in additions:
        if not rel.startswith(dc.PACKAGES) or rel.startswith(".lake/build/") or \
                module_part(rel).split("/", 1)[0].split(".", 1)[0] in roots:
            own.append(rel)
        if args.deps_dir:
            target = base_path / rel
            if os.path.lexists(target):
                overlap.append(rel)
            parts = rel.split("/")
            for i in range(1, len(parts)):
                q = base_path.joinpath(*parts[:i])
                if os.path.lexists(q) and (q.is_symlink() or not q.is_dir()):
                    under_file.append(rel)
                    break
        if rel in base_names:
            if rel not in overlap:
                overlap.append(rel)
        else:
            parts = rel.split("/")
            if any(base_names.get("/".join(parts[:i]), "d") != "d" for i in range(1, len(parts))):
                if rel not in under_file:
                    under_file.append(rel)
    if overlap:
        refuse(BASE_OVERLAP, f"{len(overlap)} addition(s) already in the base (an addition never replaces a base "
                             f"member): {', '.join(overlap[:5])}")
    if under_file:
        refuse(BASE_OVERLAP, f"{len(under_file)} addition(s) under a base member that is not a directory: "
                             f"{', '.join(under_file[:5])}")
    if own:
        refuse(PACKAGE_BUILD, f"{len(own)} addition(s) under the package's own .lake/build or holding one of its "
                              f"root namespaces ({', '.join(sorted(roots)[:5])}): {', '.join(own[:5])}")
    if additions and not (overlap or under_file or own):
        checks.append(f"✓ additions only: none of the {len(additions)} file(s) is a base member or under one; none "
                      f"under the package's own .lake/build or root namespaces")

    totals = {"missing_modules": len(missing_all), "fillable_modules": sum(1 for e in plan if e["fillable"]),
              "files": len(additions), "bytes": sum(e["bytes"] for e in plan)}
    ok = not refusals
    written = None
    if ok and plan_out:
        base_sha = sha256_file(base_path)[1] if args.deps_tar else None
        prov = {
            "schema": PROVENANCE_SCHEMA,
            "kind": PROVENANCE_KIND,
            "created_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commit": commit,
            "lean_toolchain": (want_tc or b"").decode("utf-8", "replace").strip(),
            "manifest_revs": {n: pins[n] for n in names},
            "base": {"kind": "tar" if args.deps_tar else "dir", "name": base_path.name, "sha256": base_sha},
            "donor": {"name": donor.resolve().name, "package_heads": heads,
                      "lean_toolchain_sha256": hashlib.sha256(have_tc or b"").hexdigest(),
                      "olean_header_sha256": hashlib.sha256(ref_header).hexdigest() if ref_header else None},
            "donor_checks": donor_checks,
            "plan": [{"module": e["module"], "package": e["package"], "lacking": e["lacking"]} for e in plan],
            "additions": [f for e in plan for f in e["files"]],
            "filled_archive": None,
            "next": ["write the new archive from the base's members plus these additions, never replacing a "
                     "base member (docs/receipts.md, step 5)",
                     "record the filled archive's sha256 here (step 6)",
                     "rerun `lean-pkg receipt slurm --preflight-deps` on the filled archive; submit only on "
                     "exit 0 (step 7)"],
        }
        try:
            with open(plan_out, "x", encoding="utf-8") as f:
                f.write("".join(a + "\n" for a in additions))
            with open(prov_out, "x", encoding="utf-8") as f:
                f.write(json.dumps(prov, indent=2, sort_keys=False) + "\n")
            written = {"additions": str(plan_out), "provenance": str(prov_out)}
        except OSError as e:
            refuse(PLAN_OUT, f"--plan-out: cannot write ({type(e).__name__}: {e})")
            ok = False

    if getattr(args, "json_out", False):
        core.print_json({
            "ok": ok, "preflight_deps": True, "fill_plan": True, "commit": commit,
            "entries": result["entries"], "required_external_modules": result["required_external_modules"],
            "missing_count": result["missing_count"], "missing": result["missing"],
            "base": {"kind": "tar" if args.deps_tar else "dir", "name": base_path.name,
                     "files": len(base.files) if base is not None else 0},
            "donor": {"name": donor.resolve().name, "package_heads": heads},
            "donor_checks": donor_checks, "plan": plan,
            "not_fillable": result["other_problems"], "refusals": refusals, "totals": totals,
            "plan_out": written, "checks": checks, "problems": [r["reason"] for r in refusals]})
        return 0 if ok else 1
    verdict = "ok" if ok else f"refused, {len(refusals)} reason(s)"
    print(f"receipt slurm --preflight-deps --fill-plan: {verdict}; {totals['missing_modules']} missing module(s), "
          f"{totals['files']} file(s), {totals['bytes']} byte(s) to add; "
          + (f"wrote {plan_out} and {prov_out}" if written else "nothing written"))
    for c in checks:
        print(f"  {c}")
    for e in plan[:SHOW_MODULES]:
        mark = "+" if e["fillable"] else "✗"
        print(f"  {mark} {e['package']} {e['module']}: {len(e['files'])} file(s), {e['bytes']} byte(s)")
    if len(plan) > SHOW_MODULES:
        print(f"  (and {len(plan) - SHOW_MODULES} more module(s); --json lists every one)")
    for r in refusals:
        print(f"  ✗ [{r['code']}] {r['reason']}")
    if ok:
        print("next: write the new archive yourself (base members unchanged, these additions appended; "
              "docs/receipts.md, steps 5-7), then rerun --preflight-deps on it and submit only on exit 0")
    return 0 if ok else 1
