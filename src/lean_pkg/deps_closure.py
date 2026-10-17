#!/usr/bin/env python3
"""`receipt slurm --preflight-deps`: before a job is submitted, check that a staged dependency cache (a
DEPS_TAR tarball, or a directory holding `.lake/packages`) holds every external module the verifier's own
entry files need, at the pins of the package's lake-manifest.json. Read-only; nothing is built or written.

Field report (#5): a job restored a tarball holding the dependency builds of the library target's import
cone; `lake build` passed, then `lake env lean Audit/.../Solution.lean` (which imports the `Mathlib`
umbrella) failed because `Mathlib.olean` was not in the tarball. The build target's cone is not the audit's.

The rule (fail-closed). Entry files are the .lean files the verifier elaborates, matched by --entries globs
in the tree committed at --commit (never the working tree). From their `import` headers the closure is
followed through the package's own committed modules and then through the dependencies' sources inside the
cache, so `import Mathlib` requires `Mathlib.olean` AND the .olean of every module Mathlib.lean imports,
transitively. Every external module in the closure must be found in exactly one manifest package of the
cache with its source (to read its imports) and its built .olean and .ilean (regular files); a module no
package provides is a problem unless its root is a toolchain one (Init, Std, Lean, Lake). Every git package
of the manifest must be in the cache, checked out at the manifest's rev (`.git/HEAD`, resolved through
`refs/` or `packed-refs`); a path package, an unreadable pin or a missing package is a problem. Nothing the
template checks inside the job (fresh build, shadowing, restored root modules) is relaxed by passing this."""

from __future__ import annotations

import json
import os
import pathlib
import re
import tarfile
from collections import deque

from . import cli as core

TOOLCHAIN_ROOTS = ("Init", "Std", "Lean", "Lake")
# The .lean files a verifier typically elaborates besides the build: the receipt template's Verify/, the lab
# template's AxiomCheck/TypeCheck/Examples/OwnedAudit files, and Audit/ trees. --entries replaces the list.
DEFAULT_ENTRIES = ("Verify/**/*.lean", "Audit/**/*.lean", "OwnedAudit/**/*.lean", "**/AxiomCheck*.lean",
                   "**/TypeCheck*.lean", "**/Examples*.lean", "**/OwnedAudit*.lean")
PACKAGES = ".lake/packages/"
# Where Lake writes a package's .olean/.ilean: .lake/build/lib/lean (current Lake), .lake/build/lib (earlier
# .lake layouts), build/lib (the old lake-packages layout).
BUILD_LIBS = (".lake/build/lib/lean/", ".lake/build/lib/", "build/lib/")
MAX_SOURCE = 8 * 1024 * 1024          # bytes read of one dependency .lean file (Mathlib.lean is < 1 MiB)
MAX_GIT_FILE = 64 * 1024 * 1024       # bytes read of .git/HEAD, a ref or packed-refs
SHOW_MISSING = 20
JSON_MISSING = 500
HEX40 = re.compile(r"[0-9a-f]{40}")


# --------------------------------------------------------------------------
# import headers

_NAME = re.compile(r"(?:«[^»]*»|[^\s«»()\[\]{},:;/-])+")
_MODIFIERS = {"public", "private", "meta"}


def _skip(text: str, pos: int) -> int:
    """Past whitespace, `--` line comments and (nested) `/- -/` block comments."""
    n = len(text)
    while pos < n:
        c = text[pos]
        if c.isspace():
            pos += 1
        elif text.startswith("--", pos):
            nl = text.find("\n", pos)
            pos = n if nl < 0 else nl + 1
        elif text.startswith("/-", pos):
            depth, pos = 1, pos + 2
            while depth and pos < n:
                if text.startswith("/-", pos):
                    depth, pos = depth + 1, pos + 2
                elif text.startswith("-/", pos):
                    depth, pos = depth - 1, pos + 2
                else:
                    pos += 1
        else:
            break
    return pos


def header_imports(text: str) -> list[str]:
    """The modules a Lean 4 file imports: its header is an optional `module` and `prelude`, then
    `[public|private] [meta] import [all] Name` commands; the first other token ends it."""
    out: list[str] = []
    pos = 0
    while True:
        pos = _skip(text, pos)
        m = _NAME.match(text, pos)
        if not m:
            return out
        tok = m.group(0)
        pos = m.end()
        if tok in ("module", "prelude") or tok in _MODIFIERS:
            continue
        if tok != "import":
            return out
        while True:
            pos = _skip(text, pos)
            m = _NAME.match(text, pos)
            if not m:
                return out
            pos = m.end()
            if m.group(0) not in ("all",) and m.group(0) not in _MODIFIERS:
                out.append(m.group(0).replace("«", "").replace("»", ""))
                break


def module_path(module: str) -> str:
    return module.replace(".", "/")


# --------------------------------------------------------------------------
# entry globs

def glob_regex(pattern: str) -> re.Pattern:
    """`**/` = any number of directories, `*` and `?` within one path component."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


# --------------------------------------------------------------------------
# the staged cache: a tarball (read in one pass, nothing extracted) or a directory

class Cache:
    """What the job's `.lake/packages` would hold: regular-file paths (relative to the directory holding
    `.lake/`), the imports of each dependency .lean source, and the small .git files the pins need."""

    def __init__(self, label: str):
        self.label = label
        self.files: set[str] = set()
        self.imports: dict[str, list[str]] = {}
        self.git: dict[str, bytes] = {}
        self.unreadable: list[str] = []

    def take(self, rel: str, read) -> None:
        """Record one regular file at `rel`; `read(limit)` returns its first bytes."""
        if not rel.startswith(PACKAGES):
            return
        self.files.add(rel)
        rest = rel[len(PACKAGES):]
        pkg, _, inner = rest.partition("/")
        if not inner:
            return
        if inner.startswith(".git/"):
            name = inner[len(".git/"):]
            if name in ("HEAD", "packed-refs") or name.startswith("refs/"):
                self.git[rel] = read(MAX_GIT_FILE)
        elif inner.endswith(".lean") and not inner.startswith((".lake/", "build/", "lake-packages/")):
            data = read(MAX_SOURCE + 1)
            if len(data) > MAX_SOURCE:
                self.unreadable.append(f"{rel}: over {MAX_SOURCE} bytes")
                return
            self.imports[rel] = header_imports(data.decode("utf-8", "replace"))


def _norm(name: str) -> str | None:
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)


def read_tar(path: pathlib.Path) -> Cache:
    cache = Cache(f"tarball {path.name}")
    links: list[tuple[str, str | None]] = []      # a hard link reads as its target, an earlier member
    with tarfile.open(path, "r|*") as tf:
        for member in tf:
            rel = _norm(member.name)
            if rel is None or not (member.isreg() or member.islnk()):
                continue
            if member.islnk():
                links.append((rel, _norm(member.linkname)))
                continue

            def read(limit, member=member):
                f = tf.extractfile(member)
                return f.read(limit) if f is not None else b""
            cache.take(rel, read)
    for rel, target in links:
        if target is None or target not in cache.files:
            cache.unreadable.append(f"{rel}: a hard link to a member that is not a regular file under "
                                    f".lake/packages/ ({target})")
            continue
        data = cache.git.get(target)
        if target in cache.imports:
            cache.take(rel, lambda limit: b"")
            cache.imports[rel] = list(cache.imports[target])
        elif data is not None:
            cache.take(rel, lambda limit, data=data: data[:limit])
        else:
            cache.take(rel, lambda limit: b"")
    return cache


def read_dir(root: pathlib.Path) -> Cache:
    cache = Cache(f"directory {root.name}")
    base = root / ".lake" / "packages"
    if base.is_symlink() or (root / ".lake").is_symlink():
        return cache
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        for fn in filenames:
            p = pathlib.Path(dirpath) / fn
            if p.is_symlink() or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()

            def read(limit, p=p):
                with open(p, "rb") as f:
                    return f.read(limit)
            cache.take(rel, read)
    return cache


# --------------------------------------------------------------------------
# pins

def head_sha(cache: Cache, pkg: str) -> tuple[str | None, str | None]:
    """(sha, None) or (None, why) for `.lake/packages/<pkg>/.git/HEAD`."""
    gitdir = f"{PACKAGES}{pkg}/.git/"
    head = cache.git.get(gitdir + "HEAD")
    if head is None:
        return None, "no .git/HEAD in the cache (a stripped checkout: the pin cannot be checked)"
    text = head.decode("utf-8", "replace").strip()
    if HEX40.fullmatch(text):
        return text, None
    if not text.startswith("ref: "):
        return None, f".git/HEAD is neither a commit nor a ref ({text[:40]!r})"
    ref = text[len("ref: "):].strip()
    loose = cache.git.get(gitdir + ref)
    if loose is not None and HEX40.fullmatch(loose.decode("utf-8", "replace").strip()):
        return loose.decode().strip(), None
    for line in cache.git.get(gitdir + "packed-refs", b"").decode("utf-8", "replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == ref and HEX40.fullmatch(parts[0]):
            return parts[0], None
    return None, f".git/HEAD names {ref}, which the cache does not resolve"


# --------------------------------------------------------------------------
# the closure

class Packages:
    """The manifest's git packages present in the cache: their sources and built files by module path."""

    def __init__(self, cache: Cache, names: list[str]):
        self.cache = cache
        self.names = names
        self.sources: dict[str, dict[str, str]] = {n: {} for n in names}    # pkg -> {module path: rel}
        self.nested: dict[str, dict[str, str]] = {n: {} for n in names}     # one srcDir level down
        for rel in cache.imports:
            pkg, _, inner = rel[len(PACKAGES):].partition("/")
            if pkg not in self.sources:
                continue
            self.sources[pkg][inner[:-len(".lean")]] = rel
            if "/" in inner:
                self.nested[pkg].setdefault(inner.split("/", 1)[1][:-len(".lean")], rel)

    def source(self, pkg: str, mpath: str) -> str | None:
        """The module's source: at the package root, else (only for a module built in this package) one
        srcDir level down, for a lakefile `srcDir`. Never the other way round: Mathlib/Lean/Expr.lean is
        Mathlib.Lean.Expr, not the toolchain's Lean.Expr."""
        if mpath in self.sources[pkg]:
            return self.sources[pkg][mpath]
        return self.nested[pkg].get(mpath) if self.built(pkg, mpath)[".olean"] else None

    def built(self, pkg: str, mpath: str) -> dict[str, bool]:
        for lib in BUILD_LIBS:
            olean = f"{PACKAGES}{pkg}/{lib}{mpath}.olean"
            if olean in self.cache.files:
                return {".olean": True, ".ilean": f"{PACKAGES}{pkg}/{lib}{mpath}.ilean" in self.cache.files}
        return {".olean": False, ".ilean": any(f"{PACKAGES}{pkg}/{lib}{mpath}.ilean" in self.cache.files
                                               for lib in BUILD_LIBS)}

    def provider(self, module: str) -> list[str]:
        """The packages providing `module`: a built .olean or a source at the package root."""
        mpath = module_path(module)
        return [p for p in self.names if mpath in self.sources[p] or self.built(p, mpath)[".olean"]]


def local_modules(files: list[str]) -> dict[str, str]:
    """Module path -> committed .lean path of the package itself (at the package root)."""
    return {f[:-len(".lean")]: f for f in files if f.endswith(".lean") and not f.startswith(".lake/")}


def check(repo: pathlib.Path, commit: str, cache: Cache, entries: list[str]) -> dict:
    """Every problem and check line, the entry files and the missing modules; nothing is written."""
    problems: list[str] = []
    checks: list[str] = []
    ls = core.git(repo, "ls-tree", "-r", "--name-only", "-z", commit, "--", ".")
    files = [f for f in ls.stdout.split("\0") if f] if ls.returncode == 0 else []
    pats = [glob_regex(g) for g in entries]
    entry_files = sorted(f for f in files if f.endswith(".lean") and any(p.match(f) for p in pats))
    if not entry_files:
        problems.append(f"no committed .lean file at {commit[:12]} matches the entries "
                        f"({', '.join(entries)}): nothing to check; pass --entries GLOB for the files the "
                        f"verifier elaborates")
    else:
        checks.append(f"✓ {len(entry_files)} entry file(s) at {commit[:12]}: " + ", ".join(entry_files[:5])
                      + (f" (and {len(entry_files) - 5} more)" if len(entry_files) > 5 else ""))

    manifest_raw = core.git_show_bytes(repo, "./lake-manifest.json", commit)
    names: list[str] = []
    pins: dict[str, str] = {}
    if manifest_raw is None:
        problems.append(f"no lake-manifest.json committed at {commit[:12]}: the dependency pins are unknown")
    else:
        try:
            pkgs = json.loads(manifest_raw.decode("utf-8")).get("packages") or []
        except (ValueError, UnicodeDecodeError, AttributeError):
            pkgs = None
        if not isinstance(pkgs, list):
            problems.append("lake-manifest.json: not a manifest with a `packages` list")
            pkgs = []
        for p in pkgs:
            if not isinstance(p, dict) or not p.get("name"):
                problems.append("lake-manifest.json: a package entry without a name")
                continue
            name = str(p["name"])
            if p.get("type", "git") != "git":
                problems.append(f"manifest package {name}: type {p.get('type')!r} is not restored from a "
                                f"DEPS_TAR (only .lake/packages is), so its modules cannot be checked")
                continue
            names.append(name)
            pins[name] = str(p.get("rev") or "")
    for u in cache.unreadable:
        problems.append(f"{cache.label}: {u}")
    for name in names:
        if not any(f.startswith(f"{PACKAGES}{name}/") for f in cache.files):
            problems.append(f"manifest package {name}: not in the staged cache ({PACKAGES}{name}/ is absent)")
            continue
        sha, why = head_sha(cache, name)
        if why:
            problems.append(f"manifest package {name}: {why}")
        elif sha != pins[name]:
            problems.append(f"manifest package {name}: the cache is at {sha[:12]}, the manifest pins "
                            f"{pins[name][:12] or '(no rev)'}")
        else:
            checks.append(f"✓ {name} at {sha[:12]} = the manifest's rev")
    present = [n for n in names if any(f.startswith(f"{PACKAGES}{n}/") for f in cache.files)]
    deps = Packages(cache, present)

    local = local_modules(files)
    missing: dict[str, dict] = {}
    unresolved: dict[str, str] = {}
    required: set[str] = set()
    seen_local: set[str] = set()
    queue: deque[tuple[str, str, bool]] = deque()    # (module, required by, may be local)

    def file_imports(rel: str) -> list[str]:
        blob = core.git_show_bytes(repo, f"./{rel}", commit)
        return header_imports(blob.decode("utf-8", "replace")) if blob is not None else []

    for f in entry_files:
        queue.extend((m, f, True) for m in file_imports(f))
    while queue:
        module, by, may_be_local = queue.popleft()
        mpath = module_path(module)
        found = deps.provider(module)
        if may_be_local and mpath in local and not found:
            if mpath not in seen_local:
                seen_local.add(mpath)
                queue.extend((m, local[mpath], True) for m in file_imports(local[mpath]))
            continue
        if module in required or module in unresolved:
            continue
        if not found:
            if module.split(".")[0] in TOOLCHAIN_ROOTS:
                continue
            unresolved[module] = by
            continue
        if len(found) > 1:
            problems.append(f"module {module}: provided by more than one manifest package ({', '.join(found)})")
        pkg = found[0]
        required.add(module)
        have = deps.built(pkg, mpath)
        lacking = [k for k, ok in have.items() if not ok]
        if lacking:
            missing[module] = {"module": module, "package": pkg, "files": lacking, "required_by": by}
        src = deps.source(pkg, mpath)
        if src is None:
            problems.append(f"module {module} ({pkg}): no source in the staged cache, so its imports cannot be "
                            f"followed (stage the dependency's checkout with its build)")
            continue
        queue.extend((m, f"{module} ({pkg})", False) for m in cache.imports.get(src, []))

    for module, by in sorted(unresolved.items()):
        problems.append(f"module {module} (imported by {by}): provided by no manifest package in the cache and "
                        f"not a committed module of the package")
    # Modules an entry or a package module imports directly first, then the rest, each by name.
    order = sorted(missing.values(), key=lambda d: (not d["required_by"].endswith(".lean"), d["module"]))
    if order:
        shown = ", ".join(f"{d['module']} ({'/'.join(d['files'])})" for d in order[:SHOW_MISSING])
        more = f" (and {len(order) - SHOW_MISSING} more)" if len(order) > SHOW_MISSING else ""
        problems.append(f"{len(order)} required external module(s) not built in the staged cache: {shown}{more}; "
                        f"build them into the dependency before staging (e.g. `lake build` of the modules the "
                        f"entry files import, such as `lake build {order[0]['module']}`), then stage again")
    if required and not missing:
        checks.append(f"✓ {len(required)} external module(s) in the entries' import closure, each with its "
                      f".olean and .ilean in the {cache.label}")
    return {"entries": entry_files, "required_external_modules": len(required), "missing_count": len(order),
            "missing": order[:JSON_MISSING], "checks": checks, "problems": problems}


def cmd(args) -> int:
    """`receipt slurm --preflight-deps`."""
    if bool(args.deps_tar) == bool(args.deps_dir):
        raise core.UsageError("--preflight-deps needs exactly one of --deps-tar PATH or --deps-dir DIR")
    repo = pathlib.Path(args.repo).resolve() if args.repo else pathlib.Path.cwd()
    rev = args.commit_sha or "HEAD"
    p = core.git(repo, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    commit = p.stdout.strip() if p.returncode == 0 else ""
    if not commit:
        raise core.UsageError(f"--commit {rev}: not a commit in the --repo checkout (default the current directory)")
    problems: list[str] = []
    cache = None
    if args.deps_tar:
        path = pathlib.Path(args.deps_tar)
        if not path.is_file():
            raise core.UsageError(f"--deps-tar {args.deps_tar}: not a file")
        try:
            cache = read_tar(path)
        except (tarfile.TarError, OSError, EOFError) as e:
            problems.append(f"--deps-tar: cannot read the tarball ({type(e).__name__}: {e}); gzip, bzip2, xz and "
                            f"plain tar are read: for another compression pass --deps-dir on an extracted copy")
    else:
        path = pathlib.Path(args.deps_dir)
        if not path.is_dir():
            raise core.UsageError(f"--deps-dir {args.deps_dir}: not a directory")
        cache = read_dir(path)
    result = {"entries": [], "required_external_modules": 0, "missing_count": 0, "missing": [], "checks": [],
              "problems": problems}
    if cache is not None:
        if not any(f.startswith(PACKAGES) for f in cache.files):
            problems.append(f"{cache.label}: no regular file under .lake/packages/ (the job restores only that "
                            f"directory)")
        else:
            entries = list(args.entries) or list(DEFAULT_ENTRIES)
            result = check(repo, commit, cache, entries)
            result["problems"] = problems + result["problems"]
    ok = not result["problems"]
    if getattr(args, "json_out", False):
        core.print_json({"ok": ok, "preflight_deps": True, "commit": commit, **result})
    else:
        n = len(result["problems"])
        print(f"receipt slurm --preflight-deps: {'ok' if ok else f'{n} problem(s)'}; nothing written")
        for c in result["checks"]:
            print(f"  {c}")
        for pr in result["problems"]:
            print(f"  ✗ {pr}")
        if not ok:
            print(f"receipt slurm --preflight-deps: {n} problem(s); do not submit this cache")
    return 0 if ok else 1
