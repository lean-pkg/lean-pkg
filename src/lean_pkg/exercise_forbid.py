# lean-exercise --forbid: which constants a judged theorem's proof depends on, read from a kernel export.
"""Forbidden constants for lean-exercise, computed on the compiled environment, never on the source text.

The Solution is exported with the toolchain's own `leanexport` (NDJSON, the format `lake comparator`
replays), inside the same kind of bwrap sandbox `lake comparator` uses: the learner's `.olean` files are
never loaded into this process. The export is then read here as data.

Scope `solution` (the default): start at the judged theorem's proof term, follow every constant the
Solution itself declares (helper lemmas, private lemmas, `_proof_n` and `match_n` auxiliaries, local
definitions and instances: everything not found in the environment of the Solution's own imports), and
report every constant reached whose name matches a pattern. Library constants (Mathlib, Defs, Lean core)
are reported but not entered. A helper lemma therefore cannot launder a forbidden dependency, while a
Mathlib lemma that Mathlib happens to prove with a forbidden one is not held against the learner.
Scope `all` enters every constant, library ones included.

Constants that occur in the judged theorem's statement are never reported: the statement is fixed by the
Challenge, so the learner cannot avoid them.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess

DECL_KEYS = ("axiom", "def", "opaque", "thm", "quot", "inductive")
NAME_CHUNK = 400_000                 # bytes of constant names per leanexport call (argv stays far below ARG_MAX)


class ExportError(Exception):
    """The export could not be produced or read: the forbidden-constant check fails closed."""


def match(name: str, prefixes: list[str], exact: list[str]) -> bool:
    return name in exact or any(name.startswith(p) for p in prefixes)


# ---------------------------------------------------------------------------------------------------------
# Reading an NDJSON export (lean4export format 3.x)

class Export:
    """The parts of an export the dependency walk needs: names, expression edges, declaration roots."""

    def __init__(self) -> None:
        self.names: dict[int, tuple] = {}            # name id -> component tuple
        self.exprs: dict[int, tuple] = {}            # expr id -> (const name ids, child expr ids)
        self.decls: dict[tuple, tuple] = {}          # name -> (type expr ids, value expr ids, extra name ids)

    def text(self, comps: tuple) -> str:
        return ".".join(str(c) for c in comps)


def _name(ex: Export, obj: dict) -> None:
    if "str" in obj:
        pre, s = obj["str"]["pre"], obj["str"]["str"]
    else:
        pre, s = obj["num"]["pre"], int(obj["num"]["i"])
    ex.names[obj["in"]] = ex.names.get(pre, ()) + (s,)


def _expr(ex: Export, obj: dict) -> None:
    i = obj.pop("ie")
    (kind, body), = obj.items()
    if kind == "const":
        ex.exprs[i] = ((body["name"],), ())
    elif kind == "app":
        ex.exprs[i] = ((), (body["fn"], body["arg"]))
    elif kind in ("lam", "forallE"):
        ex.exprs[i] = ((), (body["type"], body["body"]))
    elif kind == "letE":
        ex.exprs[i] = ((), (body["type"], body["value"], body["body"]))
    elif kind == "proj":
        ex.exprs[i] = ((body["typeName"],), (body["struct"],))
    elif kind == "mdata":
        ex.exprs[i] = ((), (body["expr"],))
    else:                                            # bvar, sort, natVal, strVal
        ex.exprs[i] = ((), ())


def _decl(ex: Export, kind: str, body: dict, with_exprs: bool) -> None:
    def put(name_id, types, values, extra):
        ex.decls[ex.names[name_id]] = (tuple(types), tuple(values), tuple(extra)) if with_exprs else ((), (), ())
    if kind != "inductive":
        put(body["name"], [body["type"]], [body["value"]] if "value" in body else [], [])
        return
    members = [t["name"] for t in body["types"]] + [c["name"] for c in body["ctors"]] + \
        [r["name"] for r in body["recs"]]
    types = [x["type"] for x in body["types"] + body["ctors"] + body["recs"]]
    rhs = [rule["rhs"] for r in body["recs"] for rule in r["rules"]]
    for m in members:                                # a block is one unit: any member reaches all of it
        put(m, types, rhs, members)


def read_export(path: pathlib.Path, with_exprs: bool = True) -> Export:
    ex = Export()
    try:
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                if "in" in obj:
                    _name(ex, obj)
                elif "ie" in obj:
                    if with_exprs:
                        _expr(ex, obj)
                elif "il" in obj or "meta" in obj:
                    continue
                else:
                    kind = next((k for k in DECL_KEYS if k in obj), None)
                    if kind is None:
                        raise ExportError(f"export line {n}: unknown record {sorted(obj)[:3]}")
                    _decl(ex, kind, obj[kind], with_exprs)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ExportError(f"cannot read the export: {e}") from None
    return ex


# ---------------------------------------------------------------------------------------------------------
# The walk

def _refs(ex: Export, roots, seen: set[int]) -> set[tuple]:
    """Constant names reachable inside the expressions `roots` (no unfolding of constants)."""
    out: set[tuple] = set()
    stack = [r for r in roots if r not in seen]
    while stack:
        e = stack.pop()
        if e in seen:
            continue
        seen.add(e)
        consts, kids = ex.exprs.get(e, ((), ()))
        for c in consts:
            out.add(ex.names[c])
        stack.extend(k for k in kids if k not in seen)
    return out


def literal(comps: tuple) -> str | None:
    """A name literal `leanexport` decodes back to these components, or None if there is none."""
    parts = []
    for c in comps:
        if not isinstance(c, str) or "»" in c or not c:
            return None
        parts.append(f"«{c}»")                      # always escaped: `leanexport` decodes «x» as x
    return ".".join(parts)


def find_forbidden(ex: Export, theorem: str, prefixes: list[str], exact: list[str], scope: str,
                   classify) -> list[dict]:
    """[{constant, via}] for the theorem `theorem` in `ex`. `classify(names) -> set of library names`
    is called with batches of names not yet known to be library or local (scope `solution` only)."""
    root = tuple(theorem.split("."))
    if root not in ex.decls:
        raise ExportError(f"{theorem} is not in the Solution's export")
    rtypes, rvalues, _ = ex.decls[root]
    statement = _refs(ex, rtypes, set())
    seen_exprs: set[int] = set()
    parent: dict[tuple, tuple] = {}
    local: set[tuple] = {root}
    library: set[tuple] = set()
    frontier = [root]
    reached: dict[tuple, tuple] = {}                 # constant -> the local constant that mentions it
    while frontier:
        found: set[tuple] = set()
        for c in frontier:
            types, values, extra = ex.decls.get(c, ((), (), ()))
            refs = _refs(ex, values if c == root else types + values, seen_exprs)
            refs |= {ex.names[x] for x in extra}
            for r in refs:
                if r not in reached and r != c:
                    reached[r] = c
                    found.add(r)
        unknown = {r for r in found if r not in local and r not in library and r in ex.decls}
        if scope == "all":
            new_local = unknown
        else:
            new_local = unknown - classify(unknown)
            library |= unknown - new_local
        for r in new_local:
            parent[r] = reached[r]
        local |= new_local
        frontier = sorted(new_local)
    hits = []
    for r, by in sorted(reached.items()):
        name = ex.text(r)
        if r in statement or not match(name, prefixes, exact):
            continue
        chain, x = [], by
        while x != root:
            chain.append(ex.text(x))
            x = parent[x]
        hits.append({"constant": name, "via": chain[::-1]})
    return hits


def private_module(comps: tuple) -> str | None:
    """`_private.Mod.Sub.0.x` -> "Mod.Sub" (a private name carries its module)."""
    if not comps or comps[0] != "_private":
        return None
    mod = []
    for c in comps[1:]:
        if not isinstance(c, str):
            return ".".join(mod)
        mod.append(c)
    return None


# ---------------------------------------------------------------------------------------------------------
# Running leanexport (sandboxed unless the run is a self-check)

def bwrap_args(readable: list[pathlib.Path], env: dict[str, str], cwd: pathlib.Path) -> list[str]:
    """The bwrap command line `lake comparator` uses for its exporter: `/` read-only, the home
    directories, /tmp, /run and /var covered, `readable` bound back read-only, nothing writable, no
    network, a cleared environment."""
    bw = os.environ.get("COMPARATOR_BWRAP") or shutil.which("bwrap") or "bwrap"
    args = [bw, "--ro-bind", "/", "/", "--tmpfs", "/home", "--tmpfs", "/root", "--tmpfs", "/run/user",
            "--tmpfs", "/tmp", "--dir", "/tmp/home", "--dev", "/dev", "--proc", "/proc", "--clearenv",
            "--tmpfs", "/run", "--tmpfs", "/var"]
    for p in dict.fromkeys(str(x) for x in readable):
        args += ["--ro-bind", p, p]
    for k, v in env.items():
        args += ["--setenv", k, v]
    return args + ["--setenv", "HOME", "/tmp/home", "--unshare-all", "--die-with-parent", "--new-session",
                   "--chdir", str(cwd), "--"]


def run_export(prefix: pathlib.Path, lean_path: list[pathlib.Path], readable: list[pathlib.Path],
               cwd: pathlib.Path, modules: list[str], names: list[str], out: pathlib.Path, sandboxed: bool,
               timeout: float, ignore_missing: bool = False) -> None:
    exe = prefix / "bin" / "leanexport"
    env = {"PATH": f"{prefix / 'bin'}:/usr/bin:/bin", "LEAN_PATH": os.pathsep.join(map(str, lean_path)),
           "LEAN_ABORT_ON_PANIC": "1"}
    cmd = [str(exe)] + modules + ["--"] + names + (["--ignore-missing"] if ignore_missing else [])
    if sandboxed:
        cmd = bwrap_args([prefix, cwd] + readable, env, cwd) + cmd
        run_env = {}
    else:
        run_env = {**os.environ, **env}
    try:
        with open(out, "wb") as f:
            p = subprocess.run(cmd, cwd=cwd, env=run_env, stdin=subprocess.DEVNULL, stdout=f,
                               stderr=subprocess.PIPE, timeout=timeout, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise ExportError("leanexport did not finish in time (--timeout)") from None
    except OSError as e:
        raise ExportError(f"cannot run leanexport: {e}") from None
    if p.returncode != 0:
        tail = p.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [f"exit {p.returncode}"]
        raise ExportError(f"leanexport failed: {tail[0][:300]}")


def library_classifier(prefix, lean_path, readable, cwd, imports: list[str], solution_module: str,
                       scratch: pathlib.Path, sandboxed: bool, timeout: float):
    """classify(names) -> the subset declared in the environment of `imports` (the Solution's own imports,
    plus Init), found by exporting those names from that environment with --ignore-missing."""
    modules = list(dict.fromkeys(["Init"] + imports))
    calls = [0]

    def classify(names: set[tuple]) -> set[tuple]:
        lib: set[tuple] = set()
        ask: dict[str, tuple] = {}
        for n in names:
            mod = private_module(n)
            if mod is not None:
                if mod != solution_module:
                    lib.add(n)
                continue
            lit = literal(n)
            if lit is not None:                      # a name with no literal stays local: walked, not trusted
                ask[lit] = n
        batch: list[str] = []
        size = 0
        lits = sorted(ask)
        for i, lit in enumerate(lits):
            batch.append(lit)
            size += len(lit) + 1
            if size >= NAME_CHUNK or i == len(lits) - 1:
                calls[0] += 1
                out = scratch / f"library-{calls[0]}.ndjson"
                run_export(prefix, lean_path, readable, cwd, modules, batch, out, sandboxed, timeout,
                           ignore_missing=True)
                found = set(read_export(out, with_exprs=False).decls)
                out.unlink()
                lib |= {ask[x] for x in batch if ask[x] in found}
                batch, size = [], 0
        return lib
    return classify
