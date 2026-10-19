# comparator_contracts -- `comparator check NAME --contracts-only`: the compare step of every configuration,
# no kernel, no receipt
"""A contracts-only diagnostic over every comparator configuration of a checkout at once (#280).

Why: Lake v4.35.0 has no compare-only mode. `lake comparator` exports the Challenge and the Solution,
compares them and, only when they match, runs the kernels; a statement mismatch (typically a private
helper or a generated `_proof_N` whose module-dependent name differs between a copied reference and
production, docs/comparator.md "Private helpers and copied references") is found only after a gate's
worth of work, one configuration at a time.

How: every distinct module the configurations name is exported ONCE with the toolchain's own
`leanexport` (the exporter `lake comparator` runs), over the union of the export targets of the
configurations that use it (Lake's `compareIt` list: the Quot constants when `Quot.sound` is permitted,
the theorem names, the permitted axioms, the kernel's primitive constants, the definition holes), with
--ignore-missing, in the bwrap sandbox `lean-exercise` uses (`exercise_forbid.run_export`) unless
--no-sandbox. A configuration whose targets are not all in both exports is an `error` (Lake's own
export of it would fail), never compared. Then
`scripts/ContractsOnly.lean` runs Lake's own public `Lake.Check.compareAt` and `Lake.Check.checkAxioms`
on each configuration's pair of exports, exactly as `verifyMatch.verifyCompare` does; their result is the
verdict, nothing here re-implements the comparison. A failing configuration also gets a diagnosis that
is display only: the breadcrumb (the chain of declarations from the target to the constant Lake refused,
from a walk that mirrors `Compare.loop`), the first differing ConstantInfo field (with Expr's BEq, as
Lake compares) and the first differing subterm as a short descriptor. A `hint` may name a private
module or a generated auxiliary; it is text for the reader and never changes a verdict.

Never acceptance: no kernel runs, no receipt, registry entry or event is written, and nothing in the
checkout is written. The exports live in a temporary directory that is removed afterwards. A match here is
not a pass, and a difference is a reason to fix the configuration before a gate, not the gate's
verdict; only `lake comparator` judges, inside a gate.

Python API: `run(repo, configs=None, exports=None, sandboxed=True) -> report` (schema
lean-pkg-comparator-contracts/1). Documented in docs/comparator.md, "A contracts-only diagnostic".
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile

from . import dup_constants as dc
from . import exercise_forbid as fb

SCHEMA = "lean-pkg-comparator-contracts/1"
SCRIPT = pathlib.Path(__file__).with_name("scripts") / "ContractsOnly.lean"
NEVER = ("never acceptance: no kernel ran and nothing was written (no receipt, no registry entry, no file in "
         "the checkout); only `lake comparator` in a gate judges")
DOCS = "docs/comparator.md#a-contracts-only-diagnostic"
PRIVATE_DOCS = "docs/comparator.md#private-helpers-and-copied-references"

# Lake v4.35.0 `Lake.CLI.Check.primitiveTargets` (also in scripts/ContractsOnly.lean; the test compares
# both with the toolchain's source when it is installed).
PRIMITIVE_TARGETS = (
    "Nat.add", "Nat.sub", "Nat.mul", "Nat.pow", "Nat.gcd", "Nat.div", "Nat.mod", "Nat.beq", "Nat.ble",
    "Nat.land", "Nat.lor", "Nat.xor", "Nat.shiftLeft", "Nat.shiftRight", "String.ofList", "Char.ofNat",
    "List", "eagerReduce", "Nat", "String", "String.mk", "Char", "optParam", "autoParam", "semiOutParam",
    "outParam")
QUOT_TARGETS = ("Quot", "Quot.mk", "Quot.lift", "Quot.ind")   # `builtinTargets`, when Quot.sound is permitted


class ContractsError(Exception):
    """The diagnostic could not run at all (no toolchain, no configuration, a helper failure)."""


def discover(repo: pathlib.Path) -> list[str]:
    """The configurations the runner compares: comparator*.json at the root, comparator/config*.json."""
    found = sorted(p for p in repo.glob("comparator*.json") if p.is_file()) + \
        sorted(p for p in (repo / "comparator").glob("config*.json") if p.is_file())
    return [p.relative_to(repo).as_posix() for p in found]


def load_slot(repo: pathlib.Path, rel: str) -> dict:
    """{config, challenge_module, solution_module, theorems, definitions, axioms} or {config, error}."""
    slot: dict = {"config": rel}
    try:
        cfg = json.loads((repo / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {**slot, "error": f"cannot read the configuration: {e}"}
    if not isinstance(cfg, dict):
        return {**slot, "error": "the configuration is not a JSON object"}

    def names(key: str, required: bool) -> list[str] | None:
        v = cfg.get(key)
        if v is None and not required:
            return []
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            return None
        return v

    ch, so = cfg.get("challenge_module"), cfg.get("solution_module")
    thms, defs, axs = names("theorem_names", True), names("definition_names", False), names("permitted_axioms", True)
    if not (isinstance(ch, str) and ch and isinstance(so, str) and so) or thms is None or defs is None or axs is None:
        return {**slot, "error": "malformed configuration (challenge_module, solution_module, theorem_names, "
                                 "permitted_axioms, definition_names as Lake reads them)"}
    if not thms and not defs:
        return {**slot, "error": "nothing to check: the configuration names no theorems or definitions"}
    for m in (ch, so):
        if m.startswith("-") or any(c.isspace() for c in m):
            return {**slot, "error": f"not a module name: {m!r}"}
    return {**slot, "challenge_module": ch, "solution_module": so, "theorems": thms, "definitions": defs,
            "axioms": axs}


def export_targets(slot: dict) -> list[str]:
    """Lake's `compareIt` export list for one configuration, in its order."""
    quot = list(QUOT_TARGETS) if "Quot.sound" in slot["axioms"] else []
    return list(dict.fromkeys(quot + slot["theorems"] + slot["axioms"] + list(PRIMITIVE_TARGETS)
                              + slot["definitions"]))


def parse_exports(items: list[str]) -> dict[str, pathlib.Path]:
    """--export MODULE=FILE (repeatable): an existing export to read for MODULE instead of exporting it."""
    out: dict[str, pathlib.Path] = {}
    for it in items:
        mod, sep, path = it.partition("=")
        if not sep or not mod or not path:
            raise ValueError(f"--export {it!r}: expected MODULE=FILE")
        if mod in out:
            raise ValueError(f"--export: {mod} given twice")
        p = pathlib.Path(os.path.expanduser(path)).resolve()
        if not p.is_file():
            raise ValueError(f"--export {it!r}: {p} is not a file")
        out[mod] = p
    return out


def _built(roots: dc.Roots, module: str) -> bool:
    rel = pathlib.Path(*module.split(".")).with_suffix(".olean")
    return any((d / rel).is_file() for d in roots.lean_path())


def private_module(desc: str) -> tuple[str, str] | None:
    """('A.B', 'rest') for the descriptor `const _private.A.B.0.rest`, else None (display only)."""
    name = desc.removeprefix("const ").split(".{", 1)[0]
    if not name.startswith("_private.") or ".0." not in name:
        return None
    mod, rest = name.removeprefix("_private.").split(".0.", 1)
    return mod, rest


def hint(entry: dict) -> str | None:
    """A reading aid for a failing configuration; never part of the verdict."""
    f = entry.get("first_field") or {}
    a, b = private_module(str(f.get("challenge", ""))), private_module(str(f.get("solution", "")))
    if a and b and a[1] == b[1] and a[0] != b[0]:
        return (f"the same private name {a[1]} is owned by module {a[0]} on the Challenge side and {b[0]} on the "
                f"Solution side: a private name embeds its module, so a copy under another module never matches "
                f"(see {PRIVATE_DOCS})")
    seen = [str(x) for x in entry.get("breadcrumb") or []] + [str(f.get("challenge", "")), str(f.get("solution", ""))]
    if any("_private." in x or "._proof_" in x for x in seen):
        return (f"a private or generated auxiliary (_private..., _proof_N) is involved; its name depends on the "
                f"module that declares it (see {PRIVATE_DOCS})")
    return None


def _run_helper(prefix: pathlib.Path, plan: pathlib.Path, readable: list[pathlib.Path], sandboxed: bool) -> str:
    cwd = plan.parent                 # the helper reads the plan and the exports, never the checkout
    env = {"PATH": f"{prefix / 'bin'}:/usr/bin:/bin", "LEAN_ABORT_ON_PANIC": "1"}
    cmd = [str(prefix / "bin" / "lean")]
    lake_lib = prefix / "lib" / "lean" / "libLake_shared.so"
    if lake_lib.is_file():            # Lake's compiled code instead of interpreting it (same functions)
        cmd.append(f"--load-dynlib={lake_lib}")
    cmd += ["--run", str(SCRIPT), str(plan)]
    if sandboxed:
        cmd = fb.bwrap_args([prefix, SCRIPT.parent, plan.parent] + readable, env, cwd) + cmd
        run_env: dict[str, str] = {}
    else:
        run_env = {**os.environ, **env}
    try:
        p = subprocess.run(cmd, cwd=cwd, env=run_env, stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as e:
        raise ContractsError(f"cannot run the compare helper: {e}") from None
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip() or p.stdout.decode("utf-8", "replace").strip()
        tail = [ln for ln in err.splitlines() if "error" in ln.lower()][:1] or err.splitlines()[-1:] \
            or [f"exit {p.returncode}"]
        raise ContractsError(f"the compare helper failed: {tail[0][:400]}")
    return p.stdout.decode("utf-8", "replace")


def parse_helper(text: str, expected: int) -> list[dict]:
    """The helper's slot lines; refused unless the final `done` line counts exactly `expected` slots."""
    lines, done = [], None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            raise ContractsError(f"helper output line {n} is not JSON: {line[:200]}") from None
        if not isinstance(obj, dict) or done is not None:
            raise ContractsError(f"helper output line {n} is not a slot record")
        if obj.get("done") is True:
            done = obj
        else:
            lines.append(obj)
    if done is None or done.get("slots") != expected or len(lines) != expected:
        raise ContractsError("the compare helper's output is incomplete (no final line, or a wrong count): "
                             "nothing is reported")
    return lines


def run(repo, configs: list[str] | None = None, exports: dict[str, pathlib.Path] | None = None,
        sandboxed: bool = True, toolchain: str | None = None) -> dict:
    """The report for the configurations `configs` (default: all) of the built checkout `repo`."""
    repo = pathlib.Path(repo).resolve()
    exports = exports or {}
    rels = configs if configs else discover(repo)
    if not rels:
        raise ContractsError(f"no comparator configuration in the checkout (comparator*.json or "
                             f"comparator/config*.json)")
    for rel in rels:
        p = (repo / rel).resolve()
        if not p.is_relative_to(repo) or not p.is_file():
            raise ContractsError(f"--config {rel}: not a file inside the checkout")
    try:
        roots, prefix = dc.project_roots(repo, toolchain)
    except dc.ProbeError as e:
        raise ContractsError(str(e)) from None
    if not os.access(prefix / "bin" / "leanexport", os.X_OK):
        raise ContractsError(f"the toolchain at {prefix.name} has no leanexport (Lean v4.35.0 or later ships it)")
    if sandboxed and not (os.environ.get("COMPARATOR_BWRAP") or shutil.which("bwrap")):
        raise ContractsError("bwrap is not installed: install it, or pass --no-sandbox to export unsandboxed")
    slots = [load_slot(repo, rel) for rel in rels]
    for s in slots:
        if "error" not in s:
            missing = [m for m in (s["challenge_module"], s["solution_module"])
                       if m not in exports and not _built(roots, m)]
            if missing:
                s["error"] = f"module {', '.join(missing)} is not built: run `lake build {' '.join(missing)}` first"
    live = [s for s in slots if "error" not in s]
    readable = [d for d in roots.packages.values() if d.is_dir()] + sorted({p.parent for p in exports.values()})
    export_log: list[dict] = []
    results: dict[str, dict] = {}
    work = pathlib.Path(tempfile.mkdtemp(prefix="lean-pkg-contracts-"))
    try:
        # one export per module, over the union of the targets of the configurations that use it
        users: dict[str, list[dict]] = {}
        for s in live:
            for m in dict.fromkeys((s["challenge_module"], s["solution_module"])):
                users.setdefault(m, []).append(s)
        files: dict[str, pathlib.Path] = {}
        for i, (m, ss) in enumerate(sorted(users.items())):
            cfgs = [s["config"] for s in ss]
            if m in exports:
                export_log.append({"module": m, "supplied": True, "configs": cfgs})
                files[m] = exports[m]
                continue
            # --ignore-missing: a name one configuration lacks must not fail the others; the helper reports
            # every configuration whose targets are not all in the export as an error, as Lake's export would
            names = list(dict.fromkeys(n for s in ss for n in export_targets(s)))
            out = work / f"{i}.export"
            try:
                fb.run_export(prefix, roots.lean_path(), readable, repo, [m], names, out, sandboxed, None,
                              ignore_missing=True)
                export_log.append({"module": m, "supplied": False, "configs": cfgs, "names": len(names)})
                files[m] = out
            except fb.ExportError as e:
                export_log.append({"module": m, "supplied": False, "configs": cfgs, "error": str(e)})
                for s in ss:
                    s["error"] = f"export of {m}: {e}"
        live = [s for s in live if "error" not in s]
        if live:
            plan = work / "plan.json"
            plan.write_text(json.dumps({"slots": [
                {"config": s["config"], "challenge": str(files[s["challenge_module"]]),
                 "solution": str(files[s["solution_module"]]), "targets": export_targets(s),
                 "theorems": s["theorems"],
                 "definitions": s["definitions"], "axioms": s["axioms"]} for s in live]}))
            for rec in parse_helper(_run_helper(prefix, plan, readable, sandboxed), len(live)):
                results[str(rec.get("config"))] = rec
    finally:
        shutil.rmtree(work, ignore_errors=True)

    entries = []
    for s in slots:
        e = {"config": s["config"], "challenge_module": s.get("challenge_module"),
             "solution_module": s.get("solution_module")}
        if "error" in s:
            e.update(verdict="error", detail=s["error"])
        else:
            r = results.get(s["config"])
            if r is None:
                e.update(verdict="error", detail="the compare helper reported nothing for this configuration")
            elif r.get("verdict") == "error":
                e.update(verdict="error", missing=r.get("missing") or [],
                         detail="not in the export (lake comparator's exporter would fail): "
                                + ", ".join(r.get("missing") or []))
            elif r.get("verdict") == "match":
                e.update(verdict="match", detail="Lake's compareAt and checkAxioms pass on the two exports")
            else:
                e.update(verdict="differs", detail=r.get("lake_message"), stage=r.get("stage"),
                         constant=r.get("constant"), reason=r.get("reason"), breadcrumb=r.get("breadcrumb") or [],
                         first_field=r.get("first_field"), consistent=bool(r.get("consistent")))
                e["hint"] = hint(e)
        entries.append(e)
    counts = {k: sum(e["verdict"] == k for e in entries) for k in ("match", "differs", "error")}
    ok = counts["match"] == len(entries)
    return {"schema": SCHEMA, "ok": ok, "acceptance": False, "evidence": False,
            "verdict": "match" if ok else ("differs" if counts["differs"] else "error"),
            "toolchain": prefix.name, "sandboxed": sandboxed, "counts": counts, "configs": entries,
            "exports": export_log, "note": NEVER, "docs": DOCS}


def summary_line(name: str, res: dict) -> str:
    c, n = res["counts"], len(res["configs"])
    word = "every configuration matches" if res["ok"] else \
        f"{c['differs']} differ, {c['error']} error(s), {c['match']} match"
    return f"{name}: contracts-only diagnostic, {n} configuration(s): {word} (never acceptance; no kernel, no receipt)"


def text_lines(name: str, res: dict) -> list[str]:
    out = [summary_line(name, res)]
    for e in res["configs"]:
        if e["verdict"] == "match":
            out.append(f"  match: {e['config']}")
            continue
        out.append(f"  {e['verdict'].upper()}: {e['config']}: {e['detail']}")
        if e["verdict"] != "differs":
            continue
        if e.get("breadcrumb"):
            out.append(f"    breadcrumb: {' -> '.join(e['breadcrumb'])}")
        f = e.get("first_field")
        if isinstance(f, dict):
            at = f" at {'.'.join(f['path'])}" if f.get("path") else ""
            sides = "; ".join(f"{k} {f[k]}" for k in ("challenge", "solution") if k in f)
            out.append(f"    first differing field: {f.get('field')}{at}" + (f": {sides}" if sides else ""))
        elif e.get("reason"):
            out.append(f"    reason: {e['reason']}")
        if not e.get("consistent"):
            out.append("    note: the diagnosis walk did not end at the constant Lake's message names; the "
                       "breadcrumb is a guess")
        if e.get("hint"):
            out.append(f"    hint: {e['hint']}")
    out.append(f"  {res['note']}")
    out.append(summary_line(name, res))
    return out
