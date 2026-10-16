# lean-exercise -- judge a learner's Lean solution against a Challenge with `lake comparator`.
"""lean-exercise: judge a Solution file against a Challenge file, one theorem at a time.

Registry-free and read-only: no registry, event log, git, receipt or lease is ever opened. The only
writes go to a fresh temporary directory (removed afterwards unless --keep DIR). The learner's code is
built only by `lake comparator`, inside its bwrap sandbox, unless --no-sandbox is given, and then the
verdict is a self-check, never a grade. Mathlib-free in v1. Results are never registry evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import comparator_receipt as cr
from . import lean_scan
from .cli import parse_lean_toolchain, scrubbed_env

SCHEMA = "exercise-check/1"
FLOOR = cr.TOOLCHAIN_FLOOR                       # v4.35.0-rc2: the first toolchain with `lake comparator`
DEFAULT_TIMEOUT = 600
STD_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
# A Challenge may only state theorems: comparator compares definitions by type alone, so a definition
# the Solution must supply could be replaced by any term of that type. Shared definitions go in --defs.
DEF_KEYWORDS = ("def", "abbrev", "structure", "class", "instance", "inductive", "opaque", "axiom",
                "irreducible_def")
IMPORT_OK = re.compile(r"^(Init|Std|Lean)(\.|$)")
PASS_LINE = "Your solution is okay!"
VERDICTS = ("accepted", "statement_mismatch", "illegal_axiom", "kernel_rejected", "build_error", "missing",
            "timeout", "tool_error")
NO_SANDBOX_WARN = ("WARN: --no-sandbox: the Solution's code {} UNSANDBOXED on this machine, with your "
                   "user's rights. This verdict is a self-check only: never use it to grade someone else's "
                   "work. Run without --no-sandbox on Linux (or WSL2) for a sandboxed check.")


class Refusal(Exception):
    """Usage or host refusal: exit 2, nothing written (or the temporary directory removed)."""


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------------------
# Reading the exercise (no Lean involved)

def _masked(text: str) -> str:
    return lean_scan._mask(text)[0]          # comments and strings blanked, newlines kept


def imports(text: str) -> list[str]:
    return re.findall(r"^\s*import\s+(\S+)", _masked(text), re.M)


_BOUNDARY = re.compile(r"^(namespace|section|end|open|variable|universe|set_option|attribute|#|@\[|"
                       r"private|protected|noncomputable|theorem|lemma|def|abbrev|structure|class|instance|"
                       r"inductive|opaque|axiom|example|macro|syntax|notation|infix|prefix|postfix)\b",
                       re.M)


def scan(text: str) -> list[dict]:
    """Every declaration: {name, keyword, line, private, sorry}; `sorry` when the masked source from
    the declaration to the next top-level command mentions the `sorry` keyword."""
    masked = _masked(text)
    lines = masked.split("\n")
    decls = lean_scan.declarations(text)
    starts = sorted({d[0] for d in decls} | {i + 1 for i, ln in enumerate(lines) if _BOUNDARY.match(ln)})
    out = []
    for line, kw, qual, _doc, src, _written in decls:
        nxt = next((s for s in starts if s > line), len(lines) + 1)
        body = "\n".join(lines[line - 1:nxt - 1])
        out.append({"name": qual, "keyword": kw, "line": line,
                    "private": bool(re.match(r"^\s*(@\[[^\]]*\]\s*)*private\b", src)),
                    "sorry": bool(re.search(r"(?<![\w.'])sorry(?![\w'])", body))})
    return out


def read_lean(path: str, what: str) -> tuple[pathlib.Path, str]:
    p = pathlib.Path(path)
    if not p.is_file():
        raise Refusal(f"{what} {path}: not a file")
    if p.suffix != ".lean":
        raise Refusal(f"{what} {path}: expected a .lean file")
    try:
        return p, p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise Refusal(f"{what} {path}: not UTF-8")


def check_imports(text: str, what: str, has_defs: bool) -> None:
    for mod in imports(text):
        if IMPORT_OK.match(mod) or (has_defs and mod == "Defs"):
            continue
        if mod == "Defs":
            raise Refusal(f"{what} imports Defs but no --defs FILE was given")
        if mod.split(".")[0] == "Mathlib":
            raise Refusal(f"{what} imports {mod}: lean-exercise v1 is Mathlib-free (Mathlib support is planned)")
        raise Refusal(f"{what} imports {mod}: only Init, Std, Lean{' and Defs' if has_defs else ''} can be "
                      f"imported (the exercise is judged as a standalone project)")


_DEF_CMD = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|noncomputable|partial|unsafe|"
                      r"scoped|local|nonrec)\s+)*(" + "|".join(DEF_KEYWORDS) + r")\b")


def plan_theorems(challenge: str, requested: str | None) -> list[str]:
    decls = scan(challenge)
    defs = [d for d in decls if d["keyword"] in DEF_KEYWORDS]
    seen = {d["line"] for d in defs}
    for i, ln in enumerate(_masked(challenge).split("\n"), 1):   # anonymous instances have no name to scan
        m = _DEF_CMD.match(ln)
        if m and i not in seen:
            defs.append({"keyword": m.group(1), "name": "(anonymous)", "line": i})
    if defs:
        names = ", ".join(f"{d['keyword']} {d['name']} (line {d['line']})" for d in defs[:5])
        raise Refusal(f"the Challenge declares {names}: comparator compares a definition by its type only, "
                      f"so a Solution could supply any other term of that type. Put shared definitions in "
                      f"a Defs.lean passed with --defs FILE and imported by both files")
    theorems = [d for d in decls if d["keyword"] in ("theorem", "lemma")]
    if requested is not None:
        names = [n.strip() for n in requested.split(",") if n.strip()]
        if not names:
            raise Refusal("--theorems is empty")
        known = {d["name"]: d for d in theorems}
        unknown = [n for n in names if n not in known]
        if unknown:
            raise Refusal(f"--theorems {', '.join(unknown)}: not a theorem or lemma of the Challenge "
                          f"(fully qualified; found: {', '.join(known) or 'none'})")
        chosen = [known[n] for n in dict.fromkeys(names)]
    else:
        chosen = [d for d in theorems if d["sorry"]]
        if not chosen:
            raise Refusal("the Challenge has no theorem or lemma whose proof is `sorry`; state each exercise "
                          "as `theorem name ... := by sorry` (or name them with --theorems)")
    private = [d["name"] for d in chosen if d["private"]]
    if private:
        raise Refusal(f"private theorem(s) {', '.join(private)}: a private name cannot be judged; drop `private`")
    return [d["name"] for d in chosen]


def plan_axioms(value: str | None) -> list[str]:
    if value is None:
        return list(STD_AXIOMS)
    axioms = [a.strip() for a in value.split(",") if a.strip()]
    extra = [a for a in axioms if a not in STD_AXIOMS]
    if extra:
        raise Refusal(f"--axioms {', '.join(extra)}: v1 permits only a subset of the standard axioms "
                      f"{', '.join(STD_AXIOMS)}, so a grader cannot accidentally permit a cheat")
    return list(dict.fromkeys(axioms))


# ---------------------------------------------------------------------------------------------------------
# Host and toolchain (read-only)

def host_report() -> dict:
    return cr.host_checks()


def platform_hint() -> str:
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        return ("the sandbox needs Linux (bwrap): run inside WSL2 or a Linux machine, or pass --no-sandbox "
                "to self-check your own code only")
    return ("on Ubuntu 24.04 bwrap is blocked by AppArmor by default (docs/comparator.md, Host requirements "
            "per mechanism); --no-sandbox runs a self-check of your own code only")


def host_preflight(no_sandbox: bool) -> str:
    """'sandboxed' or 'self-check-only'; a refusal lists the failing host lines."""
    if no_sandbox:
        return "self-check-only"
    res = host_report()
    by = {c["item"]: c for c in res["checks"]}
    failing = [f"{i}: {by[i]['detail']}" for i in ("kernel", "bwrap") if i in by and by[i]["level"] == "fail"]
    if failing:
        raise Refusal("this host cannot run the sandboxed judge:\n  " + "\n  ".join(failing)
                      + f"\nhint: {platform_hint()}")
    return "sandboxed"


def toolchain_dir(toolchain: str) -> pathlib.Path:
    return cr.elan_home() / "toolchains" / toolchain.replace("/", "--").replace(":", "---")


def installed_toolchains() -> list[str]:
    d = cr.elan_home() / "toolchains"
    try:
        names = sorted(x.name for x in d.iterdir() if x.is_dir())
    except OSError:
        return []
    return [n.replace("---", ":").replace("--", "/") for n in names]


def resolve(toolchain: str | None) -> tuple[str, pathlib.Path, pathlib.Path]:
    """(toolchain, its prefix, elan's lake): the --toolchain, else the newest installed >= FLOOR."""
    install = f"elan toolchain install leanprover/lean4:{FLOOR}"
    floor = parse_lean_toolchain(FLOOR)
    eb = cr.elan_bin()
    if eb is None:
        raise Refusal(f"elan not found ({cr.elan_home() / 'bin' / 'elan'}); install elan, then `{install}`")
    if toolchain:
        tc = toolchain if ":" in toolchain else f"leanprover/lean4:{toolchain}"
        key = parse_lean_toolchain(tc)
        if key is None or key < floor:
            raise Refusal(f"--toolchain {toolchain}: lean-exercise needs a release toolchain {FLOOR} or later "
                          f"(the first with `lake comparator`); `{install}`")
        if not toolchain_dir(tc).is_dir():
            raise Refusal(f"toolchain {tc} is not installed; `elan toolchain install {tc}`")
    else:
        ok = [t for t in installed_toolchains() if (parse_lean_toolchain(t) or (0,)) >= floor]
        if not ok:
            raise Refusal(f"no installed toolchain is {FLOOR} or later (`lake comparator` ships from {FLOOR}); "
                          f"`{install}`")
        tc = max(ok, key=parse_lean_toolchain)
    prefix = toolchain_dir(tc)
    missing = [t for t in cr.TOOLCHAIN_TOOLS if not os.access(prefix / "bin" / t, os.X_OK)]
    if missing:
        raise Refusal(f"toolchain {tc} does not bundle {', '.join(missing)}; `{install}`")
    return tc, prefix, eb / "lake"


# ---------------------------------------------------------------------------------------------------------
# The temporary project

MANIFEST = {"version": "1.2.0", "packagesDir": ".lake/packages", "packages": [], "name": "exercise",
            "lakeDir": ".lake", "fixedToolchain": False}


def write_project(work: pathlib.Path, toolchain: str, prefix: pathlib.Path, files: dict[str, pathlib.Path],
                  theorems: list[str], axioms: list[str]) -> list[pathlib.Path]:
    """lean-toolchain, lakefile.toml, lake-manifest.json, the .lean files byte for byte, and one comparator
    configuration per theorem (cfg/<i>.json); returns the configuration paths."""
    (work / "lean-toolchain").write_text(toolchain + "\n", encoding="utf-8")
    libs = "".join(f'\n[[lean_lib]]\nname = "{m}"\n' for m in files)
    (work / "lakefile.toml").write_text(f'name = "exercise"\n{libs}', encoding="utf-8")
    (work / "lake-manifest.json").write_text(json.dumps(MANIFEST, indent=1) + "\n", encoding="utf-8")
    for mod, src in files.items():
        shutil.copyfile(src, work / f"{mod}.lean")
    (work / "cfg").mkdir()
    (work / "lake-cache").mkdir()
    cfgs = []
    for i, name in enumerate(theorems, 1):
        cfg = {"challenge_module": "Challenge", "solution_module": "Solution", "theorem_names": [name],
               "definition_names": [], "permitted_axioms": axioms,
               "external_kernels": {"nanoda": [str(prefix / "bin" / "nanoda_bin")],
                                    "con-ron": [str(prefix / "bin" / "con-ron")]}}
        p = work / "cfg" / f"{i}.json"
        p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        cfgs.append(p)
    return cfgs


# ---------------------------------------------------------------------------------------------------------
# Judging and reading the verdict

def judge(lake: pathlib.Path, work: pathlib.Path, cfg: pathlib.Path, toolchain: str, timeout: float,
          no_sandbox: bool) -> tuple[int | None, str, float]:
    """(exit code or None on timeout, merged output, seconds)."""
    env = scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(lake.parent), env.get("PATH", "")]), "ELAN_TOOLCHAIN": toolchain,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(work / "lake-cache")})
    cmd = [str(lake), "comparator", "--config", str(cfg)] + (["--inadvisably-no-sandbox"] if no_sandbox else [])
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        out, _ = p.communicate()
        rc = None
    return rc, out.decode("utf-8", errors="replace"), time.monotonic() - t0


def _quoted(line: str, marker: str) -> str:
    m = re.search(re.escape(marker) + r"\s*'([^']*)'", line)
    return m.group(1) if m else ""


def classify(rc: int | None, out: str) -> dict:
    """{verdict, detail, axiom?, kernels} from the exit code and the marker lines of `lake comparator`."""
    lines = out.splitlines()
    kernels = [m.group(1) for ln in lines for m in [re.match(r"^(.+?) kernel accepts the solution$", ln)] if m]
    kernels = ["lean" if k == "Lean default" else k for k in kernels]
    res = {"kernels": kernels}
    if rc is None:
        return {**res, "verdict": "timeout", "detail": "the judge did not finish in time (--timeout)"}
    if rc == 0 and PASS_LINE in lines:
        return {**res, "verdict": "accepted", "detail": PASS_LINE}
    for ln in lines:
        if "Illegal axiom detected:" in ln:
            ax = _quoted(ln, "Illegal axiom detected:")
            detail = ("uses sorry" if ax == "sorryAx" else
                      "uses native_decide (Lean.ofReduceBool)" if "native_decide" in ax or "ofReduceBool" in ax
                      else f"uses the axiom {ax}")
            return {**res, "verdict": "illegal_axiom", "axiom": "sorry" if ax == "sorryAx" else ax,
                    "detail": detail}
        if re.search(r"do not match|don't match|does not match between challenge|"
                     r"Solution constant is not a theorem", ln):
            return {**res, "verdict": "statement_mismatch", "detail": ln.removeprefix("error: ").strip()}
        if re.search(r"not found in solution|Constant \S+ not found in environment", ln):
            return {**res, "verdict": "missing", "detail": "the Solution does not declare this theorem"}
        if "kernel rejected the solution" in ln:
            return {**res, "verdict": "kernel_rejected", "detail": ln.strip()}
    if "error: build failed" in lines or any(ln.startswith("Some required targets logged failures") for ln in lines):
        solution_started = any(ln.startswith("Building Solution") for ln in lines)
        first = next((ln.removeprefix("error: ") for ln in lines
                      if re.match(r"^error: (Challenge|Solution|Defs)\.lean:\d+:\d+:", ln)), "")
        if not solution_started or first.startswith(("Challenge.lean", "Defs.lean")):
            return {**res, "verdict": "tool_error", "detail": "the Challenge (or Defs) does not build: " + first}
        return {**res, "verdict": "build_error", "detail": first or "the Solution does not build"}
    tail = next((ln.strip() for ln in reversed(lines) if ln.strip()), "no output")
    return {**res, "verdict": "tool_error", "detail": f"exit {rc}: {tail[:300]}"}


def redact(text: str, work: pathlib.Path) -> str:
    return text.replace(str(work), "<workdir>").replace(str(pathlib.Path.home()), "~")


# ---------------------------------------------------------------------------------------------------------
# The command

def make_workdir(keep: str | None) -> pathlib.Path:
    if keep:
        p = pathlib.Path(keep)
        if p.exists() and (not p.is_dir() or any(p.iterdir())):
            raise Refusal(f"--keep {keep}: must not exist or be an empty directory")
        p.mkdir(parents=True, exist_ok=True)
        return p.resolve()
    base = os.environ.get("TMPDIR") or os.environ.get("XDG_STATE_HOME") or None
    return pathlib.Path(tempfile.mkdtemp(prefix="lean-exercise-", dir=base)).resolve()


def check(args) -> dict:
    if args.mathlib:
        raise Refusal("--mathlib is not supported in v1 (Mathlib-free exercises only)")
    if args.timeout <= 0:
        raise Refusal("--timeout must be positive")
    challenge_p, challenge = read_lean(args.challenge, "--challenge")
    solution_p, solution = read_lean(args.solution, "--solution")
    files = {"Challenge": challenge_p, "Solution": solution_p}
    if args.defs:
        defs_p, defs = read_lean(args.defs, "--defs")
        check_imports(defs, "--defs", False)
        if any(d["sorry"] for d in scan(defs)):
            raise Refusal("--defs: the shared definitions must not use sorry")
        files["Defs"] = defs_p
    check_imports(challenge, "the Challenge", bool(args.defs))
    check_imports(solution, "the Solution", bool(args.defs))
    theorems = plan_theorems(challenge, args.theorems)
    axioms = plan_axioms(args.axioms)
    trust = host_preflight(args.no_sandbox)
    toolchain, prefix, lake = resolve(args.toolchain)
    if args.no_sandbox:
        print(NO_SANDBOX_WARN.format("runs"), file=sys.stderr)
    work = make_workdir(args.keep)
    rows: list[dict] = []
    try:
        cfgs = write_project(work, toolchain, prefix, files, theorems, axioms)
        stop = None
        for i, (name, cfg) in enumerate(zip(theorems, cfgs), 1):
            if stop is not None:
                rows.append({"name": name, "verdict": stop, "detail": "not judged: the build failed above",
                             "kernels": [], "seconds": 0.0})
                continue
            print(f"judging {name} ({i}/{len(theorems)}) ...", file=sys.stderr)
            rc, out, secs = judge(lake, work, cfg, toolchain, args.timeout, args.no_sandbox)
            (work / "cfg" / f"{i}.log").write_text(out, encoding="utf-8")
            row = classify(rc, out)
            row["detail"] = redact(row["detail"], work)
            rows.append({"name": name, **row, "seconds": round(secs, 1)})
            if row["verdict"] == "build_error" or row["detail"].startswith("the Challenge (or Defs) does not build"):
                stop = row["verdict"]
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    if args.no_sandbox:
        print(NO_SANDBOX_WARN.format("ran"), file=sys.stderr)
    accepted = sum(r["verdict"] == "accepted" for r in rows)
    return {"schema": SCHEMA, "ok": accepted == len(rows), "trust": trust, "toolchain": toolchain,
            "mechanism": "toolchain", "challenge_sha256": sha256(challenge_p), "solution_sha256": sha256(solution_p),
            "permitted_axioms": axioms, "theorems": rows, "summary": {"accepted": accepted, "total": len(rows)},
            "kept": str(work) if args.keep else None}


def render(res: dict) -> str:
    s = res["summary"]
    head = (f"{'PASS' if res['ok'] else 'FAIL'}: {s['accepted']}/{s['total']} theorem(s) accepted "
            f"({res['trust']}, {res['toolchain']})")
    out = [head]
    for r in res["theorems"]:
        extra = f" [{', '.join(r['kernels'])}]" if r["kernels"] else ""
        out.append(f"{r['verdict']:<18} {r['name']}: {r['detail']}{extra} ({r['seconds']}s)")
    if res.get("kept"):
        out.append(f"kept: {res['kept']}")
    if res["trust"] != "sandboxed":
        out.append("self-check only: the Solution ran unsandboxed (--no-sandbox)")
    out.append(head)
    return "\n".join(out)


EXAMPLES = """\
examples:
  lean-exercise check --challenge Challenge.lean --solution Solution.lean
  lean-exercise check --challenge C.lean --solution S.lean --theorems add_zero',two_mul --json
  lean-exercise check --challenge C.lean --solution S.lean --defs Defs.lean --axioms propext

exit codes: 0 every theorem accepted, 1 a theorem rejected (or the judge failed), 2 usage or host refusal.
docs: https://lean-pkg.github.io/lean-pkg/exercise.html"""


class _Parser(argparse.ArgumentParser):
    json_requested = False

    def error(self, message):
        if _Parser.json_requested:
            print(json.dumps({"ok": False, "error": message, "usage": True}))
        super().error(message)


def parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    ap = _Parser(prog="lean-exercise", formatter_class=fmt,
                 description="Judge a Lean exercise Solution against its Challenge with `lake comparator`.",
                 epilog=EXAMPLES)
    sub = ap.add_subparsers(dest="verb", metavar="check", required=True)
    c = sub.add_parser("check", formatter_class=fmt, epilog=EXAMPLES,
                       help="judge each sorry'd theorem of the Challenge against the Solution",
                       description="Judge each sorry'd theorem of the Challenge against the Solution, one "
                                   "`lake comparator` run per theorem, in its bwrap sandbox. Registry-free; "
                                   "writes only a temporary directory. Mathlib-free (v1).")
    c.add_argument("--challenge", required=True, metavar="FILE", help="the Challenge .lean (theorems := by sorry)")
    c.add_argument("--solution", required=True, metavar="FILE", help="the Solution .lean to judge")
    c.add_argument("--theorems", metavar="a,b", help="judge only these (fully qualified) Challenge theorems")
    c.add_argument("--axioms", metavar="a,b",
                   help="permitted axioms, a subset of propext,Quot.sound,Classical.choice (default: all three)")
    c.add_argument("--defs", metavar="FILE", help="shared definitions, module Defs, imported by both files")
    c.add_argument("--toolchain", metavar="T",
                   help=f"Lean toolchain (default: the newest installed, {FLOOR} or later)")
    c.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="S",
                   help=f"seconds per theorem (default {DEFAULT_TIMEOUT})")
    c.add_argument("--keep", metavar="DIR", help="build in DIR (absent or empty) and keep it")
    c.add_argument("--no-sandbox", action="store_true",
                   help="run without the bwrap sandbox: the Solution runs as you; a self-check of your OWN "
                        "code only, never a grade (trust: self-check-only)")
    c.add_argument("--mathlib", action="store_true", help=argparse.SUPPRESS)   # v2; refused in v1
    c.add_argument("--json", dest="json_out", action="store_true", help=f"JSON on stdout (schema {SCHEMA})")
    return ap


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    _Parser.json_requested = "--json" in raw
    args = parser().parse_args(raw)
    try:
        res = check(args)
    except Refusal as e:
        if args.json_out:
            print(json.dumps({"ok": False, "error": str(e), "usage": True}, indent=2))
        print(f"lean-exercise: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2) if args.json_out else render(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
