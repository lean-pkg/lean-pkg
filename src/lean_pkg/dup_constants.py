# dup_constants -- constants that more than one module of an environment declares, judged fail-closed
"""Find every constant name that occurs in the constant table of MORE THAN ONE module of a compiled Lean
environment, and refuse any such duplicate that is not provably one and the same constant.

Why: `importModules` merges the modules' tables and tolerates a duplicate name when the two copies
"subsume" each other (Lean.Environment `subsumesInfo`: same type, universe parameters and, for a
theorem, the same `all` list). The proof terms are NOT compared, so two modules may carry two different
proofs of one theorem name, and which copy `env.find?` returns depends on the import order. Lean itself
does this routinely for realized auxiliaries (`f.congr_simp`, `f.eq_1`, `match_n.congr_eq_n`,
`_unary.induct_unfolding`, ...) that every module using them re-creates. A review that reads one copy may
not be reading the one the kernel checked against.

How: `scripts/DupConstants.lean` imports the given modules exactly as `import` does (data only:
`loadExts := false`, no initializer runs), walks `env.header.moduleData` (the tables `importModules`
merged) and prints, per duplicate name, every owner's `ConstantInfo` compared field by field with
`Expr.equal` (binder names and annotations included) against the `env.find?` winner, the module whose
copy IS the winner (pointer identity), and the module `env.getModuleIdxFor?` records (the first owner;
not necessarily the winner's). The toolchain's `lean --run` runs it with a LEAN_PATH built from the
project's lake-manifest.json (git packages under packagesDir, with their subDir; path packages at their
dir; nothing escaping the project or through a symlink; `lake env` is not used: a lakefile.lean is
code), inside the same bwrap
sandbox lean-exercise uses unless --no-sandbox.

Verdict per duplicate (fail closed):
- accepted: every owner's copy equals the winner exactly (kind, type, value/proof, levelParams, `all`,
  safety, reducibility hints, inductive/constructor/recursor shape), the winner's module and the recorded
  owner are both among the owners, every owner's .olean is attributed, and there are exactly two owners
  or the owner set is pinned with --allow-owners NAME=M1,M2,...;
- refused otherwise: `unequal` (with the differing fields per owner), `extra-owner` (a third owner, or an
  owner set other than the pinned one), `unknown-owner` (no winner, the winner is no owner's copy, the
  recorded owner is not an owner, or an .olean that cannot be attributed).
An incomplete probe (no final `done` line), a failed import (Lean's own "environment already contains")
or a missing .olean refuses the whole run. Nothing is whitelisted by name (generated and private names
are judged like any other), and counts are never added across owners or across scopes.

Scope: Lean's own library has duplicates of this kind (about a hundred in `Init`/`Std`/`Lean`, a few with
different proofs). `--scope project` (the default) judges the duplicates with at least one owner built by
the project itself; `packages` adds those with an owner in a Lake dependency; `all` judges the toolchain's
too. Duplicates outside the scope are still listed, with their verdicts, under `out_of_scope`; they do not
change the exit code.

Python API: `probe(project, modules, ...) -> (records, done)`, `judge(records, done, roots, scope=,
allow=) -> report`, `duplicate_constants(project, modules, ...) -> report`.
Command line: `python3 -m lean_pkg.dup_constants --project DIR MOD [MOD ...] [--scope S]
[--allow-owners NAME=M1,M2] [--no-sandbox] [--json]`. Exit 0 every in-scope duplicate accepted, 1 any
refused (or the probe failed), 2 usage. Documented in docs/duplicate-constants.md.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import subprocess
import sys

from . import build_scope
from . import comparator_receipt as cr
from . import exercise_forbid as fb

SCHEMA = "lean-pkg-dup-constants/1"
SCRIPT = pathlib.Path(__file__).with_name("scripts") / "DupConstants.lean"
PKG_LIB = pathlib.Path(".lake") / "build" / "lib" / "lean"
SCOPES = ("project", "packages", "all")
DEFAULT_TIMEOUT = 600.0


class ProbeError(Exception):
    """The environment could not be read: the run is refused, never passed."""


@dataclasses.dataclass
class Roots:
    """Where each origin's .olean files live, to attribute an owner module."""
    project: pathlib.Path                       # <project>/.lake/build/lib/lean
    packages: dict[str, pathlib.Path]           # name -> <packagesDir>/<name>/.lake/build/lib/lean
    toolchain: pathlib.Path | None              # <toolchain prefix>/lib/lean

    def origin(self, olean: str | None) -> str:
        if not olean:
            return "unknown"
        p = pathlib.Path(olean).resolve()
        if p.is_relative_to(self.project.resolve()):
            return "project"
        for name, d in self.packages.items():
            if p.is_relative_to(d.resolve()):
                return f"package:{name}"
        if self.toolchain is not None and p.is_relative_to(self.toolchain.resolve()):
            return "toolchain"
        return "unknown"

    def lean_path(self) -> list[pathlib.Path]:
        return [self.project] + list(self.packages.values())


# ---------------------------------------------------------------------------------------------------------
# The project: toolchain and LEAN_PATH, from lean-toolchain and lake-manifest.json (no lakefile is run)

def toolchain_prefix(toolchain: str) -> pathlib.Path:
    tc = toolchain if ":" in toolchain else f"leanprover/lean4:{toolchain}"
    return cr.elan_home() / "toolchains" / tc.replace("/", "--").replace(":", "---")


def _inside(base: pathlib.Path, rel: str, what: str) -> pathlib.Path:
    """base/rel, refused when rel is absolute or leaves base (build_scope.norm_dir, the same lexical rule the
    gate applies to lake-manifest.json path packages), or when any component on the way is a symlink or
    the result resolves outside base."""
    norm = build_scope.norm_dir(rel)
    if norm is None:
        raise ProbeError(f"{what}: {rel!r} is absolute or leaves {base}; refused")
    d = base
    for part in [x for x in norm.split("/") if x]:
        d = d / part
        if d.is_symlink():
            raise ProbeError(f"{what}: {rel!r} goes through the symlink {d}; refused")
    if not d.resolve().is_relative_to(base.resolve()):
        raise ProbeError(f"{what}: {rel!r} resolves outside {base}; refused")
    return d


def manifest_packages(project: pathlib.Path) -> dict[str, pathlib.Path]:
    """{name: its .lake/build/lib/lean} for every package of the project's lake-manifest.json, read as data
    (no lakefile is run): a `git` package at <packagesDir>/<name>[/<subDir>], a `path` package at its
    `dir`, relative to the manifest's directory. A dir or subDir that is absolute, leaves its base or goes
    through a symlink is refused, as is a package that is not checked out."""
    man = project / "lake-manifest.json"
    if not man.is_file():
        return {}
    try:
        m = json.loads(man.read_text(encoding="utf-8"))
        pkgs = m["packages"]
        if not isinstance(pkgs, list):
            raise TypeError("packages is not a list")
    except (ValueError, KeyError, TypeError, OSError) as e:
        raise ProbeError(f"{man}: not a Lake manifest ({e})") from None
    pdir = _inside(project, m.get("packagesDir") or ".lake/packages", "lake-manifest.json packagesDir")
    out: dict[str, pathlib.Path] = {}
    for x in pkgs:
        if not isinstance(x, dict) or not isinstance(x.get("name"), str) or not x["name"]:
            raise ProbeError(f"{man}: a package entry without a name")
        name, kind = x["name"], x.get("type")
        if name in out:
            raise ProbeError(f"{man}: package {name} is listed twice")
        if kind == "path":
            if not isinstance(x.get("dir"), str):
                raise ProbeError(f"{man}: path package {name} has no dir")
            d = _inside(project, x["dir"], f"path package {name} dir")
        elif kind == "git":
            d = _inside(pdir, name, f"git package {name}")
            if x.get("subDir") is not None:
                if not isinstance(x["subDir"], str):
                    raise ProbeError(f"{man}: git package {name} subDir is not a string")
                d = _inside(d, x["subDir"], f"git package {name} subDir")
        else:
            raise ProbeError(f"{man}: package {name} has type {kind!r} (only git and path are read)")
        if not d.is_dir():
            raise ProbeError(f"dependency {name} is not checked out ({d}); build the project first")
        out[name] = d / PKG_LIB
    return out


def project_roots(project: pathlib.Path, toolchain: str | None = None) -> tuple[Roots, pathlib.Path]:
    """(Roots, toolchain prefix) of a built Lake project."""
    project = project.resolve()
    if not project.is_dir():
        raise ProbeError(f"{project}: not a directory")
    if toolchain is None:
        f = project / "lean-toolchain"
        if not f.is_file():
            raise ProbeError(f"{project}: no lean-toolchain (pass --toolchain)")
        toolchain = f.read_text(encoding="utf-8").strip()
    prefix = toolchain_prefix(toolchain)
    if not os.access(prefix / "bin" / "lean", os.X_OK):
        raise ProbeError(f"toolchain {toolchain} is not installed (`elan toolchain install {toolchain}`)")
    packages = manifest_packages(project)
    return Roots(project / PKG_LIB, packages, prefix / "lib" / "lean"), prefix


# ---------------------------------------------------------------------------------------------------------
# The probe

def parse_probe(text: str) -> tuple[list[dict], dict | None]:
    """(duplicate records, the final `done` record or None) of the Lean script's output."""
    records, done = [], None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise ProbeError(f"probe output line {n} is not JSON: {line[:200]}") from None
        if not isinstance(obj, dict):
            raise ProbeError(f"probe output line {n} is not an object")
        if done is not None:
            raise ProbeError(f"probe output line {n} follows the final line")
        if obj.get("done") is True:
            done = obj
        elif isinstance(obj.get("name"), str) and isinstance(obj.get("owners"), list):
            records.append(obj)
        else:
            raise ProbeError(f"probe output line {n}: unknown record {sorted(obj)[:4]}")
    return records, done


def probe(project: pathlib.Path, modules: list[str], toolchain: str | None = None, sandboxed: bool = True,
          timeout: float = DEFAULT_TIMEOUT) -> tuple[list[dict], dict | None, Roots]:
    if not modules:
        raise ProbeError("no module given")
    for m in modules:
        if not m or m.startswith("-") or any(c.isspace() for c in m):
            raise ProbeError(f"not a module name: {m!r}")
    roots, prefix = project_roots(project, toolchain)
    for m in modules:
        rel = pathlib.Path(*m.split(".")).with_suffix(".olean")
        if not any((d / rel).is_file() for d in roots.lean_path()):
            raise ProbeError(f"module {m} is not built (no {rel} on the project's LEAN_PATH): "
                             f"ownership cannot be determined; run `lake build` first")
    env = {"PATH": f"{prefix / 'bin'}:/usr/bin:/bin", "LEAN_PATH": os.pathsep.join(map(str, roots.lean_path())),
           "LEAN_ABORT_ON_PANIC": "1"}
    cmd = [str(prefix / "bin" / "lean"), "--run", str(SCRIPT)] + modules
    cwd = roots.project.parents[3]
    if sandboxed:
        readable = [prefix, cwd, SCRIPT.parent] + [d for d in roots.packages.values() if d.is_dir()]
        cmd = fb.bwrap_args(readable, env, cwd) + cmd
        run_env: dict[str, str] = {}
    else:
        run_env = {**os.environ, **env}
    try:
        p = subprocess.run(cmd, cwd=cwd, env=run_env, stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=timeout, start_new_session=True)
    except subprocess.TimeoutExpired:
        raise ProbeError("the probe did not finish in time (--timeout)") from None
    except OSError as e:
        raise ProbeError(f"cannot run the probe: {e}") from None
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip() or out.strip()
        tail = err.splitlines()[-1:] or [f"exit {p.returncode}"]
        if "environment already contains" in err:
            raise ProbeError(f"Lean refused the import itself (an unequal duplicate it cannot merge): {tail[0][:400]}")
        raise ProbeError(f"the probe failed: {tail[0][:400]}")
    records, done = parse_probe(out)
    return records, done, roots


# ---------------------------------------------------------------------------------------------------------
# The verdict

def parse_allow(items: list[str]) -> dict[str, frozenset[str]]:
    allow: dict[str, frozenset[str]] = {}
    for it in items:
        name, sep, mods = it.partition("=")
        owners = frozenset(m.strip() for m in mods.split(",") if m.strip())
        if not sep or not name or len(owners) < 2:
            raise ValueError(f"--allow-owners {it!r}: expected NAME=MOD1,MOD2[,...] (two or more modules)")
        if name in allow:
            raise ValueError(f"--allow-owners: {name} given twice")
        allow[name] = owners
    return allow


def judge_one(rec: dict, roots: Roots, allow: dict[str, frozenset[str]]) -> dict:
    owners = []
    for o in rec["owners"]:
        diffs = o.get("vsWinner")
        owners.append({"module": o.get("module"), "origin": roots.origin(o.get("olean")),
                       "is_winner": bool(o.get("isWinner")), "kind": (o.get("constant") or {}).get("kind"),
                       "differences": diffs})
    mods = [o["module"] for o in owners]
    reasons: list[str] = []
    differences: dict[str, list[str]] = {}
    if rec.get("winner") is None:
        reasons.append("unknown-owner: env.find? does not return this name")
    elif rec.get("winnerModule") not in mods:
        reasons.append("unknown-owner: the env.find? winner is none of the owners' copies")
    if rec.get("recordedOwner") not in mods:
        reasons.append(f"unknown-owner: env.getModuleIdxFor? records {rec.get('recordedOwner')!r}, not an owner")
    for o in owners:
        if o["origin"] == "unknown":
            reasons.append(f"unknown-owner: the .olean of {o['module']} is outside the project, its packages "
                           f"and the toolchain")
        if not isinstance(o["differences"], list):
            reasons.append(f"unequal: {o['module']}'s copy could not be compared with the winner")
        elif o["differences"]:
            differences[o["module"]] = o["differences"]
    if differences:
        reasons.append("unequal: " + "; ".join(f"{m} differs from the winner in {', '.join(d)}"
                                               for m, d in differences.items()))
    if len(set(mods)) != len(mods):
        reasons.append("extra-owner: one module lists the name twice")
    pinned = allow.get(rec["name"])
    if pinned is not None:
        if frozenset(mods) != pinned:
            extra, missing = sorted(set(mods) - pinned), sorted(pinned - set(mods))
            reasons.append("extra-owner: owners differ from --allow-owners"
                           + (f"; not pinned: {', '.join(extra)}" if extra else "")
                           + (f"; pinned but absent: {', '.join(missing)}" if missing else ""))
    elif len(mods) > 2:
        reasons.append(f"extra-owner: {len(mods)} owners; more than two need --allow-owners "
                       f"{rec['name']}={','.join(mods)}")
    origins = sorted({o["origin"] for o in owners})
    return {"name": rec["name"], "verdict": "refused" if reasons else "accepted", "equal": not differences
            and all(isinstance(o["differences"], list) for o in owners), "owners": owners,
            "winner": rec.get("winnerModule"), "recorded_owner": rec.get("recordedOwner"),
            "origins": origins, "differences": differences, "reasons": reasons}


def in_scope(entry: dict, scope: str) -> bool:
    if scope == "all":
        return True
    origins = entry["origins"]
    if "unknown" in origins or "project" in origins:
        return True
    return scope == "packages" and any(o.startswith("package:") for o in origins)


def judge(records: list[dict], done: dict | None, roots: Roots, scope: str = "project",
          allow: dict[str, frozenset[str]] | None = None, modules: list[str] | None = None) -> dict:
    allow = allow or {}
    if scope not in SCOPES:
        raise ValueError(f"scope {scope!r}: one of {', '.join(SCOPES)}")
    res: dict = {"schema": SCHEMA, "modules": modules or [], "scope": scope, "ok": False}
    if done is None:
        res.update(verdict="refused", detail="the probe output is incomplete (no final line): nothing is judged",
                   duplicates=[], out_of_scope=[], counts={})
        return res
    judged = [judge_one(r, roots, allow) for r in records]
    mine = [e for e in judged if in_scope(e, scope)]
    other = [e for e in judged if not in_scope(e, scope)]
    stale = sorted(set(allow) - {e["name"] for e in judged})
    counts = {"in_scope": {"accepted": sum(e["verdict"] == "accepted" for e in mine),
                           "refused": sum(e["verdict"] == "refused" for e in mine)},
              "out_of_scope": {"accepted": sum(e["verdict"] == "accepted" for e in other),
                               "refused": sum(e["verdict"] == "refused" for e in other)},
              "environment": {"modules": done.get("modules"), "constants": done.get("constants")}}
    if done.get("duplicates") != len(records):
        detail = (f"the probe reported {done.get('duplicates')} duplicates but printed {len(records)}: "
                  f"the output is inconsistent")
        res.update(verdict="refused", detail=detail, duplicates=mine, out_of_scope=other, counts=counts)
        return res
    refused = [e["name"] for e in mine if e["verdict"] == "refused"]
    if stale:
        detail = f"--allow-owners names no duplicate: {', '.join(stale)} (a pin that matches nothing is refused)"
        res.update(verdict="refused", detail=detail)
    elif refused:
        res.update(verdict="refused", detail=f"{len(refused)} in-scope duplicate(s) refused: "
                   + ", ".join(refused[:10]) + (f" (and {len(refused) - 10} more)" if len(refused) > 10 else ""))
    else:
        res.update(ok=True, verdict="accepted",
                   detail=f"{len(mine)} in-scope duplicate(s), each exactly equal across its owners"
                   if mine else "no in-scope duplicate")
    res.update(duplicates=mine, out_of_scope=other, counts=counts)
    return res


def duplicate_constants(project, modules: list[str], toolchain: str | None = None, sandboxed: bool = True,
                        scope: str = "project", allow: dict[str, frozenset[str]] | None = None,
                        timeout: float = DEFAULT_TIMEOUT) -> dict:
    """The full report for `modules` of the built project `project`; a probe failure is a refusal."""
    try:
        records, done, roots = probe(pathlib.Path(project), modules, toolchain, sandboxed, timeout)
    except ProbeError as e:
        return {"schema": SCHEMA, "modules": modules, "scope": scope, "ok": False, "verdict": "refused",
                "detail": str(e), "duplicates": [], "out_of_scope": [], "counts": {}}
    return judge(records, done, roots, scope, allow, modules)


# ---------------------------------------------------------------------------------------------------------
# command line

def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python3 -m lean_pkg.dup_constants", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Report every constant that more than one module of a built project's environment "
                    "declares, with its owners and the env.find? winner, and refuse any duplicate that is "
                    "not exactly equal (type, proof/value, universe parameters, `all`, safety, kind).",
        epilog="examples:\n"
               "  python3 -m lean_pkg.dup_constants --project . MyLib\n"
               "  python3 -m lean_pkg.dup_constants --project . MyLib --allow-owners 'f.congr_simp=A,B,C'\n"
               "  python3 -m lean_pkg.dup_constants --project . MyLib --scope all --json\n"
               "exit codes: 0 every in-scope duplicate accepted, 1 any refused or the probe failed, 2 usage.\n"
               "docs: https://lean-pkg.github.io/lean-pkg/duplicate-constants.html")
    ap.add_argument("modules", nargs="+", metavar="MOD", help="the modules to import (e.g. the library root)")
    ap.add_argument("--project", required=True, metavar="DIR", help="the built Lake project")
    ap.add_argument("--toolchain", metavar="TC", help="default: the project's lean-toolchain")
    ap.add_argument("--scope", choices=SCOPES, default="project",
                    help="which duplicates are judged: those with an owner in the project (default), also in "
                         "its Lake packages, or all (the toolchain's included); the rest are only listed")
    ap.add_argument("--allow-owners", action="append", default=[], metavar="NAME=M1,M2",
                    help="pin the exact owner set of one name (needed for three or more owners); repeatable")
    ap.add_argument("--no-sandbox", action="store_true", help="read the .olean files without the bwrap sandbox")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS")
    ap.add_argument("--json", dest="json_out", action="store_true", help=f"JSON on stdout (schema {SCHEMA})")
    return ap


def _line(e: dict) -> str:
    owners = ", ".join(f"{o['module']}{' (winner)' if o['is_winner'] else ''}" for o in e["owners"])
    return f"  {e['verdict']}: {e['name']} [{owners}]" + "".join(f"\n      {r}" for r in e["reasons"])


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        allow = parse_allow(args.allow_owners)
    except ValueError as e:
        print(f"dup_constants: {e}", file=sys.stderr)
        return 2
    res = duplicate_constants(args.project, args.modules, args.toolchain, not args.no_sandbox, args.scope,
                              allow, args.timeout)
    if args.json_out:
        print(json.dumps(res, indent=2))
    else:
        print(f"{res['verdict']}: {res['detail']}")
        for e in res["duplicates"]:
            print(_line(e))
        o = res.get("counts", {}).get("out_of_scope")
        if o and (o["accepted"] or o["refused"]):
            print(f"out of scope (--scope {res['scope']}): {o['accepted']} accepted, {o['refused']} refused; "
                  f"--json lists them")
        print(f"{res['verdict']}: {res['detail']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
