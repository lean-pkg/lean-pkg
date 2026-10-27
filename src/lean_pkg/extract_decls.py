# lean-pkg-extract decls -- write the target modules of a declaration-level extraction, then record its ledger
"""lean-pkg-extract decls (proposal 0009, stage S3): write the target modules and record the ledger.

`lean-pkg-extract decls --repo SRC --commit C --output DIR [--scaffold NAME [--author TEXT] [--license SPDX]]
    --from-module SRC/Path.lean:New.Module ... [--namespace ...] [--rename ...] [--drop ...] [--allow-differs ...]
    --ledger PATH [--dry-run] [--json] [@ARGSFILE]`

Each mapped source module is read at C (git objects only) and written as its target module
(`New/Module.lean` under DIR): every top-level command in source order with the namespace and rename maps
applied as `ledger` applies them (`lean_scan.apply_map`, the command's own namespace scope), the
declarations named in `--drop` left out with their docstrings, the header's imports of mapped modules
rewritten to their target modules, and every comment except docstrings removed (so no source path or
provenance note is carried). An import of a module of the source that is not extracted is refused.

With `--scaffold NAME` DIR also becomes a package: the pins copied from the source commit (the Lake package
holding the modules): `lean-toolchain` byte for byte, and `lake-manifest.json` with every dependency pin
as it is and only its root package name set to NAME (the source's name may be private), a `lakefile.toml` naming one
library per target root and requiring the source manifest's direct dependencies at their pinned
revisions, a README skeleton, and the package files of `lean-pkg new` (`scaffold.package_files`:
.gitignore, CITATION.cff, LICENSE, LICENSES/, REUSE.toml); then `git init`, nothing committed.

The tree is compared with the source exactly as `ledger` compares a hand-ported one (method text/1) before
anything is written; a refusal or a failure writes nothing. Then it is written through
`extract_write.write_tree`: staged beside DIR, scanned with public-safety (a blocking hit writes nothing),
the ledger written (`target.commit: null`, since nothing is committed), and the tree moved into place
with one `os.replace`. `--dry-run` does all of it but the writing.

After `git add -A && git commit` in DIR (the tree as written), `lean-pkg-extract recheck --ledger PATH
--repo SRC --target DIR --target-commit HEAD` recomputes the ledger byte for byte.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import subprocess

from . import core
from . import extract as ex
from . import extract_ledger as el
from . import extract_write as xw
from . import lean_scan
from . import scaffold

TOOL = "lean-pkg-extract decls"
WRITTEN = "the written tree"
LAKEFILES = el.LAKEFILES
_IMPORT_AT = re.compile(r"(?<![\w'.!?«»])(import[ \t]+(?:all[ \t]+)?)(" + lean_scan._NAME.pattern + ")")
README = """\
# {name}

A Lean 4 library, written with `lean-pkg-extract decls` from the declarations of an existing project.
Its modules:

{modules}

Build it with `lake build`. Its extraction ledger (lean-pkg-extract-ledger/1), which records that every
declaration is the source's, is kept outside this repository: it names the private source.

{credit}
"""


# ----------------------------------------------------------------------------------------------------
# rendering one target module

def _keep_text(text: str) -> str:
    """The file with every comment blanked except docstrings (`/-- -/`), same length and lines."""
    return lean_scan._blank(text, lean_scan._lex(text), ("comment", "moddoc"))


def _doc_lines_before(text: str, span_start_line: int) -> set[int]:
    """0-based line numbers of the docstring standing right before the line span_start_line (1-based)."""
    regions = lean_scan._lex(text)
    masked = lean_scan._blank(text, regions, lean_scan._COMMENT_KINDS + ("string", "char"))
    starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            starts.append(i + 1)
    pos = starts[span_start_line - 1]
    for a, b, kind in reversed(regions):
        if b > pos:
            continue
        if kind == "doc" and not masked[b:pos].strip():
            first, last = text.count("\n", 0, a), text.count("\n", 0, b)
            return set(range(first, last + 1))
        break
    return set()


def render_module(text: str, decls, context, *, ns, rn, declared, dropped: set[str],
                  rewrite_import) -> str:
    """The target text of one source module: every command with the maps applied (a header import
    through rewrite_import(name) -> new name), the dropped declarations removed with their docstrings,
    non-doc comments removed, comment-only lines and runs of blank lines between commands removed."""
    orig = text.split("\n")
    keep = _keep_text(text).split("\n")
    out = list(keep)
    removed: set[int] = set()
    in_span: set[int] = set()
    for s in sorted(list(decls) + list(context), key=lambda s: s.start):
        lines = range(s.start - 1, s.end)
        in_span.update(lines)
        if s.key in dropped:
            removed.update(lines)
            removed.update(_doc_lines_before(text, s.start))
            continue
        chunk = "\n".join(keep[s.start - 1:s.end])
        if s.kind == "import":
            chunk = _IMPORT_AT.sub(lambda m: m.group(1) + rewrite_import(m.group(2)), chunk)
        elif s.kind not in lean_scan._HEADER_WORDS:
            chunk = lean_scan.apply_map(chunk, ns, rn, src_scope=s.scope, declared=declared)
        new = chunk.split("\n")
        if len(new) != len(lines):                          # apply_map rewrites identifiers only
            raise lean_scan.ScanError(f"line {s.start}: the mapped command changed its line count")
        out[s.start - 1:s.end] = new
    lines_out: list[str] = []
    for i, line in enumerate(out):
        if i in removed:
            continue
        if keep[i] != orig[i]:                              # a comment was blanked on this line
            line = line.rstrip()
            if not line.strip() and orig[i].strip():
                continue                                    # a comment-only line
        if not line.strip() and i not in in_span and (not lines_out or not lines_out[-1].strip()):
            continue                                        # a second blank line between commands
        lines_out.append(line)
    while lines_out and not lines_out[-1].strip():
        lines_out.pop()
    return "\n".join(lines_out) + "\n"


# ----------------------------------------------------------------------------------------------------
# the target tree

def _source_modules(src: el.Tree, spec: dict, refusals: list[str]):
    """[(path, source module, target module, text, root)] of the mapped modules readable at the commit, and
    the pins' Lake root."""
    warnings: list[str] = []
    paths = [p for p, _ in sorted(spec["modules"]) if not ex.xp.bad_rel_path(p)]
    tree, texts, _recs, _pins = el._side(src, paths, "source", refusals, warnings)
    roots = sorted({el._lake_root(p, tree) for p in texts})
    out = []
    for path, tmod in sorted(spec["modules"]):
        if path in texts:
            root = el._lake_root(path, tree)
            out.append((path, el._module_name(path, root), tmod, texts[path], root))
    return out, (roots[0] if roots else "")


def generate(src: el.Tree, spec: dict, refusals: list[str]) -> dict[str, bytes]:
    """{target path: bytes} of the target modules (refusals for what cannot be written)."""
    mods, _root = _source_modules(src, spec, refusals)
    split = {}
    for path, smod, _tmod, text, _r in mods:
        try:
            split[path] = lean_scan.decl_spans(text, smod)
        except lean_scan.ScanError as e:
            refusals.append(f"source {path}: cannot be split into declarations ({e}); text/1 refuses the file whole")
    if refusals:
        return {}
    units = [(path, s) for path, (decls, _c) in split.items() for s in decls]
    declared = sorted({n for _p, s in units for n in s.names})
    dropped: dict[str, set[str]] = {}
    for decl, _reason in spec["drops"]:
        for path, s in units:
            if decl == s.key or decl in s.names:
                dropped.setdefault(path, set()).add(s.key)
                break
    mod_map = {lean_scan._canon(lean_scan.split_name(smod)): tmod for _p, smod, tmod, _t, _r in mods}
    files: dict[str, bytes] = {}
    for path, _smod, tmod, text, root in mods:
        def rewrite(name: str, path=path, root=root) -> str:
            parts = lean_scan.split_name(name)
            if parts is None:
                return name
            new = mod_map.get(lean_scan._canon(parts))
            if new is not None:
                return new
            rel = lean_scan.module_file(name)
            if rel is not None and ((f"{root}/" if root else "") + rel) in src.tree:
                refusals.append(f"source {path} imports {name}, a module of the source at {src.label} that is not "
                                f"extracted: map it with --from-module, or remove the import from the source")
            return name
        decls, context = split[path]
        try:
            body = render_module(text, decls, context, ns=spec["namespaces"], rn=spec["renames"],
                                 declared=declared, dropped=dropped.get(path, set()), rewrite_import=rewrite)
        except (lean_scan.MapError, lean_scan.ScanError) as e:
            refusals.append(f"source {path}: cannot be written as {tmod} ({e})")
            continue
        files[lean_scan.module_file(tmod)] = body.encode("utf-8")
    return files


def _toml(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)       # a JSON string is a TOML basic string


def lakefile(name: str, target_modules: list[str], manifest: bytes | None, warnings: list[str]) -> str:
    """lakefile.toml: one library per target root (globbing the root module and everything under it), and
    a require for each direct dependency of the source's lake-manifest.json at its pinned input revision."""
    roots: dict[str, list[str]] = {}
    for m in sorted(target_modules):
        r = lean_scan.split_name(m)[0]
        globs = roots.setdefault(r, [])
        g = r if m == r else f"{r}.+"
        if g not in globs:
            globs.append(g)
    lines = [f"name = {_toml(name)}", "defaultTargets = [" + ", ".join(_toml(r) for r in roots) + "]"]
    doc = None
    if manifest is not None:
        doc, _err = ex.load_json_strict(manifest, "lake-manifest.json")
    for p in (doc.get("packages") if isinstance(doc, dict) else None) or []:
        if not isinstance(p, dict) or p.get("inherited") is not False or not isinstance(p.get("name"), str):
            continue
        rev = p.get("inputRev") or p.get("rev")
        if p.get("type") != "git" or not isinstance(rev, str):
            warnings.append(f"lake-manifest.json: dependency {p['name']} is not a git dependency with a revision "
                            f"(type {p.get('type')!r}); lakefile.toml does not require it: add it by hand")
            continue
        lines += ["", "[[require]]", f"name = {_toml(p['name'])}"]
        if isinstance(p.get("scope"), str) and p["scope"]:
            lines.append(f"scope = {_toml(p['scope'])}")
        else:
            lines.append(f"git = {_toml(str(p.get('url')))}")
        lines.append(f"rev = {_toml(rev)}")
    for r, globs in roots.items():
        lines += ["", "[[lean_lib]]", f"name = {_toml(r)}", "globs = [" + ", ".join(_toml(g) for g in globs) + "]"]
    return "\n".join(lines) + "\n"


def _renamed_manifest(data: bytes, name: str, warnings: list[str]) -> bytes:
    """The source's lake-manifest.json with its root package `name` set to the new package's: the
    dependency pins are kept as they are, and the source package's own name (which may be private) is not
    carried into the public tree. A manifest that is not strict JSON is copied unchanged, with a warning."""
    doc, err = ex.load_json_strict(data, "lake-manifest.json")
    if err or not isinstance(doc, dict):
        warnings.append("--scaffold: the source lake-manifest.json is not a JSON object; copied unchanged (its "
                        "root package name is the source's)")
        return data
    if doc.get("name") == name or "name" not in doc:
        return data
    doc["name"] = name
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def scaffold_files(src: el.Tree, root: str, name: str, author: str, license_spdx: str, target_modules: list[str],
                   refusals: list[str], warnings: list[str]) -> dict[str, bytes]:
    """The package around the modules: the pins copied from the source commit, lakefile.toml, README.md and
    `scaffold.package_files` (the files of `lean-pkg new` without its example theorem)."""
    where = f"{root}/" if root else ""
    ents = {f: src.tree.get(where + f) for f in ("lean-toolchain", "lake-manifest.json")}
    blobs = src.read([e["oid"] for e in ents.values() if e])
    pins = {f: blobs.get(e["oid"]) if e else None for f, e in ents.items()}
    if pins["lean-toolchain"] is None:
        refusals.append(f"--scaffold: no {where}lean-toolchain at {src.label} beside the modules; the pins are "
                        f"copied from the source commit, so it needs one")
    if pins["lake-manifest.json"] is None:
        warnings.append(f"--scaffold: no {where}lake-manifest.json at {src.label}; none is written and "
                        f"lakefile.toml requires nothing (run `lake update` in the new package)")
    out: dict[str, bytes] = {f: b for f, b in pins.items() if b is not None}
    if pins["lake-manifest.json"] is not None:
        out["lake-manifest.json"] = _renamed_manifest(pins["lake-manifest.json"], name, warnings)
    out["lakefile.toml"] = lakefile(name, target_modules, pins["lake-manifest.json"], warnings).encode("utf-8")
    from . import page as page_mod
    out["README.md"] = README.format(name=name, modules="\n".join(f"- `{m}`" for m in sorted(target_modules)),
                                     credit=page_mod.CREDIT).encode("utf-8")
    for rel, text in scaffold.package_files(name, author, license_spdx, dt.date.today().year, tool=TOOL).items():
        out[rel] = text.encode("utf-8")
    return out


def _inside_package(out: pathlib.Path) -> pathlib.Path | None:
    """The nearest ancestor of DIR (resolved) holding a Lake configuration file, or None."""
    for d in xw._resolved(out).parents:
        if any((d / f).is_file() for f in LAKEFILES):
            return d
    return None


def _object_format(path: pathlib.Path | None) -> str:
    """The object format of the repository at path, or (path None) of a repository `git init` would make."""
    args = ["git", "-C", str(path), "rev-parse", "--show-object-format"] if path is not None else \
        ["git", "config", "--get", "init.defaultObjectFormat"]
    p = subprocess.run(args, capture_output=True, text=True, env=core.scrubbed_env(), stdin=subprocess.DEVNULL,
                       check=False)
    fmt = p.stdout.strip()
    return fmt if p.returncode == 0 and fmt in ("sha1", "sha256") else "sha1"


# ----------------------------------------------------------------------------------------------------
# the verb

def run_decls(args) -> tuple[dict, int]:
    repo = pathlib.Path(args.repo).expanduser()
    top, gitdir = ex._src_top(repo)
    if args.scaffold is None and (args.author is not None or args.license is not None):
        raise ex.Usage("--author and --license describe the package of --scaffold NAME; pass --scaffold")
    refusals: list[str] = []
    warnings: list[str] = []
    spec = el.parse_spec(args, refusals)
    out_dir = pathlib.Path(args.output).expanduser()
    refusals += xw.output_refusals(out_dir, "--output", [("the source repository", top),
                                                          ("the source repository's git directory", gitdir)])
    name = author = None
    lic = args.license or "Apache-2.0"
    if args.scaffold is not None:
        name = args.scaffold
        if not scaffold.NAME_RE.match(name) or len(name) > scaffold.NAME_MAX:
            refusals.append(f"--scaffold {name!r}: lower-case letters, digits and single hyphens, starting with a "
                            f"letter, at most {scaffold.NAME_MAX} characters (e.g. my-lib), as `lean-pkg new`")
        author = (args.author if args.author is not None else scaffold._git_author() or "").strip()
        if not author or "\n" in author:
            refusals.append("--scaffold: CITATION.cff needs an author: pass --author 'Given Family' (or set git "
                            "config user.name)")
        else:
            warnings += scaffold.author_warnings(author)
        pkg_dir = _inside_package(out_dir)
        if pkg_dir is not None:
            refusals.append(f"--scaffold: --output {args.output} lies inside an existing Lake package ({pkg_dir.name}/ "
                            f"holds a lakefile); --scaffold writes a new package, so name a directory outside it")
    target_root = xw._resolved(out_dir)
    refusals += el.ledger_path_refusals(args.ledger, (top, gitdir), (target_root,), "inside --output DIR")
    c = ex.resolve_commit(repo, args.commit)
    if not c:
        refusals.append(f"--commit {args.commit!r} is not a commit of the source repository")
    files: dict[str, bytes] = {}
    fmt = "sha1"
    led, failures = el.skeleton(spec), []
    led["source"]["commit"] = c
    if c:
        src = el.git_tree(repo, c)
        el.check_spec(spec, refusals)
        if not refusals:
            files = generate(src, spec, refusals)
        if not refusals and args.scaffold is not None:
            _mods, root = _source_modules(src, spec, [])
            files.update(scaffold_files(src, root, name, author, lic, [m for _p, m in spec["modules"]],
                                        refusals, warnings))
        if not refusals:
            fmt = _object_format(None) if args.scaffold is not None else "sha1"
            led, failures = el.compute(repo, c, None, None, spec, refusals,
                                       target=el.files_tree(files, WRITTEN, fmt))
    else:
        el.check_spec(spec, refusals)
    out = el._output(led, refusals, failures)
    out["warnings"] = sorted(set(out["warnings"]) | set(warnings))
    out["ledger_path"] = args.ledger
    out["output"] = {"dir": args.output, "files": sorted(files), "scaffold": name, "dry_run": bool(args.dry_run),
                     "written": False, "git_init": False, "committed": False, "object_format": fmt}
    out["public_safety"] = None
    if not out["ok"]:
        return out, 1
    if args.dry_run:
        return out, 0

    state = {"led": led}

    def prepare(stage: pathlib.Path) -> None:
        if args.scaffold is None:
            return
        p = subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", str(stage)], capture_output=True,
                           text=True, env=core.scrubbed_env(), stdin=subprocess.DEVNULL, check=False)
        if p.returncode != 0:
            raise xw.WriteRefused([f"git init failed: {(p.stderr or p.stdout).strip()}; nothing written"])
        actual = _object_format(stage)
        if actual != fmt:                               # the blob ids follow the new repository's format
            again: list[str] = []
            led2, fail2 = el.compute(repo, c, None, None, spec, again, target=el.files_tree(files, WRITTEN, actual))
            if again or fail2:
                raise xw.WriteRefused(again + fail2)
            state["led"] = led2
            out["output"]["object_format"] = actual

    def before_move(_stage: pathlib.Path):
        ledger = pathlib.Path(args.ledger).expanduser()
        with open(ledger, "x", encoding="utf-8") as f:
            f.write(el.ledger_json(state["led"]))
        return lambda: ledger.unlink(missing_ok=True)

    try:
        scan = xw.write_tree(out_dir, files, scan_pkg={"name": name or "lean-pkg-extract-decls"}, patterns_root=top,
                             source_repo=top or gitdir, prepare=prepare, before_move=before_move)
    except xw.WriteRefused as e:
        out["refusals"] = sorted(set(out["refusals"]) | set(e.reasons))
        if e.scan is not None:
            out["public_safety"] = _scan_summary(e.scan)
        out["ok"] = False
        return out, 1
    except OSError as e:
        out["refusals"].append(f"--output {args.output}: writing failed ({e.strerror or e}); nothing written, "
                               f"the staging directory removed")
        out["ok"] = False
        return out, 1
    out.update({k: state["led"][k] for k in el.LEDGER_KEYS if k not in ("ok",)})
    out["warnings"] = sorted(set(state["led"]["warnings"]) | set(warnings))
    out["public_safety"] = _scan_summary(scan)
    out["written"] = True
    out["output"].update(written=True, git_init=args.scaffold is not None)
    return out, 0


def _scan_summary(scan: dict) -> dict:
    return {"ok": scan["ok"], "block_hits": scan["block_hits"], "warn_hits": scan["warn_hits"]}


def render(out: dict) -> str:
    """The text report: the ledger's (verdict first and last), then what was or would be written."""
    lines = el.render("decls", out).split("\n")
    o = out["output"]
    extra = []
    if o["files"]:
        verb = "written" if o["written"] else "to write"
        extra.append(f"files {verb} under {o['dir']} ({len(o['files'])}):")
        extra += [f"  {f}" for f in o["files"]]
    ps_ = out.get("public_safety")
    if ps_ is not None:
        extra.append(f"public-safety of the staged tree: {ps_['block_hits']} blocking, {ps_['warn_hits']} warn-only")
    if o["written"]:
        nxt = [f"cd {o['dir']}"]
        if o["git_init"]:
            nxt += ["git add -A && git commit -m 'extracted library'"]
        else:
            nxt += ["git init && git add -A && git commit -m 'extracted library'   # or place the modules in your "
                    "package and record a new ledger there with `lean-pkg-extract ledger`"]
        nxt.append(f"lean-pkg-extract recheck --ledger {out['ledger_path']} --repo SRC --target {o['dir']} "
                   f"--target-commit HEAD")
        extra.append("next (nothing is committed):")
        extra += [f"  {n}" for n in nxt]
    return "\n".join(lines[:-1] + extra + lines[-1:])


def json_text(out: dict) -> str:
    o = dict(out)
    o["output"] = {k: v for k, v in out["output"].items() if k != "dir"}
    return el.public_json(o)


def run(args) -> tuple[str, int]:
    out, rc = run_decls(args)
    return (json_text(out) if args.json_out else render(out) + "\n"), rc


def add_parser(sub, parser_cls, fmt) -> None:
    epilog = """\
examples:
  lean-pkg-extract decls --repo ~/paper --commit HEAD --output ~/newlib \\
      --from-module Paper/Bounds.lean:NewLib.Bounds --namespace Paper.Bounds=NewLib.Bounds \\
      --rename Paper.Bounds.main=NewLib.Bounds.main_bound --drop 'Paper.Bounds.scratch:exploration only' \\
      --scaffold newlib --author 'Ada Lovelace' --ledger ~/ledgers/newlib-1.json
  lean-pkg-extract decls @decls-args.txt --dry-run

@ARGSFILE: as for `ledger` (one argument per line; `--flag value` lines keep the rest of the line).
exit codes: 0 written (or, with --dry-run, writable) and every declaration IDENTICAL or dropped, 1 refused
(nothing written), 2 usage.
docs: https://lean-pkg.github.io/lean-pkg/extract.html#writing-the-target-modules-decls"""
    p = sub.add_parser("decls", formatter_class=fmt, epilog=epilog, fromfile_prefix_chars="@",
                       help="write the target modules of a declaration-level extraction (optionally a whole "
                            "package) and record its extraction ledger",
                       description="Read the mapped source modules at COMMIT, write each as its target module "
                                   "(the namespace and rename maps applied, --drop declarations left out, imports "
                                   "of mapped modules rewritten, comments other than docstrings removed) into "
                                   "--output DIR, which must be absent or empty and outside SRC, and record the "
                                   f"ledger ({el.EXTRACT_LEDGER_SCHEMA}, target.commit null) to --ledger PATH. "
                                   "The tree is compared with the source (text/1) before anything is written, "
                                   "staged beside DIR, scanned with public-safety and moved into place in one "
                                   "step. --scaffold NAME adds the package files and runs git init; nothing is "
                                   "committed.")
    p.add_argument("--repo", required=True, metavar="SRC", help="the source git repository (its top directory)")
    p.add_argument("--commit", required=True, metavar="COMMIT", help="the source commit; recorded as a full SHA")
    p.add_argument("--output", required=True, metavar="DIR",
                   help="where to write: a new or empty directory, outside SRC, not a symbolic link")
    p.add_argument("--from-module", action="append", required=True, metavar="SRC/Path.lean:New.Module",
                   help="a source module and the target module to write it as (repeatable, at least one)")
    p.add_argument("--namespace", action="append", metavar="OLD.Ns=NEW.Ns",
                   help="a namespace prefix map (repeatable; the longest prefix wins)")
    p.add_argument("--rename", action="append", metavar="OLD.qual=NEW.qual",
                   help="a declaration renamed, fully qualified (repeatable; takes precedence over --namespace)")
    p.add_argument("--drop", action="append", metavar="OLD.qual:REASON",
                   help="a source declaration left out of the target, with a one-line reason")
    p.add_argument("--scaffold", metavar="NAME",
                   help="also write the package: lean-toolchain and lake-manifest.json copied from the source "
                        "commit, lakefile.toml, README.md, CITATION.cff, LICENSE, REUSE.toml; then git init "
                        "(nothing committed)")
    p.add_argument("--author", metavar="TEXT", help="with --scaffold: the CITATION.cff author (default: git config "
                                                    "user.name)")
    p.add_argument("--license", choices=scaffold.LICENSES, help="with --scaffold: the LICENSE written (default "
                                                                "Apache-2.0)")
    p.add_argument("--ledger", required=True, metavar="PATH",
                   help="where to write the ledger: a new file outside SRC and outside DIR")
    p.add_argument("--dry-run", action="store_true",
                   help="read and compare, report what would be written, and write nothing (the public-safety scan "
                        "of the staged tree runs only when writing)")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="JSON on stdout: the ledger plus refusals, failures, written, output and public_safety")
    # a written target holds every source declaration as written or dropped: nothing is NEW and nothing
    # DIFFERS, so `decls` takes no --new or --allow-differs (the ledger's map records them empty)
    p.set_defaults(new=None, allow_differs=None)
