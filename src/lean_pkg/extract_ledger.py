# lean-pkg-extract ledger / recheck -- the declaration-level extraction ledger (proposal 0009, stage S2)
"""lean-pkg-extract ledger and recheck: a tool-made record that a library ported by hand from private
modules is faithful, declaration by declaration, to the source at a named commit (proposal 0009, #329).

`ledger` reads the source modules at COMMIT and the target modules at TARGET_COMMIT (git objects only,
never a working tree), splits each into declarations (`lean_scan.decl_spans`), applies the namespace
and rename maps to the source side (`lean_scan.apply_map`), and compares the normalized texts by SHA-256
(method `text/1`). Every source declaration is IDENTICAL, DIFFERS, DROPPED (declared with `--drop`) or
MISSING; every target declaration with no source is NEW. MISSING, an undeclared NEW and a DIFFERS not
listed in `--allow-differs` are failures. When nothing fails, the ledger (`lean-pkg-extract-ledger/1`) is
written once to `--ledger PATH`, which must lie outside SRC and outside the target, and must not exist.

`recheck` reads a ledger strictly, recomputes it from its own recorded map (at the recorded commits, or
at `--commit` / `--target-commit`), and fails when the recomputation fails or, at the recorded commits,
differs from the ledger in any field.

The ledger is evidence: never rewritten, read by every later release, and read by no gate, page or
index. It names private source paths and names; keep it out of anything published.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import posixpath
import re

from . import export_paths as xp
from . import extract as ex
from . import lean_scan

EXTRACT_LEDGER_SCHEMA = "lean-pkg-extract-ledger/1"
METHOD_TEXT = "text/1"
VERDICTS = ("IDENTICAL", "DROPPED", "DIFFERS", "NEW", "MISSING")
TOTAL_KEYS = VERDICTS + ("weakened",)
CONTEXT_VERDICTS = ("IDENTICAL", "DIFFERS")
DECL_KINDS = frozenset(lean_scan.KEYWORDS) | {"example", "mutual"}
LEDGER_KEYS = ("schema", "method", "ok", "source", "target", "map", "entries", "context", "warnings", "totals")
SIDE_KEYS = ("commit", "lean_toolchain", "lake_manifest_sha256", "modules")
MODULE_KEYS = ("path", "module", "blob", "sha256")
MAP_KEYS = ("modules", "namespaces", "renames", "drops", "new", "allow_differs")
PAIR_KEYS = ("from", "to")
REASON_KEYS = ("decl", "reason")
ENTRY_KEYS = ("kind", "source", "target", "source_module", "target_module", "verdict", "source_raw_sha256",
              "source_sha256", "target_sha256", "whitespace_only", "doc_changed", "reason")
CONTEXT_KEYS = ("source_module", "target_module", "verdict", "source_sha256", "target_sha256", "reason")
LAKEFILES = ("lakefile.toml", "lakefile.lean")
CONTEXT_PREFIX = "context@"
HEX64 = re.compile(r"[0-9a-f]{64}")
OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
SCHEMA_RE = re.compile(r"lean-pkg-extract-ledger/(\d+)")
FUTURE_METHOD = re.compile(r"(text|types)/\d+")
PRIVATE = "PRIVATE: source names and paths; keep this list out of anything published"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canon(name: str) -> tuple[str, ...] | None:
    parts = lean_scan.split_name(name)
    return None if parts is None else lean_scan._canon(parts)


# ----------------------------------------------------------------------------------------------------
# the input: flags (or an @argsfile) and, for recheck, the ledger's recorded map

def _split_outside_names(value: str, sep: str) -> tuple[str, str] | None:
    """(head, tail) split at the first `sep` outside a «quoted» name, or None."""
    inside = False
    for i, ch in enumerate(value):
        if ch == "«":
            inside = True
        elif ch == "»":
            inside = False
        elif ch == sep and not inside:
            return value[:i], value[i + 1:]
    return None


def empty_spec() -> dict:
    return {k: [] for k in MAP_KEYS}


def parse_spec(args, refusals: list[str]) -> dict:
    """The map from the flags; every malformed value is a refusal (and left out)."""
    spec = empty_spec()
    for v in args.from_module or []:
        path, sep, mod = v.rpartition(":")
        if not sep or not path or not mod:
            refusals.append(f"--from-module {v!r}: expected SRC/Path.lean:New.Module")
            continue
        spec["modules"].append((path, mod))
    for flag, attr, key in (("--namespace", "namespace", "namespaces"), ("--rename", "rename", "renames")):
        for v in getattr(args, attr) or []:
            pair = _split_outside_names(v, "=")
            if pair is None or not pair[0] or not pair[1]:
                refusals.append(f"{flag} {v!r}: expected OLD=NEW")
                continue
            spec[key].append(pair)
    for flag, attr, key in (("--drop", "drop", "drops"), ("--new", "new", "new"),
                            ("--allow-differs", "allow_differs", "allow_differs")):
        for v in getattr(args, attr) or []:
            pair = _split_outside_names(v, ":")
            if pair is None or not pair[0]:
                refusals.append(f"{flag} {v!r}: expected NAME:REASON (a one-line reason is required)")
                continue
            spec[key].append((pair[0], pair[1]))
    return spec


def spec_from_map(m: dict) -> dict:
    return {"modules": [(x["from"], x["to"]) for x in m["modules"]],
            "namespaces": [(x["from"], x["to"]) for x in m["namespaces"]],
            "renames": [(x["from"], x["to"]) for x in m["renames"]],
            "drops": [(x["decl"], x["reason"]) for x in m["drops"]],
            "new": [(x["decl"], x["reason"]) for x in m["new"]],
            "allow_differs": [(x["decl"], x["reason"]) for x in m["allow_differs"]]}


def map_record(spec: dict) -> dict:
    return {"modules": [{"from": a, "to": b} for a, b in sorted(spec["modules"])],
            "namespaces": [{"from": a, "to": b} for a, b in sorted(spec["namespaces"])],
            "renames": [{"from": a, "to": b} for a, b in sorted(spec["renames"])],
            "drops": [{"decl": a, "reason": b} for a, b in sorted(spec["drops"])],
            "new": [{"decl": a, "reason": b} for a, b in sorted(spec["new"])],
            "allow_differs": [{"decl": a, "reason": b} for a, b in sorted(spec["allow_differs"])]}


def check_spec(spec: dict, refusals: list[str]) -> None:
    """Refusals that need no source: reasons, duplicates, module names, the map's own consistency."""
    for flag, key in (("--drop", "drops"), ("--new", "new"), ("--allow-differs", "allow_differs")):
        seen = set()
        for decl, reason in spec[key]:
            if not ex._reason(reason):
                refusals.append(f"{flag} {decl}: the reason must be one non-empty line")
            if decl in seen:
                refusals.append(f"{flag} {decl}: named twice")
            seen.add(decl)
    both = {d for d, _ in spec["drops"]} & {d for d, _ in spec["allow_differs"]}
    for d in sorted(both):
        refusals.append(f"{d}: both dropped (--drop) and allowed to differ (--allow-differs)")
    froms, tos = {}, {}
    for path, mod in spec["modules"]:
        why = xp.bad_rel_path(path)
        if why:
            refusals.append(f"--from-module {path}:{mod}: the source path {why}")
        elif not path.endswith(".lean"):
            refusals.append(f"--from-module {path}:{mod}: the source path is not a .lean file")
        if not lean_scan.is_name(mod) or lean_scan.module_file(mod) is None or \
                (lean_scan.split_name(mod) or [""])[0] == "_root_":
            refusals.append(f"--from-module {path}:{mod}: the target module {mod!r} is not a Lean module name")
        elif lean_scan._canon(lean_scan.split_name(mod))[0] in _reserved():
            refusals.append(f"--from-module {path}:{mod}: the target module's root {mod.split('.')[0]!r} is the "
                            f"root namespace of Lean or a Mathlib dependency; choose another")
        if path in froms:
            refusals.append(f"--from-module {path}: the source module is mapped twice")
        if mod in tos:
            refusals.append(f"--from-module {path}:{mod}: the target module {mod} is also the target of {tos[mod]}")
        froms.setdefault(path, mod)
        tos.setdefault(mod, path)
    if not spec["modules"]:
        refusals.append("no --from-module: name at least one SRC/Path.lean:New.Module")
    try:
        lean_scan.apply_map("", spec["namespaces"], spec["renames"])
    except lean_scan.MapError as e:
        refusals.append(f"invalid map: {e}")


def _reserved() -> set[str]:
    from .scaffold import RESERVED_MODULES
    return set(RESERVED_MODULES)


# ----------------------------------------------------------------------------------------------------
# reading one side at its commit

def _lake_root(path: str, tree: dict) -> str:
    """The nearest directory above `path` holding a Lake configuration file ("" for the top)."""
    d = posixpath.dirname(path)
    while d:
        if any(f"{d}/{f}" in tree for f in LAKEFILES):
            return d
        d = posixpath.dirname(d)
    return ""


def _module_name(path: str, root: str) -> str:
    rel = path[len(root) + 1:] if root else path
    return rel[:-len(".lean")].replace("/", ".")


class Tree:
    """One side's files: `tree` maps a path to {"mode", "type", "oid", "size"} as `ex.ls_tree` does,
    `read(oids)` returns {oid: bytes} as `ex.read_blobs` does, and `label` names the side in messages."""

    def __init__(self, tree: dict, read, label: str):
        self.tree, self.read, self.label = tree, read, label


def git_tree(repo, commit: str) -> Tree:
    """A side read from git objects at commit."""
    return Tree(ex.ls_tree(repo, commit), lambda oids: ex.read_blobs(repo, oids), commit[:12])


def blob_oid(data: bytes, object_format: str = "sha1") -> str:
    """The git blob id of data, as `git hash-object` computes it in a repository of object_format."""
    h = hashlib.sha256() if object_format == "sha256" else hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def files_tree(files: dict[str, bytes], label: str, object_format: str = "sha1") -> Tree:
    """A side held in memory (`decls` before anything is committed): every file a regular blob."""
    tree = {p: {"mode": "100644", "type": "blob", "oid": blob_oid(b, object_format), "size": len(b)}
            for p, b in files.items()}
    by_oid = {tree[p]["oid"]: b for p, b in files.items()}
    return Tree(tree, lambda oids: {o: by_oid[o] for o in oids if o in by_oid}, label)


def _pins(t: Tree, root: str, role: str, warnings: list[str]) -> tuple[str | None, str | None, dict]:
    """(lean-toolchain text, lake-manifest.json sha256, {package: rev}) of the Lake package at root."""
    where = f"{root}/" if root else ""
    tree = t.tree
    tc_ent, lm_ent = tree.get(where + "lean-toolchain"), tree.get(where + "lake-manifest.json")
    blobs = t.read([e["oid"] for e in (tc_ent, lm_ent) if e])
    tc = blobs.get(tc_ent["oid"]) if tc_ent else None
    lm = blobs.get(lm_ent["oid"]) if lm_ent else None
    toolchain = tc.decode("utf-8", "replace").strip() if tc is not None else None
    if toolchain is None:
        warnings.append(f"{role}: no {where}lean-toolchain beside the modules; toolchains are not compared")
    revs: dict = {}
    if lm is None:
        warnings.append(f"{role}: no {where}lake-manifest.json beside the modules; dependencies are not compared")
        return toolchain, None, revs
    doc, _err = ex.load_json_strict(lm, "lake-manifest.json")
    for p in (doc.get("packages") if isinstance(doc, dict) else None) or []:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            revs[p["name"]] = p.get("rev")
    return toolchain, hashlib.sha256(lm).hexdigest(), revs


def _side(t: Tree, paths: list[str], role: str, refusals: list[str], warnings: list[str]):
    """{path: text} of the modules of one side (refusals for missing, non-file, unreadable ones), the
    module records, and the pins."""
    tree = t.tree
    ok_paths = []
    for path in paths:
        ent = tree.get(path)
        if ent is None:
            refusals.append(f"{role} {path}: missing at {t.label}")
        elif ent["mode"] in (ex.SYMLINK, ex.GIT_LINK) or ent["type"] != "blob":
            refusals.append(f"{role} {path}: not a regular file at {t.label}")
        else:
            ok_paths.append(path)
    blobs = t.read([tree[p]["oid"] for p in ok_paths])
    texts, records = {}, []
    roots = sorted({_lake_root(p, tree) for p in ok_paths})
    for path in ok_paths:
        b = blobs.get(tree[path]["oid"])
        if b is None:
            refusals.append(f"{role} {path}: blob {tree[path]['oid'][:12]} is not in the object store (partial clone?)")
            continue
        try:
            texts[path] = b.decode("utf-8")
        except UnicodeDecodeError:
            refusals.append(f"{role} {path}: not UTF-8")
            continue
        records.append({"path": path, "module": None, "blob": tree[path]["oid"],
                        "sha256": hashlib.sha256(b).hexdigest()})
    root = roots[0] if roots else ""
    if len(roots) > 1:
        warnings.append(f"{role}: the modules lie in {len(roots)} Lake packages; the pins are read from "
                        f"{roots[0] or 'the repository top'}")
    pins = _pins(t, root, role, warnings)
    return tree, texts, records, pins


def _target_path(module: str, tree: dict) -> tuple[str | None, str | None]:
    """(path, None) of a target module's file, or (None, why)."""
    want = lean_scan.module_file(module)
    if want is None:
        return None, "not a module name"
    if want in tree:
        return want, None
    cands = sorted(p for p in tree if p.endswith("/" + want))
    if len(cands) == 1:
        return cands[0], None
    if cands:
        return None, f"ambiguous: {', '.join(cands)}"
    return None, f"no file {want} (or */{want})"


# ----------------------------------------------------------------------------------------------------
# the comparison (method text/1)

class _Unit:
    """One declaration span of one side."""

    def __init__(self, span, module: str):
        self.span, self.module = span, module
        self.key, self.kind, self.names = span.key, span.kind, span.names


def _lookup(units: list[_Unit]) -> dict:
    out = {}
    for u in units:
        out.setdefault(u.key, u)
        for n in u.names:
            out.setdefault(n, u)
    return out


def _zero_totals() -> dict:
    return {k: 0 for k in TOTAL_KEYS}


def skeleton(spec: dict) -> dict:
    side = {"commit": None, "lean_toolchain": None, "lake_manifest_sha256": None, "modules": []}
    return {"schema": EXTRACT_LEDGER_SCHEMA, "method": METHOD_TEXT, "ok": False, "source": dict(side),
            "target": dict(side), "map": map_record(spec), "entries": [], "context": [], "warnings": [],
            "totals": _zero_totals()}


def compute(src_repo, src_commit: str, tgt_repo, tgt_commit: str | None, spec: dict,
            refusals: list[str], target: Tree | None = None) -> tuple[dict, list[str]]:
    """(ledger, failures): the text/1 comparison of the mapped source modules at src_commit with the
    target modules at tgt_commit (or, for `decls`, with the in-memory `target`, tgt_commit None). Input
    problems are appended to refusals."""
    led = skeleton(spec)
    led["source"]["commit"], led["target"]["commit"] = src_commit, tgt_commit
    warnings: list[str] = []
    check_spec(spec, refusals)
    modules = sorted(spec["modules"])
    src_tree, src_texts, src_recs, src_pins = _side(git_tree(src_repo, src_commit),
                                                    [p for p, _ in modules if not xp.bad_rel_path(p)],
                                                    "source", refusals, warnings)
    tgt = target if target is not None else git_tree(tgt_repo, tgt_commit)
    tgt_tree = tgt.tree
    tgt_paths: dict[str, str] = {}
    for path, mod in modules:
        tp, why = _target_path(mod, tgt_tree)
        if tp is None:
            refusals.append(f"target module {mod} (from {path}): {why} at {tgt.label}")
        else:
            tgt_paths[mod] = tp
    _t, tgt_texts, tgt_recs, tgt_pins = _side(tgt, sorted(set(tgt_paths.values())), "target", refusals, warnings)
    src_root = {p: _lake_root(p, src_tree) for p in src_texts}
    src_mod = {p: _module_name(p, src_root[p]) for p in src_texts}
    tgt_mod_of_path = {tp: m for m, tp in tgt_paths.items()}
    for r in src_recs:
        r["module"] = src_mod[r["path"]]
    for r in tgt_recs:
        r["module"] = tgt_mod_of_path[r["path"]]
    led["source"].update(lean_toolchain=src_pins[0], lake_manifest_sha256=src_pins[1],
                         modules=sorted(src_recs, key=lambda r: r["path"]))
    led["target"].update(lean_toolchain=tgt_pins[0], lake_manifest_sha256=tgt_pins[1],
                         modules=sorted(tgt_recs, key=lambda r: r["path"]))
    if src_pins[0] is not None and tgt_pins[0] is not None and src_pins[0] != tgt_pins[0]:
        warnings.append(f"lean-toolchain differs: source {src_pins[0]}, target {tgt_pins[0]} (the same text may "
                        f"elaborate differently)")
    for name in sorted(set(src_pins[2]) & set(tgt_pins[2])):
        a, b = src_pins[2][name], tgt_pins[2][name]
        if a != b:
            warnings.append(f"lake-manifest.json: dependency {name} is at {str(a)[:12]} in the source and "
                            f"{str(b)[:12]} in the target (the same text may elaborate differently)")
    led["warnings"] = sorted(set(warnings))

    # split both sides; an unreadable file is refused whole
    pairs = []                                       # (source path, source module, target module, target path)
    for path, mod in modules:
        if path in src_texts and mod in tgt_paths and tgt_paths[mod] in tgt_texts:
            pairs.append((path, src_mod[path], mod, tgt_paths[mod]))
    split: dict[tuple[str, str], tuple] = {}
    for path, smod, tmod, tpath in pairs:
        for role, p, m, texts in (("source", path, smod, src_texts), ("target", tpath, tmod, tgt_texts)):
            try:
                split[(role, p)] = lean_scan.decl_spans(texts[p], m)
            except lean_scan.ScanError as e:
                refusals.append(f"{role} {p}: cannot be split into declarations ({e}); text/1 refuses the file "
                                f"whole")
    if refusals:
        return led, []

    src_units: list[_Unit] = []
    tgt_units: list[_Unit] = []
    src_ctx: dict[str, list] = {}
    tgt_ctx: dict[str, list] = {}
    mod_map = {}
    for path, smod, tmod, tpath in pairs:
        decls, ctx = split[("source", path)]
        src_units += [_Unit(s, smod) for s in decls]
        src_ctx[smod] = ctx
        decls, ctx = split[("target", tpath)]
        tgt_units += [_Unit(s, tmod) for s in decls]
        tgt_ctx[tmod] = ctx
        mod_map[smod] = tmod
    for role, units in (("source", src_units), ("target", tgt_units)):
        seen: dict[str, str] = {}
        for u in units:
            for n in u.names or (u.key,):
                if n in seen and seen[n] != u.module:
                    refusals.append(f"{role}: {n} is declared in both {seen[n]} and {u.module}")
                seen.setdefault(n, u.module)

    ns, rn = spec["namespaces"], spec["renames"]
    declared = sorted({n for u in src_units for n in u.names})
    src_names = {_canon(n) for n in declared}

    def mapped_name(n: str) -> str:
        return lean_scan.apply_map(n, ns, rn)

    def mapped_key(u: _Unit) -> str:
        if not u.names:                                    # instance#k@Src.A -> instance#k@New.A
            return u.key.split("@", 1)[0] + "@" + mod_map[u.module]
        if u.kind == "mutual":
            return "mutual(" + ", ".join(sorted(mapped_name(n) for n in u.names)) + ")"
        return mapped_name(u.names[0])

    # staleness: every map entry must act on something
    for old, _new in ns:
        c = _canon(old)
        if c is not None and not any(s[:len(c)] == c and len(s) > len(c) for s in src_names):
            refusals.append(f"--namespace {old}={_new}: stale, no source declaration lies under {old}")
    for old, _new in rn:
        if _canon(old) not in src_names:
            refusals.append(f"--rename {old}={_new}: stale, {old} is not a source declaration")
    src_look, tgt_look = _lookup(src_units), _lookup(tgt_units)
    dropped: dict[int, str] = {}
    for decl, reason in spec["drops"]:
        u = src_look.get(decl)
        if u is None:
            refusals.append(f"--drop {decl}: stale, not a source declaration of the mapped modules")
        else:
            dropped[id(u)] = reason
    allowed: dict[int, str] = {}
    allowed_ctx: dict[str, str] = {}
    for decl, reason in spec["allow_differs"]:
        if decl.startswith(CONTEXT_PREFIX) and decl[len(CONTEXT_PREFIX):] in mod_map:
            allowed_ctx[decl[len(CONTEXT_PREFIX):]] = reason
            continue
        u = src_look.get(decl)
        if u is None:
            refusals.append(f"--allow-differs {decl}: stale, not a source declaration of the mapped modules "
                            f"(a module's context rows are {CONTEXT_PREFIX}<source module>)")
        else:
            allowed[id(u)] = reason
    try:
        keys = [(u, mapped_key(u)) for u in src_units]
    except (lean_scan.MapError, lean_scan.ScanError) as e:
        refusals.append(f"invalid map: {e}")
        return led, []
    by_key: dict[str, _Unit] = {}
    for u, k in keys:
        if k in by_key:
            refusals.append(f"{by_key[k].key} and {u.key} both map to {k} in the target (a rename or namespace "
                            f"collision)")
        by_key.setdefault(k, u)
    tgt_by_key = {u.key: u for u in tgt_units}
    for u, k in keys:
        if id(u) in dropped and k in tgt_by_key:
            refusals.append(f"--drop {u.key}: dropped, yet present in the target as {k} "
                            f"({tgt_by_key[k].module})")
    # a dropped declaration still referenced by a kept one (best effort: identifiers resolved as apply_map does)
    drop_names = [n for u in src_units if id(u) in dropped for n in u.names]
    if drop_names:
        sentinel = {n: f"lean_pkg_dropped_{i}" for i, n in enumerate(drop_names)}
        back = {v: k for k, v in sentinel.items()}
        for u in src_units:
            if id(u) in dropped:
                continue
            probe = lean_scan.apply_map(u.span.code, (), sentinel.items(), src_scope=u.span.scope, declared=declared)
            hits = sorted({back[t] for t in re.findall(r"lean_pkg_dropped_\d+", probe) if t in back})
            for n in hits:
                refusals.append(f"--drop {n}: still referenced by {u.key}, which is kept (best effort: an "
                                f"identifier that resolves to it)")
    if refusals:
        return led, []

    entries, failures = [], []
    used: set[str] = set()
    for u, k in keys:
        mapped = lean_scan.apply_map(u.span.code, ns, rn, src_scope=u.span.scope, declared=declared)
        raw_n, src_n = lean_scan.normalize(u.span.code), lean_scan.normalize(mapped)
        row = {"kind": u.kind, "source": u.key, "target": k, "source_module": u.module,
               "target_module": mod_map[u.module], "verdict": None, "source_raw_sha256": _sha(raw_n),
               "source_sha256": _sha(src_n), "target_sha256": None, "whitespace_only": False,
               "doc_changed": False, "reason": None}
        t = tgt_by_key.get(k)
        if t is None:
            if id(u) in dropped:
                row.update(verdict="DROPPED", reason=dropped[id(u)])
            else:
                row["verdict"] = "MISSING"
                failures.append(f"MISSING {u.key}: expected {k} in the target, not found (drop it with "
                                f"--drop {u.key}:REASON if that is deliberate)")
            entries.append(row)
            continue
        used.add(k)
        tgt_n = lean_scan.normalize(t.span.code)
        row.update(target_module=t.module, target_sha256=_sha(tgt_n), doc_changed=u.span.doc != t.span.doc)
        if _sha(src_n) == row["target_sha256"]:
            row["verdict"] = "IDENTICAL"
            if id(u) in allowed:
                refusals.append(f"--allow-differs {u.key}: stale, the declaration is IDENTICAL")
        else:
            row["verdict"] = "DIFFERS"
            row["whitespace_only"] = lean_scan.collapse_whitespace(src_n) == lean_scan.collapse_whitespace(tgt_n)
            if id(u) in allowed:
                row["reason"] = allowed[id(u)]
            else:
                hint = " (whitespace only)" if row["whitespace_only"] else ""
                failures.append(f"DIFFERS {u.key} -> {k}{hint}: the mapped source text and the target text "
                                f"differ (disclose it with --allow-differs {u.key}:REASON if deliberate)")
        entries.append(row)
    for u, k in keys:
        if id(u) in allowed and k not in tgt_by_key:
            refusals.append(f"--allow-differs {u.key}: stale, the declaration is not in the target")
    declared_new = {}
    for decl, reason in spec["new"]:
        t = tgt_look.get(decl)
        if t is None:
            refusals.append(f"--new {decl}: stale, not a declaration of the target modules")
        elif t.key in used:
            refusals.append(f"--new {decl}: stale, it has a source counterpart")
        else:
            declared_new[t.key] = reason
    for t in tgt_units:
        if t.key in used:
            continue
        row = {"kind": t.kind, "source": None, "target": t.key, "source_module": None, "target_module": t.module,
               "verdict": "NEW", "source_raw_sha256": None, "source_sha256": None,
               "target_sha256": _sha(lean_scan.normalize(t.span.code)), "whitespace_only": False,
               "doc_changed": False, "reason": declared_new.get(t.key)}
        if row["reason"] is None:
            failures.append(f"NEW {t.key} ({t.module}): a target declaration with no source counterpart "
                            f"(declare it with --new {t.key}:REASON)")
        entries.append(row)

    context = []
    for smod in sorted(mod_map):
        tmod = mod_map[smod]

        def joined(spans, mapped):
            parts = []
            for c in spans:
                if c.kind in lean_scan._HEADER_WORDS:
                    continue
                code = lean_scan.apply_map(c.code, ns, rn, src_scope=c.scope, declared=declared) if mapped else c.code
                parts.append(lean_scan.normalize(code))
            return "\n".join(p for p in parts if p)
        a, b = _sha(joined(src_ctx[smod], True)), _sha(joined(tgt_ctx[tmod], False))
        row = {"source_module": smod, "target_module": tmod, "verdict": "IDENTICAL" if a == b else "DIFFERS",
               "source_sha256": a, "target_sha256": b, "reason": None}
        if a != b:
            if smod in allowed_ctx:
                row["reason"] = allowed_ctx[smod]
            else:
                failures.append(f"context DIFFERS {smod} -> {tmod}: the module's other commands (open, variable, "
                                f"universe, set_option, namespace, notation, ...) differ after mapping (disclose "
                                f"it with --allow-differs {CONTEXT_PREFIX}{smod}:REASON if deliberate)")
        elif smod in allowed_ctx:
            refusals.append(f"--allow-differs {CONTEXT_PREFIX}{smod}: stale, the context rows are IDENTICAL")
        context.append(row)

    totals = _zero_totals()
    for e in entries:
        totals[e["verdict"]] += 1
    totals["weakened"] = sum(1 for e in entries if e["verdict"] == "DIFFERS" and e["reason"] is not None) + \
        sum(1 for c in context if c["verdict"] == "DIFFERS" and c["reason"] is not None)
    led.update(entries=entries, context=context, totals=totals)
    led["ok"] = not failures and not refusals
    return led, failures


# ----------------------------------------------------------------------------------------------------
# reading a ledger back: strict

def _hex64_or_none(v) -> bool:
    return v is None or (isinstance(v, str) and bool(HEX64.fullmatch(v)))


def _str_or_none(v) -> bool:
    return v is None or isinstance(v, str)


def validate_ledger(obj, refusals: list[str]) -> dict | None:
    """The ledger when it is a valid lean-pkg-extract-ledger/1 with method text/1, else None (refusals say
    why). Exact keys everywhere, closed enums, consistent totals."""
    n0 = len(refusals)
    if not isinstance(obj, dict):
        refusals.append("ledger: not a JSON object")
        return None
    schema = obj.get("schema")
    if schema != EXTRACT_LEDGER_SCHEMA:
        m = SCHEMA_RE.fullmatch(schema) if isinstance(schema, str) else None
        if m and int(m.group(1)) > 1:
            refusals.append(f"ledger: schema {schema} is newer than this lean-pkg reads ({EXTRACT_LEDGER_SCHEMA}): "
                            f"upgrade lean-pkg")
        else:
            refusals.append(f"ledger: unknown schema {schema!r} (expected {EXTRACT_LEDGER_SCHEMA})")
        return None
    if not ex._keys(obj, LEDGER_KEYS, LEDGER_KEYS, "ledger", refusals):
        return None
    method = obj["method"]
    if method != METHOD_TEXT:
        if isinstance(method, str) and FUTURE_METHOD.fullmatch(method):
            refusals.append(f"ledger: method {method} is not known to this lean-pkg (it reads {METHOD_TEXT}): "
                            f"upgrade lean-pkg")
        else:
            refusals.append(f"ledger: unknown method {method!r}")
        return None
    if obj["ok"] is not True:
        refusals.append("ledger: ok is not true; the tool writes a ledger only when nothing fails, so this one "
                        "was edited")
    for role in ("source", "target"):
        side = obj[role]
        if not ex._keys(side, SIDE_KEYS, SIDE_KEYS, f"ledger.{role}", refusals):
            continue
        c = side["commit"]
        if not ((isinstance(c, str) and ex.HEX40.fullmatch(c)) or (c is None and role == "target")):
            refusals.append(f"ledger.{role}.commit: not a full 40-hex commit: {c!r}")
        if not _str_or_none(side["lean_toolchain"]):
            refusals.append(f"ledger.{role}.lean_toolchain: not a string or null")
        if not _hex64_or_none(side["lake_manifest_sha256"]):
            refusals.append(f"ledger.{role}.lake_manifest_sha256: not a SHA-256 or null")
        if not isinstance(side["modules"], list):
            refusals.append(f"ledger.{role}.modules: not a list")
            continue
        for i, r in enumerate(side["modules"]):
            w = f"ledger.{role}.modules[{i}]"
            if ex._keys(r, MODULE_KEYS, MODULE_KEYS, w, refusals):
                if not (isinstance(r["path"], str) and isinstance(r["module"], str)):
                    refusals.append(f"{w}: path and module must be strings")
                if not (isinstance(r["blob"], str) and OID.fullmatch(r["blob"])):
                    refusals.append(f"{w}.blob: not a git object id")
                if not (isinstance(r["sha256"], str) and HEX64.fullmatch(r["sha256"])):
                    refusals.append(f"{w}.sha256: not a SHA-256")
    m = obj["map"]
    if ex._keys(m, MAP_KEYS, MAP_KEYS, "ledger.map", refusals):
        for key in MAP_KEYS:
            rows = m[key]
            want = PAIR_KEYS if key in ("modules", "namespaces", "renames") else REASON_KEYS
            if not isinstance(rows, list):
                refusals.append(f"ledger.map.{key}: not a list")
                continue
            for i, r in enumerate(rows):
                w = f"ledger.map.{key}[{i}]"
                if ex._keys(r, want, want, w, refusals) and not all(isinstance(r[k], str) for k in want):
                    refusals.append(f"{w}: every value must be a string")
    entries = obj["entries"]
    if not isinstance(entries, list):
        refusals.append("ledger.entries: not a list")
        entries = []
    for i, e in enumerate(entries):
        w = f"ledger.entries[{i}]"
        if not ex._keys(e, ENTRY_KEYS, ENTRY_KEYS, w, refusals):
            continue
        if e["verdict"] not in VERDICTS:
            refusals.append(f"{w}.verdict: {e['verdict']!r} is not one of {', '.join(VERDICTS)}")
        if e["kind"] not in DECL_KINDS:
            refusals.append(f"{w}.kind: {e['kind']!r} is not a declaration kind")
        for k in ("source", "target", "source_module", "target_module", "reason"):
            if not _str_or_none(e[k]):
                refusals.append(f"{w}.{k}: not a string or null")
        for k in ("source_raw_sha256", "source_sha256", "target_sha256"):
            if not _hex64_or_none(e[k]):
                refusals.append(f"{w}.{k}: not a SHA-256 or null")
        for k in ("whitespace_only", "doc_changed"):
            if not isinstance(e[k], bool):
                refusals.append(f"{w}.{k}: not a boolean")
    context = obj["context"]
    if not isinstance(context, list):
        refusals.append("ledger.context: not a list")
        context = []
    for i, c in enumerate(context):
        w = f"ledger.context[{i}]"
        if not ex._keys(c, CONTEXT_KEYS, CONTEXT_KEYS, w, refusals):
            continue
        if c["verdict"] not in CONTEXT_VERDICTS:
            refusals.append(f"{w}.verdict: {c['verdict']!r} is not IDENTICAL or DIFFERS")
        for k in ("source_sha256", "target_sha256"):
            if not (isinstance(c[k], str) and HEX64.fullmatch(c[k])):
                refusals.append(f"{w}.{k}: not a SHA-256")
        for k in ("source_module", "target_module"):
            if not isinstance(c[k], str):
                refusals.append(f"{w}.{k}: not a string")
        if not _str_or_none(c["reason"]):
            refusals.append(f"{w}.reason: not a string or null")
    if not (isinstance(obj["warnings"], list) and all(isinstance(x, str) for x in obj["warnings"])):
        refusals.append("ledger.warnings: not a list of strings")
    t = obj["totals"]
    if ex._keys(t, TOTAL_KEYS, TOTAL_KEYS, "ledger.totals", refusals):
        if not all(type(t[k]) is int and t[k] >= 0 for k in TOTAL_KEYS):
            refusals.append("ledger.totals: every count must be a non-negative integer")
        elif len(refusals) == n0:
            want = _zero_totals()
            for e in entries:
                want[e["verdict"]] += 1
            want["weakened"] = sum(1 for r in entries + context if r["verdict"] == "DIFFERS" and r["reason"])
            if want != t:
                refusals.append(f"ledger.totals: {t} does not count the entries ({want})")
    return obj if len(refusals) == n0 else None


def _diff(a, b, path: str, out: list[str]) -> None:
    """Field paths where a (recorded) and b (recomputed) differ."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff(a.get(k), b.get(k), f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} recorded, {len(b)} recomputed")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: recorded {a!r}, recomputed {b!r}")


def drift(old: dict, new: dict) -> list[str]:
    """What changed between the recorded ledger and a recomputation at other commits (informational)."""
    out = []
    o = {e["source"] or "NEW " + e["target"]: e for e in old["entries"]}
    n = {e["source"] or "NEW " + e["target"]: e for e in new["entries"]}
    for k in sorted(set(o) | set(n)):
        a, b = o.get(k), n.get(k)
        if a is None:
            out.append(f"{k}: not in the ledger, now {b['verdict']}")
        elif b is None:
            out.append(f"{k}: in the ledger ({a['verdict']}), no longer present")
        else:
            changed = [f for f in ("source_raw_sha256", "target_sha256") if a[f] != b[f]]
            if a["verdict"] != b["verdict"]:
                changed.append(f"verdict {a['verdict']} -> {b['verdict']}")
            if changed:
                out.append(f"{k}: " + ", ".join(changed))
    for side in ("source", "target"):
        om = {r["path"]: r["blob"] for r in old[side]["modules"]}
        nm = {r["path"]: r["blob"] for r in new[side]["modules"]}
        for p in sorted(set(om) | set(nm)):
            if om.get(p) != nm.get(p):
                out.append(f"{side} module {p}: blob {str(om.get(p))[:12]} -> {str(nm.get(p))[:12]}")
    return out


# ----------------------------------------------------------------------------------------------------
# output

def _counts(led: dict) -> str:
    t = led["totals"]
    ctx = led["context"]
    cd = sum(1 for c in ctx if c["verdict"] == "DIFFERS")
    return (f"{t['IDENTICAL']} identical, {t['DROPPED']} dropped, {t['DIFFERS']} differs, {t['NEW']} new, "
            f"{t['MISSING']} missing, {t['weakened']} weakened; {len(ctx)} context row(s) ({cd} differ); "
            f"{len(led['warnings'])} warning(s)")


def verdict(verb: str, out: dict) -> str:
    led = out
    if out["refusals"]:
        head = f"{verb}: REFUSED ({len(out['refusals'])} refusal(s))"
    elif out["failures"]:
        head = f"{verb}: FAILED ({len(out['failures'])} failure(s))"
    else:
        head = f"{verb}: OK"
    src = led["source"]["commit"] or "unresolved"
    tgt = led["target"]["commit"] or "unresolved"
    tail = ""
    if verb == "ledger":
        tail = f"; written to {out['ledger_path']}" if out["written"] else "; nothing written"
    elif verb == "decls":
        o = out["output"]
        if o["written"]:
            init = ", git initialized" if o["git_init"] else ""
            tail = (f"; {len(o['files'])} file(s) written to {o['dir']}{init}, nothing committed; ledger written "
                    f"to {out['ledger_path']}")
        elif o["dry_run"] and out["ok"]:
            tail = f"; dry run: {len(o['files'])} file(s) would be written to {o['dir']}, nothing written"
        else:
            tail = "; nothing written"
        tgt = "not committed" if out["ok"] else tgt
    return f"{head} ({METHOD_TEXT}): {_counts(led)}; source {src}, target {tgt}{tail}"


def render(verb: str, out: dict) -> str:
    lines = [verdict(verb, out)]
    if out["refusals"]:
        lines.append("refusals:")
        lines += [f"  - {r}" for r in out["refusals"]]
    if out["failures"]:
        lines.append("failures:")
        lines += [f"  - {r}" for r in out["failures"]]
    if out.get("drift"):
        lines.append("drift since the ledger:")
        lines += [f"  - {r}" for r in out["drift"]]
    if out["warnings"]:
        lines.append("warnings (reported, not refused):")
        lines += [f"  - {w}" for w in out["warnings"]]
    if out["entries"]:
        lines.append(f"entries ({PRIVATE}):")
        for e in out["entries"]:
            if e["verdict"] == "NEW":
                what = f"{e['target']} ({e['target_module']})"
            else:
                what = f"{e['source']} -> {e['target']}"
            notes = []
            if e["reason"] is not None:
                label = {"DROPPED": "dropped", "NEW": "declared", "DIFFERS": "allowed"}[e["verdict"]]
                notes.append(f"{label}: {e['reason']}")
            if e["whitespace_only"]:
                notes.append("whitespace only")
            if e["doc_changed"]:
                notes.append("docstring changed")
            lines.append(f"  {e['verdict']:<9} {e['kind']:<9} {what}" + (f"  ({'; '.join(notes)})" if notes else ""))
    if out["context"]:
        lines.append("context rows (each module's other commands, after mapping):")
        for c in out["context"]:
            note = f"  (allowed: {c['reason']})" if c["reason"] is not None else ""
            lines.append(f"  {c['verdict']:<9} {c['source_module']} -> {c['target_module']}{note}")
    if out["source"]["modules"] or out["target"]["modules"]:
        lines.append(f"modules ({PRIVATE}):")
        for role in ("source", "target"):
            side = out[role]
            pin = f"; lean-toolchain {side['lean_toolchain']}" if side["lean_toolchain"] else ""
            at = side["commit"] or ("not committed" if verb == "decls" and role == "target" else "unresolved")
            lines.append(f"  {role} at {at}{pin}")
            lines += [f"    {r['path']} ({r['module']})  blob {r['blob']}  sha256 {r['sha256']}" for r in side["modules"]]
    lines.append("totals: " + ", ".join(f"{k} {v}" for k, v in out["totals"].items()))
    lines.append(verdict(verb, out))
    return "\n".join(lines)


def ledger_json(led: dict) -> str:
    return json.dumps({k: led[k] for k in LEDGER_KEYS}, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def public_json(out: dict) -> str:
    return json.dumps({k: v for k, v in out.items() if k != "ledger_path"}, indent=2, sort_keys=True,
                      ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------------------------------------
# the verbs

def _inside(p: pathlib.Path, roots) -> bool:
    return any(r is not None and (p == r or r in p.parents) for r in roots)


def ledger_path_refusals(arg: str, src_roots, target_roots, target_where: str) -> list[str]:
    """Why --ledger PATH cannot be written: inside SRC (its work tree or git directory), inside the target
    (`target_where` names it), a symlink, existing, or in a missing directory. Shared by ledger and decls."""
    refusals = []
    ledger = pathlib.Path(arg).expanduser()
    lp = ledger.parent.resolve() / ledger.name
    if _inside(lp, src_roots):
        refusals.append(f"--ledger {arg}: inside the source repository; the ledger names private source "
                        f"paths and lean-pkg-extract leaves SRC unchanged, so write it outside SRC (copy it into "
                        f"SRC's notes/ by hand to keep it there)")
    if _inside(lp, target_roots):
        refusals.append(f"--ledger {arg}: {target_where}; the ledger is private and never enters the exported "
                        f"tree, so write it outside the target")
    if ledger.is_symlink():
        refusals.append(f"--ledger {arg}: a symlink; name a new regular file")
    elif ledger.exists():
        refusals.append(f"--ledger {arg}: exists; a ledger is evidence and is never overwritten, so "
                        f"record to a new path")
    elif not lp.parent.is_dir():
        refusals.append(f"--ledger {arg}: its directory does not exist")
    return refusals


def run_ledger(args) -> tuple[dict, int]:
    repo = pathlib.Path(args.repo).expanduser()
    tdir = pathlib.Path(args.target).expanduser()
    top, gitdir = ex._src_top(repo)
    ttop, tgitdir = ex._src_top(tdir, "--target")
    refusals: list[str] = []
    spec = parse_spec(args, refusals)
    refusals += ledger_path_refusals(args.ledger, (top, gitdir), (ttop, tgitdir), "inside the target repository")
    ledger = pathlib.Path(args.ledger).expanduser()
    c = ex.resolve_commit(repo, args.commit)
    t = ex.resolve_commit(tdir, args.target_commit or "HEAD")
    if not c:
        refusals.append(f"--commit {args.commit!r} is not a commit of the source repository")
    if not t:
        refusals.append(f"--target-commit {args.target_commit or 'HEAD'!r} is not a commit of the target repository")
    if c and t:
        led, failures = compute(repo, c, tdir, t, spec, refusals)
    else:
        check_spec(spec, refusals)
        led, failures = skeleton(spec), []
        led["source"]["commit"], led["target"]["commit"] = c, t
    out = _output(led, refusals, failures)
    if out["ok"]:
        try:
            with open(ledger, "x", encoding="utf-8") as f:
                f.write(ledger_json(led))
            out["written"] = True
        except OSError as e:
            out["refusals"].append(f"--ledger {args.ledger}: cannot be written ({e.strerror or e})")
            out["ok"] = False
    out["ledger_path"] = args.ledger
    return out, (0 if out["ok"] else 1)


def _output(led: dict, refusals: list[str], failures: list[str]) -> dict:
    out = dict(led)
    out["refusals"] = sorted(set(refusals))
    out["failures"] = failures
    out["ok"] = not refusals and not failures
    out["written"] = False
    return out


def run_recheck(args) -> tuple[dict, int]:
    repo = pathlib.Path(args.repo).expanduser()
    tdir = pathlib.Path(args.target).expanduser()
    ex._src_top(repo)
    ex._src_top(tdir, "--target")
    refusals: list[str] = []
    path = pathlib.Path(args.ledger).expanduser()
    stored = None
    try:
        data = path.read_bytes() if path.is_file() else None
    except OSError:
        data = None
    if data is None:
        refusals.append(f"--ledger {args.ledger}: not a readable regular file")
    else:
        raw, err = ex.load_json_strict(data, "ledger")
        if err:
            refusals.append(err)
        else:
            stored = validate_ledger(raw, refusals)
    if stored is None:
        led = skeleton(empty_spec())
        return _output(led, refusals, []) | {"drift": []}, 1
    spec = spec_from_map(stored["map"])
    c_arg = args.commit or stored["source"]["commit"]
    t_arg = args.target_commit or stored["target"]["commit"]
    c = ex.resolve_commit(repo, c_arg)
    t = ex.resolve_commit(tdir, t_arg) if t_arg else None
    if not c:
        refusals.append(f"source commit {c_arg} ({'--commit' if args.commit else 'the ledger'}) is not a commit "
                        f"of the source repository")
    if not t_arg:
        refusals.append("the ledger records no target commit (target.commit is null): pass --target-commit")
    elif not t:
        refusals.append(f"target commit {t_arg} ({'--target-commit' if args.target_commit else 'the ledger'}) is "
                        f"not a commit of the target repository")
    if not (c and t):
        led = skeleton(spec)
        led["source"]["commit"], led["target"]["commit"] = c, t
        return _output(led, refusals, []) | {"drift": []}, 1
    led, failures = compute(repo, c, tdir, t, spec, refusals)
    out = _output(led, refusals, failures)
    same = c == stored["source"]["commit"] and (stored["target"]["commit"] in (None, t))
    if same and not refusals:
        want = {k: led[k] for k in LEDGER_KEYS}
        recorded = dict(stored)
        if recorded["target"]["commit"] is None:
            recorded = dict(recorded, target=dict(recorded["target"], commit=t))
        diffs: list[str] = []
        _diff(recorded, want, "ledger", diffs)
        out["drift"] = diffs
        if diffs:
            shown = "; ".join(diffs[:10]) + (f"; ... ({len(diffs) - 10} more)" if len(diffs) > 10 else "")
            out["refusals"].append(f"the ledger does not match a recomputation at its own commits ({len(diffs)} "
                                   f"field(s) differ, so it was edited or the sources' git objects changed): {shown}")
            out["ok"] = False
    else:
        out["drift"] = drift(stored, led) if not refusals else []
    return out, (0 if out["ok"] else 1)


def add_parsers(sub, parser_cls, fmt) -> None:
    """The `ledger` and `recheck` subparsers of lean-pkg-extract (both read @ARGSFILE)."""
    led_ex = """\
examples:
  lean-pkg-extract ledger --repo ~/paper --commit HEAD --target ~/newlib \\
      --from-module Paper/Bounds.lean:NewLib.Bounds --namespace Paper.Bounds=NewLib.Bounds \\
      --rename Paper.Bounds.main=NewLib.Bounds.main_bound --drop 'Paper.Bounds.scratch:exploration only' \\
      --ledger ~/ledgers/newlib-1.json
  lean-pkg-extract ledger @ledger-args.txt --json

@ARGSFILE: one argument per line; a line `--flag value` is the flag and the rest of the line (so a reason
may hold spaces); blank lines and lines starting with # are skipped.
exit codes: 0 every declaration accounted for (ledger written), 1 refused or failing (nothing written), 2 usage.
docs: https://lean-pkg.github.io/lean-pkg/extract.html#declaration-level-extraction-ledger"""
    p = sub.add_parser("ledger", formatter_class=fmt, epilog=led_ex, fromfile_prefix_chars="@",
                       help="record an extraction ledger: a hand-ported target compared, declaration by "
                            "declaration, with the source at COMMIT",
                       description="Compare the target modules at TARGET_COMMIT with the source modules at COMMIT, "
                                   "declaration by declaration (method text/1: comments stripped, the namespace "
                                   "and rename maps applied, normalized text hashed), and, when nothing fails, "
                                   f"write the ledger ({EXTRACT_LEDGER_SCHEMA}) to --ledger PATH, once. Reads "
                                   "git objects only; writes nothing else.")
    p.add_argument("--repo", required=True, metavar="SRC", help="the source git repository (its top directory)")
    p.add_argument("--commit", required=True, metavar="COMMIT", help="the source commit; recorded as a full SHA")
    p.add_argument("--target", required=True, metavar="TDIR", help="the target git repository (its top directory)")
    p.add_argument("--target-commit", metavar="T", help="the target commit (default: HEAD)")
    p.add_argument("--from-module", action="append", required=True, metavar="SRC/Path.lean:New.Module",
                   help="a source module and the target module it was ported to (repeatable, at least one)")
    p.add_argument("--namespace", action="append", metavar="OLD.Ns=NEW.Ns",
                   help="a namespace prefix map (repeatable; the longest prefix wins)")
    p.add_argument("--rename", action="append", metavar="OLD.qual=NEW.qual",
                   help="a declaration renamed, fully qualified (repeatable; takes precedence over --namespace)")
    p.add_argument("--drop", action="append", metavar="OLD.qual:REASON",
                   help="a source declaration left out of the target on purpose, with a one-line reason")
    p.add_argument("--new", action="append", metavar="NEW.qual:REASON",
                   help="a target declaration with no source counterpart, declared, with a one-line reason")
    p.add_argument("--allow-differs", action="append", dest="allow_differs", metavar="OLD.qual:REASON",
                   help=f"a disclosed difference, counted as weakened ({CONTEXT_PREFIX}<source module> for a "
                        f"module's context rows)")
    p.add_argument("--ledger", required=True, metavar="PATH",
                   help="where to write the ledger: a new file outside SRC and outside the target")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="JSON on stdout: the ledger plus refusals, failures and written")
    rc_ex = """\
examples:
  lean-pkg-extract recheck --ledger ~/ledgers/newlib-1.json --repo ~/paper --target ~/newlib
  lean-pkg-extract recheck --ledger ~/ledgers/newlib-1.json --repo ~/paper --target ~/newlib --commit main

exit codes: 0 the ledger holds (at its commits: recomputed byte for byte; at other commits: nothing fails),
1 refused or failing, 2 usage.
docs: https://lean-pkg.github.io/lean-pkg/extract.html#rechecking-a-ledger"""
    r = sub.add_parser("recheck", formatter_class=fmt, epilog=rc_ex, fromfile_prefix_chars="@",
                       help="re-verify an extraction ledger against the source and the target",
                       description="Read a ledger strictly and recompute it from its own recorded map. At the "
                                   "recorded commits every field must match; at a newer --commit or "
                                   "--target-commit nothing may fail, and what changed is listed as drift. "
                                   "Writes nothing.")
    r.add_argument("--ledger", required=True, metavar="PATH", help=f"the ledger ({EXTRACT_LEDGER_SCHEMA})")
    r.add_argument("--repo", required=True, metavar="SRC", help="the source git repository (its top directory)")
    r.add_argument("--target", required=True, metavar="TDIR", help="the target git repository (its top directory)")
    r.add_argument("--commit", metavar="COMMIT", help="the source commit (default: the ledger's source.commit)")
    r.add_argument("--target-commit", metavar="T",
                   help="the target commit (default: the ledger's target.commit; required when it is null)")
    r.add_argument("--json", dest="json_out", action="store_true",
                   help="JSON on stdout: the recomputed ledger plus refusals, failures, drift and written")


def run(args) -> tuple[str, int]:
    """(stdout text, exit code) of `ledger` or `recheck`; ex.Usage propagates (exit 2)."""
    out, rc = (run_ledger if args.verb == "ledger" else run_recheck)(args)
    if args.json_out:
        return public_json(out), rc
    return render(args.verb, out) + "\n", rc


def argsfile_requests_json(raw: list[str]) -> bool:
    """Whether an @ARGSFILE on the command line holds --json (so a usage error answers in JSON)."""
    for a in raw:
        if a.startswith("@") and len(a) > 1:
            try:
                text = pathlib.Path(os.path.expanduser(a[1:])).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(line.strip() == "--json" for line in text.splitlines()):
                return True
    return False
