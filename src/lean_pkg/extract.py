# lean-pkg-extract -- plan a standalone public Lean-verification repository from a mixed working repository
"""lean-pkg-extract: materialize a public Lean-verification repository from a mixed TeX+Lean working
repository, with exact provenance. This release has the dry run only: `plan` writes nothing.

`lean-pkg-extract plan --repo SRC --commit COMMIT --manifest PATH [--json] [--report PATH]`

The declaration-level extraction ledger, `ledger` and `recheck` (proposal 0009), lives in
extract_ledger.py; this module parses and dispatches them.

Read-only by construction: it reads git objects at COMMIT (`git ls-tree -r -z --full-tree`,
`git cat-file --batch`), never the working tree, the index or a registry; no network (lazy fetches of
a partial clone are disabled, so a missing object is a refusal). The only write is `--report PATH`,
which must lie outside SRC. Every file under a declared root is classified, default deny; anything
that cannot be classified, any unsafe path or link, a missing or floating pin, an unpinned
dependency, an invalid claims file and an unresolved import is a refusal, all listed together.
Files adopted from another project whose license is not carried into the target are a warning
(manifest `adopted`, and a heuristic for a LICENSE left behind), and so is a disabled Lean source
(`*.lean.<suffix>`) the plan excludes or refuses, which no import check reads; warnings never change
the verdict.

Exit codes: 0 the plan is clean, 1 refused (every reason listed), 2 usage (argparse, SRC is not a
git repository). Output is deterministic: sorted, no timestamps, no host paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import posixpath
import re
import subprocess
import sys
import tomllib

from . import claims as cl
from . import export_paths as xp
from . import lean_scan
from . import public_safety as ps
from .core import scrubbed_env

MANIFEST_SCHEMA = "lean-pkg-extract/1"
PLAN_SCHEMA = "lean-pkg-extract-plan/1"
ROOT_KINDS = ("lean-package", "verification", "docs", "data")
MANIFEST_KEYS = ("schema", "source_commit", "roots", "pins", "exclude", "allow_private", "lean_libs",
                 "dependency_namespaces", "allow_unresolved_imports", "adopted")
REQUIRED_KEYS = ("schema", "roots", "pins")
ROOT_KEYS = ("from", "to", "kind")
PIN_KEYS = ("lean_toolchain", "lake_manifest", "claims", "verification")
REQUIRED_PINS = ("lean_toolchain", "lake_manifest")
RULE_KEYS = ("path", "reason")
LIB_KEYS = ("name", "src_dir", "roots")
UNRESOLVED_KEYS = ("import", "reason")
ADOPTED_KEYS = ("paths", "upstream", "upstream_commit", "upstream_license_file", "license", "license_file",
                "provenance")
ADOPTED_REQUIRED = ("paths", "upstream", "upstream_commit", "upstream_license_file", "license")
SPDX_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]*")
# a license file that governs the files beside and below it (the heuristic for undeclared adoption)
DIR_LICENSE = re.compile(r"^(LICEN[SC]E|COPYING)([.-][A-Za-z0-9.-]+)?$", re.I)
HEX40 = re.compile(r"[0-9a-f]{40}")
# a Lean source kept out of the build by an extra suffix (Foo.lean.disabled, .lean.off, .lean.skip):
# `*.lean.<suffix>` only, never `*.lean`, `*.olean` or `*.ilean`
DISABLED_LEAN = re.compile(r"^[^/]+\.lean\.[^/]+$")
DISABLED_LEAN_DOC = 'docs/extract.md, "Disabled negative fixtures after a namespace or import transport"'

CORE_NAMESPACES = ("Init", "Std", "Lean", "Lake")
# lake-manifest package name -> the root namespaces it provides, where capitalizing the name is wrong.
KNOWN_NAMESPACES = {"proofwidgets": ("ProofWidgets",), "importGraph": ("ImportGraph",)}
FLOATING_TOOLCHAIN = re.compile(r"(^|:)(stable|beta|nightly|latest)$")

GIT_LINK, SYMLINK = "160000", "120000"

# ----------------------------------------------------------------------------------------------------
# classification: default-denied classes first, then what each root kind may carry

LEGAL = re.compile(r"^(LICEN[SC]E|COPYING|NOTICE|README|AUTHORS|CHANGELOG|CONTRIBUTING|CITATION)"
                   r"([.-][A-Za-z0-9.-]+)?$", re.I)
GIT_META = {".gitignore", ".gitattributes"}
LAKE_FILES = {"lakefile.toml", "lakefile.lean", "lake-manifest.json", "lean-toolchain"}
EXT_CLASS = {".lean": "lean-source", ".md": "doc", ".txt": "doc", ".rst": "doc", ".html": "doc",
             ".css": "doc", ".toml": "config", ".json": "config", ".yaml": "config", ".yml": "config",
             ".sh": "script", ".py": "script", ".svg": "image", ".png": "image", ".jpg": "image",
             ".jpeg": "image", ".gif": "image", ".csv": "data", ".tsv": "data", ".expected": "expected-output"}
KIND_EXTS = {
    "lean-package": {".lean", ".md", ".txt", ".toml", ".json", ".yaml", ".yml"},
    "verification": {".lean", ".sh", ".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml", ".expected"},
    "docs": {".md", ".txt", ".rst", ".html", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif"},
    "data": {".json", ".csv", ".tsv", ".txt", ".yaml", ".yml", ".toml", ".md"},
}
REVIEW_DIRS = {"reviews", "prompts", "transcripts"}
MAIL_DIRS = {"mail", "correspondence"}


def deny_class(rel: str) -> tuple[str, str] | None:
    """(class, reason) for a default-denied path, else None. Overridable only by allow_private."""
    parts = pathlib.PurePosixPath(rel).parts
    name, dirs = parts[-1], {d.lower() for d in parts[:-1]}
    low = name.lower()
    if name == ps.PATTERNS_FILE:
        return "private-patterns", f"{ps.PATTERNS_FILE}: the private patterns file is never published"
    if xp.is_receipts_path(rel):
        return "receipts", f"{xp.RECEIPTS_DIR}/: receipts bind the source repository's commits, not the extract"
    if {".lake", ".git"} & set(parts[:-1]):
        return "build-output", ".lake/ or .git/ content"
    if dirs & REVIEW_DIRS or ".transcript" in low or low.endswith(".jsonl"):
        return "review-material", "review packets, prompts, model transcripts, *.jsonl event streams"
    if low.endswith((".eml", ".mbox")) or dirs & MAIL_DIRS:
        return "correspondence", "email and correspondence"
    rule = ps.internal_path(rel)
    if rule:
        return "private-marker", rule
    if name.startswith("TODO-private"):
        return "private-marker", "TODO-private*"
    if low.endswith((".tex", ".bib", ".pdf", ".bbl")):
        return "manuscript", ".tex/.bib/.pdf/.bbl: manuscript material, published only when allowlisted"
    return None


def allow_class(rel: str, kind: str) -> str | None:
    """The class a root of `kind` may carry this file as, else None (unclassified: a refusal)."""
    name = posixpath.basename(rel)
    if LEGAL.match(name):
        return "legal-readme"
    if name in GIT_META:
        return "git-meta"
    if kind == "lean-package" and name in LAKE_FILES:
        return "lake-config"
    if kind == "verification" and name == "Makefile":
        return "script"
    ext = posixpath.splitext(name)[1].lower()
    return EXT_CLASS.get(ext) if ext in KIND_EXTS[kind] else None


# ----------------------------------------------------------------------------------------------------
# git: read-only, no network

class Usage(Exception):
    """Exit 2: argparse-level problems, SRC is not a git repository."""


def _git(repo: pathlib.Path, *args: str, data: bytes | None = None) -> subprocess.CompletedProcess:
    env = scrubbed_env()
    env.update(GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return subprocess.run(["git", "--no-optional-locks", "-c", "protocol.allow=never", "-C", str(repo), *args],
                          input=data, capture_output=True, env=env, check=False)


def resolve_commit(repo: pathlib.Path, rev: str) -> str | None:
    if not rev or rev.startswith("-"):
        return None
    p = _git(repo, "rev-parse", "--verify", "-q", "--end-of-options", rev + "^{commit}")
    out = p.stdout.decode().strip()
    return out if p.returncode == 0 and HEX40.fullmatch(out) else None


def ls_tree(repo: pathlib.Path, sha: str) -> dict[str, dict]:
    p = _git(repo, "ls-tree", "-r", "-l", "-z", "--full-tree", sha)
    if p.returncode != 0:
        raise Usage(f"git ls-tree {sha[:12]} failed: {p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    out = {}
    for item in p.stdout.split(b"\0"):
        if not item:
            continue
        meta, path = item.split(b"\t", 1)
        mode, typ, oid, size = meta.decode().split()
        # size is `-` for a tree or gitlink and `BAD` for a blob missing from the object store (a partial
        # clone): None, and the blob read refuses it
        out[path.decode("utf-8", "surrogateescape")] = {"mode": mode, "type": typ, "oid": oid,
                                                         "size": int(size) if size.isdigit() else None}
    return out


def read_blobs(repo: pathlib.Path, oids: list[str]) -> dict[str, bytes]:
    """Raw blob contents via `git cat-file --batch` (no filters, no textconv); a missing object is
    left out (the caller refuses it)."""
    uniq = sorted(set(oids))
    if not uniq:
        return {}
    p = _git(repo, "cat-file", "--batch", data=("\n".join(uniq) + "\n").encode())
    out, buf, i = {}, p.stdout, 0
    for oid in uniq:
        nl = buf.find(b"\n", i)
        if p.returncode != 0 or nl < 0:
            break
        head = buf[i:nl].decode().split()
        i = nl + 1
        if len(head) < 3 or head[1] == "missing":
            continue
        size = int(head[2])
        out[oid] = buf[i:i + size]
        i += size + 1
    return out


# ----------------------------------------------------------------------------------------------------
# the manifest: strict JSON

def _no_dups(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate key {k!r}")
        seen[k] = v
    return seen


def _bad_constant(name):
    raise ValueError(f"non-standard JSON constant {name}")


def load_json_strict(data: bytes, where: str) -> tuple[object, str | None]:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_no_dups, parse_constant=_bad_constant), None
    except UnicodeDecodeError:
        return None, f"{where}: not UTF-8"
    except ValueError as e:
        return None, f"{where}: not strict JSON ({e})"


def _keys(obj, allowed, required, where, refusals) -> bool:
    if not isinstance(obj, dict):
        refusals.append(f"{where} must be an object")
        return False
    extra = sorted(set(obj) - set(allowed))
    missing = [k for k in required if k not in obj]
    if extra:
        refusals.append(f"{where}: unknown key(s) {extra} (allowed: {list(allowed)})")
    if missing:
        refusals.append(f"{where}: missing key(s) {missing}")
    return not extra and not missing


def _reason(v) -> bool:
    return isinstance(v, str) and bool(v.strip()) and "\n" not in v


def validate_manifest(m, commit: str, refusals: list[str]) -> dict | None:
    """The manifest normalized, or None when it is too broken to plan with (refusals say why)."""
    if not _keys(m, MANIFEST_KEYS, REQUIRED_KEYS, "manifest", refusals):
        return None
    ok = True
    if m["schema"] != MANIFEST_SCHEMA:
        refusals.append(f"manifest.schema must be {MANIFEST_SCHEMA!r}, got {m['schema']!r}")
        ok = False
    sc = m.get("source_commit")
    if sc is not None and not (isinstance(sc, str) and HEX40.fullmatch(sc)):
        refusals.append(f"manifest.source_commit must be a full 40-hex commit, got {sc!r}")
    elif sc is not None and sc != commit:
        refusals.append(f"manifest.source_commit {sc} differs from --commit {commit}")
    roots = []
    if not isinstance(m["roots"], list) or not m["roots"]:
        refusals.append("manifest.roots must be a non-empty list")
        ok = False
    else:
        for i, r in enumerate(m["roots"]):
            w = f"roots[{i}]"
            if not _keys(r, ROOT_KEYS, ROOT_KEYS, w, refusals):
                ok = False
                continue
            for k in ("from", "to"):
                why = xp.bad_rel_path(r[k], allow_dot=True)
                if why:
                    refusals.append(f"{w}.{k} {r[k]!r} {why}")
                    ok = False
            if r["kind"] not in ROOT_KINDS:
                refusals.append(f"{w}.kind must be one of {list(ROOT_KINDS)}, got {r['kind']!r}")
                ok = False
            roots.append(dict(r, index=i))
    pins = m["pins"]
    if _keys(pins, PIN_KEYS, REQUIRED_PINS, "pins", refusals):
        for k in ("lean_toolchain", "lake_manifest", "claims"):
            if k in pins and xp.bad_rel_path(pins[k]):
                refusals.append(f"pins.{k} {pins[k]!r} {xp.bad_rel_path(pins[k])}")
                ok = False
        ver = pins.get("verification", [])
        if not isinstance(ver, list):
            refusals.append("pins.verification must be a list of paths")
            ok = False
        else:
            for j, v in enumerate(ver):
                if xp.bad_rel_path(v):
                    refusals.append(f"pins.verification[{j}] {v!r} {xp.bad_rel_path(v)}")
                    ok = False
    else:
        ok = False
    rules = {}
    for key in ("exclude", "allow_private"):
        lst = m.get(key, [])
        rules[key] = []
        if not isinstance(lst, list):
            refusals.append(f"manifest.{key} must be a list")
            continue
        for i, r in enumerate(lst):
            w = f"{key}[{i}]"
            if not _keys(r, RULE_KEYS, RULE_KEYS, w, refusals):
                continue
            if not isinstance(r["path"], str) or not r["path"] or ".." in r["path"].split("/"):
                refusals.append(f"{w}.path must be a path glob without `..`, got {r['path']!r}")
                continue
            if not _reason(r["reason"]):
                refusals.append(f"{w}.reason must be a one-line, non-empty string")
                continue
            rules[key].append({"index": i, "path": r["path"], "reason": r["reason"].strip(),
                               "rx": ps.glob_regex(r["path"])})
    libs = None
    if "lean_libs" in m:
        libs = []
        if not isinstance(m["lean_libs"], list) or not m["lean_libs"]:
            refusals.append("manifest.lean_libs must be a non-empty list")
        else:
            for i, lib in enumerate(m["lean_libs"]):
                w = f"lean_libs[{i}]"
                if not _keys(lib, LIB_KEYS, ("name",), w, refusals):
                    continue
                src_dir = lib.get("src_dir", ".")
                lib_roots = lib.get("roots", [lib["name"]])
                if not isinstance(lib["name"], str) or not lean_scan.is_name(lib["name"]):
                    refusals.append(f"{w}.name must be a Lean name, got {lib['name']!r}")
                elif xp.bad_rel_path(src_dir, allow_dot=True):
                    refusals.append(f"{w}.src_dir {src_dir!r} {xp.bad_rel_path(src_dir, allow_dot=True)}")
                elif not (isinstance(lib_roots, list) and lib_roots
                          and all(isinstance(x, str) and lean_scan.is_name(x) for x in lib_roots)):
                    refusals.append(f"{w}.roots must be a non-empty list of Lean module names")
                else:
                    libs.append({"name": lib["name"], "src_dir": src_dir, "roots": list(lib_roots), "globs": []})
    dep_ns = m.get("dependency_namespaces", {})
    if not (isinstance(dep_ns, dict) and all(
            isinstance(v, list) and v and all(isinstance(x, str) and lean_scan.is_name(x) for x in v)
            for v in dep_ns.values())):
        refusals.append("manifest.dependency_namespaces must map a lake-manifest package name to a "
                        "non-empty list of root namespaces")
        dep_ns = {}
    unresolved = []
    lst = m.get("allow_unresolved_imports", [])
    if not isinstance(lst, list):
        refusals.append("manifest.allow_unresolved_imports must be a list")
    else:
        for i, r in enumerate(lst):
            w = f"allow_unresolved_imports[{i}]"
            if not _keys(r, UNRESOLVED_KEYS, UNRESOLVED_KEYS, w, refusals):
                continue
            if not (isinstance(r["import"], str) and lean_scan.is_name(r["import"])):
                refusals.append(f"{w}.import must be a Lean module name, got {r['import']!r}")
            elif not _reason(r["reason"]):
                refusals.append(f"{w}.reason must be a one-line, non-empty string")
            else:
                unresolved.append({"index": i, "import": r["import"], "reason": r["reason"].strip()})
    adopted = []
    lst = m.get("adopted", [])
    if not isinstance(lst, list):
        refusals.append("manifest.adopted must be a list")
    else:
        for i, a in enumerate(lst):
            w = f"adopted[{i}]"
            if not _keys(a, ADOPTED_KEYS, ADOPTED_REQUIRED, w, refusals):
                continue
            bad = []
            paths = a["paths"]
            if not (isinstance(paths, list) and paths and all(
                    isinstance(g, str) and g and ".." not in g.split("/") for g in paths)):
                bad.append(f"{w}.paths must be a non-empty list of path globs without `..`")
            if not _reason(a["upstream"]):
                bad.append(f"{w}.upstream must be a one-line, non-empty string (the upstream repository URL)")
            if not (isinstance(a["upstream_commit"], str) and HEX40.fullmatch(a["upstream_commit"])):
                bad.append(f"{w}.upstream_commit must be the full 40-hex upstream commit, got "
                           f"{a['upstream_commit']!r}")
            if not (isinstance(a["upstream_license_file"], str) and not xp.bad_rel_path(a["upstream_license_file"])):
                bad.append(f"{w}.upstream_license_file must be a relative path in the upstream repository, "
                           f"got {a['upstream_license_file']!r}")
            if not (isinstance(a["license"], str) and SPDX_ID.fullmatch(a["license"])):
                bad.append(f"{w}.license must be an SPDX identifier (MIT, Apache-2.0, LicenseRef-...), "
                           f"got {a['license']!r}")
            for k in ("license_file", "provenance"):
                if k in a and not (isinstance(a[k], str) and not xp.bad_rel_path(a[k])):
                    bad.append(f"{w}.{k} must be a relative path in the source repository, got {a[k]!r}")
            if bad:
                refusals.extend(bad)
                continue
            adopted.append({"index": i, "paths": list(paths), "rxs": [ps.glob_regex(g) for g in paths],
                            "upstream": a["upstream"].strip(), "upstream_commit": a["upstream_commit"],
                            "upstream_license_file": a["upstream_license_file"], "license": a["license"],
                            "license_file": a.get("license_file"), "provenance": a.get("provenance")})
    if not ok:
        return None
    return {"roots": roots, "pins": pins, "exclude": rules["exclude"], "allow_private": rules["allow_private"],
            "lean_libs": libs, "dependency_namespaces": dep_ns, "allow_unresolved_imports": unresolved,
            "adopted": adopted}


# ----------------------------------------------------------------------------------------------------
# Lake: libraries, dependencies, imports

def toml_libs(text: str, where: str, refusals: list[str]) -> list[dict] | None:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        refusals.append(f"{where}: not valid TOML ({e})")
        return None
    pkg_src = doc.get("srcDir", ".")
    libs = []
    for kind, tables in (("lean_lib", doc.get("lean_lib", [])), ("lean_exe", doc.get("lean_exe", []))):
        for t in tables if isinstance(tables, list) else []:
            name = t.get("name")
            if not isinstance(name, str):
                refusals.append(f"{where}: a [[{kind}]] without a name")
                continue
            src_dir = posixpath.normpath(t.get("srcDir", pkg_src))
            roots = t.get("roots", [name]) if kind == "lean_lib" else [t.get("root", name)]
            globs = t.get("globs", []) if kind == "lean_lib" else []
            if xp.bad_rel_path(src_dir, allow_dot=True):
                refusals.append(f"{where}: [[{kind}]] {name} srcDir {src_dir!r} {xp.bad_rel_path(src_dir, True)}")
                continue
            libs.append({"name": name, "src_dir": src_dir, "roots": [str(r) for r in roots],
                         "globs": [str(g) for g in globs]})
    return libs


def lib_owns(lib: dict, mod: str) -> bool:
    """Lake's `LeanLib.isLocalModule`: a root is a name prefix of mod, or a glob matches it."""
    if any(mod == r or mod.startswith(r + ".") for r in lib["roots"]):
        return True
    for g in lib["globs"]:
        if g.endswith(".+") and mod.startswith(g[:-2] + "."):
            return True
        if g.endswith(".*") and (mod == g[:-2] or mod.startswith(g[:-2] + ".")):
            return True
        if mod == g:
            return True
    return False


_IMPORT = re.compile(r"^[ \t]*(?:(?:public|private)[ \t]+)?(?:meta[ \t]+)?import[ \t]+(?:all[ \t]+)?([^\s]+)", re.M)


def imports(text: str) -> list[str]:
    masked = lean_scan._mask(text)[0]
    return [m.replace("«", "").replace("»", "") for m in _IMPORT.findall(masked)]


def dep_namespaces(name: str, override: dict) -> list[str]:
    if name in override:
        return list(override[name])
    if name in KNOWN_NAMESPACES:
        return list(KNOWN_NAMESPACES[name])
    return sorted({name, name[:1].upper() + name[1:]})


# ----------------------------------------------------------------------------------------------------
# the plan

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _to_path(root: dict, src: str) -> str:
    """The output path of `src` under `root` (a file root maps to its `to` itself)."""
    if src == root["from"]:
        return root["to"]
    rel = src if root["from"] == "." else src[len(root["from"]) + 1:]
    return rel if root["to"] == "." else f"{root['to']}/{rel}"


def _depth(p: str) -> int:
    return 0 if p == "." else p.count("/") + 1


def build_plan(repo: pathlib.Path, commit_arg: str, manifest_bytes: bytes) -> dict:
    refusals: list[str] = []
    plan = {"schema": PLAN_SCHEMA, "ok": False, "source": {"commit": None,
            "manifest_sha256": _sha(manifest_bytes)}, "roots": [], "included": [], "excluded": [],
            "allowed_private": [], "weakened": [], "modules": [], "unowned_lean_files": [], "claims": [],
            "pins": {}, "adopted": [], "refusals": refusals, "warnings": [], "totals": {}}
    commit = resolve_commit(repo, commit_arg)
    if not commit:
        refusals.append(f"--commit {commit_arg!r} is not a commit of the source repository")
        return _finish(plan)
    plan["source"]["commit"] = commit
    raw, err = load_json_strict(manifest_bytes, "manifest")
    if err:
        refusals.append(err)
        return _finish(plan)
    man = validate_manifest(raw, commit, refusals)
    if man is None:
        return _finish(plan)
    tree = ls_tree(repo, commit)
    dirs = {"."}
    for p in tree:
        d = posixpath.dirname(p)
        while d and d not in dirs:
            dirs.add(d)
            d = posixpath.dirname(d)
    roots = man["roots"]
    plan["roots"] = [{"from": r["from"], "to": r["to"], "kind": r["kind"]} for r in roots]

    # roots: each `from` exists; no two roots share a `from` or a `to`
    froms, tos = {}, {}
    for r in roots:
        if r["from"] not in tree and r["from"] not in dirs:
            refusals.append(f"roots[{r['index']}].from {r['from']!r} does not exist at {commit[:12]}")
        elif r["from"] in tree and r["to"] == ".":
            refusals.append(f"roots[{r['index']}]: a file root needs a file `to`, not `.`")
        if r["from"] in froms:
            refusals.append(f"roots[{r['index']}].from {r['from']!r} repeats roots[{froms[r['from']]}].from")
        if r["to"] in tos:
            refusals.append(f"roots[{r['index']}].to {r['to']!r} overlaps roots[{tos[r['to']]}].to (same target)")
        froms.setdefault(r["from"], r["index"])
        tos.setdefault(r["to"], r["index"])

    def owner(path: str) -> dict | None:            # the deepest root containing path; nested roots carve out
        cands = [r for r in roots if xp.under(path, r["from"])]
        return max(cands, key=lambda r: (_depth(r["from"]), -r["index"])) if cands else None

    root_at = {r["index"]: r for r in roots}

    pins = man["pins"]
    pin_role = {pins["lean_toolchain"]: "lean_toolchain", pins["lake_manifest"]: "lake_manifest"}
    if pins.get("claims"):
        pin_role[pins["claims"]] = "claims"
    for v in pins.get("verification", []):
        pin_role.setdefault(v, "verification")

    used_allow: set[int] = set()
    included: dict[str, dict] = {}                   # to-path -> entry
    by_src: dict[str, dict] = {}                     # from-path -> entry (included only)
    excluded: list[dict] = []
    unclassified: list[str] = []                     # under a root, refused as unclassified
    symlinks: list[dict] = []
    for path in sorted(tree):
        r = owner(path)
        if r is None:
            continue
        ent = tree[path]
        to = _to_path(r, path)
        if ent["mode"] == GIT_LINK:
            refusals.append(f"{path}: a gitlink (submodule) at {commit[:12]}; submodules are refused, "
                            f"not dropped (vendor the files or exclude the path)")
            continue
        rule = next((x for x in man["exclude"] if x["rx"].match(path)), None)
        if rule:
            excluded.append({"from": path, "class": "manifest-exclude", "reason": rule["reason"]})
            continue
        cls = None
        denied = deny_class(path)
        if denied:
            allow = next((x for x in man["allow_private"] if x["rx"].match(path)), None)
            if not allow:
                excluded.append({"from": path, "class": denied[0], "reason": denied[1]})
                continue
            used_allow.add(allow["index"])
            cls = "allowed-private"
            plan["allowed_private"].append({"to": to, "from": path, "denied_class": denied[0],
                                            "glob": allow["path"], "reason": allow["reason"]})
        if cls is None and path in pin_role:
            cls = "pin:" + pin_role[path]
        if cls is None:
            cls = allow_class(path, r["kind"])
        if cls is None:
            unclassified.append(path)
            refusals.append(f"{path}: unclassified in a {r['kind']} root (default deny); exclude it, move it, "
                            f"or put it in a root whose kind carries it")
            continue
        e = {"to": to, "from": path, "size": ent["size"], "sha256": None, "blob": ent["oid"],
             "mode": ent["mode"], "class": cls, "root": r["index"]}
        if to in included:
            refusals.append(f"{path} and {included[to]['from']} both map to {to!r} (overlapping targets)")
            continue
        included[to] = e
        by_src[path] = e
        if ent["mode"] == SYMLINK:
            symlinks.append(e)

    # interleaved targets: an output file that is also an output directory, or an outer root writing
    # inside an inner root's target directory
    to_dirs = {}
    for to, e in included.items():
        d = posixpath.dirname(to)
        while d:
            to_dirs.setdefault(d, e)
            d = posixpath.dirname(d)
    for to, e in sorted(included.items()):
        if to in to_dirs:
            refusals.append(f"{to!r} is both a file (from {e['from']}) and a directory (from "
                            f"{to_dirs[to]['from']}) in the output (overlapping targets)")
        for r in roots:
            outer_to = root_at[e["root"]]["to"]
            if r["index"] != e["root"] and xp.under(to, r["to"]) and xp.under(r["to"], outer_to):
                refusals.append(f"{e['from']} maps to {to!r}, inside roots[{r['index']}].to {r['to']!r} "
                                f"(overlapping targets)")

    for rule in man["allow_private"]:
        if rule["index"] not in used_allow:
            refusals.append(f"allow_private[{rule['index']}] {rule['path']!r} matches no default-denied file "
                            f"under the roots (a stale allowance is refused)")

    blobs = read_blobs(repo, [e["blob"] for e in included.values()])
    for e in included.values():
        b = blobs.get(e["blob"])
        if b is None:
            refusals.append(f"{e['from']}: blob {e['blob'][:12]} is not in the object store (partial clone?)")
            continue
        e["sha256"] = _sha(b)

    def text_of(src: str) -> str | None:
        e = by_src.get(src)
        b = blobs.get(e["blob"]) if e else None
        return None if b is None else b.decode("utf-8", "replace")

    # symlinks: relative, inside their root, pointing at an included file (or a directory of them)
    excluded_src = {x["from"] for x in excluded}
    for e in symlinks:
        target = text_of(e["from"]) or ""
        res, why = xp.link_target(e["from"], target)
        if why:
            refusals.append(f"{e['from']}: {why}")
            continue
        r = root_at[e["root"]]
        if not xp.under(res, r["from"]):
            refusals.append(f"{e['from']}: symlink target {target!r} leaves its root {r['from']!r}")
        elif res in excluded_src or any(xp.under(x, res) for x in excluded_src):
            refusals.append(f"{e['from']}: symlink target {target!r} is (or contains) an excluded file")
        elif res not in by_src and res not in dirs:
            refusals.append(f"{e['from']}: symlink target {target!r} does not exist at {commit[:12]}")
        elif res in dirs and not any(xp.under(s, res) for s in by_src):
            refusals.append(f"{e['from']}: symlink target {target!r} has no included file")

    # pins
    pin_out = {}
    for role, src in [("lean_toolchain", pins["lean_toolchain"]), ("lake_manifest", pins["lake_manifest"])] + \
            ([("claims", pins["claims"])] if pins.get("claims") else []) + \
            [("verification", v) for v in pins.get("verification", [])]:
        e = by_src.get(src)
        if src not in tree:
            refusals.append(f"pins.{role} {src}: missing at {commit[:12]}")
            continue
        if e is None:
            why = next((f"{x['class']}: {x['reason']}" for x in excluded if x["from"] == src), "outside every root")
            refusals.append(f"pins.{role} {src}: not included ({why})")
            continue
        if e["mode"] == SYMLINK:
            refusals.append(f"pins.{role} {src}: a symlink; pin the file itself")
            continue
        rec = {"from": src, "to": e["to"], "sha256": e["sha256"], "blob": e["blob"]}
        if role == "verification":
            pin_out.setdefault("verification", []).append(rec)
        else:
            pin_out[role] = rec
    plan["pins"] = pin_out
    if "verification" in pin_out:
        pin_out["verification"].sort(key=lambda x: x["to"])

    tc = pin_out.get("lean_toolchain")
    if tc:
        value = (text_of(tc["from"]) or "").strip()
        tc["value"] = value
        if not value or "\n" in value or FLOATING_TOOLCHAIN.search(value):
            refusals.append(f"pins.lean_toolchain {tc['from']}: {value!r} is not a pinned toolchain "
                            f"(a floating channel or empty)")

    # lake-manifest: every dependency a git dep at a full SHA, or a path dep inside the included roots
    dep_ns: set[str] = set()
    lm = pin_out.get("lake_manifest")
    pkgdir = posixpath.dirname(pins["lake_manifest"]) or "."
    if lm:
        doc, err = load_json_strict((text_of(lm["from"]) or "").encode(), f"pins.lake_manifest {lm['from']}")
        pkgs = doc.get("packages") if isinstance(doc, dict) else None
        if err or not isinstance(pkgs, list):
            refusals.append(err or f"pins.lake_manifest {lm['from']}: no `packages` list")
            pkgs = []
        lm["packages"] = []
        names = set()
        for p in pkgs:
            if isinstance(p, dict) and "type" not in p and len(p) == 1 and next(iter(p)) in ("git", "path"):
                p = dict(next(iter(p.values())), type=next(iter(p)))      # the pre-v7 nested form
            if not isinstance(p, dict):
                refusals.append("pins.lake_manifest: a package entry is not an object")
                continue
            name, typ = p.get("name"), p.get("type")
            names.add(name)
            row = {"name": name, "type": typ}
            if typ == "git":
                rev = p.get("rev")
                row.update(url=p.get("url"), rev=rev, inputRev=p.get("inputRev"))
                if not (isinstance(rev, str) and HEX40.fullmatch(rev)):
                    refusals.append(f"lake-manifest package {name!r}: git rev {rev!r} is not a full 40-hex SHA")
            elif typ == "path":
                d = p.get("dir")
                row["dir"] = d
                full = posixpath.normpath(posixpath.join(pkgdir, d)) if isinstance(d, str) else None
                if full is None or full == ".." or full.startswith("../") or (isinstance(d, str) and d.startswith("/")):
                    refusals.append(f"lake-manifest package {name!r}: path dependency {d!r} leaves the repository")
                elif not any(xp.under(s, full) for s in by_src):
                    refusals.append(f"lake-manifest package {name!r}: path dependency {d!r} is not inside "
                                    f"the included roots")
            else:
                refusals.append(f"lake-manifest package {name!r}: type {typ!r} is neither git (pinned) nor path")
            if isinstance(name, str):
                dep_ns.update(dep_namespaces(name, man["dependency_namespaces"]))
            lm["packages"].append(row)
        lm["packages"].sort(key=lambda x: str(x["name"]))
        for k in sorted(set(man["dependency_namespaces"]) - names):
            refusals.append(f"dependency_namespaces names {k!r}, which is not a lake-manifest package")

    # libraries and the module map
    libs = None
    lakefile_toml, lakefile_lean = (posixpath.join(pkgdir, n) if pkgdir != "." else n
                                    for n in ("lakefile.toml", "lakefile.lean"))
    if lakefile_lean in tree:
        if lakefile_lean not in by_src:
            refusals.append(f"{lakefile_lean}: not included")
        if man["lean_libs"] is None:
            refusals.append(f"{lakefile_lean}: a lakefile.lean package must declare its libraries in the "
                            f"manifest (lean_libs), since lean-pkg-extract does not run Lean")
        libs = man["lean_libs"]
    elif lakefile_toml in tree:
        if man["lean_libs"] is not None:
            refusals.append("manifest.lean_libs is only for lakefile.lean packages; this package has "
                            f"{lakefile_toml}, which is read instead")
        if lakefile_toml not in by_src:
            refusals.append(f"{lakefile_toml}: not included")
        else:
            libs = toml_libs(text_of(lakefile_toml) or "", lakefile_toml, refusals)
    elif lm:
        refusals.append(f"no lakefile.toml or lakefile.lean beside pins.lake_manifest ({pkgdir})")
    libs = libs or []

    lean_files = sorted(s for s in by_src if s.endswith(".lean") and by_src[s]["mode"] != SYMLINK)
    module_of: dict[str, dict] = {}
    for src in lean_files:
        if src in (lakefile_lean,):
            continue
        owners = []
        for lib in libs:
            base = posixpath.normpath(posixpath.join(pkgdir, lib["src_dir"]))
            if xp.under(src, base) and src != base:
                rel = src if base == "." else src[len(base) + 1:]
                mod = rel[:-len(".lean")].replace("/", ".")
                if lib_owns(lib, mod):
                    owners.append((lib["name"], mod))
        if len(owners) > 1:
            refusals.append(f"{src}: owned by more than one library ({', '.join(n for n, _ in owners)})")
        if owners:
            name, mod = owners[0]
            if mod in module_of:
                refusals.append(f"module {mod}: two files ({module_of[mod]['from']}, {src})")
            module_of[mod] = {"module": mod, "file": by_src[src]["to"], "from": src, "lib": name}
        else:
            plan["unowned_lean_files"].append(by_src[src]["to"])
    plan["modules"] = sorted(module_of.values(), key=lambda x: x["module"])

    # imports: an included module, core, a pinned dependency's namespace, or an explicit weakening
    allow_unres = {x["import"]: x for x in man["allow_unresolved_imports"]}
    used_unres: set[str] = set()
    for src in lean_files:
        for mod in imports(text_of(src) or ""):
            top = mod.split(".")[0]
            if top in CORE_NAMESPACES or mod in module_of:
                continue
            local = next((lib for lib in libs if lib_owns(lib, mod)), None)
            if local is not None:
                refusals.append(f"{src}: imports {mod}, a module of library {local['name']} whose file is "
                                f"not included")
                continue
            if top in dep_ns:
                continue
            if mod in allow_unres:
                used_unres.add(mod)
                continue
            refusals.append(f"{src}: imports {mod}, which resolves to no included module and no pinned "
                            f"dependency namespace")
    for mod, x in sorted(allow_unres.items()):
        if mod in used_unres:
            plan["weakened"].append({"rule": "allow_unresolved_imports", "import": mod, "reason": x["reason"]})
        else:
            refusals.append(f"allow_unresolved_imports[{x['index']}] {mod!r} is never imported unresolved "
                            f"(a stale allowance is refused)")

    # claims: the claims check schema, and every claim's file included
    cp = pin_out.get("claims")
    if cp:
        text = text_of(cp["from"]) or ""
        _count, errors = cl.schema_check_text(text, cp["from"])
        refusals.extend(f"pins.claims {cp['from']}: {e}" for e in errors)
        if not errors:
            base = posixpath.dirname(cp["to"])
            doc = cl.load_claims_text(text, cp["from"])
            files = {}
            for c in doc["claims"]:
                out = posixpath.normpath(posixpath.join(base, c["file"])) if base else c["file"]
                e = included.get(out)
                if e is None:
                    refusals.append(f"claim {c['id']}: file {c['file']} (output {out}) is not included")
                    continue
                files[c["file"]] = (blobs.get(e["blob"]) or b"").decode("utf-8", "replace")
                mod = next((m["module"] for m in module_of.values() if m["file"] == out), None)
                plan["claims"].append({"id": c["id"], "decl": c["decl"], "file": out, "module": mod})
            ok_claims = [c for c in doc["claims"] if c["file"] in files]
            refusals.extend(f"claim {p}" for p in cl.decl_problems(files.get, ok_claims))
            plan["claims"].sort(key=lambda x: x["id"])

    license_checks(plan, man["adopted"], tree, by_src, excluded, blobs, commit)
    disabled_lean_check(plan, excluded, unclassified)

    plan["included"] = [included[k] for k in sorted(included)]
    plan["excluded"] = sorted(excluded, key=lambda x: x["from"])
    plan["allowed_private"].sort(key=lambda x: x["to"])
    plan["unowned_lean_files"].sort()
    return _finish(plan)


# ----------------------------------------------------------------------------------------------------
# licenses of adopted (vendored) files: warnings, never refusals

LIST_CAP = 10


def _listed(paths: list[str]) -> str:
    """paths joined, at most LIST_CAP of them, the rest counted (the cap is stated, never silent)."""
    head = ", ".join(paths[:LIST_CAP])
    return head if len(paths) <= LIST_CAP else f"{head} (and {len(paths) - LIST_CAP} more)"


def scoped_license_name(upstream: str, spdx: str) -> str:
    """The REUSE-valid scoped license path recommended for an upstream: LICENSES/LicenseRef-<Upstream>-<SPDX>.txt
    (a LicenseRef- id, because the file keeps the upstream's own copyright line byte for byte)."""
    if spdx.startswith("LicenseRef-"):
        return f"LICENSES/{spdx}.txt"
    tail = upstream.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    slug = re.sub(r"[^A-Za-z0-9.-]+", "-", tail).strip("-.") or "Upstream"
    return f"LICENSES/LicenseRef-{slug}-{spdx}.txt"


def _why_not_carried(src: str, tree: dict, excluded: list[dict], commit: str) -> str:
    if src not in tree:
        return f"missing at {commit[:12]}"
    x = next((x for x in excluded if x["from"] == src), None)
    return f"excluded, {x['class']}: {x['reason']}" if x else "outside every root"


def license_checks(plan: dict, adopted: list[dict], tree: dict, by_src: dict, excluded: list[dict],
                   blobs: dict, commit: str) -> None:
    """Warn when included files come from a source whose license the extract does not carry.

    Declared adoption (manifest `adopted`): the upstream license must be carried as an included
    `license_file`, and an optional `provenance` list must name every adopted file. Undeclared: an
    included file whose nearest LICENSE/COPYING (in its own source directory or above) is not
    included. Warnings only: the plan's verdict and exit code do not change."""
    warn = plan["warnings"]
    covered: set[str] = set()
    for a in adopted:
        own = {a["license_file"], a["provenance"]} - {None}
        files = sorted((e for s, e in by_src.items() if s not in own and any(rx.match(s) for rx in a["rxs"])),
                       key=lambda e: e["to"])
        covered.update(e["from"] for e in files)
        w = f"adopted[{a['index']}]"
        up = f"{a['upstream']} @ {a['upstream_commit'][:12]} ({a['license']}, {a['upstream_license_file']})"
        tos = [e["to"] for e in files]
        rec = {"index": a["index"], "paths": a["paths"], "upstream": a["upstream"],
               "upstream_commit": a["upstream_commit"], "upstream_license_file": a["upstream_license_file"],
               "license": a["license"], "files": tos, "license_file": None, "provenance": None}
        if not files:
            warn.append(f"{w} {a['paths']}: matches no included file (a stale adoption entry)")
        lf = a["license_file"]
        if lf is None:
            if files:
                warn.append(f"{w}: {len(files)} file(s) adopted from {up} are extracted without the upstream "
                            f"license: {_listed(tos)}. Copy {a['upstream_license_file']} at "
                            f"{a['upstream_commit'][:12]} byte for byte to "
                            f"{scoped_license_name(a['upstream'], a['license'])}, list the files it covers, "
                            f"and name it in license_file")
        elif lf not in by_src:
            warn.append(f"{w}: license_file {lf} is not carried into the target "
                        f"({_why_not_carried(lf, tree, excluded, commit)}); the {len(files)} file(s) adopted "
                        f"from {up} would ship without their license: {_listed(tos)}")
        else:
            to = by_src[lf]["to"]
            rec["license_file"] = {"from": lf, "to": to, "sha256": by_src[lf]["sha256"]}
            parent, stem = posixpath.basename(posixpath.dirname(to)), posixpath.splitext(posixpath.basename(to))[0]
            if parent == "LICENSES" and stem != a["license"] and not stem.startswith("LicenseRef-"):
                warn.append(f"{w}: license_file {to} is under LICENSES/ but its name is neither the SPDX id "
                            f"{a['license']!r} nor a LicenseRef- id; `reuse lint` reports it as a bad license. Name it "
                            f"{scoped_license_name(a['upstream'], a['license'])} and use that id in REUSE.toml")
        pv = a["provenance"]
        if pv is not None:
            if pv not in by_src:
                warn.append(f"{w}: provenance {pv} is not carried into the target "
                            f"({_why_not_carried(pv, tree, excluded, commit)})")
            else:
                text = (blobs.get(by_src[pv]["blob"]) or b"").decode("utf-8", "replace")
                missing = [e["to"] for e in files if e["to"] not in text and e["from"] not in text]
                if missing:
                    warn.append(f"{w}: provenance {by_src[pv]['to']} does not list {len(missing)} adopted "
                                f"file(s): {_listed(missing)}")
                rec["provenance"] = {"from": pv, "to": by_src[pv]["to"], "sha256": by_src[pv]["sha256"]}
        plan["adopted"].append(rec)

    # undeclared: the nearest license file above an included file is not carried
    licenses: dict[str, list[str]] = {}
    for path in tree:
        if DIR_LICENSE.match(posixpath.basename(path)) and tree[path]["mode"] != GIT_LINK:
            licenses.setdefault(posixpath.dirname(path), []).append(path)
    license_paths = {p for group in licenses.values() for p in group}
    orphaned: dict[tuple[str, ...], list[str]] = {}
    for src, e in by_src.items():
        if src in covered or src in license_paths:
            continue
        d = posixpath.dirname(src)
        while True:
            if d in licenses:
                if not any(p in by_src for p in licenses[d]):
                    orphaned.setdefault(tuple(sorted(licenses[d])), []).append(e["to"])
                break
            if not d:
                break
            d = posixpath.dirname(d)
    for lic, tos in sorted(orphaned.items()):
        where = "the source repository's license" if not posixpath.dirname(lic[0]) else "the license"
        whys = "; ".join(f"{p}: {_why_not_carried(p, tree, excluded, commit)}" for p in lic)
        warn.append(f"{', '.join(lic)}: {where} over {len(tos)} included file(s) is not carried into the target "
                    f"({whys}): {_listed(sorted(tos))}. If these files were adopted from another project, "
                    f"declare them under `adopted` and carry the upstream license as a scoped LICENSES/ file; "
                    f"otherwise include or replace the license deliberately")


def disabled_lean_check(plan: dict, excluded: list[dict], unclassified: list[str]) -> None:
    """Warn about `*.lean.<suffix>` files under a root that the plan excludes or refuses as unclassified.

    The module inventory, unowned_lean_files and the import check read `*.lean` only, so a disabled
    negative fixture with a stale `open` or `import` (after a namespace or import transport) would go
    unreported. A warning: the plan's verdict and exit code do not change."""
    names = sorted({x["from"] for x in excluded} | set(unclassified))
    hits = [p for p in names if DISABLED_LEAN.match(posixpath.basename(p))]
    if hits:
        plan["warnings"].append(
            f"{len(hits)} disabled Lean source file(s) (*.lean.<suffix>) are excluded or refused, so the module "
            f"inventory and the import check skip them: {', '.join(hits)}. After a namespace or import "
            f"transport, a stale `open` or `import` in them goes unreported: run each one's expected-reason "
            f"check (`python3 -m lean_pkg.lean_diag LOG --error-code CODE --file NAME`; {DISABLED_LEAN_DOC})")


def _finish(plan: dict) -> dict:
    ref = sorted(set(plan["refusals"]))
    plan["refusals"] = ref
    plan["warnings"] = sorted(set(plan.get("warnings", [])))
    plan["ok"] = not ref
    plan["totals"] = {"included_files": len(plan["included"]),
                      "included_bytes": sum(e["size"] or 0 for e in plan["included"]),
                      "excluded_files": len(plan["excluded"]), "allowed_private": len(plan["allowed_private"]),
                      "weakened": len(plan["weakened"]), "modules": len(plan["modules"]),
                      "claims": len(plan["claims"]), "refusals": len(ref), "warnings": len(plan["warnings"])}
    return plan


# ----------------------------------------------------------------------------------------------------
# output

def verdict(plan: dict) -> str:
    t = plan["totals"]
    head = "plan: OK" if plan["ok"] else f"plan: REFUSED ({t['refusals']} refusal(s))"
    commit = plan["source"]["commit"] or "unresolved"
    warned = f", {t['warnings']} warning(s)" if t.get("warnings") else ""
    return (f"{head}: {t['included_files']} file(s) included ({t['included_bytes']} bytes), "
            f"{t['excluded_files']} excluded, {t['allowed_private']} allowed private, {t['modules']} module(s), "
            f"{t['claims']} claim(s), {t['weakened']} weakening(s){warned}; source commit {commit}")


def render(plan: dict) -> str:
    out = [verdict(plan)]
    if plan["refusals"]:
        out.append("refusals:")
        out += [f"  - {r}" for r in plan["refusals"]]
    if plan["warnings"]:
        out.append("warnings (reported, not refused):")
        out += [f"  - {w}" for w in plan["warnings"]]
    if plan["included"]:
        out.append("included (to <- from, size, sha256, blob, mode, class):")
        out += [f"  {e['to']} <- {e['from']}  {e['size']}  {e['sha256']}  {e['blob']}  {e['mode']}  {e['class']}"
                for e in plan["included"]]
    if plan["excluded"]:
        out.append("excluded (PRIVATE: keep this list out of anything published):")
        out += [f"  {e['from']}  {e['class']}: {e['reason']}" for e in plan["excluded"]]
    if plan["allowed_private"]:
        out.append("allowed private (default-denied, included by allow_private; disclosed):")
        out += [f"  {e['to']} <- {e['from']}  {e['denied_class']}  allow_private {e['glob']!r}: {e['reason']}"
                for e in plan["allowed_private"]]
    if plan["weakened"]:
        out.append("weakened checks (disclosed):")
        out += [f"  {w['rule']} {w['import']}: {w['reason']}" for w in plan["weakened"]]
    if plan["modules"]:
        out.append("modules (module -> file, library):")
        out += [f"  {m['module']} -> {m['file']}  ({m['lib']})" for m in plan["modules"]]
    if plan["unowned_lean_files"]:
        out.append("lean files owned by no library (import-checked, not modules):")
        out += [f"  {f}" for f in plan["unowned_lean_files"]]
    if plan["adopted"]:
        out.append("adopted files (upstream @ commit, license; license file carried; provenance):")
        for a in plan["adopted"]:
            lf = a["license_file"]["to"] if a["license_file"] else "NOT CARRIED"
            pv = a["provenance"]["to"] if a["provenance"] else "none"
            out.append(f"  adopted[{a['index']}] {a['upstream']} @ {a['upstream_commit']}, {a['license']} "
                       f"({a['upstream_license_file']}); license file {lf}; provenance {pv}")
            out += [f"    {f}" for f in a["files"]]
    if plan["claims"]:
        out.append("claims (id -> decl -> file):")
        out += [f"  {c['id']} -> {c['decl']} -> {c['file']}" for c in plan["claims"]]
    pins = plan["pins"]
    if pins:
        out.append("pins:")
        for role in ("lean_toolchain", "lake_manifest", "claims"):
            p = pins.get(role)
            if p:
                extra = f"  {p['value']}" if "value" in p else ""
                out.append(f"  {role}: {p['to']} <- {p['from']}  sha256 {p['sha256']}{extra}")
                for d in p.get("packages", []):
                    where = f"{d.get('url')} @ {d.get('rev')}" if d["type"] == "git" else f"path {d.get('dir')}"
                    out.append(f"    {d['name']}: {d['type']} {where}")
        for p in pins.get("verification", []):
            out.append(f"  verification: {p['to']} <- {p['from']}  sha256 {p['sha256']}")
    t = plan["totals"]
    out.append("totals: " + ", ".join(f"{k} {v}" for k, v in t.items()))
    out.append(verdict(plan))
    return "\n".join(out)


def public_json(plan: dict) -> str:
    return json.dumps(plan, indent=2, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------------------------------
# CLI

EXAMPLES = """\
examples:
  lean-pkg-extract plan --repo . --commit HEAD --manifest extract.json
  lean-pkg-extract plan --repo ~/paper --commit 0123abc --manifest extract.json --json
  lean-pkg-extract plan --repo . --commit v1.0 --manifest extract.json --report ../extract-plan.json
  lean-pkg-extract ledger @ledger-args.txt
  lean-pkg-extract recheck --ledger ../newlib-ledger.json --repo . --target ../newlib

Dry run only: `plan` reads git objects at COMMIT and writes nothing (--report PATH, outside SRC, is the
one exception). The `write --output DIR` step comes next. `ledger` writes only its ledger, outside SRC
and the target; `recheck` writes nothing.
exit codes: 0 plan clean, 1 refused (every reason listed), 2 usage.
docs: https://lean-pkg.github.io/lean-pkg/extract.html"""


class _Parser(argparse.ArgumentParser):
    json_requested = False

    def error(self, message):
        if _Parser.json_requested:
            print(json.dumps({"ok": False, "error": message, "usage": True}))
        super().error(message)

    def convert_arg_line_to_args(self, arg_line):
        """An @ARGSFILE line: `--flag value` is the flag and the rest of the line (a reason may hold
        spaces), any other line one argument; blank lines and `#` lines are skipped."""
        line = arg_line.strip()
        if not line or line.startswith("#"):
            return []
        head, _, rest = line.partition(" ")
        if head.startswith("--") and "=" not in head and rest.strip():
            return [head, rest.strip()]
        return [line]


def parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    ap = _Parser(prog="lean-pkg-extract", formatter_class=fmt, epilog=EXAMPLES,
                 description="Plan a standalone public Lean-verification repository from a mixed working "
                             "repository, with exact provenance. Read-only; no registry, no network.")
    sub = ap.add_subparsers(dest="verb", metavar="{plan,ledger,recheck}", required=True)
    p = sub.add_parser("plan", formatter_class=fmt, epilog=EXAMPLES,
                       help="dry run: classify, check and inventory what an extract at COMMIT would contain",
                       description="Dry run: classify every file under the manifest's roots at COMMIT "
                                   "(default deny), check paths, links, pins, dependencies, claims and "
                                   "imports, and print the inventory. Writes nothing.")
    p.add_argument("--repo", required=True, metavar="SRC", help="the source git repository (its top directory)")
    p.add_argument("--commit", required=True, metavar="COMMIT", help="the source commit; recorded as a full SHA")
    p.add_argument("--manifest", required=True, metavar="PATH",
                   help=f"the extract manifest, strict JSON, schema {MANIFEST_SCHEMA}")
    p.add_argument("--json", dest="json_out", action="store_true", help=f"JSON on stdout (schema {PLAN_SCHEMA})")
    p.add_argument("--report", metavar="PATH",
                   help="also write the JSON plan (with the private excluded list) to PATH, outside SRC")
    from . import extract_ledger
    extract_ledger.add_parsers(sub, _Parser, fmt)
    return ap


def _src_top(repo: pathlib.Path, flag: str = "--repo") -> tuple[pathlib.Path | None, pathlib.Path]:
    """(work tree top or None for a bare repository, git dir); Usage when repo is not a git repository
    or is a subdirectory of one."""
    if not repo.is_dir():
        raise Usage(f"{flag} {repo}: not a directory")
    p = _git(repo, "rev-parse", "--is-bare-repository", "--absolute-git-dir")
    if p.returncode != 0:
        raise Usage(f"{flag} {repo}: not a git repository")
    bare, gitdir = p.stdout.decode().split("\n")[:2]
    if bare == "true":
        return None, pathlib.Path(gitdir)
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.decode().strip()
    if pathlib.Path(top).resolve() != repo.resolve():
        raise Usage(f"{flag} {repo}: a subdirectory of a git repository; pass its top directory "
                    f"(manifest paths are relative to it)")
    return pathlib.Path(top).resolve(), pathlib.Path(gitdir).resolve()


def run(args) -> tuple[dict, int]:
    repo = pathlib.Path(args.repo).expanduser()
    top, gitdir = _src_top(repo)
    pre: list[str] = []
    report = None
    if args.report:
        report = pathlib.Path(args.report).expanduser()
        rp = report.parent.resolve() / report.name
        for inside in (top, gitdir):
            if inside is not None and (rp == inside or inside in rp.parents):
                pre.append(f"--report {args.report}: inside the source repository; the report lists private "
                           f"paths and a dry run leaves SRC unchanged, so write it outside SRC")
                break
        if report.is_symlink() or (report.exists() and not report.is_file()):
            pre.append(f"--report {args.report}: exists and is not a regular file")
        elif not rp.parent.is_dir():
            pre.append(f"--report {args.report}: its directory does not exist")
    try:
        mbytes = pathlib.Path(args.manifest).expanduser().read_bytes()
    except OSError as e:
        mbytes = b""
        pre.append(f"--manifest {args.manifest}: cannot be read ({e.strerror or e})")
    plan = build_plan(repo, args.commit, mbytes)
    if pre:
        plan["refusals"] = pre + plan["refusals"]
        plan = _finish(plan)
    if report is not None and not pre:
        report.write_text(public_json(plan), encoding="utf-8")
    return plan, (0 if plan["ok"] else 1)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    from . import extract_ledger
    _Parser.json_requested = "--json" in raw or extract_ledger.argsfile_requests_json(raw)
    args = parser().parse_args(raw)
    try:
        if args.verb != "plan":
            text, rc = extract_ledger.run(args)
            sys.stdout.write(text)
            return rc
        plan, rc = run(args)
    except Usage as e:
        if args.json_out:
            print(json.dumps({"ok": False, "error": str(e), "usage": True}, indent=2))
        print(f"lean-pkg-extract: {e}", file=sys.stderr)
        return 2
    sys.stdout.write(public_json(plan) if args.json_out else render(plan) + "\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
