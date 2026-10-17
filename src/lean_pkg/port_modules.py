# lean-pkg palomar port-modules -- plan (and apply) the mechanical port to Lean's module system
"""`lean-pkg palomar port-modules NAME [--dry-run | --write] [--agent A] [--json]`.

Palomar requires every tracked `.lean` file to begin with `module` (`palomar check`:
`source.module_required`). This command plans the mechanical part of that port for the package's
tracked `.lean` files at HEAD (git objects, never the working tree) and, with `--write`, applies it to
a clean working tree. It never commits, never builds and never writes the registry.

Per file that is not module-headed yet:

- `module` is inserted as the first command, after any leading blanks and ordinary comments (a module
  docstring `/-! -/` or a doc comment `/-- -/` is a command, so `module` goes before it);
- each header `import X` becomes `public import X`; `meta import X` becomes `public meta import X`;
  `import all X`, `private ...` and `public ...` forms are kept (`public import all` is never written:
  Lean rejects it);
- the body after the header (after module docstrings that follow the imports) is wrapped in
  `public section` ... `end`; when the file leaves a `namespace`/`section` open at the end, no `end`
  is appended (the section then closes at the end of the file) and the imbalance is reported.

A file already module-headed is skipped (so a second run is a no-op). A `prelude` file is refused with
a reason (it does not import `Init`; port it by hand). Not auto-fixed, reported as `review`:
definitions whose body other files unfold (`unfold`, `delta`, `simp [d]`, `rw [d]`, `d.eq_def`, or a
line that mentions `d` with `rfl`/`decide`), which need `@[expose]`; `private` declarations (a private
name in a public statement is an unknown identifier in a module); `#eval`, `native_decide` and
`initialize`, whose code from other modules needs a `meta import`; imports of a module that is not
module-headed (a module cannot import a non-module); scope imbalance.

Verified on Lean v4.35.0-rc2 (see tests/test_port_modules.py and docs/comparator.md, "The module
header"). Excluded: `.lake/`, `lake-packages/`, nested Lake projects (a directory with its own
lakefile) and `path` dependencies of lake-manifest.json; `lakefile.lean` is exempt."""

from __future__ import annotations

import difflib
import json
import os
import pathlib
import re
import tempfile

from . import cli as core
from . import lean_scan
from .palomar_check import Tree, has_module_header

EXCLUDED_DIRS = (".git", ".lake", "lake-packages")
EXEMPT_FILES = ("lakefile.lean",)
LAKEFILES = ("lakefile.lean", "lakefile.toml")
_HEADER_MODIFIERS = ("public", "private", "meta")
_NAME = re.compile(r"(?:«[^»\n]*»|[^\s«».\-/()\[\]{}:,]+)(?:\.(?:«[^»\n]*»|[^\s«».\-/()\[\]{}:,]+))*")
_SCOPE = re.compile(r"(?<![\w.'])(namespace|section|mutual|end)(?![\w.'!?])")
_REWRITE_TACTICS = r"(?:simp|simp_all|dsimp|simp_rw|rw|rwa|erw|rewrite|nth_rewrite|nth_rw|norm_num|field_simp|" \
                   r"simp_arith|unfold_let)\??(?:\s+only)?"
_DEFEQ = re.compile(r"(?<![\w.'])(rfl|decide|native_decide|show|change)(?![\w.'])")
_EVAL = re.compile(r"(?m)^\s*#eval\b|(?<![\w.'])native_decide(?![\w.'])")
_INIT = re.compile(r"(?m)^\s*(?:@\[[^\]]*\]\s*)?(?:(?:private|public|meta)\s+)*(?:builtin_)?initialize\b")
_TOKEN = re.compile(r"(?:«[^»\n]*»|[^\W\d][\w'!?]*)(?:\.(?:«[^»\n]*»|[^\W\d][\w'!?]*))*")
REVIEW_CAP = 5            # reference lines listed per definition


# ------------------------------------------------------------------------------------------- scanning

def _skip_plain(text: str, i: int) -> int:
    """Past blanks (space, CR, LF) and ordinary comments, as Lean's header parser reads them (a plain
    `/-` opener reads one character past it; `/--` and `/-!` are commands and stop the skip)."""
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \r\n\t":
            i += 1
        elif text.startswith("--", i):
            j = text.find("\n", i + 2)
            i = n if j < 0 else j + 1
        elif text.startswith("/-", i) and not text.startswith(("/--", "/-!"), i):
            i += 3
            depth = 1
            while depth and i < n:
                if text.startswith("/-", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("-/", i):
                    depth, i = depth - 1, i + 2
                else:
                    i += 1
            if depth:
                return -1          # an unterminated comment
        else:
            break
    return i


def _block_end(text: str, i: int) -> int:
    """The index after the (nested) block comment opening at text[i]; -1 when unterminated."""
    depth, j, n = 1, i + 2, len(text)
    while j < n and depth:
        if text.startswith("/-", j):
            depth, j = depth + 1, j + 2
        elif text.startswith("-/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    return j if not depth else -1


def parse_header(text: str) -> dict:
    """{first, imports[{start, end, mods, all, name}], end, prelude}: `first` is where `module` goes,
    `end` the index after the last header command (== first when there is no import)."""
    first = _skip_plain(text, 0)
    if first < 0:
        return {"error": "an unterminated block comment before the first command"}
    imports, pos, last_end, prelude = [], first, first, False
    m0 = re.compile(r"module(?![\w.'!?])").match(text, first)
    if m0:                                             # an already module-headed file (re-read after a port)
        pos = last_end = m0.end()
    while True:
        p = _skip_plain(text, pos)
        if p < 0:
            break
        start, mods, q = p, [], p
        while True:
            m = re.compile(r"[A-Za-z_]+").match(text, q)
            if not m or m.group(0) not in _HEADER_MODIFIERS:
                break
            mods.append(m.group(0))
            q = _skip_plain(text, m.end())
            if q < 0:
                break
        if q < 0:
            break
        m = re.compile(r"[A-Za-z_]+(?![\w.'])").match(text, q)
        if m and m.group(0) == "prelude" and not mods and not imports:
            prelude = True
            pos = last_end = m.end()
            continue
        if not m or m.group(0) != "import":
            break
        q = _skip_plain(text, m.end())
        is_all = False
        m2 = re.compile(r"all(?![\w.'])").match(text, q) if q >= 0 else None
        if m2:
            is_all, q = True, _skip_plain(text, m2.end())
        m3 = _NAME.match(text, q) if q >= 0 else None
        if not m3:
            break
        imports.append({"start": start, "end": m3.end(), "mods": mods, "all": is_all, "name": m3.group(0)})
        pos = last_end = m3.end()
    return {"first": first, "imports": imports, "end": last_end, "prelude": prelude}


def scope_balance(text: str) -> tuple[list[str], int]:
    """(scopes still open at the end of the file, count of `end` with no open scope), over the text with
    comments and strings blanked. A heuristic: `namespace`/`section`/`mutual` open, `end` closes."""
    masked, _docs = lean_scan._mask(text)
    stack: list[str] = []
    extra = 0
    for m in _SCOPE.finditer(masked):
        w = m.group(1)
        eol = masked.find("\n", m.end())
        rest = masked[m.end():eol if eol >= 0 else len(masked)]
        if w == "end":
            if stack:
                stack.pop()
            else:
                extra += 1
        elif w == "namespace":
            nm = rest.split()
            stack.append("namespace " + (nm[0] if nm else "?"))
        else:
            nm = rest.split()
            stack.append(w + (" " + nm[0] if w == "section" and nm and _NAME.fullmatch(nm[0]) else ""))
    return stack, extra


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ------------------------------------------------------------------------------------------ the port

def port_text(text: str) -> dict:
    """The port of one non-module file: {status port|refused, text, reason, edits[], reviews[]}."""
    bom = text.startswith("﻿")
    if bom:
        text = text[1:]
    nl = "\r\n" if "\r\n" in text else "\n"
    h = parse_header(text)
    if "error" in h:
        return {"status": "refused", "reason": h["error"], "edits": [], "reviews": []}
    if h["prelude"]:
        return {"status": "refused", "edits": [], "reviews": [],
                "reason": "a `prelude` file (it does not import Init): port it by hand, `module` before "
                          "`prelude` (Lean v4.35.0-rc2 rejects `prelude` then `module`), then check its imports"}
    inserts: list[tuple[int, int, str]] = []          # (pos, order, text)
    edits: list[str] = []
    inserts.append((h["first"], 0, "module" + nl + nl))
    edits.append("insert `module` as the first command")
    rewritten = 0
    for imp in h["imports"]:
        if "public" in imp["mods"] or "private" in imp["mods"] or imp["all"]:
            continue                                   # kept as written (never `public import all`)
        inserts.append((imp["start"], 1, "public "))
        rewritten += 1
    if rewritten:
        edits.append(f"{rewritten} import(s) -> public import")
    kept = len(h["imports"]) - rewritten
    if kept:
        edits.append(f"{kept} import(s) kept as written (`import all`, `private` or `public` forms)")
    # where `public section` goes: after the imports and any module docstrings that follow them
    anchor = h["end"] if h["imports"] else None
    q, n = h["end"], len(text)
    while q < n:
        p = _skip_plain(text, q)
        if p < 0 or p >= n:
            break
        if text.startswith("/-!", p):
            e = _block_end(text, p)
            if e < 0:
                break
            anchor, q = e, e
            continue
        break
    if anchor is None:
        inserts.append((h["first"], 2, "public section" + nl + nl))
    else:
        eol = text.find("\n", anchor)
        tail = text[anchor:eol if eol >= 0 else n]
        if tail.strip() and not tail.strip().startswith("--"):
            inserts.append((anchor, 2, nl + nl + "public section" + nl + nl))
        elif eol < 0:
            inserts.append((n, 2, nl + nl + "public section" + nl))
        else:
            gap = "" if text.startswith(("\n", "\r\n"), eol + 1) or eol + 1 >= n else nl
            inserts.append((eol + 1, 2, nl + "public section" + nl + gap))
    reviews: list[dict] = []
    stack, extra = scope_balance(text[h["end"]:])
    if stack or extra:
        what = []
        if stack:
            what.append(f"{len(stack)} scope(s) still open at the end of the file ({', '.join(stack)})")
        if extra:
            what.append(f"{extra} `end` with no open scope")
        reviews.append({"code": "scope-balance", "line": None,
                        "detail": "; ".join(what) + ": no trailing `end` appended (`public section` closes at "
                                  "the end of the file); balance the scopes by hand if you want one"})
        edits.append("wrap the body in `public section` (no trailing `end`: scopes unbalanced)")
    else:
        body_end = text.rstrip(" \r\n\t")
        inserts.append((len(text), 3, ("" if text.endswith("\n") else nl) + nl + "end" + nl
                        if body_end else "end" + nl))
        edits.append("wrap the body in `public section` ... `end`")
    out = text
    for pos, _o, ins in sorted(inserts, key=lambda t: (t[0], t[1]), reverse=True):
        out = out[:pos] + ins + out[pos:]
    if bom:
        edits.append("remove the byte-order mark (Lean's header parser does not skip it)")
    if not has_module_header(out):
        return {"status": "refused", "edits": [], "reviews": [],
                "reason": "the port did not produce a module header (report this as a lean-pkg bug)"}
    return {"status": "port", "text": out, "edits": edits, "reviews": reviews}


def file_reviews(text: str) -> list[dict]:
    """Per-file review items that do not depend on other files."""
    out = []
    masked, _docs = lean_scan._mask(text)
    for line, kw, qual, _doc, src, _w in lean_scan.declarations(text):
        head = src.split(kw, 1)[0] if kw in src else src
        if re.search(r"(?<![\w.])private(?![\w.])", head):
            out.append({"code": "private", "line": line,
                        "detail": f"private {kw} {qual}: inside a module a private name is not visible in a "
                                  f"public statement (`Unknown identifier`); fine when only proofs use it"})
    for m in _EVAL.finditer(masked):
        what = "#eval" if "#eval" in m.group(0) else "native_decide"
        out.append({"code": "meta", "line": _line_of(text, m.start()),
                    "detail": f"{what}: evaluating a definition from another module needs `public meta import` "
                              f"of it (Lean: `Invalid meta definition ... not accessible here`)"})
    for m in _INIT.finditer(masked):
        out.append({"code": "meta", "line": _line_of(text, m.start()),
                    "detail": "initialize: code it runs from other modules needs a `meta import`; review"})
    return out


def _ref_kinds(masked_line: str, comp: str, qual: str) -> str | None:
    """How a masked source line uses the definition (last component `comp`): "unfold", "defeq" or None."""
    word = r"(?<![\w.'])(?:" + re.escape(qual) + "|" + re.escape(comp) + r")(?![\w'!?])"
    if not re.search(word, masked_line):
        return None
    if re.search(word + r"\.(?:eq_def|eq_unfold|eq_\d+)\b", masked_line):
        return "unfold"
    if re.search(r"(?<![\w.'])(?:unfold|delta)\s+(?:[\w.'«»]+\s+)*?" + word, masked_line):
        return "unfold"
    if re.search(r"(?<![\w.'])" + _REWRITE_TACTICS + r"\s*\[[^\]]*" + word, masked_line):
        return "unfold"
    if _DEFEQ.search(masked_line):
        return "defeq"
    return None


def expose_reviews(files: dict[str, str]) -> list[dict]:
    """Definitions (keyword `def`, not `@[expose]`) another file unfolds: [{decl, file, line, refs[]}].
    `abbrev` and instances are exposed already (checked on v4.35.0-rc2); a file that has
    `@[expose] public section` exposes every definition in it."""
    defs = []
    masked = {p: lean_scan._mask(t)[0] for p, t in files.items()}
    for path, text in files.items():
        whole = "@[expose] public section" in masked[path]
        lines = text.splitlines()
        for line, kw, qual, _doc, src, written in lean_scan.declarations(text):
            if kw != "def" or whole:
                continue
            prev = lines[line - 2] if line >= 2 else ""
            if "expose" in src.split("def", 1)[0] or re.search(r"@\[[^\]]*\bexpose\b", prev):
                continue
            defs.append((path, line, qual, lean_scan.last_component(written)))
    by_comp: dict[str, list[int]] = {}
    for k, (_p, _l, _q, comp) in enumerate(defs):
        by_comp.setdefault(comp, []).append(k)
    found: list[list[dict]] = [[] for _ in defs]
    if by_comp:
        for other, mt in masked.items():          # one pass per line: tokens against the definition names
            for i, ml in enumerate(mt.split("\n"), 1):
                hits = {c for tok in _TOKEN.findall(ml) for c in tok.split(".") if c in by_comp}
                for c in hits:
                    for k in by_comp[c]:
                        if defs[k][0] == other:
                            continue      # the defining module sees its own bodies
                        how = _ref_kinds(ml, c, defs[k][2])
                        if how:
                            found[k].append({"file": other, "line": i, "how": how})
    out = []
    for (path, line, qual, comp), refs in zip(defs, found):
        if refs:
            strong = any(r["how"] == "unfold" for r in refs)
            out.append({"code": "expose", "decl": qual, "file": path, "line": line, "likely": strong,
                        "refs": refs[:REVIEW_CAP], "ref_count": len(refs),
                        "detail": f"def {qual} is {'unfolded' if strong else 'possibly reduced (rfl/decide)'} in "
                                  f"{len({r['file'] for r in refs})} other file(s): add `@[expose]` before `def` "
                                  f"(or `@[expose] public section` in {path}); without it `unfold`, `rfl`, "
                                  f"`simp [{comp}]` and `decide` fail across modules"})
    return out


# ---------------------------------------------------------------------------------------- the package

def _excluded_dirs(tree: Tree, prefix: str, repo: pathlib.Path) -> list[str]:
    """Package-relative directories that are other Lake projects: a nested lakefile, or a `path`
    dependency of lake-manifest.json inside the package."""
    out = set()
    for p in tree.entries:
        if not p.startswith(prefix):
            continue
        rel = p[len(prefix):]
        if "/" in rel and rel.rsplit("/", 1)[1] in LAKEFILES:
            out.add(rel.rsplit("/", 1)[0])
    blob = tree.blob(prefix + "lake-manifest.json")
    if blob:
        try:
            doc = json.loads(blob)
        except ValueError:
            doc = {}
        for pk in doc.get("packages") or [] if isinstance(doc, dict) else []:
            if isinstance(pk, dict) and pk.get("type") == "path" and isinstance(pk.get("dir"), str):
                d = os.path.normpath(pk["dir"]).replace(os.sep, "/")
                if d not in (".", "") and not d.startswith(".."):
                    out.add(d)
    return sorted(out)


def _module_file(mod: str) -> str:
    return "/".join(lean_scan.split_name(mod) or [mod]).replace("«", "").replace("»", "") + ".lean"


def plan(pkg: dict, repo: pathlib.Path) -> dict:
    name = pkg["name"]
    head = core.git_head(repo)
    if head is None:
        raise core.Fail(f"{name}: {repo} is not a git checkout with a commit")
    tree = Tree(repo, head)
    prefix = tree.prefix
    vendored = _excluded_dirs(tree, prefix, repo)
    lean, excluded, outside, links = [], [], 0, []
    for p, (mode, kind, _sha) in sorted(tree.entries.items()):
        if not p.endswith(".lean"):
            continue
        if not p.startswith(prefix):
            outside += 1
            continue
        rel = p[len(prefix):]
        parts = rel.split("/")
        if set(parts[:-1]) & set(EXCLUDED_DIRS) or any(rel.startswith(d + "/") for d in vendored):
            excluded.append(rel)
            continue
        if parts[-1] in EXEMPT_FILES:
            continue
        if mode == "120000":
            links.append(rel)
            continue
        if kind == "blob":
            lean.append(rel)
    blobs = tree.blobs([prefix + r for r in lean])
    files, texts, new_texts = [], {}, {}
    for rel in lean:
        raw = blobs.get(prefix + rel, b"")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            files.append({"path": rel, "action": "refused", "reason": "not UTF-8", "edits": [], "reviews": []})
            continue
        texts[rel] = text
        if has_module_header(text):
            files.append({"path": rel, "action": "skip", "reason": "already module-headed", "edits": [],
                          "reviews": []})
            continue
        r = port_text(text)
        entry = {"path": rel, "action": r["status"], "reason": r.get("reason"), "edits": r["edits"],
                 "reviews": r["reviews"]}
        if r["status"] == "port":
            new_texts[rel] = r["text"]
            entry["reviews"] = r["reviews"] + file_reviews(r["text"])   # lines of the ported file
        files.append(entry)
    for rel in links:
        files.append({"path": rel, "action": "refused", "reason": "a symlink (Palomar rejects symlinked .lean "
                      "files; replace it with the file)", "edits": [], "reviews": []})
    files.sort(key=lambda f: f["path"])
    # imports of a module that will not be module-headed
    deps_root = repo / ".lake" / "packages"
    by_path = {f["path"]: f for f in files}
    for rel, new in new_texts.items():
        for imp in parse_header(new)["imports"]:
            target = _module_file(imp["name"])
            if target in by_path:
                if by_path[target]["action"] == "refused":
                    by_path[rel]["reviews"].append({
                        "code": "imports-non-module", "line": _line_of(new, imp["start"]),
                        "detail": f"imports {imp['name']}, which stays non-module ({by_path[target]['reason']}); "
                                  f"a module cannot import a non-module: port it first"})
                continue
            if target in excluded:
                by_path[rel]["reviews"].append({
                    "code": "imports-non-module", "line": _line_of(new, imp["start"]),
                    "detail": f"imports {imp['name']} from an excluded directory; it must be module-headed too"})
                continue
            if deps_root.is_dir():
                for dep in sorted(deps_root.iterdir()):
                    f = dep / target
                    if f.is_file():
                        try:
                            dep_text = f.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            break
                        if not has_module_header(dep_text):
                            by_path[rel]["reviews"].append({
                                "code": "imports-non-module", "line": _line_of(new, imp["start"]),
                                "detail": f"imports {imp['name']} from the dependency {dep.name}, which is not "
                                          f"module-headed: a module cannot import it (update the dependency)"})
                        break
    ported_view = {rel: new_texts.get(rel, t) for rel, t in texts.items()}
    expose = expose_reviews(ported_view)
    for e in expose:
        if e["file"] in by_path:
            by_path[e["file"]]["reviews"].append({k: e[k] for k in ("code", "line", "detail", "decl", "likely",
                                                                    "refs", "ref_count")})
    for f in files:
        f["reviews"].sort(key=lambda r: (r.get("line") or 0, r["code"]))
    diff = "".join(
        "".join(difflib.unified_diff(texts[rel].splitlines(keepends=True), new_texts[rel].splitlines(keepends=True),
                                     fromfile=f"a/{prefix}{rel}", tofile=f"b/{prefix}{rel}"))
        for rel in sorted(new_texts))
    counts = {a: sum(f["action"] == a for f in files) for a in ("port", "skip", "refused")}
    counts["review"] = sum(len(f["reviews"]) for f in files)
    return {"commit": head, "project_path": prefix.rstrip("/") or ".", "files": files, "counts": counts,
            "expose": expose, "excluded": excluded, "vendored_dirs": vendored, "outside_package": outside,
            "diff": diff, "_new": new_texts}


# ------------------------------------------------------------------------------------------- writing

def dirty_paths(repo: pathlib.Path) -> list[str]:
    p = core.git(repo, "status", "--porcelain", "--untracked-files=normal")
    if p.returncode != 0:
        raise core.Fail(f"cannot read the working-tree status of {repo}")
    return [ln[3:] for ln in p.stdout.splitlines() if ln.strip()]


def _write_atomic(path: pathlib.Path, text: str) -> None:
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".lean-pkg-port-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def follow_ups(pkg: dict, name: str, written: int, expose: list[dict]) -> list[str]:
    out = ["lake build    # in the checkout: the port changes every ported file's interface"]
    if expose:
        out.append(f"review the {len(expose)} `@[expose]` candidate(s) above, then lake build again")
    out.append(f"lean-pkg palomar check {name}    # after you commit: it reads git objects, not the working tree")
    out.append("git diff; commit the port yourself (lean-pkg never commits it)")
    if (pkg.get("gate") or {}).get("receipt"):
        out.append(f"{name} is gated: the sources changed, so the gate receipt no longer binds the new commit; "
                   f"run a new gate (and comparator receipt) at it")
    return out


def run_port(reg: dict, name: str, write: bool, agent: str | None) -> tuple[list[str], dict, int]:
    pkg = core.get_pkg(reg, name)
    repo = core.repo_path(pkg)
    if not repo.is_dir():
        raise core.Fail(f"{name}: local_path does not exist: {repo}")
    rep = plan(pkg, repo)
    new = rep.pop("_new")
    c = rep["counts"]
    refused = [f for f in rep["files"] if f["action"] == "refused"]
    payload = {"ok": not refused, "name": name, "dry_run": not write, "written": [], **rep, "follow_ups": []}
    if write:
        if refused:
            raise core.Fail(f"{name}: {len(refused)} file(s) cannot be ported mechanically; nothing written:\n"
                            + "\n".join(f"  {f['path']}: {f['reason']}" for f in refused))
        if new:
            core.refuse_invalid_lease(name, pkg.get("lease"))
            lease = pkg.get("lease")
            if core.lease_live(lease, core.dt.datetime.now(core.dt.timezone.utc)) and agent != lease.get("agent"):
                raise core.Fail(f"{name}: lease held by {lease.get('agent')} — pass --agent {lease.get('agent')}; "
                                f"nothing written")
            dirty = dirty_paths(repo)
            if dirty:
                raise core.Fail(f"{name}: the working tree of {repo} is not clean ({len(dirty)} path(s)); "
                                f"--write needs a clean tree so the port is the only change; commit or stash "
                                f"first, nothing written:\n" + "\n".join(f"  {p}" for p in dirty[:20]))
            if core.git_head(repo) != rep["commit"]:
                raise core.Fail(f"{name}: HEAD moved during the plan; nothing written (run again)")
            for rel in sorted(new):
                _write_atomic(repo / rel, new[rel])
                payload["written"].append(rel)
        payload["follow_ups"] = follow_ups(pkg, name, len(new), rep["expose"]) if new else []
    head = (f"{name}: port-modules at {rep['commit'][:12]}: {c['port']} file(s) to port, {c['skip']} already "
            f"module-headed, {c['refused']} refused, {c['review']} review item(s)")
    if write:
        head += f" — {len(payload['written'])} file(s) written (not committed)" if new else " — nothing to port"
    else:
        head += " (dry run: nothing written)" if new or refused else " — nothing to port (dry run)"
    lines = [head]
    mark = {"port": "PORT", "skip": "skip", "refused": "✗"}
    for f in rep["files"]:
        if f["action"] == "skip" and not f["reviews"]:
            continue
        lines.append(f"  {mark[f['action']]:4}  {f['path']}" + (f": {f['reason']}" if f.get("reason") else ""))
        for e in f["edits"]:
            lines.append(f"          - {e}")
        for r in f["reviews"]:
            at = f":{r['line']}" if r.get("line") else ""
            lines.append(f"          review [{r['code']}]{at} {r['detail']}")
            for ref in r.get("refs") or []:
                lines.append(f"            used at {ref['file']}:{ref['line']} ({ref['how']})")
    if c["skip"]:
        lines.append(f"  ({c['skip']} already module-headed file(s) skipped)")
    if rep["excluded"]:
        lines.append(f"  excluded (not this package's sources): {len(rep['excluded'])} .lean file(s) under "
                     + ", ".join(sorted({*(d + '/' for d in rep['vendored_dirs']),
                                         *(p.split('/')[0] + '/' for p in rep['excluded']
                                           if p.split('/')[0] in EXCLUDED_DIRS)})))
    if rep["outside_package"]:
        lines.append(f"  {rep['outside_package']} .lean file(s) of the repository lie outside the package and are "
                     f"not touched; palomar check judges the whole repository")
    if rep["diff"] and not write:
        lines.append("")
        lines.append(rep["diff"].rstrip("\n"))
    if payload["follow_ups"]:
        lines.append("next:")
        lines += [f"  {s}" for s in payload["follow_ups"]]
    elif not write and new:
        lines.append(f"next: lean-pkg palomar port-modules {name} --write (a clean working tree; never commits)")
    if len(lines) > 12:
        lines.append(head)
    return lines, payload, 0 if not refused else 1


def cmd_port_modules(args) -> int:
    reg = core.load_registry(core.registry_path(args))
    lines, payload, rc = run_port(reg, args.name, bool(args.write), args.agent)
    if getattr(args, "json_out", False):
        core.print_json(payload)
    else:
        print("\n".join(lines))
    return rc


def add_parser(psub, add_json) -> None:
    p = psub.add_parser("port-modules", help="plan (dry run, default) or --write the mechanical port of the "
                                             "package's tracked .lean files to Lean's module system; never commits")
    p.add_argument("name")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="the default: print the per-file plan and the unified "
                                                          "diff; write nothing")
    g.add_argument("--write", action="store_true", help="apply the plan to a clean working tree (a dirty tree is "
                                                        "refused); never commits, never writes the registry")
    p.add_argument("--agent", default=None, help="with --write: the lease holder, when the package is leased")
    add_json(p)
    p.set_defaults(fn=cmd_port_modules, palomar_fn="port-modules")
