# comparator-receipt -- run comparator-check.sh on a Lake project and write
# the comparator receipt that `lean-pkg gate --comparator` / `lean-pkg
# comparator` consume. Writes NOTHING unless every comparator configuration
# passed, so a failed run can never be mistaken for evidence.
"""Usage (from a Lake project directory):

    comparator-receipt [--project DIR] [--check SCRIPT] [--mechanism M]
                       [--evidence-dir DIR] [--prepare | --from-prepared DIR]

Stages (#26.1): checkout, cache-get, build (the setup stages), comparator, receipt; each logs to
<evidence-dir>/comparator-stages/<NN>-<stage>.log with stages.json beside it, kept on failure.
--prepare runs only the setup stages; a later run with the same commit, toolchain and config hash
skips them.

Host policy: the run is allowed iff SLURM_JOB_ID is set (inside a Slurm job),
OR this host's name is listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS
(comma-separated), OR COMPARATOR_RECEIPT_ALLOW_HOST=1. The reason is recorded
in the receipt's host_policy field.

Env: COMPARATOR_CHECK (script path; default: the comparator-check.sh shipped
inside the lean_pkg package), COMPARATOR_LAKE (the lake the setup stages run), SLURM_JOB_ID (job identity when under Slurm),
COMPARATOR_RECEIPT_STACK (unlimited | a size in kB: the soft stack limit for the lake/lean children),
COMPARATOR_RECEIPT_BUILD_SOURCE (free text naming where the package's oleans were built; recorded in
build_provenance), COMPARATOR_RECEIPT_LEAN_THREADS (a positive integer: LEAN_NUM_THREADS for the lake/lean
children), COMPARATOR_RECEIPT_NO_MATHLIB (1: the project knowingly has no Mathlib; the toolchain mechanism
refuses such a project otherwise, and with it verify-comparator.sh skips `lake exe cache get`).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import importlib.resources
import json
import os
import pathlib
import re
import resource
import shutil
import socket
import subprocess
import sys
import math
import tempfile
import time

SCHEMA = "comparator-receipt/2"
PASS_LINE = "Your solution is okay!"
# Acceptance is decided by the same predicate the validator uses (one source of truth).
from .cli import build_provenance_line, comparator_pass  # noqa: E402
# Every lean/lake/git/bash below runs without the repository-selecting Git variables (#27).
from .cli import GIT_REPO_ENV, git_env_set, scrubbed_env  # noqa: E402,F401
from .holes import check_definition_holes  # noqa: E402
from .comparator_stage import redact, redact_bytes, redact_json, set_redactions  # noqa: E402
from .comparator_stage import (PARTIAL_FILE, SLOTS_DIR, UNITS_DIR, Interrupted, Judge,  # noqa: E402
                               JOBS_ENV, UNIT_SIZE_ENV, Progress, SignalGuard, SlotCache, StageLog, Unit, chunks, list_slots, parse_slot_spec, ranges, run_pool, shards, stratified_sample, unit_id, weighted_sample)
MECHANISMS = ("auto", "toolchain", "legacy")
TOOLCHAIN_FLOOR = "v4.35.0-rc2"          # Palomar's floor: the first toolchain shipping `lake comparator`
TOOLCHAIN_TOOLS = ("lake", "leanexport", "leanchecker", "nanoda_bin", "con-ron")
FAKE_LAKE_ENV = "COMPARATOR_FAKE_LAKE_OK"  # test-only: "1"/"0" overrides the toolchain probe
VERIFY_ENV = "COMPARATOR_VERIFY"           # path of verify-comparator.sh (default: the vendored copy)
# #62: the toolchain mechanism needs a Mathlib dependency; this declares a project knowingly has none.
NO_MATHLIB_ENV = "COMPARATOR_RECEIPT_NO_MATHLIB"
# ...and then the runner tells the vendored verify-comparator.sh to skip `lake exe cache get` through this.
SKIP_CACHE_GET_ENV = "VERIFY_COMPARATOR_SKIP_CACHE_GET"
# The line verify-comparator.sh prints right before `lake comparator` (the stub prints it too): a judge
# that exits non-zero without it failed before judging, which is not a verdict on the proof.
JUDGING_MARK = "== lake comparator"
LEGACY_REJECT_MARK = "::error::comparator did not accept"   # comparator-check.sh's rejection line
PIN_RE = re.compile(r"^== (comparator|lean4export|nanoda_lib|landrun) @ ([0-9a-f]{40})",
                    re.MULTILINE)
PIN_KEYS = {"comparator": "comparator", "lean4export": "lean4export",
            "nanoda_lib": "nanoda", "landrun": "landrun"}


class RunnerError(Exception):
    pass


class UsageError(RunnerError):
    """A refused invocation found only once the configurations are read (exit 2, nothing written)."""


def default_check_script():
    """The comparator-check.sh shipped as package data (a Traversable)."""
    return importlib.resources.files("lean_pkg") / "scripts" / "comparator-check.sh"


def default_verify_script():
    """The vendored PalomarTemplate verify-comparator.sh (toolchain mechanism)."""
    return importlib.resources.files("lean_pkg") / "scripts" / "verify-comparator.sh"


def toolchain_available(project: pathlib.Path) -> bool:
    """True when the project's toolchain ships `lake comparator` (Lean >= v4.35.0-rc2)."""
    fake = os.environ.get(FAKE_LAKE_ENV)
    if fake is not None:
        return fake == "1"
    # The same test the upstream script makes: the toolchain must bundle every judging binary.
    # (`lake comparator --help` is not a reliable probe: older lakes exit 0 on unknown help.)
    try:
        p = subprocess.run(["lean", "--print-prefix"], cwd=project, capture_output=True,
                           text=True, timeout=120, env=tool_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if p.returncode != 0 or not p.stdout.strip():
        return False
    prefix = pathlib.Path(p.stdout.strip())
    return all(os.access(prefix / "bin" / tool, os.X_OK) and (prefix / "bin" / tool).is_file()
               for tool in TOOLCHAIN_TOOLS)


def bwrap_preflight() -> None:
    """`lake comparator` sandboxes its judges with bwrap, which needs an unprivileged user
    namespace. Ubuntu 24.04 forbids that by default (AppArmor); fail early with the fix."""
    try:
        p = subprocess.run(["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True,
                           text=True, timeout=60, env=scrubbed_env())
    except FileNotFoundError:
        raise RunnerError("bwrap is not installed; `lake comparator` needs it (package bubblewrap)")
    except subprocess.TimeoutExpired:
        raise RunnerError("bwrap did not answer within 60 s; `lake comparator` needs a working bwrap")
    if p.returncode != 0:
        detail = (p.stderr or p.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RunnerError(
            f"bwrap cannot create a user namespace ({detail[0]}); `lake comparator` runs its judges "
            f"inside bwrap. On Ubuntu 24.04 this is the AppArmor default "
            f"(kernel.apparmor_restrict_unprivileged_userns=1). Fix as root: an AppArmor profile for "
            f"/usr/bin/bwrap with `userns,` (narrow), or "
            f"`sysctl kernel.apparmor_restrict_unprivileged_userns=0` (broad). Nothing written.")


# ---------------------------------------------------------------------------
# Host check (#22.5, #23.4): can this host produce comparator receipts at all, and with which mechanism?
# Read-only; no project needed. Per-mechanism requirements, from the scripts that run them:
# toolchain: the vendored verify-comparator.sh requires bwrap, lake, lean and python3, and `lake comparator`
#   sandboxes its judges with bwrap (an unprivileged user namespace); the project's toolchain must also be
#   v4.35.0-rc2+. No Landlock. This is what Palomar runs (docs/comparator.md, "Mechanisms").
# legacy: comparator-check.sh builds comparator/lean4export (go), nanoda (cargo) and landrun (go), needs git,
#   jq, lean, lake; comparator runs its judges under landrun (Landlock). No bwrap. The pinned comparator
#   (COMPARATOR_COMMIT, Main.lean buildLandrunArgs) always passes `--best-effort`, so landrun degrades to the
#   kernel's Landlock ABI: any ABI >= 1 runs. landrun's own default (ABI v9, strict) applies only without that
#   flag. Below ABI 4 the sandbox has no network rules; below 6 no IPC scoping (landrun README, kernel table).

LANDLOCK_SYSCALL = 444            # landlock_create_ruleset: the same number on every Linux architecture
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_MIN_ABI = 1              # landrun --best-effort runs on any Landlock (kernel 5.13+)
LANDLOCK_FULL_ABI = 4             # TCP bind/connect rules (kernel 6.7+); ABI 1-3 sandbox files only
LANDLOCK_LANDRUN_TARGET_ABI = 9   # landrun's strict default; comparator's --best-effort makes it a ceiling
ABI_SOURCE = ("thresholds: docs/comparator.md (Host requirements); landrun README at the pinned LANDRUN_COMMIT; "
              "the pinned comparator passes --best-effort")
LSM_FILE = pathlib.Path("/sys/kernel/security/lsm")
LEGACY_TOOLS = ("go", "cargo", "git", "jq", "lean", "lake")
TOOLCHAIN_TOOLS_ON_PATH = ("lean", "lake", "python3")   # verify-comparator.sh's required commands besides bwrap
PALOMAR_MECHANISM = "toolchain"   # docs/comparator.md: only the toolchain mechanism is Palomar-predictive


def landlock_abi() -> int | None:
    """The kernel's Landlock ABI version (>= 1), 0 when Landlock is absent or disabled, None when the
    probe itself cannot run (not Linux, no libc)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        r = libc.syscall(LANDLOCK_SYSCALL, None, ctypes.c_size_t(0),
                         ctypes.c_uint32(LANDLOCK_CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return None
    return int(r) if r > 0 else 0


def lsm_list() -> list[str] | None:
    try:
        return [x for x in LSM_FILE.read_text().strip().split(",") if x]
    except OSError:
        return None


def bwrap_probe() -> tuple[str, str]:
    """(level, detail) for bwrap: ok when it can create a user namespace (what bwrap_preflight demands)."""
    if shutil.which("bwrap") is None:
        return "fail", "not on PATH (package bubblewrap); the toolchain mechanism needs it"
    try:
        bwrap_preflight()
    except RunnerError as e:
        return "fail", str(e).replace(" Nothing written.", "")
    return "ok", "creates a user namespace"


def host_checks() -> dict:
    """{"checks": [{"item", "level" ok|warn|fail, "detail"}], "mechanisms": {"toolchain": bool,
    "legacy": bool}, "mechanism_detail": {m: {"ok", "needs", "failing", "palomar"}}, "can": bool,
    "palomar": bool, "verdict": str, "env_warnings": [names]}. Read-only. A mechanism is usable only
    when every one of its own requirements holds; the verdict names only those (#23.4). env_warnings
    lists the repository-selecting Git variables set in the calling environment (#27; a warn check)."""
    import platform
    checks: list[dict] = []

    def add(item, level, detail):
        checks.append({"item": item, "level": level, "detail": detail})

    linux = sys.platform.startswith("linux")
    add("kernel", "ok" if linux else "fail",
        f"{platform.system()} {platform.release()}" + ("" if linux else " (comparator receipts need Linux)"))
    abi, lsms = landlock_abi(), lsm_list()
    lsm_note = "" if lsms is None else f"; LSMs: {','.join(lsms)}"
    if abi is None:
        add("landlock", "fail", "cannot probe (not Linux)" + lsm_note)
    elif abi < LANDLOCK_MIN_ABI:
        add("landlock", "fail", "absent or disabled (needs kernel 5.13+ with landlock in the LSM list); "
            "the legacy mechanism's landrun sandbox needs it" + lsm_note)
    elif abi < LANDLOCK_FULL_ABI:
        add("landlock", "warn", f"ABI {abi}: landrun runs (--best-effort) but sandboxes files only; network "
            f"rules need ABI {LANDLOCK_FULL_ABI}, kernel 6.7+ ({ABI_SOURCE})" + lsm_note)
    elif abi < LANDLOCK_LANDRUN_TARGET_ABI:
        add("landlock", "ok", f"ABI {abi}: enough for the legacy mechanism (landrun --best-effort; ABI "
            f"{LANDLOCK_LANDRUN_TARGET_ABI} is landrun's strict default, not a requirement here; {ABI_SOURCE})"
            + lsm_note)
    else:
        add("landlock", "ok", f"ABI {abi}" + lsm_note)
    level, detail = bwrap_probe() if linux else ("fail", "Linux only")
    add("bwrap", level, detail)
    for tool in ("go", "cargo", "jq", "git", "lean", "lake", "python3"):
        where = shutil.which(tool)
        add(tool, "ok" if where else "fail", where or "not on PATH")
    eb, path_lake = elan_bin(), shutil.which("lake")   # #41: which lake would a bare PATH lookup run?
    if eb is None:
        add("lake-path", "warn", f"elan not found ({elan_home() / 'bin' / 'elan'}); comparator-receipt then uses "
            f"PATH's lake ({path_lake or 'none'}) and refuses unless its Lean version equals lean-toolchain")
    elif path_lake is None or pathlib.Path(path_lake).resolve().parent != eb.resolve():
        add("lake-path", "warn", f"PATH's lake is {path_lake or 'missing'}, not elan's ({eb / 'lake'}); "
            f"comparator-receipt resolves lake through elan per lean-toolchain, but other tools you run "
            f"here will use the PATH one")
    else:
        add("lake-path", "ok", f"PATH's lake is elan's ({path_lake})")
    leaked = git_env_set()   # #27: a launcher's repository-selecting Git variables (scrubbed by the runner)
    if leaked:
        add("git-env", "warn", f"{', '.join(leaked)} set in the calling environment; comparator-receipt "
            f"removes them from every lake/git it runs, but other tools you run here will not "
            f"(unset them in the launcher)")
    by_item = {c["item"]: c for c in checks}
    needs = {"toolchain": ("kernel", "bwrap") + TOOLCHAIN_TOOLS_ON_PATH,
             "legacy": ("kernel", "landlock") + LEGACY_TOOLS}
    detail_by: dict = {}
    for m, items in needs.items():
        failing = [f"{i}: {by_item[i]['detail']}" for i in items if by_item[i]["level"] == "fail"]
        detail_by[m] = {"ok": not failing, "needs": list(items), "failing": failing,
                        "palomar": m == PALOMAR_MECHANISM}
    mech = {m: d["ok"] for m, d in detail_by.items()}
    can = any(mech.values())
    usable = [m for m in ("toolchain", "legacy") if mech[m]]
    if can:
        parts = []
        for m in usable:
            parts.append(f"{m} (what Palomar runs; the project needs Lean {TOOLCHAIN_FLOOR}+)" if m == PALOMAR_MECHANISM
                         else f"{m} (a second-kernel replay; not what Palomar runs, so not Palomar-predictive)")
        verdict = "this host can produce comparator receipts with: " + "; ".join(parts)
        if not mech[PALOMAR_MECHANISM]:
            verdict += (f". Palomar accepts only the {PALOMAR_MECHANISM} mechanism, which fails here ("
                        + ", ".join(f.split(":", 1)[0] for f in detail_by[PALOMAR_MECHANISM]["failing"])
                        + f"; see `mechanism {PALOMAR_MECHANISM}` above)")
    else:
        verdict = ("this host cannot produce comparator receipts: "
                   + " | ".join(f"{m}: " + "; ".join(detail_by[m]["failing"]) for m in ("toolchain", "legacy")))
    return {"checks": checks, "mechanisms": mech, "mechanism_detail": detail_by, "can": can,
            "palomar": mech[PALOMAR_MECHANISM], "verdict": verdict, "env_warnings": leaked}


def toolchain_tools(project: pathlib.Path, verify: pathlib.Path, toolchain: str) -> dict:
    """Digests of the judging binaries bundled with the project's toolchain.
    Under the test-only FAKE_LAKE_ENV override, every digest is that of the verify script itself."""
    tools: dict = {"lean_toolchain": toolchain}
    if os.environ.get(FAKE_LAKE_ENV) is not None:
        d = sha256_file(verify)
        for tool in TOOLCHAIN_TOOLS:
            tools[f"{tool}_sha256"] = d
        return tools
    p = subprocess.run(["lean", "--print-prefix"], cwd=project, capture_output=True, text=True,
                       env=tool_env())
    if p.returncode != 0 or not p.stdout.strip():
        raise RunnerError("cannot resolve the toolchain prefix (`lean --print-prefix` failed)")
    prefix = pathlib.Path(p.stdout.strip())
    for tool in TOOLCHAIN_TOOLS:
        binp = prefix / "bin" / tool
        if not (binp.is_file() and os.access(binp, os.X_OK)):
            raise RunnerError(f"toolchain {toolchain} does not bundle {tool}; the toolchain "
                              f"mechanism needs Lean {TOOLCHAIN_FLOOR} or later")
        tools[f"{tool}_sha256"] = sha256_file(binp)
    return tools



# ---------------------------------------------------------------------------
# Toolchain resolution (#41). `lake` and `lean` are never taken from a bare PATH lookup: a non-login
# shell can put a system lake (an old Lean) before elan's proxies. With elan present, elan's bin/
# goes first on PATH for every stage, probe and judge (and the vendored scripts they run), and
# ELAN_TOOLCHAIN pins the checkout's lean-toolchain. Without elan, PATH's lake is used only when its
# `lake --version` names the same Lean as lean-toolchain. COMPARATOR_LAKE stays an explicit override.

ELAN_HOME_ENV = "ELAN_HOME"
_TOOL_ENV: dict = {}       # what resolve_toolchain decided for this run; merged into every tool's env
_THREADS_ENV: dict = {}    # #55: {"LEAN_NUM_THREADS": N} when COMPARATOR_RECEIPT_LEAN_THREADS is set


def tool_env() -> dict:
    env = scrubbed_env()
    for k, v in _TOOL_ENV.items():
        env[k] = v
    for k, v in _THREADS_ENV.items():
        env[k] = v
    return env


def elan_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get(ELAN_HOME_ENV) or (pathlib.Path.home() / ".elan"))


def elan_bin() -> pathlib.Path | None:
    """elan's bin/ directory when elan is installed there, else None."""
    b = elan_home() / "bin"
    e = b / "elan"
    return b if e.is_file() and os.access(e, os.X_OK) else None


def toolchain_lean_version(toolchain: str) -> str:
    """'leanprover/lean4:v4.35.0-rc2' -> '4.35.0-rc2'."""
    tail = toolchain.strip().rsplit(":", 1)[-1]
    return tail[1:] if tail.startswith("v") else tail


def lake_lean_version(text: str) -> str | None:
    m = re.search(r"Lean[^0-9]*version\s+v?([0-9][0-9A-Za-z.\-]*)", text)
    return m.group(1) if m else None


def probe_version(lake: str, project: pathlib.Path, env: dict) -> str:
    try:
        p = subprocess.run([lake, "--version"], cwd=project, capture_output=True, text=True,
                           timeout=900, env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RunnerError(f"cannot run `{lake} --version` ({e}); nothing written")
    out = (p.stdout or p.stderr).strip().splitlines()
    if p.returncode != 0 or not out:
        raise RunnerError(f"`{lake} --version` exited {p.returncode}; nothing written")
    return out[0].strip()


def resolve_toolchain(project: pathlib.Path, toolchain: str) -> dict:
    """{source: elan|path|env, lake, version}; sets _TOOL_ENV. Refuses when elan is missing and PATH's
    lake is another Lean than lean-toolchain."""
    _TOOL_ENV.clear()
    override = os.environ.get(LAKE_CMD_ENV)
    if override:
        return {"source": "env", "lake": override, "version": None}
    b = elan_bin()
    if b is not None:
        _TOOL_ENV.update({"PATH": os.pathsep.join([str(b), os.environ.get("PATH", "")]),
                          "ELAN_TOOLCHAIN": toolchain})
        lake = str(b / "lake")
        return {"source": "elan", "lake": lake, "version": probe_version(lake, project, tool_env())}
    lake = shutil.which("lake")
    if lake is None:
        raise RunnerError(f"no elan (looked for {elan_home() / 'bin' / 'elan'}; set {ELAN_HOME_ENV}) and no "
                          f"lake on PATH; install elan; nothing written")
    version = probe_version(lake, project, tool_env())
    want = toolchain_lean_version(toolchain)
    if lake_lean_version(version) != want:
        raise RunnerError(f"elan not found and PATH's lake {lake} reports {version!r}, but lean-toolchain "
                          f"names Lean {want}; install elan (or put the matching lake first on PATH); "
                          f"nothing written")
    return {"source": "path", "lake": lake, "version": version}


# ---------------------------------------------------------------------------
# Host policy. Heavy Lean work should run where the operator intends it to:
# inside a Slurm job, or on a host the operator lists explicitly. No host or
# user names are built in. COMPARATOR_RECEIPT_ALLOW_HOST=1 overrides for tests
# and explicitly authorized runs; the reason is recorded in the receipt's
# host_policy field.

ALLOWED_HOSTS_ENV = "COMPARATOR_RECEIPT_ALLOWED_HOSTS"
ALLOW_HOST_ENV = "COMPARATOR_RECEIPT_ALLOW_HOST"


def parse_allowed_hosts(value: str | None) -> set[str]:
    """Comma-separated host names; blanks and surrounding spaces ignored."""
    return {h.strip() for h in (value or "").split(",") if h.strip()}


def host_policy_ok(slurm_job: str | None, allow: bool,
                   hostname: str | None = None,
                   allowed_hosts: set[str] | None = None) -> tuple[bool, str]:
    hostname = hostname or socket.gethostname()
    allowed_hosts = allowed_hosts or set()
    if allow:
        return True, f"allowed by {ALLOW_HOST_ENV}=1"
    if slurm_job:
        return True, f"Slurm job {slurm_job} on {hostname}"
    if hostname in allowed_hosts:
        return True, f"host {hostname} listed in {ALLOWED_HOSTS_ENV}"
    return False, (f"{hostname} is not an allowed host: run inside a Slurm job "
                   f"(SLURM_JOB_ID set), list this host in {ALLOWED_HOSTS_ENV} "
                   f"(comma-separated), or set {ALLOW_HOST_ENV}=1 for an "
                   f"explicitly authorized run")

# ---------------------------------------------------------------------------


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg_name(path: str) -> str:
    """Same rule as comparator-check.sh: strip .json, '/' -> '_'."""
    base = path[:-5] if path.endswith(".json") else path
    return base.replace("/", "_")


def parse_pins(text: str) -> dict:
    pins = {PIN_KEYS[m.group(1)]: m.group(2) for m in PIN_RE.finditer(text)}
    missing = [k for k in ("comparator", "lean4export", "nanoda", "landrun")
               if k not in pins]
    if missing:
        raise RunnerError(f"comparator-check output lacks tool pins for {missing}")
    return pins


STANDARD_AXIOMS = frozenset({"propext", "Quot.sound", "Classical.choice"})


def lint_configs(project: pathlib.Path, configs: list[str]) -> None:
    """Configuration-level rules, checked before anything is built so a refusal
    writes nothing: permitted_axioms within the standard three, and definition
    holes carrying a human judged_as sign-off (see holes.py)."""
    for cfg_rel in configs:
        try:
            cfg = json.loads((project / cfg_rel).read_bytes())
        except ValueError as e:
            raise RunnerError(f"{cfg_rel}: not valid JSON ({e}); nothing written")
        if not isinstance(cfg, dict):
            raise RunnerError(f"{cfg_rel}: not a JSON object; nothing written")
        ax = cfg.get("permitted_axioms")
        if ax is None:
            raise RunnerError(f"{cfg_rel}: permitted_axioms is missing; list the axioms the solution may "
                              f"use (at most {sorted(STANDARD_AXIOMS)}, or [] for none); nothing written")
        if not isinstance(ax, list) or not all(isinstance(a, str) for a in ax):
            raise RunnerError(f"{cfg_rel}: permitted_axioms must be a list of strings; nothing written")
        extra = sorted(set(ax) - STANDARD_AXIOMS)
        if extra:
            raise RunnerError(f"{cfg_rel}: permitted_axioms {extra} outside the standard "
                              f"{sorted(STANDARD_AXIOMS)}; nothing written")
        try:
            check_definition_holes(cfg, cfg_rel)
        except ValueError as e:
            raise RunnerError(f"{e}; nothing written")


def list_configs(project: pathlib.Path) -> list[str]:
    cwd = os.getcwd()
    os.chdir(project)
    try:
        found = (sorted(glob.glob("comparator*.json"))
                 + sorted(glob.glob("comparator/config*.json")))
    finally:
        os.chdir(cwd)
    return found


def git(project: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(project), *args],
                          capture_output=True, text=True, env=scrubbed_env())


# ---------------------------------------------------------------------------
# Stages (#26.1). Every stage writes <evidence-dir>/comparator-stages/<NN>-<stage>.log and updates
# stages.json (status, exit, wall time per stage), both kept on failure. The setup stages (dependency
# checkout, Mathlib cache, build of the configured modules) can run alone with --prepare; a later run
# whose binding (commit, lean-toolchain, config hash) matches a completed setup skips straight to the
# comparison. Nothing here ever writes into the repository except the final receipt stage.

STAGES = ("checkout", "cache-get", "build", "comparator", "receipt")
SETUP_STAGES = STAGES[:3]
STAGES_DIR = "comparator-stages"
STAGES_FILE = "stages.json"
STAGES_SCHEMA = "comparator-stages/1"
DEFAULT_EVIDENCE = pathlib.Path(".lake") / "comparator-receipt"   # under the project; .lake is gitignored
LAKE_CMD_ENV = "COMPARATOR_LAKE"   # the lake the setup stages run (default: `lake` on PATH)
DONE = ("ok", "skipped", "prepared")


# Stack limit (#52). Lean recurses deeply on a large module: under the common 8 MiB soft RLIMIT_STACK a
# lean child can die with SIGSEGV (exit 139) in lean::sharecommon_quick_fn::visit_ctor, a stack overflow.
# COMPARATOR_RECEIPT_STACK=unlimited|<kB> raises the soft limit for the lake/lean children of the setup
# and comparator stages only (in their preexec), never above the hard limit; the runner itself is unchanged.
STACK_ENV = "COMPARATOR_RECEIPT_STACK"
STACK_WARN_BYTES = 8 * 1024 * 1024
STACK_WARNING = ("WARN: soft stack limit is 8 MiB; a lean SIGSEGV (exit 139) deep in "
                 "lean::sharecommon_quick_fn::visit_ctor on a large module is a stack overflow; set "
                 f"{STACK_ENV}=unlimited (or a size in kB) to raise it for the lean children")
_STACK_SOFT: list = []     # the soft limit the children get (empty: inherited, no preexec)


def parse_stack(value: str) -> int:
    """COMPARATOR_RECEIPT_STACK as bytes (resource.RLIM_INFINITY for unlimited); ValueError otherwise."""
    v = value.strip().lower()
    if v == "unlimited":
        return resource.RLIM_INFINITY
    if not re.fullmatch(r"[0-9]+", v) or int(v) <= 0:
        raise ValueError(f"{STACK_ENV} must be unlimited or a positive size in kB, got {value!r}")
    return int(v) * 1024


def stack_label(n: int) -> str:
    return "unlimited" if n == resource.RLIM_INFINITY else f"{n // 1024} kB"


def stack_kb(n: int):
    return "unlimited" if n == resource.RLIM_INFINITY else n // 1024


def plan_stack(value: str | None) -> tuple[dict, list[str]]:
    """(the receipt's stack_limit record, lines to print) for this run; sets what stack_preexec applies.
    Never above the hard limit: a request above it is clipped to it."""
    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    _STACK_SOFT.clear()
    lines: list[str] = []
    source = "inherited"
    if value is not None and value.strip():
        want = parse_stack(value)
        new = want
        if hard != resource.RLIM_INFINITY and (want == resource.RLIM_INFINITY or want > hard):
            new = hard
        _STACK_SOFT.append(new)
        source = STACK_ENV
        lines.append(f"comparator-receipt: stack: {STACK_ENV}={value.strip()}: soft stack limit for the lake/lean "
                     f"children set to {stack_label(new)} (was {stack_label(soft)}; hard limit {stack_label(hard)})"
                     + (f"; the requested {stack_label(want)} is above the hard limit, clipped to it"
                        if new != want else ""))
        soft = new
    if soft != resource.RLIM_INFINITY and soft <= STACK_WARN_BYTES:
        lines.append(STACK_WARNING)
    return {"soft_kb": stack_kb(soft), "hard_kb": stack_kb(hard), "source": source}, lines


# Lean threads (#55). Lake 5 has no -j; it schedules build jobs on the Lean task pool, whose size is
# LEAN_NUM_THREADS. COMPARATOR_RECEIPT_LEAN_THREADS=N puts LEAN_NUM_THREADS=N in the environment of every
# lake/lean child of the setup and comparator stages (the same children COMPARATOR_RECEIPT_STACK reaches),
# so a memory-tight host can serialize the build without a COMPARATOR_LAKE wrapper.
LEAN_THREADS_ENV = "COMPARATOR_RECEIPT_LEAN_THREADS"


def parse_lean_threads(value: str) -> int:
    v = value.strip()
    if not re.fullmatch(r"[0-9]+", v) or int(v) <= 0:
        raise ValueError(f"{LEAN_THREADS_ENV} must be a positive integer, got {value!r}")
    return int(v)


def plan_lean_threads(value: str | None) -> tuple[int | None, list[str]]:
    """(the receipt's lean_threads, lines to print); sets what tool_env() adds for the children."""
    _THREADS_ENV.clear()
    if value is None or not value.strip():
        return None, []
    n = parse_lean_threads(value)
    _THREADS_ENV["LEAN_NUM_THREADS"] = str(n)
    return n, [f"comparator-receipt: lean threads: {LEAN_THREADS_ENV}={n}: LEAN_NUM_THREADS={n} for the "
               f"lake/lean children of the setup and comparator stages"]


def stack_preexec():
    """The preexec_fn of the setup and comparator children: apply the planned soft stack limit, or None
    (no preexec at all) when the run inherits the runner's limit."""
    if not _STACK_SOFT:
        return None
    soft = _STACK_SOFT[0]

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_STACK, (soft, resource.getrlimit(resource.RLIMIT_STACK)[1]))
    return apply


# Build provenance (#56). The build stage runs `lake build <Challenge> <Solution>`; with a .lake/build that
# another host populated at the same commit, toolchain and manifest it is a no-op, and the receipt says so.
# The rule: snapshot the mtimes of the root package's own oleans (<project>/.lake/build/lib/**/*.olean;
# dependencies under .lake/packages/ are not the package's modules) before and after the stage. An olean
# that is new or whose mtime changed was written by this stage. Lake's own `[i/n] Built <target>` lines in
# the stage log are a second witness. compiled = either witness; modules_built = the oleans written (or,
# when none changed, the distinct `Built` targets); prebuilt = the stage ran, passed and compiled
# nothing. COMPARATOR_RECEIPT_BUILD_SOURCE declares where a prebuilt tree came from (free text, redacted).
BUILD_SOURCE_ENV = "COMPARATOR_RECEIPT_BUILD_SOURCE"
BUILT_LINE_RE = re.compile(r"\[\d+/\d+\]\s+Built\s+([^\s(]+)")


def build_source() -> str | None:
    v = (os.environ.get(BUILD_SOURCE_ENV) or "").strip()
    return redact(v) if v else None


def root_oleans(project: pathlib.Path):
    """(path under .lake/build/lib, stat) for each of the root package's oleans."""
    root = project / ".lake" / "build" / "lib"
    if root.is_dir():
        for p in root.rglob("*.olean"):
            try:
                yield p.relative_to(root).as_posix(), p.stat()
            except OSError:
                pass


def olean_snapshot(project: pathlib.Path) -> dict[str, int]:
    """{path under .lake/build/lib: mtime_ns} for the root package's oleans."""
    return {rel: st.st_mtime_ns for rel, st in root_oleans(project)}


def olean_sizes(project: pathlib.Path) -> dict[str, int]:
    """{module name: olean size in bytes} for the root package's oleans (A/B/C.olean -> A.B.C)."""
    return {rel[:-len(".olean")].replace("/", "."): st.st_size for rel, st in root_oleans(project)}


def slot_weight(name: str, sizes: dict[str, int]) -> tuple[int, str | None]:
    """--estimate's weight for a declaration (#59): the size of the olean of the module named by the
    longest proper namespace prefix of the name that is one of the package's modules (A.B.C.thm tries
    A.B.C, then A.B, then A); (0, None) when none is. A proxy: a bigger olean marks a heavier module,
    whose declarations tend to pull a bigger closure."""
    parts = name.split(".")
    for n in range(len(parts) - 1, 0, -1):
        mod = ".".join(parts[:n])
        if mod in sizes:
            return sizes[mod], mod
    return 0, None


def build_provenance(before: dict[str, int] | None, after: dict[str, int] | None, log_text: str,
                     source: str | None) -> dict:
    """The receipt's build_provenance for a build stage that ran and passed (before/after: olean
    snapshots), or for one that did not run (before is None)."""
    if before is None or after is None:
        return {"compiled": False, "modules_built": None, "prebuilt": False, "source": source}
    written = [k for k, m in after.items() if before.get(k) != m]
    built = {m.group(1) for m in BUILT_LINE_RE.finditer(log_text)}
    n = len(written) if written else len(built)
    compiled = bool(written or built)
    return {"compiled": compiled, "modules_built": n, "prebuilt": not compiled, "source": source}


def carried_provenance(prior: dict | None) -> dict:
    """The provenance a reused setup recorded; all-null when it predates build_provenance. The
    declared source of this run fills a recorded null source."""
    bp = (prior or {}).get("build_provenance")
    bp = dict(bp) if isinstance(bp, dict) else {"compiled": None, "modules_built": None, "prebuilt": None,
                                                 "source": None}
    if bp.get("source") is None:
        bp["source"] = build_source()
    return bp


class StageError(RunnerError):
    def __init__(self, msg: str, code: int | None = None):
        super().__init__(msg)
        self.code = code


def config_hash(project: pathlib.Path, configs: list[str]) -> str:
    """One digest over every configuration path and its bytes (the prepared state's config binding)."""
    rows = [[c, sha256_file(project / c)] for c in sorted(configs)]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def has_lakefile(project: pathlib.Path) -> bool:
    return (project / "lakefile.lean").is_file() or (project / "lakefile.toml").is_file()


def manifest_has_mathlib(project: pathlib.Path) -> bool:
    try:
        man = json.loads((project / "lake-manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pkgs = man.get("packages") if isinstance(man, dict) else None
    return any(isinstance(p, dict) and p.get("name") == "mathlib" for p in pkgs or [])


def config_modules(project: pathlib.Path, configs: list[str]) -> list[str]:
    mods: list[str] = []
    for c in configs:
        cfg = json.loads((project / c).read_bytes())
        for k in ("challenge_module", "solution_module"):
            m = cfg.get(k)
            if isinstance(m, str) and m.strip() and m not in mods:
                mods.append(m)
    return mods


def run_logged(cmd: list[str], cwd: pathlib.Path, log: pathlib.Path) -> int:
    """Run cmd (env: tool_env(), #41) with its output streamed, redacted, into log, the way stage 04
    runs its judges: stdin /dev/null, one merged pipe, heartbeats, killed with the runner."""
    slog = StageLog(log)
    try:
        j = Judge(cmd, cwd, tool_env(), None, slog, " ".join(cmd[1:3]) or cmd[0], preexec=stack_preexec())
        j.start()
        with SignalGuard():
            try:
                run_pool([j], 1, lambda job: job, lambda job, jd: True)
            except Interrupted as e:
                raise StageError(f"{e}; `{' '.join(cmd)}` killed; nothing written", 128 + e.signum)
        return j.returncode if j.returncode is not None else 1
    finally:
        slog.close()


class Stages:
    """The stage ledger of one run: logs and stages.json under <evidence>/comparator-stages/."""

    def __init__(self, evidence: pathlib.Path, binding: dict, prepare: bool):
        self.dir = evidence / STAGES_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.json = self.dir / STAGES_FILE
        self.binding, self.prepare = binding, prepare
        self.entries: dict[str, dict] = {}
        self.progress: dict | None = None   # stage 04's {done, total, failed, partial_report} (#39)
        self.resolution: dict | None = None  # toolchain_resolution {source, lake, version} (#41)
        self.build_provenance: dict | None = None   # #56: {compiled, modules_built, prebuilt, source}
        self.pool: dict | None = None        # #60: stage 04's {judges, unit_size} (toolchain mechanism)

    def log(self, stage: str) -> pathlib.Path:
        return self.dir / f"{STAGES.index(stage) + 1:02d}-{stage}.log"

    def clear(self, stages) -> None:
        for s in stages:
            try:
                self.log(s).unlink()
            except OSError:
                pass

    def save(self) -> None:
        entries = [self.entries[s] for s in STAGES if s in self.entries]
        failed = next((e["stage"] for e in entries if e["status"] in ("failed", "partial")), None)
        setup_done = all(self.entries.get(s, {}).get("status") in DONE for s in SETUP_STAGES)
        data = {"schema": STAGES_SCHEMA, **self.binding, "prepare": self.prepare,
                "setup_complete": setup_done, "failed_stage": failed, "stages": entries,
                "git_env_scrubbed": git_env_set(),   # #27: removed from every lake/git/bash it ran
                **({"progress": self.progress} if self.progress is not None else {}),
                **({"toolchain_resolution": self.resolution} if self.resolution is not None else {}),
                **({"build_provenance": self.build_provenance} if self.build_provenance is not None else {}),
                **(self.pool or {}),
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        tmp = self.json.with_name(STAGES_FILE + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.json)

    def record(self, stage: str, status: str, code, wall: float, note: str = "",
               log: pathlib.Path | None = None) -> None:
        self.entries[stage] = {"n": STAGES.index(stage) + 1, "stage": stage, "status": status,
                               "exit": code, "wall_s": round(wall, 3), "note": note,
                               "log": str(log or self.log(stage))}
        self.save()

    def run(self, stage: str, body) -> None:
        """body(log) -> (status, exit, note). A RunnerError marks the stage failed, keeps its log and
        stages.json, and is re-raised naming the stage and the log."""
        log = self.log(stage)
        log.write_text("", encoding="utf-8")
        tag = f"{STAGES.index(stage) + 1:02d}-{stage}"
        print(f"comparator-receipt: stage {tag}: running (log {log})", file=sys.stderr)
        t0 = time.monotonic()
        try:
            status, code, note = body(log)
        except RunnerError as e:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"comparator-receipt: {e}\n")
            status = "partial" if isinstance(e, PartialStage) else "failed"
            self.record(stage, status, getattr(e, "code", None), time.monotonic() - t0, str(e), log)
            msg = f"stage {tag} {status}: {e} (log: {log}; stages: {self.json})"
            if isinstance(e, PartialStage):
                raise PartialStage(msg, e.code) from None
            raise RunnerError(msg) from None
        self.record(stage, status, code, time.monotonic() - t0, note, log)
        print(f"comparator-receipt: stage {tag}: {status}" + (f" ({note})" if note else ""),
              file=sys.stderr)

    def summary(self) -> list[dict]:
        """The receipt's additive `stages` field: no paths, no host names."""
        return [{k: self.entries[s][k] for k in ("stage", "status", "exit", "wall_s")}
                for s in STAGES if s in self.entries]


def read_stages(path: pathlib.Path) -> dict | None:
    """A stages.json, given it, its comparator-stages/ directory or the evidence directory."""
    for cand in (path, path / STAGES_FILE, path / STAGES_DIR / STAGES_FILE):
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return data if isinstance(data, dict) and data.get("schema") == STAGES_SCHEMA else None
    return None


def prepared_mismatch(data: dict | None, binding: dict) -> str | None:
    """None when data records a completed setup for exactly this binding; else why not."""
    if data is None:
        return "no comparator-stages/stages.json there"
    for k in ("commit", "lean_toolchain", "config_hash"):
        if data.get(k) != binding[k]:
            return f"prepared for {k} {str(data.get(k))[:12]}, this run is {str(binding[k])[:12]}"
    by = {e.get("stage"): e for e in data.get("stages") or [] if isinstance(e, dict)}
    undone = [s for s in SETUP_STAGES if (by.get(s) or {}).get("status") not in DONE]
    if undone:
        return f"setup incomplete there (stage(s) {', '.join(undone)} not done)"
    return None


def setup_stages(project: pathlib.Path, configs: list[str], stages: Stages) -> None:
    lake = os.environ.get(LAKE_CMD_ENV) or "lake"   # with elan, PATH starts at elan's bin/ (tool_env)
    lakefile = has_lakefile(project)
    no_lake = ("skipped", None, "no lakefile.lean or lakefile.toml")

    def step(cmd: list[str], what: str):
        def body(log):
            code = run_logged(cmd, project, log)
            if code != 0:
                raise StageError(f"`lake {' '.join(cmd[1:])}` ({what}) exited {code}; nothing written", code)
            return "ok", 0, what
        return body

    stages.run("checkout", step([lake, "env", "true"], "dependency checkout per lake-manifest.json")
               if lakefile else (lambda log: no_lake))
    if not lakefile:
        stages.run("cache-get", lambda log: no_lake)
    elif manifest_has_mathlib(project):
        stages.run("cache-get", step([lake, "exe", "cache", "get"], "Mathlib build cache"))
    else:
        stages.run("cache-get", lambda log: ("skipped", None, "no mathlib package in lake-manifest.json"))
    mods = config_modules(project, configs)
    if lakefile and mods:
        before = olean_snapshot(project)
        stages.run("build", step([lake, "build", *mods], "build of " + ", ".join(mods)))
        log_text = stages.log("build").read_text(encoding="utf-8", errors="replace")
        stages.build_provenance = build_provenance(before, olean_snapshot(project), log_text, build_source())
    else:
        stages.run("build", lambda log: no_lake if not lakefile else
                   ("skipped", None, "no challenge/solution module configured"))
        stages.build_provenance = build_provenance(None, None, "", build_source())
    stages.save()
    print(f"comparator-receipt: {build_provenance_line(stages.build_provenance)}", file=sys.stderr)


def judged(log_text: str, mechanism: str, returncode: int | None) -> bool:
    """Whether a failed judge got as far as judging (#62): verify-comparator.sh prints JUDGING_MARK right
    before `lake comparator`, comparator-check.sh prints LEGACY_REJECT_MARK on a rejection. Exit 0 without
    the acceptance line (a judge that ran and said nothing usable) counts as judged."""
    if returncode == 0:
        return True
    lines = log_text.splitlines()
    if mechanism == "legacy":
        return any(LEGACY_REJECT_MARK in ln for ln in lines)
    return any(ln.startswith(JUDGING_MARK) for ln in lines)


def last_line(log_text: str, limit: int = 200) -> str:
    """The judge log's last non-empty line, shortened, for a failure message."""
    for ln in reversed(log_text.splitlines()):
        if ln.strip():
            ln = ln.strip()
            return ln if len(ln) <= limit else ln[:limit] + "..."
    return ""


class PartialStage(StageError):
    """Stage 04 stopped part-way (a signal): its stage status is `partial`, not `failed`."""


class Stage04:
    """Stage 04 (#39): plan the units, run their judges (streamed, heartbeat, killed on TERM), print
    per-slot progress, keep 04-comparator.partial.json and stages.json `progress` current, and
    assemble each configuration's receipt log from the logs of the units that judged it."""

    def __init__(self, project: pathlib.Path, configs: list[str], mechanism: str, check: pathlib.Path,
                 verify: pathlib.Path | None, stages: Stages, log_dir: pathlib.Path, resume_command: str,
                 resume: bool = True, judges: int = 1, subset: list | None = None, isolate: bool = False,
                 unit_size: int | None = None):
        self.judges = max(1, judges)
        self.unit_size = unit_size if unit_size and not isolate else None   # #60: names per unit, or None
        self.isolate = isolate                       # one unit per slot, run one at a time (--estimate)
        self.project, self.configs, self.mechanism = project, configs, mechanism
        self.check, self.verify, self.stages, self.log_dir = check, verify, stages, log_dir
        self.cfgs = [(c, json.loads((project / c).read_bytes())) for c in configs]
        self.all_slots = list_slots(self.cfgs)
        self.slots = list(subset) if subset is not None else list(self.all_slots)   # the slots in play
        self.partial = len(self.slots) != len(self.all_slots)
        if self.partial:
            self.configs = [c for c in configs if any(s.config == c for s in self.slots)]
            self.cfgs = [(c, d) for c, d in self.cfgs if c in self.configs]
        self.units_dir = stages.dir / UNITS_DIR
        self.resume_command = resume_command
        self.progress = Progress(stages.dir / PARTIAL_FILE, self.slots, {
            **stages.binding, "mechanism": mechanism, "resume_command": resume_command})
        self.passed: dict[str, list[tuple[Unit, pathlib.Path]]] = {c: [] for c in self.configs}
        self.legacy_output = ""
        self.slog: StageLog | None = None
        self.resume = resume
        self.cached: dict[int, dict] = {}            # slot index -> valid cache entry (resume)
        self.cache = None if mechanism == "legacy" else SlotCache(
            stages.dir / SLOTS_DIR, stages.binding, mechanism, sha256_file(verify),
            lambda text: comparator_pass(text, "toolchain"))

    # -- planning ---------------------------------------------------------------------------------
    def plan(self) -> list[Unit]:
        if self.mechanism == "legacy":   # comparator-check.sh judges every configuration in one run
            units = [Unit(1, "", list(self.slots), True)]
        else:
            if self.cache is not None and self.resume:
                for s in self.slots:
                    e = self.cache.get(s)
                    if e is not None:
                        self.cached[s.index] = e
            units = []
            for rel, _cfg in self.cfgs:
                mine = [s for s in self.slots if s.config == rel]
                every = sum(1 for s in self.all_slots if s.config == rel)
                pend = [s for s in mine if s.index not in self.cached]
                if not mine:
                    units.append(Unit(len(units) + 1, rel, [], True))
                for part in self.cut(pend):
                    units.append(Unit(len(units) + 1, rel, part, len(part) == every))
        for u in units:
            u.id = unit_id(self.stages.binding, u)
        return units

    def cut(self, pend: list) -> list[list]:
        """A configuration's pending slots as units: one per slot (--estimate); contiguous chunks of at
        most unit_size (--unit-size K, #60; --judges is then only the pool width); else --judges
        near-equal shards (the default --judges 1: the whole configuration in one unit)."""
        if not pend:
            return []
        if self.isolate:
            return shards(pend, len(pend))
        if self.unit_size:
            return chunks(pend, self.unit_size)
        return shards(pend, self.judges)

    def command(self, unit: Unit) -> tuple[list[str], dict]:
        env = tool_env()
        if self.mechanism == "legacy":
            env["LOG_DIR"] = str(self.log_dir)
            return ["bash", str(self.check)], env
        if not manifest_has_mathlib(self.project):   # reached only with NO_MATHLIB_ENV=1 (or no lakefile)
            env[SKIP_CACHE_GET_ENV] = "1"
        if unit.whole:
            return ["bash", str(self.verify), unit.config], env
        # a partial copy: the configuration with theorem_names cut to this unit's slots, all else equal
        cfg = dict(next(c for rel, c in self.cfgs if rel == unit.config))
        cfg["theorem_names"] = [s.name for s in unit.slots]
        part = self.units_dir / f"{unit.id}.json"
        part.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        return ["bash", str(self.verify), str(part)], env

    # -- progress ---------------------------------------------------------------------------------
    def say(self, text: str) -> None:
        assert self.slog is not None
        self.slog.write(text)

    def tag(self, unit: Unit) -> str:
        idx = ranges(s.index for s in unit.slots) if unit.slots else "-"
        return f"slot {idx}/{len(self.all_slots)} {unit.label()}"

    def publish(self) -> None:
        self.progress.write()
        self.stages.progress = {**self.progress.counts(), "partial_report": str(self.progress.path)}
        self.stages.save()

    # -- running ----------------------------------------------------------------------------------
    def start(self, unit: Unit) -> Judge:
        log = self.units_dir / f"{unit.id}.log"
        tmpdir = self.units_dir / f"{unit.id}.tmp"
        for stale in (log,):
            try:
                stale.unlink()
            except OSError:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)
        cmd, env = self.command(unit)
        self.say(f"{self.tag(unit)} started (unit {unit.n}/{self.n_units}, judge log {log.name})")
        j = Judge(cmd, self.project, env, log, self.slog, f"unit {unit.n}/{self.n_units} ({unit.label()})",
                  prefix=f"[{unit.n}] " if self.width > 1 else "", tmpdir=tmpdir, preexec=stack_preexec())
        j.start()
        return j

    def finish(self, unit: Unit, j: Judge) -> bool:
        shutil.rmtree(j.tmpdir, ignore_errors=True) if j.tmpdir else None
        secs = j.elapsed()
        if j.killed:
            self.say(f"{self.tag(unit)} stopped after {secs:.1f}s (judge killed)")
            return False
        out = j.log.read_text(encoding="utf-8", errors="replace") if j.log.is_file() else ""
        ok = j.returncode == 0 and (self.mechanism == "legacy" or comparator_pass(out, "toolchain"))
        if not ok:
            self.progress.mark(unit.slots, "failed")
            self.failure = (unit, j.returncode, judged(out, self.mechanism, j.returncode), last_line(out))
            self.say(f"{self.tag(unit)} failed (exit {j.returncode}) in {secs:.1f}s")
            self.publish()
            return False
        if self.mechanism == "legacy":
            self.legacy_output = out
        else:
            self.passed[unit.config].append((unit, j.log))
            if self.cache is not None and unit.slots:
                self.cache.put(unit.slots, j.log, unit.whole, secs)
        self.progress.mark(unit.slots, "judged")
        self.say(f"{self.tag(unit)} done in {secs:.1f}s ({self.progress.counts()['done']}/{len(self.slots)} done)")
        self.wall[unit.n] = secs
        self.publish()
        return True

    def run(self, stage_log: pathlib.Path) -> tuple[str, int, str]:
        self.units_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        units = self.plan()
        self.n_units, self.failure = len(units), None
        self.width = 1 if self.isolate else self.judges
        self.units, self.wall = units, {}
        self.slog = StageLog(stage_log, echo=sys.stdout)
        try:
            if self.isolate:
                self.say(f"comparator-receipt: --estimate: {len(self.slots)} sampled slot(s) "
                         f"({ranges(s.index for s in self.slots)}), each judged alone, one at a time; "
                         f"no receipt")
            elif self.partial:
                self.say(f"comparator-receipt: --slots: {len(self.slots)} of {len(self.all_slots)} slot(s) "
                         f"({ranges(s.index for s in self.slots)}); a receipt from them is partial "
                         f"(diagnostic only)")
            if self.unit_size:   # #60: memory/time guidance, once
                self.say(f"comparator-receipt: units: {sum(1 for u in units if u.slots)} of at most "
                         f"{self.unit_size} names, width {self.width}")
            self.say(f"comparator-receipt: {len(self.slots)} slot(s) in {len(self.configs)} config(s), "
                     f"{len(units)} judge invocation(s), {self.width} at a time, mechanism {self.mechanism}")
            self.announce_cached()
            self.publish()
            try:
                with SignalGuard():
                    run_pool(units, self.width, self.start, self.finish)
            except Interrupted as e:
                for t in self.units_dir.glob("*.tmp"):   # a killed judge's exports (can be GBs)
                    shutil.rmtree(t, ignore_errors=True)
                self.progress.state = "interrupted"
                self.publish()
                msg = (f"{e}; judges killed; {self.progress.summary()}; nothing written; "
                       f"partial report {self.progress.path}; resume: {self.resume_command}")
                self.say(f"comparator-receipt: {msg}")
                raise PartialStage(msg, 128 + e.signum)
            if self.failure is not None:
                unit, rc, was_judged, last = self.failure
                self.progress.state = "failed"
                self.publish()
                what = "comparator-check" if self.mechanism == "legacy" else f"{unit.config}: lake comparator"
                if was_judged:
                    verdict = f"{what} did not accept the solution ({self.tag(unit)}, exit {rc})"
                else:
                    judge = "comparator-check" if self.mechanism == "legacy" else f"{unit.config}: verify-comparator"
                    verdict = (f"{judge}: the judge tool failed before judging ({self.tag(unit)}, exit {rc}"
                               + (f"; last line: {last}" if last else "") + "); this is not a verdict on the "
                               f"proof, fix the tool or the project setup and rerun")
                raise StageError(f"{verdict}; {self.progress.summary()}; nothing written; partial report "
                                 f"{self.progress.path}", rc)
            self.assemble()
            self.progress.state = "complete"
            try:
                self.progress.path.unlink()
            except OSError:
                pass
            self.stages.progress = {**self.progress.counts(), "partial_report": None}
            return "ok", 0, (f"{len(self.configs)} config(s) accepted ({self.mechanism}; "
                             f"{len(self.slots)} slot(s))")
        finally:
            self.slog.close()

    def announce_cached(self) -> None:
        if not self.cached:
            return
        for rel in self.configs:
            hit = [s for s in self.slots if s.config == rel and s.index in self.cached]
            if hit:
                self.progress.mark(hit, "cached")
                label = hit[0].name if len(hit) == 1 else f"{rel}[{len(hit)} names]"
                self.say(f"slot {ranges(s.index for s in hit)}/{len(self.all_slots)} {label} cached "
                         f"(accepted earlier at this commit and config hash; --no-resume judges again)")

    def parts(self, rel: str) -> list[tuple[list, pathlib.Path, bool]]:
        """(slots, log, whole) of every unit that accepted part of rel, this run's and cached ones."""
        out = [(u.slots, log, u.whole) for u, log in self.passed[rel]]
        by_unit: dict[tuple, list] = {}     # (log, the accepting unit's names) -> [whole, slots]
        for s in self.slots:
            e = self.cached.get(s.index)
            if s.config == rel and e is not None:
                key = (e["log_path"], tuple(e.get("unit_slots") or ()))
                by_unit.setdefault(key, [e.get("whole"), []])[1].append(s)
        out += [(ss, log, bool(whole) and len(ss) == sum(1 for x in self.all_slots if x.config == rel))
                for (log, _names), (whole, ss) in by_unit.items()]
        return sorted(out, key=lambda p: min((s.index for s in p[0]), default=0))

    def assemble(self) -> None:
        """Each configuration's receipt log: its single whole-config unit log byte for byte, else the
        logs of the units that judged it, each under a header naming its slots."""
        self.receipt_units: dict[str, list[dict]] = {}
        if self.mechanism == "legacy":
            return                                   # comparator-check.sh wrote LOG_DIR itself
        for rel in self.configs:
            parts = self.parts(rel)
            dst = self.log_dir / f"{cfg_name(rel)}.log"
            if len(parts) == 1 and parts[0][2]:
                shutil.copyfile(parts[0][1], dst)
                continue
            rows = []
            with open(dst, "wb") as fh:
                for slots, log, _whole in parts:
                    data = log.read_bytes()
                    fh.write(f"== unit: slots {ranges(s.index for s in slots)} "
                             f"({len(slots)} names)\n".encode())
                    fh.write(data)
                    rows.append({"slots": [s.name for s in slots],
                                 "log_sha256": hashlib.sha256(data).hexdigest()})
            self.receipt_units[rel] = rows


ESTIMATE_FILE = "04-comparator.estimate.json"
ESTIMATE_SCHEMA = "comparator-estimate/2"
ESTIMATE_MARGIN = 1.25          # recommended_cap = ceil_5min(MARGIN * model_seconds + SETUP_ALLOWANCE)
SETUP_ALLOWANCE = 600           # seconds: lake env, the no-op builds and `lake exe cache get` per run
ESTIMATE_MIN_SAMPLE = 5         # the sample floor (#59): max(5, ceil(0.25 * slots)), capped at the slots
ESTIMATE_MIN_FRACTION = 0.25
WHOLE_UNIT_WARNING = (
    "the per-slot model times each sampled name judged alone; the real run judges each configuration as "
    "one unit (or one unit per --judges shard), whose single export pays the union of its names' "
    "closures, which this model does not bound, and a unit is accepted or lost whole within a cap. "
    "With --unit-size 1 every name is its own unit, cached on acceptance, so the model applies per name "
    "and a capped run resumes where it stopped")


def sample_floor(slots: int) -> int:
    return min(slots, max(ESTIMATE_MIN_SAMPLE, math.ceil(ESTIMATE_MIN_FRACTION * slots)))


def ceil_5min(seconds: float) -> int:
    return int(math.ceil(seconds / 300.0) * 300)


def estimate_numbers(times: list[float], slots: int, judges: int) -> dict:
    """The estimate from isolated per-slot timings. A slot judged alone pays its whole export closure;
    estimated_seconds is the per-slot mean model (mean * slots / judges) and lower_bound_seconds the
    dearest single slot. The mean does not bound an unsampled heavy slot (#59), so when the sample
    covers less than half the slots the cap uses the max model (max * slots / judges) instead."""
    mean = sum(times) / len(times)
    top = max(times)
    est = mean * slots / max(1, judges)
    est_max = top * slots / max(1, judges)
    model = "max" if 2 * len(times) < slots else "mean"
    return {"per_slot_seconds": round(mean, 1), "per_slot_max_seconds": round(top, 1),
            "per_slot_min_seconds": round(min(times), 1), "slots": slots, "judges": judges,
            "estimated_seconds": int(math.ceil(est)), "estimated_seconds_max": int(math.ceil(est_max)),
            "lower_bound_seconds": int(math.ceil(top)), "cap_model": model,
            "recommended_cap": ceil_5min(ESTIMATE_MARGIN * (est_max if model == "max" else est)
                                         + SETUP_ALLOWANCE),
            "recommended_cap_formula": f"ceil_5min({ESTIMATE_MARGIN} * per_slot_"
                                       f"{'max_' if model == 'max' else ''}seconds * slots / judges "
                                       f"+ {SETUP_ALLOWANCE})"}


def run_estimate(project, configs, mechanism, check, verify, stages, commit, judges, subset, k,
                 resume_command, forced: bool = False) -> dict:
    """--estimate: judge a sample of K slots (the heaviest by olean weight, then namespace-stratified)
    one at a time, time each, print and record the estimate. Writes no receipt; the judged slots are
    cached for the real run."""
    cfgs = [(c, json.loads((project / c).read_bytes())) for c in configs]
    all_slots = list_slots(cfgs)
    sizes = olean_sizes(project)                  # after the build stage: the package's own oleans
    weigh = {s.index: slot_weight(s.name, sizes) for s in all_slots}
    if subset is not None:
        sample, heavy = subset, set()
    else:
        sample, heavy = weighted_sample(all_slots, k, {i: w for i, (w, _m) in weigh.items()})
    with tempfile.TemporaryDirectory(prefix="comparator-receipt.") as tmp:
        st = Stage04(project, configs, mechanism, check, verify, stages, pathlib.Path(tmp) / "logs",
                     resume_command, resume=False, judges=1, subset=sample, isolate=True)

        def body(stage_log: pathlib.Path):
            status, code, note = st.run(stage_log)
            return status, code, f"estimate: {note}"

        stages.run("comparator", body)
    rows, times = [], []
    for u in st.units:
        secs = st.wall.get(u.n)
        for s in u.slots:
            w, mod = weigh[s.index]
            rows.append({**s.row(), "seconds": None if secs is None else round(secs, 1),
                         "weight_bytes": w, "weight_module": mod, "heavy": s.index in heavy})
        if secs is not None:
            times.append(secs)
    out = {"schema": ESTIMATE_SCHEMA, "commit": commit, "mechanism": mechanism, "sample": rows,
           "sample_floor": sample_floor(len(all_slots)), "sample_forced": forced,
           **estimate_numbers(times, len(all_slots), judges),
           "whole_unit_warning": WHOLE_UNIT_WARNING,
           "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    path = stages.dir / ESTIMATE_FILE
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return {"estimate": out, "estimate_file": str(path), "commit": commit, "stages": str(stages.json)}


def dirty_paths(project: pathlib.Path, evidence: pathlib.Path) -> list[str]:
    """Modified or untracked paths, ignoring the stage evidence directory when it lies in the project."""
    st = git(project, "status", "--porcelain", "--untracked-files=all")
    if st.returncode != 0:
        raise RunnerError(f"{project}: not a git repository ({st.stderr.strip()})")
    try:
        ev = evidence.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        ev = None
    out = []
    for line in st.stdout.splitlines():
        path = line[3:].strip('"')
        if ev and (path == ev or path.startswith(ev + "/")):
            continue
        out.append(path)
    return out


def run(project: pathlib.Path, check: pathlib.Path, host_policy: str = "",
        mechanism: str = "auto", verify: pathlib.Path | None = None,
        evidence: pathlib.Path | None = None, prepare: bool = False,
        from_prepared: pathlib.Path | None = None, resume_command: str = "comparator-receipt",
        resume: bool = True, judges: int = 1, slot_spec: str | None = None,
        estimate: int | None = None, unit_size: int | None = None, sample_explicit: bool = False,
        force_sample: bool = False) -> dict:
    if not (project / "lean-toolchain").is_file():
        raise RunnerError(f"{project}: not a Lake project (no lean-toolchain)")
    set_redactions(project)   # #41: no checkout/HOME/TMPDIR path reaches a log or a receipt
    if not prepare and not check.is_file():
        raise RunnerError(f"comparator-check script not found: {check}")
    evidence = evidence or project / DEFAULT_EVIDENCE
    dirty = dirty_paths(project, evidence)
    if dirty:
        raise RunnerError(f"dirty tree — commit or remove before a receipt run: {', '.join(dirty[:10])}")
    commit = git(project, "rev-parse", "HEAD").stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RunnerError(f"cannot resolve HEAD in {project}")
    configs = list_configs(project)
    if not configs:
        raise RunnerError("no comparator configuration found "
                          "(comparator*.json or comparator/config*.json)")
    lint_configs(project, configs)
    toolchain = (project / "lean-toolchain").read_text().strip()
    if mechanism not in MECHANISMS:
        raise RunnerError(f"unknown mechanism {mechanism!r}; one of {MECHANISMS}")
    binding = {"commit": commit, "lean_toolchain": toolchain,
               "config_hash": config_hash(project, configs)}
    _TOOL_ENV.clear()
    _THREADS_ENV.clear()
    prior_src = from_prepared or evidence
    prior = read_stages(prior_src)
    mismatch = prepared_mismatch(prior, binding)
    runs_lake = (has_lakefile(project) and (prepare or mismatch is not None)) \
        or (not prepare and os.environ.get(FAKE_LAKE_ENV) is None)
    resolution = resolve_toolchain(project, toolchain) if runs_lake else None
    if resolution is not None:
        print(f"comparator-receipt: lake {resolution['lake']} ({resolution['source']}"
              + (f"; {resolution['version']}" if resolution["version"] else "") + ")", file=sys.stderr)
    subset = None
    if slot_spec is not None:   # refused before any setup runs
        try:
            subset = parse_slot_spec(slot_spec, list_slots(
                [(c, json.loads((project / c).read_bytes())) for c in configs]))
        except ValueError as e:
            raise RunnerError(f"--slots {slot_spec!r}: {e}; nothing written")
    forced = False
    if estimate is not None:    # the sample floor (#59), refused before any setup runs
        total = len(list_slots([(c, json.loads((project / c).read_bytes())) for c in configs]))
        floor = sample_floor(total)
        asked = len(subset) if subset is not None else estimate
        what = f"--slots selects {asked}" if subset is not None else f"--sample {asked}"
        if asked < floor and (sample_explicit or subset is not None):
            if not force_sample:
                raise UsageError(f"--estimate: {what} slot(s), below the sample floor {floor} "
                                 f"(max({ESTIMATE_MIN_SAMPLE}, ceil({ESTIMATE_MIN_FRACTION} * {total} "
                                 f"slots)), capped at the slots); a smaller sample misses heavy "
                                 f"declarations; pass --force-sample to run it anyway; nothing written")
            forced = True
            print(f"comparator-receipt: warning: --force-sample: {what} slot(s), below the sample floor "
                  f"{floor}; the estimate is recorded as sample_forced: true", file=sys.stderr)
        elif subset is None:
            estimate = min(total, max(estimate, floor))
    if not prepare:   # refusals that need no build come before the setup stages
        available = toolchain_available(project)
        if mechanism == "auto":
            mechanism = "toolchain" if available else "legacy"
        elif mechanism == "toolchain" and not available:
            raise RunnerError(f"this toolchain ({toolchain}) has no `lake comparator`; the toolchain "
                              f"mechanism needs Lean {TOOLCHAIN_FLOOR} or later (Palomar's floor); "
                              f"use --mechanism legacy")
        if mechanism == "toolchain":
            if verify is None or not verify.is_file():
                raise RunnerError(f"verify-comparator script not found: {verify}")
            if has_lakefile(project) and not manifest_has_mathlib(project) \
                    and os.environ.get(NO_MATHLIB_ENV) != "1":
                raise RunnerError("the toolchain mechanism needs a Mathlib dependency (lake-manifest.json has "
                                  "none); add it and run lake update (or, if the project knowingly has no "
                                  f"Mathlib, set {NO_MATHLIB_ENV}=1); nothing written")
            if os.environ.get(FAKE_LAKE_ENV) is None:   # real host: check the sandbox first
                bwrap_preflight()
        if mechanism == "legacy" and estimate is not None:
            raise RunnerError("--estimate times single slots, which the legacy mechanism's comparator-check.sh "
                              "cannot judge; use --mechanism toolchain; nothing written")
        if mechanism == "legacy" and slot_spec:
            raise RunnerError("--slots: the legacy mechanism's comparator-check.sh judges every configuration "
                              "in one run and cannot judge a subset; use --mechanism toolchain; nothing written")
        if mechanism == "legacy" and judges > 1:
            raise RunnerError(f"--judges {judges}: the legacy mechanism's comparator-check.sh judges every "
                              f"configuration in one run and cannot be sharded; use --judges 1 (or "
                              f"--mechanism toolchain); nothing written")
        if mechanism == "legacy" and unit_size is not None:
            raise RunnerError(f"--unit-size {unit_size}: the legacy mechanism's comparator-check.sh judges every "
                              f"configuration in one run and cannot be cut into units; use --mechanism "
                              f"toolchain; nothing written")
        print(f"mechanism: {mechanism}")
    if from_prepared is not None and mismatch:
        raise RunnerError(f"--from-prepared {from_prepared}: {mismatch}; run `comparator-receipt --prepare` "
                          f"at this commit first; nothing written")
    try:
        stack_limit, stack_lines = plan_stack(os.environ.get(STACK_ENV))
        lean_threads, thread_lines = plan_lean_threads(os.environ.get(LEAN_THREADS_ENV))
    except ValueError as e:
        raise RunnerError(f"{e}; nothing written")
    stack_lines += thread_lines
    stages = Stages(evidence, binding, prepare)
    stages.resolution = resolution
    if mismatch is None:
        when = str(prior.get("updated_at") or "")
        by = {e.get("stage"): e for e in prior.get("stages") or [] if isinstance(e, dict)}
        for s in SETUP_STAGES:
            e = by[s]
            stages.entries[s] = {**e, "status": "prepared",
                                 "note": f"setup done {when} ({e.get('status')}) in {prior_src}"}
        stages.build_provenance = carried_provenance(prior)   # #56: as recorded when it was built
        stages.clear(STAGES[3:])
        stages.save()
        if prepare:
            print(f"already prepared at {commit[:12]} ({when}); nothing to do; stages: {stages.json}")
            return {"prepared": True, "commit": commit, "stages": str(stages.json), "reused": True}
        print(f"comparator-receipt: setup reused from {prior_src} (prepared {when}); "
              f"running the comparison only", file=sys.stderr)
        print(f"comparator-receipt: {build_provenance_line(stages.build_provenance)}", file=sys.stderr)
        for line in stack_lines:              # before the comparator stage's lean children
            print(line, file=sys.stderr)
    else:
        if prior is not None:
            print(f"comparator-receipt: no reusable prepared state ({mismatch}); running setup",
                  file=sys.stderr)
        stages.clear(STAGES)
        for line in stack_lines:              # before the build stage's lean children
            print(line, file=sys.stderr)
        setup_stages(project, configs, stages)
        dirty = dirty_paths(project, evidence)
        if dirty:
            raise RunnerError(f"the setup stages changed the tree ({', '.join(dirty[:10])}); commit the "
                              f"change and rerun; nothing written (stages: {stages.json})")
    if prepare:
        print(f"prepared at {commit[:12]}: setup stages done; stages: {stages.json}")
        print("next: comparator-receipt (same commit; the setup stages are skipped)")
        return {"prepared": True, "commit": commit, "stages": str(stages.json), "reused": False}
    if estimate is not None:
        return run_estimate(project, configs, mechanism, check, verify, stages, commit, judges,
                            subset, estimate, resume_command, forced)
    with tempfile.TemporaryDirectory(prefix="comparator-receipt.") as tmp:
        log_dir = pathlib.Path(tmp) / "logs"
        result: dict = {}

        if mechanism == "toolchain":   # #60: stages.json records the pool beside the receipt
            stages.pool = {"judges": judges, "unit_size": unit_size}

        def comparator_stage(stage_log: pathlib.Path):
            st = Stage04(project, configs, mechanism, check, verify, stages, log_dir, resume_command,
                         resume=resume, judges=judges, subset=subset, unit_size=unit_size)
            result["stage04"] = st
            status, code, note = st.run(stage_log)
            result["extra"] = {"stack_limit": stack_limit, "lean_threads": lean_threads,
                               **({"build_provenance": stages.build_provenance}
                                  if stages.build_provenance is not None else {}),
                               **({"judges": judges, "unit_size": unit_size} if mechanism == "toolchain"
                                  else {}),
                               **({"toolchain_resolution": resolution} if resolution else {}),
                               **({"resumed_slots": len(st.cached)} if st.cached else {})}
            result["config_extra"] = {rel: {"units": rows} for rel, rows in st.receipt_units.items()}
            result["tools"] = (parse_pins(st.legacy_output) if mechanism == "legacy"
                               else toolchain_tools(project, verify, toolchain))
            return status, code, note

        stages.run("comparator", comparator_stage)
        tools = result["tools"]
        st = result["stage04"]
        if st.partial:
            def partial_receipt_stage(stage_log: pathlib.Path):
                result.update(write_receipt(project, st.configs, log_dir, commit, mechanism, toolchain, tools,
                                            host_policy, pathlib.Path(tmp), stages.summary(),
                                            extra={**result["extra"], "partial": True,
                                                   "slots": [s.row() for s in st.slots],
                                                   "slots_total": len(st.all_slots)},
                                            config_extra=result["config_extra"], dest=stages.dir))
                stage_log.write_text(f"wrote the PARTIAL receipt {result['receipt']} (diagnostic only)\n",
                                     encoding="utf-8")
                return "ok", 0, f"partial receipt in {stages.dir.name}/ (diagnostic only)"

            stages.run("receipt", partial_receipt_stage)
            return {"receipt": result["receipt"], "commit": commit, "configs": len(st.configs),
                    "mechanism": mechanism, "stages": str(stages.json), "partial": True,
                    "slots": len(st.slots), "slots_total": len(st.all_slots)}

        def receipt_stage(stage_log: pathlib.Path):
            result.update(write_receipt(project, configs, log_dir, commit, mechanism, toolchain, tools,
                                        host_policy, pathlib.Path(tmp), stages.summary(),
                                        extra=result["extra"], config_extra=result["config_extra"]))
            stage_log.write_text(f"wrote {result['receipt']} and {len(configs)} log(s)\n", encoding="utf-8")
            return "ok", 0, f".lean-receipts/{commit[:12]}-comparator.json"

        stages.run("receipt", receipt_stage)
    return {"receipt": result["receipt"], "commit": commit, "configs": len(configs),
            "mechanism": mechanism, "stages": str(stages.json)}


def write_receipt(project: pathlib.Path, configs: list[str], log_dir: pathlib.Path, commit: str,
                  mechanism: str, toolchain: str, tools: dict, host_policy: str, tmp: pathlib.Path,
                  stage_summary: list[dict], extra: dict | None = None,
                  config_extra: dict | None = None, dest: pathlib.Path | None = None) -> dict:
    """The receipt and its logs, all or nothing, into <project>/.lean-receipts/ -- or, for a partial
    (--slots) run, into dest (the stage directory, never the repository), suffixed `.partial`."""
    partial = dest is not None
    sfx = ".partial" if partial else ""
    entries = []
    staged: list[tuple[pathlib.Path, pathlib.Path]] = []
    for cfg_rel in configs:
        name = cfg_name(cfg_rel)
        src_log = log_dir / f"{name}.log"
        if not src_log.is_file():
            raise RunnerError(f"no log for {cfg_rel} at {src_log}; nothing written")
        log_text = src_log.read_text(encoding="utf-8", errors="replace")
        accepted = comparator_pass(log_text, mechanism)
        if not accepted:
            raise RunnerError(f"{cfg_rel}: log lacks the PASS line; nothing written")
        cfg = json.loads((project / cfg_rel).read_bytes())
        log_rel = f".lean-receipts/{commit[:12]}-comparator-{name}.log"
        if partial:
            log_rel = f"{commit[:12]}-comparator-{name}{sfx}.log"
        red = tmp / f"redacted-{name}.log"            # #41: the committed log never names host paths
        red.write_bytes(redact_bytes(src_log.read_bytes()))
        src_log = red
        staged.append((src_log, (dest / log_rel) if partial else (project / log_rel)))
        entries.append({
            "path": cfg_rel,
            "sha256": sha256_file(project / cfg_rel),
            "challenge_module": cfg.get("challenge_module"),
            "solution_module": cfg.get("solution_module"),
            "theorem_names": list(cfg.get("theorem_names") or []),
            "definition_names": list(cfg.get("definition_names") or []),
            "permitted_axioms": list(cfg.get("permitted_axioms") or []),
            **({"judged_as": cfg["judged_as"]} if isinstance(cfg.get("judged_as"), dict) else {}),
            "log": log_rel,
            "log_sha256": sha256_file(src_log),
            "result": "pass",
            **((config_extra or {}).get(cfg_rel) or {}),
        })
    label = host_label()
    host = label or socket.gethostname()
    now = dt.datetime.now(dt.timezone.utc)
    job = (os.environ.get("SLURM_JOB_ID")
           or f"local-{host}-{os.getpid()}-{now.strftime('%Y%m%dT%H%M%SZ')}")
    receipt = {
        "schema": SCHEMA, "mechanism": mechanism, "commit": commit,
        "lean_toolchain": toolchain, "tools": tools, "host": host,
        **({"test_only": True} if os.environ.get(FAKE_LAKE_ENV) is not None else {}), "host_policy": host_policy, "job_id": job,
        "generated_at": now.isoformat(timespec="seconds"),
        "result": "pass", "configs": entries, "stages": stage_summary, **(extra or {}),
        **({"host_label": label} if label else {}),
    }
    receipt = redact_json(receipt)    # #41: <checkout>/, <home>/, <tmp>/ instead of host paths
    rec_tmp = tmp / "receipt.json"
    rec_tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    rec_dir = dest if partial else project / ".lean-receipts"
    rec_path = rec_dir / f"{commit[:12]}-comparator{sfx}.json"
    staged.append((rec_tmp, rec_path))
    # Everything passed: now, and only now, write into the repo — all or nothing.
    created_dir = not rec_dir.exists()
    written: list[pathlib.Path] = []
    try:
        rec_dir.mkdir(exist_ok=True)
        for src, dst in staged:
            fd, part_s = tempfile.mkstemp(dir=rec_dir, prefix=dst.name + ".",
                                          suffix=".tmp")
            os.close(fd)
            part = pathlib.Path(part_s)
            written.append(part)
            shutil.copy2(src, part)
            os.replace(part, dst)
            written[-1] = dst
    except OSError as e:
        for w in written:
            try:
                w.unlink()
            except OSError:
                pass
        if created_dir:
            try:
                rec_dir.rmdir()
            except OSError:
                pass
        raise RunnerError(f"could not write evidence into .lean-receipts/ ({e}); "
                          f"nothing written")
    return {"receipt": str(rec_path)}


HOST_LABEL_ENV = "COMPARATOR_RECEIPT_HOST_LABEL"


def host_label() -> str:
    """The label that replaces the host name in the receipt and its output (--host-label or the env)."""
    return os.environ.get(HOST_LABEL_ENV, "").strip()


def resume_command(argv: list[str]) -> str:
    """The command that resumes this run: the same arguments; completed setup and slots are reused."""
    import shlex
    return shlex.join(["comparator-receipt", *[a for a in argv if a != "--no-resume"]])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="comparator-receipt",
        description="run comparator-check.sh and write a comparator receipt")
    ap.add_argument("--project", default=".",
                    help="Lake project directory (default: cwd)")
    ap.add_argument("--check",
                    default=os.environ.get("COMPARATOR_CHECK") or None,
                    help="comparator-check.sh to run (env COMPARATOR_CHECK; "
                         "default: the copy shipped with lean-pkg)")
    ap.add_argument("--mechanism", choices=MECHANISMS, default="auto",
                    help="toolchain: `lake comparator` with the toolchain's bundled kernels "
                         f"(Lean {TOOLCHAIN_FLOOR}+, what Palomar runs); legacy: the pinned "
                         "comparator/lean4export/nanoda/landrun stack; auto (default): toolchain "
                         "when available, else legacy")
    ap.add_argument("--verify", default=os.environ.get(VERIFY_ENV) or None,
                    help="verify-comparator.sh for the toolchain mechanism (env COMPARATOR_VERIFY; "
                         "default: the vendored PalomarTemplate copy)")
    ap.add_argument("--evidence-dir", default=None, metavar="DIR",
                    help="where the stage logs and stages.json go, kept on failure "
                         f"(default: <project>/{DEFAULT_EVIDENCE.as_posix()}; its {STAGES_DIR}/ subdirectory)")
    ap.add_argument("--prepare", action="store_true",
                    help="run only the setup stages (dependency checkout, Mathlib cache, build of the "
                         "configured modules); idempotent; a later run at the same commit, toolchain and "
                         "config hash skips them and runs only the comparison")
    ap.add_argument("--from-prepared", default=None, metavar="DIR",
                    help="reuse the completed setup recorded in DIR (an evidence directory or its "
                         "stages.json); refuses when it was prepared for another commit/toolchain/config")
    ap.add_argument("--no-resume", action="store_true",
                    help="judge every slot again, ignoring the slots this commit and config hash already "
                         f"accepted (cached under <evidence-dir>/{STAGES_DIR}/{SLOTS_DIR}/)")
    ap.add_argument("--judges", default=None, metavar="N",
                    help=f"run up to N judge processes at once, each on a contiguous shard of every "
                         f"configuration's theorem_names (toolchain mechanism; env {JOBS_ENV}; default 1); "
                         f"with --unit-size, N is only how many units run at once. "
                         f"The merged verdict is exact; see docs/comparator.md (Runtime) for when it helps")
    ap.add_argument("--unit-size", default=None, metavar="K",
                    help=f"judge each configuration in contiguous units of at most K names (toolchain "
                         f"mechanism; env {UNIT_SIZE_ENV}; default: the whole configuration, cut into "
                         f"--judges shards). --unit-size 1 --judges 1 judges each name alone, one at a time, "
                         f"so a capped run resumes per name")
    ap.add_argument("--slots", default=None, metavar="SPEC",
                    help="judge only these slots (theorem_names entries): comma-separated names, "
                         "<config>:<name>, 1-based indices or ranges i-j over all configurations, or "
                         "sample:K (a namespace-stratified sample). Writes a PARTIAL receipt into "
                         "the stage directory, marked partial: true: diagnostic only, refused as evidence")
    ap.add_argument("--estimate", action="store_true",
                    help="after setup, judge a sample of slots (the heaviest by olean size, then "
                         "namespace-stratified) one at a time and print an estimate {per_slot_seconds, "
                         "slots, estimated_seconds, recommended_cap}; writes no receipt (the judged slots "
                         "are cached for the real run)")
    ap.add_argument("--sample", type=int, default=None, metavar="K",
                    help=f"--estimate: sample size (default and floor: max({ESTIMATE_MIN_SAMPLE}, "
                         f"ceil({ESTIMATE_MIN_FRACTION} * slots)), capped at the slots); --slots "
                         f"overrides the sample")
    ap.add_argument("--force-sample", action="store_true",
                    help="--estimate: accept a --sample (or --slots) below the floor; the estimate warns "
                         "and records sample_forced: true")
    ap.add_argument("--json", action="store_true", help="--estimate: print the estimate as JSON")
    ap.add_argument("--host-label", default=None, metavar="LABEL",
                    help=f"name the host LABEL instead of its host name in the receipt (host, host_label, "
                         f"job_id, host_policy) and the output (env {HOST_LABEL_ENV})")
    args = ap.parse_args(argv)
    if args.host_label is not None:
        if not args.host_label.strip():
            print("comparator-receipt: --host-label must not be empty", file=sys.stderr)
            return 2
        os.environ[HOST_LABEL_ENV] = args.host_label.strip()
    if (args.sample is not None or args.json or args.force_sample) and not args.estimate:
        print("comparator-receipt: --sample, --force-sample and --json go with --estimate", file=sys.stderr)
        return 2
    if args.sample is not None and args.sample < 1:
        print("comparator-receipt: --sample must be a positive integer", file=sys.stderr)
        return 2
    if args.estimate and args.prepare:
        print("comparator-receipt: --estimate and --prepare are exclusive", file=sys.stderr)
        return 2
    raw_jobs = args.judges if args.judges is not None else (os.environ.get(JOBS_ENV) or "1")
    try:
        judges = int(raw_jobs)
        if judges < 1:
            raise ValueError
    except ValueError:
        src = "--judges" if args.judges is not None else JOBS_ENV
        print(f"comparator-receipt: {src} must be a positive integer, got {raw_jobs!r}", file=sys.stderr)
        return 2
    raw_size = args.unit_size if args.unit_size is not None else (os.environ.get(UNIT_SIZE_ENV) or None)
    unit_size = None
    if raw_size is not None:   # #60: refused before any work
        try:
            unit_size = int(raw_size)
            if unit_size < 1:
                raise ValueError
        except ValueError:
            src = "--unit-size" if args.unit_size is not None else UNIT_SIZE_ENV
            print(f"comparator-receipt: {src} must be a positive integer, got {raw_size!r}", file=sys.stderr)
            return 2
    if args.prepare and args.from_prepared:
        print("comparator-receipt: --prepare and --from-prepared are exclusive", file=sys.stderr)
        return 2
    if (os.environ.get(STACK_ENV) or "").strip():
        try:
            parse_stack(os.environ[STACK_ENV])
        except ValueError as e:
            print(f"comparator-receipt: {e}", file=sys.stderr)
            return 2
    if (os.environ.get(LEAN_THREADS_ENV) or "").strip():
        try:
            parse_lean_threads(os.environ[LEAN_THREADS_ENV])
        except ValueError as e:
            print(f"comparator-receipt: {e}", file=sys.stderr)
            return 2
    ok, why = host_policy_ok(os.environ.get("SLURM_JOB_ID"),
                             os.environ.get(ALLOW_HOST_ENV) == "1",
                             hostname=socket.gethostname(),
                             allowed_hosts=parse_allowed_hosts(
                                 os.environ.get(ALLOWED_HOSTS_ENV)))
    if not ok:
        print(f"comparator-receipt: refusing: {why}", file=sys.stderr)
        return 1
    if os.environ.get(FAKE_LAKE_ENV) is None and not args.prepare:   # the host check (#22.5); never a refusal
        hc = host_checks()
        print(f"comparator-receipt: host-check: {hc['verdict']}", file=sys.stderr)
    label = host_label()
    if label:
        # the label replaces the host name everywhere a receipt or its output names the host
        why = why.replace(socket.gethostname(), label)
    try:
        project = pathlib.Path(args.project).resolve()
        evidence = pathlib.Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else None
        prepared = pathlib.Path(args.from_prepared).expanduser().resolve() if args.from_prepared else None
        with importlib.resources.as_file(default_check_script()) as check_default, \
                importlib.resources.as_file(default_verify_script()) as verify_default:
            check = pathlib.Path(args.check).resolve() if args.check else pathlib.Path(check_default).resolve()
            verify = pathlib.Path(args.verify).resolve() if args.verify else pathlib.Path(verify_default).resolve()
            res = run(project, check, host_policy=why, mechanism=args.mechanism, verify=verify,
                      evidence=evidence, prepare=args.prepare, from_prepared=prepared,
                      resume_command=resume_command(argv if argv is not None else sys.argv[1:]),
                      resume=not args.no_resume, judges=judges, slot_spec=args.slots,
                      estimate=(args.sample or ESTIMATE_MIN_SAMPLE) if args.estimate else None,
                      unit_size=unit_size, sample_explicit=args.sample is not None,
                      force_sample=args.force_sample)
    except RunnerError as e:
        print(f"comparator-receipt: {e}", file=sys.stderr)
        if isinstance(e, UsageError):
            return 2
        code = getattr(e, "code", None)
        return code if isinstance(e, PartialStage) and isinstance(code, int) else 1
    if res.get("prepared"):
        return 0
    if res.get("estimate"):
        e = res["estimate"]
        if args.json:
            print(json.dumps(e, indent=2))
            return 0
        print(f"estimate ({len(e['sample'])} sampled slot(s), each judged alone; no receipt written):")
        if e["sample_forced"]:
            print(f"WARNING: --force-sample: {len(e['sample'])} sampled slot(s), below the sample floor "
                  f"{e['sample_floor']}; heavy declarations may be missed")
        for row in e["sample"]:
            mark = f"; weight {row['weight_bytes']} B ({row['weight_module']})" if row["weight_module"] else ""
            print(f"  slot {row['slot']} {row['name']}: {row['seconds']}s"
                  f"{' [heavy]' if row['heavy'] else ''}{mark}")
        print(f"per_slot_seconds {e['per_slot_seconds']} (min {e['per_slot_min_seconds']}, max "
              f"{e['per_slot_max_seconds']}); slots {e['slots']}; judges {e['judges']}")
        print(f"estimated_seconds {e['estimated_seconds']} (per-slot mean model); estimated_seconds_max "
              f"{e['estimated_seconds_max']} (per-slot max model); lower_bound_seconds "
              f"{e['lower_bound_seconds']}")
        why = ("the sample covers less than half the slots" if e["cap_model"] == "max"
               else "the sample covers at least half the slots")
        print(f"recommended_cap {e['recommended_cap']} s = {e['recommended_cap_formula']} "
              f"(cap_model {e['cap_model']}: {why})")
        print(f"whole_unit_warning: {e['whole_unit_warning']}")
        print(f"estimate: {res['estimate_file']}")
        return 0
    if res.get("partial"):
        print(f"PARTIAL comparator receipt written: {res['receipt']} ({res['slots']} of "
              f"{res['slots_total']} slot(s), commit {res['commit'][:12]})")
        print("it is diagnostic only: marked partial: true, kept out of the repository, and refused as "
              "evidence by `lean-pkg comparator`, `gate --comparator` and `promote`")
        print(f"the judged slots are cached: a full run at this commit skips them (stage logs: {res['stages']})")
        return 0
    print(f"comparator receipt written: {res['receipt']} ({res['configs']} config(s), "
          f"commit {res['commit'][:12]}, host policy: {why})")
    print(f"stage logs: {res['stages']}")
    print("commit .lean-receipts/ in this repo, then record it:")
    print(f"  lean-pkg comparator <name> --receipt {res['receipt']}")
    print(f"  (or at gate time: lean-pkg gate <name> --receipt R.json "
          f"--comparator {res['receipt']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
