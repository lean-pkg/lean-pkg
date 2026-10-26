# comparator-receipt -- run comparator-check.sh on a Lake project and write
# the comparator receipt that `lean-pkg gate --comparator` / `lean-pkg
# comparator` consume. Writes NOTHING unless every comparator configuration
# passed, so a failed run can never be mistaken for evidence.
"""Usage (from a Lake project directory):

    comparator-receipt [--repo DIR] [--check SCRIPT] [--mechanism M]
                       [--evidence-dir DIR] [--prepare | --from-prepared DIR]

Stages (#26.1): checkout, cache-get, build (the setup stages), comparator, receipt; each logs to
<evidence-dir>/comparator-stages/<NN>-<stage>.log with stages.json beside it, kept on failure.
--prepare runs only the setup stages; a later run with the same commit, toolchain and config hash
skips them.
The setup ends by hashing the package's own compiled modules (comparator-stages/03-build.oleans.sha256,
stages.json olean_manifest); a reused setup whose oleans changed is refused, and the comparator stage fails
when they change during the comparison. `comparator-receipt --verify-oleans [DIR] [--json]` is the read-only
check (exit 0 same, 1 different or not recorded).
A run that replaces an earlier run's stage files first keeps them, read-only, as
comparator-stages/attempts/NNN/ (numbers never reused; never deleted or read back by lean-pkg);
`comparator-receipt --attempts [DIR] [--json]` lists them.

Host policy: the run is allowed iff SLURM_JOB_ID is set (inside a Slurm job),
OR this host's name or its label is listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS
(comma-separated), OR COMPARATOR_RECEIPT_ALLOW_HOST=1, OR (the workstation
default) the allow-list is unset, COMPARATOR_RECEIPT_WORKSTATION_DEFAULT is not 0
and this machine has no Slurm (no sbatch/srun/slurmd on PATH, no slurm.conf, no
SLURM_CONF): the machine running the command, named by its label. The reason is
recorded in the receipt's host_policy field. SLURM_JOB_ID is any shell's to set: a Slurm
claim backs a coverage.yaml compute job only after `--seal-sacct RECEIPT` sealed
the job's sacct record into it (receipt.seal_comparator_sacct).

Host label: the receipt never names the host. `host`, `host_label`, the host in
`job_id` and `host_policy` (and in COMPARATOR_RECEIPT_BUILD_SOURCE) are the label: the
operator's --host-label / COMPARATOR_RECEIPT_HOST_LABEL, else `h-` and 12 hex digits
of the SHA-256 of the lower-cased host name (receipt_systemd.host_label, the label
`receipt systemd` publishes).

Env: COMPARATOR_CHECK (script path; default: the comparator-check.sh shipped
inside the lean_pkg package), COMPARATOR_LAKE (the lake the setup stages run), SLURM_JOB_ID (job identity when under Slurm),
COMPARATOR_RECEIPT_STACK (unlimited | a size in kB: the soft stack limit for the lake/lean children),
COMPARATOR_RECEIPT_BUILD_SOURCE (free text naming where the package's oleans were built; recorded in
build_provenance), COMPARATOR_RECEIPT_LEAN_THREADS (a positive integer: LEAN_NUM_THREADS for the lake/lean
children), COMPARATOR_RECEIPT_NO_MATHLIB (1: the project knowingly has no Mathlib; the toolchain mechanism
refuses such a project otherwise, and with it verify-comparator.sh skips `lake exe cache get`),
APPTAINER_CONTAINER / SINGULARITY_CONTAINER (set by the runtime inside Apptainer / Singularity: when it names a
SIF file readable here, the receipt gains the additive `container` {engine, image_sha256, ref}, the file hashed
when the receipt is written; otherwise, and under an OCI runtime, the key is absent; #236, container.py).
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
from .gate import build_provenance_line  # noqa: E402
from .core import comparator_pass  # noqa: E402
from . import container as ctr  # noqa: E402
from . import lake_hygiene as lh  # noqa: E402
# Every lean/lake/git/bash below runs without the repository-selecting Git variables (#27).
from .core import GIT_REPO_ENV, git_env_set, scrubbed_env  # noqa: E402,F401
from .json_stdout import chatter_to_stderr  # noqa: E402
from .holes import SIDECAR, check_definition_holes, parse_sidecar  # noqa: E402
from .comparator_stage import redact, redact_bytes, redact_json, set_redactions  # noqa: E402
from .receipt_systemd import host_label as hashed_host_label  # noqa: E402
from .comparator_stage import (PARTIAL_FILE, SLOTS_DIR, UNITS_DIR, Interrupted, Judge,  # noqa: E402
                               JOBS_ENV, UNIT_SIZE_ENV, Progress, SignalGuard, SlotCache, StageLog, Unit, chunks, list_slots, parse_slot_spec, ranges, run_pool, shards, unit_id, weighted_sample)
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
        raise RunnerError("bwrap is not installed; `lake comparator` needs it (package bubblewrap)") from None
    except subprocess.TimeoutExpired:
        raise RunnerError("bwrap did not answer within 60 s; `lake comparator` needs a working bwrap") from None
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
        raise RunnerError(f"cannot run `{lake} --version` ({e}); nothing written") from e
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
# inside a Slurm job, on a host the operator lists explicitly, or (the
# workstation default) on the machine running the command when it has no Slurm
# and no allow-list is set: a solo author's own computer. No host or user names
# are built in. COMPARATOR_RECEIPT_ALLOW_HOST=1 overrides for tests
# and explicitly authorized runs; the reason is recorded in the receipt's
# host_policy field.

ALLOWED_HOSTS_ENV = "COMPARATOR_RECEIPT_ALLOWED_HOSTS"
ALLOW_HOST_ENV = "COMPARATOR_RECEIPT_ALLOW_HOST"
WORKSTATION_ENV = "COMPARATOR_RECEIPT_WORKSTATION_DEFAULT"   # 0: no workstation default (an operator's opt-out)
# A machine with a Slurm client or daemon is treated as a cluster node (a login node runs no heavy job), never
# as a workstation.
SLURM_PROGRAMS = ("sbatch", "srun", "slurmd", "slurmctld")
SLURM_CONFIGS = ("/etc/slurm/slurm.conf", "/etc/slurm-llnl/slurm.conf", "/usr/local/etc/slurm.conf")


def slurm_here() -> str | None:
    """What makes this machine a Slurm cluster node (a program on PATH, a config file, SLURM_CONF), else None."""
    if os.environ.get("SLURM_CONF", "").strip():
        return "SLURM_CONF is set"
    for prog in SLURM_PROGRAMS:
        if shutil.which(prog):
            return f"{prog} is on PATH"
    for conf in SLURM_CONFIGS:
        if os.path.exists(conf):
            return f"{conf} exists"
    return None


def workstation_default(env=None) -> tuple[bool, str]:
    """(applies, why): the workstation default allows the machine running the command, by its label, when
    nothing narrower was said: no allow-list in the environment (set at all, even empty, it is the operator's
    list), no opt-out, and no Slurm on this machine."""
    env = os.environ if env is None else env
    if ALLOWED_HOSTS_ENV in env:
        return False, f"{ALLOWED_HOSTS_ENV} is set (the allow-list decides)"
    if str(env.get(WORKSTATION_ENV, "")).strip() == "0":
        return False, f"{WORKSTATION_ENV}=0"
    slurm = slurm_here()
    if slurm:
        return False, f"this machine is a Slurm node ({slurm})"
    return True, "no Slurm on this machine and no allow-list set"


def parse_allowed_hosts(value: str | None) -> set[str]:
    """Comma-separated host names or labels; blanks and surrounding spaces ignored."""
    return {h.strip() for h in (value or "").split(",") if h.strip()}


def host_policy_ok(slurm_job: str | None, allow: bool,
                   hostname: str | None = None,
                   allowed_hosts: set[str] | None = None,
                   label: str | None = None, workstation: bool = False) -> tuple[bool, str]:
    """(allowed, reason). An allow-list entry may be the host's name (any case) or its label (the hashed
    `h-...` label or the operator's --host-label); the reason names the label only, never the name.
    `workstation` (workstation_default): with no allow-list, the machine running the command is allowed."""
    hostname = hostname or socket.gethostname()
    label = label or published_host(hostname)
    allowed_hosts = allowed_hosts or set()
    if allow:
        return True, f"allowed by {ALLOW_HOST_ENV}=1"
    if slurm_job:
        return True, f"Slurm job {slurm_job} on {label}"
    names = {hostname.strip().lower(), label, hashed_host_label(hostname)}
    if any(h.lower() in names or h in names for h in allowed_hosts):
        return True, f"host {label} listed in {ALLOWED_HOSTS_ENV}"
    if workstation and not allowed_hosts:
        return True, (f"host {label}: the machine running the command (workstation default: no Slurm here, "
                      f"{ALLOWED_HOSTS_ENV} unset)")
    return False, (f"this host ({label}) is not an allowed host: run inside a Slurm job "
                   f"(SLURM_JOB_ID set), list this host's name or its label {label} in {ALLOWED_HOSTS_ENV} "
                   f"(comma-separated), or set {ALLOW_HOST_ENV}=1 for an "
                   f"explicitly authorized run")

# ---------------------------------------------------------------------------


def sha256_file(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cfg_name(path: str) -> str:
    """Same rule as comparator-check.sh: strip .json, '/' -> '_'. Not injective
    (`comparator_config_a.json` and `comparator/config_a.json` both give `comparator_config_a`);
    cfg_name_collision refuses such a pair rather than renaming: committed receipts use these names."""
    base = path[:-5] if path.endswith(".json") else path
    return base.replace("/", "_")


def cfg_name_collision(configs: list[str]) -> tuple[str, str, str] | None:
    """(first, second, name) for the first two configurations whose logs would share a name, else None."""
    seen: dict[str, str] = {}
    for rel in configs:
        name = cfg_name(rel)
        if name in seen:
            return seen[name], rel, name
        seen[name] = rel
    return None


def parse_pins(text: str) -> dict:
    pins = {PIN_KEYS[m.group(1)]: m.group(2) for m in PIN_RE.finditer(text)}
    missing = [k for k in ("comparator", "lean4export", "nanoda", "landrun")
               if k not in pins]
    if missing:
        raise RunnerError(f"comparator-check output lacks tool pins for {missing}")
    return pins


STANDARD_AXIOMS = frozenset({"propext", "Quot.sound", "Classical.choice"})


def lint_configs(project: pathlib.Path, configs: list[str], warn: bool = True) -> dict:
    """Configuration-level rules, checked before anything is built so a refusal
    writes nothing: permitted_axioms within the standard three, and definition
    holes carrying a human sign-off (lean-pkg-signoff.json, else the deprecated inline judged_as; see
    holes.py), and no two configurations whose log names (cfg_name) coincide: one log would overwrite the
    other. Returns {config: sign-off} for the configurations with definition holes (recorded in the receipt)."""
    clash = cfg_name_collision(configs)
    if clash:
        a, b, name = clash
        raise RunnerError(f"{a} and {b} both map to the log name {name}.log (the '/' becomes '_'), so one "
                          f"configuration's log would overwrite the other's; rename one of them; nothing written")
    sidecar = read_sidecar(project)
    signoffs: dict = {}
    for cfg_rel in configs:
        raw = (project / cfg_rel).read_bytes()
        try:
            cfg = json.loads(raw)
        except ValueError as e:
            raise RunnerError(f"{cfg_rel}: not valid JSON ({e}); nothing written") from e
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
            so = check_definition_holes(cfg, cfg_rel, cfg_rel=cfg_rel, cfg_bytes=raw, sidecar=sidecar,
                                        warn=(lambda m: print(f"comparator-receipt: warning: {m}", file=sys.stderr))
                                        if warn else None)
        except ValueError as e:
            raise RunnerError(f"{e}; nothing written") from e
        if so is not None:
            signoffs[cfg_rel] = so
    return signoffs


def read_sidecar(project: pathlib.Path) -> dict:
    """lean-pkg-signoff.json of the project (holes.py), strictly parsed; empty when absent."""
    p = project / SIDECAR
    if p.is_symlink():
        raise RunnerError(f"{SIDECAR} is a symlink; refusing to read a sign-off through it; nothing written")
    try:
        return parse_sidecar(p.read_bytes() if p.is_file() else None)
    except ValueError as e:
        raise RunnerError(f"{e}; nothing written") from e


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
# Only Lake's own `[i/n] Built <target>` lines witness a compilation: compiled = any; modules_built = the
# distinct `Built` targets. Lake's artifact cache can write an olean without compiling it (`Reused`,
# `Replayed`, `Unpacked`; lake_hygiene), so the mtimes of the root package's own oleans
# (<project>/.lake/build/lib/**/*.olean; dependencies under .lake/packages/ are not the package's modules),
# snapshot before and after the stage, only tell what was written: an olean that is new or changed with no
# `Built` line for its module counts as `restored` (additive key, present when non-zero), and the distinct
# `Reused` / `Replayed` / `Unpacked` targets with no earlier `Built` line as `cache_reused` (likewise). prebuilt = the stage ran, passed
# and compiled nothing. COMPARATOR_RECEIPT_BUILD_SOURCE declares where a prebuilt tree came from (free text,
# redacted; this host's name in it becomes its label).
BUILD_SOURCE_ENV = "COMPARATOR_RECEIPT_BUILD_SOURCE"
BUILT_LINE_RE = re.compile(r"\[\d+/\d+\](?:\s+\(Optional\))?\s+Built\s+([^\s(]+)")


def build_source() -> str | None:
    v = (os.environ.get(BUILD_SOURCE_ENV) or "").strip()
    return replace_host(redact(v)) if v else None


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
    """{module name: olean size in bytes} for the root package's oleans (A/B/C.olean -> A.B.C). Lake
    writes them under .lake/build/lib/lean/ (older Lake: .lake/build/lib/); the `lean/` component is
    the layout, not part of the module name."""
    out: dict[str, int] = {}
    for rel, st in root_oleans(project):
        if rel.startswith("lean/"):
            rel = rel[len("lean/"):]
        out[rel[:-len(".olean")].replace("/", ".")] = st.st_size
    return out


_DECL_RE = re.compile(
    r"^[ \t]*(?:@\[[^\]]*\][ \t]*)?(?:(?:private|protected|noncomputable|nonrec|partial|unsafe|scoped)"
    r"[ \t]+)*(?:theorem|lemma|def|abbrev|instance|structure|inductive|class|opaque|axiom|example)"
    r"[ \t]+([^\s(:{\[]+)", re.M)
_NS_RE = re.compile(r"^[ \t]*(?:(?:noncomputable|public)[ \t]+)*(namespace|section|mutual|end)\b[ \t]*([^\s-]*)",
                    re.M)


def source_declarations(project: pathlib.Path, sizes: dict[str, int]) -> dict[str, str]:
    """{full declaration name: module} from the package's own sources, for the modules in sizes (a
    file X/Y/Z.lean outside hidden directories such as .lake whose dotted path ends in a module name:
    srcDir and subdirectories need no Lake configuration). Tracks `namespace`/`section`/`mutual`/`end`
    only; a syntactic scan, so a name it cannot see (macro-generated, `export`) stays unweighted."""
    if not sizes:
        return {}
    files: dict[str, pathlib.Path] = {}
    sources = []
    for d, dirs, names in os.walk(project):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x != "lake-packages")
        sources += [pathlib.Path(d) / n for n in sorted(names) if n.endswith(".lean")]
    for p in sources:
        rel = p.relative_to(project).parts
        parts = list(rel[:-1]) + [rel[-1][:-len(".lean")]]
        for i in range(len(parts)):
            mod = ".".join(parts[i:])
            if mod in sizes:
                files.setdefault(mod, p)
                break
    out: dict[str, str] = {}
    for mod, p in files.items():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = re.sub(r"/-.*?-/", "", text, flags=re.S)      # block and doc comments
        events = sorted([(m.start(), "ns", m.group(1), m.group(2)) for m in _NS_RE.finditer(text)]
                        + [(m.start(), "decl", m.group(1), "") for m in _DECL_RE.finditer(text)])
        stack: list[tuple[str, str]] = []          # (kind, name)
        for _pos, kind, a, b in events:
            if kind == "ns":
                if a in ("namespace", "section", "mutual"):
                    stack.append((a, b))
                elif stack:
                    stack.pop()
                continue
            name = a[len("_root_."):] if a.startswith("_root_.") else \
                ".".join([n for k, n in stack if k == "namespace" and n] + [a])
            out.setdefault(name, mod)
    return out


def slot_weight(name: str, sizes: dict[str, int],
                decls: dict[str, str] | None = None) -> tuple[int, str | None]:
    """--estimate's weight for a declaration (#59): the size of the olean of the module whose source
    declares the name (decls, from source_declarations; this covers a theorem whose namespace is not
    its module path), else of the module named by the longest proper namespace prefix of the name that
    is one of the package's modules (A.B.C.thm tries A.B.C, then A.B, then A); (0, None) when neither
    finds one. A proxy: a bigger olean marks a heavier module, whose declarations tend to pull a
    bigger closure."""
    mod = (decls or {}).get(name)
    if mod is not None and mod in sizes:
        return sizes[mod], mod
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
    jobs = lh.job_lines(log_text)
    built = {t for v, t in jobs if v == "Built"}
    reused = {t for _, t in lh.restored_jobs(jobs)}     # a Replayed after its own Built is not a restore
    # an olean path under .lake/build/lib/ (lean/A/B.olean) is module A.B
    restored = [k for k in written
                if k.removeprefix("lean/").removesuffix(".olean").replace("/", ".") not in built]
    compiled = bool(built)
    bp = {"compiled": compiled, "modules_built": len(built), "prebuilt": not compiled, "source": source}
    if reused:
        bp["cache_reused"] = len(reused)
    if restored:
        bp["restored"] = len(restored)
    return bp


def carried_provenance(prior: dict | None) -> dict:
    """The provenance a reused setup recorded; all-null when it predates build_provenance. The
    declared source of this run fills a recorded null source."""
    bp = (prior or {}).get("build_provenance")
    bp = dict(bp) if isinstance(bp, dict) else {"compiled": None, "modules_built": None, "prebuilt": None,
                                                 "source": None}
    if bp.get("source") is None:
        bp["source"] = build_source()
    return bp


# Olean manifest. build_provenance says whether the build compiled; it does not bind the bytes the judge then
# reads. The judge (lean4export and the kernel replay) consumes the root package's compiled modules under
# <project>/.lake/build/lib/ (git-ignored, so the clean-tree check cannot see them): a module copied in from
# another host, or rewritten between `--prepare` and the comparison, would change the judged environment with
# HEAD, lean-toolchain and the configurations unchanged. The setup stages therefore end by hashing every such
# file (`*.olean`, `*.olean.server`, `*.olean.private`; regular files only) into
# comparator-stages/03-build.oleans.sha256 (sorted `<path under .lake/build/lib> <size> <sha256>` lines) and
# record its digest, with the lean-toolchain and lake-manifest.json digests, as stages.json `olean_manifest`.
# The comparator stage rehashes before the first judge starts and again after the last one exits; any
# difference fails the stage, nothing written. The digest also keys the per-slot cache, so a slot accepted
# against other oleans is judged again. Dependencies under .lake/packages/ are not the package's own modules
# and are not hashed (lake-manifest.json pins their commits). `--verify-oleans [DIR]` is the read-only check.
OLEAN_MANIFEST_FILE = "03-build.oleans.sha256"
OLEAN_MANIFEST_SCHEMA = "olean-manifest/1"
OLEAN_SUFFIXES = (".olean", ".olean.server", ".olean.private")


def olean_manifest_text(project: pathlib.Path) -> bytes:
    """The manifest lines of the root package's compiled modules; a symlink or a non-regular file among them
    is refused (the manifest binds regular files only)."""
    root = project / ".lake" / "build" / "lib"
    rows, odd = [], []
    if root.is_dir():
        for d, dirs, names in os.walk(root):
            dirs.sort()
            for n in sorted(names):
                if not n.endswith(OLEAN_SUFFIXES):
                    continue
                p = pathlib.Path(d) / n
                rel = p.relative_to(root).as_posix()
                if p.is_symlink() or not p.is_file():
                    odd.append(rel)
                    continue
                h = hashlib.sha256()
                with open(p, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        h.update(chunk)
                rows.append(f"{rel} {p.stat().st_size} {h.hexdigest()}\n")
    if odd:
        raise RunnerError(f"compiled module(s) under .lake/build/lib that are not regular files: "
                          f"{', '.join(odd[:5])}{' …' if len(odd) > 5 else ''}; the olean manifest binds regular "
                          f"files only (replace each symlink with a copy of its target); nothing written")
    return "".join(sorted(rows)).encode()


def olean_manifest(project: pathlib.Path, toolchain: str, captured: str) -> tuple[dict, bytes]:
    """(stages.json / receipt `olean_manifest`, manifest bytes)."""
    text = olean_manifest_text(project)
    lm = project / "lake-manifest.json"
    return ({"schema": OLEAN_MANIFEST_SCHEMA, "files": text.count(b"\n"),
             "sha256": hashlib.sha256(text).hexdigest(), "lean_toolchain": toolchain,
             "lake_manifest_sha256": sha256_file(lm) if lm.is_file() else None,
             "captured": captured}, text)


def olean_manifest_diff(old: bytes | None, new: bytes) -> str:
    """Which files differ between two manifests (at most five named per kind)."""
    if old is None:
        return "the recorded manifest file is not there to compare file by file"

    def rows(b: bytes) -> dict[str, str]:
        out = {}
        for line in b.decode("utf-8", "replace").splitlines():
            rel, _, rest = line.partition(" ")
            out[rel] = rest
        return out
    a, b = rows(old), rows(new)
    parts = []
    for what, names in (("changed", [k for k in a if k in b and a[k] != b[k]]),
                        ("missing", [k for k in a if k not in b]), ("new", [k for k in b if k not in a])):
        if names:
            parts.append(f"{len(names)} {what} ({', '.join(names[:5])}{' …' if len(names) > 5 else ''})")
    return "; ".join(parts) or "same files, different manifest bytes"


def only_removed(old: bytes | None, new: bytes) -> bool:
    """No file both manifests list differs in size or hash, and some recorded file is gone: a build tree that
    was (partly) deleted and rebuilt since (verify.sh's `rm -rf .lake/build`), with nothing recorded changed."""
    if old is None:
        return False
    rows = lambda b: dict(line.partition(" ")[::2] for line in b.decode("utf-8", "replace").splitlines())  # noqa: E731
    a, b = rows(old), rows(new)
    return any(k not in b for k in a) and all(a[k] == v for k, v in b.items() if k in a)


def check_oleans(project: pathlib.Path, recorded: dict, recorded_text: bytes | None, when: str) -> None:
    """Refuse when the package's oleans no longer match the recorded manifest."""
    now, text = olean_manifest(project, str(recorded.get("lean_toolchain")), when)
    if now["sha256"] != recorded.get("sha256"):
        raise RunnerError(f"the package's compiled oleans changed {when}: the olean manifest is now "
                          f"{now['sha256'][:12]}… ({now['files']} file(s)), recorded "
                          f"{str(recorded.get('sha256'))[:12]}… ({recorded.get('files')} file(s)) "
                          f"when captured ({recorded.get('captured')}); {olean_manifest_diff(recorded_text, text)}; "
                          f"rebuild (comparator-receipt --no-resume after removing the evidence directory, or "
                          f"a fresh --prepare) so the judged modules are the ones the build stage produced; "
                          f"nothing written")


def read_olean_manifest(stages_dir: pathlib.Path) -> bytes | None:
    try:
        return (stages_dir / OLEAN_MANIFEST_FILE).read_bytes()
    except OSError:
        return None


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
                raise StageError(f"{e}; `{' '.join(cmd)}` killed; nothing written", 128 + e.signum) from e
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
        self.olean_manifest: dict | None = None   # the root package's compiled modules after setup
        self.started_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def keep_attempt(self, reason: str) -> pathlib.Path | None:
        """Keep the previous run's stages.json and stage logs as attempts/NNN/ before this run replaces them."""
        kept = keep_attempt(self.dir, reason)
        if kept is not None:
            print(f"comparator-receipt: the previous run's stage files kept read-only as "
                  f"{STAGES_DIR}/{ATTEMPTS_DIR}/{kept.name}/ ({reason})", file=sys.stderr)
        return kept

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
                **({"olean_manifest": self.olean_manifest} if self.olean_manifest is not None else {}),
                **(self.pool or {}),
                "started_at": self.started_at,
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


# ---------------------------------------------------------------------------
# Attempts. Before a run replaces the stage files of an earlier run (it reruns the setup, or the
# comparison after any earlier comparison, estimate or failure), the top-level files of comparator-stages/
# (stages.json, the stage logs, the olean manifest, the partial report and partial receipt, an estimate)
# and the per-unit judge logs under 04-units/ are copied to comparator-stages/attempts/NNN/ with an
# attempt.json, then made read-only. NNN is zero-padded and never reused (attempts/NEXT survives a deleted
# attempt). lean-pkg never deletes an attempt and never reads one back: resume, the slot cache and setup
# reuse read the current stage files only, and receipts bind the current run only. Nothing prunes them;
# see docs/comparator.md (Attempts) for pruning by hand.

ATTEMPTS_DIR = "attempts"
ATTEMPT_FILE = "attempt.json"
ATTEMPT_SCHEMA = "comparator-attempt/1"
ATTEMPT_NEXT = "NEXT"


def _attempt_number(name: str) -> int | None:
    return int(name) if re.fullmatch(r"\d{3,}", name) else None


def attempt_verdict(data: dict | None, files: set[str]) -> str:
    """pass | partial | failed | estimate | prepared | incomplete | unknown (no readable stages.json)."""
    if data is None:
        return "unknown"
    by = {e.get("stage"): e for e in data.get("stages") or [] if isinstance(e, dict)}
    failed = data.get("failed_stage")
    if failed:
        return "partial" if (by.get(failed) or {}).get("status") == "partial" else "failed"
    if (by.get("receipt") or {}).get("status") == "ok":
        return "partial" if any(f.endswith(".partial.json") for f in files) else "pass"
    if (by.get("comparator") or {}).get("status") == "ok" and ESTIMATE_FILE in files:
        return "estimate"
    if "comparator" not in by and data.get("setup_complete"):
        return "prepared"
    return "incomplete"


def _attempt_sources(stages_dir: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """(relative name, path) of the stage files an attempt keeps: regular files only, no *.tmp."""
    out = [(p.name, p) for p in sorted(stages_dir.iterdir())
           if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp")]
    units = stages_dir / UNITS_DIR
    if units.is_dir() and not units.is_symlink():
        out += [(f"{UNITS_DIR}/{p.name}", p) for p in sorted(units.iterdir())
                if p.is_file() and not p.is_symlink() and not p.name.endswith(".tmp")]
    return out


def keep_attempt(stages_dir: pathlib.Path, reason: str) -> pathlib.Path | None:
    """Copy the previous run's stage files to attempts/NNN/ (read-only); None when there is nothing."""
    if not stages_dir.is_dir():
        return None
    sources = _attempt_sources(stages_dir)
    if not sources:
        return None
    data = read_stages(stages_dir / STAGES_FILE)
    root = stages_dir / ATTEMPTS_DIR
    root.mkdir(exist_ok=True)
    nums = [n for n in (_attempt_number(p.name) for p in root.iterdir()) if n is not None]
    try:
        counter = int((root / ATTEMPT_NEXT).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        counter = 1
    n = max([counter, *[x + 1 for x in nums]])
    while True:
        dest = root / f"{n:03d}"
        try:
            dest.mkdir()
            break
        except FileExistsError:
            n += 1
    tmp = root / f"{ATTEMPT_NEXT}.tmp"
    tmp.write_text(f"{n + 1}\n", encoding="utf-8")
    os.replace(tmp, root / ATTEMPT_NEXT)
    hashes: dict[str, str] = {}
    for rel, src in sources:
        out = dest / rel
        out.parent.mkdir(exist_ok=True)
        shutil.copy2(src, out)
        hashes[rel] = sha256_file(out)
    om = (data or {}).get("olean_manifest")
    meta = {"schema": ATTEMPT_SCHEMA, "n": n, "reason": reason,
            "commit": (data or {}).get("commit"),
            "started_at": (data or {}).get("started_at"), "finished_at": (data or {}).get("updated_at"),
            "kept_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "failed_stage": (data or {}).get("failed_stage"),
            "verdict": attempt_verdict(data, set(hashes)),
            "olean_manifest_sha256": om.get("sha256") if isinstance(om, dict) else None,
            "files": dict(sorted(hashes.items()))}
    (dest / ATTEMPT_FILE).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for p in sorted(dest.rglob("*"), reverse=True):          # files first, then directories
        p.chmod(0o555 if p.is_dir() else 0o444)
    dest.chmod(0o555)
    return dest


def in_attempt(path: pathlib.Path) -> bool:
    """True when path lies inside a kept attempt (comparator-stages/attempts/NNN/...)."""
    parts = path.resolve().parts
    return any(parts[i] == STAGES_DIR and parts[i + 1] == ATTEMPTS_DIR
               for i in range(len(parts) - 1)) or (path / ATTEMPT_FILE).is_file() \
        or (path.parent / ATTEMPT_FILE).is_file()


def list_attempts(where: pathlib.Path) -> dict:
    """--attempts: {dir, next, attempts[{n, verdict, failed_stage, commit, started_at, finished_at,
    kept_at, reason, files, bytes, intact, problem}]} in attempt order; never writes."""
    cands = [where / STAGES_DIR / ATTEMPTS_DIR, where / ATTEMPTS_DIR, where]
    root = next((c for c in cands if c.is_dir() and c.name == ATTEMPTS_DIR), None)
    out: dict = {"dir": str(root if root is not None else cands[0]), "next": None, "attempts": []}
    if root is None:
        return out
    try:
        out["next"] = int((root / ATTEMPT_NEXT).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pass
    for d in sorted((p for p in root.iterdir() if p.is_dir() and _attempt_number(p.name) is not None),
                    key=lambda p: _attempt_number(p.name)):
        row = {"n": _attempt_number(d.name), "name": d.name, "verdict": None, "failed_stage": None,
               "commit": None, "started_at": None, "finished_at": None, "kept_at": None, "reason": None,
               "olean_manifest_sha256": None, "files": 0, "bytes": 0, "intact": False, "problem": None}
        try:
            meta = json.loads((d / ATTEMPT_FILE).read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or meta.get("schema") != ATTEMPT_SCHEMA:
                raise ValueError("not a " + ATTEMPT_SCHEMA)
        except (OSError, ValueError) as e:
            row["problem"] = f"no readable {ATTEMPT_FILE} ({e.__class__.__name__}): an interrupted copy?"
            out["attempts"].append(row)
            continue
        for k in ("verdict", "failed_stage", "commit", "started_at", "finished_at", "kept_at", "reason",
                  "olean_manifest_sha256"):
            row[k] = meta.get(k)
        files = meta.get("files") if isinstance(meta.get("files"), dict) else {}
        row["files"] = len(files)
        bad = []
        for rel, sha in sorted(files.items()):
            p = d / rel
            if not p.is_file():
                bad.append(f"{rel} missing")
                continue
            row["bytes"] += p.stat().st_size
            if sha256_file(p) != sha:
                bad.append(f"{rel} changed")
        extra = sorted(p.relative_to(d).as_posix() for p in d.rglob("*")
                       if p.is_file() and p.name != ATTEMPT_FILE
                       and p.relative_to(d).as_posix() not in files)
        bad += [f"{rel} not recorded" for rel in extra]
        row["intact"] = not bad
        row["problem"] = "; ".join(bad) or None
        out["attempts"].append(row)
    return out


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

    mods = config_modules(project, configs)
    checkout = step([lake, "env", "true"], "dependency checkout per lake-manifest.json")

    def checkout_then_targets(log):
        result = checkout(log)
        target_readiness(project, lake, log, mods)
        return result
    stages.run("checkout", checkout_then_targets if lakefile else (lambda log: no_lake))
    if not lakefile:
        stages.run("cache-get", lambda log: no_lake)
    elif manifest_has_mathlib(project):
        stages.run("cache-get", step([lake, "exe", "cache", "get"], "Mathlib build cache"))
    else:
        stages.run("cache-get", lambda log: ("skipped", None, "no mathlib package in lake-manifest.json"))
    if lakefile and mods:
        before = olean_snapshot(project)
        build = step([lake, "build", *mods], "build of " + ", ".join(mods))

        def build_then_shadow_check(log):
            result = build(log)
            shadow_preflight(project, lake, log, mods)
            return result
        stages.run("build", build_then_shadow_check)
        log_text = stages.log("build").read_text(encoding="utf-8", errors="replace")
        stages.build_provenance = build_provenance(before, olean_snapshot(project), log_text, build_source())
    else:
        stages.run("build", lambda log: no_lake if not lakefile else
                   ("skipped", None, "no challenge/solution module configured"))
        stages.build_provenance = build_provenance(None, None, "", build_source())
    capture_oleans(project, stages, "build")
    print(f"comparator-receipt: {build_provenance_line(stages.build_provenance)}", file=sys.stderr)


def capture_oleans(project: pathlib.Path, stages: Stages, captured: str) -> None:
    """Write comparator-stages/03-build.oleans.sha256 and record its digest (stages.json olean_manifest)."""
    summary, text = olean_manifest(project, stages.binding["lean_toolchain"], captured)
    path = stages.dir / OLEAN_MANIFEST_FILE
    tmp = path.with_name(OLEAN_MANIFEST_FILE + ".tmp")
    tmp.write_bytes(text)
    os.replace(tmp, path)
    stages.olean_manifest = summary
    stages.save()
    print(f"comparator-receipt: {olean_manifest_line(summary)}", file=sys.stderr)


def olean_manifest_line(om: dict) -> str:
    return (f"olean manifest: {om.get('files')} compiled module file(s) of the package, sha256 "
            f"{str(om.get('sha256'))[:12]}… (captured at {om.get('captured')})")


def verify_judged_oleans(project: pathlib.Path, stages: Stages, when: str) -> None:
    """The comparator stage's check: the oleans are still the ones the setup recorded."""
    check_oleans(project, stages.olean_manifest or {}, read_olean_manifest(stages.dir), when)


# Lake target readiness (C1). A configured module outside every [[lean_lib]]'s roots/globs passes direct
# `lean` elaboration but makes `lake build` (stage 03, and the judge's own sandboxed build) fail with
# `unknown target`. Lake decides that from the loaded workspace (a lakefile.lean is code, and a root is
# buildable only if the lib's globs match it), so the runner asks Lake rather than parsing the lakefile:
# `lake --no-build build M` exits 0 (up to date) or 3 (Lake's noBuildCode: needs a rebuild) for a known
# target, and 1 with `unknown target `M`` for one no library covers (checked on Lean v4.35.0-rc2). It runs
# at the end of stage 01, once the dependencies are checked out (loading the workspace needs them) and
# before stage 02 (the Mathlib cache download) and stage 03 (the build).
NO_BUILD_NEEDS_REBUILD = 3
UNKNOWN_TARGET_HINT = ("add it to a [[lean_lib]] in the lakefile: its roots or globs must cover the module "
                       "(when the library sets globs, they must also match a root), for example a separate "
                       "[[lean_lib]] name = \"ComparatorAudit\" with globs = [\"ComparatorAudit.+\"]; see "
                       "docs/getting-started.md (`unknown target`)")


def classify_target_probe(module: str, returncode: int, output: str) -> str:
    """One `lake --no-build build MODULE` result: "ready" (exit 0), "rebuild" (exit 3, a known target that
    is not up to date), "unknown" (Lake's `unknown target`/`unknown module` for this module) or "other"."""
    if returncode == 0:
        return "ready"
    if returncode == NO_BUILD_NEEDS_REBUILD:
        return "rebuild"
    if f"unknown target `{module}`" in output or f"unknown module `{module}`" in output:
        return "unknown"
    return "other"


def target_readiness(project: pathlib.Path, lake: str, log: pathlib.Path, modules) -> None:
    """Refuse (StageError) when a configured challenge/solution module is not a Lake target; record every
    probe in the log. Any other probe failure is logged and left to the build stage."""
    unknown: list[str] = []
    with open(log, "a", encoding="utf-8") as fh:
        for mod in modules:
            try:
                p = subprocess.run([lake, "--no-build", "build", mod], cwd=project, capture_output=True,
                                   text=True, env=tool_env(), stdin=subprocess.DEVNULL,
                                   preexec_fn=stack_preexec(), timeout=3600)
                rc, out = p.returncode, (p.stdout or "") + (p.stderr or "")
            except (OSError, subprocess.TimeoutExpired) as e:
                rc, out = -1, str(e)
            verdict = classify_target_probe(mod, rc, out)
            last = last_line(out)
            fh.write(f"lake-target: {mod}: {verdict} (exit {rc})"
                     + (f"; {redact(last)}" if verdict == "other" and last else "") + "\n")
            if verdict == "unknown":
                unknown.append(mod)
    if unknown:
        raise StageError(f"Lake target readiness: {', '.join(unknown)} "
                         f"{'is' if len(unknown) == 1 else 'are'} not a Lake target (`lake build` reports "
                         f"unknown target); {UNKNOWN_TARGET_HINT}; nothing written", 1)


def shadow_preflight(project: pathlib.Path, lake: str, log: pathlib.Path, modules=()) -> None:
    """Refuse (StageError) when a LEAN_PATH entry other than the root package's own lib directory holds one
    of its root namespaces: Lake lists the root last, Lean loads <R>/ from the first entry holding it, so the
    judge (run under `lake env`) would read that copy (lake_hygiene.shadow_hits). The root namespaces: the
    lakefile's lean_lib roots (else the tracked sources' top-level names) and the configured modules'."""
    value, why = lh.lean_path(project, lake, tool_env(), preexec=stack_preexec())
    if why:
        raise StageError(f"LEAN_PATH shadow check: {why}; nothing written")
    tracked = git(project, "ls-files").stdout.splitlines()
    roots = lh.project_roots(project, tracked) | lh.module_roots(modules)
    hits = lh.shadow_hits(value, project, roots)
    with open(log, "a", encoding="utf-8") as fh:
        for h in hits:
            fh.write(f"lean-path-shadow: {redact(h)}\n")
        if not hits:
            fh.write(f"lean-path-shadow: none ({len([e for e in value.split(os.pathsep) if e])} entries, "
                     f"roots {','.join(sorted(roots))})\n")
    if hits:
        raise StageError(f"LEAN_PATH shadows the package's own modules: {', '.join(redact(h) for h in hits)}; "
                         f"{lh.SHADOW_HINT}; nothing written")


def judged(log_text: str, mechanism: str, returncode: int | None) -> bool:
    """Whether a failed judge got as far as judging (#62): verify-comparator.sh prints JUDGING_MARK right
    before `lake comparator`, comparator-check.sh prints LEGACY_REJECT_MARK on a rejection. Exit 0 without
    the acceptance line (a judge that ran and said nothing usable) counts as judged."""
    if returncode == 0:
        return True
    lines = log_text.splitlines()
    if mechanism == "legacy":
        return any(LEGACY_REJECT_MARK in ln for ln in lines)
    marks = [i for i, ln in enumerate(lines) if ln.startswith(JUDGING_MARK)]
    if not marks:
        return False
    return pre_judge_failure(lines[marks[-1] + 1:], returncode) is None


# `lake comparator` (Lean v4.35.0-rc2, Lake/CLI/Check.lean) prints "Resolving dependencies" before its sandboxed
# workspace resolution and "Building <module>" / "Exporting ..." once it starts on the challenge; it exits 2
# (cannotRun) when it cannot even start. A run that stopped before "Building"/"Exporting" never judged (C2).
JUDGE_PROGRESS = ("Building ", "Exporting ", "Running ", "Your solution is okay!")


EXTERNAL_LINK_NOTE = ("a common cause: .lake/packages, or an entry in it, linked to a checkout outside the "
                      "project, which the sandbox cannot read")


def judge_failure_reason(log_text: str, mechanism: str, returncode: int | None) -> str | None:
    """pre_judge_failure for a whole toolchain judge log (None for legacy or a log without the mark)."""
    if mechanism == "legacy":
        return None
    lines = log_text.splitlines()
    marks = [i for i, ln in enumerate(lines) if ln.startswith(JUDGING_MARK)]
    return pre_judge_failure(lines[marks[-1] + 1:], returncode) if marks else None


def pre_judge_failure(after_mark: list[str], returncode: int | None) -> str | None:
    """Why a failed `lake comparator` (its output after JUDGING_MARK) stopped before judging, or None."""
    if any(ln.startswith(JUDGE_PROGRESS) for ln in after_mark):
        return None
    if any(ln.strip() == "Resolving dependencies" for ln in after_mark):
        return ("lake comparator failed while resolving the workspace in its sandbox (dependency "
                "resolution or workspace load), before building or judging anything")
    if returncode == 2:
        return "lake comparator could not start (exit 2)"
    return None


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
            self.failure = (unit, j.returncode, judged(out, self.mechanism, j.returncode), last_line(out),
                            judge_failure_reason(out, self.mechanism, j.returncode))
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
                raise PartialStage(msg, 128 + e.signum) from e
            if self.failure is not None:
                unit, rc, was_judged, last, reason = self.failure
                self.progress.state = "failed"
                self.publish()
                what = "comparator-check" if self.mechanism == "legacy" else f"{unit.config}: lake comparator"
                if was_judged:
                    verdict = f"{what} did not accept the solution ({self.tag(unit)}, exit {rc})"
                else:
                    judge = "comparator-check" if self.mechanism == "legacy" else f"{unit.config}: verify-comparator"
                    verdict = (f"{judge}: the judge tool failed before judging ({self.tag(unit)}, exit {rc}"
                               + (f"; {reason}" if reason else "")
                               + (f"; last line: {last}" if last else "") + "); this is a tool/environment "
                               "error, not a verdict on the proof; fix the tool or the project setup and rerun"
                               + (f" ({EXTERNAL_LINK_NOTE})" if reason and "resolving" in reason else ""))
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


REMAINING_MODEL = ("mean of the timed non-heavy sampled slots (all timed slots when none is non-heavy) "
                   "* unsampled slots / judges; the sampled slots that passed are cached and not judged "
                   "again; per-slot timings, so the whole_unit_warning applies")


def remaining_estimate(rows: list[dict], slots: int, judges: int) -> dict:
    """The additive remaining-run estimate: the slots the sample did not judge, at the mean of the
    timed non-heavy (stratified) sampled slots. The heavy slots were picked as the heaviest, so the
    unsampled ones weigh no more than they; one slow outlier then does not inflate every other slot.
    With no non-heavy timing it falls back to the mean of all timed slots."""
    timed = [r for r in rows if r.get("seconds") is not None]
    light = [r["seconds"] for r in timed if not r.get("heavy")]
    basis = light or [r["seconds"] for r in timed]
    unsampled = max(0, slots - len({r["slot"] for r in rows}))
    mean = sum(basis) / len(basis) if basis else 0.0
    return {"unsampled_slots": unsampled,
            "remaining_per_slot_seconds": round(mean, 1),
            "remaining_estimate_seconds": int(math.ceil(mean * unsampled / max(1, judges))),
            "remaining_estimate_model": REMAINING_MODEL}


def run_estimate(project, configs, mechanism, check, verify, stages, commit, judges, subset, k,
                 resume_command, forced: bool = False) -> dict:
    """--estimate: judge a sample of K slots (the heaviest by olean weight, then namespace-stratified)
    one at a time, time each, print and record the estimate. Writes no receipt; the judged slots are
    cached for the real run."""
    cfgs = [(c, json.loads((project / c).read_bytes())) for c in configs]
    all_slots = list_slots(cfgs)
    sizes = olean_sizes(project)                  # after the build stage: the package's own oleans
    decls = source_declarations(project, sizes)
    weigh = {s.index: slot_weight(s.name, sizes, decls) for s in all_slots}
    weights_found = any(w > 0 for w, _m in weigh.values())
    if subset is not None:
        sample, heavy = subset, set()
    else:
        sample, heavy = weighted_sample(all_slots, k, {i: w for i, (w, _m) in weigh.items()})
        if not weights_found:
            why = (f"none of the {len(all_slots)} slot name(s) maps to one of the package's "
                   f"{len(sizes)} olean module(s)" if sizes else
                   "no olean of the package was found under .lake/build/lib")
            print(f"comparator-receipt: WARN: --estimate: no slot has an olean weight ({why}); the "
                  f"heaviest-first part of the sample was skipped and the sample is namespace-stratified "
                  f"only", file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix="comparator-receipt.") as tmp:
        st = Stage04(project, configs, mechanism, check, verify, stages, pathlib.Path(tmp) / "logs",
                     resume_command, resume=False, judges=1, subset=sample, isolate=True)

        def body(stage_log: pathlib.Path):
            verify_judged_oleans(project, stages, "before the comparison")
            status, code, note = st.run(stage_log)
            verify_judged_oleans(project, stages, "during the comparison")
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
           "weights_found": weights_found, "olean_modules": len(sizes),
           "weighted_slots": sum(1 for w, _m in weigh.values() if w > 0),
           **estimate_numbers(times, len(all_slots), judges),
           **remaining_estimate(rows, len(all_slots), judges),
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


# External dependency symlinks (C2). `lake comparator` resolves the workspace inside its bwrap sandbox,
# which grants only the project directory, the toolchain and LakeHome (Lake/CLI/Check.lean,
# safeResolveWorkspace); a .lake or .lake/packages(/*) symlink to another checkout passes host-check and the
# unsandboxed setup stages but fails there (EEXIST while resolving dependencies). Refused before setup.
EXTERNAL_LINK_HINT = ("the `lake comparator` sandbox grants only the project directory, the toolchain and "
                      "LakeHome, so it cannot follow them; copy the dependencies inside the project (replace "
                      "each symlink with a real copy of its target, e.g. `rm LINK && cp -a TARGET LINK`, or "
                      "remove it and let `lake env true` check the dependencies out again) and rerun")


def external_dependency_links(project: pathlib.Path) -> list[str]:
    """`<path> -> <target>` for each of .lake, .lake/packages and .lake/packages/* that is a symlink whose
    target (resolved) lies outside the project directory (resolved). Targets are redacted."""
    root = pathlib.Path(os.path.realpath(project))
    cand = [project / ".lake", project / ".lake" / "packages"]
    pk = project / ".lake" / "packages"
    try:
        if pk.is_dir():
            cand += sorted(pk.iterdir())
    except OSError:
        pass
    out = []
    for c in cand:
        if not c.is_symlink():
            continue
        target = pathlib.Path(os.path.realpath(c))
        if target == root or root in target.parents:
            continue
        out.append(f"{c.relative_to(project)} -> {redact(str(target))}")
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
    evidence = evidence or project / DEFAULT_EVIDENCE
    set_redactions(project, evidence=evidence)   # #41: no checkout/evidence/HOME/TMPDIR path reaches a log or receipt
    for what, where in (("--from-prepared", from_prepared), ("--evidence-dir", evidence)):
        if where is not None and in_attempt(where):   # a kept attempt is never reused, resumed or rewritten
            raise RunnerError(f"{what} {redact(str(where))}: a kept attempt ({STAGES_DIR}/{ATTEMPTS_DIR}/NNN/) "
                              f"is a read-only record, never reused as setup or resumed; point at the "
                              f"evidence directory instead; nothing written")
    if not prepare and not check.is_file():
        raise RunnerError(f"comparator-check script not found: {check}")
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
            raise RunnerError(f"--slots {slot_spec!r}: {e}; nothing written") from e
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
    if mechanism != "legacy":   # C2: before any setup stage; the sandbox of `lake comparator` cannot follow them
        links = external_dependency_links(project)
        if links:
            raise RunnerError(f"dependency symlink(s) resolving outside the project: {'; '.join(links)}; "
                              f"{EXTERNAL_LINK_HINT}; nothing written")
    if from_prepared is not None and mismatch:
        raise RunnerError(f"--from-prepared {from_prepared}: {mismatch}; run `comparator-receipt --prepare` "
                          f"at this commit first; nothing written")
    try:
        stack_limit, stack_lines = plan_stack(os.environ.get(STACK_ENV))
        lean_threads, thread_lines = plan_lean_threads(os.environ.get(LEAN_THREADS_ENV))
    except ValueError as e:
        raise RunnerError(f"{e}; nothing written") from e
    stack_lines += thread_lines
    stages = Stages(evidence, binding, prepare)
    stages.resolution = resolution
    recorded = recorded_text = None
    if mismatch is None:
        when = str(prior.get("updated_at") or "")
        # the olean manifest is checked before anything is written: a refused run must not overwrite the
        # stages.json that records it
        recorded = prior.get("olean_manifest")
        if isinstance(recorded, dict) and recorded.get("sha256"):
            prior_dir = prior_src if (prior_src / OLEAN_MANIFEST_FILE).is_file() else prior_src / STAGES_DIR
            recorded_text = read_olean_manifest(prior_dir)
            try:
                check_oleans(project, recorded, recorded_text, f"since the setup was prepared ({when})")
            except RunnerError as e:
                gone = from_prepared is None and only_removed(recorded_text, olean_manifest_text(project))
                if not gone:   # a changed olean, or a setup named explicitly: refused, never silently redone
                    raise RunnerError(f"{e} (prepared state: {redact(str(prior_src))}; to rebuild, remove its "
                                      f"{STAGES_DIR}/{STAGES_FILE} and the package's .lake/build/lib, then "
                                      f"run comparator-receipt --prepare)") from None
                # this run's own resume state went stale: compiled modules were removed since (verify.sh
                # starts with `rm -rf .lake/build`), none that remain changed. The setup runs again, its build
                # stage rebuilds them, and the comparison judges the oleans that build records.
                mismatch = (f"compiled modules removed since the setup was prepared ({when}): "
                            f"{olean_manifest_diff(recorded_text, olean_manifest_text(project))}")
                recorded = recorded_text = None
        else:
            recorded = None
    if mismatch is None:
        by = {e.get("stage"): e for e in prior.get("stages") or [] if isinstance(e, dict)}
        for s in SETUP_STAGES:
            e = by[s]
            stages.entries[s] = {**e, "status": "prepared",
                                 "note": f"setup done {when} ({e.get('status')}) in {prior_src}"}
        stages.build_provenance = carried_provenance(prior)   # #56: as recorded when it was built
        stages.olean_manifest = recorded
        current = read_stages(stages.dir / STAGES_FILE)
        if not (current is not None and current.get("failed_stage") is None and all(
                isinstance(e, dict) and e.get("stage") in SETUP_STAGES for e in current.get("stages") or [])):
            stages.keep_attempt("comparison rerun on the prepared setup")   # a pure prepared state carries over
        stages.clear(STAGES[3:])
        if recorded_text is not None and read_olean_manifest(stages.dir) != recorded_text:
            (stages.dir / OLEAN_MANIFEST_FILE).write_bytes(recorded_text)   # --from-prepared: kept beside
        stages.save()
        if recorded is not None:
            print(f"comparator-receipt: {olean_manifest_line(recorded)}; unchanged since then", file=sys.stderr)
        else:                                  # a prepared state from before the olean manifest
            capture_oleans(project, stages, "reuse (the prepared setup recorded no olean manifest)")
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
        stages.keep_attempt(f"setup rerun ({mismatch})" if prior is not None else "setup rerun")
        stages.clear(STAGES)
        for line in stack_lines:              # before the build stage's lean children
            print(line, file=sys.stderr)
        setup_stages(project, configs, stages)
        dirty = dirty_paths(project, evidence)
        if dirty:
            raise RunnerError(f"the setup stages changed the tree ({', '.join(dirty[:10])}); commit the "
                              f"change and rerun; nothing written (stages: {stages.json})")
    if stages.olean_manifest is not None:     # a slot accepted against other oleans is judged again
        stages.binding = {**stages.binding, "olean_manifest_sha256": stages.olean_manifest["sha256"]}
        stages.save()
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
            verify_judged_oleans(project, stages, "before the comparison")
            st = Stage04(project, configs, mechanism, check, verify, stages, log_dir, resume_command,
                         resume=resume, judges=judges, subset=subset, unit_size=unit_size)
            result["stage04"] = st
            status, code, note = st.run(stage_log)
            verify_judged_oleans(project, stages, "during the comparison")
            result["extra"] = {"stack_limit": stack_limit, "lean_threads": lean_threads,
                               **({"build_provenance": stages.build_provenance}
                                  if stages.build_provenance is not None else {}),
                               **({"olean_manifest": stages.olean_manifest}
                                  if stages.olean_manifest is not None else {}),
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
    signoffs = lint_configs(project, configs, warn=False)     # the sign-offs, as the run was linted
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
            **({"judged_as": cfg["judged_as"]} if isinstance(cfg.get("judged_as"), dict)
               and cfg_rel in signoffs and signoffs[cfg_rel]["source"] == "inline" else {}),
            **({"signoff": signoffs[cfg_rel]} if cfg_rel in signoffs else {}),
            "log": log_rel,
            "log_sha256": sha256_file(src_log),
            "result": "pass",
            **((config_extra or {}).get(cfg_rel) or {}),
        })
    host = published_host()          # never the host name (the hashed label unless --host-label)
    now = dt.datetime.now(dt.timezone.utc)
    job = (os.environ.get("SLURM_JOB_ID")
           or f"local-{host}-{os.getpid()}-{now.strftime('%Y%m%dT%H%M%SZ')}")
    receipt = {
        "schema": SCHEMA, "mechanism": mechanism, "commit": commit,
        "lean_toolchain": toolchain, "tools": tools, "host": host,
        **({"test_only": True} if os.environ.get(FAKE_LAKE_ENV) is not None else {}), "host_policy": host_policy, "job_id": job,
        "generated_at": now.isoformat(timespec="seconds"),
        "result": "pass", "configs": entries, "stages": stage_summary, **(extra or {}),
        "host_label": host,
    }
    # #236: the SIF image when this runs inside Apptainer / Singularity and the image file is readable here
    # (container.from_environment); absent otherwise. Additive: validate_comparator ignores it.
    image = ctr.from_environment()
    if image is not None:
        receipt["container"] = image
    # The lean-pkg that writes this receipt, with the shape `receipt systemd --run` seals (producer.py): no
    # path, host or user name. Additive: validate_comparator, gate and receipt check ignore it.
    from . import producer as pr
    receipt["producer"] = pr.receipt_producer(pr.fingerprint())
    receipt = label_host_fields(redact_json(receipt))   # #41: <checkout>/, <home>/, <tmp>/ instead of host paths
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
                          f"nothing written") from None
    return {"receipt": str(rec_path)}


HOST_LABEL_ENV = "COMPARATOR_RECEIPT_HOST_LABEL"


def host_label() -> str:
    """The operator's label for this host (--host-label or the env), or "" when none is set."""
    return os.environ.get(HOST_LABEL_ENV, "").strip()


HASHED_LABEL_RE = re.compile(r"h-[0-9a-f]{12}")


def published_host(hostname: str | None = None) -> str:
    """The host as every receipt field and output line names it: the operator's label when set, else the
    hashed label `h-` + 12 hex of the SHA-256 of the lower-cased host name (receipt_systemd.host_label, the
    same label `receipt systemd` publishes for the same host). The name itself is never written."""
    return host_label() or hashed_host_label(hostname or socket.gethostname())


def host_tokens(hostname: str | None = None) -> list[str]:
    """This host's name, full and its first label, as replace_host matches them (longest first); the first
    label only when it is distinctive (lean-pkg receipt's identity rules: 3+ characters, a letter, not a
    stop word such as `localhost`)."""
    from .receipt import IDENTITY_MIN_LEN, IDENTITY_STOPWORDS
    name = (hostname if hostname is not None else socket.gethostname() or "").strip()
    out = []
    for v in (name, name.split(".")[0]):
        if (v and v not in out and len(v) >= IDENTITY_MIN_LEN and v.casefold() not in IDENTITY_STOPWORDS
                and re.search(r"[A-Za-z]", v)):
            out.append(v)
    return out


def replace_host(text: str, hostname: str | None = None, label: str | None = None) -> str:
    """text with this host's name (full or first label, any case, as a whole word) replaced by its label."""
    label = label or published_host(hostname)
    for tok in host_tokens(hostname):
        text = re.sub(r"(?<![A-Za-z0-9_-])" + re.escape(tok) + r"(?![A-Za-z0-9_-])", label, text, flags=re.I)
    return text


# The receipt fields that name where the run happened (build_source() labels the operator's free text).
HOST_FIELDS = ("host", "host_policy", "job_id")


def label_host_fields(receipt: dict) -> dict:
    """The receipt with this host's name replaced by its label in HOST_FIELDS; other fields (configs,
    theorem names, hashes) are bound to bytes and never rewritten."""
    return {k: replace_host(v) if k in HOST_FIELDS and isinstance(v, str) else v for k, v in receipt.items()}


def raw_host_problem(rec: dict) -> str | None:
    """Why a comparator receipt names a raw host (None when it does not): a receipt written before host
    labels (lean-pkg 0.10.0 and earlier) records socket.gethostname() in `host`, `job_id` and `host_policy`
    unless its writer gave --host-label (then `host_label` is set). Read-only; old receipts still validate."""
    host = str(rec.get("host") or "").strip()
    if not host or rec.get("host_label") or HASHED_LABEL_RE.fullmatch(host):
        return None
    return "names its host in `host` (no `host_label`): a receipt written before host labels"


def resume_command(argv: list[str], prog: str = "comparator-receipt") -> str:
    """The command that resumes this run: the same arguments; completed setup and slots are reused."""
    import shlex
    return shlex.join([*shlex.split(prog), *[a for a in argv if a != "--no-resume"]])


def verify_oleans(project: pathlib.Path, where: pathlib.Path) -> dict:
    """--verify-oleans: {ok, commit, recorded, current, problem}; never writes."""
    set_redactions(project, evidence=where)
    data = read_stages(where)
    if data is None:
        raise RunnerError(f"--verify-oleans: no comparator-stages/stages.json in {redact(str(where))}")
    head = git(project, "rev-parse", "HEAD").stdout.strip()
    rec = data.get("olean_manifest")
    out = {"ok": False, "commit": head, "recorded_commit": data.get("commit"), "recorded": rec,
           "current": None, "problem": None}
    if data.get("commit") != head:
        out["problem"] = (f"recorded for commit {str(data.get('commit'))[:12]}, HEAD is {head[:12]}; "
                          f"the manifest binds the oleans of that commit only")
        return out
    if not isinstance(rec, dict) or not rec.get("sha256"):
        out["problem"] = "no olean manifest recorded there (a setup prepared before the olean manifest)"
        return out
    sdir = where if (where / OLEAN_MANIFEST_FILE).is_file() else \
        (where.parent if where.is_file() else where / STAGES_DIR)
    now, text = olean_manifest(project, str(rec.get("lean_toolchain")), "verify")
    out["current"] = {k: now[k] for k in ("files", "sha256")}
    if now["sha256"] != rec["sha256"]:
        out["problem"] = olean_manifest_diff(read_olean_manifest(sdir), text)
        return out
    out["ok"] = True
    return out


def verify_oleans_main(args) -> int:
    project = pathlib.Path(args.project).resolve()
    where = pathlib.Path(args.verify_oleans).expanduser().resolve() if args.verify_oleans else \
        (pathlib.Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else project / DEFAULT_EVIDENCE)
    try:
        res = verify_oleans(project, where)
    except RunnerError as e:
        print(f"comparator-receipt: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(redact_json(res), indent=2, sort_keys=True))
    elif res["ok"]:
        print(f"oleans: same as recorded: {res['current']['files']} file(s), sha256 "
              f"{res['current']['sha256'][:12]}… (captured at {res['recorded'].get('captured')}); nothing written")
    else:
        print(f"oleans: DIFFERENT from the recorded manifest: {res['problem']}; nothing written")
    return 0 if res["ok"] else 1


def attempts_main(args) -> int:
    project = pathlib.Path(args.project).resolve()
    where = pathlib.Path(args.attempts).expanduser().resolve() if args.attempts else \
        (pathlib.Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else project / DEFAULT_EVIDENCE)
    set_redactions(project, evidence=where)
    res = list_attempts(where)
    if args.json:
        print(json.dumps(redact_json(res), indent=2, sort_keys=True))
    else:
        rows = res["attempts"]
        print(f"attempts: {len(rows)} kept in {redact(res['dir'])}" + (" (read-only; nothing written)"
                                                                     if rows else "; nothing written"))
        for r in rows:
            if r["verdict"] is None:
                print(f"  {r['name']}  ?  {r['problem']}")
                continue
            failed = f" ({r['failed_stage']})" if r["failed_stage"] else ""
            state = "intact" if r["intact"] else f"NOT INTACT: {r['problem']}"
            print(f"  {r['name']}  {r['verdict']}{failed}  commit {str(r['commit'])[:12]}  "
                  f"{r['started_at']} .. {r['finished_at']}  {r['files']} file(s), {r['bytes']} B  {state}"
                  f"  [{r['reason']}]")
    return 0 if all(r["intact"] for r in res["attempts"]) else 1


def seal_sacct_main(args) -> int:
    """--seal-sacct RECEIPT [--sacct-file F] [--user NAME] [--json]: receipt.seal_comparator_sacct."""
    from . import receipt as receipt_mod
    from .core import Fail, UsageError as CliUsage
    if args.seal_sacct is None:
        print("comparator-receipt: --sacct-file and --user go with --seal-sacct RECEIPT", file=sys.stderr)
        return 2
    other = [f for f, v in (("--prepare", args.prepare), ("--from-prepared", args.from_prepared),
                            ("--estimate", args.estimate), ("--slots", args.slots),
                            ("--verify-oleans", args.verify_oleans is not None),
                            ("--attempts", args.attempts is not None)) if v]
    if other:
        print(f"comparator-receipt: --seal-sacct runs alone; not with {', '.join(other)}", file=sys.stderr)
        return 2
    path = pathlib.Path(args.seal_sacct).expanduser()
    try:
        res = receipt_mod.seal_comparator_sacct(path, args.sacct_file, args.user)
    except CliUsage as e:
        print(f"comparator-receipt: {e}", file=sys.stderr)
        return 2
    except Fail as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        print(f"comparator-receipt: --seal-sacct refused: {e}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0
    b = res["slurm_sacct"]
    verb = "sealed" if res["sealed"] else "already sealed with this record; nothing written"
    print(f"comparator receipt {verb}: job {b['job']} COMPLETED 0:0, {b['start']} .. {b['end']} "
          f"(user {b['checks']['user']}, node {b['checks']['node']})")
    print(f"  record   {res['sacct']} (sha256 {b['sha256'][:12]}...)")
    print("commit both under .lean-receipts/; a receipt already recorded is recorded again with "
          "`lean-pkg comparator attach NAME --receipt R --replace`")
    return 0


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The flags of `comparator-receipt`, also `lean-pkg receipt comparator` (the same code, the same flags)."""
    ap.add_argument("--repo", dest="project", default=".", metavar="DIR",
                    help="the package checkout, a Lake project directory (default: cwd)")
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
                         f"(default: <project>/{DEFAULT_EVIDENCE.as_posix()}; its {STAGES_DIR}/ subdirectory); "
                         "outside the project its path is written as <evidence> in every log and receipt")
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
                         "slots, estimated_seconds, recommended_cap, remaining_estimate_seconds}; writes no "
                         "receipt (the judged slots are cached for the real run). The JSON is schema "
                         f"{ESTIMATE_SCHEMA}; /1 -> /2 added the sample floor, the heaviest-first sample "
                         "(per-row weight_bytes, weight_module, heavy) and the max cap model; later "
                         "additions (weights_found, remaining_estimate_seconds, ...) are additive and "
                         "keep /2. See docs/comparator.md (Estimate first)")
    ap.add_argument("--sample", type=int, default=None, metavar="K",
                    help=f"--estimate: sample size (default and floor: max({ESTIMATE_MIN_SAMPLE}, "
                         f"ceil({ESTIMATE_MIN_FRACTION} * slots)), capped at the slots); --slots "
                         f"overrides the sample")
    ap.add_argument("--force-sample", action="store_true",
                    help="--estimate: accept a --sample (or --slots) below the floor; the estimate warns "
                         "and records sample_forced: true")
    ap.add_argument("--verify-oleans", nargs="?", const="", default=None, metavar="DIR",
                    help="read-only, no host policy: rehash the package's compiled modules under "
                         ".lake/build/lib and compare them with the olean manifest the setup recorded in DIR "
                         "(an evidence directory, its comparator-stages/ or its stages.json; default: the "
                         "evidence directory); the recorded commit must be HEAD; exit 0 same, 1 different or "
                         "nothing recorded; writes nothing")
    ap.add_argument("--attempts", nargs="?", const="", default=None, metavar="DIR",
                    help=f"read-only, no host policy: list the earlier runs kept under "
                         f"{STAGES_DIR}/{ATTEMPTS_DIR}/NNN/ (verdict, failed stage, commit, times, files, "
                         f"whether every kept file still matches its recorded sha256) in DIR (an evidence "
                         f"directory, its {STAGES_DIR}/ or its {ATTEMPTS_DIR}/; default: the evidence "
                         f"directory); exit 0, or 1 when a kept file changed or went missing; writes nothing")
    ap.add_argument("--seal-sacct", default=None, metavar="RECEIPT",
                    help="after the Slurm job finished, no host policy: corroborate RECEIPT's Slurm claim (job_id "
                         "from SLURM_JOB_ID, which any shell can set) with the job's sacct record: COMPLETED, "
                         "exit 0:0, Start..End containing the run, this account (live sacct, or --user) and "
                         "the receipt's node (its h- host label against the node's); writes <c12>-comparator-sacct.txt beside RECEIPT (the record, "
                         "normalized; no user or node names) and its sha256 into RECEIPT as slurm_sacct. Only a "
                         "sealed receipt backs a coverage.yaml compute job. Exit 0 sealed (or already), 1 "
                         "refused, nothing written")
    ap.add_argument("--sacct-file", default=None, metavar="F",
                    help="--seal-sacct on a host without Slurm: the saved output of `SLURM_TIME_FORMAT="
                         "%%Y-%%m-%%dT%%H:%%M:%%S%%z sacct -j J --format=JobID,State,ExitCode,Start,End,Elapsed,"
                         "User,NodeList -P` (with its header line); the account is then checked only with --user")
    ap.add_argument("--user", default=None, metavar="NAME",
                    help="--seal-sacct: the account the job must have run under (default with live sacct: this "
                         "one); compared, never written")
    ap.add_argument("--json", action="store_true",
                    help="--estimate, --verify-oleans, --attempts or --seal-sacct: print JSON; stdout is the JSON "
                         "document alone, the progress goes to stderr")
    ap.add_argument("--host-label", default=None, metavar="LABEL",
                    help=f"name the host LABEL in the receipt (host, host_label, job_id, host_policy) and the "
                         f"output (env {HOST_LABEL_ENV}); default: h- and 12 hex digits of the SHA-256 of "
                         f"the lower-cased host name, the label `receipt systemd` uses; the host name itself "
                         f"is never written")


PROGRAM = "comparator-receipt"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=PROGRAM,
        description="run comparator-check.sh and write a comparator receipt (also `lean-pkg receipt comparator`)")
    add_arguments(ap)
    from . import command_tree
    command_tree.apply_program_flag_aliases(ap, PROGRAM)
    args = ap.parse_args(argv)
    argv = argv if argv is not None else sys.argv[1:]
    try:
        return run_main(args, argv)
    finally:   # after the run: its own first stderr line (a refusal) stays first, as lean-pkg's notes
        for line in command_tree.program_notes(argv, PROGRAM, args.json):
            print(line, file=sys.stderr)


def run_main(args, argv: list[str], prog: str = "comparator-receipt") -> int:
    """The run of parsed `args`; argv (the flags as typed, after the program or command name) is what the
    printed resume command repeats, as `prog ARGV`."""
    if args.host_label is not None:
        if not args.host_label.strip():
            print("comparator-receipt: --host-label must not be empty", file=sys.stderr)
            return 2
        os.environ[HOST_LABEL_ENV] = args.host_label.strip()
    if args.seal_sacct is not None or args.sacct_file is not None or args.user is not None:
        return seal_sacct_main(args)
    if args.attempts is not None:
        other = [f for f, v in (("--prepare", args.prepare), ("--from-prepared", args.from_prepared),
                                ("--estimate", args.estimate), ("--slots", args.slots),
                                ("--verify-oleans", args.verify_oleans is not None)) if v]
        if other:
            print(f"comparator-receipt: --attempts is a read-only listing; not with {', '.join(other)}",
                  file=sys.stderr)
            return 2
        return attempts_main(args)
    if args.verify_oleans is not None:
        other = [f for f, v in (("--prepare", args.prepare), ("--from-prepared", args.from_prepared),
                                ("--estimate", args.estimate), ("--slots", args.slots)) if v]
        if other:
            print(f"comparator-receipt: --verify-oleans is a read-only check; not with {', '.join(other)}",
                  file=sys.stderr)
            return 2
        return verify_oleans_main(args)
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
                                 os.environ.get(ALLOWED_HOSTS_ENV)),
                             workstation=workstation_default()[0])
    why = replace_host(why)       # the label replaces the host name everywhere a receipt or its output names it
    if not ok:
        print(f"comparator-receipt: refusing: {why}", file=sys.stderr)
        return 1
    if os.environ.get(FAKE_LAKE_ENV) is None and not args.prepare:   # the host check (#22.5); never a refusal
        hc = host_checks()
        print(f"comparator-receipt: host-check: {hc['verdict']}", file=sys.stderr)
    try:
        project = pathlib.Path(args.project).resolve()
        evidence = pathlib.Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else None
        prepared = pathlib.Path(args.from_prepared).expanduser().resolve() if args.from_prepared else None
        # --estimate --json: stdout carries only the JSON document; the run's stage lines, judge output
        # and warnings go to stderr (field report: progress lines before the JSON broke json.loads)
        with chatter_to_stderr(bool(args.json)), \
                importlib.resources.as_file(default_check_script()) as check_default, \
                importlib.resources.as_file(default_verify_script()) as verify_default:
            check = pathlib.Path(args.check).resolve() if args.check else pathlib.Path(check_default).resolve()
            verify = pathlib.Path(args.verify).resolve() if args.verify else pathlib.Path(verify_default).resolve()
            res = run(project, check, host_policy=why, mechanism=args.mechanism, verify=verify,
                      evidence=evidence, prepare=args.prepare, from_prepared=prepared,
                      resume_command=resume_command(argv, prog),
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
        print(f"remaining_estimate_seconds {e['remaining_estimate_seconds']} ({e['unsampled_slots']} "
              f"unsampled slot(s) at {e['remaining_per_slot_seconds']} s; model: "
              f"{e['remaining_estimate_model']})")
        if not e["weights_found"]:
            print(f"WARNING: weights_found false: no slot maps to one of the package's "
                  f"{e['olean_modules']} olean module(s); the sample is namespace-stratified only")
        print(f"whole_unit_warning: {e['whole_unit_warning']}")
        print(f"estimate: {res['estimate_file']}")
        return 0
    if res.get("partial"):
        print(f"PARTIAL comparator receipt written: {res['receipt']} ({res['slots']} of "
              f"{res['slots_total']} slot(s), commit {res['commit'][:12]})")
        print("it is diagnostic only: marked partial: true, kept out of the repository, and refused as "
              "evidence by `lean-pkg comparator`, `gate --comparator` and `publish --yes`")
        print(f"the judged slots are cached: a full run at this commit skips them (stage logs: {res['stages']})")
        return 0
    print(f"comparator receipt written: {res['receipt']} ({res['configs']} config(s), "
          f"commit {res['commit'][:12]}, host policy: {why})")
    print(f"stage logs: {res['stages']}")
    print("next: the lower-tier receipt at this same commit (commit nothing in between: both must bind it), "
          "then the gate:")
    print(f"  lean-pkg gate <name> --receipt R.json --comparator {res['receipt']}")
    print(f"  (a package already gated at this commit: lean-pkg comparator attach <name> --receipt {res['receipt']}; "
          f"`lean-pkg verify <name>` runs both receipts and the gate in order)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
