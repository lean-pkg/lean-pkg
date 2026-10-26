# lean-exercise -- judge a learner's Lean solution against a Challenge with `lake comparator`.
"""lean-exercise: judge a Solution file against a Challenge file, one theorem at a time.

Registry-free and read-only: no registry, event log, git, receipt or lease is ever opened. The only
writes go to a fresh temporary directory (removed afterwards unless --keep DIR). The learner's code is
built only by `lake comparator`, inside its bwrap sandbox, unless --no-sandbox is given, and then the
verdict is a self-check, never a grade. Mathlib-free unless --project names a prebuilt Mathlib project,
whose packages are then bound read-only into the sandbox (never downloaded). `batch` judges a directory of
Solutions into one CSV row each. Results are never registry evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from . import comparator_receipt as cr
from . import exercise_forbid as fb
from . import lean_scan
from .deps_closure import header_imports
from .palomar_check import has_module_header
from .cli import parse_lean_toolchain
from .core import scrubbed_env

SCHEMA = "exercise-check/1"
BATCH_SCHEMA = "exercise-batch/1"
FLOOR = cr.TOOLCHAIN_FLOOR                       # v4.35.0-rc2: the first toolchain with `lake comparator`
DEFAULT_TIMEOUT = 600
STD_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
# A Challenge may only state theorems: comparator compares definitions by type alone, so a definition
# the Solution must supply could be replaced by any term of that type. Shared definitions go in --defs.
DEF_KEYWORDS = ("def", "abbrev", "structure", "class", "instance", "inductive", "opaque", "axiom",
                "irreducible_def")
IMPORT_OK = re.compile(r"^(Init|Std|Lean)(\.|$)")
PASS_LINE = "Your solution is okay!"
VERDICTS = ("accepted", "statement_mismatch", "illegal_axiom", "forbidden_constant", "kernel_rejected",
            "build_error", "missing", "timeout", "tool_error")
FORBID_SCOPES = ("solution", "all")
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
    """The header's imports, module-system forms included (`public import X`, `meta import X`,
    `import all X`): a bare `^import` match missed those, so a `public import Mathlib` passed the checks."""
    return header_imports(text)


_BOUNDARY = re.compile(r"^(namespace|section|end|open|variable|universe|set_option|attribute|#|@\[|"
                       r"private|protected|public|meta|noncomputable|theorem|lemma|def|abbrev|structure|class|"
                       r"instance|inductive|opaque|axiom|example|macro|syntax|notation|infix|prefix|postfix)\b",
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
        raise Refusal(f"{what} {path}: not UTF-8") from None


def check_imports(text: str, what: str, has_defs: bool, mathlib: bool = False) -> None:
    for mod in imports(text):
        if IMPORT_OK.match(mod) or (has_defs and mod == "Defs"):
            continue
        if mod == "Defs":
            raise Refusal(f"{what} imports Defs but no --defs FILE was given")
        if mod.split(".")[0] == "Mathlib":
            if mathlib:
                continue
            raise Refusal(f"{what} imports {mod}: Mathlib needs --project DIR, a project whose "
                          f"lake-manifest.json pins Mathlib and whose Mathlib is already built")
        ok = "Init, Std, Lean" + (", Mathlib" if mathlib else "") + (" and Defs" if has_defs else "")
        raise Refusal(f"{what} imports {mod}: only {ok} can be imported (the exercise is judged as a "
                      f"standalone project)")


_DEF_CMD = re.compile(r"^\s*(?:@\[[^\]]*\]\s*)*(?:(?:private|protected|public|meta|noncomputable|partial|"
                      r"unsafe|scoped|local|nonrec)\s+)*(" + "|".join(DEF_KEYWORDS) + r")\b")


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
# --project: a prebuilt Mathlib project whose packages the sandbox reads (read-only, never downloaded)

PKG_LIB = pathlib.Path(".lake") / "build" / "lib" / "lean"


def load_project(path: str) -> dict:
    """{root, toolchain, packages (real path), binds, requires, manifest, lean_path} of a --project DIR:
    lean-toolchain + lake-manifest.json pinning mathlib, every package checked out and Mathlib built."""
    root = pathlib.Path(path)
    if not root.is_dir():
        raise Refusal(f"--project {path}: not a directory")
    tc_file, man_file = root / "lean-toolchain", root / "lake-manifest.json"
    for f in (tc_file, man_file):
        if not f.is_file():
            raise Refusal(f"--project {path}: no {f.name} (the project must pin its toolchain and Mathlib)")
    toolchain = tc_file.read_text(encoding="utf-8").strip()
    try:
        man = json.loads(man_file.read_text(encoding="utf-8"))
        pkgs = man["packages"]
        assert isinstance(pkgs, list) and all(isinstance(x, dict) and "name" in x for x in pkgs)
    except (ValueError, KeyError, AssertionError, OSError):
        raise Refusal(f"--project {path}: lake-manifest.json is not a Lake manifest") from None
    if not any(x["name"] == "mathlib" for x in pkgs):
        raise Refusal(f"--project {path}: lake-manifest.json has no mathlib package")
    pdir = root / (man.get("packagesDir") or ".lake/packages")
    hint = "run `lake exe cache get` (or `lake build`) in the project first: judging never downloads"
    if not pdir.is_dir():
        raise Refusal(f"--project {path}: {pdir.name}/ is missing; {hint}")
    real = pdir.resolve()
    binds, lean_path = [real], []
    for x in pkgs:
        if x.get("type") != "git":
            raise Refusal(f"--project {path}: dependency {x['name']} is a {x.get('type')} dependency; only "
                          f"git dependencies (as Mathlib's are) are supported")
        d = real / x["name"]
        if not d.is_dir():
            raise Refusal(f"--project {path}: dependency {x['name']} is not checked out; {hint}")
        rd = d.resolve()
        if not rd.is_relative_to(real):
            binds.append(rd)
        lean_path.append(rd / PKG_LIB)
    if not (real / "mathlib" / PKG_LIB / "Mathlib.olean").is_file():
        raise Refusal(f"--project {path}: Mathlib is not built (no Mathlib.olean); {hint}")
    return {"root": root.resolve(), "toolchain": toolchain, "packages": real, "binds": binds,
            "requires": [x for x in pkgs if not x.get("inherited")], "manifest": man, "lean_path": lean_path}


def require_toml(pkg: dict) -> str:
    """A lakefile.toml [[require]] matching the manifest entry, so Lake takes the pinned checkout as is."""
    q = json.dumps
    out = [f"\n[[require]]\nname = {q(pkg['name'])}"]
    out.append(f"scope = {q(pkg['scope'])}" if pkg.get("scope") else f"git = {q(pkg['url'])}")
    if pkg.get("inputRev"):
        out.append(f"rev = {q(pkg['inputRev'])}")
    if pkg.get("subDir"):
        out.append(f"subDir = {q(pkg['subDir'])}")
    return "\n".join(out) + "\n"


# `lake comparator` passes its whole bwrap command line through COMPARATOR_BWRAP. This wrapper binds the
# project's packages read-only into each sandbox (they sit under a home directory the sandbox covers) and
# drops --share-net, so even dependency resolution has no network. It fails closed on an unexpected line.
WRAPPER = """#!/bin/sh
# lean-exercise --project: bind the prebuilt packages read-only; no sandbox gets a network.
done=0; prev2=; prev1=
for a in "$@"; do
  shift
  if [ "$done" = 0 ] && [ "$a" = --share-net ]; then continue; fi
  if [ "$done" = 0 ] && [ "$a" = -- ] && [ "$prev2" = --chdir ]; then
    set -- "$@" {binds}
    done=1
  fi
  set -- "$@" "$a"; prev2=$prev1; prev1=$a
done
if [ "$done" = 0 ]; then
  echo "lean-exercise: unexpected bwrap command line from lake comparator; refusing to run it" >&2
  exit 125
fi
exec {bwrap} "$@"
"""


def real_bwrap() -> str:
    bw = os.environ.get("COMPARATOR_BWRAP") or "bwrap"
    found = shutil.which(bw)
    if found is None:
        raise Refusal(f"{bw} not found: `lake comparator` sandboxes with bwrap (package bubblewrap)")
    return os.path.abspath(found)


def write_wrapper(work: pathlib.Path, binds: list[pathlib.Path]) -> pathlib.Path:

    args = " ".join(f"--ro-bind {shlex.quote(str(b))} {shlex.quote(str(b))}" for b in binds)
    p = work / "bwrap-wrapper.sh"
    p.write_text(WRAPPER.format(binds=args, bwrap=shlex.quote(real_bwrap())), encoding="utf-8")
    p.chmod(0o755)
    return p


# ---------------------------------------------------------------------------------------------------------
# The temporary project

MANIFEST = {"version": "1.2.0", "packagesDir": ".lake/packages", "packages": [], "name": "exercise",
            "lakeDir": ".lake", "fixedToolchain": False}


def write_project(work: pathlib.Path, toolchain: str, prefix: pathlib.Path, files: dict[str, pathlib.Path],
                  theorems: list[str], axioms: list[str], project: dict | None = None) -> list[pathlib.Path]:
    """lean-toolchain, lakefile.toml, lake-manifest.json, the .lean files byte for byte, and one comparator
    configuration per theorem (cfg/<i>.json); returns the configuration paths. With a --project, the
    lakefile requires its direct dependencies, the manifest pins them as the project's does, and
    .lake/packages is a link to the project's (bound read-only by the wrapper)."""
    (work / "lean-toolchain").write_text(toolchain + "\n", encoding="utf-8")
    libs = "".join(f'\n[[lean_lib]]\nname = "{m}"\n' for m in files)
    reqs = "".join(require_toml(x) for x in project["requires"]) if project else ""
    (work / "lakefile.toml").write_text(f'name = "exercise"\n{reqs}{libs}', encoding="utf-8")
    manifest = dict(MANIFEST)
    if project:
        manifest["packages"] = project["manifest"]["packages"]
        (work / ".lake").mkdir(exist_ok=True)
        (work / ".lake" / "packages").symlink_to(project["packages"], target_is_directory=True)
    (work / "lake-manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
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
          no_sandbox: bool, wrapper: pathlib.Path | None = None) -> tuple[int | None, str, float]:
    """(exit code or None on timeout, merged output, seconds)."""
    env = scrubbed_env()
    env.update({"PATH": os.pathsep.join([str(lake.parent), env.get("PATH", "")]), "ELAN_TOOLCHAIN": toolchain,
                "LAKE_ARTIFACT_CACHE": "false", "LAKE_CACHE_DIR": str(work / "lake-cache")})
    if wrapper is not None:
        env["COMPARATOR_BWRAP"] = str(wrapper)
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


# Lean's module system (`module` first): a declaration is private unless it is inside `public section`
# or marked `public`, and `lake comparator` cannot export a private constant ("not found in environment").
PRIVATE_HINT = ("in a `module` file a theorem is private unless it is inside `public section ... end` or "
                "declared `public theorem`")


def classify(rc: int | None, out: str, solution_module: bool = False) -> dict:
    """{verdict, detail, axiom?, kernels} from the exit code and the marker lines of `lake comparator`.
    `solution_module`: the Solution starts with `module`, so a `missing` theorem may only be private."""
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
            # Exported before the Solution was built: the Challenge's own constant is missing (a private
            # theorem of a `module` Challenge). That is the exercise's fault, not the learner's.
            if "not found in environment" in ln and not any(x.startswith("Building Solution") for x in lines):
                return {**res, "verdict": "tool_error",
                        "detail": f"the Challenge does not export this theorem; {PRIVATE_HINT}"}
            detail = "the Solution does not declare this theorem"
            return {**res, "verdict": "missing",
                    "detail": f"{detail} (or does not make it public: {PRIVATE_HINT})" if solution_module else detail}
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


def full_toolchain(tc: str) -> str:
    return tc if ":" in tc else f"leanprover/lean4:{tc}"


def plan_forbid(args) -> dict | None:
    prefixes = [x.strip() for x in args.forbid or []]
    exact = [x.strip() for x in args.forbid_exact or []]
    bad = [x for x in prefixes + exact if not x or any(c.isspace() for c in x)]
    if bad:
        raise Refusal(f"--forbid / --forbid-exact {bad[0]!r}: a constant name or name prefix, no spaces")
    if not prefixes and not exact:
        if args.forbid_scope != "solution":
            raise Refusal("--forbid-scope needs --forbid PREFIX or --forbid-exact NAME")
        return None
    return {"prefixes": list(dict.fromkeys(prefixes)), "exact": list(dict.fromkeys(exact)),
            "scope": args.forbid_scope}


def prepare(args) -> dict:
    """Everything shared by every Solution: the exercise, the host, the toolchain, --project, --forbid."""
    if args.mathlib:
        raise Refusal("--mathlib is not a flag: pass --project DIR, a project that pins and has built Mathlib")
    if args.timeout <= 0:
        raise Refusal("--timeout must be positive")
    project = load_project(args.project) if args.project else None
    challenge_p, challenge = read_lean(args.challenge, "--challenge")
    files = {"Challenge": challenge_p}
    if args.defs:
        defs_p, defs = read_lean(args.defs, "--defs")
        check_imports(defs, "--defs", False, bool(project))
        if any(d["sorry"] for d in scan(defs)):
            raise Refusal("--defs: the shared definitions must not use sorry")
        files["Defs"] = defs_p
    check_imports(challenge, "the Challenge", bool(args.defs), bool(project))
    theorems = plan_theorems(challenge, args.theorems)
    axioms = plan_axioms(args.axioms)
    forbid = plan_forbid(args)
    trust = host_preflight(args.no_sandbox)
    requested = args.toolchain
    if project:
        if requested and full_toolchain(requested) != full_toolchain(project["toolchain"]):
            raise Refusal(f"--toolchain {requested}: the --project pins {project['toolchain']}, and its "
                          f"Mathlib is built with that one")
        requested = project["toolchain"]
    toolchain, prefix, lake = resolve(requested)
    return {"challenge_p": challenge_p, "files": files, "theorems": theorems, "axioms": axioms,
            "forbid": forbid, "trust": trust, "toolchain": toolchain, "prefix": prefix, "lake": lake,
            "project": project, "no_sandbox": args.no_sandbox, "timeout": args.timeout,
            "has_defs": bool(args.defs)}


def read_solution(plan: dict, path: str) -> tuple[pathlib.Path, str]:
    solution_p, solution = read_lean(path, "--solution")
    check_imports(solution, "the Solution", plan["has_defs"], bool(plan["project"]))
    return solution_p, solution


def forbidden(plan: dict, work: pathlib.Path, solution: str, names: list[str]) -> dict[str, list[dict]]:
    """{theorem: [{constant, via}]} from a sandboxed export of the built Solution (exercise_forbid)."""
    fz, project = plan["forbid"], plan["project"]
    scratch = work / "forbid"
    scratch.mkdir(exist_ok=True)
    lean_path = [work / PKG_LIB] + (project["lean_path"] if project else [])
    readable = project["binds"] if project else []
    sandboxed = not plan["no_sandbox"]
    lits = [fb.literal(tuple(n.split("."))) for n in names]
    if None in lits:
        raise fb.ExportError("a judged theorem name cannot be passed to leanexport")
    out = scratch / "solution.ndjson"
    fb.run_export(plan["prefix"], lean_path, readable, work, ["Solution"], lits, out, sandboxed, plan["timeout"])
    ex = fb.read_export(out)
    classify = fb.library_classifier(plan["prefix"], lean_path, readable, work, imports(solution), "Solution",
                                     scratch, sandboxed, plan["timeout"])
    return {n: fb.find_forbidden(ex, n, fz["prefixes"], fz["exact"], fz["scope"], classify) for n in names}


def judge_solution(plan: dict, solution_p: pathlib.Path, solution: str, keep: str | None = None) -> dict:
    files = dict(plan["files"])
    files["Solution"] = solution_p
    theorems, toolchain = plan["theorems"], plan["toolchain"]
    work = make_workdir(keep)
    rows: list[dict] = []
    logs: list[str] = []
    try:
        cfgs = write_project(work, toolchain, plan["prefix"], files, theorems, plan["axioms"], plan["project"])
        wrapper = write_wrapper(work, plan["project"]["binds"]) if plan["project"] and not plan["no_sandbox"] \
            else None
        stop = None
        for i, (name, cfg) in enumerate(zip(theorems, cfgs, strict=False), 1):
            if stop is not None:
                rows.append({"name": name, "verdict": stop, "detail": "not judged: the build failed above",
                             "kernels": [], "seconds": 0.0})
                continue
            print(f"judging {name} ({i}/{len(theorems)}) ...", file=sys.stderr)
            rc, out, secs = judge(plan["lake"], work, cfg, toolchain, plan["timeout"], plan["no_sandbox"], wrapper)
            (work / "cfg" / f"{i}.log").write_text(out, encoding="utf-8")
            logs.append(f"== {name}: lake comparator (exit {rc})\n" + redact(out, work))
            row = classify(rc, out, has_module_header(solution))
            row["detail"] = redact(row["detail"], work)
            rows.append({"name": name, **row, "seconds": round(secs, 1)})
            if row["verdict"] == "build_error" or row["detail"].startswith("the Challenge (or Defs) does not build"):
                stop = row["verdict"]
        judged = [r for r in rows if r["verdict"] == "accepted"]
        if plan["forbid"] and judged:
            print(f"checking forbidden constants ({len(judged)} theorem(s)) ...", file=sys.stderr)
            try:
                hits = forbidden(plan, work, solution, [r["name"] for r in judged])
            except fb.ExportError as e:
                for r in judged:
                    r.update(verdict="tool_error", detail=redact(f"the forbidden-constant check failed: {e}", work),
                             kernels=[])
                logs.append(f"== forbidden-constant check failed: {redact(str(e), work)}")
            else:
                for r in judged:
                    h = hits[r["name"]]
                    logs.append(f"== {r['name']}: forbidden constants reached: "
                                + (", ".join(x["constant"] for x in h) or "none"))
                    if h:
                        via = f" (through {' -> '.join(h[0]['via'])})" if h[0]["via"] else ""
                        more = f" and {len(h) - 1} more" if len(h) > 1 else ""
                        r.update(verdict="forbidden_constant", constant=h[0]["constant"],
                                 constants=[x["constant"] for x in h],
                                 detail=f"uses the forbidden constant {h[0]['constant']}{via}{more}")
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    accepted = sum(r["verdict"] == "accepted" for r in rows)
    res = {"schema": SCHEMA, "ok": accepted == len(rows), "trust": plan["trust"], "toolchain": toolchain,
           "mechanism": "toolchain", "challenge_sha256": sha256(plan["challenge_p"]),
           "solution_sha256": sha256(solution_p), "permitted_axioms": plan["axioms"], "theorems": rows,
           "summary": {"accepted": accepted, "total": len(rows)}, "kept": str(work) if keep else None}
    if plan["project"]:
        res["mathlib"] = True
    if plan["forbid"]:
        res["forbid"] = plan["forbid"]
    res["_logs"] = logs
    return res


def check(args) -> dict:
    plan = prepare(args)
    solution_p, solution = read_solution(plan, args.solution)
    if args.no_sandbox:
        print(NO_SANDBOX_WARN.format("runs"), file=sys.stderr)
    res = judge_solution(plan, solution_p, solution, args.keep)
    if args.no_sandbox:
        print(NO_SANDBOX_WARN.format("ran"), file=sys.stderr)
    res.pop("_logs")
    return res


# ---------------------------------------------------------------------------------------------------------
# batch: a directory of Solutions, one CSV row each

def batch(args) -> dict:
    if args.no_sandbox:
        raise Refusal("batch grades other people's work: --no-sandbox is refused (use `check --no-sandbox` "
                      "on your own Solution)")
    src = pathlib.Path(args.solutions)
    if not src.is_dir():
        raise Refusal(f"--solutions {args.solutions}: not a directory")
    sols = sorted(p for p in src.iterdir() if p.suffix == ".lean" and p.is_file())
    if not sols:
        raise Refusal(f"--solutions {args.solutions}: no .lean files in it")
    out_csv, logs_dir = pathlib.Path(args.csv), pathlib.Path(args.logs)
    if out_csv.exists():
        raise Refusal(f"--csv {args.csv}: already exists (choose a new path; nothing is overwritten)")
    if logs_dir.exists() and (not logs_dir.is_dir() or any(logs_dir.iterdir())):
        raise Refusal(f"--logs {args.logs}: must not exist or be an empty directory")
    plan = prepare(args)
    logs_dir.mkdir(parents=True, exist_ok=True)
    names = plan["theorems"]
    header = ["file", "sha256", "result", "accepted", "total"] + names + ["detail"]
    files_out = []
    with open(out_csv, "x", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for n, sp in enumerate(sols, 1):
            print(f"[{n}/{len(sols)}] {sp.name}", file=sys.stderr)
            log = [f"lean-exercise batch: {sp.name}"]
            try:
                solution_p, solution = read_solution(plan, str(sp))
            except Refusal as e:
                row = {"file": sp.name, "sha256": sha256(sp), "result": "refused", "accepted": 0,
                       "total": len(names), "detail": str(e).replace(str(sp), sp.name)}
                verdicts = ["refused"] * len(names)
                log.append(f"REFUSED: {row['detail']}")
            else:
                res = judge_solution(plan, solution_p, solution)
                verdicts = [r["verdict"] for r in res["theorems"]]
                bad = [f"{r['name']}: {r['verdict']}: {r['detail']}" for r in res["theorems"]
                       if r["verdict"] != "accepted"]
                row = {"file": sp.name, "sha256": res["solution_sha256"], "result": "pass" if res["ok"] else "fail",
                       "accepted": res["summary"]["accepted"], "total": res["summary"]["total"],
                       "detail": "; ".join(bad)}
                log += [render(res)] + res["_logs"]
            w.writerow([row["file"], row["sha256"], row["result"], row["accepted"], row["total"]] + verdicts
                       + [row["detail"]])
            fh.flush()
            (logs_dir / f"{sp.stem}.log").write_text("\n".join(log) + "\n", encoding="utf-8")
            files_out.append({**row, "verdicts": dict(zip(names, verdicts, strict=False))})
    passed = sum(f["result"] == "pass" for f in files_out)
    return {"schema": BATCH_SCHEMA, "ok": passed == len(files_out), "trust": plan["trust"],
            "toolchain": plan["toolchain"], "theorems": names, "csv": str(out_csv), "logs": str(logs_dir),
            "files": files_out, "summary": {"passed": passed, "total": len(files_out)}}


def render_batch(res: dict) -> str:
    s = res["summary"]
    head = f"{'PASS' if res['ok'] else 'FAIL'}: {s['passed']}/{s['total']} Solution(s) pass ({res['trust']}, {res['toolchain']})"
    out = [head] + [f"{f['result']:<8} {f['file']}: {f['accepted']}/{f['total']}"
                    + (f" -- {f['detail']}" if f["detail"] else "") for f in res["files"]]
    out += [f"csv: {res['csv']}", f"logs: {res['logs']}", head]
    return "\n".join(out)


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
  lean-exercise check --challenge C.lean --solution S.lean --project ~/course-mathlib \\
      --forbid MeasurableSpace.DynkinSystem. --forbid-exact MeasurableSpace.induction_on_inter
  lean-exercise batch --challenge C.lean --solutions submissions/ --csv grades.csv --logs logs/ \\
      --project ~/course-mathlib

exit codes: 0 every theorem accepted (batch: every Solution), 1 a theorem rejected (or the judge failed),
2 usage or host refusal.
docs: https://lean-pkg.github.io/lean-pkg/exercise.html"""


class _Parser(argparse.ArgumentParser):
    json_requested = False

    def error(self, message):
        if _Parser.json_requested:
            print(json.dumps({"ok": False, "error": message, "usage": True}))
        super().error(message)


def _common(c) -> None:
    c.add_argument("--challenge", required=True, metavar="FILE", help="the Challenge .lean (theorems := by sorry)")


def _options(c) -> None:
    c.add_argument("--theorems", metavar="a,b", help="judge only these (fully qualified) Challenge theorems")
    c.add_argument("--axioms", metavar="a,b",
                   help="permitted axioms, a subset of propext,Quot.sound,Classical.choice (default: all three)")
    c.add_argument("--defs", metavar="FILE", help="shared definitions, module Defs, imported by both files")
    c.add_argument("--project", metavar="DIR",
                   help="a Lake project whose lean-toolchain and lake-manifest.json pin Mathlib, built already "
                        "(`lake exe cache get`): allows `import Mathlib...`; its packages are bound read-only "
                        "into the sandbox, and nothing is downloaded")
    c.add_argument("--forbid", action="append", metavar="PREFIX",
                   help="reject a theorem whose proof depends on a constant whose name starts with PREFIX "
                        "(repeatable; computed on the compiled environment, through the Solution's helpers)")
    c.add_argument("--forbid-exact", action="append", metavar="NAME",
                   help="as --forbid, for exactly this constant (repeatable)")
    c.add_argument("--forbid-scope", choices=FORBID_SCOPES, default="solution",
                   help="solution (default): follow the Solution's own declarations, report library constants "
                        "they use; all: follow library constants too")
    c.add_argument("--toolchain", metavar="T",
                   help=f"Lean toolchain (default: the --project's, else the newest installed, {FLOOR} or later)")
    c.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="S",
                   help=f"seconds per theorem (default {DEFAULT_TIMEOUT})")
    c.add_argument("--mathlib", action="store_true", help=argparse.SUPPRESS)   # refused: --project DIR


def parser() -> argparse.ArgumentParser:
    fmt = argparse.RawDescriptionHelpFormatter
    ap = _Parser(prog="lean-exercise", formatter_class=fmt,
                 description="Judge a Lean exercise Solution against its Challenge with `lake comparator`.",
                 epilog=EXAMPLES)
    sub = ap.add_subparsers(dest="verb", metavar="{check,batch}", required=True)
    c = sub.add_parser("check", formatter_class=fmt, epilog=EXAMPLES,
                       help="judge each sorry'd theorem of the Challenge against the Solution",
                       description="Judge each sorry'd theorem of the Challenge against the Solution, one "
                                   "`lake comparator` run per theorem, in its bwrap sandbox. Registry-free; "
                                   "writes only a temporary directory. Mathlib only with --project DIR.")
    _common(c)
    c.add_argument("--solution", required=True, metavar="FILE", help="the Solution .lean to judge")
    _options(c)
    c.add_argument("--keep", metavar="DIR", help="build in DIR (absent or empty) and keep it")
    c.add_argument("--no-sandbox", action="store_true",
                   help="run without the bwrap sandbox: the Solution runs as you; a self-check of your OWN "
                        "code only, never a grade (trust: self-check-only)")
    c.add_argument("--json", dest="json_out", action="store_true", help=f"JSON on stdout (schema {SCHEMA})")
    b = sub.add_parser("batch", formatter_class=fmt, epilog=EXAMPLES,
                       help="judge every .lean file of a directory: one CSV row and one log per file",
                       description="Judge every Solution (*.lean, not recursive) in a directory as `check` "
                                   "would, sandboxed (--no-sandbox is refused), each in its own fresh "
                                   "project. Writes one CSV row per file (identified by its file name only) "
                                   "and one log per file. Registry-free.")
    _common(b)
    b.add_argument("--solutions", required=True, metavar="DIR", help="the directory of Solution .lean files")
    b.add_argument("--csv", required=True, metavar="FILE", help="the CSV to write (must not exist)")
    b.add_argument("--logs", required=True, metavar="DIR", help="per-file logs <stem>.log (absent or empty)")
    _options(b)
    b.add_argument("--no-sandbox", action="store_true", help=argparse.SUPPRESS)   # refused in batch
    b.add_argument("--json", dest="json_out", action="store_true", help=f"JSON on stdout (schema {BATCH_SCHEMA})")
    return ap


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    _Parser.json_requested = "--json" in raw
    args = parser().parse_args(raw)
    try:
        res = batch(args) if args.verb == "batch" else check(args)
    except Refusal as e:
        if args.json_out:
            print(json.dumps({"ok": False, "error": str(e), "usage": True}, indent=2))
        print(f"lean-exercise: {e}", file=sys.stderr)
        return 2
    if args.json_out:
        print(json.dumps(res, indent=2))
    else:
        print(render_batch(res) if args.verb == "batch" else render(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
