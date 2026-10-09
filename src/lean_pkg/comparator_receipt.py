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
inside the lean_pkg package), COMPARATOR_LAKE (the lake the setup stages run), SLURM_JOB_ID (job identity when under Slurm).
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
import shutil
import socket
import subprocess
import sys
import tempfile
import time

SCHEMA = "comparator-receipt/2"
PASS_LINE = "Your solution is okay!"
# Acceptance is decided by the same predicate the validator uses (one source of truth).
from .cli import comparator_pass  # noqa: E402
# Every lean/lake/git/bash below runs without the repository-selecting Git variables (#27).
from .cli import GIT_REPO_ENV, git_env_set, scrubbed_env  # noqa: E402,F401
from .holes import check_definition_holes  # noqa: E402
from .comparator_stage import (PARTIAL_FILE, UNITS_DIR, Interrupted, Judge, Progress, SignalGuard,  # noqa: E402
                               StageLog, Unit, list_slots, ranges, run_pool, unit_id)
MECHANISMS = ("auto", "toolchain", "legacy")
TOOLCHAIN_FLOOR = "v4.35.0-rc2"          # Palomar's floor: the first toolchain shipping `lake comparator`
TOOLCHAIN_TOOLS = ("lake", "leanexport", "leanchecker", "nanoda_bin", "con-ron")
FAKE_LAKE_ENV = "COMPARATOR_FAKE_LAKE_OK"  # test-only: "1"/"0" overrides the toolchain probe
VERIFY_ENV = "COMPARATOR_VERIFY"           # path of verify-comparator.sh (default: the vendored copy)
PIN_RE = re.compile(r"^== (comparator|lean4export|nanoda_lib|landrun) @ ([0-9a-f]{40})",
                    re.MULTILINE)
PIN_KEYS = {"comparator": "comparator", "lean4export": "lean4export",
            "nanoda_lib": "nanoda", "landrun": "landrun"}


class RunnerError(Exception):
    pass


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
                           text=True, timeout=120, env=scrubbed_env())
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
                       env=scrubbed_env())
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
    """Run cmd with stdout and stderr appended to log (streamed, so a hung step can be watched)."""
    with open(log, "ab") as fh:
        fh.write(("$ " + " ".join(cmd) + "\n").encode())
        fh.flush()
        try:
            return subprocess.run(cmd, cwd=cwd, stdout=fh, stderr=subprocess.STDOUT,
                                  env=scrubbed_env()).returncode
        except OSError as e:
            fh.write(f"error: cannot run {cmd[0]}: {e}\n".encode())
            return 127


class Stages:
    """The stage ledger of one run: logs and stages.json under <evidence>/comparator-stages/."""

    def __init__(self, evidence: pathlib.Path, binding: dict, prepare: bool):
        self.dir = evidence / STAGES_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        self.json = self.dir / STAGES_FILE
        self.binding, self.prepare = binding, prepare
        self.entries: dict[str, dict] = {}
        self.progress: dict | None = None   # stage 04's {done, total, failed, partial_report} (#39)

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
    lake = os.environ.get(LAKE_CMD_ENV) or "lake"
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
        stages.run("build", step([lake, "build", *mods], "build of " + ", ".join(mods)))
    else:
        stages.run("build", lambda log: no_lake if not lakefile else
                   ("skipped", None, "no challenge/solution module configured"))


class PartialStage(StageError):
    """Stage 04 stopped part-way (a signal): its stage status is `partial`, not `failed`."""


class Stage04:
    """Stage 04 (#39): plan the units, run their judges (streamed, heartbeat, killed on TERM), print
    per-slot progress, keep 04-comparator.partial.json and stages.json `progress` current, and
    assemble each configuration's receipt log from the logs of the units that judged it."""

    def __init__(self, project: pathlib.Path, configs: list[str], mechanism: str, check: pathlib.Path,
                 verify: pathlib.Path | None, stages: Stages, log_dir: pathlib.Path, resume_command: str):
        self.project, self.configs, self.mechanism = project, configs, mechanism
        self.check, self.verify, self.stages, self.log_dir = check, verify, stages, log_dir
        self.cfgs = [(c, json.loads((project / c).read_bytes())) for c in configs]
        self.slots = list_slots(self.cfgs)
        self.units_dir = stages.dir / UNITS_DIR
        self.resume_command = resume_command
        self.progress = Progress(stages.dir / PARTIAL_FILE, self.slots, {
            **stages.binding, "mechanism": mechanism, "resume_command": resume_command})
        self.passed: dict[str, list[tuple[Unit, pathlib.Path]]] = {c: [] for c in configs}
        self.legacy_output = ""
        self.slog: StageLog | None = None

    # -- planning ---------------------------------------------------------------------------------
    def plan(self) -> list[Unit]:
        if self.mechanism == "legacy":   # comparator-check.sh judges every configuration in one run
            units = [Unit(1, "", list(self.slots), True)]
        else:
            units = [Unit(i, rel, [s for s in self.slots if s.config == rel], True)
                     for i, (rel, _cfg) in enumerate(self.cfgs, 1)]
        for u in units:
            u.id = unit_id(self.stages.binding, u)
        return units

    def command(self, unit: Unit) -> tuple[list[str], dict]:
        env = scrubbed_env()
        if self.mechanism == "legacy":
            env["LOG_DIR"] = str(self.log_dir)
            return ["bash", str(self.check)], env
        return ["bash", str(self.verify), unit.config], env

    # -- progress ---------------------------------------------------------------------------------
    def say(self, text: str) -> None:
        assert self.slog is not None
        self.slog.write(text)

    def tag(self, unit: Unit) -> str:
        idx = ranges(s.index for s in unit.slots) if unit.slots else "-"
        return f"slot {idx}/{len(self.slots)} {unit.label()}"

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
                  prefix=f"[{unit.n}] " if self.width > 1 else "", tmpdir=tmpdir)
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
            self.failure = (unit, j.returncode)
            self.say(f"{self.tag(unit)} failed (exit {j.returncode}) in {secs:.1f}s")
            self.publish()
            return False
        if self.mechanism == "legacy":
            self.legacy_output = out
        else:
            self.passed[unit.config].append((unit, j.log))
        self.progress.mark(unit.slots, "judged")
        self.say(f"{self.tag(unit)} done in {secs:.1f}s ({self.progress.counts()['done']}/{len(self.slots)} done)")
        self.publish()
        return True

    def run(self, stage_log: pathlib.Path) -> tuple[str, int, str]:
        self.units_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        units = self.plan()
        self.n_units, self.width, self.failure = len(units), 1, None
        self.slog = StageLog(stage_log, echo=sys.stdout)
        try:
            self.say(f"comparator-receipt: {len(self.slots)} slot(s) in {len(self.configs)} config(s), "
                     f"{len(units)} judge invocation(s), mechanism {self.mechanism}")
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
                unit, rc = self.failure
                self.progress.state = "failed"
                self.publish()
                what = "comparator-check" if self.mechanism == "legacy" else f"{unit.config}: lake comparator"
                raise StageError(f"{what} did not accept the solution ({self.tag(unit)}, exit {rc}); "
                                 f"{self.progress.summary()}; nothing written; partial report "
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

    def assemble(self) -> None:
        """Each configuration's receipt log: its single whole-config unit log byte for byte, else the
        logs of the units that judged it, each under a header naming its slots."""
        if self.mechanism == "legacy":
            return                                   # comparator-check.sh wrote LOG_DIR itself
        for rel in self.configs:
            parts = self.passed[rel]
            dst = self.log_dir / f"{cfg_name(rel)}.log"
            if len(parts) == 1 and parts[0][0].whole:
                shutil.copyfile(parts[0][1], dst)
                continue
            with open(dst, "wb") as fh:
                for unit, log in parts:
                    fh.write(f"== unit: slots {ranges(s.index for s in unit.slots)} "
                             f"({len(unit.slots)} names)\n".encode())
                    fh.write(log.read_bytes())


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
        from_prepared: pathlib.Path | None = None, resume_command: str = "comparator-receipt") -> dict:
    if not (project / "lean-toolchain").is_file():
        raise RunnerError(f"{project}: not a Lake project (no lean-toolchain)")
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
            if os.environ.get(FAKE_LAKE_ENV) is None:   # real host: check the sandbox first
                bwrap_preflight()
        print(f"mechanism: {mechanism}")
    prior_src = from_prepared or evidence
    prior = read_stages(prior_src)
    mismatch = prepared_mismatch(prior, binding)
    if from_prepared is not None and mismatch:
        raise RunnerError(f"--from-prepared {from_prepared}: {mismatch}; run `comparator-receipt --prepare` "
                          f"at this commit first; nothing written")
    stages = Stages(evidence, binding, prepare)
    if mismatch is None:
        when = str(prior.get("updated_at") or "")
        by = {e.get("stage"): e for e in prior.get("stages") or [] if isinstance(e, dict)}
        for s in SETUP_STAGES:
            e = by[s]
            stages.entries[s] = {**e, "status": "prepared",
                                 "note": f"setup done {when} ({e.get('status')}) in {prior_src}"}
        stages.clear(STAGES[3:])
        stages.save()
        if prepare:
            print(f"already prepared at {commit[:12]} ({when}); nothing to do; stages: {stages.json}")
            return {"prepared": True, "commit": commit, "stages": str(stages.json), "reused": True}
        print(f"comparator-receipt: setup reused from {prior_src} (prepared {when}); "
              f"running the comparison only", file=sys.stderr)
    else:
        if prior is not None:
            print(f"comparator-receipt: no reusable prepared state ({mismatch}); running setup",
                  file=sys.stderr)
        stages.clear(STAGES)
        setup_stages(project, configs, stages)
        dirty = dirty_paths(project, evidence)
        if dirty:
            raise RunnerError(f"the setup stages changed the tree ({', '.join(dirty[:10])}); commit the "
                              f"change and rerun; nothing written (stages: {stages.json})")
    if prepare:
        print(f"prepared at {commit[:12]}: setup stages done; stages: {stages.json}")
        print("next: comparator-receipt (same commit; the setup stages are skipped)")
        return {"prepared": True, "commit": commit, "stages": str(stages.json), "reused": False}
    with tempfile.TemporaryDirectory(prefix="comparator-receipt.") as tmp:
        log_dir = pathlib.Path(tmp) / "logs"
        result: dict = {}

        def comparator_stage(stage_log: pathlib.Path):
            st = Stage04(project, configs, mechanism, check, verify, stages, log_dir, resume_command)
            status, code, note = st.run(stage_log)
            result["tools"] = (parse_pins(st.legacy_output) if mechanism == "legacy"
                               else toolchain_tools(project, verify, toolchain))
            return status, code, note

        stages.run("comparator", comparator_stage)
        tools = result["tools"]

        def receipt_stage(stage_log: pathlib.Path):
            result.update(write_receipt(project, configs, log_dir, commit, mechanism, toolchain, tools,
                                        host_policy, pathlib.Path(tmp), stages.summary()))
            stage_log.write_text(f"wrote {result['receipt']} and {len(configs)} log(s)\n", encoding="utf-8")
            return "ok", 0, f".lean-receipts/{commit[:12]}-comparator.json"

        stages.run("receipt", receipt_stage)
    return {"receipt": result["receipt"], "commit": commit, "configs": len(configs),
            "mechanism": mechanism, "stages": str(stages.json)}


def write_receipt(project: pathlib.Path, configs: list[str], log_dir: pathlib.Path, commit: str,
                  mechanism: str, toolchain: str, tools: dict, host_policy: str, tmp: pathlib.Path,
                  stage_summary: list[dict]) -> dict:
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
        staged.append((src_log, project / log_rel))
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
        })
    host = os.environ.get("COMPARATOR_RECEIPT_HOST_LABEL", "").strip() or socket.gethostname()
    now = dt.datetime.now(dt.timezone.utc)
    job = (os.environ.get("SLURM_JOB_ID")
           or f"local-{host}-{os.getpid()}-{now.strftime('%Y%m%dT%H%M%SZ')}")
    receipt = {
        "schema": SCHEMA, "mechanism": mechanism, "commit": commit,
        "lean_toolchain": toolchain, "tools": tools, "host": host,
        **({"test_only": True} if os.environ.get(FAKE_LAKE_ENV) is not None else {}), "host_policy": host_policy, "job_id": job,
        "generated_at": now.isoformat(timespec="seconds"),
        "result": "pass", "configs": entries, "stages": stage_summary,
    }
    rec_tmp = tmp / "receipt.json"
    rec_tmp.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    rec_dir = project / ".lean-receipts"
    rec_path = rec_dir / f"{commit[:12]}-comparator.json"
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
    args = ap.parse_args(argv)
    if args.prepare and args.from_prepared:
        print("comparator-receipt: --prepare and --from-prepared are exclusive", file=sys.stderr)
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
    label = os.environ.get("COMPARATOR_RECEIPT_HOST_LABEL", "").strip()
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
                      resume_command=resume_command(argv if argv is not None else sys.argv[1:]))
    except RunnerError as e:
        print(f"comparator-receipt: {e}", file=sys.stderr)
        code = getattr(e, "code", None)
        return code if isinstance(e, PartialStage) and isinstance(code, int) else 1
    if res.get("prepared"):
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
