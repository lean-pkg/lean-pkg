# lean-pkg comparator config --challenge-from-type -- pre-fill Challenge/Solution from elaborated types
"""Elaborated types for `comparator config --from-claims NAME --challenge-from-type` (#25).

Three modes share one code path:

- `--emit-script F` writes a self-contained driver (bash + python3) that, run from the package
  checkout at the bound commit (`cd <checkout> && bash F out.json`), elaborates each declaration
  with `lake env lean` and writes the JSON output contract below. Nothing runs here, so a
  workstation never elaborates a large package; the script can go to a Slurm job.
- `--from-output F` reads that JSON (schema and commit checked) and fills the scaffold.
- `--challenge-from-type` alone is emit to a temp dir, run, then ingest.

Output contract (schema comparator-types/1), written by the driver to its first argument:

    {"schema": "comparator-types/1", "package": NAME, "commit": "<40-hex HEAD>",
     "toolchain": "<lean-toolchain contents or null>",
     "results": [{"decl": D, "type": T|null, "status": "ok"|"error", "error": E|null,
                  "levels": [..], "modules": [..]}]}

`levels` (the declaration's universe parameters) and `modules` (modules of the constants its type
mentions) are additive; an ingested result without `modules` is marked `needs_restatement`.
Also additive: `summary` {ok, total, filled, errors, round_trip_failed, declaration_errors, jobs,
backoff} and per result `rc`, `failure` (declaration | resource | extraction | round_trip),
`output_tail`, `round_trip` ("ok" | "failed") and `round_trip_rc` (#30, #31). A result is `ok` only
when its printed type passed the round-trip check (ROUNDTRIP_SNIPPET). Also additive (field report
#5): per result `round_trip_exact` (true when the re-elaborated type is syntactically the
declaration's, `#guard_expr ... =ₛ ...`; false when it is only definitionally equal, with
`round_trip_exact_error`), and `summary.round_trip_inexact` / `summary.resumed`. Also additive
(LO-20261016-35): `round_trip_variant` / `round_trip_open` when the text that passed is the
fallback (printed with pp.coercions.types, or checked under `open scoped Classical`), and
`summary.round_trip_variant`.

The driver prints `[k/N] DECL ok|fail` progress lines to stderr and appends every finished result
to `OUT.json.checkpoint.jsonl`; a rerun with the same OUT.json (same package, commit and snippets)
reuses the settled results there and runs only the rest (LEAN_PKG_TYPES_RESUME=0 starts afresh). The
checkpoint is removed once a run ends with no extraction failure.

The driver writes only to a temp dir (its Lean scratch files), the output path and its checkpoint
beside it; it never writes into the checkout.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from . import cli, gate as gate_mod
from . import core
from . import statements_module as stm

SCHEMA = "comparator-types/1"
DEFAULT_JOBS = 2
DEFAULT_TIMEOUT = 120
# Module roots a Challenge may use: it imports Mathlib, which brings these in (one list, statements_module's).
VOCAB_ROOTS = stm.VOCAB_ROOTS
UNPARSEABLE = ("✝", "⋯")             # inaccessible-name dagger, elided-proof ellipsis
DECL_RE = re.compile(r"^[^\s«».]+(\.[^\s«».]+)*$")
LAKEFILES = ("lakefile.lean", "lakefile.toml")

# The Lean the driver writes for each declaration; it fills in __IMPORTS__ and __NAME__.
LEAN_SNIPPET = """\
__IMPORTS__
import Lean
set_option pp.fullNames true
set_option pp.proofs true
set_option pp.analyze true
set_option pp.numericTypes true
set_option pp.funBinderTypes true
open Lean Elab Command in
#eval show CommandElabM Unit from do
  let env ← getEnv
  let n : Name := __NAME__
  match env.find? n with
  | none => IO.println s!"LEANPKG-ERROR unknown constant {n}"
  | some ci =>
    let fmt ← liftTermElabM (Meta.ppExpr ci.type)
    let alt ← liftTermElabM (withOptions (·.setBool `pp.coercions.types true) (Meta.ppExpr ci.type))
    let mods := ci.type.getUsedConstants.toList.filterMap fun c =>
      (env.getModuleIdxFor? c).map fun i => (env.header.moduleNames[i.toNat]!).toString
    IO.println s!"LEANPKG-LEVELS {String.intercalate "," (ci.levelParams.map toString)}"
    IO.println s!"LEANPKG-MODULES {String.intercalate "," mods.eraseDups}"
    IO.println "LEANPKG-TYPE-BEGIN"
    IO.println (fmt.pretty 100)
    IO.println "LEANPKG-TYPE-END"
    IO.println "LEANPKG-ALT-BEGIN"
    IO.println (alt.pretty 100)
    IO.println "LEANPKG-ALT-END"
"""

# The round-trip check (#31): the printed type must elaborate, in the declaration's own module context
# with no auto-bound implicits, to a type the declaration itself inhabits (defeq). Exit status decides.
# The `#guard_expr` after it (field report #5) asks more: the re-elaborated type is syntactically the
# declaration's (`=ₛ`, Expr equality after instantiating metavariables). An error only at or after the
# LEANPKG-EXACT line means defeq but not syntactically equal (round_trip_exact false).
ROUNDTRIP_SNIPPET = """\
__IMPORTS__
set_option autoImplicit false
-- LEANPKG-ROUNDTRIP __DECL__
__UNIVERSES__
example :
    (__TYPE__) :=
  @_root_.__IDENT__
-- LEANPKG-EXACT
#guard_expr
    (__TYPE__) =ₛ type_of% @_root_.__IDENT____LEVELS__
"""

DRIVER_PY = r'''
import concurrent.futures, errno, hashlib, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time

CONFIG = json.loads(__CONFIG__)
DEFAULT_JOBS = CONFIG["default_jobs"]
SNIPPET = CONFIG["snippet"]
ROUNDTRIP = CONFIG["roundtrip_snippet"]
SIMPLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")
DIAG = re.compile(r"^.*?:\d+:\d+: (error|warning|info)(\([^)]*\))?: ?")
ERRLINE = re.compile(r"^.*?:(\d+):\d+: error")
CHECKPOINT = "comparator-types-checkpoint/1"
SETTLED = (None, "declaration", "round_trip")      # failures a rerun would only repeat
# File-table exhaustion (ENFILE 23 / EMFILE 24), common with many Lean processes on Lustre/NFS (#30).
RESOURCE = re.compile(r"too many open files|\bENFILE\b|\bEMFILE\b", re.I)
RESOURCE_ERRNOS = (errno.ENFILE, errno.EMFILE)
# A proposition Lean could not decide in the round trip: the declaration was stated under `open Classical`.
DECIDABLE = re.compile(r"failed to synthesize[^\n]*\n?[^\n]*\bDecidable\b")
CLASSICAL = "open scoped Classical\n"


def refuse(msg):
    sys.stderr.write("lean-pkg types: " + msg + "\n")
    sys.exit(2)


def lean_ident(dotted):
    return ".".join(p if SIMPLE.match(p) else "«" + p + "»" for p in dotted.split("."))


def lean_name(dotted):
    expr = "Name.anonymous"
    for p in dotted.split("."):
        expr = "(" + expr + ").str " + json.dumps(p, ensure_ascii=True)
    return "(" + expr + ")"


def elan_bin():
    home = os.environ.get("ELAN_HOME", "").strip()
    return pathlib.Path(home) if home else pathlib.Path.home() / ".elan"


def is_elan(tool):
    where = shutil.which(tool)
    if where is None:
        return None
    p, proxy = pathlib.Path(where), elan_bin() / "bin" / tool
    if p.parent.resolve() == proxy.parent.resolve():
        return True
    return proxy.exists() and p.resolve() == proxy.resolve()


def parse(out):
    """(type, levels, modules, error) from the driver snippet's stdout+stderr."""
    lines = [DIAG.sub("", l) if DIAG.match(l) and "LEANPKG-" in l else l for l in out.splitlines()]
    levels = modules = None
    body, inside, err = [], False, None
    for l in lines:
        s = l.strip()
        if inside:
            if s == "LEANPKG-TYPE-END":
                inside = False
                continue
            body.append(l.rstrip())
        elif s.startswith("LEANPKG-ERROR"):
            err = s[len("LEANPKG-ERROR"):].strip() or "unknown constant"
        elif s.startswith("LEANPKG-LEVELS"):
            levels = [x for x in s[len("LEANPKG-LEVELS"):].strip().split(",") if x]
        elif s.startswith("LEANPKG-MODULES"):
            modules = [x for x in s[len("LEANPKG-MODULES"):].strip().split(",") if x]
        elif s == "LEANPKG-TYPE-BEGIN":
            inside = True
    text = "\n".join(body).strip()
    if text and not inside and err is None:
        return text, levels or [], modules, None
    if err is None:
        for l in out.splitlines():
            m = DIAG.match(l)
            if m and m.group(1) == "error":
                err = l[m.end():].strip() or l.strip()
                break
    if err is None:
        first = [l.strip() for l in out.splitlines() if l.strip()]
        err = first[0] if first else "no output"
    return None, None, None, err


def tail(out, n=5, cap=800):
    """The last `n` non-blank lines of `out`, at most `cap` characters."""
    t = "\n".join([l.rstrip() for l in out.splitlines() if l.strip()][-n:])
    return t[-cap:]


def one(entry, work, lake_dir, timeout):
    """One declaration -> result. `failure` says why a result is not ok: `declaration` (Lean ran and
    reported the declaration missing, or the claim names no module: not retried, not an extraction
    error), `resource` (the file table was exhausted: retried with fewer workers, an extraction error
    if it persists) or `extraction` (Lean did not run to completion: exit status, timeout, OS error)."""
    decl, module = entry["decl"], entry.get("module")
    res = {"decl": decl, "type": None, "status": "error", "error": None, "levels": [], "modules": [],
           "rc": None, "failure": "extraction"}
    if not module:
        res.update(error="no Lean module for the claim's file " + str(entry.get("file")), failure="declaration")
        return res
    src = SNIPPET.replace("__IMPORTS__", "import " + lean_ident(module)).replace("__NAME__", lean_name(decl))
    f = pathlib.Path(work) / ("d%05d.lean" % entry["index"])
    try:
        f.write_text(src, encoding="utf-8")
        p = subprocess.run(["lake", "env", "lean", str(f)], cwd=lake_dir, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        res["error"] = "timed out after %ss" % timeout
        return res
    except OSError as e:
        res["error"] = "cannot run lake: %s" % e
        if e.errno in RESOURCE_ERRNOS:
            res["failure"] = "resource"
        return res
    out = p.stdout.decode("utf-8", "replace")
    res["rc"] = p.returncode
    typ, levels, modules, err = parse(out)
    if p.returncode != 0 or typ is None:
        res["error"] = err
        res["output_tail"] = tail(out)
        if p.returncode == 0 and any(l.strip().startswith("LEANPKG-ERROR") for l in out.splitlines()):
            res["failure"] = "declaration"
        elif p.returncode != 0 and typ is not None:
            res["error"] = "lake env lean exited %d after printing the type: %s" % (
                p.returncode, tail(out, 1) or "no output")
        if res["failure"] == "extraction" and RESOURCE.search(out):
            res["failure"] = "resource"
        return res
    res.update(type=typ, levels=levels)
    if modules is None:
        res.pop("modules")
    else:
        res["modules"] = modules
    alt = alt_text(out)
    if alt:
        res["alt_type"] = alt
    return round_trip(res, entry, module, work, lake_dir, timeout)


def alt_text(out):
    """The LEANPKG-ALT block (the type printed with pp.coercions.types as well), or None."""
    body, inside = [], False
    for l in out.splitlines():
        s = l.strip()
        if s == "LEANPKG-ALT-BEGIN":
            body, inside = [], True
        elif s == "LEANPKG-ALT-END" and inside:
            text = "\n".join(body).strip()
            return text or None
        elif inside:
            body.append(l.rstrip())
    return None


def rt_attempt(res, typ, entry, module, work, lake_dir, timeout, n, classical):
    """One round-trip run of the printed text `typ` -> (outcome, fields, output); outcome is exact,
    defeq, failed or abort (timed out, OS error, file table exhausted: not a verdict on the text)."""
    src = (ROUNDTRIP.replace("__IMPORTS__", "import " + lean_ident(module))
           .replace("__DECL__", res["decl"])
           .replace("__UNIVERSES__", ("universe " + " ".join(res["levels"])) if res["levels"] else "")
           .replace("__LEVELS__", (".{" + ", ".join(res["levels"]) + "}") if res["levels"] else "")
           .replace("__IDENT__", lean_ident(res["decl"]))
           .replace("__TYPE__", typ.replace("\n", "\n      ")))
    if classical:
        src = src.replace("set_option autoImplicit false\n", "set_option autoImplicit false\n" + CLASSICAL)
    marks = [i for i, l in enumerate(src.splitlines(), 1) if l.strip() == "-- LEANPKG-EXACT"]
    exact_from = marks[0] if marks else None
    f = pathlib.Path(work) / ("r%05d%s.lean" % (entry["index"], "" if n == 0 else "-%d" % n))
    try:
        f.write_text(src, encoding="utf-8")
        p = subprocess.run(["lake", "env", "lean", str(f)], cwd=lake_dir, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "abort", {"error": "round-trip check timed out after %ss" % timeout}, ""
    except OSError as e:
        fields = {"error": "cannot run lake for the round-trip check: %s" % e}
        if e.errno in RESOURCE_ERRNOS:
            fields["failure"] = "resource"
        return "abort", fields, ""
    out = p.stdout.decode("utf-8", "replace")
    if p.returncode == 0:
        fields = {"round_trip_rc": 0, "status": "ok", "failure": None, "round_trip": "ok"}
        if exact_from is not None:
            fields["round_trip_exact"] = True
        return "exact", fields, out
    errs = [int(m.group(1)) for m in (ERRLINE.match(l) for l in out.splitlines()) if m]
    if exact_from is not None and errs and min(errs) > exact_from and not RESOURCE.search(out):
        # The `example` elaborated (defeq), only the syntactic `#guard_expr` failed.
        lines = [l.rstrip() for l in out.splitlines() if l.strip()]
        first = next(i for i, l in enumerate(lines) if ERRLINE.match(l))
        msg = " | ".join([DIAG.sub("", lines[first])] + lines[first + 1:first + 4])
        return "defeq", {"round_trip_rc": p.returncode, "status": "ok", "failure": None, "round_trip": "ok",
                         "round_trip_exact": False, "round_trip_exact_error": msg[:800]}, out
    if RESOURCE.search(out):
        return "abort", {"round_trip_rc": p.returncode, "output_tail": tail(out),
                         "error": "round-trip check: " + tail(out, 1), "failure": "resource"}, out
    lines = [l.rstrip() for l in out.splitlines() if l.strip()]
    first = next((i for i, l in enumerate(lines) if DIAG.match(l) and DIAG.match(l).group(1) == "error"), 0)
    msg = [DIAG.sub("", lines[first])] + lines[first + 1:first + 8] if lines else ["no output"]
    return "failed", {"round_trip_rc": p.returncode, "output_tail": tail(out), "round_trip": "failed",
                      "failure": "round_trip",
                      "error": ("round-trip check failed (exit %d): %s" % (p.returncode, " | ".join(msg)))[:800]}, out


RANK = {"exact": 2, "defeq": 1, "failed": 0}


def round_trip(res, entry, module, work, lake_dir, timeout):
    """Re-elaborate the printed type against the declaration (#31); only a pass makes the result ok.
    When the first text is not syntactically exact, the fallbacks run in order (field report #5): the
    text printed with pp.coercions.types, then, if Lean could not decide a proposition, both texts again
    under `open scoped Classical`. The first exact attempt wins, else the best (defeq over failed, an
    earlier one on a tie); `type` is the text that passed and `round_trip_variant` names what was added."""
    primary = res["type"]
    alt = res.pop("alt_type", None)
    tries = [(primary, False)] + ([(alt, False)] if alt and alt != primary else [])
    best, i, classical_added = None, 0, False
    while i < len(tries):
        typ, classical = tries[i]
        outcome, fields, out = rt_attempt(res, typ, entry, module, work, lake_dir, timeout, i, classical)
        if outcome == "abort":
            if best is None or best[0] == "failed":     # not a verdict: report it, so a rerun repeats it
                res.update(fields)
                return res
            break
        if best is None or RANK[outcome] > RANK[best[0]]:
            best = (outcome, fields, typ, classical)
        if outcome == "exact":
            break
        if not classical_added and outcome == "failed" and DECIDABLE.search(out):
            classical_added = True
            tries += [(t, True) for t, _ in tries if (t, True) not in tries]
        i += 1
    outcome, fields, typ, classical = best
    res.update(fields)
    variant = [v for v, on in (("pp.coercions.types", typ != primary), ("open scoped Classical", classical)) if on]
    if variant and outcome != "failed":
        res["type"] = typ
        res["round_trip_variant"] = "; ".join(variant)
        if classical:
            res["round_trip_open"] = "Classical"
    return res


def safe_one(entry, work, lake_dir, timeout):
    try:
        return one(entry, work, lake_dir, timeout)
    except Exception as e:      # never lose the run to one declaration
        return {"decl": entry["decl"], "type": None, "status": "error", "levels": [], "modules": [], "rc": None,
                "error": "driver error: %s: %s" % (type(e).__name__, e),
                "failure": "resource" if getattr(e, "errno", None) in RESOURCE_ERRNOS else "extraction"}


def verdict(res):
    if res.get("status") == "ok":
        v = res.get("round_trip_variant")
        return ("ok" if res.get("round_trip_exact") is not False else "ok (defeq, not syntactically equal)") + \
            (" (with %s)" % v if v else "")
    return "fail (%s)" % (res.get("failure") or "error")


def run_pool(entries, workers, work, lake_dir, timeout, done=None):
    """Results in `entries` order; `done(entry, result)` runs in this thread as each one finishes."""
    out = [None] * len(entries)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(safe_one, e, work, lake_dir, timeout): i for i, e in enumerate(entries)}
        for f in concurrent.futures.as_completed(futs):
            i = futs[f]
            out[i] = f.result()
            if done is not None:
                done(entries[i], out[i])
    return out


class Checkpoint:
    """OUT.json.checkpoint.jsonl: a header line naming what the results belong to, then one line per
    finished result (a later line for the same index replaces an earlier one)."""

    def __init__(self, path, key):
        self.path, self.key, self.fh = path, key, None

    def load(self, entries):
        """{index: result} of the settled results a matching checkpoint holds."""
        if os.environ.get("LEAN_PKG_TYPES_RESUME", "1").strip() == "0" or not self.path.is_file():
            return {}
        got = {}
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            head = json.loads(lines[0]) if lines else {}
            if head.get("checkpoint") != CHECKPOINT or head.get("key") != self.key:
                sys.stderr.write("lean-pkg types: %s is for another run (package, commit or driver); "
                                 "starting afresh\n" % self.path)
                return {}
            for l in lines[1:]:
                try:
                    rec = json.loads(l)
                except ValueError:            # a line cut short by a killed run
                    continue
                i = rec.get("index")
                if isinstance(i, int) and 0 <= i < len(entries) and rec.get("decl") == entries[i]["decl"] \
                        and isinstance(rec.get("result"), dict):
                    got[i] = rec["result"]
        except (OSError, ValueError, IndexError) as e:
            sys.stderr.write("lean-pkg types: cannot read %s (%s); starting afresh\n" % (self.path, e))
            return {}
        return {i: r for i, r in got.items() if r.get("failure") in SETTLED}

    def open(self, kept):
        """Rewrite the file with the header and the results kept from it, then append from here on."""
        tmp = self.path.with_name(self.path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"checkpoint": CHECKPOINT, "key": self.key}) + "\n")
            for i in sorted(kept):
                fh.write(json.dumps({"index": i, "decl": kept[i]["decl"], "result": kept[i]},
                                    ensure_ascii=False) + "\n")
        tmp.replace(self.path)
        self.fh = open(self.path, "a", encoding="utf-8")

    def add(self, entry, res):
        self.fh.write(json.dumps({"index": entry["index"], "decl": entry["decl"], "result": res},
                                 ensure_ascii=False) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())

    def close(self, remove):
        if self.fh is not None:
            self.fh.close()
        if remove:
            try:
                self.path.unlink()
            except OSError:
                pass


def run_all(entries, jobs, work, lake_dir, timeout, pause, ckpt=None):
    """Run every entry with `jobs` workers; while some fail on file-table exhaustion, halve the workers
    and retry only those (at least two retry rounds, the last ones with one worker). Entries a matching
    checkpoint already settled are not run again. -> (results, backoff rounds, resumed count)."""
    kept = ckpt.load(entries) if ckpt is not None else {}
    if ckpt is not None:
        ckpt.open(kept)
    total = len(entries)
    if kept:
        sys.stderr.write("lean-pkg types: resumed %d of %d result(s) from %s\n" % (len(kept), total, ckpt.path))
    state = {"n": len(kept), "tag": ""}

    def done(entry, res):
        state["n"] += 1
        sys.stderr.write("lean-pkg types: [%s%d/%d] %s %s\n" % (state["tag"], state["n"], state["total"],
                                                                 entry["decl"], verdict(res)))
        sys.stderr.flush()
        if ckpt is not None:
            ckpt.add(entry, res)

    todo = [e for e in entries if e["index"] not in kept]
    state["total"] = total
    ran = run_pool(todo, jobs, work, lake_dir, timeout, done)
    results = [kept.get(i) for i in range(total)]
    for e, r in zip(todo, ran):
        results[e["index"]] = r
    backoff, workers, rnd = [], jobs, 0
    while True:
        idx = [i for i, r in enumerate(results) if r.get("failure") == "resource"]
        if not idx or (rnd >= 2 and workers == 1):
            break
        rnd += 1
        workers = max(1, workers // 2)
        sys.stderr.write("lean-pkg types: %d declaration(s) hit 'too many open files'; retry %d with %d "
                         "worker(s)\n" % (len(idx), rnd, workers))
        time.sleep(pause * rnd)
        state.update(n=0, total=len(idx), tag="retry %d: " % rnd)
        redo = run_pool([entries[i] for i in idx], workers, work, lake_dir, timeout, done)
        for i, r in zip(idx, redo):
            results[i] = r
        backoff.append({"round": rnd, "jobs": workers, "retried": len(idx),
                        "recovered": sum(1 for r in redo if r.get("failure") != "resource")})
    return results, backoff, len(kept)


def default_jobs():
    """min(DEFAULT_JOBS, the CPUs this process may use: a Slurm allocation, not the whole node)."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = os.cpu_count() or 1
    return max(1, min(DEFAULT_JOBS, cpus))


def summarize(results, jobs, backoff=(), resumed=0):
    filled = sum(1 for r in results if r["status"] == "ok")
    rt = sum(1 for r in results if r["status"] != "ok" and r.get("failure") == "round_trip")
    errors = sum(1 for r in results if r["status"] != "ok" and r.get("failure") not in ("declaration", "round_trip"))
    inexact = sum(1 for r in results if r["status"] == "ok" and r.get("round_trip_exact") is False)
    variant = sum(1 for r in results if r["status"] == "ok" and r.get("round_trip_variant"))
    return {"ok": errors == 0, "total": len(results), "filled": filled, "errors": errors,
            "round_trip_failed": rt, "declaration_errors": len(results) - filled - errors - rt, "jobs": jobs,
            "backoff": list(backoff), "round_trip_inexact": inexact, "resumed": resumed,
            "round_trip_variant": variant}


def checkpoint_key():
    """What a checkpoint's results depend on: the package, commit, Lake dir and the two Lean snippets."""
    basis = {k: CONFIG.get(k) for k in ("schema", "package", "commit", "lake_dir", "snippet", "roundtrip_snippet")}
    return hashlib.sha256(json.dumps(basis, sort_keys=True).encode("utf-8")).hexdigest()


def main():
    if len(sys.argv) < 2 or not sys.argv[1]:
        refuse("usage: cd <checkout> && bash DRIVER OUT.json")
    out = pathlib.Path(sys.argv[1]).absolute()
    lake_dir = pathlib.Path(CONFIG["lake_dir"]).absolute()
    if not any((lake_dir / n).is_file() for n in ("lakefile.lean", "lakefile.toml")):
        refuse("no lakefile.lean or lakefile.toml in %s; run from the package checkout" % lake_dir)
    for tool, need in (("lake", True), ("lean", False)):
        ok = is_elan(tool)
        if ok is None and need:
            refuse("%s is not on PATH (install elan)" % tool)
        if ok is False:
            refuse("%s on PATH (%s) is not elan's (%s); a %s outside elan ignores lean-toolchain"
                   % (tool, shutil.which(tool), elan_bin() / "bin" / tool, tool))
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              stdin=subprocess.DEVNULL).stdout.decode().strip()
    except OSError:
        head = ""
    if head != CONFIG["commit"]:
        refuse("checkout HEAD is %s, this driver is for %s; check out that commit first"
               % (head or "(not a git checkout)", CONFIG["commit"]))
    tc = lake_dir / "lean-toolchain"
    toolchain = tc.read_text(encoding="utf-8").strip() if tc.is_file() else None
    try:
        jobs = max(1, int(os.environ.get("LEAN_PKG_TYPES_JOBS") or CONFIG["jobs"] or default_jobs()))
        timeout = int(os.environ.get("LEAN_PKG_TYPES_TIMEOUT") or CONFIG["timeout"])
        pause = float(os.environ.get("LEAN_PKG_TYPES_BACKOFF_SECONDS") or 5)
    except ValueError as e:
        refuse("LEAN_PKG_TYPES_JOBS / _TIMEOUT / _BACKOFF_SECONDS: %s" % e)
    work = tempfile.mkdtemp(prefix="lean-pkg-types-")
    ckpt = Checkpoint(out.with_name(out.name + ".checkpoint.jsonl"), checkpoint_key())
    try:
        entries = [dict(e, index=i) for i, e in enumerate(CONFIG["decls"])]
        results, backoff, resumed = run_all(entries, jobs, work, lake_dir, timeout, pause, ckpt)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if ckpt.fh is not None:
            ckpt.fh.close()
            ckpt.fh = None
    summary = summarize(results, jobs, backoff, resumed)
    doc = {"schema": CONFIG["schema"], "package": CONFIG["package"], "commit": head, "toolchain": toolchain,
           "summary": summary, "results": results}
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(out)
    ckpt.close(remove=summary["errors"] == 0)
    sys.stderr.write("lean-pkg types: %d of %d filled (%d only defeq, not syntactically equal), %d extraction "
                     "error(s), %d round-trip failure(s), %d declaration error(s) -> %s\n"
                     % (summary["filled"], summary["total"], summary["round_trip_inexact"], summary["errors"],
                        summary["round_trip_failed"], summary["declaration_errors"], out))
    if summary["errors"]:
        sys.stderr.write("lean-pkg types: kept %s; a rerun with the same OUT.json runs only the unsettled "
                         "declarations\n" % ckpt.path)
    if summary["errors"]:
        sys.stderr.write("lean-pkg types: extraction failed for %d declaration(s) (see results[].rc, error, "
                         "output_tail); `--from-output` refuses this file unless --allow-partial\n" % summary["errors"])
        sys.exit(2 if summary["filled"] == 0 else 1)


main()
'''


LEAN_THREADS_LINES = (
    "# Shared cluster nodes (#34): the file table is per node, not per job, so several jobs' Lean\n"
    "# processes can exhaust it together. One Lean thread per process unless the caller set\n"
    "# LEAN_NUM_THREADS (an explicit value is kept); keep LEAN_PKG_TYPES_JOBS <= 2 as well.\n"
    'export LEAN_NUM_THREADS="${LEAN_NUM_THREADS:-1}"\n')


def driver_script(config: dict, *, lean_threads: bool = True) -> str:
    """The self-contained driver: bash that hands an embedded python3 program its argument.
    lean_threads (the --emit-script driver, run on a cluster node): default LEAN_NUM_THREADS to 1, keeping
    an explicit value; the one-shot local run passes False and inherits the environment unchanged."""
    cfg = json.dumps(json.dumps(config, ensure_ascii=True))
    py = DRIVER_PY.replace("__CONFIG__", cfg)
    names = ", ".join(d["decl"] for d in config["decls"][:3]) + (" ..." if len(config["decls"]) > 3 else "")
    return ("#!/usr/bin/env bash\n"
            f"# Generated by `lean-pkg comparator config --from-claims {config['package']} --emit-script`.\n"
            f"# Elaborates {len(config['decls'])} declaration(s) ({names}) of {config['package']} at commit\n"
            f"# {config['commit']} with `lake env lean` and writes the {SCHEMA} JSON to OUT.json.\n"
            "# Usage: cd <package checkout> && bash THIS_FILE OUT.json\n"
            "# Needs python3, git and elan's lake; build the package first (`lake build`). Writes only a\n"
            "# temp dir and OUT.json. LEAN_PKG_TYPES_JOBS / LEAN_PKG_TYPES_TIMEOUT override the defaults;\n"
            "# on 'too many open files' it halves the workers and retries. Exit 0 ok, 1 some extraction\n"
            "# failed, 2 nothing filled (or refused). Progress: `[k/N] DECL ok|fail` on stderr; finished\n"
            "# results go to OUT.json.checkpoint.jsonl, and a rerun with the same OUT.json resumes from it\n"
            "# (LEAN_PKG_TYPES_RESUME=0 starts afresh).\n"
            "set -euo pipefail\n"
            + (LEAN_THREADS_LINES if lean_threads else "") +
            'if [ "$#" -ne 1 ]; then echo "usage: bash $0 OUT.json" >&2; exit 2; fi\n'
            "exec python3 - \"$1\" <<'LEAN_PKG_DRIVER'\n"
            f"{py}"
            "LEAN_PKG_DRIVER\n")


def lake_dir_rel(repo: pathlib.Path, lake_env: str | None) -> str:
    rel = gate_mod._safe_rel(lake_env, "--lake-env") if lake_env else "."
    d = repo / rel
    if not any((d / n).is_file() for n in LAKEFILES):
        raise core.Fail(f"no lakefile.lean or lakefile.toml in {d}; `--challenge-from-type` needs a Lake "
                        f"package (--lake-env DIR names a sub-directory of the checkout)")
    return rel


def module_of(file: object, lake_rel: str) -> str | None:
    """`Demo/Main.lean` -> `Demo.Main` (relative to the Lake directory)."""
    if not isinstance(file, str) or not file.endswith(".lean"):
        return None
    pp = pathlib.PurePosixPath(file)
    if pp.is_absolute() or ".." in pp.parts:
        return None
    if lake_rel not in (".", ""):
        try:
            pp = pp.relative_to(pathlib.PurePosixPath(lake_rel))
        except ValueError:
            return None
    parts = list(pp.with_suffix("").parts)
    return ".".join(parts) if parts and all(parts) else None


def driver_config(name: str, head: str, lake_rel: str, claims: list[dict], jobs: int | None,
                  timeout: int) -> dict:
    decls = []
    for c in claims:
        decl = str(c.get("decl") or "")
        decls.append({"decl": decl, "claim": str(c.get("id") or ""), "file": c.get("file"),
                      "module": module_of(c.get("file"), lake_rel) if DECL_RE.match(decl) else None})
    return {"schema": SCHEMA, "package": name, "commit": head, "lake_dir": lake_rel, "jobs": jobs,
            "default_jobs": DEFAULT_JOBS, "timeout": timeout, "snippet": LEAN_SNIPPET,
            "roundtrip_snippet": ROUNDTRIP_SNIPPET, "decls": decls}


def check_jobs_timeout(jobs: int | None, timeout: int | None) -> tuple[int | None, int]:
    """(jobs, timeout). jobs None: the driver picks min(DEFAULT_JOBS, CPUs) on the host it runs on (#30)."""
    j = jobs
    t = DEFAULT_TIMEOUT if timeout is None else timeout
    if j is not None and j < 1:
        raise core.UsageError(f"--jobs {j}: must be at least 1")
    if t < 1:
        raise core.UsageError(f"--timeout {t}: must be at least 1 second")
    return j, t


def refuse_non_elan() -> None:
    for tool, need in (("lake", True), ("lean", False)):
        where = shutil.which(tool)
        if where is None:
            if need:
                raise core.Fail(f"{tool} is not on PATH; `--challenge-from-type` runs `lake env lean` "
                                f"(install elan, or use --emit-script on a host that has it)")
            continue
        if not cli.is_elan_proxy(where, tool):
            raise core.Fail(f"{tool} on PATH ({where}) is not elan's ({cli.elan_bin() / tool}); a {tool} "
                            f"outside elan ignores the package's lean-toolchain")


def emit_path(repo: pathlib.Path, f: str, flag: str = "--emit-script") -> pathlib.Path:
    """A file Lean-pkg writes beside the checkout (the driver, a --no-registry config): outside the
    checkout, or under its .lake/ build dir."""
    p = pathlib.Path(f).expanduser()
    p = p if p.is_absolute() else pathlib.Path.cwd() / p
    p = pathlib.Path(os.path.abspath(p))
    root = pathlib.Path(os.path.abspath(repo))
    try:
        rel = p.relative_to(root)
    except ValueError:
        return p
    if rel.parts[:1] != (".lake",) or len(rel.parts) < 2:
        raise core.Fail(f"{flag} {f}: inside the checkout; write it outside, or under "
                        f"{root / '.lake'}/ (the build dir), so the checkout stays clean")
    return p


def run_local(repo: pathlib.Path, config: dict) -> dict:
    """Emit the driver into a temp dir, run it from the checkout, and return the parsed output."""
    refuse_non_elan()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="lean-pkg-types-"))
    try:
        script, out = tmp / "driver.sh", tmp / "types.json"
        script.write_text(driver_script(config, lean_threads=False), encoding="utf-8")
        p = subprocess.run(["bash", str(script), str(out)], cwd=repo, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, env=core.scrubbed_env())   # #27
        # Exit 1/2 with an output file: some extractions failed; check_partial decides (#30).
        if p.returncode not in (0, 1, 2) or not out.is_file():
            tail = (p.stderr or p.stdout or "").strip().splitlines()
            raise core.Fail(f"type driver failed (exit {p.returncode}): {tail[-1] if tail else 'no output'}")
        return read_output(out, config["package"], config["commit"], str(out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_output(path: pathlib.Path, name: str, head: str, shown: str) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise core.Fail(f"--from-output {shown}: cannot read JSON ({e}); nothing written") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        got = doc.get("schema") if isinstance(doc, dict) else None
        raise core.Fail(f"--from-output {shown}: schema {got!r}, expected {SCHEMA!r}; nothing written")
    if doc.get("package") != name:
        raise core.Fail(f"--from-output {shown}: package {doc.get('package')!r}, not {name!r}; nothing written")
    if doc.get("commit") != head:
        raise core.Fail(f"--from-output {shown}: elaborated at commit {str(doc.get('commit'))[:12]}, but HEAD is "
                        f"{head[:12]}; re-run the driver at HEAD; nothing written")
    results = doc.get("results")
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        raise core.Fail(f"--from-output {shown}: results must be a list of objects; nothing written")
    return doc


DECLARATION_ERRORS = ("unknown constant", "no Lean module for the claim's file")


def failure_of(result: dict | None) -> str | None:
    """Why a result is not usable: None (ok), `declaration` (a per-declaration error Lean reported) or
    `extraction` (Lean did not run to completion; also a missing result). Output written before #30
    has no `failure`; there an `rc` other than 0, or an error that is not a declaration error, counts
    as an extraction failure."""
    if result is None:
        return "extraction"
    if result.get("status") == "ok":
        return None
    f = result.get("failure")
    if f in ("declaration", "round_trip"):
        return f
    if f is not None:
        return "extraction"
    rc = result.get("rc")
    if rc in (None, 0) and str(result.get("error") or "").startswith(DECLARATION_ERRORS):
        return "declaration"
    return "extraction"


def check_partial(results: list[dict | None], doc: dict, shown: str, allow_partial: bool) -> int:
    """The number of extraction failures among `results`; refuses when there are any, unless
    `allow_partial` (#30)."""
    failed = sum(1 for r in results if failure_of(r) == "extraction")
    if failed and not allow_partial:
        filled = sum(1 for r in results if failure_of(r) is None)
        summ = doc.get("summary") if isinstance(doc.get("summary"), dict) else {}
        jobs = f" at {summ['jobs']} job(s)" if isinstance(summ.get("jobs"), int) else ""
        raise core.Fail(f"{shown}: extraction failed for {failed} of {len(results)} declaration(s){jobs} "
                        f"({filled} filled); re-run the driver (lower LEAN_PKG_TYPES_JOBS), or pass "
                        f"--allow-partial to use the {filled} filled and mark the rest needs_restatement "
                        f"(extraction_failed); nothing written")
    return failed


def first_line(s: object) -> str:
    lines = [ln.strip() for ln in str(s or "").splitlines() if ln.strip()]
    return lines[0] if lines else "no error text"


def fill(entry: dict, result: dict | None, summary: str, source: str, statements: str | None = None) -> dict:
    """Turn a TODO scaffold entry into a pre-filled one, or record why it stays TODO. `statements`: the
    package's declared statements module (#369), whose constants a Challenge may name like Mathlib's."""
    tname, decl = entry["theorem_name"], entry["decl"]
    failure = failure_of(result)
    if result is None:
        result = {"status": "error", "error": f"no result for {decl} in {source}"}
    typ = result.get("type")
    if failure is not None or not isinstance(typ, str) or not typ.strip():
        entry["needs_restatement"] = True
        entry["type_error"] = first_line(result.get("error") or "no type in the output")
        if failure == "extraction":
            entry["restatement_reason"] = "extraction_failed"
        elif failure == "round_trip":     # the printed type does not elaborate back to the declaration's (#31)
            entry["restatement_reason"] = "round_trip_failed"
            entry["type_error"] = " ".join(str(result.get("error") or "round-trip check failed").split())[:500]
            if isinstance(typ, str) and typ.strip():
                entry["printed_type"] = typ.strip()
        return entry
    levels = [lv for lv in result.get("levels") or [] if isinstance(lv, str) and lv]
    modules = result.get("modules")
    reason = None
    if not isinstance(modules, list):
        reason = "the output does not list the modules the type uses"
    else:
        foreign = sorted({str(m) for m in modules if str(m).split(".", 1)[0] not in VOCAB_ROOTS
                          and str(m) != statements})
        if foreign:
            reason = "the type uses project definitions (" + ", ".join(foreign[:5]) + \
                     (", ..." if len(foreign) > 5 else "") + ")"
    if reason is None and any(ch in typ for ch in UNPARSEABLE):
        reason = "the printed type has inaccessible names or elided proofs and will not re-elaborate"
    if reason is None and result.get("round_trip") != "ok":
        reason = "the output has no round-trip check of the printed type (a driver older than #31); re-run it"
    if reason is None and result.get("round_trip_exact") is False:
        reason = ("the printed type is only definitionally equal to the declaration's, not syntactically "
                  "(round_trip_exact false); check it states what the claim states")
    head = f"theorem {tname}" + (".{" + ", ".join(levels) + "}" if levels else "")
    body = typ.strip()
    doc = summary.replace("-/", "- /").replace("\n", " ").strip()
    # The text passed the round trip only under `open scoped Classical` (a decidability instance): the
    # Challenge and Solution need it too.
    opens = "open scoped Classical in\n" if result.get("round_trip_open") == "Classical" else ""
    challenge = f"{opens}/-- {doc} -/\n{head} :\n    {body.replace(chr(10), chr(10) + '    ')} := by\n  sorry"
    if reason:
        challenge = f"-- TODO: restate in Mathlib vocabulary: {reason}\n" + challenge
    entry["type"] = body
    entry["needs_restatement"] = reason is not None
    if reason:
        entry["restatement_reason"] = reason
    entry["challenge"] = challenge
    entry["solution"] = (f"{opens}{head} :\n    {body.replace(chr(10), chr(10) + '    ')} := by\n"
                         f"  exact @_root_.{decl}")
    return entry
