# comparator-receipt -- run comparator-check.sh on a Lake project and write
# the comparator receipt that `lean-pkg gate --comparator` / `lean-pkg
# comparator` consume. Writes NOTHING unless every comparator configuration
# passed, so a failed run can never be mistaken for evidence.
"""Usage (from a Lake project directory):

    comparator-receipt [--project DIR] [--check SCRIPT]

Host policy: the run is allowed iff SLURM_JOB_ID is set (inside a Slurm job),
OR this host's name is listed in COMPARATOR_RECEIPT_ALLOWED_HOSTS
(comma-separated), OR COMPARATOR_RECEIPT_ALLOW_HOST=1. The reason is recorded
in the receipt's host_policy field.

Env: COMPARATOR_CHECK (script path; default: the comparator-check.sh shipped
inside the lean_pkg package), SLURM_JOB_ID (job identity when under Slurm).
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

SCHEMA = "comparator-receipt/2"
PASS_LINE = "Your solution is okay!"
# Acceptance is decided by the same predicate the validator uses (one source of truth).
from .cli import comparator_pass  # noqa: E402
from .holes import check_definition_holes  # noqa: E402
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
                           text=True, timeout=120)
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
                           text=True, timeout=60)
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
    "palomar": bool, "verdict": str}. Read-only. A mechanism is usable only when every one of its own
    requirements holds; the verdict names only those (#23.4)."""
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
            "palomar": mech[PALOMAR_MECHANISM], "verdict": verdict}


def toolchain_tools(project: pathlib.Path, verify: pathlib.Path, toolchain: str) -> dict:
    """Digests of the judging binaries bundled with the project's toolchain.
    Under the test-only FAKE_LAKE_ENV override, every digest is that of the verify script itself."""
    tools: dict = {"lean_toolchain": toolchain}
    if os.environ.get(FAKE_LAKE_ENV) is not None:
        d = sha256_file(verify)
        for tool in TOOLCHAIN_TOOLS:
            tools[f"{tool}_sha256"] = d
        return tools
    p = subprocess.run(["lean", "--print-prefix"], cwd=project, capture_output=True, text=True)
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
                          capture_output=True, text=True)


def run(project: pathlib.Path, check: pathlib.Path, host_policy: str = "",
        mechanism: str = "auto", verify: pathlib.Path | None = None) -> dict:
    if not (project / "lean-toolchain").is_file():
        raise RunnerError(f"{project}: not a Lake project (no lean-toolchain)")
    if not check.is_file():
        raise RunnerError(f"comparator-check script not found: {check}")
    st = git(project, "status", "--porcelain")
    if st.returncode != 0:
        raise RunnerError(f"{project}: not a git repository ({st.stderr.strip()})")
    if st.stdout.strip():
        files = ", ".join(line[3:] for line in st.stdout.splitlines()[:10])
        raise RunnerError(f"dirty tree — commit or remove before a receipt run: {files}")
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
    available = toolchain_available(project)
    if mechanism == "auto":
        mechanism = "toolchain" if available else "legacy"
    elif mechanism == "toolchain" and not available:
        raise RunnerError(f"this toolchain ({toolchain}) has no `lake comparator`; the toolchain "
                          f"mechanism needs Lean {TOOLCHAIN_FLOOR} or later (Palomar's floor); "
                          f"use --mechanism legacy")
    print(f"mechanism: {mechanism}")
    with tempfile.TemporaryDirectory(prefix="comparator-receipt.") as tmp:
        log_dir = pathlib.Path(tmp) / "logs"
        if mechanism == "legacy":
            env = os.environ.copy()
            env["LOG_DIR"] = str(log_dir)
            p = subprocess.run(["bash", str(check)], cwd=project, env=env,
                               capture_output=True, text=True)
            sys.stdout.write(p.stdout)
            if p.returncode != 0:
                sys.stderr.write(p.stderr)
                raise RunnerError(f"comparator-check exited {p.returncode}; nothing written")
            tools = parse_pins(p.stdout)
        else:
            if verify is None or not verify.is_file():
                raise RunnerError(f"verify-comparator script not found: {verify}")
            if os.environ.get(FAKE_LAKE_ENV) is None:   # real host: check the sandbox first
                bwrap_preflight()
            log_dir.mkdir(parents=True)
            for cfg_rel in configs:
                p = subprocess.run(["bash", str(verify), cfg_rel], cwd=project,
                                   capture_output=True, text=True)
                log_text = p.stdout + (("\n" + p.stderr) if p.stderr else "")
                (log_dir / f"{cfg_name(cfg_rel)}.log").write_text(log_text, encoding="utf-8")
                sys.stdout.write(p.stdout)
                if p.returncode != 0 or not comparator_pass(log_text, "toolchain"):
                    sys.stderr.write(p.stderr)
                    raise RunnerError(f"{cfg_rel}: lake comparator did not accept the solution "
                                      f"(exit {p.returncode}); nothing written")
            tools = toolchain_tools(project, verify, toolchain)
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
            "result": "pass", "configs": entries,
        }
        rec_tmp = pathlib.Path(tmp) / "receipt.json"
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
    return {"receipt": str(rec_path), "commit": commit, "configs": len(entries),
            "mechanism": mechanism}


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
    args = ap.parse_args(argv)
    ok, why = host_policy_ok(os.environ.get("SLURM_JOB_ID"),
                             os.environ.get(ALLOW_HOST_ENV) == "1",
                             hostname=socket.gethostname(),
                             allowed_hosts=parse_allowed_hosts(
                                 os.environ.get(ALLOWED_HOSTS_ENV)))
    if not ok:
        print(f"comparator-receipt: refusing: {why}", file=sys.stderr)
        return 1
    if os.environ.get(FAKE_LAKE_ENV) is None:   # the host check, reported up front (#22.5); never a refusal
        hc = host_checks()
        print(f"comparator-receipt: host-check: {hc['verdict']}", file=sys.stderr)
    label = os.environ.get("COMPARATOR_RECEIPT_HOST_LABEL", "").strip()
    if label:
        # the label replaces the host name everywhere a receipt or its output names the host
        why = why.replace(socket.gethostname(), label)
    try:
        project = pathlib.Path(args.project).resolve()
        with importlib.resources.as_file(default_check_script()) as check_default, \
                importlib.resources.as_file(default_verify_script()) as verify_default:
            check = pathlib.Path(args.check).resolve() if args.check else pathlib.Path(check_default).resolve()
            verify = pathlib.Path(args.verify).resolve() if args.verify else pathlib.Path(verify_default).resolve()
            res = run(project, check, host_policy=why, mechanism=args.mechanism, verify=verify)
    except RunnerError as e:
        print(f"comparator-receipt: {e}", file=sys.stderr)
        return 1
    print(f"comparator receipt written: {res['receipt']} ({res['configs']} config(s), "
          f"commit {res['commit'][:12]}, host policy: {why})")
    print("commit .lean-receipts/ in this repo, then record it:")
    print(f"  lean-pkg comparator <name> --receipt {res['receipt']}")
    print(f"  (or at gate time: lean-pkg gate <name> --receipt R.json "
          f"--comparator {res['receipt']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
